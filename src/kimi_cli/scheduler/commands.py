"""Command handlers for cron commands in Feishu."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from kimi_cli.scheduler.scheduler import get_scheduler

if TYPE_CHECKING:
    from kimi_cli.feishu.sdk_server import SDKChatSession


class CronCommandHandler:
    """处理器 for /cron 命令"""
    
    @staticmethod
    async def handle_command(text: str, session: SDKChatSession) -> tuple[bool, str | None]:
        """处理 /cron 命令
        
        Args:
            text: 消息文本
            session: 当前会话
            
        Returns:
            (是否已处理, 回复消息)
        """
        stripped = text.strip()
        
        # 检查是否是 /cron 命令
        if not stripped.startswith("/cron"):
            return False, None
        
        # 获取调度器
        scheduler = get_scheduler()
        
        # 解析命令
        parts = stripped.split(maxsplit=2)
        
        if len(parts) == 1:
            # 只有 /cron，显示帮助
            return True, await scheduler.handle_cron_help_command()
        
        subcommand = parts[1].lower()
        
        if subcommand == "help":
            return True, await scheduler.handle_cron_help_command()
        
        elif subcommand == "list":
            return True, await scheduler.handle_cron_list_command(
                chat_id=session.chat_id,
                user_id=session.user_id,
            )
        
        elif subcommand == "add":
            if len(parts) < 3:
                return True, "❌ 请提供 Cron 表达式和任务描述\n\n用法: `/cron add \"0 9 * * *\" \"生成日报\"`"
            
            # 解析参数
            args_str = parts[2]
            cron_expr, description = CronCommandHandler._parse_add_args(args_str)
            
            if not cron_expr or not description:
                return True, "❌ 参数格式错误\n\n用法: `/cron add \"0 9 * * *\" \"生成日报\"`"
            
            # 获取租户标识（如果有）
            tenant_key = None
            if hasattr(session, 'config') and session.config:
                tenant_key = getattr(session.config, 'tenant_key', None)
            
            return True, await scheduler.handle_cron_add_command(
                cron=cron_expr,
                description=description,
                user_id=session.user_id,
                chat_id=session.chat_id,
                chat_type="p2p",  # 默认为私聊，可根据需要修改
                tenant_key=tenant_key,
            )
        
        elif subcommand == "remove":
            if len(parts) < 3:
                return True, "❌ 请提供任务ID\n\n用法: `/cron remove <任务ID>`"
            
            job_id = parts[2].strip()
            return True, await scheduler.handle_cron_remove_command(job_id)
        
        elif subcommand == "toggle":
            if len(parts) < 3:
                return True, "❌ 请提供任务ID\n\n用法: `/cron toggle <任务ID>`"
            
            job_id = parts[2].strip()
            success, message = await scheduler.toggle_job(job_id)
            return True, f"{'✅' if success else '❌'} {message}"
        
        elif subcommand == "trigger":
            # 测试命令：立即触发任务
            if len(parts) < 3:
                return True, "❌ 请提供任务ID\n\n用法: `/cron trigger <任务ID>`"
            
            job_id = parts[2].strip()
            success, message = await scheduler.trigger_job_now(job_id)
            return True, f"{'✅' if success else '❌'} {message}"
        
        elif subcommand == "history":
            # 查看任务执行历史
            job_id = None
            if len(parts) >= 3:
                job_id = parts[2].strip()
            
            return True, await scheduler.handle_cron_history_command(
                chat_id=session.chat_id,
                user_id=session.user_id,
                job_id=job_id,
            )
        
        else:
            return True, f"❌ 未知命令: {subcommand}\n\n使用 `/cron help` 查看帮助"
    
    @staticmethod
    def _parse_add_args(args_str: str) -> tuple[str | None, str | None]:
        """解析 add 命令的参数
        
        Args:
            args_str: 参数字符串
            
        Returns:
            (cron表达式, 描述)
        """
        # 尝试匹配带引号的参数
        pattern = r'^["\']([^"\']+)["\']\s+["\']([^"\']+)["\']$'
        match = re.match(pattern, args_str)
        
        if match:
            return match.group(1), match.group(2)
        
        # 尝试匹配不带引号的参数（空格分隔）
        parts = args_str.split(maxsplit=1)
        if len(parts) == 2:
            return parts[0], parts[1]
        
        return None, None


async def handle_cron_command(text: str, session: SDKChatSession) -> tuple[bool, str | None]:
    """处理 /cron 命令的便捷函数
    
    Args:
        text: 消息文本
        session: 当前会话
        
    Returns:
        (是否已处理, 回复消息)
    """
    return await CronCommandHandler.handle_command(text, session)
