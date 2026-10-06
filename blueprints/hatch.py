"""
Hatch chat endpoint — the Letters & Titles assistant on Heather's and Tina's
dashboards. Logic + prompts live in hatch_engine.py.

Conversation history lives in the browser's sessionStorage and rides along
with each request; nothing is written to the database. Every message rebuilds
the live vehicle context from PostgreSQL.
"""
import logging
import os

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import hatch_engine

logger = logging.getLogger(__name__)

bp = Blueprint('hatch', __name__, url_prefix='/hatch')

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_TURNS = 20
MAX_HISTORY_CHARS = 6000


def _clean_history(raw):
    """Keep only well-formed, alternating user/assistant turns that end on an
    assistant reply, so the new user message can be appended cleanly."""
    turns = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        role, content = m.get('role'), m.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str) or not content.strip():
            continue
        content = content[:MAX_HISTORY_CHARS]
        if turns and turns[-1]['role'] == role:
            turns[-1]['content'] += '\n' + content
        else:
            turns.append({'role': role, 'content': content})
    turns = turns[-MAX_HISTORY_TURNS:]
    while turns and turns[0]['role'] != 'user':
        turns.pop(0)
    while turns and turns[-1]['role'] != 'assistant':
        turns.pop()
    return turns


@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    dashboard = data.get('dashboard')
    if dashboard == 'tina':
        allowed = current_user.can_see_tina_dashboard
    elif dashboard == 'heather':
        allowed = current_user.can_see_heather_dashboard
    else:
        return jsonify(error='Unknown dashboard.'), 400
    if not allowed:
        return jsonify(error='Access restricted.'), 403

    message = (data.get('message') or '').strip()
    if not message:
        return jsonify(error='Type a question first.'), 400
    message = message[:MAX_MESSAGE_CHARS]

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning('ANTHROPIC_API_KEY not set — Hatch unavailable')
        return jsonify(error='Hatch is offline (no API key configured).'), 503

    mode = hatch_engine.resolve_mode(current_user, dashboard)
    try:
        system = hatch_engine.system_prompt(mode, message)
    except Exception as exc:
        logger.exception('Hatch context build failed: %s', exc)
        from models import db
        db.session.rollback()
        return jsonify(error="Hatch couldn't read the vehicle data just now. Try again."), 500

    messages = _clean_history(data.get('history')) + [{'role': 'user', 'content': message}]
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=hatch_engine.HATCH_MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        reply = ''.join(b.text for b in response.content if getattr(b, 'type', '') == 'text').strip()
    except Exception as exc:
        logger.error('Hatch API error: %s', exc)
        return jsonify(error='Hatch had trouble answering. Try again in a moment.'), 502

    return jsonify(reply=reply or '(no answer)', mode=mode)
