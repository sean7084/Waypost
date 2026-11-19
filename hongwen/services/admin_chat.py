"""Staff-console chat glue for the Hongwen admin pages (PR5).

The customer API's chat helpers (hongwen.services.chat) assume a ``HongwenProfile``
exists: ``customer_id(user)`` dereferences ``user.hongwen_profile`` and the
identification flow links a company to that profile. Staff accounts have no
profile, so the admin chatbot cannot reuse ``handle_chat`` /
``prepare_stream_turn`` verbatim. This module mirrors those flows with the
profile-dependent branches removed:

* no customer identification - a staff member is already known to the system;
* knowledge-base and triage scoping fall out naturally: the orchestrator and
  triage engine resolve ``getattr(user, 'hongwen_profile', None)`` and treat a
  missing profile as NULL scoping, i.e. the *global* KB and global playbooks;
* moderation still runs, but msgSecCheck v2 requires a WeChat openid and staff
  never have one, so it degrades to SKIPPED (see utils.moderation.check_text).

Everything else - session minting, persistence, triage turns, the RAG
preparation and the SSE generators - is the shared PR3/PR4 service code, so the
admin console streams exactly the frames the mini program receives.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from django.utils.translation import gettext_lazy as _

from hongwen.models import ChatSession, Message
from hongwen.services.chat import (
    BOOTSTRAP_SESSION_PREFIX,
    ChatSessionNotFound,
    StreamTurn,
    _check_regenerate_message_id,
    ensure_chat_session,
    extract_user_input_text,
    list_chat_session_summaries,
    moderate_user_input,
    persist_assistant_message,
    persist_user_message,
    resolve_regenerate_messages,
    resolve_user_chat_session,
    run_triage_turn,
    serialize_chat_session_state,
)
from hongwen.services.llm.orchestrator import ReplyPreparation, prepare_reply_generation


def admin_greeting() -> str:
    """Staff-console greeting (the customer brand copy asks for a company)."""
    return str(_(
        'Hongwen staff console - every question answered, living water from the source. '
        'What would you like to test today?'
    ))


def create_admin_session(user) -> ChatSession:
    """Mint a console session with the staff greeting as its first message."""
    session_id = BOOTSTRAP_SESSION_PREFIX + uuid.uuid4().hex[:12]
    session = ChatSession.objects.create(user=user, session_id=session_id)
    Message.objects.create(
        user=user, session_id=session_id, role='assistant', type='text',
        content=json.dumps({'text': admin_greeting(), 'meta': {'staff_console': True}}, ensure_ascii=False),
    )
    return session


def load_admin_chat_state(user, *, session_id: Optional[str] = None, force_new: bool = False) -> Dict[str, Any]:
    """Admin variant of chat.load_chat_bootstrap_state (no profile dereference).

    The greeting always comes from the persisted first message, so
    ``serialize_chat_session_state`` never falls back to the customer
    ``bootstrap_greeting`` (which would raise for a profile-less staff user).
    """
    normalized = str(session_id or '').strip()
    restored = False

    if force_new:
        session = create_admin_session(user)
    elif normalized:
        session = resolve_user_chat_session(user, normalized)
        if session is None:
            raise ChatSessionNotFound(_('Chat session not found'))
        restored = True
    else:
        session = ChatSession.objects.filter(user=user).order_by('-created_at', '-id').first()
        if session is None:
            session = create_admin_session(user)
        else:
            restored = True

    state = serialize_chat_session_state(user, session, restored=restored)
    state['session_summaries'] = list_chat_session_summaries(
        user, current_session_id=str(state.get('session_id') or ''),
    )
    return state


def prepare_admin_stream_turn(user, payload: Dict[str, Any]) -> StreamTurn:
    """Admin variant of chat.prepare_stream_turn: triage or RAG, never identification.

    Input moderation still answers with a raised ChatInputBlocked *before* the
    StreamingHttpResponse exists, so a violation is a plain JSON 400 rather
    than an SSE error frame (parity with the customer stream endpoint).
    """
    session_id = ensure_chat_session(user, payload.get('session_id'))
    user_input_text = extract_user_input_text(payload)
    moderate_user_input(user, user_input_text)
    new_message = persist_user_message(user, payload, session_id)

    triage_turn = run_triage_turn(
        user, session_id=session_id, payload=payload, user_input_text=user_input_text,
    )
    if triage_turn is not None:
        persist_assistant_message(
            user, session_id=session_id, reply_text=triage_turn.reply_text, meta=triage_turn.meta,
        )
        result = {'reply': triage_turn.reply_text, 'session_id': session_id, 'meta': triage_turn.meta}
        return StreamTurn(
            session_id=session_id, user_message=new_message, fixed_result=result,
            fixed_start_meta={'triage': triage_turn.meta.get('triage', {})},
        )

    preparation = prepare_reply_generation(user=user, new_message=new_message, session_id=session_id)
    return StreamTurn(session_id=session_id, user_message=new_message, preparation=preparation)


def prepare_admin_regenerate_stream(user, *, session_id: str, message_id: Any = None) -> ReplyPreparation:
    """Admin variant of chat.prepare_regenerate_stream (staff are always identified)."""
    session = resolve_user_chat_session(user, session_id)
    if session is None:
        raise ChatSessionNotFound(_('Chat session not found'))

    latest, previous = resolve_regenerate_messages(user, session_id=session_id)
    _check_regenerate_message_id(message_id, latest)
    latest.delete()
    return prepare_reply_generation(user=user, new_message=previous, session_id=session_id)