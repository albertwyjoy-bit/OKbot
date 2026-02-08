"""Entry point for running Feishu integration directly.

This module can be run directly:
    python -m kimi_cli.feishu

Or via the CLI:
    kimi feishu
"""

import asyncio
import sys

from kimi_cli.feishu import FeishuConfig, run_sdk_server


def main() -> int:
    """Main entry point.
    
    Runs Feishu integration using SDK mode (long connection/WebSocket).
    This is the recommended mode that doesn't require webhook URL or tunneling.
    
    Returns:
        Exit code: 0 for normal exit, non-zero for error.
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
        print("\n👋 Shutting down...")
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
