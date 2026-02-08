"""Feishu integration CLI commands."""

from __future__ import annotations

from typing import Annotated

import typer

cli = typer.Typer(help="Feishu (Lark) integration for remote access.")


@cli.callback(invoke_without_command=True)
def feishu(
    ctx: typer.Context,
    host: Annotated[
        str | None,
        typer.Option("--host", "-h", help="Gateway host address"),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", "-p", help="Gateway port"),
    ] = None,
    work_dir: Annotated[
        str | None,
        typer.Option("--work-dir", "-w", help="Working directory for Feishu sessions (default: current directory)"),
    ] = None,
):
    """Run Feishu integration server using SDK long connection.
    
    Uses WebSocket long connection to receive events directly from Feishu.
    No webhook URL needed, no tunnel tools required.
    
    Examples:
        # Start the server
        kimi feishu
        
        # Start with custom host/port
        kimi feishu --host 0.0.0.0 --port 18888
        
        # Start with specific working directory
        kimi feishu --work-dir /path/to/workspace
        
        # Or change directory first
        cd /path/to/workspace && kimi feishu
    """
    if ctx.invoked_subcommand is not None:
        return
    
    import asyncio
    
    from kimi_cli.feishu import FeishuConfig
    from kimi_cli.feishu.sdk_server import run_sdk_server
    
    config = FeishuConfig.load()
    
    if host:
        config.host = host
    if port:
        config.port = port
    if work_dir:
        config.work_dir = work_dir
    
    print("🔌 Starting Feishu integration server (SDK long connection)...")
    asyncio.run(run_sdk_server(config))


@cli.command()
def config(
    list_accounts: Annotated[
        bool,
        typer.Option("--list", "-l", help="List configured accounts"),
    ] = False,
    add_account: Annotated[
        str | None,
        typer.Option("--add", "-a", help="Add a new account with the given name"),
    ] = None,
    remove_account: Annotated[
        str | None,
        typer.Option("--remove", "-r", help="Remove an account"),
    ] = None,
    # For non-interactive configuration
    app_id: Annotated[
        str | None,
        typer.Option("--app-id", help="Feishu App ID"),
    ] = None,
    app_secret: Annotated[
        str | None,
        typer.Option("--app-secret", help="Feishu App Secret"),
    ] = None,
    auto_approve: Annotated[
        bool,
        typer.Option("--auto-approve/--no-auto-approve", help="Auto-approve tool calls"),
    ] = False,
    show_tool_calls: Annotated[
        bool,
        typer.Option("--show-tool-calls/--no-show-tool-calls", help="Show tool calls in messages"),
    ] = True,
    show_thinking: Annotated[
        bool,
        typer.Option("--show-thinking/--no-show-thinking", help="Show thinking process"),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing account without confirmation"),
    ] = False,
):
    """Configure Feishu integration.
    
    Examples:
        # List accounts
        kimi feishu config --list
        
        # Add account interactively
        kimi feishu config --add mybot
        
        # Add account non-interactively
        kimi feishu config --add mybot --app-id cli_xxx --app-secret xxx
        
        # Remove account
        kimi feishu config --remove mybot
    """
    from kimi_cli.feishu import FeishuConfig, FeishuAccountConfig
    
    feishu_config = FeishuConfig.load()
    
    if list_accounts:
        if not feishu_config.accounts:
            print("No accounts configured.")
            return
        
        print("Configured Feishu accounts:")
        for name, account in feishu_config.accounts.items():
            default_marker = " (default)" if name == feishu_config.default_account else ""
            print(f"  • {name}{default_marker}")
            print(f"    App ID: {account.app_id[:10]}...")
            print(f"    Auto-approve: {account.auto_approve}")
            print(f"    Show tool calls: {account.show_tool_calls}")
        return
    
    if remove_account:
        if remove_account not in feishu_config.accounts:
            print(f"Account '{remove_account}' not found.")
            raise typer.Exit(1)
        
        del feishu_config.accounts[remove_account]
        if feishu_config.default_account == remove_account:
            feishu_config.default_account = None
        
        feishu_config.save()
        print(f"Account '{remove_account}' removed.")
        return
    
    if add_account:
        is_update = add_account in feishu_config.accounts
        
        if is_update and not force:
            print(f"Account '{add_account}' already exists. Use --force to overwrite.")
            raise typer.Exit(1)
        
        # Check if we have all required params for non-interactive mode
        if app_id and app_secret:
            # Non-interactive mode
            account_config = FeishuAccountConfig(
                app_id=app_id,
                app_secret=app_secret,
                auto_approve=auto_approve,
                show_tool_calls=show_tool_calls,
                show_thinking=show_thinking,
            )
        else:
            # Interactive mode - check if stdin is available
            import sys
            if not sys.stdin.isatty():
                print("Error: --app-id and --app-secret are required in non-interactive mode")
                print("Example: kimi feishu config --add mybot --app-id cli_xxx --app-secret xxx")
                raise typer.Exit(1)
            
            # Interactive prompts
            print(f"\nConfiguring Feishu account: {add_account}")
            print("You'll need your Feishu app credentials.")
            print("Get them from: https://open.feishu.cn/app/\n")
            
            app_id_input = typer.prompt("App ID") if app_id is None else app_id
            app_secret_input = typer.prompt("App Secret", hide_input=True) if app_secret is None else app_secret
            
            auto_approve_input = typer.confirm("Auto-approve tool calls?", default=auto_approve)
            show_tool_calls_input = typer.confirm("Show tool calls in messages?", default=show_tool_calls)
            show_thinking_input = typer.confirm("Show thinking process?", default=show_thinking)
            
            account_config = FeishuAccountConfig(
                app_id=app_id_input,
                app_secret=app_secret_input,
                auto_approve=auto_approve_input,
                show_tool_calls=show_tool_calls_input,
                show_thinking=show_thinking_input,
            )
        
        feishu_config.accounts[add_account] = account_config
        
        if feishu_config.default_account is None:
            feishu_config.default_account = add_account
        
        feishu_config.save()
        action = "updated" if is_update else "configured"
        print(f"\n✅ Account '{add_account}' {action} successfully!")
        print(f"\nNext steps:")
        print(f"  1. In Feishu Developer Console:")
        print(f"     - Go to: https://open.feishu.cn/app/{app_id or app_id_input}/event/subscribe")
        print(f"     - Set 'Event subscription mode' to 'Long connection'")
        print(f"     - Add events: im.message.receive_v1")
        print(f"  2. Run 'kimi feishu' to start the server")
        return
    
    # No flags provided, show help
    print("Usage:")
    print("  kimi feishu config --list")
    print("  kimi feishu config --add <name> --app-id <id> --app-secret <secret>")
    print("  kimi feishu config --remove <name>")


