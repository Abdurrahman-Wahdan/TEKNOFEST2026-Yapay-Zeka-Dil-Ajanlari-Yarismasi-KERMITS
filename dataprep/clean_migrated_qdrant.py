"""Validate and safely normalize the transferred ``campaigns`` collection.

The transferred snapshot preserves point IDs because comparison-table sources
refer to them. Consequently cleanup is *soft*: redundant active copies receive
``removed=true`` and remain resolvable by ID. Runtime retrieval excludes that
marker. No point is deleted and a Qdrant snapshot is created before ``--apply``.

Usage::

    python -m dataprep.clean_migrated_qdrant          # read-only plan
    python -m dataprep.clean_migrated_qdrant --apply  # snapshot + soft cleanup
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from qdrant_client import models

from vector_stores.client import get_qdrant_client
from api.table_links import ui_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLE_ROOT = PROJECT_ROOT / "TF26_data" / "data" / "_tables"
COLLECTION = "campaigns"
TABLES_COLLECTION = "compare_tables"

PAYLOAD_INDEXES = {
    "metadata.bank": models.PayloadSchemaType.KEYWORD,
    "metadata.url": models.PayloadSchemaType.KEYWORD,
    "metadata.type": models.PayloadSchemaType.KEYWORD,
    "metadata.gorsel_kaynak": models.PayloadSchemaType.KEYWORD,
    "metadata.validity_status": models.PayloadSchemaType.KEYWORD,
    "metadata.chunk_index": models.PayloadSchemaType.INTEGER,
    "removed": models.PayloadSchemaType.BOOL,
}


def table_point_references(root: Path = TABLE_ROOT) -> Counter[str]:
    """All point IDs cited by row and cell sources in the migrated tables."""
    references: Counter[str] = Counter()
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for sources in (data.get("sources") or {}).values():
            for source in sources or ():
                if source.get("point_id"):
                    references[str(source["point_id"])] += 1
        for columns in (data.get("cell_sources") or {}).values():
            for sources in (columns or {}).values():
                for source in sources or ():
                    if source.get("point_id"):
                        references[str(source["point_id"])] += 1
    return references


def fingerprint(payload: dict) -> str:
    """Exact evidence identity, excluding migration bookkeeping fields."""
    return json.dumps(
        {
            "page_content": payload.get("page_content") or "",
            "metadata": payload.get("metadata") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def choose_canonical(ids: list[str], references: Counter[str]) -> str:
    """Prefer the ID most used by tables; lexical order makes ties repeatable."""
    return min(ids, key=lambda point_id: (-references[point_id], point_id))


def scroll_collection(client, collection: str) -> list:
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            return points


def scroll_all(client) -> list:
    return scroll_collection(client, COLLECTION)


def audit_table_connections(client, campaign_points: list, root: Path = TABLE_ROOT) -> dict:
    """Verify disk tables, their evidence IDs, and their searchable index agree."""
    tables = [json.loads(path.read_text(encoding="utf-8"))
              for path in sorted(root.glob("*.json"))]
    evidence = []
    for table in tables:
        table_id = str(table.get("id") or "")
        for sources in (table.get("sources") or {}).values():
            evidence.extend((table_id, source) for source in sources or ())
        for columns in (table.get("cell_sources") or {}).values():
            for sources in (columns or {}).values():
                evidence.extend((table_id, source) for source in sources or ())

    campaign_by_id = {str(point.id): point.payload or {} for point in campaign_points}
    missing_urls = []
    url_mismatches = []
    source_urls = set()
    for table_id, source in evidence:
        point_id = str(source.get("point_id") or "")
        source_url = str(source.get("url") or "").strip()
        if source_url:
            source_urls.add(source_url)
        if urlsplit(source_url).scheme not in {"http", "https"}:
            missing_urls.append(f"{table_id}:{point_id}")
        payload = campaign_by_id.get(point_id) or {}
        point_url = str((payload.get("metadata") or {}).get("url") or "").strip()
        if point_id and point_url and source_url and point_url != source_url:
            url_mismatches.append(f"{table_id}:{point_id}")

    indexed_points = scroll_collection(client, TABLES_COLLECTION)
    indexed = {(point.payload or {}).get("id"): point.payload or {}
               for point in indexed_points if (point.payload or {}).get("id")}
    expected = {str(table.get("id") or ""): table for table in tables}
    missing_index_ids = sorted(set(expected) - set(indexed))
    extra_index_ids = sorted(set(indexed) - set(expected))
    bad_ui_urls = []
    incomplete_index_payloads = []
    for table_id, table in expected.items():
        payload = indexed.get(table_id)
        if payload is None:
            continue
        expected_url = ui_url(table_id, str(table.get("category") or ""))
        if payload.get("ui_url") != expected_url:
            bad_ui_urls.append(table_id)
        if any(not payload.get(field) for field in ("topic", "category", "docstring", "text")):
            incomplete_index_payloads.append(table_id)

    return {
        "table_files": len(tables),
        "table_evidence_records": len(evidence),
        "unique_table_source_urls": len(source_urls),
        "table_evidence_missing_urls": sorted(set(missing_urls)),
        "table_evidence_url_mismatches": sorted(set(url_mismatches)),
        "table_index_points": len(indexed_points),
        "missing_table_index_ids": missing_index_ids,
        "extra_table_index_ids": extra_index_ids,
        "bad_table_ui_urls": sorted(bad_ui_urls),
        "incomplete_table_index_payloads": sorted(incomplete_index_payloads),
    }


def cleanup_plan(points: list, references: Counter[str]) -> tuple[list[str], dict[str, str]]:
    """Return redundant active IDs and their preserved canonical equivalents."""
    groups: dict[str, list[str]] = defaultdict(list)
    for point in points:
        payload = point.payload or {}
        if payload.get("removed") is True:
            continue
        groups[fingerprint(payload)].append(str(point.id))

    duplicate_of: dict[str, str] = {}
    for ids in groups.values():
        if len(ids) < 2:
            continue
        canonical = choose_canonical(ids, references)
        duplicate_of.update({point_id: canonical for point_id in ids if point_id != canonical})
    return sorted(duplicate_of), duplicate_of


def corpus_shape(points: list) -> dict:
    """Active, duplicate-clean document/chunk counts for migration diagnostics."""
    documents: Counter[tuple[str, str, str]] = Counter()
    incomplete_payloads = []
    for point in points:
        payload = point.payload or {}
        if payload.get("removed") is True:
            continue
        meta = payload.get("metadata") or {}
        kind = str(meta.get("type") or "")
        page = meta.get("url")
        image = meta.get("gorsel_kaynak") if kind == "gorsel" else ""
        if (not page or (kind == "gorsel" and not image) or not kind
                or meta.get("chunk_index") is None or not meta.get("bank")):
            incomplete_payloads.append(str(point.id))
            continue
        documents[(str(page), str(image or ""), kind)] += 1
    multi = [count for count in documents.values() if count > 1]
    return {
        "active_points": sum(documents.values()),
        "active_documents": len(documents),
        "multi_chunk_documents": len(multi),
        "points_in_multi_chunk_documents": sum(multi),
        "largest_document_chunks": max(documents.values(), default=0),
        "incomplete_active_payload_ids": sorted(incomplete_payloads),
    }


def ensure_payload_indexes(client) -> list[str]:
    """Create the nested filter indexes used by specialist retrieval."""
    schema = client.get_collection(COLLECTION).payload_schema or {}
    created = []
    for field, kind in PAYLOAD_INDEXES.items():
        if field in schema:
            continue
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name=field,
            field_schema=kind,
            wait=True,
        )
        created.append(field)
    return created


def _batches(values: list[str], size: int = 256):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def run(*, apply: bool = False) -> dict:
    client = get_qdrant_client()
    references = table_point_references()
    points = scroll_all(client)
    redundant, duplicate_of = cleanup_plan(points, references)
    existing_removed = sum((point.payload or {}).get("removed") is True for point in points)
    missing_references = sorted(set(references) - {str(point.id) for point in points})

    report = {
        "collection": COLLECTION,
        "points_before": len(points),
        "existing_removed": existing_removed,
        "table_reference_ids": len(references),
        "missing_table_reference_ids": missing_references,
        "redundant_active_points": len(redundant),
        "duplicate_of": duplicate_of,
        "applied": apply,
    }
    report.update(corpus_shape(points))
    report.update(audit_table_connections(client, points))
    if not apply:
        return report

    snapshot = client.create_snapshot(collection_name=COLLECTION, wait=True)
    report["backup_snapshot"] = getattr(snapshot, "name", str(snapshot))
    for ids in _batches(redundant):
        client.set_payload(
            collection_name=COLLECTION,
            payload={"removed": True},
            points=ids,
            wait=True,
        )
    report["payload_indexes_created"] = ensure_payload_indexes(client)

    audit_dir = PROJECT_ROOT / "TF26_data" / "migration_audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ledger = audit_dir / f"campaigns-cleanup-{stamp}.json"
    ledger.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["ledger"] = str(ledger)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Snapshot then soft-mark duplicates.")
    args = parser.parse_args()
    report = run(apply=args.apply)
    printable = {key: value for key, value in report.items() if key != "duplicate_of"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
