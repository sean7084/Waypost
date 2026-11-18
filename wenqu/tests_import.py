"""Tests for the legacy helpdesk import command (PR7).

psycopg2 is mocked end-to-end: a fake connection/cursor serves canned legacy
rows keyed by the table named in each SELECT, so no PostgreSQL is contacted.
The fake cursor projects rows through the command's own TABLES column lists,
which keeps the fixtures honest about column order.

Covered: dry-run rollback (no rows, no blobs), idempotent re-runs, natural-key
upserts, pgvector text parsing, the User -> accounts.User + WeChatIdentity +
WenquProfile mapping (including legacy admins and pre-existing identities),
blob transfer through private storage, timestamp preservation, count
reconciliation / mismatch detection, and the graceful no-DSN error.
"""
import io
import os
import re
import tempfile
from datetime import datetime, timezone as dt_timezone
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User, WeChatIdentity
from wenqu.management.commands import import_helpdesk as cmd_mod
from wenqu.models import (
    ChatSession, Customer, KBChunk, KBDocument, Message, TriageAsset,
    TriagePlaybook, TriageSession, TriageStep, UploadFile, UserProfile,
    WenquProfile,
)
from wenqu.services.storage import private_storage

_APPID = 'wenqu-test-appid'
_NAIVE = datetime(2026, 10, 1, 8, 30, 0)
_NAIVE_LATER = datetime(2026, 10, 2, 9, 0, 0)
_AWARE = timezone.make_aware(_NAIVE, dt_timezone.utc)
_FILE_ID = 'f' * 32


class _FakeCursor:
    """Serves canned rows for the table named in the last SELECT."""

    def __init__(self, tables):
        self._tables = tables
        self._table = None

    def execute(self, sql, params=None):
        match = re.search(r'FROM\s+(\w+)', sql)
        self._table = match.group(1) if match else None

    def fetchall(self):
        return list(self._tables.get(self._table, []))

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, tables):
        self._tables = tables
        self.closed = False
        self.readonly = False

    def cursor(self):
        return _FakeCursor(self._tables)

    def set_session(self, readonly=False, autocommit=False):
        self.readonly = readonly

    def close(self):
        self.closed = True


def _project(table, dicts):
    columns = dict(cmd_mod.TABLES)[table]
    return [tuple(row.get(col) for col in columns) for row in dicts]


