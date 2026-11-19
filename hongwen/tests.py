"""Tests for the hongwen app: models, private storage, auth service and the
staff-directory / role wiring that keeps Hongwen customers separate from staff.

HTTP-level endpoint and authentication-class tests live in api/tests_hongwen_auth.py.
"""
import os
import tempfile
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import AdminRole, WeChatIdentity
from hongwen.models import (
    ChatSession, Customer, KBChunk, KBDocument, Message, TriageAsset,
    TriagePlaybook, TriageSession, TriageStep, UploadFile, UserProfile,
    HongwenProfile,
)
from hongwen.services.auth import (
    HongwenAccountDisabled, HongwenAuthConflict, HongwenOpenIDMismatch,
    update_wx_user, wx_login,
)
from hongwen.services.storage import private_storage

User = get_user_model()

_HONGWEN_APPID = 'hongwen-test-appid'
_OPENID = 'o' * 28
_FAKE_SESSION = {'openid': _OPENID, 'unionid': '', 'session_key': 'never-persisted'}


def _make_customer(username='wx_customer', openid=_OPENID, appid=_HONGWEN_APPID, active=True):
    user = User.objects.create_user(username=username, password='pw')
    user.is_active = active
    user.save()
    profile = HongwenProfile.objects.create(user=user, external_id=username)
    WeChatIdentity.objects.create(user=user, appid=appid, openid=openid)
    return user, profile


def _role(code):
    AdminRole.objects.get_or_create(code=code, defaults={'name': code})


class ModelTests(TestCase):
    def test_customer_str_and_defaults(self):
        customer = Customer.objects.create(name='Acme')
        self.assertEqual(str(customer), 'Acme')
        self.assertEqual(customer.notes, '')

    def test_hongwen_profile_defaults(self):
        user = User.objects.create_user(username='c1', password='pw')
        profile = HongwenProfile.objects.create(user=user, external_id='c1')
        self.assertFalse(profile.can_upload)
        self.assertTrue(profile.can_chat)
        self.assertFalse(profile.can_manage_customers)
        self.assertFalse(profile.can_manage_users)
        self.assertEqual(profile.token_version, 0)
        self.assertEqual(str(profile), 'c1')
        self.assertEqual(user.hongwen_profile, profile)

    def test_message_links_session_by_business_key(self):
        user = User.objects.create_user(username='c2', password='pw')
        session = ChatSession.objects.create(session_id='sess-1', user=user, title='Hi')
        message = Message.objects.create(
            session=session, user=user, role=Message.Role.USER,
            type=Message.Type.TEXT, content='{"text": "hello"}',
        )
        self.assertEqual(message.session_id, 'sess-1')
        self.assertEqual(list(session.messages.all()), [message])
        self.assertEqual(str(session), 'Hi')

    def test_user_profile_one_to_one_pk(self):
        user = User.objects.create_user(username='c3', password='pw')
        UserProfile.objects.create(user=user, weak_points='{}')
        self.assertEqual(user.hongwen_learning_profile.user_id, user.id)

    def test_kb_chunk_embedding_roundtrip_and_unique(self):
        doc = KBDocument.objects.create(path='a.txt', source_type='txt', content_hash='h' * 64)
        embedding = [0.1] * 1536
        chunk = KBChunk.objects.create(
            document=doc, chunk_index=0, content='text',
            embedding=embedding, source_path='a.txt',
        )
        chunk.refresh_from_db()
        self.assertEqual(chunk.embedding, embedding)
        self.assertEqual(chunk.chunk_type, KBChunk.ChunkType.TEXT)
        self.assertEqual(chunk.metadata_json, {})
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                KBChunk.objects.create(document=doc, chunk_index=0, content='dup', embedding=[], source_path='a.txt')

    def test_triage_graph_relationships_and_constraints(self):
        user = User.objects.create_user(username='c4', password='pw')
        asset = TriageAsset.objects.create(asset_key='shot-1', relative_path='triage_assets/x.png')
        playbook = TriagePlaybook.objects.create(slug='wifi', title_zh='WiFi', symptom_summary_zh='断网')
        step = TriageStep.objects.create(
            playbook=playbook, step_index=0, kind=TriageStep.Kind.CHECK,
            prompt_zh='检查', asset=asset,
        )
        self.assertEqual(step.quick_replies_json, [])
        self.assertEqual(step.branch_map_json, {})
        self.assertEqual(list(playbook.steps.all()), [step])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TriageStep.objects.create(playbook=playbook, step_index=0, kind=TriageStep.Kind.END, prompt_zh='dup')
        triage = TriageSession.objects.create(user=user, session_id='s', playbook=playbook)
        self.assertEqual(triage.status, TriageSession.Status.IN_PROGRESS)
        self.assertEqual(triage.answers_json, {})
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TriageSession.objects.create(user=user, session_id='s', playbook=playbook)
        self.assertIsNone(playbook.embedding)

    def test_str_representations(self):
        user = User.objects.create_user(username='struser', password='pw')
        customer = Customer.objects.create(name='StrCo')
        profile = HongwenProfile.objects.create(user=user, external_id='struser', customer=customer)
        session = ChatSession.objects.create(session_id='str-sess', user=user)
        message = Message.objects.create(session=session, user=user, role='user', type='text', content='{}')
        learning = UserProfile.objects.create(user=user)
        upload = UploadFile.objects.create(user=user, file_type='audio', original_filename='a.m4a')
        doc = KBDocument.objects.create(path='str.txt', source_type='txt', content_hash='h')
        chunk = KBChunk.objects.create(document=doc, chunk_index=0, content='c', embedding=[], source_path='str.txt')
        asset = TriageAsset.objects.create(asset_key='str-asset', relative_path='x.png')
        playbook = TriagePlaybook.objects.create(slug='str-pb', title_zh='T', symptom_summary_zh='S')
        step = TriageStep.objects.create(playbook=playbook, step_index=0, kind='check', prompt_zh='p')
        triage = TriageSession.objects.create(user=user, session_id='str-sess', playbook=playbook)
        for obj in (customer, profile, session, message, learning, upload, doc, chunk, asset, playbook, step, triage):
            self.assertTrue(str(obj))


