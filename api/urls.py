"""
URL configuration for Waypost REST API.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    UserViewSet, CompanyViewSet, DivisionViewSet, LocationViewSet,
    AssetCategoryViewSet, AssetBrandViewSet, AssetModelViewSet,
    AssetViewSet, AssetAssignmentViewSet, AssetMaintenanceViewSet
)
from .views_auth import (
    WeChatBindView, WeChatLoginView, WeChatLookupView, WeChatProfileCompleteView,
)
from .inspection_views import StoreInspectionViewSet

# Create router and register viewsets
router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'companies', CompanyViewSet, basename='company')
router.register(r'divisions', DivisionViewSet, basename='division')
router.register(r'locations', LocationViewSet, basename='location')
router.register(r'categories', AssetCategoryViewSet, basename='category')
router.register(r'brands', AssetBrandViewSet, basename='brand')
router.register(r'models', AssetModelViewSet, basename='model')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'assignments', AssetAssignmentViewSet, basename='assignment')
router.register(r'maintenance', AssetMaintenanceViewSet, basename='maintenance')
router.register(r'inspections', StoreInspectionViewSet, basename='inspection')

urlpatterns = [
    # WeChat mini-program authentication (JWT access/refresh)
    path('auth/wechat/lookup/', WeChatLookupView.as_view(), name='wechat-lookup'),
    path('auth/wechat/bind/', WeChatBindView.as_view(), name='wechat-bind'),
    path('auth/wechat/login/', WeChatLoginView.as_view(), name='wechat-login'),
    path('auth/wechat/profile/', WeChatProfileCompleteView.as_view(), name='wechat-profile'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),

    path('', include(router.urls)),
]
