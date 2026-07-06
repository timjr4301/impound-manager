import json
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

ROLES = ['tim', 'heather', 'tina', 'dispatcher', 'lawrence', 'lori', 'brady', 'jim', 'demo']


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

    # ── Role helpers ──────────────────────────────────────────────────────────

    @property
    def can_see_all(self):
        return self.role in ('tim', 'jim')

    @property
    def is_heather(self):
        """Tim, Jim, Lawrence, Lori, Brady, Heather, Tina, and demo can perform/preview Heather-role actions."""
        return self.role in ('heather', 'tina', 'tim', 'jim', 'lawrence', 'lori', 'brady', 'demo')

    @property
    def is_tina(self):
        """Tim, Jim, Lawrence, Lori, Brady, Tina, and demo can perform/preview Tina-role actions."""
        return self.role in ('tina', 'tim', 'jim', 'lawrence', 'lori', 'brady', 'demo')

    @property
    def is_dispatcher(self):
        return self.role in ('dispatcher', 'tim', 'jim', 'lawrence', 'lori', 'brady', 'demo')

    @property
    def can_edit_vehicles(self):
        return self.role in ('tim', 'tina', 'jim', 'lawrence', 'lori', 'brady', 'heather')

    @property
    def can_see_heather_dashboard(self):
        return self.role in ('tim', 'heather', 'tina', 'jim', 'lawrence', 'lori', 'brady', 'demo')

    @property
    def can_see_tina_dashboard(self):
        return self.role in ('tim', 'tina', 'jim', 'lawrence', 'lori', 'brady', 'demo')

    @property
    def can_see_drivers(self):
        """Only Tim and Jim (owner) have access to driver pay, payroll, timecards, HR — excluded from demo (sensitive employee data)."""
        return self.role in ('tim', 'jim')

    @property
    def can_see_dispatch(self):
        return self.role in ('tim', 'dispatcher', 'jim', 'lawrence', 'lori', 'brady', 'demo')

    @property
    def can_collect_payments(self):
        return self.role in ('tim', 'tina', 'dispatcher', 'jim', 'lawrence', 'lori', 'brady')

    @property
    def is_owner(self):
        """Jim is the owner — override actions should be visually flagged (purple)."""
        return self.role == 'jim'

    @property
    def is_demo(self):
        return self.role == 'demo'


