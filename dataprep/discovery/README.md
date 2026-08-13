# dataprep.discovery — Canlı Endpoint Keşfi

Katılım bankalarının sitelerindeki **interaktif araçların** (kâr payı/finansman/kart
taksit hesaplama, döviz çevirici, canlı kur/altın, oran tabloları) arkasındaki **canlı
XHR/fetch API çağrılarını** yakalar ve müşteriye-fayda sağlayanları eler. Amaç: sohbet
asistanının RAG'den (statik metin) alamayacağı **dinamik/gerçek-zamanlı** veriyi
çağırabileceği endpoint kataloğunu çıkarmak.

## İki adım

**1) Keşif** — `discover_endpoints.py`
Playwright (headless Chrome) her aday araç sayfasını açar; pasif ağ dinlemesi + generic
etkileşim (input doldur, "Hesapla/Çevir" tıkla) ile tetiklenen API çağrılarını
kaydeder. Tracking/analytics/auth host'ları elenir. Aday sayfalar mevcut crawl
(`data/*_site/*.md`) URL'lerinden, araç anahtar kelimeleriyle seçilir.
- Dedup: her benzersiz endpoint'ten yalnız 1 örnek; sonraki tekrarlar hızlı geçilir.
- Resume: `out/visited.json` ile gezilen sayfalar atlanır (ekleme modunda).

```bash
python -m dataprep.discovery.discover_endpoints            # tüm bankalar
python -m dataprep.discovery.discover_endpoints --banks kuveytturk vakifkatilim
python -m dataprep.discovery.discover_endpoints --urls "<tek sayfa>"   # test
python -m dataprep.discovery.discover_endpoints --headed              # tarayıcıyı gör
```

**2) Eleme** — `filter_endpoints.py`
Her tekil endpoint'i (method, URL, istek + yanıt örneği) Gemma'ya verir; **zero-shot**
ilkeyle karar verir: girdiye-bağlı hesap **veya** canlı/güncel yapılandırılmış veri
döndürüyor ve oturum gerektirmiyorsa TUT; tracking/config/auth veya statik metin/HTML ise
SİL. `--prune` ile katalog yalnız TUT'lara indirgenir.

```bash
python -m dataprep.discovery.filter_endpoints --prune
```

## Çıktılar (`out/`, gitignore — lokalde kalır)
- `raw_calls.jsonl` — yakalanan tekil çağrılar (kanıt)
- `endpoints.json` / `.md` — tekilleştirilmiş katalog
- `endpoints_kept.json` / `.md` — Gemma-elemeli nihai araç endpoint listesi
- `visited.json` — resume durumu

> Not: `out/` üretilen çıktıdır ve kod tarafından yeniden üretilebilir; repoya
> gönderilmez.
