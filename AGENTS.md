# OKbot - AI Coding Agent Guide

## Project Overview

OKbot is a Feishu (Lark) integration extension for [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli), allowing users to interact with Kimi CLI through Feishu messages. It extends the original Kimi Code CLI with:

- **Feishu Integration**: Full SDK-based WebSocket connection (no webhook/tunnel needed)
- **Cross-platform Session Continuation**: Seamlessly switch between CLI and Feishu
- **Scheduler**: Natural language cron-based job scheduling
- **Voice Messages**: Zhipu AI ASR integration for voice recognition
- **Device Control**: PC browser (Chrome) and Android device control via MCP
- **Hot Reload**: Dynamic Skills and MCP server configuration updates without restart

**Project Structure**:
```
OKbot/
├── src/kimi_cli/           # Main CLI package
│   ├── feishu/             # Feishu integration core (SDK client/server)
│   ├── scheduler/          # Cron-based job scheduling system
│   ├── web/                # Web UI backend (FastAPI)
│   ├── soul/               # Core agent runtime
│   ├── tools/              # Built-in tools
│   ├── ui/                 # UI implementations (shell/print/acp)
│   └── agents/             # Agent specifications
├── packages/               # Workspace packages
│   ├── kosong/             # LLM abstraction layer
│   ├── kaos/               # OS abstraction (local/SSH)
│   └── kimi-code/          # Meta package wrapper
├── sdks/kimi-sdk/          # Kimi API SDK
├── web/                    # Web UI frontend (React + Vite)
├── .agents/skills/         # Built-in skills
├── tests/                  # Test suite
├── docs/                   # Documentation
└── install.sh              # One-click installation script
```

## Technology Stack

- **Language**: Python 3.12+ (configured for 3.14 type checking)
- **CLI Framework**: Typer
- **Async Runtime**: asyncio
- **LLM Framework**: kosong (custom abstraction)
- **Web Backend**: FastAPI + uvicorn + websockets
- **Web Frontend**: React + TypeScript + Vite + Tailwind CSS
- **MCP Integration**: fastmcp
- **Logging**: loguru
- **Package Management**: uv + uv_build
- **Binary Build**: PyInstaller
- **Testing**: pytest + pytest-asyncio
- **Linting**: ruff (E, F, UP, B, SIM, I rules)
- **Type Checking**: pyright + ty

## Build and Development Commands

**Setup**:
```bash
# Full setup with dependencies and git hooks
make prepare

# Install pre-commit hooks only
make install-prek
```

**Development Servers** (for Web UI):
```bash
# Start web backend (with reload)
make web-back

# Start web frontend (Vite dev server)
make web-front
```

**Code Quality**:
```bash
# Format all code
make format
make format-kimi-cli
make format-kosong
make format-pykaos
make format-kimi-sdk
make format-web

# Run checks (ruff + pyright + ty)
make check
make check-kimi-cli
make check-kosong
make check-pykaos
make check-kimi-sdk
make check-web
```

**Testing**:
```bash
# Run all tests
make test

# Run specific package tests
make test-kimi-cli    # Includes tests/ and tests_e2e/
make test-kosong      # Includes doctests
make test-pykaos
make test-kimi-sdk
```

**Building**:
```bash
# Build Python packages for release
make build
make build-kimi-cli
make build-kosong
make build-pykaos
make build-kimi-sdk

# Build web UI
make build-web

# Build standalone binaries
make build-bin          # One-file mode
make build-bin-onedir   # One-directory mode
```

**AI-powered Tasks**:
```bash
# Run AI test suite
make ai-test

# Generate changelog
make gen-changelog

# Generate user docs
make gen-docs
```

## Code Style Guidelines

- **Line Length**: 100 characters maximum
- **Import Style**: Use `from __future__ import annotations` in all files
- **Type Hints**: Strict type checking enabled; all functions must be typed
- **Docstrings**: Google style preferred
- **Async**: Prefer async/await for I/O operations

