"""
MemoryAgent - 记忆系统主控

职责：
1. 异步队列处理：Observation 和 Summary 的入库
2. 上下文注入：为 Runtime 提供记忆上下文
3. 记忆搜索：提供统一搜索接口
4. 会话生命周期管理：启动、继续、总结

架构：
    ┌─────────────────────────────────────┐
    │           MemoryAgent               │
    ├─────────────────────────────────────┤
    │  ┌──────────────┐  ┌─────────────┐  │
    │  │ Write Queue  │  │ Search API  │  │
    │  │ (asyncio)    │  │             │  │
    │  └──────────────┘  └─────────────┘  │
    ├─────────────────────────────────────┤
    │  MemoryDatabase  │  HybridSearcher  │
    │  (SQLite+FTS5)   │  (Filter→Rank)   │
    └─────────────────────────────────────┘
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .types import (
    ObservationType,
    ObservationInput,
    Observation,
    SummaryInput,
    SessionSummary,
    UserPromptInput,
    UserPrompt,
    SearchFilters,
    SearchResult,
    format_observation_for_embedding,
    format_summary_for_embedding,
)
from .schema import MemoryDatabase
from .embedding_providers import EmbeddingProvider, create_embedding_provider
from .search import HybridSearcher


logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """队列项"""
    type: str  # 'observation' | 'summary' | 'prompt'
    data: ObservationInput | SummaryInput | UserPromptInput
    future: asyncio.Future


class MemoryAgent:
    """
    记忆系统主控
    
    Usage:
        # 初始化
        agent = MemoryAgent.from_runtime(runtime)
        await agent.start()
        
        # 记录观察
        await agent.queue_observation(ObservationInput(
            session_id="sess-001",
            type=ObservationType.BUGFIX,
            title="Fix auth token validation",
            concepts=["auth", "jwt"],
            ...
        ))
        
        # 记录摘要
        await agent.queue_summary(SummaryInput(
            session_id="sess-001",
            request="Fix login bug",
            completed="Fixed token validation",
            ...
        ))
        
        # 搜索记忆
        results = await agent.search("authentication bug")
        
        # 获取上下文
        context = await agent.get_context_for_prompt("sess-001", prompt_number=5)
    """
    
    DEFAULT_DB_PATH = Path.home() / ".kimi" / "memory.db"
    
    def __init__(
        self,
        db: MemoryDatabase,
        embedder: EmbeddingProvider,
        max_queue_size: int = 1000,
    ):
        self.db = db
        self.embedder = embedder
        self.searcher = HybridSearcher(db, embedder)
        
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._initialized = False
        self._project: str = "/"  # 默认 project，从 runtime 初始化时更新
    
    @classmethod
    def from_runtime(
        cls,
        runtime,
        db_path: Optional[Path] = None,
        embedding_provider: str = "kimi",
    ) -> "MemoryAgent":
        """
        从 Runtime 创建 MemoryAgent
        
        Args:
            runtime: KimiSoul 的 runtime 对象
            db_path: 数据库路径，默认 ~/.kimi/memory.db
            embedding_provider: 嵌入模型提供者
        """
        db_path = db_path or cls.DEFAULT_DB_PATH
        db = MemoryDatabase(db_path)
        
        # 优先复用 runtime 的 LLM 客户端
        llm_client = getattr(runtime, 'llm', None)
        embedder = create_embedding_provider(
            provider=embedding_provider,
            llm_client=llm_client
        )
        
        agent = cls(db, embedder)
        
        # 从 runtime 获取 project (work_dir)
        if hasattr(runtime, 'session') and runtime.session:
            agent._project = str(runtime.session.work_dir)
        else:
            agent._project = "/"
        
        return agent
    
    @classmethod
    def create(
        cls,
        db_path: Optional[str | Path] = None,
        embedding_provider: str = "kimi",
        llm_client=None,
        project: str = "/",
    ) -> "MemoryAgent":
        """
        独立创建 MemoryAgent（不依赖 Runtime）
        
        Args:
            db_path: 数据库路径
            embedding_provider: 嵌入模型提供者
            llm_client: LLM 客户端（可选）
            project: 项目/工作目录路径（参考 claude-mem）
        """
        db_path = Path(db_path) if db_path else cls.DEFAULT_DB_PATH
        db = MemoryDatabase(db_path)
        embedder = create_embedding_provider(
            provider=embedding_provider,
            llm_client=llm_client
        )
        agent = cls(db, embedder)
        agent._project = project
        return agent
    
    # ============== Lifecycle ==============
    
    async def start(self):
        """启动 MemoryAgent"""
        if self._initialized:
            return
        
        self._worker_task = asyncio.create_task(self._process_queue())
        self._initialized = True
        logger.info("MemoryAgent started")
    
    async def stop(self, timeout: float = 30.0):
        """停止 MemoryAgent，等待队列处理完成"""
        if not self._initialized:
            return
        
        logger.info("MemoryAgent stopping...")
        self._shutdown_event.set()
        
        # 等待队列处理完成
        try:
            await asyncio.wait_for(
                self._queue.join(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"MemoryAgent queue flush timeout after {timeout}s")
        
        # 取消 worker
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        self.db.close()
        self._initialized = False
        logger.info("MemoryAgent stopped")
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
    
    # ============== Queue Processing ==============
    
    async def _process_queue(self):
        """队列处理 worker"""
        while not self._shutdown_event.is_set():
            try:
                # 等待队列项，带超时以便检查 shutdown
                item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            try:
                if item.type == 'observation':
                    result = await self._persist_observation(item.data)
                elif item.type == 'summary':
                    result = await self._persist_summary(item.data)
                elif item.type == 'prompt':
                    result = await self._persist_prompt(item.data)
                else:
                    raise ValueError(f"Unknown queue item type: {item.type}")
                
                item.future.set_result(result)
            except Exception as e:
                logger.exception(f"Failed to process {item.type}")
                item.future.set_exception(e)
            finally:
                self._queue.task_done()
    
    async def _persist_observation(
        self,
        obs_input: ObservationInput
    ) -> Observation:
        """持久化 Observation（计算嵌入并入库）"""
        # 1. 计算嵌入
        embed_text = format_observation_for_embedding(obs_input)
        vector = await self.embedder.embed_single(embed_text)
        embedding_blob = self.embedder.vector_to_bytes(vector)
        
        # 2. 入库
        obs_id = self.db.insert_observation(obs_input, embedding_blob)
        
        # 3. 返回完整对象
        obs = self.db.get_observation(obs_id)
        logger.debug(f"Persisted observation {obs_id}: {obs.title}")
        return obs
    
    async def _persist_summary(
        self,
        summary_input: SummaryInput
    ) -> SessionSummary:
        """持久化 SessionSummary"""
        # 1. 计算嵌入
        embed_text = format_summary_for_embedding(summary_input)
        vector = await self.embedder.embed_single(embed_text)
        embedding_blob = self.embedder.vector_to_bytes(vector)
        
        # 2. 入库
        summary_id = self.db.insert_summary(summary_input, embedding_blob)
        
        # 3. 返回完整对象
        summary = self.db.get_summary(summary_id)
        logger.debug(f"Persisted summary {summary_id} for session {summary_input.session_id}")
        return summary
    
    async def _persist_prompt(
        self,
        prompt_input: UserPromptInput
    ) -> UserPrompt:
        """持久化 UserPrompt（无需嵌入，仅存储用于时间线展示）"""
        # 直接入库（prompt 不需要向量嵌入，仅用于时间线展示）
        prompt_id = self.db.insert_prompt(prompt_input)
        
        # 返回完整对象
        prompt = self.db.get_prompt(prompt_id)
        logger.debug(f"Persisted prompt {prompt_id} for session {prompt_input.session_id}")
        return prompt
    
    # ============== Public API: Write ==============
    
    async def queue_observation(
        self,
        obs_input: ObservationInput,
        wait: bool = False,
        timeout: float = 10.0
    ) -> Optional[Observation]:
        """
        将 Observation 加入写入队列
        
        Args:
            obs_input: 观察输入
            wait: 是否等待写入完成
            timeout: 等待超时时间
            
        Returns:
            如果 wait=True，返回 Observation；否则返回 None
        """
        future = asyncio.get_event_loop().create_future()
        item = QueueItem(type='observation', data=obs_input, future=future)
        
        try:
            await asyncio.wait_for(
                self._queue.put(item),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Memory queue full, observation dropped")
            return None
        
        if wait:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Observation persist timeout after {timeout}s")
                return None
        return None
    
    async def queue_summary(
        self,
        summary_input: SummaryInput,
        wait: bool = True,
        timeout: float = 30.0
    ) -> Optional[SessionSummary]:
        """
        将 Summary 加入写入队列
        
        注意：Summary 默认 wait=True，因为它通常需要确认保存成功
        """
        future = asyncio.get_event_loop().create_future()
        item = QueueItem(type='summary', data=summary_input, future=future)
        
        try:
            await asyncio.wait_for(self._queue.put(item), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Memory queue full, summary dropped")
            return None
        
        if wait:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Summary persist timeout after {timeout}s")
                return None
        return None
    
    async def queue_prompt(
        self,
        prompt_input: UserPromptInput,
        wait: bool = False,
        timeout: float = 10.0
    ) -> Optional[UserPrompt]:
        """
        将 UserPrompt 加入写入队列
        
        用于保存用户输入的原始文本，供时间线展示和检索
        
        Args:
            prompt_input: Prompt 输入
            wait: 是否等待写入完成
            timeout: 等待超时时间
            
        Returns:
            如果 wait=True，返回 UserPrompt；否则返回 None
        """
        future = asyncio.get_event_loop().create_future()
        item = QueueItem(type='prompt', data=prompt_input, future=future)
        
        try:
            await asyncio.wait_for(
                self._queue.put(item),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Memory queue full, prompt dropped")
            return None
        
        if wait:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Prompt persist timeout after {timeout}s")
                return None
        return None
    
    # ============== Public API: Search ==============
    
    async def search(
        self,
        query: str,
        filters: Optional[SearchFilters] = None,
        top_k: int = 10,
        include_summaries: bool = False,
    ) -> dict[str, list]:
        """
        搜索记忆
        
        Args:
            query: 搜索查询
            filters: 过滤器
            top_k: 返回结果数
            include_summaries: 是否同时搜索 summaries
            
        Returns:
            {
                "observations": [SearchResult, ...],
                "summaries": [(SessionSummary, score), ...]  # if include_summaries
            }
        """
        return await self.searcher.search(
            query=query,
            filters=filters,
            top_k=top_k,
            include_summaries=include_summaries,
        )
    
    async def search_similar(
        self,
        observation_id: int,
        top_k: int = 5
    ) -> list[SearchResult]:
        """查找相似的 Observations"""
        return await self.searcher.find_similar_observations(observation_id, top_k)
    
    def get_timeline(self, session_id: str, limit: Optional[int] = None) -> list[SessionSummary]:
        """获取会话时间线"""
        return self.searcher.get_timeline(session_id, limit)
    
    async def get_context_for_prompt(
        self,
        session_id: str,
        prompt_number: int,
        query: Optional[str] = None,
        max_observations: int = 5,
        max_summaries: int = 3,
        use_project_filter: bool = True,  # 🔴 新增：是否使用 project 过滤
    ) -> dict[str, Any]:
        """
        为当前 Prompt 获取记忆上下文
        
        这是给 Runtime 调用的主要接口
        
        Args:
            session_id: 当前会话 ID
            prompt_number: 当前 Prompt 序号
            query: 可选，如果有具体查询
            max_observations: 最多返回多少 observations
            max_summaries: 最多返回多少 summaries
            use_project_filter: 是否使用 project 过滤（参考 claude-mem）
            
        Returns:
            {
                "relevant_observations": [SearchResult, ...],
                "recent_summaries": [SessionSummary, ...],
                "context_text": "格式化后的上下文文本"
            }
        """
        context = {
            "relevant_observations": [],
            "recent_summaries": [],
            "context_text": ""
        }
        
        # 🔴 构建过滤器，优先使用 project 过滤（参考 claude-mem）
        base_filters = SearchFilters()
        if use_project_filter and self._project:
            base_filters.project = self._project  # 🔴 核心过滤字段
        
        # 1. 获取当前会话的近期 Summaries
        summaries = self.db.get_summaries_by_session(session_id)
        recent_summaries = [
            s for s in summaries 
            if s.prompt_number < prompt_number
        ][:max_summaries]
        context["recent_summaries"] = recent_summaries
        
        # 2. 如果有查询，搜索相关 Observations
        if query:
            filters = SearchFilters(
                session_id=session_id,
                prompt_number_max=prompt_number - 1
            )
            # 复制 project 过滤
            if use_project_filter and self._project:
                filters.project = self._project
            
            observations = await self.searcher.search_observations(
                query, filters, top_k=max_observations
            )
            context["relevant_observations"] = observations
        else:
            # 没有查询时，返回当前会话最近的 Observations
            observations = self.db.get_observations_by_session(session_id)
            recent_obs = [
                SearchResult(obs, 1.0, i+1) 
                for i, obs in enumerate(observations[:max_observations])
            ]
            context["relevant_observations"] = recent_obs
        
        # 3. 生成格式化的上下文文本
        context["context_text"] = self._format_context(context)
        
        return context
    
    def _format_context(self, context: dict) -> str:
        """
        将上下文格式化为统一时间线（参考 claude-mem 设计）
        
        将 observations 和 summaries 按时间混合排序，形成连贯的历史叙事
        """
        summaries = context.get("recent_summaries", [])
        observations = [r.observation for r in context.get("relevant_observations", [])]
        
        if not summaries and not observations:
            return ""
        
        return self._format_timeline(summaries, observations)
    
    def _format_timeline(
        self, 
        summaries: list[SessionSummary], 
        observations: list[Observation]
    ) -> str:
        """
        构建统一时间线（Unified Timeline）
        
        参考 claude-mem 设计，将 summaries 和 observations 按时间混合排序
        """
        from datetime import datetime
        
        lines = ["## Memory Timeline"]
        
        # 创建时间线项列表
        timeline_items: list[dict] = []
        
        # 添加 summaries（作为时间线的主要锚点）
        for summary in summaries:
            timeline_items.append({
                'type': 'summary',
                'timestamp': summary.created_at,
                'prompt_number': summary.prompt_number,
                'data': summary
            })
        
        # 添加 observations
        for obs in observations:
            timeline_items.append({
                'type': 'observation',
                'timestamp': obs.created_at,
                'prompt_number': obs.prompt_number,
                'data': obs
            })
        
        # 按时间倒序排序（最新的在前）
        timeline_items.sort(key=lambda x: x['timestamp'], reverse=True)
        
        # 按 prompt_number 分组，构建层次结构
        current_prompt: int | None = None
        
        for item in timeline_items:
            if item['type'] == 'summary':
                s = item['data']
                current_prompt = s.prompt_number
                
                # 格式化时间
                time_str = s.created_at.strftime("%Y-%m-%d %H:%M") if isinstance(s.created_at, datetime) else str(s.created_at)
                
                lines.append(f"\n### {time_str} (Prompt #{s.prompt_number})")
                
                if s.request:
                    lines.append(f"**Request:** {s.request}")
                if s.investigated:
                    lines.append(f"**Investigated:** {s.investigated}")
                if s.completed:
                    lines.append(f"**Completed:** {s.completed}")
                if s.learned:
                    lines.append(f"**Learned:** {s.learned}")
                if s.next_steps:
                    lines.append(f"**Next Steps:** {s.next_steps}")
                    
            else:  # observation
                obs = item['data']
                
                # 如果 observation 不属于当前 summary 的 prompt，显示提示
                if current_prompt is not None and obs.prompt_number != current_prompt:
                    lines.append(f"\n  *[Earlier in Prompt #{obs.prompt_number}]*")
                
                lines.append(f"\n- **[{obs.type.value.upper()}]** {obs.title}")
                
                if obs.subtitle:
                    lines.append(f"  *{obs.subtitle}*")
                
                if obs.narrative:
                    # 缩进 narrative
                    narrative_lines = obs.narrative.strip().split('\n')
                    for nl in narrative_lines:
                        lines.append(f"  > {nl}")
                
                if obs.facts:
                    for fact in obs.facts:
                        lines.append(f"  • {fact}")
                
                if obs.concepts:
                    lines.append(f"  *Concepts:* {', '.join(obs.concepts)}")
                
                if obs.files_modified:
                    files_str = ', '.join(obs.files_modified[:3])  # 最多显示3个文件
                    if len(obs.files_modified) > 3:
                        files_str += f" (+{len(obs.files_modified) - 3} more)"
                    lines.append(f"  *Files:* {files_str}")
        
        return "\n".join(lines)
    
    # ============== Session Lifecycle ==============
    
    async def on_session_start(
        self,
        session_id: str,
        context: Optional[dict] = None
    ) -> str:
        """
        会话启动时调用 - 返回完整的记忆上下文
        
        包括：
        1. 当前会话的历史 summaries
        2. 同项目的其他相关记忆（统一时间线）
        """
        # 获取该会话的历史 summaries
        session_summaries = self.db.get_summaries_by_session(session_id, limit=3)
        
        # 获取项目级别的相关 observations
        project_observations = []
        if self._project:
            # 获取最近的项目级别 observations
            obs_list = self.db.get_observations_by_project(self._project, limit=20)
            # 过滤掉当前会话的 observations（避免重复）
            project_observations = [
                obs for obs in obs_list 
                if obs.session_id != session_id
            ][:10]  # 最多10条
        
        # 如果没有数据，返回空
        if not session_summaries and not project_observations:
            return ""
        
        # 使用统一时间线格式
        context_dict = {
            "recent_summaries": session_summaries,
            "relevant_observations": [type('SearchResult', (), {'observation': obs})() for obs in project_observations]
        }
        
        return self._format_context(context_dict)
    
    async def on_session_end(
        self,
        session_id: str,
        final_summary: Optional[SummaryInput] = None
    ):
        """
        会话结束时调用
        
        确保所有数据已写入
        """
        if final_summary:
            await self.queue_summary(final_summary, wait=True)
        
        # 等待队列清空
        await self._queue.join()
    
    # ============== Project-based Methods（参考 claude-mem）==============
    
    @property
    def project(self) -> str:
        """获取当前 project（工作目录）"""
        return self._project
    
    @project.setter
    def project(self, value: str):
        """设置 project"""
        self._project = value
    
    def find_by_project(
        self,
        project: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """
        按项目查找 Observations（参考 claude-mem findByProject）
        
        🔴 project 是核心过滤字段
        
        Args:
            project: 项目路径，默认使用当前 project
            limit: 返回数量限制
        """
        project = project or self._project
        if not project:
            return []
        return self.searcher.find_by_project(project, limit)
    
    def find_by_concept(
        self,
        concept: str,
        project: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """
        按概念标签查找（参考 claude-mem findByConcept）
        
        Args:
            concept: 概念标签
            project: 限制项目范围
            limit: 返回数量限制
        """
        filters = SearchFilters()
        if project or self._project:
            filters.project = project or self._project
        return self.searcher.find_by_concept(concept, filters, limit)
    
    def find_by_file(
        self,
        file_path: str,
        project: Optional[str] = None,
        limit: int = 50,
    ) -> tuple:
        """
        按文件路径查找（参考 claude-mem findByFile）
        
        Args:
            file_path: 文件路径
            project: 限制项目范围
            limit: 返回数量限制
            
        Returns:
            (observations, summaries)
        """
        filters = SearchFilters()
        if project or self._project:
            filters.project = project or self._project
        return self.searcher.find_by_file(file_path, filters, limit)
    
    # ============== Utility ==============
    
    def get_stats(self) -> dict[str, Any]:
        """获取记忆系统统计信息"""
        conn = self.db._get_connection()
        
        obs_count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        sum_count = conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
        session_count = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM session_summaries"
        ).fetchone()[0]
        
        # 🔴 按 project 统计
        project_stats = {}
        if self._project:
            project_obs = conn.execute(
                "SELECT COUNT(*) FROM observations WHERE project = ?",
                (self._project,)
            ).fetchone()[0]
            project_sum = conn.execute(
                "SELECT COUNT(*) FROM session_summaries WHERE project = ?",
                (self._project,)
            ).fetchone()[0]
            project_stats = {
                "current_project": self._project,
                "project_observations": project_obs,
                "project_summaries": project_sum,
            }
        
        return {
            "total_observations": obs_count,
            "total_summaries": sum_count,
            "total_sessions": session_count,
            "queue_size": self._queue.qsize(),
            **project_stats,
        }
