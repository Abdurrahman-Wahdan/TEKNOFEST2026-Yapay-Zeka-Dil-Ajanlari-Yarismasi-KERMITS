"""Banka-scoped researcher subagent — Gemma, search_bank + read_full_page
tool'larına serbest erişimle bir konuyu kendi bankasında araştırır. Sorguyu
nasıl kuracağı, kaç kez arayacağı,
ne zaman yeterli bilgiye sahip olduğu tamamen KENDİ kararı — burada içerik
kararına karışan bir kural yok.

Mühendislik güvenlikleri (İÇERİK kararı değil, fiziksel/operasyonel sınırlar):
  * KAYNAK BÜTÇESİ (MAX_TOOL_CALLS) — HER tool çağrısı (search/mark/read_full_page
    fark etmez) bütçeden düşer; sonsuz döngüye karşı tek, araç-türünden bağımsız
    fiziksel tavan. Aşılırsa loglanır.
  * BAĞLAM PENCERESİ (MAX_CONTEXT_CHARS, ~64k token hedefi) — server 128k token
    destekliyor ama biz zorlamıyoruz. Asıl budama İÇERİK kararına dayalı: agent
    mark_useful(useful=[...], not_useful=[...]) ile HER chunk için kendisi karar
    verir; 'not_useful' dediği chunk'lar bir sonraki turda HEMEN silinir (bağlamın
    dolmasını beklemeden), 'useful' dediği kalıcı korunur. Adım sayısı arttıkça
    fikir değiştirebilir, son kararı geçerli olur. Bu bile yetmez de bağlam yine
    dolarsa (model henüz karar vermediği eski turlar için) SON ÇARE olarak fiziksel
    iki-geçişli budama devreye girer.
  * PAYLAŞILAN SEMAFOR (dataprep.net_limit) — server'a aynı anda ≤90 açık istek
    (Gemma ile PAYLAŞILAN tavan).
  * DAYANIKLI İSTEK — sunucudan (parse dışı) hata dönerse İSTEK sıcaklık
    merdiveniyle (0.0->1.0) tekrarlanır; kalıcı (4xx) hatada denenmez.

Bir bankanın araştırması ne sebeple olursa olsun patlarsa TÜM pipeline'ı
çökertmemesi için research_bank hiçbir istisna fırlatmaz — hata raporlanır.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from llm import get_llm

from ..net_limit import NET_SEM
from .retrieval import make_bank_search_tool, make_full_page_tool

log = logging.getLogger("dataprep.compare.bank_agent")

MAX_TOOL_CALLS = int(os.environ.get("COMPARE_MAX_TOOL_CALLS", "50"))
# Aynı sorguyu next=true ile kaç kez derinleştirebilir (sayfalama) — sınırsız
# derinlik azalan getiridir, tek bir konuda tüm bütçeyi (50) tüketebilir.
NEXT_DEPTH_LIMIT = int(os.environ.get("COMPARE_NEXT_DEPTH_LIMIT", "4"))
# Server 131072 token. Hedef ~64k — erken ve GÜVENLİ compact (char/token oranı
# beklenenden kötü çıktığı ampirik olarak görüldü, o yüzden muhafazakâr karakter eşiği).
MAX_CONTEXT_CHARS = int(os.environ.get("COMPARE_MAX_CONTEXT_CHARS", "60000"))
MAX_RETRY_SECONDS = 120
LIMIT_LOG = Path(__file__).resolve().parents[2] / "data" / "_tables" / "_tool_limit_hits.jsonl"

_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir KATILIM BANKASI araştırmacısısın. ÖNEMLİ — TERMİNOLOJİ: katılım "
    "bankacılığı faizsiz/İslami bankacılıktır; ÜRETTİĞİN HER METİNDE 'kredi'/'faiz' "
    "değil 'finansman'/'kâr payı'/'kâr oranı' kullan. Kaynak sayfa konvansiyonel "
    "terim kullansa bile SEN katılım bankacılığı terimine çevir. Tek istisna: "
    "'kredi kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu gibi kalır.\n\n"
    "search_bank aracı embedding (anlam vektörü) tabanlı arama yapar — sorgun ile "
    "arananın vektör uzayında karşılaştırılması için modelin bir 'niyet' bilgisine "
    "ihtiyacı var; bu yüzden her çağrıda intent alanına o aramayla TAM OLARAK ne "
    "bulmaya çalıştığını (query'den ayrı, ona ek olarak) yaz — bu formalite değil, "
    "arama kalitesini gerçekten belirliyor.\n\n"
    "Görevin: '{bank}' katılım bankasının "
    "'{topic}' konusuyla ilgili GERÇEK bir ürünü/kampanyası olup olmadığını "
    "search_bank aracıyla araştırmak. Sorguyu nasıl kuracağına, kaç kez arayacağına "
    "ve ne zaman yeterli bilgiye sahip olduğuna SEN karar ver — her çağrı 5 sonuç "
    "getirir, ama bunlar sayfanın küçük PARÇALARIDIR (chunk). Bir sonuç kesilmiş/"
    "yarım/yetersiz görünüyorsa read_full_page(url) ile o sayfanın/PDF'in TÜM "
    "parçalarını birleştirilmiş, TAM metin olarak okuyabilirsin. AYNI query'yi "
    "next=false ile tekrar göndermek YENİ BİR ŞEY GETİRMEZ (deterministik, birebir "
    "aynı 5 sonuç döner) — aynı konuda SONRAKİ 5 sonucu görmek istersen query'yi "
    "DEĞİŞTİRMEDEN next=true gönder, farklı bir konuya geçiyorsan yeni query yaz. "
    "Gelen sonuçlar konudan uzaklaşıyorsa ya da bu bankada böyle bir şey olmadığına "
    "kanaat getirdiysen ısrar etme; bu da geçerli bir sonuçtur.\n\n"
    "Her arama sonucunu gördükten sonra KENDİN karar ver: hangi point_id'ler "
    "gerçekten işine yarıyor (useful — kalıcı korunur), hangileri konu dışı/gereksiz "
    "(not_useful — hemen hafızadan silinir, bir daha görmezsin). Bu kararı YENİ bir "
    "search_bank çağrının useful/not_useful alanlarına göm — ayrı bir araç yok, "
    "sadece arama çağrısına eklenir. Artık aramayacaksan işaretlemeye gerek yok, "
    "doğrudan (tool çağırmadan) JSON ile bitir. Fikrini adım adım değiştirebilirsin — "
    "önce 'kalsın' dediğine sonra 'silinsin' diyebilirsin, SON kararın geçerli olur.\n\n"
    "Emin olunca SADECE şu JSON ile (tool çağırmadan) bitir:\n"
    '{{"offers": true|false, "attributes": {{"<alan adı>": "<değer>", ...}}, '
    '"sources": [{{"url": "...", "note": "<bu kaynaktan ne aldın>"}}]}}\n'
    "attributes: bu ürün/kampanyayı EN İYİ tanımlayan alanları SEN belirle (oran, "
    "tutar aralığı, vade, ücret, koşul, geçerlilik — konuya göre değişir, sabit şema "
    "yok). offers=false ise attributes boş, sources yine de aradığın kanıtı gösterir. "
    "UYDURMA — yalnız arama sonuçlarında gördüğünü yaz."
)


def _msg_len(m) -> int:
    c = m.content
    return len(c) if isinstance(c, str) else len(str(c))


def _block_has_marked(block: list, marked: set[str]) -> bool:
    if not marked:
        return False
    for m in block:
        if isinstance(m, ToolMessage):
            c = m.content if isinstance(m.content, str) else str(m.content)
            if any(f"point_id={pid}" in c for pid in marked):
                return True
    return False


_ENTRY_SEP = "\n---\n"


def _prune_discarded(messages: list, discarded: set[str]) -> list:
    """CHUNK bazlı budama — search_bank cevapları birden çok chunk içerir
    ([1] point_id=..., [2] point_id=..., ...); model mark_useful ile HER
    chunk için ayrı karar verir. Burada, model 'gereksiz' dediği tek tek
    chunk'ları o ToolMessage içinden çıkarırız (kalanlara dokunmadan) —
    bloğun tamamını atmıyoruz, sadece istenmeyen satırları."""
    if not discarded:
        return messages
    out = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            out.append(m)
            continue
        c = m.content if isinstance(m.content, str) else str(m.content)
        if _ENTRY_SEP not in c and "point_id=" not in c:
            out.append(m)
            continue
        kept = [e for e in c.split(_ENTRY_SEP)
                if not any(f"point_id={pid}" in e for pid in discarded)]
        if not kept:
            m.content = "(model bu grubu gereksiz bulup sildi)"
        elif len(kept) != len(c.split(_ENTRY_SEP)):
            m.content = _ENTRY_SEP.join(kept)
        out.append(m)
    return out


def _trim_history(messages: list, marked: set[str], discarded: set[str] = frozenset()) -> list:
    """1) Model 'gereksiz' dediği chunk'ları (discarded) HER turda, bağlam
    dolmasını beklemeden hemen budar — bu asıl mekanizma, model kararına
    dayalı. 2) Bağlam yine de MAX_CONTEXT_CHARS'ı aşarsa (son çare, model
    henüz karar vermediği eski turlar için) İKİ geçişli fiziksel budama:
    önce işaretlenmemiş turlar atılır, hâlâ sığmıyorsa işaretliler de
    atılmaya başlar. system+human (ilk 2 mesaj) hep kalır."""
    messages = _prune_discarded(messages, discarded)
    total = sum(_msg_len(m) for m in messages)
    if total <= MAX_CONTEXT_CHARS or len(messages) <= 3:
        return messages
    head, rest = messages[:2], messages[2:]
    for protect_marked in (True, False):
        i = 0
        while total > MAX_CONTEXT_CHARS and i < len(rest):
            end = i + 1
            while end < len(rest) and isinstance(rest[end], ToolMessage):
                end += 1
            block = rest[i:end]
            if protect_marked and _block_has_marked(block, marked):
                i = end                 # işaretli, koru — bir sonraki bloğa geç
                continue
            total -= sum(_msg_len(m) for m in block)
            del rest[i:end]
        if total <= MAX_CONTEXT_CHARS:
            break
    return head + rest


def _is_permanent(exc: Exception) -> bool:
    s = str(exc)
    return any(code in s for code in ("400", "401", "403", "404", "BadRequest"))


def _invoke_resilient(tools: list | None, messages: list, allow_tools: bool = True):
    """Parse dışı (sunucu/ağ) hata olursa sıcaklık merdiveniyle (0.0->1.0)
    İSTEĞİ tekrar dener — kalıcı (4xx) hatada denemez, hemen fırlatır."""
    start = time.time()
    delay = 1.0
    attempt = 0
    while True:
        t = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            with NET_SEM:                      # Gemma ile PAYLAŞILAN server tavanı
                llm = get_llm("gemma", temperature=t)
                if allow_tools and tools:
                    llm = llm.bind_tools(tools)
                return llm.invoke(messages)
        except Exception as exc:
            if _is_permanent(exc):
                raise
            elapsed = time.time() - start
            if elapsed + delay >= MAX_RETRY_SECONDS:
                raise
            log.warning("    Gemma istek hatası (deneme %d, t=%.1f): %s — %.0fs sonra tekrar",
                        attempt + 1, t, type(exc).__name__, delay)
            time.sleep(delay)
            delay = min(delay * 2, 20)
            attempt += 1


def _try_parse(content: str) -> dict | None:
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def _parse_json_ladder(messages: list, first_content: str) -> dict:
    """JSON kapanmadıysa artan sıcaklıkla YENİDEN CEVAP iste (tool'suz, araştırma
    tekrarlanmaz, sadece format düzeltilir)."""
    d = _try_parse(first_content)
    if d is not None:
        return d
    nudge = HumanMessage("Önceki cevabın geçerli JSON değildi. Az önce topladığın "
                          "bilgiyle SADECE geçerli JSON döndür, başka hiçbir şey yazma.")
    for _ in _TEMP_LADDER[1:]:
        try:
            ai = _invoke_resilient(None, messages + [nudge], allow_tools=False)
        except Exception as exc:
            log.warning("    JSON merdiveni istek hatası: %s", exc)
            continue
        d = _try_parse((ai.content or "").strip())
        if d is not None:
            return d
    log.warning("    JSON hiçbir sıcaklıkta kapanmadı — boş sonuç.")
    return {}


def _research_bank(topic: str, bank: str, max_calls: int) -> dict:
    marked: set[str] = set()
    discarded: set[str] = set()
    search_tool = make_bank_search_tool(bank, marked, discarded)
    full_page_tool = make_full_page_tool(bank)
    # BİLİNÇLİ OLARAK bağımsız bir mark_useful tool'u YOK — marking'in tek işlevi
    # gelecekteki context-trim'de bir chunk'ı korumak; son turdaysa (artık
    # aramayacaksa) zaten "gelecek" kalmıyor, marking'in hiçbir anlamı olmaz.
    # Bağımsız mark_useful varken model onu tek başına, boş boş tekrar tekrar
    # çağırıp döngüye giriyordu (kanıtlı bug). Marking SADECE search_bank'a
    # gömülü — üçüncü bir "ilerlemesiz" tool-çağrı yolu fiziksel olarak yok.
    tools = [search_tool, full_page_tool]
    by_name = {t.name: t for t in tools}

    base_system = _SYSTEM.format(bank=bank, topic=topic)
    messages = [SystemMessage(base_system), HumanMessage(f"Konu: {topic}")]
    tried: list[str] = []          # denenen sorgular — context-trim'den ETKİLENMEZ
    repeat_counts: dict[tuple, int] = {}  # (araç,argüman) imzası -> harfiyen kaç kez tekrarlandı
    next_depth: dict[str, int] = {}      # aynı sorgu next=true ile kaç kez derinleştirildi
    calls = 0
    hit_limit = False
    stuck = False                  # aynı sorgu ısrarla tekrarlanıyor — model TIKANMIŞ
    while True:
        messages = _trim_history(messages, marked, discarded)
        if tried:
            messages[0] = SystemMessage(
                base_system + "\n\nŞimdiye kadar DENEDİĞİN sorgular (bunları AYNEN "
                "TEKRARLAMA, gerçekten farklı bir şey dene ya da yeterliyse bitir): "
                + "; ".join(repr(q) for q in tried))
        ai: AIMessage = _invoke_resilient(tools, messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        if calls >= max_calls or stuck:
            hit_limit = hit_limit or calls >= max_calls
            reason = ("Aynı sorguyu ısrarla tekrarlıyorsun, yeni bilgi gelmiyor."
                      if stuck else "Kaynak bütçen doldu.")
            messages.append(HumanMessage(
                f"{reason} Daha fazla arama yapma; şimdiye kadar topladığın "
                "bilgiyle SADECE JSON formatında sonucu ver."))
            ai2 = _invoke_resilient(None, _trim_history(messages, marked, discarded), allow_tools=False)
            messages.append(ai2)
            ai = ai2
            break
        for tc in ai.tool_calls:
            name = tc.get("name", "search_bank")
            args = tc.get("args") or {}
            calls += 1                              # HER tool çağrısı bütçeden düşer —
            # tür ayrımı yapmıyoruz: hangi araçta olursa olsun sonsuz döngü aynı
            # şekilde tehlikeli, bütçe onu fiziksel olarak durdurmalı.
            if name not in by_name:                 # artık var olmayan mark_useful vb.
                out = (f"'{name}' diye bir araç yok. Elindeki search_bank sonuçları "
                       "için işaretleme YENİ bir search_bank çağrısının useful/"
                       "not_useful alanlarına gömülür; artık aramayacaksan doğrudan "
                       "(tool çağırmadan) JSON ile bitir.")
                messages.append(ToolMessage(out, tool_call_id=tc["id"]))
                continue
            tool = by_name[name]
            if name == "search_bank":
                q = args.get("query", "").strip().lower()
                is_next = bool(args.get("next"))
                tried.append(args.get("query", ""))
                if is_next:
                    # next=true MEŞRU bir devam çağrısı (sayfalama) — ama sınırsız
                    # derinlik de aynı hastalığın başka yüzü (kanıtlı: bir konuda
                    # next=true ile 42 kez art arda derinleşip hiç durmadı). Aynı
                    # query'nin next-zincirine de fiziksel bir tavan koyuyoruz —
                    # NEXT_DEPTH_LIMIT'in ötesi azalan getiri, yeni bilgi sayılmaz.
                    n = next_depth.get(q, 0) + 1
                    next_depth[q] = n
                    if n > NEXT_DEPTH_LIMIT:
                        stuck = True
                    log.info("      %s #%d: %r [next #%d]", bank, calls, args.get("query", "")[:60], n)
                else:
                    n = repeat_counts.get(("search_bank", q), 0) + 1
                    repeat_counts[("search_bank", q)] = n
                    if n >= 3:
                        stuck = True
                    log.info("      %s #%d: %r%s", bank, calls, args.get("query", "")[:60],
                              f" [{n}. kez, TIKANMA]" if n >= 2 else "")
            else:
                # search_bank DIŞINDAKİ araçlar da (ör. read_full_page) AYNI hastalığa
                # düşebilir — kanıtlı: model iki URL arasında ping-pong yaparak 10+
                # kez aynı sayfayı tekrar tekrar okudu, hiçbir korumaya takılmadan.
                # Genel (araç, argüman) imzasıyla TÜM araçlar için aynı fiziksel
                # tavan: harfiyen aynı çağrı 3. kez -> TIKANMIŞ.
                sig = (name, tuple(sorted((k, str(v)) for k, v in args.items())))
                n = repeat_counts.get(sig, 0) + 1
                repeat_counts[sig] = n
                if n >= 3:
                    stuck = True
                log.info("      %s #%d [tam sayfa oku]: %s%s", bank, calls,
                          args.get("url", "")[:70], f" [{n}. kez, TIKANMA]" if n >= 2 else "")
            try:
                out = tool.invoke(tc["args"])
            except Exception as exc:
                out = f"HATA: {exc}"
            messages.append(ToolMessage(str(out), tool_call_id=tc["id"]))

    if hit_limit:
        log.warning("  TOOL LİMİTİ: %s / %r (%d çağrı)", bank, topic, calls)
        _log_limit_hit(bank, topic, calls)

    d = _parse_json_ladder(messages, (ai.content or "").strip())
    return {
        "bank": bank,
        "offers": bool(d.get("offers")),
        "attributes": d.get("attributes") or {},
        "sources": d.get("sources") or [],
        "tool_calls": calls,
        "hit_limit": hit_limit,
    }


def research_bank(topic: str, bank: str, max_calls: int = MAX_TOOL_CALLS) -> dict:
    """Bir konuyu bir bankada araştırır. ASLA istisna fırlatmaz — bir bankanın
    araştırması patlarsa (context, ağ, parse...) hata raporlanır, pipeline sürer."""
    try:
        return _research_bank(topic, bank, max_calls)
    except Exception as exc:
        log.error("  ARAŞTIRMA HATASI: %s / %r: %s: %s", bank, topic, type(exc).__name__, exc)
        return {"bank": bank, "offers": False, "attributes": {}, "sources": [],
                "tool_calls": 0, "hit_limit": False, "error": str(exc)}


def _log_limit_hit(bank: str, topic: str, calls: int) -> None:
    LIMIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LIMIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"bank": bank, "topic": topic, "calls": calls},
                            ensure_ascii=False) + "\n")
