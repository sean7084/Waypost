# Testing Guide - Waypost

**Status:** strategy + tooling foundation. This document defines how to run tests, the
conventions the existing suite follows, the **measured** coverage baseline, and a ratchet plan.
Writing the missing tests is tracked as separate work (see §8 priorities).

All numbers below were measured on 2026-10-22 against `chore/coverage-ratchet-and-report-correction`
with the full suite. The previous version of this table (2026-09-15, 79 tests, 43.7%) is kept in
issue #71's body; the figures below replace it.

---

## 1. Measured baseline

**375 tests, all passing** (1 skipped — env-gated), **59.3% overall coverage**
(17,987 statements + 4,162 branches, branch coverage on).

| App | Tests | Coverage | Statements | Notes |
|---|---|---|---|---|
| `tests/` (harness) | 61 | **92.6%** | 795 | Storage-API guards, hygiene tripwires, deploy-script checks |
| `inspections` | 167 | **87.9%** | 4,138 | Newest code; models, import scheduling, duplicate-inspection invariant |
| `users` | 0 | 87.5% | 8 | Too small to matter |
| `waypost` | 12 | 83.6% | 450 | Settings, health endpoint, URL surface |
| `api` | 27 | **72.4%** | 1,046 | Mini-program + company API permission tests |
| `mobile`, `customers` | 0 | 62.5% / 0.0% | 24 / 2 | Model-free apps |
| `accounts` | 83 | **61.6%** | 3,640 | Was 40.3%. RFQ/LLM metering and crypto-logging tests |
| `reports` | 0 | 57.9% | 519 | Incidental coverage only — no dedicated tests |
| `products` | 7 | 57.6% | 973 | Price lifecycle covered |
| `utils` | 0 | **50.6%** | 177 | Was 32.7% — still no direct tests; `import_rollback.py` is 40.8% |
| `dashboard` | 0 | 41.4% | 128 | Covered from `inspections` view tests |
| `invoices` | 1 | 40.5% | 1,044 | Was 27.9%; one test for a 291-statement `views.py` at 22.8% |
| `purchases` | 0 | 38.9% | 284 | No test module |
| `quotations` | 8 | 35.9% | 994 | `views.py` 584 stmts at **11.1%** |
| `companies` | 0 | 32.5% | 1,199 | Zero own tests; `views.py` 661 stmts at 20.9% |
| `deliveries` | 0 | 31.4% | 467 | No test module; `views.py` 175 stmts at **14.5%** |
| `assets` | 0 | **25.3%** | 2,099 | **Worst risk, unchanged in kind**: `views.py` 1,054 stmts at 15.5% |

`audit` is gone from this table: the app now contains only `migrations/`, which
`[tool.coverage.run]` omits, so it has no measurable code of its own.

### Reading coverage numbers honestly

`assets/models.py` reports **61.8%** coverage and `invoices/models.py` **84.3%** while the apps that
contain them have **zero and one** tests respectively. That is not behavioural coverage: class
bodies, `def` statements and decorators all execute at *import* time, so any test run that imports
the module "covers" them. Treat a module with no dedicated tests as ~0% behaviourally covered
regardless of the reported figure, and prefer branch coverage (`branch = true` is on) as the more
honest signal.

Three consequences of how this project measures, all of which make the headline number softer than
it looks:

1. **`source = ["."]` counts the test files themselves.** A `tests_*.py` module is nearly fully
   covered by definition, so every test written raises the total twice - once by covering product
   code, once by adding covered statements of its own. The `tests/` harness row (61 tests, 92.6%)
   is in the total for exactly this reason. Compare per-app *behavioural* claims, not the headline.
2. **Much of the movement since 2026-09-15 is incidental.** `deliveries` went 21.5% → 31.4% and
   `dashboard` 30.9% → 41.4% with **no new tests in either app**: the inspection view and API tests
   import and exercise their modules in passing. `assets` - the stated worst risk - moved 23.9% →
   25.3% and still has no test module, which is the honest answer to "did the coverage work fix the
   risk?".
3. **The floor is enforced on PostgreSQL, the table above is measured on SQLite.** The one matched
   pair available (2026-09-15, 79 tests) was **43.7% local / 43.0% CI**, so the backend delta is
   about 0.7 points; CI skipped 3 tests there against 1 locally (`real_data`-gated). Nothing else
   about the offset is measured, which is why §5 keeps a 2-point margin below the local figure
   rather than setting the floor at the number in the table.

---

## 2. Running tests

Install the tooling once (production installs `requirements.txt` only):

```bash
pip install -r requirements-dev.txt
```

### Canonical runner — Django

This is what CI uses and what the whole existing suite is written for:

```bash
python manage.py test                          # everything (375 tests, ~63s)
python manage.py test inspections              # one app
python manage.py test api.tests_miniprogram    # one module
python manage.py test inspections.tests.TransformTests.test_monitor_size_marker   # one test
python manage.py test --keepdb                 # reuse the test DB between runs (much faster)
python manage.py test -v 2                     # per-test names
```

### Equivalent runner — pytest

```bash
python -m pytest                       # same 375 tests (verified: collection reports 375)
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
python -m coverage report                  # per-file + TOTAL; enforces fail_under = 57
python -m coverage report --include="assets/*"     # one app
python -m coverage html                    # browsable report in htmlcov/
python -m coverage json -o cov.json        # machine-readable, for per-app rollups
python -m coverage erase                   # reset the .coverage data file
```

