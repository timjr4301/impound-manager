#!/usr/bin/env python3
"""
compare_letter1_status.py

Read-only cross-check: does Towbook's "1st Letter Sent" column (blank vs.
filled) agree with what Impound Manager itself thinks — does this vehicle
have a Letter 1 that's actually marked sent?

Reads a Towbook "current inventory with first letter sent column" CSV from
/tmp/towbook_export.csv. Unlike the daily Impounds export, this file has NO
2-row metadata header — row 1 is the real column header row.

Matches by stock_number (VIN fallback). Prints:
  - AGREE: Towbook blank + IM has no sent Letter 1 (both say "not sent")
  - AGREE: Towbook filled + IM has a sent Letter 1 (both say "sent")
  - MISMATCH A: Towbook blank but IM shows it WAS sent (Towbook's field is
    stale, or it was sent through the app after this export was pulled)
  - MISMATCH B: Towbook filled but IM shows NOT sent (Towbook thinks it went
    out but IM has no record — could be sent outside the app and never
    recorded, OR "1st Letter Sent" isn't actually a sent-confirmation field;
    worth an actual look, not just believing either side blindly)
  - NOT IN IM: stock number in the CSV doesn't match any vehicle in our DB

Makes no changes — SELECT-only.

--- How to run on Render Shell ---
1. Upload the CSV:
     cat > /tmp/towbook_export.csv   (paste contents, then Ctrl-D)
2. Run:
     python3 compare_letter1_status.py
"""
import csv

from app import app
from models import Vehicle

CSV_PATH = '/tmp/towbook_export.csv'


def main():
    with open(CSV_PATH, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f'CSV rows loaded: {len(rows)}')

    with app.app_context():
        by_stock = {v.stock_number.strip().upper(): v
                    for v in Vehicle.query.all() if v.stock_number}
        by_vin = {v.vin.strip().upper(): v
                  for v in Vehicle.query.all() if v.vin}

        agree_both_not_sent = []
        agree_both_sent = []
        mismatch_towbook_blank_im_sent = []
        mismatch_towbook_filled_im_not_sent = []
        not_in_im = []

        for row in rows:
            stock = (row.get('Stock #') or '').strip()
            vin = (row.get('VIN') or '').strip()
            towbook_sent_raw = (row.get('1st Letter Sent') or '').strip()
            towbook_says_sent = bool(towbook_sent_raw)

            v = by_stock.get(stock.upper()) if stock else None
            if not v and vin:
                v = by_vin.get(vin.upper())
            if not v:
                not_in_im.append(stock or vin or '?')
                continue

            im_says_sent = bool(v.letter1 and v.letter1.sent_date)

            if towbook_says_sent and im_says_sent:
                agree_both_sent.append(stock)
            elif not towbook_says_sent and not im_says_sent:
                agree_both_not_sent.append(stock)
            elif not towbook_says_sent and im_says_sent:
                mismatch_towbook_blank_im_sent.append(
                    (stock, v.letter1.sent_date.strftime('%m/%d/%Y')))
            else:  # towbook_says_sent and not im_says_sent
                mismatch_towbook_filled_im_not_sent.append((stock, towbook_sent_raw))

        total_matched = (len(agree_both_sent) + len(agree_both_not_sent) +
                         len(mismatch_towbook_blank_im_sent) + len(mismatch_towbook_filled_im_not_sent))

        print('=' * 60)
        print(f'Matched to an IM vehicle: {total_matched}')
        print(f'Not found in IM at all:   {len(not_in_im)}')
        print('=' * 60)
        print(f'AGREE — both say NOT sent:  {len(agree_both_not_sent)}')
        print(f'AGREE — both say SENT:      {len(agree_both_sent)}')
        print(f'MISMATCH — Towbook blank, IM says SENT:      {len(mismatch_towbook_blank_im_sent)}')
        print(f'MISMATCH — Towbook filled, IM says NOT sent: {len(mismatch_towbook_filled_im_not_sent)}')

        if mismatch_towbook_blank_im_sent:
            print('\nFirst 15 — Towbook blank but IM shows sent (Towbook field is stale/behind):')
            for stock, sent_date in mismatch_towbook_blank_im_sent[:15]:
                print(f'  stock={stock}  IM sent_date={sent_date}')

        if mismatch_towbook_filled_im_not_sent:
            print('\nFirst 15 — Towbook filled but IM shows NOT sent (needs a real look):')
            for stock, raw_val in mismatch_towbook_filled_im_not_sent[:15]:
                print(f'  stock={stock}  Towbook "1st Letter Sent"={raw_val}')

        if not_in_im:
            print(f'\nFirst 15 not found in IM (released/purged/typo?):')
            for s in not_in_im[:15]:
                print(f'  {s}')

        print('\n' + '=' * 60)
        print('No changes made — this is read-only.')


if __name__ == '__main__':
    main()
