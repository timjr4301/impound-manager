"""
Shared UPS REST API client — OAuth2 token, label creation, and tracking
lookups (by tracking number or by shipper reference number).

Set UPS_CLIENT_ID / UPS_CLIENT_SECRET / UPS_ACCOUNT_NUMBER in Render env vars.
All functions raise on failure (network error, missing creds, bad response) —
callers are expected to catch and flash a message, same as the VinAudit
lookup_wholesale_value pattern, except UPS has no silent fallback value since
a failed label/tracking call has no safe default to substitute.
"""

import os
import time
import requests

_BASE = 'https://onlinetools.ups.com'

_token_cache = {'token': None, 'expires_at': 0}


def is_configured():
    return bool(os.environ.get('UPS_CLIENT_ID') and os.environ.get('UPS_CLIENT_SECRET'))


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
    """Call UPS Ship API, return (tracking_number, label_b64_gif)."""
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
                'Package': {
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
                },
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
    resp.raise_for_status()
    data = resp.json()
    results = data['ShipmentResponse']['ShipmentResults']
    pkg = results['PackageResults']
    if isinstance(pkg, list):
        pkg = pkg[0]
    tracking_number = pkg['TrackingNumber']
    label_b64 = pkg['ShippingLabel']['GraphicImage']
    return tracking_number, label_b64


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
    """Returns a single parsed package dict, or None if not found."""
    resp = requests.get(
        f'{_BASE}/api/track/v1/details/{tracking_number}',
        headers=_headers(trans_id),
        params={'locale': 'en_US'},
        timeout=15,
    )
    resp.raise_for_status()
    packages = _parse_track_response(resp.json())
    return packages[0] if packages else None


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
