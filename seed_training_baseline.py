"""
Seed the 10-vehicle training baseline used for staff walkthroughs and
click-by-click training videos.

Each vehicle is frozen at a different chapter of the SAME optimal, textbook
journey through the app — intake, BMV search, Letter 1, the 30-day wait,
Letter 2, Tina's sell/junk tracks, pending pickup, and finally released —
with no anomalies, mismatches, or edge cases mixed in. The goal is to teach
"what right looks like" first; edge cases get their own training data later.

Safe to re-run any time: it only ever touches vehicles whose stock_number
starts with "TRAIN-", so it never touches real data. Every run deletes and
recreates those 10 rows (and their letters/charges) fresh, with dates
computed relative to today — so the story is always at the same relative
point in time no matter when you run it.

    [RENDER SHELL] python3 seed_training_baseline.py
"""
from datetime import date, datetime, timedelta

from app import app
from models import db, Vehicle, CertifiedLetter, VehicleCharge

STOCK_PREFIX = 'TRAIN-'
TODAY = date.today()


def d(days_ago):
    """impound/letter date `days_ago` days before today."""
    return TODAY - timedelta(days=days_ago)


# Each entry: vehicle fields, plus an optional `letters` list and `charges` list.
# Ordered as the 10 chapters of one story, earliest-in-lifecycle first.
VEHICLES = [
    {
        # Chapter 1: just towed in, nothing done yet.
        'stock_number': 'TRAIN-01', 'vin': 'TRAIN00000000001',
        'plate': 'ABC1234', 'plate_state': 'OH', 'year': '2018', 'make': 'Honda',
        'model_name': 'Civic', 'color': 'Silver',
        'impound_type': 'PPI', 'impound_date': d(0), 'storage_location': 'Yard A',
        'owner_name': 'Marcus Webb', 'owner_address': '412 Maple St',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45402',
        'mileage': 45210, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 8400.00, 'vehicle_class': 'light',
        'bmv_stage': 'PENDING',
        'letters': [
            {'letter_number': 1, 'due_date': d(0) + timedelta(days=5), 'letter_kind': 'first_notice'},
        ],
    },
    {
        # Chapter 2: BMV search done, Letter 1 sent today.
        'stock_number': 'TRAIN-02', 'vin': 'TRAIN00000000002',
        'plate': 'DEF5678', 'plate_state': 'OH', 'year': '2016', 'make': 'Toyota',
        'model_name': 'Corolla', 'color': 'Blue',
        'impound_type': 'PPI', 'impound_date': d(3), 'storage_location': 'Yard A',
        'owner_name': 'Angela Rios', 'owner_address': '88 Riverside Dr',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45409',
        'mileage': 61890, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 6900.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(1), 'heather_complete': True,
        'heather_complete_date': TODAY,
        'letters': [
            {'letter_number': 1, 'due_date': d(3) + timedelta(days=5), 'sent_date': TODAY,
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000002'},
        ],
    },
    {
        # Chapter 3: Letter 1 sent 12 days ago, waiting out the 30-day window.
        'stock_number': 'TRAIN-03', 'vin': 'TRAIN00000000003',
        'plate': 'GHI9012', 'plate_state': 'OH', 'year': '2015', 'make': 'Ford',
        'model_name': 'Fusion', 'color': 'Black',
        'impound_type': 'PPI', 'impound_date': d(15), 'storage_location': 'Yard B',
        'owner_name': 'Devon Carter', 'owner_address': '2201 Wayne Ave',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45410',
        'lienholder_name': 'Fifth Third Bank', 'lienholder_address': 'PO Box 630091',
        'lienholder_city': 'Cincinnati', 'lienholder_state': 'OH', 'lienholder_zip': '45263',
        'mileage': 78320, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 5200.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(14), 'heather_complete': True,
        'heather_complete_date': d(14),
        'letters': [
            {'letter_number': 1, 'due_date': d(15) + timedelta(days=5), 'sent_date': d(12),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000003'},
        ],
    },
    {
        # Chapter 4: Letter 1 sent exactly 30 days ago — Letter 2 opens today.
        'stock_number': 'TRAIN-04', 'vin': 'TRAIN00000000004',
        'plate': 'JKL3456', 'plate_state': 'OH', 'year': '2012', 'make': 'Ford',
        'model_name': 'F-150', 'color': 'Red',
        'impound_type': 'PPI', 'impound_date': d(33), 'storage_location': 'Yard B',
        'owner_name': 'Sherry Combs', 'owner_address': '77 Salem Ave',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45406',
        'mileage': 112400, 'tow_fee': 175.00, 'daily_storage_rate': 37.00,
        'nada_value': 9800.00, 'vehicle_class': 'medium',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(32), 'heather_complete': True,
        'heather_complete_date': d(32),
        'letters': [
            {'letter_number': 1, 'due_date': d(33) + timedelta(days=5), 'sent_date': d(30),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000004',
             'delivery_confirmed_date': d(27)},
            {'letter_number': 2, 'due_date': TODAY, 'letter_kind': 'second_notice'},
        ],
    },
    {
        # Chapter 5: a POLICE tow — Notice of Lien sent within its 10-day window.
        'stock_number': 'TRAIN-05', 'vin': 'TRAIN00000000005',
        'plate': 'MNO7890', 'plate_state': 'OH', 'year': '2017', 'make': 'Chevrolet',
        'model_name': 'Malibu', 'color': 'White',
        'impound_type': 'POLICE', 'impound_date': d(8), 'storage_location': 'Yard A',
        'police_report_number': 'DPD-26-004821',
        'owner_name': 'Kevin Ashford', 'owner_address': '511 Xenia Ave',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45410',
        'mileage': 53100, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 7600.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(7), 'heather_complete': True,
        'heather_complete_date': d(7),
        'letters': [
            {'letter_number': 1, 'due_date': d(8) + timedelta(days=10), 'sent_date': d(2),
             'letter_kind': 'notice_of_lien', 'tracking_number': '1Z999AA10000000005'},
        ],
        'charges': [
            {'label': 'Fuel Surcharge (PD)', 'amount': 12.50},
        ],
    },
    {
        # Chapter 6: both letters sent and confirmed — handed to Tina, awaiting title.
        'stock_number': 'TRAIN-06', 'vin': 'TRAIN00000000006',
        'plate': 'PQR1234', 'plate_state': 'OH', 'year': '2014', 'make': 'Toyota',
        'model_name': 'Camry', 'color': 'Gray',
        'impound_type': 'PPI', 'impound_date': d(80), 'storage_location': 'Yard B',
        'owner_name': 'Latoya Freeman', 'owner_address': '900 Gettysburg Ave',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45417',
        'mileage': 96700, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 4800.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(79), 'heather_complete': True,
        'heather_complete_date': d(79),
        'lka_document_confirmed': True, 'title_search_confirmed': True,
        'ups_delivery_confirmed': True, 'return_receipt_filed': True,
        'tina_stage': 'AWAITING_TITLE', 'tina_stage_at': datetime.utcnow() - timedelta(days=40),
        'letters': [
            {'letter_number': 1, 'due_date': d(80) + timedelta(days=5), 'sent_date': d(75),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000006',
             'delivery_confirmed_date': d(72)},
            {'letter_number': 2, 'due_date': d(45), 'sent_date': d(44),
             'letter_kind': 'second_notice', 'tracking_number': '1Z999AA10000000106',
             'delivery_confirmed_date': d(41)},
        ],
        'charges': [
            {'label': 'Admin Fee', 'amount': 25.00},
        ],
    },
    {
        # Chapter 7: sell track, auction ready.
        'stock_number': 'TRAIN-07', 'vin': 'TRAIN00000000007',
        'plate': 'STU5678', 'plate_state': 'OH', 'year': '2013', 'make': 'Jeep',
        'model_name': 'Wrangler', 'color': 'Green',
        'impound_type': 'PPI', 'impound_date': d(110), 'storage_location': 'Yard C',
        'owner_name': 'Preston Vance', 'owner_address': '15 Wilmington Pike',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45420',
        'mileage': 88900, 'tow_fee': 175.00, 'daily_storage_rate': 37.00,
        'nada_value': 11200.00, 'vehicle_class': 'medium',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(109), 'heather_complete': True,
        'heather_complete_date': d(109),
        'lka_document_confirmed': True, 'title_search_confirmed': True,
        'ups_delivery_confirmed': True, 'return_receipt_filed': True,
        'disposition': 'SELL', 'disposition_set_date': d(70),
        'tina_stage': 'AUCTION_READY', 'tina_stage_at': datetime.utcnow() - timedelta(days=10),
        'inspection_done': True, 'inspection_diagnosis': 'AUCTION',
        'inspected_by': 'tina', 'inspected_at': datetime.utcnow() - timedelta(days=12),
        'key_location': 'TINA', 'key_location_by': 'tina',
        'key_location_at': datetime.utcnow() - timedelta(days=12),
        'letters': [
            {'letter_number': 1, 'due_date': d(110) + timedelta(days=5), 'sent_date': d(105),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000007',
             'delivery_confirmed_date': d(102)},
            {'letter_number': 2, 'due_date': d(75), 'sent_date': d(74),
             'letter_kind': 'second_notice', 'tracking_number': '1Z999AA10000000107',
             'delivery_confirmed_date': d(71)},
        ],
        'charges': [
            {'label': 'Gate Fee', 'amount': 15.00},
            {'label': 'Key Replacement', 'amount': 85.00},
        ],
    },
    {
        # Chapter 8: junk track, pending scrap.
        'stock_number': 'TRAIN-08', 'vin': 'TRAIN00000000008',
        'plate': 'VWX9012', 'plate_state': 'OH', 'year': '2004', 'make': 'Buick',
        'model_name': 'LeSabre', 'color': 'Tan',
        'impound_type': 'PPI', 'impound_date': d(100), 'storage_location': 'Yard C',
        'owner_name': 'Harold Nixon', 'owner_address': '640 Woodman Dr',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45420',
        'mileage': 168300, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 1200.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(99), 'heather_complete': True,
        'heather_complete_date': d(99),
        'lka_document_confirmed': True, 'title_search_confirmed': True,
        'ups_delivery_confirmed': True, 'return_receipt_filed': True,
        'disposition': 'JUNK', 'disposition_set_date': d(60),
        'tina_stage': 'JUNK_PENDING', 'tina_stage_at': datetime.utcnow() - timedelta(days=5),
        'inspection_done': True, 'inspection_diagnosis': 'JUNK',
        'inspected_by': 'tina', 'inspected_at': datetime.utcnow() - timedelta(days=6),
        'letters': [
            {'letter_number': 1, 'due_date': d(100) + timedelta(days=5), 'sent_date': d(95),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000008',
             'delivery_confirmed_date': d(92)},
            {'letter_number': 2, 'due_date': d(65), 'sent_date': d(64),
             'letter_kind': 'second_notice', 'tracking_number': '1Z999AA10000000108',
             'delivery_confirmed_date': d(61)},
        ],
    },
    {
        # Chapter 9: paid in full, authorized, waiting on the customer to pick it up.
        'stock_number': 'TRAIN-09', 'vin': 'TRAIN00000000009',
        'plate': 'YZA3456', 'plate_state': 'OH', 'year': '2019', 'make': 'Nissan',
        'model_name': 'Altima', 'color': 'White',
        'impound_type': 'PPI', 'impound_date': d(20), 'storage_location': 'Yard A',
        'owner_name': 'Brianna Holt', 'owner_address': '303 Third St',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45402',
        'mileage': 31200, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 13500.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(19), 'heather_complete': True,
        'heather_complete_date': d(19),
        'status': 'PENDING_PICKUP',
        'pending_pickup_since': datetime.utcnow() - timedelta(hours=6),
        'storage_paid': 609.00, 'payment_date': TODAY, 'payment_reference': 'CC-4821',
        'letters': [
            {'letter_number': 1, 'due_date': d(20) + timedelta(days=5), 'sent_date': d(17),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000009',
             'delivery_confirmed_date': d(14)},
        ],
    },
    {
        # Chapter 10: closed out — released to the owner, the end of the story.
        'stock_number': 'TRAIN-10', 'vin': 'TRAIN00000000010',
        'plate': 'BCD7890', 'plate_state': 'OH', 'year': '2020', 'make': 'Hyundai',
        'model_name': 'Elantra', 'color': 'Blue',
        'impound_type': 'PPI', 'impound_date': d(45), 'storage_location': 'Yard A',
        'owner_name': 'Terrence Boyd', 'owner_address': '1450 Shroyer Rd',
        'owner_city': 'Dayton', 'owner_state': 'OH', 'owner_zip': '45419',
        'mileage': 18700, 'tow_fee': 125.00, 'daily_storage_rate': 22.00,
        'nada_value': 15900.00, 'vehicle_class': 'light',
        'bmv_stage': 'COMPLETE', 'bmv_searched_date': d(44), 'heather_complete': True,
        'heather_complete_date': d(44),
        'status': 'RELEASED',
        'released_at': datetime.utcnow() - timedelta(days=2), 'released_by': 'lawrence',
        'storage_paid': 704.00, 'payment_date': d(2), 'payment_reference': 'CC-5127',
        'letters': [
            {'letter_number': 1, 'due_date': d(45) + timedelta(days=5), 'sent_date': d(40),
             'letter_kind': 'first_notice', 'tracking_number': '1Z999AA10000000010',
             'delivery_confirmed_date': d(37)},
            {'letter_number': 2, 'due_date': d(10), 'sent_date': d(9),
             'letter_kind': 'second_notice', 'tracking_number': '1Z999AA10000000110',
             'delivery_confirmed_date': d(5)},
        ],
    },
]


def run():
    with app.app_context():
        existing = Vehicle.query.filter(Vehicle.stock_number.like(f'{STOCK_PREFIX}%')).all()
        for v in existing:
            db.session.delete(v)
        db.session.commit()
        print(f'cleared {len(existing)} existing training vehicle(s)')

        for entry in VEHICLES:
            entry = dict(entry)
            letters = entry.pop('letters', [])
            charges = entry.pop('charges', [])
            entry.setdefault('status', 'ACTIVE')

            vehicle = Vehicle(
                **entry,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.session.add(vehicle)
            db.session.flush()

            for ld in letters:
                db.session.add(CertifiedLetter(
                    vehicle_id=vehicle.id,
                    recipient_type='owner',
                    created_at=datetime.utcnow(),
                    **ld,
                ))
            for cd in charges:
                db.session.add(VehicleCharge(
                    vehicle_id=vehicle.id,
                    added_by='seed_training_baseline',
                    added_at=datetime.utcnow(),
                    **cd,
                ))
            print(f'created: {vehicle.stock_number} - {vehicle.year} {vehicle.make} {vehicle.model_name}')

        db.session.commit()

        import task_engine
        counts = task_engine.recalculate_all()
        print(f'task engine recalculated: {counts}')
        print('Done.')


if __name__ == '__main__':
    run()
