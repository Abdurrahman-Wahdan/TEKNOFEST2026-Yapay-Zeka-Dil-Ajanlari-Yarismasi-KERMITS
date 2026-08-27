"""Gemma karar/sentez adımları — hepsi zero-shot, genel ilke, JSON çıktı. Örnek/kural
yok; model kendi kararını verir.

  fits_table       : eşleşme bulunduktan SONRA — gelen rapor gerçekten o tabloya mı
                     uyuyor (erken sayfa-bazlı tahmin yanılmış olabilir)?
  closest_subcats  : bir konu için mevcut alt kategoriler arasında en YAKIN olanları
                     (kelime + anlam benzerliği) sıralar — classify_agent.finalize_table
                     ve merge_tables'ın alt kategori kararını, ham/uzun bir listeyi
                     gözle taramak yerine somut, puanlanmış adaylara dayandırması için.
  build_row        : var olan bir tabloyu (ya da table=None ise boş bir tablodan)
                     TEK bir bankanın raporuyla genişletir — SIRALI tablo inşasının
                     tek adımı. SADECE içerik (sütun/satır/docstring) üretir;
                     kategori/alt-kategori/isim/mükerrerlik kararı burada VERİLMEZ —
                     tablo TAMAMEN kurulduktan SONRA, TEK seferde,
                     classify_agent.finalize_table tarafından kararlaştırılır
                     (bkz. pipeline.py).
  merge_tables     : mükerrer bulunan İKİ TAMAMLANMIŞ tabloyu tek tabloda birleştirir
                     (gerekirse ek sütun ekleyerek) — hem dedup.py bakım ajanı hem
                     finalize_table'ın SON anda bulduğu mükerrerlik için kullanılır,
                     build_row'dan bağımsız bir akış (iki tam tablo, tek bankanın
                     raporu değil).
"""
from __future__ import annotations

import difflib

from dataprep import vlm

# Orkestratöre (sentez adımı) giden rapor payload'ı da büyüyebilir (10 banka x
# çok alan/kaynak). İçerik kararı değil, aynı server-context güvenliği: alanları
# makul boyuta kısalt, yine de büyükse kaynak notlarını at. "Ana agent" de
# context sıkıntısı çekmesin.
def _compact_reports(reports: list[dict]) -> list[dict]:
    """Raporlar OLDUĞU GİBİ geçer — hiçbir alan kırpılmaz.

    Eskiden attributes 400, note 200 karakterde kesiliyor ve payload büyükse
    notlar tamamen atılıyordu. KALDIRILDI (kullanıcı kararı 2026-08-19): ne
    girdi ne çıktı tarafında sınır koymuyoruz; bağlam sınırı uzak sunucunun
    kendi kararı. Kırpma, kaynak notunu (hangi kaynaktan ne alındığı) yok
    ederek sonda yapılacak agentic URL/tarih eşleştirmesini de köreltiyordu.

    Fonksiyon KORUNUYOR (çağrı noktaları değişmesin) ama artık kopya döndürür,
    çağıranın elindeki raporu yan etkiyle bozmaz."""
    import copy
    return copy.deepcopy(reports)

# classify_page (kıyaslanabilir mi + mevcut tablo havuzunda erken eşleşen var mı)
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
        report=json.dumps(_compact_reports([report])[0], ensure_ascii=False))))
    if not d:
        return True                     # LLM ulaşılamadı -> muhafazakâr, tabloya güven
    return bool(d.get("fits", True))


# --- alt kategori benzerliği (kelime + anlam) --------------------------------
# Havuz büyüdükçe (yüzlerce alt kategori) modele ham/uzun bir liste vermek onu
# tutarlılık kurmaya zorlarken aslında gözle taramaya itiyordu — aynı şeyin iki
# farklı yazımı ("konut finansmanı" / "konut kredisi finansmanı") kolayca ayrı
# kategori olarak açılabiliyordu. Bu, search_tables'daki embedding felsefesiyle
# aynı: somut, puanlanmış adaylar sun. Alt kategori sayısı küçük (onlarca/yüzlerce,
# tablo sayısıyla sınırlı) olduğu için kalıcı bir Qdrant koleksiyonu gerekmiyor,
# anlık hesap yeterli.
_SUBCAT_TOP_K = 8


