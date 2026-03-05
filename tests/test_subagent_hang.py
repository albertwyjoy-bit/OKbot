"""
子 Agent 卡住问题诊断测试脚本

问题描述:
- 子 Agent 在后台运行时状态显示为 running
- 但日志不再更新，任务永远不结束
- 需要找出卡住的位置和原因

测试项目:
1. 检测 run_soul 中的事件循环问题
2. 检测 UI loop 是否阻塞
3. 检测 LLM 调用是否卡住
4. 检测文件写入是否阻塞
"""

import asyncio
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kimi_cli.soul.followup import SubagentTask, TaskManager, TaskStatus
from kimi_cli.soul import run_soul
from kimi_cli.wire import Wire


class SubagentHangDetector:
    """子 Agent 卡住检测器 - 用于定位卡住位置."""
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.checkpoints = []
        self.hang_location = None
        self.start_time = None
        
    def record(self, location: str, extra: dict = None):
        """记录检查点."""
        checkpoint = {
            "time": datetime.now().isoformat(),
            "elapsed": time.time() - self.start_time if self.start_time else 0,
            "location": location,
            "extra": extra or {},
        }
        self.checkpoints.append(checkpoint)
        print(f"[CHECKPOINT] {location} (elapsed: {checkpoint['elapsed']:.2f}s)")
        
    def start(self):
        """开始计时."""
        self.start_time = time.time()
        
    def detect_hang(self) -> dict:
        """检测卡住位置."""
        if not self.checkpoints:
            return {"error": "No checkpoints recorded"}
            
        last_checkpoint = self.checkpoints[-1]
        elapsed = time.time() - self.start_time
        
        return {
            "total_elapsed": elapsed,
            "last_location": last_checkpoint["location"],
            "last_time": last_checkpoint["time"],
            "time_since_last_checkpoint": elapsed - last_checkpoint["elapsed"],
            "all_checkpoints": self.checkpoints,
        }


@pytest.mark.asyncio
async def test_subagent_task_execution_with_timeout():
    """测试子 Agent 任务执行 - 带超时检测."""
    detector = SubagentHangDetector(timeout=10.0)
    detector.start()
    
    # 创建模拟的 Agent
    mock_agent = MagicMock()
    mock_agent.name = "test_agent"
    mock_agent.system_prompt = "You are a test agent."
    
    detector.record("agent_created")
    
    # 创建任务
    task = SubagentTask(
        session_id="test-session-001",
        description="测试任务",
        subagent_name="test_agent",
        agent=mock_agent,
        prompt="执行一个简单的测试任务",
    )
    
    detector.record("task_created", {"task_id": task.task_id})
    
    # 模拟 run_soul 函数
    async def mock_run_soul(soul, prompt, ui_loop_fn, cancel_event, is_main_agent=False):
        detector.record("run_soul_started", {"is_main_agent": is_main_agent})
        
        # 模拟执行
        await asyncio.sleep(0.1)
        detector.record("run_soul_working")
        
        # 检查是否被取消
        if cancel_event.is_set():
            detector.record("run_soul_cancelled")
            raise asyncio.CancelledError()
            
        await asyncio.sleep(0.1)
        detector.record("run_soul_completed")
    
    # 启动任务
    await task.run_in_background(run_soul_fn=mock_run_soul)
    detector.record("task_started")
    
    # 等待任务完成（带超时）
    try:
        await asyncio.wait_for(task.wait(), timeout=5.0)
        detector.record("task_completed")
    except asyncio.TimeoutError:
        detector.record("task_timeout")
        hang_info = detector.detect_hang()
        print(f"\n[HANG DETECTED] {json.dumps(hang_info, indent=2, default=str)}")
        raise
        
    # 验证任务状态
    assert task.status == TaskStatus.COMPLETED
    assert task.is_done()
    
    print(f"\n[TEST PASSED] All checkpoints:\n{json.dumps(detector.checkpoints, indent=2, default=str)}")


@pytest.mark.asyncio
async def test_ui_loop_blocking_detection():
    """测试 UI Loop 阻塞检测."""
    detector = SubagentHangDetector()
    detector.start()
    
    wire = Wire()
    detector.record("wire_created")
    
    async def blocking_ui_loop(w):
        """模拟阻塞的 UI loop."""
        detector.record("ui_loop_started")
        wire_ui = w.ui_side(merge=True)
        
        try:
            # 这可能会永远阻塞
            detector.record("ui_loop_waiting_for_message")
            msg = await asyncio.wait_for(wire_ui.receive(), timeout=2.0)
            detector.record("ui_loop_received_message", {"msg_type": type(msg).__name__})
        except asyncio.TimeoutError:
            detector.record("ui_loop_receive_timeout")
            raise
    
    # 启动 UI loop
    ui_task = asyncio.create_task(blocking_ui_loop(wire))
    detector.record("ui_task_created")
    
    # 模拟不发送任何消息，让 receive() 超时
    try:
        await asyncio.wait_for(ui_task, timeout=3.0)
    except asyncio.TimeoutError:
        detector.record("test_timeout")
        ui_task.cancel()
        try:
            await ui_task
        except asyncio.CancelledError:
            pass
    
    hang_info = detector.detect_hang()
    print(f"\n[UI LOOP TEST] Result: {json.dumps(hang_info, indent=2, default=str)}")
    
    wire.shutdown()