class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    vin = db.Column(db.String(17))
    plate = db.Column(db.String(20))
    plate_state = db.Column(db.String(20), default='OH')
    year = db.Column(db.String(10))
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
    model = db.Column(db.String(100))
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
    owner_city = db.Column(db.String(100))
    owner_state = db.Column(db.String(10))
    owner_zip = db.Column(db.String(15))
    po_box_flag = db.Column(db.Boolean, default=False)

    # Title / BMV
    title_number = db.Column(db.String(50))

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
    nada_value_is_default = db.Column(db.Boolean, default=False)
    nada_value_override = db.Column(db.Float)
    mileage = db.Column(db.Integer)

    @property
    def effective_nada_value(self):
        """The value to use in financial calcs: manual override wins over the API/fallback value."""
        return self.nada_value_override if self.nada_value_override is not None else self.nada_value

    @property
    def nada_needs_verification(self):
        """True when we're relying on the $3,499 fallback and nobody has confirmed a real value yet."""
        return self.nada_value_override is None and bool(self.nada_value_is_default)

    # Workflow: Heather's stage
    bmv_stage = db.Column(db.String(20), default='PENDING')  # PENDING, QUEUED, SEARCHED, COMPLETE
    bmv_searched_date = db.Column(db.Date)
    bmv_search_notes = db.Column(db.Text)
    heather_complete = db.Column(db.Boolean, default=False)
    heather_complete_date = db.Column(db.Date)

    # File completeness checklist (Heather confirms before Tina handoff)
    lka_document_confirmed   = db.Column(db.Boolean, default=False, nullable=False)
    title_search_confirmed   = db.Column(db.Boolean, default=False, nullable=False)
    ups_delivery_confirmed   = db.Column(db.Boolean, default=False, nullable=False)
    return_receipt_filed     = db.Column(db.Boolean, default=False, nullable=False)

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

    # Task 5: No Record Found URGENT flag (set by Heather, cleared only by Tim)
    task_no_record = db.Column(db.Boolean, default=False)
    task_no_record_notes = db.Column(db.Text)
    task_no_record_resolved = db.Column(db.Boolean, default=False)
    task_no_record_resolved_by = db.Column(db.String(50))
    task_no_record_resolved_date = db.Column(db.Date)

    # Task 4 auto-trigger tracking
    task4_triggered = db.Column(db.Boolean, default=False)
    task4_triggered_date = db.Column(db.Date)

    # Anomaly flag — hides vehicle from Heather/Tina queues until Tim/Jim/Admin
    # reviews it. Anyone can flag it (with a reason); only all_access roles can clear it.
    is_anomaly = db.Column(db.Boolean, default=False)
    anomaly_reason = db.Column(db.Text)
    anomaly_flagged_by = db.Column(db.String(50))
    anomaly_flagged_at = db.Column(db.DateTime)

    # Accident for Owner — informational only, never blocks letters. Set manually
    # when Heather marks a letter's Reference #2 as "AFO", or auto-detected when
    # the envelope scanner reads "AFO" back off a returned envelope's Reference #2.
    is_afo = db.Column(db.Boolean, default=False)
    afo_detected_at = db.Column(db.DateTime)
    afo_detected_by = db.Column(db.String(50))   # username, or 'envelope-scan'

    # VIN photo verification — driver photographs the door jamb VIN sticker and
    # the dash VIN plate at time of tow (damage_docs field form). Claude Opus
    # reads both and compares them to each other and to Vehicle.vin. Unlike
    # is_afo/is_anomaly, a mismatch here HARD BLOCKS certified letters — a wrong
    # VIN means the legal notice is for the wrong car.
    vin_door_jamb_photo = db.Column(db.Text)      # base64 data URL
    vin_dash_photo      = db.Column(db.Text)      # base64 data URL
    vin_door_jamb_read  = db.Column(db.String(20))
    vin_dash_read       = db.Column(db.String(20))
    vin_verified         = db.Column(db.Boolean, default=False)
    vin_mismatch          = db.Column(db.Boolean, default=False)
    vin_verification_notes = db.Column(db.Text)
    vin_verified_at      = db.Column(db.DateTime)
    vin_mismatch_resolved      = db.Column(db.Boolean, default=False)
    vin_mismatch_resolved_by   = db.Column(db.String(50))
    vin_mismatch_resolved_date = db.Column(db.Date)

    @property
    def vin_check_blocked(self):
        """True when a VIN photo mismatch is unresolved — hard-blocks letters."""
        return bool(self.vin_mismatch) and not self.vin_mismatch_resolved

    # Paid/Released Pending Pickup — set when staff authorize release (paid in
    # full) but the vehicle hasn't physically left the lot yet. status goes
    # ACTIVE -> PENDING_PICKUP -> RELEASED (Confirm Picked Up). Distinct from
    # the RELEASED status set directly by Tina's sale/junk invoice flows, which
    # have no "customer picks it up" step and skip this entirely.
    pending_pickup_since = db.Column(db.DateTime)

    @property
    def pending_pickup_hours(self):
        if self.status != 'PENDING_PICKUP' or not self.pending_pickup_since:
            return None
        return int((datetime.utcnow() - self.pending_pickup_since).total_seconds() // 3600)

    @property
    def pending_pickup_overdue(self):
        """True once a Paid/Released vehicle has waited more than 48h for physical pickup."""
        hours = self.pending_pickup_hours
        return hours is not None and hours >= 48

    # Pre-computed by task_engine.recalculate_all — enables fast DB queries
    letter_urgency    = db.Column(db.String(10))    # RED | YELLOW | GREEN | NA
    current_task_num  = db.Column(db.Integer)       # 1, 2, 3, 4
    current_task_label = db.Column(db.String(100))
    current_task_due  = db.Column(db.Date)

    # UPS letter tracking stage — set by ups_tracking_attach.py
    letter_stage       = db.Column(db.String(50))   # needs_1st | in_transit | awaiting_2nd | returned_rts | address_issue | confirmed_both
    letter_flag        = db.Column(db.String(50))   # returned_rts | address_issue | NULL
    letter_flag_detail = db.Column(db.Text)

    status = db.Column(db.String(20), nullable=False, default='ACTIVE')
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)

    # Release / Tina pipeline sync
    possible_release = db.Column(db.Boolean, default=False)   # flagged missing from latest CSV
    base44_id = db.Column(db.String(100))                     # ID after push to Base44 Tina Tracker

    # Letter clock restart — impound_date is locked forever (never edited after
    # intake) and the 60-day title-eligibility clock always reads impound_date.
    # restart_date only re-anchors the LETTER deadline, e.g. Heather corrects a
    # bad address after an RTS/undeliverable and needs to resend the next letter.
    restart_date    = db.Column(db.Date)
    restart_reason  = db.Column(db.Text)
    restart_set_by  = db.Column(db.String(50))
    restart_set_at  = db.Column(db.DateTime)

    # Task backlog suppression — a Tim-level user can snooze a vehicle out of
    # the daily task queues (Heather's stoplight/BMV queue, Tina's handoff/title
    # pipeline) for a fixed number of days without touching its data or clock.
    # It reappears automatically once snoozed_until passes; no cron job needed
    # since every queue query just filters on the date.
    snoozed_until = db.Column(db.Date)
    snoozed_at    = db.Column(db.DateTime)
    snoozed_by    = db.Column(db.String(50))

    @property
    def is_snoozed(self):
        return self.snoozed_until is not None and self.snoozed_until >= date.today()

    @property
    def snooze_days_remaining(self):
        if not self.is_snoozed:
            return None
        return (self.snoozed_until - date.today()).days

    # Driver / VIN-snap mode — a driver photographs the VIN plate on the lot,
    # Claude Opus reads it, and the app logs a GPS-tagged zone snap. Distinct
    # from vin_door_jamb/dash (intake-time verification against the ticket);
    # this is ongoing "where is it parked" tracking used to catch ghost
    # vehicles that haven't been physically seen in a while.
    last_location_zone = db.Column(db.String(50))
    last_location_lat   = db.Column(db.Float)
    last_location_lng   = db.Column(db.Float)
    last_location_at    = db.Column(db.DateTime)
    last_location_by    = db.Column(db.String(100))

    @property
    def location_stale(self):
        """True when active and not location-snapped in 7+ days (or never)."""
        if self.last_location_at is None:
            return True
        return (datetime.utcnow() - self.last_location_at).days >= 7

    @classmethod
    def not_snoozed_filter(cls):
        """Filter expression excluding currently-snoozed vehicles from task queues.
        An expired or never-set snooze counts as not-snoozed."""
        return db.or_(cls.snoozed_until.is_(None), cls.snoozed_until < date.today())

    # Requesting police department for POLICE impounds — drives tow/storage
    # fee lookup on the letter templates. PPI impounds never set this; their
    # fees are the hardcoded PUCO caps (see effective_tow_rate below).
    police_department_id = db.Column(db.Integer, db.ForeignKey('police_departments.id'))
    police_department = db.relationship('PoliceDepartment', back_populates='vehicles')

    # PUCO fee caps for PPI impounds — hardcoded, unlike POLICE rates which
    # vary by department and live in the police_departments table.
    PPI_TOW_RATE = 129.00
    PPI_STORAGE_RATE = 17.00
    NOTIFICATION_FEE = 25.00  # flat per-letter fee cited on Owner/Lienholder notices

    @property
    def effective_tow_rate(self):
        if self.impound_type == 'PPI':
            return self.PPI_TOW_RATE
        if self.police_department and self.police_department.tow_rate is not None:
            return float(self.police_department.tow_rate)
        return None

    @property
    def effective_storage_rate(self):
        if self.impound_type == 'PPI':
            return self.PPI_STORAGE_RATE
        if self.police_department and self.police_department.storage_rate is not None:
            return float(self.police_department.storage_rate)
        return None

    @property
    def rate_pending(self):
        """True when a POLICE impound has no department (or no rate on that
        department) assigned — letters show '[RATE PENDING]' instead of a
        dollar figure, and Tim needs to manually assign/fix the department."""
        return self.impound_type == 'POLICE' and self.effective_tow_rate is None

    # ── Letter numbering scheme (5-letter system, added on top of the
    # original 2-letter PPI/POLICE pipeline) ───────────────────────────────
    # letter_number 1 and 2 are the ORIGINAL letters — task_engine.py and
    # title_eligible_date/stoplight_color/next_action_label below all read
    # letter_number 1/2 ONLY and are untouched by this system; only their
    # printed CONTENT changed (see CertifiedLetter.letter_kind):
    #   PPI:    1 = "1st Owner Notice" (Tim's "Letter 2"), 2 = "2nd Owner
    #           Notice" (Tim's "Letter 3") — same creation/due-date logic
    #           as always.
    #   POLICE: 1 = "Notice of Lien" (Tim's "Letter 1") — same creation/
    #           due-date logic as always.
    # letter_number 3-6 are NEW and only ever created by letter_triggers.py —
    # nothing in task_engine.py or the older due-date properties looks at
    # them, so they can't regress anything that already worked:
    #   3 = POLICE 1st Owner Notice (Tim's "Letter 2" for POLICE — PPI has no
    #       letter_number 3; its "Letter 2" is already letter_number 1)
    #   4 = POLICE 2nd Owner Notice (Tim's "Letter 3" for POLICE)
    #   5 = 1st Lienholder Notice, both impound types (Tim's "Letter 4") —
    #       created alongside whichever letter is that vehicle's "Letter 2"
    #       (letter_number 1 for PPI, letter_number 3 for POLICE)
    #   6 = 2nd Lienholder Notice, both impound types (Tim's "Letter 5") —
    #       created alongside whichever letter is that vehicle's "Letter 3"
    #       (letter_number 2 for PPI, letter_number 4 for POLICE)
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
    damage_reports = db.relationship(
        'DamageReport', back_populates='vehicle',
        order_by='DamageReport.created_at',
        cascade='all, delete-orphan'
    )
    notices = db.relationship(
        'VehicleNotice', back_populates='vehicle',
        order_by='VehicleNotice.notice_number',
        cascade='all, delete-orphan'
    )
    documents = db.relationship(
        'VehicleDocument', back_populates='vehicle',
        order_by='VehicleDocument.uploaded_at.desc()',
        cascade='all, delete-orphan'
    )
    charges = db.relationship(
        'VehicleCharge', back_populates='vehicle',
        order_by='VehicleCharge.charge_date.desc()',
        cascade='all, delete-orphan'
    )

    def __repr__(self):
        return f'<Vehicle {self.id}: {self.display_name}>'

    @classmethod
    def not_anomaly_filter(cls):
        """Filter expression excluding anomaly-flagged vehicles from staff queues."""
        return db.or_(cls.is_anomaly.is_(False), cls.is_anomaly.is_(None))

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
    def lka_document(self):
        """Most recently uploaded LKA document (documents are ordered newest-first)."""
        return next((d for d in self.documents if d.doc_type == 'LKA'), None)

    @property
    def title_search_document(self):
        """Most recently uploaded title search document."""
        return next((d for d in self.documents if d.doc_type == 'TITLE_SEARCH'), None)

    @property
    def latest_envelope_scan_with_image(self):
        """Most recent scanned return-envelope image on file, if any."""
        with_image = [s for s in self.envelope_scans if s.image_data]
        if not with_image:
            return None
        return max(with_image, key=lambda s: s.scan_date or datetime.min)

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
    def title_blocked_reason(self):
        """Why title_eligible_date is None — what Tina is waiting on before a date can even be projected."""
        if self.impound_type == 'PPI':
            l2 = self.letter2
            if not l2 or not l2.sent_date:
                return 'Waiting on Letter 2 to be sent'
        elif self.impound_type == 'POLICE':
            l1 = self.letter1
            if not l1 or not l1.sent_date:
                return 'Waiting on Letter 1 to be sent'
        return None

    @property
    def days_in_storage(self):
        return (date.today() - self.impound_date).days

    @property
    def letter_clock_start(self):
        """Anchor date for the next LETTER's due-date math (Letter 1/Notification,
        or Letter 2 once it's the pending one). Defaults to impound_date; overridden
        by restart_date after Heather restarts the clock (e.g. address corrected
        post-RTS). title_eligible_date always uses impound_date directly — never this."""
        return self.restart_date or self.impound_date

    @property
    def days_since_letter_clock_start(self):
        return (date.today() - self.letter_clock_start).days

    @property
    def next_action_label(self):
        if self.status != 'ACTIVE':
            return None
        today = date.today()
        l1 = self.letter1
        l2 = self.letter2

        if self.impound_type == 'PPI':
            if not l1 or not l1.sent_date:
                due = self.letter_clock_start + timedelta(days=PPI_LETTER1_DAYS)
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
                due = self.letter_clock_start + timedelta(days=POLICE_LETTER1_DAYS)
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
                due = self.letter_clock_start + timedelta(days=PPI_LETTER1_DAYS)
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
                due = self.letter_clock_start + timedelta(days=POLICE_LETTER1_DAYS)
                if today > due:
                    return 'red'
                elif (due - today).days <= 3:
                    return 'yellow'
                return 'green'
        return 'green'

    @property
    def file_complete_for_tina(self):
        """
        All four items must be physically present before handoff to Tina.
        ups_delivery_confirmed and return_receipt_filed are separate requirements
        (Heather: "RIGHT NOW THAT HAS TO BE 2 SEPARATE DOCS").
        has_tracking removed - CertifiedLetter tracking is not the send path;
        VehicleNotice handles UPS labels and those numbers live there, not here.
        """
        return (
            self.lka_document_confirmed and
            self.title_search_confirmed and
            self.ups_delivery_confirmed and
            self.return_receipt_filed
        )

    @property
    def total_owed(self):
        from titlebot.storage import calculate_storage
        _, storage_total, _ = calculate_storage(
            self.impound_date, date.today(), self.daily_storage_rate or 0
        )
        return (self.tow_fee or 0) + storage_total + self.additional_charges_total

    @property
    def total_storage_owed(self):
        from titlebot.storage import calculate_storage
        _, storage_total, _ = calculate_storage(
            self.impound_date, date.today(), self.daily_storage_rate or 0
        )
        return storage_total

    @property
    def additional_charges_total(self):
        """Sum of vehicle_charges — fees beyond standard tow/storage (admin
        fee, gate fee, key replacement, etc). Rolls into total_owed, so it's
        automatically reflected in letters and anywhere else that reads
        total_owed; the BMV title packet and standalone valuation report
        compute their own total separately and add this in directly (see
        titlebot/pdf_gen.py and app.py's valuation_report route)."""
        return sum(float(c.amount) for c in self.charges)


