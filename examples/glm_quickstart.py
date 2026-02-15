#!/usr/bin/env python3
"""
GLM Embedding 快速入门

演示如何使用智谱 AI 的 Embedding API 进行文本向量化。

前置条件:
    1. 获取 API Key: https://bigmodel.cn/usercenter/apikeys
    2. 设置环境变量: export ZHIPU_API_KEY=your_key

Usage:
    python examples/glm_quickstart.py
"""

import asyncio
import os
import sys

sys.path.insert(0, 'src')

import numpy as np
from kimi_cli.memory import create_embedding_provider


async def main():
    """GLM Embedding 快速入门示例"""
    
    # 检查 API Key
    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        print("❌ 请设置 ZHIPU_API_KEY 环境变量")
        print("   获取地址: https://bigmodel.cn/usercenter/apikeys")
        print()
        print("   export ZHIPU_API_KEY=your_api_key_here")
        return
    
    print("=" * 60)
    print("GLM Embedding 快速入门")
    print("=" * 60)
    
    # 1. 创建 GLM Embedding Provider
    print("\n1. 创建 GLM Embedding Provider")
    provider = create_embedding_provider(
        provider="glm",
        model="embedding-2"  # 1024 维向量
    )
    print("   ✓ Provider 创建成功")
    
    # 2. 单文本嵌入
    print("\n2. 单文本嵌入示例")
    text = "智谱 AI 是一家专注于大模型技术的中国公司"
    print(f"   输入: {text}")
    
    vector = await provider.embed_single(text)
    print(f"   ✓ 输出向量: shape={vector.shape}, dtype={vector.dtype}")
    print(f"   ✓ 向量已归一化 (L2 norm = {np.linalg.norm(vector):.6f})")
    
    # 3. 批量文本嵌入（更高效）
    print("\n3. 批量文本嵌入")
    texts = [
        "人工智能正在改变世界",
        "机器学习是 AI 的重要分支",
        "深度学习使用神经网络",
        "自然语言处理让机器理解人类语言",
    ]
    print(f"   输入 {len(texts)} 个文本:")
    for i, t in enumerate(texts, 1):
        print(f"     {i}. {t}")
    
    vectors = await provider.embed(texts)
    print(f"   ✓ 输出 {len(vectors)} 个向量")
    
    # 4. 计算文本相似度
    print("\n4. 计算文本相似度")
    
    # 相关概念
    text_ai = "人工智能"
    text_ml = "机器学习"
    text_food = "北京烤鸭"
    
    vec_ai = await provider.embed_single(text_ai)
    vec_ml = await provider.embed_single(text_ml)
    vec_food = await provider.embed_single(text_food)
    
    # 余弦相似度（已归一化，直接点积即可）
    sim_ai_ml = np.dot(vec_ai, vec_ml)
    sim_ai_food = np.dot(vec_ai, vec_food)
    
    print(f"   '{text_ai}' vs '{text_ml}': {sim_ai_ml:.4f}")
    print(f"   '{text_ai}' vs '{text_food}': {sim_ai_food:.44f}")
    print(f"   ✓ AI/ML 相似度更高: {sim_ai_ml > sim_ai_food}")
    
    # 5. 搜索最相似的文本
    print("\n5. 搜索最相似的文本")
    query = "AI 技术"
    candidates = [
        "机器学习算法",
        "深度学习框架",
        "北京故宫",
        "神经网络模型",
        "四川火锅",
    ]
    
    print(f"   查询: '{query}'")
    print(f"   候选:")
    for i, c in enumerate(candidates, 1):
        print(f"     {i}. {c}")
    
    # 获取查询和候选的向量
    query_vec = await provider.embed_single(query)
    candidate_vecs = await provider.embed(candidates)
    
    # 计算相似度并排序
    similarities = [
        (candidates[i], np.dot(query_vec, candidate_vecs[i]))
        for i in range(len(candidates))
    ]
    similarities.sort(key=lambda x: x[1], reverse=True)
    
    print(f"   ✓ 最相似的 3 个:")
    for text, score in similarities[:3]:
        print(f"      - {text} (相似度: {score:.4f})")
    
    # 6. 使用 embedding-3（更高维度）
    print("\n6. 使用 embedding-3 (2048 维)")
    provider_3 = create_embedding_provider(
        provider="glm",
        model="embedding-3",
        dimensions=2048
    )
    
    vec_3 = await provider_3.embed_single("测试 embedding-3 模型")
    print(f"   ✓ 向量维度: {len(vec_3)}")
    
    print("\n" + "=" * 60)
    print("✅ GLM Embedding 快速入门完成!")
    print("=" * 60)
    print()
    print("更多信息:")
    print("  - API 文档: https://docs.bigmodel.cn/api-reference/")
    print("  - 模型对比: embedding-2 (1024维) vs embedding-3 (2048维)")
    print("  - 价格参考: https://bigmodel.cn/pricing")


if __name__ == "__main__":
    asyncio.run(main())
