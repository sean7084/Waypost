"""Repository-hygiene tests: keep documentation out of Django app directories.

These run inside the required backend CI job (`.github/workflows/backend-ci.yml`
-> `python manage.py test`), so they are a hard gate on every pull request.

The rule they encode, and the mistake that motivated it:

    `reports/` at the repository root is the Django **`reports` app**
    (`INSTALLED_APPS = [... 'reports' ...]`), not a folder for report documents.
    Five delivery/audit reports had accumulated inside it next to `models.py`
    and `views.py`, where they were invisible to `docs/INDEX.md` readers and one
    `manage.py`-adjacent glob away from being shipped in the Docker image.

So, as of 2026-10-13:

  * point-in-time delivery reports, audits and gap analyses live in
    `docs/reports/`;
  * their Canvas companions live in `docs/reports/canvas/`;
  * per-report evidence assets get a subfolder of `docs/reports/`
    (e.g. `docs/reports/inspections-iteration-2/`);
  * a Django app directory holds application code and the assets that code
    reads at runtime (e.g. `inspections/report_template.xlsx`) - never prose;
  * every relative Markdown link in the repository resolves. Moving a document
    silently breaks the links inside it, which is exactly what happened when the
    five reports moved from `reports/` to `docs/reports/` - `../docs/X.md` became
    `docs/docs/X.md`. A link check is the only thing that catches that.

The checks are static (walk the tree, match names) so they need no database and
run in a fraction of a second. They derive the app list from `INSTALLED_APPS`
rather than hardcoding it, so a newly added app is covered automatically.

The file has since become the home for every "the tree must not contain X" gate,
whatever X is: `MediaStorageApiTests` (no local filesystem media access, which a
bucket backend cannot serve) and `MiniprogramCompressionTests` (photo capture
must route through the compressing helper) both walk the tree the same way.
"""
import os
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent

# Where prose belongs.
DOCS_REPORTS_DIR = BASE_DIR / "docs" / "reports"
CANVAS_DIR = DOCS_REPORTS_DIR / "canvas"

# Mini program photo-capture surface (see MiniprogramCompressionTests below).
MINIPROGRAM_DIR = BASE_DIR / "miniprogram"
MINIPROGRAM_PAGES_DIR = MINIPROGRAM_DIR / "pages"
MINIPROGRAM_IMAGE_HELPER = MINIPROGRAM_DIR / "utils" / "image.js"

# Directories that are not part of the shipped tree: VCS metadata, virtualenvs,
# build/lint caches, browser-session dumps, runtime uploads and node modules.
# Mirrors `norecursedirs` in pyproject.toml.
#
# `.github/` is deliberately NOT skipped: its issue templates and workflow docs
# carry relative links worth checking, and the report-name rule below is
# case-sensitive so `.github/ISSUE_TEMPLATE/bug_report.md` is exempt anyway.
SKIPPED_DIR_NAMES = {
    ".git",
    ".venv",
    ".idea",
    ".vscode",
    ".claude",
    ".pytest_cache",
    ".ruff_cache",
    ".playwright-mcp",
    "__pycache__",
    "node_modules",
    "media",
    "logs",
    "runtime",
    "locale",
}

# Prose formats. Deliberately narrow: an app directory may legitimately ship a
# data asset its code reads (xlsx/csv/json/png), but never a document.
DOCUMENTATION_SUFFIXES = {".md", ".markdown", ".rst"}

CANVAS_SUFFIX = ".canvas.tsx"

# `[label](target)`. The target class excludes whitespace so a title suffix
# (`(path "title")`) is not swallowed into the path.
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# Anything with a URL scheme (`http:`, `https:`, `mailto:`, ...) or a bare
# in-page anchor. Relative filesystem targets are what we validate.
EXTERNAL_TARGET_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*:|#)")

# The report naming convention, matched case-sensitively on purpose: the
# documents that follow it are shouted (`FINAL_GITHUB_CONFIG_REPORT.md`,
# `REPO_DOCUMENTATION_ALIGNMENT_AUDIT_20260906.md`), while GitHub's issue
# template `.github/ISSUE_TEMPLATE/bug_report.md` is lowercase and must stay
# where it is -- case sensitivity is the only thing exempting it, since
# `.github/` is deliberately walked (see SKIPPED_DIR_NAMES).
REPORT_NAME_RE = re.compile(r"REPORT|AUDIT|GAP_ANALYSIS")

