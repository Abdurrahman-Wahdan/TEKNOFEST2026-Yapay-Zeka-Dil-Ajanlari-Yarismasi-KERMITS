"""Tablo denetim ajanı: her tabloyu KAYNAK KAYNAK denetler, eksikleri tamamlar.

Tablo üretimi bittikten SONRA çalışır. Üç aşama:

  1) DENETİM (paralel)  — tablodaki HER benzersiz referans URL'i için AYRI bir
     ajan. Ajan o URL'in 8196'lık chunk'larına TEK TEK bakar (chunk'lar
     birbirinden habersizdir, %10 overlap sayesinde sınıra denk gelen bilgi en
     az bir chunk'ta bütün kalır) ve şunu sorar: "bu kaynaktaki bilgi tabloda
     gerekli yere yazılmış mı?" Eksik varsa hangi satır/sütuna ne yazılması
     gerektiğini bildirir.

  2) BİRLEŞTİRME (tek ajan) — tüm geri bildirimleri görüp tabloyu SON HALİNE
     getirir. Mevcut doğru veriyi bozmaz, yalnız eksikleri tamamlar.

  3) TARİH DAMGASI (kod, LLM YOK) — ajanların "bu satırdaki bilgi şu kaynaktan
     geldi" etiketlerine bakılır; o kaynakların tarihi kesiştirilir ve satıra
     (banka) tek bir geçerlilik damgası yazılır.

Bağlantı dayanıklılığı bank_agent.py ile AYNI: sınırsız retry, önce tünel
kontrolü sonra yeniden deneme, üstel backoff (30s tavan), NET_SEM paylaşımı.
Uzunluk/karakter sınırı YOK — chunk 8196 dışında hiçbir şey kırpılmaz.

Kullanım:
  python -m dataprep.compare.tablo_denetim              # tüm tablolar
  python -m dataprep.compare.tablo_denetim --tablo <id> # tek tablo
  python -m dataprep.compare.tablo_denetim --kuru       # yazmadan raporla
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import SystemMessage

from config import tunnel
from llm import get_llm
from llm.providers.vllm_provider import reset_http_pool

from ..net_limit import NET_SEM
from .json_mod import llm_kwargs
from . import store
from .retrieval import _kanonik_tarih, _kanonik_url, _url_kosulu, _shared, COLLECTION
from qdrant_client import models

log = logging.getLogger("dataprep.compare.tablo_denetim")

DENETIM_WORKERS = int(os.environ.get("DENETIM_WORKERS", "10"))
# Aynı anda kaç TABLO işlenir (her tablo kendi içinde DENETIM_WORKERS kaynak
# ajanı çalıştırır). Gerçek ağ eşzamanlılığını NET_SEM sınırlar.
TABLO_WORKERS = int(os.environ.get("DENETIM_TABLO_WORKERS", "5"))
_BACKOFF_MAX = float(os.environ.get("VLM_BACKOFF_MAX", "30"))
_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)


# --- bağlantı (bank_agent.py ile AYNI desen) ----------------------------------
def _kalici_hata(exc: Exception) -> bool:
    """400/403 KALICI DEĞİL: tünel soketi bayatlayınca nginx reddediyor ama
    aynı istek saniyeler sonra 200 dönüyor (bank_agent::_is_permanent ile
    aynı karar)."""
    s = str(exc)
    return any(k in s for k in ("401", "404", "413", "422", "BadRequest"))


def _cagir(prompt: str, ne: str, temp: float | None = None) -> str | None:
    """LLM'i SINIRSIZ retry ile çağırır. Her hatada ÖNCE tünel kontrolü,
    SONRA bekleme — hatanın en olası sebebi tünel adresinin değişmesi."""
    start = time.time()
    delay = 1.0
    attempt = 0
    last_warn = 0.0
    while True:
        t = temp if temp is not None else _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            # Bu ajan HİÇ araç kullanmaz, her çağrı düz JSON bekler ->
            # vLLM JSON zorlaması koşulsuz açık (bkz. json_mod.py).
            llm = get_llm("gemma", temperature=t, **llm_kwargs(True))
            _t0 = time.time()
            with NET_SEM:
                res = llm.invoke([SystemMessage(prompt)])
            NET_SEM.report(ok=True, duration=time.time() - _t0)
            return getattr(res, "content", None) or None
        except Exception as exc:
            if _kalici_hata(exc):
                log.error("    [KALICI HATA] %s: %s", ne, exc)
                return None
            NET_SEM.report(ok=False)
            # Hata alınan bağlantıya geri dönülmez: havuz tamamen kapatılıp
            # tazesi açılır (kullanıcı kararı 2026-08-22, vlm.py ile AYNI).
            reset_http_pool(f"denetim/{type(exc).__name__}")
            tunnel.refresh_if_needed()
            elapsed = time.time() - start
            if elapsed - last_warn >= 300:
                log.warning("    [DENETIM_UZUN_SÜRELİ_HATA] %s: %.0fs'dir başarısız "
                            "(deneme %d) — DEVAM ediyor", ne, elapsed, attempt + 1)
                last_warn = elapsed
            time.sleep(delay)
            delay = min(delay * 2, _BACKOFF_MAX)
            attempt += 1


def _tirnak_onar(s: str) -> str:
    """Bir JSON string DEĞERİNİN içinde kaçırılmamış çift tırnak varsa kaçırır.

    Gözlendi (2026-08-26): model  "deger": "yıllık ücret "sıfır" olarak"  gibi
    çıktı üretiyor; JSON teknik olarak bozuk oluyor ve 8 denemenin hepsi
    başarısız olup chunk SESSİZCE ATLANIYORDU (bilgi tabloya hiç girmiyordu).
    Karakter karakter yürünür: string içindeyken gelen `"`nin ardından ilk
    anlamlı karakter , } ] : değilse o tırnak VERİNİN PARÇASIDIR, kaçırılır.
    Sağlam JSON'a dokunmaz (kaçışlı tırnaklar zaten korunur)."""
    out = []
    icinde = False
    kacis = False
    for i, ch in enumerate(s):
        if not icinde:
            out.append(ch)
            if ch == '"':
                icinde = True
            continue
        if kacis:
            out.append(ch); kacis = False; continue
        if ch == '\\':
            out.append(ch); kacis = True; continue
        if ch == '"':
            j = i + 1
            while j < len(s) and s[j] in ' \t\r\n':
                j += 1
            if j >= len(s) or s[j] in ',}]:':
                out.append(ch); icinde = False
            else:
                out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


