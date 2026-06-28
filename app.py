import csv
import io
import os
from datetime import date, datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from models import (db, Vehicle, CertifiedLetter, TitleFiling, VehicleNote, DamageItem,
                    PPI_LETTER1_DAYS, PPI_LETTER2_DAYS, POLICE_LETTER1_DAYS)


def run_migrations(app):
    """Add new columns to existing databases without losing data."""
    with app.app_context():
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        with db.engine.begin() as conn:
            if 'vehicles' in existing_tables:
                cols = {c['name'] for c in inspector.get_columns('vehicles')}
                new_cols = [
                    ('owner_name',          'VARCHAR(100)'),
                    ('owner_address',        'TEXT'),
                    ('lienholder_name',      'VARCHAR(100)'),
                    ('lienholder_address',   'TEXT'),
                    ('lienholder_city',      'VARCHAR(50)'),
                    ('lienholder_state',     'VARCHAR(2)'),
                    ('lienholder_zip',       'VARCHAR(10)'),
                    ('tow_fee',              'FLOAT'),
                    ('daily_storage_rate',   'FLOAT'),
                    ('nada_value',           'FLOAT'),
                    ('mileage',              'INTEGER'),
                ]
                for col_name, col_type in new_cols:
                    if col_name not in cols:
                        conn.execute(text(f'ALTER TABLE vehicles ADD COLUMN {col_name} {col_type}'))


def parse_quantum_view_csv(content: str):
    """
    Parse a UPS Quantum View CSV export.
    Returns (rows, tracking_col_used, headers).
    Each row: {tracking_number, delivered_date or None, raw_status}
    """
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []

    # Build a normalized → original header map
    norm = {h.lower().strip().replace(' ', '').replace('/', '').replace('_', ''): h
            for h in headers}

    def find_col(*candidates):
        for c in candidates:
            key = c.lower().replace(' ', '').replace('/', '').replace('_', '')
            if key in norm:
                return norm[key]
        return None

    tracking_col = find_col(
        'Tracking Number', 'TrackingNumber', 'Package/Sequence Number',
        'PackageSequenceNumber', 'UPS Tracking', 'Tracking No', 'Tracking#'
    )
    delivery_date_col = find_col(
        'Delivery Date', 'Actual Delivery Date', 'ActualDeliveryDate',
        'DeliveryDate', 'Date Delivered', 'Delivered Date'
    )
    status_col = find_col(
        'Status', 'Status Description', 'StatusDescription',
        'Activity', 'Activity Description', 'Description', 'Package Status'
    )

    results = []
    for row in reader:
        if not tracking_col:
            break
        raw_tracking = row.get(tracking_col, '').strip()
        if not raw_tracking:
            continue

        tracking = raw_tracking.replace(' ', '').replace('-', '').upper()

        raw_status = row.get(status_col, '').strip() if status_col else ''
        is_delivered = 'delivered' in raw_status.lower() or raw_status.upper() == 'D'

        delivered_date = None
        if delivery_date_col:
            raw_date = row.get(delivery_date_col, '').strip()
            if raw_date:
                for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y', '%d/%m/%Y', '%m/%d/%y'):
                    try:
                        delivered_date = datetime.strptime(raw_date, fmt).date()
                        break
                    except ValueError:
                        continue

        if delivered_date and not is_delivered:
            is_delivered = True

        results.append({
            'tracking_number': tracking,
            'raw_tracking': raw_tracking,
            'delivered_date': delivered_date,
            'is_delivered': is_delivered,
            'raw_status': raw_status,
        })

    return results, tracking_col, headers


