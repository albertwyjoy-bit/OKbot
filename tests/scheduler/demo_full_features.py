"""
定时任务功能完整演示脚本

演示内容包括：
1. Cron 表达式验证（标准5字段和秒级6字段）
2. 定时任务 CRUD 操作
3. 任务执行历史
4. 任务触发和通知机制
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime

from kimi_cli.scheduler import (
    Scheduler,
    get_scheduler,
    set_scheduler,
    validate_cron,
    get_next_runs,
)
from kimi_cli.scheduler.models import (
    ScheduledJob,
    ScheduledResult,
    PendingNotification,
    IncomingMessage,
)
from kimi_cli.scheduler.store import JobStore, PendingResultStore
from kimi_cli.scheduler.cron_engine import CronEngine


class Colors:
    """终端颜色"""
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    END = "\033[0m"


def print_section(title: str) -> None:
    """打印章节标题"""
    print(f"\n{Colors.CYAN}{'=' * 60}{Colors.END}")
    print(f"{Colors.CYAN}  {title}{Colors.END}")
    print(f"{Colors.CYAN}{'=' * 60}{Colors.END}\n")


def print_success(msg: str) -> None:
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")


def print_warning(msg: str) -> None:
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")


def print_error(msg: str) -> None:
    print(f"{Colors.RED}❌ {msg}{Colors.END}")


def print_info(msg: str) -> None:
    print(f"{Colors.BLUE}ℹ️  {msg}{Colors.END}")


async def demo_cron_validation():
    """演示 Cron 表达式验证"""
    print_section("1. Cron 表达式验证")
    
    # 标准5字段表达式
    standard_crons = [
        ("0 9 * * *", "每天上午9点"),
        ("0 9 * * 1", "每周一上午9点"),
        ("0 9 1 * *", "每月1日上午9点"),
        ("*/30 * * * *", "每30分钟"),
        ("0 0 * * 0", "每周日午夜"),
    ]
    
    print("标准5字段 Cron 表达式:")
    for cron, desc in standard_crons:
        valid, msg = validate_cron(cron)
        status = f"{Colors.GREEN}✓{Colors.END}" if valid else f"{Colors.RED}✗{Colors.END}"
        print(f"  {status} `{cron}` - {desc}")
        if valid:
            next_runs = get_next_runs(cron, count=3)
            next_runs_str = [r.strftime('%Y-%m-%d %H:%M:%S') if hasattr(r, 'strftime') else str(r) for r in next_runs]
            print(f"      下次执行: {', '.join(next_runs_str)}")
    
    # 秒级6字段表达式
    print("\n秒级6字段 Cron 表达式:")
    second_crons = [
        ("*/5 * * * * *", "每5秒"),
        ("0 * * * * *", "每分钟的第0秒"),
        ("30 * * * * *", "每分钟的第30秒"),
        ("0 0 * * * *", "每小时的第0分0秒"),
        ("0 0 9 * * 1", "每周一上午9:00:00"),
    ]
    
    for cron, desc in second_crons:
        valid, msg = validate_cron(cron)
        status = f"{Colors.GREEN}✓{Colors.END}" if valid else f"{Colors.RED}✗{Colors.END}"
        print(f"  {status} `{cron}` - {desc}")
        if valid:
            next_runs = get_next_runs(cron, count=3)
            next_runs_str = [r.strftime('%Y-%m-%d %H:%M:%S') if hasattr(r, 'strftime') else str(r) for r in next_runs]
            print(f"      下次执行: {', '.join(next_runs_str)}")
    
    # 无效表达式
    print("\n无效 Cron 表达式:")
    invalid_crons = [
        ("invalid", "完全无效"),
        ("99 99 * * *", "超出范围"),
        ("* * *", "字段不足"),
        ("", "空字符串"),
    ]
    
    for cron, desc in invalid_crons:
        valid, msg = validate_cron(cron)
        status = f"{Colors.GREEN}✓{Colors.END}" if not valid else f"{Colors.RED}✗{Colors.END}"
        print(f"  {status} `{cron}` - {desc}: {msg}")


async def demo_job_crud():
    """演示任务 CRUD 操作"""
    print_section("2. 定时任务 CRUD 操作")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建调度器
        scheduler = Scheduler(storage_dir=tmpdir)
        await scheduler.initialize()
        
        # 创建任务
        print("创建任务:")
        jobs_created = []
        
        tasks = [
            ("0 9 * * *", "生成每日报告"),
            ("0 10 * * 1", "生成周报"),
            ("0 9 1 * *", "生成月报"),
        ]
        
        for cron, desc in tasks:
            success, msg, job = await scheduler.add_job(
                cron=cron,
                description=desc,
                user_id="user_demo",
                chat_id="chat_demo",
            )
            if success:
                print_success(f"创建任务: {job.id} - {desc}")
                jobs_created.append(job)
            else:
                print_error(f"创建失败: {msg}")
        
        # 列出任务
        print("\n任务列表:")
        jobs = await scheduler.list_jobs()
        for i, job in enumerate(jobs, 1):
            status = "🟢" if job.is_active else "🔴"
            print(f"  {i}. {status} {job.id}")
            print(f"     描述: {job.description}")
            print(f"     Cron: {job.cron}")
        
        # 切换任务状态
        if jobs_created:
            job = jobs_created[0]
            print(f"\n切换任务状态: {job.id}")
            success, msg = await scheduler.toggle_job(job.id)
            print_info(msg)
            
            # 验证状态变更
            updated_job = await scheduler.get_job(job.id)
            print(f"  当前状态: {'激活' if updated_job.is_active else '暂停'}")
            
            # 切换回来
            await scheduler.toggle_job(job.id)
        
        # 删除任务
        if jobs_created:
            job = jobs_created[-1]
            print(f"\n删除任务: {job.id}")
            success, msg = await scheduler.remove_job(job.id)
            if success:
                print_success(msg)
            else:
                print_error(msg)
        
        # 最终任务列表
        print("\n最终任务列表:")
        jobs = await scheduler.list_jobs()
        print(f"  共 {len(jobs)} 个任务")
        
        await scheduler.stop()


async def demo_storage():
    """演示存储功能"""
    print_section("3. 数据持久化")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建存储
        job_store = JobStore(tmpdir)
        pending_store = PendingResultStore(tmpdir)
        
        # 创建任务
        print("保存任务到存储:")
        job = ScheduledJob(
            id="job_test_001",
            user_id="user_001",
            chat_id="chat_001",
            cron="0 9 * * *",
            description="测试任务",
        )
        await job_store.save(job)
        print_success(f"保存任务: {job.id}")
        
        # 从存储加载
        print("\n从存储加载任务:")
        loaded_job = await job_store.get("job_test_001")
        if loaded_job:
            print_success(f"加载成功: {loaded_job.id} - {loaded_job.description}")
        
        # 保存等待通知
        print("\n保存等待通知:")
        result = ScheduledResult(
            job_id="job_test_001",
            success=True,
            output="任务执行成功！",
        )
        notification = PendingNotification(
            result=result,
            chat_id="chat_001",
            user_id="user_001",
        )
        await pending_store.save("chat_001", notification)
        print_success(f"保存通知到: chat_001")
        
        # 加载等待通知
        print("\n加载等待通知:")
        notifications = await pending_store.load("chat_001")
        print(f"  共 {len(notifications)} 个通知")
        for n in notifications:
            print(f"  - 任务: {n.result.job_id}, 成功: {n.result.success}")


async def demo_models():
    """演示数据模型"""
    print_section("4. 数据模型")
    
    # ScheduledJob
    print("ScheduledJob 模型:")
    job = ScheduledJob(
        id="job_abc123",
        user_id="user_001",
        chat_id="chat_001",
        cron="0 9 * * *",
        description="每日报告",
        is_active=True,
    )
    job_dict = job.to_dict()
    print(f"  原始对象: {job}")
    print(f"  序列化: {job_dict}")
    
    restored_job = ScheduledJob.from_dict(job_dict)
    print(f"  反序列化: {restored_job.id}")
    
    # IncomingMessage
    print("\nIncomingMessage 模型:")
    message = IncomingMessage(
        text="生成今日报告",
        source="scheduled",
        source_id="job_abc123",
        chat_id="chat_001",
        user_id="user_001",
    )
    msg_dict = message.to_dict()
    print(f"  消息内容: {message.text}")
    print(f"  来源: {message.source}")
    
    # ScheduledResult
    print("\nScheduledResult 模型:")
    result = ScheduledResult(
        job_id="job_abc123",
        success=True,
        output="报告生成完成！\n共处理 100 条记录。",
        files=["/path/to/report.pdf"],
    )
    print(f"  格式化的成功消息:")
    print(f"    {result.format_message().replace(chr(10), chr(10) + '    ')}")
    
    error_result = ScheduledResult(
        job_id="job_abc123",
        success=False,
        error="连接数据库失败",
    )
    print(f"\n  格式化的错误消息:")
    print(f"    {error_result.format_message().replace(chr(10), chr(10) + '    ')}")


async def demo_cron_engine():
    """演示 Cron 引擎"""
    print_section("5. Cron 引擎")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        job_store = JobStore(tmpdir)
        
        # 创建任务
        job = ScheduledJob(
            id="job_engine_test",
            user_id="user_001",
            chat_id="chat_001",
            cron="* * * * *",  # 每分钟
            description="引擎测试任务",
            is_active=True,
        )
        await job_store.save(job)
        
        # 创建引擎
        triggered_jobs = []
        
        def on_trigger(job: ScheduledJob):
            triggered_jobs.append(job.id)
            print(f"  触发任务: {job.id}")
        
        engine = CronEngine(
            job_store=job_store,
            on_trigger=on_trigger,
            check_interval=1.0,
        )
        
        print("启动 Cron 引擎 (检查间隔: 1秒)...")
        await engine.start()
        
        # 等待引擎检查几次
        print("  等待 3 秒...")
        await asyncio.sleep(3)
        
        print("停止 Cron 引擎...")
        await engine.stop()
        
        print(f"\n引擎状态:")
        print(f"  运行中: {engine.is_running()}")
        print(f"  触发任务数: {len(triggered_jobs)}")


async def demo_commands():
    """演示命令处理"""
    print_section("6. 命令处理")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        scheduler = Scheduler(storage_dir=tmpdir)
        await scheduler.initialize()
        
        # help 命令
        print("帮助命令:")
        help_text = await scheduler.handle_cron_help_command()
        print(f"  帮助内容长度: {len(help_text)} 字符")
        print(f"  包含 '定时任务帮助': {'定时任务帮助' in help_text}")
        
        # add 命令
        print("\n添加任务:")
        result = await scheduler.handle_cron_add_command(
            cron="0 9 * * *",
            description="命令测试任务",
            user_id="user_test",
            chat_id="chat_test",
        )
        print(result[:200] + "...")
        
        # list 命令
        print("\n列出任务:")
        result = await scheduler.handle_cron_list_command(chat_id="chat_test")
        print(result)
        
        await scheduler.stop()


async def main():
    """主函数"""
    print(f"{Colors.GREEN}")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║              OKbot 定时任务功能完整测试                      ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"{Colors.END}")
    print(f"\n测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        await demo_cron_validation()
        await demo_job_crud()
        await demo_storage()
        await demo_models()
        await demo_cron_engine()
        await demo_commands()
        
        print_section("测试完成")
        print_success("所有演示成功完成！")
        
    except Exception as e:
        print_error(f"测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
