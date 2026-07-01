#!/usr/bin/env python3
"""
generate_letter1_backfill.py

One-time backfill: creates a Letter 1 CertifiedLetter row for every active
vehicle that doesn't already have any certified_letters. Due date is computed
the same way the manual "Add Vehicle" form does it (app.py: vehicles_new):

    due_date = impound_date + (PPI_LETTER1_DAYS or POLICE_LETTER1_DAYS)

This only creates data derivable from existing vehicle records (impound_date,
impound_type). It does NOT set sent_date / delivery_confirmed_date / tracking
data — that history only exists in Towbook and must come from
towbook_letter_backfill.py. Vehicles that already have any certified_letters
are left untouched.

--- How to run on Render Shell ---
    python3 generate_letter1_backfill.py

--- How to run locally ---
    python3 generate_letter1_backfill.py
"""
from datetime import timedelta, datetime

from app import app
from models import (db, Vehicle, CertifiedLetter,
                     PPI_LETTER1_DAYS, POLICE_LETTER1_DAYS)


def main():
    with app.app_context():
        vehicles = (
            Vehicle.query
            .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
            .filter(~Vehicle.letters.any())
            .all()
        )
        print(f'Vehicles with no certified_letters: {len(vehicles)}')

        created = 0
        skipped_missing_data = 0

        for v in vehicles:
            if not v.impound_date or not v.impound_type:
                skipped_missing_data += 1
                continue

            letter1_days = PPI_LETTER1_DAYS if v.impound_type == 'PPI' else POLICE_LETTER1_DAYS
            letter1_due = v.impound_date + timedelta(days=letter1_days)

            db.session.add(CertifiedLetter(
                vehicle_id=v.id,
                letter_number=1,
                due_date=letter1_due,
                created_at=datetime.utcnow(),
            ))
            created += 1

            if created % 250 == 0:
                db.session.commit()

        db.session.commit()

        print('=' * 50)
        print(f'  LETTER 1 ROWS CREATED   : {created}')
        print(f'  SKIPPED (missing data)  : {skipped_missing_data}')
        print('=' * 50)


if __name__ == '__main__':
    main()
