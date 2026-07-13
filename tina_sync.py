"""
Tina pipeline sync — push vehicles to Base44 Tina Tracker, handle possible releases.

Environment variables required:
  BASE44_URL     — base URL of the Base44 app (e.g. https://api.base44.com/api/apps/<app_id>)
  BASE44_API_KEY — API key for the Base44 app
"""
import logging
import os
from datetime import datetime

import requests

from models import db, Vehicle, VehicleNote

log = logging.getLogger(__name__)

_BASE44_APP_ID = os.environ.get('BASE44_APP_ID', '')
_BASE44_URL = (
    os.environ.get('BASE44_URL', '').rstrip('/')
    or (f'https://api.base44.com/api/apps/{_BASE44_APP_ID}' if _BASE44_APP_ID else '')
)
_BASE44_API_KEY = os.environ.get('BASE44_API_KEY', '')


def _headers():
    return {'Content-Type': 'application/json', 'api_key': _BASE44_API_KEY}


def push_vehicle_to_tina(vehicle):
    """
    Called when a vehicle is marked 'title_obtained' in Impound Manager.
    Creates a Vehicle record in Base44 at current_stage='title_received'.
    Stores the returned Base44 ID on vehicle.base44_id.
    Fails silently with logging — never blocks Impound Manager workflow.
    Returns the Base44 record ID string, or None on failure.
    """
    if not _BASE44_URL or not _BASE44_API_KEY:
        log.warning('push_vehicle_to_tina: BASE44_URL or BASE44_API_KEY not set — skipping')
        return None

    payload = {
        'year': str(vehicle.year) if vehicle.year else 'Unknown',
        'make': vehicle.make or 'Unknown',
        'model': vehicle.model_name or vehicle.model or 'Unknown',
        'vin': vehicle.vin,
        'vin_last4': vehicle.vin[-4:] if vehicle.vin and len(vehicle.vin) >= 4 else '',
        'color': vehicle.color,
        'stock_number': vehicle.stock_number,
        'current_stage': 'title_received',
        'vehicle_location_status': 'on_lot',
        'title_notes': f'Pushed from Impound Manager. Stock #{vehicle.stock_number}',
    }

    try:
        resp = requests.post(
            f'{_BASE44_URL}/entities/Vehicle',
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        base44_id = str(data.get('id') or data.get('_id') or '')
        if base44_id:
            vehicle.base44_id = base44_id
            db.session.add(VehicleNote(
                vehicle_id=vehicle.id,
                body=f'Pushed to Tina pipeline (Base44 ID: {base44_id}).',
                author='System',
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
        log.info(f'push_vehicle_to_tina: vehicle {vehicle.id} → Base44 {base44_id}')
        return base44_id or None
    except Exception as exc:
        log.error(f'push_vehicle_to_tina: failed for vehicle {vehicle.id}: {exc}')
        return None


def check_possible_releases(current_stock_numbers):
    """
    Called after CSV import. Takes the list of stock numbers present in the new CSV.
    Returns all ACTIVE vehicles in the DB whose stock_number is NOT in that list —
    these are candidates for 'Possible Release — Verify Before Sending Letter'.
    Already-flagged vehicles are excluded so they aren't double-noted.
    """
    if not current_stock_numbers:
        return []
    stock_set = {s.strip() for s in current_stock_numbers if s}
    candidates = (
        Vehicle.query
        .filter_by(status='ACTIVE')
        .filter(Vehicle.stock_number.isnot(None))
        .filter(Vehicle.possible_release.isnot(True))
        .all()
    )
    return [v for v in candidates if v.stock_number not in stock_set]


def flag_vehicle_possible_release(vehicle_id):
    """
    Marks a vehicle as possible_release=True.
    Heather must confirm before any letters are sent.
    Returns True on success, False if vehicle not found.
    """
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return False
    vehicle.possible_release = True
    vehicle.updated_at = datetime.utcnow()
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body='Flagged as Possible Release — not in latest Towbook CSV. Verify before sending letters.',
        author='System',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    return True


def confirm_still_on_lot(vehicle_id, actor=None):
    """
    Clears the possible_release flag — staff confirmed the vehicle is still on lot.
    `actor` is a username; when given, the audit note is attributed to them.
    Returns True on success, False if vehicle not found.
    """
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return False
    vehicle.possible_release = False
    vehicle.updated_at = datetime.utcnow()
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=(f'Verified still on lot by {actor} on '
              f'{datetime.utcnow().strftime("%m/%d/%Y")}'
              if actor else
              'Confirmed still on lot — possible release flag cleared.'),
        author=actor or 'System',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    return True


def mark_released(vehicle_id, actor=None):
    """
    Marks vehicle as RELEASED in Impound Manager and clears the possible_release flag.
    Does NOT push to Tina — released vehicles without a title don't go to her pipeline.
    `actor` is a username; when given, the audit note is attributed to them.
    Returns True on success, False if vehicle not found.
    """
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return False
    prior_status = vehicle.status
    now = datetime.utcnow()
    vehicle.status = 'RELEASED'
    vehicle.possible_release = False
    vehicle.released_at = now
    vehicle.released_by = actor or 'System'
    vehicle.updated_at = now
    if actor:
        body = (f'Confirmed released by {actor} on '
                f'{datetime.utcnow().strftime("%m/%d/%Y")}')
        # PENDING_PICKUP skips its Confirm Picked Up step when resolved this way —
        # record it so the jump is visible in the audit trail.
        if prior_status == 'PENDING_PICKUP':
            body += ' (was PENDING_PICKUP — pickup confirmation bypassed)'
    else:
        body = 'Vehicle marked as released.'
    db.session.add(VehicleNote(
        vehicle_id=vehicle.id,
        body=body,
        author=actor or 'System',
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    return True
