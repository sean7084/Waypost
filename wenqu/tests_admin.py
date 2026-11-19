"""Tests for the Wenqu staff admin pages (PR5): access gate, customer/user CRUD,
permission bits + token-version bump, KB upload/list/preview/replace/delete and
the staff-authenticated PC chat console with SSE streaming.

The chat tests mock the LLM/KB service boundary (prepare_reply_generation /
stream_reply_deltas) so no provider credentials or network are needed; the KB
upload tests mock ingest_one/replace_document (embedding is a provider call)
while pinning WENQU_KB_SOURCES_DIR to a temp directory.
"""
import json
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AdminRole
from wenqu.models import ChatSession, Customer, KBChunk, KBDocument, Message, WenquProfile
from wenqu.services.llm.orchestrator import ReplyPreparation

User = get_user_model()


def _superadmin(username='root'):
    return User.objects.create_user(username=username, password='pw', is_superuser=True)


def _it_admin(username='it'):
    AdminRole.objects.get_or_create(code='it_administrator', defaults={'name': 'IT Administrator'})
    user = User.objects.create_user(username=username, password='pw')
    user.set_admin_roles(['it_administrator'])
    return user


def _plain(username='plain'):
    return User.objects.create_user(username=username, password='pw')


def _customer_user(username='wx_acme', customer=None):
    user = User.objects.create_user(username=username, password='pw')
    profile = WenquProfile.objects.create(user=user, external_id=username, customer=customer)
    return user, profile


def _preparation():
    """A ReplyPreparation that needs no provider: empty KB, plain text turn."""
    return ReplyPreparation(
        primary_model='test/primary',
        fallback_model='test/fallback',
        user_profile={'weak_points': '{}', 'suggested_solutions': '{}', 'notes': ''},
        knowledge_base=[],
        kb_attachments=[],
        user_message={'role': 'user', 'content': 'hello'},
        guidance_payload={'role': 'user', 'content': '{}'},
        base_meta={'used_knowledge_base': False, 'knowledge_sources': [], 'attachments': [],
                   'image_paths': [], 'model_used': 'test/primary',
                   'model_primary': 'test/primary', 'model_fallback': 'test/fallback'},
    )


def _sse_frames(response):
    """Consume a StreamingHttpResponse into (event, data) pairs."""
    body = b''.join(response.streaming_content).decode('utf-8')
    frames = []
    for block in body.split('\n\n'):
        block = block.strip()
        if not block:
            continue
        event = 'message'
        data_lines = []
        for line in block.split('\n'):
            if line.startswith('event:'):
                event = line[len('event:'):].strip()
            elif line.startswith('data:'):
                data_lines.append(line[len('data:'):].strip())
        if data_lines:
            frames.append((event, json.loads('\n'.join(data_lines))))
    return frames


class AdminAccessTests(TestCase):
    """WenquAdminMixin mirrors invoices: denial is messages + dashboard redirect."""

    def setUp(self):
        self.pages = [
            reverse('wenqu:customer_list'),
            reverse('wenqu:user_list'),
            reverse('wenqu:file_upload'),
            reverse('wenqu:chatbot'),
        ]

    def test_anonymous_redirected_to_dashboard(self):
        for url in self.pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('dashboard:dashboard'))

    def test_plain_user_denied_with_message(self):
        self.client.force_login(_plain())
        response = self.client.get(self.pages[0], follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse('dashboard:dashboard'))
        self.assertContains(response, 'You do not have access to Wenqu administration.')

    def test_viewer_role_denied(self):
        AdminRole.objects.get_or_create(code='viewer', defaults={'name': 'Viewer'})
        user = _plain('viewer1')
        user.set_admin_roles(['viewer'])
        self.client.force_login(user)
        for url in self.pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('dashboard:dashboard'))

    def test_wenqu_customer_denied(self):
        customer_user, _profile = _customer_user()
        self.client.force_login(customer_user)
        for url in self.pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)

    def test_superadmin_reaches_every_page(self):
        self.client.force_login(_superadmin())
        for url in self.pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_it_administrator_reaches_every_page(self):
        self.client.force_login(_it_admin())
        for url in self.pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_nav_entry_visible_only_to_wenqu_admins(self):
        self.client.force_login(_superadmin())
        response = self.client.get(reverse('wenqu:customer_list'))
        self.assertContains(response, reverse('wenqu:file_upload'))
        self.assertContains(response, 'Wenqu')
        self.client.force_login(_plain('navplain'))
        response = self.client.get(reverse('dashboard:dashboard'))
        self.assertNotContains(response, reverse('wenqu:customer_list'))


