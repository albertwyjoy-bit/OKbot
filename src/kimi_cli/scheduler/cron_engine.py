"""Cron scheduling engine for scheduled tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Callable

from croniter import croniter
from loguru import logger

from kimi_cli.scheduler.models import ScheduledJob
from kimi_cli.scheduler.store import JobStore


class CronEngine:
    """Cron调度引擎
    
    支持标准 cron 表达式（5字段：分 时 日 月 周）
    支持秒级 cron 表达式（6字段：秒 分 时 日 月 周）
    """
    
    # 默认检查间隔（秒）
    DEFAULT_CHECK_INTERVAL = 30.0
    # 秒级任务检查间隔（秒）
    SECOND_LEVEL_CHECK_INTERVAL = 1.0
    
    def __init__(
        self,
        job_store: JobStore,
        on_trigger: Callable[[ScheduledJob], None],
        check_interval: float | None = None,  # None 表示自动检测
    ):
        """Initialize cron engine.
        
        Args:
            job_store: 任务存储
            on_trigger: 任务触发时的回调函数
            check_interval: 检查间隔（秒），None 表示自动根据任务类型调整
        """
        self._job_store = job_store
        self._on_trigger = on_trigger
        self._check_interval = check_interval or self.DEFAULT_CHECK_INTERVAL
        self._running = False
        self._task: asyncio.Task | None = None
        self._has_second_level_jobs = False  # 是否有秒级任务
    
    async def start(self) -> None:
        """Start the cron engine."""
        if self._running:
            logger.warning("Cron engine is already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Cron engine started")
    
    async def stop(self) -> None:
        """Stop the cron engine."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Cron engine stopped")
    
    async def _run_loop(self) -> None:
        """Main loop for checking and triggering jobs."""
        while self._running:
            try:
                await self._check_jobs()
            except Exception as e:
                logger.exception(f"Error checking jobs: {e}")
            
            try:
                await asyncio.wait_for(
                    asyncio.sleep(self._check_interval),
                    timeout=self._check_interval + 5.0  # 额外5秒容忍
                )
            except asyncio.TimeoutError:
                logger.warning("Sleep interrupted, continuing...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in sleep: {e}")
                await asyncio.sleep(1.0)  # 出错后短暂休息
    
    async def _check_jobs(self) -> None:
        """Check all active jobs and trigger due ones.
        
        使用 croniter.get_prev() 获取当前周期应该执行的时间，
        然后检查 last_run 是否在这个周期之前，如果是则执行任务。
        """
        now = datetime.now()
        jobs = await self._job_store.list_all()
        
        # 检测是否有秒级任务，调整检查间隔
        has_second_level = False
        
        for job in jobs:
            if not job.is_active:
                continue
            
            # 检测是否是秒级任务（6个字段）
            is_second_level = self._is_second_level_cron(job.cron)
            if is_second_level:
                has_second_level = True
            
            try:
                # 使用 croniter 计算时间
                # 注意：get_prev() 和 get_next() 会改变迭代器状态，需要分别创建实例
                itr_prev = croniter(job.cron, now, second_at_beginning=is_second_level)
                current_due_time = itr_prev.get_prev(datetime)
                
                itr_next = croniter(job.cron, now, second_at_beginning=is_second_level)
                next_due_time = itr_next.get_next(datetime)
                
                # 修复：如果 next_due_time 已经过或正好是当前时间，获取下一个周期
                # 保持与 _get_next_run 方法逻辑一致
                if is_second_level:
                    if next_due_time <= now:
                        next_due_time = itr_next.get_next(datetime)
                else:
                    # 分钟级任务：如果当前分钟 >= 触发分钟，算已过
                    now_minute = now.replace(second=0, microsecond=0)
                    next_minute = next_due_time.replace(second=0, microsecond=0)
                    if next_minute <= now_minute:
                        next_due_time = itr_next.get_next(datetime)
                
                # 更新下次执行时间（用于显示）
                job.next_run = next_due_time
                await self._job_store.save(job)
                
                # 检查是否应该执行：
                # 区分周期性任务和固定时间任务
                # 周期性任务（如 */5 * * * *）：使用 current_due_time 判断
                # 固定时间任务（如 0 9 * * *）：使用 today_due_time 判断
                is_periodic = self._is_periodic_cron(job.cron)
                
                # 计算 today_due_time（用于固定时间任务）
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                itr_today = croniter(job.cron, today_start, second_at_beginning=is_second_level)
                today_due_time = itr_today.get_next(datetime)
                
                should_execute = False
                
                if job.last_run is None:
                    # 新任务判断
                    if is_periodic:
                        # 周期性任务：使用 current_due_time 判断
                        if now >= current_due_time and job.created_at < current_due_time:
                            should_execute = True
                            logger.debug(f"Job {job.id} (periodic) current cycle started ({now} >= {current_due_time}), should execute")
                        elif now >= current_due_time and job.created_at >= current_due_time:
                            logger.debug(f"Job {job.id} (periodic) created in current cycle, waiting for next")
                        else:
                            logger.debug(f"Job {job.id} (periodic) waiting for current cycle ({now} < {current_due_time})")
                    else:
                        # 固定时间任务：使用 today_due_time 判断
                        if now >= today_due_time and job.created_at < today_due_time:
                            should_execute = True
                            logger.debug(f"Job {job.id} (fixed) today's due time reached and created before, should execute")
                        elif now >= today_due_time and job.created_at >= today_due_time:
                            logger.debug(f"Job {job.id} (fixed) created after today's due time, waiting for tomorrow")
                        else:
                            logger.debug(f"Job {job.id} (fixed) waiting for today's due time ({now} < {today_due_time})")
                elif job.last_run < current_due_time:
                    # 旧任务：统一使用 current_due_time 判断
                    should_execute = True
                    logger.debug(f"Job {job.id} last_run ({job.last_run}) < current_due_time ({current_due_time}), should execute")
                
                if should_execute:
                    # 再次检查，避免重复执行（双保险）
                    # 使用 <= 而不是 <，并添加 2 秒缓冲，防止边界情况下的重复执行
                    min_interval = timedelta(seconds=3) if is_second_level else timedelta(minutes=1, seconds=2)
                    if job.last_run is not None and (now - job.last_run) <= min_interval:
                        logger.debug(f"Job {job.id} executed too recently ({now - job.last_run}), skipping")
                        continue
                    
                    logger.info(f"Triggering scheduled job: {job.id} (due at {current_due_time})")
                    job.last_run = now
                    await self._job_store.save(job)
                    
                    # 触发回调
                    try:
                        self._on_trigger(job)
                    except Exception as e:
                        logger.exception(f"Error triggering job {job.id}: {e}")
            
            except Exception as e:
                logger.exception(f"Error checking job {job.id}: {e}")
        
        # 根据任务类型动态调整检查间隔
        if has_second_level and self._check_interval > self.SECOND_LEVEL_CHECK_INTERVAL:
            self._check_interval = self.SECOND_LEVEL_CHECK_INTERVAL
            logger.info("Detected second-level cron jobs, adjusted check interval to 1 second")
        elif not has_second_level and self._check_interval == self.SECOND_LEVEL_CHECK_INTERVAL:
            self._check_interval = self.DEFAULT_CHECK_INTERVAL
            logger.info("No second-level jobs, restored check interval to 30 seconds")
    
    def _is_second_level_cron(self, cron: str) -> bool:
        """检查是否是秒级 cron 表达式（6个字段）
        
        Args:
            cron: Cron 表达式
            
        Returns:
            是否是秒级表达式
        """
        # 移除首尾空格，按空格分割
        parts = cron.strip().split()
        # 秒级表达式有6个字段，标准表达式有5个字段
        return len(parts) == 6
    
    def _is_periodic_cron(self, cron: str) -> bool:
        """检查是否是周期性 cron 表达式（如 */5 * * * *）
        
        周期性任务指使用 */N、逗号分隔或范围的任务
        固定时间任务指使用具体数值的任务（如 0 9 * * *）
        
        Args:
            cron: Cron 表达式
            
        Returns:
            是否是周期性任务
        """
        parts = cron.strip().split()
        # 检查每个字段是否包含周期性特征
        for part in parts:
            if '*/' in part or ',' in part or '-' in part:
                return True
        return False
    
    def _get_next_run(self, job: ScheduledJob, now: datetime) -> datetime | None:
        """Get next run time for a job.
        
        Args:
            job: 定时任务
            now: 当前时间
            
        Returns:
            下次执行时间，如果无法计算则返回 None
        """
        try:
            # 根据字段数量判断是秒级还是分钟级
            is_second_level = self._is_second_level_cron(job.cron)
            
            # 使用 croniter 计算下次执行时间
            # second_at_beginning=True 表示将第1个字段解释为秒（6字段格式）
            itr = croniter(job.cron, now, second_at_beginning=is_second_level)
            next_run = itr.get_next(datetime)
            
            # 修复：如果 next_run 已经过或正好是当前时间，获取下一个周期
            # 这种情况发生在用户在触发时间之后创建任务时
            # 例如：用户在 17:26:30 创建 17:26 执行的任务，get_next 返回今天的 17:26
            # 但由于 17:26 已过，应该返回明天的 17:26
            if is_second_level:
                if next_run <= now:
                    next_run = itr.get_next(datetime)
            else:
                # 分钟级任务：如果当前分钟 >= 触发分钟，算已过
                now_minute = now.replace(second=0, microsecond=0)
                next_minute = next_run.replace(second=0, microsecond=0)
                if next_minute <= now_minute:
                    next_run = itr.get_next(datetime)
            
            return next_run
        except Exception as e:
            logger.error(f"Failed to parse cron expression '{job.cron}': {e}")
            return None
    
    def is_running(self) -> bool:
        """Check if the engine is running."""
        return self._running


