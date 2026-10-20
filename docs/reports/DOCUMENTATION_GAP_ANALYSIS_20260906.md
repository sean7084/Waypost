# Documentation Gap Analysis — HengjiAMS1

**Date:** September 6, 2026
**Repository:** sean7084/HengjiAMS1
**Method:** Verified against the actual codebase (not assumptions)
**Companion:** `docs/reports/REPO_DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md` (accuracy audit of existing docs)

> This analysis identifies documentation **still missing** after the alignment audit remediated the existing docs. It produces a prioritized action plan with effort estimates and GitHub-issue mappings.

---

## 1. Current State Inventory

**14 Markdown documents exist:**

| Doc | Lines | Status |
|-----|-------|--------|
| README.md | 538 | Good onboarding |
| CHANGELOG.md | 1271 | Detailed version history |
| docs/API_GUIDE.md | 316 | ✅ Code-accurate (rewritten) |
| docs/ARCHITECTURAL_DECISION_RECORDS.md | 521 | 10 domain ADRs |
| docs/CONTRIBUTING.md | 462 | Has Coding Standards + Testing Requirements sections |
| docs/DATABASE_MIGRATION.md | 223 | ✅ New, thorough |
| docs/DEPLOYMENT.md | 494 | ✅ Corrected; manual Ubuntu path solid |
| docs/GITHUB_SETTINGS.md | 320 | ✅ New |
| docs/INDEX.md | 223 | Navigation (gap-analysis section stale) |
| docs/SETUP_SUMMARY.md | 280 | ✅ Counts corrected |
| docs/WORKFLOW_GUIDE.md | 380 | ✅ Corrected |
| reports/ (3 files) | — | Audit + GitHub config reports |

---

## 2. Health Snapshot by Dimension

| Dimension | Verified reality | Grade |
|-----------|------------------|-------|
| REST API docs | Rewritten, code-accurate | 🟢 Good |
| Deployment guide | Corrected; manual Ubuntu path solid | 🟡 Ops gaps |
| DB migration guide | New, thorough | 🟢 Good |
| **Testing** | 28 tests across 4 of 14 apps; no pytest/coverage/CI | 🔴 Critical |
| **Security docs** | No SECURITY.md; SMTP creds use XOR "encryption" | 🔴 Critical |
| **DB schema** | None (15 apps, no ER/schema doc) | 🔴 Critical |
| **Non-DRF endpoints** | Real app surface (~7,000 lines of views) only narratively covered | 🟠 High |
| **CI/CD** | No `.github/workflows/` at all | 🔴 Critical |
| **Ops runbooks** | Backup script exists; no restore, no deploy-rollback, monitoring aspirational | 🟠 High |
| **ADRs** | 10 domain ADRs; ~8 architectural decisions undocumented | 🟠 High |
| **Release process** | Rich CHANGELOG but no RELEASE_PROCEDURE.md | 🟡 Medium |

---

## 3. Standout Findings

### 🔴 Security — reversible credential "encryption"
`accounts/models.py` (`UserMailboxSettings` and the system SMTP settings model) stores mailbox credentials in a field named `encrypted_password`, populated via `_xor_secret()` / restored via `_xor_secret_restore()`. **XOR obfuscation is not encryption** — it is trivially reversible and has no key management. This is undocumented and unreviewed. It is a genuine security weakness, not merely a documentation gap.

### 🔴 Testing — core app untested
Test-method counts per app (verified):

| App | Tests | | App | Tests |
|-----|-------|-|-----|-------|
| accounts | 12 | | deliveries | **no tests.py** |
| quotations | 8 | | customers | **no tests.py** |
| products | 7 | | mobile | **no tests.py** |
| invoices | 1 | | purchases | **no tests.py** |
| assets | **0** | | companies | 0 |
| audit | 0 | | dashboard | 0 |
| reports | 0 | | users | 0 |

`assets/views.py` is **2,230 lines with zero tests**; `deliveries` (core fulfillment workflow) has no test file. No `pytest`, `coverage`, `factory`, `black`, `ruff`, or `flake8` in `requirements.txt`. No CI to enforce any of it.

---

## 4. Prioritized Action Plan

