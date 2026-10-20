"""
ViewSets for the Kering store-inspection mini-program API.

Endpoints (mounted under /api/v1/):
- GET    /inspections/                     assigned store inspections
- GET    /inspections/{id}/                detail + devices + issues (offline payload)
- PATCH  /inspections/{id}/                store-level fields (times, wifi, rating, counts)
- GET    /inspections/checklist/           Kering checklist + per-category capture fields
- GET    /inspections/{id}/devices/        device list
- POST   /inspections/{id}/devices/        idempotent device upsert (multipart w/ photos)
- POST   /inspections/{id}/photos/         rack/network/issue photos
- GET|POST /inspections/{id}/issues/       issue list
- POST   /inspections/{id}/signoff/        signatures -> submitted
- POST   /inspections/{id}/report/         generate xlsx + photo zip

Device/photo writes are idempotent via client_device_uid / client_photo_uid so the
offline-first client can safely re-sync.
"""
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from inspections.constants import (
    CATEGORY_CAPTURE_FIELDS,
    CATEGORY_REQUIRED_FIELDS,
    CONFIRMATION_COUNT_LABELS,
    DEFAULT_CAPTURE_FIELDS,
    DEFAULT_REQUIRED_FIELDS,
    FIELD_OPTIONS,
    KERING_CHECKLIST,
)
from inspections.models import (
    InspectionDevice,
    InspectionIssue,
    InspectionPhoto,
    InspectionSignoffLog,
    InspectionWifiWeakPoint,
    StoreInspection,
)

from .inspection_serializers import (
    InspectionDeviceSerializer,
    InspectionIssueSerializer,
    InspectionPhotoSerializer,
    InspectionWifiWeakPointSerializer,
    StoreInspectionDetailSerializer,
    StoreInspectionSerializer,
)

# Reading fields the client may submit on a device upsert.
DEVICE_READING_FIELDS = (
    'category', 'brand_model', 'sn', 'asset_id_text', 'usage', 'status',
    'ip_address', 'cpu', 'memory', 'hdd', 'windows_version', 'ios_version',
    'is_company_phone', 'user_email',
    'drive_c_free_space', 'intact_asset_tag', 'comment',
)
# File parts accepted on a device upsert, mapped to photo kinds.
DEVICE_PHOTO_PARTS = {'overall_photo': InspectionPhoto.Kind.OVERALL, 'serial_photo': InspectionPhoto.Kind.SERIAL}


