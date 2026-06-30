"""
Tina's dashboard — title pipeline, court process tracker,
police affidavit tracker, junk/sell decisions, invoice creation.
"""
from datetime import date, datetime
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app, send_file)
from flask_login import login_required, current_user
from models import db, Vehicle, TitleFiling, Invoice, VehicleNote, DamageReport

bp = Blueprint('tina', __name__, url_prefix='/tina')


def _tina_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_tina:
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/')
@_tina_required
def dashboard():
    today = date.today()

    # Handoff queue — vehicles Heather marked complete that Tina hasn't started
    queued = (
        Vehicle.query
        .filter_by(heather_complete=True, tina_stage='QUEUED')
        .order_by(Vehicle.heather_complete_date.asc())
        .all()
    )

    # In-progress title work
    in_progress = (
        Vehicle.query
        .filter(Vehicle.tina_stage.in_(['TITLE_WORK', 'COURT', 'AFFIDAVIT']))
        .order_by(Vehicle.impound_date.asc())
        .all()
    )

    # Title-eligible and ready to file
    title_eligible = [
        v for v in Vehicle.query.filter_by(status='ACTIVE').all()
        if v.is_title_eligible and v.title_filing is None
    ]

    # Junk/Sell queue — need disposition decision
    disposition_needed = (
        Vehicle.query
        .filter_by(status='ACTIVE', heather_complete=True)
        .filter(Vehicle.disposition.is_(None))
        .filter(Vehicle.tina_stage.isnot(None))
        .order_by(Vehicle.impound_date.asc())
        .all()
    )

    # Court dates coming up
    court_upcoming = (
        Vehicle.query
        .filter(Vehicle.court_date.isnot(None))
        .filter(Vehicle.court_date >= today)
        .filter(Vehicle.status == 'ACTIVE')
        .order_by(Vehicle.court_date.asc())
        .all()
    )

    # Recent invoices
    recent_invoices = (
        Invoice.query
        .order_by(Invoice.created_at.desc())
        .limit(20)
        .all()
    )

    # Damage reports submitted by drivers — unreviewed (last 60 days)
    damage_reports = (
        DamageReport.query
        .filter(DamageReport.is_locked == False)
        .order_by(DamageReport.created_at.desc())
        .limit(50)
        .all()
    )

    return render_template('tina/dashboard.html',
        today=today,
        queued=queued,
        in_progress=in_progress,
        title_eligible=title_eligible,
        disposition_needed=disposition_needed,
        court_upcoming=court_upcoming,
        recent_invoices=recent_invoices,
        damage_reports=damage_reports,
    )


