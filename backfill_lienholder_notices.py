"""
One-time backfill: open the missing lienholder Notice of Lien (letter_number 5)
for vehicles that already have a lienholder of record but no letter.

*** NOT RUN. TIM DECIDED 2026-08-31 NOT TO BACKFILL — FORWARD-ONLY. ***

    Do not run --apply without asking him again. The trigger fix that ships
    alongside this script handles every vehicle from here on; the 54 vehicles
    that were already missing a notice at the time of that decision
    (POLICE 9 + PPI 45, counted live 2026-08-31) were deliberately left alone.

    The script is kept, unrun, for two reasons: the decision may change, and
    its DRY RUN mode (no --apply) writes nothing at all — it only prints the
    list. That is a safe way to SEE which vehicles are affected, including the
    ones where a title was already filed without the lienholder ever being
    noticed, without mailing anybody.

WHY THESE EXIST
---------------
A lienholder is almost never known at intake — it comes back FROM the BMV
search. letter_triggers.on_vehicle_created() only runs at intake, and
on_bmv_complete(), the one hook that runs at the moment the lienholder IS
known, was a no-op. So nothing ever opened letter 5 for a lienholder found
during the BMV search, on PPI or POLICE. Nothing errored; the letter simply
never existed. Found 2026-08-31 on vehicle 11374 (Wells Fargo Dealer Services,
"No letters opened yet") after the 08/25 parties build made per-party letter
status visible on the vehicle page.

The trigger is fixed for everything going forward. This script covers the
vehicles it already happened to.

WHAT IT DOES, AND DOESN'T
-------------------------
Creates a DUE letter only. It never sets sent_date, never touches a tracking
number, never mails anyone — it puts the letter on Heather's board so a human
sends it. Re-running it is safe: a vehicle that already has an active letter 5
is skipped.

Due dates are the vehicle's OWN clock (impound + 5 PPI / + 10 POLICE), not
today, so an old car lands ALREADY OVERDUE on the letter queue. That is
deliberate and correct — the notice really is late — but it means a wave of
red arrives on Heather's board the moment this runs. Expect that.

Skipped and reported, never auto-created:
  • letter_hold        — pipeline deliberately parked (boats, old trailers)
  • possible_release   — may already be gone; verify before mailing
  • status TITLE_FILED — title already applied for. Compliance-wise these are
                         the WORST ones (notice missed AND title filed), so
                         they are listed loudly for Tim rather than quietly
                         queued behind a decision nobody made.

Dry-run by default (prints what WOULD change). Add --apply to commit.

    [RENDER SHELL] python3 backfill_lienholder_notices.py           # preview
    [RENDER SHELL] python3 backfill_lienholder_notices.py --apply   # write
"""
import sys
from datetime import date, datetime

from app import app
from models import db, Vehicle, VehicleNote
import letter_triggers


def _has_active_letter5(vehicle):
    return any(l.letter_number == 5 and not l.superseded for l in vehicle.letters)


def _needs_notice(vehicle):
    return bool((vehicle.lienholder_name or '').strip()) and not _has_active_letter5(vehicle)


def main():
    apply = '--apply' in sys.argv
    today = date.today()

    with app.app_context():
        candidates = (Vehicle.query
                      .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
                      .order_by(Vehicle.impound_date.asc())
                      .all())

        to_create, held, maybe_released, already_filed = [], [], [], []
        for v in candidates:
            if not _needs_notice(v):
                continue
            if v.status == 'TITLE_FILED':
                already_filed.append(v)
            elif v.letter_hold:
                held.append(v)
            elif v.possible_release:
                maybe_released.append(v)
            else:
                to_create.append(v)

        print(f'Scanned {len(candidates)} ACTIVE/TITLE_FILED vehicles.\n')

        if already_filed:
            print(f'!! {len(already_filed)} vehicle(s) have a lienholder, NO notice, and the '
                  f'title is ALREADY FILED — Tim needs to see these:')
            for v in already_filed:
                print(f'     #{v.id:<7} {v.display_name:<34} {v.impound_type:<7} '
                      f'lienholder: {v.lienholder_name}')
            print('   (not touched by this script — decide these one at a time)\n')

        for label, bucket in (('on letter hold', held), ('flagged possible-release', maybe_released)):
            if bucket:
                print(f'-- Skipped, {label}: {len(bucket)} '
                      f'({", ".join("#%d" % v.id for v in bucket[:12])}'
                      f'{" ..." if len(bucket) > 12 else ""})')
        if held or maybe_released:
            print()

        if not to_create:
            print('No missing lienholder notices to open. Nothing to do.')
            return

        by_type = {}
        for v in to_create:
            by_type[v.impound_type] = by_type.get(v.impound_type, 0) + 1
        breakdown = ', '.join(f'{k}: {n}' for k, n in sorted(by_type.items()))
        print(f'{len(to_create)} lienholder notice(s) to open  ({breakdown})\n')

        overdue = 0
        for v in to_create:
            due = letter_triggers._first_notice_due(v)
            late = (today - due).days
            if late > 0:
                overdue += 1
            flag = f'OVERDUE by {late}d' if late > 0 else f'due {due:%m/%d/%Y}'
            print(f'  #{v.id:<7} {v.display_name:<34} {v.impound_type:<7} '
                  f'{v.lienholder_name[:28]:<28} {flag}')

        print(f'\n{overdue} of {len(to_create)} will land already overdue on the letter queue.')

        if not apply:
            print('\nDRY RUN — nothing written. Re-run with --apply to commit.')
            return

        created = 0
        for v in to_create:
            letter = letter_triggers.ensure_lienholder_notice(v)
            if letter is None:
                continue
            created += 1
            db.session.add(VehicleNote(
                vehicle_id=v.id,
                body=(f'Lienholder Notice of Lien opened by backfill '
                      f'({v.lienholder_name}) — required notice had never been '
                      f'created because on_bmv_complete did not open it.'),
                author='System',
                created_at=datetime.utcnow(),
            ))
        db.session.commit()
        print(f'\nCommitted - {created} lienholder notice(s) opened.')


if __name__ == '__main__':
    main()
