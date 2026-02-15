# OKbot Memory System

OKbot 的记忆系统基于 claude-mem 架构设计，提供跨会话的长期记忆能力。

## 核心概念

### 三重存储架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Memory System                                     │
├─────────────────────────────┬─────────────────────────────┬─────────────────┤
│       Observations          │      Session Summaries      │  User Prompts   │
│    (工具级别原子记忆)        │      (会话级别摘要)          │ (用户输入记录)   │
├─────────────────────────────┼─────────────────────────────┼─────────────────┤
│ • 每个工具调用生成一个        │ • 每个 Prompt 生成一个       │ • 记录原始请求  │
│ • 自动分类 (bugfix/feature)  │ • 包含请求/完成/学习/下一步  │ • 时间线展示    │
│ • 关联文件和概念             │ • 形成时间线链条             │ • 可搜索        │
│ • 嵌入向量检索               │ • 嵌入向量检索               │ • 无时序锚点    │
└─────────────────────────────┴─────────────────────────────┴─────────────────┘
```

### 观察类型 (ObservationType)

| 类型 | 说明 | 典型场景 |
|------|------|----------|
| `bugfix` | Bug 修复 | 修复了某个错误 |
| `feature` | 新功能 | 实现了新功能 |
| `refactor` | 重构 | 代码重构 |
| `change` | 一般修改 | 普通代码修改 |
| `discovery` | 发现 | 调研、发现问题 |
| `decision` | 决策 | 做出技术决策 |

### 概念标签 (Concepts)

从操作内容自动提取的关键词，例如：
- `auth`, `jwt`, `token` - 认证相关
- `api`, `rest`, `graphql` - API 相关
- `config`, `setting` - 配置相关
- `python`, `typescript` - 语言相关
- `git`, `docker` - 工具相关

## 系统架构

### 调度流程

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              KimiSoul (主 Agent)                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  User Input                                                                     │
│      ↓                                                                          │
│  run() ───────────────────────────────────────────────────────────────────────  │
│      │                                                                          │
│      ├─▶ queue_prompt(wait=False) ─────────┐  【Turn 开始 - 异步】              │
│      │                                      ↓                                   │
│      ├─▶ _turn() ──▶ _agent_loop() ──▶ _step()                                │
│      │                                        │                                 │
│      │                                        └─▶ _grow_context()               │
│      │                                              │                           │
│      │                                              └─▶ queue_observation()     │
│      │                                                    (wait=False)          │
│      │                                                              │           │
│      │                                                              ↓           │
│      │                                                    ┌─────────────────┐   │
│      │                                                    │  Memory Agent   │   │
│      │                                                    │  ┌───────────┐  │   │
│      │                                                    │  │   Queue   │  │   │
│      │                                                    │  │(asyncio)  │  │   │
│      │                                                    │  └─────┬─────┘  │   │
│      │                                                    │        │        │   │
│      │                                                    │        ▼        │   │
│      │                                                    │  ┌───────────┐  │   │
│      │                                                    │  │   Worker  │  │   │
│      │                                                    │  │  (后台)   │  │   │
│      │                                                    │  └───────────┘  │   │
│      │                                                    └─────────────────┘   │
│      │                                                                          │
│      └─▶ _generate_and_save_summary() ──▶ queue_summary(wait=True) ◀──────────  │
│                                              【Turn 结束 - 同步等待】            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 调度时机详解

| 操作 | 时机 | 等待策略 | 超时 | 阻塞性 |
|------|------|---------|------|--------|
| **Prompt 保存** | Turn 开始 | `wait=False` | 5s | **非阻塞** |
| **Observation 保存** | 每个工具调用后 | `wait=False` | 10s | **非阻塞** |
| **Summary 保存** | Turn 结束时 | `wait=True` | 30s | **阻塞** |
| **Memory Tools** | 按需调用 | 等待结果 | - | **阻塞** |
| **启动加载** | Runtime 创建时 | 等待加载 | - | **阻塞** |

### 为什么 Summary 是阻塞的？

Summary 记录了整个对话轮次的完成情况、学习内容和下一步计划，是**会话恢复的关键数据**。设计为阻塞写入是为了确保：

1. **数据完整性**：在响应用户之前确保摘要已保存
2. **会话可恢复**：即使程序异常退出，也能从上次 Summary 恢复
3. **时间线准确**：Summary 作为时间线锚点，需要可靠持久化

### 异步队列机制

```python
# MemoryAgent 内部架构
class MemoryAgent:
    def __init__(...):
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=1000)
        self._worker_task: asyncio.Task = None  # 后台 Worker
    
    async def _process_queue(self):
        """后台工作线程，持续处理队列"""
        while not shutdown:
            item = await self._queue.get()
            try:
                if item.type == 'observation':
                    await self._persist_observation(item.data)
                elif item.type == 'summary':
                    await self._persist_summary(item.data)
                # ...
            finally:
                self._queue.task_done()
