"""Entry point for running Feishu integration directly.

This module can be run directly:
    python -m kimi_cli.feishu

Or via the CLI:
    kimi feishu
"""

import asyncio
import sys

from kimi_cli.feishu import FeishuConfig, run_sdk_server
from kimi_cli.feishu.sdk_server import RESTART_EXIT_CODE


def main() -> int:
    """Main entry point.
    
    Runs Feishu integration using SDK mode (long connection/WebSocket).
    This is the recommended mode that doesn't require webhook URL or tunneling.
    
    Returns:
        Exit code: 0 for normal exit, RESTART_EXIT_CODE (42) for restart requested.
    """
    config = FeishuConfig.load()
    
    if not config.accounts:
        print("No Feishu accounts configured.")
        print("Please run: kimi feishu config --add <account-name>")
        print("\nFor setup guide: kimi feishu setup")
        return 1
    
    try:
        print("🔌 Starting Feishu integration in SDK mode (long connection)...")
        print("   No webhook URL needed! Events received via WebSocket.\n")
        exit_code = asyncio.run(run_sdk_server(config))
        return exit_code
    except KeyboardInterrupt:
        print("\nShutting down...")
        return 0


if __name__ == "__main__":
    sys.exit(main())
