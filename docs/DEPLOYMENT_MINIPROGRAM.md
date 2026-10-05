# Deployment Runbook — WeChat Mini Program (Kering Store Inspection)

Companion to [`DEPLOYMENT.md`](DEPLOYMENT.md), which covers the base Django
server (Ubuntu + PostgreSQL + Nginx + Gunicorn + SSL). This runbook covers the
**additional** pieces required to ship the Kering store-inspection mini program:
WeChat console registration, mini-program server env, the legal-domain
whitelist, media serving for reports/photos, the Phase 0 dataset import, CI
preview builds, and the WeChat review/release steps.

> Feature spec: [`MINIPROGRAM_SPEC.md`](MINIPROGRAM_SPEC.md) · decisions:
> ADR-0011 in [`ARCHITECTURAL_DECISION_RECORDS.md`](ARCHITECTURAL_DECISION_RECORDS.md) ·
> endpoints: [`API_GUIDE.md`](API_GUIDE.md) · client: `miniprogram/README.md`.

---

## 0. Prerequisites

- The base app is deployed and healthy per [`DEPLOYMENT.md`](DEPLOYMENT.md)
  (`DJANGO_DEBUG=False`, PostgreSQL, Nginx, Gunicorn, valid SSL).
- A **registered WeChat mini program** (企业主体 recommended) — you will need its
  AppID, AppSecret, and the code-upload key.
- An **ICP-filed HTTPS domain** for the API. WeChat rejects non-HTTPS hosts and
  hosts without ICP filing for mainland-China legal domains.

---

## 1. Register the mini program (WeChat MP console)

At <https://mp.weixin.qq.com>:

1. **开发管理 → 开发设置** — copy the **AppID** and **AppSecret**. The AppSecret is
   server-side only; never ship it in the client bundle.
2. **开发管理 → 开发设置 → 小程序代码上传密钥** — generate and download the
   **code-upload private key** (`private.<appid>.key`). This is used by
   `miniprogram-ci` in CI (Step 8). Store it as a repository secret; do not commit
   it (`.gitignore` already excludes `miniprogram/ci_private.key`).
3. **开发 → 开发管理 → 服务器域名** — add the production API host to the legal
   domains (Step 3).
4. **设置 → 基本设置** — configure the service category (类目) and privacy policy;
   these are required before review.

---

## 2. Mini-program server environment

Add these to the production `.env` (the same file described in
[`DEPLOYMENT.md` Step 4](DEPLOYMENT.md)). `settings.py` reads them directly:

```bash
# WeChat mini program (Kering store-inspection client)
WECHAT_MINI_APPID=wxXXXXXXXXXXXXXXXX
WECHAT_MINI_APPSECRET=________________________________   # server-side ONLY
# Signing key for mini-program JWTs. Falls back to DJANGO_SECRET_KEY when unset,
# but set an explicit, stable value so tokens survive a SECRET_KEY rotation.
JWT_SIGNING_KEY=________________________________
# Optional token lifetimes (defaults shown):
# JWT_ACCESS_HOURS=2
# JWT_REFRESH_DAYS=14
```

**Fail-fast guard.** When `DJANGO_DEBUG=False`, `settings.py` raises
`ImproperlyConfigured` at startup if `WECHAT_MINI_APPID` or
`WECHAT_MINI_APPSECRET` is missing (alongside the existing `SECRET_KEY` /
`DJANGO_FIELD_ENCRYPTION_KEY` guards). Verify the guard is active:

```bash
python manage.py check            # must print "no issues"
python manage.py shell -c "from django.conf import settings; print(bool(settings.WECHAT_MINI_APPID), bool(settings.WECHAT_MINI_APPSECRET))"
# -> True True
```

JWT config lives in `SIMPLE_JWT` (Bearer header, `UPDATE_LAST_LOGIN=True`).
The bind screen first resolves the engineer by Chinese name via
`POST /api/v1/auth/wechat/lookup/` (`{chinese_name}` → matching accounts, for
on-screen confirmation), then tokens are issued by
`POST /api/v1/auth/wechat/bind/` and `POST /api/v1/auth/wechat/login/`; refresh via
`POST /api/v1/auth/token/refresh/`.
The WeChat `session_key` returned by `jscode2session` is **never persisted**.

---

## 3. HTTPS + legal-domain whitelist

In **开发管理 → 开发设置 → 服务器域名**, add your production host (e.g.
`https://api.your-domain.com`) to **all three** whitelists the client uses:

| Whitelist | Used by |
|-----------|---------|
| `request` 合法域名 | every `wx.request` API call (`utils/request.js`) |
| `uploadFile` 合法域名 | photo/signature uploads (`wx.uploadFile`) |
| `downloadFile` 合法域名 | report `.xlsx` + `Photo` zip download (`pages/report`) |

