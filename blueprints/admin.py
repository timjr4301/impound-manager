"""
Admin blueprint — user management (Tim/Jim only).
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, User, PoliceDepartment
from permissions import has_permission

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
    """Stricter than _admin_required (which also allows jim/lawrence/brady) —
    the police department rate table is the source of truth for letter fee
    amounts, so only Tim can edit it."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != 'tim':
            flash('This page is Tim-only.', 'danger')
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
