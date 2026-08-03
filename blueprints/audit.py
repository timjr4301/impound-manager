"""
Status Audit Tool — backlog triage dashboard (tim / brady / jim).

Read-only: every flag here is computed at query time from columns that
already exist (letters + delivery dates, lka_document_confirmed, the
task_engine BMV-search completion signal, possible_release). No new
columns, no ALTER TABLE, no schema changes. The one CSV cross-reference
(Section 1) is parsed and matched in memory and stashed in a per-user
server-side temp file (never the DB — and never the session cookie, which
caps at ~4KB and would be silently dropped by the browser on a large
upload, taking the login cookie with it). The session holds only a
marker, so the stash still goes away when the session ends.
"""
import csv
import io
import json
import os
import tempfile
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
LETTER2_SENT_GAP_DAYS = 30  # Letter 2 due 30d after Letter 1 was SENT (not delivery)
MISSING_DOC_GRACE_DAYS = 3      # BMV search / LKA: day 1-3 grace; day 4+ flagged

SESSION_KEY = 'audit_towbook_csv'
LETTER1_SESSION_KEY = 'audit_letter1_csv'

AUDIT_ROLES = ('tim', 'brady', 'jim')   # wally is role 'tim', so included


# ── Server-side CSV stash ─────────────────────────────────────────────────────
# One JSON file per user, overwritten on every upload, deleted on Clear.
# The session cookie holds only a True marker (so the stash still "ends" with
# the session); the payload itself lives here because a real release export
# flags hundreds of rows — far past the ~4KB cookie cap.

def _csv_store_path():
    return os.path.join(tempfile.gettempdir(),
                        f'im_audit_towbook_{current_user.id}.json')


