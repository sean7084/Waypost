# Web Routes Reference - Waypost (non-DRF surface)

**Scope:** the server-rendered HTML application — **164 routes across 12 apps** — plus the admin
and utility surfaces. The REST API (`/api/v1/`, 71 routes) is documented in
[`API_GUIDE.md`](API_GUIDE.md); narrative business flows are in
[`WORKFLOW_GUIDE.md`](WORKFLOW_GUIDE.md); the data model is in
[`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md).

**Generated from:** the live URL resolver (`django.urls.get_resolver()` walked recursively), so
every pattern, route name and view below is what Django actually resolves — 490 routes in total
(245 admin, 71 API, 164 app, 10 utility/redirect).

---

## 1. How routing is composed

```
waypost/urls.py                     root urlconf
├── admin/                            django.contrib.admin  (245 routes)
├── api/v1/                           api.urls -> DRF DefaultRouter + WeChat auth  (71 routes)
├── docs/                             APIDocumentationView
├── i18n/setlang/                     django.views.i18n.set_language
├── ^media/(?P<path>.*)$              static serve (development only)
├── ^static/(?P<path>.*)$             static serve (development only)
└── <lang>/                           i18n_patterns prefix -> every app urlconf
    ├── accounts/  assets/  audit/  companies/  dashboard/
    ├── deliveries/  invoices/  mobile (m/)  products/
    └── purchases/  quotations/  reports/
```

**All application routes live behind the locale prefix.** With `en-us` active the paths are
`/en-us/assets/…`; the tables below omit the prefix for readability. `en-us/login/`,
`en-us/logout/`, `en-us/users/` and the bare `en-us/` are `RedirectView`s onto the canonical
`accounts/…` and `dashboard/` targets.

`inspections` and `customers` expose **no HTML routes** — `inspections` is API-only (mini
program) plus Django admin; `customers` contributes templates/helpers only.

---

## 2. Access-control model

Two layers, applied per view class:

1. **`LoginRequiredMixin`** — the baseline on essentially every app view. Any authenticated
   session passes. 153 mixin/decorator applications across the app `views.py` modules.
2. **`UserPassesTestMixin` + `test_func`** — capability gating via methods on
   `accounts.models.User`. 38 gated views in total. Failed checks return 403
   (`handle_no_permission`).

Verified capability → surface mapping:

| Capability method | Gates |
|---|---|
| `can_manage_users()` | `accounts/users/**` (list, detail, edit, create, reset-password, toggle-status) |
| `can_manage_orders()` | App-level base mixin in `quotations`, `deliveries`, `invoices`, `products`, `purchases` (and one `accounts` view) — i.e. the whole order-to-cash surface |
| `can_manage_companies()` | All `companies/**` CRUD (companies, divisions, locations, company contacts) incl. CSV import + rollback |
| `can_view_audit()` | `audit/**` list/detail/history, and `assets/logs/**` (asset change-log views) |
| `can_create_audit()` | `audit/new/` |
| `can_edit_audit(obj)` | `audit/assetaudit/<pk>/edit/` — **object-level** (scoped to the audit's own auditors) |
| `can_view_audit(obj)` | `audit/assetaudit/<pk>/` — **object-level** |
| `can_approve_order_management_prices()` | `products/approvals/**` (price-change approval queue) |

Ungated beyond login: `assets/**` (except `assets/logs/**`), `dashboard/**`, `reports/**`,
`mobile/**`, and all `accounts/` self-service screens (profile, settings, sessions, 2FA,
mailbox).

The `inspection_engineer` role and `can_run_inspection()` gate the **API** surface, not HTML —
see [`MINIPROGRAM_SPEC.md`](MINIPROGRAM_SPEC.md).

---

## 3. `accounts` — identity, sessions, mailbox (24 routes)

| URL | View | Purpose | Access |
|---|---|---|---|
| `accounts/login/` | `LoginView` | Session login (feeds `LoginAttempt`) | Public |
| `accounts/logout/` | `CustomLogoutView` | End session, close `UserSession` | Authenticated |
| `accounts/change-password/` | `ChangePasswordView` | Change own password | Authenticated |
| `accounts/profile/` | `ProfileView` | Own profile | Authenticated |
| `accounts/profile/edit/` | `ProfileEditView` | Edit own profile | Authenticated |
| `accounts/settings/` | `UserSettingsView` | Language / timezone preferences | Authenticated |
| `accounts/sessions/` | `SessionListView` | List own active sessions | Authenticated |
| `accounts/sessions/<pk>/terminate/` | `SessionTerminateView` | Revoke one session | Authenticated |
| `accounts/2fa/setup/` | `TwoFactorSetupView` | Enrol TOTP device (QR) | Authenticated |
| `accounts/2fa/setup-simple/` | `setup_2fa` | Simplified 2FA enrolment | Authenticated |
| `accounts/2fa/verify/` | `TwoFactorVerifyView` | Confirm TOTP code | Authenticated |
| `accounts/2fa/backup-tokens/` | `BackupTokensView` | View/regenerate static backup tokens | Authenticated |
| `accounts/2fa/disable/` | `disable_2fa` | Turn off 2FA | Authenticated |
| `accounts/mailbox/` | `MailboxInboxView` | Per-user mailbox inbox | Authenticated |
| `accounts/mailbox/<pk>/` | `MailboxMessageDetailView` | Read one synced message | Authenticated |
| `accounts/mailbox/sync/` | `MailboxSyncView` | Trigger manual mailbox sync | Authenticated |
| `accounts/mailbox/<pk>/reprocess-rfq/` | `MailboxRFQReprocessView` | Re-run Minimax RFQ classification | Authenticated |
| `accounts/users/` | `UserListView` | Staff directory | `can_manage_users` |
| `accounts/users/create/` | `UserCreateView` | Create staff user | `can_manage_users` |
| `accounts/users/<uuid>/` | `UserDetailView` | User detail + roles | `can_manage_users` |
| `accounts/users/<uuid>/edit/` | `UserEditView` | Edit user, roles, scope | `can_manage_users` |
| `accounts/users/<uuid>/reset-password/` | `UserResetPasswordView` | Force password reset | `can_manage_users` |
| `accounts/users/<uuid>/toggle-status/` | `UserToggleStatusView` | Activate / deactivate | `can_manage_users` |
| `accounts/dev-login/` | `dev_login` | Development login shortcut | see note |

> ⚠️ **`accounts/dev-login/`** is a convenience entry point. Confirm it is unreachable when
> `DJANGO_DEBUG=False` before any production deploy (tracked under the security review in
> [`SECURITY.md`](SECURITY.md)).

**Side effects:** login writes `LoginAttempt` (success + failure reason + IP + user agent) and
`UserSession`; mailbox sync upserts `ReceivedEmailMessage` (idempotent on
`(mailbox, direction, external_id)`); RFQ reprocessing calls Minimax and writes
`rfq_status` / `rfq_confidence` / `rfq_extracted_data`, and can spawn a draft `Quotation`.

---

## 4. `assets` — register, taxonomy, custody (29 routes)

| URL | View | Purpose | Access |
|---|---|---|---|
| `assets/` | `AssetListView` | Asset register with filter/search | Authenticated |
| `assets/create/` | `AssetCreateView` | Add an asset | Authenticated |
| `assets/<uuid>/` | `AssetDetailView` | Asset detail, history, custody | Authenticated |
| `assets/<uuid>/edit/` | `AssetUpdateView` | Edit asset | Authenticated |
| `assets/<uuid>/delete/` | `AssetDeleteView` | Delete asset | Authenticated |
| `assets/<uuid>/assign/` | `asset_assign_view` | Assign to user/location | Authenticated |
| `assets/<uuid>/return/` | `asset_return_view` | Record return | Authenticated |
| `assets/bulk-edit/` | `asset_bulk_edit_view` | Bulk field edit | Authenticated |
| `assets/export/` | `asset_export_view` | Export (Excel) | Authenticated |
| `assets/export/csv/` | `asset_export_csv` | Export (CSV) | Authenticated |
| `assets/import/` | `asset_import_view` | CSV/XLSX import | Authenticated |
| `assets/import/rollback/` | `asset_import_rollback_view` | Roll back an `ImportRun` | Authenticated |
| `assets/import/sample.csv` | `download_sample_csv` | Import template | Authenticated |
| `assets/api/stats/` | `asset_stats_api` | JSON counts for dashboards | Authenticated |
| `assets/brands-models/` | `brands_models_view` | Combined brand/model browser | Authenticated |
| `assets/brands/` | `BrandListView` | Brand list | Authenticated |
| `assets/brands/create/` | `BrandCreateView` | Add brand | Authenticated |
| `assets/brands/<uuid>/edit/` | `BrandUpdateView` | Edit brand | Authenticated |
| `assets/brands/<uuid>/delete/` | `BrandDeleteView` | Delete brand | Authenticated |
| `assets/models/` | `ModelListView` | Model list | Authenticated |
| `assets/models/create/` | `ModelCreateView` | Add model | Authenticated |
| `assets/models/<uuid>/edit/` | `ModelUpdateView` | Edit model | Authenticated |
| `assets/models/<uuid>/delete/` | `ModelDeleteView` | Delete model | Authenticated |
| `assets/categories/` | `CategoryListView` | Category tree | Authenticated |
| `assets/categories/create/` | `CategoryCreateView` | Add category | Authenticated |
| `assets/categories/<uuid>/edit/` | `CategoryUpdateView` | Edit category | Authenticated |
| `assets/categories/<uuid>/delete/` | `CategoryDeleteView` | Delete category | Authenticated |
| `assets/logs/` | `AssetChangeLogListView` | Asset audit-log list | `can_view_audit` |
| `assets/logs/<uuid>/` | `AssetChangeLogDetailView` | One log entry + field diffs | `can_view_audit` |

**Side effects:** assign/return write `AssetAssignment` rows and update `Asset.status`;
imports create an `ImportRun` + ordered `ImportRunChange` ledger so `import/rollback/` can
restore prior state; every mutation writes `AuditLog` (+ `ChangeLog` field diffs).

> `assets/views.py` is the largest module in the project and has the thinnest test coverage —
> it is the first priority in [`TESTING.md`](TESTING.md).

---

## 5. `companies` — tenants, sites, contacts (22 routes)

All gated by `can_manage_companies()`.

| URL | View | Purpose |
|---|---|---|
| `companies/` | `CompanyListView` | Company list |
| `companies/add/` | `CompanyCreateView` | Create company |
| `companies/<pk>/edit/` | `CompanyUpdateView` | Edit company |
| `companies/<pk>/delete/` | `CompanyDeleteView` | Delete company |
| `companies/import/csv/` | `company_import_csv_view` | Bulk company import |
| `companies/import/csv/rollback/` | `company_import_rollback_view` | Roll back that import |
| `companies/import/sample.csv` | `company_import_sample_csv_view` | Template |
| `companies/locations/` | `LocationListView` | Site list |
| `companies/locations/create/` | `LocationCreateView` | Create site |
| `companies/locations/<pk>/edit/` | `LocationUpdateView` | Edit site |
| `companies/locations/<pk>/delete/` | `LocationDeleteView` | Delete site |
| `companies/locations/import/csv/` | `location_import_csv_view` | Bulk site import |
| `companies/locations/import/csv/rollback/` | `location_import_rollback_view` | Roll back |
| `companies/locations/import/sample.csv` | `location_import_sample_csv_view` | Template |
| `companies/locations/company-contacts/` | `location_company_contacts_api_view` | JSON contacts for a site |
| `companies/users/` | `CompanyUserListView` | Customer/company contacts |
| `companies/users/create/` | `CompanyUserCreateView` | Create contact |
| `companies/users/<pk>/edit/` | `CompanyUserUpdateView` | Edit contact |
| `companies/users/<pk>/remove/` | `company_contact_remove_view` | Remove contact |
| `companies/users/import/csv/` | `company_contact_import_csv_view` | Bulk contact import |
| `companies/users/import/csv/rollback/` | `company_contact_import_rollback_view` | Roll back |
| `companies/users/import/sample.csv` | `company_contact_import_sample_csv_view` | Template |

**Note:** `CompanyUser` is the *customer-side* contact (ADR-0010 separates it from the staff
`User`); `is_authorized_rfq_sender` on that model is what authorizes an inbound email to start
the RFQ pipeline.

---

## 6. `quotations` — offer lifecycle (14 routes)

All gated by `can_manage_orders()` (app-level mixin).

| URL | View | Purpose / side effect |
|---|---|---|
| `quotations/list` | `QuotationListView` | List + filter |
| `quotations/create/` | `QuotationCreateView` | Draft a quotation (manual or from an RFQ email) |
| `quotations/<pk>/` | `QuotationDetailView` | Detail with lines and totals |
| `quotations/<pk>/edit/` | `QuotationUpdateView` | Edit lines; recalculates tax and totals |
| `quotations/<pk>/duplicate/` | `duplicate_quotation` | Clone into a new draft |
| `quotations/<pk>/confirm/` | `confirm_quotation` | **Status transition** → confirmed |
| `quotations/<pk>/cancel/` | `cancel_quotation` | **Status transition** → cancelled |
| `quotations/<pk>/send/` | `send_quotation` | Email the PDF; creates `EmailDispatch` |
| `quotations/<pk>/pdf/` | `generate_quotation_pdf` | Render PDF via WeasyPrint |
| `quotations/<pk>/convert-to-purchase/` | `convert_to_purchase` | Create the `PurchaseOrder` (OneToOne) |
| `quotations/<pk>/delete/` | `QuotationDeleteView` | Delete |
| `quotations/<pk>/attachment/upload/` | `attachment_upload` | Add `QuotationAttachment` |
| `quotations/attachment/<pk>/delete/` | `attachment_delete` | Remove attachment |
| `quotations/default-templates/` | `QuotationDefaultTemplateView` | Manage per-company default PDF template |

Status transitions write `invoices.WorkflowStatusAudit` (`entity_type` + `entity_id`) and
`AuditLog`. `convert-to-purchase` is the hinge into procurement.

---

## 7. `purchases` — procurement (4 routes)

All gated by `can_manage_orders()`.

| URL | View | Purpose / side effect |
|---|---|---|
| `purchases/list` | `PurchaseListView` | Purchase orders + receipts |
| `purchases/<uuid>/` | `PurchaseDetailView` | PO detail with receipt progress |
| `purchases/<uuid>/edit-serial/` | `edit_asset_serial` | Correct a serial number on receipt |
| `purchases/orders/<pk>/receive/` | `purchase_receipt_view` | **Goods in** — creates `PurchaseReceipt`, advances `quantity_received`, can instantiate `Asset` rows |

---

## 8. `deliveries` — dispatch (7 routes)

All gated by `can_manage_orders()`.

| URL | View | Purpose / side effect |
|---|---|---|
| `deliveries/list` | `DeliveryOrderListView` | Delivery orders |
| `deliveries/create/from-quotation/<quotation_pk>/` | `delivery_create_view` | Build a delivery from a confirmed quotation |
| `deliveries/<pk>/` | `DeliveryOrderDetailView` | Detail + lines |
| `deliveries/<pk>/dispatch/` | `mark_dispatched` | **Status transition** → dispatched |
| `deliveries/<pk>/complete/` | `mark_completed` | **Status transition** → completed |
| `deliveries/<pk>/upload-signed/` | `upload_signed_copy` | Store the signed POD (`signed_file`) |
| `deliveries/<pk>/pdf/` | `generate_delivery_pdf` | Render the sign-off sheet |

`unique_asset_per_delivery_order` prevents the same serialised asset appearing twice on one
delivery.

---

## 9. `invoices` — batches, invoice info, email dispatch (14 routes)

All gated by `can_manage_orders()`.

| URL | View | Purpose / side effect |
|---|---|---|
| `invoices/` | `WeeklyOrderBatchListView` | SharePoint weekly batches |
| `invoices/<pk>/` | `WeeklyOrderBatchDetailView` | One batch + row outcome |
| `invoices/import/` | `import_sharepoint_batch_view` | **Import** the weekly SharePoint file → `WeeklyOrderBatch` + `InvoiceInfo` rows |
| `invoices/invoice-info/` | `InvoiceInfoListView` | Invoice register |
| `invoices/invoice-info/<pk>/` | `InvoiceInfoDetailView` | Invoice detail + lines |
| `invoices/invoice-info/<pk>/update/` | `invoice_info_update_view` | Edit invoice fields |
| `invoices/invoice-info/<pk>/recalculate/` | `invoice_info_recalculate_view` | Recompute net/tax/gross from lines |
| `invoices/invoice-info/<pk>/document/` | `invoice_info_document_view` | Produce the invoice document |
| `invoices/invoice-info/export/` | `invoice_info_export_view` | Export |
| `invoices/emails/` | `EmailDispatchListView` | Outbound dispatch queue |
| `invoices/emails/compose/` | `email_dispatch_compose_view` | Compose a dispatch |
| `invoices/emails/compose/quotation/<quotation_pk>/` | `email_dispatch_compose_view` | Compose pre-linked to a quotation |
| `invoices/emails/<pk>/client-confirmed/` | `email_dispatch_mark_client_confirmed_view` | **Lifecycle** → client confirmed |
| `invoices/emails/<pk>/esker-forward/` | `email_dispatch_mark_esker_view` | **Lifecycle** → forwarded to Esker (AP) |

The batch import is guarded by `uniq_invoice_business_keys`; a failure records
`failed_row_number` + `failure_reason` on the batch. Email sends go through each user's
`UserMailboxSettings` (`DatabaseSMTPEmailBackend`) with `reply_message_id` /
`reply_references` preserving the customer thread.

---

## 10. `products` — catalog pricing (10 routes)

| URL | View | Purpose | Access |
|---|---|---|---|
| `products/price_list` | `ProductPriceListView` | Current price list (hardware + services) | `can_manage_orders` |
| `products/add/` | `ProductPriceCreateView` | Add a hardware price | `can_manage_orders` |
| `products/<pk>/edit/` | `ProductPriceUpdateView` | Edit a hardware price | `can_manage_orders` |
| `products/<pk>/delete/` | `ProductPriceDeleteView` | Delete a price | `can_manage_orders` |
| `products/services/add/` | `ServicePriceCreateView` | Add a service price | `can_manage_orders` |
| `products/services/<pk>/edit/` | `ServicePriceUpdateView` | Edit a service price | `can_manage_orders` |
| `products/import/` | `import_prices_view` | Bulk price import | `can_manage_orders` |
| `products/import/template/` | `download_import_template` | Import template | `can_manage_orders` |
| `products/approvals/` | `ProductPriceApprovalListView` | Price-change approval queue | `can_approve_order_management_prices` |
| `products/approvals/<pk>/` | `ProductPriceApprovalDetailView` | Review one request (snapshots) | `can_approve_order_management_prices` |

Approving a request flips `ProductPrice.is_current`, which the DB constrains to **one current
price per catalog target** (ADR-0005). `current_snapshot` / `proposed_snapshot` JSON preserves
the before/after for audit.

---

## 11. `audit` — stocktakes and system log (6 routes)

| URL | View | Purpose | Access |
|---|---|---|---|
| `audit/dashboard/` | `AssetAuditDashboardView` | Audit programme overview | `can_view_audit` |
| `audit/history/` | `AssetAuditHistoryView` | Past audits | `can_view_audit` |
| `audit/new/` | `AssetAuditCreateView` | Start an audit | `can_create_audit` |
| `audit/assetaudit/<uuid>/` | `AssetAuditDetailView` | One audit + records | `can_view_audit(obj)` |
| `audit/assetaudit/<uuid>/edit/` | `AssetAuditUpdateView` | Edit / progress an audit | `can_edit_audit(obj)` |
| `audit/events/` | `SystemEventListView` | Severity-graded system events | `can_view_audit` |

`can_edit_audit(obj)` / `can_view_audit(obj)` are **object-level**: they consult the audit's own
`auditors` M2M and scope, not just the global capability.

---

## 12. `reports`, `dashboard`, `mobile` (16 routes)

Ungated beyond `LoginRequiredMixin`.

| URL | View | Purpose |
|---|---|---|
| `reports/` | `ReportDashboardView` | Report launcher |
| `reports/inventory/` | `AssetInventoryReportView` | Inventory report |
| `reports/quick-stats/` | `QuickStatsView` | JSON headline metrics |
| `reports/charts/status/` | `AssetStatusChartView` | Assets by status |
| `reports/charts/category/` | `AssetCategoryChartView` | Assets by category |
| `reports/charts/brand/` | `AssetBrandChartView` | Assets by brand |
| `reports/charts/warranty/` | `WarrantyStatusChartView` | Warranty expiry profile |
| `reports/charts/quotation-status/` | `QuotationStatusChartView` | Quotation pipeline |
| `reports/charts/purchase-summary/` | `PurchaseSummaryChartView` | Spend/receipt summary |
| `dashboard/` | `dashboard_view` | Landing dashboard |
| `dashboard/quick-stats/` | `quick_stats_view` | JSON metrics for cards |
| `dashboard/save-config/` | `save_dashboard_config` | Persist per-user layout |
| `dashboard/workflow/` | `workflow_dashboard_view` | Cross-document workflow board |
| `dashboard/workflow/search/` | `workflow_search_view` | Search across quotations/deliveries/invoices |
| `m/` | `MobileDashboardView` | Compact mobile landing |
| `m/scan/` | `MobileScanView` | Barcode scan lookup |

---

## 13. Non-app surfaces

| URL | What it is |
|---|---|
| `admin/**` | Django admin — 245 auto-generated routes over 26 registered models (changelist / add / change / delete / history each). Access is `is_staff` + Django model permissions, **not** the `can_*` capability layer above. |
| `api/v1/**` | DRF — see [`API_GUIDE.md`](API_GUIDE.md). Session auth for the web app, JWT for the mini program. |
| `docs/` | `APIDocumentationView` — rendered API docs. |
| `i18n/setlang/` | Language switch (sets the locale cookie that drives the `<lang>/` prefix). |
| `^media/`, `^static/` | Dev-only file serving. In production Nginx serves both — see [`DEPLOYMENT.md`](DEPLOYMENT.md). |

---

## 14. Regenerating this document

```python
# python manage.py shell
from django.urls import get_resolver
rows = []
def walk(r, prefix=''):
    for p in r.url_patterns:
        pat = prefix + str(p.pattern)
        if hasattr(p, 'url_patterns'):
            walk(p, pat)
        else:
            cb = p.callback
            cls = getattr(cb, 'view_class', None)
            rows.append((pat, p.name, (cls or cb).__name__, (cls or cb).__module__))
walk(get_resolver())
for r in sorted(rows):
    print('\t'.join(map(str, r)))
```

Re-run after any `urls.py` change and diff against this file.

---

*Last Updated: September 15, 2026*
