"""Idempotent production import of the legacy Helpdesk database into Wenqu.

Reads the standalone FastAPI helpdesk's PostgreSQL 17 database directly with
psycopg2 (``WENQU_LEGACY_DSN``) and upserts every business table into the Django
ORM by *natural key*, so the command can be re-run safely during the migration
window and after a partial failure:

    customers        by name
    users            by openid (WeChatIdentity under the wenqu appid), falling
                     back to external_id; creates accounts.User (username =
                     external_id, unusable password) + WeChatIdentity +
                     WenquProfile with the legacy permission bits and
                     token_version preserved verbatim
    chat_sessions    by session_id
    messages         by (session_id, user, created_at, content) - the legacy
                     autoincrement id is the per-session sequence, and rows are
                     imported in id order so history keeps its ordering
    user_profiles    by user
    upload_files     by file_id; the blob is read from the legacy uploads root
                     (``WENQU_LEGACY_UPLOADS_DIR``) and written through
                     private_storage().save - never the public media backend
    kb_documents     by path (normalized to the kb_sources-relative form)
    kb_chunks        by (document, chunk_index); pgvector text '[...]' is parsed
                     into a JSON list - embeddings are NEVER recomputed
    triage_assets    by asset_key (screenshot blobs copied best-effort)
    triage_playbooks by slug (embedding parsed the same way)
    triage_steps     by (playbook, step_index)
    triage_sessions  by (user, session_id)
    password_reset_codes  SKIPPED - short-lived secrets must not travel

Legacy admin/role users are imported as *customers* with their can_* bits
preserved; staff administration stays on existing Waypost accounts (see
docs/DEPLOYMENT.md, Wenqu section). unionid is not a legacy column, so imported
identities carry an empty one and accounts are never auto-merged by it; the
first wx.login fills it in (wenqu.services.auth).

``--dry-run`` executes the whole import inside a transaction and rolls it back,
printing the per-table would-import counts; blob writes are skipped entirely in
dry-run so no file lands on disk either. A normal run ends with a per-table
source-vs-imported reconciliation and prints every mismatch.

Timestamps: legacy columns are naive UTC (``datetime.utcnow``); they are made
aware in UTC and preserved through the auto_now_add/auto_now fields with a
follow-up queryset update, which bypasses those hooks.
"""
import json
import os
from datetime import timezone as dt_timezone

import psycopg2
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User, WeChatIdentity
from wenqu.models import (
    ChatSession, Customer, KBChunk, KBDocument, Message, TriageAsset,
    TriagePlaybook, TriageSession, TriageStep, UploadFile, UserProfile,
    WenquProfile,
)
from wenqu.services.kb.paths import to_stored_path
from wenqu.services.storage import private_storage
from wenqu.services.utils.file_store import (
    normalize_upload_relative_path, storage_name_for_relative_path,
)

# Import order respects foreign keys. Each entry: (table, select columns).
# Column names are the legacy SQLAlchemy schema's (helpdesk/backend/app/models.py
# plus alembic 0001-0007: the mfa_* columns are gone, token_version exists).
TABLES = [
    ('customers', ['id', 'name', 'notes', 'created_at']),
    ('users', [
        'id', 'external_id', 'role', 'password_hash', 'customer_id', 'openid',
        'nickname', 'mobile', 'avatar', 'can_upload', 'can_chat',
        'can_manage_customers', 'can_manage_users', 'token_version', 'created_at',
    ]),
    ('chat_sessions', ['id', 'user_id', 'session_id', 'title', 'created_at']),
    ('messages', ['id', 'user_id', 'session_id', 'role', 'type', 'content', 'created_at']),
    ('user_profiles', ['user_id', 'weak_points', 'suggested_solutions', 'notes', 'last_updated']),
    ('upload_files', [
        'id', 'user_id', 'file_id', 'file_type', 'original_filename',
        'mime_type', 'stored_path', 'duration_seconds', 'created_at',
    ]),
    ('kb_documents', ['id', 'path', 'source_type', 'content_hash', 'customer_id', 'created_at']),
    ('kb_chunks', [
        'id', 'document_id', 'chunk_index', 'text', 'chunk_type',
        'metadata_json', 'embedding', 'source_path', 'page_or_section', 'created_at',
    ]),
    ('triage_assets', [
        'id', 'asset_key', 'relative_path', 'caption_zh', 'source_url',
        'license', 'width', 'height', 'created_at',
    ]),
    ('triage_playbooks', [
        'id', 'slug', 'title_zh', 'symptom_summary_zh', 'category',
        'customer_id', 'is_active', 'embedding', 'created_at', 'updated_at',
    ]),
    ('triage_steps', [
        'id', 'playbook_id', 'step_index', 'kind', 'prompt_zh', 'device_scope',
        'expected_result_zh', 'asset_id', 'quick_replies_json',
        'branch_map_json', 'created_at',
    ]),
    ('triage_sessions', [
        'id', 'user_id', 'session_id', 'playbook_id', 'current_step_index',
        'answers_json', 'status', 'created_at', 'updated_at',
    ]),
]


