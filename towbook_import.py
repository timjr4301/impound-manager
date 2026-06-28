"""
Flask Blueprint — Towbook CSV import
POST /api/import-towbook        upload CSV, upsert vehicles by stock_number
GET  /api/import-towbook/status last import result
"""
import csv, io, re
from datetime import datetime
from flask import Blueprint, request, jsonify
from models import db, Vehicle

bp = Blueprint('towbook_import', __name__, url_prefix='/api/import-towbook')

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
def import_csv():
    uploaded = request.files.get('file') or request.files.get('csv_file')
    if not uploaded:
        return jsonify({'error': 'No file. Use field name "file" or "csv_file".'}), 400

    raw = uploaded.stream.read()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    lines = content.splitlines()
    if len(lines) < 3:
        return jsonify({'error': 'File too short — need at least 3 rows (title, date, header).'}), 400

    # Skip row 0 (report title) and row 1 (report date); row 2 is the header
    csv_body = '\n'.join(lines[2:])
    reader = csv.DictReader(io.StringIO(csv_body))
    headers = reader.fieldnames or []
    norm_map = {_norm(h): h for h in headers}   # normalized → original header

    inserted = updated = skipped = 0
    errors = []

    for row_idx, row in enumerate(reader):
        try:
            stock = _get(row, norm_map, 'Stock #', 'Stock')
            if not stock:
                skipped += 1
                continue

            tasks = _parse_tasks(_get(row, norm_map, 'Tasks'))

            impound_date = _parse_date(_get(row, norm_map, 'Impound Date'))
            release_date = _parse_date(_get(row, norm_map, 'Release Date'))

            year_raw = _get(row, norm_map, 'Year')
            year = int(year_raw) if year_raw.isdigit() else None

            have_keys_raw = _get(row, norm_map, 'Have Keys').lower()
            have_keys = have_keys_raw in ('yes', 'true', '1', 'y')

            fields = {
                'stock_number':   stock,
                'call_number':    _get(row, norm_map, 'Call #', 'Call') or None,
                'invoice_number': _get(row, norm_map, 'Invoice #', 'Invoice') or None,
                'account':        _get(row, norm_map, 'Account') or None,
                'color':          _get(row, norm_map, 'Color') or None,
                'make':           _get(row, norm_map, 'Make') or None,
                'model':          _get(row, norm_map, 'Model') or None,
                'year':           year,
                'plate':          _get(row, norm_map, 'Plate') or None,
                'plate_state':    _get(row, norm_map, 'Plate State') or None,
                'vin':            _get(row, norm_map, 'VIN') or None,
                'impound_reason': _get(row, norm_map, 'Impound Reason') or None,
                'impound_date':   impound_date,
                'storage_location': _get(row, norm_map, 'Storage Lot') or None,
                'have_keys':      have_keys,
                'balance_due':    _money(_get(row, norm_map, 'Balance Due')),
                'last_synced':    datetime.utcnow(),
                **tasks,
            }

            existing = Vehicle.query.filter_by(stock_number=stock).first()
            if existing:
                for k, v in fields.items():
                    if v is not None:
                        setattr(existing, k, v)
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                if not impound_date:
                    skipped += 1
                    continue
                v = Vehicle(
                    **fields,
                    impound_type='PPI',
                    status='ACTIVE',
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.session.add(v)
                inserted += 1

        except Exception as exc:
            errors.append({'row': row_idx + 3, 'stock': stock if 'stock' in dir() else '?', 'error': str(exc)})

    db.session.commit()

    global _last_import
    _last_import = {
        'ok': True,
        'filename': uploaded.filename,
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
        'imported_at': datetime.utcnow().isoformat(),
    }
    return jsonify(_last_import)


@bp.route('/status', methods=['GET'])
def status():
    if not _last_import:
        return jsonify({'ok': True, 'message': 'No import has run yet this session.'})
    return jsonify(_last_import)
