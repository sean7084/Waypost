"""Chat turn and conversation-state helpers for the Wenqu customer API (PR4).

Port of the view-layer helpers in helpdesk/backend/app/main.py (bootstrap state,
frontend history expansion, session summaries, message persistence, moderation
wrappers, customer identification and the synchronous chat/regenerate turns)
onto the Django ORM. The client-facing contracts are preserved verbatim:

* bootstrap mini payload  {greeting, session_id, restored, sessions[],
  external_user_id, messages[]}                    (main.py:852)
* frontend history        mixed messages expand to several rows with
  ``<id>:image:N`` / ``<id>:audio`` / ``<id>:text`` suffixes; audio content is
  {playable, filePath, duration, transcription}    (main.py:462-563)
* chat / regenerate       {reply, session_id, meta} (main.py:2932 / :2979)

Only authentication (WenquJWTAuthentication + WenquProfile) and the brand
greeting copy differ from the helpdesk. The LLM/KB/triage work itself is done
by the PR3 services; this module is the glue the DRF views and the SSE
generator share, so it never imports DRF.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from accounts.models import WeChatIdentity
from wenqu.models import ChatSession, Customer, Message, UploadFile, WenquProfile
from wenqu.services.llm.model_selector import select_primary_omni_ref
from wenqu.services.llm.orchestrator import (
    ReplyPreparation,
    apply_profile_updates,
    generate_reply_and_update_profile,
    prepare_reply_generation,
)
from wenqu.services.llm.provider_router import chat_completion
from wenqu.services.triage import start_or_advance
from wenqu.services.utils import moderation
from wenqu.services.utils.file_store import build_attachment_caption, build_protected_upload_url

logger = logging.getLogger(__name__)

# Session-id prefixes are part of the helpdesk contract: bootstrap conversations
# are ``pc_sess_<uuid12>`` (main.py:818) while a chat turn without a session id
# minted one as ``sess_<uuid12>`` (main.py:2511).
BOOTSTRAP_SESSION_PREFIX = 'pc_sess_'
CHAT_SESSION_PREFIX = 'sess_'

SESSION_SUMMARY_LIMIT = 20
SESSION_TITLE_MAX_LENGTH = 255


class ChatError(Exception):
    """Base class for domain errors the views translate into HTTP responses."""


class ChatInputBlocked(ChatError):
    """User text failed content moderation (HTTP 400, plain JSON even on SSE)."""


class ChatSessionNotFound(ChatError):
    """The session id does not belong to this customer (HTTP 404)."""


class RegenerateUnavailable(ChatError):
    """Regenerate preconditions are not met (HTTP 422)."""


class RegenerateConflict(ChatError):
    """The latest assistant reply changed since the client rendered it (HTTP 409)."""


# --- identity helpers -------------------------------------------------------


def _profile(user) -> WenquProfile:
    return user.wenqu_profile


def customer_id(user) -> Optional[int]:
    return getattr(_profile(user), 'customer_id', None)


def _openid(user) -> Optional[str]:
    identity = (
        WeChatIdentity.objects.filter(user=user, appid=settings.WECHAT_WENQU_APPID)
        .values_list('openid', flat=True)
        .first()
    )
    return identity or None


def external_user_id(user) -> str:
    profile = _profile(user)
    return profile.external_id or (_openid(user) or '')


# --- greeting / sessions ----------------------------------------------------


def bootstrap_greeting(user) -> Tuple[str, bool]:
    """Return (greeting, identifying). Wenqu brand copy replaces the helpdesk's."""
    if customer_id(user) is not None:
        return str(_('问渠——有问必答，源头活水。今天有什么可以帮您的吗？')), False
    return str(_('问渠——有问必答，源头活水。请问您来自哪家公司？')), True


