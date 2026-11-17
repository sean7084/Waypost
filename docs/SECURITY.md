# Security Policy & Architecture — Waypost

**Last Updated:** September 7, 2026
**Applies to:** Waypost (Django 5.2.8)
**Audience:** Maintainers, contributors, and security reviewers

This document describes the security controls **actually implemented** in the codebase, how to report vulnerabilities, and the known gaps that remain. Where a control is aspirational rather than implemented, it is explicitly marked ⚠️.

---

## 1. Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

- Use the confidential template: [`.github/ISSUE_TEMPLATE/security.md`](../.github/ISSUE_TEMPLATE/security.md) (labels `security`, `confidential`), **or**
- Email: **security@istore-tech.com**

Include: vulnerability type, impact assessment, reproduction steps, and a minimal proof of concept.

**Response process:**
1. Acknowledge receipt within 3 business days.
2. Triage and confirm severity (see the `P0 - Critical` … `P3 - Low` priority labels).
3. Remediate, then coordinate disclosure.
4. **Embargo:** do not publish details until a fix is released.

---

## 2. Security Posture at a Glance

| Control | Status | Where |
|---------|--------|-------|
| Password hashing (PBKDF2) | ✅ Django default | `AUTH_PASSWORD_VALIDATORS`, `settings.py` |
| Password complexity validators | ✅ 4 validators | `settings.py` |
| Two-factor authentication (TOTP) | ⚠️ **opt-in login gate**, not an enforced session; the API path bypasses it | `accounts` (2FA fields); ADR-0014 |
| Role-based access control | ✅ 5 additive roles | `accounts/models.py` |
| Row-level data scoping | ✅ per role | `get_accessible_assets()` and view querysets |
| CSRF protection | ✅ `CsrfViewMiddleware` | `settings.py` |
| Session timeout (24h) | ✅ | `SESSION_COOKIE_AGE = 86400` |
| Credential encryption at rest | ✅ Fernet | `accounts/crypto.py` |
| Env-driven secrets + fail-fast guards | ✅ | `settings.py` |
| Non-enumerable primary keys (UUID) | ⚠️ **users/assets/inspections/reports only** — `companies`, locations and contacts use enumerable integers | models; ADR-0013 |
| Security headers (XSS/nosniff/frame) | ✅ | `settings.py` |
| HTTPS redirect / HSTS / secure cookies | ⚠️ **Not configured** | see §7 |
| API token authentication | ✅ **SimpleJWT for the mini program**, session for the web | `settings.py:378-403`; ADR-0019 |
| Dependency vulnerability scanning | ⚠️ **Not configured** | see §9 |

---

## 3. Authentication

- **User model:** custom `accounts.User` (extends `AbstractUser`) with a **UUID** primary key.
- **Mechanism:** **dual-scheme.** Django **session authentication** for the web UI and browsable
  API, **SimpleJWT bearer tokens** for the WeChat mini program (`wx.request` has no cookie jar,
  so a session cannot survive between calls). Access tokens live 2h, refresh tokens 14d;
  `ROTATE_REFRESH_TOKENS=False`. See §8 and ADR-0019 — the earlier "no JWT/bearer-token
  endpoint" claim here was wrong.
- **Passwords:** hashed with Django's default (PBKDF2-SHA256). Four validators enforce minimum length and reject common/numeric/similar passwords.
- **Forced rotation:** `User.must_change_password` forces a password change at next login.
- **Sessions:** `SESSION_COOKIE_AGE = 86400` (24 hours) and `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`.

### Two-Factor Authentication (2FA)

> ⚠️ **2FA is an opt-in login gate, not an enforced session state.** `django-otp` supplies the
> TOTP and static **device models only**; the gate is hand-rolled in `LoginView.form_valid`.
> Verification calls `django.contrib.auth.login`, never `django_otp.login()`, so no
> OTP-verified state is attached to the session. `OTPMiddleware` is enabled but inert —
> `is_verified()` is called nowhere and `otp_required` is imported and unused. Full record and
> the list of what would make it real: **ADR-0014**.

- `User.two_factor_enabled` tracks enrollment; `User.force_2fa_setup` forces enrollment at next
  login; `User.backup_tokens` stores static recovery codes. **Both flags default `False`**, and
  `force_2fa_setup` appears in no form and no admin fieldset — it is settable only by a raw ORM
  write, so "2FA enforced for privileged accounts" cannot currently be achieved through the UI.
- `disable_2fa` is self-service (`@login_required` + POST) with **no re-authentication**, and
  deletes all devices.
