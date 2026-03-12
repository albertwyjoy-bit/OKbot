"""消息队列实现 - 双队列设计：Steering Queue + Follow-up Queue.

设计原则:
- Steering Queue: 后台任务结果，在 tool call 边界即时处理
- Follow-up Queue: 用户后续消息，在 turn 结束后处理
- 两者独立，互不干扰，各自按时间顺序排队
- 不设超时，永久保留直到处理
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

from kosong.message import Message

from kimi_cli.soul.message import system
from kimi_cli.utils.logging import logger
from kimi_cli.wire.types import TextPart

if TYPE_CHECKING:
    from kimi_cli.soul.followup import BackgroundTaskResultMessage
    from kimi_cli.soul.context import Context


@dataclass
class QueueItem:
    """队列中的消息项."""

    # 消息类型
    type: Literal["user", "system_notification"] = "user"

    # 消息内容
    content: str = ""

    # 时间戳
    timestamp: float = field(default_factory=time.time)

    # 元数据
    metadata: dict = field(default_factory=dict)


class MessageQueue:
    """双队列设计：Steering Queue + Follow-up Queue.

    设计原则:
    - Steering Queue: 需要在 tool call 边界即时处理的消息（如子 Agent 结果）
    - Follow-up Queue: 在 turn 结束后处理的消息（如用户的后续指令）
    - 两者互不干扰，各自独立消费
    """

    def __init__(self) -> None:
        # Steering Queue: tool call 边界即时处理
        self._steering_queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        # Follow-up Queue: turn 结束后处理
        self._followup_queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._event = asyncio.Event()
        self._closed = False

    async def put_user_message(self, content: str, source: str = "feishu") -> None:
        """用户发送的消息 -> 放入 Follow-up Queue.

        Args:
            content: 消息内容
            source: 消息来源标识
        """
        if self._closed:
            logger.warning("Cannot put message to closed queue")
            return

        item = QueueItem(
            type="user",
            content=content,
            timestamp=time.time(),
            metadata={"source": source},
        )
        await self._followup_queue.put(item)
        self._event.set()
        logger.debug(f"User message queued to follow-up queue from {source}: {content[:50]}...")

    async def put_task_result(self, result: "BackgroundTaskResultMessage") -> None:
        """后台子 Agent 返回的结果 -> 放入 Steering Queue.

        Args:
            result: 后台任务结果消息
        """
        if self._closed:
            logger.warning("Cannot put task result to closed queue")
            return

        # 构建 system-level 提示内容
        system_content = self._format_task_result(result)

        item = QueueItem(
            type="system_notification",
            content=system_content,
            timestamp=time.time(),
            metadata={
                "task_id": result.task_id,
                "status": result.status,
            },
        )
        await self._steering_queue.put(item)
        self._event.set()
        logger.debug(f"Task result queued to steering queue: {result.task_id} ({result.status})")

    def _format_task_result(self, result: "BackgroundTaskResultMessage") -> str:
        """将 Task Result 格式化为 system-level 提示.

        Args:
            result: 后台任务结果

        Returns:
            格式化后的 system-level 提示内容
        """
        lines = [
            "Background task completed:",
            f"- Task ID: {result.task_id}",
            f"- Description: {result.task_description}",
            f"- Status: {result.status}",
        ]

        # 添加输出摘要（截断到 10000 字符）
        output = result.output
        if output:
            max_len = 10000
            if len(output) > max_len:
                output = output[:max_len] + "\n...truncated"
            lines.append(f"- Result:\n{output}")

        # 添加日志文件路径
        if result.output_file:
            lines.append(f"- Log file: {result.output_file}")

        # 添加错误信息
        if result.error_message:
            lines.append(f"- Error: {result.error_message}")

        # 添加时间信息
        if result.started_at and result.completed_at:
            lines.append(f"- Started: {result.started_at}")
            lines.append(f"- Completed: {result.completed_at}")

        return "\n".join(lines)

    # ==================== Steering Queue 操作 ====================

    def get_steering_messages(self) -> list[QueueItem]:
        """非阻塞获取 Steering Queue 中的所有消息.

        在 tool call 边界调用，用于即时插入子 Agent 结果。

        Returns:
            Steering 消息列表，队列为空时返回空列表
        """
        messages = []
        while not self._steering_queue.empty():
            try:
                item = self._steering_queue.get_nowait()
                if item is not None:
                    messages.append(item)
            except asyncio.QueueEmpty:
                break
        return messages

    def steering_queue_empty(self) -> bool:
        """检查 Steering Queue 是否为空."""
        return self._steering_queue.empty()

    def steering_queue_size(self) -> int:
        """获取 Steering Queue 大小."""
        return self._steering_queue.qsize()

    # ==================== Follow-up Queue 操作 ====================

    def get_followup_messages(self) -> list[QueueItem]:
        """非阻塞获取 Follow-up Queue 中的所有消息.

        在 turn 边界调用，用于处理用户的后续指令。

        Returns:
            Follow-up 消息列表，队列为空时返回空列表
        """
        messages = []
        while not self._followup_queue.empty():
            try:
                item = self._followup_queue.get_nowait()
                if item is not None:
                    messages.append(item)
            except asyncio.QueueEmpty:
                break
        return messages

    def followup_queue_empty(self) -> bool:
        """检查 Follow-up Queue 是否为空."""
        return self._followup_queue.empty()

    def followup_queue_size(self) -> int:
        """获取 Follow-up Queue 大小."""
        return self._followup_queue.qsize()

    # ==================== 通用操作 ====================

    def qsize(self) -> int:
        """获取两个队列的总大小.
        
        Returns:
            Steering Queue 和 Follow-up Queue 的总消息数
        """
        return self._steering_queue.qsize() + self._followup_queue.qsize()

    def empty(self) -> bool:
        """检查两个队列是否都为空.
        
        Returns:
            当 Steering Queue 和 Follow-up Queue 都为空时返回 True
        """
        return self._steering_queue.empty() and self._followup_queue.empty()

    def close(self) -> None:
        """关闭队列，不再接受新消息."""
        self._closed = True
        self._event.set()
        logger.debug("Message queues closed")

    def create_steering_message(self, item: QueueItem) -> Message:
        """将 Steering Queue 项转换为 steering message (Role=user, 带 system 包装).

        Args:
            item: 队列中的消息项

        Returns:
            构造好的 Message，role="user"，system 包装内容
        """
        # 所有 steering 消息都包装为 role="user" + system() 标签
        return Message(
            role="user",
            content=[
                system(item.content),
                TextPart(
                    text="Please review the above information and respond accordingly."
                ),
            ],
        )

    async def inject_to_context(self, item: QueueItem, context: "Context") -> None:
        """将 Follow-up Queue 中的消息注入到对话上下文.

        Args:
            item: 队列中的消息项
            context: 对话上下文
        """
        if item.type == "user":
            # 用户消息：正常添加
            await context.append_message(
                Message(role="user", content=[TextPart(text=item.content)])
            )
            logger.debug(f"Follow-up user message injected to context: {item.content[:50]}...")
        else:
            # 其他类型，使用 steering 格式
            message = self.create_steering_message(item)
            await context.append_message(message)
            logger.debug(f"Follow-up message injected to context: {item.type}")
