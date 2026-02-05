"""Feishu (Lark) integration for Kimi Code CLI.

This module provides integration with Feishu (Lark), allowing users to
interact with Kimi Code CLI through Feishu messages.

Uses SDK long connection (WebSocket) to receive events.
- No webhook URL needed
- No tunnel/穿透 tools required
- Direct connection to Feishu servers

Examples:
    # Start the server
    from kimi_cli.feishu import run_sdk_server
    asyncio.run(run_sdk_server())
"""

from __future__ import annotations

__all__ = [
    # Configuration
    "FeishuConfig",
    "FeishuAccountConfig",
    # SDK Client
    "FeishuSDKClient",
    # Server
    "FeishuSDKServer",
    "run_sdk_server",
]

# Configuration
from kimi_cli.feishu.config import FeishuConfig, FeishuAccountConfig

# SDK Client
from kimi_cli.feishu.sdk_client import FeishuSDKClient

# Server
from kimi_cli.feishu.sdk_server import FeishuSDKServer, run_sdk_server
