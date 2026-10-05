"""
Models for Waypost - Assets App.
This module defines models for asset management including assets, categories,
brands, and asset history tracking.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import RegexValidator
from django.urls import reverse
from django.conf import settings
import uuid
import os


class AssetCategory(models.Model):
    """
    Asset categories for organizing different types of assets.
    """

    class ItemType(models.TextChoices):
        HARDWARE = 'hardware', _('Hardware')
        SERVICE = 'service', _('Service')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        verbose_name=_('Category Name')
    )
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('Category Code'),
        help_text=_('Unique code for the category (e.g., LAPTOP, PHONE)')
    )
    description = models.TextField(
        blank=True,
        verbose_name=_('Description')
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subcategories',
        verbose_name=_('Parent Category')
    )
    default_asset_model = models.ForeignKey(
        'AssetModel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_for_categories',
        verbose_name=_('Default Asset Model'),
        help_text=_('Used when RFQ requests only specify a generic device type for this category.')
    )
    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.HARDWARE,
        verbose_name=_('Item Type'),
        help_text=_('Determines whether this category represents hardware or a service.')
    )
    
    # Category settings
    requires_serial_number = models.BooleanField(
        default=True,
        verbose_name=_('Requires Serial Number'),
        help_text=_('Whether assets in this category must have a serial number')
    )
    default_warranty_months = models.PositiveIntegerField(
        default=12,
        verbose_name=_('Default Warranty (Months)'),
        help_text=_('Default warranty period in months for this category')
    )
    depreciation_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=20.00,
        verbose_name=_('Depreciation Rate (%)'),
        help_text=_('Annual depreciation rate as percentage')
    )
    
    # Metadata
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('Asset Category')
        verbose_name_plural = _('Asset Categories')
        ordering = ['name']
    
    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk and self.default_asset_model_id and self.default_asset_model.category_id and self.default_asset_model.category_id != self.id:
            raise ValueError('Default asset model must belong to the selected category.')
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('assets:category_detail', kwargs={'pk': self.pk})


class AssetBrand(models.Model):
    """
    Asset brands/manufacturers.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_('Brand Name')
    )
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('Brand Code'),
        help_text=_('Short code for the brand (e.g., DELL, HP, APPLE)')
    )
    description = models.TextField(
        blank=True,
        verbose_name=_('Description')
    )
    website = models.URLField(
        blank=True,
        verbose_name=_('Website')
    )
    support_email = models.EmailField(
        blank=True,
        verbose_name=_('Support Email')
    )
    support_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Support Phone')
    )
    
    # Metadata
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('Asset Brand')
        verbose_name_plural = _('Asset Brands')
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        return reverse('assets:brand_detail', kwargs={'pk': self.pk})


