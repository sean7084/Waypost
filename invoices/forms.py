from django import forms
from django.utils.translation import gettext_lazy as _

from deliveries.models import DeliveryOrder
from quotations.models import Quotation

from .models import EmailDispatch, InvoiceInfo


class SharepointImportForm(forms.Form):
    sharepoint_file = forms.FileField(
        label=_('Sharepoint Excel File'),
        help_text=_('Upload an .xlsx file exported from Sharepoint.'),
    )

    def clean_sharepoint_file(self):
        file_obj = self.cleaned_data.get('sharepoint_file')
        if not file_obj:
            return file_obj

        if not file_obj.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Invalid file type. Please upload a .xlsx file.')

        return file_obj


class InvoiceInfoForm(forms.ModelForm):
    class Meta:
        model = InvoiceInfo
        fields = [
            'invoice_date',
            'payment_due_date',
            'bill_to',
            'kering_group_po_number',
            'internal_order',
            'sap_cost_center',
            'quotation',
            'delivery_order',
        ]
        widgets = {
            'invoice_date': forms.DateInput(attrs={'type': 'date'}),
            'payment_due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quotation'].queryset = Quotation.objects.order_by('-created_at')
        self.fields['delivery_order'].queryset = DeliveryOrder.objects.select_related('quotation').order_by('-created_at')

        for name, field in self.fields.items():
            css_class = 'form-control'
            if isinstance(field.widget, forms.Select):
                css_class = 'form-select'
            field.widget.attrs['class'] = css_class


class EmailDispatchForm(forms.ModelForm):
    class Meta:
        model = EmailDispatch
        fields = [
            'quotation',
            'delivery_order',
            'invoice_info',
            'sent_to',
            'cc',
            'bcc',
            'subject',
            'body',
        ]
        widgets = {
            'body': forms.Textarea(attrs={'rows': 8}),
        }

    def __init__(self, *args, **kwargs):
        quotation = kwargs.pop('quotation', None)
        super().__init__(*args, **kwargs)

        self.fields['quotation'].queryset = Quotation.objects.order_by('-created_at')
        self.fields['delivery_order'].queryset = DeliveryOrder.objects.select_related('quotation').order_by('-created_at')
        self.fields['invoice_info'].queryset = InvoiceInfo.objects.select_related('quotation', 'delivery_order').order_by('-invoice_date')

        if quotation is not None:
            self.fields['quotation'].initial = quotation
            self.fields['quotation'].queryset = Quotation.objects.filter(pk=quotation.pk)
            self.fields['delivery_order'].queryset = DeliveryOrder.objects.filter(quotation=quotation).order_by('-created_at')
            self.fields['invoice_info'].queryset = InvoiceInfo.objects.filter(quotation=quotation).order_by('-invoice_date')
            source_email_message = quotation.source_email_message

            primary_contact = quotation.customer.primary_contact_company_user
            if quotation.attn_email:
                self.fields['sent_to'].initial = quotation.attn_email
            elif source_email_message and source_email_message.sender:
                self.fields['sent_to'].initial = source_email_message.sender
            elif primary_contact and primary_contact.get_contact_email():
                self.fields['sent_to'].initial = primary_contact.get_contact_email()
            elif quotation.customer.email:
                self.fields['sent_to'].initial = quotation.customer.email

            if source_email_message and source_email_message.subject:
                if source_email_message.subject.lower().startswith('re:'):
                    self.fields['subject'].initial = source_email_message.subject
                else:
                    self.fields['subject'].initial = f"Re: {source_email_message.subject}"
                reply_body = quotation.email_dispatches.filter(source_email_message=source_email_message, status=EmailDispatch.DispatchStatus.DRAFT).order_by('-updated_at').first()
                if reply_body and reply_body.body:
                    self.fields['body'].initial = reply_body.body
            else:
                self.fields['subject'].initial = f"Document Package - {quotation.quotation_number}"

        for name, field in self.fields.items():
            css_class = 'form-control'
            if isinstance(field.widget, forms.Select):
                css_class = 'form-select'
            field.widget.attrs['class'] = css_class

        for field_name in ['sent_to', 'cc', 'bcc', 'subject']:
            self.fields[field_name].widget.attrs['class'] = 'form-control email-line-input'

    def _parse_recipients(self, value):
        return [item.strip() for item in (value or '').split(',') if item.strip()]

    def clean_sent_to(self):
        value = self.cleaned_data.get('sent_to', '')
        recipients = self._parse_recipients(value)
        if not recipients:
            raise forms.ValidationError('At least one recipient is required in To.')
        return ', '.join(recipients)

    def clean_cc(self):
        value = self.cleaned_data.get('cc', '')
        return ', '.join(self._parse_recipients(value))

    def clean_bcc(self):
        value = self.cleaned_data.get('bcc', '')
        return ', '.join(self._parse_recipients(value))

    def clean(self):
        cleaned_data = super().clean()
        quotation = cleaned_data.get('quotation')
        delivery_order = cleaned_data.get('delivery_order')
        invoice_info = cleaned_data.get('invoice_info')

        if delivery_order and quotation and delivery_order.quotation_id != quotation.pk:
            self.add_error('delivery_order', 'Selected delivery order does not belong to the selected quotation.')

        if invoice_info and quotation and invoice_info.quotation_id and invoice_info.quotation_id != quotation.pk:
            self.add_error('invoice_info', 'Selected invoice info does not belong to the selected quotation.')

        return cleaned_data
