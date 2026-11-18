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
from .wenqu_auth_views import (
    WenquGetPhoneView, WenquUpdateUserView, WenquWxLoginView,
)
from .wenqu_views import (
    WenquChatbotView, WenquChatRegenerateStreamView, WenquChatRegenerateView,
    WenquChatSessionDetailView, WenquChatSessionListView, WenquChatStreamView,
    WenquChatView, WenquFileUploadView, WenquSpeakStreamDownloadView,
    WenquSpeakStreamView, WenquSpeakView, WenquTranscribeView,
    WenquUploadDownloadView,
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

    # Wenqu (问渠) customer helpdesk authentication. Business endpoints (chat,
    # upload, KB, triage) are added in PR4.
    path('wenqu/auth/wx_login', WenquWxLoginView.as_view(), name='wenqu-wx-login'),
    path('wenqu/auth/update_wx_user', WenquUpdateUserView.as_view(), name='wenqu-update-wx-user'),
    path('wenqu/auth/get-phone', WenquGetPhoneView.as_view(), name='wenqu-get-phone'),

    # Wenqu customer chat surface (PR4). Paths mirror the helpdesk contract
    # under the /api/v1/wenqu/ namespace; the protected-upload download also
    # answers at the canonical /api/uploads/<path> (waypost/urls.py) because
    # that is what wenqu.services.utils.file_store URLs point at.
    path('wenqu/chatbot', WenquChatbotView.as_view(), name='wenqu-chatbot'),
    path('wenqu/chat/sessions', WenquChatSessionListView.as_view(), name='wenqu-chat-sessions'),
    path('wenqu/chat/sessions/<str:session_id>', WenquChatSessionDetailView.as_view(), name='wenqu-chat-session-detail'),
    path('wenqu/chat', WenquChatView.as_view(), name='wenqu-chat'),
    path('wenqu/chat/regenerate', WenquChatRegenerateView.as_view(), name='wenqu-chat-regenerate'),
    path('wenqu/chat/stream', WenquChatStreamView.as_view(), name='wenqu-chat-stream'),
    path('wenqu/chat/regenerate/stream', WenquChatRegenerateStreamView.as_view(), name='wenqu-chat-regenerate-stream'),
    path('wenqu/files/upload', WenquFileUploadView.as_view(), name='wenqu-file-upload'),
    path('wenqu/transcribe', WenquTranscribeView.as_view(), name='wenqu-transcribe'),
    path('wenqu/speak', WenquSpeakView.as_view(), name='wenqu-speak'),
    path('wenqu/speak/stream', WenquSpeakStreamView.as_view(), name='wenqu-speak-stream'),
    path('wenqu/speak/stream/<str:stream_id>', WenquSpeakStreamDownloadView.as_view(), name='wenqu-speak-stream-download'),
    # Namespaced alias of the canonical /api/uploads/<path> download (waypost/urls.py);
    # both serve the same protected blobs.
    path('wenqu/uploads/<path:relative_path>', WenquUploadDownloadView.as_view(), name='wenqu-upload-download'),

    path('', include(router.urls)),
]
