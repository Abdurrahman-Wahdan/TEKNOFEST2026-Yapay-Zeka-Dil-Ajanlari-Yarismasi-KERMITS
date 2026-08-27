# Exporting tables and reports — design

**Date:** 2026-08-27
**Status:** agreed, implementing

## The problem

Tables are everywhere in TF26 and none of them leave the browser. Five surfaces
produce something a user would want to keep:

| Surface | Route | Renderer | Shape |
| --- | --- | --- | --- |
| Live comparison board | `/compare` | `Comparator` → `ProducedTable` | `ResolvedColumn[]` + `Row[]` |
| Comparison-table pool | `/urunler`, `/kampanyalar` | `CompareTablesBrowser` → `ProducedTable` | same |
| AI-saved tables | `/ai-overview` | `SavedViewsBoard` → `TableWidget` | same |
| Tables in chat answers | `/chat` | `MarkdownTable` (HAST → `TableProps`) | same, after parsing |
| Automation reports | `/profile/reports` | `ReportsBrowser` → `AgentMarkdown` | markdown + citations |

Four of the five already converge on one shape. That is what makes a single
export core possible instead of five.

## Decisions

1. **Formats.** Tables export as CSV, XLSX, PDF and DOCX. Reports export as PDF
   and DOCX only — a report is prose with tables in it, and a CSV of prose is a
   file nobody can use.
2. **Scope is the user's choice.** The dialog offers the visible table (filtered,
   sorted, visible columns) or the whole table. The toggle is *hidden* when the
   two are identical, because a control offering two identical outcomes is a
   control that lies.
3. **Generation is server-side.** One endpoint, one renderer.
4. **The control is a dialog**, opened from a button beside the existing
   `AttachButton` in `ProducedTable`'s trailing actions column.
5. **PDF and DOCX are branded and light.** Title block, accent header row, zebra
   rows, generated-at stamp, page numbers, citations as a source list. The app
   is dark; a dark PDF is a printer's tax.
6. **Citations always travel.** Each row's `cite_url` becomes a `kaynak` column
   in the data formats and a numbered source list in the documents. A rate
   without its source is an unverifiable number.
7. **One table at a time.** No multi-table workbook. It can be added on the same
   endpoint later without changing anything below.

### Why server-side

Three reasons, all specific to this repo.

- **Turkish typography.** jsPDF's built-in fonts are WinAnsi-encoded. `ğ ş ı İ`
  are not in that encoding, so a browser-side PDF needs a base64 TTF shipped in
  the bundle. Server-side it is a font stack resolved by fontconfig.
- **Excel and Turkish CSV.** Excel under a Turkish locale reads `;` as the
  delimiter and needs a UTF-8 BOM, or every `ş` arrives as mojibake. That
  belongs in one place with a test.
- **Reports are markdown.** `agents/main/prompt.py` *mandates* inline
  `[title](url)` links on every web-sourced claim, on top of headings, lists,
  tables and code blocks. Converting that to PDF/DOCX is a solved problem in
  Python and a hard one in the browser.

### Why HTML is the hub, not markdown

Markdown looks like the obvious intermediate — "md to pdf" libraries exist and
one of our two inlets is already markdown. It is the wrong hub, for one reason:
**markdown is untyped.**

A table cell here carries a type (`money` with a currency, `percent`, `date`,
`link`, `bool`), a tone, and a note. Render it to markdown and `₺1.234,56`
becomes a string — and an XLSX column of strings cannot be summed or sorted,
which is the entire reason to ship XLSX.

So markdown is one *inlet*, not the hub:

```
report markdown ──markdown-it-py──┐
                                  ├─→ HTML ─┬─ WeasyPrint ────────→ PDF
TableProps ──────Jinja2 template──┘         └─ pandoc -f html ────→ DOCX
                    │
                    ├── xlsxwriter → XLSX   (typed cells, never via markdown)
                    └── stdlib csv → CSV
```

One stylesheet then defines the branded look for both inlets, and WeasyPrint's
`@page` CSS gives page numbers, running headers and repeated table headers
across page breaks for free.

## The export core

`api/export/`. Everything pivots on one neutral shape that both inlets produce
and all four writers consume.

```python
@dataclass(frozen=True)
class Cell:
    value: str | float | bool | None   # typed, for XLSX
    display: str                       # what the screen showed, for PDF/DOCX
    type: str = "text"
    href: str = ""                     # link cells, and cite_url
    note: str = ""                     # cell_notes
    tone: str = ""                     # cell_tones
```

