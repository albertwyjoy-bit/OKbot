"""任务管理器实现 - 管理后台子 Agent 任务的生命周期.

设计原则:
- 按 session 隔离任务
- Session 结束时清理所有后台任务
- 任务日志保存到文件，便于用户查看
"""

from __future__ import annotations

import asyncio
import enum
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional
from uuid import uuid4

from kimi_cli.utils.logging import logger

if TYPE_CHECKING:
    from kimi_cli.soul.agent import Agent
    from kimi_cli.soul.kimisoul import KimiSoul


def _choose_output_dir() -> Path:
    """Pick a writable directory for background task logs."""
    candidates: list[Path] = []

    env_dir = os.environ.get("KIMI_TASKS_DIR")
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    candidates.append(Path.home() / ".kimi" / "tasks")
    candidates.append(Path(tempfile.gettempdir()) / "kimi-cli" / "tasks")

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.touch(exist_ok=True)
            probe.unlink(missing_ok=True)
            return candidate
        except Exception as exc:
            last_error = exc
            logger.warning(f"Task log directory unavailable: {candidate} ({exc})")

    message = "No writable directory available for background task logs"
    if last_error is not None:
        raise RuntimeError(message) from last_error
    raise RuntimeError(message)


class TaskStatus(str, enum.Enum):
    """任务状态."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class SubagentTask:
    """后台子 Agent 任务包装器."""

    # 任务标识
    task_id: str = field(default_factory=lambda: f"task-{uuid4().hex[:8]}")

    # 任务信息
    session_id: str = ""
    description: str = ""
    subagent_name: str = ""

    # Agent 相关（运行时才设置）
    agent: Optional["Agent"] = field(default=None, repr=False)
    prompt: str = ""

    # 日志文件 - FIX: 使用 None 作为默认值来区分用户是否显式提供
    output_file: Optional[Path] = field(default=None, repr=False)

    # 状态
    status: TaskStatus = TaskStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # 内部状态
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _soul: Optional["KimiSoul"] = field(default=None, repr=False)
    _stop_requested: bool = field(default=False, repr=False)
    _completion_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _cancel_event: Optional[asyncio.Event] = field(default=None, repr=False)  # FIX: 取消事件
    _max_runtime: float = field(default=3600.0, repr=False)  # FIX: 最大运行时间 1小时

    def __post_init__(self):
        """初始化日志文件路径."""
        # FIX: 只有在用户没有提供 output_file 时才生成默认路径
        if self.output_file is None:
            self.output_file = _choose_output_dir() / f"{self.task_id}.log"
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

    async def run_in_background(
        self,
        run_soul_fn: Optional[callable] = None,
        ui_loop_fn: Optional[callable] = None,
    ) -> None:
        """在后台运行子 Agent.

        Args:
            run_soul_fn: 运行 soul 的函数，用于测试注入
            ui_loop_fn: UI 循环函数，用于测试注入
        """
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
        self._completion_event.clear()

        # FIX: 使用带超时的包装器，添加详细错误日志
        try:
            self._task = asyncio.create_task(self._run_with_timeout(run_soul_fn, ui_loop_fn))
            logger.info(f"Background task started: {self.task_id} (session: {self.session_id})")
        except Exception as e:
            logger.exception(f"Failed to create background task {self.task_id}: {e}")
            self.status = TaskStatus.FAILED
            raise

    async def _run_with_timeout(
        self,
        run_soul_fn: Optional[callable] = None,
        ui_loop_fn: Optional[callable] = None,
    ) -> None:
        """运行任务并添加超时保护."""
        try:
            logger.debug(f"Starting task {self.task_id} with timeout {self._max_runtime}s")
            await asyncio.wait_for(
                self._run_and_publish(run_soul_fn, ui_loop_fn),
                timeout=self._max_runtime
            )
        except asyncio.TimeoutError:
            logger.error(f"Background task {self.task_id} timed out after {self._max_runtime}s")
            
            # 设置完成事件，避免 wait() 永远等待
            self.status = TaskStatus.FAILED
            self.completed_at = datetime.now()
            self._completion_event.set()
            
            # 发送超时结果
            await self._publish_result("任务执行超时")
        except Exception as e:
            # FIX: 捕获其他异常（如导入错误），确保状态更新
            logger.exception(f"Background task {self.task_id} failed with exception: {type(e).__name__}: {e}")
            
            # 设置完成事件，避免 wait() 永远等待
            self.status = TaskStatus.FAILED
            self.completed_at = datetime.now()
            self._completion_event.set()
            self._cancel_event = None
            
            # 发送失败结果
            await self._publish_result(f"任务执行失败: {type(e).__name__}: {e}")

    async def _run_and_publish(
        self,
        run_soul_fn: Optional[callable] = None,
        ui_loop_fn: Optional[callable] = None,
    ) -> None:
        """运行子 Agent 并在完成后发布结果.

        Args:
            run_soul_fn: 运行 soul 的函数，用于测试注入
            ui_loop_fn: UI 循环函数，用于测试注入
        """
        from kimi_cli.soul import run_soul
        from kimi_cli.soul.followup import BackgroundTaskResultMessage, message_bus
        from kimi_cli.soul.context import Context
        from kimi_cli.soul.kimisoul import KimiSoul
        from kimi_cli.utils.logging import logger as soul_logger
        from kimi_cli.utils.aioqueue import QueueShutDown

        result_output = ""
        background_yolo_token = None

        # 改进的 UI loop - 实时将 wire 消息写入 .log 文件，让 TaskOutput 工具能看到进度
        async def _background_ui_loop(wire) -> None:
            """Background task UI loop - 实时写入进度到日志文件."""
            import json as _json
            from kimi_cli.wire.types import TextPart, ToolResult, StepBegin, TurnBegin, TurnEnd

            wire_ui = wire.ui_side(merge=True)

            def _append_progress(role: str, content_text: str) -> None:
                """同步追加一行进度到日志文件（JSONL 格式）."""
                try:
                    line = _json.dumps(
                        {"role": role, "content": content_text},
                        ensure_ascii=False,
                    )
                    with self.output_file.open("a", encoding="utf-8") as _f:
                        _f.write(line + "\n")
                except Exception as _e:
                    soul_logger.debug(
                        f"Background task {self.task_id} failed to write progress: {_e}"
                    )

            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(wire_ui.receive(), timeout=1.0)
                        soul_logger.debug(f"Background task {self.task_id} wire: {type(msg).__name__}")

                        # 将关键消息实时写入日志文件，供 TaskOutput 工具查看
                        if isinstance(msg, TextPart):
                            if msg.text and msg.text.strip():
                                _append_progress("_progress", msg.text)
                        elif isinstance(msg, ToolResult):
                            # tool 结果：提取文本内容
                            parts = msg.content if isinstance(msg.content, list) else [msg.content]
                            texts = [
                                p.text for p in parts
                                if isinstance(p, TextPart) and p.text and p.text.strip()
                            ]
                            if texts:
                                _append_progress("_tool_progress", " ".join(texts))
                        elif isinstance(msg, StepBegin):
                            _append_progress("_step", f"Step {msg.n}")

                    except asyncio.TimeoutError:
                        if self._stop_requested:
                            soul_logger.debug(
                                f"Background task {self.task_id} stop requested, exiting UI loop"
                            )
                            break
                        continue
            except asyncio.CancelledError:
                raise
            except QueueShutDown:
                soul_logger.debug(f"Background task {self.task_id} UI loop received QueueShutDown")
            except Exception as e:
                soul_logger.debug(f"Background task {self.task_id} UI loop ended: {e}")

        try:
            # 创建子 Agent 上下文（与主 Agent 隔离）
            logger.debug(f"Creating context for background task {self.task_id}")
            context = Context(file_backend=self.output_file)

            # 后台任务通过 ContextVar 强制 YOLO，不修改主 Agent 对象（兼容 frozen dataclass Agent）
            from kimi_cli.soul.approval import _background_yolo_mode
            background_yolo_token = _background_yolo_mode.set(True)

            # 子 Agent 不注册消息总线，避免收到其他任务的消息
            logger.debug(f"Creating KimiSoul for background task {self.task_id}")
            self._soul = KimiSoul(self.agent, context=context, enable_message_bus=False)

            # FIX: 创建取消事件并保存引用
            self._cancel_event = asyncio.Event()

            # 运行子 Agent
            actual_ui_loop = ui_loop_fn or _background_ui_loop
            
            logger.info(f"Starting background task {self.task_id}")
            
            if run_soul_fn:
                # 测试 mock 函数 - 保持原参数列表
                await run_soul_fn(self._soul, self.prompt, actual_ui_loop, self._cancel_event)
            else:
                # FIX: 创建 wire_file 用于持久化 wire 消息
                from kimi_cli.wire.file import WireFile
                wire_file = WireFile(self.output_file.with_suffix('.wire.log'))
                
                await run_soul(
                    self._soul,
                    self.prompt,
                    actual_ui_loop,
                    self._cancel_event,
                    wire_file=wire_file,
                    is_main_agent=False,
                )

            # 获取结果
            logger.debug(f"run_soul completed for task {self.task_id}")
            if context.history and len(context.history) > 0:
                last_message = context.history[-1]
                if hasattr(last_message, 'extract_text'):
                    result_output = last_message.extract_text(sep="\n")
                else:
                    result_output = str(last_message)
                logger.info(f"Background task {self.task_id} completed with {len(context.history)} messages")
            else:
                result_output = "Task completed but no output generated"
                logger.warning(f"Background task {self.task_id} completed with empty context")

            self.status = TaskStatus.COMPLETED
            logger.info(f"Background task completed: {self.task_id}")

        except asyncio.CancelledError:
            self.status = TaskStatus.STOPPED
            result_output = "任务被用户取消"
            logger.info(f"Background task stopped: {self.task_id}")

        except Exception as e:
            self.status = TaskStatus.FAILED
            result_output = f"任务执行失败: {type(e).__name__}: {e}"
            logger.exception(f"Background task {self.task_id} failed")

        finally:
            if background_yolo_token is not None:
                from kimi_cli.soul.approval import _background_yolo_mode
                _background_yolo_mode.reset(background_yolo_token)

            self.completed_at = datetime.now()
            self._completion_event.set()
            self._cancel_event = None  # 清理引用

            # 发布结果
            await self._publish_result(result_output)

    async def _publish_result(self, result_output: str) -> None:
        """发布任务结果到消息总线并从 TaskManager 移除.
        
        Args:
            result_output: 任务输出内容
        """
        from kimi_cli.soul.followup import BackgroundTaskResultMessage, message_bus
        from kimi_cli.soul.followup.message_bus import MAIN_AGENT_TARGET

        try:
            # 构建结果消息（与同步 Task 一致：10000 字符截断，截断时加标识）
            max_len = 10000
            output_text = result_output
            if len(output_text) > max_len:
                output_text = output_text[:max_len] + "\n...truncated"
            message = BackgroundTaskResultMessage(
                task_id=self.task_id,
                session_id=self.session_id,
                task_description=self.description,
                subagent_name=self.subagent_name,
                status=self.status.value,
                output=output_text,
                output_file=str(self.output_file),
                started_at=self.started_at.isoformat() if self.started_at else None,
                completed_at=self.completed_at.isoformat() if self.completed_at else None,
                target=MAIN_AGENT_TARGET,
            )

            # 通过 MessageBus 发送结果
            published = await message_bus.publish(self.session_id, message)
            if published:
                logger.info(f"Background task {self.task_id} result published (status: {self.status.value})")
            else:
                logger.warning(f"Background task {self.task_id} result not published (no subscriber)")
        except Exception as e:
            logger.exception(f"Failed to publish task {self.task_id} result: {e}")

        # 从 TaskManager 移除（无论发布是否成功）
        try:
            TaskManager().remove_task(self.session_id, self.task_id)
        except Exception as e:
            logger.warning(f"Failed to remove task {self.task_id} from TaskManager: {e}")

    def request_stop(self) -> None:
        """请求停止任务."""
        self._stop_requested = True
        
        # FIX: 设置取消事件，通知 run_soul 退出
        if self._cancel_event:
            self._cancel_event.set()
            logger.debug(f"Cancel event set for task: {self.task_id}")
        
        if self._task and not self._task.done():
            self._task.cancel()
            logger.debug(f"Task cancelled: {self.task_id}")
        
        logger.info(f"Stop requested for task: {self.task_id}")

    async def wait(self, timeout: Optional[float] = None) -> bool:
        """等待任务完成.
        
        Args:
            timeout: 可选的超时时间（秒）
            
        Returns:
            True if task completed, False if timeout
        """
        if self._task:
            try:
                if timeout:
                    await asyncio.wait_for(self._completion_event.wait(), timeout=timeout)
                else:
                    await self._completion_event.wait()
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Wait for task {self.task_id} timed out")
                return False
        return True

    def is_running(self) -> bool:
        """检查任务是否正在运行."""
        return self.status == TaskStatus.RUNNING and self._task is not None and not self._task.done()

    def is_done(self) -> bool:
        """检查任务是否已完成."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)


