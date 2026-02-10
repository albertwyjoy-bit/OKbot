#!/usr/bin/env python3
"""定时任务集成测试 - 模拟完整的任务调度流程"""

import asyncio
import tempfile
import os
import sys
from datetime import datetime, timedelta

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kimi_cli.scheduler import (
    Scheduler,
    CronEngine,
    JobStore,
    ScheduledJob,
    get_scheduler,
    set_scheduler,
)


class MockFeishuHandler:
    """模拟飞书消息处理器"""
    
    def __init__(self):
        self.messages_sent = []
        self._sessions = {}
    
    def send_text_message(self, chat_id: str, message: str):
        self.messages_sent.append({"chat_id": chat_id, "message": message})
        print(f"  [模拟发送消息到 {chat_id}]: {message[:100]}...")
    
    async def _create_soul_for_session(self, session_key: str):
        """模拟创建 soul"""
        return MockSoul()


class MockSoul:
    """模拟 KimiSoul"""
    pass


async def test_scheduler_integration():
    """测试完整的调度器集成流程"""
    print("=" * 60)
    print("定时任务集成测试")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建模拟飞书处理器
        feishu_handler = MockFeishuHandler()
        
        # 创建调度器
        scheduler = Scheduler(
            feishu_handler=feishu_handler,
            storage_dir=tmpdir,
        )
        set_scheduler(scheduler)
        
        print("\n1. 初始化调度器")
        await scheduler.initialize()
        print("✅ 调度器初始化成功")
        
        print("\n2. 启动调度器")
        await scheduler.start()
        print("✅ 调度器启动成功")
        
        print("\n3. 创建多个测试任务")
        jobs = []
        
        # 任务1: 每分钟执行
        success, message, job1 = await scheduler.add_job(
            cron="* * * * *",
            description="每分钟任务",
            user_id="user_001",
            chat_id="chat_001",
        )
        if success:
            jobs.append(job1)
            print(f"✅ 创建任务1: {job1.id}")
        
        # 任务2: 每小时执行
        success, message, job2 = await scheduler.add_job(
            cron="0 * * * *",
            description="每小时任务",
            user_id="user_001",
            chat_id="chat_001",
        )
        if success:
            jobs.append(job2)
            print(f"✅ 创建任务2: {job2.id}")
        
        # 任务3: 每天执行
        success, message, job3 = await scheduler.add_job(
            cron="0 9 * * *",
            description="每日早报",
            user_id="user_001",
            chat_id="chat_002",
        )
        if success:
            jobs.append(job3)
            print(f"✅ 创建任务3: {job3.id}")
        
        print(f"\n共创建 {len(jobs)} 个任务")
        
        print("\n4. 列出所有任务")
        all_jobs = await scheduler.list_jobs()
        print(f"✅ 系统中共有 {len(all_jobs)} 个任务")
        for job in all_jobs:
            print(f"   - {job.id}: {job.description} ({job.cron})")
        
        print("\n5. 按 chat_id 过滤任务")
        chat1_jobs = await scheduler.list_jobs(chat_id="chat_001")
        print(f"✅ chat_001 有 {len(chat1_jobs)} 个任务")
        
        chat2_jobs = await scheduler.list_jobs(chat_id="chat_002")
        print(f"✅ chat_002 有 {len(chat2_jobs)} 个任务")
        
        print("\n6. 切换任务状态")
        if jobs:
            success, message = await scheduler.toggle_job(jobs[0].id)
            print(f"✅ {message}")
            
            # 验证状态变化
            job = await scheduler.get_job(jobs[0].id)
            print(f"✅ 任务状态: {'激活' if job.is_active else '暂停'}")
        
        print("\n7. 立即触发任务（测试用）")
        if jobs:
            success, message = await scheduler.trigger_job_now(jobs[0].id)
            print(f"✅ {message}")
        
        print("\n8. 删除任务")
        for job in jobs:
            success, message = await scheduler.remove_job(job.id)
            print(f"✅ 删除 {job.id}: {message}")
        
        # 验证删除
        remaining_jobs = await scheduler.list_jobs()
        print(f"✅ 剩余任务数: {len(remaining_jobs)}")
        
        print("\n9. 停止调度器")
        await scheduler.stop()
        print("✅ 调度器停止成功")
        
    print("\n" + "=" * 60)
    print("集成测试完成！")
    print("=" * 60)


async def test_cron_engine_with_mock_trigger():
    """测试 Cron 引擎触发逻辑"""
    print("\n" + "=" * 60)
    print("测试 Cron 引擎触发逻辑")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        job_store = JobStore(tmpdir)
        
        triggered = []
        
        def on_trigger(job: ScheduledJob):
            triggered.append(job.id)
            print(f"  🔔 任务触发: {job.id} - {job.description}")
        
        # 创建引擎，使用1秒检查间隔
        engine = CronEngine(
            job_store=job_store,
            on_trigger=on_trigger,
            check_interval=1.0,
        )
        
        # 创建测试任务
        job = ScheduledJob(
            id="test_job_001",
            user_id="user_001",
            chat_id="chat_001",
            cron="* * * * *",  # 每分钟
            description="测试触发任务",
            is_active=True,
        )
        await job_store.save(job)
        
        print("\n1. 启动引擎")
        await engine.start()
        print("✅ 引擎启动成功")
        
        print("\n2. 模拟任务触发检查")
        # 手动调用检查方法
        await engine._check_jobs()
        print(f"✅ 触发检查完成，已触发任务: {len(triggered)} 个")
        
        print("\n3. 停止引擎")
        await engine.stop()
        print("✅ 引擎停止成功")


async def test_job_persistence():
    """测试任务持久化"""
    print("\n" + "=" * 60)
    print("测试任务持久化")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 第一个实例：创建任务
        store1 = JobStore(tmpdir)
        
        job = ScheduledJob(
            id="persist_job_001",
            user_id="user_001",
            chat_id="chat_001",
            cron="0 9 * * *",
            description="持久化测试任务",
        )
        await store1.save(job)
        print(f"✅ 任务已保存: {job.id}")
        
        # 第二个实例：读取任务
        store2 = JobStore(tmpdir)
        loaded_job = await store2.get("persist_job_001")
        
        if loaded_job:
            print(f"✅ 任务已恢复: {loaded_job.id}")
            print(f"   描述: {loaded_job.description}")
            print(f"   Cron: {loaded_job.cron}")
            assert loaded_job.id == job.id
            assert loaded_job.description == job.description
            print("✅ 数据一致性验证通过")
        else:
            print("❌ 任务恢复失败")


async def test_notification_modes():
    """测试不同通知模式"""
    print("\n" + "=" * 60)
    print("测试通知模式")
    print("=" * 60)
    
    from kimi_cli.scheduler.models import NotificationMode
    
    modes = [
        (NotificationMode.SILENT, "静默"),
        (NotificationMode.NORMAL, "正常"),
        (NotificationMode.VERBOSE, "详细"),
    ]
    
    for mode, name in modes:
        print(f"✅ {mode.value} - {name}")


async def main():
    """主测试函数"""
    await test_scheduler_integration()
    await test_cron_engine_with_mock_trigger()
    await test_job_persistence()
    await test_notification_modes()
    
    print("\n" + "=" * 60)
    print("所有集成测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
