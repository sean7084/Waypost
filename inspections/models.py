"""
Models for the Kering store device-inspection workflow.

This is a Kering-specific v1 domain that captures everything the legacy Feishu
questionnaire collected, so the WeChat mini program can replace it and the server
can regenerate the per-store Excel report + Photo folder.

Design notes:
- The generic `assets.Asset` model stays clean; Kering-specific device attributes
  (Usage, photo requirement, 大纲/注释, inspection-time readings) live on
  `InspectionDevice`, which links to `Asset` when a matching registered asset exists.
- `client_device_uid` / `client_photo_uid` are idempotency keys supplied by the
  offline-first mini program so re-synced submissions upsert instead of duplicating.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from inspections.services import thumbnails

from .constants import normalize_device_category


def inspection_photo_upload_path(instance, filename):
    """Store device/rack photos under the inspection's JDA/store folder."""
    return f'inspections/{instance.store_inspection_id}/photos/{filename}'


def inspection_signature_upload_path(instance, filename):
    return f'inspections/{instance.id}/signatures/{filename}'


def _inspection_media_folder(instance):
    """Human-readable MEDIA subfolder for deliverables: inspections/<jda>_<store>/."""
    store = (instance.store_name or '').replace(' ', '')
    jda = instance.jda_code or str(instance.id)
    return f'inspections/{jda}_{store}' if store else f'inspections/{jda}'


def inspection_report_upload_path(instance, filename):
    return f'{_inspection_media_folder(instance)}/{filename}'


def inspection_photo_zip_upload_path(instance, filename):
    # Retained for historical migration 0001_initial; the photo_zip field was
    # removed in 0006 (photos are now streamed on demand via bundle_export).
    # Django imports upload_to callables when loading the migration graph, so
    # this must stay importable even though no live field references it.
    return f'{_inspection_media_folder(instance)}/{filename}'


