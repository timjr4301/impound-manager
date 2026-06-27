from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timedelta

db = SQLAlchemy()

# Ohio BMV title-by-abandonment deadlines
PPI_LETTER1_DAYS = 5        # Letter 1 must be sent within 5 days of impound
PPI_LETTER2_DAYS = 30       # Letter 2 sent 30 days after Letter 1
PPI_TITLE_FROM_IMPOUND = 60 # Must be 60 days since impound
PPI_TITLE_FROM_LETTER2 = 30 # Must be 30 days since Letter 2

POLICE_LETTER1_DAYS = 10    # Notification required within 10 days (ORC 4513.61)
POLICE_TITLE_FROM_LETTER1 = 30


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

    # Last registered owner — used for certified letter addressing
    owner_name = db.Column(db.String(100))
    owner_address = db.Column(db.Text)

    status = db.Column(db.String(20), nullable=False, default='ACTIVE')  # ACTIVE, RELEASED, TITLE_FILED
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

    def __repr__(self):
        return f'<Vehicle {self.id}: {self.display_name}>'

    @property
    def display_name(self):
        parts = [str(self.year) if self.year else None, self.make, self.model_name]
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


class CertifiedLetter(db.Model):
    __tablename__ = 'certified_letters'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False)
    letter_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    sent_date = db.Column(db.Date)
    tracking_number = db.Column(db.String(50))
    delivery_confirmed_date = db.Column(db.Date)
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
        """Tracking number stripped of spaces/hyphens for matching."""
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
