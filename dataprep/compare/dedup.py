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
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from llm import get_llm
from llm.providers.vllm_provider import reset_http_pool

from config import tunnel

from ..net_limit import NET_SEM
from .json_mod import llm_kwargs
from . import store, synth
from .retrieval import drop_table_index, index_table, make_table_search_tool

log = logging.getLogger("dataprep.compare.dedup")

# 10 -> 100 (kullanıcı kararı 2026-08-22): sonsuz döngü emniyeti,
# veri sınırı DEĞİL.
MAX_CALLS = 100
# Aynı sorguyu next=true ile kaç kez derinleştirebilir (sayfalama) — bank_agent'taki
# 42-derinlik olayı gibi sınırsız bir sızıntıya karşı fiziksel tavan (genel
# tekrar-kontrolü next zincirini yakalayamıyor, her adımda offset değişiyor).
NEXT_DEPTH_LIMIT = 50
_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir karşılaştırma-tablosu mükerrerlik denetleyicisisin. BUGÜNÜN "
    "TARİHİ: {today} — geçerlilik/süre ile ilgili değerlendirmelerini buna "
    "göre yap. TERMİNOLOJİ: "
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
    "AŞIRI GENELLEME YAPMA: iki tablo ancak AYNI ürün/kampanyayı kıyaslıyorsa "
    "mükerrerdir. Aynı üst başlık altında olmaları (ör. ikisi de 'sigorta' ya "
    "da ikisi de 'kampanya') yetmez — 'kasko sigortası' ile 'sağlık sigortası' "
    "AYRI tablolardır, 'umre finansmanı' ile 'taşıt finansmanı' AYRI tablolardır. "
    "Birleştirdiğinde ortaya kategorisi ve alt kategorisi BULANIK, her şeyi "
    "içeren bir tablo çıkacaksa BİRLEŞTİRME.\n\n"
    "İncelenen tablo — id: {tid}\nAçıklama: {docstring}\n\n"
    'Emin olunca SADECE JSON: {{"duplicates": ["<id>", ...]}} (yoksa boş liste)'
)


def _is_permanent(exc: Exception) -> bool:
    """GERÇEKTEN kalıcı 4xx mi? 400/403 BİLEREK listede DEĞİL — tünel soketi
    bayatlayınca nginx "400 / 0 byte" döndürüyor ve istek sunucuya HİÇ
    ulaşmıyor; aynı istek saniyeler sonra 200 dönüyor (sunucu access log'uyla
    kanıtlandı, 2026-08-18). 403 de tünelden geliyor. Bunları kalıcı sayıp pes
    etmek işi sessizce yarıda bırakırdı. Bkz. vlm.py::_GECICI_4XX."""
    s = str(exc)
    return any(code in s for code in ("401", "404", "413", "422", "BadRequest"))


