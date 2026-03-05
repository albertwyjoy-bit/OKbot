"""Tests for TaskManager and SubagentTask."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import (
    BackgroundTaskResultMessage,
    SubagentTask,
    TaskManager,
    TaskStatus,
)


class TestTaskStatus:
    """Test cases for TaskStatus enum."""

    def test_enum_values(self):
        """Test that enum values are correct."""
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.STOPPED == "stopped"


class TestSubagentTask:
    """Test cases for SubagentTask."""

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        return MagicMock()

    @pytest.fixture
    def sample_task(self, mock_agent):
        """Create a sample subagent task."""
        return SubagentTask(
            session_id="session-123",
            description="Test task description",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test prompt",
        )

    def test_task_creation(self, sample_task, mock_agent):
        """Test task creation with default values."""
        assert sample_task.session_id == "session-123"
        assert sample_task.description == "Test task description"
        assert sample_task.subagent_name == "test_agent"
        assert sample_task.agent == mock_agent
        assert sample_task.prompt == "Test prompt"

        # Task ID should be auto-generated
        assert sample_task.task_id.startswith("task-")
        assert len(sample_task.task_id) == 13  # "task-" + 8 chars

        # Initial status
        assert sample_task.status == TaskStatus.PENDING

        # Log file should be auto-generated
        assert sample_task.task_id in str(sample_task.output_file)

    def test_custom_task_id(self, mock_agent):
        """Test task creation with custom task ID."""
        task = SubagentTask(
            task_id="custom-task-id",
            session_id="session-123",
            description="Test",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )

        assert task.task_id == "custom-task-id"

    def test_output_file_creation(self, mock_agent, tmp_path):
        """Test that output file is created in correct location."""
        with patch.object(Path, "mkdir"):
            task = SubagentTask(
                session_id="session-123",
                description="Test",
                subagent_name="test_agent",
                agent=mock_agent,
                prompt="Test",
            )

            # Log file should be in ~/.kimi/tasks/
            assert ".kimi" in str(task.output_file)
            assert "tasks" in str(task.output_file)
            assert task.task_id in str(task.output_file)

    @pytest.mark.asyncio
    async def test_request_stop_pending_task(self, sample_task):
        """Test requesting stop on a pending task."""
        assert sample_task.status == TaskStatus.PENDING

        sample_task.request_stop()

        # Status should not change for pending task without _task
        assert sample_task.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_is_running(self, sample_task):
        """Test is_running method."""
        # Initially not running
        assert not sample_task.is_running()

        # Set status to running but no _task
        sample_task.status = TaskStatus.RUNNING
        assert not sample_task.is_running()

        # Create a mock task
        sample_task._task = MagicMock()
        sample_task._task.done.return_value = False
        assert sample_task.is_running()

        # Task is done
        sample_task._task.done.return_value = True
        assert not sample_task.is_running()

    @pytest.mark.asyncio
    async def test_is_done(self, sample_task):
        """Test is_done method."""
        # Pending is not done
        assert not sample_task.is_done()

        # Running is not done
        sample_task.status = TaskStatus.RUNNING
        assert not sample_task.is_done()

        # Completed is done
        sample_task.status = TaskStatus.COMPLETED
        assert sample_task.is_done()

        # Failed is done
        sample_task.status = TaskStatus.FAILED
        assert sample_task.is_done()

        # Stopped is done
        sample_task.status = TaskStatus.STOPPED
        assert sample_task.is_done()

    @pytest.mark.asyncio
    async def test_wait_for_completion(self, sample_task):
        """Test waiting for task completion."""
        # Set completion event
        sample_task._completion_event.set()

        # Wait should complete immediately
        await sample_task.wait()

        # Verify completion event is set
        assert sample_task._completion_event.is_set()


class TestTaskManager:
    """Test cases for TaskManager singleton."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset the TaskManager singleton before each test."""
        # Clear any existing instance
        TaskManager._instance = None
        yield
        # Cleanup after test
        TaskManager._instance = None

    @pytest.fixture
    def task_manager(self):
        """Get the TaskManager instance."""
        return TaskManager()

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        return MagicMock()

    @pytest.fixture
    def sample_task(self, mock_agent):
        """Create a sample task."""
        return SubagentTask(
            session_id="session-123",
            description="Test task",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test prompt",
        )

    def test_singleton(self, task_manager):
        """Test that TaskManager is a singleton."""
        another = TaskManager()
        assert task_manager is another

    def test_register_session(self, task_manager):
        """Test session registration."""
        task_manager.register_session("session-1")

        # Should be able to list tasks (empty)
        tasks = task_manager.list_tasks("session-1")
        assert tasks == []

    def test_add_task(self, task_manager, sample_task):
        """Test adding a task."""
        task_manager.add_task("session-1", sample_task)

        tasks = task_manager.list_tasks("session-1")
        assert len(tasks) == 1
        assert tasks[0] == sample_task

    def test_add_task_auto_registers_session(self, task_manager, sample_task):
        """Test that add_task auto-registers the session."""
        task_manager.add_task("session-new", sample_task)

        tasks = task_manager.list_tasks("session-new")
        assert len(tasks) == 1

    def test_get_task(self, task_manager, sample_task):
        """Test getting a specific task."""
        task_manager.add_task("session-1", sample_task)

        found = task_manager.get_task("session-1", sample_task.task_id)
        assert found == sample_task

        # Non-existent task
        not_found = task_manager.get_task("session-1", "non-existent")
        assert not_found is None

        # Non-existent session
        not_found = task_manager.get_task("non-existent", sample_task.task_id)
        assert not_found is None

    def test_remove_task(self, task_manager, sample_task):
        """Test removing a task."""
        task_manager.add_task("session-1", sample_task)

        # Remove existing task
        result = task_manager.remove_task("session-1", sample_task.task_id)
        assert result is True
        assert task_manager.list_tasks("session-1") == []

        # Remove non-existent task
        result = task_manager.remove_task("session-1", "non-existent")
        assert result is False

    def test_list_tasks_with_status_filter(self, task_manager, mock_agent):
        """Test listing tasks with status filter."""
        # Create tasks with different statuses
        task1 = SubagentTask(
            session_id="session-1",
            description="Running task",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )
        task1.status = TaskStatus.RUNNING

        task2 = SubagentTask(
            session_id="session-1",
            description="Completed task",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )
        task2.status = TaskStatus.COMPLETED

        task_manager.add_task("session-1", task1)
        task_manager.add_task("session-1", task2)

        # List all
        all_tasks = task_manager.list_tasks("session-1")
        assert len(all_tasks) == 2

        # List running only
        running_tasks = task_manager.list_tasks("session-1", status="running")
        assert len(running_tasks) == 1
        assert running_tasks[0].status == TaskStatus.RUNNING

        # List completed only
        completed_tasks = task_manager.list_tasks("session-1", status="completed")
        assert len(completed_tasks) == 1
        assert completed_tasks[0].status == TaskStatus.COMPLETED

    def test_get_all_tasks(self, task_manager, sample_task, mock_agent):
        """Test getting all tasks across sessions."""
        # Create task for session-1
        task_manager.add_task("session-1", sample_task)

        # Create task for session-2
        task2 = SubagentTask(
            session_id="session-2",
            description="Task 2",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )
        task_manager.add_task("session-2", task2)

        all_tasks = task_manager.get_all_tasks()
        assert "session-1" in all_tasks
        assert "session-2" in all_tasks
        assert len(all_tasks["session-1"]) == 1
        assert len(all_tasks["session-2"]) == 1

    @pytest.mark.asyncio
    async def test_shutdown_session(self, task_manager, mock_agent):
        """Test shutting down a session."""
        # Create running task
        task = SubagentTask(
            session_id="session-1",
            description="Running task",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )
        task.status = TaskStatus.RUNNING
        task._task = MagicMock()
        task._task.done.return_value = False
        task._completion_event.set()  # Set so wait() returns immediately

        task_manager.add_task("session-1", task)

        # Shutdown session
        count = await task_manager.shutdown_session("session-1", timeout=0.1)

        assert count == 1
        # Task should be removed
        assert task_manager.list_tasks("session-1") == []

    @pytest.mark.asyncio
    async def test_shutdown_empty_session(self, task_manager):
        """Test shutting down a session with no tasks."""
        count = await task_manager.shutdown_session("empty-session")
        assert count == 0

    @pytest.mark.asyncio
    async def test_shutdown_nonexistent_session(self, task_manager):
        """Test shutting down a non-existent session."""
        count = await task_manager.shutdown_session("non-existent")
        assert count == 0

    def test_clear_all(self, task_manager, sample_task, mock_agent):
        """Test clearing all tasks."""
        # Add tasks to multiple sessions
        task_manager.add_task("session-1", sample_task)

        task2 = SubagentTask(
            session_id="session-2",
            description="Task 2",
            subagent_name="test_agent",
            agent=mock_agent,
            prompt="Test",
        )
        task_manager.add_task("session-2", task2)

        # Clear all
        count = task_manager.clear_all()
        assert count == 2

        # All sessions should be empty
        assert task_manager.list_tasks("session-1") == []
        assert task_manager.list_tasks("session-2") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