class CustomerCRUDTests(TestCase):
    def setUp(self):
        self.admin = _superadmin()
        self.client.force_login(self.admin)

    def test_create_customer(self):
        response = self.client.post(reverse('wenqu:customer_create'), {'name': 'Acme', 'notes': 'printer fleet'})
        self.assertRedirects(response, reverse('wenqu:customer_list'))
        customer = Customer.objects.get(name='Acme')
        self.assertEqual(customer.notes, 'printer fleet')

    def test_duplicate_name_rejected(self):
        Customer.objects.create(name='Acme')
        response = self.client.post(reverse('wenqu:customer_create'), {'name': 'Acme', 'notes': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertEqual(Customer.objects.filter(name='Acme').count(), 1)

    def test_edit_customer(self):
        customer = Customer.objects.create(name='Acme', notes='old')
        response = self.client.post(reverse('wenqu:customer_edit', args=[customer.pk]), {'name': 'Acme Corp', 'notes': 'new'})
        self.assertRedirects(response, reverse('wenqu:customer_list'))
        customer.refresh_from_db()
        self.assertEqual(customer.name, 'Acme Corp')
        self.assertEqual(customer.notes, 'new')

    def test_delete_customer_unscopes_children(self):
        customer = Customer.objects.create(name='Acme')
        _user, profile = _customer_user(customer=customer)
        doc = KBDocument.objects.create(path='acme.txt', source_type='txt', content_hash='h', customer=customer)
        response = self.client.post(reverse('wenqu:customer_delete', args=[customer.pk]))
        self.assertRedirects(response, reverse('wenqu:customer_list'))
        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())
        profile.refresh_from_db()
        doc.refresh_from_db()
        self.assertIsNone(profile.customer_id)
        self.assertIsNone(doc.customer_id)

    def test_list_search(self):
        Customer.objects.create(name='Acme')
        Customer.objects.create(name='Globex')
        response = self.client.get(reverse('wenqu:customer_list'), {'search': 'acm'})
        self.assertEqual([c.name for c in response.context['customers']], ['Acme'])


class UserAdminTests(TestCase):
    def setUp(self):
        self.admin = _superadmin()
        self.client.force_login(self.admin)
        self.customer = Customer.objects.create(name='Acme')

    def test_create_customer_user_with_password(self):
        response = self.client.post(reverse('wenqu:user_create'), {
            'username': 'acme_ops', 'password': 'secret123', 'customer': self.customer.pk,
            'nickname': 'Ops', 'mobile': '13800000000',
            'can_chat': 'on', 'can_upload': 'on',
        })
        self.assertRedirects(response, reverse('wenqu:user_list'))
        profile = WenquProfile.objects.get(external_id='acme_ops')
        self.assertEqual(profile.customer_id, self.customer.pk)
        self.assertTrue(profile.can_chat)
        self.assertTrue(profile.can_upload)
        self.assertFalse(profile.can_manage_users)
        self.assertTrue(profile.user.check_password('secret123'))
        self.assertEqual(profile.token_version, 0)

    def test_create_without_password_sets_unusable(self):
        self.client.post(reverse('wenqu:user_create'), {'username': 'wx_only', 'can_chat': 'on'})
        profile = WenquProfile.objects.get(external_id='wx_only')
        self.assertFalse(profile.user.has_usable_password())
        self.assertIsNone(profile.customer_id)

    def test_duplicate_username_rejected(self):
        _customer_user('taken')
        response = self.client.post(reverse('wenqu:user_create'), {'username': 'taken'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)

    def test_edit_permission_bits(self):
        _user, profile = _customer_user(customer=self.customer)
        response = self.client.post(reverse('wenqu:user_edit', args=[profile.pk]), {
            'customer': '', 'nickname': 'Changed', 'mobile': '',
            'can_chat': 'on', 'can_manage_users': 'on', 'can_manage_customers': 'on',
        })
        self.assertRedirects(response, reverse('wenqu:user_list'))
        profile.refresh_from_db()
        self.assertIsNone(profile.customer_id)
        self.assertEqual(profile.nickname, 'Changed')
        self.assertTrue(profile.can_chat)
        self.assertFalse(profile.can_upload)
        self.assertTrue(profile.can_manage_users)
        self.assertTrue(profile.can_manage_customers)

    def test_bump_token_version_forces_signout(self):
        _user, profile = _customer_user()
        url = reverse('wenqu:user_bump_token', args=[profile.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse('wenqu:user_list'))
        profile.refresh_from_db()
        self.assertEqual(profile.token_version, 1)
        self.client.post(url)
        profile.refresh_from_db()
        self.assertEqual(profile.token_version, 2)

    def test_bump_requires_post(self):
        _user, profile = _customer_user()
        response = self.client.get(reverse('wenqu:user_bump_token', args=[profile.pk]))
        self.assertEqual(response.status_code, 405)
        profile.refresh_from_db()
        self.assertEqual(profile.token_version, 0)

    def test_delete_customer_user_removes_account(self):
        user, profile = _customer_user()
        response = self.client.post(reverse('wenqu:user_delete', args=[profile.pk]))
        self.assertRedirects(response, reverse('wenqu:user_list'))
        self.assertFalse(WenquProfile.objects.filter(pk=profile.pk).exists())
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_list_and_search(self):
        _customer_user('wx_findme')
        _customer_user('wx_other')
        response = self.client.get(reverse('wenqu:user_list'), {'search': 'findme'})
        self.assertEqual([p.external_id for p in response.context['profiles']], ['wx_findme'])

    def test_non_admin_cannot_bump(self):
        _user, profile = _customer_user()
        self.client.force_login(_plain('intruder'))
        response = self.client.post(reverse('wenqu:user_bump_token', args=[profile.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))
        profile.refresh_from_db()
        self.assertEqual(profile.token_version, 0)


@override_settings(WENQU_TRIAGE_ENABLED=False)
class KBAdminTests(TestCase):
    def setUp(self):
        self.admin = _superadmin()
        self.client.force_login(self.admin)
        self.customer = Customer.objects.create(name='Acme')
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb_dir = os.path.join(self._tmp.name, 'kb_sources')
        self._settings = override_settings(WENQU_KB_SOURCES_DIR=self.kb_dir)
        self._settings.enable()
        self.addCleanup(self._settings.disable)

    def _upload(self, name='guide.txt', content=b'how to replace the paper roll', customer_id=None, conflict='keep_both'):
        data = {'conflict_mode': conflict, 'files': SimpleUploadedFile(name, content)}
        if customer_id is not None:
            data['customer_id'] = customer_id
        return self.client.post(reverse('wenqu:kb_upload'), data)

    def _fake_ingest(self, path, customer_id=None, **kwargs):
        from wenqu.services.kb.paths import to_stored_path
        return KBDocument.objects.create(
            path=to_stored_path(path), source_type='txt', content_hash='fakehash', customer_id=customer_id,
        )

    def test_upload_stores_source_and_ingests(self):
        with mock.patch('wenqu.services.kb.ingest.ingest_one', side_effect=self._fake_ingest) as ingest_one:
            response = self._upload(customer_id=self.customer.pk)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['success_count'], 1)
        self.assertEqual(payload['failure_count'], 0)
        self.assertEqual(len(payload['documents']), 1)
        self.assertEqual(payload['documents'][0]['display_name'], 'guide.txt')
        ingest_one.assert_called_once()
        stored_path = ingest_one.call_args[0][0]
        self.assertTrue(os.path.isfile(stored_path))
        self.assertEqual(os.path.dirname(stored_path), self.kb_dir)
        self.assertTrue(os.path.basename(stored_path).endswith('_guide.txt'))
        self.assertEqual(KBDocument.objects.get().customer_id, self.customer.pk)

    def test_upload_failure_cleans_orphan_source(self):
        with mock.patch('wenqu.services.kb.ingest.ingest_one', side_effect=RuntimeError('embed down')):
            response = self._upload()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['success_count'], 0)
        self.assertEqual(payload['failure_count'], 1)
        self.assertEqual(os.listdir(self.kb_dir), [])
        self.assertFalse(KBDocument.objects.exists())

    def test_upload_rejects_unsupported_extension(self):
        response = self._upload(name='evil.exe', content=b'MZ')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(KBDocument.objects.exists())

    def test_overwrite_mode_replaces_same_name_document(self):
        doc = KBDocument.objects.create(path='old_guide.txt', source_type='txt', content_hash='h', customer_id=None)
        with mock.patch('wenqu.services.kb.ingest.find_customer_document_by_display_name', return_value=doc) as find, \
             mock.patch('wenqu.services.kb.ingest.replace_document', return_value=doc) as replace, \
             mock.patch('wenqu.services.kb.ingest.ingest_one') as ingest_one:
            response = self._upload(conflict='overwrite')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['action'], 'replaced')
        find.assert_called_once()
        replace.assert_called_once()
        ingest_one.assert_not_called()

    def test_document_list_scoped_by_customer(self):
        KBDocument.objects.create(path='global.txt', source_type='txt', content_hash='h')
        KBDocument.objects.create(path='acme.txt', source_type='txt', content_hash='h', customer=self.customer)
        response = self.client.get(reverse('wenqu:kb_documents'))
        self.assertEqual([d['display_name'] for d in response.json()['documents']], ['global.txt'])
        response = self.client.get(reverse('wenqu:kb_documents'), {'customer_id': self.customer.pk})
        self.assertEqual([d['display_name'] for d in response.json()['documents']], ['acme.txt'])

    def test_preview_returns_chunks(self):
        doc = KBDocument.objects.create(path='guide.txt', source_type='txt', content_hash='h')
        KBChunk.objects.create(document=doc, chunk_index=0, content='replace the paper roll', embedding=[0.0], source_path='guide.txt')
        response = self.client.get(reverse('wenqu:kb_document_preview', args=[doc.pk]))
        payload = response.json()
        self.assertEqual(payload['display_name'], 'guide.txt')
        self.assertEqual(payload['chunk_count'], 1)
        self.assertEqual(payload['chunks'][0]['snippet'], 'replace the paper roll')

    def test_preview_foreign_scope_404(self):
        doc = KBDocument.objects.create(path='acme.txt', source_type='txt', content_hash='h', customer=self.customer)
        response = self.client.get(reverse('wenqu:kb_document_preview', args=[doc.pk]))
        self.assertEqual(response.status_code, 404)

    def test_download_streams_source_file(self):
        os.makedirs(self.kb_dir, exist_ok=True)
        stored = os.path.join(self.kb_dir, 'abc_guide.txt')
        with open(stored, 'wb') as fh:
            fh.write(b'paper roll instructions')
        doc = KBDocument.objects.create(path='abc_guide.txt', source_type='txt', content_hash='h')
        response = self.client.get(reverse('wenqu:kb_document_download', args=[doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'paper roll instructions')
        self.assertIn('inline', response['Content-Disposition'])

    def test_replace_uses_ingest_service(self):
        doc = KBDocument.objects.create(path='old.txt', source_type='txt', content_hash='h')
        with mock.patch('wenqu.services.kb.ingest.replace_document', return_value=doc) as replace:
            response = self.client.post(
                reverse('wenqu:kb_document_replace', args=[doc.pk]),
                {'file': SimpleUploadedFile('new.txt', b'fresh content')},
            )
        self.assertEqual(response.status_code, 200)
        replace.assert_called_once()
        self.assertEqual(replace.call_args.kwargs['document'], doc)

    def test_delete_removes_rows_and_source(self):
        os.makedirs(self.kb_dir, exist_ok=True)
        stored = os.path.join(self.kb_dir, 'gone.txt')
        with open(stored, 'wb') as fh:
            fh.write(b'bye')
        doc = KBDocument.objects.create(path='gone.txt', source_type='txt', content_hash='h')
        KBChunk.objects.create(document=doc, chunk_index=0, content='bye', embedding=[0.0], source_path='gone.txt')
        response = self.client.post(reverse('wenqu:kb_document_delete', args=[doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['documents'], [])
        self.assertFalse(KBDocument.objects.exists())
        self.assertFalse(KBChunk.objects.exists())
        self.assertFalse(os.path.exists(stored))

    def test_kb_page_renders_initial_documents(self):
        KBDocument.objects.create(path='guide.txt', source_type='txt', content_hash='h')
        response = self.client.get(reverse('wenqu:file_upload'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['initial_documents']), 1)
        self.assertContains(response, 'kb-initial-documents')

    def test_non_admin_upload_denied(self):
        self.client.force_login(_plain('kbintruder'))
        response = self._upload()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))
        self.assertFalse(KBDocument.objects.exists())


@override_settings(WENQU_TRIAGE_ENABLED=False)
class AdminChatTests(TestCase):
    """The PC console is session-authenticated staff surface, not the customer API.

    Staff have no WenquProfile, so the customer JWT endpoints would reject them;
    these views gate on can_manage_wenqu and call the services directly. The LLM
    boundary (prepare_reply_generation / stream_reply_deltas) is mocked - the
    SSE generators, persistence and moderation wrappers run for real.
    """

    def setUp(self):
        self.admin = _superadmin()
        self.client.force_login(self.admin)

    def _post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json')

    def test_root_redirects_to_chatbot(self):
        response = self.client.get(reverse('wenqu:root'))
        self.assertRedirects(response, reverse('wenqu:chatbot'))

    def test_chatbot_bootstraps_session_without_profile(self):
        self.assertFalse(WenquProfile.objects.filter(user=self.admin).exists())
        response = self.client.get(reverse('wenqu:chatbot'))
        self.assertEqual(response.status_code, 200)
        session = ChatSession.objects.get(user=self.admin)
        self.assertTrue(session.session_id.startswith('pc_sess_'))
        self.assertEqual(response.context['session_id'], session.session_id)
        greeting = Message.objects.get(session_id=session.session_id, role='assistant')
        self.assertIn('Wenqu staff console', greeting.content)
        self.assertEqual(len(response.context['session_summaries']), 1)
        self.assertContains(response, 'wq-initial-history')

    def test_it_administrator_reaches_chatbot(self):
        self.client.force_login(_it_admin('itchat'))
        response = self.client.get(reverse('wenqu:chatbot'))
        self.assertEqual(response.status_code, 200)

    def test_force_new_creates_second_session(self):
        self.client.get(reverse('wenqu:chatbot'))
        self.client.get(reverse('wenqu:chatbot'), {'force_new': '1'})
        self.assertEqual(ChatSession.objects.filter(user=self.admin).count(), 2)

    def test_foreign_session_redirects_with_message(self):
        other = _plain('chatother')
        foreign = ChatSession.objects.create(user=other, session_id='sess_foreign000')
        response = self.client.get(reverse('wenqu:chatbot'), {'session_id': foreign.session_id}, follow=True)
        self.assertRedirects(response, reverse('wenqu:chatbot'))
        self.assertContains(response, 'Chat session not found.')

    def test_stream_denied_for_anonymous_plain_and_customer(self):
        url = reverse('wenqu:chat_stream')
        payload = {'type': 'text', 'content': {'text': 'hi'}}
        self.client.logout()
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard:dashboard'))
        self.client.force_login(_plain('chatplain'))
        response = self._post_json(url, payload)
        self.assertEqual(response.status_code, 302)
        customer_user, _profile = _customer_user('chatcust')
        self.client.force_login(customer_user)
        response = self._post_json(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Message.objects.exists())

    def test_stream_sse_lifecycle_and_persistence(self):
        # The SSE generator is lazy: consume it INSIDE the mock context, or the
        # real provider router runs once the patches are gone.
        with mock.patch('wenqu.services.admin_chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('wenqu.services.sse.stream_reply_deltas',
                        return_value=iter([('Hello', 'test/primary'), (' world', 'test/primary')])):
            response = self._post_json(reverse('wenqu:chat_stream'), {'type': 'text', 'content': {'text': 'hi'}})
            frames = _sse_frames(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        self.assertEqual([event for event, _ in frames], ['start', 'delta', 'delta', 'final', 'done'], frames)
        self.assertEqual(frames[1][1]['text'], 'Hello')
        final = frames[3][1]
        self.assertEqual(final['reply'], 'Hello world')
        self.assertEqual(final['meta']['model_used'], 'test/primary')
        messages = list(Message.objects.filter(session_id=final['session_id']).order_by('id'))
        self.assertEqual([m.role for m in messages], ['user', 'assistant'])
        self.assertEqual(json.loads(messages[-1].content)['text'], 'Hello world')
        self.assertTrue(ChatSession.objects.filter(user=self.admin, session_id=final['session_id']).exists())

    def test_stream_moderation_block_is_plain_json_400(self):
        from wenqu.services.utils.moderation import Suggest
        with mock.patch('wenqu.services.utils.moderation.check_text', return_value=Suggest.RISKY):
            response = self._post_json(reverse('wenqu:chat_stream'), {'type': 'text', 'content': {'text': 'bad text'}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertFalse(Message.objects.exists())

    def test_stream_invalid_json_400(self):
        response = self.client.post(reverse('wenqu:chat_stream'), data='not-json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def _seed_turn(self, session_id='sess_regen00001'):
        session = ChatSession.objects.create(user=self.admin, session_id=session_id)
        Message.objects.create(
            user=self.admin, session_id=session_id, role='user', type='text',
            content=json.dumps({'type': 'text', 'content': {'text': 'hi'}}),
        )
        reply = Message.objects.create(
            user=self.admin, session_id=session_id, role='assistant', type='text',
            content=json.dumps({'text': 'old reply', 'meta': {}}),
        )
        return session, reply

    def test_regenerate_stream_replaces_latest_reply(self):
        session, old_reply = self._seed_turn()
        with mock.patch('wenqu.services.admin_chat.prepare_reply_generation', return_value=_preparation()), \
             mock.patch('wenqu.services.sse.stream_reply_deltas', return_value=iter([('New reply', 'test/primary')])):
            response = self._post_json(reverse('wenqu:chat_regenerate_stream'), {'session_id': session.session_id})
            frames = _sse_frames(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([event for event, _ in frames], ['start', 'delta', 'final', 'done'], frames)
        final = frames[2][1]
        self.assertEqual(final['reply'], 'New reply')
        self.assertTrue(final['meta']['regenerated'])
        self.assertFalse(Message.objects.filter(pk=old_reply.pk).exists())
        latest = Message.objects.filter(session_id=session.session_id, role='assistant').order_by('-id').first()
        self.assertEqual(json.loads(latest.content)['text'], 'New reply')

    def test_regenerate_stale_message_id_conflict_409(self):
        session, old_reply = self._seed_turn()
        response = self._post_json(
            reverse('wenqu:chat_regenerate_stream'),
            {'session_id': session.session_id, 'message_id': str(old_reply.pk + 999)},
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Message.objects.filter(pk=old_reply.pk).exists())

    def test_regenerate_without_assistant_reply_422(self):
        session = ChatSession.objects.create(user=self.admin, session_id='sess_noreply001')
        Message.objects.create(
            user=self.admin, session_id=session.session_id, role='user', type='text',
            content=json.dumps({'type': 'text', 'content': {'text': 'hi'}}),
        )
        response = self._post_json(reverse('wenqu:chat_regenerate_stream'), {'session_id': session.session_id})
        self.assertEqual(response.status_code, 422)

    def test_regenerate_requires_session_id(self):
        response = self._post_json(reverse('wenqu:chat_regenerate_stream'), {})
        self.assertEqual(response.status_code, 400)

    def test_session_rename_and_clear(self):
        session = ChatSession.objects.create(user=self.admin, session_id='sess_rename0001')
        url = reverse('wenqu:chat_session_rename', args=[session.session_id])
        response = self._post_json(url, {'title': '  Printer   issues  '})
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.title, 'Printer issues')
        response = self._post_json(url, {'title': ''})
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertIsNone(session.title)

    def test_session_rename_too_long_400(self):
        session = ChatSession.objects.create(user=self.admin, session_id='sess_rename0002')
        response = self._post_json(reverse('wenqu:chat_session_rename', args=[session.session_id]), {'title': 'x' * 256})
        self.assertEqual(response.status_code, 400)

    def test_session_delete_current_bootstraps_replacement(self):
        self.client.get(reverse('wenqu:chatbot'))
        session = ChatSession.objects.get(user=self.admin)
        response = self._post_json(
            reverse('wenqu:chat_session_delete', args=[session.session_id]),
            {'current_session_id': session.session_id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['current_session_deleted'])
        self.assertNotEqual(payload['current_session_id'], session.session_id)
        self.assertFalse(ChatSession.objects.filter(session_id=session.session_id).exists())
        self.assertFalse(Message.objects.filter(session_id=session.session_id).exists())
        self.assertTrue(ChatSession.objects.filter(user=self.admin, session_id=payload['current_session_id']).exists())

    def test_session_actions_reject_foreign_sessions(self):
        other = _plain('sessother')
        foreign = ChatSession.objects.create(user=other, session_id='sess_foreign001')
        response = self._post_json(reverse('wenqu:chat_session_rename', args=[foreign.session_id]), {'title': 'x'})
        self.assertEqual(response.status_code, 404)
        response = self._post_json(reverse('wenqu:chat_session_delete', args=[foreign.session_id]), {})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ChatSession.objects.filter(pk=foreign.pk).exists())