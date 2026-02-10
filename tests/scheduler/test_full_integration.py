"""定时任务完整集成测试

测试所有核心功能的集成:
1. Cron 引擎的启停
2. 任务触发机制
3. 消息分发
4. 持久化存储
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from kimi_cli.scheduler import (
    Scheduler,
    get_scheduler,
    set_scheduler,
    validate_cron,
    get_next_runs,
)
from kimi_cli.scheduler.cron_engine import CronEngine
from kimi_cli.scheduler.models import (
    IncomingMessage,
    PendingNotification,
    ScheduledJob,
    ScheduledResult,
)
from kimi_cli.scheduler.store import JobStore, PendingResultStore


@pytest.fixture
def temp_dir():
    """创建临时目录"""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
async def scheduler(temp_dir):
    """创建并初始化调度器"""
    sched = Scheduler(storage_dir=temp_dir)
    await sched.initialize()
    yield sched
    await sched.stop()


class TestCronValidation:
    """测试 Cron 表达式验证"""

    def test_standard_cron(self):
        """测试标准 5 字段 Cron"""
        valid, msg = validate_cron("0 9 * * *")
        assert valid is True
        assert "下次执行" in msg

    def test_second_level_cron(self):
        """测试秒级 6 字段 Cron"""
        valid, msg = validate_cron("*/5 * * * * *")
        assert valid is True
        assert "秒级" in msg

    def test_invalid_cron(self):
        """测试无效 Cron"""
        valid, msg = validate_cron("invalid")
        assert valid is False
        assert "无效" in msg

    def test_invalid_field_count(self):
        """测试字段数量错误"""
        valid, msg = validate_cron("* * *")  # 只有3个字段
        assert valid is False
        assert "5 或 6 个字段" in msg


class TestNextRuns:
    """测试获取下次执行时间"""

    def test_daily_cron(self):
        """测试每日执行"""
        runs = get_next_runs("0 9 * * *", count=3)
        assert len(runs) == 3
        for run in runs:
            assert run.hour == 9
            assert run.minute == 0

    def test_second_level_cron(self):
        """测试秒级执行"""
        runs = get_next_runs("*/10 * * * * *", count=3)
        assert len(runs) == 3
        for run in runs:
            assert run.second % 10 == 0

    def test_invalid_cron_returns_empty(self):
        """测试无效 Cron 返回空列表"""
        runs = get_next_runs("invalid", count=3)
        assert runs == []


class TestJobStore:
    """测试任务存储"""

    @pytest.fixture
    async def store(self, temp_dir):
        """创建存储实例"""
        return JobStore(storage_dir=temp_dir)

    async def test_save_and_load(self, store):
        """测试保存和加载任务"""
        job = ScheduledJob(
            id="job_test",
            user_id="user_1",
            chat_id="chat_1",
            cron="0 9 * * *",
            description="测试任务",
        )
        
        await store.save(job)
        loaded = await store.get("job_test")
        
        assert loaded is not None
        assert loaded.id == "job_test"
        assert loaded.description == "测试任务"

    async def test_list_by_chat(self, store):
        """测试按聊天列表"""
        # 创建两个不同聊天的任务
        await store.save(ScheduledJob(
            id="job_1",
            user_id="user_1",
            chat_id="chat_a",
            cron="0 9 * * *",
            description="任务A",
        ))
        await store.save(ScheduledJob(
            id="job_2",
            user_id="user_2",
            chat_id="chat_b",
            cron="0 10 * * *",
            description="任务B",
        ))
        
        jobs = await store.list_by_chat("chat_a")
        assert len(jobs) == 1
        assert jobs[0].chat_id == "chat_a"

    async def test_delete(self, store):
        """测试删除任务"""
        await store.save(ScheduledJob(
            id="job_delete",
            user_id="user_1",
            chat_id="chat_1",
            cron="0 9 * * *",
            description="待删除",
        ))
        
        success = await store.delete("job_delete")
        assert success is True
        
        loaded = await store.get("job_delete")
        assert loaded is None

    async def test_persistence(self, store, temp_dir):
        """测试持久化"""
        job = ScheduledJob(
            id="job_persist",
            user_id="user_1",
            chat_id="chat_1",
            cron="0 9 * * *",
            description="持久化测试",
        )
        await store.save(job)
        
        # 创建新的存储实例，验证数据持久化
        new_store = JobStore(storage_dir=temp_dir)
        loaded = await new_store.get("job_persist")
        
        assert loaded is not None
        assert loaded.description == "持久化测试"


class TestPendingStore:
    """测试等待通知存储"""

    @pytest.fixture
    async def store(self, temp_dir):
        """创建存储实例"""
        return PendingResultStore(storage_dir=temp_dir)

    async def test_save_and_load(self, store):
        """测试保存和加载"""
        result = ScheduledResult(
            job_id="job_1",
            success=True,
            output="测试输出",
        )
        notification = PendingNotification(
            result=result,
            chat_id="chat_1",
            user_id="user_1",
        )
        
        await store.save("chat_1", [notification])
        loaded = await store.load("chat_1")
        
        assert len(loaded) == 1
        assert loaded[0].result.job_id == "job_1"
        assert loaded[0].result.success is True

    async def test_empty_load(self, store):
        """测试空加载"""
        loaded = await store.load("nonexistent")
        assert loaded == []


class TestScheduler:
    """测试调度器功能"""

    async def test_add_job(self, scheduler):
        """测试添加任务"""
        success, msg, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="测试任务",
            user_id="user_1",
            chat_id="chat_1",
        )
        
        assert success is True
        assert job is not None
        assert job.id.startswith("job_")
        assert "下次执行" in msg

    async def test_add_invalid_cron(self, scheduler):
        """测试添加无效任务"""
        success, msg, job = await scheduler.add_job(
            cron="invalid",
            description="测试任务",
            user_id="user_1",
            chat_id="chat_1",
        )
        
        assert success is False
        assert job is None
        assert "无效" in msg

    async def test_list_jobs(self, scheduler):
        """测试列出任务"""
        # 添加多个任务
        await scheduler.add_job(
            cron="0 9 * * *",
            description="任务1",
            user_id="user_1",
            chat_id="chat_1",
        )
        await scheduler.add_job(
            cron="0 10 * * *",
            description="任务2",
            user_id="user_1",
            chat_id="chat_1",
        )
        
        jobs = await scheduler.list_jobs(chat_id="chat_1")
        assert len(jobs) == 2

    async def test_toggle_job(self, scheduler):
        """测试切换任务状态"""
        success, msg, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="测试任务",
            user_id="user_1",
            chat_id="chat_1",
        )
        job_id = job.id
        
        # 切换为暂停
        success, msg = await scheduler.toggle_job(job_id)
        assert success is True
        assert "暂停" in msg
        
        job = await scheduler.get_job(job_id)
        assert job.is_active is False
        
        # 切换为激活
        success, msg = await scheduler.toggle_job(job_id)
        assert success is True
        assert "激活" in msg
        
        job = await scheduler.get_job(job_id)
        assert job.is_active is True

    async def test_remove_job(self, scheduler):
        """测试删除任务"""
        success, msg, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="待删除",
            user_id="user_1",
            chat_id="chat_1",
        )
        job_id = job.id
        
        success, msg = await scheduler.remove_job(job_id)
        assert success is True
        assert "已删除" in msg
        
        job = await scheduler.get_job(job_id)
        assert job is None


class TestCronEngine:
    """测试 Cron 引擎"""

    async def test_engine_lifecycle(self, temp_dir):
        """测试引擎生命周期"""
        store = JobStore(storage_dir=temp_dir)
        mock_trigger = MagicMock()
        
        engine = CronEngine(
            job_store=store,
            on_trigger=mock_trigger,
            check_interval=0.1,
        )
        
        assert not engine.is_running()
        
        await engine.start()
        assert engine.is_running()
        
        await engine.stop()
        assert not engine.is_running()

    async def test_triggers_job(self, temp_dir):
        """测试触发任务"""
        store = JobStore(storage_dir=temp_dir)
        mock_trigger = MagicMock()
        
        engine = CronEngine(
            job_store=store,
            on_trigger=mock_trigger,
            check_interval=0.1,
        )
        
        # 创建一个即将执行的任务
        job = ScheduledJob(
            id="test_job",
            user_id="user_1",
            chat_id="chat_1",
            cron="* * * * *",  # 每分钟执行
            description="测试触发",
            is_active=True,
            last_run=None,  # 从未执行
        )
        await store.save(job)
        
        await engine.start()
        await asyncio.sleep(0.3)  # 等待触发
        await engine.stop()
        
        # 验证触发被调用
        assert mock_trigger.called

    async def test_skips_inactive_job(self, temp_dir):
        """测试跳过未激活任务"""
        store = JobStore(storage_dir=temp_dir)
        mock_trigger = MagicMock()
        
        engine = CronEngine(
            job_store=store,
            on_trigger=mock_trigger,
            check_interval=0.1,
        )
        
        job = ScheduledJob(
            id="inactive_job",
            user_id="user_1",
            chat_id="chat_1",
            cron="* * * * *",
            description="未激活任务",
            is_active=False,  # 未激活
        )
        await store.save(job)
        
        await engine.start()
        await asyncio.sleep(0.3)
        await engine.stop()
        
        # 验证触发未被调用
        assert not mock_trigger.called


class TestGlobalScheduler:
    """测试全局调度器实例"""

    def test_get_scheduler_singleton(self):
        """测试单例模式"""
        sched1 = get_scheduler()
        sched2 = get_scheduler()
        assert sched1 is sched2

    def test_set_scheduler(self):
        """测试设置全局实例"""
        original = get_scheduler()
        new_sched = Scheduler()
        
        set_scheduler(new_sched)
        assert get_scheduler() is new_sched
        
        # 恢复原实例
        set_scheduler(original)


class TestModels:
    """测试数据模型"""

    def test_scheduled_job_serialization(self):
        """测试任务序列化"""
        job = ScheduledJob(
            id="job_1",
            user_id="user_1",
            chat_id="chat_1",
            cron="0 9 * * *",
            description="测试",
        )
        
        data = job.to_dict()
        restored = ScheduledJob.from_dict(data)
        
        assert restored.id == job.id
        assert restored.description == job.description
        assert restored.cron == job.cron

    def test_incoming_message(self):
        """测试消息模型"""
        msg = IncomingMessage(
            text="测试消息",
            source="scheduled",
            chat_id="chat_1",
            user_id="user_1",
        )
        
        data = msg.to_dict()
        restored = IncomingMessage.from_dict(data)
        
        assert restored.text == "测试消息"
        assert restored.source == "scheduled"

    def test_scheduled_result_format(self):
        """测试结果格式化"""
        result = ScheduledResult(
            job_id="job_1",
            success=True,
            output="成功输出",
        )
        
        msg = result.format_message()
        assert "✅" in msg
        assert "job_1" in msg
        assert "成功输出" in msg

    def test_scheduled_result_error_format(self):
        """测试错误结果格式化"""
        result = ScheduledResult(
            job_id="job_1",
            success=False,
            error="错误信息",
        )
        
        msg = result.format_message()
        assert "❌" in msg
        assert "错误信息" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
