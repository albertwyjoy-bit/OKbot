from typing import Protocol, runtime_checkable

from kimi_cli.soul.event import (
    EventQueue,
)


class MaxStepsReached(Exception):
    """Raised when the maximum number of steps is reached."""

    n_steps: int
    """The number of steps that have been taken."""

    def __init__(self, n_steps: int):
        self.n_steps = n_steps


@runtime_checkable
class Soul(Protocol):
    @property
    def name(self) -> str:
        """The name of the soul."""
        ...

    @property
    def model(self) -> str:
        """The LLM model used by the soul."""
        ...

    @property
    def context_usage(self) -> float:
        """The usage of the context, in percentage."""
        ...

    async def run(self, user_input: str, event_queue: EventQueue):
        """
        Run the agent with the given user input.

        Args:
            user_input (str): The user input to the agent.
            event_queue (EventQueue): The event queue to send events to the visualization loop.

        Raises:
            ChatProviderError: When the LLM provider returns an error.
            MaxStepsReached: When the maximum number of steps is reached.
            asyncio.CancelledError: When the run is cancelled by user.
        """
        ...
