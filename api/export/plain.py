"""Emoji out, everything else untouched. The last thing a document passes through.

A report is written by a language model, and a language model decorates its
headings: `## 📊 Genel Karşılaştırma Özeti`. On a screen that is a house style
question. In a file it is not -- these exports are forwarded, printed and put in
front of a credit committee, and a bar chart glyph in a heading is the difference
between a document that reads as a bank's and one that reads as a chat log.

So it is removed here, once, on `ExportDocument` -- the same reason everything
else in this package normalises into that shape first. Four writers read it and
none of them has an opinion about emoji, which is what stops CSV and PDF from
ending up with different answers.

**Which characters go is the whole problem**, and it is not "everything
non-ASCII": this is a Turkish application whose cells are full of `ğ`, `₺` and
`—`. The rule is Unicode's own `Emoji` property, minus ASCII, minus a short list
of marks that are `Emoji=Yes` but are read as text rather than as pictures:

- `©`, `®`, `™` -- legal marks, and they turn up inside a product's name.
- `✔`, `☑`, `✖` -- how a model writes "yes" and "no" in a table cell.

`✓`, `✕` and `→` need no exemption because Unicode does not consider them emoji
at all, which is worth knowing before anyone is tempted to hand-roll a code point
range: `✓` and `✕` are what a `bool` cell is drawn with
(`UI/src/lib/cell-display.ts`) and `→` is mandated for menu paths
(`agents/main/prompt.py`). A range like `U+2600-U+27BF` -- the obvious guess --
eats all three and turns every yes/no column in the spreadsheet blank.

`regex` rather than the standard library's `re`, which has no `\\p{Emoji}`: the
alternative is a hand-maintained table of code points that is wrong the moment
Unicode adds a block, and being wrong here means deleting a bank's data.
"""

import regex

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

#: `Emoji=Yes` in Unicode, but text to a reader. See the module docstring; each
#: one is here because deleting it would delete meaning, not decoration.
KEPT = "©®™✔☑✖"

#: One pictograph, however it was composed -- and a pictograph is rarely one code
#: point. A flag is two regional indicators, a family is three people joined by
#: U+200D, a skin tone is a modifier, `⚠️` is a text symbol plus U+FE0F, and a
#: keycap is a digit plus U+FE0F plus U+20E3. Matching a single character leaves
#: the invisible remainder in the file, where it shows up as a tofu box in Word.
#:
#: The joiners are matched on their own as well as in a run, so a variation
#: selector orphaned by an earlier pass cannot survive on its own.
_PICTOGRAPH = r"[\p{Emoji}--\p{ASCII}--[" + KEPT + r"]]"
_JOINER = r"[️‍⃣\U000E0020-\U000E007F]"

#: The surrounding horizontal space is part of the match so that removal cannot
#: leave a double space or a heading that starts with a blank. Only spaces and
#: tabs, never a newline: a blank line is a paragraph break in markdown, and
#: swallowing one would join two paragraphs into one.
_RUN = regex.compile(
    rf"[ \t]*(?:{_PICTOGRAPH}|{_JOINER})+[ \t]*", regex.V1
)


def without_emoji(text: str) -> str:
    """`text` with every pictograph removed and its spacing left sane.

    Runs over markdown as happily as over a bare label, which is why the leading
    whitespace is only ever taken *after* something else on the line: in markdown
    a leading space is structure, and eating the indent of `  - 📊 Kâr oranı`
    would promote a nested bullet to a top-level one.
    """
    if not text:
        return text

    def gap(match: regex.Match) -> str:
        # A pictograph between two words leaves the one space that was already
        # doing that job; one at either end of a line leaves nothing.
        before = match.string[: match.start()].rsplit("\n", 1)[-1]
        after = match.string[match.end() :].split("\n", 1)[0]
        return " " if before and after else ""

    return _RUN.sub(gap, text)


def _cell(cell: Cell) -> Cell:
    """`href` is deliberately not touched -- it is a URL, not prose, and a
    pictograph cannot appear in one un-escaped."""
    return Cell(
        value=without_emoji(cell.value) if isinstance(cell.value, str) else cell.value,
        # A cell that was *only* a pictograph is now empty, and an empty cell in
        # this application reads `—`, not blank.
        display=without_emoji(cell.display) or BLANK,
        type=cell.type,
        href=cell.href,
        note=without_emoji(cell.note),
        tone=cell.tone,
    )


def _block(block: Block) -> Block:
    if isinstance(block, TableBlock):
        return TableBlock(
            # `key` is an identifier the frontend chose, never shown to a reader.
            columns=[
                Column(
                    key=column.key,
                    label=without_emoji(column.label),
                    type=column.type,
                    align=column.align,
                    currency=column.currency,
                    decimals=column.decimals,
                )
                for column in block.columns
            ],
            rows=[[_cell(cell) for cell in row] for row in block.rows],
            title=without_emoji(block.title),
            note=without_emoji(block.note),
        )
    return ProseBlock(html=without_emoji(block.html))


def plain(document: ExportDocument) -> ExportDocument:
    """The same document with no pictograph left in any field a reader sees.

    Applied by both `from_*` readers at their exit rather than by the four
    writers, so a fifth format cannot be added without it.
    """
    return ExportDocument(
        title=without_emoji(document.title),
        subtitle=without_emoji(document.subtitle),
        blocks=[_block(block) for block in document.blocks],
        generated_at=document.generated_at,
        citations=[
            Citation(label=without_emoji(citation.label), url=citation.url)
            for citation in document.citations
        ],
    )
