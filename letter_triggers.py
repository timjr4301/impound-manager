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
from letter_engine import PPI_L1_DAYS, POLICE_L1_DAYS


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
    # A party's own 1st notice being sent opens THEIR own 2nd notice, on
    # their own sent date — never the registered owner's.
    if letter.party_id is not None:
        if vehicle.impound_type == 'PPI' and letter.letter_kind != 'second_notice':
            party = next((p for p in vehicle.parties if p.id == letter.party_id), None)
            if party is not None:
                ensure_party_letters(vehicle, party)
        return

    if vehicle.impound_type == 'PPI' and letter.letter_number == 1:
        due = letter.sent_date + timedelta(days=PPI_LETTER2_DAYS)
        _ensure_letter(vehicle, 2, 'second_notice', 'owner', due)
        if vehicle.lienholder_name:
            _ensure_letter(vehicle, 6, 'second_notice', 'lienholder', due)


# ── Extra-party letters (VehicleParty) ────────────────────────────────────
# Everyone beyond the registered owner and the lienholder of record gets
# their own letter chain, addressed to them by name, with their own due
# dates and their own tracking — added 08/24/2026 for Heather.
#
# THE NUMBERING RULE, and why it matters: letter_number is load-bearing.
# Vehicle.letter1/letter2, task_engine and letter_engine all resolve a letter
# by `letter_number == 1` / `== 2`, and title_eligible_date hangs off
# letter1. If a party letter reused number 1 it could be picked up as THE
# Letter 1 and silently move the title clock. So party letters are allocated
# in a band starting at 101, which every existing `== 1` / `in [3,4,5,6]`
# check already excludes. Their real identity is party_id; the number is just
# a unique ordering key.
PARTY_LETTER_BAND_START = 101


def _next_party_letter_number(vehicle):
    highest = max([l.letter_number or 0 for l in vehicle.letters] or [0])
    return max(highest, PARTY_LETTER_BAND_START - 1) + 1


def _party_first_notice_due(vehicle):
    """Same deadline the registered owner's 1st notice gets — a party found
    late still inherits the vehicle's own clock, not today's date."""
    days = PPI_L1_DAYS if vehicle.impound_type == 'PPI' else POLICE_L1_DAYS
    return vehicle.letter_clock_start + timedelta(days=days)


def _party_letter(vehicle, party, second):
    """That party's own non-superseded first or second notice, if it exists."""
    return next((l for l in vehicle.letters
                 if l.party_id == party.id
                 and not l.superseded
                 and (l.letter_kind == 'second_notice') == second), None)


def _create_party_letter(vehicle, party, kind, due_date):
    letter = CertifiedLetter(
        vehicle_id=vehicle.id,
        party_id=party.id,
        letter_number=_next_party_letter_number(vehicle),
        letter_kind=kind,
        recipient_type='lienholder' if party.notice_class == 'lienholder' else 'owner',
        due_date=due_date,
        created_at=datetime.utcnow(),
    )
    db.session.add(letter)
    vehicle.letters.append(letter)
    return letter


def ensure_party_letters(vehicle, party):
    """Create whichever letters this party is currently owed. Idempotent —
    safe to call on every party save. Returns the letters it created.

    Mirrors the chain that matches the party's notice_class:
      • PPI    -> 1st notice now, 2nd notice once their own 1st is sent
      • POLICE -> one Notice of Lien, matching COMPLIANCE-TRUTH item 3

    Never touches the registered owner's letters, dates, or round.
    """
    created = []
    if party is None or party.is_primary:
        return created
    if not party.send_letters or not party.name:
        return created

    first_kind = 'notice_of_lien' if vehicle.impound_type == 'POLICE' else 'first_notice'
    first = _party_letter(vehicle, party, second=False)
    if first is None:
        first = _create_party_letter(vehicle, party, first_kind, _party_first_notice_due(vehicle))
        created.append(first)

    # POLICE is a single letter — its 60-day claim window IS the whole clock,
    # so there is nothing for a 2nd notice to escalate to.
    if vehicle.impound_type == 'PPI' and first.sent_date:
        if _party_letter(vehicle, party, second=True) is None:
            due = first.sent_date + timedelta(days=PPI_LETTER2_DAYS)
            created.append(_create_party_letter(vehicle, party, 'second_notice', due))

    return created


def ensure_all_party_letters(vehicle):
    """ensure_party_letters for every extra party on the vehicle."""
    created = []
    for party in list(vehicle.parties):
        created.extend(ensure_party_letters(vehicle, party))
    return created