Rules: HTTPS only, no port number, domain must be ICP-filed. After editing,
rebuild/re-upload the client (the whitelist is enforced at runtime against the
released version).

Point the client at production by editing `miniprogram/config/env.js`:

```js
const ENV = 'prod';                 // was 'dev'
// CONFIGS.prod.API_BASE:
API_BASE: 'https://api.your-domain.com/api/v1',
```

For local development keep `ENV = 'dev'` and enable **不校验合法域名** in WeChat
DevTools (the checked-in default).

---

## 4. Nginx — media serving + upload caps

Reports and photos are written under `MEDIA_ROOT/inspections/<jda>_<store>/`
(`MEDIA_URL = /media/`) and must be downloadable over HTTPS. The base
`/media/` location in [`DEPLOYMENT.md` Step 8](DEPLOYMENT.md) already serves
them. Uploads are capped at **two layers**: the inspection API rejects any single
photo/signature file larger than `MAX_UPLOAD_SIZE` (5 MB, `settings.py`) with
HTTP 400 (`api/inspection_views.py::_reject_oversized_uploads`), and Nginx
`client_max_body_size` bounds the total multipart body. Add the Nginx cap on the
**proxied API** location:

```nginx
    # Photo/report downloads (served directly by Nginx)
    location /media/inspections/ {
        alias /opt/waypost/media/inspections/;
        expires 7d;
        add_header Content-Disposition 'attachment';   # force download for xlsx/zip
    }

    # API proxy — cap multipart upload bodies (photos/signatures)
    location / {
        client_max_body_size 20m;      # >= a few compressed photos per request
        proxy_pass http://127.0.0.1:8000;
        # ... standard proxy_set_header lines from DEPLOYMENT.md ...
    }
```

> The `Content-Disposition: attachment` header makes the report `.xlsx` and
> `Photo` `.zip` download rather than render when the client opens the absolute
> URL returned by `POST /inspections/{id}/report/`.

Reload: `sudo nginx -t && sudo systemctl reload nginx`.

---

## 5. Phase 0 — import the Kering dataset (production)

The mini program operates on `StoreInspection` + expected `InspectionDevice`
rows created by the importer. Run it **after** `migrate`:

```bash
source .venv/bin/activate
# Preview first (no DB writes):
python manage.py import_kering_master \
    --schedule /path/to/schedule.xlsx \
    --assets   /path/to/asset_list_CN_2026.xlsx \
    --dry-run

# Then import for real (rollback-tracked via utils.import_rollback.ImportRun):
python manage.py import_kering_master \
    --schedule /path/to/schedule.xlsx \
    --assets   /path/to/asset_list_CN_2026.xlsx
```

The command maps Kering → `Company(Kering)` / `Division(brand)` /
`Location(store, code=JDA)` / `Asset`, applies the EUS device-category
normalization, cleans placeholder SN/Asset-ID values, dedupes by SN within a
store, prints a per-store summary, and records every created object in an
`ImportRun` (rollback-able). Optional: pre-fill reusable photos for planned
inspections:

```bash
python manage.py reuse_historical_photos --source /path/to/Photo
```

---

## 6. Provision engineers + assignments

1. Give each onsite engineer the `inspection_engineer` role
   (`accounts.AdminRole.INSPECTION_ENGINEER`) — via Django admin or shell. This
   grants `User.can_run_inspection`.
2. Set each engineer's **`chinese_name`** (Django admin → Users → *Chinese Name*).
   The mini-program bind screen looks engineers up by this value, so an engineer
   with no `chinese_name` **cannot log in**. Optionally set **`service_cities`**
   (multi-select — the cities they cover); these show on the engineer's task list
   so they can flag corrections. Seed the city list with
   `python manage.py seed_service_cities` and maintain it under Django admin →
   *Service Cities*.
3. Assign inspections: set `StoreInspection.engineer` (importer can set this from
   `schedule.xlsx`, or assign in admin). Engineers only see inspections assigned
   to them (`User.get_assigned_inspections`); IT-admin/superadmin see all.
4. Each engineer needs a staff `User` (username/password) for the **first-launch
   bind**; afterwards `wx.login` is seamless.

---

## 7. Client build

1. Set the real AppID in `miniprogram/project.config.json`
   (`"appid": "wx..."`, replacing `REPLACE_WITH_YOUR_MINIPROGRAM_APPID`).
2. Confirm `config/env.js` is `ENV = 'prod'` with the production `API_BASE`
   (Step 3).
3. Open the `miniprogram/` folder in WeChat DevTools, build, and smoke-test
   against production with a real device (Step 9).

---

## 8. CI preview/upload (`miniprogram-ci`)

