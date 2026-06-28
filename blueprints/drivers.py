"""
Driver management — Friday SMS system, suggestion intake, timecard exceptions.
"""
import os
import json
from datetime import date, datetime, timedelta
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify, current_app)
from flask_login import login_required, current_user
from models import db, Driver, DriverSuggestion, DriverSMS, TimecardException

bp = Blueprint('drivers', __name__, url_prefix='/drivers')

FRIDAY_QUESTIONS = [
    "What would make your job easier this week?",
    "What do you need from Tim or dispatch to do your job better?",
    "Anything to report about your truck or equipment?",
]

SUGGESTION_CATEGORIES = {
    'truck': 'equipment',
    'equipment': 'equipment',
    'van': 'equipment',
    'trailer': 'equipment',
    'tool': 'equipment',
    'dispatch': 'process',
    'route': 'process',
    'schedule': 'process',
    'time': 'process',
    'inform': 'information',
    'know': 'information',
    'told': 'information',
    'update': 'information',
    'radio': 'information',
    'training': 'information',
}


def _tim_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_see_all:
            flash('Access restricted to Tim.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


def _categorize_suggestion(text):
    text_lower = text.lower()
    for keyword, cat in SUGGESTION_CATEGORIES.items():
        if keyword in text_lower:
            return cat
    return 'process'


def _send_sms(to_number, body, driver_id=None, week_of=None, sms_type='general'):
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    from_number = os.environ.get('TWILIO_FROM_NUMBER')

    if not all([account_sid, auth_token, from_number]):
        return None, 'Twilio not configured'

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=body,
            from_=from_number,
            to=to_number,
        )
        if driver_id:
            log = DriverSMS(
                driver_id=driver_id,
                direction='outbound',
                body=body,
                twilio_sid=message.sid,
                status=message.status,
                sent_at=datetime.utcnow(),
                week_of=week_of,
                sms_type=sms_type,
            )
            db.session.add(log)
            db.session.commit()
        return message.sid, None
    except Exception as exc:
        return None, str(exc)


@bp.route('/')
@login_required
def dashboard():
    if not current_user.can_see_all:
        flash('Access restricted.', 'danger')
        return redirect(url_for('dashboard'))
    drivers = Driver.query.filter_by(is_active=True).order_by(Driver.name).all()
    return render_template('drivers/dashboard.html', drivers=drivers)


