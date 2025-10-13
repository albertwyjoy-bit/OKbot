import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any

_loop = asyncio.new_event_loop()


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """
    Run a coroutine on the global asyncio loop.

    Ensures that Ctrl-C cancellations give the coroutine a chance to
    handle `asyncio.CancelledError` before we bubble the `KeyboardInterrupt`
    back to the caller.
    """
    task = _loop.create_task(coro)
    try:
        return _loop.run_until_complete(task)
    except KeyboardInterrupt:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                _loop.run_until_complete(task)
        raise
