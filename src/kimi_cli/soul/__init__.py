from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kimi_cli.utils.aioqueue import QueueShutDown
from kimi_cli.utils.logging import logger
from kimi_cli.wire import Wire
from kimi_cli.wire.file import WireFile
from kimi_cli.wire.types import ContentPart, WireMessage

if TYPE_CHECKING:
    from kimi_cli.llm import LLM, ModelCapability
    from kimi_cli.utils.slashcmd import SlashCommand


class LLMNotSet(Exception):
    """Raised when the LLM is not set."""

    def __init__(self) -> None:
        super().__init__("LLM not set")


class LLMNotSupported(Exception):
    """Raised when the LLM does not have required capabilities."""

    def __init__(self, llm: LLM, capabilities: list[ModelCapability]):
        self.llm = llm
        self.capabilities = capabilities
        capabilities_str = "capability" if len(capabilities) == 1 else "capabilities"
        super().__init__(
            f"LLM model '{llm.model_name}' does not support required {capabilities_str}: "
            f"{', '.join(capabilities)}."
        )


class MaxStepsReached(Exception):
    """Raised when the maximum number of steps is reached."""

    n_steps: int
    """The number of steps that have been taken."""

    def __init__(self, n_steps: int):
        super().__init__(f"Max number of steps reached: {n_steps}")
        self.n_steps = n_steps


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    context_usage: float
    """The usage of the context, in percentage."""
    yolo_enabled: bool = False
    """Whether YOLO (auto-approve) mode is enabled."""


@runtime_checkable
class Soul(Protocol):
    @property
    def name(self) -> str:
        """The name of the soul."""
        ...

    @property
    def model_name(self) -> str:
        """The name of the LLM model used by the soul. Empty string if LLM is not set."""
        ...

    @property
    def model_capabilities(self) -> set[ModelCapability] | None:
        """The capabilities of the LLM model used by the soul. None if LLM is not set."""
        ...

    @property
    def thinking(self) -> bool | None:
        """
        Whether thinking mode is currently enabled.
        None if LLM is not set or thinking mode is not set explicitly.
        """
        ...

    @property
    def status(self) -> StatusSnapshot:
        """The current status of the soul. The returned value is immutable."""
        ...

    @property
    def available_slash_commands(self) -> list[SlashCommand[Any]]:
        """List of available slash commands supported by the soul."""
        ...

    async def run(self, user_input: str | list[ContentPart]):
        """
        Run the agent with the given user input until the max steps or no more tool calls.

        Args:
            user_input (str | list[ContentPart]): The user input to the agent.
                Can be a slash command call or natural language input.

        Raises:
            LLMNotSet: When the LLM is not set.
            LLMNotSupported: When the LLM does not have required capabilities.
            ChatProviderError: When the LLM provider returns an error.
            MaxStepsReached: When the maximum number of steps is reached.
            asyncio.CancelledError: When the run is cancelled by user.
        """
        ...


type UILoopFn = Callable[[Wire], Coroutine[Any, Any, None]]
"""A long-running async function to visualize the agent behavior."""


class RunCancelled(Exception):
    """The run was cancelled by the cancel event."""


async def run_soul(
    soul: Soul,
    user_input: str | list[ContentPart],
    ui_loop_fn: UILoopFn,
    cancel_event: asyncio.Event,
    wire_file: WireFile | None = None,
    is_main_agent: bool = True,
) -> None:
    """
    Run the soul with the given user input, connecting it to the UI loop with a `Wire`.

    This function will automatically continue processing queued follow-up messages
    (from Follow-up Queue) after the initial user_input is processed, until no more 
    messages are pending.
    
    Note: 
    - The auto-continuation only applies to the main agent. Background subagents
      (is_main_agent=False) will not auto-process queued messages to avoid recursion.
    - Steering Queue messages (background task results) are processed immediately
      at tool call boundaries in _step(), not here.

    `cancel_event` is a outside handle that can be used to cancel the run. When the
    event is set, the run will be gracefully stopped and a `RunCancelled` will be raised.

    Args:
        soul: The soul to run
        user_input: The user input to process
        ui_loop_fn: The UI loop function to handle wire messages
        cancel_event: Event to signal cancellation
        wire_file: Optional wire file backend
        is_main_agent: Whether this is the main agent (enables auto-continuation for background tasks)

    Raises:
        LLMNotSet: When the LLM is not set.
        LLMNotSupported: When the LLM does not have required capabilities.
        ChatProviderError: When the LLM provider returns an error.
        MaxStepsReached: When the maximum number of steps is reached.
        RunCancelled: When the run is cancelled by the cancel event.
    """
    from kimi_cli.soul.kimisoul import KimiSoul
    
    wire = Wire(file_backend=wire_file)
    wire_token = _current_wire.set(wire)

    logger.debug("Starting UI loop with function: {ui_loop_fn}", ui_loop_fn=ui_loop_fn)
    ui_task = asyncio.create_task(ui_loop_fn(wire))

    logger.debug("Starting soul run (is_main_agent={is_main})", is_main=is_main_agent)
    
    # Main run loop: process user input and then any queued background task results
    soul_task: asyncio.Task[Any] | None = None
    cancel_event_task = asyncio.create_task(cancel_event.wait())
    
    try:
        # First, process the initial user input
        soul_task = asyncio.create_task(soul.run(user_input))
        
        pending_tasks = {soul_task, cancel_event_task}
        while pending_tasks:
            done, pending_tasks = await asyncio.wait(
                pending_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            if cancel_event_task in done:
                # Cancel requested
                if soul_task and not soul_task.done():
                    logger.debug("Cancelling the soul task")
                    soul_task.cancel()
                    try:
                        await soul_task
                    except asyncio.CancelledError:
                        pass
                raise RunCancelled()
            
            if soul_task in done:
                # Soul task completed - check result
                try:
                    soul_task.result()
                except asyncio.CancelledError:
                    raise RunCancelled() from None
                
                # Check if there are queued follow-up messages to process
                # Only auto-continue for main agent, not for background subagents
                # Note: Steering Queue messages are already processed at tool call boundaries
                if is_main_agent and isinstance(soul, KimiSoul) and soul.has_pending_messages():
                    logger.info("Auto-continuing to process follow-up messages")
                    # Create a new task to process queued messages
                    # Pass None to skip initial turn and directly process follow-up queue
                    soul_task = asyncio.create_task(soul.run(None))
                    pending_tasks = {soul_task, cancel_event_task}
                else:
                    # No more messages to process
                    break
        
        # Clean up cancel_event_task
        if not cancel_event_task.done():
            cancel_event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_event_task
                
    finally:
        logger.debug("Shutting down the UI loop")
        wire.shutdown()
        await wire.join()
        try:
            await asyncio.wait_for(ui_task, timeout=5.0)
        except QueueShutDown:
            logger.debug("UI loop shut down")
            pass
        except TimeoutError:
            logger.warning("UI loop timed out")
        finally:
            _current_wire.reset(wire_token)


_current_wire = ContextVar[Wire | None]("current_wire", default=None)


def get_wire_or_none() -> Wire | None:
    """
    Get the current wire or None.
    Expect to be not None when called from anywhere in the agent loop.
    """
    return _current_wire.get()


def wire_send(msg: WireMessage) -> None:
    """
    Send a wire message to the current wire.
    Take this as `print` and `input` for souls.
    Souls should always use this function to send wire messages.
    """
    wire = get_wire_or_none()
    assert wire is not None, "Wire is expected to be set when soul is running"
    wire.soul_side.send(msg)