class CertifiedLetter(db.Model):
    __tablename__ = 'certified_letters'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    letter_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    sent_date = db.Column(db.Date)
    tracking_number = db.Column(db.String(50))
    delivery_confirmed_date = db.Column(db.Date)
    scheduled_delivery = db.Column(db.Date)
    ups_status = db.Column(db.String(50))
    return_to_sender = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)

    # Envelope Reference #2 field on the shipping label — freeform, but "AFO"
    # (Accident for Owner) is the one value the envelope scanner watches for
    # to auto-flag Vehicle.is_afo when a scanned return envelope shows it.
    reference_number_2 = db.Column(db.String(50))

    # 5-letter system fields — see the letter_number numbering note on
    # Vehicle above. recipient_type/letter_kind drive which print content
    # renders; letter_number alone still drives due-date/task_engine logic
    # exactly as before for numbers 1/2, and is otherwise just a sequence id.
    recipient_type = db.Column(db.String(20), default='owner')  # owner | lienholder
    letter_kind = db.Column(db.String(20))  # notice_of_lien | first_notice | second_notice

    vehicle = db.relationship('Vehicle', back_populates='letters')

    @property
    def effective_letter_kind(self):
        """letter_kind with a defensive fallback for any letter_number 1/2 row
        that somehow predates the run_migrations() backfill (which normally
        sets this on every boot) — infers the same way that backfill does,
        so the print template never renders a blank body."""
        if self.letter_kind:
            return self.letter_kind
        if self.letter_number == 1:
            return 'notice_of_lien' if self.vehicle.impound_type == 'POLICE' else 'first_notice'
        if self.letter_number == 2:
            return 'second_notice'
        return None

    @property
    def display_title(self):
        return {
            'notice_of_lien': 'NOTICE OF LIEN',
            'first_notice': 'FIRST NOTICE',
            'second_notice': 'SECOND NOTICE',
        }.get(self.effective_letter_kind)

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


