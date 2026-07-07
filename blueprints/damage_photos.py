"""
Damage Photos — bulk ZIP upload from Towbook call folders, single-photo
upload/delete on the vehicle detail page, and Claude Opus vision damage
assessment feeding the existing DamageItem/BMV-4202 mechanism.

Storage table is vehicle_damage_photos (VehicleDamagePhoto) — a separate
table from the older damage_photos table used by the driver-facing
damage_docs wizard (report_id FK to damage_reports). See models.py for the
full explanation; do not confuse the two.
"""
import io
import json
import os
import re
import zipfile
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from PIL import Image

from models import db, Vehicle, VehicleDamagePhoto, DamageItem
from titlebot.damages import DAMAGE_PRESETS

bp = Blueprint('damage_photos', __name__)

UPLOAD_ROLES = ('tim', 'heather', 'tina', 'brady', 'jim')
ASSESS_ROLES = ('tina', 'tim', 'brady', 'jim')

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
MAX_EDGE = 1600
JPEG_QUALITY = 80
BMV_ITEM_CAP = 9

PRESET_DEFAULT_COST = {name: cost for name, cost in DAMAGE_PRESETS}
PRESET_DEFAULT_COST.setdefault('REAR BUMPER', 450.00)

ASSESSMENT_PROMPT = """You are inspecting a towed/impounded vehicle for an Ohio towing company.
These photos show the vehicle from multiple angles.
Your job is to identify ALL visible damage and map it to damage categories
used on the Ohio BMV title form.

Rules:
- Report ONLY damage actually visible in the photos
- Do NOT invent or infer hidden damage — this goes on a legal form
- Be SPECIFIC: note the location (left/right/front/rear), severity
  (minor scratch, moderate dent, major crease, crushed, missing), and
  approximate size or extent when visible
- Consolidate duplicate angles — if three photos show the same dent,
  list it once with a thorough description
- Map each item to exactly ONE of these preset categories:
  KEY REPLACEMENT, FRONT BUMPER, REAR BUMPER, FENDER, HOOD, WINDSHIELD,
  WHEEL / TIRE, QUARTER PANEL, INTERIOR, MECHANICAL, CUSTOM
- For CUSTOM items (damage that doesn't fit a preset), provide a specific label
- Default estimated_cost to these standard amounts unless the photo clearly
  shows damage warranting more:
  KEY REPLACEMENT=350, FRONT BUMPER=450, REAR BUMPER=450, FENDER=350,
  HOOD=500, WINDSHIELD=300, WHEEL/TIRE=300, QUARTER PANEL=600,
  INTERIOR=300, MECHANICAL=1200, CUSTOM=varies

Respond ONLY with valid JSON, no prose, no markdown fences:
{
  "overall_condition": "good|fair|poor|salvage",
  "summary": "2-4 sentences describing the overall vehicle condition",
  "items": [
    {
      "preset": "<category from list above>",
      "label": "<only if CUSTOM, otherwise omit>",
      "location": "<specific: left front, right rear, etc.>",
      "severity": "<minor|moderate|major|destroyed>",
      "description": "<specific visible detail>",
      "estimated_cost": <integer>,
      "confidence": "high|med|low"
    }
  ]
}"""


def _upload_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role not in UPLOAD_ROLES:
            flash('Permission denied.', 'danger')
            return redirect(url_for('vehicles_list'))
        return f(*args, **kwargs)
    return login_required(decorated)


def _resize_to_jpeg(raw_bytes, max_edge=MAX_EDGE, quality=JPEG_QUALITY):
    """Resize to a max edge of max_edge px and re-encode as JPEG at the given quality."""
    img = Image.open(io.BytesIO(raw_bytes))
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    elif img.mode == 'L':
        img = img.convert('RGB')
    w, h = img.size
    scale = min(1.0, max_edge / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=quality)
    return out.getvalue()


def _is_junk_zip_entry(name):
    base = os.path.basename(name)
    return name.endswith('/') or name.startswith('__MACOSX/') or base.startswith('.')


