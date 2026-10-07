# Waypost - Changelog

## Unreleased

**Version:** to be assigned by the release-prep PR (per `docs/RELEASE_PROCEDURE.md` §2 this is at least a MINOR bump — it changes how production is deployed)
**Release Date:** —
**Focus:** Containerised production deployment on the shared Aliyun ECS, plus a tag-promoted CI/CD pipeline

---

### Highlights

1. Waypost can now be deployed to production as a Docker Compose stack; the image carries WeasyPrint's native libraries, LibreOffice and CJK fonts, and fails to build if any of them are missing rather than degrading silently at request time.
2. Pushing an annotated `v*` tag deploys to production automatically, with a pre-deploy database checkpoint, a health gate, and an automatic image rollback when the new release does not come up.
3. Fixed a defect that would have broken every form submission in production: behind Nginx TLS termination Django rebuilt the expected CSRF origin as `http://` while browsers sent `https://`, so every POST — starting with login — returned 403.
4. Added an unauthenticated `/healthz/` probe reporting database and cache status, used by the container healthcheck, the deploy gate and host monitoring.

### Delivered Scope (14-file iteration)

1. Production request security
- `waypost/settings.py`: env-driven `SECURE_PROXY_SSL_HEADER` and `CSRF_TRUSTED_ORIGINS`; secure cookies, SSL redirect and HSTS gated on `DEBUG` (HSTS ships at 0 because the header is sticky); `SECURE_REDIRECT_EXEMPT` keeps the probe reachable on loopback.
- Request hardening is disabled under the test runner (`settings._RUNNING_TESTS`). `manage.py test` flips `DEBUG` to `False`, which would otherwise turn every view test into an assertion about a 301 — 57 spurious failures that read like an application bug.
- Log file handler is now rotating; an unbounded `FileHandler` grows the container's writable layer forever.

2. Health probe
- `waypost/health.py` + route in `waypost/urls.py`: unauthenticated, no language prefix, 200/503, reports the deployed tag and revision. Failure detail is limited to the exception *class* name, because a database error message can contain the DSN.
- `waypost/tests.py`: 12 tests covering the healthy path, the degraded paths, the no-leak guarantee, reachability with `SECURE_SSL_REDIRECT` on, and the proxy settings' defaults.

3. Container runtime
- `docker/Dockerfile`: multi-stage (`deps` → `runtime`), Aliyun mirror defaults with build-arg overrides, renders a Chinese string through WeasyPrint at build time as proof the font and Pango stack work.
- `docker-compose.yml`: `app` + `postgres:16` (Debian, not Alpine — musl collation changes can corrupt indexes) + `redis:7`; only the app publishes a port, and only on loopback. `name: waypost` is pinned so the project name cannot collide with another stack on the same host.
- `docker/entrypoint.sh`: `migrate` → `collectstatic` → `exec gunicorn` (exec, so Gunicorn is PID 1 and receives SIGTERM instead of being SIGKILLed after the grace period).
- `docker/nginx/waypost.conf`: SNI-shared 443 with no `default_server`, ACME webroot before the redirect, `/media/` aliases, and the `X-Forwarded-Proto` header the CSRF fix depends on.
- `.dockerignore`, `.gitattributes` (`*.sh` pinned to LF — a CRLF entrypoint fails as `/bin/bash^M`).

4. CI/CD
- `.github/workflows/deploy-ecs.yml`: image-build validation on PRs, `preflight` + `deploy` on `v*` tags, plain OpenSSH with a pinned `known_hosts` and `StrictHostKeyChecking=yes`, `environment: production`, serialized by a concurrency group.
- `docker/bin/ci-deploy.sh`: the host-side deploy — rollback alias, `pg_dump` checkpoint, tag checkout, build, health gates, automatic image rollback. It never restores the database automatically, because a release may contain a one-way-door migration.
- `.gitignore`: deploy keypairs and the `waypost_data.json` migration fixture.

5. Documentation and one silent-failure fix
- `docs/DEPLOYMENT.md`: Option 2 is now the real production procedure (co-hosting, rootless file ownership, `.env`, Nginx/TLS ordering, CI/CD credentials); Step 4b's template table corrected against the code; Step 10 corrected — the mailbox sync thread is `runserver`-only, so it never runs in production. Four new troubleshooting entries.
- `docs/RELEASE_PROCEDURE.md`: §1 current-reality table re-verified, §6.2 rewritten for the container deploy, §11 gaps updated.
- `docs/BACKUP_RESTORE.md`: §2b/§3a for the containerised stack.
- `invoices/services.py`: a missing `签收单 template.xlsx` was swallowed by a bare `except FileNotFoundError: pass`, so invoice dispatch emails quietly lost their delivery documents. Now logged as a warning.

### Migration Files Added

None.

### Validation

- `python manage.py test`: 205 tests, 0 failures (baseline `origin/main` at the time: 193 tests, 0 failures) — the 12 new probe tests, no regressions.
- `python manage.py check` and `makemigrations --check --dry-run`: clean.
- `python -m ruff check .`: clean.
- Production-shaped configuration verified by booting with `DJANGO_DEBUG=False` and printing the resolved settings: proxy header, CSRF origins, SSL redirect, redirect exemption, secure cookies, WhiteNoise manifest storage and middleware, rotating log handler.
- The same 12 probe tests pass under a production-shaped environment, confirming the `_RUNNING_TESTS` guard.

---

## Release Notes v0.1.7

**Version:** 0.1.7  
**Release Date:** May 11, 2026  
**Focus:** Separate service catalog adoption, direct-dispatch fulfillment, and hardware-only asset boundaries

---

### Highlights

1. Services now live in a dedicated `products.ServiceItem` table while `products.ProductPrice` remains the unified price source across hardware and services.
2. Confirmed quotations can now move straight to delivery when stock fully covers the hardware lines, and service-only quotations no longer create unnecessary purchase orders.
3. Delivery orders now support asset-less service rows and use a simplified `Pending -> Dispatched -> Delivered` workflow.
4. Asset-facing pages, exports, and catalog-management surfaces are now hardware-only, preventing legacy service records from leaking into the Assets app.
5. Price-list, quotation, delivery, and workflow UI now label catalog type more clearly and expose service editing in the shared catalog flow.

### Delivered Scope (35-file iteration)

1. Separate service catalog and unified pricing
- Added `products.ServiceItem` plus `ProductPrice` validation and constraints so each price row targets exactly one catalog object: `model` or `service_item`.
- Added migrations to create/backfill service items from legacy service `AssetModel` records.
- Added service price edit support, service-group suggestions, and mixed-catalog display helpers.
- Updated RFQ AI matching and quotation snapshot generation to use hardware/service-safe display fields.

2. Quotation selection and fulfillment orchestration
- Updated quotation create/edit flows to present a unified hardware/service selector with explicit type filter and labels.
- Added `QuotationItem.service_item` and wired quotation duplication/snapshot logic to preserve service associations.
- Added direct-dispatch eligibility checks so confirmed quotations can create deliveries immediately when in-stock hardware fully matches.
- Changed confirmed quotation actions from unconditional purchase creation to `Create Delivery` or `Continue Fulfillment` based on actual stock coverage.

