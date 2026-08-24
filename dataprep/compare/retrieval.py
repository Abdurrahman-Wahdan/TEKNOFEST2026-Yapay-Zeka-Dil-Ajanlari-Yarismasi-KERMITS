"""Karşılaştırma hattının retrieval araçları.

Banka-scoped olanların üçü (search_bank, expand_chunk, read_full_page) artık
`corpus/search.py`'de: aynı araçları canlı banka uzmanları da kullanıyor
(agents/shared/bank_tools.py) ve Türkçe metin, süresi-geçmiş kuralı, metadata
okuması ile parça budama TEK yerde tanımlı olmalı. Buradan yeniden dışa
aktarılıyorlar, böylece bank_agent.py'nin importları değişmiyor.

Bu dosyada kalan tek şey TABLO HAVUZU araması — `compare_tables` koleksiyonu,
classify_agent'ın aracı. O koleksiyon bu hatta özgü: canlı uzmanların işi değil.
"""
from __future__ import annotations

import os
import threading
import uuid

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from qdrant_client import models

from corpus.search import (
    COLLECTION,
    RESULTS_PER_CALL,
    _apply_mark,
    _end_date,
    _expired,
    _shared,
    _source_url,
    build_bank_retrieval_tools,
    embed_query,
    make_bank_search_tool,
    make_expand_chunk_tool,
    make_full_page_tool,
    prune_entries,
)

# Banka-scoped yüzey buradan yeniden dışa aktarılıyor: bu modülü zaten import
# eden çağıranlar (bank_agent, testler) taşımadan sonra da aynı isimleri bulsun.
__all__ = [
    "COLLECTION", "RESULTS_PER_CALL", "TABLES_COLLECTION",
    "build_bank_retrieval_tools", "embed_query", "index_table",
    "make_bank_search_tool", "make_expand_chunk_tool", "make_full_page_tool",
    "make_table_search_tool", "prune_entries",
    "_apply_mark", "_end_date", "_expired", "_shared", "_source_url",
]


# --- tablo havuzu araması (classify_agent'ın aracı) -------------------------
# Tablo havuzu büyüdükçe (yüzlerce olabilir) TÜMÜNÜ tek prompt'a sığdırmak
# imkansız — search_bank ile aynı felsefe: embedding bazlı arama, LLM karar
# verene kadar özgürce (farklı sorgularla) tekrar arayabilir. KALICI: hafıza-içi
# değil, Qdrant'ta ayrı bir koleksiyon — süreç kaç kez restart olursa olsun
# yeniden embed etmeye gerek kalmaz, tek gerçek kaynak budur.

TABLES_COLLECTION = os.environ.get("QDRANT_COLLECTION_TABLES", "compare_tables")
_TABLE_NS = uuid.UUID("6f9c6e2e-6b7a-4b7a-9c1e-3a2f7b8d5e10")   # id->UUID için sabit namespace
_tables_collection_ready = False
_tables_collection_lock = threading.Lock()


def _table_point_id(table_id: str) -> str:
    return str(uuid.uuid5(_TABLE_NS, table_id))


def _ensure_tables_collection() -> None:
    global _tables_collection_ready
    if _tables_collection_ready:
        return
    with _tables_collection_lock:
        if _tables_collection_ready:
            return
        _, client = _shared()
        if not client.collection_exists(TABLES_COLLECTION):
            client.create_collection(
                collection_name=TABLES_COLLECTION,
                vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE))
        _tables_collection_ready = True


def index_table(table_id: str, topic: str, category: str, subcategory: str, docstring: str) -> None:
    """Yeni (ya da güncellenmiş) bir tabloyu Qdrant'a KALICI olarak yazar —
    search_tables bunu okur. create_table sonrası pipeline tarafından çağrılır.

    `topic` metnin EN BAŞINA ve tekrarlı konur: docstring'ler kalıplaşmış/şablon
    ağırlıklı olduğu için (ör. onlarca sigorta tablosu neredeyse birebir aynı
    cümleyle başlıyor), ayırt edici asıl bilgi (ürünün adı/türü) kalabalık ortak
    kelimeler arasında boğulup embedding benzerliğini bulanıklaştırıyordu
    (kanıtlı: "konut sigortası" araması gerçek 'konut-sigortası' tablosunu ilk
    5'e bile sokmadı). Konuyu öne çıkarmak ayırt ediciliği güçlendirir."""
    _ensure_tables_collection()
    _, client = _shared()
    text = f"{topic}. {topic}. {category} {subcategory}: {docstring}"
    vec = embed_query(text)
    client.upsert(collection_name=TABLES_COLLECTION, points=[models.PointStruct(
        id=_table_point_id(table_id), vector=vec,
        payload={"id": table_id, "category": category, "subcategory": subcategory,
                 "docstring": docstring})])


