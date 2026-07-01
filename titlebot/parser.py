"""
Extract vehicle/owner/lienholder/financial data from a Towbook-generated PDF.
Returns a dict of field values that can pre-fill the new/edit vehicle form.
"""

import io
import re
from datetime import datetime


def _find(text, pattern, flags=re.IGNORECASE | re.MULTILINE):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ''


def _is_blank(value):
    if not value:
        return True
    upper = value.upper()
    return 'NOT ENTERED' in upper or upper.strip() in ('', '0')


def _parse_money(value):
    if not value:
        return None
    cleaned = re.sub(r'[$,\s]', '', str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_text_from_pdf(pdf_bytes):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError('pypdf is required for Towbook import. Run: pip install pypdf')
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = ''
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text += t + '\n'
    return text


def extract_towbook_data(text):
    """
    Parse the raw text extracted from a Towbook PDF.
    Returns a dict with keys matching Vehicle model fields.
    """
    result = {}

    # ── Vehicle ───────────────────────────────────────────────────────────
    vin = _find(text, r'^VIN\s+([A-HJ-NPR-Z0-9]{17})')
    if vin:
        result['vin'] = vin

    year = _find(text, r'^YEAR[ \t]+(\d{4})')
    if year:
        result['year'] = int(year)

    make = _find(text, r'^MAKE[ \t]+(.+)$')
    if make and not _is_blank(make):
        result['make'] = make

    model = _find(text, r'^MODEL[ \t]+(.+)$')
    if model and not _is_blank(model):
        result['model_name'] = model

    color = _find(text, r'^COLOR[ \t]+(.+)$')
    if color and not _is_blank(color):
        result['color'] = color

    mileage_raw = _find(text, r'^MILEAGE[ \t]+([\d,]+)')
    if mileage_raw:
        try:
            result['mileage'] = int(mileage_raw.replace(',', ''))
        except ValueError:
            pass

    # ── Financial ─────────────────────────────────────────────────────────
    rate_raw = _find(text, r'STORAGE(?:[ \t]+DAILY)?[ \t]+RATE[ \t]+\$?([\d,.]+)')
    rate = _parse_money(rate_raw)
    if rate:
        result['daily_storage_rate'] = rate

    tow_raw = _find(text, r'TOW FEE[ \t]+\$?([\d,.]+)')
    tow = _parse_money(tow_raw)
    if tow:
        result['tow_fee'] = tow

    nada_raw = _find(text, r'^NADA VALUE[ \t]+\$?([\d,.]+)')
    if not _is_blank(nada_raw):
        nada = _parse_money(nada_raw)
        if nada:
            result['nada_value'] = nada

    # ── Owner ─────────────────────────────────────────────────────────────
    owner_name = _find(text, r'^OWNER NAME[ \t]+(.+)$')
    if not _is_blank(owner_name):
        result['owner_name'] = owner_name

    owner_addr = _find(text, r'^OWNER ADDRESS[ \t]+(.+)$')
    owner_city = _find(text, r'^CITY[ \t]+(.+)$')
    owner_state = _find(text, r'^STATE[ \t]+([A-Z]{2})')
    owner_zip = _find(text, r'^ZIP[ \t]+(\d{5})')

    if owner_addr and not _is_blank(owner_addr):
        csz_parts = [p for p in [owner_city, f'{owner_state} {owner_zip}'.strip()] if p]
        addr_parts = [owner_addr] + (csz_parts if csz_parts else [])
        result['owner_address'] = '\n'.join(addr_parts)
    if owner_city:
        result['owner_city'] = owner_city
    if owner_state:
        result['owner_state'] = owner_state
    if owner_zip:
        result['owner_zip'] = owner_zip

    # ── Lienholder ────────────────────────────────────────────────────────
    lien_name = _find(text, r'^LIENHOLDER NAME[ \t]+(.+)$')
    if not _is_blank(lien_name):
        result['lienholder_name'] = lien_name

    lien_addr = _find(text, r'^LIENHOLDER ADDRESS[ \t]+(.+)$')
    if not _is_blank(lien_addr):
        result['lienholder_address'] = lien_addr

    lien_city = _find(text, r'^LIENHOLDER CITY[ \t]+(.+)$')
    if lien_city:
        result['lienholder_city'] = lien_city

    lien_state = _find(text, r'^LIENHOLDER STATE[ \t]+([A-Z]{2})')
    if lien_state:
        result['lienholder_state'] = lien_state

    lien_zip = _find(text, r'^LIENHOLDER ZIP[ \t]+(\d{5})')
    if lien_zip:
        result['lienholder_zip'] = lien_zip

    return result
