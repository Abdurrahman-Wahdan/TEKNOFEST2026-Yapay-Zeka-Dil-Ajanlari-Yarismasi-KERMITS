# Görev: Geçerlilik sütunlarını satır başına teke indir

## Sorun

`dataprep/compare/tablo_tarih.py` her veri sütununun yanına ayrı bir
`<sütun> (Geçerlilik)` sütunu açıyor. Ölçüm sonucu:

```
veri sütunu        2620
geçerlilik sütunu  3424   ← tüm sütunların %57'si
  tamamen boş      2078   ← geçerlilik sütunlarının %61'i
```

Ve gereksiz: **%85 satırda tüm geçerlilik hücreleri zaten aynı tarihi taşıyor**
(552 satır tek tarih, 99 satır farklı tarih).

Ayrıca bir hata üretiyor: ajan "Geçerlilik" adında bir veri sütunu açtığında
`Geçerlilik (Geçerlilik)` diye saçma bir sütun oluşuyor.

## İstenen

Her banka satırı için **tek bir `Geçerlilik` sütunu**. Satırdaki hücreler farklı
tarihler taşıyorsa **en geniş aralık** alınır: en erken başlangıç, en geç bitiş.

Hücre bazlı kaynak/tarih bilgisi `cell_sources` içinde **aynen kalmalı** — yalnız
tablonun görünen sütun yapısı sadeleşiyor, veri silinmiyor.

## Yapılacaklar

### 1. `dataprep/compare/tablo_tarih.py` — `tabloyu_damgala()`

Şu an her veri sütunu için `c + TARIH_SUTUNU_SONEKI` sütunu açıyor. Bunun yerine:

- Tabloya tek bir `Geçerlilik` sütunu eklensin (varsa güncellensin), en sona.
- Bir bankanın tarihi: o bankanın `cell_sources[banka]` altındaki **tüm**
  sütunların kaynaklarını birleştirip `hucre_tarihi()`'ne ver. Mevcut
  `_en_genis()` yardımcısı zaten en erken başlangıç + en geç bitişi hesaplıyor.
- Kaynağı olmayan banka için `-` yazılsın (mevcut davranış korunur).

Dikkat: `TARIH_SUTUNU_SONEKI` sabiti ve `hucre_tarihi()` başka yerlerde de
kullanılıyor olabilir — kaldırmadan önce `grep -rn "TARIH_SUTUNU_SONEKI"` ile
kontrol et.

### 2. Mevcut 402 tabloyu dönüştürecek bir geçiş adımı

Yeni koşular düzgün üretecek, ama **elimizdeki tablolar eski yapıda**. Bunları
dönüştüren bir betik gerekiyor (`dataprep/compare/gecerlilik_birlestir.py` gibi):

- Her tabloda `* (Geçerlilik)` sütunlarını bul.
- Her banka satırı için bu hücrelerdeki tarihleri topla, **en geniş aralığı**
  hesapla, tek `Geçerlilik` hücresine yaz.
- Eski `* (Geçerlilik)` sütunlarını `columns` listesinden ve tüm satırlardan sil.
- `Geçerlilik (Geçerlilik)` gibi bozuk sütunları da temizle.
- `cell_sources`'a **DOKUNMA**.
- `--kuru` bayrağı olsun (yazmadan raporlasın).
- Yazma atomik olsun: `.json.tmp` yazıp `replace()` — mevcut kodda bu desen var.

Tarih biçimi mevcut kuralı korumalı (`bicimle()` fonksiyonu):
`01/08/2026 - 30/09/2026` · `01/08/2026 - ?` · `? - 30/09/2026` · `-`

### 3. `dataprep/compare/son_islem.py`

Geçiş adımı bir kez çalıştırılıp bitecekse pipeline'a eklemeye gerek yok. Ama
`tarih` adımından sonra çalışacak kalıcı bir temizlik olarak eklemek istersen
`ADIMLAR` demetine ekle ve `calistir()` içine dallandır — desen dosyada mevcut.

## Doğrulama

Uygulamadan önce **yedek al**: `cp -R data/_tables data/_tables_yedek_<tarih>`

Sonra şunları kontrol et:

- Geçerlilik sütunu sayısı 3424 → ~402 civarına inmeli (tablo başına 1).
- `Geçerlilik (Geçerlilik)` içeren tablo kalmamalı.
- **Hiçbir veri sütunu ve hücresi kaybolmamalı** — dönüşüm öncesi/sonrası
  dolu veri hücresi sayısı aynı olmalı (şu an 11.543).
- `cell_sources` toplam kaynak sayısı değişmemeli (şu an 11.534).
- Daha önce tarihi olan bir satır tarihsiz kalmamalı: dönüşüm öncesi en az bir
  `* (Geçerlilik)` hücresi dolu olan her satırın, sonrasında `Geçerlilik`
  hücresi dolu olmalı.

Örnek doğrulama sorgusu:

```python
import json, pathlib
kok = pathlib.Path("data/_tables")
vs = gs = dolu = kaynak = 0
for p in kok.glob("[!_]*.json"):
    d = json.loads(p.read_text(encoding="utf-8"))
    for c in (d.get("columns") or []):
        if c.endswith(" (Geçerlilik)") or c == "Geçerlilik": gs += 1
        else: vs += 1
    for b, h in (d.get("rows") or {}).items():
        for c, v in (h or {}).items():
            if c.endswith(" (Geçerlilik)") or c == "Geçerlilik": continue
            if str(v or "").strip() not in ("", "-"): dolu += 1
    kaynak += sum(len(v or []) for cc in (d.get("cell_sources") or {}).values()
                  for v in cc.values())
print(f"veri sütunu {vs} | geçerlilik {gs} | dolu hücre {dolu} | kaynak {kaynak}")
```

## Bağlam / kurallar

- **Veri kaybı yasak.** Bu iş yalnız sütun yapısını sadeleştirir; hiçbir değer,
  kaynak ya da tarih bilgisi silinmez (eski geçerlilik hücreleri tek sütunda
  birleştirilir, atılmaz).
- Metinlerde kırpma/truncate yok.
- `-` işareti "veri yok" demek, "banka sunmuyor" demek değil.
- Terminoloji: kâr payı / finansman (faiz / kredi değil).
- Tablo dizini: `data/_tables/` — `_` ile başlayan dosyalar hariç tutulur.
