"""
WP-3 (one-time): recover registered-owner name/address for pre-2026-07-30
POLICE vehicles whose BMV Search results only ever got typed into the old
free-text bmv_search_notes box. Those vehicles' Notice of Lien still prints
the "[REGISTERED OWNER NAME]" placeholder because print/letter.html reads
the structured owner_name/owner_address/owner_city/owner_state/owner_zip
columns, which the pre-PR#19 BMV Done modal never wrote (PR #19, 2026-07-30,
added the structured inputs; see MASTER_CONTEXT.md).

Parses ONLY a specific, recognizable shape: the notes' last non-blank line
must read as "City, ST ZIP" (or "City ST ZIP"). Everything above that line
is the name (+ street address, if there are two lines above it). Anything
that doesn't match this shape, or whose "name" line looks like a phrase
rather than a person's name (contains a digit, or is a common non-answer
like "no record"/"unknown"/"n/a"/"pending"), is left for hand entry rather
than guessed — a wrong name on a legal notice is worse than a placeholder.

Never overwrites a field that already has a value. Writes a VehicleNote
audit line on every vehicle actually changed.

Dry-run by default (prints what WOULD change, plus every notes string this
script could NOT parse, so you can see exactly what it's working with).
Add --apply to commit.

    [RENDER SHELL] python3 backfill_police_owner_from_notes.py           # preview
    [RENDER SHELL] python3 backfill_police_owner_from_notes.py --apply   # write
"""
import re
import sys
from datetime import datetime

from app import app
from models import db, Vehicle, VehicleNote

_CITY_STATE_ZIP_RE = re.compile(
    r'^(?P<city>.+?),?\s+(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(-\d{4})?)\s*$'
)
_NOT_A_NAME_RE = re.compile(
    r'\d|no record|unknown|n/?a\b|pending|see attached|same as|tbd|none found',
    re.IGNORECASE,
)


def parse_owner_from_notes(notes):
    """Return (name, address, city, state, zip) on a confident parse, else None."""
    if not notes:
        return None
    lines = [l.strip() for l in notes.splitlines() if l.strip()]
    if not lines:
        return None

    m = _CITY_STATE_ZIP_RE.match(lines[-1])
    if not m:
        return None
    city, state, zip_ = m.group('city').strip(), m.group('state').upper(), m.group('zip')

    above = lines[:-1]
    if not above or len(above) > 2:
        return None  # nothing before city/state/zip, or too many lines to be confident

    if len(above) == 2:
        name, address = above[0], above[1]
    else:
        # One line above city/state/zip: "Name, Street" splits on the first
        # comma; a bare line with no comma is treated as name-only (address
        # left blank rather than guessed).
        if ',' in above[0]:
            name, address = (p.strip() for p in above[0].split(',', 1))
        else:
            name, address = above[0], None

    if not name or _NOT_A_NAME_RE.search(name):
        return None

    return name, address, city, state, zip_


def main():
    apply = '--apply' in sys.argv
    with app.app_context():
        candidates = (
            Vehicle.query
            .filter(Vehicle.impound_type == 'POLICE')
            .filter(Vehicle.bmv_search_notes.isnot(None))
            .filter(Vehicle.bmv_search_notes != '')
            .filter((Vehicle.owner_name.is_(None)) | (Vehicle.owner_name == ''))
            .order_by(Vehicle.id)
            .all()
        )

        parsed, unparseable = [], []
        for v in candidates:
            result = parse_owner_from_notes(v.bmv_search_notes)
            if result:
                parsed.append((v, result))
            else:
                unparseable.append(v)

        if not candidates:
            print('No POLICE vehicles found with notes-only owner info (owner_name already '
                  'blank + bmv_search_notes populated). Nothing to do.')
            return

        header = 'CHANGE' if apply else 'WOULD CHANGE'
        print(f'{header} ({len(parsed)} of {len(candidates)} candidates parsed):')
        now = datetime.utcnow()
        for v, (name, address, city, state, zip_) in parsed:
            addr_str = f', {address}' if address else ''
            print(f'  #{v.id:<6} {str(v.stock_number or "-"):<10} -> {name}{addr_str}, {city} {state} {zip_}')
            if apply:
                v.owner_name = name
                if address:
                    v.owner_address = address
                v.owner_city = city
                v.owner_state = state
                v.owner_zip = zip_
                v.updated_at = now
                db.session.add(VehicleNote(
                    vehicle_id=v.id,
                    body=(f'WP-3 backfill: owner info recovered from BMV search notes '
                          f'("{v.bmv_search_notes}") -> {name}{addr_str}, {city} {state} {zip_}.'),
                    author='WP-3 backfill script',
                    created_at=now,
                ))

        print(f'\n{len(unparseable)} vehicle(s) could NOT be parsed — hand entry needed:')
        for v in unparseable:
            note_preview = (v.bmv_search_notes or '')[:120]
            print(f'  #{v.id:<6} {str(v.stock_number or "-"):<10} notes: {note_preview!r}')

        print(f'\n{len(parsed)} parsed / {len(unparseable)} unparseable / '
              f'{len(candidates)} total candidates.')

        if apply:
            db.session.commit()
            print('Committed.')
        else:
            print('Re-run with --apply to write these changes.')


if __name__ == '__main__':
    main()
