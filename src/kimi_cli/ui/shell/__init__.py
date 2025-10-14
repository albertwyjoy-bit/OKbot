import asyncio
import signal
from collections.abc import Awaitable

from kosong.base.message import ContentPart, TextPart, ToolCall, ToolCallPart
from kosong.chat_provider import ChatProviderError
from kosong.tooling import ToolResult
from rich.panel import Panel

from kimi_cli.logging import logger
from kimi_cli.soul import MaxStepsReached, Soul
from kimi_cli.soul.event import (
    ContextUsageUpdate,
    EventQueue,
    StepBegin,
    StepInterrupted,
)
from kimi_cli.ui import RunCancelled, run_soul
from kimi_cli.ui.shell.console import console
from kimi_cli.ui.shell.liveview import StepLiveView
from kimi_cli.ui.shell.metacmd import get_meta_command
from kimi_cli.ui.shell.prompt import CustomPromptSession


class ShellApp:
    def __init__(self, soul: Soul, welcome_info: dict[str, str] | None = None):
        self.soul = soul
        self.welcome_info = welcome_info or {}

    async def run(self, command: str | None = None) -> bool:
        if command is not None:
            # run single command and exit
            logger.info("Running agent with command: {command}", command=command)
            return await self._run(command)

        prompt_session = CustomPromptSession()

        welcome = f"[bold]Welcome to {self.soul.name}![/bold]"
        if self.welcome_info:
            welcome += "\n\n" + "\n".join(
                f"[grey30]{key}: {value}[/grey30]" for key, value in self.welcome_info.items()
            )
        welcome += "\n\n" + "[grey30]Send /help for help information.[/grey30]"

        console.print()
        console.print(
            Panel(
                welcome,
                border_style="blue",
                expand=False,
                padding=(1, 2),
            )
        )
        console.print()

        while True:
            try:
                user_input = await prompt_session.prompt()
            except KeyboardInterrupt:
                # TODO: check if this still works
                logger.debug("Exiting by KeyboardInterrupt")
                console.print("[grey30]Tip: press Ctrl-D or send 'exit' to quit[/grey30]")
                continue
            except EOFError:
                logger.debug("Exiting by EOF")
                console.print("Bye!")
                break

            if not user_input:
                logger.debug("Got empty input, skipping")
                continue

            logger.debug("Got user input: {user_input}", user_input=user_input)

            if user_input in ["exit", "quit", "/exit", "/quit"]:
                logger.debug("Exiting by meta command")
                console.print("Bye!")
                break
            if user_input.startswith("/"):
                logger.debug("Running meta command: {user_input}", user_input=user_input)
                await self._run_meta_command(user_input[1:])
                continue

            logger.info("Running agent with user input: {user_input}", user_input=user_input)
            await self._run(user_input)

        return True

    async def _run(self, user_input: str) -> bool:
        """
        Run the soul and handle any known exceptions.

        Returns:
            bool: Whether the run is successful.
        """
        cancel_event = asyncio.Event()

        def _handler():
            logger.debug("SIGINT received.")
            cancel_event.set()

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _handler)

        try:
            await run_soul(self.soul, user_input, self._visualize, cancel_event)
            return True
        except ChatProviderError as e:
            logger.exception("LLM provider error:")
            console.print(f"[bold red]LLM provider error: {e}[/bold red]")
        except MaxStepsReached as e:
            logger.warning("Max steps reached: {n_steps}", n_steps=e.n_steps)
            console.print(f"[bold yellow]Max steps reached: {e.n_steps}[/bold yellow]")
        except RunCancelled:
            logger.info("Cancelled by user")
            console.print("[bold red]Interrupted by user[/bold red]")
        except BaseException as e:
            logger.exception("Unknown error:")
            console.print(f"[bold red]Unknown error: {e}[/bold red]")
            raise  # re-raise unknown error
        finally:
            loop.remove_signal_handler(signal.SIGINT)
        return False

    async def _visualize(self, event_queue: EventQueue):
        """
        A loop to consume agent events and visualize the agent behavior.
        This loop never raise any exception except asyncio.CancelledError.
        """
        try:
            # expect a StepBegin
            assert isinstance(await event_queue.get(), StepBegin)

            while True:
                # spin the moon at the beginning of each step
                with console.status("", spinner="moon"):
                    event = await event_queue.get()

                with StepLiveView(self.soul.context_usage) as step:
                    # visualization loop for one step
                    while True:
                        match event:
                            case TextPart(text=text):
                                step.append_text(text)
                            case ContentPart():
                                # TODO: support more content parts
                                step.append_text(f"[{event.__class__.__name__}]")
                            case ToolCall():
                                step.append_tool_call(event)
                            case ToolCallPart():
                                step.append_tool_call_part(event)
                            case ToolResult():
                                step.append_tool_result(event)
                            case ContextUsageUpdate(usage_percentage=usage):
                                step.update_context_usage(usage)
                            case _:
                                break  # break the step loop
                        event = await event_queue.get()

                    # cleanup the step live view
                    if isinstance(event, StepInterrupted):
                        step.interrupt()
                    else:
                        step.finish()

                if isinstance(event, StepInterrupted):
                    # for StepInterrupted, the visualization loop should end immediately
                    break

                assert isinstance(event, StepBegin), "expect a StepBegin"
                # start a new step
        except asyncio.QueueShutDown:
            logger.debug("Visualization loop shutting down")

    async def _run_meta_command(self, command_str: str):
        parts = command_str.split(" ")
        command_name = parts[0]
        command_args = parts[1:]
        command = get_meta_command(command_name)
        if command is None:
            console.print(f"Meta command /{command_name} not found")
            return
        logger.debug(
            "Running meta command: {command_name} with args: {command_args}",
            command_name=command_name,
            command_args=command_args,
        )
        ret = command.func(self, command_args)
        if isinstance(ret, Awaitable):
            await ret
