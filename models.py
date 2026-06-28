from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta

db = SQLAlchemy()

# Ohio BMV title-by-abandonment deadlines
PPI_LETTER1_DAYS = 5        # Letter 1 must be sent within 5 days of impound
PPI_LETTER2_DAYS = 30       # Letter 2 sent 30 days after Letter 1
PPI_TITLE_FROM_IMPOUND = 60 # Must be 60 days since impound
PPI_TITLE_FROM_LETTER2 = 30 # Must be 30 days since Letter 2

POLICE_LETTER1_DAYS = 10    # Notification required within 10 days (ORC 4513.61)
POLICE_TITLE_FROM_LETTER1 = 30

ROLES = ['tim', 'heather', 'tina', 'dispatcher']


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='dispatcher')
    display_name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def can_see_all(self):
        return self.role == 'tim'

    @property
    def is_heather(self):
        return self.role in ('heather', 'tim')

    @property
    def is_tina(self):
        return self.role in ('tina', 'tim')

    @property
    def is_dispatcher(self):
        return self.role in ('dispatcher', 'tim')


class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    vin = db.Column(db.String(17))
    plate = db.Column(db.String(20))
    plate_state = db.Column(db.String(2), default='OH')
    year = db.Column(db.Integer)
    make = db.Column(db.String(50))
    model_name = db.Column(db.String(50))
    color = db.Column(db.String(30))

    impound_type = db.Column(db.String(10), nullable=False)  # PPI or POLICE
    impound_date = db.Column(db.Date, nullable=False)
    storage_location = db.Column(db.String(100))
    police_report_number = db.Column(db.String(50))

    # Towbook sync fields
    stock_number = db.Column(db.String(50), index=True)
    call_number = db.Column(db.String(50))
    invoice_number = db.Column(db.String(50))
    account = db.Column(db.String(100))
    model = db.Column(db.String(50))
    impound_reason = db.Column(db.String(200))
    have_keys = db.Column(db.Boolean)
    tasks_overdue = db.Column(db.Integer, default=0)
    tasks_due_today = db.Column(db.Integer, default=0)
    tasks_due_next = db.Column(db.Integer, default=0)
    tasks_due_soon = db.Column(db.Integer, default=0)
    balance_due = db.Column(db.Float)
    last_synced = db.Column(db.DateTime)

    # Owner info
    owner_name = db.Column(db.String(100))
    owner_address = db.Column(db.Text)

    # Lienholder
    lienholder_name = db.Column(db.String(100))
    lienholder_address = db.Column(db.Text)
    lienholder_city = db.Column(db.String(50))
    lienholder_state = db.Column(db.String(2))
    lienholder_zip = db.Column(db.String(10))

    # Financial
    tow_fee = db.Column(db.Float)
    daily_storage_rate = db.Column(db.Float)
    nada_value = db.Column(db.Float)
    mileage = db.Column(db.Integer)

    # Workflow: Heather's stage
    bmv_stage = db.Column(db.String(20), default='PENDING')  # PENDING, QUEUED, SEARCHED, COMPLETE
    bmv_searched_date = db.Column(db.Date)
    bmv_search_notes = db.Column(db.Text)
    heather_complete = db.Column(db.Boolean, default=False)
    heather_complete_date = db.Column(db.Date)

    # Disposition / Tina's workflow
    disposition = db.Column(db.String(10))  # SELL, JUNK, HOLD
    disposition_set_date = db.Column(db.Date)
    disposition_notes = db.Column(db.Text)
    tina_stage = db.Column(db.String(20))  # QUEUED, TITLE_WORK, COURT, READY, COMPLETE

    # Court / police affidavit tracking (Tina)
    court_date = db.Column(db.Date)
    court_notes = db.Column(db.Text)
    affidavit_filed_date = db.Column(db.Date)
    affidavit_notes = db.Column(db.Text)

    # Sale / junk invoice
    sale_price = db.Column(db.Float)
    sale_date = db.Column(db.Date)
    buyer_name = db.Column(db.String(100))
    junk_weight_lbs = db.Column(db.Float)
    junk_price_per_ton = db.Column(db.Float)
    junk_yard_name = db.Column(db.String(100))

    # Payment
    storage_paid = db.Column(db.Float, default=0.0)
    payment_date = db.Column(db.Date)
    payment_reference = db.Column(db.String(100))

    status = db.Column(db.String(20), nullable=False, default='ACTIVE')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)

    letters = db.relationship(
        'CertifiedLetter', back_populates='vehicle',
        order_by='CertifiedLetter.letter_number',
        cascade='all, delete-orphan'
    )
    title_filing = db.relationship(
        'TitleFiling', back_populates='vehicle',
        uselist=False, cascade='all, delete-orphan'
    )
    note_entries = db.relationship(
        'VehicleNote', back_populates='vehicle',
        cascade='all, delete-orphan'
    )
    damage_items = db.relationship(
        'DamageItem', back_populates='vehicle',
        order_by='DamageItem.sort_order',
        cascade='all, delete-orphan'
    )
    envelope_scans = db.relationship(
        'EnvelopeScan', back_populates='vehicle',
        cascade='all, delete-orphan'
    )
    invoices = db.relationship(
        'Invoice', back_populates='vehicle',
        cascade='all, delete-orphan'
    )
    payments = db.relationship(
        'PaymentTransaction', back_populates='vehicle',
        cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f'<Vehicle {self.id}: {self.display_name}>'

    @property
    def display_name(self):
        parts = [str(self.year) if self.year else None, self.make, self.model_name or self.model]
        name = ' '.join(p for p in parts if p)
        if not name:
            if self.plate:
                return f'{self.plate} ({self.plate_state})'
            if self.vin:
                return f'VIN ...{self.vin[-6:]}'
            return f'Vehicle #{self.id}'
        return name

    @property
    def sorted_notes(self):
        return sorted(
            self.note_entries,
            key=lambda n: n.created_at or datetime.min,
            reverse=True
        )

    @property
    def letter1(self):
        return next((l for l in self.letters if l.letter_number == 1), None)

    @property
    def letter2(self):
        return next((l for l in self.letters if l.letter_number == 2), None)

    @property
    def title_eligible_date(self):
        if self.impound_type == 'PPI':
            l2 = self.letter2
            if l2 and l2.sent_date:
                return max(
                    self.impound_date + timedelta(days=PPI_TITLE_FROM_IMPOUND),
                    l2.sent_date + timedelta(days=PPI_TITLE_FROM_LETTER2),
                )
        elif self.impound_type == 'POLICE':
            l1 = self.letter1
            if l1 and l1.sent_date:
                return l1.sent_date + timedelta(days=POLICE_TITLE_FROM_LETTER1)
        return None

    @property
    def is_title_eligible(self):
        elig = self.title_eligible_date
        return elig is not None and date.today() >= elig

    @property
    def days_in_storage(self):
        return (date.today() - self.impound_date).days

    @property
    def next_action_label(self):
        if self.status != 'ACTIVE':
            return None
        today = date.today()
        l1 = self.letter1
        l2 = self.letter2

        if self.impound_type == 'PPI':
            if not l1 or not l1.sent_date:
                due = self.impound_date + timedelta(days=PPI_LETTER1_DAYS)
                prefix = 'OVERDUE: ' if today > due else ''
                return f'{prefix}Send Letter 1 by {due.strftime("%m/%d/%Y")}'
            if not l2 or not l2.sent_date:
                if l2:
                    due = l2.due_date
                    prefix = 'OVERDUE: ' if today > due else ''
                    return f'{prefix}Send Letter 2 by {due.strftime("%m/%d/%Y")}'
            elig = self.title_eligible_date
            if elig:
                if today >= elig:
                    return 'Ready to file for title'
                return f'Title eligible {elig.strftime("%m/%d/%Y")} ({(elig - today).days} days)'

        elif self.impound_type == 'POLICE':
            if not l1 or not l1.sent_date:
                due = self.impound_date + timedelta(days=POLICE_LETTER1_DAYS)
                prefix = 'OVERDUE: ' if today > due else ''
                return f'{prefix}Send Notification Letter by {due.strftime("%m/%d/%Y")}'
            elig = self.title_eligible_date
            if elig:
                if today >= elig:
                    return 'Ready to file for title'
                return f'Title eligible {elig.strftime("%m/%d/%Y")} ({(elig - today).days} days)'

        return 'Waiting'

    @property
    def stoplight_color(self):
        """red/yellow/green for Heather's dashboard."""
        today = date.today()
        l1 = self.letter1
        l2 = self.letter2

        if self.impound_type == 'PPI':
            if not l1 or not l1.sent_date:
                due = self.impound_date + timedelta(days=PPI_LETTER1_DAYS)
                if today > due:
                    return 'red'
                elif (due - today).days <= 2:
                    return 'yellow'
                return 'green'
            if not l2 or not l2.sent_date:
                if l2 and today > l2.due_date:
                    return 'red'
                elif l2 and (l2.due_date - today).days <= 3:
                    return 'yellow'
                return 'green'
        elif self.impound_type == 'POLICE':
            if not l1 or not l1.sent_date:
                due = self.impound_date + timedelta(days=POLICE_LETTER1_DAYS)
                if today > due:
                    return 'red'
                elif (due - today).days <= 3:
                    return 'yellow'
                return 'green'
        return 'green'

    @property
    def total_owed(self):
        from titlebot.storage import calculate_storage
        _, storage_total, _ = calculate_storage(
            self.impound_date, date.today(), self.daily_storage_rate or 0
        )
        return (self.tow_fee or 0) + storage_total

    @property
    def total_storage_owed(self):
        from titlebot.storage import calculate_storage
        _, storage_total, _ = calculate_storage(
            self.impound_date, date.today(), self.daily_storage_rate or 0
        )
        return storage_total


class CertifiedLetter(db.Model):
    __tablename__ = 'certified_letters'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    letter_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    sent_date = db.Column(db.Date)
    tracking_number = db.Column(db.String(50))
    delivery_confirmed_date = db.Column(db.Date)
    return_to_sender = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime)

    vehicle = db.relationship('Vehicle', back_populates='letters')

    @property
    def is_overdue(self):
        return self.sent_date is None and self.due_date < date.today()

    @property
    def is_due_today(self):
        return self.sent_date is None and self.due_date == date.today()

    @property
    def days_overdue(self):
        if self.is_overdue:
            return (date.today() - self.due_date).days
        return 0

    @property
    def days_until_due(self):
        if self.sent_date:
            return None
        return (self.due_date - date.today()).days

    @property
    def label(self):
        if self.vehicle.impound_type == 'POLICE' and self.letter_number == 1:
            return 'Notification Letter'
        return f'Letter {self.letter_number}'

    @property
    def tracking_normalized(self):
        if not self.tracking_number:
            return None
        return self.tracking_number.replace(' ', '').replace('-', '').upper()


