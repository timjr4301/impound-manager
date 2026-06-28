"""
Heather's dashboard — letter tracking, BMV queue, stoplight view,
envelope scan intake, UPS label generation, letter templates.
"""
import os
import base64
import json
from datetime import date, datetime, timedelta
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app, jsonify)
from flask_login import login_required, current_user
from models import db, Vehicle, CertifiedLetter, EnvelopeScan, VehicleNote
from models import PPI_LETTER1_DAYS, PPI_LETTER2_DAYS, POLICE_LETTER1_DAYS

bp = Blueprint('heather', __name__, url_prefix='/heather')


def _heather_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_heather:
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/')
@_heather_required
def dashboard():
    today = date.today()
    week_ahead = today + timedelta(days=7)

    active_vehicles = Vehicle.query.filter_by(status='ACTIVE').all()

    red = [v for v in active_vehicles if v.stoplight_color == 'red']
    yellow = [v for v in active_vehicles if v.stoplight_color == 'yellow']
    green = [v for v in active_vehicles if v.stoplight_color == 'green']

    pending_letters = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(CertifiedLetter.sent_date.is_(None))
        .all()
    )

    overdue = sorted([l for l in pending_letters if l.due_date < today], key=lambda l: l.due_date)
    due_today = [l for l in pending_letters if l.due_date == today]
    due_this_week = sorted(
        [l for l in pending_letters if today < l.due_date <= week_ahead],
        key=lambda l: l.due_date
    )

    # BMV search queue — vehicles needing owner lookup
    bmv_queue = [v for v in active_vehicles
                 if (not v.owner_name or not v.owner_address)
                 and v.bmv_stage in (None, 'PENDING', 'QUEUED')]

    # Awaiting delivery confirmation
    sent_unconfirmed = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(CertifiedLetter.sent_date.isnot(None))
        .filter(CertifiedLetter.delivery_confirmed_date.is_(None))
        .order_by(CertifiedLetter.sent_date.asc())
        .all()
    )

    return render_template('heather/dashboard.html',
        today=today,
        red=red, yellow=yellow, green=green,
        overdue=overdue, due_today=due_today, due_this_week=due_this_week,
        bmv_queue=bmv_queue,
        sent_unconfirmed=sent_unconfirmed,
    )


