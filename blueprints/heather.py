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
from markupsafe import escape
from models import db, Vehicle, CertifiedLetter, EnvelopeScan, VehicleNote
from sqlalchemy import or_, func
from models import PPI_LETTER1_DAYS, PPI_LETTER2_DAYS, POLICE_LETTER1_DAYS
from permissions import require_permission

bp = Blueprint('heather', __name__, url_prefix='/heather')

# Vehicles impounded before this date are excluded from Heather's daily
# queues (BMV Search Queue, Overdue/Urgent, Due Soon, On Track) until the
# historical review screen is built. They remain in the database untouched.
HEATHER_QUEUE_CUTOFF = date(2024, 1, 1)


def _heather_required(f):
    """Tim + Heather can perform Heather actions."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_heather:
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


def _heather_view(f):
    """Tim + Heather + Tina can VIEW Heather's dashboard data."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_see_heather_dashboard:
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/')
@_heather_view
def dashboard():
    today = date.today()
    week_ahead = today + timedelta(days=7)

    # Query by stored letter_urgency — fast DB filter, no Python-side computation
    red = (Vehicle.query
           .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
           .filter(Vehicle.letter_urgency == 'RED')
           .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
           .order_by(Vehicle.impound_date.asc())
           .all())
    yellow = (Vehicle.query
              .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
              .filter(Vehicle.letter_urgency == 'YELLOW')
              .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
              .order_by(Vehicle.impound_date.asc())
              .all())
    green = (Vehicle.query
             .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
             .filter(Vehicle.letter_urgency == 'GREEN')
             .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
             .order_by(Vehicle.impound_date.desc())
             .all())

    # Fallback: if all urgencies are null (first run, not yet backfilled), recalculate now
    if not red and not yellow and not green:
        try:
            uncalculated = Vehicle.query.filter(
                Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']),
                Vehicle.letter_urgency.is_(None)
            ).count()
            if uncalculated > 0:
                from task_engine import recalculate_all
                recalculate_all()
                red = (Vehicle.query
                       .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
                       .filter(Vehicle.letter_urgency == 'RED')
                       .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
                       .order_by(Vehicle.impound_date.asc())
                       .all())
                yellow = (Vehicle.query
                          .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
                          .filter(Vehicle.letter_urgency == 'YELLOW')
                          .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
                          .order_by(Vehicle.impound_date.asc())
                          .all())
                green = (Vehicle.query
                         .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
                         .filter(Vehicle.letter_urgency == 'GREEN')
                         .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
                         .order_by(Vehicle.impound_date.desc())
                         .all())
        except Exception as exc:
            db.session.rollback()
            print(f'[heather.dashboard] urgency backfill error: {exc}')

    # Task 5 URGENT — No Record Found vehicles (unresolved)
    urgent_vehicles = (Vehicle.query
                       .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
                       .filter(Vehicle.task_no_record == True)
                       .filter(db.or_(Vehicle.task_no_record_resolved == False,
                                      Vehicle.task_no_record_resolved.is_(None)))
                       .order_by(Vehicle.impound_date.asc())
                       .all())

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

    # BMV search queue — vehicles on Task 1 needing owner lookup
    bmv_queue = (Vehicle.query
                 .filter(Vehicle.status == 'ACTIVE')
                 .filter(db.or_(Vehicle.heather_complete == False,
                                Vehicle.heather_complete.is_(None)))
                 .filter(db.or_(Vehicle.bmv_stage.in_([None, 'PENDING', 'QUEUED'])))
                 .filter(Vehicle.impound_date >= HEATHER_QUEUE_CUTOFF)
                 .order_by(Vehicle.impound_date.asc())
                 .limit(100)
                 .all())

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

    # Last recalculation time — most recently updated vehicle with urgency set
    last_calc = (
        db.session.query(db.func.max(Vehicle.updated_at))
        .filter(Vehicle.letter_urgency.isnot(None))
        .scalar()
    )

    return render_template('heather/dashboard.html',
        today=today,
        red=red, yellow=yellow, green=green,
        urgent_vehicles=urgent_vehicles,
        overdue=overdue, due_today=due_today, due_this_week=due_this_week,
        bmv_queue=bmv_queue,
        sent_unconfirmed=sent_unconfirmed,
        last_calc=last_calc,
        can_act=current_user.is_heather,  # Tina can view but not act
    )


