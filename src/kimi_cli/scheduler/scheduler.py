"""Main scheduler for OKbot."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from kimi_cli.scheduler.cron_engine import CronEngine, validate_cron
from kimi_cli.scheduler.dispatcher import MessageDispatcher, SessionManager
from kimi_cli.scheduler.models import IncomingMessage, NotificationMode, ScheduledJob
from kimi_cli.scheduler.store import JobStore, PendingResultStore


class Scheduler:
    """定时任务调度器"""
    
    def __init__(
        self,
        feishu_handler: Any | None = None,
        storage_dir: str | None = None,
    ):
        """Initialize scheduler.
        
        Args:
            feishu_handler: 飞书消息处理器
            storage_dir: 存储目录路径
        """
        self._job_store = JobStore(storage_dir)
        self._executing_jobs: set[str] = set()  # 全局集合，跟踪正在执行的job
        self._execution_lock = asyncio.Lock()  # 保护 _executing_jobs 的锁
        self._pending_store = PendingResultStore(storage_dir)
        self._feishu_handler = feishu_handler
        
        self._session_manager: SessionManager | None = None
        self._dispatcher: MessageDispatcher | None = None
        self._cron_engine: CronEngine | None = None
        
        self._initialized = False
    
    async def initialize(self, feishu_handler: Any | None = None) -> None:
        """Initialize scheduler with feishu handler.
        
        Args:
            feishu_handler: 飞书消息处理器
        """
        if self._initialized:
            return
        
        if feishu_handler:
            self._feishu_handler = feishu_handler
        
        if not self._feishu_handler:
            logger.warning("Scheduler initialized without feishu handler")
        
        # 加载所有任务
        await self._job_store.load_all()
        
        # 创建会话管理器
        if self._feishu_handler:
            self._session_manager = SessionManager(
                feishu_handler=self._feishu_handler,
                pending_store=self._pending_store,
            )
            
            # 创建消息分发器
            self._dispatcher = MessageDispatcher(
                scheduler=self,
                session_manager=self._session_manager,
            )
            
            # 恢复等待队列
            await self._session_manager.restore_pending_notifications()
        
        # 创建 Cron 引擎
        self._cron_engine = CronEngine(
            job_store=self._job_store,
            on_trigger=self._on_job_trigger,
            check_interval=30.0,
        )
        
        self._initialized = True
        logger.info("Scheduler initialized")
    
    async def start(self) -> None:
        """Start the scheduler."""
        if not self._initialized:
            raise RuntimeError("Scheduler not initialized. Call initialize() first.")
        
        if self._cron_engine:
            await self._cron_engine.start()
        
        logger.info("Scheduler started")
    
    async def stop(self) -> None:
        """Stop the scheduler."""
        if self._cron_engine:
            await self._cron_engine.stop()
        
        logger.info("Scheduler stopped")
    
    async def add_job(
        self,
        cron: str,
        description: str,
        user_id: str,
        chat_id: str,
        chat_type: str = "p2p",
        tenant_key: str | None = None,
        notification_mode: str = "silent",
        reminder_text: str | None = None,
    ) -> tuple[bool, str, ScheduledJob | None]:
        """添加定时任务
        
        Args:
            cron: Cron 表达式
            description: 任务描述
            user_id: 创建者用户ID
            chat_id: 飞书对话ID
            chat_type: 对话类型 (p2p 或 group)
            tenant_key: 飞书租户标识
            notification_mode: 通知模式 (silent/normal/verbose)
            reminder_text: 提醒内容（直接发送给用户的消息）
            
        Returns:
            (是否成功, 消息, 任务对象)
        """
        # 验证 cron 表达式
        valid, message = validate_cron(cron)
        if not valid:
            return False, message, None
        
        # 生成任务ID
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        
        # 计算下次执行时间
        now = datetime.now()
        from croniter import croniter
        is_second_level = len(cron.split()) == 6
        itr = croniter(cron, now, second_at_beginning=is_second_level)
        next_run = itr.get_next(datetime)
        # 如果 next_run 已经过，获取下一个周期
        if is_second_level:
            if next_run <= now:
                next_run = itr.get_next(datetime)
        else:
            now_minute = now.replace(second=0, microsecond=0)
            next_minute = next_run.replace(second=0, microsecond=0)
            if next_minute <= now_minute:
                next_run = itr.get_next(datetime)
        
        # 创建任务
        job = ScheduledJob(
            id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            chat_type=chat_type,  # type: ignore
            tenant_key=tenant_key,
            cron=cron,
            description=description,
            reminder_text=reminder_text,
            notification_mode=notification_mode,
            created_at=now,
            is_active=True,
            next_run=next_run,
        )
        
        # 保存任务
        await self._job_store.save(job)
        
        logger.info(f"Added scheduled job: {job_id} - {description}")
        return True, f"任务已创建: {job_id}\n下次执行: {message}", job
    
    async def remove_job(self, job_id: str) -> tuple[bool, str]:
        """删除定时任务
        
        Args:
            job_id: 任务ID
            
        Returns:
            (是否成功, 消息)
        """
        job = await self._job_store.get(job_id)
        if not job:
            return False, f"任务不存在: {job_id}"
        
        success = await self._job_store.delete(job_id)
        if success:
            logger.info(f"Removed scheduled job: {job_id}")
            return True, f"任务已删除: {job_id}"
        else:
            return False, f"删除任务失败: {job_id}"
    
    async def get_job(self, job_id: str) -> ScheduledJob | None:
        """获取任务信息
        
        Args:
            job_id: 任务ID
            
        Returns:
            任务对象或 None
        """
        return await self._job_store.get(job_id)
    
    async def list_jobs(
        self,
        chat_id: str | None = None,
        user_id: str | None = None,
    ) -> list[ScheduledJob]:
        """列出定时任务
        
        Args:
            chat_id: 过滤特定对话的任务
            user_id: 过滤特定用户的任务
            
        Returns:
            任务列表
        """
        if chat_id:
            return await self._job_store.list_by_chat(chat_id)
        elif user_id:
            return await self._job_store.list_by_user(user_id)
        else:
            return await self._job_store.list_all()
    
    async def toggle_job(self, job_id: str) -> tuple[bool, str]:
        """切换任务激活状态
        
        Args:
            job_id: 任务ID
            
        Returns:
            (是否成功, 消息)
        """
        job = await self._job_store.get(job_id)
        if not job:
            return False, f"任务不存在: {job_id}"
        
        job.is_active = not job.is_active
        await self._job_store.save(job)
        
        status = "激活" if job.is_active else "暂停"
        logger.info(f"Toggled job {job_id} to {status}")
        return True, f"任务已{status}: {job_id}"
    
    async def _on_job_trigger_async(self, job: ScheduledJob) -> None:
        """异步版本的定时任务触发处理"""
        async with self._execution_lock:
            if job.id in self._executing_jobs:
                logger.warning(f"Job {job.id} is already being executed, skipping duplicate trigger")
                return
            # 标记为执行中
            self._executing_jobs.add(job.id)
        
        try:
            logger.info(f"Job triggered: {job.id}")
            
            # 如果设置了 reminder_text，直接发送提醒消息，不经过 Agent
            if job.reminder_text:
                logger.info(f"Sending reminder text directly for job {job.id}")
                await self._send_reminder_directly(job)
                return
            
            if not self._dispatcher:
                logger.error("Dispatcher not initialized, cannot trigger job")
                return
            
            # 构造 Mock 消息（用于 Agent 执行）
            mock_message = IncomingMessage(
                text=f"[定时任务] {job.description}",
                source="scheduled",
                source_id=job.id,
                chat_id=job.chat_id,
                user_id=job.user_id,
                chat_type=job.chat_type,  # type: ignore
                tenant_key=job.tenant_key,
                metadata={
                    "cron": job.cron,
                    "created_at": job.created_at.isoformat(),
                    "notification_mode": job.notification_mode,
                },
                created_at=datetime.now(),
            )
            
            # 分发消息
            await self._dispatch_message(mock_message)
        finally:
            # 移除执行标记
            async with self._execution_lock:
                self._executing_jobs.discard(job.id)
    
    def _on_job_trigger(self, job: ScheduledJob) -> None:
        """定时任务触发时的回调（同步包装）
        
        Args:
            job: 触发的任务
        """
        try:
            loop = asyncio.get_running_loop()
            # 使用 create_task 而不是 run_coroutine_threadsafe，确保在同一线程执行
            loop.create_task(self._on_job_trigger_async(job))
        except RuntimeError:
            logger.error("No running event loop to dispatch scheduled message")
    
    async def _send_reminder_directly(self, job: ScheduledJob) -> None:
        """直接发送提醒消息给用户（不经过 Agent）
        
        Args:
            job: 定时任务
        """
        try:
            if self._feishu_handler and hasattr(self._feishu_handler, 'client'):
                client = self._feishu_handler.client
                reminder = job.reminder_text or job.description
                
                # 构建提醒卡片
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "⏰ 定时提醒"
                        },
                        "template": "blue"
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": reminder
                            }
                        },
                        {
                            "tag": "note",
                            "elements": [
                                {
                                    "tag": "plain_text",
                                    "content": f"任务: {job.description}"
                                }
                            ]
                        }
                    ]
                }
                
                # 发送卡片消息
                import asyncio
                await asyncio.to_thread(
                    client.send_interactive_card,
                    job.chat_id,
                    card
                )
                logger.info(f"Reminder sent directly to {job.chat_id}: {reminder[:50]}")
            else:
                logger.error("Feishu client not available to send reminder")
        except Exception as e:
            logger.exception(f"Failed to send reminder directly: {e}")
    
    async def _dispatch_message(self, message: IncomingMessage) -> None:
        """分发消息（异步包装）
        
        Args:
            message: 消息对象
        """
        try:
            if self._dispatcher:
                await self._dispatcher.dispatch(message)
            else:
                logger.error("Dispatcher not available")
        except Exception as e:
            logger.exception(f"Error dispatching message: {e}")
    
    async def dispatch_feishu_message(self, feishu_message: Any) -> bool:
        """分发飞书消息
        
        这是 SDKMessageHandler 处理飞书消息的入口，将飞书消息
        转换为 IncomingMessage 后统一分发处理。
        
        Args:
            feishu_message: 飞书消息事件 (P2ImMessageReceiveV1)
            
        Returns:
            是否成功处理
        """
        if not self._initialized or not self._dispatcher:
            logger.error("Scheduler not initialized, cannot dispatch Feishu message")
            return False
        
        try:
            # 从飞书消息创建 IncomingMessage
            message = IncomingMessage.from_feishu_message(feishu_message)
            if not message:
                logger.error("Failed to create IncomingMessage from Feishu message")
                return False
            
            # 分发消息
            await self._dispatcher.dispatch(message)
            return True
            
        except Exception as e:
            logger.exception(f"Error dispatching Feishu message: {e}")
            return False
    
    async def trigger_job_now(self, job_id: str) -> tuple[bool, str]:
        """立即触发一个任务（用于测试）
        
        Args:
            job_id: 任务ID
            
        Returns:
            (是否成功, 消息)
        """
        job = await self._job_store.get(job_id)
        if not job:
            return False, f"任务不存在: {job_id}"
        
        # 在后台触发任务
        asyncio.create_task(asyncio.to_thread(self._on_job_trigger, job))
        
        return True, f"任务 {job_id} 已触发"
    
    # 命令处理辅助方法
    
    async def handle_cron_add_command(
        self,
        cron: str,
        description: str,
        user_id: str,
        chat_id: str,
        chat_type: str = "p2p",
        tenant_key: str | None = None,
    ) -> str:
        """处理 /cron add 命令
        
        Args:
            cron: Cron 表达式
            description: 任务描述
            user_id: 用户ID
            chat_id: 对话ID
            chat_type: 对话类型
            tenant_key: 租户标识
            
        Returns:
            回复消息
        """
        success, message, job = await self.add_job(
            cron=cron,
            description=description,
            user_id=user_id,
            chat_id=chat_id,
            chat_type=chat_type,
            tenant_key=tenant_key,
        )
        
        if success:
            return (
                f"✅ {message}\n\n"
                f"任务详情:\n"
                f"- ID: `{job.id if job else 'N/A'}`\n"
                f"- 描述: {description}\n"
                f"- Cron: `{cron}`\n"
                f"- 通知模式: 静默"
            )
        else:
            return f"❌ 创建任务失败: {message}"
    
    async def handle_cron_list_command(
        self,
        chat_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """处理 /cron list 命令
        
        Args:
            chat_id: 对话ID
            user_id: 用户ID
            
        Returns:
            回复消息
        """
        jobs = await self.list_jobs(chat_id=chat_id, user_id=user_id)
        
        if not jobs:
            return "📭 暂无定时任务\n\n使用 `/cron add \"cron表达式\" \"任务描述\"` 创建任务"
        
        lines = [f"📋 定时任务列表 ({len(jobs)} 个)"]
        lines.append("")
        
        for i, job in enumerate(jobs, 1):
            status = "🟢" if job.is_active else "🔴"
            lines.append(f"{i}. {status} `{job.id}`")
            lines.append(f"   描述: {job.description}")
            lines.append(f"   Cron: `{job.cron}`")
            if job.next_run:
                lines.append(f"   下次: {job.next_run.strftime('%Y-%m-%d %H:%M')}")
            lines.append("")
        
        lines.append("💡 提示: 使用 `/cron remove <id>` 删除任务")
        
        return "\n".join(lines)
    
    async def handle_cron_remove_command(self, job_id: str) -> str:
        """处理 /cron remove 命令
        
        Args:
            job_id: 任务ID
            
        Returns:
            回复消息
        """
        success, message = await self.remove_job(job_id)
        
        if success:
            return f"✅ {message}"
        else:
            return f"❌ {message}"
    
    async def handle_cron_history_command(
        self,
        chat_id: str,
        user_id: str,
        job_id: str | None = None,
    ) -> str:
        """处理 /cron history 命令
        
        Args:
            chat_id: 对话ID
            user_id: 用户ID
            job_id: 可选的任务ID过滤
            
        Returns:
            回复消息
        """
        from kimi_cli.scheduler.history import JobHistoryStore
        
        try:
            history_store = JobHistoryStore()
            records = await history_store.get_recent_records(
                chat_id=chat_id,
                limit=10,
                job_id=job_id,
            )
            
            if not records:
                if job_id:
                    return f"📭 任务 `{job_id}` 暂无执行记录"
                else:
                    return "📭 暂无执行记录\n\n任务执行后，历史记录会在这里显示"
            
            lines = []
            if job_id:
                lines.append(f"📜 任务 `{job_id}` 执行历史 ({len(records)} 条)")
            else:
                lines.append(f"📜 最近执行历史 ({len(records)} 条)")
            lines.append("")
            
            for i, record in enumerate(records, 1):
                lines.append(record.format_summary())
                lines.append("")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.exception(f"Failed to get history: {e}")
            return f"❌ 获取历史记录失败: {e}"
    
    async def handle_cron_help_command(self) -> str:
        """处理 /cron help 命令
        
        Returns:
            帮助消息
        """
        return """🕐 **定时任务帮助**