# Raw photo picker. Pages must route through utils/image.js so capture is
# compressed; a direct call bypasses sizeType + compressImage (blocker 5).
CHOOSE_MEDIA_RE = re.compile(r"wx\.chooseMedia\s*\(")

# Local filesystem media access, which cannot survive the OSS backend. Three
# idioms, all of them invisible until the box flips:
#   * ``<anything>.path`` - ``FieldFile.path`` raises NotImplementedError on a
#     non-filesystem storage, so it breaks in production and nowhere else;
#   * creating a directory under ``MEDIA_ROOT`` - a bucket has no directories;
#   * ``Path(settings.MEDIA_ROOT)`` - assumes the media root is a real path.
# ``os``/``sys``/``request`` receivers are exempt: module attribute access,
# ``sys.path`` manipulation and WSGI routing are not media files. Template paths
# derived from ``__file__`` route through ``os.path``, so they are exempt too.
FIELD_PATH_RE = re.compile(r"(?P<recv>[A-Za-z_][A-Za-z0-9_]*)\.path\b")
NON_MEDIA_PATH_RECEIVERS = {"os", "sys", "request"}
MEDIA_ROOT_WRITE_RE = re.compile(r"\bmkdir\b|\bmakedirs\b")
MEDIA_ROOT_AS_PATH_RE = re.compile(r"Path\(\s*(?:settings\.)?MEDIA_ROOT\b")

# Test modules are exempt: pinning a temporary ``MEDIA_ROOT`` or asserting that
# ``Storage.path()`` raises is exactly what they are for.
TEST_MODULE_NAME_RE = re.compile(r"^(?:test_.*|.*_test|tests)\.py$")


def _walk(root):
    """Yield every file under `root`, pruning vendored/generated directories.

    Pruning happens *during* traversal (`os.walk` with in-place `dirnames`
    editing), not by filtering afterwards. `Path.rglob("*")` plus a filter looks
    tidier but enumerates all of `.venv/` and every `node_modules/` first --
    tens of thousands of files -- which made this suite take ~13s instead of
    milliseconds. Keep it pruning.
    """
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIR_NAMES)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _read(path):
    return path.read_text(encoding="utf-8")


def _local_app_dirs():
    """Top-level directories of this project's own Django apps.

    Third-party entries (`django.contrib.admin`, `rest_framework`,
    `django_otp.plugins.otp_totp`, ...) are dotted or live in site-packages, so
    they are filtered out by requiring a dot-free name that exists under
    BASE_DIR and ships an `apps.py` or `models.py`.
    """
    app_dirs = []
    for entry in settings.INSTALLED_APPS:
        if "." in entry:
            continue
        candidate = BASE_DIR / entry
        if not candidate.is_dir():
            continue
        if not ((candidate / "apps.py").is_file() or (candidate / "models.py").is_file()):
            continue
        app_dirs.append(candidate)
    return app_dirs


def _relative(path):
    """Path as a POSIX string relative to the repository root, for messages."""
    try:
        return path.relative_to(BASE_DIR).as_posix()
    except ValueError:  # pragma: no cover - outside the repo
        return path.as_posix()


