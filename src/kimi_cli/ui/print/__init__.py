import asyncio
import json
import sys
from typing import Literal, override

from kosong.base.message import Message
from kosong.chat_provider import ChatProviderError

from kimi_cli.event import EventQueue, RunEnd, StepInterrupted
from kimi_cli.logging import logger
from kimi_cli.soul import MaxStepsReached, Soul
from kimi_cli.ui import BaseApp
from kimi_cli.utils.message import message_extract_text

InputFormat = Literal["text", "stream-json"]


class PrintApp(BaseApp):
    def __init__(self, soul: Soul, input_format: InputFormat):
        self.soul = soul
        self.input_format = input_format

    @override
    def run(self, command: str | None = None) -> bool:
        if command is None and not sys.stdin.isatty() and self.input_format == "text":
            command = sys.stdin.read().strip()
            logger.info("Read command from stdin: {command}", command=command)

        # TODO: maybe unify with `_soul_run` in `ShellApp`
        try:
            while True:
                if command is None:
                    if self.input_format == "text":
                        return True
                    else:
                        assert self.input_format == "stream-json"
                        command = self._read_next_command()
                        if command is None:
                            return True

                if command:
                    logger.info("Running agent with command: {command}", command=command)
                    print(command)
                    asyncio.run(self.soul.run(command, self._visualize))
                else:
                    logger.info("Empty command, skipping")

                command = None
        except ChatProviderError as e:
            logger.exception("LLM provider error:")
            print(f"LLM provider error: {e}")
        except MaxStepsReached as e:
            logger.warning("Max steps reached: {n_steps}", n_steps=e.n_steps)
            print(f"Max steps reached: {e.n_steps}")
        except KeyboardInterrupt:
            logger.error("Interrupted by user")
            print("Interrupted by user")
        except BaseException as e:
            logger.exception("Unknown error:")
            print(f"Unknown error: {e}")
        return False

    def _read_next_command(self) -> str | None:
        while True:
            json_line = sys.stdin.readline()
            if not json_line:
                # EOF
                return None

            json_line = json_line.strip()
            if not json_line:
                # for empty line, read next line
                continue

            try:
                data = json.loads(json_line)
                message = Message.model_validate(data)
                if message.role == "user":
                    return message_extract_text(message)
                logger.warning(
                    "Ignoring message with role `{role}`: {json_line}",
                    role=message.role,
                    json_line=json_line,
                )
            except Exception:
                logger.warning("Ignoring invalid user message: {json_line}", json_line=json_line)

    async def _visualize(self, event_queue: EventQueue):
        while True:
            event = await event_queue.get()
            print(event)
            if isinstance(event, StepInterrupted | RunEnd):
                break
