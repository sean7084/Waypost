"""
Views for Waypost - Accounts App.
This module handles authentication, user management, and profile-related views.
Includes 2FA support, multi-language interface, and role-based access control.
"""

import qrcode
import io
import base64
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.views.generic import (
    TemplateView, FormView, ListView, DetailView, 
    CreateView, UpdateView, DeleteView, View
)
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import activate
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django_otp.models import Device
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.util import random_hex
from django_otp.decorators import otp_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import LogoutView as DjangoLogoutView

from .models import AdminRole, ReceivedEmailMessage, User, UserMailboxSettings, UserSession
from .models import SystemSMTPSettings
from .mailbox_sync import maybe_auto_sync_mailbox, sync_mailbox_messages
from .rfq_ai import process_rfq_message
from .forms import (
    CustomLoginForm, UserRegistrationForm, SuperuserUserForm, UserProfileForm, 
    SystemSMTPSettingsForm, UserSettingsForm, UserMailboxSettingsForm, TwoFactorSetupForm, TwoFactorVerifyForm,
    generate_random_password,
)


def _send_user_temporary_password_email(request, user, temporary_password, *, is_reset=False):
    if not user.email:
        raise ValueError(_('This user does not have an email address.'))

    login_url = request.build_absolute_uri(reverse('accounts:login'))
    subject = _('Your Waypost temporary password')
    intro = (
        _('Your Waypost password has been reset.')
        if is_reset
        else _('Your Waypost account has been created.')
    )
    message = _(
        'Hello {name},\n\n'
        '{intro}\n\n'
        'Username: {username}\n'
        'Temporary password: {temporary_password}\n'
        'Login URL: {login_url}\n\n'
        'You will be required to change your password after signing in.\n'
    ).format(
        name=user.get_full_name() or user.username,
        intro=intro,
        username=user.username,
        temporary_password=temporary_password,
        login_url=login_url,
    )
    send_mail(
        subject,
        message,
        '',
        [user.email],
        fail_silently=False,
    )


class OrderManagementAccessMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.can_manage_orders()

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, _('You do not have access to Order Management.'))
            return redirect('dashboard:dashboard')
        return super().handle_no_permission()
class LoginView(FormView):
    """
    Custom login view with 2FA support and multi-language interface.
    """
    template_name = 'accounts/login.html'
    form_class = CustomLoginForm
    success_url = reverse_lazy('dashboard:dashboard')
    
    def dispatch(self, request, *args, **kwargs):
        # Redirect if user is already logged in
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        username = form.cleaned_data['username']
        password = form.cleaned_data['password']
        user = authenticate(self.request, username=username, password=password)

        if user is not None:
            if user.is_active:
                # Check if 2FA is enabled or required
                if user.two_factor_enabled:
                    # 2FA is enabled - store user ID in session and redirect to verification
                    self.request.session['pre_2fa_user_id'] = str(user.id)
                    self.request.session['pre_2fa_username'] = user.username
                    messages.info(self.request, _('Please verify your identity with two-factor authentication.'))
                    return redirect('accounts:2fa_verify')

                if user.force_2fa_setup:
                    # 2FA setup is required - redirect to setup page
                    self.request.session['pre_2fa_user_id'] = str(user.id)
                    self.request.session['pre_2fa_username'] = user.username
                    messages.info(self.request, _('You are required to set up two-factor authentication.'))
                    return redirect('accounts:2fa_setup')

                # No 2FA required - proceed with login
                auth_login(self.request, user)
                messages.success(self.request, _(f'Welcome back, {user.get_display_name()}!'))

                # Redirect to next page or dashboard
                next_url = self.request.GET.get('next') or self.request.POST.get('next')
                if next_url:
                    return redirect(next_url)
                return redirect('dashboard:dashboard')
            else:
                messages.error(self.request, _('Your account is disabled.'))
        else:
            messages.error(self.request, _('Invalid username or password.'))

        return self.form_invalid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Login')
        context['login_page'] = True  # Flag for base template
        return context


