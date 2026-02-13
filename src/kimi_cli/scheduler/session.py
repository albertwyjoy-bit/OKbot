"""Scheduled task session for silent execution."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from kimi_cli.scheduler.history import JobExecutionRecord, JobHistoryStore
from kimi_cli.scheduler.models import IncomingMessage, PendingNotification, ScheduledResult
from kimi_cli.scheduler.store import PendingResultStore

# 最大等待时间 30 分钟
MAX_WAIT_TIME = 1800


class ScheduledTaskSession:
    """
    定时任务会话
    
    使用独立的 Session ID 格式: sched_{user_id}_{job_id}_{timestamp}
    例如: sched_u123_job_daily_1707456000
    
    特点：
    - 独立的 Context，不影响用户的对话历史
    - 静默执行，只返回最终结果
    - 支持队列等待，当用户会话忙碌时暂存结果
    """
    
    def __init__(
        self,
        session_id: str,
        chat_id: str,
        user_id: str,
        feishu_handler: Any,  # SDKMessageHandler
        pending_store: PendingResultStore,
        history_store: JobHistoryStore | None = None,
    ):
        """Initialize scheduled task session.
        
        Args:
            session_id: 会话ID (格式: sched_{user_id}_{job_id})
            chat_id: 飞书对话ID
            user_id: 用户ID
            feishu_handler: 飞书消息处理器
            pending_store: 等待结果存储
            history_store: 历史记录存储，默认创建新的
        """
        self.session_id = session_id
        self.chat_id = chat_id
        self.user_id = user_id
        self._feishu_handler = feishu_handler
        self._pending_store = pending_store
        self._history_store = history_store or JobHistoryStore()
        
        self._running = False
        self._processing_lock = asyncio.Lock()
        self._idle_event = asyncio.Event()
        self._idle_event.set()  # 初始状态为空闲
        
        # 等待队列
        self._pending_notifications: list[PendingNotification] = []
        self._notifications_lock = asyncio.Lock()
        
        # Soul 缓存 - 复用同一个 Soul 避免重复创建 MCP 连接
        self._cached_soul: Any | None = None
        self._soul_lock = asyncio.Lock()
    
    def is_processing(self) -> bool:
        """检测当前会话是否正在处理任务"""
        return self._running
    
    async def wait_for_idle(self, timeout: float = MAX_WAIT_TIME) -> bool:
        """等待会话变为空闲状态
        
        Args:
            timeout: 最大等待时间（秒）
            
        Returns:
            True 如果在超时前变为空闲，False 如果超时
        """
        try:
            await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for idle: {self.session_id}")
            return False
    
    async def queue_scheduled_task(self, message: IncomingMessage) -> None:
        """将定时任务加入等待队列
        
        Args:
            message: 定时任务消息
        """
        logger.info(f"Queuing scheduled task: {message.source_id}")
        
        # 执行任务但不发送结果，而是存入等待队列
        result = await self._execute_silently(message)
        
        async with self._notifications_lock:
            notification = PendingNotification(
                result=result,
                chat_id=self.chat_id,
                user_id=self.user_id,
            )
            self._pending_notifications.append(notification)
            
            # 持久化等待队列
            await self._pending_store.save(self.chat_id, self._pending_notifications)
            
        logger.info(f"Scheduled task queued: {message.source_id}, queue size: {len(self._pending_notifications)}")
    
    async def execute_scheduled_task(self, message: IncomingMessage, use_card: bool = True) -> None:
        """直接执行定时任务
        
        当飞书会话空闲时直接执行并发送结果。
        
        Args:
            message: 定时任务消息
            use_card: 是否使用卡片消息发送结果，默认 True
        """
        logger.info(f"Executing scheduled task directly: {message.source_id}")
        
        # 竞争条件防护：立即检查并设置状态，避免重复执行
        # 使用锁保护整个检查-设置-执行流程
        async with self._notifications_lock:
            if self._running:
                logger.warning(f"Task {message.source_id} is already running, skipping duplicate execution")
                return
            # 立即标记为执行中，防止其他调用进入
            self._running = True
            self._idle_event.clear()
        
        try:
            # 执行任务
            result = await self._execute_silently_with_lock(message)
            
            # 直接发送结果（使用卡片消息）
            await self._send_result(result, use_card=use_card)
        finally:
            # 恢复状态
            async with self._notifications_lock:
                self._running = False
                self._idle_event.set()
    
    async def _execute_silently_with_lock(self, message: IncomingMessage) -> ScheduledResult:
        """
        静默执行定时任务（外部已加锁版本）
        
        注意：调用此方法前必须已获取锁并设置 self._running = True
        
        Args:
            message: 定时任务消息
            
        Returns:
            执行结果
        """
        job_id = message.source_id or "unknown"
        
        logger.info(f"Silently executing scheduled task: {job_id}")
        
        # 获取或创建 KimiSoul（复用缓存的 Soul）
        soul = await self._get_or_create_soul()
        
        if not soul:
            return ScheduledResult(
                job_id=job_id,
                success=False,
                error="Failed to create soul for scheduled task",
            )
        
        # 检测并获取 MCP 资源锁
        from kimi_cli.scheduler.mcp_resource_lock import get_mcp_lock_manager
        lock_manager = get_mcp_lock_manager()
        
        # 从 toolset 检测需要哪些 MCP 资源锁
        required_locks: list[str] = []
        try:
            if hasattr(soul, '_agent') and soul._agent and hasattr(soul._agent, 'toolset'):
                toolset = soul._agent.toolset
                required_locks = lock_manager.detect_required_locks_from_toolset(toolset)
                if required_locks:
                    logger.info(f"Task {job_id} requires MCP resource locks: {required_locks}")
        except Exception as e:
            logger.warning(f"Failed to detect MCP locks for task {job_id}: {e}")
        
        # 获取 MCP 资源锁
        if required_locks:
            logger.info(f"Acquiring MCP resource locks for task {job_id}: {required_locks}")
            await lock_manager.acquire(required_locks)
            logger.info(f"Acquired MCP resource locks for task {job_id}: {required_locks}")
        
        try:
            # 执行但不发送中间消息
            # 使用 run_silent 方法来获取最终结果
            result_output = await self._run_soul_silent(soul, message.text)
            
            # 检测输出中提到的文件路径
            detected_files = self._extract_file_paths(result_output)
            if detected_files:
                logger.info(f"Detected {len(detected_files)} files in task output: {detected_files}")
            
            return ScheduledResult(
                job_id=job_id,
                success=True,
                output=result_output,
                files=detected_files,
            )
            
        except Exception as e:
            logger.exception(f"Error executing scheduled task {job_id}: {e}")
            return ScheduledResult(
                job_id=job_id,
                success=False,
                error=str(e),
            )
        finally:
            # 释放 MCP 资源锁
            if required_locks:
                lock_manager.release(required_locks)
                logger.info(f"Released MCP resource locks for task {job_id}: {required_locks}")
            # 注意：不清理 Soul，让它复用
    
    async def _execute_silently(self, message: IncomingMessage) -> ScheduledResult:
        """
        静默执行定时任务（兼容旧版本，内部加锁）
        
        - 使用独立的 Context
        - 不发送中间过程消息
        - 只返回最终结果
        
        Args:
            message: 定时任务消息
            
        Returns:
            执行结果
        """
        job_id = message.source_id or "unknown"
        
        async with self._processing_lock:
            self._running = True
            self._idle_event.clear()  # 标记为忙碌
            
            try:
                return await self._execute_silently_with_lock(message)
            finally:
                self._running = False
                self._idle_event.set()  # 标记为空闲
    
    async def _get_or_create_soul(self) -> Any | None:
        """获取或创建 KimiSoul（复用缓存的 Soul）
        
        复用 Soul 可以避免重复创建 MCP 连接，防止 Midscene 等 MCP server 被频繁重启。
        """
        async with self._soul_lock:
            if self._cached_soul is not None:
                logger.debug(f"Reusing cached soul for session {self.session_id}")
                return self._cached_soul
            
            try:
                from kimi_cli.feishu.sdk_server import SDKMessageHandler
                
                # 使用 feishu_handler 的方法创建 soul
                if hasattr(self._feishu_handler, '_create_soul_for_session'):
                    # 使用固定的 session key，确保 session 复用
                    temp_session_key = f"scheduled_{self.user_id}_{self.chat_id[:8]}"
                    soul = await self._feishu_handler._create_soul_for_session(temp_session_key)
                    self._cached_soul = soul
                    logger.info(f"Created and cached soul for session {self.session_id}")
                    return soul
                else:
                    logger.error("Feishu handler does not have _create_soul_for_session method")
                    return None
                    
            except Exception as e:
                logger.exception(f"Failed to create soul: {e}")
                return None
    
    async def _cleanup_soul(self) -> None:
        """清理缓存的 soul（在 session 销毁时调用）"""
        async with self._soul_lock:
            if self._cached_soul is not None:
                try:
                    # 清理 toolset 中的 MCP 连接
                    if (hasattr(self._cached_soul, '_agent') and 
                        self._cached_soul._agent and 
                        hasattr(self._cached_soul._agent, 'toolset')):
                        toolset = self._cached_soul._agent.toolset
                        if hasattr(toolset, 'cleanup'):
                            await toolset.cleanup()
                            logger.info(f"Cleaned up MCP connections for session {self.session_id}")
                except Exception as e:
                    logger.warning(f"Error cleaning up soul: {e}")
                finally:
                    self._cached_soul = None
    
    def _extract_file_paths(self, output: str | None) -> list[str]:
        """从输出中提取文件路径
        
        检测常见的文件路径格式：
        - /path/to/file.txt
        - 文件已保存到: /path/to/file.txt
        - 报告生成: ./output/report.md
        
        Args:
            output: 任务输出文本
            
        Returns:
            检测到的文件路径列表
        """
        if not output:
            return []
        
        import re
        from pathlib import Path
        
        detected = []
        
        # 模式1: 标准绝对路径 /path/to/file or ~/file
        # 模式2: 相对路径 ./file or ../file
        # 模式3: 中文提示后的路径
        patterns = [
            # 绝对路径 (Unix/Linux/macOS)
            r'(/[\w\-./]+\.[\w]+)',
            # 家目录路径
            r'(~/[\w\-./]+\.[\w]+)',
            # 相对路径
            r'(\./[\w\-./]+\.[\w]+)',
            # 中文提示后的路径 (保存到、生成在、路径为等)
            r'(?:保存到|生成在|路径为|文件位置|已保存|已生成)[：:]\s*([\w\-./~/]+\.[\w]+)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, output, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0] if match else None
                if match and match not in detected:
                    # 验证文件是否存在
                    try:
                        path = Path(match).expanduser()
                        if path.exists() and path.is_file():
                            detected.append(str(path))
                    except Exception:
                        pass
        
        return detected
    
    async def _run_soul_silent(self, soul: Any, message_text: str) -> str:
        """静默运行 soul，只返回最终结果
        
        Args:
            soul: KimiSoul 实例
            message_text: 消息文本
            
        Returns:
            执行结果文本
        """
        from kimi_cli.soul import run_soul
        from kimi_cli.wire import Wire
        from kimi_cli.wire.types import TextPart, TurnBegin, TurnEnd, ToolResult
        
        result_parts: list[str] = []
        cancel_event = asyncio.Event()  # 创建取消事件
        
        async def silent_wire_loop(wire: Wire) -> None:
            """静默 wire loop，只收集最终结果"""
            wire_ui = wire.ui_side(merge=False)
            
            try:
                while True:
                    msg = await wire_ui.receive()
                    
                    if isinstance(msg, TurnBegin):
                        result_parts.clear()
                    elif isinstance(msg, TextPart):
                        if msg.text:
                            result_parts.append(msg.text)
                    elif isinstance(msg, ToolResult):
                        # 收集工具结果
                        if hasattr(msg.return_value, 'brief'):
                            result_parts.append(str(msg.return_value.brief))
                        elif hasattr(msg.return_value, 'message'):
                            result_parts.append(str(msg.return_value.message))
                    elif isinstance(msg, TurnEnd):
                        break
                        
            except Exception as e:
                logger.debug(f"Silent wire loop ended: {e}")
        
        # 运行 soul，传入 cancel_event
        await run_soul(soul, message_text, silent_wire_loop, cancel_event)
        
        return "".join(result_parts)
    
    async def _send_result(self, result: ScheduledResult, use_card: bool = True) -> None:
        """发送结果到飞书
        
        Args:
            result: 执行结果
            use_card: 是否使用卡片消息（更美观），默认 True
        """
        try:
            if not hasattr(self._feishu_handler, 'client'):
                logger.error("Feishu handler does not have client attribute")
                return
            
            client = self._feishu_handler.client
            
            # 如果有文件，先上传到飞书
            if result.files:
                result.feishu_files = await self._upload_files_to_feishu(result.files, client)
            
            if use_card and hasattr(client, 'send_interactive_card'):
                # 使用卡片消息发送（更美观）
                card = self._build_result_card(result)
                await asyncio.to_thread(
                    client.send_interactive_card,
                    self.chat_id,
                    card,
                )
            else:
                # 使用文本消息发送
                message = result.format_message()
                await asyncio.to_thread(
                    client.send_text_message,
                    self.chat_id,
                    message,
                )
            
            # 单独发送文件消息（如果有文件）
            if result.feishu_files:
                await self._send_file_messages(result.feishu_files, client)
            
            logger.info(f"Sent scheduled result to chat {self.chat_id}: {result.job_id}")
            
            # 保存执行历史记录
            await self._save_history(result)
                
        except Exception as e:
            logger.exception(f"Failed to send scheduled result: {e}")
    
    async def _upload_files_to_feishu(
        self,
        files: list[str],
        client: Any,
    ) -> list[dict[str, Any]]:
        """上传文件到飞书
        
        Args:
            files: 本地文件路径列表
            client: 飞书客户端
            
        Returns:
            上传后的文件信息列表
        """
        uploaded = []
        
        for file_path in files:
            try:
                path = Path(file_path)
                if not path.exists():
                    continue
                
                # 读取文件内容
                file_content = path.read_bytes()
                file_name = path.name
                
                # 判断文件类型
                file_type = self._get_file_type(file_name)
                
                # 上传文件
                if hasattr(client, 'upload_file'):
                    file_key = await asyncio.to_thread(
                        client.upload_file,
                        file_content,
                        file_name,
                        file_type,
                    )
                    
                    if file_key:
                        uploaded.append({
                            "local_path": str(file_path),
                            "file_name": file_name,
                            "file_key": file_key,
                            "file_type": file_type,
                        })
                        logger.info(f"Uploaded file to Feishu: {file_name} -> {file_key}")
                    else:
                        logger.warning(f"Failed to upload file: {file_name}")
                else:
                    logger.warning("Client does not have upload_file method")
                    
            except Exception as e:
                logger.exception(f"Error uploading file {file_path}: {e}")
        
        return uploaded
    
    def _get_file_type(self, file_name: str) -> str:
        """根据文件名判断文件类型"""
        ext = Path(file_name).suffix.lower()
        
        # 图片类型
        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']:
            return 'image'
        
        # 文档类型
        if ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
            return 'document'
        
        # 默认流式文件
        return 'stream'
    
    async def _send_file_messages(
        self,
        feishu_files: list[dict[str, Any]],
        client: Any,
    ) -> None:
        """发送文件消息
        
        Args:
            feishu_files: 飞书文件信息列表
            client: 飞书客户端
        """
        for file_info in feishu_files:
            try:
                file_key = file_info["file_key"]
                file_name = file_info["file_name"]
                file_type = file_info["file_type"]
                
                if file_type == 'image' and hasattr(client, 'send_image_message'):
                    # 发送图片消息
                    await asyncio.to_thread(
                        client.send_image_message,
                        self.chat_id,
                        file_key,
                    )
                elif hasattr(client, 'send_file_message'):
                    # 发送文件消息
                    await asyncio.to_thread(
                        client.send_file_message,
                        self.chat_id,
                        file_key,
                    )
                
                logger.info(f"Sent file message: {file_name}")
                
            except Exception as e:
                logger.exception(f"Error sending file message: {e}")
    
    async def _save_history(self, result: ScheduledResult) -> None:
        """保存执行记录到历史"""
        try:
            record = JobExecutionRecord(
                job_id=result.job_id,
                job_description="定时任务执行",  # 可由调用方传入更多详情
                success=result.success,
                output=result.output,
                error=result.error,
                executed_at=result.executed_at,
                chat_id=self.chat_id,
                user_id=self.user_id,
                files=result.files,
                feishu_files=result.feishu_files,
            )
            await self._history_store.add_record(record)
        except Exception as e:
            logger.warning(f"Failed to save execution history: {e}")
    
    def _build_result_card(self, result: ScheduledResult) -> dict:
        """构建结果卡片
        
        Args:
            result: 执行结果
            
        Returns:
            飞书卡片 JSON
        """
        if result.success:
            header = {
                "template": "green",
                "title": {"tag": "plain_text", "content": "✅ 定时任务完成"}
            }
            output_text = result.output or "(无输出)"
            # 截断过长的输出
            if len(output_text) > 2000:
                output_text = output_text[:2000] + "\n\n... (内容已截断)"
        else:
            header = {
                "template": "red",
                "title": {"tag": "plain_text", "content": "❌ 定时任务失败"}
            }
            output_text = result.error or "未知错误"
            if len(output_text) > 1000:
                output_text = output_text[:1000] + "\n\n... (错误信息已截断)"
        
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**任务ID:** `{result.job_id}`"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**执行时间:** {result.executed_at.strftime('%Y-%m-%d %H:%M:%S')}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": output_text
                }
            }
        ]
        
        # 如果有文件，添加文件信息
        if result.feishu_files:
            file_items = []
            for i, f in enumerate(result.feishu_files, 1):
                file_name = f.get("file_name", f"文件{i}")
                file_items.append(f"{i}. {file_name}")
            
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📎 生成文件 ({len(result.feishu_files)}个):**\n" + "\n".join(file_items)
                }
            })
        elif result.files:
            # 文件未上传成功，只显示本地路径
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📎 检测到文件 ({len(result.files)}个)**\n但上传失败，请查看输出中的路径"
                }
            })
        
        return {
            "config": {"wide_screen_mode": True},
            "header": header,
            "elements": elements
        }
    
    async def flush_pending_notifications(self) -> None:
        """刷新等待队列，发送所有等待的结果
        
        这个方法应该在飞书会话处理完用户消息后被调用
        
        流程:
        1. 检查等待队列
        2. 清空队列并删除持久化存储
        3. 发送所有等待的结果（单条或合并）
        """
        async with self._notifications_lock:
            if not self._pending_notifications:
                logger.debug(f"No pending notifications for chat {self.chat_id}")
                return
            
            notifications = self._pending_notifications.copy()
            self._pending_notifications.clear()
            
            # 删除持久化存储
            await self._pending_store.delete(self.chat_id)
        
        logger.info(f"Flushing {len(notifications)} pending notifications for chat {self.chat_id}")
        
        if len(notifications) == 1:
            # 单条结果，直接发送（使用卡片）
            await self._send_result(notifications[0].result, use_card=True)
        else:
            # 多条结果，合并发送（使用卡片）
            await self._send_merged_results(notifications, use_card=True)
    
    async def _send_merged_results(self, notifications: list[PendingNotification], use_card: bool = True) -> None:
        """发送合并后的结果
        
        Args:
            notifications: 等待发送的通知列表
            use_card: 是否使用卡片消息（更美观），默认 True
        """
        try:
            if not hasattr(self._feishu_handler, 'client'):
                logger.error("Feishu handler does not have client attribute")
                return
            
            client = self._feishu_handler.client
            
            if use_card and hasattr(client, 'send_interactive_card'):
                # 使用卡片消息
                card = self._build_merged_card(notifications)
                await asyncio.to_thread(
                    client.send_interactive_card,
                    self.chat_id,
                    card,
                )
            else:
                # 使用文本消息
                lines = [f"📋 定时任务汇总 ({len(notifications)} 个任务)"]
                lines.append("")
                
                for i, notification in enumerate(notifications, 1):
                    result = notification.result
                    status = "✅" if result.success else "❌"
                    lines.append(f"{i}. {status} {result.job_id}")
                    if result.success and result.output:
                        output = result.output[:200].replace('\n', ' ')
                        if len(result.output) > 200:
                            output += "..."
                        lines.append(f"   {output}")
                    elif not result.success and result.error:
                        lines.append(f"   错误: {result.error[:100]}")
                    lines.append("")
                
                message = "\n".join(lines)
                await asyncio.to_thread(
                    client.send_text_message,
                    self.chat_id,
                    message,
                )
            
            logger.info(f"Sent merged results to chat {self.chat_id}: {len(notifications)} notifications")
                
        except Exception as e:
            logger.exception(f"Failed to send merged results: {e}")
    
    def _build_merged_card(self, notifications: list[PendingNotification]) -> dict:
        """构建合并结果卡片
        
        Args:
            notifications: 等待发送的通知列表
            
        Returns:
            飞书卡片 JSON
        """
        # 统计成功/失败
        success_count = sum(1 for n in notifications if n.result.success)
        fail_count = len(notifications) - success_count
        
        # 确定标题颜色
        if fail_count == 0:
            template = "green"
        elif success_count == 0:
            template = "red"
        else:
            template = "orange"
        
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**执行统计:** ✅ {success_count} 成功 | ❌ {fail_count} 失败"
                }
            },
            {"tag": "hr"}
        ]
        
        # 添加每个任务的简要信息
        for i, notification in enumerate(notifications[:10], 1):  # 最多显示10个
            result = notification.result
            status_icon = "✅" if result.success else "❌"
            
            # 截断输出
            if result.success and result.output:
                preview = result.output[:100].replace('\n', ' ')
                if len(result.output) > 100:
                    preview += "..."
            elif not result.success and result.error:
                preview = f"错误: {result.error[:80]}"
            else:
                preview = "(无输出)"
            
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"{i}. {status_icon} `{result.job_id}`\n   {preview}"
                }
            })
        
        if len(notifications) > 10:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"*还有 {len(notifications) - 10} 个任务未显示*"
                }
            })
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": f"📋 定时任务汇总 ({len(notifications)} 个)"}
            },
            "elements": elements
        }
