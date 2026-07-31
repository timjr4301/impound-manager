"""
WP-4, one-time: copy every row from production into the fresh staging
database, table by table, so staging has real-shaped data to test against.
Run scrub_for_staging.py (against staging's own DATABASE_URL) right after
this to strip out the real photos/documents it just copied over.

Written as a plain SQLAlchemy script rather than relying on pg_dump/psql,
which aren't installed on this machine — this only needs the psycopg2
driver the app already depends on.

SAFETY: takes both connection strings as required, explicit arguments —
nothing is read from DATABASE_URL or any other ambient env var, so there's
no way for it to silently pick up the wrong database. Source is opened
read-only (SELECT only, never written to). Destination tables are
TRUNCATEd then reloaded — point --dest at the staging database only, never
at production. Refuses to run if source and dest are the same URL, and
refuses to run at all without --i-understand-this-truncates-dest.

Run this from your own machine (or anywhere with network access to both
databases) using the EXTERNAL Database URL for each, from Render's
dashboard (Database -> Connect -> External Connection String):

    python3 clone_prod_to_staging.py ^
        --source "postgresql://...prod external url..." ^
        --dest   "postgresql://...staging external url..." ^
        --i-understand-this-truncates-dest
"""
import argparse
import sys

from sqlalchemy import create_engine, MetaData, insert, delete


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True, help='Production EXTERNAL database URL (read-only)')
    p.add_argument('--dest', required=True, help='Staging EXTERNAL database URL (will be truncated + reloaded)')
    p.add_argument('--i-understand-this-truncates-dest', action='store_true', dest='confirmed')
    args = p.parse_args()

    if not args.confirmed:
        print('Refusing to run without --i-understand-this-truncates-dest.')
        sys.exit(1)
    if args.source.strip() == args.dest.strip():
        print('Source and dest are the same URL — refusing (this would truncate production).')
        sys.exit(1)

    def host_of(url):
        return url.split('@')[-1].split('/')[0] if '@' in url else url

    print(f'Source (read-only): {host_of(args.source)}')
    print(f'Dest   (will be truncated + reloaded): {host_of(args.dest)}')

    src_engine = create_engine(args.source)
    dst_engine = create_engine(args.dest)

    # Reflect BOTH sides. Production turned out to also hold at least one
    # table (bj_books_invoices) that belongs to a different app sharing the
    # same physical database, not to Impound Manager. Copying only the
    # tables the FRESH staging database already has (created straight from
    # this repo's own models.py on its first boot) is what keeps that other
    # app's tables out automatically, rather than trusting "whatever's
    # sitting in the source database" the way a plain reflect(source) would.
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)
    dst_meta = MetaData()
    dst_meta.reflect(bind=dst_engine)

    dst_names = set(dst_meta.tables)
    skipped = sorted(name for name in src_meta.tables if name not in dst_names)
    if skipped:
        print(f'Skipping {len(skipped)} table(s) in source not part of Impound Manager '
              f'(not present in the freshly-created staging schema): {", ".join(skipped)}')

    meta = MetaData()
    meta.reflect(bind=src_engine, only=lambda name, _: name in dst_names)
    tables = meta.sorted_tables  # dependency order: parents before children

    with dst_engine.begin() as dst_conn:
        # Truncate children first (reverse dependency order) so FKs never block.
        for table in reversed(tables):
            dst_conn.execute(delete(table))
        print(f'Truncated {len(tables)} table(s) in staging.')

        with src_engine.connect() as src_conn:
            for table in tables:
                rows = src_conn.execute(table.select()).mappings().all()
                if not rows:
                    print(f'  {table.name}: 0 rows')
                    continue
                dst_conn.execute(insert(table), [dict(r) for r in rows])
                print(f'  {table.name}: {len(rows)} row(s) copied')

    print('\nDone. Now run, against the STAGING database only:')
    print('    python3 scrub_for_staging.py --confirm-staging')
    print('    python3 reset_users.py')


if __name__ == '__main__':
    main()
