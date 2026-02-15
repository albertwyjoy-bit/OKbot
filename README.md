# OKbot - 飞书版 Kimi Code CLI

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](https://www.python.org/)
[![Kimi CLI](https://img.shields.io/badge/Kimi%20CLI-v1.9.0-orange)](https://github.com/MoonshotAI/kimi-cli)
[![Feishu](https://img.shields.io/badge/Feishu-Lark%20SDK-green)](https://open.feishu.cn/)
[![License](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)](https://opensource.org/licenses/Apache-2.0)

> **随时随地，通过飞书操控 Kimi CLI。**

📺 **视频介绍**: [Bilibili - OKbot 功能演示](https://www.bilibili.com/video/BV1ZJcczHExX/)

OKbot 是 [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli) 的飞书扩展版本，让你**通过飞书与 Kimi CLI 实时交互**，随时随地操控 PC 和 Android 设备。

[📖 详细文档](./docs/detailed-readme.md) · [🚀 快速开始](#快速开始) · [🎬 功能演示](#功能演示)

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🧠 **Plan Mode** | 系统性权限管理，`/plan` 进入规划模式，禁止修改操作确保分析阶段安全 |
| 🕐 **定时任务** | 自然语言创建定时任务，Agent 自动执行，支持文件自动生成 |
| 🔄 **跨端接续** | CLI 上开发到一半随时切飞书继续，100% 复用 Kimi CLI Session |
| 🛠️ **动态 Skills** | 飞书中随时让 AI 帮你写 Skills，热更新立即生效 |
| 🔄 **MCP 热更新** | 运行时动态添加/删除 MCP 服务器，无需重启 |
| ✅ **灵活授权** | YOLO 自动批准（默认）和交互式卡片授权两种模式 |
| 🎤 **语音消息** | 支持飞书语音，使用智谱 ASR 自动识别 |
| 💬 **富媒体** | 支持图片、文件收发；移动端直接操控 PC |
| 🤖 **设备操控** | 支持控制 PC 浏览器（Chrome）和 Android 手机 |
| 🧠 **Memory 系统** | 长期记忆支持，跨会话上下文检索，基于 GLM/Kimi 嵌入模型 |
| 🎨 **文生图** | 自然语言生成图片，支持火山引擎 Ark 等图像生成服务 |

---

## 快速开始

### 一键安装（推荐）

```bash
git clone https://github.com/albertwyjoy-bit/OKbot.git
cd OKbot
./install.sh
```

### 手动安装

```bash
# 1. 克隆项目
git clone https://github.com/albertwyjoy-bit/OKbot.git
cd OKbot

# 2. 创建环境
conda create -n okbot python=3.12 -y
conda activate okbot
pip install -e ".[dev]"
pip install lark-oapi

# 3. 配置飞书应用凭证
# 创建 ~/.kimi/feishu.toml（参考 feishu.example.toml）
```

### 启动服务

```bash
python -m kimi_cli.feishu
```

首次运行会引导完成 Kimi OAuth 授权。

---

## 功能演示

### 跨端 Session 接续

CLI ↔ 飞书无缝切换，任务随时带走：

```bash
# CLI 端创建 session
$ kimi chat
# ... 对话中，session ID: abc123...

# 飞书端接续
/sessions          # 查看可用 sessions
/continue abc123   # 接续指定 session

# 创建新会话
/new               # 清空上下文，获取新的 Session ID
```

📹 [查看演示视频](./docs/detailed-readme.md#跨端-session-接续)

### 操控 PC 浏览器

通过飞书发送自然语言指令，在 PC 端自动操控浏览器：

> "打开 Chrome，访问 GitHub，搜索 kimi-cli"

📹 [查看演示视频](./docs/detailed-readme.md#操控-pc-浏览器)

### 模型切换

无需重启，随时切换对话模型和 Thinking 模式：

```
/model
```

执行后会显示交互式卡片，支持：
- 切换不同模型（Kimi、GLM 等）
- 开启/关闭 Thinking 模式
- 实时生效，无需重启服务

### 定时任务

用自然语言创建定时任务，OKbot 自动解析执行：

> "每天早上9点调研科技新闻并生成报告"
> "每30分钟提醒我喝水"

📖 [定时任务详细文档](./docs/scheduler_file_handling.md)

### 文生图

通过自然语言描述生成图片：

> "生成一张夕阳下的海滩，金色阳光洒在沙滩上，远处有椰树"
> "画一只戴着墨镜的猫，赛博朋克风格，霓虹灯光背景"

📖 [文生图配置说明](./docs/detailed-readme.md#24-%E7%81%AB%E5%B1%B1%E5%BC%95%E6%93%8E-ark-api-key%E5%8F%AF%E9%80%89%E7%94%A8%E4%BA%8E%E6%96%87%E7%94%9F%E5%9B%BE)

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/plan` | 进入 Plan Mode（规划模式，禁止修改操作） |
| `/stop` | 打断当前操作（保留上下文） |
| `/clear` | 清空上下文 |
| `/new` | 创建新会话（获取新的 Session ID） |
| `/model` | 切换模型和 Thinking 模式 |
| `/yolo` | 切换自动批准模式 |
| `/cron` | 管理定时任务（list/remove/toggle） |
| `/sessions` | 列出 CLI sessions |
| `/continue <id>` | 接续指定 session |
| `/id` | 查看当前 session ID |
| `/mcp` | 查看 MCP 状态 |
| `/update-mcp` | 热更新 MCP 配置 |
| `/update-skill` | 重新加载 Skills |

---

## 项目结构

```
OKbot/
├── src/kimi_cli/
│   ├── feishu/          # 飞书集成核心模块
│   ├── scheduler/       # 定时任务模块
│   └── tools/feishu/    # Feishu 工具集
├── docs/
│   ├── detailed-readme.md      # 📖 详细文档
│   ├── scheduler_file_handling.md  # 定时任务文档
│   └── voice-messages.md       # 语音功能文档
├── feishu.example.toml  # 飞书配置示例
└── install.sh           # 一键安装脚本
```

---

## 配置说明

### 必需配置

1. **Kimi Code Plan**（推荐）：https://kimi.com/code
2. **飞书应用凭证**：创建应用并获取 App ID / App Secret
3. **智谱 API Key**（可选）：用于语音和图像识别
4. **火山引擎 Ark**（可选）：用于文生图功能

📖 [详细配置指南](./docs/detailed-readme.md#步骤-2申请-api-key推荐)

### 文生图配置（可选）

在 `~/.kimi/config.toml` 中配置：

```toml
[services.image_generation]
enabled = true                          # 是否启用文生图
base_url = "https://ark.cn-beijing.volces.com/api/v3"
api_key = "your-ark-api-key"            # 火山引擎 Ark API Key
model = "doubao-seedream-4-5-251128"    # 图像生成模型
```

📖 [详细配置说明](./docs/detailed-readme.md#24-%E7%81%AB%E5%B1%B1%E5%BC%95%E6%93%8E-ark-api-key%E5%8F%AF%E9%80%89%E7%94%A8%E4%BA%8E%E6%96%87%E7%94%9F%E5%9B%BE)

### 快速配置

```toml
# ~/.kimi/feishu.toml
host = "127.0.0.1"
port = 18789
default_account = "bot"

[accounts.bot]
app_id = "cli_xxxxx"
app_secret = "xxxxxxxx"
auto_approve = true
```

---

## 文档导航

| 文档 | 说明 |
|------|------|
| [📖 完整 README](./docs/detailed-readme.md) | 详细安装步骤、配置说明、使用指南 |
| [🧠 Plan Mode](./docs/detailed-readme.md#-plan-mode规划模式) | 规划模式使用说明和权限管理 |
| [🧠 Memory 系统](./docs/memory.md) | 长期记忆系统配置和使用指南 |
| [🕐 定时任务](./docs/scheduler_file_handling.md) | 定时任务功能详细说明 |
| [🎤 语音消息](./docs/voice-messages.md) | 语音功能配置和使用 |
| [🎨 文生图配置](./docs/detailed-readme.md#24-%E7%81%AB%E5%B1%B1%E5%BC%95%E6%93%8E-ark-api-key%E5%8F%AF%E9%80%89%E7%94%A8%E4%BA%8E%E6%96%87%E7%94%9F%E5%9B%BE) | 文生图功能配置说明 |
| [🤖 MiniMax 2.5 配置](./docs/detailed-readme.md#25-minimax-25-配置可选) | MiniMax 2.5 模型接入指引 |
| [🛠️ MCP 配置](./docs/detailed-readme.md#步骤-4安装并配置-mcp-服务器可选但推荐) | MCP 服务器配置指南 |
| [🔧 高级配置](./docs/detailed-readme.md#高级配置) | 工作目录、Skills、代理等高级配置 |

---

## 为什么选 OKbot？

- **无缝衔接**：CLI 和飞书共享同一个 Session，随时切换设备继续工作
- **移动端操控 PC**：通过手机飞书控制电脑浏览器和 Android 设备
- **零停机更新**：Skills 和 MCP 配置热更新，无需重启服务
- **生产级稳定**：OAuth 自动刷新、连接保活、错误自动恢复

---

## 常见问题

**Q: Plan Mode 是什么？如何使用？**
> Plan Mode（规划模式）通过系统性权限管理规范模型行为。进入后只允许只读操作（查看文件、搜索等），禁止任何修改操作（写入文件、执行命令等）。使用 `/plan` 进入，完成规划后使用 `PlanExit` 工具退出。
> 📖 [详细说明](./docs/detailed-readme.md#-plan-mode规划模式)

**Q: 如何配置 MiniMax 2.5 模型？**
> 在 `~/.kimi/config.toml` 中添加 MiniMax 提供商配置，支持 `MiniMax-Text-01` 等模型。
> 📖 [配置指引](./docs/detailed-readme.md#23-minimax-25-配置可选)
>
> **注意**：配置步骤编号在详细文档中有所变化，MiniMax 仍为 2.3，飞书凭证现为 2.5。

**Q: 如何配置 GLM 作为对话模型？**
> 参考 [GLM 配置说明](./docs/detailed-readme.md#22-%E6%99%BA%E8%B0%B1-ai-api-key%E5%8F%AF%E9%80%89%E7%94%A8%E4%BA%8E%E8%AF%AD%E9%9F%B3%E5%92%8C%E5%9B%BE%E5%83%8F%E7%90%86%E8%A7%A3)

**Q: 如何配置文生图功能？**
> 需要申请火山引擎 Ark API Key，并在 `~/.kimi/config.toml` 中配置 `services.image_generation`。
> 📖 [配置说明](./docs/detailed-readme.md#24-%E7%81%AB%E5%B1%B1%E5%BC%95%E6%93%8E-ark-api-key%E5%8F%AF%E9%80%89%E7%94%A8%E4%BA%8E%E6%96%87%E7%94%9F%E5%9B%BE)

**Q: MCP 工具名称冲突？**
> 已自动添加 `{server}__` 前缀，如 `midscene-web__Tap`

**Q: 如何打断正在执行的任务？**
> 发送 `/stop` 命令，类似 CLI 中的 Ctrl+C

📖 [更多 FAQ](./docs/detailed-readme.md#%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98)

---

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。

原始项目 [MoonshotAI/kimi-cli](https://github.com/MoonshotAI/kimi-cli) 版权归 Moonshot AI 所有。
