"""Ana traversal: banka-banka, sayfa-sayfa gezer; kıyaslanabilir + yeni bir konu
bulursa 10 bankaya paralel researcher subagent gönderip ARAŞTIRIR; mevcut
tabloyla eşleşirse yalnız eksik bankanın subagent'ını çalıştırır.

Bir sayfa/PDF BİRDEN FAZLA farklı konu döndürebilir (uzun bir dokümanda birden
çok ürün/kampanya anlatılıyor olabilir — bkz. classify_agent.classify_page'in
paralel tarama + sıralı sınıflandırma tasarımı). process_page bu durumda her
konuyu SIRAYLA (_process_topic ile) ayrı ayrı işler; hiçbiri diğerini etkilemez.

Araştırma BİTTİKTEN SONRA iki ayrı adım SIRAYLA çalışır (görev bilerek
parçalanmış — her adım tek bir sorumluluk taşır, hatası bir sonrakini
kirletmez), ama sınıflandırma/isimlendirme artık İÇERİK İNŞASINDAN SONRA:

  1) synth.build_all (pipeline._build_sequentially) — TÜM bankaların
     satır (boş {}), sütunların ise henüz açılmamış olduğu bir İSKELET
     kurulur; sonra her banka SIRAYLA (paralel değil, her biri temiz/
     paylaşılmayan bir LLM çağrısı) bu tabloyu görüp kendi satırını doldurur/
     gerekirse sütun açar — tablo gittikçe büyür. Bir bankanın raporu
     işlenemezse (LLM ulaşılamadı) o banka SESSİZCE KAYBOLMAZ, boş satır
     olarak kalır (sonra tekrar denenebilir) — önceki bankaların verisi
     hiçbir durumda silinmez. Bu, eski "10 bankayı TEK çağrıda sentezle"
     tasarımının tam tersi: orada tek bir kötü/karışık cevap 10 bankanın da
     hatalı görünmesine (ör. hepsi "sunulmuyor") yol açabiliyordu. Bu adımda
     kategori/alt-kategori/isim kararı YOK — build_row SADECE içerik üretir.

  2) classify_agent.finalize_table — tablo TAMAMEN kurulduktan SONRA, TEK
     seferde çalışan SON karar: sütunları gerekirse sıkılaştırır (compact —
     her bankanın kendi özgün detayı için açtığı seyrek sütunları anlamca
     birleştirir, veri kaybetmeden), search_tables ile mevcut havuzda GERÇEK
     bir mükerrer var mı SON kez kontrol eder (varsa merge_tables ile
     birleştirir — AYRI tablo açmaz), kategori + alt-kategori (synth.
     closest_subcategories ile) + tablo ADI + docstring'i kararlaştırır. Bu,
     classify_page'in erken/tek-sayfalık tahmininin AKSİNE tüm bankaların
     GERÇEK verisini gördüğü için otoriter kabul edilir.

classify_page'in kendi erken (ucuz) fits_table tahmini SADECE VERİMLİLİK için
kalıyor — bir sayfa mevcut bir tabloyla eşleşiyor GİBİ görünüyorsa tam
10-banka fan-out yerine sadece eksik bankayı araştırır (_process_topic).
Bu erken tahmin YANLIŞ çıkarsa (fits_table doğrulaması reddederse) tam
fan-out'a düşülür — ama nihai kategori/isim/mükerrerlik kararı HER ZAMAN
finalize_table'a ait.

Bir bankanın sayfaları PAGE_WORKERS kadar (varsayılan 5) PARALEL işlenir
(process_bank) — bir sayfanın tam işlenmesi (yeni konu bulursa 10-banka
fan-out + sıralı inşa + finalize) ağırlıklı olarak AĞ BEKLEMESİ, CPU değil;
bu yüzden thread'lerle paralelleştirmek gerçek bir hızlanma sağlıyor. Sunucu
tarafında bir eşzamanlılık tavanı YOK (kaldırıldı) — RİSK (bilerek kabul
edilmiş): iki sayfa AYNI ANDA
aynı konuyu bulup İKİSİ DE "mevcut tablo yok" görüp ayrı ayrı tablo açabilir
(TOCTOU yarışı — classify_page + finalize_table'ın arama kontrolleri
birbirini ÖNCEDEN göremez). Bu görmezden gelinmiyor: dedup.py bakım ajanı
TAM OLARAK bu senaryo için var, havuzu periyodik tarayıp gerçek mükerrerleri
birleştiriyor — hız kazancı, "nadir kaçan mükerrer zaten sonra temizlenir"
ödünleşmesiyle kabul edildi.

Kullanım:
  python -m dataprep.compare.pipeline --banks kuveytturk --limit 30   # pilot
  python -m dataprep.compare.pipeline                                 # tam koşu
"""
from __future__ import annotations

import argparse
import glob
import logging
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from corpus import dates as _dates
from dataprep.pages import _split_front, _url_of

