"""
Models for Waypost - Accounts App.
This module defines the custom user model and related authentication models.
"""

import secrets
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from .crypto import decrypt_secret, encrypt_secret


class AdminRole(models.Model):
    """Persistent administrator roles that can be assigned additively to users."""

    code = models.CharField(max_length=64, unique=True, verbose_name=_('Role Code'))
    name = models.CharField(max_length=120, verbose_name=_('Role Name'))
    description = models.TextField(blank=True, verbose_name=_('Description'))
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Administrator Role')
        verbose_name_plural = _('Administrator Roles')
        ordering = ['name']

    def __str__(self):
        return self.name


class ServiceCity(models.Model):
    """A city/region a field engineer covers (multi-select on the engineer account).

    Managed by admins; the mini program records which cities an engineer serves
    (e.g. Beijing, Tianjin). Kept separate from companies.Location, which models
    individual stores rather than service areas.
    """

    name_en = models.CharField(max_length=80, unique=True, verbose_name=_('City (English)'))
    name_zh = models.CharField(max_length=80, blank=True, verbose_name=_('City (Chinese)'))
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Service City')
        verbose_name_plural = _('Service Cities')
        ordering = ['name_en']

    def __str__(self):
        if self.name_zh and self.name_zh != self.name_en:
            return f'{self.name_zh} ({self.name_en})'
        return self.name_zh or self.name_en


