"""
定时任务真实场景测试

模拟真实使用场景：
1. 用户通过飞书命令创建定时任务
2. Cron 引擎按预定时间触发任务
3. 任务执行并生成结果
4. 结果通知给用户
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kimi_cli.scheduler import Scheduler, get_scheduler, set_scheduler
from kimi_cli.scheduler.models import (
    IncomingMessage,
    ScheduledJob,
    ScheduledResult,
    PendingNotification,
)
from kimi_cli.scheduler.store import JobStore, PendingResultStore
from kimi_cli.scheduler.cron_engine import CronEngine


class TestRealWorldScenario:
    """真实场景测试"""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        """创建临时存储目录"""
        return tmp_path / "scheduler"

    @pytest.fixture
    async def initialized_scheduler(self, temp_storage):
        """创建并初始化调度器"""
        scheduler = Scheduler(storage_dir=str(temp_storage))
        await scheduler.initialize()
        yield scheduler
        await scheduler.stop()

    async def test_daily_report_workflow(self, initialized_scheduler):
        """测试每日报告工作流"""
        scheduler = initialized_scheduler
        
        # 1. 用户创建每日报告任务
        success, message, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="生成昨日销售日报",
            user_id="user_123",
            chat_id="chat_456",
        )
        
        assert success is True
        assert job is not None
        assert job.description == "生成昨日销售日报"
        
        # 2. 验证任务被正确保存
        jobs = await scheduler.list_jobs(chat_id="chat_456")
        assert len(jobs) == 1
        assert jobs[0].id == job.id
        
        # 3. 切换任务状态（暂停）
        success, message = await scheduler.toggle_job(job.id)
        assert success is True
        assert "暂停" in message
        
        job_refreshed = await scheduler.get_job(job.id)
        assert job_refreshed.is_active is False
        
        # 4. 重新激活任务
        success, message = await scheduler.toggle_job(job.id)
        assert success is True
        assert "激活" in message
        
        job_refreshed = await scheduler.get_job(job.id)
        assert job_refreshed.is_active is True
        
        # 5. 删除任务
        success, message = await scheduler.remove_job(job.id)
        assert success is True
        
        jobs = await scheduler.list_jobs(chat_id="chat_456")
        assert len(jobs) == 0

    async def test_multiple_jobs_per_chat(self, initialized_scheduler):
        """测试一个对话中的多个任务"""
        scheduler = initialized_scheduler
        chat_id = "chat_multi"
        
        # 创建多个任务
        tasks = [
            ("0 9 * * *", "晨会提醒"),
            ("0 18 * * *", "日报提交提醒"),
            ("0 9 * * 1", "周会提醒"),
        ]
        
        created_jobs = []
        for cron, desc in tasks:
            success, _, job = await scheduler.add_job(
                cron=cron,
                description=desc,
                user_id="user_multi",
                chat_id=chat_id,
            )
            assert success is True
            created_jobs.append(job)
        
        # 验证所有任务
        jobs = await scheduler.list_jobs(chat_id=chat_id)
        assert len(jobs) == 3
        
        # 按用户过滤
        user_jobs = await scheduler.list_jobs(user_id="user_multi")
        assert len(user_jobs) == 3
        
        # 删除一个任务
        await scheduler.remove_job(created_jobs[0].id)
        
        jobs = await scheduler.list_jobs(chat_id=chat_id)
        assert len(jobs) == 2

    async def test_cron_engine_triggers_job(self, temp_storage):
        """测试 Cron 引擎正确触发任务"""
        job_store = JobStore(str(temp_storage))
        
        # 创建即将触发的任务（每分钟）
        job = ScheduledJob(
            id="job_trigger_test",
            user_id="user_test",
            chat_id="chat_test",
            cron="* * * * *",
            description="测试触发任务",
            is_active=True,
        )
        await job_store.save(job)
        
        # 记录触发的任务
        triggered = []
        
        def on_trigger(j: ScheduledJob):
            triggered.append(j.id)
        
        # 创建引擎并启动
        engine = CronEngine(
            job_store=job_store,
            on_trigger=on_trigger,
            check_interval=0.5,  # 快速检查用于测试
        )
        
        await engine.start()
        
        # 等待引擎检查几次
        await asyncio.sleep(2)
        
        await engine.stop()
        
        # 验证任务被触发
        assert len(triggered) >= 1
        assert "job_trigger_test" in triggered

    async def test_cron_engine_skips_inactive_job(self, temp_storage):
        """测试 Cron 引擎跳过暂停的任务"""
        job_store = JobStore(str(temp_storage))
        
        # 创建暂停的任务
        job = ScheduledJob(
            id="job_inactive",
            user_id="user_test",
            chat_id="chat_test",
            cron="* * * * *",
            description="暂停的任务",
            is_active=False,
        )
        await job_store.save(job)
        
        triggered = []
        
        def on_trigger(j: ScheduledJob):
            triggered.append(j.id)
        
        engine = CronEngine(
            job_store=job_store,
            on_trigger=on_trigger,
            check_interval=0.5,
        )
        
        await engine.start()
        await asyncio.sleep(1.5)
        await engine.stop()
        
        # 验证暂停的任务没有被触发
        assert len(triggered) == 0

    async def test_second_level_cron(self, temp_storage):
        """测试秒级 Cron 表达式"""
        job_store = JobStore(str(temp_storage))
        
        # 创建秒级任务（每5秒）
        job = ScheduledJob(
            id="job_second_level",
            user_id="user_test",
            chat_id="chat_test",
            cron="*/5 * * * * *",  # 6字段秒级表达式
            description="秒级任务",
            is_active=True,
        )
        await job_store.save(job)
        
        triggered = []
        
        def on_trigger(j: ScheduledJob):
            triggered.append({
                "id": j.id,
                "time": datetime.now(),
            })
        
        engine = CronEngine(
            job_store=job_store,
            on_trigger=on_trigger,
            check_interval=1.0,
        )
        
        await engine.start()
        await asyncio.sleep(6)  # 等待至少一次触发
        await engine.stop()
        
        # 验证任务被触发
        assert len(triggered) >= 1
        assert triggered[0]["id"] == "job_second_level"

    async def test_job_persistence_across_restart(self, temp_storage):
        """测试任务在重启后持久化"""
        # 第一个调度器实例
        scheduler1 = Scheduler(storage_dir=str(temp_storage))
        await scheduler1.initialize()
        
        success, _, job = await scheduler1.add_job(
            cron="0 9 * * *",
            description="持久化测试任务",
            user_id="user_persist",
            chat_id="chat_persist",
        )
        assert success is True
        job_id = job.id
        
        await scheduler1.stop()
        
        # 第二个调度器实例（模拟重启）
        scheduler2 = Scheduler(storage_dir=str(temp_storage))
        await scheduler2.initialize()
        
        # 验证任务仍然存在
        jobs = await scheduler2.list_jobs(chat_id="chat_persist")
        assert len(jobs) == 1
        assert jobs[0].id == job_id
        assert jobs[0].description == "持久化测试任务"
        
        await scheduler2.stop()

    async def test_concurrent_job_creation(self, initialized_scheduler):
        """测试并发创建任务"""
        scheduler = initialized_scheduler
        
        async def create_job(index: int):
            return await scheduler.add_job(
                cron="0 9 * * *",
                description=f"并发任务 {index}",
                user_id="user_concurrent",
                chat_id="chat_concurrent",
            )
        
        # 并发创建10个任务
        tasks = [create_job(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        # 验证所有任务都创建成功
        assert all(r[0] for r in results)  # success
        assert len(set(r[2].id for r in results)) == 10  # 唯一ID
        
        # 验证列表
        jobs = await scheduler.list_jobs(chat_id="chat_concurrent")
        assert len(jobs) == 10

    async def test_invalid_cron_handling(self, initialized_scheduler):
        """测试无效 Cron 表达式的处理"""
        scheduler = initialized_scheduler
        
        invalid_crons = [
            "invalid",
            "",
            "* * *",
            "99 99 * * *",
        ]
        
        for cron in invalid_crons:
            success, message, job = await scheduler.add_job(
                cron=cron,
                description="无效任务",
                user_id="user_invalid",
                chat_id="chat_invalid",
            )
            assert success is False, f"Cron '{cron}' 应该被识别为无效"
            assert job is None


class TestCommandHandlers:
    """命令处理器测试"""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        return tmp_path / "scheduler"

    @pytest.fixture
    async def initialized_scheduler(self, temp_storage):
        scheduler = Scheduler(storage_dir=str(temp_storage))
        await scheduler.initialize()
        yield scheduler
        await scheduler.stop()

    async def test_add_command_response(self, initialized_scheduler):
        """测试 add 命令响应格式"""
        scheduler = initialized_scheduler
        
        result = await scheduler.handle_cron_add_command(
            cron="0 9 * * *",
            description="测试任务",
            user_id="user_1",
            chat_id="chat_1",
        )
        
        assert "✅" in result
        assert "测试任务" in result
        assert "0 9 * * *" in result
        assert "ID:" in result

    async def test_add_command_invalid_cron(self, initialized_scheduler):
        """测试 add 命令处理无效 Cron"""
        scheduler = initialized_scheduler
        
        result = await scheduler.handle_cron_add_command(
            cron="invalid",
            description="测试任务",
            user_id="user_1",
            chat_id="chat_1",
        )
        
        assert "❌" in result
        assert "失败" in result

    async def test_list_command_empty(self, initialized_scheduler):
        """测试 list 命令无任务时"""
        scheduler = initialized_scheduler
        
        result = await scheduler.handle_cron_list_command()
        
        assert "暂无定时任务" in result
        assert "/cron add" in result

    async def test_list_command_with_jobs(self, initialized_scheduler):
        """测试 list 命令有任务时"""
        scheduler = initialized_scheduler
        
        # 添加任务
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
        
        result = await scheduler.handle_cron_list_command(chat_id="chat_1")
        
        assert "定时任务列表" in result
        assert "任务1" in result
        assert "任务2" in result
        assert "/cron remove" in result

    async def test_remove_command_success(self, initialized_scheduler):
        """测试 remove 命令成功"""
        scheduler = initialized_scheduler
        
        success, _, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="待删除任务",
            user_id="user_1",
            chat_id="chat_1",
        )
        job_id = job.id
        
        result = await scheduler.handle_cron_remove_command(job_id)
        
        assert "✅" in result
        assert "已删除" in result

    async def test_remove_command_not_found(self, initialized_scheduler):
        """测试 remove 命令任务不存在"""
        scheduler = initialized_scheduler
        
        result = await scheduler.handle_cron_remove_command("nonexistent_job")
        
        assert "❌" in result
        assert "不存在" in result

    async def test_help_command_content(self, initialized_scheduler):
        """测试 help 命令内容"""
        scheduler = initialized_scheduler
        
        result = await scheduler.handle_cron_help_command()
        
        assert "定时任务帮助" in result
        assert "/cron add" in result
        assert "/cron list" in result
        assert "/cron remove" in result
        assert "Cron 表达式" in result
        assert "0 9 * * *" in result  # 示例表达式


class TestEdgeCases:
    """边界情况测试"""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        return tmp_path / "scheduler"

    @pytest.fixture
    async def initialized_scheduler(self, temp_storage):
        """创建并初始化调度器"""
        scheduler = Scheduler(storage_dir=str(temp_storage))
        await scheduler.initialize()
        yield scheduler
        await scheduler.stop()

    async def test_very_long_description(self, temp_storage):
        """测试超长描述"""
        scheduler = Scheduler(storage_dir=str(temp_storage))
        await scheduler.initialize()
        
        long_desc = "A" * 1000  # 1000字符描述
        
        success, _, job = await scheduler.add_job(
            cron="0 9 * * *",
            description=long_desc,
            user_id="user_1",
            chat_id="chat_1",
        )
        
        assert success is True
        assert job.description == long_desc
        
        await scheduler.stop()

    async def test_special_characters_in_description(self, temp_storage):
        """测试描述中的特殊字符"""
        scheduler = Scheduler(storage_dir=str(temp_storage))
        await scheduler.initialize()
        
        special_desc = '任务描述包含 "引号"、\n换行、\t制表符和 emoji 🎉'
        
        success, _, job = await scheduler.add_job(
            cron="0 9 * * *",
            description=special_desc,
            user_id="user_1",
            chat_id="chat_1",
        )
        
        assert success is True
        
        # 验证持久化后仍然正确
        jobs = await scheduler.list_jobs()
        assert jobs[0].description == special_desc
        
        await scheduler.stop()

    async def test_toggle_job_twice(self, initialized_scheduler):
        """测试切换任务状态两次"""
        scheduler = initialized_scheduler
        
        success, _, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="切换测试",
            user_id="user_1",
            chat_id="chat_1",
        )
        
        # 切换 -> 暂停
        await scheduler.toggle_job(job.id)
        job = await scheduler.get_job(job.id)
        assert job.is_active is False
        
        # 切换 -> 激活
        await scheduler.toggle_job(job.id)
        job = await scheduler.get_job(job.id)
        assert job.is_active is True
        
        # 切换 -> 暂停
        await scheduler.toggle_job(job.id)
        job = await scheduler.get_job(job.id)
        assert job.is_active is False

    async def test_job_store_operations(self, temp_storage):
        """测试任务存储的各种操作"""
        store = JobStore(str(temp_storage))
        
        # 保存多个任务
        jobs = []
        for i in range(5):
            job = ScheduledJob(
                id=f"job_{i}",
                user_id=f"user_{i % 2}",  # 两个用户
                chat_id=f"chat_{i % 3}",  # 三个对话
                cron="0 9 * * *",
                description=f"Job {i}",
            )
            await store.save(job)
            jobs.append(job)
        
        # 测试 list_all
        all_jobs = await store.list_all()
        assert len(all_jobs) == 5
        
        # 测试 list_by_user
        user0_jobs = await store.list_by_user("user_0")
        assert len(user0_jobs) == 3  # job_0, job_2, job_4
        
        user1_jobs = await store.list_by_user("user_1")
        assert len(user1_jobs) == 2  # job_1, job_3
        
        # 测试 list_by_chat
        chat0_jobs = await store.list_by_chat("chat_0")
        assert len(chat0_jobs) == 2  # job_0, job_3
        
        # 测试删除
        await store.delete("job_0")
        all_jobs = await store.list_all()
        assert len(all_jobs) == 4
        
        # 验证删除后无法获取
        deleted = await store.get("job_0")
        assert deleted is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
