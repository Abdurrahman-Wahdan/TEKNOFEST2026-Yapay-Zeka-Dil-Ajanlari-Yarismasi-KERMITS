# RAG Corpus Audit — `download_sites/`

Audit of the crawled bank corpus before embedding into Qdrant. Covers what the
markdown actually contains, which frontmatter fields are trustworthy, and the
payload schema proposed for indexing.

Corpus crawled 2026-08-08, audited 2026-08-09. All figures measured, not estimated,
except where marked.

---

## 1. Inventory

| | value |
|---|---|
| documents | 7,105 |
| body characters | 110,412,160 |
| rough tokens | ~27.6M |
| distinct titles | 5,515 |
| crawl snapshot | single, `2026-08-08` |

Per bank:

| bank | docs | of which PDF | median chars | thin (<300) | no description |
|---|---|---|---|---|---|
| kuveytturk | 2366 | 12 | 3,597 | 7 | 14 |
| turkiyefinans | 1507 | 901 | 7,107 | 19 | 995 |
| vakifkatilim | 796 | 0 | 1,712 | 104 | 0 |
| albaraka | 589 | 0 | 1,477 | 27 | 113 |
| emlakkatilim | 588 | 0 | 460 | 262 | 415 |
| ziraatkatilim | 524 | 0 | 805 | 155 | 334 |
| dunyakatilim | 272 | 4 | 6,992 | 2 | 6 |
| hayatfinans | 249 | 0 | 2,556 | 0 | 1 |
| tombank | 182 | 157 | 10,404 | 2 | 158 |
| adilkatilim | 32 | 14 | 3,533 | 1 | 32 |

### What the corpus is actually made of

| slice | docs | chars | share |
|---|---|---|---|
| **contract & form PDFs** (`sözleşme`/`form`) | 955 | 56,915,483 | **65.2%** |
| blog | 996 | 6,144,822 | 7.0% |
| **campaigns** (`kampanyalar`) | 704 | 1,353,190 | **1.6%** |
| everything else | — | — | 26.2% |

*(shares computed against the cleaned corpus, §3)*

**This is the headline finding.** Two-thirds of the corpus is loan-contract
legalese. The campaign content the system is actually being built to answer
questions about is 1.6% of it. Embedding the corpus as-is spends ~65% of the
compute budget on `Genel Kredi Sözleşmesi` boilerplate.

---

## 2. Metadata assessment

Frontmatter is present on 7,098 / 7,105 docs (99.9%) with keys
`url`, `title`, `description`, `bank`, `type`, `crawled_at`.

| field | coverage | verdict |
|---|---|---|
| `url` | 99.9% | **Trustworthy.** Needs canonicalisation (see §3.5). |
| `bank` | 99.9% | **Trustworthy.** 10 clean display values. |
| `type` | 99.9% | **Trustworthy.** `page` / `pdf`. |
| `crawled_at` | 99.9% | **Trustworthy but uniform** — every doc says `2026-08-08`. Carries no per-document freshness signal. |
| `title` | 99.9% | **Usable after cleaning.** See below. |
| `description` | 70.9% | **Do not embed.** See below. |

### `description` is boilerplate, not description

Only 1,991 distinct values across 4,477 docs that have one. The top values are
site-wide meta tags repeated verbatim:

- ×679 `Vakıf Katılım`
- ×581 `Kuveyt Türk Katılım Bankacılığı alanlarında yatırım faaliyeti gösteren…`
- ×463 `Kuveyt Türk Blog seçkin içerikleri ile yayında!…`

The Colin's taksit campaign — a specific card-instalment offer — carries the
same generic `Kuveyt Türk Katılım Bankacılığı alanlarında…` description as 580
other pages. **Concatenating `description` into embedding text would inject
identical filler into thousands of chunks and actively degrade retrieval.**
Keep it as a stored payload field if you like; do not embed it.

### `title` needs suffix stripping

2,298 Kuveyt Türk titles end in `| Kuveyt Türk Katılım Bankası`, 323 Emlak in
`| Türkiye Emlak Katılım Bankası`, and so on. The leading portion is genuinely
descriptive and worth prepending to chunks; the suffix is pure repetition.