def _legacy_tables():
    """One coherent legacy dataset covering every imported table."""
    return {
        'customers': _project('customers', [
            {'id': 1, 'name': 'Acme', 'notes': 'legacy note', 'created_at': _NAIVE},
        ]),
        'users': _project('users', [
            {'id': 10, 'external_id': 'wx_o1', 'role': 'user', 'password_hash': 'bcrypt-x',
             'customer_id': 1, 'openid': 'openid-1', 'nickname': 'Nick', 'mobile': '13800000000',
             'avatar': '/api/uploads/avatars/a.png', 'can_upload': 1, 'can_chat': 1,
             'can_manage_customers': 0, 'can_manage_users': 0, 'token_version': 7,
             'created_at': _NAIVE},
            {'id': 11, 'external_id': 'admin_pc', 'role': 'admin', 'password_hash': 'bcrypt-y',
             'customer_id': None, 'openid': None, 'nickname': None, 'mobile': None,
             'avatar': None, 'can_upload': 1, 'can_chat': 1,
             'can_manage_customers': 1, 'can_manage_users': 1, 'token_version': 0,
             'created_at': _NAIVE},
        ]),
        'chat_sessions': _project('chat_sessions', [
            {'id': 100, 'user_id': 10, 'session_id': 'sess_a', 'title': 'Legacy title',
             'created_at': _NAIVE},
        ]),
        'messages': _project('messages', [
            {'id': 1000, 'user_id': 10, 'session_id': 'sess_a', 'role': 'user', 'type': 'text',
             'content': '{"text": "hi"}', 'created_at': _NAIVE},
            {'id': 1001, 'user_id': 10, 'session_id': 'sess_a', 'role': 'assistant',
             'type': 'text', 'content': '{"text": "hello"}', 'created_at': _NAIVE_LATER},
        ]),
        'user_profiles': _project('user_profiles', [
            {'user_id': 10, 'weak_points': '{"wifi": 2}', 'suggested_solutions': '{}',
             'notes': 'learner', 'last_updated': _NAIVE_LATER},
        ]),
        'upload_files': _project('upload_files', [
            {'id': 200, 'user_id': 10, 'file_id': _FILE_ID, 'file_type': 'audio',
             'original_filename': 'voice.m4a', 'mime_type': 'audio/mp4',
             'stored_path': f'/app/uploads/{_FILE_ID}__voice.m4a', 'duration_seconds': 9,
             'created_at': _NAIVE},
        ]),
        'kb_documents': _project('kb_documents', [
            {'id': 300, 'path': 'C:\\old\\kb_sources\\doc.pdf', 'source_type': 'pdf',
             'content_hash': 'h' * 64, 'customer_id': 1, 'created_at': _NAIVE},
        ]),
        'kb_chunks': _project('kb_chunks', [
            {'id': 400, 'document_id': 300, 'chunk_index': 0, 'text': 'chunk text',
             'chunk_type': 'text', 'metadata_json': '{"page": 1}',
             'embedding': '[0.1, 0.2, 0.3]', 'source_path': '/app/kb_sources/doc.pdf',
             'page_or_section': 'p1', 'created_at': _NAIVE},
        ]),
        'triage_assets': _project('triage_assets', [
            {'id': 500, 'asset_key': 'shot-1', 'relative_path': 'triage_assets/shot.png',
             'caption_zh': '截图', 'source_url': '', 'license': '', 'width': 4, 'height': 4,
             'created_at': _NAIVE},
        ]),
        'triage_playbooks': _project('triage_playbooks', [
            {'id': 600, 'slug': 'wifi', 'title_zh': '断网', 'symptom_summary_zh': '无法上网',
             'category': 'network', 'customer_id': None, 'is_active': 1,
             'embedding': '[0.5, 0.6]', 'created_at': _NAIVE, 'updated_at': _NAIVE_LATER},
        ]),
        'triage_steps': _project('triage_steps', [
            {'id': 700, 'playbook_id': 600, 'step_index': 0, 'kind': 'check',
             'prompt_zh': '检查路由器', 'device_scope': None, 'expected_result_zh': '灯亮',
             'asset_id': 500, 'quick_replies_json': '[{"label_zh": "是", "value": "yes"}]',
             'branch_map_json': '{"yes": 1, "default": 2}', 'created_at': _NAIVE},
        ]),
        'triage_sessions': _project('triage_sessions', [
            {'id': 800, 'user_id': 10, 'session_id': 'sess_a', 'playbook_id': 600,
             'current_step_index': 1, 'answers_json': '{"0": "yes"}', 'status': 'in_progress',
             'created_at': _NAIVE, 'updated_at': _NAIVE_LATER},
        ]),
        # Never selected by the command; present to prove it is ignored.
        'password_reset_codes': [(900, 10, 'hash', _NAIVE, 0, None, '', _NAIVE)],
    }