from . import store, synth
from .bank_agent import research_bank
from .classify_agent import classify_page, finalize_table
from .retrieval import index_table

log = logging.getLogger("dataprep.compare.pipeline")

ROOT = Path(__file__).resolve().parents[2] / "data"
# Toplam işçi hattı: bankalar ARASI paralellik sınırsız (10 banka = 10 hat).
# Sınır SAYFA düzeyinde değil BANKA düzeyindedir (aşağıdaki _BANKA_KILIT):
# aynı bankanın birden çok sayfası AYNI ANDA işlenirse ikisi de aynı konuyu
# tetikleyip mükerrer tablo doğuruyor. Bu yüzden banka başına aynı anda TEK
# sayfa; farklı bankalar tam paralel gider (NET_SEM=60 üst sınırı korur).
# KULLANICI KARARI (2026-08-22): SAYFA DÜZEYİNDE TAM SERİ — aynı anda TEK
# sayfa işlenir (global, banka ayrımı olmadan). Gerekçe: mükerrerlik hiçbir
# koşulda kaçmasın. Banka başına kilit + konu kilidi, FARKLI bankalardaki iki
# sayfanın aynı konuyu aynı anda tetiklemesini ancak konu ADI aynıysa
# yakalayabiliyordu; ajan aynı ürünü iki sayfada FARKLI adlandırırsa
# (ör. "konut finansmanı" / "ev finansmanı") kilit tutmaz ve iki ayrı tablo
# doğar — finalize_table + dedup bunu sonradan temizler ama kullanıcı
# "sonradan temizlensin" ödünleşmesini İSTEMEDİ.
#
# PARALELLİK KAYBI YOK denecek kadar azdır: asıl iş sayfa başına yapılan
# 10-BANKA FAN-OUT'udur (_fan_out_all) ve o AYNEN paralel kalır — yani her an
# ~10 eşzamanlı araştırma isteği sürüyor. Seri olan sadece "kaç sayfa aynı
# anda incelenir" boyutudur.
PAGE_WORKERS = int(os.environ.get("COMPARE_PAGE_WORKERS", "1"))


def all_banks() -> list[str]:
    return sorted(os.path.basename(d)[:-5] for d in glob.glob(str(ROOT / "*_site")))


def _fresh_enough(front: str) -> bool:
    """Süresi geçmişse False (atla); tarihsiz ya da hâlâ geçerliyse True.

    Tarih varsa deterministik hesaplanır. Tarih yoksa date_pass'in metinden
    çıkardığı campaign_status'a bakılır (bitti -> atla)."""
    end = status = ""
    for line in front.splitlines():
        s = line.strip()
        if s.startswith("campaign_end:"):
            end = s.split(":", 1)[1].strip().strip('"')
        elif s.startswith("campaign_status:"):
            status = s.split(":", 1)[1].strip().strip('"')
    if end:
        return _dates.is_active(end)
    return status != "bitti"


def _tercihli_dosyalar(site: Path) -> list[Path]:
    """Bir sayfanın EN ZENGİN sürümünü seç — aynı içerik birden çok dosyada.

    Bir URL diskte 2-3 dosyayla temsil ediliyor:
      content/<yol>.md     -> AŞAMA 3 çıktısı: metin + GÖRSELLERDEN çıkarılan
                              bilgi + geçerlilik tarihleri (EN ZENGİN)
      <yol>.md (kök/alt)   -> AŞAMA 1 çıktısı: sadece temizlenmiş HTML metni
      _pdf_clean/<yol>.md  -> AŞAMA 2 çıktısı: PDF metni (content'te yoksa gerekli)
      _raw/<yol>.md        -> ham HTML, frontmatter YOK (url alanı yok, zaten
                              işlenemez; karşılığı content'te TAM olarak var)

    Eskiden rglob ham listeyi veriyordu ve rglob sırası hangi sürümün önce
    geleceğini belirliyordu. Canlı ölçüm (2026-08-19, kuveytturk): 1662 URL'in
    845'inde ÖNCE crawl çıktısı işleniyor, content/ sürümü "zaten işlendi" diye
    atlanıyordu — yani GÖRSELLERDEN gelen bilgi ve tarihler tabloya HİÇ girmiyordu.

    Artık content/ öncelikli; content'te olmayanlar (yalnız PDF'ler ya da
    aşama 3'ün henüz işlemediği sayfalar) diğer sürümlerden alınır. Böylece
    HİÇBİR benzersiz içerik kaçmaz, ama her URL EN İYİ sürümüyle işlenir."""
    tercih: dict[str, Path] = {}
    sirasiz: list[Path] = []
    for p in site.rglob("*.md"):
        if "image_text" in p.parts or "_raw" in p.parts:
            continue                          # _raw: frontmatter yok, karşılığı content'te
        parcalar = p.parts
        if "content" in parcalar:
            anahtar = str(p.relative_to(site / "content"))
            tercih[anahtar] = p               # content HER ZAMAN kazanır
        elif "_pdf_clean" in parcalar:
            anahtar = str(p.relative_to(site / "_pdf_clean"))
            tercih.setdefault(anahtar, p)
        else:
            anahtar = str(p.relative_to(site))
            tercih.setdefault(anahtar, p)
    return sorted(tercih.values())


