"""Feishu SDK server using long connection (WebSocket) for events.

This module provides a server that uses the official Feishu SDK's
long connection feature to receive events without needing a public webhook URL.

Usage:
    1. No webhook URL configuration needed
    2. No tunnel/穿透 tools required
    3. Events are received via WebSocket directly from Feishu
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any

import lark_oapi as lark
from loguru import logger

from kimi_cli.feishu.config import FeishuAccountConfig, FeishuConfig
from kimi_cli.feishu.sdk_client import FeishuSDKClient
# Gateway removed - using SDK long connection only
from kimi_cli.soul.agent import Runtime, load_agent
from kimi_cli.soul.context import Context
from kimi_cli.soul.kimisoul import KimiSoul
from kimi_cli.config import load_config
from kimi_cli.session import Session
from kimi_cli.agentspec import DEFAULT_AGENT_FILE
from kaos.path import KaosPath
from pydantic import SecretStr


class SDKChatSession:
    """A chat session with a user using SDK client.
    
    Each session has its own KimiSoul with isolated context.
    """
    
    def __init__(
        self,
        chat_id: str,
        user_id: str,
        client: FeishuSDKClient,
        config: FeishuAccountConfig,
        soul: KimiSoul,
    ):
        self.chat_id = chat_id
        self.user_id = user_id
        self.client = client
        self.config = config
        self.soul = soul
        
        self._message_id: str | None = None
        self._card: Any = None  # StreamingCard
        self._lock = asyncio.Lock()
        self._running = False
        self._cancel_event: asyncio.Event | None = None
        self._tool_call_idx = 0
        self._tool_call_map: dict[str, int] = {}
        
        # Register Feishu tools for this session
        self._register_feishu_tools()
    
    def _register_feishu_tools(self) -> None:
        """Register Feishu tools with the soul's toolset."""
        try:
            from kimi_cli.tools.feishu import set_feishu_client
            from kimi_cli.tools.feishu import FeishuSendFile, FeishuSendMessage
            
            # Set the global client reference for tools
            set_feishu_client(self.client)
            
            # Add tools to soul's toolset if not already present
            # Access toolset through soul's agent
            if hasattr(self.soul, '_agent'):
                toolset = self.soul._agent.toolset
                if not toolset.find("FeishuSendFile"):
                    toolset.add(FeishuSendFile())
                    logger.info(f"[SESSION] Registered FeishuSendFile tool for chat {self.chat_id}")
                if not toolset.find("FeishuSendMessage"):
                    toolset.add(FeishuSendMessage())
                    logger.info(f"[SESSION] Registered FeishuSendMessage tool for chat {self.chat_id}")
        except Exception as e:
            logger.warning(f"[SESSION] Failed to register Feishu tools: {e}")
    
    async def handle_message(self, message_text: str) -> None:
        """Handle an incoming message."""
        print(f"[SESSION] Handling message: {message_text[:50]}...")
        logger.info(f"[SESSION] Handling message: {message_text[:50]}...")
        
        # Set current chat ID for tool calls
        self.client.set_current_chat_id(self.chat_id)
        
        # Handle /clear command first (before lock) to allow interruption
        stripped = message_text.strip()
        if stripped == "/clear":
            logger.info("[SESSION] /clear command (pre-lock)")
            await self._handle_clear()
            return
        
        async with self._lock:
            if self._running:
                logger.info("[SESSION] Already processing, sending busy message")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "⏳ 我正在处理上一条消息，请稍候...",
                )
                return
            
            self._running = True
            self._cancel_event = asyncio.Event()
        
        try:
            # Handle local commands
            if stripped == "/help":
                logger.info("[SESSION] /help command")
                await self._send_help()
                return
            elif stripped == "/reset":
                logger.info("[SESSION] /reset command")
                await self._send_reset()
                return
            elif stripped == "/mcp" or stripped.startswith("/mcp "):
                logger.info("[SESSION] /mcp command")
                await self._handle_mcp_command(stripped)
                return
            
            # All other slash commands are passed through to KimiSoul
            if stripped.startswith("/"):
                logger.info(f"[SESSION] Slash command: {stripped[:50]}")
            
            # Process the message (including slash commands)
            logger.info(f"[SESSION] Processing message: {message_text[:100]}")
            await self._process_message(message_text)
            
        except Exception as e:
            logger.exception(f"[SESSION] Error handling message: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 处理消息时出错: {str(e)[:100]}",
            )
        finally:
            async with self._lock:
                self._running = False
                self._cancel_event = None
    
    async def _send_help(self) -> None:
        """Send help message."""
        help_text = """👋 **Kimi Code CLI 帮助**

**本地命令：**
• /help - 显示此帮助
• /reset - 重置对话
• /clear - 中断当前处理并清空上下文
• /mcp - 显示 MCP 服务器状态

**Soul 命令 (由KimiSoul处理)：**
• /compact - 压缩上下文
• /yolo - 切换自动批准模式
• /init - 生成 AGENTS.md
• ... 以及其他 Soul 级别命令

**不支持的命令：**
• /model - 请使用 --model 参数启动
• /skill - 请使用 --skills-dir 参数启动

**文件传输：**
• 📥 发送文件给我 - 我会保存到当前目录
• 📤 让我发送文件 - 直接说"把xxx文件发给我"

**工具调用：**
• Kimi 可以使用 `FeishuSendFile` 工具发送文件
• Kimi 可以使用 `FeishuSendMessage` 工具发送消息

我可以帮你：
• 编写和调试代码
• 分析项目结构
• 运行命令和工具
• 回答技术问题

直接发送消息开始对话！"""
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            help_text,
        )
    
    async def _send_reset(self) -> None:
        """Send reset confirmation."""
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            "🔄 对话已重置。让我们重新开始！",
        )
    
    async def _handle_clear(self) -> None:
        """Handle /clear command: cancel current operation and clear context."""
        # Check if there's a running operation
        was_running = False
        async with self._lock:
            if self._running and self._cancel_event:
                was_running = True
                logger.info("[SESSION] Cancelling current operation due to /clear")
                # Set the cancel event to stop the current operation
                self._cancel_event.set()
        
        # Wait a bit for the operation to cancel
        if was_running:
            await asyncio.sleep(0.5)
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                "⏹️ 已中断当前处理，正在清空上下文...",
            )
        
        # Clear the soul's context
        try:
            if self.soul and hasattr(self.soul, 'context'):
                await self.soul.context.clear()
                logger.info("[SESSION] Context cleared")
        except Exception as e:
            logger.warning(f"[SESSION] Failed to clear context: {e}")
        
        # Send confirmation
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            "🧹 上下文已清空。可以开始新的对话了！",
        )
    
    async def _handle_mcp_command(self, command: str) -> None:
        """Handle /mcp command to show MCP server status."""
        from kimi_cli.soul.toolset import KimiToolset
        
        try:
            toolset = self.soul.agent.toolset
            if not isinstance(toolset, KimiToolset):
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "❌ MCP 功能不可用：工具集类型不匹配",
                )
                return
            
            servers = toolset.mcp_servers
            
            if not servers:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "📭 没有配置 MCP 服务器\n\n使用 `kimi mcp add` 命令添加服务器",
                )
                return
            
            n_conn = sum(1 for s in servers.values() if s.status == "connected")
            n_tools = sum(len(s.tools) for s in servers.values())
            
            lines = [f"🔌 **MCP 服务器** ({n_conn}/{len(servers)} 已连接, {n_tools} 工具)", ""]
            
            status_emoji = {
                "connected": "🟢",
                "connecting": "🔵",
                "pending": "🟡",
                "failed": "🔴",
                "unauthorized": "🔴",
            }
            
            for name, info in servers.items():
                emoji = status_emoji.get(info.status, "⚪")
                status_text = info.status
                if info.status == "unauthorized":
                    status_text += " (需授权: kimi mcp auth {})"
                lines.append(f"{emoji} **{name}** - {status_text}")
                
                for tool in info.tools:
                    lines.append(f"   • {tool.name}")
            
            lines.append("")
            lines.append("💡 **提示**：使用 `kimi mcp` 命令管理服务器")
            
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                "\n".join(lines),
            )
            
        except Exception as e:
            logger.exception(f"Error handling /mcp command: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 获取 MCP 状态失败: {str(e)[:100]}",
            )
    
    async def _process_message(self, message_text: str) -> None:
        """Process a user message through the soul - multi-part text output mode."""
        import os
        from kimi_cli.soul import run_soul
        from kimi_cli.wire import Wire
        from contextlib import asynccontextmanager
        
        print(f"[_process_message] Starting with text: {message_text[:100]}")
        logger.info(f"[_process_message] Starting with text: {message_text[:100]}")
        
        # Run the soul with wire (messages sent in real-time)
        self._current_thinking_buffer: list[str] = []
        self._current_text_buffer: list[str] = []
        
        # Switch to session's work_dir for file operations
        original_cwd = os.getcwd()
        work_dir = None
        try:
            # Get work_dir from soul's runtime session
            if hasattr(self.soul, '_runtime') and self.soul._runtime.session:
                work_dir = str(self.soul._runtime.session.work_dir)
                if os.path.isdir(work_dir):
                    os.chdir(work_dir)
                    print(f"[_process_message] Switched to work_dir: {work_dir}")
                    logger.info(f"Switched to work_dir: {work_dir}")
        except Exception as e:
            logger.warning(f"Failed to switch work_dir: {e}")
        
        try:
            print("[_process_message] Starting run_soul...")
            
            # Use refreshing context manager to auto-refresh OAuth token during long operations
            if hasattr(self.soul, '_runtime') and self.soul._runtime.oauth:
                async with self.soul._runtime.oauth.refreshing(self.soul._runtime):
                    await run_soul(
                        self.soul,
                        message_text,
                        self._wire_loop_text_parts,
                        self._cancel_event,
                    )
            else:
                await run_soul(
                    self.soul,
                    message_text,
                    self._wire_loop_text_parts,
                    self._cancel_event,
                )
            print("[_process_message] run_soul completed successfully")
            
            # Flush any remaining content
            print("[_process_message] Flushing buffers after run_soul...")
            await self._flush_text_buffers()
            print("[_process_message] Buffers flushed")
            
            # Send completion indicator
            print("[_process_message] Sending completion message...")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                "✅ 回复完成",
            )
            print("[_process_message] Completion message sent")
                
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            print(f"[_process_message ERROR] {error_type}: {error_msg}")
            logger.exception(f"[_process_message] Error: {e}")
            
            # Flush any remaining content before error message
            await self._flush_text_buffers()
            
            # Send user-friendly error message
            user_friendly_msg = self._get_user_friendly_error(error_type, error_msg)
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                user_friendly_msg,
            )
        finally:
            # Restore original working directory
            try:
                os.chdir(original_cwd)
                print(f"[_process_message] Restored cwd: {original_cwd}")
            except Exception as e:
                logger.warning(f"Failed to restore cwd: {e}")
    
    def _get_user_friendly_error(self, error_type: str, error_msg: str) -> str:
        """Convert technical error to user-friendly message."""
        # Token limit errors
        if "token limit" in error_msg.lower() or "token" in error_msg.lower() and "exceed" in error_msg.lower():
            import re
            match = re.search(r'(\d+)\s*\(requested:\s*(\d+)\)', error_msg)
            if match:
                limit = match.group(1)
                requested = match.group(2)
                return (
                    f"❌ **请求超出模型 token 限制**\n"
                    f"模型限制: {limit}\n"
                    f"您的请求: {requested}\n\n"
                    f"💡 **建议**：\n"
                    f"1. 使用 `/compact` 压缩上下文\n"
                    f"2. 使用 `/clear` 清空对话重新开始\n"
                    f"3. 将大文件分批处理"
                )
            return (
                "❌ **请求超出模型 token 限制**\n\n"
                "💡 **建议**：使用 `/compact` 压缩上下文或 `/clear` 清空对话"
            )
        
        # API errors
        if "APIStatusError" in error_type or "400" in error_msg or "401" in error_msg or "403" in error_msg or "429" in error_msg:
            return f"❌ **API 错误**: {error_msg[:150]}\n\n请检查 API 密钥和网络连接。"
        
        # Network errors
        if "connection" in error_msg.lower() or "timeout" in error_msg.lower() or "network" in error_msg.lower():
            return f"❌ **网络错误**: 连接失败或超时\n\n请检查网络连接后重试。"
        
        # Default error
        return f"❌ **处理出错**: {error_msg[:200]}\n\n请重试或联系支持。"
    
    async def _wire_loop(self, wire: Wire) -> None:
        """Process wire messages during soul execution."""
        from kimi_cli.wire.types import (
            ApprovalRequest, ImageURLPart, TextPart, ThinkPart, ToolCall, ToolCallPart, ToolResult,
            TurnBegin, TurnEnd, StepBegin, StepInterrupted, SubagentEvent
        )
        
        wire_ui = wire.ui_side(merge=False)
        
        current_step = 0
        assistant_content: list[str] = []
        
        print(f"[_wire_loop] Starting wire loop...")
        
        try:
            while True:
                print(f"[_wire_loop] Waiting for message...")
                msg = await wire_ui.receive()
                print(f"[_wire_loop] Received message: {type(msg).__name__}")
                
                if isinstance(msg, TurnBegin):
                    print(f"[_wire_loop] TurnBegin received")
                    assistant_content = []
                    
                elif isinstance(msg, StepBegin):
                    current_step = msg.n
                    
                elif isinstance(msg, StepInterrupted):
                    print(f"[_wire_loop] StepInterrupted received")
                    
                elif isinstance(msg, TurnEnd):
                    print(f"[_wire_loop] TurnEnd received, final content: {''.join(assistant_content)[:100]}...")
                    
                elif isinstance(msg, TextPart):
                    print(f"[_wire_loop] TextPart received: {msg.text[:50]}...")
                    assistant_content.append(msg.text)
                    if self._card and assistant_content:
                        full_text = "".join(assistant_content)
                        print(f"[_wire_loop] Rebuilding card with: {full_text[:50]}...")
                        self._rebuild_card_with_content(full_text)
                        await self._update_card()
                        print("[_wire_loop] Card updated")
                        
                elif isinstance(msg, ThinkPart):
                    print(f"[_wire_loop] ThinkPart received: {msg.think[:50] if msg.think else 'N/A'}...")
                    if self._card:
                        # Always add thinking to card for display
                        self._card.add_thinking(msg.think)
                        # Also treat thinking as content for display
                        if msg.think:
                            assistant_content.append(msg.think)
                            full_text = "".join(assistant_content)
                            print(f"[_wire_loop] Rebuilding card with thinking: {full_text[:50]}...")
                            self._rebuild_card_with_content(full_text)
                        await self._update_card()
                        print("[_wire_loop] Thinking added to card")
                        
                elif isinstance(msg, ToolCall):
                    # Only process complete ToolCall, skip ToolCallPart fragments
                    if self._card and self.config.show_tool_calls:
                        func_name = msg.function.name if hasattr(msg.function, 'name') else 'unknown'
                        func_args = msg.function.arguments if hasattr(msg.function, 'arguments') else '{}'
                        idx = self._card.start_tool_call(func_name, func_args)
                        if hasattr(msg, 'id'):
                            self._tool_call_map[msg.id] = idx
                        await self._update_card()
                        
                elif isinstance(msg, ToolResult):
                    if self._card and self.config.show_tool_calls:
                        idx = self._tool_call_map.get(msg.tool_call_id)
                        if idx is not None:
                            status = "success"
                            result_text = ""
                            if hasattr(msg.return_value, 'brief'):
                                result_text = str(msg.return_value.brief)
                            elif hasattr(msg.return_value, 'message'):
                                result_text = str(msg.return_value.message)
                            else:
                                result_text = str(msg.return_value)[:200]
                            
                            self._card.update_tool_call(idx, status, result_text)
                            await self._update_card()
                            
                elif isinstance(msg, SubagentEvent):
                    # Handle subagent events from Task tool
                    print(f"[_wire_loop] SubagentEvent received: {type(msg.event).__name__}")
                    
                    subagent_msg = msg.event
                    
                    if isinstance(subagent_msg, TextPart):
                        if subagent_msg.text:
                            print(f"[_wire_loop] Subagent text: {subagent_msg.text[:50]}...")
                            assistant_content.append(subagent_msg.text)
                            if self._card:
                                full_text = "".join(assistant_content)
                                self._rebuild_card_with_content(full_text)
                                await self._update_card()
                    elif isinstance(subagent_msg, ThinkPart):
                        if subagent_msg.think:
                            print(f"[_wire_loop] Subagent thinking: {subagent_msg.think[:50] if subagent_msg.think else 'N/A'}...")
                            if self._card:
                                self._card.add_thinking(subagent_msg.think)
                                assistant_content.append(subagent_msg.think)
                                full_text = "".join(assistant_content)
                                self._rebuild_card_with_content(full_text)
                                await self._update_card()
                    elif isinstance(subagent_msg, ToolCall):
                        if self._card and self.config.show_tool_calls:
                            func_name = subagent_msg.function.name if hasattr(subagent_msg.function, 'name') else 'unknown'
                            func_args = subagent_msg.function.arguments if hasattr(subagent_msg.function, 'arguments') else '{}'
                            idx = self._card.start_tool_call(f"[Subagent] {func_name}", func_args)
                            if hasattr(subagent_msg, 'id'):
                                self._tool_call_map[subagent_msg.id] = idx
                            await self._update_card()
                    elif isinstance(subagent_msg, ToolResult):
                        if self._card and self.config.show_tool_calls:
                            idx = self._tool_call_map.get(subagent_msg.tool_call_id)
                            if idx is not None:
                                status = "success"
                                result_text = ""
                                if hasattr(subagent_msg.return_value, 'brief'):
                                    result_text = str(subagent_msg.return_value.brief)
                                elif hasattr(subagent_msg.return_value, 'message'):
                                    result_text = str(subagent_msg.return_value.message)
                                else:
                                    result_text = str(subagent_msg.return_value)[:200]
                                
                                self._card.update_tool_call(idx, status, f"[Subagent] {result_text}")
                                await self._update_card()
                    else:
                        print(f"[_wire_loop] Unhandled subagent event: {type(subagent_msg).__name__}")
                
                elif isinstance(msg, ApprovalRequest):
                    if self.config.auto_approve:
                        msg.resolve("approve")
                    else:
                        msg.resolve("approve")
                        
        except Exception as e:
            error_msg = str(e)
            print(f"[_wire_loop] Exception: {e}")
            logger.exception("Error in wire loop:")
            
            # Send error message to user for serious errors
            if "token limit" in error_msg.lower() or "exceeded" in error_msg.lower():
                error_info = self._format_error_for_user(type(e).__name__, error_msg)
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    f"❌ [处理中断] {error_info}",
                )
            # Don't re-raise - wire loop ending is normal
    
    def _rebuild_card_with_content(self, content: str) -> None:
        """Rebuild the card with updated assistant content."""
        from kimi_cli.feishu.card import StreamingCard
        
        if not self._card:
            return
        
        old_card = self._card
        new_card = StreamingCard("Kimi Code CLI")
        
        # Preserve user input
        if old_card.user_input:
            new_card.add_user_input(old_card.user_input)
        
        # Preserve thinking content
        if old_card.thinking_content:
            new_card.add_thinking(old_card.thinking_content)
        
        # Preserve tool calls
        for tool in old_card.tool_calls:
            idx = new_card.start_tool_call(tool["name"], tool.get("arguments", ""))
            new_card.update_tool_call(idx, tool["status"], tool.get("result"))
        
        # Add new assistant message
        new_card.add_assistant_message(content)
        new_card.set_status(old_card.status)
        
        self._card = new_card
    
    async def _flush_text_buffers(self) -> None:
        """Flush remaining text buffers."""
        import re
        
        # Send thinking buffer
        if hasattr(self, '_current_thinking_buffer') and self._current_thinking_buffer:
            content = "".join(self._current_thinking_buffer).strip()
            if content:
                print(f"[_flush_text_buffers] Sending final thinking: {len(content)} chars")
                msg_id = await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    f"💭 [思考过程]\n{content[:2000]}",
                )
                print(f"[_flush_text_buffers] Final thinking sent: {msg_id}")
            self._current_thinking_buffer = []
        
        # Send text buffer
        if hasattr(self, '_current_text_buffer') and self._current_text_buffer:
            content = "".join(self._current_text_buffer).strip()
            if content:
                # Check for file upload markers
                file_pattern = r'\[SEND_FILE:(.+?)\]'
                image_pattern = r'\[SEND_IMAGE:(.+?)\]'
                file_matches = re.findall(file_pattern, content)
                image_matches = re.findall(image_pattern, content)
                
                if file_matches or image_matches:
                    # Remove markers from content
                    content = re.sub(file_pattern, '', content).strip()
                    content = re.sub(image_pattern, '', content).strip()
                    if content:
                        print(f"[_flush_text_buffers] Sending text: {len(content)} chars")
                        msg_id = await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            f"🤖 [回复内容]\n{content[:2000]}",
                        )
                        print(f"[_flush_text_buffers] Final text sent: {msg_id}")
                    
                    # Upload files
                    for file_path in file_matches:
                        await self._upload_and_send_file(file_path)
                    
                    # Upload images
                    for image_path in image_matches:
                        await self._upload_and_send_image(image_path)
                else:
                    print(f"[_flush_text_buffers] Sending text: {len(content)} chars")
                    msg_id = await asyncio.to_thread(
                        self.client.send_text_message,
                        self.chat_id,
                        f"🤖 [回复内容]\n{content[:2000]}",
                    )
                    print(f"[_flush_text_buffers] Final text sent: {msg_id}")
            self._current_text_buffer = []
    
    async def _upload_and_send_file(self, file_path: str) -> None:
        """Upload a file and send it to the chat."""
        from pathlib import Path
        
        # Resolve file path using client's work_dir if available
        if not os.path.isabs(file_path):
            base_dir = self.client.work_dir if self.client.work_dir else os.getcwd()
            file_path = os.path.join(base_dir, file_path)
        
        file_path = os.path.expanduser(file_path)
        
        if not os.path.exists(file_path):
            print(f"[_upload_and_send_file] File not found: {file_path}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 文件不存在: {file_path}",
            )
            return
        
        try:
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            
            print(f"[_upload_and_send_file] Uploading: {file_name} ({file_size} bytes)")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"📤 正在上传文件: {file_name}...",
            )
            
            # Read file content
            with open(file_path, "rb") as f:
                file_content = f.read()
            
            # Determine file type
            file_type = "stream"
            ext = Path(file_name).suffix.lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
                file_type = "image"
            
            # Upload file
            file_key = await asyncio.to_thread(
                self.client.upload_file,
                file_content,
                file_name,
                file_type,
            )
            
            if file_key:
                # Send file message
                msg_id = await asyncio.to_thread(
                    self.client.send_file_message,
                    self.chat_id,
                    file_key,
                )
                print(f"[_upload_and_send_file] File sent: {msg_id}")
            else:
                print(f"[_upload_and_send_file] Upload failed")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    f"❌ 上传失败: {file_name}",
                )
                
        except Exception as e:
            print(f"[_upload_and_send_file] Error: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 上传出错: {str(e)[:100]}",
            )
    
    async def _upload_and_send_image(self, image_path: str) -> None:
        """Upload an image and send it to the chat."""
        from pathlib import Path
        
        # Resolve image path using client's work_dir if available
        if not os.path.isabs(image_path):
            base_dir = self.client.work_dir if self.client.work_dir else os.getcwd()
            image_path = os.path.join(base_dir, image_path)
        
        image_path = os.path.expanduser(image_path)
        
        if not os.path.exists(image_path):
            print(f"[_upload_and_send_image] Image not found: {image_path}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 图片不存在: {image_path}",
            )
            return
        
        try:
            image_name = os.path.basename(image_path)
            image_size = os.path.getsize(image_path)
            
            print(f"[_upload_and_send_image] Uploading: {image_name} ({image_size} bytes)")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"📤 正在上传图片: {image_name}...",
            )
            
            # Read image content
            with open(image_path, "rb") as f:
                image_content = f.read()
            
            # Upload image
            image_key = await asyncio.to_thread(
                self.client.upload_image,
                image_content,
                image_name,
            )
            
            if image_key:
                # Send image message
                msg_id = await asyncio.to_thread(
                    self.client.send_image_message,
                    self.chat_id,
                    image_key,
                )
                print(f"[_upload_and_send_image] Image sent: {msg_id}")
            else:
                print(f"[_upload_and_send_image] Upload failed")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    f"❌ 上传图片失败: {image_name}",
                )
                
        except Exception as e:
            print(f"[_upload_and_send_image] Error: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 上传图片出错: {str(e)[:100]}",
            )
    
    async def _wire_loop_text_parts(self, wire: Wire) -> None:
        """Wire loop that sends different parts as separate messages."""
        from kimi_cli.wire.types import (
            ApprovalRequest, ImageURLPart, TextPart, ThinkPart, ToolCall, ToolCallPart, ToolResult,
            TurnBegin, TurnEnd, StepBegin, StepInterrupted, SubagentEvent
        )
        
        print("[_wire_loop_text_parts] Starting wire loop...")
        wire_ui = wire.ui_side(merge=False)
        
        # Use instance buffers for external access
        self._current_thinking_buffer = []
        self._current_text_buffer = []
        
        # Buffer for collecting ToolCall arguments from ToolCallPart
        # List of (tool_call_id, name, args_parts_list) tuples
        self._pending_tool_calls: list[tuple[str, str, list[str]]] = []
        self._current_tool_call_idx = -1
        
        async def send_thinking():
            if self._current_thinking_buffer:
                content = "".join(self._current_thinking_buffer).strip()
                if content:
                    print(f"[_wire_loop_text_parts] Sending thinking: {len(content)} chars")
                    # Split into chunks if too long
                    max_len = 1500
                    prefix = "💭 [思考过程]\n"
                    for i in range(0, len(content), max_len):
                        chunk = content[i:i+max_len]
                        msg_id = await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            prefix + chunk if i == 0 else chunk,
                        )
                        print(f"[_wire_loop_text_parts] Thinking chunk sent: {msg_id}")
                self._current_thinking_buffer = []
        
        async def send_text():
            if self._current_text_buffer:
                content = "".join(self._current_text_buffer).strip()
                if content:
                    print(f"[_wire_loop_text_parts] Sending text: {len(content)} chars")
                    # Split into chunks if too long
                    max_len = 1500
                    prefix = "🤖 [回复内容]\n"
                    for i in range(0, len(content), max_len):
                        chunk = content[i:i+max_len]
                        msg_id = await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            prefix + chunk if i == 0 else f"(续){chunk}",
                        )
                        print(f"[_wire_loop_text_parts] Text chunk sent: {msg_id}")
                self._current_text_buffer = []
        
        try:
            while True:
                print("[_wire_loop_text_parts] Waiting for message...")
                msg = await wire_ui.receive()
                print(f"[_wire_loop_text_parts] Received: {type(msg).__name__}")
                
                if isinstance(msg, TurnBegin):
                    self._current_thinking_buffer = []
                    self._current_text_buffer = []
                    
                elif isinstance(msg, TextPart):
                    if msg.text:
                        self._current_text_buffer.append(msg.text)
                        # Only send text if not in thinking mode (to avoid interleaving)
                        # If we have thinking content pending, wait for it to complete first
                        has_thinking = self._current_thinking_buffer and any(
                            self._current_thinking_buffer
                        )
                        if not has_thinking:
                            total_chars = sum(len(t) for t in self._current_text_buffer)
                            if total_chars > 1000:
                                await send_text()
                        
                elif isinstance(msg, ThinkPart):
                    if msg.think:
                        self._current_thinking_buffer.append(msg.think)
                        # Don't send thinking content mid-stream to avoid breaking it apart
                        # Only flush thinking buffer when ToolCall arrives or TurnEnd
                        # This keeps the complete thinking process together
                        pass  # Buffer will be flushed by ToolCall or TurnEnd
                elif isinstance(msg, ToolCall):
                    # Flush buffers first
                    await send_thinking()
                    await send_text()
                    # Get function name and ID from ToolCall
                    func_name = msg.function.name if msg.function and msg.function.name else 'unknown'
                    tool_id = msg.id if msg.id else f"tool_{len(self._pending_tool_calls)}"
                    # Add to pending tool calls list
                    self._pending_tool_calls.append((tool_id, func_name, []))
                    self._current_tool_call_idx = len(self._pending_tool_calls) - 1
                    print(f"[_wire_loop_text_parts] Added tool call to pending: {func_name} (id={tool_id})")
                    # Note: Tool call message will be sent when ToolResult arrives with merged arguments
                
                elif isinstance(msg, ToolCallPart):
                    # Collect arguments from ToolCallPart fragments
                    print(f"[_wire_loop_text_parts] ToolCallPart: {msg}")
                    # Add to the current tool call being collected
                    if self._current_tool_call_idx >= 0:
                        tool_id, tool_name, args_list = self._pending_tool_calls[self._current_tool_call_idx]
                        if hasattr(msg, 'arguments_part') and msg.arguments_part:
                            args_list.append(msg.arguments_part)
                            print(f"[_wire_loop_text_parts] Added arg to {tool_name}: {msg.arguments_part[:50]}...")
                    else:
                        print(f"[_wire_loop_text_parts] Warning: ToolCallPart without matching ToolCall")
                    
                elif isinstance(msg, ToolResult):
                    # Find matching tool call and merge arguments
                    tool_call_id = msg.tool_call_id if hasattr(msg, 'tool_call_id') else None
                    func_name = "unknown"
                    func_args = "{}"
                    
                    # Find the matching pending tool call
                    found_idx = -1
                    for idx, (tc_id, tc_name, tc_args) in enumerate(self._pending_tool_calls):
                        if tc_id == tool_call_id:
                            func_name = tc_name
                            func_args = "".join(tc_args)
                            found_idx = idx
                            break
                    
                    # Remove the found tool call from pending list
                    if found_idx >= 0:
                        self._pending_tool_calls.pop(found_idx)
                        if self._current_tool_call_idx >= len(self._pending_tool_calls):
                            self._current_tool_call_idx = len(self._pending_tool_calls) - 1
                    
                    # Pretty print arguments
                    try:
                        import json
                        args_obj = json.loads(func_args) if func_args else {}
                        func_args_display = json.dumps(args_obj, ensure_ascii=False, indent=2)
                    except:
                        func_args_display = func_args if func_args else "{}"
                    
                    # Send tool call info with merged arguments
                    print(f"[_wire_loop_text_parts] Sending tool call: {func_name}, args: {func_args_display[:100]}...")
                    msg_id = await asyncio.to_thread(
                        self.client.send_text_message,
                        self.chat_id,
                        f"🔧 [工具调用]\n名称: {func_name}\n参数: {func_args_display[:800]}",
                    )
                    print(f"[_wire_loop_text_parts] Tool call sent: {msg_id}")
                    
                    # Send tool result (split if too long)
                    # Extract result text with priority: brief > message > output > str(return_value)
                    # Filter out ImageURLPart with base64 data (e.g., from midscene-android screenshots)
                    result_text = ""
                    has_image = False
                    
                    if hasattr(msg.return_value, 'brief') and msg.return_value.brief:
                        result_text = str(msg.return_value.brief)
                    elif hasattr(msg.return_value, 'message') and msg.return_value.message:
                        result_text = str(msg.return_value.message)
                    elif hasattr(msg.return_value, 'output') and msg.return_value.output:
                        output = msg.return_value.output
                        # Check if output contains ImageURLPart with base64 data
                        if isinstance(output, list):
                            filtered_parts = []
                            for part in output:
                                if isinstance(part, ImageURLPart):
                                    has_image = True
                                    # Skip base64 images, replace with placeholder
                                    if part.image_url and part.image_url.url and part.image_url.url.startswith('data:'):
                                        continue  # Skip base64 image data
                                    else:
                                        filtered_parts.append(part)
                                else:
                                    filtered_parts.append(part)
                            # Convert filtered parts to text
                            result_text = " ".join(str(p) for p in filtered_parts if not isinstance(p, ImageURLPart))
                        else:
                            result_text = str(output)
                    else:
                        result_text = str(msg.return_value)
                    
                    # Add note if image was filtered
                    if has_image:
                        result_text = "[图片内容已过滤] " + (result_text if result_text else "")
                    
                    print(f"[_wire_loop_text_parts] Sending tool result: {len(result_text)} chars")
                    
                    # Warn if result is empty (but only if it's truly empty, not just "None" or "")
                    if not result_text or len(result_text.strip()) == 0 or result_text.strip() in ('None', 'null', 'False', '[]', '{}', '[图片内容已过滤]'):
                        print(f"[_wire_loop_text_parts] WARNING: Tool result is empty!")
                        msg_id = await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            f"⚠️ [工具返回为空]\n工具 `{func_name}` 返回了空结果，可能需要重试或检查输入",
                        )
                        print(f"[_wire_loop_text_parts] Empty result warning sent: {msg_id}")
                    else:
                        max_len = 1500
                        prefix = "📊 [工具返回]\n"
                        for i in range(0, len(result_text), max_len):
                            chunk = result_text[i:i+max_len]
                            msg_id = await asyncio.to_thread(
                                self.client.send_text_message,
                                self.chat_id,
                                prefix + chunk if i == 0 else f"(续){chunk}",
                            )
                            print(f"[_wire_loop_text_parts] Tool result chunk sent: {msg_id}")
                    
                elif isinstance(msg, StepInterrupted):
                    print("[_wire_loop_text_parts] StepInterrupted received")
                    # Step was interrupted, flush buffers to show what we have so far
                    await send_thinking()
                    await send_text()
                    
                elif isinstance(msg, TurnEnd):
                    print("[_wire_loop_text_parts] TurnEnd received, flushing buffers...")
                    # Flush remaining buffers
                    await send_thinking()
                    await send_text()
                    print("[_wire_loop_text_parts] Buffers flushed")
                    
                elif isinstance(msg, SubagentEvent):
                    # Handle subagent events from Task tool
                    print(f"[_wire_loop_text_parts] SubagentEvent received: {type(msg.event).__name__}")
                    
                    # Extract the actual event from SubagentEvent
                    subagent_msg = msg.event
                    
                    if isinstance(subagent_msg, TextPart):
                        if subagent_msg.text:
                            print(f"[_wire_loop_text_parts] Subagent text: {len(subagent_msg.text)} chars")
                            # Send subagent text immediately (don't buffer)
                            await asyncio.to_thread(
                                self.client.send_text_message,
                                self.chat_id,
                                f"📎 [Subagent] {subagent_msg.text[:1500]}",
                            )
                    elif isinstance(subagent_msg, ThinkPart):
                        if subagent_msg.think:
                            print(f"[_wire_loop_text_parts] Subagent thinking: {len(subagent_msg.think)} chars")
                            # Optionally send thinking (can be noisy, skip for now)
                            pass
                    elif isinstance(subagent_msg, ToolCall):
                        # Subagent tool call
                        func_name = subagent_msg.function.name if subagent_msg.function else 'unknown'
                        print(f"[_wire_loop_text_parts] Subagent tool call: {func_name}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            f"🔧 [Subagent 工具调用] {func_name}",
                        )
                    elif isinstance(subagent_msg, ToolResult):
                        # Subagent tool result
                        # Filter out ImageURLPart with base64 data
                        result_text = ""
                        has_image = False
                        
                        if hasattr(subagent_msg.return_value, 'brief') and subagent_msg.return_value.brief:
                            result_text = str(subagent_msg.return_value.brief)
                        elif hasattr(subagent_msg.return_value, 'message') and subagent_msg.return_value.message:
                            result_text = str(subagent_msg.return_value.message)
                        elif hasattr(subagent_msg.return_value, 'output') and subagent_msg.return_value.output:
                            output = subagent_msg.return_value.output
                            if isinstance(output, list):
                                for part in output:
                                    if isinstance(part, ImageURLPart):
                                        has_image = True
                                        if part.image_url and part.image_url.url and part.image_url.url.startswith('data:'):
                                            continue  # Skip base64 image data
                            result_text = str(subagent_msg.return_value)
                        else:
                            result_text = str(subagent_msg.return_value)
                        
                        # Add note if image was filtered
                        if has_image:
                            result_text = "[图片内容已过滤] " + (result_text if result_text else "")
                        
                        print(f"[_wire_loop_text_parts] Subagent tool result: {len(result_text)} chars")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            f"📊 [Subagent 结果] {result_text[:800]}",
                        )
                    else:
                        print(f"[_wire_loop_text_parts] Unhandled subagent event: {type(subagent_msg).__name__}")
                
                elif isinstance(msg, ApprovalRequest):
                    # Auto approve
                    msg.resolve("approve")
                    
        except Exception as e:
            error_msg = str(e)
            print(f"[_wire_loop_text_parts] Exception (wire closed): {e}")
            # Wire loop ends normally on most exceptions (like QueueShutDown)
            # Only send error for serious errors that need user attention
            if "token limit" in error_msg.lower() or "exceeded" in error_msg.lower():
                error_info = self._format_error_for_user(type(e).__name__, error_msg)
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    f"❌ [处理中断] {error_info}",
                )
            # Don't re-raise - wire loop ending is normal
    
    def _format_error_for_user(self, error_type: str, error_msg: str) -> str:
        """Format technical error for user display."""
        if "token limit" in error_msg.lower():
            import re
            match = re.search(r'(\d+)\s*\(requested:\s*(\d+)\)', error_msg)
            if match:
                return (
                    f"Token 超限\n"
                    f"限制: {match.group(1)} | 请求: {match.group(2)}\n"
                    f"建议: 使用 /compact 压缩上下文"
                )
            return "Token 超限，请使用 /compact 压缩上下文"
        return f"{error_type}: {error_msg[:100]}"
    
    async def cancel(self) -> None:
        """Cancel the current operation."""
        if self._cancel_event:
            self._cancel_event.set()


