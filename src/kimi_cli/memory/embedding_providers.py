"""
Embedding Providers

多厂商嵌入模型适配器：
- Kimi (Moonshot): text-embedding-v1/v2/v3
- GLM (Zhipu): embedding-2/3
- Qwen (Aliyun): text-embedding-v1/v2/v3
- OpenAI: text-embedding-3-small/large, ada-002

设计原则：
1. 优先复用 KimiSoul 的 LLM 客户端
2. 支持独立配置（成本敏感场景）
3. 统一的 embed() 接口
"""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional
import numpy as np


@dataclass
class EmbeddingConfig:
    """嵌入模型配置"""
    provider: str = "kimi"          # kimi, glm, qwen, openai
    model: Optional[str] = None     # 具体模型名，None 使用默认
    api_key: Optional[str] = None   # 可选，默认从环境变量读取
    base_url: Optional[str] = None  # 可选，自定义 API 地址
    dimensions: int = 1024          # 向量维度
    batch_size: int = 10            # 批处理大小


class EmbeddingProvider(ABC):
    """
    嵌入模型提供者抽象基类
    """
    
    DEFAULT_MODEL: str = ""
    DEFAULT_DIMENSIONS: int = 1024
    
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self._cache: dict[str, np.ndarray] = {}  # 简单内存缓存
    
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        """
        将文本列表转换为向量列表
        
        Args:
            texts: 输入文本列表
            
        Returns:
            向量数组列表（已归一化）
        """
        pass
    
    async def embed_single(self, text: str) -> np.ndarray:
        """嵌入单个文本"""
        results = await self.embed([text])
        return results[0]
    
    def _get_cache_key(self, text: str) -> str:
        """生成缓存键"""
        return hashlib.md5(f"{self.config.provider}:{self.config.model}:{text}".encode()).hexdigest()
    
    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        """L2 归一化向量"""
        norm = np.linalg.norm(vector)
        if norm > 0:
            return vector / norm
        return vector
    
    def vector_to_bytes(self, vector: np.ndarray) -> bytes:
        """将向量转换为二进制 blob 存储"""
        return vector.astype(np.float32).tobytes()
    
    def bytes_to_vector(self, blob: bytes) -> np.ndarray:
        """从二进制 blob 恢复向量"""
        return np.frombuffer(blob, dtype=np.float32).copy()


class KimiEmbeddingProvider(EmbeddingProvider):
    """
    Moonshot Kimi 嵌入模型
    
    Models:
    - text-embedding-v1: 1024 维
    - text-embedding-v2: 1024 维（推荐）
    - text-embedding-v3: 1024 维
    
    API: https://api.moonshot.cn/v1/embeddings
    """
    
    DEFAULT_MODEL = "text-embedding-v2"
    DEFAULT_DIMENSIONS = 1024
    
    def __init__(self, config: EmbeddingConfig, llm_client=None):
        super().__init__(config)
        self.model = config.model or self.DEFAULT_MODEL
        self.dimensions = config.dimensions or self.DEFAULT_DIMENSIONS
        
        # 优先复用已有的 LLM 客户端
        self._llm_client = llm_client
        self._api_key = config.api_key
        self._base_url = config.base_url or "https://api.moonshot.cn/v1"
    
    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        # 检查缓存
        results = []
        uncached_texts = []
        uncached_indices = []
        
        for i, text in enumerate(texts):
            cache_key = self._get_cache_key(text)
            if cache_key in self._cache:
                results.append((i, self._cache[cache_key]))
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
                results.append((i, None))  # 占位
        
        if uncached_texts:
            # 调用 API
            vectors = await self._call_api(uncached_texts)
            
            # 填充结果并缓存
            for idx, orig_idx in enumerate(uncached_indices):
                vector = self._normalize(vectors[idx])
                results[orig_idx] = (orig_idx, vector)
                self._cache[self._get_cache_key(uncached_texts[idx])] = vector
        
        # 按原始顺序返回
        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]
    
    async def _call_api(self, texts: list[str]) -> list[np.ndarray]:
        """调用 Kimi API"""
        import os
        
        api_key = self._api_key or os.getenv("KIMI_API_KEY")
        if not api_key:
            raise ValueError("Kimi API key not found. Set KIMI_API_KEY env var or pass api_key in config.")
        
        # 如果提供了 LLM 客户端，尝试复用
        if self._llm_client and hasattr(self._llm_client, 'embeddings'):
            response = await self._llm_client.embeddings.create(
                model=self.model,
                input=texts
            )
            return [np.array(data.embedding) for data in response.data]
        
        # 否则使用 httpx 直接调用
        import httpx
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "input": texts
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/embeddings",
                headers=headers,
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
            return [np.array(item['embedding']) for item in data['data']]


