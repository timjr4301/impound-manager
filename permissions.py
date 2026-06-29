from functools import wraps
from flask import jsonify
from flask_login import login_required, current_user

ROLE_PERMISSIONS = {
    'jim':        {'all_access': True,  'payroll': True,  'override': True},
    'tim':        {'all_access': True,  'payroll': True,  'override': False},
    'heather':    {'all_access': False, 'payroll': False, 'override': False},
    'tina':       {'all_access': False, 'payroll': False, 'override': False},
    'lawrence':   {'all_access': True,  'payroll': False, 'override': False},
    'brady':      {'all_access': True,  'payroll': False, 'override': False},
    'dispatcher': {'all_access': False, 'payroll': False, 'override': False},
    'lori':       {'all_access': False, 'payroll': False, 'override': False},
}


def has_permission(user, permission):
    perms = ROLE_PERMISSIONS.get(user.role, {})
    if perms.get('all_access') and permission != 'payroll':
        return True
    return perms.get(permission, False)


def require_permission(permission):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not has_permission(current_user, permission):
                return jsonify({'error': 'Permission denied.'}), 403
            return f(*args, **kwargs)
        return login_required(decorated)
    return decorator
