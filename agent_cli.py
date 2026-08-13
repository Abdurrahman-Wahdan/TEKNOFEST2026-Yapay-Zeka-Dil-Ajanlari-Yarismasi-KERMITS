"""Basit terminal agent'ı: modelin kendisi CANLI TOOL mı yoksa RETRIEVAL mı seçer.

İki tür kaynağı bind eder:
  * banks.build_tools()  -> canlı hesaplama/oran/kur/taksit/mil (bankanın kendi API'si)
  * search_corpus        -> kampanya/ürün METNİ (Qdrant retrieval, statik bilgi)

Model tool_calls üretirse çağırır, sonucu geri besler, tekrar sorar (basit ReAct
döngüsü). Karar tamamen modele ait — biz sadece hangi tool'ların olduğunu ve ne işe
yaradıklarını (docstring = prompt) söylüyoruz.

Çalıştırma (repo kökünden, my_venv ile):
  python agent_cli.py "1000 dolar kaç TL kuveyt türk'te"
  python agent_cli.py "albarakada akaryakıt kampanyası var mı"
  python agent_cli.py                 # etkileşimli mod (boş enter: çıkış)
"""
import json
import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

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


@tool
def search_corpus(query: str, bank: str | None = None) -> str:
    """Katılım bankalarının kampanya ve ürün METİNLERİNDE ara (statik bilgi).

    Kullan: kampanya var mı, koşulları ne, hangi üründe ne avantajı var, SSS gibi
    METİN/AÇIKLAMA soruları. KULLANMA: güncel kur, kâr payı, taksit, hesaplama gibi
    CANLI SAYI soruları — onlar için bankanın hesaplama tool'larını çağır.
    `bank` verilirse o bankayla sınırlar (ör. albaraka, kuveytturk, vakifkatilim).
    Sonuç: en alakalı pasajlar + kaynak URL.
    """
    from qdrant_client import models

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
    if not hits and flt is not None:         # slug uyuşmazlığı olabilir -> filtresiz dene
        hits = client.query_points(
            collection_name=CORPUS_COLLECTION, query=vector,
            limit=15, with_payload=True).points
    if not hits:
        return "Kaynak metinlerde ilgili bilgi bulunamadı."

    out, dropped = [], 0
    for h in hits:
        p = h.payload or {}
        meta = p.get("metadata", {}) or {}
        text = (p.get("page_content") or "").strip()
        # GÜNCELLİK FİLTRESİ: doküman düzeyinde damgalanmış campaign_end
        # (dataprep.stamp_campaign_end) varsa onu kullan — tarih hangi chunk'ta
        # olursa olsun tüm chunk'lara yayıldığı için güvenilir. Damgasızsa (eski
        # veri) chunk metninden dene. Tarih yoksa (ürün/ücret/undated) tutulur.
        end = p.get("campaign_end") or _dates.extract(text)[1]
        if end and not _dates.is_active(end):
            dropped += 1
            continue
        url = meta.get("source_url") or meta.get("pdf_url") or meta.get("gorsel_url") or ""
        out.append({
            "text": text[:500], "bank": meta.get("bank", ""),
            "type": meta.get("type", ""), "url": url,
            "campaign_end": end or None,
        })
        if len(out) >= 5:
            break
    if not out:
        return (f"İlgili {dropped} sonuç bulundu ama hepsinin kampanya süresi dolmuş; "
                f"güncel bir bilgi yok.")
    return json.dumps(out, ensure_ascii=False)


TOOLS = build_tools() + [search_corpus]

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
   ürün açıklaması, SSS gibi METİN sorularında bunu çağır.

Kurallar:
- Hiçbir oranı/taksiti/tutarı KENDİN hesaplama. Sayı, çağırdığın tool'dan gelir.
- Bir araç gerekiyorsa önce onu çağır, sonra cevap ver.
- Cevabı kullanıcının dilinde, kısa ve net ver; kullandığın bankayı/parametreleri belirt.
- Bilgi yoksa uydurmadan "bu bilgi kaynaklarımda yok" de.
"""


def run(question: str, verbose: bool = True) -> str:
    llm = get_llm().bind_tools(TOOLS)
    by_name = {t.name: t for t in TOOLS}
    messages = [SystemMessage(SYSTEM), HumanMessage(question)]

    for _ in range(6):                       # en fazla 6 tur (tool -> gözlem -> ...)
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
