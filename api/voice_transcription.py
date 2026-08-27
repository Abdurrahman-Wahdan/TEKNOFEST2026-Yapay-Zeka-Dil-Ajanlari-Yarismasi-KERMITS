"""Warm, serialized MLX Whisper inference for browser voice recordings.

MLX uses the M1 Max GPU and unified memory. The model holder inside
``mlx-whisper`` keeps one checkpoint resident; the lock prevents two HTTP
workers from mutating that global holder or overcommitting the GPU together.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path

from config.settings import PROJECT_ROOT, settings
from voice_models.errors import VoiceTranscriptionFailed, VoiceTranscriptionUnavailable

logger = logging.getLogger(__name__)

_INFERENCE_LOCK = threading.Lock()
_VERIFIED_MODEL: tuple[Path, int, int, str] | None = None


def _model_path() -> Path:
    configured = Path(settings.VOICE_MODEL_PATH).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _runtime():
    """Import Metal-only dependencies lazily so Linux can still import the API."""
    try:
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.transcribe import ModelHolder
    except (ImportError, OSError) as exc:
        raise VoiceTranscriptionUnavailable(
            "MLX Whisper is available only on an Apple-Silicon runtime with "
            "the voice dependencies installed."
        ) from exc
    if not mx.metal.is_available():
        raise VoiceTranscriptionUnavailable("The MLX Metal backend is unavailable.")
    return mx, mlx_whisper, ModelHolder


def _require_model() -> Path:
    global _VERIFIED_MODEL
    path = _model_path()
    weights = path / "weights.npz"
    if not (path / "config.json").is_file() or not weights.is_file():
        raise VoiceTranscriptionUnavailable(
            f"The local voice checkpoint is incomplete at {path}."
        )
    stat = weights.stat()
    if stat.st_size != settings.VOICE_MODEL_BYTES or weights.with_suffix(
        ".npz.aria2"
    ).exists():
        raise VoiceTranscriptionUnavailable(
            f"The local voice checkpoint is still downloading at {path}."
        )

    expected = settings.VOICE_MODEL_SHA256.lower()
    verification = (weights, stat.st_size, stat.st_mtime_ns, expected)
    if _VERIFIED_MODEL != verification:
        digest = hashlib.sha256()
        with weights.open("rb") as source:
            for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise VoiceTranscriptionUnavailable(
                f"The local voice checkpoint failed checksum verification at {path}."
            )
        _VERIFIED_MODEL = verification
    return path


def warm_voice_model() -> None:
    """Load the checkpoint and compile the 30-second Whisper graph once."""
    import numpy as np

    path = _require_model()
    mx, mlx_whisper, ModelHolder = _runtime()
    started = time.perf_counter()
    with _INFERENCE_LOCK:
        ModelHolder.get_model(str(path), dtype=mx.float16)
        # Whisper pads every short recording to this same graph shape. A silent
        # second pays the first Metal compilation here instead of after the user
        # has pressed Stop and is waiting for text.
        mlx_whisper.transcribe(
            np.zeros(16_000, dtype=np.float32),
            path_or_hf_repo=str(path),
            language=settings.VOICE_LANGUAGE,
            task="transcribe",
            temperature=0.0,
            condition_on_previous_text=False,
            fp16=True,
            verbose=None,
        )
    logger.info(
        "Voice model warm — path=%s elapsed=%.2fs peak_memory=%.2fGB",
        path,
        time.perf_counter() - started,
        mx.get_peak_memory() / 1_000_000_000,
    )


def transcribe_voice(audio_path: Path) -> tuple[str, int]:
    """Transcribe one complete short-form recording as forced Turkish."""
    path = _require_model()
    _, mlx_whisper, _ = _runtime()
    started = time.perf_counter()
    try:
        with _INFERENCE_LOCK:
            result = mlx_whisper.transcribe(
                str(audio_path),
                path_or_hf_repo=str(path),
                language=settings.VOICE_LANGUAGE,
                task="transcribe",
                temperature=0.0,
                condition_on_previous_text=False,
                fp16=True,
                verbose=None,
            )
    except VoiceTranscriptionUnavailable:
        raise
    except Exception as exc:
        raise VoiceTranscriptionFailed("The audio could not be transcribed.") from exc

    text = str(result.get("text", "")).strip()
    return text, round((time.perf_counter() - started) * 1000)
