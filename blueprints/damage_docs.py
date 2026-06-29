"""
Feature 3 — Vehicle Damage Documentation
Drivers use a mobile form (no login required for submission) to photograph,
diagram, and describe vehicle damage at time of impound.
"""
import io
import json
import base64
import os
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, current_app)
from flask_login import login_required, current_user
from models import db, Vehicle, DamageReport, DamagePhoto, DamageDot
from permissions import require_permission

bp = Blueprint('damage_docs', __name__, url_prefix='/damage')


# ── PDF generation ─────────────────────────────────────────────────────────────

def _generate_pdf(report: DamageReport) -> bytes:
    """Build and return a PDF for the given DamageReport using reportlab."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, Image as RLImage,
                                        HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    except ImportError:
        # reportlab not installed — return a minimal placeholder
        return b'%PDF-1.4 placeholder - install reportlab'

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'Title', parent=styles['Title'],
        fontSize=16, spaceAfter=4
    )
    h2_style = ParagraphStyle(
        'H2', parent=styles['Heading2'],
        fontSize=11, spaceAfter=4
    )
    normal = styles['Normal']
    small = ParagraphStyle('Small', parent=normal, fontSize=8, textColor=colors.grey)

    vehicle = report.vehicle
    damage_label = 'Pre-Existing Damage' if report.damage_type == 'pre_existing' else 'Customer Claim'

    story = []

    # ── Watermark function (injected into canvas) ──────────────────────────────
    def _add_watermark(canvas, doc):
        if report.is_dispute:
            canvas.saveState()
            canvas.setFont('Helvetica-Bold', 40)
            canvas.setFillColorRGB(0.9, 0.1, 0.1, alpha=0.18)
            canvas.translate(letter[0] / 2, letter[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, 'LOCKED — DO NOT ALTER')
            canvas.restoreState()

    # ── Header ─────────────────────────────────────────────────────────────────
    story.append(Paragraph('Vehicle Damage Report', title_style))
    story.append(Paragraph('Broad &amp; James Towing', h2_style))
    story.append(Paragraph('3201 E Broad St, Columbus, OH 43213 | (614) 235-4700', small))
    story.append(HRFlowable(width='100%', thickness=1, color=colors.grey))
    story.append(Spacer(1, 8))

    # ── Dispute alert ──────────────────────────────────────────────────────────
    if report.is_dispute:
        dispute_style = ParagraphStyle(
            'Dispute', parent=normal, fontSize=10, textColor=colors.white,
            backColor=colors.red, spaceBefore=6, spaceAfter=6,
            leftIndent=6, rightIndent=6, borderPad=6
        )
        story.append(Paragraph(
            '  *** CUSTOMER CLAIM — FLAGGED AS DISPUTE — LOCKED ***  ',
            dispute_style
        ))
        story.append(Spacer(1, 6))

    # ── Vehicle info ───────────────────────────────────────────────────────────
    story.append(Paragraph('Vehicle Information', h2_style))

    def _fmt(val, fallback='—'):
        return str(val) if val else fallback

    v_data = [
        ['Call Number', _fmt(report.call_number)],
        ['Year / Make / Model', f"{_fmt(vehicle.year if vehicle else None)} "
                                f"{_fmt(vehicle.make if vehicle else None)} "
                                f"{_fmt(vehicle.model_name if vehicle else None)}"],
        ['Plate', _fmt(vehicle.plate if vehicle else None)],
        ['VIN', _fmt(vehicle.vin if vehicle else None)],
        ['Color', _fmt(vehicle.color if vehicle else None)],
    ]
    v_table = Table(v_data, colWidths=[2 * inch, 4.5 * inch])
    v_table.setStyle(TableStyle([
        ('FONTNAME',   (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(v_table)
    story.append(Spacer(1, 10))

    # ── Damage info ────────────────────────────────────────────────────────────
    story.append(Paragraph('Damage Information', h2_style))
    d_data = [
        ['Damage Type', damage_label],
        ['Owner Present', 'Yes' if report.owner_present else 'No'],
        ['Driver / Inspector', _fmt(report.driver_name)],
        ['Description', _fmt(report.description)],
    ]
    d_table = Table(d_data, colWidths=[2 * inch, 4.5 * inch])
    d_table.setStyle(TableStyle([
        ('FONTNAME',   (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(d_table)
    story.append(Spacer(1, 10))

    # ── GPS + timestamp ────────────────────────────────────────────────────────
    story.append(Paragraph('Location &amp; Timestamp', h2_style))
    ts = report.created_at.strftime('%B %d, %Y at %I:%M %p UTC') if report.created_at else '—'
    gps = f'{report.latitude:.6f}, {report.longitude:.6f}' if report.latitude and report.longitude else '—'
    g_data = [
        ['Timestamp', ts],
        ['GPS Coordinates', gps],
    ]
    g_table = Table(g_data, colWidths=[2 * inch, 4.5 * inch])
    g_table.setStyle(TableStyle([
        ('FONTNAME',   (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(g_table)
    story.append(Spacer(1, 10))

    # ── Damage dots ────────────────────────────────────────────────────────────
    if report.dots:
        story.append(Paragraph('Damage Locations (Diagram)', h2_style))
        dot_data = [['#', 'Location (X%, Y%)', 'Description']]
        for i, dot in enumerate(report.dots, 1):
            dot_data.append([
                str(i),
                f'{dot.x_pct:.1f}%, {dot.y_pct:.1f}%',
                dot.label or '—',
            ])
        dot_table = Table(dot_data, colWidths=[0.4 * inch, 1.5 * inch, 4.6 * inch])
        dot_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgrey),
            ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ('GRID',       (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(dot_table)
        story.append(Spacer(1, 10))

    # ── Photos ─────────────────────────────────────────────────────────────────
    photos_to_embed = report.photos[:6]
    if photos_to_embed:
        story.append(Paragraph(f'Photos ({len(report.photos)} captured)', h2_style))
        photo_cells = []
        row = []
        for idx, photo in enumerate(photos_to_embed):
            try:
                img_data = photo.image_data
                if ',' in img_data:
                    img_data = img_data.split(',', 1)[1]
                img_bytes = base64.b64decode(img_data)
                img_buf = io.BytesIO(img_bytes)
                rl_img = RLImage(img_buf, width=3 * inch, height=2.25 * inch)
                rl_img.hAlign = 'CENTER'
                row.append(rl_img)
            except Exception:
                row.append(Paragraph(f'Photo {idx + 1} (error)', small))

            if len(row) == 2:
                photo_cells.append(row)
                row = []
        if row:
            while len(row) < 2:
                row.append('')
            photo_cells.append(row)

        photo_table = Table(photo_cells, colWidths=[3.25 * inch, 3.25 * inch])
        photo_table.setStyle(TableStyle([
            ('ALIGN',    (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',   (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(photo_table)
        story.append(Spacer(1, 10))

    # ── Signature ──────────────────────────────────────────────────────────────
    if report.signature_data:
        story.append(Paragraph('Driver Signature', h2_style))
        try:
            sig_data = report.signature_data
            if ',' in sig_data:
                sig_data = sig_data.split(',', 1)[1]
            sig_bytes = base64.b64decode(sig_data)
            sig_buf = io.BytesIO(sig_bytes)
            sig_img = RLImage(sig_buf, width=2.5 * inch, height=0.8 * inch)
            story.append(sig_img)
        except Exception:
            story.append(Paragraph('(Signature on file)', normal))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f'Signed by: {report.driver_name or "—"} | {ts}',
            small
        ))

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width='100%', thickness=1, color=colors.grey))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f'Report ID: {report.id} | Generated by Broad &amp; James Towing Impound Manager',
        small
    ))
    if report.is_locked or report.is_dispute:
        story.append(Paragraph('THIS REPORT IS LOCKED AND CANNOT BE ALTERED.', ParagraphStyle(
            'LockNote', parent=small, textColor=colors.red, fontName='Helvetica-Bold'
        )))

    doc.build(story, onFirstPage=_add_watermark, onLaterPages=_add_watermark)
    return buf.getvalue()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _post_wally_alert(vehicle: Vehicle, report: DamageReport):
    """Add a VehicleNote flagging the dispute — Wally alert stub."""
    try:
        from models import VehicleNote
        if vehicle:
            msg = (
                f'DAMAGE REPORT #{report.id} filed — '
                f'{"CUSTOMER CLAIM / DISPUTE" if report.is_dispute else "Pre-Existing"} — '
                f'Driver: {report.driver_name or "unknown"}'
            )
            db.session.add(VehicleNote(
                vehicle_id=vehicle.id,
                body=msg,
                author='Wally (auto)',
                created_at=datetime.utcnow(),
            ))
    except Exception:
        pass  # never crash the submit route due to alerting


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route('/form')
@login_required
def damage_form():
    """Show the damage documentation form (mobile-optimized)."""
    call_number = request.args.get('call_number', '')
    vehicle = None
    if call_number:
        vehicle = Vehicle.query.filter_by(call_number=call_number).first()
    return render_template('damage_docs/form.html',
                           vehicle=vehicle,
                           prefill_call=call_number)


@bp.route('/form/<int:vehicle_id>')
@login_required
def damage_form_vehicle(vehicle_id):
    """Pre-fill the damage form for a specific vehicle."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    return render_template('damage_docs/form.html',
                           vehicle=vehicle,
                           prefill_call=vehicle.call_number or '')


