"""
Forms for Waypost - Accounts App.
This module defines forms for authentication, user management, and profile handling.
Includes 2FA forms, multi-language support, and validation.
"""

import secrets
import string
import imaplib
import poplib
import smtplib
from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.contrib.auth import authenticate
from django_otp.plugins.otp_totp.models import TOTPDevice
from django.utils import timezone

from .models import AdminRole, ServiceCity, SystemSMTPSettings, User, UserMailboxSettings


def generate_random_password(length=12):
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(characters) for _ in range(length))


def build_admin_roles_field(required=False):
    return forms.ModelMultipleChoiceField(
        queryset=AdminRole.objects.none(),
        required=required,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'size': '5',
        }),
        label=_('Administrator Roles'),
        help_text=_('Select one or more administrator roles (hold Ctrl/Cmd for multiple).')
    )


LANGUAGE_PREFERENCE_CHOICES = [
    ('en-us', _('English (US)')),
    ('zh-cn', _('Simplified Chinese')),
]


class CustomLoginForm(AuthenticationForm):
    """
    Custom login form with enhanced styling and validation.
    """
    username = forms.CharField(
        max_length=254,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Username'),
            'autofocus': True,
        }),
        label=_('Username')
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': _('Password'),
        }),
        label=_('Password')
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove the default error messages from parent class
        self.error_messages = {
            'invalid_login': _('Please enter correct username and password.'),
            'inactive': _('This account is inactive.'),
        }


