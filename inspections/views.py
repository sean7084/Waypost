"""
Web views for the inspections module.

Complements the mini-program REST API (``api/inspection_views.py``) with a
server-rendered UI for planning, browsing, and exporting store inspections.

Access model:
- ``can_view_inspections()`` (superadmin / IT admin / inspection engineer) gates
  every view in this module.
- ``can_manage_inspections()`` (superadmin / IT admin) gates batch creation,
  schedule import, and batch editing.
- Engineers see only their own inspections via ``user.get_assigned_inspections()``.
"""
import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import (
    CreateView, DetailView, ListView, TemplateView, UpdateView,
)

from companies.models import Location
from inspections.forms import (
    AssetListExportForm,
    EngineerAssignForm,
    InspectionBatchByBrandForm,
    InspectionIssueUpdateForm,
    ScheduleImportForm,
    StoreInspectionEditForm,
    StoreInspectionFilterForm,
    WifiWeakPointFormSet,
)
from inspections.models import (
    InspectionBatch,
    InspectionDevice,
    InspectionIssue,
    InspectionSignoffLog,
    StoreInspection,
)
from inspections.scoping import (
    scoped_batches,
    scoped_inspections,
    unbatched_inspection_count,
)
from inspections.services.asset_list_export import export_asset_list
from inspections.services.kering_import import import_kering_master
from inspections.services.sample_files import SAMPLES

# FullCalendar event colors keyed by StoreInspection.Status.
_STATUS_COLORS = {
    StoreInspection.Status.PLANNED: '#6c757d',
    StoreInspection.Status.IN_PROGRESS: '#0dcaf0',
    StoreInspection.Status.COMPLETED: '#198754',
    StoreInspection.Status.SUBMITTED: '#6610f2',
}


# -- mixins ------------------------------------------------------------------
class InspectionAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Require an authenticated user with inspections-module read access."""

    def test_func(self):
        return self.request.user.can_view_inspections()


class InspectionManageMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Require an authenticated user with inspections-module write access."""

    def test_func(self):
        return self.request.user.can_manage_inspections()


def _parse_anchor(date_value, month_value):
    """Parse a dashboard anchor date from ``?date=YYYY-MM-DD`` or ``?month=YYYY-MM``.

    Returns ``None`` when neither is present or parseable so the caller can fall
    back to today.
    """
    for value in (date_value, month_value):
        text = (value or '').strip()
        if not text:
            continue
        try:
            if len(text) == 7:  # YYYY-MM
                text += '-01'
            return date.fromisoformat(text)
        except ValueError:
            continue
    return None


