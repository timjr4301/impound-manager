"""
Towbook direct API connector.

Towbook provides token-based API access for third-party integrations (same
mechanism used by Polytomic, Samsara, etc.).

To enable automatic 5 AM daily sync set these environment variables:

  TOWBOOK_API_TOKEN   — API token from Towbook (Settings → Integrations → API)
  TOWBOOK_COMPANY_ID  — Your numeric Towbook company ID (shown in account URL)
  TOWBOOK_API_BASE    — (optional) override base URL; default https://app.towbook.com/api

How to get credentials:
  1. Log into Towbook → Settings → Integrations
  2. If no "API" section is visible, email support@towbook.com (810-320-5063)
     and ask for "API token for third-party impound call export integration"
  3. Set the env vars in Render → Environment → Add Environment Variable

Once TOWBOOK_API_TOKEN and TOWBOOK_COMPANY_ID are set the 5 AM scheduler
job switches from "alert_pending" mode to live API pull automatically.
No code changes needed.
"""

import os
from datetime import date, datetime, timedelta

TOWBOOK_API_BASE = os.environ.get('TOWBOOK_API_BASE', 'https://app.towbook.com/api')
TOWBOOK_API_TOKEN = os.environ.get('TOWBOOK_API_TOKEN', '')
TOWBOOK_COMPANY_ID = os.environ.get('TOWBOOK_COMPANY_ID', '')

# How many days back to fetch on each auto-sync
SYNC_LOOKBACK_DAYS = int(os.environ.get('TOWBOOK_SYNC_LOOKBACK_DAYS', '90'))


def is_configured():
    """Return True only when both required env vars are present."""
    return bool(TOWBOOK_API_TOKEN and TOWBOOK_COMPANY_ID)


def _headers():
    return {
        'Authorization': f'Bearer {TOWBOOK_API_TOKEN}',
        'X-Company-Id': TOWBOOK_COMPANY_ID,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'ImpoundManager/1.0 (Broad-James-Towing)',
    }


def _to_date(val):
    """Parse a variety of date string formats into a date object."""
    if not val:
        return None
    s = str(val).strip()
    for fmt in (
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%m/%d/%Y %I:%M %p',
        '%m/%d/%Y',
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).date()
    except (ValueError, TypeError):
        return None


def _money(val):
    if val is None:
        return None
    try:
        import re
        return float(re.sub(r'[$,\s]', '', str(val)))
    except (ValueError, TypeError):
        return None


def _map_call(call):
    """
    Map a Towbook API call/vehicle object to our Vehicle field dict.

    Towbook field names are camelCase in their API responses.  If the exact
    names differ from what Towbook returns, update the get() chains here after
    inspecting a real API response.  Print/log the raw `call` dict on first
    run to verify field names.
    """
    stock = (
        call.get('stockNumber') or call.get('stock_number') or
        call.get('StockNumber') or str(call.get('id', ''))
    )

    year_val = call.get('year') or call.get('vehicleYear') or call.get('Year')
    try:
        year = int(year_val) if year_val else None
    except (ValueError, TypeError):
        year = None

    have_keys_raw = call.get('haveKeys') or call.get('keys') or call.get('HaveKeys') or ''
    have_keys = str(have_keys_raw).lower() in ('true', 'yes', '1', 'y')

    return {
        'stock_number':     str(stock) if stock else None,
        'call_number':      call.get('callNumber') or call.get('callId'),
        'invoice_number':   call.get('invoiceNumber') or call.get('invoiceId'),
        'account':          call.get('account') or call.get('accountName') or call.get('towingAccount'),
        'color':            call.get('color') or call.get('vehicleColor') or call.get('Color'),
        'make':             call.get('make') or call.get('vehicleMake') or call.get('Make'),
        'model':            call.get('model') or call.get('vehicleModel') or call.get('Model'),
        'year':             year,
        'plate':            call.get('licensePlate') or call.get('plate') or call.get('Plate'),
        'plate_state':      call.get('licenseState') or call.get('plateState') or call.get('PlateState'),
        'vin':              call.get('vin') or call.get('vehicleVin') or call.get('Vin') or call.get('VIN'),
        'impound_reason':   (call.get('serviceType') or call.get('reason') or
                             call.get('callType') or call.get('ImpoundReason')),
        'impound_date':     _to_date(
                                call.get('impoundDate') or call.get('callDate') or
                                call.get('date') or call.get('ImpoundDate')
                            ),
        'storage_location': (call.get('storageLot') or call.get('lot') or
                             call.get('location') or call.get('StorageLot')),
        'have_keys':        have_keys,
        'balance_due':      _money(call.get('balanceDue') or call.get('balance') or call.get('BalanceDue')),
        'last_synced':      datetime.utcnow(),
    }


