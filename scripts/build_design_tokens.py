#!/usr/bin/env python3
"""Generate design-token stylesheets from the single source of truth.

`static/design/tokens.json` is the only place token values are defined. This
script renders it into two generated files that must never be hand-edited:

    static/css/tokens.css           backend (`:root { --wp-*: ... }`)
    miniprogram/styles/tokens.wxss  WeChat mini program (`page { --wp-*: ... }`)

Because both files come from the same JSON, the web app and the mini program are
guaranteed to share an identical palette, type scale, spacing rhythm, radii,
shadows and motion. This is what structurally enforces design parity between the
two surfaces (see docs/DESIGN_SYSTEM.md and ADR-0012).

Usage:
    python scripts/build_design_tokens.py            # write the generated files
    python scripts/build_design_tokens.py --check     # verify no drift (CI/tests)

`--check` regenerates in memory and compares against the committed files; it
exits non-zero (and prints a diff-friendly message) if they are out of date or
missing, so CI fails when someone edits tokens.json without rebuilding.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

TOKENS_JSON = BASE_DIR / "static" / "design" / "tokens.json"
TOKENS_CSS = BASE_DIR / "static" / "css" / "tokens.css"
TOKENS_WXSS = BASE_DIR / "miniprogram" / "styles" / "tokens.wxss"

# `meta` carries documentation about the token file, not token values.
NON_TOKEN_KEYS = {"meta"}

HEADER_CSS = """\
/* GENERATED FILE - DO NOT EDIT.
 *
 * Source:    static/design/tokens.json
 * Generator: scripts/build_design_tokens.py
 *
 * Design tokens shared by the whole product. Edit the JSON and re-run the
 * generator; never edit this file by hand. See docs/DESIGN_SYSTEM.md.
 */
"""

HEADER_WXSS = """\
/* GENERATED FILE - DO NOT EDIT.
 *
 * Source:    static/design/tokens.json
 * Generator: scripts/build_design_tokens.py
 *
 * WeChat mini program mirror of static/css/tokens.css. Values are identical to
 * the backend so the two surfaces stay visually consistent. Edit the JSON and
 * re-run the generator; never edit this file by hand. See docs/DESIGN_SYSTEM.md.
 */
"""


def load_tokens(path: Path = TOKENS_JSON) -> dict:
    """Load the token source JSON."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def flatten(tokens: dict) -> list[tuple[str, str]]:
    """Flatten `{category: {name: value}}` into ordered `(--wp-cat-name, value)`.

    Insertion order from the JSON is preserved so output is deterministic.
    """
    prefix = tokens.get("meta", {}).get("prefix", "--wp-")
    pairs: list[tuple[str, str]] = []
    for category, entries in tokens.items():
        if category in NON_TOKEN_KEYS:
            continue
        if not isinstance(entries, dict):
            raise ValueError(f"Token category {category!r} must be an object")
        for name, value in entries.items():
            pairs.append((f"{prefix}{category}-{name}", str(value)))
    return pairs


def _render_body(pairs: list[tuple[str, str]], selector: str) -> str:
    lines = [f"{selector} {{"]
    for var, value in pairs:
        lines.append(f"  {var}: {value};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_css(tokens: dict) -> str:
    """Render the backend `:root` stylesheet."""
    return HEADER_CSS + _render_body(flatten(tokens), ":root")


def render_wxss(tokens: dict) -> str:
    """Render the mini program `page` stylesheet (CSS custom properties)."""
    return HEADER_WXSS + _render_body(flatten(tokens), "page")


def _outputs(tokens: dict) -> dict[Path, str]:
    return {
        TOKENS_CSS: render_css(tokens),
        TOKENS_WXSS: render_wxss(tokens),
    }


def build(tokens: dict | None = None) -> list[Path]:
    """Write the generated files, creating parent directories as needed."""
    tokens = tokens if tokens is not None else load_tokens()
    written: list[Path] = []
    for path, content in _outputs(tokens).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        written.append(path)
    return written


def _read_normalized(path: Path) -> str | None:
    if not path.exists():
        return None
    # Normalize CRLF -> LF so a Windows checkout does not read as drift.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def check(tokens: dict | None = None) -> list[str]:
    """Return a list of drift problems (empty means every file is up to date)."""
    tokens = tokens if tokens is not None else load_tokens()
    problems: list[str] = []
    for path, expected in _outputs(tokens).items():
        actual = _read_normalized(path)
        if actual is None:
            problems.append(f"missing: {path.relative_to(BASE_DIR)} (run the generator)")
        elif actual != expected:
            problems.append(f"out of date: {path.relative_to(BASE_DIR)} (run the generator)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated files match tokens.json instead of writing them",
    )
    args = parser.parse_args(argv)

    tokens = load_tokens()
    if args.check:
        problems = check(tokens)
        if problems:
            print("Design token drift detected:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print(
                "Fix with: python scripts/build_design_tokens.py",
                file=sys.stderr,
            )
            return 1
        print("Design tokens are up to date.")
        return 0

    written = build(tokens)
    for path in written:
        print(f"wrote {path.relative_to(BASE_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
