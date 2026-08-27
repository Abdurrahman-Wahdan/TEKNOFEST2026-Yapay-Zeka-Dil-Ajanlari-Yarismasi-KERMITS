"""Banka-scoped researcher subagent — Gemma, search_bank + read_more +
read_full_page tool'larına serbest erişimle bir konuyu kendi bankasında
araştırır. Sorguyu nasıl kuracağı, kaç kez arayacağı, hangi chunk'ın çevresini
(read_more) ya da tüm sayfayı (read_full_page) okuyacağı, ne zaman yeterli
bilgiye sahip olduğu tamamen KENDİ kararı — burada içerik kararına karışan bir
kural yok; hiçbir sabit örnekleme/pencere sınırı da yok.

Mühendislik güvenlikleri (İÇERİK kararı değil, fiziksel/operasyonel sınırlar):
  * KAYNAK BÜTÇESİ (MAX_TOOL_CALLS) — HER tool çağrısı (search/read_more/
    read_full_page fark etmez) bütçeden düşer; sonsuz döngüye karşı tek,
    araç-türünden bağımsız fiziksel tavan. Aşılırsa loglanır.
  * BAĞLAM: kırpılmaz/budanmaz — sınırı sunucu belirler
    destekliyor ama biz zorlamıyoruz. Asıl budama İÇERİK kararına dayalı: agent
    mark_useful(useful=[...], not_useful=[...]) ile HER chunk için kendisi karar
    verir; 'not_useful' dediği chunk'lar bir sonraki turda HEMEN silinir (bağlamın
    dolmasını beklemeden), 'useful' dediği kalıcı korunur. Adım sayısı arttıkça
    fikir değiştirebilir, son kararı geçerli olur. Bu bile yetmez de bağlam yine
    dolarsa (model henüz karar vermediği eski turlar için) SON ÇARE olarak fiziksel
    iki-geçişli budama devreye girer.
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
from datetime import date
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from llm import get_llm
from llm.providers.vllm_provider import reset_http_pool

from config import tunnel

from ..net_limit import NET_SEM
from corpus.search import prune_entries

from .json_mod import llm_kwargs
from ..vlm import _BACKOFF_MAX
from .retrieval import (make_bank_search_tool, make_full_page_tool,
                         make_read_more_tool, tohum_point_id)

log = logging.getLogger("dataprep.compare.bank_agent")

# YÜKSELTİLDİ 50 -> 300 (kullanıcı kararı 2026-08-22: veri kaybı olmasın).
# Bu bir VERİ sınırı değil, sonsuz döngüye karşı fiziksel emniyet: canlı
# ortalama ~5-10 çağrı, yani pratikte hiçbir ajan buna ulaşmaz. Buna
# takılan her olay _tool_limit_hits.jsonl'e yazılır, sonradan denetlenir.
# 300 -> 100 (kullanıcı kararı 2026-08-25). 300 çağrı tek bir konu/banka
# için fazlaydı: ajan aynı bilgiyi farklı sorgularla tekrar tekrar
# çekip bağlamı şişiriyordu. 100 hâlâ cömert bir tavan — sonsuz
# döngüye karşı güvenlik sınırı, içerik kararına karışmıyor.
MAX_TOOL_CALLS = int(os.environ.get("COMPARE_MAX_TOOL_CALLS", "100"))
# Aynı sorguyu next=true ile kaç kez derinleştirebilir (sayfalama) — sınırsız
# derinlik azalan getiridir, tek bir konuda tüm bütçeyi (50) tüketebilir.
NEXT_DEPTH_LIMIT = int(os.environ.get("COMPARE_NEXT_DEPTH_LIMIT", "50"))
# Server 131072 token. Hedef ~64k — erken ve GÜVENLİ compact (char/token oranı
# beklenenden kötü çıktığı ampirik olarak görüldü, o yüzden muhafazakâr karakter eşiği).
LIMIT_LOG = Path(__file__).resolve().parents[2] / "data" / "_tables" / "_tool_limit_hits.jsonl"

_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir KATILIM BANKASI araştırmacısısın. BUGÜNÜN TARİHİ: {today} — "
    "geçerlilik/süre ile ilgili değerlendirmelerini buna göre yap. "
    "ÖNEMLİ — TERMİNOLOJİ: katılım "
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
    "{topic_aciklama}"
    "search_bank aracıyla araştırmak. Sorguyu nasıl kuracağına, kaç kez arayacağına "
    "ve ne zaman yeterli bilgiye sahip olduğuna SEN karar ver — her çağrı 5 sonuç "
    "getirir, ama bunlar sayfanın küçük PARÇALARIDIR (chunk). Bir sonuç kesilmiş/"
    "yarım/yetersiz görünüyorsa İKİ seçeneğin var: read_more(point_id) ile SADECE "
    "o chunk'ın hemen öncesini/sonrasını (komşu chunk'lar, doküman sırasına göre) "
    "okuyabilirsin — yetmezse before/after'ı büyütüp tekrar çağırabilirsin, sınır "
    "yok; ya da read_full_page(url) ile sayfanın/PDF'in TAMAMINI birleştirilmiş "
    "okuyabilirsin (PDF çok uzunsa bu daha maliyetli, read_more genelde yeterli "
    "ve daha hedefli). AYNI query'yi "
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
    '"sources": [{{"point_id": "<arama sonuçlarında gördüğün point_id>", '
    '"note": "<bu kaynaktan ne aldın>"}}]}}\n'
    "attributes: bu ürün/kampanyayı EN İYİ tanımlayan alanları SEN belirle (oran, "
    "tutar aralığı, vade, ücret, koşul, geçerlilik — konuya göre değişir, sabit şema "
    "yok). offers=false ise attributes boş, sources yine de aradığın kanıtı gösterir. "
    "sources'ta URL YAZMA — sadece gördüğün point_id'yi ver, gerçek URL'i ve tarihini "
    "biz eşleştiririz (senin yeniden yazman hataya açık). UYDURMA — yalnız arama "
    "sonuçlarında GERÇEKTEN gördüğün point_id'leri kullan."
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


def _prune_discarded(messages: list, discarded: set[str]) -> list:
    """CHUNK bazlı budama — model 'gereksiz' dediği tek tek parçaları o
    ToolMessage'ın içinden çıkarır (kalanlara dokunmadan). Karar mantığı
    corpus.search.prune_entries'te: canlı banka uzmanları da AYNI budamayı
    kullanıyor, iki ayrı uygulama olmamalı."""
    if not discarded:
        return messages
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        c = m.content if isinstance(m.content, str) else str(m.content)
        pruned = prune_entries(c, discarded)
        if pruned is not None:
            m.content = pruned
    return messages


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
    """GERÇEKTEN kalıcı 4xx mi? (retry anlamsız)

    400 ve 403 BİLEREK LİSTEDE DEĞİL (vlm.py::_GECICI_4XX ile AYNI karar):
    sunucu access log'uyla kanıtlandı (2026-08-18 23:00 UTC) — tünel soketi
    bayatlayınca nginx isteği "400 / 0 byte" ile reddediyor ve istek vLLM'e
    HİÇ ULAŞMIYOR; aynı istek saniyeler sonra 200 dönüyor. 403 de tünelden
    geliyor (vLLM auth kullanmıyor). Bunları kalıcı sayıp pes etmek, sağlam
    bir araştırmayı sessizce yarıda bırakmak demekti.

    401/404/413/422 kalıcı: kimlik/adres/gövde hatası, tekrar aynı sonucu verir.
    """
    s = str(exc)
    return any(code in s for code in ("401", "404", "413", "422", "BadRequest"))


def _invoke_resilient(tools: list | None, messages: list, allow_tools: bool = True,
                       start_attempt: int = 0):
    """Parse dışı (sunucu/ağ) hata olursa sıcaklık merdiveniyle (0.0->1.0)
    İSTEĞİ ASLA PES ETMEDEN tekrar dener (60s'de sabitlenen üstel backoff) —
    kalıcı (4xx) hatada denemez, hemen fırlatır. Uzun süredir başarısızsa
    [GEMMA_UZUN_SÜRELİ_HATA] ile uyarır ama DURMAZ.

    start_attempt: çağıran (ör. _parse_json_ladder) merdivenin belirli bir
    basamağından BAŞLAMAK isterse (ağ hatası dışı, format-düzeltme amaçlı
    tekrar) kullanılır — yoksa her çağrı sessizce 0.0'dan başlardı."""
    start = time.time()
    delay = 1.0
    attempt = start_attempt
    last_warn = 0.0
    while True:
        t = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            # Araç bağlanmayacaksa düz JSON bekleniyor demektir -> vLLM'e
            # JSON zorlaması geç (bkz. json_mod.py). Araçla BİRLİKTE
            # kullanılmaz: JSON'a zorlanan model tool_calls üretemez.
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
                log.warning("    [GEMMA_UZUN_SÜRELİ_HATA] %.0fs'dir başarısız (deneme %d): %s — "
                            "denemeye DEVAM ediyor (durmuyor)", elapsed, attempt + 1, type(exc).__name__)
                last_warn = elapsed
            else:
                log.warning("    Gemma istek hatası (deneme %d, t=%.1f): %s — %.0fs sonra tekrar",
                            attempt + 1, t, type(exc).__name__, delay)
            time.sleep(delay)
            # vlm.py::_BACKOFF_MAX ile AYNI tavan (30s). 60s'de bekleyip
            # duran bir ajan, tünel kısa süreli düştüğünde gereksiz yere
            # yavaşlıyordu; 5s ise sunucuyu boğuyordu (canlı ölçüldü).
            delay = min(delay * 2, _BACKOFF_MAX)
            attempt += 1


def _try_parse(content: str, bekleyen: tuple[str, ...] = ()) -> tuple[dict | None, str]:
    """(sonuç, hata_metni) döndürür. Hata metni modele AYNEN geri verilir —
    "geçerli JSON değildi" gibi genel bir uyarı yerine SOMUT sorun (parse
    hatası ya da eksik key) bildirilirse model düzeltmeyi hedefli yapar."""
    d = None
    hata = ""
    try:
        d = json.loads(content)
    except Exception as exc:
        hata = f"JSON ayrıştırılamadı: {exc}"
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                hata = ""
            except Exception as exc2:
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
                        bekleyen: tuple[str, ...] = ()) -> dict:
    """JSON bozuksa ya da beklenen alanlar eksikse yeniden cevap ister.

    Sıra (kullanıcı kararı 2026-08-20) — her sıcaklıkta ÖNCE feedback'siz,
    SONRA somut hatayı içeren feedback'li deneme:

        0.0 normal -> 0.0 feedback -> 0.3 normal -> 0.3 feedback -> ...

    Feedback, bulduğumuz SOMUT hatayı (parse mesajı ya da eksik alan adları)
    prompt'un EN SONUNA ekler ve "bu hatayı vermeden dene" der; genel bir
    "geçersizdi" uyarısından çok daha hedefli düzeltme sağlar. Merdiven
    tükenirse 0.0'a resetlenip _cycles tur tekrarlanır."""
    d, hata = _try_parse(first_content, bekleyen)
    if d is not None:
        return d
    for cycle in range(_cycles):
        for i in range(len(_TEMP_LADDER)):
            for feedbackli in (False, True):
                if cycle == 0 and i == 0 and not feedbackli:
                    continue            # bu deneme zaten first_content'te yapıldı
                msgs = messages
                if feedbackli:
                    msgs = messages + [HumanMessage(
                        "Önceki cevabında şu hata vardı: " + hata +
                        "\nBu hatayı vermeden, az önce topladığın bilgiyle "
                        "SADECE geçerli JSON döndür, başka hiçbir şey yazma.")]
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
    log.warning("    JSON hiçbir sıcaklıkta düzelmedi (%s) — boş sonuç.", hata)
    return {}