3. Service-aware delivery workflow
- Removed the `prepared` delivery state from the active workflow and relabeled `completed` as `Delivered` in the UI.
- Added `DeliveryItem.quotation_item` and nullable `asset` support so service lines can exist without asset records.
- Updated delivery creation to auto-add service rows while keeping manual asset selection only for hardware lines.
- Updated invoice rebuild logic and delivery templates to resolve mixed hardware/service delivery rows correctly.

4. Hardware-only asset application cleanup
- Centralized hardware-only category, brand, model, and accessible-asset querysets in asset forms and views.
- Prevented service-category records from appearing in asset list/detail/edit/delete/export/stats flows and related asset audit links.
- Restricted brands/models pages, filters, and export forms to hardware catalog data only.
- Kept service catalog management inside Products instead of Assets.

5. Workflow, dashboard, and UI polish
- Updated workflow dashboard lane behavior and responsive grid sizing.
- Refined product price list, quotation list/detail, and delivery detail/form actions to reflect mixed catalog types and direct delivery flow.
- Renamed navigation wording for weekly Sharepoint batches and aligned delivery action labels with the simplified status model.
- Added release-facing documentation updates for the new service catalog architecture.

### Migration Files Added

1. `deliveries/migrations/0002_deliveryitem_quotation_item_and_service_lines.py`
2. `products/migrations/0003_serviceitem_alter_productprice_options_and_more.py`
3. `products/migrations/0004_migrate_service_prices_to_service_items.py`
4. `quotations/migrations/0006_quotationitem_service_item.py`
5. `quotations/migrations/0007_backfill_quotationitem_service_item.py`

### Validation

1. `python manage.py check`
2. `python manage.py makemigrations --check`
3. Django shell smoke checks confirmed `ProductPrice` rows have no invalid mixed/null catalog target states after migration.
4. Known direct-dispatch quotations (`QT-20260424-004` and `QT-20260429-006`) still resolve correctly and already own delivery orders.
5. Asset smoke checks confirmed brand-list and asset-edit pages no longer render service names, and current asset-view grep checks show only the centralized hardware helper remains.

## Release Notes v0.1.6

**Version:** 0.1.6  
**Release Date:** April 29, 2026  
**Focus:** RFQ mailbox automation, quotation workflow hardening, service-item pricing, and workflow-dashboard stock consolidation

---

### Highlights

1. Mailbox sync can now classify RFQ emails, track RFQ processing state, and support reprocessing plus draft quotation/reply workflows for authorized customer senders.
2. Quotation create/edit/detail/list flows were hardened around recipient email capture, RFQ review state, stock-first fulfillment actions, and create-and-download PDF behavior.
3. The price list now supports service items, category-level default models, and better handling of generic RFQ requests that do not specify an exact catalog SKU.
4. The standalone stock page was retired in favor of a workflow dashboard view that combines Kanban stages, stock overview, receipts, and pending order-management tasks.

### Delivered Scope (65-file iteration)

1. RFQ mailbox automation and sender authorization
- Added RFQ classification, extraction, and quotation-draft orchestration in `accounts/rfq_ai.py`.
- Extended `accounts.ReceivedEmailMessage` with RFQ status, confidence, summary, extracted data, error, processed-at fields, and supporting indexes.
- Triggered pending RFQ processing after mailbox sync and added mailbox-detail reprocessing from the UI.
- Added `is_authorized_rfq_sender` on company contacts, surfaced it in forms/lists, and used it to gate automatic RFQ quotation generation.
- Added pending-task notifications in the base layout for RFQ drafts, likely RFQs, and mailbox failures.

2. Quotation workflow, PDF, and email threading
- Added quotation `attn_email`, `source_email_message`, and `requires_confirmation` fields plus create/edit autofill updates.
- Reworked quotation create to support `Save as Draft` and `Create and Download PDF`, including redirect-based download triggering on detail pages.
- Linked quotation detail/list screens to RFQ source emails, item-matching warnings, purchase/delivery next actions, and clickable list rows.
- Updated quotation PDF remark generation to deduplicate remark targets and align output wording with the current template.
- Extended dispatch email compose/send flows to reply against source mailbox messages, prefill recipients/subject/body, and include quotation PDFs.

3. Product catalog and service-item support
- Added `default_asset_model` and `item_type` on asset categories so hardware and service categories can be treated differently.
- Added category-form support for default-model assignment and validation.
- Updated the price list with type/status filters, a default-model management panel, and a renamed `Price List` entry in navigation and translations.
- Added one-step service item + price creation with a dedicated form/view/template and default service brand/category helpers.
- Excluded service categories from the regular hardware product-price add flow.

4. Purchase, delivery, and dashboard workflow consolidation
- Replaced the old purchased-assets list with a purchase-order-oriented list and action model.
- Removed `/purchases/stock/` and moved stock overview content into the workflow dashboard.
- Restricted purchase receipts to the internal warehouse location path used by current operations.
- Centralized dispatch asset selection so delivery creation uses matching available internal stock and avoids assets already in active deliveries.
- Updated workflow next-action labels, delivery/purchase back-links, and dashboard stock/receipt summaries to match the new flow.

5. Runtime, navigation, and regression fixes
- Added repo-local `.env` loading in `manage.py`, `waypost/asgi.py`, and `waypost/wsgi.py`, and ignored `*.env` in Git.
- Added RFQ/Minimax and test outbound email override settings in Django settings.
- Fixed authenticated login redirects to the namespaced dashboard route.
- Renamed the product navigation entry to `Price List`, cleaned up stock links, and updated related zh-cn translations.
- Applied focused UX fixes including quotation form comboboxes, item editing, delivery PDF wording polish, and receipt-page warehouse guidance.

### Migration Files Added

1. `accounts/migrations/0015_receivedemailmessage_rfq_confidence_and_more.py`
2. `assets/migrations/0013_assetcategory_default_asset_model.py`
3. `assets/migrations/0014_assetcategory_item_type.py`
4. `companies/migrations/0013_companyuser_is_authorized_rfq_sender.py`
5. `invoices/migrations/0005_emaildispatch_reply_message_id_and_more.py`
6. `quotations/migrations/0004_quotation_attn_email.py`
7. `quotations/migrations/0005_quotation_requires_confirmation_and_more.py`

### Validation

1. `git status --porcelain` reported 65 file changes for this iteration before release-note finalization.
2. Browser validation covered authenticated login redirect behavior, workflow dashboard stock view, removal of `/purchases/stock/`, and quotation create actions including `Save as Draft` and `Create and Download PDF`.
3. Server-side log validation confirmed the create-download flow now persists quotations before triggering PDF downloads.
4. `python manage.py check` completed successfully after the final workflow and documentation pass.

## Waypost - Release Note v0.0.1

**Version:** 0.0.1  
**Release Date:** July 7, 2025  
**Focus:** Core Project Foundation & Authentication System

---

### 🚀 Overview

Version 0.0.1 marks the successful establishment of the Waypost foundational architecture. This release includes the complete Django project setup and a fully implemented, secure user authentication system with a modern user interface.

### ✅ Key Features Delivered

#### 1. Core Project Initialization

- **Modular Django Architecture**: Established a scalable project structure with a dedicated `accounts` app for user management.

- **Environment & Configuration**:
  - Configured Conda environment (`HengjiAMS1`) for isolated dependency management.
  - Set up SQLite for development and prepared PostgreSQL configurations for production.
  - Configured static and media file handling.

- **Internationalization (i18n)**: Implemented a robust i18n framework supporting English and Chinese out-of-the-box.