@bp.route('/recalculate', methods=['POST'])
@_heather_required
def recalculate():
    """Manually trigger task pipeline recalculation for all active vehicles."""
    from task_engine import recalculate_all
    counts = recalculate_all()
    flash(
        f'Recalculated: {counts.get("RED", 0)} overdue, '
        f'{counts.get("YELLOW", 0)} due soon, '
        f'{counts.get("GREEN", 0)} on track.',
        'success'
    )
    return redirect(url_for('heather.dashboard'))


@bp.route('/mark-no-record/<int:vehicle_id>', methods=['POST'])
@_heather_required
def mark_no_record(vehicle_id):
    """Flag a vehicle as No Record Found (Task 5 URGENT)."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    notes = request.form.get('notes', '').strip()
    vehicle.task_no_record = True
    vehicle.task_no_record_notes = notes or 'No record found in BMV system'
    vehicle.task_no_record_resolved = False
    vehicle.heather_complete = True
    vehicle.bmv_stage = 'NO_RECORD'
    vehicle.updated_at = datetime.utcnow()
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=f'URGENT: No Record Found in BMV. {notes or ""}',
        author=current_user.display_name or 'Heather',
        created_at=datetime.utcnow(),
    ))
    from task_engine import recalculate_vehicle
    recalculate_vehicle(vehicle)
    db.session.commit()
    flash(f'{vehicle.display_name} flagged as No Record Found — Tim has been alerted.', 'danger')
    return redirect(request.referrer or url_for('heather.dashboard'))


@bp.route('/resolve-urgent/<int:vehicle_id>', methods=['POST'])
@require_permission('all_access')
def resolve_urgent(vehicle_id):
    """Admin-only: clear the No Record Found URGENT flag."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.task_no_record_resolved = True
    vehicle.task_no_record_resolved_by = current_user.display_name or 'Tim'
    vehicle.task_no_record_resolved_date = date.today()
    vehicle.updated_at = datetime.utcnow()
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=f'No Record Found flag resolved by {current_user.display_name or "Tim"}. '
             f'{request.form.get("resolution_notes", "").strip()}',
        author=current_user.display_name or 'Tim',
        created_at=datetime.utcnow(),
    ))
    from task_engine import recalculate_vehicle
    recalculate_vehicle(vehicle)
    db.session.commit()
    flash(f'Urgent flag cleared for {vehicle.display_name}.', 'success')
    return redirect(request.referrer or url_for('dashboard'))


