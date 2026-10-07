# Waypost Design System

**Status:** Mandatory for all UI work (web + WeChat mini program).
**Owner:** Frontend / product.
**Related:** [ADR-0012](ARCHITECTURAL_DECISION_RECORDS.md#adr-0012-unified-design-system-and-token-single-source-of-truth), [`static/design/tokens.json`](../static/design/tokens.json), [`static/css/waypost.css`](../static/css/waypost.css).

This document is the single authority on how Waypost looks and behaves. It blends
**Apple Human Interface Guidelines** clarity (restraint, whitespace, one accent,
generous radii, subtle elevation) with **Jira / ServiceNow** data density (compact
tables, clear type hierarchy, functional colour coding) because Waypost is a
data-heavy internal operations tool.

Following it is not optional. The rules in [§9 Hard rules](#9-hard-rules-enforced-in-ci)
are enforced automatically in CI; a pull request that breaks them will not pass.

---

## 1. Principles

1. **Clarity first.** Content over chrome. No decorative gradients, no busy
   backgrounds behind text or controls.
2. **One accent.** A single primary colour carries "the important action". Colour
   elsewhere is functional (status), never decorative.
3. **Consistency across surfaces.** The web app and the mini program are the same
   product. They share one token source and must not diverge.
4. **Accessible by default.** Text and interactive controls meet WCAG 2.1 AA
   contrast; touch targets are at least 44px; every interactive element has a
   visible focus state.
5. **Density with breathing room.** Tables and forms stay compact, but spacing
   follows a 4px grid so nothing feels cramped.

---

## 2. Tokens: the single source of truth

All visual values live in **[`static/design/tokens.json`](../static/design/tokens.json)**.
Nothing else defines colour, spacing, radii, shadows, type or motion.

`scripts/build_design_tokens.py` renders that JSON into two generated files:

| Generated file | Consumed by | Selector |
|---|---|---|
| `static/css/tokens.css` | Web app | `:root { --wp-*: ... }` |
| `miniprogram/styles/tokens.wxss` | Mini program (`@import` in `app.wxss`) | `page { --wp-*: ... }` |

Because both come from the same JSON, the two surfaces are guaranteed identical.

**Workflow to change any visual value:**

```bash
# 1. edit static/design/tokens.json
# 2. regenerate
python scripts/build_design_tokens.py
# 3. verify no drift (this is what CI runs)
python scripts/build_design_tokens.py --check
```

> Never hand-edit `tokens.css` or `tokens.wxss`. They are generated and the drift
> check will fail CI if they are out of sync with the JSON.

### Token categories

| Category | Prefix | Examples |
|---|---|---|
| Colour | `--wp-color-*` | `--wp-color-primary`, `--wp-color-text-muted`, `--wp-color-danger-subtle` |
| Typography | `--wp-font-*` | `--wp-font-size-base`, `--wp-font-weight-semibold`, `--wp-font-family-base` |
| Spacing | `--wp-space-*` | `--wp-space-2` (8px) … `--wp-space-6` (24px) |
| Radius | `--wp-radius-*` | `--wp-radius-sm` (6px), `--wp-radius-md` (10px), `--wp-radius-pill` |
| Shadow | `--wp-shadow-*` | `--wp-shadow-1` … `--wp-shadow-3` |
| Z-index | `--wp-z-*` | `--wp-z-sticky`, `--wp-z-modal` |
| Controls | `--wp-control-*` | `--wp-control-height-md` (40px), `--wp-control-touch-target` (44px) |
| Motion | `--wp-motion-*` | `--wp-motion-duration-base`, `--wp-motion-easing-standard` |
| Navigation | `--wp-nav-*` | `--wp-nav-height`, `--wp-nav-bg`, `--wp-nav-accent` |

---

## 3. Colour

Light theme only (dark mode is out of scope but tokens are structured so a
`[data-theme="dark"]` block can be added later without renaming anything).

| Role | Token | Value | Use |
|---|---|---|---|
| App canvas | `--wp-color-bg` | `#F6F7F9` | Page background. **Replaces the old purple gradient.** |
| Surface | `--wp-color-surface` | `#FFFFFF` | Cards, tables, nav, modals. |
| Border | `--wp-color-border` | `#E3E6EA` | Card/table/input outlines. |
| Text | `--wp-color-text` | `#1A1D21` | Primary copy (AA on surface & canvas). |
| Muted text | `--wp-color-text-muted` | `#5B6472` | Secondary copy (AA on surface). |
| Primary | `--wp-color-primary` | `#2F6FED` | The one accent: primary buttons, active nav, links. |
| Success / Warning / Danger / Info | `--wp-color-{success,warning,danger,info}` | see tokens | Status only, each with a `-subtle` background variant. |

**Contrast rules (AA minimum):**

- Body text ≥ 4.5:1 against its background.
- Large text (≥ 18.66px bold or 24px) and non-text UI (icons, borders on controls) ≥ 3:1.
- Never place text or a control on a photographic/gradient background.

---

## 4. Typography

- Family: `--wp-font-family-base` (system stack: SF Pro Text / Segoe UI / Roboto /
  PingFang SC / Microsoft YaHei) so both Latin and CJK render natively.
- Scale: `--wp-font-size-xs` (12px) → `--wp-font-size-3xl` (34px). Body copy is
  `--wp-font-size-base` (14px) on web, matching the mini program.
- Weights: regular 400, medium 500, semibold 600, bold 700. Use weight — not size
  alone — to build hierarchy (Jira/ServiceNow pattern).
- Line height: `--wp-font-line-base` (1.5) for reading, `-tight` (1.25) for headings.

---

## 5. Spacing, radius, elevation

- **Spacing** uses the 4px grid (`--wp-space-*`). Prefer the scale over arbitrary
  values; `16px` and `24px` are the workhorses for card padding and section gaps.
- **Radius**: cards/popovers `--wp-radius-lg` (14px), buttons/inputs
  `--wp-radius-md` (10px), pills/badges `--wp-radius-pill`.
- **Elevation**: three shadow levels. Cards use `--wp-shadow-1`; floating menus use
  `--wp-shadow-2`; modals use `--wp-shadow-3`. Do not invent new shadows.

---

## 6. Components

The shared component classes live in [`static/css/waypost.css`](../static/css/waypost.css)
and layer on top of Bootstrap 5.3. Prefer these classes (or the Bootstrap classes
they restyle) over per-page CSS.

### 6.1 Buttons

The button matrix is the fix for the historical "invisible button" bug. Every
button state is defined so it always has sufficient contrast on a light canvas.

| Variant | Class | Fill | Text | When |
|---|---|---|---|---|
| Primary | `.btn-primary` / `.wp-btn--primary` | `--wp-color-primary` | white | The one main action per view. |
| Secondary | `.btn-secondary` / `.wp-btn--secondary` | surface + `--wp-color-border` | `--wp-color-text` | Alternative actions. **Never transparent-only.** |
| Ghost / tertiary | `.wp-btn--ghost` | transparent | primary | Low-emphasis, inline with content. Shows a subtle fill on hover. |
| Danger | `.btn-danger` / `.wp-btn--danger` | `--wp-color-danger` | white | Destructive actions. |
| Success | `.btn-success` | `--wp-color-success` | white | Positive confirmations. |

Rules:

- Minimum control height `--wp-control-height-md` (40px); touch targets ≥ 44px.
- **Disabled** = neutral fill (`--wp-color-neutral-200`) + neutral text
  (`--wp-color-neutral-500`) + `cursor: not-allowed` at **`opacity: 1`**. Never
  signal "disabled" by dropping opacity below 1 — that is what made buttons
  unreadable.
- **Never** render an outline/ghost button directly on a coloured or gradient
  background. On the light canvas, secondary buttons always have a border and
  body-colour text, so they stay visible everywhere.
- Every button shows a `:focus-visible` ring using `--wp-color-focus-ring`.

### 6.2 Navigation (top bar)

- Single-row sticky bar, height `--wp-nav-height` (60px), `flex-wrap: nowrap` —
  **it must never wrap to a second line**.
- Overflow uses the **priority+** pattern (Apple/Jira): items that do not fit are
  moved into a "More" (…) dropdown by [`static/js/nav-overflow.js`](../static/js/nav-overflow.js).
  Below the `lg` breakpoint everything collapses into the hamburger menu.
- Active section is marked with `--wp-nav-accent` (colour + a 2px underline), not
  by a heavy background.

### 6.3 Cards, tables, forms, badges, alerts, empty states, pagination

- **Cards**: surface bg, `--wp-radius-lg`, `--wp-shadow-1`, `--wp-space-6` padding.
- **Tables**: header row on `--wp-color-surface-sunken`, `--wp-color-border` row
  separators, `--wp-space-3` cell padding (dense, per Jira/ServiceNow). Row hover
  uses `--wp-color-surface-subtle`.
- **Forms**: inputs use surface bg, `--wp-color-border`, `--wp-radius-md`; focus
  swaps the border to `--wp-color-primary` + focus ring. Labels are
  `--wp-font-size-sm` muted.
- **Badges / status pills**: subtle background + strong text from the matching
  semantic pair (e.g. `--wp-color-success-subtle` + `--wp-color-success`).
- **Alerts**: subtle semantic background, `--wp-radius-md`, leading icon.
- **Empty states**: centred icon + heading + one primary action.
- **Pagination**: compact, uses secondary button styling.

Reusable Django partials live in `templates/base/partials/`:
`_page_header.html`, `_toolbar.html`, `_empty_state.html`, `_pagination.html`.
Use them instead of re-inventing page headers.

---

## 7. WeChat mini program parity

- `app.wxss` `@import`s `styles/tokens.wxss` and maps every shared class
  (`.card`, `.btn`, `.tag`, `.row`, `.title`, `.muted`, `.field`, `.input`) onto
  tokens. It must not hardcode colours.
- `app.json` window colours (`navigationBarBackgroundColor`, `backgroundColor`)
  must equal the corresponding token values; CI verifies this.
- The mini program uses **px** (not `rpx`) for token-driven dimensions so it
  matches the web pixel-for-pixel; layout stays responsive with flexbox and
  percentage widths.

---

## 8. Accessibility checklist

- [ ] Text and controls meet WCAG 2.1 AA contrast (4.5:1 body, 3:1 large/UI).
- [ ] Touch targets ≥ 44px; form controls ≥ 40px tall.
- [ ] Every interactive element has a visible `:focus-visible` state.
- [ ] Meaning is never conveyed by colour alone (pair with an icon or text).
- [ ] No essential control is hidden until hover or dimmed with `opacity < 1`.

---

## 9. Hard rules (enforced in CI)

These are checked by `tests/test_design_system.py` (runs in the required backend
CI job) and by mini program `stylelint`:

1. **No raw colour literals** in the CSS of any web template (`<style>` blocks
   and `style=""` attributes), in `static/css/*.css`, or in `*.wxss` — outside the
   generated token files. Use `var(--wp-*)`. Chart/JS colours inside `<script>`
   are exempt (canvas cannot read CSS variables). Enforced by
   `test_no_raw_hex_in_template_css` and `test_core_files_are_hex_free`.
2. **No token drift** — generated `tokens.css` / `tokens.wxss` / `app.json` window
   colours must match `tokens.json`.
3. **No opacity-hidden controls** — `.btn` / `.wp-btn` must not be styled with
   `opacity: 0` (or hidden-until-hover) for essential actions.
4. **No outline-on-gradient** — page-header partials must not use `btn-outline-*`,
   and chrome must not use decorative `linear-gradient(...)` backgrounds.
5. **Single-row nav** — the top bar must not wrap to two lines (priority+ overflow).

If a rule blocks legitimate work, raise it and update this document plus the check
together — do not silently bypass it.

---

## 10. Contributing UI

1. Read this document and reuse tokens/components before writing new CSS.
2. New visual value → add it to `tokens.json`, regenerate, use the token.
3. Verify locally at 360px and 1440px+, and check the mini program if it touches
   shared surfaces.
4. Complete the "Design System Conformance" section of the PR template and attach
   before/after screenshots.