Configuration lives in `pyproject.toml` (`[tool.coverage.run]` / `[tool.coverage.report]`):
`branch = true`, migrations / `node_modules` / `media` / `scripts` / WSGI-ASGI entrypoints
omitted, and `fail_under = 57` as the ratchet floor.

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

**Current floor: `fail_under = 57`** in `pyproject.toml` (measured 59.3% on 2026-10-22; raised from
40 on the same date as the ratchet step, in its own commit so a coverage change is revertible
without reverting anything else).

The ratchet works one way:

1. Run `coverage report --format=total` before opening a PR.
2. If it exceeds the current `fail_under`, raise `fail_under` to the new whole-number floor in
   the same PR.
3. Never lower it. If a legitimate refactor drops coverage, add the tests in the same PR.

Step 2 is what produced 57: `floor(59.3) − 2`. The 2 is not decoration - CI enforces this number on
PostgreSQL while the table above is measured on SQLite, and the only matched pair available
(2026-09-15: 43.7% local against 43.0% on CI) says the delta is ~0.7 points. If the next
`backend-ci` run on this branch reports below 57, the floor was a bet on that delta and should be
corrected by the same rule, not by editing this sentence.

Milestones, restated per **module** rather than per app: the 2026-09-15 app-level targets were
partly satisfied by import-time and incidental coverage, which is not the thing anyone meant.
`accounts` ≥ 55% was written as a mailbox-sync/2FA milestone and is already true at 61.6% while
`mailbox_sync.py` sits at 19.0%.

| Milestone | Target (module) | Where it is now | What it takes |
|---|---|---|---|
| M1 | `assets/views.py` | **15.5%** of 1,054 stmts, 0 tests | Asset CRUD + assign/return smoke tests; import/rollback round-trip |
| M2 | `deliveries/views.py` | **14.5%** of 175 stmts, no test module | Dispatch/complete transitions, POD upload |
| M3 | `utils/import_rollback.py` | 40.8% of 85 stmts, no direct tests | CSV import + rollback ledger replay (§7.3 has the worked example) |
| M4 | `companies/views.py` | 20.9% of 661 stmts, 0 own tests | Company/location/contact CRUD |
| M5 | `quotations/views.py` | **11.1%** of 584 stmts | confirm / cancel / send / convert-to-purchase transitions |
| M6 | `accounts/mailbox_sync.py` | 19.0% | Mocked IMAP sync; assert the idempotency key |

The two `overall ≥ 48%` / `overall ≥ 60%` gates that used to sit in this table are **dropped**: the
first is long past, and the second is now within ~1 point of being reached without writing a single
test in the named apps (§1, caveat 2). An overall gate that moves on incidental coverage is not a
gate.

**Per-app minimum for new code:** any newly added app or service module ships with tests and is
expected to be ≥ 80% — that is the standard `inspections` (87.9%) and `api` (72.4%) already meet or
come closest to.
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

What that gate has actually reported, from the runs' own logs: **50.8%** at 221 tests
(2026-10-13), **51.1%** at 228 tests (2026-10-13), **51.0%** at 232 tests (2026-10-20). Those
figures are why the floor carries a margin instead of being set at §1's local number: CI runs a
different backend and skips two more env-gated tests, and its own history shows the metric moving
~0.1-0.3 points per merged PR once the suite stops growing.

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

1. **`assets/views.py`** — 1,054 statements at **15.5%**, **no tests**. It is the largest
   module in the project and holds the import + rollback flows, where a regression silently
   corrupts the asset register. Of the app's 2,099 statements only 650 execute at all, and the
   largest contributors are `models.py` (265) and `views.py`'s own `def`/class bodies (210) -
   import-time and cross-app incidental execution, not anything aimed at the asset flows.
2. **`deliveries`** — no test module at all, `views.py` at 14.5%, and it owns the
   dispatch/complete state machine plus the `unique_asset_per_delivery_order` invariant.
3. **`utils/import_rollback.py`** — small (85 statements, 40.8%) but *safety-critical*: it is the
   only thing standing between a bad bulk import and manual DB surgery. Highest value per test
   written. A worked example of the path already exists as
   [`../scripts/manual_verify_import_rollback.py`](../scripts/manual_verify_import_rollback.py): a
   `django.test.Client` script that uploads company, location, contact and asset CSVs through the real
   views, asserts the row counts come back after rollback, and deletes everything it created in a
   `finally`. Start the tests from it rather than re-deriving the fixtures — and keep it until they land.
4. **`invoices`** — 1 test for 1,044 statements, and `views.py` is at 22.8%; the SharePoint batch
   import and the client-confirmed → Esker lifecycle are money paths.
5. **`quotations`** — the order-to-cash hub (see [`ARCHITECTURE.md`](ARCHITECTURE.md) §4);
   `views.py` is 584 statements at **11.1%**, so the confirm/cancel/send/convert transitions have no
   explicit state-machine tests at all.
6. **`accounts/mailbox_sync.py`** — 19.0% (was 13.4%, and the app rose to 61.6% without it). Mock
   IMAP; verify the `(mailbox, direction, external_id)` uniqueness makes sync idempotent.

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

*Last Updated: October 22, 2026*
