"""
Forms for Quotations app.
"""
from django import forms
from django.core.validators import MinValueValidator
from django.utils.translation import gettext_lazy as _
from decimal import Decimal
import datetime

from companies.models import Company
from products.models import ProductPrice
from .models import Quotation, QuotationItem, QuotationAttachment
from .template_registry import DEFAULT_QUOTATION_TEMPLATE


class QuotationForm(forms.ModelForm):
    """Form for creating and editing Quotation."""

    class Meta:
        model = Quotation
        fields = ['customer', 'quotation_date', 'valid_until', 'attn', 'tel', 'attn_email', 'pdf_template', 'status', 'remarks', 'notes']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'quotation_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'valid_until': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'attn': forms.TextInput(attrs={'class': 'form-control'}),
            'tel': forms.TextInput(attrs={'class': 'form-control'}),
            'attn_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pdf_template': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = Company.objects.filter(status=Company.CompanyStatus.ACTIVE).order_by('name')
        # Set default validity to 30 days from today
        if not self.instance.pk:
            customer = None
            if getattr(self.instance, 'customer_id', None):
                customer = self.instance.customer
            elif self.initial.get('customer'):
                customer = Company.objects.filter(pk=self.initial['customer']).only('default_quotation_template').first()

            default_template = customer.default_quotation_template if customer else DEFAULT_QUOTATION_TEMPLATE
            self.initial.setdefault('pdf_template', default_template)
            self.initial.setdefault('remarks', self.instance.build_default_remarks(ordered_items=[]))
            self.fields['valid_until'].initial = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()

    def clean(self):
        cleaned_data = super().clean()
        customer = self.cleaned_data.get('customer')
        if customer:
            contact = customer.primary_contact_company_user
            if contact:
                if not cleaned_data.get('attn'):
                    cleaned_data['attn'] = contact.get_contact_name()
                if not cleaned_data.get('tel'):
                    cleaned_data['tel'] = contact.get_contact_phone()
                if not cleaned_data.get('attn_email'):
                    cleaned_data['attn_email'] = contact.get_contact_email()
        return cleaned_data


class QuotationItemForm(forms.ModelForm):
    """Form for creating and editing QuotationItem."""

    class Meta:
        model = QuotationItem
        fields = ['product_price', 'quantity', 'user_brand', 'user_name']
        widgets = {
            'product_price': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'user_brand': forms.TextInput(attrs={'class': 'form-control'}),
            'user_name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')
        if quantity and quantity < 1:
            raise forms.ValidationError('Quantity must be at least 1.')
        return quantity


class QuotationItemFormSet(forms.BaseInlineFormSet):
    """FormSet for QuotationItem inline in Quotation form."""

    def clean(self):
        if any(self.errors):
            return
        # At least one item required
        items = [f for f in self.forms if not f.cleaned_data.get('DELETE', False)]
        if not items:
            raise forms.ValidationError('At least one product is required.')


class QuotationAttachmentForm(forms.ModelForm):
    """Form for uploading QuotationAttachment."""

    class Meta:
        model = QuotationAttachment
        fields = ['attachment_type', 'file', 'notes']
        widgets = {
            'attachment_type': forms.Select(attrs={'class': 'form-select'}),
            'file': forms.FileInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class QuotationExcelImportForm(forms.Form):
    """Status choice for the Excel import. Files arrive via request.FILES.getlist()."""

    status = forms.ChoiceField(
        label=_('Import status'),
        initial=Quotation.QuotationStatus.DRAFT,
        choices=[
            (value, label)
            for value, label in Quotation.QuotationStatus.choices
            if value in (Quotation.QuotationStatus.DRAFT, Quotation.QuotationStatus.SENT)
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
