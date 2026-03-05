"""Steering & Follow-up Messages 机制实现.

提供消息队列、消息总线和任务管理功能，支持:
1. 用户消息的 Follow-up 排队
2. 后台子 Agent 任务结果的路由
3. 任务生命周期管理
"""

from .message_bus import (
    MAIN_AGENT_TARGET,
    AgentMessageBus,
    BackgroundTaskResultMessage,
    message_bus,
)
from .message_queue import MessageQueue, QueueItem
from .task_manager import SubagentTask, TaskManager, TaskStatus

__all__ = [
    # Message Bus
    "MAIN_AGENT_TARGET",
    "AgentMessageBus",
    "BackgroundTaskResultMessage",
    "message_bus",
    # Message Queue
    "MessageQueue",
    "QueueItem",
    # Task Management
    "SubagentTask",
    "TaskManager",
    "TaskStatus",
]