class UserRegistrationForm(UserCreationForm):
    """
    Form for creating new users with additional fields and validation.
    """
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': _('Email Address'),
        }),
        label=_('Email Address')
    )
    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('First Name'),
        }),
        label=_('First Name')
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Last Name'),
        }),
        label=_('Last Name')
    )
    chinese_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Chinese Name (optional)'),
        }),
        label=_('Chinese Name')
    )
    service_cities = forms.ModelMultipleChoiceField(
        queryset=ServiceCity.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'size': '5',
        }),
        label=_('Service Cities'),
        help_text=_('Cities/regions this field engineer covers (hold Ctrl/Cmd for multiple)')
    )
    roles = build_admin_roles_field(required=True)
    password1 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': _('Password'),
        }),
        label=_('Password')
    )
    password2 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': _('Confirm Password'),
        }),
        label=_('Confirm Password')
    )
    phone_number = forms.CharField(
        max_length=17,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Phone Number (optional)'),
        }),
        label=_('Phone Number'),
        help_text=_('Format: +1234567890')
    )
    language_preference = forms.ChoiceField(
        choices=LANGUAGE_PREFERENCE_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
        label=_('Language Preference'),
        initial='en-us'
    )
    
    # Password generation options
    use_random_password = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
            'id': 'use_random_password'
        }),
        label=_('Generate Random Password'),
        help_text=_('Generate a secure random password instead of manual entry')
    )
    
    must_change_password = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
            'id': 'must_change_password'
        }),
        label=_('Require Password Change on First Login'),
        help_text=_('User must change password when they first log in')
    )
    
    class Meta:
        model = User
        fields = (
            'username', 'email', 'first_name', 'last_name', 
            'password1', 'password2', 'roles', 'phone_number', 
            'language_preference', 'use_random_password', 'must_change_password', 'chinese_name', 'service_cities'
        )

    def is_edit_mode(self):
        return self.instance is not None and not self.instance._state.adding
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('password1', None)
        self.fields.pop('password2', None)
        self.fields.pop('use_random_password', None)

        # Add CSS classes to default fields
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': _('Username')
        })
        
        # Update labels
        self.fields['username'].label = _('Username')
        self.fields['roles'].queryset = AdminRole.objects.filter(is_active=True).order_by('name')
        self.fields['service_cities'].queryset = ServiceCity.objects.filter(is_active=True)

        if self.is_edit_mode():
            self.fields['roles'].initial = self.instance.roles.all()
    
    def clean(self):
        cleaned_data = forms.ModelForm.clean(self)
        if not self.is_edit_mode():
            self.generated_password = generate_random_password()
        return cleaned_data

    def clean_username(self):
        username = self.cleaned_data['username']
        queryset = User.objects.filter(username=username)
        if self.is_edit_mode():
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError(_('A user with that username already exists.'))
        return username
    
    def save(self, commit=True):
        user = forms.ModelForm.save(self, commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.chinese_name = self.cleaned_data.get('chinese_name', '')
        user.phone_number = self.cleaned_data['phone_number']
        user.language_preference = self.cleaned_data['language_preference']
        user.must_change_password = self.cleaned_data['must_change_password']
        if not self.is_edit_mode():
            generated_password = getattr(self, 'generated_password', generate_random_password())
            self.generated_password = generated_password
            user.set_password(generated_password)
        
        if commit:
            user.save()
            user.roles.set(self.cleaned_data['roles'])
            user.service_cities.set(self.cleaned_data.get('service_cities', []))
        else:
            user.set_admin_roles(role.code for role in self.cleaned_data['roles'])
        return user


class SuperuserUserForm(UserCreationForm):
    """
    Enhanced form for superusers to create/edit users with full access to all fields.
    """
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': _('Email Address'),
        }),
        label=_('Email Address')
    )
    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('First Name'),
        }),
        label=_('First Name')
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Last Name'),
        }),
        label=_('Last Name')
    )
    chinese_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Chinese Name (optional)'),
        }),
        label=_('Chinese Name'),
        help_text=_('Simplified-Chinese name; the mini program login looks engineers up by this.')
    )
    employee_id = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Employee ID (optional)'),
        }),
        label=_('Employee ID'),
        help_text=_('Unique employee identification number')
    )
    phone_number = forms.CharField(
        max_length=17,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Phone Number (optional)'),
        }),
        label=_('Phone Number'),
        help_text=_('Format: +1234567890')
    )
    department = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Department (optional)'),
        }),
        label=_('Department')
    )
    job_title = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Job Title (optional)'),
        }),
        label=_('Job Title')
    )
    
    # Company association fields
    company = forms.ModelChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
        label=_('Company'),
        help_text=_('Company this user belongs to')
    )
    division = forms.ModelChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
        label=_('Division'),
        help_text=_('Division this user belongs to')
    )
    manager = forms.ModelChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
        label=_('Manager'),
        help_text=_('Direct supervisor for this user')
    )
    
    # Admin role and permissions
    roles = build_admin_roles_field(required=False)
    password1 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': _('Password'),
        }),
        label=_('Password')
    )
    password2 = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': _('Confirm Password'),
        }),
        label=_('Confirm Password')
    )
    managed_company = forms.ModelChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
        label=_('Managed Company'),
        help_text=_('Company this IT administrator has access to')
    )
    managed_divisions = forms.ModelMultipleChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'size': '5',
        }),
        label=_('Managed Divisions'),
        help_text=_('Divisions this IT administrator has access to (hold Ctrl/Cmd for multiple)')
    )
    managed_locations = forms.ModelMultipleChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'size': '5',
        }),
        label=_('Managed Locations'),
        help_text=_('Locations this viewer has read-only access to (hold Ctrl/Cmd for multiple)')
    )
    service_cities = forms.ModelMultipleChoiceField(
        queryset=None,  # Will be set in __init__
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'size': '5',
        }),
        label=_('Service Cities'),
        help_text=_('Cities/regions this field engineer covers (hold Ctrl/Cmd for multiple)')
    )
    
    # Additional settings
    language_preference = forms.ChoiceField(
        choices=LANGUAGE_PREFERENCE_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-control',
        }),
        label=_('Language Preference'),
        initial='en-us'
    )
    timezone = forms.CharField(
        max_length=50,
        initial='UTC',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'UTC',
        }),
        label=_('Timezone')
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
        }),
        label=_('Active'),
        help_text=_('User can log in and access the system')
    )
    is_staff = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
        }),
        label=_('Staff Status'),
        help_text=_('User can access Django admin interface')
    )
    
    # Password generation options
    use_random_password = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
            'id': 'use_random_password'
        }),
        label=_('Generate Random Password'),
        help_text=_('Generate a secure random password instead of manual entry')
    )
    must_change_password = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input',
        }),
        label=_('Require Password Change on First Login'),
        help_text=_('User must change password when they first log in')
    )
    
    class Meta:
        model = User
        fields = (
            'username', 'email', 'first_name', 'last_name', 'chinese_name', 'employee_id',
            'phone_number', 'wechat_id', 'invite_code', 'fe_rating', 'fe_notes',
            'department', 'job_title', 'company', 'division', 'manager',
            'roles', 'managed_company', 'managed_divisions', 'managed_locations', 'service_cities',
            'language_preference', 'timezone', 'is_active', 'is_staff',
            'password1', 'password2', 'use_random_password', 'must_change_password'
        )

    def is_edit_mode(self):
        return self.instance is not None and not self.instance._state.adding
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('password1', None)
        self.fields.pop('password2', None)
        self.fields.pop('use_random_password', None)
        
        # Import here to avoid circular imports
        from companies.models import Company, Division, Location
        
        # Set up querysets for foreign key fields
        self.fields['company'].queryset = Company.objects.filter(status='active')
        self.fields['division'].queryset = Division.objects.filter(status='active')
        self.fields['manager'].queryset = User.objects.filter(is_active=True)
        self.fields['roles'].queryset = AdminRole.objects.filter(is_active=True).order_by('name')
        self.fields['managed_company'].queryset = Company.objects.filter(status='active')
        self.fields['managed_divisions'].queryset = Division.objects.filter(status='active')
        self.fields['managed_locations'].queryset = Location.objects.filter(status='active')
        self.fields['service_cities'].queryset = ServiceCity.objects.filter(is_active=True)

        if self.is_edit_mode():
            self.fields['roles'].initial = self.instance.roles.all()
        
        # Add CSS classes to default fields
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': _('Username')
        })
        
        # Update labels
        self.fields['username'].label = _('Username')

        if self.is_edit_mode():
            self.fields['roles'].initial = self.instance.roles.all()
            self.fields.pop('division', None)
            self.fields.pop('managed_divisions', None)
    
    def clean(self):
        cleaned_data = forms.ModelForm.clean(self)
        if not self.is_edit_mode():
            self.generated_password = generate_random_password()
        return cleaned_data

    def clean_username(self):
        username = self.cleaned_data['username']
        queryset = User.objects.filter(username=username)
        if self.is_edit_mode():
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError(_('A user with that username already exists.'))
        return username
    
    def save(self, commit=True):
        user = forms.ModelForm.save(self, commit=False)
        
        # Save all the additional fields
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.chinese_name = self.cleaned_data.get('chinese_name', '')
        user.employee_id = self.cleaned_data.get('employee_id')
        user.phone_number = self.cleaned_data.get('phone_number', '')
        user.department = self.cleaned_data.get('department', '')
        user.job_title = self.cleaned_data.get('job_title', '')
        user.company = self.cleaned_data.get('company')
        if 'division' in self.cleaned_data:
            user.division = self.cleaned_data.get('division')
        user.manager = self.cleaned_data.get('manager')
        user.managed_company = self.cleaned_data.get('managed_company')
        user.language_preference = self.cleaned_data.get('language_preference', 'en-us')
        user.timezone = self.cleaned_data.get('timezone', 'UTC')
        user.is_active = self.cleaned_data.get('is_active', True)
        user.is_staff = self.cleaned_data.get('is_staff', False)
        user.must_change_password = self.cleaned_data.get('must_change_password', True)
        if not self.is_edit_mode():
            generated_password = getattr(self, 'generated_password', generate_random_password())
            self.generated_password = generated_password
            user.set_password(generated_password)
        
        if commit:
            user.save()
            user.roles.set(self.cleaned_data.get('roles', []))
            # Save many-to-many fields
            if 'managed_divisions' in self.cleaned_data:
                user.managed_divisions.set(self.cleaned_data['managed_divisions'])
            if 'managed_locations' in self.cleaned_data:
                user.managed_locations.set(self.cleaned_data['managed_locations'])
            if 'service_cities' in self.cleaned_data:
                user.service_cities.set(self.cleaned_data['service_cities'])
        else:
            user.set_admin_roles(role.code for role in self.cleaned_data.get('roles', []))
        
        return user


