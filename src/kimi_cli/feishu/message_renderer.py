"""Message renderer for converting Wire messages to Feishu cards.

This module handles the conversion of internal Wire messages (thoughts, tool calls,
tool results, etc.) to Feishu interactive cards.
"""

from __future__ import annotations

import json
import time
from typing import Any

from kosong.message import TextPart, ThinkPart, ToolCall, ToolCallPart
from kosong.tooling import ToolResult, ToolReturnValue

from kimi_cli.feishu.card_builder import (
    build_error_card,
    build_response_card,
    build_search_result_card,
    build_status_card,
    build_thought_card,
    build_tool_call_card,
    build_tool_result_card,
)


class MessageRenderer:
    """Renders Wire messages as Feishu cards."""
    
    def __init__(self):
        self._tool_start_times: dict[str, float] = {}
    
    def on_tool_call_start(self, tool_call_id: str) -> None:
        """Record when a tool call starts (for timing)."""
        self._tool_start_times[tool_call_id] = time.time()
    
    def get_execution_time(self, tool_call_id: str) -> float | None:
        """Get execution time for a tool call."""
        start_time = self._tool_start_times.pop(tool_call_id, None)
        if start_time:
            return time.time() - start_time
        return None
    
    def render_thought(self, thought: str) -> dict[str, Any]:
        """Render a thought/reasoning as a card.
        
        Args:
            thought: The thought content
            
        Returns:
            Card JSON
        """
        return build_thought_card(thought, is_collapsed=len(thought) > 200)
    
    def render_think_part(self, think_part: ThinkPart) -> dict[str, Any]:
        """Render a ThinkPart as a card.
        
        Args:
            think_part: The think part from LLM
            
        Returns:
            Card JSON
        """
        return build_thought_card(think_part.think, is_collapsed=True)
    
    def render_tool_call(
        self,
        tool_call: ToolCall | ToolCallPart | None = None,
        *,
        tool_name: str | None = None,
        arguments: dict[str, Any] | str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Render a tool call as a card.
        
        Args:
            tool_call: The tool call (optional, for backward compatibility)
            tool_name: Tool name (alternative to tool_call)
            arguments: Tool arguments (alternative to tool_call)
            tool_call_id: Tool call ID (optional)
            
        Returns:
            Card JSON
        """
        # Extract info from tool_call if provided
        if tool_call is not None:
            # Record start time for execution tracking
            self.on_tool_call_start(tool_call.id)
            
            # Extract tool name and arguments
            if isinstance(tool_call, ToolCall):
                tool_name = tool_call.function.name
                arguments = tool_call.function.arguments
            else:  # ToolCallPart
                tool_name = tool_call.name
                arguments = tool_call.arguments
            
            tool_call_id = tool_call.id
        else:
            # Use provided parameters
            if tool_name is None:
                tool_name = "unknown"
            if tool_call_id is not None:
                self.on_tool_call_start(tool_call_id)
        
        # Try to parse arguments as JSON for better display
        if isinstance(arguments, str):
            try:
                args_dict = json.loads(arguments)
            except json.JSONDecodeError:
                args_dict = {"arguments": arguments}
        elif arguments is None:
            args_dict = {}
        else:
            args_dict = arguments
        
        return build_tool_call_card(tool_name, args_dict, tool_call_id=tool_call_id)
    
    def render_tool_result(
        self,
        tool_call_id: str,
        result: ToolReturnValue,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        """Render a tool result as a card.
        
        Args:
            tool_call_id: The tool call ID
            result: The tool return value
            tool_name: Optional tool name
            
        Returns:
            Card JSON
        """
        execution_time = self.get_execution_time(tool_call_id)
        tool_name = tool_name or "未知工具"
        
        # Check if result is an error
        is_error = isinstance(result, dict) and (
            result.get("error") is not None or
            result.get("type") == "error"
        )
        
        # Extract the actual result content
        if isinstance(result, dict):
            if "result" in result:
                display_result = result["result"]
            elif "content" in result:
                display_result = result["content"]
            elif "output" in result:
                display_result = result["output"]
            elif "error" in result:
                display_result = result["error"]
                is_error = True
            else:
                display_result = result
        else:
            display_result = result
        
        # Check if this is a search result that needs special formatting
        if not is_error and self._is_search_result(tool_name, display_result):
            return self._render_search_result(tool_name, display_result, execution_time)
        
        return build_tool_result_card(
            tool_name=tool_name,
            result=display_result,
            is_error=is_error,
            execution_time=execution_time,
        )
    
    def _is_search_result(self, tool_name: str, result: Any) -> bool:
        """Check if a result is a search result that should use search card."""
        search_tools = ["web_search", "search", "bing_search", "google_search", "file_search"]
        if tool_name.lower() not in search_tools and not any(
            s in tool_name.lower() for s in search_tools
        ):
            return False
        
        # Check if result has search-like structure
        if isinstance(result, dict):
            # Has results list
            if "results" in result or "hits" in result or "items" in result:
                return True
            # Has web search structure
            if any(k in result for k in ["organic", "webPages", "web_pages"]):
                return True
        elif isinstance(result, list) and len(result) > 0:
            # List of search results
            first = result[0]
            if isinstance(first, dict):
                if any(k in first for k in ["title", "url", "link", "snippet", "content"]):
                    return True
        
        return False
    
    def _render_search_result(
        self,
        tool_name: str,
        result: Any,
        execution_time: float | None = None,
    ) -> dict[str, Any]:
        """Render search results as a specialized card."""
        # Extract search results
        results: list[dict[str, str]] = []
        total_count: int | None = None
        query: str = ""
        
        # Determine search type
        search_type = "web" if "file" not in tool_name.lower() else "file"
        
        if isinstance(result, dict):
            # Try to extract query
            query = result.get("query", result.get("q", ""))
            
            # Try to extract results list
            if "results" in result:
                raw_results = result["results"]
            elif "hits" in result:
                raw_results = result["hits"]
            elif "items" in result:
                raw_results = result["items"]
            elif "organic" in result:
                raw_results = result["organic"]
            elif "webPages" in result:
                raw_results = result["webPages"].get("value", [])
                total_count = result["webPages"].get("totalEstimatedMatches")
            elif "web_pages" in result:
                raw_results = result["web_pages"]
            else:
                raw_results = []
            
            # Try to extract total count
            if total_count is None:
                total_count = result.get("total", result.get("totalResults", result.get("count")))
            
        elif isinstance(result, list):
            raw_results = result
        else:
            raw_results = []
        
        # Normalize results
        for item in raw_results[:10]:  # Max 10 results
            if isinstance(item, dict):
                title = (
                    item.get("title", "")
                    or item.get("name", "")
                    or "无标题"
                )
                url = (
                    item.get("url", "")
                    or item.get("link", "")
                    or item.get("href", "")
                    or ""
                )
                snippet = (
                    item.get("snippet", "")
                    or item.get("description", "")
                    or item.get("content", "")
                    or item.get("summary", "")
                    or ""
                )
                
                if title or snippet:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                    })
        
        return build_search_result_card(
            query=query or f"搜索 ({tool_name})",
            results=results,
            total_count=total_count,
            search_type=search_type,
        )
    
    def render_text_response(self, text: str) -> dict[str, Any]:
        """Render a text response as a card.
        
        Args:
            text: The response text
            
        Returns:
            Card JSON
        """
        return build_response_card(text, is_markdown=True)
    
    def render_text_part(self, text_part: TextPart) -> dict[str, Any]:
        """Render a TextPart as a card.
        
        Args:
            text_part: The text part
            
        Returns:
            Card JSON
        """
        return build_response_card(text_part.text, is_markdown=True)
    
    def render_error(
        self,
        error_message: str,
        error_type: str = "Error",
        suggestion: str | None = None,
    ) -> dict[str, Any]:
        """Render an error as a card.
        
        Args:
            error_message: Error description
            error_type: Type of error
            suggestion: Optional suggestion
            
        Returns:
            Card JSON
        """
        return build_error_card(error_message, error_type, suggestion)
    
    def render_status(self, status: str, detail: str | None = None, is_loading: bool = False) -> dict[str, Any]:
        """Render a status message as a card.
        
        Args:
            status: Status text
            detail: Optional detail
            is_loading: Whether loading
            
        Returns:
            Card JSON
        """
        return build_status_card(status, detail, is_loading)


def create_renderer() -> MessageRenderer:
    """Create a new message renderer instance."""
    return MessageRenderer()
