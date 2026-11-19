"""Forms for the Hongwen staff admin pages (PR5).

The admin surface manages the same rows the customer API reads: ``Customer``
companies, ``HongwenProfile`` customer accounts (permission bits + token version)
and knowledge-base source uploads. Source strings are English; zh_CN
translations live in locale/zh_CN/LC_MESSAGES/django.po (the brand name 泓问 is
kept as a proper noun in the translations).
"""
import os

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from hongwen.models import Customer, HongwenProfile

User = get_user_model()

# Must stay in sync with hongwen.services.kb.ingest._iter_files / the loader.
KB_SOURCE_EXTENSIONS = ('.pdf', '.docx', '.doc', '.txt')

KB_CONFLICT_MODES = (
    ('keep_both', _('Keep both documents')),
    ('overwrite', _('Overwrite the same-name document')),
)


class CustomerForm(forms.ModelForm):
    """Create / edit a Hongwen customer company."""

    class Meta:
        model = Customer
        fields = ['name', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter customer name'),
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Optional notes about this customer'),
            }),
        }


class HongwenUserCreateForm(forms.Form):
    """Create a customer account: ``accounts.User`` + ``HongwenProfile``.

    Waypost customers sign in through WeChat (PR2), so the password is optional
    here - unlike the standalone helpdesk, which required an email-shaped
    username for its self-service password reset. ``username`` doubles as the
    profile's client-facing ``external_id``.
    """

    username = forms.CharField(
        label=_('Username / External ID'),
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
        help_text=_('Stable client-facing id; WeChat customers use wx_<openid>.'),
    )
    password = forms.CharField(
        label=_('Password (optional)'),
        required=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
    )
    customer = forms.ModelChoiceField(
        label=_('Customer'),
        queryset=Customer.objects.all(),
        required=False,
        empty_label=_('No customer (global)'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    nickname = forms.CharField(
        label=_('Nickname'), required=False, max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    mobile = forms.CharField(
        label=_('Mobile'), required=False, max_length=32,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    can_chat = forms.BooleanField(
        label=_('Can chat'), required=False, initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    can_upload = forms.BooleanField(
        label=_('Can upload'), required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    can_manage_customers = forms.BooleanField(
        label=_('Can manage customers'), required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    can_manage_users = forms.BooleanField(
        label=_('Can manage users'), required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if not username:
            raise forms.ValidationError(_('Username is required.'))
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError(_('This username is already taken.'))
        if HongwenProfile.objects.filter(external_id=username).exists():
            raise forms.ValidationError(_('This external ID is already in use.'))
        return username

    def save(self) -> HongwenProfile:
        data = self.cleaned_data
        user = User.objects.create_user(username=data['username'], password=data['password'] or None)
        if not data['password']:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        return HongwenProfile.objects.create(
            user=user,
            external_id=data['username'],
            customer=data['customer'],
            nickname=data['nickname'] or None,
            mobile=data['mobile'] or None,
            can_chat=data['can_chat'],
            can_upload=data['can_upload'],
            can_manage_customers=data['can_manage_customers'],
            can_manage_users=data['can_manage_users'],
        )


class HongwenProfileForm(forms.ModelForm):
    """Edit a customer profile: company scoping, contact fields, permission bits.

    ``external_id`` and ``token_version`` are deliberately not editable here:
    the external id is the client-facing identity key, and invalidating tokens
    is an explicit action (the bump button on the user list).
    """

    class Meta:
        model = HongwenProfile
        fields = ['customer', 'nickname', 'mobile', 'can_upload', 'can_chat',
                  'can_manage_customers', 'can_manage_users']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'nickname': forms.TextInput(attrs={'class': 'form-control'}),
            'mobile': forms.TextInput(attrs={'class': 'form-control'}),
            'can_upload': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_chat': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_manage_customers': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_manage_users': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].empty_label = _('No customer (global)')
        self.fields['customer'].required = False


class KBUploadForm(forms.Form):
    """Knowledge-base source upload (customer scope + same-name conflict mode).

    The scope field is named ``customer_id`` to match the query-string / form
    key every other KB endpoint reads (see views._selected_customer_id).
    """

    customer_id = forms.ModelChoiceField(
        label=_('Customer'),
        queryset=Customer.objects.all(),
        required=False,
        empty_label=_('Global (shared across customers)'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    conflict_mode = forms.ChoiceField(
        label=_('When a document has the same name'),
        choices=KB_CONFLICT_MODES,
        initial='keep_both',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    files = forms.FileField(
        label=_('Source documents'),
        # Django 5.2's FileInput/ClearableFileInput refuse a `multiple` attr, so
        # the template renders the raw <input type="file" multiple> itself; this
        # field only validates what request.FILES.getlist('files') delivered.
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': ','.join(KB_SOURCE_EXTENSIONS),
        }),
        help_text=_('Supported formats: PDF, DOCX, DOC, TXT.'),
    )

    def clean_files(self):
        # The widget allows multiple selection; validate every picked file, not
        # just the first one the bound FieldFile would expose.
        uploads = self.files.getlist('files')
        if not uploads:
            raise forms.ValidationError(_('Please choose at least one document.'))
        for upload in uploads:
            ext = os.path.splitext(upload.name or '')[1].lower()
            if ext not in KB_SOURCE_EXTENSIONS:
                raise forms.ValidationError(
                    _('Unsupported file type: %(name)s. Use PDF, DOCX, DOC or TXT.')
                    % {'name': upload.name},
                )
        return uploads