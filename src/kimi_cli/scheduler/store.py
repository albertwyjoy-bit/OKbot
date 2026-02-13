"""Storage for scheduled jobs and pending notifications."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from kimi_cli.scheduler.models import PendingNotification, ScheduledJob


class JobStore:
    """定时任务存储"""
    
    def __init__(self, storage_dir: str | None = None):
        """Initialize job store.
        
        Args:
            storage_dir: 存储目录路径，默认为 ~/.kimi/scheduler/jobs
        """
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.kimi/scheduler/jobs")
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, ScheduledJob] = {}
        self._loaded = False
    
    def _get_job_file(self, job_id: str) -> Path:
        """Get storage file path for a job."""
        return self._storage_dir / f"{job_id}.json"
    
    async def load_all(self) -> dict[str, ScheduledJob]:
        """Load all jobs from storage."""
        if self._loaded:
            return self._jobs.copy()
        
        self._jobs = {}
        try:
            for job_file in self._storage_dir.glob("*.json"):
                try:
                    with open(job_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    job = ScheduledJob.from_dict(data)
                    self._jobs[job.id] = job
                    logger.debug(f"Loaded scheduled job: {job.id}")
                except Exception as e:
                    logger.warning(f"Failed to load job from {job_file}: {e}")
        except Exception as e:
            logger.exception(f"Failed to load jobs from storage: {e}")
        
        self._loaded = True
        logger.info(f"Loaded {len(self._jobs)} scheduled jobs")
        return self._jobs.copy()
    
    async def get(self, job_id: str) -> ScheduledJob | None:
        """Get a job by ID."""
        if not self._loaded:
            await self.load_all()
        return self._jobs.get(job_id)
    
    async def save(self, job: ScheduledJob) -> None:
        """Save a job to storage."""
        try:
            job_file = self._get_job_file(job.id)
            with open(job_file, "w", encoding="utf-8") as f:
                json.dump(job.to_dict(), f, ensure_ascii=False, indent=2)
            self._jobs[job.id] = job
            logger.debug(f"Saved scheduled job: {job.id}")
        except Exception as e:
            logger.exception(f"Failed to save job {job.id}: {e}")
            raise
    
    async def delete(self, job_id: str) -> bool:
        """Delete a job from storage."""
        try:
            job_file = self._get_job_file(job_id)
            if job_file.exists():
                job_file.unlink()
            self._jobs.pop(job_id, None)
            logger.info(f"Deleted scheduled job: {job_id}")
            return True
        except Exception as e:
            logger.exception(f"Failed to delete job {job_id}: {e}")
            return False
    
    async def list_all(self) -> list[ScheduledJob]:
        """List all jobs."""
        if not self._loaded:
            await self.load_all()
        return list(self._jobs.values())
    
    async def list_by_chat(self, chat_id: str) -> list[ScheduledJob]:
        """List all jobs for a specific chat."""
        if not self._loaded:
            await self.load_all()
        return [job for job in self._jobs.values() if job.chat_id == chat_id]
    
    async def list_by_user(self, user_id: str) -> list[ScheduledJob]:
        """List all jobs for a specific user."""
        if not self._loaded:
            await self.load_all()
        return [job for job in self._jobs.values() if job.user_id == user_id]


class PendingResultStore:
    """等待发送的结果持久化存储"""
    
    def __init__(self, storage_dir: str | None = None):
        """Initialize pending result store.
        
        Args:
            storage_dir: 存储目录路径，默认为 ~/.kimi/scheduler/pending
        """
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.kimi/scheduler/pending")
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_pending_file(self, chat_id: str) -> Path:
        """Get storage file path for a chat's pending results."""
        # 使用 chat_id 的哈希作为文件名，避免特殊字符问题
        safe_name = chat_id.replace("/", "_").replace("\\", "_")
        return self._storage_dir / f"{safe_name}.json"
    
    async def save(self, chat_id: str, results: list[PendingNotification]) -> None:
        """Save pending results for a chat."""
        try:
            pending_file = self._get_pending_file(chat_id)
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved {len(results)} pending notifications for chat {chat_id}")
        except Exception as e:
            logger.exception(f"Failed to save pending results for chat {chat_id}: {e}")
    
    async def load(self, chat_id: str) -> list[PendingNotification]:
        """Load pending results for a chat."""
        try:
            pending_file = self._get_pending_file(chat_id)
            if not pending_file.exists():
                return []
            
            with open(pending_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            results = [PendingNotification.from_dict(r) for r in data]
            logger.debug(f"Loaded {len(results)} pending notifications for chat {chat_id}")
            return results
        except Exception as e:
            logger.exception(f"Failed to load pending results for chat {chat_id}: {e}")
            return []
    
    async def delete(self, chat_id: str) -> None:
        """Delete pending results for a chat."""
        try:
            pending_file = self._get_pending_file(chat_id)
            if pending_file.exists():
                pending_file.unlink()
            logger.debug(f"Deleted pending notifications for chat {chat_id}")
        except Exception as e:
            logger.exception(f"Failed to delete pending results for chat {chat_id}: {e}")
    
    async def list_all_chat_ids(self) -> list[str]:
        """List all chat IDs that have pending results."""
        try:
            chat_ids = []
            for pending_file in self._storage_dir.glob("*.json"):
                # 从文件名还原 chat_id
                chat_id = pending_file.stem.replace("_", "/")
                chat_ids.append(chat_id)
            return chat_ids
        except Exception as e:
            logger.exception(f"Failed to list pending chats: {e}")
            return []
