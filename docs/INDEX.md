# Documentation Index - Waypost

Complete list of project documentation for reference and navigation.

---

## Table of Contents

### 📋 Core Project Documentation

| Document | Location | Purpose |
|----------|----------|---------|
| **README.md** | Root | Project overview, quick start guide |
| **CHANGELOG.md** | Root | Version history and release notes |
| **LICENSE** | Root | Licensing terms and usage rights |
| **CONTRIBUTING.md** | `docs/` | Contribution guidelines and coding standards |

### 🏗️ Architecture & Design

| Document | Location | Purpose |
|----------|----------|---------|
| **ARCHITECTURE.md** | `docs/` | System context, bounded contexts, end-to-end data flows |
| **ADR-0001** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Service catalog separation decision |
| **ADR-0002** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Mailbox-driven RFQ automation |
| **ADR-0003** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Direct dispatch fulfillment workflow |
| **ADR-0004** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Hardware-only assets boundary |
| **ADR-0005** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Product price lifecycle management |
| **ADR-0006** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Import rollback system design |
| **ADR-0007** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Multi-role administrator access control |
| **ADR-0008** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | HTML-to-PDF generation without LibreOffice |
| **ADR-0009** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Warehouse slot tracking implementation |
| **ADR-0010** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | Company contact vs company user model refactoring |
| **ADR-0011** | `docs/ARCHITECTURAL_DECISION_RECORDS.md` | WeChat mini program for Kering store device inspection |

### 🔧 Operational Guides

| Document | Location | Audience |
|----------|----------|----------|
| **API_GUIDE.md** | `docs/` | Developers integrating via API |
| **MINIPROGRAM_SPEC.md** | `docs/` | WeChat mini program (Kering store inspection) spec |
| **DEPLOYMENT.md** | `docs/` | DevOps engineers deploying production |
| **DEPLOYMENT_MINIPROGRAM.md** | `docs/` | Deploying the WeChat mini program (console, legal domains, CI, release) |
| **RELEASE_PROCEDURE.md** | `docs/` | Cutting, deploying, verifying, and rolling back a release |
| **WORKFLOW_GUIDE.md** | `docs/` | Business users operating daily workflows |
| **WEB_ROUTES.md** | `docs/` | Every HTML route → view → purpose → required capability |
| **GITHUB_SETTINGS.md** | `docs/` | Maintainers managing repo configuration |
| **DATABASE_MIGRATION.md** | `docs/` | Migrating data from SQLite to PostgreSQL |
| **DATABASE_SCHEMA.md** | `docs/` | ER diagrams, table/field reference, unique business keys |
| **TESTING.md** | `docs/` | Running tests, conventions, coverage baseline + ratchet plan |
| **SECURITY.md** | `docs/` | Security policy, RBAC, 2FA, encryption |
| **BACKUP_RESTORE.md** | `docs/` | Backup & disaster-recovery runbook |

### 📊 Completion & Audit Reports

Point-in-time delivery reports, audits and gap analyses. New reports live in `docs/reports/`;
the earlier ones are in the legacy `reports/` folder at the repository root.

| Document | Location | Purpose |
|----------|----------|---------|
| **INSPECTIONS_ITERATION2_COMPLETION_REPORT_20260924.md** | `docs/reports/` | Inspections iteration 2 delivery: scope, auto-arrangement rule, endpoint surface, defects caught in verification, evidence screenshots |
| **MINIPROGRAM_SPEC_COMPLETION_REPORT_20260914.md** | `reports/` | WeChat mini program spec completion |
| **DOCUMENTATION_GAP_ANALYSIS_20260906.md** | `reports/` | Authoritative documentation gap analysis |
| **DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md** | `reports/` | Documentation vs implementation alignment audit |

### 🚀 Getting Started Resources

| Document | Location | Use Case |
|----------|----------|----------|
| **README Quick Start** | `README.md` | First-time developers setting up local environment |
| **DEPLOYMENT Guide** | `docs/DEPLOYMENT.md` | Production setup and infrastructure provisioning |
| **API Documentation** | `docs/API_GUIDE.md` | Understanding available REST endpoints |
| **Workflow Guide** | `docs/WORKFLOW_GUIDE.md` | Learning how to use business features |

### 🐛 Issue Tracking Templates

Located in `.github/ISSUE_TEMPLATE/`:

| Template File | Purpose |
|---------------|---------|
| **bug_report.md** | Report application bugs systematically |
| **feature_request.md** | Request new functionality or improvements |
| **task.md** | Track complex work items and epics |
| **deployment.md** | Request production deployments formally |
| **security.md** | Report security vulnerabilities privately |

### 🎫 GitHub Configuration Files