class TwoFactorSetupView(FormView):
    """
    View for setting up two-factor authentication.
    """
    template_name = 'accounts/2fa_setup.html'
    form_class = TwoFactorSetupForm
    success_url = reverse_lazy('accounts:2fa_verify')
    
    def dispatch(self, request, *args, **kwargs):
        # Check if user is in pre-2FA state
        if 'pre_2fa_user_id' not in request.session:
            return redirect('accounts:login')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_id = self.request.session.get('pre_2fa_user_id')
        user = get_object_or_404(User, id=user_id)
        
        # Create or get TOTP device
        device, created = TOTPDevice.objects.get_or_create(
            user=user,
            name='default',
            defaults={'confirmed': False}
        )
        
        # Generate QR code
        qr_code_url = device.config_url
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_code_url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        qr_code_data = base64.b64encode(buffer.getvalue()).decode()
        
        context.update({
            'title': _('Set Up Two-Factor Authentication'),
            'qr_code_data': qr_code_data,
            'secret_key': device.key,
            'user': user,
        })
        return context
    
    def form_valid(self, form):
        user_id = self.request.session.get('pre_2fa_user_id')
        user = get_object_or_404(User, id=user_id)
        token = form.cleaned_data['token']
        
        # Verify the token
        device = TOTPDevice.objects.get(user=user, name='default')
        if device.verify_token(token):
            device.confirmed = True
            device.save()
            
            user.two_factor_enabled = True
            user.force_2fa_setup = False
            user.save()
            
            # Generate backup tokens
            static_device, created = StaticDevice.objects.get_or_create(
                user=user,
                name='backup'
            )
            
            # Generate 10 backup tokens
            tokens = []
            for _ in range(10):
                token = StaticToken.random_token()
                StaticToken.objects.create(device=static_device, token=token)
                tokens.append(token)

            # Store backup tokens in both session and user's backup_tokens field
            self.request.session['backup_tokens'] = tokens
            user.backup_tokens = tokens
            user.save()
            messages.success(self.request, _('Two-factor authentication has been set up successfully.'))
            return redirect('accounts:backup_tokens')
        else:
            messages.error(self.request, _('Invalid token. Please try again.'))
            return self.form_invalid(form)


class TwoFactorVerifyView(FormView):
    """
    View for verifying two-factor authentication during login.
    """
    template_name = 'accounts/2fa_verify.html'
    form_class = TwoFactorVerifyForm
    success_url = reverse_lazy('dashboard:dashboard')
    
    def dispatch(self, request, *args, **kwargs):
        # Check if user is in pre-2FA state
        if 'pre_2fa_user_id' not in request.session:
            return redirect('accounts:login')
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        user_id = self.request.session.get('pre_2fa_user_id')
        user = get_object_or_404(User, id=user_id)
        token = form.cleaned_data['token']
        
        # Try TOTP token first
        totp_devices = TOTPDevice.objects.filter(user=user, confirmed=True)
        for device in totp_devices:
            if device.verify_token(token):
                # Login successful
                auth_login(self.request, user)
                del self.request.session['pre_2fa_user_id']
                
                # Log the session
                UserSession.objects.create(
                    user=user,
                    session_key=self.request.session.session_key,
                    ip_address=self.get_client_ip(),
                    user_agent=self.request.META.get('HTTP_USER_AGENT', '')
                )
                
                messages.success(self.request, _('Welcome back, {user}!').format(user=user.get_full_name_display()))
                return redirect(self.success_url)
        
        # Try backup tokens
        static_devices = StaticDevice.objects.filter(user=user)
        for device in static_devices:
            if device.verify_token(token):
                # Login successful with backup token
                auth_login(self.request, user)
                del self.request.session['pre_2fa_user_id']
                
                # Log the session
                UserSession.objects.create(
                    user=user,
                    session_key=self.request.session.session_key,
                    ip_address=self.get_client_ip(),
                    user_agent=self.request.META.get('HTTP_USER_AGENT', '')
                )
                
                messages.warning(self.request, _('You used a backup token. Please generate new backup tokens.'))
                return redirect(self.success_url)
        
        messages.error(self.request, _('Invalid token. Please try again.'))
        return self.form_invalid(form)
    
    def get_client_ip(self):
        """Get client IP address from request."""
        x_forwarded_for = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = self.request.META.get('REMOTE_ADDR')
        return ip
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Verify Two-Factor Authentication')
        return context


