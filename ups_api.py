"""
Shared UPS REST API client — OAuth2 token, label creation, and tracking
lookups (by tracking number or by shipper reference number).

Set UPS_CLIENT_ID / UPS_CLIENT_SECRET / UPS_ACCOUNT_NUMBER in Render env vars.
All functions raise on failure (network error, missing creds, bad response) —
callers are expected to catch and flash a message, same as the VinAudit
lookup_wholesale_value pattern, except UPS has no silent fallback value since
a failed label/tracking call has no safe default to substitute.

STAGING SAFETY: staging's env vars are copied straight from production
(same UPS_CLIENT_ID/SECRET/ACCOUNT_NUMBER), and _BASE below is UPS's real,
billed production endpoint — there is no separate UPS sandbox account wired
up. Without a guard, "Create UPS Label" on a fake staging vehicle would
create a real, billed shipment on the real account. So: when IS_STAGING is
set, create_label()/void_shipment() never call UPS at all — they fabricate a
believable response instead, clearly tagged so nobody mistakes it for a real
shipment (tracking numbers start with the FAKE_TRACKING_PREFIX below).
Production is untouched — it never has IS_STAGING set, so it always takes
the real-call path exactly as before this was added.
"""

import base64
import os
import time
import requests

_BASE = 'https://onlinetools.ups.com'

_token_cache = {'token': None, 'expires_at': 0}

# Recognizable, never-real prefix for staging-fabricated tracking numbers.
# Real UPS 1Z numbers are never generated with this shape, so anything
# starting with it is unambiguously a staging fake, not a live shipment.
FAKE_TRACKING_PREFIX = '1ZFAKE'

# A tiny valid 1x1 GIF, base64-encoded — a real image so anything that tries
# to render/print the "label" doesn't choke on garbage bytes, clearly not a
# real UPS label.
_FAKE_LABEL_GIF_B64 = (
    'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBTAA7'
)


def is_configured():
    return bool(os.environ.get('UPS_CLIENT_ID') and os.environ.get('UPS_CLIENT_SECRET'))


def _is_staging():
    return os.environ.get('IS_STAGING', 'false').strip().lower() == 'true'


