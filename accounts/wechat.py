"""
WeChat mini-program login helpers.

Exchanges a client-side `wx.login()` code for an openid via WeChat's
`jscode2session` endpoint. The AppSecret is read from settings (server-side only)
and the returned `session_key` is transient - it must never be persisted.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

JSCODE2SESSION_URL = 'https://api.weixin.qq.com/sns/jscode2session'


class WeChatAuthError(Exception):
    """Raised when WeChat cannot exchange a login code for an openid."""

    def __init__(self, message, errcode=None, errmsg=None):
        super().__init__(message)
        self.errcode = errcode
        self.errmsg = errmsg


def code2session(js_code, timeout=10, appid=None, secret=None):
    """Exchange a wx.login() code for openid/unionid/session_key.

    Returns a dict: {'openid', 'unionid', 'session_key'}.
    Raises WeChatAuthError on missing config, network failure, or a WeChat
    API error (e.g. 40029 invalid code, 40163 code been used).

    ``appid``/``secret`` default to the staff inspection mini program
    (settings.WECHAT_MINI_APPID/SECRET). The Hongwen customer mini program passes
    its own credentials (settings.WECHAT_HONGWEN_APPID/SECRET) so the two clients
    never share an openid scope.
    """
    if appid is None:
        appid = settings.WECHAT_MINI_APPID
    if secret is None:
        secret = settings.WECHAT_MINI_APPSECRET
    if not appid or not secret:
        raise WeChatAuthError('WeChat mini-program AppID/AppSecret are not configured.')
    if not js_code:
        raise WeChatAuthError('Missing wx.login code.')

    params = {
        'appid': appid,
        'secret': secret,
        'js_code': js_code,
        'grant_type': 'authorization_code',
    }
    try:
        response = requests.get(JSCODE2SESSION_URL, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.warning('WeChat jscode2session request failed: %s', exc)
        raise WeChatAuthError('Unable to reach the WeChat login service.') from exc

    if 'openid' not in data:
        errcode = data.get('errcode')
        errmsg = data.get('errmsg', '')
        logger.warning('WeChat jscode2session error: %s %s', errcode, errmsg)
        raise WeChatAuthError(
            f'WeChat login failed ({errcode}): {errmsg}', errcode=errcode, errmsg=errmsg
        )

    return {
        'openid': data['openid'],
        'unionid': data.get('unionid', ''),
        'session_key': data.get('session_key', ''),
    }
