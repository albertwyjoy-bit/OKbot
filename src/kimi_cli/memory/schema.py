"""
Database Schema & Connection Management

SQLite + FTS5 实现：
- observations 表：存储原子观察
- session_summaries 表：存储会话摘要
- fts5_observations：FTS5 全文搜索索引
- fts5_summaries：FTS5 全文搜索索引

参考 claude-mem 设计，添加 project 和 discovery_tokens 字段用于高效检索
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from .types import ObservationType, ObservationInput, Observation, SummaryInput, SessionSummary, UserPromptInput, UserPrompt


# 数据库 Schema
SCHEMA_SQL = """
-- Observations 表：原子观察
-- 参考 claude-mem 设计，添加 project 和 discovery_tokens 字段
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,          -- 工作目录/项目路径（核心过滤字段）
    type TEXT NOT NULL CHECK(type IN ('bugfix', 'feature', 'refactor', 'change', 'discovery', 'decision')),
    title TEXT NOT NULL,
    subtitle TEXT DEFAULT '',
    facts TEXT DEFAULT '[]',        -- JSON array
    narrative TEXT DEFAULT '',      -- 详细叙述（用于向量搜索内容）
    concepts TEXT DEFAULT '[]',     -- JSON array
    files_read TEXT DEFAULT '[]',   -- JSON array
    files_modified TEXT DEFAULT '[]', -- JSON array
    tool_name TEXT,
    prompt_number INTEGER DEFAULT 0,
    discovery_tokens INTEGER DEFAULT 0,  -- 发现成本（ROI 追踪）
    created_at_epoch INTEGER NOT NULL,   -- Unix timestamp for sorting
    embedding BLOB                  -- numpy float32 array as bytes
);

-- Session Summaries 表：会话摘要
CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,          -- 工作目录/项目路径
    request TEXT DEFAULT '',
    investigated TEXT DEFAULT '',
    learned TEXT DEFAULT '',
    completed TEXT DEFAULT '',
    next_steps TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    prompt_number INTEGER DEFAULT 0,
    discovery_tokens INTEGER DEFAULT 0,  -- 发现成本（ROI 追踪）
    created_at_epoch INTEGER NOT NULL,
    embedding BLOB
);

-- FTS5 全文搜索索引 - Observations
-- 包含 narrative 字段，用于向量搜索内容生成
CREATE VIRTUAL TABLE IF NOT EXISTS fts5_observations USING fts5(
    title,
    subtitle,
    narrative,
    facts,
    concepts,
    content='observations',
    content_rowid='id'
);

-- FTS5 全文搜索索引 - Summaries  
CREATE VIRTUAL TABLE IF NOT EXISTS fts5_summaries USING fts5(
    request,
    investigated,
    learned,
    completed,
    next_steps,
    notes,
    content='session_summaries',
    content_rowid='id'
);

-- 触发器：保持 FTS5 索引同步（Observations）
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO fts5_observations(rowid, title, subtitle, narrative, facts, concepts)
    VALUES (new.id, new.title, new.subtitle, new.narrative, new.facts, new.concepts);
END;

CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO fts5_observations(fts5_observations, rowid, title, subtitle, narrative, facts, concepts)
    VALUES ('delete', old.id, old.title, old.subtitle, old.narrative, old.facts, old.concepts);
END;

CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO fts5_observations(fts5_observations, rowid, title, subtitle, narrative, facts, concepts)
    VALUES ('delete', old.id, old.title, old.subtitle, old.narrative, old.facts, old.concepts);
    INSERT INTO fts5_observations(rowid, title, subtitle, narrative, facts, concepts)
    VALUES (new.id, new.title, new.subtitle, new.narrative, new.facts, new.concepts);
END;