**Ruff Rules**:
- `E` - pycodestyle
- `F` - Pyflakes
- `UP` - pyupgrade
- `B` - flake8-bugbear
- `SIM` - flake8-simplify
- `I` - isort

**Per-file Ignores**:
- Tests: `E501` (line too long)
- FastAPI files: `B008` (function call in default argument - for Depends())

## Project Architecture

### Core Runtime (`src/kimi_cli/soul/`)

The agent runtime is built around these key components:

1. **KimiSoul** (`kimisoul.py`): Main agent loop that processes user input, calls LLM, executes tools
2. **Runtime** (`agent.py`): Container for config, OAuth, LLM, session, skills, and environment
3. **Context** (`context.py`): Conversation history with checkpoint support for DMail
4. **Toolset** (`toolset.py`): Tool loading and execution, MCP integration
5. **Approval** (`approval.py`): User approval mediation for tool actions
6. **Compaction** (`compaction.py`): Context window management

### Feishu Integration (`src/kimi_cli/feishu/`)

- **SDK Client** (`sdk_client.py`): Feishu SDK wrapper for API calls
- **SDK Server** (`sdk_server.py`): WebSocket event handling and message routing
- **Card Builder** (`card_builder.py`): Interactive Feishu card generation
- **Message Renderer** (`message_renderer.py`): Message formatting for Feishu
- **Config** (`config.py`): Feishu-specific configuration

Key features:
- Uses Feishu SDK long connection (WebSocket) - no webhook/tunnel needed
- Supports text, image, file, and voice messages
- Interactive cards for approvals, model switching, etc.
- Session continuation between CLI and Feishu

### Scheduler (`src/kimi_cli/scheduler/`)

A cron-based job scheduling system:

- **Scheduler** (`scheduler.py`): Main scheduler logic
- **CronEngine** (`cron_engine.py`): Cron expression parsing and execution
- **Dispatcher** (`dispatcher.py`): Message dispatching and session management
- **Session** (`session.py`): Scheduled task session handling
- **Store** (`store.py`): Job and result persistence
- **Models** (`models.py`): Data models for jobs and notifications

Features:
- Natural language cron expression parsing
- Silent task execution with independent sessions
- Queue-based notification delivery
- File generation and delivery support

### Tools (`src/kimi_cli/tools/`)

Built-in tool categories:

