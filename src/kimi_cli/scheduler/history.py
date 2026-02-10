"""Job execution history store."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class JobExecutionRecord:
    """任务执行记录"""
    job_id: str
    job_description: str
    success: bool
    output: str | None
    error: str | None
    executed_at: datetime
    chat_id: str
    user_id: str
    
    # 文件信息
    files: list[str] = field(default_factory=list)  # 本地文件路径
    feishu_files: list[dict[str, Any]] = field(default_factory=list)  # 飞书文件信息
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_description": self.job_description,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "executed_at": self.executed_at.isoformat(),
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "files": self.files,
            "feishu_files": self.feishu_files,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobExecutionRecord:
        return cls(
            job_id=data["job_id"],
            job_description=data["job_description"],
            success=data["success"],
            output=data.get("output"),
            error=data.get("error"),
            executed_at=datetime.fromisoformat(data["executed_at"]),
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            files=data.get("files", []),
            feishu_files=data.get("feishu_files", []),
        )
    
    def format_summary(self) -> str:
        """格式化摘要"""
        status = "✅" if self.success else "❌"
        time_str = self.executed_at.strftime("%m-%d %H:%M")
        preview = ""
        if self.success and self.output:
            preview = self.output[:50].replace("\n", " ")
            if len(self.output) > 50:
                preview += "..."
        elif not self.success and self.error:
            preview = f"错误: {self.error[:50]}"
        
        file_info = ""
        if self.feishu_files:
            file_info = f" 📎{len(self.feishu_files)}"
        elif self.files:
            file_info = f" 📄{len(self.files)}"
        
        return f"{status} [{time_str}] {self.job_description}{file_info}\n   {preview}"


class JobHistoryStore:
    """任务执行历史存储"""
    
    def __init__(self, storage_dir: str | None = None):
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.kimi/scheduler/history")
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._max_records_per_job = 10  # 每个任务最多保留10条记录
        self._max_age_days = 7  # 记录保留7天
    
    def _get_history_file(self, chat_id: str) -> Path:
        """获取存储文件路径"""
        safe_name = chat_id.replace("/", "_").replace("\\", "_")
        return self._storage_dir / f"{safe_name}.json"
    
    async def add_record(self, record: JobExecutionRecord) -> None:
        """添加执行记录"""
        try:
            # 加载现有记录
            records = await self._load_records(record.chat_id)
            
            # 添加新记录
            records.append(record)
            
            # 清理旧记录
            records = self._cleanup_records(records)
            
            # 保存
            await self._save_records(record.chat_id, records)
            
        except Exception as e:
            logger.exception(f"Failed to add history record: {e}")
    
    async def get_recent_records(
        self,
        chat_id: str,
        limit: int = 10,
        job_id: str | None = None,
    ) -> list[JobExecutionRecord]:
        """获取最近执行记录"""
        try:
            records = await self._load_records(chat_id)
            
            # 过滤指定任务
            if job_id:
                records = [r for r in records if r.job_id == job_id]
            
            # 按时间倒序
            records.sort(key=lambda r: r.executed_at, reverse=True)
            
            return records[:limit]
            
        except Exception as e:
            logger.exception(f"Failed to get history records: {e}")
            return []
    
    async def get_last_execution(
        self,
        chat_id: str,
        job_id: str,
    ) -> JobExecutionRecord | None:
        """获取任务最后一次执行记录"""
        records = await self.get_recent_records(chat_id, job_id=job_id, limit=1)
        return records[0] if records else None
    
    async def _load_records(self, chat_id: str) -> list[JobExecutionRecord]:
        """加载记录"""
        file_path = self._get_history_file(chat_id)
        if not file_path.exists():
            return []
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [JobExecutionRecord.from_dict(r) for r in data]
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")
            return []
    
    async def _save_records(self, chat_id: str, records: list[JobExecutionRecord]) -> None:
        """保存记录"""
        try:
            file_path = self._get_history_file(chat_id)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump([r.to_dict() for r in records], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception(f"Failed to save history: {e}")
    
    def _cleanup_records(self, records: list[JobExecutionRecord]) -> list[JobExecutionRecord]:
        """清理旧记录"""
        now = datetime.now()
        
        # 按任务ID分组
        by_job: dict[str, list[JobExecutionRecord]] = {}
        for r in records:
            by_job.setdefault(r.job_id, []).append(r)
        
        # 清理每个任务的记录
        cleaned = []
        for job_id, job_records in by_job.items():
            # 按时间倒序
            job_records.sort(key=lambda r: r.executed_at, reverse=True)
            
            # 保留最近N条且不超过7天的
            for r in job_records[:self._max_records_per_job]:
                if (now - r.executed_at) <= timedelta(days=self._max_age_days):
                    cleaned.append(r)
        
        return cleaned