def _json_coz(metin: str | None,
              bekleyen: tuple[str, ...] = ()) -> tuple[dict | None, str]:
    """(sonuç, hata_metni). `bekleyen` YALNIZ STATİK alanlar için verilir —
    tablo şeması (columns/rows) DİNAMİKTİR, orada beklenti dayatılmaz
    (kullanıcı kararı 2026-08-20)."""
    if not metin:
        return None, "Cevap boş geldi."
    s = metin.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        s = s[4:] if s.lower().startswith("json") else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None, "Cevapta JSON bulunamadı."
    ham = s[i:j + 1]
    try:
        d = json.loads(ham)
    except ValueError as exc:
        # SON ÇARE: veri içi kaçırılmamış tırnakları onarıp bir kez daha dene.
        # Başarısızsa yine hata döner (retry merdiveni devreye girer).
        try:
            d = json.loads(_tirnak_onar(ham))
            log.info("    [JSON_ONARILDI] veri içi tırnak kaçırıldı")
        except ValueError:
            return None, f"JSON ayrıştırılamadı: {exc}"
    if not isinstance(d, dict):
        return None, f"Beklenen bir JSON nesnesi, gelen: {type(d).__name__}."
    eksik = [k for k in bekleyen if k not in d]
    if eksik:
        return None, (f"JSON'da şu alanlar eksik: {', '.join(eksik)}. "
                      f"Gelen alanlar: {', '.join(sorted(d)) or '(hiç)'}.")
    return d, ""


