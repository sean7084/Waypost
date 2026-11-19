"""WeChat content moderation (msgSecCheck v2 / imgSecCheck) for the UGC mini program.

Port of helpdesk/backend/app/utils/moderation.py. Flags come from Django settings
(``MODERATION_*``); credentials via ``wechat_token`` (wenqu appid). Policy is
unchanged: only an explicit ``risky`` blocks by default, infrastructure faults
fail open (MODERATION_FAIL_OPEN=false flips that), and ``review`` passes with a
trail because a support bot has no review queue.
"""
from __future__ import annotations

import io
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

from wenqu.services.utils import wechat_token

logger = logging.getLogger(__name__)

MSG_SEC_CHECK_URL = 'https://api.weixin.qq.com/wxa/msg_sec_check'
IMG_SEC_CHECK_URL = 'https://api.weixin.qq.com/wxa/img_sec_check'

# Documented limits of the two synchronous endpoints; exceeding either makes
# WeChat return an infrastructure error rather than a verdict.
TEXT_CHUNK_LIMIT = 2500
IMAGE_MAX_BYTES = 1024 * 1024
IMAGE_MAX_WIDTH = 750
IMAGE_MAX_HEIGHT = 1334

# imgSecCheck reports a violating picture as an error code; no result.suggest field.
IMG_ERRCODE_RISKY = 87014

# First try the cached token, then one retry after forcing a refresh.
_ATTEMPTS = (0, 1)


class Suggest(str, Enum):
    PASS = 'pass'
    REVIEW = 'review'
    RISKY = 'risky'
    SKIPPED = 'skipped'
    ERROR = 'error'


# The chunk that says the most about the whole reply wins, so an outage on one
# chunk cannot be masked by a later chunk passing.
_VERDICT_RANK = {
    Suggest.PASS: 0,
    Suggest.SKIPPED: 1,
    Suggest.REVIEW: 2,
    Suggest.ERROR: 3,
    Suggest.RISKY: 4,
}