| File | Purpose |
|------|---------|
| **CODEOWNERS** | Define code review ownership per app/module |
| **LABELS.md** | Standardized label definitions and color codes |
| **PULL_REQUEST_TEMPLATE.md** | PR creation guidance and checklist |
| **PROJECT_WORKFLOW.md** | GitHub Projects board configuration guide |

> 📘 **Consolidated overview:** [`docs/GITHUB_SETTINGS.md`](GITHUB_SETTINGS.md) documents the live state of all the above (branch protection, CODEOWNERS, 37 labels, project automation) plus the reasoning behind each decision.

---

## Documentation Structure Diagram

```
waypost/
├── README.md                      ← Main landing page
├── CHANGELOG.md                   ← Release history  
├── LICENSE                        ← Legal terms
├── docker-compose.yml             ← Production stack: app + postgres:16 + redis:7
├── docker/                        ← Container deployment assets
│   ├── Dockerfile                 ← Multi-stage image (WeasyPrint, LibreOffice, CJK fonts)
│   ├── entrypoint.sh              ← migrate → collectstatic → exec gunicorn
│   ├── nginx/waypost.conf         ← Host Nginx site (SNI-shared 443)
│   └── bin/ci-deploy.sh           ← Tag-promoted deploy invoked by CI over SSH
│
├── docs/                          ← Detailed specifications
│   ├── ARCHITECTURAL_DECISION_RECORDS.md     ← ADRs (0001-0011)
│   ├── ARCHITECTURE.md          ← System context + data flows
│   ├── API_GUIDE.md             ← REST API reference
│   ├── WEB_ROUTES.md            ← HTML route/view/permission reference
│   ├── TESTING.md               ← Test strategy, conventions, coverage
│   ├── DEPLOYMENT.md            ← Production setup guide
│   ├── DEPLOYMENT_MINIPROGRAM.md ← WeChat mini program deployment runbook
│   ├── MINIPROGRAM_SPEC.md      ← WeChat mini program spec
│   ├── RELEASE_PROCEDURE.md     ← Versioning, tagging, deploy & rollback
│   ├── DATABASE_MIGRATION.md    ← SQLite → PostgreSQL data migration
│   ├── DATABASE_SCHEMA.md       ← ER diagrams + table reference
│   ├── BACKUP_RESTORE.md        ← Backup & restore runbook
│   ├── CONTRIBUTING.md          ← Contributing guidelines
│   ├── GITHUB_SETTINGS.md       ← Branch protection, CODEOWNERS, labels
│   ├── SECURITY.md              ← Security policy & architecture
│   └── WORKFLOW_GUIDE.md        ← Business operations manual
│
└── .github/                       ← GitHub-specific configs
    ├── ISSUE_TEMPLATE/           ← Issue templates folder
    │   ├── bug_report.md
    │   ├── feature_request.md
    │   ├── task.md
    │   ├── deployment.md
    │   └── security.md
    ├── CODEOWNERS                ← Review ownership rules
    ├── LABELS.md                 ← Label definitions
    ├── PULL_REQUEST_TEMPLATE.md  ← PR submission template
    └── PROJECT_WORKFLOW.md       ← Project board instructions
```

---

## How to Navigate This Documentation

### For New Contributors

1. **Start with**: `README.md` → understand project purpose
2. **Read**: `docs/CONTRIBUTING.md` → learn coding standards
3. **Review**: `docs/ARCHITECTURAL_DECISION_RECORDS.md` → understand architecture decisions
4. **Practice**: Fork repo, fix "good first issue" labeled tickets

### For Production Deployment

1. **Production path**: `DEPLOYMENT.md` → "Option 2: Docker Compose Deployment"
   (host prerequisites, `.env`, build, Nginx/TLS, CI/CD credentials)
2. **Bare-metal alternative**: `DEPLOYMENT.md` → "Option 1: Manual Installation"
3. **Releasing**: `RELEASE_PROCEDURE.md` §4–§6 — pushing an annotated `v*` tag is
   what deploys; merging to `main` does not
4. **Data**: `DATABASE_MIGRATION.md` for SQLite → PostgreSQL
5. **Recovery**: `BACKUP_RESTORE.md` §2b/§3a for the containerised stack
6. **Verify**: `curl https://<host>/healthz/` (`waypost/health.py`), then the
   `RELEASE_PROCEDURE.md` §7 checklist

### For API Integration

1. **Understand auth**: `docs/API_GUIDE.md` → Authentication section
2. **Browse endpoints**: Filter by resource type (assets, quotations, deliveries)
3. **Test locally**: Use Django test client or Python requests library examples
4. **Reference error format**: Section on standard response structure

### For Daily Operations

1. **Workflow walkthrough**: `docs/WORKFLOW_GUIDE.md` → Order Management section
2. **Step-by-step procedures**: Follow import/export/invoice cycles
3. **Troubleshooting tips**: Appendix sections address common issues

