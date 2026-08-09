"""Fetch, clean and publish the corpus.

    python -m corpus.build                 # every site
    python -m corpus.build --site emlak    # one
    python -m corpus.build --no-fetch      # re-derive from stored bytes, offline
    python -m corpus.build --explain-pdfs  # print every PDF decision and stop

Exit codes match the health checker: 0 clean, 1 something failed or the run
refused to publish, 2 bad input.

The artifact is `clean/documents.jsonl`. It is written only if the gates pass,
so a bad night leaves yesterday's file in place rather than replacing it with a
shorter one -- whatever reads it next cannot tell a small corpus from a broken
crawl, so the pipeline must not offer it the choice.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, replace

from banks import clock
from config.settings import settings

from . import classify, clean, dates, fetch, pdf_extract, pdf_policy, store
from .extract import extract as extract_html
from .models import Document
from .report import BuildReport
from .sites import get_site, list_sites
from .urls import canonicalise, doc_id, is_pdf, text_hash

logger = logging.getLogger(__name__)

ARTIFACT = "clean/documents.jsonl"


def _document_from_page(url: str, record, site, html: str, report) -> Document | None:
    """Build a Document from a fetched HTML page, or refuse it with a reason."""
    parts = extract_html(html, url, site)

    if parts["is_toc"]:
        report.refuse(url, "looks like a crawler index, not a page")
        return None
    if clean.is_stub(parts["text"], settings.CORPUS_MIN_CHARS):
        report.refuse(url, f"under {settings.CORPUS_MIN_CHARS} characters")
        return None

    kind = classify.doc_kind(url)
    start, end, date_source = ("", "", "")
    if kind == "campaign":
        start, end, date_source = dates.extract(parts["text"])

    return Document(
        doc_id=doc_id(url), url=url, source_urls=(url,),
        site=site.slug, bank=site.display_name, source_type="page",
        fetched_at=record.fetched_at, content_hash=record.content_hash,
        text_hash=text_hash(parts["text"]), blob=record.blob,
        doc_kind=kind, section=classify.section_of(url),
        audience=classify.audience_of(url), category=classify.category_of(url),
        title=parts["title"], title_source=parts["title_source"],
        text=parts["text"], lang=parts["lang"], chars=len(parts["text"]),
        sections=parts["sections"],
        campaign_start=start, campaign_end=end, date_source=date_source,
        extraction_engine="html",
    )


def _document_from_pdf(url: str, record, site, decision, report) -> Document | None:
    """Build a Document from a selected PDF, or refuse it with a reason."""
    path = store.blob_file(record.blob)
    result = pdf_extract.extract(path, url)

    if result.error:
        report.refuse(url, result.error[:60])
        return None
    if result.suspect:
        report.refuse(url, "extraction looks repeated; unique-line ratio too low")
        return None
    if clean.is_stub(result.text, settings.CORPUS_MIN_CHARS):
        report.refuse(url, f"under {settings.CORPUS_MIN_CHARS} characters")
        return None

    kind = classify.doc_kind(url, decision.label)
    start, end, date_source = ("", "", "")
    if kind == "campaign":
        start, end, date_source = dates.extract(result.text)

    title = clean.title_from_slug(url)
    return Document(
        doc_id=doc_id(url), url=url, source_urls=(url,),
        site=site.slug, bank=site.display_name, source_type="pdf",
        fetched_at=record.fetched_at, content_hash=record.content_hash,
        text_hash=text_hash(result.text), blob=record.blob,
        doc_kind=kind, section=classify.section_of(url),
        audience=classify.audience_of(url), category=classify.category_of(url),
        title=title, title_source="filename",
        text=result.text, lang="tr", chars=len(result.text),
        pages=result.pages,
        campaign_start=start, campaign_end=end, date_source=date_source,
        extraction_engine=result.engine, page_count=result.page_count,
        classified_by=decision.decided_by, class_reason=decision.reason,
        low_confidence=result.low_confidence,
    )


def _pdf_context(records: dict, site) -> dict[str, tuple[str, str]]:
    """`{pdf_url: (anchor_text, referrer)}` from the pages that link each PDF."""
    context: dict[str, tuple[str, str]] = {}
    for page_url, record in records.items():
        if is_pdf(page_url) or not record.blob or "html" not in record.content_type:
            continue
        try:
            html = store.get(record.blob).decode("utf-8", errors="replace")
        except OSError:
            continue
        for href, anchor in fetch.links(html, page_url):
            if not is_pdf(href):
                continue
            target = canonicalise(href)
            if target and target not in context:
                context[target] = (anchor, page_url)
    return context


def run(sites: list[str] | None = None, limit: int | None = None,
        do_fetch: bool = True, write: bool = True,
        explain_pdfs: bool = False) -> BuildReport:
    """Build the corpus and return what happened."""
    report = BuildReport(started_at=clock.stamp())
    manifest = store.read_manifest()
    previous = _count_previous()
    report.previous_documents = previous

    documents: list[Document] = []

    for slug in (sites or list_sites()):
        site = get_site(slug)
        result = report.result(slug)
        started = time.time()

        if do_fetch:
            records = fetch.crawl(site, limit=limit, manifest=manifest)
        else:
            records = _records_from_manifest(manifest, site)

        result.fetched = len(records)
        context = _pdf_context(records, site)

        for url, record in sorted(records.items()):
            if record.error:
                result.errors += 1
                continue
            if not record.blob:
                continue

            was = manifest.get(url) or {}

            if is_pdf(url):
                result.pdfs_seen += 1
                anchor, referrer = context.get(url, ("", ""))
                decision = pdf_policy.decide(url, anchor, referrer)
                if decision.needs_model:
                    decision = pdf_policy.classify(
                        store.blob_file(record.blob), url, anchor)
                    result.pdfs_classified += 1
                if explain_pdfs:
                    verdict = "ACCEPT" if decision.accepted else "reject"
                    print(f"{verdict:7} {decision.label or '?':10} "
                          f"[{decision.decided_by or 'none':13}] {url[:76]}")
                    print(f"        {decision.reason[:100]}")
                    continue
                if not decision.accepted:
                    continue
                result.pdfs_selected += 1
                document = _document_from_pdf(url, record, site, decision, report)
            else:
                if explain_pdfs:
                    continue
                try:
                    html = store.get(record.blob).decode("utf-8", errors="replace")
                except OSError:
                    result.errors += 1
                    continue
                document = _document_from_page(url, record, site, html, report)

            if document is None:
                result.refused += 1
                continue

            # New / changed / unchanged is decided on the *text*, not the bytes.
            # Bank pages carry a rotating WAF token and an FX timestamp, so their
            # bytes churn every run while the words do not; counting that churn
            # as "changed" would re-embed most of the corpus nightly for nothing.
            previous_text_hash = was.get("text_hash")
            if not was:
                result.new += 1
            elif previous_text_hash != document.text_hash:
                result.changed += 1
                result.sections_changed += max(
                    len(document.sections) + len(document.pages), 1)
            else:
                result.unchanged += 1
                # Unchanged text means an unchanged document, byte for byte. The
                # raw bytes still churn -- a bank page carries a rotating WAF
                # token and an FX timestamp, so blob, content_hash and
                # fetched_at all move every run even when the words do not -- but
                # those are acquisition facts that belong to the manifest, not to
                # the published document. Carrying the first-seen values forward
                # is what lets the downstream embedder trust that an unchanged
                # text_hash means unchanged bytes and skip the work.
                document = replace(
                    document,
                    fetched_at=was.get("fetched_at", document.fetched_at),
                    content_hash=was.get("content_hash", document.content_hash),
                    blob=was.get("blob", document.blob))

            documents.append(document)
            result.documents += 1
            # The manifest records the blob the *document* points at, not the
            # freshly fetched one. When text is unchanged the document keeps the
            # first-seen blob, so the manifest must too -- otherwise garbage
            # collection would delete the blob every published document still
            # references, leaving the whole artifact dangling.
            entry = asdict(record)
            entry.update(content_hash=document.content_hash, blob=document.blob,
                         fetched_at=document.fetched_at,
                         text_hash=document.text_hash)
            manifest[url] = entry

        result.seconds = round(time.time() - started, 1)

    if explain_pdfs:
        return report

    today = clock.stamp()[:10]
    for document in documents:
        if document.doc_kind != "campaign":
            continue
        report.campaigns_total += 1
        if dates.is_active(document.campaign_end, today):
            report.campaigns_active += 1
        else:
            report.campaigns_expired += 1

    report.gate = report.check_gates()
    if report.gate:
        logger.error("Refusing to publish: %s", report.gate)
        return report

    if write:
        _write_artifact(documents)
        store.write_manifest(manifest)
        report.blobs_collected, report.bytes_freed = store.collect_garbage(manifest)
        report.written = True

    return report


def _write_artifact(documents: list[Document]) -> None:
    """Write documents.jsonl, sorted so two runs over the same store match."""
    lines = []
    for document in sorted(documents, key=lambda d: d.doc_id):
        payload = asdict(document)
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    store.write_text(ARTIFACT, "\n".join(lines) + "\n")


def _count_previous() -> int:
    path = store.root() / ARTIFACT
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
    except OSError:
        return 0


def _records_from_manifest(manifest: dict, site):
    """Rebuild RawDocs from the manifest, for an offline re-derive."""
    from .models import RawDoc
    from .urls import same_site

    out = {}
    for url, entry in manifest.items():
        if not isinstance(entry, dict) or not entry.get("blob"):
            continue
        if not same_site(url, site.root_domain):
            continue
        fields = {k: v for k, v in entry.items()
                  if k in RawDoc.__dataclass_fields__}
        out[url] = RawDoc(**fields)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpus.build",
        description="Fetch, clean and publish the bank corpus.")
    parser.add_argument("--site", action="append", dest="sites",
                        help="Only this site. Repeatable.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after this many URLs per site. For testing.")
    parser.add_argument("--no-fetch", action="store_true",
                        help="Re-derive from stored bytes without touching the network.")
    parser.add_argument("--no-write", action="store_true",
                        help="Report what would happen; write nothing.")
    parser.add_argument("--explain-pdfs", action="store_true",
                        help="Print every PDF selection decision and stop.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level="ERROR" if args.quiet else settings.LOG_LEVEL,
                        format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        report = run(sites=args.sites, limit=args.limit, do_fetch=not args.no_fetch,
                     write=not args.no_write, explain_pdfs=args.explain_pdfs)
    except ValueError as exc:            # an unknown site lists the valid ones
        print(exc)
        return 2

    if args.explain_pdfs:
        return 0
    if not args.quiet:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
              if args.json else report.text())
    return 0 if report.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
