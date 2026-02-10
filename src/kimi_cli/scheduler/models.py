"""Data models for scheduler module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


class NotificationMode(Enum):
    """Notification mode for scheduled tasks."""
    SILENT = "silent"      # 静默：仅返回最终结果
    NORMAL = "normal"      # 正常：返回关键步骤
    VERBOSE = "verbose"    # 详细：返回完整过程


@dataclass
class ScheduledJob:
    """定时任务配置"""
    
    # 任务标识
    id: str                              # 任务唯一ID
    
    # 绑定信息（创建时确定，不可更改）
    user_id: str                         # 创建者用户ID
    chat_id: str                         # 绑定的飞书对话ID
    cron: str                            # Cron 表达式
    description: str                     # 任务描述（用于管理显示）
    reminder_text: str | None = None     # 提醒内容（定时触发时发送给用户的消息）
    
    # 可选绑定信息
    chat_type: Literal["p2p", "group"] = "p2p"  # 对话类型
    tenant_key: str | None = None        # 飞书租户
    notification_mode: str = "silent"    # 通知模式
    
    # 运行时信息
    created_at: datetime = field(default_factory=datetime.now)  # 创建时间
    last_run: datetime | None = None     # 上次执行时间
    next_run: datetime | None = None     # 下次执行时间
    is_active: bool = True               # 是否激活
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "cron": self.cron,
            "description": self.description,
            "reminder_text": self.reminder_text,
            "chat_type": self.chat_type,
            "tenant_key": self.tenant_key,
            "notification_mode": self.notification_mode,
            "created_at": self.created_at.isoformat(),
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "is_active": self.is_active,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledJob:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            chat_id=data["chat_id"],
            cron=data["cron"],
            description=data["description"],
            reminder_text=data.get("reminder_text"),
            chat_type=data.get("chat_type", "p2p"),
            tenant_key=data.get("tenant_key"),
            notification_mode=data.get("notification_mode", "silent"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            last_run=datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None,
            next_run=datetime.fromisoformat(data["next_run"]) if data.get("next_run") else None,
            is_active=data.get("is_active", True),
        )


@dataclass
class IncomingMessage:
    """
    统一的消息对象，无论来源是飞书、定时任务还是其他渠道
    
    这个类设计为兼容飞书消息格式，可以从飞书消息直接转换创建
    """
    # 消息内容
    text: str                           # 消息文本内容
    
    # 来源信息
    source: Literal["feishu", "scheduled", "webhook"] = "feishu"  # 消息来源
    source_id: str | None = None        # 来源ID（飞书message_id或job_id）
    
    # 目标信息（创建定时任务时绑定）
    chat_id: str = ""                   # 飞书对话ID（群聊或私聊）
    user_id: str = ""                   # 创建者用户ID
    chat_type: Literal["p2p", "group"] = "p2p"  # 对话类型
    tenant_key: str | None = None       # 飞书租户标识
    
    # 飞书原始消息相关字段（可选，用于兼容）
    message_type: str = "text"          # 飞书消息类型: text, image, file, audio
    content: dict[str, Any] | None = None  # 飞书原始消息内容
    
    # 元数据
    metadata: dict[str, Any] | None = None  # 扩展元数据
    created_at: datetime = field(default_factory=datetime.now)  # 消息创建时间
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "text": self.text,
            "source": self.source,
            "source_id": self.source_id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "chat_type": self.chat_type,
            "tenant_key": self.tenant_key,
            "message_type": self.message_type,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IncomingMessage:
        """Create from dictionary."""
        return cls(
            text=data["text"],
            source=data.get("source", "feishu"),
            source_id=data.get("source_id"),
            chat_id=data.get("chat_id", ""),
            user_id=data.get("user_id", ""),
            chat_type=data.get("chat_type", "p2p"),
            tenant_key=data.get("tenant_key"),
            message_type=data.get("message_type", "text"),
            content=data.get("content"),
            metadata=data.get("metadata"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
        )
    
    @classmethod
    def from_feishu_message(
        cls,
        message: Any,  # lark.im.v1.P2ImMessageReceiveV1
    ) -> IncomingMessage | None:
        """从飞书消息创建 IncomingMessage
        
        Args:
            message: 飞书消息事件 (P2ImMessageReceiveV1)
            
        Returns:
            IncomingMessage 实例，如果解析失败则返回 None
        """
        import json
        
        try:
            event = message.event
            msg = event.message
            sender = event.sender
            
            # 解析 content
            content = {}
            try:
                content = json.loads(msg.content)
            except json.JSONDecodeError:
                pass
            
            # 提取文本内容
            text = ""
            if msg.message_type == "text":
                text = content.get("text", "")
            elif msg.message_type == "image":
                text = f"[图片消息] image_key={content.get('image_key', '')}"
            elif msg.message_type == "file":
                text = f"[文件消息] file_name={content.get('file_name', '')}"
            elif msg.message_type == "audio":
                text = f"[语音消息] file_key={content.get('file_key', '')}"
            
            return cls(
                text=text,
                source="feishu",
                source_id=msg.message_id if hasattr(msg, 'message_id') else None,
                chat_id=msg.chat_id,
                user_id=sender.sender_id.open_id,
                chat_type=msg.chat_type,  # type: ignore
                tenant_key=getattr(msg, 'tenant_key', None),
                message_type=msg.message_type,
                content=content,
                metadata={
                    "parent_message_id": getattr(msg, 'parent_message_id', None),
                    "create_time": getattr(msg, 'create_time', None),
                },
                created_at=datetime.now(),
            )
        except Exception as e:
            from loguru import logger
            logger.exception(f"Failed to create IncomingMessage from Feishu message: {e}")
            return None


@dataclass
class ScheduledResult:
    """定时任务执行结果"""
    
    job_id: str                          # 任务ID
    success: bool                        # 是否成功
    output: str | None = None            # 成功时的输出
    error: str | None = None             # 失败时的错误信息
    executed_at: datetime = field(default_factory=datetime.now)  # 执行时间
    
    # 生成的文件列表（文件路径）
    files: list[str] = field(default_factory=list)
    
    # 上传到飞书后的文件信息
    feishu_files: list[dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "job_id": self.job_id,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "executed_at": self.executed_at.isoformat(),
            "files": self.files,
            "feishu_files": self.feishu_files,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledResult:
        """Create from dictionary."""
        return cls(
            job_id=data["job_id"],
            success=data["success"],
            output=data.get("output"),
            files=data.get("files", []),
            feishu_files=data.get("feishu_files", []),
            error=data.get("error"),
            executed_at=datetime.fromisoformat(data["executed_at"]) if data.get("executed_at") else datetime.now(),
        )
    
    def format_message(self) -> str:
        """Format result as user-friendly message."""
        if self.success:
            return (
                f"✅ 定时任务完成\n"
                f"任务: {self.job_id}\n"
                f"执行时间: {self.executed_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"结果:\n{self.output or '(无输出)'}"
            )
        else:
            return (
                f"❌ 定时任务失败\n"
                f"任务: {self.job_id}\n"
                f"执行时间: {self.executed_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"错误: {self.error or '未知错误'}"
            )


@dataclass
class PendingNotification:
    """等待发送的通知"""
    
    result: ScheduledResult              # 执行结果
    chat_id: str                         # 目标对话ID
    user_id: str                         # 目标用户ID
    created_at: datetime = field(default_factory=datetime.now)  # 创建时间
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "result": self.result.to_dict(),
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingNotification:
        """Create from dictionary."""
        return cls(
            result=ScheduledResult.from_dict(data["result"]),
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
        )
