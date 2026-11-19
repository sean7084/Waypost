"""Volcengine (Doubao / Ark) provider client: /responses chat, embeddings,
openspeech bigmodel ASR (submit/query polling) and bidirectional binary WS TTS.

Port of helpdesk/backend/app/llm/volcengine_client.py with httpx replaced by
synchronous ``requests`` and the TTS WebSocket on ``websockets.sync.client``.
The binary frame protocol (version/header/event/session-id/payload, gzip) is
preserved verbatim. Config comes from Django settings; audio writes go through
private storage.
"""
from __future__ import annotations

import gzip
import json
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from websockets.sync.client import connect as sync_websocket_connect

from hongwen.services.storage import private_storage
from hongwen.services.utils.file_store import build_protected_upload_url

_DEFAULT_TIMEOUT = 90.0

_WS_PROTOCOL_VERSION = 0x1
_WS_HEADER_SIZE_UNITS = 0x1
_WS_FLAG_WITH_EVENT = 0x4
_WS_SERIALIZATION_RAW = 0x0
_WS_SERIALIZATION_JSON = 0x1
_WS_COMPRESSION_NONE = 0x0
_WS_COMPRESSION_GZIP = 0x1
_WS_MESSAGE_TYPE_FULL_CLIENT_REQUEST = 0x1
_WS_MESSAGE_TYPE_FULL_SERVER_RESPONSE = 0x9
_WS_MESSAGE_TYPE_AUDIO_ONLY_RESPONSE = 0xB
_WS_MESSAGE_TYPE_ERROR = 0xF
_WS_EVENT_START_CONNECTION = 1
_WS_EVENT_FINISH_CONNECTION = 2
_WS_EVENT_CONNECTION_STARTED = 50
_WS_EVENT_CONNECTION_FAILED = 51
_WS_EVENT_CONNECTION_FINISHED = 52
_WS_EVENT_START_SESSION = 100
_WS_EVENT_FINISH_SESSION = 102
_WS_EVENT_SESSION_STARTED = 150
_WS_EVENT_SESSION_CANCELED = 151
_WS_EVENT_SESSION_FINISHED = 152
_WS_EVENT_SESSION_FAILED = 153
_WS_EVENT_TASK_REQUEST = 200
_WS_EVENT_TTS_SENTENCE_START = 350
_WS_EVENT_TTS_SENTENCE_END = 351
_WS_EVENT_TTS_RESPONSE = 352
_WS_CONNECTION_EVENTS = {
    _WS_EVENT_CONNECTION_STARTED, _WS_EVENT_CONNECTION_FAILED, _WS_EVENT_CONNECTION_FINISHED,
}
_WS_SESSION_EVENTS = {
    _WS_EVENT_SESSION_STARTED, _WS_EVENT_SESSION_CANCELED, _WS_EVENT_SESSION_FINISHED,
    _WS_EVENT_SESSION_FAILED, _WS_EVENT_TTS_SENTENCE_START, _WS_EVENT_TTS_SENTENCE_END,
    _WS_EVENT_TTS_RESPONSE,
}

_session: Optional[requests.Session] = None


def _setting(name: str) -> str:
    return str(getattr(settings, name, '') or '').strip()


