"""
Dashboard views for Waypost.
Provides main dashboard and overview functionality.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.http import JsonResponse
from assets.models import Asset, AssetCategory, AssetBrand, AssetMaintenance
from companies.models import Company, Division
from accounts.models import User
from deliveries.models import DeliveryOrder
from invoices.models import InvoiceInfo
from purchases.models import PurchaseOrder, PurchaseReceipt
from quotations.models import Quotation
import json


def _ensure_order_management_access(request):
    if request.user.can_manage_orders():
        return None
    messages.error(request, _('You do not have access to Order Management.'))
    return redirect('dashboard:dashboard')


def _build_stock_overview_context():
    assets = Asset.objects.filter(
        source_quotation__isnull=False
    ).select_related('source_quotation', 'brand', 'model', 'category')
    purchase_orders = PurchaseOrder.objects.select_related('quotation')

    by_brand = {}
    for asset in assets:
        brand_name = asset.brand.name if asset.brand else 'Unknown'
        if brand_name not in by_brand:
            by_brand[brand_name] = {
                'total': 0,
                'available': 0,
                'assigned': 0,
            }
        by_brand[brand_name]['total'] += 1
        if asset.status == Asset.AssetStatus.AVAILABLE:
            by_brand[brand_name]['available'] += 1
        elif asset.status == Asset.AssetStatus.ASSIGNED:
            by_brand[brand_name]['assigned'] += 1

    by_quotation = {}
    for asset in assets:
        quotation_number = asset.source_quotation.quotation_number if asset.source_quotation else 'Unknown'
        if quotation_number not in by_quotation:
            by_quotation[quotation_number] = {
                'total': 0,
                'available': 0,
                'assets': [],
                'quotation_pk': asset.source_quotation.pk if asset.source_quotation else None,
            }
        by_quotation[quotation_number]['total'] += 1
        by_quotation[quotation_number]['assets'].append(asset)
        if asset.status == Asset.AssetStatus.AVAILABLE:
            by_quotation[quotation_number]['available'] += 1

    return {
        'total_count': assets.count(),
        'available_count': assets.filter(status=Asset.AssetStatus.AVAILABLE).count(),
        'assigned_count': assets.filter(status=Asset.AssetStatus.ASSIGNED).count(),
        'in_use_count': assets.filter(status=Asset.AssetStatus.IN_USE).count(),
        'po_total': purchase_orders.count(),
        'po_receiving': purchase_orders.filter(status=PurchaseOrder.Status.RECEIVING).count(),
        'po_complete': purchase_orders.filter(status=PurchaseOrder.Status.COMPLETE).count(),
        'converted_quotations': Quotation.objects.filter(purchase_order__isnull=False).count(),
        'ready_dispatch_count': assets.filter(status=Asset.AssetStatus.AVAILABLE).count(),
        'by_brand': by_brand,
        'by_quotation': by_quotation,
        'by_location': assets.values('location__name').annotate(total=Count('id')).order_by('-total'),
        'recent_receipts': PurchaseReceipt.objects.select_related('purchase_order', 'quotation', 'location').order_by('-created_at')[:10],
    }


@login_required
def dashboard_view(request):
    """
    Main dashboard view showing system overview and key metrics.
    """
    user = request.user

    # Get accessible assets based on user's admin role
    accessible_assets = user.get_accessible_assets()

    # Asset statistics
    total_assets = accessible_assets.count()
    assets_by_status = accessible_assets.values('status').annotate(count=Count('id'))
    assets_by_category = accessible_assets.values('category__name').annotate(count=Count('id'))[:5]
    pending_maintenance = AssetMaintenance.objects.filter(
        asset__in=accessible_assets,
        status='scheduled'
    ).count()
    recent_assets = accessible_assets.order_by('-created_at')[:5]

    # Get user's company context for display
    user_company = user.company if hasattr(user, 'company') and user.company else None

    # Convert status QuerySet to dictionary for easier template access
    status_stats = {item['status']: item['count'] for item in assets_by_status}

    # System statistics
    total_companies = Company.objects.count()
    total_users = User.objects.count()
    total_categories = AssetCategory.objects.count()
    total_brands = AssetBrand.objects.count()

    # Recent activity (placeholder for now)
    recent_activities = []

    # Get dashboard preferences from session
    dashboard_config = request.session.get('dashboard_config', {})

    context = {
        'user_company': user_company,
        'total_assets': total_assets,
        'total_companies': total_companies,
        'total_users': total_users,
        'total_categories': total_categories,
        'total_brands': total_brands,
        'pending_maintenance': pending_maintenance,
        'status_stats': status_stats,
        'assets_by_category': assets_by_category,
        'recent_assets': recent_assets,
        'recent_activities': recent_activities,
        'available_assets': status_stats.get('available', 0),
        'assigned_assets': status_stats.get('assigned', 0),
        'maintenance_assets': status_stats.get('maintenance', 0),
        'retired_assets': status_stats.get('retired', 0),
        'dashboard_config': json.dumps(dashboard_config),
    }

    return render(request, 'dashboard/dashboard.html', context)


@login_required
def quick_stats_view(request):
    """
    API-like view for quick stats (for AJAX updates).
    """
    user = request.user
    accessible_assets = user.get_accessible_assets()

    stats = {
        'total_assets': accessible_assets.count(),
        'available_assets': accessible_assets.filter(status='available').count(),
        'assigned_assets': accessible_assets.filter(status='assigned').count(),
        'maintenance_assets': accessible_assets.filter(status='maintenance').count(),
    }

    return render(request, 'dashboard/quick_stats.html', {'stats': stats})


@login_required
def save_dashboard_config(request):
    """
    Save dashboard configuration to session.
    """
    if request.method == 'POST':
        config = json.loads(request.body or '{}')
        request.session['dashboard_config'] = config
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@login_required
def workflow_dashboard_view(request):
    """Q9 integrated workflow dashboard with kanban stages and stock overview."""
    denied_response = _ensure_order_management_access(request)
    if denied_response:
        return denied_response
    quotation_stage_qs = Quotation.objects.filter(status__in=['draft', 'sent', 'confirmed']).select_related('customer').order_by('-created_at')
    purchased_stage_qs = PurchaseOrder.objects.select_related('quotation', 'quotation__customer').order_by('-created_at')
    dispatched_stage_qs = DeliveryOrder.objects.filter(status='dispatched').select_related('quotation', 'quotation__customer').order_by('-created_at')
    delivered_stage_qs = DeliveryOrder.objects.filter(status='completed').select_related('quotation', 'quotation__customer').order_by('-created_at')
    invoiced_stage_qs = InvoiceInfo.objects.select_related('quotation', 'delivery_order').order_by('-created_at')

    workflow_stages = [
        {
            'key': 'quotation',
            'title': 'Quotations',
            'count': quotation_stage_qs.count(),
            'value': quotation_stage_qs.aggregate(total=Sum('total_with_tax')).get('total') or 0,
            'items': quotation_stage_qs[:8],
        },
        {
            'key': 'purchased',
            'title': 'Purchased',
            'count': purchased_stage_qs.count(),
            'value': purchased_stage_qs.aggregate(total=Sum('quotation__total_with_tax')).get('total') or 0,
            'items': purchased_stage_qs[:8],
        },
        {
            'key': 'dispatched',
            'title': 'Dispatched',
            'count': dispatched_stage_qs.count(),
            'value': dispatched_stage_qs.aggregate(total=Sum('quotation__total_with_tax')).get('total') or 0,
            'items': dispatched_stage_qs[:8],
        },
        {
            'key': 'delivered',
            'title': 'Delivered',
            'count': delivered_stage_qs.count(),
            'value': delivered_stage_qs.aggregate(total=Sum('quotation__total_with_tax')).get('total') or 0,
            'items': delivered_stage_qs[:8],
        },
        {
            'key': 'invoiced',
            'title': 'Invoiced',
            'count': invoiced_stage_qs.count(),
            'value': invoiced_stage_qs.aggregate(total=Sum('gross_amount')).get('total') or 0,
            'items': invoiced_stage_qs[:8],
        },
    ]

    context = {
        'workflow_stages': workflow_stages,
        **_build_stock_overview_context(),
    }
    return render(request, 'dashboard/workflow_dashboard.html', context)


@login_required
def workflow_search_view(request):
    """Q9 cross-search across quotations, deliveries, and invoices."""
    denied_response = _ensure_order_management_access(request)
    if denied_response:
        return denied_response
    query = (request.GET.get('q') or '').strip()

    quotation_results = Quotation.objects.none()
    delivery_results = DeliveryOrder.objects.none()
    invoice_results = InvoiceInfo.objects.none()

    if query:
        quotation_results = Quotation.objects.select_related('customer').filter(
            Q(quotation_number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(attn__icontains=query)
            | Q(tel__icontains=query)
        ).order_by('-created_at')[:30]

        delivery_results = DeliveryOrder.objects.select_related('quotation', 'quotation__customer').filter(
            Q(delivery_number__icontains=query)
            | Q(quotation__quotation_number__icontains=query)
            | Q(quotation__customer__name__icontains=query)
            | Q(receiver_name__icontains=query)
            | Q(receiver_phone__icontains=query)
        ).order_by('-created_at')[:30]

        invoice_results = InvoiceInfo.objects.select_related('quotation', 'delivery_order').filter(
            Q(invoice_number__icontains=query)
            | Q(bill_to__icontains=query)
            | Q(kering_group_po_number__icontains=query)
            | Q(internal_order__icontains=query)
            | Q(sap_cost_center__icontains=query)
            | Q(quotation__quotation_number__icontains=query)
            | Q(delivery_order__delivery_number__icontains=query)
        ).order_by('-created_at')[:30]

    context = {
        'query': query,
        'quotation_results': quotation_results,
        'delivery_results': delivery_results,
        'invoice_results': invoice_results,
        'result_count': quotation_results.count() + delivery_results.count() + invoice_results.count(),
    }

    return render(request, 'dashboard/workflow_search.html', context)
