"""Feishu interactive card builder for beautifying messages.

This module provides various card templates for different message types:
- Thought cards for AI reasoning
- Tool call cards for function invocations
- Tool result cards for execution results
- Search result cards for web/file search results

Uses Feishu Message Card format (schema 2.0).
Reference: https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-overview
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypeAlias

# Card color schemes matching Feishu design
CardColor: TypeAlias = Literal["blue", "green", "red", "orange", "purple", "grey", "default"]

# Feishu template colors
TEMPLATE_COLORS: dict[CardColor, str] = {
    "blue": "blue",
    "green": "green",
    "red": "red",
    "orange": "orange",
    "purple": "purple",
    "grey": "grey",
    "default": "default",
}

CARD_ICONS: dict[str, str] = {
    "thought": "💭",
    "tool_call": "🔧",
    "tool_result": "📊",
    "search": "🔍",
    "response": "🤖",
    "error": "❌",
    "success": "✅",
}


def _truncate_text(text: str, max_length: int = 500) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def _plain_text_element(content: str) -> dict[str, Any]:
    """Create a plain text element."""
    return {
        "tag": "plain_text",
        "content": content,
    }


def _markdown_element(content: str) -> dict[str, Any]:
    """Create a markdown text element."""
    return {
        "tag": "lark_md",
        "content": content,
    }


def _divider() -> dict[str, Any]:
    """Create a divider element."""
    return {"tag": "hr"}


def _note_element(content: str, icon: str = "") -> dict[str, Any]:
    """Create a note element."""
    text = f"{icon} {content}" if icon else content
    return {
        "tag": "note",
        "elements": [_plain_text_element(text)],
    }


def build_thought_card(thought: str, is_collapsed: bool = False) -> dict[str, Any]:
    """Build a card for AI thought/reasoning.
    
    Args:
        thought: The thought content
        is_collapsed: Ignored parameter (for backward compatibility)
        
    Returns:
        Interactive card JSON
    """
    truncated = _truncate_text(thought, 3000)
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(truncated),
        },
    ]
    
    # Add note if truncated
    if len(thought) > 3000:
        elements.append(_note_element("思考内容已截断"))
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "grey",
            "title": {
                "tag": "plain_text",
                "content": f"{CARD_ICONS['thought']} 思考过程",
            },
        },
        "elements": elements,
    }


def build_tool_call_card(
    tool_name: str,
    arguments: dict[str, Any] | str,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    """Build a card for tool/function call.
    
    Args:
        tool_name: Name of the tool being called
        arguments: Tool arguments (dict or JSON string)
        tool_call_id: Optional tool call ID
        
    Returns:
        Interactive card JSON
    """
    # Format arguments
    if isinstance(arguments, dict):
        args_str = json.dumps(arguments, indent=2, ensure_ascii=False)
    else:
        try:
            args_dict = json.loads(arguments)
            args_str = json.dumps(args_dict, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            args_str = str(arguments)
    
    # Truncate if too long
    display_args = _truncate_text(args_str, 2000)
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"**工具**: `{tool_name}`"),
        },
        _divider(),
        {
            "tag": "div",
            "text": _plain_text_element("**参数:**"),
        },
        {
            "tag": "div",
            "text": _markdown_element(f"```json\n{display_args}\n```"),
        },
    ]
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": f"{CARD_ICONS['tool_call']} 工具调用",
            },
        },
        "elements": elements,
    }


def build_tool_result_card(
    tool_name: str,
    result: Any,
    is_error: bool = False,
    execution_time: float | None = None,
) -> dict[str, Any]:
    """Build a card for tool execution result.
    
    Args:
        tool_name: Name of the tool
        result: Tool execution result
        is_error: Whether the result is an error
        execution_time: Optional execution time in seconds
        
    Returns:
        Interactive card JSON
    """
    template: CardColor = "red" if is_error else "green"
    status_icon = CARD_ICONS["error"] if is_error else CARD_ICONS["success"]
    status_text = "执行失败" if is_error else "执行成功"
    
    # Format result
    if isinstance(result, dict):
        result_str = json.dumps(result, indent=2, ensure_ascii=False)
    elif isinstance(result, str):
        try:
            # Try to parse as JSON for pretty printing
            parsed = json.loads(result)
            result_str = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            result_str = result
    else:
        result_str = str(result)
    
    # Build header content
    header_content = f"{status_icon} {status_text}"
    if execution_time is not None:
        header_content += f" ({execution_time:.2f}s)"
    
    # Truncate result if too long
    display_result = _truncate_text(result_str, 3000)
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"**工具**: `{tool_name}`"),
        },
        _divider(),
        {
            "tag": "div",
            "text": _markdown_element(f"```\n{display_result}\n```"),
        },
    ]
    
    # Add note if truncated
    if len(result_str) > 3000:
        elements.append(_note_element("结果内容已截断"))
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {
                "tag": "plain_text",
                "content": header_content,
            },
        },
        "elements": elements,
    }


def build_search_result_card(
    query: str,
    results: list[dict[str, str]],
    total_count: int | None = None,
    search_type: str = "web",
) -> dict[str, Any]:
    """Build a card for search results (web search, file search, etc.).
    
    Args:
        query: Search query
        results: List of search results, each with 'title', 'url', 'snippet' keys
        total_count: Total number of results (may be more than results list)
        search_type: Type of search (web, file, etc.)
        
    Returns:
        Interactive card JSON
    """
    type_icon = "🌐" if search_type == "web" else "📁"
    type_text = "网页搜索" if search_type == "web" else "文件搜索"
    
    count_text = f"共 {total_count} 条" if total_count else f"{len(results)} 条结果"
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"**查询**: {query}"),
        },
        _divider(),
    ]
    
    # Add each result
    for i, result in enumerate(results[:10], 1):  # Max 10 results
        title = result.get("title", "无标题")
        url = result.get("url", "")
        snippet = result.get("snippet", result.get("content", ""))
        
        # Result header with number and title
        if url:
            elements.append({
                "tag": "div",
                "text": _markdown_element(f"**{i}.** [{title}]({url})"),
            })
        else:
            elements.append({
                "tag": "div",
                "text": _plain_text_element(f"{i}. {title}"),
            })
        
        # Snippet
        if snippet:
            elements.append({
                "tag": "div",
                "text": _plain_text_element(_truncate_text(snippet, 150)),
            })
        
        # Add divider between results (except last)
        if i < len(results[:10]):
            elements.append(_divider())
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {
                "tag": "plain_text",
                "content": f"{type_icon} {type_text} - {count_text}",
            },
        },
        "elements": elements,
    }


def build_response_card(
    content: str,
    is_markdown: bool = True,
) -> dict[str, Any]:
    """Build a card for AI response text.
    
    Args:
        content: Response content
        is_markdown: Whether content is markdown formatted
        
    Returns:
        Interactive card JSON
    """
    # For long responses, we may want to truncate or split
    max_length = 8000  # Feishu card size limit
    
    if len(content) > max_length:
        content = content[:max_length - 100] + "\n\n... (内容已截断)"
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "default",
            "title": {
                "tag": "plain_text",
                "content": f"{CARD_ICONS['response']} 回复",
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": _markdown_element(content) if is_markdown else _plain_text_element(content),
            },
        ],
    }


def build_error_card(
    error_message: str,
    error_type: str = "Error",
    suggestion: str | None = None,
) -> dict[str, Any]:
    """Build an error notification card.
    
    Args:
        error_message: Error description
        error_type: Type of error
        suggestion: Optional suggestion to fix
        
    Returns:
        Interactive card JSON
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(error_message),
        },
    ]
    
    if suggestion:
        elements.append(_divider())
        elements.append({
            "tag": "div",
            "text": _plain_text_element(f"💡 建议: {suggestion}"),
        })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {
                "tag": "plain_text",
                "content": f"{CARD_ICONS['error']} {error_type}",
            },
        },
        "elements": elements,
    }


