"""TRIAGE — bir seviyedeki dalları ele: DIVE / FETCH / SKIP.

Her karar LLM tarafından STRUCTURED JSON (response_format=json_object) ile verilir;
kural/regex tabanlı sınıflama YOKTUR. Girdi: URL + başlık + örnek alt-başlıklar.

Sağlamlık:
  * Kararlar `seg` ile DEĞİL, benzersiz INDEX (id) ile eşlenir — farklı
    ebeveynlerin aynı adlı çocukları çakışmaz.
  * JSON parse edilemezse sıcaklık 0->0.3->0.6->1.0; istek hatasında 5-retry.

LLM (gemma) zorunludur. Host yoksa süreç kural yedeğine DÜŞMEZ; hata verir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

log = logging.getLogger("dataprep.crawl.policy")

BATCH = 1           # her dal ayrı LLM çağrısı (teker teker; en güvenilir, toplu input yok)

GOAL = (
    "Bir katılım bankasının ürün ve kampanyalarıyla ilgili HER TÜRLÜ bilgiyi, "
    "chatbot ve dashboard'da müşteriye gösterilebilecek şekilde topluyoruz. "
    "Kurumsal, prosedür, mevzuat gibi müşterinin doğrudan ürün ya da ürün bilgisi "
    "olarak alamayacağı içeriklerle İLGİLENMİYORUZ."
)

VERDICTS = ("DIVE", "FETCH", "SKIP")


def _branch_line(i: int, node) -> str:
    ex = ", ".join(t for t in node.sample_titles if t)[:200]
    leaf = " [tek sayfa]" if not node.children and node.page_count == 1 else ""
    return (f"{i}. /{node.seg}{leaf} — altında ~{node.page_count} sayfa"
            + (f"; örnek: {ex}" if ex else ""))


_SYS = (
    "Sen bir web-crawler karar motorusun. " + GOAL + "\n"
    "Sana numaralandırılmış dallar verilecek. HER dal için tek karar ver:\n"
    "  DIVE  = alt dallarına in\n"
    "  FETCH = bu sayfayı indir\n"
    "  SKIP  = bu dalı (ve altını) atla\n"
    "İlgili olup olmadığına başlık ve örnek alt-başlıklardan sen karar ver.\n"
    "HER numara için karar ver, hiçbirini atlama. Kararı dalın NUMARASI (id) ile ver.\n"
    'Yanıtı YALNIZCA geçerli JSON olarak ver: '
    '{"decisions":[{"id":1,"verdict":"DIVE|FETCH|SKIP","reason":"kısa"}]}'
)


def _parse_decisions(text: str) -> dict[int, tuple[str, str]]:
    """LLM metninden {id: (verdict, reason)} çıkarır (gevşek JSON toleransı)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out: dict[int, tuple[str, str]] = {}
    for d in data.get("decisions", []):
        try:
            i = int(d.get("id"))
        except (TypeError, ValueError):
            continue
        v = str(d.get("verdict", "")).upper()
        if v in VERDICTS:
            out[i] = (v, str(d.get("reason", ""))[:200])
    return out


TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)   # parse başarısızsa sıcaklığı kademeli artır


async def _ainvoke_retry(model, msgs, retries: int = 5):
    """LLM isteğini İSTEK HATASINDA 3 kez artan beklemeyle dener. Hepsi
    başarısızsa None döner (üst katman parse-fail sayıp sıcaklık merdiveni/
    güvenli varsayılana düşer)."""
    for attempt in range(1, retries + 1):
        try:
            return await model.ainvoke(msgs)
        except Exception as exc:
            log.warning("    LLM istek hatası (deneme %d/%d): %s",
                        attempt, retries, type(exc).__name__)
            if attempt < retries:
                await asyncio.sleep(min(1.5 * attempt, 8))
    return None


async def _one_pass(model, nodes) -> dict[int, tuple[str, str]]:
    """Tek STRUCTURED JSON geçişi; {id:(verdict,reason)} döner."""
    from langchain_core.messages import HumanMessage, SystemMessage
    listing = "\n".join(_branch_line(i, n) for i, n in enumerate(nodes, 1))
    msgs = [SystemMessage(_SYS),
            HumanMessage(f"Dallar:\n{listing}\n\nHer numara için karar ver.")]
    resp = await _ainvoke_retry(model, msgs)
    if resp is None:
        return {}
    return _parse_decisions(getattr(resp, "content", "") or "")


async def _triage_batch(nodes: list, *, llm, client, budget) -> None:
    """Tek grup için STRUCTURED JSON (response_format) triage; index ile eşle.

    JSON parse edilemezse sıcaklığı 0 -> 0.3 -> 0.6 -> 1.0 artırarak yeniden dener."""
    decisions: dict[int, tuple[str, str]] = {}
    for temp in TEMP_LADDER:
        if all(i in decisions for i in range(1, len(nodes) + 1)):
            break
        if budget.get("llm_calls_left", 0) <= 0:
            raise RuntimeError("LLM çağrı bütçesi tükendi; --max-llm artır.")
        budget["llm_calls_left"] -= 1
        try:                              # STRUCTURED: geçerli JSON zorla
            model = llm.bind(temperature=temp, response_format={"type": "json_object"})
        except Exception:
            model = llm
        got = await _one_pass(model, nodes)
        if got:
            decisions.update(got)
        if not all(i in decisions for i in range(1, len(nodes) + 1)):
            log.info("  (JSON eksik, sıcaklık %.1f ile tekrar)", temp)

    for i, n in enumerate(nodes, 1):
        if i in decisions:
            n.verdict, reason = decisions[i]
        else:
            # LLM kararı yoksa: çok-sayfalı dalı KAYBETMEMEK için DIVE (indir, alt seviyede LLM yine bakar);
            # tek sayfayı SKIP. (regex değil; yalnız veri-kaybı koruması)
            n.verdict = "DIVE" if n.page_count > 1 else "SKIP"
            reason = "LLM karar vermedi (güvenli varsayılan)"
        log.info("  /%s -> %s (%s)", n.seg, n.verdict, reason)


async def triage_level(nodes: list, *, llm, client=None, budget: dict | None = None) -> None:
    """Bir seviyedeki tüm dallara .verdict atar. Büyük seviye gruplara bölünür."""
    budget = budget or {"llm_calls_left": 999, "look_left": 0}
    if not nodes:
        return
    if llm is None:
        raise ValueError("triage için LLM zorunlu (kural yedeği yok).")
    for start in range(0, len(nodes), BATCH):
        await _triage_batch(nodes[start:start + BATCH], llm=llm, client=client, budget=budget)
