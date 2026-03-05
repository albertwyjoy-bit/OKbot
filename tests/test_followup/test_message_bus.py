"""Tests for AgentMessageBus."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import AgentMessageBus, BackgroundTaskResultMessage


class TestAgentMessageBus:
    """Test cases for AgentMessageBus."""

    @pytest.fixture
    def message_bus(self):
        """Create a fresh message bus for each test."""
        return AgentMessageBus()

    @pytest.fixture
    def sample_message(self):
        """Create a sample background task result message."""
        return BackgroundTaskResultMessage(
            task_id="task-12345678",
            session_id="session-123",
            task_description="Test task",
            subagent_name="test_agent",
            status="completed",
            output="Test output",
            output_file="/tmp/test.log",
        )

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, message_bus, sample_message):
        """Test basic subscribe and publish functionality."""
        callback = AsyncMock()
        session_id = "session-123"

        # Subscribe
        message_bus.subscribe(session_id, callback)
        assert message_bus.is_subscribed(session_id)

        # Publish
        result = await message_bus.publish(session_id, sample_message)

        # Verify
        assert result is True
        callback.assert_called_once_with(sample_message)

    @pytest.mark.asyncio
    async def test_publish_without_subscriber(self, message_bus, sample_message):
        """Test publishing to a session without subscriber."""
        session_id = "session-456"

        # Publish without subscribing
        result = await message_bus.publish(session_id, sample_message)

        # Should return False but not raise
        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe(self, message_bus, sample_message):
        """Test unsubscribe functionality."""
        callback = AsyncMock()
        session_id = "session-789"

        # Subscribe and then unsubscribe
        message_bus.subscribe(session_id, callback)
        message_bus.unsubscribe(session_id)

        assert not message_bus.is_subscribed(session_id)

        # Publish after unsubscribe
        result = await message_bus.publish(session_id, sample_message)
        assert result is False
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_subscribers_different_sessions(self, message_bus, sample_message):
        """Test that different sessions have independent subscriptions."""
        callback1 = AsyncMock()
        callback2 = AsyncMock()

        session1 = "session-1"
        session2 = "session-2"

        message_bus.subscribe(session1, callback1)
        message_bus.subscribe(session2, callback2)

        # Publish to session1
        msg1 = BackgroundTaskResultMessage(**{**sample_message.__dict__, "session_id": session1})
        await message_bus.publish(session1, msg1)

        # Only callback1 should be called
        callback1.assert_called_once()
        callback2.assert_not_called()

        # Publish to session2
        msg2 = BackgroundTaskResultMessage(**{**sample_message.__dict__, "session_id": session2})
        await message_bus.publish(session2, msg2)

        # Both should be called once now
        assert callback1.call_count == 1
        assert callback2.call_count == 1

    @pytest.mark.asyncio
    async def test_callback_exception_handling(self, message_bus, sample_message):
        """Test that callback exceptions are handled gracefully."""
        callback = AsyncMock(side_effect=Exception("Test error"))
        session_id = "session-error"

        message_bus.subscribe(session_id, callback)

        # Should not raise even if callback fails
        result = await message_bus.publish(session_id, sample_message)

        # Should return False when callback fails
        assert result is False


class TestBackgroundTaskResultMessage:
    """Test cases for BackgroundTaskResultMessage."""

    def test_default_values(self):
        """Test that message has correct default values."""
        msg = BackgroundTaskResultMessage()

        assert msg.type == "background_task_result"
        assert msg.task_id == ""
        assert msg.status == ""

    def test_custom_values(self):
        """Test that message accepts custom values."""
        msg = BackgroundTaskResultMessage(
            task_id="task-abc",
            session_id="session-xyz",
            task_description="My task",
            subagent_name="my_agent",
            status="running",
            output="Some output",
            output_file="/path/to/file.log",
            started_at="2026-03-03T10:00:00",
            completed_at="2026-03-03T10:05:00",
            token_usage=1000,
        )

        assert msg.task_id == "task-abc"
        assert msg.session_id == "session-xyz"
        assert msg.task_description == "My task"
        assert msg.subagent_name == "my_agent"
        assert msg.status == "running"
        assert msg.output == "Some output"
        assert msg.output_file == "/path/to/file.log"
        assert msg.started_at == "2026-03-03T10:00:00"
        assert msg.completed_at == "2026-03-03T10:05:00"
        assert msg.token_usage == 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
