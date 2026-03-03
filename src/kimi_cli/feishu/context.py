"""Feishu context variables for session isolation.

This module provides ContextVar-based storage for Feishu session information,
ensuring proper isolation between concurrent chat sessions.

Usage:
    # In SDKChatSession.handle_message():
    from kimi_cli.feishu.context import cv_chat_id, cv_client
    
    token_chat = cv_chat_id.set(self.chat_id)
    token_client = cv_client.set(self.client)
    try:
        # Process message - tools will use these context vars
        await process()
    finally:
        cv_chat_id.reset(token_chat)
        cv_client.reset(token_client)

    # In FeishuSendFile:
    from kimi_cli.feishu.context import cv_chat_id, cv_client
    
    chat_id = cv_chat_id.get()
    client = cv_client.get()
    if chat_id and client:
        client.send_file_message(chat_id, file_key)
"""

from __future__ import annotations

import contextvars
from typing import Any

#: Current chat ID for the active Feishu session
#: Set by SDKChatSession.handle_message() before processing
#: Accessed by Feishu tools (FeishuSendFile, FeishuSendMessage, etc.)
cv_chat_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'feishu_chat_id',
    default=None
)

#: Current Feishu SDK client instance
#: Contains the actual client for making API calls
#: Used by tools to send messages, upload files, etc.
cv_client: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    'feishu_client',
    default=None
)


def get_current_chat_id() -> str | None:
    """Get the current chat ID from context.
    
    Returns:
        The current chat ID or None if not in a Feishu session.
    """
    return cv_chat_id.get()


def get_current_client() -> Any | None:
    """Get the current Feishu client from context.
    
    Returns:
        The current FeishuSDKClient instance or None.
    """
    return cv_client.get()


def is_in_feishu_session() -> bool:
    """Check if currently in a Feishu session context.
    
    Returns:
        True if both chat_id and client are set in context.
    """
    return cv_chat_id.get() is not None and cv_client.get() is not None
