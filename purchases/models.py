"""
Models for Waypost Purchases App.
Tracks purchased assets from quotations and their receipt.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator
from decimal import Decimal
import uuid
import datetime

from products.models import ProductPrice
from assets.models import AssetBrand, AssetModel


class PurchaseOrder(models.Model):
    """Purchase order created from a confirmed quotation."""

    class Status(models.TextChoices):
        ORDERED = 'ordered', _('Ordered')
        RECEIVING = 'receiving', _('Receiving')
        COMPLETE = 'complete', _('Complete')

    quotation = models.OneToOneField(
        'quotations.Quotation',
        on_delete=models.PROTECT,
        related_name='purchase_order',
        verbose_name=_('Source Quotation')
    )
    po_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('PO Number')
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ORDERED,
        verbose_name=_('Status')
    )
    total_items = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Total Items')
    )
    total_received = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Total Received')
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Purchase Order')
        verbose_name_plural = _('Purchase Orders')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.po_number} ({self.quotation.quotation_number})"

    def save(self, *args, **kwargs):
        if not self.po_number:
            self.po_number = self.generate_po_number()
        super().save(*args, **kwargs)

    def generate_po_number(self):
        today = datetime.date.today()
        prefix = f"PO-{today.strftime('%Y%m%d')}-"

        last_po = PurchaseOrder.objects.filter(
            po_number__startswith=prefix
        ).order_by('-po_number').first()

        if last_po:
            try:
                last_seq = int(last_po.po_number.split('-')[-1])
                seq = last_seq + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1

        return f"{prefix}{seq:03d}"

    def recalculate_progress(self):
        ordered = sum(item.quantity_ordered for item in self.items.all())
        received = sum(item.quantity_received for item in self.items.all())
        self.total_items = ordered
        self.total_received = received

        if ordered and received >= ordered:
            self.status = self.Status.COMPLETE
        elif received > 0:
            self.status = self.Status.RECEIVING
        else:
            self.status = self.Status.ORDERED

        self.save(update_fields=['total_items', 'total_received', 'status', 'updated_at'])


class PurchaseOrderItem(models.Model):
    """Copied item snapshot from quotation into purchase order."""

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name=_('Purchase Order')
    )
    quotation_item = models.OneToOneField(
        'quotations.QuotationItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_order_item',
        verbose_name=_('Source Quotation Item')
    )
    product_price = models.ForeignKey(
        ProductPrice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_order_items',
        verbose_name=_('Product Price')
    )

    brand = models.ForeignKey(
        AssetBrand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_order_items',
        verbose_name=_('Brand')
    )
    model = models.ForeignKey(
        AssetModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_order_items',
        verbose_name=_('Model')
    )
    product_description = models.TextField(
        blank=True,
        verbose_name=_('Product Description')
    )
    unit = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_('Unit')
    )
    quantity_ordered = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name=_('Quantity Ordered')
    )
    quantity_received = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Quantity Received')
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name=_('Unit Price')
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))

    class Meta:
        verbose_name = _('Purchase Order Item')
        verbose_name_plural = _('Purchase Order Items')
        ordering = ['id']

    def __str__(self):
        return f"{self.purchase_order.po_number} - {self.product_description[:40]}"

    @property
    def quantity_remaining(self):
        return max(self.quantity_ordered - self.quantity_received, 0)


class PurchaseReceipt(models.Model):
    """
    Tracks receipt of purchased items from quotations.
    Links to assets created from a quotation.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        PARTIAL = 'partial', _('Partially Received')
        COMPLETE = 'complete', _('Completely Received')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Source quotation
    quotation = models.ForeignKey(
        'quotations.Quotation',
        on_delete=models.PROTECT,
        related_name='purchase_receipts',
        verbose_name=_('Source Quotation')
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='receipts',
        verbose_name=_('Purchase Order')
    )

    # Receipt details
    receipt_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('Receipt Number')
    )
    receipt_date = models.DateField(
        default=datetime.date.today,
        verbose_name=_('Receipt Date')
    )
    received_by = models.CharField(
        max_length=200,
        verbose_name=_('Received By')
    )
    location = models.ForeignKey(
        'companies.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='purchase_receipts',
        verbose_name=_('Receipt Location')
    )
    received_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Received Count')
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_('Status')
    )

    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes')
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Created At'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Updated At'))

    class Meta:
        verbose_name = _('Purchase Receipt')
        verbose_name_plural = _('Purchase Receipts')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.receipt_number} - {self.quotation.quotation_number}"

    def save(self, *args, **kwargs):
        if self.purchase_order and not self.quotation_id:
            self.quotation = self.purchase_order.quotation
        if not self.receipt_number:
            self.receipt_number = self.generate_receipt_number()
        super().save(*args, **kwargs)

    def generate_receipt_number(self):
        today = datetime.date.today()
        prefix = f"PR-{today.strftime('%Y%m%d')}-"

        last_receipt = PurchaseReceipt.objects.filter(
            receipt_number__startswith=prefix
        ).order_by('-receipt_number').first()

        if last_receipt:
            try:
                last_seq = int(last_receipt.receipt_number.split('-')[-1])
                seq = last_seq + 1
            except (ValueError, IndexError):
                seq = 1
        else:
            seq = 1

        return f"{prefix}{seq:03d}"
