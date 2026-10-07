"""Design-system conformance tests.

These run inside the required backend CI job (`.github/workflows/backend-ci.yml`
-> `python manage.py test`), so they are a hard gate on every pull request. They
enforce the rules in `docs/DESIGN_SYSTEM.md` (see ADR-0012) that stop the
historical UI regressions from returning:

  * token drift between `tokens.json` and the generated CSS/WXSS + app.json
  * decorative gradients behind content
  * references to CSS variables that no longer exist
  * the retired olive brand colour leaking back into the chrome
  * buttons hidden with `opacity: 0`
  * raw hex colours in the shared/core design-system files

The checks are intentionally static (read files, regex) so they need no database
and run in well under a second.
"""
import importlib.util
import json
import re
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_CSS_DIR = BASE_DIR / "static" / "css"
MINIPROGRAM_DIR = BASE_DIR / "miniprogram"
TOKENS_JSON = BASE_DIR / "static" / "design" / "tokens.json"
APP_JSON = MINIPROGRAM_DIR / "app.json"
GENERATOR_PATH = BASE_DIR / "scripts" / "build_design_tokens.py"

# Shared/core files must be token-pure (no raw hex colours at all).
CORE_HEX_FREE_FILES = [
    STATIC_CSS_DIR / "waypost.css",
    TEMPLATES_DIR / "base" / "base.html",
    MINIPROGRAM_DIR / "app.wxss",
]
CORE_HEX_FREE_GLOBS = [
    (TEMPLATES_DIR / "base" / "partials", "*.html"),
    (MINIPROGRAM_DIR / "components", "*.wxss"),
]

# Generated files legitimately contain hex (they ARE the token values).
GENERATED_FILES = {"tokens.css", "tokens.wxss", "tokens.json"}

HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
GRADIENT_RE = re.compile(r"linear-gradient\s*\(")
OLIVE_RE = re.compile(r"#4[fF]6228\b")
LEGACY_VAR_RE = re.compile(
    r"var\(\s*--(primary-color|secondary-color|accent-color|success-color|"
    r"warning-color|info-color|light-color|dark-color|border-radius)\b"
)
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r"style\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
OPACITY_ZERO_RE = re.compile(r"opacity\s*:\s*0(?:\.0+)?\s*(?:;|!|\}|$)")


def _load_generator():
    """Import scripts/build_design_tokens.py without adding scripts/ to sys.path."""
    spec = importlib.util.spec_from_file_location("build_design_tokens", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path):
    return path.read_text(encoding="utf-8")


def _iter_files(directory, pattern, exclude_generated=True):
    for path in sorted(directory.rglob(pattern)):
        if not path.is_file():
            continue
        if exclude_generated and path.name in GENERATED_FILES:
            continue
        yield path


def _css_chunks(path):
    """Return the CSS text to inspect for a file: whole file for .css, only the
    <style> blocks for HTML (so Django `{{ }}`/`{% %}` braces are not parsed)."""
    text = _read(path)
    if path.suffix.lower() == ".css":
        return [text]
    return STYLE_BLOCK_RE.findall(text)


MULTILINE_COMMENT_RE = re.compile(r"^[ \t]*\{#(?![^\n]*#\})", re.M)


class TokenDriftTests(SimpleTestCase):
    def test_generated_token_files_are_up_to_date(self):
        """tokens.css / tokens.wxss must match tokens.json (regenerate to fix)."""
        generator = _load_generator()
        problems = generator.check()
        self.assertEqual(
            problems, [],
            "Design token drift detected. Run `python scripts/build_design_tokens.py` "
            "and commit the result. Problems: " + "; ".join(problems),
        )

    def test_miniprogram_window_colours_match_tokens(self):
        """app.json chrome colours must equal the primary/canvas tokens so the
        mini program visually matches the web app (docs/DESIGN_SYSTEM.md §7)."""
        tokens = json.loads(_read(TOKENS_JSON))
        app = json.loads(_read(APP_JSON))
        window = app.get("window", {})
        expected_nav = tokens["color"]["primary"].lower()
        expected_bg = tokens["color"]["bg"].lower()
        self.assertEqual(
            window.get("navigationBarBackgroundColor", "").lower(), expected_nav,
            "app.json navigationBarBackgroundColor must equal the --wp-color-primary token",
        )
        self.assertEqual(
            window.get("backgroundColor", "").lower(), expected_bg,
            "app.json backgroundColor must equal the --wp-color-bg token",
        )


