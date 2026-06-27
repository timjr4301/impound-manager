"""Run once to load sample data: python seed.py"""
from datetime import date, datetime, timedelta
from app import app
from models import db, Vehicle, CertifiedLetter, TitleFiling

today = date.today()

SAMPLES = [
    # --- OVERDUE: letter 1 never sent, 8 days ago ---
    dict(
        vin='1HGBH41JXMN109186', plate='FZX4821', plate_state='OH',
        year=2019, make='Honda', model_name='Civic', color='Blue',
        impound_type='PPI', impound_date=today - timedelta(days=8),
        storage_location='Row A, Spot 3',
        letters=[dict(letter_number=1, due_date=today - timedelta(days=3))],
    ),
    # --- DUE TODAY: letter 1 due today ---
    dict(
        vin='2T1BURHE0JC043821', plate='GKP2244', plate_state='OH',
        year=2021, make='Toyota', model_name='Corolla', color='Silver',
        impound_type='PPI', impound_date=today - timedelta(days=5),
        storage_location='Row B, Spot 7',
        letters=[dict(letter_number=1, due_date=today)],
    ),
    # --- DUE TODAY: police impound, notification due today ---
    dict(
        plate='HRT9901', plate_state='OH',
        year=2017, make='Ford', model_name='F-150', color='Black',
        impound_type='POLICE', impound_date=today - timedelta(days=10),
        police_report_number='CPD-2026-044821',
        storage_location='Row D, Spot 1',
        letters=[dict(letter_number=1, due_date=today)],
    ),
    # --- Letter 1 sent, letter 2 due in 3 days ---
    dict(
        vin='3VWFE21C04M000001', plate='JLM5530', plate_state='OH',
        year=2020, make='Volkswagen', model_name='Jetta', color='White',
        impound_type='PPI', impound_date=today - timedelta(days=35),
        storage_location='Row C, Spot 12',
        letters=[
            dict(letter_number=1, due_date=today - timedelta(days=30),
                 sent_date=today - timedelta(days=27), tracking_number='9400111899223456789012'),
            dict(letter_number=2, due_date=today + timedelta(days=3)),
        ],
    ),
    # --- Both letters sent, READY TO FILE ---
    dict(
        vin='5YJSA1DG9DFP14705', plate='KBP3310', plate_state='IN',
        year=2018, make='Tesla', model_name='Model S', color='Red',
        impound_type='PPI', impound_date=today - timedelta(days=70),
        storage_location='Row A, Spot 9',
        letters=[
            dict(letter_number=1, due_date=today - timedelta(days=65),
                 sent_date=today - timedelta(days=63), tracking_number='9400111899223456780001'),
            dict(letter_number=2, due_date=today - timedelta(days=33),
                 sent_date=today - timedelta(days=31), tracking_number='9400111899223456780002'),
        ],
    ),
    # --- Police impound, letter sent, ready to file ---
    dict(
        plate='MNX7712', plate_state='OH',
        year=2015, make='Chevrolet', model_name='Malibu', color='Gray',
        impound_type='POLICE', impound_date=today - timedelta(days=45),
        police_report_number='CPD-2026-039100',
        storage_location='Row E, Spot 2',
        letters=[
            dict(letter_number=1, due_date=today - timedelta(days=35),
                 sent_date=today - timedelta(days=33), tracking_number='9400111899223456780003'),
        ],
    ),
    # --- Coming up this week: letter 2 due in 5 days ---
    dict(
        vin='1FADP3F28EL123456', plate='PLQ8843', plate_state='KY',
        year=2016, make='Ford', model_name='Focus', color='Green',
        impound_type='PPI', impound_date=today - timedelta(days=28),
        storage_location='Row B, Spot 15',
        letters=[
            dict(letter_number=1, due_date=today - timedelta(days=23),
                 sent_date=today - timedelta(days=22), tracking_number='9400111899223456780004'),
            dict(letter_number=2, due_date=today + timedelta(days=5)),
        ],
    ),
    # --- Waiting: letter 1 sent yesterday, letter 2 in 29 days ---
    dict(
        plate='SRT4401', plate_state='OH',
        year=2014, make='Dodge', model_name='Charger', color='Purple',
        impound_type='PPI', impound_date=today - timedelta(days=6),
        storage_location='Row F, Spot 8',
        letters=[
            dict(letter_number=1, due_date=today - timedelta(days=1),
                 sent_date=today - timedelta(days=1), tracking_number='9400111899223456780005'),
            dict(letter_number=2, due_date=today + timedelta(days=29)),
        ],
    ),
]

with app.app_context():
    # Wipe existing data
    TitleFiling.query.delete()
    CertifiedLetter.query.delete()
    Vehicle.query.delete()
    db.session.commit()

    for s in SAMPLES:
        letters_data = s.pop('letters', [])
        v = Vehicle(
            **s,
            status='ACTIVE',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(v)
        db.session.flush()

        for ld in letters_data:
            db.session.add(CertifiedLetter(
                vehicle_id=v.id,
                created_at=datetime.utcnow(),
                **ld,
            ))

    db.session.commit()
    print(f"Seeded {len(SAMPLES)} vehicles.")
