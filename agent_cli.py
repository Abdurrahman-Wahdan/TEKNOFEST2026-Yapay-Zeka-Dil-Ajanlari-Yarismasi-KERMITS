"""Basit terminal agent'ı: modelin kendisi CANLI TOOL mı yoksa RETRIEVAL mı seçer.

İki tür kaynağı bind eder:
  * banks.build_tools()  -> canlı hesaplama/oran/kur/taksit/mil (bankanın kendi API'si)
  * search_corpus        -> kampanya/ürün METNİ (Qdrant retrieval, statik bilgi)

Model tool_calls üretirse çağırır, sonucu geri besler, tekrar sorar (basit ReAct
döngüsü). Karar tamamen modele ait — biz sadece hangi tool'ların olduğunu ve ne işe
yaradıklarını (docstring = prompt) söylüyoruz.

İKİ EVRENSEL KURAL (dataprep/compare/'daki ajanlarla AYNI, burada da geçerli):
  1) Chunk gören her ajan scroll edebilmeli — search_corpus'un getirdiği bir
     pasaj kesik/yetersiz görünüyorsa read_more(point_id) ile o pasajın
     dokümandaki komşularını (öncesi/sonrası, chunk_index sırasına göre)
     okuyabilir.
  2) Retrieval kullanan ajan, elindeki chunk'lardan işine yarayanı/yaramayanı
     KENDİSİ seçmeli — search_corpus'un useful/not_useful alanları, gereksiz
     bulunan pasajları konuşma geçmişinden hemen siler (mark_bank_search_tool
     ile AYNI desen).

Çalıştırma (repo kökünden, my_venv ile):
  python agent_cli.py "1000 dolar kaç TL kuveyt türk'te"
  python agent_cli.py "albarakada akaryakıt kampanyası var mı"
  python agent_cli.py                 # etkileşimli mod (boş enter: çıkış)
"""
import json
import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from banks import build_tools
from corpus import dates as _dates
from embeddings import get_embedding
from llm import get_llm
from vector_stores.client import get_qdrant_client

# Bizim dataprep.embed'in doldurduğu koleksiyon (page/pdf/image ayrı chunk'lar,
# langchain şeması: payload = {page_content, metadata}). main'in bank_chunks'ı
# değil — o boş; retrieval'ı doğrudan buna bağlıyoruz.
CORPUS_COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")
_embed = None


def _embedder():
    global _embed
    if _embed is None:                       # Qwen3 ağırlıkları bir kez yüklensin
        _embed = get_embedding()
    return _embed


def _apply_mark(useful: list, not_useful: list, marked: set, discarded: set) -> str:
    """dataprep/compare/retrieval.py'deki AYNI desen: SON karar geçerli, model
    fikrini adım adım değiştirebilir (önce 'kalsın' sonra 'silinsin' diyebilir)."""
    useful, not_useful = list(useful or ()), list(not_useful or ())
    new_useful = [p for p in useful if p not in marked]
    discarded.difference_update(useful)
    marked.update(useful)
    new_discard = [p for p in not_useful if p not in discarded]
    marked.difference_update(not_useful)
    discarded.update(new_discard)
    parts = []
    if new_useful:
        parts.append(f"{len(new_useful)} sonuç kalıcı işaretlendi (korunacak).")
    if new_discard:
        parts.append(f"{len(new_discard)} sonuç gereksiz işaretlendi (geçmişten silinecek).")
    return " ".join(parts)


def _prune_discarded(messages: list, discarded: set) -> list:
    """not_useful işaretlenen point_id'leri içeren ToolMessage'ları geçmişten
    çıkarır — bank_agent'taki _prune_discarded ile aynı fikir, burada blok
    (search_corpus tek seferde ≤5 sonuç döndürdüğü, chunk-içi ayrıştırma
    gerekmediği için) bazında."""
    if not discarded:
        return messages
    out = []
    for m in messages:
        if isinstance(m, ToolMessage) and any(f'"point_id": "{pid}"' in str(m.content) for pid in discarded):
            m = ToolMessage("(model bu sonuçları gereksiz bulup sildi)", tool_call_id=m.tool_call_id)
        out.append(m)
    return out


class _SearchArgs(BaseModel):
    query: str = Field(description="Arama sorgusu, doğal dil.")
    bank: str | None = Field(default=None, description="Verilirse o bankayla sınırlar "
                              "(ör. albaraka, kuveytturk, vakifkatilim).")
    useful: list[str] = Field(default_factory=list, description="ÖNCEKİ bir "
                               "search_corpus sonucundan gerçekten kullanışlı bulduğun "
                               "point_id'ler — bu çağrıyla AYNI ANDA işaretleyebilirsin.")
    not_useful: list[str] = Field(default_factory=list, description="ÖNCEKİ bir "
                                   "sonuçtan gereksiz/konu dışı bulduğun point_id'ler — "
                                   "aynı çağrıda işaretlenip geçmişten hemen silinir.")


