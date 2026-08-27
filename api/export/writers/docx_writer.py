"""DOCX, from the same HTML the PDF is made of, via pandoc.

pandoc rather than a hand-written `python-docx` walker, for one reason: a report
is real markdown. `agents/main/prompt.py` requires an inline `[title](url)` on
every web-sourced claim, on top of headings, nested lists, tables, emphasis and
code. A walker covering that is a markdown-to-Word converter written badly, and
its failure mode is silent -- a dropped link looks like a sentence.

**Branding is applied through the theme, not the styles.** pandoc's default
reference document defines its headings and its hyperlink colour as
`themeColor="accent1"` rather than as fixed values, so swapping one colour in
`word/theme/theme1.xml` re-colours every heading and every link at once. The
table header band is the one thing not covered by that, and is patched into the
`Table` style directly.

The reference document is **generated at runtime** from pandoc's own default and
cached for the life of the process. Checking a `.docx` into git would be a
binary blob nobody could review or diff, and it would go stale silently the next
time pandoc changed its defaults.
"""

import re
import shutil
import subprocess
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

from ..document import ExportDocument
from ..errors import ExportUnavailable
from ..html import render_html

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXTENSION = "docx"

#: The brand colour, as Word wants it: RRGGBB, no `#`. Same value as `--primary`
#: in `UI/src/styles/tailwind.css`; `tests/unit/test_export_palette.py` holds the
#: two together.
ACCENT = "1E9DF1"
#: The app's body face. Word falls back on its own if the machine lacks it,
#: which is the correct behaviour for a document somebody else will open.
FONT = "Open Sans"

#: Long enough for a several-hundred-row table, short enough that a wedged
#: subprocess fails the request instead of holding a worker forever.
TIMEOUT_SECONDS = 120

_MISSING = (
    "pandoc is not installed, so DOCX export is unavailable. Install it with "
    "`brew install pandoc` (macOS) or `apt install pandoc` (Debian/Ubuntu). "
    "PDF, XLSX and CSV export do not need it."
)


def _pandoc() -> str:
    path = shutil.which("pandoc")
    if path is None:
        raise ExportUnavailable(_MISSING)
    return path


def _run(args: list[str], stdin: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(
            args, input=stdin, capture_output=True, timeout=TIMEOUT_SECONDS
        )
    except FileNotFoundError as error:  # pandoc vanished between check and call
        raise ExportUnavailable(_MISSING) from error
    except subprocess.TimeoutExpired as error:
        raise ExportUnavailable(
            f"pandoc did not finish within {TIMEOUT_SECONDS}s."
        ) from error
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()
        raise ExportUnavailable(f"pandoc failed: {detail or done.returncode}")
    return done.stdout


def _substitute(pattern: str, replacement: str, xml: str, what: str) -> str:
    """`re.sub`, but a pattern that matched nothing is an error.

    pandoc's default reference document is generated at runtime from whatever
    pandoc is installed, so these patterns are read against a file that can
    change under us. A silent no-match would ship an unbranded document that
    looks like a design decision rather than a broken patch.
    """
    patched, count = re.subn(pattern, replacement, xml, flags=re.DOTALL)
    if not count:
        raise ExportUnavailable(
            f"Could not brand the Word reference document: {what} not found in "
            "pandoc's default. The default changed; update `docx_writer.py`."
        )
    return patched


def _brand_theme(xml: str) -> str:
    """Point `accent1` and the hyperlink colour at the brand, and set the face.

    Every heading style in pandoc's default reference document is
    `themeColor="accent1"`, so this one substitution colours all of them.
    """
    xml = _substitute(
        r'(<a:accent1>\s*<a:srgbClr val=")[0-9A-Fa-f]{6}("\s*/>\s*</a:accent1>)',
        rf"\g<1>{ACCENT}\g<2>",
        xml,
        "the accent1 colour",
    )
    xml = _substitute(
        r'(<a:hlink>\s*<a:srgbClr val=")[0-9A-Fa-f]{6}("\s*/>\s*</a:hlink>)',
        rf"\g<1>{ACCENT}\g<2>",
        xml,
        "the hyperlink colour",
    )
    return _substitute(
        r'<a:latin typeface="[^"]*"',
        f'<a:latin typeface="{FONT}"',
        xml,
        "the latin typeface",
    )


def _brand_table_header(xml: str) -> str:
    """Give the table's first row the accent band the PDF has.

    The `Table` style's `firstRow` conditional formatting already exists in the
    default and carries only a bottom border; it is replaced wholesale with
    shading and white bold text, so every table pandoc emits picks them up with
    no per-table markup.
    """
    return _substitute(
        r'<w:tblStylePr w:type="firstRow">.*?</w:tblStylePr>',
        '<w:tblStylePr w:type="firstRow">'
        '<w:rPr><w:b/><w:color w:val="FFFFFF"/></w:rPr>'
        "<w:tcPr>"
        f'<w:shd w:val="clear" w:color="auto" w:fill="{ACCENT}"/>'
        '<w:vAlign w:val="bottom"/>'
        "</w:tcPr>"
        "</w:tblStylePr>",
        xml,
        "the table's firstRow style",
    )


@lru_cache(maxsize=1)
def reference_doc() -> Path:
    """pandoc's default reference document, re-coloured, written once per process.

    Returned as a path in a temp directory that deliberately outlives the call:
    `--reference-doc` needs a file, and rebuilding it per export would run pandoc
    twice for every download.
    """
    directory = Path(tempfile.mkdtemp(prefix="tf26-export-"))
    plain = directory / "pandoc-default.docx"
    plain.write_bytes(_run([_pandoc(), "--print-default-data-file", "reference.docx"]))

    branded = directory / "tf26-reference.docx"
    with zipfile.ZipFile(plain) as source, zipfile.ZipFile(
        branded, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/theme/theme1.xml":
                data = _brand_theme(data.decode("utf-8")).encode("utf-8")
            elif item.filename == "word/styles.xml":
                data = _brand_table_header(data.decode("utf-8")).encode("utf-8")
            target.writestr(item, data)
    return branded


#: `<title>` is stripped before pandoc sees the page, and this is not cosmetic.
#: pandoc reads `<head><title>` as the document's metadata title and renders it
#: as a `Title`-styled paragraph of its own -- on top of the `<h1>` already in
#: the body, so the name appeared twice at the top of every exported report.
#:
#: Stripped here rather than removed from the template because WeasyPrint uses
#: the same element for the PDF's document title, which is what a browser's PDF
#: viewer shows in its tab. The cost is an empty `dc:title` in the Word file's
#: properties; the filename carries the title, and one visible heading beats an
#: invisible property paid for with a duplicated one.
_TITLE_ELEMENT = re.compile(r"<title>.*?</title>", re.DOTALL | re.IGNORECASE)


def write_docx(document: ExportDocument) -> bytes:
    """The document as a Word file.

    pandoc refuses to write a binary format to stdout, so the output goes to a
    temp file that is read back and dropped. The input goes in on stdin, which
    keeps the document -- possibly a few megabytes of table -- off disk.
    """
    html = _TITLE_ELEMENT.sub("", render_html(document))
    with tempfile.TemporaryDirectory(prefix="tf26-docx-") as workspace:
        out = Path(workspace) / f"export.{EXTENSION}"
        _run(
            [
                _pandoc(),
                "--from=html",
                "--to=docx",
                f"--reference-doc={reference_doc()}",
                "--output",
                str(out),
            ],
            stdin=html.encode("utf-8"),
        )
        return out.read_bytes()
