"""The app.

    uvicorn api.main:app --reload --port 8000

Docs at /docs, and the OpenAPI schema at /openapi.json -- which is what
`UI/ npm run api:types` reads to generate the frontend's TypeScript types.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings

from .automations import loop as automation_loop
from .routers import ROUTERS
from .voice_speech import VoiceSpeechUnavailable, warm_speech_model
from .voice_transcription import VoiceTranscriptionUnavailable, warm_voice_model
from agents.shared.checkpoints import close_checkpointer, get_checkpointer

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TF26 API",
    description=(
        "Turkish participation-banking campaigns and live pricing. "
        "This service authenticates callers and exposes what `banks/`, "
        "`index/` and `corpus/` already do; it holds no banking logic itself."
    ),
    version="0.1.0",
    # Every operation gets a readable id, so the generated TypeScript client has
    # `getBankFinanceQuote()` rather than the default
    # `bank_finance_quote_api_banks__bank__finance_get()`.
    generate_unique_id_function=lambda route: route.name,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router, prefix="/api")


@app.on_event("startup")
def start_agent_checkpointer() -> None:
    """Create LangGraph's durable checkpoint tables before serving chat."""
    get_checkpointer()


@app.on_event("startup")
def start_voice_transcription() -> None:
    """Warm Metal inference before the first user waits for a transcript."""
    if not settings.VOICE_WARM_ON_STARTUP:
        return
    try:
        warm_voice_model()
    except VoiceTranscriptionUnavailable as exc:
        # Banking and text chat remain useful while a workstation is downloading
        # the optional local checkpoint; the voice endpoint reports the same 503.
        logger.warning("Voice model was not warmed: %s", exc)
    except Exception:
        # A corrupt/incompatible optional checkpoint must be loud in the logs,
        # but it must not take banking, comparison and text chat down with it.
        logger.exception("Voice model warm-up failed")


@app.on_event("startup")
def start_speech_synthesis() -> None:
    """Load Trendyol-TTS before the first user asks for an answer to be read.

    Worth doing here and not lazily: a cold Hugging Face cache downloads ~5 GB,
    and a warm one still costs ~5.6s. Both belong in startup rather than in the
    request of whoever happens to press the speaker first.
    """
    if not settings.SPEECH_WARM_ON_STARTUP:
        return
    try:
        warm_speech_model()
    except VoiceSpeechUnavailable as exc:
        # The speech extra is optional. Everything else stays useful without it,
        # and POST /voice/speech reports the same 503.
        logger.warning("Speech model was not warmed: %s", exc)
    except Exception:
        logger.exception("Speech model warm-up failed")


@app.on_event("startup")
def start_automation_loop() -> None:
    """Begin firing the users' scheduled automations.

    Safe to call in every process: exactly one wins an advisory lock and polls,
    and the rest log why they are not. See `api/automations/loop.py`.
    """
    automation_loop.start()


@app.on_event("shutdown")
def stop_automation_loop() -> None:
    automation_loop.stop()


@app.on_event("shutdown")
def stop_agent_checkpointer() -> None:
    close_checkpointer()

logger.info(
    "TF26 API ready — environment=%s, CORS origins=%s",
    settings.ENVIRONMENT,
    ", ".join(settings.cors_origins),
)