def _cagir_json(prompt: str, ne: str,
                bekleyen: tuple[str, ...] = ()) -> dict | None:
    """LLM'i çağırır; JSON bozuksa/eksikse SOMUT hatayı prompt'un SONUNA
    ekleyip yeniden dener. Sıra: her sıcaklıkta önce feedback'siz, sonra
    feedback'li (0.0 normal -> 0.0 feedback -> 0.3 normal -> ...)."""
    hata = ""
    for t in _TEMP_LADDER:
        for feedbackli in (False, True):
            p = prompt
            if feedbackli:
                if not hata:
                    continue
                p = (prompt + "\n\nÖnceki cevabında şu hata vardı: " + hata +
                     "\nBu hatayı vermeden SADECE geçerli JSON döndür.")
            d, hata = _json_coz(_cagir(p, ne, temp=t), bekleyen)
            if d is not None:
                return d
    log.warning("    JSON düzelmedi (%s): %s", ne, hata)
    return None


# --- kaynak chunk'ları --------------------------------------------------------
def _chunklar(url: str, bank: str | None = None) -> list[dict]:
    """Bir URL'in TÜM chunk'ları (chunk_index sırasıyla), tarihleriyle.

    Chunk'lar zaten 8196 karakterlik %10 overlap'li pencerelerdir — burada
    ek bir bölme/kırpma YAPILMAZ."""
    _, client = _shared()
    kosullar = [_url_kosulu(url)]
    if bank:
        kosullar.append(models.FieldCondition(
            key="metadata.bank", match=models.MatchValue(value=bank)))
    try:
        pts, _ = client.scroll(collection_name=COLLECTION,
                               scroll_filter=models.Filter(must=kosullar),
                               limit=500, with_payload=True)
    except Exception as exc:
        log.warning("    chunk okunamadı (%s): %s", url[:60], exc)
        return []
    out = []
    for p in pts:
        m = (p.payload or {}).get("metadata", {}) or {}
        bas, bit, _st = _kanonik_tarih(m)
        out.append({"point_id": str(p.id), "idx": m.get("chunk_index", 0),
                    "metin": (p.payload or {}).get("page_content", "") or "",
                    "bas": bas, "bit": bit, "bank": m.get("bank", "")})
    out.sort(key=lambda x: x["idx"])
    return [c for c in out if c["metin"].strip()]


_DENETIM_Q = (
    "Sen bir KATILIM BANKASI veri denetçisisin. Aşağıdaki TABLO '{topic}' konusunu "
    "kıyaslıyor. Aşağıdaki PARÇA ise '{bank}' bankasının bir kaynak sayfasından geliyor "
    "({i}/{n}. parça).\n\n"
    "ÖNEMLİ KURALLAR:\n"
    "1. KATILIM BANKACILIĞI TERMİNOLOJİSİ: Katılım bankacılığı terminolojisine tam uyum sağlandığından emin ol.\n"
    "2. FORMAT VE KIYASLANABİLİRLİK: Tablo bir UI arayüzünde gösterilecektir. Değerler tek tip, net ve karşılaştırılabilir olsun (oran, tutar, vade, koşul).\n"
    "3. SADECE EKSİK/YANLIŞ BİLGİYİ BİLDİR: Tabloda bu banka için EKSİK veya YANLIŞ olan bir bilgi varsa bildir. Tablo zaten doğruysa eksikler listesini boş bırak.\n"
    "4. 'bilgi_var' alanı: bu parçada '{bank}' satırına ait GERÇEK bir bilgi geçiyorsa true yap.\n\n"
    "Tablo (mevcut durum):\n{tablo}\n\n"
    "Kaynak Parça:\n\"\"\"{parca}\"\"\"\n\n"
    'SADECE JSON: {{"bilgi_var": true|false, "eksikler": '
    '[{{"sutun": "<mevcut sütun adı>", "deger": "<yazılması gereken>", '
    '"gerekce": "<kısa>"}}], "yeni_sutun": '
    '[{{"sutun": "<yeni sütun adı>", "deger": "<değer>", "gerekce": "<kısa>"}}]}}'
)


