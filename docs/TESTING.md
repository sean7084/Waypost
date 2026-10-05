# Testing Guide - Waypost

**Status:** strategy + tooling foundation. This document defines how to run tests, the
conventions the existing suite follows, the **measured** coverage baseline, and a ratchet plan.
Writing the missing tests is tracked as separate work (see §8 priorities).

All numbers below were measured on 2026-09-15 against `main` with the full suite.

---

## 1. Measured baseline

**79 tests, all passing** (1 skipped — env-gated), **43.7% overall coverage**
(13,225 statements + 3,290 branches).

| App | Tests | Coverage | Statements | Notes |
|---|---|---|---|---|
| `inspections` | 33 | **84.4%** | 1,367 | Newest code; models 97.9%, report/transform parity suite |
| `api` | 18 | **73.8%** | 817 | `inspection_views.py` 82.2%; DRF `APIClient` throughout |
| `audit` | 0 | 67.7% | 438 | Incidental coverage only — no dedicated tests |
| `products` | 7 | 57.6% | 973 | Price lifecycle covered |
| `reports` | 0 | 46.3% | 462 | Incidental only |
| `accounts` | 12 | 40.3% | 2,599 | Largest app. `mailbox_sync.py` **13.4%** |
| `purchases` | 0 | 38.9% | 284 | No test module |
| `quotations` | 8 | 33.7% | 1,070 | `views.py` 584 stmts largely untested |
| `utils` | 0 | 32.7% | 119 | Import rollback — safety-critical, untested directly |
| `companies` | 0 | 31.8% | 1,199 | Zero tests, 1,344-line `views.py` |
| `dashboard` | 0 | 30.9% | 128 | |
| `invoices` | 1 | 27.9% | 1,045 | One test for a 417-line views module |
| `assets` | 0 | **23.9%** | 2,046 | **Worst risk**: 2,230-line `views.py`, no tests |
| `deliveries` | 0 | **21.5%** | 489 | No test module at all |
| `customers` | 0 | 0.0% | 2 | Model-free app |
| `mobile`, `users` | 0 | 62.5% / 87.5% | 24 / 8 | Too small to matter |

### Reading coverage numbers honestly

`assets/views.py` reports **37.6%** statement coverage despite having **zero tests**. That is
not real behavioural coverage: class bodies, `def` statements and decorators all execute at
*import* time, so any test run that imports the module "covers" them. Treat a module with no
dedicated tests as ~0% behaviourally covered regardless of the reported figure, and prefer
branch coverage (`branch = true` is on) as the more honest signal.

---

## 2. Running tests

Install the tooling once (production installs `requirements.txt` only):

```bash
pip install -r requirements-dev.txt
```

### Canonical runner — Django

This is what CI uses and what the whole existing suite is written for:

```bash
python manage.py test                          # everything (79 tests, ~17s)
python manage.py test inspections              # one app
python manage.py test api.tests_miniprogram    # one module
python manage.py test inspections.tests.TransformTests.test_monitor_size_marker   # one test
python manage.py test --keepdb                 # reuse the test DB between runs (much faster)
python manage.py test -v 2                     # per-test names
```

### Equivalent runner — pytest

```bash
python -m pytest                       # same 79 tests
python -m pytest -k "idempotent"       # select by name
python -m pytest --lf                  # re-run only last failures
python -m pytest inspections/tests.py  # one file
```

> ⚠️ **Do not remove `-p waypost.pytest_bootstrap` from `addopts` in `pyproject.toml`.**
> pytest-django calls `django.setup()` inside the `pytest_load_initial_conftests` hook, which
> runs *before* a root `conftest.py` is imported. `manage.py` performs three bootstrap calls
> (`load_local_env`, `configure_windows_weasyprint_runtime`, `configure_windows_fontconfig`)
> before Django loads; without them, importing an app that pulls in WeasyPrint dies on Windows
> with `OSError: cannot load library 'gobject-2.0-0'`. The plugin module is loaded via `-p`,
> which pytest resolves earlier than initial conftests, so it can perform the same bootstrap in
> time. All three helpers are no-ops on Linux/macOS, so CI is unaffected.

### Coverage

```bash
python -m coverage run manage.py test      # or: python -m coverage run -m pytest
python -m coverage report                  # per-file + TOTAL; enforces fail_under = 43
python -m coverage report --include="assets/*"     # one app
python -m coverage html                    # browsable report in htmlcov/
python -m coverage erase                   # reset the .coverage data file
```

Configuration lives in `pyproject.toml` (`[tool.coverage.run]` / `[tool.coverage.report]`):
`branch = true`, migrations / `node_modules` / `media` / `scripts` / WSGI-ASGI entrypoints
omitted, and `fail_under = 43` as the ratchet floor.

