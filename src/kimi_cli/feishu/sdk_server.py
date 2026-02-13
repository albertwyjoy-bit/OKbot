"""Feishu SDK server using long connection (WebSocket) for events.

This module provides a server that uses the official Feishu SDK's
long connection feature to receive events without needing a public webhook URL.

Usage:
    1. No webhook URL configuration needed
    2. No tunnel/穿透 tools required
    3. Events are received via WebSocket directly from Feishu
"""

from __future__ import annotations

# NOTE: We no longer use nest_asyncio due to compatibility issues with Python 3.12
# (causes "cannot enter context" errors with contextvars).
# Instead, we ensure complete event loop isolation in WebSocket threads.
import asyncio
import json
import os
import threading
import warnings
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

# Suppress RuntimeWarning about unawaited coroutines from the Lark SDK internals
# These warnings are from the SDK's internal async methods and are not our bugs
warnings.filterwarnings(
    "ignore",
    message=r"coroutine 'Client\._(connect|disconnect)' was never awaited",
    category=RuntimeWarning,
)
from kaos.path import KaosPath
from loguru import logger
from pydantic import SecretStr

from kimi_cli.agentspec import DEFAULT_AGENT_FILE
from kimi_cli.config import load_config
from kimi_cli.feishu.card_builder import split_content_for_cards
from kimi_cli.feishu.config import FeishuAccountConfig, FeishuConfig
from kimi_cli.feishu.message_renderer import create_renderer
from kimi_cli.feishu.post_message import handle_post_message
from kimi_cli.feishu.sdk_client import FeishuSDKClient
from kimi_cli.session import Session

# Gateway removed - using SDK long connection only
from kimi_cli.soul.agent import Runtime, load_agent
from kimi_cli.soul.context import Context
from kimi_cli.soul.kimisoul import KimiSoul
from kimi_cli.utils.asr import transcribe_audio

# Scheduler imports (lazy import to avoid circular imports)
_scheduler_initialized = False


class SDKChatSession:
    """A chat session with a user using SDK client.
    
    Each session has its own KimiSoul with isolated context.
    Supports buffering media files (images/documents) until text instruction arrives.
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
        
        # YOLO mode: auto-approve all tool calls
        # Use config setting, default to False (require approval) for safety
        self._yolo_mode: bool = getattr(config, 'auto_approve', False)
        
        # Pending approval requests for non-YOLO mode
        self._pending_approvals: dict[str, Any] = {}
        
        # Pending model selection requests
        self._pending_model_selections: dict[str, Any] = {}
        
        # Flag to indicate this session should be deleted (for /new command)
        self._should_delete: bool = False
        
        # Initialize message renderer for card-based messages
        self._renderer = create_renderer()
        
        # Register Feishu tools for this session
        print(f"[SESSION] SDKChatSession.__init__ called for chat {chat_id}, calling _register_feishu_tools")
        self._register_feishu_tools()
        
        # Scheduled task pending notifications queue
        # This stores results from scheduled tasks that need to be sent
        # when the session becomes idle
        self._pending_scheduled_notifications: list[Any] = []
        self._scheduled_notifications_lock = asyncio.Lock()
        
        # Media buffer for deferred processing
        # Stores media files (images, documents) received without text instruction
        self._media_buffer: list[dict[str, Any]] = []
        self._buffer_notification_id: str | None = None  # Message ID for buffer status updates
        self._buffer_lock = asyncio.Lock()
    
    def _register_feishu_tools(self) -> None:
        """Register Feishu tools with the soul's toolset."""
        print(f"[SESSION] Starting tool registration for chat {self.chat_id}")
        logger.info(f"[SESSION] Starting tool registration for chat {self.chat_id}")
        try:
            from kimi_cli.tools.feishu import FeishuSendFile, FeishuSendMessage, set_feishu_client
            from kimi_cli.tools.scheduler_tool import (
                CreateScheduledJob,
                DeleteScheduledJob,
                ListScheduledJobs,
                ToggleScheduledJob,
            )
            
            # Set the global client reference for tools
            set_feishu_client(self.client)
            logger.info(f"[SESSION] Feishu client set for chat {self.chat_id}")
            
            # Add tools to soul's toolset if not already present
            # Access toolset through soul's agent
            if hasattr(self.soul, '_agent') and self.soul._agent is not None:
                toolset = self.soul._agent.toolset
                logger.info(f"[SESSION] Got toolset for chat {self.chat_id}, tool count: {len(toolset.tools)}")
                
                if not toolset.find("FeishuSendFile"):
                    toolset.add(FeishuSendFile())
                    logger.info(f"[SESSION] Registered FeishuSendFile tool for chat {self.chat_id}")
                else:
                    logger.info(f"[SESSION] FeishuSendFile already exists for chat {self.chat_id}")
                    
                if not toolset.find("FeishuSendMessage"):
                    toolset.add(FeishuSendMessage())
                    logger.info(f"[SESSION] Registered FeishuSendMessage tool for chat {self.chat_id}")
                else:
                    logger.info(f"[SESSION] FeishuSendMessage already exists for chat {self.chat_id}")
                    
                if not toolset.find("CreateScheduledJob"):
                    toolset.add(CreateScheduledJob())
                    logger.info(f"[SESSION] Registered CreateScheduledJob tool for chat {self.chat_id}")
                else:
                    logger.info(f"[SESSION] CreateScheduledJob already exists for chat {self.chat_id}")
                    
                if not toolset.find("ListScheduledJobs"):
                    toolset.add(ListScheduledJobs())
                    logger.info(f"[SESSION] Registered ListScheduledJobs tool for chat {self.chat_id}")
                else:
                    logger.info(f"[SESSION] ListScheduledJobs already exists for chat {self.chat_id}")
                    
                if not toolset.find("DeleteScheduledJob"):
                    toolset.add(DeleteScheduledJob())
                    logger.info(f"[SESSION] Registered DeleteScheduledJob tool for chat {self.chat_id}")
                else:
                    logger.info(f"[SESSION] DeleteScheduledJob already exists for chat {self.chat_id}")
                    
                if not toolset.find("ToggleScheduledJob"):
                    toolset.add(ToggleScheduledJob())
                    logger.info(f"[SESSION] Registered ToggleScheduledJob tool for chat {self.chat_id}")
                else:
                    logger.info(f"[SESSION] ToggleScheduledJob already exists for chat {self.chat_id}")
                    
                # Log all available tools after registration
                tool_names = [t.name for t in toolset.tools]
                logger.info(f"[SESSION] Tool registration complete for chat {self.chat_id}, total tools: {len(toolset.tools)}")
                logger.info(f"[SESSION] Available tools: {tool_names}")
                print(f"[SESSION] Registered {len(toolset.tools)} tools for chat {self.chat_id}: {tool_names}")
                
                # Refresh system prompt to include new tools
                try:
                    self.soul._agent.refresh_system_prompt()
                    logger.info(f"[SESSION] System prompt refreshed for chat {self.chat_id}")
                except Exception as e:
                    logger.warning(f"[SESSION] Failed to refresh system prompt: {e}")
            else:
                logger.warning(f"[SESSION] Soul has no _agent for chat {self.chat_id}, skipping tool registration")
        except Exception as e:
            logger.warning(f"[SESSION] Failed to register Feishu tools: {e}", exc_info=True)
    
    async def add_to_media_buffer(
        self,
        media_type: str,
        file_path: str,
        file_name: str,
        file_size: int = 0,
    ) -> bool:
        """Add media to the buffer and notify user.
        
        Args:
            media_type: 'image' or 'file'
            file_path: Path where the file is saved
            file_name: Original file name
            file_size: File size in bytes
            
        Returns:
            True if added successfully
        """
        async with self._buffer_lock:
            self._media_buffer.append({
                "type": media_type,
                "path": file_path,
                "name": file_name,
                "size": file_size,
                "timestamp": asyncio.get_event_loop().time(),
            })
            
            buffer_count = len(self._media_buffer)
            image_count = sum(1 for m in self._media_buffer if m["type"] == "image")
            file_count = sum(1 for m in self._media_buffer if m["type"] == "file")
            
            # Build status message
            parts = [f"📦 已缓存 {buffer_count} 个文件"]
            if image_count > 0:
                parts.append(f"🖼️ 图片: {image_count}")
            if file_count > 0:
                parts.append(f"📄 文件: {file_count}")
            parts.append("\n💡 发送文字说明即可一起处理")
            
            status_text = "\n".join(parts)
            
            # Update existing notification or send new one
            try:
                if self._buffer_notification_id:
                    # Try to update the existing message
                    # Note: Feishu doesn't support updating text messages easily,
                    # so we delete and resend or just send a new one
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        self.chat_id,
                        status_text,
                    )
                else:
                    # First media in buffer
                    msg_id = await asyncio.to_thread(
                        self.client.send_text_message,
                        self.chat_id,
                        status_text,
                    )
                    self._buffer_notification_id = msg_id
            except Exception as e:
                logger.warning(f"[SESSION] Failed to send buffer notification: {e}")
            
            return True
    
    async def get_buffered_media_context(self) -> str:
        """Get context string for all buffered media.
        
        Returns:
            Formatted context string describing buffered media
        """
        async with self._buffer_lock:
            if not self._media_buffer:
                return ""
            
            parts = ["\n\n[已缓存的文件/图片]"]
            for i, media in enumerate(self._media_buffer, 1):
                media_type = "图片" if media["type"] == "image" else "文件"
                parts.append(f"{i}. [{media_type}] {media['name']} ({media['size']} bytes) - 路径: {media['path']}")
            
            return "\n".join(parts)
    
    async def clear_media_buffer(self) -> list[dict[str, Any]]:
        """Clear and return all buffered media.
        
        Returns:
            List of buffered media items (for processing)
        """
        async with self._buffer_lock:
            buffered = self._media_buffer.copy()
            self._media_buffer.clear()
            self._buffer_notification_id = None
            return buffered
    
    async def has_buffered_media(self) -> bool:
        """Check if there are buffered media files."""
        async with self._buffer_lock:
            return len(self._media_buffer) > 0
    
    async def handle_message(self, message_text: str) -> None:
        """Handle an incoming message."""
        print(f"[SESSION] Handling message: {message_text[:50]}...")
        logger.info(f"[SESSION] Handling message: {message_text[:50]}...")
        
        # Set current chat ID for tool calls
        self.client.set_current_chat_id(self.chat_id)
        
        # Handle interruption commands first (before lock) to allow stopping running tasks
        stripped = message_text.strip()
        if stripped == "/clear":
            logger.info("[SESSION] /clear command (pre-lock)")
            await self._handle_clear()
            return
        elif stripped == "/stop":
            logger.info("[SESSION] /stop command (pre-lock)")
            await self._handle_stop()
            return
        elif stripped == "/clear-buffer":
            logger.info("[SESSION] /clear-buffer command")
            await self._handle_clear_buffer()
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
            # Handle session management commands (should be caught by handler, but check here as fallback)
            normalized = ' '.join(stripped.split()).lower()
            if normalized == "/sessions":
                await self._send_fallback_message("请使用 `/sessions` 命令（不要在对话中）")
                return
            elif normalized.startswith("/continue ") or normalized.startswith("/session "):
                await self._send_fallback_message("请使用 `/continue <id>` 命令（不要在对话中）")
                return
            elif normalized == "/link" or normalized == "/id":
                await self._send_fallback_message("请使用 `/link` 或 `/id` 命令（不要在对话中）")
                return
            
            # Handle local commands
            if stripped == "/help":
                logger.info("[SESSION] /help command")
                await self._send_help()
                return
            elif stripped == "/reset":
                logger.info("[SESSION] /reset command")
                await self._send_reset()
                return
            elif stripped == "/new":
                logger.info("[SESSION] /new command")
                await self._handle_new_session()
                return
            elif stripped == "/mcp" or stripped.startswith("/mcp "):
                logger.info("[SESSION] /mcp command")
                await self._handle_mcp_command(stripped)
                return
            elif stripped == "/yolo":
                logger.info("[SESSION] /yolo command")
                await self._handle_yolo_toggle()
                return
            elif stripped == "/plan":
                logger.info("[SESSION] /plan command")
                await self._handle_plan_command()
                return
            elif stripped.startswith("/cron"):
                logger.info("[SESSION] /cron command")
                await self._handle_cron_command(stripped)
                return
            elif stripped == "/model":
                logger.info("[SESSION] /model command")
                await self._handle_model_command()
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
            
            # Flush pending scheduled notifications
            # This sends any scheduled task results that were queued while we were busy
            await self._flush_pending_scheduled_notifications()
    
    async def _handle_cron_command(self, command: str) -> None:
        """Handle /cron command."""
        try:
            from kimi_cli.scheduler.commands import handle_cron_command
            handled, response = await handle_cron_command(command, self)
            if handled and response:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    response,
                )
        except Exception as e:
            logger.exception(f"Error handling /cron command: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 处理定时任务命令时出错: {str(e)[:100]}",
            )
    
    async def _handle_model_command(self) -> None:
        """Handle /model command: show model selection card."""
        from kimi_cli.auth.platforms import get_platform_name_for_provider, refresh_managed_models
        from kimi_cli.config import load_config
        from kimi_cli.feishu.card_builder import (
            build_model_selection_card,
        )
        
        try:
            config = load_config()
            
            # Refresh managed models from remote if needed
            await refresh_managed_models(config)
            
            if not config.models:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "⚠️ 没有配置任何模型。请先在配置文件中添加模型。",
                )
                return
            
            # Get current model from soul's runtime
            current_model_name: str | None = None
            if self.soul and self.soul.runtime and self.soul.runtime.llm:
                curr_model_cfg = self.soul.runtime.llm.model_config
                for name, model_cfg in config.models.items():
                    if model_cfg == curr_model_cfg:
                        current_model_name = name
                        break
            
            # If no runtime model found, use default from config
            if not current_model_name and config.default_model:
                current_model_name = config.default_model
            
            # Build model list
            models: list[dict[str, Any]] = []
            for name in sorted(config.models.keys()):
                model_cfg = config.models[name]
                provider_label = get_platform_name_for_provider(model_cfg.provider) or model_cfg.provider
                label = f"{model_cfg.model} ({provider_label})"
                models.append({
                    "name": name,
                    "model": model_cfg.model,
                    "provider": provider_label,
                    "label": label,
                })
            
            # Generate request ID for this selection
            import uuid
            request_id = f"model_select_{uuid.uuid4().hex[:8]}"
            
            # Build and send model selection card
            card = build_model_selection_card(
                models=models,
                current_model=current_model_name,
                request_id=request_id,
            )
            
            card_message_id = await asyncio.to_thread(
                self.client.send_interactive_card,
                self.chat_id,
                card,
            )
            
            logger.info(f"[SESSION] Model selection card sent: {card_message_id}")
            
            # Store pending model selection state
            self._pending_model_selections[request_id] = {
                "message_id": card_message_id,
                "models": models,
                "current_model": current_model_name,
                "stage": "selecting_model",  # selecting_model -> selecting_thinking -> confirming
                "selected_model": None,
                "selected_thinking": None,
            }
            
        except Exception as e:
            logger.exception(f"Error handling /model command: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 处理模型选择命令时出错: {str(e)[:100]}",
            )
    
    async def _flush_pending_scheduled_notifications(self) -> None:
        """Flush pending scheduled notifications.
        
        This sends any scheduled task results that were queued while the session was busy.
        """
        try:
            from kimi_cli.scheduler.scheduler import get_scheduler
            
            scheduler = get_scheduler()
            if not scheduler._initialized:
                return
            
            session_manager = scheduler._session_manager
            if not session_manager:
                return
            
            # Find all scheduled sessions for this chat and flush their notifications
            for session_id, sched_session in list(session_manager._scheduled_sessions.items()):
                if sched_session.chat_id == self.chat_id:
                    await sched_session.flush_pending_notifications()
                    logger.debug(f"Flushed pending notifications for scheduled session {session_id}")
                    
        except Exception as e:
            logger.exception(f"Error flushing pending scheduled notifications: {e}")
    
    async def _send_help(self) -> None:
        """Send help message."""
        help_text = """👋 **Kimi Code CLI 帮助**

**本地命令：**
• /help - 显示此帮助
• /new - 创建新会话（获取新的 session ID）
• /reset - 重置对话
• /stop - 打断当前操作（保留上下文，类似 Ctrl+C）
• /clear - 中断当前处理并清空上下文
• /clear-buffer - 清空已缓存的文件/图片
• /mcp - 显示 MCP 服务器状态
• /cron - 定时任务管理
• /model - 切换模型和 Thinking 模式

**跨端接续（CLI ↔ Feishu）：**
• /sessions - 列出电脑端 CLI 的所有 sessions
• /continue <id> - 接续指定的 CLI session
• /link - 查看当前关联的 session
• /id - 查看当前 session ID（用于 CLI 接续）

**打断操作：**
当我在处理长任务时，发送 `/stop` 即可立即打断，类似 CLI 中的 Ctrl+C。

**YOLO 模式：**
• /yolo - 切换 YOLO 模式（自动批准工具调用）
• 当前为 **{'YOLO' if self._yolo_mode else '非 YOLO'} 模式**
• YOLO 模式：工具调用自动批准
• 非 YOLO 模式：每次工具调用需通过卡片授权

**Plan 模式：**
• /plan - 进入 Plan 模式（规划阶段，限制写操作）
• Plan 模式下只能使用只读工具和编辑 Plan 文件
• 使用 `PlanExit` 工具退出 Plan 模式

**Soul 命令 (由KimiSoul处理)：**
• /compact - 压缩上下文
• /init - 生成 AGENTS.md
• /update-skill - 重新加载 skills
• /update-mcp - 重新加载 MCP 工具
• ... 以及其他 Soul 级别命令

**Skills：**
• /skill - 使用 skill（需先在 feishu.toml 中配置 skills_dir）

**语音消息：**
• 🎤 按住说话 - 我会自动识别语音并回复
• 使用智谱 GLM-ASR-2512 进行语音识别（中文识别效果优秀）
• 需要先设置 API Key: `export ZHIPU_API_KEY="your-api-key"`
• 获取 API Key: https://open.bigmodel.cn/

**文件传输：**
• 📥 发送文件给我 - 我会保存到当前目录
• 📤 让我发送文件 - 直接说"把xxx文件发给我"

**批量文件处理：**
• 您可以连续发送多个图片/文件，它们会被暂存
• 发送文字说明后，我会一次性处理所有内容
• 使用 `/clear-buffer` 清空暂存的文件

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
    
    async def _handle_new_session(self) -> None:
        """Handle /new command: create a new session.
        
        This deletes the current session and forces creation of a new one
        with a new session ID on the next message.
        """
        # Send confirmation first
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            "🆕 正在创建新会话...",
        )
        
        # Clear the context first (best effort)
        try:
            if self.soul and hasattr(self.soul, 'context'):
                await self.soul.context.clear()
                logger.info("[SESSION] Context cleared for new session")
        except Exception as e:
            logger.warning(f"[SESSION] Failed to clear context: {e}")
        
        # Mark this session for deletion by the handler
        # The handler will remove it from _sessions and create a new one
        self._should_delete = True
        print(f"[SESSION] _should_delete set to True for session {id(self)}, chat {self.chat_id}")
        
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            "✅ 已准备好创建新会话。请发送一条消息以开始新对话！",
        )
        logger.info(f"[SESSION] Session {id(self)} marked for deletion (_should_delete={self._should_delete}), new session will be created on next message")
    
    async def _handle_stop(self) -> None:
        """Handle /stop command: cancel current operation without clearing context.
        
        This is like Ctrl+C in CLI - it stops the current operation but preserves context.
        """
        # Check if there's a running operation
        was_running = False
        async with self._lock:
            if self._running and self._cancel_event:
                was_running = True
                logger.info("[SESSION] Cancelling current operation due to /stop")
                # Set the cancel event to stop the current operation
                self._cancel_event.set()
        
        if was_running:
            # Wait a bit for the operation to cancel
            await asyncio.sleep(0.3)
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                "⏹️ 已中断当前操作。上下文已保留，可以继续对话。",
            )
            logger.info("[SESSION] Operation stopped, context preserved")
        else:
            # No running operation
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                "ℹ️ 当前没有正在进行的操作。",
            )
            logger.info("[SESSION] /stop called but no operation was running")
    
    async def _handle_clear_buffer(self) -> None:
        """Handle /clear-buffer command: clear the media buffer."""
        async with self._buffer_lock:
            buffer_count = len(self._media_buffer)
            if buffer_count == 0:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "📭 当前没有缓存的文件/图片",
                )
                return
            
            # Clear the buffer
            self._media_buffer.clear()
            self._buffer_notification_id = None
        
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            f"🧹 已清空缓存 ({buffer_count} 个文件/图片)",
        )
        logger.info(f"[SESSION] Media buffer cleared ({buffer_count} items)")
    
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
    
    async def _handle_yolo_toggle(self) -> None:
        """Handle /yolo command: toggle YOLO mode."""
        self._yolo_mode = not self._yolo_mode
        
        # Also update the soul's runtime approval setting
        if self.soul and hasattr(self.soul, 'runtime') and self.soul.runtime:
            if self._yolo_mode:
                self.soul.runtime.approval.set_yolo(True)
                print("[SESSION] Runtime YOLO mode enabled")
            else:
                self.soul.runtime.approval.set_yolo(False)
                print("[SESSION] Runtime YOLO mode disabled")
        
        if self._yolo_mode:
            status_text = """✅ **YOLO 模式已开启**

