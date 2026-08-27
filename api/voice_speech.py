"""Warm, serialized Trendyol-TTS inference for reading answers aloud.

The sibling of `voice_transcription.py` and deliberately built the same way: one
model resident in the process, a lock so two requests cannot use it at once, and
a lazy import so a machine without the optional dependency still serves banking,
comparison and text chat.

What differs is the shape of the work. Whisper takes a whole recording and
returns a whole string; this streams -- `generate_streaming` yields ~160 ms of
audio at a time and the first chunk arrives in ~0.13 s, so the user hears the
answer begin while the rest of it is still being generated. Holding the whole
utterance to send it in one piece would throw that away and replace it with the
several seconds of silence the streaming exists to remove.

Measured on an M5 Max / 128 GB / MPS, per `TTS_ENTEGRASYON.md`:

    model load    ~5.6 s   once per process
    first audio   ~0.13 s  at inference_timesteps=10
    generation    ~1.9x real time
    chunk         ~160 ms, 48 kHz mono float32
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

from config.settings import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

# The model is not thread-safe: two requests sharing one instance corrupt each
# other's output. One instance behind one lock rather than N instances, because
# each is ~5 GB and MPS exhausts memory long before it exhausts patience --
# generation runs ~1.9x faster than speech, so one instance keeps up with several
# readers by queueing.
_INFERENCE_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()
_MODEL = None

# Completed readings are safe to reuse because the voice and generation settings
# belong to this API process. Keep this deliberately small: a long answer at
# 48kHz mono PCM can consume several megabytes, and the cache must never compete
# with the resident model for MPS/unified memory.
_AUDIO_CACHE_LOCK = threading.Lock()
_AUDIO_CACHE: OrderedDict[str, tuple[bytes, ...]] = OrderedDict()
_AUDIO_CACHE_BYTES = 0
_AUDIO_CACHE_MAX_ENTRIES = 8
_AUDIO_CACHE_MAX_BYTES = 32 * 1024 * 1024


class VoiceSpeechUnavailable(RuntimeError):
    """The optional runtime or checkpoint is not ready."""


class VoiceSpeechFailed(RuntimeError):
    """The text could not be spoken."""


def _runtime():
    """Import the optional dependency lazily.

    `voxcpm` pulls in `datasets`, which pins `fsspec` down to 2025.3.0. That is
    compatible with what this project already requires (`huggingface_hub` asks
    for >=2023.5.0 and `torch` for >=0.8.5), so it shares the environment -- but
    the import still belongs here rather than at module scope, so that an
    install without the speech extra starts the API instead of failing it.
    """
    try:
        from voxcpm.core import VoxCPM
    except (ImportError, OSError) as exc:
        raise VoiceSpeechUnavailable(
            "Trendyol-TTS is unavailable: install the speech dependencies "
            "(`pip install voxcpm soundfile`)."
        ) from exc
    return VoxCPM


def _model_path() -> Path:
    configured = Path(settings.SPEECH_MODEL_PATH).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _require_model() -> Path:
    """The local checkpoint, or a clear reason why there is not one.

    The same rule the Whisper side applies, for the same reason: a request must
    never be able to start a multi-gigabyte download. `from_pretrained` would
    happily fetch from the Hub on a cache miss, so the path is checked *here*
    and the loader is then pinned to local files only -- otherwise the failure
    mode is not an error but a user waiting several minutes inside one HTTP
    request with nothing on screen.

    Verified more loosely than Whisper's single `weights.npz`, and deliberately:
    this checkpoint is a directory of safetensors shards plus a separate
    `audiovae.pth`, so there is no one file to checksum. `config.json` is the
    cheap proof that a real checkpoint is present rather than the empty shell an
    interrupted download leaves; anything corrupt past that point surfaces when
    the model loads, which is at startup and not inside a request.
    """
    path = _model_path()
    if not path.is_dir() or not (path / "config.json").is_file():
        raise VoiceSpeechUnavailable(
            f"The local speech checkpoint is missing at {path}. "
            "Run `python scripts/download_speech_model.py`."
        )
    # `snapshot_download` writes part files under `.cache/huggingface/download/`
    # and renames them into place as each one finishes, so a leftover means the
    # checkpoint is either mid-download or was interrupted. Both are the same
    # thing to a reader -- some of the weights are not there -- and both are
    # fixed by re-running the script, which resumes.
    if any(path.rglob("*.incomplete")) or any(path.rglob("*.aria2")):
        raise VoiceSpeechUnavailable(
            f"The local speech checkpoint at {path} is incomplete — it is still "
            "downloading, or a download was interrupted. Run "
            "`python scripts/download_speech_model.py` to finish it."
        )
    return path


def _load_model():
    """The one resident model, loaded at most once per process.

    Double-checked under `_MODEL_LOCK` rather than `_INFERENCE_LOCK`: loading
    takes ~5.6 s and two requests arriving cold would otherwise both pay it and
    leave 10 GB resident. The inference lock stays free while this runs so the
    wait is reported as a wait, not as a hang.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    VoxCPM = _runtime()
    path = _require_model()
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        started = time.perf_counter()
        try:
            model = VoxCPM.from_pretrained(
                # A local directory, not a repo id. `from_pretrained` tests
                # `os.path.isdir` on this argument and loads it directly when it
                # is one, which is how the checkpoint stays under TF26_data
                # beside the Whisper one instead of in ~/.cache.
                hf_model_id=str(path),
                # Belt and braces on top of that: nothing in a request may reach
                # the network, whatever the path turns out to be.
                local_files_only=True,
                # No input audio anywhere in this path, so the denoiser is weight
                # we would load and never call.
                load_denoiser=False,
                optimize=True,
                # No `lora_config`/`lora_weights_path`, and that is correct
                # rather than an omission. The checkpoint's `merge_manifest.json`
                # reports `artifact_type: merged_voxcpm2_lora` -- the Turkish
                # adapter is already merged into `model.safetensors`, and the
                # `lora_adapter/` directory beside it is preserved for reference
                # only. Passing it again would re-apply an adapter that is
                # already in the weights.
                # MPS, and named rather than left to `auto`. This is the Metal
                # path for this model and what every measurement in
                # TTS_ENTEGRASYON.md was taken on -- and *not* MLX, which section
                # 9 of that guide records as tried and eliminated: Trendyol keeps
                # audiovae.pth as a separate PyTorch file where mlx-audio wants a
                # single safetensors, so the weights fail to load, and the
                # ready-made MLX port is base VoxCPM2 without the Turkish LoRA.
                device=settings.SPEECH_DEVICE,
            )
        except Exception as exc:
            raise VoiceSpeechUnavailable(
                f"Trendyol-TTS could not be loaded from {path}: {exc}"
            ) from exc
        _MODEL = model
        logger.info(
            "Speech model warm — path=%s device=%s elapsed=%.2fs sample_rate=%s",
            path,
            settings.SPEECH_DEVICE,
            time.perf_counter() - started,
            sample_rate(),
        )
        return _MODEL