def _gerekli_mi(p: Path) -> bool:
    """Bu dosya aşama 4'te 'gereksiz' damgalanmış mı? (frontmatter'ın ilk
    satırlarına bakar — tüm dosyayı okumaz.)

    ŞÜPHEDE İŞLE: etiket okunamazsa/yoksa True döner. 'gereksiz' damgası
    AÇIKÇA görülmedikçe hiçbir sayfa elenmez — veri kaybı riski yok."""
    try:
        with p.open(encoding="utf-8", errors="replace") as f:
            bas = f.read(1200)
    except Exception:
        return True
    return ("content_relevance: gereksiz" not in bas
            and 'content_relevance: "gereksiz"' not in bas)


def _pages(bank: str, limit: int | None):
    """(i, total, url, body, skip_reason) verir — i/total dosya SIRASINA göre
    (atlanan dosyalar da SAYILIR, "kaçıncı dosyadayız" göstergesi kesintisiz
    ilerlesin). skip_reason boşsa gerçekten işlenecek bir sayfadır; doluysa
    (kısa/eski) o dosya atlanmıştır, url/body boştur."""
    site = ROOT / f"{bank}_site"
    if not site.exists():
        return
    mds = _tercihli_dosyalar(site)
    # SADECE 'gerekli' ETİKETLİLER (kullanıcı kararı 2026-08-25). Aşama 4 her
    # sayfaya content_relevance damgası basıyor; 10523 dosyanın 4586'sı
    # 'gereksiz'. Bunlar zaten aşağıda atlanıyordu ama SIRASI GELDİĞİNDE:
    # dosya açılıp frontmatter'ı okunuyor, sayaç şişiyor ve gerçek ilerleme
    # görünmüyordu. Artık listeye HİÇ alınmıyorlar.
    # ETİKETSİZ dosya ELENMEZ (133 tane) — etiket yokluğu 'gereksiz' demek
    # değildir, veri kaybetmemek için işlenir.
    mds = [q for q in mds if _gerekli_mi(q)]
    if limit:
        mds = mds[:limit]
    total = len(mds)
    for i, p in enumerate(mds, 1):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            yield i, total, "", "", "okunamadı"
            continue
        front, body = _split_front(text)
        if "content_relevance: gereksiz" in front or 'content_relevance: "gereksiz"' in front:
            yield i, total, "", "", "gereksiz içerik"
            continue
        if len(body.strip()) < 40:
            yield i, total, "", "", "içerik yok"
            continue
        if not _fresh_enough(front):
            yield i, total, "", "", "süresi geçmiş"
            continue
        yield i, total, _url_of(front) or str(p), body, ""


# --- konu bazlı kilit (AYNI İŞİ İKİ KEZ YAPMAMAK) -----------------------------
# PAGE_WORKERS sayfa PARALEL işlenir. İki farklı sayfa AYNI konuyu tetiklerse,
# ikisi de "bu konuda tablo var mı?" kontrolünden aynı anda geçer (o an henüz
# yok), ikisi de fan-out yapar ve ikisi de yazar — klasik check-then-act yarışı.
# Kanıtlandı (canlı koşu): 'kadınlara özel kritik hastalıklar sigortası' 8 saniye
# arayla İKİ kez oluştu (…-sigortası ve …-sigortası-2), aynı kaynak URL'den.
#
# Çözüm: bir konunun İNCELEMESİ (fan-out + yazma) o konuya ait tek bir kilit
# altında yürür — 10 bankalık pahalı bir araştırma aynı anda İKİ kez koşmaz.
# Farklı konular birbirini HİÇ bloklamaz, paralellik korunur.
#
# NOT: bu bir MÜKERRERLİK TEMİZLİĞİ DEĞİL, hız/israf önlemidir. Mükerrer
# tabloların tespiti ve birleştirilmesi SONDA, agentic olarak yapılır
# (kullanıcı kararı) — burada isim/string karşılaştırmasıyla karar VERİLMEZ.
_KONU_KILIT_LOCK = threading.Lock()
_KONU_KILITLERI: dict[str, threading.Lock] = {}


def _konu_kilidi(topic: str) -> threading.Lock:
    anahtar = " ".join((topic or "").lower().split())
    with _KONU_KILIT_LOCK:
        kilit = _KONU_KILITLERI.get(anahtar)
        if kilit is None:
            kilit = _KONU_KILITLERI[anahtar] = threading.Lock()
        return kilit


