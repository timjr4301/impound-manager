import csv
import io
import os
from datetime import date, datetime, timedelta
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, send_file, jsonify, g)
from flask_login import LoginManager, login_required, current_user
from models import (db, User, Vehicle, CertifiedLetter, TitleFiling,
                    VehicleNote, DamageItem, SyncLog, VehicleDocument, StaffFeedback,
                    PoliceDepartment, VehicleCharge,
                    PPI_LETTER1_DAYS, PPI_LETTER2_DAYS, POLICE_LETTER1_DAYS)
from werkzeug.utils import secure_filename

try:
    from flask_cors import CORS as _CORS
except ImportError:
    _CORS = None

try:
    from flask_socketio import SocketIO
    socketio = SocketIO()
except ImportError:
    SocketIO = None
    socketio = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    _APScheduler = BackgroundScheduler
except ImportError:
    _APScheduler = None


def run_migrations(app):
    with app.app_context():
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        with db.engine.begin() as conn:
            # police_departments must exist before the vehicles.police_department_id
            # FK column below can be added. db.create_all() (called right before
            # run_migrations, see bottom of this file) already creates the table
            # itself from the model, so this only needs to seed it — checked by
            # row count rather than table existence, since create_all() means the
            # (empty) table is always already present by the time we get here.
            if 'police_departments' not in existing_tables:
                PoliceDepartment.__table__.create(db.engine)
                existing_tables.append('police_departments')

            dept_count = conn.execute(text('SELECT COUNT(*) FROM police_departments')).scalar()
            if dept_count == 0:
                seed_rows = [
                    ('APD Airport Police',      155.00, 20.00, 25.00),
                    ('Blendon Twp Police',      211.00, 29.00, 25.00),
                    ('GPD Gahanna Police',      211.42, 29.00, 25.00),
                    ('Grandview Heights PD',    200.00, 25.00, 25.00),
                    ('Licking County Sheriff',  211.00, 29.00, 25.00),
                    ('Madison Township PD',     130.00, 18.00, 25.00),
                    ('MPD Minerva Park Police', 215.00, 35.00, 25.00),
                    ('New Albany Police',       200.00, 25.00, 25.00),
                    ('Ohio State Patrol',       211.00, 29.00, 25.00),
                    ('Pataskala Police Dept',   211.42, 29.00, 25.00),
                    ('RPD Reynoldsburg Police', 211.00, 29.00, 25.00),
                    ('WPD Whitehall Police',    155.00, 20.00, 25.00),
                    ('County Towing Storage',   170.50, 29.00, 40.92),
                ]
                for name, tow, storage, admin in seed_rows:
                    conn.execute(
                        text('INSERT INTO police_departments (name, tow_rate, storage_rate, admin_fee, active) '
                             'VALUES (:name, :tow, :storage, :admin, TRUE)'),
                        {'name': name, 'tow': tow, 'storage': storage, 'admin': admin},
                    )

            if 'vehicles' in existing_tables:
                cols = {c['name'] for c in inspector.get_columns('vehicles')}
                new_cols = [
                    ('owner_name',             'VARCHAR(100)'),
                    ('owner_address',           'TEXT'),
                    ('lienholder_name',         'VARCHAR(100)'),
                    ('lienholder_address',      'TEXT'),
                    ('lienholder_city',         'VARCHAR(50)'),
                    ('lienholder_state',        'VARCHAR(2)'),
                    ('lienholder_zip',          'VARCHAR(10)'),
                    ('tow_fee',                 'FLOAT'),
                    ('daily_storage_rate',      'FLOAT'),
                    ('nada_value',              'FLOAT'),
                    ('nada_value_is_default',   'BOOLEAN'),
                    ('nada_value_override',     'FLOAT'),
                    ('mileage',                 'INTEGER'),
                    ('bmv_stage',               'VARCHAR(20)'),
                    ('bmv_searched_date',       'DATE'),
                    ('bmv_search_notes',        'TEXT'),
                    ('heather_complete',        'BOOLEAN'),
                    ('heather_complete_date',   'DATE'),
                    ('disposition',             'VARCHAR(10)'),
                    ('disposition_set_date',    'DATE'),
                    ('disposition_notes',       'TEXT'),
                    ('tina_stage',              'VARCHAR(20)'),
                    ('court_date',              'DATE'),
                    ('court_notes',             'TEXT'),
                    ('affidavit_filed_date',    'DATE'),
                    ('affidavit_notes',         'TEXT'),
                    ('sale_price',              'FLOAT'),
                    ('sale_date',               'DATE'),
                    ('buyer_name',              'VARCHAR(100)'),
                    ('junk_weight_lbs',         'FLOAT'),
                    ('junk_price_per_ton',      'FLOAT'),
                    ('junk_yard_name',          'VARCHAR(100)'),
                    ('storage_paid',            'FLOAT'),
                    ('payment_date',            'DATE'),
                    ('payment_reference',       'VARCHAR(100)'),
                    ('letter_urgency',          'VARCHAR(10)'),
                    ('task_no_record',          'BOOLEAN'),
                    ('task_no_record_notes',    'TEXT'),
                    ('task_no_record_resolved', 'BOOLEAN'),
                    ('task_no_record_resolved_by',   'VARCHAR(50)'),
                    ('task_no_record_resolved_date', 'DATE'),
                    ('task4_triggered',         'BOOLEAN'),
                    ('task4_triggered_date',    'DATE'),
                    ('current_task_num',        'INTEGER'),
                    ('current_task_label',      'VARCHAR(100)'),
                    ('current_task_due',        'DATE'),
                    ('is_anomaly',              'BOOLEAN'),
                    ('anomaly_reason',          'TEXT'),
                    ('anomaly_flagged_by',      'VARCHAR(50)'),
                    ('anomaly_flagged_at',      'TIMESTAMP'),
                    ('restart_date',            'DATE'),
                    ('restart_reason',          'TEXT'),
                    ('restart_set_by',          'VARCHAR(50)'),
                    ('restart_set_at',          'TIMESTAMP'),
                    ('is_afo',                  'BOOLEAN'),
                    ('afo_detected_at',         'TIMESTAMP'),
                    ('afo_detected_by',         'VARCHAR(50)'),
                    ('vin_door_jamb_photo',     'TEXT'),
                    ('vin_dash_photo',          'TEXT'),
                    ('vin_door_jamb_read',      'VARCHAR(20)'),
                    ('vin_dash_read',           'VARCHAR(20)'),
                    ('vin_verified',            'BOOLEAN'),
                    ('vin_mismatch',            'BOOLEAN'),
                    ('vin_verification_notes',  'TEXT'),
                    ('vin_verified_at',         'TIMESTAMP'),
                    ('vin_mismatch_resolved',      'BOOLEAN'),
                    ('vin_mismatch_resolved_by',   'VARCHAR(50)'),
                    ('vin_mismatch_resolved_date', 'DATE'),
                    ('pending_pickup_since',       'TIMESTAMP'),
                    ('snoozed_until',           'DATE'),
                    ('snoozed_at',              'TIMESTAMP'),
                    ('snoozed_by',              'VARCHAR(50)'),
                    ('last_location_zone',      'VARCHAR(50)'),
                    ('last_location_lat',       'FLOAT'),
                    ('last_location_lng',       'FLOAT'),
                    ('last_location_at',        'TIMESTAMP'),
                    ('last_location_by',        'VARCHAR(100)'),
                    ('police_department_id',    'INTEGER REFERENCES police_departments(id)'),
                ]
                for col_name, col_type in new_cols:
                    if col_name not in cols:
                        conn.execute(text(f'ALTER TABLE vehicles ADD COLUMN {col_name} {col_type}'))

            if 'certified_letters' in existing_tables:
                cols = {c['name'] for c in inspector.get_columns('certified_letters')}
                if 'return_to_sender' not in cols:
                    conn.execute(text('ALTER TABLE certified_letters ADD COLUMN return_to_sender BOOLEAN'))
                if 'reference_number_2' not in cols:
                    conn.execute(text('ALTER TABLE certified_letters ADD COLUMN reference_number_2 VARCHAR(50)'))
                if 'recipient_type' not in cols:
                    conn.execute(text("ALTER TABLE certified_letters ADD COLUMN recipient_type VARCHAR(20) DEFAULT 'owner'"))
                if 'letter_kind' not in cols:
                    conn.execute(text('ALTER TABLE certified_letters ADD COLUMN letter_kind VARCHAR(20)'))
                # Backfill letter_kind on pre-existing letter_number 1/2 rows
                # (created before the 5-letter system existed) so their print
                # content routes correctly. Safe to re-run — only touches
                # rows where letter_kind is still NULL.
                conn.execute(text("""
                    UPDATE certified_letters SET letter_kind = 'notice_of_lien'
                    WHERE letter_kind IS NULL AND letter_number = 1
                      AND vehicle_id IN (SELECT id FROM vehicles WHERE impound_type = 'POLICE')
                """))
                conn.execute(text("""
                    UPDATE certified_letters SET letter_kind = 'first_notice'
                    WHERE letter_kind IS NULL AND letter_number = 1
                      AND vehicle_id IN (SELECT id FROM vehicles WHERE impound_type = 'PPI')
                """))
                conn.execute(text("""
                    UPDATE certified_letters SET letter_kind = 'second_notice'
                    WHERE letter_kind IS NULL AND letter_number = 2
                """))

            if 'envelope_scans' in existing_tables:
                cols = {c['name'] for c in inspector.get_columns('envelope_scans')}
                if 'outcome' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN outcome VARCHAR(20)'))
                if 'matched_by' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN matched_by VARCHAR(20)'))
                if 'reference_number_2' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN reference_number_2 VARCHAR(50)'))
                if 'image_data' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN image_data TEXT'))
                if 'cleared_at' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN cleared_at TIMESTAMP'))
                if 'cleared_by' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN cleared_by VARCHAR(50)'))
                if 'clear_reason' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN clear_reason VARCHAR(100)'))
                if 'discarded' not in cols:
                    conn.execute(text('ALTER TABLE envelope_scans ADD COLUMN discarded BOOLEAN'))
                # vehicle_id must become nullable so a genuinely-unmatched scan
                # can be saved and surfaced in the /envelopes Unmatched tab —
                # safe to run unconditionally on Postgres, a no-op once already
                # nullable. Postgres-only syntax (SQLite has no ALTER COLUMN),
                # guarded here since local/test runs may use SQLite even though
                # production never does.
                if db.engine.dialect.name == 'postgresql':
                    conn.execute(text('ALTER TABLE envelope_scans ALTER COLUMN vehicle_id DROP NOT NULL'))

            if 'sync_log' not in existing_tables:
                # Use SQLAlchemy ORM to create the table safely on any DB backend
                SyncLog.__table__.create(db.engine)

            if 'vehicle_documents' not in existing_tables:
                VehicleDocument.__table__.create(db.engine)

            if 'staff_feedback' not in existing_tables:
                StaffFeedback.__table__.create(db.engine)

            if 'vehicle_charges' not in existing_tables:
                VehicleCharge.__table__.create(db.engine)


