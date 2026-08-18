"""Tablo havuzu bakım ajanı: tablo oluşturma sürecinin SONUNDA (ya da istenildiğinde)
elle çalıştırılır. Her tabloyu tek tek dolaşır, o tablonun search_tables aracıyla
(classify_agent'la AYNI desen: ilk 5 aday, next=true ile sonraki 5, useful/
not_useful ile eleme) havuzda kendisiyle GERÇEKTEN aynı ürün/kampanya türünü
kıyaslayan başka bir tablo olup olmadığını araştırmasını sağlar. Mükerrer derse,
iki tabloyu synth.merge_tables ile (gerekirse ek sütun ekleyerek, veri kaybetmeden)
TEK tabloda birleştirir.

Hiçbir sabit/manuel benzerlik eşiği (threshold) YOK — hangi adayların gerçekten
mükerrer olduğuna, arama sonuçlarını görüp inceleyerek TAMAMEN ajan karar verir.

Kullanım:
  python -m dataprep.compare.dedup
"""
from __future__ import annotations

import json
import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from llm import get_llm

from ..net_limit import NET_SEM
from . import store, synth
from .retrieval import index_table, make_table_search_tool

log = logging.getLogger("dataprep.compare.dedup")

MAX_CALLS = 10
NEXT_DEPTH_LIMIT = 4
MAX_RETRY_SECONDS = 120
_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir karşılaştırma-tablosu mükerrerlik denetleyicisisin. TERMİNOLOJİ: "
    "bunlar KATILIM BANKASI (faizsiz/İslami bankacılık) verileri — üreteceğin "
    "her metinde 'kredi'/'faiz' değil 'finansman'/'kâr payı'/'kâr oranı' kullan "
    "(istisna: 'kredi kartı' yerleşik ürün adı, olduğu gibi kalır).\n\n"
    "Sana BİR "
    "tablonun id'si ve açıklaması verilecek. Görevin: search_tables aracıyla "
    "mevcut tablo havuzunda bu tabloyla GERÇEKTEN AYNI ürün/kampanya TÜRÜNÜ "
    "kıyaslayan BAŞKA bir/birkaç tablo var mı diye araştırmak. Kendi id'ni "
    "sonuçlarda görürsen yok say. Kararını açıklamaların ANLAMINA göre ver, "
    "kelime benzerliğine değil.\n\n"
    "search_tables her çağrıda embedding tabanlı 5 aday getirir — intent alanına "
    "ne aradığını yaz (formalite değil, arama kalitesini etkiler). Yetmezse AYNI "
    "sorguyla next=true ile SONRAKİ 5'i görebilirsin; bunu yaparken önceki "
    "adaylardan işine yaramayanları not_useful ile işaretle (geçmişten silinir), "
    "gerçekten mükerrer olduğuna karar verdiklerini useful ile işaretle — "
    "böylece sadece işe yarayanlar elde kalır. Emin olana kadar farklı "
    "sorgularla arayabilirsin, ama gereksiz yere ısrar etme.\n\n"
    "İncelenen tablo — id: {tid}\nAçıklama: {docstring}\n\n"
    'Emin olunca SADECE JSON: {{"duplicates": ["<id>", ...]}} (yoksa boş liste)'
)


def _is_permanent(exc: Exception) -> bool:
    s = str(exc)
    return any(code in s for code in ("400", "401", "403", "404", "BadRequest"))


def _invoke_resilient(tools, messages, allow_tools: bool = True):
    start = time.time()
    delay = 1.0
    attempt = 0
    while True:
        t = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            with NET_SEM:
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
            log.warning("    dedup istek hatası (deneme %d, %.0fs): %s",
                        attempt + 1, elapsed, type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, 30)
            attempt += 1


