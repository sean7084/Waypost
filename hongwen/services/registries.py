"""Cache-backed short-lived registries for the Hongwen customer API (PR4).

The helpdesk kept three in-process dictionaries guarded by asyncio locks
(main.py:101-117, 953-1078): TTS stream requests, provider audio shares and the
password-reset IP throttle. A gunicorn gthread deployment runs several worker
processes, so an in-process dict would only ever be visible to the worker that
wrote it. Everything here therefore lives in ``django.core.cache`` (Redis in
production via DJANGO_REDIS_CACHE_URL, locmem in development - settings already
pick the backend), with a 15-minute TTL matching the helpdesk registries.

Entries are bounded by (write rate x TTL) exactly like the helpdesk's swept
dicts; the cache backend evicts expired keys on its own, so no sweep loop and no
blast-radius cap are needed here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# 15 minutes, the helpdesk _TTS_STREAM_REQUEST_TTL / _PROVIDER_AUDIO_SHARE_TTL.
REGISTRY_TTL_SECONDS = 900

_TTS_STREAM_PREFIX = 'hongwen:tts_stream:'
_PROVIDER_AUDIO_PREFIX = 'hongwen:provider_audio:'
_IP_SLOT_PREFIX = 'hongwen:ip:'


def _ttl() -> int:
    return int(getattr(settings, 'HONGWEN_REGISTRY_TTL_SECONDS', REGISTRY_TTL_SECONDS))


# --- TTS stream registry ------------------------------------------------------


def register_tts_stream(*, user, audio: bytes, model: str, voice: str, message_id: str = '') -> Dict[str, Any]:
    """Store synthesized mp3 bytes for later playback and return the entry.

    Unlike the helpdesk (which registered the *request* and synthesized lazily
    on GET), PR4 synthesizes eagerly in POST /speak/stream and caches the bytes:
    a sync WSGI worker cannot keep a provider websocket open across requests.
    The client contract is unchanged - POST returns {stream_url}, GET plays it.
    """
    stream_id = uuid.uuid4().hex
    entry = {
        'stream_id': stream_id,
        'external_id': user.hongwen_profile.external_id,
        'audio': audio,
        'model': model,
        'voice': voice,
        'message_id': message_id,
    }
    cache.set(_TTS_STREAM_PREFIX + stream_id, entry, _ttl())
    return entry


def load_tts_stream(stream_id: str) -> Optional[Dict[str, Any]]:
    normalized = str(stream_id or '').strip()
    if not normalized:
        return None
    entry = cache.get(_TTS_STREAM_PREFIX + normalized)
    return dict(entry) if isinstance(entry, dict) else None


# --- provider audio shares (Volcengine file ASR) ------------------------------


def register_provider_audio_share(file_path: str) -> str:
    """Expose a temp audio file to Volcengine ASR under a short-lived capability URL."""
    share_id = uuid.uuid4().hex
    cache.set(_PROVIDER_AUDIO_PREFIX + share_id, {'file_path': str(file_path or '')}, _ttl())
    return share_id


def load_provider_audio_share(share_id: str) -> Optional[Dict[str, Any]]:
    normalized = str(share_id or '').strip()
    if not normalized:
        return None
    entry = cache.get(_PROVIDER_AUDIO_PREFIX + normalized)
    return dict(entry) if isinstance(entry, dict) else None


def remove_provider_audio_share(share_id: str) -> None:
    normalized = str(share_id or '').strip()
    if normalized:
        cache.delete(_PROVIDER_AUDIO_PREFIX + normalized)


# --- IP throttle ---------------------------------------------------------------


def client_ip(request) -> str:
    """Best-effort origin address (port of main.py:1413).

    XFF is client-spoofable, so this is blast-radius protection only; the
    per-customer permission flags are the controls that actually hold.
    """
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR') or ''
    first_hop = forwarded.split(',')[0].strip()
    if first_hop:
        return first_hop
    return request.META.get('REMOTE_ADDR') or ''


def take_ip_slot(ip: str, *, scope: str, window_seconds: int) -> int:
    """Count a request against its IP and return the running total in the window.

    ``cache.add`` + ``cache.incr`` keeps record-and-decide atomic on Redis (the
    helpdesk used one lock for the same reason: a check-then-record pair lets
    concurrent requests all read the same count and sail past the limit).
    """
    if not ip:
        return 0
    key = f'{_IP_SLOT_PREFIX}{scope}:{ip}'
    if cache.add(key, 1, window_seconds):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        # The key expired between add and incr: start a fresh window.
        cache.set(key, 1, window_seconds)
        return 1


def ip_slot_exceeded(ip: str, *, scope: str, window_seconds: int, max_hits: int) -> bool:
    return take_ip_slot(ip, scope=scope, window_seconds=window_seconds) > max_hits