工具调用将自动批准，无需手动确认。

💡 **提示**：发送 `/yolo` 关闭 YOLO 模式"""
        else:
            status_text = """🔒 **YOLO 模式已关闭**

每次工具调用需要通过卡片授权：
• ✅ 允许一次 - 仅允许当前操作
• 🔓 始终允许 - 此对话中始终允许该操作
• ❌ 拒绝 - 拒绝当前操作

💡 **提示**：发送 `/yolo` 重新开启 YOLO 模式"""
        
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            status_text,
        )
        logger.info(f"[SESSION] YOLO mode toggled: {self._yolo_mode}")
    
    async def _handle_plan_command(self) -> None:
        """Handle /plan command: enter plan mode."""
        from pathlib import Path
        
        # Check if already in plan mode via soul's approval
        if self.soul and hasattr(self.soul, 'runtime') and self.soul.runtime:
            if self.soul.runtime.approval.is_plan_mode():
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "Already in plan mode. Use `PlanExit` tool to exit.",
                )
                return
        
        # Get session ID for plan file name
        session_id = ""
        if self.soul and hasattr(self.soul, '_runtime') and self.soul._runtime and self.soul._runtime.session:
            session_id = self.soul._runtime.session.id
        else:
            # Fallback to a unique ID based on chat_id
            import uuid
            session_id = f"feishu_{self.chat_id}_{uuid.uuid4().hex[:8]}"
        plans_dir = Path.home() / ".kimi" / "plans"
        plan_file = plans_dir / f"{session_id}.md"
        
        # Create plans directory if not exists
        try:
            plans_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create plans directory: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ Failed to create plans directory: {e}",
            )
            return
        
        # Create empty plan file if not exists
        try:
            if not plan_file.exists():
                plan_file.write_text(f"# Plan for Session {session_id}\n\n", encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to create plan file: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ Failed to create plan file: {e}",
            )
            return
        
        # Enable plan mode in soul's approval
        if self.soul and hasattr(self.soul, 'runtime') and self.soul.runtime:
            self.soul.runtime.approval.set_plan_mode(True, str(plan_file))
        
        # Send confirmation message
        status_text = f"""📝 **Entered Plan Mode**

Plan file: `{plan_file}`

In plan mode:
• Read-only tools are allowed (ReadFile, Grep, Search, etc.)
• WriteFile is allowed **only** for the plan file
• All other write operations are blocked

