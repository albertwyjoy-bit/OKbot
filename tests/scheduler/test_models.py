"""Tests for scheduler models."""

from __future__ import annotations

from datetime import datetime

import pytest

from kimi_cli.scheduler.models import (
    IncomingMessage,
    NotificationMode,
    PendingNotification,
    ScheduledJob,
    ScheduledResult,
)


class TestScheduledJob:
    """Tests for ScheduledJob model."""

    def test_create_job(self):
        """Test creating a scheduled job."""
        job = ScheduledJob(
            id="job_123",
            user_id="user_456",
            chat_id="chat_789",
            cron="0 9 * * *",
            description="Daily report",
        )
        
        assert job.id == "job_123"
        assert job.user_id == "user_456"
        assert job.chat_id == "chat_789"
        assert job.cron == "0 9 * * *"
        assert job.description == "Daily report"
        assert job.chat_type == "p2p"
        assert job.is_active is True
        assert job.notification_mode == "silent"

    def test_job_to_dict(self):
        """Test converting job to dictionary."""
        now = datetime.now()
        job = ScheduledJob(
            id="job_123",
            user_id="user_456",
            chat_id="chat_789",
            cron="0 9 * * *",
            description="Daily report",
            created_at=now,
            last_run=now,
            is_active=True,
        )
        
        data = job.to_dict()
        
        assert data["id"] == "job_123"
        assert data["user_id"] == "user_456"
        assert data["cron"] == "0 9 * * *"
        assert data["description"] == "Daily report"
        assert data["created_at"] == now.isoformat()
        assert data["last_run"] == now.isoformat()
        assert data["is_active"] is True

    def test_job_from_dict(self):
        """Test creating job from dictionary."""
        now = datetime.now()
        data = {
            "id": "job_123",
            "user_id": "user_456",
            "chat_id": "chat_789",
            "cron": "0 9 * * *",
            "description": "Daily report",
            "chat_type": "group",
            "tenant_key": "tenant_1",
            "notification_mode": "normal",
            "created_at": now.isoformat(),
            "last_run": now.isoformat(),
            "next_run": now.isoformat(),
            "is_active": False,
        }
        
        job = ScheduledJob.from_dict(data)
        
        assert job.id == "job_123"
        assert job.chat_type == "group"
        assert job.tenant_key == "tenant_1"
        assert job.notification_mode == "normal"
        assert job.is_active is False

    def test_job_from_dict_defaults(self):
        """Test creating job from dictionary with defaults."""
        data = {
            "id": "job_123",
            "user_id": "user_456",
            "chat_id": "chat_789",
            "cron": "0 9 * * *",
            "description": "Daily report",
        }
        
        job = ScheduledJob.from_dict(data)
        
        assert job.chat_type == "p2p"  # Default
        assert job.tenant_key is None
        assert job.notification_mode == "silent"
        assert job.is_active is True


class TestIncomingMessage:
    """Tests for IncomingMessage model."""

    def test_create_message(self):
        """Test creating an incoming message."""
        msg = IncomingMessage(
            text="Hello",
            source="feishu",
            chat_id="chat_123",
            user_id="user_456",
        )
        
        assert msg.text == "Hello"
        assert msg.source == "feishu"
        assert msg.chat_id == "chat_123"
        assert msg.user_id == "user_456"
        assert msg.chat_type == "p2p"
        assert msg.message_type == "text"

    def test_message_to_dict(self):
        """Test converting message to dictionary."""
        now = datetime.now()
        msg = IncomingMessage(
            text="Hello",
            source="scheduled",
            source_id="job_123",
            chat_id="chat_456",
            user_id="user_789",
            chat_type="group",
            tenant_key="tenant_1",
            metadata={"cron": "0 9 * * *"},
            created_at=now,
        )
        
        data = msg.to_dict()
        
        assert data["text"] == "Hello"
        assert data["source"] == "scheduled"
        assert data["source_id"] == "job_123"
        assert data["metadata"] == {"cron": "0 9 * * *"}
        assert data["created_at"] == now.isoformat()

    def test_message_from_dict(self):
        """Test creating message from dictionary."""
        now = datetime.now()
        data = {
            "text": "Hello",
            "source": "scheduled",
            "source_id": "job_123",
            "chat_id": "chat_456",
            "user_id": "user_789",
            "chat_type": "group",
            "created_at": now.isoformat(),
        }
        
        msg = IncomingMessage.from_dict(data)
        
        assert msg.text == "Hello"
        assert msg.source == "scheduled"
        assert msg.chat_type == "group"