def fetch_calls(since_date=None):
    """
    Pull impound calls from the Towbook API.

    Returns a list of raw call dicts.  Raises RuntimeError / requests.HTTPError
    on failure.

    NOTE: The exact endpoint path and query-param names below reflect the
    token-auth pattern documented in Polytomic's Towbook connector.  After
    obtaining credentials, test with:
        curl -H "Authorization: Bearer $TOWBOOK_API_TOKEN" \
             -H "X-Company-Id: $TOWBOOK_COMPANY_ID" \
             https://app.towbook.com/api/v1/calls?status=impound&limit=500
    and update the URL/params here if Towbook uses different names.
    """
    import requests

    if not is_configured():
        raise RuntimeError('TOWBOOK_API_TOKEN and TOWBOOK_COMPANY_ID are not set')

    params = {
        'status': 'impound',
        'limit': 500,
    }
    if since_date:
        params['from'] = since_date.strftime('%Y-%m-%d')

    url = f'{TOWBOOK_API_BASE}/v1/calls'
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)

    if resp.status_code == 401:
        raise RuntimeError('Towbook API: authentication failed — check TOWBOOK_API_TOKEN')
    if resp.status_code == 403:
        raise RuntimeError('Towbook API: permission denied — check TOWBOOK_COMPANY_ID')
    resp.raise_for_status()

    data = resp.json()
    # Handle array response or common envelope shapes
    if isinstance(data, list):
        return data
    for key in ('calls', 'data', 'results', 'items', 'records'):
        if key in data and isinstance(data[key], list):
            return data[key]
    return []


def upsert_calls(calls):
    """
    Upsert a list of raw Towbook call dicts into the vehicles table.
    Returns (inserted, updated, skipped, errors, stock_numbers_seen).

    stock_numbers_seen is every stock number present in this pull (whether
    inserted, updated, or already RELEASED) — the caller cross-references it
    against ACTIVE vehicles in our DB the same way towbook_import.py's CSV
    path already does, to catch a vehicle Towbook no longer lists at all.
    """
    from models import db, Vehicle

    inserted = updated = skipped = 0
    errors = []
    stock_numbers_seen = []

    for call in calls:
        try:
            fields = _map_call(call)
            stock = fields.get('stock_number')
            if not stock:
                skipped += 1
                continue
            stock_numbers_seen.append(stock)

            # Speculative field names, same caveat as _map_call above — untested
            # against a real Towbook API response. Only matters if Towbook's
            # /v1/calls endpoint ever includes recently-closed calls; if it only
            # returns open impounds (status=impound), a released vehicle simply
            # stops appearing at all and is caught by the possible-release check
            # in run_auto_sync() instead.
            release_date = _to_date(
                call.get('releaseDate') or call.get('release_date') or
                call.get('ReleaseDate') or call.get('releasedDate')
            )

            existing = Vehicle.query.filter_by(stock_number=stock).first()
            if existing:
                for k, v in fields.items():
                    if v is not None:
                        setattr(existing, k, v)
                if release_date and existing.status == 'ACTIVE':
                    existing.status = 'RELEASED'
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                if not fields.get('impound_date'):
                    skipped += 1
                    continue
                vehicle = Vehicle(
                    **fields,
                    impound_type='PPI',
                    status='RELEASED' if release_date else 'ACTIVE',
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.session.add(vehicle)
                inserted += 1

        except Exception as exc:
            errors.append({
                'stock': str(call.get('stockNumber', call.get('id', '?'))),
                'error': str(exc),
            })

    db.session.commit()
    return inserted, updated, skipped, errors, stock_numbers_seen


def run_auto_sync():
    """
    Full API-based sync: fetch → upsert → flag possible releases → recalculate
    task pipeline. Returns a result dict. Raises on API failure.
    """
    since = date.today() - timedelta(days=SYNC_LOOKBACK_DAYS)
    calls = fetch_calls(since_date=since)

    inserted, updated, skipped, errors, stock_numbers_seen = upsert_calls(calls)

    # Cross-reference against active records — same check the manual CSV
    # import already runs, previously missing from this API path entirely.
    possible_release_count = 0
    try:
        from tina_sync import check_possible_releases, flag_vehicle_possible_release
        for v in check_possible_releases(stock_numbers_seen):
            flag_vehicle_possible_release(v.id)
            possible_release_count += 1
    except Exception as exc:
        print(f'[towbook_api] possible-release check failed: {exc}')

    from task_engine import recalculate_all
    urgency_counts = recalculate_all()

    return {
        'ok': True,
        'source': 'api_auto',
        'call_count': len(calls),
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'errors': errors,
        'possible_releases_flagged': possible_release_count,
        'urgency': urgency_counts,
        'synced_at': datetime.utcnow().isoformat(),
    }
