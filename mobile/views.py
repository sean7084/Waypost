"""
Views for Waypost Mobile Interface.
"""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.db.models import Count

from assets.models import Asset


class MobileDashboardView(LoginRequiredMixin, TemplateView):
    """
    Mobile-friendly dashboard view.
    """
    template_name = 'mobile/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get accessible assets
        accessible_assets = user.get_accessible_assets()

        # Stats
        context['total_assets'] = accessible_assets.count()
        context['available_assets'] = accessible_assets.filter(status='available').count()

        # Recent assets
        context['recent_assets'] = accessible_assets.order_by('-created_at')[:5]

        return context


class MobileScanView(LoginRequiredMixin, TemplateView):
    """
    Mobile barcode scanning view.
    """
    template_name = 'mobile/scan.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context
