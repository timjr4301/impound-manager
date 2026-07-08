"""
Status Audit Tool — backlog triage dashboard (tim / brady / jim).

Read-only: every flag here is computed at query time from columns that
already exist (letters + delivery dates, lka_document_confirmed, the
task_engine BMV-search completion signal, possible_release). No new
columns, no ALTER TABLE, no schema changes. The one CSV cross-reference
(Section 1) is parsed and matched in memory, stashed in the Flask session
(never the DB), and cleared when the session ends. Display is link-only —
no inline mutation actions live on this page.
"""
import csv
import io
from datetime import date, datetime
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, jsonify)
from flask_login import login_required, current_user

from models import db, Vehicle, VehicleNote
from towbook_import import _norm, _get, _parse_date

bp = Blueprint('audit', __name__, url_prefix='/audit')

# ── Backlog-sweep thresholds ──────────────────────────────────────────────────
# Deliberately coarse/flat, distinct from the PPI/POLICE-specific due-date math
# in Vehicle.stoplight_color / task_engine (which drives Heather's live queue).
# This tool catches anything that fell all the way through that net.
OVERDUE_LETTER1_DAYS = 5        # day 1-5 grace; day 6+ overdue
LETTER2_DELIVERY_GAP_DAYS = 30  # Letter 2 due 30d after Letter 1 delivery
MISSING_DOC_GRACE_DAYS = 3      # BMV search / LKA: day 1-3 grace; day 4+ flagged

SESSION_KEY = 'audit_towbook_csv'

AUDIT_ROLES = ('tim', 'brady', 'jim')   # wally is role 'tim', so included


def _audit_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role not in AUDIT_ROLES:
            # JSON/AJAX callers (e.g. bulk-release) get a real 403; page GETs
            # get a friendly flash + redirect.
            if request.method != 'GET' and (request.is_json or request.accept_mimetypes.best == 'application/json'):
                return jsonify({'error': 'Restricted to Tim, Brady, and Jim.'}), 403
            flash('That page is restricted to Tim, Brady, and Jim.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


# ── Release-reason categorization ─────────────────────────────────────────────
# Buckets each Towbook release reason so Section 1 can show Tina-case / review
# badges and gate which rows get a bulk-release checkbox. Matching is
# case-insensitive and whitespace-tolerant.
_TINA_REASONS = {
    'released - title obtained',
    'released - title surrendered',
}
_BULK_REASONS = {
    'released - with payment',
    'released - to new owner',
    'release - to insurance',
    'released - promise to pay',
    'vehicle was scrapped',
}


def _classify_release_reason(reason):
    r = (reason or '').strip().lower()
    if 'affidavit' in r:
        return 'TINA_CASE'
    if r in _TINA_REASONS:
        return 'TINA_CASE'
    if r in _BULK_REASONS:
        return 'BULK_ELIGIBLE'
    return 'REVIEW'   # 'Other', 'PURGED - NOT ON INVENTORY', anything unrecognized


def _released_columns_present():
    """released_at / released_by do not currently exist on the vehicles table
    (confirmed in the Undo Release build). Check the live model so the bulk
    release safely sets them only if a future migration adds them."""
    cols = {c.name for c in Vehicle.__table__.columns}
    return ('released_at' in cols, 'released_by' in cols)


def _active_not_ghost():
    """Base population for every section: active, non-ghost vehicles."""
    return (
        Vehicle.query
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release.isnot(True))
    )


def _task1_bmv_done(v):
    """Task 1 (BMV Search) completion — same signal task_engine.compute_task
    uses: heather_complete OR bmv_stage == 'COMPLETE'. NOT title_search_confirmed
    (that's a separate document-on-file flag)."""
    return bool(v.heather_complete or (v.bmv_stage == 'COMPLETE'))


def _overdue_letter_issue(v, today):
    """Returns a single human-readable overdue-letter issue string, or None."""
    l1, l2 = v.letter1, v.letter2

    # (a) Letter 1 not sent and past the 5-day grace
    if (not l1 or not l1.sent_date):
        if v.days_in_storage > OVERDUE_LETTER1_DAYS:
            return f'Letter 1 overdue — {v.days_in_storage} days'
        return None

    # (b) Letter 1 sent + delivery confirmed, Letter 2 not sent, 30+ days since delivery
    if l1.delivery_confirmed_date and (not l2 or not l2.sent_date):
        days_since_delivery = (today - l1.delivery_confirmed_date).days
        if days_since_delivery >= LETTER2_DELIVERY_GAP_DAYS:
            past_due = days_since_delivery - LETTER2_DELIVERY_GAP_DAYS
            return f'Letter 2 overdue — {past_due} days past due'
    return None


@bp.route('/')
@_audit_required
def index():
    today = date.today()
    active_vehicles = _active_not_ghost().all()
    total_active = len(active_vehicles)

    # Section 2 — Overdue letters
    overdue = []
    for v in active_vehicles:
        issue = _overdue_letter_issue(v, today)
        if issue:
            overdue.append({'vehicle': v, 'issue': issue})
    overdue.sort(key=lambda r: r['vehicle'].days_in_storage, reverse=True)

    # Section 3 — Missing BMV title search (Task 1 not complete), 3-day grace
    missing_bmv = sorted(
        (v for v in active_vehicles
         if not _task1_bmv_done(v) and v.days_in_storage > MISSING_DOC_GRACE_DAYS),
        key=lambda v: v.days_in_storage, reverse=True,
    )

    # Section 4 — Missing LKA document, 3-day grace
    missing_lka = sorted(
        (v for v in active_vehicles
         if not v.lka_document_confirmed and v.days_in_storage > MISSING_DOC_GRACE_DAYS),
        key=lambda v: v.days_in_storage, reverse=True,
    )

    csv_data = session.get(SESSION_KEY)

    return render_template(
        'audit/index.html',
        total_active=total_active,
        overdue=overdue,
        missing_bmv=missing_bmv,
        missing_lka=missing_lka,
        csv_data=csv_data,
        last_refreshed=datetime.now(),
    )