def make_search_corpus_tool(marked: set, discarded: set) -> StructuredTool:
    def _run(query: str, bank: str | None = None, useful: list[str] = (),
              not_useful: list[str] = ()) -> str:
        from qdrant_client import models

        mark_note = _apply_mark(useful, not_useful, marked, discarded)
        vector = _embedder().embed_query(query)
        flt = None
        if bank:
            flt = models.Filter(must=[models.FieldCondition(
                key="metadata.bank", match=models.MatchValue(value=bank))])
        client = get_qdrant_client()
        # Süresi dolmuşları eleyeceğimiz için fazladan çekip aktif ilk 5'i tutuyoruz.
        hits = client.query_points(
            collection_name=CORPUS_COLLECTION, query=vector, query_filter=flt,
            limit=15, with_payload=True).points
        if not hits and flt is not None:     # slug uyuşmazlığı olabilir -> filtresiz dene
            hits = client.query_points(
                collection_name=CORPUS_COLLECTION, query=vector,
                limit=15, with_payload=True).points
        if not hits:
            body = "Kaynak metinlerde ilgili bilgi bulunamadı."
        else:
            out, dropped = [], 0
            for h in hits:
                p = h.payload or {}
                meta = p.get("metadata", {}) or {}
                text = (p.get("page_content") or "").strip()
                # GÜNCELLİK FİLTRESİ: Gemma'nın çıkarıp tüm chunk'lara yaydığı
                # metadata.campaign_end. Tarih yoksa (ürün/ücret/süresiz) tutulur;
                # varsa ve süresi geçmişse elenir.
                end = meta.get("campaign_end")
                if end and not _dates.is_active(end):
                    dropped += 1
                    continue
                if not end and meta.get("campaign_status") == "bitti":
                    dropped += 1
                    continue
                url = meta.get("source_url") or meta.get("pdf_url") or meta.get("gorsel_url") or ""
                out.append({
                    "point_id": str(h.id), "text": text[:500], "bank": meta.get("bank", ""),
                    "type": meta.get("type", ""), "url": url,
                    "campaign_end": end or None,
                })
                if len(out) >= 5:
                    break
            if not out:
                body = (f"İlgili {dropped} sonuç bulundu ama hepsinin kampanya süresi "
                         f"dolmuş; güncel bir bilgi yok.")
            else:
                body = json.dumps(out, ensure_ascii=False)
        return f"{mark_note}\n\n{body}" if mark_note else body

    return StructuredTool.from_function(
        func=_run, name="search_corpus", args_schema=_SearchArgs,
        description=("Katılım bankalarının kampanya ve ürün METİNLERİNDE ara (statik "
                     "bilgi). Kullan: kampanya var mı, koşulları ne, hangi üründe ne "
                     "avantajı var, SSS gibi METİN/AÇIKLAMA soruları. KULLANMA: güncel "
                     "kur, kâr payı, taksit, hesaplama gibi CANLI SAYI soruları — onlar "
                     "için bankanın hesaplama tool'larını çağır. Sonuçtaki bir pasaj "
                     "kesik/yetersiz görünüyorsa read_more(point_id) ile komşularını "
                     "okuyabilirsin. Önceki sonuçlar için useful/not_useful kararını "
                     "BU çağrıya gömebilirsin."))


class _NearbyArgs(BaseModel):
    point_id: str = Field(description="search_corpus sonucunda gördüğün point_id.")
    before: int = Field(default=3, description="Bu chunk'tan ÖNCEKİ kaç chunk getirilsin.")
    after: int = Field(default=3, description="Bu chunk'tan SONRAKİ kaç chunk getirilsin.")


