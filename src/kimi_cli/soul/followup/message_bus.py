"""消息总线实现 - 用于子 Agent 向主 Agent 发送消息.

设计原则:
- 极简进程内消息总线，无持久化
- 主 Agent 离线则消息丢弃（因为子 Agent 应该已被清理）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from kimi_cli.utils.logging import logger


# 特殊目标标识
BROADCAST_TARGET = "*"  # 广播给所有订阅者
MAIN_AGENT_TARGET = "main"  # 主 Agent 的目标标识


@dataclass
class BackgroundTaskResultMessage:
    """后台任务结果消息.

    子 Agent 完成任务后，通过消息总线发送给指定目标 Agent.
    """

    # 消息类型标识
    type: str = field(default="background_task_result")

    # 任务元信息
    task_id: str = ""
    task_description: str = ""
    subagent_name: str = ""

    # 执行结果
    status: str = ""  # "completed" | "failed" | "stopped"
    output: str = ""
    output_file: Optional[str] = None
    error_message: Optional[str] = None

    # 执行统计
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    token_usage: Optional[int] = None

    # 会话标识
    session_id: str = ""

    # 目标接收方（可选，默认为广播）
    # 可以是:
    # - "*" 或 None: 广播给所有订阅者
    # - "main": 主 Agent
    # - "subagent-{name}": 特定子 Agent
    # - 其他自定义标识
    target: str = field(default=BROADCAST_TARGET)


class AgentMessageBus:
    """极简进程内消息总线 - 无持久化.

    职责: Agent 间消息传递，支持定向投递和广播
    特点: 
    - 支持按 target 定向投递
    - 支持广播给所有订阅者
    - 目标 Agent 离线则消息丢弃
    """

    def __init__(self) -> None:
        # session_id -> {target: callback} 映射
        # target 可以是: "main", "subagent-{name}", 或其他标识
        self._subscribers: Dict[str, Dict[str, Callable[[BackgroundTaskResultMessage], asyncio.Coroutine]]] = {}

    def subscribe(
        self,
        session_id: str,
        callback: Callable[[BackgroundTaskResultMessage], asyncio.Coroutine],
        target: str = BROADCAST_TARGET,
    ) -> None:
        """Agent 启动时订阅消息.

        Args:
            session_id: 会话 ID
            callback: 消息处理回调函数
            target: 此订阅者的标识，用于定向投递。默认为广播标识。
        """
        if session_id not in self._subscribers:
            self._subscribers[session_id] = {}
        
        self._subscribers[session_id][target] = callback
        logger.debug(f"Message bus subscribed for session: {session_id}, target: {target} (total: {len(self._subscribers[session_id])})")

    def unsubscribe(
        self,
        session_id: str,
        target: str | None = None,
    ) -> None:
        """Session 结束时取消订阅.

        Args:
            session_id: 会话 ID
            target: 要取消的订阅者标识，如果为 None 则取消该 session 的所有订阅
        """
        if session_id not in self._subscribers:
            return

        if target is None:
            # 取消该 session 的所有订阅
            del self._subscribers[session_id]
            logger.debug(f"Message bus unsubscribed all for session: {session_id}")
        else:
            # 只取消指定的订阅
            subscribers = self._subscribers[session_id]
            if target in subscribers:
                del subscribers[target]
                logger.debug(f"Message bus unsubscribed target {target} for session: {session_id} (remaining: {len(subscribers)})")
            if not subscribers:
                del self._subscribers[session_id]

    async def publish(self, session_id: str, message: BackgroundTaskResultMessage) -> bool:
        """发布消息.

        如果 message.target 指定了目标，则只发给该目标。
        否则广播给所有订阅者。

        Args:
            session_id: 会话 ID
            message: 要发布的消息

        Returns:
            是否成功投递
        """
        subscribers = self._subscribers.get(session_id)
        if not subscribers:
            # 没有 Agent 订阅，消息自然丢弃
            logger.debug(
                f"Message dropped for offline session {session_id}: {message.task_id}"
            )
            return False

        # 确定投递目标
        target = getattr(message, 'target', BROADCAST_TARGET)
        
        if target and target != BROADCAST_TARGET:
            # 定向投递
            callback = subscribers.get(target)
            if callback:
                try:
                    await callback(message)
                    logger.debug(f"Message delivered to {target} in session {session_id}: {message.task_id}")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to deliver message to {target} in session {session_id}: {e}")
                    return False
            else:
                logger.debug(f"Target {target} not found in session {session_id}, message dropped: {message.task_id}")
                return False
        else:
            # 广播给所有订阅者
            success = False
            for target_id, callback in list(subscribers.items()):
                try:
                    await callback(message)
                    success = True
                except Exception as e:
                    logger.warning(f"Failed to deliver message to {target_id} in session {session_id}: {e}")

            if success:
                logger.debug(f"Message broadcast to session {session_id}: {message.task_id} (to {len(subscribers)} subscribers)")
            return success

    def is_subscribed(self, session_id: str, target: str | None = None) -> bool:
        """检查 session 是否已订阅.

        Args:
            session_id: 会话 ID
            target: 可选，检查特定目标是否已订阅

        Returns:
            是否已订阅
        """
        if session_id not in self._subscribers:
            return False
        
        if target is not None:
            return target in self._subscribers[session_id]
        
        return len(self._subscribers[session_id]) > 0


# 全局消息总线实例
message_bus = AgentMessageBus()
