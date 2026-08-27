"""`ExportDocument` -> one HTML string. The hub both document writers read from.

PDF and DOCX are produced from the *same* string: WeasyPrint styles it with
`assets/export.css`, pandoc takes the structure and drops the stylesheet. That
is the point of routing through HTML rather than giving each format its own
builder -- the two files cannot disagree about what the document contains,
because there is only one description of it.

Markdown is not the hub, and the reason is worth keeping written down: markdown
is untyped. A `money` cell rendered to markdown is the string `₺1.234,56`, and a
spreadsheet column of strings cannot be summed -- which is the entire reason to
offer XLSX instead of a renamed CSV. So markdown is one *inlet* (reports) and
HTML is the hub, one step later.
"""

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .document import ExportDocument, TableBlock

_HERE = Path(__file__).parent
_CSS = _HERE / "assets" / "export.css"
_TEMPLATES = _HERE / "templates"

#: Past this many columns a portrait A4 page turns each column into an
#: unreadable sliver, so the page is rotated instead. Measured against the
#: comparison-table pool, whose widest tables run to 22 columns.
WIDE_COLUMNS = 8

# The document's own chrome, in Turkish.
#
# Hard-coded here rather than sent from the browser, following the precedent
# `api/saved_tables.py` already sets with `CITE_LABEL`: these are labels the
# server puts on a file it generates, not interface copy. The application ships
# in one language (`UI/src/i18n/routing.ts`: `locales: ["tr"]`), so there is no
# second catalogue for them to be missing from.
SOURCES_LABEL = "Kaynaklar"
ROWS_LABEL = "satır"


@lru_cache(maxsize=1)
def stylesheet() -> str:
    return _CSS.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _meta_line(document: ExportDocument) -> str:
    """The grey line under the title: when this was made, and how big it is.

    The row count appears only for a document that is exactly one table. On a
    report -- prose with two tables in it -- "48 satır" describes nothing the
    reader can point at, so it is left off rather than made up.
    """
    parts: list[str] = []
    if document.generated_at is not None:
        parts.append(document.generated_at.strftime("%d.%m.%Y %H:%M"))

    blocks = document.blocks
    if len(blocks) == 1 and isinstance(blocks[0], TableBlock):
        parts.append(f"{len(blocks[0].rows)} {ROWS_LABEL}")

    return " · ".join(parts)


def is_wide(document: ExportDocument) -> bool:
    """Whether this document wants a landscape page."""
    return any(len(table.columns) > WIDE_COLUMNS for table in document.tables)


def render_html(document: ExportDocument) -> str:
    """The document as a standalone HTML page, stylesheet inlined.

    Inlined rather than linked because both consumers are handed a string with
    no base URL: WeasyPrint would have to resolve a relative `href` against a
    directory that does not exist for it, and pandoc would fetch nothing at all.
    """
    return _environment().get_template("document.html.j2").render(
        document=document,
        css=stylesheet(),
        meta=_meta_line(document),
        wide=is_wide(document),
        sources_label=SOURCES_LABEL,
    )