def _add_photo(vehicle, raw_bytes, original_filename, caption=None):
    """Resize + insert one VehicleDamagePhoto row. Returns True if added, False if unreadable."""
    try:
        jpeg_bytes = _resize_to_jpeg(raw_bytes)
    except Exception:
        return False
    import base64
    db.session.add(VehicleDamagePhoto(
        vehicle_id=vehicle.id,
        image_data=base64.b64encode(jpeg_bytes).decode('ascii'),
        image_type='image/jpeg',
        caption=(caption or None),
        original_filename=secure_filename(original_filename)[:200] if original_filename else None,
        uploaded_by=current_user.username,
        uploaded_at=datetime.utcnow(),
    ))
    return True


# ── Part 2 — Bulk upload page ────────────────────────────────────────────────

@bp.route('/damage-photos/bulk', methods=['GET', 'POST'])
@_upload_required
def bulk_upload():
    if request.method == 'GET':
        return render_template('damage_photos/bulk.html', results=None)

    files = [f for f in request.files.getlist('files') if f and f.filename]
    call_number_override = request.form.get('call_number', '').strip()

    if not files:
        flash('Choose at least one ZIP or image file to upload.', 'danger')
        return redirect(url_for('damage_photos.bulk_upload'))

    matched = {}   # vehicle_id -> {'vehicle': Vehicle, 'added': int, 'sources': [filenames]}
    unmatched = []  # [{'filename':..., 'call_number':..., 'reason':...}]

    def _record_match(vehicle, filename, added_count):
        entry = matched.setdefault(vehicle.id, {'vehicle': vehicle, 'added': 0, 'sources': []})
        entry['added'] += added_count
        entry['sources'].append(filename)

    for upload in files:
        filename = upload.filename
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext == 'zip':
            m = re.search(r'call_(\d+)_files', filename, re.IGNORECASE)
            if not m:
                unmatched.append({
                    'filename': filename, 'call_number': None,
                    'reason': 'ZIP filename did not match the call_XXXXXX_files.zip pattern',
                })
                continue
            call_number = m.group(1)
            vehicle = Vehicle.query.filter_by(call_number=call_number).first()
            if not vehicle:
                unmatched.append({
                    'filename': filename, 'call_number': call_number,
                    'reason': 'No vehicle found with this call number',
                })
                continue

            try:
                zf = zipfile.ZipFile(io.BytesIO(upload.read()))
            except zipfile.BadZipFile:
                unmatched.append({
                    'filename': filename, 'call_number': call_number,
                    'reason': 'File could not be read as a ZIP archive',
                })
                continue

            added = 0
            for zi in zf.infolist():
                if _is_junk_zip_entry(zi.filename):
                    continue
                entry_ext = zi.filename.rsplit('.', 1)[-1].lower() if '.' in zi.filename else ''
                if entry_ext not in IMAGE_EXTS:
                    continue
                try:
                    raw = zf.read(zi)
                except Exception:
                    continue
                if _add_photo(vehicle, raw, os.path.basename(zi.filename)):
                    added += 1

            if added:
                _record_match(vehicle, filename, added)
            else:
                unmatched.append({
                    'filename': filename, 'call_number': call_number,
                    'reason': f'Matched {vehicle.display_name}, but the ZIP contained no readable image files',
                })

        elif ext in IMAGE_EXTS:
            if not call_number_override:
                unmatched.append({
                    'filename': filename, 'call_number': None,
                    'reason': 'Loose image uploaded without a Call Number — enter one to attach it',
                })
                continue
            vehicle = Vehicle.query.filter_by(call_number=call_number_override).first()
            if not vehicle:
                unmatched.append({
                    'filename': filename, 'call_number': call_number_override,
                    'reason': 'No vehicle found with this call number',
                })
                continue
            raw = upload.read()
            if _add_photo(vehicle, raw, filename):
                _record_match(vehicle, filename, 1)
            else:
                unmatched.append({
                    'filename': filename, 'call_number': call_number_override,
                    'reason': 'File could not be read as an image',
                })
        else:
            unmatched.append({
                'filename': filename, 'call_number': None,
                'reason': 'Unsupported file type — expected .zip or an image (jpg/jpeg/png/webp)',
            })

    db.session.commit()

    matched_list = sorted(matched.values(), key=lambda e: e['vehicle'].display_name or '')
    total_photos = sum(e['added'] for e in matched_list)
    results = {
        'matched': matched_list,
        'unmatched': unmatched,
        'total_photos': total_photos,
        'total_vehicles': len(matched_list),
    }
    return render_template('damage_photos/bulk.html', results=results)