def create_bootstrap_session(user, *, prefix: str = BOOTSTRAP_SESSION_PREFIX) -> ChatSession:
    session_id = prefix + uuid.uuid4().hex[:12]
    greeting, identifying = bootstrap_greeting(user)
    session = ChatSession.objects.create(user=user, session_id=session_id)
    Message.objects.create(
        user=user, session_id=session_id, role='assistant', type='text',
        content=json.dumps({'text': greeting, 'meta': {'identifying': identifying}}, ensure_ascii=False),
    )
    return session


def ensure_chat_session(user, session_id: Optional[str]) -> str:
    """Resolve or mint the session for a chat turn (main.py:2510).

    Deliberate hardening over the helpdesk: the lookup is scoped to the calling
    user, so a session id owned by another customer can never be hijacked (the
    helpdesk queried by session_id alone). A foreign id answers 404.
    """
    resolved = str(session_id or '').strip() or (CHAT_SESSION_PREFIX + uuid.uuid4().hex[:12])
    session = ChatSession.objects.filter(user=user, session_id=resolved).first()
    if session is None:
        try:
            # A savepoint keeps the surrounding transaction usable when the id
            # turns out to be owned by another customer (unique constraint).
            with transaction.atomic():
                ChatSession.objects.create(user=user, session_id=resolved)
        except IntegrityError:
            raise ChatSessionNotFound(_('未找到该会话或当前用户无权访问。'))
    return resolved


def resolve_user_chat_session(user, session_id: str) -> Optional[ChatSession]:
    normalized = str(session_id or '').strip()
    if not normalized:
        return None
    return ChatSession.objects.filter(user=user, session_id=normalized).first()


def load_session_messages(user, session_id: str) -> List[Message]:
    return list(
        Message.objects.filter(user=user, session_id=session_id)
        .order_by('created_at', 'id')
    )


# --- message payload helpers (main.py:300-367, 2640) -------------------------


def _deserialize_message_content(message: Message) -> Any:
    try:
        return json.loads(str(message.content or '{}'))
    except Exception:
        return message.content


def _message_payload(message: Message) -> Any:
    parsed = _deserialize_message_content(message)
    if isinstance(parsed, dict) and 'content' in parsed:
        return parsed.get('content')
    return parsed


def _message_payload_text(payload: Any) -> str:
    if isinstance(payload, dict):
        if payload.get('text') is not None:
            return str(payload.get('text') or '').strip()
        category = str(payload.get('category') or '').strip()
        detail = str(payload.get('detail') or '').strip()
        if category and detail:
            return f'{category}: {detail}'
        if detail:
            return detail
    if isinstance(payload, str):
        return payload.strip()
    return ''


def extract_message_text(message: Message) -> str:
    try:
        parsed = json.loads(message.content or '{}')
    except Exception:
        parsed = message.content
    if isinstance(parsed, dict):
        nested = parsed.get('content')
        if isinstance(nested, dict) and nested.get('text') is not None:
            return str(nested.get('text') or '').strip()
        if parsed.get('text') is not None:
            return str(parsed.get('text') or '').strip()
    if isinstance(parsed, str):
        return parsed.strip()
    return ''


def _history_summary_text(message: Message) -> str:
    payload = _message_payload(message)
    normalized_type = str(message.type or 'text')

    if str(message.role or '') == 'assistant':
        return extract_message_text(message)
    if normalized_type in ('text', 'form'):
        return _message_payload_text(payload)
    if normalized_type == 'audio':
        return _message_payload_text(payload) or str(_('语音消息'))
    if normalized_type == 'image':
        return str(_('图片消息'))
    if normalized_type == 'mixed' and isinstance(payload, dict):
        parts: List[str] = []
        text = _message_payload_text(payload)
        if text:
            parts.append(text)
        image_count = len(payload.get('image_file_ids') or [])
        if image_count:
            parts.append(str(_('已附加 {count} 张图片')).format(count=image_count))
        video_count = len(payload.get('video_file_ids') or [])
        if video_count:
            parts.append(str(_('已附加 {count} 个视频')).format(count=video_count))
        if payload.get('audio_file_id'):
            parts.append(str(_('语音附件')))
        return '\n'.join(parts) if parts else str(_('附件消息'))
    return _message_payload_text(payload) or str(_('消息'))


