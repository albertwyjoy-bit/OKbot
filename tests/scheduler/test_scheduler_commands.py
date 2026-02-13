#!/usr/bin/env python3
"""定时任务命令处理测试脚本"""

import asyncio
import tempfile
import os
import sys
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kimi_cli.scheduler.commands import CronCommandHandler, handle_cron_command
from kimi_cli.scheduler.scheduler import Scheduler, get_scheduler, set_scheduler


class MockSDKChatSession:
    """模拟 SDKChatSession"""
    
    def __init__(self, chat_id: str = "chat_001", user_id: str = "user_001"):
        self.chat_id = chat_id
        self.user_id = user_id
        self.config = None


async def test_cron_commands():
    """测试 Cron 命令处理"""
    print("=" * 60)
    print("测试 Cron 命令处理")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建并设置调度器
        scheduler = Scheduler(storage_dir=tmpdir)
        set_scheduler(scheduler)
        await scheduler.initialize()
        
        # 创建模拟会话
        session = MockSDKChatSession()
        
        print("\n=== 测试 help 命令 ===")
        handled, response = await handle_cron_command("/cron help", session)
        print(f"已处理: {handled}")
        print(f"响应:\n{response[:500]}...")
        
        print("\n=== 测试 list 命令（空列表）===")
        handled, response = await handle_cron_command("/cron list", session)
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        print("\n=== 测试 add 命令 ===")
        # 带引号的参数
        handled, response = await handle_cron_command(
            '/cron add "0 9 * * *" "生成日报"',
            session
        )
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        # 获取刚创建的任务ID
        jobs = await scheduler.list_jobs(chat_id="chat_001")
        job_id = jobs[0].id if jobs else None
        
        print("\n=== 测试 list 命令（有任务）===")
        handled, response = await handle_cron_command("/cron list", session)
        print(f"已处理: {handled}")
        print(f"响应:\n{response}")
        
        if job_id:
            print("\n=== 测试 toggle 命令 ===")
            handled, response = await handle_cron_command(f"/cron toggle {job_id}", session)
            print(f"已处理: {handled}")
            print(f"响应: {response}")
            
            print("\n=== 测试 history 命令 ===")
            handled, response = await handle_cron_command("/cron history", session)
            print(f"已处理: {handled}")
            print(f"响应: {response}")
            
            print("\n=== 测试 remove 命令 ===")
            handled, response = await handle_cron_command(f"/cron remove {job_id}", session)
            print(f"已处理: {handled}")
            print(f"响应: {response}")
        
        print("\n=== 测试 add 命令（无效参数）===")
        handled, response = await handle_cron_command("/cron add", session)
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        print("\n=== 测试 add 命令（无效 Cron）===")
        handled, response = await handle_cron_command(
            '/cron add "invalid" "测试"',
            session
        )
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        print("\n=== 测试 remove 命令（无效ID）===")
        handled, response = await handle_cron_command("/cron remove invalid_id", session)
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        print("\n=== 测试未知命令 ===")
        handled, response = await handle_cron_command("/cron unknown", session)
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        print("\n=== 测试非 /cron 命令 ===")
        handled, response = await handle_cron_command("hello world", session)
        print(f"已处理: {handled}")
        print(f"响应: {response}")
        
        await scheduler.stop()
    
    print("\n" + "=" * 60)
    print("命令处理测试完成！")
    print("=" * 60)


def test_parse_add_args():
    """测试 add 命令参数解析"""
    print("\n" + "=" * 60)
    print("测试 add 命令参数解析")
    print("=" * 60)
    
    test_cases = [
        ('"0 9 * * *" "生成日报"', ("0 9 * * *", "生成日报")),
        ("'0 9 * * *' '生成日报'", ("0 9 * * *", "生成日报")),
        ('"0 9 * * *" 生成日报', ("0 9 * * *", "生成日报")),
        ("0 9 * * * 生成日报", ("0 9 * * *", "生成日报")),
        ('"0 9 * * *"', (None, None)),  # 缺少描述
        ("", (None, None)),  # 空参数
    ]
    
    for args_str, expected in test_cases:
        result = CronCommandHandler._parse_add_args(args_str)
        status = "✅" if result == expected else "❌"
        print(f"{status} 输入: {args_str!r}")
        print(f"   结果: {result}")
        print(f"   预期: {expected}")


async def test_cron_variations():
    """测试各种 Cron 表达式变体"""
    print("\n" + "=" * 60)
    print("测试 Cron 表达式变体")
    print("=" * 60)
    
    from kimi_cli.scheduler import validate_cron, get_next_runs
    
    cron_examples = [
        ("*/10 * * * * *", "每10秒"),
        ("0 */5 * * * *", "每5分钟"),
        ("0 0 * * * *", "每小时"),
        ("0 0 9 * * *", "每天9点"),
        ("0 0 9 * * 1", "每周一9点"),
        ("0 0 9 1 * *", "每月1日9点"),
        ("0 0 9 1 1 *", "每年1月1日9点"),
        ("0 0 9,18 * * *", "每天9点和18点"),
        ("0 0 9-18 * * 1-5", "工作日9-18点每小时"),
        ("0 0 L * * *", "每月最后一天"),
    ]
    
    for cron, description in cron_examples:
        valid, message = validate_cron(cron)
        if valid:
            next_runs = get_next_runs(cron, count=1)
            next_time = next_runs[0].strftime("%Y-%m-%d %H:%M:%S") if next_runs else "未知"
            print(f"✅ {description}")
            print(f"   Cron: {cron}")
            print(f"   下次执行: {next_time}")
        else:
            print(f"❌ {description} - {message}")


async def main():
    """主测试函数"""
    await test_cron_commands()
    test_parse_add_args()
    await test_cron_variations()


if __name__ == "__main__":
    asyncio.run(main())
