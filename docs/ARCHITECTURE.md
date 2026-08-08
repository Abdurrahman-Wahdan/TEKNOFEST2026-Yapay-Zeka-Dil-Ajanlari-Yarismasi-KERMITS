# Architecture

How the repo is laid out and where to change things.

## Layout

```
config/settings.py      every configurable value, one flat file
llm/                    chat models
embeddings/             embedding models
vector_stores/          Qdrant collections
docs/FINDINGS.md        measured behaviour of the models and bank endpoints
tests/unit/             no network
tests/integration/      real vLLM and Qdrant
```

## The three factory packages are the same shape

`llm/`, `embeddings/` and `vector_stores/` are deliberately identical:

```
<package>/
├── factory.py            the public function callers use
├── __init__.py           re-exports it
└── providers/
    ├── base.py           the ABC a provider implements
    ├── __init__.py       PROVIDERS list + get_provider()
    └── <name>_provider.py
```

Learn one and you know all three. Each factory returns a **plain LangChain object**
(`BaseChatModel`, `Embeddings`, `VectorStore`) — never a project wrapper — so anything in the
LangChain or LangGraph ecosystem works unchanged.

## Adding a provider

One new file, one new list entry. Nothing else changes.

1. Write `providers/<name>_provider.py` subclassing the package's base class.
2. Add an instance to `PROVIDERS` in `providers/__init__.py`.
3. Add whatever settings it needs to `config/settings.py` (an API key belongs here, and only
   at this point — we don't carry unused key fields).

Ordering in `PROVIDERS` matters: the first provider whose `matches()` returns `True` wins.
`LocalProvider` in `embeddings/` matches everything as a fallback, so any API provider must be
listed **before** it.

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

## Tests

```bash
pytest tests/unit -q          # nothing needs to be running
pytest tests/integration -q   # needs vLLM, and Qdrant on :6333
```

Embedding tests skip unless the model is already in the HuggingFace cache — no test downloads
gigabytes. Warm it with `python -c "from embeddings import get_embedding; get_embedding()"`.

Every module-level cache exposes a `clear_*` function, and `tests/conftest.py` calls them around
each test so state cannot leak between them.
