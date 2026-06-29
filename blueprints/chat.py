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
            return

        # Verify the sender is a member of this thread
        member = ChatThreadMember.query.filter_by(
            thread_id=thread_id, user_id=current_user.id
        ).first()
        if not member:
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

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-opus-4-8',
            max_tokens=512,
            system=(
                'You are Wally, the AI assistant for Broad & James Towing in Columbus, OH. '
                'You help towing dispatchers, office staff, and drivers with questions about '
                'impound procedures, motor club accounts, and daily operations. '
                'Be concise and helpful.'
            ),
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
