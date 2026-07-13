"""
Tina's dashboard — title pipeline, court process tracker,
police affidavit tracker, junk/sell decisions, invoice creation.
"""
from datetime import date, datetime, timedelta
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app, send_file)
from flask_login import login_required, current_user
from models import (db, Vehicle, TitleFiling, Invoice, VehicleNote, DamageReport,
                    CustodyEvent, AuctionEvent)
import disposition as dispo
import pipeline_ops as ops
from pipeline_ops import move_stage as _move_stage, record_custody as _custody

bp = Blueprint('tina', __name__, url_prefix='/tina')


def _tina_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_tina:
            flash('Access restricted.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/')
@_tina_required
def dashboard():
    today = date.today()

    # Possible Release (ghost) vehicles are excluded from every queue below —
    # the compliance pipeline stops until staff verify. Same guard heather.py
    # applies to her own queues.
    # Handoff queue — vehicles Heather marked complete that Tina hasn't started
    # (title not yet in hand: still on the pre-disposition spine)
    queued = (
        Vehicle.query
        .filter_by(heather_complete=True, tina_stage='AWAITING_TITLE')
        .filter(Vehicle.possible_release.isnot(True))
        .filter(Vehicle.not_snoozed_filter())
        .order_by(Vehicle.heather_complete_date.asc())
        .all()
    )

    # In-progress disposition work — title in hand, moving through the pipeline
    in_progress = (
        Vehicle.query
        .filter(Vehicle.tina_stage.in_(['KEY_ROW', 'INSPECT_POOL', 'NEEDS_REPAIRS',
                                        'AUCTION_READY', 'AT_AUCTION', 'JUNK_PENDING', 'HOLD']))
        .filter(Vehicle.status.in_(['ACTIVE', 'TITLE_FILED']))
        .filter(Vehicle.possible_release.isnot(True))
        .filter(Vehicle.not_snoozed_filter())
        .order_by(Vehicle.tina_stage_at.asc())
        .all()
    )

    # Title-eligible and ready to file
    title_eligible = [
        v for v in Vehicle.query.filter_by(status='ACTIVE')
                                .filter(Vehicle.possible_release.isnot(True))
                                .filter(Vehicle.not_snoozed_filter()).all()
        if v.is_title_eligible and v.title_filing is None
    ]

    # Decision queue — title obtained, needs to be located + a Sell/Junk call
    disposition_needed = (
        Vehicle.query
        .filter(Vehicle.tina_stage == 'TO_LOCATE')
        .filter(Vehicle.disposition.is_(None))
        .filter(Vehicle.possible_release.isnot(True))
        .filter(Vehicle.not_snoozed_filter())
        .order_by(Vehicle.tina_stage_at.asc())
        .all()
    )

    # Repairs awaiting Jim/Tina approval (inspection said "needs repairs")
    repairs_pending = (
        Vehicle.query
        .filter(Vehicle.tina_stage == 'NEEDS_REPAIRS')
        .filter(Vehicle.repair_approved.is_(None))
        .filter(Vehicle.possible_release.isnot(True))
        .order_by(Vehicle.tina_stage_at.asc().nullslast())
        .all()
    )

    # Auctions whose flyer should be posted now (within 7 days, not advertised)
    flyer_due = [e for e in AuctionEvent.query
                 .filter(AuctionEvent.event_date >= today)
                 .order_by(AuctionEvent.event_date.asc()).all() if e.flyer_due]

    # Court dates coming up
    court_upcoming = (
        Vehicle.query
        .filter(Vehicle.court_date.isnot(None))
        .filter(Vehicle.court_date >= today)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release.isnot(True))
        .order_by(Vehicle.court_date.asc())
        .all()
    )

    # Recent invoices
    recent_invoices = (
        Invoice.query
        .order_by(Invoice.created_at.desc())
        .limit(20)
        .all()
    )

    # Damage reports submitted by drivers — unreviewed (last 60 days)
    damage_reports = (
        DamageReport.query
        .filter(DamageReport.is_locked == False)
        .order_by(DamageReport.created_at.desc())
        .limit(50)
        .all()
    )

    return render_template('tina/dashboard.html',
        today=today,
        queued=queued,
        in_progress=in_progress,
        title_eligible=title_eligible,
        disposition_needed=disposition_needed,
        repairs_pending=repairs_pending,
        flyer_due=flyer_due,
        court_upcoming=court_upcoming,
        recent_invoices=recent_invoices,
        damage_reports=damage_reports,
        can_snooze=current_user.can_see_all,
    )


