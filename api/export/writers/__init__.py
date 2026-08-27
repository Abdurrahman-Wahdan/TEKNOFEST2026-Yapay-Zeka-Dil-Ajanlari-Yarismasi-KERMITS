"""One writer per format, and the table that picks between them.

Each writer is the only place its format is spoken. The dispatch is data rather
than a chain of `if format ==` so that adding a fifth format is one entry here
and one module beside it -- and so the router, the schema and the frontend all
read the same list instead of three copies that can disagree.
"""

from dataclasses import dataclass
from typing import Callable

from ..document import ExportDocument
from .csv_writer import EXTENSION as CSV_EXT, MEDIA_TYPE as CSV_MIME, write_csv
from .docx_writer import EXTENSION as DOCX_EXT, MEDIA_TYPE as DOCX_MIME, write_docx
from .pdf_writer import EXTENSION as PDF_EXT, MEDIA_TYPE as PDF_MIME, write_pdf
from .xlsx_writer import EXTENSION as XLSX_EXT, MEDIA_TYPE as XLSX_MIME, write_xlsx


@dataclass(frozen=True)
class Writer:
    write: Callable[[ExportDocument], bytes]
    media_type: str
    extension: str


WRITERS: dict[str, Writer] = {
    "csv": Writer(write_csv, CSV_MIME, CSV_EXT),
    "xlsx": Writer(write_xlsx, XLSX_MIME, XLSX_EXT),
    "pdf": Writer(write_pdf, PDF_MIME, PDF_EXT),
    "docx": Writer(write_docx, DOCX_MIME, DOCX_EXT),
}

__all__ = ["WRITERS", "Writer"]
