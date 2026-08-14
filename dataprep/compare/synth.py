"""Gemma karar/sentez adımları — hepsi zero-shot, genel ilke, JSON çıktı. Örnek/kural
yok; model kendi kararını verir.

  is_comparable   : bu sayfa kıyaslanabilir bir ürün/kampanya mı?
  match_table     : bu konu MEVCUT tablolardan biriyle mi eşleşiyor, yoksa yeni mi?
  fits_table      : eşleşme bulunduktan SONRA — gelen rapor gerçekten o tabloya mı
                    uyuyor (match_table yanılmış olabilir)?
  synthesize_table: banka-raporlarından (bank_agent.research_bank çıktıları) nihai
                    tabloyu (sütun+satır+docstring+kategori+alt-kategori) kurar.
"""
from __future__ import annotations

from dataprep import vlm

# Orkestratöre (sentez adımı) giden rapor payload'ı da büyüyebilir (10 banka x
# çok alan/kaynak). İçerik kararı değil, aynı server-context güvenliği: alanları
# makul boyuta kısalt, yine de büyükse kaynak notlarını at. "Ana agent" de
# context sıkıntısı çekmesin.
_MAX_ATTR_CHARS = 400
_MAX_NOTE_CHARS = 200
_MAX_PAYLOAD_CHARS = 200_000


def _compact_reports(reports: list[dict]) -> list[dict]:
    import copy
    reports = copy.deepcopy(reports)
    for r in reports:
        r["attributes"] = {k: (str(v)[:_MAX_ATTR_CHARS]) for k, v in (r.get("attributes") or {}).items()}
        for s in r.get("sources") or []:
            if s.get("note"):
                s["note"] = s["note"][:_MAX_NOTE_CHARS]
    import json as _json
    if len(_json.dumps(reports, ensure_ascii=False)) > _MAX_PAYLOAD_CHARS:
        for r in reports:                      # hâlâ büyükse kaynak notlarını tamamen at
            for s in r.get("sources") or []:
                s.pop("note", None)
    return reports

_COMPARABLE_Q = (
    "Bir KATILIM BANKASI sayfasına bakıyorsun. Soru: bu sayfa, BAŞKA bankalarla "
    "KIYASLANABİLİR SOMUT bir KAMPANYA ya da ÜRÜN mü tarif ediyor — yoksa genel/"
    "kurumsal/yardım/nav içerikli, kıyaslanamayan bir sayfa mı? Karar tamamen sana "
    "ait.\n\n"
    "Konuyu bu sayfanın ASIL ANLATTIĞI somut ürün/kampanya TÜRÜ düzeyinde tut — ne "
    "kategori şemsiyesi (ör. 'kredi kartı', 'mobil bankacılık' gibi bankanın "
    "onlarca farklı ürününü içine alan genel başlık) ne de sayfadaki dar bir "
    "alt-detay (bir sertifika şartı, tek bir ücret kalemi). ÖNEMLİ: topic bankanın "
    "kendi verdiği MARKA/ÜRÜN ADINI içermesin (ör. 'Sağlam Kart', 'Hadi Kart', "
    "'Paraf' gibi — bunlar banka-özel isimlerdir, başka bankada aynı isim "
    "aranmaz, her banka kendi markasını kullanır). Bunun yerine ürünün ne TÜRDE/"
    "hangi SEGMENTTE olduğunu tanımla (ör. 'gençlere özel kredi kartı', 'TROY "
    "altyapılı kredi kartı', 'ticari/iş amaçlı kredi kartı', belirli bir hesap "
    "türü, belirli bir kampanya konusu). Sayfa kendisi birçok farklı ürünü "
    "listeleyen bir MENÜ/genel-bakış sayfasıysa (tek bir ürünü değil, kategorinin "
    "tamamını anlatıyorsa) comparable=false de — "
    "o ürünlerin her biri kendi sayfasında ayrıca karşına çıkacak.\n\n"
    "URL: {url}\n\nMetin:\n\"\"\"{body}\"\"\"\n\n"
    'SADECE JSON: {{"comparable": true|false, "topic": "<bu sayfanın anlattığı '
    'SOMUT ürünün/kampanyanın adı, kısa — banka adı geçmesin>"}}')


def is_comparable(body: str, url: str = "") -> dict | None:
    d = vlm.call_json(vlm.txt_msg(_COMPARABLE_Q.format(url=url or "-", body=body[:8000])),
                       max_tokens=300)
    if not d:
        return None
    return {"comparable": bool(d.get("comparable")), "topic": (d.get("topic") or "").strip()}