def validate_cron(cron: str) -> tuple[bool, str]:
    """Validate a cron expression.
    
    Args:
        cron: Cron 表达式
        
    Returns:
        (是否有效, 错误信息或成功提示)
    """
    try:
        # 根据字段数量判断是秒级还是分钟级
        parts = cron.strip().split()
        
        # 验证字段数量：5个字段（标准）或6个字段（秒级）
        if len(parts) not in (5, 6):
            return False, f"无效的 Cron 表达式: 需要 5 或 6 个字段，实际有 {len(parts)} 个字段"
        
        is_second_level = len(parts) == 6
        
        # 尝试获取下次执行时间来验证
        now = datetime.now()
        itr = croniter(cron, now, second_at_beginning=is_second_level)
        next_run = itr.get_next(datetime)
        
        # 修复：如果 next_run 已经过或正好是当前时间，获取下一个周期
        # 对于分钟级任务，使用分钟精度比较；对于秒级任务，使用秒精度
        if is_second_level:
            if next_run <= now:
                next_run = itr.get_next(datetime)
        else:
            # 分钟级任务：如果当前分钟 >= 触发分钟，算已过
            now_minute = now.replace(second=0, microsecond=0)
            next_minute = next_run.replace(second=0, microsecond=0)
            if next_minute <= now_minute:
                next_run = itr.get_next(datetime)
        
        if is_second_level:
            # 秒级任务
            return True, f"有效（秒级），下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
        else:
            # 标准分钟级任务
            return True, f"有效，下次执行: {next_run.strftime('%Y-%m-%d %H:%M')}"
    except Exception as e:
        return False, f"无效的 Cron 表达式: {e}"


def get_next_runs(cron: str, count: int = 5) -> list[datetime]:
    """Get next N execution times for a cron expression.
    
    Args:
        cron: Cron 表达式
        count: 获取次数
        
    Returns:
        执行时间列表
    """
    try:
        now = datetime.now()
        # 根据字段数量判断是秒级还是分钟级
        parts = cron.strip().split()
        is_second_level = len(parts) == 6
        itr = croniter(cron, now, second_at_beginning=is_second_level)
        return [itr.get_next(datetime) for _ in range(count)]
    except Exception as e:
        logger.error(f"Failed to get next runs for '{cron}': {e}")
        return []
