"""
Status Audit Tool — Tim-only backlog triage dashboard.

Read-mostly: every flag here is computed at query time from columns that
already exist (letters, lka_document_confirmed, title_search_confirmed,
title_filing, possible_release). No new columns, no ALTER TABLE. The one
CSV cross-reference (Towbook Mismatch) is parsed and matched in memory per
request and never persisted — it's a session tool, not a data source.
"""
import csv
import io
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user

from models import db, Vehicle, VehicleNote
from towbook_import import _norm, _get, _parse_date

bp = Blueprint('audit', __name__, url_prefix='/audit')

# Coarse backlog-sweep thresholds. Deliberately looser/simpler than the
# PPI/POLICE-specific due-date math already used by Vehicle.stoplight_color
# (5/10-day Letter 1, 30-day Letter 2) — that logic drives Heather's live
# queue (which also only covers vehicles impounded since 2024-01-01). This
# tool exists to catch anything that fell all the way through that net
# across the full 650-vehicle backlog, so it uses one flat rule instead.
OVERDUE_LETTER1_DAYS = 14
OVERDUE_LETTER2_GAP_DAYS = 30
OVERDUE_TITLE_DAYS = 60


def _tim_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != 'tim':
            if request.method == 'GET':
                flash('That page is Tim-only.', 'danger')
                return redirect(url_for('dashboard'))
            return jsonify({'error': 'Tim-only action.'}), 403
        return f(*args, **kwargs)
    return login_required(decorated)


def _overdue_letter_reasons(v):
    """Returns a list of human-readable reasons this vehicle is flagged, or [] if clean."""
    reasons = []
    l1, l2 = v.letter1, v.letter2

    if not l1 or not l1.sent_date:
        if v.days_in_storage > OVERDUE_LETTER1_DAYS:
            reasons.append('Letter 1 not sent')
    elif not l2 or not l2.sent_date:
        if (date.today() - l1.sent_date).days > OVERDUE_LETTER2_GAP_DAYS:
            reasons.append('Letter 2 not sent')

    if v.days_in_storage > OVERDUE_TITLE_DAYS:
        if not v.title_filing or not v.title_filing.filed_date:
            reasons.append('Title not filed')

    return reasons


def _last_letter_sent(v):
    if v.letter2 and v.letter2.sent_date:
        return v.letter2.sent_date
    if v.letter1 and v.letter1.sent_date:
        return v.letter1.sent_date
    return None


def _active_not_ghost():
    """Base population for every section: active, non-ghost vehicles."""
    return (
        Vehicle.query
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release.isnot(True))
    )


@bp.route('/')
@_tim_required
def index():
    active_vehicles = _active_not_ghost().all()
    total_active = len(active_vehicles)

    overdue = []
    for v in active_vehicles:
        reasons = _overdue_letter_reasons(v)
        if reasons:
            overdue.append({
                'vehicle': v,
                'reasons': reasons,
                'last_letter_sent': _last_letter_sent(v),
            })
    overdue.sort(key=lambda r: r['vehicle'].days_in_storage, reverse=True)

    missing_lka = sorted(
        (v for v in active_vehicles if not v.lka_document_confirmed),
        key=lambda v: v.days_in_storage, reverse=True,
    )
    missing_title_search = sorted(
        (v for v in active_vehicles if not v.title_search_confirmed),
        key=lambda v: v.days_in_storage, reverse=True,
    )

    return render_template(
        'audit/index.html',
        total_active=total_active,
        overdue=overdue,
        missing_lka=missing_lka,
        missing_title_search=missing_title_search,
    )


@bp.route('/towbook-check', methods=['POST'])
@_tim_required
def towbook_check():
    uploaded = request.files.get('file')
    if not uploaded:
        return jsonify({'error': 'No file uploaded. Use field name "file".'}), 400

    raw = uploaded.stream.read()
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    lines = [l for l in content.splitlines() if l.strip()]
    if len(lines) < 3:
        return jsonify({
            'error': f'File has only {len(lines)} non-empty row(s). '
                     'Expected a Towbook Impounds export with 2 metadata rows then column headers.',
        }), 400

    # Same 2-metadata-row skip as the main Towbook importer (towbook_import.py).
    csv_body = '\n'.join(lines[2:])
    reader = csv.DictReader(io.StringIO(csv_body))
    headers = reader.fieldnames or []
    norm_map = {_norm(h): h for h in headers}

    if _norm('Stock #') not in norm_map and _norm('Stock') not in norm_map:
        return jsonify({
            'error': "Could not find a 'Stock #' column — is this a Towbook Impounds export?",
        }), 400

    # Towbook's export has no dedicated "status" column — the existing importer
    # (towbook_import.py) already treats a populated "Release Date" as the
    # released signal, so this reuses that same interpretation.
    total_records = 0
    csv_release_date = {}
    for row in reader:
        stock = _get(row, norm_map, 'Stock #', 'Stock')
        if not stock:
            continue
        total_records += 1
        csv_release_date[stock.strip().upper()] = _parse_date(_get(row, norm_map, 'Release Date'))

    active_vehicles = (
        _active_not_ghost()
        .filter(Vehicle.stock_number.isnot(None))
        .all()
    )
    active_by_stock = {v.stock_number.strip().upper(): v for v in active_vehicles if v.stock_number}

    matched = 0
    mismatches = []
    for key, release_date in csv_release_date.items():
        v = active_by_stock.get(key)
        if not v:
            continue
        matched += 1
        if release_date:
            mismatches.append(v)

    mismatches.sort(key=lambda v: v.days_in_storage, reverse=True)

    return jsonify({
        'ok': True,
        'total_records': total_records,
        'matched': matched,
        'mismatches': [{
            'id': v.id,
            'stock_number': v.stock_number,
            'display_name': v.display_name,
            'plate': v.plate,
            'impound_date': v.impound_date.strftime('%m/%d/%Y') if v.impound_date else None,
            'days_in': v.days_in_storage,
            'detail_url': url_for('vehicles_detail', vehicle_id=v.id),
        } for v in mismatches],
    })


def _log_and_commit(vehicle, note_body):
    vehicle.updated_at = datetime.utcnow()
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=note_body,
        author=current_user.display_name or current_user.username,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()


@bp.route('/vehicles/<int:vehicle_id>/mark-released', methods=['POST'])
@_tim_required
def mark_released(vehicle_id):
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404
    who = current_user.display_name or current_user.username
    vehicle.status = 'RELEASED'
    _log_and_commit(vehicle, f'Marked Released via Status Audit Tool (Towbook mismatch) by {who}.')
    return jsonify({'ok': True})


@bp.route('/vehicles/<int:vehicle_id>/mark-lka-confirmed', methods=['POST'])
@_tim_required
def mark_lka_confirmed(vehicle_id):
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404
    who = current_user.display_name or current_user.username
    vehicle.lka_document_confirmed = True
    _log_and_commit(vehicle, f'LKA marked confirmed via Status Audit Tool by {who}.')
    return jsonify({'ok': True})


@bp.route('/vehicles/<int:vehicle_id>/mark-title-search-confirmed', methods=['POST'])
@_tim_required
def mark_title_search_confirmed(vehicle_id):
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404
    who = current_user.display_name or current_user.username
    vehicle.title_search_confirmed = True
    _log_and_commit(vehicle, f'Title search marked confirmed via Status Audit Tool by {who}.')
    return jsonify({'ok': True})