class InspectionBatch(models.Model):
    """A named grouping of store inspections (one planning/import cycle).

    A batch is created either by selecting a brand + date range (auto-spread
    across stores) or by uploading a schedule.xlsx (Kering master import). It
    gives the web UI a stable handle for "export the asset list for this batch"
    and "show this batch on the calendar".
    """

    class Source(models.TextChoices):
        MANUAL = 'manual', _('Manual')
        BRAND = 'brand', _('By Brand')
        UPLOAD = 'upload', _('Schedule Upload')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, verbose_name=_('Batch Name'))
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        related_name='inspection_batches',
        verbose_name=_('Company'),
    )
    division = models.ForeignKey(
        'companies.Division',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspection_batches',
        verbose_name=_('Brand / Division'),
    )
    engineer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspection_batches',
        verbose_name=_('Primary Engineer'),
    )
    start_date = models.DateField(verbose_name=_('Start Date'))
    end_date = models.DateField(verbose_name=_('End Date'))
    description = models.TextField(blank=True, verbose_name=_('Description'))
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        verbose_name=_('Source'),
    )
    import_run = models.ForeignKey(
        'companies.ImportRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspection_batches',
        verbose_name=_('Import Run'),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_inspection_batches',
        verbose_name=_('Created By'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Inspection Batch')
        verbose_name_plural = _('Inspection Batches')
        ordering = ['-start_date', '-created_at']
        indexes = [
            models.Index(fields=['company', '-start_date']),
            models.Index(fields=['division', '-start_date']),
        ]

    def __str__(self):
        return self.name

    def get_completion_percentage(self):
        """Roll up device collection progress across the batch."""
        total = InspectionDevice.objects.filter(store_inspection__batch=self).count()
        if total == 0:
            return 0
        done = InspectionDevice.objects.filter(
            store_inspection__batch=self, collected_at__isnull=False
        ).count()
        return round((done / total) * 100, 1)


class StoreInspection(models.Model):
    """One onsite store health-check for a given store and inspection date."""

    class Status(models.TextChoices):
        PLANNED = 'planned', _('Planned')
        IN_PROGRESS = 'in_progress', _('In Progress')
        COMPLETED = 'completed', _('Completed')
        SUBMITTED = 'submitted', _('Submitted')

    class WifiCoverage(models.TextChoices):
        GOOD = 'good', _('Good')
        WEAK = 'weak', _('Weak')
        NONE = 'none', _('None')

    class ItSupportRating(models.TextChoices):
        SATISFIED = 'satisfied', _('Satisfied')
        NORMAL = 'normal', _('Normal')
        UNSATISFIED = 'unsatisfied', _('Unsatisfied')

    class Slot(models.TextChoices):
        AM = 'am', _('Morning')
        PM = 'pm', _('Afternoon')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Batch grouping (web-UI planning cycle; nullable so existing rows stay valid)
    batch = models.ForeignKey(
        InspectionBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspections',
        verbose_name=_('Batch'),
    )

    # Organization context (Kering -> brand division -> store location)
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        related_name='store_inspections',
        verbose_name=_('Company'),
    )
    division = models.ForeignKey(
        'companies.Division',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='store_inspections',
        verbose_name=_('Brand / Division'),
    )
    location = models.ForeignKey(
        'companies.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='store_inspections',
        verbose_name=_('Store / Location'),
    )

    # Store identity (denormalized for the report cover/confirmation pages)
    jda_code = models.CharField(max_length=20, blank=True, verbose_name=_('Store JDA Code'))
    brand_name = models.CharField(max_length=100, blank=True, verbose_name=_('Brand'))
    store_name = models.CharField(max_length=100, blank=True, verbose_name=_('Store Name'))
    store_label = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Store Label'),
        help_text=_('Full label used on reports, e.g. "22149 Gucci SZOL".'),
    )

    # Scheduling / assignment
    inspection_date = models.DateField(verbose_name=_('Inspection Date'))
    slot = models.CharField(
        max_length=2,
        choices=Slot.choices,
        default=Slot.AM,
        verbose_name=_('Time Slot'),
        help_text=_('AM or PM batch within the inspection date.'),
    )
    engineer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_inspections',
        verbose_name=_('Onsite Engineer'),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
        verbose_name=_('Status'),
    )

    # cover_page - basic info / health-check result
    arriving_time = models.TimeField(null=True, blank=True, verbose_name=_('Arriving Time'))
    leaving_time = models.TimeField(null=True, blank=True, verbose_name=_('Leaving Time'))
    wifi_coverage = models.CharField(
        max_length=10,
        choices=WifiCoverage.choices,
        blank=True,
        verbose_name=_('WiFi Coverage'),
    )
    error_setting_fixed_count = models.PositiveIntegerField(default=0, verbose_name=_('Error Setting Fixed'))
    store_issue_found_count = models.PositiveIntegerField(default=0, verbose_name=_('Store Issue Found'))
    issue_to_follow_count = models.PositiveIntegerField(default=0, verbose_name=_('Issue To Be Followed Up'))

    # confirmation_page - IT-support rating, device counts
    it_support_rating = models.CharField(
        max_length=20,
        choices=ItSupportRating.choices,
        blank=True,
        verbose_name=_('IT Support Service Rating'),
    )
    it_support_comment = models.TextField(blank=True, verbose_name=_('IT Support Comment'))
    device_counts = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Confirmation Device Counts'),
        help_text=_('Engineer-confirmed counts keyed by confirmation_page label.'),
    )
    cover_extras = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Cover Page Extras'),
        help_text=_('WiFi SSID, Redwood/store-WiFi flags, rack size, music/ShopperTrak, etc.'),
    )

    # Signatures (captured on the mini program signoff page)
    store_signature = models.ImageField(
        upload_to=inspection_signature_upload_path, null=True, blank=True, verbose_name=_('Store Signature'))
    engineer_signature = models.ImageField(
        upload_to=inspection_signature_upload_path, null=True, blank=True, verbose_name=_('Engineer Signature'))
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Signed At'))

    notes = models.TextField(blank=True, verbose_name=_('Notes'))

    # Generated deliverables
    report_file = models.FileField(
        upload_to=inspection_report_upload_path, null=True, blank=True, verbose_name=_('Report Workbook'))
    report_generated_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Report Generated At'))

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_inspections',
        verbose_name=_('Created By'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Store Inspection')
        verbose_name_plural = _('Store Inspections')
        ordering = ['-inspection_date', '-created_at']
        constraints = [
            # One onsite round per store per slot per day. ``merge_duplicate_inspections``
            # is the pre-deploy cleanup this constraint assumes.
            #
            # ``slot`` is part of the key, not decoration: a store visited both
            # morning and afternoon is two legitimate rows, and the readers depend
            # on it - ``inspections/views.py`` builds the day view from
            # ``(date, AM)`` and ``(date, PM)`` buckets, and the bundle export
            # names one folder per slot. Constraining only (location, date) makes
            # that shape impossible.
            #
            # NULL is distinct in a unique index on BOTH backends (Postgres
            # DISTINCT_DEFAULT and SQLite's NULL handling), so a row with no
            # location is not constrained at all - an unlimited number of
            # location-less inspections may share one date and slot. Do not read
            # this constraint as "one inspection per date".
            models.UniqueConstraint(
                fields=['location', 'inspection_date', 'slot'],
                name='uniq_store_inspection_location_date_slot',
            ),
        ]
        indexes = [
            models.Index(fields=['engineer', 'status']),
            # No (location, inspection_date) index here on purpose: the
            # UniqueConstraint above is a btree over (location, inspection_date,
            # slot), and a composite index serves every left-prefix of itself -
            # so the (location) and (location, inspection_date) lookups it used
            # to do are exactly the ones the plain index did. Keeping both costs
            # a second index write per insert for no read benefit.
            models.Index(fields=['jda_code']),
            models.Index(fields=['batch', 'inspection_date']),
            models.Index(fields=['batch', 'inspection_date', 'slot']),
        ]

    def __str__(self):
        return self.store_label or f'{self.jda_code} {self.brand_name} {self.store_name}'.strip()

    def save(self, *args, **kwargs):
        if not self.store_label:
            parts = [self.jda_code, self.brand_name, self.store_name]
            self.store_label = ' '.join(part for part in parts if part).strip()
        super().save(*args, **kwargs)

    def get_completion_percentage(self):
        """Percent of expected devices that have been collected onsite."""
        total = self.devices.count()
        if total == 0:
            return 0
        done = self.devices.filter(collected_at__isnull=False).count()
        return round((done / total) * 100, 1)


class InspectionDevice(models.Model):
    """A single device line within a store inspection (expected or newly found)."""

    class Status(models.TextChoices):
        IN_STORE = 'in_store', _('In Store')
        NOT_IN_STORE = 'not_in_store', _('Not In Store')
        NEW = 'new', _('New')

    class IntactTag(models.TextChoices):
        YES = 'Y', _('Yes')
        NO = 'N', _('No')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_inspection = models.ForeignKey(
        StoreInspection,
        on_delete=models.CASCADE,
        related_name='devices',
        verbose_name=_('Store Inspection'),
    )
    asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspection_devices',
        verbose_name=_('Linked Asset'),
    )
    client_device_uid = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_('Client Device UID'),
        help_text=_('Idempotency key from the mini program; auto-filled when absent.'),
    )
    row_order = models.PositiveIntegerField(default=0, verbose_name=_('Row Order'))

    # Identity / expected values (from the master asset list)
    asset_id_text = models.CharField(max_length=100, blank=True, verbose_name=_('Asset ID'))
    category = models.CharField(max_length=50, blank=True, verbose_name=_('Category'))
    brand_model = models.CharField(max_length=200, blank=True, verbose_name=_('Brand-Model'))
    sn = models.CharField(max_length=100, blank=True, verbose_name=_('Serial Number'))
    usage = models.CharField(max_length=50, blank=True, verbose_name=_('Usage'))
    warranty_start = models.DateField(null=True, blank=True, verbose_name=_('Warranty Start'))
    photo_required = models.BooleanField(default=True, verbose_name=_('Photo Required'))
    outline = models.CharField(max_length=100, blank=True, verbose_name=_('Outline'))
    device_notes = models.CharField(max_length=255, blank=True, verbose_name=_('Device Notes'))

    # Collected readings (onsite)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_STORE,
        verbose_name=_('Status'),
    )
    ip_address = models.CharField(max_length=50, blank=True, verbose_name=_('IP Address'))
    cpu = models.CharField(max_length=100, blank=True, verbose_name=_('CPU'))
    memory = models.CharField(max_length=100, blank=True, verbose_name=_('Memory'))
    hdd = models.CharField(max_length=100, blank=True, verbose_name=_('HDD'))
    windows_version = models.CharField(max_length=100, blank=True, verbose_name=_('Windows Version'))
    ios_version = models.CharField(max_length=100, blank=True, verbose_name=_('iOS Version'))
    is_company_phone = models.BooleanField(default=False, verbose_name=_('Company Phone'))
    user_email = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('User (Email)'),
        help_text=_('Company-phone user; stored in email format.'),
    )
    drive_c_free_space = models.CharField(max_length=50, blank=True, verbose_name=_('Drive C Free Space'))
    intact_asset_tag = models.CharField(
        max_length=1, choices=IntactTag.choices, blank=True, verbose_name=_('Intact Asset Tag'))
    comment = models.TextField(blank=True, verbose_name=_('Comment'))
    is_new_device = models.BooleanField(default=False, verbose_name=_('New Device'))

    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='collected_inspection_devices',
        verbose_name=_('Collected By'),
    )
    collected_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Collected At'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Inspection Device')
        verbose_name_plural = _('Inspection Devices')
        ordering = ['row_order', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['store_inspection', 'client_device_uid'],
                name='uniq_inspection_device_uid',
            ),
        ]
        indexes = [
            models.Index(fields=['store_inspection', 'category']),
            models.Index(fields=['sn']),
        ]

    def __str__(self):
        identifier = self.sn or self.asset_id_text or str(self.pk)
        return f'{self.category} {identifier}'

    def save(self, *args, **kwargs):
        if not self.client_device_uid:
            self.client_device_uid = uuid.uuid4().hex
        # Keep category canonical so counts/photo naming/report all agree.
        self.category = normalize_device_category(self.category) or self.category
        super().save(*args, **kwargs)


