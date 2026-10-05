"""
Models for Waypost Quotations App.
Quotation management with line items for the quotation-to-invoice workflow.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator
from decimal import Decimal
import datetime

from accounts.models import ReceivedEmailMessage
from companies.models import Company
from products.models import ProductPrice, ServiceItem
from quotations.template_registry import (
    DEFAULT_QUOTATION_TEMPLATE,
    get_quotation_template_choices,
    get_quotation_template_definition,
)


DEFAULT_QUOTATION_REMARK_POLICY_LINE = '2. 全部产品保修遵循相关官方政策。'
DEFAULT_QUOTATION_REMARK_FALLBACK_TARGET = '客户'


def normalize_quotation_remark_text(value):
    return ' '.join(str(value or '').split()).strip()


def build_quotation_remark_target(user_brand, user_name):
    return ' '.join(
        part for part in [
            normalize_quotation_remark_text(user_brand),
            normalize_quotation_remark_text(user_name),
        ]
        if part
    ).strip()


class Quotation(models.Model):
    """
    Quotation model for customer quotes.
    Auto-generates quotation number in format QT-YYYYMMDD-###.
    """

    class QuotationStatus(models.TextChoices):
        DRAFT = 'draft', _('Draft')
        SENT = 'sent', _('Sent')
        CONFIRMED = 'confirmed', _('Confirmed')
        EXPIRED = 'expired', _('Expired')
        CANCELLED = 'cancelled', _('Cancelled')

    # Quotation number (auto-generated)
    quotation_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('Quotation Number')
    )

    # Customer link
    customer = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name='quotations',
        verbose_name=_('Customer')
    )
    source_email_message = models.ForeignKey(
        ReceivedEmailMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='linked_quotations',
        verbose_name=_('Source RFQ Email')
    )

    # Date fields
    quotation_date = models.DateField(
        default=datetime.date.today,
        verbose_name=_('Quotation Date')
    )
    valid_until = models.DateField(
        verbose_name=_('Valid Until')
    )

    # Contact info snapshot at quotation creation
    attn = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Attention')
    )
    tel = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_('Telephone')
    )
    attn_email = models.EmailField(
        blank=True,
        verbose_name=_('Attention Email')
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=QuotationStatus.choices,
        default=QuotationStatus.SENT,
        verbose_name=_('Status')
    )

    # Totals (calculated from line items)
    total_without_tax = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name=_('Total Without Tax')
    )
    total_with_tax = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name=_('Total With Tax')
    )
    total_tax = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name=_('Total Tax')
    )

    # External remarks and internal notes
    pdf_template = models.CharField(
        max_length=20,
        choices=get_quotation_template_choices(),
        default=DEFAULT_QUOTATION_TEMPLATE,
        verbose_name=_('PDF Template')
    )
    remarks = models.TextField(
        blank=True,
        verbose_name=_('Remarks')
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes')
    )
    requires_confirmation = models.BooleanField(
        default=False,
        verbose_name=_('Requires Confirmation')
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Quotation')
        verbose_name_plural = _('Quotations')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.quotation_number} - {self.customer.name}"

    def save(self, *args, **kwargs):
        # Auto-generate quotation number if not set
        if not self.quotation_number:
            self.quotation_number = self.generate_quotation_number()
        super().save(*args, **kwargs)

    def generate_quotation_number(self):
        """Generate unique quotation number: QT-YYYYMMDD-###"""
        today = datetime.date.today()
        prefix = f"QT-{today.strftime('%Y%m%d')}-"

        # Get the last quotation number for today
        last_quotation = Quotation.objects.filter(
            quotation_number__startswith=prefix
        ).order_by('-quotation_number').first()

        if last_quotation:
            # Extract the sequence number and increment
            try:
                last_seq = int(last_quotation.quotation_number.split('-')[-1])
                seq = last_seq + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1

        return f"{prefix}{seq:03d}"

    def recalculate_totals(self):
        """Recalculate totals from line items."""
        items = self.items.all()
        total_ex = Decimal('0.00')
        total_tax = Decimal('0.00')

        for item in items:
            total_ex += item.line_total_without_tax
            total_tax += item.tax_amount

        self.total_without_tax = total_ex
        self.total_tax = total_tax
        self.total_with_tax = total_ex + total_tax
        self.save(update_fields=['total_without_tax', 'total_tax', 'total_with_tax', 'updated_at'])

    def get_customer_info(self):
        """Return customer contact and delivery info from company-linked records."""
        primary_contact = self.customer.primary_contact_company_user
        first_location = self.customer.locations.order_by('name').first()
        fallback_name = primary_contact.get_contact_name() if primary_contact else ''
        fallback_phone = primary_contact.get_contact_phone() if primary_contact else ''
        fallback_email = primary_contact.get_contact_email() if primary_contact else ''
        return {
            'attn': self.attn or fallback_name,
            'tel': self.tel or fallback_phone,
            'email': self.attn_email or fallback_email or self.customer.email,
            'delivery_address': first_location.get_full_address() if first_location else self.customer.get_full_address(),
            'delivery_city': first_location.city if first_location else self.customer.city,
        }

    @property
    def is_expired(self):
        """Check if quotation is past validity date."""
        return datetime.date.today() > self.valid_until

    def _get_customer_name_for_remarks(self):
        customer_name = ''
        if self.customer_id:
            customer_name = getattr(self.customer, 'name', '')
        else:
            customer = self._state.fields_cache.get('customer')
            if customer is not None:
                customer_name = getattr(customer, 'name', '')
        return normalize_quotation_remark_text(customer_name)

    def build_default_remarks(self, ordered_items=None):
        """Build the default external remarks text used on quotations."""
        items = list(ordered_items) if ordered_items is not None else list(self.items.all().order_by('id'))
        unique_remark_targets = []
        seen_remark_targets = set()

        for item in items:
            remark_target = build_quotation_remark_target(
                getattr(item, 'user_brand', ''),
                getattr(item, 'user_name', ''),
            )
            if not remark_target:
                continue

            key = remark_target.lower()
            if key in seen_remark_targets:
                continue

            seen_remark_targets.add(key)
            unique_remark_targets.append(remark_target)

        remark_targets_text = ', '.join(unique_remark_targets)
        if not remark_targets_text:
            remark_targets_text = self._get_customer_name_for_remarks() or DEFAULT_QUOTATION_REMARK_FALLBACK_TARGET

        return f'1. 此报价为 {remark_targets_text} 采购项目。\n{DEFAULT_QUOTATION_REMARK_POLICY_LINE}'

    def get_effective_remarks(self, ordered_items=None):
        """Return saved external remarks or the default generated text."""
        custom_remarks = str(self.remarks or '').strip()
        if custom_remarks:
            return custom_remarks
        return self.build_default_remarks(ordered_items=ordered_items)

    def get_template_definition(self):
        return get_quotation_template_definition(self.pdf_template)


