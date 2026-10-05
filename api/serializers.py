"""
Serializers for Waypost REST API.
Provides serialization for assets, categories, brands, companies, and other models.
"""
from rest_framework import serializers
from assets.models import (
    Asset, AssetCategory, AssetBrand, AssetModel,
    AssetAssignment, AssetMaintenance
)
from companies.models import Company, Division, Location, CompanyUser
from accounts.models import User


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model."""

    admin_role = serializers.CharField(read_only=True)
    admin_roles = serializers.SerializerMethodField()
    english_name = serializers.CharField(source='get_full_name', read_only=True)
    service_city_names = serializers.SerializerMethodField()

    def get_admin_roles(self, obj):
        return obj.get_admin_role_codes()

    def get_service_city_names(self, obj):
        return [c.name_zh or c.name_en for c in obj.service_cities.all()]

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'chinese_name',
            'english_name', 'admin_role', 'admin_roles', 'phone_number', 'wechat_id',
            'department', 'job_title', 'two_factor_enabled', 'language_preference',
            'profile_image', 'service_cities', 'service_city_names',
        ]
        read_only_fields = ['id', 'two_factor_enabled', 'service_cities']


class CompanySerializer(serializers.ModelSerializer):
    """Serializer for Company model."""

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'code', 'description', 'phone_number',
            'email', 'website', 'status', 'asset_prefix',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DivisionSerializer(serializers.ModelSerializer):
    """Serializer for Division model."""
    company_name = serializers.CharField(source='company.name', read_only=True)

    class Meta:
        model = Division
        fields = [
            'id', 'name', 'code', 'description', 'company',
            'company_name', 'parent_division', 'manager', 'phone_number',
            'email', 'location', 'building', 'floor', 'room',
            'status', 'budget_code', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class LocationSerializer(serializers.ModelSerializer):
    """Serializer for Location model."""
    company_name = serializers.CharField(source='company.name', read_only=True)
    division_name = serializers.CharField(source='division.name', read_only=True)

    class Meta:
        model = Location
        fields = [
            'id', 'name', 'name_en', 'code', 'code_2', 'description', 'company', 'company_name',
            'division', 'division_name', 'parent_location', 'location_type',
            'status', 'area_size', 'capacity', 'address_line1', 'address_line2',
            'city', 'state_province', 'postal_code', 'country', 'chinese_address',
            'contact', 'manager', 'phone_number', 'email', 'latitude', 'longitude',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssetCategorySerializer(serializers.ModelSerializer):
    """Serializer for AssetCategory model."""

    class Meta:
        model = AssetCategory
        fields = [
            'id', 'name', 'code', 'description', 'parent',
            'requires_serial_number', 'default_warranty_months',
            'depreciation_rate', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssetBrandSerializer(serializers.ModelSerializer):
    """Serializer for AssetBrand model."""

    class Meta:
        model = AssetBrand
        fields = [
            'id', 'name', 'code', 'description', 'website',
            'support_email', 'support_phone', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssetModelSerializer(serializers.ModelSerializer):
    """Serializer for AssetModel model."""
    brand_name = serializers.CharField(source='brand.name', read_only=True)

    class Meta:
        model = AssetModel
        fields = [
            'id', 'name', 'model_number', 'description',
            'specifications', 'brand', 'brand_name', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssetAssignmentSerializer(serializers.ModelSerializer):
    """Serializer for AssetAssignment model."""
    asset_number = serializers.CharField(source='asset.asset_number', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    assigned_by_name = serializers.CharField(source='assigned_by.get_full_name', read_only=True)

    class Meta:
        model = AssetAssignment
        fields = [
            'id', 'asset', 'asset_number', 'assigned_to', 'assigned_to_name',
            'location', 'location_name', 'assignment_type', 'assigned_by',
            'assigned_by_name', 'assigned_date', 'expected_return_date',
            'returned_date', 'returned_by', 'assignment_condition',
            'return_condition', 'notes', 'return_notes'
        ]
        read_only_fields = ['id', 'assigned_date']


class AssetMaintenanceSerializer(serializers.ModelSerializer):
    """Serializer for AssetMaintenance model."""
    asset_number = serializers.CharField(source='asset.asset_number', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)

    class Meta:
        model = AssetMaintenance
        fields = [
            'id', 'asset', 'asset_number', 'maintenance_type', 'status',
            'title', 'description', 'scheduled_date', 'started_date',
            'completed_date', 'assigned_to', 'assigned_to_name', 'vendor',
            'estimated_cost', 'actual_cost', 'work_performed', 'parts_replaced',
            'next_maintenance_date', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssetSerializer(serializers.ModelSerializer):
    """
    Serializer for Asset model.
    Includes read-only fields for related object names.
    """
    category_name = serializers.CharField(source='category.name', read_only=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    model_name = serializers.CharField(source='model.name', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    division_name = serializers.CharField(source='division.name', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    location_name = serializers.CharField(source='location.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    class Meta:
        model = Asset
        fields = [
            'id', 'asset_number', 'description', 'category', 'category_name',
            'brand', 'brand_name', 'model', 'model_name',
            'serial_number', 'barcode', 'company', 'company_name',
            'division', 'division_name', 'assigned_to', 'assigned_to_name',
            'location', 'location_name', 'current_location',
            'status', 'condition', 'purchase_price', 'current_value',
            'purchase_date', 'warranty_start_date', 'warranty_end_date',
            'warranty_provider', 'photo', 'notes',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'last_audit_date'
        ]
        read_only_fields = [
            'id', 'asset_number', 'created_at', 'updated_at', 'created_by'
        ]

    def get_assigned_to_name(self, obj):
        if obj.assigned_to:
            return obj.assigned_to.get_full_name() or obj.assigned_to.username
        return None


class AssetListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for Asset list views.
    Includes minimal fields for performance.
    """
    category_name = serializers.CharField(source='category.name', read_only=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    condition_display = serializers.CharField(source='get_condition_display', read_only=True)

    class Meta:
        model = Asset
        fields = [
            'id', 'asset_number', 'serial_number', 'barcode',
            'category_name', 'brand_name', 'status', 'status_display',
            'condition', 'condition_display', 'company',
            'assigned_to', 'location'
        ]
