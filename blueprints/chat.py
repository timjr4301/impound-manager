import os
import json
import logging
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from models import db, User, ChatThread, ChatMessage, ChatThreadMember, PushSubscription

logger = logging.getLogger(__name__)

bp = Blueprint('chat', __name__, url_prefix='/chat')


# ---------------------------------------------------------------------------
# Socket.IO event registration
# ---------------------------------------------------------------------------

def register_socket_events(socketio):
    from flask_socketio import join_room

    @socketio.on('connect', namespace='/chat')
    def on_connect():
        if not current_user.is_authenticated:
            return False
        memberships = ChatThreadMember.query.filter_by(user_id=current_user.id).all()
        for m in memberships:
            join_room(f'thread_{m.thread_id}')

    @socketio.on('join_thread', namespace='/chat')
    def on_join_thread(data):
        if not current_user.is_authenticated:
            return
        thread_id = data.get('thread_id')
        if not thread_id:
            return
        member = ChatThreadMember.query.filter_by(
            thread_id=thread_id, user_id=current_user.id
        ).first()
        if member:
            join_room(f'thread_{thread_id}')

    @socketio.on('send_message', namespace='/chat')
    def on_send_message(data):
        if not current_user.is_authenticated:
            return

        thread_id = data.get('thread_id')
        body = (data.get('body') or '').strip()

        if not thread_id or not body:
            socketio.emit('message_error', {'error': 'Message or thread missing.'},
                          room=request.sid, namespace='/chat')
            return

        # Verify the sender is a member of this thread
        member = ChatThreadMember.query.filter_by(
            thread_id=thread_id, user_id=current_user.id
        ).first()
        if not member:
            socketio.emit('message_error', {'error': 'You are not a member of this thread.'},
                          room=request.sid, namespace='/chat')
            return

        # Persist the user's message
        msg = ChatMessage(
            thread_id=thread_id,
            user_id=current_user.id,
            username=current_user.display_name or current_user.username,
            body=body,
            is_wally=False,
            created_at=datetime.utcnow(),
        )
        db.session.add(msg)
        db.session.commit()

        # Broadcast to thread room
        socketio.emit('new_message', {
            'id': msg.id,
            'thread_id': thread_id,
            'user_id': msg.user_id,
            'username': msg.username,
            'body': msg.body,
            'is_wally': False,
            'created_at': msg.created_at.isoformat(),
        }, room=f'thread_{thread_id}', namespace='/chat')

        # Trigger Wally when mentioned
        if '@wally' in body.lower():
            socketio.emit('wally_typing', {'thread_id': thread_id},
                          room=f'thread_{thread_id}', namespace='/chat')
            _call_wally(socketio, thread_id)


# ---------------------------------------------------------------------------
# Wally AI helper
# ---------------------------------------------------------------------------

def _wally_snapshot():
    """Live pipeline numbers injected into Wally's system prompt so he answers
    from real Impound Manager data instead of generic towing advice."""
    from datetime import date
    from models import Vehicle, CertifiedLetter
    today = date.today()

    active = Vehicle.query.filter_by(status='ACTIVE').count()
    ghosts = (Vehicle.query
              .filter(Vehicle.status == 'ACTIVE')
              .filter(Vehicle.possible_release == True)
              .count())
    pending = (
        CertifiedLetter.query
        .join(Vehicle)
        .filter(Vehicle.status == 'ACTIVE')
        .filter(Vehicle.possible_release.isnot(True))
        .filter(CertifiedLetter.sent_date.is_(None))
        .filter(CertifiedLetter.superseded.isnot(True))
        .all()
    )
    overdue = sorted((l for l in pending if l.due_date and l.due_date < today),
                     key=lambda l: l.due_date)
    due_today = [l for l in pending if l.due_date == today]

    lines = [
        f'{active} ACTIVE vehicles on the lot; {ghosts} of them are flagged '
        f'Possible Release (letters blocked until someone verifies them).',
        f'{len(overdue)} letter(s) OVERDUE and unsent; {len(due_today)} due today.',
    ]
    for l in overdue[:5]:
        v = l.vehicle
        lines.append(f'- OVERDUE: {v.display_name} (stock {v.stock_number or v.id}) — '
                     f'{l.label} was due {l.due_date.strftime("%m/%d/%Y")}.')
    if len(overdue) > 5:
        lines.append(f'- …and {len(overdue) - 5} more (full list on the Letters page).')
    return '\n'.join(lines)