def sample_rate() -> int:
    """The rate the model generates at. 48 kHz, and the client must match it.

    Read off the loaded model rather than hardcoded, and sent to the browser on
    the response so the two cannot drift: an `AudioContext` built at the wrong
    rate does not fail, it plays the answer at the wrong pitch.
    """
    if _MODEL is None:
        return settings.SPEECH_SAMPLE_RATE
    try:
        return int(_MODEL.tts_model.sample_rate)
    except AttributeError:
        return settings.SPEECH_SAMPLE_RATE


def warm_speech_model() -> None:
    """Load the checkpoint during startup so the first reader does not wait 5.6 s."""
    _load_model()


def cached_audio(text: str) -> tuple[bytes, ...] | None:
    """Return a completed reading, promoting it as the most-recent cache item."""
    with _AUDIO_CACHE_LOCK:
        chunks = _AUDIO_CACHE.get(text)
        if chunks is None:
            return None
        _AUDIO_CACHE.move_to_end(text)
        return chunks


def remember_audio(text: str, chunks: list[bytes]) -> None:
    """Store only a fully generated reading, bounded by entries and bytes."""
    global _AUDIO_CACHE_BYTES
    frozen = tuple(chunks)
    size = sum(len(chunk) for chunk in frozen)
    if not frozen or size > _AUDIO_CACHE_MAX_BYTES:
        return
    with _AUDIO_CACHE_LOCK:
        previous = _AUDIO_CACHE.pop(text, None)
        if previous is not None:
            _AUDIO_CACHE_BYTES -= sum(len(chunk) for chunk in previous)
        _AUDIO_CACHE[text] = frozen
        _AUDIO_CACHE_BYTES += size
        while (
            len(_AUDIO_CACHE) > _AUDIO_CACHE_MAX_ENTRIES
            or _AUDIO_CACHE_BYTES > _AUDIO_CACHE_MAX_BYTES
        ):
            _, evicted = _AUDIO_CACHE.popitem(last=False)
            _AUDIO_CACHE_BYTES -= sum(len(chunk) for chunk in evicted)


def clear_audio_cache() -> None:
    """Clear completed readings, primarily for tests and local model changes."""
    global _AUDIO_CACHE_BYTES
    with _AUDIO_CACHE_LOCK:
        _AUDIO_CACHE.clear()
        _AUDIO_CACHE_BYTES = 0


def prepare() -> int:
    """Make sure the model is loaded, and report the rate it generates at.

    Called before the response starts, and that is the whole point of it being
    separate from `speak`. Loading is where "the speech dependency is not
    installed" and "the checkpoint will not load" are discovered, and both need
    to reach the caller as a 503 -- inside the generator they would arrive after
    the status line had already gone out as 200, and the browser would see a
    successful, empty reading.
    """
    _load_model()
    return sample_rate()


