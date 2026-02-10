"""智能定时任务工具 - 用自然语言创建和管理定时任务."""

from __future__ import annotations

from typing import Any

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools.utils import ToolResultBuilder


class CreateScheduledJobParams(BaseModel):
    """参数模型 - 创建定时任务."""

    natural_language_time: str = Field(
        description="用户的自然语言时间描述，如：每30分钟、每天上午9点、每周一早上8点、每小时"
    )
    cron_expression: str = Field(
        description="根据时间描述生成的标准 Cron 表达式。5字段格式：分 时 日 月 周（如 '*/30 * * * *'）；6字段格式：秒 分 时 日 月 周（如 '*/5 * * * * *'）"
    )
    task_description: str = Field(
        description="任务描述（用于管理和显示），例如：喝水提醒、日报生成",
    )
    reminder_text: str = Field(
        description="提醒内容（定时触发时直接发送给用户的消息）。如果不提供，则使用 task_description。例如：该喝水了！💧、该写日报了！",
        default="",
    )


class CreateScheduledJob(CallableTool2[CreateScheduledJobParams]):
    """创建定时任务 - 将用户的自然语言时间描述转换为 Cron 表达式并创建任务."""

    name: str = "CreateScheduledJob"
    params: type[CreateScheduledJobParams] = CreateScheduledJobParams
    description: str = (
        "Create a scheduled/recurring task from natural language time description. "
        "Use this tool when the user wants to schedule a recurring task like:\n"
        '- "每30分钟提醒我喝水" (remind me to drink water every 30 minutes)\n'
        '- "每天上午9点生成日报" (generate daily report at 9 AM every day)\n'
        '- "每周一早上8点发送周报" (send weekly report at 8 AM every Monday)\n'
        '- "每小时检查一次邮件" (check emails every hour)\n\n'
        "=== ⚠️ IMPORTANT: task_description vs reminder_text (MUTUALLY EXCLUSIVE) ===\n"
        "These two fields are MUTUALLY EXCLUSIVE - only ONE will take effect:\n\n"
        "1️⃣ If reminder_text is provided (not empty):\n"
        "   → The reminder_text will be sent DIRECTLY to user WITHOUT going through the agent\n"
        "   → Use for: simple reminders like '该喝水了！💧', '休息一下！☕'\n"
        "   → task_description is IGNORED when reminder_text is set\n\n"
        "2️⃣ If reminder_text is EMPTY (default):\n"
        "   → task_description will be sent to the AGENT for intelligent execution\n"
        "   → Use for: complex tasks that need agent processing\n\n"
        "=== 📝 How to fill task_description (when reminder_text is empty) ===\n"
        "The task_description will be passed to the agent at trigger time as: '[定时任务] {task_description}'\n\n"
        "Guidelines for task_description:\n"
        "- MUST include the specific action and any necessary context\n"
        "- Include file paths if the task involves specific files\n"
        "- Include URLs if the task involves web resources\n"
        "- Be specific about what output is expected\n\n"
        "Examples of GOOD task_description:\n"
        '- "Check ~/logs/app.log and summarize any errors from the last hour"\n'
        '- "Read https://api.example.com/status and report if any service is down"\n'
        '- "List all .py files in ~/project/src that were modified today and count lines of code"\n'
        '- "Generate a summary of the ~/data/sales.csv file and save the report to ~/reports/daily.md"\n'
        '- "Check my unread emails and list those from high-priority senders"\n\n'
        "Examples of BAD task_description (too vague):\n"
        '- "Check logs" → ❌ Which logs? Where? What to check for?\n'
        '- "Read file" → ❌ Which file? What to do with it?\n'
        '- "Generate report" → ❌ About what? Where to save?\n\n'
        "Cron format guide:\n"
        "- 5 fields: minute hour day month weekday (e.g., '*/30 * * * *' = every 30 minutes)\n"
        "- 6 fields: second minute hour day month weekday (e.g., '*/5 * * * * *' = every 5 seconds)\n\n"
        "Common patterns:\n"
        "- Every N minutes: '*/N * * * *'\n"
        "- Every day at H:MM: 'MM H * * *'\n"
        "- Every Monday at H:MM: 'MM H * * 1'\n"
        "- Workdays at 9:00: '0 9 * * 1-5'\n\n"
        "The task will be bound to the current chat and automatically executed at scheduled times."
    )

    async def __call__(self, params: CreateScheduledJobParams) -> ToolReturnValue:
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"[CreateScheduledJob] Called with: time='{params.natural_language_time}', cron='{params.cron_expression}', task='{params.task_description}'")
        print(f"[CreateScheduledJob] Called with: time='{params.natural_language_time}', cron='{params.cron_expression}'")
        
        builder = ToolResultBuilder()

        try:
            # 导入 scheduler
            from kimi_cli.scheduler.scheduler import get_scheduler
            from kimi_cli.scheduler.cron_engine import validate_cron
            from kimi_cli.tools.feishu import get_feishu_client

            # 验证 cron 表达式
            is_valid, error_msg = validate_cron(params.cron_expression)
            if not is_valid:
                logger.warning(f"[CreateScheduledJob] Invalid cron: {error_msg}")
                return builder.error(
                    f"Cron 表达式无效: {error_msg}",
                    brief="时间格式错误",
                )

            scheduler = get_scheduler()
            logger.info(f"[CreateScheduledJob] Scheduler initialized: {scheduler._initialized}, handler: {scheduler._feishu_handler}")

            # 检查调度器是否已初始化
            if not scheduler._initialized or not scheduler._feishu_handler:
                logger.warning(f"[CreateScheduledJob] Scheduler not ready: initialized={scheduler._initialized}, handler={scheduler._feishu_handler}")
                return builder.error(
                    "定时任务调度器未初始化，请稍后重试",
                    brief="调度器未就绪",
                )

            # 获取当前会话信息 - 尝试多个来源
            chat_id = None
            user_id = None
            
            # 尝试 1: 从 feishu client 获取
            client = get_feishu_client()
            logger.info(f"[CreateScheduledJob] Feishu client from global: {client}")
            
            if client is not None:
                chat_id = getattr(client, 'current_chat_id', None)
                logger.info(f"[CreateScheduledJob] Got chat_id from client: {chat_id}")
            else:
                logger.warning("[CreateScheduledJob] get_feishu_client() returned None, trying alternative methods")
            
            # 尝试 2: 从 scheduler handler 获取（如果 handler 有 sessions）
            if scheduler._feishu_handler:
                handler = scheduler._feishu_handler
                logger.info(f"[CreateScheduledJob] Handler type: {type(handler)}")
                
                # 尝试获取 handler 中的 sessions
                if hasattr(handler, '_sessions') and handler._sessions:
                    sessions = handler._sessions
                    logger.info(f"[CreateScheduledJob] Handler has {len(sessions)} sessions")
                    
                    # 获取第一个会话
                    if sessions:
                        first_session = list(sessions.values())[0]
                        # 如果还没有 chat_id 或 user_id，从 session 获取
                        if not chat_id:
                            chat_id = getattr(first_session, 'chat_id', None)
                        if not user_id:
                            user_id = getattr(first_session, 'user_id', None)
                        logger.info(f"[CreateScheduledJob] Got info from first session: chat_id={chat_id}, user_id={user_id}")
            
            # 尝试 3: 如果 handler 有 user_id 属性（直接设置时）
            if not user_id and scheduler._feishu_handler:
                user_id = getattr(scheduler._feishu_handler, 'user_id', None)
                chat_id = getattr(scheduler._feishu_handler, 'chat_id', chat_id)
                logger.info(f"[CreateScheduledJob] Got info from handler: chat_id={chat_id}, user_id={user_id}")
            
            logger.info(f"[CreateScheduledJob] Final: chat_id={chat_id}, user_id={user_id}")

            if not chat_id or not user_id:
                logger.warning(f"[CreateScheduledJob] Missing chat_id or user_id: chat_id={chat_id}, user_id={user_id}")
                return builder.error(
                    "无法获取当前会话信息，请在飞书聊天中使用",
                    brief="会话信息缺失",
                )

            # 创建任务
            reminder_text = params.reminder_text if params.reminder_text else None
            
            # 检查两者是否都填充了
            has_task_description = params.task_description and params.task_description.strip()
            has_reminder_text = reminder_text and reminder_text.strip()
            both_filled = has_task_description and has_reminder_text
            
            success, message, job = await scheduler.add_job(
                cron=params.cron_expression,
                description=params.task_description,
                user_id=user_id,
                chat_id=chat_id,
                chat_type="p2p",
                reminder_text=reminder_text,
            )

            if success and job:
                builder.write(f"✅ 定时任务创建成功！\n\n")
                builder.write(f"📋 任务信息:\n")
                builder.write(f"  • 任务ID: {job.id}\n")
                builder.write(f"  • 自然语言: {params.natural_language_time}\n")
                builder.write(f"  • Cron表达式: {params.cron_expression}\n")
                
                # 显示互斥警告
                if both_filled:
                    builder.write(f"\n⚠️ 注意：reminder_text 和 task_description 同时提供了，但只有 reminder_text 会生效\n")
                    builder.write(f"  • 任务描述 (被忽略): {params.task_description}\n")
                    builder.write(f"  • 提醒内容 (生效): {reminder_text}\n")
                    builder.write(f"  → 定时触发时将直接发送提醒内容，不会执行智能任务\n\n")
                elif reminder_text:
                    builder.write(f"  • 提醒内容: {reminder_text}\n")
                else:
                    builder.write(f"  • 任务描述: {params.task_description}\n")
                    builder.write(f"  → 定时触发时将调用 Agent 执行此任务\n")
                    
                if job.next_run:
                    builder.write(f"  • 下次执行: {job.next_run.strftime('%Y-%m-%d %H:%M:%S')}\n")
                builder.write(f"\n💡 管理命令:\n")
                builder.write(f"  • 查看所有任务: 使用 ListScheduledJobs 工具\n")
                builder.write(f"  • 暂停/启用: 使用 ToggleScheduledJob 工具\n")
                builder.write(f"  • 删除任务: 使用 DeleteScheduledJob 工具\n")

                brief_msg = f"已创建: {params.natural_language_time}"
                if both_filled:
                    brief_msg += " (注意: reminder_text优先)"
                
                return builder.ok(
                    message=f"定时任务 {job.id} 创建成功",
                    brief=brief_msg,
                )
            else:
                return builder.error(
                    f"创建任务失败: {message}",
                    brief="创建失败",
                )

        except Exception as e:
            logger.exception(f"[CreateScheduledJob] Error: {e}")
            return builder.error(
                f"创建定时任务时出错: {str(e)}",
                brief=f"错误: {str(e)[:50]}",
            )


