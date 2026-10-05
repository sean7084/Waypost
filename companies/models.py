"""
Company and Division models for Waypost.
This module defines the organizational structure models that support
multi-company and multi-division asset management.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import RegexValidator
from django.contrib.auth import get_user_model
import re

from quotations.template_registry import (
    DEFAULT_QUOTATION_TEMPLATE,
    get_quotation_template_choices,
)


class Company(models.Model):
    """
    Company model representing different organizations using the system.
    Each company can have multiple divisions and manage their own assets.
    """
    
    # Company status choices
    class CompanyStatus(models.TextChoices):
        ACTIVE = 'active', _('Active')
        INACTIVE = 'inactive', _('Inactive')
        SUSPENDED = 'suspended', _('Suspended')
    
    # Basic company information
    name = models.CharField(
        max_length=200,
        unique=True,
        verbose_name=_('Company Name'),
        help_text=_('Official name of the company')
    )
    
    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name=_('Company Code'),
        help_text=_('Unique identifier code for the company (used in asset numbering)')
    )
    
    description = models.TextField(
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Brief description of the company')
    )
    
    # Contact information
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message=_("Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed.")
    )
    
    phone_number = models.CharField(
        validators=[phone_regex],
        max_length=17,
        blank=True,
        verbose_name=_('Phone Number')
    )
    
    email = models.EmailField(
        blank=True,
        verbose_name=_('Email'),
        help_text=_('Company contact email')
    )

    primary_contact_company_user = models.ForeignKey(
        'CompanyUser',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='primary_contact_for_companies',
        verbose_name=_('Primary Contact'),
        help_text=_('Primary business contact selected from company users')
    )

    default_quotation_template = models.CharField(
        max_length=20,
        choices=get_quotation_template_choices(),
        default=DEFAULT_QUOTATION_TEMPLATE,
        verbose_name=_('Default Quotation Template'),
        help_text=_('Default PDF template used for new quotations for this company')
    )
    
    website = models.URLField(
        blank=True,
        verbose_name=_('Website'),
        help_text=_('Company website URL')
    )
    
    # Address information
    address_line1 = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Address Line 1')
    )
    
    address_line2 = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Address Line 2')
    )
    
    city = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('City')
    )
    
    state_province = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('State/Province')
    )
    
    postal_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Postal Code')
    )
    
    country = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Country')
    )
    
    # Company settings
    status = models.CharField(
        max_length=20,
        choices=CompanyStatus.choices,
        default=CompanyStatus.ACTIVE,
        verbose_name=_('Status'),
        help_text=_('Current status of the company')
    )
    
    logo = models.ImageField(
        upload_to='companies/logos/',
        blank=True,
        null=True,
        verbose_name=_('Company Logo'),
        help_text=_('Upload company logo (optional)')
    )
    
    # Asset numbering settings
    asset_prefix = models.CharField(
        max_length=10,
        blank=True,
        verbose_name=_('Asset Prefix'),
        help_text=_('Prefix used for asset numbering (e.g., "COMP-")')
    )
    
    next_asset_number = models.PositiveIntegerField(
        default=1,
        verbose_name=_('Next Asset Number'),
        help_text=_('Next number to be used for asset numbering')
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('Company')
        verbose_name_plural = _('Companies')
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def get_full_address(self):
        """Return the full formatted address."""
        address_parts = [
            self.address_line1,
            self.address_line2,
            self.city,
            self.state_province,
            self.postal_code,
            self.country
        ]
        return ', '.join(filter(None, address_parts))
    
    def generate_next_asset_number(self):
        """Generate the next asset number for this company."""
        asset_number = f"{self.asset_prefix}{self.next_asset_number}"
        self.next_asset_number += 1
        self.save()
        return asset_number

    def get_primary_contact(self):
        """Return the selected primary contact membership."""
        return self.primary_contact_company_user


class Division(models.Model):
    """
    Division model representing departments or divisions within a company.
    Each division can have its own asset management and user access controls.
    """
    
    # Division status choices
    class DivisionStatus(models.TextChoices):
        ACTIVE = 'active', _('Active')
        INACTIVE = 'inactive', _('Inactive')
        MERGED = 'merged', _('Merged')
    
    # Basic division information
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='divisions',
        verbose_name=_('Company'),
        help_text=_('Company this division belongs to')
    )
    
    name = models.CharField(
        max_length=200,
        verbose_name=_('Division Name'),
        help_text=_('Name of the division or department')
    )
    
    code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Division Code'),
        help_text=_('Unique identifier code for the division within the company (optional)')
    )
    
    description = models.TextField(
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Brief description of the division')
    )
    
    # Division hierarchy
    parent_division = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='subdivisions',
        verbose_name=_('Parent Division'),
        help_text=_('Parent division if this is a sub-division')
    )
    
    # Contact information
    manager = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='division_manager_for',
        verbose_name=_('Division Manager'),
        help_text=_('User responsible for managing this division')
    )
    
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message=_("Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed.")
    )
    
    phone_number = models.CharField(
        validators=[phone_regex],
        max_length=17,
        blank=True,
        verbose_name=_('Phone Number')
    )
    
    email = models.EmailField(
        blank=True,
        verbose_name=_('Email'),
        help_text=_('Division contact email')
    )
    
    # Location information
    location = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Location'),
        help_text=_('Physical location of the division')
    )
    
    building = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Building'),
        help_text=_('Building name or number')
    )
    
    floor = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('Floor'),
        help_text=_('Floor number or name')
    )
    
    room = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('Room'),
        help_text=_('Room number or name')
    )
    
    # Division settings
    status = models.CharField(
        max_length=20,
        choices=DivisionStatus.choices,
        default=DivisionStatus.ACTIVE,
        verbose_name=_('Status'),
        help_text=_('Current status of the division')
    )
    
    # Budget and cost center
    budget_code = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('Budget Code'),
        help_text=_('Budget or cost center code for this division')
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))


class Location(models.Model):
    """
    Location model representing physical locations within companies.
    Locations can be buildings, floors, rooms, or any physical space
    where assets can be placed.
    """
    
    class LocationType(models.TextChoices):
        WAREHOUSE = 'warehouse', _('Warehouse')
        OFFICE = 'office', _('Office')
        STORE = 'store', _('Store')
        OTHER = 'other', _('Other')
    
    class LocationStatus(models.TextChoices):
        ACTIVE = 'active', _('Active')
        CLOSED = 'closed', _('Closed')
        UNDER_CONSTRUCTION = 'under_construction', _('Under Construction')
    
    # Basic location information
    name = models.CharField(
        max_length=200,
        verbose_name=_('Location Name'),
        help_text=_('Name of the location')
    )

    name_en = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Location Name (English)'),
        help_text=_('English name of the location')
    )
    
    code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Location Code'),
        help_text=_('Unique code for the location (e.g., B1-F3-R101)')
    )

    code_2 = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('Location Code 2'),
        help_text=_('Secondary code for the location (optional)')
    )

    zone = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Zone'),
        help_text=_('Optional zone identifier')
    )

    rack = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Rack'),
        help_text=_('Optional rack identifier')
    )

    shelf = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Shelf'),
        help_text=_('Optional shelf identifier')
    )
    
    description = models.TextField(
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Detailed description of the location')
    )
    
    # Hierarchy
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='locations',
        verbose_name=_('Company')
    )
    
    division = models.ForeignKey(
        'Division',
        on_delete=models.CASCADE,
        related_name='locations',
        blank=True,
        null=True,
        verbose_name=_('Division')
    )
    
    parent_location = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='child_locations',
        blank=True,
        null=True,
        verbose_name=_('Parent Location'),
        help_text=_('Parent location in hierarchy (e.g., floor for room)')
    )
    
    # Location details
    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
        default=LocationType.OTHER,
        verbose_name=_('Location Type')
    )
    
    status = models.CharField(
        max_length=20,
        choices=LocationStatus.choices,
        default=LocationStatus.ACTIVE,
        verbose_name=_('Status')
    )
    
    # Physical details
    area_size = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name=_('Area Size (sq meters)'),
        help_text=_('Size of the location in square meters')
    )
    
    capacity = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_('Capacity'),
        help_text=_('Maximum capacity (people, assets, etc.)')
    )
    
    # Address information
    address_line1 = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Address Line 1')
    )
    
    address_line2 = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Address Line 2')
    )
    
    city = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('City')
    )
    
    state_province = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('State/Province')
    )
    
    postal_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Postal Code')
    )
    
    country = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Country')
    )

    chinese_address = models.TextField(
        blank=True,
        verbose_name=_('Chinese Address')
    )
    
    # Contact information
    manager = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        related_name='location_manager_for',
        blank=True,
        null=True,
        verbose_name=_('Location Manager')
    )

    contact = models.ForeignKey(
        'CompanyUser',
        on_delete=models.SET_NULL,
        related_name='location_contacts',
        blank=True,
        null=True,
        verbose_name=_('Location Contact')
    )
    
    phone_number = models.CharField(
        max_length=17,
        blank=True,
        verbose_name=_('Phone Number')
    )
    
    email = models.EmailField(
        blank=True,
        verbose_name=_('Email')
    )
    
    # Coordinates for mapping
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        verbose_name=_('Latitude'),
        help_text=_('GPS latitude coordinate')
    )
    
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        verbose_name=_('Longitude'),
        help_text=_('GPS longitude coordinate')
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('Location')
        verbose_name_plural = _('Locations')
        ordering = ['company', 'name']
        unique_together = [['company', 'code']]

    @staticmethod
    def _expand_range_expression(expression):
        """Expand inputs like 'Z1-Z3, Z5' into ['Z1', 'Z2', 'Z3', 'Z5']."""
        if not expression:
            return []

        values = []
        for raw_part in str(expression).split(','):
            part = raw_part.strip()
            if not part:
                continue

            if '-' not in part:
                values.append(part)
                continue

            start, end = [item.strip() for item in part.split('-', 1)]
            start_match = re.fullmatch(r'([A-Za-z]*)(\d+)', start)
            end_match = re.fullmatch(r'([A-Za-z]*)(\d+)', end)

            if not start_match or not end_match:
                values.append(part)
                continue

            start_prefix, start_num = start_match.group(1), int(start_match.group(2))
            end_prefix, end_num = end_match.group(1), int(end_match.group(2))
            if start_prefix != end_prefix or end_num < start_num:
                values.append(part)
                continue

            pad_width = max(len(start_match.group(2)), len(end_match.group(2)))
            values.extend([
                f"{start_prefix}{str(number).zfill(pad_width)}"
                for number in range(start_num, end_num + 1)
            ])

        deduped = []
        seen = set()
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(value)
        return deduped

    def is_warehouse(self):
        return self.location_type == self.LocationType.WAREHOUSE

    def expanded_zones(self):
        return self._expand_range_expression(self.zone) if self.is_warehouse() else []

    def expanded_racks(self):
        return self._expand_range_expression(self.rack) if self.is_warehouse() else []

    def expanded_shelves(self):
        return self._expand_range_expression(self.shelf) if self.is_warehouse() else []

    def get_slot_count(self):
        zones = self.expanded_zones()
        racks = self.expanded_racks()
        shelves = self.expanded_shelves()
        if not zones or not racks or not shelves:
            return 0
        return len(zones) * len(racks) * len(shelves)

    def get_naming_scheme(self):
        """Return zone-rack-shelf token, skipping blank values."""
        if not self.is_warehouse():
            return ''
        parts = [
            (self.zone or '').strip(),
            (self.rack or '').strip(),
            (self.shelf or '').strip(),
        ]
        parts = [part for part in parts if part]
        return '-'.join(parts)

    def get_display_location_code(self):
        """Prefer structured naming scheme; fall back to legacy code."""
        scheme = self.get_naming_scheme()
        if scheme:
            return scheme
        return (self.code or '').strip()

    def save(self, *args, **kwargs):
        # Enforce warehouse-only slot fields to keep location semantics consistent.
        if not self.is_warehouse():
            self.zone = None
            self.rack = None
            self.shelf = None
        super().save(*args, **kwargs)
    
    def __str__(self):
        display_code = self.get_display_location_code()
        if display_code:
            return f"{self.company.name} - {self.name} ({display_code})"
        return f"{self.company.name} - {self.name}"
    
    def get_full_path(self):
        """Return the full hierarchical path of the location."""
        path = [self.name]
        parent = self.parent_location
        while parent:
            path.insert(0, parent.name)
            parent = parent.parent_location
        return " > ".join(path)
    
    def get_full_address(self):
        """Return formatted address string."""
        address_parts = []
        if self.address_line1:
            address_parts.append(self.address_line1)
        if self.address_line2:
            address_parts.append(self.address_line2)
        if self.city:
            address_parts.append(self.city)
        if self.state_province:
            address_parts.append(self.state_province)
        if self.postal_code:
            address_parts.append(self.postal_code)
        if self.country:
            address_parts.append(self.country)
        return ", ".join(address_parts)
    
    def get_children_count(self):
        """Return count of child locations."""
        return self.child_locations.count()
    
    def get_assets_count(self):
        """Return count of assets in this location."""
        from assets.models import Asset
        return Asset.objects.filter(location=self).count()


class CompanyUser(models.Model):
    """
    Company contact record used for recipient/business contact workflows.
    Contact rows can optionally link to an authentication user, but are
    maintained independently from the User database.
    """
    
    # User role choices for company access
    class CompanyRole(models.TextChoices):
        EMPLOYEE = 'employee', _('Employee')
        MANAGER = 'manager', _('Manager')
        ADMIN = 'admin', _('Company Admin')
        VIEWER = 'viewer', _('Viewer')
    
    # User status within company
    class UserStatus(models.TextChoices):
        ACTIVE = 'active', _('Active')
        INACTIVE = 'inactive', _('Inactive')
        SUSPENDED = 'suspended', _('Suspended')
    
    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='company_memberships',
        verbose_name=_('Linked User')
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name=_('Name'),
        help_text=_('Primary contact/recipient name')
    )
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='company_users',
        verbose_name=_('Company')
    )
    
    role = models.CharField(
        max_length=20,
        choices=CompanyRole.choices,
        default=CompanyRole.EMPLOYEE,
        verbose_name=_('Role'),
        help_text=_('User role within the company')
    )
    
    division = models.ForeignKey(
        Division,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='division_users',
        verbose_name=_('Division'),
        help_text=_('Primary division for this user')
    )
    
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='location_users',
        verbose_name=_('Primary Location'),
        help_text=_('Primary work location for this user')
    )
    
    status = models.CharField(
        max_length=20,
        choices=UserStatus.choices,
        default=UserStatus.ACTIVE,
        verbose_name=_('Status')
    )
    
    # Employee details
    employee_id = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('Employee ID'),
        help_text=_('Company employee identification number')
    )
    
    department = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Department'),
        help_text=_('Department or team within the company')
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
        verbose_name=_('Manager'),
        help_text=_('Direct manager for this user')
    )
    
    # Dates
    hire_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Hire Date')
    )
    
    start_date = models.DateField(
        auto_now_add=True,
        verbose_name=_('Company Start Date'),
        help_text=_('Date when user joined this company in the system')
    )
    
    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('End Date'),
        help_text=_('Date when user left the company')
    )
    
    # Contact within company
    work_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Work Phone')
    )
    
    work_email = models.EmailField(
        blank=True,
        verbose_name=_('Work Email'),
        help_text=_('Company email address if different from user email')
    )

    is_authorized_rfq_sender = models.BooleanField(
        default=False,
        verbose_name=_('Authorized RFQ Sender'),
        help_text=_('Only authorized contacts can trigger quotation auto-generation from RFQ emails.')
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('Company Contact')
        verbose_name_plural = _('Company Contacts')
        unique_together = ['user', 'company']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['employee_id']),
        ]
        ordering = ['company__name', 'name', 'user__last_name', 'user__first_name']
    
    def __str__(self):
        return f"{self.get_contact_name()} - {self.company.name} ({self.get_role_display()})"

    def get_contact_name(self):
        if (self.name or '').strip():
            return self.name.strip()
        if self.user_id:
            return self.user.get_full_name_display()
        return '-'

    def get_contact_email(self):
        if self.work_email:
            return self.work_email
        if self.user_id:
            return self.user.email or ''
        return ''

    def get_contact_phone(self):
        if self.work_phone:
            return self.work_phone
        if self.user_id:
            return self.user.phone_number or ''
        return ''

    def save(self, *args, **kwargs):
        if not (self.name or '').strip() and self.user_id:
            self.name = self.user.get_full_name_display()
        if not self.work_email and self.user_id and self.user.email:
            self.work_email = self.user.email
        if not self.work_phone and self.user_id and self.user.phone_number:
            self.work_phone = self.user.phone_number
        super().save(*args, **kwargs)
    
    def can_manage_assets(self):
        """Check if user can manage assets for this company."""
        return self.role in [self.CompanyRole.ADMIN, self.CompanyRole.MANAGER]
    
    def can_view_company_data(self):
        """Check if user can view company data."""
        return self.status == self.UserStatus.ACTIVE
    
    def get_accessible_locations(self):
        """Return locations this user can access."""
        if self.role == self.CompanyRole.ADMIN:
            return self.company.locations.all()
        elif self.division:
            return self.division.locations.all()
        elif self.location:
            return Location.objects.filter(pk=self.location.pk)
        return Location.objects.none()
    
    def get_full_display_name(self):
        """Return full display name with company context."""
        title = f" ({self.job_title})" if self.job_title else ""
        return f"{self.get_contact_name()}{title} - {self.company.name}"


class ImportRun(models.Model):
    """Tracks one CSV/Excel import execution for rollback and audit."""

    class RunStatus(models.TextChoices):
        COMPLETED = 'completed', _('Completed')
        FAILED = 'failed', _('Failed')
        ROLLED_BACK = 'rolled_back', _('Rolled Back')

    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='import_runs',
        verbose_name=_('User'),
    )
    module = models.CharField(max_length=50, verbose_name=_('Module'))
    import_type = models.CharField(max_length=50, verbose_name=_('Import Type'))
    status = models.CharField(
        max_length=20,
        choices=RunStatus.choices,
        default=RunStatus.COMPLETED,
        verbose_name=_('Status'),
    )
    total_rows = models.PositiveIntegerField(default=0, verbose_name=_('Total Rows'))
    created_count = models.PositiveIntegerField(default=0, verbose_name=_('Created Count'))
    updated_count = models.PositiveIntegerField(default=0, verbose_name=_('Updated Count'))
    skipped_count = models.PositiveIntegerField(default=0, verbose_name=_('Skipped Count'))
    error_count = models.PositiveIntegerField(default=0, verbose_name=_('Error Count'))
    notes = models.TextField(blank=True, verbose_name=_('Notes'))
    rolled_back_at = models.DateTimeField(null=True, blank=True, verbose_name=_('Rolled Back At'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Import Run')
        verbose_name_plural = _('Import Runs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'module', 'import_type', 'created_at']),
            models.Index(fields=['module', 'import_type', 'created_at']),
            models.Index(fields=['status', 'rolled_back_at']),
        ]

    def __str__(self):
        return f"{self.module}:{self.import_type} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def can_rollback(self):
        return self.status == self.RunStatus.COMPLETED and self.rolled_back_at is None


class ImportRunChange(models.Model):
    """Stores per-object snapshots for rollback."""

    class ChangeOperation(models.TextChoices):
        CREATE = 'create', _('Create')
        UPDATE = 'update', _('Update')

    run = models.ForeignKey(
        ImportRun,
        on_delete=models.CASCADE,
        related_name='changes',
        verbose_name=_('Import Run'),
    )
    sequence = models.PositiveIntegerField(default=0, verbose_name=_('Sequence'))
    row_number = models.PositiveIntegerField(null=True, blank=True, verbose_name=_('Source Row'))
    app_label = models.CharField(max_length=100, verbose_name=_('App Label'))
    model_name = models.CharField(max_length=100, verbose_name=_('Model Name'))
    object_pk = models.CharField(max_length=64, verbose_name=_('Object PK'))
    operation = models.CharField(
        max_length=20,
        choices=ChangeOperation.choices,
        verbose_name=_('Operation'),
    )
    before_data = models.JSONField(default=dict, blank=True, verbose_name=_('Before Data'))
    after_data = models.JSONField(default=dict, blank=True, verbose_name=_('After Data'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Import Run Change')
        verbose_name_plural = _('Import Run Changes')
        ordering = ['sequence', 'id']
        indexes = [
            models.Index(fields=['run', 'sequence']),
            models.Index(fields=['app_label', 'model_name', 'object_pk']),
        ]

    def __str__(self):
        return f"{self.run_id}:{self.operation} {self.app_label}.{self.model_name}#{self.object_pk}"
