"""
WP-4 bugfix: repair auto-increment counters left broken by
clone_prod_to_staging.py.

That script copies rows over with their real production `id` values already
set (so a letter still points at the same vehicle it did in production).
Postgres's per-table auto-increment counter (the "sequence") only advances
when a row is inserted WITHOUT an explicit id — so after the clone, every
table's sequence was still sitting wherever the fresh empty schema left it
(usually 1), even though real rows now occupy ids into the thousands. The
first time the running app tries to insert a brand-new row (a new vehicle
note, a new letter, a new envelope scan...) it collides with an existing
cloned row and the insert fails with "duplicate key value violates unique
constraint ... already exists."

This is a one-time repair: for every table with an integer `id` primary key,
set its sequence to (MAX(id) currently in the table), so the next new row
gets an id past everything the clone copied in.

Only ever touches the database this script's own DATABASE_URL points to —
same one-database-only convention as every other script in this repo. It
still refuses to run at all without an explicit flag, and prints exactly
which database host it's about to modify so you can check it's staging
before confirming. Safe to re-run any time — setting a sequence to the
current MAX(id) is idempotent.

    [STAGING RENDER SHELL ONLY] python3 fix_staging_sequences.py --confirm-staging
"""
import sys

from sqlalchemy import text

from app import app
from models import db


def main():
    if '--confirm-staging' not in sys.argv:
        print('Refusing to run without --confirm-staging. This only ever '
              'reads/adjusts sequence counters (no row data changes), but '
              'only ever run it against a staging database.')
        sys.exit(1)

    with app.app_context():
        db_url = app.config['SQLALCHEMY_DATABASE_URI']
        host = db_url.split('@')[-1].split('/')[0] if '@' in db_url else db_url
        print(f'About to fix sequences in: {host}')
        print('Ctrl+C now if this is not the staging database.\n')

        fixed = 0
        for table in db.metadata.sorted_tables:
            if 'id' not in table.columns:
                continue
            col = table.columns['id']
            if not col.primary_key or not col.autoincrement:
                continue
            result = db.session.execute(text(
                f'SELECT setval(pg_get_serial_sequence(:tbl, :col), '
                f'COALESCE((SELECT MAX(id) FROM "{table.name}"), 1), '
                f'(SELECT MAX(id) FROM "{table.name}") IS NOT NULL)'
            ), {'tbl': table.name, 'col': 'id'})
            new_val = result.scalar()
            print(f'  {table.name}: sequence set to {new_val}')
            fixed += 1
        db.session.commit()
        print(f'\nDone. {fixed} table sequence(s) checked/fixed.')


if __name__ == '__main__':
    main()
