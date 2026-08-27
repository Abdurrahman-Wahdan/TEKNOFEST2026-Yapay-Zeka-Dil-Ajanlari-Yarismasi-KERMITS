"""Provider contracts for speech-to-text and text-to-speech."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path


class BaseTranscriptionProvider(ABC):
    """A source of speech-to-text inference."""

    provider_name: str = ""

    def warm(self) -> None:
        """Prepare the provider before traffic. Remote providers need no warm-up."""

    def close(self) -> None:
        """Release any connection pool owned by the provider."""

    @abstractmethod
    def transcribe(self, audio_path: Path) -> tuple[str, int]:
        """Return the transcript and provider processing time in milliseconds."""


class BaseSpeechProvider(ABC):
    """A source of mono, little-endian signed 16-bit PCM speech."""

    provider_name: str = ""

    def warm(self) -> None:
        """Prepare the provider before traffic. Remote providers need no warm-up."""

    def close(self) -> None:
        """Release any connection pool owned by the provider."""

    def cached_audio(self, text: str) -> tuple[bytes, ...] | None:
        return None

    def remember_audio(self, text: str, chunks: list[bytes]) -> None:
        del text, chunks

    def audio_cache_max_bytes(self) -> int:
        return 0

    def acquire(self) -> bool:
        """Reserve a non-concurrent provider. Remote servers manage their own queue."""
        return True

    def release(self) -> None:
        """Release a reservation made by :meth:`acquire`."""

    @abstractmethod
    def prepare(self) -> int:
        """Return the PCM sample rate before response headers are sent."""

    @abstractmethod
    def speak(self, text: str) -> Iterable[bytes]:
        """Yield mono, little-endian signed 16-bit PCM chunks."""
