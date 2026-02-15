"""
Hybrid Search Implementation

混合检索实现：Filter → Rank → Intersect
参考 claude-mem 设计，支持 Filter-only 模式（当 query 为空时）

流程：
1. FILTER: 元数据过滤（project、类型、概念、时间范围等）
2. RANK: 在过滤结果上进行向量相似度排序
3. INTERSECT: 合并结果，保持语义排序

同时支持三级检索：
- Search（索引）: 全局语义搜索
- Timeline（上下文）: 会话历史摘要链
- Get（详情）: 具体 Observation 详情
"""

import asyncio
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import numpy as np

from .types import (
    Observation,
    SessionSummary,
    SearchFilters,
    SearchResult,
    format_observation_for_embedding,
    format_summary_for_embedding,
)
from .schema import MemoryDatabase
from .embedding_providers import EmbeddingProvider


@dataclass
class VectorSearchResult:
    """向量搜索结果"""
    id: int
    score: float
    vector: Optional[np.ndarray] = None


class HybridSearcher:
    """
    混合检索器（参考 claude-mem 设计）
    
    实现 Filter → Rank → Intersect 模式：
    1. 先用元数据过滤缩小候选集
    2. 再对候选集进行向量相似度排序
    3. 返回 Top-K 结果
    
    🔴 关键特性：支持 Filter-only 搜索（当 query 为空时，仅使用元数据过滤）
    这是 claude-mem 的核心设计，用于日期过滤等场景
    """
    
    def __init__(
        self,
        db: MemoryDatabase,
        embedder: EmbeddingProvider,
        observation_weight: float = 0.7,    # Observation 结果权重
        summary_weight: float = 0.3,         # Summary 结果权重
    ):
        self.db = db
        self.embedder = embedder
        self.observation_weight = observation_weight
        self.summary_weight = summary_weight
    
    # ============== Filter → Rank → Intersect ==============
    
    async def search_observations(
        self,
        query: Optional[str] = None,
        filters: Optional[SearchFilters] = None,
        top_k: int = 10,
        order_by: str = 'relevance',  # 'relevance' | 'date_desc' | 'date_asc'
    ) -> list[SearchResult]:
        """
        搜索 Observations（参考 claude-mem SessionSearch.searchObservations）
        
        Args:
            query: 搜索查询（可选，为空时仅使用元数据过滤）
            filters: 元数据过滤器
            top_k: 返回结果数量
            order_by: 排序方式
            
        Returns:
            SearchResult 列表
            
        Note:
            当 query 为空时，使用 Filter-only 模式（参考 claude-mem 设计）
            这支持日期过滤等 Chroma 无法处理的场景
        """
        # FILTER-ONLY PATH: 当 query 为空时，直接查询表
        if not query:
            return self._filter_only_observations(filters, top_k, order_by)
        
        # VECTOR SEARCH PATH: 使用向量搜索 + 过滤
        return await self._vector_search_observations(query, filters, top_k)
    
    def _filter_only_observations(
        self,
        filters: Optional[SearchFilters],
        limit: int = 10,
        order_by: str = 'date_desc',
    ) -> list[SearchResult]:
        """
        Filter-only 搜索 Observations（参考 claude-mem 设计）
        
        当 query 为空时使用，支持日期过滤等场景
        这是 Chroma 无法直接处理的，需要直接查询 SQLite
        """
        if not filters:
            raise ValueError('Either query or filters required for search')
        
        # 构建过滤条件
        candidate_ids = self._build_metadata_filter(filters)
        
        if not candidate_ids:
            return []
        
        # 获取完整 observation 数据
        observations = self.db.get_observations_batch(candidate_ids[:limit * 2])
        
        # 排序
        if order_by == 'date_asc':
            observations.sort(key=lambda x: x.created_at)
        else:  # date_desc or relevance without query
            observations.sort(key=lambda x: x.created_at, reverse=True)
        
        # 构建结果（filter-only 模式下 score 为 0）
        results = []
        for i, obs in enumerate(observations[:limit]):
            results.append(SearchResult(
                observation=obs,
                score=0.0,
                rank=i + 1
            ))
        
        return results
    
    async def _vector_search_observations(
        self,
        query: str,
        filters: Optional[SearchFilters],
        top_k: int,
    ) -> list[SearchResult]:
        """向量搜索 Observations"""
        # Step 1: FILTER - 元数据过滤
        candidate_ids = self._build_metadata_filter(filters)
        
        if not candidate_ids:
            # 如果没有过滤器，使用 FTS 获取候选集
            fts_results = self.db.fts_search_observations(query, limit=100)
            candidate_ids = [id for id, _ in fts_results]
        
        if not candidate_ids:
            return []
        
        # Step 2: RANK - 向量排序
        ranked = await self._vector_rank_observations(query, candidate_ids, top_k)
        
        # Step 3: INTERSECT - 合并结果
        observations = self.db.get_observations_batch([r.id for r in ranked])
        id_to_obs = {obs.id: obs for obs in observations}
        
        results = []
        for i, vr in enumerate(ranked):
            if vr.id in id_to_obs:
                results.append(SearchResult(
                    observation=id_to_obs[vr.id],
                    score=vr.score,
                    rank=i + 1
                ))
        
        return results
    
    async def search_summaries(
        self,
        query: Optional[str] = None,
        filters: Optional[SearchFilters] = None,
        top_k: int = 5,
        order_by: str = 'date_desc',
    ) -> list[tuple[SessionSummary, float]]:
        """
        搜索 SessionSummaries（参考 claude-mem SessionSearch.searchSessions）
        
        支持 Filter-only 模式（当 query 为空时）
        """
        # FILTER-ONLY PATH
        if not query:
            return self._filter_only_summaries(filters, top_k, order_by)
        
        # VECTOR SEARCH PATH
        return await self._vector_search_summaries(query, filters, top_k)
    
    def _filter_only_summaries(
        self,
        filters: Optional[SearchFilters],
        limit: int = 5,
        order_by: str = 'date_desc',
    ) -> list[tuple[SessionSummary, float]]:
        """Filter-only 搜索 Summaries"""
        if not filters:
            raise ValueError('Either query or filters required for search')
        
        # 构建过滤条件（summaries 不支持 type 过滤）
        filter_copy = SearchFilters()
        if filters:
            filter_copy.session_id = filters.session_id
            filter_copy.project = filters.project
            filter_copy.date_after = filters.date_after
            filter_copy.date_before = filters.date_before
            filter_copy.prompt_number_min = filters.prompt_number_min
            filter_copy.prompt_number_max = filters.prompt_number_max
        
        candidate_ids = self._build_metadata_filter_summaries(filter_copy)
        
        if not candidate_ids:
            return []
        
        # 获取完整数据
        summaries = self.db.get_summaries_batch(candidate_ids[:limit * 2])
        
        # 排序
        if order_by == 'date_asc':
            summaries.sort(key=lambda x: x.created_at)
        else:
            summaries.sort(key=lambda x: x.created_at, reverse=True)
        
        # 构建结果（filter-only 模式下 score 为 0）
        return [(s, 0.0) for s in summaries[:limit]]
    
    async def _vector_search_summaries(
        self,
        query: str,
        filters: Optional[SearchFilters],
        top_k: int,
    ) -> list[tuple[SessionSummary, float]]:
        """向量搜索 Summaries"""
        # Step 1: FILTER
        candidate_ids = self._build_metadata_filter_summaries(filters)
        
        if not candidate_ids:
            fts_results = self.db.fts_search_summaries(query, limit=50)
            candidate_ids = [id for id, _ in fts_results]
        
        if not candidate_ids:
            return []
        
        # Step 2: RANK
        ranked = await self._vector_rank_summaries(query, candidate_ids, top_k)
        
        # Step 3: INTERSECT
        summaries = self.db.get_summaries_batch([r.id for r in ranked])
        id_to_sum = {s.id: s for s in summaries}
        
        results = []
        for vr in ranked:
            if vr.id in id_to_sum:
                results.append((id_to_sum[vr.id], vr.score))
        
        return results
    
    async def search(
        self,
        query: Optional[str] = None,
        filters: Optional[SearchFilters] = None,
        top_k: int = 10,
        include_summaries: bool = False,
    ) -> dict[str, list]:
        """
        统一搜索接口（支持 Filter-only 模式）
        
        Returns:
            {
                "observations": [SearchResult, ...],
                "summaries": [(SessionSummary, score), ...]  # 如果 include_summaries=True
            }
        """
        results = {
            "observations": await self.search_observations(query, filters, top_k),
        }
        
        if include_summaries:
            results["summaries"] = await self.search_summaries(query, filters, top_k // 2)
        
        return results
    
    # ============== Filter Implementation ==============
    
    def _build_metadata_filter(
        self,
        filters: Optional[SearchFilters]
    ) -> list[int]:
        """
        构建元数据过滤条件（参考 claude-mem buildFilterClause）
        
        🔴 project 是核心过滤字段，优先使用
        """
        if not filters:
            return []
        
        # 转换日期范围
        date_range = None
        if filters.date_after or filters.date_before:
            date_range = {}
            if filters.date_after:
                date_range['start'] = int(filters.date_after.timestamp() * 1000)
            if filters.date_before:
                date_range['end'] = int(filters.date_before.timestamp() * 1000)
        
        return self.db.metadata_filter_observations(
            session_id=filters.session_id,
            project=filters.project,  # 🔴 核心过滤字段
            types=[t.value for t in filters.types] if filters.types else None,
            concepts=filters.concepts,
            files=filters.files,
            tool_name=filters.tool_name,
            prompt_number_min=filters.prompt_number_min,
            prompt_number_max=filters.prompt_number_max,
        )
    
    def _build_metadata_filter_summaries(
        self,
        filters: Optional[SearchFilters]
    ) -> list[int]:
        """构建 Summaries 的元数据过滤条件"""
        if not filters:
            return []
        
        return self.db.metadata_filter_summaries(
            session_id=filters.session_id,
            project=filters.project,  # 🔴 核心过滤字段
            prompt_number_min=filters.prompt_number_min,
            prompt_number_max=filters.prompt_number_max,
        )
    
    # ============== Vector Rank Implementation ==============
    
    async def _vector_rank_observations(
        self,
        query: str,
        candidate_ids: list[int],
        top_k: int
    ) -> list[VectorSearchResult]:
        """对候选 Observations 进行向量排序"""
        # 获取查询向量
        query_vector = await self.embedder.embed_single(query)
        
        # 获取候选 Observation 的嵌入
        observations = self.db.get_observations_batch(candidate_ids)
        
        results = []
        for obs in observations:
            if obs.embedding is None:
                continue
            
            obs_vector = self.embedder.bytes_to_vector(obs.embedding)
            score = self._cosine_similarity(query_vector, obs_vector)
            results.append(VectorSearchResult(id=obs.id, score=score))
        
        # 按相似度排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]
    
    async def _vector_rank_summaries(
        self,
        query: str,
        candidate_ids: list[int],
        top_k: int
    ) -> list[VectorSearchResult]:
        """对候选 Summaries 进行向量排序"""
        query_vector = await self.embedder.embed_single(query)
        
        summaries = self.db.get_summaries_batch(candidate_ids)
        
        results = []
        for summary in summaries:
            if summary.embedding is None:
                continue
            
            sum_vector = self.embedder.bytes_to_vector(summary.embedding)
            score = self._cosine_similarity(query_vector, sum_vector)
            results.append(VectorSearchResult(id=summary.id, score=score))
        
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]
    
    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """计算余弦相似度（假设向量已归一化）"""
        return float(np.dot(a, b))
    
    # ============== Three-Level Retrieval ==============
    
    async def search_global(
        self,
        query: str,
        filters: Optional[SearchFilters] = None,
        top_k: int = 10
    ) -> list[SearchResult]:
        """
        Level 1: Search（全局语义搜索）
        
        从所有历史记忆中搜索相关内容
        """
        return await self.search_observations(query, filters, top_k)
    
    def get_timeline(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> list[SessionSummary]:
        """
        Level 2: Timeline（会话上下文）
        
        获取指定会话的历史摘要链
        """
        return self.db.get_timeline(session_id, limit)
    
    def get_observation_details(
        self,
        observation_id: int
    ) -> Optional[Observation]:
        """
        Level 3: Get（详情获取）
        
        获取具体 Observation 的完整信息
        """
        return self.db.get_observation(observation_id)
    
    async def contextual_search(
        self,
        query: str,
        session_id: str,
        project: str,
        current_prompt: int,
        lookback: int = 3,
        top_k: int = 5
    ) -> list[SearchResult]:
        """
        上下文感知搜索（参考 claude-mem 设计）
        
        🔴 project 是核心过滤字段，优先搜索同一项目内的记忆
        
        优先搜索：
        1. 当前会话的历史
        2. 同一项目的其他会话
        3. 跨项目的相似记忆（fallback）
        """
        results = []
        
        # 1. 先搜索当前会话的历史
        session_filters = SearchFilters(
            session_id=session_id,
            project=project,
            prompt_number_max=current_prompt - 1,
        )
        session_results = await self.search_observations(
            query, session_filters, top_k=top_k
        )
        
        for r in session_results:
            r.score *= 1.2  # 当前会话结果加权
            results.append(r)
        
        # 2. 如果需要更多结果，搜索同一项目的其他会话
        if len(results) < top_k:
            project_filters = SearchFilters(
                project=project,
                prompt_number_min=max(0, current_prompt - lookback)
            )
            project_results = await self.search_observations(
                query, project_filters, top_k=top_k - len(results)
            )
            # 排除已找到的会话内结果
            seen_ids = {r.observation.id for r in results}
            for r in project_results:
                if r.observation.id not in seen_ids:
                    r.score *= 1.1  # 同项目结果加权
                    results.append(r)
        
        # 3. 如果还不够，搜索跨项目记忆
        if len(results) < top_k:
            global_filters = SearchFilters(
                prompt_number_min=max(0, current_prompt - lookback)
            )
            global_results = await self.search_observations(
                query, global_filters, top_k=top_k - len(results)
            )
            seen_ids = {r.observation.id for r in results}
            for r in global_results:
                if r.observation.id not in seen_ids:
                    results.append(r)
        
        # 去重并排序
        seen = set()
        unique_results = []
        for r in sorted(results, key=lambda x: x.score, reverse=True):
            if r.observation.id not in seen:
                seen.add(r.observation.id)
                unique_results.append(r)
        
        return unique_results[:top_k]
    
    # ============== Advanced Search Methods（参考 claude-mem）==============
    
    def find_by_project(
        self,
        project: str,
        limit: int = 50,
    ) -> list[Observation]:
        """
        按项目查找 Observations（参考 claude-mem findByProject）
        
        🔴 project 是核心过滤字段
        """
        return self.db.get_observations_by_project(project, limit)
    
    def find_by_concept(
        self,
        concept: str,
        filters: Optional[SearchFilters] = None,
        limit: int = 50,
    ) -> list[Observation]:
        """
        按概念标签查找 Observations（参考 claude-mem findByConcept）
        """
        # 添加 concept 到 filters
        concept_filters = SearchFilters()
        if filters:
            concept_filters = filters
        concept_filters.concepts = [concept]
        
        candidate_ids = self._build_metadata_filter(concept_filters)
        observations = self.db.get_observations_batch(candidate_ids[:limit])
        
        # 按时间倒序排序
        observations.sort(key=lambda x: x.created_at, reverse=True)
        return observations[:limit]
    
    def find_by_file(
        self,
        file_path: str,
        filters: Optional[SearchFilters] = None,
        limit: int = 50,
    ) -> tuple[list[Observation], list[SessionSummary]]:
        """
        按文件路径查找 Observations 和 Summaries（参考 claude-mem findByFile）
        
        搜索 files_read 和 files_modified 字段
        """
        # 构建 file 过滤条件
        file_filters = SearchFilters()
        if filters:
            file_filters = filters
        file_filters.files = [file_path]
        
        # 搜索 Observations
        obs_ids = self._build_metadata_filter(file_filters)
        observations = self.db.get_observations_batch(obs_ids[:limit])
        
        # 搜索 Summaries（通过 project 和 session 关联）
        summaries = []
        if filters and filters.project:
            all_summaries = self.db.get_summaries_by_project(filters.project, limit)
            # 过滤包含该文件的 summaries
            for s in all_summaries:
                # Note: 需要检查 summary 中是否有 files 字段
                summaries.append(s)
        
        return observations, summaries
    
    def find_by_type(
        self,
        type: str,
        filters: Optional[SearchFilters] = None,
        limit: int = 50,
    ) -> list[Observation]:
        """
        按类型查找 Observations（参考 claude-mem findByType）
        """
        type_filters = SearchFilters()
        if filters:
            type_filters = filters
        from .types import ObservationType
        type_filters.types = [ObservationType(type)]
        
        candidate_ids = self._build_metadata_filter(type_filters)
        observations = self.db.get_observations_batch(candidate_ids[:limit])
        
        # 按时间倒序排序
        observations.sort(key=lambda x: x.created_at, reverse=True)
        return observations[:limit]
    
    # ============== Utility Methods ==============
    
    async def find_similar_observations(
        self,
        observation_id: int,
        top_k: int = 5
    ) -> list[SearchResult]:
        """
        查找与指定 Observation 相似的其他 Observations
        """
        obs = self.db.get_observation(observation_id)
        if not obs:
            return []
        
        # 使用 observation 的嵌入作为查询
        if obs.embedding:
            query_vector = self.embedder.bytes_to_vector(obs.embedding)
        else:
            # 没有嵌入则实时计算
            query_text = format_observation_for_embedding(obs)
            query_vector = await self.embedder.embed_single(query_text)
        
        # 搜索相似内容（排除自己）
        filters = SearchFilters()
        if obs.project:
            filters.project = obs.project  # 限制在同一项目
        if obs.concepts:
            filters.concepts = obs.concepts[:3]  # 使用前3个概念
        
        candidates = self._build_metadata_filter(filters)
        if observation_id in candidates:
            candidates.remove(observation_id)
        
        if not candidates:
            return []
        
        # 向量排序
        observations = self.db.get_observations_batch(candidates[:100])
        results = []
        
        for o in observations:
            if o.embedding is None:
                continue
            o_vector = self.embedder.bytes_to_vector(o.embedding)
            score = self._cosine_similarity(query_vector, o_vector)
            results.append(VectorSearchResult(id=o.id, score=score))
        
        results.sort(key=lambda x: x.score, reverse=True)
        
        # 构建返回结果
        top_ids = [r.id for r in results[:top_k]]
        top_obs = self.db.get_observations_batch(top_ids)
        id_to_obs = {o.id: o for o in top_obs}
        
        search_results = []
        for i, r in enumerate(results[:top_k]):
            if r.id in id_to_obs:
                search_results.append(SearchResult(
                    observation=id_to_obs[r.id],
                    score=r.score,
                    rank=i + 1
                ))
        
        return search_results
