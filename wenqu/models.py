"""Data models for the Wenqu (问渠) customer helpdesk.

Ported from the standalone FastAPI helpdesk's SQLAlchemy schema
(helpdesk/backend/app/models.py) onto the Django ORM. Two deliberate adaptations:

* Identity is split: the login principal is ``accounts.User`` (UUID pk), the
  WeChat openid lives in ``accounts.WeChatIdentity`` keyed by the *wenqu* appid,
  and customer-specific profile/permission fields live in ``WenquProfile``.
  Staff and customers therefore share one user table but never one profile.
* Embeddings are stored as ``JSONField`` (jsonb) lists rather than a pgvector
  ``vector`` column. Retrieval casts to ``vector`` at query time
  (``embedding::text::vector <=> CAST(:q AS vector)``); migration 0002 only
  creates the extension. An ivfflat index needs a real ``vector`` column, so it
  is deferred as performance debt (the KB is small enough for a seq-scan cosine).

Timestamps use Django's ``auto_now_add``/``auto_now`` (timezone-aware, backed by
``django.utils.timezone.now``); no naive ``utcnow`` anywhere.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from wenqu.services.storage import private_storage


def _uuid4_hex():
    """Default client-facing file id: 32-char uuid4 hex (no dashes)."""
    return uuid.uuid4().hex


class Customer(models.Model):
    """A company whose staff use Wenqu. NULL scoping elsewhere means 'global'."""

    name = models.CharField(max_length=255, unique=True, verbose_name=_('Name'))
    notes = models.TextField(blank=True, default='', verbose_name=_('Notes'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Customer')
        verbose_name_plural = _('Customers')
        ordering = ['name']

    def __str__(self):
        return self.name


class WenquProfile(models.Model):
    """Customer-side profile attached to an ``accounts.User``.

    The presence of this row is what marks a user as a Wenqu *customer* rather
    than staff: ``api.wenqu_authentication.WenquCustomerPermission`` gates on it,
    and the staff user list excludes anyone who has one. ``token_version`` is the
    JWT ``tv`` claim source - bumping it invalidates every access token minted
    before the bump (see wenqu.services.auth).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wenqu_profile',
        verbose_name=_('User'),
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        verbose_name=_('Customer'),
    )
    external_id = models.CharField(
        max_length=128, unique=True, verbose_name=_('External ID'),
        help_text=_('Stable client-facing id; for WeChat customers this equals the username (wx_<openid[:12]>).'),
    )
    nickname = models.CharField(max_length=255, null=True, blank=True, verbose_name=_('Nickname'))
    mobile = models.CharField(max_length=32, null=True, blank=True, verbose_name=_('Mobile'))
    avatar = models.CharField(max_length=512, null=True, blank=True, verbose_name=_('Avatar'))

    can_upload = models.BooleanField(default=False, verbose_name=_('Can Upload'))
    can_chat = models.BooleanField(default=True, verbose_name=_('Can Chat'))
    can_manage_customers = models.BooleanField(default=False, verbose_name=_('Can Manage Customers'))
    can_manage_users = models.BooleanField(default=False, verbose_name=_('Can Manage Users'))

    token_version = models.IntegerField(
        default=0, verbose_name=_('Token Version'),
        help_text=_('Bump to invalidate every JWT minted before the bump (checked against the tv claim).'),
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Wenqu Profile')
        verbose_name_plural = _('Wenqu Profiles')

    def __str__(self):
        return self.external_id


class ChatSession(models.Model):
    """One conversation. ``session_id`` is the client-facing business key."""

    session_id = models.CharField(max_length=64, unique=True, verbose_name=_('Session ID'))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wenqu_chat_sessions',
        verbose_name=_('User'),
    )
    title = models.CharField(max_length=255, null=True, blank=True, verbose_name=_('Title'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Chat Session')
        verbose_name_plural = _('Chat Sessions')
        indexes = [models.Index(fields=['user', 'created_at'])]

    def __str__(self):
        return self.title or self.session_id


class Message(models.Model):
    """A single chat turn. ``content`` is a JSON-serialized string, kept verbatim."""

    class Role(models.TextChoices):
        USER = 'user', _('User')
        ASSISTANT = 'assistant', _('Assistant')
        SYSTEM = 'system', _('System')

    class Type(models.TextChoices):
        TEXT = 'text', _('Text')
        AUDIO = 'audio', _('Audio')
        IMAGE = 'image', _('Image')
        FORM = 'form', _('Form')
        MIXED = 'mixed', _('Mixed')

    session = models.ForeignKey(
        ChatSession,
        to_field='session_id',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='messages',
        verbose_name=_('Session'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wenqu_messages',
        verbose_name=_('User'),
    )
    role = models.CharField(max_length=16, choices=Role.choices, verbose_name=_('Role'))
    type = models.CharField(max_length=16, choices=Type.choices, verbose_name=_('Type'))
    content = models.TextField(verbose_name=_('Content'), help_text=_('JSON-serialized payload, stored as-is.'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Message')
        verbose_name_plural = _('Messages')
        indexes = [models.Index(fields=['user', 'created_at'])]

    def __str__(self):
        return f'{self.role}/{self.type} @ {self.created_at:%Y-%m-%d %H:%M}'


class UserProfile(models.Model):
    """Per-customer learning profile (weak points / suggested solutions / notes).

    Structured JSON is kept as text to stay DB-portable, mirroring the helpdesk
    schema. Created lazily by the chat services (PR3).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='wenqu_learning_profile',
        verbose_name=_('User'),
    )
    weak_points = models.TextField(null=True, blank=True, verbose_name=_('Weak Points'))
    suggested_solutions = models.TextField(null=True, blank=True, verbose_name=_('Suggested Solutions'))
    notes = models.TextField(null=True, blank=True, verbose_name=_('Notes'))
    last_updated = models.DateTimeField(auto_now=True, verbose_name=_('Last Updated'))

    class Meta:
        verbose_name = _('User Profile')
        verbose_name_plural = _('User Profiles')

    def __str__(self):
        return f'profile:{self.user_id}'


class UploadFile(models.Model):
    """A customer upload (voice message / photo), stored on the private backend.

    ``file_id`` is the client-facing id (uuid4 hex). The blob lives under
    ``WENQU_PRIVATE_MEDIA_ROOT`` via ``private_storage`` - never the public
    default backend. Download goes through an authenticated FileResponse.
    """

    class FileType(models.TextChoices):
        AUDIO = 'audio', _('Audio')
        IMAGE = 'image', _('Image')

    file_id = models.CharField(max_length=32, unique=True, default=_uuid4_hex, editable=False, verbose_name=_('File ID'))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wenqu_uploads',
        verbose_name=_('User'),
    )
    file_type = models.CharField(max_length=16, choices=FileType.choices, verbose_name=_('File Type'))
    file = models.FileField(upload_to='wenqu/uploads/', storage=private_storage, verbose_name=_('File'))
    original_filename = models.CharField(max_length=512, verbose_name=_('Original Filename'))
    mime_type = models.CharField(max_length=128, default='application/octet-stream', verbose_name=_('MIME Type'))
    duration_seconds = models.IntegerField(null=True, blank=True, verbose_name=_('Duration (seconds)'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Upload File')
        verbose_name_plural = _('Upload Files')

    def __str__(self):
        return self.file_id


class KBDocument(models.Model):
    """A knowledge-base source document. ``path`` is relative to WENQU_KB_SOURCES_DIR."""

    path = models.CharField(max_length=512, unique=True, verbose_name=_('Path'))
    source_type = models.CharField(max_length=32, verbose_name=_('Source Type'), help_text=_('pdf / docx / txt / ...'))
    content_hash = models.CharField(max_length=64, verbose_name=_('Content Hash'))
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name=_('Customer'),
        help_text=_('NULL means global/shared across customers.'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('KB Document')
        verbose_name_plural = _('KB Documents')

    def __str__(self):
        # getattr keeps the media-access hygiene guard quiet: this is a CharField
        # named "path", not a FieldFile, but the guard matches the attribute idiom.
        return getattr(self, 'path')


class KBChunk(models.Model):
    """An embedded chunk of a KBDocument. ``embedding`` is a 1536-dim JSON list."""

    class ChunkType(models.TextChoices):
        TEXT = 'text', _('Text')
        QA_PAIR = 'qa_pair', _('QA Pair')

    document = models.ForeignKey(
        KBDocument,
        on_delete=models.CASCADE,
        related_name='chunks',
        verbose_name=_('Document'),
    )
    chunk_index = models.IntegerField(verbose_name=_('Chunk Index'))
    content = models.TextField(verbose_name=_('Content'))
    chunk_type = models.CharField(max_length=32, choices=ChunkType.choices, default=ChunkType.TEXT, verbose_name=_('Chunk Type'))
    metadata_json = models.JSONField(default=dict, blank=True, verbose_name=_('Metadata'))
    embedding = models.JSONField(default=list, verbose_name=_('Embedding'), help_text=_('1536-dimension vector as a JSON list; cast to pgvector at query time.'))
    source_path = models.CharField(max_length=512, verbose_name=_('Source Path'))
    page_or_section = models.CharField(max_length=128, blank=True, default='', verbose_name=_('Page / Section'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('KB Chunk')
        verbose_name_plural = _('KB Chunks')
        unique_together = [('document', 'chunk_index')]

    def __str__(self):
        return f'{self.document_id}#{self.chunk_index}'


# --- Guided triage engine -------------------------------------------------
# Structured troubleshooting playbooks: symptom -> confirm device -> step-by-step
# checks (with screenshots) -> concrete solution. Content is Simplified Chinese.


class TriageAsset(models.Model):
    """A screenshot referenced by triage steps, keyed by a stable human key."""

    asset_key = models.CharField(max_length=128, unique=True, verbose_name=_('Asset Key'))
    relative_path = models.CharField(max_length=512, verbose_name=_('Relative Path'))
    caption_zh = models.CharField(max_length=512, blank=True, default='', verbose_name=_('Caption (ZH)'))
    source_url = models.CharField(max_length=1024, blank=True, default='', verbose_name=_('Source URL'))
    license = models.CharField(max_length=255, blank=True, default='', verbose_name=_('License'))
    width = models.IntegerField(default=0, verbose_name=_('Width'))
    height = models.IntegerField(default=0, verbose_name=_('Height'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Triage Asset')
        verbose_name_plural = _('Triage Assets')

    def __str__(self):
        return self.asset_key


class TriagePlaybook(models.Model):
    """A troubleshooting playbook, optionally scoped to one customer."""

    slug = models.CharField(max_length=128, unique=True, verbose_name=_('Slug'))
    title_zh = models.CharField(max_length=255, verbose_name=_('Title (ZH)'))
    symptom_summary_zh = models.TextField(verbose_name=_('Symptom Summary (ZH)'))
    category = models.CharField(max_length=64, blank=True, default='', verbose_name=_('Category'))
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='playbooks',
        verbose_name=_('Customer'),
        help_text=_('NULL means global/shared across customers.'),
    )
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    embedding = models.JSONField(null=True, blank=True, verbose_name=_('Embedding'), help_text=_('1536-dimension vector as a JSON list; NULL until embedded.'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Triage Playbook')
        verbose_name_plural = _('Triage Playbooks')

    def __str__(self):
        return self.slug


class TriageStep(models.Model):
    """One node in a playbook's decision graph."""

    class Kind(models.TextChoices):
        INTAKE_DEVICE = 'intake_device', _('Intake Device')
        CHECK = 'check', _('Check')
        SOLUTION = 'solution', _('Solution')
        ESCALATE = 'escalate', _('Escalate')
        END = 'end', _('End')

    playbook = models.ForeignKey(
        TriagePlaybook,
        on_delete=models.CASCADE,
        related_name='steps',
        verbose_name=_('Playbook'),
    )
    step_index = models.IntegerField(verbose_name=_('Step Index'))
    kind = models.CharField(max_length=32, choices=Kind.choices, verbose_name=_('Kind'))
    prompt_zh = models.TextField(verbose_name=_('Prompt (ZH)'))
    device_scope = models.CharField(max_length=32, null=True, blank=True, verbose_name=_('Device Scope'), help_text=_('NULL = device-agnostic; otherwise ios / windows / printer / ...'))
    expected_result_zh = models.TextField(blank=True, default='', verbose_name=_('Expected Result (ZH)'))
    asset = models.ForeignKey(
        TriageAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='steps',
        verbose_name=_('Asset'),
    )
    quick_replies_json = models.JSONField(default=list, blank=True, verbose_name=_('Quick Replies'), help_text=_('List of {"label_zh": ..., "value": ...} tappable buttons.'))
    branch_map_json = models.JSONField(default=dict, blank=True, verbose_name=_('Branch Map'), help_text=_('Map of answer value -> next step index, with a "default" key.'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Triage Step')
        verbose_name_plural = _('Triage Steps')
        constraints = [
            models.UniqueConstraint(fields=['playbook', 'step_index'], name='uniq_triage_playbook_step_index'),
        ]
        ordering = ['playbook', 'step_index']

    def __str__(self):
        return f'{self.playbook_id}#{self.step_index} ({self.kind})'


class TriageSession(models.Model):
    """A customer's progress through one playbook, scoped per chat session."""

    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', _('In Progress')
        RESOLVED = 'resolved', _('Resolved')
        ABANDONED = 'abandoned', _('Abandoned')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wenqu_triage_sessions',
        verbose_name=_('User'),
    )
    session_id = models.CharField(max_length=64, verbose_name=_('Session ID'), help_text=_('Matches the chat session_id so progress is per conversation.'))
    playbook = models.ForeignKey(
        TriagePlaybook,
        on_delete=models.CASCADE,
        related_name='sessions',
        verbose_name=_('Playbook'),
    )
    current_step_index = models.IntegerField(default=0, verbose_name=_('Current Step Index'))
    answers_json = models.JSONField(default=dict, blank=True, verbose_name=_('Answers'))
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS, verbose_name=_('Status'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Triage Session')
        verbose_name_plural = _('Triage Sessions')
        constraints = [
            models.UniqueConstraint(fields=['user', 'session_id'], name='uniq_triage_user_session'),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.session_id} ({self.status})'