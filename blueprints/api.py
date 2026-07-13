"""
CORS-enabled REST API for Base44 apps (LotCheck, Tow Command Hub, Tina Tracker).
All endpoints return JSON. Authentication via API key header.
"""
import os
from datetime import date, datetime
from functools import wraps
from flask import Blueprint, request, jsonify
try:
    from flask_cors import cross_origin
except ImportError:
    def cross_origin(*args, **kwargs):
        def decorator(f):
            return f
        return decorator
from models import db, Vehicle, CertifiedLetter, TitleFiling, VehicleNote

bp = Blueprint('api', __name__, url_prefix='/api/v1')

API_KEY = os.environ.get('API_KEY')  # Must be set in environment — no default


def api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            return jsonify({'error': 'API not configured on server'}), 503
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
        'nada_value_override': v.nada_value_override,
        'effective_nada_value': v.effective_nada_value,
        'nada_needs_verification': v.nada_needs_verification,
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


# ── Lotcheck Integration ────────────────────────────────────────────────────

@bp.route('/vehicles/vin/<vin>', methods=['GET'])
@cross_origin()
@api_key_required
def get_vehicle_by_vin(vin):
    """Lotcheck: look up a vehicle by VIN and return full status."""
    v = Vehicle.query.filter(Vehicle.vin.ilike(vin.strip())).first()
    if not v:
        return jsonify({'found': False, 'vin': vin}), 404

    data = _vehicle_json(v)
    data['found'] = True

    # Title status
    tf = v.title_filing
    data['title'] = {
        'filed': tf is not None,
        'filed_date': tf.filed_date.isoformat() if tf and tf.filed_date else None,
        'bmv_receipt': tf.bmv_receipt_number if tf else None,
        'status': tf.status if tf else None,
    }

    # Towbook status
    data['towbook'] = {
        'synced': v.last_synced is not None,
        'stock_number': v.stock_number,
        'account': v.account,
        'have_keys': v.have_keys,
        'balance_due': v.balance_due,
        'last_synced': v.last_synced.isoformat() if v.last_synced else None,
    }

    # Auction / disposition
    data['auction'] = {
        'flagged': v.disposition == 'SELL',
        'disposition': v.disposition,
        'tina_stage': v.tina_stage,
        'stage_label': v.stage_label,
        'outcome': v.disposition_outcome,
    }

    # Open UPS notices
    try:
        from models import VehicleNotice
        notices = VehicleNotice.query.filter_by(vehicle_id=v.id).all()
        data['notices'] = [{
            'notice_number': n.notice_number,
            'sent_at': n.sent_at.isoformat() if n.sent_at else None,
            'tracking_number': n.tracking_number,
            'status': n.status,
        } for n in notices]
    except Exception:
        data['notices'] = []

    return jsonify(data)


@bp.route('/vehicles/<int:vehicle_id>/scan-log', methods=['POST'])
@cross_origin()
@api_key_required
def scan_log(vehicle_id):
    """Lotcheck: record a scan event for a vehicle."""
    v = Vehicle.query.get_or_404(vehicle_id)
    payload = request.get_json() or {}
    scanner = payload.get('scanner', 'Lotcheck')
    location = payload.get('location', '')
    note_body = f'[Lotcheck Scan] Scanned by {scanner}'
    if location:
        note_body += f' at {location}'
    db.session.add(VehicleNote(
        vehicle_id=v.id,
        body=note_body,
        author='Lotcheck (API)',
        created_at=datetime.utcnow(),
    ))
    v.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'vehicle_id': vehicle_id, 'logged': note_body})


# ── Tow Command Integration ─────────────────────────────────────────────────

def _get_or_create_tow_command_thread():
    """Get or create the Tow Command announcements chat thread."""
    from models import ChatThread, ChatThreadMember, User as _User
    thread = ChatThread.query.filter_by(title='Tow Command').first()
    if not thread:
        thread = ChatThread(title='Tow Command', is_group=True)
        db.session.add(thread)
        db.session.flush()
        # Add Tim, Lawrence, and dispatcher users
        key_roles = {'tim', 'lawrence', 'lori', 'dispatcher'}
        for u in _User.query.filter(_User.role.in_(key_roles)).all():
            db.session.add(ChatThreadMember(thread_id=thread.id, user_id=u.id))
        db.session.commit()
    return thread


@bp.route('/tow-command/message', methods=['POST'])
@cross_origin()
@api_key_required
def tow_command_message():
    """Tow Command: push a callout/event/announcement into the shared chat thread."""
    from models import ChatThread, ChatMessage
    data = request.get_json() or {}
    text = (data.get('message') or data.get('text') or '').strip()
    sender = (data.get('sender') or data.get('from') or 'Tow Command').strip()
    msg_type = (data.get('type') or 'announcement').strip()

    if not text:
        return jsonify({'error': 'message required'}), 400

    thread = _get_or_create_tow_command_thread()

    body = f'[{msg_type.upper()}] {sender}: {text}' if sender != 'Tow Command' else f'[{msg_type.upper()}] {text}'

    msg = ChatMessage(
        thread_id=thread.id,
        user_id=None,
        username='Tow Command',
        body=body,
        is_wally=False,
        alert_type='tow_command',
        created_at=datetime.utcnow(),
    )
    db.session.add(msg)
    db.session.commit()

    # Broadcast via Socket.IO if available
    try:
        from app import socketio
        socketio.emit('new_message', {
            'id': msg.id,
            'thread_id': thread.id,
            'username': 'Tow Command',
            'body': body,
            'is_wally': False,
            'created_at': msg.created_at.isoformat(),
        }, room=f'thread_{thread.id}', namespace='/chat')
    except Exception:
        pass

    return jsonify({'ok': True, 'message_id': msg.id, 'thread_id': thread.id})