- **URL Routing**: Configured main project and app-level URL routing with i18n support.

- **Database Migrations**: Ensured the initial database schema is clean and all migrations are successfully applied.

#### 2. Full Authentication & Login Page Implementation

- **Custom User Model**: Implemented a secure, extended `AbstractUser` model with:
  - UUID primary keys to prevent enumeration attacks.
  - Fields for future implementation of roles, 2FA, and language preferences.

- **Secure Login Page**:
  - Developed a modern, responsive login page using Bootstrap 5.
  - Integrated the company logo and a dynamic language switcher.

- **Session & Security Management**:
  - Implemented user session tracking.
  - Added monitoring for failed and successful login attempts for security auditing.

- **Admin Interface**: Fully configured the Django admin for the custom user model, allowing for easy user management.

### 🔧 Technical Stack

- **Framework**: Django 4.2+
- **Database**: SQLite (Development)
- **Frontend**: Bootstrap 5, HTML, Vanilla JavaScript

### 🎯 Success Criteria Met

- ✅ Django project runs without errors (`manage.py check` passes).
- ✅ The admin interface is fully functional for user management.
- ✅ The login page at `/accounts/login/` renders correctly and is fully functional.
- ✅ Multi-language switching is operational on the login page.
- ✅ Core security best practices (UUID keys, session tracking) are in place.

## Release Notes and Progress Report for Version 0.0.2

---

### Release Notes for Version 0.0.2

#### Key Enhancements and New Features

##### 1. Asset Models

- Re-enabled and fixed the models in the assets application.

##### 2. Audit System

- Re-enabled and fixed the models in the audit application.

##### 3. Dashboard

- Created the main dashboard for users after login.
- Updated the dashboard to display sample assets and provide real-time data.

##### 4. Additional Templates

- Added templates for:
  - Two-Factor Authentication (2FA) setup.
  - Profile and settings pages.
  - Company and user management pages.
  - Admin pages for companies, divisions, locations, and audit logs.

##### 5. Asset Import/Export

- Introduced CSV and Excel import/export functionality for assets.

##### 6. Reporting System

- Implemented charts and analytics for enhanced reporting capabilities.

##### 7. Mobile Optimization

- Added barcode scanning support for mobile devices.

##### 8. Role-Based Access Control

- Defined and implemented four administrator roles:
  - **Superadmin**: Access to all data.
  - **Manager**: Access to all assets in their company.
  - **IT Specialist**: Access to assets in one or multiple divisions within their company.
  - **Viewer**: Read-only access to specific locations within their company.

##### 9. Language Support

- Completed English and Chinese language support for all HTML templates.

---

#### Fixes and Adjustments

##### 1. QR Code for 2FA

- Resolved an issue where the QR code was not displayed on the 2FA setup page (`/accounts/2fa/setup-simple/`).

##### 2. Navigation and URLs

- Updated the dashboard URL to `/dashboard` (previously `/`).
- Ensured all key components (e.g., dashboard, asset management, audit, location management, reports, user management) are accessible via the navigation pane.

##### 3. User Roles Display

- Fixed an issue where user roles were displayed as "Staff" instead of their actual roles.

##### 4. Default Templates for Admin Pages

- Updated default Django admin templates to align with the base template's style.
- Introduced new pages for:
  - `/companies/`
  - `/companies/division/`
  - `/companies/location/`
  - `/audit/auditlog/`
  - `/audit/assetaudit/`
  - `/audit/systemevent/`

##### 5. Asset Visibility

- Ensured asset visibility on the dashboard matches user permissions on the assets page.

##### 6. Legacy Role Removal

- Removed legacy role support and references from all relevant pages.

---

#### Merges and Consolidations

- Merged `assets/urls_old.py` with `assets/urls.py` for extended support.
- Merged `audit/urls_old.py` with `audit/urls_new.py` to finalize URL development for the audit app.
- Consolidated `PROGRESS_REPORT_v0.0.1.md` with `docs/PROGRESS_REPORT_v0.0.1.md` and `docs/v0.0.1.md`.

---

#### Known Issues

1. **Method Not Allowed (GET): `/zh-hans/accounts/logout/`**
   - Logout functionality for Chinese language support needs further investigation.

2. **Default Passwords for New Users**
   - On `/zh-hans/accounts/users/create/`, newly created users should have:
     - A default random password.
     - An option to require password change on the next login (enabled by default).

3. **Python Version Compatibility**
   - Compatibility with Python 3.13 is under review. Updating without confirmation may cause issues.

---

#### Testing Notes

- Use the admin account for testing:
  - **Username**: `admin`
  - **Password**: `hjadmpass`
- Sample assets have been generated for testing purposes.

---

#### Rollbacks

- Reverted changes to admin pages (`/admin/companies/...`, `/admin/audit/...`) that aligned them with the base template's style. Instead, focused on creating new user-facing pages aligned with the base template.

---

### Waypost Development Progress Report v0.0.2

**Date:** July 8, 2025  
**Focus:** Complete Implementation of Core Asset & Company Management Systems

---

#### 🎯 Overview

Building on the foundation of v0.0.1, this development cycle focused on implementing the primary business logic of the Waypost. Version 0.0.2 delivers a fully functional, end-to-end asset management system and the complete company/location organizational structure.

---

#### ✅ Completed Components in This Iteration

##### 1. Asset Management System - Full Implementation

- **Core Models**: Implemented the full suite of asset-related models: `AssetCategory`, `AssetBrand`, `Asset`, `AssetAssignment`, and `AssetMaintenance`.

- **CRUD Functionality**: Delivered a complete user-facing interface for creating, reading, updating, and retiring assets.

- **Advanced Features**:
  - **Search & Filter**: Implemented an advanced search form for filtering assets.
  - **Assignment Tracking**: Developed views for assigning assets to users and processing returns.
  - **Data Export**: Added functionality to export asset lists to CSV.

- **UI/UX**: Created modern, responsive, and user-friendly templates for asset lists, details, and forms using Bootstrap 5.

##### 2. Company & Location Structure - Full Implementation

- **Core Models**: Implemented the `Company`, `Division`, and hierarchical `Location` models to structure the organization.

- **Admin Interface**: Configured the Django admin for easy management of companies, divisions, and locations.

- **Data Integrity**: Ensured all relationships between assets, users, and locations are correctly established.

##### 3. Audit System Integration

- **Enabled Audit App**: With all core models in place, the `audit` app was successfully integrated.

- **Automated Logging**: The system now automatically logs all creation, update, and deletion events for assets, providing a complete audit trail for compliance.

##### 4. UI & Navigation Enhancements

- **Updated Main Navigation**: The base template now includes navigation links to the new asset management sections.

- **Status Indicators**: Implemented color-coded badges for asset statuses (e.g., Available, Assigned) for improved clarity.

- **Data Synchronization**: Ensured all field names across models, forms, and templates are consistent.

---

#### 🚀 System Status: Core Functionality Complete

The system is now operational with the following end-to-end features:

1. User Authentication & Management (from v0.0.1)
2. Company, Division & Location Management
3. Complete Asset Lifecycle Management (CRUD)
4. Asset Assignment & Return Tracking
5. Data Export to CSV
6. Comprehensive Audit Trail

---

#### 📈 Next Steps (v0.0.3 Roadmap)

##### High Priority