def _yakin_point_id(pid: str, point_meta: dict) -> str | None:
    """Bozuk kopyalanmis bir point_id'yi, ajanin GERCEKTEN gordugu id'ler
    arasindan kurtarir. Sadece TEK bir aday varsa dondurur — birden fazla
    yakin id varsa yanlis kaynaga baglamaktansa dusurmek daha guvenli.
    Uydurma (tamamen ilgisiz) id'ler hicbir adaya yakin olmadigi icin
    elenmeye devam eder."""
    p = (pid or "").replace("-", "").lower()
    if len(p) < 24:                    # cok kisa -> guvenli eslesme yapilamaz
        return None
    adaylar = []
    for k in point_meta:
        k2 = k.replace("-", "").lower()
        if k2 == p or (abs(len(k2) - len(p)) <= 2 and (p in k2 or k2 in p)):
            adaylar.append(k)
            continue
        # tek karakter dusmus/eklenmis: ortak on-ek + ortak son-ek uzunlugu
        i = 0
        while i < min(len(p), len(k2)) and p[i] == k2[i]:
            i += 1
        j = 0
        while j < min(len(p), len(k2)) - i and p[-1 - j] == k2[-1 - j]:
            j += 1
        if i + j >= min(len(p), len(k2)) - 1 and abs(len(p) - len(k2)) <= 2:
            adaylar.append(k)
    return adaylar[0] if len(adaylar) == 1 else None


