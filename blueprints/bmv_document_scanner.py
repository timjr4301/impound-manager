import os
import base64
import json
import re
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
import anthropic

from models import db, Vehicle, VehicleNote, BMVScanHistory

bp = Blueprint('bmv_scanner', __name__, url_prefix='/bmv-scanner')


# ── Anthropic client ──────────────────────────────────────────────────────────
def _client():
    return anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))


# ── LKA extraction ─────────────────────────────────────────────────────────────
LKA_SYSTEM_PROMPT = """You are reading an Ohio BMV "Last Known Address" records search
result (form BMV 2433) for Broad & James Towing in Columbus, Ohio.

Extract the data and respond ONLY with valid JSON, no markdown, no backticks, no preamble.
Use this exact schema:

{
  "document_type": "LKA",
  "requested_on": "YYYY-MM-DD or null",
  "vin": "string or null",
  "owner_name": "string or null",
  "owner_street": "string or null",
  "owner_city": "string or null",
  "owner_state": "string or null",
  "owner_zip": "string or null",
  "owner_current_as_of": "YYYY-MM-DD or null",
  "is_po_box": true/false,
  "second_owner_name": "string or null (if N/A, use null)",
  "second_owner_address": "string or null",
  "no_information_found": true/false,
  "consent_not_provided": true/false
}

is_po_box: true if owner_street contains "PO BOX", "P.O. BOX", or similar — case insensitive.
no_information_found: true if the "No information found" checkbox is marked.
consent_not_provided: true if the consent disclosure checkbox is marked."""


def _analyze_lka(image_b64, mime_type):
    client = _client()
    response = client.messages.create(
        model='claude-opus-4-8',
        max_tokens=800,
        system=LKA_SYSTEM_PROMPT,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': mime_type, 'data': image_b64}},
                {'type': 'text', 'text': 'Extract all data from this BMV Last Known Address document.'}
            ]
        }]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)


# ── Title Abstract extraction ──────────────────────────────────────────────────
TITLE_SYSTEM_PROMPT = """You are reading an Ohio BMV Title Record / Title Abstract
(form BMV 1148) for Broad & James Towing in Columbus, Ohio.

Extract the data and respond ONLY with valid JSON, no markdown, no backticks, no preamble.
Use this exact schema:

{
  "document_type": "TITLE_ABSTRACT",
  "report_date": "YYYY-MM-DD or null",
  "title_number": "string or null",
  "control_number": "string or null",
  "title_status": "string or null",
  "title_type": "string or null",
  "issue_date": "YYYY-MM-DD or null",
  "vin": "string or null",
  "year": "string or null",
  "make": "string or null",
  "model": "string or null",
  "body_type": "string or null",
  "condition": "string or null",
  "mileage": "string or null",
  "mileage_brand": "string or null",
  "mileage_warning": "string or null (e.g. ODOMETER DISCREPANCY warning text, else null)",
  "num_liens": "integer or null",
  "num_active_liens": "integer or null",
  "owner_first_name": "string or null",
  "owner_last_name": "string or null",
  "owner_company_name": "string or null (if this is a dealer/lessor, not a person)",
  "owner_dealer_permit": "string or null",
  "owner_street": "string or null",
  "owner_city": "string or null",
  "owner_state": "string or null",
  "owner_zip": "string or null",
  "is_dealer_or_lessor": true/false,
  "lienholder_name": "string or null",
  "lienholder_street": "string or null",
  "lienholder_city": "string or null",
  "lienholder_state": "string or null",
  "lienholder_zip": "string or null",
  "lien_status": "string or null",
  "previous_owner_name": "string or null",
  "previous_owner_address": "string or null"
}

is_dealer_or_lessor: true if owner_dealer_permit or owner_company_name is populated
(meaning the "owner" is a dealership/leasing company, not an individual).
mileage_warning: capture any warning text near mileage brand, like "NON-ACTUAL WARNING:
ODOMETER DISCREPANCY" — this is operationally important for the title packet.
Liens are typically on page 2 under "Lien Information" — look for Lien Name and address."""


