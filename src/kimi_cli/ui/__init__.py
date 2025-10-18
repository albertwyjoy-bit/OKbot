import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any

from kimi_cli.soul import Soul
from kimi_cli.soul.event import EventQueue
from kimi_cli.utils.logging import logger

type VisualizeFn = Callable[[EventQueue], Coroutine[Any, Any, None]]
"""A long-running async function to visualize the agent behavior."""


class RunCancelled(Exception):
    """The run was cancelled by the cancel event."""


async def run_soul(
    soul: Soul,
    user_input: str,
    visualize_fn: VisualizeFn,
    cancel_event: asyncio.Event,
):
    """
    Run the soul with the given user input.

    `cancel_event` is a outside handle that can be used to cancel the run. When the event is set,
    the run will be gracefully stopped and a `RunCancelled` will be raised.

    Raises:
        ChatProviderError: When the LLM provider returns an error.
        MaxStepsReached: When the maximum number of steps is reached.
        RunCancelled: When the run is cancelled by the cancel event.
    """
    event_queue = EventQueue()
    logger.debug(
        "Starting visualization loop with visualize function: {visualize}",
        visualize=visualize_fn,
    )

    vis_task = asyncio.create_task(visualize_fn(event_queue))
    run_task = asyncio.create_task(soul.run(user_input, event_queue))

    cancel_event_task = asyncio.create_task(cancel_event.wait())
    await asyncio.wait(
        [run_task, cancel_event_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    try:
        if cancel_event.is_set():
            logger.debug("Cancelling the run task")
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                raise RunCancelled from None
        else:
            assert run_task.done()  # either stop event is set or the run task is done
            cancel_event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_event_task
            run_task.result()  # this will raise if any exception was raised in the run task
    finally:
        logger.debug("Shutting down the visualization loop")
        # shutting down the event queue should break the visualization loop
        event_queue.shutdown()
        try:
            await asyncio.wait_for(vis_task, timeout=0.5)
        except TimeoutError:
            logger.warning("Visualization loop timed out")
