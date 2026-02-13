# 定时任务文件处理功能

## 功能概述

定时任务现在支持**自动生成文件、上传到飞书、引用时读取内容**的完整流程。

## 使用场景

### 场景：每日自动生成数据报告

```
用户: /cron add "0 9 * * *" "生成昨日销售数据报告并发送给我"
```

定时任务执行时：

1. **Agent 执行任务**：
   ```python
   # Agent 生成报告文件
   report_path = "/Users/okbot/workspace/reports/sales_2024-02-09.md"
   # 输出: "报告已生成: /Users/okbot/workspace/reports/sales_2024-02-09.md"
   ```

2. **自动检测文件**：系统从输出中提取文件路径

3. **上传到飞书**：
   - 文件自动上传到飞书服务器
   - 获取 file_key

4. **发送卡片 + 文件**：

```
┌─────────────────────────────────────┐
│ ✅ 定时任务完成                      │
├─────────────────────────────────────┤
│ 任务ID: daily_sales_report          │
│ 执行时间: 2024-02-09 09:00:00       │
├─────────────────────────────────────┤
│ 报告已生成:                          │
│ 昨日销售额: ¥125,000 (+15%)         │
│ 订单量: 342 单                      │
│ ...                                 │
├─────────────────────────────────────┤
│ 📎 生成文件 (1个):                  │
│ 1. sales_2024-02-09.md              │
└─────────────────────────────────────┘
```

同时，文件 `sales_2024-02-09.md` 会作为单独的消息发送到对话中。

5. **用户引用卡片提问**：

```
用户（引用任务卡片）: "分析一下销售增长的原因"
```

Agent 看到的输入：
```
分析一下销售增长的原因

[引用消息]:
【✅ 定时任务完成】
任务ID: daily_sales_report
执行时间: 2024-02-09 09:00:00
报告已生成: 昨日销售额: ¥125,000 (+15%)...

[关联文件内容]:
=== sales_2024-02-09.md ===
# 销售数据日报 (2024-02-09)

## 核心指标
- 销售额: ¥125,000 (环比 +15%)
- 订单量: 342 单 (环比 +8%)
- 客单价: ¥365 (环比 +6%)

## 增长分析
1. 促销活动 "新春特惠" 带来流量增长 25%
2. 新品上架 3 款，贡献销售额 ¥35,000
3. 复购率提升至 32% (+5%)
...
```

Agent 回复：
> 根据报告数据分析，销售增长主要有以下原因：
> 1. **促销活动效果显著**："新春特惠"带来25%流量增长
> 2. **新品表现优异**：3款新品贡献¥35,000销售额
> 3. **用户粘性提升**：复购率增长5个百分点至32%
> 
> 建议继续保持促销力度，并关注新品的长期表现。

## 技术实现

### 1. 文件检测与提取

```python
# session.py
class ScheduledTaskSession:
    def _extract_file_paths(self, output: str) -> list[str]:
        """从任务输出中提取文件路径"""
        patterns = [
            r'(/[\w\-./]+\.[\w]+)',  # 绝对路径
            r'(~/[\w\-./]+\.[\w]+)',  # 家目录路径  
            r'(\./[\w\-./]+\.[\w]+)',  # 相对路径
            r'(?:保存到|生成在)[：:]\s*([\w\-./~/]+\.[\w]+)',  # 中文提示
        ]
        # 验证文件存在后返回路径列表
```

### 2. 文件上传到飞书

```python
async def _upload_files_to_feishu(self, files: list[str], client) -> list[dict]:
    """上传文件到飞书"""
    for file_path in files:
        file_content = Path(file_path).read_bytes()
        file_key = client.upload_file(file_content, file_name, file_type)
        # 返回 file_key 用于后续发送
```

### 3. 卡片显示文件信息

```python
def _build_result_card(self, result: ScheduledResult) -> dict:
    elements = [...]  # 原有内容
    
    if result.feishu_files:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**📎 生成文件 ({len(result.feishu_files)}个):**\n"
                          + "\n".join([f"{i}. {f['file_name']}" ...])
            }
        })
```

### 4. 引用时读取文件内容

```python
async def _load_scheduled_task_files(self, card_text: str) -> str | None:
    """从历史记录中加载文件并读取内容"""
    # 1. 提取任务ID
    job_id = extract_job_id(card_text)
    
    # 2. 从历史记录查找文件
    record = history_store.find_by_job_id(job_id)
    
    # 3. 读取文本文件内容
    for file_path in record.files:
        content = Path(file_path).read_text()
        file_contents.append(f"=== {name} ===\n{content}")
    
    return "\n\n".join(file_contents)
```

## 支持的文件类型

| 类型 | 处理方式 | 引用时可读取 |
|------|----------|--------------|
| `.md`, `.txt`, `.json`, `.csv` | 上传 + 发送 | ✅ 直接读取内容 |
| `.pdf`, `.docx`, `.xlsx` | 上传 + 发送 | ❌ 提供文件信息 |
| `.png`, `.jpg` | 上传 + 发送图片 | ❌ 提供图片说明 |
| 其他格式 | 上传 + 发送 | ❌ 仅文件名 |

## 限制说明

1. **文件大小**: 单个文件最大 500KB 可被读取引用，超过则只显示文件名
2. **文件数量**: 最多同时处理 10 个文件
3. **历史记录**: 文件信息保留 7 天，之后引用卡片将无法读取文件内容
4. **飞书文件**: 上传到飞书的文件引用时无法直接读取内容，需要重新上传

## 最佳实践

### 1. 任务描述清晰
```
❌ 不好的描述: "生成报告"
✅ 好的描述: "生成昨日销售数据报告，保存为Markdown格式"
```

### 2. 输出文件路径
Agent 应该明确输出文件路径：
```
✅ 好的输出: "报告已保存到: /workspace/reports/sales_2024-02-09.md"
❌ 不好的输出: "报告已生成" (没有路径信息)
```

### 3. 引用提问技巧
```
❌ 模糊的提问: "这个怎么样？"
✅ 具体的提问: "根据销售报告，哪个产品线增长最快？"
```