Additionally, 1,100+ docs share a non-identifying title — ×220 `Dünya Katılım`,
×161 `Fotoğraf Galerisi`, ×117 `Duyuru Detay`, ×116 `Detay`, ×89 `Blog Detay`.
For these, derive a title from the URL slug instead.

---

## 3. Content defects, by severity

### 3.1 Appended PDF text pollutes host pages — **critical**

The crawler inlines the full text of every linked PDF into the page that links
it (`## Ek belge:` blocks). 120 pages carry 290 such blocks totalling
**19,169,248 chars — 17.4% of the entire corpus.**

Worst case: `kuveytturk_site/hakkimizda/katilim-bankaciligi/faizsiz-bankacilik-nedir.md`
is **6,567 chars of real content followed by 2,565,813 chars of 2021/2022 annual-report
financial tables** — 99.7% pollution. Its frontmatter still says
`title: "Faizsiz Bankacılık Nedir?"`.

Embedded as-is, hundreds of chunks of balance-sheet line items would inherit the
title "What is interest-free banking?". Any question about interest-free banking
principles retrieves financial statement rows.

**Fix:** truncate each page at the first `\n## Ek belge:` and index the PDFs as
their own documents (most already exist separately as `type: pdf`).

### 3.2 Every bank's homepage was destroyed — **high**

Seven files named `index.md` contain the crawler's own table of contents
(`# <Bank> — Site İçeriği`, a list of every crawled URL), not homepage content.

Cause: `url_to_path()` maps the site root to `OUT/"index.md"`, while `finalize()`
writes the TOC to `OUT/"INDEX.md"`. **macOS APFS is case-insensitive, so these
are the same file** — the TOC overwrites the homepage.

Affected: adilkatilim, dunyakatilim, hayatfinans, kuveytturk, tombank,
turkiyefinans, ziraatkatilim — 1,426,147 chars of link lists. These must be
excluded from indexing; they are pure URL soup.

### 3.3 675 redundant duplicate documents — **high**

159 groups of byte-identical bodies under different URLs, ~6.4M chars:

- ×161 emlakkatilim, 251 chars each — identical press-release stubs
- ×150 turkiyefinans, 15,933 chars each — the same page under `www`/`:443`/`http` variants
- ×106 dunyakatilim, 6,992 chars each — the KVKK cookie notice
- ×15 vakifkatilim, 5,740 chars each — branch pages with identical text

Deduplicate on a hash of the normalised body before embedding.

### 3.4 Dünya Katılım's cookie notice is 63.8% of its text — **medium**

A 6,928-char KVKK/çerez aydınlatma metni appears in 190 of 272 Dünya Katılım
documents. Strip it as a known block, or its 1.3M chars will dominate every
Dünya Katılım retrieval.

Corpus-wide, line-level boilerplate is otherwise low (1.2% overall) — trafilatura
did a good job stripping nav chrome on the other nine banks.

### 3.5 URL variants need canonicalisation — **medium**

The same page appears as `https://www.x.com.tr/p`, `https://www.x.com.tr:443/p`,
and `http://www.x.com.tr/p`. Canonicalise to https + no port + no fragment +
lowercase host before hashing to `doc_id`, or the same content lands three times
under three IDs.

### 3.6 Minor

- **31 docs are effectively non-Turkish** (`patriot-act.md`, `W8BEN.md`,
  `WOLFSBERG-questionnarie-CBDDQ.md`, AML policies). bge-m3 is multilingual so
  they will embed fine, but they will never match a Turkish query. Tag with a
  `lang` field rather than dropping.
- **3 files of 6,708 contain U+FFFD** replacement characters. Negligible.
- **Markdown link syntax is 7.1% of campaign text**, of which 5.7% is bare URLs
  inside `](…)`. Strip URL targets and keep anchor text before embedding — URLs
  are semantically empty tokens that dilute the vector.
- **262 emlakkatilim and 155 ziraatkatilim docs are under 300 chars** — mostly
  nav stubs. Drop below a ~250-char floor.

