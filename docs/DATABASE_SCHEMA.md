# Database Schema Reference - Waypost

**Scope:** all 52 persisted models across 11 first-party Django apps (plus `django-otp` tables).
**Generated from:** the live Django app registry (`apps.get_models()` + `_meta`), then curated — so
every field, relation, primary-key type and constraint below reflects the code, not a guess.
**Companion docs:** [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md) (SQLite → PostgreSQL),
[`ARCHITECTURE.md`](ARCHITECTURE.md) (bounded contexts and data flow).

---

## 1. Conventions

### Primary-key strategy (mixed, by design and by history)

| Strategy | Apps / models | Notes |
|---|---|---|
| `UUIDField` PK | `accounts.User`, `accounts.LoginAttempt`, `accounts.UserSession`, `accounts.WeChatIdentity`, **all** `assets.*`, **all** `audit.*`, **all** `inspections.*`, **all** `reports.*`, `purchases.PurchaseReceipt` | UUID4 default; safe to expose in URLs, no cross-DB sequence concerns |
| `BigAutoField` PK | **all** `companies.*`, `quotations.*`, `deliveries.*`, `invoices.*`, `products.*`, `purchases.PurchaseOrder`/`PurchaseOrderItem`, `accounts.AdminRole`, `accounts.ReceivedEmailMessage`, `accounts.UserMailboxSettings` | Sequential 64-bit ids |
| `AutoField` PK | `otp_totp.TOTPDevice`, `otp_static.StaticDevice`, `otp_static.StaticToken` | Third-party (`django-otp`) — do not change |
| Singleton | `accounts.SystemSMTPSettings` (`PositiveSmallIntegerField` PK, fixed id `1`) | One row system-wide |

> ⚠️ **Migration impact:** the mixed strategy matters for SQLite → PostgreSQL. BigAutoField
> sequences must be reset after a `loaddata` (see `DATABASE_MIGRATION.md`); UUID tables need none.

### Shared field patterns

- **Timestamps:** nearly every first-party model carries `created_at` / `updated_at`
  (`auto_now_add` / `auto_now`). Exceptions are event-style tables that use a single
  domain timestamp instead (`LoginAttempt.timestamp`, `AuditLog.timestamp`,
  `SystemEvent.timestamp`, `AssetAssignment.assigned_date`, `InvoiceInfoItem`).
- **Status as `CharField` + choices:** no enum columns. State machines live in Python
  (`TextChoices`) and are persisted as short strings — `Asset.status`, `Quotation.status`,
  `DeliveryOrder.status`, `PurchaseOrder.status`, `StoreInspection.status`, `EmailDispatch.status`.
- **Encrypted secrets:** credential fields are stored as `encrypted_password: TextField`
  (Fernet, key from `DJANGO_FIELD_ENCRYPTION_KEY`) — `UserMailboxSettings`,
  `SystemSMTPSettings`. Never plaintext. See `accounts/crypto.py` and [`SECURITY.md`](SECURITY.md).
- **JSON escape hatches:** `JSONField` holds heterogeneous or evolving data —
  `AssetModel.specifications`, `ProductPriceApprovalRequest.current_snapshot`/`proposed_snapshot`,
  `AuditLog.metadata`, `SystemEvent.metadata`, `EmailDispatch.attachments`,
  `StoreInspection.device_counts`/`cover_extras`, `ReportTemplate.template_definition`.
- **Generic relations:** only `audit.AuditLog` uses `content_type` + `object_id`
  (`GenericForeignKey content_object`), letting one table log actions against any model.
- **Money:** `DecimalField` everywhere for prices/tax/totals — never `FloatField`.

---

## 2. Bounded contexts

