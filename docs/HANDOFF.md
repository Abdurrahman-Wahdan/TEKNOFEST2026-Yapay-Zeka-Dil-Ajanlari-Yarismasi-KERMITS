# Handoff — TF26 Karşılaştırma Tablosu Boru Hattı

**Durum: 20 Ağustos 2026.** Tablo üretimi tamamlandı.
Tüm sayılar bu tarihte canlı depodan ölçüldü.

Kök dizin: `/Users/ifa/Desktop/TEKNOFEST/TF26`

---

## 0. Sistem tek paragrafta

10 Türk katılım bankasının sitesi taranır; sayfalar ve PDF'ler LLM ile temiz
Markdown'a çevrilir; görseller VLM ile okunur. Tüm metin 8196 karakterlik %10
örtüşmeli parçalara bölünüp Qdrant'a gömülür. Sonra bir ajan hattı bu havuzu
gezip "bu sayfa karşılaştırılabilir mi?" diye sorar; karşılaştırılabilir
konular için 10 bankaya paralel araştırma ajanı gönderip tablo kurar. En sonda
mükerrer tablolar birleştirilir, her referans denetlenir ve satırlara
geçerlilik tarihi damgalanır.

**Terminoloji (her prompt'ta tekrarlanır):** Bunlar *katılım bankası* (faizsiz)
verileridir. "kredi"/"faiz" değil **"finansman" / "kâr payı" / "kâr oranı"**
kullanılır. Tek istisna yerleşik ürün adı olan "kredi kartı".

### Ölçülen durum

| | |
|---|---|
| Banka | 10 |
| Site sayfası (md) | 8.522 |
| PDF | 839 |
| Temizlenmiş içerik (md) | 5.083 site + 836 PDF |
| Qdrant chunk (`campaigns`) | 7.030 |
| Tablo | 403 |
| Tablo satırı | 4.030 |
| Referans (benzersiz point_id) | 2.456 |
| Sağlam referans | %93,2 |
| Doğrulanmış geçerlilik tarihi | 193 satır |

---

## 1. Altyapı

| Bileşen | Değer | Nerede tanımlı |
|---|---|---|
| Sohbet modeli | `google/gemma-4-31B-it` | `llm/providers/vllm_provider.py` |
| Model rotası | `/gemma/v1` | `llm/providers/vllm_provider.py` |
| Gömme modeli | `Qwen/Qwen3-Embedding-0.6B` | `config/settings.py` |
| Gömme rotası / boyut | `/embed/v1` · 1024 | `config/settings.py` |
| Vektör deposu | `http://localhost:6333` | `config/settings.py` |
| Kaynak koleksiyonu | `campaigns` | `dataprep/embed.py` |
| Tablo koleksiyonu | `compare_tables` | `dataprep/compare/retrieval.py` |

> `settings.QDRANT_COLLECTION_CHUNKS` = `bank_chunks` görünür ama **tablo hattı
> onu kullanmaz.** Hat `campaigns` ve `compare_tables` koleksiyonlarını
> `os.environ` üzerinden ayrı okur.

### Tünel: adres kendiliğinden değişir

vLLM sunucusu bir tünel arkasında ve **adres periyodik olarak değişir.**
Canlı adres düz metin bir URL'de yayımlanır; `config/tunnel.py` onu çekip
`.env`'e yazar.

Adresin kendisi **kodda değil, `.env`'de** durur: `TUNNEL_GIST_URL` bir
dağıtımın özel kanalıdır, bu deponun bir özelliği değil. Boş bırakılırsa o
aday merdivenden düşer, gerisi (`.env` ve eldeki adres) aynen denenir.

```python
# config/tunnel.py
gist = settings.TUNNEL_GIST_URL.strip()      # .env'den; boşsa None döner
url = f"{gist}?t={int(time.time())}"         # CDN önbelleğini kırar
tunnel.refresh_if_needed()                   # her hatadan sonra çağrılır
```

**Kritik davranış:** bir istek hata verdiğinde **önce tünel adresi kontrol
edilir, sonra yeniden denenir.** Sıralama böyle çünkü hataların en yaygın
sebebi adres değişimidir. Bu desen hattaki tüm ağ çağrılarında aynıdır.

Gist'e `?t=` zaman damgası eklenmezse GitHub CDN eski adresi döndürür — bu
daha önce saatlerce süren takılmaya sebep oldu.

### Eşzamanlılık

Tüm ağ çağrıları `dataprep/net_limit.py` içindeki paylaşılan semaforu kullanır.
Sınır **60** eşzamanlı istek. Worker sayısını bunun üstüne çıkarmak zararsızdır
— fazlası sırada bekler, hata üretmez.

```python
with NET_SEM:
    res = llm.invoke([...])
NET_SEM.report(ok=True, duration=...)   # başarı + süre
NET_SEM.report(ok=False)                # kopma/timeout
```

---

## 2. Aşamalar

Numaralandırma gerçek bir sıradır: her aşama bir öncekinin çıktısını okur.
Aşama 1 ayrı bir komut; 2, 3.1 ve 3.2 aynı modülün alt aşamalarıdır.

### Aşama 1 — Crawling (site taraması)

Her bankanın sitesi gezilir, sayfalar ham indirilir, PDF bağlantıları toplanır.
SPA olan bankalarda (`adilkatilim`, `tombank`, `turkiyefinans`) `--render` ile
tarayıcı motoru devreye girer.

```bash
bash dataprep/crawl/run_all.sh              # 10 banka sırayla
python -m dataprep.crawl.graph --bank kuveytturk --max-retries 4 [--render]
```

**Yazar:** `data/<banka>_site/_raw/`, `pdfs/`, `_tree.json`, `_universe.json`,
`_catalog.json`

### Aşama 1b — Metin temizleme (site sayfaları)

Ham sayfalar LLM ile temiz, frontmatter'lı Markdown'a çevrilir.
**Site metinleri bu aşamada temizlenir**, tablo aşamasında değil.

```bash
python -m dataprep.content            # --stage all içinde
```

**Yazar:** `data/<banka>_site/content/**.md` · **Defter:** `_content_ledger.json`

### Aşama 2 — PDF metinleri

```bash
python -m dataprep.content --stage pdf-text
```

**Yazar:** `data/<banka>_site/_pdf_clean/**.md` ·
**Defter:** `_pdf_clean_ledger.json`

> **Dikkat:** bu aşama `_content_ledger.json` değil **`_pdf_clean_ledger.json`**
> kullanır. Yeniden işlemek için yanlış defteri temizlemek sessiz atlamaya yol
> açar.

### Aşama 3.1 — Sayfa görselleri (page image)

Site sayfalarındaki görseller VLM ile okunup metne çevrilir — kampanya
afişlerinin çoğu sadece görselde yazılıdır.

```bash
python -m dataprep.content --stage images-page
```

**Yazar:** ilgili `content/*.md` içine gömülür ·
**Önbellek:** `_image_cache.json`, `_olu_gorsel_url.json`

### Aşama 3.2 — PDF görselleri (pdf image)

PDF içindeki görseller ve taranmış tam sayfalar VLM'e gönderilir.

```bash
python -m dataprep.content --stage images-pdf
python -m dataprep.content --stage images        # 3.1 + 3.2
```

**Yazar:** ilgili `_pdf_clean/*.md` içine gömülür

### Aşama 4 — Gömme (Qdrant indeksleme)

```bash
python -m dataprep.embed                  # artımlı
python -m dataprep.embed --recreate       # koleksiyonu sıfırla
```

**Yazar:** Qdrant `campaigns` — şu an 7.030 nokta

### Aşama 5 — Tablo üretimi (ajan hattı)

Havuzdaki her sayfa gezilir. `classify_agent` "bu karşılaştırılabilir mi?"
der; evetse konu için 10 bankaya paralel `bank_agent` gönderilir, her banka
kendi satırını doldurur, `finalize_table` sütunları sıkılaştırıp isim/kategori
verir.

```bash
python -m dataprep.compare.pipeline [--banks ...] [--limit N]
```

**Yazar:** `data/_tables/<konu>.json` · **Defterler:** `_registry.json`,
`_page_ledger.json`, `_subcategories.json`, `_url_havuzu.json`,
`_indeks_kuyrugu.json`

### Aşama 6 — Mükerrerlik birleştirme (dedup)

Sorgu + niyet araması ile mükerrer tablolar bulunur ve birleştirilir. Aşırı
genelleme yasak — kategori ve alt kategori temiz kalmalı. Referanslar kod
tarafında birleştirilir.

```bash
python -m dataprep.compare.dedup
```

**Sonuç:** 461 → 403 tablo (58 birleşme, 373 tekil, 0 hata)

### Aşama 7 — Denetim ajanı

Her tablonun her benzersiz referans URL'i için **ayrı bir ajan** açılır. Ajan o
URL'in chunk'larına tek tek bakar (chunk'lar birbirinden habersiz) ve "bu
kaynaktaki bilgi tabloda gerekli yere yazılmış mı?" diye sorar. Sonra **tek bir
birleştirme ajanı** tüm geri bildirimleri işleyip tabloyu son haline getirir;
sütun birleştirme ve seyreklik kararı bu ajanındır, kodda eşik yoktur. En sonda
kod satır bazlı tarih damgasını yazar.

```bash
python -m dataprep.compare.tablo_denetim            # tüm tablolar
python -m dataprep.compare.tablo_denetim --tablo <id>
python -m dataprep.compare.tablo_denetim --kuru     # yazmadan raporla
```

**Sonuç:** 403/403 tablo, 0 hata, 3.210 eksik işlendi (ort. 7,96/tablo)

---

## 3. Veri nereye yazılıyor

### Banka dizini — `data/<banka>_site/`

| Yol | İçerik | Yazan aşama |
|---|---|---|
| `_raw/` | Ham indirilen HTML | 1 |
| `pdfs/` | İndirilen PDF dosyaları | 1 |
| `content/**.md` | Temizlenmiş sayfa metni + görsel metinleri | 1b, 3.1 |
| `_pdf_clean/**.md` | Temizlenmiş PDF metni + görsel metinleri | 2, 3.2 |
| `_content_ledger.json` | URL → çıktı yolu, hash, durum | 1b |
| `_pdf_clean_ledger.json` | PDF defteri (ayrı!) | 2 |
| `_image_cache.json` | Görsel URL → okunan metin | 3.1, 3.2 |
| `_catalog.json` / `_tree.json` | Site haritası ve keşif çıktısı | 1 |
| `_universe.json` | Bilinen URL evreni + diff | 1 |

### Tablo dizini — `data/_tables/`

| Dosya | İçerik |
|---|---|
| `<konu>.json` | Bir karşılaştırma tablosu (403 adet) |
| `_registry.json` | Tablo kimliği → konu/kategori kaydı |
| `_page_ledger.json` | İşlenen sayfa URL'leri — **tablo aşamasının kendi defteri** |
| `_subcategories.json` | Alt kategori tutarlılığı için havuz |
| `_url_havuzu.json` | Banka bazlı benzersiz URL havuzu |
| `_indeks_kuyrugu.json` | Qdrant'a yazılmayı bekleyen tablolar |

### Banka bazlı hacim

| Banka | Sayfa | PDF | Temiz md | Qdrant chunk |
|---|---:|---:|---:|---:|
| kuveytturk | 2.786 | 262 | 1.655 | 1.932 |
| ziraatkatilim | 1.554 | 63 | 829 | 1.186 |
| albaraka | 1.398 | 36 | 734 | 928 |
| vakifkatilim | 1.038 | 134 | 653 | 1.360 |
| turkiyefinans | 838 | 113 | 530 | 726 |
| emlakkatilim | 460 | 195 | 425 | 551 |
| hayatfinans | 258 | 1 | 129 | 179 |
| dunyakatilim | 144 | 3 | 73 | 82 |
| tombank | 38 | 22 | 41 | 72 |
| adilkatilim | 8 | 10 | 14 | 14 |

Son üç banka dijital banka olduğu için site yüzeyi doğal olarak küçük — eksik
tarama değil.

---

## 4. Veri şemaları

### Qdrant `campaigns` noktası

```json
{
  "page_content": "...",
  "metadata": {
    "bank": "vakifkatilim",
    "url": "http://...",
    "validity_status": "bilinmiyor",
    "type": "metin",
    "chunk_index": "0"
  }
}
```

URL birden çok alanda olabilir. Kod her zaman `retrieval.py` içindeki kanonik
çözücüyü kullanır — doğrudan `metadata.url` okumak **hatalıdır**:

```python
# dataprep/compare/retrieval.py
_URL_ALANLARI = ("url", "source_url", "pdf_url",
                 "gorsel_url", "source_page", "parent")

_kanonik_url(meta)     # ilk dolu alanı döndürür
_kanonik_tarih(meta)   # gecerlilik_* / campaign_* ikisini de dener
_url_kosulu(url)       # tüm alanlarda OR arayan Qdrant filtresi
```

### Tablo dosyası

```json
{
  "id": "akaryakıt-indirimi-kampanyası",
  "topic": "akaryakıt indirimi kampanyası",
  "docstring": "...",
  "category": "kampanya",
  "subcategory": "alışveriş indirimleri",
  "columns": ["Kampanya Durumu", "İndirim Oranı / Kazanım", "...", "Geçerlilik"],
  "rows": {
    "vakifkatilim": {
      "Kampanya Durumu": "Sunuluyor",
      "İndirim Oranı / Kazanım": "%5",
      "Geçerlilik": "01/08/2026 - 31/08/2026"
    }
  },
  "sources": {
    "vakifkatilim": [
      {"point_id": "90867ef9-...", "url": "https://...", "note": "..."}
    ]
  },
  "cell_sources": {},
  "created_from": null, "created_at": null, "updated_at": null
}
```

`rows` her zaman 10 bankayı içerir. `sources` satır (banka) bazlı referans
listesidir; `cell_sources` hücre bazlıdır.

**Kaynak izlenebilirliği:** her referans bir `point_id` taşır — uydurma URL
yazılamaz. Ajanlar yalnızca `point_id` döndürür, URL'i kod çözer. Şu an 4.030
satırın 3.797'sinde (%94,2) referans var; boş kalan 233 satır o ürünü gerçekten
sunmayan bankalara ait.

### Geçerlilik biçimi

`dataprep/compare/tablo_tarih.py` tek metin alanı üretir. Tek tarih yalnız
yazılmaz — hangi taraf olduğu belirsiz kalmasın diye eksik taraf `?` ile
işaretlenir.

| Değer | Anlamı |
|---|---|
| `01/08/2026 - 30/09/2026` | Başlangıç ve bitiş biliniyor |
| `01/08/2026 - ?` | Sadece başlangıç biliniyor |
| `? - 30/09/2026` | Sadece bitiş biliniyor |
| `-` | Tarih yok (süresiz ürün ya da belgede yazmıyor) |

Damga **satır (banka) bazlıdır.** Bir bankanın birden çok kaynağı farklı tarih
taşıyorsa **en dar ortak aralık** (kesişim) alınır — `_kesistir()`.

---

## 5. Değiştirilmemesi gereken kurallar

Bunlar tasarım kararlarıdır, tercih değil. Her biri bir hatadan sonra kondu.

1. **Tek organik sınır 8196 karakterlik chunk'tır.** `max_tokens` yok, çıktı
   sınırı yok, bağlam kırpma yok, karakter/görsel/boyut sınırı yok. 1
   karakterlik dosya bile işlenir.
2. **Örtüşme %10 (~820 karakter)** ve yalnızca 8196'yı aşan metni bölerken
   uygulanır.
3. **Karşılaştırma tabloları asla chunk'lanmaz ve asla kırpılmaz** — gömme
   sürecinde docstring dahil tam gider. 8k kuralı tabloların dışındaki her şey
   için geçerlidir.
4. **Tarihler belgeler arasında asla paylaşılmaz.** Ne bulunuyorsa o kalır; bir
   sayfanın tarihi başka sayfaya kopyalanmaz.
5. **Retry sınırsızdır** ve her denemede **önce tünel kontrolü, sonra bekleme**
   yapılır. Üstel backoff, 30 saniye tavan.
6. **Kaynak atama yalnız `point_id` ile olur.** Model URL uydurursa atılır;
   tahmin ederek kaynak bağlama yoktur.
7. **Kararlar ajanındır, kodun değil.** Sütun birleştirme, seyreklik,
   mükerrerlik — hiçbirinde kod tarafında eşik veya benzerlik kuralı yoktur.
   Agentic sistemde kural tabanlı düzeltme ajanın bilinçli kararlarını bozar.
8. **Yeni parametre icat edilmez** — bağlantı, JSON ve chunk davranışı mevcut
   modüllerden miras alınır.

### JSON hata merdiveni

Bozuk JSON geldiğinde **somut hata prompt'un sonuna eklenip** yeniden denenir.
Sıra: her sıcaklıkta önce geri bildirimsiz, sonra geri bildirimli.

```
0.0 normal → 0.0 feedback → 0.3 normal → 0.3 feedback
           → 0.6 normal → 0.6 feedback → 1.0 normal → 1.0 feedback
```

**Statik / dinamik ayrımı:** beklenen anahtar kontrolü **yalnız statik
çıktılarda** yapılır. Tablo şeması (`columns`/`rows`) dinamiktir — model sütun
ekleyip silebilir, orada beklenti dayatmak hattı kilitler. Banka isimleri sabit
10 tane olduğu için `rows` anahtarları doğrulanabilir.

| Modül | Beklenen anahtarlar |
|---|---|
| `bank_agent` | `offers`, `sources` |
| `classify_page` | `comparable`, `topic` |
| `finalize` | `topic`, `category`, `subcategory` |
| `dedup` | `duplicates` |
| `tablo_denetim` | `bilgi_var`, `eksikler` |

---

## 6. Bilinen tuzaklar

Hepsi gerçekten yaşandı. Devralan kişi aynı çukurlara düşmesin.

### Denetimi iki kez çalıştırmak referansları bozar

Denetim biterken `store.overwrite_table()` tabloyu baştan yazar ve `sources`
içine o an okuduğu `point_id`'leri koyar. Aynı iş ikinci kez çalıştırılırsa
aradaki koleksiyon değişiklikleriyle uyuşmayan id'ler tabloda kalır — **ölü
referans**.

20 Ağustos'ta bu oldu: zincir script'i denetimi 14:15'te başlatıp 15:48'de
hatasız bitirmişti; izleyici süreç sustuğu için iş durmuş sanıldı ve 17:14'te
ikinci kez çalıştırıldı. Sonuç: 2.973 referansın 1.159'u (%39) öldü. URL
üzerinden kurtarıldı (%93,2'ye çıktı), ama **kalıcı düzeltme yapılmadı** —
`overwrite_table`'ın referans yazma davranışı hâlâ bu riski taşıyor.

**Çalıştırmadan önce logu kontrol edin:** `logs/zincir_*.log` ve
`logs/tablo_denetim_*.log` son satırına bakın.

### Gömme istemcisi bayat tünel adresiyle önbelleğe alınıyordu

`embeddings/providers/remote_provider.py` istemciyi yalnız model adıyla
önbelleğe alıyordu; tünel adresi değişince eski istemci kullanılmaya devam
ediyor ve hat sunucu sağlıklıyken saatlerce takılıyordu.

```python
key = (model, settings.VLLM_BASE_URL)   # önce sadece (model,) idi
```

### Taranmış PDF'lerde sessiz veri kaybı

1,3 MB'lık bir PDF yalnızca 335 karakter veriyordu: 24 karakterlik bir başlık
`has_text=True` yapıp görsel yolunu kapatıyordu. Eşik eklendi — metin bu eşiğin
altındaysa sayfa tam sayfa görsel olarak VLM'e gider.

```python
# dataprep/content.py
_TAM_SAYFA_ESIK = int(os.environ.get("PDF_TAM_SAYFA_ESIK", "400"))
if not has_text or len((_txt or "").strip()) < _TAM_SAYFA_ESIK:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
```

Düzeltmeden sonra aynı PDF 4.374 karakter ve tam tablolar verdi.

### Tablo silinince Qdrant kaydı kalıyordu

`delete_table()` dosyayı ve kaydı siliyor ama Qdrant indeksine dokunmuyordu.
Dedup 58 tabloyu birleştirdikten sonra indekste 461, diskte 403 kayıt vardı —
mükerrerlik ajanı **var olmayan tablolarla eşleşme arıyordu**.
`drop_table_index()` eklendi ve `dedup.merge_pair()`'den çağrılıyor.

### Tarihlerin çoğu belgede gerçekten yok

4.030 satırın yalnızca 193'ünde doğrulanmış tarih var. Bu bir kayıp değil:
referans chunk'ların yaklaşık yarısında ne metinde ne metadata'da tarih
geçiyor. Akreditif, vesaik mukabili ödeme, kâr paylaşım oranları gibi kalıcı
ürünler doğaları gereği tarihsizdir.

Metinde geçen tarihlerin çoğu da kampanya tarihi **değil**: mevzuat, kuruluş,
sözleşme, oran güncelleme tarihleri. Bu ayrımı regex yapamaz — 20 Ağustos'ta
bir ajan koşusu 404 hatalı damgayı eleyip 193 doğrulanmış tarih bıraktı.

### Aşama defterlerini karıştırmak

Üç ayrı defter var ve yanlışını temizlemek sessiz atlamaya yol açar:
`_content_ledger.json` (site metni), `_pdf_clean_ledger.json` (PDF metni),
`data/_tables/_page_ledger.json` (tablo aşaması). Bir sayfayı baştan işletmek
istiyorsanız **üçünü de** gözden geçirin.

---

## 7. Sıfırdan tam koşu

```bash
# 0 — sunucu ve depo ayakta mı
curl -s localhost:6333/collections | head
python -c "from config import tunnel; tunnel.refresh_if_needed()"

# 1 — crawling (uzun; banka başına ayrı süreç)
bash dataprep/crawl/run_all.sh

# 2 — metin temizleme + PDF + görseller
python -m dataprep.content --stage all

# 3 — Qdrant'a göm
python -m dataprep.embed

# 4 — tabloları üret
python -m dataprep.compare.pipeline

# 5 — mükerrerleri birleştir
python -m dataprep.compare.dedup

# 6 — denetle + tarih damgala  (BİR KEZ! bkz. Bölüm 6)
python -m dataprep.compare.tablo_denetim
```

### Doğrulama

```bash
# tablo ve satır sayısı
python -c "
import json,glob
t=[json.load(open(f)) for f in glob.glob('data/_tables/*.json')]
t=[x for x in t if isinstance(x,dict) and 'rows' in x]
print('tablo',len(t),'satır',sum(len(x['rows']) for x in t))"

# referans sağlamlığı — %90 altına düşerse ölü point_id var
python -c "
import json,glob,sys; sys.path.insert(0,'.')
from dataprep.compare.retrieval import _shared, COLLECTION
_,c=_shared(); p=set()
for f in glob.glob('data/_tables/*.json'):
    t=json.load(open(f))
    if not isinstance(t,dict) or 'rows' not in t: continue
    for b,l in (t.get('sources') or {}).items():
        for s in l or []:
            if s.get('point_id'): p.add(s['point_id'])
pl=sorted(p); v=set()
for i in range(0,len(pl),256):
    for x in c.retrieve(COLLECTION,ids=pl[i:i+256]): v.add(str(x.id))
print(f'{len(v)}/{len(pl)} sağlam')"
```

### Testler

```bash
pytest tests/ -q
```

Hat davranışını koruyan testler: `test_json_merdiveni.py`,
`test_tablo_denetim.py`, `test_dedup_referans.py`, `test_indeks_kuyrugu.py`,
`test_indeks_retry.py`, `test_url_havuzu.py`, `test_kisit_yok.py`,
`test_chunk_overlap.py`, `test_banka_paralellik.py`, `test_konu_yarisi.py`,
`test_tek_sentez.py`, `test_point_id_kurtarma.py`.

**Bilinen test sorunu:** `test_dedup_referans.py` dosya bütün olarak koşarken
takılıyor; testler tek tek geçiyor. Muhtemel sebep Qdrant çekişmesi.
Doğrulanmadı.

---

## 8. Açık işler

- **`overwrite_table` referans yazma davranışı.** Denetimin ikinci kez
  çalıştırılması hâlâ ölü referans üretebilir. Kalıcı düzeltme yapılmadı;
  kurtarma manuel bir script ile yapıldı.
- **Tarih çıkarımı crawl aşamasına taşınabilir.** Şu an tarih yalnız chunk
  metadata'sından geliyor ve metinde geçen kampanya tarihlerinin bir kısmı
  metadata'ya hiç yazılmamış. URL bazına taşımak ölçüldü — kazanç yalnızca 3
  chunk, yani sorun bağlama yeri değil, çıkarımın kendisi.
- **Kurtarılamayan 166 referans.** İşaret ettikleri 127 URL Qdrant'ta gerçekten
  yok; çoğu `.png`/`.jpg` görsel dosyası ve indekslenmemiş sayfa. Silinmediler.
- **Sütun birleştirme sıfır kaldı.** Denetimin birleştirme ajanı 403 tablonun
  hepsinde çalıştı ama hiçbirinde sütun birleştirmedi; ön testte bir tablo
  17→10 sütuna inmişti. Ajanın kararı olduğu için koda müdahale edilmedi, ama
  gözden geçirilmeli.

### Yedekler

`data/_tables_yedek_20260819_171015/` ve `data_backups/` altında eski anlık
görüntüler var. 20 Ağustos oturumunda alınan iki yedek (tarih yamasından önce
ve point_id kurtarmasından önce) oturum scratchpad'indedir, repoda değil.