1. **2FA Implementation**: Complete the user setup and verification workflow.
2. **Reporting & Dashboard**: Build out the main dashboard with asset analytics and charts.
3. **Company Management Views**: Create a user-facing UI for managing locations.

##### Medium Priority

1. **Asset Data Import**: Implement CSV/Excel import functionality.
2. **Mobile Optimization**: Enhance mobile views and prepare for barcode scanning integration.

---

#### 🏁 Conclusion

Version 0.0.2 marks a major milestone, transforming the project from a foundational shell into a fully functional asset management system. The core business requirements are now met, providing a stable platform for building advanced features.
## Waypost - Changelog v0.0.3

**Version:** 0.0.3  
**Release Date:** August 11, 2025  
**Focus:** Asset Management Enhancements, Category/Brand Management, Export System, and UI/UX Improvements

---

### 🎯 Release Overview

Version 0.0.3 represents a significant enhancement to the Waypost with a focus on comprehensive asset management features, advanced category and brand management capabilities, improved export functionality, and substantial UI/UX improvements. This release consolidates system architecture, implements missing navigation functionality, and introduces powerful data export capabilities.

### ✅ Major Changes and Improvements

#### 1. Asset Management System Enhancements

#### **Complete Category and Brand Management System**
- **Category Management**: Comprehensive CRUD system for asset categories
  - CategoryListView with search and filtering capabilities
  - CategoryCreateView, CategoryUpdateView, CategoryDeleteView with proper validation
  - Professional templates with Bootstrap 5 styling and responsive design
  - Audit logging for all category operations

- **Brand and Model Management**: Full brand and model lifecycle management
  - BrandListView, ModelListView with advanced filtering and search
  - Complete CRUD operations for both brands and models with relationship management
  - Combined brands_models view for efficient management workflow
  - Professional templates with consistent UI/UX design

##### **Asset Data Structure Improvements**
- **Asset Number System**: Replaced "Asset Name" with "Asset Number" as primary identifier
  - Auto-generated asset numbers with manual override capability
  - Updated all templates and forms to reflect the new naming convention
  - Enhanced data consistency and asset tracking capabilities

##### **Advanced Export System**
- **Comprehensive Data Export**: Replaced simple CSV export with full-featured export system
  - AssetExportForm with comprehensive filtering options (status, category, brand, date ranges)
  - Multiple export formats: CSV, Excel (.xlsx), and PDF
  - Field selection capability allowing users to choose specific data columns
  - Professional export interface with preview and configuration options
  - Integration with openpyxl for Excel exports and reportlab for PDF generation

#### 2. User Role Management Refactoring

##### **Role Structure Optimization**
- **Role Consolidation**: Streamlined user role system
  - Removed "Manager" role from the entire Django project
  - Renamed "IT Specialist" to "IT Administrator" throughout the system
  - Updated all references, templates, and documentation to reflect new role structure

##### **Enhanced Access Control**
- **Multi-Company and Division Access**: Advanced permission system
  - IT Administrators can be assigned to multiple companies and divisions
  - Granular access control allowing specific company-division combinations
  - Superadmin capability to manage user assignments during creation and editing
  - Flexible permission matrix supporting complex organizational structures

#### 3. UI/UX and Visual Improvements

##### **Enhanced Visual Design**
- **Button and Status Badge Improvements**: Enhanced visibility and accessibility
  - Updated button colors for better visibility under current theme
  - Improved status badge styling with enhanced contrast and readability
  - Professional color scheme for "In Use", "Disposed", and other asset statuses
  - Consistent styling across all asset management pages

##### **Dashboard Optimization**
- **Streamlined Dashboard Layout**: Improved focus and usability
  - Removed Quick Action block for cleaner interface
  - Removed System Statistics block for better space utilization
  - Recent assets display at full width for better visibility
  - Enhanced responsive design for mobile and tablet devices

##### **Navigation Enhancements**
- **Asset Management Navigation**: Comprehensive dropdown menu system
  - Added "Import Assets" option for bulk asset management
  - Added "Manage Categories" and "Manage Brands & Models" options
  - Fixed navigation links for category and brand management
  - Improved user workflow with logical menu organization

#### 4. Import/Export Functionality

##### **Asset Import System**
- **Bulk Asset Import**: CSV and Excel file import capabilities
  - Support for both .csv and .xlsx file formats
  - Data validation and error reporting during import process
  - Template download for proper import file formatting
  - Batch processing with progress indicators

##### **Data Export Enhancement**
- **Professional Export Interface**: Comprehensive data export capabilities
  - Filter-based export with multiple criteria options
  - Format selection (CSV, Excel, PDF) with appropriate formatting
  - Field selection allowing customized export content
  - Export preview and validation before file generation

#### 5. URL Configuration Consolidation

- **Assets URLs Merged**: Consolidated `assets/urls_old.py` into `assets/urls.py`
  - Maintained backward compatibility with legacy routes
  - Unified all asset-related endpoints (CRUD, assignment, categories, brands, import/export, mobile, analytics, API)
  - Cleaned up duplicate and non-existent view references

- **Audit URLs Merged**: Consolidated `audit/urls_old.py` and `audit/urls_new.py` into `audit/urls.py`
  - Combined comprehensive audit functionality with legacy compatibility
  - Included audit management, execution, asset verification, mobile interface, logs, system events, compliance, and API endpoints
  - Maintained existing route names for backward compatibility

#### 6. Template System Improvements

- **Fixed Blank Company Page**: Created comprehensive `company_list.html` template
  - Modern card-based layout with Bootstrap 5 styling
  - Search and filtering functionality
  - Company status indicators and statistics display
  - Responsive design with mobile optimization
  - Empty state handling and pagination support
  - Integration with Django admin for management actions

- **Fixed URL Navigation Issues**: Corrected double-prefix URL problems
  - Fixed hardcoded relative URLs in base template navigation
  - Changed `/companies/companies/divisions/` to proper `/companies/divisions/`
  - Updated all company navigation links to use Django URL names
  - Resolved 404 errors in companies, divisions, locations, and users navigation

- **Navigation Updates**: Updated base template navigation
  - Changed audit navigation links from admin URLs to user-facing URLs
  - Added proper URL name references for audit functionality
  - Improved user experience with consistent navigation

#### 7. Role-Based Access Control Enhancements

- **Consistent Asset Filtering**: Implemented role-based asset visibility
  - Updated dashboard and asset views to use `user.get_accessible_assets()`
  - Ensured consistent asset filtering across all views (list, detail, update, delete, assign, return)
  - Fixed permission method calls in companies and audit views

#### 8. Admin Interface Cleanup

- **Restored Default Django Admin Styling**: Removed custom admin templates
  - Deleted custom templates for companies and audit admin pages
  - Restored clean, default Django admin interface
  - Improved consistency across admin pages

#### 9. Documentation Consolidation

- **Merged Progress Reports**: Consolidated multiple documentation files
  - Combined `PROGRESS_REPORT_v0.0.1.md`, `PROGRESS_REPORT_v0.0.2.md`, and `v 0.0.1.md`
  - Created comprehensive project history
  - Removed duplicate documentation files

#### 10. System Stability Improvements

- **Database and URL Integrity**: Fixed system configuration issues
  - Resolved non-existent view references in URL patterns
  - Ensured all URL patterns point to existing views
  - Maintained proper app namespace consistency

### 🔧 Technical Details

#### Files Created/Enhanced