class BackupTokensView(LoginRequiredMixin, TemplateView):
    """
    View for displaying backup tokens after 2FA setup.
    """
    template_name = 'accounts/backup_tokens.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Backup Tokens')
        context['backup_tokens'] = self.request.session.get('backup_tokens', [])
        return context
    
    def post(self, request, *args, **kwargs):
        # Clear backup tokens from session after user acknowledges them
        if 'backup_tokens' in request.session:
            del request.session['backup_tokens']
        return redirect('dashboard:dashboard')


class ProfileView(LoginRequiredMixin, DetailView):
    """
    View for displaying user profile.
    """
    template_name = 'accounts/profile.html'
    context_object_name = 'profile_user'
    
    def get_object(self):
        return self.request.user
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Profile')
        return context


class ProfileEditView(LoginRequiredMixin, UpdateView):
    """
    View for editing user profile.
    """
    template_name = 'accounts/profile_edit.html'
    form_class = UserProfileForm
    success_url = reverse_lazy('accounts:profile')
    
    def get_object(self):
        return self.request.user
    
    def form_valid(self, form):
        messages.success(self.request, _('Profile updated successfully.'))
        return super().form_valid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Profile')
        return context


class UserSettingsView(LoginRequiredMixin, UpdateView):
    """
    View for user settings including language preference.
    """
    template_name = 'accounts/settings.html'
    form_class = UserSettingsForm
    success_url = reverse_lazy('accounts:settings')
    
    def get_object(self):
        return self.request.user

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['prefix'] = 'preferences'
        return kwargs

    def get_mailbox_form(self):
        if not self.request.user.can_manage_orders():
            return None
        mailbox_instance = getattr(self.request.user, 'mailbox_settings', None)
        if self.request.method == 'POST':
            return UserMailboxSettingsForm(self.request.POST, instance=mailbox_instance, prefix='mailbox')
        return UserMailboxSettingsForm(instance=mailbox_instance, prefix='mailbox')

    def get_system_smtp_form(self):
        if not self.request.user.is_superadmin():
            return None
        smtp_instance = SystemSMTPSettings.get_solo()
        if self.request.method == 'POST':
            return SystemSMTPSettingsForm(self.request.POST, instance=smtp_instance, prefix='system_smtp')
        return SystemSMTPSettingsForm(instance=smtp_instance, prefix='system_smtp')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        settings_form = self.get_form()
        mailbox_form = self.get_mailbox_form()
        system_smtp_form = self.get_system_smtp_form()

        if 'save_preferences' in request.POST:
            if settings_form.is_valid():
                return self.preferences_form_valid(settings_form)
            return self.render_to_response(self.get_context_data(form=settings_form, mailbox_form=mailbox_form, system_smtp_form=system_smtp_form))

        if 'test_mailbox' in request.POST and mailbox_form is not None:
            if mailbox_form.is_valid():
                mailbox_settings = mailbox_form.save(commit=False)
                mailbox_settings.user = request.user
                try:
                    mailbox_form.test_connections()
                except Exception as exc:
                    mailbox_settings.last_connection_test_at = timezone.now()
                    mailbox_settings.last_connection_status = 'failed'
                    mailbox_settings.last_connection_message = str(exc)
                    mailbox_settings.save()
                    messages.error(request, _('Mailbox connection test failed: {error}').format(error=exc))
                else:
                    mailbox_settings.save()
                    messages.success(request, _('Mailbox connection test succeeded.'))
            return self.render_to_response(self.get_context_data(form=settings_form, mailbox_form=mailbox_form, system_smtp_form=system_smtp_form))

        if 'save_mailbox' in request.POST and mailbox_form is not None:
            if mailbox_form.is_valid():
                mailbox_settings = mailbox_form.save(commit=False)
                mailbox_settings.user = self.request.user
                mailbox_settings.save()
                messages.success(request, _('Mailbox settings updated successfully.'))
                return redirect(self.success_url)
            return self.render_to_response(self.get_context_data(form=settings_form, mailbox_form=mailbox_form, system_smtp_form=system_smtp_form))

        if 'save_system_smtp' in request.POST and system_smtp_form is not None:
            if system_smtp_form.is_valid():
                system_smtp_form.save()
                messages.success(request, _('System SMTP settings updated successfully.'))
                return redirect(self.success_url)
            return self.render_to_response(self.get_context_data(form=settings_form, mailbox_form=mailbox_form, system_smtp_form=system_smtp_form))

        messages.error(request, _('Select a settings section to save.'))
        return self.render_to_response(self.get_context_data(form=settings_form, mailbox_form=mailbox_form, system_smtp_form=system_smtp_form))

    def preferences_form_valid(self, form):
        response = super().form_valid(form)
        # Activate the selected language
        language = User.normalize_language_code(form.cleaned_data['language_preference'])
        activate(language)
        messages.success(self.request, _('Preferences updated successfully.'))
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Settings')
        context.setdefault('mailbox_form', self.get_mailbox_form())
        context.setdefault('system_smtp_form', self.get_system_smtp_form())
        context['can_manage_orders'] = self.request.user.can_manage_orders()
        context['can_manage_system_smtp'] = self.request.user.is_superadmin()
        context['two_factor_enabled'] = self.request.user.two_factor_enabled
        context['backup_token_count'] = len(self.request.user.backup_tokens or [])
        return context


