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

        assert queue.qsize() == 1

        item = queue.get_nowait()
        assert item is not None
        assert item.type == "user"
        assert item.content == "Hello, world!"
        assert item.metadata["source"] == "feishu"

    @pytest.mark.asyncio
    async def test_put_task_result(self, queue, sample_task_result):
        """Test putting a task result into the queue."""
        await queue.put_task_result(sample_task_result)

        assert queue.qsize() == 1

        item = queue.get_nowait()
        assert item is not None
        assert item.type == "system_notification"
        assert "task-12345678" in item.content
        assert "Test task" in item.content
        assert "completed" in item.content

    @pytest.mark.asyncio
    async def test_get_next_blocking(self, queue):
        """Test that get_next blocks when queue is empty."""
        # Start a task that will put a message after a short delay
        async def put_message_after_delay():
            await asyncio.sleep(0.1)
            await queue.put_user_message("Delayed message")

        asyncio.create_task(put_message_after_delay())

        # This should block until the message is put
        item = await queue.get_next()

        assert item is not None
        assert item.content == "Delayed message"

    @pytest.mark.asyncio
    async def test_get_nowait_empty(self, queue):
        """Test get_nowait returns None when queue is empty."""
        item = queue.get_nowait()
        assert item is None

    @pytest.mark.asyncio
    async def test_empty_property(self, queue):
        """Test the empty property."""
        assert queue.empty()

        await queue.put_user_message("Test")
        assert not queue.empty()

        queue.get_nowait()
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_close_queue(self, queue):
        """Test closing the queue."""
        await queue.put_user_message("Before close")

        queue.close()

        # Should not accept new messages after close
        await queue.put_user_message("After close")
        assert queue.qsize() == 1  # Only the first message

    @pytest.mark.asyncio
    async def test_multiple_messages_order(self, queue):
        """Test that messages are retrieved in FIFO order."""
        await queue.put_user_message("First")
        await queue.put_user_message("Second")
        await queue.put_user_message("Third")

        item1 = queue.get_nowait()
        item2 = queue.get_nowait()
        item3 = queue.get_nowait()

        assert item1.content == "First"
        assert item2.content == "Second"
        assert item3.content == "Third"

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
        item = queue.get_nowait()

        # Should be truncated (max 10000 chars + truncation message)
        assert len(item.content) < len(long_output)
        assert "...truncated" in item.content

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
        item = queue.get_nowait()

        assert "task-error" in item.content
        assert "failed" in item.content
        assert "Something went wrong" in item.content

    @pytest.mark.asyncio
    async def test_inject_user_message_to_context(self, queue):
        """Test injecting user message to context."""
        mock_context = MagicMock()
        mock_context.append_message = AsyncMock()

        await queue.put_user_message("Hello")
        item = queue.get_nowait()

        await queue.inject_to_context(item, mock_context)

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
        item = queue.get_nowait()

        await queue.inject_to_context(item, mock_context)

        # Verify context.append_message was called
        mock_context.append_message.assert_called_once()
        call_args = mock_context.append_message.call_args[0][0]
        assert call_args.role == "user"
        # Content should have system part and text part
        assert len(call_args.content) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
