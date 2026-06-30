#!/usr/bin/env python3
"""
ups_tracking_attach.py  —  FINAL VERSION
Combines multiple UPS Quantum View CSV exports, deduplicates by tracking
number, then attaches tracking + delivery status to Impound Manager vehicles.

Files expected in /tmp/:
  towbook_export.csv         — Towbook Current-impounds export
  ups_export_1.csv           — UPS Quantum View export #1
  ups_export_2.csv           — UPS Quantum View export #2  (optional)

Run in Render Shell:
  python3 ups_tracking_attach.py

Run migration SQL first if you haven't already:
  psql $DATABASE_URL -f letter_migration.sql
"""

import csv
import io
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

TOWBOOK_CSV = '/tmp/towbook_export.csv'
UPS_FILES   = ['/tmp/ups_export_1.csv', '/tmp/ups_export_2.csv']


# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_date(val):
    if not val or not val.strip() or val.strip() in ('Not Avail.', '--', 'N/A', ''):
        return None
    val = val.strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%Y %I:%M %p', '%m/%d/%Y %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def extract_invoice(ref):
    """UPS reference: 'INVOICE|SUFFIX|...' — first segment is invoice number."""
    if not ref:
        return None
    part = ref.split('|')[0].strip()
    return part if part else None


def classify_stage(shipments):
    """
    Given all UPS shipments for one invoice (sorted oldest first),
    return (letter_stage, letter_flag, flag_detail).
    """
    rts_any       = any(s['Return To Sender Indicator'] == 'YES' for s in shipments)
    exception_any = any(s['Status'] == 'Exception' for s in shipments)
    delivered     = [s for s in shipments
                     if s['Status'] == 'Delivered'
                     and s['Return To Sender Indicator'] != 'YES']
    in_transit    = any(s['Status'] in ('In Transit', 'Out for Delivery', 'Manifest')
                        for s in shipments)
    void_only     = all(s['Status'] == 'Void' for s in shipments)

    flag_detail = None
    for s in shipments:
        desc = s.get('Exception Description', '').strip()
        if desc and desc not in ('--', ''):
            flag_detail = desc[:500]
            break

    if void_only:
        return 'needs_1st', None, None
    if rts_any:
        return 'returned_rts', 'returned_rts', flag_detail or 'Return to sender'
    if exception_any and not delivered:
        return 'address_issue', 'address_issue', flag_detail
    if len(delivered) >= 2:
        return 'confirmed_both', None, None
    if len(delivered) == 1:
        return 'awaiting_2nd', None, None
    if in_transit:
        return 'in_transit', None, None
    return 'needs_1st', None, None


# ── Load data ──────────────────────────────────────────────────────────────────

