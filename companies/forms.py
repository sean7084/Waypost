"""
Forms for Companies app.
Provides forms for company, division, location, and company user management.
"""
from django import forms
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from .models import Company, Division, Location, CompanyUser

User = get_user_model()


class CompanyForm(forms.ModelForm):
    """
    Form for creating and updating companies.
    """
    
    class Meta:
        model = Company
        fields = [
            'name', 'code', 'description', 'phone_number', 'email', 'website',
            'primary_contact_company_user', 'status', 'logo', 'asset_prefix'
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter company name'),
                'required': True
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter company code (e.g., ISTORE)')
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Enter company description')
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('+1234567890')
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': _('company@example.com')
            }),
            'website': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': _('https://www.company.com')
            }),
            'primary_contact_company_user': forms.Select(attrs={
                'class': 'form-select'
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
            'logo': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*'
            }),
            'asset_prefix': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Asset numbering prefix (e.g., COMP-)')
            })
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['primary_contact_company_user'].required = False
        self.fields['primary_contact_company_user'].empty_label = _('Select primary contact (optional)')

        if self.instance.pk:
            self.fields['primary_contact_company_user'].queryset = self.instance.company_users.filter(
                status=CompanyUser.UserStatus.ACTIVE
            ).order_by('name', 'id')
        else:
            self.fields['primary_contact_company_user'].queryset = CompanyUser.objects.none()

    def clean_primary_contact_company_user(self):
        contact = self.cleaned_data.get('primary_contact_company_user')
        if contact and self.instance.pk and contact.company_id != self.instance.pk:
            raise forms.ValidationError(_('Selected contact must belong to this company.'))
        return contact


class DivisionForm(forms.ModelForm):
    """
    Form for creating and updating divisions.
    """
    
    class Meta:
        model = Division
        fields = [
            'company', 'name', 'code', 'description', 'parent_division',
            'manager', 'phone_number', 'email', 'location', 'building',
            'floor', 'room', 'status', 'budget_code'
        ]
        widgets = {
            'company': forms.Select(attrs={
                'class': 'form-select',
                'required': True
            }),
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter division name'),
                'required': True
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter division code (optional, e.g., IT, HR, FIN)')
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Enter division description')
            }),
            'parent_division': forms.Select(attrs={
                'class': 'form-select'
            }),
            'manager': forms.Select(attrs={
                'class': 'form-select'
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('+1234567890')
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': _('division@company.com')
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter location')
            }),
            'building': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Building name or number')
            }),
            'floor': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Floor number')
            }),
            'room': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Room number')
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
            'budget_code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Budget or cost center code')
            })
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Filter parent divisions based on selected company
        if 'company' in self.data:
            try:
                company_id = int(self.data.get('company'))
                self.fields['parent_division'].queryset = Division.objects.filter(company_id=company_id)
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.company:
            self.fields['parent_division'].queryset = Division.objects.filter(
                company=self.instance.company
            ).exclude(pk=self.instance.pk)
        else:
            self.fields['parent_division'].queryset = Division.objects.none()
        
        # Filter managers to users with appropriate roles
        self.fields['manager'].queryset = self.fields['manager'].queryset.filter(
            roles__code__in=['superadmin', 'it_administrator']
        ).distinct()
        self.fields['manager'].empty_label = _('Select manager (optional)')
        
        # Make code field explicitly optional
        self.fields['code'].required = False
        self.fields['code'].help_text = _('Optional: Unique identifier code for the division within the company')