-- 触发器：保持 FTS5 索引同步（Summaries）
CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON session_summaries BEGIN
    INSERT INTO fts5_summaries(rowid, request, investigated, learned, completed, next_steps, notes)
    VALUES (new.id, new.request, new.investigated, new.learned, new.completed, new.next_steps, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS summaries_ad AFTER DELETE ON session_summaries BEGIN
    INSERT INTO fts5_summaries(fts5_summaries, rowid, request, investigated, learned, completed, next_steps, notes)
    VALUES ('delete', old.id, old.request, old.investigated, old.learned, old.completed, old.next_steps, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS summaries_au AFTER UPDATE ON session_summaries BEGIN
    INSERT INTO fts5_summaries(fts5_summaries, rowid, request, investigated, learned, completed, next_steps, notes)
    VALUES ('delete', old.id, old.request, old.investigated, old.learned, old.completed, old.next_steps, old.notes);
    INSERT INTO fts5_summaries(rowid, request, investigated, learned, completed, next_steps, notes)
    VALUES (new.id, new.request, new.investigated, new.learned, new.completed, new.next_steps, new.notes);
END;

-- 索引：加速常见查询（参考 claude-mem 设计）
-- Observations 索引
CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_project ON observations(project);              -- 🔴 核心过滤字段
CREATE INDEX IF NOT EXISTS idx_obs_project_created ON observations(project, created_at_epoch DESC);  -- 复合索引
CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(type);
CREATE INDEX IF NOT EXISTS idx_obs_prompt ON observations(prompt_number);
CREATE INDEX IF NOT EXISTS idx_obs_created ON observations(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_obs_tool ON observations(tool_name);

-- Session Summaries 索引
CREATE INDEX IF NOT EXISTS idx_sum_session ON session_summaries(session_id);
CREATE INDEX IF NOT EXISTS idx_sum_project ON session_summaries(project);         -- 🔴 核心过滤字段
CREATE INDEX IF NOT EXISTS idx_sum_project_created ON session_summaries(project, created_at_epoch DESC);  -- 复合索引
CREATE INDEX IF NOT EXISTS idx_sum_prompt ON session_summaries(prompt_number);
CREATE INDEX IF NOT EXISTS idx_sum_created ON session_summaries(created_at_epoch DESC);

-- User Prompts 表：用户输入的原始请求
-- 参考 claude-mem 设计，独立存储 prompts 用于时间线展示
CREATE TABLE IF NOT EXISTS user_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,          -- 工作目录/项目路径
    prompt_number INTEGER NOT NULL, -- Prompt 序号
    prompt_text TEXT NOT NULL,      -- 用户输入的原始文本
    created_at_epoch INTEGER NOT NULL
);

-- FTS5 全文搜索索引 - User Prompts
CREATE VIRTUAL TABLE IF NOT EXISTS fts5_prompts USING fts5(
    prompt_text,
    content='user_prompts',
    content_rowid='id'
);

-- 触发器：保持 FTS5 索引同步（User Prompts）
CREATE TRIGGER IF NOT EXISTS prompts_ai AFTER INSERT ON user_prompts BEGIN
    INSERT INTO fts5_prompts(rowid, prompt_text)
    VALUES (new.id, new.prompt_text);
END;

CREATE TRIGGER IF NOT EXISTS prompts_ad AFTER DELETE ON user_prompts BEGIN
    INSERT INTO fts5_prompts(fts5_prompts, rowid, prompt_text)
    VALUES ('delete', old.id, old.prompt_text);
END;

CREATE TRIGGER IF NOT EXISTS prompts_au AFTER UPDATE ON user_prompts BEGIN
    INSERT INTO fts5_prompts(fts5_prompts, rowid, prompt_text)
    VALUES ('delete', old.id, old.prompt_text);
    INSERT INTO fts5_prompts(rowid, prompt_text)
    VALUES (new.id, new.prompt_text);
END;

-- User Prompts 索引
CREATE INDEX IF NOT EXISTS idx_prompt_session ON user_prompts(session_id);
CREATE INDEX IF NOT EXISTS idx_prompt_project ON user_prompts(project);           -- 🔴 核心过滤字段
CREATE INDEX IF NOT EXISTS idx_prompt_project_created ON user_prompts(project, created_at_epoch DESC);  -- 复合索引
CREATE INDEX IF NOT EXISTS idx_prompt_number ON user_prompts(prompt_number);
CREATE INDEX IF NOT EXISTS idx_prompt_created ON user_prompts(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_lookup ON user_prompts(session_id, prompt_number);
"""


class MemoryDatabase:
    """
    SQLite 数据库管理器
    
    线程安全：每个线程有自己的连接
    """
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")  # 更好的并发性能
            self._local.conn = conn
        return self._local.conn
    
    def _init_db(self):
        """初始化数据库 Schema"""
        with self._transaction() as conn:
            conn.executescript(SCHEMA_SQL)
    
    @contextmanager
    def _transaction(self):
        """事务上下文管理器"""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    def close(self):
        """关闭当前线程的连接"""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
    
    # ============== Observation CRUD ==============
    
    def insert_observation(self, obs: ObservationInput, embedding: Optional[bytes] = None) -> int:
        """插入 Observation，返回 ID"""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO observations 
                (session_id, project, type, title, subtitle, facts, narrative, concepts,
                 files_read, files_modified, tool_name, prompt_number, discovery_tokens, created_at_epoch, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs.session_id,
                    obs.project,
                    obs.type.value,
                    obs.title,
                    obs.subtitle,
                    json.dumps(obs.facts, ensure_ascii=False),
                    obs.narrative,
                    json.dumps(obs.concepts, ensure_ascii=False),
                    json.dumps(obs.files_read, ensure_ascii=False),
                    json.dumps(obs.files_modified, ensure_ascii=False),
                    obs.tool_name,
                    obs.prompt_number,
                    obs.discovery_tokens,
                    int(obs.created_at.timestamp()),
                    embedding
                )
            )
            return cursor.lastrowid
    
    def get_observation(self, obs_id: int) -> Optional[Observation]:
        """通过 ID 获取 Observation"""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM observations WHERE id = ?",
            (obs_id,)
        ).fetchone()
        return self._row_to_observation(row) if row else None
    
    def get_observations_by_session(
        self, 
        session_id: str, 
        prompt_number: Optional[int] = None
    ) -> list[Observation]:
        """获取指定会话的 Observations"""
        conn = self._get_connection()
        if prompt_number is not None:
            rows = conn.execute(
                """
                SELECT * FROM observations 
                WHERE session_id = ? AND prompt_number = ?
                ORDER BY created_at_epoch DESC
                """,
                (session_id, prompt_number)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM observations 
                WHERE session_id = ?
                ORDER BY created_at_epoch DESC
                """,
                (session_id,)
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]
    
    def get_observations_by_project(
        self,
        project: str,
        limit: Optional[int] = None
    ) -> list[Observation]:
        """获取指定项目的 Observations（新增方法，参考 claude-mem）"""
        conn = self._get_connection()
        sql = """
            SELECT * FROM observations 
            WHERE project = ?
            ORDER BY created_at_epoch DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, (project,)).fetchall()
        return [self._row_to_observation(row) for row in rows]
    
    def _row_to_observation(self, row: sqlite3.Row) -> Observation:
        """将数据库行转换为 Observation 对象"""
        return Observation(
            id=row['id'],
            session_id=row['session_id'],
            project=row['project'],
            type=ObservationType(row['type']),
            title=row['title'],
            subtitle=row['subtitle'] or '',
            facts=json.loads(row['facts'] or '[]'),
            narrative=row['narrative'] or '',
            concepts=json.loads(row['concepts'] or '[]'),
            files_read=json.loads(row['files_read'] or '[]'),
            files_modified=json.loads(row['files_modified'] or '[]'),
            tool_name=row['tool_name'],
            prompt_number=row['prompt_number'],
            discovery_tokens=row['discovery_tokens'] or 0,
            created_at=datetime.fromtimestamp(row['created_at_epoch']),
            embedding=row['embedding']
        )
    
    # ============== Summary CRUD ==============
    
    def insert_summary(self, summary: SummaryInput, embedding: Optional[bytes] = None) -> int:
        """插入 SessionSummary，返回 ID"""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO session_summaries
                (session_id, project, request, investigated, learned, completed, next_steps, notes,
                 prompt_number, discovery_tokens, created_at_epoch, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.session_id,
                    summary.project,
                    summary.request,
                    summary.investigated,
                    summary.learned,
                    summary.completed,
                    summary.next_steps,
                    summary.notes,
                    summary.prompt_number,
                    summary.discovery_tokens,
                    int(summary.created_at.timestamp()),
                    embedding
                )
            )
            return cursor.lastrowid
    
    def get_summary(self, summary_id: int) -> Optional[SessionSummary]:
        """通过 ID 获取 Summary"""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM session_summaries WHERE id = ?",
            (summary_id,)
        ).fetchone()
        return self._row_to_summary(row) if row else None
    
    def get_summaries_by_session(
        self, 
        session_id: str,
        limit: Optional[int] = None
    ) -> list[SessionSummary]:
        """获取指定会话的 Summaries，按时间倒序"""
        conn = self._get_connection()
        sql = """
            SELECT * FROM session_summaries 
            WHERE session_id = ?
            ORDER BY created_at_epoch DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, (session_id,)).fetchall()
        return [self._row_to_summary(row) for row in rows]
    
    def get_summaries_by_project(
        self,
        project: str,
        limit: Optional[int] = None
    ) -> list[SessionSummary]:
        """获取指定项目的 Summaries（新增方法，参考 claude-mem）"""
        conn = self._get_connection()
        sql = """
            SELECT * FROM session_summaries 
            WHERE project = ?
            ORDER BY created_at_epoch DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, (project,)).fetchall()
        return [self._row_to_summary(row) for row in rows]
    
    def get_timeline(self, session_id: str, limit: int | None = None) -> list[SessionSummary]:
        """获取会话的完整时间线（所有 summaries）"""
        return self.get_summaries_by_session(session_id, limit)
    
    def _row_to_summary(self, row: sqlite3.Row) -> SessionSummary:
        """将数据库行转换为 SessionSummary 对象"""
        return SessionSummary(
            id=row['id'],
            session_id=row['session_id'],
            project=row['project'],
            request=row['request'] or '',
            investigated=row['investigated'] or '',
            learned=row['learned'] or '',
            completed=row['completed'] or '',
            next_steps=row['next_steps'] or '',
            notes=row['notes'] or '',
            prompt_number=row['prompt_number'],
            discovery_tokens=row['discovery_tokens'] or 0,
            created_at=datetime.fromtimestamp(row['created_at_epoch']),
            embedding=row['embedding']
        )
    
    # ============== UserPrompt CRUD ==============
    
    def insert_prompt(self, prompt: UserPromptInput) -> int:
        """插入 UserPrompt，返回 ID"""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_prompts
                (session_id, project, prompt_number, prompt_text, created_at_epoch)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    prompt.session_id,
                    prompt.project,
                    prompt.prompt_number,
                    prompt.prompt_text,
                    int(prompt.created_at.timestamp())
                )
            )
            return cursor.lastrowid
    
    def get_prompt(self, prompt_id: int) -> Optional[UserPrompt]:
        """通过 ID 获取 UserPrompt"""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM user_prompts WHERE id = ?",
            (prompt_id,)
        ).fetchone()
        return self._row_to_prompt(row) if row else None
    
    def get_prompts_by_session(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> list[UserPrompt]:
        """获取指定会话的 Prompts，按时间倒序"""
        conn = self._get_connection()
        sql = """
            SELECT * FROM user_prompts
            WHERE session_id = ?
            ORDER BY created_at_epoch DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, (session_id,)).fetchall()
        return [self._row_to_prompt(row) for row in rows]
    
    def get_prompts_by_project(
        self,
        project: str,
        limit: Optional[int] = None
    ) -> list[UserPrompt]:
        """获取指定项目的 Prompts（参考 claude-mem）"""
        conn = self._get_connection()
        sql = """
            SELECT * FROM user_prompts
            WHERE project = ?
            ORDER BY created_at_epoch DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, (project,)).fetchall()
        return [self._row_to_prompt(row) for row in rows]
    
    def get_prompt_by_session_and_number(
        self,
        session_id: str,
        prompt_number: int
    ) -> Optional[UserPrompt]:
        """通过 session_id 和 prompt_number 获取 Prompt"""
        conn = self._get_connection()
        row = conn.execute(
            """
            SELECT * FROM user_prompts
            WHERE session_id = ? AND prompt_number = ?
            """,
            (session_id, prompt_number)
        ).fetchone()
        return self._row_to_prompt(row) if row else None
    
    def _row_to_prompt(self, row: sqlite3.Row) -> UserPrompt:
        """将数据库行转换为 UserPrompt 对象"""
        return UserPrompt(
            id=row['id'],
            session_id=row['session_id'],
            project=row['project'],
            prompt_number=row['prompt_number'],
            prompt_text=row['prompt_text'],
            created_at=datetime.fromtimestamp(row['created_at_epoch'])
        )
    
    # ============== Search Methods ==============
    
    def fts_search_observations(self, query: str, limit: int = 50) -> list[tuple[int, float]]:
        """
        FTS5 全文搜索 Observations
        返回: [(id, rank), ...]
        """
        conn = self._get_connection()
        # 转义 FTS5 特殊字符
        query = query.replace('"', '""')
        rows = conn.execute(
            """
            SELECT rowid, rank FROM fts5_observations
            WHERE fts5_observations MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (query, limit)
        ).fetchall()
        return [(row['rowid'], row['rank']) for row in rows]
    
    def fts_search_summaries(self, query: str, limit: int = 20) -> list[tuple[int, float]]:
        """FTS5 全文搜索 Summaries"""
        conn = self._get_connection()
        query = query.replace('"', '""')
        rows = conn.execute(
            """
            SELECT rowid, rank FROM fts5_summaries
            WHERE fts5_summaries MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (query, limit)
        ).fetchall()
        return [(row['rowid'], row['rank']) for row in rows]
    
    def fts_search_prompts(self, query: str, limit: int = 20) -> list[tuple[int, float]]:
        """FTS5 全文搜索 User Prompts"""
        conn = self._get_connection()
        query = query.replace('"', '""')
        rows = conn.execute(
            """
            SELECT rowid, rank FROM fts5_prompts
            WHERE fts5_prompts MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (query, limit)
        ).fetchall()
        return [(row['rowid'], row['rank']) for row in rows]
    
    def get_observations_batch(self, ids: list[int]) -> list[Observation]:
        """批量获取 Observations"""
        if not ids:
            return []
        conn = self._get_connection()
        placeholders = ','.join('?' * len(ids))
        rows = conn.execute(
            f"SELECT * FROM observations WHERE id IN ({placeholders})",
            ids
        ).fetchall()
        # 保持输入顺序
        id_map = {row['id']: self._row_to_observation(row) for row in rows}
        return [id_map[id] for id in ids if id in id_map]
    
    def get_summaries_batch(self, ids: list[int]) -> list[SessionSummary]:
        """批量获取 Summaries"""
        if not ids:
            return []
        conn = self._get_connection()
        placeholders = ','.join('?' * len(ids))
        rows = conn.execute(
            f"SELECT * FROM session_summaries WHERE id IN ({placeholders})",
            ids
        ).fetchall()
        id_map = {row['id']: self._row_to_summary(row) for row in rows}
        return [id_map[id] for id in ids if id in id_map]
    
    def metadata_filter_observations(
        self,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
        types: Optional[list[str]] = None,
        concepts: Optional[list[str]] = None,
        files: Optional[list[str]] = None,
        tool_name: Optional[str] = None,
        prompt_number_min: Optional[int] = None,
        prompt_number_max: Optional[int] = None,
    ) -> list[int]:
        """
        元数据过滤 Observations（参考 claude-mem 设计）
        返回符合条件的 ID 列表
        
        Args:
            session_id: 会话 ID 过滤
            project: 项目/工作目录过滤（核心字段）
            types: 观察类型列表
            concepts: 概念标签列表
            files: 文件路径列表（匹配 files_read 或 files_modified）
            tool_name: 工具名称
            prompt_number_min/max: Prompt 序号范围
        """
        conn = self._get_connection()
        conditions = []
        params = []
        
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        
        # 🔴 核心过滤字段：project
        if project:
            conditions.append("project = ?")
            params.append(project)
        
        if types:
            placeholders = ','.join('?' * len(types))
            conditions.append(f"type IN ({placeholders})")
            params.extend(types)
        
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        
        if prompt_number_min is not None:
            conditions.append("prompt_number >= ?")
            params.append(prompt_number_min)
        
        if prompt_number_max is not None:
            conditions.append("prompt_number <= ?")
            params.append(prompt_number_max)
        
        sql = "SELECT id FROM observations"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        
        rows = conn.execute(sql, params).fetchall()
        ids = [row['id'] for row in rows]
        
        # JSON 字段过滤（concepts 和 files）
        if concepts or files:
            filtered_ids = []
            for obs in self.get_observations_batch(ids):
                # Concepts 过滤
                if concepts:
                    if not any(c in obs.concepts for c in concepts):
                        continue
                
                # Files 过滤（匹配 files_read 或 files_modified）
                if files:
                    all_files = obs.files_read + obs.files_modified
                    if not any(f in ' '.join(all_files) for f in files):
                        continue
                
                filtered_ids.append(obs.id)
            return filtered_ids
        
        return ids
    
    def metadata_filter_summaries(
        self,
        session_id: Optional[str] = None,
        project: Optional[str] = None,
        prompt_number_min: Optional[int] = None,
        prompt_number_max: Optional[int] = None,
    ) -> list[int]:
        """
        元数据过滤 Summaries（新增方法，参考 claude-mem）
        返回符合条件的 ID 列表
        """
        conn = self._get_connection()
        conditions = []
        params = []
        
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        
        # 🔴 核心过滤字段：project
        if project:
            conditions.append("project = ?")
            params.append(project)
        
        if prompt_number_min is not None:
            conditions.append("prompt_number >= ?")
            params.append(prompt_number_min)
        
        if prompt_number_max is not None:
            conditions.append("prompt_number <= ?")
            params.append(prompt_number_max)
        
        sql = "SELECT id FROM session_summaries"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        
        rows = conn.execute(sql, params).fetchall()
        return [row['id'] for row in rows]
