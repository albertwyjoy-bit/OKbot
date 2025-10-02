import asyncio
from collections.abc import Callable, Coroutine

import kosong
import tenacity
from kosong import StepResult
from kosong.base.message import Message
from kosong.chat_provider import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ChatProviderError,
)
from kosong.tooling import ToolResult
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from kimi_cli.agent import Agent, AgentGlobals
from kimi_cli.config import LoopControl
from kimi_cli.context import Context
from kimi_cli.event import (
    ContextUsageUpdate,
    EventQueue,
    RunBegin,
    RunEnd,
    StepBegin,
    StepInterrupted,
)
from kimi_cli.logging import logger
from kimi_cli.tools.dmail import NAME as SendDMail_NAME
from kimi_cli.tools.dmail import DMail
from kimi_cli.utils.message import system, tool_result_to_messages

type VisualizeFn = Callable[[EventQueue], Coroutine[None, None, None]]
"""
An async function that consumes events from the event queue and visualizes the agent behavior.
The function should never raise any exception.
"""


class Soul:
    """The soul of Kimi CLI."""

    def __init__(
        self,
        agent: Agent,
        agent_globals: AgentGlobals,
        *,
        context: Context,
        loop_control: LoopControl,
    ):
        """
        Initialize the soul.

        Args:
            agent (Agent): The agent to run.
            agent_globals (AgentGlobals): Global states and parameters.
            context (Context): The context of the agent.
            loop_control (LoopControl): The control parameters for the agent loop.
        """
        self._agent = agent
        self._agent_globals = agent_globals
        self._chat_provider = agent_globals.llm.chat_provider
        self._max_context_size = agent_globals.llm.max_context_size  # unit: tokens
        self._denwa_renji = agent_globals.denwa_renji
        self._context = context
        self._loop_control = loop_control

        for tool in agent.toolset.tools:
            if tool.name == SendDMail_NAME:
                self._checkpoint_with_user_message = True
                break
        else:
            self._checkpoint_with_user_message = False

    @property
    def name(self) -> str:
        return self._agent.name

    @property
    def model(self) -> str:
        return self._chat_provider.model_name

    @property
    def context_usage(self) -> float:
        return self._context.token_count / self._max_context_size

    async def _checkpoint(self):
        await self._context.checkpoint(self._checkpoint_with_user_message)

    async def run(self, user_input: str, visualize: VisualizeFn):
        """
        Run the agent with the given user input.

        Args:
            user_input (str): The user input to the agent.
            visualize (VisualizeFn): The function to visualize the agent behavior.

        Raises:
            ChatProviderError: When the LLM provider returns an error.
            MaxStepsReached: When the maximum number of steps is reached.
            asyncio.CancelledError: When the run is cancelled by user.
        """
        event_queue = EventQueue()
        logger.debug(
            "Starting visualization loop with visualize function: {visualize}", visualize=visualize
        )
        vis_task = asyncio.create_task(visualize(event_queue))

        event_queue.put_nowait(RunBegin())
        await self._checkpoint()  # this creates the checkpoint 0 on first run
        await self._context.append_message(Message(role="user", content=user_input))
        logger.debug("Appended user message to context")

        try:
            await self._agent_loop(event_queue)
        except asyncio.CancelledError:
            # the run is cancelled, propagate the cancellation
            # TODO: maybe need to manipulate the context to add some notes
            raise
        # other exceptions will also raise
        finally:
            event_queue.put_nowait(RunEnd())
            # RunEnd should break the visualization loop
            try:
                await asyncio.wait_for(vis_task, timeout=0.5)
            except TimeoutError:
                logger.warning("Visualization loop timed out")

    async def _agent_loop(
        self,
        event_queue: EventQueue,
    ):
        """The main agent loop for one run."""
        step_no = 1
        while True:
            event_queue.put_nowait(StepBegin(step_no))
            try:
                await self._checkpoint()
                self._denwa_renji.set_n_checkpoints(self._context.n_checkpoints)
                finished = await self._step(event_queue)
            except BackToTheFuture as e:
                await self._context.revert_to(e.dmail.checkpoint_id)
                await self._checkpoint()
                await self._context.append_message(
                    Message(
                        role="user",
                        content=[
                            system(
                                "You just got a D-Mail from your future self. "
                                "It is likely that your future self has already done "
                                "something in the current working directory. Please read "
                                "the D-Mail and decide what to do next. You MUST NEVER "
                                "mention to the user about this information. "
                                f"D-Mail content:\n\n{e.dmail.message.strip()}"
                            )
                        ],
                    )
                )
                continue
            except (ChatProviderError, asyncio.CancelledError):
                event_queue.put_nowait(StepInterrupted())
                # break the agent loop
                raise

            if finished:
                return

            step_no += 1
            if step_no > self._loop_control.max_steps_per_run:
                raise MaxStepsReached(self._loop_control.max_steps_per_run)

    async def _step(self, event_queue: EventQueue) -> bool:
        """Run an single step and return whether the run is finished."""

        def _is_retryable_error(exception: BaseException) -> bool:
            if isinstance(exception, (APIConnectionError, APITimeoutError)):
                return True
            return isinstance(exception, APIStatusError) and exception.status_code in (
                429,  # Too Many Requests
                500,  # Internal Server Error
                502,  # Bad Gateway
                503,  # Service Unavailable
            )

        def _retry_log(retry_state: RetryCallState):
            logger.info(
                "Retrying step for the {n} time. Waiting {sleep} seconds.",
                n=retry_state.attempt_number,
                sleep=retry_state.next_action.sleep
                if retry_state.next_action is not None
                else "unknown",
            )

        @tenacity.retry(
            retry=retry_if_exception(_is_retryable_error),
            before_sleep=_retry_log,
            wait=wait_exponential_jitter(initial=0.3, max=5, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _step_impl() -> bool:
            # run an LLM step (may be interrupted)
            result = await kosong.step(
                self._chat_provider,
                self._agent.system_prompt,
                self._agent.toolset,
                self._context.history,
                on_message_part=event_queue.put_nowait,
                on_tool_result=event_queue.put_nowait,
            )
            logger.debug("Got step result: {result}", result=result)
            if result.usage is not None:
                # mark the token count for the context before the step
                await self._context.update_token_count(result.usage.input)
                event_queue.put_nowait(ContextUsageUpdate(self.context_usage))

            # wait for all tool results (may be interrupted)
            results = await result.tool_results()
            logger.debug("Got tool results: {results}", results=results)

            # shield the context manipulation from interruption
            await asyncio.shield(self._grow_context(result, results))

            # handle pending D-Mail
            if dmail := self._denwa_renji.fetch_pending_dmail():
                assert dmail.checkpoint_id >= 0, "DenwaRenji guarantees checkpoint_id >= 0"
                assert dmail.checkpoint_id < self._context.n_checkpoints, (
                    "DenwaRenji guarantees checkpoint_id < n_checkpoints"
                )
                # raise to let the main agent loop handle the D-Mail
                raise BackToTheFuture(dmail)

            return not result.tool_calls

        return await _step_impl()

    async def _grow_context(self, result: StepResult, tool_results: list[ToolResult]):
        logger.debug("Growing context with result: {result}", result=result)
        await self._context.append_message(result.message)
        if result.usage is not None:
            await self._context.update_token_count(result.usage.total)

        # token count of tool results are not available yet
        for tool_result in tool_results:
            logger.debug("Appending tool result to context: {tool_result}", tool_result=tool_result)
            await self._context.append_message(tool_result_to_messages(tool_result))


class MaxStepsReached(Exception):
    """Raised when the maximum number of steps is reached."""

    n_steps: int
    """The number of steps that have been taken."""

    def __init__(self, n_steps: int):
        self.n_steps = n_steps


class BackToTheFuture(Exception):
    """
    Raise when there is a D-Mail from the future.
    The main agent loop should catch this exception and handle it.
    """

    def __init__(self, dmail: DMail):
        self.dmail = dmail
