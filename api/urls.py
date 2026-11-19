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
from .hongwen_auth_views import (
    HongwenGetPhoneView, HongwenUpdateUserView, HongwenWxLoginView,
)
from .hongwen_views import (
    HongwenChatbotView, HongwenChatRegenerateStreamView, HongwenChatRegenerateView,
    HongwenChatSessionDetailView, HongwenChatSessionListView, HongwenChatStreamView,
    HongwenChatView, HongwenFileUploadView, HongwenSpeakStreamDownloadView,
    HongwenSpeakStreamView, HongwenSpeakView, HongwenTranscribeView,
    HongwenUploadDownloadView,
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

    # Hongwen (泓问) customer helpdesk authentication. Business endpoints (chat,
    # upload, KB, triage) are added in PR4.
    path('hongwen/auth/wx_login', HongwenWxLoginView.as_view(), name='hongwen-wx-login'),
    path('hongwen/auth/update_wx_user', HongwenUpdateUserView.as_view(), name='hongwen-update-wx-user'),
    path('hongwen/auth/get-phone', HongwenGetPhoneView.as_view(), name='hongwen-get-phone'),

    # Hongwen customer chat surface (PR4). Paths mirror the helpdesk contract
    # under the /api/v1/hongwen/ namespace; the protected-upload download also
    # answers at the canonical /api/uploads/<path> (waypost/urls.py) because
    # that is what hongwen.services.utils.file_store URLs point at.
    path('hongwen/chatbot', HongwenChatbotView.as_view(), name='hongwen-chatbot'),
    path('hongwen/chat/sessions', HongwenChatSessionListView.as_view(), name='hongwen-chat-sessions'),
    path('hongwen/chat/sessions/<str:session_id>', HongwenChatSessionDetailView.as_view(), name='hongwen-chat-session-detail'),
    path('hongwen/chat', HongwenChatView.as_view(), name='hongwen-chat'),
    path('hongwen/chat/regenerate', HongwenChatRegenerateView.as_view(), name='hongwen-chat-regenerate'),
    path('hongwen/chat/stream', HongwenChatStreamView.as_view(), name='hongwen-chat-stream'),
    path('hongwen/chat/regenerate/stream', HongwenChatRegenerateStreamView.as_view(), name='hongwen-chat-regenerate-stream'),
    path('hongwen/files/upload', HongwenFileUploadView.as_view(), name='hongwen-file-upload'),
    path('hongwen/transcribe', HongwenTranscribeView.as_view(), name='hongwen-transcribe'),
    path('hongwen/speak', HongwenSpeakView.as_view(), name='hongwen-speak'),
    path('hongwen/speak/stream', HongwenSpeakStreamView.as_view(), name='hongwen-speak-stream'),
    path('hongwen/speak/stream/<str:stream_id>', HongwenSpeakStreamDownloadView.as_view(), name='hongwen-speak-stream-download'),
    # Namespaced alias of the canonical /api/uploads/<path> download (waypost/urls.py);
    # both serve the same protected blobs.
    path('hongwen/uploads/<path:relative_path>', HongwenUploadDownloadView.as_view(), name='hongwen-upload-download'),

    path('', include(router.urls)),
]
