from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

import kosong
import tenacity
from kosong import StepResult
from kosong.chat_provider import (
    APIConnectionError,
    APIEmptyResponseError,
    APIStatusError,
    APITimeoutError,
)
from kosong.message import Message
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from kimi_cli.llm import ModelCapability
from kimi_cli.skill import Skill, read_skill_text
from kimi_cli.skill.flow import Flow, FlowEdge, FlowNode, parse_choice
from kimi_cli.soul import (
    LLMNotSet,
    LLMNotSupported,
    MaxStepsReached,
    Soul,
    StatusSnapshot,
    wire_send,
)
from kimi_cli.memory import MemoryAgent, ObservationInput, ObservationType, SummaryInput, UserPromptInput
from kimi_cli.soul.agent import Agent, Runtime
from kimi_cli.soul.compaction import SimpleCompaction
from kimi_cli.soul.context import Context
from kimi_cli.soul.message import check_message, system, tool_result_to_message
from kimi_cli.soul.slash import registry as soul_slash_registry
from kimi_cli.soul.toolset import KimiToolset
from kimi_cli.tools.dmail import NAME as SendDMail_NAME
from kimi_cli.tools.utils import ToolRejectedError
from kimi_cli.utils.logging import logger
from kimi_cli.utils.slashcmd import SlashCommand, parse_slash_command_call
from kimi_cli.wire.file import WireFile
from kimi_cli.wire.types import (
    ApprovalRequest,
    ApprovalResponse,
    CompactionBegin,
    CompactionEnd,
    ContentPart,
    StatusUpdate,
    StepBegin,
    StepInterrupted,
    TextPart,
    ToolResult,
    TurnBegin,
    TurnEnd,
)

if TYPE_CHECKING:

    def type_check(soul: KimiSoul):
        _: Soul = soul


SKILL_COMMAND_PREFIX = "skill:"
FLOW_COMMAND_PREFIX = "flow:"
DEFAULT_MAX_FLOW_MOVES = 1000


type StepStopReason = Literal["no_tool_calls", "tool_rejected"]


@dataclass(frozen=True, slots=True)
class StepOutcome:
    stop_reason: StepStopReason
    assistant_message: Message


type TurnStopReason = StepStopReason


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    stop_reason: TurnStopReason
    final_message: Message | None
    step_count: int