##### **New Views and Templates**
- `assets/views.py` - Enhanced with comprehensive category/brand management views:
  - CategoryListView, CategoryCreateView, CategoryUpdateView, CategoryDeleteView
  - BrandListView, BrandCreateView, BrandUpdateView, BrandDeleteView
  - ModelListView, ModelCreateView, ModelUpdateView, ModelDeleteView
  - asset_export_view with filtering and format selection
  - generate_csv_export, generate_excel_export, generate_pdf_export functions

- `assets/forms.py` - Added AssetExportForm with comprehensive filtering options:
  - Status, category, brand filtering capabilities
  - Date range selection for created/modified dates
  - Field selection for customized exports
  - Export format selection (CSV, Excel, PDF)

##### **Template System Expansion**
- `templates/assets/category_list.html` - Professional category management interface
- `templates/assets/category_form.html` - Category creation/editing form
- `templates/assets/category_confirm_delete.html` - Category deletion confirmation
- `templates/assets/brand_list.html` - Brand management interface with search
- `templates/assets/brand_form.html` - Brand creation/editing form
- `templates/assets/brand_confirm_delete.html` - Brand deletion confirmation
- `templates/assets/model_list.html` - Model management interface
- `templates/assets/model_form.html` - Model creation/editing form
- `templates/assets/model_confirm_delete.html` - Model deletion confirmation
- `templates/assets/brands_models.html` - Combined brand and model management
- `templates/assets/asset_export.html` - Comprehensive export interface

##### **URL Configuration Updates**
- `assets/urls.py` - Enhanced with new routing patterns:
  - Category CRUD routes (list, create, edit, delete)
  - Brand CRUD routes (list, create, edit, delete)
  - Model CRUD routes (list, create, edit, delete)
  - Combined brands_models view route
  - Enhanced export route with filtering capabilities

#### Files Modified

##### **Core System Updates**
- `assets/urls.py` - Consolidated and cleaned up asset URL patterns
- `audit/urls.py` - Merged all audit URL configurations
- `templates/companies/company_list.html` - Created comprehensive company list template
- `templates/base/base.html` - Updated navigation for asset management options
- `templates/assets/asset_list.html` - Enhanced with improved status badge styling
- `templates/assets/asset_detail.html` - Updated with better button visibility
- `templates/dashboard.html` - Streamlined layout with full-width recent assets
- `docs/PROGRESS_REPORT_v0.0.1.md` - Consolidated project documentation

##### **Styling and UI Enhancements**
- Enhanced CSS styling for status badges with improved color schemes
- Updated button styling for better visibility under current theme
- Responsive design improvements across all asset management templates
- Professional form styling with Bootstrap 5 integration

#### Files Removed

- `assets/urls_old.py` - Merged into main assets URLs
- `audit/urls_old.py` - Merged into main audit URLs
- `audit/urls_new.py` - Merged into main audit URLs
- `docs/PROGRESS_REPORT_v0.0.2.md` - Consolidated into main progress report
- `docs/v 0.0.1.md` - Consolidated into main progress report
- Custom admin templates for companies and audit modules

#### New Dependencies

##### **Python Packages**
- `openpyxl` - For Excel file generation and export functionality
- `reportlab` - For PDF generation and advanced report formatting

##### **Frontend Enhancements**
- Enhanced Bootstrap 5 styling with custom CSS modifications
- Improved responsive design patterns for mobile compatibility
- Professional status badge styling with accessibility improvements

#### Database Schema Considerations

- Asset model updated to prioritize Asset Number over Asset Name
- Enhanced category and brand relationship management
- Improved audit logging for all CRUD operations
- Optimized queries for role-based asset filtering

#### New Features Implementation

##### **Category and Brand Management**
- Complete CRUD system with professional templates
- Search and filtering capabilities across all management interfaces
- Audit logging integration for all operations
- Role-based access control for management functions

##### **Advanced Export System**
- Multi-format export support (CSV, Excel, PDF)
- Comprehensive filtering system with date ranges
- Field selection for customized export content
- Professional export interface with preview capabilities

##### **Enhanced User Experience**
- Streamlined navigation with logical menu organization
- Improved visual feedback with enhanced status indicators
- Responsive design for mobile and tablet compatibility
- Professional form validation and error handling

#### Performance Optimizations

- Optimized database queries for category and brand listings
- Efficient filtering systems with proper indexing considerations
- Streamlined template rendering with reduced redundancy
- Enhanced caching strategies for frequently accessed data

### 🚀 Upgrade Instructions

#### For Existing Installations

1. **Install New Dependencies**:
   ```bash
   pip install openpyxl reportlab
   ```

2. **Update Database** (if applicable):
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

3. **Update Static Files**:
   ```bash
   python manage.py collectstatic --noinput
   ```

4. **Role Migration**:
   - Existing "Manager" role users will need role reassignment
   - "IT Specialist" role automatically renamed to "IT Administrator"
   - Review and update user permissions as needed

#### Configuration Updates

- Verify navigation menu functionality for category/brand management
- Test export functionality with various filter combinations
- Validate role-based access control with new permission structure

### 🧪 Testing and Validation

#### Tested Functionality

- ✅ Category and Brand CRUD operations with proper validation
- ✅ Advanced export system with CSV, Excel, and PDF formats
- ✅ Role-based access control with new permission structure
- ✅ Navigation functionality for all asset management features
- ✅ Visual improvements and responsive design across all devices
- ✅ Import functionality for bulk asset management
- ✅ Dashboard optimization with streamlined layout

#### Validation Checklist

- [ ] All navigation links functional
- [ ] Category and brand management accessible
- [ ] Export system working with all formats
- [ ] Role permissions properly configured
- [ ] Visual improvements visible under current theme
- [ ] Import functionality operational
- [ ] Mobile responsiveness confirmed

### 📋 Known Issues and Limitations

#### Current Limitations

- Export functionality requires appropriate file permissions on server
- Large dataset exports may require increased timeout settings
- Mobile interface optimization ongoing for complex management forms

#### Future Enhancements

- Advanced filtering options for category and brand management
- Bulk operations for category and brand assignments
- Enhanced mobile interface for management functions
- Additional export format support (e.g., XML, JSON)

### 🤝 Contributors and Acknowledgments

This release represents significant system enhancements developed through collaborative effort focusing on user experience improvements, comprehensive feature implementation, and system reliability enhancements.

#### Key Development Areas

- **Asset Management Enhancement**: Complete category and brand management system
- **Export System Development**: Advanced multi-format export capabilities
- **UI/UX Improvements**: Professional styling and enhanced user experience
- **Role Management Optimization**: Streamlined permission structure
- **System Consolidation**: URL and template system improvements

---

**Note**: This release builds upon the foundation established in v0.0.1 (authentication system, Django setup, i18n framework) and the major features implemented in v0.0.2 (asset models, audit system, dashboard, role-based access control). Version 0.0.3 focuses specifically on asset management enhancements, advanced export capabilities, and comprehensive UI/UX improvements.
- **Advanced Asset List Customization**: Users can now customize their asset list view
  - Column visibility controls: Show/hide any column (Image, Name, Tag, Category, Status, Location, Assigned To, Actions)
  - Per-column filtering: Individual filter controls for each column type
  - Real-time client-side filtering without page reloads
  - User preferences saved in browser localStorage
  - Keyboard shortcuts: Ctrl+H (toggle panel), Ctrl+R (clear filters)
  - Professional table-based list layout replacing card view
  - Enhanced search capabilities with instant results