class UserProfileForm(forms.ModelForm):
    """
    Form for editing user profile information.
    """
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'email', 'phone_number', 
            'profile_image', 'chinese_name'
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('First Name')
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Last Name')
            }),
            'chinese_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Chinese Name')
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': _('Email Address')
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Phone Number')
            }),
            'profile_image': forms.FileInput(attrs={
                'class': 'form-control-file'
            }),
        }
        labels = {
            'first_name': _('First Name'),
            'last_name': _('Last Name'),
            'chinese_name': _('Chinese Name'),
            'email': _('Email Address'),
            'phone_number': _('Phone Number'),
            'profile_image': _('Profile Image'),
        }
        help_texts = {
            'phone_number': _('Format: +1234567890'),
            'profile_image': _('Upload a profile image (optional)'),
        }


class UserSettingsForm(forms.ModelForm):
    """
    Form for user settings including language preference.
    """
    class Meta:
        model = User
        fields = ['language_preference', 'timezone']
        widgets = {
            'language_preference': forms.Select(attrs={
                'class': 'form-control'
            }),
            'timezone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'UTC',
            }),
        }
        labels = {
            'language_preference': _('Language Preference'),
            'timezone': _('Timezone'),
        }


class SystemSMTPSettingsForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
        label=_('SMTP Password'),
        help_text=_('Leave blank to keep the currently stored password.'),
    )

    class Meta:
        model = SystemSMTPSettings
        fields = [
            'from_email', 'from_display_name', 'username', 'password',
            'smtp_host', 'smtp_port', 'smtp_security', 'timeout', 'is_active',
        ]
        widgets = {
            'from_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'from_display_name': forms.TextInput(attrs={'class': 'form-control'}),
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'smtp_host': forms.TextInput(attrs={'class': 'form-control'}),
            'smtp_port': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'smtp_security': forms.Select(attrs={'class': 'form-select'}),
            'timeout': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        if cleaned_data.get('is_active') and not password and not self.instance.encrypted_password:
            self.add_error('password', _('SMTP password is required for an active SMTP configuration.'))
        if cleaned_data.get('timeout', 0) < 1:
            self.add_error('timeout', _('Connection timeout must be at least 1 second.'))
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            instance.set_password(password)
        if commit:
            instance.save()
        return instance


class UserMailboxSettingsForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'autocomplete': 'new-password',
        }),
        label=_('Mailbox Password'),
        help_text=_('Leave blank to keep the currently stored password.'),
    )

    class Meta:
        model = UserMailboxSettings
        fields = [
            'email_address', 'display_name', 'username', 'password', 'receive_protocol',
            'imap_host', 'imap_port', 'imap_security', 'pop3_host', 'pop3_port', 'pop3_security',
            'smtp_host', 'smtp_port', 'smtp_security', 'sync_lookback_months', 'imap_sent_folder',
            'sync_outbox', 'auto_sync_enabled', 'is_active',
        ]
        widgets = {
            'email_address': forms.EmailInput(attrs={'class': 'form-control'}),
            'display_name': forms.TextInput(attrs={'class': 'form-control'}),
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'receive_protocol': forms.Select(attrs={'class': 'form-select'}),
            'imap_host': forms.TextInput(attrs={'class': 'form-control'}),
            'imap_port': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'imap_security': forms.Select(attrs={'class': 'form-select'}),
            'pop3_host': forms.TextInput(attrs={'class': 'form-control'}),
            'pop3_port': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'pop3_security': forms.Select(attrs={'class': 'form-select'}),
            'smtp_host': forms.TextInput(attrs={'class': 'form-control'}),
            'smtp_port': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'smtp_security': forms.Select(attrs={'class': 'form-select'}),
            'sync_lookback_months': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '24'}),
            'imap_sent_folder': forms.TextInput(attrs={'class': 'form-control'}),
            'sync_outbox': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'auto_sync_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        if not self.instance.pk and not password:
            self.add_error('password', _('Mailbox password is required for a new mailbox configuration.'))

        receive_protocol = cleaned_data.get('receive_protocol')
        if receive_protocol == UserMailboxSettings.ReceiveProtocol.IMAP and not cleaned_data.get('imap_host'):
            self.add_error('imap_host', _('IMAP host is required when IMAP is selected.'))
        if receive_protocol == UserMailboxSettings.ReceiveProtocol.POP3 and not cleaned_data.get('pop3_host'):
            self.add_error('pop3_host', _('POP3 host is required when POP3 is selected.'))
        if not cleaned_data.get('smtp_host'):
            self.add_error('smtp_host', _('SMTP host is required.'))
        if cleaned_data.get('sync_lookback_months', 0) < 1:
            self.add_error('sync_lookback_months', _('Sync lookback must be at least 1 month.'))
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            instance.set_password(password)
        if commit:
            instance.save()
        return instance

    def test_connections(self):
        settings_instance = self.instance
        username = self.cleaned_data['username']
        password = self.cleaned_data.get('password') or settings_instance.password
        if not password:
            raise ValidationError(_('Mailbox password is required before testing the connection.'))

        if self.cleaned_data['receive_protocol'] == UserMailboxSettings.ReceiveProtocol.IMAP:
            host = self.cleaned_data['imap_host']
            port = self.cleaned_data['imap_port']
            security = self.cleaned_data['imap_security']
            if security == UserMailboxSettings.ConnectionSecurity.SSL_TLS:
                mailbox = imaplib.IMAP4_SSL(host, port)
            else:
                mailbox = imaplib.IMAP4(host, port)
                if security == UserMailboxSettings.ConnectionSecurity.STARTTLS:
                    mailbox.starttls()
            mailbox.login(username, password)
            mailbox.logout()
        else:
            host = self.cleaned_data['pop3_host']
            port = self.cleaned_data['pop3_port']
            security = self.cleaned_data['pop3_security']
            if security == UserMailboxSettings.ConnectionSecurity.SSL_TLS:
                mailbox = poplib.POP3_SSL(host, port)
            else:
                mailbox = poplib.POP3(host, port)
                if security == UserMailboxSettings.ConnectionSecurity.STARTTLS and hasattr(mailbox, 'stls'):
                    mailbox.stls()
            mailbox.user(username)
            mailbox.pass_(password)
            mailbox.quit()

        smtp_host = self.cleaned_data['smtp_host']
        smtp_port = self.cleaned_data['smtp_port']
        smtp_security = self.cleaned_data['smtp_security']
        if smtp_security == UserMailboxSettings.ConnectionSecurity.SSL_TLS:
            smtp = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        else:
            smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            smtp.ehlo()
            if smtp_security == UserMailboxSettings.ConnectionSecurity.STARTTLS:
                smtp.starttls()
                smtp.ehlo()
        smtp.login(username, password)
        smtp.quit()

        settings_instance.last_connection_test_at = timezone.now()
        settings_instance.last_connection_status = 'success'
        settings_instance.last_connection_message = _('Mailbox connection test succeeded.')

