"""Guided-triage decision-graph engine on the Django ORM.

Rewrite of helpdesk/backend/app/triage/engine.py. A cheap cosine pre-filter
shortlists a playbook, an LLM gatekeeper confirms troubleshooting intent and
detects the device, then deterministic steps (quick replies + branch map) walk
the flow. ``start_or_advance`` returns a TriageTurn when the message belongs to a
guided flow, or None so the caller falls back to normal RAG chat.

Playbook content is authored Simplified Chinese (not UI catalogue strings); the
JSONField columns hold dicts/lists directly (no JSON-string round-trip).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from django.db import connection
from django.db.models import Q

from hongwen.models import TriageAsset, TriagePlaybook, TriageSession, TriageStep
from hongwen.services.kb.vector_store import _to_vector_literal, cosine_similarity, embed_query
from hongwen.services.llm.model_selector import select_primary_text_ref
from hongwen.services.llm.provider_router import chat_completion
from hongwen.services.triage.assets import asset_exists, resolve_asset_url

# Deliberately lenient cosine pre-filter: the LLM gatekeeper decides precision.
MATCH_THRESHOLD = 0.35

_TERMINAL_KINDS = {'end', 'escalate'}
_YES_TOKENS = {'yes', 'y', '是', '对', '对的', '正确', '没错', '确认', '能', '可以', '正常', '已连接', '已解决', 'ok'}
_NO_TOKENS = {'no', 'n', '否', '不', '不是', '没有', '没', '不行', '不能', '打不开', '还不行', '未解决', '找不到'}


@dataclass
class TriageTurn:
    """One deterministic step of a guided triage flow."""

    reply_text: str
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    quick_replies: List[Dict[str, str]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) else default
    except Exception:
        return default
    return value if value is not None else default


def _customer_id(user) -> Optional[int]:
    profile = getattr(user, 'hongwen_profile', None)
    return getattr(profile, 'customer_id', None)


def _strip_code_fence(content: str) -> str:
    cleaned = str(content or '').strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[a-zA-Z0-9_-]*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```$', '', cleaned)
    return cleaned.strip()


def _chat_json(messages: List[Dict[str, Any]], *, temperature: float = 0.0) -> Dict[str, Any]:
    """Small helper LLM call that must return a single JSON object."""
    try:
        response = chat_completion(
            model=select_primary_text_ref().qualified_name, messages=messages, temperature=temperature,
        )
        content = response.get('choices', [{}])[0].get('message', {}).get('content', '')
        parsed = json.loads(_strip_code_fence(content))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _active_playbooks(*, customer_id: Optional[int]) -> List[TriagePlaybook]:
    query = TriagePlaybook.objects.filter(is_active=True)
    if customer_id is not None:
        query = query.filter(Q(customer_id=customer_id) | Q(customer_id__isnull=True))
    else:
        query = query.filter(customer_id__isnull=True)
    return list(query.order_by('id'))


def _match_postgres(vec_literal: str, customer_id: Optional[int], threshold: float) -> Optional[Tuple[TriagePlaybook, float]]:
    table = TriagePlaybook._meta.db_table
    if customer_id is not None:
        customer_filter = '(p.customer_id = %s OR p.customer_id IS NULL)'
        params: List[Any] = [vec_literal, customer_id, vec_literal]
    else:
        customer_filter = 'p.customer_id IS NULL'
        params = [vec_literal, vec_literal]
    sql = f"""
        SELECT p.id, (1 - ((p.embedding::text)::vector <=> CAST(%s AS vector))) AS score
        FROM {table} p
        WHERE {customer_filter}
          AND p.is_active
          AND p.embedding IS NOT NULL
        ORDER BY (p.embedding::text)::vector <=> CAST(%s AS vector)
        LIMIT 1
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    if not row:
        return None
    score = float(row[1] or 0.0)
    if score < threshold:
        return None
    playbook = TriagePlaybook.objects.filter(id=int(row[0])).first()
    return (playbook, score) if playbook else None


def _match_python(query_vec: List[float], customer_id: Optional[int], threshold: float) -> Optional[Tuple[TriagePlaybook, float]]:
    best: Optional[Tuple[TriagePlaybook, float]] = None
    for playbook in _active_playbooks(customer_id=customer_id):
        if not playbook.embedding:
            continue
        score = cosine_similarity(query_vec, playbook.embedding)
        if best is None or score > best[1]:
            best = (playbook, score)
    if best is None or best[1] < threshold:
        return None
    return best


def match_playbook(*, query: str, customer_id: Optional[int] = None, threshold: float = MATCH_THRESHOLD) -> Optional[Tuple[TriagePlaybook, float]]:
    """Vector pre-filter: closest active playbook above threshold, else None."""
    if not (query or '').strip():
        return None
    if not _active_playbooks(customer_id=customer_id):
        return None
    try:
        query_vec = embed_query(query)
    except Exception:
        return None

    if connection.vendor == 'postgresql':
        return _match_postgres(_to_vector_literal(query_vec), customer_id, threshold)
    return _match_python(query_vec, customer_id, threshold)