def build_status_card(
    status: str,
    detail: str | None = None,
    is_loading: bool = False,
) -> dict[str, Any]:
    """Build a status/loading card.
    
    Args:
        status: Status text
        detail: Optional detail text
        is_loading: Whether to show loading indicator
        
    Returns:
        Interactive card JSON
    """
    icon = "⏳" if is_loading else "ℹ️"
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"{icon} {status}"),
        },
    ]
    
    if detail:
        elements.append({
            "tag": "div",
            "text": _plain_text_element(detail),
        })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": status,
            },
        },
        "elements": elements,
    }


def build_approval_card(
    tool_name: str,
    description: str,
    request_id: str,
    display_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build an approval request card with interactive buttons.
    
    This card is used when YOLO mode is disabled and user approval is required
    for tool execution. Users can choose to:
    1. Approve once - allow this single execution
    2. Approve for this conversation - always allow this action
    3. Reject - deny this execution
    
    Args:
        tool_name: Name of the tool requesting approval
        description: Description of the action
        request_id: Unique ID for this approval request
        display_blocks: Optional display blocks from the tool
        
    Returns:
        Interactive card JSON with approval buttons
    """
    # Truncate description if too long
    display_desc = _truncate_text(description, 500)
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"**工具**: `{tool_name}`"),
        },
        {
            "tag": "div",
            "text": _plain_text_element(f"**操作**: {display_desc}"),
        },
        _divider(),
        {
            "tag": "div",
            "text": _markdown_element("**请选择操作：**"),
        },
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✅ 允许一次",
                    },
                    "type": "primary",
                    "value": {
                        "key": "approve_once",
                        "request_id": request_id,
                    },
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "🔓 始终允许",
                    },
                    "type": "default",
                    "value": {
                        "key": "approve_session",
                        "request_id": request_id,
                    },
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "❌ 拒绝",
                    },
                    "type": "danger",
                    "value": {
                        "key": "reject",
                        "request_id": request_id,
                    },
                },
            ],
        },
        {
            "tag": "note",
            "elements": [
                _plain_text_element("💡 提示: 发送 /yolo 可切换为自动批准模式")
            ],
        },
    ]
    
    # Add display blocks if provided
    if display_blocks:
        elements.insert(2, _divider())
        elements.insert(3, {
            "tag": "div",
            "text": _plain_text_element("**详细信息:**"),
        })
        for block in display_blocks[:5]:  # Limit to 5 blocks
            content = block.get("content", "")
            if content:
                elements.insert(4, {
                    "tag": "div",
                    "text": _markdown_element(f"```\n{_truncate_text(content, 1000)}\n```"),
                })
    
    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
            "update_multi": True,
        },
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": f"{CARD_ICONS['tool_call']} 需要授权",
            },
        },
        "elements": elements,
    }


def build_approval_result_card(
    tool_name: str,
    approved: bool,
    is_session_approval: bool = False,
) -> dict[str, Any]:
    """Build a card showing the approval result.
    
    Args:
        tool_name: Name of the tool
        approved: Whether the action was approved
        is_session_approval: Whether this is a session-level approval
        
    Returns:
        Card JSON
    """
    if approved:
        template: CardColor = "green"
        icon = "✅"
        status = "已批准"
        if is_session_approval:
            detail = f"{tool_name} 已添加到始终允许列表"
        else:
            detail = f"{tool_name} 执行中..."
    else:
        template = "red"
        icon = "❌"
        status = "已拒绝"
        detail = f"{tool_name} 执行被拒绝"
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {
                "tag": "plain_text",
                "content": f"{icon} {status}",
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": _plain_text_element(detail),
            },
        ],
    }


def build_model_selection_card(
    models: list[dict[str, Any]],
    current_model: str | None = None,
    request_id: str = "model_select",
) -> dict[str, Any]:
    """Build a card for selecting LLM model.
    
    This card displays a list of available models as selectable buttons.
    Users can click on a model to select it.
    
    Args:
        models: List of model info dicts with 'name', 'model', 'provider', 'label' keys
        current_model: Name of the currently selected model
        request_id: Unique ID for this selection request
        
    Returns:
        Interactive card JSON with model selection buttons
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _markdown_element("**请选择要使用的模型：**"),
        },
        _divider(),
    ]
    
    # Add model selection buttons
    actions: list[dict[str, Any]] = []
    for model in models:
        name = model.get("name", "")
        label = model.get("label", name)
        is_current = name == current_model
        
        button_text = f"✓ {label}" if is_current else label
        button_style = "primary" if is_current else "default"
        
        actions.append({
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": button_text,
            },
            "type": button_style,
            "value": {
                "key": "select_model",
                "request_id": request_id,
                "model_name": name,
            },
        })
    
    # Add action buttons in groups of 3 per row
    for i in range(0, len(actions), 3):
        row_actions = actions[i:i+3]
        elements.append({
            "tag": "action",
            "actions": row_actions,
        })
    
    # Add note about current selection
    if current_model:
        elements.append(_note_element(f"当前模型: {current_model}", "💡"))
    
    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
            "update_multi": True,
        },
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": "🤖 选择模型",
            },
        },
        "elements": elements,
    }