def aware_utc(value):
    """Legacy naive-UTC datetime -> aware UTC; aware values pass through."""
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, dt_timezone.utc)
    return value


def parse_embedding(value):
    """pgvector text '[0.1,0.2,...]' -> JSON list. Never recomputes anything.

    psycopg2 returns the unknown ``vector`` type as text; a list (already
    decoded) passes through, and NULL stays NULL (triage playbooks allow it).
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f'Unparseable pgvector embedding text: {value!r:.80}') from exc
    if not isinstance(parsed, list) or not all(isinstance(x, (int, float)) for x in parsed):
        raise CommandError(f'Embedding is not a numeric JSON list: {value!r:.80}')
    return parsed


def parse_json_text(value, default, context):
    """Legacy JSON-as-TEXT column -> Python object. Malformed data aborts the
    whole import (the transaction rolls back) rather than corrupting a row."""
    if value is None or value == '':
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f'Malformed JSON in {context}: {value!r:.80}') from exc


def legacy_upload_relative(stored_path):
    """Absolute legacy stored_path -> path relative to the uploads root.

    Legacy rows hold machine-local absolutes ('/app/uploads/x', a Windows
    drive path, ...). Everything after the last '/uploads/' segment is the
    root-relative form; a bare filename is already relative.
    """
    normalized = str(stored_path or '').strip().replace('\\', '/')
    marker = '/uploads/'
    index = normalized.rfind(marker)
    if index >= 0:
        return normalized[index + len(marker):]
    if normalized.startswith('uploads/'):
        return normalized[len('uploads/'):]
    return normalized.rsplit('/', 1)[-1]


class Command(BaseCommand):
    help = (
        'Import the legacy Helpdesk PostgreSQL database into Wenqu by natural '
        'key (idempotent upsert). Requires WENQU_LEGACY_DSN; upload blobs are '
        'read from WENQU_LEGACY_UPLOADS_DIR. --dry-run rolls everything back.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Execute inside a transaction, roll back, print would-import counts. No blob writes.',
        )

    def handle(self, *args, **options):
        dsn = str(getattr(settings, 'WENQU_LEGACY_DSN', '') or '').strip()
        if not dsn:
            raise CommandError(
                'WENQU_LEGACY_DSN is not set. Point it at the legacy helpdesk '
                'PostgreSQL, e.g. postgresql://helpdesk_user:<pw>@127.0.0.1:5432/helpdesk_db '
                '(see docs/DEPLOYMENT.md, Wenqu migration window).'
            )
        self.dry_run = bool(options.get('dry_run'))
        self.uploads_dir = str(getattr(settings, 'WENQU_LEGACY_UPLOADS_DIR', '') or '').strip()
        self.warnings = []
        self.stats = {table: {'source': 0, 'created': 0, 'updated': 0, 'skipped': 0} for table, _ in TABLES}
        # Legacy integer pk -> imported instance, for FK remapping.
        self.customers = {}
        self.users = {}
        self.documents = {}
        self.assets = {}
        self.playbooks = {}

        connection = psycopg2.connect(dsn)
        try:
            # The source is read-only: this command must never write to the
            # legacy database, even by accident (a stray UPDATE would break
            # the one-week rollback window).
            connection.set_session(readonly=True, autocommit=True)
            cursor = connection.cursor()
            try:
                with transaction.atomic():
                    for table, columns in TABLES:
                        rows = self._fetch(cursor, table, columns)
                        self.stats[table]['source'] = len(rows)
                        getattr(self, f'_import_{table}')(rows)
                    self._note_skipped_tables()
                    self._print_report()
                    if self.dry_run:
                        transaction.set_rollback(True)
            finally:
                cursor.close()
        finally:
            connection.close()

        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN complete - transaction rolled back, nothing committed.'))
        else:
            self.stdout.write(self.style.SUCCESS('Import complete.'))

    # -- source reading -----------------------------------------------------

    def _fetch(self, cursor, table, columns):
        # "text" is a type name in PostgreSQL; quote it. ORDER BY id keeps the
        # legacy insertion sequence (messages have no other ordering column).
        quoted = ', '.join(f'"{c}"' if c == 'text' else c for c in columns)
        order = 'user_id' if table == 'user_profiles' else 'id'
        cursor.execute(f'SELECT {quoted} FROM {table} ORDER BY {order}')
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _warn(self, message):
        self.warnings.append(message)
        self.stderr.write(self.style.WARNING(f'WARNING: {message}'))

    # -- upsert helpers -----------------------------------------------------

    def _upsert(self, table, model, lookup, defaults, timestamps=None):
        """Natural-key update_or_create + timestamp preservation.

        auto_now_add/auto_now fire on save(), so created_at/updated_at values
        from the legacy row are applied afterwards with queryset.update(),
        which bypasses those hooks. Returns (instance, created).
        """
        obj, created = model.objects.update_or_create(defaults=defaults, **lookup)
        if timestamps:
            updates = {field: aware_utc(value) for field, value in timestamps.items() if value is not None}
            if updates:
                model.objects.filter(pk=obj.pk).update(**updates)
                for field, value in updates.items():
                    setattr(obj, field, value)
        bucket = self.stats[table]
        bucket['created' if created else 'updated'] += 1
        return obj, created

    def _legacy_file_bytes(self, relative, context):
        """Read a blob under the legacy uploads root; None when unavailable."""
        if not self.uploads_dir:
            return None
        full = os.path.join(self.uploads_dir, *str(relative).split('/'))
        if not os.path.isfile(full):
            self._warn(f'{context}: legacy file not found under WENQU_LEGACY_UPLOADS_DIR: {relative}')
            return None
        with open(full, 'rb') as fh:
            return fh.read()

    def _customer_for(self, legacy_customer_id):
        if legacy_customer_id is None:
            return None
        customer = self.customers.get(legacy_customer_id)
        if customer is None:
            raise CommandError(f'users/KB/triage row references unknown legacy customer id {legacy_customer_id}')
        return customer

    def _user_for(self, legacy_user_id):
        user = self.users.get(legacy_user_id)
        if user is None:
            raise CommandError(f'row references unknown legacy user id {legacy_user_id}')
        return user

    # -- per-table importers --------------------------------------------------

    def _import_customers(self, rows):
        for row in rows:
            customer, _ = self._upsert(
                'customers', Customer,
                {'name': row['name']},
                {'notes': row['notes'] or ''},
                timestamps={'created_at': row['created_at']},
            )
            self.customers[row['id']] = customer

    def _import_users(self, rows):
        appid = settings.WECHAT_WENQU_APPID
        for row in rows:
            external_id = row['external_id']
            openid = row['openid'] or ''
            user = None
            if openid:
                identity = WeChatIdentity.objects.filter(appid=appid, openid=openid).select_related('user').first()
                if identity is not None:
                    user = identity.user
            if user is None:
                profile = WenquProfile.objects.filter(external_id=external_id).select_related('user').first()
                if profile is not None:
                    user = profile.user
            if user is None:
                # Deterministic disambiguation if an unrelated Waypost account
                # already owns the username (legacy id is stable across runs).
                username = external_id
                if User.objects.filter(username=username).exists():
                    username = f'{external_id}_h{row["id"]}'
                user = User(username=username)
                user.set_unusable_password()
                user.save()
            # The WenquProfile upsert below is the single per-row counter for
            # the 'users' table (one profile per legacy user).
            User.objects.filter(pk=user.pk).update(created_at=aware_utc(row['created_at']))
            user.created_at = aware_utc(row['created_at'])

            if openid:
                WeChatIdentity.objects.update_or_create(
                    appid=appid, openid=openid,
                    # unionid is not a legacy column; the first wx.login fills
                    # it in. Never merge accounts by unionid here.
                    defaults={'user': user, 'unionid': ''},
                )
            self._upsert(
                'users', WenquProfile,
                {'user': user},
                {
                    'external_id': external_id,
                    'nickname': row['nickname'],
                    'mobile': row['mobile'],
                    'avatar': row['avatar'],
                    # Legacy admin/role users stay customers: the can_* bits
                    # travel, is_staff/is_superuser never do. Staff admin is
                    # an existing Waypost account (docs/DEPLOYMENT.md).
                    'can_upload': bool(row['can_upload']),
                    'can_chat': bool(row['can_chat']),
                    'can_manage_customers': bool(row['can_manage_customers']),
                    'can_manage_users': bool(row['can_manage_users']),
                    'token_version': row['token_version'],
                    'customer': self._customer_for(row['customer_id']),
                },
                timestamps={'created_at': row['created_at']},
            )
            self.users[row['id']] = user
            self._copy_avatar(user, row['avatar'])

    def _copy_avatar(self, user, avatar):
        """Best-effort avatar blob copy (legacy stores a protected URL)."""
        if self.dry_run or not avatar or not self.uploads_dir:
            return
        relative = normalize_upload_relative_path(avatar)
        if not relative:
            return
        data = self._legacy_file_bytes(relative, f'avatar of {user.username}')
        if data is None:
            return
        storage = private_storage()
        name = storage_name_for_relative_path(relative)
        if not storage.exists(name):
            storage.save(name, ContentFile(data))

    def _import_chat_sessions(self, rows):
        for row in rows:
            self._upsert(
                'chat_sessions', ChatSession,
                {'session_id': row['session_id']},
                {'user': self._user_for(row['user_id']), 'title': row['title']},
                timestamps={'created_at': row['created_at']},
            )

    def _import_messages(self, rows):
        for row in rows:
            created_at = aware_utc(row['created_at'])
            # The legacy id is the per-session sequence; (session, user,
            # created_at, content) is the stable natural key that survives
            # re-runs because created_at is preserved verbatim.
            self._upsert(
                'messages', Message,
                {
                    'session_id': row['session_id'],
                    'user': self._user_for(row['user_id']),
                    'created_at': created_at,
                    'content': row['content'],
                },
                {'role': row['role'], 'type': row['type']},
                # auto_now_add would otherwise stamp 'now' on create and the
                # next run's created_at lookup would miss -> duplicate rows.
                timestamps={'created_at': row['created_at']},
            )

    def _import_user_profiles(self, rows):
        for row in rows:
            self._upsert(
                'user_profiles', UserProfile,
                {'user': self._user_for(row['user_id'])},
                {
                    'weak_points': row['weak_points'],
                    'suggested_solutions': row['suggested_solutions'],
                    'notes': row['notes'],
                },
                timestamps={'last_updated': row['last_updated']},
            )

    def _import_upload_files(self, rows):
        for row in rows:
            existing = UploadFile.objects.filter(file_id=row['file_id']).first()
            if existing is not None:
                # Idempotent re-run: keep the stored blob, refresh metadata.
                self._upsert(
                    'upload_files', UploadFile,
                    {'file_id': row['file_id']},
                    {
                        'user': self._user_for(row['user_id']),
                        'file_type': row['file_type'],
                        'original_filename': row['original_filename'],
                        'mime_type': row['mime_type'],
                        'duration_seconds': row['duration_seconds'],
                    },
                    timestamps={'created_at': row['created_at']},
                )
                continue
            relative = legacy_upload_relative(row['stored_path'])
            if self.dry_run:
                # No blob writes in dry-run; only probe readability.
                if self.uploads_dir and not os.path.isfile(os.path.join(self.uploads_dir, *relative.split('/'))):
                    self._warn(f'upload_files {row["file_id"]}: legacy blob missing: {relative}')
                    self.stats['upload_files']['skipped'] += 1
                    continue
                self.stats['upload_files']['created'] += 1
                continue
            data = self._legacy_file_bytes(relative, f'upload_files {row["file_id"]}')
            if data is None:
                self.stats['upload_files']['skipped'] += 1
                continue
            safe_name = os.path.basename(row['original_filename'] or relative)
            storage = private_storage()
            name = storage.save(f'wenqu/uploads/{row["file_id"]}__{safe_name}', ContentFile(data))
            self._upsert(
                'upload_files', UploadFile,
                {'file_id': row['file_id']},
                {
                    'user': self._user_for(row['user_id']),
                    'file_type': row['file_type'],
                    'file': name,
                    'original_filename': row['original_filename'],
                    'mime_type': row['mime_type'],
                    'duration_seconds': row['duration_seconds'],
                },
                timestamps={'created_at': row['created_at']},
            )

    def _import_kb_documents(self, rows):
        for row in rows:
            document, _ = self._upsert(
                'kb_documents', KBDocument,
                {'path': to_stored_path(row['path'])},
                {
                    'source_type': row['source_type'],
                    'content_hash': row['content_hash'],
                    'customer': self._customer_for(row['customer_id']),
                },
                timestamps={'created_at': row['created_at']},
            )
            self.documents[row['id']] = document

    def _import_kb_chunks(self, rows):
        for row in rows:
            document = self.documents.get(row['document_id'])
            if document is None:
                raise CommandError(f'kb_chunks row {row["id"]} references unknown legacy document id {row["document_id"]}')
            self._upsert(
                'kb_chunks', KBChunk,
                {'document': document, 'chunk_index': row['chunk_index']},
                {
                    'content': row['text'],
                    'chunk_type': row['chunk_type'],
                    'metadata_json': parse_json_text(row['metadata_json'], {}, f'kb_chunks {row["id"]}.metadata_json'),
                    'embedding': parse_embedding(row['embedding']),
                    'source_path': to_stored_path(row['source_path']),
                    'page_or_section': row['page_or_section'] or '',
                },
                timestamps={'created_at': row['created_at']},
            )

    def _import_triage_assets(self, rows):
        for row in rows:
            asset, created = self._upsert(
                'triage_assets', TriageAsset,
                {'asset_key': row['asset_key']},
                {
                    'relative_path': row['relative_path'],
                    'caption_zh': row['caption_zh'] or '',
                    'source_url': row['source_url'] or '',
                    'license': row['license'] or '',
                    'width': row['width'],
                    'height': row['height'],
                },
                timestamps={'created_at': row['created_at']},
            )
            self.assets[row['id']] = asset
            if created and not self.dry_run and self.uploads_dir:
                data = self._legacy_file_bytes(row['relative_path'], f'triage_assets {row["asset_key"]}')
                if data is not None:
                    storage = private_storage()
                    name = storage_name_for_relative_path(row['relative_path'])
                    if not storage.exists(name):
                        storage.save(name, ContentFile(data))

    def _import_triage_playbooks(self, rows):
        for row in rows:
            playbook, _ = self._upsert(
                'triage_playbooks', TriagePlaybook,
                {'slug': row['slug']},
                {
                    'title_zh': row['title_zh'],
                    'symptom_summary_zh': row['symptom_summary_zh'],
                    'category': row['category'] or '',
                    'customer': self._customer_for(row['customer_id']),
                    'is_active': bool(row['is_active']),
                    'embedding': parse_embedding(row['embedding']),
                },
                timestamps={'created_at': row['created_at'], 'updated_at': row['updated_at']},
            )
            self.playbooks[row['id']] = playbook

    def _import_triage_steps(self, rows):
        for row in rows:
            playbook = self.playbooks.get(row['playbook_id'])
            if playbook is None:
                raise CommandError(f'triage_steps row {row["id"]} references unknown legacy playbook id {row["playbook_id"]}')
            self._upsert(
                'triage_steps', TriageStep,
                {'playbook': playbook, 'step_index': row['step_index']},
                {
                    'kind': row['kind'],
                    'prompt_zh': row['prompt_zh'],
                    'device_scope': row['device_scope'],
                    'expected_result_zh': row['expected_result_zh'] or '',
                    'asset': self.assets.get(row['asset_id']) if row['asset_id'] is not None else None,
                    'quick_replies_json': parse_json_text(row['quick_replies_json'], [], f'triage_steps {row["id"]}.quick_replies_json'),
                    'branch_map_json': parse_json_text(row['branch_map_json'], {}, f'triage_steps {row["id"]}.branch_map_json'),
                },
                timestamps={'created_at': row['created_at']},
            )

    def _import_triage_sessions(self, rows):
        for row in rows:
            playbook = self.playbooks.get(row['playbook_id'])
            if playbook is None:
                raise CommandError(f'triage_sessions row {row["id"]} references unknown legacy playbook id {row["playbook_id"]}')
            self._upsert(
                'triage_sessions', TriageSession,
                {'user': self._user_for(row['user_id']), 'session_id': row['session_id']},
                {
                    'playbook': playbook,
                    'current_step_index': row['current_step_index'],
                    'answers_json': parse_json_text(row['answers_json'], {}, f'triage_sessions {row["id"]}.answers_json'),
                    'status': row['status'],
                },
                timestamps={'created_at': row['created_at'], 'updated_at': row['updated_at']},
            )

    # -- reporting -------------------------------------------------------------

    def _note_skipped_tables(self):
        self.stdout.write('password_reset_codes: skipped by design (short-lived secrets do not travel).')
        if not self.uploads_dir:
            self._warn(
                'WENQU_LEGACY_UPLOADS_DIR is not set - upload blobs cannot be copied; '
                'upload_files rows are skipped and triage/avatar blobs are not transferred.'
            )

    def _print_report(self):
        verb = 'would import' if self.dry_run else 'imported'
        self.stdout.write('')
        self.stdout.write(f'{"table":<20}{"source":>8}{"created":>9}{"updated":>9}{"skipped":>9}{verb:>14}')
        mismatches = []
        for table, _ in TABLES:
            bucket = self.stats[table]
            imported = bucket['created'] + bucket['updated']
            self.stdout.write(
                f'{table:<20}{bucket["source"]:>8}{bucket["created"]:>9}'
                f'{bucket["updated"]:>9}{bucket["skipped"]:>9}{imported:>14}'
            )
            if imported != bucket['source']:
                mismatches.append(
                    f'{table}: source={bucket["source"]} {verb}={imported} '
                    f'(created={bucket["created"]} updated={bucket["updated"]} skipped={bucket["skipped"]})'
                )
        if mismatches:
            self.stdout.write(self.style.ERROR('COUNT MISMATCH - investigate before releasing:'))
            for line in mismatches:
                self.stdout.write(self.style.ERROR(f'  {line}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'All tables reconciled: source == {verb}.'))
        if self.warnings:
            self.stdout.write(self.style.WARNING(f'{len(self.warnings)} warning(s) emitted (see stderr).'))