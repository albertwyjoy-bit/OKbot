# Kimi Code CLI (OKbot - 飞书版)

## Quick commands (use uv)

- `make prepare` - Sync deps for all workspace packages and install git hooks
- `make format` - Auto-format all code (ruff for Python, biome for web)
- `make check` - Run linting and type checks
- `make test` - Run all test suites
- `make ai-test` - Run AI-powered test suite
- `make build` / `make build-bin` - Build Python packages or standalone binary
- `make web-back` / `make web-front` - Start web backend/frontend dev servers

If running tools directly, use `uv run ...`.

## Project overview

**OKbot** is a Feishu (Lark) extension of [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli), 
a Python CLI agent for software engineering workflows. It enables real-time interaction with 
Kimi through Feishu messages, supporting cross-device session continuity, scheduled tasks, 
MCP tool integration, and device control (PC browser and Android).

Key capabilities:
- **Plan Mode**: `/plan` enters read-only analysis mode for safe code review
- **Scheduled Tasks**: Natural language cron jobs with `scheduler/` module
- **Cross-device Sessions**: Seamlessly switch between CLI and Feishu
- **Dynamic Skills**: Hot-reloadable skill system under `.agents/skills/`
- **MCP Hot-swap**: Runtime MCP server management without restart
- **Memory System**: Long-term memory with embedding-based retrieval
- **Device Control**: Chrome browser automation and Android device control
- **Rich Media**: Image generation, voice messages (ASR), file attachments

## Tech stack

### Core (Python)
- **Python**: 3.12+ (tooling configured for 3.14)
- **CLI framework**: Typer
- **Async runtime**: asyncio
- **LLM framework**: kosong (workspace package)
- **MCP integration**: fastmcp
- **HTTP client**: aiohttp, httpx
- **Logging**: loguru
- **Config**: tomlkit, PyYAML
- **TUI**: prompt-toolkit, rich

### Web UI (TypeScript/React)
- **Framework**: React 19 + TypeScript
- **Build tool**: Vite
- **Styling**: Tailwind CSS 4
- **UI components**: Radix UI + shadcn/ui patterns
- **State**: React hooks + tanstack/react-table
- **Lint/Format**: Biome

### Package management
- **Python**: uv + uv_build
- **Node.js**: pnpm
- **Nix**: flake.nix for reproducible dev environments

### Testing & Quality
- **Tests**: pytest + pytest-asyncio
- **Lint/Format**: ruff (Python), biome (web)
- **Type checking**: pyright + ty (Python), tsc (TypeScript)
- **Git hooks**: pre-commit with custom hooks

### Distribution
- **Python packages**: uv build
- **Standalone binary**: PyInstaller (kimi.spec)

## Workspace structure

This is a uv workspace with multiple packages:

```
root (kimi-cli)
├── packages/
│   ├── kosong/          # LLM abstraction layer
│   │   └── src/kosong/
│   │       ├── chat_provider/    # Kimi, OpenAI, Anthropic, Google GenAI
│   │       ├── tooling/          # Tool orchestration
│   │       └── message.py        # Unified message types
│   ├── kaos/            # OS abstraction layer (PyKAOS)
│   │   └── src/kaos/
│   │       ├── local.py          # Local file/execution
│   │       └── ssh.py            # Remote via SSH
│   └── kimi-code/       # Wrapper package for distribution
├── sdks/
│   └── kimi-sdk/        # Lightweight SDK for Kimi API
└── web/                 # React-based web UI
    ├── src/
    ├── package.json
    └── vite.config.ts
```

## Architecture overview

### Entry points
- **CLI**: `src/kimi_cli/cli/__main__.py` → `cli.py` (Typer) → `app.py::KimiCLI`
- **Feishu bot**: `src/kimi_cli/feishu/__main__.py` → Feishu SDK client/server
- **Web**: `src/kimi_cli/web/app.py` → FastAPI + React frontend

### Core agent loop
1. **KimiCLI.create** (`app.py`): Load config, select provider, build Runtime
2. **KimiSoul.run** (`soul/kimisoul.py`): Main event loop
   - Parse slash commands (`soul/slash.py`)
   - Manage conversation Context (`soul/context.py`)
   - Call LLM via kosong
   - Execute tools via KimiToolset (`soul/toolset.py`)
   - Handle compaction (`soul/compaction.py`)
3. **Wire protocol** (`wire/`): Event streaming between soul and UI

### Key modules

