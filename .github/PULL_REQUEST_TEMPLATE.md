# Pull Request Template

## Description
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

## Design System Conformance (required for UI changes)
Applies to any change touching `templates/`, `static/css`, `static/js`, or `miniprogram/`.
See [`docs/DESIGN_SYSTEM.md`](../docs/DESIGN_SYSTEM.md).
- [ ] Uses design tokens (`var(--wp-*)`); no raw hex in shared/core files
- [ ] Text and controls meet WCAG 2.1 AA contrast
- [ ] No control hidden/dimmed with `opacity: 0`; disabled uses a neutral fill at full opacity
- [ ] No decorative gradients behind text or controls
- [ ] Top nav stays on a single row (priority+ overflow verified at narrow widths)
- [ ] Mini program kept in sync (tokens regenerated; `app.json` colours match tokens)
- [ ] Ran `python scripts/build_design_tokens.py --check` and `python manage.py test tests.test_design_system`
- [ ] Attached before/after screenshots at ~360px and ~1440px widths

## Related Issues
Fixes #[issue_number]

## Notes
Any additional context or questions for reviewers.
