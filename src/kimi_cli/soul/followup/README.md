# Steering & Follow-up Messages 机制

本模块实现了 Kimi Code CLI 的 Steering & Follow-up Messages 机制，用于处理人机交互中的消息排队和后台子 Agent 任务结果的路由。

## 设计原则

### 人机交互层

- **统一 Follow-up 模型**: 用户普通消息统一作为 Follow-up Message 排队，等待当前 Agent 执行完成后处理
- **显式打断**: 用户想打断当前操作时使用 `/stop` 等 Slash 命令

### Agent 间通信层

- **后台任务结果**: 子 Agent 完成后通过 Message Bus 发送结果给主 Agent
- **自动路由**: 主 Agent 根据状态（空闲/忙碌）决定立即处理或排队等待

## 核心组件

### 1. AgentMessageBus (消息总线)

极简进程内消息总线，用于子 Agent 向主 Agent 投递任务结果。

```python
from kimi_cli.soul.followup import message_bus, BackgroundTaskResultMessage

# 主 Agent 订阅消息
def on_task_complete(message: BackgroundTaskResultMessage):
    print(f"Task {message.task_id} completed with status: {message.status}")

message_bus.subscribe(session_id, on_task_complete)

# 子 Agent 发布消息
await message_bus.publish(session_id, result_message)
```

**特点**:
- 无持久化，进程内通信
- 主 Agent 离线则消息丢弃
- 按 session_id 隔离

### 2. MessageQueue (消息队列)

统一的消息队列，按时间顺序排队处理用户消息和后台任务结果。

```python
from kimi_cli.soul.followup import MessageQueue

queue = MessageQueue()

# 添加用户消息
await queue.put_user_message("Hello", source="feishu")

# 添加任务结果
await queue.put_task_result(task_result_message)

# 处理 follow-up 消息
for item in queue.get_followup_messages():
    await queue.inject_to_context(item, context)

# 处理 steering 消息
for item in queue.get_steering_messages():
    steering_msg = queue.create_steering_message(item)
    await context.append_message(steering_msg)
```

**特点**:
- FIFO 顺序处理
- 不设超时，永久保留
- 任务结果包装为 system-level 提示

### 3. TaskManager (任务管理器)

全局单例，按 session 隔离管理后台子 Agent 任务。

```python
from kimi_cli.soul.followup import TaskManager, SubagentTask

task_manager = TaskManager()

# 注册 session
task_manager.register_session(session_id)

# 添加任务
task = SubagentTask(...)
await task.run_in_background()
task_manager.add_task(session_id, task)

# 查询任务
tasks = task_manager.list_tasks(session_id, status="running")
task = task_manager.get_task(session_id, task_id)

# 停止任务
task.request_stop()

# Session 结束时清理
await task_manager.shutdown_session(session_id)
```

**特点**:
- 单例模式
- 按 session 隔离
- Session 结束时自动清理后台任务

### 4. 工具扩展

#### Task 工具

支持后台执行模式：

```python
# 同步执行（默认，向后兼容）
Task(description="分析代码", subagent_name="coder", prompt="...")

# 后台执行（新增）
Task(
    description="分析代码",
    subagent_name="coder",
    prompt="...",
    run_in_background=True
)
# 返回: {"task_id": "task-xxx", "status": "running"}
```

#### 任务管理工具

- **TaskList**: 查询当前 session 的所有任务
- **TaskOutput**: 查询指定任务的输出
- **TaskStop**: 停止指定的后台任务

## 与 KimiSoul 的集成

KimiSoul 集成了 MessageQueue 和状态管理：

```python
class KimiSoul:
    def __init__(self, ...):
        self._message_queue = MessageQueue()
        self._state = AgentState.IDLE
        self._register_message_bus()

    async def run(self, user_input):
        self._state = AgentState.RUNNING
        try:
            if user_input is not None:
                await self._run_single_turn(user_input)
            elif self.has_pending_messages():
                await self._agent_loop()

            # 检查是否有新到达的 steering 消息
            if not self._message_queue.steering_queue_empty():
                await self._agent_loop()
        finally:
            self._state = AgentState.IDLE
```

实际实现中，队列消费主要发生在 `_agent_loop()` 内：
- turn 结束后批量读取 `Follow-up Queue` 并注入 context
- step / turn 边界读取 `Steering Queue` 并继续 agent loop
- `run()` 只负责入口分流和收尾竞态处理

## Session 生命周期

```
Session 开始
    │
    ├── TaskManager.register_session(session_id)
    ├── MessageBus.subscribe(session_id, callback)
    │
    └── 用户启动后台任务
            │
            ├── TaskManager.add_task(session_id, task)
            └── 子 Agent 开始运行
                    │
                    ├── 正常运行 → MessageBus.publish() → 主 Agent 处理
                    │
                    └── Session 结束（exit/new/sessions/clear）
                            │
                            └── TaskManager.shutdown_session()
                                    ├── 发送 stop 信号给子 Agent
                                    ├── 等待/强制取消
                                    └── MessageBus.unsubscribe(session_id)
```

## 配置

在 `agent.yaml` 中启用任务管理工具：

```yaml
tools:
  - "kimi_cli.tools.multiagent:Task"           # 启动子 Agent
  - "kimi_cli.tools.multiagent:TaskList"      # 查询任务列表
  - "kimi_cli.tools.multiagent:TaskOutput"    # 查询任务输出
  - "kimi_cli.tools.multiagent:TaskStop"      # 停止后台任务
```

## 测试

运行所有测试：

```bash
pytest tests/test_followup/ -v
```

运行特定测试：

```bash
pytest tests/test_followup/test_message_bus.py -v
pytest tests/test_followup/test_message_queue.py -v
pytest tests/test_followup/test_task_manager.py -v
pytest tests/test_followup/test_task_tool.py -v
```

## 注意事项

1. **Python 版本**: 需要 Python 3.12+（使用 `type X = Y` 语法）
2. **KimiSoul 集成**: KimiSoul 修改使用了 Python 3.12+ 语法，在低版本 Python 中会报语法错误
3. **单例模式**: TaskManager 和 message_bus 是单例，注意在测试时重置状态
4. **消息持久化**: 消息总线无持久化，主 Agent 离线则消息丢弃
