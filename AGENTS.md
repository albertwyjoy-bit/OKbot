# AGENTS.md - OKbot Development Guide

> This file contains essential information for AI coding agents working on the OKbot project.
> OKbot is a Feishu (Lark) extension for Kimi Code CLI.

## Project Overview

**OKbot** is the Feishu (Lark) integration version of [Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli). It allows users to interact with Kimi CLI through Feishu messages, enabling seamless cross-device task continuation between CLI and mobile.

### Key Features
- **Feishu Integration**: WebSocket-based long connection for real-time messaging
- **Cross-device Session Continuation**: Switch between CLI and Feishu seamlessly
- **Scheduled Tasks**: Natural language cron job creation with AI execution
- **Memory System**: Long-term memory based on claude-mem architecture
- **Plan Mode**: Read-only analysis mode for safe planning
- **MCP Hot Reload**: Dynamic MCP server management without restart
- **Voice Messages**: GLM-ASR-2512 powered speech recognition
- **Image Generation**: Volcano Engine Ark API integration
- **Device Control**: PC browser (Chrome) and Android device manipulation

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.12+ |
| Package Manager | uv ( Astral's Python package manager) |
| Web Framework | FastAPI + Uvicorn |
| WebSocket | websockets library |
| Feishu SDK | lark-oapi |
| Task Scheduling | croniter |
| Voice Recognition | GLM-ASR-2512 |
| Frontend (Web UI) | React + TypeScript + Vite |
| Build | uv_build, PyInstaller (optional) |
| Nix Support | Full flake.nix configuration |

## Project Structure

```
OKbot/
├── src/kimi_cli/                 # Main source code
│   ├── feishu/                   # Feishu integration core
│   │   ├── sdk_client.py         # Feishu SDK client
│   │   ├── sdk_server.py         # WebSocket server implementation
│   │   ├── card_builder.py       # Interactive card builders
│   │   ├── config.py             # Configuration management
│   │   └── message_renderer.py   # Message formatting
│   ├── scheduler/                # Scheduled task system
│   │   ├── scheduler.py          # Core scheduler
│   │   ├── cron_engine.py        # Cron expression parsing
│   │   ├── session.py            # Silent task sessions
│   │   └── store.py              # Job persistence
│   ├── wire/                     # Wire protocol for ACP
│   │   ├── server.py             # Wire server implementation
│   │   ├── types.py              # Protocol types
│   │   └── jsonrpc.py            # JSON-RPC handling
│   ├── soul/                     # Core agent implementation
│   │   ├── kimisoul.py           # Main agent logic
│   │   ├── agent.py              # Base agent
│   │   └── approval.py           # Tool approval handling
│   ├── memory/                   # Long-term memory system
│   ├── tools/                    # Tool implementations
│   │   ├── file/                 # File operations
│   │   ├── web/                  # Web search/fetch
│   │   ├── shell/                # Shell execution
│   │   ├── feishu/               # Feishu-specific tools
│   │   └── scheduler_tool.py     # Task scheduling tool
│   ├── web/                      # Web UI backend
│   └── ui/                       # CLI UI components
├── packages/                     # Workspace packages
│   ├── kosong/                   # Core utility library
│   ├── kaos/                     # Agent OS primitives
│   └── kimi-code/                # Kimi Code package
├── sdks/kimi-sdk/                # Kimi SDK
├── web/                          # Web frontend (React/Vite)
├── tests/                        # Unit tests
│   ├── okbot/                    # Feishu integration tests
│   ├── scheduler/                # Scheduler tests
│   ├── core/                     # Core functionality tests
│   └── tools/                    # Tool tests
├── tests_e2e/                    # End-to-end tests
├── docs/                         # Documentation
├── klips/                        # OKbot Enhancement Proposals
└── .agents/skills/               # Project skills for AI
```

## Build Commands

### Setup (First Time)
```bash
# One-time installation script
./install.sh

# Or manual setup
conda create -n okbot python=3.12 -y
conda activate okbot
pip install -e ".[dev]"
pip install lark-oapi
```

### Development
```bash
# Sync dependencies
make prepare              # Full setup with git hooks
uv sync --frozen --all-extras --all-packages

# Web development
make web-back             # Start FastAPI backend (port 5494)
make web-front            # Start Vite dev server
```

### Code Quality
```bash
# Format all code
make format               # Format all workspace packages
make format-kimi-cli      # Format only main CLI

# Run checks
make check                # Lint and type check all
make check-kimi-cli       # Check only main CLI
```

### Testing
```bash
# Run all tests
make test                 # All tests across workspace
pytest tests -vv          # Unit tests only
pytest tests_e2e -vv      # E2E tests only

# Specific modules
make test-kimi-cli        # Main CLI tests
make test-kosong          # Kosong package tests
```

### Building
```bash
# Build packages
make build                # Build all Python packages
make build-web            # Build web UI
make build-bin            # PyInstaller binary (one-file)
make build-bin-onedir     # PyInstaller binary (one-dir)
```

## Running the Application

### Start Feishu Integration
```bash
# Method 1: Direct module
python -m kimi_cli.feishu

# Method 2: Via CLI
kimi feishu
```

### Configuration Required
Create `~/.kimi/feishu.toml` based on `feishu.example.toml`:

```toml
host = "127.0.0.1"
port = 18789
default_account = "bot"

[accounts.bot]
app_id = "cli_xxxxx"
app_secret = "xxxxxxxx"
auto_approve = true
```

Also ensure `~/.kimi/config.toml` has proper LLM provider configuration.

## Code Style Guidelines

### Python
- **Line Length**: 100 characters (ruff config)
- **Formatter**: ruff
- **Type Checker**: pyright (strict mode for src/kimi_cli/)
- **Import Style**: isort-compatible

### Ruff Rules Enabled
- `E`, `F`: pycodestyle, Pyflakes
- `UP`: pyupgrade
- `B`: flake8-bugbear
- `SIM`: flake8-simplify
- `I`: isort

### Type Checking
- Python 3.14 target for type checking
- Strict mode enabled for `src/kimi_cli/**/*.py`
- Standard mode for tests

### Frontend (Web)
- **Formatter**: Biome
- **Type Checker**: TypeScript
- Located in `web/` directory

## Testing Strategy

### Test Organization
- `tests/`: Unit tests organized by module
  - `okbot/`: Feishu integration tests
  - `scheduler/`: Scheduled task tests
  - `core/`: Core agent logic tests
  - `tools/`: Tool implementation tests
- `tests_e2e/`: End-to-end integration tests
- `tests_ai/`: AI-powered test suite

### Test Configuration
- Pytest with `asyncio_mode = auto`
- Fixtures in `tests/conftest.py`
- Use `pytest-asyncio` for async tests

### Writing Tests
```python
# Example async test
async def test_feishu_integration():
    client = FeishuSDKClient(...)
    result = await client.send_message(...)
    assert result.code == 0
```

## Key Architectural Patterns

### Feishu Integration
- Uses **WebSocket long connection** via Feishu SDK
- No webhook URL or tunneling required
- Event-driven architecture with `sdk_server.py`
- Interactive cards for approvals (`card_builder.py`)

### Session Management
- Sessions stored in `.kimi/sessions/{session_id}.json`
- Shared between CLI and Feishu (same directory structure)
- Cross-device continuation via `/continue <session_id>`

### Scheduler
- Cron-based scheduling with `croniter`
- Silent execution in independent sessions
- Queue-based notification delivery
- File auto-upload to Feishu

### Wire Protocol
- JSON-RPC based protocol for ACP (Agent Communication Protocol)
- Supports approvals, tool execution, file transfers
- Used for CLI and web UI communication

## Security Considerations

### Configuration Security
- API keys stored in `~/.kimi/config.toml` (user home)
- Feishu credentials in `~/.kimi/feishu.toml`
- Environment variables preferred for secrets (e.g., `ZHIPU_API_KEY`)

### Authorization Modes
1. **YOLO Mode** (default): Auto-approve all tool calls
2. **Interactive Mode**: Card-based approval for each tool

### Plan Mode
- Read-only mode for safe analysis
- Blocks all write operations and MCP tools
- Dedicated planning file: `~/.kimi/plans/{session_id}.md`

## MCP (Model Context Protocol)

### Configuration
MCP servers configured in `~/.kimi/config.toml`:

```toml
[[mcp.servers]]
name = "midscene-web"
type = "stdio"
command = "npx"
args = ["@midscene/web"]
```

### Hot Reload
- Use `/update-mcp` command to reload without restart
- Tools prefixed with `{server}__` to avoid conflicts

## Development Tips

### Adding New Feishu Commands
1. Add handler in `src/kimi_cli/feishu/sdk_server.py`
2. Update help text in card builders
3. Add tests in `tests/okbot/`

### Adding Scheduler Features
1. Update models in `scheduler/models.py`
2. Modify engine in `scheduler/cron_engine.py`
3. Update dispatcher in `scheduler/dispatcher.py`

### Adding Tools
1. Implement in `src/kimi_cli/tools/<category>/`
2. Register in `src/kimi_cli/tools/__init__.py`
3. Add tests in `tests/tools/`

## Common Commands Reference

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/plan` | Enter Plan Mode (read-only analysis) |
| `/stop` | Interrupt current operation |
| `/clear` | Clear context |
| `/new` | Create new session |
| `/model` | Switch model and thinking mode |
| `/yolo` | Toggle auto-approval mode |
| `/cron` | Manage scheduled tasks |
| `/sessions` | List CLI sessions |
| `/continue <id>` | Continue CLI session |
| `/mcp` | View MCP status |
| `/update-mcp` | Hot reload MCP config |
| `/update-skill` | Reload skills |

## Documentation

- `README.md`: Main project overview (Chinese)
- `docs/detailed-readme.md`: Detailed documentation
- `docs/memory.md`: Memory system documentation
- `docs/scheduler_file_handling.md`: Scheduler documentation
- `docs/voice-messages.md`: Voice feature documentation
- `klips/`: OKbot Enhancement Proposals

## License

Apache License 2.0 - Original [kimi-cli](https://github.com/MoonshotAI/kimi-cli) copyright belongs to Moonshot AI.
