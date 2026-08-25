"""PDF -> metin/GÖRSEL çıkarımı — HİBRİT (kural/eşik YOK), SAYFA-SIRASI korunur.

Kararlar:
  * HİBRİT, kural tabanı yok: metin katmanı VARSA sayfa "web sayfası gibi" işlenir —
    metin blokları text-layer'dan AYNEN alınır, görsel item'lar VLM ile incelenir
    (dekoratif eleme + içerik çıkarımı) ve hepsi SAYFA SIRASINA (yukarıdan aşağı)
    göre birleştirilir: görsel üstte metin altta ise md'de de öyle olur. Metin 1
    harf bile olsa alınır; kalan görsel item'lar HER ZAMAN ayrıca incelenir.
  * Metin katmanı YOKSA (taranmış PDF) -> YAZILIM-ZOOM bant-VLM: en küçük font
    ölçülür (yoksa küçük-font varsayımı), en küçük yazı >= ~28px olacak zoom seçilir,
    yükseklik overlap'li BANT'lara bölünür, overlap-stitch ile birleşir. VLM'e
    "okuyabiliyor musun" SORULMAZ (güvenilmez; halüsinasyon yapıyor).
  * GÖRSEL dedup: aynı gömülü/sayfa görseli ImageCache (sha256) ile YALNIZCA BİR KEZ
    VLM'e sorulur. RELEVANCE/SKIP YOK — her PDF tam işlenir.
  * TÜM VLM çağrıları STRUCTURED JSON (response_format=json_object); istek hatası ->
    5-retry; JSON parse olmazsa -> sıcaklık merdiveni 0..1.0.

Çıktı: <bank>_site/pdf_text/<pdf>.md (temiz metin) + <pdf>.json (sayfa-segmentli).
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import pymupdf

from dataprep import vlm
from dataprep.ledger import Ledger
from dataprep.vlm import ImageCache

log = logging.getLogger("dataprep.pdf.extract")

BASE_URL = "http://127.0.0.1:9000"
VLM_PATH = "/gemma/v1/chat/completions"
VLM_MODEL = "google/gemma-4-31B-it"

MIN_TEXT_PX = 28         # en küçük yazının hedef px'i (okunur eşiği)
MAX_W_PX = 2600          # VLM'e giden görüntünün en fazla genişliği
BAND_H_PX = 1600         # dikey bant yüksekliği (yükseklik overlap'li bölünür)
OVERLAP_PX = 200         # bantlar arası görüntü overlap'i
Z_MIN, Z_MAX = 2.0, 5.0  # render zoom sınırları
DEFAULT_FONT_PT = 6.0    # taranmış PDF (text-layer yok) için küçük-font varsayımı
ANCHOR_CHARS = 800       # stitch anchor: önceki metnin son N karakteri
TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)


# --- VLM çağrısı (structured JSON + retry + sıcaklık merdiveni) -------------
def _img_msg(text: str, png: bytes) -> list:
    return vlm.img_msg(text, png)               # 413'ü önlemek için ortak fit'i kullan


def _post(payload, retries=None) -> str:
    """Ortak HAVUZLU client + ısrarlı retry (vlm._post): üstel backoff, 10 dk sonra vazgeç."""
    return vlm._post(payload)


def _call_json(messages, max_tokens=4096) -> dict:
    """STRUCTURED JSON çağrısı. Parse olmazsa sıcaklığı 0->0.3->0.6->1.0 artırır."""
    for t in TEMP_LADDER:
        raw = _post({"model": VLM_MODEL, "messages": messages, "temperature": t,
                     "max_tokens": max_tokens, "response_format": {"type": "json_object"}})
        if not raw:                       # bağlantı çöktü -> sıcaklık merdivenini deneme, çık
            break
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            import re
            mm = re.search(r"\{.*\}", raw, re.S)
            if mm:
                try:
                    return json.loads(mm.group(0))
                except json.JSONDecodeError:
                    pass
        log.info("    (JSON parse edilemedi, sıcaklık %.1f ile tekrar)", t)
    return {}


# --- YAZILIM ZOOM: font boyutundan render zoom'u -----------------------------
def _min_font(page) -> float | None:
    sizes = [s["size"] for b in page.get_text("dict")["blocks"]
             for l in b.get("lines", []) for s in l.get("spans", []) if s["text"].strip()]
    return min(sizes) if sizes else None


def _page_zoom(page) -> float:
    """En küçük yazı >= MIN_TEXT_PX olacak zoom; genişlik MAX_W_PX ile sınırlı."""
    W = page.rect.width or 1
    mf = _min_font(page) or DEFAULT_FONT_PT
    z = MIN_TEXT_PX / mf
    z = min(z, MAX_W_PX / W)                 # görüntü çok geniş olmasın
    return max(Z_MIN, min(z, Z_MAX))


def _render_bands(page, zoom: float) -> list[bytes]:
    """Sayfayı zoom'la render et, yüksekliği overlap'li BANT'lara böl (tam genişlik)."""
    from PIL import Image
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    H = img.height
    bands, top = [], 0
    if H <= BAND_H_PX:
        boxes = [(0, H)]
    else:
        boxes = []
        while top < H:
            bottom = min(top + BAND_H_PX, H)
            boxes.append((top, bottom))
            if bottom >= H:
                break
            top = bottom - OVERLAP_PX
    for (t, b) in boxes:
        import io
        buf = io.BytesIO()
        img.crop((0, t, img.width, b)).save(buf, format="PNG")
        bands.append(buf.getvalue())
    return bands


