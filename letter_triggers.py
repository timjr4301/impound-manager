"""
Extended letter trigger logic.

COMPLIANCE-TRUTH.md item 3, confirmed by Tim 2026-07-31: POLICE impounds
get ONE letter (the Notice of Lien, letter_number=1) — not the 1->3->4
owner-notice chain this module used to build. That chain was generated and
sendable but had zero effect on title eligibility or the Task Pipeline
(both only ever read letter_number=1 for POLICE), so removing it changes
nothing about compliance timing — it only stops generating letters nobody
was relying on. on_bmv_complete() is now a no-op kept for the existing
call site in blueprints/heather.py; on_letter_sent()'s POLICE branch is
removed outright since letter_number=3 will never exist to trigger it.

PPI's lienholder notices (letter_number 5/6) are untouched — letter_number
1 and 2 keep their ORIGINAL creation logic completely unchanged.
"""
from datetime import datetime, timedelta

from models import db, CertifiedLetter, PPI_LETTER2_DAYS


def _ensure_letter(vehicle, letter_number, kind, recipient_type, due_date):
    """Create the letter if an active (non-superseded) row with this
    letter_number doesn't already exist for this vehicle. Idempotent."""
    existing = next((l for l in vehicle.letters if l.letter_number == letter_number and not l.superseded), None)
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


# Public alias — the Generate Letters hub (app.py) resolves a letter slug to a
# CertifiedLetter and needs the same canonical numbering/idempotency this
# module already owns, rather than duplicating the numbering scheme.
ensure_letter = _ensure_letter


def on_vehicle_created(vehicle, letter1_due):
    """Call right after a new vehicle's initial letter_number=1 is created
    (app.py's vehicles_new route). Only PPI needs anything extra here —
    POLICE is one letter only (item 3), nothing further to create."""
    if vehicle.impound_type == 'PPI' and vehicle.lienholder_name:
        _ensure_letter(vehicle, 5, 'first_notice', 'lienholder', letter1_due)


def on_bmv_complete(vehicle):
    """No-op as of COMPLIANCE-TRUTH.md item 3 (2026-07-31) — POLICE gets one
    letter only, so BMV completion no longer creates anything extra. Kept
    (rather than removed) only because blueprints/heather.py's bmv_complete
    route still calls it; safe to delete entirely in a later cleanup."""
    return


def on_letter_sent(vehicle, letter):
    """Call right after a CertifiedLetter is marked sent (app.py's
    letters_mark_sent route). PPI only, as of COMPLIANCE-TRUTH.md item 3 —
    POLICE's letter_number=3 will never exist to trigger a second notice."""
    if vehicle.impound_type == 'PPI' and letter.letter_number == 1:
        due = letter.sent_date + timedelta(days=PPI_LETTER2_DAYS)
        _ensure_letter(vehicle, 2, 'second_notice', 'owner', due)
        if vehicle.lienholder_name:
            _ensure_letter(vehicle, 6, 'second_notice', 'lienholder', due)