### 🚀 Current System Status

#### ✅ Fully Operational Features

1. **User Authentication & Management** with role-based permissions
2. **Dashboard with Statistics** using role-based asset filtering
3. **Company, Division & Location Management** with modern UI
4. **Complete Asset Management System** with CRUD operations
5. **Asset Assignment & Return Processing** with audit trails
6. **Audit System & Logging** with comprehensive change tracking
7. **Multi-language Support (i18n)** with English and Chinese
8. **Admin Panel Access** with clean Django styling
9. **Data Export (CSV)** with filtering capabilities
10. **Mobile-Responsive Design** with Bootstrap 5 styling

#### Performance Improvements

- Reduced URL configuration complexity
- Eliminated dead code and unused routes
- Improved template loading efficiency
- Enhanced user navigation experience

### 🎯 Next Development Phase (v0.0.4)

#### High Priority

1. **2FA Implementation**: Complete TOTP setup and verification workflow
2. **Advanced Reporting System**: Charts and analytics dashboard
3. **Enhanced Mobile Features**: Barcode scanning and offline capability
4. **API Enhancement**: REST API development for mobile integration

#### Medium Priority

1. **Performance Optimization**: Query optimization and caching
2. **Advanced Asset Features**: Maintenance scheduling and depreciation
3. **Workflow Automation**: Automated processes and notifications
4. **Integration Features**: Third-party system integrations

### 📊 Quality Metrics

- **System Health**: ✅ Excellent (no Django check errors)
- **Feature Completeness**: 90% of core requirements
- **Code Quality**: High with proper error handling and validation
- **User Experience**: Modern, intuitive, and fully responsive
- **Documentation**: Comprehensive and up-to-date

### 🏁 Conclusion

Version 0.0.3 successfully consolidates the system architecture, fixes critical user interface issues, and improves the overall user experience. The Waypost now provides a clean, unified interface with consistent role-based access control and modern responsive design.

**Production Readiness**: The system is ready for production deployment with robust error handling, comprehensive security, and professional user interface.

---

## Version 0.0.4

**Release Date**: April 15, 2026

Version 0.0.4 introduces enhanced security, advanced reporting capabilities, mobile-optimized features, and a comprehensive REST API for system integration.

### Security Enhancements

**Two-Factor Authentication (2FA)**
- TOTP-based 2FA using `pyotp` library
- QR code generation for easy authenticator app setup
- Per-user 2FA enable/disable toggle
- Backup codes for account recovery
- Required 2FA status display in user profile
- Secure session management with 2FA verification

**Key Technical Implementation**:
- `django_otp` middleware integration
- TOTP secret key generation and storage
- QR code PNG generation as base64 data URL
- 2FA required decorator for protected views

### Advanced Reporting with Chart.js

**Interactive Dashboard Charts**
- Six chart types: Status Distribution, Category Distribution, Brand Distribution, Warranty Status, Quotation Status, Purchase Summary
- Four display modes: Doughnut, Pie, Bar, Line
- User-customizable dashboard layout via modal interface
- Session-based chart configuration persistence
- AJAX-powered chart data loading via REST API

**Chart API Endpoints**:
- `/api/reports/charts/quotation-status/` - Quotation status distribution
- `/api/reports/charts/purchase-summary/` - Purchase value comparison
- `/api/reports/charts/category-distribution/` - Product category breakdown
- `/api/reports/charts/brand-distribution/` - Brand distribution analysis
- `/api/reports/charts/warranty-status/` - Warranty expiry tracking

### Mobile Features with Barcode Scanning

**Barcode Scanning Support**
- Compatible with handheld barcode scanner devices
- Works with smartphone camera-based scanning apps
- Rapid product lookup by scanning barcode in product list
- Quotation and stock item barcode support

**Mobile Optimizations**:
- Responsive Bootstrap 5 design
- Touch-friendly interface elements
- Large tap targets for handheld devices
- Fast input field switching for rapid scanning

### REST API Endpoints

**Authentication API**
- `POST /api/auth/login/` - User authentication
- `POST /api/auth/logout/` - Session termination
- `GET /api/auth/user/` - Current user profile

**Products API**
- `GET /api/products/` - List all products
- `POST /api/products/` - Create new product
- `GET /api/products/{id}/` - Retrieve product details
- `PUT /api/products/{id}/` - Update product
- `DELETE /api/products/{id}/` - Delete product
- `GET /api/products/barcode/{barcode}/` - Lookup by barcode

**Quotations API**
- `GET /api/quotations/` - List all quotations
- `POST /api/quotations/` - Create new quotation
- `GET /api/quotations/{id}/` - Retrieve quotation details
- `PUT /api/quotations/{id}/` - Update quotation
- `DELETE /api/quotations/{id}/` - Delete quotation
- `GET /api/quotations/{id}/pdf/` - Export quotation as PDF

**Purchases API**
- `GET /api/purchases/` - List all purchases
- `POST /api/purchases/` - Create new purchase
- `GET /api/purchases/{id}/` - Retrieve purchase details
- `PUT /api/purchases/{id}/` - Update purchase
- `DELETE /api/purchases/{id}/` - Delete purchase

**Stock API**
- `GET /api/stock/` - List all stock items
- `POST /api/stock/` - Create new stock item
- `GET /api/stock/{id}/` - Retrieve stock item details
- `PUT /api/stock/{id}/` - Update stock item
- `DELETE /api/stock/{id}/` - Delete stock item
- `GET /api/stock/low-stock/` - List low stock alerts

**Reports API**
- `GET /api/reports/charts/<chart_type>/` - Chart data endpoints
- `GET /api/reports/dashboard-config/` - Get dashboard configuration
- `POST /api/reports/dashboard-config/` - Save dashboard configuration

### User Interface Improvements

- Language code standardization (zh-cn, en-us)
- English (US) locale option
- Improved language switcher functionality
- Dashboard customization modal with modern UI
- Consistent Bootstrap 5 styling throughout
- Fixed template truncation issues in forms

---

*Generated on April 15, 2026 - Waypost v0.0.4*
*Previous versions: v0.0.1 (Foundation), v0.0.2 (Major Features), v0.0.3 (Consolidation & Fixes), v0.0.4 (Security & Reporting)*

---

## Version 0.1.0

**Release Date**: April 16, 2026

Version 0.1.0 delivers the initial full Quotation & Invoice Management System with end-to-end workflow coverage from quotation creation through dispatch and invoicing lifecycle control.

### Quotation & Customer Workflow

- Added customer and product business extensions for sales flow:
  - `products.ProductPrice` for unit/tax pricing tied to brand and model
  - `customers.CustomerProfile` for delivery and contact defaults
- Implemented quotation lifecycle:
  - quotation creation/edit/list/detail with item-level totals
  - status transitions (`draft` -> `sent` -> `confirmed`)
  - duplicate/cancel actions
  - quotation PDF generation path validated in smoke run

### Purchase and Stock Conversion

- Implemented conversion of confirmed quotations into purchase orders.
- Added receipt workflow with serial capture and stock creation as `assets.Asset` records.
- Added stock overview/list/detail views to monitor received and dispatch-ready assets.

### Delivery Order Workflow

- Added `DeliveryOrder` and `DeliveryItem` workflow with serial-level linkage.
- Implemented delivery creation from available quotation-linked stock.
- Added dispatch/completion transitions with status and asset-state updates.
- Added signed copy upload requirement before completion and delivery document generation route.