@bp.route('/lookup')
@login_required
def damage_lookup():
    """AJAX endpoint — return vehicle JSON by call_number."""
    call_number = request.args.get('call_number', '').strip()
    if not call_number:
        return jsonify({'found': False})
    vehicle = Vehicle.query.filter_by(call_number=call_number).first()
    if not vehicle:
        # also try stock_number
        vehicle = Vehicle.query.filter_by(stock_number=call_number).first()
    if not vehicle:
        return jsonify({'found': False})
    return jsonify({
        'found': True,
        'vehicle_id': vehicle.id,
        'year': vehicle.year,
        'make': vehicle.make,
        'model_name': vehicle.model_name or vehicle.model,
        'plate': vehicle.plate,
        'color': vehicle.color,
        'vin': vehicle.vin,
        'call_number': vehicle.call_number,
    })


@bp.route('/submit', methods=['POST'])
def damage_submit():
    """
    Accept a JSON damage report from the mobile form.
    No login required — drivers submit from the field.
    """
    data = request.get_json(force=True, silent=True) or {}

    vehicle_id = data.get('vehicle_id')
    call_number = data.get('call_number', '').strip()
    damage_type = data.get('damage_type', 'pre_existing')
    description = data.get('description', '').strip()
    owner_present = bool(data.get('owner_present', False))
    driver_name = data.get('driver_name', '').strip()
    latitude = data.get('latitude')
    longitude = data.get('longitude')
    signature = data.get('signature', '')
    photos_b64 = data.get('photos', [])
    dots_raw = data.get('dots', [])

    vehicle = None
    if vehicle_id:
        vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None and call_number:
        vehicle = Vehicle.query.filter_by(call_number=call_number).first()

    is_dispute = damage_type == 'customer_claim'

    report = DamageReport(
        vehicle_id=vehicle.id if vehicle else None,
        call_number=call_number or (vehicle.call_number if vehicle else None),
        damage_type=damage_type,
        description=description,
        owner_present=owner_present,
        driver_name=driver_name,
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
        signature_data=signature or None,
        is_dispute=is_dispute,
        is_locked=is_dispute,
        submitted_by=driver_name or 'field',
        created_at=datetime.utcnow(),
    )
    db.session.add(report)
    db.session.flush()  # get report.id

    # Photos
    for idx, img_b64 in enumerate(photos_b64):
        db.session.add(DamagePhoto(
            report_id=report.id,
            image_data=img_b64,
            sort_order=idx,
        ))

    # Dots
    for idx, dot in enumerate(dots_raw):
        db.session.add(DamageDot(
            report_id=report.id,
            x_pct=float(dot.get('x_pct', 0)),
            y_pct=float(dot.get('y_pct', 0)),
            label=str(dot.get('label', '')),
            sort_order=idx,
        ))

    db.session.flush()

    # Generate and store PDF
    try:
        pdf_bytes = _generate_pdf(report)
        report.pdf_data = pdf_bytes
    except Exception as exc:
        current_app.logger.warning(f'DamageReport PDF generation failed: {exc}')

    # Wally alert
    _post_wally_alert(vehicle, report)

    db.session.commit()

    return jsonify({'ok': True, 'report_id': report.id})


