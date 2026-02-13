"""Message dispatcher for unified message handling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from kimi_cli.scheduler.models import IncomingMessage, ScheduledJob
from kimi_cli.scheduler.store import PendingResultStore

if TYPE_CHECKING:
    from kimi_cli.scheduler.scheduler import Scheduler


class MessageDispatcher:
    """
    统一的消息分发器
    无论消息来源是飞书、定时任务还是其他渠道，都通过这里分发
    """
    
    def __init__(
        self,
        scheduler: Scheduler,
        session_manager: Any,  # SessionManager
    ):
        """Initialize message dispatcher.
        
        Args:
            scheduler: 调度器实例
            session_manager: 会话管理器
        """
        self._scheduler = scheduler
        self._session_manager = session_manager
    
    async def dispatch(self, message: IncomingMessage) -> None:
        """
        分发消息到对应的 Session 处理
        
        Args:
            message: 统一消息对象
        """
        # 1. 获取或创建 Session
        # 注意：定时任务使用独立的 Session ID
        session_id = self._get_session_id(message)
        
        logger.info(f"Dispatching message from {message.source}: {session_id}")
        
        # 2. 根据来源类型处理
        if message.source == "feishu":
            # 飞书消息：正常处理
            await self._handle_feishu_message(message, session_id)
        elif message.source == "scheduled":
            # 定时任务：静默处理，进入等待队列
            await self._handle_scheduled_message(message, session_id)
    
    def _get_session_id(self, message: IncomingMessage) -> str:
        """
        根据消息来源生成 Session ID
        
        飞书消息: feishu_{chat_id}
        定时任务: sched_{user_id}_{job_id}
        """
        if message.source == "feishu":
            return f"feishu_{message.chat_id}"
        elif message.source == "scheduled":
            return f"sched_{message.user_id}_{message.source_id}"
        else:
            return f"other_{message.chat_id}"
    
    async def _handle_feishu_message(self, message: IncomingMessage, session_id: str) -> None:
        """处理飞书消息
        
        现在飞书消息也通过 IncomingMessage 统一处理，便于扩展
        """
        # 获取或创建飞书会话
        session = await self._session_manager.get_or_create_feishu_session(
            session_id=session_id,
            chat_id=message.chat_id,
            user_id=message.user_id,
        )
        
        if not session:
            logger.error(f"Failed to get or create Feishu session: {session_id}")
            return
        
        # 设置当前对话上下文
        if hasattr(session, 'client') and session.client:
            session.client.set_current_chat_id(message.chat_id)
        
        # 根据消息类型处理
        if message.message_type == "text":
            # 文本消息正常处理
            if hasattr(session, 'handle_message'):
                await session.handle_message(message.text)
            else:
                logger.error(f"Session {session_id} does not have handle_message method")
        
        elif message.message_type == "image":
            # 图片消息 - 下载并保存
            await self._handle_feishu_image(message, session)
        
        elif message.message_type == "file":
            # 文件消息 - 下载并保存
            await self._handle_feishu_file(message, session)
        
        elif message.message_type == "audio":
            # 语音消息 - 下载并识别
            await self._handle_feishu_audio(message, session)
        
        else:
            logger.warning(f"Unsupported message type: {message.message_type}")
            if hasattr(session, 'client') and session.client:
                import asyncio
                await asyncio.to_thread(
                    session.client.send_text_message,
                    message.chat_id,
                    f"不支持的消息类型: {message.message_type}",
                )
    
    async def _handle_feishu_image(self, message: IncomingMessage, session: Any) -> None:
        """处理飞书图片消息"""
        # 这里可以实现图片处理逻辑
        # 目前让 session.handle_message 处理
        if hasattr(session, 'handle_message'):
            await session.handle_message(message.text)
    
    async def _handle_feishu_file(self, message: IncomingMessage, session: Any) -> None:
        """处理飞书文件消息"""
        # 这里可以实现文件处理逻辑
        if hasattr(session, 'handle_message'):
            await session.handle_message(message.text)
    
    async def _handle_feishu_audio(self, message: IncomingMessage, session: Any) -> None:
        """处理飞书语音消息"""
        # 这里可以实现语音处理逻辑
        if hasattr(session, 'handle_message'):
            await session.handle_message(message.text)
    
    async def _handle_scheduled_message(self, message: IncomingMessage, session_id: str) -> None:
        """处理定时任务消息"""
        from kimi_cli.scheduler.scheduler import Scheduler
        import asyncio
        
        # 获取或创建定时任务会话
        session = await self._session_manager.get_or_create_scheduled_session(
            session_id=session_id,
            chat_id=message.chat_id,
            user_id=message.user_id,
            source=message.source,
        )
        
        if not session:
            logger.error(f"Failed to create scheduled session: {session_id}")
            return
        
        # 竞争条件防护：使用 SessionManager 级别的锁来保护 session 的获取和初始化
        # 这确保了 _execution_lock 只被创建一次
        async with self._session_manager._lock:
            if not hasattr(session, '_execution_lock'):
                session._execution_lock = asyncio.Lock()
        
        # 现在可以安全地使用 session._execution_lock
        async with session._execution_lock:
            # 检查当前是否忙碌
            if hasattr(session, 'is_processing') and session.is_processing():
                # 忙碌：加入等待队列
                logger.info(f"Session {session_id} is busy, queuing scheduled task")
                await session.queue_scheduled_task(message)
            else:
                # 空闲：直接执行
                logger.info(f"Session {session_id} is idle, executing scheduled task directly")
                await session.execute_scheduled_task(message)


class SessionManager:
    """
    管理飞书会话和定时任务会话
    两者完全隔离，互不影响
    """
    
    def __init__(
        self,
        feishu_handler: Any,  # SDKMessageHandler
        pending_store: PendingResultStore,
    ):
        """Initialize session manager.
        
        Args:
            feishu_handler: 飞书消息处理器
            pending_store: 等待结果存储
        """
        self._feishu_handler = feishu_handler
        self._pending_store = pending_store
        self._scheduled_sessions: dict[str, Any] = {}  # job_id -> ScheduledTaskSession
        self._lock = asyncio.Lock()
    
    async def get_or_create_feishu_session(
        self,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Any:
        """获取或创建飞书会话"""
        # 使用飞书处理器中的现有会话管理
        session_key = f"{chat_id}:{user_id}"
        
        async with self._lock:
            if hasattr(self._feishu_handler, '_sessions'):
                if session_key in self._feishu_handler._sessions:
                    return self._feishu_handler._sessions[session_key]
        
        # 如果会话不存在，返回 None，让飞书处理器自己创建
        logger.warning(f"Feishu session not found: {session_key}")
        return None
    
    async def get_or_create_scheduled_session(
        self,
        session_id: str,
        chat_id: str,
        user_id: str,
        source: str,
    ) -> Any:
        """
        获取或创建定时任务会话
        
        Args:
            session_id: 会话ID
            chat_id: 飞书对话ID
            user_id: 用户ID
            source: 来源类型
            
        Returns:
            ScheduledTaskSession 实例
        """
        from kimi_cli.scheduler.session import ScheduledTaskSession
        
        async with self._lock:
            if session_id in self._scheduled_sessions:
                logger.debug(f"Reusing existing scheduled session: {session_id}")
                return self._scheduled_sessions[session_id]
            
            # 创建新的定时任务会话
            logger.info(f"Creating new scheduled session: {session_id}")
            session = ScheduledTaskSession(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                feishu_handler=self._feishu_handler,
                pending_store=self._pending_store,
            )
            self._scheduled_sessions[session_id] = session
            return session
    
    async def remove_scheduled_session(self, session_id: str) -> None:
        """移除定时任务会话"""
        async with self._lock:
            if session_id in self._scheduled_sessions:
                session = self._scheduled_sessions[session_id]
                # 清理 session 中的 Soul（关闭 MCP 连接）
                try:
                    if hasattr(session, '_cleanup_soul'):
                        await session._cleanup_soul()
                except Exception as e:
                    logger.warning(f"Error cleaning up session {session_id}: {e}")
                
                del self._scheduled_sessions[session_id]
                logger.debug(f"Removed scheduled session: {session_id}")
    
    async def restore_pending_notifications(self) -> None:
        """服务重启后恢复等待队列"""
        try:
            chat_ids = await self._pending_store.list_all_chat_ids()
            for chat_id in chat_ids:
                notifications = await self._pending_store.load(chat_id)
                if notifications:
                    logger.info(f"Restored {len(notifications)} pending notifications for chat {chat_id}")
                    # TODO: 将这些通知关联到对应的会话
        except Exception as e:
            logger.exception(f"Failed to restore pending notifications: {e}")
