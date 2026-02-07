"""Feishu interactive card builder for beautifying messages.

This module provides various card templates for different message types:
- Thought cards for AI reasoning
- Tool call cards for function invocations
- Tool result cards for execution results
- Search result cards for web/file search results
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypeAlias

# Card color schemes matching Feishu design
CardColor: TypeAlias = Literal["blue", "green", "red", "orange", "purple", "grey", "default"]

CARD_COLORS: dict[CardColor, dict[str, str]] = {
    "blue": {"bg": "#E8F1FF", "border": "#3370FF", "text": "#3370FF", "icon": "🔵"},
    "green": {"bg": "#E8F8F0", "border": "#34D399", "text": "#059669", "icon": "🟢"},
    "red": {"bg": "#FEE2E2", "border": "#F87171", "text": "#DC2626", "icon": "🔴"},
    "orange": {"bg": "#FFF3E0", "border": "#FB923C", "text": "#EA580C", "icon": "🟠"},
    "purple": {"bg": "#F3E8FF", "border": "#A855F7", "text": "#7C3AED", "icon": "🟣"},
    "grey": {"bg": "#F5F5F5", "border": "#9CA3AF", "text": "#6B7280", "icon": "⚪"},
    "default": {"bg": "#FFFFFF", "border": "#E5E7EB", "text": "#374151", "icon": "⚪"},
}


def _truncate_text(text: str, max_length: int = 500) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def _create_header(title: str, color: CardColor = "default") -> dict[str, Any]:
    """Create card header element."""
    colors = CARD_COLORS[color]
    return {
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": f"{colors['icon']} {title}",
        },
        "icon": {
            "tag": "standard_icon",
            "token": "robot_outlined" if color == "blue" else "info_outlined",
        },
        "padding": "12px 16px",
        "background_style": colors["bg"],
        "border_radius": {"top_left": 8, "top_right": 8, "bottom_left": 0, "bottom_right": 0},
    }


def _create_code_block(content: str, language: str = "json") -> dict[str, Any]:
    """Create a code block element."""
    # Truncate if too long
    display_content = _truncate_text(content, 2000)
    return {
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": f"```{language}\n{display_content}\n```",
        },
        "padding": "8px 12px",
        "background_style": "#1F2937",
        "border_radius": 6,
    }


def _create_text_section(content: str, is_markdown: bool = False) -> dict[str, Any]:
    """Create a text section element."""
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md" if is_markdown else "plain_text",
            "content": _truncate_text(content, 3000),
        },
        "padding": "8px 0",
    }


def _create_divider() -> dict[str, Any]:
    """Create a divider element."""
    return {"tag": "hr"}


def build_thought_card(thought: str, is_collapsed: bool = True) -> dict[str, Any]:
    """Build a card for AI thought/reasoning.
    
    Args:
        thought: The thought content
        is_collapsed: Whether to collapse the content initially
        
    Returns:
        Interactive card JSON
    """
    colors = CARD_COLORS["grey"]
    truncated = _truncate_text(thought, 800)
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "🧠 思考过程",
            },
            "icon": {
                "tag": "standard_icon",
                "token": "brain_outlined",
            },
            "padding": "12px 16px",
            "background_style": colors["bg"],
        },
    ]
    
    if is_collapsed and len(thought) > 200:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": truncated[:200] + "...",
            },
            "padding": "8px 16px",
        })
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "展开完整思考",
                    },
                    "type": "primary",
                    "value": {"action": "expand_thought"},
                }
            ],
            "padding": "8px 16px",
        })
    else:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": thought,
            },
            "padding": "8px 16px",
        })
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": elements,
            "border": {
                "style": {"color": colors["border"], "width": 1},
                "radius": {"top_left": 8, "top_right": 8, "bottom_left": 8, "bottom_right": 8},
            },
        },
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
    colors = CARD_COLORS["blue"]
    
    # Format arguments
    if isinstance(arguments, dict):
        args_str = json.dumps(arguments, indent=2, ensure_ascii=False)
    else:
        try:
            args_dict = json.loads(arguments)
            args_str = json.dumps(args_dict, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            args_str = str(arguments)
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"🔧 调用工具: {tool_name}",
            },
            "icon": {
                "tag": "standard_icon",
                "token": "tool_outlined",
            },
            "padding": "12px 16px",
            "background_style": colors["bg"],
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "📋 参数:",
            },
            "padding": "8px 16px 0 16px",
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"```json\n{_truncate_text(args_str, 1500)}\n```",
            },
            "padding": "0 16px 12px 16px",
        },
    ]
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": elements,
            "border": {
                "style": {"color": colors["border"], "width": 1},
                "radius": {"top_left": 8, "top_right": 8, "bottom_left": 8, "bottom_right": 8},
            },
        },
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
    color: CardColor = "red" if is_error else "green"
    colors = CARD_COLORS[color]
    status_icon = "❌" if is_error else "✅"
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
    
    # Header with execution time
    header_text = f"{status_icon} {status_text} - {tool_name}"
    if execution_time is not None:
        header_text += f" ({execution_time:.2f}s)"
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": header_text,
            },
            "icon": {
                "tag": "standard_icon",
                "token": "error_outlined" if is_error else "check_circle_outlined",
            },
            "padding": "12px 16px",
            "background_style": colors["bg"],
        },
    ]
    
    # Add result content (collapsible for large results)
    if len(result_str) > 500:
        elements.extend([
            {
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": _truncate_text(result_str, 500),
                },
                "padding": "8px 16px",
            },
            {
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": f"... ({len(result_str) - 500} 字符已折叠)",
                },
                "text_align": "center",
                "padding": "4px 16px",
            },
        ])
    else:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"```\n{result_str}\n```" if "\n" in result_str else result_str,
            },
            "padding": "8px 16px",
        })
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": elements,
            "border": {
                "style": {"color": colors["border"], "width": 1},
                "radius": {"top_left": 8, "top_right": 8, "bottom_left": 8, "bottom_right": 8},
            },
        },
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
    colors = CARD_COLORS["purple"]
    type_icon = "🌐" if search_type == "web" else "📁"
    type_text = "网页搜索" if search_type == "web" else "文件搜索"
    
    count_text = f"共 {total_count} 条" if total_count else f"{len(results)} 条结果"
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"{type_icon} {type_text} - {count_text}",
            },
            "icon": {
                "tag": "standard_icon",
                "token": "search_outlined",
            },
            "padding": "12px 16px",
            "background_style": colors["bg"],
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"🔍 查询: {query}",
            },
            "padding": "4px 16px 8px 16px",
        },
    ]
    
    # Add each result
    for i, result in enumerate(results[:10], 1):  # Max 10 results
        title = result.get("title", "无标题")
        url = result.get("url", "")
        snippet = result.get("snippet", result.get("content", ""))
        
        # Result header with number and title
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{i}.** [{title}]({url})" if url else f"**{i}.** {title}",
            },
            "padding": "8px 16px 0 16px",
        })
        
        # Snippet
        if snippet:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": _truncate_text(snippet, 200),
                },
                "padding": "2px 16px 0 24px",
            })
        
        # Add divider between results (except last)
        if i < len(results[:10]):
            elements.append({
                "tag": "hr",
                "margin": "8px 16px",
            })
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": elements,
            "border": {
                "style": {"color": colors["border"], "width": 1},
                "radius": {"top_left": 8, "top_right": 8, "bottom_left": 8, "bottom_right": 8},
            },
        },
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
        "schema": "2.0",
        "config": {"width_mode": "default"},
        "body": {
            "direction": "vertical",
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md" if is_markdown else "plain_text",
                        "content": content,
                    },
                    "padding": "12px 16px",
                },
            ],
        },
    }


def build_multi_element_card(
    elements: list[dict[str, Any]],
    title: str | None = None,
    color: CardColor = "default",
) -> dict[str, Any]:
    """Build a card with multiple elements.
    
    Useful for combining multiple cards into one message.
    
    Args:
        elements: List of pre-built card elements
        title: Optional card title
        color: Color theme
        
    Returns:
        Interactive card JSON
    """
    colors = CARD_COLORS[color]
    
    card_elements: list[dict[str, Any]] = []
    
    if title:
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": title,
            },
            "padding": "12px 16px",
            "background_style": colors["bg"],
        })
    
    card_elements.extend(elements)
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": card_elements,
            "border": {
                "style": {"color": colors["border"], "width": 1},
                "radius": {"top_left": 8, "top_right": 8, "bottom_left": 8, "bottom_right": 8},
            },
        },
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
    colors = CARD_COLORS["red"]
    
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"❌ {error_type}",
            },
            "icon": {
                "tag": "standard_icon",
                "token": "error_outlined",
            },
            "padding": "12px 16px",
            "background_style": colors["bg"],
        },
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": error_message,
            },
            "padding": "8px 16px",
        },
    ]
    
    if suggestion:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"💡 建议: {suggestion}",
            },
            "padding": "8px 16px 12px 16px",
        })
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": elements,
            "border": {
                "style": {"color": colors["border"], "width": 1},
                "radius": {"top_left": 8, "top_right": 8, "bottom_left": 8, "bottom_right": 8},
            },
        },
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
            "text": {
                "tag": "plain_text",
                "content": f"{icon} {status}",
            },
            "padding": "12px 16px",
        },
    ]
    
    if detail:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": detail,
            },
            "padding": "4px 16px 12px 16px",
        })
    
    return {
        "schema": "2.0",
        "config": {"width_mode": "compact"},
        "body": {
            "direction": "vertical",
            "elements": elements,
        },
    }