def _wally_vehicle_briefs(text_body):
    """Look up any vehicles mentioned by stock #, plate, or VIN and summarize
    their pipeline state for the prompt. Returns [] when nothing matches."""
    import re
    from models import Vehicle
    tokens = {t.upper() for t in re.findall(r'[A-Za-z0-9-]{4,20}', text_body or '')}
    if not tokens:
        return []
    matches = (
        Vehicle.query
        .filter(db.or_(
            db.func.upper(Vehicle.stock_number).in_(tokens),
            db.func.upper(Vehicle.plate).in_(tokens),
            db.func.upper(Vehicle.vin).in_(tokens),
        ))
        .limit(3)
        .all()
    )

    briefs = []
    for v in matches:
        parts = [
            f'{v.display_name} — stock {v.stock_number or v.id}, plate {v.plate or "—"}, '
            f'{v.impound_type} impound, status {v.status}, impounded '
            f'{v.impound_date.strftime("%m/%d/%Y")} ({v.days_in_storage} days on lot).'
        ]
        if v.possible_release:
            parts.append('FLAGGED POSSIBLE RELEASE — must be verified before any letter goes out.')
        if v.letter_round > 1:
            parts.append(f'Letter process is on Round {v.letter_round} (restarted after Returned to Sender).')
        for name, l in (('Letter 1', v.letter1), ('Letter 2', v.letter2)):
            if name == 'Letter 2' and v.impound_type != 'PPI':
                continue
            if l is None:
                parts.append(f'{name}: not created yet.')
            elif l.sent_date:
                s = f'{name}: sent {l.sent_date.strftime("%m/%d/%Y")}'
                if l.delivery_confirmed_date:
                    s += f', delivered {l.delivery_confirmed_date.strftime("%m/%d/%Y")}'
                elif l.return_to_sender:
                    s += ', RETURNED TO SENDER'
                parts.append(s + '.')
            else:
                parts.append(f'{name}: NOT sent, due {l.due_date.strftime("%m/%d/%Y")}.')
        next_action = v.next_action_label
        if next_action:
            parts.append(f'Next action: {next_action}')
        briefs.append(' '.join(parts))
    return briefs


def _wally_system_prompt(latest_user_text):
    """Base persona + compliance rules + live data. Any failure building the
    live sections degrades to the plain persona — Wally must never go silent
    because a lookup broke."""
    from models import (PPI_LETTER1_DAYS, PPI_LETTER2_DAYS, POLICE_LETTER1_DAYS)
    base = (
        'You are Wally, the AI assistant built INTO Impound Manager, the internal web app '
        'Broad & James Towing (Columbus, OH) uses to run its impound lot. You help office '
        'staff (Heather, Tina, Brady, Jim, Tim) and drivers with impound procedures, the '
        'abandoned-vehicle letter pipeline, and daily operations. Be concise and helpful. '
        'You are shown live data from the app below — answer from it with specifics '
        '(stock numbers, dates, counts) and point people at the right page in Impound '
        'Manager (Dashboard, Letters page, Status Audit, the vehicle\'s detail page). '
        'NEVER tell anyone to "check your impound management system or inventory records" '
        '— you are reading that system\'s data right now. If the data below doesn\'t '
        'answer the question, say what page in the app will.\n\n'
        f'House letter rules: PPI Letter 1 within {PPI_LETTER1_DAYS} days of impound; '
        f'POLICE Notification within {POLICE_LETTER1_DAYS} days (ORC 4513.61); Letter 2 '
        f'{PPI_LETTER2_DAYS} days after Letter 1 is SENT; title eligibility runs 60 days '
        'from the impound date. A vehicle flagged Possible Release gets NO letters until '
        'verified (Still On Lot / Confirm Released).'
    )
    try:
        base += '\n\nLive pipeline right now:\n' + _wally_snapshot()
    except Exception as exc:
        logger.warning('Wally snapshot failed: %s', exc)
    try:
        briefs = _wally_vehicle_briefs(latest_user_text)
        if briefs:
            base += '\n\nVehicles mentioned in this conversation:\n' + '\n'.join(f'- {b}' for b in briefs)
    except Exception as exc:
        logger.warning('Wally vehicle lookup failed: %s', exc)
    return base