---

## 4. Campaign content — the important part

Despite being 1.6% of the corpus, campaign pages are the **highest-quality
content in it**. A representative document:

```
url: ".../kampanyalar/kendim-icin/kart-kampanyalari/colinsde-vade-farksiz-4-aya-varan-taksit-firsati"
title: "Colin's'de Vade Farksız 4 Aya Varan Taksit Fırsatı | Kuveyt Türk Katılım Bankası"

**Kampanya Tarihleri**6.08.2026 - 31.12.2026
- Kuveyt Türk bireysel kredi kartları ile 31 Aralık 2026 tarihine kadar Colin's…
- Kampanyaya tüm Kuveyt Türk Bireysel Kredi Kartları (Miles & Smiles…, Sağlam Kart…)
- Mevzuatın öngördüğü taksit sayılarının üzerinde taksitlendirme yapılamayacaktır.
```

Dates, eligible cards, and conditions all present. Median campaign doc is
1,473 chars — **small enough to embed whole without splitting**, which matters
because splitting would separate the validity dates from the conditions.

### Date extractability varies sharply by bank

| bank | campaign docs | `Kampanya Tarihleri` label | `dd.mm.yyyy` range | long Turkish date |
|---|---|---|---|---|
| kuveytturk | 442 | 410 | 410 | 259 |
| vakifkatilim | 101 | 4 | 5 | 72 |
| emlakkatilim | 67 | 0 | 0 | 52 |
| albaraka | 40 | 1 | 4 | 24 |
| turkiyefinans | 25 | 9 | 0 | 16 |
| hayatfinans | 13 | 0 | 0 | 10 |
| dunyakatilim | 8 | 0 | 1 | 1 |
| ziraatkatilim | 7 | 0 | 1 | 2 |
| tombank | 1 | 0 | 1 | 0 |

**86% (607/704) expose a parseable date**, but only Kuveyt Türk uses a structured
label. Everyone else buries dates in prose (`31 Aralık 2026 tarihine kadar`), so
extraction needs both a regex pass and an LLM fallback — which is what the
`EXTRACTOR_MODEL` (qwen) role in `config/settings.py` is presumably for.

### ⚠ 77% of dated campaigns were already expired at crawl time

Of the 422 campaigns with an explicit `start - end` range, **323 had an end date
before 2026-08-08** and only 99 were still valid.

This is the single biggest correctness risk in the whole system. Without a date
filter the agent will answer "yes, Kuveyt Türk offers X" citing a campaign that
ended in 2024. `campaign_end` must be a first-class indexed payload field, and
the retriever must filter on it by default.

---

## 5. Cleaning impact

| stage | docs | chars | ~tokens |
|---|---|---|---|
| 0 — raw | 7,105 | 110,412,160 | 27.6M |
| 1 — strip appended PDF blocks | 7,105 | 91,242,912 | 22.8M |
| 2 — drop exact duplicates | 6,430 | 87,305,688 | 21.8M |
| 3 — drop stubs <250 chars | 6,132 | 87,245,619 | 21.8M |

21% of characters removed. The remainder is still dominated by contract PDFs;
excluding those (§6, open question 1) takes it to roughly **30M chars / 7.5M
tokens**, an order of magnitude cheaper to embed.

> **Unverified:** embedding throughput was not benchmarked. `BAAI/bge-m3` is
> **not** in the local HF cache (`~/.cache/huggingface` has `bge-reranker-v2-m3`,
> `Qwen3-Embedding-8B`, `gte-multilingual-base` and
> `Trendyol/TY-ecomm-embed-multilingual-base` — but not bge-m3), so first run
> downloads ~2.2GB. Benchmark on a 200-chunk sample before committing to a
> full CPU index run.

---

## 6. Proposed Qdrant schema

### Collections — two, not one

`config/settings.py` already defines `QDRANT_COLLECTION_CAMPAIGNS = "campaigns"`.
Recommend keeping that and adding a second:

