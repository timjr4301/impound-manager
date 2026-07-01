from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=False)
            if not current_user.is_authenticated:
                return "Login failed - user not authenticated after login_user()", 500
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            # Route each role to their own dashboard
            if user.role == 'tina':
                return redirect(url_for('tina.dashboard'))
            if user.role == 'heather':
                return redirect(url_for('heather.dashboard'))
            if user.role == 'dispatcher':
                return redirect(url_for('dispatch_board'))
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