@pytest.mark.asyncio
async def test_wire_shutdown_hang():
    """测试 Wire 关闭时可能的卡住问题."""
    detector = SubagentHangDetector()
    detector.start()
    
    wire = Wire()
    detector.record("wire_created")
    
    # 创建接收任务
    received_messages = []
    
    async def receive_loop():
        wire_ui = wire.ui_side(merge=True)
        detector.record("receive_loop_started")
        try:
            while True:
                msg = await wire_ui.receive()
                received_messages.append(msg)
                detector.record("message_received", {"msg_type": type(msg).__name__})
        except Exception as e:
            detector.record("receive_loop_error", {"error": str(e)})
    
    receive_task = asyncio.create_task(receive_loop())
    detector.record("receive_task_created")
    
    # 发送一些消息
    from kimi_cli.wire.types import TextPart
    wire.soul_side.send(TextPart(text="Test message 1"))
    detector.record("message_1_sent")
    
    await asyncio.sleep(0.1)
    
    wire.soul_side.send(TextPart(text="Test message 2"))
    detector.record("message_2_sent")
    
    await asyncio.sleep(0.1)
    
    # 关闭 wire
    detector.record("wire_shutdown_start")
    wire.shutdown()
    detector.record("wire_shutdown_done")
    
    # 等待接收任务结束
    try:
        await asyncio.wait_for(receive_task, timeout=2.0)
        detector.record("receive_task_ended")
    except asyncio.TimeoutError:
        detector.record("receive_task_timeout")
        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            detector.record("receive_task_cancelled")
    
    await wire.join()
    detector.record("wire_joined")
    
    print(f"\n[WIRE TEST] Messages received: {len(received_messages)}")
    print(f"Checkpoints: {json.dumps(detector.checkpoints, indent=2, default=str)}")


@pytest.mark.asyncio
async def test_run_soul_cancel_event():
    """测试 run_soul 的取消事件机制."""
    detector = SubagentHangDetector()
    detector.start()
    
    from kimi_cli.soul.kimisoul import KimiSoul
    
    # 创建 mock soul
    mock_soul = MagicMock(spec=KimiSoul)
    
    async def slow_run(user_input):
        detector.record("soul_run_started", {"input": str(user_input)[:50]})
        # 模拟长时间运行的任务
        for i in range(10):
            await asyncio.sleep(0.5)
            detector.record(f"soul_run_progress_{i}")
    
    mock_soul.run = slow_run
    
    cancel_event = asyncio.Event()
    
    async def ui_loop_fn(wire):
        detector.record("ui_loop_started")
        try:
            while True:
                wire_ui = wire.ui_side(merge=True)
                msg = await asyncio.wait_for(wire_ui.receive(), timeout=1.0)
                detector.record("ui_loop_received", {"msg": str(msg)[:50]})
        except asyncio.TimeoutError:
            detector.record("ui_loop_timeout")
        except asyncio.CancelledError:
            detector.record("ui_loop_cancelled")
            raise
    
    # 设置超时后触发取消
    async def cancel_after_delay():
        await asyncio.sleep(1.5)
        detector.record("cancel_event_set")
        cancel_event.set()
    
    cancel_task = asyncio.create_task(cancel_after_delay())
    detector.record("cancel_task_created")
    
    try:
        await run_soul(mock_soul, "test input", ui_loop_fn, cancel_event, is_main_agent=False)
        detector.record("run_soul_completed")
    except Exception as e:
        detector.record("run_soul_error", {"error": str(e)})
    
    await cancel_task
    
    hang_info = detector.detect_hang()
    print(f"\n[CANCEL TEST] Result: {json.dumps(hang_info, indent=2, default=str)}")


def test_task_manager_memory_leak():
    """测试 TaskManager 是否有内存泄漏."""
    manager = TaskManager()
    
    # 清理之前的任务
    manager.clear_all()
    
    initial_count = sum(len(tasks) for tasks in manager._tasks_by_session.values())
    print(f"[MEMORY TEST] Initial task count: {initial_count}")
    
    # 创建多个任务
    for i in range(10):
        task = SubagentTask(
            session_id=f"test-session-{i % 3}",  # 3个不同的session
            description=f"测试任务 {i}",
            subagent_name="test_agent",
            agent=MagicMock(),
            prompt="测试",
        )
        manager.add_task(f"test-session-{i % 3}", task)
    
    after_add_count = sum(len(tasks) for tasks in manager._tasks_by_session.values())
    print(f"[MEMORY TEST] After adding 10 tasks: {after_add_count}")
    
    # 模拟移除一些任务
    for session_id in list(manager._tasks_by_session.keys()):
        tasks = manager._tasks_by_session[session_id]
        if tasks:
            manager.remove_task(session_id, tasks[0].task_id)
    
    after_remove_count = sum(len(tasks) for tasks in manager._tasks_by_session.values())
    print(f"[MEMORY TEST] After removing some tasks: {after_remove_count}")
    
    # 清理
    cleared = manager.clear_all()
    print(f"[MEMORY TEST] Cleared {cleared} tasks")
    
    final_count = sum(len(tasks) for tasks in manager._tasks_by_session.values())
    print(f"[MEMORY TEST] Final task count: {final_count}")
    
    assert final_count == 0


