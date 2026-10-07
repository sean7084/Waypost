import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


class WeeklyOrderBatch(models.Model):
    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Uploaded'
        PROCESSING = 'processing', 'Processing'
        PROCESSED = 'processed', 'Processed'
        FAILED = 'failed', 'Failed'

    batch_id = models.CharField(max_length=30, unique=True)
    sharepoint_file = models.FileField(upload_to='invoices/sharepoint/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='processed_weekly_batches',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    total_rows = models.PositiveIntegerField(default=0)
    created_rows = models.PositiveIntegerField(default=0)
    failed_row_number = models.PositiveIntegerField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['batch_id']),
            models.Index(fields=['status']),
            models.Index(fields=['uploaded_at']),
        ]

    def __str__(self):
        return self.batch_id

    def save(self, *args, **kwargs):
        if not self.batch_id:
            self.batch_id = self.generate_batch_id()
        super().save(*args, **kwargs)

    @classmethod
    def generate_batch_id(cls):
        today = datetime.date.today()
        prefix = f"WB-{today.strftime('%Y%m%d')}-"
        last_batch = cls.objects.filter(batch_id__startswith=prefix).order_by('-batch_id').first()

        if last_batch:
            try:
                last_seq = int(last_batch.batch_id.split('-')[-1])
            except (ValueError, IndexError):
                last_seq = 0
        else:
            last_seq = 0

        return f"{prefix}{last_seq + 1:03d}"


class InvoiceInfo(models.Model):
    invoice_number = models.CharField(max_length=20, unique=True)
    invoice_date = models.DateField()
    payment_due_date = models.DateField(null=True, blank=True)
    bill_to = models.CharField(max_length=255, blank=True)
    kering_group_po_number = models.CharField(max_length=100)
    internal_order = models.CharField(max_length=100)
    sap_cost_center = models.CharField(max_length=100)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('13.00'))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    weekly_batch = models.ForeignKey(
        WeeklyOrderBatch,
        on_delete=models.PROTECT,
        related_name='invoice_infos',
    )
    quotation = models.ForeignKey(
        'quotations.Quotation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_infos',
    )
    delivery_order = models.ForeignKey(
        'deliveries.DeliveryOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_infos',
    )
    source_row_number = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['invoice_date', 'source_row_number', 'id']
        indexes = [
            models.Index(fields=['invoice_number']),
            models.Index(fields=['invoice_date']),
            models.Index(fields=['bill_to']),
            models.Index(fields=['kering_group_po_number']),
            models.Index(fields=['internal_order']),
            models.Index(fields=['sap_cost_center']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['kering_group_po_number', 'internal_order', 'sap_cost_center'],
                name='uniq_invoice_business_keys',
            )
        ]

    def __str__(self):
        return self.invoice_number

    @classmethod
    def next_invoice_number(cls, invoice_date):
        prefix = invoice_date.strftime('%y%m%d')
        last_invoice = cls.objects.filter(invoice_number__startswith=prefix).order_by('-invoice_number').first()

        if last_invoice:
            try:
                suffix = int(last_invoice.invoice_number[-2:]) + 1
            except ValueError:
                suffix = 1
        else:
            suffix = 1

        return f"{prefix}{suffix:02d}"

    def save(self, *args, **kwargs):
        if not self.invoice_date:
            self.invoice_date = timezone.localdate()

        if self.delivery_order_id and not self.quotation_id:
            self.quotation = self.delivery_order.quotation

        if self.delivery_order_id and self.quotation_id:
            if self.delivery_order.quotation_id != self.quotation_id:
                raise ValidationError('Quotation must match the linked delivery order quotation.')

        if not self.invoice_number:
            with transaction.atomic():
                candidate = self.next_invoice_number(self.invoice_date)
                while InvoiceInfo.objects.filter(invoice_number=candidate).exists():
                    suffix = int(candidate[-2:]) + 1
                    candidate = f"{self.invoice_date.strftime('%y%m%d')}{suffix:02d}"
                self.invoice_number = candidate

        super().save(*args, **kwargs)

    def recalculate_from_sources(self):
        from .services import recalculate_invoice_from_delivery

        recalculate_invoice_from_delivery(self)


class InvoiceInfoItem(models.Model):
    invoice_info = models.ForeignKey(
        InvoiceInfo,
        on_delete=models.CASCADE,
        related_name='items',
    )
    line_number = models.PositiveIntegerField(default=1)
    description = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1.00'))
    total_price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('13.00'))

    class Meta:
        ordering = ['line_number', 'id']
        indexes = [
            models.Index(fields=['invoice_info', 'line_number']),
        ]

    def __str__(self):
        return f"{self.invoice_info.invoice_number} - {self.description}"


class EmailDispatch(models.Model):
    class DispatchStatus(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        CLIENT_CONFIRMED = 'client_confirmed', 'Client Confirmed'
        ESKER_FORWARDED = 'esker_forwarded', 'Esker Forwarded'
        FAILED = 'failed', 'Failed'

    quotation = models.ForeignKey(
        'quotations.Quotation',
        on_delete=models.CASCADE,
        related_name='email_dispatches',
    )
    source_email_message = models.ForeignKey(
        'accounts.ReceivedEmailMessage',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='email_dispatches',
    )
    invoice_info = models.ForeignKey(
        InvoiceInfo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='email_dispatches',
    )
    delivery_order = models.ForeignKey(
        'deliveries.DeliveryOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='email_dispatches',
    )

    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)

    sent_to = models.TextField(help_text='Comma separated recipients')
    cc = models.TextField(blank=True, help_text='Comma separated CC recipients')
    bcc = models.TextField(blank=True, help_text='Comma separated BCC recipients')

    attachments = models.JSONField(default=list, blank=True)
    # Copied verbatim from a ReceivedEmailMessage.message_id (accounts/rfq_ai.py,
    # invoices/views.py) and emitted as the In-Reply-To header
    # (invoices/services.py), so it has to accept everything that field accepts -
    # see the note on accounts.ReceivedEmailMessage.message_id. Its sibling
    # reply_references was already a TextField for the same reason.
    reply_message_id = models.TextField(blank=True)
    reply_references = models.TextField(blank=True)

    status = models.CharField(max_length=30, choices=DispatchStatus.choices, default=DispatchStatus.DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True)
    esker_sent = models.BooleanField(default=False)
    esker_sent_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_email_dispatches',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['quotation', 'status']),
            models.Index(fields=['sent_at']),
            models.Index(fields=['esker_sent']),
            models.Index(fields=['source_email_message', 'status']),
        ]

    def __str__(self):
        return f"{self.quotation.quotation_number} - {self.subject}"


class WorkflowStatusAudit(models.Model):
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=50)
    reference = models.CharField(max_length=120, blank=True)
    from_status = models.CharField(max_length=50, blank=True)
    to_status = models.CharField(max_length=50)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='workflow_status_audits',
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id']),
            models.Index(fields=['changed_at']),
        ]

    def __str__(self):
        return f"{self.entity_type}:{self.reference or self.entity_id} {self.from_status} -> {self.to_status}"
