"""
Generate a filled Ohio BMV 4202 title-by-abandonment packet from a Vehicle record.
Uses pypdf to write AcroForm fields into the BlankTitlePacket.pdf template.
"""

import io
import os
from datetime import date

# AcroForm field maps (from BlankTitlePacket.pdf)
STORAGE_2025_DAYS = ['Text24','Text26','Text28','Text30','Text32','Text34',
                     'Text36','Text38','Text40','Text42','Text44','Text46']
STORAGE_2025_AMTS = ['Text25','Text27','Text29','Text31','Text33','Text35',
                     'Text37','Text39','Text41','Text43','Text45','Text47']
STORAGE_2026_DAYS = ['Text82','Text84','Text86','Text88','Text90','Text92',
                     'Text94','Text96','Text98','Text100','Text102','Text104']
STORAGE_2026_AMTS = ['Text83','Text85','Text87','Text89','Text91','Text93',
                     'Text95','Text97','Text99','Text101','Text103','Text105']
# Damage list (page 3): 14 description/value row pairs, Text50/51 .. Text76/77.
# totaldv in the template sums Text51..Text77, so nothing else may be written here.
DAMAGE_DESCS = ['Text50','Text52','Text54','Text56','Text58','Text60','Text62',
                'Text64','Text66','Text68','Text70','Text72','Text74','Text76']
DAMAGE_VALS  = ['Text51','Text53','Text55','Text57','Text59','Text61','Text63',
                'Text65','Text67','Text69','Text71','Text73','Text75','Text77']


def _s(value):
    return str(value) if value is not None else ''


def _d(d):
    if not d:
        return ''
    return f'{d.month}/{d.day}/{d.year}'


def _join_address(street, city, state, zip_code):
    """'123 MAIN ST, COLUMBUS, OH 43219' — skips blank parts."""
    state_zip = ' '.join(p for p in (_s(state).strip(), _s(zip_code).strip()) if p)
    return ', '.join(p for p in (_s(street).strip(), _s(city).strip(), state_zip) if p)