def _load_csv_data():
    """The stashed cross-reference for this user, or None. Session marker must
    be present (old sessions may carry the pre-file-store dict — treat any
    truthy value as the marker) and the file must parse."""
    if not session.get(SESSION_KEY):
        return None
    try:
        with open(_csv_store_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_csv_data(data):
    with open(_csv_store_path(), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    session[SESSION_KEY] = True


def _clear_csv_data():
    session.pop(SESSION_KEY, None)
    try:
        os.remove(_csv_store_path())
    except OSError:
        pass


def _letter1_csv_store_path():
    return os.path.join(tempfile.gettempdir(),
                        f'im_audit_letter1_{current_user.id}.json')


def _load_letter1_data():
    if not session.get(LETTER1_SESSION_KEY):
        return None
    try:
        with open(_letter1_csv_store_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_letter1_data(data):
    with open(_letter1_csv_store_path(), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    session[LETTER1_SESSION_KEY] = True


def _clear_letter1_data():
    session.pop(LETTER1_SESSION_KEY, None)
    try:
        os.remove(_letter1_csv_store_path())
    except OSError:
        pass


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
    """Base population for every section: active, non-ghost vehicles. Also
    excludes letter_hold vehicles (boats, etc. — Tim manually paused the
    letter pipeline for them 08/02/2026) from every section here, same as
    possible_release — the whole point of a hold is to stop a vehicle from
    nagging across every queue, not just one."""
    return (
        Vehicle.query
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release.isnot(True))
        .filter(Vehicle.letter_hold.isnot(True))
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

    # (b) Letter 1 sent, Letter 2 not sent, 30+ days since Letter 1 was SENT
    if l1.sent_date and (not l2 or not l2.sent_date):
        days_since_sent = (today - l1.sent_date).days
        if days_since_sent >= LETTER2_SENT_GAP_DAYS:
            past_due = days_since_sent - LETTER2_SENT_GAP_DAYS
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

    csv_data = _load_csv_data()
    letter1_data = _load_letter1_data()

    return render_template(
        'audit/index.html',
        total_active=total_active,
        overdue=overdue,
        missing_bmv=missing_bmv,
        missing_lka=missing_lka,
        csv_data=csv_data,
        letter1_data=letter1_data,
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
    # ALL active IM vehicles by stock_number first, VIN as fallback — including
    # those flagged possible_release: a documented Towbook release is exactly
    # the verification that flag was waiting for, so excluding them (as this
    # did originally) hid every car the export was meant to reconcile.
    active_vehicles = Vehicle.query.filter(Vehicle.status == 'ACTIVE').all()
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
            'was_flagged': bool(v.possible_release),
            'detail_url': url_for('vehicles_detail', vehicle_id=v.id),
        })

    flagged.sort(key=lambda r: r['days_since_release'], reverse=True)

    _save_csv_data({
        'filename': uploaded.filename,
        'uploaded_at': datetime.now().strftime('%m/%d/%Y %I:%M %p'),
        'total_records': total_records,
        'flagged': flagged,
    })
    confirmed_ghosts = sum(1 for r in flagged if r['was_flagged'])
    msg = (f'{len(flagged)} vehicle{"" if len(flagged) == 1 else "s"} flagged from CSV '
           f'({total_records} rows cross-referenced).')
    if confirmed_ghosts:
        msg += (f' {confirmed_ghosts} of them were already flagged Possible Release — '
                'the export confirms those releases.')
    flash(msg, 'info')
    return redirect(url_for('audit.index'))


@bp.route('/towbook-clear', methods=['POST'])
@_audit_required
def towbook_clear():
    _clear_csv_data()
    flash('Cleared the uploaded Towbook cross-reference.', 'info')
    return redirect(url_for('audit.index'))


@bp.route('/letter1-check', methods=['POST'])
@_audit_required
def letter1_check():
    """Cross-check a Towbook 'current inventory with 1st Letter Sent column'
    export against what IM itself thinks — does each vehicle have a Letter 1
    actually marked sent? Reports agreements plus both mismatch directions
    rather than assuming either side is correct. Accepts either export
    layout: this report's plain header-row-1 format, or the standard 2-row-
    metadata Impounds format (auto-detected)."""
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        flash('No file selected. Choose a Towbook export CSV with a "1st Letter Sent" column.', 'danger')
        return redirect(url_for('audit.index'))

    raw = uploaded.stream.read()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    lines = [l for l in content.splitlines() if l.strip()]
    if not lines:
        flash('Uploaded file is empty.', 'danger')
        return redirect(url_for('audit.index'))

    # Auto-detect layout: does row 1 already look like the header (has a
    # "Stock" column), or are there 2 metadata rows first (standard Impounds
    # export) before the real header on row 3?
    def _looks_like_header(line):
        return 'stock' in _norm(line)

    if _looks_like_header(lines[0]):
        csv_body = '\n'.join(lines)
    elif len(lines) >= 3 and _looks_like_header(lines[2]):
        csv_body = '\n'.join(lines[2:])
    else:
        flash("Could not find a 'Stock #' column — is this a Towbook export?", 'danger')
        return redirect(url_for('audit.index'))

    reader = csv.DictReader(io.StringIO(csv_body))
    headers = reader.fieldnames or []
    norm_map = {_norm(h): h for h in headers}

    if _norm('1st Letter Sent') not in norm_map and _norm('1st Letter') not in norm_map:
        flash('This file has no "1st Letter Sent" column — upload the '
              '"current inventory with first letter sent column" export.', 'danger')
        return redirect(url_for('audit.index'))

    active_vehicles = Vehicle.query.filter(Vehicle.status == 'ACTIVE').all()
    by_stock = {v.stock_number.strip().upper(): v
                for v in active_vehicles if v.stock_number}
    by_vin = {v.vin.strip().upper(): v
              for v in active_vehicles if v.vin}

    total_rows = 0
    agree_not_sent = []
    agree_sent = []
    mismatch_blank_but_sent = []   # Towbook blank, IM says sent
    mismatch_filled_but_not_sent = []  # Towbook filled, IM says not sent
    not_in_im = []

    for row in reader:
        stock = (_get(row, norm_map, 'Stock #', 'Stock') or '').strip()
        vin = (_get(row, norm_map, 'VIN') or '').strip()
        if not stock and not vin:
            continue
        total_rows += 1

        towbook_raw = (_get(row, norm_map, '1st Letter Sent', '1st Letter') or '').strip()
        towbook_says_sent = bool(towbook_raw)

        v = by_stock.get(stock.upper()) if stock else None
        if not v and vin:
            v = by_vin.get(vin.upper())
        if not v:
            not_in_im.append({'stock': stock or None, 'vin': vin or None})
            continue

        im_says_sent = bool(v.letter1 and v.letter1.sent_date)
        row_out = {
            'id': v.id,
            'stock_number': v.stock_number,
            'vin': v.vin,
            'description': v.display_name,
            'towbook_value': towbook_raw or None,
            'im_sent_date': v.letter1.sent_date.strftime('%m/%d/%Y') if im_says_sent else None,
            'detail_url': url_for('vehicles_detail', vehicle_id=v.id),
        }

        if towbook_says_sent and im_says_sent:
            agree_sent.append(row_out)
        elif not towbook_says_sent and not im_says_sent:
            agree_not_sent.append(row_out)
        elif not towbook_says_sent and im_says_sent:
            mismatch_blank_but_sent.append(row_out)
        else:
            mismatch_filled_but_not_sent.append(row_out)

    _save_letter1_data({
        'filename': uploaded.filename,
        'uploaded_at': datetime.now().strftime('%m/%d/%Y %I:%M %p'),
        'total_rows': total_rows,
        'agree_not_sent_count': len(agree_not_sent),
        'agree_sent_count': len(agree_sent),
        'mismatch_blank_but_sent': mismatch_blank_but_sent,
        'mismatch_filled_but_not_sent': mismatch_filled_but_not_sent,
        'not_in_im': not_in_im,
    })

    flash(
        f'{total_rows} rows checked. '
        f'{len(agree_not_sent) + len(agree_sent)} agree with IM. '
        f'{len(mismatch_blank_but_sent)} Towbook-blank-but-IM-sent, '
        f'{len(mismatch_filled_but_not_sent)} Towbook-filled-but-IM-not-sent, '
        f'{len(not_in_im)} not matched to any active vehicle.',
        'info',
    )
    return redirect(url_for('audit.index'))


@bp.route('/letter1-clear', methods=['POST'])
@_audit_required
def letter1_clear():
    _clear_letter1_data()
    flash('Cleared the 1st Letter Sent cross-check.', 'info')
    return redirect(url_for('audit.index'))


@bp.route('/bulk-release', methods=['POST'])
@_audit_required
def bulk_release():
    """Mark a batch of Section 1 vehicles RELEASED in IM. Already-released
    vehicles are skipped silently. Possible Release vehicles are releasable
    ONLY when the current CSV upload documents their release (matched row with
    a Release Date) AND that row is BULK_ELIGIBLE — the documented release is
    the verification the flag was waiting for, and the CSV row (date, reason,
    filename) is recorded as evidence. Flagged vehicles NOT in the CSV, or
    whose reason routes to Tina/Review, stay blocked here. No reason field —
    the front end gates this behind a confirm() dialog."""
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

    csv_data = _load_csv_data() or {}
    rows_by_id = {r['id']: r for r in csv_data.get('flagged', [])}
    csv_filename = csv_data.get('filename') or 'Towbook CSV'

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
        # No-op already-released vehicles.
        if v.status == 'RELEASED':
            skipped += 1
            continue
        row = rows_by_id.get(vid)
        was_flagged = bool(v.possible_release)
        if was_flagged:
            # Ghost stays hard-blocked unless this upload documents its
            # release and the reason is bulk-eligible (Tina/Review reasons
            # keep their manual paths).
            if not row or row.get('category') != 'BULK_ELIGIBLE':
                skipped += 1
                continue
            v.possible_release = False
        v.status = 'RELEASED'
        v.updated_at = now
        if has_released_at:
            setattr(v, 'released_at', now)
        if has_released_by:
            setattr(v, 'released_by', who)
        body = (f'Marked Released via Status Audit bulk release by '
                f'{current_user.display_name or who}.')
        if row:
            body += (f' Towbook shows released {row["release_date"]} — '
                     f'{row["release_reason"]} (evidence: {csv_filename}).')
        if was_flagged:
            body += (' Was flagged Possible Release; flag cleared — the '
                     'documented Towbook release is the verification.')
        db.session.add(VehicleNote(
            vehicle_id=v.id,
            body=body,
            author=current_user.display_name or who,
            created_at=now,
        ))
        released += 1

    if released:
        db.session.commit()

    # Keep the CSV stash intact — released rows drop off on the next
    # upload (they'll no longer be ACTIVE), per spec.
    flash(f'{released} vehicle{"" if released == 1 else "s"} released. '
          f'{skipped} skipped (already released, not in the uploaded CSV, '
          f'or routed to Tina/Review).', 'success')

    if request.is_json:
        return jsonify({
            'released': released,
            'skipped': skipped,
            'errors': errors,
            'redirect': url_for('audit.index'),
        })
    return redirect(url_for('audit.index'))