# --- frontend history expansion (main.py:370-636) ----------------------------


def _upload_relative_path(upload: UploadFile) -> str:
    name = str(upload.file.name or '').strip()
    if name.startswith('wenqu/'):
        return name[len('wenqu/'):]
    return name


def _upload_public_url(upload: Optional[UploadFile]) -> str:
    if upload is None:
        return ''
    relative = _upload_relative_path(upload)
    return build_protected_upload_url(relative) if relative else ''


def _build_upload_lookup(user, messages: List[Message]) -> Dict[str, UploadFile]:
    file_ids: set[str] = set()
    for message in messages:
        payload = _message_payload(message)
        if not isinstance(payload, dict):
            continue
        for key in ('file_id', 'audio_file_id'):
            value = str(payload.get(key) or '').strip()
            if value:
                file_ids.add(value)
        for key in ('image_file_ids', 'video_file_ids'):
            for value in payload.get(key) or []:
                normalized = str(value or '').strip()
                if normalized:
                    file_ids.add(normalized)
    if not file_ids:
        return {}
    uploads = UploadFile.objects.filter(user=user, file_id__in=list(file_ids))
    return {str(upload.file_id): upload for upload in uploads}


def _frontend_audio_content(payload: Any, upload_lookup: Dict[str, UploadFile]) -> Dict[str, Any]:
    content = payload if isinstance(payload, dict) else {}
    audio_file_id = str(content.get('audio_file_id') or content.get('file_id') or '').strip()
    upload = upload_lookup.get(audio_file_id)
    audio_url = _upload_public_url(upload)

    result: Dict[str, Any] = {'playable': bool(audio_url)}
    if audio_url:
        result['filePath'] = audio_url
    duration_seconds = int(getattr(upload, 'duration_seconds', 0) or 0) if upload is not None else 0
    if duration_seconds > 0:
        result['duration'] = duration_seconds
    transcription = _message_payload_text(content)
    if transcription:
        result['transcription'] = transcription
    return result


def _frontend_image_message(*, message_id: str, role: str, upload: Optional[UploadFile]) -> Optional[Dict[str, Any]]:
    image_url = _upload_public_url(upload)
    if not image_url:
        return None
    return {
        'id': message_id,
        'role': role,
        'type': 'image',
        'content': {'filePath': image_url, 'previewPath': image_url},
    }


def _serialize_frontend_user_message(message: Message, *, upload_lookup: Dict[str, UploadFile]) -> List[Dict[str, Any]]:
    payload = _message_payload(message)
    message_id = str(message.id)
    normalized_type = str(message.type or 'text')

    if normalized_type == 'text':
        return [{'id': message_id, 'role': 'user', 'type': 'text', 'content': _message_payload_text(payload)}]

    if normalized_type == 'form':
        form_content = payload if isinstance(payload, dict) else {}
        return [{
            'id': message_id, 'role': 'user', 'type': 'form',
            'content': {
                'category': str(form_content.get('category') or ''),
                'detail': str(form_content.get('detail') or ''),
            },
        }]

    if normalized_type == 'audio':
        return [{
            'id': message_id, 'role': 'user', 'type': 'audio',
            'content': _frontend_audio_content(payload, upload_lookup),
        }]

    if normalized_type == 'image':
        image_payload = payload if isinstance(payload, dict) else {}
        upload = upload_lookup.get(str(image_payload.get('file_id') or '').strip())
        image_message = _frontend_image_message(message_id=message_id, role='user', upload=upload)
        if image_message:
            return [image_message]
        return [{'id': message_id, 'role': 'user', 'type': 'text', 'content': str(_('图片消息'))}]

    if normalized_type == 'mixed' and isinstance(payload, dict):
        items: List[Dict[str, Any]] = []
        for index, file_id in enumerate(payload.get('image_file_ids') or []):
            upload = upload_lookup.get(str(file_id or '').strip())
            image_message = _frontend_image_message(message_id=f'{message_id}:image:{index}', role='user', upload=upload)
            if image_message:
                items.append(image_message)
        if payload.get('audio_file_id'):
            items.append({
                'id': f'{message_id}:audio', 'role': 'user', 'type': 'audio',
                'content': _frontend_audio_content(payload, upload_lookup),
            })
        text = _message_payload_text(payload)
        if text:
            items.append({
                'id': f'{message_id}:text' if items else message_id,
                'role': 'user', 'type': 'text', 'content': text,
            })
        video_count = len(payload.get('video_file_ids') or [])
        if video_count:
            items.append({
                'id': f'{message_id}:video', 'role': 'user', 'type': 'text',
                'content': str(_('已附加 {count} 个视频')).format(count=video_count),
            })
        if items:
            return items

    return [{'id': message_id, 'role': 'user', 'type': 'text', 'content': _history_summary_text(message)}]