`Cell` carrying **both** `value` and `display` is the load-bearing decision.
XLSX writes `value` so the column stays arithmetic; PDF and DOCX write
`display` so the page reads exactly like the screen — `%2,89`, `₺1.234,56`,
Turkish decimal comma and all. No writer ever reformats a number, and neither
format has to do the other's job.

| Module | Job |
| --- | --- |
| `document.py` | The dataclasses. No I/O, no third-party imports. |
| `from_table.py` | `TableProps` → `ExportDocument`. Appends the `kaynak` column, reusing the key `api/saved_tables.py` already defines. |
| `from_report.py` | Report → `ExportDocument`. `markdown-it-py` renders prose to HTML; `\|` tables are lifted out as real `TableBlock`s so they stay tables in Word, not pictures of tables. |
| `html.py` + `templates/` + `assets/export.css` | `ExportDocument` → one HTML string. The hub. |
| `writers/*.py` | `ExportDocument` (or the HTML) → bytes. One module per format, and the only place that format is spoken. |
| `filename.py` | `konut-finansmani-20260827-1432.xlsx`, RFC 5987-encoded for Turkish titles. |

`from_table` and `from_report` are pure functions: testable with no database,
no browser and no language model.

## The endpoint

```
POST /api/export
  { format, source: {kind:"table", table} | {kind:"report", report_id} }
  → 200 <mime> + Content-Disposition: attachment; filename*=UTF-8''…
```

The table travels in the request body because the `/compare` board is assembled
client-side from live bank rates and **has no server-side identity**. Reports
travel by id because the server already holds them — no upload, and the export
cannot drift from what is stored.

**There is no `scope` field, and that is the point.** Sending the table settles
the scope question on the way out: the browser puts either the filtered, sorted,
visible rows or the whole table into `table`, and the server never learns which
it got because it has no filter state of its own to disagree with. A `scope`
enum on the wire would be a second description of a decision already made.

`format × kind` is validated in the schema: `kind:"report"` accepts only `pdf`
and `docx`. A CSV request for a report is a 422 with a reason, not a useless
file.

Authentication: `CurrentUser`. A report is checked for ownership the same way
`api/routers/automations.py::_own_report` does.

**Nothing here truncates.** No row cap, no cell-length cap. The table the user
exported is the table they get.

## The frontend

- `ExportDialog` — MUI `Dialog`, following `FeedbackDialog`'s shape and CSS
  variables. Format picker, scope toggle, row counts, download button.
- `useExportTable` — the hook every table call site uses, symmetric with the
  existing `useAttachTable`. It receives *both* the visible view and the full
  table, which is what makes the scope toggle possible: `ProducedTable` only
  ever sees the filtered, visible subset.
- `ExportButton` — lives inside `ProducedTable` next to `AttachButton`, so all
  four table surfaces get it without any call site opting in. That is the rule
  `ProducedTable` already states about row hover: a control added at the call
  sites is a control three of four pages forget.
- `MarkdownTable` gets its own button beside `SaveToDashboard`; a chat table
  becomes `TableProps` through the existing `tableFromHast`, so it enters through
  the *table* inlet like everything else.
- `ReportsBrowser`'s open report gets a PDF/DOCX button in its header.

## Failure

Downloads fail silently more often than anything else in a UI, so:

- The dialog holds a `saving` state and shows the failure inline, where the
  action was — the same choice `MarkdownTable`'s save already makes, because
  this app has no toast system.
- A missing `pandoc` at runtime returns a clear 503 naming the missing binary,
  matching the rule `requirements.txt` already states for poppler: refuse with a
  message rather than failing deep inside a subprocess.

## Dependencies

Added to `requirements.txt`:

- `xlsxwriter` — write-only, which is exactly this use. Number formats,
  hyperlinks, autofilter, frozen header.
- `weasyprint` — HTML/CSS → PDF. Needs pango/cairo from brew, documented the
  same way poppler and tesseract already are.
- `markdown-it-py` — already installed transitively; declared because we now
  depend on it directly.

`pandoc` is a system dependency, like poppler. Already present on the dev
machine at 3.8.2.1.

## Tests

- `test_export_document.py` — `TableProps` → `ExportDocument`: type carry-over,
  the `kaynak` column, an empty table, a row with no citation.
- `test_export_report.py` — markdown → blocks: prose stays prose, a markdown
  table becomes a `TableBlock`, inline links survive.
- `test_export_writers.py` — CSV delimiter and BOM; XLSX cells are numbers not
  strings; PDF is a PDF and contains Turkish glyphs; DOCX unzips to valid
  WordprocessingML.
- `test_export_api.py` — the format × kind matrix, ownership on reports, the
  `Content-Disposition` filename, 401 unauthenticated.
