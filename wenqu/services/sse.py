"""Server-sent events for Wenqu chat streaming on sync WSGI (PR4).

Port of helpdesk/backend/app/main.py:2659-2749 / :3084-3178. The helpdesk ran
async generators on uvicorn and polled ``request.is_disconnected()``; Waypost
runs gunicorn gthread workers, so the generators here are plain synchronous
iterators consumed by ``StreamingHttpResponse``. Frame format is unchanged:

    event: <name>\\ndata: <json>\\n\\n

with ``Cache-Control: no-cache`` and ``X-Accel-Buffering: no`` so nginx forwards
each frame as it is produced (docker/nginx/waypost.conf disables proxy_buffering
for these locations).

Disconnect handling: WSGI has no is_disconnected hook. When the client goes
away the server stops iterating and closes the generator, which raises
``GeneratorExit`` at the suspended ``yield`` (some servers throw
``BrokenPipeError`` / ``ConnectionError`` instead). The ``finally`` block
persists whatever text was already generated so a dropped connection still
leaves a usable partial reply in the history, then the generator terminates
quietly - no error frame is attempted on a dead socket.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterator, Optional

from django.http import StreamingHttpResponse

from wenqu.services.chat import moderate_assistant_reply, persist_assistant_message
from wenqu.services.llm.orchestrator import (
    ReplyPreparation,
    finalize_reply_payload,
    stream_reply_deltas,
)

logger = logging.getLogger(__name__)

SSE_CONTENT_TYPE = 'text/event-stream'


def sse_event(event: str, data: Dict[str, Any]) -> str:
    """One SSE frame (main.py:2816). ensure_ascii=False keeps Chinese readable."""
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def sse_response(generator: Iterator[str]) -> StreamingHttpResponse:
    response = StreamingHttpResponse(generator, content_type=SSE_CONTENT_TYPE)
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


def fixed_reply_stream(*, session_id: str, result: Dict[str, Any], start_meta: Dict[str, Any]) -> Iterator[str]:
    """start/final/done for deterministic replies (identification, triage).

    The reply is already persisted by the caller; no deltas are emitted because
    there is no model token stream to relay (main.py:3109 / :3147).
    """
    yield sse_event('start', {'session_id': session_id, 'meta': start_meta})
    yield sse_event('final', result)
    yield sse_event('done', {'session_id': session_id})


def chat_event_stream(
    *,
    user,
    session_id: str,
    preparation: ReplyPreparation,
    start_meta: Dict[str, Any],
    regenerate: bool = False,
    temperature: float = 0.2,
    final_meta_overrides: Optional[Dict[str, Any]] = None,
) -> Iterator[str]:
    """The full streaming lifecycle: start -> delta* -> final -> done.

    Output moderation happens after the deltas have gone out (the typing effect
    is deliberate), so a violating reply only replaces the authoritative
    ``final`` frame and the stored message (main.py:2709). Streaming never
    updates the learning profile - parity with the helpdesk.
    """
    accumulated: list[str] = []
    model_used = preparation.primary_model
    completed = False
    try:
        yield sse_event('start', {'session_id': session_id, 'meta': start_meta})

        for delta_text, streamed_model in stream_reply_deltas(
            preparation=preparation, regenerate=regenerate, temperature=temperature,
        ):
            model_used = streamed_model or model_used
            accumulated.append(delta_text)
            yield sse_event('delta', {'text': delta_text})

        reply_text, meta, _updates = finalize_reply_payload(
            raw_content=''.join(accumulated),
            preparation=preparation,
            include_profile_updates=False,
            model_used=model_used,
        )
        if final_meta_overrides:
            meta = {**meta, **final_meta_overrides}
        reply_text = moderate_assistant_reply(user, reply_text, meta)
        persist_assistant_message(user, session_id=session_id, reply_text=reply_text, meta=meta)
        completed = True

        yield sse_event('final', {'reply': reply_text, 'session_id': session_id, 'meta': meta})
        yield sse_event('done', {'session_id': session_id})
    except (BrokenPipeError, ConnectionError):
        # The socket died mid-write; stop quietly. The finally block keeps the
        # partial reply. Re-raising would only surface as a worker traceback.
        logger.info('Chat stream client disconnected (session %s)', session_id)
        return
    except Exception as exc:
        logger.exception('Streamed chat failed (session %s)', session_id)
        yield sse_event('error', {'detail': str(exc), 'session_id': session_id})
    finally:
        if not completed:
            partial = ''.join(accumulated).strip()
            if partial:
                try:
                    meta = dict(preparation.base_meta)
                    meta['model_used'] = model_used
                    meta['partial'] = True
                    persist_assistant_message(user, session_id=session_id, reply_text=partial, meta=meta)
                except Exception:
                    logger.exception('Could not persist partial streamed reply (session %s)', session_id)