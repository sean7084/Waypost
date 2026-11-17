"""
URL configuration for accounts app.
Handles authentication, user management, and profile-related URLs.
"""
from django.conf import settings
from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

app_name = 'accounts'

urlpatterns = [
    # Authentication URLs
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    
    # 2FA Setup URLs
    path('2fa/setup/', views.TwoFactorSetupView.as_view(), name='2fa_setup'),
    path('2fa/setup-simple/', views.setup_2fa_simple, name='setup_2fa'),
    path('2fa/disable/', views.disable_2fa, name='disable_2fa'),
    path('2fa/verify/', views.TwoFactorVerifyView.as_view(), name='2fa_verify'),
    path('2fa/backup-tokens/', views.BackupTokensView.as_view(), name='backup_tokens'),
    
    # Profile and Settings URLs
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('profile/edit/', views.ProfileEditView.as_view(), name='profile_edit'),
    path('settings/', views.UserSettingsView.as_view(), name='settings'),
    path('mailbox/', views.MailboxInboxView.as_view(), name='mailbox_inbox'),
    path('mailbox/sync/', views.MailboxSyncView.as_view(), name='mailbox_sync'),
    path('mailbox/<int:pk>/', views.MailboxMessageDetailView.as_view(), name='mailbox_detail'),
    path('mailbox/<int:pk>/reprocess-rfq/', views.MailboxRFQReprocessView.as_view(), name='mailbox_reprocess_rfq'),
    path('change-password/', views.ChangePasswordView.as_view(), name='change_password'),
    
    # User Management (Admin only)
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/create/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<uuid:pk>/', views.UserDetailView.as_view(), name='user_detail'),
    path('users/<uuid:pk>/edit/', views.UserEditView.as_view(), name='user_edit'),
    path('users/<uuid:pk>/reset-password/', views.UserResetPasswordView.as_view(), name='user_reset_password'),
    path('users/<uuid:pk>/toggle-status/', views.UserToggleStatusView.as_view(), name='user_toggle_status'),
    
    # Session Management
    path('sessions/', views.SessionListView.as_view(), name='session_list'),
    path('sessions/<int:pk>/terminate/', views.SessionTerminateView.as_view(), name='session_terminate'),
]

# A passwordless login as `admin` must not exist in a production URLconf at all.
# The view refuses when DEBUG is off too, but a route that is never registered
# cannot be found by something guessing paths.
if settings.DEBUG:
    urlpatterns += [
        path('dev-login/', views.dev_login, name='dev_login'),
    ]
