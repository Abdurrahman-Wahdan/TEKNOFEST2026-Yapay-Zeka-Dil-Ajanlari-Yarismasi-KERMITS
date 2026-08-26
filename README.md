<div align="center">
  <img src="UI/public/vision/images/kermits-logo.png" alt="Kermits AI — TEKNOFEST 2026" width="180" />

  <h1>KERMİTS</h1>

  <p><strong>Evidence-first participation banking intelligence for Türkiye.</strong></p>
  <p>Compare products, investigate official bank sources, converse with bank-bound AI specialists, and turn decisions into reusable tables and scheduled reports.</p>

  [![Competition](https://img.shields.io/badge/TEKNOFEST_2026-Yapay_Zeka_Dil_Ajanları_Yarışması-1599e8?style=for-the-badge)](https://www.teknofest.org/tr/yarismalar/yapay-zeka-dil-ajanlari-yarismasi/)

  [![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
  [![Next.js](https://img.shields.io/badge/Next.js-16-000000?style=flat-square&logo=nextdotjs&logoColor=white)](https://nextjs.org/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
  [![LangGraph](https://img.shields.io/badge/LangGraph-multi--agent-1C3C3C?style=flat-square)](https://langchain-ai.github.io/langgraph/)
  [![License](https://img.shields.io/badge/License-Apache_2.0-D22128?style=flat-square)](LICENSE)

  [Features](#features) · [Screenshots](#screenshots) · [Quick start](#quick-start) · [Architecture](#architecture) · [Configuration](#configuration) · [Testing](#testing)
</div>

---

Kermits AI is a Turkish participation-banking research and comparison platform. It combines live bank calculators, an indexed official-document corpus, optional public-web research, and a bank-isolated multi-agent system. Every bank specialist works only within its assigned bank, records the provenance of useful evidence, and hands supported findings to a supervisor that produces the user-facing answer.

The product is designed around one principle: **live values come from live endpoints, durable product knowledge comes from the official-source corpus, and web research is a supporting tool—not a replacement for either.**

> [!IMPORTANT]
> This repository is a research and competition project, not financial advice. Bank rates, fees, campaigns, and eligibility conditions can change. Always verify a decision against the cited official source.

## Table of contents

- [Features](#features)
- [Screenshots](#screenshots)
- [How the agent works](#how-the-agent-works)
- [Architecture](#architecture)
- [Technology](#technology)
- [Quick start](#quick-start)
- [Model gateway](#model-gateway)
- [Knowledge base setup](#knowledge-base-setup)
- [Turkish voice input](#turkish-voice-input)
- [Configuration](#configuration)
- [API and useful commands](#api-and-useful-commands)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [Security and public-repository hygiene](#security-and-public-repository-hygiene)
- [Contributors](#contributors)
- [License](#license)

## Features

### Evidence-first banking assistant

- A supervisor delegates bank-specific work to **10 isolated specialists**.
- Specialists prefer indexed official documents and live bank endpoints.
- Optional SearXNG research discovers and reads additional public bank pages.
- Useful evidence is returned with provenance and rendered as clickable citations.
- Sources are grouped by origin, including the knowledge base, online research, and internal product tables.
- A final output guard checks editable participation-banking and disclosure policies before an answer is stored or shown.

### Live comparison

- Compare financing, profit-share, foreign-exchange, precious-metal, card, and reward data.
- Discover product families dynamically instead of relying on a frozen UI catalogue.
- Fan out requests across supported banks concurrently.
- Keep customer-entered scenarios visibly separate from bank-reported facts.

### Research workspace

- Browse 260+ prepared product-comparison tables.
- Attach a table directly to a conversation.
- Save useful Markdown tables generated in chat.
- Search campaigns and official product pages.
- Let the assistant inspect the current application page when explicitly enabled.

### Multimodal chat

- Upload images, Markdown, and plain-text files directly.
- Convert PDF and DOCX pages to images privately before model inference.
- Mention attached files with `@`.
- Record Turkish speech and transcribe it locally with Whisper large-v3 on Apple Silicon.
- Receive context-aware next-message recommendations that can be inserted into the composer.

### Personal workflow

- Persistent chat history backed by PostgreSQL and LangGraph checkpoints.
- Scheduled automations and report notifications.
- Saved views and comparison tables.
- Turkish light and dark interfaces with responsive navigation.

### Covered banks

Kuveyt Türk, Albaraka Türk, Vakıf Katılım, Türkiye Emlak Katılım, Dünya Katılım, Ziraat Katılım, Türkiye Finans, Hayat Finans, T.O.M. Katılım, and Adil Katılım.

## Screenshots

### Cited research, reusable tables, and automations

The assistant can combine official-source evidence, produce a comparison table, expose the supporting links, and create a scheduled follow-up in the same conversation.

![Cited multi-bank research and automation](docs/screenshots/chat-research.png)

### Model, thinking, and web controls

Advanced controls expose clean model names and let the user enable reasoning or online research per request. Voice, attachments, page context, and recommendation insertion remain in the main composer.

![Chat advanced controls](docs/screenshots/chat-advanced.png)

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/compare.png" alt="Live comparison workspace" /></td>
    <td width="50%"><img src="docs/screenshots/products.png" alt="Product comparison table catalogue" /></td>
  </tr>
  <tr><td align="center"><strong>Live comparison</strong></td><td align="center"><strong>Product-table catalogue</strong></td></tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/campaigns.png" alt="Campaign discovery" /></td>
    <td width="50%"><img src="docs/screenshots/profile-automations.png" alt="Profile, reports, and automations" /></td>
  </tr>
  <tr><td align="center"><strong>Campaign discovery</strong></td><td align="center"><strong>Profiles, reports, and automations</strong></td></tr>
</table>

## How the agent works

1. The supervisor reads the conversation, attachments, selected table, and enabled capabilities.
2. It delegates only the relevant banks to bank-bound specialists.
3. Each specialist decides which of its own tools are appropriate:
   - live endpoints for current quotes and rates;
   - Qdrant retrieval for indexed official documents;
   - SearXNG and page reading as extra research when enabled and useful.
4. Specialists return supported findings plus only the sources that materially helped form those findings.
5. The supervisor synthesizes one answer without exposing internal tool or agent implementation details.
6. The output guard reads the finished answer against an editable rule set and returns a verdict.
7. A failed verdict hands the turn back to the supervisor once; the guard never rewrites the answer and never blocks it.
8. The published answer is streamed to the UI, persisted in PostgreSQL, and retained in the LangGraph checkpoint.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Next.js chat
    participant API as FastAPI
    participant S as Supervisor
    participant B as Bank specialist
    participant L as Live bank endpoint
    participant Q as Qdrant campaigns
    participant W as SearXNG + reader
    participant G as Output guard

    User->>UI: Ask, attach, speak, or select a table
    UI->>API: Authenticated streaming request
    API->>S: Conversation + enabled capabilities
    S->>B: ask_bankname — one delegated request per relevant bank
    par Current values
        B->>L: finance_quote / profit_share_quote / exchange_rates
        L-->>B: Live figure + official page URL
    and Published documents
        B->>Q: search_bank filtered to this bank
        Q-->>B: Chunks + url, type, validity
    and Optional research
        B->>W: search_bank_web / read_bank_source
        W-->>B: Page evidence + retrieved_at
    end
    B-->>S: Findings + the citations that were actually used
    S->>G: Finished answer + specialist handoffs as evidence
    alt Verdict passes
        G-->>API: Publish
    else Verdict fails once
        G-->>S: What to fix
        S->>G: Second attempt
        G-->>API: Publish the second attempt either way
    end
    API-->>UI: SSE tokens, citations, tables, suggestions
    UI-->>User: Final persisted answer
```

### The agent roster

Seven kinds of agent run in this system. Only the first two touch bank facts; the rest
shape, schedule, or check what those two produced.

```mermaid
flowchart LR
    subgraph Fact[Agents that establish bank facts]
        SUP["Supervisor<br/>agents/main<br/>thread session:main"]
        SPEC["10 bank specialists<br/>agents/&lt;bank&gt;<br/>thread session:bank:&lt;bank&gt;"]
    end
    subgraph Check[Agent that checks the answer]
        GUARD["Output guard<br/>agents/output_guard<br/>stateless, policies.json"]
    end
    subgraph Assist[One-shot agents behind product features]
        REC["Recommendation<br/>next questions to ask<br/>thread session:recommendation"]
        TOV["Table overview<br/>what one table shows<br/>cached per source_hash + model"]
        TMD["Table metadata<br/>names a saved table"]
        AUT["Automation draft<br/>free text to a schedule"]
    end

    SUP -->|delegates one bank at a time| SPEC
    SPEC -->|private findings| SUP
    SUP --> GUARD
    GUARD -->|fail, once| SUP

    Chat[Chat turn] --> SUP
    Suggest[Suggestion chips] --> REC
    Page[Comparison page opened] --> TOV
    Save[Table saved to dashboard] --> TMD
    Sched[Automation typed in a box] --> AUT
```

A specialist is bound to its bank before LangChain ever sees a tool, so it cannot be
prompted into answering for another bank. Each one keeps its own private thread and its
own compaction window; the supervisor sees only the specialist's final response.

### The tool surface

The supervisor holds no bank tool at all. Everything factual arrives through a specialist.

```mermaid
flowchart TB
    subgraph SupTools[Supervisor tools]
        A1["ask_kuveytturk … ask_adil<br/>10 bank specialists"]
        A2["find_comparison_table<br/>page directory, never a citation"]
        A3["create_automation<br/>update_automation<br/>list_automations"]
    end
    subgraph SpecTools[Bank specialist tools, bound to one bank]
        L["Live endpoints — capability gated<br/>list_products · finance_quote<br/>profit_share_quote · exchange_rates<br/>card_installment_quote · convert_currency<br/>mile_earning_rates · check_live_endpoint_health"]
        R["Corpus retrieval — always present<br/>search_bank · expand_chunk · read_full_page"]
        WB["Web research — only when the user enables it<br/>search_bank_web · read_bank_source"]
    end

    A1 --> SpecTools
    A2 --> TP[("data/_tables<br/>offline table pool")]
    A3 --> PG[("PostgreSQL<br/>automations")]
    L --> BANKAPI["Official bank calculators and feeds"]
    R --> QD[("Qdrant<br/>campaigns collection")]
    WB --> SX[("SearXNG<br/>+ bounded page/PDF reader")]
```

Two gates decide what a specialist is actually handed:

| Gate | Effect |
|---|---|
| `bank.capabilities` | A bank with no published calculator is never given one. Adil Katılım has no live tools; Kuveyt Türk has all seven. |
| Advanced web toggle | `search_bank_web` and `read_bank_source` are absent from the schema when the user leaves it off, so no prompt can reach the network. |

Retrieval and web tools carry a per-turn call ceiling; live endpoint tools do not, because a
question that needs six quotes should make six calls.

## Architecture

```mermaid
flowchart TB
    subgraph Client[Next.js 16 client]
        Pages["Compare · Products · Campaigns<br/>AI Overview · Dashboard · Profile"]
        Chat["Multimodal composer<br/>text · files · voice · page capture"]
    end
    subgraph Service[FastAPI application]
        Auth[JWT authentication]
        Routes[REST · SSE · WebSocket]
        Loop[Automation scheduler]
        Voice[Whisper transcription]
    end
    subgraph AgentLayer[Agent layer]
        SUP[Supervisor]
        SPEC[10 bank specialists]
        GUARD[Output guard]
        ONE[One-shot agents]
    end
    subgraph Stores[State and evidence]
        PG[("PostgreSQL 17<br/>app rows + LangGraph checkpoints")]
        QD[("Qdrant<br/>campaigns · bank_chunks")]
        SX[("SearXNG")]
        BANKAPI["Official bank APIs and pages"]
    end
    subgraph Models["Model gateway — OpenAI-compatible, vLLM"]
        CHAT["Gemma 4 31B IT · Qwen 3.6 27B · GPT-OSS 20B"]
        EMB["Qwen3 Embedding 0.6B"]
        WSP["Whisper large-v3, local MLX"]
    end

    Chat --> Routes
    Pages --> Routes
    Routes --> SUP
    Routes --> ONE
    Auth --> PG
    Loop --> PG
    Loop --> SUP
    Voice --> WSP
    SUP --> SPEC
    SUP --> GUARD
    SPEC --> BANKAPI
    SPEC --> QD
    SPEC --> SX
    AgentLayer --> PG
    AgentLayer --> CHAT
    QD --- EMB
```

### Where state lives

Two stores, and they answer different questions. PostgreSQL holds what the product
remembers about a user; Qdrant holds what the banks have published.

```mermaid
flowchart LR
    subgraph PGS["PostgreSQL — what the product remembers"]
        T1["users · profiles"]
        T2["chat_sessions · chat_messages"]
        T3["saved_views · table_overviews"]
        T4["automations · automation_reports"]
        T5["LangGraph checkpoints<br/>one thread per agent per session"]
    end
    subgraph QDS["Qdrant — what the banks published"]
        C1["campaigns<br/>read by the specialists<br/>filtered by bank, ids are uuid5 of url + chunk"]
        C2["bank_chunks<br/>read by the /search endpoint<br/>campaign feed and citation panel"]
    end

    Corpus["Crawled bank sites<br/>pages · PDFs · banner images"] --> Clean["Clean and classify"]
    Clean --> Chunk["Chunk and embed"]
    Chunk --> C1
    Chunk --> C2

    Auth2[Sign-in] --> T1
    ChatTurn[Chat turn] --> T2
    ChatTurn --> T5
    Dash[Dashboard save] --> T3
    Auto[Automation run] --> T4

    SpecA[Bank specialist] --> C1
    RestA["GET /search"] --> C2
```

Deleting a chat removes the supervisor thread, the recommendation thread, and all ten bank
threads together, so no specialist keeps a memory of a conversation the user erased.

### Source priority

```mermaid
flowchart LR
    Request[User request] --> Decide{What evidence is needed?}
    Decide -->|Current numeric value| Live["Live bank endpoint<br/>carries retrieved_at"]
    Decide -->|Product rules and documents| KB["Qdrant campaigns corpus<br/>published, not a quote"]
    Decide -->|Breadth or explicit online research| Web["Bank-scoped web research<br/>only when enabled"]
    Live --> Cite[Claim + its exact source URL]
    KB --> Cite
    Web --> Cite
    Cite --> Answer[Supervisor synthesis]
    Answer --> Guard{Output guard verdict}
    Guard -->|pass| Publish[Published answer]
    Guard -->|fail once| Answer
```

## Technology

| Layer | Main components |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript 5, Material UI, Tailwind CSS 4, next-intl |
| API | FastAPI, Pydantic, SQLAlchemy 2, Alembic, Server-Sent Events, WebSockets |
| Agents | LangGraph, LangChain, persistent PostgreSQL checkpoints, bank-bound specialists |
| Models | `google/gemma-4-31B-it`, `Qwen/Qwen3.6-27B`, `openai/gpt-oss-20b` via vLLM |
| Retrieval | Qdrant, `Qwen/Qwen3-Embedding-0.6B`, bank-filtered metadata |
| Search | Self-hosted SearXNG and bounded page/PDF extraction |
| Voice | `openai/whisper-large-v3` converted to a local 4-bit MLX checkpoint |
| Documents | PyMuPDF, Poppler, Pillow; PDF/DOCX pages become model-ready images |
| State | PostgreSQL 17 for users, chat, saved tables, automations, reports, and checkpoints |
| Runtime | Docker Compose for PostgreSQL, Qdrant, and SearXNG |

## Quick start

### Prerequisites

- macOS or Linux
- Python **3.13.2**
- Node.js **20.9+** and npm
- Docker Desktop or Docker Engine with Compose v2
- `openssl`
- An OpenAI-compatible model gateway described in [Model gateway](#model-gateway)
- Recommended for document ingestion: Poppler (`brew install poppler` or `sudo apt install poppler-utils`)

### Automated setup

```bash
git clone https://github.com/Abdurrahman-Wahdan/TF26.git
cd TF26
bash scripts/setup_local.sh
```

The setup script creates `.venv`, installs Python and frontend dependencies, creates `.env` once with a generated JWT secret, starts PostgreSQL/Qdrant/SearXNG, waits for PostgreSQL, and runs Alembic migrations. It never overwrites an existing `.env`.

Set the model gateway in `.env`:

```dotenv
VLLM_BASE_URL=http://YOUR_MODEL_HOST:PORT
VLLM_API_KEY=EMPTY
```

Then start the API and UI together:

```bash
bash scripts/dev.sh
```

Open:

- Application: <http://127.0.0.1:3000/tr>
- API documentation: <http://127.0.0.1:8000/docs>
- API readiness: <http://127.0.0.1:8000/api/ready>
- SearXNG: <http://127.0.0.1:8888>
- Qdrant dashboard: <http://127.0.0.1:6333/dashboard>

Create an account from the sign-up screen; no seed account is required.

### Manual setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set API_JWT_SECRET plus VLLM_BASE_URL.

npm --prefix UI ci
docker compose up -d postgres qdrant searxng
alembic upgrade head
```

Run the services in two terminals:

```bash
source .venv/bin/activate
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
npm --prefix UI run dev -- --hostname 127.0.0.1
```

## Model gateway

The API expects one OpenAI-compatible base URL with model-specific routes:

| Display name | Model ID | Route | Purpose |
|---|---|---|---|
| Gemma 4 31B IT | `google/gemma-4-31B-it` | `/gemma/v1` | Default Turkish chat, vision, output guard |
| Qwen 3.6 27B | `Qwen/Qwen3.6-27B` | `/qwen/v1` | Structured extraction |
| GPT-OSS 20B | `openai/gpt-oss-20b` | `/gpt/v1` | Alternative reasoning model |
| Qwen3 Embedding 0.6B | `Qwen/Qwen3-Embedding-0.6B` | `/embed/v1` | Remote embedding mode |

The measured vLLM tool-calling flags are important:

```bash
vllm serve google/gemma-4-31B-it --enable-auto-tool-choice \
  --tool-call-parser gemma4 --reasoning-parser gemma4 \
  --chat-template examples/tool_chat_template_gemma4.jinja

vllm serve Qwen/Qwen3.6-27B --enable-auto-tool-choice \
  --tool-call-parser hermes --reasoning-parser qwen3

vllm serve openai/gpt-oss-20b --enable-auto-tool-choice \
  --tool-call-parser openai --reasoning-parser openai_gptoss
```

These models require substantial accelerator memory. They may run on a separate GPU workstation; only the gateway URL must be reachable from the API host. For frontend/API development without generated answers, PostgreSQL, Qdrant, and SearXNG can still run locally.

For local embeddings instead of `/embed/v1`:

```dotenv
EMBEDDING_PROVIDER=local
EMBEDDING_DEVICE=mps  # use cuda or cpu on other machines
```

## Knowledge base setup

The multi-gigabyte corpus and Qdrant storage are intentionally excluded from Git. A new clone starts with an empty knowledge base until the corpus is built or a prepared snapshot is restored.

### Build official-source documents

```bash
source .venv/bin/activate

# Faster website pass; queues PDFs for the slower vision pass.
python -m corpus --pages-only

# Process queued PDFs when the vision model is available.
python -m corpus --pdfs
```

For a single bank during development:

```bash
python -m corpus --site kuveytturk --limit 50
```

### Index into Qdrant

```bash
python -m index --no-write  # inspect the planned diff
python -m index             # embed and apply it
```

Both pipelines are incremental. Corpus publishing refuses unsafe shrinkage, and index deletion is gated to protect a healthy collection from a truncated crawl.

### Verify retrieval and live providers

```bash
python -m banks.health --no-write --no-notify
python scripts/verify_source_priority_routing.py
python scripts/verify_specialist_citations.py
```

## Turkish voice input

Voice transcription is optional and currently optimized for Apple Silicon. The browser records audio; FastAPI transcribes it locally with a verified 4-bit MLX checkpoint and forces Turkish to avoid short-utterance language-detection latency.

Expected path:

```text
TF26_data/models/whisper-large-v3-mlx-4bit/
├── config.json
└── weights.npz
```

The checkpoint is deliberately not downloaded during a request. Place the converted `openai/whisper-large-v3` MLX checkpoint at that path, or set `VOICE_MODEL_PATH` to an equivalent local directory. The API validates the expected size and SHA-256 before loading weights.

To run text-only or on Linux/Windows:

```dotenv
VOICE_WARM_ON_STARTUP=false
```

Text chat, comparisons, and retrieval continue to work when the optional voice runtime is unavailable.

## Configuration

Copy `.env.example` to `.env`. Important groups:

| Group | Key examples | Notes |
|---|---|---|
| Model gateway | `VLLM_BASE_URL`, `CHAT_MODEL`, `REASONER_MODEL` | `.env.example` documents every role |
| Output guard | `OUTPUT_GUARD_MODEL`, `OUTPUT_GUARD_POLICY_FILE` | Policies live in `agents/output_guard/policies.json` |
| Retrieval | `QDRANT_URL`, `QDRANT_COLLECTION_CHUNKS` | One collection with bank/source metadata |
| Web research | `WEB_SEARCH_URL`, `WEB_READ_SOURCE_ENABLED` | Specialist-only, bounded, and optional |
| PostgreSQL | `API_DATABASE_URL`, `API_JWT_SECRET` | Never commit the populated `.env` |
| Voice | `VOICE_MODEL_PATH`, `VOICE_LANGUAGE` | Optional Apple-Silicon path |
| Corpus/index | `CORPUS_*`, `INDEX_*`, `EMBEDDING_*` | Includes concurrency and safety gates |

The frontend proxies `/api/*` to `API_ORIGIN`, defaulting to `http://127.0.0.1:8000`. If the API runs elsewhere:

```bash
API_ORIGIN=http://127.0.0.1:8001 npm --prefix UI run dev -- --hostname 127.0.0.1
```

## API and useful commands

| Command | Purpose |
|---|---|
| `bash scripts/dev.sh` | Start infrastructure, migrations, API, and UI |
| `docker compose ps` | Inspect PostgreSQL, Qdrant, and SearXNG |
| `curl http://127.0.0.1:8000/api/health` | API liveness |
| `curl http://127.0.0.1:8000/api/ready` | PostgreSQL + Qdrant readiness |
| `python -m banks.health` | Exercise declared live-bank capabilities |
| `python -m corpus --pages-only` | Crawl official web pages and queue PDFs |
| `python -m corpus --pdfs` | Process selected PDFs with vision |
| `python -m index` | Incrementally synchronize Qdrant |
| `npm --prefix UI run api:schema` | Download the OpenAPI schema |
| `npm --prefix UI run api:types` | Regenerate TypeScript API types |

Main API groups include `/api/auth`, `/api/chat`, `/api/banks`, `/api/compare`, `/api/search`, `/api/profile`, `/api/automations`, `/api/models`, `/api/health`, and `/api/ready`. The interactive contract is always available at `/docs` while FastAPI is running.

## Testing

### Backend

```bash
source .venv/bin/activate
pytest tests/unit -q
```

Live/integration tests require their named services and may contact real bank or model endpoints:

```bash
pytest tests/integration -q
```

### Frontend

```bash
npm --prefix UI run lint
npm --prefix UI run typecheck
npm --prefix UI test
npm --prefix UI run i18n:check
npm --prefix UI run build
```

### Public-repository checks

```bash
git status --short
git ls-files | grep -E '(^|/)(\.env|TF26_data|corpus_data|qdrant_storage|node_modules|\.next)(/|$)'
git diff --check
```

The second command should print nothing.

## Troubleshooting

### Login says “Incorrect email or password” for every account

Confirm that port 8000 belongs to this project:

```bash
curl http://127.0.0.1:8000/openapi.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["title"])'
```

Expected: `TF26 API`. If another project owns the port, run TF26 on another port and set `API_ORIGIN` for Next.js.

### `/api/ready` reports PostgreSQL or Qdrant unavailable

```bash
docker compose ps
docker compose logs postgres qdrant
alembic upgrade head
```

### Chat loads but model calls fail

- Verify `VLLM_BASE_URL` and each model route.
- Query `${VLLM_BASE_URL}/gemma/v1/models` with `curl`.
- Ensure vLLM was launched with the model-specific tool parser.
- Do not point the UI at the model host; the FastAPI service owns model access.

### Retrieval returns no official documents

Qdrant can be healthy and still empty. Build the corpus and run `python -m index`, then inspect <http://127.0.0.1:6333/dashboard>.

### Web search returns no results or rate-limit errors

Check SearXNG directly at <http://127.0.0.1:8888>, inspect `docker compose logs searxng`, and keep specialist tool limits conservative. Web research supplements Qdrant and live endpoints; it is not required for ordinary banking questions.

### Voice transcription is unavailable

Set `VOICE_WARM_ON_STARTUP=false` for text-only development. On Apple Silicon, verify the checkpoint path, `weights.npz` size/checksum, and that the conditional MLX dependencies installed successfully.

## Repository layout

```text
TF26/
├── agents/                  # Supervisor, ten bank specialists, output guard
├── api/                     # FastAPI routes, auth, chat, attachments, jobs
│   └── migrations/          # Alembic database migrations
├── banks/                   # Provider registry, live capabilities, health
├── config/                  # Environment settings and tunnel recovery
├── corpus/                  # Official-site crawl and document extraction
├── embeddings/              # Remote and local embedding providers
├── index/                   # Chunking, payloads, incremental Qdrant sync
├── llm/                     # Model factory and vLLM provider
├── scripts/                 # Setup, startup, and routing/citation checks
├── searxng/                 # Local metasearch configuration
├── tests/                   # Unit and live integration suites
├── UI/                      # Next.js application
├── vector_stores/           # Qdrant abstraction
├── docker-compose.yml       # PostgreSQL, Qdrant, SearXNG
└── .env.example             # Complete safe configuration template
```

## Security and public-repository hygiene

- `.env`, tokens, passwords, tunnel secrets, and machine-local status files are ignored.
- `TF26_data/`, `corpus_data/`, `data/`, model weights, Qdrant storage, logs, caches, `.next/`, and `node_modules/` stay local.
- The Docker services bind to loopback by default.
- The setup script generates a unique local JWT secret and never overwrites an existing environment file.
- Uploaded document processing remains server-side; rendered PDF/DOCX pages are not exposed as separate user artifacts.
- The included password-reset route is explicitly a **local-demo shortcut** and must be replaced with a verified, time-limited email-token flow before an internet-facing deployment.
- Before publishing, review `git status`, run a secret scanner, and inspect every staged binary.

## Contributors

<table>
  <tr>
    <td align="center" width="50%">
      <img src="assets/ismail-furkan-atasoy.jpeg" alt="İsmail Furkan Atasoy" width="120" height="120" style="border-radius:50%; object-fit:cover;" /><br/><br/>
      <strong><a href="https://www.linkedin.com/in/ifurkanatasoy/">İsmail Furkan Atasoy</a></strong><br/>
      <sub>Contributor</sub><br/><br/>
      <a href="https://www.linkedin.com/in/ifurkanatasoy/"><img src="https://img.shields.io/badge/LinkedIn-Profile-0A66C2?logo=linkedin&logoColor=white" alt="İsmail Furkan Atasoy LinkedIn" /></a>
      <a href="https://github.com/ifurkanatasoy"><img src="https://img.shields.io/badge/GitHub-ifurkanatasoy-181717?logo=github&logoColor=white" alt="İsmail Furkan Atasoy GitHub" /></a>
      <a href="https://www.instagram.com/ifurkanatasoy/"><img src="https://img.shields.io/badge/Instagram-@ifurkanatasoy-E4405F?logo=instagram&logoColor=white" alt="İsmail Furkan Atasoy Instagram" /></a>
    </td>
    <td align="center" width="50%">
      <img src="assets/abdelrahman-wahdan.jpeg" alt="Abdelrahman Wahdan" width="120" height="120" style="border-radius:50%; object-fit:cover;" /><br/><br/>
      <strong><a href="https://www.linkedin.com/in/abdelrahman-wahdan">Abdelrahman Wahdan</a></strong><br/>
      <sub>Contributor</sub><br/><br/>
      <a href="https://www.linkedin.com/in/abdelrahman-wahdan"><img src="https://img.shields.io/badge/LinkedIn-Profile-0A66C2?logo=linkedin&logoColor=white" alt="Abdelrahman Wahdan LinkedIn" /></a>
      <a href="https://github.com/Abdurrahman-Wahdan"><img src="https://img.shields.io/badge/GitHub-Abdurrahman--Wahdan-181717?logo=github&logoColor=white" alt="Abdelrahman Wahdan GitHub" /></a>
      <a href="https://www.instagram.com/boodywahdan_/"><img src="https://img.shields.io/badge/Instagram-@boodywahdan_-E4405F?logo=instagram&logoColor=white" alt="Abdelrahman Wahdan Instagram" /></a>
    </td>
  </tr>
</table>

## License

Licensed under the [Apache License 2.0](LICENSE). See `LICENSE` for the complete terms.

## TEKNOFEST 2026

<div align="center">
  <img src="UI/public/vision/images/kermits-logo.png" alt="Kermits AI" width="96" />
  <br />
  <strong>Built for <a href="https://www.teknofest.org/tr/yarismalar/yapay-zeka-dil-ajanlari-yarismasi/">TEKNOFEST 2026 Yapay Zeka Dil Ajanları Yarışması</a></strong>
  <br />
  <sub><strong>Kategori 2</strong> — Katılım Bankacılığı Finansal Metin Madenciliği, Bilgi Çıkarımı ve Akıllı Dashboard-Asistan Çözümleri</sub>
  <br />
  <sub>Kermits AI · Evidence-first participation banking intelligence</sub>
</div>