# --- EXTRACT (structured JSON) + overlap-stitch ----------------------------
# NOT: PDF relevance/SKIP YOK — risk almıyoruz, HER PDF tam extract edilir.
# İlgili olup olmadığına sonraki aşama (embedding/kullanım) karar verir.
_EXTRACT_FIRST = ('Bu KATILIM BANKASI PDF görüntüsündeki TÜM metni eksiksiz ve AYNEN çıkar. '
                  'Tabloları markdown tablosu yap, görsel düzeni koru. '
                  'SADECE şu JSON ile yanıt ver: {"content": "<markdown metin>"}')
_EXTRACT_CONT = ('Bu görüntü önceki bölümle OVERLAP\'lidir. Önceki metnin sonu:\n'
                 '"""{tail}"""\n'
                 'Overlap kısmını TEKRARLAMA; sadece o noktadan SONRASINI eksiksiz çıkar. '
                 'Tabloları markdown yap. SADECE JSON: {{"content": "<devam metni>"}}')


MIN_IMG_PX = 100         # bu boyutun altındaki gömülü görsel ikon/dekoratif -> atla


def _norm_png(data: bytes | None) -> bytes | None:
    """Gömülü görsel ham baytını PNG'ye normalize et (dedup hash tutarlı); küçükse None."""
    if not data:
        return None
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    if im.width < MIN_IMG_PX or im.height < MIN_IMG_PX:
        return None
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _bands_vlm(page, full: str, segments: list, pno: int) -> str:
    """Metin katmanı OLMAYAN (taranmış) sayfa: yazılım-zoom bant -> JSON extract -> stitch."""
    for band in _render_bands(page, _page_zoom(page)):
        if not full:
            d = _call_json(_img_msg(_EXTRACT_FIRST, band), max_tokens=8192)
        else:
            tail = full if len(full) <= ANCHOR_CHARS else full[-ANCHOR_CHARS:]
            d = _call_json(_img_msg(_EXTRACT_CONT.format(tail=tail), band), max_tokens=8192)
        txt = (d.get("content") or "").strip()
        if txt:
            full += ("\n" + txt) if full else txt
            segments.append({"page": pno, "text": txt, "mode": "vlm"})
    return full


def _page_ordered(page, cache) -> str | None:
    """Sayfayı web sayfası gibi işle: metin ve GÖRSEL bloklarını SAYFA SIRASINA
    (yukarıdan aşağı) göre birleştir. Görseller VLM ile incelenir (dekoratif eleme +
    içerik çıkarımı), metin blokları text-layer'dan AYNEN alınır. Sayfada hiç metin
    yoksa None döner (taranmış -> render-VLM'e bırakılır). KURAL/EŞİK YOK: metin
    varsa (1 harf bile) alınır, ayrıca kalan görsel item'lar da her zaman incelenir."""
    d = page.get_text("dict")
    items: list[tuple[float, float, str]] = []   # (y0, x0, markdown)
    has_text = False
    for b in d.get("blocks", []):
        y0, x0 = b["bbox"][1], b["bbox"][0]
        if b.get("type") == 0:                   # METİN bloğu -> aynen
            txt = "".join(s["text"] for l in b.get("lines", []) for s in l.get("spans", [])).strip()
            if txt:
                has_text = True
                items.append((y0, x0, txt))
        elif b.get("type") == 1 and cache is not None:   # GÖRSEL bloğu -> VLM (dedup)
            png = _norm_png(b.get("image"))
            if png:
                res = cache.examine(png)
                if res and not res.get("decorative") and res.get("content"):
                    items.append((y0, x0, res["content"]))
    if not has_text:
        return None                              # metin katmanı yok -> taranmış
    items.sort(key=lambda it: (round(it[0], 1), it[1]))   # sayfa sırası: y sonra x
    return "\n\n".join(t for _, _, t in items).strip()


