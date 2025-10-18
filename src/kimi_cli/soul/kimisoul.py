import asyncio

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
from kimi_cli.soul import MaxStepsReached, Soul, StatusSnapshot
from kimi_cli.soul.context import Context
from kimi_cli.soul.event import EventQueue, StatusUpdate, StepBegin, StepInterrupted
from kimi_cli.soul.message import system, tool_result_to_messages
from kimi_cli.tools.dmail import NAME as SendDMail_NAME
from kimi_cli.utils.logging import logger


class KimiSoul:
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
    def status(self) -> StatusSnapshot:
        return StatusSnapshot(
            context_usage=self._context.token_count / self._max_context_size,
        )

    async def _checkpoint(self):
        await self._context.checkpoint(self._checkpoint_with_user_message)

    async def run(self, user_input: str, event_queue: EventQueue):
        await self._checkpoint()  # this creates the checkpoint 0 on first run
        await self._context.append_message(Message(role="user", content=user_input))
        logger.debug("Appended user message to context")
        await self._agent_loop(event_queue)

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
                await self._context.revert_to(e.checkpoint_id)
                await self._checkpoint()
                await self._context.append_message(e.message)
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
                event_queue.put_nowait(StatusUpdate(status=self.status))

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
                # raise to let the main loop take us back to the future
                raise BackToTheFuture(
                    dmail.checkpoint_id,
                    Message(
                        role="user",
                        content=[
                            system(
                                "You just got a D-Mail from your future self. "
                                "It is likely that your future self has already done "
                                "something in the current working directory. Please read "
                                "the D-Mail and decide what to do next. You MUST NEVER "
                                "mention to the user about this information. "
                                f"D-Mail content:\n\n{dmail.message.strip()}"
                            )
                        ],
                    ),
                )

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


class BackToTheFuture(Exception):
    """
    Raise when we need to revert the context to a previous checkpoint.
    The main agent loop should catch this exception and handle it.
    """

    def __init__(self, checkpoint_id: int, message: Message):
        self.checkpoint_id = checkpoint_id
        self.message = message


def __static_type_check(
    kimi_soul: KimiSoul,
):
    _: Soul = kimi_soul