class PoliceDepartment(db.Model):
    """Source of truth for POLICE-impound tow/storage/admin fee rates,
    looked up via Vehicle.police_department_id. PPI impounds never use this —
    their rates are the hardcoded PUCO caps on Vehicle."""
    __tablename__ = 'police_departments'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    tow_rate = db.Column(db.Numeric(10, 2))
    storage_rate = db.Column(db.Numeric(10, 2))
    admin_fee = db.Column(db.Numeric(10, 2))
    active = db.Column(db.Boolean, default=True)

    vehicles = db.relationship('Vehicle', back_populates='police_department')


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


class BMVScanHistory(db.Model):
    __tablename__ = 'bmv_scan_history'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    scan_type = db.Column(db.String(50))
    lka_data = db.Column(db.Text)
    title_data = db.Column(db.Text)
    comparison_flags = db.Column(db.Text)
    scanned_by = db.Column(db.String(100))
    scanned_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', backref=db.backref('bmv_scans', lazy='dynamic'))


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
    # Nullable so a scan the AI/staff couldn't match to any vehicle can still
    # be persisted (and shown in the /envelopes Unmatched tab) instead of
    # being silently dropped the way it was before the Envelope Tab build.
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=True)
    tracking_number = db.Column(db.String(50))
    scan_date = db.Column(db.DateTime, default=datetime.utcnow)
    image_path = db.Column(db.String(500))
    scan_notes = db.Column(db.Text)
    is_return_to_sender = db.Column(db.Boolean, default=False)
    is_delivered = db.Column(db.Boolean, default=False)
    delivery_date = db.Column(db.Date)
    claude_raw_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # DELIVERED | ADDRESS_ISSUE | SERVICE_DISRUPTED | UNKNOWN
    outcome = db.Column(db.String(20))
    matched_by = db.Column(db.String(20))  # reference_number | tracking_number | stock_number | owner_name | manual | unmatched

    # Reference #2 as read off the envelope by the scan AI — "AFO" here
    # auto-flags Vehicle.is_afo (see reference_number for Reference #1).
    reference_number_2 = db.Column(db.String(50))

    # Base64 data URL (data:image/jpeg;base64,...) of the scanned envelope,
    # captured at save time from the same image already sent to Opus for
    # reading. Stored as JPEG at reduced (~80%) quality rather than PNG/full
    # quality — envelope photos don't need print-grade fidelity, and at
    # 650+ vehicles with multiple scans each, full-quality images would bloat
    # the Postgres row size considerably. See camera capture code for the
    # actual compression setting.
    image_data = db.Column(db.Text)

    # Envelope Tab clear workflow — vehicle sold/released/or a manual reason
    # someone no longer needs this scan surfaced in the working Matched queue.
    # Cleared scans are never deleted, only hidden from Matched (kept forever
    # for audit).
    cleared_at = db.Column(db.DateTime)
    cleared_by = db.Column(db.String(50))
    clear_reason = db.Column(db.String(100))  # vehicle_sold | vehicle_released | manual

    # Set when staff explicitly discard an unmatched scan (e.g. a duplicate,
    # a misfire, junk mail) rather than link it to a vehicle. Distinct from
    # "cleared", which only applies to scans that did get matched.
    discarded = db.Column(db.Boolean, default=False)

    vehicle = db.relationship('Vehicle', back_populates='envelope_scans')

    @property
    def ai_read_summary(self):
        """Best-effort human-readable summary of what the AI extracted off
        the envelope, for display in the Unmatched tab where there's no
        vehicle record to show details from instead."""
        if not self.claude_raw_response:
            return self.scan_notes or None
        try:
            data = json.loads(self.claude_raw_response)
        except (ValueError, TypeError):
            return self.scan_notes or None
        parts = []
        if data.get('owner_name'):
            parts.append(f"Owner: {data['owner_name']}")
        if data.get('reference_number'):
            parts.append(f"Ref #: {data['reference_number']}")
        if data.get('tracking_number'):
            parts.append(f"Tracking: {data['tracking_number']}")
        if data.get('stock_number'):
            parts.append(f"Stock #: {data['stock_number']}")
        return '; '.join(parts) if parts else (self.scan_notes or None)


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