class TestScheduledResult:
    """Tests for ScheduledResult model."""

    def test_create_success_result(self):
        """Test creating a successful result."""
        result = ScheduledResult(
            job_id="job_123",
            success=True,
            output="Task completed successfully",
        )
        
        assert result.job_id == "job_123"
        assert result.success is True
        assert result.output == "Task completed successfully"
        assert result.error is None

    def test_create_failure_result(self):
        """Test creating a failed result."""
        result = ScheduledResult(
            job_id="job_123",
            success=False,
            error="Something went wrong",
        )
        
        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.output is None

    def test_format_message_success(self):
        """Test formatting success message."""
        result = ScheduledResult(
            job_id="job_123",
            success=True,
            output="Task output",
        )
        
        message = result.format_message()
        
        assert "✅" in message
        assert "job_123" in message
        assert "Task output" in message

    def test_format_message_failure(self):
        """Test formatting failure message."""
        result = ScheduledResult(
            job_id="job_123",
            success=False,
            error="Error details",
        )
        
        message = result.format_message()
        
        assert "❌" in message
        assert "job_123" in message
        assert "Error details" in message

    def test_result_to_dict_with_files(self):
        """Test converting result with files to dictionary."""
        result = ScheduledResult(
            job_id="job_123",
            success=True,
            output="Done",
            files=["/path/to/file.txt"],
            feishu_files=[{"file_key": "key123", "file_name": "file.txt"}],
        )
        
        data = result.to_dict()
        
        assert data["files"] == ["/path/to/file.txt"]
        assert data["feishu_files"] == [{"file_key": "key123", "file_name": "file.txt"}]


class TestPendingNotification:
    """Tests for PendingNotification model."""

    def test_create_notification(self):
        """Test creating a pending notification."""
        result = ScheduledResult(
            job_id="job_123",
            success=True,
            output="Done",
        )
        notification = PendingNotification(
            result=result,
            chat_id="chat_456",
            user_id="user_789",
        )
        
        assert notification.result.job_id == "job_123"
        assert notification.chat_id == "chat_456"
        assert notification.user_id == "user_789"

    def test_notification_to_dict(self):
        """Test converting notification to dictionary."""
        result = ScheduledResult(
            job_id="job_123",
            success=True,
            output="Done",
        )
        notification = PendingNotification(
            result=result,
            chat_id="chat_456",
            user_id="user_789",
        )
        
        data = notification.to_dict()
        
        assert data["result"]["job_id"] == "job_123"
        assert data["chat_id"] == "chat_456"
        assert data["user_id"] == "user_789"

    def test_notification_from_dict(self):
        """Test creating notification from dictionary."""
        now = datetime.now()
        data = {
            "result": {
                "job_id": "job_123",
                "success": True,
                "output": "Done",
                "executed_at": now.isoformat(),
            },
            "chat_id": "chat_456",
            "user_id": "user_789",
            "created_at": now.isoformat(),
        }
        
        notification = PendingNotification.from_dict(data)
        
        assert notification.result.job_id == "job_123"
        assert notification.chat_id == "chat_456"


class TestNotificationMode:
    """Tests for NotificationMode enum."""

    def test_enum_values(self):
        """Test notification mode enum values."""
        assert NotificationMode.SILENT.value == "silent"
        assert NotificationMode.NORMAL.value == "normal"
        assert NotificationMode.VERBOSE.value == "verbose"
