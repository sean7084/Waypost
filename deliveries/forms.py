"""Forms for delivery order workflow."""

from django import forms
from django.utils.translation import gettext_lazy as _

from assets.models import Asset

from .models import DeliveryOrder
from .services import get_dispatch_asset_queryset, split_quotation_items_for_delivery


class DeliveryOrderForm(forms.ModelForm):
    """Create/update delivery order and choose dispatch assets."""

    selected_assets = forms.ModelMultipleChoiceField(
        queryset=Asset.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_('Assets to Dispatch'),
    )

    class Meta:
        model = DeliveryOrder
        fields = [
            'delivery_date',
            'receiver_name',
            'receiver_phone',
            'delivery_address',
            'delivery_method',
            'remarks',
        ]

    def __init__(self, *args, quotation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.quotation = quotation

        if quotation is not None:
            self.fields['selected_assets'].queryset = get_dispatch_asset_queryset(quotation)
            self.fields['selected_assets'].label_from_instance = (
                lambda obj: f"{obj.asset_number} - {obj.serial_number} - "
                f"{obj.brand.name if obj.brand else '-'} - {obj.description or '-'}"
            )

    def clean(self):
        cleaned_data = super().clean()
        if self.quotation is None:
            return cleaned_data

        hardware_items, service_items = split_quotation_items_for_delivery(self.quotation)
        selected_assets = cleaned_data.get('selected_assets')

        if hardware_items and not selected_assets:
            self.add_error('selected_assets', 'Select assets for the hardware items in this quotation.')

        if not hardware_items and not service_items:
            raise forms.ValidationError('This quotation has no deliverable items.')

        return cleaned_data


class SignedCopyUploadForm(forms.ModelForm):
    """Upload signed delivery copy."""

    class Meta:
        model = DeliveryOrder
        fields = ['signed_file']

    def clean_signed_file(self):
        file_obj = self.cleaned_data.get('signed_file')
        if not file_obj:
            return file_obj

        allowed_exts = {'.pdf', '.jpg', '.jpeg', '.png'}
        filename = file_obj.name.lower()
        if not any(filename.endswith(ext) for ext in allowed_exts):
            raise forms.ValidationError('Invalid file type. Allowed: PDF, JPG, JPEG, PNG.')

        return file_obj
