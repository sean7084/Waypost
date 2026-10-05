"""
Admin configuration for Accounts app.
Configures Django admin interface for User and UserSession models.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from .models import AdminRole, ServiceCity, User, UserSession


@admin.register(AdminRole)
class AdminRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'description')
    ordering = ('name',)


@admin.register(ServiceCity)
class ServiceCityAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'name_zh', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name_en', 'name_zh')
    ordering = ('name_en',)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Custom admin interface for the User model.
    Extends Django's built-in UserAdmin to support additional fields and admin roles.
    """
    list_display = ('username', 'email', 'first_name', 'last_name', 'chinese_name', 'admin_roles_display', 'two_factor_enabled', 'is_active', 'date_joined')
    list_filter = ('roles', 'service_cities', 'two_factor_enabled', 'is_active', 'is_staff', 'is_superuser', 'date_joined', 'language_preference')
    search_fields = ('username', 'first_name', 'last_name', 'chinese_name', 'email', 'phone_number', 'employee_id')
    ordering = ('username',)
    filter_horizontal = ('roles', 'groups', 'user_permissions', 'managed_divisions', 'managed_locations', 'service_cities')
    
    # Define fieldsets for the admin form
    fieldsets = (
        (None, {
            'fields': ('username', 'password')
        }),
        (_('Personal info'), {
            'fields': ('first_name', 'last_name', 'chinese_name', 'email', 'phone_number', 'profile_image', 'employee_id')
        }),
        (_('Administrator Role System'), {
            'fields': ('roles', 'managed_company', 'managed_divisions', 'managed_locations'),
            'description': _('Configure administrator access levels and scope')
        }),
        (_('Field Engineer (Mini Program)'), {
            'fields': ('service_cities',),
            'classes': ('collapse',),
            'description': _('Cities/regions this field engineer covers; used by the WeChat mini program.')
        }),
        (_('Company Association'), {
            'fields': ('company', 'division'),
            'classes': ('collapse',),
            'description': _('Associate user with company and division')
        }),
        (_('Work Information'), {
            'fields': ('department', 'job_title', 'manager'),
            'classes': ('collapse',)
        }),
        (_('Preferences'), {
            'fields': ('language_preference', 'timezone')
        }),
        (_('Security'), {
            'fields': ('two_factor_enabled',)
        }),
        (_('Permissions'), {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        (_('Important dates'), {
            'fields': ('last_login', 'date_joined')
        }),
    )
    
    # Define fieldsets for adding new users
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2', 'roles', 'chinese_name', 'service_cities'),
        }),
    )
    
    readonly_fields = ('date_joined', 'last_login')
    
    def get_queryset(self, request):
        """Optimize queryset with related objects."""
        return super().get_queryset(request).select_related(
            'company', 'division', 'managed_company', 'manager'
        ).prefetch_related('roles', 'managed_divisions', 'managed_locations', 'service_cities')

    def admin_roles_display(self, obj):
        return obj.get_admin_roles_display()
    admin_roles_display.short_description = _('Administrator Roles')
    
    def get_access_scope(self, obj):
        """Display the user's access scope."""
        return obj.get_access_scope_display()
    get_access_scope.short_description = _('Access Scope')
    
    def get_managed_divisions_display(self, obj):
        """Display managed divisions."""
        divisions = obj.managed_divisions.all()
        if divisions.count() == 0:
            return "-"
        elif divisions.count() <= 3:
            return ", ".join([d.name for d in divisions])
        else:
            return f"{divisions.count()} divisions"
    get_managed_divisions_display.short_description = _('Managed Divisions')
    
    def get_managed_locations_display(self, obj):
        """Display managed locations."""
        locations = obj.managed_locations.all()
        if locations.count() == 0:
            return "-"
        elif locations.count() <= 3:
            return ", ".join([l.name for l in locations])
        else:
            return f"{locations.count()} locations"
    get_managed_locations_display.short_description = _('Managed Locations')
    
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Customize foreign key fields."""
        if db_field.name == "managed_company":
            # Only show companies for IT administrator role
            if hasattr(request, '_editing_user'):
                user = request._editing_user
                if user and not user.has_admin_role(User.AdminRole.IT_ADMINISTRATOR):
                    kwargs["queryset"] = kwargs["queryset"].none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """Customize many-to-many fields."""
        if db_field.name == "managed_divisions":
            # Only show divisions for IT administrator role
            if hasattr(request, '_editing_user'):
                user = request._editing_user
                if user and not user.has_admin_role(User.AdminRole.IT_ADMINISTRATOR):
                    kwargs["queryset"] = kwargs["queryset"].none()
        elif db_field.name == "managed_locations":
            # Only show locations for viewer role
            if hasattr(request, '_editing_user'):
                user = request._editing_user
                if user and not user.has_admin_role(User.AdminRole.VIEWER):
                    kwargs["queryset"] = kwargs["queryset"].none()
        return super().formfield_for_manytomany(db_field, request, **kwargs)
    
    def get_form(self, request, obj=None, **kwargs):
        """Customize form based on admin role."""
        request._editing_user = obj
        return super().get_form(request, obj, **kwargs)
    
    class Media:
        js = ('admin/js/admin_roles.js',)
        css = {
            'all': ('admin/css/admin_roles.css',)
        }


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    """
    Admin interface for UserSession model.
    """
    list_display = ('user', 'ip_address', 'created_at', 'last_activity', 'is_active')
    list_filter = ('is_active', 'created_at', 'last_activity')
    search_fields = ('user__username', 'user__email', 'ip_address', 'session_key')
    ordering = ('-created_at',)
    readonly_fields = ('session_key', 'created_at', 'last_activity')
    
    fieldsets = (
        (_('Session Info'), {
            'fields': ('user', 'session_key', 'ip_address', 'user_agent')
        }),
        (_('Status'), {
            'fields': ('is_active',)
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'last_activity')
        }),
    )


# Configure admin site headers
admin.site.site_header = _('Waypost Administration')
admin.site.site_title = _('Waypost Admin')
admin.site.index_title = _('Welcome to Waypost Administration')
