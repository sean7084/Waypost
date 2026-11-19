"""HTTP-level contract tests for the Hongwen customer API (PR4).

DRF APIClient against the real URLconf; every provider/KB call is stubbed at
its import site so no API key, ffmpeg, Redis or PostgreSQL is required:

* turn/state helpers      -> ``hongwen.services.chat.*`` (views call through the
  ``chat`` module object)
* SSE deltas              -> ``hongwen.services.sse.stream_reply_deltas``
* provider/media functions-> ``api.hongwen_views.*`` (imported by name)
* WeChat moderation       -> ``hongwen.services.utils.moderation.check_text`` /
  ``check_image`` (module attribute, shared by every importer)

The contract canon is helpdesk/backend/app/main.py; assertions mirror the
payload shapes, SSE frame sequence and meta keys documented there.
"""
import json
import os
import shutil
import tempfile
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import WeChatIdentity
from api.hongwen_views import CHAT_UPLOAD_MAX_BYTES
from hongwen.models import (
    ChatSession, Customer, KBChunk, KBDocument, Message, UploadFile, UserProfile, HongwenProfile,
)
from hongwen.services import registries
from hongwen.services.llm.orchestrator import ReplyPreparation
from hongwen.services.storage import private_storage
from hongwen.services.triage import TriageTurn
from hongwen.services.utils import moderation
from hongwen.services.utils.file_store import store_upload_bytes
from hongwen.services_testing import stub_vector

User = get_user_model()

_HONGWEN_APPID = 'hongwen-test-appid'
_OPENID = 'o' * 28

_BOOTSTRAP_URL = '/api/v1/hongwen/chatbot'
_SESSIONS_URL = '/api/v1/hongwen/chat/sessions'
_CHAT_URL = '/api/v1/hongwen/chat'
_REGENERATE_URL = '/api/v1/hongwen/chat/regenerate'
_STREAM_URL = '/api/v1/hongwen/chat/stream'
_REGENERATE_STREAM_URL = '/api/v1/hongwen/chat/regenerate/stream'
_UPLOAD_URL = '/api/v1/hongwen/files/upload'
_TRANSCRIBE_URL = '/api/v1/hongwen/transcribe'
_SPEAK_URL = '/api/v1/hongwen/speak'
_SPEAK_STREAM_URL = '/api/v1/hongwen/speak/stream'
_GET_PHONE_URL = '/api/v1/hongwen/auth/get-phone'

_REPLY = '请先重启路由器，再检查电源适配器。'
_META = {
    'used_knowledge_base': True,
    'knowledge_sources': ['manual.pdf'],
    'attachments': [],
    'image_paths': [],
    'model_used': 'dashscope/qwen-test',
    'model_primary': 'dashscope/qwen-test',
    'model_fallback': 'dashscope/qwen-backup',
}
_UPDATES = {'weak_points': '{"network": true}', 'suggested_solutions': '{}'}


def _make_customer(username='wx_customer', openid=_OPENID, customer=None, **flags):
    user = User.objects.create_user(username=username, password='pw')
    profile = HongwenProfile.objects.create(user=user, external_id=username, customer=customer, **flags)
    WeChatIdentity.objects.create(user=user, appid=_HONGWEN_APPID, openid=openid)
    return user, profile


def _access_token(user, tv=None):
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token
    profile = getattr(user, 'hongwen_profile', None)
    if profile is not None:
        access['tv'] = profile.token_version if tv is None else tv
    return str(access)


def _preparation(base_meta=None):
    meta = dict(_META if base_meta is None else base_meta)
    return ReplyPreparation(
        primary_model=meta.get('model_primary', 'dashscope/qwen-test'),
        fallback_model=meta.get('model_fallback', 'dashscope/qwen-backup'),
        user_profile={}, knowledge_base=[], kb_attachments=[],
        user_message={'role': 'user', 'content': '你好'},
        guidance_payload={'role': 'user', 'content': '{}'},
        base_meta=meta,
    )


def _fake_generate(*, user, new_message, session_id, regenerate=False):
    return _REPLY, dict(_META), dict(_UPDATES)


def _seed_turn(user, session_id, *, user_text='路由器亮红灯', reply_text='旧回复'):
    session, _ = ChatSession.objects.get_or_create(session_id=session_id, user=user)
    user_msg = Message.objects.create(
        user=user, session_id=session_id, role='user', type='text',
        content=json.dumps({'type': 'text', 'content': {'text': user_text}, 'session_id': session_id}),
    )
    assistant_msg = Message.objects.create(
        user=user, session_id=session_id, role='assistant', type='text',
        content=json.dumps({'text': reply_text, 'meta': {}}),
    )
    return session, user_msg, assistant_msg


