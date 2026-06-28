"""
CORS-enabled REST API for Base44 apps (LotCheck, Tow Command Hub).
All endpoints return JSON. Authentication via API key header.
"""
import os
from datetime import date, datetime
from functools import wraps
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from models import db, Vehicle, CertifiedLetter, TitleFiling

bp = Blueprint('api', __name__, url_prefix='/api/v1')

API_KEY = os.environ.get('API_KEY', 'change-me-in-production')


def api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if key != API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def _vehicle_json(v):
    today = date.today()
    return {
        'id': v.id,
        'vin': v.vin,
        'plate': v.plate,
        'plate_state': v.plate_state,
        'year': v.year,
        'make': v.make,
        'model': v.model_name or v.model,
        'color': v.color,
        'impound_type': v.impound_type,
        'impound_date': v.impound_date.isoformat() if v.impound_date else None,
        'days_in_storage': v.days_in_storage,
        'status': v.status,
        'owner_name': v.owner_name,
        'owner_address': v.owner_address,
        'stock_number': v.stock_number,
        'storage_location': v.storage_location,
        'tow_fee': v.tow_fee,
        'daily_storage_rate': v.daily_storage_rate,
        'nada_value': v.nada_value,
        'total_owed': round((v.tow_fee or 0) + v.total_storage_owed, 2),
        'disposition': v.disposition,
        'stoplight': v.stoplight_color,
        'is_title_eligible': v.is_title_eligible,
        'next_action': v.next_action_label,
        'updated_at': v.updated_at.isoformat() if v.updated_at else None,
    }


@bp.route('/vehicles', methods=['GET'])
@cross_origin()
@api_key_required
def list_vehicles():
    status = request.args.get('status', 'ACTIVE')
    vehicles = Vehicle.query.filter_by(status=status).order_by(Vehicle.impound_date.desc()).all()
    return jsonify({'vehicles': [_vehicle_json(v) for v in vehicles], 'count': len(vehicles)})


@bp.route('/vehicles/<int:vehicle_id>', methods=['GET'])
@cross_origin()
@api_key_required
def get_vehicle(vehicle_id):
    v = Vehicle.query.get_or_404(vehicle_id)
    data = _vehicle_json(v)
    data['letters'] = [{
        'id': l.id,
        'label': l.label,
        'letter_number': l.letter_number,
        'due_date': l.due_date.isoformat(),
        'sent_date': l.sent_date.isoformat() if l.sent_date else None,
        'tracking_number': l.tracking_number,
        'delivery_confirmed_date': l.delivery_confirmed_date.isoformat() if l.delivery_confirmed_date else None,
        'is_overdue': l.is_overdue,
    } for l in v.letters]
    return jsonify(data)


@bp.route('/vehicles/search', methods=['GET'])
@cross_origin()
@api_key_required
def search_vehicles():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'q parameter required'}), 400
    like = f'%{q}%'
    results = Vehicle.query.filter(
        db.or_(
            Vehicle.plate.ilike(like),
            Vehicle.vin.ilike(like),
            Vehicle.make.ilike(like),
            Vehicle.model_name.ilike(like),
            Vehicle.owner_name.ilike(like),
            Vehicle.stock_number.ilike(like),
        )
    ).order_by(Vehicle.impound_date.desc()).limit(50).all()
    return jsonify({'results': [_vehicle_json(v) for v in results], 'count': len(results)})


@bp.route('/lot-status', methods=['GET'])
@cross_origin()
@api_key_required
def lot_status():
    """Summary for LotCheck dashboard."""
    today = date.today()
    active = Vehicle.query.filter_by(status='ACTIVE').all()
    pending_letters = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(CertifiedLetter.sent_date.is_(None))
        .all()
    )
    return jsonify({
        'total_active': len(active),
        'total_in_storage': len(active),
        'letters_overdue': sum(1 for l in pending_letters if l.is_overdue),
        'letters_due_today': sum(1 for l in pending_letters if l.is_due_today),
        'title_eligible': sum(1 for v in active if v.is_title_eligible and v.title_filing is None),
        'red_vehicles': sum(1 for v in active if v.stoplight_color == 'red'),
        'yellow_vehicles': sum(1 for v in active if v.stoplight_color == 'yellow'),
        'green_vehicles': sum(1 for v in active if v.stoplight_color == 'green'),
        'as_of': datetime.utcnow().isoformat(),
    })


@bp.route('/vehicles/<int:vehicle_id>/checkin', methods=['POST'])
@cross_origin()
@api_key_required
def checkin_vehicle(vehicle_id):
    """Dispatch endpoint — log a note on vehicle arrival."""
    v = Vehicle.query.get_or_404(vehicle_id)
    from models import VehicleNote
    note_text = request.json.get('note', 'Vehicle checked in via dispatch.') if request.is_json else 'Vehicle checked in via dispatch.'
    db.session.add(VehicleNote(
        vehicle_id=v.id,
        body=note_text,
        author='Dispatch (API)',
        created_at=datetime.utcnow(),
    ))
    v.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'vehicle_id': vehicle_id})


@bp.route('/dispatch/board', methods=['GET'])
@cross_origin()
@api_key_required
def dispatch_board():
    """Tow Command Hub board — active vehicles with Towbook sync data."""
    active = (
        Vehicle.query
        .filter_by(status='ACTIVE')
        .filter(Vehicle.stock_number.isnot(None))
        .order_by(Vehicle.impound_date.desc())
        .all()
    )
    return jsonify({
        'vehicles': [{
            'id': v.id,
            'stock_number': v.stock_number,
            'display_name': v.display_name,
            'impound_date': v.impound_date.isoformat(),
            'days_in_storage': v.days_in_storage,
            'storage_location': v.storage_location,
            'have_keys': v.have_keys,
            'balance_due': v.balance_due,
            'tasks_overdue': v.tasks_overdue,
            'tasks_due_today': v.tasks_due_today,
            'impound_reason': v.impound_reason,
            'stoplight': v.stoplight_color,
        } for v in active],
        'count': len(active),
        'as_of': datetime.utcnow().isoformat(),
    })


@bp.route('/health', methods=['GET'])
@cross_origin()
def health():
    return jsonify({'ok': True, 'service': 'impound-manager', 'time': datetime.utcnow().isoformat()})