class TemplateHygieneTests(SimpleTestCase):
    def test_no_multiline_template_comments(self):
        """Django's lexer matches `{# ... #}` without DOTALL, so a comment that
        spans lines is emitted into the HTML as literal text. Keep every template
        comment on a single line."""
        offenders = []
        for path in _iter_files(TEMPLATES_DIR, "*.html"):
            text = _read(path)
            if MULTILINE_COMMENT_RE.search(text):
                offenders.append(path.relative_to(BASE_DIR).as_posix())
        self.assertEqual(
            offenders, [],
            "Multi-line {# ... #} comments leak into rendered HTML (Django only "
            "strips single-line comments); put each on one line: "
            + ", ".join(offenders),
        )

    def test_no_raw_hex_in_template_css(self):
        """No hardcoded hex colours in the CSS of any web template - neither in
        <style> blocks nor in style="" attributes. Colours must come from
        var(--wp-*) tokens so the whole UI stays on one palette. PDF templates
        are exempt (print media). Chart/JS colours live in <script> and are out
        of scope for this rule."""
        offenders = []
        for path in _iter_files(TEMPLATES_DIR, "*.html"):
            rel = path.relative_to(BASE_DIR).as_posix()
            if "pdf" in rel.lower():
                continue
            text = _read(path)
            css_chunks = list(STYLE_BLOCK_RE.findall(text))
            css_chunks += [m.group(1) for m in STYLE_ATTR_RE.finditer(text)]
            hits = set()
            for chunk in css_chunks:
                hits.update(HEX_COLOR_RE.findall(chunk))
            if hits:
                offenders.append(f"{rel} {sorted(hits)}")
        self.assertEqual(
            offenders, [],
            "Hardcoded hex colours in template CSS - use var(--wp-*) tokens "
            "(see docs/DESIGN_SYSTEM.md): " + "; ".join(offenders),
        )

    def test_no_decorative_gradients_in_templates(self):
        """No `linear-gradient(...)` in web templates or shared CSS. Gradients
        behind text/controls caused the low-contrast button bug. PDF templates
        are exempt (print media, not interactive UI)."""
        offenders = []
        for path in _iter_files(TEMPLATES_DIR, "*.html"):
            rel = path.relative_to(BASE_DIR).as_posix()
            if "pdf" in rel.lower():
                continue
            if GRADIENT_RE.search(_read(path)):
                offenders.append(rel)
        for path in _iter_files(STATIC_CSS_DIR, "*.css"):
            if GRADIENT_RE.search(_read(path)):
                offenders.append(path.relative_to(BASE_DIR).as_posix())
        self.assertEqual(
            offenders, [],
            "Decorative gradients are banned in web UI (use solid token colours): "
            + ", ".join(offenders),
        )

    def test_no_references_to_removed_css_variables(self):
        """The old base.html :root variables were removed; referencing them now
        silently breaks styling."""
        offenders = []
        search_dirs = [(TEMPLATES_DIR, "*.html"), (STATIC_CSS_DIR, "*.css")]
        for directory, pattern in search_dirs:
            for path in _iter_files(directory, pattern):
                if LEGACY_VAR_RE.search(_read(path)):
                    offenders.append(path.relative_to(BASE_DIR).as_posix())
        self.assertEqual(
            offenders, [],
            "These files reference removed CSS variables (migrate to --wp-* tokens): "
            + ", ".join(offenders),
        )

    def test_retired_brand_olive_not_used_in_chrome(self):
        """The olive #4F6228 was retired from the UI in favour of the shared
        palette. It may only live in the token source as an optional brand token."""
        offenders = []
        for path in _iter_files(TEMPLATES_DIR, "*.html"):
            if OLIVE_RE.search(_read(path)):
                offenders.append(path.relative_to(BASE_DIR).as_posix())
        for directory, pattern in ((MINIPROGRAM_DIR, "*.wxss"), (MINIPROGRAM_DIR, "*.wxml")):
            for path in _iter_files(directory, pattern):
                if "node_modules" in path.parts:
                    continue
                if OLIVE_RE.search(_read(path)):
                    offenders.append(path.relative_to(BASE_DIR).as_posix())
        self.assertEqual(
            offenders, [],
            "Retired olive #4F6228 found (use --wp-color-* tokens): " + ", ".join(offenders),
        )

    def test_no_opacity_hidden_buttons(self):
        """A button must never be hidden/dimmed with opacity:0 (the chart gear
        button regression). Selectors containing `btn` may not set opacity to 0."""
        offenders = []
        targets = list(_iter_files(TEMPLATES_DIR, "*.html")) + list(_iter_files(STATIC_CSS_DIR, "*.css"))
        for path in targets:
            rel = path.relative_to(BASE_DIR).as_posix()
            for chunk in _css_chunks(path):
                for selector, body in CSS_RULE_RE.findall(chunk):
                    if "btn" in selector.lower() and OPACITY_ZERO_RE.search(body):
                        offenders.append(f"{rel} :: {selector.strip()[:60]}")
        self.assertEqual(
            offenders, [],
            "Buttons must not be hidden with opacity:0 (essential controls stay "
            "visible): " + "; ".join(offenders),
        )


class CoreTokenPurityTests(SimpleTestCase):
    def test_core_files_are_hex_free(self):
        """The shared design-system files must use var(--wp-*) only - no raw hex.
        Per-page templates are migrated incrementally and are not covered here."""
        files = list(CORE_HEX_FREE_FILES)
        for directory, pattern in CORE_HEX_FREE_GLOBS:
            if directory.exists():
                files.extend(_iter_files(directory, pattern))
        offenders = []
        for path in files:
            if not path.exists():
                self.fail(f"Expected core design-system file is missing: {path}")
            match = HEX_COLOR_RE.search(_read(path))
            if match:
                offenders.append(f"{path.relative_to(BASE_DIR).as_posix()} ({match.group(0)})")
        self.assertEqual(
            offenders, [],
            "Core design-system files must not contain raw hex colours (use "
            "var(--wp-*) tokens): " + ", ".join(offenders),
        )

    def test_miniprogram_wxss_uses_tokens_not_hex(self):
        """Every mini program stylesheet (except the generated tokens.wxss) must
        use var(--wp-*) tokens rather than raw hex, so the mini program can never
        drift from the web palette (docs/DESIGN_SYSTEM.md section 7)."""
        offenders = []
        for path in _iter_files(MINIPROGRAM_DIR, "*.wxss"):
            if "node_modules" in path.parts:
                continue
            match = HEX_COLOR_RE.search(_read(path))
            if match:
                offenders.append(
                    f"{path.relative_to(BASE_DIR).as_posix()} ({match.group(0)})"
                )
        self.assertEqual(
            offenders, [],
            "Mini program WXSS must use var(--wp-*) tokens, not raw hex: "
            + ", ".join(offenders),
        )