def extract_pdf(doc, cache=None) -> list[dict]:
    """HİBRİT (kural/eşik YOK): metin katmanı varsa sayfa 'web sayfası gibi' işlenir —
    metin ve görsel item'lar SAYFA SIRASINA göre birleştirilir (görsel üstteyse md'de
    de üstte). Metin katmanı yoksa (taranmış) -> yazılım-zoom bant-VLM.
    Segment: [{page, text, mode}] ('text' = sıralı-hibrit, 'vlm' = render)."""
    segments: list[dict] = []
    full = ""
    for p in range(doc.page_count):
        page = doc[p]
        seg = _page_ordered(page, cache)
        if seg is not None:
            segments.append({"page": p + 1, "text": seg, "mode": "text"})
            full += ("\n" + seg) if full else seg
        else:
            full = _bands_vlm(page, full, segments, p + 1)   # taranmış sayfa
    return segments


# --- RELEVANCE: Gemma ile "related mı" (web triage prompt'una benzer) --------
# Web sayfası triage'ıyla AYNI amaç/felsefe; modalite içeriğe göre: metin varsa
# metinden, görsel ağırlıklıysa ilk sayfanın KALİTELİ ekran görüntüsünden sorulur.
_REL_GOAL = (
    "Bir KATILIM BANKASININ ürün ve kampanyalarıyla ilgili HER TÜRLÜ bilgiyi, chatbot "
    "ve dashboard'da müşteriye gösterilebilecek şekilde topluyoruz. Kurumsal, prosedür, "
    "mevzuat, icazet belgesi, imzalı sözleşme/teminat gibi müşterinin doğrudan ürün ya da "
    "ürün bilgisi olarak alamayacağı içeriklerle İLGİLENMİYORUZ.\n\n")
_REL_ASK = ('Yukarıda bu PDF\'ten RASTGELE seçilmiş sayfalar (metin ya da görüntü) ve '
            'PDF adı + bulunduğu bölüm verildi. Buna göre: bu PDF müşteriye '
            'gösterilecek ürün/kampanya bilgisi mi? SADECE JSON: '
            '{"related": true|false, "reason": "kısa gerekçe"}')
_REL_TEXT_MIN = 100        # sayfada bu kadar metin varsa metin, yoksa görüntü ver
_REL_IMG_BUDGET = 300_000  # relevance mesajındaki görsellerin TOPLAM byte tavanı. DÜŞÜK:
                           # base64 ~1.34x şişirir + metin -> toplam body 1MB'ı aşmasın (413).


def _rel_sample(n_pages: int) -> list[int]:
    """Sayfa sayısına göre RASTGELE örnek sayfa indeksleri (relevance için)."""
    import random
    if n_pages <= 3:
        return list(range(n_pages))
    k = 3 if n_pages <= 10 else 5
    return sorted(random.sample(range(n_pages), k))


def _rel_screenshot(page, max_w: int = 1000) -> bytes | None:
    """Relevance için orta-çözünürlüklü ekran görüntüsü (belge türünü tanımak yeter)."""
    import io
    from PIL import Image
    z = min(2.0, max_w / (page.rect.width or 1))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def relevance(pdf_name: str, parent: str, doc) -> dict:
    """RELEVANCE GATE: metadata (pdf adı + parent) + RASTGELE seçilmiş sayfalar (text
    varsa metin, yoksa görüntü) tek bir mesajda Gemma'ya verilir; 'related mı' sorulur.
    LLM yoksa GÜVENLİ varsayılan related=True (kaybetme; sonra tekrar sorulur)."""
    parts = [vlm.txt_block(
        _REL_GOAL + f"PDF adı: {pdf_name}\nBulunduğu bölüm (parent): {parent or '-'}\n\n"
        "Aşağıda bu PDF'ten RASTGELE seçilmiş sayfalar var:")]
    budget = _REL_IMG_BUDGET
    for p in _rel_sample(doc.page_count):
        page = doc[p]
        txt = (page.get_text("text") or "").strip()
        if len(txt) >= _REL_TEXT_MIN:                      # metin varsa metin
            parts.append(vlm.txt_block(f"\n[Sayfa {p + 1} — metin]\n{txt[:4000]}"))
        elif budget > 0:                                   # yoksa görüntü (bütçe dahilinde)
            try:
                png = _rel_screenshot(page)
            except Exception:
                continue
            blk, size = vlm.img_block(png)
            if size > budget:                              # taşacaksa EKLEME (413 önle)
                continue
            parts.append(vlm.txt_block(f"\n[Sayfa {p + 1} — görüntü]"))
            parts.append(blk)
            budget -= size
    parts.append(vlm.txt_block("\n\n" + _REL_ASK))
    d = vlm.call_json([{"role": "user", "content": parts}], max_tokens=256)
    if not d:
        return {"relevant": True, "reason": "LLM yanıt yok (güvenli varsayılan: tut)"}
    return {"relevant": bool(d.get("related", d.get("relevant", True))),
            "reason": str(d.get("reason", ""))[:200]}


