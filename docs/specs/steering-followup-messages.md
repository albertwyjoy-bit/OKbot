# Steering & Follow-up Messages 机制设计规范

> **状态**: Ready for Review  
> **日期**: 2026-03-03  
> **相关**: Multi-Agent Level 2, Feishu Integration  
> **版本**: 1.0

---

## 1. 设计原则

### 1.1 核心决策

**决策一：人机对话中统一使用 Follow-up Message**

参考 pi-mono 的设计，虽然区分了 `steer()` 和 `followUp()` 两种机制，但在 **OKbot 的 Feishu 场景** 中，我们做出以下简化：

| 场景 | pi-mono 设计 | OKbot 设计 | 理由 |
|------|-------------|-----------|------|
| 用户发送消息 | Enter=Steer, Alt+Enter=FollowUp | 统一视为 FollowUp | Feishu 无快捷键机制，简化用户认知 |
| 用户打断 | 通过 Steer 机制 | 通过 `/stop` 等 Slash 命令 | 显式命令更符合 Feishu 交互习惯 |

**决策二：Follow-up Message 作为 Agent 间通信的基础机制**

后台子 Agent（Level 2 异步任务）完成后，通过消息机制与主 Agent 通信，该消息根据主 Agent 状态自动路由为：
- **User Message**: 主 Agent 空闲时
- **Follow-up Message**: 主 Agent 正在执行时

---

## 2. 机制详解

### 2.1 人机交互层

```
用户发送消息
    │
    ├─ 如果是 /slash 命令（如 /stop, /clear）
    │      → 立即执行，可能打断当前操作
    │
    └─ 如果是普通消息
           → 统一作为 Follow-up Message 排队
           → 等待当前 Agent 执行完成后处理
```

**Why?** 用户不需要理解 "Steering" vs "Follow-up" 的概念，只需：
- 正常对话 → 自动排队
- 想打断 → 使用显式命令 `/stop`

### 2.2 Agent 间通信层

```
┌─────────────────────────────────────────────────────────────┐
│                      主 Agent (Main)                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Message Router                                     │   │
│  │  - 检测主 Agent 状态（是否在 loop 中）               │   │
│  │  - 决定消息投递方式                                 │   │
│  └──────────┬────────────────────────────┬─────────────┘   │
│             │                            │                 │
│     空闲（无 loop）               忙碌（在 loop 中）         │
│             │                            │                 │
│             ▼                            ▼                 │
│      作为 User Message           作为 Follow-up Message    │
│      立即触发新 turn             排队等待当前 turn 结束    │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │
┌─────────────────────────────┴─────────────────────────────┐
│                   后台子 Agent (Background)                │
│                                                            │
│  Task 工具执行完成                                          │
│       │                                                    │
│       ▼                                                    │
│  生成结果消息（包含 task_id, output, status）               │
│       │                                                    │
│       ▼                                                    │
│  通过 Message Bus 发送给主 Agent                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 消息格式

### 3.1 后台子 Agent → 主 Agent 的消息格式

**关键设计：Task Result 作为 System-Level 提示**

参考 `/plan` 模式中的 `ExitPlanMode` 工具实现，Task Result 不应该作为 `user` message，而是作为 **system-level 提示** 插入到对话上下文中。这样 Agent 可以感知到后台任务完成，但不会将其误解为用户的意图。

```typescript
interface BackgroundTaskResultMessage {
  // 消息类型标识
  type: "background_task_result";
  
  // 任务元信息
  task_id: string;
  task_description: string;
  subagent_name: string;
  
  // 执行结果
  status: "completed" | "failed" | "stopped";
  output: string;           // 任务输出摘要（展示给用户）
  output_file?: string;     // 完整日志文件路径
  error_message?: string;   // 失败原因（如果 status != completed）
  
  // 执行统计
  started_at: string;       // ISO timestamp
  completed_at: string;     // ISO timestamp
  token_usage?: number;     // Token 使用量
}
```

**渲染为 System Message 的格式：**

```python
from kimi_cli.soul.message import system

# 构建 system-level 提示内容
system_content = f"""Background task completed:
- Task ID: {result.task_id}
- Description: {result.task_description}
- Status: {result.status}
- Result: {result.output}
- Log file: {result.output_file}
"""

