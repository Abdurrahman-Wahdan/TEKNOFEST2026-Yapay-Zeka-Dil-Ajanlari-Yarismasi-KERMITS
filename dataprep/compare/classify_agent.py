"""Sayfa-sınıflandırma ajanı: bir sayfanın (1) kıyaslanabilir SOMUT bir ürün/
kampanya olup olmadığına, (2) öyleyse mevcut tablo havuzunda AYNI konuyu
kıyaslayan bir tablo olup olmadığına karar verir.

Tablo havuzu büyüdükçe (yüzlerce olabilir) TÜMÜNÜ tek prompt'a sığdırmak mümkün
değil — bank_agent'taki search_bank ile AYNI felsefe: search_tables (embedding
bazlı arama) aracı veriyoruz, LLM karar verene kadar özgürce (farklı sorgularla)
tekrar arayabilir. Karar tamamen modele ait; burada içerik kararına karışan bir
kural yok, yalnızca mühendislik güvenlikleri (bank_agent'la aynı desen: kaynak
bütçesi + dayanıklı istek + sıcaklık merdiveni)."""
from __future__ import annotations

import json
import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from llm import get_llm

from ..net_limit import NET_SEM
from . import store
from .retrieval import make_table_search_tool

log = logging.getLogger("dataprep.compare.classify_agent")