def _get_token():
    now = time.time()
    if _token_cache['token'] and _token_cache['expires_at'] > now + 30:
        return _token_cache['token']

    client_id = os.environ.get('UPS_CLIENT_ID', '')
    client_secret = os.environ.get('UPS_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        raise RuntimeError('UPS_CLIENT_ID / UPS_CLIENT_SECRET not configured')

    resp = requests.post(
        f'{_BASE}/security/v1/oauth/token',
        data={'grant_type': 'client_credentials'},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data['access_token']
    expires_in = int(data.get('expires_in', 3600))
    _token_cache['token'] = token
    _token_cache['expires_at'] = now + expires_in
    return token


def _headers(trans_id):
    return {
        'Authorization': f'Bearer {_get_token()}',
        'Content-Type': 'application/json',
        'transId': trans_id,
        'transactionSrc': 'impound-manager',
    }


# ── Label creation ───────────────────────────────────────────────────────────

def create_label(reference, recipient_name, recipient_address, recipient_city,
                  recipient_state, recipient_zip, trans_id):
    """Call UPS Ship API, return (tracking_number, label_b64_gif).

    On staging, fabricates the response instead — see the STAGING SAFETY note
    at the top of this file for why a real call here is never acceptable."""
    if _is_staging():
        fake_tracking = f'{FAKE_TRACKING_PREFIX}{abs(hash(trans_id)) % 10**12:012d}'
        return fake_tracking, _FAKE_LABEL_GIF_B64

    account_number = os.environ.get('UPS_ACCOUNT_NUMBER', '81Y7X1')
    company_name = os.environ.get('COMPANY_NAME', 'Broad & James Towing')

    shipper_line = '4301 E 5th Ave'
    shipper_city = 'Columbus'
    shipper_state = 'OH'
    shipper_zip = '43219'

    payload = {
        'ShipmentRequest': {
            'Shipment': {
                'Shipper': {
                    'Name': company_name,
                    'ShipperNumber': account_number,
                    'Address': {
                        'AddressLine': [shipper_line],
                        'City': shipper_city,
                        'StateProvinceCode': shipper_state,
                        'PostalCode': shipper_zip,
                        'CountryCode': 'US',
                    },
                },
                'ShipTo': {
                    'Name': recipient_name,
                    'Address': {
                        'AddressLine': [recipient_address or ''],
                        'City': recipient_city or '',
                        'StateProvinceCode': (recipient_state or 'OH')[:2],
                        'PostalCode': recipient_zip or '',
                        'CountryCode': 'US',
                    },
                },
                'ShipFrom': {
                    'Name': company_name,
                    'Address': {
                        'AddressLine': [shipper_line],
                        'City': shipper_city,
                        'StateProvinceCode': shipper_state,
                        'PostalCode': shipper_zip,
                        'CountryCode': 'US',
                    },
                },
                'Service': {'Code': '03', 'Description': 'UPS Ground'},
                # UPS's Ship API schema documents Shipment.Package as an ARRAY
                # (Shipment.Package.[]) even for a single package — sending a
                # bare object here is what caused "Missing or invalid Package
                # PackagingType Code" (confirmed 08/02/2026 against a real 400
                # on a live letter; UPS's parser couldn't find a valid package
                # entry to read PackagingType.Code out of).
                'Package': [{
                    'PackagingType': {'Code': '02', 'Description': 'Customer Supplied Package'},
                    'Dimensions': {
                        'UnitOfMeasurement': {'Code': 'IN'},
                        'Length': '9', 'Width': '6', 'Height': '1',
                    },
                    'PackageWeight': {
                        'UnitOfMeasurement': {'Code': 'LBS'},
                        'Weight': '0.1',
                    },
                    'ReferenceNumber': {'Value': (reference or '')[:35]},
                    'PackageServiceOptions': {
                        # DCISType 2 = Signature Required. Hard requirement: no
                        # signature on the label means no signed POD to fetch later.
                        'DeliveryConfirmation': {'DCISType': '2'},
                    },
                }],
                'PaymentInformation': {
                    'ShipmentCharge': {
                        'Type': '01',
                        'BillShipper': {'AccountNumber': account_number},
                    },
                },
            },
            'LabelSpecification': {
                'LabelImageFormat': {'Code': 'GIF', 'Description': 'GIF'},
            },
        },
    }

    resp = requests.post(
        f'{_BASE}/api/shipments/v1801/ship',
        json=payload,
        headers=_headers(trans_id),
        timeout=20,
    )
    if resp.status_code >= 400:
        # Surface UPS's actual reason (e.g. "Missing or Invalid Postal Code")
        # instead of the generic "400 Bad Request" raise_for_status() gives —
        # same error-body shape already parsed in void_shipment() below.
        try:
            err = (resp.json().get('response', {}).get('errors') or [{}])[0]
            message = err.get('message') or f'UPS refused (HTTP {resp.status_code})'
        except ValueError:
            message = f'UPS refused (HTTP {resp.status_code}): {resp.text[:300]}'
        raise RuntimeError(f'UPS Ship API error: {message}')
    data = resp.json()
    results = data['ShipmentResponse']['ShipmentResults']
    pkg = results['PackageResults']
    if isinstance(pkg, list):
        pkg = pkg[0]
    tracking_number = pkg['TrackingNumber']
    label_b64 = pkg['ShippingLabel']['GraphicImage']
    return tracking_number, label_b64


# ── Label void ───────────────────────────────────────────────────────────────

def void_shipment(tracking_number, trans_id):
    """Void an unshipped label via the UPS Void Shipment API. Our letters are
    single-package shipments, so the shipment identification number IS the 1Z
    tracking number.

    Returns (True, description) when UPS accepted the void, or
    (False, reason) when UPS refused — already picked up / in transit, already
    voided, or past the void window. A refusal is a normal outcome (and the
    real safety net against voiding a label that's actually moving), so it's
    surfaced as data, not an exception. Raises only on network/auth failure,
    same contract as the other calls here.

    On staging (or for any fake-prefixed tracking number, belt-and-suspenders),
    fabricates success without calling UPS — see STAGING SAFETY note above."""
    if _is_staging() or tracking_number.startswith(FAKE_TRACKING_PREFIX):
        return True, 'Voided (staging fake — no real UPS shipment ever existed)'
    resp = requests.delete(
        f'{_BASE}/api/shipments/v1/void/cancel/{tracking_number}',
        headers=_headers(trans_id),
        timeout=15,
    )
    if resp.status_code >= 400:
        try:
            err = (resp.json().get('response', {}).get('errors') or [{}])[0]
            message = err.get('message') or f'UPS refused (HTTP {resp.status_code})'
            return False, message
        except ValueError:
            resp.raise_for_status()
    data = resp.json()
    status = (((data.get('VoidShipmentResponse') or {}).get('SummaryResult') or {})
              .get('Status') or {})
    ok = status.get('Code') == '1' or 'void' in (status.get('Description') or '').lower()
    return ok, status.get('Description') or ('Voided' if ok else 'Void not confirmed by UPS')


# ── Tracking lookups ─────────────────────────────────────────────────────────

_RTS_PHRASES = (
    'return to sender', 'returned to sender', 'return to shipper',
    'returned to shipper', 'undeliverable as addressed', 'rts',
)


def _parse_package(pkg):
    """Normalize one trackResponse.shipment[].package[] entry."""
    current = pkg.get('currentStatus', {}) or {}
    status_desc = current.get('description') or current.get('simplifiedTextDescription') or ''
    activity = pkg.get('activity', []) or []

    exception_desc = None
    is_rts = False
    for act in activity:
        desc = ((act.get('status') or {}).get('description') or '').strip()
        if desc and any(p in desc.lower() for p in _RTS_PHRASES):
            is_rts = True
        act_type = ((act.get('status') or {}).get('type') or '').upper()
        if act_type == 'X' and not exception_desc:
            exception_desc = desc

    if any(p in status_desc.lower() for p in _RTS_PHRASES):
        is_rts = True

    delivered_date = None
    for dd in pkg.get('deliveryDate', []) or []:
        if dd.get('type') in ('DEL', 'RDD') and dd.get('date'):
            delivered_date = dd['date']  # YYYYMMDD string
            break

    return {
        'tracking_number': pkg.get('trackingNumber'),
        'status_code': current.get('code'),
        'status_description': status_desc,
        'is_delivered': bool(delivered_date) or 'delivered' in status_desc.lower(),
        'is_rts': is_rts,
        'exception_description': exception_desc,
        'delivered_date': delivered_date,
    }


def _parse_track_response(data):
    shipments = (data.get('trackResponse', {}) or {}).get('shipment', []) or []
    packages = []
    for shipment in shipments:
        for pkg in shipment.get('package', []) or []:
            packages.append(_parse_package(pkg))
    return packages


def lookup_by_tracking_number(tracking_number, trans_id):
    """Returns a single parsed package dict, or None if not found.

    A fake-prefixed tracking number (staging only) never existed at UPS, so
    a real lookup would just 404 — simulate an already-delivered package
    instead so the delivery-confirmation flow is still testable end to end."""
    if tracking_number.startswith(FAKE_TRACKING_PREFIX):
        today = time.strftime('%Y%m%d')
        return {
            'tracking_number': tracking_number,
            'status_code': 'D',
            'status_description': 'Delivered (staging fake — no real UPS shipment)',
            'is_delivered': True,
            'is_rts': False,
            'exception_description': None,
            'delivered_date': today,
        }
    resp = requests.get(
        f'{_BASE}/api/track/v1/details/{tracking_number}',
        headers=_headers(trans_id),
        params={'locale': 'en_US'},
        timeout=15,
    )
    resp.raise_for_status()
    packages = _parse_track_response(resp.json())
    return packages[0] if packages else None


def fetch_pod(tracking_number, trans_id):
    """Request the signed Proof of Delivery document for an already-delivered
    package (returnPOD=true on the same Track API endpoint -- no new UPS
    product, no new OAuth scope). Returns (pod_b64, pod_type), or (None, None)
    if UPS doesn't have it ready yet -- POD can lag real delivery by 7-10
    days, so a None result here is the normal/expected case for a recently-
    delivered package, not an error. Raises on a genuine API/network failure,
    same as the other lookup functions -- callers are expected to catch.

    A fake-prefixed tracking number (staging only) has no real POD to fetch --
    returns the normal "not ready yet" shape rather than calling UPS."""
    if tracking_number.startswith(FAKE_TRACKING_PREFIX):
        return None, None
    resp = requests.get(
        f'{_BASE}/api/track/v1/details/{tracking_number}',
        headers=_headers(trans_id),
        params={'locale': 'en_US', 'returnPOD': 'true'},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    shipments = (data.get('trackResponse', {}) or {}).get('shipment', []) or []
    for shipment in shipments:
        for pkg in shipment.get('package', []) or []:
            pod = (pkg.get('deliveryInformation') or {}).get('pod') or {}
            content = pod.get('content')
            if content:
                return content, 'application/pdf'  # UPS POD documents are PDF; no format field in the response
    return None, None


def lookup_by_reference(reference_number, trans_id, from_date=None, to_date=None):
    """
    Returns a list of parsed package dicts shipped under this reference number
    within the given date range (UPS defaults to the last 14 days if omitted —
    pass from_date/to_date as 'YYYYMMDD' strings to widen the window for
    backfilling older impounds).
    """
    account_number = os.environ.get('UPS_ACCOUNT_NUMBER', '81Y7X1')
    params = {'locale': 'en_US', 'shipperNum': account_number}
    if from_date:
        params['fromPickUpDate'] = from_date
    if to_date:
        params['toPickUpDate'] = to_date

    resp = requests.get(
        f'{_BASE}/api/track/v1/reference/details/{reference_number}',
        headers=_headers(trans_id),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return _parse_track_response(resp.json())
