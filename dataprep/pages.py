"""Sayfa ham metnini LLM ile TEMİZLE — nav/footer/boilerplate at, gerçek içeriği çıkar.

Kural (regex/liste) YOK — GÖRSEL ve PDF aşamalarıyla TUTARLI biçimde LLM karar verir:
crawl trafilatura/ham-HTML ile sayfayı indirir (nav+footer+duyuru dahil), bu aşama her
sayfanın metnini LLM'e verip "bu sayfaya ÖZGÜ ürün/kampanya/hizmet içeriğini çıkar,
tekrarlayan site öğelerini (menü, footer, duyuru, sosyal medya, çerez) at" dedirtir.

İki yerde kullanılır:
  * POSTPROCESS: mevcut <bank>_site/*.md sayfalarını temizler (bu modülün process_bank'ı).
  * CRAWL-HOOK: store.fetch_and_store yeni sayfayı kaydetmeden clean_page ile temizler.

LLM ulaşılamazsa None döner -> ham metne DOKUNULMAZ (kayıp yok; sonra tekrar denenir).
Havuzlu vlm client (opn<100) + thread pool + url-cache resume + ledger.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from dataprep import on_eleme, vlm
from dataprep.ledger import Ledger

log = logging.getLogger("dataprep.pages")

WORKERS = int(os.environ.get("PAGE_WORKERS", "50"))
# EŞİK YOK (kullanıcı kararı 2026-08-19): "TÜM SİTE VE PDF'LER eksiksiz,
# 1 karakter 1 KB bile olsa İŞLENECEK". Eskiden 40 karakterin altındaki gövde
# LLM'e hiç gönderilmiyordu — taranmış bir PDF'in tek satırlık başlığı ya da
# kısa ama veri taşıyan bir sayfa böyle sessizce atlanabiliyordu.
MIN_LEN = int(os.environ.get("CONTENT_MIN_LEN", "0"))
# Tek seferde LLM'e verilecek ham metin: 8196 KARAKTER (kullanıcı kararı
# 2026-08-19). Bu bizim TEK organik kırpmamızdır — başka hiçbir yerde, ne
# girdi ne çıktı tarafında bağlam kırpılmaz.
CHUNK = int(os.environ.get("CONTENT_CHUNK_CHARS", "8196"))
OVERLAP = 820          # 8196'nın tam %10'u kadar bağlam dikişi
# Erken relevance kararı: 10 chunk'a (81.960 karakter) kadar TAMAMI işlenir.
# 10 chunk'tan uzun devasa dokümanlarda İLK 10 parçanın TAMAMI 'gereksiz' ise durdurulur.
_TUMU_ISLE_SINIRI = int(os.environ.get("CONTENT_TUMU_ISLE_SINIRI", "10"))
# Kaç chunk'tan itibaren ÖN-ELEME yapılsın (majority eşiğinden AYRI).
_ON_ELEME_ESIGI = int(os.environ.get("ON_ELEME_ESIGI", "2"))
# Art arda bu kadar "gereksiz" chunk gelirse dosyanın gerisine bakılmaz.
_ARDISIK_GEREKSIZ_SINIRI = int(os.environ.get("ARDISIK_GEREKSIZ", "10"))
_KARAR_CHUNK = int(os.environ.get("CONTENT_KARAR_CHUNK", "10"))

# AŞAMA 3 (PDF) ve AŞAMA 4 (sayfa) ORTAK promptu — kullanıcı kararı
# 2026-08-22: "3 ve 4 birebir aynı süreç, sadece birisi PDF birisi page".
# Talimat TEK kaynaktan gelir (dataprep/temizleme_prompt.py); eskiden burada
# ve content.py'de AYRI tanımlıydı ve ayrışmışlardı (bkz. o modülün docstring'i).
from dataprep.temizleme_prompt import (ETIKET_Q, GOAL as _GOAL, DATE_RULE as _DATE_RULE,
                                        RELEVANCE_RULE as _RELEVANCE_RULE,
                                        JSON_HEAD as _JSON_HEAD,
                                        JSON_TAIL as _JSON_TAIL)

_CLEAN_Q = (_GOAL + _DATE_RULE + _RELEVANCE_RULE + "\nSayfa URL: {url}{title_line}\n\nHam metin:\n\"\"\"{body}\"\"\""
            + _JSON_HEAD + "LLM-friendly markdown ya da boş" + _JSON_TAIL)
_CONT_Q = (_GOAL + _DATE_RULE + _RELEVANCE_RULE + "\nSayfa URL: {url}{title_line}\n\nBu, uzun sayfanın DEVAM parçası. "
           "Önceki temiz çıktının sonu:\n\"\"\"{tail}\"\"\"\nBunu TEKRARLAMA; bu parçadaki "
           "YENİ ürün/kampanya içeriğini kaldığın yerden, akıcı biçimde ekle.\n\n"
           "Ham metin (devam):\n\"\"\"{body}\"\"\""
           + _JSON_HEAD + "devam markdown ya da boş" + _JSON_TAIL)


def _title_line(title: str) -> str:
    t = (title or "").strip()
    return f"\nSayfa Başlığı: {t}" if t else ""


def _clean_one(prompt: str) -> dict | None:
    """(content, gecerlilik_baslangic, gecerlilik_bitis, relevance) sözlüğü ya da None (LLM yok)."""
    # max_tokens BİZ BELİRLEMİYORUZ: sunucu kendi tavanını uygulasın
    # (sabit tavan uzun içeriği sessizce kesebiliyordu).
    d = vlm.call_json(vlm.txt_msg(prompt))
    if not d:
        return None
    rel = (d.get("musteri_icerigi") or "").strip()
    # durum_tahmini: ortak prompt (temizleme_prompt.py) bunu HER İKİ aşamada da
    # istiyor — "3 ve 4 birebir aynı süreç" kuralı. Eskiden sayfa tarafı bu
    # alanı OKUMUYORDU, dolayısıyla HİÇ TARİHİ OLMAYAN ama içeriğinde
    # "kampanya sona ermiştir" yazan sayfalar 'bitmedi' sayılıyor ve süresi
    # geçmiş kampanyalar tabloya girebiliyordu. Artık okunuyor.
    dt = (d.get("durum") or d.get("durum_tahmini") or "").strip()
    return {"content": (d.get("content") or "").strip(),
            "start": (d.get("gecerlilik_baslangic") or "").strip(),
            "end": (d.get("gecerlilik_bitis") or "").strip(),
            "durum": dt if dt in ("gecerli", "suresi_gecmis", "bilinmiyor") else "",
            "relevance": rel if rel in ("gerekli", "gereksiz") else "gerekli"}


def _fold_dates(agg: dict, part: dict) -> None:
    """Sayfa düzeyinde tarih topla: en GEÇ bitiş, en ERKEN başlangıç (ISO -> lexik = kronolojik)."""
    e = part.get("end") or ""
    if e and (not agg["end"] or e > agg["end"]):
        agg["end"] = e
    s = part.get("start") or ""
    if s and (not agg["start"] or s < agg["start"]):
        agg["start"] = s
    # durum_tahmini SADECE hiç tarih yoksa anlamlıdır (content.py::_fold_dates
    # ile AYNI ilke). Parçalardan biri "süresi geçmiş" diyorsa onu tut.
    d = part.get("durum") or ""
    if d and not agg.get("durum"):
        agg["durum"] = d


def _majority_relevance(votes: list[str], toplam_chunk: int | None = None) -> str:
    """content.py::_majority_relevance ile BİREBİR AYNI kural (tek kaynak
    olmadığı için burada da tekrarlanıyor; ikisi ayrışmamalı).

    KÜÇÜK belge (< CONTENT_TUMU_ISLE_SINIRI chunk) -> TEK 'gerekli' yeter.
    BÜYÜK belge -> ÇOĞUNLUK. Eşitlik/oy yok -> 'gerekli' (veri elenmez).
    Kullanıcı kararı 2026-08-23."""
    if not votes:
        return "gerekli"
    import os as _os
    esik = int(_os.environ.get("CONTENT_TUMU_ISLE_SINIRI", "10"))
    n = toplam_chunk if toplam_chunk is not None else len(votes)
    if n < esik:
        return "gerekli" if "gerekli" in votes else "gereksiz"
    gereksiz = sum(1 for v in votes if v == "gereksiz")
    return "gereksiz" if gereksiz > len(votes) - gereksiz else "gerekli"


def clean_page(raw_body: str, url: str = "", title: str = "") -> tuple[str | None, dict]:
    """Ham sayfa gövdesini LLM ile TEMİZLE + LLM-friendly yeniden yaz; ayrıca kampanya
    geçerlilik tarihini VE içerik-düzeyinde gerekli/gereksiz etiketini çıkarır (URL
    bazlı ön-elemeden çok daha isabetli — LLM artık GERÇEK içeriği görüyor). Uzun
    sayfa parçalanıp overlap-bağlamla dikilir, tarih tüm parçalardan toplanır,
    relevance parçalar arası ÇOĞUNLUK OYLAMASIYLA belirlenir. Dönen: (temiz_markdown
    | None, {"start","end","relevance"}). None -> LLM ulaşılamadı (çağıran ham
    metni korusun).

    title verilirse (sayfanın <title>'ı) prompt'a AYRI bir satır olarak eklenir —
    kampanya adı bazen sadece başlıkta geçip gövdede geçmeyebilir. SADECE LLM
    girdisini zenginleştirir; ham gövdeye/imzaya (digest) dokunmaz."""
    dates = {"start": "", "end": "", "relevance": "gerekli"}
    body = (raw_body or "").strip()
    if not body:
        dates["relevance"] = "gereksiz"
        _durumu_coz(dates)
        return "", dates
    tl = _title_line(title)
    today = date.today().isoformat()
    adim = CHUNK - OVERLAP
    toplam_chunk = max(1, (len(body) + adim - 1) // adim)

    def _etiketle(part_txt: str) -> str:
        """Bu parça müşteri içeriği mi? Hızlı tek kelimelik LLM sorusu."""
        d = vlm.call_json(vlm.txt_msg(ETIKET_Q.format(
            url=url or "-", title_line=tl, today=today, body=part_txt)))
        if not d:                      # LLM'e ulaşılamadı -> eleme YAPMA
            return "gerekli"
        rel = (d.get("musteri_icerigi") or "").strip()
        return rel if rel in ("gerekli", "gereksiz") else "gerekli"

    if len(body) <= CHUNK:
        # ÖNCE HIZLI GEREKLİ/GEREKSİZ ANALİZİ (1-2s)
        rel = _etiketle(body)
        if rel == "gereksiz":
            dates["relevance"] = "gereksiz"
            _durumu_coz(dates)
            return "", dates
        # GEREKLİ İSE CLEAN TEXT AL
        d = _clean_one(_CLEAN_Q.format(url=url or "-", title_line=tl, today=today, body=body))
        if d is None:
            return None, dates
        _fold_dates(dates, d)
        dates["relevance"] = "gerekli"
        _durumu_coz(dates)
        return d["content"], dates
    # ÖN-ELEME EŞİĞİ: kullanıcı kararı 2026-08-23 ("bu uzun metinler önce
    # gerekli/gereksiz etiketinden geçirilsin, boşa yazmasın gereksizse").
    # ESKİDEN 10 chunk üstü gerekiyordu -> emlakkatilim'de kalan 1391 sayfanın
    # yalnız 4'ü bu eşiği aşıyordu, yani ön-eleme PRATİKTE HİÇ çalışmıyordu.
    # 2 chunk'tan itibaren eleme yapılır: gereksizse dikiş HİÇ başlamaz.
    # MAJORITY KURALI DEĞİŞMEDİ — _majority_relevance hâlâ
    # CONTENT_TUMU_ISLE_SINIRI (10) kullanır: 10 chunk altı tek 'gerekli'
    # yeter, 10 ve üstü çoğunluk ister. Bu ayrı bir değişken.
    # ============ CHUNK BAZLI ETİKETLEME (kullanıcı kararı 2026-08-23) ============
    # "Her chunk önce LLM'e gerekli mi diye sorulur. Gerekli dedikleri işlenir.
    #  200 sayfalık PDF'ler için bile tek tek sorulur. Eğer bir dosya için art
    #  arda 10 chunk gereksiz ise o zaman gerisine bakmasın."
    #
    # NEDEN: tam temizleme isteği ~135s sürüyor (tünelin 120s limitini aşıyor,
    # sonsuz retry döngüsü). Hızlı etiket isteği ~2.4s — 56 kat hızlı. Sadece
    # 'gerekli' chunk'lar pahalı temizlemeye girer.
    #
    # VERİ KAYBI YOK: 'gerekli' çıkan her chunk TAM temizlemeden geçer,
    # hiçbir karakteri kırpılmadan. Etiket isteği diske hiçbir şey yazmaz.
    # Emin olunmayan durumda prompt 'gerekli' demeyi emrediyor.

    def _etiketle(part_txt: str) -> str:
        """Bu parça müşteri içeriği mi? Hızlı tek kelimelik LLM sorusu."""
        d = vlm.call_json(vlm.txt_msg(ETIKET_Q.format(
            url=url or "-", title_line=tl, today=today, body=part_txt)))
        if not d:                      # LLM'e ulaşılamadı -> eleme YAPMA
            return "gerekli"
        rel = (d.get("musteri_icerigi") or "").strip()
        return rel if rel in ("gerekli", "gereksiz") else "gerekli"

    parcalar = [body[k * adim : k * adim + CHUNK] for k in range(toplam_chunk)]
    votes: list[str] = []

    if toplam_chunk >= _ON_ELEME_ESIGI:
        # Etiketler PARALEL alınır (hepsi kısa istek), ama ARDIŞIK GEREKSİZ
        # sayacı sıra korunarak işlenir.
        etiketler = on_eleme.esle(_etiketle, parcalar)

        ardisik_gereksiz = 0
        kesme_noktasi = len(parcalar)
        for k, e in enumerate(etiketler):
            if e == "gereksiz":
                ardisik_gereksiz += 1
                if ardisik_gereksiz >= _ARDISIK_GEREKSIZ_SINIRI:
                    kesme_noktasi = k + 1      # bu noktadan SONRASINA bakma
                    log.info("  %s: %d ardışık 'gereksiz' chunk — %d/%d'ten "
                             "sonrasına BAKILMIYOR",
                             url or "-", ardisik_gereksiz, kesme_noktasi,
                             toplam_chunk)
                    break
            else:
                ardisik_gereksiz = 0

        islenecek = [(k, parcalar[k]) for k in range(kesme_noktasi)
                     if etiketler[k] == "gerekli"]
        votes.extend(etiketler[:kesme_noktasi])

        if not islenecek:
            log.info("  %s: %d/%d chunk'ın hiçbiri 'gerekli' değil — "
                     "temizlemeye HİÇ BAŞLANMIYOR", url or "-",
                     len(etiketler), toplam_chunk)
            dates["relevance"] = "gereksiz"
            _durumu_coz(dates)
            return "", dates
        if len(islenecek) < toplam_chunk:
            log.info("  %s: %d/%d chunk 'gerekli' — sadece onlar temizleniyor",
                     url or "-", len(islenecek), toplam_chunk)
    else:
        islenecek = [(k, parcalar[k]) for k in range(len(parcalar))]

    # ---- SADECE 'gerekli' chunk'lar temizlenir — DİKİŞ YOK, PARALEL ----
    # KULLANICI KARARI 2026-08-23: "Dikiş işini de boş ver, gerekli denen
    # chunk'lar kendisi için clean text versin yeter, dikiş iptal."
    #
    # Dikiş kalkınca chunk'lar birbirine bağımlı DEĞİL -> hepsi PARALEL
    # gidebilir. Sıralı döngü tek yavaş chunk'ta tüm sayfayı bekletiyordu.
    # Çıktılar indekse göre sıralanıp birleştirilir; sıra korunur.
    #
    # VERİ KAYBI YOK: chunk'lar %10 (820 karakter) örtüşmeli üretiliyor,
    # sınırdaki cümleler iki parçada da var. Hiçbir metin kırpılmaz.
    def _temizle(ix_part):
        k, part = ix_part
        return k, _clean_one(_CLEAN_Q.format(url=url or "-", title_line=tl,
                                             today=today, body=part))

    sonuclar = (on_eleme.esle(_temizle, islenecek) if len(islenecek) > 1
                else [_temizle(islenecek[0])])

    sirali = []
    for k, d in sonuclar:
        if d is None:
            return None, dates
        _fold_dates(dates, d)
        if not votes:                  # ön-eleme yapılmadıysa oy buradan
            votes.append(d["relevance"])
        if d["content"]:
            sirali.append((k, d["content"]))
    sirali.sort(key=lambda x: x[0])
    out_pieces = [c for _k, c in sirali]
    dates["relevance"] = _majority_relevance(votes, toplam_chunk)
    _durumu_coz(dates)
    return "\n\n".join(out_pieces), dates


def _split_front(text: str) -> tuple[str, str]:
    """(frontmatter_bloğu, gövde). Frontmatter yoksa ('', text).

    SINIR SATIR BAZLI bulunur — `text.split("---", 2)` DEĞİL.
    ÖLÇÜLEN BUG (2026-08-22): eski sürüm metnin İÇİNDEKİ ilk "---" dizisinde
    bölüyordu. kuveytturk'ün PDF adlarında "---" geçiyor
    (`.../medium/29012021---kuveyt-turk-...pdf`), dolayısıyla frontmatter
    ORTADAN kesiliyor, `url` KIRPIK okunuyor ve frontmatter'ın kalanı
    gövdenin BAŞINA sızıyordu.

    Etkisi zincirleme: content.py bu kırık gövdeyi diske yazdı; embed.py
    kırpık URL'den point_id ürettiği için farklı belgeler AYNI id'ye düşüp
    Qdrant'ta birbirinin üzerine yazdı (5 kredi derecelendirme belgesi ->
    2 point, 3 belge kayıp).

    Doğrusu: yalnız TEK BAŞINA "---" olan satırlar sınırdır (YAML kuralı)."""
    if not text.startswith("---"):
        return "", text
    satirlar = text.split("\n")
    for i, satir in enumerate(satirlar[1:], 1):
        if satir.strip() == "---":
            front = "\n".join(satirlar[:i + 1])
            return front, "\n".join(satirlar[i + 1:]).lstrip("\n")
    return "", text                           # kapanmamış frontmatter -> gövde say


def _url_of(front: str) -> str:
    for line in front.splitlines():
        if line.strip().startswith("url:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def _title_of(front: str) -> str:
    for line in front.splitlines():
        if line.strip().startswith("title:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def _durumu_coz(dates: dict) -> None:
    """Modelin `durum` kararı -> frontmatter'ın `campaign_status` alanı.

    KARARI TAMAMEN LLM VERİR (kullanıcı kararı 2026-08-22). Eskiden burada
    "tarih varsa koddan hesapla, LLM'i ezme" mantığı vardı; bu, projenin
    temel ilkesine aykırıydı. Model bugünün tarihini prompt'ta görüyor ve
    tarihleri, göreceli ifadeleri, 'kampanya sona ermiştir' gibi açık
    beyanları BİRLİKTE değerlendirip karar veriyor.

    'bilinmiyor' -> alan yazılmaz (varsayılan 'bitmedi' gibi davranılır),
    böylece belirsizlikte içerik ELENMEZ."""
    d = dates.get("durum") or ""
    if d == "suresi_gecmis":
        dates["status"] = "bitti"
    elif d == "gecerli":
        dates["status"] = "bitmedi"


def _set_front_dates(front: str, dates: dict) -> str:
    """Frontmatter bloğuna campaign_start/campaign_end/campaign_status anahtarlarını
    yaz (varsa günceller). Frontmatter yoksa yeni bir blok oluşturur.
    campaign_status: 'bitti'|'bitmedi' — tarihli sayfada tarihten hesaplanır (deterministik,
    LLM'e sorulmaz); tarihsiz sayfada SADECE model içerikten GERÇEKTEN eminse 'bitti'
    yazılır, aksi halde 'bitmedi' (varsayılan) kalır."""
    pairs = {}
    # TARİH ALANLARI HER ZAMAN yazılır (kullanıcı kararı, 2026-08-19) — boş
    # olsa bile. "Sorulmadı" ile "soruldu, belgede yok" ayırt edilebilsin;
    # content.py::_frontmatter ile AYNI ilke. Tek taraflı tarih de böylece
    # hangi tarafın bilindiğini açıkça taşır.
    pairs["campaign_start"] = dates.get("start") or ""
    pairs["campaign_end"] = dates.get("end") or ""
    if dates.get("status"):
        pairs["campaign_status"] = dates["status"]
    if dates.get("relevance"):
        pairs["content_relevance"] = dates["relevance"]
    if not pairs:
        return front
    if not front:
        lines = [f'{k}: "{v}"' for k, v in pairs.items()]
        return "---\n" + "\n".join(lines) + "\n---"
    inner = front[3:-3] if front.startswith("---") and front.endswith("---") else front
    kept = [ln for ln in inner.splitlines()
            if ln.split(":", 1)[0].strip() not in pairs]
    for k, v in pairs.items():
        kept.append(f'{k}: "{v}"')
    return "---" + "\n".join([""] + [ln for ln in kept if ln.strip()]) + "\n---"


def process_bank(slug: str, workers: int = WORKERS) -> None:
    """Bir bankanın TÜM sayfa .md'lerini LLM ile temizler (pdf_text/image_text hariç)."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    if not site.exists():
        log.warning("%s: klasör yok", slug); return
    ledger = Ledger(site / "_processing_log.jsonl")
    cpath = site / "_page_clean_cache.json"
    done: dict[str, int] = json.loads(cpath.read_text()) if cpath.exists() else {}

    # SAYFA (.md kök) temizlenir; özel klasörler (_raw, content, _pdf_clean, image_text) atlanır.
    _OZEL = ("content", "_raw", "_pdf_clean", "_image_tables", "image_text", "pdf_text")
    # "_" ile başlayan KLASÖRLER iç/teknik dizinlerdir (_raw, _pdf_clean...).
    # AMA percent-encode edilmiş TÜRKÇE KARAKTER de "_" ile başlayabilir:
    # ".../bireysel/%C3%B6demeler-ve-hizmetler/..." -> "_C3_B6demeler-ve-..."
    # Bu klasör GERÇEK bir sayfa dizini; "_" kuralı onu sessizce eliyordu
    # (ölçüldü 2026-08-25: ziraatkatilim'de 2 gerçek sayfa hiç temizlenmedi,
    # denetimde "eksik" görünüp her koşuda kapanmıyordu).
    # AYRIM: percent-encode kalıbı (_XX iki hex hane) teknik klasör DEĞİLDİR.
    import re as _re
    _ENC = _re.compile(r"^_[0-9A-Fa-f]{2}")

    def _teknik_klasor(part: str) -> bool:
        return (part in _OZEL) or (part.startswith("_") and not _ENC.match(part))

    # DOSYA ADI "_" ile başlasa bile İÇERİĞİ varsa temizlenir. Şablon adlı
    # dosyalar (__Link__.md, __ApplicationLink__.md) CMS artığı gibi görünür
    # ama içlerinde GERÇEK kampanya metni olabilir — ölçüldü 2026-08-25:
    # albaraka'nın __Link__.md'sinde "Dijital Müşterilere Özel Pratik
    # Finansman Kart" kampanyası vardı ve her koşuda atlanıyordu.
    # Teknik KLASÖR kuralı (yol parçaları) aynen korunur; yalnız DOSYA ADI
    # kuralı gevşetildi — dosyanın adı değil, içeriği belirleyici.
    mds = [p for p in site.rglob("*.md")
           if not p.name.startswith(".")
           and not any(_teknik_klasor(part) for part in p.relative_to(site).parts[:-1])]
    todo = [p for p in mds if str(p.relative_to(site)) not in done]
    log.info("%s: %d sayfa (%d temizlenmiş, %d yapılacak, workers=%d)",
             slug, len(mds), len(mds) - len(todo), len(todo), workers)

    lock_n = [0]

    def work(p: Path):
        rel = str(p.relative_to(site))
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return rel, None
        front, body = _split_front(text)
        cleaned, dates = clean_page(body, _url_of(front), _title_of(front))
        if cleaned is None:                      # LLM yok -> dokunma, cache'leme (retry)
            ledger.record("page_clean", _url_of(front), decision="unreachable")
            return rel, None
        # kampanya tarihini frontmatter'a yaz (campaign_start/campaign_end).
        front = _set_front_dates(front, dates)
        # temiz gövdeyi yaz (frontmatter korunur). boş -> boilerplate-only sayfa.
        new = (front + "\n\n" + cleaned + "\n") if front else (cleaned + "\n")
        p.write_text(new, encoding="utf-8")
        dec = "content" if cleaned else "boilerplate_only"
        ledger.record("page_clean", _url_of(front), decision=dec,
                      reason=f"{len(body)}->{len(cleaned)} char end={dates.get('end') or '-'}")
        return rel, len(cleaned)

    # ex.map SIRALI sonuç döndürür: 3. sayfa yavaşsa 4-25 bitmiş olsa bile
    # döngü bekler. Sonuç: (a) ilerleme sayacı donuk görünür — bekçi 12 dakika
    # "sayaç sabit" yazdı, (b) 100'lük kayıt aralığı çökmede 99 sayfalık işi
    # çöpe atardı (kullanıcı kuralı: veri kaybı yok).
    # as_completed + her 10 sayfada kayıt: iş BİTTİĞİ anda sayılır, kayıp
    # penceresi 99'dan 9'a iner.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, t): t for t in todo}
        for j, fut in enumerate(as_completed(futs), 1):
            rel, res = fut.result()
            if res is not None:
                done[rel] = res
            # HER SAYFADA KAYDET (10'da bir DEĞİL). Kullanıcı kararı
            # 2026-08-23: "nöbetçilerini adam gibi kur, süreci baltalamasınlar".
            # 10'luk kayıt iki soruna yol açıyordu: (a) bekçi disk ilerlemesini
            # dakikalarca göremeyip sağlam süreci "asıldı" sanıp öldürüyordu,
            # (b) kill anında 9 sayfalık iş yeniden yapılıyordu.
            # Maliyet: küçük bir JSON'un saniyede birkaç kez yazılması — ihmal
            # edilebilir; kazanç: gerçek zamanlı ilerleme görünürlüğü.
            cpath.write_text(json.dumps(done, ensure_ascii=False))
            if j % 100 == 0:
                log.info("  %s: %d/%d temizlendi", slug, j, len(todo))
    cpath.write_text(json.dumps(done, ensure_ascii=False))
    empty = sum(1 for v in done.values() if v == 0)
    log.info("%s BİTTİ: %d sayfa temiz (%d boilerplate-only/boş)", slug, len(done), empty)


def retag_bank(slug: str, workers: int = WORKERS) -> None:
    """TEK SEFERLİK, TAM (resumable/skip YOK — kasıtlı): bankanın _raw/*.md
    (crawl-time HAM metin, LLM'den ÖNCEKİ hâli) dosyalarından clean_page'i
    BAŞTAN çalıştırır — yeni eklenen gerekli/gereksiz içerik-etiketini (ve
    güncel tarih mantığını) mevcut TÜM sayfalara işler, siteyi tekrar
    TARAMADAN (ağa gitmez, sadece LLM'e). 'gereksiz' çıkan sayfaların hem
    temiz (.md) hem ham (_raw) dosyası SİLİNİR — sadece URL+hash katalogda
    kalır (URL'i yeterli); 'gerekli' çıkanların frontmatter'ı (title/desc/
    bank/type/crawled_at KORUNARAK) güncellenir. Kataloğa relevance yazılıp
    kaydedilir — content.py bunu okuyup ileride bu URL'i tekrar işlemez.
    LLM'e ulaşılamayan sayfalar DOKUNULMADAN bırakılır (veri kaybı yok)."""
    site = Path(__file__).resolve().parents[1] / "data" / f"{slug}_site"
    cat_path = site / "_catalog.json"
    if not cat_path.exists():
        log.warning("%s: katalog yok, atlanıyor", slug)
        return
    catalog: dict = json.loads(cat_path.read_text(encoding="utf-8"))
    ledger_path = site / "_page_clean_ledger.json"
    done_ledger: dict = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    raw_root = site / "_raw"
    plog = Ledger(site / "_processing_log.jsonl")
    lock = threading.Lock()

    todo = []
    for u, r in catalog.items():
        if r.get("kind") != "page" or r.get("status") == "removed":
            continue
        if u in done_ledger and done_ledger[u].get("source_hash") == r.get("hash"):
            continue
        todo.append((u, r))
    log.info("%s (sayfa temizlik): %d sayfa işlenecek (workers=%d)", slug, len(todo), workers)

    def work(item: tuple[str, dict]):
        url, rec = item
        raw_path = raw_root / rec["path"]
        out_path = site / rec["path"]
        if not raw_path.exists():
            plog.record("retag", url, decision="missing_raw")
            return url, None
        try:
            raw_body = raw_path.read_text(encoding="utf-8")
            existing = out_path.read_text(encoding="utf-8") if out_path.exists() else raw_body
            front, _old_body = _split_front(existing)
            title = _title_of(front) or rec.get("title", "")
            cleaned, dates = clean_page(raw_body, url, title)
            if cleaned is None:
                plog.record("retag", url, decision="llm_unreachable")
                return url, None            # eski dosya DOKUNULMADAN kalır
            relevance = dates.get("relevance") or "gerekli"
            if relevance == "gereksiz":
                # İÇERİK-DÜZEYİNDE gereksiz -> dosyaları SİL (hem temiz hem ham),
                # URL+hash katalogda kalması yeterli.
                out_path.unlink(missing_ok=True)
                raw_path.unlink(missing_ok=True)
                plog.record("retag", url, decision="deleted_gereksiz")
            else:
                new_front = _set_front_dates(front, dates) if front else (
                    f'---\nurl: "{url}"\ntitle: "{title}"\nbank: "{rec.get("bank", slug)}"\ntype: page\n'
                    f'crawled_at: {rec.get("crawled_at", "2026-08-20")}\n'
                    f'campaign_start: "{dates.get("start", "")}"\ncampaign_end: "{dates.get("end", "")}"\n'
                    f'content_relevance: "{relevance}"\n---'
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                new_text = (new_front + "\n\n" + cleaned + "\n") if new_front else (cleaned + "\n")
                out_path.write_text(new_text, encoding="utf-8")
                plog.record("retag", url, decision="ok",
                            reason=f"relevance={relevance} end={dates.get('end') or '-'}")
            return url, relevance
            return url, relevance
        except Exception as exc:
            log.warning("  %s HATA (retag): %s", url, exc)
            plog.record("retag", url, decision="error", reason=str(exc))
            return url, None

    ok = 0
    gereksiz_n = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, it): it for it in todo}
        for j, fut in enumerate(as_completed(futures), 1):
            url, relevance = fut.result()
            if relevance is not None:
                ok += 1
                if relevance == "gereksiz":
                    gereksiz_n += 1
                rec_hash = catalog.get(url, {}).get("hash", "")
                with lock:
                    catalog[url]["relevance"] = relevance
                    done_ledger[url] = {"source_hash": rec_hash, "relevance": relevance}
            if j % 10 == 0 or j == len(todo):
                with lock:
                    cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
                    ledger_path.write_text(json.dumps(done_ledger, ensure_ascii=False, indent=1), encoding="utf-8")
                log.info("  %s (sayfa temizlik): %d/%d işlendi", slug, j, len(todo))
    with lock:
        cat_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
        ledger_path.write_text(json.dumps(done_ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("%s (sayfa temizlik) BİTTİ: %d/%d başarılı (%d gereksiz -> silindi)", slug, ok, len(todo), gereksiz_n)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Sayfa metnini LLM ile temizle")
    ap.add_argument("banks", nargs="*")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(os.path.basename(d)[:-5]
                                 for d in glob.glob(str(root / "*_site")))
    for b in banks:
        process_bank(b)


if __name__ == "__main__":
    main()