- **The mini-program JWT path does not check 2FA at all**, including for a superadmin.
- Two enrolment paths exist (`TwoFactorSetupView` via django-otp, `setup_2fa_simple` via
  `pyotp` + a session secret); the latter bypasses django-otp's throttling and drift handling.
- Issuer name: `OTP_TOTP_ISSUER = 'Waypost'`.

---

## 4. Authorization (RBAC)

Roles are **additive** — a user may hold several (many-to-many `User.roles` → `AdminRole`). Role codes:

| Code | Label | Typical scope |
|------|-------|---------------|
| `superadmin` | Superadmin | All data and user management |
| `it_administrator` | IT Administrator | Asset/company data scoped to `managed_company` / `managed_divisions` |
| `viewer` | Viewer | Read-only, scoped to `managed_locations` |
| `order_management_specialist` | Order Management Specialist | Day-to-day quotation/delivery/invoice workflows |
| `order_management_manager` | Order Management Manager | Approve & apply pricing changes |

**Scoping fields:** `managed_company`, `managed_divisions`, `managed_locations`.

**Enforcement:**
- Permission helpers on `User`: `is_superadmin()`, `is_it_administrator()`, `is_viewer_admin()`, `is_order_management_specialist()`, `is_order_management_manager()`, `can_manage_orders()`, `can_manage_assets()`, `can_view_assets()`, `can_edit_assets()`, `can_manage_companies()`.
- Row-level filtering: `User.get_accessible_assets()` restricts asset visibility by role; API viewsets apply the same scoping.
- Order-management views are gated by `OrderManagementAccessMixin` (`user.can_manage_orders()`).

> A role matrix from the operator's perspective is in [`WORKFLOW_GUIDE.md`](WORKFLOW_GUIDE.md).

---

## 5. Data Protection

### Credential encryption at rest
Mailbox/SMTP/IMAP passwords are stored encrypted in `UserMailboxSettings.encrypted_password` and `SystemSMTPSettings.encrypted_password`.

- **Cipher:** **Fernet** (AES-128-CBC + HMAC-SHA256, authenticated) via `accounts/crypto.py`.
- **Key:** `DJANGO_FIELD_ENCRYPTION_KEY` (a Fernet key). In development it may be omitted, in which case a key is derived from `SECRET_KEY`.
- **History:** this replaced a reversible XOR obfuscation; data migration `accounts/0019_encrypt_mailbox_passwords_with_fernet` re-encrypted existing values, and `decrypt_secret()` still transparently reads legacy values.

Generate a key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Key rotation:** changing `DJANGO_FIELD_ENCRYPTION_KEY` after data exists requires a re-encryption migration (decrypt with the old key, encrypt with the new). Rotation is not yet automated.

### Other protections
- **UUID primary keys** for users, assets, and companies prevent enumerable/guessable IDs.
- **Outbound-email safety:** `TEST_OUTBOUND_EMAIL_OVERRIDE` redirects all outbound email to a single address, and is now **honoured only when `DEBUG` is true** (`accounts/email_backends.test_outbound_override`, read by both the system backend and the per-user dispatch path). Its default is **empty**, not a personal address: it used to default to one and be honoured in production, so a deploy whose `.env` omitted the key silently redirected every customer email to a single inbox. Covered by `accounts/tests_security_guards.py`.
- **Uploads:** `MAX_UPLOAD_SIZE = 5 MB`; media is served by Nginx in production (see `DEPLOYMENT.md`).

---

## 6. Secrets Management

- Secrets are read from a **repo-local `.env`** file (loaded by `waypost/runtime_setup.py`), which is **not committed** (`.gitignore`). See `.env.example`.
- Key secrets: `DJANGO_SECRET_KEY`, `DJANGO_FIELD_ENCRYPTION_KEY`, `DATABASE_PASSWORD`, `minimax_token_plan_key`.
- **Fail-fast guards** (`settings.py`) refuse to start when `DEBUG=False` and:
  - `DJANGO_SECRET_KEY` is unset (still the insecure dev fallback), or
  - `DJANGO_FIELD_ENCRYPTION_KEY` is unset.
- Never commit secrets. Local secrets live in a gitignored `.env` at the repository root, which holds both the Django runtime settings and the GitHub automation token (`GITHUB_CLASSIC_TOKEN`). The automation scripts fall back to a legacy `.env.local` if `.env` is absent. Note that `load_local_env()` exports every key in `.env` into the process environment, so in production that file must contain only what the app needs — never add the GitHub token to a production `.env`.

---

## 7. Transport & Web Hardening

