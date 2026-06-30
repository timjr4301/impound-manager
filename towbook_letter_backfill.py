#!/usr/bin/env python3
"""
towbook_letter_backfill.py

Reads Towbook CSV from /tmp/towbook_export.csv and backfills letter data
into the Render PostgreSQL database.

--- How to run on Render Shell ---
1. In Render Shell, upload the CSV:
     cat > /tmp/towbook_export.csv   (paste contents, then Ctrl-D)
   Or via scp / sftp.
2. Run:
     python3 towbook_letter_backfill.py
   (DATABASE_URL is already set in the Render environment)

--- How to run locally against Render DB ---
     DATABASE_URL=postgresql://... python3 towbook_letter_backfill.py

Columns touched in certified_letters:
  due_date, sent_date, delivery_confirmed_date, return_to_sender, notes, created_at
Columns touched in vehicles:
  nada_value (only if currently NULL in DB)
Never deletes or modifies any other vehicle data.
"""

import csv
import io
import os
import sys
from datetime import datetime, date

import psycopg2
import psycopg2.extras

# ── Config ─────────────────────────────────────────────────────────────────────
CSV_PATH = '/tmp/towbook_export.csv'
HEADER_ROWS_TO_SKIP = 2   # row 1 = "Current - Impounds", row 2 = timestamp


# ── Date parser ─────────────────────────────────────────────────────────────────
def parse_date(val):
    if not val or not val.strip():
        return None
    val = val.strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%Y %I:%M %p', '%m/%d/%Y %I:%M%p',
                '%m/%d/%Y %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


# ── Letter stage helper (informational — printed in summary) ────────────────────
def get_letter_stage(sent_1st, tracking_complete_1st, due_2nd, complete_2nd,
                     date_signed, ready_to_file, title_filing):
    today = date.today()
    if title_filing:
        return 'ready_to_file'
    if ready_to_file:
        return 'ready_to_file'
    if complete_2nd:
        return 'ready_to_file'
    if date_signed:
        return 'ready_to_file'
    if due_2nd:
        if sent_1st:
            return 'needs_2nd' if today >= due_2nd else 'awaiting_2nd_delivery'
        return 'needs_2nd'
    if sent_1st or tracking_complete_1st:
        return 'awaiting_1st_delivery'
    return 'needs_1st'


# ── DB helpers ──────────────────────────────────────────────────────────────────
def find_vehicle(cur, stock_number, invoice_number, call_number):
    for field, val in [('stock_number', stock_number),
                       ('invoice_number', invoice_number),
                       ('call_number', call_number)]:
        if val:
            cur.execute(
                f"SELECT id FROM vehicles WHERE {field} = %s LIMIT 1", (val,)
            )
            row = cur.fetchone()
            if row:
                return row['id']
    return None


