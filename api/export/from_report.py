"""An automation report as an `ExportDocument`.

A report is the assistant's markdown -- `agents/main/prompt.py` requires an
inline `[title](url)` on every web-sourced claim, on top of headings, lists,
tables and the literal `→` it mandates for menu paths. So this is the one inlet
where markdown is the input, and the parse has to be a real one.

**Tables are lifted out of the prose rather than left in it.** A markdown table
rendered as prose HTML would reach Word as a `<table>` pandoc styles like a web
page; lifted into a `TableBlock` it goes through the same path a `/compare`
export does and comes out looking like every other table this application
produces. It also means a report and a standalone table are one document shape,
which is what stops the two from drifting apart.

`commonmark` plus `table` and `strikethrough`, not the `gfm-like` preset: that
preset also switches on linkify, which needs `linkify-it-py`, and there is
nothing for it to do here. The agent is instructed never to emit a bare URL.
"""

from datetime import datetime
from typing import Any, Iterable

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agents.shared.clock import TZ

from .document import (
    BLANK,
    Block,
    Cell,
    Citation,
    Column,
    ExportDocument,
    ProseBlock,
    TableBlock,
)
from .from_table import source_label
from .plain import plain, without_emoji

#: `html=False` is a security setting, not a formatting one.
#:
#: CommonMark permits raw HTML and markdown-it passes it through by default. A
#: report body is written by a language model that has just read bank websites
#: and web search results, so an injected `<img src="http://attacker/...">` in a
#: retrieved page could reach this string -- and unlike the chat, the renderer
#: here is WeasyPrint running *on the server*, which would fetch it. Turning raw
#: HTML off means such a tag is escaped and shows up as visible text in the PDF,
#: which is both harmless and the honest thing to show.
#:
#: It is also what makes the template's `| safe` on a prose block safe.
_MD = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def _inline_text(token: Token) -> str:
    """The visible text of an inline token, with its markup removed.

    Walks the children rather than rendering to HTML and stripping tags: a cell
    reading `<2 gün` would survive the walk and lose everything after the `<` in
    a strip.
    """
    parts: list[str] = []
    for child in token.children or ():
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts).strip()


def _inline_href(token: Token) -> str:
    """The first link in an inline token, if it has one."""
    for child in token.children or ():
        if child.type == "link_open":
            return child.attrGet("href") or ""
    return ""


def _align(token: Token) -> str:
    """The column alignment markdown declared, as a CSS keyword.

    markdown-it writes it as an inline `style` attribute on every cell; the
    document shape wants it once, on the column.
    """
    style = token.attrGet("style") or ""
    for keyword in ("right", "center"):
        if keyword in style:
            return keyword
    return "left"


def _table_block(tokens: list[Token]) -> TableBlock:
    """One `table_open` … `table_close` run, as a grid."""
    columns: list[Column] = []
    rows: list[list[Cell]] = []
    current: list[Cell] = []
    in_head = False
    cell_open: Token | None = None

    for token in tokens:
        if token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif token.type in ("th_open", "td_open"):
            cell_open = token
        elif token.type == "inline" and cell_open is not None:
            text = _inline_text(token)
            href = _inline_href(token)
            if in_head:
                columns.append(
                    Column(
                        key=f"c{len(columns)}",
                        label=text,
                        align=_align(cell_open),
                    )
                )
            else:
                current.append(
                    Cell(
                        value=text or None,
                        display=text or BLANK,
                        type="link" if href else "text",
                        href=href,
                    )
                )
            cell_open = None
        elif token.type == "tr_close" and not in_head:
            # A malformed row is padded rather than dropped. Markdown lets a row
            # carry fewer cells than the header declares, and losing the row
            # would lose data the reader could see on screen.
            while len(current) < len(columns):
                current.append(Cell(display=BLANK))
            rows.append(current[: len(columns)])
            current = []

    return TableBlock(columns=columns, rows=rows)


def _blocks(body: str) -> list[Block]:
    """Top-level markdown, split into prose runs and table runs."""
    tokens = _MD.parse(body)
    blocks: list[Block] = []
    prose: list[Token] = []
    table: list[Token] = []
    # A flag rather than a depth counter: markdown tables cannot nest, so there
    # is no second level to count to.
    in_table = False

    def flush_prose() -> None:
        if not prose:
            return
        html = _MD.renderer.render(prose, _MD.options, {}).strip()
        if html:
            blocks.append(ProseBlock(html=html))
        prose.clear()

    for token in tokens:
        if not in_table and token.type == "table_open":
            flush_prose()
            in_table = True
            table = [token]
            continue
        if in_table:
            table.append(token)
            if token.type == "table_close":
                block = _table_block(table)
                # A table with no header row is not a table -- it is a fragment
                # of one, and rendering it would draw an empty heading band.
                if block.columns:
                    blocks.append(block)
                in_table = False
                table = []
            continue
        prose.append(token)

    flush_prose()
    return blocks


def _citations(raw: Iterable[dict[str, Any]]) -> list[Citation]:
    """The report's sources, deduplicated, in the order they were first cited.

    A report earns a bibliography where a table does not: its links are inline in
    prose, so a printed page shows the label and swallows the URL. Collecting
    them at the end is the only way a PDF stays checkable.
    """
    seen: set[str] = set()
    out: list[Citation] = []
    for entry in raw:
        url = str(entry.get("cite_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(Citation(label=str(entry.get("title") or "") or source_label(url), url=url))
    return out


def report_document(
    *,
    title: str,
    body: str,
    citations: Iterable[dict[str, Any]] = (),
    generated_at: datetime | None = None,
) -> ExportDocument:
    """One report, ready for the PDF and DOCX writers."""
    # Stripped before the parse, not after it: markdown is where the emoji
    # actually is, and `## 📊 Genel Özet` cleaned here yields `<h2>Genel Özet</h2>`
    # where cleaning the rendered HTML would leave `<h2> Genel Özet</h2>`.
    return plain(
        ExportDocument(
            title=title,
            blocks=_blocks(without_emoji(body)),
            generated_at=generated_at or datetime.now(TZ),
            citations=_citations(citations),
        )
    )
