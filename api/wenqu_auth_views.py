"""REST endpoints for Wenqu (问渠) customer WeChat authentication.

Mounted under /api/v1/wenqu/auth/ (see api.urls).

* wx_login       - unauthenticated; exchanges a wx.login code for a customer
                   session, auto-registering on first use.
* update_wx_user - customer-authenticated; refreshes nickname/avatar/mobile.
* get-phone      - customer-authenticated; exchanges a getPhoneNumber code via
                   the wenqu appid access_token (completed in PR4).
"""
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.wechat import WeChatAuthError
from wenqu.services.auth import (
    WenquAccountDisabled,
    WenquAuthConflict,
    WenquOpenIDMismatch,
    WenquPhoneError,
    WenquPhoneUnavailable,
    get_phone_number,
    update_wx_user,
    wx_login,
)

from .wenqu_authentication import WenquCustomerPermission, WenquJWTAuthentication


class WenquWxLoginView(APIView):
    """POST {code} -> customer session (status 'ok' or 'new_user')."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        code = request.data.get('code')
        if not code:
            return Response({'error': _('code is required.')}, status=400)
        try:
            payload = wx_login(code)
        except WeChatAuthError as exc:
            return Response({'error': str(exc)}, status=400)
        except WenquAuthConflict as exc:
            return Response({'error': str(exc)}, status=409)
        except WenquAccountDisabled as exc:
            return Response({'error': str(exc)}, status=403)
        return Response(payload, status=200)


class WenquUpdateUserView(APIView):
    """POST {userInfo: {nickName, avatarUrl, phoneNumber}, openid?} -> fresh tokens."""

    authentication_classes = [WenquJWTAuthentication]
    permission_classes = [WenquCustomerPermission]
    pagination_class = None

    def post(self, request):
        try:
            payload = update_wx_user(request.user, request.data)
        except WenquOpenIDMismatch as exc:
            return Response({'error': str(exc)}, status=403)
        return Response(payload, status=200)


class WenquGetPhoneView(APIView):
    """POST {code} -> {status: 'success', phone} (helpdesk main.py:3193).

    The exchange runs on the *wenqu* appid access_token (wechat_token cache,
    cgi-bin/token is quota-limited) and persists the number on the profile.
    """

    authentication_classes = [WenquJWTAuthentication]
    permission_classes = [WenquCustomerPermission]
    pagination_class = None

    def post(self, request):
        code = request.data.get('code')
        if not code:
            return Response({'detail': _('获取手机号失败')}, status=400)
        try:
            phone = get_phone_number(request.user, code)
        except WenquPhoneUnavailable as exc:
            return Response({'detail': str(exc)}, status=500)
        except WenquPhoneError as exc:
            return Response({'detail': str(exc)}, status=400)
        return Response({'status': 'success', 'phone': phone}, status=200)