@bp.route('/title-eligibility')
@_tina_required
def title_eligibility():
    """Full title-filing pipeline view: ready to file, upcoming (with countdown),
    blocked on letters, and recently filed — so Tina can see what's coming, not
    just what's ready today."""
    active = (
        Vehicle.query
        .filter_by(status='ACTIVE')
        .filter(Vehicle.not_snoozed_filter())
        .order_by(Vehicle.impound_date.asc())
        .all()
    )

    ready = sorted(
        (v for v in active if v.is_title_eligible and v.title_filing is None),
        key=lambda v: v.title_eligible_date
    )
    upcoming = sorted(
        (v for v in active
         if not v.is_title_eligible and v.title_eligible_date and v.title_filing is None),
        key=lambda v: v.title_eligible_date
    )
    blocked = sorted(
        (v for v in active if not v.title_eligible_date and v.title_filing is None),
        key=lambda v: v.impound_date
    )

    recently_filed = (
        Vehicle.query
        .filter_by(status='TITLE_FILED')
        .order_by(Vehicle.updated_at.desc())
        .limit(25)
        .all()
    )

    return render_template('tina/title_eligibility.html',
        today=date.today(),
        ready=ready,
        upcoming=upcoming,
        blocked=blocked,
        recently_filed=recently_filed,
        can_snooze=current_user.can_see_all,
    )


