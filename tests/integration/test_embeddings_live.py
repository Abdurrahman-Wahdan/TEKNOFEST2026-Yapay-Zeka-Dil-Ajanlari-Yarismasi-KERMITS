"""Live check of the local embedding model.

Skipped unless the model is already in the HuggingFace cache — no test should
trigger a multi-GB download. Warm the cache first:

    python -c "from embeddings import get_embedding; get_embedding()"
"""

import pytest

from config.settings import settings
from embeddings import get_embedding

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _model_is_cached(model: str) -> bool:
    from pathlib import Path

    cache = Path.home() / ".cache" / "huggingface" / "hub"
    folder = "models--" + model.replace("/", "--")
    return (cache / folder).exists()


@pytest.fixture(scope="module")
def embedding():
    if not _model_is_cached(settings.EMBEDDING_MODEL):
        pytest.skip(f"{settings.EMBEDDING_MODEL} is not downloaded yet")
    return get_embedding()


def test_query_vector_matches_configured_dimensions(embedding):
    """A mismatch here means every collection is created with the wrong size."""
    vector = embedding.embed_query("kâr payı oranı nedir?")
    assert len(vector) == settings.EMBEDDING_DIMENSIONS


def test_documents_embed_in_batch(embedding):
    vectors = embedding.embed_documents(["konut finansmanı", "alışveriş finansmanı"])
    assert len(vectors) == 2
    assert all(len(v) == settings.EMBEDDING_DIMENSIONS for v in vectors)


def test_related_text_scores_above_unrelated(embedding):
    """Sanity check that the model encodes Turkish meaning, not noise."""

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
        return dot / norm

    anchor = embedding.embed_query("konut finansmanı kâr payı oranı")
    related = embedding.embed_query("ev kredisi faiz oranı")
    unrelated = embedding.embed_query("hava durumu yarın yağmurlu")

    assert cosine(anchor, related) > cosine(anchor, unrelated)


def test_model_instance_is_reused(embedding):
    """Loading twice would cost seconds and hundreds of MB."""
    assert get_embedding() is embedding
