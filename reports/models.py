"""
Reports models for Waypost.
This module defines models for generating, storing, and managing
various types of reports and analytics for the asset management system.
"""
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
import uuid
import json


class ReportTemplate(models.Model):
    """
    Report template definitions for standardized reporting.
    Templates define the structure, filters, and format of reports
    that can be generated repeatedly with different parameters.
    """
    # Report types
    class ReportType(models.TextChoices):
        ASSET_INVENTORY = 'asset_inventory', _('Asset Inventory')
        ASSET_VALUATION = 'asset_valuation', _('Asset Valuation')
        ASSIGNMENT_HISTORY = 'assignment_history', _('Assignment History')
        AUDIT_SUMMARY = 'audit_summary', _('Audit Summary')
        DEPRECIATION = 'depreciation', _('Depreciation Report')
        WARRANTY_EXPIRY = 'warranty_expiry', _('Warranty Expiry')
        UTILIZATION = 'utilization', _('Asset Utilization')
        COST_ANALYSIS = 'cost_analysis', _('Cost Analysis')
        COMPLIANCE = 'compliance', _('Compliance Report')
        CUSTOM = 'custom', _('Custom Report')

    # Output formats
    class OutputFormat(models.TextChoices):
        PDF = 'pdf', _('PDF')
        EXCEL = 'excel', _('Excel')
        CSV = 'csv', _('CSV')
        JSON = 'json', _('JSON')
        HTML = 'html', _('HTML')

    # Unique identifier
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Template identification
    name = models.CharField(
        max_length=200,
        verbose_name=_('Template Name'),
        help_text=_('Descriptive name for the report template')
    )

    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('Template Code'),
        help_text=_('Unique code for programmatic reference')
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Detailed description of what this report contains')
    )

    # Report configuration
    report_type = models.CharField(
        max_length=20,
        choices=ReportType.choices,
        verbose_name=_('Report Type')
    )

    output_format = models.CharField(
        max_length=10,
        choices=OutputFormat.choices,
        default=OutputFormat.PDF,
        verbose_name=_('Default Output Format')
    )

    # Template definition (stored as JSON)
    template_definition = models.JSONField(
        default=dict,
        verbose_name=_('Template Definition'),
        help_text=_('JSON configuration defining report structure, filters, and formatting')
    )

    # Default filters and parameters
    default_filters = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Default Filters'),
        help_text=_('Default filter values for this template')
    )

    # Access control
    is_public = models.BooleanField(
        default=False,
        verbose_name=_('Is Public'),
        help_text=_('Whether this template is available to all users')
    )

    allowed_roles = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_('Allowed Roles'),
        help_text=_('List of user roles that can use this template')
    )

    # Template settings
    is_active = models.BooleanField(
        default=True,
        verbose_name=_('Is Active'),
        help_text=_('Whether this template is available for use')
    )

    requires_approval = models.BooleanField(
        default=False,
        verbose_name=_('Requires Approval'),
        help_text=_('Whether reports from this template require approval before access')
    )

    # Scheduling options
    can_be_scheduled = models.BooleanField(
        default=True,
        verbose_name=_('Can Be Scheduled'),
        help_text=_('Whether this report can be scheduled for automatic generation')
    )

    # Metadata
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_report_templates',
        verbose_name=_('Created By')
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Report Template')
        verbose_name_plural = _('Report Templates')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_report_type_display()})"

    def get_template_definition(self):
        """Get the template definition as a Python object."""
        return self.template_definition

    def set_template_definition(self, definition):
        """Set the template definition from a Python object."""
        self.template_definition = definition

    def is_accessible_by_user(self, user):
        """Check if a user can access this template."""
        if self.is_public:
            return True
        
        if not self.allowed_roles:
            return True
            
        return user.role in self.allowed_roles