# 作为 system-level 提示插入（不是 user message）
message = Message(
    role="user",  # 包装在 user message 中，但内容是 system 标签
    content=[
        system(system_content),  # 生成 <system>...</system> 格式的提示
        # 可选：附加指令
        TextPart(text="Please review the background task result above and incorporate it into your response if relevant.")
    ]
)
```

**为什么这样设计？**

1. **区分用户意图和系统通知**：Task Result 是系统通知，不是用户的指令
2. **Agent 行为可控**：Agent 知道如何处理 system 提示（参考 system.md 第17行说明）
3. **Context 清晰**：不会混淆对话历史中的 user/assistant 交替模式

### 3.2 主 Agent 中的消息队列

```python
from kimi_cli.soul.message import system

class MessageQueue:
    """
    统一的消息队列，按时间顺序排队。
    消息处理时不区分来源，统一视为 "待注入的上下文"
    """
    
    def __init__(self):
        # 统一队列，按时间顺序
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    
    async def put_user_message(self, content: str, source: str = "feishu"):
        """用户发送的消息 → 作为正常 user message"""
        await self._queue.put(QueueItem(
            type="user",
            content=content,
            timestamp=time.time()
        ))
    
    async def put_task_result(self, result: BackgroundTaskResultMessage):
        """后台子 Agent 返回的结果 → 包装为 system-level 提示"""
        # 构建 system-level 提示内容
        system_content = self._format_task_result(result)
        
        await self._queue.put(QueueItem(
            type="system_notification",
            content=system_content,  # 预格式化的 system 内容
            timestamp=time.time(),
            metadata={"task_id": result.task_id}
        ))
    
    def _format_task_result(self, result: BackgroundTaskResultMessage) -> str:
        """将 Task Result 格式化为 system-level 提示"""
        return f"""Background task completed:
- Task ID: {result.task_id}
- Description: {result.task_description}
- Status: {result.status}
- Result: {result.output[:500]}  # 截断显示
- Log file: {result.output_file}
"""
    
    async def inject_to_context(self, item: QueueItem, context: Context):
        """将队列中的消息注入到对话上下文"""
        if item.type == "user":
            # 用户消息：正常添加
            await context.append_message(Message(
                role="user",
                content=[TextPart(text=item.content)]
            ))
        
        elif item.type == "system_notification":
            # Task Result：包装为 system-level 提示
            await context.append_message(Message(
                role="user",  # 使用 user role 包装，但内容是 system 标签
                content=[
                    system(item.content),
                    TextPart(text="Please review the background task result above and respond to the user.")
                ]
            ))
```

---

## 4. 状态机与消息路由

### 4.1 主 Agent 状态

```
┌──────────────┐     prompt()      ┌──────────────┐
│   IDLE       │ ────────────────> │  RUNNING     │
│  (空闲)      │                   │  (执行中)    │
└──────────────┘                   └──────┬───────┘
     ▲                                    │
     │         turn_end + no followup     │
     └────────────────────────────────────┘
                                          │
                                          │ turn_end + has followup
                                          ▼
                                   ┌──────────────┐
                                   │  PENDING     │
                                   │  (有排队消息) │
                                   └──────┬───────┘
                                          │
                                          │ continue()
                                          ▼
                                   ┌──────────────┐
                                   │  RUNNING     │
                                   │  (处理排队)   │
                                   └──────────────┘
```

### 4.2 消息路由逻辑

```python
class KimiSoul:
    async def handle_incoming_message(self, message: Union[UserMessage, TaskResultMessage]):
        """
        处理收到的消息（来自用户或后台 Agent）
        """
        if self._state == AgentState.IDLE:
            # 主 Agent 空闲，立即作为 User Message 处理
            await self._do_run(message)
            
        elif self._state == AgentState.RUNNING:
            # 主 Agent 正在执行，作为 Follow-up Message 排队
            self._pending_messages.append(message)
            # 通知用户消息已排队
            await self._notify_message_queued(message)
    
    async def _agent_loop(self):
        """
        主循环：处理完当前 turn 后检查并处理排队消息
        """
        while True:
            # 执行当前 turn
            turn_result = await self._run_turn()
            
            # Turn 结束，检查是否有排队消息
            if self._pending_messages:
                # 将排队消息转为 User Message 继续处理
                next_message = self._pending_messages.pop(0)
                await self._context.append_message(next_message)
                continue  # 继续循环，不退出
            
            # 没有排队消息，进入 IDLE 状态
            self._state = AgentState.IDLE
            break
```

---

## 5. 与 Level 2 Multi-Agent 的集成

### 5.1 后台任务完成后的消息流程

```
1. 用户启动后台任务
   用户: "分析代码库（后台运行）"
   → Task(description="分析代码库", run_in_background=True)
   ← 返回: {task_id: "task-001", status: "running"}

