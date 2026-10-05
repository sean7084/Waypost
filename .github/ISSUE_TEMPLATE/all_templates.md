# Issue Templates for Waypost

Place these files in `.github/ISSUE_TEMPLATE/` directory.

---

## Bug Report

```markdown
---
name: 🐛 Bug Report
about: Create a report to help us improve
title: "[BUG] Brief description of the issue"
labels: ["bug", "triage"]
assignees: []
---

## Description
A clear and concise description of what the bug is.

## Steps to Reproduce
1. Go to '...'
2. Click on '...'
3. Scroll down to '...'
4. See error

## Expected Behavior
What did you expect to happen?

## Actual Behavior
What actually happened? Include screenshots/error messages here.

## Environment
- Browser: [e.g., Chrome 120, Firefox 121]
- OS: [e.g., Windows 11, Ubuntu 22.04]
- Django Version: [e.g., 5.2.3]
- Python Version: [e.g., 3.11.5]
- Database: SQLite/PostgreSQL

## Additional Context
Add any other context about the problem here.
Include relevant code snippets or traceback if applicable.

## Screenshots
Attach screenshots if visual evidence helps explain the issue.
```

---

## Feature Request

```markdown
---
name: ✨ Feature Request
about: Suggest an idea for this project
title: "[FEATURE] Short feature description"
labels: ["enhancement", "ideas"]
assignees: []
---

## Problem Statement
Why do we need this feature? What problem does it solve?

## Proposed Solution
Describe your proposed solution. How should it work?

## Alternatives Considered
What other approaches have you thought about? Why are they less ideal?

## Acceptance Criteria
- [ ] When user accesses ..., they see ...
- [ ] The system accepts ... input and outputs ...
- [ ] Edge cases include ...
- [ ] Performance impact: < X seconds for Y users

## User Story (optional)
As a [role], I want [goal], so that [benefit].

## Mockups/Wireframes
If applicable, attach sketches, Figma links, or wireframe tools.

## Technical Notes (optional)
Any implementation ideas, potential risks, or dependencies.
```

---

## Task / Epic

```markdown
---
name: 🎫 Task / Epic
about: Track larger pieces of work (epics or tasks)
title: "[TASK/EPIC] Name of work item"
labels: ["task", "planning"]
assignees: []
---

## Overview
Brief description of what needs to be done.

## Scope
### In Scope
- Item 1
- Item 2

### Out of Scope (explicitly excluded)
- Item that might seem related but isn't included

## Dependencies
- Depends on: #[issue_number]
- Blocks: #[issue_number]

## Implementation Plan
Break down into subtasks:

### Phase 1: [Name]
- [ ] Step 1
- [ ] Step 2

### Phase 2: [Name]
- [ ] Step 1
- [ ] Step 2

## Testing Requirements
- Unit tests needed for: ...
- Integration tests covering: ...
- E2E flow validation: ...

## Timeline (optional)
- Target start: YYYY-MM-DD
- Estimated completion: YYYY-MM-DD
- Milestone: vX.X.X

## Related Documentation
Links to design docs, architecture decisions (ADR), or specs.
```

---

## Documentation Improvement

```markdown
---
name: 📝 Documentation Improvement
about: Suggest improvements to project documentation
title: "[DOCS] Topic to improve"
labels: ["documentation", "good first issue"]
assignees: []
---

## Current State
What's wrong with the current documentation? What's missing?

## Suggested Improvement
Describe what content should be added or changed.

## Target Audience
Who will benefit from this documentation change?

## References
Links to official docs, tutorials, or examples that could inform the improvement.
```

---

## Help Wanted

```markdown
---
name: ❓ Help Wanted
about: Asking for assistance or clarification
title: "[HELP] Specific question"
labels: ["question", "help wanted"]
assignees: []
---

## Question Summary
What are you trying to accomplish? Be specific.

## Context
Provide background information about your situation.

## What I've Tried
List steps you've already taken or issues explored.

## Code Snippets (if applicable)
Paste relevant code or configurations here.

## Attachments
Screenshots, logs, or other supporting materials.
```

---

## Installation/Setup Issue

```markdown
---
name: 🚀 Installation/Setup Issue
about: Problems with getting started or deployment
title: "[SETUP] Environment or installation error"
labels: ["setup", "infrastructure"]
assignees: []
---

## Environment Details
- OS: [Windows/Linux/Mac, version]
- Python version: [from `python --version`]
- Database: [SQLite/PostgreSQL version, MySQL version]
- Virtual environment details (`.venv`)

## Steps Taken
Describe exactly what commands/actions were performed to reproduce the issue.

## Error Messages
Paste full stack trace or error output here:

```
[Paste error log]
```

## Configuration Files
If relevant, share `.env`, `settings.py`, or Docker compose contents (without secrets):

```
{redacted configuration}
```

## What Was Working Before? (if applicable)
Did this used to work? If so, what changed recently?

## Additional Context
Mention anything else that might be relevant to the installation problem.
```