class MailboxInboxView(LoginRequiredMixin, OrderManagementAccessMixin, ListView):
    template_name = 'accounts/mailbox_inbox.html'
    context_object_name = 'mailbox_messages'
    paginate_by = 20

    def get_queryset(self):
        mailbox = getattr(self.request.user, 'mailbox_settings', None)
        if not mailbox:
            return ReceivedEmailMessage.objects.none()
        maybe_auto_sync_mailbox(mailbox)
        queryset = mailbox.received_messages.all()
        direction = self.request.GET.get('direction') or ReceivedEmailMessage.MessageDirection.INBOX
        if direction in dict(ReceivedEmailMessage.MessageDirection.choices):
            queryset = queryset.filter(direction=direction)
        search = (self.request.GET.get('search') or '').strip()
        if search:
            queryset = queryset.filter(
                Q(subject__icontains=search)
                | Q(sender__icontains=search)
                | Q(recipients__icontains=search)
                | Q(body_preview__icontains=search)
            )
        return queryset.order_by('-received_at', '-sent_at', '-id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Order Management Email')
        mailbox = getattr(self.request.user, 'mailbox_settings', None)
        active_direction = self.request.GET.get('direction') or ReceivedEmailMessage.MessageDirection.INBOX
        context['mailbox'] = mailbox
        context['search_query'] = self.request.GET.get('search', '')
        context['active_direction'] = active_direction
        context['inbox_count'] = mailbox.received_messages.filter(direction=ReceivedEmailMessage.MessageDirection.INBOX).count() if mailbox else 0
        context['outbox_count'] = mailbox.received_messages.filter(direction=ReceivedEmailMessage.MessageDirection.OUTBOX).count() if mailbox else 0
        context['time_column_label'] = _('Receive Time') if active_direction == ReceivedEmailMessage.MessageDirection.INBOX else _('Sent Time')
        return context


class MailboxMessageDetailView(LoginRequiredMixin, OrderManagementAccessMixin, DetailView):
    model = ReceivedEmailMessage
    template_name = 'accounts/mailbox_detail.html'
    context_object_name = 'message'

    def get_queryset(self):
        mailbox = getattr(self.request.user, 'mailbox_settings', None)
        if not mailbox:
            return ReceivedEmailMessage.objects.none()
        return mailbox.received_messages.prefetch_related('linked_quotations', 'email_dispatches').all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['linked_quotation'] = self.object.linked_quotations.order_by('-created_at').first()
        context['reply_draft'] = self.object.email_dispatches.filter(status='draft').order_by('-updated_at').first()
        return context

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        if not self.object.is_read:
            self.object.is_read = True
            self.object.save(update_fields=['is_read', 'synced_at'])
        return response


class MailboxSyncView(LoginRequiredMixin, OrderManagementAccessMixin, View):
    def post(self, request, *args, **kwargs):
        mailbox = getattr(request.user, 'mailbox_settings', None)
        if not mailbox or not mailbox.is_active:
            messages.error(request, _('Configure and activate your mailbox settings first.'))
            return redirect('accounts:mailbox_inbox')

        try:
            synced_count = sync_mailbox_messages(mailbox)
        except Exception as exc:
            mailbox.last_connection_test_at = timezone.now()
            mailbox.last_connection_status = 'failed'
            mailbox.last_connection_message = str(exc)
            mailbox.save(update_fields=['last_connection_test_at', 'last_connection_status', 'last_connection_message', 'updated_at'])
            messages.error(request, _('Mailbox sync failed: {error}').format(error=exc))
        else:
            messages.success(request, _('Mailbox synchronized. Inbox: {inbox}, Outbox: {outbox}.').format(inbox=synced_count['inbox'], outbox=synced_count['outbox']))
        return redirect('accounts:mailbox_inbox')


