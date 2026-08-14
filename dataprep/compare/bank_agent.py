"""Banka-scoped researcher subagent — Qwen (bizim stack'te function-calling'de
Gemma'dan daha güvenilir), search_bank + mark_useful tool'larına serbest erişimle
bir konuyu kendi bankasında araştırır. Sorguyu nasıl kuracağı, kaç kez arayacağı,
ne zaman yeterli bilgiye sahip olduğu tamamen KENDİ kararı — burada içerik
kararına karışan bir kural yok.

Mühendislik güvenlikleri (İÇERİK kararı değil, fiziksel/operasyonel sınırlar):
  * KAYNAK BÜTÇESİ (MAX_TOOL_CALLS) — sonsuz döngüye karşı. Aşılırsa loglanır.
  * BAĞLAM PENCERESİ (MAX_CONTEXT_CHARS, ~64k token hedefi) — server 128k token
    destekliyor ama biz zorlamıyoruz. Sıkışınca İŞARETLENMEMİŞ eski arama
    turları önce atılır; agent mark_useful ile hangi sonuçların tabloya
    gireceğini işaretler, işaretliler mümkün olduğunca korunur.
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
from .retrieval import make_bank_search_tool, make_full_page_tool, make_mark_tool

log = logging.getLogger("dataprep.compare.bank_agent")

MAX_TOOL_CALLS = int(os.environ.get("COMPARE_MAX_TOOL_CALLS", "30"))
# Server 131072 token. Hedef ~64k — erken ve GÜVENLİ compact (char/token oranı
# beklenenden kötü çıktığı ampirik olarak görüldü, o yüzden muhafazakâr karakter eşiği).
MAX_CONTEXT_CHARS = int(os.environ.get("COMPARE_MAX_CONTEXT_CHARS", "60000"))
MAX_RETRY_SECONDS = 120
LIMIT_LOG = Path(__file__).resolve().parents[2] / "data" / "_tables" / "_tool_limit_hits.jsonl"

_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir KATILIM BANKASI araştırmacısısın. Görevin: '{bank}' katılım bankasının "
    "'{topic}' konusuyla ilgili GERÇEK bir ürünü/kampanyası olup olmadığını "
    "search_bank aracıyla araştırmak. Sorguyu nasıl kuracağına, kaç kez arayacağına "
    "ve ne zaman yeterli bilgiye sahip olduğuna SEN karar ver — her çağrı 5 sonuç "
    "getirir, ama bunlar sayfanın küçük PARÇALARIDIR (chunk). Bir sonuç kesilmiş/"
    "yarım/yetersiz görünüyorsa read_full_page(url) ile o sayfanın/PDF'in TÜM "
    "parçalarını birleştirilmiş, TAM metin olarak okuyabilirsin. Gelen sonuçlar "
    "konudan uzaklaşıyorsa ya da bu bankada böyle bir şey olmadığına kanaat "
    "getirdiysen ısrar etme; bu da geçerli bir sonuçtur.\n\n"
    "Kullanışlı bulduğun sonuçları mark_useful ile point_id'leriyle işaretle — "
    "araştırma uzarsa işaretlenmemiş eski sonuçlar bellekten silinebilir, "
    "işaretlediklerin korunur. Son cevabını kuracağın kanıtları işaretlemeyi unutma.\n\n"
    "Emin olunca SADECE şu JSON ile (tool çağırmadan) bitir:\n"
    '{{"offers": true|false, "attributes": {{"<alan adı>": "<değer>", ...}}, '
    '"sources": [{{"url": "...", "note": "<bu kaynaktan ne aldın>"}}]}}\n'
    "attributes: bu ürün/kampanyayı EN İYİ tanımlayan alanları SEN belirle (oran, "
    "tutar aralığı, vade, ücret, koşul, geçerlilik — konuya göre değişir, sabit şema "
    "yok). offers=false ise attributes boş, sources yine de aradığın kanıtı gösterir. "
    "UYDURMA — yalnız arama sonuçlarında gördüğünü yaz. 'katılım bankası' terminolojisini kullan."
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


def _trim_history(messages: list, marked: set[str]) -> list:
    """Bağlam MAX_CONTEXT_CHARS'ı aşarsa eski arama-turlarını at. İKİ geçiş:
    önce İŞARETLENMEMİŞ turlar atılır (mark_useful ile korunanlar dokunulmaz);
    hâlâ sığmıyorsa (son çare) işaretliler de atılmaya başlar. system+human
    (ilk 2 mesaj) hep kalır."""
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
                llm = get_llm("qwen", temperature=t)
                if allow_tools and tools:
                    llm = llm.bind_tools(tools)
                return llm.invoke(messages)
        except Exception as exc:
            if _is_permanent(exc):
                raise
            elapsed = time.time() - start
            if elapsed + delay >= MAX_RETRY_SECONDS:
                raise
            log.warning("    Qwen istek hatası (deneme %d, t=%.1f): %s — %.0fs sonra tekrar",
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
    search_tool = make_bank_search_tool(bank)
    full_page_tool = make_full_page_tool(bank)
    marked: set[str] = set()
    mark_tool = make_mark_tool(marked)
    tools = [search_tool, full_page_tool, mark_tool]
    by_name = {t.name: t for t in tools}

    base_system = _SYSTEM.format(bank=bank, topic=topic)
    messages = [SystemMessage(base_system), HumanMessage(f"Konu: {topic}")]
    tried: list[str] = []          # denenen sorgular — context-trim'den ETKİLENMEZ
    calls = 0
    hit_limit = False
    while True:
        messages = _trim_history(messages, marked)
        if tried:
            messages[0] = SystemMessage(
                base_system + "\n\nŞimdiye kadar DENEDİĞİN sorgular (bunları AYNEN "
                "TEKRARLAMA, gerçekten farklı bir şey dene ya da yeterliyse bitir): "
                + "; ".join(repr(q) for q in tried))
        ai: AIMessage = _invoke_resilient(tools, messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        if calls >= max_calls:
            hit_limit = True
            messages.append(HumanMessage(
                "Kaynak bütçen doldu. Daha fazla arama yapma; şimdiye kadar "
                "topladığın bilgiyle SADECE JSON formatında sonucu ver."))
            ai2 = _invoke_resilient(None, _trim_history(messages, marked), allow_tools=False)
            messages.append(ai2)
            ai = ai2
            break
        for tc in ai.tool_calls:
            name = tc.get("name", "search_bank")
            tool = by_name.get(name, search_tool)
            args = tc.get("args") or {}
            if name == "search_bank":
                calls += 1                          # yalnız ARAMA çağrıları bütçeden düşer
                q = args.get("query", "")
                tried.append(q)
                log.info("      %s #%d: %r", bank, calls, q[:60])
            elif name == "read_full_page":
                log.info("      %s [tam sayfa oku]: %s", bank, args.get("url", "")[:70])
            else:
                log.info("      %s [işaretle]: %s", bank, args.get("point_ids"))
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