---

## 3. Conventions the existing suite follows

Match these when adding tests — consistency matters more than novelty.

1. **`django.test.TestCase`**, not `unittest` or bare pytest functions. Every existing test
   class subclasses `TestCase` (transactional rollback per test). Use `SimpleTestCase` only
   for pure-logic tests that touch no DB.
2. **One `tests.py` per app** (or `api/tests_<area>.py` where an app has several concerns).
   Group into `class <Area>Tests(TestCase)`.
3. **DRF API tests use `rest_framework.test.APIClient`** with `force_authenticate(user)` —
   see `api/tests_miniprogram.py`. Assert on `res.status_code` and pass `res.content` as the
   assertion message so failures show the response body.
4. **Never hit the network.** External calls are mocked at the module boundary, e.g.
   `@mock.patch('api.views_auth.code2session', return_value=_FAKE_SESSION)`. The WeChat
   `session_key` is asserted to be *not* persisted.
5. **Redirect file storage away from the repo.** Media-writing tests use a temp dir:
   `_TEMP_MEDIA = tempfile.mkdtemp(...)` plus `@override_settings(MEDIA_ROOT=_TEMP_MEDIA)`.
   Never write into `media/`.
6. **Real images, not fake bytes.** Photo tests build an actual PNG via
   `PIL.Image.new(...).save(buffer, format='PNG')` wrapped in `SimpleUploadedFile`, so
   ImageField validation really runs.
7. **Shared setup goes in a mixin or module helper**, not copy-paste — e.g.
   `InspectionFixtureMixin` in `inspections/tests.py`, `_make_engineer()` /
   `_make_inspection()` in `api/tests_miniprogram.py`.
8. **Expensive external-data tests are env-gated and skipped by default.**
   `inspections.tests.RealDataIntegrationTests` skips unless `INSPECTION_REAL_DATA_TEST=1` and
   the real Kering files are present; it imports 126 stores and then rolls the run back.
9. **Regression tests are named for the bug**, and the commit message explains the real data
   that exposed it — e.g. `test_brand_case_variants_share_one_brand`,
   `test_blank_warranty_nat_imports_as_null`.
10. **Assert idempotency explicitly** where the system promises it: run the operation twice and
    assert the row count did not change (`test_device_upsert_is_idempotent_by_client_uid`,
    `test_reuse_is_idempotent`).
11. **Golden-output parity over heuristic assertions.** Where a deliverable must match a legacy
    artifact, compare against the real generated file (`ReportGoldenParityTests`) rather than
    asserting plausible-looking values. This is how two real defects were caught.
12. **No test artifacts in the repo.** Temp scripts and dumps go under `logs/` (gitignored) and
    are deleted afterwards; `.coverage`, `htmlcov/` and `.pytest_cache/` are gitignored.

---

## 4. Factories

`factory-boy` is installed but **no factories exist yet** — the current suite builds objects with
direct `Model.objects.create(...)` calls and small helper functions, which is fine and should not
be rewritten wholesale. Introduce factories where a test needs the same multi-model graph
repeatedly. Suggested first ones (field names verified against the models):

```python
# companies/factories.py  (not yet in the repo - proposed starting point)
import factory
from .models import Company, Location


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Company

    name = factory.Sequence(lambda n: f"Kering {n}")     # unique
    code = factory.Sequence(lambda n: f"KER{n:04d}")     # unique


class LocationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Location

    company = factory.SubFactory(CompanyFactory)
    name = factory.Sequence(lambda n: f"Store {n}")
    code = factory.Sequence(lambda n: f"22{n:03d}")      # unique per (company, code)
```

Rules: keep factories in `<app>/factories.py`, use `factory.Sequence` for every unique business
key (see the unique-key table in [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md)), and prefer
`build()` over `create()` when the test does not need a DB row.

---

## 5. Coverage bar and ratchet plan

**Current floor: `fail_under = 40`** in `pyproject.toml` (measured 43.7%).

The ratchet works one way:

1. Run `coverage report --format=total` before opening a PR.
2. If it exceeds the current `fail_under`, raise `fail_under` to the new whole-number floor in
   the same PR.
3. Never lower it. If a legitimate refactor drops coverage, add the tests in the same PR.

Milestones (each is a separate, reviewable PR):

