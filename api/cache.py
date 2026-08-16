"""A short-lived cache in front of the bank endpoints.

Nothing sat between the browser and the banks. That was survivable while every
call was a deliberate "Compare" click, and stops being survivable the moment a
page polls: an FX board refreshing every few seconds, open in three tabs, is
three requests per second per bank forever. Two of these boards are page reads
now, and Albaraka's WAF fingerprints the TLS handshake -- getting the
deployment's IP banned is not something that can be undone from here.

So the browser polls as fast as it likes and the banks see one request per TTL.

**Single-flight is the point, not the memoisation.** Twenty tabs refreshing
together would otherwise be twenty simultaneous fan-outs on a cold key; the
lock means the first caller fetches and the other nineteen wait for its answer.

Deliberately not inside `banks/`: a caller asking "check this bank right now"
-- the health check, the audit, an agent verifying a complaint -- must still
reach the bank. Only the HTTP layer caches.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class _Entry:
    value: object = None
    stored_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    error: BaseException | None = None


class TTLCache:
    """Memoise per key for `ttl` seconds, one fetch at a time per key."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._entries: dict[str, _Entry] = {}
        self._guard = threading.Lock()

    def _entry(self, key: str) -> _Entry:
        with self._guard:
            return self._entries.setdefault(key, _Entry())

    def fresh(self, key: str) -> bool:
        entry = self._entry(key)
        return entry.stored_at > 0 and (time.monotonic() - entry.stored_at) < self.ttl

    def age(self, key: str) -> float:
        entry = self._entry(key)
        return time.monotonic() - entry.stored_at if entry.stored_at else 0.0

    def get(self, key: str, build: Callable[[], T]) -> T:
        """The cached value, or `build()`'s, with one build in flight per key.

        A failure is cached too, for the same TTL. Without that, a bank that is
        down turns every poll into a fresh timeout and the page gets slower the
        more broken the bank is -- the opposite of what should happen.
        """
        entry = self._entry(key)
        if self.fresh(key):
            if entry.error:
                raise entry.error
            return entry.value  # type: ignore[return-value]

        with entry.lock:
            # Someone may have filled it while this thread waited for the lock.
            if self.fresh(key):
                if entry.error:
                    raise entry.error
                return entry.value  # type: ignore[return-value]
            try:
                entry.value, entry.error = build(), None
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                entry.value, entry.error = None, exc
            entry.stored_at = time.monotonic()

        if entry.error:
            raise entry.error
        return entry.value  # type: ignore[return-value]

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()


# FX moves intraday and the banks restate it every minute or so, so a few
# seconds is short enough that a board never shows a stale price and long
# enough that polling costs the banks nothing.
RATES_TTL = 15.0

rates = TTLCache(RATES_TTL)


def clear_all() -> None:
    """Drop every cached response. Used by tests."""
    rates.clear()
