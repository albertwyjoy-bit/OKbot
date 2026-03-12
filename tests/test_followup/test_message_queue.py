"""Tests for MessageQueue."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import BackgroundTaskResultMessage, MessageQueue, QueueItem


class TestQueueItem:
    """Test cases for QueueItem."""

    def test_default_creation(self):
        """Test default QueueItem creation."""
        item = QueueItem()

        assert item.type == "user"
        assert item.content == ""
        assert isinstance(item.timestamp, float)
        assert item.metadata == {}

    def test_custom_creation(self):
        """Test custom QueueItem creation."""
        item = QueueItem(
            type="system_notification",
            content="Test content",
            timestamp=1234567890.0,
            metadata={"key": "value"},
        )

        assert item.type == "system_notification"
        assert item.content == "Test content"
        assert item.timestamp == 1234567890.0
        assert item.metadata == {"key": "value"}


class TestMessageQueue:
    """Test cases for MessageQueue."""

    @pytest.fixture
    def queue(self):
        """Create a fresh queue for each test."""
        return MessageQueue()

    @pytest.fixture
    def sample_task_result(self):
        """Create a sample task result."""
        return BackgroundTaskResultMessage(
            task_id="task-12345678",
            session_id="session-123",
            task_description="Test task",
            subagent_name="test_agent",
            status="completed",
            output="Test output content",
            output_file="/tmp/test.log",
            started_at="2026-03-03T10:00:00",
            completed_at="2026-03-03T10:05:00",
        )

    @pytest.mark.asyncio
    async def test_put_user_message(self, queue):
        """Test putting a user message into the queue."""
        await queue.put_user_message("Hello, world!", source="feishu")

        assert not queue.followup_queue_empty()

        items = queue.get_followup_messages()
        assert len(items) == 1
        assert items[0].type == "user"
        assert items[0].content == "Hello, world!"
        assert items[0].metadata["source"] == "feishu"

    @pytest.mark.asyncio
    async def test_put_task_result(self, queue, sample_task_result):
        """Test putting a task result into the queue."""
        await queue.put_task_result(sample_task_result)

        assert not queue.steering_queue_empty()

        items = queue.get_steering_messages()
        assert len(items) == 1
        assert items[0].type == "system_notification"
        assert "task-12345678" in items[0].content
        assert "Test task" in items[0].content
        assert "completed" in items[0].content

    @pytest.mark.asyncio
    async def test_empty_property(self, queue):
        """Test the empty property."""
        assert queue.empty()

        await queue.put_user_message("Test")
        assert not queue.empty()

        queue.get_followup_messages()
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_close_queue(self, queue):
        """Test closing the queue."""
        await queue.put_user_message("Before close")

        queue.close()

        # Should not accept new messages after close
        await queue.put_user_message("After close")
        assert queue.followup_queue_size() == 1  # Only the first message

    @pytest.mark.asyncio
    async def test_multiple_messages_order(self, queue):
        """Test that messages are retrieved in FIFO order."""
        await queue.put_user_message("First")
        await queue.put_user_message("Second")
        await queue.put_user_message("Third")

        items = queue.get_followup_messages()
        assert len(items) == 3

        assert items[0].content == "First"
        assert items[1].content == "Second"
        assert items[2].content == "Third"

    @pytest.mark.asyncio
    async def test_format_task_result_truncation(self, queue):
        """Test that long task output is truncated at 10000 chars."""
        long_output = "A" * 15000  # 超过 10000 字符的阈值

        result = BackgroundTaskResultMessage(
            task_id="task-123",
            task_description="Test",
            status="completed",
            output=long_output,
        )

        await queue.put_task_result(result)
        items = queue.get_steering_messages()
        assert len(items) == 1

        # Should be truncated (max 10000 chars + truncation message)
        assert len(items[0].content) < len(long_output)
        assert "...truncated" in items[0].content

    @pytest.mark.asyncio
    async def test_format_task_result_with_error(self, queue):
        """Test formatting task result with error."""
        result = BackgroundTaskResultMessage(
            task_id="task-error",
            task_description="Failing task",
            status="failed",
            output="",
            error_message="Something went wrong",
        )

        await queue.put_task_result(result)
        items = queue.get_steering_messages()
        assert len(items) == 1

        assert "task-error" in items[0].content
        assert "failed" in items[0].content
        assert "Something went wrong" in items[0].content

    @pytest.mark.asyncio
    async def test_inject_user_message_to_context(self, queue):
        """Test injecting user message to context."""
        mock_context = MagicMock()
        mock_context.append_message = AsyncMock()

        await queue.put_user_message("Hello")
        items = queue.get_followup_messages()
        assert len(items) == 1

        await queue.inject_to_context(items[0], mock_context)

        # Verify context.append_message was called with correct arguments
        mock_context.append_message.assert_called_once()
        call_args = mock_context.append_message.call_args[0][0]
        assert call_args.role == "user"

    @pytest.mark.asyncio
    async def test_inject_task_result_to_context(self, queue, sample_task_result):
        """Test injecting task result to context."""
        mock_context = MagicMock()
        mock_context.append_message = AsyncMock()

        await queue.put_task_result(sample_task_result)
        items = queue.get_steering_messages()
        assert len(items) == 1

        await queue.inject_to_context(items[0], mock_context)

        # Verify context.append_message was called
        mock_context.append_message.assert_called_once()
        call_args = mock_context.append_message.call_args[0][0]
        assert call_args.role == "user"
        # Content should have system part and text part
        assert len(call_args.content) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