class ListScheduledJobsParams(BaseModel):
    """参数模型 - 列出定时任务."""
    pass  # 不需要参数，自动获取当前会话的任务


class ListScheduledJobs(CallableTool2[ListScheduledJobsParams]):
    """列出当前对话的所有定时任务."""

    name: str = "ListScheduledJobs"
    params: type[ListScheduledJobsParams] = ListScheduledJobsParams
    description: str = (
        "List all scheduled/recurring tasks for the current chat. "
        "Use this tool when the user asks about their scheduled tasks, such as:\n"
        '- "查看我的定时任务" (show my scheduled tasks)\n'
        '- "列出所有定时任务" (list all scheduled tasks)\n'
        '- "我设置了哪些提醒" (what reminders did I set)\n'
        '- "显示任务列表" (show task list)\n\n'
        "Returns: job ID, cron schedule, description, active status, and next run time. "
        "The job IDs returned can be used with DeleteScheduledJob or ToggleScheduledJob."
    )

    async def __call__(self, params: ListScheduledJobsParams) -> ToolReturnValue:
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info("[ListScheduledJobs] Called")
        print("[ListScheduledJobs] Called")
        
        builder = ToolResultBuilder()

        try:
            from kimi_cli.scheduler.scheduler import get_scheduler
            from kimi_cli.tools.feishu import get_feishu_client

            scheduler = get_scheduler()

            if not scheduler._initialized:
                return builder.error(
                    "定时任务调度器未初始化",
                    brief="调度器未就绪",
                )

            client = get_feishu_client()
            if client is None:
                return builder.error(
                    "无法获取当前会话信息",
                    brief="会话信息缺失",
                )

            chat_id = getattr(client, 'current_chat_id', None)
            if not chat_id:
                return builder.error(
                    "无法获取当前会话信息",
                    brief="会话信息缺失",
                )

            jobs = await scheduler.list_jobs(chat_id=chat_id)

            if not jobs:
                builder.write("📭 当前对话没有定时任务\n")
                builder.write("使用 CreateScheduledJob 工具创建新任务\n")
                return builder.ok(
                    message="没有定时任务",
                    brief="无定时任务",
                )

            builder.write(f"📋 当前对话共有 {len(jobs)} 个定时任务:\n\n")

            for i, job in enumerate(jobs, 1):
                status = "🟢 运行中" if job.is_active else "🔴 已暂停"
                builder.write(f"{i}. {status}\n")
                builder.write(f"   ID: {job.id}\n")
                builder.write(f"   时间: {job.cron}\n")
                builder.write(f"   描述: {job.description}\n")
                if job.reminder_text:
                    builder.write(f"   提醒: {job.reminder_text}\n")
                if job.next_run:
                    builder.write(f"   下次: {job.next_run.strftime('%Y-%m-%d %H:%M:%S')}\n")
                builder.write(f"\n")

            builder.write("💡 管理操作:\n")
            builder.write("  • 删除任务: 使用 DeleteScheduledJob 工具，传入任务ID\n")
            builder.write("  • 暂停/启用: 使用 ToggleScheduledJob 工具，传入任务ID\n")

            return builder.ok(
                message=f"共有 {len(jobs)} 个定时任务",
                brief=f"{len(jobs)} 个任务",
            )

        except Exception as e:
            return builder.error(
                f"列出任务时出错: {str(e)}",
                brief=f"错误: {str(e)[:50]}",
            )


