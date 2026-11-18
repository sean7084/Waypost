"""URL routes for the Wenqu staff admin pages (PR5), mounted at /wenqu/.

Namespaced ``wenqu:`` and included inside waypost.urls.i18n_patterns so the
pages carry the language prefix like every other staff surface. The customer
API stays where PR4 put it (/api/v1/wenqu/, non-i18n) - these routes never
touch its contracts.
"""
from django.urls import path

from wenqu import views

app_name = 'wenqu'

urlpatterns = [
    # Customers (company CRUD)
    path('customers/', views.WenquCustomerListView.as_view(), name='customer_list'),
    path('customers/create/', views.WenquCustomerCreateView.as_view(), name='customer_create'),
    path('customers/<int:pk>/edit/', views.WenquCustomerUpdateView.as_view(), name='customer_edit'),
    path('customers/<int:pk>/delete/', views.WenquCustomerDeleteView.as_view(), name='customer_delete'),

    # Customer users (WenquProfile list / create / permission edit / tv bump / delete)
    path('users/', views.WenquUserListView.as_view(), name='user_list'),
    path('users/create/', views.WenquUserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/edit/', views.WenquUserUpdateView.as_view(), name='user_edit'),
    path('users/<int:pk>/bump-token/', views.WenquUserBumpTokenView.as_view(), name='user_bump_token'),
    path('users/<int:pk>/delete/', views.WenquUserDeleteView.as_view(), name='user_delete'),

    # Knowledge base (upload -> ingest, list, preview, download, replace, delete)
    path('kb/', views.WenquKBView.as_view(), name='file_upload'),
    path('kb/upload/', views.WenquKBUploadView.as_view(), name='kb_upload'),
    path('kb/documents/', views.WenquKBDocumentsView.as_view(), name='kb_documents'),
    path('kb/documents/<int:document_id>/', views.WenquKBDocumentPreviewView.as_view(), name='kb_document_preview'),
    path('kb/documents/<int:document_id>/download/', views.WenquKBDocumentDownloadView.as_view(), name='kb_document_download'),
    path('kb/documents/<int:document_id>/replace/', views.WenquKBDocumentReplaceView.as_view(), name='kb_document_replace'),
    path('kb/documents/<int:document_id>/delete/', views.WenquKBDocumentDeleteView.as_view(), name='kb_document_delete'),
]