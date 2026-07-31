"""
WP-4, one-time: copy every row from production into the fresh staging
database, table by table, so staging has real-shaped data to test against.
Run scrub_for_staging.py (against staging's own DATABASE_URL) right after
this to strip out the real photos/documents it just copied over.

Written as a plain SQLAlchemy script rather than relying on pg_dump/psql,
which aren't installed on this machine — this only needs the psycopg2
driver the app already depends on.

RESILIENCE: a real run against production found two things that mattered —
production holds tables/columns that don't belong to Impound Manager
(another app sharing the same database) and don't fit the current schema
(pre-rename leftovers), AND some tables (envelope_scans especially) carry
multi-hundred-KB base64 images that can make a single giant INSERT trip
over an unstable connection. So: rows are sent in small chunks, each chunk
commits in its own transaction (earlier chunks/tables survive even if a
later one fails), a connection-level failure (SSL drop, network blip) gets
a fresh connection and a retry rather than being treated as bad data, and
only a genuine per-row data problem (bad value, dangling FK) gets reported
and skipped. If the whole script is interrupted, it's always safe to just
re-run it from the top — truncation makes every run start from a clean
slate, there's no partial state to reconcile by hand.

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

from sqlalchemy import create_engine, MetaData, insert, delete, text
from sqlalchemy.exc import OperationalError, InterfaceError

CHUNK_SIZE = 20
MAX_RETRIES = 3


def is_connection_error(exc):
    """True for a dead/dropped connection (SSL closed, network blip) —
    worth retrying with a fresh connection. False for a real data problem
    (bad value, constraint violation) — worth reporting and skipping, not
    retrying, since retrying won't change a data-shape mismatch."""
    return isinstance(exc, (OperationalError, InterfaceError))


def fetch_all_with_retry(src_engine, table):
    """Read a table's rows in ordered chunks, each over its own fresh
    connection, retrying on a connection-level failure. A table with large
    blob columns (LKA/title PDFs, envelope images) can be too much to pull
    in one unbroken SELECT * — chunking the read the same way the write
    side is chunked means a dropped connection only costs one chunk, not
    the whole table's read."""
    pk_cols = list(table.primary_key.columns) or [list(table.columns)[0]]
    rows = []
    offset = 0
    while True:
        chunk = None
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with src_engine.connect() as conn:
                    stmt = table.select().order_by(*pk_cols).limit(CHUNK_SIZE).offset(offset)
                    chunk = conn.execute(stmt).mappings().all()
                break
            except Exception as exc:
                if is_connection_error(exc) and attempt < MAX_RETRIES:
                    last_exc = exc
                    continue
                raise
        if not chunk:
            break
        rows.extend(chunk)
        offset += CHUNK_SIZE
    return rows


def insert_with_retry(dst_engine, table, params):
    """Insert `params` (a list of one or more row dicts) in its own fresh
    connection + transaction. Retries with a brand-new connection on a
    connection-level failure; re-raises immediately on a data-level failure
    so the caller can fall back to smaller units instead of retrying
    something that will just fail the same way again."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with dst_engine.connect() as conn:
                with conn.begin():
                    conn.execute(insert(table), params)
            return
        except Exception as exc:
            if is_connection_error(exc) and attempt < MAX_RETRIES:
                last_exc = exc
                continue
            raise
    raise last_exc


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

    # Truncation is its own single pass, all committed before any inserts
    # start — children first (reverse order) so FKs never block a delete.
    with dst_engine.begin() as dst_conn:
        for table in reversed(tables):
            dst_conn.execute(delete(table))
    print(f'Truncated {len(tables)} table(s) in staging.')

    for table in tables:
        rows = fetch_all_with_retry(src_engine, table)
        if not rows:
            print(f'  {table.name}: 0 rows')
            continue
        # Same idea as the table-level skip: production can carry columns
        # (e.g. police_departments.hook_fee/storage_fee) left over from an
        # earlier build that current models.py no longer defines. Only
        # load columns the destination actually has; drop anything else,
        # once, with a note.
        dst_cols = set(dst_meta.tables[table.name].columns.keys())
        extra_cols = set(rows[0].keys()) - dst_cols
        if extra_cols:
            print(f'  {table.name}: dropping column(s) not in current schema: '
                  f'{", ".join(sorted(extra_cols))}')
        filtered = [{k: v for k, v in dict(r).items() if k in dst_cols} for r in rows]

        copied, failed = 0, []
        for i in range(0, len(filtered), CHUNK_SIZE):
            chunk = filtered[i:i + CHUNK_SIZE]
            try:
                insert_with_retry(dst_engine, table, chunk)
                copied += len(chunk)
            except Exception:
                # Something in this chunk won't go in as a batch — drop to
                # one row at a time so only the actual bad row(s) in it are
                # reported, not the whole chunk.
                for row in chunk:
                    try:
                        insert_with_retry(dst_engine, table, [row])
                        copied += 1
                    except Exception as row_exc:
                        failed.append((row.get('id', '?'), str(row_exc).splitlines()[0]))

        if failed:
            print(f'  {table.name}: {copied} row(s) copied, {len(failed)} row(s) FAILED and skipped')
            for pk, err in failed:
                print(f'    id={pk}: {err}')
        else:
            print(f'  {table.name}: {copied} row(s) copied')

    # Rows above were inserted with their real production `id` already set,
    # which never advances Postgres's per-table auto-increment sequence —
    # left alone, the very next row the app inserts normally (no explicit
    # id) would collide with one of the ids just copied in. Bring every
    # sequence up to the data's real MAX(id) so new rows start past it.
    with dst_engine.begin() as dst_conn:
        for table in tables:
            if 'id' not in table.columns:
                continue
            col = table.columns['id']
            if not col.primary_key or not col.autoincrement:
                continue
            dst_conn.execute(text(
                f'SELECT setval(pg_get_serial_sequence(:tbl, :col), '
                f'COALESCE((SELECT MAX(id) FROM "{table.name}"), 1), '
                f'(SELECT MAX(id) FROM "{table.name}") IS NOT NULL)'
            ), {'tbl': table.name, 'col': 'id'})
    print(f'Reset auto-increment sequences on {len(tables)} table(s).')

    print('\nDone. Now run, against the STAGING database only:')
    print('    python3 scrub_for_staging.py --confirm-staging')
    print('    python3 reset_users.py')


if __name__ == '__main__':
    main()