class TaskManager:
    """全局任务管理器（按 session 隔离）.

    这是一个单例类，用于管理所有后台任务.
    """

    _instance: Optional["TaskManager"] = None

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._tasks_by_session: Dict[str, List[SubagentTask]] = {}
        self._completed_tasks_by_session: Dict[str, Dict[str, SubagentTask]] = {}
        self._initialized = True

    def register_session(self, session_id: str) -> None:
        """Session 启动时注册.

        Args:
            session_id: 会话 ID
        """
        if session_id not in self._tasks_by_session:
            self._tasks_by_session[session_id] = []
        if session_id not in self._completed_tasks_by_session:
            self._completed_tasks_by_session[session_id] = {}
        logger.debug(f"Session registered in TaskManager: {session_id}")

    def add_task(self, session_id: str, task: SubagentTask) -> None:
        """添加后台任务到指定 session.

        Args:
            session_id: 会话 ID
            task: 后台任务
        """
        self.register_session(session_id)
        self._tasks_by_session[session_id].append(task)
        logger.debug(f"Task {task.task_id} added to session {session_id}")

    def get_task(self, session_id: str, task_id: str) -> Optional[SubagentTask]:
        """获取指定任务.

        Args:
            session_id: 会话 ID
            task_id: 任务 ID

        Returns:
            任务对象，如果不存在返回 None
        """
        tasks = self._tasks_by_session.get(session_id, [])
        for task in tasks:
            if task.task_id == task_id:
                return task
        return None

    def get_completed_task(self, session_id: str, task_id: str) -> Optional[SubagentTask]:
        """获取已完成并已从活动列表移除的任务."""
        return self._completed_tasks_by_session.get(session_id, {}).get(task_id)

    def remove_task(self, session_id: str, task_id: str) -> bool:
        """从管理器中移除任务.

        Args:
            session_id: 会话 ID
            task_id: 任务 ID

        Returns:
            是否成功移除
        """
        tasks = self._tasks_by_session.get(session_id, [])
        for i, task in enumerate(tasks):
            if task.task_id == task_id:
                self._completed_tasks_by_session.setdefault(session_id, {})[task_id] = task
                tasks.pop(i)
                logger.debug(f"Task {task_id} removed from session {session_id}")
                return True
        return False

    def list_tasks(self, session_id: str, status: Optional[str] = None) -> List[SubagentTask]:
        """获取 session 的所有任务.

        Args:
            session_id: 会话 ID
            status: 可选的状态过滤 (running/completed/failed/stopped/pending)

        Returns:
            任务列表
        """
        tasks = list(self._tasks_by_session.get(session_id, []))

        if status:
            tasks = [t for t in tasks if t.status.value == status]

        return tasks

    def get_all_tasks(self, status: Optional[str] = None) -> Dict[str, List[SubagentTask]]:
        """获取所有 session 的任务.

        Args:
            status: 可选的状态过滤

        Returns:
            session_id -> 任务列表 的字典
        """
        result = {}
        for session_id, tasks in self._tasks_by_session.items():
            if status:
                filtered = [t for t in tasks if t.status.value == status]
                if filtered:
                    result[session_id] = filtered
            else:
                result[session_id] = list(tasks)
        return result

    async def shutdown_session(self, session_id: str, timeout: float = 30.0) -> int:
        """Session 结束时调用，清理该 session 的所有后台任务.

        由以下场景触发:
        - /new (新建 session)
        - /sessions 切换
        - exit/quit/Ctrl+D
        - /clear (context 清空)

        Args:
            session_id: 会话 ID
            timeout: 优雅停止超时时间（秒）

        Returns:
            清理的任务数量
        """
        tasks = self._tasks_by_session.pop(session_id, [])
        self._completed_tasks_by_session.pop(session_id, None)
        if not tasks:
            logger.debug(f"No tasks to shutdown for session: {session_id}")
            return 0

        logger.info(f"Shutting down {len(tasks)} background tasks for session {session_id}")

        # 1. 发送优雅停止信号
        for task in tasks:
            task.request_stop()

        # 2. 等待完成（带超时）
        try:
            await asyncio.wait_for(
                self._wait_tasks_complete(tasks),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # 3. 超时后强制取消
            logger.warning(f"Shutdown timeout for session {session_id}, force cancelling")
            for task in tasks:
                if task._task and not task._task.done():
                    task._task.cancel()

        return len(tasks)

    async def _wait_tasks_complete(self, tasks: List[SubagentTask]) -> None:
        """等待所有任务完成.

        Args:
            tasks: 任务列表
        """
        if not tasks:
            return

        await asyncio.gather(
            *[task.wait() for task in tasks],
            return_exceptions=True,
        )

    def clear_all(self) -> int:
        """清理所有任务（用于测试）.

        Returns:
            清理的任务数量
        """
        total = sum(len(tasks) for tasks in self._tasks_by_session.values())
        self._tasks_by_session.clear()
        self._completed_tasks_by_session.clear()
        logger.debug(f"All tasks cleared: {total}")
        return total