class DeleteScheduledJobParams(BaseModel):
    """参数模型 - 删除定时任务."""

    job_id: str = Field(
        description="要删除的任务ID（从 ListScheduledJobs 工具获取）",
    )


class DeleteScheduledJob(CallableTool2[DeleteScheduledJobParams]):
    """删除指定的定时任务."""

    name: str = "DeleteScheduledJob"
    params: type[DeleteScheduledJobParams] = DeleteScheduledJobParams
    description: str = (
        "Delete/remove a scheduled task by its ID. "
        "Use this tool when the user wants to cancel or delete a scheduled task, such as:\n"
        '- "删除喝水提醒任务" (delete the water reminder task)\n'
        '- "取消定时任务 job_xxx" (cancel scheduled task job_xxx)\n'
        '- "移除这个提醒" (remove this reminder)\n\n'
        "You must first use ListScheduledJobs to find the job ID before deleting. "
        "Example: job_id='job_abc123xyz'"
    )

    async def __call__(self, params: DeleteScheduledJobParams) -> ToolReturnValue:
        builder = ToolResultBuilder()

        try:
            from kimi_cli.scheduler.scheduler import get_scheduler

            scheduler = get_scheduler()

            if not scheduler._initialized:
                return builder.error(
                    "定时任务调度器未初始化",
                    brief="调度器未就绪",
                )

            success, message = await scheduler.remove_job(params.job_id)

            if success:
                builder.write(f"✅ 任务已删除\n")
                builder.write(f"ID: {params.job_id}\n")
                return builder.ok(
                    message=message,
                    brief="删除成功",
                )
            else:
                return builder.error(
                    message,
                    brief="删除失败",
                )

        except Exception as e:
            return builder.error(
                f"删除任务时出错: {str(e)}",
                brief=f"错误: {str(e)[:50]}",
            )


