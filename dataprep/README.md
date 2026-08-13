# dataprep — Veri Seti Hazırlama Pipeline'ı

Katılım bankalarının web sitelerinden **müşteriye yönelik kampanya ve ürün
bilgisini** toplayıp, dashboard ve chatbot için temiz bir veri seti üretir.
İki aşamalıdır ve her kararı bir LLM verir (kural/regex yoktur).

```
dataprep/
  crawl/            AŞAMA 1 — agentic site indirme
    graph.py          LangGraph orkestrasyon (CLI giriş noktası)
    frontier.py       keşif: sitemap veya özyinelemeli BFS → URL ağacı
    policy.py         triage: her dal için LLM kararı (DIVE/FETCH/SKIP)
    store.py          sayfa → markdown, PDF → binary, içerik-hash katalog
    render.py         JS-render (SPA) siteler için Playwright/Chromium
    bank.py           aktif banka motorunu seçen proxy
    adapters/         banka-özel düzeltmeler (ör. tombank soft-404)
    engines/          banka başına ağ/çıkarım motoru (10 banka)
  pdf/              AŞAMA 2 — PDF → metin (Vision LLM)
    chunk.py          sayfa → overlap'li görüntü chunk
    extract.py        agentic VLM: zoom → skip → overlap-stitch extract
  verify.py         bütünlük denetimi (sayfa/PDF kaçağı, boş, FAIL)

data/               ÇIKTI (git'e girmez): <bank>_site/ … md + pdfs + pdf_text
```

## Aşama 1 — Crawl

```bash
python -m dataprep.crawl.graph --bank kuveytturk          # tek banka
bash   dataprep/crawl/run_all.sh                          # tümü
```

Akış: **keşif** (sitemap ya da özyinelemeli BFS) → **triage** (LLM her dalı
DIVE/FETCH/SKIP; parse gelmezse sıcaklık 0→0.3→0.6→1.0) → **hasat** (sayfa
metni + linkli PDF'ler binary). Değişim takibi katalogla; ikinci koşu yalnız
değişeni işler. JS-render siteler için `--render` (Playwright).

Çıktı `data/<bank>_site/`:
- URL ağacını yansıtan `.md` (YAML frontmatter: url, başlık, banka, **parent**)
- `pdfs/<kaynak-sayfa-yolu>/…pdf` — PDF, onu linkleyen sayfanın altında
- `_catalog.json` (hash + parent + kaynak sayfa), `_decisions.json`, `failures.txt`

## Aşama 2 — PDF → metin (VLM)

```bash
python -m dataprep.pdf.extract          # (batch runner — tüm PDF'ler)
```

Her PDF sayfası Vision LLM'e (gemma) görüntü olarak gider (OCR değil → tablo ve
görsel düzen korunur):
1. **Zoom/okunabilirlik:** okunana kadar overlap'li 2'ye böl (taban: derinlik 4 / <250px).
2. **Skip:** ≤3 sayfa → bir "faydalı" yeterli; >3 → ilk/orta/son çoğunluk.
3. **Extract:** sırayla, önceki çıktının son 800 karakteri anchor → dikişsiz metin.

Çıktı: `data/<bank>_site/pdf_text/<pdf>.md`.

## Gereksinimler
`httpx, trafilatura, pypdf, pymupdf, pillow, playwright (+chromium), langgraph,
langchain-openai`. Vision/LLM: yerel vLLM host (model başına `/…/v1/chat/completions`).
