"""
Extended letter trigger logic — the 5-letter system.

Creates the newer letter_number 3-6 records (POLICE owner notices for
letter_number 1/2 not covered, plus lienholder notices for both impound
types) at the right moments. letter_number 1 and 2 keep their ORIGINAL
creation logic and meaning completely untouched — task_engine.py and
Vehicle.title_eligible_date/stoplight_color/next_action_label only ever
read letter_number 1/2, so nothing here can affect them. See the full
numbering scheme documented on Vehicle in models.py.
"""
from datetime import date, datetime, timedelta

from models import db, CertifiedLetter, PPI_LETTER2_DAYS


def _ensure_letter(vehicle, letter_number, kind, recipient_type, due_date):
    """Create the letter if a row with this letter_number doesn't already
    exist for this vehicle. Idempotent — safe to call on every relevant
    event without checking first."""
    existing = next((l for l in vehicle.letters if l.letter_number == letter_number), None)
    if existing:
        return existing
    letter = CertifiedLetter(
        vehicle_id=vehicle.id,
        letter_number=letter_number,
        letter_kind=kind,
        recipient_type=recipient_type,
        due_date=due_date,
        created_at=datetime.utcnow(),
    )
    db.session.add(letter)
    return letter


def on_vehicle_created(vehicle, letter1_due):
    """Call right after a new vehicle's initial letter_number=1 is created
    (app.py's vehicles_new route). Only PPI needs anything extra here —
    POLICE's lienholder 1st notice (letter_number=5) isn't triggered until
    BMV search completes, same as its owner counterpart (letter_number=3)."""
    if vehicle.impound_type == 'PPI' and vehicle.lienholder_name:
        _ensure_letter(vehicle, 5, 'first_notice', 'lienholder', letter1_due)


def on_bmv_complete(vehicle):
    """Call when Heather marks Task 1 (BMV Search) complete (heather.py's
    bmv_complete route). POLICE impounds only — this is what unlocks their
    1st Owner/Lienholder Notice, since letter_number=1 for POLICE is already
    spoken for by the Notice of Lien."""
    if vehicle.impound_type != 'POLICE':
        return
    today = date.today()
    _ensure_letter(vehicle, 3, 'first_notice', 'owner', today)
    if vehicle.lienholder_name:
        _ensure_letter(vehicle, 5, 'first_notice', 'lienholder', today)


def on_letter_sent(vehicle, letter):
    """Call right after a CertifiedLetter is marked sent (app.py's
    letters_mark_sent route), to create whichever next letter(s) in the
    5-letter sequence this send unlocks."""
    if vehicle.impound_type == 'PPI' and letter.letter_number == 1:
        due = letter.sent_date + timedelta(days=PPI_LETTER2_DAYS)
        _ensure_letter(vehicle, 2, 'second_notice', 'owner', due)
        if vehicle.lienholder_name:
            _ensure_letter(vehicle, 6, 'second_notice', 'lienholder', due)
    elif vehicle.impound_type == 'POLICE' and letter.letter_number == 3:
        due = letter.sent_date + timedelta(days=PPI_LETTER2_DAYS)  # same 30-day gap as PPI's 2nd notice
        _ensure_letter(vehicle, 4, 'second_notice', 'owner', due)
        if vehicle.lienholder_name:
            _ensure_letter(vehicle, 6, 'second_notice', 'lienholder', due)