def _keyword_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def closest_subcategories(topic: str, subcats: list[str] | None,
                            top_k: int = _SUBCAT_TOP_K) -> list[tuple[str, float]]:
    """Mevcut alt kategorileri `topic`e göre (kelime + anlam benzerliğinin
    MAKSİMUMU) sıralar. Embedding çağrısı başarısız olursa (ağ/GPU) sessizce
    kelime-benzerliğine düşer — bir sıralama önerisi asla tüm sentez adımını
    çökertmemeli."""
    subcats = list(dict.fromkeys(s for s in (subcats or []) if s))  # tekilleştir, sırayı koru
    if not subcats:
        return []
    try:
        from embeddings import get_embedding
        from index.embed_text import query_text
        embed = get_embedding()
        # query_text: Qwen3-Embedding asimetrik retrieval bekliyor -- sorgu
        # tarafına görev talimatı eklenmezse (bkz. index/embed_text.py) benzerlik
        # skorları sessizce bozuluyor (kanıtlı: talimatsız haliyle "kredi kartı
        # kampanyaları" "konut finansmanı"nı geride bırakıyordu). Alt kategoriler
        # burada PASAJ rolünde, düz metin kalır.
        q_vec = embed.embed_query(query_text(topic))
        doc_vecs = embed.embed_documents(subcats)

        def _cosine(u, v):
            dot = sum(x * y for x, y in zip(u, v))
            nu = sum(x * x for x in u) ** 0.5
            nv = sum(y * y for y in v) ** 0.5
            return dot / (nu * nv) if nu and nv else 0.0

        scored = [(cat, max(_cosine(q_vec, vec), _keyword_score(topic, cat)))
                  for cat, vec in zip(subcats, doc_vecs)]
    except Exception:                          # noqa: BLE001 — kelime-only'e düş
        scored = [(cat, _keyword_score(topic, cat)) for cat in subcats]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]


def _subcat_block(topic: str, subcats: list[str] | None) -> str:
    ranked = closest_subcategories(topic, subcats)
    if not ranked:
        return "(henüz hiç alt kategori yok, ilkini sen aç)"
    lines = "\n".join(f"  - {name!r} (benzerlik: {score:.2f})" for name, score in ranked)
    return ("En YAKIN mevcut alt kategoriler, benzerliğe göre sıralı (gerçekten "
            "aynı şeyi ifade eden biri varsa MUTLAKA aynı ismi kullan — tutarlılık "
            "için; hiçbiri gerçekten uymuyorsa yeni kısa bir ad üret):\n" + lines)


