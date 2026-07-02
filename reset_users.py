"""
Reset the 9 canonical staff/demo account passwords to their documented
defaults, creating any that don't exist yet. Run this in the Render Shell
after every deploy.

Uses app.STAFF_USER_DEFAULTS as the single source of truth, so this always
matches what seed_default_users() creates on boot — no separate list to
drift out of sync.

    [RENDER SHELL] python3 reset_users.py
"""
from app import app, STAFF_USER_DEFAULTS
from models import db, User

with app.app_context():
    for username, password, role, display in STAFF_USER_DEFAULTS:
        user = User.query.filter_by(username=username).first()
        if user:
            user.set_password(password)
            user.role = role
            user.display_name = display
            print(f'reset: {username}')
        else:
            user = User(username=username, role=role, display_name=display)
            user.set_password(password)
            db.session.add(user)
            print(f'created: {username}')
    db.session.commit()
    print('Done.')
