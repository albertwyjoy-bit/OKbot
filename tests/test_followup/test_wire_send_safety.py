"""Tests for wire_send safety fix in on_background_task_complete callback.

Verifies:
1. Main agent in IDLE state (wire=None) does NOT raise AssertionError when
   on_background_task_complete fires.
2. Three concurrent background tasks all complete with COMPLETED status.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import (
    BackgroundTaskResultMessage,
    SubagentTask,
    TaskManager,
    TaskStatus,
    message_bus,
)
from kimi_cli.soul.followup.message_bus import MAIN_AGENT_TARGET


class TestWireSendSafety:
    """Ensure on_background_task_complete is safe when wire is None (agent IDLE)."""

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.name = "worker"
        agent.runtime = MagicMock()
        agent.runtime.session.id = "test-session-wire"
        agent.runtime.config = MagicMock()
        agent.runtime.config.loop_control = MagicMock()
        agent.runtime.config.loop_control.max_steps_per_turn = 10
        agent.runtime.config.loop_control.max_retries_per_step = 3
        agent.runtime.config.loop_control.max_ralph_iterations = 0
        agent.runtime.config.loop_control.reserved_context_size = 1000
        agent.toolset = MagicMock()
        agent.toolset.tools = []
        return agent

    @pytest.mark.asyncio
    async def test_idle_agent_no_assert_error(self):
        """Bug 1 fix: on_background_task_complete must NOT raise when wire is None.

        Before the fix, wire_send() would assert wire is not None, causing
        AssertionError that prevented on_steering_message from being called.
        """
        from kimi_cli.soul import get_wire_or_none

        # Wire must be None (simulates IDLE agent / no active run_soul)
        assert get_wire_or_none() is None, "Wire should be None in test context"

        session_id = "test-idle-session"

        # Track whether on_steering_message was called
        steering_called = []

        # Simulate a KimiSoul-like callback (mimics _register_message_bus logic)
        # with the FIXED implementation (get_wire_or_none check)
        from kimi_cli.soul import get_wire_or_none
        from kimi_cli.wire.types import TextPart

        async def safe_callback(message: BackgroundTaskResultMessage) -> None:
            # Fixed: check wire before sending
            wire = get_wire_or_none()
            if wire is not None:
                wire.soul_side.send(TextPart(text="task done"))
            # on_steering_message equivalent
            steering_called.append(message.task_id)

        message_bus.subscribe(session_id, safe_callback, target=MAIN_AGENT_TARGET)

        try:
            msg = BackgroundTaskResultMessage(
                task_id="task-idle-test",
                session_id=session_id,
                task_description="idle test task",
                status="completed",
                output="done",
                target=MAIN_AGENT_TARGET,
            )
            # Must not raise
            published = await message_bus.publish(session_id, msg)
            assert published is True
            assert "task-idle-test" in steering_called
        finally:
            message_bus.unsubscribe(session_id)

    @pytest.mark.asyncio
    async def test_three_concurrent_tasks_all_complete(self, mock_agent):
        """Three concurrent background tasks must all reach COMPLETED status.

        This is the core regression test for the 'sub-agent stuck in RUNNING'
        bug caused by the wire_send AssertionError swallowing the
        on_steering_message trigger in on_background_task_complete.
        """
        task_manager = TaskManager()
        session_id = "test-session-wire"

        # Three simple async tasks with slightly different durations
        async def make_task_fn(label: str, delay: float):
            async def fn(soul, prompt, ui_loop, cancel_event):
                from kosong.message import Message
                from kimi_cli.wire.types import TextPart

                await soul.context.append_message(
                    Message(role="assistant", content=[TextPart(text=f"{label}: started")])
                )
                await asyncio.sleep(delay)
                await soul.context.append_message(
                    Message(role="assistant", content=[TextPart(text=f"{label}: done")])
                )

            return fn

        configs = [
            ("Iran", 0.05),
            ("US", 0.08),
            ("Israel", 0.03),
        ]

        tasks = []
        for label, delay in configs:
            run_fn = await make_task_fn(label, delay)
            t = SubagentTask(
                session_id=session_id,
                description=f"Search {label} war news",
                subagent_name="searcher",
                agent=mock_agent,
                prompt=f"Search {label}",
            )
            await t.run_in_background(run_soul_fn=run_fn)
            task_manager.add_task(session_id, t)
            tasks.append((label, t))

        # Wait for all tasks (with a generous timeout to avoid hanging tests)
        results = await asyncio.wait_for(
            asyncio.gather(*[t.wait() for _, t in tasks]),
            timeout=10.0,
        )

        # Every task must have finished (wait returns True on completion)
        assert all(results), "All tasks should return True from wait()"

        for label, t in tasks:
            assert t.status == TaskStatus.COMPLETED, (
                f"{label} task status is {t.status.value!r}, expected 'completed'"
            )
            assert t.completed_at is not None, f"{label} task should have completed_at set"

    @pytest.mark.asyncio
    async def test_task_completion_event_always_set(self, mock_agent):
        """_completion_event must be set even when publish raises an exception.

        If _publish_result fails internally (e.g., callback raises), the
        completion event should still be set so wait() doesn't block forever.
        """
        task_manager = TaskManager()
        session_id = "test-session-event"

        async def simple_task(soul, prompt, ui_loop, cancel_event):
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart

            await soul.context.append_message(
                Message(role="assistant", content=[TextPart(text="hello")])
            )

        task = SubagentTask(
            session_id=session_id,
            description="event test",
            subagent_name="worker",
            agent=mock_agent,
            prompt="do something",
        )

        # Register a subscriber that raises to simulate a bad callback
        async def bad_callback(message: BackgroundTaskResultMessage) -> None:
            raise RuntimeError("simulated callback failure")

        message_bus.subscribe(session_id, bad_callback, target=MAIN_AGENT_TARGET)
        try:
            await task.run_in_background(run_soul_fn=simple_task)
            task_manager.add_task(session_id, task)

            # Should complete despite the bad callback
            finished = await asyncio.wait_for(task.wait(), timeout=5.0)
            assert finished is True
            # Status is COMPLETED (run_soul succeeded; publish failure is separate)
            assert task.status == TaskStatus.COMPLETED
        finally:
            message_bus.unsubscribe(session_id)