Use `PlanExit` tool when you're ready to proceed with execution."""
        
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            status_text,
        )
        
        # Add plan mode prompt to context
        try:
            from kosong.message import Message

            import kimi_cli.prompts as prompts
            from kimi_cli.soul.message import system
            
            plan_message_content = (
                f"{prompts.PLAN}\n\n"
                f"Current editable plan file: {plan_file}"
            )
            
            if self.soul and hasattr(self.soul, 'context'):
                await self.soul.context.append_message(Message(
                    role="user",
                    content=[system(plan_message_content)]
                ))
                logger.info("[SESSION] Added plan mode prompt to context")
        except Exception as e:
            logger.warning(f"[SESSION] Failed to add plan mode prompt to context: {e}")
        
        logger.info(f"[SESSION] Entered plan mode, plan file: {plan_file}")
    
    async def _process_message(self, message_text: str) -> None:
        """Process a user message through the soul - multi-part text output mode."""

        from kimi_cli.soul import run_soul
        
        print(f"[_process_message] Starting with text: {message_text[:100]}")
        logger.info(f"[_process_message] Starting with text: {message_text[:100]}")
        
        # Log toolset status
        try:
            if self.soul and hasattr(self.soul, '_agent') and self.soul._agent:
                toolset = self.soul._agent.toolset
                tool_names = [t.name for t in toolset.tools]
                print(f"[_process_message] Toolset has {len(toolset.tools)} tools: {tool_names}")
                logger.info(f"[_process_message] Toolset has {len(toolset.tools)} tools: {tool_names}")
        except Exception as e:
            print(f"[_process_message] Failed to get toolset info: {e}")
            logger.warning(f"[_process_message] Failed to get toolset info: {e}")
        
        # Run the soul with wire (messages sent in real-time)
        self._current_thinking_buffer: list[str] = []
        self._current_text_buffer: list[str] = []
        
        # Note: We no longer switch working directory here.
        # The work_dir is managed by the soul's session and tools use absolute paths.
        # This allows starting the server from any directory without polluting it.
        work_dir = None
        if hasattr(self.soul, '_runtime') and self.soul._runtime.session:
            work_dir = str(self.soul._runtime.session.work_dir)
            print(f"[_process_message] Using work_dir: {work_dir}")
            logger.info(f"Using work_dir: {work_dir}")
        
        async def _run_with_retry(max_retries: int = 1):
            """Run soul with automatic token refresh retry on 401."""
            # Get wire_file from soul's session for persistence
            wire_file = None
            if hasattr(self.soul, '_runtime') and self.soul._runtime.session:
                wire_file = self.soul._runtime.session.wire_file
                logger.debug(f"[SESSION] Using wire_file: {wire_file}")
            
            for attempt in range(max_retries + 1):
                try:
                    # Use refreshing context manager to auto-refresh OAuth token during long operations
                    if hasattr(self.soul, '_runtime') and self.soul._runtime.oauth:
                        async with self.soul._runtime.oauth.refreshing(self.soul._runtime):
                            await run_soul(
                                self.soul,
                                message_text,
                                self._wire_loop_text_parts,
                                self._cancel_event,
                                wire_file=wire_file,  # ← 传递 wire_file 实现持久化
                            )
                    else:
                        await run_soul(
                            self.soul,
                            message_text,
                            self._wire_loop_text_parts,
                            self._cancel_event,
                            wire_file=wire_file,  # ← 传递 wire_file 实现持久化
                        )
                    return  # Success
                    
                except Exception as e:
                    error_msg = str(e)
                    # Only treat as OAuth error if it's specifically about OAuth/unauthorized
                    # Don't retry on generic API key errors like "API Key appears to be invalid"
                    is_oauth_error = (
                        "invalid_authentication" in error_msg 
                        or "unauthorized" in error_msg.lower()
                        or "token expired" in error_msg.lower()
                        or "token invalid" in error_msg.lower()
                    )
                    # 401 could be OAuth or API key - check if current model uses OAuth
                    has_oauth = False
                    if hasattr(self.soul, '_runtime') and self.soul._runtime.llm:
                        model_config = self.soul._runtime.llm.model_config
                        if model_config:
                            provider = self.soul._runtime.config.providers.get(model_config.provider)
                            if provider and provider.oauth:
                                has_oauth = True
                    is_auth_error = is_oauth_error or ("401" in error_msg and has_oauth)
                    
                    if is_auth_error and attempt < max_retries:
                        print(f"[_process_message] OAuth token expired (attempt {attempt + 1}), refreshing...")
                        logger.warning(f"OAuth token expired, attempting refresh (attempt {attempt + 1})")
                        
                        # Force token refresh
                        if hasattr(self.soul, '_runtime') and self.soul._runtime.oauth:
                            try:
                                await self.soul._runtime.oauth.ensure_fresh(self.soul._runtime)
                                print("[_process_message] Token refreshed, retrying...")
                                logger.info("Token refreshed, retrying request")
                                continue  # Retry
                            except Exception as refresh_error:
                                print(f"[_process_message] Token refresh failed: {refresh_error}")
                                logger.error(f"Token refresh failed: {refresh_error}")
                                raise  # Re-raise the original error
                    
                    raise  # Re-raise if not auth error or no retries left
        
        try:
            print("[_process_message] Starting run_soul...")
            await _run_with_retry(max_retries=1)
            print("[_process_message] run_soul completed successfully")
            
            # Note: Buffers are already flushed by _wire_loop_text_parts on TurnEnd
            # No need to flush again here to avoid duplicate messages
            
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
            try:
                await self._flush_text_buffers()
            except Exception as flush_err:
                print(f"[_process_message] Error flushing buffers: {flush_err}")
                logger.warning(f"Error flushing buffers: {flush_err}")
            
            # Send user-friendly error message
            try:
                user_friendly_msg = self._get_user_friendly_error(error_type, error_msg)
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    user_friendly_msg,
                )
            except Exception as send_err:
                print(f"[_process_message] Error sending error message: {send_err}")
                logger.error(f"Error sending error message: {send_err}")
                # Fallback error message
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    f"❌ 处理消息时出错: {error_type}",
                )
        finally:
            # Cleanup is handled automatically since we don't change working directory
            pass
    
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
            return "❌ **网络错误**: 连接失败或超时\n\n请检查网络连接后重试。"
        
        # Default error
        return f"❌ **处理出错**: {error_msg[:200]}\n\n请重试或联系支持。"
    
    async def _wire_loop(self, wire: Wire) -> None:
        """Process wire messages during soul execution."""
        from kimi_cli.wire.types import (
            ApprovalRequest,
            StepBegin,
            StepInterrupted,
            SubagentEvent,
            TextPart,
            ThinkPart,
            ToolCall,
            ToolResult,
            TurnBegin,
            TurnEnd,
        )
        
        wire_ui = wire.ui_side(merge=False)
        
        current_step = 0
        assistant_content: list[str] = []
        
        print("[_wire_loop] Starting wire loop...")
        
        try:
            while True:
                print("[_wire_loop] Waiting for message...")
                msg = await wire_ui.receive()
                print(f"[_wire_loop] Received message: {type(msg).__name__}")
                
                if isinstance(msg, TurnBegin):
                    print("[_wire_loop] TurnBegin received")
                    assistant_content = []
                    
                elif isinstance(msg, StepBegin):
                    current_step = msg.n
                    
                elif isinstance(msg, StepInterrupted):
                    print("[_wire_loop] StepInterrupted received")
                    
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
                    # Check if YOLO mode is enabled (forced in Feishu mode by default)
                    if self._yolo_mode:
                        # YOLO mode: auto approve all tool calls
                        msg.resolve("approve")
                    else:
                        # Non-YOLO mode: send approval card and wait for user response
                        await self._handle_approval_request(msg)
                        
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
    
    async def _handle_approval_request(self, msg: Any) -> None:
        """Handle approval request by sending an interactive card to the user.
        
        This method is called when YOLO mode is disabled and a tool needs user approval.
        It sends a card with three options:
        1. Approve once - allow this single execution
        2. Approve for this conversation - always allow this action
        3. Reject - deny this execution
        
        The user can click buttons on the card to respond. Card callbacks are
        received via WebSocket and handled by handle_card_action method.
        """
        from kimi_cli.feishu.card_builder import build_approval_card
        
        request_id = msg.id
        tool_name = msg.sender
        description = msg.description
        
        print(f"[_handle_approval] Request {request_id}: {tool_name} - {description[:50]}...")
        logger.info(f"Approval request {request_id}: {tool_name}")
        
        # Store the pending request
        self._pending_approvals[request_id] = msg
        print(f"[_handle_approval] Stored request {request_id}, msg id: {id(msg)}, _future: {msg._future}")
        
        # Build and send approval card
        try:
            # Convert display blocks to dict format if present
            display_blocks = None
            if hasattr(msg, 'display') and msg.display:
                display_blocks = [
                    {"type": block.type, "content": block.content}
                    for block in msg.display
                    if hasattr(block, 'content')
                ]
            
            card = build_approval_card(
                tool_name=tool_name,
                description=description,
                request_id=request_id,
                display_blocks=display_blocks,
            )
            
            # Send the approval card
            card_message_id = await asyncio.to_thread(
                self.client.send_interactive_card,
                self.chat_id,
                card,
            )
            
            print(f"[_handle_approval] Approval card sent: {card_message_id}")
            logger.info(f"Approval card sent for request {request_id}")
            
            # Wait for user to click card button
            # The request will be resolved by handle_card_action when user clicks
            print("[_handle_approval] Waiting for user approval via card button...")
            
            try:
                # Wait for the approval response (resolved by handle_card_action)
                print(f"[_handle_approval] About to call wait() for {request_id}, _future id: {id(msg._future) if msg._future else 'None'}")
                response = await msg.wait()
                print(f"[_handle_approval] Got response: {response} for {request_id}")
                logger.info(f"Approval request {request_id} resolved with: {response}")
                
                # Update card to show result
                if response == "approve":
                    result_card = build_approval_result_card(tool_name, approved=True)
                elif response == "approve_for_session":
                    result_card = build_approval_result_card(tool_name, approved=True, is_session_approval=True)
                else:
                    result_card = build_approval_result_card(tool_name, approved=False)
                
                await asyncio.to_thread(
                    self.client.update_interactive_card,
                    card_message_id,
                    result_card,
                )
                
            except Exception as wait_err:
                print(f"[_handle_approval] Error waiting for approval: {wait_err}")
                logger.exception(f"Error waiting for approval: {wait_err}")
                # Auto-approve on error to prevent blocking
                if hasattr(msg, 'resolved') and not msg.resolved:
                    msg.resolve("approve")
            finally:
                # Clean up pending approval (use pop to avoid KeyError if already removed)
                # Note: handle_card_action now removes the request immediately to prevent race conditions
                self._pending_approvals.pop(request_id, None)
                    
        except Exception as e:
            logger.exception(f"Error handling approval request: {e}")
            # In case of error, auto-approve to prevent blocking
            if hasattr(msg, 'resolved') and not msg.resolved:
                msg.resolve("approve")
            self._pending_approvals.pop(request_id, None)
    
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
                print("[_upload_and_send_file] Upload failed")
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
                print("[_upload_and_send_image] Upload failed")
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
    
    async def _send_screenshot_to_feishu(self, data_url: str) -> None:
        """Send a screenshot (base64 data URL) to Feishu chat.
        
        This is used for MCP tools like midscene-android and chrome-devtools
        that return screenshots as base64 data URLs.
        
        Args:
            data_url: Base64 data URL like "data:image/png;base64,iVBORw0KG..."
        """
        import base64
        import re
        
        try:
            print("[_send_screenshot_to_feishu] Processing screenshot")
            
            # Parse data URL
            # Format: data:[<mediatype>][;base64],<data>
            match = re.match(r'data:(image/\w+);base64,(.+)', data_url)
            if not match:
                print("[_send_screenshot_to_feishu] Invalid data URL format")
                return
            
            mime_type = match.group(1)
            base64_data = match.group(2)
            
            # Determine file extension
            ext = 'png'
            if mime_type == 'image/jpeg' or mime_type == 'image/jpg':
                ext = 'jpg'
            elif mime_type == 'image/gif':
                ext = 'gif'
            elif mime_type == 'image/webp':
                ext = 'webp'
            
            # Decode base64
            image_content = base64.b64decode(base64_data)
            image_size = len(image_content)
            
            print(f"[_send_screenshot_to_feishu] Screenshot size: {image_size} bytes ({mime_type})")
            
            # Upload image
            image_key = await asyncio.to_thread(
                self.client.upload_image,
                image_content,
                f"screenshot.{ext}",
            )
            
            if image_key:
                # Send image message
                msg_id = await asyncio.to_thread(
                    self.client.send_image_message,
                    self.chat_id,
                    image_key,
                )
                print(f"[_send_screenshot_to_feishu] Screenshot sent: {msg_id}")
            else:
                print("[_send_screenshot_to_feishu] Upload failed")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    self.chat_id,
                    "❌ 截图上传失败",
                )
                
        except Exception as e:
            print(f"[_send_screenshot_to_feishu] Error: {e}")
            logger.exception(f"Error sending screenshot: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                self.chat_id,
                f"❌ 发送截图出错: {str(e)[:100]}",
            )
    
    async def _wire_loop_text_parts(self, wire: Wire) -> None:
        """Wire loop that sends different parts as separate messages."""
        from kimi_cli.wire.types import (
            ApprovalRequest,
            ImageURLPart,
            StepInterrupted,
            SubagentEvent,
            TextPart,
            ThinkPart,
            ToolCall,
            ToolCallPart,
            ToolResult,
            TurnBegin,
            TurnEnd,
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
                    print(f"[_wire_loop_text_parts] Sending thinking card: {len(content)} chars")
                    try:
                        # Use card renderer for thinking
                        card = self._renderer.render_thought(content)
                        msg_id = await asyncio.to_thread(
                            self.client.send_interactive_card,
                            self.chat_id,
                            card,
                        )
                        print(f"[_wire_loop_text_parts] Thinking card sent: {msg_id}")
                    except Exception as e:
                        print(f"[_wire_loop_text_parts] Error sending thinking card: {e}")
                        # Fallback to text message
                        try:
                            msg_id = await asyncio.to_thread(
                                self.client.send_text_message,
                                self.chat_id,
                                f"💭 [思考过程]\n{content[:1500]}",
                            )
                            print(f"[_wire_loop_text_parts] Thinking text sent (fallback): {msg_id}")
                        except Exception as e2:
                            logger.exception(f"Error sending thinking to Feishu: {e2}")
                self._current_thinking_buffer = []
        
        async def send_text():
            print("[_wire_loop_text_parts] send_text() called")
            if not hasattr(self, '_current_text_buffer'):
                print("[_wire_loop_text_parts] WARNING: _current_text_buffer not initialized")
                self._current_text_buffer = []
                return
            
            buffer_size = len(self._current_text_buffer)
            print(f"[_wire_loop_text_parts] Buffer size: {buffer_size}")
            
            if not self._current_text_buffer:
                print("[_wire_loop_text_parts] Buffer is empty, nothing to send")
                return
            
            content = "".join(self._current_text_buffer).strip()
            print(f"[_wire_loop_text_parts] Content length after strip: {len(content)}")
            
            if not content:
                print("[_wire_loop_text_parts] Content is empty after strip()")
                self._current_text_buffer = []
                return
            
            try:
                # Split content into chunks if too long
                chunks = split_content_for_cards(content, max_length=8000)
                total = len(chunks)
                print(f"[_wire_loop_text_parts] Content split into {total} chunk(s)")
                
                if total == 0:
                    print("[_wire_loop_text_parts] WARNING: split_content_for_cards returned empty list")
                    # Send original content as single chunk
                    chunks = [content]
                    total = 1
                
                for i, chunk in enumerate(chunks, 1):
                    print(f"[_wire_loop_text_parts] Sending response card {i}/{total}: {len(chunk)} chars")
                    try:
                        # Use card renderer for response with pagination info
                        page_info = {"current": i, "total": total} if total > 1 else None
                        card = self._renderer.render_text_response(chunk, page_info=page_info)
                        msg_id = await asyncio.to_thread(
                            self.client.send_interactive_card,
                            self.chat_id,
                            card,
                        )
                        print(f"[_wire_loop_text_parts] Response card {i}/{total} sent: {msg_id}")
                    except Exception as e:
                        print(f"[_wire_loop_text_parts] Error sending response card {i}/{total}: {e}")
                        logger.exception(f"Error sending response card: {e}")
                        # Fallback to text message for this chunk
                        try:
                            max_len = 1500
                            prefix = f"🤖 [回复内容 ({i}/{total})]\n"
                            for j in range(0, len(chunk), max_len):
                                sub_chunk = chunk[j:j+max_len]
                                msg_id = await asyncio.to_thread(
                                    self.client.send_text_message,
                                    self.chat_id,
                                    prefix + sub_chunk if j == 0 else f"(续){sub_chunk}",
                                )
                                print(f"[_wire_loop_text_parts] Text chunk sent (fallback): {msg_id}")
                        except Exception as e2:
                            logger.exception(f"Error sending text to Feishu: {e2}")
                
                self._current_text_buffer = []
            except Exception as e:
                print(f"[_wire_loop_text_parts] Error in send_text: {e}")
                logger.exception(f"Error in send_text: {e}")
                raise  # Re-raise to be handled by caller
        
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
                        print(f"[_wire_loop_text_parts] TextPart received: {len(msg.text)} chars")
                        self._current_text_buffer.append(msg.text)
                        # Only send text if not in thinking mode (to avoid interleaving)
                        # If we have thinking content pending, wait for it to complete first
                        has_thinking = self._current_thinking_buffer and any(
                            self._current_thinking_buffer
                        )
                        print(f"[_wire_loop_text_parts] has_thinking={has_thinking}, buffer_size={len(self._current_text_buffer)}")
                        if not has_thinking:
                            total_chars = sum(len(t) for t in self._current_text_buffer)
                            # Lower threshold for slash command responses (usually short)
                            # to ensure they are sent immediately
                            if total_chars > 100:
                                print(f"[_wire_loop_text_parts] Sending text (>{100} chars)")
                                await send_text()
                            else:
                                print(f"[_wire_loop_text_parts] Buffering text ({total_chars} chars), waiting for more...")
                        
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
                        print("[_wire_loop_text_parts] Warning: ToolCallPart without matching ToolCall")
                    
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
                    
                    # Send tool call card
                    print(f"[_wire_loop_text_parts] Sending tool call card: {func_name}")
                    try:
                        tool_call_card = self._renderer.render_tool_call(
                            tool_name=func_name,
                            arguments=args_obj if 'args_obj' in dir() else func_args_display,
                            tool_call_id=tool_call_id,
                        )
                        msg_id = await asyncio.to_thread(
                            self.client.send_interactive_card,
                            self.chat_id,
                            tool_call_card,
                        )
                        print(f"[_wire_loop_text_parts] Tool call card sent: {msg_id}")
                    except Exception as e:
                        print(f"[_wire_loop_text_parts] Error sending tool call card: {e}")
                        # Fallback to text
                        msg_id = await asyncio.to_thread(
                            self.client.send_text_message,
                            self.chat_id,
                            f"🔧 [工具调用]\n名称: {func_name}\n参数: {func_args_display[:800]}",
                        )
                        print(f"[_wire_loop_text_parts] Tool call text sent (fallback): {msg_id}")
                    
                    # Send tool result (split if too long)
                    # Extract result text with priority: brief > message > output > str(return_value)
                    # Handle ImageURLPart with base64 data (e.g., from midscene-android screenshots)
                    result_text = ""
                    has_image = False
                    screenshot_urls = []  # Collect base64 screenshots to send
                    
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
                                    image_url = part.image_url.url if part.image_url else ""
                                    # Check if it's a base64 data URL (screenshot from MCP tools)
                                    if image_url.startswith('data:image'):
                                        screenshot_urls.append(image_url)
                                        continue  # Skip from text output
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
                    
                    # Send screenshots if any
                    for screenshot_url in screenshot_urls:
                        await self._send_screenshot_to_feishu(screenshot_url)
                    
                    # Add note if image was present
                    if has_image and screenshot_urls:
                        result_text = "[截图已发送] " + (result_text if result_text else "")
                    
                    print(f"[_wire_loop_text_parts] Sending tool result: {len(result_text)} chars")
                    
                    # Warn if result is empty (but only if it's truly empty, not just "None" or "")
                    if not result_text or len(result_text.strip()) == 0 or result_text.strip() in ('None', 'null', 'False', '[]', '{}', '[图片内容已过滤]'):
                        print("[_wire_loop_text_parts] WARNING: Tool result is empty!")
                        empty_card = self._renderer.render_error(
                            error_message=f"工具 `{func_name}` 返回了空结果，可能需要重试或检查输入",
                            error_type="工具返回为空",
                        )
                        msg_id = await asyncio.to_thread(
                            self.client.send_interactive_card,
                            self.chat_id,
                            empty_card,
                        )
                        print(f"[_wire_loop_text_parts] Empty result warning sent: {msg_id}")
                    else:
                        # Send tool result card
                        print(f"[_wire_loop_text_parts] Sending tool result card: {len(result_text)} chars")
                        try:
                            tool_result_card = self._renderer.render_tool_result(
                                tool_call_id=tool_call_id or "",
                                result=result_text,
                                tool_name=func_name,
                            )
                            msg_id = await asyncio.to_thread(
                                self.client.send_interactive_card,
                                self.chat_id,
                                tool_result_card,
                            )
                            print(f"[_wire_loop_text_parts] Tool result card sent: {msg_id}")
                        except Exception as e:
                            print(f"[_wire_loop_text_parts] Error sending tool result card: {e}")
                            # Fallback to text
                            max_len = 1500
                            prefix = "📊 [工具返回]\n"
                            for i in range(0, len(result_text), max_len):
                                chunk = result_text[i:i+max_len]
                                msg_id = await asyncio.to_thread(
                                    self.client.send_text_message,
                                    self.chat_id,
                                    prefix + chunk if i == 0 else f"(续){chunk}",
                                )
                                print(f"[_wire_loop_text_parts] Tool result text sent (fallback): {msg_id}")
                    
                elif isinstance(msg, StepInterrupted):
                    print("[_wire_loop_text_parts] StepInterrupted received")
                    # Step was interrupted, flush buffers to show what we have so far
                    await send_thinking()
                    await send_text()
                    
                elif isinstance(msg, TurnEnd):
                    print("[_wire_loop_text_parts] TurnEnd received, flushing buffers...")
                    print(f"[_wire_loop_text_parts] thinking_buffer={len(self._current_thinking_buffer)}, text_buffer={len(self._current_text_buffer)}")
                    # Flush remaining buffers - use try/except to ensure both are attempted
                    try:
                        await send_thinking()
                    except Exception as e:
                        print(f"[_wire_loop_text_parts] Error in send_thinking: {e}")
                    try:
                        await send_text()
                    except Exception as e:
                        print(f"[_wire_loop_text_parts] Error in send_text: {e}")
                    print("[_wire_loop_text_parts] Buffers flushed")
                    
                elif isinstance(msg, SubagentEvent):
                    # Handle subagent events from Task tool
                    print(f"[_wire_loop_text_parts] SubagentEvent received: {type(msg.event).__name__}")
                    
                    # Extract the actual event from SubagentEvent
                    subagent_msg = msg.event
                    
                    if isinstance(subagent_msg, TextPart):
                        if subagent_msg.text:
                            print(f"[_wire_loop_text_parts] Subagent text: {len(subagent_msg.text)} chars")
                            # Send subagent text as card
                            try:
                                card = self._renderer.render_text_response(f"📎 [Subagent]\n\n{subagent_msg.text}")
                                await asyncio.to_thread(
                                    self.client.send_interactive_card,
                                    self.chat_id,
                                    card,
                                )
                            except Exception as e:
                                print(f"[_wire_loop_text_parts] Error sending subagent text card: {e}")
                                await asyncio.to_thread(
                                    self.client.send_text_message,
                                    self.chat_id,
                                    f"📎 [Subagent] {subagent_msg.text[:1500]}",
                                )
                    elif isinstance(subagent_msg, ThinkPart):
                        if subagent_msg.think:
                            print(f"[_wire_loop_text_parts] Subagent thinking: {len(subagent_msg.think)} chars")
                            # Send subagent thinking as card
                            try:
                                card = self._renderer.render_thought(f"[Subagent 思考]\n\n{subagent_msg.think}")
                                await asyncio.to_thread(
                                    self.client.send_interactive_card,
                                    self.chat_id,
                                    card,
                                )
                            except Exception as e:
                                print(f"[_wire_loop_text_parts] Error sending subagent thinking card: {e}")
                    elif isinstance(subagent_msg, ToolCall):
                        # Subagent tool call
                        func_name = subagent_msg.function.name if subagent_msg.function else 'unknown'
                        print(f"[_wire_loop_text_parts] Subagent tool call: {func_name}")
                        try:
                            card = self._renderer.render_tool_call(
                                tool_name=f"[Subagent] {func_name}",
                                arguments=subagent_msg.function.arguments if subagent_msg.function else "{}",
                            )
                            await asyncio.to_thread(
                                self.client.send_interactive_card,
                                self.chat_id,
                                card,
                            )
                        except Exception as e:
                            print(f"[_wire_loop_text_parts] Error sending subagent tool call card: {e}")
                            await asyncio.to_thread(
                                self.client.send_text_message,
                                self.chat_id,
                                f"🔧 [Subagent 工具调用] {func_name}",
                            )
                    elif isinstance(subagent_msg, ToolResult):
                        # Subagent tool result
                        # Handle ImageURLPart with base64 data (screenshots)
                        result_text = ""
                        has_image = False
                        screenshot_urls = []
                        
                        if hasattr(subagent_msg.return_value, 'brief') and subagent_msg.return_value.brief:
                            result_text = str(subagent_msg.return_value.brief)
                        elif hasattr(subagent_msg.return_value, 'message') and subagent_msg.return_value.message:
                            result_text = str(subagent_msg.return_value.message)
                        elif hasattr(subagent_msg.return_value, 'output') and subagent_msg.return_value.output:
                            output = subagent_msg.return_value.output
                            if isinstance(output, list):
                                filtered_parts = []
                                for part in output:
                                    if isinstance(part, ImageURLPart):
                                        has_image = True
                                        image_url = part.image_url.url if part.image_url else ""
                                        if image_url.startswith('data:image'):
                                            screenshot_urls.append(image_url)
                                            continue
                                        else:
                                            filtered_parts.append(part)
                                    else:
                                        filtered_parts.append(part)
                                result_text = " ".join(str(p) for p in filtered_parts if not isinstance(p, ImageURLPart))
                            else:
                                result_text = str(subagent_msg.return_value)
                        else:
                            result_text = str(subagent_msg.return_value)
                        
                        # Send screenshots if any
                        for screenshot_url in screenshot_urls:
                            await self._send_screenshot_to_feishu(screenshot_url)
                        
                        # Add note if image was present
                        if has_image and screenshot_urls:
                            result_text = "[截图已发送] " + (result_text if result_text else "")
                        
                        print(f"[_wire_loop_text_parts] Subagent tool result: {len(result_text)} chars")
                        try:
                            card = self._renderer.render_tool_result(
                                tool_call_id=subagent_msg.tool_call_id if hasattr(subagent_msg, 'tool_call_id') else "",
                                result=result_text,
                                tool_name="[Subagent] 工具结果",
                            )
                            await asyncio.to_thread(
                                self.client.send_interactive_card,
                                self.chat_id,
                                card,
                            )
                        except Exception as e:
                            print(f"[_wire_loop_text_parts] Error sending subagent tool result card: {e}")
                            await asyncio.to_thread(
                                self.client.send_text_message,
                                self.chat_id,
                                f"📊 [Subagent 结果] {result_text[:800]}",
                            )
                    else:
                        print(f"[_wire_loop_text_parts] Unhandled subagent event: {type(subagent_msg).__name__}")
                
                elif isinstance(msg, ApprovalRequest):
                    # Check if YOLO mode is enabled and request is not mandatory
                    if self._yolo_mode and not msg.mandatory:
                        # YOLO mode: auto approve non-mandatory tool calls
                        msg.resolve("approve")
                    else:
                        # Non-YOLO mode or mandatory request: send approval card and wait for user response
                        await self._handle_approval_request(msg)
                    
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
    
    async def _send_fallback_message(self, msg: str) -> None:
        """Send a fallback message for commands that should be handled by SDKMessageHandler."""
        await asyncio.to_thread(
            self.client.send_text_message,
            self.chat_id,
            f"⚠️ {msg}\n\n"
            f"如果此命令持续无效，请检查：\n"
            f"1. 命令拼写是否正确\n"
            f"2. 是否有多余空格\n"
            f"3. 重新发送 `/help` 查看可用命令",
        )
    
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
        server: FeishuSDKServer | None = None,
    ):
        self.client = client
        self.config = config
        self.feishu_config = feishu_config
        self._server = server
        self._sessions: dict[str, SDKChatSession] = {}
        self._lock = asyncio.Lock()
        # Track linked CLI sessions: session_key -> session_id
        self._linked_sessions: dict[str, str] = {}
    
    def _get_session_key(self, chat_id: str, user_id: str) -> str:
        """Get unique session key."""
        return f"{chat_id}:{user_id}"
    
    async def _get_or_create_session_for_media(
        self,
        chat_id: str,
        user_id: str,
        session_key: str,
    ) -> SDKChatSession | None:
        """Get existing session or create new one for buffering media.
        
        This is used when receiving images/files without text - we need
        a session to store the buffer, but we don't trigger Agent processing.
        
        Returns:
            SDKChatSession if successful, None if failed
        """
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is not None:
                return session
            
            # Session doesn't exist, create a new one
            print(f"[HANDLER] Creating new session for media buffering: {session_key}")
            logger.info(f"[HANDLER] Creating new session for media buffering: {session_key}")
            
            try:
                # Check if there's a linked CLI session
                linked_session_id = self._linked_sessions.get(session_key)
                
                if linked_session_id:
                    # Try to load existing CLI session
                    soul = await self._create_soul_from_session_id(linked_session_id)
                    if soul:
                        print(f"[HANDLER] Loaded CLI session: {linked_session_id}")
                        logger.info(f"[HANDLER] Loaded CLI session: {linked_session_id}")
                    else:
                        # Fall back to new session
                        print(f"[HANDLER] Failed to load session {linked_session_id}, creating new")
                        soul = await self._create_soul_for_session(session_key)
                else:
                    # Create new soul for this chat session
                    soul = await self._create_soul_for_session(session_key)
                
                session = SDKChatSession(
                    chat_id=chat_id,
                    user_id=user_id,
                    client=self.client,
                    config=self.config,
                    soul=soul,
                )
                self._sessions[session_key] = session
                print("[HANDLER] New session created for media buffering")
                logger.info("[HANDLER] New session created for media buffering")
                return session
                
            except Exception as e:
                print(f"[HANDLER ERROR] Failed to create session for media: {e}")
                logger.exception(f"[HANDLER] Failed to create session for media: {e}")
                return None
    
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
            # Use current working directory where kimi feishu was started
            work_dir = os.getcwd()
        
        # Ensure directory exists
        os.makedirs(work_dir, exist_ok=True)
        return work_dir
    
    def _get_work_dir_kaos(self) -> KaosPath:
        """Get the working directory as KaosPath."""
        import os
        
        if self.feishu_config and self.feishu_config.work_dir:
            work_dir = KaosPath(self.feishu_config.work_dir)
        else:
            work_dir = KaosPath(os.getcwd())
        
        os.makedirs(str(work_dir), exist_ok=True)
        return work_dir
    
    async def _list_user_sessions(self) -> list[dict]:
        """List all available CLI sessions for the user.
        
        Returns:
            List of session info dicts with id, title, updated_at, work_dir
        """
        from datetime import datetime

        from kimi_cli.metadata import load_metadata
        
        sessions = []
        metadata = load_metadata()
        
        # Get work directories from metadata
        for wd_meta in metadata.work_dirs:
            sessions_dir = wd_meta.sessions_dir
            if not sessions_dir.exists():
                continue
            
            # List all session directories
            for session_dir in sessions_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                
                session_id = session_dir.name
                context_file = session_dir / "context.jsonl"
                metadata_file = session_dir / "metadata.json"
                
                # Skip sessions without context
                if not context_file.exists():
                    continue
                
                # Load metadata if exists
                title = "Untitled"
                if metadata_file.exists():
                    try:
                        import json
                        meta = json.loads(metadata_file.read_text())
                        title = meta.get("title", "Untitled")
                    except:
                        pass
                
                # Get last modified time
                try:
                    updated_at = datetime.fromtimestamp(
                        context_file.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M")
                except:
                    updated_at = "Unknown"
                
                # Get message count from context file
                msg_count = 0
                try:
                    with open(context_file) as f:
                        for line in f:
                            if line.strip():
                                msg_count += 1
                except:
                    pass
                
                sessions.append({
                    "id": session_id,
                    "short_id": session_id[:8],
                    "title": title,
                    "updated_at": updated_at,
                    "work_dir": wd_meta.path,
                    "message_count": msg_count,
                })
        
        # Sort by updated_at descending
        sessions.sort(key=lambda x: x["updated_at"], reverse=True)
        return sessions
    
    async def _create_soul_from_session_id(self, session_id: str) -> KimiSoul | None:
        """Create a KimiSoul from an existing CLI session ID.
        
        Args:
            session_id: The CLI session ID to load
            
        Returns:
            KimiSoul if successful, None if session not found
        """
        from kimi_cli.auth.oauth import OAuthManager
        from kimi_cli.llm import augment_provider_with_env_vars, create_llm
        
        kimi_config = load_config()
        work_dir = self._get_work_dir_kaos()
        
        # Try to find the session
        existing_session = await Session.find(work_dir, session_id)
        
        if existing_session is None:
            # Try searching in all work directories
            from kimi_cli.metadata import load_metadata
            metadata = load_metadata()
            for wd_meta in metadata.work_dirs:
                existing_session = await Session.find(KaosPath(wd_meta.path), session_id)
                if existing_session:
                    work_dir = KaosPath(wd_meta.path)
                    break
        
        if existing_session is None:
            return None
        
        logger.info(f"[HANDLER] Loading existing session: {session_id}")
        
        # Use the existing session
        session = existing_session
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
        
        # Determine skills_dir from feishu config
        skills_dir = None
        if self.feishu_config and self.feishu_config.skills_dir:
            skills_dir = KaosPath(self.feishu_config.skills_dir)
        
        runtime = await Runtime.create(
            config=kimi_config,
            oauth=oauth,
            llm=llm,
            session=session,
            yolo=self.config.auto_approve,  # Use config setting for YOLO mode
            skills_dir=skills_dir,
        )
        
        # Load MCP configs from global mcp.json
        mcp_configs = self._load_mcp_configs()
        
        agent = await load_agent(DEFAULT_AGENT_FILE, runtime, mcp_configs=mcp_configs)
        
        # Start MCP tools connection in background to avoid blocking first query
        if mcp_configs and hasattr(agent.toolset, 'wait_for_mcp_tools'):
            logger.info("[HANDLER] Starting MCP tools connection in background...")
            
            async def _connect_mcp_tools():
                try:
                    await asyncio.wait_for(
                        agent.toolset.wait_for_mcp_tools(),
                        timeout=60.0  # Longer timeout for background connection
                    )
                    logger.info("[HANDLER] MCP tools connected successfully")
                except TimeoutError:
                    logger.warning("[HANDLER] Timeout waiting for MCP tools, some tools may be unavailable")
                except Exception as e:
                    logger.warning(f"[HANDLER] Error waiting for MCP tools: {e}")
            
            # Start background task without awaiting
            asyncio.create_task(_connect_mcp_tools())
            logger.info("[HANDLER] MCP tools connection started in background")
        
        # Restore context from existing session
        context = Context(session.context_file)
        await context.restore()
        
        soul = KimiSoul(agent, context=context)
        
        # Set work_dir on client for tools to use
        self.client.set_work_dir(str(work_dir))
        
        return soul
    
    async def _create_soul_for_session(self, session_key: str) -> KimiSoul:
        """Create a new KimiSoul for a chat session."""
        import os

        from kimi_cli.auth.oauth import OAuthManager
        from kimi_cli.llm import augment_provider_with_env_vars, create_llm
        
        kimi_config = load_config()
        
        # Create work directory for this session
        # Use configured work_dir if available, otherwise use default workspace
        if self.feishu_config and self.feishu_config.work_dir:
            work_dir = KaosPath(self.feishu_config.work_dir)
            # Ensure the directory exists
            os.makedirs(str(work_dir), exist_ok=True)
        else:
            # Use current working directory where kimi feishu was started
            work_dir = KaosPath(os.getcwd())
            os.makedirs(str(work_dir), exist_ok=True)
        
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
        
        # Determine skills_dir from feishu config
        skills_dir = None
        if self.feishu_config and self.feishu_config.skills_dir:
            skills_dir = KaosPath(self.feishu_config.skills_dir)
        
        runtime = await Runtime.create(
            config=kimi_config,
            oauth=oauth,
            llm=llm,
            session=session,
            yolo=self.config.auto_approve,  # Use config setting for YOLO mode
            skills_dir=skills_dir,
        )
        
        # Load MCP configs from global mcp.json
        mcp_configs = self._load_mcp_configs()
        
        agent = await load_agent(DEFAULT_AGENT_FILE, runtime, mcp_configs=mcp_configs)
        
        # Start MCP tools connection in background to avoid blocking first query
        if mcp_configs and hasattr(agent.toolset, 'wait_for_mcp_tools'):
            logger.info("[HANDLER] Starting MCP tools connection in background...")
            
            async def _connect_mcp_tools():
                try:
                    await asyncio.wait_for(
                        agent.toolset.wait_for_mcp_tools(),
                        timeout=60.0  # Longer timeout for background connection
                    )
                    logger.info("[HANDLER] MCP tools connected successfully")
                except TimeoutError:
                    logger.warning("[HANDLER] Timeout waiting for MCP tools, some tools may be unavailable")
                except Exception as e:
                    logger.warning(f"[HANDLER] Error waiting for MCP tools: {e}")
            
            # Start background task without awaiting
            asyncio.create_task(_connect_mcp_tools())
            logger.info("[HANDLER] MCP tools connection started in background")
        
        # Create isolated context for this session
        context = Context(session.context_file)
        await context.restore()
        
        soul = KimiSoul(agent, context=context)
        
        # Set work_dir on client for tools to use
        self.client.set_work_dir(str(work_dir))
        
        return soul
    
    async def _handle_sessions_command(self, chat_id: str) -> None:
        """Handle /sessions command to list available CLI sessions."""
        await asyncio.to_thread(
            self.client.send_text_message,
            chat_id,
            "📋 正在获取您的 CLI sessions...",
        )
        
        try:
            sessions = await self._list_user_sessions()
            
            if not sessions:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "📭 暂无 CLI sessions\n\n"
                    "在电脑端使用 `kimi chat` 开始对话后，\n"
                    "您可以在这里用 `/continue <session_id>` 接续。",
                )
                return
            
            # Format sessions list
            lines = [f"📚 找到 {len(sessions)} 个 CLI sessions：\n"]
            
            for i, s in enumerate(sessions[:10], 1):  # Show top 10
                title = s['title'] if s['title'] != 'Untitled' else '(无标题)'
                lines.append(
                    f"{i}. `{s['short_id']}` - {title}\n"
                    f"   📁 {s['work_dir'][:40]}...\n"
                    f"   🕐 {s['updated_at']} | 💬 {s['message_count']} 条消息\n"
                )
            
            if len(sessions) > 10:
                lines.append(f"\n... 还有 {len(sessions) - 10} 个 sessions")
            
            lines.append("\n💡 使用 `/continue <session_id>` 接续指定会话")
            lines.append("💡 例如：`/continue abc123`")
            
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                "\n".join(lines),
            )
            
        except Exception as e:
            logger.exception(f"[HANDLER] Failed to list sessions: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"❌ 获取 sessions 失败: {str(e)[:100]}",
            )
    
    async def _handle_continue_command(
        self, 
        chat_id: str, 
        user_id: str, 
        session_key: str, 
        session_id: str
    ) -> None:
        """Handle /continue command to attach to an existing CLI session."""
        await asyncio.to_thread(
            self.client.send_text_message,
            chat_id,
            f"🔗 正在接续 session `{session_id[:8]}`...",
        )
        
        try:
            # Close existing session if any
            async with self._lock:
                existing_session = self._sessions.get(session_key)
                if existing_session:
                    del self._sessions[session_key]
                    logger.info(f"[HANDLER] Closed existing session for {session_key}")
            
            # Try to find the full session ID (support short ID matching)
            sessions = await self._list_user_sessions()
            full_session_id = None
            session_info = None
            
            for s in sessions:
                if s['id'].startswith(session_id) or s['short_id'] == session_id:
                    full_session_id = s['id']
                    session_info = s
                    break
            
            if not full_session_id:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    f"❌ 未找到 session `{session_id}`\n\n"
                    f"使用 `/sessions` 查看可用 sessions",
                )
                return
            
            # Create soul from existing session
            soul = await self._create_soul_from_session_id(full_session_id)
            
            if not soul:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    f"❌ 无法加载 session `{session_id}`\n"
                    f"可能已被删除或损坏。",
                )
                return
            
            # Create new SDKChatSession with loaded soul
            session = SDKChatSession(
                chat_id=chat_id,
                user_id=user_id,
                client=self.client,
                config=self.config,
                soul=soul,
            )
            
            # Store the session
            async with self._lock:
                self._sessions[session_key] = session
                self._linked_sessions[session_key] = full_session_id
            
            # Send success message
            title = session_info['title'] if session_info['title'] != 'Untitled' else '(无标题)'
            msg_count = session_info['message_count']
            
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"✅ 已成功接续 session！\n\n"
                f"📝 {title}\n"
                f"🆔 `{full_session_id[:8]}`\n"
                f"💬 历史消息: {msg_count} 条\n"
                f"🕐 最后更新: {session_info['updated_at']}\n\n"
                f"现在可以继续对话了！",
            )
            
            logger.info(f"[HANDLER] Successfully attached to session {full_session_id}")
            
        except Exception as e:
            logger.exception(f"[HANDLER] Failed to continue session: {e}")
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"❌ 接续 session 失败: {str(e)[:100]}",
            )
    
    async def _handle_link_command(self, chat_id: str, user_id: str, session_key: str) -> None:
        """Handle /link command to show current linked session."""
        linked_id = self._linked_sessions.get(session_key)
        
        if linked_id:
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"🔗 当前已关联 CLI session:\n"
                f"🆔 `{linked_id}`\n\n"
                f"在 CLI 中使用:\n"
                f"`kimi --session {linked_id}`",
            )
        else:
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                "ℹ️ 当前未关联 CLI session\n\n"
                "使用 `/sessions` 查看可用 sessions\n"
                "使用 `/continue <id>` 关联并接续",
            )
    
    async def _handle_id_command(self, chat_id: str, session_key: str) -> None:
        """Handle /id command to show current session ID."""
        async with self._lock:
            session = self._sessions.get(session_key)
            
            if session and hasattr(session.soul, '_runtime') and session.soul._runtime.session:
                session_id = session.soul._runtime.session.id
                work_dir = str(session.soul._runtime.session.work_dir)
                session_dir = str(session.soul._runtime.session.dir)
                
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    f"🆔 **当前 Session**\n\n"
                    f"**ID**: `{session_id}`\n"
                    f"**工作目录**: `{work_dir}`\n\n"
                    f"✅ **在 CLI 中接续（方式一 - 推荐）**:\n"
                    f"```\n"
                    f"cd {work_dir}\n"
                    f"kimi --session {session_id}\n"
                    f"```\n\n"
                    f"✅ **在 CLI 中接续（方式二 - 任意目录）**:\n"
                    f"```\n"
                    f"kimi --session {session_id} --work-dir {work_dir}\n"
                    f"```\n\n"
                    f"💡 **提示**: Session 文件存储在 `{session_dir}`",
                )
            else:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "ℹ️ 当前没有活跃的 session\n\n"
                    "发送任意消息开始对话",
                )
    
    def _load_mcp_configs(self) -> list[dict[str, Any]]:
        """Load MCP configs from global mcp.json file.
        
        Returns:
            List of MCP config dicts (each with 'mcpServers' key)
            
        Note:
            Set environment variable DISABLE_MCP=1 to disable MCP tools loading.
        """
        # Check if MCP is disabled via environment variable
        if os.environ.get("DISABLE_MCP", "0") == "1":
            logger.info("MCP tools disabled via DISABLE_MCP environment variable")
            return []
        
        try:
            # Import here to avoid circular import issues
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
    
    async def _get_quoted_message_content(self, message: Any) -> str | None:
        """获取被引用消息的内容
        
        当用户引用/回复一条消息时，尝试获取被引用消息的内容
        
        Args:
            message: 飞书消息对象
            
        Returns:
            被引用消息的文本内容，如果没有引用则返回 None
        """
        try:
            # 检查是否有 parent_id（表示这是一条回复消息）
            parent_id = getattr(message, 'parent_id', None)
            if not parent_id:
                # 某些版本可能使用 root_id
                parent_id = getattr(message, 'root_id', None)
            
            if not parent_id:
                return None
            
            print(f"[DEBUG] Found quoted message, parent_id: {parent_id}")
            
            # 使用 client 获取消息内容
            if not hasattr(self.client, 'get_message'):
                print("[DEBUG] Client does not have get_message method")
                return None
            
            print(f"[DEBUG] Fetching message content for {parent_id}")
            msg_data = await asyncio.to_thread(
                self.client.get_message,
                parent_id
            )
            
            print(f"[DEBUG] Got msg_data: {msg_data is not None}")
            print(f"[DEBUG] msg_data keys: {list(msg_data.keys()) if msg_data else 'None'}")
            print(f"[DEBUG] Full msg_data: {msg_data}")
            
            if not msg_data:
                print(f"[DEBUG] Failed to get message data for {parent_id}")
                # 即使获取失败，也返回一个提示，让对话可以继续
                return "[无法获取引用消息内容]"
            
            # 解析被引用消息的内容
            quoted_content = msg_data.get('content', '{}')
            quoted_msg_type = msg_data.get('msg_type', 'text')
            print(f"[DEBUG] Raw quoted_content: {quoted_content[:100]}...")
            print(f"[DEBUG] Raw quoted_msg_type: {quoted_msg_type}")
            
            try:
                content_dict = json.loads(quoted_content)
            except json.JSONDecodeError:
                content_dict = {}
            
            # 提取文本内容
            if quoted_msg_type == 'text':
                text_content = content_dict.get('text', '')
                if not text_content:
                    print(f"[DEBUG] Empty text content in message {parent_id}")
                    return "[引用消息内容为空 - 可能无权限查看或消息已过期]"
                return text_content
            
            elif quoted_msg_type == 'interactive':
                # 卡片消息，提取标题和内容摘要
                card_content = content_dict
                header = card_content.get('header', {})
                title = header.get('content', '') if isinstance(header, dict) else ''
                # 尝试多种可能的标题路径
                if not title and 'title' in header:
                    title_obj = header.get('title', {})
                    if isinstance(title_obj, dict):
                        title = title_obj.get('content', '')
                
                elements = card_content.get('elements', [])
                
                # 提取元素中的文本
                texts = []
                if title:
                    texts.append(f"【{title}】")
                
                # 处理嵌套数组结构 [[...]]
                def extract_text_from_elements(elems):
                    for elem in elems:
                        if isinstance(elem, list):
                            # 嵌套数组，递归处理
                            extract_text_from_elements(elem)
                        elif isinstance(elem, dict):
                            tag = elem.get('tag', '')
                            if tag in ('div', 'text', 'plain_text', 'lark_md'):
                                text_obj = elem.get('text', {})
                                if isinstance(text_obj, dict):
                                    text_content = text_obj.get('content', '')
                                else:
                                    text_content = str(text_obj)
                                if text_content:
                                    texts.append(text_content)
                            # 处理 title 标签
                            elif tag == 'title' and not title:
                                title_text = elem.get('content', '')
                                if title_text:
                                    texts.insert(0, f"【{title_text}】")
                
                extract_text_from_elements(elements)
                
                card_text = '\n'.join(texts) if texts else '[卡片消息]'
                
                # 检查是否是定时任务卡片，尝试读取关联的文件
                if '定时任务' in title or '任务' in title:
                    file_content = await self._load_scheduled_task_files(card_text)
                    if file_content:
                        card_text += f"\n\n[关联文件内容]:\n{file_content}"
                
                return card_text
            
            elif quoted_msg_type == 'file':
                # 文件消息，尝试获取文件信息
                file_key = content_dict.get('file_key', '')
                file_name = content_dict.get('file_name', '未知文件')
                
                # 对于文件消息，我们无法直接读取内容，但提供文件信息
                return f"[文件消息: {file_name}]\n(文件内容无法直接读取，请在对话中上传文件后提问)"
            
            elif quoted_msg_type == 'image':
                # 图片消息
                image_key = content_dict.get('image_key', '')
                return "[图片消息]\n(图片内容无法直接读取，请描述图片内容或重新上传)"
            
            else:
                return f'[{quoted_msg_type} 消息]'
                
        except Exception as e:
            logger.debug(f"Error getting quoted message content: {e}")
            return None
    
    async def _load_scheduled_task_files(self, card_text: str) -> str | None:
        """加载定时任务卡片关联的文件内容
        
        Args:
            card_text: 卡片文本内容
            
        Returns:
            文件内容摘要，如果没有文件则返回 None
        """
        try:
            # 从卡片文本中提取任务ID
            import re
            job_id_match = re.search(r'任务ID:\s*`([^`]+)`', card_text)
            if not job_id_match:
                return None
            
            job_id = job_id_match.group(1)
            
            # 从历史记录中获取文件信息
            from kimi_cli.scheduler.history import JobHistoryStore
            
            history_store = JobHistoryStore()
            # 由于不知道具体的 chat_id，我们尝试从所有记录中查找
            # 这里简化处理：在历史存储中查找最近包含此 job_id 的记录
            
            # 遍历所有历史文件（实际使用时可以优化）
            history_dir = history_store._storage_dir
            if not history_dir.exists():
                return None
            
            for history_file in history_dir.glob("*.json"):
                try:
                    import json
                    with open(history_file, encoding="utf-8") as f:
                        records = json.load(f)
                    
                    for record_data in records:
                        if record_data.get("job_id") == job_id:
                            # 找到匹配的记录
                            files = record_data.get("files", [])
                            feishu_files = record_data.get("feishu_files", [])
                            
                            if not files and not feishu_files:
                                return None
                            
                            # 读取文件内容（只读文本文件）
                            file_contents = []
                            for file_path in files[:3]:  # 最多读3个文件
                                try:
                                    path = Path(file_path)
                                    if path.exists() and path.is_file():
                                        # 检查文件大小（不超过 500KB）
                                        if path.stat().st_size > 500 * 1024:
                                            file_contents.append(f"[{path.name}]: 文件过大，无法读取")
                                            continue
                                        
                                        # 检查是否是文本文件
                                        content = path.read_text(encoding='utf-8', errors='ignore')
                                        # 截断内容
                                        if len(content) > 2000:
                                            content = content[:2000] + "\n... (内容已截断)"
                                        file_contents.append(f"=== {path.name} ===\n{content}")
                                except Exception as e:
                                    file_contents.append(f"[{path.name}]: 读取失败 - {e}")
                            
                            if file_contents:
                                return "\n\n".join(file_contents)
                            elif feishu_files:
                                file_names = [f.get("file_name", "未知文件") for f in feishu_files]
                                return f"关联文件: {', '.join(file_names)}\n(飞书文件需要重新上传才能读取内容)"
                            
                except Exception:
                    continue
            
            return None
            
        except Exception as e:
            logger.debug(f"Error loading scheduled task files: {e}")
            return None
    
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
        print("\n[DEBUG] Received event:")
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
        print(f"[DEBUG] Checking access for user {user_id} in chat {chat_id}")
        if not self._check_access(user_id, chat_id):
            print(f"[DEBUG] Access denied for user {user_id} in chat {chat_id}")
            logger.warning(f"Access denied for user {user_id} in chat {chat_id}")
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                "❌ 访问被拒绝：您不在允许的用户列表中",
            )
            return
        print("[DEBUG] Access granted")
        
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
        # Track if this message has media content that should be buffered
        has_media_only = False
        
        if msg_type == "text":
            text = content.get("text", "")
        elif msg_type == "image":
            # Handle image download with buffer support
            image_key = content.get("image_key")
            
            if image_key:
                print(f"[HANDLER] Received image: {image_key}")
                
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
                        
                        # Add to session's media buffer (will be processed when text arrives)
                        session_key = self._get_session_key(chat_id, user_id)
                        session = await self._get_or_create_session_for_media(
                            chat_id, user_id, session_key
                        )
                        if session:
                            await session.add_to_media_buffer(
                                media_type="image",
                                file_path=save_path,
                                file_name=image_name,
                                file_size=len(image_content),
                            )
                        has_media_only = True
                        text = ""  # No text yet, will be processed later
                    except Exception as e:
                        print(f"[HANDLER ERROR] Failed to save image: {e}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"❌ 保存图片失败: {str(e)[:100]}",
                        )
                        return
                else:
                    print("[HANDLER ERROR] Failed to download image")
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        chat_id,
                        "❌ 下载图片失败",
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
                        
                        # Add to session's media buffer (will be processed when text arrives)
                        session_key = self._get_session_key(chat_id, user_id)
                        session = await self._get_or_create_session_for_media(
                            chat_id, user_id, session_key
                        )
                        if session:
                            await session.add_to_media_buffer(
                                media_type="file",
                                file_path=save_path,
                                file_name=file_name,
                                file_size=len(file_content),
                            )
                        has_media_only = True
                        text = ""  # No text yet, will be processed later
                    except Exception as e:
                        print(f"[HANDLER ERROR] Failed to save file: {e}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"❌ 保存文件失败: {str(e)[:100]}",
                        )
                        return
                else:
                    print("[HANDLER ERROR] Failed to download file")
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        chat_id,
                        "❌ 下载文件失败",
                    )
                    return
            else:
                text = f"[File uploaded: {file_name}]"
        elif msg_type == "audio":
            # Handle voice/audio message
            file_key = content.get("file_key")
            
            if file_key:
                print(f"[HANDLER] Received audio message: {file_key}")
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "🎤 收到语音消息\n正在下载并识别...",
                )
                
                # Download the audio file
                message_id = message.message_id if hasattr(message, 'message_id') else None
                result = await asyncio.to_thread(
                    self.client.download_audio,
                    file_key,
                    message_id,
                )
                
                if result:
                    audio_content, audio_name = result
                    # Save to work directory
                    work_dir = self._get_work_dir()
                    save_path = os.path.join(work_dir, f"received_{audio_name}")
                    try:
                        with open(save_path, "wb") as f:
                            f.write(audio_content)
                        print(f"[HANDLER] Audio saved to: {save_path}")
                        
                        # Perform ASR using GLM-ASR-2512
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            "📝 正在进行语音识别 (GLM-ASR-2512)...",
                        )
                        
                        try:
                            # Use GLM-ASR-2512 for transcription
                            # Get API key from config or env var
                            api_key = None
                            if self.config.asr_api_key:
                                api_key = self.config.asr_api_key.get_secret_value()
                            
                            transcribed_text = await asyncio.to_thread(
                                transcribe_audio,
                                save_path,
                                api_key=api_key,
                            )
                            
                            if transcribed_text.strip():
                                await asyncio.to_thread(
                                    self.client.send_text_message,
                                    chat_id,
                                    f"✅ 语音识别完成！\n🎯 识别结果：\n{transcribed_text}",
                                )
                                # Pass transcribed text to Kimi
                                text = f"[语音消息转文字] {transcribed_text}"
                            else:
                                await asyncio.to_thread(
                                    self.client.send_text_message,
                                    chat_id,
                                    "⚠️ 未能识别到语音内容，请重试或发送文字消息",
                                )
                                return
                                
                        except Exception as e:
                            print(f"[HANDLER ERROR] GLM-ASR-2512 failed: {e}")
                            await asyncio.to_thread(
                                self.client.send_text_message,
                                chat_id,
                                f"❌ 语音识别失败 (GLM-ASR-2512): {str(e)[:100]}\n请检查 ZHIPU_API_KEY 环境变量或发送文字消息",
                            )
                            return
                            
                    except Exception as e:
                        print(f"[HANDLER ERROR] Failed to save audio: {e}")
                        await asyncio.to_thread(
                            self.client.send_text_message,
                            chat_id,
                            f"❌ 保存音频失败: {str(e)[:100]}",
                        )
                        return
                else:
                    print("[HANDLER ERROR] Failed to download audio")
                    await asyncio.to_thread(
                        self.client.send_text_message,
                        chat_id,
                        "❌ 下载语音消息失败",
                    )
                    return
            else:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "🎤 收到语音消息，但无法获取音频信息",
                )
                return
        elif msg_type == "post":
            # Handle rich text (post) message
            print("[HANDLER] Received post message")
            work_dir = self._get_work_dir()
            message_id = message.message_id if hasattr(message, 'message_id') else None
            
            post_text = await handle_post_message(
                self,
                self.client,
                chat_id,
                content,
                message_id,
                work_dir,
            )
            
            if post_text:
                text = post_text
            else:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "⚠️ 处理富文本消息失败，请尝试分开发送文字和文件",
                )
                return
        else:
            await asyncio.to_thread(
                self.client.send_text_message,
                chat_id,
                f"Unsupported message type: {msg_type}. Please send text, image, file, or audio messages only.",
            )
            return
        
        # Clean up @ mentions
        print(f"[DEBUG] Text before clean: '{text}'")
        text = self._clean_mentions(text)
        print(f"[DEBUG] Text after clean: '{text}'")
        
        # Handle quoted/reply messages - fetch the quoted content
        quoted_content = await self._get_quoted_message_content(message)
        if quoted_content:
            print(f"[DEBUG] Quoted content: {quoted_content[:200]}...")
            # Append quoted content to user's message
            text = f"{text}\n\n[引用消息]:\n{quoted_content}"
        
        # Handle media buffer logic
        session_key = self._get_session_key(chat_id, user_id)
        
        if has_media_only and not text.strip():
            # Pure media message (no text) - buffered and wait for instruction
            print("[HANDLER] Media buffered, waiting for text instruction")
            return
        
        # If there's text and we have buffered media, merge them
        if text.strip():
            existing_session = self._sessions.get(session_key)
            if existing_session and await existing_session.has_buffered_media():
                buffered_context = await existing_session.get_buffered_media_context()
                if buffered_context:
                    text = text + buffered_context
                    # Clear buffer after retrieving context
                    await existing_session.clear_media_buffer()
                    print("[HANDLER] Merged buffered media into message")
        
        if not text.strip():
            print("[DEBUG] Empty text after cleaning, returning")
            return
        
        logger.info(f"[HANDLER] Received message from {user_id} in {chat_id} ({chat_type}): {text[:100]}")
        logger.info(f"[HANDLER] Session key: {session_key}")
        
        # Handle session management commands (before creating session)
        # Use original text for command matching to handle edge cases
        stripped = text.strip()
        
        # Normalize command: remove extra spaces and convert to lowercase for comparison
        normalized_cmd = ' '.join(stripped.split()).lower()
        
        logger.info(f"[HANDLER] Checking command: '{stripped}' (normalized: '{normalized_cmd}')")
        print(f"[HANDLER] Checking command: '{stripped}' (normalized: '{normalized_cmd}')")
        
        # Check for session management commands (case insensitive)
        if normalized_cmd == "/sessions":
            logger.info("[HANDLER] Matched /sessions command")
            print("[HANDLER] Matched /sessions command")
            await self._handle_sessions_command(chat_id)
            return
        elif normalized_cmd.startswith("/continue "):
            parts = stripped.split(maxsplit=1)  # Use original for session_id
            if len(parts) == 2:
                session_id = parts[1].strip()
                logger.info(f"[HANDLER] Matched /continue command with ID: {session_id}")
                print(f"[HANDLER] Matched /continue command with ID: {session_id}")
                await self._handle_continue_command(chat_id, user_id, session_key, session_id)
                return
            else:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "❌ 请提供 session ID，例如：`/continue abc123`",
                )
                return
        elif normalized_cmd.startswith("/session "):
            parts = stripped.split(maxsplit=1)  # Use original for session_id
            if len(parts) == 2:
                session_id = parts[1].strip()
                logger.info(f"[HANDLER] Matched /session command with ID: {session_id}")
                print(f"[HANDLER] Matched /session command with ID: {session_id}")
                await self._handle_continue_command(chat_id, user_id, session_key, session_id)
                return
            else:
                await asyncio.to_thread(
                    self.client.send_text_message,
                    chat_id,
                    "❌ 请提供 session ID，例如：`/session abc123`",
                )
                return
        elif normalized_cmd == "/link":
            logger.info("[HANDLER] Matched /link command")
            print("[HANDLER] Matched /link command")
            await self._handle_link_command(chat_id, user_id, session_key)
            return
        elif normalized_cmd == "/id":
            logger.info("[HANDLER] Matched /id command")
            print("[HANDLER] Matched /id command")
            await self._handle_id_command(chat_id, session_key)
            return
        
        # Get or create session
        async with self._lock:
            session = self._sessions.get(session_key)
            
            # Check if session should be deleted (from /new command)
            should_delete = getattr(session, '_should_delete', False) if session else False
            print(f"[HANDLER] Session {id(session) if session else 'None'} _should_delete={should_delete}, session_key={session_key}")
            if session is not None and should_delete:
                print(f"[HANDLER] Session marked for deletion, removing {session_key}")
                logger.info(f"[HANDLER] Session {id(session)} marked for deletion, removing {session_key}")
                del self._sessions[session_key]
                session = None
            
            if session is None:
                print(f"[HANDLER] Creating new session for {session_key}")
                logger.info(f"[HANDLER] Creating new session for {session_key}")
                
                # Check if there's a linked CLI session
                linked_session_id = self._linked_sessions.get(session_key)
                
                try:
                    if linked_session_id:
                        # Try to load existing CLI session
                        soul = await self._create_soul_from_session_id(linked_session_id)
                        if soul:
                            print(f"[HANDLER] Loaded CLI session: {linked_session_id}")
                            logger.info(f"[HANDLER] Loaded CLI session: {linked_session_id}")
                        else:
                            # Fall back to new session
                            print(f"[HANDLER] Failed to load session {linked_session_id}, creating new")
                            soul = await self._create_soul_for_session(session_key)
                    else:
                        # Create new soul for this chat session
                        soul = await self._create_soul_for_session(session_key)
                    
                    print("[HANDLER] Soul created successfully")
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
                print("[HANDLER] New session created")
                logger.info("[HANDLER] New session created")
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
    
    async def handle_card_action(self, data: P2CardActionTrigger) -> None:
        """Handle card action trigger (button click).
        
        This is called when user clicks a button on an interactive card.
        """
        try:
            event = data.event
            action_value = event.action.value
            # Use operator.open_id instead of user_id
            user_id = event.operator.open_id if event.operator else "unknown"
            # Use context.open_chat_id instead of chat_id
            chat_id = event.context.open_chat_id if event.context else None
            message_id = event.context.open_message_id if event.context else None
            
            print(f"[CARD ACTION] Received action: {action_value}")
            logger.info(f"Card action from user {user_id}: {action_value}")
            
            # Extract action key and request_id from button value
            key = action_value.get("key") if isinstance(action_value, dict) else None
            request_id = action_value.get("request_id") if isinstance(action_value, dict) else None
            
            if not key or not request_id:
                print(f"[CARD ACTION] Invalid action value: {action_value}")
                return
            
            # Find the session for this chat
            print(f"[CARD ACTION] Looking for session with chat_id: {chat_id}")
            print(f"[CARD ACTION] Available sessions: {list(self._sessions.keys())}")
            session_key = None
            for s_key, session in self._sessions.items():
                print(f"[CARD ACTION] Checking session {s_key}, chat_id: {session.chat_id}")
                if session.chat_id == chat_id:
                    session_key = s_key
                    break
            
            if not session_key:
                print(f"[CARD ACTION] No session found for chat {chat_id}")
                return
            
            session = self._sessions.get(session_key)
            if not session:
                print(f"[CARD ACTION] Session not found: {session_key}")
                return
            
            print(f"[CARD ACTION] Found session {session_key}, pending_approvals: {list(session._pending_approvals.keys())}, pending_model_selections: {list(session._pending_model_selections.keys())}")
            
            # Check if this is a model selection action
            if key in ("select_model", "select_thinking", "confirm_model", "cancel_model"):
                await self._handle_model_card_action(session, message_id, key, request_id, action_value)
                return
            
            # Find the pending approval request
            if request_id not in session._pending_approvals:
                print(f"[CARD ACTION] Request {request_id} not found or already processed")
                # Don't send error message to user - it's likely they just clicked twice
                # The original request handler will update the card
                return
            
            # Get the message and IMMEDIATELY remove from pending to prevent race conditions
            # This ensures that even if user clicks multiple buttons rapidly,
            # only the first one will be processed
            msg = session._pending_approvals.pop(request_id)
            
            # Debug: Check the _future status
            future_id = id(msg._future) if msg._future else "None"
            print(f"[CARD ACTION] Got msg {request_id}, _future id: {future_id}, resolved: {msg.resolved}")
            
            # Check if already resolved (double-check)
            if msg.resolved:
                print(f"[CARD ACTION] Request {request_id} already resolved, ignoring")
                return
            
            # Handle different actions
            print(f"[CARD ACTION] Calling resolve('{key}') for {request_id}")
            if key == "approve_once":
                msg.resolve("approve")
                print(f"[CARD ACTION] Request {request_id} approved once")
                
            elif key == "approve_session":
                msg.resolve("approve_for_session")
                print(f"[CARD ACTION] Request {request_id} approved for session")
                
            elif key == "reject":
                msg.resolve("reject")
                print(f"[CARD ACTION] Request {request_id} rejected")
            else:
                print(f"[CARD ACTION] Unknown action key: {key}")
                # Put it back if we didn't handle it
                session._pending_approvals[request_id] = msg
                
        except Exception as e:
            logger.exception(f"Error handling card action: {e}")
            print(f"[CARD ACTION ERROR] {e}")
    
    async def _handle_model_card_action(
        self,
        session: SDKChatSession,
        message_id: str | None,
        key: str,
        request_id: str,
        action_value: dict[str, Any],
    ) -> None:
        """Handle model selection card actions.
        
        Args:
            session: The chat session
            message_id: The message ID of the card
            key: The action key
            request_id: The request ID
            action_value: The full action value dict
        """
        from kimi_cli.config import load_config, save_config
        from kimi_cli.feishu.card_builder import (
            build_model_confirm_card,
            build_model_result_card,
            build_thinking_selection_card,
        )
        from kimi_cli.llm import derive_model_capabilities
        
        print(f"[MODEL ACTION] Handling model action: {key}, request_id: {request_id}")
        
        # Check if this is a valid model selection request
        if request_id not in session._pending_model_selections:
            print(f"[MODEL ACTION] Request {request_id} not found in pending_model_selections")
            return
        
        selection_state = session._pending_model_selections[request_id]
        
        try:
            if key == "select_model":
                # User selected a model
                model_name = action_value.get("model_name")
                if not model_name:
                    print("[MODEL ACTION] No model_name in action value")
                    return
                
                print(f"[MODEL ACTION] Model selected: {model_name}")
                selection_state["selected_model"] = model_name
                selection_state["stage"] = "selecting_thinking"
                
                # Get current thinking state
                current_thinking = False
                if session.soul and hasattr(session.soul, 'thinking'):
                    current_thinking = session.soul.thinking
                
                # Load config to check if this model supports thinking
                config = load_config()
                model_cfg = config.models.get(model_name)
                supports_thinking = False
                always_thinking = False
                
                if model_cfg:
                    capabilities = derive_model_capabilities(model_cfg)
                    if "always_thinking" in capabilities:
                        always_thinking = True
                        current_thinking = True
                    elif "thinking" in capabilities:
                        supports_thinking = True
                
                if always_thinking:
                    # Skip thinking selection, go directly to confirm
                    selection_state["selected_thinking"] = True
                    selection_state["stage"] = "confirming"
                    confirm_card = build_model_confirm_card(
                        model_name=model_name,
                        thinking=True,
                        confirm_request_id=request_id,
                    )
                    await asyncio.to_thread(
                        session.client.update_interactive_card,
                        message_id,
                        confirm_card,
                    )
                elif supports_thinking:
                    # Show thinking selection card
                    thinking_card = build_thinking_selection_card(
                        current_thinking=current_thinking,
                        model_name=model_name,
                        request_id=request_id,
                    )
                    await asyncio.to_thread(
                        session.client.update_interactive_card,
                        message_id,
                        thinking_card,
                    )
                else:
                    # Model doesn't support thinking, skip to confirm
                    selection_state["selected_thinking"] = False
                    selection_state["stage"] = "confirming"
                    confirm_card = build_model_confirm_card(
                        model_name=model_name,
                        thinking=False,
                        confirm_request_id=request_id,
                    )
                    await asyncio.to_thread(
                        session.client.update_interactive_card,
                        message_id,
                        confirm_card,
                    )
                    
            elif key == "select_thinking":
                # User selected thinking mode
                thinking = action_value.get("thinking", False)
                model_name = action_value.get("model_name")
                
                print(f"[MODEL ACTION] Thinking selected: {thinking} for model {model_name}")
                selection_state["selected_thinking"] = thinking
                selection_state["stage"] = "confirming"
                
                # Show confirm card
                confirm_card = build_model_confirm_card(
                    model_name=model_name or selection_state.get("selected_model", ""),
                    thinking=thinking,
                    confirm_request_id=request_id,
                )
                await asyncio.to_thread(
                    session.client.update_interactive_card,
                    message_id,
                    confirm_card,
                )
                
            elif key == "confirm_model":
                # User confirmed the selection
                model_name = action_value.get("model_name")
                thinking = action_value.get("thinking", False)
                
                print(f"[MODEL ACTION] Confirming model: {model_name}, thinking: {thinking}")
                
                # Update config
                try:
                    config = load_config()
                    if model_name in config.models:
                        config.default_model = model_name
                        config.default_thinking = thinking
                        save_config(config)
                        
                        # Update the card to show success
                        result_card = build_model_result_card(
                            model_name=model_name,
                            thinking=thinking,
                            success=True,
                        )
                        await asyncio.to_thread(
                            session.client.update_interactive_card,
                            message_id,
                            result_card,
                        )
                        
                        # Clean up pending selection
                        session._pending_model_selections.pop(request_id, None)
                        
                        # Note: We don't reload the session here to avoid disrupting the conversation
                        # The new settings will take effect on the next session
                        
                    else:
                        # Model not found
                        result_card = build_model_result_card(
                            model_name=model_name or "Unknown",
                            thinking=thinking,
                            success=False,
                        )
                        await asyncio.to_thread(
                            session.client.update_interactive_card,
                            message_id,
                            result_card,
                        )
                        session._pending_model_selections.pop(request_id, None)
                        
                except Exception as e:
                    logger.exception(f"Error saving model config: {e}")
                    result_card = build_model_result_card(
                        model_name=model_name or "Unknown",
                        thinking=thinking,
                        success=False,
                    )
                    await asyncio.to_thread(
                        session.client.update_interactive_card,
                        message_id,
                        result_card,
                    )
                    session._pending_model_selections.pop(request_id, None)
                    
            elif key == "cancel_model":
                # User cancelled
                print("[MODEL ACTION] Model selection cancelled")
                
                # Update card to show cancelled
                from kimi_cli.feishu.card_builder import _plain_text_element
                cancel_card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "grey",
                        "title": {
                            "tag": "plain_text",
                            "content": "❌ 已取消",
                        },
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": _plain_text_element("模型切换已取消，当前设置保持不变。"),
                        },
                    ],
                }
                await asyncio.to_thread(
                    session.client.update_interactive_card,
                    message_id,
                    cancel_card,
                )
                session._pending_model_selections.pop(request_id, None)
                
        except Exception as e:
            logger.exception(f"Error handling model card action: {e}")
            print(f"[MODEL ACTION ERROR] {e}")