class _TableSearchArgs(BaseModel):
    query: str = Field(description="Sayfanın konusunu betimleyen doğal dil sorgusu "
                        "— en anlamca yakın 5 tabloyu (benzerlik skoruyla) getirir. "
                        "AYNI query'yi next=false ile tekrar göndermek BİREBİR AYNI "
                        "5 sonucu döner (deterministik) — FARKLI bir query yaz, ya "
                        "da aynı query için SONRAKİ 5 sonucu görmek istiyorsan "
                        "next=true gönder.")
    intent: str = Field(description="Bu aramayla TAM OLARAK NEYİ bulmaya çalıştığını "
                        "kısa bir cümleyle belirt. Arama embedding tabanlıdır ve bu "
                        "niyet cümlesi arama kalitesini doğrudan etkiler.")
    next: bool = Field(default=False, description="True ise: bu AYNI query için bir "
                        "önceki çağrıda görmediğin SONRAKİ 5 sonucu getirir "
                        "(sayfalama). False (varsayılan) ise en baştan en alakalı "
                        "5 sonucu getirir.")
    useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                        "ÖNCEKİ sonuçtan gerçekten kullanışlı bulduğun tablo "
                        "id'leri — bu aramayla AYNI ÇAĞRIDA işaretleyebilirsin.")
    not_useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                        "ÖNCEKİ sonuçtan gereksiz/konu dışı bulduğun tablo id'leri "
                        "— aynı çağrıda işaretlenip hemen silinir.")


def make_table_search_tool(registry_loader, marked: set | None = None,
                             discarded: set | None = None) -> StructuredTool:
    """`registry_loader`: kullanılmıyor gibi görünse de havuz TAMAMEN boşsa (0
    tablo, Qdrant koleksiyonu henüz yok) hızlı bir "kesin yeni konu" cevabı
    vermek için kullanılır — sorgu maliyetinden kaçınmak için.
    `marked`/`discarded`: search_bank ile AYNI desen — üretilen tüm ajanlar bu
    şekilde çalışsın: ilk 5 aday gelir, model isterse next=true ile sonraki 5'i
    ister, bunu yaparken önceki adaylardan gereksiz olanları not_useful ile eler
    (history'de sadece işe yarayanlar kalır)."""
    marked = marked if marked is not None else set()
    discarded = discarded if discarded is not None else set()
    _offsets: dict[str, int] = {}

    def _run(query: str, intent: str = "", next: bool = False,
              useful: list[str] = (), not_useful: list[str] = ()) -> str:
        mark_note = _apply_mark(useful, not_useful, marked, discarded)
        if not registry_loader():
            return "Henüz hiç karşılaştırma tablosu yok — bu kesin olarak yeni bir konu."
        _ensure_tables_collection()
        _, client = _shared()
        key = query.strip().lower()
        offset = (_offsets.get(key, 0) + 5) if next else 0
        _offsets[key] = offset
        # `intent`: statik/sabit bir talimat metni BİZ yazmıyoruz — modelin kendi
        # ifade ettiği arama niyeti kullanılıyor (asimetrik retrieval talimatı).
        qvec = embed_query(query, task=intent or None)
        hits = client.query_points(collection_name=TABLES_COLLECTION, query=qvec,
                                    limit=5, offset=offset, with_payload=True).points
        if not hits:
            body = "Sonuç yok."
        else:
            body = "\n---\n".join(
                f"id={h.payload['id']} [{h.payload.get('category', '')}/"
                f"{h.payload.get('subcategory', '')}] benzerlik={h.score:.2f}\n"
                f"{h.payload['docstring']}"
                for h in hits)
        return f"{mark_note}\n\n{body}" if mark_note else body

    return StructuredTool.from_function(
        func=_run, name="search_tables", args_schema=_TableSearchArgs,
        description=("Mevcut karşılaştırma tablolarında ANLAM bazlı ara (embedding "
                     "benzerliği). Sayfanın konusunu betimleyen bir sorgu yaz, en "
                     "yakın 5 tabloyu (id, kategori/alt-kategori, açıklama, benzerlik "
                     "skoru) getirir. Havuz büyük olabilir, hepsi tek seferde "
                     "gösterilmez — bu yüzden bu araçla ara. Emin olamıyorsan next=true "
                     "ile SONRAKİ 5'i görebilir ya da FARKLI bir sorguyla tekrar "
                     "arayabilirsin. intent alanına ne aradığını yazman arama "
                     "isabetini gerçekten etkiler, formalite değildir."))