_MATCH_Q = (
    "Elindeki bir konu var: {topic!r}. Aşağıda ŞU AN VAR OLAN karşılaştırma "
    "tablolarının listesi (id + kısa açıklama). Bu konu bunlardan BİRİYLE aynı şey "
    "mi (aynı ürün/kampanya ailesi, sadece başka bir bankadan görülmüş olabilir), "
    "yoksa GERÇEKTEN yeni bir konu mu? Kararını açıklamaların anlamına göre ver, "
    "kelime benzerliğine değil.\n\nMevcut tablolar:\n{tables}\n\n"
    'SADECE JSON: {{"match_id": "<eşleşen tablo id\'si ya da boş>"}}')


def match_table(topic: str, registry: list[dict]) -> str:
    """registry: [{"id","docstring"}...]. Dönen: eşleşen id ya da ''."""
    if not registry:
        return ""
    listing = "\n".join(f"- {r['id']}: {r['docstring']}" for r in registry)
    d = vlm.call_json(vlm.txt_msg(_MATCH_Q.format(topic=topic, tables=listing)), max_tokens=300)
    if not d:
        return ""
    mid = (d.get("match_id") or "").strip()
    return mid if any(r["id"] == mid for r in registry) else ""


_FITS_Q = (
    "Bir karşılaştırma tablosu var, konusu: {docstring!r}. Şimdi '{bank}' bankasından "
    "şu araştırma raporu geldi:\n\"\"\"{report}\"\"\"\n\n"
    "Bu rapor GERÇEKTEN bu tablonun konusuyla mı ilgili, yoksa aslında farklı bir "
    "ürün/kampanyadan mı bahsediyor (eşleştirme yanlış olmuş olabilir)?\n\n"
    'SADECE JSON: {{"fits": true|false}}')


def fits_table(table_docstring: str, bank: str, report: dict) -> bool:
    """match_table eşleşme bulduktan SONRA doğrulama — yanlış eşleşmeye karşı."""
    import json
    d = vlm.call_json(vlm.txt_msg(_FITS_Q.format(
        docstring=table_docstring, bank=bank,
        report=json.dumps(_compact_reports([report])[0], ensure_ascii=False))),
        max_tokens=200)
    if not d:
        return True                     # LLM ulaşılamadı -> muhafazakâr, tabloya güven
    return bool(d.get("fits", True))


_SYNTH_Q = (
    "Aşağıda '{topic}' konusunda, katılım bankalarından toplanan araştırma raporları "
    "var (her biri bir bankanın kendi verisinden). Bunlardan bir KARŞILAŞTIRMA TABLOSU "
    "kur:\n"
    "- SÜTUNLARI SEN belirle: raporlardaki attributes alanlarını anlamına göre "
    "birleştir/normalize et (aynı şeyi farklı isimle söyleyenler tek sütun olsun), "
    "en kapsamlı ve karşılaştırmaya en çok yarayacak alanları seç.\n"
    "- offers=false olan bankalar tabloda 'sunulmuyor' değeriyle yer alır (gizlenmez).\n"
    "- Kısa bir docstring açıklaması yaz: bu tablo neyi kıyaslıyor (1-2 cümle, "
    "ileride başka bir sayfanın bu tabloyla mı eşleştiğine karar verirken kullanılacak).\n"
    "- Bu tabloya bir ANA KATEGORİ ata: 'kampanya' ya da 'ürün' (hangisiyse). Bir de "
    "bir ALT KATEGORİ ata (UI'da filtrelemek için) — daha önce kullanılmış alt "
    "kategoriler: {subcats}. Bunlardan biri gerçekten uyuyorsa AYNI ismi kullan "
    "(tutarlılık için), uymuyorsa yeni kısa bir alt kategori adı üret.\n\n"
    "Raporlar:\n\"\"\"{reports}\"\"\"\n\n"
    'SADECE JSON: {{"docstring": "<1-2 cümle>", "category": "kampanya"|"ürün", '
    '"subcategory": "<kısa alt kategori>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer ya da sunulmuyor>", ...}}}}}}')


def synthesize_table(topic: str, reports: list[dict], subcats: list[str] | None = None) -> dict | None:
    import json
    payload = json.dumps(_compact_reports(reports), ensure_ascii=False)
    subcat_list = ", ".join(subcats or []) or "(henüz yok)"
    d = vlm.call_json(vlm.txt_msg(_SYNTH_Q.format(topic=topic, reports=payload, subcats=subcat_list)),
                       max_tokens=4096)
    if not d:
        return None
    return {"docstring": (d.get("docstring") or "").strip(),
            "category": (d.get("category") or "").strip(),
            "subcategory": (d.get("subcategory") or "").strip(),
            "columns": d.get("columns") or [],
            "rows": d.get("rows") or {}}