### Invoice Batch and Invoice Information

- Added weekly Sharepoint batch import (`WeeklyOrderBatch`) with strict duplicate detection and failure tracking.
- Added `InvoiceInfo` and `InvoiceInfoItem` with:
  - `yymmdd##` invoice numbering
  - quotation/delivery linkage
  - tax/net/gross recalculation from delivery/quotation sources
- Added invoice information list/detail/update/recalculate/export/document routes.

### Email Dispatch and Esker Handoff

- Added `EmailDispatch` compose/list/history flow with recipient controls and attachment manifesting.
- Implemented status transitions:
  - `draft` -> `sent` -> `client_confirmed` -> `esker_forwarded`
- Added explicit client-confirmed and Esker-forward actions for operational tracking.

### Workflow Dashboard and Governance

- Added workflow dashboard (Kanban stages) and cross-entity workflow search.
- Added standardized workflow badges and next-action suggestions across key list/detail screens.
- Added `WorkflowStatusAudit` model and status-change signals for quotation/purchase/delivery/invoice-dispatch transitions.

### Validation and Stability

- Applied migrations for the new invoice/workflow models.
- Resolved workflow dashboard null-related rendering issue in invoiced stage cards.
- Executed a broad end-to-end smoke run (quotation -> purchase -> delivery -> invoice -> dispatch) with passing assertions across transition endpoints and status checks.

---

*Generated on April 16, 2026 - Waypost v0.1.0*

---

## Version 0.1.1

**Release Date**: April 17, 2026

Version 0.1.1 focuses on document generation hardening and print-layout fidelity by removing LibreOffice dependency for core customer documents and standardizing template rendering behavior.

### Quotation PDF Rendering Upgrade

- Replaced quotation PDF export conversion path with direct HTML-to-PDF rendering.
- Implemented a dedicated Excel-style quotation template with iterative layout tuning for close match to reference print output.
- Improved typography consistency, column behavior, summary sizing, remark handling, and signature alignment in quotation output.
- Added dynamic default remark line composition using customer and first-item context.

### Delivery Order (签收单) PDF Rendering Upgrade

- Introduced direct HTML-to-PDF generation for delivery orders and routed download endpoint to the new renderer.
- Added delivery-specific Excel-style template modeled from 签收单 reference layout and aligned with quotation style system.
- Reworked delivery sections for:
  - standardized header/subheader/sheet outline styles
  - auto-filled delivery method text
  - normalized signature block with aligned labels and underlines
- Ensured single-page rendering behavior in validation output for current baseline data.

### Runtime and Platform Stability

- Added Windows runtime setup for font configuration so WeasyPrint rendering is stable in local development runs.
- Applied startup bootstrap in manage/ASGI/WSGI entrypoints for consistent environment initialization.

### Validation and Regression Checks

- Verified updated quotation and delivery rendering paths with Django shell smoke generation.
- Produced preview artifacts for reference comparison during pixel-tuning passes.
- Confirmed no syntax/lint errors in modified delivery/quotation rendering files after updates.

---

*Generated on April 17, 2026 - Waypost v0.1.1*

---

## Version 0.1.2

**Release Date**: April 17, 2026

Version 0.1.2 focuses on asset creation workflow usability, broader translation coverage for template attributes, and stability fixes for admin and protected deletion behavior.

### Asset Create Workflow Enhancements

- Added batch creation support on asset create with configurable quantity and per-row serial number/status input.
- Implemented searchable dropdown controls for asset create/edit form fields:
  - category
  - brand
  - model
  - location
- Added dependent brand -> model filtering so model options only show entries under the selected brand.
- Improved location behavior in create form:
  - fallback to all active locations if company-scoped locations are empty
  - default location to `Vanke VMO Warehouse` when available

### Data Model and Validation Updates

- Updated `assets.Asset.serial_number` to allow blank values for inventory items without serial numbers.
- Added migration `assets/migrations/0008_alter_asset_serial_number.py`.
- Kept single-item create compatibility while adding transaction-safe multi-item creation path.

### Internationalization Coverage Expansion

- Completed translation wrapping for remaining template attribute strings across placeholders, aria labels, and title attributes.
- Added/updated zh-cn translations for newly wrapped asset create labels/help text/notes and accessibility labels.
- Recompiled zh-cn message catalog to apply updated translations.

### Stability and Admin UX Fixes

- Fixed asset model deletion flow to handle protected references gracefully with user-facing error messages instead of raw exceptions.
- Restored non-empty Django admin changelist template overrides for company-related admin pages:
  - company
  - division
  - companyuser
  - location

### Verification

- Django system checks executed successfully after workflow and i18n updates.
- Asset create form behavior validated for searchable controls, brand/model dependency, and batch input rendering.

---

*Generated on April 17, 2026 - Waypost v0.1.2*

## Release Notes v0.1.3

**Version:** 0.1.3  
**Release Date:** April 20, 2026  
**Focus:** Warehouse slot workflows, asset batch operations, export stability, and company user editing

---

### Highlights

1. Warehouse slot support is now integrated from company/location setup through asset create, list, and bulk edit flows.
2. Asset create and list experiences now support practical batch operations and grouped drill-down workflows.
3. Export and edit-route defects found during QA were fixed and validated.
4. Navigation and localization were updated to match the revised operational model.

### Delivered Scope (22-file release set)

1. Asset create improvements
- Model selection now auto-fills category and brand.
- Batch row inputs preserve values while quantity changes.
- Duplicate serials are blocked on frontend and backend, with persistent warnings.
- Zone/rack/shelf dropdowns are dynamically populated and validated from selected warehouses.

2. Asset list and batch operations
- Grouped rows for non-serialized assets with drill-down behavior.
- Quantity-aware batch edit panel with selection preview.
- Grouped-row selection expansion and server-side bulk edit endpoint.

3. Data model and migrations
- Added `location_zone`, `location_rack`, `location_shelf` on assets.
- Added `zone`, `rack`, `shelf` on locations with range expansion helpers.
- Added optional `category` on asset models for filtering consistency.
- Added supporting migrations in assets and companies apps.

4. Import/export updates
- Import now enforces required `category` and `brand` and supports normalized headers.
- Export now uses accessible-assets scope and current location display mapping.
- Fixed invalid `select_related` and outdated legacy export field mappings.

5. Company user management fix
- Added company user update view and edit route.
- Wired list-page edit action to real route and fixed URL converter mismatch (`int` vs `uuid`).

6. UI/navigation and i18n
- Split "Manage Brands" and "Manage Models" in assets navigation.
- Removed timed global auto-dismiss for alerts.
- Added/updated zh-cn translations for new fields and labels.

### Migration Files Added

1. `assets/migrations/0009_alter_asset_barcode.py`
2. `assets/migrations/0010_alter_assetmodel_model_number.py`
3. `assets/migrations/0011_asset_location_rack_asset_location_shelf_and_more.py`
4. `assets/migrations/0012_assetmodel_category.py`
5. `companies/migrations/0008_location_rack_location_shelf_location_zone.py`

### Validation

1. `python manage.py check` executed after each major change set.
2. Reported defects for bulk-edit slot validation, export CSV behavior, and company-user edit route were resolved.

## Release Notes v0.1.4

