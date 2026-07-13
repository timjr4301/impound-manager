"""
Shared disposition-pipeline mutation helpers — stage moves and chain-of-custody
logging — used by both Tina's office board (blueprints/tina.py) and the field
crew screens (blueprints/field_ops.py). Every mutation drops a CustodyEvent so
'where has this car and its key been, and who touched it' is always answerable.
"""
from datetime import date, datetime
from flask_login import current_user
from models import db, VehicleNote, CustodyEvent
import disposition as dispo


def actor():
    if current_user.is_authenticated:
        return current_user.display_name or current_user.username
    return 'system'


def record_custody(vehicle, event_type, detail, who=None):
    """Append one row to the chain-of-custody / audit trail. Does not commit."""
    db.session.add(CustodyEvent(
        vehicle_id=vehicle.id,
        event_type=event_type,
        detail=detail,
        actor=who or actor(),
        created_at=datetime.utcnow(),
    ))


def move_stage(vehicle, new_stage, note=''):
    """Move a vehicle to a pipeline stage: sets the stage, stamps the entry time
    (days-in-stage), syncs disposition when the stage implies a track, and drops
    an audit + custody note. Does not commit."""
    vehicle.tina_stage = new_stage
    vehicle.tina_stage_at = datetime.utcnow()
    implied = dispo.disposition_for_stage(new_stage)
    if implied and vehicle.disposition != implied:
        vehicle.disposition = implied
        vehicle.disposition_set_date = date.today()
    vehicle.updated_at = datetime.utcnow()
    label = dispo.STAGE_LABELS.get(new_stage, new_stage)
    body = f'Pipeline → {label}' + (f': {note}' if note else '')
    db.session.add(VehicleNote(vehicle_id=vehicle.id, body=body,
                               author=actor(), created_at=datetime.utcnow()))
    record_custody(vehicle, 'stage', f'Moved to {label}' + (f' — {note}' if note else ''))


def set_car_location(vehicle, location, note=''):
    """Record the car's current physical location (chain of custody)."""
    vehicle.custody_location = location
    vehicle.custody_location_by = actor()
    vehicle.custody_location_at = datetime.utcnow()
    record_custody(vehicle, 'car_move', f'Car at {location}' + (f' — {note}' if note else ''))


def set_key_location(vehicle, key_loc, note=''):
    """Record where the key is now (chain of custody)."""
    vehicle.key_location = key_loc
    vehicle.key_location_by = actor()
    vehicle.key_location_at = datetime.utcnow()
    label = dispo.KEY_LOCATION_LABELS.get(key_loc, key_loc)
    record_custody(vehicle, 'key_move', f'Key: {label}' + (f' — {note}' if note else ''))