def build_thinking_selection_card(
    current_thinking: bool = False,
    model_name: str = "",
    request_id: str = "thinking_select",
) -> dict[str, Any]:
    """Build a card for selecting thinking mode.
    
    This card displays options to enable or disable thinking mode.
    
    Args:
        current_thinking: Current thinking mode state
        model_name: Name of the selected model
        request_id: Unique ID for this selection request
        
    Returns:
        Interactive card JSON with thinking mode selection buttons
    """
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"**模型**: `{model_name}`"),
        },
        {
            "tag": "div",
            "text": _markdown_element("**是否启用 Thinking 模式？**"),
        },
        _divider(),
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ 开启" if current_thinking else "开启",
                    },
                    "type": "primary" if current_thinking else "default",
                    "value": {
                        "key": "select_thinking",
                        "request_id": request_id,
                        "thinking": True,
                        "model_name": model_name,
                    },
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✓ 关闭" if not current_thinking else "关闭",
                    },
                    "type": "primary" if not current_thinking else "default",
                    "value": {
                        "key": "select_thinking",
                        "request_id": request_id,
                        "thinking": False,
                        "model_name": model_name,
                    },
                },
            ],
        },
        {
            "tag": "note",
            "elements": [
                _plain_text_element("💡 Thinking 模式会让模型展示更详细的推理过程")
            ],
        },
    ]
    
    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
            "update_multi": True,
        },
        "header": {
            "template": "purple",
            "title": {
                "tag": "plain_text",
                "content": "💭 Thinking 模式",
            },
        },
        "elements": elements,
    }


