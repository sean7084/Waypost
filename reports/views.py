"""
Views for Waypost Reports App.
Provides reporting views with filtering and chart data.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView, ListView, DetailView
from django.db.models import Count, Q, Sum, Avg
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from datetime import datetime, timedelta

from assets.models import Asset, AssetCategory, AssetBrand, AssetAssignment, AssetMaintenance
from companies.models import Company, Division, Location
from quotations.models import Quotation
from .models import ReportTemplate, GeneratedReport


class ReportDashboardView(LoginRequiredMixin, TemplateView):
    """
    Report dashboard - overview of available reports and recent generations.
    """
    template_name = 'reports/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get accessible companies based on user role
        if user.is_superadmin():
            companies = Company.objects.all()
        elif user.is_it_administrator():
            companies = Company.objects.filter(id=user.managed_company_id)
        else:
            companies = Company.objects.none()

        # Asset statistics
        accessible_assets = user.get_accessible_assets()
        context['total_assets'] = accessible_assets.count()
        context['assets_by_status'] = accessible_assets.values('status').annotate(
            count=Count('id')
        ).order_by('status')

        # Category distribution
        context['assets_by_category'] = accessible_assets.values(
            'category__name'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        # Brand distribution
        context['assets_by_brand'] = accessible_assets.values(
            'brand__name'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        # Recent assets
        context['recent_assets'] = accessible_assets.order_by('-created_at')[:10]

        # Assets needing attention
        context['maintenance_due'] = AssetMaintenance.objects.filter(
            asset__in=accessible_assets,
            status='scheduled',
            scheduled_date__lte=datetime.now().date() + timedelta(days=7)
        ).count()

        context['warranty_expiring'] = accessible_assets.filter(
            warranty_end_date__lte=datetime.now().date() + timedelta(days=30),
            warranty_end_date__gte=datetime.now().date()
        ).count()

        context['unassigned_assets'] = accessible_assets.filter(
            status='available',
            assigned_to__isnull=False
        ).exclude(
            assigned_to__isnull=True
        ).count()

        return context


class AssetInventoryReportView(LoginRequiredMixin, ListView):
    """
    Asset inventory report with filtering capabilities.
    """
    model = Asset
    template_name = 'reports/asset_inventory.html'
    context_object_name = 'assets'
    paginate_by = 50

    def get_queryset(self):
        user = self.request.user
        queryset = user.get_accessible_assets().select_related(
            'category', 'brand', 'company', 'assigned_to', 'location'
        )

        # Filter by status
        status_filter = self.request.GET.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter by category
        category = self.request.GET.get('category')
        if category:
            queryset = queryset.filter(category_id=category)

        # Filter by brand
        brand = self.request.GET.get('brand')
        if brand:
            queryset = queryset.filter(brand_id=brand)

        # Filter by company
        company = self.request.GET.get('company')
        if company:
            queryset = queryset.filter(company_id=company)

        # Filter by location
        location = self.request.GET.get('location')
        if location:
            queryset = queryset.filter(location_id=location)

        # Filter by assignment status
        assignment_filter = self.request.GET.get('assignment')
        if assignment_filter == 'assigned':
            queryset = queryset.filter(assigned_to__isnull=False)
        elif assignment_filter == 'unassigned':
            queryset = queryset.filter(assigned_to__isnull=True)

        # Filter by date range
        date_from = self.request.GET.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        date_to = self.request.GET.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Add filter choices
        context['status_choices'] = Asset.AssetStatus.choices
        context['categories'] = AssetCategory.objects.filter(is_active=True)
        context['brands'] = AssetBrand.objects.filter(is_active=True)
        context['companies'] = Company.objects.filter(status='active')

        # Preserve filter values for form
        context['current_filters'] = {
            'status': self.request.GET.get('status', ''),
            'category': self.request.GET.get('category', ''),
            'brand': self.request.GET.get('brand', ''),
            'company': self.request.GET.get('company', ''),
            'location': self.request.GET.get('location', ''),
            'assignment': self.request.GET.get('assignment', ''),
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
        }

        return context


class AssetStatusChartView(LoginRequiredMixin, TemplateView):
    """
    API view for asset status chart data (JSON).
    """
    template_name = 'reports/charts/status_chart.html'

    def render_to_response(self, context, **response_kwargs):
        user = self.request.user
        assets = user.get_accessible_assets()

        status_counts = assets.values('status').annotate(
            count=Count('id')
        ).order_by('status')

        data = {
            'labels': [],
            'datasets': [{
                'data': [],
                'backgroundColor': []
            }]
        }

        color_map = {
            'available': '#28a745',
            'assigned': '#17a2b8',
            'in_use': '#ffc107',
            'maintenance': '#dc3545',
            'repair': '#fd7e14',
            'retired': '#6c757d',
            'disposed': '#dee2e6',
            'lost': '#343a40',
            'stolen': '#dc3545',
        }

        for item in status_counts:
            status_display = dict(Asset.AssetStatus.choices).get(item['status'], item['status'])
            data['labels'].append(status_display)
            data['datasets'][0]['data'].append(item['count'])
            data['datasets'][0]['backgroundColor'].append(
                color_map.get(item['status'], '#6c757d')
            )

        return JsonResponse(data)


class AssetCategoryChartView(LoginRequiredMixin, TemplateView):
    """
    API view for asset category distribution chart data (JSON).
    """
    template_name = 'reports/charts/category_chart.html'

    def render_to_response(self, context, **response_kwargs):
        user = self.request.user
        assets = user.get_accessible_assets()

        category_counts = assets.values(
            'category__name'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        data = {
            'labels': [],
            'datasets': [{
                'label': _('Assets by Category'),
                'data': [],
                'backgroundColor': []
            }]
        }

        colors = [
            '#007bff', '#6610f2', '#6f42c1', '#e83e8c', '#dc3545',
            '#fd7e14', '#ffc107', '#28a745', '#20c997', '#17a2b8'
        ]

        for i, item in enumerate(category_counts):
            category_name = item['category__name'] or _('Uncategorized')
            data['labels'].append(category_name)
            data['datasets'][0]['data'].append(item['count'])
            data['datasets'][0]['backgroundColor'].append(colors[i % len(colors)])

        return JsonResponse(data)


class AssetBrandChartView(LoginRequiredMixin, TemplateView):
    """
    API view for asset brand distribution chart data (JSON).
    """
    template_name = 'reports/charts/brand_chart.html'

    def render_to_response(self, context, **response_kwargs):
        user = self.request.user
        assets = user.get_accessible_assets()

        brand_counts = assets.values(
            'brand__name'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]

        data = {
            'labels': [],
            'datasets': [{
                'label': _('Assets by Brand'),
                'data': [],
                'backgroundColor': []
            }]
        }

        colors = [
            '#0d6efd', '#6610f2', '#6f42c1', '#e83e8c', '#dc3545',
            '#fd7e14', '#ffc107', '#28a745', '#20c997', '#17a2b8'
        ]

        for i, item in enumerate(brand_counts):
            brand_name = item['brand__name'] or _('Unknown Brand')
            data['labels'].append(brand_name)
            data['datasets'][0]['data'].append(item['count'])
            data['datasets'][0]['backgroundColor'].append(colors[i % len(colors)])

        return JsonResponse(data)


class WarrantyStatusChartView(LoginRequiredMixin, TemplateView):
    """
    API view for warranty status chart data (JSON).
    """
    template_name = 'reports/charts/warranty_chart.html'

    def render_to_response(self, context, **response_kwargs):
        user = self.request.user
        assets = user.get_accessible_assets()

        today = datetime.now().date()
        thirty_days = today + timedelta(days=30)

        expired = assets.filter(
            warranty_end_date__lt=today
        ).count()

        expiring_soon = assets.filter(
            warranty_end_date__gte=today,
            warranty_end_date__lte=thirty_days
        ).count()

        active = assets.filter(
            warranty_end_date__gt=thirty_days
        ).count()

        no_warranty = assets.filter(
            warranty_end_date__isnull=True
        ).count()

        data = {
            'labels': [
                _('Expired'),
                _('Expiring Soon'),
                _('Active'),
                _('No Warranty')
            ],
            'datasets': [{
                'data': [expired, expiring_soon, active, no_warranty],
                'backgroundColor': ['#dc3545', '#ffc107', '#28a745', '#6c757d']
            }]
        }

        return JsonResponse(data)


class QuickStatsView(LoginRequiredMixin, TemplateView):
    """
    Quick statistics for AJAX updates.
    """
    template_name = 'reports/quick_stats.html'

    def render_to_response(self, context, **response_kwargs):
        user = self.request.user
        assets = user.get_accessible_assets()

        stats = {
            'total': assets.count(),
            'available': assets.filter(status='available').count(),
            'assigned': assets.filter(status='assigned').count(),
            'in_use': assets.filter(status='in_use').count(),
            'maintenance': assets.filter(status='maintenance').count(),
            'retired': assets.filter(status='retired').count(),
        }

        return JsonResponse(stats)


class QuotationStatusChartView(LoginRequiredMixin, TemplateView):
    """
    API view for quotation status chart data (JSON).
    """
    template_name = 'reports/charts/quotation_status_chart.html'

    def render_to_response(self, context, **response_kwargs):
        status_counts = Quotation.objects.values('status').annotate(
            count=Count('id')
        ).order_by('status')

        data = {
            'labels': [],
            'datasets': [{
                'data': [],
                'backgroundColor': []
            }]
        }

        color_map = {
            'draft': '#6c757d',
            'sent': '#17a2b8',
            'confirmed': '#28a745',
            'cancelled': '#dc3545',
        }

        for item in status_counts:
            status_display = dict(Quotation.QuotationStatus.choices).get(item['status'], item['status'])
            data['labels'].append(status_display)
            data['datasets'][0]['data'].append(item['count'])
            data['datasets'][0]['backgroundColor'].append(
                color_map.get(item['status'], '#6c757d')
            )

        return JsonResponse(data)


class PurchaseSummaryChartView(LoginRequiredMixin, TemplateView):
    """
    API view for purchase summary chart data (JSON).
    """
    template_name = 'reports/charts/purchase_summary_chart.html'

    def render_to_response(self, context, **response_kwargs):
        # Get quotation totals by status
        quotations = Quotation.objects.filter(status='confirmed')
        total_quotation_value = sum(q.total_with_tax for q in quotations)

        # Get purchased assets value
        purchased_assets = Asset.objects.filter(source_quotation__isnull=False)
        total_purchased_value = sum(float(a.purchase_price or 0) for a in purchased_assets)

        data = {
            'labels': [
                _('Quotation Value'),
                _('Purchased Value'),
                _('Pending Value')
            ],
            'datasets': [{
                'data': [total_quotation_value, total_purchased_value, total_quotation_value - total_purchased_value],
                'backgroundColor': ['#28a745', '#17a2b8', '#ffc107']
            }]
        }

        return JsonResponse(data)
