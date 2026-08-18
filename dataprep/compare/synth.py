"""Gemma karar/sentez adımları — hepsi zero-shot, genel ilke, JSON çıktı. Örnek/kural
yok; model kendi kararını verir.

  is_comparable   : bu sayfa kıyaslanabilir bir ürün/kampanya mı?
  match_table     : bu konu MEVCUT tablolardan biriyle mi eşleşiyor, yoksa yeni mi?
  fits_table      : eşleşme bulunduktan SONRA — gelen rapor gerçekten o tabloya mı
                    uyuyor (match_table yanılmış olabilir)?
  synthesize_table: banka-raporlarından (bank_agent.research_bank çıktıları) nihai
                    tabloyu (sütun+satır+docstring+kategori+alt-kategori) kurar.
  merge_tables    : mükerrer bulunan İKİ tabloyu tek tabloda birleştirir (gerekirse
                    ek sütun ekleyerek) — dedup.py bakım ajanı tarafından kullanılır.
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


def _format_subcats(subcats: dict[str, list[str]] | list[str] | None) -> str:
    """Alt kategorileri LLM'e BAĞLAMLI göster: sadece isim değil, altındaki
    örnek tabloların ne olduğu da (docstring) — isim benzerliğine değil,
    GERÇEK İÇERİK uyumuna dayalı bir karar verebilsin diye. Eski çağıranlar
    hâlâ düz bir isim listesi (list[str]) verebilir; o durumda örneksiz gösterilir."""
    if not subcats:
        return "(henüz yok)"
    if isinstance(subcats, list):
        return ", ".join(subcats)
    lines = []
    for sub, examples in subcats.items():
        ex = " | ".join(e for e in examples if e) or "(örnek yok)"
        lines.append(f"- {sub}: {ex}")
    return "\n".join(lines)

# classify_page (kıyaslanabilir mi + mevcut tablo havuzunda eşleşen var mı) artık
# BURADA değil, classify_agent.py'de — tablo havuzu büyüdükçe (yüzlerce olabilir)
# TÜMÜNÜ tek prompt'a sığdırmak mümkün değil; o yüzden sabit-listeli tek çağrı
# yerine, search_tables (embedding bazlı arama) aracına sahip bir Gemma ajanı var.


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
    "ÖNEMLİ — TERMİNOLOJİ: bunlar KATILIM BANKASI (faizsiz/İslami bankacılık) "
    "verileri. ÜRETECEĞİN HER METİNDE (docstring, sütun adları, alt kategori, "
    "değerler) 'kredi'/'faiz' değil 'finansman'/'kâr payı'/'kâr oranı' kullan; "
    "raporda konvansiyonel terim geçse bile SEN katılım bankacılığı terimine "
    "çevir. Tek istisna: 'kredi kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu "
    "gibi kalır.\n\n"
    "Aşağıda '{topic}' konusunda, katılım bankalarından toplanan araştırma raporları "
    "var (her biri bir bankanın kendi verisinden). Bunlardan bir KARŞILAŞTIRMA TABLOSU "
    "kur:\n"
    "- SÜTUNLARI SEN belirle: raporlardaki attributes alanlarını anlamına göre "
    "birleştir/normalize et (aynı şeyi farklı isimle söyleyenler tek sütun olsun), "
    "en kapsamlı ve karşılaştırmaya en çok yarayacak alanları seç.\n"
    "- offers=false olan bankalar tabloda 'sunulmuyor' değeriyle yer alır (gizlenmez).\n"
    "- Kısa bir docstring açıklaması yaz: bu tablo neyi kıyaslıyor (1-2 cümle). "
    "Bu docstring İLERİDE EMBEDDING'E ÇIKARILIP ANLAM BAZLI ARAMADA kullanılacak — "
    "başka bir sayfa aynı ürün/kampanyayla mı eşleşiyor diye bu metin üzerinden "
    "karşılaştırılacak. Bu yüzden: (1) bu ürünün/kampanyanın NE OLDUĞUNU somut ve "
    "KENDİNE ÖZGÜ kelimelerle anlat — 'katılım bankalarının sunduğu X ürünlerinin "
    "karşılaştırmasıdır' gibi diğer tablolarla ORTAK, kalıplaşmış bir çerçeve "
    "cümleye boğma; ayırt edici olan NE (ürünün/kampanyanın türü, konusu, kapsamı) "
    "cümlenin başında ve belirgin olsun. (2) Aynı zamanda AÇIKLAYICI kal — okuyan "
    "birinin bu tablonun ne olduğunu tam anlaması gerekiyor, sadece anahtar "
    "kelime listesi olmasın. Hem özgün/ayırt edici hem açıklayıcı olan bir "
    "denge kur.\n"
    "- Bu tabloya bir ANA KATEGORİ ata: 'kampanya' ya da 'ürün' (hangisiyse). Bir de "
    "bir ALT KATEGORİ ata (UI'da filtrelemek için). Aşağıda MEVCUT alt kategoriler "
    "VE her birinin altında GERÇEKTEN ne tür tablolar olduğunu gösteren örnek "
    "docstring'ler var — SADECE alt kategori ADINA bakıp karar VERME; altındaki "
    "örnek tabloların bu yeni tabloyla GERÇEKTEN AYNI ürün/kampanya TÜRÜNDE olup "
    "olmadığını oku. İsim olarak yakın görünse de İÇERİK farklıysa (örn. bir "
    "'hesap' ürünüyle bir 'teminatlı finansman' ürünü, isim benzese de FARKLI "
    "ürün tipleridir) AYNI alt kategoriyi kullanma. Gerçekten aynı türdeyse AYNI "
    "ismi kullan (tutarlılık için); hiçbiri uymuyorsa yeni kısa bir alt kategori "
    "adı üret.\n\n"
    "Mevcut alt kategoriler ve örnekleri:\n\"\"\"{subcats}\"\"\"\n\n"
    "Raporlar:\n\"\"\"{reports}\"\"\"\n\n"
    'SADECE JSON: {{"docstring": "<1-2 cümle>", "category": "kampanya"|"ürün", '
    '"subcategory": "<kısa alt kategori>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer ya da sunulmuyor>", ...}}}}}}')


def synthesize_table(topic: str, reports: list[dict],
                      subcats: dict[str, list[str]] | list[str] | None = None) -> dict | None:
    import json
    payload = json.dumps(_compact_reports(reports), ensure_ascii=False)
    d = vlm.call_json(vlm.txt_msg(_SYNTH_Q.format(
        topic=topic, reports=payload, subcats=_format_subcats(subcats))),
        max_tokens=4096)
    if not d:
        return None
    return {"docstring": (d.get("docstring") or "").strip(),
            "category": (d.get("category") or "").strip(),
            "subcategory": (d.get("subcategory") or "").strip(),
            "columns": d.get("columns") or [],
            "rows": d.get("rows") or {}}


_MERGE_Q = (
    "ÖNEMLİ — TERMİNOLOJİ: bunlar KATILIM BANKASI (faizsiz/İslami bankacılık) "
    "verileri. ÜRETECEĞİN HER METİNDE 'kredi'/'faiz' değil 'finansman'/'kâr "
    "payı'/'kâr oranı' kullan. Tek istisna: 'kredi kartı' yerleşik bir ÜRÜN ADI "
    "olduğu için olduğu gibi kalır.\n\n"
    "İki karşılaştırma tablosu var, aynı ürün/kampanya TÜRÜNÜ kıyasladıkları "
    "için birleştirilmeleri gerekiyor. Bunları TEK bir tabloda birleştir:\n"
    "- SÜTUNLARI SEN belirle: iki tablonun sütunlarını anlamına göre birleştir/"
    "normalize et (aynı şeyi ifade edenler tek sütun olsun). Eğer iki tablo "
    "GERÇEKTEN farklı bir açıdan bakıyorsa (biri bir yöne, diğeri başka bir "
    "yöne odaklanmışsa) o farkı temsil eden EK bir sütun ekle — hiçbir bilginin "
    "kaybolmaması esas.\n"
    "- Her banka için: o banka HER İKİ tabloda da satır sahibiyse bilgilerini "
    "birleştir (çelişen değerler varsa daha somut/detaylı olanı tercih et); "
    "sadece bir tabloda satırı varsa aynen koru.\n"
    "- Kısa bir docstring yaz: bu birleşik tablo neyi kıyaslıyor (1-2 cümle). "
    "Bu docstring ileride EMBEDDING'E ÇIKARILIP anlam bazlı aramada kullanılacak "
    "— kalıplaşmış/diğer tablolarla ortak bir çerçeve cümleye boğma, ayırt edici "
    "olan NE cümlenin başında ve belirgin olsun; aynı zamanda açıklayıcı kal.\n"
    "- Bir ANA KATEGORİ ata: 'kampanya' ya da 'ürün'. Bir ALT KATEGORİ ata. "
    "Aşağıda MEVCUT alt kategoriler VE her birinin altında GERÇEKTEN ne tür "
    "tablolar olduğunu gösteren örnek docstring'ler var — SADECE alt kategori "
    "ADINA bakıp karar VERME; altındaki örnek tabloların bu birleşik tabloyla "
    "GERÇEKTEN AYNI ürün/kampanya TÜRÜNDE olup olmadığını oku. İsim yakın "
    "görünse de İÇERİK farklıysa AYNI alt kategoriyi kullanma. Gerçekten aynı "
    "türdeyse AYNI ismi kullan; hiçbiri uymuyorsa yeni kısa bir ad üret.\n\n"
    "Mevcut alt kategoriler ve örnekleri:\n\"\"\"{subcats}\"\"\"\n\n"
    "Tablo A ({a_id}) — {a_docstring}:\n\"\"\"{a}\"\"\"\n\n"
    "Tablo B ({b_id}) — {b_docstring}:\n\"\"\"{b}\"\"\"\n\n"
    'SADECE JSON: {{"docstring": "<1-2 cümle>", "category": "kampanya"|"ürün", '
    '"subcategory": "<kısa alt kategori>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer ya da sunulmuyor>", ...}}}}}}')


def merge_tables(a: dict, b: dict,
                  subcats: dict[str, list[str]] | list[str] | None = None) -> dict | None:
    """İki tabloyu (store.load_table çıktısı) TEK tabloda birleştirir — gerekirse
    EK SÜTUN ekleyerek, veri kaybetmeden. `dedup.py` bakım ajanı tarafından,
    mükerrer bulunduktan SONRA çağrılır."""
    import json
    payload_a = json.dumps({"columns": a["columns"], "rows": a["rows"]}, ensure_ascii=False)
    payload_b = json.dumps({"columns": b["columns"], "rows": b["rows"]}, ensure_ascii=False)
    d = vlm.call_json(vlm.txt_msg(_MERGE_Q.format(
        a_id=a["id"], a_docstring=a["docstring"], a=payload_a,
        b_id=b["id"], b_docstring=b["docstring"], b=payload_b,
        subcats=_format_subcats(subcats))), max_tokens=4096)
    if not d:
        return None
    return {"docstring": (d.get("docstring") or "").strip(),
            "category": (d.get("category") or "").strip(),
            "subcategory": (d.get("subcategory") or "").strip(),
            "columns": d.get("columns") or [],
            "rows": d.get("rows") or {}}