MAX_CALLS = 10                 # bir sayfa sınıflandırması için arama bütçesi
NEXT_DEPTH_LIMIT = 4           # aynı sorguyu next=true ile kaç kez derinleştirebilir
MAX_RETRY_SECONDS = 120
_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir KATILIM BANKASI sayfa-sınıflandırma ajanısın. Sana bir sayfanın "
    "metni verilecek. ÖNEMLİ — TERMİNOLOJİ: katılım bankacılığı faizsiz/İslami "
    "bankacılıktır; ÜRETTİĞİN HER METİNDE (topic dahil) 'kredi'/'faiz' değil "
    "'finansman'/'kâr payı'/'kâr oranı' kullan. Kaynak sayfa konvansiyonel terim "
    "kullansa bile SEN katılım bankacılığı terimine çevir. Tek istisna: 'kredi "
    "kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu gibi kalır.\n\n"
    "search_tables aracı embedding (anlam vektörü) tabanlı arama yapar — sorgun "
    "ile aranan arasındaki vektör karşılaştırması için modelin bir 'niyet' "
    "bilgisine ihtiyacı var; her çağrıda intent alanına o aramayla TAM OLARAK ne "
    "bulmaya çalıştığını yaz — bu formalite değil, arama kalitesini gerçekten "
    "belirliyor. Her çağrı 5 aday getirir; yetmezse AYNI sorguyla next=true "
    "gönderip SONRAKİ 5'i görebilirsin, bunu yaparken önceki adaylardan işine "
    "yaramayanları not_useful ile işaretle (geçmişten silinir), işine yarayanları "
    "useful ile işaretle — böylece sadece gerçekten kullanışlı adaylar elde "
    "kalır.\n\nGörevin:\n\n"
    "1) Sorulacak soru tam olarak şu: bu sayfa, RAKİP bankalarla KARŞILAŞTIRILABİLİR "
    "bir KAMPANYA mı (bir promosyon/kazanım teklifi), YA DA RAKİP bankalarla "
    "KARŞILAŞTIRILABİLİR bir ÜRÜN mü (müşterinin bağımsız olarak edinebileceği "
    "somut bir finansal ürün: finansman, hesap, kart, yatırım aracı vb.)? Değilse "
    "— bankanın genel bir HİZMETİ/işlevi (dijital kanal özelliği, altyapı/iş "
    "birliği bilgisi, güvenlik, başvuru süreci gibi operasyonel bir yetenek) ya da "
    "daha derin/arka plan bir bilgiyse — comparable=false de; sayının/oranın somut "
    "olması tek başına yeterli değil, asıl soru bunun bağımsız edinilebilen bir "
    "ürün ya da katılınabilen bir kampanya olup olmadığı. Sayfa birçok farklı "
    "ürünü listeleyen bir MENÜ/genel-bakış sayfasıysa (kategorinin tamamını "
    "anlatıyorsa, tek bir somut ürünü değil) de comparable=false de — "
    "o ürünlerin her biri kendi sayfasında ayrıca karşına çıkacak.\n\n"
    "2) Kıyaslanabilirse: search_tables aracıyla mevcut karşılaştırma tablosu "
    "havuzunda bu ürün/kampanyayla AYNI ŞEY olan bir tablo var mı ara. Kararını "
    "açıklamaların ANLAMINA göre ver, kelime benzerliğine değil — gördüğün TAM "
    "sayfa metniyle kıyasla. YENİ TABLO AÇMAK PAHALI bir işlemdir (10 bankaya tam "
    "araştırma tetikler) — bu yüzden 'yeni konu' kararına varmadan önce en az "
    "birkaç FARKLI ifadeyle gerçekten aradığından emin ol, tek aramaya güvenip "
    "vazgeçme. Yine de gerçekten eşleşme yoksa ısrar etme, ilk bulduğun alakasız "
    "sonuca da zorla uydurma — eşleşme yanlış olursa daha sonra ayrı bir doğrulama "
    "adımıyla düzeltilir, ama ilk elden isabetli olman süreci hızlandırır.\n\n"
    "3) HER durumda (eşleşse de eşleşmese de) bu sayfanın ASIL ANLATTIĞI somut "
    "ürün/kampanya TÜRÜNÜ tanımlayan bir konu adı belirle. Bunu bir insan analist "
    "gibi düşünerek karar ver: bu gerçekten TEK BAŞINA ayrı, anlamlı bir ürün/"
    "kampanya mı — yoksa (a) birçok farklı ürünü birden kapsayan bir ÜST BAŞLIK mı, "
    "(b) bir ürünün yalnızca dar bir alt-koşulu/detayı mı, ya da (c) zaten var olan "
    "bir ürün/kampanyayla AYNI temel AMACA/HEDEFE hizmet eden, sadece somut "
    "mekanizması (nasıl uygulandığı, hangi araçla sağlandığı, hangi koşullarla "
    "sunulduğu) farklılaşan bir VARYANTI mı? Bu ayrım hem ÜRÜNLER hem KAMPANYALAR "
    "için aynı şekilde geçerlidir — hedef/amaç aynıysa, sadece uygulama şekli "
    "farklıysa bu tek bir ailedir. (a), (b) ve (c) durumlarında bunu ayrı bir "
    "konu SAYMA — (c) durumunda search_tables ile o temel ürün/kampanya ailesinin "
    "tablosunu ara ve varsa ona eşleştir; farklılaşan detay o tablonun bir "
    "SÜTUNU olabilir, ayrı tablo açma sebebi değildir. "
    "Konu adı bankanın kendi verdiği MARKA/ÜRÜN ADINI içermesin — marka adları "
    "banka-özeldir, başka bankada aranmaz; bunun yerine ürünün/kampanyanın "
    "sektörde genel kabul gören TÜRÜNÜ/HEDEFİNİ tanımla. Eşleşme bulduysan bu "
    "konu adı mevcut tablonun konusuyla TUTARLI olsun (sonraki araştırmalarda "
    "kullanılacak).\n\n"
    "Emin olunca SADECE şu JSON ile (tool çağırmadan) bitir:\n"
    '{{"comparable": true|false, "fits_table": "<eşleşen mevcut tablo id\'si ya '
    'da boş>", "topic": "<bu sayfanın anlattığı konu adı, banka adı geçmesin>"}}\n\n'
    "Sayfa URL: {url}\n\nSayfa metni:\n\"\"\"{body}\"\"\""
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
            log.warning("    classify istek hatası (deneme %d, %.0fs): %s",
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


def _classify_page(body: str, url: str) -> dict | None:
    marked: set[str] = set()
    discarded: set[str] = set()
    search_tool = make_table_search_tool(store.load_registry, marked, discarded)
    tools = [search_tool]
    system = _SYSTEM.format(url=url or "-", body=body[:8000])
    messages = [SystemMessage(system), HumanMessage("Sınıflandır.")]
    calls = 0
    repeat_counts: dict[str, int] = {}   # aynı sorgu (next=false) kaç kez tekrarlandı
    next_depth: dict[str, int] = {}      # aynı sorgu next=true ile kaç kez derinleştirildi
    stuck = False                        # bank_agent'taki İLE AYNI kanıtlı desen
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
                f"{reason} Daha fazla arama yapma; şimdiye kadar gördüklerinle "
                "SADECE JSON formatında karar ver."))
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
    if d is None:
        return None
    registry = store.load_registry()
    fits = (d.get("fits_table") or "").strip()
    if fits and not any(r["id"] == fits for r in registry):
        fits = ""                        # uydurma/geçersiz id -> yok say
    return {"comparable": bool(d.get("comparable")), "fits_table": fits,
            "topic": (d.get("topic") or "").strip()}


def classify_page(body: str, url: str) -> dict | None:
    """ASLA istisna fırlatmaz — hata olursa None döner (üst katman retry sonra
    dener, own_verdict kaydedilmediği için sayfa atlanmaz)."""
    try:
        return _classify_page(body, url)
    except Exception as exc:
        log.error("  SINIFLANDIRMA HATASI: %s: %s: %s", url, type(exc).__name__, exc)
        return None
