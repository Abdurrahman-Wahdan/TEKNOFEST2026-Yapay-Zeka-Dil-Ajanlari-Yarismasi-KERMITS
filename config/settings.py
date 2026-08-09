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
    LLM_TIMEOUT: float = Field(default=300.0, gt=0)
    LLM_MAX_RETRIES: int = Field(default=1, ge=0)
    LLM_TEMPERATURE: float = Field(
        default=0.0,
        description="Extraction favours repeatability over variety.",
    )

    # Role -> model key. Lets model choice change in .env without touching code.
    DEFAULT_MODEL: str = "qwen"
    CHAT_MODEL: str = Field(default="gemma", description="Fastest, cleanest Turkish.")
    EXTRACTOR_MODEL: str = Field(default="qwen", description="Best structured output.")
    REASONER_MODEL: str = "gpt"

    # ===== Embeddings =====
    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = Field(
        default="BAAI/bge-m3",
        description="Multilingual, strong on Turkish, Apache-2.0. Change freely.",
    )
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DIMENSIONS: int = Field(
        default=1024,
        gt=0,
        description="Must match the model. Collections are created with this size.",
    )
    EMBEDDING_BATCH_SIZE: int = Field(default=16, gt=0)

    # ===== Vector store (local Qdrant) =====
    VECTOR_STORE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_TIMEOUT: int = Field(default=30, gt=0)
    QDRANT_COLLECTION_CAMPAIGNS: str = "campaigns"

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
        description="Per site. Lower than the old crawler's 12: this runs every "
        "night against ten hosts, and around 170 requests in a burst from one "
        "address is what a WAF throttles.",
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
        default=2,
        gt=0,
        description="Vertical tiles per page sent to the model. Its image token "
        "budget is fixed per tile, so tiling doubles effective resolution at no "
        "extra input cost and is the lever for detail, not DPI.",
    )
    CORPUS_PDF_MAX_PAGES: int = Field(
        default=40,
        gt=0,
        description="Pages read per PDF. Beyond it the document is marked "
        "truncated with its real page count. The only files this touches are "
        "113-127 page SPK prospectus annexes, which answer no product question.",
    )
    CORPUS_PDF_MAX_TOKENS: int = Field(
        default=2048,
        gt=0,
        description="Per tile. Measured 399-951 completion tokens for half a "
        "page, so this is headroom; a 'length' finish means retry with more tiles.",
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

    # ===== Application =====
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"

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


settings = Settings()