# ── Part 3 — Single upload / delete on vehicle detail ────────────────────────

@bp.route('/vehicles/<int:vehicle_id>/damage-photos', methods=['POST'])
@_upload_required
def upload_single(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    upload = request.files.get('file')
    if not upload or not upload.filename:
        flash('Choose a photo to upload.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    raw = upload.read()
    if not raw:
        flash('That file appears to be empty.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    caption = request.form.get('caption', '').strip()
    if not _add_photo(vehicle, raw, upload.filename, caption=caption):
        flash('That file could not be read as an image.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    db.session.commit()
    flash('Damage photo uploaded.', 'success')
    return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')


@bp.route('/damage-photos/<int:photo_id>/delete', methods=['POST'])
@login_required
def delete_photo(photo_id):
    photo = db.get_or_404(VehicleDamagePhoto, photo_id)
    vehicle_id = photo.vehicle_id
    if not (current_user.role == 'tim' or current_user.username == photo.uploaded_by):
        flash('You can only delete your own uploads.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')
    db.session.delete(photo)
    db.session.commit()
    flash('Photo deleted.', 'info')
    return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')


# ── Part 4 — AI damage assessment ────────────────────────────────────────────

@bp.route('/vehicles/<int:vehicle_id>/assess-damage', methods=['POST'])
@login_required
def assess_damage(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if current_user.role not in ASSESS_ROLES:
        flash('Permission denied.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    photos = list(vehicle.damage_photos)
    if not photos:
        flash('No photos uploaded yet.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        flash('AI damage assessment is not configured (missing API key).', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    content = []
    for photo in photos:
        content.append({
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': photo.image_type or 'image/jpeg',
                'data': photo.image_data,
            },
        })
    content.append({'type': 'text', 'text': ASSESSMENT_PROMPT})

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-opus-4-8',
            max_tokens=4096,
            messages=[{'role': 'user', 'content': content}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        current_app.logger.warning(f'Damage assessment API call failed: {exc}')
        flash('AI damage assessment failed — try again.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
    except (ValueError, TypeError):
        current_app.logger.warning(f'Damage assessment JSON parse failed. Raw response:\n{raw}')
        flash('AI assessment failed to parse — try again.', 'warning')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    most_recent = max(photos, key=lambda p: p.uploaded_at or datetime.min)
    most_recent.ai_assessment = parsed.get('summary', '')
    most_recent.ai_items_json = json.dumps(parsed)
    db.session.commit()

    item_count = len(parsed.get('items', []))
    flash(f'AI assessment complete — {item_count} damage item(s) identified.', 'success')
    return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')


@bp.route('/vehicles/<int:vehicle_id>/damage-assessment/add-to-bmv', methods=['POST'])
@login_required
def add_to_bmv(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if current_user.role not in ASSESS_ROLES:
        flash('Permission denied.', 'danger')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    try:
        items = json.loads(request.form.get('items_json', '[]'))
    except (ValueError, TypeError):
        items = []

    if not items:
        flash('No items were selected.', 'info')
        return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#damage-photos')

    existing_count = len(vehicle.damage_items)
    available_slots = max(0, BMV_ITEM_CAP - existing_count)
    to_add = items[:available_slots]
    overflow = items[available_slots:]

    next_order = max((d.sort_order for d in vehicle.damage_items), default=-1) + 1
    for item in to_add:
        preset = (item.get('preset') or '').strip().upper()
        if preset == 'CUSTOM' and item.get('label'):
            description = item['label'].strip().upper()
        else:
            description = preset or 'DAMAGE'
        try:
            amount = float(item.get('estimated_cost') or 0)
        except (ValueError, TypeError):
            amount = 0.0
        db.session.add(DamageItem(
            vehicle_id=vehicle.id,
            description=description[:100],
            amount=amount,
            is_fallback=False,
            sort_order=next_order,
            created_at=datetime.utcnow(),
        ))
        next_order += 1

    db.session.commit()

    if to_add:
        flash(f'{len(to_add)} damage items added to BMV form.', 'success')
    if overflow:
        names = ', '.join(
            (i.get('label') or i.get('preset') or 'item') for i in overflow
        )
        flash(f'9-item BMV cap reached. The following were NOT added: {names}', 'warning')

    return redirect(url_for('vehicles_detail', vehicle_id=vehicle_id) + '#title-packet')