_BUILD_ROW_Q = (
    "ÖNEMLİ — TERMİNOLOJİ: bunlar KATILIM BANKASI (faizsiz/İslami bankacılık) "
    "verileri. ÜRETECEĞİN HER METİNDE (docstring, sütun adları, değerler) "
    "'kredi'/'faiz' değil 'finansman'/'kâr payı'/'kâr oranı' kullan; raporda "
    "konvansiyonel terim geçse bile SEN katılım bankacılığı terimine çevir. Tek "
    "istisna: 'kredi kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu gibi kalır.\n\n"
    "Konu: '{topic}'.\n\n"
    "{table_desc}\n\n"
    "Şimdi '{bank}' bankasından şu araştırma raporu geldi:\n\"\"\"{report}\"\"\"\n\n"
    "Bunu tabloya işle:\n"
    "- '{bank}' için bir satır ekle/güncelle — offers=false ise hücreyi '-' "
    "bırak. 'sunulmuyor' YAZMA: sunulmadığını KANITLAMAK neredeyse imkânsızdır, "
    "bilgiyi bulamamış da olabilirsin. '-' nötrdür, yanlış iddia taşımaz.\n"
    "- Tabloda BAZI bankaların satırı TAMAMEN BOŞ ({{}}) görünüyor olabilir — bu "
    "'sunulmuyor' ANLAMINA GELMEZ, henüz o bankanın sırası gelmemiş/işlenmemiş "
    "demektir (sıralı bir süreçle, banka banka dolduruluyorsun). Böyle boş "
    "satırlara DOKUNMA, olduğu gibi bırak — sadece SENİN işlediğin '{bank}' "
    "satırını doldur.\n"
    "- SÜTUNLARI SEN belirle: raporun attributes alanları mevcut sütunlardan "
    "birine anlamca karşılık geliyorsa AYNI sütunu kullan (yeniden icat etme); "
    "gerçekten hiçbirine uymayan, ayırt edici yeni bir bilgiyse (veri kaybetmemek "
    "için) YENİ bir sütun aç — ama gereksiz yere çoğaltma, önce mevcutlara "
    "uyup uymadığına bak.\n"
    "- Yeni bir sütun açtıysan, DİĞER bankaların o sütununu BOŞ bırak (henüz "
    "bilinmiyor, o bankalar için ayrıca araştırılmadı).\n"
    "- VERİ KAYBI YASAK: mevcut sütun/satırları GÜNCELLEMEKTE serbestsin (ör. "
    "elindeki yeni kanıt bir 'sunulmuyor' değerinin aslında yanlış olduğunu "
    "gösteriyorsa gerçek değerle düzelt, ya da bir sütun adını daha isabetli "
    "bir isme normalize et) — ama halihazırda dolu bir hücreyi güçlü bir "
    "sebebin yoksa SADECE boşaltma/silme; belirsizsen olduğu gibi bırak. Bu "
    "kural yalnız senin işlediğin '{bank}' satırı için değil, tablodaki HİÇBİR "
    "bankanın verisi için de geçerli.\n"
    "- docstring'i yaz/gerekiyorsa güncelle: bu tablo neyi kıyaslıyor (1-2 cümle). "
    "Bu docstring İLERİDE EMBEDDING'E ÇIKARILIP ANLAM BAZLI ARAMADA kullanılacak — "
    "ayırt edici olan NE (ürünün/kampanyanın türü, konusu, kapsamı) cümlenin "
    "başında ve belirgin olsun, kalıplaşmış/diğer tablolarla ortak bir çerçeve "
    "cümleye boğma; aynı zamanda AÇIKLAYICI kal.\n"
    "- KAYNAK HARİTASI: rapordaki 'sources' listesinde her kaynağın 'note' alanı "
    "o kaynaktan NE aldığını anlatıyor — '{bank}' satırında SEN AZ ÖNCE doldurduğun "
    "her SÜTUN için, o değeri hangi kaynak(lar)dan (point_id) aldığını eşleştir. "
    "Sadece '{bank}' satırındaki sütunlar için ver, başka bankalar için verme.\n"
    "- '... (Geçerlilik)' adlı tarih sütunlarını SEN AÇMA ve mevcut olanları "
    "DEĞİŞTİRME/SİLME: onları en sonda, tüm tablolar bittikten sonra ayrı bir "
    "adım hücrelerin kaynaklarından üretecek. Bunun DIŞINDA, bir ürünün "
    "geçerlilik/vade/son başvuru bilgisi konunun DOĞAL bir özelliğiyse onu "
    "normal bir sütun olarak eklemekte serbestsin — senin işin değerin KENDİSİ "
    "ve onun kaynak_haritasi'dır.\n\n"
    'SADECE JSON: {{"docstring": "<1-2 cümle>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer ya da - ya da boş>", ...}}}}, '
    '"kaynak_haritasi": {{"<sütun>": ["<point_id>", ...]}}}}')


def _restore_lost_values(prior_rows: dict, prior_columns: list[str],
                          columns: list[str], rows: dict) -> None:
    """VERİ KAYBI YASAK'ın asıl uygulaması — columns/rows'u YERİNDE günceller.

    ESKİ tasarım sütun İSMİNİ koruyordu: bir önceki sütun yeni şemada yoksa
    körü körüne geri ekleniyordu — model o sütunu (verisini de taşıyarak) daha
    isabetli bir isme yeniden adlandırdığında bile. Sonuç: hiçbir satırda
    kullanılmayan "hayalet" sütunlar birikiyordu (canlı koşuda kanıtlandı —
    özel-cari-hesap tablosunda 12 sütunun 4'ü tamamen boştu).

    Bunun yerine sütun İSMİNİ değil VERİYİ koruyoruz: bir bankanın eski bir
    sütundaki değeri, o bankanın YENİ satırının HERHANGİ bir yerinde (başka
    bir isimle de olsa) hâlâ görünüyorsa, eski isim GERİ GELMEZ — gerçekten
    yeniden adlandırılmış/birleştirilmiş demektir. Görünmüyorsa (gerçekten
    kaybolmuşsa) O DEĞER, SADECE o banka için, eski sütun adıyla geri eklenir
    — tüm tabloya boş bir sütun açılmaz."""
    for bank, prior_vals in prior_rows.items():
        rows.setdefault(bank, prior_vals)      # banka bütünüyle unutulmuşsa (ayrı durum)
        bank_new_values = None                 # tembel hesap: sadece gerekirse
        for col in prior_columns:
            if col in columns:
                continue                        # sütun zaten şemada, sorun yok
            old_val = prior_vals.get(col)
            if not old_val:
                continue
            if bank_new_values is None:
                bank_new_values = {str(v).strip() for v in rows.get(bank, {}).values() if v}
            if str(old_val).strip() in bank_new_values:
                continue                        # değer başka bir isimle korunmuş
            if col not in columns:
                columns.append(col)
            rows.setdefault(bank, {})[col] = old_val