#### Agent configuration (`agents/`)
- YAML specs define agent behavior
- `default/agent.yaml`: Default agent with standard tools
- `okabe/`: Alternative agent persona
- Tools specified by import path (e.g., `kimi_cli.tools.shell:Shell`)

#### Tools (`tools/`)
Built-in tools:
- `shell`: Command execution via PyKAOS
- `file`: Read, write, glob, grep, replace
- `web`: Search (DuckDuckGo) and fetch
- `multiagent`: Task spawner for subagents
- `todo`: Task list management
- `memory_tools`: Memory search and storage
- `image_generation`: Text-to-image via Ark API
- `think`: Reasoning tool

MCP tools are loaded dynamically via `fastmcp`.

#### Feishu integration (`feishu/`)
- `sdk_client.py`: Feishu OpenAPI client
- `sdk_server.py`: Webhook server for bot events
- `card_builder.py`: Interactive message cards
- `context.py`: Session management for cross-device continuity
- `message_renderer.py`: Rich content rendering
- `post_message.py`: Message posting utilities

#### Scheduler (`scheduler/`)
- Natural language cron expression parsing
- Task persistence with SQLite
- Integration with agent session for execution
- File-based task outputs

#### Memory system (`memory/`)
- Embedding-based retrieval (GLM/Kimi embedders)
- Observation storage with timestamps
- Timeline and semantic search

#### Wire protocol (`wire/`)
Event types for UI communication:
- `TurnBegin`/`TurnEnd`: Conversation boundaries
- `StepBegin`/`StepInterrupted`: Execution steps
- `TextDelta`/`ToolCallBegin`: Streaming content
- `ApprovalRequest`/`ApprovalComplete`: User approval flow

#### UI frontends (`ui/`)
- `shell/`: Interactive TUI (default)
- `print/`: Non-interactive output
- `acp/`: Agent Communication Protocol server

#### Skills (`skill/`, `.agents/skills/`)
- Standard skills: Load `SKILL.md` as user prompt (`/skill:name`)
- Flow skills: Execute parsed flow diagrams (`/flow:name`)
- Flow formats: Mermaid, D2

## Configuration

### User config
Location: `~/.kimi/config.toml`

Key sections:
- `model`: Default model and provider settings
- `approval`: Auto-approval settings (yolo mode)
- `feishu`: Feishu app credentials (for OKbot)
- `mcp`: MCP server configurations
- `memory`: Embedding provider settings

### Feishu config
Location: `~/.kimi/feishu.toml`

```toml
[app]
app_id = "cli_xxxxxxxxxxxxxxxx"
app_secret = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
encrypt_key = "your-encrypt-key"

[server]
port = 8080
webhook_path = "/webhook"
```

## Development workflow

### Setup
```bash
# Install deps and git hooks
make prepare

# Or manually:
uv sync --frozen --all-extras --all-packages
uv tool install prek && uv tool run prek install
```

### Development servers
```bash
# Web backend (FastAPI)
make web-back

# Web frontend (Vite)
make web-front

# Feishu bot
python -m kimi_cli.feishu
```

### Code quality
```bash
# Format all
make format

# Check all
make check

# Individual packages
make format-kimi-cli / make check-kimi-cli
make format-kosong / make check-kosong
make format-web / make check-web
```

### Testing
```bash
# All tests
make test

# Individual packages
make test-kimi-cli
make test-kosong
make test-pykaos
make test-kimi-sdk

# AI tests (requires API keys)
make ai-test
```

### Building
```bash
# Python packages
make build

# Standalone binary (one-file)
make build-bin

# Standalone binary (one-dir)
make build-bin-onedir

# Web UI only
make build-web
```

## Repo map

```
src/kimi_cli/
├── agents/              # Agent YAML specs and prompts
│   ├── default/         # Default agent
│   └── okabe/           # Alternative persona
├── cli/                 # CLI commands (Typer)
├── feishu/              # Feishu/Lark integration
├── memory/              # Long-term memory system
├── prompts/             # Shared prompt templates
├── scheduler/           # Cron task scheduler
├── skill/               # Skill framework + flows
├── soul/                # Core agent runtime
│   ├── agent.py         # Runtime, Agent, LaborMarket
│   ├── kimisoul.py      # Main agent loop
│   ├── context.py       # Conversation history
│   ├── toolset.py       # Tool loading/execution
│   ├── slash.py         # Slash commands
│   ├── approval.py      # User approval flow
│   └── compaction.py    # Context compression
├── tools/               # Built-in tools
├── ui/                  # UI frontends
├── utils/               # Utilities
├── web/                 # Web API (FastAPI)
└── wire/                # Event protocol

packages/
├── kosong/              # LLM abstraction
├── kaos/                # OS abstraction
└── kimi-code/           # Distribution wrapper

sdks/
└── kimi-sdk/            # Public SDK

web/                     # React web UI
.agents/skills/          # Built-in skills
tests/                   # Unit tests
tests_ai/                # AI-powered tests
tests_e2e/               # End-to-end tests
klips/                   # Improvement proposals (KLIP)
```