# --- tek PDF işleme + çıktı ------------------------------------------------
def process(pdf_path: str, meta: dict | None = None, cache=None,
            relevance_check: bool = True) -> dict:
    """RELEVANCE GATE önce: rastgele sayfa örneğiyle related mı sorulur. related DEĞİLSE
    tam çıkarım YAPILMAZ (relevant:false TAG'lenir, kayıt kalır). related İSE düzenli
    tam çıkarım (metin + görsel-VLM) yapılır. cache: gömülü görsel dedup."""
    meta = dict(meta or {})
    doc = pymupdf.open(pdf_path)
    n = doc.page_count
    if relevance_check:
        rel = relevance(Path(pdf_path).name,
                        meta.get("parent") or meta.get("source_page", ""), doc)
        meta["relevant"] = rel["relevant"]
        meta["relevance_reason"] = rel["reason"]
        if not rel["relevant"]:                  # unrelated -> tam çıkarım yok (TAG'li kayıt)
            doc.close()
            return {"pdf": pdf_path, "skipped": True, "pages": n,
                    "segments": [], "text": "", "meta": meta}
    segments = extract_pdf(doc, cache=cache)     # related -> düzenli tam çıkarım
    doc.close()
    text = "\n".join(s["text"] for s in segments).strip()
    return {"pdf": pdf_path, "skipped": False, "pages": n,
            "segments": segments, "text": text, "meta": meta}


def write_output(result: dict, out_dir) -> None:
    """pdf_text/<stem>.md (temiz metin) + <stem>.json (sayfa-segmentli metadata)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(result["pdf"]).stem
    m = result.get("meta", {})
    fm = ["---",
          f'pdf_url: "{m.get("pdf_url", "")}"',
          f'source_page: "{m.get("source_page", "")}"',
          f'parent: "{m.get("parent", "")}"',
          f'bank: "{m.get("bank", "")}"',
          "type: pdf_text",
          f'page_count: {result["pages"]}']
    if m.get("campaign_start"):
        fm.append(f'campaign_start: "{m.get("campaign_start")}"')
    if m.get("campaign_end"):
        fm.append(f'campaign_end: "{m.get("campaign_end")}"')
    if "relevant" in m:                        # Gemma relevance TAG'i (silme yok, işaretle)
        fm.append(f'relevant: {str(m.get("relevant")).lower()}')
        fm.append(f'relevance_reason: "{str(m.get("relevance_reason", "")).replace(chr(34), "")}"')
    fm += ["---", ""]
    (out_dir / f"{stem}.md").write_text("\n".join(fm) + result["text"] + "\n", encoding="utf-8")
    payload = {**m, "pdf_file": result["pdf"], "page_count": result["pages"],
               "segments": result["segments"]}
    (out_dir / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# --- banka toplu işleme (banka-içi SERİ; paralel dış shell'de) --------------
def _md_is_empty(md_path: Path) -> bool:
    """pdf_text/<stem>.md yok ya da gövdesi (frontmatter sonrası) neredeyse boş mu?
    Taranmış PDF'ler VLM down iken boş yazılmış olabilir -> --redo-empty ile yeniden denenir."""
    if not md_path.exists():
        return True
    try:
        body = md_path.read_text(encoding="utf-8").split("---", 2)[-1].strip()
    except Exception:
        return True
    return len(body) < 5