def _invoke_resilient(tools, messages, allow_tools: bool = True, start_attempt: int = 0):
    """ASLA PES ETMEZ (60s'de sabitlenen üstel backoff) — kalıcı (4xx) hatada
    denemez, hemen fırlatır. Uzun süredir başarısızsa [DEDUP_UZUN_SÜRELİ_HATA]
    ile uyarır ama DURMAZ."""
    start = time.time()
    delay = 1.0
    attempt = start_attempt
    last_warn = 0.0
    while True:
        t = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            # Araç bağlanmayacaksa düz JSON bekleniyor -> vLLM JSON zorlaması
            # (bkz. json_mod.py). Araçla birlikte kullanılmaz.
            _arac_var = bool(allow_tools and tools)
            llm = get_llm("gemma", temperature=t, **llm_kwargs(not _arac_var))
            if _arac_var:
                llm = llm.bind_tools(tools)
            _t0 = time.time()
            with NET_SEM:
                _res = llm.invoke(messages)
            # UYARLANABILIR SINIRLAYICI geri bildirimi (bkz. net_limit.py):
            # hizli biten istek -> eszamanlilik artirilabilir; tunelin omur
            # sinirina yaklasan istek -> artirilmamali.
            NET_SEM.report(ok=True, duration=time.time() - _t0)
            return _res
        except Exception as exc:
            if _is_permanent(exc):
                raise
            elapsed = time.time() - start
            # BAĞLANTI GÜVENLİĞİ (kullanıcı kararı 2026-08-22, önceki
            # aşamalarla AYNI): hata aldığımız bağlantıya bir daha
            # dönülmez — havuz TAMAMEN kapatılır, tazesi açılır.
            reset_http_pool(f"{type(exc).__name__}")
            tunnel.refresh_if_needed()      # büyük ihtimalle tünel URL'i değişti
            if elapsed - last_warn >= 300:
                log.warning("    [DEDUP_UZUN_SÜRELİ_HATA] %.0fs'dir başarısız (deneme %d): %s — "
                            "denemeye DEVAM ediyor (durmuyor)", elapsed, attempt + 1, type(exc).__name__)
                last_warn = elapsed
            else:
                log.warning("    dedup istek hatası (deneme %d, %.0fs): %s",
                            attempt + 1, elapsed, type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            attempt += 1


def _try_parse(text: str, bekleyen: tuple[str, ...] = ()) -> tuple[dict | None, str]:
    """(sonuç, hata_metni). Hata metni modele AYNEN geri verilir — genel bir
    "geçersizdi" uyarısı yerine SOMUT sorun (parse hatası ya da eksik alan)
    bildirilirse model düzeltmeyi hedefli yapar."""
    if not text:
        return None, "Cevap boş geldi."
    d = None
    hata = ""
    try:
        d = json.loads(text)
    except json.JSONDecodeError as exc:
        hata = f"JSON ayrıştırılamadı: {exc}"
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                hata = ""
            except json.JSONDecodeError as exc2:
                hata = f"JSON ayrıştırılamadı: {exc2}"
    if d is None:
        return None, hata or "Cevapta JSON bulunamadı."
    if not isinstance(d, dict):
        return None, f"Beklenen bir JSON nesnesi, gelen: {type(d).__name__}."
    eksik = [k for k in bekleyen if k not in d]
    if eksik:
        return None, (f"JSON'da şu alanlar eksik: {', '.join(eksik)}. "
                      f"Gelen alanlar: {', '.join(sorted(d)) or '(hiç)'}.")
    return d, ""


def _parse_json_ladder(messages: list, first_content: str, _cycles: int = 2,
                        bekleyen: tuple[str, ...] = ()) -> dict | None:
    """JSON bozuksa ya da beklenen alanlar eksikse yeniden cevap ister.

    Sıra (kullanıcı kararı 2026-08-20) — her sıcaklıkta ÖNCE feedback'siz,
    SONRA somut hatayı içeren feedback'li deneme:
        0.0 normal -> 0.0 feedback -> 0.3 normal -> 0.3 feedback -> ...
    Feedback bulunan SOMUT hatayı prompt'un EN SONUNA ekler ve "bu hatayı
    vermeden dene" der. Merdiven tükenirse 0.0'a resetlenip _cycles tur
    tekrarlanır."""
    d, hata = _try_parse((first_content or "").strip(), bekleyen)
    if d is not None:
        return d
    for cycle in range(_cycles):
        for i in range(len(_TEMP_LADDER)):
            for feedbackli in (False, True):
                if cycle == 0 and i == 0 and not feedbackli:
                    continue            # first_content'te zaten denendi
                msgs = messages
                if feedbackli:
                    msgs = messages + [HumanMessage(
                        "Önceki cevabında şu hata vardı: " + hata +
                        "\nBu hatayı vermeden SADECE geçerli JSON döndür, "
                        "başka hiçbir şey yazma.")]
                try:
                    ai = _invoke_resilient(None, msgs, allow_tools=False,
                                           start_attempt=i)
                except Exception as exc:
                    log.warning("    JSON merdiveni istek hatası: %s", exc)
                    continue
                d, hata = _try_parse((ai.content or "").strip(), bekleyen)
                if d is not None:
                    return d
        if cycle + 1 < _cycles:
            log.info("    (sıcaklık merdiveni tükendi — 0.0'a resetlenip tekrar)")
    log.warning("    JSON hiçbir sıcaklıkta düzelmedi (%s)", hata)
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
    messages = [SystemMessage(_SYSTEM.format(tid=table_id, docstring=docstring,
                                             today=date.today().isoformat())),
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

    d = _parse_json_ladder(messages, ai.content, bekleyen=("duplicates",))
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
    # KAYNAK BİRLEŞTİRME: sources.update() aynı bankanın a'daki kaynaklarını
    # b'ninkilerle EZİYORDU — birleşen tablonun yarısının referansı kayboluyordu.
    # Artık iki tarafın kaynakları point_id bazında BİRLEŞTİRİLİR.
    sources: dict = {}
    for kaynak in (a.get("sources") or {}, b.get("sources") or {}):
        for banka, liste in kaynak.items():
            mevcut = sources.setdefault(banka, [])
            gorulen = {s.get("point_id") for s in mevcut if s.get("point_id")}
            for s in liste or []:
                pid = s.get("point_id")
                if pid and pid in gorulen:
                    continue
                mevcut.append(s)
                if pid:
                    gorulen.add(pid)
    store.overwrite_table(canon_id, merged["docstring"], merged["columns"],
                            merged["rows"], sources, merged["category"], merged["subcategory"],
                            cell_sources=merged.get("cell_sources"))
    store.delete_table(dup_id)
    # SİLİNEN TABLO İNDEKSTEN DE DÜŞMELİ: delete_table yalnız dosya+registry
    # siliyordu; Qdrant'ta kalan "hayalet" kayıt mükerrerlik aramasını
    # kirletiyordu (canlı: 58 silinmiş tablo indekste duruyordu, ajan var
    # olmayan tablolarla eşleşme arıyordu).
    try:
        drop_table_index(dup_id)
    except Exception as exc:
        log.warning("  [İNDEKS SİLME HATASI] %s: %s", dup_id, exc)
    store.remap_ledger_table(dup_id, canon_id)
    try:                              # URL havuzu birleşen tabloyu da bilsin
        store.record_url_pool(sources, canon_id)
    except Exception as exc:
        log.warning("  [URL HAVUZ HATASI] %s: %s", canon_id, exc)
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