def _fan_out_all(topic: str, banks: list[str], seed_bank: str | None = None,
                  seed_url: str | None = None,
                  topic_aciklama: str = "") -> list[dict]:
    """10 bankaya PARALEL researcher subagent — yalnız gerçekten yeni konuda.

    seed_bank/seed_url: bu konuyu tetikleyen (banka, sayfa) biliniyorsa, SADECE
    o bankanın araştırması bu sayfaya yönlendirilir (kör aramaya bırakılmaz) —
    yoksa tetikleyici bankanın kendi sayfasını arama-paraphrase uyuşmazlığıyla
    kaçırıp yanlışlıkla "sunmuyor" demesi mümkün (gözlemlenen 0/10 sebebi)."""
    reports = []
    with ThreadPoolExecutor(max_workers=len(banks)) as ex:
        futures = {
            ex.submit(research_bank, topic, b,
                      seed_url=seed_url if b == seed_bank else None,
                      topic_aciklama=topic_aciklama): b
            for b in banks
        }
        for i, fut in enumerate(as_completed(futures), 1):
            b = futures[fut]
            rep = fut.result()
            reports.append(rep)
            flag = " [LİMİT]" if rep.get("hit_limit") else (" [HATA]" if rep.get("error") else "")
            log.info("    [%d/%d bitti] %s -> sunuyor=%s (%d çağrı)%s",
                      i, len(banks), b, rep["offers"], rep["tool_calls"], flag)
    return reports


def _fan_out_one(topic: str, bank: str) -> dict:
    return research_bank(topic, bank)


def _indeksle(table_id: str, topic: str, category: str, subcategory: str,
               docstring: str) -> None:
    """Tabloyu arama havuzuna yazar; BAŞARISIZSA kuyruğa alır.

    Eskiden hata yalnızca loglanıp unutuluyordu — tablo diskte kalıyor ama
    aramada GÖRÜNMÜYORDU, bu da mükerrerlik kontrolünü körleştirip aynı konuda
    ikinci bir tablo açılmasına yol açıyordu ('kasko sigortası' 4 kez). Artık
    başarısızlık kalıcı olarak kaydedilir ve _kuyrugu_bosalt() bir sonraki
    fırsatta otomatik tamamlar; dışarıdan nöbetçi script GEREKMEZ."""
    try:
        index_table(table_id, topic, category, subcategory, docstring)
        store.dequeue_index(table_id)
    except Exception as exc:
        store.queue_for_index(table_id)
        log.warning("  [İNDEKS HATASI] %s: %s: %s — kuyruğa alındı, sonra "
                    "otomatik denenecek", table_id, type(exc).__name__, exc)


def _kuyrugu_bosalt() -> None:
    """Bekleyen indekslemeleri dener (her tablo yazımından sonra çağrılır).
    Başarısız olanlar kuyrukta kalır, bir sonraki turda tekrar denenir."""
    for tid in store.load_index_queue():
        t = store.load_table(tid)
        if t is None:
            store.dequeue_index(tid)          # tablo silinmiş -> kuyruktan düş
            continue
        try:
            index_table(tid, t.get("topic", tid), t.get("category", ""),
                        t.get("subcategory", ""), t.get("docstring", ""))
            store.dequeue_index(tid)
            log.info("  [İNDEKS TAMAMLANDI] %s (kuyruktan)", tid)
        except Exception:
            break                             # servis hâlâ sorunlu -> sonra


def _record_sources(table_id: str, reports: list[dict]) -> None:
    """Kanıt olarak kullanılan sayfaları ledger'a KAYNAK olarak işaretle — bilgi
    amaçlı, ana traversal'ın kendi sırasında bu sayfaları ATLAMASINA yol AÇMAZ
    (own_verdict set edilmez, sadece record_citation)."""
    for r in reports:
        for src in r.get("sources", []):
            u = src.get("url")
            if u:
                store.record_citation(u, table_id)
    # BANKA BAZLI BENZERSİZ URL HAVUZU — sondaki toplu/agentic URL+tarih
    # eşleştirmesinin girdisi. Üç yazma yolu da (yeni tablo, satır ekleme,
    # mükerrer birleştirme) buradan geçtiği için hiçbir kaynak atlanmaz.
    try:
        store.record_url_pool({r["bank"]: r.get("sources") or []
                                for r in reports if r.get("bank")}, table_id)
    except Exception as exc:                  # havuz yazımı tabloyu ASLA düşürmez
        log.warning("  [URL HAVUZ HATASI] %s: %s: %s",
                    table_id, type(exc).__name__, exc)


def _order_for_build(reports: list[dict]) -> list[dict]:
    """Sıralı inşanın işleme sırası: offers=true olan raporlar ÖNCE — ilk
    (tohum) tablo gerçek veriden kurulsun, "sunulmuyor" diyen bir bankanın
    raporuyla başlayıp boş bir iskelet kurulmasın. Eşitlikte banka adına göre
    (deterministik, koşudan koşuya aynı sıra — hata ayıklamayı kolaylaştırır)."""
    return sorted(reports, key=lambda r: (not r.get("offers"), r.get("bank", "")))