@cli.command()
def setup():
    """Interactive setup guide for Feishu integration."""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║           Feishu (Lark) Integration Setup Guide                   ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                   ║
║  This guide will help you set up Feishu integration using the    ║
║  SDK mode (long connection).                                      ║
║                                                                   ║
║  Benefits of SDK mode:                                            ║
║  ✅ No webhook URL needed                                        ║
║  ✅ No tunnel/穿透 tools required                                ║
║  ✅ Direct WebSocket connection to Feishu                        ║
║  ✅ More stable and secure                                       ║
║                                                                   ║
╠══════════════════════════════════════════════════════════════════╣
║  Step 1: Create a Feishu App                                     ║
║  ─────────────────────────────────────────────────────────────    ║
║  1. Go to: https://open.feishu.cn/app/                           ║
║  2. Click "Create Custom App"                                    ║
║  3. Fill in app name and description                             ║
║  4. Choose "Deploy as enterprise app"                            ║
║                                                                   ║
║  Step 2: Get App Credentials                                     ║
║  ─────────────────────────────────────────────────────────────    ║
║  1. In your app page, go to "Credentials & Basic Info"           ║
║  2. Copy the "App ID" and "App Secret"                           ║
║                                                                   ║
║  Step 3: Enable Bot Capability                                   ║
║  ─────────────────────────────────────────────────────────────    ║
║  1. Go to "Add features" → "Bot"                                 ║
║  2. Enable the bot                                               ║
║  3. Add bot name and description                                 ║
║                                                                   ║
║  Step 4: Subscribe to Events                                     ║
║  ─────────────────────────────────────────────────────────────    ║
║  1. Go to "Event Subscriptions"                                  ║
║  2. Set "Subscription mode" to "Long connection"                 ║
║  3. Add these events:                                            ║
║     • im.message.receive_v1 (Receive message)                    ║
║     • im.chat.member.bot.added_v1 (Bot added to chat)            ║
║     • im.chat.access_event_v1 (P2P chat created)                 ║
║                                                                   ║
║  Step 5: Configure Permissions                                   ║
║  ─────────────────────────────────────────────────────────────    ║
║  1. Go to "Permissions" → "API Permissions"                      ║
║  2. Add these permissions:                                       ║
║     • im:chat:readonly (Read chat info)                          ║
║     • im:message:send (Send messages)                            ║
║     • im:message.group_msg (Send group messages)                 ║
║                                                                   ║
║  Step 6: Publish App                                             ║
║  ─────────────────────────────────────────────────────────────    ║
║  1. Go to "Version Management" → "Create Version"                ║
║  2. Fill in version info and submit for approval                 ║
║  3. After approval, the app will be available                    ║
║                                                                   ║
║  Step 7: Configure kimi-cli                                      ║
║  ─────────────────────────────────────────────────────────────    ║
║  Run the following command with your credentials:                ║
║                                                                   ║
║    kimi feishu config --add <name> \\                          ║
║      --app-id <your-app-id> \\                                ║
║      --app-secret <your-app-secret>                             ║
║                                                                   ║
║  Step 8: Start the Server                                        ║
║  ─────────────────────────────────────────────────────────────    ║
║    kimi feishu                                                    ║
║                                                                   ║
║  The server will start and connect to Feishu via WebSocket.      ║
║  No additional configuration needed!                              ║
║                                                                   ║
╚══════════════════════════════════════════════════════════════════╝
    """)