def build_model_confirm_card(
    model_name: str,
    thinking: bool,
    confirm_request_id: str = "model_confirm",
) -> dict[str, Any]:
    """Build a card for confirming model and thinking mode selection.
    
    Args:
        model_name: Selected model name
        thinking: Selected thinking mode
        confirm_request_id: Unique ID for confirmation
        
    Returns:
        Interactive card JSON with confirm/cancel buttons
    """
    thinking_text = "开启" if thinking else "关闭"
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(f"**模型**: `{model_name}`"),
        },
        {
            "tag": "div",
            "text": _plain_text_element(f"**Thinking 模式**: {thinking_text}"),
        },
        _divider(),
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "✅ 确认切换",
                    },
                    "type": "primary",
                    "value": {
                        "key": "confirm_model",
                        "request_id": confirm_request_id,
                        "model_name": model_name,
                        "thinking": thinking,
                    },
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "❌ 取消",
                    },
                    "type": "default",
                    "value": {
                        "key": "cancel_model",
                        "request_id": confirm_request_id,
                    },
                },
            ],
        },
        {
            "tag": "note",
            "elements": [
                _plain_text_element("💡 确认后请发送 /new 创建新会话以应用新设置")
            ],
        },
    ]
    
    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
            "update_multi": True,
        },
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": "🔄 确认切换",
            },
        },
        "elements": elements,
    }


def build_model_result_card(
    model_name: str,
    thinking: bool,
    success: bool = True,
) -> dict[str, Any]:
    """Build a card showing the model switch result.
    
    Args:
        model_name: The selected model name
        thinking: Thinking mode state
        success: Whether the switch was successful
        
    Returns:
        Card JSON
    """
    thinking_text = "开启" if thinking else "关闭"
    
    if success:
        template: CardColor = "green"
        icon = "✅"
        status = "切换成功"
        detail = f"已切换到 `{model_name}`，Thinking 模式: {thinking_text}"
        note = "请发送 /new 创建新会话后生效"
    else:
        template = "red"
        icon = "❌"
        status = "切换失败"
        detail = "切换模型时出错，请重试"
        note = None
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": _plain_text_element(detail),
        },
    ]
    
    if note:
        elements.append({
            "tag": "note",
            "elements": [
                _plain_text_element(f"💡 {note}")
            ],
        })
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {
                "tag": "plain_text",
                "content": f"{icon} {status}",
            },
        },
        "elements": elements,
    }
