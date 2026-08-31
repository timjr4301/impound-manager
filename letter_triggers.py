"""
Extended letter trigger logic.

COMPLIANCE-TRUTH.md item 3, confirmed by Tim 2026-07-31: POLICE impounds
get ONE letter (the Notice of Lien, letter_number=1) — not the 1->3->4
owner-notice chain this module used to build. That chain was generated and
sendable but had zero effect on title eligibility or the Task Pipeline
(both only ever read letter_number=1 for POLICE), so removing it changes
nothing about compliance timing — it only stops generating letters nobody
was relying on. on_letter_sent()'s POLICE branch is removed outright since
letter_number=3 will never exist to trigger it.

PPI's lienholder notices (letter_number 5/6) are untouched — letter_number
1 and 2 keep their ORIGINAL creation logic completely unchanged.

REVISED 2026-08-31 — on_bmv_complete() is no longer a no-op. Item 3's
"one letter only" reading was already reversed for lienholders on
2026-08-04 (Tim, real case: 2022 Dodge Charger / Ally Financial): the
lienholder of record gets their own separately sent, separately tracked
notice on POLICE too. But nothing OPENED it. on_vehicle_created() only
fires at intake, where a lienholder is almost never known yet — it comes
back from the BMV search — and on_bmv_complete(), the one hook that runs
at the moment the lienholder IS known, did nothing. So a lienholder found
during the BMV search never got a letter, on either impound type. That is
fixed here; backfill_lienholder_notices.py covers the vehicles it already
happened to.
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


def _first_notice_due(vehicle):
    """The vehicle's own 1st-notice deadline. A recipient discovered late —
    a lienholder that only surfaced in the BMV search, say — inherits the
    vehicle's clock, not today's date."""
    days = PPI_L1_DAYS if vehicle.impound_type == 'PPI' else POLICE_L1_DAYS
    return vehicle.letter_clock_start + timedelta(days=days)


def _lienholder_notice_kind(vehicle):
    """POLICE's single Notice of Lien vs PPI's 1st notice — the lienholder of
    record gets the same content as the owner, addressed to them."""
    return 'notice_of_lien' if vehicle.impound_type == 'POLICE' else 'first_notice'


def ensure_lienholder_notice(vehicle):
    """Open the lienholder of record's notice (letter_number 5) if they need
    one. Idempotent, and it only ever CREATES a due letter — it never marks
    anything sent, so calling it again mails nobody twice.

    Returns the letter if this call created one, else None.
    """
    if not (vehicle.lienholder_name or '').strip():
        return None
    if vehicle.letter_hold:
        return None
    # Query rather than scan vehicle.letters: this runs from a backfill and
    # from bmv_complete's mid-request flush, where an already-loaded
    # collection can be stale, and a stale read here means a duplicate
    # envelope in someone's mail.
    existing = (CertifiedLetter.query
                .filter_by(vehicle_id=vehicle.id, letter_number=5)
                .filter(CertifiedLetter.superseded.isnot(True))
                .first())
    if existing is not None:
        return None
    letter = CertifiedLetter(
        vehicle_id=vehicle.id,
        letter_number=5,
        letter_kind=_lienholder_notice_kind(vehicle),
        recipient_type='lienholder',
        due_date=_first_notice_due(vehicle),
        created_at=datetime.utcnow(),
    )
    vehicle.letters.append(letter)
    db.session.add(letter)
    return letter


def on_vehicle_created(vehicle, letter1_due):
    """Call right after a new vehicle's initial letter_number=1 is created
    (the vehicles_new route, towbook_import and towbook_api all call this).

    The lienholder of record gets their own notice on BOTH impound types as of
    2026-08-31 — POLICE was excluded here on the old "POLICE is one letter
    only" reading (item 3), which 2026-08-04 already reversed for lienholders.

    In practice this branch rarely fires: a lienholder is usually not known at
    intake, it comes back FROM the BMV search. That case is on_bmv_complete's.
    """
    if vehicle.lienholder_name:
        _ensure_letter(vehicle, 5, _lienholder_notice_kind(vehicle),
                       'lienholder', letter1_due)


def on_bmv_complete(vehicle):
    """Open the lienholder of record's Notice of Lien once the BMV search
    names one.

    THIS BEING A NO-OP WAS THE BUG (found 2026-08-31). A lienholder is almost
    never known at intake — it comes back FROM the BMV search — so
    on_vehicle_created's `if vehicle.lienholder_name` check was False at the
    only moment it ever ran, and nothing else opened letter 5. A lienholder
    discovered during the BMV search silently never got their required notice,
    on PPI and POLICE alike. Nothing errored; the letter just never existed.
    Surfaced on vehicle 11374 (Wells Fargo Dealer Services, "No letters opened
    yet") once the 08/25 parties build made per-party letter status visible.

    Policy is not in question — Tim settled it 2026-08-04 on a real case (2022
    Dodge Charger / Ally Financial): the lienholder of record gets their own
    separately sent, separately tracked notice, letter_number 5, addressed to
    them. See LETTER_SLUGS in app.py.

    Idempotent. Creates a DUE letter only — never marks anything sent — so a
    re-run cannot mail anyone twice.
    """
    return ensure_lienholder_notice(vehicle)


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
    return _first_notice_due(vehicle)


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