| Context | App | Models | Responsibility |
|---|---|---|---|
| Identity & access | `accounts` | User, AdminRole, UserSession, LoginAttempt, WeChatIdentity, UserMailboxSettings, SystemSMTPSettings, ReceivedEmailMessage | Authentication, RBAC, 2FA, per-user mailboxes, inbound email + RFQ triage |
| 2FA (vendor) | `otp_totp`, `otp_static` | TOTPDevice, StaticDevice, StaticToken | `django-otp` device/token storage |
| Organization | `companies` | Company, Division, Location, CompanyUser, ImportRun, ImportRunChange | Tenant hierarchy, site structure, customer contacts, import rollback ledger |
| Catalog | `assets` (taxonomy), `products` (pricing) | AssetCategory, AssetBrand, AssetModel, ProductPrice, ServiceItem, ProductPriceApprovalRequest | What can be quoted/owned, and at what price |
| Asset lifecycle | `assets` | Asset, AssetAssignment, AssetMaintenance | Physical devices, custody, upkeep |
| Sales | `quotations` | Quotation, QuotationItem, QuotationAttachment | Offer lifecycle from RFQ to confirmation |
| Procurement | `purchases` | PurchaseOrder, PurchaseOrderItem, PurchaseReceipt | Buying against a confirmed quotation |
| Fulfilment | `deliveries` | DeliveryOrder, DeliveryItem | Dispatch and signed receipt |
| Billing | `invoices` | WeeklyOrderBatch, InvoiceInfo, InvoiceInfoItem, EmailDispatch, WorkflowStatusAudit | SharePoint batch import, invoice lines, outbound email lifecycle |
| Audit | `audit` | AssetAudit, AssetAuditRecord, AuditLog, ChangeLog, SystemEvent | Physical stocktakes plus system-wide action/field/system logging |
| Reporting | `reports` | ReportTemplate, GeneratedReport, ReportSchedule, ReportShare | Template-driven report generation, scheduling, sharing |
| Store inspection | `inspections` | StoreInspection, InspectionDevice, InspectionPhoto, InspectionIssue | Kering EUS store health-check (WeChat mini program backend) |

`customers`, `dashboard`, `mobile`, `users` and `utils` are **model-free** apps: they contribute
views, URL routes and helpers only (see [`WEB_ROUTES.md`](WEB_ROUTES.md)).

---

## 3. ER diagrams

### 3.1 Identity & access (`accounts`)

```mermaid
erDiagram
    User ||--o{ UserSession : "has"
    User ||--o{ LoginAttempt : "attempted by"
    User ||--o| UserMailboxSettings : "owns"
    User ||--o{ WeChatIdentity : "binds"
    User }o--o{ AdminRole : "roles (M2M)"
    User }o--o{ Division : "managed_divisions (M2M)"
    User }o--o{ Location : "managed_locations (M2M)"
    User |o--o| User : "manager (self FK)"
    User }o--o| Company : "company / managed_company"
    UserMailboxSettings ||--o{ ReceivedEmailMessage : "syncs"
    TOTPDevice }o--|| User : "2FA device"
    StaticDevice }o--|| User : "2FA device"
    StaticDevice ||--o{ StaticToken : "backup tokens"

    User {
        uuid id PK
        string username UK
        string employee_id UK "nullable"
        string password
        bool two_factor_enabled
        json backup_tokens
        bool must_change_password
        string language_preference
        string timezone
    }
    UserMailboxSettings {
        bigauto id PK
        uuid user FK "OneToOne"
        string email_address
        text encrypted_password "Fernet"
        string receive_protocol "imap|pop3"
        bool auto_sync_enabled
        bool is_active
        datetime last_mailbox_sync_at
    }
    ReceivedEmailMessage {
        bigauto id PK
        bigauto mailbox FK
        string direction "in|out"
        string external_id
        string message_id
        string rfq_status
        decimal rfq_confidence
        json rfq_extracted_data
    }
    WeChatIdentity {
        uuid id PK
        uuid user FK
        string appid "UK with openid"
        string openid "UK with appid"
        string unionid
    }
```

**Constraints:** `uniq_mailbox_direction_external_message` (UniqueConstraint on
`ReceivedEmailMessage`) makes mailbox sync idempotent; `uniq_wechat_appid_openid`
prevents one WeChat openid binding to two staff users.

### 3.2 Organization (`companies`)

