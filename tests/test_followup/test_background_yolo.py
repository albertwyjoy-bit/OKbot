"""Test that background sub-agents are forced to use YOLO mode without affecting main agent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.approval import Approval
from kimi_cli.soul.followup import SubagentTask, TaskManager, TaskStatus


class TestBackgroundYoloMode:
    """Verify background tasks force YOLO mode for tool approval WITHOUT affecting main agent."""

    class _DummyTool:
        def __init__(self, approval):
            self.name = "DummyTool"
            self._approval = approval

    class _DummyToolset:
        def __init__(self, tool):
            self._tool_dict = {"dummy": tool}
            self._plan_mode_check = lambda: False
            self._mcp_servers = {}
            self._mcp_loading_task = None

        @property
        def tools(self):
            return [SimpleNamespace(name="DummyTool")]

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_agent_non_yolo(self):
        """Create a mock agent with YOLO mode disabled."""
        agent = MagicMock()
        agent.name = "test_worker"
        agent.runtime = MagicMock()
        agent.runtime.session.id = "test-session-yolo"
        agent.runtime.config = MagicMock()
        agent.runtime.config.loop_control = MagicMock()
        agent.runtime.config.loop_control.max_steps_per_turn = 10
        agent.runtime.config.loop_control.max_retries_per_step = 3
        agent.runtime.config.loop_control.max_ralph_iterations = 0
        agent.runtime.config.loop_control.reserved_context_size = 1000
        agent.runtime.approval = Approval(yolo=False)
        agent.toolset = self._DummyToolset(self._DummyTool(agent.runtime.approval))

        return agent

    @pytest.mark.asyncio
    async def test_background_task_does_not_affect_main_agent_yolo(self, mock_agent_non_yolo):
        """Verify background task enables YOLO ONLY for itself, not affecting main agent."""
        task_manager = TaskManager()
        session_id = "test-session-yolo"

        # Record initial YOLO state of main agent
        main_agent_initial_yolo = mock_agent_non_yolo.runtime.approval.is_yolo()
        assert main_agent_initial_yolo is False, "Main agent should start with YOLO disabled"

        async def task_with_tool_call(soul, prompt, ui_loop, cancel_event):
            """Simulate a task that makes a tool call."""
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart
            from kimi_cli.soul.approval import _background_yolo_mode

            # 后台任务通过 ContextVar 强制 YOLO，不修改主 Agent approval 对象
            assert _background_yolo_mode.get() is True
            assert mock_agent_non_yolo.runtime.approval.is_yolo() is False

            background_tool = soul.agent.toolset._tool_dict["dummy"]
            assert background_tool._approval is mock_agent_non_yolo.runtime.approval

            # Simulate some work
            await asyncio.sleep(0.05)

            # Add a message to context
            await soul.context.append_message(
                Message(role="assistant", content=[TextPart(text="Task completed")])
            )

        task = SubagentTask(
            session_id=session_id,
            description="Test YOLO isolation",
            subagent_name="test_worker",
            agent=mock_agent_non_yolo,
            prompt="Do something",
        )

        await task.run_in_background(run_soul_fn=task_with_tool_call)
        task_manager.add_task(session_id, task)
        await task.wait()

        # Verify task completed
        assert task.status == TaskStatus.COMPLETED

        # Verify main agent approval state remains unchanged
        assert mock_agent_non_yolo.runtime.approval.is_yolo() is False

    @pytest.mark.asyncio
    async def test_background_task_works_with_pre_enabled_yolo(self, mock_agent_non_yolo):
        """Verify background task works when main agent already has YOLO enabled."""
        task_manager = TaskManager()
        session_id = "test-session-yolo"

        # Set YOLO already enabled
        mock_agent_non_yolo.runtime.approval.set_yolo(True)

        async def simple_task(soul, prompt, ui_loop, cancel_event):
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart

            await asyncio.sleep(0.05)
            await soul.context.append_message(
                Message(role="assistant", content=[TextPart(text="Done")])
            )

        task = SubagentTask(
            session_id=session_id,
            description="Test YOLO pre-enabled",
            subagent_name="test_worker",
            agent=mock_agent_non_yolo,
            prompt="Do something",
        )

        await task.run_in_background(run_soul_fn=simple_task)
        task_manager.add_task(session_id, task)
        await task.wait()

        # Verify task completed
        assert task.status == TaskStatus.COMPLETED


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