def _resolve_sources(raw_sources: list, point_meta: dict, bank: str, topic: str) -> list[dict]:
    """LLM'in point_id ile işaret ettiği kaynakları KOD tarafında gerçek
    url/tarih bilgisine çözer — LLM URL YAZMAZ (halüsinasyon riski), sadece
    arama sırasında GERÇEKTEN gördüğü point_id'leri raporlar. point_meta'da
    bulunamayan (uydurma/yanlış) point_id'ler SESSİZCE atlanır (loglanır)."""
    out = []
    for s in raw_sources or []:
        if not isinstance(s, dict):
            continue
        pid = str(s.get("point_id") or "").strip()
        meta = point_meta.get(pid)
        if not meta:
            duzeltilmis = _yakin_point_id(pid, point_meta)
            if duzeltilmis:
                # LLM point_id'yi kopyalarken karakter dusurmus/eklemis (gozlemlendi:
                # '133b361-...' -> UUID ilk blogu 7 hane). Kaynak GERCEK; tek harf
                # yuzunden kanit dusurulmesin — TEK bir aday varsa kod duzeltir.
                log.info("  point_id kopyalama hatasi duzeltildi: %s -> %s", pid, duzeltilmis)
                pid = duzeltilmis
                meta = point_meta[pid]
            else:
                log.warning("  UYDURMA/BULUNAMAYAN point_id atlandı: %s / %r: %s", bank, topic, pid)
                continue
        out.append({
            "point_id": pid, "url": meta.get("url", ""), "note": s.get("note", ""),
            "gecerlilik_baslangic": meta.get("gecerlilik_baslangic", ""),
            "gecerlilik_bitis": meta.get("gecerlilik_bitis", ""),
            "validity_status": meta.get("validity_status", ""),
        })
    return out