```

**队列行为**：
- 队列满时（1000项），新数据会被丢弃并记录警告
- 每个队列项包含 `asyncio.Future`，支持可选的等待
- Worker 使用 1 秒超时检查 shutdown 信号

## 混合检索 (Hybrid Search)

使用 **Filter → Rank → Intersect** 模式：

1. **Filter**: 元数据过滤（类型、会话、时间范围、概念）
2. **Rank**: 在候选集上进行向量相似度排序
3. **Intersect**: 合并结果，保持语义排序

### 三级检索接口

```python
# Level 1: Search (全局语义搜索)
results = await agent.search("authentication bug fix")

# Level 2: Timeline (会话上下文)
summaries = agent.get_timeline("session-001")

# Level 3: Get (详情获取)
obs = agent.get_observation_details(123)
```

## Memory Tools (记忆工具)

当 Memory 系统启用时，以下工具会自动注册到 Agent：

### 1. SearchMemory - 搜索记忆

```python
# 基本搜索
SearchMemory(query="authentication bug")

# 带过滤器的搜索
SearchMemory(
    query="database migration",
    type="feature",           # 类型过滤: bugfix/feature/refactor/change/discovery/decision
    obs_type="observation",   # 记录类型: observation/session/prompt
    project="/path/to/project",
    dateStart="2024-01-01",
    dateEnd="2024-12-31",
    limit=20,
    orderBy="relevance"       # relevance/date_desc/date_asc
)
```

**返回值**：
```json
{
  "query": "authentication bug",
  "count": 5,
  "results": [
    {
      "id": 123,
      "type": "bugfix",
      "item_type": "observation",
      "title": "Fixed JWT validation",
      "concepts": ["auth", "jwt"],
      "score": 0.92
    }
  ],
  "hint": "Use TimelineMemory(anchor=id) or GetObservations(ids=[...]) for full details"
}
```

### 2. TimelineMemory - 时间线上下文

```python
# 使用 ID 锚点
TimelineMemory(anchor="123", depth_before=3, depth_after=3)

# 使用 Summary 锚点
TimelineMemory(anchor="S456", depth_before=5, depth_after=2)

# 使用查询自动查找锚点
TimelineMemory(query="last bug fix", depth_before=3, depth_after=3)

# 使用 Prompt ID
TimelineMemory(anchor="P789")
```

**锚点格式**：
- Observation ID: `"123"`
- Summary ID: `"S456"` 或 `"#S456"`
- Prompt ID: `"P789"` 或 `"#P789"`
- 时间戳: `"2024-01-15T10:30:00"`

**返回值**：
```json
{
  "anchor": "123",
  "anchor_type": "observation",
  "anchor_title": "Fixed JWT validation",
  "timeline": [
    {"item_type": "summary", "id": "S10", "request": "Fix auth bug", ...},
    {"item_type": "observation", "id": 123, "title": "Fixed JWT validation", ...},
    {"item_type": "prompt", "id": "P42", "prompt_text": "Fix the auth issue", ...}
  ]
}
```

### 3. GetObservations - 获取详情

```python
# 批量获取（推荐）
GetObservations(ids=[123, 456, 789])

# 带过滤
GetObservations(ids=[123, 456], orderBy="date_desc", limit=5)
```

**最佳实践**：
- 总是批量获取（2+ items）以减少工具调用次数
- 先使用 SearchMemory 获取 ID，再用 GetObservations 获取详情

### 4. SaveMemory - 手动保存记忆

```python
# 基本用法
SaveMemory(text="发现生产环境数据库连接池配置过小")

