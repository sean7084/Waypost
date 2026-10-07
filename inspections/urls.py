"""
URL configuration for the inspections web UI.

Mounted at ``/inspections/`` inside the project's ``i18n_patterns`` block so the
module respects the active language like the rest of the site.
"""
from django.urls import path

from . import views

app_name = 'inspections'

urlpatterns = [
    # Calendar dashboard (default landing page for the module).
    path('', views.InspectionDashboardView.as_view(), name='dashboard'),
    path('api/move/', views.InspectionMoveView.as_view(), name='inspection_move'),

    # Batch management.
    path('batches/', views.InspectionBatchListView.as_view(), name='batch_list'),
    path('batches/new/', views.InspectionBatchCreateView.as_view(), name='batch_create'),
    path('batches/new/by-brand/', views.InspectionBatchCreateByBrandView.as_view(), name='batch_create_by_brand'),
    path('batches/new/import/', views.InspectionBatchImportView.as_view(), name='batch_import'),
    path('batches/new/import/sample/<str:kind>/', views.ImportSampleFileView.as_view(),
         name='import_sample'),
    path('batches/<uuid:pk>/', views.InspectionBatchDetailView.as_view(), name='batch_detail'),
    path('batches/<uuid:pk>/edit/', views.InspectionBatchUpdateView.as_view(), name='batch_update'),

    # Inspection browsing.
    path('list/', views.StoreInspectionListView.as_view(), name='inspection_list'),
    path('review/', views.InspectionReviewView.as_view(), name='review'),
    path('review/bulk/', views.InspectionReviewBulkUpdateView.as_view(), name='review_bulk'),
    path('<uuid:pk>/', views.StoreInspectionDetailView.as_view(), name='inspection_detail'),
    path('<uuid:pk>/edit/', views.StoreInspectionUpdateView.as_view(), name='inspection_edit'),
    path('<uuid:pk>/assign-fe/', views.EngineerAssignView.as_view(), name='assign_fe'),
    path('issues/<uuid:pk>/update/', views.InspectionIssueUpdateView.as_view(), name='issue_update'),

    # Cross-store asset-list export (GUCCI_资产表 format).
    path('export/', views.AssetListExportView.as_view(), name='asset_list_export'),
]
