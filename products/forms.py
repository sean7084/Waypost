"""
Forms for Products app.
"""
from decimal import Decimal, ROUND_HALF_UP

from django import forms
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.core.exceptions import ValidationError

from assets.models import AssetCategory, AssetModel

from .models import ProductPrice, ServiceItem


class ProductPriceForm(forms.ModelForm):
    """Form for creating and editing ProductPrice."""

    brand_name = forms.CharField(
        required=False,
        label=_('Brand'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_price_model_ids = ProductPrice.objects.filter(is_current=True)
        if self.instance.pk:
            current_price_model_ids = current_price_model_ids.exclude(pk=self.instance.pk)

        self.fields['model'].queryset = AssetModel.objects.filter(is_active=True).exclude(
            category__item_type=AssetCategory.ItemType.SERVICE,
        ).exclude(
            pk__in=current_price_model_ids.values_list('model_id', flat=True)
        ).select_related('brand').order_by('brand__name', 'name')
        self.fields['model'].empty_label = _('Select model')
        self.fields['model'].widget.attrs.update({'class': 'form-select'})
        self.fields['unit'].widget.attrs.update({'readonly': 'readonly'})

        if not self.instance.pk and not self.is_bound:
            self.fields['valid_from'].initial = timezone.localdate()

        selected_model = self.instance.model if getattr(self.instance, 'model_id', None) else None
        if selected_model:
            self.fields['brand_name'].initial = selected_model.brand.name
            self.fields['unit'].initial = selected_model.unit
        elif self.data.get('model'):
            try:
                selected_model = self.fields['model'].queryset.get(pk=self.data['model'])
            except (AssetModel.DoesNotExist, ValueError, TypeError):
                selected_model = None

            if selected_model:
                self.fields['brand_name'].initial = selected_model.brand.name
                self.fields['unit'].initial = selected_model.unit

    class Meta:
        model = ProductPrice
        fields = ['model', 'unit', 'price_without_tax', 'tax_rate', 'price_with_tax', 'is_current', 'valid_from', 'valid_until', 'notes']
        widgets = {
            'model': forms.Select(attrs={'class': 'form-select'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'price_without_tax': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'tax_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'price_with_tax': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
            'is_current': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'valid_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'valid_until': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        selected_model = cleaned_data.get('model')
        price_without_tax = cleaned_data.get('price_without_tax')
        tax_rate = cleaned_data.get('tax_rate')

        if selected_model:
            cleaned_data['unit'] = selected_model.unit or 'PCS'

        if price_without_tax and tax_rate:
            # Auto-calculate price_with_tax
            calculated_price = price_without_tax * (Decimal('1') + (tax_rate / Decimal('100')))
            cleaned_data['price_with_tax'] = calculated_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.model_id:
            instance.brand = instance.model.brand
            instance.unit = instance.model.unit or instance.unit or 'PCS'
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ServicePriceForm(forms.Form):
    """Create a service item and its price in one step."""

    service_group = forms.CharField(
        required=False,
        label=_('Service Group'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'list': 'service-group-options'}),
        help_text=_('Optional. Use this to group related services in the catalog.'),
    )
    service_name = forms.CharField(
        label=_('Service Name'),
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    description = forms.CharField(
        required=False,
        label=_('Description'),
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
    )
    unit = forms.CharField(
        label=_('Unit'),
        initial='JOB',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    price_without_tax = forms.DecimalField(
        label=_('Price Without Tax'),
        min_value=Decimal('0.00'),
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    tax_rate = forms.DecimalField(
        label=_('Tax Rate (%)'),
        min_value=Decimal('0.00'),
        decimal_places=2,
        max_digits=5,
        initial=Decimal('13.00'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    price_with_tax = forms.DecimalField(
        label=_('Price With Tax'),
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
    )
    is_current = forms.BooleanField(
        required=False,
        initial=True,
        label=_('Current Price'),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    valid_from = forms.DateField(
        required=False,
        label=_('Valid From'),
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    valid_until = forms.DateField(
        required=False,
        label=_('Valid Until'),
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    notes = forms.CharField(
        required=False,
        label=_('Notes'),
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
    )

    def __init__(self, *args, **kwargs):
        self.product_price = kwargs.pop('product_price', None)
        self.service_item = getattr(self.product_price, 'service_item', None) if self.product_price else None
        super().__init__(*args, **kwargs)

        if not self.is_bound and self.service_item:
            self.fields['service_group'].initial = self.service_item.service_group
            self.fields['service_name'].initial = self.service_item.name
            self.fields['description'].initial = self.service_item.description
            self.fields['unit'].initial = self.service_item.unit
            self.fields['price_without_tax'].initial = self.product_price.price_without_tax
            self.fields['tax_rate'].initial = self.product_price.tax_rate
            self.fields['price_with_tax'].initial = self.product_price.price_with_tax
            self.fields['is_current'].initial = self.product_price.is_current
            self.fields['valid_from'].initial = self.product_price.valid_from
            self.fields['valid_until'].initial = self.product_price.valid_until
            self.fields['notes'].initial = self.product_price.notes
        elif not self.is_bound:
            self.fields['valid_from'].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        price_without_tax = cleaned_data.get('price_without_tax')
        tax_rate = cleaned_data.get('tax_rate')
        service_group = (cleaned_data.get('service_group') or '').strip()
        service_name = (cleaned_data.get('service_name') or '').strip()
        unit = (cleaned_data.get('unit') or '').strip()

        cleaned_data['service_group'] = service_group
        if service_name:
            cleaned_data['service_name'] = service_name
        if unit:
            cleaned_data['unit'] = unit
        else:
            cleaned_data['unit'] = 'JOB'

        if price_without_tax is not None and tax_rate is not None:
            calculated_price = price_without_tax * (Decimal('1') + (tax_rate / Decimal('100')))
            cleaned_data['price_with_tax'] = calculated_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if self.service_item and cleaned_data.get('is_current'):
            existing_current = ProductPrice.objects.filter(
                service_item=self.service_item,
                is_current=True,
            )
            if self.product_price:
                existing_current = existing_current.exclude(pk=self.product_price.pk)
            if existing_current.exists():
                raise ValidationError(_('Another current price already exists for this service item.'))

        return cleaned_data

    def save(self, *, product_price=None, service_item=None):
        service_item = service_item or self.service_item or ServiceItem()
        service_item.service_group = self.cleaned_data.get('service_group', '')
        service_item.name = self.cleaned_data['service_name']
        service_item.description = self.cleaned_data.get('description', '')
        service_item.unit = self.cleaned_data['unit']
        service_item.is_active = True
        service_item.save()

        product_price = product_price or self.product_price or ProductPrice()
        product_price.brand = None
        product_price.model = None
        product_price.service_item = service_item
        product_price.unit = service_item.unit
        product_price.price_without_tax = self.cleaned_data['price_without_tax']
        product_price.price_with_tax = self.cleaned_data.get('price_with_tax')
        product_price.tax_rate = self.cleaned_data['tax_rate']
        product_price.is_current = self.cleaned_data.get('is_current', True)
        product_price.valid_from = self.cleaned_data.get('valid_from')
        product_price.valid_until = self.cleaned_data.get('valid_until')
        product_price.notes = self.cleaned_data.get('notes', '')
        product_price.full_clean()
        product_price.save()
        return product_price