class SyncLog(db.Model):
    """One row per Towbook sync attempt (CSV upload or API auto-pull)."""
    __tablename__ = 'sync_log'

    id = db.Column(db.Integer, primary_key=True)
    sync_date = db.Column(db.Date, nullable=False, index=True)
    source = db.Column(db.String(20))   # csv_manual | api_auto | alert_pending
    status = db.Column(db.String(20))   # ok | error | pending
    inserted = db.Column(db.Integer, default=0)
    updated = db.Column(db.Integer, default=0)
    skipped = db.Column(db.Integer, default=0)
    call_count = db.Column(db.Integer, default=0)
    error_msg = db.Column(db.Text)
    triggered_by = db.Column(db.String(50))   # username or 'scheduler'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def summary(self):
        if self.status == 'ok':
            return f'{self.inserted} added, {self.updated} updated'
        if self.status == 'error':
            return self.error_msg or 'Unknown error'
        return 'Manual sync needed'


class DamageReport(db.Model):
    __tablename__ = 'damage_reports'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=True)
    call_number = db.Column(db.String(50))
    damage_type = db.Column(db.String(20))  # pre_existing | customer_claim
    description = db.Column(db.Text)
    owner_present = db.Column(db.Boolean, default=False)
    driver_name = db.Column(db.String(100))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    signature_data = db.Column(db.Text)   # base64 PNG data URL
    is_dispute = db.Column(db.Boolean, default=False)
    is_locked = db.Column(db.Boolean, default=False)
    pdf_data = db.Column(db.LargeBinary)
    submitted_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Claude Opus damage analysis
    ai_severity = db.Column(db.String(20))       # MINOR | MODERATE | SEVERE | TOTAL_LOSS
    ai_repair_cost_low = db.Column(db.Float)
    ai_repair_cost_high = db.Column(db.Float)
    ai_total_loss = db.Column(db.Boolean, default=False)
    ai_analysis = db.Column(db.Text)             # full JSON from Claude
    ai_analyzed_at = db.Column(db.DateTime)

    vehicle = db.relationship('Vehicle', back_populates='damage_reports')
    photos = db.relationship(
        'DamagePhoto', back_populates='report',
        order_by='DamagePhoto.sort_order',
        cascade='all, delete-orphan'
    )
    dots = db.relationship(
        'DamageDot', back_populates='report',
        order_by='DamageDot.sort_order',
        cascade='all, delete-orphan'
    )