class FeishuSDKServer:
    """Feishu server using SDK long connection (WebSocket)."""
    
    # Class-level ID to identify the current active server instance
    # This helps old WebSocket threads detect when they should stop processing
    _current_instance_id: int = 0
    
    def __init__(self, config: FeishuConfig):
        self.config = config
        self._running = False
        self._clients: dict[str, FeishuSDKClient] = {}
        self._handlers: dict[str, SDKMessageHandler] = {}
        self._ws_clients: dict[str, Any] = {}  # WebSocket clients
        self._ws_threads: dict[str, threading.Thread] = {}
        # Instance ID to identify this specific server instance
        self._instance_id = FeishuSDKServer._get_next_instance_id()
    
    @classmethod
    def _get_next_instance_id(cls) -> int:
        """Get the next instance ID."""
        cls._current_instance_id += 1
        return cls._current_instance_id
    
    @property
    def is_current_instance(self) -> bool:
        """Check if this is the current active server instance."""
        return self._instance_id == FeishuSDKServer._current_instance_id
    
    async def start(self) -> None:
        """Start the Feishu SDK server."""
        if self._running:
            logger.warning(f"[START] Instance {self._instance_id} already running")
            return
        
        logger.info(f"[START] Starting Feishu SDK server instance {self._instance_id}...")
        print(f"[START] Starting server instance {self._instance_id}")
        
        self._running = True
        
        # Initialize all accounts
        await self._init_accounts()
        
        # Give WebSocket clients time to establish connections
        logger.info("[START] Waiting for WebSocket connections to establish...")
        await asyncio.sleep(1.5)
        
        # Initialize scheduler in background task (don't block)
        print("[START] Initializing scheduler in background...")
        asyncio.create_task(self._init_scheduler_bg())
        
        logger.info(f"[START] Feishu SDK server instance {self._instance_id} started successfully")
        print(f"✅ Feishu SDK server is running (instance {self._instance_id})")
        print("   Use Ctrl+C to stop")
    
    async def stop(self) -> None:
        """Stop the server."""
        if not self._running:
            return
        
        self._running = False
        # Mark this instance as "old" so WebSocket threads stop processing
        FeishuSDKServer._current_instance_id += 1
        logger.info(f"Stopping Feishu SDK server (instance {self._instance_id})...")
        print(f"[STOP] Stopping instance {self._instance_id}, new current instance will be {FeishuSDKServer._current_instance_id}")
        
        # Stop all WebSocket clients first (with timeout)
        for name, ws_client in list(self._ws_clients.items()):
            logger.info(f"[STOP] Stopping WebSocket client for {name}")
            print(f"[STOP] Stopping WebSocket client for {name}")
            try:
                # Use a timeout to avoid blocking indefinitely
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(ws_client.stop)
                    try:
                        future.result(timeout=3.0)
                    except concurrent.futures.TimeoutError:
                        print(f"[STOP] WebSocket client {name} stop timed out, continuing...")
            except Exception as e:
                logger.warning(f"[STOP] Error stopping WebSocket client for {name}: {e}")
        
        # Wait for WebSocket threads to finish with longer timeout
        for name, thread in list(self._ws_threads.items()):
            logger.info(f"[STOP] Waiting for WebSocket thread {name} to finish...")
            print(f"[STOP] Waiting for WebSocket thread {name}...")
            try:
                thread.join(timeout=5.0)  # Increased timeout
                if thread.is_alive():
                    logger.warning(f"[STOP] WebSocket thread {name} did not stop in time")
                    print(f"[STOP] WARNING: Thread {name} still alive (will be cleaned up on restart)")
            except Exception as e:
                logger.warning(f"[STOP] Error joining thread {name}: {e}")
        
        self._ws_clients.clear()
        self._ws_threads.clear()
        self._handlers.clear()
        self._clients.clear()
        
        # Stop scheduler
        await self._stop_scheduler()
        
        # Give extra time for connections to fully close
        logger.info("[STOP] Waiting for connections to close...")
        await asyncio.sleep(2.0)  # Increased delay
        
        logger.info(f"[STOP] Feishu SDK server stopped (instance {self._instance_id})")
        print(f"[STOP] Instance {self._instance_id} fully stopped")
    
    async def _stop_scheduler(self) -> None:
        """Stop the scheduler."""
        try:
            from kimi_cli.scheduler.scheduler import get_scheduler
            scheduler = get_scheduler()
            await scheduler.stop()
            logger.info("[STOP] Scheduler stopped")
        except Exception as e:
            logger.exception(f"[STOP] Error stopping scheduler: {e}")
    
    async def _init_accounts(self) -> None:
        """Initialize Feishu SDK clients for all accounts."""
        # Get the running event loop (this is safe in async context)
        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("[INIT] No running event loop found")
            return
        
        logger.info(f"[INIT] Initializing accounts for instance {self._instance_id}")
        print(f"[INIT] Initializing accounts for instance {self._instance_id}")
        
        for account_name, account_config in self.config.accounts.items():
            try:
                logger.info(f"[INIT] Setting up account: {account_name}")
                print(f"[INIT] Setting up account: {account_name}")
                
                # Create SDK client for API calls
                client = FeishuSDKClient(account_config)
                self._clients[account_name] = client
                
                # Test authentication by getting bot info
                bot_info = client.get_bot_info()
                if bot_info:
                    logger.info(f"[INIT] Account '{account_name}' authenticated: {bot_info.get('app_name')}")
                else:
                    logger.warning(f"[INIT] Could not get bot info for account '{account_name}'")
                
                # Create message handler (each handler manages its own sessions with isolated souls)
                handler = SDKMessageHandler(client, account_config, self.config, self)
                self._handlers[account_name] = handler
                
                # Start WebSocket client for event receiving (pass main_loop)
                self._start_ws_client(account_name, account_config, handler, main_loop)
                
                # Small delay to ensure WebSocket client is fully started before next account
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"[INIT] Failed to initialize account '{account_name}': {e}")
                print(f"[INIT] ERROR: Failed to initialize account '{account_name}': {e}")
        
        logger.info(f"[INIT] Accounts initialization complete for instance {self._instance_id}")
        print(f"[INIT] Accounts initialization complete for instance {self._instance_id}")
        
        # Note: Scheduler is now initialized in start() method after _init_accounts
    
    async def _init_scheduler_bg(self) -> None:
        """Initialize scheduler in background"""
        # 等待一小段时间确保 handler 已经创建
        await asyncio.sleep(2)
        
        print("[SCHEDULER] Background initialization starting...")
        
        try:
            from kimi_cli.scheduler.cron_engine import CronEngine
            from kimi_cli.scheduler.scheduler import get_scheduler
            
            # Get first handler
            handler = None
            for h in self._handlers.values():
                handler = h
                break
            
            if not handler:
                print("[SCHEDULER] No handler available")
                return
            
            scheduler = get_scheduler()
            
            # 简单初始化
            await scheduler._job_store.load_all()
            scheduler._initialized = True
            scheduler._feishu_handler = handler
            
            # 创建并启动 cron 引擎
            scheduler._cron_engine = CronEngine(
                job_store=scheduler._job_store,
                on_trigger=lambda job: self._trigger_scheduled_task(scheduler, job),
                check_interval=30.0,
            )
            await scheduler._cron_engine.start()
            
            print("✅ Scheduler initialized and started")
            print(f"   Jobs: {len(await scheduler._job_store.list_all())}")
            
        except Exception as e:
            print(f"⚠️ Scheduler init failed: {e}")
            import traceback
            traceback.print_exc()
    
    def _trigger_scheduled_task(self, scheduler, job) -> None:
        """触发定时任务"""
        import asyncio
        from datetime import datetime

        from kimi_cli.scheduler.models import IncomingMessage
        
        async def run_task():
            try:
                handler = scheduler._feishu_handler
                if not handler:
                    return
                
                # 如果设置了 reminder_text，直接发送提醒消息，不经过 Agent
                if job.reminder_text:
                    try:
                        # 构建提醒卡片
                        card = {
                            "config": {"wide_screen_mode": True},
                            "header": {
                                "title": {
                                    "tag": "plain_text",
                                    "content": "⏰ 定时提醒"
                                },
                                "template": "blue"
                            },
                            "elements": [
                                {
                                    "tag": "div",
                                    "text": {
                                        "tag": "lark_md",
                                        "content": job.reminder_text
                                    }
                                },
                                {
                                    "tag": "note",
                                    "elements": [
                                        {
                                            "tag": "plain_text",
                                            "content": f"任务: {job.description}"
                                        }
                                    ]
                                }
                            ]
                        }
                        # 直接发送卡片消息
                        await asyncio.to_thread(
                            handler.client.send_interactive_card,
                            job.chat_id,
                            card
                        )
                        return
                    except Exception:
                        # 如果发送失败，回退到 Agent 执行
                        pass
                
                # 没有 reminder_text 或发送失败时，通过 Agent 执行
                message = IncomingMessage(
                    text=f"[定时任务] {job.description}",
                    source="scheduled",
                    source_id=job.id,
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    chat_type=job.chat_type,
                    tenant_key=job.tenant_key,
                    metadata={"cron": job.cron},
                    created_at=datetime.now(),
                )
                
                from kimi_cli.scheduler.session import ScheduledTaskSession
                sched_session = ScheduledTaskSession(
                    session_id=f"sched_{job.user_id}_{job.id}",
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    feishu_handler=handler,
                    pending_store=scheduler._pending_store,
                )
                await sched_session.execute_scheduled_task(message)
                
            except Exception as e:
                print(f"[SCHEDULED] Error: {e}")
                import traceback
                traceback.print_exc()
        
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(run_task())
        except RuntimeError:
            pass
    
    def _start_ws_client(
        self,
        account_name: str,
        account_config: FeishuAccountConfig,
        handler: SDKMessageHandler,
        main_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Start WebSocket client for event receiving.
        
        This runs in a separate thread since the SDK's start() blocks.
        CRITICAL: All Lark SDK objects must be created inside the thread to avoid
        event loop conflicts with the main thread.
        """
        # Store references needed in the thread (do NOT create SDK objects here)
        # The event_handler must be created inside the thread to avoid capturing
        # the main event loop
        
        def run_ws_client():
            """Run WebSocket client in a thread with isolated event loop."""
            import asyncio
            
            # CRITICAL: Create a completely new event loop for this thread
            # This isolates the SDK's asyncio from the main event loop
            
            # First, ensure no event loop is set for this thread
            try:
                old_loop = asyncio.get_event_loop()
                if old_loop and not old_loop.is_closed():
                    try:
                        # Cancel all pending tasks
                        pending = asyncio.all_tasks(old_loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            old_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                    old_loop.close()
            except RuntimeError:
                # No event loop set for this thread yet, which is expected
                pass
            
            # Create and set a fresh event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # CRITICAL: Create event handler INSIDE the thread to avoid capturing main event loop
            event_handler = self._create_event_handler(handler, main_loop)
            
            # CRITICAL: Create WebSocket client INSIDE the thread to avoid capturing main event loop
            ws_client = lark.ws.Client(
                app_id=account_config.app_id,
                app_secret=account_config.app_secret.get_secret_value(),
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,  # Normal logging
            )
            
            # Store reference for cleanup
            self._ws_clients[account_name] = ws_client
            
            try:
                logger.info(f"[WS-{account_name}] Starting WebSocket client for instance {self._instance_id}")
                print(f"[WS-{account_name}] Starting WebSocket client for instance {self._instance_id}")
                ws_client.start()
            except Exception as e:
                error_msg = str(e)
                # Only log error if this is still the current instance
                if self.is_current_instance and self._running:
                    logger.error(f"[WS-{account_name}] WebSocket client error: {e}")
                    print(f"[WS-{account_name}] ERROR: {e}")
                else:
                    logger.info(f"[WS-{account_name}] WebSocket client stopped (expected on shutdown)")
            finally:
                # Clean up the event loop
                try:
                    if not loop.is_closed():
                        # Cancel all pending tasks
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()
                        # Run the event loop briefly to let tasks process cancellation
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        loop.close()
                except Exception:
                    pass
        
        # Start in a daemon thread with a unique name for easier debugging
        thread = threading.Thread(
            target=run_ws_client, 
            daemon=True,
            name=f"FeishuWS-{account_name}-{self._instance_id}"
        )
        thread.start()
        self._ws_threads[account_name] = thread
        
        logger.info(f"[WS-{account_name}] WebSocket client thread started for instance {self._instance_id}")
    
    def _create_event_handler(
        self,
        handler: SDKMessageHandler,
        main_loop: asyncio.AbstractEventLoop,
    ) -> lark.EventDispatcherHandler:
        """Create event dispatcher handler with all event callbacks.
        
        NOTE: main_loop is captured at creation time. If the server restarts,
        a new event handler will be created with the new event loop.
        """
        
        def _schedule_async(coro_factory, name: str = "task"):
            """Schedule async coroutine in main loop, non-blocking.
            
            Args:
                coro_factory: A callable that returns a coroutine when called.
                             This avoids creating the coroutine if we can't schedule it.
                name: Name for logging purposes.
            """
            # Check if this is still the current active server instance
            # (prevents old WebSocket threads from processing events after restart)
            if not self.is_current_instance:
                print(f"[SCHEDULE] Old server instance (id={self._instance_id}), ignoring {name}")
                return None
            
            # Check server running state
            if not self._running:
                print(f"[SCHEDULE] Server not running (instance {self._instance_id}), ignoring {name}")
                return None
            
            # Check if event loop is closed
            if main_loop.is_closed():
                print(f"[SCHEDULE] Event loop closed (instance {self._instance_id}), ignoring {name}")
                return None
            
            # Create the coroutine only when we're going to schedule it
            try:
                coro = coro_factory()
            except Exception as e:
                print(f"[SCHEDULE ERROR] Failed to create coroutine for {name}: {e}")
                import traceback
                traceback.print_exc()
                return None
            
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
            except RuntimeError as e:
                if "loop is closed" in str(e):
                    print(f"[SCHEDULE] Event loop closed, ignoring {name}")
                    return None
                print(f"[SCHEDULE ERROR] Failed to schedule {name}: {e}")
                import traceback
                traceback.print_exc()
                return None
            except Exception as e:
                print(f"[SCHEDULE ERROR] Failed to schedule {name}: {e}")
                import traceback
                traceback.print_exc()
                return None
        
        # Define event callbacks - these run in the SDK's thread
        def on_p2_im_message_receive_v1(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
            """Handle message receive event."""
            print("\n[EVENT] Message received!")
            try:
                content = data.event.message.content
                print(f"  content: {content[:100] if content else 'N/A'}")
            except Exception:
                print("  (content not available)")
            
            # Schedule in main event loop (non-blocking)
            # Use lambda to defer coroutine creation until we're sure we can schedule it
            print("[EVENT] Scheduling handler in main loop...")
            _schedule_async(lambda: handler.handle_message_event(data), "message_handler")
            print("[EVENT] Handler scheduled (non-blocking)")
        
        def on_p2_im_chat_member_bot_added_v1(data: lark.im.v1.P2ImChatMemberBotAddedV1) -> None:
            """Handle bot added to chat."""
            _schedule_async(lambda: handler.handle_add_bot_event(data), "bot_added")
        
        def on_p2_im_chat_member_bot_deleted_v1(data: lark.im.v1.P2ImChatMemberBotDeletedV1) -> None:
            """Handle bot removed from chat."""
            _schedule_async(lambda: handler.handle_remove_bot_event(data), "bot_removed")
        
        def on_p2_im_chat_access_event_v1(data: lark.im.v1.P2ImChatAccessEventBotP2pChatEnteredV1) -> None:
            """Handle P2P chat access event."""
            _schedule_async(lambda: handler.handle_p2p_chat_create(data), "p2p_chat")
        
        def on_p2_im_message_message_read_v1(data: lark.im.v1.P2ImMessageMessageReadV1) -> None:
            """Handle message read event (ignore)."""
            pass
        
        def on_p2_card_action_trigger(data: P2CardActionTrigger) -> None:
            """Handle card action trigger (button click)."""
            print("\n[EVENT] Card action triggered!")
            try:
                action = data.event.action
                action_value = action.value
                # Use operator.open_id instead of user_id
                user_id = data.event.operator.open_id if data.event.operator else "unknown"
                # Use context.open_chat_id instead of chat_id
                chat_id = data.event.context.open_chat_id if data.event.context else None
                message_id = data.event.context.open_message_id if data.event.context else None
                
                print(f"  action_value: {action_value}")
                print(f"  user_id: {user_id}")
                print(f"  chat_id: {chat_id}")
                print(f"  message_id: {message_id}")
                
                # Schedule in main event loop
                _schedule_async(lambda: handler.handle_card_action(data), "card_action_handler")
            except Exception as e:
                print(f"[EVENT ERROR] Failed to handle card action: {e}")
                import traceback
                traceback.print_exc()
        
        # Build event handler
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(on_p2_im_message_receive_v1) \
            .register_p2_im_chat_member_bot_added_v1(on_p2_im_chat_member_bot_added_v1) \
            .register_p2_im_chat_member_bot_deleted_v1(on_p2_im_chat_member_bot_deleted_v1) \
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(on_p2_im_chat_access_event_v1) \
            .register_p2_im_message_message_read_v1(on_p2_im_message_message_read_v1) \
            .register_p2_card_action_trigger(on_p2_card_action_trigger) \
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
) -> int:
    """Run the Feishu SDK server.
    
    This uses the official Feishu SDK's long connection (WebSocket) feature,
    eliminating the need for webhook URLs or tunneling tools.
    
    Args:
        config: Feishu configuration. If None, loads from default location.
        host: Override host from config.
        port: Override port from config.
    
    Returns:
        Exit code: 0 for normal exit, non-zero for error.
    """
    # NOTE: We no longer use nest_asyncio due to compatibility issues with Python 3.12
    # (causes "cannot enter context" errors with contextvars).
    logger.debug("Starting run_sdk_server without nest_asyncio")
    
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
        return 1
    
    # Verify we're running in the expected event loop context
    try:
        current_loop = asyncio.get_running_loop()
        logger.debug(f"Running in event loop: {id(current_loop)}")
    except RuntimeError:
        logger.error("No running event loop - this should not happen when called via asyncio.run()")
        return 1
    
    server = FeishuSDKServer(config)
    
    try:
        await server.start()
        print("\n🚀 Feishu SDK server started!")
        print("   Mode: SDK Long Connection (WebSocket)")
        print(f"   Accounts: {', '.join(config.accounts.keys())}")
        print("\n✅ No webhook URL needed!")
        print("✅ No tunnel/穿透 tools required!")
        print("✅ Events received via WebSocket directly from Feishu")
        print("✅ Each chat has isolated context")
        
        await server.run_forever()
        return 0
        
    except asyncio.CancelledError:
        return 0
    finally:
        await server.stop()