def _denetle_kaynak(tablo: dict, url: str, bank: str) -> dict:
    """TEK bir kaynak URL'i için ajan: chunk'lara TEK TEK bakar.

    Her chunk BAĞIMSIZ değerlendirilir (öncekilerden habersiz) — bu bilinçli:
    chunk'lar zaten %10 overlap taşıdığı için sınıra denk gelen bilgi en az
    bir chunk'ta bütün görünür, ve bağımsızlık sayesinde tek bir uzun bağlam
    yerine küçük/net kararlar alınır."""
    chunklar = _chunklar(url, bank)
    if not chunklar:
        return {"url": url, "bank": bank, "eksikler": [], "yeni_sutun": [],
                "tarihler": [], "chunk": 0}
    tablo_metni = json.dumps(
        {"columns": tablo["columns"], "rows": tablo["rows"]}, ensure_ascii=False)
    eksikler: list[dict] = []
    yeni: list[dict] = []
    tarihler: list[tuple[str, str]] = []
    for i, c in enumerate(chunklar, 1):
        # STATİK şema (bilgi_var/eksikler/yeni_sutun) -> merdiven + feedback
        d = _cagir_json(_DENETIM_Q.format(
            topic=tablo.get("topic", ""), tablo=tablo_metni, bank=bank,
            i=i, n=len(chunklar), parca=c["metin"]), f"{bank}/{url[-40:]}#{i}",
            bekleyen=("bilgi_var", "eksikler"))
        if not d:
            continue
        for e in (d.get("eksikler") or []):
            if isinstance(e, dict) and e.get("sutun") and e.get("deger"):
                eksikler.append({**e, "bank": bank, "url": url,
                                  "point_id": c["point_id"]})
        for e in (d.get("yeni_sutun") or []):
            if isinstance(e, dict) and e.get("sutun") and e.get("deger"):
                yeni.append({**e, "bank": bank, "url": url,
                              "point_id": c["point_id"]})
        # TARİH ETİKETİ: bu parçada bu bankaya ait bilgi geçtiyse, parçanın
        # tarihi o satır için adaydır (kod tarafında kesiştirilecek).
        if d.get("bilgi_var") and (c["bas"] or c["bit"]):
            tarihler.append((c["bas"], c["bit"]))
    return {"url": url, "bank": bank, "eksikler": eksikler, "yeni_sutun": yeni,
            "tarihler": tarihler, "chunk": len(chunklar)}


# --- tarih kesişimi (KOD, LLM YOK) --------------------------------------------
def _kesistir(araliklar: list[tuple[str, str]]) -> tuple[str, str]:
    """Birden çok tarih aralığından EN DAR ortak aralığı döndürür.

    Kullanıcı kararı 2026-08-20: "2 farklı tarih varsa en dar aralığı seçersin,
    ikisinde ortak olan". Kesişim = en GEÇ başlangıç, en ERKEN bitiş."""
    bas = ""
    bit = ""
    for b, s in araliklar:
        if b and (not bas or b > bas):
            bas = b
        if s and (not bit or s < bit):
            bit = s
    if bas and bit and bas > bit:             # kesişim boş -> tek yanlı bırak
        return bas, ""
    return bas, bit


