from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from kosong.chat_provider import (
    APIEmptyResponseError,
    ChatProvider,
    StreamedMessagePart,
    TokenUsage,
)
from kosong.message import ContentPart, Message, ToolCall
from kosong.tooling import Tool
from kosong.utils.aio import Callback, callback


def _format_message_for_log(message: Message, max_content_length: int = 500) -> dict[str, Any]:
    """Format a message for logging, truncating long content."""
    content_preview = message.extract_text()
    if len(content_preview) > max_content_length:
        content_preview = content_preview[:max_content_length] + "..."

    result: dict[str, Any] = {
        "role": message.role,
        "content": content_preview,
    }

    if message.tool_calls:
        result["tool_calls"] = [
            {"id": tc.id, "function": tc.function.name} for tc in message.tool_calls
        ]

    if message.tool_call_id:
        result["tool_call_id"] = message.tool_call_id

    return result


def _format_tools_for_log(tools: Sequence[Tool], max_length: int = 300) -> list[dict[str, Any]]:
    """Format tools for logging, truncating long descriptions."""
    result = []
    for tool in tools:
        tool_info: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
        }
        if tool.description and len(tool.description) > max_length:
            tool_info["description"] = tool.description[:max_length] + "..."
        result.append(tool_info)
    return result


async def generate(
    chat_provider: ChatProvider,
    system_prompt: str,
    tools: Sequence[Tool],
    history: Sequence[Message],
    *,
    on_message_part: Callback[[StreamedMessagePart], None] | None = None,
    on_tool_call: Callback[[ToolCall], None] | None = None,
) -> "GenerateResult":
    """
    Generate one message based on the given context.
    Parts of the message will be streamed to the specified callbacks if provided.

    Args:
        chat_provider: The chat provider to use for generation.
        system_prompt: The system prompt to use for generation.
        tools: The tools available for the model to call.
        history: The message history to use for generation.
        on_message_part: An optional callback to be called for each raw message part.
        on_tool_call: An optional callback to be called for each complete tool call.

    Returns:
        A tuple of the generated message and the token usage (if available).
        All parts in the message are guaranteed to be complete and merged as much as possible.

    Raises:
        APIConnectionError: If the API connection fails.
        APITimeoutError: If the API request times out.
        APIStatusError: If the API returns a status code of 4xx or 5xx.
        APIEmptyResponseError: If the API returns an empty response.
        ChatProviderError: If any other recognized chat provider error occurs.
    """
    message = Message(role="assistant", content=[])
    pending_part: StreamedMessagePart | None = None  # message part that is currently incomplete

    # Log LLM call information at INFO level
    system_prompt_preview = system_prompt
    if len(system_prompt_preview) > 500:
        system_prompt_preview = system_prompt_preview[:500] + "..."

    logger.info(
        "LLM Request | model={model} | system_prompt={system_prompt} | "
        "tools_count={tools_count} | history_count={history_count}",
        model=chat_provider.model_name,
        system_prompt=system_prompt_preview,
        tools_count=len(tools),
        history_count=len(history),
    )

    # Log detailed messages info at DEBUG level for verbose logging
    logger.debug(
        "LLM Messages | messages={messages}",
        messages=[_format_message_for_log(msg) for msg in history],
    )

    # Log tools info at DEBUG level
    if tools:
        logger.debug(
            "LLM Tools | tools={tools}",
            tools=_format_tools_for_log(tools),
        )

    logger.trace("Generating with history: {history}", history=history)
    stream = await chat_provider.generate(system_prompt, tools, history)
    async for part in stream:
        logger.trace("Received part: {part}", part=part)
        if on_message_part:
            await callback(on_message_part, part.model_copy(deep=True))

        if pending_part is None:
            pending_part = part
        elif not pending_part.merge_in_place(part):  # try merge into the pending part
            # unmergeable part must push the pending part to the buffer
            _message_append(message, pending_part)
            if isinstance(pending_part, ToolCall) and on_tool_call:
                await callback(on_tool_call, pending_part)
            pending_part = part

    # end of message
    if pending_part is not None:
        _message_append(message, pending_part)
        if isinstance(pending_part, ToolCall) and on_tool_call:
            await callback(on_tool_call, pending_part)

    if not message.content and not message.tool_calls:
        raise APIEmptyResponseError("The API returned an empty response.")

    # Log response at INFO level
    response_preview = message.extract_text()
    if len(response_preview) > 500:
        response_preview = response_preview[:500] + "..."

    tool_calls_info = None
    if message.tool_calls:
        tool_calls_info = [{"id": tc.id, "function": tc.function.name} for tc in message.tool_calls]

    usage_info = None
    if stream.usage:
        usage_info = {
            "input": stream.usage.input,
            "output": stream.usage.output,
            "total": stream.usage.total,
        }

    logger.info(
        "LLM Response | id={id} | response={response} | tool_calls={tool_calls} | usage={usage}",
        id=stream.id,
        response=response_preview,
        tool_calls=tool_calls_info,
        usage=usage_info,
    )

    return GenerateResult(
        id=stream.id,
        message=message,
        usage=stream.usage,
    )


@dataclass(frozen=True, slots=True)
class GenerateResult:
    """The result of a generation."""

    id: str | None
    """The ID of the generated message."""
    message: Message
    """The generated message."""
    usage: TokenUsage | None
    """The token usage of the generated message."""


def _message_append(message: Message, part: StreamedMessagePart) -> None:
    match part:
        case ContentPart():
            message.content.append(part)
        case ToolCall():
            if message.tool_calls is None:
                message.tool_calls = []
            message.tool_calls.append(part)
        case _:
            # may be an orphaned `ToolCallPart`
            return