#: Sentence terminators, and the rule for which ones actually end a sentence.
#:
#: A full stop only ends one when whitespace or the end of the text follows it.
#: Turkish groups thousands with full stops and writes dates as 27.08.2026, and
#: these answers are made of both -- the same rule `speech-text.ts` applies in the
#: browser, for the same reason.
_TERMINATOR = re.compile(r"[.!?…]+")


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in _TERMINATOR.finditer(text):
        end = match.end()
        following = text[end : end + 1]
        if following and not following.isspace():
            continue
        pieces.append(text[start:end])
        start = end
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def segments(text: str, budget: int | None = None) -> list[str]:
    """Cut the text into pieces the model generates in one pass, losing nothing.

    `max_len` bounds what one `generate_streaming` call will produce, and a text
    over that bound comes back *silently short* -- the end of the answer simply
    never gets spoken, with no error to notice. Segmenting first and generating
    each piece into the same stream is what makes that impossible: the listener
    hears one continuous reading either way, and nothing is dropped.

    Cut on sentence boundaries, never mid-sentence, because the seam between two
    segments is audible and a seam in the middle of a clause sounds like a fault.
    A single sentence over the budget is passed whole rather than cut: a wrong
    pause is worse than a long segment.
    """
    limit = budget or settings.SPEECH_SEGMENT_CHARS
    pieces: list[str] = []
    current = ""
    for sentence in _sentences(text):
        candidate = f"{current}{sentence}" if current else sentence
        if current and len(candidate.strip()) > limit:
            pieces.append(current.strip())
            current = sentence
            continue
        current = candidate
    if current.strip():
        pieces.append(current.strip())
    return pieces


def speak(text: str):
    """Stream one answer as 16-bit PCM at `sample_rate()`, chunk by chunk.

    A generator of `bytes`, not of arrays: the caller is an HTTP response, and
    converting here keeps the numpy dependency and the clipping rule in one
    place. 16-bit because that is what a browser `AudioContext` wants and it
    halves the bytes on the wire against float32, with no audible cost at 48 kHz.

    The inference lock is held for the whole reading. That is the queueing the
    model's thread-unsafety requires, and it is why the caller acquires the lock
    *before* the response starts -- a second reader is told the model is busy
    while it can still be told anything.
    """
    import numpy as np

    model = _load_model()
    started = time.perf_counter()
    first_audio: float | None = None
    total = 0

    try:
        for segment in segments(text):
            for chunk in model.generate_streaming(
                text=segment,
                # Every value below is measured, not chosen. See the guide:
                # timesteps=10 is counter-intuitively better than 4 -- lower is
                # faster overall but delays and destabilises the *first* chunk
                # (±0.44 s), and in streaming the delay a user feels is the first
                # one. 2 makes the model skip parts of the text outright.
                cfg_value=settings.SPEECH_CFG_VALUE,
                inference_timesteps=settings.SPEECH_TIMESTEPS,
                max_len=settings.SPEECH_MAX_LEN,
                # Off, and against the guide's advice -- see SPEECH_NORMALIZE.
                # voxcpm's normaliser is wetext's *English* model: it raises on
                # `%2,89`, reads the Turkish thousands separator as a decimal
                # point, and says "teraliters" for TL.
                normalize=settings.SPEECH_NORMALIZE,
                denoise=False,
            ):
                if first_audio is None:
                    first_audio = time.perf_counter() - started
                pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16)
                total += pcm.size
                yield pcm.tobytes()
    except VoiceSpeechUnavailable:
        raise
    except Exception as exc:
        raise VoiceSpeechFailed("The answer could not be spoken.") from exc
    finally:
        rate = sample_rate()
        logger.info(
            "speech_read chars=%d first_audio=%s audio=%.1fs elapsed=%.1fs",
            len(text),
            f"{first_audio:.2f}s" if first_audio is not None else "none",
            total / rate if rate else 0.0,
            time.perf_counter() - started,
        )


def acquire(timeout: float | None = None) -> bool:
    """Take the inference lock, or report that the model is busy.

    Separate from `speak` on purpose. The model serves one reader at a time, and
    a second one has to be refused *before* the response body starts -- once the
    first byte of a stream is out, there is no status code left to send.
    """
    return _INFERENCE_LOCK.acquire(
        timeout=settings.SPEECH_QUEUE_TIMEOUT_SECONDS if timeout is None else timeout
    )


def release() -> None:
    try:
        _INFERENCE_LOCK.release()
    except RuntimeError:
        # Already released. Releasing twice is a bug in the caller, not a reason
        # to fail a request that has otherwise finished.
        logger.warning("Speech lock released twice")