class UploadFileStorageTests(TestCase):
    """Uploads must land on the private backend, never the public MEDIA_ROOT.

    Django's FileField resolves a callable storage eagerly at field-construction
    (model import) time, so ``override_settings`` cannot redirect an already-built
    field. The write tests therefore swap ``field.storage`` for a FileSystemStorage
    rooted at a temp dir and restore it afterwards; the callable itself is tested
    separately for its lazy settings read.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.field = UploadFile._meta.get_field('file')

    def _swap_to_tmp_storage(self):
        original = self.field.storage
        self.field.storage = FileSystemStorage(location=self._tmp.name)
        self.addCleanup(setattr, self.field, 'storage', original)

    def test_private_storage_callable_reads_setting_lazily(self):
        with override_settings(HONGWEN_PRIVATE_MEDIA_ROOT=self._tmp.name):
            self.assertEqual(str(private_storage().location), self._tmp.name)

    def test_field_default_storage_is_private_root_not_media(self):
        storage = self.field.storage
        self.assertIsInstance(storage, FileSystemStorage)
        self.assertEqual(
            os.path.normpath(storage.location),
            os.path.normpath(str(settings.HONGWEN_PRIVATE_MEDIA_ROOT)),
        )
        self.assertNotEqual(
            os.path.normpath(storage.location), os.path.normpath(str(settings.MEDIA_ROOT)),
        )

    def test_file_id_default_is_uuid4_hex(self):
        user = User.objects.create_user(username='u1', password='pw')
        upload = UploadFile.objects.create(
            user=user, file_type=UploadFile.FileType.AUDIO, original_filename='v.m4a',
        )
        self.assertEqual(len(upload.file_id), 32)
        int(upload.file_id, 16)  # raises if not hex

    def test_save_writes_under_private_root_not_media(self):
        self._swap_to_tmp_storage()
        user = User.objects.create_user(username='u2', password='pw')
        upload = UploadFile.objects.create(
            user=user, file_type=UploadFile.FileType.AUDIO, original_filename='v.m4a',
        )
        upload.file.save('voice.m4a', ContentFile(b'audio-bytes'), save=True)
        self.assertTrue(upload.file.name.startswith('hongwen/uploads/'))
        storage = upload.file.storage
        self.assertTrue(storage.exists(upload.file.name))
        self.assertEqual(os.path.normpath(storage.location), os.path.normpath(self._tmp.name))
        self.assertFalse(
            os.path.normpath(storage.location).startswith(os.path.normpath(str(settings.MEDIA_ROOT)))
        )
        with storage.open(upload.file.name) as fh:
            self.assertEqual(fh.read(), b'audio-bytes')


@override_settings(WECHAT_HONGWEN_APPID=_HONGWEN_APPID, WECHAT_HONGWEN_APPSECRET='hongwen-secret')
class WxLoginServiceTests(TestCase):
    @mock.patch('hongwen.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_new_user_auto_registers(self, _m):
        payload = wx_login('code')
        self.assertEqual(payload['status'], 'new_user')
        self.assertEqual(payload['token_type'], 'Bearer')
        user = User.objects.get(username=f'wx_{_OPENID[:12]}')
        self.assertFalse(user.has_usable_password())
        profile = user.hongwen_profile
        self.assertEqual(profile.external_id, user.username)
        self.assertTrue(WeChatIdentity.objects.filter(appid=_HONGWEN_APPID, openid=_OPENID, user=user).exists())
        self.assertEqual(payload['user']['user_id'], str(user.id))
        self.assertEqual(AccessToken(payload['auth_token'])['tv'], profile.token_version)
        self.assertIn('refresh', payload)

    @mock.patch('hongwen.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_existing_user_returns_ok(self, _m):
        user, profile = _make_customer(username=f'wx_{_OPENID[:12]}')
        payload = wx_login('code')
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['user']['user_id'], str(user.id))
        self.assertEqual(User.objects.count(), 1)

    @mock.patch('hongwen.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_conflict_when_openid_bound_to_non_customer(self, _m):
        staff = User.objects.create_user(username='staff', password='pw')
        WeChatIdentity.objects.create(user=staff, appid=_HONGWEN_APPID, openid=_OPENID)
        with self.assertRaises(HongwenAuthConflict):
            wx_login('code')

    @mock.patch('hongwen.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_disabled_account_rejected(self, _m):
        _make_customer(username=f'wx_{_OPENID[:12]}', active=False)
        with self.assertRaises(HongwenAccountDisabled):
            wx_login('code')

    @mock.patch('hongwen.services.auth.code2session', return_value={**_FAKE_SESSION, 'unionid': 'union-1'})
    def test_unionid_refreshed_on_login(self, _m):
        user, _profile = _make_customer(username=f'wx_{_OPENID[:12]}')
        wx_login('code')
        self.assertEqual(WeChatIdentity.objects.get(user=user).unionid, 'union-1')

    @mock.patch('hongwen.services.auth.code2session', return_value=_FAKE_SESSION)
    def test_username_collision_gets_suffix(self, _m):
        User.objects.create_user(username=f'wx_{_OPENID[:12]}', password='pw')
        payload = wx_login('code')
        self.assertEqual(payload['user']['external_id'], f'wx_{_OPENID[:12]}_2')
        self.assertEqual(User.objects.count(), 2)


@override_settings(WECHAT_HONGWEN_APPID=_HONGWEN_APPID, WECHAT_HONGWEN_APPSECRET='hongwen-secret')
class UpdateWxUserServiceTests(TestCase):
    def setUp(self):
        self.user, self.profile = _make_customer()

    def test_updates_profile_fields_and_remints(self):
        payload = update_wx_user(self.user, {
            'userInfo': {'nickName': 'Nick', 'avatarUrl': 'http://a/x.png', 'phoneNumber': '13800000000'},
        })
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.nickname, 'Nick')
        self.assertEqual(self.profile.avatar, 'http://a/x.png')
        self.assertEqual(self.profile.mobile, '13800000000')
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(AccessToken(payload['auth_token'])['tv'], self.profile.token_version)

    def test_openid_mismatch_rejected(self):
        with self.assertRaises(HongwenOpenIDMismatch):
            update_wx_user(self.user, {'openid': 'someone-else'})

    def test_matching_openid_accepted(self):
        payload = update_wx_user(self.user, {'openid': _OPENID, 'userInfo': {'nickName': 'N'}})
        self.assertEqual(payload['status'], 'ok')

    def test_empty_userinfo_is_noop(self):
        payload = update_wx_user(self.user, {})
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.nickname)
        self.assertEqual(payload['status'], 'ok')


class CanManageHongwenTests(TestCase):
    def test_superadmin(self):
        user = User.objects.create_user(username='root', password='pw', is_superuser=True)
        self.assertTrue(user.can_manage_hongwen())

    def test_it_administrator(self):
        _role('it_administrator')
        user = User.objects.create_user(username='it', password='pw')
        user.set_admin_roles(['it_administrator'])
        self.assertTrue(user.can_manage_hongwen())

    def test_other_roles_and_plain_user_denied(self):
        for code in ('viewer', 'order_management_specialist', 'inspection_engineer'):
            _role(code)
        user = User.objects.create_user(username='other', password='pw')
        user.set_admin_roles(['viewer', 'order_management_specialist', 'inspection_engineer'])
        self.assertFalse(user.can_manage_hongwen())
        plain = User.objects.create_user(username='plain', password='pw')
        self.assertFalse(plain.can_manage_hongwen())


class StaffDirectoryExcludesCustomersTests(TestCase):
    def test_web_user_list_excludes_hongwen_customers(self):
        admin = User.objects.create_user(username='admin', password='pw', is_superuser=True, is_staff=True)
        staff = User.objects.create_user(username='staffer', password='pw')
        customer, _profile = _make_customer(username='wx_external')
        self.client.force_login(admin)
        res = self.client.get(reverse('accounts:user_list'))
        self.assertEqual(res.status_code, 200)
        listed = {u.username for u in res.context['users']}
        self.assertIn('staffer', listed)
        self.assertNotIn('wx_external', listed)


@skipUnless(connection.vendor == 'postgresql', 'pgvector requires PostgreSQL')
class PgVectorTests(TestCase):
    def test_extension_installed_by_migration(self):
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            self.assertIsNotNone(cur.fetchone())

    def test_jsonb_embedding_cast_cosine_retrieval(self):
        doc = KBDocument.objects.create(path='p.txt', source_type='txt', content_hash='h')
        KBChunk.objects.create(document=doc, chunk_index=0, content='near', embedding=[1.0, 0.0, 0.0], source_path='p.txt')
        KBChunk.objects.create(document=doc, chunk_index=1, content='far', embedding=[0.0, 1.0, 0.0], source_path='p.txt')
        with connection.cursor() as cur:
            cur.execute(
                "SELECT chunk_index FROM hongwen_kbchunk "
                "ORDER BY embedding::text::vector <=> CAST(%s AS vector) LIMIT 1",
                ['[1.0,0.0,0.0]'],
            )
            self.assertEqual(cur.fetchone()[0], 0)