def _analyze_title_abstract(images_b64_list, mime_type):
    """Title abstract may be 2 pages — pass both images."""
    client = _client()
    content = []
    for img_b64 in images_b64_list:
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime_type, 'data': img_b64}})
    content.append({'type': 'text', 'text': 'Extract all data from this BMV Title Abstract (may be 2 pages).'})
    response = client.messages.create(
        model='claude-opus-4-8',
        max_tokens=1200,
        system=TITLE_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': content}]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)


# ── Discrepancy detection ──────────────────────────────────────────────────────
def _normalize_name(name):
    if not name:
        return ''
    parts = re.split(r'[\s,]+', name.upper().strip())
    return ' '.join(sorted(p for p in parts if p))


def _normalize_address(street):
    if not street:
        return ''
    s = street.upper().strip()
    s = re.sub(r'[.,]', '', s)
    s = re.sub(r'\bSTREET\b', 'ST', s)
    s = re.sub(r'\bAVENUE\b', 'AVE', s)
    s = re.sub(r'\bDRIVE\b', 'DR', s)
    s = re.sub(r'\bLANE\b', 'LN', s)
    s = re.sub(r'\bROAD\b', 'RD', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def compare_lka_and_title(lka, title):
    """
    Returns a dict of flags comparing LKA owner data vs Title Abstract owner data.
    This is the core logic Tina does by eye on every file (SOP steps B/C/D).
    """
    flags = []

    lka_name = _normalize_name(lka.get('owner_name', ''))
    title_first = title.get('owner_first_name', '') or ''
    title_last = title.get('owner_last_name', '') or ''
    title_name = _normalize_name(f'{title_first} {title_last}')
    title_company = title.get('owner_company_name', '')

    name_match = None
    if lka_name and title_name:
        name_match = (lka_name == title_name)
    elif lka_name and title_company:
        name_match = (_normalize_name(lka.get('owner_name', '')) == _normalize_name(title_company))

    if name_match is False:
        flags.append({
            'severity': 'high',
            'code': 'name_mismatch',
            'message': (f"Name mismatch — LKA: '{lka.get('owner_name')}' vs "
                        f"Title: '{(title_first + ' ' + title_last).strip()}'")
        })

    lka_addr = _normalize_address(lka.get('owner_street', ''))
    title_addr = _normalize_address(title.get('owner_street', ''))
    address_match = None
    if lka_addr and title_addr:
        address_match = (lka_addr == title_addr)
        if not address_match:
            flags.append({
                'severity': 'high',
                'code': 'address_mismatch',
                'message': (f"Address mismatch — LKA: '{lka.get('owner_street')}, "
                            f"{lka.get('owner_city')}' vs Title: "
                            f"'{title.get('owner_street')}, {title.get('owner_city')}'. "
                            f"LKA current as of {lka.get('owner_current_as_of', 'unknown')} "
                            f"— per BMV process, use the more recent LKA address, but verify.")
            })

    if lka.get('is_po_box'):
        flags.append({
            'severity': 'critical',
            'code': 'po_box',
            'message': ('Owner address is a PO Box. Per SOP: must pull confirmation from '
                        'tow lien AND report of delivery, or BMV will reject the packet.')
        })

    if title.get('is_dealer_or_lessor'):
        flags.append({
            'severity': 'medium',
            'code': 'dealer_lessor',
            'message': (f"Title owner is a dealer/lessor ({title.get('owner_company_name')}), "
                        "not an individual. Confirm correct recipient for letter.")
        })

    active_liens = title.get('num_active_liens') or 0
    try:
        active_liens = int(active_liens)
    except (TypeError, ValueError):
        active_liens = 0

    if active_liens > 0:
        if not title.get('lienholder_name'):
            flags.append({
                'severity': 'high',
                'code': 'lien_no_name',
                'message': (f"Title shows {active_liens} active lien(s) but no lienholder "
                            "name was extracted — check page 2 manually.")
            })
        else:
            flags.append({
                'severity': 'info',
                'code': 'lienholder_found',
                'message': (f"Active lienholder: {title.get('lienholder_name')}, "
                            f"{title.get('lienholder_street')}, {title.get('lienholder_city')} "
                            f"{title.get('lienholder_state')} {title.get('lienholder_zip')} — "
                            "second certified letter required to this address.")
            })

    if title.get('mileage_warning'):
        flags.append({
            'severity': 'medium',
            'code': 'odometer_warning',
            'message': f"Title flags: {title.get('mileage_warning')}"
        })

    if lka.get('no_information_found'):
        flags.append({
            'severity': 'critical',
            'code': 'no_info_found',
            'message': "LKA returned 'No information found' — no address available from BMV."
        })

    return {
        'name_match': name_match,
        'address_match': address_match,
        'flags': flags,
        'recommended_owner_name': lka.get('owner_name') or f'{title_first} {title_last}'.strip(),
        'recommended_owner_address': lka.get('owner_street') or title.get('owner_street'),
        'recommended_owner_city': lka.get('owner_city') or title.get('owner_city'),
        'recommended_owner_state': lka.get('owner_state') or title.get('owner_state'),
        'recommended_owner_zip': lka.get('owner_zip') or title.get('owner_zip'),
    }


# ── Vehicle matching ───────────────────────────────────────────────────────────
def _match_vehicle_by_vin(vin):
    if not vin:
        return None
    return Vehicle.query.filter(Vehicle.vin == vin).first()


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def scanner_page():
    return render_template('bmv_scanner.html')


@bp.route('/scan-lka', methods=['POST'])
@login_required
def scan_lka():
    data = request.get_json()
    image_data = data.get('image', '')
    if ',' in image_data:
        header, image_b64 = image_data.split(',', 1)
        mime_type = header.split(':')[1].split(';')[0] if ':' in header else 'image/jpeg'
    else:
        image_b64, mime_type = image_data, 'image/jpeg'

    try:
        parsed = _analyze_lka(image_b64, mime_type)
    except Exception as e:
        return jsonify({'error': f'LKA scan failed: {str(e)}'}), 500

    vehicle = _match_vehicle_by_vin(parsed.get('vin'))
    result = {'parsed': parsed, 'vehicle_match': None}
    if vehicle:
        result['vehicle_match'] = {
            'id': vehicle.id,
            'stock_number': vehicle.stock_number,
            'owner_name': vehicle.owner_name,
        }
    return jsonify(result)


@bp.route('/scan-title', methods=['POST'])
@login_required
def scan_title():
    data = request.get_json()
    images = data.get('images', [])
    if not images:
        return jsonify({'error': 'No images received'}), 400

    images_b64 = []
    mime_type = 'image/jpeg'
    for img in images:
        if ',' in img:
            header, img_b64 = img.split(',', 1)
            mime_type = header.split(':')[1].split(';')[0] if ':' in header else 'image/jpeg'
        else:
            img_b64 = img
        images_b64.append(img_b64)

    try:
        parsed = _analyze_title_abstract(images_b64, mime_type)
    except Exception as e:
        return jsonify({'error': f'Title abstract scan failed: {str(e)}'}), 500

    vehicle = _match_vehicle_by_vin(parsed.get('vin'))
    result = {'parsed': parsed, 'vehicle_match': None}
    if vehicle:
        result['vehicle_match'] = {
            'id': vehicle.id,
            'stock_number': vehicle.stock_number,
            'owner_name': vehicle.owner_name,
        }
    return jsonify(result)


@bp.route('/compare', methods=['POST'])
@login_required
def compare():
    """Run discrepancy detection once both LKA and Title have been scanned."""
    data = request.get_json()
    lka = data.get('lka', {})
    title = data.get('title', {})
    return jsonify(compare_lka_and_title(lka, title))


@bp.route('/save', methods=['POST'])
@login_required
def save():
    """
    Permanently store scan results to the vehicle record and write history.
    Accepts: { vehicle_id, lka, title, comparison }
    """
    data = request.get_json()
    vehicle_id = data.get('vehicle_id')
    lka = data.get('lka', {})
    title = data.get('title', {})
    comparison = data.get('comparison', {})

    if not vehicle_id:
        return jsonify({'error': 'vehicle_id required'}), 400

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404

    scanned_by = getattr(current_user, 'username', 'heather')
    now = datetime.utcnow()

    # Update vehicle with recommended data (never overwrite existing values)
    if not vehicle.owner_name:
        vehicle.owner_name = comparison.get('recommended_owner_name') or ''
    if not vehicle.owner_address:
        vehicle.owner_address = comparison.get('recommended_owner_address') or ''
    if not vehicle.owner_city:
        vehicle.owner_city = comparison.get('recommended_owner_city') or ''
    if not vehicle.owner_state:
        vehicle.owner_state = comparison.get('recommended_owner_state') or ''
    if not vehicle.owner_zip:
        vehicle.owner_zip = comparison.get('recommended_owner_zip') or ''
    if not vehicle.lienholder_name and title.get('lienholder_name'):
        vehicle.lienholder_name = title['lienholder_name']
    if not vehicle.lienholder_address and title.get('lienholder_street'):
        vehicle.lienholder_address = title['lienholder_street']
    if not vehicle.lienholder_city and title.get('lienholder_city'):
        vehicle.lienholder_city = title['lienholder_city']
    if not vehicle.lienholder_state and title.get('lienholder_state'):
        vehicle.lienholder_state = title['lienholder_state']
    if not vehicle.lienholder_zip and title.get('lienholder_zip'):
        vehicle.lienholder_zip = title['lienholder_zip']
    if not vehicle.title_number and title.get('title_number'):
        vehicle.title_number = title['title_number']
    if title.get('mileage') and not vehicle.mileage:
        try:
            vehicle.mileage = int(re.sub(r'[^\d]', '', str(title['mileage'])))
        except (ValueError, TypeError):
            pass
    vehicle.po_box_flag = lka.get('is_po_box', False)
    vehicle.bmv_stage = 'SEARCHED'
    vehicle.bmv_searched_date = now.date()
    vehicle.updated_at = now

    # Compose activity note
    flags = comparison.get('flags', [])
    high_flags = [f for f in flags if f.get('severity') in ('critical', 'high')]
    note_lines = [f'BMV documents scanned by {scanned_by} — LKA + Title Abstract.']
    if high_flags:
        note_lines.append(f'ISSUES ({len(high_flags)}):')
        for f in high_flags:
            note_lines.append(f'  [{f["code"]}] {f["message"]}')
    else:
        note_lines.append('No discrepancies found — name and address match.')

    note = VehicleNote(
        vehicle_id=vehicle.id,
        body='\n'.join(note_lines),
        author=scanned_by,
        created_at=now,
    )
    db.session.add(note)

    # Permanent raw scan history record
    history = BMVScanHistory(
        vehicle_id=vehicle.id,
        scan_type='lka_and_title',
        lka_data=json.dumps(lka),
        title_data=json.dumps(title),
        comparison_flags=json.dumps(flags),
        scanned_by=scanned_by,
        scanned_at=now,
    )
    db.session.add(history)

    db.session.commit()

    return jsonify({
        'success': True,
        'vehicle_id': vehicle.id,
        'stock_number': vehicle.stock_number,
        'flags_count': len(high_flags),
        'flags': flags,
    })
