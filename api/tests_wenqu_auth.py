"""HTTP-level tests for the Wenqu customer auth surface and its DRF glue.

Covers api.wenqu_auth_views (wx_login / update_wx_user / get-phone stub),
api.wenqu_authentication (tv enforcement + customer permissions) and the staff
directory exclusion in api.views.UserViewSet. Service-level logic is tested in
wenqu/tests.py.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import WeChatIdentity
from accounts.wechat import WeChatAuthError
from api.wenqu_authentication import (
    CanChat, CanUpload, WenquCustomerPermission, WenquJWTAuthentication,
)
from wenqu.models import WenquProfile

User = get_user_model()

_WENQU_APPID = 'wenqu-test-appid'
_OPENID = 'o' * 28
_FAKE_SESSION = {'openid': _OPENID, 'unionid': '', 'session_key': 'never-persisted'}
_WX_LOGIN_URL = '/api/v1/wenqu/auth/wx_login'
_UPDATE_URL = '/api/v1/wenqu/auth/update_wx_user'
_GET_PHONE_URL = '/api/v1/wenqu/auth/get-phone'


def _make_customer(username='wx_customer', openid=_OPENID, active=True):
    user = User.objects.create_user(username=username, password='pw')
    user.is_active = active
    user.save()
    profile = WenquProfile.objects.create(user=user, external_id=username)
    WeChatIdentity.objects.create(user=user, appid=_WENQU_APPID, openid=openid)
    return user, profile


def _access_token(user, tv=None):
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token
    profile = getattr(user, 'wenqu_profile', None)
    if profile is not None:
        access['tv'] = profile.token_version if tv is None else tv
    return str(access)


@override_settings(WECHAT_WENQU_APPID=_WENQU_APPID, WECHAT_WENQU_APPSECRET='wenqu-secret')
class WenquAuthEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @mock.patch('wenqu.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_wx_login_new_user(self, _m):
        res = self.client.post(_WX_LOGIN_URL, {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['status'], 'new_user')
        self.assertEqual(res.data['token_type'], 'Bearer')
        self.assertIn('auth_token', res.data)
        self.assertIn('refresh', res.data)
        self.assertTrue(User.objects.filter(username=f'wx_{_OPENID[:12]}').exists())

    @mock.patch('wenqu.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_wx_login_existing_user(self, _m):
        user, _profile = _make_customer(username=f'wx_{_OPENID[:12]}')
        res = self.client.post(_WX_LOGIN_URL, {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'ok')
        self.assertEqual(res.data['user']['user_id'], str(user.id))

    def test_wx_login_missing_code(self):
        res = self.client.post(_WX_LOGIN_URL, {}, format='json')
        self.assertEqual(res.status_code, 400)

    @mock.patch('wenqu.services.auth.code2session', side_effect=WeChatAuthError('bad code'))
    def test_wx_login_wechat_error(self, _m):
        res = self.client.post(_WX_LOGIN_URL, {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 400)

    @mock.patch('wenqu.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_wx_login_conflict(self, _m):
        staff = User.objects.create_user(username='staff', password='pw')
        WeChatIdentity.objects.create(user=staff, appid=_WENQU_APPID, openid=_OPENID)
        res = self.client.post(_WX_LOGIN_URL, {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 409)

    @mock.patch('wenqu.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_wx_login_disabled(self, _m):
        _make_customer(username=f'wx_{_OPENID[:12]}', active=False)
        res = self.client.post(_WX_LOGIN_URL, {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_update_wx_user(self):
        user, profile = _make_customer()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(user)}')
        res = self.client.post(_UPDATE_URL, {'userInfo': {'nickName': 'Nick'}}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        profile.refresh_from_db()
        self.assertEqual(profile.nickname, 'Nick')
        self.assertEqual(res.data['status'], 'ok')

    def test_update_wx_user_openid_mismatch(self):
        user, _profile = _make_customer()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(user)}')
        res = self.client.post(_UPDATE_URL, {'openid': 'other'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_update_wx_user_unauthenticated(self):
        res = self.client.post(_UPDATE_URL, {'userInfo': {}}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_update_wx_user_staff_forbidden(self):
        staff = User.objects.create_user(username='staff', password='pw')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(staff)}')
        res = self.client.post(_UPDATE_URL, {'userInfo': {}}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_tv_bump_invalidates_old_token(self):
        user, profile = _make_customer()
        old_token = _access_token(user)
        profile.token_version += 1
        profile.save(update_fields=['token_version'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {old_token}')
        res = self.client.post(_UPDATE_URL, {'userInfo': {'nickName': 'N'}}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_get_phone_success(self):
        user, profile = _make_customer()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(user)}')
        with mock.patch('api.wenqu_auth_views.get_phone_number', return_value='13800138000') as phone_mock:
            res = self.client.post(_GET_PHONE_URL, {'code': 'phone-code'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data, {'status': 'success', 'phone': '13800138000'})
        phone_mock.assert_called_once_with(user, 'phone-code')

    def test_get_phone_missing_code(self):
        user, _profile = _make_customer()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(user)}')
        res = self.client.post(_GET_PHONE_URL, {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_get_phone_wechat_refused(self):
        from wenqu.services.auth import WenquPhoneError
        user, _profile = _make_customer()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(user)}')
        with mock.patch('api.wenqu_auth_views.get_phone_number', side_effect=WenquPhoneError('获取手机号失败')):
            res = self.client.post(_GET_PHONE_URL, {'code': 'bad'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_get_phone_token_unavailable(self):
        from wenqu.services.auth import WenquPhoneUnavailable
        user, _profile = _make_customer()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(user)}')
        with mock.patch('api.wenqu_auth_views.get_phone_number', side_effect=WenquPhoneUnavailable('微信配置缺失')):
            res = self.client.post(_GET_PHONE_URL, {'code': 'phone-code'}, format='json')
        self.assertEqual(res.status_code, 500)

    def test_get_phone_unauthenticated(self):
        res = self.client.post(_GET_PHONE_URL, {'code': 'phone-code'}, format='json')
        self.assertEqual(res.status_code, 401)


class WenquJWTAuthenticationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.auth = WenquJWTAuthentication()

    def _authenticate(self, token):
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Bearer {token}')
        return self.auth.authenticate(request)

    def test_no_header_returns_none(self):
        self.assertIsNone(self.auth.authenticate(self.factory.get('/')))

    def test_staff_token_without_tv_passes(self):
        staff = User.objects.create_user(username='staff', password='pw')
        user, _token = self._authenticate(_access_token(staff))
        self.assertEqual(user, staff)

    def test_customer_token_matching_tv_passes(self):
        user, _profile = _make_customer()
        authenticated, _token = self._authenticate(_access_token(user))
        self.assertEqual(authenticated, user)

    def test_customer_token_stale_tv_rejected(self):
        user, profile = _make_customer()
        stale = _access_token(user, tv=profile.token_version + 5)
        with self.assertRaises(AuthenticationFailed):
            self._authenticate(stale)

    def test_customer_token_without_tv_passes(self):
        # The tv check only fires when the claim is present (spec); a customer
        # token minted without tv (e.g. via the shared refresh endpoint) passes.
        user, _profile = _make_customer()
        refresh = RefreshToken.for_user(user)
        authenticated, _token = self._authenticate(str(refresh.access_token))
        self.assertEqual(authenticated, user)


class WenquPermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _request(self, user):
        request = self.factory.get('/')
        request.user = user
        return request

    def test_customer_permission_allows_profile_user(self):
        user, _profile = _make_customer()
        self.assertTrue(WenquCustomerPermission().has_permission(self._request(user), None))

    def test_customer_permission_denies_staff_and_anonymous(self):
        staff = User.objects.create_user(username='staff', password='pw')
        self.assertFalse(WenquCustomerPermission().has_permission(self._request(staff), None))
        self.assertFalse(WenquCustomerPermission().has_permission(self._request(AnonymousUser()), None))

    def test_can_chat_respects_flag(self):
        user, profile = _make_customer()
        self.assertTrue(CanChat().has_permission(self._request(user), None))
        profile.can_chat = False
        profile.save(update_fields=['can_chat'])
        self.assertFalse(CanChat().has_permission(self._request(user), None))

    def test_can_upload_respects_flag(self):
        user, profile = _make_customer()
        self.assertFalse(CanUpload().has_permission(self._request(user), None))
        profile.can_upload = True
        profile.save(update_fields=['can_upload'])
        self.assertTrue(CanUpload().has_permission(self._request(user), None))


class StaffDirectoryApiTests(TestCase):
    def test_user_list_excludes_wenqu_customers(self):
        admin = User.objects.create_user(username='admin', password='pw', is_superuser=True)
        User.objects.create_user(username='staffer', password='pw')
        _make_customer(username='wx_external')
        client = APIClient()
        client.force_authenticate(admin)
        res = client.get('/api/v1/users/')
        self.assertEqual(res.status_code, 200)
        usernames = {row['username'] for row in res.data['results']}
        self.assertIn('staffer', usernames)
        self.assertNotIn('wx_external', usernames)