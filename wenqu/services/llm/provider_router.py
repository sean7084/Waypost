"""Provider routing and failover for the Wenqu LLM services.

Port of helpdesk/backend/app/llm/provider_router.py, made fully synchronous
(requests + websockets.sync). Failover semantics are preserved exactly:

* chat / TTS: try the primary model, fall back to the backup on failure (unless
  both refs are identical, in which case the error propagates);
* streaming chat / TTS: once any chunk has been yielded, a later failure is NOT
  retried on the backup - the client already has partial output, so switching
  mid-stream would duplicate or corrupt it (helpdesk provider_router.py:254).

The async ``*_async`` variants are gone; the streaming helpers are now sync
generators. TTS no longer takes an ``output_dir``: audio is written through
private storage by the clients.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

from wenqu.services.llm import dashscope_client, volcengine_client
from wenqu.services.llm.model_selector import ProviderModelRef, parse_model_ref

logger = logging.getLogger(__name__)


def _dispatch_chat_completion(
    *, model_ref: ProviderModelRef, messages: List[Dict[str, Any]], temperature: float,
    tools: Optional[List[Dict[str, Any]]], tool_choice: str | Dict[str, Any] | None,
    response_format: str | Dict[str, Any] | None,
) -> Dict[str, Any]:
    if model_ref.provider == 'dashscope':
        return dashscope_client.chat_completion(
            model=model_ref.model_id, messages=messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        )
    if model_ref.provider == 'volcengine':
        return volcengine_client.chat_completion(
            model=model_ref.model_id, messages=messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        )
    raise RuntimeError(f"Unsupported chat provider '{model_ref.provider}'")


def _dispatch_stream_chat_completion(
    *, model_ref: ProviderModelRef, messages: List[Dict[str, Any]], temperature: float,
    tools: Optional[List[Dict[str, Any]]], tool_choice: str | Dict[str, Any] | None,
    response_format: str | Dict[str, Any] | None,
) -> Iterator[Dict[str, Any]]:
    if model_ref.provider == 'dashscope':
        yield from dashscope_client.stream_chat_completion(
            model=model_ref.model_id, messages=messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        )
        return
    if model_ref.provider == 'volcengine':
        yield from volcengine_client.stream_chat_completion(
            model=model_ref.model_id, messages=messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        )
        return
    raise RuntimeError(f"Unsupported chat provider '{model_ref.provider}'")


def _dispatch_synthesize_speech(
    *, model_ref: ProviderModelRef, text: str, voice: str, language_type: str | None,
) -> Dict[str, Any]:
    if model_ref.provider == 'dashscope':
        return dashscope_client.synthesize_speech(
            model=model_ref.model_id, text=text, voice=voice, language_type=language_type,
        )
    if model_ref.provider == 'volcengine':
        return volcengine_client.synthesize_speech(
            model=model_ref.model_id, text=text, voice=voice, language_type=language_type,
        )
    raise RuntimeError(f"TTS provider '{model_ref.provider}' is not implemented yet")


def _dispatch_stream_speech_audio(
    *, model_ref: ProviderModelRef, text: str, voice: str, language_type: str | None,
) -> Iterator[bytes]:
    if model_ref.provider == 'dashscope':
        yield from dashscope_client.stream_speech_audio(
            model=model_ref.model_id, text=text, voice=voice, language_type=language_type,
        )
        return
    if model_ref.provider == 'volcengine':
        yield from volcengine_client.stream_speech_audio(
            model=model_ref.model_id, text=text, voice=voice, language_type=language_type,
        )
        return
    raise RuntimeError(f"TTS provider '{model_ref.provider}' is not implemented yet")


def shutdown_provider_clients() -> None:
    """Close the pooled requests sessions (no event loop / lifespan needed)."""
    dashscope_client.close_client()
    volcengine_client.close_client()


def chat_completion(
    *, model: str, messages: List[Dict[str, Any]], temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    model_ref = parse_model_ref(model)
    response = _dispatch_chat_completion(
        model_ref=model_ref, messages=messages, temperature=temperature,
        tools=tools, tool_choice=tool_choice, response_format=response_format,
    )
    response.setdefault('_model_used', model_ref.qualified_name)
    return response


def chat_completion_with_fallback(
    *, primary_model: str, fallback_model: str, messages: List[Dict[str, Any]],
    fallback_messages: Optional[List[Dict[str, Any]]] = None, temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    primary_ref = parse_model_ref(primary_model)
    fallback_ref = parse_model_ref(fallback_model)
    try:
        response = _dispatch_chat_completion(
            model_ref=primary_ref, messages=messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        )
        response['_model_used'] = primary_ref.qualified_name
        return response
    except Exception:
        if fallback_ref.qualified_name == primary_ref.qualified_name:
            raise
        response = _dispatch_chat_completion(
            model_ref=fallback_ref, messages=fallback_messages or messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        )
        response['_model_used'] = fallback_ref.qualified_name
        return response


def stream_chat_completion_with_fallback(
    *, primary_model: str, fallback_model: str, messages: List[Dict[str, Any]],
    fallback_messages: Optional[List[Dict[str, Any]]] = None, temperature: float = 0.2,
    tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str | Dict[str, Any] | None = None,
    response_format: str | Dict[str, Any] | None = None,
) -> Iterator[Dict[str, Any]]:
    primary_ref = parse_model_ref(primary_model)
    fallback_ref = parse_model_ref(fallback_model)
    yielded_any = False

    try:
        for chunk in _dispatch_stream_chat_completion(
            model_ref=primary_ref, messages=messages, temperature=temperature,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
        ):
            yielded_any = True
            chunk.setdefault('_model_used', primary_ref.qualified_name)
            yield chunk
        return
    except Exception:
        # Already-yielded chunks must not be retried on the backup mid-stream.
        if yielded_any or fallback_ref.qualified_name == primary_ref.qualified_name:
            raise

    for chunk in _dispatch_stream_chat_completion(
        model_ref=fallback_ref, messages=fallback_messages or messages, temperature=temperature,
        tools=tools, tool_choice=tool_choice, response_format=response_format,
    ):
        chunk.setdefault('_model_used', fallback_ref.qualified_name)
        yield chunk


def embed_text(*, model: str, texts: List[str]) -> List[List[float]]:
    model_ref = parse_model_ref(model)
    if model_ref.provider == 'dashscope':
        return dashscope_client.embed_text(model=model_ref.model_id, texts=texts)
    if model_ref.provider == 'volcengine':
        return volcengine_client.embed_text(model=model_ref.model_id, texts=texts)
    raise RuntimeError(f"Embedding provider '{model_ref.provider}' is not implemented yet")


def transcribe_audio(
    *, model: str, file_path: str, language: str | None = None, public_file_url: str | None = None,
) -> str:
    model_ref = parse_model_ref(model)
    if model_ref.provider == 'dashscope':
        return dashscope_client.transcribe_audio(model=model_ref.model_id, file_path=file_path, language=language)
    if model_ref.provider == 'volcengine':
        return volcengine_client.transcribe_audio(
            model=model_ref.model_id, file_path=file_path,
            public_file_url=str(public_file_url or ''), language=language,
        )
    raise RuntimeError(f"ASR provider '{model_ref.provider}' is not implemented yet")


def transcribe_audio_with_fallback(
    *, primary_model: str, fallback_model: str, file_path: str,
    language: str | None = None, public_file_url: str | None = None,
) -> Dict[str, str]:
    """Try primary then backup ASR; return {'text', 'model_used'}.

    Mirrors the helpdesk /api/transcribe candidate loop. ``public_file_url`` is
    prepared by the caller (PR4) for Volcengine file ASR; DashScope ignores it.
    """
    primary_ref = parse_model_ref(primary_model)
    fallback_ref = parse_model_ref(fallback_model)
    candidates = [primary_ref]
    if fallback_ref.qualified_name != primary_ref.qualified_name:
        candidates.append(fallback_ref)

    last_error: Exception | None = None
    for index, model_ref in enumerate(candidates):
        try:
            text = transcribe_audio(
                model=model_ref.qualified_name, file_path=file_path,
                language=language, public_file_url=public_file_url,
            ).strip()
            return {'text': text, 'model_used': model_ref.qualified_name}
        except Exception as exc:
            last_error = exc
            logger.warning('Audio transcription attempt failed for %s: %s', model_ref.qualified_name, exc)
            if index == len(candidates) - 1:
                raise
    raise RuntimeError(f'Audio transcription failed: {last_error}')


def synthesize_speech(
    *, model: str, text: str, voice: str, language_type: str | None = None,
) -> Dict[str, Any]:
    model_ref = parse_model_ref(model)
    result = _dispatch_synthesize_speech(model_ref=model_ref, text=text, voice=voice, language_type=language_type)
    result.setdefault('_model_used', model_ref.qualified_name)
    result.setdefault('_voice_used', voice)
    return result


def synthesize_speech_with_fallback(
    *, primary_model: str, fallback_model: str, text: str, primary_voice: str,
    fallback_voice: str | None, language_type: str | None = None,
) -> Dict[str, Any]:
    primary_ref = parse_model_ref(primary_model)
    fallback_ref = parse_model_ref(fallback_model)
    try:
        result = _dispatch_synthesize_speech(model_ref=primary_ref, text=text, voice=primary_voice, language_type=language_type)
        result.setdefault('_model_used', primary_ref.qualified_name)
        result.setdefault('_voice_used', primary_voice)
        return result
    except Exception:
        if fallback_ref.qualified_name == primary_ref.qualified_name:
            raise

    resolved_fallback_voice = str(fallback_voice or primary_voice)
    result = _dispatch_synthesize_speech(model_ref=fallback_ref, text=text, voice=resolved_fallback_voice, language_type=language_type)
    result.setdefault('_model_used', fallback_ref.qualified_name)
    result.setdefault('_voice_used', resolved_fallback_voice)
    return result


def stream_speech_audio(*, model: str, text: str, voice: str, language_type: str | None = None) -> Iterator[bytes]:
    model_ref = parse_model_ref(model)
    yield from _dispatch_stream_speech_audio(model_ref=model_ref, text=text, voice=voice, language_type=language_type)


def stream_speech_audio_with_fallback(
    *, primary_model: str, fallback_model: str, text: str, primary_voice: str,
    fallback_voice: str | None, language_type: str | None = None,
) -> Iterator[bytes]:
    primary_ref = parse_model_ref(primary_model)
    fallback_ref = parse_model_ref(fallback_model)
    yielded_any = False

    try:
        for chunk in _dispatch_stream_speech_audio(model_ref=primary_ref, text=text, voice=primary_voice, language_type=language_type):
            if chunk:
                yielded_any = True
            yield chunk
        return
    except Exception:
        if yielded_any or fallback_ref.qualified_name == primary_ref.qualified_name:
            raise

    resolved_fallback_voice = str(fallback_voice or primary_voice)
    for chunk in _dispatch_stream_speech_audio(model_ref=fallback_ref, text=text, voice=resolved_fallback_voice, language_type=language_type):
        yield chunk