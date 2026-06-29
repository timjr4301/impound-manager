"""
Admin blueprint — user management (Tim/Jim only).
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, User
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