class LocationForm(forms.ModelForm):
    """
    Form for creating and updating locations.
    """
    
    class Meta:
        model = Location
        fields = [
            'company', 'name', 'name_en', 'code', 'code_2', 'zone', 'rack', 'shelf', 'description',
            'parent_location', 'location_type', 'status', 'area_size',
            'capacity', 'address_line1', 'address_line2', 'city',
            'state_province', 'postal_code', 'country', 'chinese_address',
            'contact', 'email', 'phone_number'
        ]
        labels = {
            'name': _('Location Name (Chinese)'),
            'name_en': _('Location Name (English)'),
            'code_2': _('Location Code 2'),
            'contact': _('Contact for Location'),
            'email': _('E-mail Address'),
            'phone_number': _('Phone Number'),
        }
        widgets = {
            'company': forms.Select(attrs={
                'class': 'form-select',
                'required': True
            }),
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter location name in Chinese'),
                'required': True
            }),
            'name_en': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter location name in English (optional)')
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter location code (optional)')
            }),
            'code_2': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Enter location code 2 (optional)')
            }),
            'zone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Zone range (e.g., Z1-Z2)')
            }),
            'rack': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Rack range (e.g., R1-R3)')
            }),
            'shelf': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Shelf range (e.g., S1-S4)')
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Enter location description')
            }),
            'parent_location': forms.Select(attrs={
                'class': 'form-select'
            }),
            'location_type': forms.Select(attrs={
                'class': 'form-select'
            }),
            'status': forms.Select(attrs={
                'class': 'form-select'
            }),
            'area_size': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': _('Area in square meters'),
                'step': '0.01'
            }),
            'capacity': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': _('Maximum capacity')
            }),
            'address_line1': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Address line 1')
            }),
            'address_line2': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Address line 2 (optional)')
            }),
            'city': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('City')
            }),
            'state_province': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('State/Province')
            }),
            'postal_code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Postal code')
            }),
            'country': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('Country')
            }),
            'chinese_address': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': _('Enter Chinese address')
            }),
            'contact': forms.Select(attrs={
                'class': 'form-select'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': _('location@example.com')
            }),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': _('+8613800000000')
            })
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Filter parent locations based on selected company.
        if 'company' in self.data:
            try:
                company_id = int(self.data.get('company'))
                self.fields['parent_location'].queryset = Location.objects.filter(company_id=company_id)
            except (ValueError, TypeError):
                pass
        elif self.instance.pk and self.instance.company:
            self.fields['parent_location'].queryset = Location.objects.filter(
                company=self.instance.company
            ).exclude(pk=self.instance.pk)
        else:
            self.fields['parent_location'].queryset = Location.objects.none()

        self.fields['parent_location'].empty_label = _('Select parent location (optional)')
        self.fields['contact'].required = False
        self.fields['contact'].empty_label = _('Select company contact (optional)')

        self.fields['zone'].required = False
        self.fields['rack'].required = False
        self.fields['shelf'].required = False
        self.fields['code'].required = False
        self.fields['code'].help_text = _('Optional: location code is not required for create or import.')
        self.fields['zone'].help_text = _('Warehouse only. Supports ranges like Z1-Z2, Z4.')
        self.fields['rack'].help_text = _('Warehouse only. Supports ranges like R1-R3.')
        self.fields['shelf'].help_text = _('Warehouse only. Supports ranges like S1-S4.')

        if 'company' in self.data:
            try:
                company_id = int(self.data.get('company'))
                self.fields['contact'].queryset = CompanyUser.objects.filter(
                    company_id=company_id,
                    status=CompanyUser.UserStatus.ACTIVE,
                ).order_by('name', 'id')
            except (ValueError, TypeError):
                self.fields['contact'].queryset = CompanyUser.objects.none()
        elif self.instance.pk and self.instance.company:
            self.fields['contact'].queryset = CompanyUser.objects.filter(
                company=self.instance.company,
                status=CompanyUser.UserStatus.ACTIVE,
            ).order_by('name', 'id')
        else:
            self.fields['contact'].queryset = CompanyUser.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        location_type = cleaned_data.get('location_type')
        company = cleaned_data.get('company')
        contact = cleaned_data.get('contact')

        if location_type != Location.LocationType.WAREHOUSE:
            cleaned_data['zone'] = None
            cleaned_data['rack'] = None
            cleaned_data['shelf'] = None

        if company and contact and contact.company_id != company.id:
            self.add_error('contact', _('Selected contact must belong to the selected company.'))

        return cleaned_data


class CompanyUserForm(forms.ModelForm):
    """Form for adding company contacts/recipients."""

    class Meta:
        model = CompanyUser
        fields = ['name', 'company', 'role', 'location', 'status', 'employee_id', 'department', 'job_title', 'work_phone', 'work_email', 'is_authorized_rfq_sender']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'company': forms.Select(attrs={'class': 'form-select'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'employee_id': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'job_title': forms.TextInput(attrs={'class': 'form-control'}),
            'work_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'work_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'is_authorized_rfq_sender': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['location'].required = False
        self.fields['location'].empty_label = _('Select location (optional)')
        self.fields['company'].queryset = Company.objects.filter(status=Company.CompanyStatus.ACTIVE).order_by('name')

        if 'company' in self.data:
            try:
                company_id = int(self.data.get('company'))
                self.fields['location'].queryset = Location.objects.filter(
                    company_id=company_id,
                    status=Location.LocationStatus.ACTIVE,
                ).order_by('name')
            except (TypeError, ValueError):
                self.fields['location'].queryset = Location.objects.none()
        elif self.instance.pk:
            self.fields['location'].queryset = Location.objects.filter(
                company=self.instance.company,
                status=Location.LocationStatus.ACTIVE,
            ).order_by('name')
        else:
            self.fields['location'].queryset = Location.objects.none()
        self.fields['is_authorized_rfq_sender'].required = False

    def clean(self):
        cleaned_data = super().clean()
        company = cleaned_data.get('company')
        location = cleaned_data.get('location')
        name = (cleaned_data.get('name') or '').strip()
        work_email = (cleaned_data.get('work_email') or '').strip().lower()

        if not name:
            self.add_error('name', _('Name is required.'))
        else:
            cleaned_data['name'] = name

        # One-way sync: include user data in company contact when email matches,
        # but never create or update User records from this form.
        if work_email:
            linked_user = User.objects.filter(email__iexact=work_email).first()
            if linked_user:
                cleaned_data['user'] = linked_user
                if not cleaned_data.get('work_phone') and linked_user.phone_number:
                    cleaned_data['work_phone'] = linked_user.phone_number
                if not cleaned_data.get('name'):
                    cleaned_data['name'] = linked_user.get_full_name_display()

        if company and location and location.company_id != company.id:
            self.add_error('location', _('Selected location must belong to the selected company.'))

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        linked_user = self.cleaned_data.get('user')
        if linked_user:
            instance.user = linked_user
        if commit:
            instance.save()
        return instance


class CSVImportForm(forms.Form):
    """Generic CSV import form for companies module pages."""

    file = forms.FileField(
        label=_('CSV File'),
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv',
        }),
        help_text=_('Upload a CSV file encoded as UTF-8.')
    )

    def clean_file(self):
        file = self.cleaned_data['file']
        if not file.name.lower().endswith('.csv'):
            raise ValidationError(_('Only CSV files are supported.'))
        if file.size > 10 * 1024 * 1024:
            raise ValidationError(_('File too large. Maximum size is 10MB.'))
        return file
