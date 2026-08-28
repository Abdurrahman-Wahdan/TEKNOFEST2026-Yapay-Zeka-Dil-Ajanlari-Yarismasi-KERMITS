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
    VLLM_BASE_URL: str = Field(
        default="http://127.0.0.1:9000",
        description="Where the vLLM server answers. Localhost by default, and "
        "matching .env.example: the working address is a tunnel that rotates, "
        "so it belongs in .env rather than baked in here.",
    )
    TUNNEL_GIST_URL: str = Field(
        default="",
        description="A plain-text URL publishing the current VLLM_BASE_URL, "
        "read by config/tunnel.py when a request fails and the tunnel has "
        "probably moved. Empty by default and deliberately so: it is one "
        "deployment's private channel, not a property of this repository. "
        "Unset simply removes that candidate from the ladder -- .env and the "
        "current address are still tried, so nothing breaks without it.",
    )
    VLLM_API_KEY: str = Field(
        default="EMPTY",
        description="vLLM needs no auth, but the OpenAI client rejects an empty key.",
    )
    # dataprep/vlm.py::_READ_TIMEOUT ile AYNI değer ve AYNI gerekçe: streaming
    # açıkken istek süresi rahatça 200s'yi aşabiliyor (canlı ölçüm: 135-188s
    # süren istekler SORUNSUZ tamamlandı). Erken kesmek KENDİ ÜRETTİĞİMİZ bir
    # arıza oluyordu — timeout, uyarlanabilir sınırlayıcıya "tıkanıklık" diye
    # raporlanıp limiti düşürüyor, kuyruk uzuyor, daha çok timeout doğuyordu
    # (kısır döngü, canlı yaşandı). Asıl koruma sonsuz retry olduğu için taban
    # cömert tutulur; gerçekten asılı kalan bir bağlantı yine de buradan kopar.
    LLM_TIMEOUT: float = Field(default=900.0, gt=0)
    LLM_MAX_RETRIES: int = Field(default=1, ge=0)
    # Üstel backoff'un tavanı — llm/providers/vllm_provider.py ve
    # embeddings/providers/remote_provider.py bu alanı okur.
    LLM_RETRY_MAX_DELAY_SECONDS: float = Field(default=60.0, gt=0)
    LLM_TEMPERATURE: float = Field(
        default=0.0,
        description="Extraction favours repeatability over variety.",
    )

    # Role -> model key. Lets model choice change in .env without touching code.
    DEFAULT_MODEL: str = "qwen"
    CHAT_MODEL: str = Field(default="gemma", description="Fastest, cleanest Turkish.")
    EXTRACTOR_MODEL: str = Field(default="qwen", description="Best structured output.")
    REASONER_MODEL: str = "gpt"

    # ===== Public answer output guard =====
    OUTPUT_GUARD_MODEL: str = Field(
        default="gemma",
        description="Fast local model used for the final policy checklist.",
    )
    OUTPUT_GUARD_MAX_TOKENS: int = Field(
        default=1400,
        ge=300,
        description="Small structured checklist/patch budget; never an answer rewrite.",
    )
    OUTPUT_GUARD_POLICY_FILE: Path = Field(
        default=PROJECT_ROOT / "agents" / "output_guard" / "policies.json",
        description="Editable public-answer policy set, reloaded for every turn.",
    )

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

    # ===== Corpus retrieval (a specialist reading its own bank's documents) =====
    #
    # A physical ceiling on one delegated turn, not a judgement about content.
    # The agent decides what to search for and when it has enough; these only
    # stop a loop, and a loop is what happens without them: the offline
    # researcher, which has the same tools, was measured going 42 `next=true`
    # calls deep on a single topic and 50 calls into a marking cycle that
    # returned nothing. `exit_behavior="continue"` means hitting one of these
    # blocks that tool and lets the specialist answer with what it has, rather
    # than failing the turn.
    RETRIEVE_SEARCH_LIMIT: int = Field(
        default=8,
        gt=0,
        description="Most `search_bank` calls in one delegated turn. Lower than "
        "the offline pipeline's budget on purpose: a user is waiting on this "
        "one, and each call now returns whole chunks rather than previews.",
    )
    RETRIEVE_EXPAND_LIMIT: int = Field(
        default=8,
        gt=0,
        description="Most `expand_chunk` calls in one delegated turn. Roughly one "
        "per search: widening a cut-off passage is the normal follow-up to a "
        "hit, and walking a long document outward is several.",
    )
    RETRIEVE_PAGE_LIMIT: int = Field(
        default=3,
        gt=0,
        description="Most `read_full_page` calls in one delegated turn. The "
        "smallest of the three because it is the largest payload and the one "
        "`expand_chunk` usually replaces.",
    )

    # ===== On-demand web research (specialists only) =====
    WEB_SEARCH_URL: str = Field(default="http://127.0.0.1:8888")
    WEB_SEARCH_TIMEOUT: float = Field(default=15.0, gt=0)
    WEB_SEARCH_MAX_RESULTS: int = Field(default=6, gt=0, le=20)
    WEB_RESEARCH_CACHE_SECONDS: float = Field(default=300.0, ge=0)
    WEB_READ_SOURCE_ENABLED: bool = Field(
        default=True,
        description=(
            "Expose read_bank_source beside search_bank_web. Set false only for "
            "a search-only assessment; production research should keep it true."
        ),
    )
    WEB_RESEARCH_USER_AGENT: str = "TF26-web-research/1.0"
    WEB_READ_TIMEOUT: float = Field(default=30.0, gt=0)
    WEB_READ_MAX_BYTES: int = Field(default=10_000_000, gt=0)
    WEB_READ_MAX_CHARS: int = Field(default=35_000, gt=0)
    WEB_READ_MAX_REDIRECTS: int = Field(default=5, ge=0, le=15)
    WEB_READ_MAX_PDF_PAGES: int = Field(default=40, gt=0)
    WEB_SEARCH_TOOL_LIMIT: int = Field(default=4, gt=0)
    WEB_READ_TOOL_LIMIT: int = Field(default=8, gt=0)

    # ===== Table overviews =====
    TABLE_OVERVIEW_CONCURRENCY: int = Field(
        default=2,
        gt=0,
        description="How many overviews may be generated at once, process-wide. "
        "The per-table lock stops two readers of the *same* table racing; this "
        "stops a reader who opens six tables in a minute queueing six vision "
        "calls on a single-GPU host that is also serving the chat. Measured: "
        "the engine had 14 requests in flight during testing and stopped "
        "answering a ten-token prompt inside 90 seconds.",
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
        default=8196,
        gt=0,
        description="Chunk size in characters (user decision 2026-08-19): every "
        "chunk outside the text-cleaning stage is 8196. A unit larger than this "
        "is split on paragraph boundaries, then sentence ends, then a hard cut.",
    )
    INDEX_CHUNK_OVERLAP_CHARS: int = Field(
        default=820,
        ge=0,
        description="%10 overlap carried from the previous chunk (user decision "
        "2026-08-19). A fact split across a boundary stays whole in at least one "
        "chunk, so retrieval cannot miss it.",
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

    # ===== Voice transcription =====
    VOICE_MODEL_PATH: str = Field(
        default=str(
            PROJECT_ROOT
            / "TF26_data"
            / "models"
            / "whisper-large-v3-mlx-4bit"
        ),
        description="Local MLX checkpoint for Turkish voice input. A local path "
        "is deliberate: serving a request must never trigger a multi-gigabyte "
        "network download. The default is exact Whisper large-v3 with 4-bit "
        "weights, ready for the on-device M1 Max benchmark.",
    )
    VOICE_LANGUAGE: str = Field(
        default="tr",
        min_length=2,
        max_length=5,
        description="Forced source language. Skipping language detection removes "
        "work from every short recording and prevents Turkish speech being "
        "misclassified from a one- or two-word prompt.",
    )
    VOICE_MODEL_BYTES: int = Field(
        default=973_563_040,
        gt=0,
        description="Expected byte size of the configured MLX weights.npz.",
    )
    VOICE_MODEL_SHA256: str = Field(
        default="3bfa3c4e42344ea87a5b81a73992a867088ae076d377042e384b657adea81db9",
        min_length=64,
        max_length=64,
        description="Official Hugging Face LFS SHA-256 for the configured "
        "checkpoint. Verified once before any model bytes are executed.",
    )
    VOICE_MAX_UPLOAD_MB: int = Field(
        default=10,
        gt=0,
        le=100,
        description="Hard cap on one compressed browser recording.",
    )
    VOICE_WARM_ON_STARTUP: bool = Field(
        default=False,
        description="Load and compile Whisper during API startup so the first "
        "voice request has warm-request latency.",
    )

    # ===== Speech synthesis (reading answers aloud) =====
    SPEECH_REMOTE_URL: str = Field(
        default="",
        description="Remote streaming TTS endpoint. It returns raw s16le PCM. "
        "Empty by default rather than pointing at a machine on someone's LAN: "
        "the address is deployment-specific, and `voice_speech.speak` already "
        "answers an unset value with a 503 that says so. Set it in .env.",
    )
    SPEECH_REMOTE_SEGMENT_CHARS: int = Field(
        default=1_500,
        gt=0,
        description="Maximum text sent per remote speech request.",
    )
    SPEECH_REMOTE_CONNECT_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    SPEECH_REMOTE_READ_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0)
    SPEECH_REMOTE_WRITE_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    SPEECH_MODEL_PATH: str = Field(
        default=str(PROJECT_ROOT / "TF26_data" / "models" / "Trendyol-TTS"),
        description="Local checkpoint for Turkish speech output, downloaded by "
        "`scripts/download_speech_model.py`. A local path for the same reason "
        "VOICE_MODEL_PATH is one: serving a request must never be able to "
        "trigger a multi-gigabyte network download, and a checkpoint that lives "
        "beside the Whisper one is a checkpoint operators can see, size and "
        "delete.",
    )
    SPEECH_MODEL_ID: str = Field(
        default="Trendyol/Trendyol-TTS",
        description="Hugging Face repository the local checkpoint is downloaded "
        "from. VoxCPM2 with a 20+ hour Turkish LoRA, MIT licensed. Read by the "
        "download script, not at request time.",
    )
    SPEECH_SAMPLE_RATE: int = Field(
        default=48_000,
        gt=0,
        description="What the model generates at, used only until the model is "
        "loaded and can be asked directly. The browser must build its "
        "AudioContext at this rate -- a mismatch does not fail, it plays the "
        "answer at the wrong pitch, so the real value travels on the response.",
    )
    SPEECH_TIMESTEPS: int = Field(
        default=4,
        ge=4,
        le=32,
        description="Diffusion steps, and the setting that decides whether the "
        "stream keeps up with playback. **Hardware-dependent -- re-measure when "
        "the machine changes.** TTS_ENTEGRASYON.md recommends 10, measured on an "
        "M5 Max where every setting generates faster than real time (10 gives "
        "1.94x) so the choice comes down to first-chunk stability. On the M1 Max "
        "this runs on, that is not the situation: measured 4=1.22x, 6=1.02x, "
        "8=0.95x, 10=0.81x, 16=0.57x -- at 10 the audio is produced *slower* "
        "than it plays, so a long reading runs dry and stutters. The guide's "
        "objection to 4 (first audio 0.41s ±0.44, unstable) does not reproduce "
        "here either: first audio is flat at 0.50-0.70s across every setting. So "
        "4 on this hardware, and 10 on anything that clears real time at 10. "
        "Floor of 4 is deliberate -- at 2 the model silently skips parts of the "
        "text (a 55s passage came out 29s).",
    )
    SPEECH_CFG_VALUE: float = Field(
        default=2.0,
        gt=0,
        le=2.4,
        description="Classifier-free guidance, per the model card. 1.5 gives "
        "livelier intonation. Capped below 2.5, where peak amplitude approaches "
        "0 dBFS and clips.",
    )
    SPEECH_MAX_LEN: int = Field(
        default=4096,
        gt=0,
        description="Per-call generation bound. Text longer than this comes back "
        "silently short, which is why SPEECH_SEGMENT_CHARS splits first.",
    )
    SPEECH_NORMALIZE: bool = Field(
        default=False,
        description="voxcpm's built-in text normalisation. OFF, and this is the "
        "one place we contradict TTS_ENTEGRASYON.md -- measured against the "
        "installed package, `normalize=True` runs wetext's *English* normaliser "
        "and it is actively wrong for Turkish banking text. It raises "
        "AssertionError on `%2,89` (percent before the number, the Turkish "
        "convention), reads the Turkish thousands separator as a decimal point, "
        "and expands TL (Türk Lirası) as 'teraliters': `24.180 TL` becomes "
        "'twenty four point one eight oh teraliters' and `27.08.2026` becomes "
        "'the twenty seventh of august twenty twenty six'. The guide's example "
        "text was prose with no numbers, which is why it never surfaced there. "
        "Turkish-finetuned weights read Turkish digits without help.",
    )
    SPEECH_SEGMENT_CHARS: int = Field(
        default=1_500,
        gt=0,
        description="How much text goes into one generate_streaming call. Cut on "
        "sentence boundaries and streamed back to back, so a long answer is one "
        "continuous reading with nothing dropped.",
    )
    SPEECH_QUEUE_TIMEOUT_SECONDS: float = Field(
        default=2.0,
        ge=0,
        description="How long a second reader waits for the model before being "
        "told it is busy. The model is not thread-safe, so readings queue; this "
        "bounds the wait to something a person will sit through rather than "
        "leaving a request hanging behind a long answer.",
    )
    SPEECH_MAX_CHARS: int = Field(
        default=20_000,
        gt=0,
        description="Hard cap on one request's text. Not a truncation -- text "
        "over this is refused with 422 rather than read in part, because a "
        "reading that stops halfway is indistinguishable from a crash.",
    )
    SPEECH_WARM_ON_STARTUP: bool = Field(
        default=False,
        description="Load Trendyol-TTS during API startup. Without it the first "
        "reader pays the ~5.6s load.",
    )
    SPEECH_MPS_HIGH_WATERMARK_RATIO: float = Field(
        default=1.0,
        gt=0,
        le=1.7,
        description="MPS allocation ceiling for the unified-memory speech process.",
    )
    SPEECH_DEVICE: str = Field(
        default="mps",
        description="Torch device. MPS is the Metal path for this model and what "
        "every figure in TTS_ENTEGRASYON.md was measured on. Deliberately *not* "
        "MLX, unlike the Whisper side: section 9 of that guide records MLX as "
        "tried and eliminated -- mlx-audio supports the voxcpm2 architecture but "
        "Trendyol's weights fail to load (`Missing 233 parameters`) because "
        "Trendyol keeps audiovae.pth as a separate PyTorch file where MLX wants "
        "one safetensors, and the ready-made mlx-community/VoxCPM2-4bit port is "
        "base VoxCPM2 rather than the Turkish finetune. Set to `cpu` to prove a "
        "Metal problem is a Metal problem.",
    )

    # ===== Spoken answer shaping (voice mode only) =====
    #
    # A finished answer is markdown -- the supervisor is told to write
    # comparisons as tables with a link after every claim -- and markdown read
    # aloud is asterisks and addresses. This stage rewrites it as prose.
    #
    # Voice mode only. The speaker button on a message keeps using the browser's
    # deterministic converter, which is also the fallback here whenever the
    # route is unavailable: it cannot invent a number, so it is the safe answer
    # to "the model is down", not a second-class one.
    VOICE_RESPONSE_MODEL: str = Field(
        default="gemma",
        description="Fast local model that rewrites one finished answer for speech.",
    )
    VOICE_RESPONSE_MAX_TOKENS: int = Field(
        default=4000,
        ge=300,
        description="Room for a whole answer restated. Larger than the output "
        "guard's budget on purpose: the guard returns a verdict, this returns "
        "the answer again, and a cap that truncates speech mid-sentence is the "
        "failure SPEECH_MAX_CHARS already refuses to commit.",
    )
    VOICE_RESPONSE_MAX_INPUT_CHARS: int = Field(
        default=12_000,
        gt=0,
        description="Longest answer accepted for rewriting. Refused rather than "
        "truncated -- half an answer spoken confidently is worse than none.",
    )

    # ===== Chat attachments =====
    CHAT_ATTACHMENT_MAX_UPLOAD_MB: int = Field(default=20, gt=0, le=100)
    CHAT_ATTACHMENT_MAX_FILES: int = Field(default=8, gt=0, le=20)
    CHAT_ATTACHMENT_MAX_PAGES: int = Field(default=40, gt=0, le=200)
    CHAT_ATTACHMENT_MAX_TOTAL_IMAGES: int = Field(default=40, gt=0, le=200)
    CHAT_ATTACHMENT_MAX_TEXT_CHARS: int = Field(default=100_000, gt=0)
    CHAT_ATTACHMENT_MAX_TOTAL_TEXT_CHARS: int = Field(default=200_000, gt=0)
    CHAT_ATTACHMENT_TTL_SECONDS: int = Field(default=3600, gt=0)
    CHAT_ATTACHMENT_PROCESS_TIMEOUT_SECONDS: int = Field(default=120, gt=0)
    CHAT_ATTACHMENT_RENDER_LONG_EDGE: int = Field(default=1800, gt=0)
    CHAT_ATTACHMENT_JPEG_QUALITY: int = Field(default=88, gt=0, le=100)
    CHAT_ATTACHMENT_SOFFICE_PATH: str = Field(
        default="",
        description="Optional explicit path to LibreOffice's soffice binary for DOCX rendering.",
    )

    # ===== Automations (the user's scheduled agent runs) =====
    AUTOMATIONS_ENABLED: bool = Field(
        default=True,
        description="Run the background loop that fires the user's scheduled "
        "automations. Off leaves the API fully usable -- automations can still "
        "be created, edited and run on demand -- they simply do not fire on "
        "their own, which is what a second API instance or a CI run wants.",
    )
    AUTOMATIONS_POLL_SECONDS: int = Field(
        default=30,
        gt=0,
        le=600,
        description="How often the loop looks for a due automation. Schedules "
        "have minute granularity, so anything under 60 is already exact; 30 "
        "halves the worst-case lateness for the cost of one indexed query.",
    )

    # ===== Email reports =====
    EMAIL_SMTP_HOST: str = ""
    EMAIL_SMTP_PORT: int = Field(default=587, gt=0, lt=65536)
    EMAIL_SMTP_USERNAME: str = ""
    EMAIL_SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""
    EMAIL_USE_TLS: bool = True

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
            "OUTPUT_GUARD_MODEL": self.OUTPUT_GUARD_MODEL,
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
