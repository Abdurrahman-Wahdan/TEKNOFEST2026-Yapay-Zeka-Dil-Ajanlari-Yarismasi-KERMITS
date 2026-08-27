"""The app.

    uvicorn api.main:app --reload --port 8000

Docs at /docs, and the OpenAPI schema at /openapi.json -- which is what
`UI/ npm run api:types` reads to generate the frontend's TypeScript types.
"""

import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.shared.checkpoints import close_checkpointer, get_checkpointer
from config.settings import settings
from voice_models import (
    VoiceSpeechUnavailable,
    VoiceTranscriptionUnavailable,
    close_voice_providers,
    get_synthesizer,
    get_transcriber,
)

from .automations import loop as automation_loop
from .routers import ROUTERS

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
    """Warm STT in the background so tunnel/model latency cannot block startup."""
    if not settings.VOICE_WARM_ON_STARTUP:
        return
    transcriber = get_transcriber()
    # The remote Whisper worker currently becomes unavailable after repeated
    # inference calls while its health endpoint remains green. A synthetic warm
    # request therefore consumes a useful inference slot and makes the first
    # real recording more likely to hit the stuck worker. Local checkpoints
    # still benefit from startup loading; remote inference starts on demand.
    if transcriber.provider_name == "remote":
        return

    def warm() -> None:
        try:
            transcriber.warm()
        except VoiceTranscriptionUnavailable as exc:
            logger.warning("Voice model was not warmed: %s", exc)
        except Exception:
            logger.exception("Voice model warm-up failed")

    threading.Thread(target=warm, name="remote-stt-warm", daemon=True).start()


@app.on_event("startup")
def start_speech_synthesis() -> None:
    """Warm local TTS only; remote TTS must not race a user reading request.

    The remote Trendyol service is a single-reader process. A background warm
    request can hold that reader while the first real history playback is
    waiting, which makes the API queue return 503 even though the endpoint is
    healthy. Remote requests already have their own streamed timeout/retry path,
    so they are started on demand instead.
    """
    if not settings.SPEECH_WARM_ON_STARTUP:
        return
    synthesizer = get_synthesizer()
    if synthesizer.provider_name == "remote":
        return

    def warm() -> None:
        try:
            synthesizer.warm()
        except VoiceSpeechUnavailable as exc:
            # The speech extra is optional. Everything else stays useful without
            # it, and POST /voice/speech reports the same 503.
            logger.warning("Speech model was not warmed: %s", exc)
        except Exception:
            logger.exception("Speech model warm-up failed")

    threading.Thread(target=warm, name="remote-tts-warm", daemon=True).start()


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


@app.on_event("shutdown")
def stop_voice_providers() -> None:
    close_voice_providers()

logger.info(
    "TF26 API ready — environment=%s, CORS origins=%s",
    settings.ENVIRONMENT,
    ", ".join(settings.cors_origins),
)