@bp.route('/towbook-check', methods=['POST'])
@_audit_required
def towbook_check():
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        flash('No file selected. Choose a Towbook Release Export CSV.', 'danger')
        return redirect(url_for('audit.index'))

    raw = uploaded.stream.read()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    lines = [l for l in content.splitlines() if l.strip()]
    if len(lines) < 3:
        flash(f'File has only {len(lines)} non-empty row(s). Expected a Towbook '
              'Impounds export with 2 metadata rows then column headers.', 'danger')
        return redirect(url_for('audit.index'))

    # Same 2-metadata-row skip as the main Towbook importer (towbook_import.py).
    csv_body = '\n'.join(lines[2:])
    reader = csv.DictReader(io.StringIO(csv_body))
    headers = reader.fieldnames or []
    norm_map = {_norm(h): h for h in headers}

    if _norm('Stock #') not in norm_map and _norm('Stock') not in norm_map:
        flash("Could not find a 'Stock #' column — is this a Towbook Impounds export?", 'danger')
        return redirect(url_for('audit.index'))

    # Towbook exports have no dedicated status column; a populated Release Date is
    # the released signal (same interpretation as towbook_import.py). Match to
    # active, non-ghost IM vehicles by stock_number first, VIN as fallback.
    active_vehicles = _active_not_ghost().all()
    by_stock = {v.stock_number.strip().upper(): v
                for v in active_vehicles if v.stock_number}
    by_vin = {v.vin.strip().upper(): v
              for v in active_vehicles if v.vin}

    today = date.today()
    total_records = 0
    flagged = []
    for row in reader:
        stock = (_get(row, norm_map, 'Stock #', 'Stock') or '').strip()
        vin = (_get(row, norm_map, 'VIN') or '').strip()
        if not stock and not vin:
            continue
        total_records += 1

        release_date = _parse_date(_get(row, norm_map, 'Release Date'))
        if not release_date:
            continue  # only released-in-Towbook rows are of interest

        v = None
        if stock:
            v = by_stock.get(stock.upper())
        if not v and vin:
            v = by_vin.get(vin.upper())
        if not v:
            continue  # not an active IM vehicle → not a mismatch

        reason = (_get(row, norm_map, 'Release Reason') or '').strip()
        flagged.append({
            'id': v.id,
            'stock_number': v.stock_number or stock or None,
            'vin': v.vin or vin or None,
            'description': (_get(row, norm_map, 'Vehicle') or '').strip() or v.display_name,
            'release_date': release_date.strftime('%m/%d/%Y'),
            'release_reason': reason or '—',
            'category': _classify_release_reason(reason),
            'days_since_release': (today - release_date).days,
            'detail_url': url_for('vehicles_detail', vehicle_id=v.id),
        })

    flagged.sort(key=lambda r: r['days_since_release'], reverse=True)

    session[SESSION_KEY] = {
        'filename': uploaded.filename,
        'uploaded_at': datetime.now().strftime('%m/%d/%Y %I:%M %p'),
        'total_records': total_records,
        'flagged': flagged,
    }
    flash(f'{len(flagged)} vehicle{"" if len(flagged) == 1 else "s"} flagged from CSV '
          f'({total_records} rows cross-referenced).', 'info')
    return redirect(url_for('audit.index'))


@bp.route('/towbook-clear', methods=['POST'])
@_audit_required
def towbook_clear():
    session.pop(SESSION_KEY, None)
    flash('Cleared the uploaded Towbook cross-reference.', 'info')
    return redirect(url_for('audit.index'))


@bp.route('/bulk-release', methods=['POST'])
@_audit_required
def bulk_release():
    """Mark a batch of Section 1 vehicles RELEASED in IM. Ghost and
    already-released vehicles are skipped silently. No reason field — the
    front end gates this behind a confirm() dialog."""
    if request.is_json:
        vehicle_ids = (request.get_json(silent=True) or {}).get('vehicle_ids', [])
    else:
        vehicle_ids = request.form.getlist('vehicle_ids')

    # Normalize to ints, drop anything unparseable
    ids = []
    for raw in vehicle_ids or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    has_released_at, has_released_by = _released_columns_present()
    who = current_user.username
    now = datetime.utcnow()

    released = 0
    skipped = 0
    errors = []
    for vid in ids:
        v = db.session.get(Vehicle, vid)
        if v is None:
            skipped += 1
            errors.append(f'Vehicle {vid} not found')
            continue
        # Hard-block ghosts and no-op already-released vehicles.
        if v.possible_release or v.status == 'RELEASED':
            skipped += 1
            continue
        v.status = 'RELEASED'
        v.updated_at = now
        if has_released_at:
            setattr(v, 'released_at', now)
        if has_released_by:
            setattr(v, 'released_by', who)
        db.session.add(VehicleNote(
            vehicle_id=v.id,
            body=f'Marked Released via Status Audit bulk release by '
                 f'{current_user.display_name or who}.',
            author=current_user.display_name or who,
            created_at=now,
        ))
        released += 1

    if released:
        db.session.commit()

    # Keep the CSV session data intact — released rows drop off on the next
    # upload (they'll no longer be ACTIVE), per spec.
    flash(f'{released} vehicle{"" if released == 1 else "s"} released. '
          f'{skipped} skipped (already released or ghost).', 'success')

    if request.is_json:
        return jsonify({
            'released': released,
            'skipped': skipped,
            'errors': errors,
            'redirect': url_for('audit.index'),
        })
    return redirect(url_for('audit.index'))
