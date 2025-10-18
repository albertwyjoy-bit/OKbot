import asyncio
from typing import NamedTuple

from kosong.base.message import ContentPart, ToolCall, ToolCallPart
from kosong.tooling import ToolResult

from kimi_cli.soul import StatusSnapshot
from kimi_cli.utils.logging import logger


class StepBegin(NamedTuple):
    n: int


class StepInterrupted(NamedTuple):
    pass


class StatusUpdate(NamedTuple):
    status: StatusSnapshot


type ControlFlowEvent = StepBegin | StepInterrupted | StatusUpdate
type Event = ControlFlowEvent | ContentPart | ToolCall | ToolCallPart | ToolResult


class EventQueue:
    def __init__(self):
        self._queue = asyncio.Queue()

    def put_nowait(self, event: Event):
        if not isinstance(event, ContentPart | ToolCallPart):
            logger.debug("Emitting event: {event}", event=event)
        self._queue.put_nowait(event)

    async def get(self) -> Event:
        event = await self._queue.get()
        if not isinstance(event, ContentPart | ToolCallPart):
            logger.debug("Consuming event: {event}", event=event)
        return event

    def shutdown(self):
        self._queue.shutdown()
