"""Sync documents.jsonl into Qdrant, embedding only what changed.

    python -m index                 # nightly sync
    python -m index --no-write      # show the diff, embed and write nothing
    python -m index --json

The whole point is to do the least work: read the artifact, compare each chunk's
`text_hash` against what the collection already holds, and embed only the new or
changed ones. A night where nothing changed makes zero embedding calls and zero
writes — the only cost is one scroll over the stored hashes.

Deletes are gated: a run that would remove more than a fifth of the index (a
truncated artifact, a half-finished corpus build) refuses and writes nothing,
the same guard the corpus build uses against a bad crawl.
"""

import argparse
import json
import logging
import sys

from banks import clock
from config.settings import settings
from corpus import dates, store
from embeddings import get_embedding
from vector_stores import ensure_collection
from vector_stores.client import get_qdrant_client

from .chunk import chunks, linked_from_map
from .models import Chunk
from .payload import PAYLOAD_INDEXES, point_id
from .report import IndexReport

logger = logging.getLogger(__name__)

ARTIFACT = "clean/documents.jsonl"


def _read_documents(artifact: str | None) -> list[dict]:
    path = store.root() / (artifact or ARTIFACT)
    try:
        return [json.loads(line) for line in path.read_text("utf-8").splitlines()
                if line.strip()]
    except OSError as exc:
        raise FileNotFoundError(
            f"No corpus artifact at {path}. Run `python -m corpus` first.") from exc


def _flatten(documents: list[dict]) -> dict[str, Chunk]:
    """Every document's chunks, keyed by point id."""
    linked = linked_from_map(documents)
    out: dict[str, Chunk] = {}
    for document in documents:
        for chunk in chunks(document, linked.get(document.get("doc_id", ""), ())):
            out[point_id(chunk.chunk_id)] = chunk
    return out


def _held_hashes(client, collection: str) -> dict[str, str]:
    """`{point_id: text_hash}` for every point already in the collection.

    One scroll, payload only (`text_hash`), no vectors — cheap even at tens of
    thousands of points, and enough to tell an unchanged chunk from a changed one.
    """
    held: dict[str, str] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, with_payload=["text_hash"],
            with_vectors=False, limit=1000, offset=offset)
        for p in points:
            held[str(p.id)] = (p.payload or {}).get("text_hash", "")
        if offset is None:
            return held


def _ensure(collection: str) -> None:
    """Create the collection and its payload indexes if absent (idempotent)."""
    ensure_collection(collection, settings.EMBEDDING_DIMENSIONS)
    from qdrant_client.models import PayloadSchemaType

    schema = {"keyword": PayloadSchemaType.KEYWORD,
              "bool": PayloadSchemaType.BOOL,
              "datetime": PayloadSchemaType.DATETIME}
    client = get_qdrant_client()
    for field, kind in PAYLOAD_INDEXES.items():
        try:
            client.create_payload_index(collection, field, schema[kind])
        except Exception:  # noqa: BLE001 - already-exists is the normal case
            logger.debug("payload index %s already present", field)


def _tally(report: IndexReport, today: dict[str, Chunk]) -> None:
    """Fill the per-kind / per-source / campaign counts from today's chunks."""
    for chunk in today.values():
        payload = chunk.payload
        report.by_source[payload["source_type"]] = report.by_source.get(
            payload["source_type"], 0) + 1
        report.by_kind[payload["doc_kind"]] = report.by_kind.get(
            payload["doc_kind"], 0) + 1
        if payload["doc_kind"] == "campaign":
            if dates.is_active(payload.get("campaign_end", "")):
                report.campaigns_active += 1
            else:
                report.campaigns_expired += 1


def run(artifact: str | None = None, write: bool = True,
        collection: str | None = None) -> IndexReport:
    """Sync the artifact into Qdrant and return what happened."""
    report = IndexReport(started_at=clock.stamp())
    collection = collection or settings.QDRANT_COLLECTION_CHUNKS

    today = _flatten(_read_documents(artifact))
    report.chunks_total = len(today)
    _tally(report, today)

    _ensure(collection)
    client = get_qdrant_client()
    held = _held_hashes(client, collection)
    report.held = len(held)

    to_embed = [(pid, chunk) for pid, chunk in today.items()
                if held.get(pid) != chunk.text_hash]
    to_delete = [pid for pid in held if pid not in today]
    report.skipped = len(today) - len(to_embed)

    report.gate = report.check_gate(len(to_delete))
    if report.gate:
        logger.error("Refusing to sync: %s", report.gate)
        return report

    if write and to_embed:
        from qdrant_client.models import PointStruct

        # Embed and upsert in batches, never all at once. Each batch lands in
        # Qdrant before the next is embedded, which buys three things: the point
        # count is live progress you can watch, a killed run keeps everything it
        # had already written, and a restart resumes -- the text_hash diff above
        # sees those points as unchanged and skips them. Embedding 20,000 chunks
        # into memory first meant an hour of work with nothing on disk and no way
        # to tell how far along it was.
        embedding = get_embedding()
        batch = settings.INDEX_EMBED_BATCH
        for start in range(0, len(to_embed), batch):
            window = to_embed[start:start + batch]
            vectors = embedding.embed_documents([c.embed_text for _, c in window])
            client.upsert(collection_name=collection, points=[
                PointStruct(id=pid, vector=vector, payload=chunk.payload)
                for (pid, chunk), vector in zip(window, vectors)])
            report.embedded += len(window)
            logger.info("embedded %d / %d chunks", report.embedded, len(to_embed))

    if write and to_delete:
        from qdrant_client.models import PointIdsList

        client.delete(collection_name=collection,
                      points_selector=PointIdsList(points=to_delete))
        report.deleted = len(to_delete)

    report.written = write and bool(to_embed or to_delete)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m index",
        description="Embed the corpus artifact into Qdrant, incrementally.")
    parser.add_argument("--artifact", default=None,
                        help="Path (under the corpus root) to documents.jsonl.")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--no-write", action="store_true",
                        help="Show the diff; embed and write nothing.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level="ERROR" if args.quiet else settings.LOG_LEVEL,
                        format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "urllib3", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        report = run(artifact=args.artifact, write=not args.no_write,
                     collection=args.collection)
    except FileNotFoundError as exc:
        print(exc)
        return 2

    if not args.quiet:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
              if args.json else report.text())
    return 0 if report.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