class InspectionPhoto(models.Model):
    """A photo captured onsite (device overall/serial, or rack/network/issue)."""

    class Kind(models.TextChoices):
        OVERALL = 'overall', _('Device Overall')
        SERIAL = 'serial', _('Serial Number')
        RACK1 = 'rack1', _('Rack 1')
        RACK2 = 'rack2', _('Rack 2')
        RACK3 = 'rack3', _('Rack 3')
        ROUTER = 'router', _('Router')
        SWITCH = 'switch', _('Switch')
        PATCHPANEL = 'patchpanel', _('Patch Panel')
        SPEEDTEST_ETHERNET = 'speedtest_ethernet', _('Speedtest Ethernet')
        SPEEDTEST_WIFI = 'speedtest_wifi', _('Speedtest WiFi')
        ISSUE = 'issue', _('Issue')
        CASH_DRAWER = 'cash_drawer', _('Cash Drawer')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_inspection = models.ForeignKey(
        StoreInspection,
        on_delete=models.CASCADE,
        related_name='photos',
        verbose_name=_('Store Inspection'),
    )
    device = models.ForeignKey(
        InspectionDevice,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='photos',
        verbose_name=_('Device'),
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name=_('Kind'))
    image = models.ImageField(upload_to=inspection_photo_upload_path, verbose_name=_('Image'))
    client_photo_uid = models.CharField(
        max_length=64, blank=True, verbose_name=_('Client Photo UID'),
        help_text=_('Idempotency key from the mini program; auto-filled when absent.'),
    )
    display_name = models.CharField(
        max_length=255, blank=True, verbose_name=_('Display Name'),
        help_text=_('Final business filename assigned at report time.'),
    )
    sort_index = models.PositiveIntegerField(default=0, verbose_name=_('Sort Index'))
    captured_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Captured At'))
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_inspection_photos',
        verbose_name=_('Uploaded By'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Inspection Photo')
        verbose_name_plural = _('Inspection Photos')
        ordering = ['sort_index', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['store_inspection', 'client_photo_uid'],
                name='uniq_inspection_photo_uid',
            ),
        ]
        indexes = [
            models.Index(fields=['store_inspection', 'kind']),
        ]

    def __str__(self):
        return self.display_name or f'{self.get_kind_display()} {self.sort_index}'

    def save(self, *args, **kwargs):
        if not self.client_photo_uid:
            self.client_photo_uid = uuid.uuid4().hex
        super().save(*args, **kwargs)

    @property
    def thumbnail_url(self):
        """Sized-down ``thumbs/`` derivative URL, or the original when there is none.

        Serves both the DRF payload (the mini program's photo grid) and the web
        templates from one place, so a list never ships full-size JPEGs it will
        display 80px high. Falls back per row: photos stored before the thumbnail
        pipeline exists have no derivative until ``generate_photo_thumbnails``
        runs, and must still display.
        """
        return thumbnails.thumbnail_url_for(self)


