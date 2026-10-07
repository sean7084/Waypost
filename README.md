# Waypost

[![Django](https://img.shields.io/badge/Django-5.2.3-green.svg)](https://www.djangoproject.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

Waypost (驿) is a store IT operations platform built with Django. It carries a request the whole way: mailbox RFQ intake and AI classification through quotation, purchase, delivery and invoice dispatch, plus onsite store device inspections captured on a WeChat mini program and rendered back into the client's report format. Multi-language (English/Chinese), 2FA, multi-company scoping, full audit trail.

![Logo](static/images/logo.png)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Test Accounts](#test-accounts)

---

## Overview

Waypost is the operations backbone for an IT services company whose work happens in other people's stores. It ties together three things that used to live in separate tools: the **commercial chain** (RFQ → quotation → purchase → delivery → invoice → dispatch email), the **asset ledger** those orders draw from and deposit into, and the **field reality** engineers verify onsite. Everything shares one company/division/location model, so a store is the same object whether it appears on a quotation, a delivery order or an inspection.

The name is deliberate: a waypost is a marker along a route where status is recorded and handed on. Every entity in the system is exactly that - a `DeliveryOrder` moves `Pending → Dispatched → Delivered`, a `StoreInspection` moves `planned → in_progress → completed → submitted`.

### Key Capabilities

- Multi-company, multi-division asset management
- Additive administrator roles (Superadmin, IT Administrator, Viewer, Order Management Specialist, Order Management Manager)
- 2FA authentication enforcement
- Asset lifecycle tracking (assignment, return, maintenance, disposal)
- Order management workflow from quotation through dispatch and invoice processing
- Mailbox-driven RFQ intake with draft quotation generation, reply drafting, and review queues
- User-configurable mailbox sync and dispatch email support for order-management users
- Stock-first fulfillment workflow with purchase receipt, internal-warehouse checks, and delivery orchestration
- Hardware and service price-list management with category default-model matching for generic RFQs
- **Store device inspection** via an offline-first WeChat mini program: per-device readings and photos, rack/network/speedtest capture, issues, confirmed counts, signatures, then server-side regeneration of the client's `Report Per Store.xlsx` + `Photo/` archive
- Multi-language support (English / Simplified Chinese)
- Bulk import/export (CSV, Excel, PDF)
- Mobile-responsive design

---

## Features

### Core Features

| Feature | Description |
|---------|-------------|
| **Asset Management** | Full CRUD operations, barcode support, photo uploads |
| **Assignment System** | Assign assets to users, locations, or departments |
| **Return Processing** | Track asset returns with condition assessment |
| **Maintenance Tracking** | Schedule and record maintenance activities |
| **Warranty Monitoring** | Automatic warranty status and expiration alerts |
| **Depreciation Calculation** | Built-in depreciation tracking per category |
| **Audit System** | Comprehensive change logging and audit trails |
| **Import/Export** | Bulk import via CSV/Excel, multi-format export |
| **Advanced Filtering** | Filter by status, category, brand, date, location |

### User Management

- Additive admin-role system with persistent role records and multi-role assignment
- Company-division-location based access control
- Authorized RFQ-sender controls on company contacts for mailbox automation
- 2FA enforcement for all users
- Mailbox and order-management settings integrated into the account settings page
- Profile management with avatar support

### Asset Numbering

Three asset numbering modes supported:

1. **Continuity**: Auto-incrementing (e.g., `KCNLP-1`, `KCNLP-2`)
2. **Prefix + SN**: Company code + Serial Number (e.g., `CNSP-{SERIAL}`)
3. **Custom**: Manual import from external systems (e.g., accounting)

### Internationalization

- English (`en`)
- Simplified Chinese (`zh-hans`)
- Language switch on login page and profile settings

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend Framework | Django 5.2.3 |
| Database (Dev) | SQLite |
| Database (Prod) | PostgreSQL |
| Authentication | django-otp (2FA) |
| ORM | Django ORM |
| Frontend | Bootstrap 5, HTML5, CSS3, JavaScript |
| Excel Export | openpyxl |
| PDF Export | WeasyPrint (primary), reportlab (legacy) |
| Internationalization | Django i18n |

### Style Reference

UI follows the **Waypost Design System** - a token-driven blend of Apple Human
Interface Guidelines clarity (restraint, whitespace, one accent) and Jira /
ServiceNow data density (compact tables, clear hierarchy). A single token source
(`static/design/tokens.json`) generates both the web stylesheet and the WeChat
mini program styles so the two surfaces stay identical. The mandatory guide,
component rules and CI-enforced guardrails live in
[`docs/DESIGN_SYSTEM.md`](docs/DESIGN_SYSTEM.md) (see ADR-0012).

### App Responsibilities

| App | Purpose |
|-----|---------|
| `accounts` | User authentication, 2FA, mailbox sync, RFQ classification, profile management, role management |
| `assets` | Asset CRUD, categories, brands, models, assignments, maintenance |
| `companies` | Company, Division, Location, Company-User associations |
| `audit` | System audit logging and compliance tracking |
| `reports` | Analytics and reporting views |
| `dashboard` | Main landing page, workflow dashboard, pending tasks |
| `products` | Unified hardware/service catalog pricing; hardware uses AssetBrand/AssetModel, services use ServiceItem |
| `customers` | Customer profiles (extends Company) |
| `quotations` | Quotation creation, RFQ-linked drafts, PDF generation, attachments |
| `deliveries` | Delivery order (签收单) generation and tracking |
| `invoices` | Invoice info sheets, weekly batch processing, dispatch email threading |
| `accounts` mailbox module | User mailbox settings, inbox/outbox sync, and cached message views |
| `inspections` | Store device inspections: `StoreInspection` / `InspectionDevice` / `InspectionPhoto` / `InspectionIssue`, DRF endpoints for the mini program, and the report + photo-archive generator |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Django 5.2.8

### 1. Create and Activate a Virtual Environment

```bash
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Database Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### 4. Create Superuser (Optional)

```bash
python manage.py createsuperuser
```

### 5. Start Development Server

```bash
python manage.py runserver
```

Access the application at: http://127.0.0.1:8000/

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DJANGO_SETTINGS_MODULE` | Django settings module | `waypost.settings` |
| `SECRET_KEY` | Django secret key | (dev key in settings) |
| `DEBUG` | Debug mode | `True` |
| `TEST_OUTBOUND_EMAIL_OVERRIDE` | Override all outbound email recipients during test runs | `sean.liu@istore-tech.com` |
| `minimax_token_plan_key` | Minimax API token used for RFQ classification and draft generation | empty |
| `MINIMAX_RFQ_API_URL` | Minimax messages endpoint for RFQ workflows | `https://api.minimaxi.com/anthropic/v1/messages` |
| `MINIMAX_RFQ_MODEL` | Minimax model name used for RFQ workflows | `MiniMax-M2.7-highspeed` |
| `MINIMAX_RFQ_TIMEOUT_SECONDS` | Request timeout for RFQ AI calls | `30` |
| `MINIMAX_RFQ_MAX_TOKENS` | Maximum tokens for RFQ AI responses | `800` |

Repository-local `.env` values are loaded automatically by `manage.py`, `waypost/asgi.py`, and `waypost/wsgi.py`. Keep mailbox credentials, Minimax keys, and test-only overrides in that file and out of version control.

### Windows WeasyPrint Runtime

On Windows, WeasyPrint needs native GTK/Pango/GObject DLLs in addition to the Python packages from `requirements.txt`.

If you installed the standalone MSYS2 runtime in the default location, the project now auto-detects these directories during startup:

- `C:\msys64\ucrt64\bin`
- `C:\msys64\mingw64\bin`
- `C:\msys64\clang64\bin`

If your GTK runtime lives elsewhere, set one of these values in the repo-local `.env` file:

```env
WEASYPRINT_DLL_DIRECTORIES=C:\path\to\gtk\bin
```

or:

```env
WEASYPRINT_DLL_DIR=C:\path\to\gtk\bin
```

### Database Configuration

**Development (SQLite):**
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```

**Production (PostgreSQL):**
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'waypost_db',
        'USER': 'waypost_django',
        'PASSWORD': 'waypost_djangopass',
        'HOST': '127.0.0.1',
        'PORT': '5433',
    }
}
```

### Language Settings

Supported languages are configured in `settings.py`:
```python
LANGUAGES = [
    ('en', 'English'),
    ('zh-hans', 'Simplified Chinese'),
]
```

To switch language, use the language selector on:
- Login page (below credentials)
- Profile settings page (bottom navigation)

### 2FA Setup

The system uses `django-otp` for two-factor authentication. Users are enforced to enable 2FA after login.

### Asset Numbering Prefix

Configure `asset_prefix` on each `Company` model to control asset number generation.

---

## Project Progress

### Last three updates

| Version | Date | Focus |
|---------|------|-------|
| v0.1.7 | May 2026 | Separate ServiceItem catalog + direct dispatch + hardware-only asset cleanup |
| v0.1.6 | April 2026 | RFQ mailbox automation + quotation hardening + service items + workflow dashboard stock view |
| v0.1.5 | April 2026 | Multi-role admin controls + order-management mailbox + product price history |

### Current Status (v0.1.7)

#### Fully Operational Features

- User authentication with role-based permissions
- Dashboard with role-filtered statistics
- Company, Division & Location management
- Complete Asset CRUD operations
- Asset Assignment & Return with audit trails
- Audit system with comprehensive change tracking
- Multi-language support (EN/ZH)
- Admin panel with Django styling
- Data export (CSV, Excel, PDF)
- Mobile-responsive Bootstrap 5 design
- Asset list customization (column visibility, filtering)
- **2FA with TOTP** - Full setup and verification workflow
- **Advanced Reporting** - Charts and analytics dashboard with Chart.js
- **Enhanced Mobile Features** - Barcode scanning with Html5-QRCode
- **REST API** - Full API endpoints at /api/v1/ with documentation at /docs/
- **Quotation & Invoice Management System** - End-to-end quotation -> purchase -> delivery -> invoice -> dispatch workflow
- **RFQ Mailbox Automation** - Inbox messages can be classified, reprocessed, linked to quotations, and surfaced as pending review work
- **Authorized RFQ Sender Controls** - Company contacts can be flagged as approved sources for automatic quotation drafting
- **Quotation Review & PDF Flow** - Quotations now store attention email, source RFQ linkage, review-required drafts, and create-download PDF behavior
- **Separate Service Catalog** - Services now live in `products.ServiceItem`, while `products.ProductPrice` remains the unified price source for both hardware and services
- **Unified Catalog Selection** - Price list and quotation forms support labeled hardware/service rows, type filtering, and dedicated service-item edit flows
- **Direct Dispatch Fulfillment** - Confirmed quotations can create delivery orders immediately when hardware stock fully matches, and service-only quotations skip purchase-order creation
- **Service-aware Delivery Workflow** - Delivery orders auto-add service lines, track them via `quotation_item`, and use `Pending -> Dispatched -> Delivered` without the old `Prepared` step
- **Hardware-only Asset Surfaces** - Asset forms, list/detail/edit/export/stats, and brand/model pages exclude service-category records
- **RFQ Mixed Catalog Matching** - RFQ matching and quotation snapshots use catalog display helpers so service rows no longer depend on hardware-only fields
- **Default Model Matching** - Categories can define default asset models so generic RFQ requests still draft useful quotation items
- **Workflow Dashboard Stock Overview** - Stock overview is consolidated into the workflow dashboard and replaces the standalone stock page
- **Responsive Workflow Board** - Workflow dashboard columns auto-fit smaller screens and confirmed quotations share the quotation lane
- **Internal Warehouse Fulfillment Rules** - Purchase receipts and dispatch asset selection now enforce the internal warehouse path used by current operations
- **Repo-local .env Loading** - Runtime bootstrap loads local `.env` values for mailbox, RFQ AI, and test outbound-email overrides
- **LibreOffice-free Document Generation** - Quotation and Delivery PDFs rendered directly from HTML templates via WeasyPrint
- **Template Fidelity Tuning** - Excel-style quotation and 签收单 layouts tuned against reference PDF output
- **Asset Create Batch Flow** - Create multiple assets in one submission with per-row serial/status input
- **Searchable Create Controls** - Search-enabled dropdowns on asset create/edit for category, brand, model, and location
- **Brand-Scoped Model Selection** - Model options on asset create/edit are filtered by selected brand
- **Expanded Template i18n Coverage** - Placeholder/ARIA/title attribute strings wrapped and translated in zh-cn templates
- **Admin Reliability Fixes** - Restored blank admin changelist overrides for company models
- **Safe Model Deletion Handling** - Graceful protected-reference errors for asset model delete operations
- **Warehouse Slot Structure** - Zone/rack/shelf support added to locations and assets, with warehouse-only validation
- **Advanced Asset List Workflow** - Grouped non-serialized rows, drill-down mode, and row-level navigation
- **Batch Edit Operations** - Bulk edit endpoint/UI supports partial-quantity updates with slot-aware validation
- **Create-Form Model Autofill** - Selecting model now auto-syncs category and brand
- **Duplicate Serial Hardening** - Duplicate serials blocked in batch create frontend and backend checks
- **Export Data Reliability** - Export uses accessible assets scope and current location display helpers
- **Company User Edit Flow** - Users list edit action is fully wired with matching URL converter type
- **Company CSV Import Workflows** - Companies, locations, and company contacts now support sample CSV, preview, and confirm import pages
- **Import Rollback Protection** - Latest import per user/module can be rolled back for companies, locations, company contacts, and assets
- **Import Result Dashboard** - Shared result page now shows total, processed, created, updated, skipped, and error counts plus row-level issues
- **Company Contacts Model** - Company contacts now support recipient-first records with optional linked auth users and reusable contact helpers
- **Expanded Location Schema** - Locations now support bilingual names, secondary codes, Chinese address, and linked company contacts
- **Location Duplicate Update Mode** - Location import can update matched entries instead of only skipping duplicates
- **Count Accuracy Fixes** - Company-contact and location list badges/cards now display full totals instead of page-size counts
- **Multi-Role Admin System** - Users now support additive administrator roles with persistent role records and admin/API/template compatibility helpers
- **Order Management Access Gating** - Navigation and direct workflow URLs are restricted to authorized order-management users
- **Asset Change Logs** - Asset-related audit records now live under Assets with clickable list/detail pages and import/export coverage
- **Audit Dashboard Split** - Asset audits are separated into Dashboard, New Audit, and History views with improved back-navigation and detail context
- **Product Price Lifecycle Tracking** - Unified prices enforce exactly one catalog target (`model` or `service_item`), preserve inactive history, and keep current rows unique per catalog item
- **Stable Product Model Selection** - Product add/edit uses a native filtered selector to avoid browser-specific issues with custom dropdown widgets
- **Mailbox Module** - Order-management users can configure IMAP/POP3 receive plus SMTP send settings, browse inbox/outbox views, and sync messages from settings-driven windows
- **Automatic Mail Sync** - Mailboxes can auto-sync every 5 minutes during local `runserver`, with in-process locking to avoid SQLite contention
- **Settings Consolidation** - Account settings now include mailbox controls and 2FA management actions in one place
- **Workflow Hardening** - Quotation contact fallbacks, mailbox sync locking, admin edit validation, and translated admin-role display issues were fixed

#### Database Schema

- `accounts.User` - Custom user with roles and 2FA
- `companies.Company` - Organization management
- `companies.Division` - Department/division within company
- `companies.Location` - Physical locations with hierarchy
- `companies.CompanyUser` - Company contacts with optional linked user associations
- `companies.ImportRun` - Import execution ledger for rollback tracking
- `companies.ImportRunChange` - Per-record rollback snapshot history
- `assets.AssetCategory` - Asset categorization
- `assets.AssetBrand` - Brand/manufacturer management
- `assets.AssetModel` - Hardware models within brands
- `assets.Asset` - Main asset model with full lifecycle
- `assets.AssetAssignment` - Assignment history tracking
- `assets.AssetMaintenance` - Maintenance scheduling and records
- `products.ServiceItem` - Standalone catalog entries for non-asset service offerings
- `products.ProductPrice` - Unified price list for hardware models and service items
- `audit.*` - Audit logging models

### v0.1.7 Migration Notes

Run database migrations before deploying v0.1.7:

```bash
python manage.py migrate
```

New migration files included in this release:

- `deliveries/migrations/0002_deliveryitem_quotation_item_and_service_lines.py`
- `products/migrations/0003_serviceitem_alter_productprice_options_and_more.py`
- `products/migrations/0004_migrate_service_prices_to_service_items.py`
- `quotations/migrations/0006_quotationitem_service_item.py`
- `quotations/migrations/0007_backfill_quotationitem_service_item.py`

---

## TODO

1. for do pages like http://127.0.0.1:8000/en-us/deliveries/16/, we should able to edit the fields
2. for po, rename status ordered to purchasing
3. for do creation http://127.0.0.1:8000/en-us/deliveries/create/from-quotation/60/, delivery method should be a droplist of: 送货上门，快递运输，订货方上门自取。 and default to 送货上门
4. when an asset is dispatched, update its status
5. on pages like http://127.0.0.1:8000/en-us/deliveries/20/, keep the upload button and rename it to upload more once we uploaded a file, and rename mark delivered button to upload and mark delivered while also adding the upload function to it

for page http://127.0.0.1:8000/en-us/invoices/invoice-info/, we need this page, but need to build from scratch:
1. for each quotation and delivery order combo, we need to import the invoice combo, refer to the ofd, pdf and zip files under template_files
2. we then need to extract the total with customer name, tax, total without tax, tax, created date and invoice number from the pdf or xml file in the zip. it is 
古驰（中国）贸易有限公司, 1210.23, 139.23, 1071
2026年04月13日 and 26312000002243628076 for the sample files. we can use regex to extract these information. we will then match these file combos with the info to our existing quotations.



### jd
加入顺丰京东快递api for dispatching and delivery tracking


### High Priority

- [ ] **Enhanced Mobile Features** - Barcode scanning and offline capability
- [ ] **REST API Development** - API endpoints for mobile integration

### Medium Priority

- [ ] **Performance Optimization** - Query optimization and caching
- [ ] **Depreciation Enhancement** - Detailed depreciation reports
- [ ] **Workflow Automation**

### Future Features

- [ ] **WeChat Mini Program Integration**
  - Device audit/inspection for admins and field engineers
  - Quick item lookup (admins)
  - Quick item addition (admins)
  - Barcode scanning support

- [ ] **Write-off Processing** - Formal asset disposal workflow
- [ ] **Bulk Assignment/Return Reports** - Report generation for bulk operations
- [ ] **Photo-based Audit** - Audit with updated asset photos, SN, asset numbers
- [ ] **Grouped Auditing** - Audit by company and location

## Order Management System

### Overview

A complete business workflow for managing quotations, purchase orders, deliveries, and invoices, integrated with the asset management system. Hardware lines are purchased and received into inventory as assets, while service lines stay in a separate catalog and can flow through quotation and delivery without creating asset records.

### Future Reference Summary

- Scope delivered in v0.1.0: end-to-end workflow from quotation creation to client dispatch and Esker forwarding.
- Scope delivered in v0.1.1: quotation and delivery document rendering migrated to direct HTML PDF generation with template fidelity tuning.
- Scope delivered in v0.1.2: asset create UX modernization (searchable selects, brand-filtered models, batch creation), broader template attribute translation coverage, and admin/delete stability hardening.
- Scope delivered in v0.1.3: warehouse slot schema + UI integration, grouped asset listing with batch edit workflow, export/access-scope fixes, and company user edit-route completion.
- Scope delivered in v0.1.4: preview-confirm CSV import flows for companies/locations/contacts, shared import-result reporting, rollback tracking for imports, and company-contact/location data model expansion.
- Scope delivered in v0.1.5: multi-role admin migration, order-management permission gating, product price history/derived-model behavior, and user-configurable mailbox inbox/outbox workflows with auto-sync.
- Scope delivered in v0.1.6: mailbox RFQ classification/reprocessing with authorized-sender gating, separate `ServiceItem` catalog pricing, service-aware quotation and delivery handling, workflow-dashboard stock consolidation, and repo-local `.env` runtime loading.
- Scope delivered in v0.1.7: dedicated service catalog adoption, direct-dispatch fulfillment for stock-covered quotations, service-aware delivery lines, and hardware-only cleanup across asset-facing pages.
- Core workflow stages: quotation (draft/sent/confirmed) -> direct dispatch or purchase conversion and receipt -> delivery dispatch and signed completion -> invoice batch import/recalculation/document generation -> email dispatch tracking.
- Key data extensions: `products.ProductPrice`, `customers.CustomerProfile`, `quotations.Quotation*`, `purchases.PurchaseOrder*`, `deliveries.DeliveryOrder*`, `invoices.WeeklyOrderBatch`, `invoices.InvoiceInfo*`, `invoices.EmailDispatch`, `invoices.WorkflowStatusAudit`.
- Key automation paths: quotation HTML-PDF generation, delivery HTML-PDF generation, invoice information template generation with PDF fallback, bulk weekly Sharepoint import, mailbox inbox/outbox sync, RFQ classification/draft quotation generation, and SMTP dispatch caching.
- Workflow governance: dashboard Kanban with integrated stock overview, pending-task notifications, cross-entity workflow search, and status badge standardization.
- Operational validation baseline: full smoke transition run verified quotation -> purchase -> delivery -> invoice -> dispatch transitions in Django test client flow.

### Integration with Asset Management

The system leverages existing Waypost infrastructure:

| Existing Component | Used For |
|-------------------|----------|
| `assets.AssetBrand` | Product brands (repurposed for product catalog) |
| `assets.AssetModel` | Hardware product models |
| `products.ServiceItem` | Service catalog entries kept outside the Assets app |
| `products.ProductPrice` | Unified price list for both hardware and services |
| `companies.Company` | Customer companies |
| `customers.CustomerProfile` | Customer contacts and delivery defaults (attn, tel, address) |
| `assets.Asset` | Purchased products added as assets |
| `assets.AssetAssignment` | Dispatch to stores/offices |

### Data Models

#### Catalog and Pricing

Hardware catalog entries continue to use `AssetBrand` and `AssetModel`. Service offerings are stored separately in `products.ServiceItem`. `products.ProductPrice` remains the unified pricing layer for both catalog types.

| Model | Fields |
|-------|--------|
| `ServiceItem` | `service_group`, `name`, `description`, `unit`, `is_active` |
| `ProductPrice` | `brand`/`model` for hardware or `service_item` for services, `unit`, `price_without_tax`, `price_with_tax`, `tax_rate`, `is_current` |

#### Customers (extends existing Company)

| Model | Fields |
|-------|--------|
| `CustomerProfile` | `company` (OneToOne→Company), `contact_person`, `phone`, `email`, `delivery_address`, `delivery_city`, `delivery_contact`, `delivery_phone`, `delivery_method`, `tax_id` |

#### Quotations

| Model | Fields |
|-------|--------|
| `Quotation` | `quotation_number`, `customer`, `quotation_date`, `valid_until`, `attn`, `tel`, `status`, `total_without_tax`, `total_with_tax`, `notes` |
| `QuotationItem` | `quotation` (FK), `brand`, `product_description`, `user_brand`, `user_name`, `unit`, `quantity`, `price_without_tax`, `price_with_tax`, `tax_amount`, `line_total` |
| `QuotationAttachment` | `quotation` (FK), `attachment_type` (invoice_pdf/invoice_ofd/invoice_xml/email_confirm), `file`, `uploaded_at`, `notes` |

#### Deliveries

| Model | Fields |
|-------|--------|
| `DeliveryOrder` | `delivery_number`, `quotation`, `delivery_date`, `收货人`, `电话`, `交货地址`, `交货方式`, `status`, `signed_file`, `remarks` |
| `DeliveryItem` | `delivery_order` (FK), `asset` (FK→Asset), `serial_number`, `brand`, `product_description`, `user_brand`, `user_name`, `quantity` |

#### Invoices

| Model | Fields |
|-------|--------|
| `WeeklyOrderBatch` | `batch_id`, `sharepoint_file`, `uploaded_at`, `processed_at`, `status` |
| `InvoiceInfo` | `invoice_number` (yymmdd+##), `invoice_date`, `payment_due_date`, `bill_to`, `kering_group_po_number`, `internal_order`, `sap_cost_center`, `total_amount`, `net_amount`, `tax_amount`, `gross_amount`, `tax_rate`, `weekly_batch` (FK) |
| `InvoiceInfoItem` | `invoice_info` (FK), `description`, `unit_price`, `quantity`, `total_price`, `net_amount`, `tax_amount`, `gross_amount` |
| `EmailDispatch` | `quotation` (FK), `sent_to`, `cc`, `sent_at`, `esker_sent`, `esker_sent_at`, `attachments` (JSON), `status` |

### Template Files

| Template | Purpose | Key Fields |
|----------|---------|------------|
| `invoice information template.xlsx` | Invoice info sheet | Bill To, PI Number, Invoice Date, Due Date, amounts, PO Number, SAP Cost Center, line items |
| `quotations/pdf_excel_style.html` | Quotation PDF (active) | quote header, line items, totals, remark, signature blocks |
| `deliveries/pdf_excel_style.html` | Delivery PDF (active) | 订货方, 收货人, 电话, delivery items, delivery info, signature blocks |

---

---

## Test Accounts

| Username | Password | Role |
|----------|----------|------|
| `testuser` | `testpass123` | Viewer |
| `admin` | `hjadmpass` | Superadmin |

### Database Credentials (PostgreSQL)

| Field | Value |
|-------|-------|
| Database | `waypost_db` |
| Username | `waypost_django` |
| Password | `waypost_djangopass` |
| Host | `127.0.0.1:5433` |

---

## Development Guidelines

1. **Coding Standards**
   - Django best practices
   - Extensive comments for maintainability
   - Bootstrap 5 responsive design

2. **Environment**
   - Use the repo-local virtual environment (`.venv`)
   - SQLite for development, PostgreSQL for production
   - Docker deployment planned

3. **Branching**
   - Work in feature branches
   - Commit message convention: clear, descriptive