```mermaid
erDiagram
    Company ||--o{ Division : "has"
    Company ||--o{ Location : "has"
    Company ||--o{ CompanyUser : "has"
    Division |o--o{ Division : "parent_division"
    Location |o--o{ Location : "parent_location"
    Division ||--o{ Location : "optionally groups"
    CompanyUser }o--o| User : "linked staff account"
    CompanyUser |o--o{ CompanyUser : "manager"
    Company |o--o| CompanyUser : "primary_contact_company_user"
    User ||--o{ ImportRun : "started"
    ImportRun ||--o{ ImportRunChange : "tracks"

    Company {
        bigauto id PK
        string name UK
        string code UK
        string asset_prefix
        int next_asset_number
        string default_quotation_template
        string status
    }
    Location {
        bigauto id PK
        string name
        string code "UK with company"
        string location_type
        string zone
        string rack
        string shelf
        decimal latitude
        decimal longitude
    }
    CompanyUser {
        bigauto id PK
        uuid user FK "nullable"
        bigauto company FK
        string role
        bool is_authorized_rfq_sender
        string work_email
    }
    ImportRunChange {
        bigauto id PK
        bigauto run FK
        int sequence
        string app_label
        string model_name
        string object_pk
        string operation "create|update|delete"
        json before_data
        json after_data
    }
```

**Constraints:** `Location` `unique_together (company, code)`; `CompanyUser`
`unique_together (user, company)`. `ImportRun.rolled_back_at` + the ordered
`ImportRunChange` ledger are what make `utils/import_rollback.rollback_run` possible.

### 3.3 Catalog & pricing (`assets` taxonomy + `products`)

```mermaid
erDiagram
    AssetCategory |o--o{ AssetCategory : "parent"
    AssetBrand ||--o{ AssetModel : "makes"
    AssetCategory ||--o{ AssetModel : "classifies"
    AssetCategory |o--o| AssetModel : "default_asset_model"
    AssetBrand |o--o{ ProductPrice : "priced"
    AssetModel |o--o{ ProductPrice : "priced"
    ServiceItem |o--o{ ProductPrice : "priced"
    ProductPrice |o--o{ ProductPriceApprovalRequest : "target_price"
    User ||--o{ ProductPriceApprovalRequest : "requested_by"

    AssetCategory {
        uuid id PK
        string code UK
        string name
        string item_type "hardware|service"
        bool requires_serial_number
        int default_warranty_months
        decimal depreciation_rate
    }
    AssetModel {
        uuid id PK
        uuid brand FK
        uuid category FK "nullable"
        string model_number "UK with brand"
        string unit
        json specifications
    }
    ProductPrice {
        bigauto id PK
        uuid brand FK "nullable"
        uuid model FK "nullable"
        bigauto service_item FK "nullable"
        decimal price_without_tax
        decimal tax_rate
        bool is_current
        date valid_from
        date valid_until
    }
```

**Constraints (the interesting ones):**

- `productprice_single_catalog_target` (**CheckConstraint**) — a price row targets exactly one
  of `model` / `service_item`, enforcing the hardware-vs-service catalog split at the DB level.
- `uniq_current_product_price_per_model` and `uniq_current_product_price_per_service_item`
  (**UniqueConstraint**) — only one `is_current=True` price per catalog target, so "current
  price" lookups cannot return two rows. This is the backbone of price lifecycle management
  (ADR-0005): superseding a price clears `is_current` on the old row.
- `AssetModel.unique_together (brand, model_number)`; `AssetBrand.name`/`code` unique;
  `AssetCategory.code` unique.

> ⚠️ Because brand `code` is unique and derived from the name, case variants of the same
> brand (`HP` vs `hp`) collide. Importers must look up **by code, then by name**, and only
> create when neither matches (see `import_kering_master`).

### 3.4 Asset lifecycle (`assets`)