@bp.route('/set-stage/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_stage(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    stage = request.form.get('stage', '').strip()
    notes = request.form.get('notes', '').strip()
    if stage:
        vehicle.tina_stage = stage
        vehicle.updated_at = datetime.utcnow()
        if notes:
            db.session.add(VehicleNote(
                vehicle_id=vehicle.id,
                body=f'[Tina] Stage → {stage}: {notes}',
                author=current_user.display_name or 'Tina',
                created_at=datetime.utcnow(),
            ))
        db.session.commit()
        flash(f'{vehicle.display_name} moved to {stage}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/damage-report/<int:report_id>/lock', methods=['POST'])
@_tina_required
def lock_damage_report(report_id):
    """Mark a damage report as reviewed (locks it out of the unreviewed list)."""
    report = db.get_or_404(DamageReport, report_id)
    report.is_locked = True
    db.session.commit()
    flash('Damage report marked as reviewed.', 'success')
    return redirect(url_for('tina.dashboard'))


# ── Pipeline Board ──────────────────────────────────────────────────────────

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

PIPELINE_ALERT_TARGETS = {
    'TITLE_COMPLETE': {'tina'},
    'SERVICE_EVAL':   {'tim', 'lawrence', 'lori'},
    'AUCTION_CAND':   {'tim', 'lawrence', 'lori'},
    'KEY_INSPECT':    {'tim', 'dispatcher'},
    'ROUTED_LIVE':    {'tina', 'tim'},
    'ROUTED_ONLINE':  {'tina', 'tim'},
    'ROUTED_JUNK':    {'tina', 'tim'},
}

PIPELINE_ALERT_MSGS = {
    'TITLE_COMPLETE': '✅ {name} title is complete — ready for service evaluation.',
    'SERVICE_EVAL':   '🔧 {name} needs service evaluation.',
    'AUCTION_CAND':   '🏷 {name} flagged as auction candidate.',
    'KEY_INSPECT':    '🔑 {name} needs key inspection.',
    'ROUTED_LIVE':    '🔨 {name} routed to live auction.',
    'ROUTED_ONLINE':  '💻 {name} listed for online auction.',
    'ROUTED_JUNK':    '♻️ {name} routed to junkyard.',
}


@bp.route('/pipeline')
@_tina_required
def pipeline():
    from models import User
    stage_data = []
    for key, label in PIPELINE_STAGES:
        vehicles = (Vehicle.query
                    .filter_by(tina_stage=key)
                    .order_by(Vehicle.impound_date.asc())
                    .all())
        stage_data.append({'key': key, 'label': label, 'vehicles': vehicles})
    all_users = User.query.filter_by(is_active=True).order_by(User.display_name).all()
    return render_template('tina/pipeline.html',
                           stages=stage_data,
                           all_users=all_users,
                           pipeline_stages=PIPELINE_STAGES)


@bp.route('/pipeline/move/<int:vehicle_id>', methods=['POST'])
@_tina_required
def pipeline_move(vehicle_id):
    from flask import jsonify as _json
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    data = request.get_json() or {}
    new_stage = (data.get('stage') or '').upper()
    stage_keys = [s[0] for s in PIPELINE_STAGES]

    if new_stage not in stage_keys:
        return _json({'error': f'Invalid stage: {new_stage}'}), 400

    old_stage = vehicle.tina_stage
    vehicle.tina_stage = new_stage
    vehicle.updated_at = datetime.utcnow()

    stage_label = dict(PIPELINE_STAGES).get(new_stage, new_stage)
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=f'Pipeline stage → {stage_label}',
        author=current_user.display_name or 'Tina',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    # Post Wally alert
    try:
        from models import ChatThread, ChatMessage, ChatThreadMember, User as _User
        roles = PIPELINE_ALERT_TARGETS.get(new_stage)
        if roles:
            thread = ChatThread.query.filter_by(title='Wally Alerts').first()
            if not thread:
                thread = ChatThread(title='Wally Alerts', is_group=True)
                db.session.add(thread)
                db.session.flush()
                for u in _User.query.filter(_User.role.in_({'tim', 'lawrence', 'lori', 'tina'})).all():
                    db.session.add(ChatThreadMember(thread_id=thread.id, user_id=u.id))
            body = PIPELINE_ALERT_MSGS.get(new_stage, '{name} moved to ' + stage_label).format(
                name=vehicle.display_name
            )
            db.session.add(ChatMessage(thread_id=thread.id, username='Wally',
                                       is_wally=True, alert_type='pipeline', body=body))
            db.session.commit()
    except Exception:
        pass

    return _json({'ok': True, 'stage': new_stage, 'label': stage_label,
                  'vehicle_name': vehicle.display_name})


@bp.route('/set-disposition/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_disposition(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    disposition = request.form.get('disposition', '').strip().upper()
    notes = request.form.get('notes', '').strip()
    if disposition in ('SELL', 'JUNK', 'HOLD'):
        vehicle.disposition = disposition
        vehicle.disposition_set_date = date.today()
        vehicle.disposition_notes = notes or None
        vehicle.updated_at = datetime.utcnow()
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Disposition set to {disposition}. {notes}',
            author=current_user.display_name or 'Tina',
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'{vehicle.display_name} disposition set to {disposition}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/set-court/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_court(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    court_date_str = request.form.get('court_date', '').strip()
    notes = request.form.get('notes', '').strip()
    if court_date_str:
        vehicle.court_date = date.fromisoformat(court_date_str)
        vehicle.court_notes = notes or None
        vehicle.tina_stage = 'COURT'
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'Court date set for {vehicle.display_name}: {vehicle.court_date.strftime("%m/%d/%Y")}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/set-affidavit/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_affidavit(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    filed_str = request.form.get('filed_date', '').strip()
    notes = request.form.get('notes', '').strip()
    vehicle.affidavit_filed_date = date.fromisoformat(filed_str) if filed_str else date.today()
    vehicle.affidavit_notes = notes or None
    vehicle.tina_stage = 'AFFIDAVIT'
    vehicle.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f'Police affidavit filed for {vehicle.display_name}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/create-invoice/<int:vehicle_id>', methods=['GET', 'POST'])
@_tina_required
def create_invoice(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    if request.method == 'POST':
        invoice_type = request.form.get('invoice_type', '').upper()
        if invoice_type not in ('SALE', 'JUNK'):
            flash('Invalid invoice type.', 'danger')
            return redirect(url_for('tina.create_invoice', vehicle_id=vehicle_id))

        from titlebot.storage import calculate_storage
        storage_days, storage_total, _ = calculate_storage(
            vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
        )

        def _f(name):
            v = request.form.get(name, '').strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None

        # Build invoice number: INV-{type}-{vehicle_id}-{YYYYMMDD}
        inv_num = f'INV-{invoice_type[:1]}-{vehicle.id:05d}-{date.today().strftime("%Y%m%d")}'

        tow = vehicle.tow_fee or 0
        total_fees = tow + storage_total

        if invoice_type == 'SALE':
            sale_price = _f('sale_price') or 0
            net = sale_price - total_fees
            inv = Invoice(
                vehicle_id=vehicle.id,
                invoice_type='SALE',
                invoice_number=inv_num,
                issue_date=date.today(),
                buyer_name=request.form.get('buyer_name', '').strip() or None,
                buyer_address=request.form.get('buyer_address', '').strip() or None,
                sale_price=sale_price,
                tow_fee=tow,
                storage_fee=storage_total,
                storage_days=storage_days,
                total_fees=total_fees,
                net_proceeds=net,
                notes=request.form.get('notes', '').strip() or None,
            )
            vehicle.sale_price = sale_price
            vehicle.sale_date = date.today()
            vehicle.buyer_name = request.form.get('buyer_name', '').strip() or None
            vehicle.status = 'RELEASED'
            vehicle.tina_stage = 'COMPLETE'
        else:
            weight = _f('weight_lbs')
            ppt = _f('price_per_ton')
            gross = round((weight / 2000) * ppt, 2) if weight and ppt else 0
            net = gross - total_fees
            inv = Invoice(
                vehicle_id=vehicle.id,
                invoice_type='JUNK',
                invoice_number=inv_num,
                issue_date=date.today(),
                junk_yard_name=request.form.get('junk_yard_name', '').strip() or None,
                junk_yard_address=request.form.get('junk_yard_address', '').strip() or None,
                weight_lbs=weight,
                price_per_ton=ppt,
                tow_fee=tow,
                storage_fee=storage_total,
                storage_days=storage_days,
                total_fees=total_fees,
                net_proceeds=net,
                notes=request.form.get('notes', '').strip() or None,
            )
            vehicle.junk_weight_lbs = weight
            vehicle.junk_price_per_ton = ppt
            vehicle.junk_yard_name = request.form.get('junk_yard_name', '').strip() or None
            vehicle.status = 'RELEASED'
            vehicle.tina_stage = 'COMPLETE'

        db.session.add(inv)
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Invoice {inv_num} created ({invoice_type}). Net: ${net:.2f}',
            author=current_user.display_name or 'Tina',
            created_at=datetime.utcnow(),
        ))
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'Invoice {inv_num} created successfully.', 'success')
        return redirect(url_for('tina.invoice_print', invoice_id=inv.id))

    from titlebot.storage import calculate_storage
    storage_days, storage_total, _ = calculate_storage(
        vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
    )
    return render_template('tina/create_invoice.html',
        vehicle=vehicle,
        storage_days=storage_days,
        storage_total=storage_total,
        today=date.today(),
    )


@bp.route('/invoice/<int:invoice_id>/print')
@_tina_required
def invoice_print(invoice_id):
    inv = db.get_or_404(Invoice, invoice_id)
    return render_template('tina/invoice_print.html',
        inv=inv,
        vehicle=inv.vehicle,
        today=date.today(),
        company_name=current_app.config['COMPANY_NAME'],
        company_address=current_app.config['COMPANY_ADDRESS'],
        company_phone=current_app.config['COMPANY_PHONE'],
    )
