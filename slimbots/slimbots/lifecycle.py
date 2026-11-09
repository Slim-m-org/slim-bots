"""Clean shutdown on SIGTERM/SIGINT (docker sends it on every redeploy), and containing one bad handler's crash."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from websockets.exceptions import WebSocketException

from .http import ApiError, is_token_revoked

log = logging.getLogger(__name__)

OUTSIDE_FAILURES = (ApiError, OSError, WebSocketException)


def log_failure(logger: logging.Logger, err: BaseException, message: str, *args: Any) -> None:
    """Logs `message` with `err`'s traceback; an API, network or websocket failure is outside the bot and gets one line."""
    if isinstance(err, OUTSIDE_FAILURES):
        logger.warning(message + ": %s: %s", *args, type(err).__name__, err)
    else:
        logger.error(message, *args, exc_info=err)


async def run_with_shutdown(task: asyncio.Task[Any]) -> Any:
    """Awaits `task`; SIGTERM/SIGINT cancel it. The CancelledError always propagates - the caller decides what it means."""
    loop = asyncio.get_running_loop()

    def cancel() -> None:
        print("shutting down", file=sys.stderr)
        task.cancel()

    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, cancel)
            installed.append(sig)
        except NotImplementedError:
            pass

    try:
        return await task
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


async def guard_dispatch(
    func: Callable[..., Awaitable[Any]], *args: Any,
    on_error: Callable[[Exception], Awaitable[None]] | None = None, **kwargs: Any,
) -> Any:
    """Runs one dispatched handler, swallowing anything but a revoked token (401 must stop the loop)."""
    try:
        return await func(*args, **kwargs)
    except Exception as err:
        if is_token_revoked(err):
            raise
        if on_error:
            await on_error(err)
        else:
            log_failure(log, err, "unhandled error in %s", getattr(func, "__qualname__", None) or repr(func))
        return None


@dataclass
class Periodic:
    """One `bot.every` task: what it runs, how often, and which errors must stop the bot."""

    func: Callable[[], Awaitable[Any]]
    seconds: float | Callable[[], float]
    fatal: tuple[type[BaseException], ...] = ()
    delay_first: bool = False

    def interval(self) -> float:
        return self.seconds() if callable(self.seconds) else self.seconds


async def run_periodic(name: str, spec: Periodic) -> None:
    """Runs `spec.func` forever; a failure is logged and retried next cycle unless it is a revoked token or `fatal`."""
    if spec.delay_first:
        await asyncio.sleep(spec.interval())
    while True:
        wait = 0.0
        try:
            result = await spec.func()
            if isinstance(result, (int, float)) and not isinstance(result, bool):
                wait = result
        except Exception as err:
            if is_token_revoked(err) or isinstance(err, spec.fatal):
                raise
            log_failure(log, err, "%s failed, retrying next cycle", name)
        await asyncio.sleep(max(spec.interval(), wait))