class TwoFactorSetupForm(forms.Form):
    """
    Form for setting up two-factor authentication.
    """
    token = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control text-center',
            'placeholder': '000000',
            'pattern': '[0-9]{6}',
            'maxlength': '6',
            'autocomplete': 'off',
        }),
        label=_('Verification Code'),
        help_text=_('Enter the 6-digit code from your authenticator app')
    )
    
    def clean_token(self):
        token = self.cleaned_data['token']
        if not token.isdigit():
            raise ValidationError(_('Token must be 6 digits'))
        if len(token) != 6:
            raise ValidationError(_('Token must be exactly 6 digits'))
        return token


class TwoFactorVerifyForm(forms.Form):
    """
    Form for verifying two-factor authentication during login.
    """
    token = forms.CharField(
        max_length=20,  # Allow backup tokens which are longer
        widget=forms.TextInput(attrs={
            'class': 'form-control text-center',
            'placeholder': _('6-digit code or backup token'),
            'autocomplete': 'off',
        }),
        label=_('Verification Code'),
        help_text=_('Enter the 6-digit code from your authenticator app or a backup token')
    )
    
    def clean_token(self):
        token = self.cleaned_data['token']
        # Allow both 6-digit TOTP codes and longer backup tokens
        if len(token) == 6:
            if not token.isdigit():
                raise ValidationError(_('6-digit code must contain only numbers'))
        elif len(token) < 6:
            raise ValidationError(_('Token is too short'))
        # Backup tokens can be alphanumeric and longer
        return token


class LanguageSwitchForm(forms.Form):
    """
    Simple form for language switching.
    """
    language = forms.ChoiceField(
        choices=LANGUAGE_PREFERENCE_CHOICES,
        widget=forms.Select(attrs={
            'class': 'form-control form-control-sm',
            'onchange': 'this.form.submit();'
        }),
        label=''
    )


class UserSearchForm(forms.Form):
    """
    Form for searching users in admin interface.
    """
    search = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Search users by username, name, or email...'),
        }),
        label=''
    )


class CompanyAccessForm(forms.Form):
    """
    Form for managing user access to companies and divisions.
    """
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # This will be implemented when we create the companies app
        # For now, just a placeholder
        pass