`.github/workflows/miniprogram-ci.yml` lints on every `miniprogram/**` change and
runs an **optional** `preview` job on push to `main`. It is a safe no-op until
you add these **repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Value |
|--------|-------|
| `WECHAT_MINI_APPID` | the mini-program AppID |
| `WECHAT_CI_PRIVATE_KEY` | the **contents** of `private.<appid>.key` (Step 1.2) |

When both are present, the job runs `miniprogram-ci preview`, generates a QR
code, and uploads it as the `miniprogram-preview-qr` artifact — scan it in WeChat
to try the build. To push a reviewable version instead, switch the CLI verb from
`preview` to `upload` in that job (or run `miniprogram-ci upload` locally).

### 8a. Local preview/upload (`miniprogram/scripts/ci.js`)

You can run the same flow from a dev machine without waiting on CI. Two npm
scripts wrap `miniprogram-ci` and read credentials from the repo-root `.env`:

```bash
cd miniprogram
npm install                 # one-time: installs miniprogram-ci + eslint
npm run preview             # -> writes miniprogram/preview-qr.jpg (git-ignored); scan in WeChat
npm run upload              # -> pushes a dev version to 版本管理 → 开发版本
# Optional overrides (defaults: version = package.json, desc = "<cmd> by <you> on <date>"):
node scripts/ci.js upload --version 1.2.0 --desc "signoff fixes"
```

The runner reads `WECHAT_MINI_APPID` and `WECHAT_CI_PRIVATE_KEY` from `.env`
(real environment variables take precedence). `WECHAT_CI_PRIVATE_KEY` may be
**either** a path to the key file (the local convention, e.g.
`private.wx6aafa4e05e26c068.key` at the repo root) **or** the raw key contents
(what the GitHub Actions secret stores) — the script detects which and, for
inline contents, writes a temporary `0600` key file. The key file is git-ignored
(`private.*.key`); never commit it.

> **IP whitelist.** If `preview`/`upload` fails with an IP/whitelist error, the MP
> console has *小程序代码上传 → IP白名单* enabled. Either add this machine's public
> IP there, or turn the whitelist off. `settings.py` does **not** read
> `WECHAT_CI_PRIVATE_KEY` — it is a build/CI concern only.

---

## 9. Smoke verification (production)

Before submitting for review, verify the live path end-to-end:

```bash
# 1. Unauthenticated access is rejected (expect 401):
curl -i https://api.your-domain.com/api/v1/inspections/

# 2. Bind/login returns JWT (run from a device or with a real wx.login code):
#    POST /api/v1/auth/wechat/bind/ {code, username, password} -> {access, refresh, user}

# 3. With a Bearer token, list assigned inspections and upsert a device reading:
curl -H "Authorization: Bearer $ACCESS" https://api.your-domain.com/api/v1/inspections/
#    POST /api/v1/inspections/{id}/devices/ {client_device_uid, ...} is idempotent.

# 4. Generate + download a report for one store:
curl -X POST -H "Authorization: Bearer $ACCESS" \
     https://api.your-domain.com/api/v1/inspections/{id}/report/
#    -> {report_file, photo_zip} absolute URLs; both must download over HTTPS.
```

On-device: bind → see assigned store → scan a device barcode → capture readings +
photos offline → reconnect → sync (queue drains, no duplicates) → signoff →
generate → download/share the report.

---

## 10. WeChat review & release

Follow the checklist in `miniprogram/README.md` ("Release checklist"), then in the
MP console: **版本管理 → 开发版本 → 提交审核**. Provide the privacy policy, service
category, and a test account if the reviewer needs to log in. After approval,
**发布** the release version. Track the client version in the console; note
backend-affecting changes in `CHANGELOG.md`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ImproperlyConfigured: WECHAT_MINI_APPID ...` on startup | Missing WeChat env with `DEBUG=False` | Set `WECHAT_MINI_APPID` + `WECHAT_MINI_APPSECRET` (Step 2) |
| Client `request:fail url not in domain list` | Host not whitelisted | Add host to `request`/`uploadFile`/`downloadFile` (Step 3) |
| `jscode2session` 40029/40163 | Wrong AppSecret or reused `code` | Confirm AppSecret; each `wx.login` code is single-use |
| Report/photo URL 404 in production | `/media/` not served | Check the Nginx `location /media/` alias (Step 4) |
| Upload 413 | Body over Nginx cap | Raise `client_max_body_size` (Step 4) |
| Engineer sees no inspections | Not assigned / wrong role | Set `StoreInspection.engineer` + `inspection_engineer` role (Step 6) |
| Bind screen shows 未找到该中文名 | `chinese_name` not set on the account | Set `User.chinese_name` in admin (Step 6) |
| CI `preview` job skips | Secrets not set | Add `WECHAT_MINI_APPID` + `WECHAT_CI_PRIVATE_KEY` (Step 8) |

---

*Last Updated: September 13, 2026*
