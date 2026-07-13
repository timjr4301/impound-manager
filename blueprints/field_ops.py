"""
Field crew screens for the disposition pipeline — deliberately bare, mobile-
first, dark-themed (same look as /driver) so the wheel-lift drivers and the key
maker will actually use them.

Increment 1 — the wheel-lift driver "Find & Assess" flow:
  /field/            landing (what each crew touches)
  /field/find        the Find List — title-obtained cars to locate on the lot
  /field/assess/<id> record converter + junk/auction call + car location

Junk is a *recommendation* here (routes to Junk — Pending); Tina gives the
Ohio Steel sign-off from the office board.
"""
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Vehicle
import disposition as dispo
import pipeline_ops as ops

bp = Blueprint('field_ops', __name__, url_prefix='/field')

# Physical areas a driver can drop a car into (chain of custody).
CAR_AREAS = [
    'Storage Lot (4301)', 'Key Row', 'Inspection Pool (4301)',
    'Online Auction Row (4301)', 'Live Auction Lot (3865)',
    'PPI (3865)', 'Ohio Steel', 'Other',
]


def _field_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not (current_user.is_dispatcher or current_user.is_tina):
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


def _key_maker_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_key_maker:
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


# Common key types (drives the cost hint on the key screen).
KEY_TYPES = ['Generic', 'BMW', 'Mercedes', 'Audi/VW', 'Push-to-start / fob', 'Other']


@bp.route('/')
@_field_required
def index():
    find_count = (Vehicle.query.filter_by(tina_stage='TO_LOCATE')
                  .filter(Vehicle.possible_release.isnot(True)).count())
    return render_template('field_ops/index.html', find_count=find_count)


@bp.route('/find')
@_field_required
def find():
    """The Find List — cars whose titles are in hand, waiting to be located and
    assessed on the lot."""
    vehicles = (
        Vehicle.query
        .filter_by(tina_stage='TO_LOCATE')
        .filter(Vehicle.possible_release.isnot(True))
        .order_by(Vehicle.tina_stage_at.asc().nullslast())
        .all()
    )
    return render_template('field_ops/find.html', vehicles=vehicles)


@bp.route('/assess/<int:vehicle_id>', methods=['GET', 'POST'])
@_field_required
def assess(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    if request.method == 'POST':
        converter = request.form.get('converter', '')          # 'yes' | 'no'
        call = request.form.get('call', '')                    # 'auction' | 'junk'
        area = request.form.get('area', '').strip()
        photo = request.form.get('converter_photo', '').strip()
        notes = request.form.get('notes', '').strip()

        if converter not in ('yes', 'no') or call not in ('auction', 'junk'):
            flash('Please mark the converter and the junk/auction call.', 'danger')
            return redirect(url_for('field_ops.assess', vehicle_id=vehicle_id))

        # Converter documentation (Ohio Steel evidence)
        vehicle.converter_present = (converter == 'yes')
        vehicle.converter_checked_by = ops.actor()
        vehicle.converter_checked_at = datetime.utcnow()
        vehicle.converter_photo = photo or None
        vehicle.converter_notes = notes or None
        ops.record_custody(
            vehicle, 'converter',
            f'Converter {"PRESENT" if converter == "yes" else "MISSING"}'
            + (f' — {notes}' if notes else ''))

        # Where the driver left it
        if area:
            ops.set_car_location(vehicle, area)

        # The call routes the car onto its track
        if call == 'auction':
            ops.move_stage(vehicle, 'KEY_ROW', note='driver called it auction')
            msg = f'{vehicle.display_name} → Key Row (auction).'
        else:
            ops.move_stage(vehicle, 'JUNK_PENDING', note='driver called it junk')
            msg = f'{vehicle.display_name} → Junk (pending Tina).'

        db.session.commit()
        flash(msg, 'success')
        return redirect(url_for('field_ops.find'))

    return render_template('field_ops/assess.html', vehicle=vehicle, car_areas=CAR_AREAS)


# ── Key Row (Robert the key maker) ──────────────────────────────────────────

@bp.route('/keys')
@_key_maker_required
def keys():
    """Robert's queue — auction cars waiting on a key (the digital Key Row)."""
    vehicles = (
        Vehicle.query
        .filter_by(tina_stage='KEY_ROW')
        .filter(Vehicle.possible_release.isnot(True))
        .order_by(Vehicle.tina_stage_at.asc().nullslast())
        .all()
    )
    return render_template('field_ops/keys.html', vehicles=vehicles)


@bp.route('/keys/<int:vehicle_id>', methods=['GET', 'POST'])
@_key_maker_required
def key_make(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    if request.method == 'POST':
        action = request.form.get('action', '')

        # "Can't make a key" — kick it to Junk — Pending for Tina's call.
        if action == 'no_key':
            notes = request.form.get('notes', '').strip()
            ops.record_custody(vehicle, 'key_made',
                               'No key possible' + (f' — {notes}' if notes else ''))
            ops.move_stage(vehicle, 'JUNK_PENDING', note='no key possible')
            db.session.commit()
            flash(f'{vehicle.display_name} → Junk (pending Tina) — no key.', 'warning')
            return redirect(url_for('field_ops.keys'))

        key_type = request.form.get('key_type', '').strip()
        key_location = request.form.get('key_location', '').strip()
        holder = request.form.get('holder', '').strip()
        notes = request.form.get('notes', '').strip()
        try:
            key_cost = float(request.form.get('key_cost', '') or 0)
        except ValueError:
            key_cost = 0.0

        if key_location not in dispo.KEY_LOCATION_LABELS:
            flash('Please say where the key went.', 'danger')
            return redirect(url_for('field_ops.key_make', vehicle_id=vehicle_id))

        vehicle.key_made = True
        vehicle.key_type = key_type or None
        vehicle.key_cost = key_cost
        vehicle.key_made_by = ops.actor()
        vehicle.key_made_at = datetime.utcnow()
        loc_note = f'holder {holder}' if (key_location == 'SERVICE_HOLDER' and holder) else ''
        ops.set_key_location(vehicle, key_location, note=loc_note)
        ops.record_custody(vehicle, 'key_made',
                           f'Key made ({key_type or "key"}, ${key_cost:.0f})'
                           + (f' — {notes}' if notes else ''))
        ops.move_stage(vehicle, 'INSPECT_POOL', note='key made')
        db.session.commit()
        flash(f'Key made for {vehicle.display_name} → Inspection Pool.', 'success')
        return redirect(url_for('field_ops.keys'))

    return render_template('field_ops/key_make.html', vehicle=vehicle,
                           key_types=KEY_TYPES, key_locations=dispo.KEY_LOCATIONS)
