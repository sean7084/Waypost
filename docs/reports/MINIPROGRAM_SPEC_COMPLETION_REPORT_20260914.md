# WeChat Mini Program Spec Completion Report - HengjiAMS1

**Date:** September 14, 2026  
**Repository:** sean7084/HengjiAMS1  
**Spec:** `WeChat_Audit_Mini_Program_task-efd.md` · `docs/MINIPROGRAM_SPEC.md` · ADR-0011  
**Epic:** [#30 Kering Store Inspection Mini Program](https://github.com/sean7084/HengjiAMS1/issues/30)  
**Status:** ✅ IMPLEMENTATION COMPLETE (operator provisioning pending — see §9)

---

## 1. Summary

The Kering EUS store health-check has been digitized: the WeChat mini program replaces the
Feishu questionnaire for onsite collection, and HengjiAMS1 generates the same client
deliverable the legacy EUS scripts produced (`<JDA> <Brand> <Store> Report Per Store.xlsx` +
`Photo/` archive).

Delivered in this repository:

- **New `inspections` Django app** — `StoreInspection`, `InspectionDevice`, `InspectionPhoto`,
  `InspectionIssue` with Kering checklist/label constants as the single source of truth for
  both the API and the report renderer.
- **WeChat + JWT auth** — `jscode2session` openid binding to existing staff `User` accounts,
  SimpleJWT access/refresh, first-launch bind then seamless `wx.login`.
- **DRF REST API** (`/api/v1/inspections/...`) — engineer-scoped, idempotent offline sync.
- **Report + photo service** — the EUS logic ported into Django and validated against a real
  golden workbook.
- **Offline-first native mini program** — 10 pages, persistent idempotent mutation queue.
- **Phase 0 dataset import** — executed against the real Kering data (126 stores, 3,550 devices).
- **Labels, CI, docs, and a 28-item issue backlog** (Epic #30 + children #31–#58).

---

## 2. Verification Gates

All gates run against the working tree on September 13–14, 2026.

| Gate | Command | Result |
|------|---------|--------|
| System check | `python manage.py check` | ✅ no issues (0 silenced) |
| Pending migrations | `python manage.py makemigrations --check --dry-run` | ✅ No changes detected |
| Test suite | `python manage.py test` | ✅ **Ran 79 tests · OK (skipped=1)** |
| Mini program lint | `npm run lint` (eslint 9 flat config) | ✅ exit 0 |
| CI workflow syntax | `js-yaml` on both workflows | ✅ exit 0 |
| Real-data import | `import_kering_master` (live, not dry-run) | ✅ 126 inspections · 3,550 devices |
| Rollback tracking | `utils.import_rollback.ImportRun` | ✅ ImportRun #8 · `can_rollback=True` |
| Issue backlog | `create-miniprogram-issues.ps1` | ✅ 28 created · 0 failed |
| Report parity | golden `22149 Gucci SZOL` workbook | ✅ `ReportGoldenParityTests` passing |

> The single skip is `RealDataIntegrationTests`, which is intentionally env-gated: it imports
> the full real dataset and generates a report, then rolls back. It runs only when the Kering
> source files are present.

---

## 3. Spec Coverage

| Spec section | Status | Evidence |
|--------------|--------|----------|
| Phase 0 — Kering dataset import | ✅ Done | `inspections/management/commands/import_kering_master.py`; executed on real data |
| Backend A — `inspections` app | ✅ Done | `models.py` (436 ln), `constants.py` (265 ln), `admin.py`, `migrations/0001_initial.py` |
| Backend A — engineer role + scoping | ✅ Done | `AdminRole.INSPECTION_ENGINEER`, `User.can_run_inspection`, `get_assigned_inspections` |
| Backend B — SimpleJWT + WeChat auth | ✅ Done | `accounts/wechat.py`, `migrations/0020_wechatidentity.py`, `api/views_auth.py` |
| Backend C — REST API | ✅ Done | `api/inspection_views.py`, `api/inspection_serializers.py`, wired in `api/urls.py` |
| Backend D — photo naming | ✅ Done | `inspections/services/photo_naming.py` |
| Backend D — report generator + template | ✅ Done | `report_generator.py` + bundled `report_template.xlsx` (472,349 bytes) |
| Backend D — transforms | ✅ Done | `transforms.py`: monitor-size table, XStore linking, version merge, tag autofill, status |
| Backend D — parity tests | ✅ Done | `ReportParityTests`, `ReportGoldenParityTests` |
| Backend D — historical photo reuse (P2) | ✅ Done | `historical_photos.py` + `reuse_historical_photos` command |
| Client — scaffold + utils | ✅ Done | `app.*`, `project.config.json`, `config/env.js`, `utils/{request,auth,db,queue,sync}.js` |
| Client — 10 pages | ✅ Done | login, inspections, inspection-detail, device-verify, rack-network, issues, confirmation, signoff, report, sync |
| Client — scan / search / new device | ✅ Done | `wx.scanCode` + search in inspection-detail; `is_new_device` in device-verify |
| Client — signatures / share | ✅ Done | dual signature canvas in signoff; `wx.shareFileMessage` + `onShareAppMessage` in report |
| Infra — env + secrets + fail-fast | ✅ Done | `.env.example`; `settings.py` raises `ImproperlyConfigured` when `DEBUG=False` |
| Infra — server-side upload caps | ✅ Done | `api/inspection_views.py::_reject_oversized_uploads` (per-file 400) + 2 tests |
| Infra — AppID / HTTPS / whitelist / Nginx | ⚠️ Operator | `docs/DEPLOYMENT_MINIPROGRAM.md` + issue #53 (external provisioning) |
| Repo hygiene — labels | ✅ Done | 4 new `component:` labels created live; `LABELS.md` + `setup-github-labels.ps1` |
| Repo hygiene — CI | ✅ Done | `.github/workflows/backend-ci.yml`, `miniprogram-ci.yml` (lint + gated preview job) |
| Documentation | ✅ Done | `MINIPROGRAM_SPEC.md`, ADR-0011, `API_GUIDE.md`, `INDEX.md`, `DEPLOYMENT_MINIPROGRAM.md`, `miniprogram/README.md` |
| Issue backlog | ✅ Live | Epic #30 + children #31–#58 |

---

## 4. REST API Surface (`/api/v1`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/wechat/bind/` | `{code, username, password}` → bind openid, issue JWT |
| POST | `/auth/wechat/login/` | `{code}` → `{bound, access, refresh, user}` |
| POST | `/auth/token/refresh/` | `{refresh}` → `{access}` |
| GET | `/inspections/` | Inspections assigned to the caller |
| GET | `/inspections/{id}/` | Detail incl. `devices` + `issues` (offline payload) |
| PATCH | `/inspections/{id}/` | Store-level fields (times, wifi, rating, counts) |
| GET | `/inspections/checklist/` | Kering checklist + capture fields + confirmation labels |
| GET/POST | `/inspections/{id}/devices/` | List devices / idempotent upsert (multipart w/ photos) |
| POST/PATCH | `/inspections/{id}/devices/{uid}/` | Idempotent upsert addressed by `client_device_uid` |
| POST | `/inspections/{id}/photos/` | Rack / network / issue / cash-drawer photos |
| GET/POST | `/inspections/{id}/issues/` | Issue list (keeps cover-page counts in sync) |
| POST | `/inspections/{id}/signoff/` | Signatures → `submitted` + `AuditLog` entry |
| POST | `/inspections/{id}/report/` | Generate xlsx + Photo zip; returns absolute URLs |

Writes are rejected with `409` once `status == submitted`, and with `403` when the caller is
not permitted for that inspection.

---

## 5. Phase 0 — Real Data Import (executed)

```
companies: 1   divisions: 2   locations: 126   inspections: 126
categories: 5  brands: 30     models: 94       assets: 3,540
devices: 3,550 devices_skipped_duplicate: 14
ImportRun #8 · tracked objects 7,474 · can_rollback True
```

Two real-data defects were found only by running the full dataset and were fixed with
regression tests:

1. **`UNIQUE constraint failed: assets_assetbrand.code`** — real brand names carry case
   variants (`HP` vs `hp`) that collided on the derived code. Fixed with a code-then-name
   lookup that only creates when neither exists (`test_brand_case_variants_share_one_brand`).
2. **`ValueError: NaTType does not support utcoffset`** — pandas returns `NaT` for blank
   warranty dates. Fixed by guarding every `_parse_date` branch with `pd.isna`
   (`test_blank_warranty_nat_imports_as_null`).

A real report was generated from persisted data to prove the end-to-end path:
`22153 Gucci CDDC` → `media/inspections/22153_CDDC/22153_Gucci_CDDC_Report_Per_Store.xlsx`
(441,435 bytes · 8 sheets · 90 asset rows · cover page populated).

---

## 6. Report Fidelity to the Golden Output

Ported exactly from the EUS scripts rather than approximated:

- `build_cover_page_summary_counts` — 17 labels with independent brand-model substring
  matching (e.g. Reception Printer requires `printer` **and** `tm-t88`; iPhone SE counts only
  `iphone se`).
- Monitor-size marker table (`MONITOR_MODEL_SIZES`) and `format_monitor_size_marker`.
- Device-category normalization — iPhone/iPad/iPod → `IOS_Device` (all-caps `IOS`, matching the
  golden output), RFID/LineaPro → `AR_Device`, UPS → `Other`.
- XStore peripheral linking by Asset ID (two-pass), Intact-Asset-Tag autofill, Windows/iOS
  version merging, In Store / Not In Store status, new-device `NEW` comment marker.
- Label-based template population that preserves merged cells (`_safe_set` resolves the
  merged top-left), with text number formats for Asset ID / SN / JDA.

Two parity defects were caught by comparing against a real EUS-generated workbook and fixed:
iOS casing, and iPhone over-counting (golden counts only iPhone SE, not iPhone 16e).

---

## 7. GitHub Issue Backlog (created live)

Epic **[#30](https://github.com/sean7084/HengjiAMS1/issues/30)** with 27 children. Plan item
*N* maps to issue **#(N+30)**.

| Issues | Scope |
|--------|-------|
| #31, #32 | Phase 0 import command; placeholder cleaning, category normalization, rollback |
| #33, #34 | `inspections` models + migrations; engineer role and scoping |
| #35, #36, #37 | SimpleJWT config; `WeChatIdentity` + `jscode2session`; bind/login endpoints |
| #38, #39 | Device/photo/issue/signoff endpoints; checklist + report endpoints |
| #40, #41, #42, #43 | Photo naming; report generator + template; transforms; parity tests |
| #44–#51 | Client scaffold/utils, login, list/detail, device verify, offline cache, queue, sync, P1 pages |
| #52, #54, #55, #56 | Env + fail-fast; docs; labels; CI workflows |
| #57, #58 | Historical photo reuse; WeChat review/release checklist |
| **#53** | **Production HTTPS domain + legal-domain whitelist — the only item still open (external)** |

Implemented items were titled `[Implemented - verify & close]` so the board reflects reality.

Labels created live for this work: `component: api`, `component: inspections`,
`component: miniprogram`, `component: import`.

---

## 8. CI

- `.github/workflows/backend-ci.yml` — path-filtered Django tests on backend changes.
- `.github/workflows/miniprogram-ci.yml` — `lint` job (eslint 9) on `miniprogram/**`, plus a
  gated `preview` job that runs `miniprogram-ci preview` and uploads a QR artifact **only**
  when the `WECHAT_MINI_APPID` and `WECHAT_CI_PRIVATE_KEY` secrets exist; otherwise it is a
  safe no-op.

---

## 9. Remaining Operator Prerequisites (external)

These require the WeChat business account, a production server, and an ICP-filed domain, so
they cannot be completed in-repo. Each is documented step-by-step in
[`docs/DEPLOYMENT_MINIPROGRAM.md`](../DEPLOYMENT_MINIPROGRAM.md) and tracked as issue #53.

- ⚠️ Register the mini program; obtain AppID / AppSecret / code-upload key (runbook §1)
- ⚠️ Production HTTPS on an ICP-filed domain + legal-domain whitelist for `request`,
  `uploadFile`, `downloadFile` (runbook §3)
- ⚠️ Nginx media serving for `MEDIA_ROOT/inspections/` + upload caps (runbook §4)
- ⚠️ Production dataset import + engineer role/assignment provisioning (runbook §5–6)
- ⚠️ On-device E2E in WeChat DevTools, then submit for review and release (runbook §9–10)

Note: the mini program renders only inside WeChat DevTools/runtime with a registered AppID, so
it is not browser-reachable. Verification therefore relies on the Django test suite, live HTTP
API tests, the persisted real-data import, and GitHub API evidence.

---

## 10. Related Documents

- [`docs/MINIPROGRAM_SPEC.md`](../MINIPROGRAM_SPEC.md) — feature spec (personas, screens, sync contract, API map)
- [`docs/DEPLOYMENT_MINIPROGRAM.md`](../DEPLOYMENT_MINIPROGRAM.md) — deployment runbook
- [`docs/ARCHITECTURAL_DECISION_RECORDS.md`](../ARCHITECTURAL_DECISION_RECORDS.md) — ADR-0011
- [`docs/API_GUIDE.md`](../API_GUIDE.md) — JWT + inspection endpoints
- [`miniprogram/README.md`](../../miniprogram/README.md) — client structure + release checklist

---

*Generated September 14, 2026 · HengjiAMS1 · Kering Store Inspection Mini Program*
