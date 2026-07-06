"""
Envelope Tab — manage scanned return-envelope images across all vehicles.

Builds on the existing envelope scanner (blueprints/heather.py) without
changing its matching logic: this only adds image storage (already wired
into heather.py's save routes) and this tab's Matched / Unmatched / Cleared
views on top of what the scanner already persists.
"""
from datetime import datetime
from functools import wraps

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user

from models import db, Vehicle, EnvelopeScan, VehicleNote
from blueprints.heather import _apply_afo_flag, _apply_scan_letter_effects

bp = Blueprint('envelopes', __name__, url_prefix='/envelopes')

ALLOWED_ROLES = ('heather', 'tim', 'brady', 'jim')
CLEAR_REASONS = ('vehicle_sold', 'vehicle_released', 'manual')


def _envelopes_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role not in ALLOWED_ROLES:
            if request.method == 'GET':
                flash('Access restricted.', 'danger')
                return redirect(url_for('dashboard'))
            return jsonify({'error': 'Access restricted.'}), 403
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/')
@_envelopes_required
def index():
    matched = (
        EnvelopeScan.query
        .join(Vehicle)
        .filter(EnvelopeScan.cleared_at.is_(None))
        .filter(Vehicle.possible_release.isnot(True))  # ghost vehicles excluded, same as letter/envelope queues elsewhere
        .order_by(EnvelopeScan.scan_date.desc())
        .all()
    )

    unmatched = (
        EnvelopeScan.query
        .filter(EnvelopeScan.vehicle_id.is_(None))
        .filter(EnvelopeScan.discarded.isnot(True))
        .order_by(EnvelopeScan.scan_date.desc())
        .all()
    )

    cleared = (
        EnvelopeScan.query
        .filter(EnvelopeScan.cleared_at.isnot(None))
        .order_by(EnvelopeScan.cleared_at.desc())
        .all()
    )

    active_vehicles = (
        Vehicle.query
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release.isnot(True))
        .order_by(Vehicle.impound_date.desc())
        .all()
    )

    return render_template(
        'envelopes/index.html',
        matched=matched,
        unmatched=unmatched,
        cleared=cleared,
        active_vehicles=active_vehicles,
    )


@bp.route('/<int:scan_id>/clear', methods=['POST'])
@_envelopes_required
def clear(scan_id):
    scan = db.session.get(EnvelopeScan, scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found.'}), 404
    if not scan.vehicle_id:
        return jsonify({'error': 'Only matched scans can be cleared.'}), 400

    data = request.get_json() or {}
    reason = data.get('reason')
    if reason not in CLEAR_REASONS:
        return jsonify({'error': f'Reason must be one of: {", ".join(CLEAR_REASONS)}.'}), 400

    scan.cleared_at = datetime.utcnow()
    scan.cleared_by = current_user.display_name or current_user.username
    scan.clear_reason = reason
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/<int:scan_id>/link', methods=['POST'])
@_envelopes_required
def link(scan_id):
    """Manually link an Unmatched scan to a vehicle — applies the same AFO
    flag + letter-timeline effects an automatic match would have, via the
    shared helpers in heather.py, so behavior is identical either way."""
    scan = db.session.get(EnvelopeScan, scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found.'}), 404
    if scan.vehicle_id:
        return jsonify({'error': 'This scan is already linked to a vehicle.'}), 400

    data = request.get_json() or {}
    vehicle_id = data.get('vehicle_id')
    if not vehicle_id:
        return jsonify({'error': 'vehicle_id is required.'}), 400

    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found.'}), 404

    scan.vehicle_id = vehicle.id
    scan.matched_by = 'manual'
    scan.scan_notes = f'Linked to {vehicle.display_name} via Envelopes tab. {scan.scan_notes or ""}'.strip()

    _apply_afo_flag(vehicle, scan.reference_number_2, detected_by='envelopes-tab-link')
    _apply_scan_letter_effects(
        vehicle,
        scan.tracking_number,
        scan.is_return_to_sender,
        scan.is_delivered,
        scan.delivery_date,
        scan.outcome,
        scan.scan_notes or '',
    )

    db.session.commit()
    return jsonify({'ok': True, 'vehicle': vehicle.display_name})


@bp.route('/<int:scan_id>/discard', methods=['POST'])
@_envelopes_required
def discard(scan_id):
    scan = db.session.get(EnvelopeScan, scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found.'}), 404
    if scan.vehicle_id:
        return jsonify({'error': 'Only unmatched scans can be discarded.'}), 400

    scan.discarded = True
    db.session.commit()
    return jsonify({'ok': True})