class KimiSoul:
    """The soul of Kimi Code CLI."""

    def __init__(
        self,
        agent: Agent,
        *,
        context: Context,
    ):
        """
        Initialize the soul.

        Args:
            agent (Agent): The agent to run.
            context (Context): The context of the agent.
        """
        self._agent = agent
        self._runtime = agent.runtime
        self._denwa_renji = agent.runtime.denwa_renji
        self._approval = agent.runtime.approval
        self._context = context
        self._loop_control = agent.runtime.config.loop_control
        self._compaction = SimpleCompaction()  # TODO: maybe configurable and composable

        for tool in agent.toolset.tools:
            if tool.name == SendDMail_NAME:
                self._checkpoint_with_user_message = True
                break
        else:
            self._checkpoint_with_user_message = False

        self._slash_commands = self._build_slash_commands()
        self._slash_command_map = self._index_slash_commands(self._slash_commands)
        
        # Memory tracking
        self._prompt_number = 0  # Incremented on each run()
        self._current_turn_tool_results: list[tuple] = []  # Track (ToolResult, ToolCall) tuples for observation extraction

    @property
    def name(self) -> str:
        return self._agent.name

    @property
    def model_name(self) -> str:
        return self._runtime.llm.chat_provider.model_name if self._runtime.llm else ""

    @property
    def model_capabilities(self) -> set[ModelCapability] | None:
        if self._runtime.llm is None:
            return None
        return self._runtime.llm.capabilities

    @property
    def thinking(self) -> bool | None:
        """Whether thinking mode is enabled."""
        if self._runtime.llm is None:
            return None
        if thinking_effort := self._runtime.llm.chat_provider.thinking_effort:
            return thinking_effort != "off"
        return None

    @property
    def status(self) -> StatusSnapshot:
        return StatusSnapshot(
            context_usage=self._context_usage,
            yolo_enabled=self._approval.is_yolo(),
        )

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    @property
    def context(self) -> Context:
        return self._context

    @property
    def _context_usage(self) -> float:
        if self._runtime.llm is not None:
            return self._context.token_count / self._runtime.llm.max_context_size
        return 0.0

    @property
    def wire_file(self) -> WireFile:
        return self._runtime.session.wire_file

    async def _checkpoint(self):
        await self._context.checkpoint(self._checkpoint_with_user_message)

    @property
    def available_slash_commands(self) -> list[SlashCommand[Any]]:
        return self._slash_commands

    async def reload_skills(self) -> tuple[int, str]:
        """Reload skills from disk and update slash commands and system prompt.
        
        Returns:
            Tuple of (number of skills loaded, formatted skills string)
        """
        # Reload skills in runtime
        count, skills_formatted = await self._runtime.reload_skills()
        
        # Rebuild slash commands with new skills
        self._slash_commands = self._build_slash_commands()
        self._slash_command_map = self._index_slash_commands(self._slash_commands)
        
        # Refresh system prompt with new skills info
        try:
            self._agent.refresh_system_prompt()
            logger.info("System prompt refreshed with new skills")
        except Exception:
            logger.warning("Failed to refresh system prompt: {e}")
        
        logger.info("KimiSoul reloaded {count} skills, {slash_count} slash commands", 
                   count=count, slash_count=len(self._slash_commands))
        
        return count, skills_formatted

    async def reload_mcp(self) -> tuple[int, int, list[str]]:
        """Reload MCP tools from global config file.
        
        This will disconnect existing MCP servers and reconnect with new configuration.
        
        Returns:
            Tuple of (servers_connected, total_tools, list of connected server names)
        """
        from fastmcp.mcp_config import MCPConfig

        from kimi_cli.share import get_share_dir
        
        logger.info("Running reload_mcp")
        
        # Load MCP config from global mcp.json
        mcp_file = get_share_dir() / "mcp.json"
        if not mcp_file.exists():
            raise FileNotFoundError(f"MCP config file not found: {mcp_file}")
        
        import json
        config_data = json.loads(mcp_file.read_text(encoding="utf-8"))
        
        if not config_data.get("mcpServers"):
            raise ValueError("No MCP servers configured in mcp.json")
        
        mcp_config = MCPConfig.model_validate(config_data)
        
        # Reload MCP tools through toolset
        if not hasattr(self._agent.toolset, 'reload_mcp_tools'):
            raise RuntimeError("Current toolset does not support MCP reload")
        
        servers_count, tools_count, server_names = await self._agent.toolset.reload_mcp_tools(
            [mcp_config], self._runtime
        )
        
        logger.info(
            "MCP reloaded: {servers} servers, {tools} tools",
            servers=servers_count, tools=tools_count
        )
        
        return servers_count, tools_count, server_names

    async def run(self, user_input: str | list[ContentPart]):
        # Refresh OAuth tokens on each turn to avoid idle-time expirations.
        await self._runtime.oauth.ensure_fresh(self._runtime)

        wire_send(TurnBegin(user_input=user_input))
        user_message = Message(role="user", content=user_input)
        text_input = user_message.extract_text(" ").strip()
        
        # Increment prompt number
        self._prompt_number += 1
        current_prompt = self._prompt_number
        
        # Clear tool results tracking for this turn
        self._current_turn_tool_results: list[tuple] = []

        # Check if this is a slash command that affects session flow
        is_session_command = False
        summary_generated = False

        if command_call := parse_slash_command_call(text_input):
            command = self._find_slash_command(command_call.name)
            if command is None:
                # this should not happen actually, the shell should have filtered it out
                wire_send(TextPart(text=f'Unknown slash command "/{command_call.name}".'))
            else:
                # Check if this is a session-affecting command
                is_session_command = command_call.name in ("compact", "new")
                ret = command.func(self, command_call.args)
                if isinstance(ret, Awaitable):
                    await ret
        elif self._loop_control.max_ralph_iterations != 0:
            runner = FlowRunner.ralph_loop(
                user_message,
                self._loop_control.max_ralph_iterations,
            )
            await runner.run(self, "")
        else:
            await self._turn(user_message)

        # Generate summary at turn end for all cases (regular turn or slash command)
        # This ensures no data loss and handles /compact, /new, exit uniformly
        if self._runtime.memory_agent:
            try:
                await self._generate_and_save_summary(
                    user_input=text_input,
                    is_compact=is_session_command and "compact" in text_input.lower(),
                    prompt_number=current_prompt,
                )
                summary_generated = True
            except Exception as e:
                logger.warning(f"Failed to generate summary: {e}")

        wire_send(TurnEnd())
        
        # Log memory stats if enabled
        if self._runtime.memory_agent and summary_generated:
            try:
                stats = self._runtime.memory_agent.get_stats()
                logger.debug("Memory stats: {stats}", stats=stats)
            except Exception:
                pass

    async def _turn(self, user_message: Message) -> TurnOutcome:
        if self._runtime.llm is None:
            raise LLMNotSet()

        if missing_caps := check_message(user_message, self._runtime.llm.capabilities):
            raise LLMNotSupported(self._runtime.llm, list(missing_caps))

        await self._checkpoint()  # this creates the checkpoint 0 on first run
        await self._context.append_message(user_message)
        logger.debug("Appended user message to context")
        return await self._agent_loop()

    def _build_slash_commands(self) -> list[SlashCommand[Any]]:
        commands: list[SlashCommand[Any]] = list(soul_slash_registry.list_commands())
        seen_names = {cmd.name for cmd in commands}

        for skill in self._runtime.skills.values():
            if skill.type not in ("standard", "flow"):
                continue
            name = f"{SKILL_COMMAND_PREFIX}{skill.name}"
            if name in seen_names:
                logger.warning(
                    "Skipping skill slash command /{name}: name already registered",
                    name=name,
                )
                continue
            commands.append(
                SlashCommand(
                    name=name,
                    func=self._make_skill_runner(skill),
                    description=skill.description or "",
                    aliases=[],
                )
            )
            seen_names.add(name)

        for skill in self._runtime.skills.values():
            if skill.type != "flow":
                continue
            if skill.flow is None:
                logger.warning("Flow skill {name} has no flow; skipping", name=skill.name)
                continue
            command_name = f"{FLOW_COMMAND_PREFIX}{skill.name}"
            if command_name in seen_names:
                logger.warning(
                    "Skipping prompt flow slash command /{name}: name already registered",
                    name=command_name,
                )
                continue
            runner = FlowRunner(skill.flow, name=skill.name)
            commands.append(
                SlashCommand(
                    name=command_name,
                    func=runner.run,
                    description=skill.description or "",
                    aliases=[],
                )
            )
            seen_names.add(command_name)

        return commands

    @staticmethod
    def _index_slash_commands(
        commands: list[SlashCommand[Any]],
    ) -> dict[str, SlashCommand[Any]]:
        indexed: dict[str, SlashCommand[Any]] = {}
        for command in commands:
            indexed[command.name] = command
            for alias in command.aliases:
                indexed[alias] = command
        return indexed

    def _find_slash_command(self, name: str) -> SlashCommand[Any] | None:
        return self._slash_command_map.get(name)

    def _make_skill_runner(self, skill: Skill) -> Callable[[KimiSoul, str], None | Awaitable[None]]:
        async def _run_skill(soul: KimiSoul, args: str, *, _skill: Skill = skill) -> None:
            skill_text = await read_skill_text(_skill)
            if skill_text is None:
                wire_send(
                    TextPart(text=f'Failed to load skill "/{SKILL_COMMAND_PREFIX}{_skill.name}".')
                )
                return
            extra = args.strip()
            if extra:
                skill_text = f"{skill_text}\n\nUser request:\n{extra}"
            await soul._turn(Message(role="user", content=skill_text))

        _run_skill.__doc__ = skill.description
        return _run_skill

    async def _agent_loop(self) -> TurnOutcome:
        """The main agent loop for one run."""
        assert self._runtime.llm is not None
        if isinstance(self._agent.toolset, KimiToolset):
            await self._agent.toolset.wait_for_mcp_tools()

        async def _pipe_approval_to_wire():
            while True:
                request = await self._approval.fetch_request()
                # Here we decouple the wire approval request and the soul approval request.
                wire_request = ApprovalRequest(
                    id=request.id,
                    action=request.action,
                    description=request.description,
                    sender=request.sender,
                    tool_call_id=request.tool_call_id,
                    display=request.display,
                    mandatory=request.mandatory,
                )
                wire_send(wire_request)
                # We wait for the request to be resolved over the wire, which means that,
                # for each soul, we will have only one approval request waiting on the wire
                # at a time. However, be aware that subagents (which have their own souls) may
                # also send approval requests to the root wire.
                resp = await wire_request.wait()
                self._approval.resolve_request(request.id, resp)
                wire_send(ApprovalResponse(request_id=request.id, response=resp))

        step_no = 0
        while True:
            step_no += 1
            if step_no > self._loop_control.max_steps_per_turn:
                raise MaxStepsReached(self._loop_control.max_steps_per_turn)

            wire_send(StepBegin(n=step_no))
            approval_task = asyncio.create_task(_pipe_approval_to_wire())
            back_to_the_future: BackToTheFuture | None = None
            step_outcome: StepOutcome | None = None
            try:
                # compact the context if needed
                reserved = self._loop_control.reserved_context_size
                if self._context.token_count + reserved >= self._runtime.llm.max_context_size:
                    logger.info("Context too long, compacting...")
                    await self.compact_context()

                logger.debug("Beginning step {step_no}", step_no=step_no)
                await self._checkpoint()
                self._denwa_renji.set_n_checkpoints(self._context.n_checkpoints)
                step_outcome = await self._step()
            except BackToTheFuture as e:
                back_to_the_future = e
            except Exception:
                # any other exception should interrupt the step
                wire_send(StepInterrupted())
                # break the agent loop
                raise
            finally:
                approval_task.cancel()  # stop piping approval requests to the wire
                with suppress(asyncio.CancelledError):
                    try:
                        await approval_task
                    except Exception:
                        logger.exception("Approval piping task failed")

            if step_outcome is not None:
                final_message = (
                    step_outcome.assistant_message
                    if step_outcome.stop_reason == "no_tool_calls"
                    else None
                )
                return TurnOutcome(
                    stop_reason=step_outcome.stop_reason,
                    final_message=final_message,
                    step_count=step_no,
                )

            if back_to_the_future is not None:
                await self._context.revert_to(back_to_the_future.checkpoint_id)
                await self._checkpoint()
                await self._context.append_message(back_to_the_future.messages)

    async def _step(self) -> StepOutcome | None:
        """Run a single step and return a stop outcome, or None to continue."""
        # already checked in `run`
        assert self._runtime.llm is not None
        chat_provider = self._runtime.llm.chat_provider

        @tenacity.retry(
            retry=retry_if_exception(self._is_retryable_error),
            before_sleep=partial(self._retry_log, "step"),
            wait=wait_exponential_jitter(initial=0.3, max=5, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _kosong_step_with_retry() -> StepResult:
            # run an LLM step (may be interrupted)
            return await kosong.step(
                chat_provider,
                self._agent.system_prompt,
                self._agent.toolset,
                self._context.history,
                on_message_part=wire_send,
                on_tool_result=wire_send,
            )

        result = await _kosong_step_with_retry()
        logger.debug("Got step result: {result}", result=result)
        status_update = StatusUpdate(token_usage=result.usage, message_id=result.id)
        if result.usage is not None:
            # mark the token count for the context before the step
            await self._context.update_token_count(result.usage.input)
            status_update.context_usage = self.status.context_usage
        wire_send(status_update)

        # wait for all tool results (may be interrupted)
        results = await result.tool_results()
        logger.debug("Got tool results: {results}", results=results)

        # shield the context manipulation from interruption
        await asyncio.shield(self._grow_context(result, results))

        rejected = any(isinstance(result.return_value, ToolRejectedError) for result in results)
        if rejected:
            _ = self._denwa_renji.fetch_pending_dmail()
            return StepOutcome(stop_reason="tool_rejected", assistant_message=result.message)

        # handle pending D-Mail
        if dmail := self._denwa_renji.fetch_pending_dmail():
            assert dmail.checkpoint_id >= 0, "DenwaRenji guarantees checkpoint_id >= 0"
            assert dmail.checkpoint_id < self._context.n_checkpoints, (
                "DenwaRenji guarantees checkpoint_id < n_checkpoints"
            )
            # raise to let the main loop take us back to the future
            raise BackToTheFuture(
                dmail.checkpoint_id,
                [
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
                    )
                ],
            )

        if result.tool_calls:
            return None
        return StepOutcome(stop_reason="no_tool_calls", assistant_message=result.message)

    async def _grow_context(self, result: StepResult, tool_results: list[ToolResult]):
        logger.info("Growing context with {n} tool results", n=len(tool_results))
        
        # Build a map of tool_call_id -> ToolCall for lookup
        tool_call_map = {tc.id: tc for tc in (result.tool_calls or [])}
        
        # Store tool results with their corresponding ToolCall for observation extraction
        for tr in tool_results:
            tc = tool_call_map.get(tr.tool_call_id)
            if tc:
                self._current_turn_tool_results.append((tr, tc))
        
        # Log memory agent status
        if self._runtime.memory_agent:
            logger.info("Memory agent is available")
        else:
            logger.warning("Memory agent is NOT available")

        assert self._runtime.llm is not None
        tool_messages = [tool_result_to_message(tr) for tr in tool_results]
        for tm in tool_messages:
            if missing_caps := check_message(tm, self._runtime.llm.capabilities):
                logger.warning(
                    "Tool result message requires unsupported capabilities: {caps}",
                    caps=missing_caps,
                )
                raise LLMNotSupported(self._runtime.llm, list(missing_caps))

        await self._context.append_message(result.message)
        if result.usage is not None:
            await self._context.update_token_count(result.usage.total)

        logger.debug(
            "Appending tool messages to context: {tool_messages}", tool_messages=tool_messages
        )
        await self._context.append_message(tool_messages)
        # token count of tool results are not available yet
        
        # Extract and queue observations from tool results
        if self._runtime.memory_agent:
            # Build a map of tool_call_id -> ToolCall for lookup
            tool_call_map = {tc.id: tc for tc in (result.tool_calls or [])}
            
            logger.info(
                "Extracting observations from {n} tool results", 
                n=len(tool_results)
            )
            
            for tool_result in tool_results:
                try:
                    # Find the corresponding ToolCall to get tool name and args
                    tool_call = tool_call_map.get(tool_result.tool_call_id)
                    if tool_call:
                        tool_name = tool_call.function.name
                        obs_input = await self._extract_observation_from_tool_result(
                            tool_result, tool_call
                        )
                        if obs_input:
                            await self._runtime.memory_agent.queue_observation(
                                obs_input, wait=False
                            )
                            logger.info(
                                "Queued observation: {title} (type: {type})",
                                title=obs_input.title,
                                type=obs_input.type.value
                            )
                        else:
                            logger.debug(
                                "No observation extracted for tool: {tool_name}",
                                tool_name=tool_name
                            )
                    else:
                        logger.warning(
                            "No ToolCall found for tool_result id: {id}",
                            id=tool_result.tool_call_id
                        )
                except Exception as e:
                    logger.exception("Failed to extract observation: {e}")

    async def compact_context(self) -> None:
        """
        Compact the context.

        Raises:
            LLMNotSet: When the LLM is not set.
            ChatProviderError: When the chat provider returns an error.
        """

        @tenacity.retry(
            retry=retry_if_exception(self._is_retryable_error),
            before_sleep=partial(self._retry_log, "compaction"),
            wait=wait_exponential_jitter(initial=0.3, max=5, jitter=0.5),
            stop=stop_after_attempt(self._loop_control.max_retries_per_step),
            reraise=True,
        )
        async def _compact_with_retry() -> Sequence[Message]:
            if self._runtime.llm is None:
                raise LLMNotSet()
            return await self._compaction.compact(self._context.history, self._runtime.llm)

        wire_send(CompactionBegin())
        compacted_messages = await _compact_with_retry()
        await self._context.clear()
        await self._checkpoint()
        await self._context.append_message(compacted_messages)
        wire_send(CompactionEnd())

    @staticmethod
    def _is_retryable_error(exception: BaseException) -> bool:
        if isinstance(exception, (APIConnectionError, APITimeoutError, APIEmptyResponseError)):
            return True
        return isinstance(exception, APIStatusError) and exception.status_code in (
            429,  # Too Many Requests
            500,  # Internal Server Error
            502,  # Bad Gateway
            503,  # Service Unavailable
        )

    @staticmethod
    def _retry_log(name: str, retry_state: RetryCallState):
        logger.info(
            "Retrying {name} for the {n} time. Waiting {sleep} seconds.",
            name=name,
            n=retry_state.attempt_number,
            sleep=retry_state.next_action.sleep
            if retry_state.next_action is not None
            else "unknown",
        )

    # ============== Memory Integration ==============

    # 🔴 LLM 智能分类 Observation 的 System Prompt（参考 claude-mem）
    _OBSERVATION_CLASSIFICATION_PROMPT = '''You are an expert at analyzing AI agent tool calls and classifying them into structured observations.

Your task is to analyze the tool call and extract key information:

## Type Classification Rules

**type**: MUST be EXACTLY one of these 6 options:
- **bugfix**: Something was broken, now fixed (errors resolved, tests now pass)
- **feature**: New capability or functionality added (new files, new endpoints, new UI)
- **refactor**: Code restructured without changing behavior (function extraction, renaming, moving code)
- **change**: Generic modification (docs, config updates, misc changes)
- **discovery**: Learning about existing system (research, reading code, web search, exploration)
- **decision**: Architectural/design choice with rationale (plan approval, todo list updates, design decisions)

## Output Format

Respond in this exact JSON format:
```json
{
  "type": "one_of_the_6_options",
  "title": "Brief, specific title (max 10 words)",
  "subtitle": "Optional clarifying detail (max 15 words)",
  "facts": ["Key fact 1", "Key fact 2"],
  "concepts": ["concept1", "concept2"],
  "narrative": "What happened and why (1-2 sentences)"
}
```

## Guidelines

- **title**: Be specific, include filenames when relevant (e.g., "Fixed auth token validation in auth.py")
- **facts**: Extract concrete outcomes, not descriptions (e.g., "Added null check on line 42")
- **concepts**: Technical keywords for searchability (e.g., ["auth", "jwt", "validation"])
- **narrative**: Explain the context and reasoning behind the action

## Examples

Tool: StrReplaceFile on "auth.py" with "extract validate_token() to helper function"
→ type: "refactor", concepts: ["auth", "refactoring", "helpers"]

Tool: WriteFile creating "api/users.ts" with new endpoint
→ type: "feature", concepts: ["api", "typescript", "users"]

Tool: Shell running "pytest tests/" with errors then fix applied
→ type: "bugfix", concepts: ["testing", "pytest"]

Tool: SearchWeb on "python asyncio best practices"
→ type: "discovery", concepts: ["python", "asyncio", "research"]'''

    async def _classify_observation_with_llm(
        self,
        tool_name: str,
        args: Any,
        return_value: Any,
        files_read: list[str],
        files_modified: list[str]
    ) -> dict | None:
        """
        使用 LLM 智能分类 Observation（参考 claude-mem）
        
        Returns:
            dict with keys: type, title, subtitle, facts, concepts, narrative
            or None if classification failed
        """
        if self._runtime.llm is None:
            return None
        
        # Build the user prompt with tool call details
        user_content = f"""Analyze this tool call and classify it:

**Tool**: {tool_name}

**Arguments**:
```json
{json.dumps(args, indent=2, default=str)}
```

**Return Value** (truncated):
```
{str(return_value)[:1000]}
```

**Files Read**: {files_read or 'None'}
**Files Modified**: {files_modified or 'None'}

Provide your classification in the specified JSON format."""

        try:
            logger.info("Calling LLM to classify observation for {tool_name}", tool_name=tool_name)
            
            # Call LLM for classification
            response = await self._runtime.llm.chat_provider.generate(
                system_prompt=self._OBSERVATION_CLASSIFICATION_PROMPT,
                tools=[],  # No tools needed for classification
                history=[
                    Message(role="user", content=user_content)
                ]
            )
            
            # Parse the response - handle streamed messages
            logger.info("LLM raw response type: {rtype}", rtype=type(response).__name__)
            
            # Extract text from streamed message
            response_text = ""
            if hasattr(response, '__aiter__'):
                # It's an async iterator (KimiStreamedMessage)
                async for part in response:
                    if hasattr(part, 'text'):
                        response_text += part.text
                    elif hasattr(part, 'think'):
                        pass  # Skip thinking parts for classification
            elif hasattr(response, 'content') and response.content:
                response_text = response.content
            elif hasattr(response, 'text') and response.text:
                response_text = response.text
            else:
                response_text = str(response)
            
            if not response_text or response_text.startswith('<'):
                logger.warning("Could not extract text from response")
                return None
            
            logger.info("LLM classification response: {response}", response=response_text[:500])
            
            # Extract JSON from response
            json_match = None
            if '```json' in response_text:
                json_match = response_text.split('```json')[1].split('```')[0].strip()
            elif '```' in response_text:
                json_match = response_text.split('```')[1].split('```')[0].strip()
            else:
                json_match = response_text.strip()
            
            if json_match:
                try:
                    result = json.loads(json_match)
                    # Validate required fields
                    required = ['type', 'title', 'facts', 'concepts', 'narrative']
                    if all(k in result for k in required):
                        logger.info("LLM classified observation: {title} (type: {type}, concepts: {concepts})",
                                   title=result.get('title', ''),
                                   type=result.get('type', ''),
                                   concepts=result.get('concepts', []))
                        return result
                    else:
                        missing = [k for k in required if k not in result]
                        logger.warning("LLM classification missing fields: {missing}", missing=missing)
                        return None
                except json.JSONDecodeError as e:
                    logger.warning("Failed to parse LLM classification JSON: {e}", e=e)
                    return None
            else:
                logger.warning("No JSON found in LLM classification response")
                return None
                
        except Exception as e:
            logger.warning("LLM observation classification failed: {e}", e=e)
            return None

    _SUMMARY_GENERATION_PROMPT = '''You are a session summarizer. Analyze the user's request and tool executions to generate a concise summary.

## Output Format

Respond in this exact JSON format:
```json
{
  "investigated": "What was explored or researched (1 sentence)",
  "completed": "What was accomplished (1 sentence)",
  "learned": "Key insights or discoveries (1 sentence, or empty if none)",
  "next_steps": "Suggested next actions (1 sentence, or empty if none)"
}
```

## Guidelines

- **investigated**: Describe what the user asked and what was explored
- **completed**: Describe concrete outcomes (files modified, commands run, etc.)
- **learned**: Key technical insights or findings (empty if straightforward task)
- **next_steps**: Logical follow-up actions (empty if task is complete)

## Examples

User: "Check how many stars my GitHub repo has"
Tools: Shell(curl api.github.com...)
→ investigated: "Queried GitHub API for repository star count"
→ completed: "Retrieved star count for albertwyjoy-bit/OKbot"
→ learned: "GitHub API returns stargazers_count field"
→ next_steps: "Update documentation with star count"

User: "Fix the bug in auth.py"
Tools: ReadFile(auth.py), StrReplaceFile(auth.py, fix)
→ investigated: "Analyzed authentication bug in auth.py"
→ completed: "Fixed null pointer exception in validate_token()"
→ learned: "Token validation was missing null check"
→ next_steps: "Run tests to verify fix"'''

    async def _generate_summary_with_llm(
        self,
        user_input: str,
        tool_history: list[dict],
        is_compact: bool
    ) -> dict:
        """
        使用 LLM 智能生成 Session Summary
        
        Returns:
            dict with keys: investigated, completed, learned, next_steps
        """
        if self._runtime.llm is None:
            return {"investigated": "", "completed": "", "learned": "", "next_steps": ""}
        
        # Build the user prompt
        tool_history_text = json.dumps(tool_history, indent=2, default=str) if tool_history else "No tools executed"
        
        user_content = f"""Generate a summary for this session:

**User Request**: {user_input}

**Tools Executed**:
```json
{tool_history_text}
```

**Context**: {"Context was compacted during this session." if is_compact else ""}

Provide your summary in the specified JSON format."""

        try:
            # Call LLM for summary generation
            response = await self._runtime.llm.chat_provider.generate(
                system_prompt=self._SUMMARY_GENERATION_PROMPT,
                tools=[],
                history=[
                    Message(role="user", content=user_content)
                ]
            )
            
            # Parse the response - handle streamed messages
            response_text = ""
            if hasattr(response, '__aiter__'):
                # It's an async iterator (KimiStreamedMessage)
                async for part in response:
                    if hasattr(part, 'text'):
                        response_text += part.text
                    elif hasattr(part, 'think'):
                        pass  # Skip thinking parts
            elif hasattr(response, 'content') and response.content:
                response_text = response.content
            elif hasattr(response, 'text') and response.text:
                response_text = response.text
            else:
                response_text = str(response)
            
            if not response_text or response_text.startswith('<'):
                logger.warning("Could not extract text from summary response")
                return {"investigated": "", "completed": "", "learned": "", "next_steps": ""}
            
            # Extract JSON from response
            json_match = None
            if '```json' in response_text:
                json_match = response_text.split('```json')[1].split('```')[0].strip()
            elif '```' in response_text:
                json_match = response_text.split('```')[1].split('```')[0].strip()
            else:
                json_match = response_text.strip()
            
            if json_match:
                result = json.loads(json_match)
                # Validate required fields
                required = ['investigated', 'completed', 'learned', 'next_steps']
                if all(k in result for k in required):
                    logger.info("LLM generated summary: {inv} | {comp}", 
                               inv=result.get('investigated', '')[:50],
                               comp=result.get('completed', '')[:50])
                    return result
                else:
                    logger.debug("LLM summary missing required fields")
                    return {"investigated": "", "completed": "", "learned": "", "next_steps": ""}
            else:
                return {"investigated": "", "completed": "", "learned": "", "next_steps": ""}
                
        except Exception as e:
            logger.warning("LLM summary generation failed: {e}", e=e)
            return {"investigated": "", "completed": "", "learned": "", "next_steps": ""}

    async def _extract_observation_from_tool_result(
        self, tool_result, tool_call
    ) -> ObservationInput | None:
        """
        从工具结果中提取 Observation
        
        🔴 使用 LLM 智能分类（参考 claude-mem），失败时回退到启发式规则
        """
        memory = self._runtime.memory_agent
        if not memory:
            return None

        # Extract tool info from ToolCall
        tool_name = tool_call.function.name
        return_value = tool_result.return_value
        
        # Parse arguments from JSON string
        import json
        args = {}
        if tool_call.function.arguments:
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                args = {}

        # Skip certain tools that don't produce meaningful observations
        if tool_name in ("ReadFile", "ReadMediaFile", "Grep", "Glob"):
            # Read-only tools typically don't need observations unless they reveal bugs
            logger.debug("Skipping observation for read-only tool: {tool_name}", tool_name=tool_name)
            return None

        # Extract file paths from args
        files_modified = []
        files_read = []
        if isinstance(args, dict):
            if "path" in args:
                path = args["path"]
                if isinstance(path, str):
                    files_modified.append(path)
            if "paths" in args and isinstance(args["paths"], list):
                files_modified.extend(args["paths"])

        # 🔴 首先尝试使用 LLM 智能分类
        logger.info("Attempting LLM classification for {tool_name}", tool_name=tool_name)
        llm_classification = await self._classify_observation_with_llm(
            tool_name=tool_name,
            args=args,
            return_value=return_value,
            files_read=files_read,
            files_modified=files_modified
        )
        
        if llm_classification:
            logger.info("Using LLM classification for {tool_name}", tool_name=tool_name)
            # 使用 LLM 分类结果
            type_str = llm_classification.get('type', 'change').lower()
            obs_type = ObservationType.CHANGE
            try:
                obs_type = ObservationType(type_str)
            except ValueError:
                # Map to closest type if LLM returns invalid
                type_mapping = {
                    'bugfix': ObservationType.BUGFIX,
                    'feature': ObservationType.FEATURE,
                    'refactor': ObservationType.REFACTOR,
                    'change': ObservationType.CHANGE,
                    'discovery': ObservationType.DISCOVERY,
                    'decision': ObservationType.DECISION,
                }
                obs_type = type_mapping.get(type_str, ObservationType.CHANGE)
            
            # 🔴 从 runtime 获取 project（work_dir）
            project = str(self._runtime.session.work_dir) if self._runtime.session else "/"
            
            return ObservationInput(
                session_id=self._runtime.session.id,
                project=project,
                type=obs_type,
                title=llm_classification.get('title', f"Used {tool_name}"),
                subtitle=llm_classification.get('subtitle', ''),
                facts=llm_classification.get('facts', []),
                narrative=llm_classification.get('narrative', ''),
                concepts=llm_classification.get('concepts', []),
                files_read=files_read,
                files_modified=files_modified,
                tool_name=tool_name,
                prompt_number=self._prompt_number,
                discovery_tokens=0,
            )
        
        # 🔴 回退到启发式规则（LLM 分类失败时）
        logger.debug("Falling back to heuristic rules for {tool_name}", tool_name=tool_name)
        obs_type = ObservationType.CHANGE
        title = f"Used {tool_name}"
        facts = []
        concepts = []

        # Extract concepts from command
        if isinstance(args, dict) and "command" in args:
            cmd = args["command"]
            if isinstance(cmd, str):
                if any(x in cmd.lower() for x in ["git", "commit", "merge", "rebase"]):
                    concepts.append("git")
                if any(x in cmd.lower() for x in ["pip", "npm", "yarn", "pnpm"]):
                    concepts.append("dependencies")

        # Classify by tool name and result
        if tool_name == "StrReplaceFile" or tool_name == "WriteFile":
            obs_type = ObservationType.CHANGE
            title = f"Modified {files_modified[0] if files_modified else 'file'}"
            if files_modified:
                facts.append(f"Modified file: {files_modified[0]}")
                # Infer concepts from file path
                path = files_modified[0].lower()
                if ".py" in path:
                    concepts.append("python")
                if ".ts" in path or ".js" in path:
                    concepts.append("javascript")
                if "test" in path:
                    concepts.append("testing")
                if "config" in path:
                    concepts.append("config")

        elif tool_name == "Shell":
            obs_type = ObservationType.DISCOVERY
            title = "Executed shell command"
            if isinstance(args, dict) and "command" in args:
                cmd = args["command"]
                if len(cmd) > 50:
                    cmd = cmd[:47] + "..."
                title = f"Shell: {cmd}"
                facts.append(f"Command: {cmd}")

        elif tool_name == "Task":
            obs_type = ObservationType.FEATURE
            title = "Spawned subagent task"
            if isinstance(args, dict) and "description" in args:
                desc = args["description"]
                if len(desc) > 50:
                    desc = desc[:47] + "..."
                title = f"Task: {desc}"

        elif tool_name == "SearchWeb":
            obs_type = ObservationType.DISCOVERY
            title = "Web search"
            if isinstance(args, dict) and "query" in args:
                facts.append(f"Searched: {args['query']}")

        elif tool_name == "PlanExit":
            obs_type = ObservationType.DECISION
            title = "User confirmed plan execution"

        elif tool_name == "SetTodoList":
            obs_type = ObservationType.DECISION
            title = "Updated task list"

        # Check for error indicators in return value
        return_str = str(return_value).lower()
        if any(x in return_str for x in ["error", "exception", "failed", "traceback"]):
            obs_type = ObservationType.BUGFIX if "fix" in return_str else ObservationType.DISCOVERY
            facts.append("Result may contain error information")

        if not facts and not files_modified:
            logger.debug(
                "Skipping observation: no facts or files_modified for {tool_name}",
                tool_name=tool_name
            )
            return None

        # 🔴 从 runtime 获取 project（work_dir）
        project = str(self._runtime.session.work_dir) if self._runtime.session else "/"

        return ObservationInput(
            session_id=self._runtime.session.id,
            project=project,
            type=obs_type,
            title=title,
            facts=facts,
            concepts=concepts,
            files_read=files_read,
            files_modified=files_modified,
            tool_name=tool_name,
            prompt_number=self._prompt_number,
            discovery_tokens=0,
        )

    async def _generate_and_save_summary(
        self,
        user_input: str,
        is_compact: bool,
        prompt_number: int,
    ):
        """
        生成并保存 Session Summary 和 User Prompt
        
        同时保存：
        1. UserPrompt - 用户输入的原始文本（用于时间线展示）
        2. SessionSummary - 会话摘要（包含完成情况等）
        """
        memory = self._runtime.memory_agent
        if not memory:
            return

        # 🔴 首先保存 UserPrompt（确保时间戳早于 observations 和 summary）
        project = str(self._runtime.session.work_dir) if self._runtime.session else "/"
        prompt_input = UserPromptInput(
            session_id=self._runtime.session.id,
            project=project,
            prompt_number=prompt_number,
            prompt_text=user_input,
        )
        try:
            await memory.queue_prompt(prompt_input, wait=False)  # 异步保存，不阻塞
        except Exception as e:
            logger.warning(f"Failed to save prompt: {e}")

        # Build tool execution history for LLM analysis
        tool_history = []
        files_touched = set()
        
        for tool_result, tool_call in self._current_turn_tool_results:
            tool_name = tool_call.function.name
            
            # Parse arguments from JSON string
            args = {}
            if tool_call.function.arguments:
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    args = {}

            if isinstance(args, dict):
                if "path" in args:
                    files_touched.add(str(args["path"]))

            tool_history.append({
                "tool": tool_name,
                "args": args,
                "result_preview": str(tool_result.return_value)[:200] if tool_result.return_value else ""
            })

        # 🔴 使用 LLM 生成智能 summary
        llm_summary = await self._generate_summary_with_llm(
            user_input=user_input,
            tool_history=tool_history,
            is_compact=is_compact
        )
        
        # Build summary input with LLM-generated content
        summary_input = SummaryInput(
            session_id=self._runtime.session.id,
            project=project,
            request=user_input[:200] if len(user_input) > 200 else user_input,
            investigated=llm_summary.get("investigated", ""),
            completed=llm_summary.get("completed", ""),
            learned=llm_summary.get("learned", ""),
            next_steps=llm_summary.get("next_steps", ""),
            notes=f"Files touched: {', '.join(files_touched)}" if files_touched else "",
            prompt_number=prompt_number,
            discovery_tokens=0,
        )

        await memory.queue_summary(summary_input, wait=True)


class BackToTheFuture(Exception):
    """
    Raise when we need to revert the context to a previous checkpoint.
    The main agent loop should catch this exception and handle it.
    """

    def __init__(self, checkpoint_id: int, messages: Sequence[Message]):
        self.checkpoint_id = checkpoint_id
        self.messages = messages


class FlowRunner:
    def __init__(
        self,
        flow: Flow,
        *,
        name: str | None = None,
        max_moves: int = DEFAULT_MAX_FLOW_MOVES,
    ) -> None:
        self._flow = flow
        self._name = name
        self._max_moves = max_moves

    @staticmethod
    def ralph_loop(
        user_message: Message,
        max_ralph_iterations: int,
    ) -> FlowRunner:
        prompt_content = list(user_message.content)
        prompt_text = Message(role="user", content=prompt_content).extract_text(" ").strip()
        total_runs = max_ralph_iterations + 1
        if max_ralph_iterations < 0:
            total_runs = 1000000000000000  # effectively infinite

        nodes: dict[str, FlowNode] = {
            "BEGIN": FlowNode(id="BEGIN", label="BEGIN", kind="begin"),
            "END": FlowNode(id="END", label="END", kind="end"),
        }
        outgoing: dict[str, list[FlowEdge]] = {"BEGIN": [], "END": []}

        nodes["R1"] = FlowNode(id="R1", label=prompt_content, kind="task")
        nodes["R2"] = FlowNode(
            id="R2",
            label=(
                f"{prompt_text}. (You are running in an automated loop where the same "
                "prompt is fed repeatedly. Only choose STOP when the task is fully complete. "
                "Including it will stop further iterations. If you are not 100% sure, "
                "choose CONTINUE.)"
            ).strip(),
            kind="decision",
        )
        outgoing["R1"] = []
        outgoing["R2"] = []

        outgoing["BEGIN"].append(FlowEdge(src="BEGIN", dst="R1", label=None))
        outgoing["R1"].append(FlowEdge(src="R1", dst="R2", label=None))
        outgoing["R2"].append(FlowEdge(src="R2", dst="R2", label="CONTINUE"))
        outgoing["R2"].append(FlowEdge(src="R2", dst="END", label="STOP"))

        flow = Flow(nodes=nodes, outgoing=outgoing, begin_id="BEGIN", end_id="END")
        max_moves = total_runs
        return FlowRunner(flow, max_moves=max_moves)

    async def run(self, soul: KimiSoul, args: str) -> None:
        if args.strip():
            command = f"/{FLOW_COMMAND_PREFIX}{self._name}" if self._name else "/flow"
            logger.warning("Agent flow {command} ignores args: {args}", command=command, args=args)
            return

        current_id = self._flow.begin_id
        moves = 0
        total_steps = 0
        while True:
            node = self._flow.nodes[current_id]
            edges = self._flow.outgoing.get(current_id, [])

            if node.kind == "end":
                logger.info("Agent flow reached END node {node_id}", node_id=current_id)
                return

            if node.kind == "begin":
                if not edges:
                    logger.error(
                        'Agent flow BEGIN node "{node_id}" has no outgoing edges; stopping.',
                        node_id=node.id,
                    )
                    return
                current_id = edges[0].dst
                continue

            if moves >= self._max_moves:
                raise MaxStepsReached(total_steps)
            next_id, steps_used = await self._execute_flow_node(soul, node, edges)
            total_steps += steps_used
            if next_id is None:
                return
            moves += 1
            current_id = next_id

    async def _execute_flow_node(
        self,
        soul: KimiSoul,
        node: FlowNode,
        edges: list[FlowEdge],
    ) -> tuple[str | None, int]:
        if not edges:
            logger.error(
                'Agent flow node "{node_id}" has no outgoing edges; stopping.',
                node_id=node.id,
            )
            return None, 0

        base_prompt = self._build_flow_prompt(node, edges)
        prompt = base_prompt
        steps_used = 0
        while True:
            result = await self._flow_turn(soul, prompt)
            steps_used += result.step_count
            if result.stop_reason == "tool_rejected":
                logger.error("Agent flow stopped after tool rejection.")
                return None, steps_used

            if node.kind != "decision":
                return edges[0].dst, steps_used

            choice = (
                parse_choice(result.final_message.extract_text(" "))
                if result.final_message
                else None
            )
            next_id = self._match_flow_edge(edges, choice)
            if next_id is not None:
                return next_id, steps_used

            options = ", ".join(edge.label or "" for edge in edges)
            logger.warning(
                "Agent flow invalid choice. Got: {choice}. Available: {options}.",
                choice=choice or "<missing>",
                options=options,
            )
            prompt = (
                f"{base_prompt}\n\n"
                "Your last response did not include a valid choice. "
                "Reply with one of the choices using <choice>...</choice>."
            )

    @staticmethod
    def _build_flow_prompt(node: FlowNode, edges: list[FlowEdge]) -> str | list[ContentPart]:
        if node.kind != "decision":
            return node.label

        if not isinstance(node.label, str):
            label_text = Message(role="user", content=node.label).extract_text(" ")
        else:
            label_text = node.label
        choices = [edge.label for edge in edges if edge.label]
        lines = [
            label_text,
            "",
            "Available branches:",
            *(f"- {choice}" for choice in choices),
            "",
            "Reply with a choice using <choice>...</choice>.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _match_flow_edge(edges: list[FlowEdge], choice: str | None) -> str | None:
        if not choice:
            return None
        for edge in edges:
            if edge.label == choice:
                return edge.dst
        return None

    @staticmethod
    async def _flow_turn(
        soul: KimiSoul,
        prompt: str | list[ContentPart],
    ) -> TurnOutcome:
        wire_send(TurnBegin(user_input=prompt))
        res = await soul._turn(Message(role="user", content=prompt))  # type: ignore[reportPrivateUsage]
        wire_send(TurnEnd())
        return res
