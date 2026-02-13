# OKbot 定时任务模块

为 OKbot 提供定时任务功能的模块，支持静默执行、队列等待和批量通知。

## 功能特性

- **Cron 表达式调度**: 支持标准 Cron 表达式
- **静默执行**: 定时任务使用独立的 Session ID，不影响用户对话
- **队列等待**: 用户对话忙碌时，任务结果自动进入队列等待
- **批量通知**: 用户对话结束后，批量发送等待队列中的结果
- **数据持久化**: 任务配置和等待队列支持持久化存储

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Event Sources                                │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────────────────┐  │
│  │ Feishu        │  │  Scheduler    │  │   Webhook (Future)      │  │
│  │ WebSocket     │  │  (Mock)       │  │                         │  │
│  └───────┬───────┘  └───────┬───────┘  └───────────┬─────────────┘  │
│          │                  │                      │                │
│          └──────────────────┼──────────────────────┘                │
│                             │                                       │
│                             ▼                                       │
│              ┌──────────────────────────────┐                       │
│              │     IncomingMessage          │  ← 统一消息对象       │
│              │     (text, chat_id, user_id) │                       │
│              └──────────────┬───────────────┘                       │
│                             │                                       │
│                             ▼                                       │
│              ┌──────────────────────────────┐                       │
│              │   MessageDispatcher          │                       │
│              │   - dispatch()               │                       │
│              └──────────────┬───────────────┘                       │
│                             │                                       │
│              ┌──────────────┼──────────────┐                        │
│              ▼              ▼              ▼                        │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐       │
│  │ Feishu Session  │ │ Scheduled Sess  │ │ Queue Manager   │       │
│  │ (正常对话)       │ │ (定时任务)       │ │ (等待队列)       │       │
│  │                 │ │ - 独立SessionID │ │                 │       │
│  │                 │ │ - 静默执行       │ │                 │       │
│  └────────┬────────┘ └────────┬────────┘ └─────────────────┘       │
│           │                   │                                     │
│           └───────────┬───────┘                                     │
│                       ▼                                             │
│              ┌─────────────────┐                                    │
│              │ Feishu Client   │                                    │
│              │ send_message()  │  ← 返回消息使用卡片或文本         │
│              │ send_card()     │                                     │
│              └─────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────┘
```

## 使用方法

### 飞书命令

```
# 显示帮助
/cron help

# 列出所有定时任务
/cron list

# 创建定时任务
/cron add "0 9 * * *" "生成日报"

# 删除定时任务
/cron remove <任务ID>

# 切换任务开关
/cron toggle <任务ID>
```

### Cron 表达式格式

格式: `分 时 日 月 周`

- 分: 0-59
- 时: 0-23
- 日: 1-31
- 月: 1-12
- 周: 0-6 (0=周日)

示例:
- `0 9 * * *` - 每天上午9点
- `0 9 * * 1` - 每周一上午9点
- `0 9 1 * *` - 每月1日上午9点
- `*/30 * * * *` - 每30分钟

### 代码中使用

```python
from kimi_cli.scheduler import get_scheduler, IncomingMessage

# 获取调度器实例
scheduler = get_scheduler()

# 添加任务
success, message, job = await scheduler.add_job(
    cron="0 9 * * *",
    description="生成日报",
    user_id="user123",
    chat_id="chat456",
)

# 列出任务
jobs = await scheduler.list_jobs(chat_id="chat456")

# 删除任务
success, message = await scheduler.remove_job("job_xxx")

# 从飞书消息创建 IncomingMessage
message = IncomingMessage.from_feishu_message(feishu_event)

# 通过调度器处理飞书消息
await scheduler.dispatch_feishu_message(feishu_event)
```

## 核心模块

### models.py
数据模型定义：
- `ScheduledJob`: 定时任务配置
- `IncomingMessage`: 统一消息对象，**兼容飞书消息格式**
  - `from_feishu_message()`: 从飞书消息直接创建
  - 支持 text/image/file/audio 消息类型
- `ScheduledResult`: 任务执行结果
- `PendingNotification`: 等待发送的通知

### store.py
存储模块：
- `JobStore`: 任务存储（JSON 文件）
- `PendingResultStore`: 等待结果存储

### cron_engine.py
Cron 调度引擎：
- `CronEngine`: 基于 croniter 的调度引擎
- `validate_cron()`: 验证 Cron 表达式
- `get_next_runs()`: 获取下次执行时间

### dispatcher.py
消息分发器：
- `MessageDispatcher`: 统一消息分发
- `SessionManager`: 会话管理器

### session.py
定时任务会话：
- `ScheduledTaskSession`: 支持静默执行和队列等待的会话

### scheduler.py
主调度器：
- `Scheduler`: 定时任务管理主类
- `get_scheduler()`: 获取全局实例

### commands.py
命令处理器：
- `handle_cron_command()`: 处理 /cron 命令

## 存储位置

- 任务配置: `~/.kimi/scheduler/jobs/{job_id}.json`
- 等待队列: `~/.kimi/scheduler/pending/{chat_id}.json`

## 消息发送逻辑

### 发送时机

1. **直接发送**: 当飞书会话空闲时，定时任务执行完成后立即发送结果
2. **队列等待**: 当飞书会话忙碌时，结果进入等待队列
3. **批量刷新**: 飞书会话处理完用户消息后，自动刷新等待队列

### 发送方式

支持两种消息格式：

1. **卡片消息**（默认）：使用飞书交互式卡片，更美观
   - 单条结果：显示任务ID、执行时间、输出内容
   - 合并结果：显示任务统计和摘要列表

2. **文本消息**：普通文本，兼容性更好

### 消息格式示例

**单条成功结果（卡片）：**
```
┌─────────────────────────────┐
│ ✅ 定时任务完成              │
├─────────────────────────────┤
│ 任务ID: job_xxx             │
│ 执行时间: 2025-01-09 09:00  │
│ ───────────────────────────│
│ [任务输出内容...]           │
└─────────────────────────────┘
```

**合并结果（卡片）：**
```
┌─────────────────────────────┐
│ 📋 定时任务汇总 (3 个)       │
├─────────────────────────────┤
│ 执行统计: ✅ 2 成功 ❌ 1 失败│
│ ───────────────────────────│
│ 1. ✅ job_1                 │
│    [输出摘要...]            │
│ 2. ❌ job_2                 │
│    错误: xxx                │
│ ...                         │
└─────────────────────────────┘
```

## 集成说明

调度器通过修改 `sdk_server.py` 集成：

1. 在 `SDKChatSession.handle_message()` 中添加 /cron 命令处理
2. 在 `handle_message()` 的 finally 块中刷新等待队列
3. 在 `FeishuSDKServer._init_accounts()` 中初始化调度器
4. 在 `FeishuSDKServer.stop()` 中关闭调度器

## 依赖

- `croniter`: Cron 表达式解析
- `loguru`: 日志记录