- **file/**: File operations (read, write, replace, glob, grep)
- **shell/**: Shell command execution (bash, PowerShell)
- **web/**: Web operations (search, fetch)
- **multiagent/**: Subagent spawning and management
- **feishu/**: Feishu-specific tools
- **scheduler_tool.py**: Scheduler integration tools
- **todo/**: Todo list management
- **think/**: Reasoning tool
- **dmail/**: Checkpointed reply system

### UI Layer (`src/kimi_cli/ui/`)

Multiple UI implementations:

- **shell/**: Interactive TUI (default) with autocomplete and slash commands
- **print/**: Non-interactive print mode for scripts
- **acp/**: Agent Communication Protocol server mode

### Agent Specifications (`src/kimi_cli/agents/`)

Agent configs in YAML format:

- **default/**: Default agent with full toolset
- **okabe/**: Alternative agent configuration

Spec format includes: name, system prompt path, tools list, subagents definition.

### Workspace Packages

- **kosong**: LLM abstraction layer with provider unification
- **pykaos**: OS abstraction for local and remote (SSH) operations
- **kimi-sdk**: Lightweight SDK for Kimi API

## Testing Strategy

**Test Organization**:
- `tests/`: Unit and integration tests
- `tests_e2e/`: End-to-end tests
- `tests_ai/`: AI-powered test suite

**Test Configuration**:
- pytest with asyncio_mode = auto
- Uses pytest-asyncio for async test support
- inline-snapshot for snapshot testing

**Running Tests**:
```bash
# All tests
pytest tests -vv
pytest tests_e2e -vv

# With coverage (if configured)
pytest tests --cov=kimi_cli
```

## Configuration

### User Configuration (`~/.kimi/config.toml`)

Main config file for Kimi CLI with providers, models, and settings.

### Feishu Configuration (`~/.kimi/feishu.toml`)

```toml
host = "127.0.0.1"
port = 18789
default_account = "bot"

[accounts.bot]
app_id = "cli_xxxxx"
app_secret = "xxxxxxxx"
auto_approve = true
```

### Environment Variables

- `KIMI_BASE_URL`: Override API base URL
- `KIMI_API_KEY`: Override API key
- `KIMI_MODEL_NAME`: Override model name
- `ZHIPU_API_KEY`: For ASR voice recognition

## Deployment

### PyInstaller Binary

```bash
make build-bin         # Single executable
make build-bin-onedir  # Directory mode (faster startup)
```

### Python Package

```bash
make build
# Distributes: kimi-cli, kimi-code, kosong, pykaos, kimi-sdk
```

### Nix

```bash
nix build .#kimi-cli
```

### Feishu Server Startup

```bash
python -m kimi_cli.feishu
```

Or use the convenience command:
```bash
kimi feishu
```

## Security Considerations

- **OAuth Token Management**: Automatic refresh via OAuthManager
- **Credential Storage**: Uses system keyring for secure storage
- **Approval System**: YOLO mode (auto-approve) can be toggled per account
- **Access Control**: Configurable allowed_users and allowed_chats in Feishu config
- **Webhook Security**: Optional encrypt_key and verification_token for Feishu webhooks

## Versioning

**Minor-bump-only scheme** (`MAJOR.MINOR.PATCH`):
- Patch is always `0`
- Minor is bumped for all changes (features, fixes, etc.)
- Major only changed by explicit decision

Example: `1.9.0` → `1.10.0` → `1.11.0`

## Git Conventions

**Commit Message Format** (Conventional Commits):
```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `test`, `refactor`, `chore`, `style`, `docs`, `perf`, `build`, `ci`, `revert`

## Skills System

Skills are reusable capabilities in `.agents/skills/`:

- Each skill has a `SKILL.md` file with instructions
- Skills auto-discovered from user home and project directories
- Hot-reloadable without restart

**Available Skills** (23 built-in):
- algorithmic-art, android-app-pilot, canvas-design, codex-worker
- doc-coauthoring, docx, frontend-design, gen-changelog
- gen-docs, gen-rust, kimi-cli-help, mac-filesearch, mcp-builder
- pdf, pptx, pull-request, release, skill-creator
- theme-factory, translate-docs, web-artifacts-builder
- worktree-status, xlsx

## MCP Integration

- MCP servers configured in `~/.kimi/mcp.json`
- Hot-reload supported via `/update-mcp` command
- Tool name prefixing: `{server}__{tool}` to avoid conflicts
- Supports both local stdio and remote SSE transports

## Development Notes

1. **First-time Setup**: Run `make prepare` after cloning
2. **Pre-commit Hooks**: Automatically run format and check
3. **Type Checking**: pyright runs in strict mode for src/kimi_cli
4. **Async Patterns**: All I/O should be async; use kaos for file operations
5. **Session Management**: Sessions stored in `.kimi/sessions/` under work_dir
6. **Logging**: Logs to `~/.kimi/logs/kimi.log` with rotation

## Common Tasks

**Add a new tool**:
1. Create module in `src/kimi_cli/tools/<category>/`
2. Implement function with `@tool` decorator from kosong
3. Add to agent spec tools list

**Add a new skill**:
1. Create directory in `.agents/skills/<skill-name>/`
2. Write `SKILL.md` with instructions
3. No restart needed - skills auto-discovered

**Add Feishu message handler**:
1. Extend `sdk_server.py` event handlers
2. Use `card_builder.py` for interactive UI
3. Update message renderer for formatting

**Debug Feishu integration**:
```bash
LOG_LEVEL=DEBUG python -m kimi_cli.feishu
```