def make_read_more_tool() -> StructuredTool:
    """search_corpus'un getirdiği bir chunk cümle/bilgi ortadan kesiliyormuş
    gibi görünürse, o chunk'ın KOMŞULARINI (doküman sırasına göre öncesi/
    sonrası) okur — dataprep/compare/retrieval.py:make_read_more_tool ile aynı
    fikir, burada tek fark banka sabit (closure) değil: point_id'nin kendi
    metadata'sından okunuyor (search_corpus bankaya göre sınırlı değil)."""
    from qdrant_client import models

    def _run(point_id: str, before: int = 3, after: int = 3) -> str:
        client = get_qdrant_client()
        try:
            pts = client.retrieve(collection_name=CORPUS_COLLECTION, ids=[point_id], with_payload=True)
        except Exception as exc:
            return f"HATA: point_id okunamadı ({exc})."
        if not pts:
            return "Bu point_id için chunk bulunamadı (yanlış id olabilir)."
        meta = (pts[0].payload or {}).get("metadata", {}) or {}
        idx = meta.get("chunk_index", 0)
        bank = meta.get("bank", "")
        url = meta.get("source_url") or meta.get("pdf_url") or meta.get("gorsel_url") or ""
        url_match = models.Filter(should=[
            models.FieldCondition(key="metadata.source_url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.pdf_url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.gorsel_url", match=models.MatchValue(value=url)),
        ])
        flt = models.Filter(must=[
            models.FieldCondition(key="metadata.bank", match=models.MatchValue(value=bank)),
            url_match,
            models.FieldCondition(key="metadata.chunk_index",
                                    range=models.Range(gte=idx - before, lte=idx + after)),
        ])
        points, _ = client.scroll(collection_name=CORPUS_COLLECTION, scroll_filter=flt,
                                   limit=200, with_payload=True)
        rows = sorted(
            ((p.payload or {}).get("metadata", {}).get("chunk_index", 0),
             (p.payload or {}).get("page_content", "")) for p in points)
        if not rows:
            return "Komşu chunk bulunamadı."
        return "\n\n".join(f"[chunk_index={i}]\n{t}" for i, t in rows if t.strip())

    return StructuredTool.from_function(
        func=_run, name="read_more", args_schema=_NearbyArgs,
        description=("search_corpus sonucundaki bir chunk cümle/bilgi ortadan kesiliyormuş "
                     "gibi görünüyorsa, o sonucun point_id'sini vererek dokümanın HEMEN "
                     "öncesini/sonrasını (before/after kadar komşu chunk) okuyabilirsin. "
                     "Yetmezse before/after'ı büyütüp tekrar çağırabilirsin, sınır yok."))


LIVE_TOOLS = build_tools()

SYSTEM = """\
Sen Türk katılım bankalarının ürün ve kampanyaları konusunda bir asistansın.
Elinde iki tür araç var ve hangisini kullanacağına SEN karar verirsin:

1) CANLI ARAÇLAR (bankanın kendi API'si — güncel sayı döndürür):
   finance_quote, profit_share_quote, card_installment_quote, exchange_rates,
   convert_currency, mile_earning_rates, compare_finance, compare_profit_share,
   compare_exchange, check_bank_health, list_banks, list_products.
   Kur, kâr payı, taksit, ödeme planı, mil oranı, "hangi banka daha ucuz" gibi
   HESAPLAMA/ORAN sorularında bunları çağır.

2) search_corpus (retrieval — statik metin): kampanya var mı, koşulları,
   ürün açıklaması, SSS gibi METİN sorularında bunu çağır. Bir sonuç kesik/
   yetersiz görünüyorsa read_more(point_id) ile komşularını okuyabilirsin.

Kurallar:
- Hiçbir oranı/taksiti/tutarı KENDİN hesaplama. Sayı, çağırdığın tool'dan gelir.
- Bir araç gerekiyorsa önce onu çağır, sonra cevap ver.
- Cevabı kullanıcının dilinde, kısa ve net ver; kullandığın bankayı/parametreleri belirt.
- Bilgi yoksa uydurmadan "bu bilgi kaynaklarımda yok" de.
"""


def run(question: str, verbose: bool = True) -> str:
    # marked/discarded: bu TEK konuşmaya özel (kapanış) — search_corpus'un
    # useful/not_useful kararları burada birikir, _prune_discarded her turda
    # 'gereksiz' işaretlenenleri geçmişten hemen siler.
    marked: set[str] = set()
    discarded: set[str] = set()
    tools = LIVE_TOOLS + [make_search_corpus_tool(marked, discarded), make_read_more_tool()]
    by_name = {t.name: t for t in tools}
    llm = get_llm().bind_tools(tools)
    messages = [SystemMessage(SYSTEM), HumanMessage(question)]

    for _ in range(6):                       # en fazla 6 tur (tool -> gözlem -> ...)
        messages = _prune_discarded(messages, discarded)
        ai: AIMessage = llm.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            return ai.content or "(boş yanıt)"
        for tc in ai.tool_calls:
            if verbose:
                print(f"  \033[36m↳ {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})\033[0m")
            try:
                result = by_name[tc["name"]].invoke(tc["args"])
            except Exception as exc:          # tool patlarsa modele hatayı ver, çökme
                result = f"TOOL HATASI: {type(exc).__name__}: {exc}"
            if verbose:
                print(f"    \033[90m{str(result)[:200]}\033[0m")
            messages.append(ToolMessage(str(result), tool_call_id=tc["id"]))
    return "(çok fazla adım — durduruldu)"


def _warmup() -> None:
    """Retrieval embedding'ini (Qwen3) oturum BAŞINDA yükle — ilk kampanya
    sorusunun ortasında 'Loading weights' araya girmesin."""
    print("Retrieval modeli yükleniyor...", end=" ", flush=True)
    _embedder().embed_query("ısınma")       # ağırlıklar + ilk forward burada olsun
    print("hazır.")


def main() -> None:
    _warmup()
    if len(sys.argv) > 1:
        print(run(" ".join(sys.argv[1:])))
        return
    print("Agent hazır. Soru yaz (çıkış için boş enter).")
    while True:
        try:
            q = input("\n\033[1m> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        print("\n" + run(q))


if __name__ == "__main__":
    main()