def generate_title_packet(vehicle, template_path, filing_date=None):
    """
    Fill BlankTitlePacket.pdf with data from a Vehicle ORM record.
    Returns the completed PDF as bytes.

    Args:
        vehicle:       Vehicle model instance (with damage_items, letters loaded)
        template_path: Absolute path to BlankTitlePacket.pdf
        filing_date:   date override; defaults to today
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, BooleanObject
    from titlebot.storage import days_by_month

    if not os.path.isfile(template_path):
        raise FileNotFoundError(f'Title packet template not found: {template_path}')

    filing_date = filing_date or date.today()
    f = {}  # field dict

    # ── Vehicle ID ────────────────────────────────────────────────────────
    # The template reuses lowercase field names ('vin', 'make', 'model',
    # 'vehich year' [sic], 'mileage') as repeated widgets on every page,
    # including the BMV 4202 (page 6) vehicle row.
    f['vin']            = _s(vehicle.vin)
    f['make']           = _s(vehicle.make)
    f['model']          = _s(vehicle.model_name)
    f['vehich year']    = _s(vehicle.year)
    f['mileage']        = _s(vehicle.mileage)
    f['REFERENCE #']    = vehicle.vin[-6:] if vehicle.vin else ''

    # ── Owner ─────────────────────────────────────────────────────────────
    f['previous owner name']    = _s(vehicle.owner_name)
    f['previous owner address'] = _s(vehicle.owner_address)
    f['PO CITY']                 = _s(vehicle.owner_city)
    f['PO STATE']                = _s(vehicle.owner_state)
    f['PO ZIP']                  = _s(vehicle.owner_zip)
    # BMV 4202 OWNER'S ADDRESS box: one line, "street, city, state zip".
    f['previous owner full address'] = _join_address(
        vehicle.owner_address, vehicle.owner_city, vehicle.owner_state, vehicle.owner_zip)

    # ── Lienholder ────────────────────────────────────────────────────────
    f['lien holder name']       = _s(vehicle.lienholder_name) or 'None'
    # BMV 4202 LIENHOLDER'S ADDRESS box: one line, "street, city, state zip".
    f['Lien holder address']    = _join_address(
        vehicle.lienholder_address, vehicle.lienholder_city,
        vehicle.lienholder_state, vehicle.lienholder_zip)
    f['LIENHOLDER NAME']        = _s(vehicle.lienholder_name) or 'None'
    f['LIENHOLDER ADDRESS']     = _s(vehicle.lienholder_address)
    f['LIENHOLDER CITY']        = _s(vehicle.lienholder_city)
    f['LIENHOLDER STATE']       = _s(vehicle.lienholder_state)
    f['LIENHOLDER ZIP']         = _s(vehicle.lienholder_zip)

    # ── Dates ─────────────────────────────────────────────────────────────
    l1 = vehicle.letter1
    l2 = vehicle.letter2

    f['date of tow']    = _d(vehicle.impound_date)
    f['DATE OF COMPLETED REPAIR  TERM OF STORAGE'] = _d(vehicle.impound_date)
    f['date completed repair'] = _d(vehicle.impound_date)

    l1_sent = l1.sent_date if l1 else None
    f['1st letter date']                  = _d(l1_sent)
    f['DATE CERTIFIED MAIL SENT']         = _d(l1_sent)
    f['date of certified letter sent']    = _d(l1_sent)

    if vehicle.impound_type == 'PPI' and l2:
        f['2nd letter date'] = _d(l2.sent_date)

    l1_signed = l1.delivery_confirmed_date if l1 else None
    l2_signed = l2.delivery_confirmed_date if l2 else None

    # Page 1 data sheet: latest signed/undeliverable date (unchanged behaviour).
    signed = l2_signed or l1_signed
    f['date of signed certified or undeliverable notice']  = _d(signed)
    f['date of signed receipts or undeliverable']          = _d(signed)
    # Section A pairs with DATE CERTIFIED MAIL SENT (letter 1), so use letter 1's
    # delivery date (falling back to letter 2 only if letter 1 has none).
    f['DATE OF SIGNED RECEIPT OR UNDELIVERABLE NOTICE']   = _d(l1_signed or l2_signed)
    # Section B "DATES OF SIGNED RECEIPTS OR UNDELIVERABLE NOTICES": both letters.
    parts = []
    if l1_signed:
        parts.append(f'1st: {_d(l1_signed)}')
    if l2_signed:
        parts.append(f'2nd: {_d(l2_signed)}')
    f['signed receipt dates 1st 2nd'] = '    '.join(parts)

    f['Title Filing Date'] = _d(filing_date)
    f['notary day']    = str(filing_date.day)
    f['notary month']  = filing_date.strftime('%B')
    f['notary year']   = str(filing_date.year)
    f['todays year']   = str(filing_date.year)
    f['today day']     = str(filing_date.day)
    f['today month']   = filing_date.strftime('%B')
    f['today year']    = str(filing_date.year)
    f['Date2_af_date'] = _d(filing_date)
    f['notary county'] = 'Franklin'
    f['ohio']          = 'OHIO'

    # ── Storage ───────────────────────────────────────────────────────────
    daily_rate = vehicle.daily_storage_rate or 0.0
    for fld in STORAGE_2025_DAYS + STORAGE_2026_DAYS:
        f[fld] = '0'
    for fld in STORAGE_2025_AMTS + STORAGE_2026_AMTS:
        f[fld] = '0.00'

    total_storage_days = 0
    total_storage_amt  = 0.0

    if vehicle.impound_date and daily_rate > 0:
        total_storage_days = max(0, (filing_date - vehicle.impound_date).days + 1)
        total_storage_amt  = round(total_storage_days * daily_rate, 2)
        monthly = days_by_month(vehicle.impound_date, filing_date)
        for mo_idx, month_num in enumerate(range(1, 13)):
            d25 = monthly.get((2025, month_num), 0)
            f[STORAGE_2025_DAYS[mo_idx]] = str(d25)
            f[STORAGE_2025_AMTS[mo_idx]] = f'{round(d25 * daily_rate, 2):.2f}'
            d26 = monthly.get((2026, month_num), 0)
            f[STORAGE_2026_DAYS[mo_idx]] = str(d26)
            f[STORAGE_2026_AMTS[mo_idx]] = f'{round(d26 * daily_rate, 2):.2f}'

    f['Text48'] = str(total_storage_days)
    f['totalst'] = f'{total_storage_amt:.2f}'

    # ── Damage items ──────────────────────────────────────────────────────
    for fld in DAMAGE_DESCS + DAMAGE_VALS:
        f[fld] = ''
    items = sorted(vehicle.damage_items, key=lambda d: d.sort_order)
    total_damage = 0.0
    for i, item in enumerate(items[:len(DAMAGE_DESCS)]):
        f[DAMAGE_DESCS[i]] = item.description
        f[DAMAGE_VALS[i]]  = f'{item.amount:.2f}'
        total_damage += item.amount
    total_damage = round(total_damage, 2)
    f['totaldv'] = f'{total_damage:.2f}'

    # ── Financial summary ─────────────────────────────────────────────────
    nada          = vehicle.effective_nada_value or 3499.0
    tow_fee       = vehicle.tow_fee or 0.0
    additional_charges = vehicle.additional_charges_total or 0.0  # admin/gate/key-replacement fees etc.
    # (A) - (B) - (C): may be negative when damage exceeds the wholesale value.
    vehicle_value = round(nada - total_damage, 2)
    # AMOUNT PAID TO THE CLERK = vehicle value - (1) tow - (2) storage - additional
    # charges. May be negative (no floor).
    owner_payout  = round(vehicle_value - tow_fee - total_storage_amt - additional_charges, 2)
    f['wsvalue']     = f'{nada:.2f}'    # (A) on page 6 / NADA VALUE on page 1
    f['Text106']     = f'{vehicle_value:.2f}'
    # Hidden page-6 field so the template's Acrobat calculation for 'amount paid'
    # subtracts additional charges exactly like owner_payout does.
    f['additional charges'] = f'{(additional_charges or 0.0):.2f}'
    f['amount paid'] = f'{owner_payout:.2f}'

    # ── Checkboxes ────────────────────────────────────────────────────────
    f['Towing Service that removed the vehicle under'] = '/On'   # this widget's on-state is /On, not /Yes
    f['Check Box2opiijn'] = '/Yes'

    # ── Write PDF ─────────────────────────────────────────────────────────
    reader = PdfReader(template_path)
    writer = PdfWriter()
    writer.append(reader)
    clean = {k: str(v) for k, v in f.items() if v is not None}
    for page in writer.pages:
        writer.update_page_form_field_values(page, clean)
    if '/AcroForm' in writer._root_object:
        writer._root_object['/AcroForm'].update({
            NameObject('/NeedAppearances'): BooleanObject(True)
        })
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# 0-indexed page in BlankTitlePacket.pdf holding the Damage Analysis worksheet
# (Text50-Text77 damage description/value rows + totaldv).
DAMAGE_ANALYSIS_PAGE_INDEX = 2


def generate_damage_analysis_pdf(vehicle, template_path, filing_date=None):
    """
    Fill the full title packet, then return just the Damage Analysis page
    (page 3 of BlankTitlePacket.pdf) as its own single-page PDF — for printing
    that worksheet without the rest of the BMV 4202 packet.
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, BooleanObject

    full_pdf = generate_title_packet(vehicle, template_path, filing_date=filing_date)
    reader = PdfReader(io.BytesIO(full_pdf))
    writer = PdfWriter()
    writer.append(reader, pages=(DAMAGE_ANALYSIS_PAGE_INDEX, DAMAGE_ANALYSIS_PAGE_INDEX + 1))
    if '/AcroForm' in writer._root_object:
        writer._root_object['/AcroForm'].update({
            NameObject('/NeedAppearances'): BooleanObject(True)
        })
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
