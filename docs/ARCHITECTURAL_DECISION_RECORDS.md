# Architecture Decision Records (ADR) - Waypost

This directory contains architectural decision records for the Waypost.

## Table of Contents

| ID | Title | Status | Date |
|----|-------|--------|------|
| [0001](#adr-0001-service-catalog-separation) | Service Catalog Separation | Accepted | 2026-04-29 |
| [0002](#adr-0002-mailbox-driven-rfq-automation) | Mailbox-Driven RFQ Automation | Accepted | 2026-04-29 |
| [0003](#adr-0003-direct-dispatch-fulfillment) | Direct Dispatch Fulfillment | Accepted | 2026-04-29 |
| [0004](#adr-0004-hardware-only-assets-boundary) | Hardware-Only Assets Boundary | Accepted | 2026-04-29 |
| [0005](#adr-0005-product-price-history--lifecycle) | Product Price History & Lifecycle | Accepted | 2026-04-29 |
| [0006](#adr-0006-import-rollback-system) | Import Rollback System | Accepted | 2026-04-21 |
| 0007 | Multi-Role Administrator Access Control | Accepted — ⚠️ **body never committed** | 2026-04-24 |
| 0008 | HTML-to-PDF Generation Without LibreOffice | Accepted — ⚠️ **body never committed**; scope restated by [0020](#adr-0020-three-pdf-paths--and-what-adr-0008-actually-decided) | 2026-04-17 |
| 0009 | Warehouse Slot Tracking | Accepted — ⚠️ **body never committed** | 2026-04-20 |
| 0010 | Company Contact vs Company User Model | Accepted — ⚠️ **body never committed** | 2026-04-21 |
| [0011](#adr-0011-wechat-mini-program-for-kering-store-device-inspection) | WeChat Mini Program for Kering Store Device Inspection | Accepted | 2026-09-13 |
| [0012](#adr-0012-unified-design-system-and-token-single-source-of-truth) | Unified Design System and Token Single Source of Truth | Accepted | 2026-10-05 |
| [0013](#adr-0013-mixed-primary-keys--uuid-where-identity-leaves-the-system) | Mixed Primary Keys — UUID Where Identity Leaves the System | Accepted | 2026-10-22 |
| [0014](#adr-0014-two-factor-authentication-is-an-opt-in-login-gate-not-an-otp-enforced-session) | Two-Factor Authentication Is an Opt-In Login Gate | Accepted (gap open) | 2026-10-22 |
| [0015](#adr-0015-one-system-smtp-identity-for-django-mail-a-parallel-per-user-path-for-dispatch) | One System SMTP Identity, a Parallel Per-User Dispatch Path | Accepted | 2026-10-22 |
| [0016](#adr-0016-two-locales-project-level-catalogs-url-prefix-routing--and-a-preference-that-is-stored-but-not-applied) | Two Locales, Project Catalogs, URL-Prefix Routing | Accepted (gaps open) | 2026-10-22 |
| [0017](#adr-0017-production-stack--rootless-compose-three-gunicorn-workers-whitenoise-for-static-a-shared-nginx-443) | Production Stack — Rootless Compose, Three Workers, Shared Nginx 443 | Accepted | 2026-10-22 |
| [0018](#adr-0018-environment-driven-settings-with-fail-fast-guards--and-the-two-defaults-that-are-not-guarded) | Environment-Driven Settings with Fail-Fast Guards | Accepted (gap open) | 2026-10-22 |
| [0019](#adr-0019-api-authentication--simplejwt-for-the-mini-program-session-for-the-web) | API Authentication — SimpleJWT for the Mini Program, Session for the Web | Accepted | 2026-10-22 |
| [0020](#adr-0020-three-pdf-paths--and-what-adr-0008-actually-decided) | Three PDF Paths — and What ADR-0008 Actually Decided | Accepted (restates 0008) | 2026-10-22 |
| [0021](#adr-0021-helpdesk--wenqu-merge-one-user-table-profile-marked-customers-and-the-tv-claim) | Helpdesk → Wenqu Merge: One User Table, Profile-Marked Customers, tv Claim | Accepted | 2026-11-19 |
| [0022](#adr-0022-embeddings-as-jsonfield-with-a-query-time-vector-cast--ivfflat-as-named-debt) | Embeddings as JSONField with a Query-Time vector Cast — ivfflat as Named Debt | Accepted (debt open) | 2026-11-19 |
| [0023](#adr-0023-synchronous-sse-on-wsgi-gthread--asgi-rejected) | Synchronous SSE on WSGI gthread — ASGI Rejected | Accepted | 2026-11-19 |
| [0024](#adr-0024-private-media-on-a-dedicated-storage-outside-the-public-media-alias) | Private Media on a Dedicated Storage Outside the Public /media/ Alias | Accepted | 2026-11-19 |

> ⚠️ **0007–0010 are table-of-contents entries with no decision text.** They were listed as
> Accepted but no body was ever committed (verified with `git log -S` back to `7406d6d`), so
> their anchors above are deliberately not links. Writing those four bodies is tracked
> separately; 0020 restates 0008's scope in the meantime.
>
> **0013–0020 were written from the code, not from issue #20's titles.** Three of that issue's
> premises turned out to be inverted — companies do not use UUIDs (0013), 2FA is not mandatory
> (0014), and the API is not session-only (0019) — and a fourth named the wrong mechanism
> (`DatabaseSMTPEmailBackend` is system-wide, 0015). Each ADR records the correction and the
> documents that still carry the old claim.

---

## ADR-0001: Service Catalog Separation

**Status**: Accepted  
**Date**: 2026-04-29  
**Authors**: Sean Liu

### Context

Previously, services were modeled using `AssetModel` alongside hardware products, creating confusion about what constitutes a physical asset vs. a service offering. This caused issues in:
- Quotation creation (hardware/services mixed together)
- Delivery workflows (services don't need asset tracking)
- Purchase orders (services don't create inventory items)
- Export reports (inconsistent categorization)

### Decision

Split the unified product catalog into two distinct entities:

1. **Hardware Products**: Continue using `AssetBrand` → `AssetModel` hierarchy stored in `products.ProductPrice`
   - These become `assets.Asset` records when purchased
   - Require serial number tracking
   - Need warehouse slot management

2. **Service Offerings**: New standalone model `products.ServiceItem`
   - Fields: `service_group`, `name`, `description`, `unit`, `is_active`
   - Priced via `ProductPrice.service_item` (nullable FK)
   - Never create asset records
   - Flow directly through quotation → delivery without purchase

**Schema Changes:**
```python
# New model
class ServiceItem(models.Model):
    service_group = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=20, default='hours')
    is_active = models.BooleanField(default=True)
    
# Updated ProductPrice constraints
class ProductPrice(models.Model):
    # Hardware path
    brand = models.ForeignKey(AssetBrand, null=True)
    model = models.ForeignKey(AssetModel, null=True)
    # Service path  
    service_item = models.ForeignKey(ServiceItem, null=True)
    
    # Enforce exactly one target
    class Meta:
        constraints = [
            CheckConstraint(
                check=(
                    Q(brand__isnull=False, model__isnull=False, service_item__isnull=True) |
                    Q(brand__isnull=True, model__isnull=True, service_item__isnull=False)
                ),
                name="must_target_model_or_service_item"
            )
        ]
```

### Consequences

**Positive:**
- Clear separation of concerns between assets and services
- Simplified delivery workflow (no need to filter out services)
- Accurate reporting (hardware-only exports work correctly)
- Natural fit for service-only quotations (consulting, maintenance)

**Negative:**
- Requires data migration from legacy `AssetModel` service category records
- Adds complexity to price selection logic (need to know type upfront)
- Two different add flows (product vs service item)

**Mitigations:**
- Create backfill migration: migrate existing service `AssetModel` records → `ServiceItem`
- Add unified catalog helper views that display both types with labels
- Keep single `ProductPrice` table for admin simplicity

### References
- Related migrations: `products/migrations/0003_serviceitem...`, `deliveries/migrations/0002_deliveryitem_quotation_item_and_service_lines.py`
- Quotation workflows updated to support `QuotationItem.service_item`

---

## ADR-0002: Mailbox-Driven RFQ Automation

**Status**: Accepted  
**Date**: 2026-04-29  
**Authors**: Sean Liu

### Context

Customers send RFQs via email. Order management staff manually:
1. Read email
2. Classify products
3. Search pricing
4. Draft quotations
5. Send reply

This process is slow, error-prone, and doesn't scale.

### Decision

Implement automated RFQ classification and draft generation from mailbox messages:

1. **Store Received Messages**: Extend `accounts.ReceivedEmailMessage` with RFQ analysis fields:
   ```python
   class ReceivedEmailMessage(models.Model):
       rfq_classification = models.CharField(max_length=50, choices=RFQType.choices)
       rfq_confidence = models.FloatField(null=True)
       rfq_summary = models.TextField(blank=True)
       extracted_rfq_data = models.JSONField(default=dict)
       has_pending_rfq = models.BooleanField(default=False)  # Needs manual review
   ```

2. **Trigger Auto-Classification**: When mailbox sync completes, trigger async task:
   - Parse email body (plaintext/HTML)
   - Extract line items using NLP (Minimax API)
   - Match against product catalog
   - Create draft `Quotation` if confidence > threshold
   - Flag for human review if low confidence or unauthorized sender

3. **Authorize RFQ Senders**: Add `company_contacts.CompanyUser.is_authorized_rfq_sender` flag:
   - Only auto-generate quotes from trusted contacts
   - Allow manual override per customer

4. **UI for RFQ Management**: Add inbox views with actions:
   - Classify/reclassify message
   - Reprocess after corrections
   - Link/unlink from existing quotation
   - Mark as handled

### Consequences

**Positive:**
- Reduces quote turnaround time significantly
- Consistent product matching rules
- Audit trail of all incoming RFQs
- Handles edge cases via human-in-the-loop

**Negative:**
- AI costs (Minimax API calls)
- False positives require manual cleanup
- Configuration complexity (API keys, thresholds)

**Mitigations:**
- Cache matched results to avoid duplicate API calls
- Confidence scoring allows human priority judgment
- Manual review queue prevents unwanted auto-quoting

### References
- Files: `accounts/rfq_ai.py`, `accounts/models.py` (ReceivedEmailMessage)
- Migrations: `accounts/migrations/0015_receivedemailmessage_rfq_confidence_and_more.py`
- Settings: `MINIMAX_RFQ_API_URL`, `MINIMAX_RFQ_MODEL`, `MINIMAX_TOKEN_PLAN_KEY`

---

## ADR-0003: Direct Dispatch Fulfillment

**Status**: Accepted  
**Date**: 2026-04-29  
**Authors**: Sean Liu

### Context

Current flow always creates `PurchaseOrder` after confirming a quotation:
1. Quotation confirmed → Create PO
2. Receive goods into stock
3. Create delivery from stock

However, some quotations only contain items we already have in stock (no need to purchase). Other quotations might be 100% services (no physical goods at all). Forcing purchase orders in these cases wastes time.

### Decision

Allow confirmed quotations to skip purchase orders under specific conditions:

1. **Direct Dispatch Eligibility Check**: When viewing confirmed quotation, evaluate:
   - If all hardware items exist in internal warehouse stock quantities ≥ quoted quantities
     → Show "Create Delivery" button instead of "Create Purchase Order"
   - If any hardware items are missing from stock
     → Show "Continue Fulfillment" (creates PO for missing items, then deliver)
   - If quotation is 100% services (no hardware lines)
     → Always show "Create Delivery" (services never create POs)

2. **Updated Delivery Workflow**: Remove `Prepared` status from active workflow:
   - Old states: `pending` → `prepared` → `dispatched` → `completed`
   - New states: `pending` → `dispatched` → `delivered`
   - Renamed `completed` to `Delivered` for clarity

3. **Service-Aware Delivery Items**: Update `DeliveryItem` to support non-asset lines:
   ```python
   class DeliveryItem(models.Model):
       delivery_order = models.ForeignKey(DeliveryOrder, ...)
       asset = models.ForeignKey(Asset, null=True)  # Null for services
       quotation_item = models.ForeignKey(QuotationItem, ...)  # Links to source
       
       # Preserve snapshot data
       brand = models.CharField(...)
       product_description = models.CharField(...)
       service_item = models.ForeignKey(ServiceItem, null=True)
   ```

### Consequences

**Positive:**
- Eliminates unnecessary PO/delay for in-stock or service-only orders
- Faster fulfillment cycle time
- Cleaner operational logic (less mental overhead)
- Matches real-world business scenarios

**Negative:**
- More complex quotation next-action buttons (need conditional rendering)
- Stock availability must be checked dynamically on quotation list/detail
- Need to handle partial fulfillment scenarios

**Mitigations:**
- Precompute stock summary dashboard column (cached)
- Show warning if stock drops below required quantity during checkout
- Explicit "Continue Fulfillment" button bridges old/new paths

### References
- Views: `quotations/views.py` (`direct_dispatch_eligible` logic)
- Templates: `quotations/quotation_list.html`, `deliveries/create_from_quotation.html`
- Deliveries migration: `deliveries/migrations/0002_deliveryitem_quotation_item_and_service_lines.py`

---

## ADR-0004: Hardware-Only Assets Boundary

**Status**: Accepted  
**Date**: 2026-04-29  
**Authors**: Sean Liu

### Context

After introducing `ServiceItem` separate from hardware products, several pages accidentally included service records:
- Asset list view (showed consulting services)
- Brand/model management (mixed service brands)
- Export operations (exported non-physical items)
- Reports (skewed totals with services)

Users expected "Assets" page to only show physical equipment.

### Decision

Enforce strict hardware-only boundaries across all asset-facing functionality:

1. **Category-Level Type Flag**: Add `assets.AssetCategory.item_type`:
   ```python
   class AssetCategory(models.Model):
       item_type = models.CharField(max_length=20, 
                                    choices=[('hardware', 'Hardware'),
                                            ('service', 'Service')])
   ```
   - All existing categories migrate to `'hardware'` by default
   - Service categories created explicitly for `ServiceItem`

2. **Centralized Helper Functions**: Create `apps.assets.helpers.get_hardware_only_*()` functions:
   ```python
   def get_hardware_only_categories():
       return AssetCategory.objects.filter(item_type='hardware')
   
   def get_hardware_only_brands():
       return AssetBrand.objects.filter(category__item_type='hardware')
   
   def get_accessible_assets(user):
       # Base queryset + role filters, but exclude service-category assets
       return Asset.objects.filter(category__item_type='hardware') \
                          .filter(accessible_to(user))
   ```

3. **Apply Helpers Site-Wide**: Audit all asset-related views/templates:
   - List/Detail/Edit/Delete → use `get_hardware_only_` helpers
   - Brand/Model management pages → filter to hardware only
   - Export flows → use accessible-assets scope filtered to hardware
   - Statistics pages → same filtering
   - Audit log links → point to hardware-only asset change logs

4. **Keep Service Management Separate**: Move service catalog management into `Products` app:
   - `products.views.manage_service_items`
   - `templates/products/service_item_list.html`
   - Distinct navigation entry: "Services" under "Catalog"

### Consequences

**Positive:**
- Users see consistent, relevant data on each page
- Prevents accidental service-item deletion (protected by location references)
- Clean separation makes debugging easier
- Export/report reliability improved

**Negative:**
- Requires careful auditing of all code paths
- Need to prevent new asset-related code from including services
- Migration of legacy service `AssetModel` records needed (handled in ADR-0001)

**Mitigations:**
- Centralize filtering logic so one function update propagates everywhere
- Add tests verifying hardware-only behavior on critical views
- Document boundary clearly in code comments

### References
- Files modified: 35+ files in iteration (see CHANGELOG v0.1.7)
- Key templates: `assets/asset_list.html`, `assets/brand_list.html`, `exports/excel_export.html`

---

## ADR-0005: Product Price History & Lifecycle

**Status**: Accepted  
**Date**: 2026-04-24  
**Authors**: Sean Liu

### Context

Historical pricing data was lost when prices were edited:
- Unable to reconstruct past profit margins
- Cannot explain why previous customer paid different rate
- Reporting for prior periods inaccurate (uses current price)

Old system had single `price_with_tax` field on `ProductPrice`.

### Decision

Implement versioned pricing with historical preservation:

1. **Add Validity Window Fields**:
   ```python
   class ProductPrice(models.Model):
       valid_from = models.DateTimeField(default=timezone.now)
       valid_until = models.DateTimeField(null=True, blank=True)  # NULL = current
       
       @property
       def is_current(self):
           return self.valid_until is None
   ```

2. **On Price Update Strategy**:
   - When editing price row R1, do NOT overwrite in place
   - Create new row R2 with `valid_from = now()`, `valid_until = NULL`
   - Set R1's `valid_until = yesterday_at_midnight` (or precise timestamp)
   - R1 remains in DB with `is_current=False`

3. **Preserve Derived Fields**: Include brand/model/unit snapshots:
   - If user changes brand on edit, R2 still remembers what R1 pointed to
   - Unit derived from selected model captured at time of creation
   - JSON field `derived_snapshot` optional for full context

4. **Unique Constraint on Current Rows**:
   ```python
   class Meta:
       constraints = [
           UniqueConstraint(
               fields=['model', 'service_item'],
               condition=Q(valid_until=None),
               name='unique_current_price_per_catalog_item'
           )
       ]
   ```

### Consequences

**Positive:**
- Full audit trail of pricing changes
- Accurate historical reporting
- Ability to reconstruct past profitability
- Transparent explanation of customer charges

**Negative:**
- Larger database (multiple rows per product over time)
- More complex queries (need to join history for trends)
- Potential performance degradation on large catalogs

**Mitigations:**
- Index `valid_from` and `valid_until` columns
- Materialized view for "current price" cache
- Archive old history after configurable period (e.g., 7 years)

### References
- Migration: `products/migrations/0002_productprice_history_and_current_constraint.py`
- Views: `products/views.py` (`ProductPriceCreateView`, `ProductPriceUpdateView`)
- Forms: `products/forms.py` (date validation helpers)

---

## ADR-0006: Import Rollback System

**Status**: Accepted  
**Date**: 2026-04-21  
**Authors**: Sean Liu

### Context

CSV imports for companies/locations/assets/contacts sometimes fail mid-way:
- Data format errors
- Validation mismatches
- Duplicate detection conflicts

Before rollback: users had no way to undo imports except manual database restoration.

### Decision

Implement systematic import execution tracking and reverse-capability:

1. **Track Import Runs**: New models:
   ```python
   class ImportRun(models.Model):
       uploaded_file = models.FileField()
       user = models.ForeignKey(User, ...)
       module = models.CharField(choices=['companies', 'locations', 'contacts', 'assets'])
       started_at = models.DateTimeField(auto_now_add=True)
       processed_count = models.IntegerField(default=0)
       created_count = models.IntegerField(default=0)
       updated_count = models.IntegerField(default=0)
       skipped_count = models.IntegerField(default=0)
       error_count = models.IntegerField(default=0)
       
   class ImportRunChange(models.Model):
       import_run = models.ForeignKey(ImportRun, ...)
       record_id = models.UUIDField()  # Target object UUID
       operation_type = models.CharField(choices=['created', 'updated'])
       before_snapshot = models.JSONField(null=True)  # For updates
       after_snapshot = models.JSONField()
   ```

2. **Per-Record Snapshots**: During import:
   - On CREATE: store whole row as `after_snapshot`
   - On UPDATE: capture `before_snapshot` (original values) + `after_snapshot`
   - Serialize model objects to JSON before writing changes

3. **Rollback Execution**: When user clicks "Rollback Latest Import":
   - Fetch all `ImportRunChange` records ordered by id DESC
   - Iterate backwards, reversing each change:
     - Created → DELETE the record
     - Updated → restore `before_snapshot` fields
   - Log rollback result

4. **Safe Defaults**: 
   - Only allow rollback of last import (prevent cascading deletions)
   - Show confirmation modal listing affected counts
   - Disable rollback button if no eligible run exists

### Consequences

**Positive:**
- One-click safety net for bad imports
- Audit trail of every import's impact
- Confidence to experiment with data updates
- Reduced support burden

**Negative:**
- Increased storage (snapshots double-write)
- Longer import processing time
- Complex serialization logic (handle non-JSON fields like images)

**Mitigations:**
- Compress snapshots if size exceeds threshold
- Don't track successful re-imports (already existed)
- Limit retention to 30 days

### References
- Views: `companies/views.py` (`csv_upload`, `confirm_import`, `rollback_import`)
- Utils: `utils/import_rollback.py`
- URLs: `companies/urls.py` (rollback route added)

---

## ADR-0011: WeChat Mini Program for Kering Store Device Inspection

**Status**: Accepted  
**Date**: 2026-09-13  
**Authors**: Sean Liu

### Context

The Kering EUS store health-check is performed onsite and currently collected via a
Feishu questionnaire plus photo archives, then transformed into a per-store Excel
report by two standalone Python tools in the separate `EUS_Device_Inspec` repo
(`kering_inspection_folder_and_report_creation.py`, `FeishuDeviceInspecAutomation.py`).
We want a WeChat mini program to replace the Feishu collection step. Key constraints:
Waypost is a solo-maintained Django monolith; the inspection data contract is rich
(per-device IP/CPU/memory/HDD/OS/C-drive/asset-tag, multiple named photos, rack/network
photos, device counts, issue list, signatures); stores often have weak connectivity.

### Decision

1. **Monorepo.** The mini program lives in this repo under `miniprogram/`. A solo
   maintainer benefits from atomic backend+client changes and one PR/CI surface;
   the client is a first-party consumer of this backend only.
2. **Backend in Waypost.** A new `inspections` Django app models the workflow and
   the EUS report/photo logic is ported into `inspections/services/`. This reuses
   Waypost auth, roles, companies/locations/assets, and admin.
3. **Kering-specific v1.** The checklist, cover-page labels, device categories, and
   photo-naming rules are hardcoded in `inspections/constants.py`; generalization is
   deferred. This matches the legacy scripts and ships fastest.
4. **Dedicated `inspections` domain, not `audit`.** The existing `AssetAudit` /
   `AssetAuditRecord` models do not carry the device readings, rack/network photos,
   counts, or signature fields the Kering report needs, so a separate domain keeps
   `assets.Asset` clean (Kering-only attributes live on `InspectionDevice`).
5. **Auth: WeChat `jscode2session` + SimpleJWT bound to staff accounts.** First launch
   binds an existing `User` (username/password + `wx.login` code) via
   `accounts.WechatIdentity`; later launches use seamless `wx.login`. The AppSecret is
   server-side only and `session_key` is never persisted.
6. **Offline-first with idempotent sync.** The client caches an inspection and queues
   mutations; writes carry `client_device_uid` / `client_photo_uid` and the server
   upserts on unique constraints, so re-syncing never duplicates rows.

### Consequences

- Positive: one system, one CI, atomic changes; reuses proven roles/scoping; the field
  workflow works without connectivity; report deliverable format is preserved.
- Trade-offs: porting the nuanced EUS Excel/photo logic risks parity regressions —
  mitigated by centralizing rules in `inspections/services/transforms.py` +
  `photo_naming.py` and a report parity test against the golden `22149 Gucci SZOL`
  output. Cover-page count mapping and the monitor-size table remain heuristics to tune.
- The `inspections` app is Kering-shaped; generalizing to other clients is future work.

### References

- Spec: `docs/MINIPROGRAM_SPEC.md`; client: `miniprogram/README.md`
- Models: `inspections/models.py`; constants: `inspections/constants.py`
- Services: `inspections/services/{transforms,photo_naming,report_generator}.py`
- API: `api/inspection_views.py`, `api/inspection_serializers.py`, `api/views_auth.py`
- Import: `inspections/management/commands/import_kering_master.py`
- Settings: `waypost/settings.py` (SimpleJWT + WeChat config), `.env.example`

---

## ADR-0012: Unified Design System and Token Single Source of Truth

**Status**: Accepted  
**Date**: 2026-10-05  
**Authors**: Sean Liu

### Context

The web front end accumulated visual definitions in three places with no single
authority: a large inline `<style>` block in `templates/base/base.html`, 24+
per-template `{% block extra_css %}` blocks, and the WeChat mini program's own
`app.wxss`. The consequences were user-visible defects:

- The page background used a decorative purple gradient
  (`linear-gradient(135deg,#667eea,#764ba2)`) duplicated in both `base.html` and
  `dashboard.html`. Outline buttons placed on it (e.g. the "Dashboard" button on
  `/inspections/batches/`) had too little contrast to read reliably.
- A fragile fix that forced outline buttons to render solid only matched
  `.row.mb-4` headers, so newer `.d-flex ... mb-4` headers were not covered.
- Some essential controls were hidden until hover via `opacity: 0`.
- The top navigation wrapped to two lines when the viewport was not wide enough,
  because nothing moved overflow items out of the bar.
- The mini program used an olive-green palette (`#4F6228`) that did not match the
  web app at all.

We needed one design language, applied consistently to both surfaces, with a
mechanism that stops the drift from recurring.

### Decision

Adopt a token-driven design system with a single source of truth and CI-enforced
guardrails.

1. **Single token source.** All visual values live in `static/design/tokens.json`.
   `scripts/build_design_tokens.py` generates both `static/css/tokens.css`
   (`:root { --wp-* }`) and `miniprogram/styles/tokens.wxss` (`page { --wp-* }`),
   so the web app and the mini program are structurally guaranteed to share an
   identical palette, type scale, spacing, radii, shadows and motion.
2. **Shared component layer.** `static/css/waypost.css` layers a blended
   Apple-HIG-clarity + Jira/ServiceNow-density component system on top of
   Bootstrap 5.3 (buttons, cards, tables, forms, badges, alerts, empty states,
   pagination, nav). Bootstrap is kept; tokens restyle it.
3. **Priority+ navigation.** `static/js/nav-overflow.js` keeps the top bar on one
   row, moving overflow items into a "More" (…) dropdown and collapsing to the
   hamburger below the `lg` breakpoint.
4. **Mandatory guide.** `docs/DESIGN_SYSTEM.md` documents the language and the
   hard rules (no raw colour literals, no opacity-hidden controls, no
   outline-on-gradient, single-row nav, AA contrast).
5. **Automated enforcement.** `tests/test_design_system.py` runs inside the
   existing required backend CI job: it fails on token drift, raw colour literals
   outside the token files/allowlist, and banned opacity/gradient/outline
   patterns. Mini program styles are linted with `stylelint`.

### Consequences

- **Positive:** one place to change any visual value; web and mini program cannot
  drift apart; the contrast/opacity/nav defects are prevented by CI rather than
  by reviewer memory; new pages inherit a consistent look from shared components.
- **Negative / trade-offs:** a one-time big-bang migration of 119 templates and 8
  mini program pages; the generated files must be rebuilt after any token change
  (the drift check makes forgetting this fail loudly); the mini program moves from
  `rpx` to token `px` for shared dimensions.
- **Mitigations:** delivered as a sequence of module-scoped PRs to keep CI green
  and review manageable; the generator is deterministic and idempotent; dark mode
  is deferred but the token structure already anticipates a `[data-theme]` block.

### References

- Guide: `docs/DESIGN_SYSTEM.md`
- Tokens: `static/design/tokens.json`; generator: `scripts/build_design_tokens.py`
- Generated: `static/css/tokens.css`, `miniprogram/styles/tokens.wxss`
- Components: `static/css/waypost.css`; nav: `static/js/nav-overflow.js`
- Enforcement: `tests/test_design_system.py`, `miniprogram/.stylelintrc.json`
- Shell: `templates/base/base.html`

---

## ADR-0013: Mixed Primary Keys — UUID Where Identity Leaves the System

**Status**: Accepted (recorded retroactively; the split is original, never migrated)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

Issue #20 asked for an ADR titled "UUID primary keys for assets/companies". That premise is
wrong in a way worth recording: **`companies` does not use UUIDs.** Three live documents
assert otherwise — `docs/API_GUIDE.md:146`, `docs/DATABASE_MIGRATION.md:154`, and
`docs/SECURITY.md:41`, which lists companies among the models with "non-enumerable primary
keys". Company, location and contact IDs are enumerable integers and appear in URLs
(`companies/urls.py:17`). Only `docs/DATABASE_SCHEMA.md:17` describes the split correctly.

### Decision

There is **no abstract base class** (`abstract = True` has zero hits repo-wide). Each UUID
model declares its own `id = models.UUIDField(primary_key=True, default=uuid.uuid4,
editable=False)`; roughly 25 models repeat that line. `DEFAULT_AUTO_FIELD = 'BigAutoField'`
is set globally (`waypost/settings.py:550`) and again, redundantly, in every `AppConfig`.

| Scheme | Models |
|---|---|
| UUID | all 8 `assets.*`, all 7 `inspections.*`, all 4 `reports.*`, `accounts.User` / `UserSession` / `LoginAttempt` / `WeChatIdentity`, `purchases.PurchaseReceipt` |
| Integer (`BigAutoField`) | all of `companies.*`, `quotations`, `deliveries`, `invoices`, `products`, `customers`, `purchases.PurchaseOrder` |
| Singleton `pk=1` (`PositiveSmallIntegerField`) | `accounts.SystemSMTPSettings` |

The dividing line is **whether the identifier leaves the database**. UUIDs go to models whose
IDs are handed to clients or minted outside a single request — mini-program payloads, asset
tags and QR codes, cross-device sync, client-generated `uid`s — where a non-enumerable,
collision-free identifier can be created without a central allocator. Integer keys stay on
models that are only ever referenced inside one database and surfaced in admin URLs.

The split is deliberately **type-erased at the one place that must span both**:
`ImportRunChange.object_pk` is a `CharField(max_length=64)` holding `str(instance.pk)`
(`utils/import_rollback.py:76`), so a single rollback table can record changes to a UUID
asset and an integer company alike. ADR-0006's text describes that column as
`record_id = models.UUIDField()`, which would not compile against integer-PK companies —
that line is wrong and this ADR corrects it.

### Consequences

- **Positive:** client-minted identifiers cannot collide or be guessed; one rollback
  mechanism covers both schemes; no cross-app PK coupling.
- **Negative:** the rule is invisible in the code — nothing names it, so a new model picks a
  scheme by copying its neighbour. Imports match on **natural keys**, never PKs
  (`inspections/services/kering_import.py:267-274`), so UUIDs buy nothing for CSV matching.
  JSON payloads must hand-stringify (`assets/views.py:557`).
- **Live defect found while writing this:** `accounts/urls.py:44` routes
  `sessions/<int:pk>/terminate/` while `UserSession.pk` is a UUID, and
  `templates/accounts/session_list.html:62` reverses it with `session.pk` — the session list
  page raises `NoReverseMatch`. Recorded in the backlog, not fixed here.
- **Mitigation:** `docs/DATABASE_SCHEMA.md` is the authoritative table; the three documents
  above must be corrected rather than left to contradict it. No migration anywhere alters an
  `id` field, so the split can be changed per-model only with a data migration.

### References

- `waypost/settings.py:550`; `accounts/apps.py:8`; `assets/models.py:267`;
  `companies/migrations/0001_initial.py:21`; `accounts/models.py:962`;
  `purchases/models.py:192`; `companies/models.py:1002`
- `utils/import_rollback.py:76`; ADR-0006 (its `record_id` line is superseded by this ADR)
- `docs/DATABASE_SCHEMA.md:17` (accurate); `docs/SECURITY.md:41`,
  `docs/API_GUIDE.md:146`, `docs/DATABASE_MIGRATION.md:154` (inaccurate)

---

## ADR-0014: Two-Factor Authentication Is an Opt-In Login Gate, Not an OTP-Enforced Session

**Status**: Accepted (records what the code does; the gap it exposes is open)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

`docs/SECURITY.md` presents 2FA as a protection and issue #20 calls it "Mandatory 2FA via
django-otp (TOTP)". Neither is true. Recording the real mechanism matters because the
difference between "a second factor is checked at login" and "a verified OTP state is
required to act" is the whole security property.

### Decision

`django-otp==1.6.0` (`requirements.txt:4`) supplies **only the TOTP and Static device
models**. The gate is hand-rolled in `LoginView.form_valid` (`accounts/views.py:116-131`):

1. `user.two_factor_enabled` → stash `pre_2fa_user_id` in the session, redirect to verify;
2. else `user.force_2fa_setup` → redirect to setup;
3. **else log straight in.**

Both flags default `False` (`accounts/models.py:174-182`). Verification calls
`django.contrib.auth.login` (`accounts/views.py:261`), **never `django_otp.login()`**, so no
OTP-verified state is ever attached to the session. `OTPMiddleware` is installed
(`waypost/settings.py:300`) but inert: `is_verified()` is called nowhere, `otp_required` is
imported at `accounts/views.py:35` and used zero times, and `OTP_LOGIN_URL` is dead config.

### Consequences

- **Not mandatory for anyone.** `force_2fa_setup` appears in no form and no admin fieldset
  (admin exposes only `two_factor_enabled`, `accounts/admin.py:68-70`), so it is settable
  only by a raw ORM write. `docs/SECURITY.md:172`'s checklist item "2FA enforced for
  privileged accounts (`force_2fa_setup`)" therefore cannot be ticked through the UI.
- **Self-service disable.** `disable_2fa` is `@login_required` + POST
  (`accounts/views.py:988-1001`): any user disables their own 2FA and deletes all devices,
  with no re-authentication and no token.
- **The API bypasses it entirely.** `WeChatLoginView` (`api/views_auth.py:180-217`,
  `AllowAny`, `authentication_classes = []`) issues access and refresh JWTs from a
  `wx.login` code with no 2FA check — including for a superadmin. See ADR-0019.
- **Two divergent enrolment paths.** `TwoFactorSetupView` uses `device.config_url` +
  `device.verify_token`; `setup_2fa_simple` uses `pyotp` with a session-stored secret and
  verifies via `pyotp.TOTP.verify` (`accounts/views.py:901`), bypassing django-otp's
  throttling and drift handling. Both mint 10 `StaticToken`s and duplicate them into
  `User.backup_tokens` JSON, so two stores can disagree.
- **No test covers the gate**, the verify view, or forced setup; the only 2FA test is an
  `UnboundLocalError` regression (`accounts/tests_regressions.py:18-42`).
- **What would make it real:** `django_otp.login()` at verification plus `otp_required` (or a
  middleware asserting `is_verified()`) on privileged views; `force_2fa_setup` exposed in the
  admin and the user form; re-authentication before disable; one enrolment path; and an
  explicit decision on whether the JWT path must honour 2FA. Each is a separate change —
  this ADR records the gap rather than pretending it is closed.

### References

- `waypost/settings.py:270-272,300,561-562`; `accounts/views.py:35,116-131,261,901,988-1001`
- `accounts/models.py:174-182`; `accounts/admin.py:68-70`; `api/views_auth.py:180-217`
- `accounts/tests_regressions.py:18-42`; `docs/SECURITY.md:44,52,172`

---

## ADR-0015: One System SMTP Identity for Django Mail, a Parallel Per-User Path for Dispatch

**Status**: Accepted (recorded retroactively)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

Three documents say outbound mail "goes through each user's `UserMailboxSettings`
(`DatabaseSMTPEmailBackend`)" — `docs/DEPLOYMENT.md:197`, `docs/WORKFLOW_GUIDE.md:300`,
`docs/WEB_ROUTES.md:289`. The parenthetical is false: that backend has **no user, message or
mailbox awareness whatsoever**. Two separate sending paths exist, and conflating them is how
a credential failure gets diagnosed as an SMTP outage.

### Decision

**Path 1 — Django mail.** `EMAIL_BACKEND = 'accounts.email_backends.DatabaseSMTPEmailBackend'`
(`waypost/settings.py:601`). The backend resolves exactly one row: the `SystemSMTPSettings`
singleton (`pk=1`, `is_active=True`) via `get_active()`
(`accounts/email_backends.py:26,44-53`; `accounts/models.py:724,749-758`). It was born
system-wide for password-reset mail (`accounts/views.py:76`, commit `e5ed905`) and never
gained per-sender routing. `accounts/tests.py:299-340` asserts the system row is the one used.

**Path 2 — per-user dispatch.** `invoices/services.py::_send_via_user_mailbox` (`:585-618`)
builds a raw `smtplib.SMTP_SSL`/`SMTP` client from `dispatch.created_by.mailbox_settings`, and
only when it returns `False` (no mailbox / inactive / empty password) falls back to
`DjangoEmailMessage.send()` (`:632-655`), i.e. Path 1. This arrived separately (commit
`ab81d02`) because dispatch needs the sender's own `From` and `In-Reply-To`/`References`
thread headers, which a shared system identity cannot produce.

The rejected alternative is a message-routing backend that picks credentials per sender.
Raw `smtplib` was chosen instead of extending the backend, which is why the two paths have
separate credential stores, separate timeouts and separate failure modes.

### Consequences

- **A decryption failure looks like an outage.** `decrypt_secret` returns `''` on any failure
  (rotated key, corruption) and only logs a warning (`accounts/crypto.py:90-107`);
  `password or None` then attempts SMTP auth with no password. `manage.py
  check_encrypted_secrets` exists precisely to tell these apart, and exits 1 only on
  `unreadable`, not on a `legacy` row that still decrypts.
- **Stale credentials on a reused connection.** `self._config` / `self._backend` are cached
  per backend instance, so a long-lived instance keeps the credentials it first resolved.
- **Mail can vanish silently.** Unconfigured + `fail_silently=True` makes `send_messages`
  return 0 with no exception.
- **The dispatch path passes no socket timeout** and calls `client.quit()` in `finally`;
  `smtp_security='none'` still calls `login()` in cleartext.
- `timeout_override or config.timeout or None` turns an explicit `0` into blocking.
- **Recipient override is now DEBUG-only and shared.** `TEST_OUTBOUND_EMAIL_OVERRIDE` used to
  default to a real personal address and was honoured in production by both paths; it is now
  empty by default and read through one helper,
  `accounts.email_backends.test_outbound_override()`, so the paths cannot drift apart on it
  again. `accounts/tests_security_guards.py` covers both.

### References

- `waypost/settings.py:601`; `accounts/email_backends.py:26,44-53,80`
- `accounts/models.py:687-705,724,749-758`; `accounts/crypto.py:90-107`
- `invoices/services.py:395,585-618,632-655`; `accounts/tests.py:299-340`
- Commits `e5ed905` (system backend), `ab81d02` (per-user dispatch)

---

## ADR-0016: Two Locales, Project-Level Catalogs, URL-Prefix Routing — and a Preference That Is Stored but Not Applied

**Status**: Accepted (recorded retroactively; two gaps are open)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

The project ships English and Simplified Chinese, and `User.language_preference` looks like
the mechanism that selects between them. It is not. `docs/ARCHITECTURE.md:263` lists it as an
i18n mechanism; no code applies it to a subsequent request.

### Decision

`LANGUAGE_CODE='en-us'`, `LANGUAGES=[('en-us',…),('zh-cn',…)]`, a single
`LOCALE_PATHS=[BASE_DIR/'locale']`, `USE_I18N=True`, `USE_TZ=True`, `TIME_ZONE='UTC'`
(`waypost/settings.py:409-431`). Middleware is stock `LocaleMiddleware` only — there is no
project middleware or signal module anywhere. `i18n_patterns(..., prefix_default_language=True)`
wraps the app URLs while `healthz/`, `admin/`, `api/v1/` and `i18n/` stay unprefixed
(`waypost/urls.py:44,82`).

Language is chosen by the `set_language` POST switcher (cookie/session,
`templates/base/language_switcher.html:6-18`). The **only** `activate()` call in the project
fires once when preferences are saved (`accounts/views.py:447-453`), so it affects that
response and nothing afterwards. `User.save()` normalizes aliases (`zh-hans`/`zh-hant` →
`zh-cn`, `accounts/models.py:80-86,296-297`) — normalization, not activation.

Rejected alternatives, with evidence: **per-app catalogs** (no app has a `locale/` directory);
**a preference-applying middleware or signal** (none exists, `MIDDLEWARE` is all stock);
**gettext on the client** (the mini program uses Chinese-only display maps,
`miniprogram/utils/i18n.wxs:1-13`, `labels.js:1-20`, and sends no `Accept-Language`,
`request.js:61`).

### Consequences

- **One catalog is dead.** `locale/zh-cn/LC_MESSAGES/django.po` (510 msgids) never loads:
  Django's `to_locale('zh-cn')` is `zh_CN`, so only `locale/zh_CN` (2,536 msgids) is read.
  `locale/en` holds 162. Any translation added under `zh-cn` is silently invisible.
- **API errors are English-only** regardless of locale — zero `gettext` imports across all ten
  `api/*.py`. The mini program is Chinese-only regardless of `language_preference`.
- `.mo` files are gitignored and compiled in-image (`compile_translations.py:41-50`,
  `docker/Dockerfile:142`, `.gitignore:10`), so a translation change needs a rebuild.
- Bilingual literals bypass gettext **by design**: `inspections/constants.py:77-108` carries
  `name`/`name_en` pairs, and PDF partials hardcode both languages
  (`templates/quotations/template_v1.html:12-13`). 19 templates have no `{% load i18n %}`.
- **Timezone is stored and never used.** `User.timezone` (`accounts/models.py:203-207`) has no
  consumer: there is no `ZoneInfo(user.timezone)` anywhere. `docker/Dockerfile:126` sets
  `TZ=Asia/Shanghai`, which Django overwrites from `TIME_ZONE='UTC'`; the API records
  `timezone.localtime()` in UTC (`api/inspection_views.py:363,480`). Making the preference
  take effect is a separate change (per-request activation), not a form default.
- Project-supplied date format for Chinese: `waypost/formats/zh_CN/formats.py:5-12`
  (`DATE_FORMAT='Y年n月j日'`).

### References

- `waypost/settings.py:296,409-431`; `waypost/urls.py:44,82`
- `accounts/models.py:80-86,197-207,296-297`; `accounts/views.py:447-453`
- `locale/zh_CN/`, `locale/zh-cn/` (dead), `locale/en/`; `compile_translations.py:41-50`
- `miniprogram/utils/i18n.wxs`, `labels.js`, `request.js:61`
- `docs/ARCHITECTURE.md:263` (claims the preference drives i18n — it does not)

---

## ADR-0017: Production Stack — Rootless Compose, Three Gunicorn Workers, WhiteNoise for Static, a Shared Nginx 443

**Status**: Accepted (recorded retroactively)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

One Aliyun ECS hosts two applications (Waypost and Helpdesk) under two rootless Docker
daemons, sharing the host's nginx and its 443 socket. Several documents describe a different,
older deployment shape ("Gunicorn 4 workers under systemd", "nginx serves `/static/`",
"PostgreSQL 14+ / Redis 6+"), which is the legacy Option-1 path, not what runs.

### Decision

**Three compose services** (`docker-compose.yml`, project `name: waypost` at `:32` so it
cannot collide with Helpdesk's `app`):

- **app** — Gunicorn `--workers 3 --threads 2 --timeout 300` (`docker/Dockerfile:169-176`);
  threads > 1 selects the gthread worker without an explicit `--worker-class`. Bound to
  `0.0.0.0:8000` in-container, published **loopback-only**. `HEALTHCHECK` hits `/healthz/`
  (`Dockerfile:160-161`). `depends_on` both services with `condition: service_healthy`,
  `restart: always`, `stop_grace_period: 60s` (`docker-compose.yml:92-100`).
- **db** — `postgres:16` **Debian**, deliberately not Alpine: musl collation changes index
  ordering (`docker-compose.yml:107-112`). No published port (`:132`). Named volume
  `waypost_pg_data` (`:148-164`) — removing the service does not remove the volume.
- **redis** — `redis:7-alpine` with `--save "" --appendonly no` (`:134-146`): cache only, no
  persistence, so a restart loses nothing that matters.

**Entrypoint** runs `migrate` → `collectstatic --clear` → `exec "$@"` (`docker/entrypoint.sh`);
`collectstatic` is deliberately **not** run at build time (`Dockerfile:152-154`).

**Host nginx** terminates TLS and proxies (`docker/nginx/waypost.conf:124-141`, 300s timeouts,
`X-Forwarded-Proto`). The server block has **no `default_server` and no `http2`** (`:20-30`)
because the 443 socket is shared with Helpdesk and selected by `server_name`.
**WhiteNoise serves static inside the container**; nginx explicitly does not
(`waypost.conf:38-42`). Nginx aliases **media** from the bind mount with `expires 7d`/`30d`
and forced attachment (`:113-122`), and caps bodies at `client_max_body_size 20m` (`:94`).

### Consequences

- **Three workers is why mailbox polling cannot live in the app.** `accounts/apps.py:11-24`
  gates the sync thread on `runserver` + `RUN_MAIN`, and that gate must stay: removing it
  starts one poller per worker, and `mailbox_sync._SYNC_LOCKS` is process-local. Production
  polling is a systemd user timer running `manage.py sync_mailboxes --once`, with a Redis
  cache lock for cross-process exclusion (ADR-0002, `docs/DEPLOYMENT.md`).
- **Two different upload caps**: nginx 20 MB per body vs `MAX_UPLOAD_SIZE = 5242880` per file
  (`waypost/settings.py:598`). A request can pass nginx and still be rejected per file.
- **The OSS cutover silently retires the nginx media block**: signed URLs are absolute, so
  `waypost.conf:113-122` stops matching, and the cache lifetimes are duplicated in
  `waypost/storage.py:49-50`. `docs/OSS_STORAGE.md` covers the retirement.
- **Application logs are not durable.** `logs/waypost.log` lives in the container's writable
  layer — compose bind-mounts only media and `template_files` (`docker-compose.yml:83-91`) —
  so a rebuild loses them (`docs/DEPLOYMENT.md:783-785`). Gunicorn access/error go to stdout
  and are kept by the rootless daemon's json-file rotation (20 MB × 5).
- **Documents to correct:** `docs/DEPLOYMENT.md:80` and `docs/ARCHITECTURE.md:264,278` claim
  nginx serves `/static/` (it does not; `DEPLOYMENT.md:601` says the opposite, and
  `waypost/settings.py:495-496` repeats the wrong claim in a comment).
  `docs/ARCHITECTURE.md:275` says "4 workers, systemd"; `DEPLOYMENT.md:264-271` says
  `--workers 4 --timeout 120` and `:325-327` a 60s proxy timeout. `ARCHITECTURE.md:276-277`
  and `DEPLOYMENT.md:78-79` say PostgreSQL 14+ / Redis 6+ while compose pins 16 / 7.

### References

- `docker/Dockerfile:100-120,152-154,160-161,169-176`; `docker/entrypoint.sh`
- `docker-compose.yml:32,79-100,107-146,148-164`; `docker/nginx/waypost.conf:20-48,94,113-141`
- `waypost/settings.py:308-309,495-510,527-545,598`; `requirements.txt:27-34`
- `docs/OSS_STORAGE.md`; `docs/DEPLOYMENT.md`; `docs/ARCHITECTURE.md`

---

## ADR-0018: Environment-Driven Settings with Fail-Fast Guards — and the Two Defaults That Are Not Guarded

**Status**: Accepted
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

There is no staging environment. A change that needs a coordinated edit to a running
production `.env` is therefore a change that can fail in production first. Settings are
env-driven so that a cutover is one `.env` edit that reverts the same way, and guards exist so
that a half-finished configuration refuses to boot rather than booting insecurely.

### Decision

A **hand-rolled env layer** — no `django-environ`, no `python-dotenv` in `requirements.txt`.
`waypost/runtime_setup.py:9-25` loads a repo-local `.env`, and **`os.environ` wins** (`:22`).
Helpers: `_env_bool` (`settings.py:36-41`, accepts `1/true/yes/on`), `_env_first` (`:44-50`),
and `_env_first_with_legacy` (`:53-74`) which reads a new name then a retired one and logs a
deprecation warning when the legacy name wins.

**Four guards raise `ImproperlyConfigured`, all keyed on `not DEBUG`:**

| Condition | Location | Message |
|---|---|---|
| `SECRET_KEY` still starts with `django-insecure-` | `:120-125` | must be set to a strong, non-insecure value |
| `DJANGO_FIELD_ENCRYPTION_KEY` empty | `:164-170` | must be set; includes the `Fernet.generate_key()` command |
| WeChat `APPID` or `APPSECRET` empty | `:181-186` | both must be set |
| Some but not all four `OSS_*` keys set | `:475-484` | "OSS is half-configured: set all four … or none of them" |

The OSS guard's shape is deliberate: **"if you started, finish"**, never "OSS is required" —
the latter would break every box that has no bucket. The backend choice itself is a pure
function, `resolve_default_storage_backend(env)` (`waypost/storage.py:66-75`), with no
`settings` import, so it is unit-testable without a bucket; `oss2` is imported **inside** the
backend (`storage.py:143-154`), never at module scope, so a broken `oss2` install cannot break
a box using FileSystemStorage.

**Deliberately not env-driven:** `PRODUCT_*` / `COMPANY_*` / `PUBLISHER_*` (`:231-256` — three
distinct names, and brand drift already happened once), `LANGUAGE_CODE` / `LANGUAGES` /
`TIME_ZONE` (`:409-431`), `EMAIL_BACKEND` (`:601`), `SESSION_COOKIE_AGE` (`:565`),
`MAX_UPLOAD_SIZE` (`:598`). RFQ **prices** are env-driven on purpose (`:206-209`: "an `.env`
edit, not a migration").

### Consequences

- 🔴 **The honest gap: `DEBUG` defaults `True` (`:108`) and `ALLOWED_HOSTS` defaults `'*'`
  (`:112-116`), and neither is guarded.** A production `.env` that omits `DJANGO_DEBUG=False`
  boots wide open and fires **none** of the four checks — every guard is keyed on `not DEBUG`,
  so the one missing line disables all of them. This is the single highest-value hardening
  change available and is recorded in the backlog rather than silently fixed here, because
  flipping the default changes every dev box and CI run.
- `WAYPOST_RUNNING_TESTS=1` (`:91-95`) disables cookie/SSL hardening in any process that sets
  it; the four guards are unaffected by design (`:85-86`). Hardening is gated on
  `not DEBUG and not _RUNNING_TESTS` (`:573-587`), and `SECURE_REDIRECT_EXEMPT` keeps
  `/healthz/` reachable over plain HTTP (`:593`).
- The five retired `MINIMAX_*` aliases (`:223-227`) have **zero Python consumers** — only
  `accounts/llm_client.py:221` reads `RFQ_LLM_API_KEY`. They exist for the hand-edited `.env`,
  which survives every deploy (`git clean -ffd`, never `-x`), and can be deleted in a 5-line PR
  once the deployed file is renamed.
- **Rejected alternatives, with evidence:** a big-bang `MINIMAX_*` rename (`:53-61` — no
  staging, `.env` survives deploys, so dual-read makes the deploy safe in either order);
  "OSS required" (`:459-462`); HSTS on by default (`:581-585`); build-time `collectstatic`
  (`Dockerfile:152-154`).
- Two citations elsewhere are stale: `settings.py:57` points at `ci-deploy.sh:368` while the
  `rsync --exclude='.env'` is at `:398`; `README.md:192-195`, `docs/WORKFLOW_GUIDE.md:296` and
  ADR-0002's References still document only the retired `MINIMAX_*` names.

### References

- `waypost/settings.py:36-95,108-186,206-227,231-256,459-491,573-598`
- `waypost/runtime_setup.py:9-25`; `waypost/storage.py:38-43,66-75,143-154`
- `tests/test_storage_backend.py:100-128`; `docker/bin/ci-deploy.sh:398`
- `docs/OSS_STORAGE.md`; `docs/RELEASE_PROCEDURE.md` §11 (no staging)

---

## ADR-0019: API Authentication — SimpleJWT for the Mini Program, Session for the Web

**Status**: Accepted (recorded retroactively; supersedes the "session-only" claim in three documents)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

Issue #20 asks for an ADR on "Session-only API authentication (no JWT/token)". The premise is
inverted: the API has been dual-scheme since the mini program shipped, and ADR-0011 decision 5
already records the JWT choice. `docs/SECURITY.md:52,142,147` and `docs/API_GUIDE.md:336`
still assert "no token/JWT authentication … future work" — and `API_GUIDE.md` contradicts its
own §Authentication at `:61-62` in the same file.

### Decision

`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` = SimpleJWT **plus**
`SessionAuthentication`, default permission `IsAuthenticated`
(`waypost/settings.py:378-392`).

**Mini program (bearer).** `wx.login` code → `POST /api/v1/auth/wechat/login/`, or `/bind/`
with username+password on first launch → server calls WeChat `jscode2session`, resolves
`accounts.WechatIdentity`, and mints access + refresh JWTs (`api/views_auth.py:134-217`,
`_tokens_for` at `:26-29`). Access lives 2h, refresh 14d (`settings.py:396-403`). The client
stores them in `wx.setStorageSync` (`mp_access_token` / `mp_refresh_token`) and sends
`Authorization: Bearer` on every call **including `wx.uploadFile`**
(`miniprogram/utils/request.js:10-26,62-65,86-90`). On 401 it refreshes once and retries, else
clears the tokens and routes to bind/login (`request.js:74-79`, `utils/auth.js:73-76`).

**Why JWT at all:** `wx.request` has no cookie jar, so a session cookie cannot survive between
calls. There is no `sessionid`/`csrftoken` handling anywhere in `miniprogram/`. Session auth
remains for the web UI and the browsable API.

**CSRF.** No `csrf_exempt` anywhere in `api/`. JWT requests are CSRF-exempt by DRF design;
session-authenticated unsafe methods enforce CSRF (`docs/API_GUIDE.md:84-95`). The three
unauthenticated endpoints (`lookup`, `bind`, `login`) set `authentication_classes = []` +
`AllowAny` (`api/views_auth.py:68-69,137-138,183-184`), and `auth/token/refresh/` is open
(`api/urls.py:38`).

**Authorization is queryset scoping, not a permission class.** Every ViewSet uses plain
`IsAuthenticated` plus a role-scoped `get_queryset()` (`api/views.py:38,44-55,70-81,234-236`);
company creation raises DRF `PermissionDenied` in `perform_create` (`:83-88`, a 403 — it used
to raise the builtin `PermissionError`, which Django turned into a 500). Inspections add
object-level `CanAccessInspection` (`api/inspection_views.py:98-118`) over
`get_assigned_inspections` / `can_run_inspection` (`accounts/models.py:549-579`).
`IsSuperadminOrReadOnly` was **dead code** — defined, never referenced, and not even a
`BasePermission` subclass — and was deleted in `0ae8546`; nothing replaced it because the
scoping above was always the real enforcement.

### Consequences

- 🔴 **`WeChatLookupView` is an unauthenticated user-enumeration oracle**: it returns
  username/phone matches to anyone (`api/views_auth.py:84-94`). It exists so the bind screen
  can find an account, and it needs a rate limit or a proof-of-ownership step.
- 🔴 **`ROTATE_REFRESH_TOKENS=False`** — a stolen 14-day refresh token stays valid for its
  whole life, and `JWT_SIGNING_KEY` falls back to `SECRET_KEY` (`settings.py:177`), so one leak
  is two.
- **2FA does not apply to this path at all**, even for a superadmin (ADR-0014).
- `accounts/views.py:26` imports `csrf_exempt` and never uses it — a dead import that reads
  like an exemption.
- The three documents above must be corrected; leaving them makes the next reader design
  against an authentication scheme that does not exist.

### References

- `waypost/settings.py:177,378-403`; `api/urls.py:38`; `api/views_auth.py:26-29,68-94,134-217`
- `api/views.py:38,44-55,70-88,234-236`; `api/inspection_views.py:98-118`
- `accounts/models.py:549-579`; `miniprogram/utils/request.js`, `utils/auth.js:73-76`
- ADR-0011 decision 5; `docs/SECURITY.md:52,142,147`; `docs/API_GUIDE.md:61-62,84-95,336`

---

## ADR-0020: Three PDF Paths — and What ADR-0008 Actually Decided

**Status**: Accepted (restates ADR-0008 with its real scope; supersedes the blanket reading)
**Date**: 2026-10-22
**Authors**: Sean Liu

### Context

ADR-0008 is listed in the table of contents as *Accepted, 2026-04-17*, titled "HTML-to-PDF
Generation Without LibreOffice" — but **its body was never committed**. The same is true of
ADR-0007, ADR-0009 and ADR-0010: four TOC entries with no decision text (verified with
`git log -S` back to `7406d6d`). The de-facto text lives in `docs/ARCHITECTURE.md:97-98` and
`:266` ("PDF rendering is WeasyPrint, deliberately without LibreOffice (ADR-0008)") and in
`CHANGELOG.md:1333` (v0.1.1, same date: "removing LibreOffice dependency for **core customer
documents**").

Read as a blanket claim, that is contradicted by the code. Read with the scope the changelog
gives it — core customer documents rendered from HTML — it is still true.

### Decision

**Three producers, by document type:**

| Path | Documents | Code |
|---|---|---|
| **WeasyPrint** (HTML → PDF) | quotation PDFs; delivery-order download PDFs | `quotations/services.py:104-127` (`HTML(string=…).write_pdf()` at `:127`), `deliveries/services.py:222-257`; `requirements.txt:17` (weasyprint 62.3) |
| **LibreOffice / `soffice`** (XLSX → PDF) | invoice document download; email-dispatch attachments for delivery and invoice workbooks | one shared converter, `utils/generated_documents.py:35-85` (`_find_soffice_binary`, `convert_xlsx_to_pdf`); callers `invoices/views.py:283-289`, `invoices/services.py:488,519` |
| **reportlab** (legacy) | asset-list PDF export only | `assets/views.py:1186-1197`; `requirements.txt:15` |

Inspection reports are **xlsx-only**, with no PDF path
(`inspections/services/report_generator.py:15,191-198`).

LibreOffice **is installed**, in the image's `runtime` stage
(`docker/Dockerfile:81,100-101`, `libreoffice-calc-nogui` with a fallback package), and the
build asserts `command -v soffice` (`:107-115`), warms the profile (`:120`), and separately
probes WeasyPrint's CJK rendering (`:131-134`). So the two paths are both first-class, not one
plus an accident.

**Everything generated is ephemeral.** `waypost-gen-*` `TemporaryDirectory`s
(`generated_documents.py:32,88-97`), an `_OwnedFile` whose cleanup is tied to
`FileResponse.close` (`:107-133`), and `generated_document_dir('dispatch')` for email
(`invoices/services.py:629`). Nothing writes to `MEDIA_ROOT` or the default storage. The old
per-app quotation converter was **deleted** for having no callers and leaking files into
`MEDIA_ROOT/quotations/generated/` (`quotations/services.py:1-8`, `docs/DEPLOYMENT.md:219`).

### Consequences

- **A missing converter degrades silently.** Missing `soffice`, a non-zero exit, or the 120s
  timeout all make `convert_xlsx_to_pdf` return `None` — it never raises
  (`generated_documents.py:66-80`). The download view warns and serves the xlsx
  (`invoices/views.py:284-286`); dispatch simply omits the PDF. A customer can therefore
  receive an xlsx where a PDF was intended, with no error anywhere.
- **Delivery documents have two different renderers depending on channel**: HTML→PDF for a
  download, XLSX→PDF for an email attachment. They are not byte-identical documents.
- **Concurrent conversions share one LibreOffice profile** (`docs/DEPLOYMENT.md:1064`), which
  is the documented cause of intermittent conversion failures under load.
- **ADR-0008's scope is hereby restated:** WeasyPrint renders the customer-facing HTML
  documents without LibreOffice; LibreOffice is used for workbook→PDF conversion. The blanket
  "no LibreOffice" reading in `docs/ARCHITECTURE.md:97-98,266` is superseded and must be
  corrected. ADR-0007/0009/0010 still have no bodies; that gap is recorded in the backlog.

### References

- `utils/generated_documents.py:1-16,32,35-85,88-133`; `requirements.txt:15,17`
- `quotations/services.py:1-8,104-127`; `deliveries/services.py:222-257`
- `invoices/views.py:283-289`; `invoices/services.py:488,519,629`; `assets/views.py:1186-1197`
- `docker/Dockerfile:81,100-134`; `docs/DEPLOYMENT.md:219,1053-1065`
- `docs/ARCHITECTURE.md:97-98,266`; `CHANGELOG.md:1333` (v0.1.1); ADR-0008 (TOC only)

---

## ADR-0021: Helpdesk → Wenqu Merge: One User Table, Profile-Marked Customers, and the tv Claim

**Status**: Accepted
**Date**: 2026-11-19
**Authors**: Sean Liu

### Context

The standalone FastAPI "Helpdesk" (SQLAlchemy + PG17, its own `users` table with
`role='admin'|'user'`, bcrypt passwords, WeChat openid columns and per-user `can_*`
permission bits) is merged into Waypost and rebranded **问渠 (Wenqu)**. Waypost already
has a custom `accounts.User` (UUID pk, AbstractUser) with an administrator-role system
and a staff WeChat mini program whose openids live in `accounts.WeChatIdentity` keyed
by appid. Two identity systems had to become one without granting customers staff
surface, and the helpdesk's JWT revocation mechanism (`token_version`, alembic 0007)
had to survive the move.

### Decision

**Merge, not coexist.** The helpdesk schema is ported to Django models (`wenqu/models.py`),
its services rewritten onto the ORM (`wenqu/services/`), its endpoints served under
`/api/v1/wenqu/` by this application, and its admin UI rebuilt as staff pages under
`/wenqu/`. The legacy stack becomes a co-tenant in retirement (`OPERATIONS_RUNBOOK.md` §7);
production data moves through `wenqu/management/commands/import_helpdesk.py` — natural-key
idempotent upserts, `--dry-run` rollback, per-table source-vs-imported reconciliation,
embeddings parsed from pgvector text and never recomputed, `password_reset_codes` skipped.
The public hostname moves `ams.istore-tech.cn` → `waypost.istore-tech.cn` (one nginx site,
one cert with both SANs; `DEPLOYMENT.md` §2.15).

**One user table, no customer role.** A Wenqu customer is an `accounts.User` with
`username = external_id` (`wx_<openid[:12]>`), an unusable password, a
`WeChatIdentity(appid=settings.WECHAT_WENQU_APPID, openid)` row, and a `WenquProfile`.
**The presence of the profile row is the customer marker** — `api.wenqu_authentication.WenquCustomerPermission`
gates on it and the staff user list excludes anyone who has one. No `role='customer'`
enum value, no new flag on `accounts.User`, no change to the administrator-role system:
staff administration stays on existing Waypost accounts (`User.can_manage_wenqu()` =
superadmin / IT administrator). Legacy helpdesk admin/role users are imported **as
customers** with their `can_upload/can_chat/can_manage_customers/can_manage_users` bits
preserved on the profile; `is_staff`/`is_superuser` are never set by the import.
**unionid never auto-merges accounts** — it is not even a legacy column; imported
identities carry an empty one and the first `wx.login` fills it in
(`wenqu/services/auth.py`).

**The tv claim travels.** Every customer access token carries `tv = profile.token_version`
(`wenqu/services/auth.py::_tokens_for_profile`); `WenquJWTAuthentication` rejects a token
whose `tv` no longer matches the column, so bumping `token_version` (admin page or reset
flow) ends every older session. Staff tokens carry no `tv` and are exempt; customer
tokens without the claim pass through (backward compatibility). The import preserves
legacy `token_version` verbatim, so a pre-merge bump keeps its effect.

### Consequences

- One login principal, one staff directory, one 2FA/role system; customers can never
  reach staff surface because the profile marker and the appid-scoped identity are
  both checked (`WenquAuthConflict` when an openid is bound to a non-customer).
- The import is re-runnable during the migration window: customers who tried the new
  client before the merge are matched by openid and keep their account; history attaches
  to it. Re-running overwrites profile fields with legacy values — the window freezes
  the legacy app first (`DEPLOYMENT.md` §2.15 Phase 3).
- Legacy PC passwords (bcrypt) are **not** imported; customer auth is WeChat-only, and
  the reset flow is email codes (`PASSWORD_RESET_EMAIL_ENABLED` + SMTP relay).
- Two WeChat mini programs (staff inspection, wenqu customer) share one backend but
  never one appid scope; their CI keys and MP whitelists are separate
  (`DEPLOYMENT.md` §2.14 env gap).

### References

- `wenqu/models.py` (WenquProfile docstring), `wenqu/services/auth.py`, `api/wenqu_authentication.py`
- `wenqu/management/commands/import_helpdesk.py`; `accounts/models.py` (User, WeChatIdentity)
- `docs/DEPLOYMENT.md` §2.14–§2.15; `docs/OPERATIONS_RUNBOOK.md` §7
- helpdesk `backend/app/models.py`, alembic 0007 (token_version + password_reset_codes)

---

## ADR-0022: Embeddings as JSONField with a Query-Time vector Cast — ivfflat as Named Debt

**Status**: Accepted (performance debt open)
**Date**: 2026-11-19
**Authors**: Sean Liu

### Context

The helpdesk stored 1536-dim embeddings in native pgvector `vector(1536)` columns with
ivfflat cosine indexes (alembic 0003/0005 fixed the opclass trap: an ivfflat index only
serves its own operator family, so the default `vector_l2_ops` made every `<=>` retrieval
a sequential scan). Waypost develops on SQLite and runs PostgreSQL in production; a
native `vector` column would break the dev backend and every `manage.py test` run.

### Decision

**Embeddings are `JSONField` (jsonb) lists** on `KBChunk.embedding` and
`TriagePlaybook.embedding`. `wenqu/migrations/0002_pgvector` only creates the extension
(`CREATE EXTENSION IF NOT EXISTS vector`, no-op on SQLite). Retrieval casts at query
time: `(c.embedding::text)::vector <=> CAST(%s AS vector)` with the query vector bound
as pgvector text `'[0.1,0.2,...]'` (`wenqu/services/kb/vector_store.py`) — a Python list
would bind as `numeric[]` and break the operator. The production image is
`pgvector/pgvector:pg16` (postgres:16 + extension; Debian-based, not alpine, for collation
stability — `docker-compose.yml`).

**ivfflat is deferred, explicitly.** An ivfflat index requires a real `vector` column;
indexing a jsonb cast is not possible without a schema change. The KB is small enough
that a sequential cosine scan is acceptable today. This is recorded debt, not an
oversight: when retrieval latency shows up, the fix is a generated/stored `vector`
column + `vector_cosine_ops` ivfflat (lists ≈ rows/1000), keeping the JSONField as the
portable source of truth.

**The import never recomputes.** `import_helpdesk` parses the legacy pgvector text
`'[...]'` into a JSON list (`parse_embedding`), so migrated chunks keep byte-equivalent
vectors with no provider keys and no embedding spend.

### Consequences

- One codebase, two backends: SQLite dev/test needs no extension; PG prod gets the
  cosine operator. Tests assert the JSON round-trip (`wenqu/tests.py`).
- Every retrieval is a seq scan + cast until the debt is paid; fine at current KB size,
  named here so it is a decision rather than a surprise.
- The opclass trap from helpdesk alembic 0003/0005 cannot recur silently: there is no
  index to mis-configure yet, and the future index must use `vector_cosine_ops`.

### References

- `wenqu/models.py` (module docstring), `wenqu/migrations/0002_pgvector.py`
- `wenqu/services/kb/vector_store.py`; `docker-compose.yml` (db image)
- `wenqu/management/commands/import_helpdesk.py::parse_embedding`
- helpdesk alembic 0003/0005 (opclass fixes), `backend/app/models.py` index notes

---

## ADR-0023: Synchronous SSE on WSGI gthread — ASGI Rejected

**Status**: Accepted
**Date**: 2026-11-19
**Authors**: Sean Liu

### Context

Wenqu chat replies, regeneration and TTS playback stream as Server-Sent Events
(`wenqu/services/sse.py`): a synchronous generator yields `event: …\ndata: {...}\n\n`
frames for the whole model turn (tens of seconds). The production stack is Gunicorn
WSGI sync workers behind a shared nginx (ADR-0017). Each open stream holds a worker
thread for its duration, so a handful of concurrent customers could starve the rest
of the API at the old `--threads 2`.

### Decision

**Stay WSGI; expand the thread pool.** `docker/Dockerfile` runs
`gunicorn waypost.wsgi:application --workers 3 --threads 8 --timeout 300` — 24 gthreads,
tunable per host via `GUNICORN_CMD_ARGS` without a rebuild. The streams are plain
synchronous generators returned as `StreamingHttpResponse`; no async views, no
channels, no second server. nginx carries three prefix locations
(`/api/v1/wenqu/chat/stream`, `/chat/regenerate/stream`, `/speak/stream`) with
`proxy_buffering off`, `proxy_cache off` and `proxy_read_timeout 300s` matching
Gunicorn (`docker/nginx/waypost.conf`, PR4), each repeating the full
`proxy_set_header` block because nginx does not inherit location-level headers into
siblings — dropping `X-Forwarded-Proto` there would 403 every POST.

**ASGI was considered and rejected:** it would mean a second application entrypoint
(`waypost/asgi.py` exists but is unused), an async rewrite of the service layer that
is deliberately synchronous (ORM calls, provider HTTP, ffmpeg subprocesses), a different
worker class and deploy surface, and new failure modes on a host that shares its nginx
with another application — all to solve a concurrency problem that 24 threads already
solve at this customer count. Revisit when concurrent streams routinely approach the
thread pool, not before.

### Consequences

- One deploy path, one worker model, the existing rollback story unchanged.
- Thread exhaustion is the capacity ceiling: ~24 simultaneous streams across 3 workers;
  beyond that, new requests queue. The IP throttle on `/speak/stream`
  (`WENQU_SPEAK_STREAM_IP_*`) is blast-radius protection, not the real gate — per-customer
  `can_chat` is.
- A slow model turn can hold a thread up to 300s; the matched nginx/Gunicorn timeouts
  mean a cut stream is a deliberate timeout, not a proxy mismatch.

### References

- `docker/Dockerfile` (CMD comment), `docker/nginx/waypost.conf` (SSE locations)
- `wenqu/services/sse.py`, `api/wenqu_views.py`; `waypost/settings.py` (WENQU_SPEAK_STREAM_*)
- ADR-0017 (production stack), ADR-0019 (API authentication)

---

## ADR-0024: Private Media on a Dedicated Storage Outside the Public /media/ Alias

**Status**: Accepted
**Date**: 2026-11-19
**Authors**: Sean Liu

### Context

nginx serves `MEDIA_ROOT` world-readable through `location /media/ { alias … }`
(`docker/nginx/waypost.conf`), and the default storage backend may become Aliyun OSS
(`waypost/storage.py`, ADR in `OSS_STORAGE.md`). Wenqu customer uploads (voice messages,
photos), generated TTS audio and KB page attachments are **customer-private**: they must
only leave through authenticated Django views, never a static alias or a public bucket
URL. The helpdesk wrote them straight to an `uploads/` disk directory.

### Decision

**A dedicated `FileSystemStorage` rooted at `WENQU_PRIVATE_MEDIA_ROOT`**, exposed as a
callable (`wenqu/services/storage.py::private_storage`) so `FileField` resolves it lazily
and tests can override the root. Compose bind-mounts `${WAYPOST_DATA_DIR}/private` →
`/app/private_media` — deliberately outside `MEDIA_ROOT` and outside the default
(possibly OSS) backend. Downloads go through `FileResponse` + `storage.open` behind
customer auth (`api/wenqu_views.py` protected-download view); the URL convention
(`wenqu/uploads/<file_id>__<name>`, `wenqu/tts/…`, `wenqu/kb_assets/…`,
`wenqu/triage_assets/…`) is owned by `wenqu/services/utils/file_store.py`.

**KB source documents are the exception that proves the boundary:** they are
operator-managed files under `WENQU_KB_SOURCES_DIR`, read with plain `open()` and never
touching the storage API — they are not media (`wenqu/services/kb/paths.py`).

The repo-hygiene gate (`tests/test_repo_hygiene.py::MediaStorageApiTests`) forbids
`.path`, `MEDIA_ROOT` directory writes and `Path(MEDIA_ROOT)` in application code, so
the private/public boundary cannot be crossed by a stray idiom that works on a dev box.

### Consequences

- No nginx location, no www-data ACL, no bucket policy is needed for private files;
  the only route out is an authenticated view, and the import writes through the same
  `storage.save` path as live uploads.
- Private media is **not** covered by the public-media OSS cutover or its lifecycle
  rules; it lives on the bind mount and is backed up with the host data, a separate
  retention decision.
- Two storage roots to provision and remember (`private/`, `kb_sources/`); compose
  already mounts both, and `DEPLOYMENT.md` §2.14 lists them.

### References

- `wenqu/services/storage.py`, `wenqu/services/utils/file_store.py`, `wenqu/models.py` (UploadFile)
- `docker-compose.yml` (private + kb_sources mounts), `docker/nginx/waypost.conf` (/media/ alias)
- `tests/test_repo_hygiene.py` (MediaStorageApiTests); `docs/OSS_STORAGE.md`

---

## ADR Template

When creating new ADRs, follow this template:

```markdown
## ADR-NNNN: Title

**Status**: [Proposed | Accepted | Deprecated | Superseded]  
**Date**: YYYY-MM-DD  
**Authors**: [Your Name]

### Context
What problem are you trying to solve? What constraints exist?

### Decision
What did you decide? Include code snippets, diagrams, schemas.

### Consequences
- Positive outcomes
- Negative outcomes / trade-offs
- Mitigations

### References
Links to related files, migrations, discussions
```

---

*Last Updated: November 19, 2026*