```mermaid
erDiagram
    AssetCategory ||--o{ Asset : "classifies"
    AssetBrand ||--o{ Asset : "makes"
    AssetModel |o--o{ Asset : "model"
    Company ||--o{ Asset : "owns"
    Division |o--o{ Asset : "within"
    Location |o--o{ Asset : "sited at"
    User |o--o{ Asset : "assigned_to"
    Quotation |o--o{ Asset : "source_quotation"
    Asset ||--o{ AssetAssignment : "custody history"
    Asset ||--o{ AssetMaintenance : "upkeep"

    Asset {
        uuid id PK
        string asset_number UK
        string serial_number
        string barcode UK "nullable"
        string status
        string condition
        decimal purchase_price
        decimal current_value
        date purchase_date
        date warranty_start_date
        date warranty_end_date
        string location_zone
        string location_rack
        string location_shelf
        datetime last_audit_date
    }
    AssetAssignment {
        uuid id PK
        uuid asset FK
        uuid assigned_to FK "nullable"
        uuid location FK "nullable"
        string assignment_type
        datetime assigned_date
        datetime returned_date "nullable"
        string return_condition
    }
```

`Asset.asset_number` is the **business key** (unique, text — keep it out of scientific
notation on export). `Asset.barcode` is unique but nullable, backing the barcode-scan flows.
Warehouse slot detail is duplicated onto the asset (`location_zone`/`_rack`/`_shelf`) as well
as resolvable via `Location` (ADR-0009).

### 3.5 Order-to-cash spine (`quotations` → `purchases` → `deliveries` → `invoices`)

```mermaid
erDiagram
    Company ||--o{ Quotation : "customer"
    ReceivedEmailMessage |o--o{ Quotation : "source_email_message"
    Quotation ||--o{ QuotationItem : "lines"
    Quotation ||--o{ QuotationAttachment : "files"
    ProductPrice ||--o{ QuotationItem : "priced from"
    ServiceItem |o--o{ QuotationItem : "service line"

    Quotation ||--|| PurchaseOrder : "OneToOne"
    PurchaseOrder ||--o{ PurchaseOrderItem : "lines"
    QuotationItem |o--|| PurchaseOrderItem : "OneToOne"
    Quotation ||--o{ PurchaseReceipt : "goods in"

    Quotation ||--o{ DeliveryOrder : "dispatched as"
    DeliveryOrder ||--o{ DeliveryItem : "lines"
    Asset |o--o{ DeliveryItem : "serialised item"
    QuotationItem |o--o{ DeliveryItem : "source line"

    WeeklyOrderBatch ||--o{ InvoiceInfo : "batch"
    Quotation |o--o{ InvoiceInfo : "linked"
    DeliveryOrder |o--o{ InvoiceInfo : "linked"
    InvoiceInfo ||--o{ InvoiceInfoItem : "lines"
    Quotation ||--o{ EmailDispatch : "about"
    InvoiceInfo |o--o{ EmailDispatch : "attaches"
    DeliveryOrder |o--o{ EmailDispatch : "attaches"

    Quotation {
        bigauto id PK
        string quotation_number UK
        bigauto customer FK "Company"
        string status
        date quotation_date
        date valid_until
        decimal total_without_tax
        decimal total_with_tax
        decimal total_tax
        string pdf_template
        bool requires_confirmation
    }
    InvoiceInfo {
        bigauto id PK
        string invoice_number UK
        bigauto weekly_batch FK
        string kering_group_po_number
        string internal_order
        string sap_cost_center
        decimal net_amount
        decimal tax_amount
        decimal gross_amount
    }
    EmailDispatch {
        bigauto id PK
        bigauto quotation FK
        string status
        string reply_message_id
        text reply_references
        json attachments
        bool esker_sent
        datetime sent_at
    }
    DeliveryItem {
        bigauto id PK
        bigauto delivery_order FK
        uuid asset FK "nullable"
        string serial_number
        int quantity
    }
```

**Key facts for maintainers:**

- `Quotation` is the **hub**: `PurchaseOrder` (OneToOne), `PurchaseReceipt`, `DeliveryOrder`,
  `InvoiceInfo` and `EmailDispatch` all point back at it, and `Asset.source_quotation` closes
  the loop into the asset register.
- `PurchaseOrderItem.quotation_item` is **OneToOne** — a PO line maps to exactly one quotation
  line, which is what keeps `quantity_ordered` vs `quantity_received` reconcilable.
- `unique_asset_per_delivery_order` (UniqueConstraint on `DeliveryItem`) prevents the same
  serialised asset appearing twice on one delivery.