class DamagePhoto(db.Model):
    __tablename__ = 'damage_photos'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('damage_reports.id'), nullable=False)
    image_data = db.Column(db.Text)   # base64 data URL (data:image/jpeg;base64,...)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    report = db.relationship('DamageReport', back_populates='photos')


class DamageDot(db.Model):
    __tablename__ = 'damage_dots'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('damage_reports.id'), nullable=False)
    x_pct = db.Column(db.Float)
    y_pct = db.Column(db.Float)
    label = db.Column(db.String(200))
    sort_order = db.Column(db.Integer, default=0)

    report = db.relationship('DamageReport', back_populates='dots')


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


# ── Chat Models ────────────────────────────────────────────────────────────────

class ChatThread(db.Model):
    __tablename__ = 'chat_threads'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    is_group = db.Column(db.Boolean, default=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship('ChatMessage', back_populates='thread',
                               order_by='ChatMessage.created_at',
                               cascade='all, delete-orphan')
    members = db.relationship('ChatThreadMember', back_populates='thread',
                              cascade='all, delete-orphan')
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def last_message(self):
        if self.messages:
            return self.messages[-1]
        return None

    @property
    def display_title(self):
        if self.title:
            return self.title
        names = [m.user.display_name or m.user.username
                 for m in self.members if m.user_id]
        return ', '.join(names[:3]) or 'Chat'


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey('chat_threads.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    username = db.Column(db.String(50))
    body = db.Column(db.Text, nullable=False)
    is_wally = db.Column(db.Boolean, default=False)
    alert_type = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    thread = db.relationship('ChatThread', back_populates='messages')
    user = db.relationship('User', foreign_keys=[user_id])


class ChatThreadMember(db.Model):
    __tablename__ = 'chat_thread_members'

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey('chat_threads.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_read_at = db.Column(db.DateTime)

    thread = db.relationship('ChatThread', back_populates='members')
    user = db.relationship('User')


class PushSubscription(db.Model):
    __tablename__ = 'push_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text)
    auth_key = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')


# ── UPS Vehicle Notices ────────────────────────────────────────────────────────

class VehicleNotice(db.Model):
    __tablename__ = 'vehicle_notices'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    notice_number = db.Column(db.Integer, default=1)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    tracking_number = db.Column(db.String(50))
    status = db.Column(db.String(30), default='sent')  # sent | delivered | returned
    label_data = db.Column(db.Text)  # base64 GIF label
    recipient_name = db.Column(db.String(100))
    recipient_address = db.Column(db.Text)
    recipient_city = db.Column(db.String(50))
    recipient_state = db.Column(db.String(2))
    recipient_zip = db.Column(db.String(10))
    sent_by = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', back_populates='notices')


# ── Vehicle Documents (LKA / Title Search PDFs) ─────────────────────────────────

class VehicleDocument(db.Model):
    __tablename__ = 'vehicle_documents'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    doc_type = db.Column(db.String(20), nullable=False)   # LKA | TITLE_SEARCH
    filename = db.Column(db.String(255))
    content_type = db.Column(db.String(100))
    file_data = db.Column(db.LargeBinary, nullable=False)
    uploaded_by = db.Column(db.String(50))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', back_populates='documents')

    @property
    def label(self):
        return 'LKA (BMV 2433)' if self.doc_type == 'LKA' else 'Title Search (BMV 1148)'


# ── Additional Charges ───────────────────────────────────────────────────────
# Free-text fees beyond standard tow/storage (admin fee, gate fee, key
# replacement, etc). Rolls into Vehicle.total_owed/additional_charges_total,
# which feeds letters, the BMV title packet, and the standalone valuation
# report.

class VehicleCharge(db.Model):
    __tablename__ = 'vehicle_charges'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id', ondelete='CASCADE'), nullable=False)
    label = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    charge_date = db.Column(db.Date, nullable=False, default=date.today)
    added_by = db.Column(db.String(50))
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    vehicle = db.relationship('Vehicle', back_populates='charges')


# ── Staff Feedback ───────────────────────────────────────────────────────────

class StaffFeedback(db.Model):
    __tablename__ = 'staff_feedback'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    username = db.Column(db.String(50))     # denormalized so it still shows if the user is later removed
    display_name = db.Column(db.String(100))
    body = db.Column(db.Text, nullable=False)
    page_url = db.Column(db.String(500))    # where they were when they hit the button
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    read_by = db.Column(db.String(50))
    read_at = db.Column(db.DateTime)

    user = db.relationship('User')