2. 主 Agent 继续执行其他任务或进入 IDLE
   Kimi: "任务已启动，ID: task-001。您可以继续其他操作。"

3. 后台子 Agent 执行完成
   ┌─────────────────────────────────────────┐
   │  子 Agent 生成结果消息                  │
   │  {                                      │
   │    type: "background_task_result",      │
   │    task_id: "task-001",                 │
   │    status: "completed",                 │
   │    output: "代码分析完成...",            │
   │    output_file: "/tmp/task-001.log"     │
   │  }                                      │
   └─────────────────────────────────────────┘
                    │
                    ▼
   通过 Message Bus 发送给主 Agent

4. 主 Agent 接收消息
   
   **Case A: 主 Agent 在 IDLE**
   ```
   → 立即构建 system-level 提示
   → Message(role="user", content=[system("Background task completed: ...")])
   → 触发新 turn
   → Kimi: "任务 task-001 已完成。根据分析结果，代码库中有3个问题需要修复..."
   ```
   
   **Case B: 主 Agent 在 RUNNING**
   ```
   → Task Result 进入 Follow-up Queue
   → 通知用户: "📝 后台任务 task-001 完成，将在当前操作后展示结果"
   → 当前 turn 结束后，将 system-level 提示注入 context
   → 继续执行，让模型自然响应
   ```
   
   **关键：Task Result 始终作为 system-level 提示，不是 user message**
```

### 5.2 Message Bus 实现

**设计决策：无持久化**

理由：
- 主 Agent 离线时（exit/切换 session/clear），子 Agent 会被 TaskManager 强制停止，不会执行 publish()
- 竞态条件（子 Agent 刚完成时主 Agent 退出）极少发生，且消息可能来不及处理
- 子 Agent 日志已保存到文件，用户可通过文件找回结果

```python
class AgentMessageBus:
    """
    极简进程内消息总线 - 无持久化
    
    职责：子 Agent 完成任务后，将结果投递给主 Agent
    特点：主 Agent 离线则消息丢弃（因为子 Agent 应该已被清理）
    """
    
    def __init__(self):
        # session_id -> callback 映射
        self._callbacks: Dict[str, Callable] = {}
    
    def subscribe(self, session_id: str, callback: Callable):
        """主 Agent 启动时订阅消息"""
        self._callbacks[session_id] = callback
    
    def unsubscribe(self, session_id: str):
        """Session 结束时取消订阅"""
        self._callbacks.pop(session_id, None)
    
    async def publish(self, session_id: str, message: BackgroundTaskResultMessage):
        """后台子 Agent 发布消息"""
        if callback := self._callbacks.get(session_id):
            await callback(message)
        # else: 主 Agent 已离线，消息自然丢弃
        # （子 Agent 应该已被 TaskManager 清理，不会走到这里）
```

### 5.3 任务管理工具

后台任务启动后，用户需要能够查询进度和停止任务。提供两个工具：

#### TaskOutput - 查询任务输出

```python
class TaskOutput:
    """查询后台任务的当前输出"""
    
    async def run(self, task_id: str, follow: bool = False) -> str:
        """
        Args:
            task_id: 任务 ID
            follow: 是否持续跟随输出（类似 tail -f）
        
        Returns:
            任务当前输出内容
        """
        session_id = self.runtime.session.id
        
        # 从 TaskManager 获取任务
        task = TaskManager().get_task(session_id, task_id)
        
        if not task:
            return f"任务 {task_id} 不存在或已完成"
        
        # 读取日志文件
        if task.output_file.exists():
            output = task.output_file.read_text()
            
            # 截断显示（避免 context 过长）
            max_len = 2000
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (共 {len(output)} 字符，完整内容在文件)"
            
            return f"任务 {task_id} 状态: {task.status.value}\n\n输出:\n{output}"
        
        return f"任务 {task_id} 状态: {task.status.value}\n暂无输出"
```

**使用场景**：
```
用户: 查看任务 task-001 的输出
Kimi: 任务 task-001 状态: running

