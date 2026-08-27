"""Regression checks for the transferred campaigns snapshot's retrieval rules."""

import pytest
from qdrant_client import models

from corpus import search


pytestmark = pytest.mark.unit


class Point:
    def __init__(self, pid: str, text: str, *, kind: str = "metin", image: str = "",
                 page: str = "https://bank.example/page", removed: bool = False):
        metadata = {
            "bank": "vakifkatilim",
            "url": page,
            "type": kind,
            "chunk_index": 0,
        }
        if image:
            metadata["gorsel_kaynak"] = image
        self.id = pid
        self.payload = {"page_content": text, "metadata": metadata}
        if removed:
            self.payload["removed"] = True


def test_soft_removed_and_exact_duplicate_points_are_not_returned_twice():
    original = Point("original", "aynı kanıt")
    duplicate = Point("duplicate", "aynı kanıt")
    removed = Point("removed", "eski kanıt", removed=True)

    assert search._unique_points([original, duplicate, removed]) == [original]


def test_each_image_is_its_own_document_even_on_the_same_page():
    first = Point("a", "ilk", kind="gorsel", image="https://img.example/a.png")
    second = Point("b", "ikinci", kind="gorsel", image="https://img.example/b.png")

    first_meta = first.payload["metadata"]
    second_meta = second.payload["metadata"]
    assert search._document_key(first_meta) != search._document_key(second_meta)


def test_a_shared_image_on_different_pages_stays_in_separate_documents():
    first = Point("a", "ilk", kind="gorsel", image="https://img.example/logo.png",
                  page="https://bank.example/one")
    second = Point("b", "ikinci", kind="gorsel", image="https://img.example/logo.png",
                   page="https://bank.example/two")
    assert search._document_key(first.payload["metadata"]) != search._document_key(
        second.payload["metadata"])


def test_every_bank_filter_excludes_the_literal_removed_marker():
    flt = search._bank_filter("vakifkatilim")
    assert flt.must_not == [
        models.FieldCondition(
            key="removed", match=models.MatchValue(value=True)
        )
    ]
