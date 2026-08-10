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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace

from banks import clock
from config.settings import settings

from . import classify, clean, dates, fetch, pdf_extract, pdf_policy, store
from .extract import extract as extract_html
from .models import Document
from .report import BuildReport, SiteResult
from .sites import get_site, list_sites
from .urls import canonicalise, doc_id, is_pdf, text_hash

logger = logging.getLogger(__name__)

ARTIFACT = "clean/documents.jsonl"
# Selected PDFs waiting for the slow OCR pass. Crawling the ten sites is minutes;
# OCR'ing their PDFs through the vision model is hours, so the two are split:
# `--pages-only` fills this queue and publishes the websites fast, and `--pdfs`
# drains it and merges the PDF documents in afterwards.
PDF_QUEUE = "clean/pdf_queue.jsonl"
# PDFs the OCR pass could not read, rewritten every run. They are already
# retried on their own -- nothing was cached, so they stay in the queue -- but
# an unread file that leaves no trace is indistinguishable from one that was
# never there, and this is the difference.
PDF_DEFERRED = "clean/pdf_deferred.jsonl"


def _document_from_page(url: str, record, site, html: str,
                        refusals: list) -> Document | None:
    """Build a Document from a fetched HTML page, or refuse it with a reason.

    Refusals go to a caller-owned list rather than the shared report, so this is
    safe to call from one thread per site.
    """
    parts = extract_html(html, url, site)

    if parts["is_toc"]:
        refusals.append((url, "looks like a crawler index, not a page"))
        return None
    if clean.is_stub(parts["text"], settings.CORPUS_MIN_CHARS):
        refusals.append((url, f"under {settings.CORPUS_MIN_CHARS} characters"))
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


def _document_from_pdf(url: str, record, site, decision,
                       refusals: list) -> Document | None:
    """Build a Document from a selected PDF, or refuse it with a reason.

    Refusals go to a caller-owned list, so this is safe to call concurrently.
    """
    path = store.blob_file(record.blob)
    result = pdf_extract.extract(path, url)

    # The only reason to drop a PDF here is that reading it failed. A short one,
    # a repetitive one, an almost-empty one -- all still get written. Whether a
    # file is wanted at all was already decided by relevance, upstream, and a
    # length gate applied afterwards only ever threw away real documents: the
    # stub floor alone refused a 3.4 MB fund brochure that reads perfectly.
    if result.error:
        refusals.append((url, result.error[:60]))
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


def _is_pdf_record(url: str, record) -> bool:
    """Whether this record is a PDF, by URL extension or by its content type.

    The content type matters because some banks serve PDFs at extensionless
    URLs; `fetch` sniffs the bytes and stamps the record `application/pdf`, and
    routing on that is what keeps such a file out of the HTML parser.
    """
    return is_pdf(url) or "pdf" in (record.content_type or "").lower()


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


def _process_one_site(slug: str, *, manifest: dict, limit: int | None,
                      do_fetch: bool, explain_pdfs: bool, pages_only: bool) -> dict:
    """Crawl and process one site, entirely in its own accumulators.

    Returns everything the caller needs to merge -- documents, manifest updates,
    the PDF queue, refusals and the SiteResult -- so nothing is shared between
    the threads that run this, one per bank.
    """
    site = get_site(slug)
    result = SiteResult(site=slug)
    documents: list[Document] = []
    queue: list[dict] = []
    refusals: list[tuple[str, str]] = []
    manifest_updates: dict = {}
    started = time.time()

    records = (fetch.crawl(site, limit=limit, manifest=manifest) if do_fetch
               else _records_from_manifest(manifest, site))
    result.fetched = len(records)
    context = _pdf_context(records, site)

    for url, record in sorted(records.items()):
        if record.error:
            result.errors += 1
            continue
        if not record.blob:
            continue

        was = manifest.get(url) or {}

        if _is_pdf_record(url, record):
            result.pdfs_seen += 1
            anchor, referrer = context.get(url, ("", ""))
            if pages_only and not explain_pdfs:
                queue.append({"url": url, "site": slug, "blob": record.blob,
                              "content_hash": record.content_hash,
                              "fetched_at": record.fetched_at,
                              "anchor": anchor, "referrer": referrer})
                continue
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
            try:
                document = _document_from_pdf(url, record, site, decision, refusals)
            except pdf_extract.TransientExtractionError as exc:
                # One unreachable model must not end a site's crawl. The PDF is
                # left unpublished and unrecorded, so the next run retries it.
                logger.warning("deferring %s: %s", url, exc)
                result.errors += 1
                continue
        else:
            if explain_pdfs:
                continue
            try:
                html = store.get(record.blob).decode("utf-8", errors="replace")
            except OSError:
                result.errors += 1
                continue
            document = _document_from_page(url, record, site, html, refusals)

        if document is None:
            result.refused += 1
            continue

        previous_text_hash = was.get("text_hash")
        if not was:
            result.new += 1
        elif previous_text_hash != document.text_hash:
            result.changed += 1
            result.sections_changed += max(
                len(document.sections) + len(document.pages), 1)
        else:
            result.unchanged += 1
            document = replace(
                document,
                fetched_at=was.get("fetched_at", document.fetched_at),
                content_hash=was.get("content_hash", document.content_hash),
                blob=was.get("blob", document.blob))

        documents.append(document)
        result.documents += 1
        entry = asdict(record)
        entry.update(content_hash=document.content_hash, blob=document.blob,
                     fetched_at=document.fetched_at, text_hash=document.text_hash)
        # Keyed by url; merged into the shared manifest after the parallel phase.
        manifest_updates[url] = entry

    result.seconds = round(time.time() - started, 1)
    return {"slug": slug, "result": result, "documents": documents,
            "queue": queue, "refusals": refusals,
            "manifest_updates": manifest_updates}


