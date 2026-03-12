"""后台任务系统集成测试 - 真实执行，最小 Mock.

与现有 mock 测试的区别：
- 不 mock SubagentTask.run_in_background()，直接调用真实路径
- 通过 run_soul_fn 注入规避 LLM API 依赖，但 asyncio 任务本身是真实的
- 验证真实文件 I/O（日志写入、TaskOutput 解析）
- 验证真实 MessageBus 发布/订阅时序
- 验证真实 asyncio 取消传播（request_stop → CancelledError → STOPPED）
- 验证 ContextVar 后台 YOLO 模式隔离

覆盖现有 mock 测试未覆盖的场景：
1. 任务异常 → FAILED + MessageBus 发布
2. request_stop() 真正取消 asyncio 任务 → STOPPED
3. TaskOutput 解析真实写入的日志文件
4. TaskStop 工具真实取消运行中任务
5. 多任务并发：部分成功/部分失败，各自独立发布
6. 超长输出 >10000 字符截断
7. ContextVar 后台 YOLO 不泄漏到主 Task
8. 任务完成后从 TaskManager 自动移除
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kosong.message import Message
from kimi_cli.soul.approval import _background_yolo_mode
from kimi_cli.soul.followup import (
    MAIN_AGENT_TARGET,
    BackgroundTaskResultMessage,
    SubagentTask,
    TaskManager,
    TaskStatus,
    message_bus,
)
from kimi_cli.wire.types import TextPart


# ──────────────────────────────────────────────────────────
# 共用工具函数
# ──────────────────────────────────────────────────────────


def _make_task(
    tmp_path: Path,
    *,
    session_id: str = "test-session",
    description: str = "test task",
    subagent_name: str = "coder",
    name: str = "task",
) -> SubagentTask:
    """创建一个 SubagentTask，日志写到 tmp_path 避免污染 ~/.kimi/tasks/."""
    return SubagentTask(
        session_id=session_id,
        description=description,
        subagent_name=subagent_name,
        agent=MagicMock(),
        prompt="do something",
        output_file=tmp_path / f"{name}.log",
    )


async def _write_messages(soul, messages: list[str]) -> None:
    """辅助：向 soul.context 写入多条 assistant 消息."""
    for text in messages:
        await soul.context.append_message(
            Message(role="assistant", content=[TextPart(text=text)])
        )


async def _wait_message(
    event: asyncio.Event, timeout: float = 3.0, label: str = ""
) -> None:
    """等待消息总线事件，带超时保护."""
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"MessageBus event timed out after {timeout}s{' (' + label + ')' if label else ''}")


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_task_manager():
    TaskManager._instance = None
    yield
    TaskManager._instance = None


@pytest.fixture(autouse=True)
def reset_message_bus():
    message_bus._subscribers.clear()
    yield
    message_bus._subscribers.clear()


# ══════════════════════════════════════════════════════════
# Group 1 ── 真实任务生命周期
# ══════════════════════════════════════════════════════════


class TestRealTaskLifecycle:
    """SubagentTask 真实 asyncio 执行，不 mock run_in_background。"""

    async def test_successful_task_status_transition(self, tmp_path: Path):
        """任务正常完成：PENDING → RUNNING → COMPLETED，时间戳均有值。"""
        task = _make_task(tmp_path, name="t1")
        assert task.status == TaskStatus.PENDING

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["step1", "final answer"])

        await task.run_in_background(run_soul_fn=run_fn)

        # 任务刚 create，状态应已切换为 RUNNING
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None

        await task.wait(timeout=5.0)

        assert task.status == TaskStatus.COMPLETED
        assert task.completed_at is not None
        assert task.completed_at >= task.started_at

    async def test_task_failure_sets_failed_status(self, tmp_path: Path):
        """run_soul_fn 抛出异常 → FAILED，completed_at 有值。"""
        task = _make_task(tmp_path, name="t1")

        async def failing_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["starting..."])
            raise ValueError("Deliberate failure for testing")

        await task.run_in_background(run_soul_fn=failing_fn)
        await task.wait(timeout=5.0)

        assert task.status == TaskStatus.FAILED
        assert task.completed_at is not None

    async def test_task_cancelled_by_request_stop(self, tmp_path: Path):
        """request_stop() 触发 asyncio 取消 → STOPPED。"""
        started = asyncio.Event()

        async def long_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["starting long work"])
            started.set()
            # 模拟耗时操作，await 点让 cancel() 能注入 CancelledError
            for _ in range(200):
                await asyncio.sleep(0.02)

        task = _make_task(tmp_path, name="t1")
        await task.run_in_background(run_soul_fn=long_fn)

        # 等任务真正开始（已执行到 started.set()）
        await asyncio.wait_for(started.wait(), timeout=3.0)
        assert task.status == TaskStatus.RUNNING

        task.request_stop()
        completed = await task.wait(timeout=5.0)

        assert completed is True
        assert task.status == TaskStatus.STOPPED
        assert task.completed_at is not None

    async def test_task_writes_last_assistant_message_as_output(self, tmp_path: Path):
        """任务完成后，最后一条 assistant 消息作为 output 发布。"""
        received: list[BackgroundTaskResultMessage] = []
        delivered = asyncio.Event()

        async def cb(msg: BackgroundTaskResultMessage):
            received.append(msg)
            delivered.set()

        session_id = "sess-output"
        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        task = _make_task(tmp_path, session_id=session_id, name="t1")

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["intermediate step", "FINAL: the real answer"])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)
        await _wait_message(delivered, label="output delivery")

        assert len(received) == 1
        assert "FINAL: the real answer" in received[0].output

    async def test_task_wait_returns_false_on_timeout(self, tmp_path: Path):
        """wait(timeout=...) 在超时时返回 False 而不是抛出异常。"""
        task = _make_task(tmp_path, name="t1")

        async def stuck_fn(soul, prompt, ui_loop, cancel_event):
            await asyncio.sleep(60)  # 远超测试超时

        await task.run_in_background(run_soul_fn=stuck_fn)
        result = await task.wait(timeout=0.1)

        assert result is False
        # 清理：强制取消以免影响后续测试
        task.request_stop()
        await asyncio.sleep(0.2)


# ══════════════════════════════════════════════════════════
# Group 2 ── MessageBus 真实发布/订阅
# ══════════════════════════════════════════════════════════


class TestRealMessageBusDelivery:
    """任务完成后通过 MessageBus 真实发布结果。"""

    async def test_completed_task_publishes_to_subscriber(self, tmp_path: Path):
        """完成的任务发布 status=completed 到总线。"""
        received: list[BackgroundTaskResultMessage] = []
        delivered = asyncio.Event()

        async def cb(msg):
            received.append(msg)
            delivered.set()

        session_id = "sess-completed"
        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        task = _make_task(tmp_path, session_id=session_id, name="t1")

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["done"])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)
        await _wait_message(delivered, label="completed delivery")

        assert len(received) == 1
        assert received[0].status == "completed"
        assert received[0].session_id == session_id
        assert received[0].task_id == task.task_id
        assert received[0].task_description == task.description

    async def test_failed_task_publishes_failed_status(self, tmp_path: Path):
        """失败的任务发布 status=failed 到总线。"""
        received: list[BackgroundTaskResultMessage] = []
        delivered = asyncio.Event()

        async def cb(msg):
            received.append(msg)
            delivered.set()

        session_id = "sess-failed"
        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        task = _make_task(tmp_path, session_id=session_id, name="t1")

        async def failing_fn(soul, prompt, ui_loop, cancel_event):
            raise RuntimeError("boom")

        await task.run_in_background(run_soul_fn=failing_fn)
        await task.wait(timeout=5.0)
        await _wait_message(delivered, label="failed delivery")

        assert len(received) == 1
        assert received[0].status == "failed"
        assert "boom" in received[0].output

    async def test_stopped_task_publishes_stopped_status(self, tmp_path: Path):
        """被取消的任务发布 status=stopped 到总线。"""
        received: list[BackgroundTaskResultMessage] = []
        delivered = asyncio.Event()
        started = asyncio.Event()

        async def cb(msg):
            received.append(msg)
            delivered.set()

        session_id = "sess-stopped"
        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        task = _make_task(tmp_path, session_id=session_id, name="t1")

        async def long_fn(soul, prompt, ui_loop, cancel_event):
            started.set()
            for _ in range(200):
                await asyncio.sleep(0.02)

        await task.run_in_background(run_soul_fn=long_fn)
        await asyncio.wait_for(started.wait(), timeout=3.0)
        task.request_stop()
        await task.wait(timeout=5.0)
        await _wait_message(delivered, label="stopped delivery")

        assert len(received) == 1
        assert received[0].status == "stopped"

    async def test_long_output_truncated_at_10000_chars(self, tmp_path: Path):
        """超过 10000 字符的输出在发布消息中被截断。"""
        received: list[BackgroundTaskResultMessage] = []
        delivered = asyncio.Event()

        async def cb(msg):
            received.append(msg)
            delivered.set()

        session_id = "sess-truncate"
        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        task = _make_task(tmp_path, session_id=session_id, name="t1")
        huge_text = "x" * 20_000  # 远超 10000

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, [huge_text])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)
        await _wait_message(delivered, label="truncation delivery")

        assert len(received) == 1
        assert len(received[0].output) <= 10_100  # 10000 + "...truncated" 允许少量余量
        assert "...truncated" in received[0].output

    async def test_no_subscriber_does_not_crash(self, tmp_path: Path):
        """没有订阅者时任务完成不抛异常。"""
        task = _make_task(tmp_path, session_id="orphan-session", name="t1")

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["done"])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)

        assert task.status == TaskStatus.COMPLETED  # 不崩溃，状态正常

    async def test_message_contains_output_file_path(self, tmp_path: Path):
        """发布的消息包含日志文件路径，便于 TaskOutput 查询。"""
        received: list[BackgroundTaskResultMessage] = []
        delivered = asyncio.Event()

        async def cb(msg):
            received.append(msg)
            delivered.set()

        session_id = "sess-filepath"
        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        task = _make_task(tmp_path, session_id=session_id, name="t1")

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["result"])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)
        await _wait_message(delivered, label="filepath delivery")

        assert received[0].output_file is not None
        assert str(task.output_file) == received[0].output_file


# ══════════════════════════════════════════════════════════
# Group 3 ── TaskManager 真实状态集成
# ══════════════════════════════════════════════════════════


class TestRealTaskManagerIntegration:
    """任务完成后 TaskManager 的真实状态变更。"""

    async def test_task_removed_from_manager_after_completion(self, tmp_path: Path):
        """任务正常完成后从 TaskManager 自动移除。"""
        session_id = "sess-remove"
        task = _make_task(tmp_path, session_id=session_id, name="t1")
        TaskManager().add_task(session_id, task)

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["done"])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)

        # 任务完成后 _publish_result 中会调用 remove_task
        # 给一次 event loop 机会执行 remove
        await asyncio.sleep(0.1)

        remaining = TaskManager().list_tasks(session_id)
        assert task not in remaining

    async def test_task_removed_after_failure(self, tmp_path: Path):
        """任务失败后也从 TaskManager 自动移除。"""
        session_id = "sess-remove-fail"
        task = _make_task(tmp_path, session_id=session_id, name="t1")
        TaskManager().add_task(session_id, task)

        async def failing_fn(soul, prompt, ui_loop, cancel_event):
            raise RuntimeError("fail")

        await task.run_in_background(run_soul_fn=failing_fn)
        await task.wait(timeout=5.0)
        await asyncio.sleep(0.1)

        remaining = TaskManager().list_tasks(session_id)
        assert task not in remaining

    async def test_concurrent_tasks_all_complete_independently(self, tmp_path: Path):
        """5 个并发任务各自独立完成，不互相干扰。"""
        session_id = "sess-concurrent"
        N = 5
        tasks = []

        for i in range(N):
            t = _make_task(
                tmp_path, session_id=session_id, description=f"task-{i}", name=f"t{i}"
            )
            TaskManager().add_task(session_id, t)
            tasks.append(t)

        delays = [0.05, 0.12, 0.08, 0.03, 0.10]

        async def make_fn(delay: float, idx: int):
            async def _fn(soul, prompt, ui_loop, cancel_event):
                await asyncio.sleep(delay)
                await _write_messages(soul, [f"result-{idx}"])

            return _fn

        # 并发启动所有任务
        for i, t in enumerate(tasks):
            fn = await make_fn(delays[i], i)
            await t.run_in_background(run_soul_fn=fn)

        # 等待所有完成
        results = await asyncio.gather(*[t.wait(timeout=5.0) for t in tasks])

        assert all(results), "部分任务超时未完成"
        assert all(t.status == TaskStatus.COMPLETED for t in tasks)

    async def test_concurrent_tasks_some_fail_independently(self, tmp_path: Path):
        """并发任务：部分失败，部分成功，状态独立。"""
        session_id = "sess-mixed"
        tasks = []
        expected_statuses = [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.COMPLETED,
        ]

        for i in range(3):
            t = _make_task(tmp_path, session_id=session_id, name=f"t{i}")
            TaskManager().add_task(session_id, t)
            tasks.append(t)

        async def ok_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["ok"])

        async def bad_fn(soul, prompt, ui_loop, cancel_event):
            raise ValueError("planned failure")

        fns = [ok_fn, bad_fn, ok_fn]
        for t, fn in zip(tasks, fns):
            await t.run_in_background(run_soul_fn=fn)

        await asyncio.gather(*[t.wait(timeout=5.0) for t in tasks])

        for t, expected in zip(tasks, expected_statuses):
            assert t.status == expected, f"{t.description}: expected {expected}, got {t.status}"

    async def test_each_concurrent_task_publishes_independently(self, tmp_path: Path):
        """并发任务各自独立发布结果到 MessageBus，消息数量等于任务数。"""
        session_id = "sess-publish-count"
        received: list[BackgroundTaskResultMessage] = []
        N = 4
        # 用 Counter 来判断是否收到 N 条消息
        all_delivered = asyncio.Event()

        async def cb(msg):
            received.append(msg)
            if len(received) >= N:
                all_delivered.set()

        message_bus.subscribe(session_id, cb, target=MAIN_AGENT_TARGET)

        tasks = []
        for i in range(N):
            t = _make_task(tmp_path, session_id=session_id, name=f"t{i}")
            tasks.append(t)

        async def make_fn(i: int):
            async def _fn(soul, prompt, ui_loop, cancel_event):
                await asyncio.sleep(0.01 * i)
                await _write_messages(soul, [f"result-{i}"])

            return _fn

        for i, t in enumerate(tasks):
            fn = await make_fn(i)
            await t.run_in_background(run_soul_fn=fn)

        await asyncio.gather(*[t.wait(timeout=5.0) for t in tasks])
        await _wait_message(all_delivered, timeout=5.0, label=f"all {N} deliveries")

        assert len(received) == N
        task_ids = {m.task_id for m in received}
        assert task_ids == {t.task_id for t in tasks}


# ══════════════════════════════════════════════════════════
# Group 4 ── TaskOutput 解析真实日志文件
# ══════════════════════════════════════════════════════════


class TestRealTaskOutputParsing:
    """TaskOutput 工具读取由真实任务写入的日志文件。"""

    def _write_jsonl_log(self, log_file: Path, messages: list[dict]) -> None:
        """辅助：向日志文件写入 JSONL 格式的 Context 消息，模拟真实任务执行。"""
        import json

        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def _make_completed_task_in_manager(
        self,
        tmp_path: Path,
        session_id: str,
        log_messages: list[dict],
        name: str = "t1",
    ) -> SubagentTask:
        """创建已完成状态的任务，写入日志文件并注入 TaskManager。

        使用手动预填充日志文件 + 注入 TaskManager 的方式，
        直接测试 TaskOutput 的文件解析逻辑，
        避免依赖 _read_output_file 的硬编码路径（该路径在任务移除后使用）。
        """
        task = _make_task(tmp_path, session_id=session_id, name=name)
        task.status = TaskStatus.COMPLETED
        self._write_jsonl_log(task.output_file, log_messages)
        TaskManager().add_task(session_id, task)
        return task

    async def test_task_output_reads_real_log_after_completion(self, tmp_path: Path):
        """TaskOutput 能从已完成任务的日志文件中读取到写入的内容。"""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        session_id = "sess-output-read"
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "phase 1 done"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "phase 2 done"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "FINAL: all complete"}]},
        ]
        task = self._make_completed_task_in_manager(tmp_path, session_id, messages)

        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        tool = TaskOutput(mock_runtime)
        result = await tool(TaskOutputParams(task_id=task.task_id))

        assert result.is_error is False
        assert "FINAL: all complete" in result.output
        assert "phase 1 done" in result.output

    async def test_task_output_reads_archived_completed_task(self, tmp_path: Path):
        """任务完成后从活动列表移除，TaskOutput 仍应能读取真实日志路径."""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        session_id = "sess-output-archived"
        task = _make_task(tmp_path, session_id=session_id, name="archived")
        task.status = TaskStatus.COMPLETED
        self._write_jsonl_log(
            task.output_file,
            [{"role": "assistant", "content": [{"type": "text", "text": "FINAL: archived output"}]}],
        )
        TaskManager().add_task(session_id, task)
        TaskManager().remove_task(session_id, task.task_id)

        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        tool = TaskOutput(mock_runtime)
        result = await tool(TaskOutputParams(task_id=task.task_id))

        assert result.is_error is False
        assert "FINAL: archived output" in result.output

    async def test_task_output_tail_limits_returned_messages(self, tmp_path: Path):
        """tail=2 时只返回最后 2 条消息。"""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        session_id = "sess-tail"
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": f"msg{i}"}]}
            for i in range(1, 6)  # msg1 ~ msg5
        ]
        task = self._make_completed_task_in_manager(tmp_path, session_id, messages)

        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        tool = TaskOutput(mock_runtime)
        result = await tool(TaskOutputParams(task_id=task.task_id, tail=2))

        assert result.is_error is False
        # 只有最后 2 条
        assert "msg5" in result.output
        assert "msg4" in result.output
        # 早期消息不在结果中
        assert "msg1" not in result.output
        assert "msg2" not in result.output
        assert "msg3" not in result.output

    async def test_task_output_truncates_long_tool_messages(self, tmp_path: Path):
        """tool 角色超长消息被截断，assistant 消息保留完整。"""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        session_id = "sess-truncate-tool"
        long_tool_output = "T" * 500  # 超过默认 max_tool_output_tokens=200
        long_assistant_msg = "A" * 500  # assistant 不截断

        messages = [
            {
                "role": "tool",
                "content": [{"type": "text", "text": long_tool_output}],
                "tool_call_id": "call_1",
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": long_assistant_msg}],
            },
        ]
        task = self._make_completed_task_in_manager(tmp_path, session_id, messages)

        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        tool = TaskOutput(mock_runtime)
        result = await tool(TaskOutputParams(task_id=task.task_id, max_tool_output_tokens=200))

        assert result.is_error is False
        # assistant 消息完整（500 个 A 全部保留）
        assert "A" * 500 in result.output
        # tool 消息被截断（不含完整的 500 个 T）
        assert "T" * 500 not in result.output
        assert "..." in result.output

    async def test_task_output_unknown_task_returns_not_found(self, tmp_path: Path):
        """查询不存在的 task_id 返回提示而非报错。"""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        mock_runtime = MagicMock()
        mock_runtime.session.id = "sess-unknown"

        tool = TaskOutput(mock_runtime)
        result = await tool(TaskOutputParams(task_id="nonexistent-task-xyz"))

        assert result.is_error is False
        # 提示不存在或已过期
        assert "nonexistent-task-xyz" in result.output or "不存在" in result.output


# ══════════════════════════════════════════════════════════
# Group 5 ── TaskStop 工具真实取消
# ══════════════════════════════════════════════════════════


class TestRealTaskStopTool:
    """TaskStop 工具真实取消正在运行的 asyncio 任务。"""

    async def test_stop_actually_cancels_running_task(self, tmp_path: Path):
        """TaskStop 工具调用后，真实 asyncio 任务被取消 → STOPPED。"""
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        session_id = "sess-stop-tool"
        task = _make_task(tmp_path, session_id=session_id, name="t1")
        TaskManager().add_task(session_id, task)
        started = asyncio.Event()

        async def long_fn(soul, prompt, ui_loop, cancel_event):
            started.set()
            for _ in range(200):
                await asyncio.sleep(0.02)

        await task.run_in_background(run_soul_fn=long_fn)
        await asyncio.wait_for(started.wait(), timeout=3.0)
        assert task.status == TaskStatus.RUNNING

        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        stop_tool = TaskStop(mock_runtime)
        result = await stop_tool(TaskStopParams(task_id=task.task_id))

        assert result.is_error is False
        assert "已停止" in result.output or "stopped" in result.output.lower()
        assert task.status == TaskStatus.STOPPED

    async def test_stop_all_cancels_multiple_running_tasks(self, tmp_path: Path):
        """stop_all=True 同时取消所有运行中任务。"""
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        session_id = "sess-stop-all"
        N = 3
        tasks = []
        started_events = [asyncio.Event() for _ in range(N)]

        for i in range(N):
            t = _make_task(tmp_path, session_id=session_id, name=f"t{i}")
            TaskManager().add_task(session_id, t)
            tasks.append(t)

        for i, t in enumerate(tasks):
            ev = started_events[i]

            async def make_fn(e):
                async def _fn(soul, prompt, ui_loop, cancel_event):
                    e.set()
                    for _ in range(200):
                        await asyncio.sleep(0.02)

                return _fn

            fn = await make_fn(ev)
            await t.run_in_background(run_soul_fn=fn)

        # 等所有任务启动
        await asyncio.gather(*[asyncio.wait_for(e.wait(), timeout=3.0) for e in started_events])
        assert all(t.status == TaskStatus.RUNNING for t in tasks)

        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        stop_tool = TaskStop(mock_runtime)
        result = await stop_tool(TaskStopParams(stop_all=True))

        assert result.is_error is False
        assert f"{N}" in result.output  # 停止了 N 个任务

        # 等待所有任务真正完成
        await asyncio.gather(*[t.wait(timeout=5.0) for t in tasks])
        assert all(t.status == TaskStatus.STOPPED for t in tasks)

    async def test_stop_nonexistent_task_returns_not_found(self, tmp_path: Path):
        """TaskStop 查询不存在的任务返回提示而非报错。"""
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        mock_runtime = MagicMock()
        mock_runtime.session.id = "sess-noop"

        stop_tool = TaskStop(mock_runtime)
        result = await stop_tool(TaskStopParams(task_id="ghost-task-id"))

        assert result.is_error is False
        assert "不存在" in result.output or "not found" in result.output.lower() or "已结束" in result.output

    async def test_stop_completed_task_is_noop(self, tmp_path: Path):
        """对已完成任务调用 TaskStop 无效，返回友好提示。"""
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        session_id = "sess-stop-done"
        task = _make_task(tmp_path, session_id=session_id, name="t1")
        TaskManager().add_task(session_id, task)

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await _write_messages(soul, ["done"])

        await task.run_in_background(run_soul_fn=run_fn)
        await task.wait(timeout=5.0)
        await asyncio.sleep(0.1)  # 等 remove_task 执行

        # 任务已完成并被移除，stop 应返回"不存在或已结束"
        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id

        stop_tool = TaskStop(mock_runtime)
        result = await stop_tool(TaskStopParams(task_id=task.task_id))

        assert result.is_error is False
        # 不应崩溃


# ══════════════════════════════════════════════════════════
# Group 6 ── ContextVar YOLO 模式隔离
# ══════════════════════════════════════════════════════════


class TestBackgroundYoloContextVarIsolation:
    """_background_yolo_mode ContextVar 在 asyncio.create_task 间的隔离。"""

    async def test_background_task_has_yolo_set(self, tmp_path: Path):
        """后台任务内 _background_yolo_mode 被设为 True。"""
        yolo_in_task: list[bool] = []
        done = asyncio.Event()

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            # 此时应处于 run_in_background 内部的 ContextVar 设置之后
            yolo_in_task.append(_background_yolo_mode.get())
            done.set()

        task = _make_task(tmp_path, name="t1")
        await task.run_in_background(run_soul_fn=run_fn)
        await asyncio.wait_for(done.wait(), timeout=3.0)
        await task.wait(timeout=3.0)

        assert len(yolo_in_task) == 1
        assert yolo_in_task[0] is True, "后台任务内应看到 YOLO=True"

    async def test_yolo_does_not_leak_to_main_context(self, tmp_path: Path):
        """后台任务的 YOLO 设置不泄漏到主 asyncio Task（当前上下文）。"""
        # 主 Task 上下文中 YOLO 为 False（默认）
        assert _background_yolo_mode.get() is False

        task = _make_task(tmp_path, name="t1")
        done = asyncio.Event()

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            done.set()

        await task.run_in_background(run_soul_fn=run_fn)
        await asyncio.wait_for(done.wait(), timeout=3.0)
        await task.wait(timeout=3.0)

        # 任务完成后，主 Task 的 ContextVar 不受影响
        assert _background_yolo_mode.get() is False, "YOLO 不应泄漏到主 Task"

    async def test_two_concurrent_tasks_yolo_isolated(self, tmp_path: Path):
        """两个并发后台任务各自独立持有 YOLO=True，互不干扰。"""
        results: dict[str, bool] = {}
        events = [asyncio.Event(), asyncio.Event()]

        async def make_fn(name: str, ev: asyncio.Event):
            async def _fn(soul, prompt, ui_loop, cancel_event):
                results[name] = _background_yolo_mode.get()
                ev.set()
                await asyncio.sleep(0.05)  # 保持运行让两任务并发

            return _fn

        task1 = _make_task(tmp_path, name="t1")
        task2 = _make_task(tmp_path, name="t2")

        await task1.run_in_background(run_soul_fn=await make_fn("task1", events[0]))
        await task2.run_in_background(run_soul_fn=await make_fn("task2", events[1]))

        await asyncio.gather(
            asyncio.wait_for(events[0].wait(), timeout=3.0),
            asyncio.wait_for(events[1].wait(), timeout=3.0),
        )
        await asyncio.gather(task1.wait(timeout=3.0), task2.wait(timeout=3.0))

        assert results.get("task1") is True
        assert results.get("task2") is True
        # 主 Task 不受影响
        assert _background_yolo_mode.get() is False

    async def test_yolo_reset_after_task_completes(self, tmp_path: Path):
        """任务完成后，ContextVar token 被重置（不泄漏）。

        具体：asyncio.create_task() 复制当前 context 的快照，
        所以任务完成不影响外部 context。验证任务完成前后外部均为 False。
        """
        # 先验证外部默认值
        assert _background_yolo_mode.get() is False

        task = _make_task(tmp_path, name="t1")

        async def run_fn(soul, prompt, ui_loop, cancel_event):
            await asyncio.sleep(0.05)

        await task.run_in_background(run_soul_fn=run_fn)

        # 任务运行中，外部依然 False
        assert _background_yolo_mode.get() is False

        await task.wait(timeout=3.0)

        # 任务完成后，外部依然 False
        assert _background_yolo_mode.get() is False


# ══════════════════════════════════════════════════════════
# Group 7 ── Task 工具 run_in_background 参数真实路径
# ══════════════════════════════════════════════════════════


class TestTaskToolBackgroundParam:
    """Task 工具 run_in_background=True 真实执行路径（不 mock _run_in_background）。"""

    async def test_background_task_registered_in_manager(self, tmp_path: Path):
        """Task 工具调用后，SubagentTask 被加入 TaskManager。"""
        from kimi_cli.tools.multiagent.task import Params, Task

        session_id = "sess-task-tool"
        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id
        mock_runtime.session.context_file = tmp_path / "ctx.jsonl"
        mock_agent = MagicMock()
        mock_runtime.labor_market.subagents = {"coder": mock_agent}
        mock_runtime.labor_market.fixed_subagent_descs = {"coder": "A coder"}

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "kimi_cli.tools.multiagent.task.load_desc",
                lambda *a, **kw: "desc",
            )
            tool = Task(mock_runtime)

        params = Params(
            description="bg test",
            subagent_name="coder",
            prompt="do something",
            run_in_background=True,
        )

        # Mock run_in_background on SubagentTask to avoid real execution in this test
        # （只测试"注册到 TaskManager"这一步，不需要真实执行）
        from unittest.mock import AsyncMock, patch

        with patch(
            "kimi_cli.tools.multiagent.task.SubagentTask.run_in_background",
            new_callable=AsyncMock,
        ):
            result = await tool(params)

        assert result.is_error is False
        tasks = TaskManager().list_tasks(session_id)
        assert len(tasks) == 1
        assert tasks[0].description == "bg test"

    async def test_sync_task_truncates_long_output(self, tmp_path: Path):
        """同步执行（run_in_background=False）超长输出被截断到 10000 字符。"""
        from kimi_cli.tools.multiagent.task import Params, Task

        session_id = "sess-sync-trunc"
        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id
        mock_runtime.session.context_file = tmp_path / "ctx.jsonl"
        mock_agent = MagicMock()
        mock_runtime.labor_market.subagents = {"coder": mock_agent}
        mock_runtime.labor_market.fixed_subagent_descs = {"coder": "A coder"}

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "kimi_cli.tools.multiagent.task.load_desc",
                lambda *a, **kw: "desc",
            )
            tool = Task(mock_runtime)

        params = Params(
            description="sync truncation test",
            subagent_name="coder",
            prompt="output a lot",
            run_in_background=False,
        )

        huge_output = "Z" * 20_000
        from unittest.mock import AsyncMock, MagicMock as MM

        mock_result = MM()
        mock_result.is_error = False
        mock_result.output = huge_output

        # _run_subagent_sync 内部调用 run_soul，这里直接 mock 其输出
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                tool,
                "_run_subagent_sync",
                AsyncMock(return_value=mock_result),
            )
            result = await tool(params)

        # 注意：同步模式下截断在 _run_subagent_sync 内部完成，
        # mock 绕过了截断逻辑。此测试验证 _run_subagent_sync 的返回值直接透传。
        # 真正的截断逻辑测试见下方。
        assert result.is_error is False

    async def test_sync_subagent_output_truncated_in_real_run(self, tmp_path: Path):
        """同步子 Agent 输出超过 10000 字符时被截断（验证真实截断逻辑）。"""
        from kimi_cli.tools.multiagent.task import Task

        session_id = "sess-sync-real-trunc"
        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id
        mock_runtime.session.context_file = tmp_path / "ctx.jsonl"

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "kimi_cli.tools.multiagent.task.load_desc",
                lambda *a, **kw: "desc",
            )
            tool = Task(mock_runtime)

        # 直接测试 _run_subagent_sync 的截断逻辑
        # 构造一个 context 最后一条消息是超长文本
        huge_text = "W" * 20_000
        from kimi_cli.soul.context import Context
        from kimi_cli.soul.kimisoul import KimiSoul
        from unittest.mock import AsyncMock, patch

        mock_agent = MagicMock()
        ctx = Context(file_backend=tmp_path / "sub_ctx.jsonl")
        await ctx.append_message(
            Message(role="assistant", content=[TextPart(text=huge_text)])
        )

        # 用 patch 让 run_soul 直接返回（不真实执行），然后验证截断
        with patch("kimi_cli.tools.multiagent.task.run_soul", new_callable=AsyncMock), patch(
            "kimi_cli.tools.multiagent.task.Context", return_value=ctx
        ), patch(
            "kimi_cli.tools.multiagent.task.KimiSoul", return_value=MagicMock()
        ), patch(
            "kimi_cli.tools.multiagent.task.get_wire_or_none", return_value=MagicMock()
        ), patch(
            "kimi_cli.tools.multiagent.task.get_current_tool_call_or_none",
            return_value=MagicMock(),
        ):
            result = await tool._run_subagent_sync(mock_agent, "prompt")

        assert result.is_error is False
        assert len(result.output) <= 10_100
        assert "...truncated" in result.output


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v", "-s"])
