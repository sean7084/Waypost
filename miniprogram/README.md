# Kering Store Inspection — WeChat Mini Program

Field client for the Kering EUS store health-check. It replaces the Feishu
questionnaire: engineers download their assigned store, capture per-device
readings and photos onsite (offline-first), then sync to the Waypost backend,
which regenerates the per-store `Report Per Store.xlsx` + `Photo/` archive.

## Status

Full field workflow implemented (P0 + P1): bind/login, assigned-inspection list,
inspection detail (progress, barcode scan, search, offline download, cover /
confirmation summary), device verification (category-driven fields + photos, incl.
onsite-new devices) with an idempotent offline queue, rack/network + speedtest
photos, issue list, confirmation counts, store/engineer signature signoff,
on-device report generation / download / share, and a sync-status page. Backend:
the `inspections` app + JWT API + report generator (tracked under Epic #30).
Deployment: [`docs/DEPLOYMENT_MINIPROGRAM.md`](../docs/DEPLOYMENT_MINIPROGRAM.md).

## Structure

```
miniprogram/
├── app.js / app.json / app.wxss      # entry, routes, global styles
├── project.config.json               # WeChat DevTools project (set your appid)
├── sitemap.json
├── config/env.js                     # API_BASE per environment (dev/prod)
├── utils/
│   ├── request.js                    # JWT-aware wx.request + upload, 401 refresh
│   ├── auth.js                       # wx.login seamless login + first-launch bind
│   ├── db.js                         # offline cache (inspections, checklist)
│   ├── queue.js                      # persistent mutation queue (idempotent)
│   └── sync.js                       # flush engine (retry/backoff, offline-safe)
└── pages/
    ├── login/                        # bind staff account -> JWT
    ├── inspections/                  # assigned store inspections
    ├── inspection-detail/            # progress, scan, search, offline download, devices
    ├── device-verify/                # readings + photos -> queue (incl. new device)
    ├── rack-network/                 # rack/router/switch/patch-panel + speedtest photos
    ├── issues/                       # issue list (fixed / to-be-followed)
    ├── confirmation/                 # device counts, WiFi, IT-support rating
    ├── signoff/                      # store + engineer signature canvas
    ├── report/                       # generate / download / share report
    └── sync/                         # queue status + manual/auto flush
```

## Backend contract

Base URL: `<API_BASE>` (see `config/env.js`), all under `/api/v1/`.

- `POST /auth/wechat/login/` `{code}` → `{bound, access, refresh, user}`
- `POST /auth/wechat/bind/` `{code, username, password}` → `{access, refresh, user}`
- `POST /auth/token/refresh/` `{refresh}` → `{access}`
- `GET /inspections/` → assigned inspections
- `GET /inspections/{id}/` → detail incl. `devices` + `issues`
- `GET /inspections/checklist/` → Kering checklist + per-category capture fields
- `POST /inspections/{id}/devices/` → idempotent device upsert (multipart w/ photos)
- `POST /inspections/{id}/photos/` → rack/network/issue photos
- `GET|POST /inspections/{id}/issues/`
- `PATCH /inspections/{id}/` → store-level fields
- `POST /inspections/{id}/signoff/` → signatures → submitted
- `POST /inspections/{id}/report/` → generate report + photo zip

## Offline-first + idempotency

- The client caches an inspection (`utils/db.js`) and enqueues every change with a
  stable `dedupeKey` (`utils/queue.js`).
- Device writes carry a `client_device_uid`; photos carry a `client_photo_uid`.
  The server upserts on these, so re-syncing never duplicates rows
  (`uniq_inspection_device_uid` / `uniq_inspection_photo_uid`).
- On a network error the sync stops and leaves the remainder pending; it retries on
  the next connectivity change or manual sync.

## Setup

1. Set your mini-program `appid` in `project.config.json`.
2. Point `config/env.js` `API_BASE` at the Django API (HTTPS in production).
3. In the WeChat MP console, add the API host to the legal domains
   (`request` / `uploadFile` / `downloadFile`). For local dev, enable
   "不校验合法域名" in WeChat DevTools.
4. Open this folder in WeChat DevTools and build.

## Preview / upload (miniprogram-ci)

Push a build to WeChat without opening DevTools, using the code-upload key from
the MP console (开发管理 → 开发设置 → 小程序代码上传密钥). Put the AppID and the
key in the repo-root `.env` (`WECHAT_MINI_APPID`, `WECHAT_CI_PRIVATE_KEY` = path
to `private.<appid>.key`, git-ignored), then:

```bash
npm install        # one-time
npm run preview    # -> preview-qr.jpg (scan in WeChat to try the build)
npm run upload     # -> dev version in 版本管理 → 开发版本 (submit for review)
```

`scripts/ci.js` accepts `WECHAT_CI_PRIVATE_KEY` as a key **path** (local) or the
raw key **contents** (the GitHub Actions secret form). Overrides:
`node scripts/ci.js upload --version 1.2.0 --desc "notes"`. Full runbook:
[`docs/DEPLOYMENT_MINIPROGRAM.md`](../docs/DEPLOYMENT_MINIPROGRAM.md) Step 8/8a.

## Release checklist

Before submitting for WeChat review:

- [ ] `project.config.json` `appid` set to the real mini-program AppID.
- [ ] `config/env.js` `ENV = 'prod'` and `API_BASE` points at the production HTTPS API.
- [ ] Production API host added to the MP console legal domains: `request`,
      `uploadFile`, `downloadFile` (report/photo download uses `downloadFile`).
- [ ] Server env set: `WECHAT_MINI_APPID`, `WECHAT_MINI_APPSECRET`, `JWT_SIGNING_KEY`
      (settings fail fast when `DJANGO_DEBUG=False` if these are missing).
- [ ] Kering dataset imported (`import_kering_master`) and, optionally,
      `reuse_historical_photos` run for planned inspections.
- [ ] At least one engineer account has the `inspection_engineer` role and is
      assigned to a `StoreInspection`; bind flow verified on a real device.
- [ ] Version bumped in the MP console; privacy policy + service category configured.
- [ ] Report generation verified end-to-end for one store (xlsx + Photo zip download).

## Versioning

Track the client version in the MP console (build/upload sets it). Backend API
changes that affect the client should be noted in `CHANGELOG.md` and, when
breaking, gated behind a new `/api/vN/` path rather than mutating `/api/v1/`.