输出:
正在分析 src/main.py...
发现 3 个函数，正在生成文档...
...
```

#### TaskStop - 停止后台任务

```python
class TaskStop:
    """停止正在运行的后台任务"""
    
    async def run(self, task_id: str) -> str:
        """
        Args:
            task_id: 要停止的任务 ID
        
        Returns:
            停止结果
        """
        session_id = self.runtime.session.id
        
        # 从 TaskManager 获取任务
        task = TaskManager().get_task(session_id, task_id)
        
        if not task:
            return f"任务 {task_id} 不存在或已结束"
        
        if task.status != TaskStatus.RUNNING:
            return f"任务 {task_id} 状态为 {task.status.value}，无需停止"
        
        # 发送停止信号
        task.request_stop()
        
        # 等待任务结束（短超时）
        try:
            await asyncio.wait_for(task.wait(), timeout=5.0)
            return f"任务 {task_id} 已停止"
        except asyncio.TimeoutError:
            # 强制取消
            if task._task and not task._task.done():
                task._task.cancel()
            return f"任务 {task_id} 已强制停止"
```

**使用场景**：
```
用户: 停止任务 task-001
Kimi: 任务 task-001 已停止

用户: 停止所有后台任务
Kimi: 已停止 3 个后台任务: task-001, task-002, task-003
```

#### TaskList - 任务列表

```python
class TaskList:
    """获取当前 session 的所有任务列表"""
    
    async def run(self, status: Optional[str] = None) -> str:
        """
        Args:
            status: 过滤状态 (running/completed/failed/stopped)，默认为全部
        
        Returns:
            任务列表表格
        """
        session_id = self.runtime.session.id
        tasks = TaskManager().list_tasks(session_id)
        
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        
        if not tasks:
            return "当前没有后台任务"
        
        lines = ["任务ID | 描述 | 状态 | 开始时间", "-" * 50]
        for task in tasks:
            lines.append(
                f"{task.task_id} | {task.description[:20]}... | "
                f"{task.status.value} | {task.started_at.strftime('%H:%M:%S')}"
            )
        
        return "\n".join(lines)
```

**使用场景 1 - 用户主动查询**：
```
用户: /tasks
Kimi: 任务ID | 描述 | 状态 | 开始时间
      --------------------------------------------------
      task-001 | 分析代码库... | running | 14:32:10
      task-002 | 搜索文档... | completed | 14:30:05
```

**使用场景 2 - 主 Agent 主动查看**：
```
用户: 现在后台有什么任务在跑？
Kimi: [调用 TaskList 工具]
      当前有 2 个后台任务：
      - task-001: 分析代码库 (running)
      - task-002: 搜索文档 (completed)

用户: 停止那个还在跑的
Kimi: [调用 TaskStop 工具停止 task-001]
      已停止任务 task-001
```

---

## 6. Feishu 集成细节

### 6.1 用户消息处理

```python
class FeishuMessageHandler:
    async def handle_user_message(self, chat_id: str, user_id: str, text: str):
        session = self._get_session(chat_id, user_id)
        
        # 检查是否是打断命令
        if text.strip() == "/stop":
            await session.abort()
            await self._send_message(chat_id, "✅ 已停止当前操作")
            return
        
        # 普通消息统一作为 Follow-up
        await session.message_queue.put_user_message(text, source="feishu")
        
        if session.is_running:
            await self._send_message(chat_id, "📝 已收到消息，将在当前操作后处理")
        # 否则消息会被立即处理
```

### 6.2 后台任务完成通知

```python
async def on_background_task_complete(self, session_id: str, result: BackgroundTaskResultMessage):
    """后台任务完成回调"""
    chat_id = self._get_chat_id_by_session(session_id)
    
    # 构造消息卡片
    card = TaskCompleteCard(
        task_id=result.task_id,
        description=result.task_description,
        status=result.status,
        summary=result.output[:500],  # 摘要
        full_output_url=f"https://.../tasks/{result.task_id}"  # 完整结果链接
    )
    
    await self._send_card(chat_id, card)
