"""
Views for Quotations app.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import HttpResponse, HttpResponseRedirect
from django.db import transaction
from django.db.models import Q, ProtectedError
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from django.utils.translation import gettext as _
from decimal import Decimal, InvalidOperation
import datetime
import json

from companies.models import Company, CompanyUser
from deliveries.services import (
    build_dispatch_asset_assignments,
    create_delivery_items_from_asset_assignments,
    get_dispatch_asset_queryset,
    split_quotation_items_for_delivery,
    sync_service_delivery_items,
)
from products.models import ProductPrice
from utils.import_rollback import get_latest_rollback_run, rollback_run
from .import_xlsx import (
    IMPORT_MODULE,
    IMPORT_PREVIEW_SESSION_KEY,
    IMPORT_STATUS_CHOICES,
    IMPORT_TYPE,
    build_preview_payload,
    catalog_prices,
    commit_import,
    serialize_catalog,
    validate_confirm_payload,
    validate_upload_files,
)
from .models import Quotation, QuotationItem, QuotationAttachment
from .forms import QuotationForm, QuotationItemForm, QuotationExcelImportForm
from .services import render_quotation_pdf_html
from .template_registry import (
    DEFAULT_QUOTATION_TEMPLATE,
    get_quotation_template_choices,
    get_serializable_template_definitions,
)


class OrderManagementAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.can_manage_orders()

    def handle_no_permission(self):
        messages.error(self.request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')


def _normalize_name(name):
    return ' '.join((name or '').split()).strip()


def _get_membership_user_name(membership):
    if hasattr(membership, 'get_contact_name'):
        name = _normalize_name(membership.get_contact_name())
        return '' if name == '-' else name
    user = getattr(membership, 'user', None)
    if not user:
        return ''
    return _normalize_name(user.get_display_name() or user.get_full_name())


def _get_membership_user_phone(membership):
    if hasattr(membership, 'get_contact_phone'):
        return membership.get_contact_phone() or ''
    user = getattr(membership, 'user', None)
    return membership.work_phone or getattr(user, 'phone_number', '') or ''


def _get_membership_user_email(membership):
    if hasattr(membership, 'get_contact_email'):
        return membership.get_contact_email() or ''
    user = getattr(membership, 'user', None)
    return getattr(user, 'email', '') or ''


def _find_company_user_by_name(company, name):
    target = _normalize_name(name).lower()
    if not target:
        return None

    memberships = company.company_users.select_related('user').all()
    for membership in memberships:
        display_name = _get_membership_user_name(membership).lower()
        if target == display_name:
            return membership
    return None


def _build_location_display(location):
    name = _normalize_name(location.name)
    codes = []
    for code in [location.code, location.code_2]:
        normalized = _normalize_name(code)
        if normalized and normalized.lower() not in [value.lower() for value in codes]:
            codes.append(normalized)
    if name and codes:
        return f"{name} ({' / '.join(codes)})"
    return name or ' / '.join(codes)


def _customer_has_location_label(company, label):
    target = _normalize_name(label).lower()
    if not target:
        return False
    for location in company.locations.all():
        if _build_location_display(location).lower() == target:
            return True
    return False


def _split_name(full_name):
    parts = _normalize_name(full_name).split()
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], ' '.join(parts[1:])


def _create_company_user(company, full_name, phone='', email=''):
    UserModel = get_user_model()
    first_name, last_name = _split_name(full_name)

    base = slugify(full_name) or 'user'
    candidate = f'auto_{base}'
    suffix = 1
    while UserModel.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f'auto_{base}_{suffix}'

    user = UserModel(
        username=candidate[:150],
        first_name=first_name[:150],
        last_name=last_name[:150],
        phone_number=(phone or '')[:20],
        email=(email or '')[:254],
        company=company,
    )
    user.set_unusable_password()
    user.save()

    membership = CompanyUser.objects.create(
        user=user,
        company=company,
        role=CompanyUser.CompanyRole.EMPLOYEE,
        status=CompanyUser.UserStatus.ACTIVE,
        work_phone=(phone or '')[:20],
        work_email=(email or '')[:254],
    )
    return membership


def _ensure_company_user(company, full_name, phone='', email=''):
    normalized = _normalize_name(full_name)
    if not normalized:
        return None, False

    existing = _find_company_user_by_name(company, normalized)
    if existing:
        fields_to_update = []
        normalized_phone = (phone or '').strip()
        normalized_email = (email or '').strip()
        if normalized_phone and existing.work_phone != normalized_phone:
            existing.work_phone = normalized_phone[:20]
            fields_to_update.append('work_phone')
        if normalized_email and existing.work_email != normalized_email:
            existing.work_email = normalized_email[:254]
            fields_to_update.append('work_email')
        user = getattr(existing, 'user', None)
        if user is not None:
            if normalized_phone and getattr(user, 'phone_number', '') != normalized_phone:
                user.phone_number = normalized_phone[:20]
                user.save(update_fields=['phone_number'])
            if normalized_email and getattr(user, 'email', '') != normalized_email:
                user.email = normalized_email[:254]
                user.save(update_fields=['email'])
        if fields_to_update:
            existing.save(update_fields=fields_to_update + ['updated_at'])
        return existing, False
    return _create_company_user(company, normalized, phone, email), True


def _build_customer_user_context(customers):
    company_users_by_customer = {}
    global_authorized_attention_contacts = []
    global_authorized_contact_keys = set()
    for customer in customers:
        seen_contacts = set()
        seen_product_users = set()
        attention_contacts = []
        product_users = []
        for membership in customer.company_users.select_related('user').all():
            name = _get_membership_user_name(membership)
            if not name:
                continue
            key = name.lower()
            phone = _get_membership_user_phone(membership)
            email = _get_membership_user_email(membership)
            contact_payload = {
                'name': name,
                'phone': phone,
                'email': email,
                'value': name,
                'display': name,
                'search_text': ' '.join(filter(None, [name, phone, email])).lower(),
            }
            if membership.is_authorized_rfq_sender and key not in seen_contacts:
                seen_contacts.add(key)
                attention_contacts.append(contact_payload)
            if membership.is_authorized_rfq_sender:
                global_key = '|'.join([
                    key,
                    (email or '').lower(),
                    (phone or '').lower(),
                    str(customer.pk),
                ])
                if global_key not in global_authorized_contact_keys:
                    global_authorized_contact_keys.add(global_key)
                    global_authorized_attention_contacts.append({
                        **contact_payload,
                        'company_name': customer.name,
                        'search_text': ' '.join(filter(None, [name, phone, email, customer.name])).lower(),
                    })
            if key not in seen_product_users:
                seen_product_users.add(key)
                product_users.append({
                    'type': 'contact',
                    **contact_payload,
                })

        for location in customer.locations.select_related('contact__user').all():
            display = _build_location_display(location)
            if not display:
                continue
            key = display.lower()
            if key in seen_product_users:
                continue
            seen_product_users.add(key)
            contact_name = _get_membership_user_name(location.contact) if location.contact_id else ''
            location_email = (location.email or '').strip()
            if not location_email and location.contact_id:
                location_email = _get_membership_user_email(location.contact)
            location_phone = (location.phone_number or '').strip()
            if not location_phone and location.contact_id:
                location_phone = _get_membership_user_phone(location.contact)
            product_users.append({
                'type': 'location',
                'name': _normalize_name(location.name),
                'phone': location_phone,
                'email': location_email,
                'code': _normalize_name(location.code),
                'code_2': _normalize_name(location.code_2),
                'value': display,
                'display': display,
                'search_text': ' '.join(filter(None, [
                    location.name,
                    location.code,
                    location.code_2,
                    display,
                    contact_name,
                ])).lower(),
            })

        company_users_by_customer[str(customer.pk)] = {
            'attention_contacts': attention_contacts,
            'product_users': product_users,
        }

    company_codes = list(
        Company.objects.filter(status=Company.CompanyStatus.ACTIVE)
        .exclude(code='')
        .order_by('code')
        .values_list('code', flat=True)
        .distinct()
    )

    return company_users_by_customer, company_codes, global_authorized_attention_contacts


def _get_active_quotation_customers():
    return Company.objects.filter(status=Company.CompanyStatus.ACTIVE).order_by('name')


def _get_quotation_editor_customers():
    return _get_active_quotation_customers().select_related(
        'primary_contact_company_user__user'
    ).prefetch_related(
        'company_users__user',
        'locations__contact__user',
    )


def _get_template_selector_context():
    return {
        'quotation_template_definitions': get_serializable_template_definitions(),
        'default_quotation_template_code': DEFAULT_QUOTATION_TEMPLATE,
    }


def _get_quotation_editor_products(quotation=None):
    queryset = ProductPrice.objects.select_related('brand', 'model', 'service_item')
    if quotation is None:
        return queryset.filter(is_current=True).order_by('service_item__name', 'brand__name', 'model__name')

    product_price_ids = quotation.items.values_list('product_price_id', flat=True)
    return queryset.filter(
        Q(is_current=True) | Q(pk__in=product_price_ids)
    ).distinct().order_by('service_item__name', 'brand__name', 'model__name')


def _apply_product_price_snapshot(item, product_price):
    item.service_item = product_price.service_item if product_price.service_item_id else None
    item.brand_name = product_price.display_brand_name
    item.product_description = product_price.display_description
    item.model_number = product_price.display_model_number
    item.unit = product_price.display_unit
    return item


def _get_latest_delivery_order(quotation):
    prefetched = getattr(quotation, '_prefetched_objects_cache', {}).get('delivery_orders')
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return quotation.delivery_orders.order_by('-created_at').first()


def _can_dispatch_quotation_directly(quotation):
    assignments = _allocate_internal_assets_for_quotation(quotation)
    return assignments is not None


def _create_or_get_purchase_order_from_quotation(quotation):
    from purchases.models import PurchaseOrder, PurchaseOrderItem

    purchasable_items, _ = split_quotation_items_for_delivery(quotation)
    if not purchasable_items:
        return None, False, 0

    purchase_order, created = PurchaseOrder.objects.get_or_create(
        quotation=quotation,
        defaults={
            'status': PurchaseOrder.Status.ORDERED,
        }
    )

    created_items = 0
    for item in purchasable_items:
        _, item_created = PurchaseOrderItem.objects.get_or_create(
            purchase_order=purchase_order,
            quotation_item=item,
            defaults={
                'product_price': item.product_price,
                'brand': item.product_price.brand if item.product_price else None,
                'model': item.product_price.model if item.product_price else None,
                'product_description': item.product_description,
                'unit': item.unit,
                'quantity_ordered': item.quantity,
                'unit_price': item.unit_price,
            }
        )
        if item_created:
            created_items += 1

    purchase_order.recalculate_progress()
    return purchase_order, created, created_items


def _allocate_internal_assets_for_quotation(quotation):
    return build_dispatch_asset_assignments(quotation, get_dispatch_asset_queryset(quotation))


def _create_delivery_order_from_direct_dispatch(quotation, assignments):
    from deliveries.models import DeliveryOrder

    existing_delivery = _get_latest_delivery_order(quotation)
    if existing_delivery:
        sync_service_delivery_items(existing_delivery)
        return existing_delivery, False

    customer_info = quotation.get_customer_info()
    delivery_order = DeliveryOrder.objects.create(
        quotation=quotation,
        delivery_date=datetime.date.today(),
        receiver_name=customer_info.get('attn') or quotation.attn or quotation.customer.name,
        receiver_phone=customer_info.get('tel') or quotation.tel or '',
        delivery_address=customer_info.get('delivery_address') or quotation.customer.get_full_address(),
        delivery_method='',
        remarks='Created automatically from confirmed quotation for direct dispatch.',
    )

    create_delivery_items_from_asset_assignments(delivery_order, assignments)
    sync_service_delivery_items(delivery_order)

    return delivery_order, True


class QuotationListView(OrderManagementAccessMixin, ListView):
    """List view for quotations with filtering."""
    model = Quotation
    template_name = 'quotations/list.html'
    context_object_name = 'quotations'
    paginate_by = 20

    def get_queryset(self):
        queryset = Quotation.objects.select_related('customer', 'source_email_message', 'purchase_order').prefetch_related('delivery_orders').all()

        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        # Filter by customer
        customer_id = self.request.GET.get('customer')
        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)

        # Search
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(quotation_number__icontains=search) |
                Q(customer__name__icontains=search) |
                Q(attn__icontains=search)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Quotation.QuotationStatus.choices
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_customer'] = self.request.GET.get('customer', '')
        context['search_query'] = self.request.GET.get('search', '')
        context['customers'] = _get_active_quotation_customers()
        for quotation in context['quotations']:
            quotation.current_delivery_order = _get_latest_delivery_order(quotation)
            quotation.can_dispatch_directly = (
                quotation.status == Quotation.QuotationStatus.CONFIRMED
                and quotation.current_delivery_order is None
                and _can_dispatch_quotation_directly(quotation)
            )
        return context




class QuotationDefaultTemplateView(OrderManagementAccessMixin, TemplateView):
    template_name = 'quotations/default_templates.html'

    def get_companies(self):
        return _get_active_quotation_customers()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['companies'] = self.get_companies()
        context['template_choices'] = get_quotation_template_choices()
        context['template_definitions'] = get_serializable_template_definitions().values()
        return context

    def post(self, request, *args, **kwargs):
        valid_template_codes = {code for code, _ in get_quotation_template_choices()}
        updated_count = 0

        for company in self.get_companies():
            field_name = f'company_{company.pk}'
            selected_template = request.POST.get(field_name, company.default_quotation_template)
            if selected_template not in valid_template_codes:
                continue
            if company.default_quotation_template == selected_template:
                continue

            company.default_quotation_template = selected_template
            company.save(update_fields=['default_quotation_template', 'updated_at'])
            updated_count += 1

        if updated_count == 0:
            messages.info(request, 'No default quotation templates were changed.')
        elif updated_count == 1:
            messages.success(request, 'Updated default quotation templates for 1 company.')
        else:
            messages.success(request, f'Updated default quotation templates for {updated_count} companies.')

        return redirect('quotations:default_templates')


class QuotationDetailView(OrderManagementAccessMixin, DetailView):
    """Detail view for quotation."""
    model = Quotation
    template_name = 'quotations/detail.html'
    context_object_name = 'quotation'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.all()
        context['attachments'] = self.object.attachments.all()
        context['purchase_order'] = getattr(self.object, 'purchase_order', None)
        context['current_delivery_order'] = _get_latest_delivery_order(self.object)
        context['can_dispatch_directly'] = (
            self.object.status == Quotation.QuotationStatus.CONFIRMED
            and context['current_delivery_order'] is None
            and _can_dispatch_quotation_directly(self.object)
        )
        context['auto_download_pdf'] = self.request.GET.get('download_pdf') == '1'
        rfq_extracted_data = getattr(self.object.source_email_message, 'rfq_extracted_data', {}) if self.object.source_email_message_id else {}
        item_matching = rfq_extracted_data.get('item_matching') or {}
        context['rfq_item_warnings'] = item_matching.get('warnings') or []
        context['rfq_matched_items'] = item_matching.get('matched_items') or []
        return context


class QuotationCreateView(OrderManagementAccessMixin, CreateView):
    """Create view for quotation."""
    model = Quotation
    form_class = QuotationForm
    template_name = 'quotations/form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields.pop('status', None)
        return form

    def get_initial(self):
        initial = super().get_initial()
        initial['quotation_date'] = datetime.date.today()
        initial['valid_until'] = datetime.date.today() + datetime.timedelta(days=30)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customers = _get_quotation_editor_customers()
        company_users_by_customer, company_codes, authorized_attention_contacts = _build_customer_user_context(customers)
        context['customers'] = customers
        context['products'] = _get_quotation_editor_products()
        context['company_users_by_customer'] = company_users_by_customer
        context['company_codes'] = company_codes
        context['authorized_attention_contacts'] = authorized_attention_contacts
        context['is_edit'] = False
        context.update(_get_template_selector_context())
        return context

    def form_valid(self, form):
        quotation = form.save(commit=False)
        submit_action = self.request.POST.get('submit_action')
        if submit_action == 'save_draft':
            quotation.status = Quotation.QuotationStatus.DRAFT
        else:
            quotation.status = Quotation.QuotationStatus.SENT

        company_user, created = _ensure_company_user(
            quotation.customer,
            quotation.attn,
            quotation.tel,
            quotation.attn_email,
        )
        if company_user:
            resolved_name = _get_membership_user_name(company_user)
            if resolved_name:
                quotation.attn = resolved_name
            resolved_phone = _get_membership_user_phone(company_user)
            if resolved_phone:
                quotation.tel = resolved_phone
            resolved_email = _get_membership_user_email(company_user)
            if resolved_email:
                quotation.attn_email = resolved_email

        contact = quotation.customer.primary_contact_company_user
        if contact:
            if not quotation.attn:
                quotation.attn = _get_membership_user_name(contact)
            if not quotation.tel:
                quotation.tel = _get_membership_user_phone(contact)
            if not quotation.attn_email:
                quotation.attn_email = _get_membership_user_email(contact)

        quotation.save()

        # Handle line items from POST
        items_data = self.request.POST.getlist('items')
        for item_data in items_data:
            if not item_data:
                continue

            # Accept both formats:
            # 1) new|<product_price_id>|<quantity>|<user_brand>|<user_name>|<unit_price>|<tax_rate>
            # 2) <product_price_id>|<quantity>|<user_brand>|<user_name>
            parts = item_data.split('|')
            if len(parts) < 4:
                continue

            if parts[0] == 'new' and len(parts) >= 5:
                product_price_id, quantity, user_brand, user_name = parts[1], parts[2], parts[3], parts[4]
                unit_price = parts[5] if len(parts) > 5 else None
                tax_rate = parts[6] if len(parts) > 6 else None
            elif parts[0].startswith('item_'):
                # Ignore existing-item payload in create flow.
                continue
            else:
                product_price_id, quantity, user_brand, user_name = parts[0], parts[1], parts[2], parts[3]
                unit_price = None
                tax_rate = None

            try:
                product_price = ProductPrice.objects.select_related('brand', 'model', 'service_item').get(pk=product_price_id)
                quantity = int(quantity)
                if user_name and not _customer_has_location_label(quotation.customer, user_name):
                    _ensure_company_user(quotation.customer, user_name, '')
                item = QuotationItem(
                    quotation=quotation,
                    product_price=product_price,
                    quantity=quantity,
                    user_brand=user_brand or quotation.customer.code,
                    user_name=user_name
                )
                _apply_product_price_snapshot(item, product_price)
                item.unit_price = Decimal(unit_price) if unit_price else product_price.price_without_tax
                item.tax_rate = Decimal(tax_rate) if tax_rate else product_price.tax_rate
                item.save()
            except (ProductPrice.DoesNotExist, ValueError, InvalidOperation):
                pass

        messages.success(self.request, f'Quotation {quotation.quotation_number} created successfully.')
        if submit_action == 'create_download_pdf':
            return HttpResponseRedirect(f"{reverse_lazy('quotations:detail', args=[quotation.pk])}?download_pdf=1")
        return HttpResponseRedirect(reverse_lazy('quotations:detail', args=[quotation.pk]))


class QuotationUpdateView(UpdateView):
    """Update view for quotation."""
    model = Quotation
    form_class = QuotationForm
    template_name = 'quotations/form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customers = _get_quotation_editor_customers()
        company_users_by_customer, company_codes, authorized_attention_contacts = _build_customer_user_context(customers)
        context['customers'] = customers
        context['products'] = _get_quotation_editor_products(self.object)
        context['company_users_by_customer'] = company_users_by_customer
        context['company_codes'] = company_codes
        context['authorized_attention_contacts'] = authorized_attention_contacts
        context['items'] = self.object.items.all()
        context['is_edit'] = True
        context.update(_get_template_selector_context())
        return context

    def form_valid(self, form):
        quotation = form.save()

        company_user, created = _ensure_company_user(
            quotation.customer,
            quotation.attn,
            quotation.tel,
            quotation.attn_email,
        )
        if company_user:
            resolved_name = _get_membership_user_name(company_user)
            if resolved_name:
                quotation.attn = resolved_name
            resolved_phone = _get_membership_user_phone(company_user)
            if resolved_phone:
                quotation.tel = resolved_phone
            resolved_email = _get_membership_user_email(company_user)
            if resolved_email:
                quotation.attn_email = resolved_email
            quotation.save(update_fields=['attn', 'tel', 'attn_email', 'updated_at'])
        elif quotation.customer.primary_contact_company_user and not quotation.attn_email:
            quotation.attn_email = _get_membership_user_email(quotation.customer.primary_contact_company_user)
            quotation.save(update_fields=['attn_email', 'updated_at'])

        # Update line items
        # First, delete removed items
        submitted_ids = set()
        items_data = self.request.POST.getlist('items')

        for item_data in items_data:
            if not item_data:
                continue

            # Existing item: item_<pk>|<product_price_id>|<quantity>|<user_brand>|<user_name>|<unit_price>|<tax_rate>
            # New item: new|<product_price_id>|<quantity>|<user_brand>|<user_name>|<unit_price>|<tax_rate>
            parts = item_data.split('|')
            if len(parts) < 5:
                continue

            marker = parts[0]
            product_price_id, quantity, user_brand, user_name = parts[1], parts[2], parts[3], parts[4]
            unit_price = parts[5] if len(parts) > 5 else None
            tax_rate = parts[6] if len(parts) > 6 else None

            try:
                product_price = ProductPrice.objects.select_related('brand', 'model', 'service_item').get(pk=product_price_id)
                quantity = int(quantity)

                if marker.startswith('item_'):
                    item_pk = marker.replace('item_', '')
                    submitted_ids.add(item_pk)
                    item = QuotationItem.objects.get(pk=item_pk, quotation=quotation)
                    if user_name and not _customer_has_location_label(quotation.customer, user_name):
                        _ensure_company_user(quotation.customer, user_name, '')
                    item.product_price = product_price
                    item.quantity = quantity
                    item.user_brand = user_brand or quotation.customer.code
                    item.user_name = user_name
                    _apply_product_price_snapshot(item, product_price)
                    item.unit_price = Decimal(unit_price) if unit_price else product_price.price_without_tax
                    item.tax_rate = Decimal(tax_rate) if tax_rate else product_price.tax_rate
                    item.save()
                elif marker == 'new':
                    if user_name and not _customer_has_location_label(quotation.customer, user_name):
                        _ensure_company_user(quotation.customer, user_name, '')
                    item = QuotationItem(
                        quotation=quotation,
                        product_price=product_price,
                        quantity=quantity,
                        user_brand=user_brand or quotation.customer.code,
                        user_name=user_name,
                    )
                    _apply_product_price_snapshot(item, product_price)
                    item.unit_price = Decimal(unit_price) if unit_price else product_price.price_without_tax
                    item.tax_rate = Decimal(tax_rate) if tax_rate else product_price.tax_rate
                    item.save()
                    submitted_ids.add(str(item.pk))
            except (ProductPrice.DoesNotExist, QuotationItem.DoesNotExist, ValueError, InvalidOperation):
                pass

        # Delete items not in submitted list
        for item in quotation.items.all():
            if str(item.pk) not in submitted_ids:
                item.delete()

        messages.success(self.request, f'Quotation {quotation.quotation_number} updated successfully.')
        return HttpResponseRedirect(reverse_lazy('quotations:detail', args=[quotation.pk]))


class QuotationDeleteView(DeleteView):
    """Delete view for quotation."""
    model = Quotation
    template_name = 'quotations/confirm_delete.html'
    success_url = reverse_lazy('quotations:list')

    def form_valid(self, form):
        messages.success(self.request, 'Quotation deleted successfully.')
        return super().form_valid(form)


def generate_quotation_pdf(request, pk):
    """Generate PDF for quotation using HTML template rendering."""
    quotation = get_object_or_404(Quotation, pk=pk)
    try:
        pdf_bytes = render_quotation_pdf_html(quotation)
    except Exception:
        messages.error(request, 'Quotation PDF generation failed. Please verify HTML template and WeasyPrint runtime dependencies.')
        return redirect(reverse_lazy('quotations:detail', args=[quotation.pk]))

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{quotation.quotation_number}.pdf"'
    return response


def duplicate_quotation(request, pk):
    """Duplicate an existing quotation."""
    original = get_object_or_404(Quotation, pk=pk)

    with transaction.atomic():
        # Create new quotation
        new_quotation = Quotation(
            customer=original.customer,
            quotation_date=datetime.date.today(),
            valid_until=datetime.date.today() + datetime.timedelta(days=30),
            attn=original.attn,
            tel=original.tel,
            attn_email=original.attn_email,
            pdf_template=original.pdf_template,
            status=Quotation.QuotationStatus.DRAFT,
            remarks=original.remarks,
            notes=original.notes,
        )
        new_quotation.save()

        # Copy items
        for item in original.items.all():
            new_item = QuotationItem(
                quotation=new_quotation,
                product_price=item.product_price,
                service_item=item.service_item,
                brand_name=item.brand_name,
                product_description=item.product_description,
                model_number=item.model_number,
                unit=item.unit,
                quantity=item.quantity,
                unit_price=item.unit_price,
                tax_rate=item.tax_rate,
                user_brand=item.user_brand,
                user_name=item.user_name,
            )
            new_item.save()

    messages.success(request, f'Quotation duplicated as {new_quotation.quotation_number}.')
    return HttpResponseRedirect(reverse_lazy('quotations:detail', args=[new_quotation.pk]))


def cancel_quotation(request, pk):
    """Cancel a quotation."""
    quotation = get_object_or_404(Quotation, pk=pk)
    quotation.status = Quotation.QuotationStatus.CANCELLED
    quotation.save()
    messages.success(request, f'Quotation {quotation.quotation_number} cancelled.')
    return redirect(reverse_lazy('quotations:detail', args=[quotation.pk]))


def confirm_quotation(request, pk):
    """Mark quotation as confirmed and advance fulfillment."""
    quotation = get_object_or_404(Quotation, pk=pk)
    quotation.status = Quotation.QuotationStatus.CONFIRMED
    quotation.requires_confirmation = False
    quotation.save(update_fields=['status', 'requires_confirmation', 'updated_at'])
    if quotation.source_email_message_id:
        quotation.source_email_message.rfq_status = quotation.source_email_message.RFQStatus.QUOTATION_CONFIRMED
        quotation.source_email_message.save(update_fields=['rfq_status', 'synced_at'])

    existing_delivery = _get_latest_delivery_order(quotation)
    if existing_delivery:
        messages.info(request, f'Quotation {quotation.quotation_number} is already linked to delivery order {existing_delivery.delivery_number}.')
        return redirect('deliveries:detail', pk=existing_delivery.pk)

    assignments = _allocate_internal_assets_for_quotation(quotation)
    if assignments is not None:
        with transaction.atomic():
            delivery_order, _ = _create_delivery_order_from_direct_dispatch(quotation, assignments)
        messages.success(request, f'Quotation {quotation.quotation_number} confirmed and delivery order {delivery_order.delivery_number} was created directly.')
        return redirect('deliveries:detail', pk=delivery_order.pk)

    purchase_order = getattr(quotation, 'purchase_order', None)
    if purchase_order:
        messages.info(request, f'Quotation {quotation.quotation_number} is already linked to purchase order {purchase_order.po_number}.')
        if purchase_order.status == purchase_order.Status.COMPLETE:
            return redirect('deliveries:create_from_quotation', quotation_pk=quotation.pk)
        return redirect('purchases:receive', pk=purchase_order.pk)

    with transaction.atomic():
        purchase_order, created, created_items = _create_or_get_purchase_order_from_quotation(quotation)

    if purchase_order is None:
        messages.error(request, f'Quotation {quotation.quotation_number} has no purchasable lines and could not be dispatched directly.')
        return redirect('quotations:detail', pk=quotation.pk)

    if created_items:
        messages.success(request, f'Quotation {quotation.quotation_number} confirmed and purchase order {purchase_order.po_number} was created with {created_items} line(s).')
    elif created:
        messages.warning(request, f'Quotation {quotation.quotation_number} confirmed and purchase order {purchase_order.po_number} was created without lines.')
    else:
        messages.info(request, f'Quotation {quotation.quotation_number} confirmed. Purchase order {purchase_order.po_number} already exists.')
    return redirect('purchases:receive', pk=purchase_order.pk)


def send_quotation(request, pk):
    """Mark quotation as sent."""
    quotation = get_object_or_404(Quotation, pk=pk)
    quotation.status = Quotation.QuotationStatus.SENT
    quotation.requires_confirmation = False
    quotation.save(update_fields=['status', 'requires_confirmation', 'updated_at'])
    messages.success(request, f'Quotation {quotation.quotation_number} marked as sent.')
    return redirect(reverse_lazy('quotations:detail', args=[quotation.pk]))


def attachment_upload(request, pk):
    """Upload attachment to quotation."""
    quotation = get_object_or_404(Quotation, pk=pk)

    if request.method == 'POST':
        attachment_type = request.POST.get('attachment_type')
        file = request.FILES.get('file')
        notes = request.POST.get('notes', '')

        if not file:
            messages.error(request, 'No file provided.')
            return redirect(reverse_lazy('quotations:detail', args=[pk]))

        # Validate file type
        allowed_extensions = ['.pdf', '.ofd', '.zip']
        import os
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in allowed_extensions:
            messages.error(request, f'Invalid file type. Allowed: {", ".join(allowed_extensions)}')
            return redirect(reverse_lazy('quotations:detail', args=[pk]))

        # Create attachment
        attachment = QuotationAttachment(
            quotation=quotation,
            attachment_type=attachment_type,
            file=file,
            notes=notes
        )
        attachment.save()

        messages.success(request, 'Attachment uploaded successfully.')

    return redirect(reverse_lazy('quotations:detail', args=[pk]))


def attachment_delete(request, pk):
    """Delete an attachment."""
    attachment = get_object_or_404(QuotationAttachment, pk=pk)
    quotation_pk = attachment.quotation.pk

    if request.method == 'POST':
        attachment.file.delete()
        attachment.delete()
        messages.success(request, 'Attachment deleted.')

    return redirect(reverse_lazy('quotations:detail', args=[quotation_pk]))


def convert_to_purchase(request, pk):
    """Convert confirmed quotation to purchase order records."""
    quotation = get_object_or_404(Quotation, pk=pk)

    if quotation.status != Quotation.QuotationStatus.CONFIRMED:
        messages.error(request, 'Only confirmed quotations can be converted to purchase.')
        return redirect(reverse_lazy('quotations:detail', args=[pk]))

    existing_delivery = _get_latest_delivery_order(quotation)
    if existing_delivery:
        messages.info(request, f'Quotation {quotation.quotation_number} already has delivery order {existing_delivery.delivery_number}.')
        return redirect('deliveries:detail', pk=existing_delivery.pk)

    with transaction.atomic():
        assignments = _allocate_internal_assets_for_quotation(quotation)
        if assignments is not None:
            delivery_order, _ = _create_delivery_order_from_direct_dispatch(quotation, assignments)
            messages.success(request, f'Quotation {quotation.quotation_number} can be dispatched directly. Delivery order {delivery_order.delivery_number} was created.')
            return redirect('deliveries:detail', pk=delivery_order.pk)

        purchase_order, created, created_items = _create_or_get_purchase_order_from_quotation(quotation)

    if purchase_order is None:
        messages.error(request, f'Quotation {quotation.quotation_number} has no purchasable lines.')
        return redirect('quotations:detail', pk=quotation.pk)

    if created_items:
        messages.success(request, f'Created purchase order {purchase_order.po_number} with {created_items} line(s).')
    elif created:
        messages.warning(request, f'Purchase order {purchase_order.po_number} was created without lines.')
    else:
        messages.info(request, f'Purchase order {purchase_order.po_number} already exists. You can continue receiving stock.')

    return redirect('purchases:receive', pk=purchase_order.pk)


def _can_manage_orders(request):
    return request.user.can_manage_orders()


def _import_preview_context(payload):
    return {
        'preview_mode': True,
        'preview': payload,
        'customers': Company.objects.filter(status=Company.CompanyStatus.ACTIVE).order_by('name'),
        'catalog_json': serialize_catalog(catalog_prices()),
        'import_status_choices': [
            (value, label)
            for value, label in Quotation.QuotationStatus.choices
            if value in IMPORT_STATUS_CHOICES
        ],
    }


@login_required
def quotation_import_view(request):
    """Upload -> preview/map -> confirm, for externally issued quotation workbooks."""
    if not _can_manage_orders(request):
        messages.error(request, _('You do not have access to Order Management.'))
        return redirect('dashboard:dashboard')

    back_url = reverse_lazy('quotations:list')
    latest_run = get_latest_rollback_run(request.user, IMPORT_MODULE, IMPORT_TYPE)
    context = {
        'back_url': back_url,
        'rollback_url': reverse_lazy('quotations:import_rollback') if latest_run else None,
    }

    if request.method == 'POST' and request.POST.get('confirm_import') == '1':
        payload = request.session.get(IMPORT_PREVIEW_SESSION_KEY)
        if not payload:
            messages.error(request, _('Import preview expired. Please upload the files again.'))
            return redirect('quotations:import')
        try:
            submitted = json.loads(request.POST.get('mappings_payload') or '{}')
        except (TypeError, ValueError):
            submitted = {}
        decisions, errors = validate_confirm_payload(payload, submitted)
        if not errors:
            try:
                result = commit_import(request.user, payload, decisions)
            except Exception as exc:
                errors = [str(exc)]
        if errors:
            context.update(_import_preview_context(payload))
            context['form'] = QuotationExcelImportForm(initial={'status': payload['default_status']})
            context['confirm_errors'] = errors
            return render(request, 'quotations/import.html', context)

        request.session.pop(IMPORT_PREVIEW_SESSION_KEY, None)
        request.session.modified = True
        messages.success(request, _('Created %(count)s quotations: %(numbers)s.') % {
            'count': result['created'],
            'numbers': ', '.join(q.quotation_number for q in result['quotations']),
        })
        return render(request, 'common/import_result.html', {
            'title': _('Quotation Import Result'),
            'back_url': back_url,
            'total_rows': result['total_rows'],
            'processed_rows': result['processed_rows'],
            'created': result['created'],
            'updated': 0,
            'skipped': result['skipped'],
            'errors': [],
            'issues': result['issues'],
            'rollback_url': (
                reverse_lazy('quotations:import_rollback') if result['run'].can_rollback else None
            ),
        })

    if request.method == 'POST':
        files = request.FILES.getlist('files')
        form = QuotationExcelImportForm(request.POST)
        upload_errors = validate_upload_files(files)
        if upload_errors or not form.is_valid():
            context['form'] = form
            context['upload_errors'] = upload_errors
            return render(request, 'quotations/import.html', context)
        payload = build_preview_payload(files, form.cleaned_data['status'])
        request.session[IMPORT_PREVIEW_SESSION_KEY] = payload
        request.session.modified = True
        context.update(_import_preview_context(payload))
        context['form'] = form
        return render(request, 'quotations/import.html', context)

    context['form'] = QuotationExcelImportForm()
    return render(request, 'quotations/import.html', context)


@login_required
@require_POST
def quotation_import_rollback_view(request):
    if not _can_manage_orders(request):
        messages.error(request, _('You do not have access to Order Management.'))
        return redirect('dashboard:dashboard')

    run = get_latest_rollback_run(request.user, IMPORT_MODULE, IMPORT_TYPE)
    if run is None:
        messages.warning(request, _('No rollback-eligible import run was found.'))
        return redirect('quotations:import')

    try:
        outcome = rollback_run(run)
    except ProtectedError:
        messages.error(request, _('Rollback blocked: imported catalog entries are referenced by other records.'))
        return redirect('quotations:import')

    if outcome['errors']:
        messages.warning(
            request,
            _('Rollback completed with warnings: %(count)s issue(s).') % {'count': len(outcome['errors'])}
        )
    messages.success(
        request,
        _('Rollback completed. Deleted %(deleted)s created records and restored %(restored)s updated records.') % {
            'deleted': outcome['deleted'],
            'restored': outcome['restored'],
        }
    )
    return redirect('quotations:import')
