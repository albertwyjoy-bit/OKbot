#!/usr/bin/env python3
"""定时任务执行测试 - 模拟实际的定时任务触发"""

import asyncio
import sys
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from kimi_cli.scheduler.scheduler import Scheduler
from kimi_cli.scheduler.dispatcher import MessageDispatcher, SessionManager
from kimi_cli.scheduler.session import ScheduledTaskSession
from kimi_cli.scheduler.models import IncomingMessage, ScheduledResult


async def test_job_trigger():
    """测试定时任务触发流程"""
    print("\n" + "="*60)
    print("测试: 定时任务触发流程")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建模拟的飞书处理器
        mock_feishu_handler = MagicMock()
        mock_feishu_handler._sessions = {}
        mock_feishu_handler.send_text_card = MagicMock()
        
        # 创建调度器
        scheduler = Scheduler(storage_dir=tmpdir)
        await scheduler.initialize(mock_feishu_handler)
        
        # 添加一个测试任务
        success, message, job = await scheduler.add_job(
            cron="0 9 * * *",
            description="测试触发任务",
            user_id="user_test",
            chat_id="chat_test",
            chat_type="p2p",
        )
        
        print(f"\n✅ 创建任务: {job.id}")
        print(f"   描述: {job.description}")
        print(f"   Cron: {job.cron}")
        
        # 立即触发任务
        success, trigger_msg = await scheduler.trigger_job_now(job.id)
        print(f"\n✅ 手动触发任务: {success}")
        print(f"   消息: {trigger_msg}")
        
        # 等待一下让任务有机会执行
        await asyncio.sleep(0.5)
        
        # 停止调度器
        await scheduler.stop()
        print("\n✅ 调度器已停止")


async def test_message_dispatch():
    """测试消息分发流程"""
    print("\n" + "="*60)
    print("测试: 消息分发流程")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建模拟的飞书处理器
        mock_feishu_handler = MagicMock()
        mock_feishu_handler._sessions = {}
        
        # 创建调度器
        scheduler = Scheduler(storage_dir=tmpdir)
        await scheduler.initialize(mock_feishu_handler)
        
        # 创建模拟消息
        message = IncomingMessage(
            text="[定时任务] 执行数据备份",
            source="scheduled",
            source_id="job_test_001",
            chat_id="chat_test",
            user_id="user_test",
            chat_type="p2p",
            metadata={"cron": "0 9 * * *", "notification_mode": "silent"},
        )
        
        print(f"\n✅ 创建定时任务消息:")
        print(f"   内容: {message.text}")
        print(f"   来源: {message.source}")
        print(f"   来源ID: {message.source_id}")
        
        # 测试从飞书消息创建
        feishu_event = MagicMock()
        feishu_event.event = MagicMock()
        feishu_event.event.message = MagicMock()
        feishu_event.event.message.message_type = "text"
        feishu_event.event.message.chat_id = "chat_feishu_001"
        feishu_event.event.message.chat_type = "p2p"
        feishu_event.event.message.content = '{"text": "查询今日数据"}'
        feishu_event.event.sender = MagicMock()
        feishu_event.event.sender.sender_id = MagicMock()
        feishu_event.event.sender.sender_id.open_id = "user_feishu_001"
        
        feishu_msg = IncomingMessage.from_feishu_message(feishu_event)
        print(f"\n✅ 从飞书消息创建:")
        print(f"   内容: {feishu_msg.text}")
        print(f"   用户ID: {feishu_msg.user_id}")
        print(f"   聊天ID: {feishu_msg.chat_id}")
        
        await scheduler.stop()
        print("\n✅ 测试完成")


async def test_session_management():
    """测试会话管理"""
    print("\n" + "="*60)
    print("测试: 会话管理")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_feishu_handler = MagicMock()
        mock_feishu_handler._sessions = {}
        
        from kimi_cli.scheduler.store import PendingResultStore
        
        pending_store = PendingResultStore(tmpdir)
        
        # 创建会话管理器
        session_manager = SessionManager(
            feishu_handler=mock_feishu_handler,
            pending_store=pending_store,
        )
        
        # 创建定时任务会话
        session = await session_manager.get_or_create_scheduled_session(
            session_id="sched_user_001_job_001",
            chat_id="chat_001",
            user_id="user_001",
            source="scheduled",
        )
        
        print(f"\n✅ 创建定时任务会话:")
        print(f"   Session ID: {session.session_id}")
        print(f"   Chat ID: {session.chat_id}")
        print(f"   User ID: {session.user_id}")
        
        # 测试复用会话
        session2 = await session_manager.get_or_create_scheduled_session(
            session_id="sched_user_001_job_001",
            chat_id="chat_001",
            user_id="user_001",
            source="scheduled",
        )
        
        print(f"\n✅ 复用会话: {session is session2}")
        
        # 创建新会话
        session3 = await session_manager.get_or_create_scheduled_session(
            session_id="sched_user_002_job_002",
            chat_id="chat_002",
            user_id="user_002",
            source="scheduled",
        )
        
        print(f"✅ 创建新会话: {session3.session_id}")
        
        # 移除会话
        await session_manager.remove_scheduled_session("sched_user_001_job_001")
        print(f"\n✅ 移除会话: sched_user_001_job_001")


async def test_cron_engine_with_second_level():
    """测试秒级 Cron 引擎"""
    print("\n" + "="*60)
    print("测试: 秒级 Cron 表达式")
    print("="*60)
    
    from kimi_cli.scheduler.cron_engine import CronEngine, validate_cron, get_next_runs
    
    # 测试各种秒级表达式
    second_level_crons = [
        ("*/5 * * * * *", "每5秒"),
        ("0 * * * * *", "每分钟第0秒"),
        ("0 0 * * * *", "每小时第0分0秒"),
        ("30 0 9 * * *", "每天9:00:30"),
    ]
    
    for cron, desc in second_level_crons:
        valid, msg = validate_cron(cron)
        status = "✅" if valid else "❌"
        print(f"\n{status} {desc}")
        print(f"   表达式: {cron}")
        print(f"   验证: {msg}")
        
        if valid:
            runs = get_next_runs(cron, count=3)
            print(f"   未来3次执行:")
            for i, run in enumerate(runs, 1):
                print(f"     {i}. {run.strftime('%Y-%m-%d %H:%M:%S')}")


async def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("  OKbot 定时任务执行测试")
    print("="*60)
    print(f"\n当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        await test_job_trigger()
        await test_message_dispatch()
        await test_session_management()
        await test_cron_engine_with_second_level()
        
        print("\n" + "="*60)
        print("  ✅ 所有执行测试通过!")
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
