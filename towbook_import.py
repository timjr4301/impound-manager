"""
Flask Blueprint — Towbook CSV import
POST /api/import-towbook        upload CSV, upsert vehicles by stock_number
GET  /api/import-towbook/status last import result
"""
import csv, io, re
from datetime import datetime, date
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Vehicle, SyncLog, PoliceDepartment

bp = Blueprint('towbook_import', __name__, url_prefix='/api/import-towbook')

_DEPT_GENERIC_WORDS_RE = re.compile(
    r'\b(apd|gpd|mpd|rpd|wpd|police department|police dept\.?|police|pd|'
    r"sheriff'?s?\s*office|sheriff|dept\.?|department)\b",
    re.IGNORECASE,
)


def _normalize_dept_name(name):
    """Strip department-code prefixes (GPD/MPD/RPD/WPD/APD) and generic
    words (Police, Dept, Sheriff, etc.) so 'GPD Gahanna Police' and 'Gahanna'
    both normalize to 'gahanna' for fuzzy matching."""
    n = _DEPT_GENERIC_WORDS_RE.sub(' ', name or '')
    n = re.sub(r'[^a-z0-9]+', ' ', n.lower()).strip()
    return n


def _match_police_department(account_value):
    """Fuzzy-match a Towbook 'Account' field value (the requesting police
    department, for police impounds) against PoliceDepartment.name.
    Case-insensitive, ignores department-code prefixes and generic words.
    Returns the PoliceDepartment or None if nothing matches."""
    target = _normalize_dept_name(account_value)
    if not target:
        return None
    depts = PoliceDepartment.query.filter_by(active=True).all()
    for d in depts:
        if _normalize_dept_name(d.name) == target:
            return d
    for d in depts:
        norm_name = _normalize_dept_name(d.name)
        if norm_name and (target in norm_name or norm_name in target):
            return d
    return None

# ── Task string parser ────────────────────────────────────────────────────────
# Handles concatenated strings like "2 Overdue7 Due Next1 Due soon"
_TASK_RE = {
    'tasks_overdue':   re.compile(r'(\d+)\s*Overdue',   re.IGNORECASE),
    'tasks_due_today': re.compile(r'(\d+)\s*Due\s*Today', re.IGNORECASE),
    'tasks_due_next':  re.compile(r'(\d+)\s*Due\s*Next',  re.IGNORECASE),
    'tasks_due_soon':  re.compile(r'(\d+)\s*Due\s*Soon',  re.IGNORECASE),
}

def _parse_tasks(raw):
    return {k: int(m.group(1)) if (m := p.search(raw or '')) else 0
            for k, p in _TASK_RE.items()}

def _parse_date(value):
    if not value or not value.strip():
        return None
    v = value.strip()
    for fmt in (
        '%m/%d/%Y %I:%M %p',   # 9/22/2021 4:03 PM
        '%m/%d/%Y %H:%M',      # 9/22/2021 16:03
        '%m/%d/%Y',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%m-%d-%Y',
        '%m/%d/%y',
    ):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None

def _money(value):
    if not value:
        return None
    try:
        return float(re.sub(r'[$,\s]', '', str(value)))
    except ValueError:
        return None

def _norm(header):
    return re.sub(r'[^a-z0-9]', '', header.lower())

def _get(row, norm_map, *candidates):
    for c in candidates:
        key = _norm(c)
        if key in norm_map:
            return row.get(norm_map[key], '').strip()
    return ''


_last_import: dict = {}


@bp.route('', methods=['POST'])
@login_required
def import_csv():
    try:
        return _do_import()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'error': f'Unexpected server error: {exc}'}), 500


