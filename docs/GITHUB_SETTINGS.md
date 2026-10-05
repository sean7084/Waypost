# GitHub Settings Overview - Waypost

**Repository:** [sean7084/Waypost](https://github.com/sean7084/Waypost)  
**Type:** Personal repository (single maintainer)  
**Last Verified:** September 6, 2026  

This document is the single source of truth for all GitHub repository configuration. It records *what* is configured, *why* decisions were made, and *how* to re-apply settings after changes.

---

## Table of Contents

1. [Branch Protection](#1-branch-protection)
2. [CODEOWNERS](#2-codeowners)
3. [Labels](#3-labels)
4. [Issue Templates](#4-issue-templates)
5. [Pull Request Template](#5-pull-request-template)
6. [Project Board & Automation](#6-project-board--automation)
7. [Automation Scripts Inventory](#7-automation-scripts-inventory)
8. [Personal Repository Limitations](#8-personal-repository-limitations)
9. [Re-applying Settings](#9-re-applying-settings)

---

## 1. Branch Protection

**Branch:** `main`  
**Strategy:** **Option C — Strict protection with admin bypass**

### Active Rules

| Setting | Value | Effect |
|---------|-------|--------|
| Require pull request before merging | ✅ Enabled | No direct pushes to `main` |
| Required approving reviews | **1** | Collaborators need one approval |
| Require review from Code Owners | ✅ Enabled | CODEOWNERS enforced for collaborators |
| Dismiss stale reviews | ✅ Enabled | Approvals reset when new commits are pushed |
| Enforce for admins (`enforce_admins`) | ❌ **Disabled** | **Owner can bypass** review requirements |
| Required status checks | ✅ **`Django checks + tests`**, **`Python lint (ruff)`** | Backend CI and the ruff correctness gate must pass; `strict = true` requires the branch to be up to date with `main` |
| Allow force pushes | ❌ Blocked | History rewriting prevented |
| Allow deletions | ❌ Blocked | `main` cannot be deleted |

### Why Option C?

This project currently has a **single maintainer** (`sean7084`). GitHub never lets an author approve their own pull request, so requiring reviews with `enforce_admins = true` would permanently block the owner from merging their own work.

By setting `enforce_admins = false`:
- ✅ The **owner can merge** their own PRs (practical for solo development)
- 🔒 **Future collaborators** are still forced through the review process
- 🔒 CODEOWNERS auto-review activates automatically once other contributors exist

This is the personal-repository equivalent of the organization-only **"Allow specified actors to bypass"** setting.

> **Note:** The "Allow specified actors to bypass required pull requests" option in the GitHub UI only appears for **organization** repositories. For personal repositories, `enforce_admins = false` achieves the same result.

### API Payload (for reference)

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Django checks + tests", "Python lint (ruff)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null
}
```

### Which checks are required, and why

Both required contexts are jobs in `.github/workflows/backend-ci.yml`:

- **`Django checks + tests`** — `manage.py check`, `makemigrations --check`, then the suite under
  coverage (which enforces `fail_under`) against PostgreSQL 16 and Redis 7 service containers.
- **`Python lint (ruff)`** — the correctness-only ruff gate configured under `[tool.ruff]` in
  `pyproject.toml`. It runs as a separate job with no database or Django install, so it reports
  in seconds. Adopting it immediately found three real defects in untested code (issues #68,
  #69, #70), which is the argument for gating on correctness rules rather than style.

That workflow has **no path filter** — deliberately. A required status check that never runs on a
given PR leaves the PR permanently blocked, so a path-filtered required workflow (one ignoring
`docs/**` or `miniprogram/**`) would make docs-only PRs unmergeable. Backend CI therefore runs on
every PR.

`Lint mini program` (`.github/workflows/miniprogram-ci.yml`) *is* path-filtered to
`miniprogram/**`, which is correct for a non-required check — it stays green/absent on unrelated
PRs without blocking them. It is intentionally **not** in `contexts`.

Because `enforce_admins = false`, the owner can still bypass a failing or missing check; the
requirement mainly protects future collaborators and makes CI status visible on every PR.

> ⚠️ `dismissal_restrictions` and `restrictions` with users/teams are **rejected** on personal repositories with error: *"Only organization repositories can have users and team restrictions"*. These fields must be omitted or set to `null`.

---

## 2. CODEOWNERS

**Location:** Repository root (`/CODEOWNERS`)  
**Format:** Standard GitHub `pattern @owner` syntax  
**Validation Status:** ✅ Valid (0 errors via `/codeowners/errors` API)

### Configuration

All paths are owned by `@sean7084` (sole maintainer). Key patterns:

```
* @sean7084                    # Default owner for all files
/accounts/ @sean7084           # Each Django app module
/assets/ @sean7084
...
**/migrations/ @sean7084       # Database migrations (extra care)
requirements.txt @sean7084     # Security-sensitive dependencies
*.md @sean7084                 # Documentation
/.github/ @sean7084            # GitHub configuration
```

### Important Behaviors (Learned)

1. **CODEOWNERS is read from the base branch**, not the PR head branch. A valid file must exist on `main` before auto-assignment works.
2. **The PR author is never auto-assigned** as a reviewer. Since the owner authors most PRs, the reviewer list appears blank — this is expected, correct behavior.
3. **Syntax must be plain text** (`path @owner`). A previous Markdown/YAML version was silently ignored (parsed zero rules).
4. **Valid locations:** root `/CODEOWNERS`, `/.github/CODEOWNERS`, or `/docs/CODEOWNERS`.

---

## 3. Labels

**Total:** 37 labels (34 custom + 3 GitHub defaults)

### Custom Labels by Category

**Status (4)**
| Label | Color |
|-------|-------|
| `triage` | #E9D75F |
| `awaiting response` | #0E8A16 |
| `blocked` | #BDBDBD |
| `stale` | #EEEEEE |

**Priority (4)**
| Label | Color |
|-------|-------|
| `P0 - Critical` | #B60205 |
| `P1 - High` | #D93F0B |
| `P2 - Medium` | #FBCA04 |
| `P3 - Low` | #EDEDED |

**Category (7)**
| Label | Color |
|-------|-------|
| `bug` | #D73A4A |
| `enhancement` | #A2EEEF |
| `documentation` | #0075CA |
| `good first issue` | #7057FF |
| `help wanted` | #008672 |
| `question` | #CC3147 |
| `discussion` | #CCE329 |

**Component (9)**
| Label | Color |
|-------|-------|
| `component: accounts` | #1D76EB |
| `component: assets` | #0052CC |
| `component: companies` | #54AE5F |
| `component: quotations` | #FBE75F |
| `component: deliveries` | #FBF29D |
| `component: invoices` | #A2EEEF |
| `component: products` | #5791EF |
| `component: dashboard` | #FFFFFF |
| `component: reports` | #79CB23 |

**Impact (3)**
| Label | Color |
|-------|-------|
| `breaking change` | #B60205 |
| `deprecation` | #D8744E |
| `migration-required` | #BF5D73 |

**Testing (3)**
| Label | Color |
|-------|-------|
| `needs testing` | #FFEBD6 |
| `test-passed` | #84b6ef |
| `e2e-test-needed` | #7057FF |

**Deployment & Quality (4)**
| Label | Color |
|-------|-------|
| `deployment` | #C2E0C6 |
| `release-notes` | #0366D6 |
| `security` | #EB6420 |
| `performance` | #C6CFCF |

### GitHub Default Labels (3)

`duplicate`, `invalid`, `wontfix` — pre-existing GitHub defaults, retained.

> Full definitions with descriptions: [`.github/LABELS.md`](../.github/LABELS.md)

---

## 4. Issue Templates

**Location:** `.github/ISSUE_TEMPLATE/`

| Template | File | Purpose |
|----------|------|---------|
| 🐛 Bug Report | `bug_report.md` | Structured bug reporting |
| ✨ Feature Request | `feature_request.md` | New functionality requests |
| 🎫 Task / Epic | `task.md` | Complex work items |
| 🚀 Deployment Request | `deployment.md` | Production deployment process |
| 🔒 Security Vulnerability | `security.md` | Confidential vulnerability reports |

---

## 5. Pull Request Template

**Location:** `.github/PULL_REQUEST_TEMPLATE.md`

Automatically appears when creating any PR. Includes a review checklist, testing requirements, and description prompts.

---

## 6. Project Board & Automation

**Project URL:** https://github.com/users/sean7084/projects/1  
**Project Type:** New GitHub Projects (Projects V2), **not** Classic

### Board Columns

```
To Do → In Progress → Code Review → Testing → Ready for Deploy → Deployed → Done
```

### Custom Fields (Configured)

| Field | Type | Options |
|-------|------|---------|
| Priority | Single select | P0-Critical, P1-High, P2-Medium, P3-Low |
| Component | Multi select | 9 components (accounts, assets, companies, quotations, deliveries, invoices, products, dashboard, reports) |
| Story Points | Number | — |
| Sprint | Text | — |

### Automation Workflows

The new Projects experience uses **Workflows** (not Classic "Automate" rules).

| Rule | Trigger | Action | Status |
|------|---------|--------|--------|
| **PR created** | Item added (pull request) | Set Status → **Code Review** | ✅ Configured |
| **PR merged** | Pull request merged | Set Status → **Testing** | ✅ Configured |
| **Bug labeled** | Issue labeled `bug` | Move to In Progress | ❌ Not supported natively |
| **Stale archive** | 30d inactivity + `stale` | Archive card | ⚠️ Partial (inactivity only) |

### Automation Limitations

The new Projects workflow engine **cannot filter by repository labels** (`Invalid filter: Unknown field name "label"`). To implement label-based rules (bug → In Progress, stale + 30d → archive), a **scheduled GitHub Actions workflow** using the Projects GraphQL API is required. This is deferred as future work.

---

## 7. Automation Scripts Inventory

**Location:** `scripts/`

| Script | Purpose | Status |
|--------|---------|--------|
| `setup-github-labels.ps1` | Create all labels via REST API | ✅ Active |
| `setup-branch-protection.ps1` | Configure branch protection (Option C) | ✅ Active — **canonical** |
| `verify-github-setup.ps1` | Verify labels, project, protection | ✅ Active |
| `setup-github-project-automation.ps1` | Print automation setup guide | 📄 Reference only |
| `run-github-setup.ps1` | Master runner for all setup steps | ⚠️ Legacy |
| `setup-github-project.ps1` | Project creation (superseded by manual) | ⚠️ Legacy |
| `setup-github-branch-protection.ps1` | Older protection script | ⚠️ Legacy — superseded |

> **Note:** `setup-branch-protection.ps1` is the current, working branch protection script. The older `setup-github-branch-protection.ps1` is superseded and can be removed during future cleanup.

All scripts read the token from `.env` (`GITHUB_CLASSIC_TOKEN`), falling back to a legacy `.env.local` if that is what exists. Both are gitignored.

---

## 8. Personal Repository Limitations

Constraints discovered because this is a **personal** (not organization) repository:

1. **No `restrictions`/`dismissal_restrictions`** — API rejects user/team restrictions with HTTP 422.
2. **No "Allow specified actors" UI** — use `enforce_admins = false` instead.
3. **No self-approval** — the owner cannot approve their own PR; CODEOWNERS won't assign the author.
4. **Projects V2 label filtering unsupported** — label-based automation needs GitHub Actions.
5. **Token scopes** — a classic token needs `repo` (and ideally `admin:repo_hook`, `project`) scopes. A token missing `repo` returns HTTP 401.

---

## 9. Re-applying Settings

### Re-apply Branch Protection

```powershell
.\scripts\setup-branch-protection.ps1
```

### Re-create Labels

```powershell
.\scripts\setup-github-labels.ps1
```

### Verify Everything

```powershell
.\scripts\verify-github-setup.ps1
```

### Manual Verification via API

```powershell
$envFile = @('.env', '.env.local') | Where-Object { Test-Path $_ } | Select-Object -First 1
$token = (Get-Content $envFile | Select-String '^GITHUB_CLASSIC_TOKEN=').ToString().Split('=')[1]
$headers = @{ "Authorization" = "Bearer $token"; "Accept" = "application/vnd.github.v3+json" }

# Branch protection
Invoke-RestMethod -Uri "https://api.github.com/repos/sean7084/Waypost/branches/main/protection" -Headers $headers

# CODEOWNERS validity
Invoke-RestMethod -Uri "https://api.github.com/repos/sean7084/Waypost/codeowners/errors" -Headers $headers

# Label count
(Invoke-RestMethod -Uri "https://api.github.com/repos/sean7084/Waypost/labels?per_page=100" -Headers $headers).Count
```

---

## Related Documentation

- [`.github/LABELS.md`](../.github/LABELS.md) — Label definitions
- [`.github/PROJECT_WORKFLOW.md`](../.github/PROJECT_WORKFLOW.md) — Project workflow guide
- [`docs/CONTRIBUTING.md`](CONTRIBUTING.md) — Contribution guidelines
- [`docs/INDEX.md`](INDEX.md) — Documentation index

---

*Last Updated: September 6, 2026*  
*Maintainer: Sean Liu (@sean7084)*