def _normalize_meta_attachments(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Derive captions for attachments persisted before the field existed (main.py:575)."""
    attachments = meta.get('attachments')
    if not isinstance(attachments, list):
        return meta
    normalized: List[Any] = []
    for item in attachments:
        if isinstance(item, dict) and not item.get('caption'):
            caption = build_attachment_caption(
                str(item.get('source') or ''), str(item.get('page_or_section') or ''),
            )
            if caption:
                item = {**item, 'caption': caption}
        normalized.append(item)
    return {**meta, 'attachments': normalized}


def serialize_page_chat_history(messages: List[Message]) -> List[Dict[str, Any]]:
    history: List[Dict[str, Any]] = []
    for message in messages:
        parsed = _deserialize_message_content(message)
        meta = parsed.get('meta', {}) if isinstance(parsed, dict) and isinstance(parsed.get('meta'), dict) else {}
        history.append({
            'id': str(message.id),
            'role': message.role,
            'text': _history_summary_text(message),
            'meta': _normalize_meta_attachments(meta),
        })
    return history


def serialize_frontend_chat_history(user, messages: List[Message]) -> List[Dict[str, Any]]:
    history: List[Dict[str, Any]] = []
    upload_lookup = _build_upload_lookup(user, messages)
    for message in messages:
        if str(message.role or '') == 'assistant':
            parsed = _deserialize_message_content(message)
            meta = parsed.get('meta', {}) if isinstance(parsed, dict) and isinstance(parsed.get('meta'), dict) else {}
            history.append({
                'id': str(message.id),
                'role': 'assistant',
                'type': 'text',
                'content': extract_message_text(message),
                'meta': _normalize_meta_attachments(meta),
            })
            continue
        history.extend(_serialize_frontend_user_message(message, upload_lookup=upload_lookup))
    return history


# --- session summaries (main.py:639-757) -------------------------------------


def _chat_session_summary_text(messages: List[Message], *, prefer_user: bool = True) -> str:
    ordered = messages if prefer_user else list(reversed(messages))
    if prefer_user:
        for message in ordered:
            if str(message.role or '') != 'user':
                continue
            text = _history_summary_text(message)
            if text:
                return text
    else:
        for message in ordered:
            text = _history_summary_text(message)
            if text:
                return text
    for message in messages:
        if str(message.role or '') != 'assistant':
            continue
        text = extract_message_text(message)
        if text:
            return text
    return ''


def _truncate_session_summary(value: str, *, max_length: int) -> str:
    normalized = ' '.join(str(value or '').split())
    if not normalized:
        return ''
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + '...'


def _isoformat(value: Any) -> str:
    return value.isoformat() if hasattr(value, 'isoformat') else ''


def serialize_chat_session_summary(session: ChatSession, *, messages: List[Message], current_session_id: str = '') -> Dict[str, Any]:
    session_id = str(session.session_id or '')
    created_at = _isoformat(session.created_at)
    updated_at = created_at
    if messages:
        updated_at = _isoformat(messages[-1].created_at) or created_at

    custom_title = _truncate_session_summary(str(session.title or ''), max_length=30)
    title_source = _chat_session_summary_text(messages, prefer_user=True)
    preview_source = _chat_session_summary_text(list(reversed(messages)), prefer_user=False)
    title = custom_title or _truncate_session_summary(title_source, max_length=30) or str(_('未命名对话'))
    preview = _truncate_session_summary(preview_source, max_length=52)

    return {
        'session_id': session_id,
        'title': title,
        'preview': preview,
        'created_at': created_at,
        'updated_at': updated_at,
        'message_count': len(messages),
        'is_current': bool(current_session_id and current_session_id == session_id),
    }


def list_chat_session_summaries(user, *, current_session_id: str = '', limit: int = SESSION_SUMMARY_LIMIT) -> List[Dict[str, Any]]:
    sessions = list(ChatSession.objects.filter(user=user).order_by('-created_at', '-id'))
    if not sessions:
        return []
    session_ids = [str(session.session_id or '') for session in sessions if str(session.session_id or '').strip()]
    messages = (
        list(Message.objects.filter(user=user, session_id__in=session_ids).order_by('created_at', 'id'))
        if session_ids else []
    )
    messages_by_session: Dict[str, List[Message]] = {}
    for message in messages:
        normalized = str(message.session_id or '')
        if normalized:
            messages_by_session.setdefault(normalized, []).append(message)

    summaries = [
        serialize_chat_session_summary(
            session,
            messages=messages_by_session.get(str(session.session_id or ''), []),
            current_session_id=current_session_id,
        )
        for session in sessions
    ]
    summaries.sort(
        key=lambda item: (
            str(item.get('updated_at') or ''),
            str(item.get('created_at') or ''),
            str(item.get('session_id') or ''),
        ),
        reverse=True,
    )
    return summaries[:limit]


# --- bootstrap state (main.py:772-867) ---------------------------------------


def serialize_chat_session_state(user, session: ChatSession, *, restored: bool) -> Dict[str, Any]:
    session_id = str(session.session_id or '')
    messages = load_session_messages(user, session_id)
    page_history = serialize_page_chat_history(messages)
    frontend_history = serialize_frontend_chat_history(user, messages)

    greeting = next(
        (
            item.get('content', '')
            for item in frontend_history
            if item.get('role') == 'assistant' and item.get('type') == 'text'
        ),
        '',
    )
    if not greeting:
        greeting, _identifying = bootstrap_greeting(user)

    latest_assistant_meta: Dict[str, Any] = {}
    for item in reversed(page_history):
        if item.get('role') == 'assistant' and item.get('meta'):
            latest_assistant_meta = dict(item.get('meta') or {})
            break

    return {
        'session_id': session_id,
        'greeting': greeting,
        'page_history': page_history,
        'frontend_history': frontend_history,
        'latest_assistant_meta': latest_assistant_meta,
        'restored': restored,
    }


def load_chat_bootstrap_state(user, *, session_id: Optional[str] = None, force_new: bool = False,
                              session_prefix: str = BOOTSTRAP_SESSION_PREFIX) -> Dict[str, Any]:
    normalized = str(session_id or '').strip()
    restored = False

    if force_new:
        session = create_bootstrap_session(user, prefix=session_prefix)
    elif normalized:
        session = resolve_user_chat_session(user, normalized)
        if session is None:
            raise ChatSessionNotFound(_('未找到该会话或当前用户无权访问。'))
        restored = True
    else:
        session = ChatSession.objects.filter(user=user).order_by('-created_at', '-id').first()
        if session is None:
            session = create_bootstrap_session(user, prefix=session_prefix)
        else:
            restored = True

    state = serialize_chat_session_state(user, session, restored=restored)
    state['session_summaries'] = list_chat_session_summaries(
        user, current_session_id=str(state.get('session_id') or ''),
    )
    return state


def chatbot_client_payload(*, client_type: str, user, bootstrap: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        'greeting': bootstrap['greeting'],
        'session_id': bootstrap['session_id'],
        'restored': bootstrap['restored'],
        'sessions': bootstrap.get('session_summaries', []),
    }
    if client_type == 'wechat-mini':
        payload['external_user_id'] = external_user_id(user)
        payload['messages'] = bootstrap['frontend_history']
        return payload
    payload['chat_history'] = bootstrap['page_history']
    payload['latest_assistant_meta'] = bootstrap['latest_assistant_meta']
    return payload


# --- persistence (main.py:2600-2637) -----------------------------------------


def persist_user_message(user, payload: Dict[str, Any], session_id: str) -> Message:
    return Message.objects.create(
        user=user, session_id=session_id, role='user', type=payload.get('type') or 'text',
        content=json.dumps(payload, ensure_ascii=False),
    )


def persist_assistant_message(user, *, session_id: str, reply_text: str, meta: Dict[str, Any]) -> Message:
    return Message.objects.create(
        user=user, session_id=session_id, role='assistant', type='text',
        content=json.dumps({'text': reply_text, 'meta': meta}, ensure_ascii=False),
    )


# --- moderation wrappers (main.py:2535-2560) ---------------------------------


def moderate_user_input(user, text: str) -> None:
    """Reject violating user text before it is stored or handed to the model."""
    if not text.strip():
        return
    suggest = moderation.check_text(text, _openid(user))
    if moderation.should_block(suggest):
        logger.warning('Blocked user input from user %s (verdict=%s)', user.id, suggest.value)
        raise ChatInputBlocked(_('您的消息包含不适宜内容，已停止处理，请修改后重试'))


def moderate_assistant_reply(user, reply_text: str, meta: Dict[str, Any]) -> str:
    """Swap a violating model reply for the substitute text before it is stored."""
    if not reply_text.strip():
        return reply_text
    suggest = moderation.check_text(reply_text, _openid(user))
    if moderation.should_block(suggest):
        logger.warning('Replaced assistant reply for user %s (verdict=%s)', user.id, suggest.value)
        meta['content_moderation'] = 'replaced'
        return str(_('该回答包含不适宜内容，已被拦截。请换个说法重新提问。'))
    return reply_text


# --- input extraction / triage (main.py:2525, 2563-2597) ----------------------


def extract_user_input_text(payload: Dict[str, Any]) -> str:
    content = payload.get('content')
    if isinstance(content, dict):
        return str(content.get('text') or '')
    return str(content)


def extract_quick_value(payload: Dict[str, Any]) -> Optional[str]:
    content = payload.get('content')
    if isinstance(content, dict):
        value = content.get('quick_value')
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def run_triage_turn(user, *, session_id: str, payload: Dict[str, Any], user_input_text: str):
    if not getattr(settings, 'WENQU_TRIAGE_ENABLED', True):
        return None
    return start_or_advance(
        user=user, session_id=session_id, user_text=user_input_text,
        quick_value=extract_quick_value(payload),
    )


# --- customer identification (main.py:2477-2507, 2752-2813) -------------------


def identify_customer_from_llm(user_input: str) -> Optional[int]:
    customers = list(Customer.objects.all())
    if not customers:
        return None
    customer_list = '\n'.join(f'ID: {c.id}, Name: {c.name}, Notes: {c.notes}' for c in customers)
    prompt = (
        "Given the user's input and a list of companies, identify which company the user belongs to.\n"
        'Respond ONLY with the numerical ID of the company. If no match is found, respond with "NONE".\n\n'
        f'User input: "{user_input}"\n\n'
        f'Companies:\n{customer_list}\n'
    )
    model = select_primary_omni_ref(multimodal=False).qualified_name
    try:
        response = chat_completion(
            model=model, messages=[{'role': 'user', 'content': prompt}], temperature=0,
        )
        content = response.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if content.isdigit():
            return int(content)
    except Exception as exc:
        logger.error('LLM customer identification failed: %s', exc)
    return None


_POSITIVE_WORDS = ['yes', 'yeah', 'yep', 'correct', '是的', '对', '正确', '没错', '确认']


def handle_customer_identification_flow(user, *, session_id: str, user_input_text: str) -> Dict[str, Any]:
    last_assistant = (
        Message.objects.filter(session_id=session_id, role='assistant')
        .order_by('-created_at', '-id')
        .first()
    )
    last_meta: Dict[str, Any] = {}
    if last_assistant is not None:
        try:
            last_meta = json.loads(last_assistant.content).get('meta', {}) or {}
        except Exception:
            last_meta = {}

    reply_text = ''
    new_meta: Dict[str, Any] = {}

    if last_assistant is None or not (last_meta.get('identifying') or last_meta.get('verifying_customer_id')):
        reply_text = str(_('您来自哪家公司？'))
        new_meta = {'identifying': True}
    elif last_meta.get('identifying'):
        matched_id = identify_customer_from_llm(user_input_text)
        matched = Customer.objects.filter(id=matched_id).first() if matched_id else None
        if matched is not None:
            reply_text = str(_('您是来自 {name} 吗？')).format(name=matched.name)
            new_meta = {'verifying_customer_id': matched.id}
        else:
            reply_text = str(_('抱歉，我无法识别您的公司。请拨打 4008383636 寻求帮助。'))
    elif last_meta.get('verifying_customer_id'):
        if any(word in user_input_text.lower() for word in _POSITIVE_WORDS):
            profile = _profile(user)
            profile.customer_id = last_meta['verifying_customer_id']
            profile.save(update_fields=['customer', 'updated_at'])
            reply_text = str(_('已成功关联您的账户。请问有什么我可以帮您的？'))
            new_meta = {'linked': True}
        else:
            reply_text = str(_('抱歉，请重新输入您的公司名称，或拨打 4008383636 寻求帮助。'))
            new_meta = {'identifying': True}

    persist_assistant_message(user, session_id=session_id, reply_text=reply_text, meta=new_meta)
    return {'reply': reply_text, 'session_id': session_id, 'meta': new_meta}


# --- synchronous turns (main.py:2819-2883, 2948-3019) -------------------------


def handle_chat(user, payload: Dict[str, Any]) -> Dict[str, Any]:
    session_id = ensure_chat_session(user, payload.get('session_id'))
    user_input_text = extract_user_input_text(payload)
    moderate_user_input(user, user_input_text)
    new_message = persist_user_message(user, payload, session_id)

    if customer_id(user) is None:
        return handle_customer_identification_flow(
            user, session_id=session_id, user_input_text=user_input_text,
        )

    triage_turn = run_triage_turn(
        user, session_id=session_id, payload=payload, user_input_text=user_input_text,
    )
    if triage_turn is not None:
        persist_assistant_message(
            user, session_id=session_id, reply_text=triage_turn.reply_text, meta=triage_turn.meta,
        )
        return {'reply': triage_turn.reply_text, 'session_id': session_id, 'meta': triage_turn.meta}

    reply_text, meta, profile_updates = generate_reply_and_update_profile(
        user=user, new_message=new_message, session_id=session_id,
    )
    meta = dict(meta)
    reply_text = moderate_assistant_reply(user, reply_text, meta)
    persist_assistant_message(user, session_id=session_id, reply_text=reply_text, meta=meta)
    apply_profile_updates(user, profile_updates)
    return {'reply': reply_text, 'session_id': session_id, 'meta': meta}


def resolve_regenerate_messages(user, *, session_id: str) -> Tuple[Message, Message]:
    latest = (
        Message.objects.filter(user=user, session_id=session_id)
        .order_by('-created_at', '-id')
        .first()
    )
    if latest is None:
        raise RegenerateUnavailable(_('No assistant reply is available to regenerate'))
    if latest.role != 'assistant' or latest.type != 'text':
        raise RegenerateUnavailable(_('Only the latest assistant text reply can be regenerated'))

    previous = (
        Message.objects.filter(user=user, session_id=session_id)
        .exclude(id=latest.id)
        .order_by('-created_at', '-id')
        .first()
    )
    if previous is None or previous.role != 'user':
        raise RegenerateUnavailable(_('No preceding user message is available for regeneration'))
    return latest, previous


def _check_regenerate_message_id(message_id: Any, latest: Message) -> None:
    normalized = str(message_id or '').strip()
    if normalized and normalized.isdigit() and normalized != str(latest.id):
        raise RegenerateConflict(_('The latest assistant reply has changed'))


def handle_regenerate(user, *, session_id: str, message_id: Any = None) -> Dict[str, Any]:
    session = resolve_user_chat_session(user, session_id)
    if session is None:
        raise ChatSessionNotFound(_('Chat session not found'))
    if customer_id(user) is None:
        raise RegenerateUnavailable(_('Regenerate is unavailable until customer identification is complete'))

    latest, previous = resolve_regenerate_messages(user, session_id=session_id)
    previous_reply_text = extract_message_text(latest)
    _check_regenerate_message_id(message_id, latest)

    latest.delete()
    reply_text, meta, profile_updates = generate_reply_and_update_profile(
        user=user, new_message=previous, session_id=session_id, regenerate=True,
    )
    if previous_reply_text and reply_text.strip() == previous_reply_text:
        reply_text, meta, profile_updates = generate_reply_and_update_profile(
            user=user, new_message=previous, session_id=session_id, regenerate=True,
        )

    meta = dict(meta)
    meta['regenerated'] = True
    reply_text = moderate_assistant_reply(user, reply_text, meta)
    persist_assistant_message(user, session_id=session_id, reply_text=reply_text, meta=meta)
    apply_profile_updates(user, profile_updates)
    return {'reply': reply_text, 'session_id': session_id, 'meta': meta}


# --- streaming turn preparation (main.py:3084-3178) ---------------------------


@dataclass
class StreamTurn:
    """Everything the SSE generator needs, decided before the response starts.

    ``fixed_result`` is set for the identification and triage branches, which
    deliver a deterministic reply as start/final/done with no deltas. Otherwise
    ``preparation`` carries the RAG pipeline state for token streaming.
    """

    session_id: str
    user_message: Message
    fixed_result: Optional[Dict[str, Any]] = None
    fixed_start_meta: Optional[Dict[str, Any]] = None
    preparation: Optional[ReplyPreparation] = None


def prepare_stream_turn(user, payload: Dict[str, Any]) -> StreamTurn:
    session_id = ensure_chat_session(user, payload.get('session_id'))
    user_input_text = extract_user_input_text(payload)
    # Raised before the StreamingResponse exists, so a violating input answers
    # with a plain JSON 400 instead of an SSE error frame (main.py:3096).
    moderate_user_input(user, user_input_text)
    new_message = persist_user_message(user, payload, session_id)

    if customer_id(user) is None:
        result = handle_customer_identification_flow(
            user, session_id=session_id, user_input_text=user_input_text,
        )
        return StreamTurn(session_id=session_id, user_message=new_message, fixed_result=result, fixed_start_meta={})

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


def prepare_regenerate_stream(user, *, session_id: str, message_id: Any = None) -> ReplyPreparation:
    session = resolve_user_chat_session(user, session_id)
    if session is None:
        raise ChatSessionNotFound(_('Chat session not found'))
    if customer_id(user) is None:
        raise RegenerateUnavailable(_('Regenerate is unavailable until customer identification is complete'))

    latest, previous = resolve_regenerate_messages(user, session_id=session_id)
    _check_regenerate_message_id(message_id, latest)
    latest.delete()
    return prepare_reply_generation(user=user, new_message=previous, session_id=session_id)


def stream_start_meta(preparation: ReplyPreparation, *, regenerated: bool = False) -> Dict[str, Any]:
    meta = {
        'used_knowledge_base': preparation.base_meta.get('used_knowledge_base', False),
        'knowledge_sources': preparation.base_meta.get('knowledge_sources', []),
        'model_primary': preparation.primary_model,
        'model_fallback': preparation.fallback_model,
    }
    if regenerated:
        meta['regenerated'] = True
    return meta