def create_app():
    app = Flask(__name__)

    basedir = os.path.abspath(os.path.dirname(__file__))
    db_url = os.environ.get(
        'DATABASE_URL',
        f'sqlite:///{os.path.join(basedir, "impound.db")}'
    )
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')

    # Company info for letter templates — set via environment variables on Render
    app.config['COMPANY_NAME'] = os.environ.get('COMPANY_NAME', 'Columbus Towing LLC')
    app.config['COMPANY_ADDRESS'] = os.environ.get('COMPANY_ADDRESS', '123 Storage Dr, Columbus, OH 43215')
    app.config['COMPANY_PHONE'] = os.environ.get('COMPANY_PHONE', '(614) 555-0100')
    app.config['STORAGE_ADDRESS'] = os.environ.get('STORAGE_ADDRESS', '123 Storage Dr, Columbus, OH 43215')

    # Title packet template — BlankTitlePacket.pdf from TitleBot
    default_template = os.environ.get(
        'TITLE_PACKET_TEMPLATE',
        r'C:\TitleBot\Templates\BlankTitlePacket.pdf'
    )
    app.config['TITLE_PACKET_TEMPLATE'] = default_template

    db.init_app(app)

    with app.app_context():
        db.create_all()

    run_migrations(app)

    # ── Jinja helpers ─────────────────────────────────────────────────────────

    @app.template_filter('mdY')
    def mdY(value):
        return value.strftime('%m/%d/%Y') if value else ''

    @app.template_filter('human_date')
    def human_date(value):
        if not value:
            return ''
        return f"{value.strftime('%A, %B')} {value.day}, {value.year}"

    @app.template_filter('short_date')
    def short_date(value):
        if not value:
            return ''
        return f"{value.strftime('%b')} {value.day}"

    # ── Dashboard ─────────────────────────────────────────────────────────────

    @app.route('/')
    def dashboard():
        today = date.today()
        week_ahead = today + timedelta(days=7)

        total_active = Vehicle.query.filter_by(status='ACTIVE').count()

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

        all_active = Vehicle.query.filter_by(status='ACTIVE').all()
        title_eligible = [v for v in all_active if v.is_title_eligible and v.title_filing is None]

        return render_template('dashboard.html',
            today=today,
            total_active=total_active,
            overdue=overdue,
            due_today=due_today,
            due_this_week=due_this_week,
            title_eligible=title_eligible,
        )

    # ── Search ────────────────────────────────────────────────────────────────

    @app.route('/search')
    def search():
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
                        Vehicle.make.ilike(like),
                        Vehicle.model_name.ilike(like),
                        Vehicle.owner_name.ilike(like),
                        Vehicle.police_report_number.ilike(like),
                    )
                )
                .order_by(Vehicle.impound_date.desc())
                .all()
            )
        return render_template('search.html', q=q, results=results)

    # ── Pipeline (30-day view) ────────────────────────────────────────────────

    @app.route('/pipeline')
    def pipeline():
        today = date.today()
        horizon = today + timedelta(days=30)

        # Unsent letters due in the next 30 days
        upcoming_letters = (
            CertifiedLetter.query
            .join(Vehicle)
            .filter(Vehicle.status == 'ACTIVE')
            .filter(CertifiedLetter.sent_date.is_(None))
            .filter(CertifiedLetter.due_date <= horizon)
            .order_by(CertifiedLetter.due_date.asc())
            .all()
        )

        # Vehicles becoming title-eligible within 30 days (not yet eligible, not yet filed)
        active_vehicles = Vehicle.query.filter_by(status='ACTIVE').all()
        upcoming_eligibility = sorted(
            [v for v in active_vehicles
             if v.title_eligible_date
             and today < v.title_eligible_date <= horizon
             and v.title_filing is None],
            key=lambda v: v.title_eligible_date
        )

        # Already eligible but not filed
        eligible_now = [
            v for v in active_vehicles
            if v.is_title_eligible and v.title_filing is None
        ]

        # Group letters by ISO week for display
        from itertools import groupby
        def week_label(d):
            mon = d - timedelta(days=d.weekday())
            sun = mon + timedelta(days=6)
            return f"Week of {mon.strftime('%b')} {mon.day} – {sun.strftime('%b')} {sun.day}"

        grouped_letters = []
        for week, group in groupby(upcoming_letters, key=lambda l: week_label(l.due_date)):
            grouped_letters.append((week, list(group)))

        return render_template('pipeline.html',
            today=today,
            horizon=horizon,
            grouped_letters=grouped_letters,
            upcoming_eligibility=upcoming_eligibility,
            eligible_now=eligible_now,
        )

    # ── Vehicles ──────────────────────────────────────────────────────────────

    @app.route('/vehicles')
    def vehicles_list():
        status_filter = request.args.get('status', 'ACTIVE')
        vehicles = (
            Vehicle.query
            .filter_by(status=status_filter)
            .order_by(Vehicle.impound_date.desc())
            .all()
        )
        return render_template('vehicles/list.html', vehicles=vehicles, status_filter=status_filter)

    def _vehicle_from_form(form, vehicle=None):
        year_str = form.get('year', '').strip()
        mile_str = form.get('mileage', '').strip()
        tow_str  = form.get('tow_fee', '').strip()
        rate_str = form.get('daily_storage_rate', '').strip()
        nada_str = form.get('nada_value', '').strip()
        fields = dict(
            vin=form.get('vin', '').strip() or None,
            plate=form.get('plate', '').strip() or None,
            plate_state=form.get('plate_state', 'OH').strip() or 'OH',
            year=int(year_str) if year_str.isdigit() else None,
            make=form.get('make', '').strip() or None,
            model_name=form.get('model_name', '').strip() or None,
            color=form.get('color', '').strip() or None,
            storage_location=form.get('storage_location', '').strip() or None,
            police_report_number=form.get('police_report_number', '').strip() or None,
            owner_name=form.get('owner_name', '').strip() or None,
            owner_address=form.get('owner_address', '').strip() or None,
            lienholder_name=form.get('lienholder_name', '').strip() or None,
            lienholder_address=form.get('lienholder_address', '').strip() or None,
            lienholder_city=form.get('lienholder_city', '').strip() or None,
            lienholder_state=form.get('lienholder_state', '').strip() or None,
            lienholder_zip=form.get('lienholder_zip', '').strip() or None,
            mileage=int(mile_str.replace(',', '')) if mile_str.replace(',', '').isdigit() else None,
            tow_fee=float(tow_str) if tow_str else None,
            daily_storage_rate=float(rate_str) if rate_str else None,
            nada_value=float(nada_str) if nada_str else None,
            notes=form.get('notes', '').strip() or None,
        )
        if vehicle:
            for k, v in fields.items():
                setattr(vehicle, k, v)
            vehicle.updated_at = datetime.utcnow()
        return fields

    @app.route('/vehicles/new', methods=['GET', 'POST'])
    def vehicles_new():
        if request.method == 'POST':
            impound_date_str = request.form.get('impound_date', '').strip()
            impound_type = request.form.get('impound_type', '').strip()

            if not impound_date_str or not impound_type:
                flash('Impound date and type are required.', 'danger')
                return render_template('vehicles/new.html', today=date.today(), form=request.form)

            impound_date = date.fromisoformat(impound_date_str)
            fields = _vehicle_from_form(request.form)

            vehicle = Vehicle(
                **fields,
                impound_type=impound_type,
                impound_date=impound_date,
                status='ACTIVE',
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.session.add(vehicle)
            db.session.flush()

            letter1_days = PPI_LETTER1_DAYS if impound_type == 'PPI' else POLICE_LETTER1_DAYS
            letter1_due = impound_date + timedelta(days=letter1_days)
            db.session.add(CertifiedLetter(
                vehicle_id=vehicle.id,
                letter_number=1,
                due_date=letter1_due,
                created_at=datetime.utcnow(),
            ))
            db.session.commit()

            label = 'Letter 1' if impound_type == 'PPI' else 'Notification Letter'
            flash(f'{vehicle.display_name} added. {label} due by {letter1_due.strftime("%m/%d/%Y")}.', 'success')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle.id))

        return render_template('vehicles/new.html', today=date.today(), form={})

    @app.route('/vehicles/<int:vehicle_id>')
    def vehicles_detail(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        from titlebot.storage import calculate_storage
        storage_days, storage_total, storage_breakdown = calculate_storage(
            vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
        )
        return render_template(
            'vehicles/detail.html',
            vehicle=vehicle,
            today=date.today(),
            storage_days=storage_days,
            storage_total=storage_total,
            storage_breakdown=storage_breakdown,
        )

    @app.route('/vehicles/<int:vehicle_id>/edit', methods=['GET', 'POST'])
    def vehicles_edit(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if request.method == 'POST':
            _vehicle_from_form(request.form, vehicle=vehicle)
            db.session.commit()
            flash(f'{vehicle.display_name} updated.', 'success')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle.id))
        return render_template('vehicles/edit.html', vehicle=vehicle)

    @app.route('/vehicles/<int:vehicle_id>/release', methods=['POST'])
    def vehicles_release(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        vehicle.status = 'RELEASED'
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'{vehicle.display_name} marked as released.', 'info')
        return redirect(url_for('dashboard'))

    # ── Notes ─────────────────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/notes', methods=['POST'])
    def vehicles_add_note(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        body = request.form.get('body', '').strip()
        if body:
            db.session.add(VehicleNote(
                vehicle_id=vehicle.id,
                body=body,
                author=request.form.get('author', 'Heather').strip() or 'Heather',
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    @app.route('/notes/<int:note_id>/delete', methods=['POST'])
    def notes_delete(note_id):
        note = db.get_or_404(VehicleNote, note_id)
        vehicle_id = note.vehicle_id
        db.session.delete(note)
        db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    # ── Letters ───────────────────────────────────────────────────────────────

    @app.route('/letters/<int:letter_id>/mark-sent', methods=['GET', 'POST'])
    def letters_mark_sent(letter_id):
        letter = db.get_or_404(CertifiedLetter, letter_id)

        if request.method == 'POST':
            sent_str = request.form.get('sent_date', '').strip()
            sent_date = date.fromisoformat(sent_str) if sent_str else date.today()

            letter.sent_date = sent_date
            letter.tracking_number = request.form.get('tracking_number', '').strip() or None
            letter.notes = request.form.get('notes', '').strip() or letter.notes

            vehicle = letter.vehicle

            if vehicle.impound_type == 'PPI' and letter.letter_number == 1:
                letter2_due = sent_date + timedelta(days=PPI_LETTER2_DAYS)
                db.session.add(CertifiedLetter(
                    vehicle_id=vehicle.id,
                    letter_number=2,
                    due_date=letter2_due,
                    created_at=datetime.utcnow(),
                ))
                flash(f'Letter 1 sent. Letter 2 due by {letter2_due.strftime("%m/%d/%Y")}.', 'success')
            else:
                flash(f'{letter.label} marked as sent for {vehicle.display_name}.', 'success')

            vehicle.updated_at = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('dashboard'))

        return render_template('letters/mark_sent.html', letter=letter, today=date.today())

    @app.route('/letters/<int:letter_id>/confirm-delivery', methods=['POST'])
    def letters_confirm_delivery(letter_id):
        letter = db.get_or_404(CertifiedLetter, letter_id)
        confirmed_str = request.form.get('delivery_date', '').strip()
        letter.delivery_confirmed_date = (
            date.fromisoformat(confirmed_str) if confirmed_str else date.today()
        )
        db.session.commit()
        flash('Delivery confirmation recorded.', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=letter.vehicle_id))

    # ── Title Filing ──────────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/file-title', methods=['GET', 'POST'])
    def file_title(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)

        if not vehicle.is_title_eligible:
            flash('This vehicle is not yet eligible for title filing.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

        if vehicle.title_filing:
            flash('A title filing already exists for this vehicle.', 'warning')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

        if request.method == 'POST':
            db.session.add(TitleFiling(
                vehicle_id=vehicle.id,
                filed_date=date.today(),
                bmv_receipt_number=request.form.get('bmv_receipt_number', '').strip() or None,
                status='FILED',
                notes=request.form.get('notes', '').strip() or None,
                created_at=datetime.utcnow(),
            ))
            vehicle.status = 'TITLE_FILED'
            vehicle.updated_at = datetime.utcnow()
            db.session.commit()
            flash(f'Title filing recorded for {vehicle.display_name}.', 'success')
            return redirect(url_for('dashboard'))

        return render_template('vehicles/file_title.html', vehicle=vehicle, today=date.today())

    # ── Print: Post Office Checklist ──────────────────────────────────────────

    @app.route('/print/post-office')
    def print_post_office():
        today = date.today()
        week_ahead = today + timedelta(days=7)

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

        return render_template('print/post_office.html',
            today=today, overdue=overdue, due_today=due_today, due_this_week=due_this_week)

    # ── Print: Certified Letter ───────────────────────────────────────────────

    @app.route('/letters/<int:letter_id>/print')
    def print_letter(letter_id):
        letter = db.get_or_404(CertifiedLetter, letter_id)
        return render_template('print/letter.html',
            letter=letter,
            vehicle=letter.vehicle,
            today=date.today(),
            company_name=app.config['COMPANY_NAME'],
            company_address=app.config['COMPANY_ADDRESS'],
            company_phone=app.config['COMPANY_PHONE'],
            storage_address=app.config['STORAGE_ADDRESS'],
        )

    # ── Towbook PDF import ────────────────────────────────────────────────

    @app.route('/towbook-import', methods=['POST'])
    def towbook_import():
        uploaded = request.files.get('pdf_file')
        if not uploaded or not uploaded.filename:
            return jsonify({'error': 'No file uploaded'}), 400
        try:
            from titlebot.parser import extract_text_from_pdf, extract_towbook_data
            pdf_bytes = uploaded.stream.read()
            text = extract_text_from_pdf(pdf_bytes)
            data = extract_towbook_data(text)
            return jsonify({'ok': True, 'data': data})
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    # ── Damage items ──────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/damages', methods=['POST'])
    def damages_add(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        desc = request.form.get('description', '').strip()
        amt_str = request.form.get('amount', '').strip()
        if not desc or not amt_str:
            flash('Description and amount are required.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        try:
            amount = float(amt_str)
        except ValueError:
            flash('Invalid amount.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        next_order = (max((d.sort_order for d in vehicle.damage_items), default=-1) + 1)
        db.session.add(DamageItem(
            vehicle_id=vehicle.id,
            description=desc.upper(),
            amount=amount,
            is_fallback=False,
            sort_order=next_order,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'Damage item added: {desc.upper()} ${amount:.2f}', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')

    @app.route('/damages/<int:damage_id>/delete', methods=['POST'])
    def damages_delete(damage_id):
        item = db.get_or_404(DamageItem, damage_id)
        vehicle_id = item.vehicle_id
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')

    @app.route('/vehicles/<int:vehicle_id>/damages/auto-fill', methods=['POST'])
    def damages_auto_fill(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        from titlebot.damages import auto_fill_fallbacks
        from titlebot.storage import calculate_storage
        nada = vehicle.nada_value or 3499.0
        tow  = vehicle.tow_fee or 0.0
        _, total_storage, _ = calculate_storage(vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0)
        to_add = auto_fill_fallbacks(vehicle.damage_items, nada, tow, total_storage)
        next_order = max((d.sort_order for d in vehicle.damage_items), default=-1) + 1
        for desc, amount, is_fallback in to_add:
            db.session.add(DamageItem(
                vehicle_id=vehicle.id,
                description=desc,
                amount=amount,
                is_fallback=is_fallback,
                sort_order=next_order,
                created_at=datetime.utcnow(),
            ))
            next_order += 1
        db.session.commit()
        if to_add:
            flash(f'{len(to_add)} fallback damage item(s) added — review for accuracy.', 'warning')
        else:
            flash('Damage items are already sufficient to cover the NADA gap.', 'info')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')

    # ── NADA / Edmunds value lookup ───────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/nada-lookup', methods=['POST'])
    def nada_lookup(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not vehicle.vin:
            flash('VIN is required for NADA lookup.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        from titlebot.nada import lookup_wholesale_value
        mileage = vehicle.mileage or 80000
        result = lookup_wholesale_value(
            vin=vehicle.vin,
            mileage=mileage,
            api_key=os.environ.get('ANTHROPIC_API_KEY'),
        )
        vehicle.nada_value = result['value']
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        if result['used_default']:
            flash(
                f'NADA lookup returned default ${result["value"]:,.0f} — {result["notes"]} '
                'Enter the correct value manually.',
                'warning'
            )
        else:
            flash(
                f'NADA value set to ${result["value"]:,.0f} ({result["condition"]}, '
                f'{result["confidence"]} confidence via {result["source"]}).',
                'success'
            )
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')

    # ── Generate Title Packet PDF ─────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/title-packet.pdf')
    def title_packet_pdf(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        template_path = app.config['TITLE_PACKET_TEMPLATE']
        if not os.path.isfile(template_path):
            flash(
                f'Title packet template not found at: {template_path}. '
                'Set TITLE_PACKET_TEMPLATE environment variable to the correct path.',
                'danger'
            )
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        try:
            from titlebot.pdf_gen import generate_title_packet
            pdf_bytes = generate_title_packet(vehicle, template_path)
            safe_name = (vehicle.vin or f'vehicle{vehicle.id}')[-10:]
            filename = f'{safe_name}_TitlePacket_{date.today().strftime("%Y%m%d")}.pdf'
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename,
            )
        except Exception as exc:
            flash(f'PDF generation failed: {exc}', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    # ── Import: UPS Quantum View ──────────────────────────────────────────────

    @app.route('/import/quantum-view', methods=['GET', 'POST'])
    def import_quantum_view():
        if request.method == 'GET':
            return render_template('import/quantum_view.html')

        uploaded = request.files.get('csv_file')
        if not uploaded or not uploaded.filename:
            flash('Please select a CSV file.', 'danger')
            return redirect(url_for('import_quantum_view'))

        try:
            content = uploaded.stream.read().decode('utf-8-sig')  # utf-8-sig strips BOM
        except UnicodeDecodeError:
            content = uploaded.stream.read().decode('latin-1')

        rows, tracking_col, headers = parse_quantum_view_csv(content)

        if not rows:
            flash(
                f'No tracking rows found. Headers detected: {", ".join(headers) or "none"}. '
                'Make sure "Tracking Number" (or similar) is a column in your export.',
                'warning'
            )
            return redirect(url_for('import_quantum_view'))

        # Build a map of normalized tracking number → letter
        letters_with_tracking = (
            CertifiedLetter.query
            .filter(CertifiedLetter.tracking_number.isnot(None))
            .all()
        )
        tracking_map = {l.tracking_normalized: l for l in letters_with_tracking if l.tracking_normalized}

        updated, not_found, already_confirmed, not_delivered = [], [], [], []

        for row in rows:
            letter = tracking_map.get(row['tracking_number'])
            if not letter:
                not_found.append(row)
                continue
            if letter.delivery_confirmed_date:
                already_confirmed.append((row, letter))
                continue
            if not row['is_delivered']:
                not_delivered.append((row, letter))
                continue

            letter.delivery_confirmed_date = row['delivered_date'] or date.today()
            updated.append((row, letter))

        if updated:
            db.session.commit()

        return render_template('import/quantum_view_result.html',
            today=date.today(),
            filename=uploaded.filename,
            tracking_col=tracking_col,
            total_rows=len(rows),
            updated=updated,
            not_found=not_found,
            already_confirmed=already_confirmed,
            not_delivered=not_delivered,
        )

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