def _tarih_damgala(tablo: dict, raporlar: list[dict]) -> int:
    """Satır (banka) bazında geçerlilik damgası yazar — LLM'e sorulmaz."""
    banka_tarih: dict[str, list[tuple[str, str]]] = {}
    for r in raporlar:
        if r["tarihler"]:
            banka_tarih.setdefault(r["bank"], []).extend(r["tarihler"])
    from .tablo_tarih import bicimle, TARIH_SUTUNU_SONEKI
    sutun = "Geçerlilik"
    n = 0
    for bank, satir in (tablo.get("rows") or {}).items():
        if not isinstance(satir, dict):
            continue
        ar = banka_tarih.get(bank) or []
        satir[sutun] = bicimle(*_kesistir(ar)) if ar else bicimle("", "")
        n += 1
    if sutun not in tablo["columns"]:
        tablo["columns"].append(sutun)
    return n


_BIRLESTIR_Q = (
    "ÖNEMLİ — TERMİNOLOJİ: KATILIM BANKASI verileri; 'kredi'/'faiz' değil "
    "'finansman'/'kâr payı'/'kâr oranı' (istisna: 'kredi kartı').\n\n"
    "Aşağıda bir karşılaştırma tablosu ve onu kaynaklarına karşı denetleyen "
    "ajanların bulguları var. Bu bulguları işleyerek tabloyu SON HALİNE "
    "getir.\n\n"
    "TABLO (konu: {topic}):\n\"\"\"{tablo}\"\"\"\n\n"
    "DENETİM BULGULARI:\n\"\"\"{bulgular}\"\"\"\n\n"
    "Nasıl bir tablo istediğimiz: bankaların KIYASLANABİLDİĞİ, derli toplu bir "
    "tablo. Aynı şeyi ölçen sütunlar TEK sütunda birleşsin; yalnız tek bankada "
    "dolu kalan gereksiz sütunlar birikmesin. Bu kararları SEN ver — hangi "
    "sütunlar aynı şeydir, hangisi gerçekten ayırt edicidir, sen bilirsin.\n\n"
    "Tek katı kural: HİÇBİR BANKANIN BİLGİSİNİ KAYBETME. Sütun birleştirirken "
    "değerleri taşı, gerekirse '; ' ile birleştir. 'Geçerlilik' sütununa "
    "dokunma (onu kod üretir).\n\n"
    'SADECE JSON: {{"columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer>", ...}}}}}}'
)