@bp.route('/set-stage/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_stage(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    stage = request.form.get('stage', '').strip().upper()
    notes = request.form.get('notes', '').strip()
    if stage in dispo.TERMINAL_STAGES:
        flash('Record a Sold/Junked outcome through the Invoice button so the '
              'sale or scrap details are captured.', 'warning')
        return redirect(url_for('tina.dashboard'))
    if stage not in dispo.STAGE_KEYS:
        flash('Unknown pipeline stage.', 'danger')
        return redirect(url_for('tina.dashboard'))
    _move_stage(vehicle, stage, notes)
    db.session.commit()
    flash(f'{vehicle.display_name} moved to {dispo.STAGE_LABELS[stage]}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/damage-report/<int:report_id>/lock', methods=['POST'])
@_tina_required
def lock_damage_report(report_id):
    """Mark a damage report as reviewed (locks it out of the unreviewed list)."""
    report = db.get_or_404(DamageReport, report_id)
    report.is_locked = True
    db.session.commit()
    flash('Damage report marked as reviewed.', 'success')
    return redirect(url_for('tina.dashboard'))


# ── Disposition Pipeline Board ──────────────────────────────────────────────
# Stage ladder lives in disposition.py (single source of truth). The board is
# the primary post-title surface: drag a card down its track from title to Sold
# or Junked. Dragging into a track lane also sets the SELL/JUNK decision.

# Roles alerted (via the Wally Alerts thread) when a card lands on a stage.
PIPELINE_ALERT_TARGETS = {
    'AUCTION_PREP':  {'tim', 'lawrence', 'lori', 'dispatcher'},
    'AUCTION_READY': {'tim', 'tina'},
    'AT_AUCTION':    {'tim', 'tina'},
    'JUNK_PREP':     {'tim', 'tina'},
    'HOLD':          {'tim', 'tina'},
}

PIPELINE_ALERT_MSGS = {
    'AUCTION_PREP':  '🔧 {name} in auction prep — photos, keys, inspection, detail.',
    'AUCTION_READY': '🏷 {name} is auction ready.',
    'AT_AUCTION':    '🔨 {name} routed to auction.',
    'JUNK_PREP':     '♻️ {name} routed to junk — ready to scrap.',
    'HOLD':          '⏸ {name} placed on hold.',
}


@bp.route('/pipeline')
@_tina_required
def pipeline():
    from models import User
    stage_data = []
    for key, label, track in dispo.board_columns():
        vehicles = (Vehicle.query
                    .filter_by(tina_stage=key)
                    .filter(Vehicle.possible_release.isnot(True))
                    .order_by(Vehicle.tina_stage_at.asc().nullslast())
                    .all())
        stage_data.append({'key': key, 'label': label, 'track': track,
                           'vehicles': vehicles})
    all_users = User.query.filter_by(is_active=True).order_by(User.display_name).all()
    # Serializable transition map for the client: stage -> [[key,label,terminal],…]
    transitions = {k: [[t[0], t[1], t[2]] for t in dispo.move_targets(k)]
                   for k in dispo.STAGE_KEYS}
    return render_template('tina/pipeline.html',
                           stages=stage_data,
                           all_users=all_users,
                           transitions=transitions,
                           terminal_stages=sorted(dispo.TERMINAL_STAGES))


@bp.route('/pipeline/move/<int:vehicle_id>', methods=['POST'])
@_tina_required
def pipeline_move(vehicle_id):
    from flask import jsonify as _json
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    data = request.get_json() or {}
    new_stage = (data.get('stage') or '').upper()

    if new_stage not in dispo.STAGE_KEYS:
        return _json({'error': f'Invalid stage: {new_stage}'}), 400

    # Terminal stages (Sold/Junked) require outcome data — route through the
    # invoice/capture form instead of a bare drag.
    if new_stage in dispo.TERMINAL_STAGES:
        return _json({'error': 'terminal',
                      'redirect': url_for('tina.create_invoice', vehicle_id=vehicle.id)}), 409

    _move_stage(vehicle, new_stage)

    roles = PIPELINE_ALERT_TARGETS.get(new_stage)
    if roles:
        body = PIPELINE_ALERT_MSGS.get(new_stage, '{name} moved to ' + new_stage).format(
            name=vehicle.display_name)
        ops.post_alert(body, roles=roles, alert_type='pipeline')
    db.session.commit()

    return _json({'ok': True, 'stage': new_stage,
                  'label': dispo.STAGE_LABELS.get(new_stage, new_stage),
                  'disposition': vehicle.disposition,
                  'vehicle_name': vehicle.display_name})


@bp.route('/set-disposition/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_disposition(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    disposition = request.form.get('disposition', '').strip().upper()
    notes = request.form.get('notes', '').strip()
    if disposition in ('SELL', 'JUNK', 'HOLD'):
        vehicle.disposition = disposition
        vehicle.disposition_set_date = date.today()
        vehicle.disposition_notes = notes or None
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Disposition set to {disposition}. {notes}'.strip(),
            author=current_user.display_name or 'Tina',
            created_at=datetime.utcnow(),
        ))
        # Advance the pipeline to that track's first working stage, unless the
        # vehicle is already further along that same track.
        entry = dispo.DISPOSITION_ENTRY.get(disposition)
        if entry and vehicle.tina_stage not in dispo.allowed_stages_for(disposition) - {'AWAITING_TITLE', 'TO_LOCATE'}:
            _move_stage(vehicle, entry)
        else:
            vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'{vehicle.display_name} disposition set to {disposition}.', 'success')
    return redirect(url_for('tina.dashboard'))