**命令列表:**
• `/cron add "表达式" "描述"` - 创建定时任务
• `/cron list` - 列出所有任务
• `/cron remove <id>` - 删除任务
• `/cron toggle <id>` - 切换任务开关
• `/cron history [任务ID]` - 查看执行历史
• `/cron help` - 显示帮助

**标准 Cron 表达式（5字段，分钟级）:**
格式: `分 时 日 月 周`
• `0 9 * * *` - 每天上午9点00分
• `0 9 * * 1` - 每周一上午9点00分
• `0 9 1 * *` - 每月1日上午9点00分
• `*/30 * * * *` - 每30分钟

**秒级 Cron 表达式（6字段）:**
格式: `秒 分 时 日 月 周`（注意：第1个字段是秒！）

常用示例：
• `*/5 * * * * *` - 每5秒执行
• `0 * * * * *` - 每分钟的第0秒执行（即每分钟一次）
• `30 * * * * *` - 每分钟的第30秒执行
• `0 0 * * * *` - 每小时的第0分0秒执行
• `0 0 9 * * 1` - 每周一上午9:00:00执行

⚠️ **重要：6字段的第1个是秒，不是分！**
- `12 0 9 * * 1` = 每周一 9:00:12（12秒 0分 9时）
- `0 12 9 * * 1` = 每周一 9:12:00（0秒 12分 9时）

