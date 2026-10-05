# GitHub Projects Workflow Setup for Waypost

## Overview

This document describes how to configure and use GitHub Projects for managing the Waypost development lifecycle.

---

## Project Board Location

**Current Project URL:** https://github.com/users/sean7084/projects/1

**Status:** ✅ Created manually (August 2026)

---

## Recommended Views

GitHub Projects supports multiple views. Configure all three for optimal workflow management:

### 1. Kanban Board View (Primary)

**Columns:**
```
To Do → In Progress → Code Review → Testing → Ready for Deploy → Deployed → Done
```

**Setup Steps:**
1. Go to your project board
2. Click "Customize columns"
3. Rename default columns or add custom workflow stages
4. Enable auto-mapping for issues/PRs

### 2. Backlog Board (Planning Mode)

**Columns:**
```
Icebox → Next Sprint → Current Sprint → In Progress → Completed
```

**Use Case:** Sprint planning and backlog grooming sessions

### 3. Table View (Detailed Filtering)

Group by priority or component. Useful for cross-board analysis.

---

## Custom Fields Configuration

Define these custom fields for each issue/task:

| Field Name | Type | Options | Purpose |
|------------|------|---------|---------|
| **Priority** | Single select | P0-Critical, P1-High, P2-Medium, P3-Low | Impact assessment |
| **Component** | Multiple select | assets, quotations, deliveries, invoices, products, accounts, companies, dashboard, reports | Area of code affected |
| **Sprint** | Number | Sprint 26, Sprint 27, etc. | Iteration tracking |
| **Story Points** | Number | 1, 2, 3, 5, 8, 13 | Estimation metric |
| **Estimated Hours** | Number | - | Time estimate for task |
| **Milestone** | Link | vX.Y.Z releases | Release target |

**How to Add Custom Fields:**
1. Click "+ New field" on your project board
2. Select field type from dropdown
3. Configure options and visibility
4. Map existing labels if applicable

---

## Automation Rules

GitHub can automatically move issues based on rules defined in the project's "Automate" menu.

### Accessing Automation Rules:
1. Open your project: https://github.com/users/sean7084/projects/1
2. Click the **"Automate"** dropdown at the top-right
3. Click **"Add rule"** to create new automations

### Rule Templates to Add:

#### Rule 1: Pull Request Creation
```
When: Pull request is created
Then: Move card to column "Code Review"
Conditions: Branch name ≠ main
```

#### Rule 2: Pull Request Merged
```
When: Pull request is merged
Then: 
  • Move card to column "Testing"
  • Add label "test-passed"
  • Link related deployment request issue
```

#### Rule 3: Bug Report Status Change
```
When: Issue labeled with "bug"
Then: Move to "In Progress" if assigned
```

#### Rule 4: Stale Issues
```
When: No activity for 30 days AND label="stale"
Then: Archive card (do NOT delete)
```

#### Rule 5: Deployment Requests
```
When: Issue labeled "deployment" created
Then: Move to "Ready for Deploy" column
Notification: Tag @devops-team on creation
```

---

## Workflow Stages Defined

### To Do
- **Definition:** Issue/PR identified but not yet started
- **Required Fields:** Priority, Component labels
- **Actions:** Assign owner, add estimated hours, link to milestone

### In Progress
- **Definition:** Work actively being performed
- **Requirements:** At least one assignee
- **Rules:** Maximum 3 tasks per developer concurrently

### Code Review
- **Definition:** PR submitted, awaiting approval
- **Required:** Minimum 1 reviewer from CODEOWNERS
- **Timeout:** Escalate after 48h if unresponsive

### Testing
- **Definition:** Code merged, awaiting QA validation
- **Criteria:** All tests green, migration tested
- **Owner:** QA team member

### Ready for Deploy
- **Definition:** Verified in staging, approved by product owner
- **Approval:** Product Owner sign-off required
- **Checklist:** Deployment template completed, rollback plan ready

### Deployed
- **Definition:** Live in production environment
- **Monitoring:** Observe error logs for 24h post-deploy
- **Success Criteria:** No critical alerts triggered

### Done
- **Definition:** Fully delivered and verified end-user facing
- **Activities:** Update release notes, close milestone

---

## Best Practices

### Card Hygiene
- Close/archived cards older than 90 days
- Keep descriptions concise but informative
- Use checkboxes for multi-step actions
- Attach screenshots/mockups directly to cards

### Communication Etiquette
- Tag people instead of assigning to themselves
- Reference issue numbers in commit messages
- Link sub-tasks to parent epic using GitHub's linking syntax
- Celebrate wins publicly in team channels

### Continuous Improvement
Monthly retrospective questions:
- What slowed us down?
- Which processes worked well?
- How to reduce cycle time next month?

---

## Quick Start Checklist

- [ ] Visit project: https://github.com/users/sean7084/projects/1
- [ ] Configure columns per recommendations above
- [ ] Add custom fields (Priority, Component, Story Points, Sprint)
- [ ] Set up automation rules via "Automate" dropdown
- [ ] Invite team members with appropriate permissions
- [ ] Create first sprint board view
- [ ] Schedule sprint planning session

---

## Troubleshooting

**Issue:** Can't find "Automate" button
- **Solution:** Ensure you're viewing the Kanban board (not Table view)
- **Alternative:** Check if using Projects Classic vs Next Gen UI

**Issue:** Automation rules not triggering
- **Solution:** Verify column names match exactly (case-sensitive)
- **Check:** Ensure labels are applied correctly

**Issue:** Columns won't reorder
- **Solution:** Drag-and-drop may be disabled; check project permissions

---

*Last Updated: August 24, 2026*
*Project Created: August 20, 2026*