def _flag(name: str, *, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    raw = str(value or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}


def is_enabled() -> bool:
    return _flag('MODERATION_ENABLED', default=True)


def _scene() -> int:
    raw = str(getattr(settings, 'MODERATION_SCENE', '') or '').strip()
    if not raw:
        return 2
    try:
        # scene 1 profile / 2 comment / 3 forum / 4 social log; 2 fits support chat.
        return int(raw)
    except ValueError:
        return 2


def _timeout_seconds() -> float:
    raw = str(getattr(settings, 'MODERATION_TIMEOUT_SECONDS', '') or '').strip()
    try:
        return max(float(raw or '5'), 1.0)
    except ValueError:
        return 5.0


def should_block(suggest: Suggest) -> bool:
    """Only an explicit ``risky`` blocks by default; ERROR stays fail-open."""
    if suggest is Suggest.RISKY:
        return True
    if suggest is Suggest.ERROR:
        return not _flag('MODERATION_FAIL_OPEN', default=True)
    return False


def _post_json(
    url: str,
    *,
    token: str,
    json_body: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The single place this module touches the network, so tests can stub it wholesale."""
    response = requests.post(
        url,
        params={'access_token': token},
        json=json_body,
        files=files,
        timeout=_timeout_seconds(),
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f'WeChat check reply was not JSON: {exc}') from exc
    return payload if isinstance(payload, dict) else {'errcode': -1, 'errmsg': str(payload)}


def _split_text(text: str) -> List[str]:
    chunks: List[str] = []
    remaining = text
    while remaining:
        chunks.append(remaining[:TEXT_CHUNK_LIMIT])
        remaining = remaining[TEXT_CHUNK_LIMIT:]
    return chunks


def check_text(text: Optional[str], openid: Optional[str]) -> Suggest:
    """Run msgSecCheck v2 over user or assistant text."""
    body = (text or '').strip()
    if not body:
        return Suggest.SKIPPED
    if not is_enabled():
        return Suggest.SKIPPED
    if not wechat_token.has_credentials():
        return Suggest.SKIPPED
    if not openid:
        # msgSecCheck v2 requires an openid; dashboard accounts never have one.
        logger.info('Content moderation skipped: user has no WeChat openid')
        return Suggest.SKIPPED

    token = _access_token_or_none()
    if token is None:
        return Suggest.ERROR

    worst = Suggest.PASS
    for chunk in _split_text(body):
        suggest = _check_text_chunk(chunk, openid, token)
        if _VERDICT_RANK[suggest] >= _VERDICT_RANK[worst]:
            worst = suggest
            if suggest is Suggest.RISKY:
                break
    return worst


def _check_text_chunk(chunk: str, openid: str, token: Optional[str]) -> Suggest:
    payload_body = {
        'content': chunk,
        'version': 2,
        'scene': _scene(),
        'openid': openid,
    }
    access_token = token
    for attempt in _ATTEMPTS:
        if access_token is None:
            return Suggest.ERROR
        try:
            payload = _post_json(MSG_SEC_CHECK_URL, token=access_token, json_body=payload_body)
        except Exception as exc:
            logger.warning('msgSecCheck request failed: %s', exc)
            return Suggest.ERROR

        errcode = payload.get('errcode')
        if errcode == 0:
            return _suggest_of(payload)
        if wechat_token.is_refreshable_error(errcode) and attempt == 0:
            access_token = _force_refreshed_token()
            continue
        logger.warning('msgSecCheck returned errcode %s: %s', errcode, payload.get('errmsg'))
        return Suggest.ERROR
    return Suggest.ERROR


def _suggest_of(payload: Dict[str, Any]) -> Suggest:
    raw = str((payload.get('result') or {}).get('suggest') or '').strip().lower()
    if raw == Suggest.RISKY.value:
        return Suggest.RISKY
    if raw == Suggest.REVIEW.value:
        logger.warning('msgSecCheck suggested review, label=%s', (payload.get('result') or {}).get('label'))
        return Suggest.REVIEW
    if raw == Suggest.PASS.value:
        return Suggest.PASS
    logger.warning('Unexpected msgSecCheck result: %s', payload.get('result'))
    return Suggest.ERROR


def check_image(data: bytes, mime_type: Optional[str]) -> Suggest:
    """Run the synchronous imgSecCheck over an uploaded picture."""
    if not data:
        return Suggest.SKIPPED
    if not is_enabled() or not wechat_token.has_credentials():
        return Suggest.SKIPPED

    token = _access_token_or_none()
    if token is None:
        return Suggest.ERROR

    try:
        payload_bytes, payload_mime = _prepare_image(data, mime_type)
    except Exception as exc:
        logger.warning('Could not prepare image for moderation: %s', exc)
        return Suggest.ERROR

    access_token: Optional[str] = token
    for attempt in _ATTEMPTS:
        if access_token is None:
            return Suggest.ERROR
        try:
            payload = _post_json(
                IMG_SEC_CHECK_URL,
                token=access_token,
                files={'media': ('upload', payload_bytes, payload_mime)},
            )
        except Exception as exc:
            logger.warning('imgSecCheck request failed: %s', exc)
            return Suggest.ERROR

        errcode = payload.get('errcode')
        if errcode == 0:
            return Suggest.PASS
        if errcode == IMG_ERRCODE_RISKY:
            return Suggest.RISKY
        if wechat_token.is_refreshable_error(errcode) and attempt == 0:
            access_token = _force_refreshed_token()
            continue
        logger.warning('imgSecCheck returned errcode %s: %s', errcode, payload.get('errmsg'))
        return Suggest.ERROR
    return Suggest.ERROR


def _prepare_image(data: bytes, mime_type: Optional[str]) -> Tuple[bytes, str]:
    """Fit a picture inside imgSecCheck's 1MB / 750x1334 envelope.

    Phone photos routinely exceed both, and the endpoint answers an oversized
    upload with a hard error instead of a verdict, so pass-through would quietly
    disable image moderation.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    width, height = image.size
    within_bounds = (
        len(data) <= IMAGE_MAX_BYTES
        and width <= IMAGE_MAX_WIDTH
        and height <= IMAGE_MAX_HEIGHT
        and str(mime_type or '').lower() in {'image/png', 'image/jpeg', 'image/jpg', 'image/gif'}
    )
    if within_bounds:
        return data, str(mime_type or 'image/jpeg')

    # The endpoint only accepts these four types; anything else is re-encoded.
    fmt = 'GIF' if image.format == 'GIF' else 'JPEG'
    if fmt == 'JPEG' and image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')

    scale = min(IMAGE_MAX_WIDTH / max(width, 1), IMAGE_MAX_HEIGHT / max(height, 1), 1.0)
    encoded = b''
    for _ in range(6):
        target = image if scale >= 1 else image.resize(
            (max(int(width * scale), 1), max(int(height * scale), 1)), Image.LANCZOS
        )
        buffer = io.BytesIO()
        save_kwargs = {'quality': 85} if fmt == 'JPEG' else {}
        target.save(buffer, format=fmt, **save_kwargs)
        encoded = buffer.getvalue()
        if len(encoded) <= IMAGE_MAX_BYTES:
            return encoded, 'image/gif' if fmt == 'GIF' else 'image/jpeg'
        scale *= 0.7

    raise ValueError(f'Could not compress image below {IMAGE_MAX_BYTES} bytes')


def _access_token_or_none() -> Optional[str]:
    try:
        return wechat_token.get_access_token()
    except Exception as exc:
        logger.warning('Could not obtain WeChat access_token for moderation: %s', exc)
        return None


def _force_refreshed_token() -> Optional[str]:
    try:
        return wechat_token.get_access_token(force_refresh=True)
    except Exception as exc:
        logger.warning('Could not refresh WeChat access_token: %s', exc)
        return None