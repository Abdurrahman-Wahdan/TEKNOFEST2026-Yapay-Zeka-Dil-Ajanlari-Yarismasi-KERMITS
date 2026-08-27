from collections import Counter

import pytest

from dataprep.clean_migrated_qdrant import (
    audit_table_connections,
    choose_canonical,
    cleanup_plan,
    corpus_shape,
)


pytestmark = pytest.mark.unit


class Point:
    def __init__(self, point_id: str, text: str, *, removed: bool = False):
        self.id = point_id
        self.payload = {
            "page_content": text,
            "metadata": {
                "bank": "kuveytturk", "url": "https://bank.example/page",
                "type": "metin", "chunk_index": 0,
            },
        }
        if removed:
            self.payload["removed"] = True


def test_canonical_prefers_the_id_used_most_by_tables():
    refs = Counter({"b": 4, "a": 1})
    assert choose_canonical(["a", "b"], refs) == "b"


def test_cleanup_is_soft_and_ignores_already_removed_history():
    points = [Point("a", "same"), Point("b", "same"), Point("old", "same", removed=True)]
    redundant, mapping = cleanup_plan(points, Counter({"b": 2}))
    assert redundant == ["a"]
    assert mapping == {"a": "b"}


def test_corpus_shape_excludes_removed_history():
    report = corpus_shape([
        Point("a", "one"), Point("b", "two"), Point("old", "three", removed=True),
    ])
    assert report["active_points"] == 2
    assert report["active_documents"] == 1
    assert report["multi_chunk_documents"] == 1
    assert report["largest_document_chunks"] == 2
    assert report["incomplete_active_payload_ids"] == []


def test_table_connection_audit_covers_evidence_index_and_ui_url(tmp_path):
    table = {
        "id": "ornek-urun",
        "topic": "örnek ürün",
        "category": "ürün",
        "docstring": "Karşılaştırma açıklaması",
        "sources": {"kuveytturk": [{
            "point_id": "evidence-1", "url": "https://bank.example/page",
        }]},
        "cell_sources": {},
    }
    (tmp_path / "ornek-urun.json").write_text(
        __import__("json").dumps(table), encoding="utf-8")

    campaign = [Point("evidence-1", "source")]
    table_point = type("TablePoint", (), {
        "id": "table-1",
        "payload": {
            "id": "ornek-urun", "topic": "örnek ürün", "category": "ürün",
            "docstring": "Karşılaştırma açıklaması", "text": "embedded",
            "ui_url": "/tr/urunler?tablo=ornek-urun",
        },
    })()

    class Client:
        def scroll(self, **kwargs):
            assert kwargs["collection_name"] == "compare_tables"
            return [table_point], None

    report = audit_table_connections(Client(), campaign, tmp_path)
    assert report["table_files"] == 1
    assert report["table_index_points"] == 1
    assert report["table_evidence_missing_urls"] == []
    assert report["table_evidence_url_mismatches"] == []
    assert report["missing_table_index_ids"] == []
    assert report["bad_table_ui_urls"] == []
