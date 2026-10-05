# GitHub Labels Configuration for Waypost

## Overview

This document defines the labeling strategy for the Waypost repository to streamline issue tracking, pull request management, and project workflow.

---

## Label Categories

### Status Labels (4)

| Label Name | Color | Description |
|------------|-------|-------------|
| `triage` | #E9D75F | Needs initial review and categorization |
| `awaiting response` | #0E8A16 | Waiting on author feedback |
| `blocked` | #BDBDBD | Cannot proceed due to dependency |
| `stale` | #EEEEEE | No activity for 30 days |

---

### Priority Labels (4)

| Label Name | Color | Description |
|------------|-------|-------------|
| `P0 - Critical` | #B60205 | Blocks release; needs immediate fix |
| `P1 - High` | #D93F0B | Important bug/feature for next sprint |
| `P2 - Medium` | #FBCA04 | Regular backlog item |
| `P3 - Low` | #EDEDED | Nice-to-have, low priority |

---

### Category Labels (7)

| Label Name | Color | Description |
|------------|-------|-------------|
| `bug` | #D73A4A | Something isn't working correctly |
| `enhancement` | #A2EEEF | New feature request |
| `documentation` | #0075CA | Improvements to docs |
| `good first issue` | #7057FF | Easy for newcomers |
| `help wanted` | #008672 | Contribution needed |
| `question` | #CC3147 | More information needed |
| `discussion` | #CCE329 | Topics requiring community input |

---

### Component Labels (13)

| Label Name | Color | Description |
|------------|-------|-------------|
| `component: accounts` | #1D76EB | User management, authentication |
| `component: assets` | #0052CC | Asset CRUD, assignments |
| `component: companies` | #54AE5F | Company/location structures |
| `component: quotations` | #FBE75F | Quotation lifecycle |
| `component: deliveries` | #FBF29D | Delivery orders |
| `component: invoices` | #A2EEEF | Invoice processing |
| `component: products` | #5791EF | Price list, service catalog |
| `component: dashboard` | #FFFFFF | Main landing page |
| `component: reports` | #79CB23 | Analytics & charts |
| `component: api` | #0E8A16 | REST API surface (`/api/v1`) |
| `component: inspections` | #5319E7 | Kering store device-inspection domain |
| `component: miniprogram` | #07A162 | WeChat mini program client |
| `component: import` | #D4C5F9 | Data import pipelines |

---

### Impact Labels (3)

| Label Name | Color | Description |
|------------|-------|-------------|
| `breaking change` | #B60205 | Incompatible API/model changes |
| `deprecation` | #D8744E | Deprecated feature scheduled for removal |
| `migration-required` | #BF5D73 | Database migration required |

---

### Testing Labels (3)

| Label Name | Color | Description |
|------------|-------|-------------|
| `needs testing` | #FFEBD6 | Awaiting QA validation |
| `test-passed` | #84b6ef | Verified in staging |
| `e2e-test-needed` | #7057FF | End-to-end test coverage gap |

---

### Deployment Labels (3)

| Label Name | Color | Description |
|------------|-------|-------------|
| `deployment` | #C2E0C6 | Ready for deployment |
| `release-notes` | #0366D6 | Must be included in changelog |
| `security` | #EB6420 | Security-related fix |
| `performance` | #C6CFCF | Speed/performance improvement |

---

## Usage Guidelines

### Label Combinations

**For Bug Reports:**
- Priority: `P0`, `P1`, `P2`, or `P3`
- Type: `bug`
- Component: `component: [area]`
- Status: `needs testing` or `test-passed`

**For Feature Requests:**
- Priority: `P0`, `P1`, `P2`, or `P3`
- Type: `enhancement`
- Component: `component: [area]`
- Planning: `discussion` if still being evaluated

**For Pull Requests:**
- Status: `awaiting response` (if waiting for review)
- Testing: `e2e-test-needed` (if automation tests missing)
- Approval: `test-passed` (after QA verification)

### Automation Integration

These labels work seamlessly with GitHub Projects automation rules:
- Automatically move cards based on status changes
- Trigger notifications for high-priority items
- Archive stale issues after 30 days

---

## Setup Script

Use `scripts/setup-github-labels.ps1` to automatically create all labels via GitHub API.

```powershell
.\scripts\setup-github-labels.ps1
```

---

*Last Updated: August 24, 2026*