---

## Performance Issue

```markdown
---
name: ⚡ Performance Issue
about: Report slow performance or bottlenecks
title: "[PERF] Describe the slow operation"
labels: ["performance", "optimization"]
assignees: []
---

## Symptom Description
What feels slow? Which pages or operations are affected?

## Frequency
- Always happens: ~100% of requests fail
- Intermittent: Occurs occasionally
- Under load: Only visible when multiple users access simultaneously

## Metrics (if available)
- Response time observed: ~X seconds
- Database query duration: X ms
- Memory/CPU usage spikes: Yes/No

## Reproduction Steps
1. Navigate to page X
2. Perform action Y
3. Observe Z delay

## Browser DevTools / Server Logs
Paste timing analysis or relevant server log excerpts.

## Suspected Cause (optional)
Do you think this relates to database queries, frontend rendering, external API calls?
```

---

## Security Vulnerability Report

```markdown
---
name: 🔒 Security Vulnerability
about: Report a security concern privately
title: "[SECURITY] Confidential vulnerability report"
labels: ["security", "confidential"]
assignees: []
---

## VULNERABILITY TYPE
[e.g., SQL Injection, XSS, CSRF, Authentication Bypass, Privilege Escalation]

## IMPACT ASSESSMENT
How severe is this issue? What data/functions could be compromised?

## REPRODUCTION STEPS
Step-by-step instructions to verify the vulnerability:
1. ...
2. ...
3. ...

## Proof of Concept
Minimal code snippet or curl command demonstrating exploit:

```bash
curl -X POST http://example.com/api/end-point \
  -d '{"malicious": "payload"}'
```

## MITIGATION (if known)
Have you identified how to fix this? Or any workarounds?

## CONFIDENTIAL NOTE
Please treat this report as confidential. Do not publish details until remediated.

---

## Reporting Guidelines
For security issues, please also email: security@istore-tech.com
We commit to responding within 48 hours.
```

---

## Deployment Request

```markdown
---
name: 🚢 Deployment Request
about: Request production deployment
title: "[DEPLOY] Deploy version vX.Y.Z"
labels: ["deployment", "release"]
assignees: []
---

## Release Information
- Version tag: vX.Y.Z
- Branch: develop/main/feature/name
- Commit hash: abcdef1

## Changes Included
Summary of features, bug fixes, and breaking changes in this release.

## Testing Status
- [ ] All unit tests passing
- [ ] Integration tests green
- [ ] E2E critical flows validated
- [ ] Migration tested in staging
- [ ] Performance benchmarks within SLA

## Rollback Plan
If deployment fails, revert procedure:
1. `git checkout previous-tag`
2. `python manage.py migrate --plan` to verify reversibility
3. Redeploy previous stable image

## Post-Deploy Verification Checklist
- [ ] Application starts without errors
- [ ] Database migrations applied successfully
- [ ] Static files collected
- [ ] Health check endpoint returns 200
- [ ] Critical workflows verified
- [ ] Logging/alerts operational

## Stakeholder Approval
- Developer: [Signed off by lead dev]
- QA: [Approved by testing team]
- Product Owner: [Authorized by product owner]
```

---

## Pull Request Template (PR Body)

When creating a PR, the template below appears automatically:

```markdown
## What does this PR do?
Explain the purpose and scope of changes made in this pull request.

## Type of Change
- [ ] Bugfix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update
- [ ] Refactoring (no behavior changes)
- [ ] Tests added
- [ ] Performance improvement

## Affected Components
List files or apps modified:
- `apps/assets/views.py`
- `templates/assets/asset_list.html`

## How to Test
Detailed instructions for verifying the changes:
1. Apply migration: `python manage.py migrate`
2. Navigate to: `/assets/`
3. Create new asset with serial number "TEST123"
4. Verify it appears in asset list immediately

## Screenshots (UI Changes)
Attach GIF/screenshot comparisons showing before vs after.

## Checklist
- [ ] Self-reviewed my code
- [ ] Added/updated tests accordingly
- [ ] Commented hard-to-understand sections
- [ ] Made corresponding documentation changes
- [ ] Ran lint/format checks with no warnings
- [ ] Rebased onto/up-to-date with base branch
- [ ] Confirmed no regression in existing functionality

## Related Issues
Fixes #[issue_number]

## Notes
Any additional context or questions for reviewers.
```

---

*Last Updated: August 20, 2026*