- `uniq_invoice_business_keys` (UniqueConstraint on `InvoiceInfo`) guards the SharePoint batch
  import against duplicate invoice rows; `WeeklyOrderBatch.failed_row_number` /
  `failure_reason` record where a batch stopped.
- `EmailDispatch.reply_message_id` / `reply_references` implement correct threading back into
  the customer's inbox; `esker_sent` tracks the downstream AP hand-off.
- `invoices.WorkflowStatusAudit` is deliberately **FK-free** (`entity_type` + `entity_id`
  strings) so one table can record status transitions for quotations, deliveries and invoices.

### 3.6 Audit (`audit`)

```mermaid
erDiagram
    Company ||--o{ AssetAudit : "scoped to"
    User ||--o{ AssetAudit : "primary_auditor"
    AssetAudit }o--o{ Division : "divisions (M2M)"
    AssetAudit }o--o{ Location : "locations (M2M)"
    AssetAudit }o--o{ AssetCategory : "categories (M2M)"
    AssetAudit }o--o{ User : "auditors (M2M)"
    AssetAudit ||--o{ AssetAuditRecord : "findings"
    Asset ||--o{ AssetAuditRecord : "audited"
    User ||--o{ AuditLog : "actor"
    AuditLog ||--o{ ChangeLog : "field diffs"
    ContentType |o--o{ AuditLog : "generic target"

    AssetAudit {
        uuid id PK
        string audit_number UK
        string audit_type
        string status
        int total_assets_expected
        int total_assets_found
        int total_assets_missing
        int total_discrepancies
        bool report_generated
    }
    AuditLog {
        uuid id PK
        uuid user FK "nullable"
        string action
        int content_type FK "nullable"
        string object_id
        json metadata
        string ip_address
    }
```

`AssetAuditRecord.unique_together (audit, asset)` — one finding per asset per audit.
`AuditLog` is the system-wide trail (generic FK); `ChangeLog` hangs field-level
old/new values off it; `SystemEvent` is a separate severity-graded operational log with a
`resolved` / `resolved_by` / `resolved_at` lifecycle.

> The Kering store inspection deliberately does **not** reuse `AssetAudit`/`AssetAuditRecord` —
> its data contract is richer, so it has its own context (ADR-0011). It does write `AuditLog`
> entries on signoff for traceability.

### 3.7 Reporting (`reports`)

```mermaid
erDiagram
    ReportTemplate ||--o{ GeneratedReport : "instantiates"
    ReportTemplate ||--o{ ReportSchedule : "schedules"
    Company ||--o{ GeneratedReport : "scoped to"
    Company ||--o{ ReportSchedule : "scoped to"
    GeneratedReport ||--o{ ReportShare : "shared via"
    GeneratedReport }o--o{ User : "shared_with (M2M)"
    User ||--o{ ReportShare : "shared_with"

    ReportTemplate {
        uuid id PK
        string code UK
        string report_type
        string output_format
        json template_definition
        json default_filters
        json allowed_roles
        bool requires_approval
        bool can_be_scheduled
    }
    GeneratedReport {
        uuid id PK
        string report_number UK
        json filters_applied
        string status
        string generation_method
        string file_path
        int file_size
        bool requires_approval
        bool approved
        datetime expires_at
    }
    ReportSchedule {
        uuid id PK
        string frequency
        datetime next_run
        json email_recipients
        int consecutive_failures
        int max_failures
        bool is_active
    }
```

`ReportShare.unique_together (report, shared_with)`; approval gating is
`requires_approval` → `approved` / `approved_by` / `approved_at` on `GeneratedReport`.

### 3.8 Store inspection (`inspections`)

