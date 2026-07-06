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
DAMAGE_DESCS = ['Text50','Text52','Text54','Text56','Text58',
                'Text60','Text62','Text64','Text66']
DAMAGE_VALS  = ['Text51','Text53','Text55','Text57','Text59',
                'Text61','Text63','Text65','Text67']


def _s(value):
    return str(value) if value is not None else ''


def _d(d):
    if not d:
        return ''
    return f'{d.month}/{d.day}/{d.year}'


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
    f['VIN']            = _s(vehicle.vin)
    f['Make']           = _s(vehicle.make)
    f['Model']          = _s(vehicle.model_name)
    f['REFERENCE #']    = vehicle.vin[-6:] if vehicle.vin else ''
    f['Text68']         = _s(vehicle.year)     # DMG_YEAR_FIELD
    f['Text69']         = _s(vehicle.make)     # DMG_MAKE_FIELD
    f['Text70']         = _s(vehicle.model_name)  # DMG_MODEL_FIELD
    f['Text71']         = _s(vehicle.vin)      # DMG_VIN_FIELD

    # ── Owner ─────────────────────────────────────────────────────────────
    f['previous owner name']    = _s(vehicle.owner_name)
    f['previous owner address'] = _s(vehicle.owner_address)

    # ── Lienholder ────────────────────────────────────────────────────────
    f['lien holder name']       = _s(vehicle.lienholder_name) or 'None'
    f['Lien holder address']    = _s(vehicle.lienholder_address)
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

    l1_sent = l1.sent_date if l1 else None
    f['1st letter date']                  = _d(l1_sent)
    f['DATE CERTIFIED MAIL SENT']         = _d(l1_sent)
    f['date of certified letter sent']    = _d(l1_sent)

    if vehicle.impound_type == 'PPI' and l2:
        f['2nd letter date'] = _d(l2.sent_date)

    signed = None
    if l2 and l2.delivery_confirmed_date:
        signed = l2.delivery_confirmed_date
    elif l1 and l1.delivery_confirmed_date:
        signed = l1.delivery_confirmed_date
    f['date of signed certified or undeliverable notice']  = _d(signed)
    f['date of signed receipts or undeliverable']          = _d(signed)
    f['DATE OF SIGNED RECEIPT OR UNDELIVERABLE NOTICE']   = _d(signed)

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
    for i, item in enumerate(items[:9]):
        f[DAMAGE_DESCS[i]] = item.description
        f[DAMAGE_VALS[i]]  = f'{item.amount:.2f}'
        total_damage += item.amount
    total_damage = round(total_damage, 2)
    f['totaldv'] = f'{total_damage:.2f}'

    # ── Financial summary ─────────────────────────────────────────────────
    nada          = vehicle.effective_nada_value or 3499.0
    tow_fee       = vehicle.tow_fee or 0.0
    additional_charges = vehicle.additional_charges_total  # admin/gate/key-replacement fees etc.
    vehicle_value = max(0.0, nada - total_damage)
    owner_payout  = max(0.0, vehicle_value - tow_fee - total_storage_amt - additional_charges)
    f['Text106']     = f'{vehicle_value:.2f}'
    f['amount paid'] = f'{owner_payout:.2f}'

    # ── Checkboxes ────────────────────────────────────────────────────────
    f['Towing Service that removed the vehicle under'] = '/Yes'
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
