from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from kosong.message import Message
from loguru import logger

import kimi_cli.prompts as prompts
from kimi_cli.soul import wire_send
from kimi_cli.soul.agent import load_agents_md
from kimi_cli.soul.context import Context
from kimi_cli.soul.message import system
from kimi_cli.utils.slashcmd import SlashCommandRegistry
from kimi_cli.wire.types import StatusUpdate, TextPart

if TYPE_CHECKING:
    from kimi_cli.soul.kimisoul import KimiSoul

type SoulSlashCmdFunc = Callable[[KimiSoul, str], None | Awaitable[None]]
"""
A function that runs as a KimiSoul-level slash command.

Raises:
    Any exception that can be raised by `Soul.run`.
"""

registry = SlashCommandRegistry[SoulSlashCmdFunc]()


@registry.command
async def init(soul: KimiSoul, args: str):
    """Analyze the codebase and generate an `AGENTS.md` file"""
    from kimi_cli.soul.kimisoul import KimiSoul

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_context = Context(file_backend=Path(temp_dir) / "context.jsonl")
        tmp_soul = KimiSoul(soul.agent, context=tmp_context)
        await tmp_soul.run(prompts.INIT)

    agents_md = await load_agents_md(soul.runtime.builtin_args.KIMI_WORK_DIR)
    system_message = system(
        "The user just ran `/init` slash command. "
        "The system has analyzed the codebase and generated an `AGENTS.md` file. "
        f"Latest AGENTS.md file content:\n{agents_md}"
    )
    await soul.context.append_message(Message(role="user", content=[system_message]))


@registry.command
async def compact(soul: KimiSoul, args: str):
    """Compact the context"""
    if soul.context.n_checkpoints == 0:
        wire_send(TextPart(text="The context is empty."))
        return

    logger.info("Running `/compact`")
    await soul.compact_context()
    wire_send(TextPart(text="The context has been compacted."))
    wire_send(StatusUpdate(context_usage=soul.status.context_usage))


@registry.command(aliases=["reset"])
async def clear(soul: KimiSoul, args: str):
    """Clear the context"""
    logger.info("Running `/clear`")
    await soul.context.clear()
    wire_send(TextPart(text="The context has been cleared."))
    wire_send(StatusUpdate(context_usage=soul.status.context_usage))


@registry.command
async def yolo(soul: KimiSoul, args: str):
    """Toggle YOLO mode (auto-approve all actions)"""
    if soul.runtime.approval.is_yolo():
        soul.runtime.approval.set_yolo(False)
        wire_send(TextPart(text="You only die once! Actions will require approval."))
    else:
        soul.runtime.approval.set_yolo(True)
        wire_send(TextPart(text="You only live once! All actions will be auto-approved."))


@registry.command(name="update-skill")
async def update_skill(soul: KimiSoul, args: str):
    """Reload skills from disk and update system prompt.
    
    Use this command after adding, removing, or modifying skills.
    The new skills will be available immediately via /skill:name commands.
    """
    logger.info("Running `/update-skill`")
    wire_send(TextPart(text="🔄 Reloading skills from disk..."))
    
    try:
        count, skills_formatted = await soul.reload_skills()
        
        # Build summary message
        skill_names = list(soul.runtime.skills.keys())
        skills_list = ", ".join(f"`{name}`" for name in skill_names[:10])
        if len(skill_names) > 10:
            skills_list += f", and {len(skill_names) - 10} more"
        
        message = f"""✅ Skills reloaded successfully!

**Loaded {count} skills:**
{skills_list}

**New skills are now available via:**
• `/skill:name` - Use a specific skill
• Direct mention in conversation

The system prompt has been updated with new skill metadata."""
        
        wire_send(TextPart(text=message))
        
    except Exception as e:
        logger.exception("Failed to reload skills")
        wire_send(TextPart(text=f"❌ Failed to reload skills: {e}"))


@registry.command(name="update-mcp")
async def update_mcp(soul: KimiSoul, args: str):
    """Reload MCP tools from global config file.
    
    Use this command after adding, removing, or modifying MCP servers in `~/.kimi/mcp.json`.
    This will disconnect existing MCP connections and reconnect with the new configuration.
    """
    logger.info("Running `/update-mcp`")
    wire_send(TextPart(text="🔄 Reloading MCP tools from config..."))
    
    try:
        servers_count, tools_count, server_names = await soul.reload_mcp()
        
        # Build summary message
        if servers_count == 0:
            message = """⚠️ No MCP servers connected.

Please check:
• `~/.kimi/mcp.json` exists and contains valid MCP server configurations
• Run `kimi mcp add` to add MCP servers
• Run `kimi mcp list` to see configured servers"""
        else:
            servers_list = ", ".join(f"`{name}`" for name in server_names[:5])
            if len(server_names) > 5:
                servers_list += f", and {len(server_names) - 5} more"
            
            message = f"""✅ MCP tools reloaded successfully!

**Connected {servers_count} servers ({tools_count} tools):**
{servers_list}

MCP tools are now available immediately.
Use `/mcp` to check server status."""
        
        wire_send(TextPart(text=message))
        
    except FileNotFoundError as e:
        wire_send(TextPart(text=f"""❌ MCP config file not found.

Please create `~/.kimi/mcp.json` first:

```bash
# Example: Add a stdio MCP server
kimi mcp add --transport stdio my-server -- npx my-mcp-server

# Or add an HTTP MCP server  
kimi mcp add --transport http my-api https://api.example.com/mcp
```

Error: {e}"""))
    except ValueError as e:
        wire_send(TextPart(text=f"""⚠️ No MCP servers configured.

Please add MCP servers first:

```bash
kimi mcp add --transport stdio chrome-devtools -- npx chrome-devtools-mcp@latest
kimi mcp list
```

Error: {e}"""))
    except Exception as e:
        logger.exception("Failed to reload MCP tools")
        wire_send(TextPart(text=f"❌ Failed to reload MCP tools: {type(e).__name__}: {e}"))