def run(sites: list[str] | None = None, limit: int | None = None,
        do_fetch: bool = True, write: bool = True,
        explain_pdfs: bool = False, pages_only: bool = False) -> BuildReport:
    """Build the corpus and return what happened.

    With `pages_only`, PDFs are fetched and their bytes stored, but instead of
    being OCR'd they are recorded to the PDF queue for the separate `--pdfs`
    pass. This publishes the websites in minutes rather than waiting hours for
    the vision model to read every PDF.
    """
    report = BuildReport(started_at=clock.stamp())
    manifest = store.read_manifest()
    report.previous_documents = _count_previous(exclude_pdf=pages_only)

    documents: list[Document] = []
    queue: list[dict] = []

    # All ten banks are crawled at once. Each is a different server, so running
    # them in parallel does not raise the request rate any single host sees --
    # each still gets only CORPUS_CONCURRENCY connections -- while the whole crawl
    # finishes in about the time the slowest bank takes rather than the sum of
    # all ten. explain_pdfs prints as it goes, so it stays single-threaded to
    # keep its output readable.
    slugs = sites or list_sites()
    opts = dict(limit=limit, do_fetch=do_fetch, explain_pdfs=explain_pdfs,
                pages_only=pages_only, manifest=manifest)

    if explain_pdfs or len(slugs) == 1:
        outputs = [_process_one_site(slug, **opts) for slug in slugs]
    else:
        with ThreadPoolExecutor(max_workers=settings.CORPUS_SITE_WORKERS) as pool:
            futures = {pool.submit(_process_one_site, slug, **opts): slug
                       for slug in slugs}
            outputs = [f.result() for f in as_completed(futures)]

    if explain_pdfs:
        return report

    # Merge the per-site results. Each site produced its own documents, manifest
    # updates and refusals, so nothing was shared across threads and there is
    # nothing to lock -- the merge is a plain fold. Manifest keys never collide
    # because sites are on different domains.
    for out in outputs:
        report.sites[out["slug"]] = out["result"]
        documents.extend(out["documents"])
        queue.extend(out["queue"])
        report.refusals.extend(out["refusals"])
        manifest.update(out["manifest_updates"])

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
        payloads = [asdict(d) for d in documents]
        # pages_only keeps the previously-published PDF documents in place and
        # replaces only the HTML ones, so a fast website refresh never wipes the
        # PDFs the slow pass produced. A full run owns the whole artifact.
        if pages_only:
            payloads = _merge(payloads, keep=lambda d: d.get("source_type") == "pdf")
            _write_queue(queue)
            report.pdfs_selected = len(queue)
        _write_artifact(payloads)
        store.write_manifest(manifest)
        report.blobs_collected, report.bytes_freed = store.collect_garbage(manifest)
        report.written = True

    return report


def _write_artifact(payloads: list[dict]) -> None:
    """Write documents.jsonl, sorted so two runs over the same store match."""
    lines = [json.dumps(p, ensure_ascii=False, sort_keys=True)
             for p in sorted(payloads, key=lambda p: p["doc_id"])]
    store.write_text(ARTIFACT, "\n".join(lines) + "\n")


def _read_artifact() -> list[dict]:
    """The current documents.jsonl as a list of payloads, or [] if none."""
    path = store.root() / ARTIFACT
    try:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()
                if line.strip()]
    except OSError:
        return []


