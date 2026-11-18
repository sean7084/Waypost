"""WeChat customer authentication for the Wenqu mini program.

Semantics ported from helpdesk/backend/app/main.py ``wx_login`` / ``update_wx_user``
and app/auth.py's ``tv`` (token_version) mechanism, adapted to Waypost's split
identity model:

* openid lookup goes through ``accounts.WeChatIdentity`` keyed by the *wenqu*
  appid (``settings.WECHAT_WENQU_APPID``), never the staff mini-program appid.
* a brand-new openid auto-registers an ``accounts.User`` with an unusable
  password, a ``WenquProfile`` and the ``WeChatIdentity`` row, all atomically.
* every minted access token carries ``tv = profile.token_version``; bumping the
  column invalidates older tokens (enforced by api.wenqu_authentication).

The view layer (api.wenqu_auth_views) translates the domain exceptions below
into HTTP status codes; this module never imports DRF responses.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User, WeChatIdentity
from accounts.wechat import code2session
from wenqu.models import WenquProfile

logger = logging.getLogger(__name__)


class WenquAuthConflict(Exception):
    """The openid is already bound to a different user (HTTP 409 on login)."""


class WenquAccountDisabled(Exception):
    """The customer account exists but has been deactivated (HTTP 403)."""


class WenquOpenIDMismatch(Exception):
    """update_wx_user was given an openid that is not this customer's (HTTP 403)."""


def _tokens_for_profile(profile):
    """Mint a SimpleJWT pair whose access token is pinned to the current tv.

    ``RefreshToken.access_token`` is a plain property that builds a *fresh*
    AccessToken on every access, so the claim must be set on a captured instance
    (setting it on ``refresh.access_token`` inline would mutate a throwaway).
    """
    refresh = RefreshToken.for_user(profile.user)
    access = refresh.access_token
    access['tv'] = profile.token_version
    return str(access), str(refresh)


def _user_payload(profile):
    return {
        'user_id': str(profile.user_id),
        'external_id': profile.external_id,
        'nickname': profile.nickname,
        'avatar': profile.avatar,
    }


def _login_response(profile, status):
    access, refresh = _tokens_for_profile(profile)
    return {
        'status': status,
        'auth_token': access,
        'refresh': refresh,
        'token_type': 'Bearer',
        'user': _user_payload(profile),
    }


def _external_id_for(openid):
    """``wx_<openid[:12]>``, disambiguated if a different user already owns it.

    Two openids can share their first 12 characters; without a suffix the second
    customer would hit the username unique constraint and get a 500. The base
    rule is preserved for the common case.
    """
    base = f'wx_{openid[:12]}'
    candidate = base
    suffix = 2
    while User.objects.filter(username=candidate).exists():
        candidate = f'{base}_{suffix}'
        suffix += 1
    return candidate


def wx_login(code):
    """Exchange a wx.login code for a customer session, registering on first use.

    Returns the login payload (``status`` is ``'ok'`` or ``'new_user'``).
    Raises ``accounts.wechat.WeChatAuthError`` on a bad code / WeChat outage,
    ``WenquAuthConflict`` when the openid belongs to a non-customer user, and
    ``WenquAccountDisabled`` when the customer is deactivated.
    """
    appid = settings.WECHAT_WENQU_APPID
    secret = settings.WECHAT_WENQU_APPSECRET
    session = code2session(code, appid=appid, secret=secret)
    openid = session['openid']

    identity = (
        WeChatIdentity.objects.filter(appid=appid, openid=openid)
        .select_related('user', 'user__wenqu_profile')
        .first()
    )
    if identity is not None:
        user = identity.user
        profile = getattr(user, 'wenqu_profile', None)
        if profile is None:
            # Bound to a staff/other account under the wenqu appid: refuse rather
            # than silently hand a customer token to a non-customer principal.
            raise WenquAuthConflict(_('This WeChat account is already bound to another user.'))
        if not user.is_active:
            raise WenquAccountDisabled(_('Account is disabled.'))
        unionid = session.get('unionid', '')
        if unionid and identity.unionid != unionid:
            identity.unionid = unionid
            identity.save(update_fields=['unionid', 'updated_at'])
        return _login_response(profile, 'ok')

    with transaction.atomic():
        external_id = _external_id_for(openid)
        user = User(username=external_id)
        user.set_unusable_password()
        user.save()
        profile = WenquProfile.objects.create(user=user, external_id=external_id)
        WeChatIdentity.objects.create(
            user=user, appid=appid, openid=openid, unionid=session.get('unionid', ''),
        )
    logger.info('Wenqu customer registered: %s', external_id)
    return _login_response(profile, 'new_user')


def update_wx_user(user, payload):
    """Update nickname/avatar/mobile from the mini-program userInfo, re-mint tokens.

    ``user`` is already authenticated and guaranteed to have a wenqu_profile by
    WenquCustomerPermission. An optional ``openid`` in the payload must match the
    caller's bound identity (mirrors helpdesk's 403 guard).
    """
    profile = user.wenqu_profile
    openid = payload.get('openid')
    if openid:
        identity = WeChatIdentity.objects.filter(
            user=user, appid=settings.WECHAT_WENQU_APPID,
        ).first()
        if identity is None or identity.openid != openid:
            raise WenquOpenIDMismatch(_('openid does not match the authenticated customer.'))

    user_info = payload.get('userInfo') or {}
    nickname = user_info.get('nickName')
    avatar = user_info.get('avatarUrl')
    mobile = user_info.get('phoneNumber')
    updated = []
    if nickname:
        profile.nickname = nickname
        updated.append('nickname')
    if avatar:
        profile.avatar = avatar
        updated.append('avatar')
    if mobile:
        profile.mobile = mobile
        updated.append('mobile')
    if updated:
        profile.save(update_fields=updated + ['updated_at'])
    return _login_response(profile, 'ok')