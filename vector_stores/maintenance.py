"""Kaynak URL bazlı bakım işlemleri — SİLME değil, İŞARETLEME.

Bir kaynak sayfa/PDF crawl'da artık bulunamazsa (bkz. dataprep/crawl/graph.py
::mark_removed), buradaki fonksiyon ona ait TÜM Qdrant point'lerinin
payload'ına "removed": true bayrağı ekler — point'ler SİLİNMEZ (geri dönüşü
kolay, retrieval tarafı isterse bu bayrağı filtreleyebilir). Gerçek fiziksel
silme kasıtlı olarak SONRAKİ bir aşamaya bırakıldı.
"""
import logging

from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
)

from vector_stores.client import get_qdrant_client

logger = logging.getLogger(__name__)

# langchain-qdrant metadata'yı payload'da bu anahtar altında (nested) tutar.
_METADATA_URL_FIELDS = ("url", "source_url", "pdf_url", "source_page")


def mark_removed_by_url(collection: str, url: str) -> int:
    """`url`'i (metadata.url/source_url/pdf_url/source_page alanlarından
    HERHANGİ biriyle) kaynak gösteren TÜM point'lerin payload'ına
    {"removed": true} ekler. Dönen: etkilenen alan-koşulu sayısı (point
    sayısı değil — Qdrant set_payload count döndürmez, çağıran sadece
    'denendi' bilgisini bu şekilde alır)."""
    client = get_qdrant_client()
    if not client.collection_exists(collection):
        logger.warning("koleksiyon yok: %s", collection)
        return 0
    matched = 0
    for field in _METADATA_URL_FIELDS:
        flt = Filter(must=[FieldCondition(key=f"metadata.{field}", match=MatchValue(value=url))])
        client.set_payload(
            collection_name=collection,
            payload={"removed": True},
            points=FilterSelector(filter=flt),
        )
        matched += 1
    return matched