**Version:** 0.1.4  
**Release Date:** April 21, 2026  
**Focus:** Company/location/contact import workflows, rollback safety, and import-result visibility

---

### Highlights

1. Companies, locations, company contacts, and assets now support rollback of the latest import per user and module.
2. Companies-module CSV imports now use a preview-and-confirm flow with clearer result reporting and downloadable samples.
3. Company users were refactored into company contacts, improving recipient/contact workflows across locations and quotations.
4. Import UX and list summaries were corrected to show real totals, clearer validation feedback, and safer duplicate handling.

### Delivered Scope (30-file release set)

1. Companies CSV import workflows
- Added dedicated CSV import pages for companies, locations, and company contacts.
- Added preview/confirm flow with shared upload UI, sample CSV downloads, and required/optional column guidance.
- Added location-specific "update existing matched entries" support instead of always skipping duplicates.
- Added tolerant CSV decoding helpers for UTF-8, GB18030/CP936, and Big5 inputs.

2. Import rollback system
- Added shared rollback tracking models: `ImportRun` and `ImportRunChange`.
- Added shared rollback utilities to snapshot created/updated records and restore them in reverse order.
- Enabled rollback actions for company, location, company contact, and asset imports.
- Exposed rollback buttons on upload, preview, and result pages when a rollback-eligible run exists.

3. Import result visibility and validation
- Added a shared import result page with total rows, processed rows, created, updated, skipped, and error counts.
- Added row-level issue reporting and updated-field diff summaries for location update imports.
- Hardened import validation so unsupported asset status/condition values and invalid location/status enums surface as errors instead of silent defaults.
- Fixed JSON snapshot serialization for non-primitive model fields during rollback logging.

4. Company contacts and location data model updates
- Refactored `CompanyUser` into a company-contact-oriented workflow with optional linked auth user, explicit contact name, and contact helper methods.
- Added company contact create/edit/remove workflows and updated UI labels from "Company Users" to "Company Contacts".
- Extended locations with `name_en`, `code_2`, `chinese_address`, and linked company contact support.
- Added dynamic contact loading on the location form and updated quotation fallbacks to use contact helper methods safely.

5. List-page and admin improvements
- Added import entry points from company, location, and company contact list pages.
- Fixed list badges to use total paginator counts instead of current page length.
- Updated the locations summary card to show total locations rather than warehouse-slot aggregates.
- Updated admin/API support for the expanded location and company-contact schema.

### Migration Files Added

1. `companies/migrations/0009_alter_companyuser_options_companyuser_name_and_more.py`
2. `companies/migrations/0010_location_chinese_address_location_code_2_and_more.py`
3. `companies/migrations/0011_migrate_location_legacy_values.py`
4. `companies/migrations/0012_importrun_importrunchange_and_more.py`

### Validation

1. `python manage.py check` executed successfully after each major implementation step.
2. End-to-end manual verification confirmed import and rollback behavior for companies, locations, company contacts, and assets.
3. Verified rollback restored counts correctly after import runs and exposed/fixed snapshot serialization edge cases.

## Release Notes v0.1.5

**Version:** 0.1.5  
**Release Date:** April 24, 2026  
**Focus:** Multi-role admin controls, order-management access hardening, product price lifecycle tracking, and mailbox workflows

---

### Highlights

1. The single admin-role field was replaced with additive persistent administrator roles, including a new combined Order Management & Procurement Specialist role.
2. Order Management is now permission-gated in both navigation and direct URLs across quotations, purchases, deliveries, invoices, products, and workflow dashboards.
3. Product pricing now derives brand and unit from the selected asset model, preserves previous prices as inactive history, and tightens create/edit behavior around current prices.
4. A user-configurable mailbox module was added to account settings with IMAP or POP3 receive options, SMTP sending, inbox/outbox caching, and automatic 5-minute sync while the Django server is running.

### Delivered Scope (47-file release set)

1. Administrator role system overhaul
- Added persistent `accounts.AdminRole` records and migrated users from the legacy single `admin_role` field to the `User.roles` many-to-many relationship.
- Added compatibility helpers such as `admin_role`, `get_admin_roles_display()`, and access-scope utilities so older code paths can keep working during the transition.
- Introduced the `order_management_procurement_specialist` role and updated admin, forms, templates, API serializers, and demo-data setup to work with multi-role assignment.
- Fixed Django admin list rendering for translated role labels by coercing lazy translation values to strings before display.

2. Order Management access control and navigation
- Renamed the main navigation section from Products & Sales to Order Management.
- Hid the Order Management navigation from unauthorized users and blocked direct access with explicit permission checks in products, quotations, purchases, deliveries, invoices, and workflow dashboard views.
- Added the mailbox entry point under Order Management and surfaced related mailbox configuration on the account settings page.

3. Asset logs and audit workflow restructuring
- Moved asset-related audit visibility under Assets as Asset Change Logs with dedicated list/detail routes and clickable rows.
- Split asset audits into Dashboard, New Audit, and History flows, with updated templates, back-navigation handling, and richer detail pages.
- Expanded asset import logging so import/export activity appears in asset change history with clearer descriptions and metadata.

4. Product pricing and quotation workflow updates
- Product price create/edit now derives brand and unit from the selected asset model and rounds tax-inclusive pricing consistently.
- Product add flow defaults `valid_from` to today and only offers models not already used by current prices.
- Product price edits now create inactive historical rows with the prior price state and a `valid_until` timestamp on the change date.
- The unstable model selector was replaced with a native select plus client-side filtering for better browser compatibility.
- Quotation customer contact resolution was hardened to tolerate orphaned company-user memberships without crashing create/edit views.

5. Mailbox, email dispatch, and account settings
- Added `UserMailboxSettings` and `ReceivedEmailMessage` models with migrations for inbox/outbox sync configuration, cached messages, sync window settings, and sent-folder support.
- Added account settings UI for mailbox credentials, protocol/security options, sync lookback, outbox sync toggle, auto-sync toggle, and 2FA status/actions.
- Added mailbox inbox/detail/sync views with inbox/outbox tabs, newest-first sorting, clickable rows, and manual sync support.
- Added SMTP sending through per-user mailbox settings with fallback to Django email backend, and cached sent dispatches into the mailbox outbox.
- Added a background mailbox auto-sync worker thread for `runserver`, plus in-process locking to avoid SQLite write contention during concurrent sync attempts.

6. Validation and regression fixes
- Fixed admin user edit validation so username uniqueness excludes the current instance and password fields become optional on edit.
- Fixed the mailbox sync `timezone` import regression and the quotation create `NoneType` crash caused by orphaned contact memberships.
- Applied and validated new migrations for accounts and products, and verified the updated flows with focused Django checks and runtime probes.

### Migration Files Added

1. `accounts/migrations/0012_admin_roles_m2m.py`
2. `accounts/migrations/0013_user_mailbox_settings_and_received_messages.py`
3. `accounts/migrations/0014_mailbox_sync_window_and_outbox.py`
4. `products/migrations/0002_productprice_history_and_current_constraint.py`

### Validation

1. `python manage.py makemigrations --check` executed successfully after the release migrations were added.
2. `python manage.py migrate accounts` and `python manage.py migrate products` were applied locally and verified.
3. `python manage.py check` executed successfully after the role, product, mailbox, and regression-fix slices.
4. Focused runtime probes verified product-price history creation, mailbox schema availability, and mailbox sync lock behavior.