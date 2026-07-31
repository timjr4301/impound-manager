"""
WP-4: strip real customer image/document data out of a database that a
Towbook-pg_dump copy just landed in, before it's used as the staging DB.

Only ever touches the database this script's own DATABASE_URL points to —
same convention as every other one-off script in this repo. It never accepts
a second connection string, so there's no way for a bug here to reach across
and touch a different database. It still refuses to run at all without an
explicit flag, and prints exactly which database host it's about to modify
so you can check it's staging before confirming.

Nulls out (or empties, where the column is NOT NULL) every base64/binary
image and document blob: Vehicle VIN/converter photos, envelope scan images
+ their returned/POD images on CertifiedLetter, driver damage-report
signatures/PDFs/photos, general documents, and the legacy UPS label cache.
Everything else (vehicle records, letters, notes, dates) is left intact —
staging needs real-looking data to test against, just not real photos of
real people's cars and paperwork.

Run reset_users.py afterward to put passwords back to the documented
defaults (that script already does exactly that — no need to duplicate it
here).

    [STAGING RENDER SHELL ONLY] python3 scrub_for_staging.py --confirm-staging
"""
import sys

from app import app
from models import (
    db, Vehicle, CertifiedLetter, EnvelopeScan, DamageReport, DamagePhoto,
    VehicleNotice, VehicleDocument, GeneralDocument, VehicleDamagePhoto,
)


def main():
    if '--confirm-staging' not in sys.argv:
        print('Refusing to run without --confirm-staging. This blanks out real '
              'image/document data — only ever run it against a staging database.')
        sys.exit(1)

    with app.app_context():
        db_url = app.config['SQLALCHEMY_DATABASE_URI']
        host = db_url.split('@')[-1].split('/')[0] if '@' in db_url else db_url
        print(f'About to scrub blobs in: {host}')
        print('Ctrl+C now if this is not the staging database.\n')

        counts = {}

        counts['Vehicle photos'] = Vehicle.query.filter(
            db.or_(Vehicle.converter_photo.isnot(None),
                   Vehicle.vin_door_jamb_photo.isnot(None),
                   Vehicle.vin_dash_photo.isnot(None))
        ).update({
            Vehicle.converter_photo: None,
            Vehicle.vin_door_jamb_photo: None,
            Vehicle.vin_dash_photo: None,
        }, synchronize_session=False)

        counts['CertifiedLetter images'] = CertifiedLetter.query.filter(
            db.or_(CertifiedLetter.returned_envelope_image_data.isnot(None),
                   CertifiedLetter.pod_image_data.isnot(None),
                   CertifiedLetter.pod_image_data_2.isnot(None))
        ).update({
            CertifiedLetter.returned_envelope_image_data: None,
            CertifiedLetter.pod_image_data: None,
            CertifiedLetter.pod_image_data_2: None,
        }, synchronize_session=False)

        counts['EnvelopeScan images'] = EnvelopeScan.query.filter(
            EnvelopeScan.image_data.isnot(None)
        ).update({EnvelopeScan.image_data: None}, synchronize_session=False)

        counts['DamageReport signatures/PDFs'] = DamageReport.query.filter(
            db.or_(DamageReport.signature_data.isnot(None),
                   DamageReport.pdf_data.isnot(None))
        ).update({
            DamageReport.signature_data: None,
            DamageReport.pdf_data: None,
        }, synchronize_session=False)

        counts['DamagePhoto images'] = DamagePhoto.query.filter(
            DamagePhoto.image_data.isnot(None)
        ).update({DamagePhoto.image_data: None}, synchronize_session=False)

        counts['VehicleNotice labels'] = VehicleNotice.query.filter(
            VehicleNotice.label_data.isnot(None)
        ).update({VehicleNotice.label_data: None}, synchronize_session=False)

        # These three columns are NOT NULL — empty instead of None.
        counts['VehicleDocument files'] = VehicleDocument.query.update(
            {VehicleDocument.file_data: b''}, synchronize_session=False)
        counts['GeneralDocument files'] = GeneralDocument.query.update(
            {GeneralDocument.file_data: ''}, synchronize_session=False)
        counts['VehicleDamagePhoto images'] = VehicleDamagePhoto.query.update(
            {VehicleDamagePhoto.image_data: ''}, synchronize_session=False)

        for label, n in counts.items():
            print(f'  {label}: {n} row(s) scrubbed')

        db.session.commit()
        print('\nCommitted. Now run: python3 reset_users.py')


if __name__ == '__main__':
    main()