@pytest.mark.asyncio
async def test_background_task_result_publishing():
    """测试后台任务结果发布机制."""
    detector = SubagentHangDetector()
    detector.start()
    
    from kimi_cli.soul.followup import message_bus, BackgroundTaskResultMessage, MAIN_AGENT_TARGET
    
    session_id = "test-pub-session"
    received_messages = []
    
    async def message_handler(msg: BackgroundTaskResultMessage):
        detector.record("message_received", {"task_id": msg.task_id})
        received_messages.append(msg)
    
    # 订阅消息
    message_bus.subscribe(session_id, message_handler, target=MAIN_AGENT_TARGET)
    detector.record("subscribed")
    
    # 发布消息
    msg = BackgroundTaskResultMessage(
        task_id="test-task-001",
        session_id=session_id,
        task_description="测试任务",
        subagent_name="test_agent",
        status="completed",
        output="测试输出",
        target=MAIN_AGENT_TARGET,
    )
    
    detector.record("publishing_message")
    success = await message_bus.publish(session_id, msg)
    detector.record("message_published", {"success": success})
    
    # 等待消息处理
    await asyncio.sleep(0.1)
    
    # 取消订阅
    message_bus.unsubscribe(session_id, target=MAIN_AGENT_TARGET)
    detector.record("unsubscribed")
    
    print(f"\n[PUBLISH TEST] Received {len(received_messages)} messages")
    print(f"Checkpoints: {json.dumps(detector.checkpoints, indent=2, default=str)}")
    
    assert len(received_messages) == 1
    assert received_messages[0].task_id == "test-task-001"


@pytest.mark.asyncio
async def test_concurrent_subagent_tasks():
    """测试并发子 Agent 任务 - 模拟真实卡住场景."""
    detector = SubagentHangDetector()
    detector.start()
    
    manager = TaskManager()
    manager.clear_all()
    
    mock_agent = MagicMock()
    mock_agent.name = "concurrent_test_agent"
    
    async def variable_duration_run_soul(soul, prompt, ui_loop_fn, cancel_event, is_main_agent=False):
        """模拟不同执行时长的 run_soul."""
        task_num = int(prompt.split()[-1]) if prompt.split()[-1].isdigit() else 0
        detector.record(f"task_{task_num}_started")
        
        # 模拟不同任务执行时间
        duration = 0.1 * (task_num + 1)
        
        try:
            await asyncio.wait_for(
                asyncio.sleep(duration),
                timeout=1.0
            )
            detector.record(f"task_{task_num}_completed")
        except asyncio.TimeoutError:
            detector.record(f"task_{task_num}_timeout")
            raise
    
    # 创建多个并发任务
    tasks = []
    for i in range(5):
        task = SubagentTask(
            session_id="concurrent-session",
            description=f"并发任务 {i}",
            subagent_name="concurrent_test_agent",
            agent=mock_agent,
            prompt=f"执行任务 {i}",
        )
        tasks.append(task)
        await task.run_in_background(run_soul_fn=variable_duration_run_soul)
        detector.record(f"task_{i}_scheduled")
    
    # 等待所有任务完成
    try:
        await asyncio.wait_for(
            asyncio.gather(*[t.wait() for t in tasks]),
            timeout=10.0
        )
        detector.record("all_tasks_completed")
    except asyncio.TimeoutError:
        detector.record("wait_timeout")
        # 检查哪些任务还在运行
        for i, task in enumerate(tasks):
            print(f"Task {i}: status={task.status.value}, running={task.is_running()}")
    
    hang_info = detector.detect_hang()
    print(f"\n[CONCURRENT TEST] Result: {json.dumps(hang_info, indent=2, default=str)}")


if __name__ == "__main__":
    # 运行所有测试
    print("=" * 60)
    print("子 Agent 卡住问题诊断测试")
    print("=" * 60)
    
    asyncio.run(test_subagent_task_execution_with_timeout())
    print("\n" + "=" * 60 + "\n")
    
    asyncio.run(test_ui_loop_blocking_detection())
    print("\n" + "=" * 60 + "\n")
    
    asyncio.run(test_wire_shutdown_hang())
    print("\n" + "=" * 60 + "\n")
    
    asyncio.run(test_run_soul_cancel_event())
    print("\n" + "=" * 60 + "\n")
    
    test_task_manager_memory_leak()
    print("\n" + "=" * 60 + "\n")
    
    asyncio.run(test_background_task_result_publishing())
    print("\n" + "=" * 60 + "\n")
    
    asyncio.run(test_concurrent_subagent_tasks())