class User(AbstractUser):
    """
    Custom user model for Waypost with additional fields.
    """
    
    # Administrator role choices
    class AdminRole(models.TextChoices):
        SUPERADMIN = 'superadmin', _('Superadmin')
        IT_ADMINISTRATOR = 'it_administrator', _('IT Administrator')
        VIEWER = 'viewer', _('Viewer')
        ORDER_MANAGEMENT_SPECIALIST = 'order_management_specialist', _('Order Management Specialist')
        ORDER_MANAGEMENT_MANAGER = 'order_management_manager', _('Order Management Manager')
        INSPECTION_ENGINEER = 'inspection_engineer', _('Field Engineer')

    LANGUAGE_CHOICES = [
        ('en-us', _('English (US)')),
        ('zh-cn', _('Chinese (Simplified)')),
    ]

    LANGUAGE_CODE_ALIASES = {
        'en': 'en-us',
        'en-us': 'en-us',
        'zh-cn': 'zh-cn',
        'zh-hans': 'zh-cn',
        'zh-hant': 'zh-cn',
    }
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Administrator role assignments
    roles = models.ManyToManyField(
        'accounts.AdminRole',
        blank=True,
        related_name='users',
        verbose_name=_('Administrator Roles'),
        help_text=_('Roles for administrator access levels')
    )
    
    # Profile image
    profile_image = models.ImageField(
        upload_to='profiles/',
        null=True,
        blank=True,
        verbose_name=_('Profile Image')
    )
    
    # Additional user fields
    chinese_name = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name=_('Chinese Name'),
        help_text=_('Simplified-Chinese name; the mini program login looks engineers up by this.'),
    )
    employee_id = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name=_('Employee ID'),
        help_text=_('Unique employee identification number')
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Phone Number')
    )
    wechat_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name=_('WeChat ID'),
        help_text=_('WeChat account id used to look up field engineers.'),
    )
    invite_code = models.CharField(
        max_length=16,
        unique=True,
        null=True,
        blank=True,
        verbose_name=_('Invite Code'),
        help_text=_('One-time invite id offered to a field engineer on first mini-program use.'),
    )
    fe_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_('Field Engineer Rating'),
        help_text=_('1-5 quality rating tracked for field engineers.'),
    )
    fe_notes = models.TextField(
        blank=True,
        verbose_name=_('Field Engineer Notes'),
        help_text=_('Free-form notes tracking field-engineer quality.'),
    )
    department = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Department')
    )
    job_title = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Job Title')
    )
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='direct_reports',
        verbose_name=_('Manager')
    )
    
    # 2FA settings
    two_factor_enabled = models.BooleanField(
        default=False,
        verbose_name=_('Two-Factor Authentication Enabled')
    )
    force_2fa_setup = models.BooleanField(
        default=False,
        verbose_name=_('Force 2FA Setup'),
        help_text=_('User must set up 2FA on next login')
    )
    backup_tokens = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_('2FA Backup Tokens')
    )
    
    # Security settings
    must_change_password = models.BooleanField(
        default=False,
        verbose_name=_('Must Change Password'),
        help_text=_('User must change password on next login')
    )
    
    # Profile settings
    language_preference = models.CharField(
        max_length=10,
        default='en-us',
        choices=LANGUAGE_CHOICES,
        verbose_name=_('Language Preference')
    )
    timezone = models.CharField(
        max_length=50,
        default='UTC',
        verbose_name=_('Timezone')
    )
    
    # Company association
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='employees',
        verbose_name=_('Company')
    )
    division = models.ForeignKey(
        'companies.Division',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
        verbose_name=_('Division')
    )
    
    # Administrator access control
    # For IT Administrator role: company and/or division access
    managed_company = models.ForeignKey(
        'companies.Company',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='it_administrators',
        verbose_name=_('Managed Company'),
        help_text=_('Company this IT administrator has access to')
    )
    
    # For IT Administrator role: division-specific access
    managed_divisions = models.ManyToManyField(
        'companies.Division',
        blank=True,
        related_name='it_administrators',
        verbose_name=_('Managed Divisions'),
        help_text=_('Divisions this IT administrator has access to')
    )
    
    # For Viewer role: location-specific access
    managed_locations = models.ManyToManyField(
        'companies.Location',
        blank=True,
        related_name='viewers',
        verbose_name=_('Managed Locations'),
        help_text=_('Locations this viewer has read-only access to')
    )

    # For field engineers: cities/regions they cover (mini program account).
    service_cities = models.ManyToManyField(
        'accounts.ServiceCity',
        blank=True,
        related_name='engineers',
        verbose_name=_('Service Cities'),
        help_text=_('Cities/regions this field engineer covers (select multiple).')
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')
        ordering = ['username']
    
    def __str__(self):
        return f"{self.get_full_name()} ({self.username})" if self.get_full_name() else self.username

    @classmethod
    def normalize_language_code(cls, language_code):
        """Normalize legacy language codes to currently supported settings."""
        if not language_code:
            return 'en-us'
        return cls.LANGUAGE_CODE_ALIASES.get(language_code.lower(), 'en-us')

    @staticmethod
    def normalize_employee_id(employee_id):
        if employee_id is None:
            return None
        normalized_employee_id = str(employee_id).strip()
        return normalized_employee_id or None

    def clean(self):
        super().clean()
        self.employee_id = self.normalize_employee_id(self.employee_id)

    def save(self, *args, **kwargs):
        self.language_preference = self.normalize_language_code(self.language_preference)
        self.employee_id = self.normalize_employee_id(self.employee_id)
        self._ensure_invite_code()
        result = super().save(*args, **kwargs)
        pending_role_codes = getattr(self, '_pending_admin_role_codes', None)
        if pending_role_codes is not None:
            self.set_admin_roles(pending_role_codes)
            delattr(self, '_pending_admin_role_codes')
        return result

    def _ensure_invite_code(self):
        """Auto-generate a first-login invite code for field engineers."""
        if self.invite_code:
            return
        pending = getattr(self, '_pending_admin_role_codes', None)
        codes = set(pending) if pending is not None else set(self.get_admin_role_codes())
        if self.AdminRole.INSPECTION_ENGINEER in codes:
            self.invite_code = secrets.token_urlsafe(8)

    @classmethod
    def order_admin_role_codes(cls, role_codes):
        ordered_codes = []
        input_codes = [code for code in role_codes if code]
        for code, _label in cls.AdminRole.choices:
            if code in input_codes and code not in ordered_codes:
                ordered_codes.append(code)
        for code in input_codes:
            if code not in ordered_codes:
                ordered_codes.append(code)
        return ordered_codes

    @classmethod
    def admin_role_label(cls, role_code):
        return str(dict(cls.AdminRole.choices).get(role_code, role_code or ''))

    @property
    def admin_role(self):
        role_codes = self.get_admin_role_codes()
        return role_codes[0] if role_codes else ''

    @admin_role.setter
    def admin_role(self, value):
        if isinstance(value, (list, tuple, set)):
            self._pending_admin_role_codes = self.order_admin_role_codes(value)
        elif value:
            self._pending_admin_role_codes = [value]
        else:
            self._pending_admin_role_codes = []

    def has_admin_role(self, role_code):
        pending_role_codes = getattr(self, '_pending_admin_role_codes', None)
        if pending_role_codes is not None:
            return role_code in pending_role_codes
        if not self.pk:
            return False
        return self.roles.filter(code=role_code).exists()

    def get_admin_role_codes(self):
        pending_role_codes = getattr(self, '_pending_admin_role_codes', None)
        if pending_role_codes is not None:
            return self.order_admin_role_codes(pending_role_codes)
        if not self.pk:
            return []
        return self.order_admin_role_codes(self.roles.values_list('code', flat=True))

    def set_admin_roles(self, role_codes):
        normalized_codes = self.order_admin_role_codes(role_codes or [])
        if not self.pk:
            self._pending_admin_role_codes = normalized_codes
            return
        roles = AdminRole.objects.filter(code__in=normalized_codes)
        self.roles.set(roles)
        # Roles are only known after the M2M is set, so a user promoted to field
        # engineer post-creation still needs its first-login invite code.
        before = self.invite_code
        self._ensure_invite_code()
        if self.invite_code != before:
            self.save(update_fields=['invite_code'])

    def get_admin_role_display(self):
        return self.admin_role_label(self.admin_role)

    def get_admin_roles_display(self):
        role_codes = self.get_admin_role_codes()
        if not role_codes:
            return _('No admin roles')
        return ', '.join(self.admin_role_label(code) for code in role_codes)
    
    def get_display_name(self):
        """Get the user's display name (full name or username)."""
        return self.get_full_name() or self.username
    
    def get_full_name_display(self):
        """Get the user's display name for navigation."""
        full_name = self.get_full_name()
        if full_name:
            return full_name
        return self.username
    
    # Permission methods for the new admin role system
    def is_superadmin(self):
        """Check if user is a superadmin with access to all data."""
        return (self.is_superuser or 
                self.has_admin_role(self.AdminRole.SUPERADMIN))
    
    def is_it_administrator(self):
        """Check if user is an IT administrator with division/company access."""
        return self.has_admin_role(self.AdminRole.IT_ADMINISTRATOR)
    
    def is_viewer_admin(self):
        """Check if user is a viewer with location read-only access."""
        return self.has_admin_role(self.AdminRole.VIEWER)

    def is_order_management_specialist(self):
        """Check if user can work in day-to-day order-management flows."""
        return self.has_admin_role(self.AdminRole.ORDER_MANAGEMENT_SPECIALIST)

    def is_order_management_manager(self):
        """Check if user can approve and directly apply order-management pricing changes."""
        return self.has_admin_role(self.AdminRole.ORDER_MANAGEMENT_MANAGER)

    def is_order_management_procurement_specialist(self):
        """Compatibility alias for the legacy combined order-management role."""
        return self.is_order_management_specialist() or self.is_order_management_manager()

    def is_inspection_engineer(self):
        """Check if user can run onsite store device inspections (mini program)."""
        return self.has_admin_role(self.AdminRole.INSPECTION_ENGINEER)

    def is_field_engineer(self):
        """Alias of is_inspection_engineer(); the role is labelled 'Field Engineer'."""
        return self.is_inspection_engineer()
    
    def get_accessible_companies(self):
        """Get companies this admin can access."""
        from companies.models import Company

        if self.is_superadmin():
            return Company.objects.all()

        company_ids = set()
        if self.managed_company_id and self.is_it_administrator():
            company_ids.add(self.managed_company_id)
        if self.is_it_administrator():
            company_ids.update(self.managed_divisions.values_list('company_id', flat=True))
        if self.is_viewer_admin():
            company_ids.update(self.managed_locations.values_list('company_id', flat=True))
        return Company.objects.filter(id__in=company_ids)
    
    def get_accessible_divisions(self):
        """Get divisions this admin can access."""
        from companies.models import Division

        if self.is_superadmin():
            return Division.objects.all()

        division_ids = set()
        if self.managed_company_id and self.is_it_administrator():
            division_ids.update(self.managed_company.divisions.values_list('id', flat=True))
        if self.is_it_administrator():
            division_ids.update(self.managed_divisions.values_list('id', flat=True))
        if self.is_viewer_admin():
            division_ids.update(self.managed_locations.exclude(division__isnull=True).values_list('division_id', flat=True))
        return Division.objects.filter(id__in=division_ids)
    
    def get_accessible_locations(self):
        """Get locations this admin can access."""
        from companies.models import Location

        if self.is_superadmin():
            return Location.objects.all()

        location_ids = set()
        if self.managed_company_id and self.is_it_administrator():
            location_ids.update(self.managed_company.locations.values_list('id', flat=True))
        if self.is_it_administrator():
            location_ids.update(Location.objects.filter(division__in=self.managed_divisions.all()).values_list('id', flat=True))
        if self.is_viewer_admin():
            location_ids.update(self.managed_locations.values_list('id', flat=True))
        return Location.objects.filter(id__in=location_ids)
    
    def get_accessible_assets(self):
        """Get assets this admin can access."""
        from assets.models import Asset

        if self.is_superadmin():
            return Asset.objects.all()

        asset_queryset = Asset.objects.none()
        if self.managed_company_id and self.is_it_administrator():
            asset_queryset = asset_queryset | Asset.objects.filter(company=self.managed_company)
        if self.is_it_administrator():
            asset_queryset = asset_queryset | Asset.objects.filter(division__in=self.managed_divisions.all())
        if self.is_viewer_admin():
            asset_queryset = asset_queryset | Asset.objects.filter(location__in=self.managed_locations.all())
        return asset_queryset.distinct()
    
    def can_manage_assets(self):
        """Check if user can manage assets."""
        return (self.is_superadmin() or 
                self.is_it_administrator() or
                self.has_perm('assets.add_asset'))
    
    def can_view_assets(self):
        """Check if user can view assets (including read-only)."""
        return (self.is_superadmin() or 
                self.is_it_administrator() or
                self.is_viewer_admin() or
                self.has_perm('assets.view_asset'))
    
    def can_edit_assets(self):
        """Check if user can edit assets (excludes viewers)."""
        return (self.is_superadmin() or 
                self.is_it_administrator())
    
    def can_manage_companies(self):
        """Check if user can manage companies."""
        return (self.is_superadmin() or
                self.has_perm('companies.add_company'))
    
    def can_view_reports(self):
        """Check if user can view reports."""
        return (self.is_superadmin() or 
                self.is_it_administrator() or
                self.is_viewer_admin() or
                self.has_perm('reports.view_report'))
    
    def can_manage_users(self):
        """Check if user can manage other users (only superadmins)."""
        return (self.is_superadmin() or
                self.has_perm('accounts.add_user'))
    
    def can_manage_company_users(self):
        """Check if user can manage company users."""
        return self.is_superadmin()

    def can_manage_orders(self):
        """Check if user can access order-management workflows."""
        return (
            self.is_superadmin()
            or self.is_order_management_specialist()
            or self.is_order_management_manager()
        )

    def can_approve_order_management_prices(self):
        """Check if user can approve and directly apply price-list changes."""
        return self.is_superadmin() or self.is_order_management_manager()

    def can_import_product_prices(self):
        """Check if user can import live product prices into the system."""
        return self.can_approve_order_management_prices()

    def can_run_inspection(self, inspection=None):
        """Check if user can collect/submit a store device inspection.

        Superadmins and IT administrators may always act; inspection engineers may
        act on inspections assigned to them (or any when none is given).
        """
        if self.is_superadmin() or self.is_it_administrator():
            return True
        if not self.is_inspection_engineer():
            return False
        if inspection is not None:
            return inspection.engineer_id == self.id
        return True

    def can_view_inspections(self):
        """Web-UI read access to the inspections module (batches, calendar, exports)."""
        return self.is_superadmin() or self.is_it_administrator() or self.is_inspection_engineer()

    def can_manage_inspections(self):
        """Web-UI write access: create batches, import schedules, configure exports."""
        return self.is_superadmin() or self.is_it_administrator()

    def get_assigned_inspections(self):
        """Return store inspections this user is responsible for onsite."""
        from inspections.models import StoreInspection

        if self.is_superadmin() or self.is_it_administrator():
            return StoreInspection.objects.all()
        if self.is_inspection_engineer():
            return StoreInspection.objects.filter(engineer=self)
        return StoreInspection.objects.none()
    
    def get_role_display_name(self):
        """Get the display name for the user's admin role."""
        if self.get_admin_role_codes():
            return self.get_admin_roles_display()
        if hasattr(self, 'get_role_display') and getattr(self, 'role', None):
            return self.get_role_display()
        return _('Standard User')
    
    def get_access_scope_display(self):
        """Get a description of the user's access scope."""
        if self.is_superadmin():
            return str(_("All companies and data"))

        scopes = []
        if self.is_it_administrator():
            if self.managed_company:
                scopes.append(_("Company: {company}").format(company=self.managed_company.name))
            divisions = self.managed_divisions.all()
            if divisions.count() == 1:
                scopes.append(_("Division: {division}").format(division=divisions.first().name))
            elif divisions.count() > 1:
                scopes.append(_("{count} divisions").format(count=divisions.count()))
        if self.is_viewer_admin():
            locations = self.managed_locations.all()
            if locations.count() == 1:
                scopes.append(_("Location: {location}").format(location=locations.first().name))
            elif locations.count() > 1:
                scopes.append(_("{count} locations").format(count=locations.count()))
        if self.is_order_management_specialist():
            scopes.append(_("Order management workflows"))
        if self.is_order_management_manager():
            scopes.append(_("Order management management and price approvals"))
        return ", ".join(str(scope) for scope in scopes) if scopes else str(_("No admin access"))


class UserMailboxSettings(models.Model):
    """Per-user mailbox configuration for order-management workflows."""

    class ReceiveProtocol(models.TextChoices):
        IMAP = 'imap', _('IMAP')
        POP3 = 'pop3', _('POP3')

    class ConnectionSecurity(models.TextChoices):
        NONE = 'none', _('None')
        SSL_TLS = 'ssl_tls', _('SSL/TLS')
        STARTTLS = 'starttls', _('STARTTLS')

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mailbox_settings',
        verbose_name=_('User'),
    )
    email_address = models.EmailField(verbose_name=_('Email Address'))
    display_name = models.CharField(max_length=150, blank=True, verbose_name=_('Display Name'))
    username = models.CharField(max_length=255, verbose_name=_('Login Username'))
    encrypted_password = models.TextField(blank=True, verbose_name=_('Encrypted Password'))
    receive_protocol = models.CharField(
        max_length=10,
        choices=ReceiveProtocol.choices,
        default=ReceiveProtocol.IMAP,
        verbose_name=_('Receive Protocol'),
    )
    imap_host = models.CharField(max_length=255, blank=True, verbose_name=_('IMAP Host'))
    imap_port = models.PositiveIntegerField(default=993, verbose_name=_('IMAP Port'))
    imap_security = models.CharField(
        max_length=10,
        choices=ConnectionSecurity.choices,
        default=ConnectionSecurity.SSL_TLS,
        verbose_name=_('IMAP Security'),
    )
    pop3_host = models.CharField(max_length=255, blank=True, verbose_name=_('POP3 Host'))
    pop3_port = models.PositiveIntegerField(default=995, verbose_name=_('POP3 Port'))
    pop3_security = models.CharField(
        max_length=10,
        choices=ConnectionSecurity.choices,
        default=ConnectionSecurity.SSL_TLS,
        verbose_name=_('POP3 Security'),
    )
    smtp_host = models.CharField(max_length=255, verbose_name=_('SMTP Host'))
    smtp_port = models.PositiveIntegerField(default=465, verbose_name=_('SMTP Port'))
    smtp_security = models.CharField(
        max_length=10,
        choices=ConnectionSecurity.choices,
        default=ConnectionSecurity.SSL_TLS,
        verbose_name=_('SMTP Security'),
    )
    sync_lookback_months = models.PositiveIntegerField(default=6, verbose_name=_('Sync Lookback Months'))
    imap_sent_folder = models.CharField(max_length=120, default='Sent', verbose_name=_('IMAP Sent Folder'))
    sync_outbox = models.BooleanField(default=True, verbose_name=_('Sync Outbox'))
    auto_sync_enabled = models.BooleanField(default=True, verbose_name=_('Auto Sync Enabled'))
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    last_mailbox_sync_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Last Mailbox Sync At'))
    last_connection_test_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Last Connection Test At'))
    last_connection_status = models.CharField(max_length=40, blank=True, verbose_name=_('Last Connection Status'))
    last_connection_message = models.TextField(blank=True, verbose_name=_('Last Connection Message'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('User Mailbox Settings')
        verbose_name_plural = _('User Mailbox Settings')

    def __str__(self):
        return f'{self.user.username} mailbox'

    @property
    def password(self):
        return decrypt_secret(self.encrypted_password)

    def set_password(self, raw_password):
        self.encrypted_password = encrypt_secret(raw_password)

    def save(self, *args, **kwargs):
        if self.email_address and not self.display_name:
            self.display_name = self.user.get_display_name()
        super().save(*args, **kwargs)


class SystemSMTPSettings(models.Model):
    """Singleton SMTP configuration used for system-generated outbound email."""

    class ConnectionSecurity(models.TextChoices):
        NONE = 'none', _('None')
        SSL_TLS = 'ssl_tls', _('SSL/TLS')
        STARTTLS = 'starttls', _('STARTTLS')

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    from_email = models.EmailField(verbose_name=_('From Email'))
    from_display_name = models.CharField(max_length=150, blank=True, verbose_name=_('From Display Name'))
    username = models.CharField(max_length=255, blank=True, verbose_name=_('SMTP Username'))
    encrypted_password = models.TextField(blank=True, verbose_name=_('Encrypted Password'))
    smtp_host = models.CharField(max_length=255, verbose_name=_('SMTP Host'))
    smtp_port = models.PositiveIntegerField(default=587, verbose_name=_('SMTP Port'))
    smtp_security = models.CharField(
        max_length=10,
        choices=ConnectionSecurity.choices,
        default=ConnectionSecurity.STARTTLS,
        verbose_name=_('SMTP Security'),
    )
    timeout = models.PositiveIntegerField(default=15, verbose_name=_('Connection Timeout'))
    is_active = models.BooleanField(default=False, verbose_name=_('Active'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('System SMTP Settings')
        verbose_name_plural = _('System SMTP Settings')

    def __str__(self):
        return self.from_email or str(_('System SMTP Settings'))

    @classmethod
    def get_solo(cls):
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    @classmethod
    def get_active(cls):
        instance = cls.objects.filter(pk=1, is_active=True).first()
        if instance and instance.smtp_host and instance.from_email:
            return instance
        return None

    @property
    def password(self):
        return decrypt_secret(self.encrypted_password)

    def set_password(self, raw_password):
        self.encrypted_password = encrypt_secret(raw_password)

    @property
    def use_tls(self):
        return self.smtp_security == self.ConnectionSecurity.STARTTLS

    @property
    def use_ssl(self):
        return self.smtp_security == self.ConnectionSecurity.SSL_TLS


class ReceivedEmailMessage(models.Model):
    """Locally cached mailbox messages for the order-management email module."""

    class MessageDirection(models.TextChoices):
        INBOX = 'inbox', _('Inbox')
        OUTBOX = 'outbox', _('Outbox')

    class RFQStatus(models.TextChoices):
        UNREVIEWED = 'unreviewed', _('Unreviewed')
        CLASSIFIED_RFQ = 'classified_rfq', _('Classified RFQ')
        CLASSIFIED_NON_RFQ = 'classified_non_rfq', _('Classified Non-RFQ')
        QUOTATION_DRAFTED = 'quotation_drafted', _('Quotation Drafted')
        QUOTATION_CONFIRMED = 'quotation_confirmed', _('Quotation Confirmed')
        CLASSIFICATION_FAILED = 'classification_failed', _('Classification Failed')

    mailbox = models.ForeignKey(
        UserMailboxSettings,
        on_delete=models.CASCADE,
        related_name='received_messages',
        verbose_name=_('Mailbox'),
    )
    direction = models.CharField(
        max_length=10,
        choices=MessageDirection.choices,
        default=MessageDirection.INBOX,
        verbose_name=_('Direction'),
    )
    external_id = models.CharField(max_length=255, verbose_name=_('External ID'))
    message_id = models.CharField(max_length=255, blank=True, verbose_name=_('Message-ID'))
    folder_name = models.CharField(max_length=120, blank=True, verbose_name=_('Folder'))
    subject = models.CharField(max_length=255, blank=True, verbose_name=_('Subject'))
    sender = models.CharField(max_length=255, blank=True, verbose_name=_('Sender'))
    recipients = models.TextField(blank=True, verbose_name=_('Recipients'))
    received_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Received At'))
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Sent At'))
    body_preview = models.TextField(blank=True, verbose_name=_('Body Preview'))
    body_text = models.TextField(blank=True, verbose_name=_('Body Text'))
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_('Metadata'))
    rfq_status = models.CharField(
        max_length=40,
        choices=RFQStatus.choices,
        default=RFQStatus.UNREVIEWED,
        verbose_name=_('RFQ Status'),
    )
    rfq_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('RFQ Confidence'),
    )
    rfq_summary = models.TextField(blank=True, verbose_name=_('RFQ Summary'))
    rfq_extracted_data = models.JSONField(default=dict, blank=True, verbose_name=_('RFQ Extracted Data'))
    rfq_error = models.TextField(blank=True, verbose_name=_('RFQ Error'))
    rfq_processed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('RFQ Processed At'))
    is_read = models.BooleanField(default=False, verbose_name=_('Read'))
    synced_at = models.DateTimeField(auto_now=True, verbose_name=_('Synced At'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Received Email Message')
        verbose_name_plural = _('Received Email Messages')
        ordering = ['-received_at', '-sent_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['mailbox', 'direction', 'external_id'], name='uniq_mailbox_direction_external_message'),
        ]
        indexes = [
            models.Index(fields=['rfq_status', '-rfq_processed_at']),
            models.Index(fields=['direction', 'rfq_status']),
        ]

    def __str__(self):
        return self.subject or self.external_id

    @property
    def event_at(self):
        return self.received_at or self.sent_at or self.created_at
    
    # Legacy permission methods (for backward compatibility)
    def can_manage_assets_legacy(self):
        """Legacy method for asset management permissions."""
        return (self.role in ['admin', 'manager'] or 
                self.is_superuser or 
                self.has_perm('assets.add_asset'))
    
    def can_manage_companies_legacy(self):
        """Legacy method for company management permissions."""
        return (self.role == 'admin' or 
                self.is_superuser or
                self.has_perm('companies.add_company'))
    
    def can_view_reports_legacy(self):
        """Legacy method for reports view permissions."""
        return (self.role in ['admin', 'manager'] or 
                self.is_superuser or
                self.has_perm('reports.view_report'))
    
    def can_manage_users_legacy(self):
        """Legacy method for user management permissions."""
        return (self.role == 'admin' or 
                self.is_superuser or
                self.has_perm('accounts.add_user'))


class UserSession(models.Model):
    """
    Track user sessions for security and audit purposes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sessions_history',
        verbose_name=_('User')
    )
    session_key = models.CharField(
        max_length=40,
        unique=True,
        verbose_name=_('Session Key')
    )
    ip_address = models.GenericIPAddressField(
        verbose_name=_('IP Address')
    )
    user_agent = models.TextField(
        verbose_name=_('User Agent')
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Created At')
    )
    last_activity = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Last Activity')
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_('Active')
    )
    
    class Meta:
        verbose_name = _('User Session')
        verbose_name_plural = _('User Sessions')
        ordering = ['-last_activity']
    
    def __str__(self):
        return f"{self.user.username} - {self.ip_address}"


class LoginAttempt(models.Model):
    """
    Track login attempts for security monitoring.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        max_length=150,
        verbose_name=_('Username')
    )
    ip_address = models.GenericIPAddressField(
        verbose_name=_('IP Address')
    )
    user_agent = models.TextField(
        verbose_name=_('User Agent')
    )
    success = models.BooleanField(
        default=False,
        verbose_name=_('Successful')
    )
    failure_reason = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Failure Reason')
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Timestamp')
    )
    
    class Meta:
        verbose_name = _('Login Attempt')
        verbose_name_plural = _('Login Attempts')
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['username', '-timestamp']),
            models.Index(fields=['ip_address', '-timestamp']),
        ]
    
    def __str__(self):
        status = "Success" if self.success else "Failed"
        return f"{self.username} - {status} - {self.timestamp}"


class WeChatIdentity(models.Model):
    """Links a WeChat mini-program openid to a staff User.

    The mini program authenticates by binding an existing User on first launch
    (username/password + wx.login code) and then uses seamless wx.login afterwards.
    The sensitive WeChat session_key is intentionally NOT persisted; if encrypted
    user data is ever required it should be stored via accounts.crypto (Fernet).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wechat_identities',
        verbose_name=_('User'),
    )
    appid = models.CharField(
        max_length=64,
        verbose_name=_('WeChat AppID'),
        help_text=_('Mini-program AppID this openid belongs to.'),
    )
    openid = models.CharField(max_length=128, verbose_name=_('OpenID'))
    unionid = models.CharField(max_length=128, blank=True, verbose_name=_('UnionID'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('WeChat Identity')
        verbose_name_plural = _('WeChat Identities')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['appid', 'openid'], name='uniq_wechat_appid_openid'),
        ]
        indexes = [
            models.Index(fields=['user', 'appid']),
        ]

    def __str__(self):
        return f'{self.user} @ {self.appid}:{self.openid}'
