"""Live checks against local Qdrant.

Start it with:
    docker run -d --name qdrant -p 6333:6333 \
      -v "$HOME/qdrant_storage:/qdrant/storage" qdrant/qdrant
"""

import uuid

import pytest
from langchain_core.embeddings import Embeddings

from vector_stores import ensure_collection, get_qdrant_client, get_vector_store

pytestmark = pytest.mark.integration

DIMS = 8


class FakeEmbeddings(Embeddings):
    """Deterministic vectors, so the store is tested without loading a model."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = sum(ord(c) for c in text)
        return [((seed + i) % 10) / 10 for i in range(DIMS)]


@pytest.fixture
def collection(qdrant_available):
    """A throwaway collection, removed afterwards."""
    if not qdrant_available:
        pytest.skip("Qdrant is not running on localhost:6333")

    name = f"test_{uuid.uuid4().hex[:8]}"
    ensure_collection(name, dimensions=DIMS)
    yield name
    get_qdrant_client().delete_collection(name)


def test_ensure_collection_creates_once(collection):
    """Second call is a no-op, not an error."""
    assert ensure_collection(collection, dimensions=DIMS) is False


def test_dimension_mismatch_is_refused(collection):
    """Writing wrong-sized vectors fails late and confusingly, so block it."""
    with pytest.raises(ValueError, match="dim"):
        ensure_collection(collection, dimensions=DIMS + 1)


def test_write_search_delete_round_trip(collection):
    store = get_vector_store(collection, FakeEmbeddings())

    ids = store.add_texts(
        ["kâr payı oranı %1,89", "konut finansmanı 120 ay"],
        metadatas=[{"kind": "rate"}, {"kind": "term"}],
    )
    assert len(ids) == 2

    hits = store.similarity_search("kâr payı oranı %1,89", k=1)
    assert hits and hits[0].metadata["kind"] == "rate"

    store.delete(ids)
    assert get_qdrant_client().count(collection).count == 0
