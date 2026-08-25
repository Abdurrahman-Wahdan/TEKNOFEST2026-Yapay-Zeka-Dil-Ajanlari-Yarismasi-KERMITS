"""`compare_tables` koleksiyonu — karşılaştırma tablosu havuzunun aranabilir dizini.

Bu koleksiyon `campaigns`'in yanında ikinci bir kaynak sınıfıdır ve İÇERİK
TAŞIMAZ: her point BİR tablonun künyesidir (adı, kategorisi, ne karşılaştırdığı)
ve o tablonun BİZİM arayüzümüzdeki adresi. Banka bilgisi burada değil,
`campaigns`'te ve bankaların canlı uç noktalarındadır.

NEDEN BURADA. Bu tanımlar `dataprep/compare/retrieval.py` içindeydi ve o dosya
"bu koleksiyon bu hatta özgü: canlı uzmanların işi değil" diyordu. Artık değil:
2026-08-25'ten beri canlı süpervizör de aynı koleksiyonu okuyor
(`agents/shared/table_tools.py`), yani okuyan iki taraf var ve koleksiyon adı,
point id türetmesi ve payload isimleri TEK yerde tanımlı olmalı. Aynı gerekçeyle
banka-scoped araçlar da `corpus/search.py`'ye taşınmıştı; `retrieval.py` bunları
yeniden dışa aktarıyor, böylece çevrimdışı hattın importları değişmiyor.

PAYLOAD. Canlı koleksiyonun 403 point'inin tamamı şunları taşıyor: `id`, `topic`,
`category`, `subcategory`, `docstring`, `text`, `indexed_at` — ve `ui_url`
(bkz. `dataprep/stamp_table_urls.py`). `index_table` bunların hepsini yazar;
BİR ZAMANLAR `topic` ve `text` yazmıyordu ve o sürümle yeniden indekslenen bir
tablo, canlı point'lerde duran adını KAYBEDİYORDU — hiçbir yerde hata vermeden,
yalnızca arama sonucunda adı boş çıkarak.
"""
from __future__ import annotations

import os
import threading
import unicodedata
import uuid

from qdrant_client import models

from .search import _shared, embed_query

__all__ = ["TABLES_COLLECTION", "index_table", "search_tables", "table_point_id"]

TABLES_COLLECTION = os.environ.get("QDRANT_COLLECTION_TABLES", "compare_tables")

# id -> UUID için sabit namespace. Rastgele id ASLA: aynı tabloyu iki kez
# indekslemek aynı point'i güncellemeli, ikinci bir kopya bırakmamalı.
_TABLE_NS = uuid.UUID("6f9c6e2e-6b7a-4b7a-9c1e-3a2f7b8d5e10")
_ready = False
_ready_lock = threading.Lock()


def _nfc(value: str) -> str:
    """Türkçe id'ler diskte NFD, dosyanın içinde NFC olabiliyor (bkz.
    `api/compare_tables_pool.load_table`). uuid5 bayta duyarlı olduğu için tek
    biçime çekilir, yoksa aynı tablo iki point olur."""
    return unicodedata.normalize("NFC", value or "")


def table_point_id(table_id: str) -> str:
    return str(uuid.uuid5(_TABLE_NS, _nfc(table_id)))


def _ensure_collection() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if _ready:
            return
        _, client = _shared()
        if not client.collection_exists(TABLES_COLLECTION):
            client.create_collection(
                collection_name=TABLES_COLLECTION,
                vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE))
        _ready = True


def index_text(topic: str, category: str, subcategory: str, docstring: str) -> str:
    """Gömülen metin.

    `topic` metnin EN BAŞINA ve tekrarlı konur: docstring'ler kalıplaşmış/şablon
    ağırlıklı olduğu için (ör. onlarca sigorta tablosu neredeyse birebir aynı
    cümleyle başlıyor), ayırt edici asıl bilgi (ürünün adı/türü) kalabalık ortak
    kelimeler arasında boğulup embedding benzerliğini bulanıklaştırıyordu
    (kanıtlı: "konut sigortası" araması gerçek 'konut-sigortası' tablosunu ilk
    5'e bile sokmadı). Konuyu öne çıkarmak ayırt ediciliği güçlendirir."""
    return f"{topic}. {topic}. {category} {subcategory}: {docstring}"


def index_table(table_id: str, topic: str, category: str, subcategory: str,
                docstring: str, ui_url: str | None = None) -> None:
    """Yeni (ya da güncellenmiş) bir tabloyu Qdrant'a KALICI olarak yazar —
    `search_tables` bunu okur. create_table sonrası pipeline tarafından çağrılır.

    `ui_url` VERİLMEZSE point'te zaten duran değer KORUNUR — okunup geri yazılır.
    `upsert` payload'ı EKLEMİYOR, KOMPLE DEĞİŞTİRİYOR: adresi yazan taraf başkası
    (`dataprep/stamp_table_urls.py`) olduğu için, bunu yapmayan bir sürüm her
    yeniden indekslemede o adresi sessizce siler ve ajanın linki kaybolur.
    """
    _ensure_collection()
    _, client = _shared()
    table_id = _nfc(table_id)
    point_id = table_point_id(table_id)
    if not ui_url:
        existing = client.retrieve(TABLES_COLLECTION, ids=[point_id],
                                   with_payload=["ui_url"], with_vectors=False)
        ui_url = (existing[0].payload or {}).get("ui_url") if existing else None
    text = index_text(topic, category, subcategory, docstring)
    # `topic` ve `text` de yazılıyor: canlı koleksiyonun 403 point'inin tamamında
    # varlar ve arama sonucunda tablonun ADI olarak okunan alan `topic`. Bunları
    # yazmayan bir sürüm, dokunduğu her tablonun adını sessizce siliyordu.
    payload = {"id": table_id, "topic": topic, "category": category,
               "subcategory": subcategory, "docstring": docstring, "text": text}
    if ui_url:
        payload["ui_url"] = ui_url
    client.upsert(collection_name=TABLES_COLLECTION, points=[models.PointStruct(
        id=point_id, vector=embed_query(text), payload=payload)])


def search_tables(query: str, intent: str = "", limit: int = 5, offset: int = 0) -> list[dict]:
    """Havuzda ANLAM bazlı arama. Sonuçlar künye + arayüz adresi, tablo içeriği DEĞİL.

    `intent`: asimetrik retrieval talimatı — sabit bir metin BİZ yazmıyoruz,
    arayan tarafın kendi ifade ettiği niyet kullanılıyor.
    """
    _ensure_collection()
    _, client = _shared()
    hits = client.query_points(
        collection_name=TABLES_COLLECTION, query=embed_query(query, task=intent or None),
        limit=limit, offset=offset, with_payload=True).points
    return [{
        "id": (h.payload or {}).get("id", ""),
        # `topic` yoksa `id`: eski bir sürümle yazılmış point'te ad boş olabilir
        # ve o zaman slug, hiç yoktan iyidir.
        "topic": (h.payload or {}).get("topic") or (h.payload or {}).get("id", ""),
        "category": (h.payload or {}).get("category", ""),
        "subcategory": (h.payload or {}).get("subcategory", ""),
        "docstring": (h.payload or {}).get("docstring", ""),
        "ui_url": (h.payload or {}).get("ui_url") or "",
        "score": h.score,
    } for h in hits]