def upsert_letter(cur, vehicle_id, letter_number, due_date, sent_date,
                  delivery_confirmed_date, return_to_sender, notes_extra):
    """
    certified_letters columns used:
      id, vehicle_id, letter_number, due_date, sent_date,
      delivery_confirmed_date, return_to_sender, notes, created_at
    """
    cur.execute(
        """SELECT id, due_date, sent_date, delivery_confirmed_date, return_to_sender
             FROM certified_letters
            WHERE vehicle_id = %s AND letter_number = %s
            LIMIT 1""",
        (vehicle_id, letter_number),
    )
    existing = cur.fetchone()

    if existing:
        sets, params = [], []
        # Always sync due_date from Towbook (authoritative)
        if due_date and existing['due_date'] != due_date:
            sets.append('due_date = %s')
            params.append(due_date)
        # Fill sent_date only if DB is blank
        if sent_date and not existing['sent_date']:
            sets.append('sent_date = %s')
            params.append(sent_date)
        # Fill delivery_confirmed_date only if DB is blank
        if delivery_confirmed_date and not existing['delivery_confirmed_date']:
            sets.append('delivery_confirmed_date = %s')
            params.append(delivery_confirmed_date)
        # Set return_to_sender if CSV says so (never un-set it)
        if return_to_sender and not existing['return_to_sender']:
            sets.append('return_to_sender = %s')
            params.append(True)
        if notes_extra:
            sets.append("notes = COALESCE(notes || E'\\n', '') || %s")
            params.append(notes_extra)
        if sets:
            params.append(existing['id'])
            cur.execute(
                f"UPDATE certified_letters SET {', '.join(sets)} WHERE id = %s",
                params,
            )
            return 'updated'
        return 'unchanged'
    else:
        cur.execute(
            """INSERT INTO certified_letters
                   (vehicle_id, letter_number, due_date, sent_date,
                    delivery_confirmed_date, return_to_sender, notes, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
            (vehicle_id, letter_number, due_date, sent_date,
             delivery_confirmed_date, return_to_sender or False, notes_extra),
        )
        return 'inserted'


# ── Main ────────────────────────────────────────────────────────────────────────
def main():
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        print('ERROR: DATABASE_URL not set')
        sys.exit(1)
    if db_url.startswith('postgres://'):
        db_url = 'postgresql://' + db_url[len('postgres://'):]

    if not os.path.exists(CSV_PATH):
        print(f'ERROR: CSV not found at {CSV_PATH}')
        sys.exit(1)

    print(f'Reading {CSV_PATH} ...')
    with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()

    # Skip title + timestamp rows; row 3 becomes the header
    reader = csv.DictReader(io.StringIO(''.join(lines[HEADER_ROWS_TO_SKIP:])))
    rows = list(reader)
    # Strip whitespace from all header keys
    rows = [{k.strip(): v for k, v in row.items()} for row in rows]
    print(f'CSV rows loaded: {len(rows)}')

    print('Connecting to database ...')
    conn = psycopg2.connect(db_url)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    print('Connected.\n')

    stats = {
        'matched':    0,
        'no_match':   0,
        'no_data':    0,
        'inserted':   0,
        'updated':    0,
        'unchanged':  0,
        'errors':     0,
    }
    no_match_stocks = []

    for row in rows:
        def g(col):
            return (row.get(col) or '').strip()

        stock_number   = g('Stock #')
        invoice_number = g('Invoice #')
        call_number    = g('Call #')

        if not stock_number and not call_number:
            stats['no_data'] += 1
            continue

        # ── Locate vehicle ──────────────────────────────────────────────────
        vehicle_id = find_vehicle(cur, stock_number, invoice_number, call_number)
        if not vehicle_id:
            stats['no_match'] += 1
            no_match_stocks.append(stock_number or call_number)
            continue

        stats['matched'] += 1

        # ── Parse all date columns ──────────────────────────────────────────
        sent_1st              = parse_date(g('1st Letter Sent'))
        tracking_due_1st      = parse_date(g('Tracking of 1st Letter  Due Date'))
        tracking_complete_1st = parse_date(g('Tracking of 1st Letter  Complete Date'))
        due_2nd               = parse_date(g('SECOND LETTER Due Date'))
        complete_2nd          = parse_date(g('SECOND LETTER Complete Date'))
        date_signed           = parse_date(g('DATE SIGNED OR UNDELIVERABLE'))
        ready_to_file         = g('READY TO FILE') or None
        title_filing_date     = parse_date(g('Title Filing Date'))
        nada_raw              = g('NADA Value').replace(',', '').replace('$', '') or None

        # Infer return_to_sender: Towbook's "DATE SIGNED OR UNDELIVERABLE" fires
        # for both outcomes; we can't distinguish here, so only flag RTS if the
        # 1st letter was never confirmed delivered but the column is set.
        rts_1st = bool(date_signed and not tracking_complete_1st)

        stage = get_letter_stage(sent_1st, tracking_complete_1st, due_2nd,
                                 complete_2nd, date_signed, ready_to_file,
                                 title_filing_date)

        # ── Skip if no letter data at all ──────────────────────────────────
        has_letter_data = any([sent_1st, tracking_due_1st, tracking_complete_1st,
                               due_2nd, complete_2nd])
        if not has_letter_data:
            stats['no_data'] += 1
            continue

        try:
            # ── Vehicle: update nada_value if DB is NULL ────────────────────
            if nada_raw:
                try:
                    nada_float = float(nada_raw)
                    cur.execute(
                        'UPDATE vehicles SET nada_value = %s WHERE id = %s AND nada_value IS NULL',
                        (nada_float, vehicle_id),
                    )
                except ValueError:
                    pass

            # ── Upsert Letter 1 ─────────────────────────────────────────────
            if tracking_due_1st or sent_1st:
                notes_1 = f'Date signed / undeliverable: {date_signed}' if date_signed else None
                result = upsert_letter(
                    cur, vehicle_id, 1,
                    due_date=tracking_due_1st,
                    sent_date=sent_1st,
                    delivery_confirmed_date=tracking_complete_1st,
                    return_to_sender=rts_1st,
                    notes_extra=notes_1,
                )
                stats[result] += 1

            # ── Upsert Letter 2 ─────────────────────────────────────────────
            if due_2nd:
                result = upsert_letter(
                    cur, vehicle_id, 2,
                    due_date=due_2nd,
                    sent_date=complete_2nd,
                    delivery_confirmed_date=None,
                    return_to_sender=False,
                    notes_extra=None,
                )
                stats[result] += 1

            conn.commit()

        except Exception as exc:
            conn.rollback()
            stats['errors'] += 1
            print(f'  ERROR stock={stock_number}: {exc}')

    cur.close()
    conn.close()

    print()
    print('=' * 50)
    print(f"  VEHICLES MATCHED      : {stats['matched']}")
    print(f"  NOT IN DATABASE       : {stats['no_match']}")
    print(f"  SKIPPED (no data)     : {stats['no_data']}")
    print(f"  LETTERS INSERTED      : {stats['inserted']}")
    print(f"  LETTERS UPDATED       : {stats['updated']}")
    print(f"  LETTERS UNCHANGED     : {stats['unchanged']}")
    print(f"  ERRORS                : {stats['errors']}")
    print('=' * 50)

    if no_match_stocks:
        shown = no_match_stocks[:20]
        print(f"\nStocks not found in DB ({len(no_match_stocks)}):")
        for s in shown:
            print(f'  {s}')
        if len(no_match_stocks) > 20:
            print(f'  ... and {len(no_match_stocks) - 20} more')


if __name__ == '__main__':
    main()