def denetle_tablo(table_id: str, kuru: bool = False) -> dict | None:
    tablo = store.load_table(table_id)
    if tablo is None:
        return None
    # Tablodaki HER benzersiz (banka, url) çifti ayrı bir ajana gider.
    hedefler: dict[tuple[str, str], None] = {}
    for b, cc in (tablo.get("cell_sources") or {}).items():
        for v in cc.values():
            for s in v:
                if s.get("url"):
                    hedefler[(b, s["url"])] = None
    for b, l in (tablo.get("sources") or {}).items():
        for s in l:
            if s.get("url"):
                hedefler[(b, s["url"])] = None
    if not hedefler:
        log.info("[%s] kaynak yok, atlandı", table_id)
        return None

    log.info("[%s] %d kaynak -> %d ajan", table_id, len(hedefler), len(hedefler))
    raporlar: list[dict] = []
    with ThreadPoolExecutor(max_workers=DENETIM_WORKERS) as ex:
        futures = {ex.submit(_denetle_kaynak, tablo, u, b): (b, u)
                   for (b, u) in hedefler}
        for fut in as_completed(futures):
            b, u = futures[fut]
            try:
                raporlar.append(fut.result())
            except Exception as exc:          # bir kaynak patlarsa diğerleri sürsün
                log.warning("  [DENETİM HATASI] %s / %s: %s", b, u[:50], exc)

    eksik = sum(len(r["eksikler"]) for r in raporlar)
    yeni = sum(len(r["yeni_sutun"]) for r in raporlar)
    log.info("  %d chunk denetlendi, %d eksik, %d yeni sütun önerisi",
             sum(r["chunk"] for r in raporlar), eksik, yeni)
    if kuru:
        for r in raporlar:
            for e in r["eksikler"][:3]:
                log.info("    EKSİK %s/%s = %r", e["bank"], e["sutun"],
                         str(e["deger"])[:60])
        return {"id": table_id, "eksik": eksik, "yeni": yeni}

    # Birleştirme ajanı HER tabloda çalışır. Sütunların birleştirilip
    # birleştirilmeyeceğine, bir sütunun fazla seyrek olup olmadığına KOD
    # DEĞİL AJAN karar verir (kullanıcı kararı 2026-08-20) — burada eşik ya da
    # benzerlik kuralı YOKTUR.
    if True:
        bulgular = json.dumps(
            {"eksikler": [e for r in raporlar for e in r["eksikler"]],
             "yeni_sutun": [e for r in raporlar for e in r["yeni_sutun"]]},
            ensure_ascii=False)
        # DİNAMİK şema (columns/rows modelin kararı) -> alan beklentisi YOK,
        # yalnız "geçerli JSON" düzeyinde merdiven uygulanır.
        d = _cagir_json(_BIRLESTIR_Q.format(
            topic=tablo.get("topic", ""),
            tablo=json.dumps({"columns": tablo["columns"], "rows": tablo["rows"]},
                             ensure_ascii=False),
            bulgular=bulgular), f"birleştir({table_id[:32]})")
        if d and d.get("rows"):
            # Ajanın çıktısı OLDUĞU GİBİ kabul edilir. Kod tarafında "şu değer
            # kayboldu mu" diye metin karşılaştırması YAPILMAZ (kullanıcı kararı
            # 2026-08-20): agentic bir sistemde kural tabanlı düzeltme, ajanın
            # bilinçli birleştirme kararlarını da bozar. Tek kısıt: uydurma
            # banka anahtarı (model bazen sütun adını banka sanıyor) elenir.
            gecerli = set(tablo["rows"])
            tablo["rows"] = {b: v for b, v in d["rows"].items()
                             if b in gecerli and isinstance(v, dict)}
            tablo["columns"] = list(d.get("columns") or tablo["columns"])
        else:
            log.warning("  [BİRLEŞTİRME HATASI] %s: LLM ulaşılamadı, tablo "
                        "DEĞİŞMEDEN korunuyor", table_id)

    n = _tarih_damgala(tablo, raporlar)
    store.overwrite_table(table_id, tablo.get("docstring", ""), tablo["columns"],
                          tablo["rows"], tablo.get("sources", {}),
                          tablo.get("category", ""), tablo.get("subcategory", ""),
                          cell_sources=tablo.get("cell_sources"))
    log.info("  [TAMAM] %s: %d eksik işlendi, %d satır tarih damgalandı",
             table_id, eksik, n)
    return {"id": table_id, "eksik": eksik, "yeni": yeni, "tarih": n}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tablo", help="sadece bu tablo")
    ap.add_argument("--kuru", action="store_true", help="yazma, raporla")
    a = ap.parse_args()
    ids = [a.tablo] if a.tablo else [r["id"] for r in store.load_registry()]
    log.info("TOPLAM %d tablo denetlenecek (%d paralel)", len(ids), TABLO_WORKERS)
    # TABLOLAR PARALEL işlenir (kullanıcı kararı 2026-08-20): bir tablonun
    # kaynak ajanları zaten paralel; tabloları da paralel vermek sunucuyu
    # doyurur. Eşzamanlılığın ÜST SINIRI NET_SEM'dir (paylaşılan), buradaki
    # havuz yalnız kaç tablonun aynı anda AÇIK olacağını belirler.
    bitti = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=TABLO_WORKERS) as ex:
        futures = {ex.submit(denetle_tablo, tid, a.kuru): tid for tid in ids}
        for fut in as_completed(futures):
            tid = futures[fut]
            try:
                fut.result()
            except Exception as exc:          # bir tablo patlarsa diğerleri sürsün
                log.error("  [TABLO HATASI] %s: %s: %s", tid, type(exc).__name__, exc)
            with lock:
                bitti += 1
                log.info("[%d/%d bitti] %s", bitti, len(ids), tid)


if __name__ == "__main__":
    main()