def _merge(new_payloads: list[dict], keep) -> list[dict]:
    """Combine freshly built documents with existing ones worth keeping.

    A fresh document replaces the stored one of the same doc_id; stored
    documents that `keep` accepts and were not rebuilt are carried through. Used
    so the fast pages pass replaces the HTML documents without wiping the PDF
    documents the slow pass produced.
    """
    fresh_ids = {p["doc_id"] for p in new_payloads}
    kept = [r for r in _read_artifact()
            if r.get("doc_id") not in fresh_ids and keep(r)]
    return new_payloads + kept


def _write_queue(entries: list[dict]) -> None:
    lines = [json.dumps(e, ensure_ascii=False, sort_keys=True)
             for e in sorted(entries, key=lambda e: e["url"])]
    store.write_text(PDF_QUEUE, "\n".join(lines) + "\n")


def _read_queue() -> list[dict]:
    path = store.root() / PDF_QUEUE
    try:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()
                if line.strip()]
    except OSError:
        return []


def process_pdfs(write: bool = True) -> BuildReport:
    """Drain the PDF queue: classify, OCR, and merge into documents.jsonl.

    The slow half of the pipeline, kept separate so the websites publish fast.
    Reads what `--pages-only` queued, runs each selected PDF through the vision
    model, and adds the PDF documents to the artifact the pages pass wrote --
    the HTML documents are left untouched.
    """
    report = BuildReport(started_at=clock.stamp())
    queue = _read_queue()
    if not queue:
        logger.info("PDF queue is empty; nothing to OCR.")
        return report

    manifest = store.read_manifest()
    pdf_documents: list[dict] = []

    # OCR is the slow part -- a handful of vision calls per PDF -- so the queue is
    # drained in parallel, bounded by CORPUS_PDF_WORKERS. The vLLM host batches
    # concurrent requests, so several PDFs in flight keep it busy. Each worker
    # owns its own accumulators; the merge below is single-threaded.
    total = len(queue)
    done = from_cache = 0
    with ThreadPoolExecutor(max_workers=settings.CORPUS_PDF_WORKERS) as pool:
        for out in pool.map(_process_one_pdf, queue):
            done += 1
            from_cache += out["cached"]
            if done % 50 == 0:
                logger.info("OCR pass: %d / %d PDFs (%d from cache, %d fresh)",
                            done, total, from_cache, done - from_cache)
            result = report.result(out["slug"])
            result.pdfs_seen += 1
            result.pdfs_classified += out["classified"]
            result.errors += out["errors"]
            result.refused += out["refused"]
            report.refusals.extend(out["refusals"])
            if out["deferred"]:
                report.deferred.append(out["deferred"])
            if out["document"] is not None:
                result.pdfs_selected += 1
                result.documents += 1
                pdf_documents.append(out["document"])
                url, entry = out["manifest"]
                manifest[url] = entry
            elif out["selected"]:
                result.pdfs_selected += 1

    today = clock.stamp()[:10]
    for payload in pdf_documents:
        if payload.get("doc_kind") != "campaign":
            continue
        report.campaigns_total += 1
        if dates.is_active(payload.get("campaign_end", ""), today):
            report.campaigns_active += 1
        else:
            report.campaigns_expired += 1

    if write:
        # Always rewritten, empty included: this file answers "what is still
        # owed?", and a stale list from an earlier run would answer it wrongly.
        # These PDFs are already retried automatically, since refusing to cache
        # them is what leaves them in the queue -- this is the record that says
        # how many, which ones, and why.
        store.write_text(PDF_DEFERRED, "".join(
            json.dumps({"url": url, "reason": reason}, ensure_ascii=False) + "\n"
            for url, reason in report.deferred))

    if write and pdf_documents:
        # Keep every HTML document; replace only the PDF documents. Adding PDFs
        # can only grow the corpus, so the shrink gate does not apply here.
        merged = _merge(pdf_documents, keep=lambda d: d.get("source_type") != "pdf")
        _write_artifact(merged)
        store.write_manifest(manifest)
        report.written = True

    if report.deferred:
        logger.warning("%d PDF(s) deferred and still owed a reading; see %s",
                       len(report.deferred), PDF_DEFERRED)
    return report


