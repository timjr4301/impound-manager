"""
Feature 4 — Invoice Camera page for Tim Sr., Lawrence, and Lori.
Big camera button → Claude reads invoice/check → confirm → log payment.
"""
import json
import os
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from models import db, Vehicle, PaymentTransaction

bp = Blueprint('invoice_camera', __name__, url_prefix='/invoice-camera')

# Roles allowed to use this feature
_ALLOWED_ROLES = {'tim', 'lawrence', 'lori'}


def _allowed(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in _ALLOWED_ROLES:
            return jsonify({'error': 'Access restricted'}), 403
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/')
@login_required
def index():
    if current_user.role not in _ALLOWED_ROLES:
        from flask import flash, redirect, url_for
        flash('Invoice Camera is restricted.', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('invoice_camera/index.html')


@bp.route('/read', methods=['POST'])
@_allowed
def read_invoice():
    """Use Claude vision to extract invoice/check data from a photo."""
    data = request.get_json() or {}
    image_b64 = data.get('image', '')
    media_type = data.get('media_type', 'image/jpeg')

    if not image_b64:
        return jsonify({'error': 'No image provided'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 500

    # Strip data URL prefix if present
    if ',' in image_b64:
        image_b64 = image_b64.split(',', 1)[1]
        media_type = data.get('media_type', 'image/jpeg')

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=512,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': image_b64,
                        },
                    },
                    {
                        'type': 'text',
                        'text': (
                            'This is a photo of an invoice or check for an impound/towing company. '
                            'Extract the key fields and respond ONLY with valid JSON (no extra text):\n'
                            '{\n'
                            '  "account_name": "company or person name paying, null if not found",\n'
                            '  "amount": 0.00,\n'
                            '  "invoice_number": "invoice or check number as string, null if not found",\n'
                            '  "date": "YYYY-MM-DD or null",\n'
                            '  "check_number": "check number if this is a check, null otherwise",\n'
                            '  "payment_type": "CHECK or CASH or CARD or null",\n'
                            '  "notes": "any other relevant info"\n'
                            '}'
                        ),
                    },
                ],
            }],
        )

        raw = response.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            result = json.loads(m.group()) if m else {}

        return jsonify({'ok': True, 'data': result})

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@bp.route('/search')
@_allowed
def search():
    """Search vehicles/accounts matching a query string."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    like = f'%{q}%'
    vehicles = (
        Vehicle.query
        .filter(
            db.or_(
                Vehicle.account.ilike(like),
                Vehicle.owner_name.ilike(like),
                Vehicle.plate.ilike(like),
                Vehicle.call_number.ilike(like),
                Vehicle.stock_number.ilike(like),
            )
        )
        .filter(Vehicle.status == 'ACTIVE')
        .order_by(Vehicle.impound_date.desc())
        .limit(15)
        .all()
    )
    return jsonify([{
        'id': v.id,
        'display_name': v.display_name,
        'account': v.account or v.owner_name or '',
        'plate': v.plate or '',
        'call_number': v.call_number or '',
        'balance_due': v.balance_due or 0,
    } for v in vehicles])


@bp.route('/confirm', methods=['POST'])
@_allowed
def confirm():
    """Log a confirmed payment against a vehicle."""
    data = request.get_json() or {}
    vehicle_id = data.get('vehicle_id')
    amount_raw = data.get('amount', 0)
    payment_type = data.get('payment_type', 'CASH').upper()
    reference_number = (data.get('reference_number') or '').strip() or None
    notes = (data.get('notes') or '').strip() or None

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid amount'}), 400

    if amount <= 0:
        return jsonify({'error': 'Amount must be positive'}), 400

    if not vehicle_id:
        return jsonify({'error': 'vehicle_id required'}), 400

    vehicle = db.session.get(Vehicle, int(vehicle_id))
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404

    txn = PaymentTransaction(
        vehicle_id=vehicle.id,
        amount=amount,
        payment_type=payment_type,
        payment_date=datetime.utcnow(),
        reference_number=reference_number,
        notes=notes,
        processed_by=current_user.display_name or current_user.username,
        created_at=datetime.utcnow(),
    )
    db.session.add(txn)

    # Update vehicle's storage_paid running total
    vehicle.storage_paid = (vehicle.storage_paid or 0) + amount
    vehicle.payment_date = datetime.utcnow().date()
    vehicle.payment_reference = reference_number

    db.session.commit()

    return jsonify({
        'ok': True,
        'transaction_id': txn.id,
        'vehicle': vehicle.display_name,
        'amount': amount,
    })
