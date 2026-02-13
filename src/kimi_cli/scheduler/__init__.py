"""Scheduler module for OKbot.

This module provides scheduled task functionality for OKbot, including:
- Cron-based job scheduling
- Silent task execution with independent sessions
- Queue-based notification delivery
- Unified message dispatching
"""

from kimi_cli.scheduler.commands import handle_cron_command
from kimi_cli.scheduler.cron_engine import CronEngine, get_next_runs, validate_cron
from kimi_cli.scheduler.dispatcher import MessageDispatcher, SessionManager
from kimi_cli.scheduler.models import (
    IncomingMessage,
    NotificationMode,
    PendingNotification,
    ScheduledJob,
    ScheduledResult,
)
from kimi_cli.scheduler.scheduler import Scheduler, get_scheduler, set_scheduler
from kimi_cli.scheduler.session import ScheduledTaskSession
from kimi_cli.scheduler.store import JobStore, PendingResultStore

__all__ = [
    # Main scheduler
    "Scheduler",
    "get_scheduler",
    "set_scheduler",
    
    # Models
    "ScheduledJob",
    "IncomingMessage",
    "ScheduledResult",
    "PendingNotification",
    "NotificationMode",
    
    # Engine
    "CronEngine",
    "validate_cron",
    "get_next_runs",
    
    # Dispatcher
    "MessageDispatcher",
    "SessionManager",
    
    # Session
    "ScheduledTaskSession",
    
    # Storage
    "JobStore",
    "PendingResultStore",
    
    # Commands
    "handle_cron_command",
]
