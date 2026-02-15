"""
OKbot Memory System

基于 claude-mem 架构适配的记忆系统：
- Observations: 工具级别的原子记忆
- SessionSummaries: 会话级别的摘要记忆
- UserPrompts: 用户输入的原始请求
- Hybrid Search: 混合检索（元数据过滤 + 语义排序）
- Multi-provider Embeddings: 支持 Kimi/GLM/Qwen/OpenAI

Usage:
    from kimi_cli.memory import MemoryAgent
    
    agent = MemoryAgent(llm_client)
    await agent.queue_observation(ObservationInput(...))
    await agent.queue_summary(SummaryInput(...))
    await agent.queue_prompt(UserPromptInput(...))
    
    results = await agent.search(
        query="authentication bug fix",
        filters=SearchFilters(types=["bugfix"], concepts=["auth"])
    )
"""

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
from .embedding_providers import EmbeddingProvider, create_embedding_provider
from .search import HybridSearcher
from .agent import MemoryAgent

__all__ = [
    "ObservationType",
    "ObservationInput",
    "Observation",
    "SummaryInput", 
    "SessionSummary",
    "UserPromptInput",
    "UserPrompt",
    "SearchFilters",
    "SearchResult",
    "format_observation_for_embedding",
    "format_summary_for_embedding",
    "EmbeddingProvider",
    "create_embedding_provider",
    "HybridSearcher",
    "MemoryAgent",
]
