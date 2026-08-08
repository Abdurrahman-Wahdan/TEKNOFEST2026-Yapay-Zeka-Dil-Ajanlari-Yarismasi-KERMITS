# TF26

NLP system for Turkish participation-banking campaigns: collect campaign text from bank
websites, extract the financial details, and make products comparable through a dashboard and
a chatbot.

Everything runs locally — three LLMs on a local vLLM host, embeddings in-process, Qdrant in
Docker. No external API, no data leaving the machine.

## Setup

```bash
pyenv activate tf26
```

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

```bash
docker run -d --name qdrant -p 6333:6333 -v "$HOME/qdrant_storage:/qdrant/storage" qdrant/qdrant
```

## Use

```python
from llm import get_llm
from embeddings import get_embedding
from vector_stores import ensure_collection, get_vector_store

get_llm("extractor").invoke("...")

emb = get_embedding()
ensure_collection("campaigns")
store = get_vector_store("campaigns", emb)
```

## Models

| key | model | role |
|---|---|---|
| `gemma` | google/gemma-4-31B-it | chat — fastest, cleanest Turkish |
| `qwen` | Qwen/Qwen3.6-27B | extraction — best structured output |
| `gpt` | openai/gpt-oss-20b | reasoning |

Roles are set in `.env`, so swapping a model needs no code change.

## Tests

```bash
pytest tests/unit -q
```

```bash
pytest tests/integration -q
```

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layout, and how to add a provider
- [docs/FINDINGS.md](docs/FINDINGS.md) — measured model behaviour, vLLM launch flags, MCP
  traps, and the reverse-engineered bank calculator endpoint
