# Inspections Web Frontend — Verification Report

Browser-verification evidence for the inspections web frontend delivered in
[PR #80](https://github.com/sean7084/Waypost/pull/80)
(closes [#78](https://github.com/sean7084/Waypost/issues/78) and
[#79](https://github.com/sean7084/Waypost/issues/79)).

## How verification was performed

- A throwaway SQLite database (`DATABASE_NAME=verify_db.sqlite3`) was migrated from
  the current models and seeded with one batch, 12 store inspections spread across
  the month, and 24 devices (half collected, half untouched).
- The dev server was run against that database and driven in a real browser as a
  superuser; every page below was loaded and screenshotted.
- The asset-list export was exercised end-to-end: selecting the batch and clicking
  **Download XLSX** produced a real `.xlsx` download.
- Automated coverage lives in `inspections/tests_frontend.py` (25 tests); the full
  suite (115 tests) passes and `manage.py check` / `makemigrations --check` are clean.
- The throwaway database and temporary superuser were removed afterwards; the
  production `db.sqlite3` was never modified.

## Screenshots

| File | What it shows |
| --- | --- |
| `01-dashboard-calendar.png` | `/inspections/` FullCalendar month view: status-coloured inspection events, summary tiles (Total / Planned / In Progress / Completed / Submitted), legend, and the batch selector used to scope to the current batch. |
| `02-batch-list.png` | `/inspections/batches/` list with the created batch, its date range, source and inspection count. |
| `03-batch-detail.png` | Batch detail: per-store inspection table with dates, status badges and device-collection progress bars. |
| `04-batch-create-landing.png` | `/inspections/batches/new/` landing page offering the two creation flows. |
| `05-batch-create-by-brand.png` | By-brand flow: brand + date-range + engineer form (stores auto-spread round-robin). |
| `06-batch-import-schedule.png` | Schedule-upload flow: schedule.xlsx (+ optional asset list) with the expected-column reference. |
| `07-inspection-list.png` | `/inspections/list/` filterable inspection list (search, batch, brand, status, engineer, date range). |
| `08-inspection-detail.png` | Read-only inspection detail: cover-page fields, devices table, status badges. |
| `09-export-form.png` | `/inspections/export/` filter form (batch / brand / date range / ISO week) plus the 17 GUCCI asset-table columns and filename rules. |
| `10-export-download.png` | Export executed: batch selected and **Download XLSX** clicked, producing `Gucci Verification Batch.xlsx`. |
| `11-nav-inspections-dropdown.png` | Site navigation with the role-gated **Inspections** dropdown (Dashboard, Batches, New Batch, All Inspections, Export Asset List). |

## Notes

- Screenshots were captured against seeded demo data; store names / JDA codes /
  dates are illustrative, not client production data.
- The audit-module retirement (issue #79) is behavioural rather than visual; its
  evidence is the passing test suite and the removed `audit` app, not screenshots.
