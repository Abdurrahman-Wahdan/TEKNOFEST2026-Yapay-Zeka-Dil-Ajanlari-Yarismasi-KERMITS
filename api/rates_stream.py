"""The live FX board: polled once here, pushed to every viewer.

The board has to read as alive — a price that moved should be on screen within
a second or two — and the banks cannot be asked at that rate. Two of the six
boards are page reads and Albaraka's WAF fingerprints the TLS handshake, so
"every tab polls every two seconds" is how the deployment's IP gets banned.

So the two rates are separated:

  **banks -> us**   one poller, `POLL_SECONDS` apart, six banks in parallel.
  **us -> browser** a socket that pushes the moment a poll lands.

A viewer therefore sees a change as soon as it exists, and the banks see a
fixed, small, predictable load no matter how many people are watching. Every
figure is still the bank's own: this moves numbers around, it never makes one
up, and a bank that fails simply keeps its last board with the failure noted.

The poller runs only while someone is connected. An empty page should not be
calling six banks all night.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from banks import get_bank, list_banks

from .converters import rate_out

logger = logging.getLogger(__name__)

# How often the banks are asked. Their own boards move on the order of a
# minute; this is well inside that and still only six requests per interval,
# shared by every viewer.
POLL_SECONDS = 3.0

# How long to keep polling after the last viewer disconnects, so a refresh or a
# tab switch does not tear the poller down and immediately rebuild it.
LINGER_SECONDS = 30.0


@dataclass
class Board:
    """The latest board for one bank, and how it went."""

    bank: str
    rates: list = field(default_factory=list)
    error: str = ""
    fetched_at: float = 0.0


class RatesHub:
    """Polls every publishing bank and fans the result out to subscribers."""

    def __init__(self) -> None:
        self._boards: dict[str, Board] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._last_wanted = 0.0
        self._version = 0

    # ----- what a caller sees -----

    def snapshot(self) -> dict:
        """Every bank's latest board, in one message."""
        return {
            "type": "rates",
            "version": self._version,
            "banks": {
                name: {
                    "rates": board.rates,
                    "error": board.error,
                    "fetched_at": board.fetched_at,
                }
                for name, board in sorted(self._boards.items())
            },
        }

    @contextlib.asynccontextmanager
    async def subscribe(self):
        """Yield a queue receiving a snapshot after every poll."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        self._last_wanted = time.monotonic()
        self._ensure_running()
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
            self._last_wanted = time.monotonic()

    # ----- the poller -----

    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        names = [n for n, e in sorted(list_banks().items()) if "rates" in e["publishes"]]
        logger.info("Rates poller started for %s", ", ".join(names))
        try:
            while True:
                await self._poll(names)
                self._publish()
                if not self._subscribers and (
                    time.monotonic() - self._last_wanted > LINGER_SECONDS
                ):
                    logger.info("Rates poller idle; stopping until someone watches again")
                    return
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # noqa: BLE001 - the poller must not die silently
            logger.exception("Rates poller stopped unexpectedly")
            raise

    async def _poll(self, names: list[str]) -> None:
        """One round, every bank at once.

        Each bank runs in a worker thread: the providers are synchronous and
        blocking, and awaiting them on the event loop would stall the sockets
        this exists to feed.
        """
        results = await asyncio.gather(
            *(asyncio.to_thread(self._fetch, name) for name in names),
            return_exceptions=True,
        )
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                # Keep whatever we had. A bank that is down should freeze on its
                # last published board, not blank the column.
                board = self._boards.setdefault(name, Board(bank=name))
                board.error = f"{type(result).__name__}: {result}"[:200]
                continue
            self._boards[name] = result

    @staticmethod
    def _fetch(name: str) -> Board:
        provider = get_bank(name)
        rows = [rate_out(r).model_dump() for r in provider.rates()]
        return Board(bank=name, rates=rows, fetched_at=time.time())

    def _publish(self) -> None:
        self._version += 1
        message = self.snapshot()
        for queue in list(self._subscribers):
            # A viewer that has not drained the last message only wants the
            # newest one: this is a board, not a log, and an intermediate tick
            # nobody rendered is worth nothing.
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(message)


hub = RatesHub()
