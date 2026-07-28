"""
VIN -> weight class detection via the free NHTSA vPIC decoder (no API key).

Maps a decoded VIN to the PPI weight classes used for storage billing:

    light   $22/day   passenger car / SUV / pickup, GVWR <= 10,000 lb  (DOT class 1-2)
    medium  $37/day   box truck / large van,         GVWR 10,001-26,000 (class 3-6)
    heavy   $82/day   semi / bus / heavy equipment,   GVWR > 26,000 lb  (class 7-8)

Used two ways:
  * the /admin/reclassify fleet scan (batch decode of every active PPI VIN), and
  * the "Detect from VIN" button on the intake / edit form (single VIN).

Network calls are best-effort: on any failure the affected VINs come back as
`detected=None` (an "undetected" bucket for manual handling) — never an exception
that would break a page. NHTSA is a plain data API, so no AI model is involved.
"""
import re

import requests

# vPIC endpoints (public, keyless)
_BATCH_URL = 'https://vpic.nhtsa.gov/api/vehicles/DecodeVINValuesBatch/'
_SINGLE_URL = 'https://vpic.nhtsa.gov/api/vehicles/DecodeVinValues/{vin}'

# NHTSA caps a batch at 50 VINs per request.
BATCH_SIZE = 50
_TIMEOUT = 30

# Body-class fragments that unambiguously mean a light passenger vehicle,
# regardless of the (usually empty) GVWR field for such vehicles.
_CAR_BODY_HINTS = (
    'sedan', 'saloon', 'coupe', 'hatchback', 'wagon', 'convertible',
    'roadster', 'sport utility', 'suv', 'multipurpose', 'mpv', 'minivan',
    'crossover',
)


def _class_from_gvwr(gvwr):
    """Map an NHTSA GVWR string to a weight class, or None if not determinable.

    The field looks like ``'Class 2E: 6,001 - 7,000 lb (2,722 - 3,175 kg)'`` or
    ``'Class 8: 33,001 lb and above (14,969 kg and above)'``; sometimes it is
    blank, 'Not Applicable', or only carries a pound range.
    """
    if not gvwr:
        return None
    g = gvwr.lower()
    if 'not applicable' in g or 'not reported' in g:
        return None
    # Primary: the "Class N" token ("Class 2E" -> 2, "Class 8" -> 8).
    m = re.search(r'class\s+(\d+)', g)
    if m:
        n = int(m.group(1))
        if n <= 2:
            return 'light'
        if n <= 6:
            return 'medium'
        return 'heavy'
    # Fallback: bucket by the largest pound figure mentioned.
    nums = [int(x.replace(',', '')) for x in re.findall(r'([\d,]+)\s*lb', g)]
    if nums:
        top = max(nums)
        if top <= 10000:
            return 'light'
        if top <= 26000:
            return 'medium'
        return 'heavy'
    return None


def class_from_decode(row):
    """Given one NHTSA Results dict, return ``(class_or_None, human_reason)``.

    ``class_or_None`` is 'light' / 'medium' / 'heavy', or None when the VIN
    couldn't be classified (bad/partial VIN, or NHTSA returned nothing usable).
    ``human_reason`` is a short phrase for the review row.
    """
    vtype = (row.get('VehicleType') or '').strip()
    body = (row.get('BodyClass') or '').strip()
    gvwr = (row.get('GVWR') or '').strip()
    vt = vtype.upper()
    bl = body.lower()

    # 1) Clear passenger vehicles -> light. (These carry no meaningful GVWR.)
    if vt == 'PASSENGER CAR' or any(h in bl for h in _CAR_BODY_HINTS):
        return 'light', f'Light — {body or vtype or "passenger vehicle"}'

    # 2) Trucks / vans / buses -> drive off GVWR weight class.
    cls = _class_from_gvwr(gvwr)
    if cls:
        label = {'light': 'Light', 'medium': 'Medium', 'heavy': 'Heavy'}[cls]
        parts = [gvwr] if gvwr else []
        if body:
            parts.append(body)
        detail = ' — ' + ', '.join(parts) if parts else ''
        return cls, f'{label}{detail}'

    # 3) GVWR blank but clearly a pickup/truck -> assume light, flag the guess.
    if 'pickup' in bl or vt == 'TRUCK':
        return 'light', f'Light (assumed) — {body or vtype}, no GVWR from VIN'

    # 4) Nothing usable.
    bits = []
    if vtype:
        bits.append(f'type {vtype}')
    if body:
        bits.append(body)
    why = ', '.join(bits) if bits else 'no VIN data returned'
    return None, f'Could not determine — {why}'


def _blank_result(vin, reason):
    return {'vin': vin, 'detected': None, 'reason': reason,
            'make': None, 'model': None, 'year': None,
            'vehicle_type': None, 'body_class': None, 'gvwr': None}


def _result_from_row(vin, row):
    detected, reason = class_from_decode(row)
    return {
        'vin': vin,
        'detected': detected,
        'reason': reason,
        'make': (row.get('Make') or '').strip() or None,
        'model': (row.get('Model') or '').strip() or None,
        'year': (row.get('ModelYear') or '').strip() or None,
        'vehicle_type': (row.get('VehicleType') or '').strip() or None,
        'body_class': (row.get('BodyClass') or '').strip() or None,
        'gvwr': (row.get('GVWR') or '').strip() or None,
    }


def _valid_vin(vin):
    return bool(vin) and len(vin.strip()) == 17


def decode_vins(vins):
    """Decode a list of VINs. Returns ``{vin: result_dict}``.

    Each result dict has: vin, detected ('light'|'medium'|'heavy'|None), reason,
    make, model, year, vehicle_type, body_class, gvwr. VINs that are missing,
    the wrong length, or that NHTSA can't reach come back with detected=None.
    """
    out = {}
    clean = []
    for v in vins:
        vin = (v or '').strip().upper()
        if not vin:
            continue
        if not _valid_vin(vin):
            out[vin] = _blank_result(vin, 'No/short VIN — decode skipped')
            continue
        clean.append(vin)

    for i in range(0, len(clean), BATCH_SIZE):
        chunk = clean[i:i + BATCH_SIZE]
        try:
            resp = requests.post(
                _BATCH_URL,
                data={'DATA': ';'.join(chunk), 'format': 'json'},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get('Results', []) or []
        except Exception:
            # Whole batch failed — mark each undetected, keep going.
            for vin in chunk:
                out[vin] = _blank_result(vin, "Couldn't reach the VIN decoder")
            continue

        # Map rows back to VINs (NHTSA echoes the VIN in each row).
        by_vin = {}
        for row in results:
            rv = (row.get('VIN') or '').strip().upper()
            if rv:
                by_vin[rv] = row
        for vin in chunk:
            row = by_vin.get(vin)
            out[vin] = _result_from_row(vin, row) if row else \
                _blank_result(vin, 'VIN not found in NHTSA database')

    return out


def detect_class(vin):
    """Single-VIN convenience for the intake button. Returns a result dict."""
    vin = (vin or '').strip().upper()
    if not _valid_vin(vin):
        return _blank_result(vin, 'Enter a full 17-character VIN to auto-detect')
    return decode_vins([vin]).get(vin, _blank_result(vin, 'Decode failed'))
