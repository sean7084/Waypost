"""REST endpoints for Wenqu (问渠) customer WeChat authentication.

Mounted under /api/v1/wenqu/auth/ (see api.urls). Only the auth surface ships in
PR2; chat / upload / KB / triage business endpoints arrive in PR4.

* wx_login       - unauthenticated; exchanges a wx.login code for a customer
                   session, auto-registering on first use.
* update_wx_user - customer-authenticated; refreshes nickname/avatar/mobile.
* get-phone      - customer-authenticated stub; the WeChat getuserphonenumber
                   exchange (needs the wenqu appid access_token manager) is
                   completed in PR4.
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

    def post(self, request):
        try:
            payload = update_wx_user(request.user, request.data)
        except WenquOpenIDMismatch as exc:
            return Response({'error': str(exc)}, status=403)
        return Response(payload, status=200)


class WenquGetPhoneView(APIView):
    """POST {code} -> {status, phone}. STUB: completed in PR4.

    The real flow calls WeChat ``wxa/business/getuserphonenumber`` with an
    access_token minted from the *wenqu* appid (cgi-bin/token, quota-limited and
    therefore cached). That token manager is part of the services port (PR3) and
    the endpoint is wired up in PR4; until then this returns 501 so the client
    can degrade gracefully rather than hit a 404.
    """

    authentication_classes = [WenquJWTAuthentication]
    permission_classes = [WenquCustomerPermission]

    def post(self, request):
        return Response(
            {'error': _('Phone number retrieval is not implemented yet (PR4).')},
            status=501,
        )