# 子 Agent 间定向通信方案

基于当前的消息总线架构，实现子 Agent 之间的定向通信。

## 当前架构回顾

```python
# 消息总线支持 target 定向投递
message_bus.subscribe(session_id, callback, target="main")
message_bus.publish(session_id, message)  # message.target 指定接收方
```

## 方案 1: 使用 Task ID 作为标识（简单）

### 1. 修改 SubagentTask 启用消息总线并使用 task_id 作为 target

```python
# task_manager.py

async def _run_and_publish(self, ...):
    # ...
    
    # 创建子 Agent，启用消息总线，使用 task_id 作为 target
    context = Context(file_backend=self.output_file)
    self._soul = KimiSoul(
        self.agent, 
        context=context,
        enable_message_bus=True,  # 启用！
        message_bus_target=self.task_id  # 使用 task_id 作为唯一标识
    )
    
    # 设置消息处理回调
    async def on_subagent_message(msg):
        """处理其他 Agent 发来的消息"""
        if msg.target == self.task_id:
            # 将消息注入上下文，让子 Agent 可以看到
            await self._soul.message_queue.put(msg)
    
    # 订阅消息（可选，如果需要接收其他 Agent 的消息）
    from kimi_cli.soul.followup import message_bus
    message_bus.subscribe(
        self.session_id, 
        on_subagent_message, 
        target=self.task_id
    )
    
    try:
        await run_soul(self._soul, self.prompt, ...)
    finally:
        message_bus.unsubscribe(self.session_id, target=self.task_id)
```

### 2. 添加 send_message_to_task 工具

```python
# tools/multiagent/send_message.py

class SendMessageParams(BaseModel):
    target_task_id: str = Field(description="目标任务的 ID")
    content: str = Field(description="消息内容")
    msg_type: str = Field(default="message", description="消息类型")

class SendMessage(CallableTool2[SendMessageParams]):
    """发送消息给指定的子 Agent"""
    
    async def __call__(self, params: SendMessageParams) -> ToolReturnValue:
        from kimi_cli.soul.followup import SubagentMessage
        
        msg = SubagentMessage(
            task_id=self._session.id,  # 发送方标识
            session_id=self._session.id,
            target=params.target_task_id,  # 指定接收方
            sender="main",  # 或当前 agent 的标识
            output=params.content,
            msg_type=params.msg_type,
        )
        
        success = await message_bus.publish(self._session.id, msg)
        
        if success:
            return ToolOk(output=f"消息已发送给 {params.target_task_id}")
        else:
            return ToolError(message=f"目标 Agent {params.target_task_id} 不在线")
```

### 3. 使用示例

```python
# 启动两个子 Agent
task1 = await Task(description="A", ..., run_in_background=True)
task2 = await Task(description="B", ..., run_in_background=True)

# 等待它们启动
await asyncio.sleep(2)

# 让 A 向 B 发送消息
await SendMessage(
    target_task_id=task2.task_id,
    content="Hello B, please process this data: {...}",
    msg_type="request"
)

# B 会收到消息并可以回复
await SendMessage(
    target_task_id=task1.task_id,
    content="A, I got the data, result is: {...}",
    msg_type="response"
)
```

## 方案 2: 命名服务（更灵活）

### 1. 创建 Agent 注册中心

```python
# soul/followup/agent_registry.py

class AgentRegistry:
    """Agent 注册中心，支持通过名称查找 Agent"""
    
    def __init__(self):
        self._agents: Dict[str, Dict] = {}  # session_id -> {name: info}
    
    def register(self, session_id: str, name: str, task_id: str, capabilities: List[str]):
        """注册 Agent"""
        if session_id not in self._agents:
            self._agents[session_id] = {}
        
        self._agents[session_id][name] = {
            "task_id": task_id,
            "capabilities": capabilities,
            "status": "running",
            "registered_at": datetime.now(),
        }
    
    def resolve(self, session_id: str, name: str) -> Optional[str]:
        """解析名称到 task_id"""
        session_agents = self._agents.get(session_id, {})
        agent_info = session_agents.get(name)
        return agent_info["task_id"] if agent_info else None
    
    def discover(self, session_id: str, capability: str) -> List[str]:
        """发现具有特定能力的 Agent"""
        session_agents = self._agents.get(session_id, {})
        return [
            name for name, info in session_agents.items()
            if capability in info["capabilities"]
        ]
    
    def unregister(self, session_id: str, name: str):
        """注销 Agent"""
        if session_id in self._agents:
            self._agents[session_id].pop(name, None)

# 全局注册中心
agent_registry = AgentRegistry()
```

### 2. 注册 Agent

```python
# task_manager.py - 在 run_in_background 中

async def run_in_background(self, ...):
    # ...
    
    # 注册到 Agent 注册中心
    from kimi_cli.soul.followup import agent_registry
    agent_registry.register(
        session_id=self.session_id,
        name=self.description,  # 使用描述作为名称
        task_id=self.task_id,
        capabilities=self.agent.capabilities  # Agent 的能力列表
    )
    
    try:
        await self._run_and_publish(...)
    finally:
        agent_registry.unregister(self.session_id, self.description)
```

### 3. 通过名称发送消息