def _window_for(view, anchor):
    """Return the (start, end) dates of the month / week / day window at anchor."""
    if view == 'day':
        return anchor, anchor
    if view == 'week':
        start = anchor - timedelta(days=anchor.weekday())  # Monday
        return start, start + timedelta(days=6)
    start = anchor.replace(day=1)
    return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _shift_anchor(view, anchor, direction):
    """Move the anchor one whole view period forwards (+1) or backwards (-1)."""
    if view == 'day':
        return anchor + timedelta(days=direction)
    if view == 'week':
        return anchor + timedelta(days=7 * direction)
    month_index = anchor.year * 12 + anchor.month - 1 + direction
    return date(month_index // 12, month_index % 12 + 1, 1)


# -- dashboard ---------------------------------------------------------------
class InspectionDashboardView(InspectionAccessMixin, TemplateView):
    """AM/PM scheduling board over a month, week or day window."""

    template_name = 'inspections/dashboard.html'
    VIEWS = ('month', 'week', 'day')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        inspections = scoped_inspections(user)

        # Optional batch filter (query string) so the dashboard can be scoped
        # to one planning cycle from the batch detail page.
        batch_id = self.request.GET.get('batch')
        batch = None
        if batch_id:
            batch = InspectionBatch.objects.filter(id=batch_id).first()
            if batch:
                inspections = inspections.filter(batch=batch)

        # Annotate device counts once (avoids per-row COUNTs) and group into a
        # date x (AM|PM) grid for the drag-and-drop scheduler.
        inspections = inspections.annotate(
            device_total=Count('devices', distinct=True),
            device_collected=Count(
                'devices', filter=Q(devices__collected_at__isnull=False), distinct=True
            ),
        )

        # Window: ?view=month|week|day with an optional ?date (or legacy ?month)
        # anchor. A selected batch with no explicit anchor shows that batch's
        # whole planning range instead.
        today = timezone.localdate()
        requested_view = (self.request.GET.get('view') or '').strip().lower()
        anchor = _parse_anchor(self.request.GET.get('date'), self.request.GET.get('month'))
        if requested_view in self.VIEWS or anchor is not None or batch is None:
            view = requested_view if requested_view in self.VIEWS else 'month'
            anchor = anchor or today
            window_start, window_end = _window_for(view, anchor)
        else:
            view = 'batch'
            anchor = batch.start_date
            window_start, window_end = batch.start_date, batch.end_date

        in_range = inspections.filter(
            inspection_date__gte=window_start, inspection_date__lte=window_end
        )

        by_key = {}
        for inspection in in_range:
            card = {
                'id': str(inspection.id),
                'label': inspection.store_label or str(inspection),
                'brand': inspection.brand_name or (inspection.division.name if inspection.division else ''),
                'engineer': inspection.engineer.get_full_name() if inspection.engineer else '',
                'status': inspection.status,
                'status_display': inspection.get_status_display(),
                'collected': inspection.device_collected,
                'total': inspection.device_total,
                'jda': inspection.jda_code,
                'arriving': inspection.arriving_time.strftime('%H:%M') if inspection.arriving_time else '',
                'leaving': inspection.leaving_time.strftime('%H:%M') if inspection.leaving_time else '',
                'url': reverse('inspections:inspection_detail', args=[inspection.id]),
                'color': _STATUS_COLORS.get(inspection.status, '#6c757d'),
            }
            by_key.setdefault((inspection.inspection_date, inspection.slot), []).append(card)

        days = []
        cursor = window_start
        while cursor <= window_end:
            days.append({
                'date': cursor.isoformat(),
                'weekday': cursor.strftime('%a'),
                'am': by_key.get((cursor, StoreInspection.Slot.AM), []),
                'pm': by_key.get((cursor, StoreInspection.Slot.PM), []),
            })
            cursor += timedelta(days=1)

        context.update({
            'days_json': json.dumps(days),
            'view': view,
            'views': self.VIEWS,
            # Only echo back a window the user actually asked for: the batch
            # selector then keeps your place, while a bare ?batch= link (e.g.
            # from the batch detail page) still shows that batch's whole range.
            'requested_view': requested_view if requested_view in self.VIEWS else '',
            'requested_anchor': (self.request.GET.get('date')
                                 or self.request.GET.get('month') or ''),
            'batch': batch,
            'batches': scoped_batches(user).order_by('-start_date')[:50],
            'window_start': window_start,
            'window_end': window_end,
            'anchor': anchor.isoformat(),
            'prev_anchor': _shift_anchor(view, anchor, -1).isoformat() if view in self.VIEWS else '',
            'next_anchor': _shift_anchor(view, anchor, 1).isoformat() if view in self.VIEWS else '',
            'today': today.isoformat(),
            'is_current_period': window_start <= today <= window_end,
            'total_count': in_range.count(),
            'planned_count': in_range.filter(status=StoreInspection.Status.PLANNED).count(),
            'in_progress_count': in_range.filter(status=StoreInspection.Status.IN_PROGRESS).count(),
            'completed_count': in_range.filter(status=StoreInspection.Status.COMPLETED).count(),
            'submitted_count': in_range.filter(status=StoreInspection.Status.SUBMITTED).count(),
            'can_manage': user.can_manage_inspections(),
        })
        return context


class InspectionMoveView(InspectionManageMixin, View):
    """POST {inspection_id, date, slot} to rearrange a site on the AM/PM grid."""

    def post(self, request):
        import json as _json
        try:
            payload = _json.loads(request.body or b'{}')
        except ValueError:
            payload = {}
        inspection_id = payload.get('inspection_id')
        new_date = payload.get('date')
        new_slot = payload.get('slot')
        inspection = get_object_or_404(StoreInspection, pk=inspection_id)
        if new_slot not in (StoreInspection.Slot.AM, StoreInspection.Slot.PM):
            return HttpResponse(_json.dumps({'error': 'invalid slot'}), status=400,
                                content_type='application/json')
        try:
            from datetime import date as _date
            parsed = _date.fromisoformat(new_date)
        except (TypeError, ValueError):
            return HttpResponse(_json.dumps({'error': 'invalid date'}), status=400,
                                content_type='application/json')
        with transaction.atomic():
            inspection.inspection_date = parsed
            inspection.slot = new_slot
            inspection.save(update_fields=['inspection_date', 'slot', 'updated_at'])
            InspectionSignoffLog.objects.create(
                user=request.user,
                store_inspection=inspection,
                operation='reschedule',
                description=f'Rescheduled to {parsed} {new_slot.upper()}',
                metadata={'inspection_id': str(inspection.id), 'date': str(parsed), 'slot': new_slot},
            )
        return HttpResponse(_json.dumps({'ok': True}), status=200, content_type='application/json')


# -- batch list / create / detail / update -----------------------------------
class InspectionBatchListView(InspectionAccessMixin, ListView):
    model = InspectionBatch
    template_name = 'inspections/batch_list.html'
    context_object_name = 'batches'
    paginate_by = 20

    def get_queryset(self):
        user = self.request.user
        qs = scoped_batches(user).select_related(
            'company', 'division', 'engineer'
        ).annotate(
            inspection_count=Count('inspections')
        )
        search = self.request.GET.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(division__name__icontains=search)
            )
        source = self.request.GET.get('source', '').strip()
        if source:
            qs = qs.filter(source=source)
        return qs.order_by('-start_date', '-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['source'] = self.request.GET.get('source', '')
        context['source_choices'] = InspectionBatch.Source.choices
        context['can_manage'] = self.request.user.can_manage_inspections()
        # Explains an empty list: inspections imported without a planning cycle
        # are not batches, so they never show up here until they are adopted.
        context['unbatched_count'] = unbatched_inspection_count(self.request.user)
        return context


class InspectionBatchCreateView(InspectionManageMixin, TemplateView):
    """Landing page: choose between by-brand and schedule-upload flows."""

    template_name = 'inspections/batch_create.html'


class InspectionBatchCreateByBrandView(InspectionManageMixin, CreateView):
    """Create a batch + auto-spread one inspection per active store location."""

    model = InspectionBatch
    form_class = InspectionBatchByBrandForm
    template_name = 'inspections/batch_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Create Batch by Brand')
        context['submit_text'] = _('Create Batch & Schedule Stores')
        context['back_url'] = reverse('inspections:batch_create')
        context['mode'] = 'by-brand'
        return context

    def form_valid(self, form):
        user = self.request.user
        with transaction.atomic():
            batch = form.save(commit=False)
            batch.source = InspectionBatch.Source.BRAND
            batch.created_by = user
            batch.save()
            form.save_m2m()

            created = self._spread_stores(batch, user)

        messages.success(
            self.request,
            _('Batch "%(name)s" created with %(count)s store inspections.') % {
                'name': batch.name, 'count': created,
            }
        )
        return redirect('inspections:batch_detail', pk=batch.pk)

    def _spread_stores(self, batch, user):
        """Round-robin the division's active store locations across the date range."""
        locations = Location.objects.filter(
            division=batch.division,
            location_type=Location.LocationType.STORE,
            status=Location.LocationStatus.ACTIVE,
        ).order_by('code', 'name')

        total_days = (batch.end_date - batch.start_date).days + 1
        if total_days <= 0:
            total_days = 1
        dates = [batch.start_date + timedelta(days=i) for i in range(total_days)]

        created = 0
        for index, location in enumerate(locations):
            inspection_date = dates[index % len(dates)]
            store_label = ' '.join(
                part for part in [location.code, batch.division.name, location.name] if part
            ).strip()
            _inspection, was_created = StoreInspection.objects.get_or_create(
                location=location,
                inspection_date=inspection_date,
                defaults={
                    'batch': batch,
                    'company': batch.company,
                    'division': batch.division,
                    'engineer': batch.engineer,
                    'jda_code': location.code or '',
                    'brand_name': batch.division.name if batch.division else '',
                    'store_name': location.name,
                    'store_label': store_label,
                    'status': StoreInspection.Status.PLANNED,
                    'created_by': user,
                },
            )
            if was_created:
                created += 1
        return created


