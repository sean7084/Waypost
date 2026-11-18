"""
URL configuration for waypost project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.views.generic import RedirectView

from api.views import APIDocumentationView
from api.wenqu_views import WenquProviderAudioView, WenquUploadDownloadView
from waypost.health import healthz

# Non-internationalized URLs (API, admin, etc.)
urlpatterns = [
    # Liveness/readiness probe. Unauthenticated, no language prefix, and exempt
    # from SECURE_SSL_REDIRECT (see waypost/health.py) so the container
    # healthcheck and the deploy script can reach it over plain HTTP.
    path('healthz/', healthz, name='healthz'),

    # Admin interface
    path('admin/', admin.site.urls),

    # Media files (development only)
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),

    # Static files (development only)
    *static(settings.STATIC_URL, document_root=settings.STATIC_ROOT),

    # Language switching
    path('i18n/', include('django.conf.urls.i18n')),

    # API Documentation
    path('docs/', APIDocumentationView.as_view(), name='api-docs'),

    # REST API
    path('api/v1/', include('api.urls')),

    # Wenqu protected media downloads at the canonical helpdesk paths: every URL
    # wenqu.services.utils.file_store.build_protected_upload_url emits points at
    # /api/uploads/<...>, and Volcengine file ASR fetches /api/provider/audio/<id>.
    # These live outside the /api/v1/wenqu/ namespace deliberately (the namespaced
    # alias in api.urls serves the same view).
    path('api/uploads/<path:relative_path>', WenquUploadDownloadView.as_view(), name='wenqu-upload-download-canonical'),
    path('api/provider/audio/<str:share_id>', WenquProviderAudioView.as_view(), name='wenqu-provider-audio'),
]

# Internationalized URLs
urlpatterns += i18n_patterns(
    # Dashboard URLs
    path('dashboard/', include('dashboard.urls')),

    # Home page redirect to dashboard
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),

    # Authentication URLs
    path('accounts/', include('accounts.urls')),

    # Main application URLs
    path('assets/', include('assets.urls')),
    path('companies/', include('companies.urls')),
    path('inspections/', include('inspections.urls')),
    path('reports/', include('reports.urls')),
    path('users/', include('users.urls')),

    # Product and Customer Management
    path('products/', include('products.urls')),

    # Quotations
    path('quotations/', include('quotations.urls')),

    # Purchases
    path('purchases/', include('purchases.urls')),

    # Deliveries
    path('deliveries/', include('deliveries.urls')),

    # Invoices
    path('invoices/', include('invoices.urls')),

    # Wenqu (问渠) helpdesk staff administration
    path('wenqu/', include('wenqu.urls')),

    # Mobile interface
    path('m/', include('mobile.urls', namespace='mobile')),

    # Login/logout shortcuts
    path('login/', RedirectView.as_view(url='/accounts/login/', permanent=False)),
    path('logout/', RedirectView.as_view(url='/accounts/logout/', permanent=False)),
    prefix_default_language=True,
)
