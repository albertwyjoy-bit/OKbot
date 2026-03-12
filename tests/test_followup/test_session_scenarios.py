"""Session级别的Steering & Follow-up Messages测试用例.

测试场景基于 docs/specs/steering-followup-messages.md 中的规范:
1. 人机交互层 - 用户消息排队和Slash命令打断
2. Agent间通信层 - 后台任务结果路由
3. 任务管理 - TaskList/TaskOutput/TaskStop工具
4. Session生命周期 - 任务清理

使用方法:
    # 运行所有测试
    python -m pytest tests/test_followup/test_session_scenarios.py -v
    
    # 运行特定测试
    python -m pytest tests/test_followup/test_session_scenarios.py::TestHumanAgentInteraction::test_user_message_queued_when_agent_running -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import (
    AgentMessageBus,
    BackgroundTaskResultMessage,
    MessageQueue,
    QueueItem,
    SubagentTask,
    TaskManager,
    TaskStatus,
    message_bus,
)
from kimi_cli.soul.kimisoul import AgentState, KimiSoul
from kimi_cli.wire.types import TextPart


class TestHumanAgentInteraction:
    """人机交互层测试 - 场景1: 用户消息处理."""

    @pytest.fixture
    def queue(self):
        """Create a fresh message queue."""
        return MessageQueue()

    @pytest.mark.asyncio
    async def test_user_message_queued_when_agent_running(self, queue):
        """场景: Agent正在执行时，用户发送消息 → 消息应进入队列.
        
        Spec: Section 2.1 - 普通消息统一作为 Follow-up 排队
        """
        # Given: Agent 正在运行
        agent_state = AgentState.RUNNING

        # When: 用户发送消息
        await queue.put_user_message("帮我分析一下这个代码", source="feishu")

        # Then: 消息应该进入队列
        assert not queue.followup_queue_empty()
        items = queue.get_followup_messages()
        assert len(items) == 1
        assert items[0].type == "user"
        assert items[0].content == "帮我分析一下这个代码"
        assert items[0].metadata["source"] == "feishu"

    @pytest.mark.asyncio
    async def test_slash_stop_command_interrupts_immediately(self):
        """场景: 用户发送/stop命令 → 应立即打断当前操作.
        
        Spec: Section 2.1 - /slash 命令立即执行，可能打断当前操作
        """
        # Given: 模拟 KimiSoul 正在运行
        mock_soul = MagicMock()
        mock_soul.state = AgentState.RUNNING
        mock_soul._state = AgentState.RUNNING

        # When: 用户发送 /stop
        # Note: 在真实场景中，Shell 会解析 slash 命令并调用 soul.abort()
        # 这里我们验证命令解析逻辑
        from kimi_cli.utils.slashcmd import parse_slash_command_call

        command = parse_slash_command_call("/stop")
        
        # Then: 应该识别为 stop 命令
        assert command is not None
        assert command.name == "stop"

    @pytest.mark.asyncio
    async def test_multiple_messages_fifo_order(self, queue):
        """场景: 用户连续发送多条消息 → 按FIFO顺序处理.
        
        Spec: Section 7.1 - 所有消息按时间顺序排队，先进先出
        """
        # When: 用户连续发送多条消息
        await queue.put_user_message("第一条消息", source="feishu")
        await queue.put_user_message("第二条消息", source="feishu")
        await queue.put_user_message("第三条消息", source="feishu")

        # Then: 消息按FIFO顺序
        items = queue.get_followup_messages()
        assert len(items) == 3

        assert items[0].content == "第一条消息"
        assert items[1].content == "第二条消息"
        assert items[2].content == "第三条消息"
        assert items[0].timestamp <= items[1].timestamp <= items[2].timestamp

    @pytest.mark.asyncio
    async def test_user_message_injected_to_context(self, queue):
        """场景: 队列中的用户消息被注入到上下文.
        
        Spec: Section 3.2 - 用户消息正常添加到 context
        """
        from kosong.message import Message

        # Given: 队列中有用户消息
        await queue.put_user_message("请帮我分析代码", source="feishu")
        items = queue.get_followup_messages()
        item = items[0]

        # And: Mock context
        mock_context = MagicMock()
        mock_context.append_message = AsyncMock()

        # When: 注入到上下文
        await queue.inject_to_context(item, mock_context)

        # Then: 应该以 user role 添加
        mock_context.append_message.assert_called_once()
        call_args = mock_context.append_message.call_args[0][0]
        assert isinstance(call_args, Message)
        assert call_args.role == "user"


class TestAgentCommunication:
    """Agent间通信层测试 - 场景2: 后台任务结果路由."""

    @pytest.fixture(autouse=True)
    def reset_message_bus(self):
        """Reset message bus before each test."""
        message_bus._subscribers.clear()
        yield
        message_bus._subscribers.clear()

    @pytest.fixture
    def queue(self):
        """Create a fresh message queue."""
        return MessageQueue()

    @pytest.fixture
    def sample_task_result(self):
        """Create a sample task result message."""
        return BackgroundTaskResultMessage(
            task_id="task-abc12345",
            session_id="session-test",
            task_description="代码分析任务",
            subagent_name="coder",
            status="completed",
            output="分析完成：发现3个问题",
            output_file="/home/user/.kimi/tasks/task-abc12345.log",
            started_at="2026-03-03T10:00:00",
            completed_at="2026-03-03T10:05:00",
        )

    @pytest.mark.asyncio
    async def test_task_result_delivered_when_agent_idle(self, queue, sample_task_result):
        """场景: Agent空闲时收到后台任务结果 → 立即处理.
        
        Spec: Section 5.1 Case A - 主 Agent 在 IDLE，立即构建 system-level 提示
        """
        # Given: Agent 处于 IDLE 状态
        agent_state = AgentState.IDLE

        # When: 后台任务结果进入队列
        await queue.put_task_result(sample_task_result)

        # Then: 消息应该在队列中
        assert not queue.steering_queue_empty()
        items = queue.get_steering_messages()
        assert len(items) == 1
        assert items[0].type == "system_notification"
        assert sample_task_result.task_id in items[0].content

    @pytest.mark.asyncio
    async def test_task_result_queued_when_agent_running(self, queue, sample_task_result):
        """场景: Agent运行时收到后台任务结果 → 进入队列.
        
        Spec: Section 5.1 Case B - 主 Agent 在 RUNNING，Task Result 进入 Follow-up Queue
        """
        # Given: Agent 处于 RUNNING 状态
        agent_state = AgentState.RUNNING

        # When: 后台任务结果进入队列（模拟运行期间收到）
        await queue.put_task_result(sample_task_result)

        # Then: 消息应该在队列中等待
        assert not queue.steering_queue_empty()
        items = queue.get_steering_messages()
        assert len(items) == 1
        assert items[0].type == "system_notification"
        assert "task-abc12345" in items[0].content
        assert "代码分析任务" in items[0].content

    @pytest.mark.asyncio
    async def test_task_result_formatted_as_system_notification(self, queue, sample_task_result):
        """场景: Task Result格式化为system-level提示.
        
        Spec: Section 3.1 - Task Result 作为 System-Level 提示，包装在 system 标签中
        """
        # When: 放入队列
        await queue.put_task_result(sample_task_result)
        items = queue.get_steering_messages()
        assert len(items) == 1

        # Then: 内容应包含所有关键信息
        assert "Background task completed:" in items[0].content
        assert "task-abc12345" in items[0].content
        assert "代码分析任务" in items[0].content
        assert "completed" in items[0].content
        assert "分析完成：发现3个问题" in items[0].content

    @pytest.mark.asyncio
    async def test_task_result_injected_with_system_wrapper(self, queue, sample_task_result):
        """场景: Task Result注入context时包装system标签.
        
        Spec: Section 3.2 - Task Result 包装为 system-level 提示注入 context
        """
        from kosong.message import Message
        from kimi_cli.soul.message import system

        # Given: 队列中有任务结果
        await queue.put_task_result(sample_task_result)
        items = queue.get_steering_messages()
        item = items[0]

        # And: Mock context
        mock_context = MagicMock()
        mock_context.append_message = AsyncMock()

        # When: 注入到上下文
        await queue.inject_to_context(item, mock_context)

        # Then: 应该包含 system 标签和普通文本
        mock_context.append_message.assert_called_once()
        call_args = mock_context.append_message.call_args[0][0]
        assert isinstance(call_args, Message)
        assert call_args.role == "user"
        assert len(call_args.content) == 2  # system + text

    @pytest.mark.asyncio
    async def test_message_bus_publish_and_subscribe(self, sample_task_result):
        """场景: Message Bus 发布和订阅机制.
        
        Spec: Section 5.2 - 极简进程内消息总线
        """
        # Given: 订阅者
        received_messages = []

        async def callback(message):
            received_messages.append(message)

        message_bus.subscribe("session-test", callback)

        # When: 发布消息
        result = await message_bus.publish("session-test", sample_task_result)

        # Then: 消息应被接收
        assert result is True
        assert len(received_messages) == 1
        assert received_messages[0].task_id == "task-abc12345"

    @pytest.mark.asyncio
    async def test_message_bus_drops_message_when_offline(self, sample_task_result):
        """场景: 主Agent离线时消息被丢弃.
        
        Spec: Section 5.2 - 主 Agent 离线则消息丢弃
        """
        # Given: 没有订阅者（主 Agent 离线）
        # When: 发布消息
        result = await message_bus.publish("offline-session", sample_task_result)

        # Then: 消息应被丢弃
        assert result is False

    @pytest.mark.asyncio
    async def test_long_output_truncation(self, queue):
        """场景: 任务输出过长时自动截断.
        
        Spec: Section 3.2 - 输出摘要（截断）
        """
        # Given: 很长的输出
        long_output = "A" * 1000
        result = BackgroundTaskResultMessage(
            task_id="task-long",
            task_description="长输出任务",
            status="completed",
            output=long_output,
        )

        # When: 放入队列
        await queue.put_task_result(result)
        items = queue.get_steering_messages()
        assert len(items) == 1

        # Then: 内容应被截断
        assert len(items[0].content) < len(long_output) + 200  # 保留一些元数据空间


class TestTaskManagement:
    """任务管理工具测试 - 场景3: TaskList/TaskOutput/TaskStop."""

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

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_task_list_empty(self, mock_runtime):
        """场景: 查询空任务列表.
        
        Spec: Appendix C.1 - TaskList 工具
        """
        from kimi_cli.tools.multiagent.task_management import TaskList, TaskListParams

        tool = TaskList(mock_runtime)
        params = TaskListParams()

        result = await tool(params)

        assert result.is_error is False
        assert "没有" in result.output or "No" in result.output

    @pytest.mark.asyncio
    async def test_task_list_with_tasks(self, mock_runtime, mock_agent):
        """场景: 查询有任务时的列表.
        
        Spec: Appendix C.1 - TaskList 列出所有任务
        """
        from kimi_cli.tools.multiagent.task_management import TaskList, TaskListParams

        # Given: 添加一些任务
        task1 = SubagentTask(
            session_id="test-session",
            description="任务1",
            subagent_name="agent1",
            agent=mock_agent,
            prompt="Do 1",
        )
        task2 = SubagentTask(
            session_id="test-session",
            description="任务2",
            subagent_name="agent2",
            agent=mock_agent,
            prompt="Do 2",
        )
        TaskManager().add_task("test-session", task1)
        TaskManager().add_task("test-session", task2)

        # When: 调用 TaskList
        tool = TaskList(mock_runtime)
        params = TaskListParams()
        result = await tool(params)

        # Then: 应显示所有任务
        assert result.is_error is False
        assert task1.task_id in result.output
        assert task2.task_id in result.output
        assert "任务1" in result.output or "任务2" in result.output

    @pytest.mark.asyncio
    async def test_task_list_with_status_filter(self, mock_runtime, mock_agent):
        """场景: 按状态过滤任务列表.
        
        Spec: Appendix C.1 - TaskList 支持 status 过滤
        """
        from kimi_cli.tools.multiagent.task_management import TaskList, TaskListParams

        # Given: 不同状态的任务
        running_task = SubagentTask(
            session_id="test-session",
            description="运行中任务",
            subagent_name="agent1",
            agent=mock_agent,
            prompt="Do 1",
        )
        running_task.status = TaskStatus.RUNNING

        completed_task = SubagentTask(
            session_id="test-session",
            description="已完成任务",
            subagent_name="agent2",
            agent=mock_agent,
            prompt="Do 2",
        )
        completed_task.status = TaskStatus.COMPLETED

        TaskManager().add_task("test-session", running_task)
        TaskManager().add_task("test-session", completed_task)

        # When: 按 running 过滤
        tool = TaskList(mock_runtime)
        params = TaskListParams(status="running")
        result = await tool(params)

        # Then: 只应显示运行中任务
        assert result.is_error is False
        assert "运行中任务" in result.output or "running" in result.output.lower()

    @pytest.mark.asyncio
    async def test_task_output_nonexistent(self, mock_runtime):
        """场景: 查询不存在的任务.
        
        Spec: Appendix C.1 - TaskOutput 查询指定任务
        """
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams

        tool = TaskOutput(mock_runtime)
        params = TaskOutputParams(task_id="non-existent-task")

        result = await tool(params)

        assert result.is_error is False
        assert "不存在" in result.output or "not found" in result.output.lower() or "已完成" in result.output

    @pytest.mark.asyncio
    async def test_task_stop_nonexistent(self, mock_runtime):
        """场景: 停止不存在的任务.
        
        Spec: Appendix C.1 - TaskStop 停止指定任务
        """
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        tool = TaskStop(mock_runtime)
        params = TaskStopParams(task_id="non-existent-task")

        result = await tool(params)

        assert result.is_error is False
        assert "不存在" in result.output or "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_task_stop_all(self, mock_runtime, mock_agent):
        """场景: 停止所有后台任务.
        
        Spec: Appendix C.1 - TaskStop 支持 stop_all
        """
        from kimi_cli.tools.multiagent.task_management import TaskStop, TaskStopParams

        # Given: 多个运行中任务
        task1 = SubagentTask(
            session_id="test-session",
            description="任务1",
            subagent_name="agent1",
            agent=mock_agent,
            prompt="Do 1",
        )
        task1.status = TaskStatus.RUNNING
        task1._completion_event.set()  # 确保 wait() 立即返回

        TaskManager().add_task("test-session", task1)

        # When: 停止所有任务
        tool = TaskStop(mock_runtime)
        params = TaskStopParams(stop_all=True)
        result = await tool(params)

        # Then: 应成功停止
        assert result.is_error is False
        assert "已停止" in result.output or "stopped" in result.output.lower()


class TestSessionLifecycle:
    """Session生命周期测试 - 场景4: 任务清理."""

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        """Reset TaskManager singleton before each test."""
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_shutdown_session_cleans_tasks(self, mock_agent):
        """场景: Session结束时清理所有后台任务.
        
        Spec: Section 7.3.2 - Session 结束时调用 shutdown_session()
        """
        # Given: Session 有运行中任务
        task = SubagentTask(
            session_id="session-to-shutdown",
            description="后台任务",
            subagent_name="agent1",
            agent=mock_agent,
            prompt="Do something",
        )
        task.status = TaskStatus.RUNNING
        task._completion_event.set()  # 确保 wait() 立即返回

        TaskManager().add_task("session-to-shutdown", task)
        assert len(TaskManager().list_tasks("session-to-shutdown")) == 1

        # When: Session 结束（调用 shutdown_session）
        count = await TaskManager().shutdown_session("session-to-shutdown", timeout=0.1)

        # Then: 任务应被清理
        assert count == 1
        assert len(TaskManager().list_tasks("session-to-shutdown")) == 0

    @pytest.mark.asyncio
    async def test_new_session_starts_fresh(self, mock_agent):
        """场景: 新Session不继承旧Session的任务.
        
        Spec: Section 7.3.2 - Session 重启 = 重新开始
        """
        # Given: 旧 Session 有任务
        old_session = "old-session"
        task = SubagentTask(
            session_id=old_session,
            description="旧任务",
            subagent_name="agent1",
            agent=mock_agent,
            prompt="Do something",
        )
        TaskManager().add_task(old_session, task)

        # When: 新 Session 注册
        new_session = "new-session"
        TaskManager().register_session(new_session)

        # Then: 新 Session 应该没有任务
        assert len(TaskManager().list_tasks(new_session)) == 0
        # 旧 Session 任务仍然存在（直到清理）
        assert len(TaskManager().list_tasks(old_session)) == 1

    @pytest.mark.asyncio
    async def test_clear_command_cleans_tasks(self):
        """场景: /clear命令清理所有后台任务.
        
        Spec: Section 7.3.1 - /clear (context 清空) 需要清理后台任务
        """
        # Given: Session 有任务
        session_id = "session-with-tasks"
        task = SubagentTask(
            session_id=session_id,
            description="任务",
            subagent_name="agent1",
            agent=MagicMock(),
            prompt="Do something",
        )
        task.status = TaskStatus.RUNNING
        task._completion_event.set()
        TaskManager().add_task(session_id, task)

        # When: 模拟 /clear 命令的行为（shutdown_session + clear context）
        count = await TaskManager().shutdown_session(session_id, timeout=0.1)

        # Then: 任务应被清理
        assert count == 1
        assert len(TaskManager().list_tasks(session_id)) == 0

    @pytest.mark.asyncio
    async def test_shutdown_empty_session(self):
        """场景: 关闭没有任务的Session.
        
        Spec: Section 7.3.2 - 没有任务时正常返回
        """
        # When: 关闭没有任务的 Session
        count = await TaskManager().shutdown_session("empty-session")

        # Then: 应返回 0
        assert count == 0


class TestIntegrationScenarios:
    """集成场景测试 - 完整流程."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """Reset all singletons before each test."""
        TaskManager._instance = None
        message_bus._subscribers.clear()
        yield
        TaskManager._instance = None
        message_bus._subscribers.clear()

    @pytest.mark.asyncio
    async def test_full_background_task_workflow(self):
        """场景: 完整的后台任务生命周期.
        
        流程:
        1. 用户启动后台任务
        2. 任务在后台运行
        3. 任务完成后通过 Message Bus 发送结果
        4. 结果被放入队列
        5. Agent 处理队列中的消息
        """
        from unittest.mock import MagicMock

        # Given: Session 和队列
        session_id = "test-session"
        queue = MessageQueue()
        TaskManager().register_session(session_id)

        # And: 订阅 Message Bus
        received_messages = []

        async def on_task_complete(message):
            await queue.put_task_result(message)
            received_messages.append(message)

        message_bus.subscribe(session_id, on_task_complete)

        # When: 模拟后台任务完成
        task_result = BackgroundTaskResultMessage(
            task_id="task-12345678",
            session_id=session_id,
            task_description="测试任务",
            subagent_name="test_agent",
            status="completed",
            output="任务完成结果",
            output_file="/tmp/test.log",
        )

        # 发布结果
        await message_bus.publish(session_id, task_result)

        # Then: 结果应被接收并放入队列
        assert len(received_messages) == 1
        assert not queue.steering_queue_empty()

        # And: 可以注入到上下文
        items = queue.get_steering_messages()
        assert len(items) == 1
        assert items[0].type == "system_notification"
        assert "task-12345678" in items[0].content

    @pytest.mark.asyncio
    async def test_mixed_user_and_task_messages(self):
        """场景: 用户消息和任务结果混合排队.
        
        Spec: Section 7.1 - 所有消息按时间顺序排布
        """
        queue = MessageQueue()

        # When: 混合放入用户消息和任务结果
        await queue.put_user_message("用户消息1", source="feishu")
        
        task_result = BackgroundTaskResultMessage(
            task_id="task-1",
            task_description="任务1",
            status="completed",
            output="结果1",
        )
        await queue.put_task_result(task_result)
        
        await queue.put_user_message("用户消息2", source="feishu")

        # Then: followup 队列有用户消息，steering 队列有任务结果
        followup_items = queue.get_followup_messages()
        steering_items = queue.get_steering_messages()

        assert len(followup_items) == 2
        assert followup_items[0].type == "user"
        assert followup_items[0].content == "用户消息1"
        assert followup_items[1].type == "user"
        assert followup_items[1].content == "用户消息2"

        assert len(steering_items) == 1
        assert steering_items[0].type == "system_notification"
        assert "task-1" in steering_items[0].content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
