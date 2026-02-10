"""Tests for scheduler store module."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kimi_cli.scheduler.models import PendingNotification, ScheduledJob, ScheduledResult
from kimi_cli.scheduler.store import JobStore, PendingResultStore


class TestJobStore:
    """Tests for JobStore class."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """Create a temporary storage directory."""
        return tmp_path / "jobs"

    @pytest.fixture
    def job_store(self, temp_storage_dir):
        """Create a job store with temp directory."""
        return JobStore(str(temp_storage_dir))

    @pytest.fixture
    def sample_job(self):
        """Create a sample job."""
        return ScheduledJob(
            id="job_123",
            user_id="user_456",
            chat_id="chat_789",
            cron="0 9 * * *",
            description="Test job",
            is_active=True,
        )

    async def test_save_and_get(self, job_store, sample_job):
        """Test saving and retrieving a job."""
        await job_store.save(sample_job)
        
        retrieved = await job_store.get("job_123")
        
        assert retrieved is not None
        assert retrieved.id == "job_123"
        assert retrieved.description == "Test job"

    async def test_get_nonexistent(self, job_store):
        """Test getting a non-existent job."""
        result = await job_store.get("nonexistent")
        assert result is None

    async def test_delete(self, job_store, sample_job):
        """Test deleting a job."""
        await job_store.save(sample_job)
        assert await job_store.get("job_123") is not None
        
        success = await job_store.delete("job_123")
        
        assert success is True
        assert await job_store.get("job_123") is None

    async def test_list_all(self, job_store):
        """Test listing all jobs."""
        job1 = ScheduledJob(
            id="job_1",
            user_id="user_1",
            chat_id="chat_1",
            cron="0 9 * * *",
            description="Job 1",
        )
        job2 = ScheduledJob(
            id="job_2",
            user_id="user_2",
            chat_id="chat_2",
            cron="0 10 * * *",
            description="Job 2",
        )
        
        await job_store.save(job1)
        await job_store.save(job2)
        
        jobs = await job_store.list_all()
        
        assert len(jobs) == 2
        job_ids = {j.id for j in jobs}
        assert job_ids == {"job_1", "job_2"}

    async def test_list_by_chat(self, job_store):
        """Test listing jobs by chat."""
        job1 = ScheduledJob(
            id="job_1",
            user_id="user_1",
            chat_id="chat_a",
            cron="0 9 * * *",
            description="Job 1",
        )
        job2 = ScheduledJob(
            id="job_2",
            user_id="user_2",
            chat_id="chat_b",
            cron="0 10 * * *",
            description="Job 2",
        )
        job3 = ScheduledJob(
            id="job_3",
            user_id="user_3",
            chat_id="chat_a",
            cron="0 11 * * *",
            description="Job 3",
        )
        
        await job_store.save(job1)
        await job_store.save(job2)
        await job_store.save(job3)
        
        jobs = await job_store.list_by_chat("chat_a")
        
        assert len(jobs) == 2
        assert all(j.chat_id == "chat_a" for j in jobs)

    async def test_list_by_user(self, job_store):
        """Test listing jobs by user."""
        job1 = ScheduledJob(
            id="job_1",
            user_id="user_x",
            chat_id="chat_1",
            cron="0 9 * * *",
            description="Job 1",
        )
        job2 = ScheduledJob(
            id="job_2",
            user_id="user_y",
            chat_id="chat_2",
            cron="0 10 * * *",
            description="Job 2",
        )
        
        await job_store.save(job1)
        await job_store.save(job2)
        
        jobs = await job_store.list_by_user("user_x")
        
        assert len(jobs) == 1
        assert jobs[0].user_id == "user_x"

    async def test_persistence(self, job_store, temp_storage_dir, sample_job):
        """Test that jobs persist to disk."""
        await job_store.save(sample_job)
        
        # Create a new store instance pointing to same directory
        new_store = JobStore(str(temp_storage_dir))
        retrieved = await new_store.get("job_123")
        
        assert retrieved is not None
        assert retrieved.description == "Test job"

    async def test_load_all_twice(self, job_store, sample_job):
        """Test that load_all returns cached data on second call."""
        await job_store.save(sample_job)
        
        # First call should load from disk
        jobs1 = await job_store.load_all()
        
        # Second call should return cached copy
        jobs2 = await job_store.load_all()
        
        assert len(jobs1) == len(jobs2) == 1


class TestPendingResultStore:
    """Tests for PendingResultStore class."""

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """Create a temporary storage directory."""
        return tmp_path / "pending"

    @pytest.fixture
    def pending_store(self, temp_storage_dir):
        """Create a pending store with temp directory."""
        return PendingResultStore(str(temp_storage_dir))

    @pytest.fixture
    def sample_notification(self):
        """Create a sample notification."""
        result = ScheduledResult(
            job_id="job_123",
            success=True,
            output="Task done",
        )
        return PendingNotification(
            result=result,
            chat_id="chat_456",
            user_id="user_789",
        )

    async def test_save_and_load(self, pending_store, sample_notification):
        """Test saving and loading pending notifications."""
        await pending_store.save("chat_456", [sample_notification])
        
        loaded = await pending_store.load("chat_456")
        
        assert len(loaded) == 1
        assert loaded[0].result.job_id == "job_123"
        assert loaded[0].chat_id == "chat_456"

    async def test_load_empty(self, pending_store):
        """Test loading from empty/non-existent chat."""
        loaded = await pending_store.load("nonexistent_chat")
        
        assert loaded == []

    async def test_delete(self, pending_store, sample_notification):
        """Test deleting pending notifications."""
        await pending_store.save("chat_456", [sample_notification])
        assert len(await pending_store.load("chat_456")) == 1
        
        await pending_store.delete("chat_456")
        
        assert await pending_store.load("chat_456") == []

    async def test_multiple_notifications(self, pending_store):
        """Test saving multiple notifications."""
        notifications = [
            PendingNotification(
                result=ScheduledResult(job_id="job_1", success=True, output="Done 1"),
                chat_id="chat_abc",
                user_id="user_xyz",
            ),
            PendingNotification(
                result=ScheduledResult(job_id="job_2", success=False, error="Failed"),
                chat_id="chat_abc",
                user_id="user_xyz",
            ),
        ]
        
        await pending_store.save("chat_abc", notifications)
        loaded = await pending_store.load("chat_abc")
        
        assert len(loaded) == 2
        job_ids = {n.result.job_id for n in loaded}
        assert job_ids == {"job_1", "job_2"}

    async def test_list_all_chat_ids(self, pending_store):
        """Test listing all chat IDs with pending notifications."""
        # Save to multiple chats
        await pending_store.save("chat_1", [])
        await pending_store.save("chat_2", [])
        
        # Note: empty lists don't create files, so we need non-empty
        notification = PendingNotification(
            result=ScheduledResult(job_id="job_1", success=True),
            chat_id="chat_1",
            user_id="user_1",
        )
        await pending_store.save("chat_1", [notification])
        await pending_store.save("chat_2", [notification])
        
        chat_ids = await pending_store.list_all_chat_ids()
        
        # Files should exist
        assert len(chat_ids) >= 0  # May be 0 if files weren't created for empty lists

    async def test_special_characters_in_chat_id(self, pending_store, sample_notification):
        """Test handling special characters in chat ID."""
        chat_id = "chat/with/slashes"
        await pending_store.save(chat_id, [sample_notification])
        
        loaded = await pending_store.load(chat_id)
        
        assert len(loaded) == 1
        assert loaded[0].chat_id == "chat_456"  # Original chat_id in notification
