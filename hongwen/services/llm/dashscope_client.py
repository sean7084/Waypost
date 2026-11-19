"""DashScope (Qwen) provider client: compatible-mode chat/embeddings, multimodal
generation ASR, and WebSocket streaming TTS.

Port of helpdesk/backend/app/llm/dashscope_client.py with httpx replaced by
synchronous ``requests`` (chat streaming via ``post(stream=True).iter_lines()``)
and the TTS WebSocket on ``websockets.sync.client`` (no event loop). Config comes
from Django settings; generated audio is written through private storage.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from typing import Any, Dict, Iterator, List, Optional

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from websockets.sync.client import connect as sync_websocket_connect

from hongwen.services.storage import private_storage
from hongwen.services.utils.file_store import build_protected_upload_url

DASHSCOPE_COMPATIBLE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
DASHSCOPE_CHAT_URL = f'{DASHSCOPE_COMPATIBLE_BASE_URL}/chat/completions'
DASHSCOPE_EMBED_URL = f'{DASHSCOPE_COMPATIBLE_BASE_URL}/embeddings'
DASHSCOPE_GENERATION_URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
DASHSCOPE_REALTIME_TTS_URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'

_DEFAULT_TIMEOUT = 90.0

_session: Optional[requests.Session] = None


def _setting(name: str) -> str:
    return str(getattr(settings, name, '') or '').strip()


def _require_api_key() -> str:
    api_key = _setting('QWEN_API_KEY')
    if not api_key:
        raise RuntimeError('Missing required environment variable: QWEN_API_KEY')
    return api_key


def _headers() -> Dict[str, str]:
    return {
        'Authorization': f'Bearer {_require_api_key()}',
        'HTTP-Referer': _setting('QWEN_REFERRER') or 'hongwen-mini',
        'X-Title': _setting('QWEN_TITLE') or 'hongwen-mini',
    }


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
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


def _resolve_embed_url() -> str:
    return _setting('QWEN_EMBED_URL') or DASHSCOPE_EMBED_URL


def _resolve_generation_url(candidate: str | None) -> str:
    normalized_candidate = str(candidate or '').strip()
    if not normalized_candidate:
        return DASHSCOPE_GENERATION_URL
    if '/api-ws/v1/inference' in normalized_candidate:
        return DASHSCOPE_GENERATION_URL
    if '/api/v1/services/aigc/multimodal-generation/generation' in normalized_candidate:
        return normalized_candidate
    if normalized_candidate.rstrip('/').endswith('/api/v1'):
        return normalized_candidate.rstrip('/') + '/services/aigc/multimodal-generation/generation'
    if '/compatible-mode/v1/chat/completions' in normalized_candidate:
        return normalized_candidate.replace(
            '/compatible-mode/v1/chat/completions',
            '/api/v1/services/aigc/multimodal-generation/generation',
        )
    if '/compatible-mode/v1' in normalized_candidate:
        return normalized_candidate.replace(
            '/compatible-mode/v1',
            '/api/v1/services/aigc/multimodal-generation/generation',
        )
    return normalized_candidate


def _resolve_asr_url() -> str:
    return _resolve_generation_url(_setting('QWEN_ASR_URL'))


def _encode_audio_file_as_data_url(file_path: str) -> str:
    guessed_type, _ = mimetypes.guess_type(file_path)
    mime_type = guessed_type or 'audio/mpeg'
    with open(file_path, 'rb') as audio_file:
        encoded = base64.b64encode(audio_file.read()).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'


def _build_asr_payload(*, model: str, file_path: str, language: str | None) -> Dict[str, Any]:
    asr_options: Dict[str, Any] = {'enable_itn': False}
    if language:
        asr_options['language'] = language
    return {
        'model': model,
        'input': {
            'messages': [
                {'role': 'user', 'content': [{'audio': _encode_audio_file_as_data_url(file_path)}]}
            ]
        },
        'parameters': {'result_format': 'message', 'asr_options': asr_options},
    }


def _extract_transcription_text(result: Dict[str, Any]) -> str:
    message: Dict[str, Any] = {}
    choices = result.get('choices') or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get('message') or {}
    if not message:
        output = result.get('output') or {}
        output_choices = output.get('choices') or []
        if output_choices and isinstance(output_choices[0], dict):
            message = output_choices[0].get('message') or {}

    content = message.get('content')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get('text') or item.get('content') or ''
            if text:
                parts.append(str(text))
        return ''.join(parts).strip()

    text = result.get('text')
    if isinstance(text, str):
        return text.strip()
    return ''


def _build_chat_payload(
    *, model: str, messages: List[Dict[str, Any]], temperature: float,
    tools: Optional[List[Dict[str, Any]]], tool_choice: str | Dict[str, Any] | None,
    response_format: str | Dict[str, Any] | None, stream: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {'model': model, 'messages': messages, 'temperature': temperature}
    if getattr(settings, 'QWEN_INCLUDE_REASONING', False):
        payload['include_reasoning'] = True
    if tools:
        payload['tools'] = tools
    if tool_choice is not None:
        payload['tool_choice'] = tool_choice
    if response_format is not None:
        payload['response_format'] = response_format
    if stream:
        payload['stream'] = True
        payload['incremental_output'] = True
        payload['enable_thinking'] = False
        payload.pop('include_reasoning', None)
    return payload


def chat_completion(
    *, model: str, messages: List[Dict[str, Any]], temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = _build_chat_payload(
        model=model, messages=messages, temperature=temperature,
        tools=tools, tool_choice=tool_choice, response_format=response_format,
    )
    response = _get_session().post(DASHSCOPE_CHAT_URL, headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT)
    _raise_for_status(response, action='Chat completion', model=model)
    return response.json()


def embed_text(*, model: str, texts: List[str]) -> List[List[float]]:
    payload = {'model': model, 'input': texts}
    response = _get_session().post(_resolve_embed_url(), headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT)
    _raise_for_status(response, action='Embedding', model=model)
    data = response.json()
    return [item['embedding'] for item in data['data']]


def transcribe_audio(*, model: str, file_path: str, language: str | None = None) -> str:
    payload = _build_asr_payload(model=model, file_path=file_path, language=language)
    response = _get_session().post(_resolve_asr_url(), headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT)
    _raise_for_status(response, action='Audio transcription', model=model)
    transcript = _extract_transcription_text(response.json())
    if transcript:
        return transcript
    raise RuntimeError(f"Audio transcription failed for model '{model}': empty transcript")


def stream_chat_completion(
    *, model: str, messages: List[Dict[str, Any]], temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Iterator[Dict[str, Any]]:
    payload = _build_chat_payload(
        model=model, messages=messages, temperature=temperature,
        tools=tools, tool_choice=tool_choice, response_format=response_format, stream=True,
    )
    response = _get_session().post(
        DASHSCOPE_CHAT_URL, headers=_headers(), json=payload, timeout=_DEFAULT_TIMEOUT, stream=True,
    )
    try:
        if response.status_code >= 400:
            _raise_for_status(response, action='Chat completion stream', model=model)
        for raw_line in response.iter_lines(decode_unicode=True):
            chunk = (raw_line or '').strip()
            if not chunk or not chunk.startswith('data:'):
                continue
            payload_text = chunk[len('data:'):].strip()
            if not payload_text or payload_text == '[DONE]':
                continue
            try:
                yield json.loads(payload_text)
            except json.JSONDecodeError:
                continue
    finally:
        response.close()


# --- Realtime WebSocket TTS (sync) -----------------------------------------


def _resolve_realtime_tts_url() -> str:
    return _setting('QWEN_TTS_URL') or DASHSCOPE_REALTIME_TTS_URL


def _realtime_tts_headers() -> Dict[str, str]:
    headers = {'Authorization': f'Bearer {_require_api_key()}'}
    workspace_id = _setting('QWEN_WORKSPACE_ID')
    if workspace_id:
        headers['X-DashScope-WorkSpace'] = workspace_id
    data_inspection = _setting('QWEN_DATA_INSPECTION')
    if data_inspection:
        headers['X-DashScope-DataInspection'] = data_inspection
    return headers


def _resolve_realtime_tts_audio_format() -> str:
    return _setting('QWEN_TTS_AUDIO_FORMAT') or 'mp3'


def _resolve_realtime_tts_sample_rate() -> int:
    value = _setting('QWEN_TTS_SAMPLE_RATE')
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return 24000


def _build_realtime_tts_run_task_event(*, task_id: str, model: str, voice: str) -> Dict[str, Any]:
    return {
        'header': {'action': 'run-task', 'task_id': task_id, 'streaming': 'duplex'},
        'payload': {
            'task_group': 'audio', 'task': 'tts', 'function': 'SpeechSynthesizer', 'model': model,
            'parameters': {
                'text_type': 'PlainText', 'voice': voice,
                'format': _resolve_realtime_tts_audio_format(),
                'sample_rate': _resolve_realtime_tts_sample_rate(),
            },
            'input': {},
        },
    }


def _build_realtime_tts_continue_event(*, task_id: str, text: str) -> Dict[str, Any]:
    return {
        'header': {'action': 'continue-task', 'task_id': task_id, 'streaming': 'duplex'},
        'payload': {'input': {'text': text}},
    }


def _build_realtime_tts_finish_event(*, task_id: str) -> Dict[str, Any]:
    return {
        'header': {'action': 'finish-task', 'task_id': task_id, 'streaming': 'duplex'},
        'payload': {'input': {}},
    }


def _iter_text_chunks(text: str, *, limit: int = 20000) -> List[str]:
    normalized = str(text or '')
    if not normalized:
        return []
    return [normalized[index:index + limit] for index in range(0, len(normalized), limit)]


def _parse_realtime_tts_event(message: str, *, model: str) -> Dict[str, Any]:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Speech synthesis stream failed for model '{model}': invalid JSON event") from exc
    header = payload.get('header') or {}
    event = str(header.get('event') or '').strip()
    if event == 'task-failed':
        error_code = str(header.get('error_code') or '').strip()
        error_message = str(header.get('error_message') or '').strip()
        detail = error_message or error_code or 'unknown error'
        raise RuntimeError(f"Speech synthesis failed for model '{model}': {detail}")
    return payload


def _wait_for_realtime_tts_event_sync(websocket: Any, *, model: str, expected_event: str) -> Dict[str, Any]:
    while True:
        message = websocket.recv(timeout=30)
        if isinstance(message, bytes):
            continue
        payload = _parse_realtime_tts_event(message, model=model)
        header = payload.get('header') or {}
        if str(header.get('event') or '').strip() == expected_event:
            return payload


def _stream_speech_audio_sync(*, model: str, text: str, voice: str, language_type: str | None = None) -> Iterator[bytes]:
    normalized_text = str(text or '').strip()
    if not normalized_text:
        raise RuntimeError('Speech synthesis failed: empty text')
    normalized_voice = str(voice or '').strip()
    if not normalized_voice:
        raise RuntimeError('Speech synthesis failed: missing voice')
    if not model.strip().lower().startswith('cosyvoice'):
        raise RuntimeError(f"Speech synthesis is not implemented for model '{model}'")

    task_id = str(uuid.uuid4())
    run_task_payload = _build_realtime_tts_run_task_event(task_id=task_id, model=model, voice=normalized_voice)
    chunk_list = _iter_text_chunks(normalized_text)
    if not chunk_list:
        raise RuntimeError('Speech synthesis failed: empty text')

    try:
        with sync_websocket_connect(
            _resolve_realtime_tts_url(),
            additional_headers=_realtime_tts_headers(),
            user_agent_header='hongwen-mini/tts-sync',
            open_timeout=20,
            max_size=None,
        ) as websocket:
            websocket.send(json.dumps(run_task_payload))
            _wait_for_realtime_tts_event_sync(websocket, model=model, expected_event='task-started')
            for text_chunk in chunk_list:
                websocket.send(json.dumps(_build_realtime_tts_continue_event(task_id=task_id, text=text_chunk)))
            websocket.send(json.dumps(_build_realtime_tts_finish_event(task_id=task_id)))
            while True:
                message = websocket.recv(timeout=30)
                if isinstance(message, bytes):
                    if message:
                        yield message
                    continue
                payload = _parse_realtime_tts_event(message, model=model)
                header = payload.get('header') or {}
                if str(header.get('event') or '').strip() == 'task-finished':
                    return
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"Speech synthesis failed for model '{model}': {exc}") from exc


def stream_speech_audio(*, model: str, text: str, voice: str, language_type: str | None = None) -> Iterator[bytes]:
    yield from _stream_speech_audio_sync(model=model, text=text, voice=voice, language_type=language_type)


def synthesize_speech(*, model: str, text: str, voice: str, language_type: str | None = None) -> Dict[str, Any]:
    """Synthesize to a private-storage mp3; return audio_url + storage name."""
    audio = b''.join(
        chunk for chunk in _stream_speech_audio_sync(model=model, text=text, voice=voice, language_type=language_type)
        if chunk
    )
    if not audio:
        raise RuntimeError(f"Speech synthesis failed for model '{model}': empty audio stream")

    final_name = f'tts_{uuid.uuid4().hex}.mp3'
    stored_name = private_storage().save(f'hongwen/tts/{final_name}', ContentFile(audio))
    relative_path = stored_name[len('hongwen/'):] if stored_name.startswith('hongwen/') else stored_name
    return {
        'audio_url': build_protected_upload_url(relative_path),
        'stored_path': stored_name,
        'provider_audio_url': '',
        'request_id': '',
    }