def load_towbook(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()
    rows = [{k.strip(): v for k, v in r.items()}
            for r in csv.DictReader(io.StringIO(''.join(lines[2:])))]
    by_invoice = {}
    for r in rows:
        inv = (r.get('Invoice #') or '').strip()
        if inv:
            by_invoice[inv] = r
    print(f"Towbook: {len(rows)} vehicles, {len(by_invoice)} with invoice numbers")
    return rows, by_invoice


def load_ups_combined(paths):
    """Load and deduplicate multiple UPS CSVs by tracking number."""
    seen_tracking = set()
    all_rows = []
    for path in paths:
        if not os.path.exists(path):
            print(f"  (skipping {path} — not found)")
            continue
        with open(path, 'r', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
        new_rows = [r for r in rows if r['Tracking Number'] not in seen_tracking]
        seen_tracking.update(r['Tracking Number'] for r in new_rows)
        all_rows.extend(new_rows)
        dates = [r['Manifest Date'] for r in rows if r.get('Manifest Date', '').strip()]
        print(f"  {path}: {len(rows)} rows ({len(new_rows)} new unique) "
              f"| dates {min(dates) if dates else '?'} → {max(dates) if dates else '?'}")

    by_invoice = defaultdict(list)
    for r in all_rows:
        inv = extract_invoice(r.get('Reference Number(s)', ''))
        if inv:
            by_invoice[inv].append(r)

    for inv in by_invoice:
        by_invoice[inv].sort(key=lambda x: x.get('Manifest Date', ''))

    print(f"UPS combined: {len(all_rows)} unique shipments | {len(by_invoice)} unique invoices")
    return all_rows, by_invoice


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("Loading CSVs...")
    tb_rows, tb_by_invoice = load_towbook(TOWBOOK_CSV)
    print("Loading UPS exports:")
    all_ups, ups_by_invoice = load_ups_combined(UPS_FILES)

    matched_invoices = set(tb_by_invoice.keys()) & set(ups_by_invoice.keys())
    print(f"\nInvoices matched Towbook ↔ UPS: {len(matched_invoices)}")
    print(f"Towbook invoices with no UPS shipment: "
          f"{len(set(tb_by_invoice.keys()) - set(ups_by_invoice.keys()))}")
    print()

    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    updated      = 0
    no_db        = 0
    errors       = 0
    rts_list     = []
    exc_list     = []
    stage_counts = Counter()

    for inv in sorted(matched_invoices):
        tb_row    = tb_by_invoice[inv]
        shipments = ups_by_invoice[inv]  # sorted oldest → newest
        stock     = tb_row.get('Stock #', '').strip()
        owner     = tb_row.get('Account', '').strip()
        vehicle_s = tb_row.get('Vehicle', '').strip()

        # ── Find vehicle in DB ────────────────────────────────────────────────
        cur.execute(
            """SELECT id FROM vehicles
                WHERE stock_number = %s OR invoice_number = %s
                LIMIT 1""",
            (stock, inv),
        )
        db_vehicle = cur.fetchone()
        if not db_vehicle:
            no_db += 1
            continue

        vehicle_id = db_vehicle['id']
        letter_stage, letter_flag, flag_detail = classify_stage(shipments)
        stage_counts[letter_stage] += 1

        if letter_flag == 'returned_rts':
            rts_list.append(f"  Stock {stock} | Inv {inv} | {owner} | {vehicle_s}")
        elif letter_flag == 'address_issue':
            exc_list.append(f"  Stock {stock} | Inv {inv} | {owner}\n"
                            f"    -> {(flag_detail or '')[:90]}")

        try:
            # ── Letter 1 ──────────────────────────────────────────────────────
            s1 = shipments[0]
            fields1 = {
                'tracking_number':    s1['Tracking Number'],
                'sent_date':          parse_date(s1['Manifest Date']),
                'delivery_confirmed_date': (parse_date(s1['Date Delivered'])
                                            if s1['Status'] == 'Delivered'
                                            and s1['Return To Sender Indicator'] != 'YES'
                                            else None),
                'scheduled_delivery': parse_date(s1.get('Scheduled Delivery', '')),
                'return_to_sender':   s1['Return To Sender Indicator'] == 'YES',
                'ups_status':         s1['Status'],
            }

            cur.execute(
                "SELECT id FROM certified_letters WHERE vehicle_id = %s AND letter_number = 1 LIMIT 1",
                (vehicle_id,),
            )
            row1 = cur.fetchone()

            if row1:
                cur.execute(
                    """UPDATE certified_letters SET
                           tracking_number          = COALESCE(NULLIF(tracking_number,''), %(tracking_number)s),
                           sent_date                = COALESCE(sent_date, %(sent_date)s),
                           delivery_confirmed_date  = COALESCE(delivery_confirmed_date, %(delivery_confirmed_date)s),
                           scheduled_delivery       = COALESCE(scheduled_delivery, %(scheduled_delivery)s),
                           return_to_sender         = CASE WHEN return_to_sender IS NULL
                                                          THEN %(return_to_sender)s
                                                          ELSE return_to_sender END,
                           ups_status               = %(ups_status)s,
                           updated_at               = NOW()
                       WHERE id = %(id)s""",
                    {**fields1, 'id': row1['id']},
                )
            else:
                # due_date is NOT NULL — use sent_date as approximation when
                # inserting from UPS data only (letter is already in the mail)
                cur.execute(
                    """INSERT INTO certified_letters
                           (vehicle_id, letter_number, tracking_number, due_date,
                            sent_date, delivery_confirmed_date, scheduled_delivery,
                            return_to_sender, ups_status, created_at, updated_at)
                       VALUES
                           (%(vehicle_id)s, 1, %(tracking_number)s, %(sent_date)s,
                            %(sent_date)s, %(delivery_confirmed_date)s, %(scheduled_delivery)s,
                            %(return_to_sender)s, %(ups_status)s, NOW(), NOW())""",
                    {**fields1, 'vehicle_id': vehicle_id},
                )

            # ── Letter 2 (if second shipment exists) ──────────────────────────
            if len(shipments) >= 2:
                s2 = shipments[1]
                fields2 = {
                    'tracking_number':    s2['Tracking Number'],
                    'sent_date':          parse_date(s2['Manifest Date']),
                    'delivery_confirmed_date': (parse_date(s2['Date Delivered'])
                                                if s2['Status'] == 'Delivered'
                                                and s2['Return To Sender Indicator'] != 'YES'
                                                else None),
                    'scheduled_delivery': parse_date(s2.get('Scheduled Delivery', '')),
                    'return_to_sender':   s2['Return To Sender Indicator'] == 'YES',
                    'ups_status':         s2['Status'],
                }

                cur.execute(
                    "SELECT id FROM certified_letters WHERE vehicle_id = %s AND letter_number = 2 LIMIT 1",
                    (vehicle_id,),
                )
                row2 = cur.fetchone()

                if row2:
                    cur.execute(
                        """UPDATE certified_letters SET
                               tracking_number         = COALESCE(NULLIF(tracking_number,''), %(tracking_number)s),
                               sent_date               = COALESCE(sent_date, %(sent_date)s),
                               delivery_confirmed_date = COALESCE(delivery_confirmed_date, %(delivery_confirmed_date)s),
                               scheduled_delivery      = COALESCE(scheduled_delivery, %(scheduled_delivery)s),
                               return_to_sender        = CASE WHEN return_to_sender IS NULL
                                                             THEN %(return_to_sender)s
                                                             ELSE return_to_sender END,
                               ups_status              = %(ups_status)s,
                               updated_at              = NOW()
                           WHERE id = %(id)s""",
                        {**fields2, 'id': row2['id']},
                    )
                else:
                    cur.execute(
                        """INSERT INTO certified_letters
                               (vehicle_id, letter_number, tracking_number, due_date,
                                sent_date, delivery_confirmed_date, scheduled_delivery,
                                return_to_sender, ups_status, created_at, updated_at)
                           VALUES
                               (%(vehicle_id)s, 2, %(tracking_number)s, %(sent_date)s,
                                %(sent_date)s, %(delivery_confirmed_date)s, %(scheduled_delivery)s,
                                %(return_to_sender)s, %(ups_status)s, NOW(), NOW())""",
                        {**fields2, 'vehicle_id': vehicle_id},
                    )

            # ── Update vehicle letter stage + flag ────────────────────────────
            cur.execute(
                """UPDATE vehicles SET
                       letter_stage       = %(stage)s,
                       letter_flag        = CASE WHEN letter_flag IS NULL THEN %(flag)s
                                                ELSE letter_flag END,
                       letter_flag_detail = CASE WHEN letter_flag_detail IS NULL THEN %(detail)s
                                                ELSE letter_flag_detail END
                   WHERE id = %(id)s""",
                {'stage': letter_stage, 'flag': letter_flag,
                 'detail': flag_detail, 'id': vehicle_id},
            )

            conn.commit()
            updated += 1

        except Exception as exc:
            conn.rollback()
            errors += 1
            print(f"  ERROR — inv {inv} stock {stock}: {exc}")

    cur.close()
    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 58)
    print(f"  UPDATED IN DATABASE     : {updated}")
    print(f"  NOT FOUND IN DB         : {no_db}")
    print(f"  ERRORS                  : {errors}")
    print("=" * 58)
    print()
    print("Letter stage breakdown:")
    labels = {
        'returned_rts':  'Return to Sender',
        'awaiting_2nd':  'Awaiting 2nd letter',
        'in_transit':    'In Transit',
        'confirmed_both':'Both confirmed',
        'address_issue': 'Address Issue',
        'needs_1st':     'Needs 1st letter',
    }
    for stage, count in stage_counts.most_common():
        print(f"  {labels.get(stage, stage)}: {count}")

    if rts_list:
        print(f"\nRETURN TO SENDER — {len(rts_list)} vehicles need resend TODAY:")
        for line in rts_list:
            print(line)

    if exc_list:
        print(f"\nADDRESS ISSUES — {len(exc_list)} vehicles:")
        for line in exc_list:
            print(line)

    print()
    print("Done.")


if __name__ == '__main__':
    main()