def _ark_base_url() -> str:
    return (_setting('VOLCENGINE_ARK_BASE_URL') or 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')


def _responses_url() -> str:
    return f'{_ark_base_url()}/responses'


def _embed_url() -> str:
    return _setting('VOLCENGINE_EMBED_URL') or f'{_ark_base_url()}/embeddings'


def _multimodal_embed_url() -> str:
    return _setting('VOLCENGINE_MULTIMODAL_EMBED_URL') or f'{_ark_base_url()}/embeddings/multimodal'


def _asr_submit_url() -> str:
    return _setting('VOLCENGINE_ASR_SUBMIT_URL') or 'https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit'


def _asr_query_url() -> str:
    return _setting('VOLCENGINE_ASR_QUERY_URL') or 'https://openspeech.bytedance.com/api/v3/auc/bigmodel/query'


def _require_api_key() -> str:
    api_key = _setting('VOLCENGINE_API_KEY')
    if not api_key:
        raise RuntimeError('Missing required environment variable: VOLCENGINE_API_KEY')
    return api_key


def _require_speech_api_key() -> str:
    api_key = _setting('VOLCENGINE_SPEECH_API_KEY')
    if not api_key:
        raise RuntimeError('Missing required environment variable: VOLCENGINE_SPEECH_API_KEY')
    return api_key


def _require_asr_api_key() -> str:
    return _require_speech_api_key()


def _asr_resource_id(model: str | None = None) -> str:
    return str(model or '').strip() or 'volc.seedasr.auc'


def _asr_poll_interval_seconds() -> float:
    value = _setting('VOLCENGINE_ASR_POLL_INTERVAL_SECONDS')
    try:
        return max(0.2, float(value)) if value else 1.0
    except ValueError:
        return 1.0


def _asr_poll_timeout_seconds() -> float:
    value = _setting('VOLCENGINE_ASR_POLL_TIMEOUT_SECONDS')
    try:
        return max(5.0, float(value)) if value else 120.0
    except ValueError:
        return 120.0


def _headers() -> Dict[str, str]:
    return {'Authorization': f'Bearer {_require_api_key()}', 'Content-Type': 'application/json'}


def _asr_headers(task_id: str, *, model: str, include_sequence: bool) -> Dict[str, str]:
    headers = {
        'Content-Type': 'application/json',
        'X-Api-Key': _require_asr_api_key(),
        'X-Api-Resource-Id': _asr_resource_id(model),
        'X-Api-Request-Id': task_id,
    }
    if include_sequence:
        headers['X-Api-Sequence'] = '-1'
    return headers


def _resolve_tts_websocket_url() -> str:
    candidate = _setting('VOLCENGINE_TTS_WS_URL')
    if not candidate:
        return 'wss://openspeech.bytedance.com/api/v3/tts/bidirection'
    if candidate.startswith(('ws://', 'wss://')):
        return candidate
    if candidate.startswith(('http://', 'https://')):
        parsed = urlparse(candidate)
        scheme = 'wss' if parsed.scheme == 'https' else 'ws'
        return f'{scheme}://{parsed.netloc}/api/v3/tts/bidirection'
    return 'wss://openspeech.bytedance.com/api/v3/tts/bidirection'


def _tts_headers(connect_id: str, *, model: str) -> Dict[str, str]:
    resource_id = str(model or '').strip() or 'seed-tts-2.0'
    return {
        'X-Api-Key': _require_speech_api_key(),
        'X-Api-Resource-Id': resource_id,
        'X-Api-Connect-Id': connect_id,
    }


def _force_utf8(response, *args, **kwargs):
    # These APIs return UTF-8 but often omit charset (text/event-stream, text/plain).
    # requests defaults such responses to ISO-8859-1, which double-encodes Chinese:
    # UTF-8 bytes read as Latin-1, then re-encoded to UTF-8 (你 e4bda0 -> c3a4c2bdc2a0).
    # Force UTF-8 on every response so iter_lines(decode_unicode=True), .text and
    # .json() all decode correctly. (httpx defaulted to UTF-8; requests does not.)
    response.encoding = 'utf-8'
    return response


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.hooks['response'].append(_force_utf8)
    return _session


def close_client() -> None:
    global _session
    if _session is not None:
        _session.close()
        _session = None


def _raise_for_status(response: requests.Response, *, action: str, model: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = ''
        try:
            response_text = (response.text or '').strip()
        except Exception:
            response_text = ''
        if len(response_text) > 1000:
            response_text = f'{response_text[:1000]}...'
        detail = response_text or response.reason
        raise RuntimeError(
            f"{action} failed for model '{model}' with HTTP {response.status_code}: {detail}"
        ) from exc


def _convert_content_items(content: List[Any], *, role: str) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            if item:
                converted.append({'type': 'input_text', 'text': item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type') or '').strip().lower()
        if item_type in {'text', 'input_text', 'output_text'}:
            text = str(item.get('text') or item.get('content') or '').strip()
            if text:
                converted.append({'type': 'input_text', 'text': text})
            continue
        if item_type == 'image_url':
            image_payload = item.get('image_url') or {}
            image_url = str(image_payload.get('url') or item.get('url') or '').strip()
            if image_url:
                converted.append({'type': 'input_image', 'image_url': image_url})
            continue
        if item_type == 'video_url':
            video_payload = item.get('video_url') or {}
            video_url = str(video_payload.get('url') or item.get('url') or '').strip()
            if video_url:
                converted.append({'type': 'input_video', 'video_url': video_url})
            continue
        if item_type == 'input_audio':
            raise RuntimeError('Volcengine Responses API adapter does not support raw audio chat input yet')
        fallback_text = str(item.get('text') or item.get('content') or '').strip()
        if fallback_text:
            converted.append({'type': 'input_text', 'text': fallback_text})

    if role != 'user' and converted:
        merged_text = ''.join(str(item.get('text') or '') for item in converted if item.get('type') == 'input_text')
        if merged_text:
            return [{'type': 'input_text', 'text': merged_text}]
    return converted


def _convert_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted_messages: List[Dict[str, Any]] = []
    for message in messages:
        role = str(message.get('role') or 'user').strip() or 'user'
        content = message.get('content')
        if isinstance(content, list):
            converted_content = _convert_content_items(content, role=role)
            if not converted_content:
                converted_messages.append({'role': role, 'content': ''})
                continue
            if role != 'user':
                merged_text = ''.join(str(item.get('text') or '') for item in converted_content)
                converted_messages.append({'role': role, 'content': merged_text})
            else:
                converted_messages.append({'role': role, 'content': converted_content})
            continue
        converted_messages.append({'role': role, 'content': str(content or '')})
    return converted_messages


def _build_chat_payload(
    *, model: str, messages: List[Dict[str, Any]], temperature: float,
    tools: Optional[List[Dict[str, Any]]], tool_choice: str | Dict[str, Any] | None,
    response_format: str | Dict[str, Any] | None, stream: bool = False,
) -> Dict[str, Any]:
    if tools or tool_choice is not None or response_format is not None:
        raise RuntimeError('Volcengine Responses API adapter does not support tools or response_format yet')
    payload: Dict[str, Any] = {'model': model, 'input': _convert_messages(messages), 'store': False}
    if temperature is not None:
        payload['temperature'] = temperature
    if stream:
        payload['stream'] = True
    return payload


def _extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    parts: List[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type') or '').strip().lower()
        if 'reasoning' in item_type:
            continue
        text = item.get('text')
        if isinstance(text, str):
            parts.append(text)
            continue
        nested_content = item.get('content')
        if isinstance(nested_content, str):
            parts.append(nested_content)
    return ''.join(parts)


def _extract_text_from_response(payload: Dict[str, Any]) -> str:
    direct_output_text = payload.get('output_text')
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        return direct_output_text.strip()
    if isinstance(direct_output_text, list):
        extracted = _extract_text_from_content(direct_output_text).strip()
        if extracted:
            return extracted

    output = payload.get('output') or []
    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get('type') or '').strip().lower()
        if item_type == 'message':
            parts.append(_extract_text_from_content(item.get('content')))
            continue
        if 'reasoning' in item_type:
            continue
        text = item.get('text')
        if isinstance(text, str):
            parts.append(text)
            continue
        content = item.get('content')
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.append(_extract_text_from_content(content))
    return ''.join(parts).strip()


def _normalize_chat_response(payload: Dict[str, Any], *, model: str) -> Dict[str, Any]:
    return {
        'id': payload.get('id') or '',
        'model': payload.get('model') or model,
        'choices': [{'message': {'content': _extract_text_from_response(payload)}}],
        'raw_response': payload,
    }


def _finalize_sse_event(event_name: str, data_lines: List[str]) -> Tuple[str, Dict[str, Any]] | None:
    if not data_lines:
        return None
    payload_text = '\n'.join(data_lines).strip()
    if not payload_text or payload_text == '[DONE]':
        return None
    try:
        return event_name or 'message', json.loads(payload_text)
    except json.JSONDecodeError:
        return None


def _extract_stream_delta_text(event_name: str, payload: Dict[str, Any]) -> str:
    event_type = str(payload.get('type') or event_name or '').strip().lower()
    if 'reasoning' in event_type:
        return ''
    if 'output_text.delta' in event_type:
        return str(payload.get('delta') or '')
    if event_type.endswith('content_part.added'):
        part = payload.get('part') or {}
        part_type = str(part.get('type') or '').strip().lower()
        if part_type in {'output_text', 'text'}:
            return str(part.get('text') or part.get('content') or '')
    return ''


def _normalize_asr_language(language: str | None) -> str | None:
    normalized = str(language or '').strip()
    if not normalized:
        return None
    aliases = {'zh': 'zh-CN', 'zh-cn': 'zh-CN', 'en': 'en-US', 'en-us': 'en-US', 'ja': 'ja-JP', 'ja-jp': 'ja-JP'}
    return aliases.get(normalized.lower(), normalized)


def _normalize_tts_language(language: str | None) -> str | None:
    normalized = str(language or '').strip()
    if not normalized:
        return None
    aliases = {
        'zh': 'zh-cn', 'zh-cn': 'zh-cn', 'en': 'en', 'en-us': 'en', 'ja': 'ja', 'ja-jp': 'ja',
        'es': 'es-mx', 'es-mx': 'es-mx', 'id': 'id', 'pt': 'pt-br', 'pt-br': 'pt-br',
    }
    return aliases.get(normalized.lower(), normalized)


def _guess_asr_audio_config(file_path: str) -> Dict[str, Any]:
    import os
    extension = os.path.splitext(str(file_path or ''))[1].strip().lower()
    if extension == '.mp3':
        return {'format': 'mp3'}
    if extension == '.wav':
        return {'format': 'wav'}
    if extension in {'.ogg', '.opus'}:
        return {'format': 'ogg'}
    if extension in {'.pcm', '.raw'}:
        return {'format': 'raw'}
    raise RuntimeError(
        f'Volcengine ASR requires mp3, wav, ogg, or raw audio. Unsupported file extension: {extension or "unknown"}'
    )


def _build_asr_submit_payload(*, audio_url: str, file_path: str, language: str | None) -> Dict[str, Any]:
    audio_config = {'url': str(audio_url or '').strip(), **_guess_asr_audio_config(file_path)}
    normalized_language = _normalize_asr_language(language)
    if normalized_language:
        audio_config['language'] = normalized_language
    return {
        'user': {'uid': _setting('VOLCENGINE_ASR_UID') or 'hongwen-mini'},
        'audio': audio_config,
        'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True},
    }


def _tts_namespace() -> str:
    return _setting('VOLCENGINE_TTS_NAMESPACE') or 'BidirectionalTTS'


def _build_tts_start_session_payload(*, voice: str, language_type: str | None) -> Dict[str, Any]:
    normalized_language = _normalize_tts_language(language_type)
    req_params: Dict[str, Any] = {'speaker': voice, 'audio_params': {'format': 'mp3', 'sample_rate': 24000}}
    if normalized_language:
        req_params['language'] = normalized_language
    return {
        'user': {'uid': _setting('VOLCENGINE_TTS_UID') or 'hongwen-mini'},
        'event': _WS_EVENT_START_SESSION,
        'namespace': _tts_namespace(),
        'req_params': req_params,
    }


def _build_tts_task_payload(*, text: str) -> Dict[str, Any]:
    return {'event': _WS_EVENT_TASK_REQUEST, 'namespace': _tts_namespace(), 'req_params': {'text': text}}


def _iter_tts_text_chunks(text: str, *, limit: int = 20000) -> List[str]:
    normalized = str(text or '')
    if not normalized:
        return []
    return [normalized[index:index + limit] for index in range(0, len(normalized), limit)]


def _uint32_bytes(value: int) -> bytes:
    return int(value).to_bytes(4, byteorder='big', signed=False)


def _read_uint32(buffer: bytes, offset: int, *, model: str, action: str) -> tuple[int, int]:
    if len(buffer) < offset + 4:
        raise RuntimeError(f"{action} failed for model '{model}': truncated websocket frame")
    return int.from_bytes(buffer[offset:offset + 4], byteorder='big', signed=False), offset + 4


def _build_tts_ws_frame(*, event: int, payload: Dict[str, Any] | None = None, session_id: str | None = None) -> bytes:
    payload_bytes = json.dumps(payload or {}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    header = bytes([
        (_WS_PROTOCOL_VERSION << 4) | _WS_HEADER_SIZE_UNITS,
        (_WS_MESSAGE_TYPE_FULL_CLIENT_REQUEST << 4) | _WS_FLAG_WITH_EVENT,
        (_WS_SERIALIZATION_JSON << 4) | _WS_COMPRESSION_NONE,
        0,
    ])
    parts = [header, _uint32_bytes(event)]
    if session_id is not None:
        session_bytes = session_id.encode('utf-8')
        parts.extend([_uint32_bytes(len(session_bytes)), session_bytes])
    parts.extend([_uint32_bytes(len(payload_bytes)), payload_bytes])
    return b''.join(parts)


def _decode_tts_ws_json(payload: bytes, *, model: str) -> Dict[str, Any]:
    if not payload:
        return {}
    try:
        decoded = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': invalid websocket JSON payload") from exc
    return decoded if isinstance(decoded, dict) else {'value': decoded}


def _maybe_decompress_tts_ws_payload(payload: bytes, *, compression: int, model: str) -> bytes:
    if not payload or compression == _WS_COMPRESSION_NONE:
        return payload
    if compression == _WS_COMPRESSION_GZIP:
        try:
            return gzip.decompress(payload)
        except OSError as exc:
            raise RuntimeError(f"Speech synthesis failed for model '{model}': invalid gzip websocket payload") from exc
    raise RuntimeError(f"Speech synthesis failed for model '{model}': unsupported websocket compression {compression}")


def _parse_tts_ws_frame(message: bytes, *, model: str) -> Dict[str, Any]:
    if len(message) < 4:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': truncated websocket frame header")

    header_0, header_1, header_2, _ = message[:4]
    version = header_0 >> 4
    header_size = (header_0 & 0x0F) * 4
    if version != _WS_PROTOCOL_VERSION:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': unsupported websocket protocol version {version}")
    if len(message) < header_size:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': invalid websocket header size")

    message_type = header_1 >> 4
    flags = header_1 & 0x0F
    serialization = header_2 >> 4
    compression = header_2 & 0x0F
    offset = header_size

    if message_type == _WS_MESSAGE_TYPE_ERROR:
        error_code, offset = _read_uint32(message, offset, model=model, action='Speech synthesis')
        payload_size, offset = _read_uint32(message, offset, model=model, action='Speech synthesis')
        if len(message) < offset + payload_size:
            raise RuntimeError(f"Speech synthesis failed for model '{model}': truncated websocket error payload")
        payload = _maybe_decompress_tts_ws_payload(message[offset:offset + payload_size], compression=compression, model=model)
        payload_json = _decode_tts_ws_json(payload, model=model) if payload else {}
        detail = str(payload_json.get('message') or payload_json.get('status_code') or error_code)
        raise RuntimeError(f"Speech synthesis failed for model '{model}': {detail}")

    event = None
    if flags & _WS_FLAG_WITH_EVENT:
        event, offset = _read_uint32(message, offset, model=model, action='Speech synthesis')

    connection_id = ''
    session_id = ''
    if event in _WS_CONNECTION_EVENTS:
        connection_id_size, offset = _read_uint32(message, offset, model=model, action='Speech synthesis')
        if len(message) < offset + connection_id_size:
            raise RuntimeError(f"Speech synthesis failed for model '{model}': truncated websocket connection id")
        connection_id = message[offset:offset + connection_id_size].decode('utf-8', errors='replace')
        offset += connection_id_size
    elif event in _WS_SESSION_EVENTS:
        session_id_size, offset = _read_uint32(message, offset, model=model, action='Speech synthesis')
        if len(message) < offset + session_id_size:
            raise RuntimeError(f"Speech synthesis failed for model '{model}': truncated websocket session id")
        session_id = message[offset:offset + session_id_size].decode('utf-8', errors='replace')
        offset += session_id_size

    payload_size, offset = _read_uint32(message, offset, model=model, action='Speech synthesis')
    if len(message) < offset + payload_size:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': truncated websocket payload")
    payload = message[offset:offset + payload_size]
    payload = _maybe_decompress_tts_ws_payload(payload, compression=compression, model=model)

    payload_json: Dict[str, Any] | None = None
    if message_type != _WS_MESSAGE_TYPE_AUDIO_ONLY_RESPONSE and serialization == _WS_SERIALIZATION_JSON and payload:
        payload_json = _decode_tts_ws_json(payload, model=model)

    return {
        'event': event, 'message_type': message_type, 'serialization': serialization,
        'compression': compression, 'connection_id': connection_id, 'session_id': session_id,
        'payload': payload, 'payload_json': payload_json or {},
    }


def _raise_for_tts_ws_event(frame: Dict[str, Any], *, model: str, action: str) -> None:
    payload_json = frame.get('payload_json') or {}
    detail = str(payload_json.get('message') or payload_json.get('status_code') or frame.get('event') or 'unknown error')
    raise RuntimeError(f"{action} failed for model '{model}': {detail}")


def _ensure_tts_ws_success(frame: Dict[str, Any], *, model: str, action: str) -> None:
    payload_json = frame.get('payload_json') or {}
    status_code = str(payload_json.get('status_code') or '').strip()
    if status_code and status_code != '20000000':
        _raise_for_tts_ws_event(frame, model=model, action=action)


def _recv_tts_ws_frame_sync(websocket: Any, *, model: str) -> Dict[str, Any]:
    message = websocket.recv(timeout=30)
    if isinstance(message, str):
        detail = message.strip() or 'unexpected text websocket message'
        raise RuntimeError(f"Speech synthesis failed for model '{model}': {detail}")
    return _parse_tts_ws_frame(message, model=model)


def _wait_for_tts_ws_event_sync(websocket: Any, *, model: str, expected_event: int, action: str) -> Dict[str, Any]:
    while True:
        frame = _recv_tts_ws_frame_sync(websocket, model=model)
        event = frame.get('event')
        if event == expected_event:
            return frame
        if event in {_WS_EVENT_CONNECTION_FAILED, _WS_EVENT_SESSION_CANCELED, _WS_EVENT_SESSION_FAILED}:
            _raise_for_tts_ws_event(frame, model=model, action=action)


def _stream_tts_websocket_audio_sync(*, model: str, text: str, voice: str, language_type: str | None = None) -> Iterator[tuple[str, bytes]]:
    connect_id = str(uuid.uuid4())
    session_id = uuid.uuid4().hex
    text_chunks = _iter_tts_text_chunks(text)
    if not text_chunks:
        raise RuntimeError('Speech synthesis failed: empty text')

    with sync_websocket_connect(
        _resolve_tts_websocket_url(),
        additional_headers=_tts_headers(connect_id, model=model),
        user_agent_header='hongwen-mini/volcengine-tts',
        open_timeout=20,
        max_size=None,
    ) as websocket:
        websocket.send(_build_tts_ws_frame(event=_WS_EVENT_START_CONNECTION, payload={}))
        _wait_for_tts_ws_event_sync(websocket, model=model, expected_event=_WS_EVENT_CONNECTION_STARTED, action='Speech synthesis connection')

        websocket.send(_build_tts_ws_frame(
            event=_WS_EVENT_START_SESSION,
            payload=_build_tts_start_session_payload(voice=voice, language_type=language_type),
            session_id=session_id,
        ))
        _wait_for_tts_ws_event_sync(websocket, model=model, expected_event=_WS_EVENT_SESSION_STARTED, action='Speech synthesis session')

        for text_chunk in text_chunks:
            websocket.send(_build_tts_ws_frame(
                event=_WS_EVENT_TASK_REQUEST, payload=_build_tts_task_payload(text=text_chunk), session_id=session_id,
            ))
        websocket.send(_build_tts_ws_frame(event=_WS_EVENT_FINISH_SESSION, payload={}, session_id=session_id))

        try:
            while True:
                frame = _recv_tts_ws_frame_sync(websocket, model=model)
                event = frame.get('event')
                if event == _WS_EVENT_TTS_RESPONSE:
                    chunk = bytes(frame.get('payload') or b'')
                    if chunk:
                        yield connect_id, chunk
                    continue
                if event in {_WS_EVENT_TTS_SENTENCE_START, _WS_EVENT_TTS_SENTENCE_END}:
                    continue
                if event == _WS_EVENT_SESSION_FINISHED:
                    _ensure_tts_ws_success(frame, model=model, action='Speech synthesis session')
                    break
                if event in {_WS_EVENT_CONNECTION_FAILED, _WS_EVENT_SESSION_CANCELED, _WS_EVENT_SESSION_FAILED}:
                    _raise_for_tts_ws_event(frame, model=model, action='Speech synthesis')
        finally:
            websocket.send(_build_tts_ws_frame(event=_WS_EVENT_FINISH_CONNECTION, payload={}))
            _wait_for_tts_ws_event_sync(websocket, model=model, expected_event=_WS_EVENT_CONNECTION_FINISHED, action='Speech synthesis connection close')


def _extract_asr_status(response: requests.Response) -> tuple[str, str]:
    status_code = str(response.headers.get('X-Api-Status-Code') or '').strip()
    status_message = str(response.headers.get('X-Api-Message') or response.reason or '').strip()
    return status_code, status_message


def _raise_for_asr_status(response: requests.Response, *, action: str, model: str, allow_pending: bool = False) -> str:
    status_code, status_message = _extract_asr_status(response)
    if status_code == '20000000':
        return status_code
    if allow_pending and status_code in {'20000001', '20000002'}:
        return status_code
    detail = status_message or f'status_code={status_code or "unknown"}'
    raise RuntimeError(f"{action} failed for model '{model}': {detail}")


def _extract_asr_text(payload: Dict[str, Any], *, model: str) -> str:
    result = payload.get('result') or {}
    text = str(result.get('text') or '').strip()
    if text:
        return text
    raise RuntimeError(f"Audio transcription failed for model '{model}': empty transcript")


def _extract_embeddings(payload: Dict[str, Any], *, model: str) -> List[List[float]]:
    data = payload.get('data')
    if not isinstance(data, list):
        raise RuntimeError(f"Embedding failed for model '{model}': malformed response payload")
    embeddings: List[List[float]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        vector = item.get('embedding')
        if not isinstance(vector, list):
            continue
        embeddings.append([float(value) for value in vector])
    if embeddings:
        return embeddings
    raise RuntimeError(f"Embedding failed for model '{model}': empty embedding response")


def _is_multimodal_embedding_model(model: str) -> bool:
    return 'embedding-vision' in str(model or '').strip().lower()


def _extract_multimodal_embedding(payload: Dict[str, Any], *, model: str) -> List[float]:
    data = payload.get('data') or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Embedding failed for model '{model}': malformed multimodal embedding payload")
    vector = data.get('embedding')
    if not isinstance(vector, list) or not vector:
        raise RuntimeError(f"Embedding failed for model '{model}': empty multimodal embedding response")
    return [float(value) for value in vector]


def embed_text(*, model: str, texts: List[str]) -> List[List[float]]:
    if _is_multimodal_embedding_model(model):
        embeddings: List[List[float]] = []
        for text in texts:
            payload = {
                'model': model, 'encoding_format': 'float',
                'input': [{'type': 'text', 'text': str(text or '')}],
            }
            response = _get_session().post(_multimodal_embed_url(), headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT)
            _raise_for_status(response, action='Embedding', model=model)
            embeddings.append(_extract_multimodal_embedding(response.json(), model=model))
        return embeddings

    payload = {'model': model, 'input': texts}
    response = _get_session().post(_embed_url(), headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT)
    _raise_for_status(response, action='Embedding', model=model)
    return _extract_embeddings(response.json(), model=model)


def chat_completion(
    *, model: str, messages: List[Dict[str, Any]], temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = _build_chat_payload(
        model=model, messages=messages, temperature=temperature,
        tools=tools, tool_choice=tool_choice, response_format=response_format,
    )
    response = _get_session().post(_responses_url(), headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT)
    _raise_for_status(response, action='Chat completion', model=model)
    return _normalize_chat_response(response.json(), model=model)


def stream_chat_completion(
    *, model: str, messages: List[Dict[str, Any]], temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Iterator[Dict[str, Any]]:
    payload = _build_chat_payload(
        model=model, messages=messages, temperature=temperature,
        tools=tools, tool_choice=tool_choice, response_format=response_format, stream=True,
    )
    response = _get_session().post(_responses_url(), headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT, stream=True)
    try:
        if response.status_code >= 400:
            _raise_for_status(response, action='Chat completion stream', model=model)

        current_event = 'message'
        current_data_lines: List[str] = []

        def _emit(event_name: str, payload_data: Dict[str, Any]):
            delta_text = _extract_stream_delta_text(event_name, payload_data)
            if not delta_text:
                return None
            return {'choices': [{'delta': {'content': delta_text}}], 'raw_event': payload_data}

        for raw_line in response.iter_lines(decode_unicode=True):
            line = (raw_line or '').rstrip('\r')
            if not line:
                parsed_event = _finalize_sse_event(current_event, current_data_lines)
                current_event = 'message'
                current_data_lines = []
                if not parsed_event:
                    continue
                event_name, payload_data = parsed_event
                chunk = _emit(event_name, payload_data)
                if chunk:
                    yield chunk
                continue
            if line.startswith('event:'):
                current_event = line[len('event:'):].strip() or 'message'
                continue
            if line.startswith('data:'):
                current_data_lines.append(line[len('data:'):].lstrip())

        parsed_event = _finalize_sse_event(current_event, current_data_lines)
        if parsed_event:
            event_name, payload_data = parsed_event
            chunk = _emit(event_name, payload_data)
            if chunk:
                yield chunk
    finally:
        response.close()


def transcribe_audio(*, model: str, file_path: str, public_file_url: str, language: str | None = None) -> str:
    normalized_public_file_url = str(public_file_url or '').strip()
    if not normalized_public_file_url:
        raise RuntimeError(f"Audio transcription failed for model '{model}': missing public audio URL")

    task_id = str(uuid.uuid4())
    payload = _build_asr_submit_payload(audio_url=normalized_public_file_url, file_path=file_path, language=language)

    submit_response = _get_session().post(
        _asr_submit_url(), headers=_asr_headers(task_id, model=model, include_sequence=True), json=payload, timeout=_DEFAULT_TIMEOUT,
    )
    _raise_for_status(submit_response, action='Audio transcription submit', model=model)
    _raise_for_asr_status(submit_response, action='Audio transcription submit', model=model)

    deadline = time.monotonic() + _asr_poll_timeout_seconds()
    last_pending_status = ''
    while time.monotonic() < deadline:
        query_response = _get_session().post(
            _asr_query_url(), headers=_asr_headers(task_id, model=model, include_sequence=False), json={}, timeout=_DEFAULT_TIMEOUT,
        )
        _raise_for_status(query_response, action='Audio transcription query', model=model)
        query_status = _raise_for_asr_status(query_response, action='Audio transcription query', model=model, allow_pending=True)
        if query_status == '20000000':
            return _extract_asr_text(query_response.json(), model=model)
        last_pending_status = query_status
        time.sleep(_asr_poll_interval_seconds())

    raise RuntimeError(
        f"Audio transcription failed for model '{model}': timed out while waiting for Volcengine ASR ({last_pending_status or 'pending'})"
    )


def synthesize_speech(*, model: str, text: str, voice: str, language_type: str | None = None) -> Dict[str, Any]:
    normalized_text = str(text or '').strip()
    if not normalized_text:
        raise RuntimeError('Speech synthesis failed: empty text')
    normalized_voice = str(voice or '').strip()
    if not normalized_voice:
        raise RuntimeError('Speech synthesis failed: missing voice')

    request_id = ''
    chunks: List[bytes] = []
    for request_id, chunk in _stream_tts_websocket_audio_sync(
        model=model, text=normalized_text, voice=normalized_voice, language_type=language_type,
    ):
        if chunk:
            chunks.append(chunk)
    audio = b''.join(chunks)
    if not audio:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': empty audio stream")

    stored_name = f'tts_{uuid.uuid4().hex}.mp3'
    stored_name = private_storage().save(f'hongwen/tts/{stored_name}', ContentFile(audio))
    relative_path = stored_name[len('hongwen/'):] if stored_name.startswith('hongwen/') else stored_name
    return {
        'audio_url': build_protected_upload_url(relative_path),
        'stored_path': stored_name,
        'provider_audio_url': '',
        'request_id': request_id,
    }


def stream_speech_audio(*, model: str, text: str, voice: str, language_type: str | None = None) -> Iterator[bytes]:
    normalized_text = str(text or '').strip()
    if not normalized_text:
        raise RuntimeError('Speech synthesis failed: empty text')
    normalized_voice = str(voice or '').strip()
    if not normalized_voice:
        raise RuntimeError('Speech synthesis failed: missing voice')

    yielded_any = False
    for _, chunk in _stream_tts_websocket_audio_sync(
        model=model, text=normalized_text, voice=normalized_voice, language_type=language_type,
    ):
        if chunk:
            yielded_any = True
            yield chunk
    if not yielded_any:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': empty audio stream")