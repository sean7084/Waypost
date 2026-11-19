"""URL routes for the Hongwen staff admin pages (PR5), mounted at /hongwen/.

Namespaced ``hongwen:`` and included inside waypost.urls.i18n_patterns so the
pages carry the language prefix like every other staff surface. The customer
API stays where PR4 put it (/api/v1/hongwen/, non-i18n) - these routes never
touch its contracts.
"""
from django.urls import path

from hongwen import views

app_name = 'hongwen'

urlpatterns = [
    # Console root lands on the chatbot (helpdesk /admin/ parity)
    path('', views.HongwenAdminRootView.as_view(), name='root'),

    # Chatbot (staff PC console, session-authenticated SSE)
    path('chatbot/', views.HongwenChatbotView.as_view(), name='chatbot'),
    path('chatbot/stream/', views.HongwenChatStreamView.as_view(), name='chat_stream'),
    path('chatbot/regenerate/stream/', views.HongwenChatRegenerateStreamView.as_view(), name='chat_regenerate_stream'),
    path('chatbot/sessions/<str:session_id>/rename/', views.HongwenChatSessionRenameView.as_view(), name='chat_session_rename'),
    path('chatbot/sessions/<str:session_id>/delete/', views.HongwenChatSessionDeleteView.as_view(), name='chat_session_delete'),

    # Customers (company CRUD)
    path('customers/', views.HongwenCustomerListView.as_view(), name='customer_list'),
    path('customers/create/', views.HongwenCustomerCreateView.as_view(), name='customer_create'),
    path('customers/<int:pk>/edit/', views.HongwenCustomerUpdateView.as_view(), name='customer_edit'),
    path('customers/<int:pk>/delete/', views.HongwenCustomerDeleteView.as_view(), name='customer_delete'),

    # Customer users (HongwenProfile list / create / permission edit / tv bump / delete)
    path('users/', views.HongwenUserListView.as_view(), name='user_list'),
    path('users/create/', views.HongwenUserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/edit/', views.HongwenUserUpdateView.as_view(), name='user_edit'),
    path('users/<int:pk>/bump-token/', views.HongwenUserBumpTokenView.as_view(), name='user_bump_token'),
    path('users/<int:pk>/delete/', views.HongwenUserDeleteView.as_view(), name='user_delete'),

    # Knowledge base (upload -> ingest, list, preview, download, replace, delete)
    path('kb/', views.HongwenKBView.as_view(), name='file_upload'),
    path('kb/upload/', views.HongwenKBUploadView.as_view(), name='kb_upload'),
    path('kb/documents/', views.HongwenKBDocumentsView.as_view(), name='kb_documents'),
    path('kb/documents/<int:document_id>/', views.HongwenKBDocumentPreviewView.as_view(), name='kb_document_preview'),
    path('kb/documents/<int:document_id>/download/', views.HongwenKBDocumentDownloadView.as_view(), name='kb_document_download'),
    path('kb/documents/<int:document_id>/replace/', views.HongwenKBDocumentReplaceView.as_view(), name='kb_document_replace'),
    path('kb/documents/<int:document_id>/delete/', views.HongwenKBDocumentDeleteView.as_view(), name='kb_document_delete'),
]