#!/usr/bin/env python3
"""
overdue_letters_breakdown.py

Read-only report on the 626 "Overdue Letters" backlog (same population as
/audit's Section 2 — see blueprints/audit.py: _overdue_letter_issue).

Before spending time on a Towbook release-history export to cross-check these,
this answers: what's actually IN the backlog? Groups by account (broker/PD/
property name), impound_type, and how long the vehicle has been sitting —
so we know how much is genuinely "released years ago, just never marked" vs.
oddball cases (transport/relocation brokers, campers/trailers, etc.) that need
a real look, not a CSV date match.

Makes no changes — SELECT-only.

--- How to run on Render Shell ---
    python3 overdue_letters_breakdown.py
"""
from collections import Counter
from datetime import date

from app import app
from models import db, Vehicle
from blueprints.audit import _overdue_letter_issue, _active_not_ghost

_SUSPECT_ACCOUNT_WORDS = ('auction', 'broker', 'salvage', 'transport', 'recovery', 'logistics')


def main():
    with app.app_context():
        today = date.today()
        active_vehicles = _active_not_ghost().all()

        overdue = []
        for v in active_vehicles:
            issue = _overdue_letter_issue(v, today)
            if issue:
                overdue.append(v)

        print(f'Total overdue-letter vehicles: {len(overdue)}')
        print('=' * 60)

        # ── By account (broker / PD / property name) ────────────────────────
        by_account = Counter((v.account or '(blank)').strip() for v in overdue)
        print('\nTop 30 accounts by count:')
        for acct, count in by_account.most_common(30):
            flag = ' <-- possible broker/transport' if any(
                w in acct.lower() for w in _SUSPECT_ACCOUNT_WORDS) else ''
            print(f'  {count:4d}  {acct}{flag}')

        # ── By impound type ──────────────────────────────────────────────────
        by_type = Counter(v.impound_type or '(blank)' for v in overdue)
        print('\nBy impound type:')
        for t, count in by_type.most_common():
            print(f'  {count:4d}  {t}')

        # ── By age bucket ─────────────────────────────────────────────────────
        buckets = Counter()
        for v in overdue:
            days = v.days_in_storage
            if days is None:
                buckets['(no impound_date)'] += 1
            elif days < 30:
                buckets['0-29 days'] += 1
            elif days < 90:
                buckets['30-89 days'] += 1
            elif days < 365:
                buckets['90-364 days'] += 1
            elif days < 730:
                buckets['1-2 years'] += 1
            else:
                buckets['2+ years'] += 1
        print('\nBy age:')
        for label in ('0-29 days', '30-89 days', '90-364 days', '1-2 years', '2+ years', '(no impound_date)'):
            if buckets.get(label):
                print(f'  {buckets[label]:4d}  {label}')

        # ── Flagged as possible broker/transport by account name ────────────
        suspect = [v for v in overdue if any(
            w in (v.account or '').lower() for w in _SUSPECT_ACCOUNT_WORDS)]
        print(f'\nVehicles with a broker/transport-sounding account: {len(suspect)}')
        if suspect:
            print('First 20:')
            for v in suspect[:20]:
                print(f'  stock={v.stock_number}  account={v.account}  '
                      f'days={v.days_in_storage}  type={v.impound_type}')

        print('\n' + '=' * 60)
        print('No changes made — this is read-only.')


if __name__ == '__main__':
    main()
