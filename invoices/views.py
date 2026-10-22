import tempfile
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import FileResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView
from django.utils import timezone

from utils.generated_documents import (
    TEMP_DIR_PREFIX,
    convert_xlsx_to_pdf,
    generated_document_dir,
    open_generated_document,
)

from .forms import EmailDispatchForm, InvoiceInfoForm, SharepointImportForm
from .models import EmailDispatch, InvoiceInfo, WeeklyOrderBatch
from .services import (
    collect_email_attachments,
    export_invoice_infos_to_excel,
    fill_invoice_template,
    process_sharepoint_batch,
    recalculate_invoice_from_delivery,
    send_email_dispatch,
)


class OrderManagementAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.can_manage_orders()

    def handle_no_permission(self):
        messages.error(self.request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')


class WeeklyOrderBatchListView(OrderManagementAccessMixin, ListView):
    model = WeeklyOrderBatch
    template_name = 'invoices/weekly_batch_list.html'
    context_object_name = 'batches'
    paginate_by = 20

    def get_queryset(self):
        queryset = WeeklyOrderBatch.objects.select_related('processed_by').all()

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(batch_id__icontains=search)
                | Q(failure_reason__icontains=search)
                | Q(sharepoint_file__icontains=search)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = WeeklyOrderBatch.Status.choices
        context['selected_status'] = self.request.GET.get('status', '')
        context['search_query'] = self.request.GET.get('search', '')
        return context


class WeeklyOrderBatchDetailView(OrderManagementAccessMixin, DetailView):
    model = WeeklyOrderBatch
    template_name = 'invoices/weekly_batch_detail.html'
    context_object_name = 'batch'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['invoice_infos'] = self.object.invoice_infos.all().order_by('source_row_number')
        return context


class InvoiceInfoListView(OrderManagementAccessMixin, ListView):
    model = InvoiceInfo
    template_name = 'invoices/invoice_info_list.html'
    context_object_name = 'invoice_infos'
    paginate_by = 20

    def get_queryset(self):
        queryset = InvoiceInfo.objects.select_related('quotation', 'delivery_order', 'weekly_batch').all()

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(invoice_number__icontains=search)
                | Q(bill_to__icontains=search)
                | Q(kering_group_po_number__icontains=search)
                | Q(internal_order__icontains=search)
                | Q(sap_cost_center__icontains=search)
            )

        date_from = self.request.GET.get('date_from')
        if date_from:
            queryset = queryset.filter(invoice_date__gte=date_from)

        date_to = self.request.GET.get('date_to')
        if date_to:
            queryset = queryset.filter(invoice_date__lte=date_to)

        return queryset.order_by('-invoice_date', '-id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        return context


class InvoiceInfoDetailView(OrderManagementAccessMixin, DetailView):
    model = InvoiceInfo
    template_name = 'invoices/invoice_info_detail.html'
    context_object_name = 'invoice_info'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = InvoiceInfoForm(instance=self.object)
        context['items'] = self.object.items.all().order_by('line_number')
        return context


class EmailDispatchListView(OrderManagementAccessMixin, ListView):
    model = EmailDispatch
    template_name = 'invoices/email_dispatch_list.html'
    context_object_name = 'dispatches'
    paginate_by = 20

    def get_queryset(self):
        queryset = EmailDispatch.objects.select_related('quotation', 'invoice_info', 'delivery_order').all()

        quotation_id = self.request.GET.get('quotation')
        if quotation_id:
            queryset = queryset.filter(quotation_id=quotation_id)

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(quotation__quotation_number__icontains=search)
                | Q(subject__icontains=search)
                | Q(sent_to__icontains=search)
                | Q(cc__icontains=search)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = EmailDispatch.DispatchStatus.choices
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_quotation'] = self.request.GET.get('quotation', '')
        context['search_query'] = self.request.GET.get('search', '')
        return context


@login_required
def import_sharepoint_batch_view(request):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    if request.method == 'POST':
        form = SharepointImportForm(request.POST, request.FILES)
        if form.is_valid():
            batch = WeeklyOrderBatch.objects.create(
                sharepoint_file=form.cleaned_data['sharepoint_file'],
                processed_by=request.user,
            )
            try:
                result = process_sharepoint_batch(batch, user=request.user)
            except Exception as exc:
                reason = str(exc)
                row_number = None
                if reason.lower().startswith('row '):
                    try:
                        row_number = int(reason.split(':', 1)[0].replace('Row', '').strip())
                    except ValueError:
                        row_number = None

                batch.status = WeeklyOrderBatch.Status.FAILED
                batch.failure_reason = reason
                batch.failed_row_number = row_number
                batch.processed_at = None
                batch.total_rows = 0
                batch.created_rows = 0
                batch.save(
                    update_fields=[
                        'status',
                        'failure_reason',
                        'failed_row_number',
                        'processed_at',
                        'total_rows',
                        'created_rows',
                    ]
                )
                messages.error(request, f'Import failed: {reason}')
                return redirect('invoices:batch_detail', pk=batch.pk)

            messages.success(
                request,
                f'Import completed successfully. {result.created_rows} rows processed.',
            )
            return redirect('invoices:batch_detail', pk=batch.pk)
    else:
        form = SharepointImportForm()

    return render(request, 'invoices/import_sharepoint.html', {'form': form})


@login_required
def invoice_info_update_view(request, pk):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    invoice_info = get_object_or_404(InvoiceInfo, pk=pk)

    if request.method != 'POST':
        return redirect('invoices:invoice_detail', pk=pk)

    form = InvoiceInfoForm(request.POST, instance=invoice_info)
    if form.is_valid():
        invoice_info = form.save()
        if invoice_info.delivery_order_id:
            recalculate_invoice_from_delivery(invoice_info)
        messages.success(request, f'Invoice {invoice_info.invoice_number} updated.')
    else:
        messages.error(request, '; '.join([f'{k}: {", ".join(v)}' for k, v in form.errors.items()]))

    return redirect('invoices:invoice_detail', pk=pk)


@login_required
def invoice_info_recalculate_view(request, pk):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    if request.method != 'POST':
        messages.warning(request, 'Invalid request method.')
        return redirect('invoices:invoice_detail', pk=pk)

    invoice_info = get_object_or_404(InvoiceInfo, pk=pk)
    if not invoice_info.delivery_order_id:
        messages.warning(request, 'Link a delivery order before recalculating.')
        return redirect('invoices:invoice_detail', pk=pk)

    recalculate_invoice_from_delivery(invoice_info)
    messages.success(request, 'Invoice totals recalculated from delivery items.')
    return redirect('invoices:invoice_detail', pk=pk)


@login_required
def invoice_info_document_view(request, pk):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    invoice_info = get_object_or_404(InvoiceInfo, pk=pk)
    if invoice_info.delivery_order_id:
        recalculate_invoice_from_delivery(invoice_info)

    # Deliberately not a `with` block: FileResponse reads the file lazily while
    # the view is already off the stack, so the directory has to be owned by the
    # response itself (risk R2 - a context manager deletes it before byte one).
    holder = tempfile.TemporaryDirectory(prefix=f'{TEMP_DIR_PREFIX}invoice-')
    try:
        xlsx_path = fill_invoice_template(invoice_info, Path(holder.name))
    except FileNotFoundError as exc:
        holder.cleanup()
        messages.error(request, str(exc))
        return redirect('invoices:invoice_detail', pk=pk)

    pdf_path = convert_xlsx_to_pdf(xlsx_path)
    document_path = pdf_path or xlsx_path
    if not pdf_path:
        messages.warning(request, 'PDF converter not found. Downloading filled Excel file instead.')
    try:
        document = open_generated_document(document_path, holder)
        return FileResponse(document, as_attachment=True, filename=document_path.name)
    except Exception:
        holder.cleanup()
        raise


@login_required
def invoice_info_export_view(request):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    queryset = InvoiceInfo.objects.select_related('quotation', 'delivery_order', 'weekly_batch').all()

    search = request.GET.get('search')
    if search:
        queryset = queryset.filter(
            Q(invoice_number__icontains=search)
            | Q(bill_to__icontains=search)
            | Q(kering_group_po_number__icontains=search)
            | Q(internal_order__icontains=search)
            | Q(sap_cost_center__icontains=search)
        )

    date_from = request.GET.get('date_from')
    if date_from:
        queryset = queryset.filter(invoice_date__gte=date_from)

    date_to = request.GET.get('date_to')
    if date_to:
        queryset = queryset.filter(invoice_date__lte=date_to)

    queryset = queryset.order_by('-invoice_date', '-id')
    return export_invoice_infos_to_excel(queryset)


def _preview_attachment_list(dispatch):
    """Build the auto-attachment manifest for the compose preview only.

    The compose template renders ``label`` and ``name`` and never reads the
    bytes, so the generated documents are discarded as soon as the manifest is
    back - they exist only to make the names known. A preview that fails is an
    empty list: the draft itself is never at risk.
    """
    try:
        with generated_document_dir('dispatch-preview') as output_dir:
            return collect_email_attachments(dispatch, output_dir=output_dir)
    except Exception:
        return []


@login_required
def email_dispatch_compose_view(request, quotation_pk=None):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    quotation = None
    preview_files = []
    preview_dispatch = None

    if quotation_pk is not None:
        from quotations.models import Quotation

        quotation = get_object_or_404(Quotation, pk=quotation_pk)

    existing_draft = None
    if quotation is not None and quotation.source_email_message_id:
        existing_draft = quotation.email_dispatches.filter(
            source_email_message=quotation.source_email_message,
            status=EmailDispatch.DispatchStatus.DRAFT,
        ).order_by('-updated_at').first()

    if request.method == 'POST':
        form = EmailDispatchForm(request.POST, quotation=quotation, instance=existing_draft)
        if form.is_valid():
            action = request.POST.get('action', 'preview')
            draft_dispatch = form.save(commit=False)
            if not draft_dispatch.created_by_id:
                draft_dispatch.created_by = request.user
            if quotation is not None and quotation.source_email_message_id and not draft_dispatch.source_email_message_id:
                draft_dispatch.source_email_message = quotation.source_email_message
                draft_dispatch.reply_message_id = quotation.source_email_message.message_id or ''
                draft_dispatch.reply_references = quotation.source_email_message.message_id or ''

            if action == 'send':
                draft_dispatch.status = EmailDispatch.DispatchStatus.DRAFT
                draft_dispatch.save()
                try:
                    send_email_dispatch(draft_dispatch)
                except Exception as exc:
                    draft_dispatch.status = EmailDispatch.DispatchStatus.FAILED
                    draft_dispatch.save(update_fields=['status', 'updated_at'])
                    messages.error(request, f'Email send failed: {exc}')
                else:
                    messages.success(request, 'Email sent successfully.')
                    return redirect('invoices:email_dispatch_list')
            else:
                preview_dispatch = draft_dispatch
                preview_files = _preview_attachment_list(draft_dispatch)
    else:
        form = EmailDispatchForm(quotation=quotation, instance=existing_draft)
        if existing_draft is not None:
            preview_dispatch = existing_draft
            preview_files = _preview_attachment_list(existing_draft)

    return render(
        request,
        'invoices/email_dispatch_compose.html',
        {
            'form': form,
            'quotation': quotation,
            'preview_files': preview_files,
            'preview_dispatch': preview_dispatch,
        },
    )


@login_required
def email_dispatch_mark_client_confirmed_view(request, pk):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    if request.method != 'POST':
        messages.warning(request, 'Invalid request method.')
        return redirect('invoices:email_dispatch_list')

    dispatch = get_object_or_404(EmailDispatch, pk=pk)
    if dispatch.status == EmailDispatch.DispatchStatus.SENT:
        dispatch.status = EmailDispatch.DispatchStatus.CLIENT_CONFIRMED
        dispatch.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Dispatch marked as client confirmed.')
    else:
        messages.warning(request, 'Dispatch must be in sent state before client confirmation.')

    return redirect('invoices:email_dispatch_list')


@login_required
def email_dispatch_mark_esker_view(request, pk):
    if not request.user.can_manage_orders():
        messages.error(request, 'You do not have access to Order Management.')
        return redirect('dashboard:dashboard')
    if request.method != 'POST':
        messages.warning(request, 'Invalid request method.')
        return redirect('invoices:email_dispatch_list')

    dispatch = get_object_or_404(EmailDispatch, pk=pk)
    if dispatch.status != EmailDispatch.DispatchStatus.CLIENT_CONFIRMED:
        messages.warning(request, 'Dispatch must be client confirmed before Esker forwarding.')
        return redirect('invoices:email_dispatch_list')

    dispatch.esker_sent = True
    dispatch.esker_sent_at = timezone.now()
    dispatch.status = EmailDispatch.DispatchStatus.ESKER_FORWARDED
    dispatch.save(update_fields=['esker_sent', 'esker_sent_at', 'status', 'updated_at'])
    messages.success(request, 'Dispatch marked as forwarded to Esker.')
    return redirect('invoices:email_dispatch_list')