Effort = realistic solo-developer hours. All items map to **new** GitHub issues (audit issues #4–#11 are closed).

### 🔴 P0 — Before/at production launch

| ID | Item | Why | Effort | Labels |
|----|------|-----|--------|--------|
| **A1** | `docs/SECURITY.md` — vuln disclosure policy, threat model, RBAC map, 2FA flow, secrets management, credential-encryption standard | Launching prod with no security doc + reversible secrets | 5–7h | security, P0 - Critical, documentation |
| **A2** | `docs/BACKUP_RESTORE.md` — tested **restore** procedure (DB + `media/`), schedule, verification | Backup script exists; restore untested/undocumented = data-loss risk | 3–4h | deployment, P0 - Critical |
| **A3** | `docs/RELEASE_PROCEDURE.md` — versioning policy, changelog/tag process, deploy + rollback-a-bad-release runbook | About to deploy; no rollback path documented | 3–4h | deployment, P0 - Critical |
| **A4** | **Code fix:** replace XOR with Fernet/symmetric encryption + key from env | A1 must document a *safe* design | 4–6h | security, P0 - Critical, breaking change |

### 🟠 P1 — Near-term (2–4 weeks)

| ID | Item | Why | Effort | Labels |
|----|------|-----|--------|--------|
| **B1** | `docs/TESTING.md` — strategy, pytest+coverage setup, fixtures/factories, per-app coverage targets | No test culture/tooling; core app untested | 5–6h (doc) | needs testing, P1 - High, documentation |
| **B2** | CI pipeline (`.github/workflows/ci.yml`) + doc; wire into branch-protection `required_status_checks` | No automated quality gate | 4–6h | deployment, P1 - High, needs testing |
| **B3** | `docs/DATABASE_SCHEMA.md` — ER diagrams + table/field reference (15 apps) | No schema doc; needed for maintenance + PG migration | 6–8h | documentation, P1 - High |
| **B4** | `docs/WEB_ROUTES.md` — per-app route/feature maps for the HTML (non-DRF) surface | Actual app (~7,000 lines of views) undocumented beyond narrative | 8–12h (phase per app) | documentation, P1 - High |
| **B5** | New ADRs 0011–0018 — UUID PKs, 2FA, custom email backend, i18n, production stack, env-driven settings + fail-fast guard, session-only API auth | ~8 real decisions with no ADR | 4–6h | documentation, P1 - High |
| **B6** | Lint/format tooling — add `ruff`/`black` + standards doc | CONTRIBUTING has standards but nothing enforces them | 2–3h | documentation, P2 - Medium |

### 🟡 P2/P3 — Maintenance / long-term

| ID | Item | Why | Effort | Labels |
|----|------|-----|--------|--------|
| **C1** | `docs/OPERATIONS_RUNBOOK.md` — consolidated troubleshooting, incident response, health-check endpoint, monitoring actually implemented | Monitoring aspirational; troubleshooting scattered | 4–6h | deployment, P2 - Medium |
| **C2** | `docs/ARCHITECTURE.md` — system overview, data-flow diagram, app-dependency map | No single high-level architecture view | 3–4h | documentation, P2 - Medium |
| **C3** | Mailbox-sync production strategy — resolve multi-worker duplicate-sync (flagged in DEPLOYMENT.md Step 10) | Known unaddressed production behavior | 2–3h | bug, P2 - Medium, component: accounts |
| **C4** | Refresh `INDEX.md` gap-analysis section — assigns work to fictional owners ("DBA team", "QA lead", "Marketing team") on a solo project | Stale/misleading | 1h | documentation, P3 - Low |
| **C5** | Video tutorials | INDEX lists at 20h/Low | 20h | — (defer/skip for solo dev) |

---

## 5. Coverage of the 10 Requested Evaluation Dimensions

| Requested dimension | Where addressed |
|---------------------|-----------------|
| Current state vs best-practice coverage | §2 Health Snapshot |
| Missing operational runbooks (troubleshooting, rollback) | A2, A3, C1 |
| Incomplete/outdated technical specifications | C4 (+ audit report for corrected specs) |
| Undocumented architectural decisions | B5 |
| Missing API docs for non-DRF endpoints | B4 |
| Gaps in deployment and migration guides | A2, A3, C1 (migration guide itself is complete) |
| Insufficient security documentation | A1, A4 |
| Testing strategy gaps | B1, B2, B6 |
| Database schema documentation needs | B3 |
| Future work items in existing docs (INDEX gap analysis) | C4; and INDEX's planned TESTING/DATABASE_SCHEMA/SECURITY_POLICY/RELEASE_PROCEDURE map to B1/B3/A1/A3 |

---

## 6. Sequencing Recommendation

```
Week 1 (launch blockers):   A4 (fix XOR) -> A1 (SECURITY.md) -> A2 (backup/restore) -> A3 (release/rollback)
Week 2-3 (quality floor):   B1 (TESTING.md) -> B2 (CI) -> B6 (lint)   [then test-writing epic]
Week 3-5 (maintainability): B3 (schema) -> B5 (ADRs) -> B4 (routes, phased per app)
Ongoing:                    C1-C4 as capacity allows
```

**Total documentation effort:** ~55–80h (excludes the test-*writing* epic and C5 videos).
**Highest ROI first:** A4 + A1 (security), because production deployment is imminent and credentials currently use reversible obfuscation with no policy.

---

## 7. Interdependencies

- **A1** (SECURITY.md) depends on **A4** (credential encryption fix) — document the safe design.
- **B2** (CI) should enforce **B1** (tests) and **B6** (lint), and populate branch-protection `required_status_checks` (currently `null`).
- **B3** (schema) supports **DATABASE_MIGRATION.md** and future maintenance.
- **C3** (mailbox sync) resolves a caveat already noted in **DEPLOYMENT.md** Step 10.

---

*Generated: September 6, 2026*
*Maintainer: Sean Liu (@sean7084)*