def _build_sequentially(table: dict | None, topic: str, reports: list[dict]) -> dict | None:
    """TÜM bankaların raporunu synth.build_all ile TEK çağrıda tabloya işler.

    (İsim tarihsel: eskiden banka banka sıralı işliyordu.) Kategori/alt-kategori/isim kararı
    BURADA YOK — sadece içerik (sütun/satır/docstring); tablo tamamen
    kurulduktan SONRA finalize_table (classify_agent.py) TEK seferde
    kararlaştırır.

    Başlarken, işlenecek TÜM bankalar önce BOŞ birer satır ({}) olarak
    iskelete eklenir — "sadece bankaların satır, sütunların boş olduğu bir
    tablo"dan başlanır, ilk banka tabloyu SIFIRDAN icat etmez. Bunun asıl
    faydası: bir bankanın raporu işlenemezse (LLM ulaşılamadı) o banka
    SESSİZCE KAYBOLMAZ, tabloda boş bir satır olarak görünür kalır — sonraki
    bir koşuda ayrıca tekrar denenebilir. build_row bu boş satırları
    "henüz işlenmedi" diye ayırt eder (build_row'daki prompt notu), 'sunulmuyor'
    ile karıştırmaz — {} (boş) ile {"sütun": "sunulmuyor"} (işlenmiş, sunmuyor)
    JSON'da doğal olarak ayrışıyor.

    Girdi `table`'a DOKUNULMAZ (kopyalanır) — çağıranın elindeki nesne (ör.
    mevcut bir tablo) yan etkiyle bozulmaz. table=None ise tamamen boş bir
    iskeletten başlar; bu durumda hiçbir banka işlenemezse None döner (gerçekten
    boş, retry sonra); table verilmişse (mevcut tabloya ekleme) en kötü
    ihtimalle GİRDİYLE AYNI (artı boş iskelet satırları) döner, asla None
    olmaz — mevcut veri her koşulda korunur."""
    # TEK ÇAĞRIDA SENTEZ (kullanıcı kararı 2026-08-19): 10 bankanın raporu tek
    # LLM çağrısında işlenir. Eski sıralı yol (build_row × 10) tablo başına
    # ~3.9 dk sürüyordu ve aynı tabloyu 10 kez taşıdığı için ~4.4 kat fazla
    # bağlam üretiyordu. Ham chunk'lar prompt'a KONMAZ — ajanların özet
    # raporları kullanılır, arama zekası fan-out katmanında kalır.
    _t0 = time.time()
    yeni = synth.build_all(table, topic, reports)
    if yeni is None:
        log.warning("  [SENTEZ HATASI] %r: LLM ulaşılamadı — tablo kurulamadı "
                    "(own_verdict kaydedilmez, sonraki koşuda tekrar denenir)", topic)
        return table if table is not None else None
    log.info("      [sentez] %r -> %d banka, %d sütun (%.0fs)",
             topic, len(yeni.get("rows", {})), len(yeni.get("columns", [])),
             time.time() - _t0)
    return yeni


def process_page(bank: str, url: str, body: str, banks: list[str]) -> None:
    prior = store.page_verdict(url)              # SADECE ana-agent'ın KENDİ kararı
    if prior is not None:
        return                                    # zaten anchor olarak işlendi -> atla

    # Kıyaslanabilir mi + (öyleyse) mevcut bir tabloya mı uyuyor yoksa yeni konu
    # mu — ERKEN/UCUZ bir tahmin, ajan kendi search_tables aracıyla (embedding,
    # Qdrant) tablo havuzunda arar; TÜM havuz tek prompt'a sığdırılmıyor, model
    # kendi karar verene kadar özgürce arar (bank_agent'taki search_bank ile
    # aynı desen). Uzun bir sayfa/PDF BİRDEN FAZLA farklı konu döndürebilir
    # (classify_page — bkz. classify_agent.py); her biri SADECE o parçanın
    # metnine dayanır — yeni-konu yolunda tablo TAMAMEN kurulduktan sonra
    # finalize_table tarafından bağımsızca doğrulanır/düzeltilir (aşağıda).
    decisions = classify_page(body, url)
    if decisions is None:
        return                                     # LLM ulaşılamadı -> retry (kaydetme)
    comparable = [d for d in decisions if d["comparable"]]
    if not comparable:
        store.record_verdict(url, False, "", None)
        return
    for d in comparable:
        _process_topic(bank, url, d["topic"], d["fits_table"], banks,
                       d.get("topic_aciklama", ""))