**Implemented** (`settings.py`):
- `X_FRAME_OPTIONS = 'DENY'` (clickjacking protection)
- `SECURE_CONTENT_TYPE_NOSNIFF = True`
- `SECURE_BROWSER_XSS_FILTER = True`
- CSRF middleware enabled
- TLS is terminated at **Nginx** (see `DEPLOYMENT.md`, including HTTP→HTTPS redirect and Let's Encrypt)

⚠️ **Known gaps — not yet configured** (recommend enabling in production):
- `SECURE_SSL_REDIRECT = True`
- `SECURE_HSTS_SECONDS` (+ `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`)
- `SESSION_COOKIE_SECURE = True` and `CSRF_COOKIE_SECURE = True`
- `CSRF_TRUSTED_ORIGINS` for the production domain(s)

These are omitted from the defaults because they would break local HTTP development; they should be set (env-driven) for production. Tracked as a follow-up hardening item.

---

## 8. API Security

- The REST API (`/api/v1/`, DRF) accepts **SimpleJWT bearer tokens and `SessionAuthentication`**, and requires **`IsAuthenticated`** by default (`settings.py:378-392`).
- The mini program authenticates with `wx.login` → `POST /api/v1/auth/wechat/login/` (or `/bind/` on first launch) and then sends `Authorization: Bearer` on every call, including `wx.uploadFile`. Access 2h, refresh 14d.
- Querysets are **role-scoped** (superadmin sees all; IT admins/viewers see only their scope). Inspections add object-level `CanAccessInspection`.
- Company creation is restricted to superadmins and raises DRF `PermissionDenied` (**403**, not the builtin `PermissionError` → 500); the `users` endpoint is read-only.
- Session-auth writes require a **CSRF token**; JWT requests are CSRF-exempt by DRF design. There is no `csrf_exempt` in `api/`.

⚠️ **Open limitations (ADR-0019 records them):**
- **`WeChatLookupView` is unauthenticated** and returns username/phone matches — a user-enumeration oracle that needs a rate limit or proof of ownership.
- **`ROTATE_REFRESH_TOKENS=False`**: a stolen 14-day refresh token stays valid for its whole life; `JWT_SIGNING_KEY` falls back to `SECRET_KEY`.
- **2FA is not checked on the JWT path**, even for a superadmin (ADR-0014).
- `lookup`, `bind`, `login` and `auth/token/refresh/` are deliberately unauthenticated.

---

## 9. Dependencies & Supply Chain

- All dependencies are **pinned** in `requirements.txt` (reproducible installs).
- `gunicorn` is gated with `sys_platform != 'win32'` for cross-platform installs.

⚠️ **Gaps:** no automated dependency vulnerability scanning. Recommended: enable **Dependabot** alerts and run **`pip-audit`** (or **`safety`**) in CI (see the CI issue in the backlog).

---

## 10. Production Hardening Checklist

Before going live, confirm:
- [ ] `DJANGO_DEBUG=False`
- [ ] Strong `DJANGO_SECRET_KEY` set (app fails fast otherwise)
- [ ] `DJANGO_FIELD_ENCRYPTION_KEY` set to a generated Fernet key (app fails fast otherwise)
- [ ] `DJANGO_ALLOWED_HOSTS` restricted to real domains
- [ ] PostgreSQL configured (`DATABASE_*`); Redis configured (`DJANGO_REDIS_CACHE_URL`)
- [ ] HTTPS enabled at Nginx; consider adding the §7 Django HTTPS settings
- [ ] `TEST_OUTBOUND_EMAIL_OVERRIDE` is **empty** (belt-and-braces: it is ignored unless `DEBUG=True`)
- [ ] `DJANGO_DEBUG=False` — this is what removes `accounts/dev-login/` from the URLconf entirely
- [ ] `template_files/` provisioned (document generation depends on it)
- [ ] 2FA enforced for privileged accounts (`force_2fa_setup`)
- [ ] Backups configured **and a restore tested** (see backlog `BACKUP_RESTORE.md`)
- [ ] Dependency scanning enabled (Dependabot / pip-audit)

---

## Related Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — production deployment & TLS
- [`DATABASE_MIGRATION.md`](DATABASE_MIGRATION.md) — SQLite → PostgreSQL
- [`WORKFLOW_GUIDE.md`](WORKFLOW_GUIDE.md) — role matrix from an operator view
- [`GITHUB_SETTINGS.md`](GITHUB_SETTINGS.md) — branch protection & CODEOWNERS
- `accounts/crypto.py` — credential encryption implementation

---

*Maintainer: Sean Liu (@sean7084)*
