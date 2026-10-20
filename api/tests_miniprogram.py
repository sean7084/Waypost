"""
Tests for the mini-program REST surface: WeChat bind/login auth and the
store-inspection API (engineer scoping, idempotent device upsert, checklist).
"""
import io
import tempfile
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import AdminRole, WeChatIdentity
from companies.models import Company, Location
from inspections.models import InspectionDevice, InspectionIssue, InspectionPhoto, StoreInspection

User = get_user_model()

_FAKE_SESSION = {'openid': 'openid-123', 'unionid': 'union-1', 'session_key': 'secret-not-stored'}
_TEMP_MEDIA = tempfile.mkdtemp(prefix='mp-api-test-media-')


def _png_upload(name='photo.png', color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def _make_engineer(username):
    AdminRole.objects.get_or_create(code='inspection_engineer', defaults={'name': 'Inspection Engineer'})
    user = User.objects.create_user(username=username, password='pw')
    user.set_admin_roles(['inspection_engineer'])
    return user


def _make_inspection(engineer, jda='22149'):
    company = Company.objects.create(name=f'Kering-{jda}', code=f'KER{jda}')
    location = Location.objects.create(
        company=company, name='SZOL', code=jda, location_type=Location.LocationType.STORE,
    )
    return StoreInspection.objects.create(
        company=company, location=location, jda_code=jda, brand_name='Gucci',
        store_name='SZOL', inspection_date=date(2026, 9, 2), engineer=engineer,
    )


@override_settings(WECHAT_MINI_APPID='test-appid', WECHAT_MINI_APPSECRET='test-secret')
class WeChatAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='sean', password='pw')

    @mock.patch('api.views_auth.code2session', return_value=_FAKE_SESSION)
    def test_bind_links_openid_and_issues_tokens(self, _m):
        res = self.client.post('/api/v1/auth/wechat/bind/', {
            'code': 'wx-code', 'username': 'sean', 'password': 'pw',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data['bound'])
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertTrue(WeChatIdentity.objects.filter(appid='test-appid', openid='openid-123', user=self.user).exists())

    @mock.patch('api.views_auth.code2session', return_value=_FAKE_SESSION)
    def test_bind_rejects_bad_credentials(self, _m):
        res = self.client.post('/api/v1/auth/wechat/bind/', {
            'code': 'wx-code', 'username': 'sean', 'password': 'wrong',
        }, format='json')
        self.assertEqual(res.status_code, 401)

    @mock.patch('api.views_auth.code2session', return_value=_FAKE_SESSION)
    def test_login_unbound_returns_bound_false(self, _m):
        res = self.client.post('/api/v1/auth/wechat/login/', {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['bound'])

    @mock.patch('api.views_auth.code2session', return_value=_FAKE_SESSION)
    def test_login_after_bind_returns_tokens(self, _m):
        WeChatIdentity.objects.create(user=self.user, appid='test-appid', openid='openid-123')
        res = self.client.post('/api/v1/auth/wechat/login/', {'code': 'wx-code'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['bound'])
        self.assertIn('access', res.data)


class InspectionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.engineer = _make_engineer('eng1')
        self.other = _make_engineer('eng2')
        self.inspection = _make_inspection(self.engineer)

    def test_engineer_sees_only_own_inspections(self):
        self.client.force_authenticate(self.engineer)
        res = self.client.get('/api/v1/inspections/')
        ids = [row['id'] for row in res.data['results']]
        self.assertIn(str(self.inspection.id), ids)

        self.client.force_authenticate(self.other)
        res = self.client.get('/api/v1/inspections/')
        ids = [row['id'] for row in res.data['results']]
        self.assertNotIn(str(self.inspection.id), ids)

    def test_other_engineer_cannot_access_detail(self):
        self.client.force_authenticate(self.other)
        res = self.client.get(f'/api/v1/inspections/{self.inspection.id}/')
        self.assertEqual(res.status_code, 404)

    def test_device_upsert_is_idempotent_by_client_uid(self):
        self.client.force_authenticate(self.engineer)
        url = f'/api/v1/inspections/{self.inspection.id}/devices/'
        payload = {'client_device_uid': 'dev-1', 'category': 'Desktop', 'sn': 'PC1', 'status': 'in_store'}
        first = self.client.post(url, payload, format='json')
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.post(url, {**payload, 'ip_address': '10.0.0.9'}, format='json')
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(InspectionDevice.objects.filter(store_inspection=self.inspection).count(), 1)
        device = InspectionDevice.objects.get(client_device_uid='dev-1')
        self.assertEqual(device.ip_address, '10.0.0.9')
        self.assertIsNotNone(device.collected_at)

    def test_device_detail_route_upsert_by_path_uid(self):
        """POST|PATCH /inspections/{id}/devices/{uid}/ upserts by the path uid."""
        self.client.force_authenticate(self.engineer)
        url = f'/api/v1/inspections/{self.inspection.id}/devices/dev-path-1/'
        first = self.client.post(url, {'category': 'Desktop', 'sn': 'PC9', 'status': 'in_store'}, format='json')
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.patch(url, {'ip_address': '10.9.9.9'}, format='json')
        self.assertEqual(second.status_code, 200, second.content)
        qs = InspectionDevice.objects.filter(store_inspection=self.inspection, client_device_uid='dev-path-1')
        self.assertEqual(qs.count(), 1)
        device = qs.get()
        self.assertEqual(device.ip_address, '10.9.9.9')
        self.assertEqual(device.sn, 'PC9')  # preserved from the initial POST

    def test_submitted_inspection_rejects_writes(self):
        self.inspection.status = StoreInspection.Status.SUBMITTED
        self.inspection.save()
        self.client.force_authenticate(self.engineer)
        res = self.client.post(
            f'/api/v1/inspections/{self.inspection.id}/devices/',
            {'client_device_uid': 'x', 'category': 'Monitor'}, format='json',
        )
        self.assertEqual(res.status_code, 409)

    def test_checklist_endpoint(self):
        self.client.force_authenticate(self.engineer)
        res = self.client.get('/api/v1/inspections/checklist/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('checklist', res.data)
        self.assertIn('capture_fields', res.data)
        self.assertIn('confirmation_count_labels', res.data)
        self.assertTrue(res.data['checklist'])


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class InspectionWorkflowApiTests(TestCase):
    """Photo / issue / signoff / report endpoints (media-writing, offline-sync targets)."""

    def setUp(self):
        self.client = APIClient()
        self.engineer = _make_engineer('eng-wf')
        self.inspection = _make_inspection(self.engineer, jda='22150')
        self.client.force_authenticate(self.engineer)

    def test_issue_post_updates_cover_counts(self):
        url = f'/api/v1/inspections/{self.inspection.id}/issues/'
        res = self.client.post(url, {'seq': 1, 'description': 'Monitor light leakage', 'status': 'to_be_followed'}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.store_issue_found_count, 1)
        self.assertEqual(self.inspection.issue_to_follow_count, 1)
        self.assertEqual(InspectionIssue.objects.filter(store_inspection=self.inspection).count(), 1)

    def test_photo_upload_rack(self):
        url = f'/api/v1/inspections/{self.inspection.id}/photos/'
        res = self.client.post(url, {
            'kind': 'rack1', 'client_photo_uid': 'p-1', 'image': _png_upload(),
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(InspectionPhoto.objects.filter(store_inspection=self.inspection, kind='rack1').count(), 1)

    def test_photo_upload_is_idempotent_by_uid(self):
        url = f'/api/v1/inspections/{self.inspection.id}/photos/'
        self.client.post(url, {'kind': 'router', 'client_photo_uid': 'p-dup', 'image': _png_upload('a.png')}, format='multipart')
        self.client.post(url, {'kind': 'router', 'client_photo_uid': 'p-dup', 'image': _png_upload('b.png', 'blue')}, format='multipart')
        self.assertEqual(
            InspectionPhoto.objects.filter(store_inspection=self.inspection, client_photo_uid='p-dup').count(), 1
        )

    def test_device_upsert_with_photo_multipart(self):
        url = f'/api/v1/inspections/{self.inspection.id}/devices/'
        res = self.client.post(url, {
            'client_device_uid': 'dev-mp', 'category': 'Desktop', 'sn': 'PC77',
            'status': 'in_store', 'overall_photo': _png_upload('overall.png'), 'overall_photo_uid': 'ph-1',
        }, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        device = InspectionDevice.objects.get(client_device_uid='dev-mp')
        self.assertEqual(device.photos.filter(kind='overall').count(), 1)

    def test_signoff_marks_submitted(self):
        url = f'/api/v1/inspections/{self.inspection.id}/signoff/'
        res = self.client.post(url, {
            'store_signature': _png_upload('store.png'),
            'engineer_signature': _png_upload('eng.png'),
        }, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.status, StoreInspection.Status.SUBMITTED)
        self.assertIsNotNone(self.inspection.signed_at)

    def test_report_generation_returns_urls(self):
        device_url = f'/api/v1/inspections/{self.inspection.id}/devices/'
        self.client.post(device_url, {
            'client_device_uid': 'dev-r', 'category': 'Monitor', 'brand_model': 'Lenovo-L1710',
            'sn': 'M77', 'status': 'in_store',
        }, format='json')
        res = self.client.post(f'/api/v1/inspections/{self.inspection.id}/report/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data['report_file'])
        self.inspection.refresh_from_db()
        self.assertIsNotNone(self.inspection.report_generated_at)
        self.assertTrue(self.inspection.report_file.storage.exists(self.inspection.report_file.name))

    def test_photo_bundle_streams_zip(self):
        """photo_zip is retired; photos stream on demand from the photo-bundle action."""
        self.client.post(
            f'/api/v1/inspections/{self.inspection.id}/photos/',
            {'kind': 'rack1', 'client_photo_uid': 'p-bundle', 'image': _png_upload()},
            format='multipart',
        )
        res = self.client.get(f'/api/v1/inspections/{self.inspection.id}/photo-bundle/')
        self.assertEqual(res.status_code, 200)
        import zipfile
        content = b''.join(res.streaming_content) if res.streaming else res.content
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            self.assertTrue(any('/Photo/' in n for n in zf.namelist()), zf.namelist())

    @override_settings(MAX_UPLOAD_SIZE=10)  # 10 bytes: any real image exceeds this
    def test_oversized_photo_upload_rejected(self):
        """Server-side upload cap: files over MAX_UPLOAD_SIZE are rejected with 400."""
        url = f'/api/v1/inspections/{self.inspection.id}/photos/'
        res = self.client.post(url, {
            'kind': 'rack1', 'client_photo_uid': 'p-big', 'image': _png_upload('big.png'),
        }, format='multipart')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn('limit', res.data['error'].lower())
        self.assertEqual(InspectionPhoto.objects.filter(store_inspection=self.inspection).count(), 0)

    @override_settings(MAX_UPLOAD_SIZE=10)
    def test_oversized_device_photo_upload_rejected(self):
        """The cap also guards the multipart device-upsert photo parts (before any write)."""
        url = f'/api/v1/inspections/{self.inspection.id}/devices/'
        res = self.client.post(url, {
            'client_device_uid': 'dev-big', 'category': 'Desktop', 'sn': 'PC88',
            'status': 'in_store', 'overall_photo': _png_upload('o.png'), 'overall_photo_uid': 'ph-big',
        }, format='multipart')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(InspectionDevice.objects.filter(client_device_uid='dev-big').exists())