class QuotationItem(models.Model):
    """
    Line item for quotation.
    Links to ProductPrice and stores snapshot of product info at time of quote.
    """

    quotation = models.ForeignKey(
        Quotation,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name=_('Quotation')
    )

    # Link to product price
    product_price = models.ForeignKey(
        ProductPrice,
        on_delete=models.PROTECT,
        related_name='quotation_items',
        verbose_name=_('Product Price')
    )
    service_item = models.ForeignKey(
        ServiceItem,
        on_delete=models.SET_NULL,
        related_name='quotation_items',
        null=True,
        blank=True,
        verbose_name=_('Service Item')
    )

    # Snapshot of product info at time of quote
    brand_name = models.CharField(
        max_length=255,
        verbose_name=_('Brand')
    )
    product_description = models.TextField(
        verbose_name=_('Product Description')
    )
    model_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_('Model Number')
    )
    unit = models.CharField(
        max_length=50,
        verbose_name=_('Unit')
    )

    # Pricing (from ProductPrice at time of quote)
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name=_('Quantity')
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name=_('Unit Price')
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('13.00'),
        verbose_name=_('Tax Rate (%)')
    )

    # Calculated fields
    price_without_tax = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name=_('Price Without Tax')
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name=_('Tax Amount')
    )
    line_total_without_tax = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name=_('Line Total')
    )
    line_total_with_tax = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name=_('Line Total With Tax')
    )

    # Additional fields for quotation template
    user_brand = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('User Brand')
    )
    user_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('User Name')
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Quotation Item')
        verbose_name_plural = _('Quotation Items')
        ordering = ['id']

    def __str__(self):
        return f"{self.quotation.quotation_number} - {self.brand_name} {self.product_description[:50]}"

    def save(self, *args, **kwargs):
        # Copy product info from ProductPrice if not set
        if self.product_price:
            self.service_item = self.product_price.service_item if self.product_price.service_item_id else None
            if not self.brand_name:
                self.brand_name = self.product_price.display_brand_name
                self.product_description = self.product_price.display_description
                self.model_number = self.product_price.display_model_number
                self.unit = self.product_price.display_unit
                self.unit_price = self.product_price.price_without_tax
                self.tax_rate = self.product_price.tax_rate

        # Calculate line totals
        self.price_without_tax = self.unit_price * self.quantity
        self.tax_amount = self.price_without_tax * (self.tax_rate / 100)
        self.line_total_without_tax = self.price_without_tax
        self.line_total_with_tax = self.price_without_tax + self.tax_amount

        super().save(*args, **kwargs)

        # Update quotation totals after saving
        if self.quotation:
            self.quotation.recalculate_totals()


class QuotationAttachment(models.Model):
    """
    Attachments for quotation (invoice PDF, OFD, XML zip, email confirmation).
    """

    class AttachmentType(models.TextChoices):
        INVOICE_PDF = 'invoice_pdf', _('Invoice PDF')
        INVOICE_OFD = 'invoice_ofd', _('Invoice OFD')
        INVOICE_XML = 'invoice_xml', _('Invoice XML (Zipped)')
        EMAIL_CONFIRMATION = 'email_confirmation', _('Email Confirmation Screenshot')

    quotation = models.ForeignKey(
        Quotation,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name=_('Quotation')
    )
    attachment_type = models.CharField(
        max_length=30,
        choices=AttachmentType.choices,
        verbose_name=_('Attachment Type')
    )
    file = models.FileField(
        upload_to='quotations/attachments/%Y/%m/',
        verbose_name=_('File')
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes')
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Uploaded At'))

    class Meta:
        verbose_name = _('Quotation Attachment')
        verbose_name_plural = _('Quotation Attachments')
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.quotation.quotation_number} - {self.get_attachment_type_display()}"
