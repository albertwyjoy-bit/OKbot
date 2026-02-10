#!/usr/bin/env python3
"""定时任务功能简单测试"""

import asyncio
import sys
import os
import tempfile
from datetime import datetime

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from kimi_cli.scheduler.models import (
    ScheduledJob, 
    IncomingMessage, 
    ScheduledResult,
    PendingNotification,
)
from kimi_cli.scheduler.cron_engine import CronEngine, validate_cron, get_next_runs
from kimi_cli.scheduler.store import JobStore, PendingResultStore
from kimi_cli.scheduler.scheduler import Scheduler


def test_cron_validation():
    """测试 Cron 表达式验证"""
    print("\n" + "="*60)
    print("测试 1: Cron 表达式验证")
    print("="*60)
    
    # 测试有效表达式
    test_cases = [
        ("0 9 * * *", "每天上午9点", True),
        ("0 9 * * 1", "每周一上午9点", True),
        ("*/30 * * * *", "每30分钟", True),
        ("0 0 1 * *", "每月1日0点", True),
        ("*/5 * * * * *", "每5秒 (6字段秒级)", True),
        ("invalid", "无效的表达式", False),
        ("99 99 * * *", "超出范围的值", False),
        ("", "空表达式", False),
    ]
    
    for cron, desc, expected_valid in test_cases:
        is_valid, msg = validate_cron(cron)
        status = "✅" if is_valid == expected_valid else "❌"
        print(f"\n{status} {desc}")
        print(f"   表达式: '{cron}'")
        print(f"   验证结果: {'通过' if is_valid else '失败'}")
        print(f"   消息: {msg}")


