# Architecture

How the repo is laid out and where to change things.

## Layout

```
config/settings.py      every configurable value, one flat file
llm/                    chat models
embeddings/             embedding models
vector_stores/          Qdrant collections
banks/                  live pricing from ten banks' own calculators
corpus/                 crawl, clean and standardise the ten banks' websites
docs/FINDINGS.md        measured behaviour of the models and bank endpoints
tests/unit/             no network
tests/integration/      real vLLM, Qdrant and bank endpoints
```

## corpus/ — the crawled website corpus

`banks/` prices products live; `corpus/` is the other half — the campaigns,
product pages, fees and PDFs the banks publish, cleaned into one artifact ready
to embed. It runs nightly (`python -m corpus.build`) and is incremental: an
unchanged page costs a conditional GET and nothing else.

```
corpus/
├── sites.py       the ten sites as data (replaces ten copied crawler scripts)
├── urls.py        canonicalisation and stable doc ids
├── fetch.py       one crawl engine: conditional GET, robots, PDF discovery
├── store.py       content-addressed raw bytes + manifest + garbage collection
├── extract.py     HTML -> clean text and citable sections (anchors from the HTML)
├── pdf_policy.py  which PDFs are worth reading; a model classifies the ambiguous
├── pdf_extract.py pdftotext for numbers, a vision model for layout and scans
├── quality.py     the detectors that refuse rather than emit garbage
├── clean.py       one function per measured content defect
├── classify.py    doc_kind / audience / section, from the URL path
├── dates.py       campaign validity dates (the expiry-filter correctness gate)
├── report.py      what a run did, and the gates that let it publish
├── build.py       the orchestrator and `python -m corpus.build`
└── schedule.py    prints a cron line / launchd plist; never installs
```

Three rules worth knowing before changing it:

- **Change is decided on cleaned text, not raw bytes.** Bank pages carry a
  rotating WAF token and an FX timestamp, so their bytes churn every run while
  the words do not. `text_hash` is the semantic key; a document with an
  unchanged `text_hash` is carried forward byte-identical, so the embedder can
  skip it.
- **A bad run publishes nothing.** If the corpus would shrink past a threshold,
  or a site that had documents yields none, the gate refuses and yesterday's
  `documents.jsonl` stays in place. Stale but correct beats fresh but empty.
- **Nothing computes `is_active`.** A campaign's expiry is `end >= today`, and
  today moves without the document changing, so it is computed at query time
  from the stored `campaign_end`, never baked into the artifact.

PDF text comes from poppler (`pdftotext`, `pdfinfo`, `pdftoppm` via
`subprocess`), not `pypdf` — measured wrong on this corpus — and not PyMuPDF,
whose AGPL network clause is a hazard for a bank-facing service.

## The four factory packages are the same shape

`llm/`, `embeddings/`, `vector_stores/` and `banks/` are deliberately identical:

```
<package>/
├── factory.py            the public function callers use
├── __init__.py           re-exports it
└── providers/
    ├── base.py           the ABC a provider implements
    ├── __init__.py       PROVIDERS list + get_provider()
    └── <name>_provider.py
```

Learn one and you know all four. The first three return a **plain LangChain object**
(`BaseChatModel`, `Embeddings`, `VectorStore`) — never a project wrapper — so anything in the
LangChain or LangGraph ecosystem works unchanged.

`banks/` returns dataclasses instead, because there is no LangChain type for a
finance quote, and adds four modules the others do not need: `models.py` (the
shape every bank maps onto), `parse.py` (one number parser for all of them),
`http.py` (the shared clients) and `tools.py` (the tools the agent binds).
**Adding a bank must not add a tool** — there is one tool per product category
and `bank` is a parameter, so ten banks are seven tools rather than forty.

Nothing on `BaseBank` is abstract. Every method refuses by default, naming what
the bank does publish, and a provider overrides only what its bank really
answers. That is what lets Adil and T.O.M. be registered banks with no endpoints
rather than absent ones, and it means a gap is always a sentence rather than a
crash or an empty result.

**[BANK_TOOLS.md](BANK_TOOLS.md) is the design document for this package** — the
tool contract, how to add a bank, how to add a tool, and the traps each one has
already cost. Read it before changing `banks/`.

### The ten banks

| bank | publishes | transport | catalogue |
|---|---|---|---|
| Kuveyt Türk | finance, profit share, card, rates, convert | httpx | an endpoint, five `p1` values |
| Albaraka | finance, profit share, rates, convert | curl_cffi (WAF) | page HTML, echoed back verbatim |
| Vakıf | finance, profit share, card, convert | httpx + CSRF | page `<option>` values |
| Emlak | finance, profit share | curl_cffi (WAF) | page `<option>` values |
| Dünya | finance, profit share, convert | httpx + CSRF | homepage HTML, JSON blobs |
| Ziraat | finance | httpx | an endpoint, per product |
| Türkiye Finans | products only | httpx | a table service |
| Hayat | profit share, rates, convert | httpx | none — three account types |
| T.O.M. | nothing | none | — |
| Adil | nothing | none | — |

Two rules hold this together and both are enforced by unit tests:

- **`capabilities` is a promise.** Declaring one without implementing its method,
  or implementing without declaring, fails the capability test. An override that
  only explains a refusal in better words — as Türkiye Finans does, naming the
  rate it publishes even though it states no instalment — is marked `@refusal`
  and does not count as a capability.
- **`transport` is declared, never hardcoded in a provider.** `httpx` is plain,
  `csrf` adds a per-page anti-forgery token, `impersonate` is curl_cffi for the
  two hosts whose WAF fingerprints the TLS handshake, and `none` is a bank with
  nothing to call. The health checker reads it to know which banks are cheap to
  poll, and `curl_cffi` is never the default — it is slower, and httpx is the
  project's client everywhere else.