@bp.route('/new', methods=['GET', 'POST'])
@_tim_required
def new_driver():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        if not name:
            flash('Name is required.', 'danger')
            return redirect(url_for('drivers.new_driver'))
        driver = Driver(
            name=name,
            phone=phone or None,
            towbook_driver_id=request.form.get('towbook_driver_id', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
        )
        db.session.add(driver)
        db.session.commit()
        flash(f'Driver {name} added.', 'success')
        return redirect(url_for('drivers.dashboard'))
    return render_template('drivers/new_driver.html')


@bp.route('/send-friday-sms', methods=['GET', 'POST'])
@_tim_required
def send_friday_sms():
    """
    Compose and send Friday summary SMS to all active drivers with SMS opt-in.
    Includes: weekly hours summary, bonus call list, 3-question prompt.
    """
    if request.method == 'POST':
        week_str = request.form.get('week_of', date.today().isoformat())
        week_of = date.fromisoformat(week_str)

        drivers = Driver.query.filter_by(is_active=True, sms_opt_in=True).all()
        results = []

        for driver in drivers:
            if not driver.phone:
                results.append({'driver': driver.name, 'status': 'skipped (no phone)'})
                continue

            hours = request.form.get(f'hours_{driver.id}', '').strip()
            bonus_calls = request.form.get(f'bonus_{driver.id}', '').strip()

            # Build message
            lines = [f'Hi {driver.name.split()[0]}! B&J Towing — Week of {week_of.strftime("%m/%d")}']
            if hours:
                lines.append(f'Hours: {hours}')
            if bonus_calls:
                lines.append(f'Bonus Calls: {bonus_calls}')
            lines.append('\nThree quick questions:')
            for i, q in enumerate(FRIDAY_QUESTIONS, 1):
                lines.append(f'{i}. {q}')
            lines.append('\nReply 1, 2, or 3 followed by your answer.')

            body = '\n'.join(lines)
            sid, err = _send_sms(driver.phone, body, driver.id, week_of, 'weekly_summary')

            if err:
                results.append({'driver': driver.name, 'status': f'error: {err}'})
            else:
                results.append({'driver': driver.name, 'status': 'sent', 'sid': sid})

        flash(f'SMS sent to {sum(1 for r in results if r["status"] == "sent")} drivers.', 'success')
        return render_template('drivers/sms_results.html', results=results, week_of=week_of)

    week_of = date.today()
    # Find the most recent Friday
    days_since_friday = (week_of.weekday() - 4) % 7
    week_of = week_of - timedelta(days=days_since_friday)

    drivers = Driver.query.filter_by(is_active=True, sms_opt_in=True).all()
    return render_template('drivers/send_friday_sms.html',
        drivers=drivers,
        week_of=week_of,
        questions=FRIDAY_QUESTIONS,
    )


@bp.route('/sms-webhook', methods=['POST'])
def sms_webhook():
    """Twilio webhook for inbound driver replies."""
    from_number = request.form.get('From', '').strip()
    body = request.form.get('Body', '').strip()

    driver = Driver.query.filter_by(phone=from_number).first()
    if not driver:
        # Try to match with + prefix or without
        alt = from_number.lstrip('+')
        driver = Driver.query.filter(
            db.or_(
                Driver.phone.like(f'%{alt[-10:]}%')
            )
        ).first()

    if not driver:
        return '', 204

    # Log inbound SMS
    log = DriverSMS(
        driver_id=driver.id,
        direction='inbound',
        body=body,
        sent_at=datetime.utcnow(),
        sms_type='reply',
    )
    db.session.add(log)

    # Parse question number from reply (e.g., "1 more training please")
    week_of = date.today() - timedelta(days=date.today().weekday())

    question_num = None
    suggestion_body = body

    if body and body[0].isdigit() and len(body) > 1 and body[1] in (' ', '.', ')'):
        question_num = int(body[0])
        suggestion_body = body[2:].strip()

    if suggestion_body:
        category = _categorize_suggestion(suggestion_body)

        suggestion = DriverSuggestion(
            driver_id=driver.id,
            week_of=week_of,
            category=category,
            body=suggestion_body,
            question_number=question_num,
        )
        db.session.add(suggestion)

        # Send acknowledgment
        ack = f'Got it, {driver.name.split()[0]}! We\'ll review your feedback. Thanks for keeping us sharp.'
        _send_sms(driver.phone, ack, driver.id, week_of, 'acknowledgment')

    db.session.commit()
    return '', 204


@bp.route('/suggestions')
@_tim_required
def suggestions():
    all_suggestions = (
        DriverSuggestion.query
        .order_by(DriverSuggestion.created_at.desc())
        .all()
    )
    by_category = {'equipment': [], 'information': [], 'process': []}
    for s in all_suggestions:
        cat = s.category or 'process'
        if cat in by_category:
            by_category[cat].append(s)
        else:
            by_category['process'].append(s)

    return render_template('drivers/suggestions.html',
        suggestions=all_suggestions,
        by_category=by_category,
    )


@bp.route('/suggestions/<int:suggestion_id>/action', methods=['POST'])
@_tim_required
def suggestion_action(suggestion_id):
    s = db.get_or_404(DriverSuggestion, suggestion_id)
    s.action_taken = request.form.get('action', '').strip() or None
    s.acknowledged = True
    s.acknowledged_date = datetime.utcnow()
    db.session.commit()
    flash('Action recorded.', 'success')
    return redirect(url_for('drivers.suggestions'))


@bp.route('/timecards')
@_tim_required
def timecards():
    exceptions = (
        TimecardException.query
        .filter_by(resolved=False)
        .order_by(TimecardException.exception_date.desc())
        .all()
    )
    return render_template('drivers/timecards.html', exceptions=exceptions)


@bp.route('/timecards/upload', methods=['POST'])
@_tim_required
def timecards_upload():
    """Parse uploaded timecard CSV and flag exceptions."""
    uploaded = request.files.get('csv_file')
    if not uploaded:
        flash('No file uploaded.', 'danger')
        return redirect(url_for('drivers.timecards'))

    import csv, io
    content = uploaded.stream.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(content))
    exceptions_found = 0

    def _norm_header(h):
        import re
        return re.sub(r'[^a-z0-9]', '', h.lower())

    rows = list(reader)
    headers = reader.fieldnames or []
    norm = {_norm_header(h): h for h in headers}

    def _get(row, *candidates):
        import re
        for c in candidates:
            key = re.sub(r'[^a-z0-9]', '', c.lower())
            if key in norm:
                return row.get(norm[key], '').strip()
        return ''

    for row in rows:
        driver_name = _get(row, 'Driver', 'Employee', 'Name')
        clock_in = _get(row, 'Clock In', 'Punch In', 'Start', 'Time In')
        clock_out = _get(row, 'Clock Out', 'Punch Out', 'End', 'Time Out')
        shift_date_raw = _get(row, 'Date', 'Shift Date', 'Work Date')

        if not driver_name:
            continue

        shift_date = None
        if shift_date_raw:
            for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y', '%m/%d/%y'):
                try:
                    from datetime import datetime as dt
                    shift_date = dt.strptime(shift_date_raw, fmt).date()
                    break
                except ValueError:
                    continue

        # Flag missing punch-out
        if clock_in and not clock_out:
            exc = TimecardException(
                driver_name=driver_name,
                exception_date=shift_date,
                exception_type='missing_punch',
                description=f'Clock-in at {clock_in} with no clock-out recorded.',
                suggested_correction=f'Check with {driver_name} — likely forgot to clock out.',
            )
            db.session.add(exc)
            exceptions_found += 1

        # Flag very short shifts (< 1 hour)
        if clock_in and clock_out:
            try:
                from datetime import datetime as dt
                for fmt in ('%I:%M %p', '%H:%M', '%I:%M%p'):
                    try:
                        t_in = dt.strptime(clock_in.upper(), fmt)
                        t_out = dt.strptime(clock_out.upper(), fmt)
                        hours = (t_out - t_in).seconds / 3600
                        if hours < 1 and hours > 0:
                            exc = TimecardException(
                                driver_name=driver_name,
                                exception_date=shift_date,
                                exception_type='short_shift',
                                description=f'Shift only {hours:.1f} hours ({clock_in}–{clock_out}).',
                                suggested_correction='Verify this was a legitimate short shift or a punch error.',
                            )
                            db.session.add(exc)
                            exceptions_found += 1
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

    db.session.commit()
    flash(f'Timecard processed — {exceptions_found} exception(s) flagged.', 'success' if exceptions_found == 0 else 'warning')
    return redirect(url_for('drivers.timecards'))


@bp.route('/timecards/<int:exc_id>/approve', methods=['POST'])
@_tim_required
def timecard_approve(exc_id):
    exc = db.get_or_404(TimecardException, exc_id)
    exc.approved_by = current_user.display_name or 'Tim'
    exc.approved_at = datetime.utcnow()
    exc.resolved = True
    db.session.commit()
    flash('Exception approved and resolved.', 'success')
    return redirect(url_for('drivers.timecards'))