def parse_quantum_view_csv(content: str):
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
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


# Canonical staff/demo accounts — single source of truth also used by
# reset_users.py for the manual post-deploy password reset. Lori runs on the
# 'lawrence' role (not a separate 'lori' role) and Wally runs on 'tim', per Tim.
STAFF_USER_DEFAULTS = [
    # (username, password, role, display_name)
    ('tim',      'BandJ2024!', 'tim',      'Tim'),
    ('heather',  'BandJ2024!', 'heather',  'Heather'),
    ('tina',     'BandJ2024!', 'tina',     'Tina'),
    ('lawrence', 'BandJ2024!', 'lawrence', 'Lawrence'),
    ('lori',     'BandJ2024!', 'lawrence', 'Lori'),
    ('brady',    'BandJ2024!', 'brady',    'Brady'),
    ('jim',      'BandJ2024!', 'jim',      'Jim'),
    ('wally',    'BandJ2024!', 'tim',      'Wally'),
    ('test',     'BandJDemo!', 'demo',     'Demo'),
]


def seed_default_users(app):
    """Create default user accounts if they don't exist. Runs on every boot —
    only ever CREATES missing accounts, never resets an existing account's
    password (that would silently undo a password someone set via /admin/users).
    For an actual password reset, run reset_users.py in the Render Shell."""
    with app.app_context():
        staff_defaults = STAFF_USER_DEFAULTS + [
            ('dispatcher', 'bjt-dispatch-2024!', 'dispatcher', 'Dispatch'),
        ]
        for username, password, role, display in staff_defaults:
            if not User.query.filter_by(username=username).first():
                u = User(username=username, role=role, display_name=display)
                u.set_password(password)
                db.session.add(u)

        # 30 driver accounts
        for i in range(1, 31):
            uname = f'driver{i:02d}'
            if not User.query.filter_by(username=uname).first():
                u = User(username=uname, role='driver',
                         display_name=f'Driver {i:02d}')
                u.set_password(f'bjt-driver{i:02d}-2024!')
                db.session.add(u)

        db.session.commit()


def _backfill_urgency(app):
    """Run task pipeline calculation for any vehicles missing urgency data."""
    with app.app_context():
        try:
            null_count = Vehicle.query.filter(
                Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']),
                Vehicle.letter_urgency.is_(None)
            ).count()
            if null_count > 0:
                from task_engine import recalculate_all
                counts = recalculate_all()
                print(f'[task_engine] backfill: {counts}')
        except Exception as exc:
            print(f'[task_engine] backfill error: {exc}')


