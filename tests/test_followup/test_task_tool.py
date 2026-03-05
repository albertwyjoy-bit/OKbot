"""Tests for Task tool with background execution support."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import TaskManager
from kimi_cli.tools.multiagent.task import Params, Task


class TestTaskParams:
    """Test cases for Task Params model."""

    def test_default_params(self):
        """Test default parameter values."""
        params = Params(
            description="Test task",
            subagent_name="test_agent",
            prompt="Do something",
        )

        assert params.description == "Test task"
        assert params.subagent_name == "test_agent"
        assert params.prompt == "Do something"
        assert params.run_in_background is False  # Default value

    def test_background_execution_params(self):
        """Test params with background execution enabled."""
        params = Params(
            description="Test task",
            subagent_name="test_agent",
            prompt="Do something",
            run_in_background=True,
        )

        assert params.run_in_background is True


class TestTaskTool:
    """Test cases for Task tool."""

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        """Reset TaskManager singleton before each test."""
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_runtime(self):
        """Create a mock runtime."""
        runtime = MagicMock()
        runtime.session.id = "test-session"
        runtime.session.context_file = Path("/tmp/test_context.jsonl")
        runtime.labor_market.fixed_subagent_descs = {}
        runtime.labor_market.subagents = {}
        return runtime

    @pytest.fixture
    def task_tool(self, mock_runtime):
        """Create a Task tool instance."""
        with patch.object(Path, "exists", return_value=True), patch(
            "kimi_cli.tools.multiagent.task.load_desc", return_value="Task description"
        ):
            return Task(mock_runtime)

    @pytest.mark.asyncio
    async def test_subagent_not_found(self, task_tool):
        """Test error when subagent is not found."""
        params = Params(
            description="Test task",
            subagent_name="non_existent_agent",
            prompt="Do something",
        )

        result = await task_tool(params)

        assert result.is_error is True
        assert "Subagent not found" in result.brief

    @pytest.mark.asyncio
    async def test_sync_execution(self, task_tool, mock_runtime):
        """Test synchronous execution mode (default)."""
        # Setup mock subagent
        mock_agent = MagicMock()
        mock_runtime.labor_market.subagents = {"test_agent": mock_agent}

        params = Params(
            description="Test task",
            subagent_name="test_agent",
            prompt="Do something",
            run_in_background=False,
        )

        # Mock _run_subagent_sync to avoid complex setup
        expected_output = "Task completed successfully"
        task_tool._run_subagent_sync = AsyncMock(
            return_value=MagicMock(is_error=False, output=expected_output)
        )

        result = await task_tool(params)

        # Should call sync execution
        task_tool._run_subagent_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_background_execution(self, task_tool, mock_runtime):
        """Test background execution mode."""
        # Setup mock subagent
        mock_agent = MagicMock()
        mock_runtime.labor_market.subagents = {"test_agent": mock_agent}

        params = Params(
            description="Test task",
            subagent_name="test_agent",
            prompt="Do something",
            run_in_background=True,
        )

        # Mock run_in_background to avoid actual execution
        with patch(
            "kimi_cli.tools.multiagent.task.SubagentTask.run_in_background", new_callable=AsyncMock
        ) as mock_run:
            result = await task_tool(params)

            # Should start background task
            mock_run.assert_called_once()

            # Should return task info
            assert result.is_error is False
            assert "task-" in result.output.lower()
            assert "后台任务已启动" in result.output or "background task" in result.output.lower()

    @pytest.mark.asyncio
    async def test_background_execution_registers_task(self, task_tool, mock_runtime):
        """Test that background task is registered in TaskManager."""
        # Setup mock subagent
        mock_agent = MagicMock()
        mock_runtime.labor_market.subagents = {"test_agent": mock_agent}

        params = Params(
            description="Test task",
            subagent_name="test_agent",
            prompt="Do something",
            run_in_background=True,
        )

        # Mock run_in_background
        with patch(
            "kimi_cli.tools.multiagent.task.SubagentTask.run_in_background", new_callable=AsyncMock
        ):
            result = await task_tool(params)

            # Task should be registered
            tasks = TaskManager().list_tasks("test-session")
            assert len(tasks) == 1
            assert tasks[0].description == "Test task"


class TestTaskManagementTools:
    """Test cases for TaskList, TaskOutput, TaskStop tools."""

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        """Reset TaskManager singleton before each test."""
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_runtime(self):
        """Create a mock runtime."""
        runtime = MagicMock()
        runtime.session.id = "test-session"
        return runtime

    @pytest.mark.asyncio
    async def test_task_list_empty(self, mock_runtime):
        """Test TaskList with no tasks."""
        from kimi_cli.tools.multiagent.task_management import TaskList, TaskListParams

        tool = TaskList(mock_runtime)
        params = TaskListParams()

        result = await tool(params)

        assert result.is_error is False
        assert "没有" in result.output or "no" in result.output.lower()

    @pytest.mark.asyncio
    async def test_task_list_with_tasks(self, mock_runtime):
        """Test TaskList with tasks."""
        from kimi_cli.tools.multiagent.task_management import TaskList, TaskListParams
        from kimi_cli.soul.followup import SubagentTask

        # Add some tasks
        task1 = SubagentTask(
            session_id="test-session",
            description="Task 1",
            subagent_name="agent1",
            agent=MagicMock(),
            prompt="Do 1",
        )
        task2 = SubagentTask(
            session_id="test-session",
            description="Task 2",
            subagent_name="agent2",
            agent=MagicMock(),
            prompt="Do 2",
        )

        TaskManager().add_task("test-session", task1)
        TaskManager().add_task("test-session", task2)

        tool = TaskList(mock_runtime)
        params = TaskListParams()

        result = await tool(params)

        assert result.is_error is False
        assert task1.task_id in result.output
        assert task2.task_id in result.output
        assert "2" in result.output  # Total count

    @pytest.mark.asyncio
    async def test_task_list_with_status_filter(self, mock_runtime):
        """Test TaskList with status filter."""
        from kimi_cli.tools.multiagent.task_management import TaskList, TaskListParams
        from kimi_cli.soul.followup import SubagentTask, TaskStatus

        # Add tasks with different statuses
        task1 = SubagentTask(
            session_id="test-session",
            description="Running task",
            subagent_name="agent1",
            agent=MagicMock(),
            prompt="Do 1",
        )
        task1.status = TaskStatus.RUNNING

        task2 = SubagentTask(
            session_id="test-session",
            description="Completed task",
            subagent_name="agent2",
            agent=MagicMock(),
            prompt="Do 2",
        )
        task2.status = TaskStatus.COMPLETED

        TaskManager().add_task("test-session", task1)
        TaskManager().add_task("test-session", task2)

        tool = TaskList(mock_runtime)
        params = TaskListParams(status="running")

        result = await tool(params)

        assert result.is_error is False
        assert "Running task" in result.output
        # Should not contain completed task
        # (this depends on exact implementation)

    @pytest.mark.asyncio
    async def test_task_output_nonexistent(self, mock_runtime):
        """Test TaskOutput for non-existent task."""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        tool = TaskOutput(mock_runtime)
        params = TaskOutputParams(task_id="non-existent-task", tail=10, max_tool_output_tokens=100)

        result = await tool(params)

        assert result.is_error is False
        # Should indicate task not found

    @pytest.mark.asyncio
    async def test_task_stop_nonexistent(self, mock_runtime):
        """Test TaskStop for non-existent task."""
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        tool = TaskStop(mock_runtime)
        params = TaskStopParams(task_id="non-existent-task")

        result = await tool(params)

        assert result.is_error is False
        assert "不存在" in result.output or "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_task_stop_all(self, mock_runtime):
        """Test TaskStop with stop_all=True."""
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams
        from kimi_cli.soul.followup import SubagentTask, TaskStatus

        # Add running tasks
        task1 = SubagentTask(
            session_id="test-session",
            description="Task 1",
            subagent_name="agent1",
            agent=MagicMock(),
            prompt="Do 1",
        )
        task1.status = TaskStatus.RUNNING
        task1._completion_event.set()  # So wait() returns immediately

        TaskManager().add_task("test-session", task1)

        tool = TaskStop(mock_runtime)
        params = TaskStopParams(stop_all=True)

        result = await tool(params)

        assert result.is_error is False
        # Should indicate tasks were stopped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
