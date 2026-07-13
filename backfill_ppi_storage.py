"""
One-time backfill: set each ACTIVE PPI vehicle's daily storage rate to its
weight-class default, per the PUCO schedule now driven by vehicle_class:

    light  $22/day
    medium $37/day
    heavy  $82/day

Existing PPI tickets keep whatever rate they were imported/entered with until
this runs; after running, each active PPI vehicle's stored daily rate (which
feeds both the amount owed and the notice-letter copy) matches its class.
Heather can still override any individual ticket afterward — this is a one-time
correction, NOT a boot migration, so it never re-clobbers later edits.

Dry-run by default (prints what WOULD change). Add --apply to commit.

    [RENDER SHELL] python3 backfill_ppi_storage.py           # preview
    [RENDER SHELL] python3 backfill_ppi_storage.py --apply   # write
"""
import sys

from app import app
from models import db, Vehicle


def main():
    apply = '--apply' in sys.argv
    with app.app_context():
        ppi = (Vehicle.query
               .filter(Vehicle.status == 'ACTIVE')
               .filter(Vehicle.impound_type == 'PPI')
               .order_by(Vehicle.id)
               .all())

        changes = []
        for v in ppi:
            target = Vehicle.ppi_storage_rate_for_class(v.vehicle_class)
            current = float(v.daily_storage_rate) if v.daily_storage_rate is not None else None
            if current != target:
                changes.append((v, current, target))

        if not changes:
            print(f'All {len(ppi)} active PPI vehicles already match their class rate. Nothing to do.')
            return

        header = 'CHANGE' if apply else 'WOULD CHANGE'
        print(f'{header}:')
        for v, current, target in changes:
            cur_str = f'${current:.2f}' if current is not None else '(none)'
            print(f'  #{v.id:<6} {str(v.stock_number or "-"):<10} '
                  f'{(v.vehicle_class or "light"):<6} {cur_str:>9} -> ${target:.2f}')
            if apply:
                v.daily_storage_rate = target

        print(f'\n{len(changes)} of {len(ppi)} active PPI vehicles '
              f'{"UPDATED" if apply else "would change (dry-run)"}.')

        if apply:
            db.session.commit()
            print('Committed.')
        else:
            print('Re-run with --apply to write these changes.')


if __name__ == '__main__':
    main()
