"""
Views for Waypost REST API.
Provides ViewSets and API views for assets, categories, brands, companies, and more.
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from assets.models import (
    Asset, AssetCategory, AssetBrand, AssetModel,
    AssetAssignment, AssetMaintenance
)
from companies.models import Company, Division, Location, CompanyUser
from accounts.models import User

from .serializers import (
    AssetSerializer, AssetListSerializer,
    AssetCategorySerializer, AssetBrandSerializer, AssetModelSerializer,
    AssetAssignmentSerializer, AssetMaintenanceSerializer,
    CompanySerializer, DivisionSerializer, LocationSerializer,
    UserSerializer
)


class IsSuperadminOrReadOnly:
    """
    Custom permission to only allow superadmins to edit.
    """

    def has_permission(self, request, view):
        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True
        return request.user and request.user.is_superuser


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing users (read-only).
    Only superadmins can view all users.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['username', 'first_name', 'last_name', 'email']
    ordering_fields = ['username', 'first_name', 'last_name', 'date_joined']
    ordering = ['username']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return User.objects.all()
        # IT administrators can only see users in their company
        elif user.is_it_administrator():
            return User.objects.filter(
                Q(company=user.managed_company) |
                Q(managed_company=user.managed_company)
            ).distinct()
        # Viewers can only see themselves
        return User.objects.filter(id=user.id)


class CompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing companies.
    """
    queryset = Company.objects.all()
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'code', 'email']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return Company.objects.all()
        elif user.is_it_administrator():
            return Company.objects.filter(id=user.managed_company_id)
        elif user.is_viewer_admin():
            # Viewers can see companies through their managed locations
            return Company.objects.filter(
                locations__in=user.managed_locations.all()
            ).distinct()
        return Company.objects.none()

    def perform_create(self, serializer):
        if not self.request.user.is_superadmin():
            raise PermissionError("Only superadmins can create companies.")
        serializer.save()


class DivisionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing divisions.
    """
    queryset = Division.objects.all()
    serializer_class = DivisionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'code', 'company__name']
    ordering_fields = ['name', 'code', 'company__name', 'created_at']
    ordering = ['company__name', 'name']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return Division.objects.all()
        elif user.is_it_administrator():
            return Division.objects.filter(
                Q(company=user.managed_company) |
                Q(id__in=user.managed_divisions.all())
            ).distinct()
        elif user.is_viewer_admin():
            return Division.objects.filter(
                locations__in=user.managed_locations.all()
            ).distinct()
        return Division.objects.none()


class LocationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing locations.
    """
    queryset = Location.objects.all()
    serializer_class = LocationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'code', 'company__name', 'city']
    ordering_fields = ['name', 'code', 'company__name', 'created_at']
    ordering = ['company__name', 'name']

    def get_queryset(self):
        user = self.request.user
        company_filter = self.request.query_params.get('company')
        if user.is_superadmin():
            queryset = Location.objects.all()
        elif user.is_it_administrator():
            queryset = Location.objects.filter(
                Q(company=user.managed_company) |
                Q(division__in=user.managed_divisions.all())
            ).distinct()
        elif user.is_viewer_admin():
            queryset = user.managed_locations.all()
        else:
            queryset = Location.objects.none()

        if company_filter:
            queryset = queryset.filter(company_id=company_filter)

        return queryset


class AssetCategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing asset categories.
    """
    queryset = AssetCategory.objects.all()
    serializer_class = AssetCategorySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return AssetCategory.objects.all()
        # IT administrators and viewers can see all categories
        return AssetCategory.objects.filter(is_active=True)


class AssetBrandViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing asset brands.
    """
    queryset = AssetBrand.objects.all()
    serializer_class = AssetBrandSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return AssetBrand.objects.all()
        return AssetBrand.objects.filter(is_active=True)


class AssetModelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing asset models.
    """
    queryset = AssetModel.objects.all()
    serializer_class = AssetModelSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'model_number', 'brand__name']
    ordering_fields = ['name', 'brand__name', 'created_at']
    ordering = ['brand__name', 'name']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return AssetModel.objects.all()
        return AssetModel.objects.filter(is_active=True)


class AssetViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing assets.
    Includes filtering by status, category, brand, company, location.
    """
    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    search_fields = ['asset_number', 'serial_number', 'barcode', 'description']
    ordering_fields = [
        'asset_number', 'created_at', 'updated_at',
        'category__name', 'brand__name', 'status'
    ]
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return AssetListSerializer
        return AssetSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = user.get_accessible_assets()

        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter by category
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category_id=category)

        # Filter by brand
        brand = self.request.query_params.get('brand')
        if brand:
            queryset = queryset.filter(brand_id=brand)

        # Filter by company
        company = self.request.query_params.get('company')
        if company:
            queryset = queryset.filter(company_id=company)

        # Filter by location
        location = self.request.query_params.get('location')
        if location:
            queryset = queryset.filter(location_id=location)

        # Filter by division
        division = self.request.query_params.get('division')
        if division:
            queryset = queryset.filter(division_id=division)

        # Filter by assigned_to
        assigned_to = self.request.query_params.get('assigned_to')
        if assigned_to:
            queryset = queryset.filter(assigned_to_id=assigned_to)

        # Filter by barcode (exact match)
        barcode = self.request.query_params.get('barcode')
        if barcode:
            queryset = queryset.filter(barcode=barcode)

        # Filter by serial number (exact match)
        serial_number = self.request.query_params.get('serial_number')
        if serial_number:
            queryset = queryset.filter(serial_number=serial_number)

        return queryset.distinct()

    @action(detail=False, methods=['get'])
    def by_barcode(self, request):
        """
        Get asset by barcode.
        GET /api/v1/assets/by_barcode/?barcode=XXX
        """
        barcode = request.query_params.get('barcode')
        if not barcode:
            return Response(
                {'error': 'barcode parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = self.get_queryset()
        try:
            asset = queryset.get(barcode=barcode)
            serializer = self.get_serializer(asset)
            return Response(serializer.data)
        except Asset.DoesNotExist:
            return Response(
                {'error': 'Asset not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=False, methods=['get'])
    def by_serial(self, request):
        """
        Get asset by serial number.
        GET /api/v1/assets/by_serial/?serial=XXX
        """
        serial = request.query_params.get('serial')
        if not serial:
            return Response(
                {'error': 'serial parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = self.get_queryset()
        try:
            asset = queryset.get(serial_number=serial)
            serializer = self.get_serializer(asset)
            return Response(serializer.data)
        except Asset.DoesNotExist:
            return Response(
                {'error': 'Asset not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['get'])
    def assignments(self, request, pk=None):
        """
        Get assignment history for an asset.
        GET /api/v1/assets/{id}/assignments/
        """
        asset = self.get_object()
        assignments = asset.assignments.all().order_by('-assigned_date')
        serializer = AssetAssignmentSerializer(assignments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def maintenance(self, request, pk=None):
        """
        Get maintenance history for an asset.
        GET /api/v1/assets/{id}/maintenance/
        """
        asset = self.get_object()
        maintenance = asset.maintenance_records.all().order_by('-scheduled_date')
        serializer = AssetMaintenanceSerializer(maintenance, many=True)
        return Response(serializer.data)


class AssetAssignmentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing asset assignments.
    """
    queryset = AssetAssignment.objects.all()
    serializer_class = AssetAssignmentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['assigned_date', 'returned_date']
    ordering = ['-assigned_date']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return AssetAssignment.objects.all()
        # Get accessible assets and filter assignments
        accessible_assets = user.get_accessible_assets()
        return AssetAssignment.objects.filter(asset__in=accessible_assets)


class AssetMaintenanceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and managing asset maintenance records.
    """
    queryset = AssetMaintenance.objects.all()
    serializer_class = AssetMaintenanceSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['scheduled_date', 'completed_date', 'created_at']
    ordering = ['-scheduled_date']

    def get_queryset(self):
        user = self.request.user
        if user.is_superadmin():
            return AssetMaintenance.objects.all()
        # Get accessible assets and filter maintenance
        accessible_assets = user.get_accessible_assets()
        return AssetMaintenance.objects.filter(asset__in=accessible_assets)


class APIDocumentationView(LoginRequiredMixin, TemplateView):
    """
    API documentation page with all endpoints.
    """
    template_name = 'api/documentation.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Define API endpoints with descriptions
        context['endpoints'] = [
            {
                'category': 'Assets',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/assets/', 'description': 'List all assets with filtering and pagination', 'params': ['status', 'category', 'brand', 'company', 'location', 'barcode', 'search', 'ordering', 'page']},
                    {'method': 'POST', 'url': '/api/v1/assets/', 'description': 'Create a new asset'},
                    {'method': 'GET', 'url': '/api/v1/assets/{id}/', 'description': 'Get asset details by ID'},
                    {'method': 'PUT', 'url': '/api/v1/assets/{id}/', 'description': 'Update an asset'},
                    {'method': 'DELETE', 'url': '/api/v1/assets/{id}/', 'description': 'Delete an asset'},
                    {'method': 'GET', 'url': '/api/v1/assets/by_barcode/?barcode=XXX', 'description': 'Look up asset by barcode'},
                    {'method': 'GET', 'url': '/api/v1/assets/{id}/assignments/', 'description': 'Get assignment history for an asset'},
                    {'method': 'GET', 'url': '/api/v1/assets/{id}/maintenance/', 'description': 'Get maintenance history for an asset'},
                ]
            },
            {
                'category': 'Categories',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/categories/', 'description': 'List all asset categories'},
                    {'method': 'POST', 'url': '/api/v1/categories/', 'description': 'Create a new category'},
                    {'method': 'GET', 'url': '/api/v1/categories/{id}/', 'description': 'Get category details'},
                    {'method': 'PUT', 'url': '/api/v1/categories/{id}/', 'description': 'Update a category'},
                    {'method': 'DELETE', 'url': '/api/v1/categories/{id}/', 'description': 'Delete a category'},
                ]
            },
            {
                'category': 'Brands',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/brands/', 'description': 'List all asset brands'},
                    {'method': 'POST', 'url': '/api/v1/brands/', 'description': 'Create a new brand'},
                    {'method': 'GET', 'url': '/api/v1/brands/{id}/', 'description': 'Get brand details'},
                    {'method': 'PUT', 'url': '/api/v1/brands/{id}/', 'description': 'Update a brand'},
                    {'method': 'DELETE', 'url': '/api/v1/brands/{id}/', 'description': 'Delete a brand'},
                ]
            },
            {
                'category': 'Companies',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/companies/', 'description': 'List all companies'},
                    {'method': 'POST', 'url': '/api/v1/companies/', 'description': 'Create a new company'},
                    {'method': 'GET', 'url': '/api/v1/companies/{id}/', 'description': 'Get company details'},
                    {'method': 'PUT', 'url': '/api/v1/companies/{id}/', 'description': 'Update a company'},
                    {'method': 'DELETE', 'url': '/api/v1/companies/{id}/', 'description': 'Delete a company'},
                ]
            },
            {
                'category': 'Divisions',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/divisions/', 'description': 'List all divisions'},
                    {'method': 'POST', 'url': '/api/v1/divisions/', 'description': 'Create a new division'},
                    {'method': 'GET', 'url': '/api/v1/divisions/{id}/', 'description': 'Get division details'},
                    {'method': 'PUT', 'url': '/api/v1/divisions/{id}/', 'description': 'Update a division'},
                    {'method': 'DELETE', 'url': '/api/v1/divisions/{id}/', 'description': 'Delete a division'},
                ]
            },
            {
                'category': 'Locations',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/locations/', 'description': 'List all locations'},
                    {'method': 'POST', 'url': '/api/v1/locations/', 'description': 'Create a new location'},
                    {'method': 'GET', 'url': '/api/v1/locations/{id}/', 'description': 'Get location details'},
                    {'method': 'PUT', 'url': '/api/v1/locations/{id}/', 'description': 'Update a location'},
                    {'method': 'DELETE', 'url': '/api/v1/locations/{id}/', 'description': 'Delete a location'},
                ]
            },
            {
                'category': 'Users',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/users/', 'description': 'List all users (superadmin only)'},
                    {'method': 'GET', 'url': '/api/v1/users/{id}/', 'description': 'Get user details'},
                ]
            },
            {
                'category': 'Assignments',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/assignments/', 'description': 'List all asset assignments'},
                    {'method': 'POST', 'url': '/api/v1/assignments/', 'description': 'Create a new assignment'},
                    {'method': 'GET', 'url': '/api/v1/assignments/{id}/', 'description': 'Get assignment details'},
                    {'method': 'PUT', 'url': '/api/v1/assignments/{id}/', 'description': 'Update an assignment'},
                ]
            },
            {
                'category': 'Maintenance',
                'items': [
                    {'method': 'GET', 'url': '/api/v1/maintenance/', 'description': 'List all maintenance records'},
                    {'method': 'POST', 'url': '/api/v1/maintenance/', 'description': 'Create a new maintenance record'},
                    {'method': 'GET', 'url': '/api/v1/maintenance/{id}/', 'description': 'Get maintenance record details'},
                    {'method': 'PUT', 'url': '/api/v1/maintenance/{id}/', 'description': 'Update a maintenance record'},
                ]
            },
        ]

        context['browseable_api_url'] = '/api/v1/'

        return context
