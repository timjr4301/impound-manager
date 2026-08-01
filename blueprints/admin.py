"""
Admin blueprint — user management (Tim/Jim only).
"""
import os
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, User, PoliceDepartment, Vehicle, VehicleNote
from permissions import has_permission
import vin_decode

bp = Blueprint('admin', __name__, url_prefix='/admin')


def _admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not has_permission(current_user, 'all_access'):
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


def _tim_only_required(f):
    """Owner-only gate (Tim & Jim). Stricter than _admin_required, which also
    allows lawrence/brady. Covers fee-sensitive / admin tools (PD rate table,
    truck reclassification). Jim is a co-owner and, for business continuity,
    has the same access Tim does."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role not in ('tim', 'jim'):
            flash('This page is for owners (Tim & Jim) only.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


@bp.route('/users')
@_admin_required
def users():
    all_users = User.query.order_by(User.role, User.display_name).all()
    return render_template('admin/users.html', users=all_users)


@bp.route('/users/new', methods=['POST'])
@_admin_required
def users_new():
    username = request.form.get('username', '').strip().lower()
    display_name = request.form.get('display_name', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role', 'driver').strip()
    phone = request.form.get('phone', '').strip() or None

    if not username or not password:
        flash('Username and password are required.', 'danger')
        return redirect(url_for('admin.users'))

    if User.query.filter_by(username=username).first():
        flash(f'Username "{username}" is already taken.', 'danger')
        return redirect(url_for('admin.users'))

    u = User(username=username, display_name=display_name or username.title(),
             role=role, email=phone)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash(f'User "{username}" created.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/users/<int:user_id>/edit', methods=['POST'])
@_admin_required
def users_edit(user_id):
    user = db.get_or_404(User, user_id)
    user.display_name = request.form.get('display_name', '').strip() or user.display_name
    user.role = request.form.get('role', user.role).strip()
    user.email = request.form.get('phone', '').strip() or user.email
    new_password = request.form.get('password', '').strip()
    if new_password:
        user.set_password(new_password)
    db.session.commit()
    flash(f'User "{user.username}" updated.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@_admin_required
def users_toggle(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('You cannot deactivate yourself.', 'danger')
        return redirect(url_for('admin.users'))
    user.is_active = not user.is_active
    db.session.commit()
    state = 'activated' if user.is_active else 'deactivated'
    flash(f'User "{user.username}" {state}.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@_admin_required
def users_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('You cannot delete yourself.', 'danger')
        return redirect(url_for('admin.users'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{username}" deleted.', 'success')
    return redirect(url_for('admin.users'))


@bp.route('/run-migrations', methods=['GET'])
@_admin_required
def run_migrations():
    """One-time migration: ai_* columns on damage_reports + release/sync columns on vehicles. Remove after running."""
    from sqlalchemy import text
    results = []
    migrations = [
        # Claude damage analysis columns
        "ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS ai_severity VARCHAR(20)",
        "ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS ai_repair_cost_low FLOAT",
        "ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS ai_repair_cost_high FLOAT",
        "ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS ai_total_loss BOOLEAN DEFAULT FALSE",
        "ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS ai_analysis TEXT",
        "ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS ai_analyzed_at TIMESTAMP",
        # Release tracking + Base44 sync
        "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS possible_release BOOLEAN DEFAULT FALSE",
        "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS towbook_seen BOOLEAN DEFAULT FALSE",
        "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS base44_id VARCHAR(100)",
        # Impound-type correction — superseded letters kept as historical records
        "ALTER TABLE certified_letters ADD COLUMN IF NOT EXISTS superseded BOOLEAN DEFAULT FALSE",
    ]
    try:
        with db.engine.connect() as conn:
            for sql in migrations:
                conn.execute(text(sql))
                results.append({'sql': sql, 'status': 'ok'})
            conn.commit()
        return jsonify({'ok': True, 'migrations': results})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc), 'completed': results}), 500


@bp.route('/users/list-json')
@_admin_required
def users_list_json():
    users = User.query.filter_by(is_active=True).order_by(User.display_name).all()
    return jsonify([{
        'id': u.id,
        'username': u.username,
        'display_name': u.display_name or u.username,
        'role': u.role,
    } for u in users])


# ── Police Department Rate Table ────────────────────────────────────────────
# Source of truth for POLICE-impound letter fee amounts (Vehicle.effective_tow_rate
# / effective_storage_rate look this up via Vehicle.police_department_id).

@bp.route('/departments')
@_tim_only_required
def departments():
    depts = PoliceDepartment.query.order_by(PoliceDepartment.name).all()
    return render_template('admin/departments.html', departments=depts)


@bp.route('/departments/new', methods=['POST'])
@_tim_only_required
def departments_new():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Department name is required.', 'danger')
        return redirect(url_for('admin.departments'))

    def _num(field):
        raw = request.form.get(field, '').strip()
        return float(raw) if raw else None

    dept = PoliceDepartment(
        name=name,
        tow_rate=_num('tow_rate'),
        storage_rate=_num('storage_rate'),
        admin_fee=_num('admin_fee'),
        active=True,
    )
    db.session.add(dept)
    db.session.commit()
    flash(f'Department "{name}" added.', 'success')
    return redirect(url_for('admin.departments'))


@bp.route('/departments/<int:dept_id>/edit', methods=['POST'])
@_tim_only_required
def departments_edit(dept_id):
    dept = db.get_or_404(PoliceDepartment, dept_id)

    def _num(field):
        raw = request.form.get(field, '').strip()
        return float(raw) if raw else None

    dept.name = request.form.get('name', dept.name).strip() or dept.name
    dept.tow_rate = _num('tow_rate')
    dept.storage_rate = _num('storage_rate')
    dept.admin_fee = _num('admin_fee')
    db.session.commit()
    flash(f'"{dept.name}" updated.', 'success')
    return redirect(url_for('admin.departments'))


@bp.route('/departments/<int:dept_id>/toggle', methods=['POST'])
@_tim_only_required
def departments_toggle(dept_id):
    dept = db.get_or_404(PoliceDepartment, dept_id)
    dept.active = not dept.active
    db.session.commit()
    state = 'activated' if dept.active else 'deactivated'
    flash(f'"{dept.name}" {state}.', 'success')
    return redirect(url_for('admin.departments'))


# ─────────────────────────────────────────────────────────────────────────
# Find Trucks — VIN-based reclassification of PPI vehicles.
#
# Every PPI vehicle predates the weight-class system, so they all default to
# 'light' ($22/day) and heavier trucks are under-billed on storage. This scans
# each active PPI VIN through the free NHTSA decoder, auto-confirms the ~90% that
# really are light, and surfaces only the trucks it flags as medium/heavy for Tim
# to confirm. The apply step writes BOTH vehicle_class and daily_storage_rate,
# because effective_storage_rate returns the stored rate whenever it is set — so
# changing the class alone would not change the bill.
# ─────────────────────────────────────────────────────────────────────────

_KNOWN_CLASS_RATES = set(Vehicle.PPI_STORAGE_RATE_BY_CLASS.values())  # {22.0, 37.0, 82.0}


def _active_ppi_query():
    return (Vehicle.query
            .filter(Vehicle.status == 'ACTIVE')
            .filter(Vehicle.impound_type == 'PPI')
            .filter(Vehicle.possible_release.isnot(True)))


@bp.route('/reclassify')
@_tim_only_required
def reclassify():
    vehicles = _active_ppi_query().order_by(Vehicle.stock_number).all()
    rows = [{
        'id': v.id,
        'stock': v.stock_number or '',
        'desc': v.display_name,
        'vin': (v.vin or '').strip().upper(),
        'current_class': (v.vehicle_class or 'light').lower(),
    } for v in vehicles]
    return render_template(
        'admin/reclassify.html',
        rows=rows,
        class_rates=Vehicle.PPI_STORAGE_RATE_BY_CLASS,
    )


@bp.route('/reclassify/scan', methods=['POST'])
@_tim_only_required
def reclassify_scan():
    """Decode one client-sent batch of {id, vin} items. Stateless — the browser
    slices the fleet into chunks and calls this repeatedly, so no single request
    is long (keeps memory flat / dodges worker timeouts)."""
    payload = request.get_json(silent=True) or {}
    items = payload.get('items') or []
    # Preserve id -> vin, decode the distinct VINs once, map results back per id.
    vins = [(it.get('vin') or '').strip().upper() for it in items]
    decoded = vin_decode.decode_vins(vins)
    results = []
    for it in items:
        vin = (it.get('vin') or '').strip().upper()
        d = decoded.get(vin) or vin_decode._blank_result(vin, 'No/short VIN — decode skipped')
        results.append({
            'id': it.get('id'),
            'vin': vin,
            'detected': d['detected'],
            'reason': d['reason'],
            'make': d['make'], 'model': d['model'], 'year': d['year'],
            'body_class': d['body_class'], 'gvwr': d['gvwr'],
        })
    return jsonify({'results': results})


@bp.route('/reclassify/apply', methods=['POST'])
@_tim_only_required
def reclassify_apply():
    """Apply the reclassifications Tim confirmed. Writes vehicle_class AND the
    class-default daily_storage_rate (unless a custom rate was hand-typed, which
    is preserved and reported back)."""
    payload = request.get_json(silent=True) or {}
    changes = payload.get('changes') or []
    updated = 0
    rate_kept = []      # class changed but a custom $ rate was left alone
    skipped = 0
    errors = []
    now = datetime.utcnow()

    for ch in changes:
        try:
            vid = int(ch.get('id'))
        except (TypeError, ValueError):
            errors.append(f'Bad vehicle id: {ch.get("id")!r}')
            continue
        new_class = (ch.get('vehicle_class') or '').strip().lower()
        if new_class not in Vehicle.VEHICLE_CLASSES:
            errors.append(f'#{vid}: invalid class {new_class!r}')
            continue
        v = db.session.get(Vehicle, vid)
        if v is None:
            errors.append(f'Vehicle {vid} not found')
            continue
        if v.status != 'ACTIVE' or v.impound_type != 'PPI' or v.possible_release:
            skipped += 1
            continue
        old_class = (v.vehicle_class or 'light').lower()
        if old_class == new_class:
            skipped += 1
            continue

        old_rate = float(v.daily_storage_rate) if v.daily_storage_rate is not None else None
        target_rate = Vehicle.ppi_storage_rate_for_class(new_class)
        v.vehicle_class = new_class

        # Rate rule: only move the stored rate when it is blank or itself a known
        # class default — never clobber a hand-typed custom rate.
        if old_rate is None or old_rate in _KNOWN_CLASS_RATES:
            v.daily_storage_rate = target_rate
            rate_note = (f'storage ${old_rate:.2f}→${target_rate:.2f}'
                         if old_rate is not None else f'storage set ${target_rate:.2f}')
        else:
            rate_kept.append({'id': vid, 'stock': v.stock_number, 'rate': old_rate})
            rate_note = f'custom storage ${old_rate:.2f} kept'

        v.updated_at = now
        who = current_user.display_name or current_user.username
        db.session.add(VehicleNote(
            vehicle_id=v.id,
            body=f'Reclassified {old_class}→{new_class} ({rate_note}) via VIN scan by {who}.',
            author=who,
            created_at=now,
        ))
        updated += 1

    if updated:
        db.session.commit()
        msg = f'Reclassified {updated} vehicle(s).'
        if rate_kept:
            msg += f' {len(rate_kept)} kept a custom rate — check those tickets.'
        flash(msg, 'success')

    return jsonify({
        'updated': updated,
        'rate_kept': rate_kept,
        'skipped': skipped,
        'errors': errors,
        'redirect': url_for('admin.reclassify'),
    })


def _is_staging():
    return os.environ.get('IS_STAGING', 'false').strip().lower() == 'true'


@bp.route('/training-reset')
@_tim_only_required
def training_reset():
    """One-click reset of the TRAIN-01..10 training baseline back to its
    original 10-chapter story — staging only, so a stray click can never
    touch anything on production."""
    if not _is_staging():
        flash('Training reset is only available on staging.', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('admin/training_reset.html')


@bp.route('/training-reset/run', methods=['POST'])
@_tim_only_required
def training_reset_run():
    if not _is_staging():
        flash('Training reset is only available on staging.', 'danger')
        return redirect(url_for('dashboard'))
    import seed_training_baseline
    try:
        seed_training_baseline.run()
        flash('Training data reset — TRAIN-01 through TRAIN-10 are back to their original story.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Reset failed: {exc}', 'danger')
    return redirect(url_for('admin.training_reset'))