class MailboxRFQReprocessView(LoginRequiredMixin, OrderManagementAccessMixin, View):
    def post(self, request, pk, *args, **kwargs):
        mailbox = getattr(request.user, 'mailbox_settings', None)
        if not mailbox:
            messages.error(request, _('Configure and activate your mailbox settings first.'))
            return redirect('accounts:mailbox_inbox')

        message = get_object_or_404(mailbox.received_messages.all(), pk=pk)
        process_rfq_message(message)
        messages.success(request, _('RFQ email reprocessed.'))
        return redirect('accounts:mailbox_detail', pk=pk)


class ChangePasswordView(LoginRequiredMixin, FormView):
    """
    View for changing user password.
    """
    template_name = 'accounts/change_password.html'
    form_class = PasswordChangeForm
    success_url = reverse_lazy('accounts:profile')
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
    
    def form_valid(self, form):
        user = form.save()
        update_session_auth_hash(self.request, user)
        messages.success(self.request, _('Password changed successfully.'))
        return super().form_valid(form)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Change Password')
        return context


# Admin-only views for user management
class UserListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    """
    View for listing all administrators (superadmin only).
    """
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    paginate_by = 20
    
    def test_func(self):
        return self.request.user.can_manage_users()
    
    def get_queryset(self):
        queryset = User.objects.all().select_related(
            'managed_company', 'company', 'division'
        ).prefetch_related('roles', 'managed_divisions', 'managed_locations').order_by('username')
        
        # Search filter
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(employee_id__icontains=search)
            )
        
        # Admin role filter
        admin_role = self.request.GET.get('admin_role')
        if admin_role:
            queryset = queryset.filter(roles__code=admin_role).distinct()
        
        # Status filter
        status = self.request.GET.get('status')
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('User Management')
        context['search'] = self.request.GET.get('search', '')
        context['admin_role'] = self.request.GET.get('admin_role', '')
        context['admin_role_choices'] = AdminRole.objects.filter(is_active=True).order_by('name')
        context['status'] = self.request.GET.get('status', '')
        return context


class UserDetailView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    """
    View for displaying administrator details (superadmin only).
    """
    model = User
    template_name = 'accounts/user_detail.html'
    context_object_name = 'profile_user'
    
    def test_func(self):
        return self.request.user.can_manage_users()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Administrator Details')
        return context


class UserCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    """
    View for creating new users (admin only).
    Uses different forms based on user permissions.
    """
    model = User
    template_name = 'accounts/user_create.html'
    success_url = reverse_lazy('accounts:user_list')
    
    def get_form_class(self):
        """Return different form classes based on user permissions."""
        if self.request.user.is_superuser:
            return SuperuserUserForm
        else:
            return UserRegistrationForm
    
    def test_func(self):
        return self.request.user.can_manage_users()
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                user = form.save()
                _send_user_temporary_password_email(
                    self.request,
                    user,
                    form.generated_password,
                    is_reset=False,
                )
        except Exception:
            form.add_error(None, _('Unable to send the initial password email. The user was not created.'))
            return self.form_invalid(form)

        messages.success(
            self.request,
            _('User created successfully. A temporary password was emailed to {email}.').format(email=user.email),
        )
        return redirect(self.success_url)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Create User')
        context['is_superuser'] = self.request.user.is_superuser
        return context


class UserCreateSuccessView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """
    View for showing user creation success with generated password.
    """
    template_name = 'accounts/user_create_success.html'
    
    def test_func(self):
        return self.request.user.can_manage_users()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('User Created Successfully')
        context['generated_password'] = self.request.session.get('generated_password')
        context['new_user_username'] = self.request.session.get('new_user_username')
        context['new_user_email'] = self.request.session.get('new_user_email')
        return context
    
    def dispatch(self, request, *args, **kwargs):
        # Ensure this view is only accessed after user creation
        if 'generated_password' not in request.session:
            return redirect('accounts:user_list')
        return super().dispatch(request, *args, **kwargs)
    
    def post(self, request, *args, **kwargs):
        # Clear sensitive data from session after viewing
        for key in ['generated_password', 'new_user_username', 'new_user_email']:
            request.session.pop(key, None)
        return redirect('accounts:user_list')