@bp.route('/tow-command/messages', methods=['GET'])
@cross_origin()
@api_key_required
def tow_command_messages():
    """Tow Command: fetch recent messages from the shared thread."""
    from models import ChatThread, ChatMessage
    thread = ChatThread.query.filter_by(title='Tow Command').first()
    if not thread:
        return jsonify({'messages': [], 'thread_id': None})

    limit = min(int(request.args.get('limit', 50)), 200)
    messages = (
        ChatMessage.query
        .filter_by(thread_id=thread.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify({
        'thread_id': thread.id,
        'messages': [{
            'id': m.id,
            'username': m.username,
            'body': m.body,
            'alert_type': m.alert_type,
            'created_at': m.created_at.isoformat() if m.created_at else None,
        } for m in reversed(messages)],
    })


# ── Tina Pipeline API ────────────────────────────────────────────────────────

PIPELINE_STAGES = [
    ('TITLE_PENDING',  'Title Pending'),
    ('TITLE_COMPLETE', 'Title Complete'),
    ('SERVICE_EVAL',   'Service Evaluation'),
    ('AUCTION_CAND',   'Auction Candidate'),
    ('KEY_INSPECT',    'Key Inspection'),
    ('ROUTED_LIVE',    'Live Auction'),
    ('ROUTED_ONLINE',  'Online Auction'),
    ('ROUTED_JUNK',    'Junk Route'),
]

PIPELINE_STAGE_KEYS = [s[0] for s in PIPELINE_STAGES]


@bp.route('/pipeline', methods=['GET'])
@cross_origin()
@api_key_required
def pipeline_board():
    """Tina Tracker: return all vehicles grouped by pipeline stage."""
    stages = {}
    for key, label in PIPELINE_STAGES:
        vehicles = Vehicle.query.filter_by(tina_stage=key).order_by(Vehicle.impound_date.asc()).all()
        stages[key] = {
            'label': label,
            'count': len(vehicles),
            'vehicles': [_vehicle_json(v) for v in vehicles],
        }
    return jsonify({'stages': stages, 'stage_order': PIPELINE_STAGE_KEYS})


@bp.route('/pipeline/<int:vehicle_id>/move', methods=['POST'])
@cross_origin()
@api_key_required
def pipeline_move(vehicle_id):
    """Tina Tracker: move a vehicle to a new pipeline stage."""
    v = Vehicle.query.get_or_404(vehicle_id)
    data = request.get_json() or {}
    new_stage = (data.get('stage') or '').upper()

    if new_stage not in PIPELINE_STAGE_KEYS:
        return jsonify({'error': f'Invalid stage. Must be one of: {PIPELINE_STAGE_KEYS}'}), 400

    old_stage = v.tina_stage
    v.tina_stage = new_stage
    v.updated_at = datetime.utcnow()

    stage_label = dict(PIPELINE_STAGES).get(new_stage, new_stage)
    db.session.add(VehicleNote(
        vehicle_id=v.id,
        body=f'Pipeline stage changed to "{stage_label}" via API',
        author='API',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    # Post Wally alert for important stage transitions
    _post_pipeline_alert(v, old_stage, new_stage, stage_label)

    return jsonify({'ok': True, 'vehicle_id': vehicle_id, 'stage': new_stage})


def _post_pipeline_alert(vehicle, old_stage, new_stage, stage_label):
    """Post a Wally alert to the appropriate chat thread when pipeline stage changes."""
    try:
        from models import ChatThread, ChatMessage, ChatThreadMember, User as _User

        # Determine who to alert based on the new stage
        alert_roles = {
            'TITLE_COMPLETE': {'tina'},
            'SERVICE_EVAL': {'tim', 'lawrence', 'lori'},
            'AUCTION_CAND': {'tim', 'lawrence', 'lori'},
            'KEY_INSPECT': {'tim', 'dispatcher'},
            'ROUTED_LIVE': {'tina', 'tim'},
            'ROUTED_ONLINE': {'tina', 'tim'},
            'ROUTED_JUNK': {'tina', 'tim'},
        }
        roles = alert_roles.get(new_stage)
        if not roles:
            return

        thread = ChatThread.query.filter_by(title='Wally Alerts').first()
        if not thread:
            thread = ChatThread(title='Wally Alerts', is_group=True)
            db.session.add(thread)
            db.session.flush()
            for u in _User.query.filter(_User.role.in_({'tim', 'lawrence', 'lori', 'tina'})).all():
                db.session.add(ChatThreadMember(thread_id=thread.id, user_id=u.id))

        alert_messages = {
            'TITLE_COMPLETE': f'✅ {vehicle.display_name} title is complete — ready for service evaluation.',
            'SERVICE_EVAL': f'🔧 {vehicle.display_name} needs service evaluation.',
            'AUCTION_CAND': f'🏷 {vehicle.display_name} flagged as auction candidate.',
            'KEY_INSPECT': f'🔑 {vehicle.display_name} needs key inspection.',
            'ROUTED_LIVE': f'🔨 {vehicle.display_name} routed to live auction.',
            'ROUTED_ONLINE': f'💻 {vehicle.display_name} listed for online auction.',
            'ROUTED_JUNK': f'♻️ {vehicle.display_name} routed to junkyard.',
        }

        body = alert_messages.get(new_stage, f'{vehicle.display_name} moved to {stage_label}.')
        db.session.add(ChatMessage(
            thread_id=thread.id,
            username='Wally',
            is_wally=True,
            alert_type='pipeline',
            body=body,
        ))
        db.session.commit()
    except Exception:
        pass