def _start_scheduler(app):
    """Start APScheduler background thread for scheduled daily jobs."""
    if _APScheduler is None:
        return
    try:
        scheduler = _APScheduler(timezone='America/New_York')

        # 5:00 AM — Towbook auto-sync (API if configured, else alert_pending)
        def _towbook_sync():
            with app.app_context():
                _run_towbook_sync_job()

        # 6:00 AM — Task pipeline recalculation (runs after sync so urgency is fresh)
        def _recalc():
            with app.app_context():
                from task_engine import recalculate_all
                recalculate_all()

        scheduler.add_job(_towbook_sync, 'cron', hour=5, minute=0, id='daily_towbook_sync')
        scheduler.add_job(_recalc,       'cron', hour=6, minute=0, id='daily_urgency')
        scheduler.start()
    except Exception as exc:
        print(f'[scheduler] could not start: {exc}')


def _run_towbook_sync_job():
    """
    Core logic for the 5 AM Towbook sync.  Also called from the manual trigger route.
    Returns a SyncLog instance (not yet committed — caller commits).
    """
    today = date.today()

    # Already succeeded today? Skip.
    ok_today = SyncLog.query.filter_by(sync_date=today, status='ok').first()
    if ok_today:
        print(f'[towbook_sync] already synced today ({ok_today.source}), skipping')
        return ok_today

    from towbook_api import is_configured, run_auto_sync

    if is_configured():
        try:
            result = run_auto_sync()
            log = SyncLog(
                sync_date=today,
                source='api_auto',
                status='ok',
                inserted=result.get('inserted', 0),
                updated=result.get('updated', 0),
                skipped=result.get('skipped', 0),
                call_count=result.get('call_count', 0),
                triggered_by='scheduler',
                created_at=datetime.utcnow(),
            )
            print(f'[towbook_sync] API sync OK: {result.get("inserted")} in, {result.get("updated")} up')
        except Exception as exc:
            log = SyncLog(
                sync_date=today,
                source='api_auto',
                status='error',
                error_msg=str(exc)[:500],
                triggered_by='scheduler',
                created_at=datetime.utcnow(),
            )
            print(f'[towbook_sync] API sync FAILED: {exc}')
    else:
        # No API credentials — write a pending alert (once per day)
        pending_today = SyncLog.query.filter_by(sync_date=today).first()
        if pending_today:
            return pending_today
        log = SyncLog(
            sync_date=today,
            source='alert_pending',
            status='pending',
            triggered_by='scheduler',
            created_at=datetime.utcnow(),
        )
        print('[towbook_sync] no API configured — alert_pending log created')

    db.session.add(log)
    db.session.commit()
    return log


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

    from datetime import timedelta
    app.config['SESSION_COOKIE_SECURE'] = False
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
    app.config['SESSION_COOKIE_NAME'] = 'bj_session'

    app.config['COMPANY_NAME'] = os.environ.get('COMPANY_NAME', 'Broad & James Towing')
    app.config['COMPANY_ADDRESS'] = os.environ.get('COMPANY_ADDRESS', '3201 E Broad St, Columbus, OH 43213')
    app.config['COMPANY_PHONE'] = os.environ.get('COMPANY_PHONE', '(614) 235-4700')
    app.config['STORAGE_ADDRESS'] = os.environ.get('STORAGE_ADDRESS', '3201 E Broad St, Columbus, OH 43213')

    default_template = os.environ.get(
        'TITLE_PACKET_TEMPLATE',
        os.path.join(basedir, 'titlebot', 'BlankTitlePacket.pdf')
    )
    app.config['TITLE_PACKET_TEMPLATE'] = default_template

    # CORS for Base44 apps (optional — only if flask-cors is installed)
    if _CORS:
        _CORS(app, resources={r'/api/*': {'origins': '*'}})

    db.init_app(app)

    with app.app_context():
        db.create_all()

    run_migrations(app)
    seed_default_users(app)
    _backfill_urgency(app)
    _start_scheduler(app)

    # Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to continue.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ── Demo mode: block all writes for the read-only demo account ─────────────
    @app.before_request
    def _block_demo_writes():
        if (current_user.is_authenticated
                and current_user.role == 'demo'
                and request.method not in ('GET', 'HEAD', 'OPTIONS')):
            if request.is_json or request.accept_mimetypes.best == 'application/json':
                return jsonify({'ok': False, 'error': 'Demo mode is read-only.'}), 403
            flash('Demo mode is read-only — changes are disabled.', 'warning')
            return redirect(request.referrer or url_for('dashboard'))

    # ── SocketIO (optional — only if flask-socketio is installed) ──────────────
    if socketio is not None:
        socketio.init_app(app, async_mode='threading', cors_allowed_origins='*',
                          logger=False, engineio_logger=False)

    # ── Blueprints ─────────────────────────────────────────────────────────────
    from blueprints.auth import bp as auth_bp
    from blueprints.heather import bp as heather_bp
    from blueprints.tina import bp as tina_bp
    from blueprints.api import bp as api_bp
    from blueprints.drivers import bp as drivers_bp
    from blueprints.payments import bp as payments_bp
    from blueprints.admin import bp as admin_bp
    from blueprints.damage_docs import bp as damage_bp
    from blueprints.help import bp as help_bp
    from blueprints.bmv_document_scanner import bp as bmv_scanner_bp
    from blueprints.driver_snap import bp as driver_snap_bp
    from blueprints.audit import bp as audit_bp
    from blueprints.envelopes import bp as envelopes_bp
    from towbook_import import bp as towbook_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(heather_bp)
    app.register_blueprint(tina_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(drivers_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(damage_bp)
    app.register_blueprint(help_bp)
    app.register_blueprint(towbook_bp)
    app.register_blueprint(bmv_scanner_bp)
    app.register_blueprint(driver_snap_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(envelopes_bp)

    # Chat + Invoice Camera registered only when their files exist
    try:
        from blueprints.chat import bp as chat_bp, register_socket_events
        app.register_blueprint(chat_bp)
        if socketio is not None:
            register_socket_events(socketio)
        else:
            app.logger.warning(
                'flask-socketio not installed — chat HTTP routes are live but '
                'realtime send/receive and Wally are disabled.'
            )
    except ImportError:
        pass
    try:
        from blueprints.invoice_camera import bp as invoice_bp
        app.register_blueprint(invoice_bp)
    except ImportError:
        pass

    # ── Jinja helpers ──────────────────────────────────────────────────────────

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

    @app.template_filter('nice_date')
    def nice_date(value):
        if not value:
            return ''
        return f"{value.strftime('%A, %B')} {value.day}, {value.year}"

    @app.template_filter('long_date')
    def long_date(value):
        """Cross-platform 'Month D, YYYY' format (no leading zero on day)."""
        if not value:
            return ''
        return f"{value.strftime('%B')} {value.day}, {value.year}"

    @app.template_filter('currency')
    def currency(value):
        if value is None:
            return '—'
        return f'${value:,.2f}'

    @app.context_processor
    def inject_globals():
        from datetime import timedelta as _td
        from flask_login import current_user as _cu

        sync_status = None
        if _cu.is_authenticated and _cu.role in ('tim', 'heather'):
            try:
                _today = date.today()
                _log = (SyncLog.query
                        .filter_by(sync_date=_today)
                        .order_by(SyncLog.created_at.desc())
                        .first())
                if _log:
                    sync_status = _log
                elif datetime.utcnow().hour >= 10:
                    # After 10 AM UTC (~5-6 AM ET) with no log = missed
                    _placeholder = SyncLog(sync_date=_today, status='no_sync',
                                           source='none', created_at=datetime.utcnow())
                    sync_status = _placeholder
            except Exception:
                pass

        return {
            'company_name': app.config['COMPANY_NAME'],
            'company_phone': app.config['COMPANY_PHONE'],
            'timedelta': _td,
            'towbook_sync_status': sync_status,
        }

    # ── Dashboard ──────────────────────────────────────────────────────────────

    @app.route('/')
    @login_required
    def dashboard():
        # Role-based redirect
        if current_user.role == 'heather':
            return redirect(url_for('heather.dashboard'))
        if current_user.role == 'tina':
            return redirect(url_for('tina.dashboard'))
        if current_user.role == 'dispatcher':
            return redirect(url_for('dispatch_board'))

        # Tim / fallback — main dashboard
        today = date.today()
        week_ahead = today + timedelta(days=7)

        total_active = Vehicle.query.filter_by(status='ACTIVE').count()

        pending_letters = (
            CertifiedLetter.query
            .join(Vehicle)
            .filter(Vehicle.status == 'ACTIVE')
            .filter(CertifiedLetter.sent_date.is_(None))
            .filter(Vehicle.possible_release.isnot(True))
            .all()
        )

        overdue = sorted([l for l in pending_letters if l.due_date < today], key=lambda l: l.due_date)
        due_today = [l for l in pending_letters if l.due_date == today]
        due_this_week = sorted(
            [l for l in pending_letters if today < l.due_date <= week_ahead],
            key=lambda l: l.due_date
        )

        # Possible Release / Ghost Vehicle — flagged missing from the latest
        # Towbook export or a manual lot-walk flag. Highest legal priority:
        # sending an abandonment notice for a vehicle that may already be gone
        # is real exposure, so these must be verified before any letter goes out.
        ghost_vehicles = (
            Vehicle.query
            .filter(Vehicle.status == 'ACTIVE')
            .filter(Vehicle.possible_release == True)
            .order_by(Vehicle.updated_at.desc())
            .all()
        )

        # Stale Location — extends (does not replace) the ghost-vehicle logic
        # above. A vehicle with no VIN-snap in 7+ days (or never snapped) may
        # not actually be on the lot; already-flagged Possible Release
        # vehicles are excluded here since they're already surfaced above.
        stale_location_vehicles = (
            Vehicle.query
            .filter(Vehicle.status == 'ACTIVE')
            .filter(Vehicle.possible_release.isnot(True))
            .filter(db.or_(
                Vehicle.last_location_at.is_(None),
                Vehicle.last_location_at < datetime.utcnow() - timedelta(days=7),
            ))
            .order_by(Vehicle.last_location_at.asc().nullsfirst())
            .all()
        )

        all_active = Vehicle.query.filter_by(status='ACTIVE').all()
        title_eligible = [v for v in all_active if v.is_title_eligible and v.title_filing is None]

        towbook_total = Vehicle.query.filter(Vehicle.stock_number.isnot(None)).count()
        last_sync = (
            db.session.query(db.func.max(Vehicle.last_synced))
            .filter(Vehicle.last_synced.isnot(None))
            .scalar()
        )

        # No Record Found URGENT vehicles (Task 5)
        urgent_no_record = (
            Vehicle.query
            .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
            .filter(Vehicle.task_no_record == True)
            .filter(db.or_(Vehicle.task_no_record_resolved == False,
                           Vehicle.task_no_record_resolved.is_(None)))
            .order_by(Vehicle.impound_date.asc())
            .all()
        )

        # Paid/Released Pending Pickup — authorized for release but not yet
        # physically collected. Oldest (most overdue) first.
        pending_pickup_vehicles = (
            Vehicle.query
            .filter_by(status='PENDING_PICKUP')
            .order_by(Vehicle.pending_pickup_since.asc())
            .all()
        )
        pending_pickup_overdue = [v for v in pending_pickup_vehicles if v.pending_pickup_overdue]

        # Heather→Tina handoff queue
        handoff_queue = Vehicle.query.filter_by(heather_complete=True, tina_stage='QUEUED').all()

        # Open timecard exceptions
        from models import TimecardException
        timecard_flags = TimecardException.query.filter_by(resolved=False).count()

        from towbook_api import is_configured as towbook_api_configured

        # Staff feedback — visible to Tim/Jim only
        staff_feedback = []
        if current_user.can_see_all:
            staff_feedback = (
                StaffFeedback.query
                .order_by(StaffFeedback.is_read.asc(), StaffFeedback.created_at.desc())
                .limit(50)
                .all()
            )

        return render_template('dashboard.html',
            today=today,
            total_active=total_active,
            overdue=overdue,
            due_today=due_today,
            due_this_week=due_this_week,
            title_eligible=title_eligible,
            towbook_total=towbook_total,
            last_sync=last_sync,
            urgent_no_record=urgent_no_record,
            ghost_vehicles=ghost_vehicles,
            stale_location_vehicles=stale_location_vehicles,
            handoff_queue=handoff_queue,
            timecard_flags=timecard_flags,
            towbook_api_configured=towbook_api_configured(),
            staff_feedback=staff_feedback,
            pending_pickup_vehicles=pending_pickup_vehicles,
            pending_pickup_overdue=pending_pickup_overdue,
        )

    @app.route('/api/import-towbook/trigger', methods=['POST'])
    @login_required
    def towbook_trigger_sync():
        """Tim-only: manually trigger a Towbook API sync (bypasses the 5 AM schedule)."""
        if current_user.role != 'tim':
            return jsonify({'error': 'Access restricted to Tim.'}), 403

        from towbook_api import is_configured, run_auto_sync
        if not is_configured():
            return jsonify({
                'error': 'Towbook API not configured.',
                'help': (
                    'Set TOWBOOK_API_TOKEN and TOWBOOK_COMPANY_ID in Render → '
                    'Environment Variables, then redeploy. '
                    'Contact Towbook support (support@towbook.com) to obtain your API token.'
                ),
            }), 400

        try:
            result = run_auto_sync()
            log = SyncLog(
                sync_date=date.today(),
                source='api_auto',
                status='ok',
                inserted=result.get('inserted', 0),
                updated=result.get('updated', 0),
                skipped=result.get('skipped', 0),
                call_count=result.get('call_count', 0),
                triggered_by=current_user.username,
                created_at=datetime.utcnow(),
            )
            db.session.add(log)
            db.session.commit()
            return jsonify(result)
        except Exception as exc:
            log = SyncLog(
                sync_date=date.today(),
                source='api_auto',
                status='error',
                error_msg=str(exc)[:500],
                triggered_by=current_user.username,
                created_at=datetime.utcnow(),
            )
            db.session.add(log)
            db.session.commit()
            return jsonify({'error': str(exc)}), 500

    @app.route('/dispatch-board')
    @login_required
    def dispatch_board():
        today = date.today()
        active = (
            Vehicle.query
            .filter_by(status='ACTIVE')
            .order_by(Vehicle.impound_date.desc())
            .all()
        )
        return render_template('dispatch/board.html',
            today=today,
            vehicles=active,
        )

    # ── Staff Training Guides ─────────────────────────────────────────────────
    # Short, bookmarkable URLs to the same role guides served from the ? help
    # modal (blueprints/help.py's _HELP data + printable_guide.html), so staff
    # can jump straight to their own guide from inside the app.

    @app.route('/guides/<role>')
    @login_required
    def staff_guide(role):
        from blueprints.help import _HELP, _DEFAULT_HELP
        data = _HELP.get(role, _DEFAULT_HELP)
        return render_template('help/printable_guide.html', data=data, role=role)

    # Standalone embedded guides (own HTML/CSS, no base layout). These static
    # paths take precedence over the /guides/<role> rule above for these two
    # roles specifically; every other role still falls through to it.
    @app.route('/guides/heather')
    @login_required
    def heather_guide():
        return render_template('guides/heather-guide.html')

    @app.route('/guides/tina')
    @login_required
    def tina_guide():
        return render_template('guides/tina-guide.html')

    # ── Search ─────────────────────────────────────────────────────────────────

    @app.route('/search')
    @login_required
    def search():
        q = request.args.get('q', '').strip()
        results = []
        if q:
            like = f'%{q}%'
            ref2_match = (
                db.session.query(CertifiedLetter.id)
                .filter(CertifiedLetter.vehicle_id == Vehicle.id)
                .filter(CertifiedLetter.reference_number_2.ilike(like))
                .exists()
            )
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
                        Vehicle.stock_number.ilike(like),
                        Vehicle.invoice_number.ilike(like),  # Reference #1 on the UPS label
                        ref2_match,  # Reference #2 on the UPS label
                    )
                )
                .order_by(Vehicle.impound_date.desc())
                .all()
            )
        return render_template('search.html', q=q, results=results)

    @app.route('/vin-lookup')
    @login_required
    def vin_lookup():
        """Dedicated VIN-only lookup by the last 4-6 digits — precise suffix
        match, unlike the general /search box which matches any field anywhere."""
        digits = request.args.get('digits', '').strip().upper()
        error = None
        results = []
        if digits:
            if len(digits) < 4:
                error = 'Enter at least 4 characters.'
            else:
                results = (
                    Vehicle.query
                    .filter(Vehicle.vin.isnot(None))
                    .filter(Vehicle.vin.ilike(f'%{digits}'))
                    .order_by(Vehicle.impound_date.desc())
                    .all()
                )
        return render_template('vin_lookup.html', digits=digits, dlen=len(digits), results=results, error=error)

    # ── Staff Feedback ───────────────────────────────────────────────────────────

    @app.route('/feedback/submit', methods=['POST'])
    @login_required
    def feedback_submit():
        body = request.form.get('body', '').strip()
        if not body:
            flash('Enter some feedback before submitting.', 'danger')
            return redirect(request.referrer or url_for('dashboard'))

        db.session.add(StaffFeedback(
            user_id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name or current_user.username,
            body=body,
            page_url=request.form.get('page_url', '').strip() or request.referrer,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash('Feedback sent. Thanks!', 'success')
        return redirect(request.referrer or url_for('dashboard'))

    @app.route('/feedback/<int:feedback_id>/mark-read', methods=['POST'])
    @login_required
    def feedback_mark_read(feedback_id):
        if not current_user.can_see_all:
            flash('Permission denied.', 'danger')
            return redirect(url_for('dashboard'))
        fb = db.get_or_404(StaffFeedback, feedback_id)
        fb.is_read = True
        fb.read_by = current_user.display_name or current_user.username
        fb.read_at = datetime.utcnow()
        db.session.commit()
        return redirect(request.referrer or url_for('dashboard'))

    # ── Pipeline ───────────────────────────────────────────────────────────────

    @app.route('/pipeline')
    @login_required
    def pipeline():
        today = date.today()
        horizon = today + timedelta(days=30)

        upcoming_letters = (
            CertifiedLetter.query
            .join(Vehicle)
            .filter(Vehicle.status == 'ACTIVE')
            .filter(CertifiedLetter.sent_date.is_(None))
            .filter(CertifiedLetter.due_date <= horizon)
            .filter(Vehicle.possible_release.isnot(True))
            .order_by(CertifiedLetter.due_date.asc())
            .all()
        )

        active_vehicles = Vehicle.query.filter_by(status='ACTIVE').all()
        upcoming_eligibility = sorted(
            [v for v in active_vehicles
             if v.title_eligible_date
             and today < v.title_eligible_date <= horizon
             and v.title_filing is None],
            key=lambda v: v.title_eligible_date
        )

        eligible_now = [
            v for v in active_vehicles
            if v.is_title_eligible and v.title_filing is None
        ]

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

    # ── Vehicles ───────────────────────────────────────────────────────────────

    @app.route('/vehicles')
    @login_required
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
        dept_str = form.get('police_department_id', '').strip()
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
            police_department_id=int(dept_str) if dept_str.isdigit() else None,
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
    @login_required
    def vehicles_new():
        if not current_user.can_edit_vehicles:
            flash('You do not have permission to add new vehicles.', 'danger')
            return redirect(url_for('vehicles_list'))
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
                bmv_stage='PENDING',
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
                letter_kind='notice_of_lien' if impound_type == 'POLICE' else 'first_notice',
                recipient_type='owner',
                created_at=datetime.utcnow(),
            ))
            db.session.flush()
            import letter_triggers
            letter_triggers.on_vehicle_created(vehicle, letter1_due)
            db.session.commit()

            label = 'Letter 1' if impound_type == 'PPI' else 'Notification Letter'
            flash(f'{vehicle.display_name} added. {label} due by {letter1_due.strftime("%m/%d/%Y")}.', 'success')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle.id))

        return render_template('vehicles/new.html', today=date.today(), form={})

    @app.route('/vehicles/<int:vehicle_id>')
    @login_required
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
    @login_required
    def vehicles_edit(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_edit_vehicles:
            flash('You do not have permission to edit vehicles.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        if request.method == 'POST':
            _vehicle_from_form(request.form, vehicle=vehicle)
            db.session.commit()
            flash(f'{vehicle.display_name} updated.', 'success')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle.id))
        police_departments = PoliceDepartment.query.filter_by(active=True).order_by(PoliceDepartment.name).all()
        return render_template('vehicles/edit.html', vehicle=vehicle, police_departments=police_departments)

    @app.route('/vehicles/<int:vehicle_id>/release', methods=['POST'])
    @login_required
    def vehicles_release(vehicle_id):
        """Authorize release (paid in full) — vehicle isn't gone yet, just cleared
        to be picked up. See confirm_pickup for the actual departure step."""
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_edit_vehicles:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        vehicle.status = 'PENDING_PICKUP'
        vehicle.pending_pickup_since = datetime.utcnow()
        vehicle.updated_at = datetime.utcnow()
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Marked Paid/Released — pending pickup by {current_user.display_name or current_user.username}.',
            author=current_user.display_name or current_user.username,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'{vehicle.display_name} marked Paid/Released — pending pickup.', 'info')
        return redirect(url_for('dashboard'))

    @app.route('/vehicles/<int:vehicle_id>/confirm-pickup', methods=['POST'])
    @login_required
    def vehicles_confirm_pickup(vehicle_id):
        """The vehicle has actually left the lot — final RELEASED status."""
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_edit_vehicles:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        vehicle.status = 'RELEASED'
        vehicle.updated_at = datetime.utcnow()
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Pickup confirmed by {current_user.display_name or current_user.username} — vehicle has left the lot.',
            author=current_user.display_name or current_user.username,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'{vehicle.display_name} pickup confirmed — released.', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    # ── Valuation Report ───────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/valuation-report')
    @login_required
    def valuation_report(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        from titlebot.storage import calculate_storage
        storage_days, storage_total, storage_breakdown = calculate_storage(
            vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
        )
        total_owed = vehicle.total_owed  # tow + storage + additional charges
        total_dmg = sum(d.amount for d in vehicle.damage_items)
        vehicle_val = max(0, (vehicle.effective_nada_value or 0) - total_dmg)
        net = vehicle_val - total_owed
        return render_template('reports/valuation.html',
            vehicle=vehicle,
            today=date.today(),
            storage_days=storage_days,
            storage_total=storage_total,
            storage_breakdown=storage_breakdown,
            total_owed=total_owed,
            total_dmg=total_dmg,
            vehicle_val=vehicle_val,
            net=net,
            company_name=app.config['COMPANY_NAME'],
            company_address=app.config['COMPANY_ADDRESS'],
            company_phone=app.config['COMPANY_PHONE'],
        )

    # ── Notes ──────────────────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/notes', methods=['POST'])
    @login_required
    def vehicles_add_note(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        body = request.form.get('body', '').strip()
        if body:
            author = request.form.get('author', '').strip()
            if not author and current_user.is_authenticated:
                author = current_user.display_name or current_user.username
            db.session.add(VehicleNote(
                vehicle_id=vehicle.id,
                body=body,
                author=author or 'Staff',
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    @app.route('/notes/<int:note_id>/delete', methods=['POST'])
    @login_required
    def notes_delete(note_id):
        note = db.get_or_404(VehicleNote, note_id)
        vehicle_id = note.vehicle_id
        db.session.delete(note)
        db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

    # ── Documents (LKA / Title Search PDFs) ─────────────────────────────────────

    ALLOWED_DOCUMENT_TYPES = {'LKA', 'TITLE_SEARCH'}

    @app.route('/vehicles/<int:vehicle_id>/documents/upload', methods=['POST'])
    @login_required
    def vehicles_document_upload(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.is_heather:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

        doc_type = request.form.get('doc_type', '').strip().upper()
        if doc_type not in ALLOWED_DOCUMENT_TYPES:
            flash('Invalid document type.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#documents')

        upload = request.files.get('file')
        if not upload or not upload.filename:
            flash('Choose a file to upload.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#documents')

        file_bytes = upload.read()
        if not file_bytes:
            flash('That file appears to be empty.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#documents')

        actor = current_user.display_name or current_user.username
        db.session.add(VehicleDocument(
            vehicle_id=vehicle.id,
            doc_type=doc_type,
            filename=secure_filename(upload.filename),
            content_type=upload.content_type or 'application/octet-stream',
            file_data=file_bytes,
            uploaded_by=actor,
            uploaded_at=datetime.utcnow(),
        ))

        # Uploading the file is the confirmation — check the matching checklist box.
        if doc_type == 'LKA':
            vehicle.lka_document_confirmed = True
        else:
            vehicle.title_search_confirmed = True
        vehicle.updated_at = datetime.utcnow()

        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'{"LKA (BMV 2433)" if doc_type == "LKA" else "Title Search (BMV 1148)"} document uploaded by {actor}.',
            author=actor,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()

        flash(f'Document uploaded and confirmed.', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#documents')

    @app.route('/documents/<int:doc_id>')
    @login_required
    def vehicles_document_view(doc_id):
        doc = db.get_or_404(VehicleDocument, doc_id)
        return send_file(
            io.BytesIO(doc.file_data),
            mimetype=doc.content_type or 'application/octet-stream',
            as_attachment=False,
            download_name=doc.filename or f'document_{doc.id}',
        )

    @app.route('/documents/<int:doc_id>/delete', methods=['POST'])
    @login_required
    def vehicles_document_delete(doc_id):
        doc = db.get_or_404(VehicleDocument, doc_id)
        vehicle_id = doc.vehicle_id
        if not current_user.is_heather:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        db.session.delete(doc)
        db.session.commit()
        flash('Document removed.', 'info')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#documents')

    # ── Letters ────────────────────────────────────────────────────────────────

    @app.route('/letters/<int:letter_id>/mark-sent', methods=['GET', 'POST'])
    @login_required
    def letters_mark_sent(letter_id):
        letter = db.get_or_404(CertifiedLetter, letter_id)

        if letter.vehicle.possible_release:
            flash(
                f'{letter.vehicle.display_name} is flagged Possible Release — verify it\'s '
                'still on the lot before sending any letter.',
                'danger',
            )
            return redirect(url_for('vehicles_detail', vehicle_id=letter.vehicle_id))

        if letter.vehicle.vin_check_blocked:
            flash(
                f'{letter.vehicle.display_name} has a VIN mismatch from field photo verification — '
                'resolve it before sending any letter.',
                'danger',
            )
            return redirect(url_for('vehicles_detail', vehicle_id=letter.vehicle_id))

        if request.method == 'POST':
            sent_str = request.form.get('sent_date', '').strip()
            sent_date = date.fromisoformat(sent_str) if sent_str else date.today()

            letter.sent_date = sent_date
            letter.tracking_number = request.form.get('tracking_number', '').strip() or None
            letter.reference_number_2 = request.form.get('reference_number_2', '').strip() or None
            letter.notes = request.form.get('notes', '').strip() or letter.notes

            vehicle = letter.vehicle

            if vehicle.impound_type == 'PPI' and letter.letter_number == 1:
                letter2_due = sent_date + timedelta(days=PPI_LETTER2_DAYS)
                db.session.add(CertifiedLetter(
                    vehicle_id=vehicle.id,
                    letter_number=2,
                    due_date=letter2_due,
                    letter_kind='second_notice',
                    recipient_type='owner',
                    created_at=datetime.utcnow(),
                ))
                flash(f'Letter 1 sent. Letter 2 due by {letter2_due.strftime("%m/%d/%Y")}.', 'success')
            else:
                flash(f'{letter.label} marked as sent for {vehicle.display_name}.', 'success')

            # 5-letter system: unlocks POLICE's 2nd Owner Notice (letter_number
            # 4, once its 1st Owner Notice — letter_number 3 — is sent) and
            # either impound type's 2nd Lienholder Notice (letter_number 6).
            import letter_triggers
            letter_triggers.on_letter_sent(vehicle, letter)

            vehicle.updated_at = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('dashboard'))

        return render_template('letters/mark_sent.html', letter=letter, today=date.today())

    @app.route('/letters/<int:letter_id>/confirm-delivery', methods=['POST'])
    @login_required
    def letters_confirm_delivery(letter_id):
        letter = db.get_or_404(CertifiedLetter, letter_id)
        confirmed_str = request.form.get('delivery_date', '').strip()
        letter.delivery_confirmed_date = (
            date.fromisoformat(confirmed_str) if confirmed_str else date.today()
        )
        db.session.commit()
        flash('Delivery confirmation recorded.', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=letter.vehicle_id))

    # ── Title Filing ───────────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/file-title', methods=['GET', 'POST'])
    @login_required
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

    # ── Print routes ───────────────────────────────────────────────────────────

    @app.route('/print/post-office')
    @login_required
    def print_post_office():
        today = date.today()
        week_ahead = today + timedelta(days=7)

        pending_letters = (
            CertifiedLetter.query
            .join(Vehicle)
            .filter(Vehicle.status == 'ACTIVE')
            .filter(CertifiedLetter.sent_date.is_(None))
            .filter(Vehicle.possible_release.isnot(True))
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

    @app.route('/letters/<int:letter_id>/print')
    @login_required
    def print_letter(letter_id):
        letter = db.get_or_404(CertifiedLetter, letter_id)
        vehicle = letter.vehicle

        # Ghost vehicles: hard block on ALL letter generation, no exceptions —
        # this is stricter than before (previously only the mark-sent action
        # was blocked; print/view was not).
        if vehicle.possible_release:
            flash(
                f'{vehicle.display_name} is flagged Possible Release (ghost vehicle) — '
                'letter generation is blocked until this is verified/resolved.',
                'danger',
            )
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle.id))

        return render_template('print/letter.html',
            letter=letter,
            vehicle=vehicle,
            today=date.today(),
            company_name=app.config['COMPANY_NAME'],
            company_address=app.config['COMPANY_ADDRESS'],
            company_phone=app.config['COMPANY_PHONE'],
            storage_address=app.config['STORAGE_ADDRESS'],
        )

    # ── Towbook PDF import ─────────────────────────────────────────────────────

    @app.route('/towbook-import', methods=['POST'])
    @login_required
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

    # ── Damage items ───────────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/damages', methods=['POST'])
    @login_required
    def damages_add(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_edit_vehicles:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
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
    @login_required
    def damages_delete(damage_id):
        item = db.get_or_404(DamageItem, damage_id)
        vehicle_id = item.vehicle_id
        db.session.delete(item)
        db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')

    # ── Additional Charges ───────────────────────────────────────────────────
    CHARGES_ROLES = ('heather', 'tina', 'tim', 'brady', 'jim')

    @app.route('/vehicles/<int:vehicle_id>/charges', methods=['POST'])
    @login_required
    def vehicle_charges_add(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if current_user.role not in CHARGES_ROLES:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

        label = request.form.get('label', '').strip()
        amount_str = request.form.get('amount', '').strip()
        date_str = request.form.get('charge_date', '').strip()

        if not label or not amount_str:
            flash('Label and amount are required.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#charges')
        try:
            amount = float(amount_str)
        except ValueError:
            flash('Invalid amount.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#charges')

        try:
            charge_date = date.fromisoformat(date_str) if date_str else date.today()
        except ValueError:
            charge_date = date.today()

        db.session.add(VehicleCharge(
            vehicle_id=vehicle.id,
            label=label,
            amount=amount,
            charge_date=charge_date,
            added_by=current_user.display_name or current_user.username,
            added_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'Charge added: {label} ${amount:.2f}', 'success')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#charges')

    @app.route('/charges/<int:charge_id>/delete', methods=['POST'])
    @login_required
    def vehicle_charges_delete(charge_id):
        charge = db.get_or_404(VehicleCharge, charge_id)
        vehicle_id = charge.vehicle_id
        if current_user.role not in CHARGES_ROLES:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        db.session.delete(charge)
        db.session.commit()
        flash('Charge removed.', 'info')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#charges')

    @app.route('/vehicles/<int:vehicle_id>/damages/auto-fill', methods=['POST'])
    @login_required
    def damages_auto_fill(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        from titlebot.damages import auto_fill_fallbacks
        from titlebot.storage import calculate_storage
        nada = vehicle.effective_nada_value or 3499.0
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

    # ── NADA lookup ────────────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/nada-lookup', methods=['POST'])
    @login_required
    def nada_lookup(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_edit_vehicles:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
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
        vehicle.nada_value_is_default = result['used_default']
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

    @app.route('/vehicles/<int:vehicle_id>/nada-override', methods=['POST'])
    @login_required
    def nada_override(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.is_heather:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))
        override_str = request.form.get('nada_value_override', '').strip()
        if override_str:
            try:
                vehicle.nada_value_override = float(override_str)
                flash(f'Manual NADA value set to ${vehicle.nada_value_override:,.2f}.', 'success')
            except ValueError:
                flash('Enter a valid dollar amount.', 'danger')
                return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')
        else:
            vehicle.nada_value_override = None
            flash('Manual override cleared — using the looked-up/fallback value again.', 'info')
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')

    # ── Letter Clock Restart ──────────────────────────────────────────────────
    # impound_date is locked forever and the 60-day title-eligibility clock
    # (Vehicle.title_eligible_date) always reads it directly — restart_date never
    # touches that. This only re-anchors the due date of whichever letter is
    # currently pending (Letter 2 if it exists, otherwise Letter 1/Notification),
    # and — if that letter was already sent and came back RTS/address-issue —
    # resets it to unsent so it re-enters the Need to Send queue for a resend.
    @app.route('/vehicles/<int:vehicle_id>/restart-letters', methods=['POST'])
    @login_required
    def vehicles_restart_letters(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.is_heather:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id))

        if vehicle.possible_release:
            flash(
                f'{vehicle.display_name} is flagged Possible Release — verify it\'s '
                'still on the lot before restarting the letter clock.',
                'danger',
            )
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#letters')

        if vehicle.vin_check_blocked:
            flash(
                f'{vehicle.display_name} has a VIN mismatch from field photo verification — '
                'resolve it before restarting the letter clock.',
                'danger',
            )
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#letters')

        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('A reason is required to restart the letter clock.', 'danger')
            return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#letters')

        restart_str = request.form.get('restart_date', '').strip()
        new_restart_date = date.fromisoformat(restart_str) if restart_str else date.today()

        actor = current_user.display_name or current_user.username
        vehicle.restart_date = new_restart_date
        vehicle.restart_reason = reason
        vehicle.restart_set_by = actor
        vehicle.restart_set_at = datetime.utcnow()

        target = vehicle.letter2 or vehicle.letter1
        if target is None:
            target = CertifiedLetter(vehicle_id=vehicle.id, letter_number=1, created_at=datetime.utcnow())
            db.session.add(target)

        if target.letter_number == 1:
            days_offset = PPI_LETTER1_DAYS if vehicle.impound_type == 'PPI' else POLICE_LETTER1_DAYS
        else:
            days_offset = PPI_LETTER2_DAYS

        target.due_date = new_restart_date + timedelta(days=days_offset)
        target.sent_date = None
        target.tracking_number = None
        target.delivery_confirmed_date = None
        target.scheduled_delivery = None
        target.ups_status = None
        target.return_to_sender = False
        target.updated_at = datetime.utcnow()

        vehicle.letter_flag = None
        vehicle.letter_flag_detail = None
        vehicle.letter_stage = 'needs_1st' if target.letter_number == 1 else 'awaiting_2nd'
        vehicle.updated_at = datetime.utcnow()

        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'{target.label} clock restarted to {new_restart_date.strftime("%m/%d/%Y")} '
                 f'by {actor}. Reason: {reason}',
            author=actor,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()

        from task_engine import recalculate_vehicle
        recalculate_vehicle(vehicle)
        db.session.commit()

        flash(
            f'Letter clock restarted. {target.label} now due by '
            f'{target.due_date.strftime("%m/%d/%Y")}.',
            'success',
        )
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#letters')

    # ── Task Backlog Suppression (Snooze) ────────────────────────────────────
    # Only Tim-level users (tim/jim, plus wally who uses the tim role) can
    # snooze/un-snooze — this hides a vehicle from Heather's and Tina's daily
    # queues for a fixed window without touching its data or letter clock.

    @app.route('/vehicles/<int:vehicle_id>/snooze', methods=['POST'])
    @login_required
    def vehicles_snooze(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_see_all:
            flash('Permission denied.', 'danger')
            return redirect(request.referrer or url_for('vehicles_detail', vehicle_id=vehicle_id))

        try:
            days = int(request.form.get('days', ''))
        except ValueError:
            days = 0
        if days not in (7, 14, 30):
            flash('Choose a snooze length of 7, 14, or 30 days.', 'danger')
            return redirect(request.referrer or url_for('vehicles_detail', vehicle_id=vehicle_id))

        actor = current_user.display_name or current_user.username
        vehicle.snoozed_until = date.today() + timedelta(days=days)
        vehicle.snoozed_at = datetime.utcnow()
        vehicle.snoozed_by = actor
        vehicle.updated_at = datetime.utcnow()
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Snoozed for {days} days (until {vehicle.snoozed_until.strftime("%m/%d/%Y")}) by {actor}. '
                 'Hidden from the main task queues until it expires.',
            author=actor,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'{vehicle.display_name} snoozed until {vehicle.snoozed_until.strftime("%m/%d/%Y")}.', 'success')
        return redirect(request.referrer or url_for('vehicles_detail', vehicle_id=vehicle_id))

    @app.route('/vehicles/<int:vehicle_id>/unsnooze', methods=['POST'])
    @login_required
    def vehicles_unsnooze(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        if not current_user.can_see_all:
            flash('Permission denied.', 'danger')
            return redirect(request.referrer or url_for('vehicles_detail', vehicle_id=vehicle_id))

        actor = current_user.display_name or current_user.username
        vehicle.snoozed_until = None
        vehicle.snoozed_at = None
        vehicle.snoozed_by = None
        vehicle.updated_at = datetime.utcnow()
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Un-snoozed by {actor} — back in the main task queues.',
            author=actor,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
        flash(f'{vehicle.display_name} is back in the main task queues.', 'info')
        return redirect(request.referrer or url_for('vehicles_detail', vehicle_id=vehicle_id))

    @app.route('/snoozed')
    @login_required
    def vehicles_snoozed():
        if not (current_user.can_see_heather_dashboard or current_user.can_see_tina_dashboard):
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        snoozed = (
            Vehicle.query
            .filter(Vehicle.snoozed_until.isnot(None))
            .filter(Vehicle.snoozed_until >= date.today())
            .order_by(Vehicle.snoozed_until.asc())
            .all()
        )
        return render_template('snoozed.html', snoozed=snoozed)

    # ── Title Packet PDF ───────────────────────────────────────────────────────

    @app.route('/vehicles/<int:vehicle_id>/title-packet.pdf')
    @login_required
    def title_packet_pdf(vehicle_id):
        vehicle = db.get_or_404(Vehicle, vehicle_id)
        template_path = app.config['TITLE_PACKET_TEMPLATE']
        if not os.path.isfile(template_path):
            flash(
                f'Title packet template not found at: {template_path}. '
                'Set TITLE_PACKET_TEMPLATE environment variable.',
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

    # ── UPS Quantum View import ────────────────────────────────────────────────

    @app.route('/import/quantum-view', methods=['GET', 'POST'])
    @login_required
    def import_quantum_view():
        if request.method == 'GET':
            return render_template('import/quantum_view.html')

        uploaded = request.files.get('csv_file')
        if not uploaded or not uploaded.filename:
            flash('Please select a CSV file.', 'danger')
            return redirect(url_for('import_quantum_view'))

        try:
            content = uploaded.stream.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            content = uploaded.stream.read().decode('latin-1')

        rows, tracking_col, headers = parse_quantum_view_csv(content)

        if not rows:
            flash(
                f'No tracking rows found. Headers detected: {", ".join(headers) or "none"}.',
                'warning'
            )
            return redirect(url_for('import_quantum_view'))

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

    # ── Hub (unified navigation for Jim, Lawrence, Tim's dad) ─────────────────

    @app.route('/hub')
    @login_required
    def hub():
        return render_template('hub.html', today=date.today())

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