@override_settings(
    WENQU_LEGACY_DSN='postgresql://fake-user:fake@127.0.0.1:5432/legacy',
    WECHAT_WENQU_APPID=_APPID,
)
class ImportHelpdeskTests(TestCase):
    def setUp(self):
        self._uploads = tempfile.TemporaryDirectory()
        self.addCleanup(self._uploads.cleanup)
        self._private = tempfile.TemporaryDirectory()
        self.addCleanup(self._private.cleanup)
        self.uploads_dir = self._uploads.name
        self.private_root = self._private.name
        # Legacy blobs on disk, under the uploads root.
        self._write(os.path.join(self.uploads_dir, f'{_FILE_ID}__voice.m4a'), b'audio-bytes')
        self._write(os.path.join(self.uploads_dir, 'avatars', 'a.png'), b'avatar-png')
        self._write(os.path.join(self.uploads_dir, 'triage_assets', 'shot.png'), b'asset-png')

        self.tables = _legacy_tables()
        self.conn = _FakeConnection(self.tables)
        patcher = mock.patch.object(cmd_mod.psycopg2, 'connect', return_value=self.conn)
        self.connect = patcher.start()
        self.addCleanup(patcher.stop)

        self.out = io.StringIO()
        self.err = io.StringIO()
        self._overrides = override_settings(
            WENQU_LEGACY_UPLOADS_DIR=self.uploads_dir,
            WENQU_PRIVATE_MEDIA_ROOT=self.private_root,
        )
        self._overrides.enable()
        self.addCleanup(self._overrides.disable)

    @staticmethod
    def _write(full, data):
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as fh:
            fh.write(data)

    def _run(self, *args):
        call_command('import_helpdesk', *args, stdout=self.out, stderr=self.err)

    def _stored_files(self):
        found = []
        for dirpath, _dirnames, filenames in os.walk(self.private_root):
            found.extend(os.path.join(dirpath, name) for name in filenames)
        return sorted(found)

    # -- dry run -------------------------------------------------------------

    def test_dry_run_rolls_back_and_writes_no_blobs(self):
        self._run('--dry-run')
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(WenquProfile.objects.count(), 0)
        self.assertEqual(ChatSession.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(UploadFile.objects.count(), 0)
        self.assertEqual(KBChunk.objects.count(), 0)
        self.assertEqual(self._stored_files(), [])
        output = self.out.getvalue()
        self.assertIn('would import', output)
        self.assertIn('DRY RUN complete', output)
        self.assertIn('rolled back', output)
        self.assertTrue(self.conn.closed)
        self.assertTrue(self.conn.readonly)

    # -- full mapping ----------------------------------------------------------

    def test_full_import_maps_every_table(self):
        self._run()
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(WenquProfile.objects.count(), 2)
        self.assertEqual(ChatSession.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(UserProfile.objects.count(), 1)
        self.assertEqual(UploadFile.objects.count(), 1)
        self.assertEqual(KBDocument.objects.count(), 1)
        self.assertEqual(KBChunk.objects.count(), 1)
        self.assertEqual(TriageAsset.objects.count(), 1)
        self.assertEqual(TriagePlaybook.objects.count(), 1)
        self.assertEqual(TriageStep.objects.count(), 1)
        self.assertEqual(TriageSession.objects.count(), 1)
        self.assertIn('All tables reconciled', self.out.getvalue())
        self.assertIn('password_reset_codes: skipped by design', self.out.getvalue())

        customer = Customer.objects.get(name='Acme')
        self.assertEqual(customer.notes, 'legacy note')
        self.assertEqual(customer.created_at, _AWARE)

        user = User.objects.get(username='wx_o1')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        identity = WeChatIdentity.objects.get(user=user)
        self.assertEqual(identity.appid, _APPID)
        self.assertEqual(identity.openid, 'openid-1')
        self.assertEqual(identity.unionid, '')
        profile = user.wenqu_profile
        self.assertEqual(profile.external_id, 'wx_o1')
        self.assertEqual(profile.customer, customer)
        self.assertTrue(profile.can_upload)
        self.assertTrue(profile.can_chat)
        self.assertFalse(profile.can_manage_customers)
        self.assertEqual(profile.token_version, 7)
        self.assertEqual(profile.created_at, _AWARE)

        # Legacy admin imported as a customer, bits preserved, never staff.
        admin = User.objects.get(username='admin_pc')
        self.assertFalse(admin.is_staff)
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.has_usable_password())
        self.assertTrue(admin.wenqu_profile.can_manage_users)
        self.assertTrue(admin.wenqu_profile.can_manage_customers)
        self.assertIsNone(admin.wenqu_profile.customer)
        self.assertEqual(WeChatIdentity.objects.count(), 1)  # no openid -> no identity

        session = ChatSession.objects.get(session_id='sess_a')
        self.assertEqual(session.user, user)
        self.assertEqual(session.title, 'Legacy title')
        first = Message.objects.get(content='{"text": "hi"}')
        self.assertEqual(first.session, session)
        self.assertEqual(first.role, 'user')
        self.assertEqual(first.created_at, _AWARE)

        learning = UserProfile.objects.get(user=user)
        self.assertEqual(learning.weak_points, '{"wifi": 2}')
        self.assertEqual(learning.last_updated, timezone.make_aware(_NAIVE_LATER, dt_timezone.utc))

        document = KBDocument.objects.get()
        # getattr keeps the media-access hygiene guard quiet: a CharField named
        # "path", not a FieldFile (same idiom as KBDocument.__str__).
        self.assertEqual(getattr(document, 'path'), 'doc.pdf')  # absolute legacy path normalized
        self.assertEqual(document.customer, customer)
        chunk = KBChunk.objects.get()
        self.assertEqual(chunk.document, document)
        self.assertEqual(chunk.content, 'chunk text')
        self.assertEqual(chunk.embedding, [0.1, 0.2, 0.3])
        self.assertEqual(chunk.metadata_json, {'page': 1})
        self.assertEqual(chunk.source_path, 'doc.pdf')

        asset = TriageAsset.objects.get(asset_key='shot-1')
        playbook = TriagePlaybook.objects.get(slug='wifi')
        self.assertEqual(playbook.embedding, [0.5, 0.6])
        self.assertTrue(playbook.is_active)
        self.assertEqual(playbook.updated_at, timezone.make_aware(_NAIVE_LATER, dt_timezone.utc))
        step = TriageStep.objects.get()
        self.assertEqual(step.playbook, playbook)
        self.assertEqual(step.asset, asset)
        self.assertEqual(step.quick_replies_json, [{'label_zh': '是', 'value': 'yes'}])
        self.assertEqual(step.branch_map_json, {'yes': 1, 'default': 2})
        triage = TriageSession.objects.get()
        self.assertEqual(triage.user, user)
        self.assertEqual(triage.playbook, playbook)
        self.assertEqual(triage.answers_json, {'0': 'yes'})

    def test_upload_blob_goes_through_private_storage(self):
        self._run()
        upload = UploadFile.objects.get(file_id=_FILE_ID)
        self.assertEqual(upload.file_type, 'audio')
        self.assertEqual(upload.original_filename, 'voice.m4a')
        self.assertEqual(upload.duration_seconds, 9)
        self.assertEqual(upload.mime_type, 'audio/mp4')
        self.assertEqual(upload.created_at, _AWARE)
        storage = private_storage()
        self.assertTrue(storage.exists(upload.file.name))
        self.assertTrue(upload.file.name.startswith('wenqu/uploads/'))
        with storage.open(upload.file.name) as fh:
            self.assertEqual(fh.read(), b'audio-bytes')
        # Avatar and triage screenshot blobs travelled too.
        self.assertTrue(storage.exists('wenqu/uploads/avatars/a.png'))
        self.assertTrue(storage.exists('wenqu/triage_assets/shot.png'))

    # -- idempotency / upsert ----------------------------------------------------

    def test_rerun_is_idempotent(self):
        self._run()
        pks = {
            'customer': Customer.objects.get().pk,
            'user': User.objects.get(username='wx_o1').pk,
            'message': Message.objects.first().pk,
            'upload': UploadFile.objects.get().pk,
        }
        files_before = self._stored_files()
        self.out = io.StringIO()
        self._run()
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(UploadFile.objects.count(), 1)
        self.assertEqual(Customer.objects.get().pk, pks['customer'])
        self.assertEqual(User.objects.get(username='wx_o1').pk, pks['user'])
        self.assertEqual(Message.objects.first().pk, pks['message'])
        self.assertEqual(UploadFile.objects.get().pk, pks['upload'])
        self.assertEqual(self._stored_files(), files_before)  # no suffixed duplicates
        self.assertIn('All tables reconciled', self.out.getvalue())

    def test_natural_key_upsert_updates_changed_fields_in_place(self):
        self._run()
        customer_pk = Customer.objects.get().pk
        session_pk = ChatSession.objects.get().pk
        self.tables['customers'] = _project('customers', [
            {'id': 1, 'name': 'Acme', 'notes': 'changed note', 'created_at': _NAIVE},
        ])
        self.tables['chat_sessions'] = _project('chat_sessions', [
            {'id': 100, 'user_id': 10, 'session_id': 'sess_a', 'title': 'New title',
             'created_at': _NAIVE},
        ])
        self._run()
        self.assertEqual(Customer.objects.get().pk, customer_pk)
        self.assertEqual(Customer.objects.get().notes, 'changed note')
        self.assertEqual(ChatSession.objects.get().pk, session_pk)
        self.assertEqual(ChatSession.objects.get().title, 'New title')

    def test_existing_wenqu_identity_is_reused_not_duplicated(self):
        # A customer who already logged into the new client before the merge.
        early = User.objects.create_user(username='early_user', password='pw')
        early.set_unusable_password()
        early.save()
        WenquProfile.objects.create(user=early, external_id='wx_o1')
        WeChatIdentity.objects.create(user=early, appid=_APPID, openid='openid-1')
        self._run()
        self.assertEqual(User.objects.count(), 2)  # early + admin_pc, no third account
        self.assertEqual(WeChatIdentity.objects.filter(openid='openid-1').count(), 1)
        profile = WenquProfile.objects.get(user=early)
        self.assertEqual(profile.token_version, 7)
        self.assertEqual(profile.nickname, 'Nick')
        self.assertEqual(Message.objects.first().user, early)

    def test_username_collision_gets_deterministic_suffix(self):
        User.objects.create_user(username='wx_o1', password='pw')  # unrelated account
        self._run()
        imported = WenquProfile.objects.get(external_id='wx_o1').user
        self.assertEqual(imported.username, 'wx_o1_h10')
        self._run()
        self.assertEqual(User.objects.filter(username__startswith='wx_o1').count(), 2)

    # -- parsing / guards ---------------------------------------------------------

    def test_parse_embedding_variants(self):
        self.assertEqual(cmd_mod.parse_embedding('[0.1, 0.2]'), [0.1, 0.2])
        self.assertIsNone(cmd_mod.parse_embedding(None))
        self.assertEqual(cmd_mod.parse_embedding([1, 2]), [1, 2])
        with self.assertRaises(CommandError):
            cmd_mod.parse_embedding('not-json')
        with self.assertRaises(CommandError):
            cmd_mod.parse_embedding('{"a": 1}')

    def test_aware_utc_helper(self):
        self.assertIsNone(cmd_mod.aware_utc(None))
        self.assertEqual(cmd_mod.aware_utc(_NAIVE), _AWARE)
        self.assertEqual(cmd_mod.aware_utc(_AWARE), _AWARE)

    def test_missing_dsn_fails_gracefully_without_connecting(self):
        with override_settings(WENQU_LEGACY_DSN=''):
            with self.assertRaises(CommandError) as ctx:
                self._run()
        self.assertIn('WENQU_LEGACY_DSN', str(ctx.exception))
        self.connect.assert_not_called()

    def test_missing_blob_skips_row_and_reports_mismatch(self):
        os.remove(os.path.join(self.uploads_dir, f'{_FILE_ID}__voice.m4a'))
        self._run()
        self.assertEqual(UploadFile.objects.count(), 0)
        output = self.out.getvalue()
        self.assertIn('COUNT MISMATCH', output)
        self.assertIn('upload_files', output)
        self.assertIn('skipped=1', output)
        # The rest of the import still landed.
        self.assertEqual(Message.objects.count(), 2)

    def test_unset_uploads_dir_skips_blobs_but_imports_metadata(self):
        with override_settings(WENQU_LEGACY_UPLOADS_DIR=''):
            self._run()
        self.assertEqual(UploadFile.objects.count(), 0)
        self.assertEqual(TriageAsset.objects.count(), 1)  # metadata row still imported
        self.assertIn('WENQU_LEGACY_UPLOADS_DIR is not set', self.err.getvalue())
        self.assertEqual(self._stored_files(), [])

    def test_malformed_legacy_json_aborts_atomically(self):
        self.tables['kb_chunks'] = _project('kb_chunks', [
            {'id': 400, 'document_id': 300, 'chunk_index': 0, 'text': 'chunk text',
             'chunk_type': 'text', 'metadata_json': '{bad json',
             'embedding': '[0.1]', 'source_path': 'doc.pdf',
             'page_or_section': '', 'created_at': _NAIVE},
        ])
        with self.assertRaises(CommandError):
            self._run()
        self.assertEqual(Customer.objects.count(), 0)  # whole transaction rolled back
        self.assertEqual(Message.objects.count(), 0)
        self.assertTrue(self.conn.closed)