class AppDirectoryHygieneTests(SimpleTestCase):
    """A Django app directory must not accumulate documentation."""

    def test_local_app_dirs_are_discovered(self):
        """Guard the guard: if app discovery silently returns nothing, the
        hygiene checks below would pass vacuously."""
        names = {d.name for d in _local_app_dirs()}
        self.assertIn("reports", names)
        self.assertIn("inspections", names)
        # Sanity: third-party/dotted entries never become app dirs.
        self.assertNotIn("django.contrib.admin", names)

    def test_no_documentation_files_inside_app_directories(self):
        offenders = []
        for app_dir in _local_app_dirs():
            for path in _walk(app_dir):
                if path.suffix.lower() in DOCUMENTATION_SUFFIXES:
                    offenders.append(_relative(path))
        self.assertEqual(
            offenders,
            [],
            "Documentation found inside Django app directories:\n  "
            + "\n  ".join(offenders)
            + "\nApp directories hold application code and the assets that code reads "
            "at runtime. Move delivery reports, audits and gap analyses to "
            "`docs/reports/` (Canvas companions to `docs/reports/canvas/`), then "
            "update `docs/INDEX.md` and any inbound links.",
        )

    def test_reports_app_contains_only_python(self):
        """The directory that actually got polluted, asserted explicitly.

        `inspections/report_template.xlsx` is why this is scoped to `reports/`
        rather than applied to every app: that workbook is a runtime asset, so a
        blanket "Python only" rule would be wrong. `reports/` has no such asset.
        """
        reports_app = BASE_DIR / "reports"
        self.assertTrue(reports_app.is_dir(), "the `reports` Django app disappeared")
        non_python = [
            _relative(p)
            for p in _walk(reports_app)
            if p.suffix.lower() != ".py"
        ]
        self.assertEqual(
            non_python,
            [],
            "`reports/` is the Django reports app and must contain only Python "
            "source. Non-Python files found:\n  "
            + "\n  ".join(non_python)
            + "\nReports belong in `docs/reports/`.",
        )


class ReportLocationTests(SimpleTestCase):
    """Reports and their canvases have exactly one home."""

    def test_report_documents_live_in_docs_reports(self):
        offenders = [
            _relative(p)
            for p in _walk(BASE_DIR)
            if p.suffix.lower() == ".md"
            and REPORT_NAME_RE.search(p.name)
            and DOCS_REPORTS_DIR not in p.parents
        ]
        self.assertEqual(
            offenders,
            [],
            "Report-style documents outside `docs/reports/`:\n  "
            + "\n  ".join(offenders)
            + "\nDelivery reports, audits and gap analyses are catalogued in "
            "`docs/INDEX.md` -> 'Completion & Audit Reports' and must live in "
            "`docs/reports/`. (The match is case-sensitive on REPORT/AUDIT/"
            "GAP_ANALYSIS so `.github/ISSUE_TEMPLATE/bug_report.md` is exempt.)",
        )

    def test_canvas_files_live_in_docs_reports_canvas(self):
        offenders = [
            _relative(p)
            for p in _walk(BASE_DIR)
            if p.name.endswith(CANVAS_SUFFIX) and CANVAS_DIR not in p.parents
        ]
        self.assertEqual(
            offenders,
            [],
            "Canvas artifacts outside `docs/reports/canvas/`:\n  "
            + "\n  ".join(offenders)
            + "\nA `.canvas.tsx` is the visual companion to a report; keep it "
            "beside the report tree so it is version-controlled with it.",
        )

    def test_docs_reports_dirs_exist(self):
        """Fail loudly if the destination is ever renamed, which would make the
        rules above point contributors at a missing folder."""
        self.assertTrue(DOCS_REPORTS_DIR.is_dir(), "docs/reports/ is missing")
        self.assertTrue(CANVAS_DIR.is_dir(), "docs/reports/canvas/ is missing")


class MarkdownLinkIntegrityTests(SimpleTestCase):
    """Every relative Markdown link must resolve.

    Moving a document breaks the links *inside* it without touching the file's
    own content, so nothing else catches this. Concretely: when the five reports
    moved from `reports/` to `docs/reports/`, their `../docs/X.md` links silently
    became `docs/docs/X.md`, and `docs/SETUP_SUMMARY.md` had five `docs/X.md`
    links that never resolved at all.
    """

    def test_relative_links_resolve(self):
        broken = []
        checked = 0
        for path in _walk(BASE_DIR):
            if path.suffix.lower() != ".md":
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):  # pragma: no cover - binary/locked
                continue
            for lineno, line in enumerate(lines, start=1):
                for match in MARKDOWN_LINK_RE.finditer(line):
                    target = match.group(2)
                    if EXTERNAL_TARGET_RE.match(target):
                        continue
                    checked += 1
                    resolved = target.split("#", 1)[0].strip("<>")
                    if not resolved:
                        continue
                    if not (path.parent / resolved).exists():
                        broken.append(
                            f"{_relative(path)}:{lineno} -> {target}"
                        )
        self.assertGreater(
            checked,
            50,
            "Only %d relative links were scanned - the walk or the regex has "
            "probably stopped matching, which would make this test pass "
            "vacuously." % checked,
        )
        self.assertEqual(
            broken,
            [],
            "Broken relative Markdown links:\n  "
            + "\n  ".join(broken)
            + "\nRelative links are resolved from the *containing file's* "
            "directory. Moving a document usually means its own links need "
            "re-anchoring (`../docs/X.md` -> `../X.md`), and anything linking "
            "*to* it needs updating too.",
        )