class GeneratedReport(models.Model):
    """
    Generated report instances.
    Stores information about reports that have been generated,
    including their parameters, status, and file locations.
    """
    # Report status
    class ReportStatus(models.TextChoices):
        PENDING = 'pending', _('Pending')
        GENERATING = 'generating', _('Generating')
        COMPLETED = 'completed', _('Completed')
        FAILED = 'failed', _('Failed')
        CANCELLED = 'cancelled', _('Cancelled')

    # Generation methods
    class GenerationMethod(models.TextChoices):
        MANUAL = 'manual', _('Manual')
        SCHEDULED = 'scheduled', _('Scheduled')
        API = 'api', _('API')
        BATCH = 'batch', _('Batch')

    # Unique identifier
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Report identification
    report_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('Report Number'),
        help_text=_('Unique identifier for this generated report')
    )

    name = models.CharField(
        max_length=200,
        verbose_name=_('Report Name'),
        help_text=_('Descriptive name for this report instance')
    )

    # Template and configuration
    template = models.ForeignKey(
        ReportTemplate,
        on_delete=models.PROTECT,
        related_name='generated_reports',
        verbose_name=_('Report Template')
    )

    # Generation parameters
    filters_applied = models.JSONField(
        default=dict,
        verbose_name=_('Filters Applied'),
        help_text=_('Filters and parameters used to generate this report')
    )

    output_format = models.CharField(
        max_length=10,
        choices=ReportTemplate.OutputFormat.choices,
        verbose_name=_('Output Format')
    )

    # Generation details
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.PENDING,
        verbose_name=_('Status')
    )

    generation_method = models.CharField(
        max_length=20,
        choices=GenerationMethod.choices,
        default=GenerationMethod.MANUAL,
        verbose_name=_('Generation Method')
    )

    # Timing
    requested_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Requested At')
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Started At')
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Completed At')
    )

    # File information
    file_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('File Path'),
        help_text=_('Path to the generated report file')
    )

    file_size = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('File Size'),
        help_text=_('Size of the generated file in bytes')
    )

    # Report statistics
    record_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('Record Count'),
        help_text=_('Number of records included in the report')
    )

    # User and organization context
    requested_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='requested_reports',
        verbose_name=_('Requested By')
    )

    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        related_name='reports',
        verbose_name=_('Company')
    )

    # Access control
    is_public = models.BooleanField(
        default=False,
        verbose_name=_('Is Public'),
        help_text=_('Whether this report is accessible to all users in the company')
    )

    shared_with = models.ManyToManyField(
        'accounts.User',
        blank=True,
        related_name='accessible_reports',
        verbose_name=_('Shared With'),
        help_text=_('Users who have access to this report')
    )

    # Approval workflow
    requires_approval = models.BooleanField(
        default=False,
        verbose_name=_('Requires Approval')
    )

    approved = models.BooleanField(
        default=False,
        verbose_name=_('Approved')
    )

    approved_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_reports',
        verbose_name=_('Approved By')
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Approved At')
    )

    # Error handling
    error_message = models.TextField(
        blank=True,
        verbose_name=_('Error Message'),
        help_text=_('Error message if report generation failed')
    )

    # Retention
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Expires At'),
        help_text=_('When this report will be automatically deleted')
    )

    # Notes
    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes'),
        help_text=_('Additional notes about this report')
    )

    class Meta:
        verbose_name = _('Generated Report')
        verbose_name_plural = _('Generated Reports')
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['company', '-requested_at']),
            models.Index(fields=['requested_by', '-requested_at']),
            models.Index(fields=['status', '-requested_at']),
            models.Index(fields=['template', '-requested_at']),
        ]

    def __str__(self):
        return f"{self.report_number} - {self.name}"

    def get_generation_duration(self):
        """Get the duration of report generation."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None

    def is_expired(self):
        """Check if this report has expired."""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def can_be_accessed_by(self, user):
        """Check if a user can access this report."""
        if self.requested_by == user:
            return True
        
        if self.is_public and user.has_company_access(self.company):
            return True
            
        if self.shared_with.filter(id=user.id).exists():
            return True
            
        return False

    def get_file_size_display(self):
        """Get file size in human-readable format."""
        if not self.file_size:
            return "Unknown"
        
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"


class ReportSchedule(models.Model):
    """
    Scheduled report generation.
    Defines recurring schedules for automatic report generation
    and distribution.
    """
    # Schedule frequency
    class Frequency(models.TextChoices):
        DAILY = 'daily', _('Daily')
        WEEKLY = 'weekly', _('Weekly')
        MONTHLY = 'monthly', _('Monthly')
        QUARTERLY = 'quarterly', _('Quarterly')
        YEARLY = 'yearly', _('Yearly')

    # Schedule status
    class ScheduleStatus(models.TextChoices):
        ACTIVE = 'active', _('Active')
        PAUSED = 'paused', _('Paused')
        EXPIRED = 'expired', _('Expired')
        CANCELLED = 'cancelled', _('Cancelled')

    # Unique identifier
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Schedule identification
    name = models.CharField(
        max_length=200,
        verbose_name=_('Schedule Name'),
        help_text=_('Descriptive name for this schedule')
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Description of what this schedule generates')
    )

    # Template and configuration
    template = models.ForeignKey(
        ReportTemplate,
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name=_('Report Template')
    )

    # Schedule configuration
    frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        verbose_name=_('Frequency')
    )

    # Schedule timing
    start_date = models.DateField(
        verbose_name=_('Start Date'),
        help_text=_('When the schedule should start')
    )

    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('End Date'),
        help_text=_('When the schedule should end (optional)')
    )

    next_run = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Next Run'),
        help_text=_('When the next report should be generated')
    )

    last_run = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Last Run'),
        help_text=_('When the last report was generated')
    )

    # Schedule parameters
    filters = models.JSONField(
        default=dict,
        verbose_name=_('Filters'),
        help_text=_('Default filters to apply to scheduled reports')
    )

    output_format = models.CharField(
        max_length=10,
        choices=ReportTemplate.OutputFormat.choices,
        verbose_name=_('Output Format')
    )

    # Distribution settings
    email_recipients = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_('Email Recipients'),
        help_text=_('Email addresses to send generated reports to')
    )

    email_subject = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Email Subject'),
        help_text=_('Subject line for scheduled report emails')
    )

    email_body = models.TextField(
        blank=True,
        verbose_name=_('Email Body'),
        help_text=_('Body text for scheduled report emails')
    )

    # Status and control
    status = models.CharField(
        max_length=20,
        choices=ScheduleStatus.choices,
        default=ScheduleStatus.ACTIVE,
        verbose_name=_('Status')
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=_('Is Active'),
        help_text=_('Whether this schedule is currently active')
    )

    # Organization context
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        related_name='report_schedules',
        verbose_name=_('Company')
    )

    # Error handling
    consecutive_failures = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Consecutive Failures'),
        help_text=_('Number of consecutive failed generation attempts')
    )

    max_failures = models.PositiveIntegerField(
        default=3,
        verbose_name=_('Max Failures'),
        help_text=_('Maximum failures before schedule is paused')
    )

    last_error = models.TextField(
        blank=True,
        verbose_name=_('Last Error'),
        help_text=_('Last error message from failed generation')
    )

    # Metadata
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_schedules',
        verbose_name=_('Created By')
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Report Schedule')
        verbose_name_plural = _('Report Schedules')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    def calculate_next_run(self):
        """Calculate the next run time based on frequency."""
        from datetime import timedelta
        import calendar
        
        if not self.last_run:
            base_time = timezone.now()
        else:
            base_time = self.last_run
            
        if self.frequency == self.Frequency.DAILY:
            self.next_run = base_time + timedelta(days=1)
        elif self.frequency == self.Frequency.WEEKLY:
            self.next_run = base_time + timedelta(weeks=1)
        elif self.frequency == self.Frequency.MONTHLY:
            if base_time.month == 12:
                self.next_run = base_time.replace(year=base_time.year + 1, month=1)
            else:
                self.next_run = base_time.replace(month=base_time.month + 1)
        elif self.frequency == self.Frequency.QUARTERLY:
            quarter_months = [1, 4, 7, 10]
            current_quarter = (base_time.month - 1) // 3
            next_quarter = (current_quarter + 1) % 4
            if next_quarter == 0:
                self.next_run = base_time.replace(year=base_time.year + 1, month=1)
            else:
                self.next_run = base_time.replace(month=quarter_months[next_quarter])
        elif self.frequency == self.Frequency.YEARLY:
            self.next_run = base_time.replace(year=base_time.year + 1)
            
        self.save(update_fields=['next_run'])

    def should_run(self):
        """Check if this schedule should run now."""
        if not self.is_active or self.status != self.ScheduleStatus.ACTIVE:
            return False
            
        if self.end_date and self.end_date < timezone.localdate():
            return False
            
        if not self.next_run:
            return False
            
        return timezone.now() >= self.next_run

    def record_failure(self, error_message):
        """Record a failed generation attempt."""
        self.consecutive_failures += 1
        self.last_error = error_message
        
        if self.consecutive_failures >= self.max_failures:
            self.status = self.ScheduleStatus.PAUSED
            
        self.save(update_fields=['consecutive_failures', 'last_error', 'status'])

    def record_success(self):
        """Record a successful generation."""
        self.consecutive_failures = 0
        self.last_error = ''
        self.last_run = timezone.now()
        self.calculate_next_run()
        self.save(update_fields=['consecutive_failures', 'last_error', 'last_run'])


class ReportShare(models.Model):
    """
    Report sharing and access control.
    Tracks who has access to specific reports and when.
    """
    # Share types
    class ShareType(models.TextChoices):
        VIEW = 'view', _('View Only')
        DOWNLOAD = 'download', _('View and Download')
        FULL = 'full', _('Full Access')

    # Unique identifier
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Shared report
    report = models.ForeignKey(
        GeneratedReport,
        on_delete=models.CASCADE,
        related_name='shares',
        verbose_name=_('Report')
    )

    # Share recipient
    shared_with = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='report_shares',
        verbose_name=_('Shared With')
    )

    # Share configuration
    share_type = models.CharField(
        max_length=20,
        choices=ShareType.choices,
        default=ShareType.VIEW,
        verbose_name=_('Share Type')
    )

    # Access control
    can_reshare = models.BooleanField(
        default=False,
        verbose_name=_('Can Reshare'),
        help_text=_('Whether the recipient can share this report with others')
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Expires At'),
        help_text=_('When this share expires (optional)')
    )

    # Tracking
    shared_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='reports_shared_by_user',
        verbose_name=_('Shared By')
    )

    shared_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Shared At')
    )

    accessed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('First Accessed At')
    )

    access_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Access Count'),
        help_text=_('Number of times this share has been accessed')
    )

    # Notes
    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes'),
        help_text=_('Notes about this share')
    )

    class Meta:
        verbose_name = _('Report Share')
        verbose_name_plural = _('Report Shares')
        ordering = ['-shared_at']
        unique_together = [['report', 'shared_with']]

    def __str__(self):
        return f"{self.report.report_number} shared with {self.shared_with.username}"

    def is_expired(self):
        """Check if this share has expired."""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def record_access(self):
        """Record an access to this shared report."""
        
        if not self.accessed_at:
            self.accessed_at = timezone.now()
        
        self.access_count += 1
        self.save(update_fields=['accessed_at', 'access_count'])