```python
class SendMessageToAgent(CallableTool2[...]):
    """通过名称发送消息给 Agent"""
    
    async def __call__(self, params):
        # 解析名称到 task_id
        target_id = agent_registry.resolve(
            self._session.id, 
            params.agent_name
        )
        
        if not target_id:
            # 尝试发现具有能力的 Agent
            candidates = agent_registry.discover(
                self._session.id,
                params.required_capability
            )
            if candidates:
                target_id = agent_registry.resolve(
                    self._session.id,
                    candidates[0]
                )
        
        if not target_id:
            return ToolError(message=f"找不到 Agent: {params.agent_name}")
        
        # 发送消息
        msg = SubagentMessage(
            target=target_id,
            sender=self._context.task_id,  # 当前 Agent ID
            ...
        )
        await message_bus.publish(self._session.id, msg)
```

### 4. 使用示例

```python
# 启动具有不同能力的 Agent
await Task(
    description="data-parser",
    subagent_name="parser",
    run_in_background=True
)

await Task(
    description="data-analyzer", 
    subagent_name="analyzer",
    run_in_background=True
)

await Task(
    description="report-writer",
    subagent_name="writer",
    run_in_background=True
)

# 通过名称发送消息
await SendMessageToAgent(
    agent_name="data-parser",
    content="Parse this CSV file",
)

# 或通过能力发现 Agent
await SendMessageToAgent(
    required_capability="data_analysis",
    content="Analyze the parsed data",
)
```

## 方案 3: 工作流编排（最强大）

### 定义工作流

```yaml
# workflow.yaml
name: data_pipeline

steps:
  - name: parser
    agent: csv_parser
    input: "{{initial_input}}"
    next: analyzer
    
  - name: analyzer
    agent: data_analyzer
    condition: "if data.valid"
    next:
      - writer  # 成功
      - error_handler  # 失败
      
  - name: writer
    agent: report_writer
    input: "{{analyzer.result}}"
    
  - name: error_handler
    agent: error_reporter
```

### 工作流引擎

```python
class WorkflowEngine:
    """工作流引擎，自动管理 Agent 间通信"""
    
    def __init__(self, workflow_def: dict, session_id: str):
        self.workflow = workflow_def
        self.session_id = session_id
        self.agents: Dict[str, SubagentTask] = {}
        self.results: Dict[str, Any] = {}
        
    async def execute(self, initial_input: str):
        """执行工作流"""
        current_step = self.workflow["steps"][0]
        
        while current_step:
            # 启动或获取 Agent
            agent = await self._get_or_create_agent(current_step)
            
            # 准备输入（替换模板变量）
            input_data = self._render_template(
                current_step["input"], 
                initial_input
            )
            
            # 发送消息给 Agent
            await self._send_to_agent(
                agent.task_id,
                input_data
            )
            
            # 等待结果
            result = await self._wait_for_result(agent.task_id)
            self.results[current_step["name"]] = result
            
            # 决定下一步
            next_step_name = self._determine_next_step(
                current_step, 
                result
            )
            current_step = self._get_step(next_step_name)
    
    async def _send_to_agent(self, task_id: str, data: str):
        """发送消息给工作流中的 Agent"""
        msg = SubagentMessage(
            target=task_id,
            sender="workflow_engine",
            output=data,
            msg_type="workflow_input",
        )
        await message_bus.publish(self.session_id, msg)
```

## 关键修改点

### 1. KimiSoul 支持自定义 target

```python
class KimiSoul:
    def __init__(
        self,
        agent: Agent,
        *,
        context: Context,
        enable_message_bus: bool = True,
        message_bus_target: str | None = None,  # 新增参数
    ):
        # ...
        if enable_message_bus:
            target = message_bus_target or MAIN_AGENT_TARGET
            self._register_message_bus(target=target)
```

### 2. SubagentMessage 消息类型

```python
@dataclass
class SubagentMessage(BackgroundTaskResultMessage):
    """子 Agent 间通信消息"""
    sender: str = ""           # 发送方标识
    msg_type: str = "result"   # result | request | response | status
    correlation_id: str = ""   # 关联 ID，用于请求-响应配对
    priority: int = 0          # 优先级
    ttl: int = 300            # 生存时间（秒）
```

### 3. 在 Agent YAML 中启用

```yaml
# agent.yaml
tools:
  - "kimi_cli.tools.multiagent:Task"
  - "kimi_cli.tools.multiagent:SendMessage"        # 新增
  - "kimi_cli.tools.multiagent:SendMessageToAgent" # 新增
  - "kimi_cli.tools.multiagent:DiscoverAgents"     # 新增
  
features:
  inter_agent_messaging: true  # 启用子 Agent 间通信
```

## 使用场景

1. **工作流分解**：复杂任务拆分为多个步骤，每个步骤一个 Agent
2. **协作处理**：多个 Agent 同时处理不同部分，然后合并结果
3. **主从架构**：主 Agent 分配任务，多个子 Agent 并行处理
4. **链式调用**：Agent A -> Agent B -> Agent C 的管道处理

## 示例：并行处理然后汇总

```python
# 启动 3 个分析 Agent
analyzers = []
for i in range(3):
    task = await Task(
        description=f"analyzer-{i}",
        prompt=f"Analyze section {i} of the data",
        run_in_background=True
    )
    analyzers.append(task)

# 等待所有分析完成
results = await asyncio.gather(*[
    wait_for_task(a.task_id) for a in analyzers
])

# 启动汇总 Agent
summarizer = await Task(
    description="summarizer",
    prompt="Summarize these 3 analysis results",
    run_in_background=True
)

# 将 3 个结果发送给汇总 Agent
for i, result in enumerate(results):
    await SendMessage(
        target_task_id=summarizer.task_id,
        content=f"Analysis {i}: {result}",
    )
```
