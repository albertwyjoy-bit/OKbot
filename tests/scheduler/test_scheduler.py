"""Tests for the main scheduler module."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kimi_cli.scheduler.models import IncomingMessage, ScheduledJob
from kimi_cli.scheduler.scheduler import Scheduler, get_scheduler, set_scheduler


class TestSchedulerBasics:
    """Tests for basic scheduler functionality."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """Create a temporary storage directory."""
        return tmp_path / "scheduler"

    @pytest.fixture
    def scheduler(self, temp_storage_dir):
        """Create a scheduler instance."""
        return Scheduler(storage_dir=str(temp_storage_dir))

    @pytest.fixture
    def mock_feishu_handler(self):
        """Create a mock Feishu handler."""
        return MagicMock()

    async def test_scheduler_initialization(self, scheduler, mock_feishu_handler):
        """Test scheduler initialization."""
        await scheduler.initialize(mock_feishu_handler)
        
        assert scheduler._initialized is True
        assert scheduler._feishu_handler == mock_feishu_handler
        assert scheduler._cron_engine is not None

    async def test_initialize_without_handler(self, scheduler):
        """Test initialization without handler."""
        await scheduler.initialize()
        
        assert scheduler._initialized is True
        assert scheduler._feishu_handler is None
        # Dispatcher and session manager should not be created
        assert scheduler._dispatcher is None

    async def test_double_initialize(self, scheduler, mock_feishu_handler):
        """Test that double initialization is safe."""
        await scheduler.initialize(mock_feishu_handler)
        await scheduler.initialize(mock_feishu_handler)  # Should not raise
        
        assert scheduler._initialized is True

    async def test_start_before_initialize(self, scheduler):
        """Test that starting before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await scheduler.start()

    async def test_start_stop(self, scheduler, mock_feishu_handler):
        """Test starting and stopping the scheduler."""
        await scheduler.initialize(mock_feishu_handler)
        
        # Mock the cron engine
        scheduler._cron_engine = MagicMock()
        scheduler._cron_engine.start = AsyncMock()
        scheduler._cron_engine.stop = AsyncMock()
        
        await scheduler.start()
        scheduler._cron_engine.start.assert_called_once()
        
        await scheduler.stop()
        scheduler._cron_engine.stop.assert_called_once()