def _do_import():
    uploaded = request.files.get('file') or request.files.get('csv_file')
    if not uploaded:
        return jsonify({'error': 'No file. Use field name "file" or "csv_file".'}), 400

    raw = uploaded.stream.read()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    lines = [l for l in content.splitlines() if l.strip()]  # drop blank lines
    if not lines:
        return jsonify({'error': 'Uploaded file is empty.'}), 400

    # Towbook exports always have exactly 2 metadata rows before column headers:
    #   Row 0: "Report - Impounds"
    #   Row 1: export timestamp (e.g. "Exported: 06/29/2026 10:34 AM")
    #   Row 2: column headers
    #   Row 3+: data
    # We skip rows 0 and 1 unconditionally.
    if len(lines) < 3:
        return jsonify({
            'error': f'File has only {len(lines)} non-empty row(s). '
                     'Expected a Towbook impound CSV with 2 metadata rows then column headers.',
            'first_row': lines[0] if lines else '',
        }), 400

    csv_body = '\n'.join(lines[2:])
    reader = csv.DictReader(io.StringIO(csv_body))
    headers = reader.fieldnames or []
    norm_map = {_norm(h): h for h in headers}

    # Confirm we got a recognisable Towbook header row, not another metadata row
    if _norm('Stock #') not in norm_map and _norm('Stock') not in norm_map:
        return jsonify({
            'error': "Could not find a 'Stock #' column — is this a Towbook Impounds CSV? "
                     "Check that the file was exported from Towbook's Impounds report.",
            'detected_headers': headers[:15],
        }), 400

    inserted = updated = skipped = 0
    errors = []
    dept_unmatched = []      # police impound rows whose Account field didn't fuzzy-match any department
    csv_stock_numbers = []   # collect every stock # seen in this CSV

    for row_idx, row in enumerate(reader):
        stock = None
        try:
            stock = _get(row, norm_map, 'Stock #', 'Stock #', 'Stock')
            if not stock:
                skipped += 1
                continue
            csv_stock_numbers.append(stock)

            tasks = _parse_tasks(_get(row, norm_map, 'Tasks'))

            impound_date = _parse_date(_get(row, norm_map, 'Impound Date'))
            # Release Date exists in CSV but Vehicle has no release_date column;
            # use it only to flip status to RELEASED on existing records.
            release_date = _parse_date(_get(row, norm_map, 'Release Date'))

            year_raw = _get(row, norm_map, 'Year')
            year = year_raw or None  # stored as VARCHAR(10) now

            have_keys_raw = _get(row, norm_map, 'Have Keys').lower()
            have_keys = have_keys_raw in ('yes', 'true', '1', 'y')

            # Daily Storage Total from Towbook = accumulated charge (rate × days).
            # Store in balance_due only when no explicit Balance Due value is present.
            balance_due = (
                _money(_get(row, norm_map, 'Balance Due'))
                or _money(_get(row, norm_map, 'Total'))
                or _money(_get(row, norm_map, 'Daily Storage Total'))
            )

            fields = {
                'stock_number':     stock,
                'call_number':      _get(row, norm_map, 'Call #', 'Call') or None,
                'invoice_number':   _get(row, norm_map, 'Invoice #', 'Invoice') or None,
                'account':          _get(row, norm_map, 'Account') or None,
                'color':            _get(row, norm_map, 'Color') or None,
                'make':             _get(row, norm_map, 'Make') or None,
                'model':            _get(row, norm_map, 'Model') or None,
                'year':             year,
                'plate':            _get(row, norm_map, 'Plate') or None,
                'plate_state':      _get(row, norm_map, 'Plate State') or None,
                'vin':              _get(row, norm_map, 'VIN') or None,
                'impound_reason':   _get(row, norm_map, 'Impound Reason') or None,
                'impound_date':     impound_date,
                'storage_location': _get(row, norm_map, 'Storage Lot') or None,
                'have_keys':        have_keys,
                'balance_due':      balance_due,
                'last_synced':      datetime.utcnow(),
                **tasks,
            }

            existing = Vehicle.query.filter_by(stock_number=stock).first()
            if existing:
                for k, v in fields.items():
                    if v is not None:
                        setattr(existing, k, v)
                # If Towbook shows a release date, mark the vehicle released
                if release_date and existing.status == 'ACTIVE':
                    existing.status = 'RELEASED'
                existing.towbook_seen = True  # seen in this CSV — eligible for future possible_release checks
                existing.updated_at = datetime.utcnow()
                updated += 1
                vehicle_for_dept_match = existing
            else:
                if not impound_date:
                    skipped += 1
                    continue
                v = Vehicle(
                    **fields,
                    impound_type='PPI',
                    status='RELEASED' if release_date else 'ACTIVE',
                    towbook_seen=True,  # inserted via CSV — eligible for future possible_release checks
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.session.add(v)
                inserted += 1
                vehicle_for_dept_match = v

            # Police department fee lookup: Towbook's Account field carries
            # the requesting department name for POLICE impounds. Fuzzy-match
            # it against police_departments.name; log (don't fail) on a miss.
            if vehicle_for_dept_match.impound_type == 'POLICE' and fields.get('account'):
                dept = _match_police_department(fields['account'])
                if dept:
                    vehicle_for_dept_match.police_department_id = dept.id
                else:
                    dept_unmatched.append({'row': row_idx + 3, 'stock': stock, 'account': fields['account']})

        except Exception as exc:
            errors.append({'row': row_idx + 3, 'stock': stock or '?', 'error': str(exc)})

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'error': f'Database error while saving: {exc}'}), 500

    # Flag active vehicles absent from this CSV as possible releases
    possible_release_count = 0
    try:
        from tina_sync import check_possible_releases, flag_vehicle_possible_release
        for v in check_possible_releases(csv_stock_numbers):
            flag_vehicle_possible_release(v.id)
            possible_release_count += 1
    except Exception as exc:
        current_app.logger.warning(f'possible-release check failed: {exc}')

    # Recalculate task pipeline for all active vehicles after every sync
    try:
        from task_engine import recalculate_all
        urgency_counts = recalculate_all()
    except Exception as exc:
        urgency_counts = {'error': str(exc)}

    # Record this sync so the dashboard banner clears
    try:
        today = date.today()
        triggered_by = 'unknown'
        try:
            if current_user.is_authenticated:
                triggered_by = current_user.username
        except Exception:
            pass
        sync_log = SyncLog(
            sync_date=today,
            source='csv_manual',
            status='ok',
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            call_count=inserted + updated,
            triggered_by=triggered_by,
            created_at=datetime.utcnow(),
        )
        db.session.add(sync_log)
        db.session.commit()
    except Exception:
        pass  # Don't let logging failure break the import response

    global _last_import
    _last_import = {
        'ok': True,
        'filename': uploaded.filename,
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'possible_releases_flagged': possible_release_count,
        'errors': errors,
        'department_unmatched': dept_unmatched,
        'urgency': urgency_counts,
        'imported_at': datetime.utcnow().isoformat(),
    }
    return jsonify(_last_import)


@bp.route('/status', methods=['GET'])
def status():
    if not _last_import:
        return jsonify({'ok': True, 'message': 'No import has run yet this session.'})
    return jsonify(_last_import)
