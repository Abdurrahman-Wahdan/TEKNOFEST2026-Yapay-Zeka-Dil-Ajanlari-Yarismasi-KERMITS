# Architecture

How the repo is laid out and where to change things.

## Layout

```
config/settings.py      every configurable value, one flat file
llm/                    chat models
embeddings/             embedding models
vector_stores/          Qdrant collections
banks/                  live pricing from the banks' own calculators
docs/FINDINGS.md        measured behaviour of the models and bank endpoints
tests/unit/             no network
tests/integration/      real vLLM, Qdrant and bank endpoints
```

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
finance quote, and adds three modules the others do not need: `models.py` (the
shape every bank maps onto), `http.py` (the shared clients) and `tools.py` (the
tools the agent binds). **Adding a bank must not add a tool** — there is one tool
per product category and `bank` is a parameter, so ten banks stay seven tools
rather than becoming forty.

## Adding a provider

One new file, one new list entry. Nothing else changes.

1. Write `providers/<name>_provider.py` subclassing the package's base class.
2. Add an instance to `PROVIDERS` in `providers/__init__.py`.
3. Add whatever settings it needs to `config/settings.py` (an API key belongs here, and only
   at this point — we don't carry unused key fields).

Ordering in `PROVIDERS` matters: the first provider whose `matches()` returns `True` wins.
`LocalProvider` in `embeddings/` matches everything as a fallback, so any API provider must be
listed **before** it.

`banks/` is the same three steps against `providers/<bank>.py` and `BANKS`, plus one
rule: declare `capabilities` honestly. A bank that publishes no card calculator
leaves `"card"` out and inherits a refusal, because answering with nothing is
indistinguishable from a broken endpoint.

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
- **A zero is not a price.** Both banks answer an unsupported product/currency
  combination with `200` and every field `0.0`. The providers raise instead of
  reporting a real product as paying nothing.
- **Kuveyt Türk's profit share counts days, not months**, whatever its `p10`
  flag claims — see the correction in `docs/discovery/captured/kuveytturk.md`.
  Months are sent as 30-day multiples; reading the field as months understates a
  year by about thirty times.
- **Albaraka needs `curl_cffi`.** Its WAF fingerprints the TLS handshake, so
  httpx is rejected whatever the headers. `banks/http.py` keeps one client per
  transport.
- **We never compute a price.** The one agreed exception is Kuveyt Türk's
  currency and gold conversion, which has no endpoint; it is done in `Decimal`
  and flagged `derived=True` so a caller can tell it from a bank's own figure.

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