# 带标题和标签
SaveMemory(
    text="发现生产环境数据库连接池配置过小，建议调整为100",
    title="数据库连接池配置问题",
    concepts=["database", "performance", "production"]
)
```

**使用场景**：
- 发现重要信息需要记住
- 做出关键决策需要记录
- 找到解决方案可能复用
- 需要为将来会话添加上下文

## 三层检索工作流

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         3-Layer Memory Retrieval                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   Step 1: SearchMemory                                                          │
│   ┌──────────────────────────────────────────────────────────────────────┐    │
│   │  query: "authentication bug"                                          │    │
│   │  → 返回紧凑索引，含 IDs（~50-100 tokens/result）                        │    │
│   │  → {id: 123, type: "bugfix", title: "...", score: 0.92}               │    │
│   └──────────────────────────────────────────────────────────────────────┘    │
│                                      ↓                                          │
│   Step 2: TimelineMemory                                                        │
│   ┌──────────────────────────────────────────────────────────────────────┐    │
│   │  anchor: "123"  (选择感兴趣的 ID)                                      │    │
│   │  → 获取时间线上下文（observations + summaries + prompts）              │    │
│   │  → 按时间排序，了解当时发生了什么                                       │    │
│   └──────────────────────────────────────────────────────────────────────┘    │
│                                      ↓                                          │
│   Step 3: GetObservations                                                       │
│   ┌──────────────────────────────────────────────────────────────────────┐    │
│   │  ids: [123, 456]  (批量获取完整详情)                                   │    │
│   │  → 返回完整信息（~500-1000 tokens/result）                             │    │
│   │  → 包含 narrative, facts, concepts, files_modified 等                  │    │
│   └──────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│   💡 优势：通过先过滤再获取详情，节省约 10x tokens                              │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 配置

在 `~/.kimi/config.toml` 中添加：

```toml
[memory]
enabled = true                    # 启用记忆系统
provider = "glm"                  # 嵌入模型: kimi, glm, qwen, openai
model = "embedding-3"             # 具体模型名 (null 使用默认)
db_path = ""                      # 自定义数据库路径 (空字符串使用默认 ~/.kimi/memory.db)
max_observations_in_context = 5   # 上下文中最多包含的 observations
max_summaries_in_context = 3      # 上下文中最多包含的 summaries
```

### 嵌入模型选择

| 提供商 | 模型 | 维度 | 特点 |
|--------|------|------|------|
| Kimi | text-embedding-v2 | 1024 | 推荐，中文效果好 |
| GLM | embedding-2 | 1024 | 智谱 AI，性价比高 |
| GLM | embedding-3 | 2048 | 智谱 AI，更高维度 |
| Qwen | text-embedding-v3 | 1024 | 阿里通义 |
| OpenAI | text-embedding-3-small | 1536 | 便宜 |
| OpenAI | text-embedding-3-large | 3072 | 准确 |

**默认使用 Kimi**，因为它：
1. 复用主 agent 的 LLM 客户端（无需额外配置）
2. 中文 embedding 效果好
3. 成本可控

#### GLM (智谱 AI) 配置

使用 GLM Embedding API 需要：

1. **获取 API Key**: 访问 https://bigmodel.cn/usercenter/apikeys 创建 API Key

2. **设置环境变量**:
   ```bash
   export ZHIPU_API_KEY=your_api_key_here
   ```

3. **配置 config.toml**:
   ```toml
   [memory]
   enabled = true
   provider = "glm"
   model = "embedding-2"  # 或 "embedding-3"
   ```

4. **验证安装**:
   ```bash
   python tests/test_glm_embedding.py
   ```

**GLM 特点**:
- 中文语义理解优秀
- 支持批量嵌入（自动优化）
- 价格相对便宜
- embedding-2: 1024维，适合大多数场景
- embedding-3: 2048维，需要更高精度时使用

更多提供商详情参见：[Embedding Providers 对比](embedding_providers.md)

## 与现有代码集成

### 系统提示模板

记忆上下文通过 `${KIMI_MEMORY_CONTEXT}` 变量注入系统提示：

```markdown
## Memory Timeline

### 2024-01-15 10:30 (Prompt #5)
**Request:** Fix login bug
**Completed:** Fixed token validation in auth.py
**Next Steps:** Write tests

- **[BUGFIX]** Fixed JWT validation error
  > Added null check for token payload
  • Fixed edge case when token is expired
  *Concepts:* auth, jwt, validation
  *Files:* src/auth.py
```

### 会话生命周期

```
Session Start
    ↓
Runtime.create() → MemoryAgent.start()
    ↓
on_session_start() → 加载历史上下文
    ↓
User Input
    ↓
run() → queue_prompt(wait=False) 【异步】
    ↓
_turn() → _agent_loop() → _step()
    ↓
_grow_context() → queue_observation(wait=False) 【每个工具调用后】
    ↓
Turn End
    ↓
_generate_and_save_summary() → queue_summary(wait=True) 【同步等待】
    ↓
Session End (可选)
    ↓
on_session_end() → 确保所有数据写入
```

## 使用示例

### 手动搜索记忆

```python
from kimi_cli.memory import MemoryAgent, SearchFilters, ObservationType

agent = MemoryAgent.create()
await agent.start()

# 搜索所有关于认证的 bugfix
results = await agent.search(
    query="authentication token bug",
    filters=SearchFilters(
        types=[ObservationType.BUGFIX],
        concepts=["auth", "token"]
    ),
    top_k=10
)

for result in results["observations"]:
    print(f"[{result.observation.type.value}] {result.observation.title}")
    print(f"  Score: {result.score}")
```

### 获取会话时间线

```python
# 获取当前会话的完整历史
summaries = agent.get_timeline("session-001")
for s in summaries:
    print(f"Prompt {s.prompt_number}: {s.request}")
    print(f"  Completed: {s.completed}")
    print(f"  Next: {s.next_steps}")