def build_row(table: dict | None, topic: str, bank: str, report: dict) -> dict | None:
    """Tek bir bankanın raporunu tabloya işler — SIRALI inşanın tek adımı.

    `table=None` ise boş bir tablodan bu bankanın satırıyla İLK tabloyu kurar.
    SADECE içerik (sütun/satır/docstring) üretir — kategori/alt-kategori/isim/
    mükerrerlik kararı burada VERİLMEZ, tablo TAMAMEN kurulduktan SONRA, TEK
    seferde classify_agent.finalize_table tarafından kararlaştırılır (bkz.
    pipeline.py). Pipeline bunu banka-banka, TEMİZ (paylaşılmayan) bir LLM
    çağrısıyla sırayla çağırır; bir bankanın hatası/boş cevabı önceki
    bankaların verisini SİLEMEZ — tek-atımlık eski tasarımın (tüm bankalar tek
    çağrıda) aksine, en kötü ihtimalle o TEK bankanın satırı eksik kalır.
    Ayrıca: model önceden var olan bir satırı/sütunu unutup dönerse bu
    fonksiyon onu DETERMİNİSTİK olarak geri ekler (prompt talimatı tek başına
    yeterli güven değil) — güncelleme serbest, sessiz veri kaybı değil."""
    import json
    if table is None:
        table_desc = "Tablo şu an BOŞ — bu ilk satır, sen kurarsın."
    else:
        table_desc = ("Mevcut tablo (şu ana kadar %d bankanın verisiyle):\n\"\"\"%s\"\"\"" % (
            len(table["rows"]),
            json.dumps({"columns": table["columns"], "rows": table["rows"]}, ensure_ascii=False)))
    report_payload = json.dumps(_compact_reports([report])[0], ensure_ascii=False)
    d = vlm.call_json(vlm.txt_msg(_BUILD_ROW_Q.format(
        topic=topic, table_desc=table_desc, bank=bank, report=report_payload)))
    if not d:
        return None
    columns = d.get("columns") or (table["columns"] if table else [])
    rows = d.get("rows") or (table["rows"] if table else {})
    # SAHTE BANKA KORUMASI — model bazen "rows" içine yanlışlıkla bir SÜTUN
    # adını (ya da başka bir uydurma anahtarı) banka gibi üst seviyeye koyup
    # döndürüyor (kanıtlı: canlı koşuda "geçerlilik_alanı" adında hayalet bir
    # "banka" satırı oluştu, sonra _restore_lost_values her turda onu sadakatle
    # koruyup kalıcılaştırdı). Geçerli anahtarlar SADECE önceki tablonun zaten
    # bildiği bankalar + şu an işlenen '{bank}' olabilir — başka HERHANGİ bir
    # anahtar (o bankanın verisi tabloya hiç ulaşmadan) burada silinir.
    known_banks = (set(table["rows"].keys()) if table else set()) | {bank}
    rows = {b: v for b, v in rows.items() if b in known_banks}
    # VERİ KAYBI YASAK — prompt talimatı yeterli güven değil (bu tasarımın tüm
    # amacı zaten "modele güvenip toptan sıfırlanma" riskini azaltmaktı).
    # Model önceden var olan bir bankanın satırını ya da bir sütunu unutup
    # dönerse (kasıtsız/hata), burada DETERMİNİSTİK olarak geri eklenir —
    # kasıtlı bir "sil" burada yok, sadece değeri boşaltmak/güncellemek var.
    if table:
        _restore_lost_values(table["rows"], table["columns"], columns, rows)
    docstring = (d.get("docstring") or "").strip() or (table["docstring"] if table else "")

    # HÜCRE-bazlı kaynak haritası: LLM sadece point_id verir (kaynak_haritasi),
    # gerçek url/tarih zenginleştirmesi report["sources"]'ta (bank_agent.py'nin
    # point_meta ile çözdüğü) zaten hazır — burada sadece eşleştiriyoruz.
    by_point = {s.get("point_id"): s for s in (report.get("sources") or []) if s.get("point_id")}
    cell_sources = {b: dict(v) for b, v in (table.get("cell_sources", {}) if table else {}).items()}
    bank_map = cell_sources.setdefault(bank, {})
    harita = d.get("kaynak_haritasi") or {}
    gecerli_sutun = set(columns)
    for col, pids in harita.items():
        if not isinstance(pids, list) or col not in gecerli_sutun:
            continue                     # hayalet anahtar (sütun değil) -> yok say
        resolved = [by_point[p] for p in pids if p in by_point]
        if resolved:
            bank_map[col] = resolved     # bu tur ne bulduysa geçerli (o sütun için güncel)

    # NOT: kaynaksiz kalan DOLU hucrelere "yedek" kaynak BAGLAMIYORUZ.
    # Denendi ve geri alindi: bankanin raporundaki ilk kaynagi bagliyordu —
    # banka dogru ama o kaynak tam O SUTUNUN bilgisini icermeyebilir, yani
    # ALAKASIZ bir URL/tarih damgalanabilirdi. Yanlis kaynak, eksik kaynaktan
    # daha kotudur (yanlis tarih sessizce dogru gorunur). Kaynaksiz hucreler
    # sonda TOPLU, agentic bir URL/tarih atama adimiyla eslestirilir.
    if not bank_map:
        cell_sources.pop(bank, None)

    return {"docstring": docstring, "columns": columns, "rows": rows, "cell_sources": cell_sources}