class GLMEmbeddingProvider(EmbeddingProvider):
    """
    智谱 GLM 嵌入模型
    
    基于智谱 AI Embedding API: https://docs.bigmodel.cn/api-reference/
    
    Models:
    - embedding-2: 1024 维
    - embedding-3: 2048 维
    
    API: https://open.bigmodel.cn/api/paas/v4/embeddings
    
    认证方式: Authorization: Bearer {api_key}
    请求格式: {"model": "embedding-2", "input": "文本"} 或 {"input": ["文本1", "文本2"]}
    """
    
    DEFAULT_MODEL = "embedding-2"
    DEFAULT_DIMENSIONS = 1024
    
    def __init__(self, config: EmbeddingConfig):
        super().__init__(config)
        self.model = config.model or self.DEFAULT_MODEL
        self.dimensions = config.dimensions or self.DEFAULT_DIMENSIONS
        self._api_key = config.api_key
        self._base_url = config.base_url or "https://open.bigmodel.cn/api/paas/v4"
    
    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        """
        将文本列表转换为向量列表
        
        GLM API 支持批量请求，这里使用批量方式提高效率
        """
        import os
        import httpx
        
        api_key = self._api_key or os.getenv("ZHIPU_API_KEY")
        if not api_key:
            raise ValueError(
                "GLM API key not found. Set ZHIPU_API_KEY env var or pass api_key in config. "
                "Get your API key from: https://bigmodel.cn/usercenter/apikeys"
            )
        
        # 检查缓存，分离需要请求的文本
        results = [None] * len(texts)
        uncached_texts = []
        uncached_indices = []
        
        for i, text in enumerate(texts):
            cache_key = self._get_cache_key(text)
            if cache_key in self._cache:
                results[i] = self._cache[cache_key]
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
        
        if not uncached_texts:
            return results
        
        # 准备请求
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # GLM 支持批量嵌入，直接将列表作为 input
        payload = {
            "model": self.model,
            "input": uncached_texts
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers=headers,
                    json=payload,
                    timeout=60.0
                )
                response.raise_for_status()
                data = response.json()
                
                # 解析响应
                # data['data'] 是列表，每个元素包含 embedding, index, object
                embeddings_data = data.get('data', [])
                
                # 按 index 排序确保顺序正确
                embeddings_data.sort(key=lambda x: x.get('index', 0))
                
                for item in embeddings_data:
                    idx = item.get('index', 0)
                    if idx < len(uncached_texts):
                        original_idx = uncached_indices[idx]
                        vector = self._normalize(np.array(item['embedding']))
                        # 缓存结果
                        cache_key = self._get_cache_key(uncached_texts[idx])
                        self._cache[cache_key] = vector
                        results[original_idx] = vector
                
        except httpx.HTTPStatusError as e:
            error_msg = f"GLM API error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                if 'error' in error_data:
                    error_msg = f"GLM API error: {error_data['error'].get('message', str(e))}"
            except Exception:
                pass
            raise ValueError(error_msg) from e
        except Exception as e:
            raise ValueError(f"Failed to get embeddings from GLM: {str(e)}") from e
        
        return results