def _process_topic(bank: str, url: str, topic: str, match_id: str,
                    banks: list[str], topic_aciklama: str = "") -> None:
    """Tek bir (sayfa, konu) çifti — process_page bunu çok-konulu bir sayfada
    HER konu için ayrı ayrı çağırır.

    AYNI KONU için tüm inceleme (kontrol + fan-out + yazma) tek bir kilit
    altında yürür: iki paralel sayfa aynı konuyu tetiklediğinde ikisinin de
    "tablo yok" görüp ikişer tablo yazması böyle engellenir (bkz. _konu_kilidi).
    Bekleyen ikinci sayfa, sırası geldiğinde tabloyu ARTIK VAR bulur ve normal
    'mevcut tabloya satır ekle' yoluna girer — veri kaybolmaz, sadece mükerrer
    tablo oluşmaz. Farklı konular birbirini bloklamaz."""
    with _konu_kilidi(topic):
        _process_topic_kilitli(bank, url, topic, match_id, banks,
                               topic_aciklama)


def _process_topic_kilitli(bank: str, url: str, topic: str, match_id: str,
                            banks: list[str], topic_aciklama: str = "") -> None:
    if match_id:
        table = store.load_table(match_id)
        if table is None:
            # registry'de var ama dosyası yok (elle silme / kesintili yazma) —
            # ÇÖKMEK yerine bunu "eşleşme yok" say, aşağıdaki YENİ KONU yoluna
            # (tam 10-banka fan-out) düş. Bloktan çıkmak için if/else, match_id
            # bilerek TEMİZLENMİYOR — aşağıdaki 'if match_id:' zaten atlanır.
            log.warning("  [TUTARSIZ KAYIT] %s registry'de var ama dosyası yok — "
                        "yeni tabloya düşülüyor", match_id)
        else:
            already = table["rows"].get(bank)
            if already:
                store.record_verdict(url, True, topic, match_id)
                return
            # GÜVENLİK AĞI: yalnız EKSİK bankanın subagent'ı — 10'u değil.
            report = _fan_out_one(topic, bank)
            # eşleşme YANLIŞ olabilir (erken tahmin yanılmış olabilir) — veri
            # gelmeden önce doğrula; uymuyorsa bu tabloya zorla eklemek yerine
            # YENİ tabloya git.
            fits = (not report["offers"]) or synth.fits_table(table["docstring"], bank, report)
            if fits:
                new_table = synth.build_row(table, topic, bank, report)
                if new_table is None:
                    log.warning("  [SENTEZ HATASI] %s / %s: LLM ulaşılamadı, "
                                "satır eklenemedi (retry sonra)", bank, match_id)
                    return                        # own_verdict KAYDEDİLMEZ -> retry
                # kategori/alt-kategori bu tablonun KENDİ finalize_table
                # kararından geliyor (ilk kurulduğunda verildi) — build_row
                # içerik dışında bir şeye karışmadığı için burada AYNEN korunur.
                sources = dict(table.get("sources", {}))
                sources[bank] = report["sources"]
                store.overwrite_table(match_id, new_table["docstring"], new_table["columns"],
                                        new_table["rows"], sources, table["category"],
                                        table["subcategory"],
                                        cell_sources=new_table.get("cell_sources"))
                _indeksle(match_id, topic, table["category"],
                          table["subcategory"], new_table["docstring"])
                _kuyrugu_bosalt()
                store.record_verdict(url, True, topic, match_id)
                _record_sources(match_id, [report])
                log.info("  [satır eklendi] %s -> %s (%s)", bank, match_id, report["offers"])
                return
            log.info("  [YANLIŞ EŞLEŞME] %s / %r bu tabloya uymuyor -> yeni tablo için tam fan-out", bank, topic)
            reports = _fan_out_all(topic, banks, seed_bank=bank, seed_url=url,
                                   topic_aciklama=topic_aciklama)
            _finish_new_table(url, topic, reports, banks, bank)
            return

    # gerçekten YENİ konu (olabilir) -> 10 bankaya paralel fan-out (ASIL mekanizma)
    log.info("  [YENİ KONU] %r — 10 bankaya fan-out", topic)
    reports = _fan_out_all(topic, banks, seed_bank=bank, seed_url=url,
                           topic_aciklama=topic_aciklama)
    _finish_new_table(url, topic, reports, banks, bank)