def _call_wally(socketio, thread_id):
    """Call Claude to generate a Wally response and broadcast it."""
    try:
        import anthropic

        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            logger.warning('ANTHROPIC_API_KEY not set — Wally unavailable')
            return

        # Fetch recent messages for context
        recent = (
            ChatMessage.query
            .filter_by(thread_id=thread_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(10)
            .all()
        )
        recent = list(reversed(recent))

        # Build alternating user/assistant conversation for the API
        raw = []
        for m in recent:
            role = 'assistant' if m.is_wally else 'user'
            content = m.body if m.is_wally else f'{m.username}: {m.body}'
            raw.append((role, content))

        # Merge consecutive same-role entries
        grouped = []
        for role, content in raw:
            if grouped and grouped[-1]['role'] == role:
                grouped[-1]['content'] += '\n' + content
            else:
                grouped.append({'role': role, 'content': content})

        # API requires starting with 'user'
        if grouped and grouped[0]['role'] == 'assistant':
            grouped = grouped[1:]

        conversation = grouped if grouped else [{'role': 'user', 'content': '@Wally hello'}]

        # Live-data system prompt, keyed off what was actually asked. Model is
        # Sonnet per the house routing rule (Opus is for vision/photo tasks
        # only — chat is text/logic).
        latest_user_text = ' '.join(m.body for m in recent[-3:] if not m.is_wally)
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=768,
            system=_wally_system_prompt(latest_user_text),
            messages=conversation,
        )

        wally_body = response.content[0].text

        # Persist Wally's response
        wally_msg = ChatMessage(
            thread_id=thread_id,
            user_id=None,
            username='Wally',
            body=wally_body,
            is_wally=True,
            created_at=datetime.utcnow(),
        )
        db.session.add(wally_msg)
        db.session.commit()

        # Broadcast Wally's response
        socketio.emit('new_message', {
            'id': wally_msg.id,
            'thread_id': thread_id,
            'user_id': None,
            'username': 'Wally',
            'body': wally_body,
            'is_wally': True,
            'created_at': wally_msg.created_at.isoformat(),
        }, room=f'thread_{thread_id}', namespace='/chat')

        # Send push notification to other thread members
        _send_push_to_thread(thread_id, 'Wally', wally_body[:100])

    except Exception as e:
        logger.error('Wally error: %s', e)
    finally:
        socketio.emit('wally_done', {'thread_id': thread_id},
                      room=f'thread_{thread_id}', namespace='/chat')


def _send_push_to_thread(thread_id, title, body, exclude_user_id=None):
    """Send Web Push notifications to all thread members."""
    vapid_private_key = os.environ.get('VAPID_PRIVATE_KEY')
    if not vapid_private_key:
        return

    try:
        from pywebpush import webpush, WebPushException

        vapid_claims = {
            'sub': f"mailto:{os.environ.get('VAPID_CONTACT_EMAIL', 'dispatch@broadandjames.com')}"
        }

        members = ChatThreadMember.query.filter_by(thread_id=thread_id).all()
        user_ids = [m.user_id for m in members if m.user_id != exclude_user_id]
        if not user_ids:
            return

        subs = PushSubscription.query.filter(
            PushSubscription.user_id.in_(user_ids)
        ).all()

        payload = json.dumps({'title': title, 'body': body, 'thread_id': thread_id})

        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        'endpoint': sub.endpoint,
                        'keys': {'p256dh': sub.p256dh, 'auth': sub.auth_key},
                    },
                    data=payload,
                    vapid_private_key=vapid_private_key,
                    vapid_claims=vapid_claims,
                )
            except WebPushException as exc:
                logger.warning('Push failed for subscription %s: %s', sub.id, exc)

    except ImportError:
        logger.debug('pywebpush not installed — skipping push notifications')
    except Exception as exc:
        logger.error('Push notification error: %s', exc)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@bp.route('/')
