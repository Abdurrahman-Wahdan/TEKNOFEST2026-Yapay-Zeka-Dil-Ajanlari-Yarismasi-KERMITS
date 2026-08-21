"""Application settings, loaded from environment variables and .env.

One flat Settings class. Grouping is by section banner and field-name prefix,
not by nesting, so any value can be found and changed in one place.
"""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor paths to this file, not the working directory, so settings load the
# same way from pytest, a CLI run, or a server process.
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Model keys served by the local vLLM host. Roles below must point at one of
# these; the validator enforces it.
MODEL_KEYS = ("gemma", "qwen", "gpt")


class Settings(BaseSettings):
    """Settings for the local LLM, embedding and vector store stack."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ===== LLM (local vLLM) =====
    VLLM_BASE_URL: str = "https://unbundle-semisoft-mouth.ngrok-free.dev"
    VLLM_API_KEY: str = Field(
        default="EMPTY",
        description="vLLM needs no auth, but the OpenAI client rejects an empty key.",
    )
    # The full retry window for a live model call.  During this period the
    # tunnel-aware client retries transport/tunnel failures with exponential
    # backoff after refreshing the published tunnel URL.
    LLM_TIMEOUT: float = Field(default=3000.0, gt=0)
    LLM_RETRY_MAX_DELAY_SECONDS: float = Field(default=60.0, gt=0)
    LLM_MAX_RETRIES: int = Field(
        default=20,
        ge=0,
        description="Transport retries inside the SDK client. The chat "
        "clients set 0 and retry in TunnelAwareChatOpenAI instead, which "
        "re-resolves the tunnel first; embeddings have no such wrapper, so "
        "the SDK's own retries are all they get.",
    )
    LLM_TEMPERATURE: float = Field(
        default=0.0,
        description="Extraction favours repeatability over variety.",
    )

    # Role -> model key. Lets model choice change in .env without touching code.
    DEFAULT_MODEL: str = "qwen"
    CHAT_MODEL: str = Field(default="gemma", description="Fastest, cleanest Turkish.")
    EXTRACTOR_MODEL: str = Field(default="qwen", description="Best structured output.")
    REASONER_MODEL: str = "gpt"

    # ===== Compaction (summarising a thread before it fills its window) =====
    #
    # Every agent is compacted the same way -- the supervisor and all ten bank
    # specialists -- because each is a separate instance with its own thread and
    # its own history. The two tiers get their own keys so they can be tuned
    # apart: a specialist's thread fills with bank JSON at a different rate than
    # a conversation does, and nobody is watching it.
    COMPACT_AT_FRACTION: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        description="Compact the supervisor's thread once its messages reach "
        "this share of the usable window. Measured against what the server "
        "reports for the model, not a constant.",
    )
    COMPACT_SPECIALIST_AT_FRACTION: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        description="The same, for one bank specialist's private thread.",
    )
    COMPACT_KEEP_MESSAGES: int = Field(
        default=10,
        gt=0,
        description="Messages left untouched at the end of the supervisor's "
        "thread. Everything before them is replaced by the summary; these are "
        "carried through verbatim so the last exchanges keep their exact wording.",
    )
    COMPACT_SPECIALIST_KEEP_MESSAGES: int = Field(
        default=10,
        gt=0,
        description="The same, for one bank specialist's private thread.",
    )
    COMPACT_MODEL: str = Field(
        default="chat",
        description="Role or model key that writes the summary. A role, so it "
        "follows CHAT_MODEL rather than pinning a model that may not be served.",
    )

    # ===== Embeddings =====
    EMBEDDING_PROVIDER: str = Field(
        default="remote",
        description="'remote' calls the Qwen3 embedding server over the vLLM "
        "host (EMBEDDING_ROUTE); 'local' runs sentence-transformers on-device. "
        "Remote is the default: the embedding server already runs on the same "
        "host as the chat models, so no local GPU/download is needed.",
    )
    EMBEDDING_ROUTE: str = Field(
        default="/embed/v1",
        description="Path on VLLM_BASE_URL serving the embedding model, same "
        "pattern as the chat model routes in llm/providers/vllm_provider.py.",
    )
    EMBEDDING_MODEL: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B",
        description="Multilingual, strong on Turkish, 1024-dim, 32k context so "
        "long fee-table pages embed whole. Queries take an instruction prefix "
        "(handled in index/embed_text.py); passages do not.",
    )
    EMBEDDING_DEVICE: str = Field(
        default="mps",
        description="Only read by the 'local' provider (unused while "
        "EMBEDDING_PROVIDER=remote). Apple GPU. Measured on an M1 Max (32 GPU "
        "cores): 0.09s per chunk against 3.61s on CPU -- 40x, turning a 20-hour "
        "index into 25 minutes. Use 'cpu' on a machine without Metal, 'cuda' "
        "with an NVIDIA GPU.",
    )
    EMBEDDING_DIMENSIONS: int = Field(
        default=1024,
        gt=0,
        description="Must match the model. Collections are created with this size.",
    )
    EMBEDDING_BATCH_SIZE: int = Field(
        default=32,
        gt=0,
        description="Only read by the 'local' provider (unused while "
        "EMBEDDING_PROVIDER=remote, where OpenAIEmbeddings' own chunk_size "
        "applies instead). Measured on an M1 Max: 32 is the fastest batch on "
        "MPS (0.074s/chunk); 64 and 128 are slower, not faster.",
    )

    # ===== Vector store (local Qdrant) =====
    VECTOR_STORE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_TIMEOUT: int = Field(default=30, gt=0)
    QDRANT_COLLECTION_CHUNKS: str = Field(
        default="bank_chunks",
        description="One collection for every chunk -- web sections and PDF pages "
        "together -- so a query searches both in one ranked list. Source type and "
        "kind are payload filters, not separate collections.",
    )

    # ===== Index (embedding the corpus into Qdrant) =====
    INDEX_MAX_CHUNK_CHARS: int = Field(
        default=3500,
        gt=0,
        description="A unit larger than this is split on paragraph boundaries. "
        "Rare: Qwen3's context is 32k, so this only touches a few long sections.",
    )
    INDEX_EMBED_BATCH: int = Field(
        default=128,
        gt=0,
        description="Chunks embedded and upserted per batch. Each batch is "
        "written to Qdrant before the next is embedded, so the point count is "
        "live progress and a killed run keeps what it finished -- a restart "
        "skips those chunks on the text_hash diff and resumes.",
    )
    INDEX_RETRIEVE_TOP_K: int = Field(default=8, gt=0)
    INDEX_MAX_DELETE_PCT: int = Field(
        default=20,
        gt=0,
        description="A sync that would delete more than this share of the index "
        "in one run refuses, so a truncated documents.jsonl cannot wipe it. Same "
        "guard as the corpus shrink gate.",
    )
    INDEX_SCHEDULE: str = Field(
        default="30 3 * * *",
        description="Cron for the nightly index, staggered 30 min after the "
        "corpus build (0 3) so documents.jsonl is finished first. Printed by "
        "index.schedule, never installed.",
    )

    # ===== Banks (live calculator endpoints) =====
    BANK_HTTP_TIMEOUT: float = Field(default=30.0, gt=0)
    BANK_HTTP_RETRIES: int = Field(
        default=1,
        ge=0,
        description="Extra attempts. Kuveyt Türk's finance endpoint "
        "intermittently answers 200 with an empty Meta.",
    )
    BANK_USER_AGENT: str = Field(
        default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        description="These are public browser endpoints; they expect a browser.",
    )

    # ===== Cross-bank comparison =====
    BANK_COMPARE_WORKERS: int = Field(
        default=8,
        gt=0,
        description="One thread per bank, never more: providers hold their own "
        "caches and share one HTTP client per transport. Six banks measured "
        "0.59s in parallel against 11.99s one at a time.",
    )
    BANK_COMPARE_TIMEOUT: float = Field(
        default=45.0,
        gt=0,
        description="Budget for a whole comparison. A bank past it is reported "
        "as an error rather than holding up the answer.",
    )

    # ===== Corpus (crawled bank sites) =====
    CORPUS_ROOT: str = Field(
        default="",
        description="Where raw bytes, the manifest and the clean artifact live. "
        "Empty means beside the project, so a blank line in .env cannot "
        "silently move the whole corpus into the working directory.",
    )
    CORPUS_USER_AGENT: str = Field(
        default="TF26-corpus/1.0 (+https://github.com/Abdurrahman-Wahdan/TF26)",
        description="An honest identifier, unlike the browser string the bank "
        "endpoints need. These are public content pages, not calculators that "
        "reject non-browsers, and a nightly job should say who it is.",
    )
    CORPUS_TIMEOUT: float = Field(default=40.0, gt=0)
    CORPUS_CONCURRENCY: int = Field(
        default=6,
        gt=0,
        description="Connections per site. Lower than the old crawler's 12: this "
        "runs every night against ten hosts, and around 170 requests in a burst "
        "from one address is what a WAF throttles.",
    )
    CORPUS_SITE_WORKERS: int = Field(
        default=10,
        gt=0,
        description="Banks crawled at once, one thread each. Ten is one per bank. "
        "Each is a different server, so parallelising across them does not raise "
        "the per-host request rate (still CORPUS_CONCURRENCY each) -- it only "
        "overlaps the wait, so the crawl takes the slowest bank's time, not the "
        "sum of all ten.",
    )
    CORPUS_DELAY: float = Field(
        default=0.25,
        ge=0,
        description="Pause between requests to one site, matching "
        "HEALTH_AUDIT_PRODUCT_DELAY. robots.txt Crawl-delay overrides it upward.",
    )
    CORPUS_MAX_PAGES_PER_SITE: int = Field(default=8000, gt=0)
    CORPUS_MAX_PDF_MB: int = Field(
        default=50,
        gt=0,
        description="A PDF past this is refused and reported rather than "
        "silently skipped. Measured sizes run 0.2-9.8 MB.",
    )
    CORPUS_PDF_MODEL: str = Field(
        default="gemma",
        description="Reads PDF pages as images, for classifying the files the "
        "filename rules cannot judge and for extracting scanned ones. Measured "
        "against qwen on a real scanned Turkish page: gemma spent 304 prompt "
        "tokens per tile to qwen's 4,328 and made no transcription errors "
        "where qwen made three. gpt-oss cannot see images at all.",
    )
    CORPUS_PDF_DPI: int = Field(
        default=200,
        gt=0,
        description="The vision encoder resamples to a fixed grid, so a higher "
        "DPI costs raster time and bandwidth without giving the model more to "
        "read. Tiling is the lever for detail, not DPI.",
    )
    CORPUS_PDF_MIN_CHARS_PER_PAGE: int = Field(
        default=100,
        gt=0,
        description="Below this, after stamp-stripping, a page has no text "
        "layer and must be read as an image. The measured gap is disjoint: "
        "scanned pages yield 0-1 characters, text pages 269 and up.",
    )
    CORPUS_PDF_STAMP_FRACTION: float = Field(
        default=0.8,
        gt=0,
        le=1,
        description="A line on this share of pages is a header, footer or "
        "watermark. The 113-page scans carry a per-page 'Dogrulama Kodu' stamp, "
        "which is what let the old crawler mistake them for readable text.",
    )
    CORPUS_PDF_TILES: int = Field(
        default=4,
        gt=0,
        description="Vertical tiles a page starts as, and a legibility knob as "
        "much as a length one: a tile is rendered to CORPUS_PDF_SCALE_TO on its "
        "longest side, so quartering a page doubles the pixels per line of text "
        "against halving it. Measured on a card-sector table the model was "
        "misreading -- two tiles recovered 26.7% of the words and invented the "
        "column header, four recovered 94.6% and every number. A tile the model "
        "still cannot transcribe is halved again by the extractor.",
    )
    CORPUS_PDF_MAX_SPLIT_DEPTH: int = Field(
        default=3,
        ge=0,
        description="How many times a failing region may be halved before the "
        "page is given up as unreadable. Three takes a half-page down to a "
        "sixteenth, well past the quarter page that read a dense 46-page "
        "contract cleanly. A region still failing that small is not a matter of "
        "length, so splitting further would only burn tokens.",
    )
    CORPUS_PDF_MAX_PAGES: int = Field(
        default=40,
        gt=0,
        description="Pages read per PDF. Beyond it the document is marked "
        "truncated with its real page count. The only files this touches are "
        "113-127 page SPK prospectus annexes, which answer no product question.",
    )
    CORPUS_PDF_WORKERS: int = Field(
        default=6,
        gt=0,
        description="PDFs OCR'd at once. A single vLLM instance serves these "
        "concurrently by continuous batching, which is what it is built for: "
        "measured 23.9s per page one at a time against 4.8s per page with six in "
        "flight, a clean 5x. An earlier run was read as six workers deadlocking "
        "on one instance; it was really six 40-page contracts in progress, and a "
        "PDF writes nothing until its last page returns.",
    )
    CORPUS_PDF_MAX_TOKENS: int = Field(
        default=4096,
        gt=0,
        description="Output budget for one tile, which is half a page. Measured "
        "399-951 tokens for a real half page, so this is generous headroom "
        "without being an invitation: the failure mode at the top end is the "
        "model repeating itself until it hits the ceiling, and a lower ceiling "
        "makes that cheap to detect and retry. A truncated tool call arrives as "
        "no structured output at all, which the extractor treats as a failed "
        "page rather than a blank one.",
    )
    CORPUS_PDF_JPEG_QUALITY: int = Field(
        default=90,
        gt=0,
        le=100,
        description="Quality of the JPEG sent to the model. Ninety, because the "
        "request has to fit the tunnel's size limit and PNG does not: a scanned "
        "A4 quarter-tile is 497 KB of base64 as PNG and 222 KB at this quality, "
        "reading identically. Lower starts softening the small print that the "
        "resolution settings exist to preserve.",
    )
    CORPUS_PDF_SCALE_TO: int = Field(
        default=2200,
        gt=0,
        description="Longest side, in pixels, of the image sent for one tile. "
        "This is the single most important quality setting in the PDF path. Too "
        "large and the request returns no structured output at all -- an "
        "uncapped A4 page rendered to 714 KB did exactly that, which reads "
        "downstream as a blank page. Too small and small print stops being "
        "legible, and a vision model that cannot read does not say so: it emits "
        "something plausible instead. At 1400 a sector table came back with MCC "
        "code 5013 rewritten as NACE 4511, an invented value in a bank "
        "document. At 2200 the same page returned every number correctly, and "
        "pages that were already fine were unaffected.",
    )
    CORPUS_PDF_PAGE_ATTEMPTS: int = Field(
        default=4,
        gt=0,
        description="Tries per page before the whole PDF is refused. The tunnel "
        "in front of the model fails in bursts -- one run lost 12 of its first "
        "50 pages to gateway errors, then only 1 of the next 85 -- so a page is "
        "worth retrying, and a burst outlasting four tries is worth stopping for.",
    )
    CORPUS_PDF_RETRY_BACKOFF: float = Field(
        default=2.0,
        gt=0,
        description="Seconds before the second try, doubling after. Four tries "
        "spans about fourteen seconds, which covered every burst measured.",
    )
    CORPUS_PDF_MIN_UNIQUE_LINES: float = Field(
        default=0.60,
        gt=0,
        le=1,
        description="Below this, extraction is suspect and the document is "
        "refused. The corpus median is 0.972; the files pypdf mangled sat at "
        "0.008-0.043.",
    )
    CORPUS_MIN_CHARS: int = Field(
        default=250,
        gt=0,
        description="Below this a document is a navigation stub, not content. "
        "262 Emlak and 155 Ziraat documents in the crawled corpus were these.",
    )
    CORPUS_MAX_SHRINK_PCT: int = Field(
        default=10,
        gt=0,
        description="A run that would shrink the corpus more than this writes "
        "nothing and leaves yesterday's artifact in place. A site rolling out a "
        "WAF block 403s everything, and would otherwise quietly delete thousands "
        "of documents that are still there.",
    )
    CORPUS_MAX_ERROR_PCT: int = Field(
        default=20,
        gt=0,
        description="Share of one site's fetches that may fail before the whole "
        "run refuses to publish.",
    )
    CORPUS_SCHEDULE: str = Field(
        default="0 3 * * *",
        description="Cron expression for the nightly run, in local time. Nothing "
        "reads it at run time -- it is what `python -m corpus.schedule` prints. "
        "03:00 keeps it clear of the 06:00 health check's request budget.",
    )
    CORPUS_MISSING_RUNS: int = Field(
        default=3,
        gt=0,
        description="Consecutive runs a URL must be missing before its document "
        "is dropped. The same rule as the audit's: one WAF blip returning 403 "
        "for everything must not delete a site's 2,366 documents.",
    )

    # ===== Health checks =====
    HEALTH_STATUS_FILE: str = Field(
        default=str(PROJECT_ROOT / "bank_status.json"),
        description="Where the checker records which bank capabilities are down. "
        "The tools read it, so a broken endpoint refuses instead of guessing.",
    )
    HEALTH_WEBHOOK_URL: str = Field(
        default="",
        description="POSTed a JSON summary when a bank changes state. "
        "Empty disables it; the run still logs and still writes the status file.",
    )
    HEALTH_SCHEDULE: str = Field(
        default="0 6 * * *",
        description="Cron expression for the scheduled run, in the local "
        "timezone. Nothing reads this at run time -- it is the value used when "
        "generating a crontab line or launchd plist, so the schedule lives in "
        "settings rather than being baked into the code.",
    )
    HEALTH_AUDIT_WORKERS: int = Field(
        default=8,
        gt=0,
        description="Banks checked at once during the extensive audit. One "
        "thread per bank; each walks its own products in order.",
    )
    HEALTH_AUDIT_PRODUCT_DELAY: float = Field(
        default=0.25,
        ge=0,
        description="Pause between products at one bank. Around 170 requests in "
        "a burst from one address is what a WAF throttles, and a throttled "
        "address looks exactly like an outage.",
    )
    HEALTH_TIMEOUT: float = Field(
        default=60.0,
        gt=0,
        description="Per-capability budget. Higher than BANK_HTTP_TIMEOUT "
        "because one check may call a catalogue and then a quote.",
    )

    # ===== API (the FastAPI service the dashboard talks to) =====
    API_HOST: str = "127.0.0.1"
    API_PORT: int = Field(default=8000, gt=0, lt=65536)
    API_CORS_ORIGINS: str = Field(
        default="http://localhost:3000",
        description="Comma-separated browser origins allowed to call the API. "
        "A list, not '*': the API carries a session cookie, and a wildcard "
        "origin with credentials is both refused by browsers and wrong. Read "
        "through the cors_origins property, never split at the call site.",
    )
    API_DATABASE_URL: str = Field(
        default="postgresql+psycopg://tf26:tf26@localhost:5433/tf26",
        description="Users, profiles and chat history. Separate from Qdrant, "
        "which holds no user data -- a user's question never becomes a vector. "
        "Port 5433, not 5432: docker-compose.yml keeps off the default so it "
        "cannot collide with another project's Postgres on the same machine.",
    )
    API_JWT_SECRET: str = Field(
        default="",
        description="Signing key for access and refresh tokens. Empty is "
        "refused at startup outside development, so an unsigned deployment "
        "cannot happen quietly. Generate with `openssl rand -hex 32`.",
    )
    API_JWT_ALGORITHM: str = "HS256"
    API_ACCESS_TOKEN_MINUTES: int = Field(
        default=30,
        gt=0,
        description="Short, because the refresh token is what carries the "
        "session. A leaked access token expires before it is useful.",
    )
    API_REFRESH_TOKEN_DAYS: int = Field(default=30, gt=0)

    # ===== Application =====
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"

    @property
    def cors_origins(self) -> list[str]:
        """API_CORS_ORIGINS as a list, blanks and stray spaces removed."""
        return [o.strip() for o in self.API_CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def validate_model_roles(self):
        """Fail at startup if a role points at a model that does not exist.

        Without this a typo in .env surfaces mid-run as a confusing provider
        error, long after the process started.
        """
        roles = {
            "DEFAULT_MODEL": self.DEFAULT_MODEL,
            "CHAT_MODEL": self.CHAT_MODEL,
            "EXTRACTOR_MODEL": self.EXTRACTOR_MODEL,
            "REASONER_MODEL": self.REASONER_MODEL,
        }
        for field, value in roles.items():
            if value not in MODEL_KEYS:
                raise ValueError(
                    f"{field}={value!r} is not a known model. "
                    f"Valid keys: {', '.join(MODEL_KEYS)}"
                )
        return self

    @model_validator(mode="after")
    def validate_jwt_secret(self):
        """Refuse to start outside development without a signing key.

        A blank key is convenient in development and a vulnerability anywhere
        else -- anyone could mint a token for any user. Failing at startup makes
        that impossible to ship by accident.
        """
        if self.ENVIRONMENT != "development" and not self.API_JWT_SECRET:
            raise ValueError(
                "API_JWT_SECRET is required when ENVIRONMENT is not "
                "'development'. Generate one with: openssl rand -hex 32"
            )
        return self


settings = Settings()
