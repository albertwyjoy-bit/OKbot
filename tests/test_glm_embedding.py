#!/usr/bin/env python3
"""
Test script for GLM Embedding API

Usage:
    export ZHIPU_API_KEY=your_api_key
    python tests/test_glm_embedding.py
"""

import asyncio
import os
import sys

sys.path.insert(0, 'src')

import numpy as np
from kimi_cli.memory.embedding_providers import GLMEmbeddingProvider, EmbeddingConfig


async def test_glm_embedding():
    """测试 GLM Embedding API"""
    
    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        print("❌ Error: ZHIPU_API_KEY environment variable not set")
        print("   Get your API key from: https://bigmodel.cn/usercenter/apikeys")
        return False
    
    print("=" * 60)
    print("GLM Embedding API Test")
    print("=" * 60)
    
    # 测试 embedding-2 (默认)
    print("\n1. Testing embedding-2 (1024 dim)")
    config = EmbeddingConfig(
        provider="glm",
        model="embedding-2",
        dimensions=1024,
        api_key=api_key
    )
    provider = GLMEmbeddingProvider(config)
    
    # 单文本测试
    text = "这是一个测试文本"
    print(f"   Input: {text}")
    
    try:
        vector = await provider.embed_single(text)
        print(f"   ✓ Success! Vector shape: {vector.shape}")
        print(f"   ✓ Vector norm (should be ~1.0): {np.linalg.norm(vector):.6f}")
        assert len(vector) == 1024, f"Expected 1024 dimensions, got {len(vector)}"
        assert abs(np.linalg.norm(vector) - 1.0) < 0.01, "Vector should be normalized"
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False
    
    # 批量测试
    print("\n2. Testing batch embedding")
    texts = [
        "智谱 AI 是一家中国人工智能公司",
        "GLM 是大语言模型",
        "Embedding 用于文本向量化",
    ]
    print(f"   Inputs: {texts}")
    
    try:
        vectors = await provider.embed(texts)
        print(f"   ✓ Success! Got {len(vectors)} vectors")
        for i, vec in enumerate(vectors):
            print(f"   - Vector {i+1}: shape={vec.shape}, norm={np.linalg.norm(vec):.6f}")
        assert len(vectors) == len(texts), f"Expected {len(texts)} vectors, got {len(vectors)}"
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False
    
    # 测试缓存
    print("\n3. Testing cache (should be instant)")
    try:
        import time
        start = time.time()
        vector_cached = await provider.embed_single(text)
        elapsed = time.time() - start
        print(f"   ✓ Cache hit! Time: {elapsed:.4f}s")
        assert elapsed < 0.01, "Cache should be very fast"
        assert np.allclose(vector, vector_cached), "Cached vector should match"
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False
    
    # 测试相似度计算
    print("\n4. Testing similarity calculation")
    try:
        vec1 = await provider.embed_single("机器学习")
        vec2 = await provider.embed_single("深度学习")
        vec3 = await provider.embed_single("苹果派")
        
        sim_1_2 = np.dot(vec1, vec2)  # 余弦相似度（已归一化）
        sim_1_3 = np.dot(vec1, vec3)
        
        print(f"   '机器学习' vs '深度学习': {sim_1_2:.4f}")
        print(f"   '机器学习' vs '苹果派': {sim_1_3:.4f}")
        print(f"   ✓ Similar concepts have higher similarity: {sim_1_2 > sim_1_3}")
        assert sim_1_2 > sim_1_3, "Similar concepts should have higher similarity"
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False
    
    # 测试 embedding-3
    print("\n5. Testing embedding-3 (2048 dim)")
    config_3 = EmbeddingConfig(
        provider="glm",
        model="embedding-3",
        dimensions=2048,
        api_key=api_key
    )
    provider_3 = GLMEmbeddingProvider(config_3)
    
    try:
        vector_3 = await provider_3.embed_single("测试 embedding-3 模型")
        print(f"   ✓ Success! Vector shape: {vector_3.shape}")
        assert len(vector_3) == 2048, f"Expected 2048 dimensions, got {len(vector_3)}"
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    return True


async def test_error_handling():
    """测试错误处理"""
    print("\n" + "=" * 60)
    print("Error Handling Test")
    print("=" * 60)
    
    # 测试无效 API Key
    print("\n1. Testing invalid API key")
    config = EmbeddingConfig(
        provider="glm",
        model="embedding-2",
        api_key="invalid_key"
    )
    provider = GLMEmbeddingProvider(config)
    
    try:
        await provider.embed_single("测试")
        print("   ❌ Should have raised an error")
        return False
    except ValueError as e:
        print(f"   ✓ Correctly raised error: {e}")
    except Exception as e:
        print(f"   ✓ Raised error: {type(e).__name__}: {e}")
    
    # 测试空 API Key
    print("\n2. Testing missing API key")
    # 临时清除环境变量
    old_key = os.getenv("ZHIPU_API_KEY")
    if old_key:
        if "ZHIPU_API_KEY" in os.environ:
            del os.environ["ZHIPU_API_KEY"]
    
    config_no_key = EmbeddingConfig(provider="glm")
    provider_no_key = GLMEmbeddingProvider(config_no_key)
    
    try:
        await provider_no_key.embed_single("测试")
        print("   ❌ Should have raised an error")
        if old_key:
            os.environ["ZHIPU_API_KEY"] = old_key
        return False
    except ValueError as e:
        print(f"   ✓ Correctly raised error: {e}")
    
    # 恢复环境变量
    if old_key:
        os.environ["ZHIPU_API_KEY"] = old_key
    
    print("\n" + "=" * 60)
    print("✅ Error handling tests passed!")
    print("=" * 60)
    return True


async def main():
    """主函数"""
    success = True
    
    # 主要功能测试
    if not await test_glm_embedding():
        success = False
    
    # 错误处理测试
    if not await test_error_handling():
        success = False
    
    return success


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
