#!/usr/bin/env python3
"""
Memory System Demo

演示 OKbot 记忆系统的基本用法。

Usage:
    python examples/memory_demo.py
"""

import argparse
import asyncio
import os
import tempfile
from datetime import datetime

from kimi_cli.memory import (
    MemoryAgent,
    ObservationInput,
    ObservationType,
    SummaryInput,
    SearchFilters,
    format_observation_for_embedding,
    format_summary_for_embedding,
)
from kimi_cli.memory.embedding_providers import (
    EmbeddingProvider, 
    EmbeddingConfig,
    create_embedding_provider
)
from kimi_cli.memory.schema import MemoryDatabase
import numpy as np


class MockEmbedder(EmbeddingProvider):
    """Mock embedder for demo (no API key needed)"""
    
    DEFAULT_DIMENSIONS = 128
    
    def __init__(self):
        super().__init__(EmbeddingConfig(provider="mock"))
        self.dimensions = self.DEFAULT_DIMENSIONS
    
    async def embed(self, texts: list[str]) -> list:
        import hashlib
        results = []
        for text in texts:
            seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % (2**32)
            np.random.seed(seed)
            vector = np.random.randn(self.dimensions).astype(np.float32)
            vector = self._normalize(vector)
            results.append(vector)
        return results


def create_demo_embedder(provider_name: str = "mock"):
    """
    创建用于 demo 的 embedder
    
    Args:
        provider_name: "mock", "kimi", "glm", "qwen", "openai"
    
    注意：除了 mock，其他都需要对应的 API key
    """
    if provider_name == "mock":
        return MockEmbedder()
    
    # 使用真实的 API provider
    try:
        return create_embedding_provider(provider_name)
    except Exception as e:
        print(f"Warning: Failed to create {provider_name} embedder: {e}")
        print("Falling back to mock embedder")
        return MockEmbedder()