class UserEditView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """
    View for editing users (admin only).
    Uses different forms based on user permissions.
    """
    model = User
    template_name = 'accounts/user_edit.html'
    success_url = reverse_lazy('accounts:user_list')
    
    def get_form_class(self):
        """Return different form classes based on user permissions."""
        if self.request.user.is_superuser:
            return SuperuserUserForm
        else:
            return UserRegistrationForm
    
    def test_func(self):
        return self.request.user.can_manage_users()
    
    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, _('User updated successfully.'))
        return HttpResponseRedirect(self.get_success_url())
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit User')
        context['is_superuser'] = self.request.user.is_superuser
        return context


class UserToggleStatusView(LoginRequiredMixin, UserPassesTestMixin, View):
    """
    View for toggling user active status (admin only).
    """
    
    def test_func(self):
        return self.request.user.can_manage_users()
    
    def get_object(self):
        """Get the user object to toggle."""
        return get_object_or_404(User, pk=self.kwargs['pk'])
    
    def post(self, request, *args, **kwargs):
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()
        
        status = _('activated') if user.is_active else _('deactivated')
        messages.success(request, _('User {username} has been {status}.').format(
            username=user.username, status=status
        ))
        return redirect('accounts:user_list')
    
    def get(self, request, *args, **kwargs):
        """Handle GET requests by redirecting to user list."""
        return redirect('accounts:user_list')