## Conventions and quality

### Python
- **Version**: >=3.12 (strict typing for 3.14)
- **Line length**: 100
- **Import style**: `from __future__ import annotations`
- **Type hints**: Required, strict mode with pyright

### Ruff rules
- `E` - pycodestyle
- `F` - Pyflakes
- `UP` - pyupgrade
- `B` - flake8-bugbear
- `SIM` - flake8-simplify
- `I` - isort

Per-file ignores:
- `tests/**/*.py`: `E501` (line too long in tests)
- `src/kimi_cli/web/api/**/*.py`: `B008` (FastAPI Depends)

### Testing
- Test files: `tests/test_*.py`
- Fixtures in `conftest.py`
- Async tests use `pytest-asyncio`
- Snapshots via `inline-snapshot`

### Git hooks
Pre-commit runs:
1. `make format-kimi-cli` - Auto-fix formatting
2. `make check-kimi-cli` - Lint and type check

## Git commit messages

Conventional Commits format:

```
<type>(<scope>): <subject>
```

Allowed types:
`feat`, `fix`, `test`, `refactor`, `chore`, `style`, `docs`, `perf`, `build`, `ci`, `revert`.

## Versioning

**Minor-bump-only** scheme (`MAJOR.MINOR.PATCH`):

- **Patch**: Always `0`, never bump
- **Minor**: Bump for any change (features, fixes, improvements)
- **Major**: Manual decision only

Examples: `0.68.0` → `0.69.0` → `0.70.0` (never `0.68.1`)

Applies to: root, `packages/*`, `sdks/*`

Current versions:
- `kimi-cli`: 1.9.0
- `kimi-code`: 1.12.0
- `kosong`: 0.42.0
- `pykaos`: 0.7.0
- `kimi-sdk`: 0.2.1

## Release workflow

1. Ensure `main` is up to date: `git pull origin main`
2. Create release branch: `git checkout -b bump-X.Y`
3. Update `CHANGELOG.md`: Rename `[Unreleased]` to `[X.Y] - YYYY-MM-DD`
4. Update `pyproject.toml` version
5. Run `uv sync` to align `uv.lock`
6. Commit and push: `git commit -m "chore: bump version to X.Y" && git push`
7. Open PR, get review, merge
8. Switch to `main` and pull: `git checkout main && git pull`
9. Tag and push:
   ```bash
   git tag X.Y.0          # or pykaos-X.Y.0 for packages
   git push --tags
   ```
10. GitHub Actions handles the release

## CI/CD

GitHub Actions workflows (`.github/workflows/`):

- `ci-kimi-cli.yml` - Test and check on PR/push
- `ci-kosong.yml`, `ci-pykaos.yml`, `ci-kimi-sdk.yml` - Package CI
- `release-kimi-cli.yml` - Release builds and PyPI upload
- `release-*.yml` - Package releases
- `typos.yml` - Spell checking
- `pr-title-checker.yml` - Conventional Commits validation

## Nix support

For Nix users, a `flake.nix` provides:
- `nix build` - Build kimi-cli package
- `nix develop` - Enter dev shell with all dependencies
- Uses uv2nix for Python package management

## Security considerations

- OAuth tokens stored in system keyring via `keyring` library
- Feishu encrypt_key for webhook verification
- MCP server configs in user config (not repo)
- Shell tool has approval requirements (configurable)
- File operations respect `.gitignore` patterns

## Common slash commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/plan` | Enter Plan Mode (read-only analysis) |
| `/stop` | Interrupt current operation |
| `/clear` | Clear conversation context |
| `/new` | Create new session |
| `/model` | Switch model and Thinking mode |
| `/yolo` | Toggle auto-approval |
| `/cron` | Manage scheduled tasks |
| `/sessions` | List CLI sessions |
| `/continue <id>` | Resume session |
| `/mcp` | View MCP status |
| `/skill:<name>` | Load skill |
| `/flow:<name>` | Execute flow |
