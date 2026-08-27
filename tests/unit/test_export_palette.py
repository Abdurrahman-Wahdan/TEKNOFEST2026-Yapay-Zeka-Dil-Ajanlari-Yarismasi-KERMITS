"""The exported document's palette is the application's palette.

`UI/src/styles/tailwind.css` is the single source for this app's colours -- the
same rule `UI/src/lib/palette.ts` states for the frontend. `api/export/assets/
export.css` holds a copy, because the API has to be able to render a PDF on a
machine where the frontend source tree is not installed.

A copy that nothing checks is a copy that goes stale, and the failure is
invisible: exports keep rendering, in last year's blue. This test is the check.
When it fails, copy the `:root` block across again -- do not edit either value
by hand.
"""

import re
from pathlib import Path

import pytest

from api.export.writers.docx_writer import ACCENT, FONT

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
TAILWIND = ROOT / "UI" / "src" / "styles" / "tailwind.css"
EXPORT_CSS = ROOT / "api" / "export" / "assets" / "export.css"

_DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def _tailwind_light() -> dict[str, str]:
    """The `:root` block, which is tailwind.css's light mode."""
    css = TAILWIND.read_text(encoding="utf-8")
    block = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL)
    assert block, "tailwind.css no longer has a :root block"
    return {k: v.strip() for k, v in _DECLARATION.findall(block.group(1))}


def _export_copy() -> dict[str, str]:
    """Whatever sits between the copy markers in export.css."""
    css = EXPORT_CSS.read_text(encoding="utf-8")
    block = re.search(
        r"/\* tf26:palette:start \*/(.*?)/\* tf26:palette:end \*/", css, re.DOTALL
    )
    assert block, "the palette markers are missing from export.css"
    return {k: v.strip() for k, v in _DECLARATION.findall(block.group(1))}


def test_the_copied_palette_still_matches_tailwind():
    source = _tailwind_light()
    copied = _export_copy()

    assert copied, "export.css copied no tokens at all"
    unknown = sorted(set(copied) - set(source))
    assert not unknown, f"not in tailwind.css's :root any more: {unknown}"

    drifted = {
        token: (copied[token], source[token])
        for token in copied
        if copied[token] != source[token]
    }
    assert not drifted, f"export.css is out of date (copied, actual): {drifted}"


def test_the_word_accent_is_the_same_colour_as_the_css_one():
    """Word wants `RRGGBB` with no `#`, so the DOCX writer cannot read the
    variable and holds its own copy. One more place to keep honest."""
    primary = _export_copy()["--primary"]

    assert primary.lstrip("#").upper() == ACCENT


def test_the_word_font_is_the_app_font():
    stack = _export_copy()["--font-sans"]

    assert stack.split(",")[0].strip() == FONT