class TitleFiling(db.Model):
    __tablename__ = 'title_filings'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    filed_date = db.Column(db.Date)
    bmv_receipt_number = db.Column(db.String(50))
    status = db.Column(db.String(20), default='FILED')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime)

    vehicle = db.relationship('Vehicle', back_populates='title_filing')


class VehicleNote(db.Model):
    __tablename__ = 'vehicle_notes'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(50), default='Heather')
    created_at = db.Column(db.DateTime)

    vehicle = db.relationship('Vehicle', back_populates='note_entries')


class DamageItem(db.Model):
    __tablename__ = 'damage_items'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    description = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    is_fallback = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime)

    vehicle = db.relationship('Vehicle', back_populates='damage_items')


class EnvelopeScan(db.Model):
    __tablename__ = 'envelope_scans'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    tracking_number = db.Column(db.String(50))
    scan_date = db.Column(db.DateTime, default=datetime.utcnow)
    image_path = db.Column(db.String(500))
    scan_notes = db.Column(db.Text)
    is_return_to_sender = db.Column(db.Boolean, default=False)
    is_delivered = db.Column(db.Boolean, default=False)
    delivery_date = db.Column(db.Date)
    claude_raw_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', back_populates='envelope_scans')


class Invoice(db.Model):
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    invoice_type = db.Column(db.String(10), nullable=False)  # SALE or JUNK
    invoice_number = db.Column(db.String(30), unique=True)
    issue_date = db.Column(db.Date, default=date.today)

    # Sale invoice
    buyer_name = db.Column(db.String(100))
    buyer_address = db.Column(db.Text)
    sale_price = db.Column(db.Float)

    # Junk invoice
    junk_yard_name = db.Column(db.String(100))
    junk_yard_address = db.Column(db.Text)
    weight_lbs = db.Column(db.Float)
    price_per_ton = db.Column(db.Float)

    tow_fee = db.Column(db.Float)
    storage_fee = db.Column(db.Float)
    storage_days = db.Column(db.Integer)
    total_fees = db.Column(db.Float)
    net_proceeds = db.Column(db.Float)

    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', back_populates='invoices')

    @property
    def gross_amount(self):
        if self.invoice_type == 'SALE':
            return self.sale_price or 0
        elif self.invoice_type == 'JUNK' and self.weight_lbs and self.price_per_ton:
            return round((self.weight_lbs / 2000) * self.price_per_ton, 2)
        return 0


