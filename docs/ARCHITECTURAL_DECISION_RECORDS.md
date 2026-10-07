# Architecture Decision Records (ADR) - Waypost

This directory contains architectural decision records for the Waypost.

## Table of Contents

| ID | Title | Status | Date |
|----|-------|--------|------|
| [0001](#adr-0001-service-catalog-separation) | Service Catalog Separation | Accepted | 2026-04-29 |
| [0002](#adr-0002-mailbox-driven-rfq-automation) | Mailbox-Driven RFQ Automation | Accepted | 2026-04-29 |
| [0003](#adr-0003-direct-dispatch-fulfillment) | Direct Dispatch Fulfillment | Accepted | 2026-04-29 |
| [0004](#adr-0004-hardware-only-assets-boundary) | Hardware-Only Assets Boundary | Accepted | 2026-04-29 |
| [0005](#adr-0005-product-price-history-lifecycle) | Product Price History & Lifecycle | Accepted | 2026-04-29 |
| [0006](#adr-0006-import-rollack-system) | Import Rollback System | Accepted | 2026-04-21 |
| [0007](#adr-0007-multi-role-administrator-access-control) | Multi-Role Administrator Access Control | Accepted | 2026-04-24 |
| [0008](#adr-0008-html-pdf-generation-without-libreoffice) | HTML-to-PDF Generation Without LibreOffice | Accepted | 2026-04-17 |
| [0009](#adr-0009-warehouse-slot-tracking) | Warehouse Slot Tracking | Accepted | 2026-04-20 |
| [0010](#adr-0010-company-contact-vs-company-user) | Company Contact vs Company User Model | Accepted | 2026-04-21 |
| [0011](#adr-0011-wechat-mini-program-for-kering-store-device-inspection) | WeChat Mini Program for Kering Store Device Inspection | Accepted | 2026-09-13 |
| [0012](#adr-0012-unified-design-system-and-token-single-source-of-truth) | Unified Design System and Token Single Source of Truth | Accepted | 2026-10-05 |

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

[Continue for remaining ADRs...]

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

*Last Updated: October 5, 2026*
