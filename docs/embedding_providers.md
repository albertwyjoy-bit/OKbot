# Embedding Providers 对比与配置

OKbot 记忆系统支持多种 Embedding Provider，本文档详细介绍各提供商的特点和配置方法。

## 快速对比

| 提供商 | 默认模型 | 维度 | 中文效果 | 价格 | 速度 | 推荐场景 |
|--------|----------|------|----------|------|------|----------|
| **Kimi** | text-embedding-v2 | 1024 | ⭐⭐⭐⭐⭐ | 中 | 快 | 默认推荐，无需额外配置 |
| **GLM** | embedding-2 | 1024 | ⭐⭐⭐⭐⭐ | 低 | 快 | 性价比首选 |
| **GLM** | embedding-3 | 2048 | ⭐⭐⭐⭐⭐ | 中 | 快 | 需要更高精度 |
| **Qwen** | text-embedding-v3 | 1024 | ⭐⭐⭐⭐ | 低 | 快 | 阿里云用户 |
| **OpenAI** | text-embedding-3-small | 1536 | ⭐⭐⭐ | 低 | 快 | 海外用户 |
| **OpenAI** | text-embedding-3-large | 3072 | ⭐⭐⭐ | 高 | 快 | 高精度需求 |

## Kimi (Moonshot)

**默认推荐**，与 OKbot 主 LLM 无缝集成。

### 配置

```toml
[memory]
provider = "kimi"
model = "text-embedding-v2"  # 或 text-embedding-v1, text-embedding-v3
```

### 环境变量

```bash
export KIMI_API_KEY=your_key
```

### 特点

- ✅ 复用主 LLM 客户端
- ✅ 中文效果优秀
- ✅ 无需额外配置

---

## GLM (智谱 AI)

**性价比首选**，中文语义理解优秀。

### 配置

```toml
[memory]
provider = "glm"
model = "embedding-2"  # 或 embedding-3
```

### 环境变量

```bash
export ZHIPU_API_KEY=your_key
```

### 获取 API Key

1. 访问 https://bigmodel.cn/usercenter/apikeys
2. 创建新的 API Key
3. 复制并设置环境变量

### 模型对比

| 模型 | 维度 | 适用场景 | 价格 |
|------|------|----------|------|
| embedding-2 | 1024 | 通用场景，性价比高 | ¥0.0005/千token |
| embedding-3 | 2048 | 需要更高精度的场景 | ¥0.002/千token |

### 特点

- ✅ 中文效果优秀
- ✅ 价格便宜
- ✅ 支持批量嵌入
- ⚠️ 需要单独配置 API Key

### 示例代码

```python
from kimi_cli.memory import create_embedding_provider

provider = create_embedding_provider(
    provider="glm",
    model="embedding-2"
)

# 单文本
vector = await provider.embed_single("智谱 AI 是一家中国公司")
print(vector.shape)  # (1024,)

# 批量文本（更高效）
vectors = await provider.embed(["文本1", "文本2", "文本3"])
```

---

## Qwen (阿里通义千问)

适合阿里云生态用户。

### 配置

```toml
[memory]
provider = "qwen"
model = "text-embedding-v3"  # 或 v1, v2
```

### 环境变量

```bash
export DASHSCOPE_API_KEY=your_key
```

### 获取 API Key

1. 访问 https://dashscope.console.aliyun.com/apiKey
2. 创建新的 API Key

---

## OpenAI

适合海外用户或已有 OpenAI 账号的用户。

### 配置

```toml
[memory]
provider = "openai"
model = "text-embedding-3-small"  # 或 text-embedding-3-large, ada-002
```

### 环境变量

```bash
export OPENAI_API_KEY=your_key
```

### 模型对比

| 模型 | 维度 | 特点 | 价格 |
|------|------|------|------|
| text-embedding-3-small | 1536 | 价格便宜，性能良好 | $0.02/1M tokens |
| text-embedding-3-large | 3072 | 精度最高 | $0.13/1M tokens |
| ada-002 | 1536 | 旧版模型 | $0.10/1M tokens |

---

## 性能优化建议

### 1. 批量请求

所有 provider 都支持批量嵌入，批量请求比单条请求效率高：

```python
# 不推荐：单条循环
vectors = [await provider.embed_single(text) for text in texts]

# 推荐：批量请求
vectors = await provider.embed(texts)
```

### 2. 缓存

Embedding 结果会自动缓存，相同的文本不会重复请求 API：

```python
# 第一次会调用 API
vec1 = await provider.embed_single("测试文本")

# 第二次直接返回缓存结果（极快）
vec2 = await provider.embed_single("测试文本")
```

### 3. 选择合适的维度

- 1024 维：大多数场景足够
- 1536 维（OpenAI）：兼容性好
- 2048 维（GLM-3）：需要更高精度
- 3072 维（OpenAI large）：最高精度

**建议**：先从 1024 维开始，如有需要再升级。

---

## 故障排除

### API Key 错误

```
ValueError: GLM API key not found. Set ZHIPU_API_KEY env var...
```

**解决**：设置对应的环境变量
```bash
export ZHIPU_API_KEY=your_key
export KIMI_API_KEY=your_key
export DASHSCOPE_API_KEY=your_key
export OPENAI_API_KEY=your_key
```

### API 调用失败

```
GLM API error: 401 Unauthorized
```

**可能原因**：
1. API Key 错误或过期的
2. 账户余额不足
3. 网络问题

**解决**：
1. 检查 API Key 是否正确
2. 在控制台检查账户余额
3. 检查网络连接

### 向量维度不匹配

如果手动指定了 `dimensions` 但与模型实际维度不符，可能会导致错误。

**建议**：使用默认维度，或确保指定的维度与模型匹配：
- Kimi: 1024
- GLM-2: 1024
- GLM-3: 2048
- Qwen-v3: 1024
- OpenAI small: 1536
- OpenAI large: 3072

---

## 测试

### 测试 GLM

```bash
export ZHIPU_API_KEY=your_key
python tests/test_glm_embedding.py
```

### 测试其他 Provider

修改测试脚本中的 provider 名称即可测试其他提供商。

---

## 参考链接

- **GLM**: https://docs.bigmodel.cn/api-reference/
- **Kimi**: https://platform.moonshot.cn/docs/api-reference
- **Qwen**: https://help.aliyun.com/zh/dashscope/developer-reference/text-embedding-api-details
- **OpenAI**: https://platform.openai.com/docs/guides/embeddings