```

### 查找相似观察

```python
# 查找与某个 observation 相似的其他观察
similar = await agent.search_similar(observation_id=123, top_k=5)
for result in similar:
    print(f"- {result.observation.title} (score: {result.score:.2f})")
```

### 按项目查找

```python
# 查找特定项目的所有记忆
obs_list = agent.find_by_project("/path/to/project", limit=50)

# 按概念查找
obs_list = agent.find_by_concept("docker", limit=20)

# 按文件查找
obs_list, summaries = agent.find_by_file("src/auth.py")
```

## 数据库 Schema

### Observations 表

```sql
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT,              -- 工作目录路径
    type TEXT NOT NULL,        -- bugfix/feature/refactor/change/discovery/decision
    title TEXT NOT NULL,
    subtitle TEXT,
    facts TEXT,                -- JSON array
    narrative TEXT,
    concepts TEXT,             -- JSON array
    files_read TEXT,           -- JSON array
    files_modified TEXT,       -- JSON array
    tool_name TEXT,
    prompt_number INTEGER,
    created_at_epoch INTEGER,
    embedding BLOB             -- numpy float32 array
);
```

### Session Summaries 表

```sql
CREATE TABLE session_summaries (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT,              -- 工作目录路径
    request TEXT,
    investigated TEXT,
    learned TEXT,
    completed TEXT,
    next_steps TEXT,
    notes TEXT,
    prompt_number INTEGER,
    created_at_epoch INTEGER,
    embedding BLOB
);
```

### User Prompts 表

```sql
CREATE TABLE user_prompts (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT,
    prompt_number INTEGER,
    prompt_text TEXT,
    created_at_epoch INTEGER
);
```

## 性能考虑

1. **异步队列**: 写入操作通过异步队列处理，不阻塞主流程
2. **WAL 模式**: SQLite 使用 WAL 模式，读写不冲突
3. **内存缓存**: 嵌入向量有简单内存缓存
4. **FTS5 索引**: 全文搜索使用 FTS5 虚拟表
5. **批量处理**: 支持批量获取和搜索
6. **队列上限**: 队列大小限制 1000，防止内存无限增长

## 最佳实践

### 1. 使用三层检索节省 Tokens

```python
# ❌ 不推荐：直接获取所有详情
results = GetObservations(ids=range(1, 100))  # 太多不必要的数据

# ✅ 推荐：先搜索过滤
search_results = SearchMemory(query="database issue", limit=10)
interesting_ids = [r["id"] for r in search_results["results"][:3]]

# 获取时间线上下文
timeline = TimelineMemory(anchor=str(interesting_ids[0]))

# 只获取感兴趣的详情
details = GetObservations(ids=interesting_ids)
```

### 2. 手动保存重要发现

```python
# 当发现重要信息时主动保存
SaveMemory(
    text="发现 API 响应时间超过 2s，原因是缺少数据库索引",
    concepts=["performance", "database", "api"]
)
```

### 3. 使用项目过滤

```python
# 搜索时限制在当前项目
SearchMemory(
    query="authentication",
    project="/path/to/current/project"
)
```

### 4. 理解阻塞行为

```
Observation 保存: 异步，无感知延迟
Summary 保存: 同步，在 Turn 结束时等待（默认30s超时）
Memory Tools: 同步，与普通工具一致
```

## 故障排除

### 记忆系统未启动

检查日志：
```bash
kimi --verbose 2>&1 | grep -i memory
```

常见问题：
- API Key 未设置：设置 `KIMI_API_KEY` 环境变量
- 数据库权限：检查 `~/.kimi/memory.db` 写入权限

### 上下文未注入

检查系统提示模板是否包含 `${KIMI_MEMORY_CONTEXT}`：

```bash
grep KIMI_MEMORY_CONTEXT ~/.kimi/agents/default.md
```

### 搜索无结果

- 确认 memory 已启用：`[memory]` 配置节存在且 `enabled = true`
- 检查是否有历史数据：查看 `~/.kimi/memory.db` 文件大小
- 尝试不同的查询词：使用更通用的关键词
- 检查 project 过滤：可能需要移除 project 限制

### 队列满警告

如果出现 `Memory queue full, observation dropped`：
- 检查网络连接（嵌入 API 是否可达）
- 增加队列大小（修改 `max_queue_size`）
- 检查 Worker 线程是否卡住

## 未来扩展

1. **概念图谱**: 基于 concepts 构建知识图谱
2. **自动标签**: 使用 LLM 自动提取更丰富的概念标签
3. **跨项目记忆**: 支持在多个项目间共享相关记忆
4. **记忆压缩**: 对旧记忆进行 LLM 压缩摘要
5. **可视化**: 提供记忆浏览和搜索的 Web UI
6. **智能提醒**: 根据上下文自动提醒相关历史记忆