class ImportSampleFileView(InspectionAccessMixin, View):
    """Download a sample schedule / master asset list for the import form.

    The files are generated from the importer's own column maps (see
    ``services/sample_files.py``), so a sample always matches what the parser
    accepts instead of being a hand-maintained file that silently drifts.
    """

    def get(self, request, kind):
        generator = SAMPLES.get(kind)
        if generator is None:
            raise Http404(f'Unknown sample file: {kind}')
        content, filename = generator()
        response = HttpResponse(content, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class InspectionBatchImportView(InspectionManageMixin, View):
    """Upload schedule.xlsx (+ optional asset_list.xlsx) to create a batch."""

    template_name = 'inspections/batch_import.html'

    def get(self, request):
        form = ScheduleImportForm(user=request.user)
        return render(request, self.template_name, {
            'form': form,
            'title': _('Import Schedule'),
            'back_url': reverse('inspections:batch_create'),
        })

    def post(self, request):
        form = ScheduleImportForm(request.POST, request.FILES, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'title': _('Import Schedule'),
                'back_url': reverse('inspections:batch_create'),
            })

        company = form.cleaned_data['company']
        name = form.cleaned_data['name']
        schedule_file = form.cleaned_data['schedule_file']
        assets_file = form.cleaned_data.get('assets_file')
        engineer = form.cleaned_data.get('engineer')
        description = form.cleaned_data.get('description', '')
        auto_arrange = form.cleaned_data.get('auto_arrange', False)
        arrange_start = form.cleaned_data.get('arrange_start')
        arrange_end = form.cleaned_data.get('arrange_end')

        # Use the arrange window as the batch range when supplied, else a
        # provisional 30-day placeholder the user can edit later.
        today = timezone.localdate()
        batch = InspectionBatch.objects.create(
            name=name,
            company=company,
            division=None,
            engineer=engineer,
            start_date=arrange_start or today,
            end_date=arrange_end or (today + timedelta(days=30)),
            description=description,
            source=InspectionBatch.Source.UPLOAD,
            created_by=request.user,
        )

        try:
            result = import_kering_master(
                schedule_file,
                assets_file,
                company_name=company.name,
                company_code=company.code,
                engineer=engineer,
                batch=batch,
                user=request.user,
                dry_run=False,
                track_rollback=True,
                auto_arrange=auto_arrange,
                arrange_start=arrange_start,
                arrange_end=arrange_end,
            )
        except Exception as exc:  # pragma: no cover - surfaced to the user
            batch.delete()
            messages.error(self.request, _('Import failed: %(error)s') % {'error': exc})
            return render(request, self.template_name, {
                'form': form,
                'title': _('Import Schedule'),
                'back_url': reverse('inspections:batch_create'),
            })

        stats = result['stats']
        per_store = result['per_store']

        # Tighten the batch date range to match what was actually imported.
        imported_inspections = StoreInspection.objects.filter(batch=batch)
        if imported_inspections.exists():
            min_date = imported_inspections.order_by('inspection_date').first().inspection_date
            max_date = imported_inspections.order_by('-inspection_date').first().inspection_date
            # Infer the division from the first inspection with one (schedule may
            # mix brands; pick the most common).
            division = (
                imported_inspections.exclude(division__isnull=True)
                .values('division').annotate(n=Count('id')).order_by('-n').first()
            )
            batch.start_date = min_date or batch.start_date
            batch.end_date = max_date or batch.end_date
            if division:
                from companies.models import Division
                batch.division = Division.objects.filter(id=division['division']).first()
            batch.save(update_fields=['start_date', 'end_date', 'division', 'updated_at'])

        messages.success(
            self.request,
            _('Imported %(inspections)s inspections, %(devices)s expected devices, '
              '%(assets)s assets across %(stores)s stores.') % {
                'inspections': stats['inspections'],
                'devices': stats['devices'],
                'assets': stats['assets'],
                'stores': len(per_store),
            }
        )
        return redirect('inspections:batch_detail', pk=batch.pk)