def _repair_decider_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('tina', 'tim', 'jim'):
            flash('Only Jim or Tina can decide repairs.', 'danger')
            return redirect(url_for('tina.dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/repair/<int:vehicle_id>/approve', methods=['POST'])
@_repair_decider_required
def repair_approve(vehicle_id):
    """Jim/Tina authorize repairs — the car is cleared to be fixed and auctioned."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.repair_approved = True
    vehicle.repair_decided_by = ops.actor()
    vehicle.repair_decided_at = datetime.utcnow()
    _custody(vehicle, 'repair', f'Repairs APPROVED by {ops.actor()}')
    _move_stage(vehicle, 'AUCTION_READY', note='repairs approved')
    ops.post_alert(f'✅ {vehicle.display_name}: repairs approved — cleared for auction.',
                   roles=['tina', 'tim', 'jim'], alert_type='repair')
    db.session.commit()
    flash(f'Repairs approved for {vehicle.display_name}.', 'success')
    return redirect(request.referrer or url_for('tina.dashboard'))


@bp.route('/repair/<int:vehicle_id>/deny', methods=['POST'])
@_repair_decider_required
def repair_deny(vehicle_id):
    """Jim/Tina decline repairs — not worth it, send to junk."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.repair_approved = False
    vehicle.repair_decided_by = ops.actor()
    vehicle.repair_decided_at = datetime.utcnow()
    _custody(vehicle, 'repair', f'Repairs DENIED by {ops.actor()} — to junk')
    _move_stage(vehicle, 'JUNK_PENDING', note='repairs denied → junk')
    ops.post_alert(f'🚫 {vehicle.display_name}: repairs denied — routed to junk.',
                   roles=['tina', 'tim', 'jim'], alert_type='repair')
    db.session.commit()
    flash(f'Repairs denied for {vehicle.display_name} — sent to junk.', 'info')
    return redirect(request.referrer or url_for('tina.dashboard'))


@bp.route('/set-court/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_court(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    court_date_str = request.form.get('court_date', '').strip()
    notes = request.form.get('notes', '').strip()
    if court_date_str:
        vehicle.court_date = date.fromisoformat(court_date_str)
        vehicle.court_notes = notes or None
        # Court is part of acquiring title — it's tracked by court_date, not a
        # ladder stage, so leave the pipeline position where it is.
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'Court date set for {vehicle.display_name}: {vehicle.court_date.strftime("%m/%d/%Y")}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/set-affidavit/<int:vehicle_id>', methods=['POST'])
@_tina_required
def set_affidavit(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    filed_str = request.form.get('filed_date', '').strip()
    notes = request.form.get('notes', '').strip()
    vehicle.affidavit_filed_date = date.fromisoformat(filed_str) if filed_str else date.today()
    vehicle.affidavit_notes = notes or None
    # Affidavit is part of acquiring title (tracked by affidavit_filed_date);
    # don't move the vehicle off its current pipeline stage.
    vehicle.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f'Police affidavit filed for {vehicle.display_name}.', 'success')
    return redirect(url_for('tina.dashboard'))


@bp.route('/create-invoice/<int:vehicle_id>', methods=['GET', 'POST'])
@_tina_required
def create_invoice(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    if request.method == 'POST':
        invoice_type = request.form.get('invoice_type', '').upper()
        if invoice_type not in ('SALE', 'JUNK'):
            flash('Invalid invoice type.', 'danger')
            return redirect(url_for('tina.create_invoice', vehicle_id=vehicle_id))

        from titlebot.storage import calculate_storage
        storage_days, storage_total, _ = calculate_storage(
            vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
        )

        def _f(name):
            v = request.form.get(name, '').strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None

        # Build invoice number: INV-{type}-{vehicle_id}-{YYYYMMDD}
        inv_num = f'INV-{invoice_type[:1]}-{vehicle.id:05d}-{date.today().strftime("%Y%m%d")}'

        tow = vehicle.tow_fee or 0
        total_fees = tow + storage_total

        if invoice_type == 'SALE':
            sale_price = _f('sale_price') or 0
            net = sale_price - total_fees
            inv = Invoice(
                vehicle_id=vehicle.id,
                invoice_type='SALE',
                invoice_number=inv_num,
                issue_date=date.today(),
                buyer_name=request.form.get('buyer_name', '').strip() or None,
                buyer_address=request.form.get('buyer_address', '').strip() or None,
                sale_price=sale_price,
                tow_fee=tow,
                storage_fee=storage_total,
                storage_days=storage_days,
                total_fees=total_fees,
                net_proceeds=net,
                notes=request.form.get('notes', '').strip() or None,
            )
            vehicle.sale_price = sale_price
            vehicle.sale_date = date.today()
            vehicle.buyer_name = request.form.get('buyer_name', '').strip() or None
            vehicle.auctioneer = request.form.get('auctioneer', '').strip() or None
            vehicle.auction_lot = request.form.get('auction_lot', '').strip() or None
            _auction_date = request.form.get('auction_date', '').strip()
            vehicle.auction_date = date.fromisoformat(_auction_date) if _auction_date else date.today()
            vehicle.disposition = 'SELL'
            vehicle.disposition_outcome = 'SOLD'
            vehicle.status = 'RELEASED'
            vehicle.tina_stage = 'SOLD'
            vehicle.tina_stage_at = datetime.utcnow()
        else:
            weight = _f('weight_lbs')
            ppt = _f('price_per_ton')
            gross = round((weight / 2000) * ppt, 2) if weight and ppt else 0
            net = gross - total_fees
            inv = Invoice(
                vehicle_id=vehicle.id,
                invoice_type='JUNK',
                invoice_number=inv_num,
                issue_date=date.today(),
                junk_yard_name=request.form.get('junk_yard_name', '').strip() or None,
                junk_yard_address=request.form.get('junk_yard_address', '').strip() or None,
                weight_lbs=weight,
                price_per_ton=ppt,
                tow_fee=tow,
                storage_fee=storage_total,
                storage_days=storage_days,
                total_fees=total_fees,
                net_proceeds=net,
                notes=request.form.get('notes', '').strip() or None,
            )
            vehicle.junk_weight_lbs = weight
            vehicle.junk_price_per_ton = ppt
            vehicle.junk_yard_name = request.form.get('junk_yard_name', '').strip() or None
            vehicle.disposition = 'JUNK'
            vehicle.disposition_outcome = 'JUNKED'
            vehicle.status = 'RELEASED'
            vehicle.tina_stage = 'JUNKED'
            vehicle.tina_stage_at = datetime.utcnow()

        db.session.add(inv)
        db.session.add(VehicleNote(
            vehicle_id=vehicle.id,
            body=f'Invoice {inv_num} created ({invoice_type}). Net: ${net:.2f}',
            author=current_user.display_name or 'Tina',
            created_at=datetime.utcnow(),
        ))
        # Both disposition branches (SELL / JUNK) set status RELEASED above —
        # stamp the release timestamp so it lands on Lawrence's Daily Release List.
        vehicle.released_at = datetime.utcnow()
        vehicle.released_by = current_user.username
        vehicle.updated_at = datetime.utcnow()
        db.session.commit()
        flash(f'Invoice {inv_num} created successfully.', 'success')
        return redirect(url_for('tina.invoice_print', invoice_id=inv.id))

    from titlebot.storage import calculate_storage
    storage_days, storage_total, _ = calculate_storage(
        vehicle.impound_date, date.today(), vehicle.daily_storage_rate or 0
    )
    return render_template('tina/create_invoice.html',
        vehicle=vehicle,
        storage_days=storage_days,
        storage_total=storage_total,
        today=date.today(),
    )


@bp.route('/disposition-report')
@_tina_required
def disposition_report():
    """Monitor every vehicle after title: what's still moving through the
    pipeline (by stage) and what has already been Sold or Junked, with proceeds.
    The single 'where does everything stand post-title' view."""
    today = date.today()

    # In-flight — title in hand, on the pipeline, not yet a terminal outcome.
    in_flight_stages = ['TO_LOCATE', 'KEY_ROW', 'INSPECT_POOL', 'NEEDS_REPAIRS',
                        'AUCTION_READY', 'AT_AUCTION', 'JUNK_PENDING', 'HOLD']
    in_flight = (
        Vehicle.query
        .filter(Vehicle.tina_stage.in_(in_flight_stages))
        .filter(Vehicle.disposition_outcome.is_(None))
        .filter(Vehicle.possible_release.isnot(True))
        .order_by(Vehicle.tina_stage_at.asc().nullslast())
        .all()
    )
    by_stage = {k: [] for k in in_flight_stages}
    for v in in_flight:
        by_stage.setdefault(v.tina_stage, []).append(v)
    stage_summary = [
        {'key': k, 'label': dispo.STAGE_LABELS[k], 'vehicles': by_stage.get(k, [])}
        for k in in_flight_stages
    ]

    # Completed outcomes (most recent).
    sold = (
        Vehicle.query
        .filter(Vehicle.disposition_outcome == 'SOLD')
        .order_by(Vehicle.sale_date.desc().nullslast())
        .limit(100)
        .all()
    )
    junked = (
        Vehicle.query
        .filter(Vehicle.disposition_outcome == 'JUNKED')
        .order_by(Vehicle.updated_at.desc())
        .limit(100)
        .all()
    )
    sold_total = sum((v.sale_price or 0) for v in sold)
    junk_total = sum(((v.junk_weight_lbs or 0) / 2000) * (v.junk_price_per_ton or 0)
                     for v in junked)

    return render_template('tina/disposition_report.html',
        today=today,
        stage_summary=stage_summary,
        in_flight_count=len(in_flight),
        sold=sold,
        junked=junked,
        sold_total=sold_total,
        junk_total=junk_total,
    )


# ── Auction events (1st & 3rd Saturday) ─────────────────────────────────────

def _first_and_third_saturdays(count):
    """Return the next `count` upcoming 1st- and 3rd-Saturday dates."""
    out = []
    today = date.today()
    y, mo = today.year, today.month
    while len(out) < count:
        # Saturdays in month y-mo
        d = date(y, mo, 1)
        sats = []
        while d.month == mo:
            if d.weekday() == 5:  # Saturday
                sats.append(d)
            d += timedelta(days=1)
        for idx in (0, 2):  # 1st and 3rd
            if idx < len(sats) and sats[idx] >= today and len(out) < count:
                out.append(sats[idx])
        mo += 1
        if mo > 12:
            mo = 1; y += 1
    return out


@bp.route('/auctions')
@_tina_required
def auctions():
    today = date.today()
    upcoming = (AuctionEvent.query
                .filter(AuctionEvent.event_date >= today)
                .order_by(AuctionEvent.event_date.asc()).all())
    past = (AuctionEvent.query
            .filter(AuctionEvent.event_date < today)
            .order_by(AuctionEvent.event_date.desc()).limit(10).all())
    # Cars diagnosed auction-ready but not yet on an event
    ready = (Vehicle.query
             .filter(Vehicle.tina_stage == 'AUCTION_READY')
             .filter(Vehicle.possible_release.isnot(True))
             .order_by(Vehicle.tina_stage_at.asc().nullslast()).all())
    return render_template('tina/auctions.html',
        today=today, upcoming=upcoming, past=past, ready=ready,
        venues=dispo.AUCTION_VENUES,
        suggested=_first_and_third_saturdays(6))


@bp.route('/auctions/create', methods=['POST'])
@_tina_required
def auction_create():
    d = request.form.get('event_date', '').strip()
    venue = request.form.get('venue', '').strip()
    label = request.form.get('label', '').strip()
    if not d:
        flash('Pick a date.', 'danger')
        return redirect(url_for('tina.auctions'))
    ev = AuctionEvent(event_date=date.fromisoformat(d),
                      venue=venue if venue in dispo.AUCTION_VENUE_LABELS else None,
                      label=label or None, created_at=datetime.utcnow())
    db.session.add(ev)
    db.session.commit()
    flash(f'Auction added for {ev.event_date.strftime("%b %-d")}.', 'success')
    return redirect(url_for('tina.auctions'))


@bp.route('/auctions/generate', methods=['POST'])
@_tina_required
def auction_generate():
    """Add the next 6 first/third-Saturday dates that don't already exist."""
    venue = request.form.get('venue', '').strip()
    venue = venue if venue in dispo.AUCTION_VENUE_LABELS else None
    existing = {e.event_date for e in AuctionEvent.query.all()}
    added = 0
    for d in _first_and_third_saturdays(6):
        if d not in existing:
            db.session.add(AuctionEvent(event_date=d, venue=venue, created_at=datetime.utcnow()))
            added += 1
    db.session.commit()
    flash(f'Added {added} auction date(s).', 'success')
    return redirect(url_for('tina.auctions'))


@bp.route('/auctions/<int:event_id>/advertised', methods=['POST'])
@_tina_required
def auction_advertised(event_id):
    ev = db.get_or_404(AuctionEvent, event_id)
    ev.advertised = True
    ev.advertised_at = datetime.utcnow()
    ev.advertised_by = ops.actor()
    db.session.commit()
    flash('Marked as advertised.', 'success')
    return redirect(url_for('tina.auctions'))


@bp.route('/auctions/<int:event_id>/delete', methods=['POST'])
@_tina_required
def auction_delete(event_id):
    ev = db.get_or_404(AuctionEvent, event_id)
    # Unhook any assigned cars first
    for v in ev.vehicles.all():
        v.auction_event_id = None
    db.session.delete(ev)
    db.session.commit()
    flash('Auction removed.', 'info')
    return redirect(url_for('tina.auctions'))


@bp.route('/auctions/assign/<int:vehicle_id>', methods=['POST'])
@_tina_required
def auction_assign(vehicle_id):
    """Put an auction-ready car onto an event → moves it to At Auction and sets
    the venue from the event."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    event_id = request.form.get('event_id', '')
    ev = db.session.get(AuctionEvent, int(event_id)) if event_id.isdigit() else None
    if not ev:
        flash('Pick an auction to assign to.', 'danger')
        return redirect(url_for('tina.auctions'))
    vehicle.auction_event_id = ev.id
    vehicle.auction_venue = ev.venue
    vehicle.auction_date = ev.event_date
    _move_stage(vehicle, 'AT_AUCTION',
                note=f'assigned to {ev.venue_label} auction {ev.event_date.strftime("%b %-d")}')
    db.session.commit()
    flash(f'{vehicle.display_name} → At Auction ({ev.event_date.strftime("%b %-d")}).', 'success')
    return redirect(url_for('tina.auctions'))


@bp.route('/auctions/unassign/<int:vehicle_id>', methods=['POST'])
@_tina_required
def auction_unassign(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.auction_event_id = None
    _move_stage(vehicle, 'AUCTION_READY', note='pulled from auction')
    db.session.commit()
    flash(f'{vehicle.display_name} pulled back to Auction Ready.', 'info')
    return redirect(url_for('tina.auctions'))


@bp.route('/junk-reconciliation')
@_tina_required
def junk_reconciliation():
    """Ohio Steel reconciliation — every junked car with its documented
    converter status, so a later 'these had no converters' deduction can be
    checked against our own record instead of taken on faith."""
    junked = (Vehicle.query
              .filter(Vehicle.disposition_outcome == 'JUNKED')
              .order_by(Vehicle.updated_at.desc()).limit(300).all())
    present = [v for v in junked if v.converter_present is True]
    missing = [v for v in junked if v.converter_present is False]
    unchecked = [v for v in junked if v.converter_present is None]
    return render_template('tina/junk_reconciliation.html',
        today=date.today(), junked=junked,
        n_present=len(present), n_missing=len(missing), n_unchecked=len(unchecked))


@bp.route('/invoice/<int:invoice_id>/print')
@_tina_required
def invoice_print(invoice_id):
    inv = db.get_or_404(Invoice, invoice_id)
    return render_template('tina/invoice_print.html',
        inv=inv,
        vehicle=inv.vehicle,
        today=date.today(),
        company_name=current_app.config['COMPANY_NAME'],
        company_address=current_app.config['COMPANY_ADDRESS'],
        company_phone=current_app.config['COMPANY_PHONE'],
    )