def _finish_new_table(url: str, topic: str, reports: list[dict], banks: list[str],
                       origin_bank: str) -> None:
    """Araştırma BİTTİKTEN SONRA iki ayrı adım: ÖNCE build_row banka-banka
    SIRAYLA içeriği kurar (kategori/isim kararı YOK), SONRA finalize_table
    tablonun TAM haline bakıp TEK seferde sıkılaştırma + mükerrerlik kontrolü +
    kategori/alt-kategori/isim/docstring kararını verir. Görev bilerek bu
    sırayla ayrılmış — finalize_table artık classify_page'in erken/tek-
    sayfalık tahmininden çok daha güvenilir bir karar verebiliyor çünkü
    ELİNDE TÜM BANKALARIN GERÇEK, İŞLENMİŞ VERİSİ var."""
    table_data = _build_sequentially(None, topic, reports)
    if table_data is None:
        return                                      # hiçbir banka işlenemedi -> retry sonra

    subcats = store.load_subcategories()
    final = finalize_table(topic, table_data, subcats)
    if final is None:
        # finalize başarısız olsa da İNŞA EDİLEN tabloyu KAYBETME — taslak
        # kategoriyle kaydet (sonraki bir bakım geçişinde ya da manuel
        # düzeltilebilir). Süreç asla topladığı gerçek veriyi bir
        # sınıflandırma hatası yüzünden çöpe atmaz.
        log.warning("  [FINALIZE HATASI] %r: taslak kategoriyle kaydediliyor", topic)
        final = {"duplicate_of": "", "topic": topic, "category": "ürün",
                  "subcategory": "diğer", "docstring": table_data.get("docstring", ""),
                  "columns": table_data["columns"], "rows": table_data["rows"]}

    dup_id = final["duplicate_of"]
    existing = store.load_table(dup_id) if dup_id else None
    if existing is not None:
        # finalize_table'ın SON kontrolü gerçek bir mükerrerlik buldu — ayrı
        # tablo AÇMAK yerine mevcut tabloyla birleştir (synth.merge_tables,
        # dedup.py bakım ajanıyla AYNI mekanizma).
        fresh = {"id": "(yeni, henüz kaydedilmedi)", "topic": final["topic"],
                  "docstring": final["docstring"], "columns": final["columns"],
                  "rows": final["rows"], "cell_sources": table_data.get("cell_sources", {})}
        merged = synth.merge_tables(existing, fresh, subcats)
        if merged is None:
            log.warning("  [BİRLEŞTİRME HATASI] %s + %r: LLM ulaşılamadı, "
                        "ayrı tablo olarak kaydediliyor", dup_id, topic)
        else:
            sources = dict(existing.get("sources", {}))
            sources.update({r["bank"]: r["sources"] for r in reports})
            store.overwrite_table(dup_id, merged["docstring"], merged["columns"],
                                    merged["rows"], sources, merged["category"], merged["subcategory"],
                                    cell_sources=merged.get("cell_sources"))
            _indeksle(dup_id, final["topic"], merged["category"],
                      merged["subcategory"], merged["docstring"])
            _kuyrugu_bosalt()
            store.record_verdict(url, True, final["topic"], dup_id)
            _record_sources(dup_id, reports)
            log.info("  [FINALIZE MÜKERRER] %r -> %s ile birleştirildi", topic, dup_id)
            return

    sources = {r["bank"]: r["sources"] for r in reports}
    table_id = store.create_table(final["topic"], final["docstring"], final["columns"],
                                   final["rows"], sources, final["category"], final["subcategory"],
                                   created_from={"bank": origin_bank, "url": url},
                                   cell_sources=table_data.get("cell_sources"))
    # arama indeksine yazma başarısız olsa bile tablo geçerli; başarısızlık
    # kuyruğa alınır ve bir sonraki tablo yazımında otomatik tamamlanır.
    _indeksle(table_id, final["topic"], final["category"], final["subcategory"],
              final["docstring"])
    _kuyrugu_bosalt()
    store.record_verdict(url, True, final["topic"], table_id)
    _record_sources(table_id, reports)
    n_offer = sum(1 for r in reports if r["offers"])
    n_included = sum(1 for v in final["rows"].values() if v)   # gerçekten işlenmiş (boş iskelet hariç)
    log.info("  [TABLO] %s (%s/%s): %d/%d banka sunuyor, %d/%d banka işlendi", table_id,
              final["category"], final["subcategory"], n_offer, len(banks), n_included, len(reports))


def _total_pages(bank: str, limit: int | None) -> int:
    """`_pages`'in SAYDIĞI aynı dosya listesi (içerik okunmadan, ucuz) —
    ilerleme yüzdesi için toplam."""
    site = ROOT / f"{bank}_site"
    if not site.exists():
        return 0
    # _pages ile AYNI filtre — ayrışırsa ilerleme yüzdesi yalan söyler.
    mds = [q for q in _tercihli_dosyalar(site) if _gerekli_mi(q)]
    return min(len(mds), limit) if limit else len(mds)


