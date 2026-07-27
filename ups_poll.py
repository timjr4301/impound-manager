"""UPS delivery-tracking poll — the single source of truth for the per-letter
refresh, the signed-POD pull, and the in-flight sweep.

Used by BOTH the manual "Refresh from UPS" / "Refresh all tracking" buttons and
the scheduled auto-poll job (app.py::_start_scheduler), so both behave
identically — no parallel copy of the delivery/RTS/POD logic to drift apart.

Every function assumes it is running inside a Flask app context (it touches the
db session). The route callers already have a request context; the scheduled
job wraps its call in `with app.app_context():`.
"""
from datetime import datetime

import ups_api
from models import db, Vehicle, CertifiedLetter, UpsPollLog


def try_fetch_pod(letter, tracking_number, which, trans_id):
    """Best-effort POD pull for one tracking number on a letter -- swallows
    all errors (network, missing creds, UPS not ready yet). Returns True if
    a new POD was actually stored, so callers can decide whether to commit/
    flash. 'which' is 'primary' or '2nd'; never raises."""
    try:
        pod_b64, pod_type = ups_api.fetch_pod(tracking_number, trans_id=trans_id)
    except Exception:
        return False
    if not pod_b64:
        return False
    if which == 'primary':
        letter.pod_image_data = pod_b64
        letter.pod_image_type = pod_type
    else:
        letter.pod_image_data_2 = pod_b64
        letter.pod_image_type_2 = pod_type
    return True


def refresh_letter_tracking(letter):
    """Poll UPS for one letter's tracking number(s), updating delivery/RTS
    status and pulling any newly-available POD(s). Returns a dict describing
    what changed. Does NOT commit, flash, or redirect — callers do that.
    Never raises; UPS/network errors are captured in result['error']."""
    result = {'checked': False, 'newly_delivered': False, 'newly_returned': False,
              'pods_pulled': 0, 'error': None, 'no_record': False}
    if not letter.tracking_number:
        result['error'] = 'no tracking number'
        return result
    try:
        pkg = ups_api.lookup_by_tracking_number(letter.tracking_number, trans_id=f'refresh-{letter.id}')
    except Exception as exc:
        result['error'] = str(exc)
        return result
    if not pkg:
        result['no_record'] = True
        return result

    result['checked'] = True
    letter.ups_status = pkg['status_description'] or letter.ups_status
    was_delivered = letter.delivery_confirmed_date is not None
    was_rts = letter.return_to_sender
    if pkg['is_rts']:
        letter.return_to_sender = True
        if not was_rts:
            result['newly_returned'] = True
    elif pkg['is_delivered'] and pkg['delivered_date']:
        letter.delivery_confirmed_date = datetime.strptime(pkg['delivered_date'], '%Y%m%d').date()
        if not was_delivered:
            result['newly_delivered'] = True

    if letter.delivery_confirmed_date and not letter.pod_image_data:
        if try_fetch_pod(letter, letter.tracking_number, 'primary', trans_id=f'pod-auto-{letter.id}'):
            result['pods_pulled'] += 1

    if letter.tracking_number_2 and not letter.pod_image_data_2:
        try:
            pkg2 = ups_api.lookup_by_tracking_number(letter.tracking_number_2, trans_id=f'refresh2-{letter.id}')
        except Exception:
            pkg2 = None
        if pkg2 and pkg2['is_delivered']:
            if try_fetch_pod(letter, letter.tracking_number_2, '2nd', trans_id=f'pod-auto-{letter.id}-2nd'):
                result['pods_pulled'] += 1
    return result


def _in_flight_letters():
    """Certified letters still worth polling: a primary tracking number, on an
    ACTIVE non-ghost vehicle, not yet fully wrapped up (awaiting delivery,
    delivered but POD not yet retrieved, or a 2nd-party label missing its POD).
    Return-to-sender letters are terminal and skipped."""
    return (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release == False)
        .filter(CertifiedLetter.tracking_number.isnot(None))
        .filter(CertifiedLetter.return_to_sender == False)
        .filter(db.or_(
            CertifiedLetter.delivery_confirmed_date.is_(None),
            CertifiedLetter.pod_image_data.is_(None),
            db.and_(CertifiedLetter.tracking_number_2.isnot(None),
                    CertifiedLetter.pod_image_data_2.is_(None)),
        ))
        .all()
    )


def sweep(triggered_by):
    """Refresh every in-flight letter, log a UpsPollLog run, commit. Returns a
    counts dict {checked, delivered, returned, pods, errors}. Shared by the
    manual 'Refresh all' button and the scheduled auto-poll."""
    checked = delivered = returned = pods = errors = 0
    for letter in _in_flight_letters():
        r = refresh_letter_tracking(letter)
        if r['error']:
            errors += 1
            continue
        if r['no_record']:
            continue
        checked += 1
        if r['newly_delivered']:
            delivered += 1
        if r['newly_returned']:
            returned += 1
        pods += r['pods_pulled']

    db.session.add(UpsPollLog(
        triggered_by=triggered_by,
        letters_checked=checked,
        newly_delivered=delivered,
        newly_returned=returned,
        pods_pulled=pods,
        errors=errors,
    ))
    db.session.commit()
    return dict(checked=checked, delivered=delivered, returned=returned,
                pods=pods, errors=errors)


def run_scheduled(app):
    """Entry point for the APScheduler auto-poll job (see _start_scheduler).
    Wraps the sweep in an app context and never lets an exception escape into
    the scheduler thread."""
    try:
        with app.app_context():
            counts = sweep('auto-poll')
            print(f'[ups-poll] auto-poll: {counts}')
    except Exception as exc:
        print(f'[ups-poll] auto-poll failed: {exc}')