class UserResetPasswordView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Generate a temporary password for a user and deliver it by email."""

    def test_func(self):
        return self.request.user.can_manage_users()

    def get_object(self):
        return get_object_or_404(User, pk=self.kwargs['pk'])

    def post(self, request, *args, **kwargs):
        user = self.get_object()

        if user == request.user:
            messages.error(request, _('Reset your own password from your profile settings instead.'))
            return redirect('accounts:user_list')

        if not user.email:
            messages.error(request, _('Cannot reset password because this user does not have an email address.'))
            return redirect('accounts:user_list')

        temporary_password = generate_random_password()

        try:
            with transaction.atomic():
                user.set_password(temporary_password)
                user.must_change_password = True
                user.save(update_fields=['password', 'must_change_password'])
                _send_user_temporary_password_email(
                    request,
                    user,
                    temporary_password,
                    is_reset=True,
                )
        except Exception:
            messages.error(request, _('Unable to send the password reset email. No password changes were saved.'))
            return redirect('accounts:user_list')

        messages.success(
            request,
            _('A temporary password was emailed to {email}.').format(email=user.email),
        )
        return redirect('accounts:user_list')

    def get(self, request, *args, **kwargs):
        return redirect('accounts:user_list')


class SessionListView(LoginRequiredMixin, ListView):
    """
    View for listing user sessions.
    """
    template_name = 'accounts/session_list.html'
    context_object_name = 'sessions'
    paginate_by = 20
    
    def get_queryset(self):
        return UserSession.objects.filter(user=self.request.user, is_active=True).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Active Sessions')
        return context


class SessionTerminateView(LoginRequiredMixin, View):
    """
    View for terminating user sessions.
    """
    
    def get_object(self):
        """Get the session object to terminate."""
        return get_object_or_404(UserSession, pk=self.kwargs['pk'], user=self.request.user)
    
    def post(self, request, *args, **kwargs):
        session = self.get_object()
        session.is_active = False
        session.save()
        
        messages.success(request, _('Session terminated successfully.'))
        return redirect('accounts:session_list')
    
    def get(self, request, *args, **kwargs):
        """Handle GET requests by redirecting to session list."""
        return redirect('accounts:session_list')


@login_required
def setup_2fa_simple(request):
    """Simple 2FA setup view for users enabling 2FA from their profile."""
    from django_otp.plugins.otp_totp.models import TOTPDevice
    from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
    import qrcode
    from io import BytesIO
    import base64
    import pyotp

    user = request.user

    # Check if user already has 2FA enabled
    if user.two_factor_enabled:
        messages.info(request, _('Two-factor authentication is already enabled.'))
        return redirect('accounts:profile')

    if request.method == 'POST':
        verification_code = request.POST.get('verification_code')
        secret = request.session.get('totp_secret')

        if verification_code and secret:
            # Verify the TOTP token
            totp = pyotp.TOTP(secret)
            if totp.verify(verification_code):
                # Enable 2FA
                user.two_factor_enabled = True
                user.force_2fa_setup = False
                user.save()

                # Confirm the TOTP device
                device = TOTPDevice.objects.get(user=user, name='default')
                device.confirmed = True
                device.save()

                # Generate backup tokens
                static_device, created = StaticDevice.objects.get_or_create(
                    user=user,
                    name='backup'
                )

                # Generate 10 backup tokens
                # NB: the loop variable must not be `_`, which is the gettext
                # alias used elsewhere in this function - assigning to `_`
                # would make it function-local and break those earlier calls.
                tokens = []
                for _attempt in range(10):
                    token = StaticToken.random_token()
                    StaticToken.objects.create(device=static_device, token=token)
                    tokens.append(token)

                # Store backup tokens
                user.backup_tokens = tokens
                user.save()

                # Store in session for display
                request.session['backup_tokens'] = tokens

                messages.success(request, _('Two-factor authentication has been enabled successfully!'))
                return redirect('accounts:backup_tokens')
            else:
                messages.error(request, _('Invalid verification code. Please try again.'))
        else:
            messages.error(request, _('Please enter a verification code.'))

    # Get or create TOTP device
    device, created = TOTPDevice.objects.get_or_create(
        user=user,
        name='default',
        defaults={'confirmed': False}
    )

    # Use existing secret or generate new one
    secret = device.key if device.key else pyotp.random_base32()
    if created or not device.key:
        device.key = secret
        device.save()

    # Store secret in session for verification
    request.session['totp_secret'] = secret

    # Create TOTP URI for QR code. The issuer must match OTP_TOTP_ISSUER so the
    # authenticator-app label and django-otp's own provisioning agree.
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.email or user.username,
        issuer_name=settings.OTP_TOTP_ISSUER
    )

    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(totp_uri)
    qr.make(fit=True)

    # Create QR code image
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Convert to base64 for display
    buffer = BytesIO()
    qr_img.save(buffer, format='PNG')
    qr_code_data = base64.b64encode(buffer.getvalue()).decode()
    qr_code_url = f"data:image/png;base64,{qr_code_data}"

    context = {
        'secret_key': secret,
        'qr_code_url': qr_code_url,
    }

    return render(request, 'accounts/setup_2fa.html', context)


@login_required
def disable_2fa(request):
    """Disable 2FA for the user."""
    if request.method == 'POST':
        user = request.user

        # Disable 2FA flags
        user.two_factor_enabled = False
        user.backup_tokens = []
        user.save()

        # Remove all OTP devices (TOTP and Static)
        TOTPDevice.objects.filter(user=user).delete()
        StaticDevice.objects.filter(user=user).delete()

        messages.success(request, _('Two-factor authentication has been disabled.'))
        return redirect('accounts:settings')

    return render(request, 'accounts/disable_2fa.html')


class CustomLogoutView(DjangoLogoutView):
    """
    Custom logout view that handles both GET and POST requests.
    Resolves the Method Not Allowed issue with i18n URLs.
    """
    next_page = '/login/'
    
    def dispatch(self, request, *args, **kwargs):
        """Handle both GET and POST requests for logout"""
        if request.method == 'GET':
            # For GET requests, show confirmation or directly logout
            auth_logout(request)
            messages.success(request, _('You have been successfully logged out.'))
            return HttpResponseRedirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)
    
    def get_success_url(self):
        """Return the URL to redirect to after logout"""
        next_page = self.request.GET.get('next') or self.request.POST.get('next')
        if next_page:
            return next_page
        return self.next_page


def dev_login(request):
    """Development login view - remove in production!"""
    from django.contrib.auth import login
    try:
        user = User.objects.get(username='admin')
        login(request, user)
        messages.success(request, f'Logged in as {user.username} for testing')
        return redirect('dashboard:dashboard')
    except User.DoesNotExist:
        messages.error(request, 'Admin user not found')
        return redirect('accounts:login')