# --- TEK SEFERDE SENTEZ (10 banka, tek LLM çağrısı) ---------------------------
# Eski tasarım bankaları SIRAYLA işliyordu (build_row × 10). Ölçüldü: adım
# başına ~23s, tablo başına ~3.9 dk ve aynı tablo 10 kez (her seferinde biraz
# daha büyümüş halde) prompt'a konduğu için TOPLAM ~4.4 kat fazla veri
# taşınıyordu. Tek çağrı hem daha az bağlam taşır (~2000 token) hem ~4 kat
# hızlıdır. Ham chunk'lar BURAYA KONMAZ — ajanların ÖZET raporları kullanılır;
# arama zekası ve banka izolasyonu fan-out katmanında korunur.
_BUILD_ALL_Q = (
    "ÖNEMLİ — TERMİNOLOJİ: bunlar KATILIM BANKASI (faizsiz/İslami bankacılık) "
    "verileri. ÜRETECEĞİN HER METİNDE (docstring, sütun adları, değerler) "
    "'kredi'/'faiz' değil 'finansman'/'kâr payı'/'kâr oranı' kullan; raporda "
    "konvansiyonel terim geçse bile SEN katılım bankacılığı terimine çevir. Tek "
    "istisna: 'kredi kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu gibi kalır.\n\n"
    "Konu: '{topic}'.\n\n"
    "{table_desc}\n\n"
    "Aşağıda {n} bankanın araştırma raporu var (her biri KENDİ bankasının "
    "içeriğinde arama yapmış bir ajandan geliyor):\n\"\"\"{reports}\"\"\"\n\n"
    "Bunların HEPSİNİ TEK bir karşılaştırma tablosunda birleştir:\n"
    "- HER banka için bir satır: offers=false olan bankanın hücrelerini '-' bırak "
    "('sunulmuyor' YAZMA — sunulmadığını kanıtlamak imkânsızdır, bilgiyi "
    "bulamamış da olabilirsin; '-' nötrdür). Raporu hiç gelmemiş bir banka "
    "varsa satırını BOŞ ({{}}) bırak.\n"
    "- SÜTUNLARI SEN belirle: bankaların RAPORLARINDA ortaklaşan nitelikleri tek "
    "bir sütunda topla (aynı şeyi farklı adla söyleyen iki sütun AÇMA). Bir "
    "nitelik yalnız tek bankada varsa ve gerçekten ayırt ediciyse yine sütun "
    "açabilirsin; diğer bankalar için o hücreyi BOŞ bırak ('sunulmuyor' değil — "
    "o banka için araştırılmamış demektir).\n"
    "- Karşılaştırılabilirlik önceliklidir: mümkün olduğunca ÇOK bankanın "
    "doldurabileceği sütunlar seç.\n"
    "- Değerler KISA ve karşılaştırılabilir olsun (oran, tutar, vade, koşul); "
    "cümleye boğma ama açıklayıcı kal.\n"
    "- KAYNAK HARİTASI: her rapordaki 'sources' listesinde her kaynağın 'note' "
    "alanı o kaynaktan NE alındığını anlatıyor. HER banka için, doldurduğun HER "
    "sütunun değerini hangi kaynak(lar)dan (point_id) aldığını eşleştir.\n"
    "- '... (Geçerlilik)' adlı tarih sütunlarını SEN AÇMA ve mevcut olanları "
    "DEĞİŞTİRME/SİLME: onları en sonda ayrı bir adım üretecek. Bunun DIŞINDA bir "
    "ürünün vade/son başvuru bilgisi konunun DOĞAL özelliğiyse normal sütun "
    "olarak ekleyebilirsin.\n"
    "- docstring yaz: bu tablo neyi kıyaslıyor (1-2 cümle). Bu docstring "
    "EMBEDDING'E ÇIKARILIP anlam bazlı aramada kullanılacak — ayırt edici olan NE "
    "(ürünün türü, konusu, kapsamı) cümlenin başında ve belirgin olsun.\n\n"
    'SADECE JSON: {{"docstring": "<1-2 cümle>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer ya da - ya da boş>", ...}}}}, '
    '"kaynak_haritasi": {{"<banka>": {{"<sütun>": ["<point_id>", ...]}}}}}}'
)