def classify_intake(*, query: str, customer_id: Optional[int] = None) -> Dict[str, str]:
    """LLM gatekeeper: confirm a troubleshooting request and detect the device."""
    playbooks = _active_playbooks(customer_id=customer_id)
    if not playbooks:
        return {'playbook_slug': '', 'device': ''}

    options = '\n'.join(f'- {p.slug}: {p.title_zh} / {p.symptom_summary_zh}' for p in playbooks)
    system = (
        '你是帮助台分诊助手。判断用户这句话是否属于下列某个排障场景，并识别设备类型。'
        '只输出一个 JSON 对象，不要输出多余文本或代码块。'
    )
    user_prompt = (
        f'可选排障场景：\n{options}\n\n'
        '输出格式：{"playbook_slug": "<场景slug或空字符串>", '
        '"device": "<ios|windows|printer|mac|android|other|空字符串>"}\n'
        '规则：若只是普通咨询、寒暄或不属于任何排障场景，playbook_slug 输出空字符串。\n'
        f'用户这句话：{query}'
    )
    result = _chat_json([{'role': 'system', 'content': system}, {'role': 'user', 'content': user_prompt}])
    slug = str(result.get('playbook_slug') or '').strip()
    device = str(result.get('device') or '').strip().lower()
    valid_slugs = {p.slug for p in playbooks}
    if slug not in valid_slugs:
        slug = ''
    if device not in {'ios', 'windows', 'printer', 'mac', 'android', 'other'}:
        device = ''
    return {'playbook_slug': slug, 'device': device}


def _resolve_playbook(*, slug: str, customer_id: Optional[int]) -> Optional[TriagePlaybook]:
    if not slug:
        return None
    for playbook in _active_playbooks(customer_id=customer_id):
        if playbook.slug == slug:
            return playbook
    return None


def _get_step(*, playbook_id: int, step_index: int) -> Optional[TriageStep]:
    return TriageStep.objects.filter(playbook_id=playbook_id, step_index=step_index).first()


def _local_answer_match(step: TriageStep, user_text: str) -> Optional[str]:
    """Deterministic answer match from quick-reply values/labels or yes-no synonyms."""
    normalized = str(user_text or '').strip().lower()
    if not normalized:
        return None

    quick_replies: List[Dict[str, Any]] = _loads(step.quick_replies_json, [])
    for reply in quick_replies:
        value = str(reply.get('value') or '').strip().lower()
        label = str(reply.get('label_zh') or '').strip().lower()
        if value and normalized == value:
            return str(reply.get('value'))
        if label and (normalized == label or label in normalized):
            return str(reply.get('value'))

    branch_map: Dict[str, Any] = _loads(step.branch_map_json, {})
    tokens = set(re.split(r'[\s,，。.、!！?？]+', normalized))
    if tokens & _YES_TOKENS and 'yes' in branch_map:
        return 'yes'
    if tokens & _NO_TOKENS and 'no' in branch_map:
        return 'no'
    return None


def extract_answer(*, step: TriageStep, user_text: str) -> str:
    """Reduce a free-text answer to one branch key; falls back to 'default'."""
    local = _local_answer_match(step, user_text)
    if local:
        return local

    quick_replies: List[Dict[str, Any]] = _loads(step.quick_replies_json, [])
    branch_map: Dict[str, Any] = _loads(step.branch_map_json, {})
    allowed = [str(reply.get('value')) for reply in quick_replies if reply.get('value')]
    allowed += [k for k in branch_map.keys() if k not in {'default'} and k not in allowed]
    if not allowed:
        return 'default'

    options = '\n'.join(
        f"- {reply.get('value')}: {reply.get('label_zh')}" for reply in quick_replies if reply.get('value')
    ) or '\n'.join(f'- {value}' for value in allowed)
    system = '你负责把用户回复归类到给定选项之一。只输出一个 JSON 对象，不要多余文本。'
    user_prompt = (
        f'可选答案：\n{options}\n\n'
        '输出格式：{"answer": "<上面某个value，或 default>"}\n'
        f'用户回复：{user_text}'
    )
    result = _chat_json([{'role': 'system', 'content': system}, {'role': 'user', 'content': user_prompt}])
    answer = str(result.get('answer') or '').strip()
    return answer if answer in allowed else 'default'


def _build_attachments(step: TriageStep) -> List[Dict[str, Any]]:
    asset: Optional[TriageAsset] = step.asset
    if not asset or not asset_exists(asset.relative_path):
        return []
    return [{
        'type': 'image',
        'path': asset.relative_path,
        'url': resolve_asset_url(asset.relative_path),
        'caption': asset.caption_zh or '',
    }]


def _build_reply_text(step: TriageStep) -> str:
    parts = [str(step.prompt_zh or '').strip()]
    expected = str(step.expected_result_zh or '').strip()
    if expected:
        parts.append(f'（正常情况下：{expected}）')
    return '\n'.join(part for part in parts if part)