```mermaid
erDiagram
    Company ||--o{ StoreInspection : "client"
    Division |o--o{ StoreInspection : "brand"
    Location |o--o{ StoreInspection : "store"
    User |o--o{ StoreInspection : "engineer"
    StoreInspection ||--o{ InspectionDevice : "expected + collected"
    StoreInspection ||--o{ InspectionPhoto : "rack/network/issue"
    StoreInspection ||--o{ InspectionIssue : "findings"
    Asset |o--o{ InspectionDevice : "matches register"
    InspectionDevice |o--o{ InspectionPhoto : "device photos"
    InspectionPhoto |o--o{ InspectionIssue : "evidence"

    StoreInspection {
        uuid id PK
        string jda_code
        string brand_name
        string store_name
        string store_label
        date inspection_date
        string status "planned|in_progress|completed|submitted"
        json device_counts
        json cover_extras
        file report_file
        file photo_zip
        image store_signature
        image engineer_signature
    }
    InspectionDevice {
        uuid id PK
        uuid store_inspection FK
        string client_device_uid "UK within inspection"
        uuid asset FK "nullable - new devices"
        string category
        string sn
        string status
        string intact_asset_tag
        bool is_new_device
    }
    InspectionPhoto {
        uuid id PK
        uuid store_inspection FK
        uuid device FK "nullable"
        string kind
        string client_photo_uid "UK within inspection"
        string display_name
    }
```

**Idempotency is schema-enforced:** `uniq_inspection_device_uid`
(`store_inspection` + `client_device_uid`) and `uniq_inspection_photo_uid`
(`store_inspection` + `client_photo_uid`) are what let the offline-first mini program re-sync
a mutation queue without duplicating rows. `InspectionDevice.asset` is nullable on purpose —
an onsite-new device has no register entry yet.

---

## 4. Unique business keys (quick reference)

| Model | Unique key(s) |
|---|---|
| `Company` | `name`, `code` |
| `Location` | `(company, code)` |
| `CompanyUser` | `(user, company)` |
| `AssetBrand` | `name`, `code` |
| `AssetCategory` | `code` |
| `AssetModel` | `(brand, model_number)` |
| `Asset` | `asset_number`, `barcode` (nullable) |
| `User` | `username`, `employee_id` (nullable) |
| `WeChatIdentity` | `(appid, openid)` |
| `ReceivedEmailMessage` | `(mailbox, direction, external_id)` |
| `Quotation` | `quotation_number` |
| `PurchaseOrder` | `po_number` |
| `PurchaseReceipt` | `receipt_number` |
| `DeliveryOrder` | `delivery_number` |
| `InvoiceInfo` | `invoice_number`, `uniq_invoice_business_keys` |
| `WeeklyOrderBatch` | `batch_id` |
| `AssetAudit` | `audit_number` |
| `AssetAuditRecord` | `(audit, asset)` |
| `ReportTemplate` | `code` |
| `GeneratedReport` | `report_number` |
| `ReportShare` | `(report, shared_with)` |
| `DeliveryItem` | `unique_asset_per_delivery_order` |
| `InspectionDevice` | `(store_inspection, client_device_uid)` |
| `InspectionPhoto` | `(store_inspection, client_photo_uid)` |
| `ProductPrice` | one `is_current` per model; one `is_current` per service item |

---

## 5. Cross-context dependency notes

- `companies` is the root tenant context — `assets`, `quotations`, `audit`, `reports` and
  `inspections` all FK into `Company` (and usually `Division`/`Location`).
- `assets` taxonomy (`AssetCategory`/`AssetBrand`/`AssetModel`) is shared by `products`
  (pricing) and `purchases` (PO lines) — a change there ripples across quoting and buying.
- `accounts.ReceivedEmailMessage` is the entry point of the RFQ pipeline and is FK'd by both
  `quotations.Quotation` and `invoices.EmailDispatch`, tying inbound and outbound mail to the
  documents they produced (ADR-0002).
- `audit.AuditLog` is the only cross-cutting write target: nearly every app records to it via
  the generic FK rather than adding its own trail table.

---

## 6. Regenerating this document

The tables and diagrams were produced from the live app registry. To refresh after a model
change, dump the current schema and diff it against this file:

```python
# python manage.py shell
from django.apps import apps
for model in sorted(apps.get_models(), key=lambda m: (m._meta.app_label, m.__name__)):
    m = model._meta
    print(f"=== {m.app_label}.{model.__name__} pk={type(m.pk).__name__}")
    for f in m.get_fields():
        if getattr(f, 'auto_created', False):
            continue
        print("   ", f.name, type(f).__name__)
```

Then run `python manage.py makemigrations --check` — if it reports changes, this document and
the migrations are out of step.

---

*Last Updated: September 15, 2026*