def build_all(table: dict | None, topic: str, reports: list[dict]) -> dict | None:
    """TÜM bankaların raporunu TEK LLM çağrısında tabloya işler.

    `table` verilirse (mevcut tabloya ekleme) onun sütun/satırları korunarak
    üstüne yazılır; None ise sıfırdan kurar. Dönen sözlük build_row ile AYNI
    biçimdedir (docstring/columns/rows/cell_sources), böylece pipeline'ın geri
    kalanı değişmez. LLM ulaşılamazsa None döner — çağıran retry'a bırakır."""
    import json

    if not reports:
        return None
    table_desc = "Henüz bir tablo yok, sıfırdan kuruyorsun."
    if table:
        table_desc = ("Mevcut tablo (üstüne ekleyeceksin, VERİSİNİ SİLME):\n"
                       "\"\"\"%s\"\"\"" % json.dumps(
                           {"columns": table["columns"], "rows": table["rows"]},
                           ensure_ascii=False))
    payload = json.dumps(_compact_reports(reports), ensure_ascii=False)
    d = vlm.call_json(vlm.txt_msg(_BUILD_ALL_Q.format(
        topic=topic, table_desc=table_desc, n=len(reports), reports=payload)))
    if not d:
        return None

    columns = d.get("columns") or (table["columns"] if table else [])
    rows = d.get("rows") or {}
    # SAHTE BANKA KORUMASI (build_row ile aynı ilke): yalnız gerçek banka
    # anahtarları kalır — model sütun adını banka gibi üst seviyeye koyabiliyor.
    gecerli = {r["bank"] for r in reports if r.get("bank")}
    if table:
        gecerli |= set(table["rows"].keys())
    rows = {b: v for b, v in rows.items() if b in gecerli and isinstance(v, dict)}
    # Raporu gelen ama modelin unuttuğu banka BOŞ satır olarak durur (kaybolmaz).
    for r in reports:
        rows.setdefault(r["bank"], {})
    if table:
        _restore_lost_values(table["rows"], table["columns"], columns, rows)
    docstring = (d.get("docstring") or "").strip() or (table["docstring"] if table else "")

    # HÜCRE-bazlı kaynak haritası — build_row'daki ile AYNI kural: yalnız modelin
    # AÇIKÇA eşleştirdiği point_id'ler yazılır, tahmin YAPILMAZ (alakasız kaynak
    # bağlamak, kaynaksız bırakmaktan kötüdür; sonda agentic eşleştirilecek).
    by_point = {}
    for r in reports:
        for s in (r.get("sources") or []):
            if s.get("point_id"):
                by_point[s["point_id"]] = s
    cell_sources = {b: dict(v) for b, v in
                     ((table.get("cell_sources") or {}) if table else {}).items()}
    gecerli_sutun = set(columns)
    for banka, harita in (d.get("kaynak_haritasi") or {}).items():
        if banka not in gecerli or not isinstance(harita, dict):
            continue
        bmap = cell_sources.setdefault(banka, {})
        for col, pids in harita.items():
            if col not in gecerli_sutun or not isinstance(pids, list):
                continue
            cozulen = [by_point[p] for p in pids if p in by_point]
            if cozulen:
                bmap[col] = cozulen
        if not bmap:
            cell_sources.pop(banka, None)
    return {"docstring": docstring, "columns": columns, "rows": rows,
            "cell_sources": cell_sources}


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
    "- Bir ANA KATEGORİ ata: 'kampanya' ya da 'ürün'. Bir ALT KATEGORİ ata — "
    "{subcats}\n\n"
    "Tablo A ({a_id}) — {a_docstring}:\n\"\"\"{a}\"\"\"\n\n"
    "Tablo B ({b_id}) — {b_docstring}:\n\"\"\"{b}\"\"\"\n\n"
    'SADECE JSON: {{"docstring": "<1-2 cümle>", "category": "kampanya"|"ürün", '
    '"subcategory": "<kısa alt kategori>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer ya da ->", ...}}}}}}')


