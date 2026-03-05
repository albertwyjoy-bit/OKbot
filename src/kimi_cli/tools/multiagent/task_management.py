"""后台任务管理工具 - TaskList, TaskOutput, TaskStop.

用于查询和管理后台运行的子 Agent 任务.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
from typing import override as typing_override

from kosong.tooling import CallableTool2, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.soul.agent import Runtime
from kimi_cli.soul.followup import SubagentTask, TaskManager, TaskStatus


class TaskListParams(BaseModel):
    """TaskList 工具参数."""

    status: Optional[str] = Field(
        default=None,
        description="Filter by task status: running/completed/failed/stopped/pending",
    )


class TaskList(CallableTool2[TaskListParams]):
    """查询当前 session 的所有后台任务."""

    name: str = "TaskList"
    params: type[TaskListParams] = TaskListParams

    def __init__(self, runtime: Runtime):
        super().__init__(description="列出当前会话的所有后台任务及其状态")
        self._session = runtime.session

    @typing_override
    async def __call__(self, params: TaskListParams) -> ToolReturnValue:
        """获取任务列表.

        Args:
            params: 查询参数

        Returns:
            ToolReturnValue 包含格式化的任务列表
        """
        tasks = TaskManager().list_tasks(self._session.id, status=params.status)

        if not tasks:
            if params.status:
                return ToolOk(output=f"当前没有状态为 '{params.status}' 的后台任务")
            return ToolOk(output="当前没有后台任务")

        # 格式化任务列表
        lines = [
            "任务列表:",
            "-" * 90,
            f"{'任务ID':<15} {'描述':<25} {'状态':<10} {'已运行':<10} {'开始时间':<20}",
            "-" * 90,
        ]

        now = datetime.now()

        for task in tasks:
            desc = task.description[:23] + ".." if len(task.description) > 25 else task.description
            status_str = task.status.value
            start_time_str = task.started_at.strftime("%H:%M:%S") if task.started_at else "N/A"
            
            # 计算运行时长
            if task.started_at:
                # 如果任务已完成，使用完成时间；否则使用当前时间
                end_time = task.completed_at if task.completed_at else now
                duration = end_time - task.started_at
                duration_seconds = int(duration.total_seconds())
                
                # 格式化时长：短于1分钟显示秒，短于1小时显示分:秒，否则显示时:分
                if duration_seconds < 60:
                    duration_str = f"{duration_seconds}s"
                elif duration_seconds < 3600:
                    minutes = duration_seconds // 60
                    seconds = duration_seconds % 60
                    duration_str = f"{minutes}m{seconds:02d}s"
                else:
                    hours = duration_seconds // 3600
                    minutes = (duration_seconds % 3600) // 60
                    duration_str = f"{hours}h{minutes:02d}m"
            else:
                duration_str = "-"

            lines.append(
                f"{task.task_id:<15} {desc:<25} {status_str:<10} {duration_str:<10} {start_time_str:<20}"
            )

        lines.append("-" * 90)
        lines.append(f"共 {len(tasks)} 个任务")

        return ToolOk(output="\n".join(lines))


class TaskOutputParams(BaseModel):
    """TaskOutput 工具参数."""

    task_id: str = Field(description="任务 ID")
    tail: int = Field(
        default=20,
        description="返回最新的多少条消息，默认为 20",
    )
    max_tool_output_tokens: int = Field(
        default=200,
        description="tool 角色的消息最大保留多少字符，超长自动截断，其他角色不截断。默认为 200",
    )


class TaskOutput(CallableTool2[TaskOutputParams]):
    """查询后台任务的当前输出."""

    name: str = "TaskOutput"
    params: type[TaskOutputParams] = TaskOutputParams

    def __init__(self, runtime: Runtime):
        super().__init__(description="查询指定后台任务的最新输出内容")
        self._session = runtime.session

    @typing_override
    async def __call__(self, params: TaskOutputParams) -> ToolReturnValue:
        """获取任务输出.

        Args:
            params: 查询参数

        Returns:
            ToolReturnValue 包含任务输出内容
        """
        task = TaskManager().get_task(self._session.id, params.task_id)

        if not task:
            # 尝试从已完成的任务中查找（任务完成后会从 TaskManager 移除）
            return await self._read_output_file(params.task_id, params.tail, params.max_tool_output_tokens)

        # 构建响应
        lines = [
            f"任务ID: {task.task_id}",
            f"描述: {task.description}",
            f"状态: {task.status.value}",
            f"子 Agent: {task.subagent_name}",
        ]

        if task.started_at:
            lines.append(f"开始时间: {task.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

        if task.completed_at:
            lines.append(f"完成时间: {task.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")

        lines.append(f"日志文件: {task.output_file}")
        lines.append("")

        # 读取并解析最新的消息（仅截断 tool 角色的消息）
        messages = self._parse_messages(task.output_file, params.tail, params.max_tool_output_tokens)
        if messages:
            lines.append(f"最新 {len(messages)} 条消息:")
            lines.append("-" * 50)
            lines.extend(messages)
        else:
            lines.append("暂无输出")

        return ToolOk(output="\n".join(lines))

    def _parse_messages(self, output_file: Path, tail: int, max_tool_tokens: int = 200) -> list[str]:
        """解析日志文件，返回最新的若干条消息，仅截断 tool 角色的消息.

        Args:
            output_file: 日志文件路径
            tail: 返回最新的多少条
            max_tool_tokens: tool 角色消息最大保留多少字符，其他角色不截断

        Returns:
            格式化的消息列表（最新的消息在最后）
        """
        if not output_file.exists():
            return []

        try:
            import json
            content = output_file.read_text(encoding="utf-8")
            if not content.strip():
                return []
            
            lines = content.strip().split("\n")
            
            # 解析所有消息（跳过 _usage 和 _checkpoint 等内部标记）
            messages = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                    role = msg.get("role", "unknown")
                    
                    # 跳过内部标记行（context 格式）
                    if role in ("_usage", "_checkpoint"):
                        continue

                    # 提取文本内容
                    content_parts = msg.get("content", [])
                    if isinstance(content_parts, list):
                        texts = []
                        for part in content_parts:
                            if isinstance(part, dict) and "text" in part:
                                texts.append(part["text"])
                        content_text = " ".join(texts)
                    elif isinstance(content_parts, str):
                        content_text = content_parts
                    else:
                        content_text = str(content_parts)

                    # 跳过空内容的消息
                    if not content_text.strip():
                        continue

                    # 进度行（由 _background_ui_loop 实时写入）用可读前缀展示
                    if role == "_step":
                        messages.append(f"[⚙ {content_text}]")
                        continue
                    if role == "_progress":
                        messages.append(f"[💬 {content_text}]")
                        continue
                    if role == "_tool_progress":
                        if len(content_text) > max_tool_tokens:
                            content_text = content_text[:max_tool_tokens - 3] + "..."
                        messages.append(f"[🔧 {content_text}]")
                        continue
                    if role == "_turn":
                        # turn 开始/结束仅作分隔，不显示
                        continue

                    # 仅对 tool 角色的消息进行截断
                    # assistant 和 user 的消息保留完整（通常包含分析结论，更重要）
                    if role == "tool" and len(content_text) > max_tool_tokens:
                        content_text = content_text[:max_tool_tokens - 3] + "..."

                    messages.append(f"[{role}] {content_text}")
                except json.JSONDecodeError:
                    continue
            
            # 返回最新的 tail 条（messages 列表是按时间顺序的，最新的在最后）
            if len(messages) > tail:
                return messages[-tail:]
            return messages
            
        except Exception as e:
            return [f"解析日志文件失败: {e}"]

    async def _read_output_file(self, task_id: str, tail: int, max_tool_tokens: int = 200) -> ToolReturnValue:
        """尝试从日志文件读取已完成的任务输出.

        Args:
            task_id: 任务 ID
            tail: 返回最新的多少条
            max_tool_tokens: tool 角色消息最大保留多少字符

        Returns:
            ToolReturnValue 包含任务输出内容或错误信息
        """
        from pathlib import Path

        output_file = Path.home() / ".kimi" / "tasks" / f"{task_id}.log"

        if not output_file.exists():
            return ToolOk(output=f"任务 {task_id} 不存在或已过期")

        lines = [
            f"任务ID: {task_id}",
            f"状态: 已完成（已从任务管理器移除）",
            f"日志文件: {output_file}",
            "",
        ]

        # 读取并解析最新的消息（仅截断 tool 角色的消息）
        messages = self._parse_messages(output_file, tail, max_tool_tokens)
        if messages:
            lines.append(f"最新 {len(messages)} 条消息:")
            lines.append("-" * 50)
            lines.extend(messages)
        else:
            lines.append("暂无输出")

        return ToolOk(output="\n".join(lines))


class TaskStopParams(BaseModel):
    """TaskStop 工具参数."""

    task_id: str = Field(
        default="",
        description="任务 ID，如果为空则停止所有任务",
    )
    stop_all: bool = Field(
        default=False,
        description="是否停止所有后台任务",
    )


class TaskStop(CallableTool2[TaskStopParams]):
    """停止正在运行的后台任务."""

    name: str = "TaskStop"
    params: type[TaskStopParams] = TaskStopParams

    def __init__(self, runtime: Runtime):
        super().__init__(description="停止指定的后台任务或所有任务")
        self._session = runtime.session

    @typing_override
    async def __call__(self, params: TaskStopParams) -> ToolReturnValue:
        """停止任务.

        Args:
            params: 停止参数

        Returns:
            ToolReturnValue 包含停止结果
        """
        if params.stop_all:
            return await self._stop_all_tasks()
        elif params.task_id:
            return await self._stop_single_task(params.task_id)
        else:
            return ToolOk(output="请指定任务 ID 或设置 stop_all=True 停止所有任务")

    async def _stop_single_task(self, task_id: str) -> ToolReturnValue:
        """停止单个任务.

        Args:
            task_id: 任务 ID

        Returns:
            ToolReturnValue 包含停止结果
        """
        task = TaskManager().get_task(self._session.id, task_id)

        if not task:
            return ToolOk(output=f"任务 {task_id} 不存在或已结束")

        if task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
            return ToolOk(output=f"任务 {task_id} 状态为 {task.status.value}，无需停止")

        # 发送停止信号
        task.request_stop()

        # 等待任务结束（短超时）
        try:
            await asyncio.wait_for(task.wait(), timeout=5.0)
            return ToolOk(output=f"任务 {task_id} 已停止")
        except asyncio.TimeoutError:
            # 强制取消
            if task._task and not task._task.done():
                task._task.cancel()
            return ToolOk(output=f"任务 {task_id} 已强制停止")

    async def _stop_all_tasks(self) -> ToolReturnValue:
        """停止所有任务.

        Returns:
            ToolReturnValue 包含停止结果
        """
        tasks = TaskManager().list_tasks(self._session.id)

        if not tasks:
            return ToolOk(output="当前没有后台任务需要停止")

        # 只停止运行中或待处理的任务
        running_tasks = [t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.PENDING)]

        if not running_tasks:
            return ToolOk(output="没有正在运行的任务需要停止")

        stopped_count = 0
        for task in running_tasks:
            task.request_stop()
            stopped_count += 1

        # 等待所有任务结束（短超时）
        try:
            await asyncio.wait_for(
                asyncio.gather(*[t.wait() for t in running_tasks], return_exceptions=True),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            # 强制取消剩余任务
            for task in running_tasks:
                if task._task and not task._task.done():
                    task._task.cancel()

        task_ids = ", ".join([t.task_id for t in running_tasks])
        return ToolOk(output=f"已停止 {stopped_count} 个后台任务: {task_ids}")

