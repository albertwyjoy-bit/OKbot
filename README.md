# OKbot - Kimi Feishu Integration

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](https://www.python.org/)
[![Feishu](https://img.shields.io/badge/Feishu-Lark%20SDK-green)](https://open.feishu.cn/)
[![License](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)](https://opensource.org/licenses/Apache-2.0)

> **Touch Kimi CLI anywhere, anytime.**

**OKbot** 以 [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli) 为大脑，让你在飞书聊天中通过自然语言操控 PC 浏览器和手机应用，打通手机与 PC 之间的通信桥梁，支持文件、图片双向传输，实现跨设备智能协作。

> 🌟 **Forked from**: [MoonshotAI/kimi-cli](https://github.com/MoonshotAI/kimi-cli)

## 🎬 Showcase

通过飞书直接让 Kimi CLI 帮你完成各种任务！

![Showcase](./images/showcase.gif)

**演示场景**：用户在飞书中发送视频链接，Kimi CLI 自动完成下载、压缩视频，并将处理后的文件发送回飞书。全程无需手动操作电脑，随时随地通过手机即可操控 PC。

## ✨ 主要特性

### 🤖 飞书深度集成
- **SDK WebSocket 长连接**：基于飞书官方 SDK，稳定接收和发送消息
- **消息实时响应**：支持群聊和私聊，自动回复用户消息
- **👌 OK 表情反馈**：收到消息时自动添加 👌 反应，表示已收到
- **富媒体支持**：支持图片、文件下载和处理

### 🛠️ MCP 工具生态
- **多 MCP 服务器支持**：可同时连接多个 MCP 服务器，工具名自动添加前缀避免冲突
  - `midscene-android__Tap` - Android 自动化测试
  - `midscene-web__Tap` - Web 自动化测试
  - `chrome-devtools__navigate_page` - Chrome 浏览器控制
  - `notion__API-post-page` - Notion 文档操作
  - `markitdown__convert_to_markdown` - 文件格式转换

### 🔐 OAuth 令牌自动刷新
- **智能令牌管理**：长对话场景下自动刷新 OAuth 令牌（每 60 秒检查一次）
- **双令牌体系**：
  - Feishu 租户令牌（2 小时有效期）
  - Kimi OAuth 令牌（30 分钟有效期）

### 🌐 Web 自动化测试（Midscene）
- **AI 驱动的 Web 自动化**：通过自然语言描述执行浏览器操作
- **支持 Chrome DevTools**：远程调试和控制浏览器
- **多端支持**：Web 端和 Android 端自动化测试

## 🚀 快速开始

### 环境要求

- **Python**: >= 3.12
- **Node.js**: >= 18 (用于 Midscene Web 自动化)
- **操作系统**: macOS / Linux / Windows

### 1. 克隆项目

```bash
git clone https://github.com/albertwyjoy-bit/OKbot.git
cd OKbot
```

### 2. 创建 Conda 环境

```bash
conda create -n okbot python=3.12 -y
conda activate okbot
```

### 3. 安装依赖

```bash
# Python 依赖
pip install -e ".[dev]"

# 飞书 SDK（必需）
pip install lark-oapi

# Node.js 依赖
pnpm install
```

**注意**：对于 macOS 11.x 用户，esbuild 版本需锁定在 0.14.54（已在 package.json 中配置）

### 4. 飞书平台配置

#### 4.1 创建自建应用

1. 访问 [飞书开放平台](https://open.feishu.cn/app/) 并登录
2. 点击**创建应用** → 选择**企业自建应用**
3. 填写应用名称和描述，点击**创建**
4. 进入应用详情页，点击**凭证与基础信息**，获取 **App ID** 和 **App Secret**

#### 4.2 添加机器人能力

1. 在应用详情页，点击**添加能力与权限**
2. 找到**机器人**能力，点击**添加**
3. 设置机器人名称、头像和介绍

#### 4.3 配置必需权限

进入**权限管理** → **API 权限**，添加以下权限：

| 权限 | 说明 |
|------|------|
| `aily:message:write` | 发送消息 |
| `im:chat:readonly` | 获取群组信息 |
| `im:message` | 获取与发送单聊、群组消息 |
| `im:message.group_at_msg:readonly` | 接收群聊中@机器人消息事件 |
| `im:message.p2p_msg:readonly` | 读取用户发给机器人的单聊消息 |
| `im:message:readonly` | 获取单聊、群组消息 |
| `im:resource` | 获取与上传图片或文件资源 |

#### 4.4 配置事件订阅（⚠️ 关键步骤）

> **注意**：这是最容易被遗漏的配置！如果机器人能发送消息但无法接收消息，请检查此步骤。

1. 进入**事件与回调**页面
2. **订阅方式**：选择**长连接**（推荐）
3. 添加以下事件订阅：

| 事件 | 说明 |
|------|------|
| `im.message.receive_v1` | 接收消息（必需） |
| `im.message.message_read_v1` | 消息已读回执 |
| `im.chat.member.bot.added_v1` | 机器人被添加到群组 |
| `im.chat.member.bot.deleted_v1` | 机器人被移出群组 |

4. 点击**保存**，确认事件权限已申请

#### 4.5 发布应用

1. 进入**版本管理与发布**
2. 点击**创建版本**，填写版本号（如 1.0.0）和更新说明
3. 点击**保存并发布**

> **重要**：应用必须**发布**后，长连接才能正常建立。

#### 4.6 本地配置文件

创建 `~/.kimi/feishu.toml` 配置文件（可参考 `feishu.example.toml`）：

```toml
host = "127.0.0.1"
port = 18789
default_account = "bot"

[accounts.bot]
app_id = "cli_xxxxx"           # 替换为你的 App ID
app_secret = "xxxxxxxx"        # 替换为你的 App Secret
auto_approve = false
show_tool_calls = true
show_thinking = true
```

### 5. 安装 Midscene

本项目集成 [Midscene](https://midscenejs.com/zh/introduction.html) 实现 AI 驱动的 Web/Android 自动化测试。

**安装步骤**：

```bash
# 1. 安装 Midscene Web 依赖（已包含在 package.json 中）
pnpm install

# 2. 安装 Midscene Android MCP（全局安装）
npm install -g @midscene/android-mcp

# 3. 确保 Android SDK 环境变量已设置
export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
```

#### Chrome 桥接模式（推荐用于桌面浏览器自动化）

Midscene 支持通过 Chrome 插件实现桥接模式，无需额外安装 Playwright：

1. **安装 Chrome 插件**：
   - 下载 [Midscene Chrome 插件](https://chromewebstore.google.com/detail/midscene/gbldofopkkldkbgllfaodbaeadknajpa)
   - 或在 Chrome 应用商店搜索 "Midscene"

2. **启动桥接模式**：
   - 点击 Chrome 插件图标，选择 "Bridge Mode"
   - 或使用快捷键 `⇧ Shift + D` 快速启动

详细配置参考：https://midscenejs.com/zh/bridge-mode

**Midscene 文档参考**：https://midscenejs.com/zh/introduction.html

### 6. 配置 MCP 服务器

创建 `~/.kimi/mcp.json` 配置文件：

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    },
    "midscene-web": {
      "command": "npx",
      "args": ["-y", "@midscene/web-bridge-mcp"],
      "env": {
        "MIDSCENE_MODEL_BASE_URL": "https://api.example.com/v1",
        "MIDSCENE_MODEL_API_KEY": "your-api-key",
        "MIDSCENE_MODEL_NAME": "glm-4.6v",
        "MIDSCENE_MODEL_FAMILY": "glm-v",
        "MCP_SERVER_REQUEST_TIMEOUT": "600000"
      }
    },
    "midscene-android": {
      "command": "node",
      "args": ["$HOME/.nvm/versions/node/v22.22.0/lib/node_modules/@midscene/android-mcp/dist/index.js"],
      "env": {
        "MIDSCENE_MODEL_BASE_URL": "https://api.example.com/v1",
        "MIDSCENE_MODEL_API_KEY": "your-api-key",
        "MIDSCENE_MODEL_NAME": "glm-4.6v",
        "MIDSCENE_MODEL_FAMILY": "glm-v",
        "MCP_SERVER_REQUEST_TIMEOUT": "800000",
        "ANDROID_HOME": "$HOME/Library/Android/sdk",
        "PATH": "$HOME/Library/Android/sdk/platform-tools:/usr/local/bin:/usr/bin:/bin"
      }
    }
  }
}
```

**注意**：请将 `args` 中的路径修改为你实际的 `@midscene/android-mcp` 安装路径。

#### markitdown MCP 安装（可选）

如需使用文件格式转换功能，安装 markitdown-mcp：

```bash
# 创建独立的 markitdown 环境（推荐）
conda create -n markitdown python=3.12 -y
conda activate markitdown
pip install markitdown-mcp

# 然后在 mcp.json 中配置路径
# "command": "/path/to/anaconda3/envs/markitdown/bin/markitdown-mcp"
```

### 7. 启动服务

首次启动时会引导你完成 Kimi CLI 的登录验证：

```bash
# 使用启动脚本
./start-feishu.sh

# 或直接启动
python -m kimi_cli.feishu
```

首次运行时会显示登录链接，请在浏览器中完成授权。

### 8. 使用说明

#### Slash 命令

在飞书聊天中，支持以下 slash 命令：

| 命令 | 说明 |
|------|------|
| `/clear` | 清除当前会话上下文，开始新的对话 |
| `/mcp` | 查看当前可用的 MCP 工具列表 |
| `/help` | 显示帮助信息 |
| `/reset` | 重置当前会话（同 `/clear`） |

**注意**：所有 slash 命令会直接透传给 Kimi CLI 处理。

#### 文件传输

支持在飞书中直接发送文件和图片：
- 发送文件：机器人会下载并可以进一步处理
- 发送图片：机器人可以识别图片内容并回复
- 接收文件：机器人可以上传文件到飞书

## 🛠️ 开发指南

### 项目结构

```
OKbot/
├── src/kimi_cli/
│   ├── feishu/              # 飞书集成核心模块
│   │   ├── sdk_client.py    # 飞书 SDK 客户端
│   │   ├── sdk_server.py    # WebSocket 服务器
│   │   ├── config.py        # 配置管理
│   │   └── __main__.py      # 入口点
│   ├── cli/feishu.py        # CLI 命令
│   ├── tools/feishu/        # Feishu 工具
│   ├── auth/oauth.py        # OAuth 令牌管理
│   └── soul/toolset.py      # MCP 工具集
├── scripts/                 # 辅助脚本
├── docs/                    # 文档
└── tests/                   # 测试
```

### 常用命令

```bash
# 格式化代码
make format

# 运行检查
make check

# 运行测试
make test
```

## 🔧 高级配置

### 代理设置

```bash
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"
export NO_PROXY="localhost,127.0.0.1"
```

### 日志级别

```bash
export KIMI_LOG_LEVEL=DEBUG  # DEBUG, INFO, WARNING, ERROR
```

## 🐛 常见问题

### Q: 启动时报 "Connection refused" 错误
A: 检查飞书应用是否正确配置了事件订阅，确保 WebSocket 端口可访问。

### Q: MCP 工具名称冲突
A: 本项目已自动为 MCP 工具添加 `{server}__{tool}` 前缀，如 `midscene-web__Tap`。

### Q: OAuth 401 错误
A: 长对话中令牌可能过期，代码已自动处理刷新，如仍有问题请检查系统时间同步。

### Q: Midscene Android 连接失败
A: 请确保：
1. Android 设备已启用开发者模式和 USB 调试
2. `adb devices` 能识别到设备
3. `ANDROID_HOME` 环境变量已正确设置

## 📚 相关链接

- [Midscene 官方文档](https://midscenejs.com/zh/introduction.html)
- [飞书开放平台](https://open.feishu.cn/)
- [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli)

## 📝 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)

## 📄 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。

原始项目 [MoonshotAI/kimi-cli](https://github.com/MoonshotAI/kimi-cli) 版权归 Moonshot AI 所有。
