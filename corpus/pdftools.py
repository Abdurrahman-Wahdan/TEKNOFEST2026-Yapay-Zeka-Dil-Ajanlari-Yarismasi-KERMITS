"""Poppler, wrapped.

`pdftotext`, `pdfinfo` and `pdftoppm` are called as subprocesses rather than
linked, which is both simpler and the reason there is no licence question: they
are GPL binaries, and running a program is not linking against it. The
alternative in-process library, PyMuPDF, is AGPL-3.0, whose network clause
reaches a service that merely serves users over a network — a real hazard for a
bank-facing product, and not worth it for a `subprocess.run`.

`pypdf` is deliberately not used. Measured on this corpus, it returned the whole
document body for every page: the 36-page `Genel Kredi Sözleşmesi` came out as
2,573,207 characters of which 107,572 were unique. `pdftotext` returns 110,948
characters at a 0.965 unique-line ratio — the same document, once.

Requires: brew install poppler
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Page separator in pdftotext's output.
PAGE_BREAK = "\f"

_INSTALL = "brew install poppler"


class PdfToolError(RuntimeError):
    """A poppler tool failed or is missing."""


def require(binary: str) -> str:
    """The path to a poppler binary.

    Raises:
        PdfToolError: naming the install command, rather than dying inside a
            subprocess call three hours into a crawl.
    """
    found = shutil.which(binary)
    if not found:
        raise PdfToolError(f"{binary} is not installed. Install with: {_INSTALL}")
    return found


def _run(argv: list[str], timeout: float = 120.0) -> bytes:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PdfToolError(f"{argv[0]} timed out after {timeout}s") from exc
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()[:200]
        raise PdfToolError(f"{argv[0]} exited {done.returncode}: {detail or 'no detail'}")
    return done.stdout


def text_pages(pdf: Path) -> list[str]:
    """The text layer, one string per page.

    Default reading-order mode, never `-layout`. On the two-column contracts
    `-layout` interleaves the columns onto shared lines, so every sentence
    becomes two unrelated halves and every chunk is nonsense.
    """
    out = _run([require("pdftotext"), "-enc", "UTF-8", "-q", str(pdf), "-"])
    text = out.decode("utf-8", "replace")
    pages = text.split(PAGE_BREAK)
    # pdftotext emits a trailing break, so the last split is an empty tail.
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def info(pdf: Path) -> dict[str, str]:
    """What `pdfinfo` reports, as a dict.

    `ModDate` is the point of this: it is present on ~94% of these files and is
    the only real per-document freshness signal the corpus has — the crawl date
    is identical for every document, so it says nothing about the document.
    """
    try:
        out = _run([require("pdfinfo"), "-enc", "UTF-8", str(pdf)], timeout=30.0)
    except PdfToolError as exc:
        logger.debug("pdfinfo failed for %s: %s", pdf, exc)
        return {}
    fields: dict[str, str] = {}
    for line in out.decode("utf-8", "replace").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def page_count(pdf: Path) -> int:
    try:
        return int(info(pdf).get("Pages", "0"))
    except ValueError:
        return 0


def render(pdf: Path, page: int, dpi: int = 200, gray: bool = True,
           crop: tuple[int, int, int, int] | None = None,
           scale_to: int | None = None) -> bytes:
    """One page as a PNG.

    Grayscale by default: these are black-ink scans, so colour is three times the
    bytes over the tunnel for nothing. 200 DPI because the vision encoder
    resamples to a fixed grid — a higher DPI costs raster time and bandwidth
    without giving the model more to read.

    Args:
        crop: `(x, y, width, height)` in pixels, for sending half a page at a
            time. Cropping happens here rather than in Python so no image
            library is needed.
        scale_to: Cap the longest side at this many pixels. A large-format page
            rendered at 200 DPI produces megabytes of PNG, and the vLLM host
            answers `413 Request Entity Too Large` -- measured on a 6.5 MB
            Kuveyt Türk brochure. Capping bounds the payload whatever the page
            size, and costs nothing in what the model can read: the vision
            encoder resamples to a fixed grid anyway.
    """
    argv = [require("pdftoppm"), "-png", "-r", str(dpi),
            "-f", str(page), "-l", str(page), "-singlefile"]
    if scale_to:
        argv += ["-scale-to", str(scale_to)]
    if gray:
        argv.append("-gray")
    if crop:
        x, y, width, height = crop
        argv += ["-x", str(x), "-y", str(y), "-W", str(width), "-H", str(height)]

    # Both ends of this call insist on real files.
    #
    # Input: fed a PDF on stdin, pdftoppm writes nothing and reports nothing.
    # Output: measured on poppler 25.10.0, it does not write an image to stdout
    # either -- it wants a filename prefix, and given "-" it cheerfully creates
    # a file called "-.png" and exits 0. Both failures are silent, which is why
    # this writes to a temporary directory and reads the file back.
    with tempfile.TemporaryDirectory() as scratch:
        prefix = Path(scratch) / "page"
        _run([*argv, str(pdf), str(prefix)])
        rendered = prefix.with_suffix(".png")
        if not rendered.exists():
            raise PdfToolError(
                f"pdftoppm produced no image for page {page} of {pdf.name}")
        return rendered.read_bytes()


def page_size(pdf: Path, dpi: int = 200) -> tuple[int, int]:
    """The first page's size in pixels at `dpi`, for computing crop boxes."""
    raw = info(pdf).get("Page size", "")
    try:
        # "595.276 x 841.89 pts (A4)"
        width, _, rest = raw.partition(" x ")
        height = rest.split(" ")[0]
        scale = dpi / 72.0
        return int(float(width) * scale), int(float(height) * scale)
    except (ValueError, IndexError):
        return 0, 0
