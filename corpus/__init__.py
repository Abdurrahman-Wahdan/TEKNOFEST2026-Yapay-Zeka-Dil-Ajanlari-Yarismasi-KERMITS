"""The crawled bank corpus: fetch, clean and standardise, ready to be embedded.

    from corpus import run

    report = run(sites=["emlak"], limit=50)
    print(report.text())
"""

from .build import run
from .models import Block, Document, Page, RawDoc, Section, Site
from .report import BuildReport, SiteResult
from .sites import SITES, get_site, list_sites
from .store import clear_cache
from .urls import canonicalise, doc_id, is_pdf, same_site, text_hash

__all__ = [
    "Block",
    "BuildReport",
    "Document",
    "Page",
    "RawDoc",
    "SITES",
    "Section",
    "Site",
    "SiteResult",
    "canonicalise",
    "clear_cache",
    "doc_id",
    "get_site",
    "is_pdf",
    "list_sites",
    "run",
    "same_site",
    "text_hash",
]