class SDKMessageHandler:
    """Message handler for SDK events."""
    
    def __init__(
        self,
        client: FeishuSDKClient,
        config: FeishuAccountConfig,
        feishu_config: FeishuConfig | None = None,
    ):
        self.client = client
        self.config = config
        self.feishu_config = feishu_config
        self._sessions: dict[str, SDKChatSession] = {}
        self._lock = asyncio.Lock()
    
    def _get_session_key(self, chat_id: str, user_id: str) -> str:
        """Get unique session key."""
        return f"{chat_id}:{user_id}"
    
    def _check_access(self, user_id: str, chat_id: str) -> bool:
        """Check if user/chat has access."""
        if self.config.allowed_users:
            if user_id not in self.config.allowed_users:
                return False
        
        if self.config.allowed_chats:
            if chat_id not in self.config.allowed_chats:
                return False
        
        return True
    
    def _clean_mentions(self, text: str) -> str:
        """Clean up @ mentions from text."""
        import re
        text = re.sub(r'@_user_\d+', '', text)
        text = re.sub(r'@\w+', '', text)
        return text.strip()
    
    def _get_work_dir(self) -> str:
        """Get the working directory for saving files.
        
        Returns:
            Path to working directory (guaranteed to exist)
        """
        import os
        if self.feishu_config and self.feishu_config.work_dir:
            work_dir = self.feishu_config.work_dir
        else:
            # Use the directory where kimi feishu was started
            work_dir = os.getcwd()
        
        # Ensure directory exists
        os.makedirs(work_dir, exist_ok=True)
        return work_dir
    
    async def _create_soul_for_session(self, session_key: str) -> KimiSoul:
        """Create a new KimiSoul for a chat session."""
        import os
        from kimi_cli.llm import augment_provider_with_env_vars, create_llm
        from kimi_cli.auth.oauth import OAuthManager
        
        kimi_config = load_config()
        
        # Create work directory for this session
        # Use configured work_dir if available, otherwise use directory where kimi feishu was started
        if self.feishu_config and self.feishu_config.work_dir:
            work_dir = KaosPath(self.feishu_config.work_dir)
            # Ensure the directory exists
            os.makedirs(str(work_dir), exist_ok=True)
        else:
            # Use the directory where kimi feishu was started (not current process cwd)
            work_dir = KaosPath(os.getcwd())
        
        session = await Session.create(work_dir)
        oauth = OAuthManager(kimi_config)
        
        model = None
        provider = None
        
        if kimi_config.default_model and kimi_config.default_model in kimi_config.models:
            model = kimi_config.models[kimi_config.default_model]
            provider = kimi_config.providers[model.provider]
        
        if model is None:
            from kimi_cli.config import LLMModel, LLMProvider
            model = LLMModel(provider="", model="", max_context_size=100_000)
            provider = LLMProvider(type="kimi", base_url="", api_key=SecretStr(""))
        
        augment_provider_with_env_vars(provider, model)
        
        llm = create_llm(
            provider,
            model,
            thinking=kimi_config.default_thinking,
            session_id=session.id,
            oauth=oauth,
        )
        
        runtime = await Runtime.create(
            config=kimi_config,
            oauth=oauth,
            llm=llm,
            session=session,
            yolo=self.config.auto_approve,
        )
        
        # Load MCP configs from global mcp.json
        mcp_configs = self._load_mcp_configs()
        
        agent = await load_agent(DEFAULT_AGENT_FILE, runtime, mcp_configs=mcp_configs)
        
        # Wait for MCP tools to be fully connected (not in background)
        if mcp_configs and hasattr(agent.toolset, 'wait_for_mcp_tools'):
            logger.info("[HANDLER] Waiting for MCP tools to connect...")
            try:
                await asyncio.wait_for(
                    agent.toolset.wait_for_mcp_tools(),
                    timeout=30.0  # Wait up to 30 seconds for MCP connections
                )
                logger.info("[HANDLER] MCP tools connected")
            except asyncio.TimeoutError:
                logger.warning("[HANDLER] Timeout waiting for MCP tools, continuing...")
            except Exception as e:
                logger.warning(f"[HANDLER] Error waiting for MCP tools: {e}")
        
        # Create isolated context for this session
        context = Context(session.context_file)
        await context.restore()
        
        soul = KimiSoul(agent, context=context)
        
        # Set work_dir on client for tools to use
        self.client.set_work_dir(str(work_dir))
        
        return soul
    
    def _load_mcp_configs(self) -> list[dict[str, Any]]:
        """Load MCP configs from global mcp.json file.
        
        Returns:
            List of MCP config dicts (each with 'mcpServers' key)
        """
        try:
            # Import here to avoid circular import issues
            from pathlib import Path
            from kimi_cli.share import get_share_dir
            
            mcp_file = get_share_dir() / "mcp.json"
            if not mcp_file.exists():
                logger.debug("No global MCP config file found")
                return []
            
            import json
            config = json.loads(mcp_file.read_text(encoding="utf-8"))
            
            # Validate that it has mcpServers
            if not config.get("mcpServers"):
                logger.debug("MCP config has no servers")
                return []
            
            # Return as a list with one config dict (format expected by load_agent)
            logger.info(f"Loaded MCP config with {len(config.get('mcpServers', {}))} servers")
            return [config]
            
        except Exception as e:
            logger.warning(f"Failed to load MCP config: {e}")
            return []
    
    async def handle_message_event(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        """Handle message receive event (v2.0)."""
        event = data.event
        message = event.message
        sender = event.sender
        
        chat_id = message.chat_id
        chat_type = message.chat_type
        user_id = sender.sender_id.open_id
        msg_type = message.message_type
        
        # DEBUG: Print received event
        print(f"\n[DEBUG] Received event:")
        print(f"  chat_id: {chat_id}")
        print(f"  chat_type: {chat_type}")
        print(f"  user_id: {user_id}")
        print(f"  msg_type: {msg_type}")
        print(f"  message.message_id: {message.message_id if hasattr(message, 'message_id') else 'N/A'}")
        print(f"  message.content: {message.content}")
        
        try:
            content = json.loads(message.content)
            print(f"  parsed content: {content}")
        except json.JSONDecodeError as e:
            print(f"  JSON parse error: {e}")
            content = {}
        
        # Check access
        if not self._check_access(user_id, chat_id):
            logger.warning(f"Access denied for user {user_id} in chat {chat_id}")
            return
        
        # Add OK reaction to the user's message
        message_id = message.message_id if hasattr(message, 'message_id') else None
        if message_id:
            try:
                await asyncio.to_thread(
                    self.client.add_message_reaction,
                    message_id,
                    "OK"
                )
            except Exception as e:
                logger.warning(f"Failed to add OK reaction: {e}")
        
        # Extract text content
        text = ""
        if msg_type == "text":
            text = content.get("text", "")
        elif msg_type == "image":
            # Handle image download
            image_key = content.get("image_key")
            
            if image_key:
                print(f"[HANDLER] Received image: {image_key}")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    f"📷 收到图片\n正在下载...",
                )
                
                # Download the image
                message_id = message.message_id if hasattr(message, 'message_id') else None
                result = await asyncio.to_thread(
                    self.client.download_image,
                    image_key,
                    message_id,
                )
                
                if result:
                    image_content, image_name = result
                    # Save to work directory
                    work_dir = self._get_work_dir()
                    save_path = os.path.join(work_dir, f"received_{image_name}")
                    try:
                        with open(save_path, "wb") as f:
                            f.write(image_content)
                        print(f"[HANDLER] Image saved to: {save_path}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"✅ 图片已保存: {save_path}\n大小: {len(image_content)} 字节",
                        )
                        # Also inform Kimi about the image
                        text = f"[用户上传了图片，已保存到: {save_path}]"
                    except Exception as e:
                        print(f"[HANDLER ERROR] Failed to save image: {e}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"❌ 保存图片失败: {str(e)[:100]}",
                        )
                        return
                else:
                    print(f"[HANDLER ERROR] Failed to download image")
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        chat_id,
                        f"❌ 下载图片失败",
                    )
                    return
            else:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "📷 收到图片，但无法获取图片信息",
                )
                return
        elif msg_type == "file":
            # Handle file download
            file_key = content.get("file_key")
            file_name = content.get("file_name", "unknown")
            
            if file_key:
                print(f"[HANDLER] Received file: {file_name}, key: {file_key}")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    f"📁 收到文件: {file_name}\n正在下载...",
                )
                
                # Download the file (pass message_id for user-sent files)
                message_id = message.message_id if hasattr(message, 'message_id') else None
                result = await asyncio.to_thread(
                    self.client.download_file,
                    file_key,
                    message_id,
                )
                
                if result:
                    file_content, actual_name = result
                    # Save to work directory
                    work_dir = self._get_work_dir()
                    save_path = os.path.join(work_dir, file_name)
                    try:
                        with open(save_path, "wb") as f:
                            f.write(file_content)
                        print(f"[HANDLER] File saved to: {save_path}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"✅ 文件已保存: {save_path}\n大小: {len(file_content)} 字节",
                        )
                        # Also inform Kimi about the file
                        text = f"[用户上传了文件: {file_name}，已保存到: {save_path}]"
                    except Exception as e:
                        print(f"[HANDLER ERROR] Failed to save file: {e}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"❌ 保存文件失败: {str(e)[:100]}",
                        )
                        return
                else:
                    print(f"[HANDLER ERROR] Failed to download file")
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        chat_id,
                        f"❌ 下载文件失败",
                    )
                    return
            else:
                text = f"[File uploaded: {file_name}]"
        else:
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"Unsupported message type: {msg_type}. Please send text messages only.",
            )
            return
        
        # Clean up @ mentions
        text = self._clean_mentions(text)
        
        if not text.strip():
            return
        
        logger.info(f"[HANDLER] Received message from {user_id} in {chat_id} ({chat_type}): {text[:100]}")
        
        # Get or create session
        session_key = self._get_session_key(chat_id, user_id)
        logger.info(f"[HANDLER] Session key: {session_key}")
        
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                print(f"[HANDLER] Creating new session for {session_key}")
                logger.info(f"[HANDLER] Creating new session for {session_key}")
                # Create new soul for this chat session
                try:
                    soul = await self._create_soul_for_session(session_key)
                    print(f"[HANDLER] Soul created successfully")
                except Exception as e:
                    print(f"[HANDLER ERROR] Failed to create soul: {e}")
                    logger.exception(f"[HANDLER] Failed to create soul: {e}")
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        chat_id,
                        f"❌ 创建会话失败: {str(e)[:100]}",
                    )
                    return
                
                session = SDKChatSession(
                    chat_id=chat_id,
                    user_id=user_id,
                    client=self.client,
                    config=self.config,
                    soul=soul,
                )
                self._sessions[session_key] = session
                print(f"[HANDLER] New session created")
                logger.info(f"[HANDLER] New session created")
            else:
                print(f"[HANDLER] Using existing session for {session_key}")
                logger.info(f"[HANDLER] Using existing session for {session_key}")
        
        # Handle the message
        print(f"[HANDLER] Calling session.handle_message with: {text[:50]}...")
        logger.info(f"[HANDLER] Calling session.handle_message with: {text[:50]}...")
        try:
            await session.handle_message(text)
            print("[HANDLER] session.handle_message completed successfully")
            logger.info("[HANDLER] session.handle_message completed successfully")
        except Exception as e:
            print(f"[HANDLER ERROR] handle_message failed: {e}")
            logger.exception(f"[HANDLER] handle_message failed: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"❌ 处理消息失败: {str(e)[:100]}",
            )
    
    async def handle_add_bot_event(self, data: lark.im.v1.P2ImChatMemberBotAddedV1) -> None:
        """Handle bot added to chat event."""
        event = data.event
        chat_id = event.chat_id
        
        logger.info(f"Bot added to chat: {chat_id}")
        # Removed auto welcome message - user can start conversation directly
    
    async def handle_remove_bot_event(self, data: lark.im.v1.P2ImChatMemberBotDeletedV1) -> None:
        """Handle bot removed from chat event."""
        event = data.event
        chat_id = event.chat_id
        
        logger.info(f"Bot removed from chat: {chat_id}")
    
    async def handle_p2p_chat_create(self, data: lark.im.v1.P2ImChatAccessEventBotP2pChatEnteredV1) -> None:
        """Handle P2P chat created event."""
        event = data.event
        chat_id = event.chat_id
        user_id = event.operator_id.open_id if event.operator_id else None
        
        logger.info(f"P2P chat created with user: {user_id}")
        # Removed auto welcome message - user can start conversation directly