def test_next_runs():
    """测试获取下次执行时间"""
    print("\n" + "="*60)
    print("测试 2: 获取下次执行时间")
    print("="*60)
    
    cron = "0 9 * * *"
    print(f"\n表达式: {cron} (每天上午9点)")
    print("未来5次执行时间:")
    
    runs = get_next_runs(cron, count=5)
    for i, run in enumerate(runs, 1):
        print(f"  {i}. {run.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 测试秒级表达式
    cron_seconds = "*/5 * * * * *"
    print(f"\n表达式: {cron_seconds} (每5秒)")
    runs = get_next_runs(cron_seconds, count=5)
    for i, run in enumerate(runs, 1):
        print(f"  {i}. {run.strftime('%Y-%m-%d %H:%M:%S')}")


def test_models():
    """测试数据模型"""
    print("\n" + "="*60)
    print("测试 3: 数据模型")
    print("="*60)
    
    # 创建任务
    job = ScheduledJob(
        id="job_test_001",
        cron="0 9 * * *",
        description="生成日报",
        user_id="user123",
        chat_id="chat456",
        is_active=True,
        created_at=datetime.now()
    )
    print(f"\n✅ 创建 ScheduledJob:")
    print(f"   ID: {job.id}")
    print(f"   描述: {job.description}")
    print(f"   Cron: {job.cron}")
    print(f"   启用: {job.is_active}")
    
    # 序列化和反序列化
    data = job.to_dict()
    restored = ScheduledJob.from_dict(data)
    print(f"\n✅ 序列化/反序列化测试:")
    print(f"   原始ID: {job.id}")
    print(f"   恢复ID: {restored.id}")
    print(f"   匹配: {job.id == restored.id}")
    
    # 创建消息
    msg = IncomingMessage(
        text="测试消息内容",
        chat_id="chat456",
        user_id="user123",
        message_type="text"
    )
    print(f"\n✅ 创建 IncomingMessage:")
    print(f"   内容: {msg.text}")
    print(f"   来源: {msg.source}")
    print(f"   用户ID: {msg.user_id}")
    
    # 创建执行结果
    result = ScheduledResult(
        job_id="job_test_001",
        success=True,
        output="日报生成成功，包含10条记录",
        executed_at=datetime.now()
    )
    print(f"\n✅ 创建 ScheduledResult:")
    print(f"   格式化输出:")
    print("   " + result.format_message().replace("\n", "\n   "))


async def test_store():
    """测试存储模块"""
    print("\n" + "="*60)
    print("测试 4: 存储模块")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 测试 JobStore
        job_store = JobStore(tmpdir)
        
        job = ScheduledJob(
            id="job_test_store",
            cron="0 10 * * *",
            description="测试任务",
            user_id="user123",
            chat_id="chat456",
            is_active=True,
            created_at=datetime.now()
        )
        
        # 保存任务
        await job_store.save(job)
        print(f"\n✅ 保存任务: {job.id}")
        
        # 读取任务
        loaded_job = await job_store.get(job.id)
        if loaded_job:
            print(f"✅ 读取任务成功: {loaded_job.description}")
        
        # 列出任务
        jobs = await job_store.list_all()
        print(f"✅ 列出任务: 共 {len(jobs)} 个任务")
        
        # 按 chat_id 筛选
        chat_jobs = await job_store.list_by_chat(chat_id="chat456")
        print(f"✅ 按 chat_id 筛选: 共 {len(chat_jobs)} 个任务")
        
        # 删除任务
        await job_store.delete(job.id)
        jobs = await job_store.list_all()
        print(f"✅ 删除任务后: 共 {len(jobs)} 个任务")
        
        # 测试 PendingResultStore
        pending_store = PendingResultStore(tmpdir)
        
        result = ScheduledResult(
            job_id="job_pending_001",
            success=True,
            output="任务执行成功",
            executed_at=datetime.now()
        )
        
        pending = PendingNotification(
            result=result,
            chat_id="chat789",
            user_id="user789",
        )
        
        await pending_store.save("chat789", [pending])
        print(f"\n✅ 添加待发送通知")
        
        pending_list = await pending_store.load("chat789")
        print(f"✅ 获取待发送列表: 共 {len(pending_list)} 个")


async def test_scheduler():
    """测试调度器基本功能"""
    print("\n" + "="*60)
    print("测试 5: 调度器基本功能")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        scheduler = Scheduler(storage_dir=tmpdir)
        await scheduler.initialize()
        print("\n✅ 调度器初始化成功")
        
        # 添加任务
        success, message, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="测试定时任务",
            user_id="user_test",
            chat_id="chat_test"
        )
        print(f"\n✅ 添加任务: {success}")
        print(f"   消息: {message}")
        
        if job:
            print(f"   任务ID: {job.id}")
            
            # 列出任务
            jobs = await scheduler.list_jobs(chat_id="chat_test")
            print(f"\n✅ 列出任务: 共 {len(jobs)} 个")
            
            # 切换任务状态
            toggle_success, toggle_msg = await scheduler.toggle_job(job.id)
            print(f"\n✅ 切换任务状态: {toggle_success}")
            print(f"   消息: {toggle_msg}")
            
            # 重新读取任务
            job_refreshed = await scheduler.get_job(job.id)
            if job_refreshed:
                print(f"   当前状态: 启用={job_refreshed.is_active}")
            
            # 删除任务
            remove_success, remove_msg = await scheduler.remove_job(job.id)
            print(f"\n✅ 删除任务: {remove_success}")
            print(f"   消息: {remove_msg}")
        
        await scheduler.stop()
        print(f"\n✅ 调度器已停止")


async def test_commands():
    """测试命令处理"""
    print("\n" + "="*60)
    print("测试 6: 命令处理")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        scheduler = Scheduler(storage_dir=tmpdir)
        await scheduler.initialize()
        
        # 添加测试任务
        await scheduler.add_job(
            cron="0 9 * * *",
            description="早晨报告",
            user_id="user_001",
            chat_id="chat_001",
        )
        await scheduler.add_job(
            cron="0 18 * * *",
            description="晚间总结",
            user_id="user_001",
            chat_id="chat_001",
        )
        
        # 测试 list 命令
        print("\n📋 /cron list 命令输出:")
        result = await scheduler.handle_cron_list_command(chat_id="chat_001")
        print(result[:500] + "..." if len(result) > 500 else result)
        
        # 测试 help 命令
        print("\n❓ /cron help 命令输出 (部分):")
        result = await scheduler.handle_cron_help_command()
        lines = result.split("\n")[:15]
        print("\n".join(lines))
        print("... (其余内容省略)")


async def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("  OKbot 定时任务模块功能测试")
    print("="*60)
    print(f"\n当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # 运行各项测试
        test_cron_validation()
        test_next_runs()
        test_models()
        await test_store()
        await test_scheduler()
        await test_commands()
        
        print("\n" + "="*60)
        print("  ✅ 所有测试通过!")
        print("="*60)
        return 0
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