@bp.route('/report/<int:report_id>')
@login_required
def damage_view(report_id):
    """View a submitted damage report."""
    report = db.get_or_404(DamageReport, report_id)
    return render_template('damage_docs/view.html', report=report)


@bp.route('/report/<int:report_id>/pdf')
@login_required
def damage_pdf(report_id):
    """Stream the stored PDF for a damage report."""
    report = db.get_or_404(DamageReport, report_id)
    if not report.pdf_data:
        # Regenerate on-the-fly
        try:
            pdf_bytes = _generate_pdf(report)
            report.pdf_data = pdf_bytes
            db.session.commit()
        except Exception as exc:
            flash(f'PDF generation failed: {exc}', 'danger')
            return redirect(url_for('damage_docs.damage_view', report_id=report_id))

    filename = f'DamageReport_{report_id}_{datetime.utcnow().strftime("%Y%m%d")}.pdf'
    return send_file(
        io.BytesIO(report.pdf_data),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=filename,
    )


@bp.route('/reports')
@require_permission('all_access')
def damage_reports_list():
    """List all damage reports (admin only)."""

    reports = (DamageReport.query
               .order_by(DamageReport.created_at.desc())
               .limit(200)
               .all())

    if request.accept_mimetypes.best == 'application/json':
        return jsonify([{
            'id': r.id,
            'call_number': r.call_number,
            'damage_type': r.damage_type,
            'is_dispute': r.is_dispute,
            'driver_name': r.driver_name,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        } for r in reports])

    return render_template('damage_docs/list.html', reports=reports)


@bp.route('/vehicle/<int:vehicle_id>/reports')
@login_required
def vehicle_damage_reports(vehicle_id):
    """List damage reports for a specific vehicle."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    reports = (DamageReport.query
               .filter_by(vehicle_id=vehicle_id)
               .order_by(DamageReport.created_at.desc())
               .all())
    return jsonify([{
        'id': r.id,
        'damage_type': r.damage_type,
        'is_dispute': r.is_dispute,
        'driver_name': r.driver_name,
        'created_at': r.created_at.isoformat() if r.created_at else None,
        'photo_count': len(r.photos),
        'view_url': url_for('damage_docs.damage_view', report_id=r.id),
        'pdf_url': url_for('damage_docs.damage_pdf', report_id=r.id),
    } for r in reports])
