"""Banka-scoped retrieval tool — bir subagent'ın ELİNE VERİLEN tek araç.

search_bank(query, k): subagent kendi query'sini yazar, k'yı kendi seçer, yetmezse
farklı query'yle veya daha büyük k ile TEKRAR çağırabilir. Banka sabittir (closure) —
subagent kendi bankasının dışına çıkamaz, "sadece o bankanın sorumlusu" tasarımı.

Qdrant 'campaigns' koleksiyonunu, metadata.bank filtresiyle arar. Süresi geçmiş
kampanyalar (metadata.campaign_end damgalıysa) elenir — bu tarih KIYASI, metinden
regex'le veri çekme değil; zaten Gemma'nın çıkardığı yapılandırılmış tarihi
karşılaştırıyoruz.
"""
from __future__ import annotations

import os
import threading

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from qdrant_client import models

from corpus import dates
from embeddings import get_embedding
from vector_stores.client import get_qdrant_client

COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")

_embed = None
_client = None
_lock = threading.Lock()   # 10 subagent aynı anda ilk çağrıyı yapabilir; tekil yükleme


def _shared():
    global _embed, _client
    with _lock:
        if _embed is None:
            _embed = get_embedding()
        if _client is None:
            _client = get_qdrant_client()
    return _embed, _client


RESULTS_PER_CALL = 5   # sabit grup boyutu — model sayı seçmez


class _SearchArgs(BaseModel):
    query: str = Field(description="Arama sorgusu — doğal dil, Türkçe. Her çağrı "
                        "en alakalı 5 sonucu getirir. Derinleşmek için FARKLI bir "
                        "query ile tekrar çağır.")


def make_bank_search_tool(bank: str) -> StructuredTool:
    """Bu bankaya SABİTLENMİŞ bir arama tool'u üretir (subagent bankayı seçemez)."""

    def _run(query: str) -> str:
        embedder, client = _shared()
        # PyTorch MPS (Apple GPU) eşzamanlı thread'lerden çağrılınca sessizce
        # çöküyor (traceback'siz) — 10 subagent aynı anda embed edebileceği için
        # GPU çağrısını SERİLEŞTİR. Embedding hızlı; darboğaz zaten LLM çağrıları,
        # onlar kilitlenmeden paralel kalır.
        with _lock:
            vector = embedder.embed_query(query)
        flt = models.Filter(must=[models.FieldCondition(
            key="metadata.bank", match=models.MatchValue(value=bank))])
        hits = client.query_points(
            collection_name=COLLECTION, query=vector, query_filter=flt,
            limit=RESULTS_PER_CALL, with_payload=True).points
        out = []
        for h in hits:
            p = h.payload or {}
            meta = p.get("metadata", {}) or {}
            end = meta.get("campaign_end")
            if end and not dates.is_active(end):        # süresi geçmiş -> gösterme
                continue
            url = meta.get("source_url") or meta.get("pdf_url") or meta.get("source_page") or ""
            out.append({
                "point_id": str(h.id), "url": url, "type": meta.get("type", ""),
                "text": (p.get("page_content") or "").strip()[:600],
                "campaign_end": end,
            })
        if not out:
            return "Sonuç yok (bu banka için bu sorguyla hiçbir güncel içerik bulunamadı)."
        return "\n---\n".join(
            f"[{i+1}] point_id={o['point_id']} url={o['url']} campaign_end={o['campaign_end']}\n{o['text']}"
            for i, o in enumerate(out))

    return StructuredTool.from_function(
        func=_run, name="search_bank", args_schema=_SearchArgs,
        description=(f"'{bank}' katılım bankasının KENDİ içeriğinde ara (yalnız bu "
                     f"bankada, başkasında değil). Her çağrı 5 sonuç getirir — kaç "
                     f"sonuç istediğine SEN karar vermezsin. Süresi geçmiş kampanyalar "
                     f"zaten gösterilmez. Yetersizse FARKLI bir query ile tekrar ara."))


class _MarkArgs(BaseModel):
    point_ids: list[str] = Field(description="search_bank sonuçlarından GERÇEKTEN "
                        "kullanışlı bulduklarının point_id'leri.")


def make_mark_tool(marked: set) -> StructuredTool:
    """İşaretlenen point_id'ler bağlam sıkışıp eski aramalar silinirken KORUNUR —
    işaretlemediklerin silinebilir. `marked`: research_bank'a özel, çağrı boyunca
    paylaşılan küme (closure)."""

    def _run(point_ids: list[str]) -> str:
        marked.update(point_ids)
        return f"{len(point_ids)} sonuç işaretlendi, korunacak."

    return StructuredTool.from_function(
        func=_run, name="mark_useful", args_schema=_MarkArgs,
        description=("search_bank sonuçlarından bu ürün/kampanya için GERÇEKTEN "
                     "kullanışlı olanları point_id'leriyle işaretle. Bağlam çok "
                     "büyürse işaretlenmemiş eski sonuçlar silinebilir, "
                     "işaretlediklerin KORUNUR — son cevabını kuracağın kanıtları "
                     "işaretlemeyi unutma."))


class _FullPageArgs(BaseModel):
    url: str = Field(description="search_bank sonucunda gördüğün 'url=' değeri.")


def make_full_page_tool(bank: str) -> StructuredTool:
    """search_bank sadece bir chunk (parça, ~600 karakter) döndürür — sayfanın
    KENDİSİ o parçada kesilmiş/eksik görünüyorsa, agent bu tool'la aynı URL'nin
    TÜM chunk'larını (chunk_index sırasıyla) birleştirilmiş, tam metin olarak
    okuyabilir. Banka sabit (closure) — sadece kendi bankasının sayfalarını okur."""

    def _run(url: str) -> str:
        _, client = _shared()
        url_match = models.Filter(should=[
            models.FieldCondition(key="metadata.source_url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.pdf_url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.source_page", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.gorsel_url", match=models.MatchValue(value=url)),
        ])
        flt = models.Filter(must=[
            models.FieldCondition(key="metadata.bank", match=models.MatchValue(value=bank)),
            url_match,
        ])
        points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                                   limit=200, with_payload=True)
        if not points:
            return "Bu URL için chunk bulunamadı (yanlış url ya da başka bankaya ait olabilir)."
        rows = sorted(
            ((p.payload or {}).get("metadata", {}).get("chunk_index", 0),
             (p.payload or {}).get("page_content", "")) for p in points)
        return "\n\n".join(t for _, t in rows if t.strip())

    return StructuredTool.from_function(
        func=_run, name="read_full_page", args_schema=_FullPageArgs,
        description=("search_bank bir sayfanın yalnızca KÜÇÜK BİR PARÇASINI (chunk) "
                     "getirir; kesilmiş/yarım/yetersiz görünüyorsa bu tool'la aynı "
                     "sonucun 'url' değerini vererek o sayfanın/PDF'in TÜM parçalarını "
                     "birleştirilmiş, TAM metin olarak okuyabilirsin."))