class TestSchedulerJobManagement:
    """Tests for job management operations."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """Create a temporary storage directory."""
        return tmp_path / "scheduler"

    @pytest.fixture
    async def initialized_scheduler(self, temp_storage_dir):
        """Create and initialize a scheduler."""
        scheduler = Scheduler(storage_dir=str(temp_storage_dir))
        await scheduler.initialize()
        return scheduler

    async def test_add_valid_job(self, initialized_scheduler):
        """Test adding a valid job."""
        success, message, job = await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Daily report",
            user_id="user_123",
            chat_id="chat_456",
        )
        
        assert success is True
        assert job is not None
        assert job.description == "Daily report"
        assert job.cron == "0 9 * * *"
        assert job.id.startswith("job_")

    async def test_add_invalid_cron(self, initialized_scheduler):
        """Test adding a job with invalid cron."""
        success, message, job = await initialized_scheduler.add_job(
            cron="invalid cron",
            description="Test",
            user_id="user_123",
            chat_id="chat_456",
        )
        
        assert success is False
        assert job is None
        assert "无效" in message or "Invalid" in message

    async def test_remove_existing_job(self, initialized_scheduler):
        """Test removing an existing job."""
        # Add a job first
        success, _, job = await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Test job",
            user_id="user_123",
            chat_id="chat_456",
        )
        assert success is True
        job_id = job.id
        
        # Remove it
        success, message = await initialized_scheduler.remove_job(job_id)
        
        assert success is True
        assert job_id in message

    async def test_remove_nonexistent_job(self, initialized_scheduler):
        """Test removing a non-existent job."""
        success, message = await initialized_scheduler.remove_job("nonexistent")
        
        assert success is False
        assert "不存在" in message or "not found" in message

    async def test_get_job(self, initialized_scheduler):
        """Test getting a job."""
        # Add a job
        success, _, job = await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Test job",
            user_id="user_123",
            chat_id="chat_456",
        )
        job_id = job.id
        
        # Get it
        retrieved = await initialized_scheduler.get_job(job_id)
        
        assert retrieved is not None
        assert retrieved.id == job_id
        assert retrieved.description == "Test job"

    async def test_get_nonexistent_job(self, initialized_scheduler):
        """Test getting a non-existent job."""
        result = await initialized_scheduler.get_job("nonexistent")
        
        assert result is None

    async def test_list_jobs(self, initialized_scheduler):
        """Test listing all jobs."""
        # Add multiple jobs
        await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Job 1",
            user_id="user_1",
            chat_id="chat_1",
        )
        await initialized_scheduler.add_job(
            cron="0 10 * * *",
            description="Job 2",
            user_id="user_2",
            chat_id="chat_2",
        )
        
        jobs = await initialized_scheduler.list_jobs()
        
        assert len(jobs) == 2

    async def test_list_jobs_by_chat(self, initialized_scheduler):
        """Test listing jobs filtered by chat."""
        # Add jobs to different chats
        await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Chat A Job",
            user_id="user_1",
            chat_id="chat_a",
        )
        await initialized_scheduler.add_job(
            cron="0 10 * * *",
            description="Chat B Job",
            user_id="user_2",
            chat_id="chat_b",
        )
        
        jobs = await initialized_scheduler.list_jobs(chat_id="chat_a")
        
        assert len(jobs) == 1
        assert jobs[0].chat_id == "chat_a"

    async def test_list_jobs_by_user(self, initialized_scheduler):
        """Test listing jobs filtered by user."""
        # Add jobs for different users
        await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="User X Job",
            user_id="user_x",
            chat_id="chat_1",
        )
        await initialized_scheduler.add_job(
            cron="0 10 * * *",
            description="User Y Job",
            user_id="user_y",
            chat_id="chat_2",
        )
        
        jobs = await initialized_scheduler.list_jobs(user_id="user_x")
        
        assert len(jobs) == 1
        assert jobs[0].user_id == "user_x"

    async def test_toggle_job(self, initialized_scheduler):
        """Test toggling job active state."""
        # Add a job
        success, _, job = await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Test job",
            user_id="user_123",
            chat_id="chat_456",
        )
        job_id = job.id
        assert job.is_active is True
        
        # Toggle off
        success, message = await initialized_scheduler.toggle_job(job_id)
        assert success is True
        assert "暂停" in message
        
        # Verify it's inactive
        job = await initialized_scheduler.get_job(job_id)
        assert job.is_active is False
        
        # Toggle on
        success, message = await initialized_scheduler.toggle_job(job_id)
        assert success is True
        assert "激活" in message
        
        # Verify it's active
        job = await initialized_scheduler.get_job(job_id)
        assert job.is_active is True

    async def test_toggle_nonexistent_job(self, initialized_scheduler):
        """Test toggling a non-existent job."""
        success, message = await initialized_scheduler.toggle_job("nonexistent")
        
        assert success is False
        assert "不存在" in message


class TestSchedulerCommands:
    """Tests for command handlers."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """Create a temporary storage directory."""
        return tmp_path / "scheduler"

    @pytest.fixture
    async def initialized_scheduler(self, temp_storage_dir):
        """Create and initialize a scheduler."""
        scheduler = Scheduler(storage_dir=str(temp_storage_dir))
        await scheduler.initialize()
        return scheduler

    async def test_handle_cron_add_command(self, initialized_scheduler):
        """Test handling cron add command."""
        result = await initialized_scheduler.handle_cron_add_command(
            cron="0 9 * * *",
            description="Daily task",
            user_id="user_123",
            chat_id="chat_456",
        )
        
        assert "✅" in result
        assert "Daily task" in result
        assert "0 9 * * *" in result

    async def test_handle_cron_add_command_invalid(self, initialized_scheduler):
        """Test handling cron add command with invalid cron."""
        result = await initialized_scheduler.handle_cron_add_command(
            cron="invalid",
            description="Test",
            user_id="user_123",
            chat_id="chat_456",
        )
        
        assert "❌" in result

    async def test_handle_cron_list_command_empty(self, initialized_scheduler):
        """Test handling cron list command with no jobs."""
        result = await initialized_scheduler.handle_cron_list_command()
        
        assert "暂无定时任务" in result
        assert "/cron add" in result

    async def test_handle_cron_list_command_with_jobs(self, initialized_scheduler):
        """Test handling cron list command with jobs."""
        # Add a job
        await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Test job",
            user_id="user_123",
            chat_id="chat_456",
        )
        
        result = await initialized_scheduler.handle_cron_list_command()
        
        assert "定时任务列表" in result
        assert "Test job" in result
        assert "/cron remove" in result

    async def test_handle_cron_remove_command(self, initialized_scheduler):
        """Test handling cron remove command."""
        # Add a job
        success, _, job = await initialized_scheduler.add_job(
            cron="0 9 * * *",
            description="Test job",
            user_id="user_123",
            chat_id="chat_456",
        )
        job_id = job.id
        
        result = await initialized_scheduler.handle_cron_remove_command(job_id)
        
        assert "✅" in result
        assert "已删除" in result

    async def test_handle_cron_remove_command_invalid(self, initialized_scheduler):
        """Test handling cron remove command with invalid ID."""
        result = await initialized_scheduler.handle_cron_remove_command("nonexistent")
        
        assert "❌" in result
        assert "不存在" in result

    async def test_handle_cron_help_command(self, initialized_scheduler):
        """Test handling cron help command."""
        result = await initialized_scheduler.handle_cron_help_command()
        
        assert "定时任务帮助" in result
        assert "/cron add" in result
        assert "/cron list" in result
        assert "/cron remove" in result
        assert "Cron 表达式" in result


class TestGlobalScheduler:
    """Tests for global scheduler instance."""

    def test_get_scheduler_creates_instance(self):
        """Test that get_scheduler creates a new instance."""
        scheduler = get_scheduler()
        
        assert scheduler is not None
        assert isinstance(scheduler, Scheduler)

    def test_get_scheduler_returns_same_instance(self):
        """Test that get_scheduler returns the same instance."""
        scheduler1 = get_scheduler()
        scheduler2 = get_scheduler()
        
        assert scheduler1 is scheduler2

    def test_set_scheduler(self):
        """Test setting a custom scheduler instance."""
        custom_scheduler = Scheduler()
        
        set_scheduler(custom_scheduler)
        
        assert get_scheduler() is custom_scheduler

    def test_set_scheduler_overwrites(self):
        """Test that set_scheduler overwrites the existing instance."""
        original = get_scheduler()
        new_scheduler = Scheduler()
        
        set_scheduler(new_scheduler)
        
        assert get_scheduler() is new_scheduler
        assert get_scheduler() is not original
