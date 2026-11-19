"""WeChat API-wide access_token management (moderation + get-phone).

Port of helpdesk/backend/app/utils/wechat_token.py. Credentials come from the
wenqu mini-program settings (``WECHAT_WENQU_APPID`` / ``WECHAT_WENQU_APPSECRET``,
PR2) rather than the helpdesk's generic APP_ID/APP_SECRET env. The token cache
is the whole point: cgi-bin/token is quota-limited (errcode 45009) and tokens
live 7200s, so every msgSecCheck call must not re-fetch it.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TOKEN_URL = 'https://api.weixin.qq.com/cgi-bin/token'

_EXPIRY_MARGIN_SECONDS = 300.0
_REFRESH_ERROR_CODES = {40001, 40014, 42001}

_lock = threading.Lock()
_token: Optional[str] = None
_expires_at = 0.0


class WeChatTokenUnavailable(RuntimeError):
    """Credentials are configured but the token endpoint could not be reached or refused."""


def reset_cache() -> None:
    """Forget the cached token (tests use it to keep stubbed credentials from leaking)."""
    global _token, _expires_at
    with _lock:
        _token = None
        _expires_at = 0.0


def _credentials() -> tuple[str, str]:
    app_id = str(getattr(settings, 'WECHAT_WENQU_APPID', '') or '').strip()
    app_secret = str(getattr(settings, 'WECHAT_WENQU_APPSECRET', '') or '').strip()
    return app_id, app_secret


def has_credentials() -> bool:
    app_id, app_secret = _credentials()
    return bool(app_id and app_secret)


def get_access_token(*, force_refresh: bool = False) -> Optional[str]:
    """Return a valid API-wide access_token, or None when no WeChat credentials exist.

    Missing credentials are normal for local development, so this returns None
    instead of raising; callers treat that as "skip the check".
    """
    global _token, _expires_at

    app_id, app_secret = _credentials()
    if not app_id or not app_secret:
        return None

    with _lock:
        if not force_refresh and _token and time.monotonic() < _expires_at:
            return _token

        response = requests.get(
            TOKEN_URL,
            params={'grant_type': 'client_credential', 'appid': app_id, 'secret': app_secret},
            timeout=_request_timeout_seconds(),
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeChatTokenUnavailable(f'WeChat token reply was not JSON: {exc}') from exc

        token = payload.get('access_token')
        if not token:
            raise WeChatTokenUnavailable(
                f"WeChat token error {payload.get('errcode')}: {payload.get('errmsg')}"
            )

        # Floor the cached lifetime so a short expires_in cannot cause a refresh storm.
        lifetime = max(float(payload.get('expires_in') or 7200) - _EXPIRY_MARGIN_SECONDS, 60.0)
        _token = str(token)
        _expires_at = time.monotonic() + lifetime
        return _token


def is_refreshable_error(errcode) -> bool:
    """True for errcodes meaning "your access_token was rejected", not "content was bad"."""
    return errcode in _REFRESH_ERROR_CODES


def _request_timeout_seconds() -> float:
    raw = str(getattr(settings, 'MODERATION_TIMEOUT_SECONDS', '') or '').strip()
    try:
        return max(float(raw or '5'), 1.0)
    except ValueError:
        return 5.0