class _CustomerAPIMixin:
    """Authenticated customer with a linked company; moderation is skipped
    (no WeChat credentials configured) unless a test patches check_text."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.customer = Customer.objects.create(name='Acme')
        self.user, self.profile = _make_customer(customer=self.customer)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(self.user)}')


class _PrivateMediaMixin:
    """Pin private storage to a throwaway root so uploads never touch the repo."""

    def setUp(self):
        super().setUp()
        self.private_root = tempfile.mkdtemp(prefix='hongwen-api-private-')
        self.addCleanup(shutil.rmtree, self.private_root, True)
        override = override_settings(HONGWEN_PRIVATE_MEDIA_ROOT=self.private_root)
        override.enable()
        self.addCleanup(override.disable)


class BootstrapTests(_CustomerAPIMixin, TestCase):
    def test_bootstrap_shape_and_brand_greeting(self):
        res = self.client.get(_BOOTSTRAP_URL)
        self.assertEqual(res.status_code, 200, res.content)
        data = res.data
        self.assertEqual(
            set(data.keys()),
            {'greeting', 'session_id', 'restored', 'sessions', 'external_user_id', 'messages'},
        )
        self.assertIn('泓问', data['greeting'])
        self.assertFalse(data['restored'])
        self.assertTrue(data['session_id'].startswith('pc_sess_'))
        self.assertEqual(data['external_user_id'], self.profile.external_id)
        self.assertIsInstance(data['sessions'], list)
        self.assertIsInstance(data['messages'], list)
        first = data['messages'][0]
        self.assertEqual(first['role'], 'assistant')
        self.assertEqual(first['type'], 'text')
        self.assertEqual(first['content'], data['greeting'])
        self.assertEqual(first['meta'], {'identifying': False})
        # The greeting is persisted, so a second bootstrap restores it.
        again = self.client.get(_BOOTSTRAP_URL)
        self.assertTrue(again.data['restored'])
        self.assertEqual(again.data['session_id'], data['session_id'])
        self.assertEqual(again.data['greeting'], data['greeting'])

    def test_bootstrap_unlinked_customer_asks_company(self):
        self.profile.customer = None
        self.profile.save(update_fields=['customer'])
        res = self.client.get(_BOOTSTRAP_URL)
        self.assertEqual(res.status_code, 200)
        self.assertIn('哪家公司', res.data['greeting'])
        self.assertEqual(res.data['messages'][0]['meta'], {'identifying': True})

    def test_bootstrap_force_new_and_restore_by_id(self):
        first = self.client.get(_BOOTSTRAP_URL).data
        forced = self.client.get(_BOOTSTRAP_URL, {'force_new': '1'}).data
        self.assertNotEqual(forced['session_id'], first['session_id'])
        self.assertFalse(forced['restored'])
        restored = self.client.get(_BOOTSTRAP_URL, {'session_id': first['session_id']}).data
        self.assertEqual(restored['session_id'], first['session_id'])
        self.assertTrue(restored['restored'])

    def test_bootstrap_foreign_session_404(self):
        other, _ = _make_customer(username='wx_other', openid='p' * 28)
        foreign = ChatSession.objects.create(session_id='pc_sess_foreign', user=other)
        res = self.client.get(_BOOTSTRAP_URL, {'session_id': foreign.session_id})
        self.assertEqual(res.status_code, 404)

    def test_bootstrap_requires_customer(self):
        staff = User.objects.create_user(username='staff', password='pw')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(staff)}')
        self.assertEqual(self.client.get(_BOOTSTRAP_URL).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.get(_BOOTSTRAP_URL).status_code, 401)

    def test_sessions_summary_is_bare_array_not_paginated(self):
        _seed_turn(self.user, 'sess_aaa')
        res = self.client.get(_SESSIONS_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data.keys()), {'sessions'})
        self.assertIsInstance(res.data['sessions'], list)
        self.assertNotIn('results', res.data)
        self.assertNotIn('count', res.data)
        summary = res.data['sessions'][0]
        self.assertEqual(
            set(summary.keys()),
            {'session_id', 'title', 'preview', 'created_at', 'updated_at', 'message_count', 'is_current'},
        )
        self.assertEqual(summary['message_count'], 2)
        self.assertEqual(summary['title'], '路由器亮红灯')


class SessionCrudTests(_CustomerAPIMixin, TestCase):
    def test_patch_rename_and_clear(self):
        session, _u, _a = _seed_turn(self.user, 'sess_rename')
        url = f'{_SESSIONS_URL}/{session.session_id}'
        res = self.client.patch(url, {'title': '  新   标题  '}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        session.refresh_from_db()
        self.assertEqual(session.title, '新 标题')
        self.assertEqual(res.data['session']['title'], '新 标题')
        self.assertIn('sessions', res.data)
        cleared = self.client.patch(url, {'title': None}, format='json')
        self.assertEqual(cleared.status_code, 200)
        session.refresh_from_db()
        self.assertIsNone(session.title)

    def test_patch_title_too_long_400(self):
        session, _u, _a = _seed_turn(self.user, 'sess_long')
        res = self.client.patch(f'{_SESSIONS_URL}/{session.session_id}', {'title': 'x' * 256}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_patch_foreign_session_404(self):
        res = self.client.patch(f'{_SESSIONS_URL}/sess_missing', {'title': 't'}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_delete_other_session(self):
        session, _u, _a = _seed_turn(self.user, 'sess_del')
        _seed_turn(self.user, 'sess_keep')
        res = self.client.delete(f'{_SESSIONS_URL}/{session.session_id}?current_session_id=sess_keep')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.data['current_session_deleted'])
        self.assertEqual(res.data['deleted_session_id'], 'sess_del')
        self.assertEqual(res.data['current_session_id'], 'sess_keep')
        self.assertNotIn('bootstrap', res.data)
        self.assertFalse(ChatSession.objects.filter(session_id='sess_del').exists())
        self.assertFalse(Message.objects.filter(session_id='sess_del').exists())
        self.assertTrue(ChatSession.objects.filter(session_id='sess_keep').exists())

    def test_delete_current_session_returns_bootstrap(self):
        session, _u, _a = _seed_turn(self.user, 'sess_cur')
        res = self.client.delete(f'{_SESSIONS_URL}/{session.session_id}?current_session_id=sess_cur')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data['current_session_deleted'])
        bootstrap = res.data['bootstrap']
        self.assertEqual(
            set(bootstrap.keys()),
            {'greeting', 'session_id', 'restored', 'sessions', 'external_user_id', 'messages'},
        )
        self.assertEqual(res.data['current_session_id'], bootstrap['session_id'])
        self.assertNotEqual(bootstrap['session_id'], 'sess_cur')
        self.assertIn('泓问', bootstrap['greeting'])

    def test_delete_foreign_session_404(self):
        res = self.client.delete(f'{_SESSIONS_URL}/sess_missing')
        self.assertEqual(res.status_code, 404)


class ChatSyncTests(_CustomerAPIMixin, TestCase):
    def test_chat_reply_contract_and_persistence(self):
        with mock.patch('hongwen.services.chat.generate_reply_and_update_profile', side_effect=_fake_generate):
            res = self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': '路由器亮红灯'}}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(set(res.data.keys()), {'reply', 'session_id', 'meta'})
        self.assertEqual(res.data['reply'], _REPLY)
        self.assertTrue(res.data['session_id'].startswith('sess_'))
        self.assertTrue(res.data['meta']['used_knowledge_base'])
        self.assertEqual(res.data['meta']['knowledge_sources'], ['manual.pdf'])

        messages = list(Message.objects.filter(session_id=res.data['session_id']).order_by('id'))
        self.assertEqual([m.role for m in messages], ['user', 'assistant'])
        envelope = json.loads(messages[0].content)
        self.assertEqual(envelope['type'], 'text')
        self.assertEqual(envelope['content'], {'text': '路由器亮红灯'})
        stored = json.loads(messages[1].content)
        self.assertEqual(stored['text'], _REPLY)
        self.assertEqual(stored['meta']['model_used'], 'dashscope/qwen-test')
        # The learning profile updates are applied by the view layer (PR3 returns them).
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.weak_points, '{"network": true}')

    def test_chat_reuses_given_session(self):
        session, _u, _a = _seed_turn(self.user, 'sess_given')
        with mock.patch('hongwen.services.chat.generate_reply_and_update_profile', side_effect=_fake_generate):
            res = self.client.post(
                _CHAT_URL, {'type': 'text', 'content': {'text': '再问一次'}, 'session_id': session.session_id},
                format='json',
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['session_id'], 'sess_given')

    def test_chat_foreign_session_id_404(self):
        other, _ = _make_customer(username='wx_other2', openid='q' * 28)
        ChatSession.objects.create(session_id='sess_foreign', user=other)
        res = self.client.post(
            _CHAT_URL, {'type': 'text', 'content': {'text': 'hi'}, 'session_id': 'sess_foreign'}, format='json',
        )
        self.assertEqual(res.status_code, 404)

    def test_chat_missing_type_422(self):
        res = self.client.post(_CHAT_URL, {'content': {'text': 'hi'}}, format='json')
        self.assertEqual(res.status_code, 422)

    def test_chat_moderation_block_is_plain_json_400(self):
        with mock.patch.object(moderation, 'check_text', return_value=moderation.Suggest.RISKY):
            res = self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': '违规内容'}}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('detail', res.data)
        self.assertFalse(Message.objects.filter(user=self.user, role='user').exists())

    def test_chat_output_moderation_replaces_reply(self):
        def _risky_reply(*, user, new_message, session_id, regenerate=False):
            return '违规回复', dict(_META), dict(_UPDATES)

        def _check(text, openid):
            # Only the generated reply is risky; the user's question passes.
            return moderation.Suggest.RISKY if text == '违规回复' else moderation.Suggest.PASS

        with mock.patch('hongwen.services.chat.generate_reply_and_update_profile', side_effect=_risky_reply), \
             mock.patch.object(moderation, 'check_text', side_effect=_check):
            res = self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': '问题'}}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotEqual(res.data['reply'], '违规回复')
        self.assertEqual(res.data['meta']['content_moderation'], 'replaced')

    def test_chat_unlinked_customer_identification_flow(self):
        self.profile.customer = None
        self.profile.save(update_fields=['customer'])
        # The real client bootstraps first: the greeting carries identifying=True,
        # which is what routes the first chat turn into company identification.
        session_id = self.client.get(_BOOTSTRAP_URL).data['session_id']
        with mock.patch('hongwen.services.chat.identify_customer_from_llm', return_value=self.customer.id):
            first = self.client.post(
                _CHAT_URL, {'type': 'text', 'content': {'text': 'Acme'}, 'session_id': session_id}, format='json',
            )
            self.assertEqual(first.status_code, 200, first.content)
            self.assertIn('Acme', first.data['reply'])
            self.assertEqual(first.data['meta'], {'verifying_customer_id': self.customer.id})
            confirm = self.client.post(
                _CHAT_URL, {'type': 'text', 'content': {'text': '是的'}, 'session_id': session_id}, format='json',
            )
        self.assertEqual(confirm.status_code, 200)
        self.assertEqual(confirm.data['meta'], {'linked': True})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.customer_id, self.customer.id)

    def test_chat_triage_turn_short_circuits_rag(self):
        turn = TriageTurn(
            reply_text='请检查网线。',
            meta={'used_knowledge_base': False, 'knowledge_sources': [], 'quick_replies': [{'label_zh': '已检查', 'value': 'checked'}],
                  'triage': {'playbook_slug': 'no-internet', 'step_index': 0, 'kind': 'check', 'status': 'in_progress'}},
        )
        with mock.patch('hongwen.services.chat.start_or_advance', return_value=turn) as triage, \
             mock.patch('hongwen.services.chat.generate_reply_and_update_profile') as gen:
            res = self.client.post(
                _CHAT_URL, {'type': 'text', 'content': {'text': '设备上不了网', 'quick_value': 'checked'}}, format='json',
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['reply'], '请检查网线。')
        self.assertEqual(res.data['meta']['triage']['playbook_slug'], 'no-internet')
        self.assertEqual(res.data['meta']['quick_replies'], [{'label_zh': '已检查', 'value': 'checked'}])
        self.assertEqual(triage.call_args.kwargs['quick_value'], 'checked')
        gen.assert_not_called()


class RegenerateTests(_CustomerAPIMixin, TestCase):
    def test_regenerate_replaces_latest_reply(self):
        session, _user_msg, assistant = _seed_turn(self.user, 'sess_regen')
        with mock.patch('hongwen.services.chat.generate_reply_and_update_profile', side_effect=_fake_generate) as gen:
            res = self.client.post(
                _REGENERATE_URL, {'session_id': session.session_id, 'message_id': str(assistant.id)}, format='json',
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['reply'], _REPLY)
        self.assertTrue(res.data['meta']['regenerated'])
        self.assertFalse(Message.objects.filter(id=assistant.id).exists())
        self.assertTrue(gen.call_args.kwargs.get('regenerate'))
        latest = Message.objects.filter(session_id=session.session_id).order_by('-id').first()
        self.assertEqual(latest.role, 'assistant')
        self.assertEqual(json.loads(latest.content)['text'], _REPLY)

    def test_regenerate_stale_message_id_409(self):
        session, _user_msg, assistant = _seed_turn(self.user, 'sess_409')
        res = self.client.post(
            _REGENERATE_URL, {'session_id': session.session_id, 'message_id': str(assistant.id + 1)}, format='json',
        )
        self.assertEqual(res.status_code, 409)
        self.assertTrue(Message.objects.filter(id=assistant.id).exists())

    def test_regenerate_non_latest_assistant_422(self):
        session, _user_msg, _assistant = _seed_turn(self.user, 'sess_nonlatest')
        Message.objects.create(
            user=self.user, session_id=session.session_id, role='user', type='text',
            content=json.dumps({'text': '后续问题'}),
        )
        res = self.client.post(_REGENERATE_URL, {'session_id': session.session_id}, format='json')
        self.assertEqual(res.status_code, 422)

    def test_regenerate_empty_session_422(self):
        session = ChatSession.objects.create(session_id='sess_empty', user=self.user)
        res = self.client.post(_REGENERATE_URL, {'session_id': session.session_id}, format='json')
        self.assertEqual(res.status_code, 422)

    def test_regenerate_unlinked_customer_422(self):
        self.profile.customer = None
        self.profile.save(update_fields=['customer'])
        session, _u, _a = _seed_turn(self.user, 'sess_unlinked')
        res = self.client.post(_REGENERATE_URL, {'session_id': session.session_id}, format='json')
        self.assertEqual(res.status_code, 422)

    def test_regenerate_foreign_session_404(self):
        res = self.client.post(_REGENERATE_URL, {'session_id': 'sess_missing'}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_regenerate_retries_when_reply_identical(self):
        # The helpdesk regenerates a second time when the model returns the very
        # same text (main.py:2992); seed the old reply equal to the stub's reply.
        session, _u, _a = _seed_turn(self.user, 'sess_same', reply_text=_REPLY)
        with mock.patch('hongwen.services.chat.generate_reply_and_update_profile', side_effect=_fake_generate) as gen:
            res = self.client.post(_REGENERATE_URL, {'session_id': session.session_id}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(gen.call_count, 2)


class AuthGuardTests(_CustomerAPIMixin, TestCase):
    def test_tv_bump_invalidates_business_endpoints(self):
        token = _access_token(self.user)
        self.profile.token_version += 1
        self.profile.save(update_fields=['token_version'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.client.get(_BOOTSTRAP_URL).status_code, 401)
        self.assertEqual(self.client.get(_SESSIONS_URL).status_code, 401)
        self.assertEqual(
            self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': 'hi'}}, format='json').status_code,
            401,
        )

    def test_can_chat_flag_gates_chat_but_not_bootstrap(self):
        self.profile.can_chat = False
        self.profile.save(update_fields=['can_chat'])
        self.assertEqual(self.client.get(_BOOTSTRAP_URL).status_code, 200)
        self.assertEqual(self.client.get(_SESSIONS_URL).status_code, 403)
        self.assertEqual(
            self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': 'hi'}}, format='json').status_code,
            403,
        )

    def test_staff_token_forbidden_on_chat(self):
        staff = User.objects.create_user(username='staff2', password='pw')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(staff)}')
        self.assertEqual(
            self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': 'hi'}}, format='json').status_code,
            403,
        )

    def test_unauthenticated_chat_401(self):
        self.client.credentials()
        self.assertEqual(
            self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': 'hi'}}, format='json').status_code,
            401,
        )


# --- SSE streaming -------------------------------------------------------------


def _body(response):
    """Response bytes for both buffered and streaming (FileResponse) responses."""
    if getattr(response, 'streaming', False):
        return b''.join(response.streaming_content)
    return response.content


def _consume_sse(response):
    """Parse ``event: X\\ndata: {json}\\n\\n`` frames from a streaming response."""
    text = b''.join(response.streaming_content).decode('utf-8')
    frames = []
    for block in text.split('\n\n'):
        if not block.strip():
            continue
        lines = block.split('\n')
        event = lines[0][len('event: '):]
        data = json.loads(lines[1][len('data: '):])
        frames.append((event, data))
    return frames


def _fake_deltas(*, preparation, regenerate=False, temperature=0.2):
    yield '你好', 'dashscope/qwen-test'
    yield '世界', 'dashscope/qwen-test'


class ChatStreamTests(_CustomerAPIMixin, TransactionTestCase):
    # TransactionTestCase, not TestCase: the SSE generator performs a late DB
    # write in its ``finally`` when the client disconnects (res.close() throws
    # GeneratorExit). Under TestCase's single wrapped transaction that late
    # write leaves the PostgreSQL connection closed, poisoning every later test
    # in the class ("connection already closed"). TransactionTestCase gives each
    # test its own connection lifecycle, so the disconnect path is isolated.
    def _post_stream(self, payload=None):
        return self.client.post(
            _STREAM_URL, payload or {'type': 'text', 'content': {'text': '路由器亮红灯'}}, format='json',
        )

    def test_sse_frame_sequence_and_headers(self):
        with mock.patch('hongwen.services.chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('hongwen.services.sse.stream_reply_deltas', side_effect=_fake_deltas) as deltas:
            res = self._post_stream()
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res['Content-Type'], 'text/event-stream')
            self.assertEqual(res['Cache-Control'], 'no-cache')
            self.assertEqual(res['X-Accel-Buffering'], 'no')
            frames = _consume_sse(res)

        self.assertEqual([name for name, _ in frames], ['start', 'delta', 'delta', 'final', 'done'])
        start = frames[0][1]
        self.assertIn('session_id', start)
        self.assertTrue(start['session_id'].startswith('sess_'))
        self.assertEqual(start['meta']['used_knowledge_base'], True)
        self.assertEqual(start['meta']['knowledge_sources'], ['manual.pdf'])
        self.assertEqual(start['meta']['model_primary'], 'dashscope/qwen-test')
        self.assertEqual(start['meta']['model_fallback'], 'dashscope/qwen-backup')
        self.assertEqual(frames[1][1], {'text': '你好'})
        self.assertEqual(frames[2][1], {'text': '世界'})
        final = frames[3][1]
        self.assertEqual(final['reply'], '你好世界')
        self.assertEqual(final['session_id'], start['session_id'])
        self.assertEqual(final['meta']['model_used'], 'dashscope/qwen-test')
        self.assertEqual(frames[4][1], {'session_id': start['session_id']})
        self.assertEqual(deltas.call_args.kwargs['temperature'], 0.2)
        self.assertFalse(deltas.call_args.kwargs['regenerate'])

        stored = Message.objects.filter(session_id=start['session_id'], role='assistant').first()
        self.assertEqual(json.loads(stored.content)['text'], '你好世界')
        # Streaming never updates the learning profile (helpdesk parity).
        self.assertFalse(UserProfile.objects.filter(user=self.user).exists())

    def test_sse_moderation_violation_is_plain_json_400(self):
        with mock.patch.object(moderation, 'check_text', return_value=moderation.Suggest.RISKY):
            res = self._post_stream()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res['Content-Type'], 'application/json')
        self.assertIn('detail', res.data)
        self.assertFalse(Message.objects.filter(user=self.user, role='user').exists())

    def test_sse_output_moderation_replaces_final_only(self):
        def _check(text, openid):
            return moderation.Suggest.RISKY if text == '你好世界' else moderation.Suggest.PASS

        with mock.patch('hongwen.services.chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('hongwen.services.sse.stream_reply_deltas', side_effect=_fake_deltas), \
             mock.patch.object(moderation, 'check_text', side_effect=_check):
            frames = _consume_sse(self._post_stream())
        self.assertEqual([name for name, _ in frames], ['start', 'delta', 'delta', 'final', 'done'])
        final = frames[3][1]
        self.assertNotEqual(final['reply'], '你好世界')
        self.assertEqual(final['meta']['content_moderation'], 'replaced')
        stored = Message.objects.filter(session_id=final['session_id'], role='assistant').first()
        self.assertEqual(json.loads(stored.content)['text'], final['reply'])

    def test_sse_client_disconnect_persists_partial_reply(self):
        def _slow_deltas(*, preparation, regenerate=False, temperature=0.2):
            yield '第一段', 'dashscope/qwen-test'
            yield '第二段', 'dashscope/qwen-test'
            yield '第三段', 'dashscope/qwen-test'

        with mock.patch('hongwen.services.chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('hongwen.services.sse.stream_reply_deltas', side_effect=_slow_deltas):
            res = self._post_stream()
            iterator = iter(res.streaming_content)
            start_frame = next(iterator)  # start
            self.assertIn(b'event: start', start_frame)
            next(iterator)  # first delta
            # The WSGI server closes the response when the client goes away;
            # that throws GeneratorExit into the suspended generator.
            res.close()

        session_id = json.loads(start_frame.decode('utf-8').split('data: ', 1)[1])['session_id']
        stored = Message.objects.filter(session_id=session_id, role='assistant').first()
        self.assertIsNotNone(stored, 'partial reply must survive a disconnect')
        payload = json.loads(stored.content)
        self.assertEqual(payload['text'], '第一段')
        self.assertTrue(payload['meta']['partial'])

    def test_sse_broken_pipe_terminates_gracefully(self):
        def _broken_deltas(*, preparation, regenerate=False, temperature=0.2):
            yield '第一段', 'dashscope/qwen-test'
            raise BrokenPipeError('client went away')

        with mock.patch('hongwen.services.chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('hongwen.services.sse.stream_reply_deltas', side_effect=_broken_deltas):
            frames = _consume_sse(self._post_stream())
        self.assertEqual([name for name, _ in frames], ['start', 'delta'])
        stored = Message.objects.filter(user=self.user, role='assistant').first()
        self.assertEqual(json.loads(stored.content)['text'], '第一段')

    def test_sse_provider_error_emits_error_frame(self):
        def _broken_deltas(*, preparation, regenerate=False, temperature=0.2):
            raise RuntimeError('provider down')
            yield  # pragma: no cover - makes this a generator

        with mock.patch('hongwen.services.chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('hongwen.services.sse.stream_reply_deltas', side_effect=_broken_deltas):
            frames = _consume_sse(self._post_stream())
        self.assertEqual([name for name, _ in frames], ['start', 'error'])
        self.assertEqual(frames[1][1]['detail'], 'provider down')
        self.assertIn('session_id', frames[1][1])
        self.assertFalse(Message.objects.filter(user=self.user, role='assistant').exists())

    def test_sse_triage_branch_three_frames_no_delta(self):
        turn = TriageTurn(
            reply_text='请检查网线。',
            meta={'triage': {'playbook_slug': 'no-internet', 'step_index': 0, 'kind': 'check', 'status': 'in_progress'},
                  'quick_replies': []},
        )
        with mock.patch('hongwen.services.chat.start_or_advance', return_value=turn), \
             mock.patch('hongwen.services.chat.prepare_reply_generation') as prep:
            frames = _consume_sse(self._post_stream({'type': 'text', 'content': {'text': '设备上不了网'}}))
        self.assertEqual([name for name, _ in frames], ['start', 'final', 'done'])
        self.assertEqual(frames[0][1]['meta'], {'triage': turn.meta['triage']})
        self.assertEqual(frames[1][1]['reply'], '请检查网线。')
        prep.assert_not_called()

    def test_sse_identification_branch_three_frames(self):
        self.profile.customer = None
        self.profile.save(update_fields=['customer'])
        session_id = self.client.get(_BOOTSTRAP_URL).data['session_id']
        with mock.patch('hongwen.services.chat.identify_customer_from_llm', return_value=self.customer.id):
            frames = _consume_sse(self._post_stream(
                {'type': 'text', 'content': {'text': 'Acme'}, 'session_id': session_id},
            ))
        self.assertEqual([name for name, _ in frames], ['start', 'final', 'done'])
        self.assertEqual(frames[0][1]['meta'], {})
        self.assertIn('Acme', frames[1][1]['reply'])
        self.assertEqual(frames[1][1]['meta'], {'verifying_customer_id': self.customer.id})

    def test_regenerate_stream_409_and_success(self):
        session, _user_msg, assistant = _seed_turn(self.user, 'sess_regen_stream')
        stale = self.client.post(
            _REGENERATE_STREAM_URL, {'session_id': session.session_id, 'message_id': str(assistant.id + 1)},
            format='json',
        )
        self.assertEqual(stale.status_code, 409)

        with mock.patch('hongwen.services.chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('hongwen.services.sse.stream_reply_deltas', side_effect=_fake_deltas) as deltas:
            res = self.client.post(
                _REGENERATE_STREAM_URL, {'session_id': session.session_id, 'message_id': str(assistant.id)},
                format='json',
            )
            frames = _consume_sse(res)
        self.assertEqual([name for name, _ in frames], ['start', 'delta', 'delta', 'final', 'done'])
        self.assertTrue(frames[0][1]['meta']['regenerated'])
        self.assertTrue(frames[3][1]['meta']['regenerated'])
        self.assertEqual(deltas.call_args.kwargs['temperature'], 0.45)
        self.assertTrue(deltas.call_args.kwargs['regenerate'])
        self.assertFalse(Message.objects.filter(id=assistant.id).exists())


# --- uploads / transcribe / speak -------------------------------------------------


def _upload_file(name='photo.png', data=b'image-bytes', content_type='image/png'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, data, content_type=content_type)


class FileUploadTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    def test_upload_image_contract(self):
        res = self.client.post(_UPLOAD_URL, {'file': _upload_file(), 'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.data
        self.assertEqual(set(data.keys()), {'file_id', 'type', 'original_filename', 'url'})
        self.assertEqual(len(data['file_id']), 32)
        self.assertEqual(data['type'], 'image')
        self.assertEqual(data['original_filename'], 'photo.png')
        self.assertEqual(data['url'], f'/api/uploads/{data["file_id"]}__photo.png')
        upload = UploadFile.objects.get(file_id=data['file_id'])
        self.assertEqual(upload.file_type, 'image')
        self.assertEqual(upload.user_id, self.user.id)
        self.assertTrue(private_storage().exists(upload.file.name))

    def test_upload_audio_contract(self):
        res = self.client.post(
            _UPLOAD_URL, {'file': _upload_file('voice.mp3', b'audio', 'audio/mpeg'), 'type': 'audio'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['type'], 'audio')

    def test_upload_video_422(self):
        res = self.client.post(
            _UPLOAD_URL, {'file': _upload_file('clip.mp4', b'video', 'video/mp4'), 'type': 'video'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 422)
        self.assertFalse(UploadFile.objects.exists())

    def test_upload_invalid_type_422(self):
        res = self.client.post(_UPLOAD_URL, {'file': _upload_file(), 'type': 'document'}, format='multipart')
        self.assertEqual(res.status_code, 422)

    def test_upload_empty_file_422(self):
        res = self.client.post(_UPLOAD_URL, {'file': _upload_file(data=b''), 'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 422)

    def test_upload_missing_file_422(self):
        res = self.client.post(_UPLOAD_URL, {'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 422)

    def test_upload_over_15mb_413(self):
        big = _upload_file('big.png', b'x' * (CHAT_UPLOAD_MAX_BYTES + 1))
        res = self.client.post(_UPLOAD_URL, {'file': big, 'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 413)
        self.assertFalse(UploadFile.objects.exists())

    def test_upload_at_15mb_boundary_allowed(self):
        exact = _upload_file('exact.png', b'x' * CHAT_UPLOAD_MAX_BYTES)
        with mock.patch.object(moderation, 'check_image', return_value=moderation.Suggest.PASS):
            res = self.client.post(_UPLOAD_URL, {'file': exact, 'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)

    def test_upload_image_moderation_blocked_422(self):
        with mock.patch.object(moderation, 'check_image', return_value=moderation.Suggest.RISKY):
            res = self.client.post(_UPLOAD_URL, {'file': _upload_file(), 'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 422)
        self.assertFalse(UploadFile.objects.exists())

    def test_upload_gated_by_can_chat_not_can_upload(self):
        # Helpdesk parity: chat attachments check can_chat (main.py:1793); the
        # can_upload flag gates KB administration, not customer attachments.
        self.assertFalse(self.profile.can_upload)
        res = self.client.post(_UPLOAD_URL, {'file': _upload_file(), 'type': 'image'}, format='multipart')
        self.assertEqual(res.status_code, 200)
        self.profile.can_chat = False
        self.profile.save(update_fields=['can_chat'])
        denied = self.client.post(_UPLOAD_URL, {'file': _upload_file(), 'type': 'image'}, format='multipart')
        self.assertEqual(denied.status_code, 403)


class TranscribeTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    def _post(self, language='zh'):
        return self.client.post(
            _TRANSCRIBE_URL, {'file': _upload_file('voice.mp3', b'audio', 'audio/mpeg'), 'language': language},
            format='multipart',
        )

    def test_transcribe_contract(self):
        with mock.patch(
            'api.hongwen_views.transcribe_audio_with_fallback',
            return_value={'text': '  转写结果  ', 'model_used': 'dashscope/qwen3-asr-flash'},
        ) as asr:
            res = self._post()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data, {'text': '转写结果', 'model': 'dashscope/qwen3-asr-flash'})
        self.assertEqual(asr.call_args.kwargs['language'], 'zh')
        self.assertTrue(asr.call_args.kwargs['file_path'].endswith('.mp3'))

    def test_transcribe_empty_422(self):
        with mock.patch('api.hongwen_views.transcribe_audio_with_fallback', return_value={'text': '  ', 'model_used': 'm'}):
            res = self._post()
        self.assertEqual(res.status_code, 422)

    def test_transcribe_provider_failure_502(self):
        with mock.patch('api.hongwen_views.transcribe_audio_with_fallback', side_effect=RuntimeError('asr down')):
            res = self._post()
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.data['detail'], 'asr down')

    def test_transcribe_moderation_block_400(self):
        with mock.patch('api.hongwen_views.transcribe_audio_with_fallback', return_value={'text': '违规', 'model_used': 'm'}), \
             mock.patch.object(moderation, 'check_text', return_value=moderation.Suggest.RISKY):
            res = self._post()
        self.assertEqual(res.status_code, 400)

    def test_transcribe_missing_file_422(self):
        res = self.client.post(_TRANSCRIBE_URL, {'language': 'zh'}, format='multipart')
        self.assertEqual(res.status_code, 422)

    def test_transcribe_volcengine_public_share(self):
        with override_settings(PRIMARY_ASR_MODEL='volcengine/asr-test', PUBLIC_API_BASE_URL='https://api.example.com'), \
             mock.patch(
                 'api.hongwen_views.transcribe_audio_with_fallback',
                 return_value={'text': '结果', 'model_used': 'volcengine/asr-test'},
             ) as asr:
            res = self._post()
        self.assertEqual(res.status_code, 200, res.content)
        public_url = asr.call_args.kwargs['public_file_url']
        self.assertTrue(public_url.startswith('https://api.example.com/api/provider/audio/'))
        share_id = public_url.rsplit('/', 1)[1]
        # The share is removed as soon as transcription finishes (no lingering URL).
        self.assertIsNone(registries.load_provider_audio_share(share_id))

    def test_transcribe_volcengine_converts_non_mp3(self):
        fd, converted = tempfile.mkstemp(suffix='.mp3')
        os.close(fd)
        with override_settings(PRIMARY_ASR_MODEL='volcengine/asr-test', PUBLIC_API_BASE_URL='https://api.example.com'), \
             mock.patch('api.hongwen_views.convert_to_mp3', return_value=converted) as conv, \
             mock.patch('api.hongwen_views.transcribe_audio_with_fallback', return_value={'text': '结果', 'model_used': 'm'}):
            res = self.client.post(
                _TRANSCRIBE_URL, {'file': _upload_file('voice.amr', b'audio', 'audio/amr')}, format='multipart',
            )
        self.assertEqual(res.status_code, 200, res.content)
        conv.assert_called_once()
        self.assertFalse(os.path.exists(converted))  # converted temp cleaned up

    def test_transcribe_volcengine_requires_public_base(self):
        with override_settings(PRIMARY_ASR_MODEL='volcengine/asr-test', PUBLIC_API_BASE_URL=''):
            res = self._post()
        self.assertEqual(res.status_code, 502)
        self.assertIn('PUBLIC_API_BASE_URL', res.data['detail'])


class SpeakTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    def test_speak_contract(self):
        result = {
            'audio_url': '/api/uploads/tts/tts_abc.mp3', 'stored_path': 'hongwen/tts/tts_abc.mp3',
            '_model_used': 'dashscope/cosyvoice-v3-flash', '_voice_used': 'longanyang',
        }
        with mock.patch('api.hongwen_views.synthesize_speech_with_fallback', return_value=result) as tts:
            res = self.client.post(
                _SPEAK_URL, {'text': '你好', 'message_id': '12', 'language_type': 'zh'}, format='json',
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data, {
            'audio_url': '/api/uploads/tts/tts_abc.mp3',
            'model': 'dashscope/cosyvoice-v3-flash',
            'voice': 'longanyang',
            'message_id': '12',
        })
        self.assertEqual(tts.call_args.kwargs['text'], '你好')
        self.assertEqual(tts.call_args.kwargs['language_type'], 'zh')

    def test_speak_empty_text_422(self):
        res = self.client.post(_SPEAK_URL, {'text': '   '}, format='json')
        self.assertEqual(res.status_code, 422)

    def test_speak_provider_failure_502(self):
        with mock.patch('api.hongwen_views.synthesize_speech_with_fallback', side_effect=RuntimeError('tts down')):
            res = self.client.post(_SPEAK_URL, {'text': '你好'}, format='json')
        self.assertEqual(res.status_code, 502)


class SpeakStreamTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    def _post(self, text='你好'):
        with mock.patch('api.hongwen_views.stream_speech_audio_with_fallback', return_value=iter([b'ID3', b'-mp3'])):
            return self.client.post(_SPEAK_STREAM_URL, {'text': text, 'message_id': '7'}, format='json')

    def test_stream_registry_and_playback(self):
        res = self._post()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(set(res.data.keys()), {'stream_url', 'model', 'voice', 'message_id'})
        stream_url = res.data['stream_url']
        self.assertTrue(stream_url.startswith('/api/v1/hongwen/speak/stream/'))
        stream_id = stream_url.rsplit('/', 1)[1]
        self.assertEqual(res.data['message_id'], '7')

        entry = registries.load_tts_stream(stream_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['audio'], b'ID3-mp3')
        self.assertEqual(entry['external_id'], self.profile.external_id)

        play = self.client.get(stream_url)
        self.assertEqual(play.status_code, 200)
        self.assertEqual(play['Content-Type'], 'audio/mpeg')
        self.assertEqual(play.content, b'ID3-mp3')
        # Replay within the TTL still works (cache registry, not a one-shot stream).
        self.assertEqual(self.client.get(stream_url).content, b'ID3-mp3')

    def test_stream_playback_with_query_token(self):
        stream_url = self._post().data['stream_url']
        token = _access_token(self.user)
        self.client.credentials()
        res = self.client.get(f'{stream_url}?access_token={token}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'ID3-mp3')

    def test_stream_playback_requires_auth(self):
        stream_url = self._post().data['stream_url']
        self.client.credentials()
        self.assertEqual(self.client.get(stream_url).status_code, 401)

    def test_stream_foreign_owner_404(self):
        stream_url = self._post().data['stream_url']
        other, _ = _make_customer(username='wx_other3', openid='r' * 28)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {_access_token(other)}')
        self.assertEqual(self.client.get(stream_url).status_code, 404)

    def test_stream_unknown_id_404(self):
        self.assertEqual(self.client.get('/api/v1/hongwen/speak/stream/deadbeef').status_code, 404)

    def test_stream_expired_404(self):
        stream_url = self._post().data['stream_url']
        stream_id = stream_url.rsplit('/', 1)[1]
        # Simulate the 15-minute TTL elapsing.
        cache.delete(f'hongwen:tts_stream:{stream_id}')
        self.assertEqual(self.client.get(stream_url).status_code, 404)

    def test_stream_zero_ttl_expires_immediately(self):
        with override_settings(HONGWEN_REGISTRY_TTL_SECONDS=0):
            stream_url = self._post().data['stream_url']
        self.assertEqual(self.client.get(stream_url).status_code, 404)

    def test_stream_empty_text_422(self):
        res = self.client.post(_SPEAK_STREAM_URL, {'text': ''}, format='json')
        self.assertEqual(res.status_code, 422)

    def test_stream_provider_failure_502(self):
        with mock.patch('api.hongwen_views.stream_speech_audio_with_fallback', side_effect=RuntimeError('tts down')):
            res = self.client.post(_SPEAK_STREAM_URL, {'text': '你好'}, format='json')
        self.assertEqual(res.status_code, 502)

    def test_stream_ip_throttle_429(self):
        with override_settings(HONGWEN_SPEAK_STREAM_IP_MAX_HITS=1):
            first = self._post()
            second = self._post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)


class ProtectedDownloadTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.upload = store_upload_bytes(
            user=self.user, filename='photo.png', data=b'image-bytes', file_type='image', mime_type='image/png',
        )
        self.relative = f'{self.upload.file_id}__photo.png'
        self.url = f'/api/uploads/{self.relative}'

    def test_download_with_header_token(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_body(res), b'image-bytes')
        self.assertEqual(res['Content-Type'], 'image/png')

    def test_download_with_query_token(self):
        token = _access_token(self.user)
        self.client.credentials()
        res = self.client.get(f'{self.url}?access_token={token}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_body(res), b'image-bytes')

    def test_download_without_token_401(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_download_stale_tv_401(self):
        token = _access_token(self.user)
        self.profile.token_version += 1
        self.profile.save(update_fields=['token_version'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.get(f'{self.url}?access_token={token}').status_code, 401)

    def test_download_namespaced_alias(self):
        res = self.client.get(f'/api/v1/hongwen/uploads/{self.relative}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_body(res), b'image-bytes')

    def test_download_tts_subdir(self):
        private_storage().save('hongwen/tts/song.mp3', ContentFile(b'mp3-bytes'))
        res = self.client.get('/api/uploads/tts/song.mp3')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_body(res), b'mp3-bytes')
        self.assertEqual(res['Content-Type'], 'audio/mpeg')

    def test_download_missing_404(self):
        self.assertEqual(self.client.get('/api/uploads/nope.png').status_code, 404)

    def test_download_traversal_404(self):
        self.assertEqual(self.client.get('/api/uploads/kb_assets/../../secret.png').status_code, 404)

    def test_provider_audio_share_capability_url(self):
        import os
        fd, temp_path = tempfile.mkstemp(suffix='.mp3')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(b'share-audio')
        self.addCleanup(lambda: os.path.exists(temp_path) and os.remove(temp_path))
        share_id = registries.register_provider_audio_share(temp_path)
        # Unauthenticated: the provider fetches this URL, the uuid + TTL are the guard.
        self.client.credentials()
        res = self.client.get(f'/api/provider/audio/{share_id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_body(res), b'share-audio')
        registries.remove_provider_audio_share(share_id)
        self.assertEqual(self.client.get(f'/api/provider/audio/{share_id}').status_code, 404)


class HistoryExpansionTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    def _seed_mixed_session(self):
        image = store_upload_bytes(user=self.user, filename='pic.png', data=b'img', file_type='image', mime_type='image/png')
        audio = store_upload_bytes(
            user=self.user, filename='voice.mp3', data=b'aud', file_type='audio', mime_type='audio/mpeg', duration_seconds=7,
        )
        ChatSession.objects.create(session_id='sess_mixed', user=self.user)
        Message.objects.create(
            user=self.user, session_id='sess_mixed', role='assistant', type='text',
            content=json.dumps({'text': '您好', 'meta': {}}),
        )
        message = Message.objects.create(
            user=self.user, session_id='sess_mixed', role='user', type='mixed',
            content=json.dumps({
                'type': 'mixed',
                'content': {'text': '看这个', 'image_file_ids': [image.file_id], 'audio_file_id': audio.file_id},
                'session_id': 'sess_mixed',
            }),
        )
        return image, audio, message

    def test_mixed_expands_to_image_audio_text_rows(self):
        image, audio, message = self._seed_mixed_session()
        res = self.client.get(_BOOTSTRAP_URL, {'session_id': 'sess_mixed'})
        self.assertEqual(res.status_code, 200)
        rows = res.data['messages']
        self.assertEqual(len(rows), 4)  # greeting + image + audio + text
        self.assertEqual(rows[1]['id'], f'{message.id}:image:0')
        self.assertEqual(rows[1]['type'], 'image')
        self.assertEqual(rows[1]['content']['filePath'], f'/api/uploads/{image.file_id}__pic.png')
        self.assertEqual(rows[1]['content']['previewPath'], rows[1]['content']['filePath'])
        self.assertEqual(rows[2]['id'], f'{message.id}:audio')
        self.assertEqual(rows[2]['type'], 'audio')
        self.assertEqual(rows[2]['content']['playable'], True)
        self.assertEqual(rows[2]['content']['filePath'], f'/api/uploads/{audio.file_id}__voice.mp3')
        self.assertEqual(rows[2]['content']['duration'], 7)
        self.assertEqual(rows[2]['content']['transcription'], '看这个')
        self.assertEqual(rows[3]['id'], f'{message.id}:text')
        self.assertEqual(rows[3]['content'], '看这个')

    def test_image_without_upload_falls_back_to_text(self):
        ChatSession.objects.create(session_id='sess_img', user=self.user)
        Message.objects.create(
            user=self.user, session_id='sess_img', role='user', type='image',
            content=json.dumps({'type': 'image', 'content': {'file_id': 'missing'}, 'session_id': 'sess_img'}),
        )
        rows = self.client.get(_BOOTSTRAP_URL, {'session_id': 'sess_img'}).data['messages']
        self.assertEqual(rows[0]['type'], 'text')
        self.assertEqual(rows[0]['content'], '图片消息')

    def test_assistant_meta_attachments_caption_normalized(self):
        ChatSession.objects.create(session_id='sess_meta', user=self.user)
        Message.objects.create(
            user=self.user, session_id='sess_meta', role='assistant', type='text',
            content=json.dumps({'text': '答案', 'meta': {'attachments': [{'source': 'manual.pdf', 'page_or_section': 'page_1'}]}}),
        )
        res = self.client.get(_BOOTSTRAP_URL, {'session_id': 'sess_meta'})
        meta = res.data['messages'][0]['meta']
        self.assertEqual(meta['attachments'][0]['caption'], 'manual.pdf · page_1')

    def test_pc_client_branch_shape(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {_access_token(self.user)}', HTTP_HELPDESK_CLIENT_TYPE='pc',
        )
        res = self.client.get(_BOOTSTRAP_URL)
        self.assertEqual(res.status_code, 200)
        self.assertIn('chat_history', res.data)
        self.assertIn('latest_assistant_meta', res.data)
        self.assertNotIn('messages', res.data)

    def test_form_message_history_and_summary(self):
        ChatSession.objects.create(session_id='sess_form', user=self.user)
        Message.objects.create(
            user=self.user, session_id='sess_form', role='user', type='form',
            content=json.dumps({'type': 'form', 'content': {'category': '网络', 'detail': '断网'}, 'session_id': 'sess_form'}),
        )
        res = self.client.get(_BOOTSTRAP_URL, {'session_id': 'sess_form'})
        row = res.data['messages'][0]
        self.assertEqual(row['type'], 'form')
        self.assertEqual(row['content'], {'category': '网络', 'detail': '断网'})
        self.assertEqual(res.data['sessions'][0]['title'], '网络: 断网')


class IdentificationServiceTests(TestCase):
    """chat.identify_customer_from_llm (main.py:2477) with the LLM stubbed."""

    def setUp(self):
        self.customer = Customer.objects.create(name='Acme')

    def test_identify_returns_numeric_id(self):
        from hongwen.services import chat
        completion = {'choices': [{'message': {'content': str(self.customer.id)}}]}
        with mock.patch('hongwen.services.chat.chat_completion', return_value=completion):
            self.assertEqual(chat.identify_customer_from_llm('我在 Acme'), self.customer.id)

    def test_identify_none_for_non_numeric(self):
        from hongwen.services import chat
        completion = {'choices': [{'message': {'content': 'NONE'}}]}
        with mock.patch('hongwen.services.chat.chat_completion', return_value=completion):
            self.assertIsNone(chat.identify_customer_from_llm('不知道'))

    def test_identify_no_customers_short_circuits(self):
        from hongwen.services import chat
        Customer.objects.all().delete()
        with mock.patch('hongwen.services.chat.chat_completion') as completion:
            self.assertIsNone(chat.identify_customer_from_llm('Acme'))
        completion.assert_not_called()

    def test_identify_provider_error_degrades_to_none(self):
        from hongwen.services import chat
        with mock.patch('hongwen.services.chat.chat_completion', side_effect=RuntimeError('llm down')):
            self.assertIsNone(chat.identify_customer_from_llm('Acme'))


class GetPhoneServiceTests(TestCase):
    def setUp(self):
        self.user, self.profile = _make_customer(username='wx_phone')

    def test_get_phone_number_persists_mobile(self):
        from hongwen.services import auth
        response = mock.Mock()
        response.json.return_value = {'phone_info': {'phoneNumber': '13800138000'}}
        with mock.patch.object(auth.wechat_token, 'get_access_token', return_value='token'), \
             mock.patch.object(auth.requests, 'post', return_value=response) as post:
            phone = auth.get_phone_number(self.user, 'code')
        self.assertEqual(phone, '13800138000')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.mobile, '13800138000')
        self.assertEqual(post.call_args.kwargs['json'], {'code': 'code'})
        self.assertEqual(post.call_args.kwargs['params'], {'access_token': 'token'})

    def test_get_phone_number_wechat_refused(self):
        from hongwen.services import auth
        response = mock.Mock()
        response.json.return_value = {'errcode': 40029, 'errmsg': 'invalid code'}
        with mock.patch.object(auth.wechat_token, 'get_access_token', return_value='token'), \
             mock.patch.object(auth.requests, 'post', return_value=response):
            with self.assertRaises(auth.HongwenPhoneError):
                auth.get_phone_number(self.user, 'bad')

    def test_get_phone_number_no_credentials(self):
        from hongwen.services import auth
        with mock.patch.object(auth.wechat_token, 'get_access_token', return_value=None):
            with self.assertRaises(auth.HongwenPhoneUnavailable):
                auth.get_phone_number(self.user, 'code')


@skipUnless(connection.vendor == 'postgresql', 'pgvector retrieval path needs PostgreSQL')
class ChatKBPostgresTests(_PrivateMediaMixin, _CustomerAPIMixin, TestCase):
    """End-to-end KB retrieval through the chat view on the pgvector SQL path.

    Embeddings are stubbed (hash vectors) so no provider key is needed; the
    SQLite brute-force path is covered by hongwen/tests_services_vector_store.py.
    """

    def test_chat_retrieves_kb_on_postgres(self):
        document = KBDocument.objects.create(path='manual.txt', source_type='txt', content_hash='h', customer=self.customer)
        KBChunk.objects.create(
            document=document, chunk_index=0, content='路由器亮红灯先重启', chunk_type='text',
            embedding=stub_vector('路由器亮红灯先重启'), source_path='manual.txt', page_or_section='',
        )
        reply_json = json.dumps({'assistant_reply': _REPLY, 'updated_weak_points': '{}', 'updated_suggested_solutions': '{}'})
        completion = {'choices': [{'message': {'content': reply_json}}], '_model_used': 'dashscope/qwen-test'}
        # Only the LLM completion is stubbed: prepare_reply_generation runs for
        # real and search_kb takes the pgvector SQL path with hash embeddings.
        with mock.patch('hongwen.services.kb.vector_store.embed_text', side_effect=lambda *, model, texts, **_: [stub_vector(t) for t in texts]), \
             mock.patch('hongwen.services.llm.orchestrator.chat_completion_with_fallback', return_value=completion):
            res = self.client.post(_CHAT_URL, {'type': 'text', 'content': {'text': '路由器亮红灯'}}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['reply'], _REPLY)
        self.assertTrue(res.data['meta']['used_knowledge_base'])
        self.assertIn('manual.txt', res.data['meta']['knowledge_sources'])