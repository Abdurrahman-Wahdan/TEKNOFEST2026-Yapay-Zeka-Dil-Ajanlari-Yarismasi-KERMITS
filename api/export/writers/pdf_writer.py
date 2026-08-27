"""PDF, from the HTML hub, via WeasyPrint.

Three lines of code, and the reason it is three lines is the whole argument for
generating documents server-side from CSS rather than drawing them box by box in
the browser:

- **Turkish renders.** WeasyPrint resolves fonts through fontconfig, so `ğ ş ı İ`
  come out of whatever the system has. jsPDF's built-in fonts are WinAnsi and
  cannot encode those characters at all -- a browser-side PDF would need a TTF
  shipped in the JavaScript bundle before it could spell a single bank's name.
- **Page furniture is free.** Page numbers, the running title in the footer and
  the header band repeated on top of every page a long table breaks onto are all
  `@page` and `display: table-header-group` in `assets/export.css`. Each one is
  real work against a drawing API.
- **One stylesheet, both inlets.** A `/compare` table and an automation report
  arrive here as the same HTML, so they cannot drift into looking like two
  different products.
"""

from weasyprint import HTML

from ..document import ExportDocument
from ..html import render_html

MEDIA_TYPE = "application/pdf"
EXTENSION = "pdf"


def write_pdf(document: ExportDocument) -> bytes:
    """The document as a PDF.

    No `base_url`: the stylesheet is inlined by `render_html` and there are no
    images, so nothing in the page has a relative reference to resolve. Passing
    one would only give a malformed URL somewhere to be fetched from.
    """
    return HTML(string=render_html(document)).write_pdf()