class PaymentTransaction(db.Model):
    __tablename__ = 'payment_transactions'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_type = db.Column(db.String(20))  # CASH, CARD, CHECK
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)
    reference_number = db.Column(db.String(100))
    cloudpos_transaction_id = db.Column(db.String(100))
    notes = db.Column(db.Text)
    processed_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', back_populates='payments')


class Driver(db.Model):
    __tablename__ = 'drivers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    towbook_driver_id = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    sms_opt_in = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    suggestions = db.relationship('DriverSuggestion', back_populates='driver')
    sms_log = db.relationship('DriverSMS', back_populates='driver')


class DriverSuggestion(db.Model):
    __tablename__ = 'driver_suggestions'

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=False)
    week_of = db.Column(db.Date)
    category = db.Column(db.String(20))  # equipment, information, process
    body = db.Column(db.Text, nullable=False)
    question_number = db.Column(db.Integer)  # 1, 2, or 3
    action_taken = db.Column(db.Text)
    acknowledged = db.Column(db.Boolean, default=False)
    acknowledged_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship('Driver', back_populates='suggestions')


class DriverSMS(db.Model):
    __tablename__ = 'driver_sms'

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=False)
    direction = db.Column(db.String(10))  # outbound, inbound
    body = db.Column(db.Text)
    twilio_sid = db.Column(db.String(50))
    status = db.Column(db.String(20))
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    week_of = db.Column(db.Date)
    sms_type = db.Column(db.String(30))  # weekly_summary, bonus_call, question, reply

    driver = db.relationship('Driver', back_populates='sms_log')


class TimecardException(db.Model):
    __tablename__ = 'timecard_exceptions'

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('drivers.id'), nullable=True)
    driver_name = db.Column(db.String(100))
    exception_date = db.Column(db.Date)
    exception_type = db.Column(db.String(30))
    # missing_punch, suspicious_gap, dispatch_mismatch, short_shift
    description = db.Column(db.Text)
    suggested_correction = db.Column(db.Text)
    approved_by = db.Column(db.String(50))
    approved_at = db.Column(db.DateTime)
    resolved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship('Driver', foreign_keys=[driver_id])
