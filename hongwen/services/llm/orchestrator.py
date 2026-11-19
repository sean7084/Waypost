"""Chat reply orchestration: the core Hongwen RAG + multimodal pipeline.

Rewrite of helpdesk/backend/app/llm/orchestrator.py on the Django ORM. The chain
is preserved exactly:

  profile -> parse multimodal input -> ASR transcript -> embed + retrieve top 40
  -> hybrid rerank top 5 (Epson term weights, hardcoded debt) -> KB attachments /
  PDF page render -> 20-turn history -> guidance_payload as a SECOND user message
  -> primary/backup model selection.

Streaming does not update the learning profile; non-streaming asks the model for
a JSON object {assistant_reply, updated_weak_points, ...} and measures knowledge
provenance itself (the model cannot override it).

File adaptation: ``file_id`` resolves to an ``UploadFile`` and is materialized to
a real temp file through ``storage.open`` / ``.name`` (never ``.path``) so the
ffmpeg / vision encoders can subprocess; temp files are cleaned up afterwards.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from django.core.files.base import ContentFile

from hongwen.models import Message, UploadFile, UserProfile
from hongwen.services.kb.paths import resolve_stored_path
from hongwen.services.kb.vector_store import search_kb
from hongwen.services.llm.model_selector import (
    select_fallback_omni_ref,
    select_primary_asr_ref,
    select_primary_omni_ref,
)
from hongwen.services.llm.prompts import PromptMode, load_prompt_pack, render_helpdesk_system_prompt
from hongwen.services.llm.provider_router import (
    chat_completion_with_fallback,
    stream_chat_completion_with_fallback,
    transcribe_audio,
)
from hongwen.services.storage import private_storage
from hongwen.services.utils.audio_utils import encode_audio_as_base64
from hongwen.services.utils.chunking import extract_image_paths
from hongwen.services.utils.file_store import (
    build_attachment_caption,
    build_protected_upload_url,
    materialize_upload_to_tempfile,
    normalize_upload_relative_path,
)
from hongwen.services.utils.image_utils import encode_image_as_data_url
from hongwen.services.utils.video_utils import encode_video_as_base64

logger = logging.getLogger(__name__)


@dataclass
class ReplyPreparation:
    primary_model: str
    fallback_model: str
    user_profile: Dict[str, Any]
    knowledge_base: List[Dict[str, Any]]
    kb_attachments: List[Dict[str, str]]
    user_message: Dict[str, Any]
    guidance_payload: Dict[str, Any]
    base_meta: Dict[str, Any]

    def build_messages_for_model(self, model: str, *, mode: PromptMode) -> List[Dict[str, Any]]:
        system_prompt = render_helpdesk_system_prompt(model=model, mode=mode)
        return [
            {'role': 'system', 'content': system_prompt},
            self.user_message,
            self.guidance_payload,
        ]

    def build_message_pair(self, *, mode: PromptMode) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        primary_messages = self.build_messages_for_model(self.primary_model, mode=mode)
        fallback_messages = self.build_messages_for_model(self.fallback_model, mode=mode)
        return primary_messages, fallback_messages


def _json_loads_maybe(s: Any) -> Any:
    if isinstance(s, (dict, list)):
        return s
    if isinstance(s, str):
        try:
            return json.loads(s)
        except Exception:
            return s
    return s


def _unwrap_persisted_message_content(raw_content: Any) -> Any:
    parsed = _json_loads_maybe(raw_content)
    if not isinstance(parsed, dict):
        return parsed
    nested_content = parsed.get('content')
    if nested_content is None:
        return parsed
    # User messages are stored as the full request envelope; history needs the nested payload.
    if 'type' in parsed or 'session_id' in parsed:
        return _json_loads_maybe(nested_content)
    return parsed


def _customer_id(user) -> Optional[int]:
    profile = getattr(user, 'hongwen_profile', None)
    return getattr(profile, 'customer_id', None)


def _learning_profile(user) -> Optional[UserProfile]:
    return getattr(user, 'hongwen_learning_profile', None)


_QUERY_STOPWORDS = {
    'a', 'an', 'and', 'are', 'at', 'by', 'do', 'for', 'how', 'i', 'if', 'in', 'is',
    'it', 'of', 'on', 'or', 'the', 'to', 'with', 'you', 'your',
}


def _normalize_search_text(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


def _query_terms(text: str) -> List[str]:
    return [
        token for token in _normalize_search_text(text).split()
        if len(token) >= 2 and token not in _QUERY_STOPWORDS
    ]


def _parse_page_number(page_or_section: Any) -> Optional[int]:
    match = re.search(r'page_(\d+)', str(page_or_section or ''), re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _search_knowledge_base(*, query: str, customer_id: Optional[int], top_k: int) -> List[Dict[str, Any]]:
    # An unusable embedding provider raises here; a visitor is better served by an
    # answer with no KB passages than by a 500, so degrade and log the reason.
    try:
        return search_kb(query=query, customer_id=customer_id, top_k=top_k)
    except Exception:
        logger.exception('Knowledge-base search failed; answering without knowledge-base passages')
        return []


def _rerank_knowledge_base(query: str, knowledge_base: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
    if len(knowledge_base) <= limit:
        return knowledge_base[:limit]

    query_terms = _query_terms(query)
    if not query_terms:
        return knowledge_base[:limit]

    source_scores: Dict[str, float] = {}
    for entry in knowledge_base[:10]:
        source = str(entry.get('source') or '')
        source_scores[source] = source_scores.get(source, 0.0) + float(entry.get('score') or 0.0)
    primary_source = max(source_scores, key=source_scores.get) if source_scores else ''

    # DEBT: Epson TM-T88VI identity terms and the priority term weights below are
    # hardcoded from the helpdesk's first deployment. They should become per-customer
    # configuration once a second printer fleet exists; ported verbatim for parity.
    source_identity_terms = {'epson', 'tm', 't88vi'}
    content_terms = [term for term in query_terms if term not in source_identity_terms]

    phrase_candidates: List[str] = []
    for left, right in zip(content_terms, content_terms[1:]):
        phrase = f'{left} {right}'
        if phrase not in phrase_candidates:
            phrase_candidates.append(phrase)

    priority_term_weights = {
        'replace': 0.10, 'replacing': 0.10, 'roll': 0.08, 'paper': 0.08, 'cover': 0.04,
        'jammed': 0.10, 'install': 0.05, 'installing': 0.05, 'load': 0.05, 'loading': 0.05,
    }

    ranked: List[Tuple[int, float, Dict[str, Any]]] = []
    for index, entry in enumerate(knowledge_base):
        snippet_text = str(entry.get('snippet') or '')
        snippet_normalized = _normalize_search_text(snippet_text)
        snippet_terms = set(snippet_normalized.split())
        source_normalized = _normalize_search_text(os.path.basename(str(entry.get('source') or '')))
        source_terms = set(source_normalized.split())

        text_term_score = sum(weight for token, weight in priority_term_weights.items() if token in snippet_terms)
        phrase_matches = sum(1 for phrase in phrase_candidates if phrase in snippet_normalized)
        source_term_matches = sum(1 for token in source_identity_terms if token in source_terms)
        procedure_bonus = 0.0
        if 'roll paper' in snippet_normalized and (
            {'replace', 'replacing', 'install', 'installing', 'load', 'loading'} & snippet_terms
        ):
            procedure_bonus = 0.18
        toc_penalty = 0.18 if re.search(r'\.{5,}', snippet_text) else 0.0

        hybrid_score = float(entry.get('score') or 0.0)
        hybrid_score += text_term_score
        hybrid_score += 0.14 * phrase_matches
        hybrid_score += 0.005 * source_term_matches
        hybrid_score += procedure_bonus
        hybrid_score -= toc_penalty
        same_source = int(str(entry.get('source') or '') == primary_source)
        if same_source:
            hybrid_score += 0.02
        hybrid_score -= index * 1e-6
        ranked.append((same_source, hybrid_score, entry))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [entry for _, _, entry in ranked[:limit]]


def _render_pdf_page_attachment(entry: Dict[str, Any]) -> Optional[Dict[str, str]]:
    stored_source = str(entry.get('source') or '').strip()
    page_number = _parse_page_number(entry.get('page_or_section'))
    if not stored_source.lower().endswith('.pdf') or not page_number:
        return None

    source_path = resolve_stored_path(stored_source)
    if not source_path or not os.path.exists(source_path):
        return None

    try:
        import fitz  # PyMuPDF
    except Exception:
        return None

    cache_key = hashlib.sha1(f'{os.path.abspath(source_path)}:{page_number}'.encode('utf-8')).hexdigest()
    relative_path = f'kb_assets/{cache_key}_page_{page_number}.png'
    storage_name = f'hongwen/{relative_path}'
    storage = private_storage()

    if not storage.exists(storage_name):
        document = fitz.open(source_path)
        try:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            storage.save(storage_name, ContentFile(pixmap.tobytes('png')))
        finally:
            document.close()

    page_or_section = str(entry.get('page_or_section') or '')
    return {
        'type': 'image',
        'path': relative_path,
        'url': build_protected_upload_url(relative_path),
        'source': stored_source,
        'page_or_section': page_or_section,
        'caption': build_attachment_caption(stored_source, page_or_section),
    }


def _build_kb_attachments(knowledge_base: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    attachments: List[Dict[str, str]] = []
    seen_paths: set[str] = set()
    if not knowledge_base:
        return attachments

    primary_entry = knowledge_base[0]
    metadata = primary_entry.get('metadata') or {}
    raw_paths: List[str] = []
    for key in ('image_paths', 'images'):
        value = metadata.get(key)
        if isinstance(value, list):
            raw_paths.extend(str(p) for p in value if p)
    raw_paths.extend(extract_image_paths(str(primary_entry.get('snippet') or '')))

    for raw_path in raw_paths:
        normalized_path = normalize_upload_relative_path(raw_path)
        if not normalized_path or normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)
        source = str(primary_entry.get('source') or '')
        page_or_section = str(primary_entry.get('page_or_section') or '')
        attachments.append({
            'type': 'image',
            'path': normalized_path,
            'url': build_protected_upload_url(normalized_path),
            'source': source,
            'page_or_section': page_or_section,
            'caption': build_attachment_caption(source, page_or_section),
        })

    if attachments:
        return attachments

    rendered_attachment = _render_pdf_page_attachment(primary_entry)
    if rendered_attachment:
        attachments.append(rendered_attachment)
    return attachments


def _strip_inline_image_markdown(text: str) -> str:
    cleaned = re.sub(r'!\[.*?\]\((.*?)\)', '', text or '')
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _build_conversation_history(user, session_id: Optional[str], limit: int = 20) -> List[Dict[str, Any]]:
    query = Message.objects.filter(user_id=user.id)
    if session_id:
        query = query.filter(session_id=session_id)
    rows = list(query.order_by('-created_at')[:limit])
    rows.reverse()
    return [
        {'role': m.role, 'type': m.type, 'content': _unwrap_persisted_message_content(m.content)}
        for m in rows
    ]


def _resolve_uploads(user, file_ids: List[str]) -> List[UploadFile]:
    if not file_ids:
        return []
    files = UploadFile.objects.filter(user_id=user.id, file_id__in=file_ids)
    by_id = {f.file_id: f for f in files}
    return [by_id[fid] for fid in file_ids if fid in by_id]


def _materialize_uploads(user, file_ids: List[str], temp_files: List[str]) -> List[str]:
    """Resolve file_ids to temp filesystem paths (for ffmpeg/vision), tracking cleanup."""
    paths: List[str] = []
    for upload in _resolve_uploads(user, file_ids):
        tmp = materialize_upload_to_tempfile(upload)
        temp_files.append(tmp)
        paths.append(tmp)
    return paths


def _extract_latest_user_input_for_kb_and_llm(
    user, message_type: str, message_content: Dict[str, Any], temp_files: List[str],
) -> Tuple[Dict[str, Any], str, bool, bool, bool, bool]:
    """Return (latest_input, kb_query_text, has_text, has_images, has_audio, has_video)."""
    text_parts: List[str] = []
    has_images = has_audio = has_video = has_text = False
    latest_input: Dict[str, Any] = {'type': message_type}

    if message_type in ('text', 'form'):
        latest_input['text'] = message_content.get('text') or message_content.get('detail') or message_content
        return latest_input, str(latest_input['text']), True, False, False, False

    if message_type == 'audio':
        file_id = message_content.get('file_id') or message_content.get('audio_file_id')
        audio_paths = _materialize_uploads(user, [file_id] if file_id else [], temp_files)
        latest_input['audio'] = {'file_path': audio_paths[0] if audio_paths else ''}
        return latest_input, '', False, False, True, False

    if message_type == 'image':
        if 'file_paths' in message_content:
            file_paths = list(message_content['file_paths'])
        else:
            file_id = message_content.get('file_id')
            file_paths = _materialize_uploads(user, [file_id] if file_id else [], temp_files)
        latest_input['images'] = {'file_paths': file_paths}
        return latest_input, '', False, True, False, False

    if message_type == 'mixed':
        text_val = message_content.get('text') or ''
        if isinstance(text_val, str) and text_val.strip():
            has_text = True
            text_parts.append(text_val.strip())
        latest_input['text'] = text_val

        image_file_ids = message_content.get('image_file_ids') or []
        image_paths = _materialize_uploads(user, list(image_file_ids), temp_files)
        if image_paths:
            has_images = True
            latest_input['images'] = {'file_paths': image_paths}

        audio_file_id = message_content.get('audio_file_id')
        audio_paths = _materialize_uploads(user, [audio_file_id] if audio_file_id else [], temp_files)
        if audio_paths:
            has_audio = True
            latest_input['audio'] = {'file_path': audio_paths[0]}

        video_file_ids = message_content.get('video_file_ids') or []
        video_paths = _materialize_uploads(user, list(video_file_ids), temp_files)
        if video_paths:
            has_video = True
            latest_input['videos'] = {'file_paths': video_paths}

        kb_query_text = ' '.join(text_parts).strip()
        return latest_input, kb_query_text, has_text, has_images, has_audio, has_video

    # unknown => treat as text
    latest_input['text'] = str(message_content)
    return latest_input, latest_input['text'], True, False, False, False


def _build_unified_multimodal_user_message(
    *, has_text: bool, has_images: bool, has_audio: bool, has_video: bool,
    latest_input: Dict[str, Any], transcript: Optional[str],
) -> Dict[str, Any]:
    """Construct an OpenAI-style multimodal user message."""
    parts: List[Dict[str, Any]] = []
    if has_text and latest_input.get('text'):
        parts.append({'type': 'text', 'text': str(latest_input.get('text'))})
    if transcript:
        transcript_label = 'Audio transcript'
        if has_text and latest_input.get('text'):
            transcript_label = 'Additional audio transcript'
        parts.append({'type': 'text', 'text': f'[{transcript_label}] {transcript}'})
    elif has_audio:
        apath = latest_input.get('audio', {}).get('file_path', '')
        if apath:
            try:
                audio_data_url = encode_audio_as_base64(apath)
                parts.append({'type': 'input_audio', 'input_audio': {'data': audio_data_url, 'format': 'mp3'}})
            except Exception:
                parts.append({'type': 'text', 'text': f'[Audio reference (failed to encode): {apath}]'})

    if has_images:
        for p in latest_input.get('images', {}).get('file_paths', []):
            _, data_url = encode_image_as_data_url(p)
            parts.append({'type': 'image_url', 'image_url': {'url': data_url}})

    if has_video:
        for p in latest_input.get('videos', {}).get('file_paths', []):
            try:
                video_data_url = encode_video_as_base64(p)
                parts.append({
                    'type': 'video_url', 'video_url': {'url': video_data_url},
                    'fps': 2, 'media_resolution': 'default',
                })
            except Exception:
                parts.append({'type': 'text', 'text': f'[Video reference (failed to encode): {p}]'})

    return {'role': 'user', 'content': parts if parts else (latest_input.get('text') or '')}


def _build_reply_meta(
    *, knowledge_base: List[Dict[str, Any]], kb_attachments: List[Dict[str, str]],
    primary_model: str, fallback_model: str, model_used: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        'used_knowledge_base': bool(knowledge_base),
        'knowledge_sources': [x.get('source', '') for x in knowledge_base],
        'attachments': kb_attachments,
        'image_paths': [attachment['path'] for attachment in kb_attachments],
        'model_used': model_used or primary_model,
        'model_primary': primary_model,
        'model_fallback': fallback_model,
        'prompt_pack_primary': load_prompt_pack(primary_model).pack_name,
        'prompt_pack_fallback': load_prompt_pack(fallback_model).pack_name,
    }


def _extract_stream_text_delta(chunk: Dict[str, Any]) -> str:
    choices = chunk.get('choices') or []
    if not choices:
        return ''
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message_delta = choice.get('delta') or choice.get('message') or {}
    content = message_delta.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get('type') or '').lower()
            if item_type in {'text', 'output_text'}:
                parts.append(str(item.get('text') or item.get('content') or ''))
        return ''.join(parts)
    return ''


def prepare_reply_generation(*, user, new_message: Message, session_id: Optional[str]) -> ReplyPreparation:
    profile = _learning_profile(user)
    user_profile = {
        'weak_points': profile.weak_points if profile else '{}',
        'suggested_solutions': profile.suggested_solutions if profile else '{}',
        'notes': profile.notes if profile else '',
    }

    message_content = _unwrap_persisted_message_content(new_message.content) or {}
    if not isinstance(message_content, dict):
        message_content = {'text': str(message_content)}

    temp_files: List[str] = []
    try:
        latest_input, kb_query_text, has_text, has_images, has_audio, has_video = _extract_latest_user_input_for_kb_and_llm(
            user, new_message.type, message_content, temp_files,
        )

        if has_images and not (kb_query_text or '').strip():
            kb_query_text = 'helpdesk image problem'

        transcript: Optional[str] = None
        if has_audio:
            audio_path = str(latest_input.get('audio', {}).get('file_path') or '').strip()
            if audio_path:
                try:
                    transcript = transcribe_audio(
                        model=select_primary_asr_ref().qualified_name, file_path=audio_path, language='zh',
                    ).strip()
                except Exception:
                    transcript = None

        if transcript:
            kb_query_text = ' '.join(part for part in [kb_query_text, transcript] if part).strip()

        knowledge_base_candidates = _search_knowledge_base(
            query=kb_query_text or '', customer_id=_customer_id(user), top_k=40,
        ) if (kb_query_text or has_images) else []
        knowledge_base = _rerank_knowledge_base(kb_query_text or '', knowledge_base_candidates, limit=5)
        kb_attachments = _build_kb_attachments(knowledge_base)

        conversation_history = _build_conversation_history(user, session_id=session_id, limit=20)
        llm_user_input = {
            'conversation_history': conversation_history,
            'user_profile': user_profile,
            'knowledge_base': knowledge_base,
        }

        user_msg_for_multimodal = _build_unified_multimodal_user_message(
            has_text=has_text, has_images=has_images, has_audio=has_audio, has_video=has_video,
            latest_input=latest_input, transcript=transcript,
        )
    finally:
        for tmp in temp_files:
            try:
                os.remove(tmp)
            except OSError:
                pass

    guidance_payload = {
        'role': 'user',
        'content': json.dumps({'guidance_payload': llm_user_input}, ensure_ascii=False),
    }

    has_multimodal_input = has_images or has_video or (has_audio and not transcript)
    primary_ref = select_primary_omni_ref(multimodal=has_multimodal_input)
    primary_model = primary_ref.qualified_name
    fallback_model = select_fallback_omni_ref(multimodal=has_multimodal_input, primary_ref=primary_ref).qualified_name

    return ReplyPreparation(
        primary_model=primary_model,
        fallback_model=fallback_model,
        user_profile=user_profile,
        knowledge_base=knowledge_base,
        kb_attachments=kb_attachments,
        user_message=user_msg_for_multimodal,
        guidance_payload=guidance_payload,
        base_meta=_build_reply_meta(
            knowledge_base=knowledge_base, kb_attachments=kb_attachments,
            primary_model=primary_model, fallback_model=fallback_model,
        ),
    )


def finalize_reply_payload(
    *, raw_content: str, preparation: ReplyPreparation, include_profile_updates: bool,
    model_used: Optional[str] = None,
) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
    content = raw_content.strip() if isinstance(raw_content, str) else ''
    reply_text = content
    updated_weak_points = '{}'
    updated_suggested_solutions = '{}'
    meta = dict(preparation.base_meta)
    if model_used:
        meta['model_used'] = model_used

    if include_profile_updates:
        try:
            parsed = json.loads(content)
            reply_text = parsed.get('assistant_reply', reply_text)
            updated_weak_points = parsed.get('updated_weak_points', updated_weak_points)
            updated_suggested_solutions = parsed.get('updated_suggested_solutions', updated_suggested_solutions)
            model_meta = parsed.get('meta') or {}
            if isinstance(model_meta, dict):
                for key, value in model_meta.items():
                    # Provenance is measured here, not self-reported by the model.
                    if key in {'attachments', 'image_paths', 'knowledge_sources', 'used_knowledge_base'}:
                        continue
                    meta[key] = value
        except Exception:
            pass

    if preparation.kb_attachments:
        reply_text = _strip_inline_image_markdown(reply_text)

    profile_updates = {
        'weak_points': updated_weak_points,
        'suggested_solutions': updated_suggested_solutions,
    }
    return reply_text, meta, profile_updates


def stream_reply_deltas(
    *, preparation: ReplyPreparation, regenerate: bool = False, temperature: float = 0.2,
) -> Iterator[Tuple[str, str]]:
    model_used = preparation.primary_model
    primary_messages, fallback_messages = preparation.build_message_pair(
        mode='stream_regenerate' if regenerate else 'stream',
    )
    for chunk in stream_chat_completion_with_fallback(
        primary_model=preparation.primary_model,
        fallback_model=preparation.fallback_model,
        messages=primary_messages,
        fallback_messages=fallback_messages,
        temperature=temperature,
    ):
        model_used = str(chunk.get('_model_used') or model_used)
        delta_text = _extract_stream_text_delta(chunk)
        if delta_text:
            yield delta_text, model_used


def generate_reply_and_update_profile(
    *, user, new_message: Message, session_id: Optional[str], regenerate: bool = False,
) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
    """Return (reply_text, meta, profile_updates); the caller (PR4) persists the profile."""
    preparation = prepare_reply_generation(user=user, new_message=new_message, session_id=session_id)
    temperature = 0.45 if regenerate else 0.2
    primary_messages, fallback_messages = preparation.build_message_pair(
        mode='chat_regenerate' if regenerate else 'chat',
    )
    response = chat_completion_with_fallback(
        primary_model=preparation.primary_model,
        fallback_model=preparation.fallback_model,
        messages=primary_messages,
        fallback_messages=fallback_messages,
        temperature=temperature,
    )
    content = response.get('choices', [{}])[0].get('message', {}).get('content', '')
    return finalize_reply_payload(
        raw_content=content, preparation=preparation, include_profile_updates=True,
        model_used=response.get('_model_used', preparation.primary_model),
    )


def apply_profile_updates(user, profile_updates: Dict[str, str]) -> None:
    """Persist the learning-profile updates returned by a non-streaming turn."""
    profile, _created = UserProfile.objects.get_or_create(user=user)
    profile.weak_points = profile_updates.get('weak_points', profile.weak_points)
    profile.suggested_solutions = profile_updates.get('suggested_solutions', profile.suggested_solutions)
    profile.save(update_fields=['weak_points', 'suggested_solutions', 'last_updated'])