def _research_bank(topic: str, bank: str, max_calls: int,
                    seed_url: str | None = None,
                    topic_aciklama: str = "") -> dict:
    marked: set[str] = set()
    discarded: set[str] = set()
    point_meta: dict = {}
    search_tool = make_bank_search_tool(bank, marked, discarded, point_meta)
    nearby_tool = make_read_more_tool(bank, point_meta)
    full_page_tool = make_full_page_tool(bank, point_meta)
    # BİLİNÇLİ OLARAK bağımsız bir mark_useful tool'u YOK — marking'in tek işlevi
    # gelecekteki context-trim'de bir chunk'ı korumak; son turdaysa (artık
    # aramayacaksa) zaten "gelecek" kalmıyor, marking'in hiçbir anlamı olmaz.
    # Bağımsız mark_useful varken model onu tek başına, boş boş tekrar tekrar
    # çağırıp döngüye giriyordu (kanıtlı bug). Marking SADECE search_bank'a
    # gömülü — üçüncü bir "ilerlemesiz" tool-çağrı yolu fiziksel olarak yok.
    tools = [search_tool, nearby_tool, full_page_tool]
    by_name = {t.name: t for t in tools}

    # KONU AÇIKLAMASI: bu ajan tetikleyici sayfayı GÖRMÜYOR — konuyu yalnız
    # adından tanıyor. Sınıflandırma ajanının ürettiği 1-2 cümlelik tarif
    # (topic_aciklama) burada prompt'a giriyor ki 10 araştırmacı AYNI şeyi
    # arasın (kullanıcı kararı 2026-08-25).
    _acik = (f"\n\nKONUNUN TARİFİ (bu konuyu tetikleyen sayfayı sen görmedin — "
             f"aradığın şey tam olarak budur): {topic_aciklama}\n\n"
             if topic_aciklama else "")
    base_system = _SYSTEM.format(bank=bank, topic=topic, topic_aciklama=_acik,
                                 today=date.today().isoformat())
    # seed_url: bu konu ZATEN bu bankanın bir sayfasında tespit edildiği için
    # (classify_page tetikledi) — kör arama yerine önce doğrudan o sayfaya
    # yönlendir. Aramayla paraphrase uyuşmazlığı yüzünden tetikleyici bankanın
    # kendi sayfasını bulamayıp "sunmuyor" demesi (yanlış 0/10) buradan geliyordu.
    human = f"Konu: {topic}"
    if seed_url:
        # URL'i MODELE VERMEYIZ — kod, seed sayfasinin point_id'sini cozup onu
        # verir. Model yalnizca index takip eder (read_full_page artik point_id
        # aliyor); URL gormedigi icin uydurma/yanlis URL yazma yolu fiziksel
        # olarak kapali. point_id cozulemezse yonlendirmeyi hic vermeyiz —
        # model normal aramayla devam eder, yanlis bir id ile ugrasmaz.
        seed_pid = tohum_point_id(bank, seed_url)
        if seed_pid:
            human += (f"\n\nBu konu ZATEN bu bankanın bir sayfasında tespit edildi: "
                       f"point_id={seed_pid} — ÖNCE read_full_page ile o sayfayı oku, "
                       "oradaki bilgi yeterliyse başka arama yapmana gerek yok.")
    messages = [SystemMessage(base_system), HumanMessage(human)]
    tried: list[str] = []          # denenen sorgular — context-trim'den ETKİLENMEZ
    repeat_counts: dict[tuple, int] = {}  # (araç,argüman) imzası -> harfiyen kaç kez tekrarlandı
    next_depth: dict[str, int] = {}      # aynı sorgu next=true ile kaç kez derinleştirildi
    calls = 0
    hit_limit = False
    stuck = False                  # aynı sorgu ısrarla tekrarlanıyor — model TIKANMIŞ
    while True:
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
            ai2 = _invoke_resilient(None, messages, allow_tools=False)
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
                # search_bank DIŞINDAKİ araçlar da (ör. read_full_page, read_more)
                # AYNI hastalığa düşebilir — kanıtlı: model iki URL arasında ping-pong
                # yaparak 10+ kez aynı sayfayı tekrar tekrar okudu, hiçbir korumaya
                # takılmadan. Genel (araç, argüman) imzasıyla TÜM araçlar için aynı
                # fiziksel tavan: harfiyen aynı çağrı 3. kez -> TIKANMIŞ.
                sig = (name, tuple(sorted((k, str(v)) for k, v in args.items())))
                n = repeat_counts.get(sig, 0) + 1
                repeat_counts[sig] = n
                if n >= 3:
                    stuck = True
                target = args.get("url") or args.get("point_id") or ""
                log.info("      %s #%d [%s]: %s%s", bank, calls, name, target[:70],
                          f" [{n}. kez, TIKANMA]" if n >= 2 else "")
            try:
                out = tool.invoke(tc["args"])
            except Exception as exc:
                out = f"HATA: {exc}"
            messages.append(ToolMessage(str(out), tool_call_id=tc["id"]))

    if hit_limit:
        log.warning("  TOOL LİMİTİ: %s / %r (%d çağrı)", bank, topic, calls)
        _log_limit_hit(bank, topic, calls)

    d = _parse_json_ladder(messages, (ai.content or "").strip(),
                            bekleyen=("offers", "sources"))
    return {
        "bank": bank,
        "offers": bool(d.get("offers")),
        "attributes": d.get("attributes") or {},
        "sources": _resolve_sources(d.get("sources") or [], point_meta, bank, topic),
        "tool_calls": calls,
        "hit_limit": hit_limit,
    }


def research_bank(topic: str, bank: str, max_calls: int = MAX_TOOL_CALLS,
                   seed_url: str | None = None, topic_aciklama: str = "") -> dict:
    """Bir konuyu bir bankada araştırır. ASLA istisna fırlatmaz — bir bankanın
    araştırması patlarsa (context, ağ, parse...) hata raporlanır, pipeline sürer.

    seed_url verilirse (bu konuyu tetikleyen sayfa biliniyorsa) ajan önce o
    sayfayı okumaya yönlendirilir — kör aramaya güvenmez."""
    try:
        return _research_bank(topic, bank, max_calls, seed_url, topic_aciklama)
    except Exception as exc:
        log.error("  ARAŞTIRMA HATASI: %s / %r: %s: %s", bank, topic, type(exc).__name__, exc)
        return {"bank": bank, "offers": False, "attributes": {}, "sources": [],
                "tool_calls": 0, "hit_limit": False, "error": str(exc)}


def _log_limit_hit(bank: str, topic: str, calls: int) -> None:
    LIMIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LIMIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"bank": bank, "topic": topic, "calls": calls},
                            ensure_ascii=False) + "\n")
