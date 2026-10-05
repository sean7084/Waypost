# WeChat Mini Program Spec — Kering Store Device Inspection

Status: v1 (Kering-specific) · Backend: Waypost (Django) · Client: native WeChat mini program (monorepo `miniprogram/`)

## 1. Purpose

Digitize the Kering EUS store health-check. The mini program replaces the Feishu
questionnaire used onsite: engineers download their assigned store, capture
per-device readings and photos (offline-first), record rack/network/speedtest
photos, issues, and confirmed device counts, collect signatures, and sync to
Waypost. The server regenerates the client deliverable:

- `<JDA> <Brand> <Store> Report Per Store.xlsx` with sheets `inspection_items`,
  `confirmation_page`, `cover_page`, `asset_list`
- a `Photo/` archive following the established naming convention

## 2. Personas

- Onsite / field engineer (`inspection_engineer` role): runs inspections assigned to
  them. Identified in the mini program by `chinese_name`; may cover one or more
  `service_cities` (e.g. Beijing, Tianjin), shown on the task list for verification.
- IT administrator / superadmin: full visibility, can generate reports.

## 3. Architecture

```
WeChat mini program (offline-first)
  → DRF API /api/v1 (JWT bearer)
    → inspections app (StoreInspection, InspectionDevice, InspectionPhoto, InspectionIssue)
      → services: transforms + photo_naming + report_generator (openpyxl)
        → MEDIA_ROOT/inspections/... (xlsx + Photo zip)
```

Prerequisite: the Kering master dataset is imported first
(`python manage.py import_kering_master --schedule ... --assets ...`), which
creates Company(Kering) → Division(brand) → Location(store, code=JDA), the
`Asset` records, and each `StoreInspection` with its expected `InspectionDevice`
rows.

## 4. Data model (inspections app)

- `StoreInspection`: company/division(brand)/location(store), `jda_code`,
  `inspection_date`, `engineer`, `status` (planned/in_progress/completed/submitted),
  cover-page fields (arriving/leaving time, wifi_coverage, error/issue/follow counts,
  `cover_extras` JSON), confirmation fields (`wifi_covers_store`, `it_support_rating`,
  `it_support_comment`, `device_counts` JSON), signatures, generated `report_file` +
  `photo_zip`.
- `InspectionDevice`: links to `Asset` (nullable for onsite-new devices),
  `client_device_uid` (idempotency), expected identity (category/brand_model/sn/
  asset_id_text/usage/warranty_start/photo_required/outline/device_notes) and
  collected readings (status, ip_address, cpu, memory, hdd, windows_version,
  ios_version, drive_c_free_space, intact_asset_tag, comment, is_new_device).
  Unique `(store_inspection, client_device_uid)`.
- `InspectionPhoto`: kind (overall/serial/rack1..3/router/switch/patchpanel/
  speedtest_ethernet/speedtest_wifi/issue/cash_drawer), image, `client_photo_uid`
  (idempotency), computed `display_name`. Unique `(store_inspection, client_photo_uid)`.
- `InspectionIssue`: seq, description, status (fixed/to_be_followed), optional photo.

Kering-specific attributes (Usage, photo requirement, 大纲/注释) live on
`InspectionDevice`, keeping the generic `assets.Asset` model clean.

Engineer identity (accounts app): `User.chinese_name` (indexed; the mini-program
login lookup key) and `User.service_cities` (M2M to `ServiceCity` — `name_en` /
`name_zh`, admin-managed, seeded via `python manage.py seed_service_cities`).
`UserSerializer` exposes `chinese_name`, `english_name`, `service_cities`,
`service_city_names`.

## 5. Authentication

The bind screen identifies the engineer by **Chinese name** (not username):

- Name lookup: `POST /api/v1/auth/wechat/lookup/` `{chinese_name}` →
  `{found, matches:[{username, english_name, chinese_name}]}` (unauthenticated).
  The client shows the resolved English name for confirmation; multiple same-name
  matches are offered as a picker. Requires `User.chinese_name` to be populated
  (Django admin or the user forms), else the engineer cannot log in.
- First launch (bind): `POST /api/v1/auth/wechat/bind/` `{code, username, password}`
  authenticates the staff `User` resolved above, calls WeChat `jscode2session`,
  links the openid (`accounts.WechatIdentity`), and returns SimpleJWT
  `{access, refresh, user}`.