class MediaStorageApiTests(SimpleTestCase):
    """Application code must reach media through the storage API, never the disk.

    ``waypost/settings.py`` flips ``STORAGES['default']`` to a bucket as soon as
    an operator fills in the four ``OSS_*`` keys, and every one of the idioms
    below still works on a development box: that asymmetry is what lets a stray
    ``.path`` survive review and fail on the first production upload.
    """

    def test_no_local_filesystem_media_access(self):
        offenders = []
        scanned = 0
        for path in _walk(BASE_DIR):
            if path.suffix != ".py" or TEST_MODULE_NAME_RE.match(path.name):
                continue
            if "tests" in path.relative_to(BASE_DIR).parts:
                continue
            scanned += 1
            try:
                lines = _read(path).splitlines()
            except (UnicodeDecodeError, OSError):  # pragma: no cover - binary/locked
                continue
            for lineno, line in enumerate(lines, start=1):
                matched = FIELD_PATH_RE.search(line)
                if matched and matched.group("recv") not in NON_MEDIA_PATH_RECEIVERS:
                    offenders.append(f"{_relative(path)}:{lineno} {line.strip()}")
                elif "MEDIA_ROOT" in line and MEDIA_ROOT_WRITE_RE.search(line):
                    offenders.append(f"{_relative(path)}:{lineno} {line.strip()}")
                elif MEDIA_ROOT_AS_PATH_RE.search(line):
                    offenders.append(f"{_relative(path)}:{lineno} {line.strip()}")

        self.assertGreater(
            scanned,
            100,
            f"Only {scanned} Python files were scanned - the walk has probably "
            "stopped matching, which would make this check pass vacuously.",
        )
        self.assertEqual(
            offenders,
            [],
            "Local filesystem media access found; use default_storage / "
            "FieldFile.name / absolute_media_url instead:\n  " + "\n  ".join(offenders)
            + "\nThese raise NotImplementedError or silently write outside the "
            "bucket once STORAGES['default'] is waypost.storage.WaypostOSSStorage.",
        )


class MiniprogramCompressionTests(SimpleTestCase):
    """Photo capture must go through utils/image.js so compression is guaranteed.

    Raw ``wx.chooseMedia`` calls in pages bypass ``sizeType`` + ``compressImage``,
    uploading 2.5-5 MB phone originals (blocker 5 in the go-live migration
    report). The helper is the single place that enforces the ~300 KB target, so
    the invariant is "only utils/image.js may call wx.chooseMedia".
    """

    def test_pages_do_not_call_choose_media_directly(self):
        offenders = [
            _relative(p)
            for p in _walk(MINIPROGRAM_PAGES_DIR)
            if p.suffix == ".js" and CHOOSE_MEDIA_RE.search(_read(p))
        ]
        self.assertEqual(
            offenders,
            [],
            "Pages must use utils/image.js pickPhotos(), not raw wx.chooseMedia "
            "(compression would be bypassed): " + ", ".join(offenders),
        )

    def test_image_helper_exists_and_compresses(self):
        self.assertTrue(
            MINIPROGRAM_IMAGE_HELPER.exists(),
            f"Missing {_relative(MINIPROGRAM_IMAGE_HELPER)} - the photo capture "
            "helper was deleted or never created",
        )
        text = _read(MINIPROGRAM_IMAGE_HELPER)
        self.assertIn(
            "sizeType", text,
            "utils/image.js must pass sizeType to wx.chooseMedia",
        )
        self.assertIn(
            "compressImage", text,
            "utils/image.js must call wx.compressImage for the deterministic cap",
        )