class InspectionBatchDetailView(InspectionAccessMixin, DetailView):
    model = InspectionBatch
    template_name = 'inspections/batch_detail.html'
    context_object_name = 'batch'

    def get_queryset(self):
        return scoped_batches(self.request.user).select_related(
            'company', 'division', 'engineer', 'import_run', 'created_by'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        batch = self.object
        inspections = (
            StoreInspection.objects.filter(batch=batch)
            .select_related('location', 'division', 'engineer')
            .annotate(device_total=Count('devices'))
            .order_by('inspection_date', 'jda_code')
        )
        # Per-inspection collected count for the progress column.
        collected_map = dict(
            InspectionDevice.objects
            .filter(store_inspection__batch=batch, collected_at__isnull=False)
            .values('store_inspection_id')
            .annotate(n=Count('id'))
            .values_list('store_inspection_id', 'n')
        )
        rows = []
        for inspection in inspections:
            collected = collected_map.get(inspection.id, 0)
            total = inspection.device_total or 0
            pct = round((collected / total) * 100, 1) if total else 0
            rows.append({
                'inspection': inspection,
                'collected': collected,
                'total': total,
                'pct': pct,
            })
        context.update({
            'rows': rows,
            'inspection_count': len(rows),
            'completion_pct': batch.get_completion_percentage(),
            'can_manage': self.request.user.can_manage_inspections(),
            'export_url': f'{reverse("inspections:asset_list_export")}?batch={batch.id}',
        })
        return context


class InspectionBatchUpdateView(InspectionManageMixin, UpdateView):
    model = InspectionBatch
    form_class = InspectionBatchByBrandForm
    template_name = 'inspections/batch_form.html'

    def get_queryset(self):
        return scoped_batches(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse('inspections:batch_detail', args=[self.object.pk])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = _('Edit Batch')
        context['submit_text'] = _('Save Changes')
        context['back_url'] = reverse('inspections:batch_detail', args=[self.object.pk])
        context['mode'] = 'edit'
        return context


# -- inspection list / detail ------------------------------------------------
class StoreInspectionListView(InspectionAccessMixin, ListView):
    model = StoreInspection
    template_name = 'inspections/inspection_list.html'
    context_object_name = 'inspections'
    paginate_by = 30

    def get_queryset(self):
        qs = scoped_inspections(self.request.user)
        form = StoreInspectionFilterForm(self.request.GET, user=self.request.user)
        self.filter_form = form
        if form.is_valid():
            data = form.cleaned_data
            if data.get('batch'):
                qs = qs.filter(batch=data['batch'])
            if data.get('division'):
                qs = qs.filter(division=data['division'])
            if data.get('status'):
                qs = qs.filter(status=data['status'])
            if data.get('engineer'):
                qs = qs.filter(engineer=data['engineer'])
            if data.get('date_from'):
                qs = qs.filter(inspection_date__gte=data['date_from'])
            if data.get('date_to'):
                qs = qs.filter(inspection_date__lte=data['date_to'])
            if data.get('search'):
                needle = data['search']
                qs = qs.filter(
                    Q(store_label__icontains=needle)
                    | Q(jda_code__icontains=needle)
                    | Q(store_name__icontains=needle)
                    | Q(brand_name__icontains=needle)
                )
        return qs.order_by('-inspection_date', 'jda_code')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = self.filter_form
        context['can_manage'] = self.request.user.can_manage_inspections()
        return context


class StoreInspectionDetailView(InspectionAccessMixin, DetailView):
    model = StoreInspection
    template_name = 'inspections/inspection_detail.html'
    context_object_name = 'inspection'

    def get_queryset(self):
        return scoped_inspections(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        inspection = self.object
        devices = (
            inspection.devices.select_related('asset', 'collected_by')
            .prefetch_related('photos').order_by('row_order', 'created_at')
        )
        issues = inspection.issues.all().order_by('seq', 'created_at')
        photos = inspection.photos.filter(device__isnull=True).order_by('kind', 'sort_index')
        context.update({
            'devices': devices,
            'issues': issues,
            'store_photos': photos,
            'device_total': devices.count(),
            'device_collected': devices.filter(collected_at__isnull=False).count(),
            'completion_pct': inspection.get_completion_percentage(),
            'can_manage': self.request.user.can_manage_inspections(),
            'issue_status_choices': InspectionIssue.Status.choices,
            'wifi_weak_points': inspection.wifi_weak_points.all(),
        })
        return context


# -- asset list export -------------------------------------------------------
class AssetListExportView(InspectionAccessMixin, View):
    """GET renders the filter form; POST streams the xlsx download."""

    template_name = 'inspections/export_form.html'

    def get(self, request):
        form = AssetListExportForm(request.GET or None, user=request.user)
        # Pre-fill batch from ?batch=<id> so the batch-detail "Export" button
        # lands here with the batch already selected.
        batch_id = request.GET.get('batch')
        if batch_id and not form.is_bound:
            batch = InspectionBatch.objects.filter(id=batch_id).first()
            if batch:
                form = AssetListExportForm(initial={'batch': batch}, user=request.user)
        return render(request, self.template_name, {
            'form': form,
            'title': _('Export'),
        })

    def post(self, request):
        form = AssetListExportForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {
                'form': form,
                'title': _('Export'),
            })

        inspections = list(form.resolve_inspections())
        if not inspections:
            messages.warning(request, _('No inspections match the selected filters.'))
            return render(request, self.template_name, {
                'form': form,
                'title': _('Export'),
            })

        batch = form.cleaned_data.get('batch')
        include_asset_list = form.cleaned_data.get('include_asset_list', True)
        include_photos = form.cleaned_data.get('include_photos', False)
        include_reports = form.cleaned_data.get('include_reports', False)

        # Photos/reports require the ZIP bundle; asset-list-only stays a plain xlsx.
        if include_photos or include_reports:
            from inspections.services.bundle_export import bundle_as_file_response
            return bundle_as_file_response(
                inspections, batch=batch,
                include_asset_list=include_asset_list,
                include_photos=include_photos,
                include_reports=include_reports,
            )

        content, filename = export_asset_list(inspections, batch=batch)

        response = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


# -- backend editing of onsite-captured data ---------------------------------
class StoreInspectionUpdateView(InspectionManageMixin, UpdateView):
    """Edit arriving/leaving/wifi/IT-rating/notes + WiFi weak points from the backend."""

    model = StoreInspection
    form_class = StoreInspectionEditForm
    template_name = 'inspections/inspection_edit.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['weakpoint_formset'] = WifiWeakPointFormSet(
                self.request.POST, instance=self.object
            )
        else:
            context['weakpoint_formset'] = WifiWeakPointFormSet(instance=self.object)
        context['title'] = _('Edit Inspection')
        # The template addresses the record as ``inspection`` (UpdateView only
        # supplies ``object`` / ``storeinspection``).
        context['inspection'] = self.object
        context['back_url'] = reverse('inspections:inspection_detail', args=[self.object.pk])
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        formset = context['weakpoint_formset']
        if not formset.is_valid():
            return self.render_to_response(context)
        with transaction.atomic():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
            # Re-derive coverage from weak points after backend edits.
            self.object.wifi_coverage = (
                StoreInspection.WifiCoverage.GOOD
                if not self.object.wifi_weak_points.exists()
                else StoreInspection.WifiCoverage.WEAK
            )
            self.object.save(update_fields=['wifi_coverage', 'updated_at'])
        messages.success(self.request, _('Inspection updated.'))
        return redirect('inspections:inspection_detail', pk=self.object.pk)


class InspectionIssueUpdateView(InspectionManageMixin, View):
    """Inline backend edit of an issue's description/status (incl. escalated)."""

    def post(self, request, pk):
        issue = get_object_or_404(InspectionIssue, pk=pk)
        form = InspectionIssueUpdateForm(request.POST, instance=issue)
        if form.is_valid():
            form.save()
            messages.success(request, _('Issue updated.'))
        else:
            messages.error(request, '; '.join(f'{k}: {v[0]}' for k, v in form.errors.items()))
        return redirect('inspections:inspection_detail', pk=issue.store_inspection_id)


class EngineerAssignView(InspectionManageMixin, View):
    """Assign (or create) a field engineer by chinese name / phone / wechat / invite."""

    def post(self, request, pk):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        inspection = get_object_or_404(StoreInspection, pk=pk)
        form = EngineerAssignForm(request.POST)
        if not form.is_valid():
            messages.error(request, _('Enter a field-engineer identifier.'))
            return redirect('inspections:inspection_detail', pk=pk)
        query = form.cleaned_data['query']
        fe = User.objects.filter(
            Q(chinese_name__iexact=query)
            | Q(phone_number=query)
            | Q(wechat_id__iexact=query)
            | Q(invite_code__iexact=query)
        ).first()
        if fe is None and form.cleaned_data['create_if_missing']:
            fe = User.objects.create_user(
                username=f'fe_{query[:20]}'.replace(' ', '_'),
                chinese_name=query,
                phone_number=query if query.isdigit() else '',
            )
            fe.set_admin_roles([User.AdminRole.INSPECTION_ENGINEER])
        if fe is None:
            messages.error(request, _('No field engineer matched "%(q)s".') % {'q': query})
            return redirect('inspections:inspection_detail', pk=pk)
        inspection.engineer = fe
        inspection.save(update_fields=['engineer', 'updated_at'])
        messages.success(request, _('Assigned %(fe)s.') % {'fe': fe.get_display_name()})
        return redirect('inspections:inspection_detail', pk=pk)


# -- review (batch status review) --------------------------------------------
class InspectionReviewView(InspectionAccessMixin, TemplateView):
    """Batch-review asset/device status per batch / week / month / custom range."""

    template_name = 'inspections/review.html'

    def _resolve_range(self, request):
        period = request.GET.get('period', 'month')
        today = timezone.localdate()
        if period == 'week':
            iso = today.isocalendar()
            start = date.fromisocalendar(iso[0], iso[1], 1)
            end = start + timedelta(days=6)
        elif period == 'custom':
            start = request.GET.get('date_from') or today.replace(day=1)
            end = request.GET.get('date_to') or today
            if isinstance(start, str):
                start = date.fromisoformat(start)
            if isinstance(end, str):
                end = date.fromisoformat(end)
        else:  # month
            start = today.replace(day=1)
            end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return period, start, end

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        qs = scoped_inspections(user)

        period, start, end = self._resolve_range(self.request)
        batch_id = self.request.GET.get('batch')
        batch = None
        if batch_id:
            batch = InspectionBatch.objects.filter(id=batch_id).first()
            if batch:
                qs = qs.filter(batch=batch)
                start, end = batch.start_date, batch.end_date
        qs = qs.filter(inspection_date__gte=start, inspection_date__lte=end)

        qs = qs.annotate(
            expected=Count('devices', filter=Q(devices__is_new_device=False), distinct=True),
            collected=Count('devices', filter=Q(devices__collected_at__isnull=False), distinct=True),
            new_found=Count('devices', filter=Q(devices__is_new_device=True), distinct=True),
            not_found=Count('devices', filter=Q(devices__status='not_in_store'), distinct=True),
            issue_count=Count('issues', distinct=True),
        ).order_by('inspection_date', 'jda_code')

        rows = [
            {
                'inspection': i,
                'expected': i.expected,
                'collected': i.collected,
                'new_found': i.new_found,
                'not_found': i.not_found,
                'issue_count': i.issue_count,
                'pct': round((i.collected / i.expected) * 100, 1) if i.expected else 0,
            }
            for i in qs
        ]
        context.update({
            'rows': rows,
            'period': period,
            'start': start,
            'end': end,
            'batch': batch,
            'batches': scoped_batches(user).order_by('-start_date')[:50],
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'can_manage': user.can_manage_inspections(),
        })
        return context


class InspectionReviewBulkUpdateView(InspectionManageMixin, View):
    """Bulk-set device status from the review page.

    Accepts explicit ``device_ids`` and/or ``inspection_ids``. For inspections the
    update targets their *outstanding* expected devices (no onsite reading yet) -
    the common review action for sites that were never visited. Everything is
    scoped to the inspections the requester may see.
    """

    def post(self, request):
        new_status = request.POST.get('status')
        back = request.META.get('HTTP_REFERER') or reverse('inspections:review')
        valid = {value for value, _label in InspectionDevice.Status.choices}
        if new_status not in valid:
            messages.error(request, _('Invalid status.'))
            return redirect(back)

        device_ids = request.POST.getlist('device_ids')
        inspection_ids = request.POST.getlist('inspection_ids')
        query = Q()
        if device_ids:
            query |= Q(id__in=device_ids)
        if inspection_ids:
            query |= Q(
                store_inspection_id__in=inspection_ids,
                is_new_device=False,
                collected_at__isnull=True,
            )
        if not query:
            messages.error(request, _('Select at least one site or device.'))
            return redirect(back)

        devices = InspectionDevice.objects.filter(query).filter(
            store_inspection__in=scoped_inspections(request.user)
        )
        updated = devices.update(status=new_status)
        if updated and new_status == InspectionDevice.Status.NOT_IN_STORE:
            # Preserve the mandatory-note invariant for not-found devices.
            devices.filter(comment='').update(
                comment=_('Marked not found during batch review.')
            )
        messages.success(request, _('Updated %(n)s device(s).') % {'n': updated})
        return redirect(back)