---

## Documentation Maintenance Guidelines

### Update Frequency

| Document Type | Review Cycle | Owner |
|---------------|--------------|-------|
| Technical specs (ADRs) | Per major change | Lead architect |
| API guide | With each release cycle | Backend team |
| Deployment guide | When environment changes | DevOps engineer |
| Workflow guide | Quarterly | Operations lead |
| Issue templates | As needed | Maintainers |

### Change Control

To update documentation:

1. Submit PR modifying target `.md` file
2. Tag relevant reviewer from domain
3. Ensure cross-references updated if moved between files
4. Verify links render correctly before merge

### Quality Standards

All documentation should:
- ✅ Use clear, active voice (avoid passive constructions)
- ✅ Include code examples where applicable
- ✅ Reference specific file paths, line numbers when possible
- ✅ Provide screenshots for visual workflows
- ✅ Link to related internal documentation

---

## Missing Documentation Gap Analysis

> **Maintainer reality:** this is a **solo-maintainer** project. Earlier revisions of this
> section assigned work to roles that do not exist here (DBA team, QA lead, Security officer,
> Engineering manager, Marketing team). Tracking is by GitHub issue instead. The authoritative
> analysis is
> [`reports/DOCUMENTATION_GAP_ANALYSIS_20260906.md`](../reports/DOCUMENTATION_GAP_ANALYSIS_20260906.md).

### Delivered

| Document | Delivered | Issue |
|---|---|---|
| `docs/SECURITY.md` — policy, RBAC, 2FA, encryption | Sept 7, 2026 | — |
| `docs/RELEASE_PROCEDURE.md` — versioning, tagging, deploy, rollback | Sept 7, 2026 | — |
| `docs/BACKUP_RESTORE.md` — backup & disaster recovery | Sept 13, 2026 | #14 |
| `docs/MINIPROGRAM_SPEC.md` — WeChat mini program contract | Sept 13, 2026 | #54 |
| `docs/DEPLOYMENT_MINIPROGRAM.md` — mini program deployment runbook | Sept 14, 2026 | #54 |
| `docs/ARCHITECTURE.md` — system context, bounded contexts, data flows | Sept 15, 2026 | #23 |
| `docs/DATABASE_SCHEMA.md` — ER diagrams, table reference, unique keys | Sept 15, 2026 | #18 |
| `docs/WEB_ROUTES.md` — HTML route → view → purpose → capability | Sept 15, 2026 | #19 |
| `docs/TESTING.md` — strategy, conventions, coverage baseline + ratchet | Sept 15, 2026 | #16 |

### Remaining gaps

| Gap | Priority | Est. effort | Issue |
|---|---|---|---|
| **Write the missing tests** — `assets` (23.9%), `deliveries` (21.5%), `utils/import_rollback`, `invoices` first; overall coverage is 43.7% | High | 20+ hours | #16 (strategy done — test-writing is a separate epic) |
| ADRs 0012–0018 for undocumented architectural decisions | High | 4 hours | #20 |
| `docs/OPERATIONS_RUNBOOK.md` — troubleshooting, incident response, monitoring | Medium | 4 hours | #22 |
| Production mailbox-sync strategy — the background thread is `runserver`-only, so nothing polls unattended in production | Medium | 3 hours | #24 |
| WeChat MP registration, HTTPS/ICP domain, legal-domain whitelist | Medium | external | #53 |
| Video tutorials for key workflows | Low | 20 hours | untracked |

**Delivered since the table above was written:** lint tooling and enforcement
(#21 — ruff is a required check on `main`) and the CI coverage step with
PostgreSQL/Redis service containers (#17 — `backend-ci.yml`).

> Effort estimates are indicative for one maintainer working in focused blocks; they are not
> commitments. Priority reflects risk × size, per the gap analysis above.

---

## External Resources Links

### Official References

- **[Django Documentation](https://docs.djangoproject.com/)** - Framework reference
- **[Bootstrap 5 Docs](https://getbootstrap.com/docs/5.0/)** - UI component library
- **[WeasyPrint Examples](https://doc.courtbard.net/projects/weasyprint/en/stable/examples.html)** - PDF rendering samples
- **[Minimax API Docs](https://open.xiaomi.com/)** - AI classification integration

### Community Resources

- **[Django Channels Slack](https://django-channels-community.slack.com/)** - Async development support
- **[Stack Overflow Questions Tagged "Django"]()** - Problem-solving Q&A platform

---

## Contact Information for Documentation Issues

Questions about any document or suggestions for improvement?

📧 Email: docs@istore-tech.com  
💬 Slack Channel: #documentation-team  
📝 Contribution: Submit PR using contribution guidelines

---

*Last Updated: October 7, 2026*  
*Author: Sean Liu and contributors*
