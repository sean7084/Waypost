"""
URL configuration for Quotations app.
"""
from django.urls import path
from . import views

app_name = 'quotations'

urlpatterns = [
    path('', views.QuotationListView.as_view(), name='list'),
    path('default-templates/', views.QuotationDefaultTemplateView.as_view(), name='default_templates'),
    path('create/', views.QuotationCreateView.as_view(), name='create'),
    path('import/', views.quotation_import_view, name='import'),
    path('import/rollback/', views.quotation_import_rollback_view, name='import_rollback'),
    path('<int:pk>/', views.QuotationDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.QuotationUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.QuotationDeleteView.as_view(), name='delete'),
    path('<int:pk>/pdf/', views.generate_quotation_pdf, name='pdf'),
    path('<int:pk>/duplicate/', views.duplicate_quotation, name='duplicate'),
    path('<int:pk>/cancel/', views.cancel_quotation, name='cancel'),
    path('<int:pk>/confirm/', views.confirm_quotation, name='confirm'),
    path('<int:pk>/send/', views.send_quotation, name='send'),
    path('<int:pk>/attachment/upload/', views.attachment_upload, name='attachment_upload'),
    path('attachment/<int:pk>/delete/', views.attachment_delete, name='attachment_delete'),
    path('<int:pk>/convert-to-purchase/', views.convert_to_purchase, name='convert_to_purchase'),
]