class ToggleScheduledJobParams(BaseModel):
    """参数模型 - 切换定时任务状态."""

    job_id: str = Field(
        description="要暂停/启用的任务ID（从 ListScheduledJobs 工具获取）",
    )


class ToggleScheduledJob(CallableTool2[ToggleScheduledJobParams]):
    """暂停或启用指定的定时任务."""

    name: str = "ToggleScheduledJob"
    params: type[ToggleScheduledJobParams] = ToggleScheduledJobParams
    description: str = (
        "Toggle (pause or resume) a scheduled task by its ID. "
        "Use this tool when the user wants to pause or reactivate a scheduled task, such as:\n"
        '- "暂停喝水提醒" (pause the water reminder)\n'
        '- "启用任务 job_xxx" (enable task job_xxx)\n'
        '- "暂停这个定时任务" (pause this scheduled task)\n\n'
        "If the job is active, it will be paused. If paused, it will be reactivated. "
        "You must first use ListScheduledJobs to find the job ID before toggling. "
        "Example: job_id='job_abc123xyz'"
    )

    async def __call__(self, params: ToggleScheduledJobParams) -> ToolReturnValue:
        builder = ToolResultBuilder()

        try:
            from kimi_cli.scheduler.scheduler import get_scheduler

            scheduler = get_scheduler()

            if not scheduler._initialized:
                return builder.error(
                    "定时任务调度器未初始化",
                    brief="调度器未就绪",
                )

            success, message = await scheduler.toggle_job(params.job_id)

            if success:
                builder.write(f"✅ 任务状态已切换\n")
                builder.write(f"ID: {params.job_id}\n")
                builder.write(f"状态: {message}\n")
                return builder.ok(
                    message=message,
                    brief="状态已切换",
                )
            else:
                return builder.error(
                    message,
                    brief="切换失败",
                )

        except Exception as e:
            return builder.error(
                f"切换任务状态时出错: {str(e)}",
                brief=f"错误: {str(e)[:50]}",
            )
