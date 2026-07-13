"""
Driver / VIN-snap mode — a driver on the lot photographs the VIN plate,
Claude Opus reads it, and the app logs a GPS-tagged zone location against
the matched vehicle. Ties into ghost-vehicle detection on the Tim dashboard
(see Vehicle.location_stale in models.py).

The QR Scan mode (Build Q) is a faster alternative to the photo-OCR path:
the driver points the camera at the Towbook QR flyer on the windshield,
jsQR decodes it client-side, and /driver/match-qr matches by stock number,
VIN, or plate — then the same confirm → zone → save flow runs.
"""
import json
import os
import re
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from models import db, Vehicle

bp = Blueprint('driver_snap', __name__, url_prefix='/driver')

ZONES = ['Lot A', 'Lot B', 'Lot C', 'PPI', 'Inspection Pool', '4301 Main']


def _match_vehicle_by_vin(vin_read):
    """Full VIN match first, then last-4 suffix match, active vehicles only."""
    if not vin_read:
        return None
    vin_read = vin_read.strip().upper()
    if len(vin_read) < 4:
        return None

    exact = (
        Vehicle.query
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.vin.isnot(None))
        .filter(db.func.upper(Vehicle.vin) == vin_read)
        .first()
    )
    if exact:
        return exact

    suffix = vin_read[-4:]
    return (
        Vehicle.query
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.vin.isnot(None))
        .filter(Vehicle.vin.ilike(f'%{suffix}'))
        .order_by(Vehicle.impound_date.desc())
        .first()
    )


def _match_vehicle_by_qr(text):
    """Match an active vehicle from a decoded Towbook QR payload. The flyer
    encodes stock #, VIN, and plate; the exact framing varies (delimited text,
    or a URL with query params), so we tokenize the whole string and try, in
    order of reliability: 17-char VIN, then stock number, then plate — all
    against active vehicles only. Returns (vehicle, matched_by) or (None, None)."""
    if not text:
        return None, None
    tokens = [t for t in re.split(r'[\s,;:|/?&=#]+', text.strip().upper()) if t]
    if not tokens:
        return None, None

    # 1) VIN — a 17-char alphanumeric token, exact match.
    for t in tokens:
        if len(t) == 17 and t.isalnum():
            v = (Vehicle.query
                 .filter(Vehicle.status == 'ACTIVE')
                 .filter(Vehicle.vin.isnot(None))
                 .filter(db.func.upper(Vehicle.vin) == t)
                 .first())
            if v:
                return v, 'vin'

    # 2) Stock number — exact match.
    for t in tokens:
        v = (Vehicle.query
             .filter(Vehicle.status == 'ACTIVE')
             .filter(Vehicle.stock_number.isnot(None))
             .filter(db.func.upper(Vehicle.stock_number) == t)
             .first())
        if v:
            return v, 'stock_number'

    # 3) Plate — exact match, last resort (plates are the least unique).
    for t in tokens:
        v = (Vehicle.query
             .filter(Vehicle.status == 'ACTIVE')
             .filter(Vehicle.plate.isnot(None))
             .filter(db.func.upper(Vehicle.plate) == t)
             .first())
        if v:
            return v, 'plate'

    return None, None


@bp.route('/')
@login_required
def index():
    return render_template('driver_snap/index.html', zones=ZONES)


@bp.route('/match-qr', methods=['POST'])
@login_required
def match_qr():
    """Match an active vehicle from a client-side-decoded Towbook QR code."""
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'No QR text provided'}), 400

    matched, matched_by = _match_vehicle_by_qr(text)
    return jsonify({
        'ok': True,
        'decoded': text,
        'matched_by': matched_by,
        'matched_vehicle': {
            'id': matched.id,
            'display_name': matched.display_name,
            'plate': matched.plate,
            'stock_number': matched.stock_number,
            'current_zone': matched.last_location_zone,
        } if matched else None,
    })


@bp.route('/read-vin', methods=['POST'])
@login_required
def read_vin():
    """Send the captured photo to Claude Opus to read the VIN, then match it
    against active vehicles."""
    data = request.get_json() or {}
    image_b64 = data.get('image', '')
    media_type = data.get('media_type', 'image/jpeg')

    if not image_b64:
        return jsonify({'error': 'No image provided'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 500

    if ',' in image_b64:
        image_b64 = image_b64.split(',', 1)[1]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model='claude-opus-4-8',
            max_tokens=512,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': image_b64,
                        },
                    },
                    {
                        'type': 'text',
                        'text': (
                            'This is a photo of a vehicle VIN plate or door jamb sticker, '
                            'taken on a tow lot. Read the full 17-character VIN and respond '
                            'ONLY with valid JSON:\n'
                            '{\n'
                            '  "vin": "17-char VIN exactly as printed, or null if unreadable",\n'
                            '  "notes": "anything unusual — glare, damage, partially obscured, etc."\n'
                            '}'
                        ),
                    },
                ],
            }],
        )

        raw = message.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            result = json.loads(m.group()) if m else {}

        vin_read = (result.get('vin') or '').strip().upper() or None
        matched = _match_vehicle_by_vin(vin_read) if vin_read else None

        return jsonify({
            'ok': True,
            'vin': vin_read,
            'notes': result.get('notes'),
            'matched_vehicle': {
                'id': matched.id,
                'display_name': matched.display_name,
                'plate': matched.plate,
                'stock_number': matched.stock_number,
                'current_zone': matched.last_location_zone,
            } if matched else None,
        })

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@bp.route('/save-location', methods=['POST'])
@login_required
def save_location():
    data = request.get_json() or {}
    vehicle_id = data.get('vehicle_id')
    zone = (data.get('zone') or '').strip()
    lat = data.get('lat')
    lng = data.get('lng')

    if not vehicle_id:
        return jsonify({'error': 'vehicle_id required'}), 400
    if zone not in ZONES:
        return jsonify({'error': 'Invalid zone'}), 400

    vehicle = db.session.get(Vehicle, int(vehicle_id))
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404

    vehicle.last_location_zone = zone
    vehicle.last_location_lat = float(lat) if lat is not None else None
    vehicle.last_location_lng = float(lng) if lng is not None else None
    vehicle.last_location_at = datetime.utcnow()
    vehicle.last_location_by = current_user.display_name or current_user.username
    db.session.commit()

    return jsonify({
        'ok': True,
        'vehicle': vehicle.display_name,
        'zone': zone,
    })
