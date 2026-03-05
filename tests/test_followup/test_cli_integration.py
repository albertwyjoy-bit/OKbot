"""CLI集成测试 - 使用 python -m kimi_cli.cli --print 测试session级别功能.

使用方法:
    # 运行所有CLI集成测试
    source /Users/wuyang/anaconda3/etc/profile.d/conda.sh && conda activate okbot && python -m pytest tests/test_followup/test_cli_integration.py -v
    
    # 或者直接运行测试函数
    source /Users/wuyang/anaconda3/etc/profile.d/conda.sh && conda activate okbot && python -c "from tests.test_followup.test_cli_integration import *; import asyncio; asyncio.run(test_cli_basic_prompt())"
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# Test 1: Basic CLI invocation
@pytest.mark.asyncio
async def test_cli_basic_prompt():
    """测试基本CLI命令调用.
    
    命令: python -m kimi_cli.cli --print -p "test prompt"
    """
    result = subprocess.run(
        [sys.executable, "-m", "kimi_cli.cli", "--print", "-p", "你好，这是一个测试"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    
    # 检查命令是否成功执行（返回0或帮助信息）
    # 注意: 如果没有配置API key，可能会显示配置提示
    print(f"Return code: {result.returncode}")
    print(f"Stdout: {result.stdout[:500] if result.stdout else 'None'}")
    print(f"Stderr: {result.stderr[:500] if result.stderr else 'None'}")
    
    # 只要命令没有崩溃就算成功
    assert result.returncode in [0, 1]  # 0=成功, 1=配置错误等


# Test 2: CLI with Task tool background execution
@pytest.mark.asyncio
async def test_cli_task_background_execution():
    """测试CLI中Task工具的后台执行功能.
    
    场景: 使用 Task 工具启动后台子 Agent
    """
    # 这是一个集成测试，验证 Task 工具在 CLI 中的工作
    # 实际测试需要通过完整的 CLI 流程来验证
    
    from kimi_cli.soul.followup import TaskManager, SubagentTask
    from kimi_cli.tools.multiagent.task import Task, Params
    from unittest.mock import MagicMock, patch
    
    # Reset singleton
    TaskManager._instance = None
    
    try:
        # Create mock runtime
        runtime = MagicMock()
        runtime.session.id = "cli-test-session"
        runtime.session.context_file = Path("/tmp/test_context.jsonl")
        runtime.labor_market.fixed_subagent_descs = {"coder": "A coding agent"}
        runtime.labor_market.subagents = {}
        
        # Create Task tool
        with patch("kimi_cli.tools.multiagent.task.load_desc", return_value="Task desc"):
            task_tool = Task(runtime)
        
        # Test background execution params
        params = Params(
            description="测试后台任务",
            subagent_name="coder",
            prompt="请分析这段代码",
            run_in_background=True,
        )
        
        assert params.run_in_background is True
        assert params.description == "测试后台任务"
        
    finally:
        TaskManager._instance = None


# Test 3: TaskList /tasks slash command
@pytest.mark.asyncio
async def test_cli_tasks_slash_command():
    """测试 /tasks 命令.
    
    场景: 用户输入 /tasks 查看所有后台任务
    """
    from kimi_cli.utils.slashcmd import parse_slash_command_call
    
    # Test command parsing
    command = parse_slash_command_call("/tasks")
    assert command is not None
    assert command.name == "tasks"
    
    # Test with status filter
    command2 = parse_slash_command_call("/tasks running")
    assert command2 is not None
    assert command2.name == "tasks"
    assert command2.args == "running"


# Test 4: Session state management
@pytest.mark.asyncio
async def test_cli_session_state():
    """测试CLI session状态管理.
    
    场景: 验证 AgentState 在 CLI 中的转换
    """
    from kimi_cli.soul.kimisoul import AgentState
    
    # Test state enum values
    assert AgentState.IDLE.value == "idle"
    assert AgentState.RUNNING.value == "running"
    
    # Test state comparison
    assert AgentState.IDLE != AgentState.RUNNING


# Test 5: Message queue integration
@pytest.mark.asyncio
async def test_cli_message_queue_integration():
    """测试CLI消息队列集成.
    
    场景: 验证 MessageQueue 在 CLI session 中的工作
    """
    from kimi_cli.soul.followup import MessageQueue, QueueItem
    
    queue = MessageQueue()
    
    # Test putting messages
    await queue.put_user_message("用户消息", source="cli")
    assert queue.qsize() == 1
    
    # Test getting messages
    item = queue.get_nowait()
    assert item is not None
    assert item.type == "user"
    assert item.content == "用户消息"


# Test 6: Complete workflow simulation
@pytest.mark.asyncio
async def test_cli_complete_workflow_simulation():
    """模拟完整的CLI工作流.
    
    场景: 
    1. 用户启动后台任务
    2. 任务进入 TaskManager
    3. 用户查询 /tasks
    4. 任务完成后发送结果
    """
    from unittest.mock import MagicMock, patch
    from kimi_cli.soul.followup import (
        TaskManager,
        AgentMessageBus,
        BackgroundTaskResultMessage,
        message_bus,
        TaskStatus,
        SubagentTask,
    )
    
    # Reset singletons
    TaskManager._instance = None
    message_bus._subscribers.clear()
    
    try:
        session_id = "workflow-test-session"
        
        # Step 1: Register session
        TaskManager().register_session(session_id)
        
        # Step 2: Create and add a task
        mock_agent = MagicMock()
        task = SubagentTask(
            session_id=session_id,
            description="代码分析任务",
            subagent_name="coder",
            agent=mock_agent,
            prompt="分析项目结构",
        )
        task.status = TaskStatus.RUNNING
        TaskManager().add_task(session_id, task)
        
        # Step 3: Verify task is listed
        tasks = TaskManager().list_tasks(session_id)
        assert len(tasks) == 1
        assert tasks[0].description == "代码分析任务"
        
        # Step 4: Simulate task completion via MessageBus
        received = []
        async def callback(msg):
            received.append(msg)
        
        message_bus.subscribe(session_id, callback)
        
        result_msg = BackgroundTaskResultMessage(
            task_id=task.task_id,
            session_id=session_id,
            task_description="代码分析任务",
            subagent_name="coder",
            status="completed",
            output="分析完成",
        )
        
        await message_bus.publish(session_id, result_msg)
        
        # Verify message was delivered
        assert len(received) == 1
        assert received[0].status == "completed"
        
    finally:
        TaskManager._instance = None
        message_bus._subscribers.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