@login_required
def index():
    threads = (
        ChatThread.query
        .join(ChatThreadMember, ChatThread.id == ChatThreadMember.thread_id)
        .filter(ChatThreadMember.user_id == current_user.id)
        .order_by(ChatThread.created_at.desc())
        .all()
    )
    return render_template('chat/index.html', threads=threads)


@bp.route('/thread/<int:thread_id>')
@login_required
def thread_messages(thread_id):
    # 404 if the user is not a member
    ChatThreadMember.query.filter_by(
        thread_id=thread_id, user_id=current_user.id
    ).first_or_404()

    messages = (
        ChatMessage.query
        .filter_by(thread_id=thread_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    return jsonify([{
        'id': m.id,
        'user_id': m.user_id,
        'username': m.username,
        'body': m.body,
        'is_wally': m.is_wally,
        'created_at': m.created_at.isoformat() if m.created_at else None,
    } for m in messages])


@bp.route('/thread/new', methods=['POST'])
@login_required
def new_thread():
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    user_ids = data.get('user_ids', [])

    if not user_ids:
        return jsonify({'error': 'At least one user is required'}), 400

    all_user_ids = list({current_user.id} | {int(uid) for uid in user_ids})

    if not title:
        other_users = User.query.filter(
            User.id.in_(all_user_ids),
            User.id != current_user.id,
        ).all()
        title = ', '.join(u.display_name or u.username for u in other_users) or 'Group Chat'

    thread = ChatThread(
        title=title,
        is_group=(len(all_user_ids) > 2),
        created_at=datetime.utcnow(),
        created_by_id=current_user.id,
    )
    db.session.add(thread)
    db.session.flush()

    for uid in all_user_ids:
        db.session.add(ChatThreadMember(
            thread_id=thread.id,
            user_id=uid,
            joined_at=datetime.utcnow(),
        ))

    db.session.commit()
    return jsonify({'id': thread.id, 'title': thread.title})


@bp.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    data = request.get_json() or {}
    endpoint = data.get('endpoint')
    p256dh = data.get('p256dh')
    auth = data.get('auth')

    if not endpoint:
        return jsonify({'error': 'endpoint required'}), 400

    sub = PushSubscription.query.filter_by(
        user_id=current_user.id, endpoint=endpoint
    ).first()

    if sub:
        sub.p256dh = p256dh
        sub.auth_key = auth
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth_key=auth,
            created_at=datetime.utcnow(),
        )
        db.session.add(sub)

    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/vapid-public-key')
def vapid_public_key():
    return jsonify({'key': os.environ.get('VAPID_PUBLIC_KEY', '')})


@bp.route('/wally-alert', methods=['POST'])
def wally_alert():
    """Internal endpoint for automated Wally alerts (e.g. from the scheduler)."""
    secret = request.headers.get('X-Internal-Secret', '')
    expected = os.environ.get('INTERNAL_SECRET', 'wally-internal')
    if secret != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    thread_id = data.get('thread_id')
    message = (data.get('message') or '').strip()

    if not thread_id or not message:
        return jsonify({'error': 'thread_id and message required'}), 400

    msg = ChatMessage(
        thread_id=thread_id,
        user_id=None,
        username='Wally',
        body=message,
        is_wally=True,
        created_at=datetime.utcnow(),
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({'id': msg.id})


@bp.route('/users')
@login_required
def users():
    """Return active users (excluding self) for the New Chat modal."""
    all_users = User.query.filter_by(is_active=True).all()
    return jsonify([{
        'id': u.id,
        'display_name': u.display_name or u.username,
        'username': u.username,
        'role': u.role,
    } for u in all_users if u.id != current_user.id])
