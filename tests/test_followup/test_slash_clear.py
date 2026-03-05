"""Tests for /clear slash command with background task cleanup."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import TaskManager, TaskStatus, SubagentTask


class TestSlashClear:
    """Test cases for /clear command with background task cleanup."""

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        """Reset TaskManager singleton before each test."""
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_soul(self):
        """Create a mock KimiSoul."""
        soul = MagicMock()
        soul.runtime.session.id = "test-session"
        soul.status.context_usage = 0.0
        
        # Mock context.clear as async
        soul.context.clear = AsyncMock()
        
        return soul

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_clear_without_tasks(self, mock_soul):
        """Test /clear when no background tasks exist."""
        from kimi_cli.soul.slash import clear
        
        # Patch wire_send to avoid wire context requirement
        with patch('kimi_cli.soul.slash.wire_send'):
            # Run clear command
            await clear(mock_soul, "")
        
        # Context should be cleared
        mock_soul.context.clear.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_clear_with_running_tasks(self, mock_soul, mock_agent):
        """Test /clear stops running background tasks."""
        from kimi_cli.soul.slash import clear
        
        # Create and add a running task
        task = SubagentTask(
            session_id="test-session",
            description="Running task",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )
        task.status = TaskStatus.RUNNING
        task._task = MagicMock()
        task._task.done.return_value = False
        task._completion_event.set()  # So wait() returns immediately
        
        task_manager = TaskManager()
        task_manager.add_task("test-session", task)
        
        # Verify task exists
        assert len(task_manager.list_tasks("test-session")) == 1
        
        # Patch wire_send to avoid wire context requirement
        with patch('kimi_cli.soul.slash.wire_send'):
            # Run clear command
            await clear(mock_soul, "")
        
        # Task should be removed (stopped)
        assert len(task_manager.list_tasks("test-session")) == 0
        
        # Context should be cleared
        mock_soul.context.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_with_multiple_tasks(self, mock_soul, mock_agent):
        """Test /clear stops multiple background tasks."""
        from kimi_cli.soul.slash import clear
        
        task_manager = TaskManager()
        
        # Create multiple tasks
        for i in range(3):
            task = SubagentTask(
                session_id="test-session",
                description=f"Task {i}",
                subagent_name="test_agent",
                agent=mock_agent,
                prompt="Test",
            )
            task.status = TaskStatus.RUNNING if i < 2 else TaskStatus.PENDING
            task._task = MagicMock()
            task._task.done.return_value = False
            task._completion_event.set()
            task_manager.add_task("test-session", task)
        
        # Verify tasks exist
        assert len(task_manager.list_tasks("test-session")) == 3
        
        # Patch wire_send to avoid wire context requirement
        with patch('kimi_cli.soul.slash.wire_send'):
            # Run clear command
            await clear(mock_soul, "")
        
        # All tasks should be removed
        assert len(task_manager.list_tasks("test-session")) == 0
        
        # Context should be cleared
        mock_soul.context.clear.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