@bp.route('/file-checklist/<int:vehicle_id>', methods=['POST'])
@_heather_required
def update_file_checklist(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.lka_document_confirmed = 'lka_document_confirmed' in request.form
    vehicle.title_search_confirmed = 'title_search_confirmed' in request.form
    vehicle.ups_delivery_confirmed = 'ups_delivery_confirmed' in request.form
    vehicle.return_receipt_filed   = 'return_receipt_filed' in request.form
    vehicle.updated_at = datetime.utcnow()
    db.session.commit()
    flash('File checklist updated.', 'success')
    return redirect(request.referrer or url_for('heather.dashboard'))


@bp.route('/bmv-complete/<int:vehicle_id>', methods=['POST'])
@_heather_required
def bmv_complete(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    if not vehicle.file_complete_for_tina:
        missing = []
        if not vehicle.lka_document_confirmed:
            missing.append('LKA document (BMV 2433)')
        if not vehicle.title_search_confirmed:
            missing.append('Title search (BMV 1148)')
        if not any(l.tracking_number for l in vehicle.letters):
            missing.append('UPS tracking number')
        if not vehicle.ups_delivery_confirmed:
            missing.append('UPS delivery confirmation')
        if not vehicle.return_receipt_filed:
            missing.append('Return receipt filed')
        flash(
            f'Cannot hand off to Tina — file incomplete. Missing: '
            f'{", ".join(missing)}',
            'danger'
        )
        return redirect(request.referrer or url_for('heather.dashboard'))

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


# ── UPS Notices ────────────────────────────────────────────────────────────────

def _ups_get_token():
    """Fetch a short-lived OAuth2 token from UPS."""
    import requests as _req
    client_id = os.environ.get('UPS_CLIENT_ID', '')
    client_secret = os.environ.get('UPS_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        raise RuntimeError('UPS_CLIENT_ID / UPS_CLIENT_SECRET not configured')
    resp = _req.post(
        'https://onlinetools.ups.com/security/v1/oauth/token',
        data={'grant_type': 'client_credentials'},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def _ups_create_label(vehicle, notice_number, recipient_name, recipient_address,
                      recipient_city, recipient_state, recipient_zip):
    """Call UPS Ship API and return (tracking_number, label_b64_gif)."""
    import requests as _req
    account_number = os.environ.get('UPS_ACCOUNT_NUMBER', '81Y7X1')
    token = _ups_get_token()

    company_name = current_app.config.get('COMPANY_NAME', 'Broad & James Towing')
    company_address = current_app.config.get('COMPANY_ADDRESS', '3201 E Broad St')

    shipper_line = '4301 E 5th Ave'
    shipper_city = 'Columbus'
    shipper_state = 'OH'
    shipper_zip = '43219'

    reference = (vehicle.call_number or vehicle.plate or f'VEH{vehicle.id}')[:35]

    payload = {
        'ShipmentRequest': {
            'Shipment': {
                'Shipper': {
                    'Name': company_name,
                    'ShipperNumber': account_number,
                    'Address': {
                        'AddressLine': [shipper_line],
                        'City': shipper_city,
                        'StateProvinceCode': shipper_state,
                        'PostalCode': shipper_zip,
                        'CountryCode': 'US',
                    },
                },
                'ShipTo': {
                    'Name': recipient_name,
                    'Address': {
                        'AddressLine': [recipient_address or ''],
                        'City': recipient_city or '',
                        'StateProvinceCode': (recipient_state or 'OH')[:2],
                        'PostalCode': recipient_zip or '',
                        'CountryCode': 'US',
                    },
                },
                'ShipFrom': {
                    'Name': company_name,
                    'Address': {
                        'AddressLine': [shipper_line],
                        'City': shipper_city,
                        'StateProvinceCode': shipper_state,
                        'PostalCode': shipper_zip,
                        'CountryCode': 'US',
                    },
                },
                'Service': {'Code': '03', 'Description': 'UPS Ground'},
                'Package': {
                    'PackagingType': {'Code': '02', 'Description': 'Customer Supplied Package'},
                    'Dimensions': {
                        'UnitOfMeasurement': {'Code': 'IN'},
                        'Length': '9', 'Width': '6', 'Height': '1',
                    },
                    'PackageWeight': {
                        'UnitOfMeasurement': {'Code': 'LBS'},
                        'Weight': '0.1',
                    },
                    'ReferenceNumber': {'Value': reference},
                },
                'PaymentInformation': {
                    'ShipmentCharge': {
                        'Type': '01',
                        'BillShipper': {'AccountNumber': account_number},
                    },
                },
            },
            'LabelSpecification': {
                'LabelImageFormat': {'Code': 'GIF', 'Description': 'GIF'},
            },
        },
    }

    resp = _req.post(
        'https://onlinetools.ups.com/api/shipments/v1801/ship',
        json=payload,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'transId': f'notice-{vehicle.id}-{notice_number}',
            'transactionSrc': 'impound-manager',
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data['ShipmentResponse']['ShipmentResults']
    pkg = results['PackageResults']
    if isinstance(pkg, list):
        pkg = pkg[0]
    tracking = pkg['TrackingNumber']
    label_b64 = pkg['ShippingLabel']['GraphicImage']
    return tracking, label_b64


@bp.route('/notices')
@_heather_required
def notices_search():
    from models import VehicleNotice
    q = request.args.get('q', '').strip()
    results = []
    if q:
        like = f'%{q}%'
        results = (
            Vehicle.query
            .filter(
                db.or_(
                    Vehicle.plate.ilike(like),
                    Vehicle.vin.ilike(like),
                    Vehicle.owner_name.ilike(like),
                    Vehicle.call_number.ilike(like),
                    Vehicle.stock_number.ilike(like),
                )
            )
            .order_by(Vehicle.impound_date.desc())
            .limit(30)
            .all()
        )
        # Pre-load notices for each vehicle
        for v in results:
            _ = v.notices  # noqa: trigger lazy load
    recent_notices = (
        VehicleNotice.query
        .order_by(VehicleNotice.sent_at.desc())
        .limit(20)
        .all()
    )
    return render_template('heather/notices_search.html',
                           q=q, results=results, recent_notices=recent_notices)


@bp.route('/notices/send-prefill')
@_heather_required
def send_prefill():
    invoice = request.args.get('invoice', '').strip()
    recipient_type = request.args.get('type', '').strip()

    vehicle = Vehicle.query.filter_by(invoice_number=invoice).first()
    if not vehicle:
        return f'Vehicle not found for invoice {escape(invoice)}. Check the invoice number and try again.'

    if recipient_type == 'owner':
        name = vehicle.owner_name
        address = vehicle.owner_address
        city = vehicle.owner_city
        state = vehicle.owner_state
        zip_code = vehicle.owner_zip
    elif recipient_type == 'lienholder':
        if not vehicle.lienholder_name or not vehicle.lienholder_name.strip():
            return f'No lienholder on record for invoice {escape(invoice)}.'
        name = vehicle.lienholder_name
        address = vehicle.lienholder_address
        city = vehicle.lienholder_city
        state = vehicle.lienholder_state
        zip_code = vehicle.lienholder_zip
    else:
        return 'Invalid type. Use owner or lienholder.'

    return redirect(url_for(
        'heather.notices',
        vehicle_id=vehicle.id,
        prefill_name=name or '',
        prefill_address=address or '',
        prefill_city=city or '',
        prefill_state=state or '',
        prefill_zip=zip_code or '',
        prefill_type=recipient_type,
    ))


@bp.route('/notices/<int:vehicle_id>')
@_heather_required
def notices(vehicle_id):
    from models import VehicleNotice
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    notices = (
        VehicleNotice.query
        .filter_by(vehicle_id=vehicle.id)
        .order_by(VehicleNotice.notice_number)
        .all()
    )
    next_notice_number = len(notices) + 1
    ups_configured = bool(
        os.environ.get('UPS_CLIENT_ID') and os.environ.get('UPS_CLIENT_SECRET')
    )
    label_b64 = request.args.get('label')
    prefill = None
    if request.args.get('prefill_type'):
        prefill = {
            'name': request.args.get('prefill_name', ''),
            'address': request.args.get('prefill_address', ''),
            'city': request.args.get('prefill_city', ''),
            'state': request.args.get('prefill_state', ''),
            'zip': request.args.get('prefill_zip', ''),
            'type': request.args.get('prefill_type', ''),
        }
    return render_template('heather/notices.html',
                           vehicle=vehicle,
                           notices=notices,
                           next_notice_number=next_notice_number,
                           ups_configured=ups_configured,
                           label_b64=label_b64,
                           prefill=prefill)


@bp.route('/notices/<int:vehicle_id>/send', methods=['POST'])
@_heather_required
def send_notice(vehicle_id):
    from models import VehicleNotice
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    recipient_name = request.form.get('recipient_name', '').strip()
    recipient_address = request.form.get('recipient_address', '').strip()
    recipient_city = request.form.get('recipient_city', '').strip()
    recipient_state = request.form.get('recipient_state', 'OH').strip()
    recipient_zip = request.form.get('recipient_zip', '').strip()
    notes = request.form.get('notes', '').strip() or None

    if not recipient_name:
        flash('Recipient name is required.', 'danger')
        return redirect(url_for('heather.notices', vehicle_id=vehicle.id))

    existing_count = VehicleNotice.query.filter_by(vehicle_id=vehicle.id).count()
    notice_number = existing_count + 1

    try:
        tracking, label_b64 = _ups_create_label(
            vehicle, notice_number,
            recipient_name, recipient_address,
            recipient_city, recipient_state, recipient_zip,
        )
        notice = VehicleNotice(
            vehicle_id=vehicle.id,
            notice_number=notice_number,
            tracking_number=tracking,
            label_data=label_b64,
            recipient_name=recipient_name,
            recipient_address=recipient_address,
            recipient_city=recipient_city,
            recipient_state=recipient_state,
            recipient_zip=recipient_zip,
            sent_by=current_user.display_name or current_user.username,
            notes=notes,
        )
        db.session.add(notice)

        # Wally alert to Tim/Lawrence
        try:
            from models import ChatThread, ChatMessage, ChatThreadMember
            from models import User as _User
            alert_thread = (
                ChatThread.query
                .filter(ChatThread.title == 'Wally Alerts')
                .first()
            )
            if not alert_thread:
                alert_thread = ChatThread(title='Wally Alerts', is_group=True)
                db.session.add(alert_thread)
                db.session.flush()
                for u in _User.query.filter(_User.role.in_(['tim', 'lawrence', 'lori'])).all():
                    db.session.add(ChatThreadMember(thread_id=alert_thread.id, user_id=u.id))
            db.session.add(ChatMessage(
                thread_id=alert_thread.id,
                username='Wally',
                is_wally=True,
                alert_type='ups_notice',
                body=(
                    f'📬 UPS Notice #{notice_number} sent for {vehicle.display_name} '
                    f'(plate {vehicle.plate or "—"}) to {recipient_name}. '
                    f'Tracking: {tracking}'
                ),
            ))
        except Exception:
            pass  # don't fail the whole request for a chat alert

        db.session.commit()
        flash(
            f'Notice #{notice_number} sent! Tracking: {tracking}',
            'success',
        )
        return redirect(
            url_for('heather.notices', vehicle_id=vehicle.id) + f'?label={label_b64}'
        )
    except Exception as exc:
        flash(f'UPS API error: {exc}', 'danger')
        return redirect(url_for('heather.notices', vehicle_id=vehicle.id))


@bp.route('/letters')
@_heather_view
def letters():
    """Letters management tab — sent/pending/RTS status for all active vehicles."""
    today = date.today()

    # All unsent letters (pending)
    pending = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(CertifiedLetter.sent_date.is_(None))
        .order_by(CertifiedLetter.due_date.asc())
        .all()
    )

    # Sent letters awaiting delivery confirmation
    awaiting = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(CertifiedLetter.sent_date.isnot(None))
        .filter(CertifiedLetter.delivery_confirmed_date.is_(None))
        .order_by(CertifiedLetter.sent_date.asc())
        .all()
    )

    # Return-to-sender letters
    returned = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(CertifiedLetter.return_to_sender == True)
        .filter(Vehicle.status == 'ACTIVE')
        .order_by(CertifiedLetter.sent_date.desc())
        .all()
    )

    # Fully confirmed deliveries (last 30 days)
    confirmed = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(CertifiedLetter.delivery_confirmed_date.isnot(None))
        .filter(CertifiedLetter.delivery_confirmed_date >= today - timedelta(days=30))
        .order_by(CertifiedLetter.delivery_confirmed_date.desc())
        .limit(50)
        .all()
    )

    # Recent envelope scans
    from models import EnvelopeScan
    recent_scans = (
        EnvelopeScan.query
        .order_by(EnvelopeScan.scan_date.desc())
        .limit(30)
        .all()
    )

    return render_template('heather/letters.html',
        today=today,
        pending=pending,
        awaiting=awaiting,
        returned=returned,
        confirmed=confirmed,
        recent_scans=recent_scans,
        can_act=current_user.is_heather,
    )


@bp.route('/letters/scan', methods=['POST'])
@_heather_required
def letters_scan():
    """AJAX: Claude vision reads a scanned envelope and updates the matching letter."""
    data = request.get_json() or {}
    image_b64 = data.get('image', '')
    letter_id = data.get('letter_id')

    if not image_b64:
        return jsonify({'error': 'No image provided'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 500

    if ',' in image_b64:
        image_b64 = image_b64.split(',', 1)[1]

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=512,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': image_b64},
                    },
                    {
                        'type': 'text',
                        'text': (
                            'This is a USPS certified mail envelope or return card for an impound lot. '
                            'Respond ONLY with valid JSON — no extra text:\n'
                            '{\n'
                            '  "tracking_number": "full 20-22 digit USPS tracking number, null if not visible",\n'
                            '  "outcome": "DELIVERED | RETURNED | UNDELIVERABLE | UNKNOWN",\n'
                            '  "delivery_date": "YYYY-MM-DD if stamped, null if not",\n'
                            '  "return_reason": "reason envelope was returned, null if delivered",\n'
                            '  "notes": "any other relevant stamps or markings"\n'
                            '}'
                        ),
                    },
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        import json, re
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            result = json.loads(m.group()) if m else {}

        # Update the letter if an ID was provided
        updated_letter = None
        if letter_id:
            letter = CertifiedLetter.query.get(int(letter_id))
            if letter:
                outcome = result.get('outcome', 'UNKNOWN')
                if outcome == 'DELIVERED':
                    if not letter.delivery_confirmed_date:
                        raw_d = result.get('delivery_date')
                        try:
                            letter.delivery_confirmed_date = (
                                date.fromisoformat(raw_d) if raw_d else date.today()
                            )
                        except ValueError:
                            letter.delivery_confirmed_date = date.today()
                elif outcome in ('RETURNED', 'UNDELIVERABLE'):
                    letter.return_to_sender = True
                    if not letter.notes:
                        letter.notes = result.get('return_reason') or outcome
                    # Add urgent note to vehicle
                    db.session.add(VehicleNote(
                        vehicle_id=letter.vehicle_id,
                        body=(
                            f'ALERT: {letter.label} returned / undeliverable. '
                            f'{result.get("return_reason") or ""} — '
                            'Heather must verify address and resend.'
                        ),
                        author='Envelope Scanner',
                        created_at=datetime.utcnow(),
                    ))
                    # Update letter urgency
                    letter.vehicle.letter_urgency = 'RED'

                db.session.commit()
                updated_letter = {
                    'id': letter.id,
                    'label': letter.label,
                    'vehicle': letter.vehicle.display_name,
                    'outcome': outcome,
                }

        return jsonify({'ok': True, 'result': result, 'updated_letter': updated_letter})

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@bp.route('/letters/<int:letter_id>/mark-rts', methods=['POST'])
@_heather_required
def mark_return_to_sender(letter_id):
    """Mark a letter as returned to sender."""
    letter = db.get_or_404(CertifiedLetter, letter_id)
    letter.return_to_sender = True
    notes = request.form.get('notes', '').strip()
    if notes:
        letter.notes = notes
    letter.vehicle.letter_urgency = 'RED'
    db.session.add(VehicleNote(
        vehicle_id=letter.vehicle_id,
        body=f'Letter #{letter.letter_number} returned to sender. {notes}',
        author=current_user.display_name or 'Heather',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    flash(f'{letter.vehicle.display_name} — {letter.label} marked Return to Sender. Address must be fixed.', 'danger')
    return redirect(url_for('heather.letters'))


@bp.route('/envelope-scan/camera', methods=['POST'])
@_heather_required
def envelope_scan_camera():
    """Process image from IPEVO camera via Claude vision."""
    data = request.get_json()
    image_b64 = data.get('image')

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

        # Auto-match: look up certified_letters by the tracking number the AI
        # just read, so the front end can auto-select the vehicle instead of
        # making Heather find it in the dropdown herself.
        matched_vehicle = None
        raw_tracking = result.get('tracking_number')
        if raw_tracking:
            import re
            normalized = re.sub(r'[\s-]', '', raw_tracking).upper()
            matched_letter = (
                CertifiedLetter.query
                .filter(CertifiedLetter.tracking_number.isnot(None))
                .filter(func.upper(func.replace(
                    func.replace(CertifiedLetter.tracking_number, ' ', ''), '-', ''
                )) == normalized)
                .first()
            )
            if matched_letter:
                matched_vehicle = matched_letter.vehicle

        return jsonify({
            'ok': True,
            'result': result,
            'raw': raw,
            'matched_vehicle': {
                'id': matched_vehicle.id,
                'display_name': matched_vehicle.display_name,
            } if matched_vehicle else None,
        })

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@bp.route('/envelope-scan/match-save', methods=['POST'])
@_heather_required
def envelope_scan_match_save():
    """AJAX: one-click save of an AI-read envelope scan to the matched (or manually picked) vehicle."""
    data = request.get_json() or {}
    vehicle_id = data.get('vehicle_id')
    tracking = (data.get('tracking_number') or '').strip()

    if not vehicle_id or not tracking:
        return jsonify({'error': 'Vehicle and tracking number are required.'}), 400

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found.'}), 404

    is_rts = bool(data.get('is_return_to_sender'))
    is_delivered = bool(data.get('is_delivered'))
    tracking_clean = tracking.replace(' ', '').upper()

    delivery_date = None
    if is_delivered:
        raw_date = data.get('delivery_date')
        try:
            delivery_date = date.fromisoformat(raw_date) if raw_date else date.today()
        except (ValueError, TypeError):
            delivery_date = date.today()

    scan = EnvelopeScan(
        vehicle_id=vehicle.id,
        tracking_number=tracking_clean,
        scan_date=datetime.utcnow(),
        scan_notes=data.get('notes') or None,
        is_return_to_sender=is_rts,
        is_delivered=is_delivered,
        delivery_date=delivery_date,
        claude_raw_response=data.get('raw'),
    )
    db.session.add(scan)

    # Update the matching letter if we can find one, same as manual entry does
    letters = CertifiedLetter.query.filter_by(vehicle_id=vehicle.id).all()
    for ltr in letters:
        if not ltr.tracking_number:
            ltr.tracking_number = tracking_clean
            if is_rts:
                ltr.return_to_sender = True
            if is_delivered and not ltr.delivery_confirmed_date:
                ltr.delivery_confirmed_date = delivery_date
            break

    db.session.commit()
    return jsonify({'ok': True, 'vehicle': vehicle.display_name})
