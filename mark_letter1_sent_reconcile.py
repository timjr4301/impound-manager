"""
One-time reconciliation: mark every PENDING 1st Notice Letter as SENT.

During a July 2026 site outage Heather could not get into the app, so all the
1st letters for that period were mailed manually "the old way." The app still
shows them as unsent, cluttering her board. This clears that backlog so she
starts fresh — 0 first-letters-waiting — after which the daily Towbook upload
tracks new ones normally.

SAFETY — this deliberately does the MINIMUM:
  * Sets ONLY `sent_date` on `certified_letters` rows with letter_number = 1.
  * Sent date = the day AFTER the vehicle was impounded, rolled forward to the
    next weekday (a Fri/Sat/Sun impound -> the following Monday) — B&J's real
    practice. Never stamped later than today.
  * Does NOT create, modify, or touch any 2nd letter (letter_number 2/4/6).
  * Does NOT call letter_triggers, does NOT set delivery/return-to-sender — so
    the 2nd-letter clock never starts (task_engine leaves these "Awaiting
    Delivery", GREEN/locked). Second letters are left completely as-is.
  * Only ACTIVE, non-ghost vehicles; skips already-sent and superseded letters.
  * Writes an audit VehicleNote on each vehicle so the record shows WHY the
    letter is marked sent.

Bypasses the BMV sequential gate on purpose — these letters were physically
mailed during the outage regardless of the in-app BMV flag.

Dry-run by default (prints what WOULD change). Add --apply to commit.

    [RENDER SHELL] python3 mark_letter1_sent_reconcile.py           # preview count
    [RENDER SHELL] python3 mark_letter1_sent_reconcile.py --apply   # write
"""
import sys
from datetime import datetime, date, timedelta

from app import app
from models import db, Vehicle, CertifiedLetter, VehicleNote


def sent_date_for(impound_date, today):
    """Day after impound, rolled to the next weekday (Fri/Sat/Sun -> Monday).
    Never returns a date later than today."""
    d = impound_date + timedelta(days=1)
    while d.weekday() >= 5:      # 5 = Saturday, 6 = Sunday
        d += timedelta(days=1)
    return min(d, today)


def main():
    apply = '--apply' in sys.argv
    today = date.today()
    with app.app_context():
        pairs = (
            db.session.query(CertifiedLetter, Vehicle)
            .join(Vehicle, Vehicle.id == CertifiedLetter.vehicle_id)
            .filter(CertifiedLetter.letter_number == 1)
            .filter(CertifiedLetter.sent_date.is_(None))
            .filter(CertifiedLetter.superseded.isnot(True))
            .filter(Vehicle.status == 'ACTIVE')
            .filter(Vehicle.possible_release.isnot(True))
            .order_by(CertifiedLetter.vehicle_id)
            .all()
        )

        if not pairs:
            print('No pending 1st letters found — Heather\'s board is already clean.')
            return

        print(f'{"APPLYING" if apply else "DRY-RUN (no changes)"} — '
              f'{len(pairs)} pending 1st letters:\n')
        now = datetime.utcnow()
        for letter, v in pairs:
            desc = (v.display_name or '')[:34]
            sent = sent_date_for(v.impound_date, today)
            print(f'  #{v.id:<6} {str(v.stock_number or "-"):<10} {desc:<34} '
                  f'impounded {v.impound_date} -> sent {sent}')
            if apply:
                letter.sent_date = sent
                if not v.task_2_letter_completed_at:
                    v.task_2_letter_completed_at = now
                db.session.add(VehicleNote(
                    vehicle_id=v.id,
                    body=('1st Notice Letter marked SENT (' + str(sent) + ') via '
                          'July-outage reconciliation — mailed manually the old way '
                          '(day after impound) while the site was down. No 2nd '
                          'letter created; 2nd-letter clock not started.'),
                    author='System (reconcile)',
                    created_at=now,
                ))

        print(f'\n{len(pairs)} first letters '
              f'{"MARKED SENT." if apply else "would be marked sent (dry-run)."}')
        if apply:
            db.session.commit()
            print('Committed. Heather\'s 1st-letter queue is now clear.')
        else:
            print('Re-run with --apply to write these changes.')


if __name__ == '__main__':
    main()