- Later launches: `POST /api/v1/auth/wechat/login/` `{code}` → `{bound, access, refresh, user}`.
  `bound:false` routes the client to the bind screen.
- Refresh: `POST /api/v1/auth/token/refresh/` `{refresh}` → `{access}`.
- All other endpoints require `Authorization: Bearer <access>`.
- The WeChat AppSecret is server-side only (`WECHAT_MINI_APPSECRET`); `session_key`
  is never persisted.

## 6. REST API map (`/api/v1`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/wechat/lookup/` | Resolve a Chinese name → English name for bind confirmation |
| GET | `/inspections/` | Inspections assigned to the caller |
| GET | `/inspections/{id}/` | Detail incl. `devices` + `issues` (offline payload) |
| PATCH | `/inspections/{id}/` | Store-level fields (times, wifi, rating, counts) |
| GET | `/inspections/checklist/` | Kering checklist + per-category capture fields + confirmation labels |
| GET/POST | `/inspections/{id}/devices/` | List devices / idempotent upsert (multipart w/ photos) |
| POST | `/inspections/{id}/photos/` | Rack/network/issue photos |
| GET/POST | `/inspections/{id}/issues/` | Issue list |
| POST | `/inspections/{id}/signoff/` | Signatures → submitted |
| POST | `/inspections/{id}/report/` | Generate xlsx + Photo zip; returns absolute URLs |

Authorization: engineers act only on inspections assigned to them
(`User.can_run_inspection`); writes are rejected once `status == submitted`.

Localization: the client renders device `category` and `usage` values in Simplified
Chinese via `miniprogram/utils/i18n.wxs` (`categoryZh` / `usageZh`, called from
WXML); combined usage codes (e.g. `SK/AD/KPI`) translate part-by-part. Canonical
English values are unchanged on the server, so the report contract still holds.

## 7. Offline-first + idempotency contract

- The client caches the checklist and each inspection (`utils/db.js`).
- Every mutation is enqueued (`utils/queue.js`) with a stable `dedupeKey`; device
  writes carry `client_device_uid`, photos carry `client_photo_uid`. The server
  upserts on these, so re-syncing never duplicates rows.
- The sync engine (`utils/sync.js`) flushes FIFO; on a network error it stops and
  leaves the remainder pending. A device's first photo travels with the multipart
  device upsert; extra photos follow on `/photos/`.

## 8. Report + photo output contract

Ported from the legacy EUS scripts into `inspections/services/`:

- `photo_naming.py`: device photos `<Category><SN>-1` (overall) / `-2` (serial);
  rack/network `Rack1-1`, `Router-1`, `Switch-1`, `PatchPanel-1`,
  `SpeedtestEthernet-1`, `SpeedtestWiFi-1`, `Issue-1`; cash drawer
  `Other_Cash_Drawer N`.
- `transforms.py`: device-category normalization, cover-page summary counts,
  XStore peripheral linking by Asset ID, monitor-size comment markers,
  Intact-Asset-Tag autofill (N when blank/Asset ID missing), Windows/iOS version
  merge, In Store / Not In Store status.
- `report_generator.py`: builds the 4-sheet workbook (openpyxl) + Photo zip and
  attaches them to the `StoreInspection`.

Note: cover-page count mapping and the monitor-size table are Kering-specific
heuristics centralized in `transforms.py`; they should be validated against the
golden `22149 Gucci SZOL` output (report parity test, plan issue 13).

## 9. Mini program pages

`login` (bind) · `inspections` (list) · `inspection-detail` (progress, scan,
offline download, workflow nav) · `device-verify` (category-driven fields +
photos) · `rack-network` · `issues` · `confirmation` (counts/wifi/rating) ·
`signoff` (signature canvas) · `report` (generate/download) · `sync` (queue).

## 10. Deployment prerequisites

- Registered mini program AppID/AppSecret; `WECHAT_MINI_APPID`,
  `WECHAT_MINI_APPSECRET`, `JWT_SIGNING_KEY` set in production (settings fail fast
  when DEBUG is off).
- Production API over HTTPS on an ICP-filed domain, added to the mini program
  legal-domain whitelist (request/uploadFile/downloadFile).
- Media (photos/reports) served by Nginx from `MEDIA_ROOT`.

## 11. Related

- ADR-0011 (this feature's decisions) in `docs/ARCHITECTURAL_DECISION_RECORDS.md`
- `docs/API_GUIDE.md` (JWT + inspection endpoints)
- `miniprogram/README.md` (client structure + backend contract)