| Milestone | Target | What it takes |
|---|---|---|
| M1 | `assets` ≥ 45%, overall ≥ 48% | Smoke tests for the asset CRUD + assign/return views; import/rollback round-trip |
| M2 | `deliveries` ≥ 50%, `invoices` ≥ 45% | Dispatch/complete transitions, POD upload, batch import + recalculate |
| M3 | `companies` ≥ 50%, `utils` ≥ 70% | Company/location/contact CRUD, CSV import + rollback ledger replay |
| M4 | `quotations` ≥ 55%, overall ≥ 60% | confirm / cancel / send / convert-to-purchase transitions |
| M5 | `accounts` ≥ 55% | Mailbox sync (mocked IMAP), RFQ classification (mocked Minimax), 2FA enrolment |

**Per-app minimum for new code:** any newly added app or service module ships with tests and is
expected to be ≥ 80% — that is the standard `inspections` (84.4%) and `api` (73.8%) already meet.
Do not let a new module land at the project average.

---

## 6. CI

`.github/workflows/backend-ci.yml` runs on **every** push to `main` and on **every** pull
request. It has no path filter on purpose: its job (`Django checks + tests`) is a *required
status check* on `main`, and a required check that never runs would block a docs-only PR forever.

Steps:

1. Service containers: **PostgreSQL 16** and **Redis 7** — mirroring production instead of the
   SQLite / local-memory dev defaults
2. `actions/setup-python` (3.12) + WeasyPrint system libraries
3. `pip install -r requirements-dev.txt` (pulls in `requirements.txt` plus pytest/coverage)
4. `python manage.py check`
5. `python manage.py makemigrations --check --dry-run`
6. `python -m coverage run manage.py test`, then `python -m coverage report` — the report
   **enforces `fail_under`**, so a coverage regression fails the build

`DJANGO_DEBUG=True` is set in the workflow so the production fail-fast guards (secret key, field
encryption key, WeChat credentials) do not block CI, and the `DATABASE_*` /
`DJANGO_REDIS_CACHE_URL` env vars point at the service containers.

The same workflow has a second, parallel job — **`Python lint (ruff)`** — running `ruff check .`
with the correctness-only rule set defined under `[tool.ruff]` in `pyproject.toml`. Both jobs are
required status checks on `main`. Run it locally with:

```bash
python -m ruff check .          # must pass before pushing
python -m ruff check --fix .    # apply the safe autofixes
```

`.github/workflows/miniprogram-ci.yml` runs eslint over `miniprogram/**`. It *is* path-filtered
and therefore deliberately **not** a required check.

The branch-protection wiring lives in `scripts/setup-branch-protection.ps1`
(`required_status_checks.contexts = ["Django checks + tests"]`, `strict = true`); the rationale
is recorded in [`GITHUB_SETTINGS.md`](GITHUB_SETTINGS.md).

> 🐛 **Fresh-checkout gotcha (fixed):** `logs/` is gitignored but `LOGGING`'s file handler points
> into it, so every `manage.py` command used to crash on a clean checkout with
> `ValueError: Unable to configure handler 'file'`. `settings.py` now creates `LOG_DIR` before
> `LOGGING` is consumed (issue #66). If CI ever fails at the *system checks* step with that
> error, that bootstrap has regressed.

---

## 7. What to prioritise, and why

Ranked by risk × size, not by ease:

1. **`assets/views.py`** — 1,057 statements, 23.9% app coverage, **no tests**. It is the largest
   module in the project and holds the import + rollback flows, where a regression silently
   corrupts the asset register.
2. **`deliveries`** — no test module at all, and it owns the dispatch/complete state machine plus
   the `unique_asset_per_delivery_order` invariant.
3. **`utils/import_rollback.py`** — small (119 statements, 32.7%) but *safety-critical*: it is the
   only thing standing between a bad bulk import and manual DB surgery. Highest value per test
   written.
4. **`invoices`** — 1 test for 1,045 statements; the SharePoint batch import and the
   client-confirmed → Esker lifecycle are money paths.
5. **`quotations`** — the order-to-cash hub (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §4);
   confirm/cancel/send/convert transitions deserve explicit state-machine tests.
6. **`accounts/mailbox_sync.py`** — 13.4%. Mock IMAP; verify the
   `(mailbox, direction, external_id)` uniqueness makes sync idempotent.

Prefer **behavioural** tests (post a request, assert the DB state and the side effect) over
coverage-driven tests that merely import modules.

---

## 8. Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — bounded contexts and the flows these tests should cover
- [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md) — unique business keys (what factories must vary)
- [`WEB_ROUTES.md`](WEB_ROUTES.md) — the 164 HTML routes and the capability each one requires
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch, commit and PR conventions
- [`DEPLOYMENT.md`](DEPLOYMENT.md) §Step 4b — the gitignored `template_files/` that document
  generation needs (absent in CI; no current test depends on it)

---

*Last Updated: September 15, 2026*