class InspectionIssue(models.Model):
    """An entry in the cover_page issue list."""

    class Status(models.TextChoices):
        FIXED = 'fixed', _('Fixed')
        TO_BE_FOLLOWED = 'to_be_followed', _('To Be Followed')
        ESCALATED = 'escalated', _('Escalated')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_inspection = models.ForeignKey(
        StoreInspection,
        on_delete=models.CASCADE,
        related_name='issues',
        verbose_name=_('Store Inspection'),
    )
    seq = models.PositiveIntegerField(default=0, verbose_name=_('Sequence'))
    description = models.TextField(verbose_name=_('Description'))
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.TO_BE_FOLLOWED,
        verbose_name=_('Status'),
    )
    photo = models.ForeignKey(
        InspectionPhoto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issues',
        verbose_name=_('Photo'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Inspection Issue')
        verbose_name_plural = _('Inspection Issues')
        ordering = ['seq', 'created_at']

    def __str__(self):
        return f'#{self.seq} {self.description[:40]}'


class InspectionSignoffLog(models.Model):
    """Append-only trail of inspection signoff / submission events.

    Replaces the former generic ``audit.AuditLog`` write that the mini-program
    signoff endpoint used, keeping inspection events inside the inspections domain.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_inspection = models.ForeignKey(
        StoreInspection,
        on_delete=models.CASCADE,
        related_name='signoff_logs',
        verbose_name=_('Store Inspection'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inspection_signoff_logs',
        verbose_name=_('User'),
    )
    operation = models.CharField(
        max_length=20,
        default='signoff',
        verbose_name=_('Operation'),
    )
    description = models.TextField(verbose_name=_('Description'))
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_('Metadata'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Inspection Signoff Log')
        verbose_name_plural = _('Inspection Signoff Logs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store_inspection', '-created_at']),
        ]

    def __str__(self):
        return f'{self.operation} - {self.store_inspection} - {self.created_at:%Y-%m-%d %H:%M}'


class InspectionWifiWeakPoint(models.Model):
    """A recorded WiFi weak point captured onsite under 机柜/网络.

    Replaces the former boolean ``wifi_covers_store``: an inspection with zero
    weak points is considered good coverage; each weak point carries a location
    and a short description of the issue.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_inspection = models.ForeignKey(
        StoreInspection,
        on_delete=models.CASCADE,
        related_name='wifi_weak_points',
        verbose_name=_('Store Inspection'),
    )
    sort_index = models.PositiveIntegerField(default=0, verbose_name=_('Sort Index'))
    location = models.CharField(max_length=120, verbose_name=_('Weak Point Location'))
    description = models.CharField(max_length=255, verbose_name=_('Issue Description'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('WiFi Weak Point')
        verbose_name_plural = _('WiFi Weak Points')
        ordering = ['sort_index', 'created_at']
        indexes = [
            models.Index(fields=['store_inspection', 'sort_index']),
        ]

    def __str__(self):
        return f'{self.location}: {self.description[:40]}'