def _process_one_pdf(item: dict) -> dict:
    """Classify and OCR one queued PDF, in its own accumulators.

    Returns the counters and the built document (or None) for the caller to
    merge, so this is safe to run many at a time. A blob that vanished, a
    rejected PDF and a refused extraction are all reported, never crashed on.
    """
    out = {"slug": item["site"], "classified": 0, "selected": 0, "errors": 0,
           "refused": 0, "cached": 0, "document": None, "manifest": None,
           "refusals": [], "deferred": None}
    blob = item["blob"]
    content_hash = item.get("content_hash", "")
    url = item["url"]

    # Resume from the cache: an already-processed PDF (same bytes) skips the
    # classifier and the vision model entirely. This is what makes the pass
    # observable -- count the cache -- and safe to kill and restart.
    cached = store.read_pdf_doc(content_hash) if content_hash else None
    if cached is not None:
        out["cached"] = 1
        if cached.get("document"):
            out["selected"] = 1
            out["document"] = cached["document"]
            out["manifest"] = (url, {"content_hash": content_hash,
                                     "text_hash": cached["document"].get("text_hash", ""),
                                     "blob": blob, "fetched_at": item.get("fetched_at", "")})
        return out

    if not store.has(blob):
        out["errors"] = 1
        return out

    site = get_site(item["site"])
    anchor = item.get("anchor", "")
    decision = pdf_policy.decide(url, anchor, item.get("referrer", ""))
    if decision.needs_model:
        decision = pdf_policy.classify(store.blob_file(blob), url, anchor)
        out["classified"] = 1

    record = _record_from_queue(item)
    document = None
    if decision.accepted:
        out["selected"] = 1
        refusals: list = []
        try:
            document = _document_from_pdf(url, record, site, decision, refusals)
        except pdf_extract.TransientExtractionError as exc:
            # The model or the tunnel failed, which says nothing about this PDF.
            # Returning before the cache write is the whole point: a verdict
            # stored now would be permanent, and the next run would skip a file
            # we never actually read.
            logger.warning("deferring %s: %s", url, exc)
            out["errors"] = 1
            out["deferred"] = (url, str(exc)[:160])
            return out
        out["refusals"] = refusals
        if document is None:
            out["refused"] = 1

    # Cache the outcome either way -- a rejected PDF must not be re-classified
    # through the model on the next run any more than an accepted one is re-OCR'd.
    doc_dict = asdict(document) if document is not None else None
    if content_hash:
        store.write_pdf_doc(content_hash, {
            "accepted": decision.accepted, "label": decision.label,
            "document": doc_dict})
    if doc_dict is not None:
        out["document"] = doc_dict
        out["manifest"] = (url, {**asdict(record), "text_hash": document.text_hash})
    return out


def _record_from_queue(item: dict):
    """Rebuild a RawDoc from a queue entry, for the OCR pass."""
    from .models import RawDoc
    return RawDoc(
        url=item["url"], fetched_at=item.get("fetched_at", ""), status=200,
        content_type="application/pdf", content_hash=item.get("content_hash", ""),
        blob=item["blob"])


def _count_previous(exclude_pdf: bool = False) -> int:
    """How many documents the last run published.

    `exclude_pdf` counts only the HTML documents, so the pages pass compares
    like with like: it replaces the HTML and keeps the PDFs, so counting the
    kept PDFs against it would read as a shrink and trip the gate for nothing.
    """
    if not exclude_pdf:
        path = store.root() / ARTIFACT
        try:
            return sum(1 for line in path.read_text("utf-8").splitlines() if line)
        except OSError:
            return 0
    return sum(1 for p in _read_artifact() if p.get("source_type") != "pdf")


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
    parser.add_argument("--pages-only", action="store_true",
                        help="Crawl and publish the websites fast; queue the PDFs "
                             "for the separate --pdfs pass instead of OCR'ing them.")
    parser.add_argument("--pdfs", action="store_true",
                        help="Drain the PDF queue: OCR the selected PDFs and merge "
                             "them into the artifact. The slow pass.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level="ERROR" if args.quiet else settings.LOG_LEVEL,
                        format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # trafilatura warns "empty link" / "missing link attribute" for every anchor
    # with no text or a javascript: href -- hundreds per run, all harmless (an
    # empty link carries no content to keep). Silence it so real warnings show.
    for chatty in ("trafilatura", "trafilatura.core", "trafilatura.utils"):
        logging.getLogger(chatty).setLevel(logging.ERROR)

    try:
        if args.pdfs:
            report = process_pdfs(write=not args.no_write)
        else:
            report = run(sites=args.sites, limit=args.limit, do_fetch=not args.no_fetch,
                         write=not args.no_write, explain_pdfs=args.explain_pdfs,
                         pages_only=args.pages_only)
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