def _all_pages(todo: list[str], limit: int | None):
    """TÜM bankaların sayfalarını (banka sırasına göre) TEK bir akışta verir —
    (bank, (i, total, url, body, skip_reason)) çiftleri. Neden tek akış: her
    banka kendi işçi havuzuna sahip olursa, bir bankanın son birkaç AĞIR
    sayfası (uzun süren yeni-konu araştırması) biterken diğer işçiler BOŞTA
    kalıyordu — sıradaki bankanın sayfaları henüz kuyruğa girmemiş oluyordu
    (canlı gözlem: 7 işçiden sadece 4'ü dolu kalıyordu, adilkatilim'in son
    4 ağır konusu bitene kadar). Tek akış + TEK paylaşılan havuz, bir bankanın
    arzı azalırken bir sonrakinin sayfalarının HEMEN kuyruğa girmesini sağlar
    — işçiler banka sınırında asla boşa düşmez."""
    # BANKALAR ARASI DÖNÜŞÜMLÜ (round-robin) akış: banka banka sırayla vermek,
    # banka başına tek-sayfa kuralıyla birleşince TÜM işçileri aynı bankaya
    # düşürüp seri çalışmaya zorluyordu (ölçüldü: 10 hat açıkken yalnız 1-2
    # banka işleniyordu, hız 2.6 istek/sn'de kaldı). Dönüşümlü akışta her hat
    # FARKLI bir bankaya düşer — bankalar arası paralellik gerçekten kullanılır.
    akislar = {b: _pages(b, limit) for b in todo}
    while akislar:
        for bank in list(akislar):
            try:
                yield bank, next(akislar[bank])
            except StopIteration:
                del akislar[bank]


def run_all(todo: list[str], banks: list[str], limit: int | None, progress: dict) -> None:
    """Tüm koşuyu TEK bir PAGE_WORKERS'lık işçi havuzuyla (varsayılan 7)
    işler — bkz. _all_pages. `done`/`real_done`/`lock`: banka başına "kaç
    dosya işlendi" ve "kaçı gerçekten sayfa (atlanmadı)" sayaçları thread'ler
    arası PAYLAŞILDIĞI için kilitli; bir bankanın SON dosyası (hangi thread
    bitirirse bitirsin) "sayfa tarandı" satırını TAM BİR KEZ tetikler."""
    totals = {b: _total_pages(b, limit) for b in todo}
    lock = threading.Lock()
    # Banka başına aynı anda TEK sayfa — mükerrer konu tetiklemesini azaltır.
    # Bankalar arası paralellik bundan ETKİLENMEZ (her bankanın kendi kilidi).
    banka_kilit = {b: threading.Lock() for b in todo}
    done = {b: 0 for b in todo}
    real_done = {b: 0 for b in todo}

    def _handle(pair: tuple) -> None:
        bank, (i, total, url, body, skip_reason) = pair
        with lock:
            progress["seen"] += 1
            seen = progress["seen"]
        g = f", genel {seen}/{progress['total']}"
        if skip_reason:
            log.info("  [%d/%d%s] %s: atlandı (%s)", i, total, g, bank, skip_reason)
        else:
            log.info("  [%d/%d%s] %s: %s", i, total, g, bank, url[-90:])
            try:
                with banka_kilit[bank]:        # aynı bankadan tek sayfa
                    process_page(bank, url, body, banks)
            except Exception as exc:            # noqa: BLE001 — TEK sayfa TÜM koşuyu
                # çökertmesin (research_bank katmanındaki aynı prensip bir üst
                # seviyede de geçerli: beklenmedik bir hata bu sayfayı atlasın,
                # kalan binlerce sayfa/banka etkilenmesin). own_verdict
                # KAYDEDİLMEZ — bir sonraki koşuda bu sayfa tekrar denenir.
                log.error("  [SAYFA HATASI] %s / %s: %s: %s", bank, url,
                           type(exc).__name__, exc)
            with lock:
                real_done[bank] += 1
        with lock:
            done[bank] += 1
            if done[bank] == totals[bank]:
                log.info("%s: %d sayfa tarandı", bank, real_done[bank])

    with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as ex:
        list(ex.map(_handle, _all_pages(todo, limit)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--banks", nargs="*", help="taranacak bankalar (boş=tümü)")
    ap.add_argument("--limit", type=int, help="banka başına sayfa sınırı (pilot için)")
    # Tablo üretimi bittiğinde tablolar HAM haldedir (mükerrerler ayrı, tarih
    # damgası yok, 'Sunulmuyor' iddiaları temizlenmemiş). --son-islem ile
    # bakım pipeline'ı üretimin hemen ardından çalışır. VARSAYILAN KAPALI:
    # uzun bir üretim koşusunun sonuna sürpriz bir bakım eklemek yerine
    # kullanıcı açıkça istesin.
    ap.add_argument("--son-islem", action="store_true",
                    help="üretim bitince tablo sonrası bakımı da çalıştır "
                         "(dedup -> denetim -> tarih -> expire -> sunulmuyor)")
    args = ap.parse_args()
    banks = all_banks()
    todo = args.banks or banks
    progress = {"seen": 0, "total": sum(_total_pages(b, args.limit) for b in todo)}
    log.info("TOPLAM %d dosya taranacak (%d banka)", progress["total"], len(todo))
    run_all(todo, banks, args.limit, progress)

    if args.son_islem:
        log.info("=== TABLO ÜRETİMİ BİTTİ — tablo sonrası bakım başlıyor ===")
        from .son_islem import calistir, ADIMLAR
        calistir(list(ADIMLAR))


if __name__ == "__main__":
    main()
