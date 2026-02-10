"""定时任务功能演示脚本

这个脚本演示了 OKbot 定时任务模块的核心功能:
1. 创建定时任务
2. 列出所有任务
3. 切换任务状态
4. 删除任务
5. 验证 Cron 表达式
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from kimi_cli.scheduler import Scheduler, get_scheduler, validate_cron, get_next_runs
from kimi_cli.scheduler.models import ScheduledJob, IncomingMessage, ScheduledResult


async def demo_cron_validation():
    """演示 Cron 表达式验证"""
    print("\n" + "="*60)
    print("🕐 演示 1: Cron 表达式验证")
    print("="*60)
    
    test_expressions = [
        ("0 9 * * *", "每天上午9点"),
        ("0 9 * * 1", "每周一上午9点"),
        ("0 9 1 * *", "每月1日上午9点"),
        ("*/30 * * * *", "每30分钟"),
        ("*/5 * * * * *", "每5秒 (6字段秒级)"),
        ("invalid", "无效的表达式"),
    ]
    
    for cron, desc in test_expressions:
        valid, message = validate_cron(cron)
        status = "✅" if valid else "❌"
        print(f"\n{status} {desc}")
        print(f"   表达式: {cron}")
        print(f"   结果: {message}")


async def demo_next_runs():
    """演示获取下次执行时间"""
    print("\n" + "="*60)
    print("🕐 演示 2: 获取下次执行时间")
    print("="*60)
    
    cron = "0 9 * * *"
    print(f"\n表达式: {cron} (每天上午9点)")
    print("未来5次执行时间:")
    
    runs = get_next_runs(cron, count=5)
    for i, run in enumerate(runs, 1):
        print(f"  {i}. {run.strftime('%Y-%m-%d %H:%M:%S')}")


async def demo_job_management():
    """演示任务管理功能"""
    print("\n" + "="*60)
    print("🕐 演示 3: 任务管理")
    print("="*60)
    
    # 创建调度器
    scheduler = Scheduler()
    await scheduler.initialize()
    
    # 1. 添加任务
    print("\n📌 创建定时任务...")
    success, message, job = await scheduler.add_job(
        cron="0 9 * * *",
        description="每日数据备份",
        user_id="user_001",
        chat_id="chat_001",
        chat_type="p2p",
    )
    print(f"结果: {message}")
    
    if job:
        # 2. 列出任务
        print("\n📋 列出所有任务:")
        jobs = await scheduler.list_jobs()
        for j in jobs:
            status = "🟢 激活" if j.is_active else "🔴 暂停"
            print(f"  - {j.id}: {j.description} ({status})")
        
        # 3. 切换任务状态
        print(f"\n🔄 切换任务状态:")
        success, message = await scheduler.toggle_job(job.id)
        print(f"结果: {message}")
        
        # 4. 获取任务详情
        print(f"\n🔍 获取任务详情:")
        job_detail = await scheduler.get_job(job.id)
        if job_detail:
            print(f"  ID: {job_detail.id}")
            print(f"  描述: {job_detail.description}")
            print(f"  Cron: {job_detail.cron}")
            print(f"  状态: {'激活' if job_detail.is_active else '暂停'}")
            print(f"  创建时间: {job_detail.created_at}")
        
        # 5. 删除任务
        print(f"\n🗑️ 删除任务:")
        success, message = await scheduler.remove_job(job.id)
        print(f"结果: {message}")
    
    # 验证删除
    jobs = await scheduler.list_jobs()
    print(f"\n剩余任务数: {len(jobs)}")


async def demo_models():
    """演示数据模型"""
    print("\n" + "="*60)
    print("🕐 演示 4: 数据模型")
    print("="*60)
    
    # 创建任务
    job = ScheduledJob(
        id="job_123",
        user_id="user_001",
        chat_id="chat_001",
        cron="0 9 * * *",
        description="测试任务",
        chat_type="p2p",
    )
    print("\n📌 创建 ScheduledJob:")
    print(f"  {job}")
    
    # 序列化和反序列化
    data = job.to_dict()
    print(f"\n📦 序列化为字典:")
    print(f"  {data}")
    
    restored = ScheduledJob.from_dict(data)
    print(f"\n🔄 反序列化后:")
    print(f"  ID: {restored.id}")
    print(f"  描述: {restored.description}")
    
    # 创建消息
    message = IncomingMessage(
        text="生成日报",
        source="scheduled",
        source_id="job_123",
        chat_id="chat_001",
        user_id="user_001",
    )
    print(f"\n📨 创建 IncomingMessage:")
    print(f"  内容: {message.text}")
    print(f"  来源: {message.source}")
    print(f"  用户: {message.user_id}")
    
    # 创建执行结果
    result = ScheduledResult(
        job_id="job_123",
        success=True,
        output="任务执行成功，已生成报告",
    )
    print(f"\n✅ 创建 ScheduledResult:")
    print(f"  格式化输出:\n{result.format_message()}")


async def demo_commands():
    """演示命令处理"""
    print("\n" + "="*60)
    print("🕐 演示 5: 命令处理")
    print("="*60)
    
    scheduler = Scheduler()
    await scheduler.initialize()
    
    # 添加一些测试任务
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
    print(result)
    
    # 测试 help 命令
    print("\n❓ /cron help 命令输出 (部分):")
    result = await scheduler.handle_cron_help_command()
    # 只显示前20行
    lines = result.split("\n")[:20]
    print("\n".join(lines))
    print("... (其余内容省略)")


async def main():
    """主函数"""
    print("\n" + "="*60)
    print("🚀 OKbot 定时任务功能演示")
    print("="*60)
    print(f"\n当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        await demo_cron_validation()
        await demo_next_runs()
        await demo_job_management()
        await demo_models()
        await demo_commands()
        
        print("\n" + "="*60)
        print("✅ 所有演示完成!")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ 演示出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