def _try_parse(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _parse_json_ladder(messages: list, first_content: str) -> dict | None:
    d = _try_parse((first_content or "").strip())
    if d is not None:
        return d
    nudge = HumanMessage("Önceki cevabın geçerli JSON değildi. SADECE geçerli "
                          "JSON döndür, başka hiçbir şey yazma.")
    for _ in _TEMP_LADDER[1:]:
        try:
            ai = _invoke_resilient(None, messages + [nudge], allow_tools=False)
        except Exception as exc:
            log.warning("    JSON merdiveni istek hatası: %s", exc)
            continue
        d = _try_parse((ai.content or "").strip())
        if d is not None:
            return d
    return None


def find_duplicates(table_id: str, docstring: str) -> list[str]:
    """Bir tablo için havuzda gerçek mükerrer var mı araştırır — ASLA istisna
    fırlatmaz (hata olursa boş liste döner, o tablo atlanır)."""
    try:
        return _find_duplicates(table_id, docstring)
    except Exception as exc:
        log.error("  MÜKERRERLİK ARAŞTIRMA HATASI: %s: %s: %s", table_id, type(exc).__name__, exc)
        return []


def _find_duplicates(table_id: str, docstring: str) -> list[str]:
    marked: set[str] = set()
    discarded: set[str] = set()
    search_tool = make_table_search_tool(store.load_registry, marked, discarded)
    tools = [search_tool]
    messages = [SystemMessage(_SYSTEM.format(tid=table_id, docstring=docstring)),
                HumanMessage("Araştır.")]
    calls = 0
    repeat_counts: dict[str, int] = {}
    next_depth: dict[str, int] = {}
    stuck = False
    ai: AIMessage
    while True:
        ai = _invoke_resilient(tools, messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        if calls >= MAX_CALLS or stuck:
            reason = ("Aynı sorguyu tekrarlıyorsun, yeni bilgi gelmiyor."
                      if stuck else "Arama bütçen doldu.")
            messages.append(HumanMessage(
                f"{reason} Daha fazla arama yapma; SADECE JSON ile karar ver."))
            ai = _invoke_resilient(None, messages, allow_tools=False)
            messages.append(ai)
            break
        for tc in ai.tool_calls:
            calls += 1
            args = tc.get("args") or {}
            q = args.get("query", "").strip().lower()
            if args.get("next"):
                n = next_depth.get(q, 0) + 1
                next_depth[q] = n
                if n > NEXT_DEPTH_LIMIT:
                    stuck = True
            else:
                n = repeat_counts.get(q, 0) + 1
                repeat_counts[q] = n
                if n >= 3:
                    stuck = True
            try:
                out = search_tool.invoke(tc["args"])
            except Exception as exc:
                out = f"HATA: {exc}"
            messages.append(ToolMessage(str(out), tool_call_id=tc["id"]))

    d = _parse_json_ladder(messages, ai.content)
    dups = [x for x in (d or {}).get("duplicates", []) if x != table_id]
    return dups


def merge_pair(canon_id: str, dup_id: str) -> bool:
    """İki tabloyu (canon_id kalır, dup_id silinir) synth.merge_tables ile
    birleştirir; registry/ledger/Qdrant güncellenir. Başarısızsa False döner,
    HİÇBİR ŞEY değiştirmez (LLM ulaşılamazsa güvenli — veri kaybı yok)."""
    a = store.load_table(canon_id)
    b = store.load_table(dup_id)
    if a is None or b is None:
        return False
    subcats = store.subcategory_examples()
    merged = synth.merge_tables(a, b, subcats)
    if merged is None:
        log.warning("  BİRLEŞTİRME BAŞARISIZ (LLM ulaşılamadı): %s + %s", canon_id, dup_id)
        return False
    sources = dict(a.get("sources", {}))
    sources.update(b.get("sources", {}))
    store.overwrite_table(canon_id, merged["docstring"], merged["columns"],
                            merged["rows"], sources, merged["category"], merged["subcategory"])
    store.delete_table(dup_id)
    store.remap_ledger_table(dup_id, canon_id)
    try:
        topic = a.get("topic") or canon_id
        index_table(canon_id, topic, merged["category"],
                    merged["subcategory"], merged["docstring"])
    except Exception as exc:
        log.warning("  [İNDEKS HATASI] %s: %s: %s", canon_id, type(exc).__name__, exc)
    log.info("  [BİRLEŞTİ] %s + %s -> %s", canon_id, dup_id, canon_id)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    processed: set[str] = set()   # bu koşuda zaten karara bağlanmış id'ler (ya
                                    # kalıcı ya da silinmiş) — tekrar işlenmesin
    while True:
        registry = store.load_registry()
        remaining = [r for r in registry if r["id"] not in processed]
        if not remaining:
            break
        r = remaining[0]
        table_id = r["id"]
        table = store.load_table(table_id)
        if table is None:
            processed.add(table_id)
            continue
        dups = find_duplicates(table_id, table["docstring"])
        # registry'de gerçekten var olan, kendisi dışındaki adaylar
        valid_dups = [d for d in dups if d != table_id and store.load_table(d) is not None]
        if not valid_dups:
            log.info("[tekil] %s", table_id)
            processed.add(table_id)
            continue
        log.info("[MÜKERRER] %s -> %s", table_id, valid_dups)
        canon = table_id
        for dup_id in valid_dups:
            if dup_id == canon or store.load_table(dup_id) is None:
                continue
            ok = merge_pair(canon, dup_id)
            if not ok:
                processed.add(dup_id)   # birleşemedi, ikisini de ayrı bırak
        processed.add(canon)


if __name__ == "__main__":
    main()
