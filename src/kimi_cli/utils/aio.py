import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any

_loop = asyncio.new_event_loop()


async def _cancel_pending(tasks: set[asyncio.Task[Any]]):
    if not tasks:
        return
    for pending in tasks:
        pending.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _drain(awaitable: asyncio.Future[Any] | Coroutine[Any, Any, Any]):
    future = awaitable if isinstance(awaitable, asyncio.Future) else _loop.create_task(awaitable)
    while True:
        try:
            return _loop.run_until_complete(future)
        except KeyboardInterrupt:
            continue


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
                _drain(task)
        raise
    finally:
        pending = {
            pending_task
            for pending_task in asyncio.all_tasks(_loop)
            if pending_task is not task and not pending_task.done()
        }
        if pending:
            with suppress(asyncio.CancelledError):
                _drain(_cancel_pending(pending))
        _drain(_loop.shutdown_asyncgens())
