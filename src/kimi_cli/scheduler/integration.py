"""Integration module for connecting scheduler with OKbot's SDK server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from kimi_cli.scheduler.scheduler import get_scheduler, set_scheduler

if TYPE_CHECKING:
    from kimi_cli.feishu.sdk_server import SDKChatSession, SDKMessageHandler


async def initialize_scheduler(feishu_handler: SDKMessageHandler) -> None:
    """Initialize scheduler with feishu handler.
    
    This should be called when the server starts.
    
    Args:
        feishu_handler: The SDK message handler instance
    """
    try:
        scheduler = get_scheduler()
        await scheduler.initialize(feishu_handler)
        await scheduler.start()
        logger.info("Scheduler initialized and started with feishu handler")
    except Exception as e:
        logger.exception(f"Failed to initialize scheduler: {e}")


async def shutdown_scheduler() -> None:
    """Shutdown scheduler.
    
    This should be called when the server stops.
    """
    try:
        scheduler = get_scheduler()
        await scheduler.stop()
        logger.info("Scheduler stopped")
    except Exception as e:
        logger.exception(f"Error stopping scheduler: {e}")


def patch_sdk_chat_session():
    """Patch SDKChatSession to support scheduled tasks.
    
    This function adds the necessary methods and attributes to SDKChatSession
    to support scheduled task queue flushing.
    """
    from kimi_cli.feishu.sdk_server import SDKChatSession
    
    # Store original handle_message
    original_handle_message = SDKChatSession.handle_message
    
    async def patched_handle_message(self: SDKChatSession, message_text: str) -> None:
        """Patched handle_message that flushes pending notifications after processing."""
        try:
            # Call original method
            await original_handle_message(self, message_text)
        finally:
            # Flush pending scheduled notifications
            await _flush_pending_notifications(self)
    
    # Replace the method
    SDKChatSession.handle_message = patched_handle_message  # type: ignore
    
    logger.info("Patched SDKChatSession.handle_message for scheduled task support")


async def _flush_pending_notifications(session: SDKChatSession) -> None:
    """Flush pending scheduled notifications for a session.
    
    Args:
        session: The SDK chat session
    """
    try:
        from kimi_cli.scheduler.scheduler import get_scheduler
        
        scheduler = get_scheduler()
        if not scheduler._initialized:
            return
        
        # Find the scheduled session for this chat
        session_manager = scheduler._session_manager
        if not session_manager:
            return
        
        # Construct the scheduled session ID pattern
        # sched_{user_id}_{job_id}
        # We need to find all scheduled sessions for this user/chat
        
        # For now, we use a simpler approach: check all scheduled sessions
        # that match this chat_id
        for session_id, sched_session in list(session_manager._scheduled_sessions.items()):
            if sched_session.chat_id == session.chat_id:
                await sched_session.flush_pending_notifications()
                logger.debug(f"Flushed pending notifications for {session_id}")
                
    except Exception as e:
        logger.exception(f"Error flushing pending notifications: {e}")


async def setup_scheduler_integration(feishu_handler: SDKMessageHandler) -> None:
    """Setup scheduler integration.
    
    This function:
    1. Patches SDKChatSession
    2. Initializes the scheduler
    
    Args:
        feishu_handler: The SDK message handler instance
    """
    # Patch SDKChatSession
    patch_sdk_chat_session()
    
    # Initialize scheduler
    await initialize_scheduler(feishu_handler)


# Hook for SDKMessageHandler._init_accounts
_original_init_accounts = None

async def _patched_init_accounts(self):
    """Patched _init_accounts that also initializes scheduler."""
    # Call original
    if _original_init_accounts:
        await _original_init_accounts(self)
    
    # Initialize scheduler
    await setup_scheduler_integration(self)


def patch_sdk_message_handler():
    """Patch SDKMessageHandler to initialize scheduler on startup."""
    from kimi_cli.feishu.sdk_server import SDKMessageHandler
    
    global _original_init_accounts
    
    # We patch handle_message_event to handle /cron commands
    original_handle_message_event = SDKMessageHandler.handle_message_event
    
    async def patched_handle_message_event(self, data) -> None:
        """Patched handle_message_event that handles /cron commands."""
        from lark_oapi.event.callback.model.p2_im_message_receive_v1 import (
            P2ImMessageReceiveV1,
        )
        
        event: P2ImMessageReceiveV1 = data
        message = event.event.message
        
        # Extract text content
        import json
        try:
            content = json.loads(message.content)
            text = content.get("text", "")
        except json.JSONDecodeError:
            text = ""
        
        # Check if it's a /cron command
        if text.strip().startswith("/cron"):
            # Import here to avoid circular imports
            from kimi_cli.scheduler.commands import handle_cron_command
            
            # Get or create session
            session_key = self._get_session_key(message.chat_id, event.event.sender.sender_id.open_id)
            
            async with self._lock:
                session = self._sessions.get(session_key)
                
                if session is None:
                    # Create session first
                    from kimi_cli.feishu.sdk_server import SDKChatSession
                    
                    soul = await self._create_soul_for_session(session_key)
                    session = SDKChatSession(
                        chat_id=message.chat_id,
                        user_id=event.event.sender.sender_id.open_id,
                        client=self.client,
                        config=self.config,
                        soul=soul,
                    )
                    self._sessions[session_key] = session
            
            # Handle the command
            handled, response = await handle_cron_command(text, session)
            
            if handled and response:
                import asyncio
                await asyncio.to_thread(
                    self.client.send_text_message,
                    message.chat_id,
                    response,
                )
                return
        
        # Not a /cron command, call original
        await original_handle_message_event(self, data)
    
    # Replace the method
    SDKMessageHandler.handle_message_event = patched_handle_message_event  # type: ignore
    
    logger.info("Patched SDKMessageHandler.handle_message_event for /cron commands")
