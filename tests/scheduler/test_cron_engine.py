"""Tests for the cron engine module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from kimi_cli.scheduler.cron_engine import CronEngine, get_next_runs, validate_cron
from kimi_cli.scheduler.models import ScheduledJob
from kimi_cli.scheduler.store import JobStore


class TestValidateCron:
    """Tests for validate_cron function."""

    def test_valid_standard_cron(self):
        """Test validation of standard 5-field cron expressions."""
        # Daily at 9:00
        valid, message = validate_cron("0 9 * * *")
        assert valid is True
        assert "有效" in message
        
        # Every 30 minutes
        valid, message = validate_cron("*/30 * * * *")
        assert valid is True
        assert "有效" in message
        
        # Every Monday at 9:00
        valid, message = validate_cron("0 9 * * 1")
        assert valid is True
        assert "有效" in message

    def test_valid_second_level_cron(self):
        """Test validation of 6-field (second-level) cron expressions."""
        # Every 5 seconds
        valid, message = validate_cron("*/5 * * * * *")
        assert valid is True
        assert "秒级" in message
        
        # At 0 seconds of every minute
        valid, message = validate_cron("0 * * * * *")
        assert valid is True
        assert "秒级" in message

    def test_invalid_cron(self):
        """Test validation of invalid cron expressions."""
        # Invalid field
        valid, message = validate_cron("invalid * * * *")
        assert valid is False
        assert "无效" in message
        
        # Too few fields
        valid, message = validate_cron("* * * *")
        assert valid is False
        
        # Too many fields
        valid, message = validate_cron("* * * * * * *")
        assert valid is False

    def test_next_run_calculation(self):
        """Test that next run time is calculated correctly."""
        valid, message = validate_cron("0 9 * * *")
        assert valid is True
        # Message should contain next execution time
        assert "下次执行" in message


class TestGetNextRuns:
    """Tests for get_next_runs function."""

    def test_get_next_runs_standard(self):
        """Test getting next runs for standard cron."""
        runs = get_next_runs("0 9 * * *", count=3)
        assert len(runs) == 3
        # All runs should be at 9:00
        for run in runs:
            assert run.hour == 9
            assert run.minute == 0

    def test_get_next_runs_second_level(self):
        """Test getting next runs for second-level cron."""
        runs = get_next_runs("*/5 * * * * *", count=3)
        assert len(runs) == 3
        # All runs should be multiples of 5 seconds
        for run in runs:
            assert run.second % 5 == 0

    def test_get_next_runs_invalid(self):
        """Test getting next runs for invalid cron."""
        runs = get_next_runs("invalid", count=3)
        assert runs == []


class TestCronEngine:
    """Tests for CronEngine class."""

    @pytest.fixture
    def mock_job_store(self, tmp_path):
        """Create a mock job store."""
        store = MagicMock(spec=JobStore)
        store.list_all = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_trigger(self):
        """Create a mock trigger callback."""
        return MagicMock()

    @pytest.fixture
    def engine(self, mock_job_store, mock_trigger):
        """Create a cron engine instance."""
        return CronEngine(
            job_store=mock_job_store,
            on_trigger=mock_trigger,
            check_interval=0.1,  # Fast check for testing
        )

    async def test_start_stop(self, engine):
        """Test starting and stopping the engine."""
        assert not engine.is_running()
        
        await engine.start()
        assert engine.is_running()
        
        await engine.stop()
        assert not engine.is_running()

    async def test_double_start(self, engine):
        """Test that starting twice doesn't create duplicate tasks."""
        await engine.start()
        await engine.start()  # Should not raise
        assert engine.is_running()
        await engine.stop()

    async def test_check_jobs_triggers_due_job(self, engine, mock_job_store, mock_trigger):
        """Test that due jobs are triggered."""
        # Create a job that should run (never run before)
        job = ScheduledJob(
            id="test_job",
            user_id="user1",
            chat_id="chat1",
            cron="* * * * *",  # Every minute
            description="Test job",
            is_active=True,
            last_run=None,
        )
        mock_job_store.list_all = AsyncMock(return_value=[job])
        mock_job_store.save = AsyncMock()
        
        await engine.start()
        await asyncio.sleep(0.2)  # Wait for check
        await engine.stop()
        
        # Trigger should have been called
        assert mock_trigger.called

    async def test_check_jobs_skips_inactive(self, engine, mock_job_store, mock_trigger):
        """Test that inactive jobs are skipped."""
        job = ScheduledJob(
            id="test_job",
            user_id="user1",
            chat_id="chat1",
            cron="* * * * *",
            description="Test job",
            is_active=False,  # Inactive
            last_run=None,
        )
        mock_job_store.list_all = AsyncMock(return_value=[job])
        
        await engine.start()
        await asyncio.sleep(0.2)
        await engine.stop()
        
        # Trigger should not have been called
        assert not mock_trigger.called

    async def test_check_jobs_skips_recently_run(self, engine, mock_job_store, mock_trigger):
        """Test that recently run jobs are skipped."""
        job = ScheduledJob(
            id="test_job",
            user_id="user1",
            chat_id="chat1",
            cron="* * * * *",
            description="Test job",
            is_active=True,
            last_run=datetime.now(),  # Just ran
        )
        mock_job_store.list_all = AsyncMock(return_value=[job])
        
        await engine.start()
        await asyncio.sleep(0.2)
        await engine.stop()
        
        # Trigger should not have been called
        assert not mock_trigger.called


class TestCronEngineSecondLevel:
    """Tests for second-level cron detection."""

    def test_is_second_level_cron(self):
        """Test detection of second-level cron expressions."""
        engine = CronEngine(
            job_store=MagicMock(),
            on_trigger=MagicMock(),
        )
        
        # 6 fields = second level
        assert engine._is_second_level_cron("*/5 * * * * *") is True
        assert engine._is_second_level_cron("0 * * * * *") is True
        
        # 5 fields = standard
        assert engine._is_second_level_cron("* * * * *") is False
        assert engine._is_second_level_cron("0 9 * * *") is False