@bp.route('/bmv-complete/<int:vehicle_id>', methods=['POST'])
@_heather_required
def bmv_complete(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.bmv_stage = 'COMPLETE'
    vehicle.bmv_searched_date = date.today()
    vehicle.bmv_search_notes = request.form.get('notes', '').strip() or None
    vehicle.heather_complete = True
    vehicle.heather_complete_date = date.today()
    vehicle.tina_stage = 'QUEUED'
    vehicle.updated_at = datetime.utcnow()

    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=f'BMV search complete. Handed off to Tina. {vehicle.bmv_search_notes or ""}',
        author=current_user.display_name or 'Heather',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    flash(f'{vehicle.display_name} marked BMV complete — appeared in Tina\'s queue.', 'success')
    return redirect(url_for('heather.dashboard'))


@bp.route('/letter-template/<int:letter_id>')
@_heather_required
def letter_template(letter_id):
    letter = db.get_or_404(CertifiedLetter, letter_id)
    vehicle = letter.vehicle
    return render_template('heather/letter_template.html',
        letter=letter,
        vehicle=vehicle,
        today=date.today(),
        company_name=current_app.config['COMPANY_NAME'],
        company_address=current_app.config['COMPANY_ADDRESS'],
        company_phone=current_app.config['COMPANY_PHONE'],
        storage_address=current_app.config['STORAGE_ADDRESS'],
    )


@bp.route('/ups-label/<int:vehicle_id>')
@_heather_required
def ups_label(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    return render_template('heather/ups_label.html',
        vehicle=vehicle,
        company_name=current_app.config['COMPANY_NAME'],
        company_address=current_app.config['COMPANY_ADDRESS'],
    )


@bp.route('/envelope-scan', methods=['GET', 'POST'])
@_heather_required
def envelope_scan():
    """Manual envelope scan intake — enter tracking number or use camera."""
    if request.method == 'POST':
        vehicle_id = request.form.get('vehicle_id', type=int)
        tracking = request.form.get('tracking_number', '').strip()
        notes = request.form.get('notes', '').strip()
        is_rts = bool(request.form.get('return_to_sender'))
        is_delivered = bool(request.form.get('is_delivered'))

        if not vehicle_id or not tracking:
            flash('Vehicle and tracking number are required.', 'danger')
            return redirect(url_for('heather.envelope_scan'))

        vehicle = db.get_or_404(Vehicle, vehicle_id)

        scan = EnvelopeScan(
            vehicle_id=vehicle.id,
            tracking_number=tracking.replace(' ', '').upper(),
            scan_date=datetime.utcnow(),
            scan_notes=notes or None,
            is_return_to_sender=is_rts,
            is_delivered=is_delivered,
            delivery_date=date.today() if is_delivered else None,
        )
        db.session.add(scan)

        # Update the matching letter if we can find it
        letters = CertifiedLetter.query.filter_by(vehicle_id=vehicle.id).all()
        for ltr in letters:
            if not ltr.tracking_number:
                ltr.tracking_number = tracking.replace(' ', '').upper()
                if is_rts:
                    ltr.return_to_sender = True
                if is_delivered and not ltr.delivery_confirmed_date:
                    ltr.delivery_confirmed_date = date.today()
                break

        db.session.commit()
        flash(f'Envelope scan recorded for {vehicle.display_name}.', 'success')
        return redirect(url_for('heather.dashboard'))

    active_vehicles = Vehicle.query.filter_by(status='ACTIVE').order_by(Vehicle.impound_date.desc()).all()
    return render_template('heather/envelope_scan.html', vehicles=active_vehicles)


@bp.route('/envelope-scan/camera', methods=['POST'])
@_heather_required
def envelope_scan_camera():
    """Process image from IPEVO camera via Claude vision."""
    data = request.get_json()
    image_b64 = data.get('image')
    vehicle_id = data.get('vehicle_id')

    if not image_b64:
        return jsonify({'error': 'No image provided'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 500

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Strip data URL prefix if present
        if ',' in image_b64:
            image_b64 = image_b64.split(',', 1)[1]

        message = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1024,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': 'image/jpeg',
                            'data': image_b64,
                        },
                    },
                    {
                        'type': 'text',
                        'text': (
                            'This is a certified mail envelope or USPS label scanned for an impound lot. '
                            'Extract the following and respond ONLY with valid JSON:\n'
                            '{\n'
                            '  "tracking_number": "the full tracking number if visible, null if not",\n'
                            '  "is_return_to_sender": true/false,\n'
                            '  "is_delivered": true/false,\n'
                            '  "delivery_date": "YYYY-MM-DD if visible, null if not",\n'
                            '  "usps_stamps_visible": true/false,\n'
                            '  "notes": "any other relevant markings or status"\n'
                            '}'
                        ),
                    },
                ],
            }],
        )

        raw = message.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            result = json.loads(m.group()) if m else {}

        if vehicle_id:
            vehicle = Vehicle.query.get(vehicle_id)
            if vehicle:
                scan = EnvelopeScan(
                    vehicle_id=vehicle.id,
                    tracking_number=result.get('tracking_number'),
                    scan_date=datetime.utcnow(),
                    scan_notes=result.get('notes'),
                    is_return_to_sender=result.get('is_return_to_sender', False),
                    is_delivered=result.get('is_delivered', False),
                    claude_raw_response=raw,
                )
                if result.get('delivery_date'):
                    try:
                        scan.delivery_date = date.fromisoformat(result['delivery_date'])
                    except ValueError:
                        pass
                db.session.add(scan)
                db.session.commit()

        return jsonify({'ok': True, 'result': result})

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
