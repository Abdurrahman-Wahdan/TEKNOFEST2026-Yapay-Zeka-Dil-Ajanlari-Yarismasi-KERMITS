"""Karşılaştırma hattının retrieval araçları.

Banka-scoped olanların üçü (search_bank, expand_chunk, read_full_page) artık
`corpus/search.py`'de: aynı araçları canlı banka uzmanları da kullanıyor
(agents/shared/bank_tools.py) ve Türkçe metin, süresi-geçmiş kuralı, metadata
okuması ile parça budama TEK yerde tanımlı olmalı. Buradan yeniden dışa
aktarılıyorlar, böylece bank_agent.py'nin importları değişmiyor.

Bu dosyada kalan tek şey TABLO HAVUZU ARACI — `compare_tables` koleksiyonunu
okuyan, classify_agent'a ait araç. Koleksiyonun KENDİSİ artık `corpus/tables.py`:
2026-08-25'e kadar bu hatta özgüydü, ama canlı süpervizör de aynı koleksiyonu
okumaya başladı (`agents/shared/table_tools.py`), yani okuyan iki taraf var ve
koleksiyon adı, point id türetmesi, payload isimleri TEK yerde durmalı. Aynı
gerekçeyle banka-scoped araçlar da `corpus/search.py`'ye taşınmıştı.
"""
from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

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
from corpus.tables import TABLES_COLLECTION, index_table, search_tables, table_point_id

# Banka-scoped yüzey buradan yeniden dışa aktarılıyor: bu modülü zaten import
# eden çağıranlar (bank_agent, testler) taşımadan sonra da aynı isimleri bulsun.
__all__ = [
    "COLLECTION", "RESULTS_PER_CALL", "TABLES_COLLECTION",
    "build_bank_retrieval_tools", "embed_query", "index_table",
    "make_bank_search_tool", "make_expand_chunk_tool", "make_full_page_tool",
    "make_table_search_tool", "prune_entries", "search_tables", "table_point_id",
    "_apply_mark", "_end_date", "_expired", "_shared", "_source_url",
]


# --- tablo havuzu araması (classify_agent'ın aracı) -------------------------
# Tablo havuzu büyüdükçe (yüzlerce olabilir) TÜMÜNÜ tek prompt'a sığdırmak
# imkansız — search_bank ile aynı felsefe: embedding bazlı arama, LLM karar
# verene kadar özgürce (farklı sorgularla) tekrar arayabilir. KALICI: hafıza-içi
# değil, Qdrant'ta ayrı bir koleksiyon — süreç kaç kez restart olursa olsun
# yeniden embed etmeye gerek kalmaz, tek gerçek kaynak budur.
#
# Koleksiyonun KENDİSİ artık `corpus/tables.py`'de: canlı süpervizör de aynı
# koleksiyonu okuyor, yani "bu hatta özgü" değil. Yukarıda yeniden dışa
# aktarılıyor, bu modülü import eden çevrimdışı hat değişmedi. Burada kalan tek
# şey AŞAĞIDAKİ ARAÇ: `next`/`useful`/`not_useful` işaretlemesi çevrimdışı
# ajanın bağlam budamasına bağlı ve yalnızca ona ait.


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
        key = query.strip().lower()
        offset = (_offsets.get(key, 0) + 5) if next else 0
        _offsets[key] = offset
        hits = search_tables(query, intent=intent, limit=5, offset=offset)
        if not hits:
            body = "Sonuç yok."
        else:
            # `ui_url` KASITLI olarak yazılmıyor: bu araç bir tablonun VAR OLUP
            # OLMADIĞINA karar vermek için, kullanıcıya link vermek için değil —
            # çevrimdışı hattın kullanıcısı yok.
            body = "\n---\n".join(
                f"id={h['id']} [{h['category']}/{h['subcategory']}] "
                f"benzerlik={h['score']:.2f}\n{h['docstring']}"
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