async def demo_memory_system(provider: str = "mock"):
    """
    演示记忆系统的完整功能
    
    Args:
        provider: "mock", "kimi", "glm", "qwen", "openai"
    """
    print("=" * 60)
    print(f"OKbot Memory System Demo (Provider: {provider})")
    print("=" * 60)
    
    # 创建临时数据库
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    print(f"\n1. 创建 MemoryAgent (数据库: {db_path})")
    
    # 创建 embedder（支持 mock 或真实 API）
    embedder = create_demo_embedder(provider)
    db = MemoryDatabase(db_path)
    agent = MemoryAgent(db=db, embedder=embedder)
    
    print("2. 启动 MemoryAgent")
    await agent.start()
    
    print("3. 记录一些 Observations")
    
    # 模拟一些开发活动
    observations = [
        ObservationInput(
            session_id="session-001",
            type=ObservationType.BUGFIX,
            title="Fixed JWT token validation",
            subtitle="Token expiration check was incorrect",
            facts=["JWT was expiring 1 hour early", "Fixed timezone handling"],
            narrative="Found that the JWT validation was using local time instead of UTC",
            concepts=["auth", "jwt", "token", "timezone"],
            files_modified=["src/auth/jwt.py"],
            tool_name="StrReplaceFile",
            prompt_number=1,
        ),
        ObservationInput(
            session_id="session-001",
            type=ObservationType.FEATURE,
            title="Added user profile API",
            subtitle="GET /api/users/{id}/profile",
            facts=["New endpoint returns user profile", "Includes avatar URL"],
            concepts=["api", "rest", "user"],
            files_modified=["src/api/users.py", "src/models/user.py"],
            tool_name="WriteFile",
            prompt_number=2,
        ),
        ObservationInput(
            session_id="session-001",
            type=ObservationType.REFACTOR,
            title="Refactored database connection pool",
            subtitle="Improved connection reuse",
            facts=["Reduced connection overhead by 50%"],
            concepts=["database", "performance", "refactor"],
            files_modified=["src/db/pool.py"],
            tool_name="StrReplaceFile",
            prompt_number=3,
        ),
        ObservationInput(
            session_id="session-002",  # Different session
            type=ObservationType.DISCOVERY,
            title="Found performance issue in query",
            subtitle="Missing index on user_id column",
            facts=["Query was doing full table scan"],
            concepts=["database", "performance", "index"],
            tool_name="Shell",
            prompt_number=1,
        ),
    ]
    
    # 手动计算嵌入并保存（实际使用中通过 queue_observation 自动处理）
    for obs in observations:
        embed_text = format_observation_for_embedding(obs)
        vector = await embedder.embed_single(embed_text)
        embedding_blob = embedder.vector_to_bytes(vector)
        obs_id = db.insert_observation(obs, embedding_blob)
        print(f"   ✓ Saved: {obs.title} (ID: {obs_id})")
    
    print("\n4. 记录 Session Summaries")
    
    summaries = [
        SummaryInput(
            session_id="session-001",
            request="Fix authentication bugs",
            investigated="JWT token validation logic",
            learned="Timezone handling is critical for JWT",
            completed="Fixed token validation, added tests",
            next_steps="Deploy to staging",
            prompt_number=1,
        ),
        SummaryInput(
            session_id="session-001",
            request="Add user profile API",
            investigated="Existing user endpoints",
            learned="Need to handle avatar upload separately",
            completed="Implemented GET /api/users/{id}/profile",
            next_steps="Add avatar upload endpoint",
            prompt_number=2,
        ),
    ]
    
    for summary in summaries:
        embed_text = format_summary_for_embedding(summary)
        vector = await embedder.embed_single(embed_text)
        embedding_blob = embedder.vector_to_bytes(vector)
        sum_id = db.insert_summary(summary, embedding_blob)
        print(f"   ✓ Saved summary for prompt {summary.prompt_number} (ID: {sum_id})")
    
    print("\n5. 搜索记忆")
    
    # 按关键词搜索
    print("\n   a) 搜索 'authentication':")
    results = await agent.search("authentication", top_k=5)
    for r in results["observations"]:
        print(f"      - [{r.observation.type.value}] {r.observation.title} (score: {r.score:.3f})")
    
    # 带过滤器的搜索
    print("\n   b) 搜索 'performance' 类型的 observations:")
    results = await agent.search(
        "performance",
        filters=SearchFilters(types=[ObservationType.REFACTOR, ObservationType.DISCOVERY]),
        top_k=5
    )
    for r in results["observations"]:
        print(f"      - [{r.observation.type.value}] {r.observation.title} (score: {r.score:.3f})")
    
    # 搜索特定会话
    print("\n   c) 搜索 session-001 中的 'API':")
    results = await agent.search(
        "API",
        filters=SearchFilters(session_id="session-001"),
        top_k=5
    )
    for r in results["observations"]:
        print(f"      - [{r.observation.type.value}] {r.observation.title} (score: {r.score:.3f})")
    
    print("\n6. 获取会话时间线")
    
    timeline = agent.get_timeline("session-001")
    print(f"   Session-001 有 {len(timeline)} 个 summaries:")
    for s in timeline:
        print(f"   - Prompt {s.prompt_number}: {s.request}")
        print(f"     Completed: {s.completed}")
    
    print("\n7. 获取统计信息")
    
    stats = agent.get_stats()
    print(f"   Total observations: {stats['total_observations']}")
    print(f"   Total summaries: {stats['total_summaries']}")
    print(f"   Queue size: {stats['queue_size']}")
    
    print("\n8. 停止 MemoryAgent")
    await agent.stop()
    
    print("\n" + "=" * 60)
    print("Demo completed!")
    print("=" * 60)


async def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="OKbot Memory System Demo")
    parser.add_argument(
        "--provider", 
        default="mock",
        choices=["mock", "kimi", "glm", "qwen", "openai"],
        help="Embedding provider to use (default: mock)"
    )
    parser.add_argument(
        "--glm-api-key",
        default=None,
        help="GLM API key (or set ZHIPU_API_KEY env var)"
    )
    args = parser.parse_args()
    
    # 如果提供了 GLM API key，设置环境变量
    if args.glm_api_key:
        os.environ["ZHIPU_API_KEY"] = args.glm_api_key
    
    try:
        await demo_memory_system(args.provider)
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
