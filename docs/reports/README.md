# `docs/reports/` — point-in-time delivery reports

This folder is the **only** home for **Waypost project** delivery reports, audits and gap analyses.

Scope boundary: infrastructure and cloud-account material (ECS, RDS, OSS, CDN, DNS, RAM,
certificates, snapshots, bandwidth, procurement and costing) belongs to the **Aliyun ops knowledge
base**, not here — that base covers the host and the account and is shared with the Helpdesk
tenant on the same ECS. A report that is half procurement and half code change gets **split**:
the costing stays in the ops KB, the code-level blockers and effort estimates come here, and the
two link to each other. `GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md` is exactly that case.

> ⚠️ `reports/` at the repository root is the Django **`reports` app** (`models.py`, `views.py`,
> `urls.py`, `migrations/`). It is not a documentation folder and must contain only Python.
> Five reports had already been dropped in there before they were moved here on 2026-10-13.
> [`tests/test_repo_hygiene.py`](../../tests/test_repo_hygiene.py) enforces this in the required
> `manage.py test` CI gate.

## Layout

```
docs/reports/
├── <NAME>_<YYYYMMDD>.md   # the report itself, e.g. DOCUMENTATION_GAP_ANALYSIS_20260906.md
├── canvas/                # .canvas.tsx visual companions (Canvas preview artifacts)
└── <report-slug>/         # evidence assets for one report, e.g. inspections-iteration-2/*.png
```

## Conventions

1. **Naming** — shout the document type and pin the date: `<NAME>_<YYYYMMDD>.md`. The hygiene test
   matches `REPORT`, `AUDIT` and `GAP_ANALYSIS` **case-sensitively**, so a file using one of those
   words in caps must live in this folder. (This is why `.github/ISSUE_TEMPLATE/bug_report.md`,
   lowercase, is exempt and stays where it is.)
2. **Catalogue it** — add a row to [`docs/INDEX.md`](../INDEX.md) → “Completion & Audit Reports”,
   newest first, with the `docs/reports/` location and a one-line purpose.
3. **Evidence assets get a subfolder** — do not scatter screenshots next to the report; use
   `<report-slug>/` as `inspections-iteration-2/` does.
4. **Canvas companions go in `canvas/`** — a `.canvas.tsx` is the visual summary of a report and is
   version-controlled beside it. Note the trade-off: the IDE Canvas preview panel discovers canvases
   under `~/.qoder/projects/<workspace>/canvases/`, so a copy kept here is a *durable, reviewable
   artifact* rather than a live-preview file. If you want it to render in the panel, keep the
   working copy there and sync the finished version into `canvas/`.
5. **Reports are historical** — they are point-in-time snapshots. Do not rewrite one to reflect
   later changes; write a new report and link the old one, the way the ops knowledge base appends
   corrections to its CHANGELOG instead of editing history.
6. **Moving a report means re-anchoring its links.** Relative links resolve from the *containing
   file's* directory, so a move silently breaks every link inside the document — when these five
   reports moved from `reports/` to `docs/reports/`, their `../docs/X.md` links became
   `docs/docs/X.md`. Nothing in the file's own content changes, which is why it goes unnoticed.
   `tests/test_repo_hygiene.py::MarkdownLinkIntegrityTests` scans every relative Markdown link in
   the repo and fails CI on a broken one; also check whatever links *to* the moved file.

## Current contents

| Report | Date | Subject |
| --- | --- | --- |
| `GO_LIVE_MIGRATION_BLOCKERS_REPORT_20261013.md` | 2026-10-13 | Code-level blockers and effort for the pre-go-live OSS + RDS migration (application half of the ops KB cost estimate) |
| `INSPECTIONS_ITERATION2_COMPLETION_REPORT_20260924.md` | 2026-09-24 | Inspections iteration 2 delivery |
| `MINIPROGRAM_SPEC_COMPLETION_REPORT_20260914.md` | 2026-09-14 | WeChat mini program spec completion |
| `DOCUMENTATION_GAP_ANALYSIS_20260906.md` | 2026-09-06 | Documentation gap analysis |
| `REPO_DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md` | 2026-09-06 | This repo's documentation vs its implementation — renamed 2026-10-13; the ops KB holds a same-dated `KB_DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md` auditing a different corpus |
| `GITHUB_LABELS_SETUP_REPORT.md` | 2026-08-24 | GitHub label taxonomy setup |
| `FINAL_GITHUB_CONFIG_REPORT.md` | 2026-08-24 | GitHub repository configuration status |

| Canvas | Companion to |
| --- | --- |
| `canvas/wechat-miniprogram-spec-completion.canvas.tsx` | `MINIPROGRAM_SPEC_COMPLETION_REPORT_20260914.md` |