**字段说明:**
| 字段 | 范围 | 说明 |
|------|------|------|
| 秒 | 0-59 | 仅6字段格式使用 |
| 分 | 0-59 | 分钟 |
| 时 | 0-23 | 小时（24小时制）|
| 日 | 1-31 | 日期 |
| 月 | 1-12 | 月份 |
| 周 | 0-6 | 星期（0=周日，1=周一）|

**注意事项:**
• 秒级任务会占用更多系统资源，建议使用5秒以上间隔
• 避免使用 `* * * * * *`（每秒执行），可能导致系统过载
• 创建后会显示下次执行时间，请确认是否符合预期

**查看任务结果:**
• 任务执行后会通过卡片/消息发送结果到当前对话
• 使用 `/cron history` 查看最近10条执行记录
• 使用 `/cron history <任务ID>` 查看指定任务的历史
• 引用任务结果卡片提问，我可以根据卡片内容回复

**文件生成任务:**
• 如果任务生成了文件（如报告、数据等），系统会自动检测文件路径
• 文件会自动上传到飞书并随卡片一起发送
• 引用卡片时，文本文件（.md/.txt/.json/.csv）的内容会被自动读取
• 引用时可直接针对文件内容提问，如"分析一下报告中的数据趋势"

**示例任务:**
```
/cron add "0 9 * * *" "生成昨日销售报告，包含数据分析和图表"
```
"""


# 全局调度器实例
_scheduler_instance: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """获取全局调度器实例"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = Scheduler()
    return _scheduler_instance


def set_scheduler(scheduler: Scheduler) -> None:
    """设置全局调度器实例"""
    global _scheduler_instance
    _scheduler_instance = scheduler