## Adding a provider

One new file, one new list entry. Nothing else changes.

1. Write `providers/<name>_provider.py` subclassing the package's base class.
2. Add an instance to `PROVIDERS` in `providers/__init__.py`.
3. Add whatever settings it needs to `config/settings.py` (an API key belongs here, and only
   at this point — we don't carry unused key fields).

Ordering in `PROVIDERS` matters: the first provider whose `matches()` returns `True` wins.
`LocalProvider` in `embeddings/` matches everything as a fallback, so any API provider must be
listed **before** it.

`banks/` is the same three steps against `providers/<bank>.py` and `BANKS`, plus
two rules: declare `capabilities` honestly, and declare `transport`. A bank that
publishes no card calculator leaves `"card"` out and inherits a refusal, because
answering with nothing is indistinguishable from a broken endpoint. A bank with
no endpoints at all is still registered — `list_banks` has to be able to say
"this bank publishes no calculator", and `notes` carries why.

## Configuration

`config/settings.py` holds one flat `Settings`. Paths are anchored to the file, so settings
load identically from pytest, a CLI run, or a server.

Roles (`DEFAULT_MODEL`, `CHAT_MODEL`, `EXTRACTOR_MODEL`, `REASONER_MODEL`) map a job to a model
key, so model choice moves in `.env` without touching code. A validator rejects a role pointing
at a model that does not exist — a typo fails at startup, not mid-run.

`.env.example` mirrors the settings file, and a unit test asserts every key in it resolves to a
real field.

## Usage

```python
from llm import get_llm
from embeddings import get_embedding
from vector_stores import ensure_collection, get_vector_store

get_llm().invoke("Merhaba")                       # DEFAULT_MODEL
get_llm("extractor")                              # by role
get_llm("gemma", temperature=0.7)                 # by model key

emb = get_embedding()
ensure_collection("campaigns")
store = get_vector_store("campaigns", emb)

from banks import build_tools, get_bank

get_bank("kuveytturk").finance_quote("ihtiyaç finansmanı", 100000, 24)
get_llm().bind_tools(build_tools())               # the seven bank tools
```

## Behaviour worth knowing before you change it

These are measured, not assumed. Full detail in [FINDINGS.md](FINDINGS.md).

- **Use `method="function_calling"` for extraction.** `json_schema` invents values for absent
  fields; `function_calling` returns `None`. `json_mode` does not enforce the schema at all.
- **Thinking is disabled where it pollutes `content`.** qwen reasons by default and mixes it in;
  the provider turns it off (433 output tokens → 36). Pass `thinking=True` to keep it.
- **gpt-oss returns empty content below 300 `max_tokens`** with no exception. The provider
  raises instead of letting that through.
- **Collections are checked against `EMBEDDING_DIMENSIONS`.** Opening one with a mismatched size
  raises rather than writing vectors that fail later.
- **"No data" has four shapes and none is an HTTP error**: `200` with all-zero
  fields, `200` with an empty body, `200` with an `errorMessage` inside the JSON,
  and `404` with an empty body. A check that reads only the status code calls all
  four healthy. Every one of them raises instead of reporting a real product as
  paying nothing — and Hayat's floor is checked before the call, so someone below
  50 000 TL is told the minimum rather than quoted "0 TL".
- **Product identity is rarely just a code.** Albaraka repeats `ProductCode`
  across nine products, Türkiye Finans repeats `Code` across `CreditID`s with
  different fees, and Ziraat lists the same product once per term band with a
  ceiling that falls as the term rises. Each provider keys on a bank-supplied
  identity and keeps the raw catalogue entry on the `Product`, because Albaraka
  and Dünya need it echoed back as a request parameter.
- **Amounts go out as bare integers.** Dünya strips dots as thousands
  separators, so `"100000.00"` is read as ten million and answers with a
  plausible instalment a hundred times too large, with no error.
- **Kuveyt Türk's profit share counts days, not months**, whatever its `p10`
  flag claims — see the correction in `docs/discovery/captured/kuveytturk.md`.
  Months are sent as 30-day multiples; reading the field as months understates a
  year by about thirty times.
- **Albaraka and Emlak need `curl_cffi`.** Their WAF fingerprints the TLS
  handshake, so httpx is rejected whatever the headers. `banks/http.py` keeps one
  client per transport and deliberately does not set a user-agent when
  impersonating: curl_cffi sends one matching the fingerprint, and a mismatched
  pair is rejected again — as a JSON decode error, not an obvious block.
- **We never compute a price.** The one agreed exception is currency and gold
  conversion at the banks that publish rates but no converter (Kuveyt Türk and
  Hayat); it is done in `Decimal` and flagged `derived=True` so a caller can tell
  it from a bank's own figure. It is also why Türkiye Finans quotes nothing: it
  publishes a rate table and does the annuity in the browser, so an instalment
  would have to be ours rather than the bank's.

## Tests

```bash
pytest tests/unit -q          # nothing needs to be running
pytest tests/integration -q   # needs vLLM, Qdrant on :6333, and the internet
```

Bank unit tests run against payloads recorded from the live endpoints into
`tests/fixtures/banks/`. The probe captures in `docs/discovery/captured/` are not
usable for this: they truncate every response at 6000 characters, so the larger
ones are not valid JSON. Bank integration tests assert the contract — field
present, type right, value in a sane range — never an exact number, because
rates change daily and that change is not a failure.

Embedding tests skip unless the model is already in the HuggingFace cache — no test downloads
gigabytes. Warm it with `python -c "from embeddings import get_embedding; get_embedding()"`.

Every module-level cache exposes a `clear_*` function, and `tests/conftest.py` calls them around
each test so state cannot leak between them.