def _merge_cell_sources(a: dict, b: dict) -> dict:
    """İki tablonun cell_sources'ını mekanik (LLM'siz) birleştirir — kategori/
    docstring gibi yorum gerektiren bir şey değil, sütun bazında dict-merge.
    Her iki taraf da aynı (banka, sütun) için kaynak taşıyorsa BİRLEŞTİRİLİR
    (point_id'ye göre tekilleştirilir), veri kaybı olmaz."""
    out: dict = {b_: {c: list(v) for c, v in cols.items()} for b_, cols in a.items()}
    for bank, cols in b.items():
        dst = out.setdefault(bank, {})
        for col, sources in cols.items():
            existing = dst.setdefault(col, [])
            seen = {s.get("point_id") for s in existing}
            for s in sources:
                if s.get("point_id") not in seen:
                    existing.append(s)
                    seen.add(s.get("point_id"))
    return out


def merge_tables(a: dict, b: dict, subcats: list[str] | None = None) -> dict | None:
    """İki TAMAMLANMIŞ tabloyu (store.load_table çıktısı) TEK tabloda birleştirir
    — gerekirse EK SÜTUN ekleyerek, veri kaybetmeden. `dedup.py` bakım ajanı
    tarafından, mükerrer bulunduktan SONRA çağrılır; build_row'dan bağımsız bir
    akış (tek bankanın raporu değil, iki tam tablo birleşiyor)."""
    import json
    payload_a = json.dumps({"columns": a["columns"], "rows": a["rows"]}, ensure_ascii=False)
    payload_b = json.dumps({"columns": b["columns"], "rows": b["rows"]}, ensure_ascii=False)
    topic_hint = a.get("topic") or a["docstring"]
    subcat_block = _subcat_block(topic_hint, subcats)
    d = vlm.call_json(vlm.txt_msg(_MERGE_Q.format(
        a_id=a["id"], a_docstring=a["docstring"], a=payload_a,
        b_id=b["id"], b_docstring=b["docstring"], b=payload_b,
        subcats=subcat_block)))
    if not d:
        return None
    return {"docstring": (d.get("docstring") or "").strip(),
            "category": (d.get("category") or "").strip(),
            "subcategory": (d.get("subcategory") or "").strip(),
            "columns": d.get("columns") or [],
            "rows": d.get("rows") or {},
            "cell_sources": _merge_cell_sources(a.get("cell_sources", {}), b.get("cell_sources", {}))}