class AssetModel(models.Model):
    """
    Asset models for specific products within brands.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand = models.ForeignKey(
        AssetBrand,
        on_delete=models.CASCADE,
        related_name='models',
        verbose_name=_('Brand')
    )
    category = models.ForeignKey(
        AssetCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_models',
        verbose_name=_('Category'),
        help_text=_('Default category used for filtering in asset create forms')
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_('Model Name')
    )
    model_number = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_('Model Number'),
        help_text=_('Official model number from manufacturer')
    )
    unit = models.CharField(
        max_length=50,
        default='PCS',
        verbose_name=_('Unit'),
        help_text=_('Default unit for this model (e.g., PCS, SET, KG)')
    )
    description = models.TextField(
        blank=True,
        verbose_name=_('Description')
    )
    
    # Technical specifications
    specifications = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Specifications'),
        help_text=_('Technical specifications as JSON')
    )
    
    # Metadata
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('Asset Model')
        verbose_name_plural = _('Asset Models')
        ordering = ['brand__name', 'name']
        unique_together = ['brand', 'model_number']
    
    def __str__(self):
        return f"{self.brand.name} {self.name}"

    def save(self, *args, **kwargs):
        # Treat empty model number as NULL so optional values do not violate uniqueness.
        if self.model_number == '':
            self.model_number = None
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('assets:model_detail', kwargs={'pk': self.pk})


def asset_photo_upload_path(instance, filename):
    """Generate upload path for asset photos."""
    return f'assets/photos/{instance.asset_number}/{filename}'


def asset_document_upload_path(instance, filename):
    """Generate upload path for asset documents."""
    return f'assets/documents/{instance.asset.asset_number}/{filename}'


class Asset(models.Model):
    """
    Main asset model representing individual assets.
    """
    
    # Asset status choices
    class AssetStatus(models.TextChoices):
        AVAILABLE = 'available', _('Available')
        ASSIGNED = 'assigned', _('Assigned')
        IN_USE = 'in_use', _('In Use')
        MAINTENANCE = 'maintenance', _('Under Maintenance')
        REPAIR = 'repair', _('Under Repair')
        RETIRED = 'retired', _('Retired')
        DISPOSED = 'disposed', _('Disposed')
        LOST = 'lost', _('Lost')
        STOLEN = 'stolen', _('Stolen')
    
    # Condition choices
    class AssetCondition(models.TextChoices):
        EXCELLENT = 'excellent', _('Excellent')
        GOOD = 'good', _('Good')
        FAIR = 'fair', _('Fair')
        POOR = 'poor', _('Poor')
        DAMAGED = 'damaged', _('Damaged')
    
    # Basic information
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset_number = models.CharField(
        max_length=100,
        unique=True,
        blank=True,  # Allow blank for auto-generation
        verbose_name=_('Asset Number'),
        help_text=_('Unique identifier for the asset (auto-generated if left blank)')
    )
    description = models.TextField(
        blank=True,
        verbose_name=_('Description')
    )
    
    # Classification
    category = models.ForeignKey(
        AssetCategory,
        on_delete=models.PROTECT,
        related_name='assets',
        verbose_name=_('Category')
    )
    brand = models.ForeignKey(
        AssetBrand,
        on_delete=models.PROTECT,
        related_name='assets',
        verbose_name=_('Brand')
    )
    model = models.ForeignKey(
        AssetModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name=_('Model')
    )
    
    # Technical details
    serial_number = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Serial Number')
    )
    barcode = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        verbose_name=_('Barcode'),
        help_text=_('Barcode for scanning')
    )
    
    # Ownership and location
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        related_name='assets',
        verbose_name=_('Company')
    )
    division = models.ForeignKey(
        'companies.Division',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name=_('Division')
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_assets',
        verbose_name=_('Assigned To'),
        help_text=_('User to whom this asset is assigned')
    )
    
    # Location can be either a company user or a specific location
    location = models.ForeignKey(
        'companies.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        verbose_name=_('Location'),
        help_text=_('Physical location where the asset is located')
    )

    location_zone = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Location Zone'),
        help_text=_('Exact warehouse zone for this asset')
    )

    location_rack = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Location Rack'),
        help_text=_('Exact warehouse rack for this asset')
    )

    location_shelf = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_('Location Shelf'),
        help_text=_('Exact warehouse shelf for this asset')
    )
    
    # Legacy field for compatibility - will be migrated to location
    current_location = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Current Location (Legacy)'),
        help_text=_('Physical location of the asset - deprecated, use Location field')
    )
    
    # Status and condition
    status = models.CharField(
        max_length=20,
        choices=AssetStatus.choices,
        default=AssetStatus.AVAILABLE,
        verbose_name=_('Status')
    )
    condition = models.CharField(
        max_length=20,
        choices=AssetCondition.choices,
        default=AssetCondition.GOOD,
        verbose_name=_('Condition')
    )
    
    # Financial information
    purchase_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Purchase Price')
    )
    current_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Current Value')
    )
    purchase_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Purchase Date')
    )

    # Source tracking
    source_quotation = models.ForeignKey(
        'quotations.Quotation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchased_assets',
        verbose_name=_('Source Quotation')
    )

    warranty_start_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Warranty Start Date')
    )
    warranty_end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Warranty End Date')
    )
    warranty_provider = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Warranty Provider')
    )
    
    # Photos and documents
    photo = models.ImageField(
        upload_to=asset_photo_upload_path,
        blank=True,
        null=True,
        verbose_name=_('Photo')
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes')
    )
    
    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_assets',
        verbose_name=_('Created By')
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    last_audit_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Last Audit Date')
    )
    
    class Meta:
        verbose_name = _('Asset')
        verbose_name_plural = _('Assets')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['asset_number']),
            models.Index(fields=['serial_number']),
            models.Index(fields=['barcode']),
            models.Index(fields=['status']),
            models.Index(fields=['company']),
        ]
    
    def save(self, *args, **kwargs):
        """Override save to auto-generate asset number if not provided."""
        # Store empty barcode as NULL so unique constraint allows multiple non-barcoded assets.
        if self.barcode == '':
            self.barcode = None
        if self.location_zone == '':
            self.location_zone = None
        if self.location_rack == '':
            self.location_rack = None
        if self.location_shelf == '':
            self.location_shelf = None
        if not self.asset_number:
            self.asset_number = self.generate_asset_number()
        super().save(*args, **kwargs)
    
    def generate_asset_number(self):
        """
        Generate a unique asset number based on company and category.
        Format: {COMPANY_CODE}-{CATEGORY_CODE}-{SEQUENCE}
        Example: ACME-LAPTOP-001
        """
        # Get company code (first 4 characters of company name, uppercase)
        company_code = self.company.name[:4].upper().replace(' ', '')
        
        # Get category code (first 3 characters of category code, uppercase)
        category_code = self.category.code[:3].upper()
        
        # Find the next sequence number for this company-category combination
        prefix = f"{company_code}-{category_code}-"
        
        # Get existing assets with the same prefix
        existing_assets = Asset.objects.filter(
            asset_number__startswith=prefix
        ).order_by('-asset_number')
        
        if existing_assets.exists():
            # Extract the sequence number from the last asset
            last_number = existing_assets.first().asset_number
            try:
                sequence = int(last_number.split('-')[-1]) + 1
            except (ValueError, IndexError):
                sequence = 1
        else:
            sequence = 1
        
        # Format sequence with leading zeros (3 digits)
        return f"{prefix}{sequence:03d}"
    
    def __str__(self):
        """Return asset number as string representation."""
        return self.asset_number

    @staticmethod
    def _format_slot_part(value, prefix):
        cleaned = (value or '').strip()
        if not cleaned:
            return ''
        upper_cleaned = cleaned.upper()
        upper_prefix = prefix.upper()
        if upper_cleaned.startswith(upper_prefix):
            return upper_cleaned
        return f"{upper_prefix}{upper_cleaned}"

    def has_serial_number(self):
        return bool((self.serial_number or '').strip())

    def get_brand_model_display(self):
        if self.brand and self.model:
            return f"{self.brand.name} {self.model.name}"
        if self.brand:
            return self.brand.name
        if self.model:
            return self.model.name
        return '-'

    def get_location_display_abbr(self):
        base = ''
        if self.location:
            base = (self.location.code or '').strip() or (self.location.name or '').strip()
        else:
            base = (self.current_location or '').strip()

        zone = self._format_slot_part(self.location_zone, 'Z')
        rack = self._format_slot_part(self.location_rack, 'R')
        shelf = self._format_slot_part(self.location_shelf, 'S')

        parts = [part for part in [zone, rack, shelf] if part]
        if base and parts:
            return f"{base}-{'-'.join(parts)}"
        if base:
            return base
        if parts:
            return '-'.join(parts)
        return '-'
    
    def get_absolute_url(self):
        return reverse('assets:detail', kwargs={'pk': self.pk})
    
    def is_under_warranty(self):
        """Check if asset is currently under warranty."""
        if not self.warranty_end_date:
            return False
        from django.utils import timezone
        return timezone.now().date() <= self.warranty_end_date
    
    def warranty_status(self):
        """Get warranty status description."""
        if not self.warranty_end_date:
            return _('No warranty information')
        
        from django.utils import timezone
        from datetime import timedelta
        
        today = timezone.now().date()
        if today > self.warranty_end_date:
            return _('Warranty expired')
        elif today + timedelta(days=30) >= self.warranty_end_date:
            return _('Warranty expiring soon')
        else:
            return _('Under warranty')
    
    def calculate_depreciation(self):
        """Calculate current depreciated value."""
        if not self.purchase_price or not self.purchase_date:
            return None
        
        from django.utils import timezone
        from datetime import date
        
        years_owned = (timezone.now().date() - self.purchase_date).days / 365.25
        depreciation_rate = self.category.depreciation_rate / 100
        
        depreciated_value = self.purchase_price * ((1 - depreciation_rate) ** years_owned)
        return max(depreciated_value, self.purchase_price * 0.1)  # Min 10% residual value
    
    def assign_to_user(self, user, location=None):
        """
        Assign asset to a specific user.
        """
        self.assigned_to = user
        if location:
            self.location = location
        self.status = self.AssetStatus.ASSIGNED
        self.save()
        
        # Create assignment record
        AssetAssignment.objects.create(
            asset=self,
            assigned_to=user,
            location=location,
            assignment_type=AssetAssignment.AssignmentType.USER,
            assigned_by=user,  # This should be the current user making the assignment
            notes=f"Assigned to {user.get_full_name_display()}"
        )
    
    def assign_to_location(self, location, assigned_by=None):
        """
        Assign asset to a specific location without a user.
        """
        self.assigned_to = None
        self.location = location
        self.status = self.AssetStatus.AVAILABLE
        self.save()
        
        # Create assignment record
        AssetAssignment.objects.create(
            asset=self,
            location=location,
            assignment_type=AssetAssignment.AssignmentType.LOCATION,
            assigned_by=assigned_by,
            notes=f"Assigned to location {location.name}"
        )
    
    def unassign(self, assigned_by=None):
        """
        Unassign asset from current user/location.
        """
        old_user = self.assigned_to
        old_location = self.location
        
        self.assigned_to = None
        self.status = self.AssetStatus.AVAILABLE
        self.save()
        
        # Create unassignment record
        notes = f"Unassigned from {old_user.get_full_name_display() if old_user else 'location'}"
        if old_location:
            notes += f" at {old_location.name}"
            
        AssetAssignment.objects.create(
            asset=self,
            assignment_type=AssetAssignment.AssignmentType.UNASSIGNED,
            assigned_by=assigned_by,
            notes=notes
        )
    
    def get_assignment_history(self):
        """
        Get assignment history for this asset.
        """
        return self.assignments.all().order_by('-assigned_date')
    
    def get_current_assignment_display(self):
        """
        Get human-readable current assignment status.
        """
        if self.assigned_to:
            location_info = f" at {self.location.name}" if self.location else ""
            return f"Assigned to {self.assigned_to.get_full_name_display()}{location_info}"
        elif self.location:
            return f"Located at {self.location.name}"
        else:
            return "Available"


class AssetAssignment(models.Model):
    """
    Track asset assignments to users or locations with detailed history.
    """
    
    # Assignment type choices
    class AssignmentType(models.TextChoices):
        USER = 'user', _('User Assignment')
        LOCATION = 'location', _('Location Assignment')
        UNASSIGNED = 'unassigned', _('Unassigned')
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='assignments',
        verbose_name=_('Asset')
    )
    
    # Assignment can be to a user or just a location
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='asset_assignments',
        verbose_name=_('Assigned To User')
    )
    
    location = models.ForeignKey(
        'companies.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_assignments',
        verbose_name=_('Location')
    )
    
    assignment_type = models.CharField(
        max_length=20,
        choices=AssignmentType.choices,
        default=AssignmentType.USER,
        verbose_name=_('Assignment Type')
    )
    
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='assignments_made',
        verbose_name=_('Assigned By')
    )
    
    # Assignment details
    assigned_date = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Assigned Date')
    )
    expected_return_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Expected Return Date')
    )
    returned_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Returned Date')
    )
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='returns_processed',
        verbose_name=_('Returned By')
    )
    
    # Assignment condition
    assignment_condition = models.CharField(
        max_length=20,
        choices=Asset.AssetCondition.choices,
        blank=True,
        verbose_name=_('Condition at Assignment')
    )
    return_condition = models.CharField(
        max_length=20,
        choices=Asset.AssetCondition.choices,
        blank=True,
        verbose_name=_('Condition at Return')
    )
    
    # Notes
    notes = models.TextField(
        blank=True,
        verbose_name=_('Assignment Notes')
    )
    return_notes = models.TextField(
        blank=True,
        verbose_name=_('Return Notes')
    )
    
    class Meta:
        verbose_name = _('Asset Assignment')
        verbose_name_plural = _('Asset Assignments')
        ordering = ['-assigned_date']
        indexes = [
            models.Index(fields=['asset', 'assigned_date']),
            models.Index(fields=['assigned_to', 'returned_date']),
            models.Index(fields=['location', 'assigned_date']),
        ]
    
    def __str__(self):
        if self.assignment_type == self.AssignmentType.USER and self.assigned_to:
            location_info = f" at {self.location.name}" if self.location else ""
            return f"{self.asset} -> {self.assigned_to.get_full_name_display()}{location_info}"
        elif self.assignment_type == self.AssignmentType.LOCATION and self.location:
            return f"{self.asset} -> {self.location.name}"
        else:
            return f"{self.asset} -> Unassigned"
    
    @property
    def is_active(self):
        """Check if assignment is currently active."""
        return self.returned_date is None
    
    @property
    def is_overdue(self):
        """Check if assignment is overdue."""
        if not self.expected_return_date or self.returned_date:
            return False
        from django.utils import timezone
        return timezone.now().date() > self.expected_return_date
    
    def get_assignment_display(self):
        """Get human-readable assignment description."""
        if self.assignment_type == self.AssignmentType.USER and self.assigned_to:
            location_info = f" at {self.location.name}" if self.location else ""
            return f"User: {self.assigned_to.get_full_name_display()}{location_info}"
        elif self.assignment_type == self.AssignmentType.LOCATION and self.location:
            return f"Location: {self.location.name}"
        else:
            return "Unassigned"


class AssetMaintenance(models.Model):
    """
    Track asset maintenance and repair activities.
    """
    
    class MaintenanceType(models.TextChoices):
        ROUTINE = 'routine', _('Routine Maintenance')
        REPAIR = 'repair', _('Repair')
        UPGRADE = 'upgrade', _('Upgrade')
        INSPECTION = 'inspection', _('Inspection')
        CALIBRATION = 'calibration', _('Calibration')
    
    class MaintenanceStatus(models.TextChoices):
        SCHEDULED = 'scheduled', _('Scheduled')
        IN_PROGRESS = 'in_progress', _('In Progress')
        COMPLETED = 'completed', _('Completed')
        CANCELLED = 'cancelled', _('Cancelled')
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='maintenance_records',
        verbose_name=_('Asset')
    )
    
    # Maintenance details
    maintenance_type = models.CharField(
        max_length=20,
        choices=MaintenanceType.choices,
        verbose_name=_('Maintenance Type')
    )
    status = models.CharField(
        max_length=20,
        choices=MaintenanceStatus.choices,
        default=MaintenanceStatus.SCHEDULED,
        verbose_name=_('Status')
    )
    title = models.CharField(
        max_length=255,
        verbose_name=_('Title')
    )
    description = models.TextField(
        verbose_name=_('Description')
    )
    
    # Scheduling
    scheduled_date = models.DateTimeField(
        verbose_name=_('Scheduled Date')
    )
    started_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Started Date')
    )
    completed_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Completed Date')
    )
    
    # Personnel
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='maintenance_assignments',
        verbose_name=_('Assigned To')
    )
    vendor = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Vendor/Service Provider')
    )
    
    # Cost
    estimated_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Estimated Cost')
    )
    actual_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Actual Cost')
    )
    
    # Results
    work_performed = models.TextField(
        blank=True,
        verbose_name=_('Work Performed')
    )
    parts_replaced = models.TextField(
        blank=True,
        verbose_name=_('Parts Replaced')
    )
    next_maintenance_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Next Maintenance Date')
    )
    
    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='maintenance_created',
        verbose_name=_('Created By')
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))
    
    class Meta:
        verbose_name = _('Asset Maintenance')
        verbose_name_plural = _('Asset Maintenance Records')
        ordering = ['-scheduled_date']
    
    def __str__(self):
        return f"{self.asset} - {self.title}"


class AssetActivityLog(models.Model):
    """
    Append-only activity trail for asset lifecycle operations.

    Replaces the former generic ``audit.AuditLog`` for asset-scoped events
    (create / update / delete / assign / return / export / import). Bulk
    export/import events have ``asset`` left null because they are not tied to a
    single asset row.
    """

    class Operation(models.TextChoices):
        CREATE = 'create', _('Create')
        UPDATE = 'update', _('Update')
        DELETE = 'delete', _('Delete')
        ASSIGN = 'assign', _('Assign Asset')
        RETURN = 'return', _('Return Asset')
        EXPORT = 'export', _('Data Export')
        IMPORT = 'import', _('Data Import')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        'assets.Asset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
        verbose_name=_('Asset'),
        help_text=_('Null for bulk export/import events.'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_activity_logs',
        verbose_name=_('User'),
    )
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_activity_logs',
        verbose_name=_('Company'),
    )
    operation = models.CharField(
        max_length=20,
        choices=Operation.choices,
        verbose_name=_('Operation'),
    )
    description = models.TextField(verbose_name=_('Description'))
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_('Metadata'))
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_('IP Address'))
    user_agent = models.TextField(blank=True, verbose_name=_('User Agent'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Asset Activity Log')
        verbose_name_plural = _('Asset Activity Logs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['asset', '-created_at']),
            models.Index(fields=['operation', '-created_at']),
            models.Index(fields=['company', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        target = self.asset.asset_number if self.asset else _('bulk')
        return f"{self.get_operation_display()} - {target} - {self.created_at:%Y-%m-%d %H:%M}"


class AssetFieldChange(models.Model):
    """
    Field-level old -> new diff attached to an AssetActivityLog.

    Replaces the former ``audit.ChangeLog`` so asset change detail lives with the
    asset domain.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity_log = models.ForeignKey(
        AssetActivityLog,
        on_delete=models.CASCADE,
        related_name='field_changes',
        verbose_name=_('Activity Log'),
    )
    field_name = models.CharField(max_length=100, verbose_name=_('Field Name'))
    old_value = models.TextField(blank=True, verbose_name=_('Old Value'))
    new_value = models.TextField(blank=True, verbose_name=_('New Value'))
    field_type = models.CharField(max_length=50, blank=True, verbose_name=_('Field Type'))

    class Meta:
        verbose_name = _('Asset Field Change')
        verbose_name_plural = _('Asset Field Changes')
        ordering = ['field_name']

    def __str__(self):
        return f"{self.field_name}: {self.old_value} -> {self.new_value}"