def _reject_oversized_uploads(request):
    """Return a 400 Response if any uploaded file exceeds MAX_UPLOAD_SIZE, else None.

    Server-side upload cap for photo/signature submissions (spec: "server-side
    upload size caps"). Enforced per file so a single oversized image is rejected
    with a clear message instead of exhausting storage/bandwidth; the Nginx
    ``client_max_body_size`` cap is the outer backstop. No-op when MAX_UPLOAD_SIZE
    is not configured.
    """
    cap = getattr(settings, 'MAX_UPLOAD_SIZE', 0)
    if not cap:
        return None
    oversized = []
    for name in request.FILES:
        for upload in request.FILES.getlist(name):
            if upload.size > cap and name not in oversized:
                oversized.append(name)
    if not oversized:
        return None
    return Response(
        {'error': 'Uploaded file(s) exceed the %dMB limit: %s.' % (cap // (1024 * 1024), ', '.join(oversized))},
        status=status.HTTP_400_BAD_REQUEST,
    )


class CanAccessInspection(BasePermission):
    """Object-level guard: engineers see only their own inspections; staff see all."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superadmin() or user.is_it_administrator():
            return True
        if user.is_inspection_engineer():
            return obj.engineer_id == user.id
        return False


class StoreInspectionViewSet(viewsets.ModelViewSet):
    """Primary entry point for the mini program's field workflow."""

    queryset = StoreInspection.objects.all()
    serializer_class = StoreInspectionSerializer
    permission_classes = [IsAuthenticated, CanAccessInspection]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return StoreInspection.objects.none()
        return user.get_assigned_inspections().select_related(
            'company', 'division', 'location', 'engineer'
        ).annotate(
            device_total=Count('devices', distinct=True),
            device_collected=Count('devices', filter=Q(devices__collected_at__isnull=False), distinct=True),
        )

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return StoreInspectionDetailSerializer
        return StoreInspectionSerializer

    def _ensure_writable(self, inspection):
        """Return an error Response if the inspection cannot be modified, else None."""
        user = self.request.user
        if not user.can_run_inspection(inspection):
            return Response({'error': 'Not permitted for this inspection.'}, status=status.HTTP_403_FORBIDDEN)
        if inspection.status == StoreInspection.Status.SUBMITTED:
            return Response({'error': 'Inspection already submitted.'}, status=status.HTTP_409_CONFLICT)
        return None

    @staticmethod
    def _upsert_photo(inspection, device, kind, image_file, client_photo_uid, uploaded_by, sort_index=0):
        """Create/refresh a photo row, deduping on the client-supplied uid."""
        defaults = {
            'device': device,
            'kind': kind,
            'image': image_file,
            'uploaded_by': uploaded_by,
            'captured_at': timezone.now(),
        }
        if sort_index:
            defaults['sort_index'] = sort_index
        if client_photo_uid:
            photo, _ = InspectionPhoto.objects.update_or_create(
                store_inspection=inspection, client_photo_uid=client_photo_uid, defaults=defaults
            )
        else:
            photo = InspectionPhoto.objects.create(store_inspection=inspection, **defaults)
        return photo

    @action(detail=False, methods=['get'])
    def checklist(self, request):
        """Return the Kering inspection checklist + per-category capture fields."""
        return Response({
            'checklist': KERING_CHECKLIST,
            'capture_fields': CATEGORY_CAPTURE_FIELDS,
            'default_capture_fields': DEFAULT_CAPTURE_FIELDS,
            'field_options': FIELD_OPTIONS,
            'required_fields': CATEGORY_REQUIRED_FIELDS,
            'default_required_fields': DEFAULT_REQUIRED_FIELDS,
            'confirmation_count_labels': CONFIRMATION_COUNT_LABELS,
        })

    @action(detail=True, methods=['get', 'post'], url_path='devices')
    def devices(self, request, pk=None):
        inspection = self.get_object()

        if request.method == 'GET':
            devices = inspection.devices.select_related('asset').prefetch_related('photos')
            return Response(InspectionDeviceSerializer(devices, many=True).data)

        error = self._ensure_writable(inspection)
        if error is not None:
            return error

        too_large = _reject_oversized_uploads(request)
        if too_large is not None:
            return too_large

        device, is_new = self._upsert_device(
            inspection, request, (request.data.get('client_device_uid') or '').strip()
        )
        return Response(
            InspectionDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if is_new else status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post', 'patch'], url_path=r'devices/(?P<uid>[^/.]+)')
    def device_detail(self, request, pk=None, uid=None):
        """POST|PATCH /inspections/{id}/devices/{uid}/ - idempotent upsert by uid.

        Addressing the device by its client_device_uid in the path (per the API
        contract) so the offline client can upsert a known device without resending
        the uid in the body.
        """
        inspection = self.get_object()
        error = self._ensure_writable(inspection)
        if error is not None:
            return error
        too_large = _reject_oversized_uploads(request)
        if too_large is not None:
            return too_large
        device, is_new = self._upsert_device(inspection, request, (uid or '').strip())
        return Response(
            InspectionDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if is_new else status.HTTP_200_OK,
        )

    def _upsert_device(self, inspection, request, client_device_uid):
        """Create-or-update one device by client_device_uid and attach any photos.

        Idempotent: the same uid always resolves to the same row, so re-syncing the
        offline queue never duplicates devices. Returns (device, created).
        """
        device = None
        if client_device_uid:
            device = InspectionDevice.objects.filter(
                store_inspection=inspection, client_device_uid=client_device_uid
            ).first()

        is_new = device is None
        if device is None:
            device = InspectionDevice(store_inspection=inspection)
            if client_device_uid:
                device.client_device_uid = client_device_uid

        for field in DEVICE_READING_FIELDS:
            if field in request.data:
                setattr(device, field, request.data.get(field))
        # Boolean readings may arrive as JSON bools or multipart strings; normalize
        # so a submitted "false" never stores True.
        if 'is_company_phone' in request.data:
            device.is_company_phone = request.data.get('is_company_phone') in (
                True, 'true', 'True', '1', 1,
            )
        if request.data.get('is_new_device') in (True, 'true', 'True', '1', 1):
            device.is_new_device = True
        elif is_new and not device.asset_id_text and not device.sn:
            device.is_new_device = True

        device.collected_by = request.user
        device.collected_at = timezone.now()

        # A device reported not-found during the inspection must carry a note.
        # Enforced here (not only in the serializer) because this upsert path
        # writes model fields directly; the mini program also blocks it client-side.
        if device.status == InspectionDevice.Status.NOT_IN_STORE and not (device.comment or '').strip():
            raise ValidationError({
                'comment': 'A note is mandatory when the device was not found during the inspection.'
            })

        device.save()

        # Attach any submitted photos (idempotent per client_photo_uid).
        for part_name, kind in DEVICE_PHOTO_PARTS.items():
            image_file = request.FILES.get(part_name)
            if image_file is None:
                continue
            photo_uid = (request.data.get(f'{part_name}_uid') or '').strip()
            sort_index = int(request.data.get(f'{part_name}_index') or 0)
            self._upsert_photo(inspection, device, kind, image_file, photo_uid, request.user, sort_index)

        return device, is_new

    @action(detail=True, methods=['post'], url_path='photos')
    def photos(self, request, pk=None):
        """Upload rack/network/issue/cash-drawer photos (not tied to a device)."""
        inspection = self.get_object()
        error = self._ensure_writable(inspection)
        if error is not None:
            return error

        too_large = _reject_oversized_uploads(request)
        if too_large is not None:
            return too_large

        kind = request.data.get('kind')
        image_file = request.FILES.get('image')
        valid_kinds = {choice.value for choice in InspectionPhoto.Kind}
        if kind not in valid_kinds:
            return Response({'error': 'A valid photo kind is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if image_file is None:
            return Response({'error': 'An image file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        device = None
        device_uid = (request.data.get('client_device_uid') or '').strip()
        if device_uid:
            device = InspectionDevice.objects.filter(
                store_inspection=inspection, client_device_uid=device_uid
            ).first()

        photo_uid = (request.data.get('client_photo_uid') or '').strip()
        sort_index = int(request.data.get('sort_index') or 0)
        photo = self._upsert_photo(inspection, device, kind, image_file, photo_uid, request.user, sort_index)
        return Response(InspectionPhotoSerializer(photo).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='issues')
    def issues(self, request, pk=None):
        inspection = self.get_object()

        if request.method == 'GET':
            return Response(InspectionIssueSerializer(inspection.issues.all(), many=True).data)

        error = self._ensure_writable(inspection)
        if error is not None:
            return error

        serializer = InspectionIssueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue = serializer.save(store_inspection=inspection)
        # Keep the cover-page "issue found" count in sync with the issue list.
        inspection.store_issue_found_count = inspection.issues.count()
        inspection.issue_to_follow_count = inspection.issues.filter(
            status=InspectionIssue.Status.TO_BE_FOLLOWED
        ).count()
        inspection.error_setting_fixed_count = inspection.issues.filter(
            status=InspectionIssue.Status.FIXED
        ).count()
        inspection.save(update_fields=[
            'store_issue_found_count', 'issue_to_follow_count', 'error_setting_fixed_count', 'updated_at',
        ])
        return Response(InspectionIssueSerializer(issue).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='start')
    def start(self, request, pk=None):
        """Mark the inspection in-progress and auto-record the arriving time.

        Idempotent: only transitions PLANNED -> IN_PROGRESS and stamps
        ``arriving_time`` when it is still empty.
        """
        inspection = self.get_object()
        user = request.user
        if not user.can_run_inspection(inspection):
            return Response({'error': 'Not permitted for this inspection.'}, status=status.HTTP_403_FORBIDDEN)
        changed = []
        if inspection.status == StoreInspection.Status.PLANNED:
            inspection.status = StoreInspection.Status.IN_PROGRESS
            changed.append('status')
        if inspection.arriving_time is None:
            inspection.arriving_time = timezone.localtime().time()
            changed.append('arriving_time')
        if changed:
            inspection.save(update_fields=changed + ['updated_at'])
        return Response(StoreInspectionSerializer(inspection).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='wifi-weak-points')
    def wifi_weak_points(self, request, pk=None):
        """CRUD-lite for WiFi weak points; recomputes ``wifi_coverage``."""
        inspection = self.get_object()

        if request.method == 'GET':
            points = inspection.wifi_weak_points.all()
            return Response(InspectionWifiWeakPointSerializer(points, many=True).data)

        if not request.user.can_run_inspection(inspection):
            return Response({'error': 'Not permitted for this inspection.'}, status=status.HTTP_403_FORBIDDEN)

        if request.method == 'DELETE':
            inspection.wifi_weak_points.all().delete()
        else:
            payload = request.data.get('weak_points')
            if payload is None:
                return Response({'error': 'weak_points list is required.'}, status=status.HTTP_400_BAD_REQUEST)
            if not isinstance(payload, list):
                # A form-encoded client can collapse the list into a single string;
                # reject it explicitly rather than iterating characters.
                return Response({'error': 'weak_points must be a list.'}, status=status.HTTP_400_BAD_REQUEST)
            inspection.wifi_weak_points.all().delete()
            for index, item in enumerate(payload):
                InspectionWifiWeakPoint.objects.create(
                    store_inspection=inspection,
                    sort_index=index,
                    location=str(item.get('location', ''))[:120],
                    description=str(item.get('description', ''))[:255],
                )
        # Auto-derive coverage: good when no weak points, otherwise weak.
        inspection.wifi_coverage = (
            StoreInspection.WifiCoverage.GOOD
            if not inspection.wifi_weak_points.exists()
            else StoreInspection.WifiCoverage.WEAK
        )
        inspection.save(update_fields=['wifi_coverage', 'updated_at'])
        return Response(
            InspectionWifiWeakPointSerializer(inspection.wifi_weak_points.all(), many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], url_path='assign-fe')
    def assign_fe(self, request, pk=None):
        """Assign (or create) a field engineer by chinese name / phone / wechat / invite code."""
        inspection = self.get_object()
        if not (request.user.is_superadmin() or request.user.is_it_administrator()):
            return Response({'error': 'Not permitted.'}, status=status.HTTP_403_FORBIDDEN)
        from accounts.models import User

        query = (request.data.get('query') or '').strip()
        create_if_missing = request.data.get('create_if_missing') in (True, 'true', 'True', '1', 1)
        fe = None
        if query:
            fe = User.objects.filter(
                Q(chinese_name__iexact=query)
                | Q(phone_number=query)
                | Q(wechat_id__iexact=query)
                | Q(invite_code__iexact=query)
            ).first()
        if fe is None and create_if_missing and query:
            fe = User.objects.create_user(
                username=f'fe_{query[:20]}'.replace(' ', '_'),
                chinese_name=query,
                phone_number=query if query.isdigit() else '',
            )
            fe.set_admin_roles([User.AdminRole.INSPECTION_ENGINEER])
        if fe is None:
            return Response({'error': 'No field engineer matched the query.'}, status=status.HTTP_404_NOT_FOUND)
        inspection.engineer = fe
        inspection.save(update_fields=['engineer', 'updated_at'])
        return Response(StoreInspectionSerializer(inspection).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='signoff')
    def signoff(self, request, pk=None):
        """Capture signatures + merged confirmation data and mark submitted.

        The mini program's former 设备数量确认 page is merged into signoff, so this
        endpoint also accepts device_counts / it_support_rating / it_support_comment
        / wifi_coverage. Leaving time is auto-recorded and the per-store report is
        generated server-side (the mini-program report page was removed).
        """
        inspection = self.get_object()
        user = request.user
        if not user.can_run_inspection(inspection):
            return Response({'error': 'Not permitted for this inspection.'}, status=status.HTTP_403_FORBIDDEN)

        too_large = _reject_oversized_uploads(request)
        if too_large is not None:
            return too_large

        store_signature = request.FILES.get('store_signature')
        engineer_signature = request.FILES.get('engineer_signature')
        if store_signature is not None:
            inspection.store_signature = store_signature
        if engineer_signature is not None:
            inspection.engineer_signature = engineer_signature

        # Merged confirmation payload (optional keys).
        if isinstance(request.data.get('device_counts'), dict):
            inspection.device_counts = request.data['device_counts']
        if request.data.get('it_support_rating'):
            inspection.it_support_rating = request.data['it_support_rating']
        if request.data.get('it_support_comment') is not None:
            inspection.it_support_comment = request.data['it_support_comment']
        if request.data.get('wifi_coverage'):
            inspection.wifi_coverage = request.data['wifi_coverage']

        inspection.status = StoreInspection.Status.SUBMITTED
        inspection.signed_at = timezone.now()
        if inspection.leaving_time is None:
            inspection.leaving_time = timezone.localtime().time()
        inspection.save()

        InspectionSignoffLog.objects.create(
            user=user,
            store_inspection=inspection,
            operation='signoff',
            description=f'Submitted store inspection signoff for {inspection}',
            metadata={'inspection_id': str(inspection.id), 'operation': 'signoff'},
        )

        # Generate the per-store report server-side now that signoff is final.
        from inspections.services.report_generator import generate_inspection_report
        try:
            generate_inspection_report(inspection)
            inspection.refresh_from_db()
        except Exception:  # report generation must not block signoff
            pass
        return Response(StoreInspectionSerializer(inspection).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='report')
    def report(self, request, pk=None):
        """Generate the per-store Excel report and return its download URL."""
        inspection = self.get_object()
        from inspections.services.report_generator import generate_inspection_report

        generate_inspection_report(inspection)
        inspection.refresh_from_db()
        return Response({
            'report_file': request.build_absolute_uri(inspection.report_file.url) if inspection.report_file else None,
            'report_generated_at': inspection.report_generated_at,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='photo-bundle')
    def photo_bundle(self, request, pk=None):
        """Stream a ZIP of this inspection's photos on demand.

        Replaces the persisted ``photo_zip`` field (retired in migration 0006):
        photos are no longer stored twice on disk. The archive is built per
        request via ``bundle_export.build_bundle`` and streamed as a FileResponse.
        """
        from inspections.services.bundle_export import photo_bundle_response

        inspection = self.get_object()
        return photo_bundle_response(inspection)