def render_step(*, playbook: TriagePlaybook, step: TriageStep, status: str) -> TriageTurn:
    attachments = _build_attachments(step)
    quick_replies_raw: List[Dict[str, Any]] = _loads(step.quick_replies_json, [])
    quick_replies = [
        {'label_zh': str(reply.get('label_zh') or reply.get('value') or ''), 'value': str(reply.get('value') or '')}
        for reply in quick_replies_raw if reply.get('value')
    ]
    meta: Dict[str, Any] = {
        'used_knowledge_base': False,
        'knowledge_sources': [],
        'attachments': attachments,
        'image_paths': [item['path'] for item in attachments],
        'quick_replies': quick_replies,
        'triage': {
            'playbook_slug': playbook.slug,
            'playbook_title': playbook.title_zh,
            'step_index': step.step_index,
            'kind': step.kind,
            'status': status,
        },
    }
    return TriageTurn(reply_text=_build_reply_text(step), attachments=attachments, quick_replies=quick_replies, meta=meta)


def _get_session(*, user, session_id: str) -> Optional[TriageSession]:
    return TriageSession.objects.filter(user_id=user.id, session_id=session_id).first()


def _start_session(*, user, session_id: str, playbook: TriagePlaybook, device: str) -> Optional[TriageTurn]:
    first_step = _get_step(playbook_id=playbook.id, step_index=0)
    if not first_step:
        return None

    answers: Dict[str, Any] = {}
    if device:
        answers['device_hint'] = device

    existing = _get_session(user=user, session_id=session_id)
    if existing:
        existing.playbook = playbook
        existing.current_step_index = 0
        existing.answers_json = answers
        existing.status = 'in_progress'
        existing.save()
    else:
        TriageSession.objects.create(
            user=user, session_id=session_id, playbook=playbook,
            current_step_index=0, answers_json=answers, status='in_progress',
        )
    return render_step(playbook=playbook, step=first_step, status='in_progress')


def _advance(*, session: TriageSession, user_text: str, quick_value: Optional[str]) -> Optional[TriageTurn]:
    playbook = TriagePlaybook.objects.filter(id=session.playbook_id).first()
    if not playbook:
        session.status = 'abandoned'
        session.save()
        return None

    step = _get_step(playbook_id=playbook.id, step_index=session.current_step_index)
    if not step:
        session.status = 'abandoned'
        session.save()
        return None

    answer_value = str(quick_value).strip() if quick_value else extract_answer(step=step, user_text=user_text)
    answers: Dict[str, Any] = _loads(session.answers_json, {})
    answers[str(step.step_index)] = answer_value
    if step.kind == 'intake_device':
        answers['device'] = answer_value

    branch_map: Dict[str, Any] = _loads(step.branch_map_json, {})
    next_index = branch_map.get(answer_value, branch_map.get('default', step.step_index + 1))
    try:
        next_index = int(next_index)
    except (TypeError, ValueError):
        next_index = step.step_index + 1

    next_step = _get_step(playbook_id=playbook.id, step_index=next_index)
    if not next_step:
        # Defensive: a branch pointed past the last step. Close the flow cleanly.
        session.current_step_index = step.step_index
        session.answers_json = answers
        session.status = 'resolved'
        session.save()
        return TriageTurn(
            reply_text='已经帮你记录本次排查结果。如果仍未恢复，请联系 IT/人工同事进一步处理。',
            meta={
                'used_knowledge_base': False, 'knowledge_sources': [], 'attachments': [],
                'image_paths': [], 'quick_replies': [],
                'triage': {
                    'playbook_slug': playbook.slug, 'playbook_title': playbook.title_zh,
                    'step_index': step.step_index, 'kind': 'end', 'status': 'resolved',
                },
            },
        )

    status = 'resolved' if next_step.kind in _TERMINAL_KINDS else 'in_progress'
    session.current_step_index = next_step.step_index
    session.answers_json = answers
    session.status = status
    session.save()
    return render_step(playbook=playbook, step=next_step, status=status)


def start_or_advance(*, user, session_id: str, user_text: str, quick_value: Optional[str] = None) -> Optional[TriageTurn]:
    """Route a chat turn through triage; None means fall back to normal RAG chat."""
    session = _get_session(user=user, session_id=session_id)
    # A tapped quick-reply always belongs to the current flow (even after a terminal
    # step, e.g. the restart chip); free text only continues an in-progress flow.
    if session and (session.status == 'in_progress' or quick_value):
        return _advance(session=session, user_text=user_text, quick_value=quick_value)

    customer_id = _customer_id(user)
    candidate = match_playbook(query=user_text, customer_id=customer_id)
    if not candidate:
        return None

    classification = classify_intake(query=user_text, customer_id=customer_id)
    slug = classification.get('playbook_slug') or ''
    device = classification.get('device') or ''

    playbook = _resolve_playbook(slug=slug, customer_id=customer_id)
    if not playbook:
        return None

    return _start_session(user=user, session_id=session_id, playbook=playbook, device=device)