```

---

## 7. 已决策事项

### 7.1 消息排序策略 ✅

**决策：严格按时间顺序排布**

```
所有消息（User Messages + Task Results）进入统一队列
按到达时间排序，先进先出
不区分优先级，保持简单可预测
```

### 7.2 消息格式 ✅

**决策：Task Result 作为 System-Level 提示**

参考 `/plan` 模式，Task Result 不直接作为 user message，而是包装在 `<system>` 标签中注入 context。

### 7.3 主 Agent 退出场景与后台任务清理

#### 7.3.1 主 Agent 退出场景枚举

| 场景 | 触发方式 | 当前行为 | 是否需要清理后台任务 |
|------|---------|---------|-------------------|
| **1. CLI 正常退出** | `exit`, `quit`, `/exit`, `/quit`, `Ctrl+D` | 退出程序 | ✅ 需要 |
| **2. 新建 Session** | `/new` | 创建新 session，当前 session 结束 | ✅ 需要 |
| **3. 切换 Session** | `/sessions` + 选择其他 session | 切换到其他 session | ✅ 需要 |
| **4. 清空 Context** | `/clear` (aliases: `/reset`) | 清空当前 context，session 继续 | ✅ **需要** - 主 Agent "失忆" |
| **5. 压缩 Context** | `/compact` | 压缩历史消息 | ❌ 不需要 |
| **6. 中断当前操作** | `Ctrl+C`, `/stop` | 中断当前 turn | ❌ 不需要（session 继续） |
| **7. 程序异常退出** | 崩溃、kill 信号 | 异常终止 | ✅ 需要（尽力清理） |

#### 7.3.2 后台任务清理策略

**核心原则**：

| 条件 | 行为 | 理由 |
|------|------|------|
| **Session 结束** | 清理所有后台任务 | Session 结束，无人接收结果 |
| **Context 清空 (`/clear`)** | **清理所有后台任务** | 主 Agent "失忆"，无法理解和处理任务结果 |
| **Context 压缩 (`/compact`)** | 后台任务继续运行 | Context 保留任务记录，主 Agent 记得任务 |
| **Turn 中断 (`/stop`)** | 后台任务继续运行 | Context 保留，只是中断当前 turn |

**实现方案：Shutdown Hook 机制**

```python
class TaskManager:
    """全局任务管理器（按 session 隔离）"""
    
    def __init__(self):
        self._tasks_by_session: Dict[str, List[SubagentTask]] = {}
    
    def register_session(self, session_id: str):
        """Session 启动时注册"""
        self._tasks_by_session[session_id] = []
    
    def add_task(self, session_id: str, task: SubagentTask):
        """添加后台任务到指定 session"""
        if session_id in self._tasks_by_session:
            self._tasks_by_session[session_id].append(task)
    
    def get_task(self, session_id: str, task_id: str) -> Optional[SubagentTask]:
        """获取指定任务（用于 TaskOutput/TaskStop 工具）"""
        tasks = self._tasks_by_session.get(session_id, [])
        for task in tasks:
            if task.task_id == task_id:
                return task
        return None
    
    def list_tasks(self, session_id: str) -> List[SubagentTask]:
        """获取 session 的所有任务（用于 /tasks 命令）"""
        return self._tasks_by_session.get(session_id, [])
    
    async def shutdown_session(self, session_id: str, timeout: float = 30.0):
        """
        Session 结束时调用，清理该 session 的所有后台任务
        由以下场景触发：
        - /new (新建 session)
        - /sessions 切换
        - exit/quit/Ctrl+D
        - /clear (context 清空)
        """
        tasks = self._tasks_by_session.pop(session_id, [])
        if not tasks:
            return
        
        logger.info(f"Shutting down {len(tasks)} background tasks for session {session_id}")
        
        # 1. 发送优雅停止信号
        for task in tasks:
            if task._soul:
                task._soul.request_stop()
        
        # 2. 等待完成（带超时）
        try:
            await asyncio.wait_for(
                self._wait_tasks_complete(tasks),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            # 3. 超时后强制取消
            logger.warning(f"Shutdown timeout for session {session_id}, force cancelling")
            for task in tasks:
                if task._task and not task._task.done():
                    task._task.cancel()
        
        # 注意：无持久化，子 Agent 日志已保存到文件
```

#### 7.3.3 集成到 CLI 生命周期

```python
# ui/shell/__init__.py - Shell.run()
async def run(self):
    try:
        while True:
            user_input = await self.get_user_input()
            await self.session.prompt(user_input)
    except (EOFError, KeyboardInterrupt):
        # Ctrl+D 或程序退出
        await self._cleanup()
    finally:
        await self._cleanup()

async def _cleanup(self):
    """清理当前 session 的后台任务"""
    if self.soul:
        session_id = self.soul.runtime.session.id
        await TaskManager().shutdown_session(session_id)

# ui/shell/slash.py - /new, /sessions
@registry.command
def exit(app: Shell, args: str):
    """Exit the application"""
    # 触发 cleanup
    raise SystemExit(0)

@registry.command(name="sessions", aliases=["resume"])
async def list_sessions(app: Shell, args: str):
    """List sessions and resume optionally"""
    # ... 选择其他 session ...
    if selection != current_session_id:
        # 切换 session 前清理当前 session 的后台任务
        await TaskManager().shutdown_session(current_session_id)
        raise Reload(session_id=selection)

@registry.command(name="clear", aliases=["reset"])
async def clear_context(app: Shell, args: str):
    """Clear context and stop all background tasks"""
    # 清空 context 前停止所有后台任务
    # 因为主 Agent 将"失忆"，无法处理任务结果
    session_id = app.soul.runtime.session.id
    await TaskManager().shutdown_session(session_id)
    
    # 然后清空 context
    app.soul.context.clear()
    await app._render_system_message("Context cleared. All background tasks stopped.")
```

### 7.4 其他决策事项 ✅

#### 7.4.1 消息合并

**决策：不合并，每个 Task Result 独立展示**

理由：
- 保持简单可预测
- 每个任务结果独立响应，LLM 更容易处理
- 避免批量展示导致的 context 过长

```python
# 不采用批量格式
❌ "3 个后台任务完成：\n1. task-001...\n2. task-002...\n3. task-003..."

# 采用独立消息
✅ "后台任务 task-001 完成：..."
✅ "后台任务 task-002 完成：..."
```

#### 7.4.2 Follow-up Message 超时处理

**决策：不设超时，永久保留直到处理**

理由：
- 消息队列是内存中的，session 结束时自然清理
- 用户可能长时间不查看消息，不应自动丢弃
- 如需清理，用户可通过 `/clear` 清空 context

```python
class MessageQueue:
    async def get_next(self) -> MessageItem:
        """获取下一条消息，永久等待"""
        while True:
            # 检查是否有消息
            if self._messages:
                return self._messages.pop(0)
            
            # 无消息则等待（无超时）
            await self._event.wait()
            self._event.clear()
```

#### 7.4.3 Session 重启后的任务恢复

**决策：不恢复消息，子 Agent 输出通过文件找回**

理由：
- 主 Agent 退出时子 Agent 已被清理，不会发送消息
- 消息持久化增加复杂度，收益有限
- 子 Agent 日志已保存到文件，用户可手动查看

**用户提示设计**：
```
用户: 分析代码库（后台运行）
Kimi: 任务已启动，ID: task-001
      日志保存到: ~/.kimi/tasks/task-001.log
      可随时通过「查看任务 task-001」获取结果
```

---

## 8. 实现计划

### Phase 1: 基础消息队列
- [ ] 实现 `MessageQueue` 类
- [ ] 实现 `AgentMessageBus` 基础版
- [ ] KimiSoul 集成 Follow-up Message 处理

### Phase 2: Multi-Agent 集成
- [ ] Task 工具支持 `run_in_background` 参数（向后兼容）
  - 添加 `run_in_background: bool = False` 参数
  - `False`：同步执行（现有行为）
  - `True`：后台执行，返回 `{"task_id": "...", "status": "running"}`
- [ ] 后台子 Agent 结果通过 Message Bus 发送
- [ ] 主 Agent 自动路由消息（User vs Follow-up）
- [ ] `/clear` 命令清理后台任务（主 Agent "失忆" 问题）

### Phase 3: 任务管理工具
- [ ] 实现 `TaskOutput` 工具（查询任务输出）
- [ ] 实现 `TaskStop` 工具（停止后台任务）
- [ ] 任务进度查询命令 `/task <id>`
- [ ] 任务列表命令 `/tasks`

### Phase 4: Feishu 优化
- [ ] 后台任务完成的消息卡片
- [ ] 任务状态实时推送

---

## 附录 A: 与 pi-mono 的对比

| 特性 | pi-mono | OKbot |
|-----|---------|-------|
| Steering | 快捷键 Enter | Slash 命令 `/stop` |
| Follow-up | 快捷键 Alt+Enter | 默认行为 |
| 消息队列 | 独立队列 | 统一队列 + 类型区分 |
| Agent 间通信 | 无 | Message Bus |
| 适用场景 | CLI TUI | Feishu Bot |

---

## 附录 B: 相关文档

- [Multi-Agent 实现层次分析](./multi-agent-levels.md) (待创建)
- [Task 工具设计](./task-tool-design.md) - 包含 TaskOutput、TaskStop 工具详细设计

---

## 附录 C: 完整工具清单与配置

### C.1 工具清单

| 工具 | 用途 | 调用者 | 工具类路径 |
|------|------|--------|-----------|
| `Task` | 启动子 Agent（同步/后台） | 主 Agent (LLM) | `kimi_cli.tools.multiagent:Task` |
| `TaskList` | 查询当前所有任务 | 主 Agent 或用户 | `kimi_cli.tools.multiagent:TaskList` |
| `TaskOutput` | 查询指定任务输出 | 主 Agent 或用户 | `kimi_cli.tools.multiagent:TaskOutput` |
| `TaskStop` | 停止后台任务 | 主 Agent 或用户 | `kimi_cli.tools.multiagent:TaskStop` |
| `/tasks` | 列出所有任务（slash 命令） | 用户 | - |
| `/task <id>` | 查看指定任务（slash 命令） | 用户 | - |

### C.2 Agent 配置方式

所有 Multi-Agent 工具通过 **agent.yaml** 配置，与现有工具完全一致：

```yaml
# src/kimi_cli/agents/default/agent.yaml
version: 1
agent:
  name: ""
  system_prompt_path: ./system.md
  tools:
    # ===== Multi-Agent 工具 =====
    - "kimi_cli.tools.multiagent:Task"        # 启动子 Agent
    - "kimi_cli.tools.multiagent:TaskList"    # 查询任务列表（新增）
    - "kimi_cli.tools.multiagent:TaskOutput"  # 查询任务输出（新增）
    - "kimi_cli.tools.multiagent:TaskStop"    # 停止后台任务（新增）
    
    # ===== 现有工具 =====
    - "kimi_cli.tools.shell:Shell"
    - "kimi_cli.tools.file:ReadFile"
    # ... 其他工具
```

### C.3 Task 工具向后兼容

**现有调用方式（同步执行）**：
```python
# 当前行为：阻塞直到子 Agent 完成
result = await Task(description="分析代码", subagent_name="coder", prompt="...")
```

**扩展后调用方式**：
```python
class Params(BaseModel):
    description: str
    subagent_name: str
    prompt: str
    run_in_background: bool = False  # 新增参数，默认 False（向后兼容）

# 方式 1：同步执行（默认，向后兼容）
result = await Task(
    description="分析代码",
    subagent_name="coder",
    prompt="..."
)

# 方式 2：后台执行（新增）
result = await Task(
    description="分析代码",
    subagent_name="coder",
    prompt="...",
    run_in_background=True
)
# 返回: {"task_id": "task-001", "status": "running"}
```

### C.4 工具类实现模板

所有工具遵循相同的 `CallableTool2` 模式：

```python
# src/kimi_cli/tools/multiagent/task_list.py
from kosong.tooling import CallableTool2, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

class TaskListParams(BaseModel):
    status: Optional[str] = Field(
        default=None,
        description="过滤状态: running/completed/failed/stopped"
    )

class TaskList(CallableTool2[TaskListParams]):
    name: str = "TaskList"
    params: type[TaskListParams] = TaskListParams
    
    def __init__(self, runtime: Runtime):
        super().__init__(description="查询当前 session 的所有后台任务")
        self._session = runtime.session
    
    async def __call__(self, params: TaskListParams) -> ToolReturnValue:
        tasks = TaskManager().list_tasks(self._session.id)
        # ... 过滤和格式化 ...
        return ToolOk(output=formatted_list)
```

**依赖注入**：工具通过 `runtime` 获取 session，与现有 `Task` 工具一致。

---

## 附录 D: Task 工具实现变更

### D.1 当前实现（Level 1 - 同步）

```python
# src/kimi_cli/tools/multiagent/task.py（当前）
class Task(CallableTool2[Params]):
    async def __call__(self, params: Params) -> ToolReturnValue:
        agent = self._labor_market.subagents[params.subagent_name]
        # 同步执行：阻塞直到子 Agent 完成
        result = await self._run_subagent(agent, params.prompt)
        return result  # ToolOk(output=...) 或 ToolError(...)
```

### D.2 扩展后实现（Level 2 - 同步+后台）

```python
# src/kimi_cli/tools/multiagent/task.py（扩展后）
class Params(BaseModel):
    description: str
    subagent_name: str  
    prompt: str
    run_in_background: bool = Field(
        default=False,
        description="是否在后台运行（不阻塞主 Agent）"
    )

class Task(CallableTool2[Params]):
    async def __call__(self, params: Params) -> ToolReturnValue:
        agent = self._labor_market.subagents[params.subagent_name]
        
        if params.run_in_background:
            # ===== 后台执行模式（新增）=====
            task = SubagentTask(
                session_id=self._session.id,
                description=params.description,
                subagent_name=params.subagent_name,
                agent=agent,
                prompt=params.prompt,
            )
            # 启动后台任务（不等待）
            await task.run_in_background()
            # 返回任务信息，让 LLM 知道任务已启动
            return ToolOk(
                output=f"后台任务已启动\n任务ID: {task.task_id}\n"
                       f"描述: {params.description}\n"
                       f"日志保存到: {task.output_file}"
            )
        else:
            # ===== 同步执行模式（现有行为，保持兼容）=====
            result = await self._run_subagent(agent, params.prompt)
            return result
```

### D.3 SubagentTask 类（后台任务包装器）

```python
# src/kimi_cli/tools/multiagent/task.py（新增）
class SubagentTask:
    """后台子 Agent 任务包装器"""
    
    def __init__(self, session_id: str, description: str, 
                 subagent_name: str, agent: Agent, prompt: str):
        self.task_id = f"task-{uuid4().hex[:8]}"
        self.session_id = session_id
        self.description = description
        self.subagent_name = subagent_name
        self.agent = agent
        self.prompt = prompt
        
        # 日志文件（用户可通过文件找回结果）
        self.output_file = (
            Path.home() / ".kimi" / "tasks" / f"{self.task_id}.log"
        )
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 状态
        self.status = TaskStatus.PENDING
        self._task: Optional[asyncio.Task] = None
        self._soul: Optional[KimiSoul] = None
        self.started_at: Optional[datetime] = None
    
    async def run_in_background(self) -> None:
        """在后台运行子 Agent"""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
        
        # 创建异步任务
        self._task = asyncio.create_task(self._run_and_publish())
        
        # 注册到 TaskManager
        TaskManager().add_task(self.session_id, self)
    
    async def _run_and_publish(self):
        """运行子 Agent 并在完成后发布结果"""
        try:
            # 创建子 Agent 上下文（与主 Agent 隔离）
            context = Context(file_backend=self.output_file)
            self._soul = KimiSoul(self.agent, context=context)
            
            # 运行子 Agent
            await run_soul(self._soul, self.prompt, ...)
            
            # 获取结果
            result = context.history[-1].extract_text(sep="\n")
            self.status = TaskStatus.COMPLETED
            
        except asyncio.CancelledError:
            self.status = TaskStatus.STOPPED
            result = "任务被用户取消"
        except Exception as e:
            self.status = TaskStatus.FAILED
            result = f"任务执行失败: {e}"
        
        finally:
            # 通过 MessageBus 发送结果给主 Agent
            message = BackgroundTaskResultMessage(
                task_id=self.task_id,
                session_id=self.session_id,
                task_description=self.description,
                subagent_name=self.subagent_name,
                status=self.status,
                output=result[:2000],  # 截断
                output_file=str(self.output_file),
                started_at=self.started_at.isoformat(),
                completed_at=datetime.now().isoformat(),
            )
            await message_bus.publish(self.session_id, message)
            
            # 从 TaskManager 移除（已完成）
            TaskManager().remove_task(self.session_id, self.task_id)
```

### D.4 关键设计点

| 设计点 | 说明 |
|--------|------|
| **向后兼容** | `run_in_background=False` 为默认值，现有代码无需修改 |
| **输出隔离** | 后台任务日志保存到独立文件，不污染主 Agent 上下文 |
| **自动清理** | 任务完成后自动从 TaskManager 移除，避免内存泄漏 |
| **状态查询** | 通过 `TaskList`/`TaskOutput` 工具查询运行中/已完成任务 |

---

## 附录 E: Session 生命周期与任务清理

```
Session 开始
    │
    ├── TaskManager.register_session(session_id)
    │
    └── 用户启动后台任务
            │
            ├── TaskManager.add_task(session_id, task)
            └── 子 Agent 开始运行
                    │
                    ├── 正常运行 → 完成后 MessageBus.publish()
                    │
                    └── Session 结束（exit/new/sessions/clear）
                            │
                            └── TaskManager.shutdown_session()
                                    │
                                    ├── 发送 stop 信号给子 Agent
                                    ├── 等待/强制取消
                                    └── 子 Agent 被清理（不恢复）

Session 重启
    │
    └── TaskManager.register_session(session_id)  # 新 session，无旧任务
        └── 旧任务不可恢复（已通过 shutdown 清理）
```

**重要原则**：
1. **Session 结束 = 任务清理**：exit/new/sessions/clear 都会触发 shutdown_session()，强制停止所有子 Agent
2. **Session 重启 = 重新开始**：新 session 不继承旧 session 的任务状态
3. **任务日志保留**：子 Agent 日志文件仍保留在磁盘，用户可手动查看历史日志