def process_bank(slug: str, overwrite: bool = False, examine_images: bool = True,
                 redo_empty: bool = False, relevance_check: bool = True) -> None:
    """Bir bankanın TÜM PDF/doc'larını sırayla işler; görsel dedup için tek
    ImageCache paylaşılır. Çıktı pdfs/ ağacını pdf_text/ altında AYNEN yansıtır.
    examine_images=False -> gömülü görselleri VLM'e SOKMA (yalnız metin + taranmış-render);
    flaky sunucuda metin kapsamasını hızla tamamlamak için. Sonra --overwrite + True ile
    gömülü görseller eklenebilir.
    redo_empty=True -> yalnızca .md'si eksik/boş olan PDF'leri yeniden dener (taranmış-VLM
    kurtarma; sunucu sağlıklıyken)."""
    site = Path(__file__).resolve().parents[2] / "data" / f"{slug}_site"
    cat_path = site / "_catalog.json"
    if not cat_path.exists():
        log.warning("%s: katalog yok, atlanıyor", slug)
        return
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    cache = ImageCache(site / "_pdf_image_cache.json") if examine_images else None
    ledger = Ledger(site / "_processing_log.jsonl")
    recs = [(u, v) for u, v in cat.items()
            if v.get("kind") in ("pdf", "doc") and v.get("path", "").endswith(".pdf")]
    log.info("%s: %d PDF işlenecek", slug, len(recs))
    for i, (url, rec) in enumerate(recs, 1):
        pdf_path = site / rec["path"]
        if not pdf_path.exists():
            log.warning("  %s: dosya yok: %s", slug, rec["path"])
            ledger.record("pdf", url, status="MISSING", decision="skip", reason="dosya yok")
            continue
        rel = Path(rec["path"]).relative_to("pdfs") if rec["path"].startswith("pdfs/") \
            else Path(rec["path"]).name
        out_dir = site / "pdf_text" / Path(rel).parent
        md_path = out_dir / f"{pdf_path.stem}.md"
        if redo_empty:                       # sadece eksik/boş .md'leri yeniden dene
            if not _md_is_empty(md_path):
                continue
        elif not overwrite and md_path.exists():
            ledger.record("pdf", url, decision="skip_exists", reason="zaten işlenmiş")
            continue
        meta = {"pdf_url": url, "source_page": rec.get("source_page", ""),
                "parent": rec.get("parent", ""), "bank": slug}
        try:
            res = process(str(pdf_path), meta=meta, cache=cache,
                          relevance_check=relevance_check)
            if res.get("text") and res["meta"].get("relevant") is not False:
                from dataprep import pages   # LLM-friendly yeniden yaz (sayı/tablo AYNEN)
                cl, dts = pages.clean_page(res["text"], meta.get("pdf_url", ""))
                if cl is not None:
                    res["text"] = cl
                # kampanya tarihi PDF frontmatter'ına (chunk'lara yayılacak).
                if dts.get("start"):
                    res["meta"]["campaign_start"] = dts["start"]
                if dts.get("end"):
                    res["meta"]["campaign_end"] = dts["end"]
            write_output(res, out_dir)
            m = res.get("meta", {})
            if m.get("relevant") is False:            # gereksiz -> alınmaz (TAG'li)
                dec = "unrelated"
            elif res.get("skipped"):
                dec = "unrelated"
            elif not res.get("text"):
                dec = "empty"                         # taranmış/çıkarılamadı
            else:
                dec = "related_extracted"             # işlendi
            ledger.record("pdf", url, decision=dec, reason=m.get("relevance_reason", ""),
                          pages=res.get("pages", 0), chars=len(res.get("text", "")))
        except Exception as exc:
            log.warning("  %s: PDF hata %s: %s", slug, rec["path"], exc)
            ledger.record("pdf", url, status="ERROR", decision="error", reason=str(exc))
        if i % 5 == 0:
            if cache is not None:
                cache.save()
            log.info("  %s: %d/%d PDF işlendi", slug, i, len(recs))
    if cache is not None:
        cache.save()
    log.info("%s BİTTİ: cache=%d benzersiz görsel", slug, len(cache.data) if cache else 0)


def main():
    import argparse
    import glob as _glob
    import os
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="PDF -> pdf_text/ (hibrit, banka-içi seri)")
    ap.add_argument("banks", nargs="*")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--text-only", action="store_true",
                    help="gömülü görselleri VLM'e sokma (yalnız metin + taranmış-render)")
    ap.add_argument("--no-relevance", action="store_true",
                    help="Gemma relevance TAG'ini atla (sunucu yoksa / saf metin çıkarımı)")
    ap.add_argument("--redo-empty", action="store_true",
                    help="yalnızca eksik/boş .md'leri yeniden dene (taranmış-VLM kurtarma)")
    args = ap.parse_args()
    banks = args.banks or sorted(
        os.path.basename(d)[:-5]
        for d in _glob.glob(str(Path(__file__).resolve().parents[2] / "data" / "*_site")))
    for b in banks:
        process_bank(b, overwrite=args.overwrite, examine_images=not args.text_only,
                     redo_empty=args.redo_empty, relevance_check=not args.no_relevance)


if __name__ == "__main__":
    main()