| collection | contents | ~size | refresh |
|---|---|---|---|
| `campaigns` | campaign pages only | ~700 docs / ~900 chunks | frequent — campaigns expire |
| `bank_docs` | products, FAQ, corporate, blog | ~4,400 docs / ~15k chunks | monthly |
| *(deferred)* `bank_legal` | contract & form PDFs | ~955 docs / ~32k chunks | rarely |

Separating them means the expensive, static legalese is not re-embedded every
time a campaign changes, and campaign retrieval is not polluted by contract text
that lexically resembles it (`taksit`, `vade`, `kâr payı` appear in both).

### Payload

```python
{
  # ---- identity ----
  "doc_id":        "sha1(canonical_url)[:16]",
  "chunk_id":      "{doc_id}:{i}",
  "chunk_index":   0,
  "chunk_total":   1,

  # ---- provenance ----
  "url":           "https://www.kuveytturk.com.tr/kampanyalar/...",   # canonicalised
  "bank_slug":     "kuveytturk",        # keyword index
  "bank_name":     "Kuveyt Türk",
  "source_type":   "page",              # page | pdf
  "crawled_at":    "2026-08-08",

  # ---- taxonomy, derived from URL path ----
  "doc_kind":      "campaign",          # campaign|product|legal|blog|corporate|faq
  "section":       "kampanyalar",
  "audience":      "bireysel",          # bireysel|ticari|kobi|ozel|null
  "category":      "kart-kampanyalari",

  # ---- content ----
  "title":         "Colin's'de Vade Farksız 4 Aya Varan Taksit Fırsatı",  # suffix stripped
  "heading_path":  "Colin's'de Vade Farksız… > Kampanya Koşulları",
  "lang":          "tr",

  # ---- campaign-only, nullable ----
  "campaign_start": "2026-08-06",
  "campaign_end":   "2026-12-31",
  "is_active":      true,               # computed vs crawled_at
}
```

Create payload indexes on `bank_slug`, `doc_kind`, `section`, `is_active`, and a
range index on `campaign_end`. Without these, filtered search degrades to a full
scan.

The URL path already yields a clean taxonomy for free — the depth-1 segments are
`/kampanyalar` (485), `/blog` (715), `/hakkimizda` (632), `/kendim-icin` (291),
`/isim-icin` (241), `/ozel-bankacilik` (192), `/bireysel` (115), `/ticari` (82).
No LLM classification needed for `section`/`audience`.

### Chunking

- **Campaign docs: do not split.** Median 1,473 chars ≈ 370 tokens, comfortably
  inside bge-m3's window. Splitting divorces dates from conditions.
- **Everything else:** markdown-header-aware split (`MarkdownHeaderTextSplitter`)
  then recursive character split at ~1,800 chars / ~200 overlap, carrying
  `heading_path` into the payload.
- **Embedding text:** prepend `"{bank_name} — {title}\n\n"` to each chunk so a
  chunk from the middle of a document still carries its bank and subject.
  **Do not** prepend `description` (§2).
- Strip `](url)` targets, keeping anchor text, before embedding.

---

## 7. Open questions

1. **Index the contract PDFs?** They are 65% of the corpus and ~32k chunks of
   legalese. They answer "what does the Genel Kredi Sözleşmesi say about early
   repayment" but nothing about campaigns. Recommend deferring to a third
   collection built later, not blocking the campaign index on them.
2. **Expired campaigns — drop or keep?** Recommend keeping with
   `is_active: false` so the agent can answer "that campaign ended on X" rather
   than "no such campaign", but filtering them out of default retrieval.
3. **Confirm the embedding model.** `bge-m3` is configured but not downloaded,
   while a Turkish-tuned `Trendyol/TY-ecomm-embed-multilingual-base` and
   `Qwen3-Embedding-8B` are already cached. Worth a quick retrieval bake-off on
   ~30 real campaign questions before committing to a multi-hour CPU index.
4. **Re-crawl before indexing?** The corpus has a known ~300–400 page gap at
   turkiyefinans from a filename-collision bug (see git history / prior session),
   and 77% of dated campaigns are already stale. If a re-crawl is planned, doing
   it before the first index avoids embedding twice.