class QwenEmbeddingProvider(EmbeddingProvider):
    """
    阿里通义千问嵌入模型
    
    Models:
    - text-embedding-v1: 1536 维
    - text-embedding-v2: 1536 维
    - text-embedding-v3: 1024 维
    
    API: https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding
    """
    
    DEFAULT_MODEL = "text-embedding-v3"
    DEFAULT_DIMENSIONS = 1024
    
    def __init__(self, config: EmbeddingConfig):
        super().__init__(config)
        self.model = config.model or self.DEFAULT_MODEL
        self.dimensions = config.dimensions or self.DEFAULT_DIMENSIONS
        self._api_key = config.api_key
    
    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        import os
        import httpx
        
        api_key = self._api_key or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("DashScope API key not found. Set DASHSCOPE_API_KEY env var or pass api_key in config.")
        
        results = []
        
        # DashScope 支持批量
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "input": {"texts": texts}
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding",
                headers=headers,
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
            
            for item in data['output']['embeddings']:
                text = item['text']
                vector = self._normalize(np.array(item['embedding']))
                cache_key = self._get_cache_key(text)
                self._cache[cache_key] = vector
                results.append(vector)
        
        return results


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    OpenAI 嵌入模型
    
    Models:
    - text-embedding-3-small: 1536 维（便宜）
    - text-embedding-3-large: 3072 维（准确）
    - text-embedding-ada-002: 1536 维（旧版）
    
    API: https://api.openai.com/v1/embeddings
    """
    
    DEFAULT_MODEL = "text-embedding-3-small"
    DEFAULT_DIMENSIONS = 1536
    
    def __init__(self, config: EmbeddingConfig):
        super().__init__(config)
        self.model = config.model or self.DEFAULT_MODEL
        self.dimensions = config.dimensions or self.DEFAULT_DIMENSIONS
        self._api_key = config.api_key
        self._base_url = config.base_url or "https://api.openai.com/v1"
    
    async def embed(self, texts: list[str]) -> list[np.ndarray]:
        import os
        import httpx
        
        api_key = self._api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not found. Set OPENAI_API_KEY env var or pass api_key in config.")
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "input": texts
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/embeddings",
                headers=headers,
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data['data']:
                # OpenAI 返回的 embedding 可能需要在指定维度截断
                vector = np.array(item['embedding'])
                if len(vector) > self.dimensions:
                    vector = vector[:self.dimensions]
                vector = self._normalize(vector)
                results.append(vector)
            
            return results


# ============== Factory ==============

_PROVIDERS = {
    "kimi": KimiEmbeddingProvider,
    "glm": GLMEmbeddingProvider,
    "qwen": QwenEmbeddingProvider,
    "openai": OpenAIEmbeddingProvider,
}


def create_embedding_provider(
    provider: str = "kimi",
    llm_client=None,
    **kwargs
) -> EmbeddingProvider:
    """
    创建嵌入模型提供者
    
    Args:
        provider: 提供者名称 (kimi/glm/qwen/openai)
        llm_client: 可选，复用的 LLM 客户端
        **kwargs: 传递给 EmbeddingConfig 的参数
        
    Returns:
        EmbeddingProvider 实例
        
    Examples:
        >>> # 使用 Kimi（默认）
        >>> provider = create_embedding_provider()
        >>> 
        >>> # 使用 GLM
        >>> provider = create_embedding_provider("glm", model="embedding-3")
        >>> 
        >>> # 复用现有的 LLM 客户端
        >>> provider = create_embedding_provider("kimi", llm_client=runtime.llm)
    """
    provider = provider.lower()
    
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown embedding provider: {provider}. "
                        f"Supported: {list(_PROVIDERS.keys())}")
    
    config = EmbeddingConfig(provider=provider, **kwargs)
    provider_class = _PROVIDERS[provider]
    
    # Kimi 支持复用 llm_client
    if provider == "kimi" and llm_client is not None:
        return provider_class(config, llm_client=llm_client)
    
    return provider_class(config)


def get_available_providers() -> list[str]:
    """获取支持的提供者列表"""
    return list(_PROVIDERS.keys())
