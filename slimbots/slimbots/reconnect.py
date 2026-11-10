"""How long the `Bot` waits between connection attempts; see docs/framework.md."""

from __future__ import annotations

import random
import time
from typing import Callable

STABLE_SECONDS = 30.0
JITTER_RANGE = (0.5, 1.5)


class Backoff:
    """Doubles from `base` to `cap`, jitters each wait, and resets only after a connection stayed ready for `stable_after`."""

    def __init__(
        self, base: float, cap: float, *, stable_after: float = STABLE_SECONDS,
        clock: Callable[[], float] = time.monotonic, jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._base = base
        self._cap = cap
        self._stable_after = stable_after
        self._clock = clock
        self._jitter = jitter
        self._delay = base
        self._ready_at: float | None = None

    def connecting(self) -> None:
        """Marks a new attempt, so a previous connection's readiness does not count for it."""
        self._ready_at = None

    def ready(self) -> None:
        """Marks the connection as caught up and serving; the wait only resets if it then stays up."""
        self._ready_at = self._clock()

    def next_wait(self) -> float:
        """Seconds to sleep before the next attempt, called once per ended connection."""
        if self._ready_at is not None and self._clock() - self._ready_at >= self._stable_after:
            self._delay = self._base
        wait = self._delay * self._jitter(*JITTER_RANGE)
        self._delay = min(self._delay * 2, self._cap)
        return wait