class FeishuSDKServer:
    """Feishu server using SDK long connection (WebSocket)."""
    
    def __init__(self, config: FeishuConfig):
        self.config = config
        self._running = False
        self._clients: dict[str, FeishuSDKClient] = {}
        self._handlers: dict[str, SDKMessageHandler] = {}
        self._ws_clients: dict[str, Any] = {}  # WebSocket clients
        self._ws_threads: dict[str, threading.Thread] = {}
    
    async def start(self) -> None:
        """Start the Feishu SDK server."""
        if self._running:
            return
        
        self._running = True
        
        # Initialize all accounts
        await self._init_accounts()
        
        logger.info("Feishu SDK server started successfully")
        print(f"✅ Feishu SDK server is running")
        print(f"   Use Ctrl+C to stop")
    
    async def stop(self) -> None:
        """Stop the server."""
        if not self._running:
            return
        
        self._running = False
        
        # Stop all WebSocket clients
        for name, ws_client in self._ws_clients.items():
            logger.info(f"Stopping WebSocket client for {name}")
        
        self._ws_clients.clear()
        self._handlers.clear()
        self._clients.clear()
        
        logger.info("Feishu SDK server stopped")
    
    async def _init_accounts(self) -> None:
        """Initialize Feishu SDK clients for all accounts."""
        for account_name, account_config in self.config.accounts.items():
            try:
                # Create SDK client for API calls
                client = FeishuSDKClient(account_config)
                self._clients[account_name] = client
                
                # Test authentication by getting bot info
                bot_info = client.get_bot_info()
                if bot_info:
                    logger.info(
                        f"Feishu account '{account_name}' authenticated: "
                        f"{bot_info.get('app_name')}"
                    )
                else:
                    logger.warning(f"Could not get bot info for account '{account_name}'")
                
                # Create message handler (each handler manages its own sessions with isolated souls)
                handler = SDKMessageHandler(client, account_config, self.config)
                self._handlers[account_name] = handler
                
                # Start WebSocket client for event receiving
                self._start_ws_client(account_name, account_config, handler)
                
            except Exception as e:
                logger.error(f"Failed to initialize account '{account_name}': {e}")
    
    def _start_ws_client(
        self,
        account_name: str,
        account_config: FeishuAccountConfig,
        handler: SDKMessageHandler,
    ) -> None:
        """Start WebSocket client for event receiving.
        
        This runs in a separate thread since the SDK's start() blocks.
        """
        # Create event dispatcher first
        event_handler = self._create_event_handler(handler)
        
        # Create WebSocket client (stored before starting thread)
        ws_client = lark.ws.Client(
            app_id=account_config.app_id,
            app_secret=account_config.app_secret.get_secret_value(),
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,  # Normal logging
        )
        
        self._ws_clients[account_name] = ws_client
        
        def run_ws_client():
            """Run WebSocket client in a thread."""
            try:
                logger.info(f"Starting WebSocket client for account: {account_name}")
                ws_client.start()
            except Exception as e:
                logger.error(f"WebSocket client error for {account_name}: {e}")
        
        # Start in a daemon thread
        thread = threading.Thread(target=run_ws_client, daemon=True)
        thread.start()
        self._ws_threads[account_name] = thread
        
        logger.info(f"WebSocket client thread started for: {account_name}")
    
    def _create_event_handler(
        self,
        handler: SDKMessageHandler,
    ) -> lark.EventDispatcherHandler:
        """Create event dispatcher handler with all event callbacks."""
        
        # Store reference to the main event loop
        main_loop = asyncio.get_event_loop()
        
        def _schedule_async(coro, name: str = "task"):
            """Schedule async coroutine in main loop, non-blocking."""
            def on_done(fut):
                try:
                    fut.result()
                    print(f"[ASYNC] {name} completed successfully")
                except Exception as e:
                    print(f"[ASYNC ERROR] {name} failed: {e}")
                    import traceback
                    traceback.print_exc()
            
            try:
                future = asyncio.run_coroutine_threadsafe(coro, main_loop)
                future.add_done_callback(on_done)
                return future
            except Exception as e:
                print(f"[SCHEDULE ERROR] Failed to schedule {name}: {e}")
                import traceback
                traceback.print_exc()
                return None
        
        # Define event callbacks - these run in the SDK's thread
        def on_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
            """Handle message receive event."""
            print(f"\n[EVENT] Message received!")
            try:
                content = data.event.message.content
                print(f"  content: {content[:100] if content else 'N/A'}")
            except Exception:
                print(f"  (content not available)")
            
            # Schedule in main event loop (non-blocking)
            print(f"[EVENT] Scheduling handler in main loop...")
            _schedule_async(handler.handle_message_event(data), "message_handler")
            print(f"[EVENT] Handler scheduled (non-blocking)")
        
        def on_p2_im_chat_member_bot_added_v1(data: lark.im.v1.P2ImChatMemberBotAddedV1) -> None:
            """Handle bot added to chat."""
            _schedule_async(handler.handle_add_bot_event(data), "bot_added")
        
        def on_p2_im_chat_member_bot_deleted_v1(data: lark.im.v1.P2ImChatMemberBotDeletedV1) -> None:
            """Handle bot removed from chat."""
            _schedule_async(handler.handle_remove_bot_event(data), "bot_removed")
        
        def on_p2_im_chat_access_event_v1(data: lark.im.v1.P2ImChatAccessEventBotP2pChatEnteredV1) -> None:
            """Handle P2P chat access event."""
            _schedule_async(handler.handle_p2p_chat_create(data), "p2p_chat")
        
        def on_p2_im_message_message_read_v1(data: lark.im.v1.P2ImMessageMessageReadV1) -> None:
            """Handle message read event (ignore)."""
            pass
        
        # Build event handler
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(on_p2_im_message_receive_v1) \
            .register_p2_im_chat_member_bot_added_v1(on_p2_im_chat_member_bot_added_v1) \
            .register_p2_im_chat_member_bot_deleted_v1(on_p2_im_chat_member_bot_deleted_v1) \
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(on_p2_im_chat_access_event_v1) \
            .register_p2_im_message_message_read_v1(on_p2_im_message_message_read_v1) \
            .build()
        
        return event_handler
    
    def _get_status(self) -> dict[str, Any]:
        """Get current server status."""
        return {
            "running": self._running,
            "mode": "sdk_long_connection",
            "accounts": {
                name: {
                    "connected": name in self._clients,
                    "ws_connected": name in self._ws_clients,
                }
                for name in self.config.accounts.keys()
            },
        }
    
    async def run_forever(self) -> None:
        """Run the server until stopped."""
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass


async def run_sdk_server(
    config: FeishuConfig | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Run the Feishu SDK server.
    
    This uses the official Feishu SDK's long connection (WebSocket) feature,
    eliminating the need for webhook URLs or tunneling tools.
    
    Args:
        config: Feishu configuration. If None, loads from default location.
        host: Override host from config.
        port: Override port from config.
    """
    if config is None:
        config = FeishuConfig.load()
    
    if host:
        config.host = host
    if port:
        config.port = port
    
    # Validate configuration
    if not config.accounts:
        print("No Feishu accounts configured.")
        print("Please run 'kimi feishu config' to set up your Feishu integration.")
        print("\nFor setup guide: kimi feishu setup")
        return
    
    server = FeishuSDKServer(config)
    
    try:
        await server.start()
        print(f"\n🚀 Feishu SDK server started!")
        print(f"   Mode: SDK Long Connection (WebSocket)")
        print(f"   Accounts: {', '.join(config.accounts.keys())}")
        print("\n✅ No webhook URL needed!")
        print("✅ No tunnel/穿透 tools required!")
        print("✅ Events received via WebSocket directly from Feishu")
        print("✅ Each chat has isolated context")
        
        await server.run_forever()
        
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()
