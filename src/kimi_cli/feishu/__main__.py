"""Entry point for running Feishu integration directly.

This module can be run directly:
    python -m kimi_cli.feishu

Or via the CLI:
    kimi feishu

The server supports auto-restart when /restart command is received.
Just use `python -m kimi_cli.feishu` - no need for external scripts!
"""

import asyncio
import os
import sys
import time

from kimi_cli.feishu import FeishuConfig, run_sdk_server
from kimi_cli.feishu.sdk_server import RESTART_EXIT_CODE

# Maximum number of restart attempts
MAX_RESTARTS = 10
# Delay between restarts (seconds)
RESTART_DELAY = 3


def main() -> int:
    """Main entry point with auto-restart support.
    
    Runs Feishu integration using SDK mode (long connection/WebSocket).
    This is the recommended mode that doesn't require webhook URL or tunneling.
    
    Supports automatic restart when /restart command is received.
    Just use `python -m kimi_cli.feishu` - no external script needed!
    
    Returns:
        Exit code: 0 for normal exit, non-zero for error.
    """
    config = FeishuConfig.load()
    
    if not config.accounts:
        print("No Feishu accounts configured.")
        print("Please run: kimi feishu config --add <account-name>")
        print("\nFor setup guide: kimi feishu setup")
        return 1
    
    restart_count = 0
    
    while restart_count < MAX_RESTARTS:
        restart_count += 1
        
        if restart_count > 1:
            print(f"\n{'='*50}")
            print(f"🔄 Restart attempt {restart_count}/{MAX_RESTARTS}")
            print(f"{'='*50}\n")
            time.sleep(RESTART_DELAY)
        
        try:
            print("🔌 Starting Feishu integration in SDK mode (long connection)...")
            print("   No webhook URL needed! Events received via WebSocket.")
            print("   Send /restart to reload after code changes.\n")
            
            exit_code = asyncio.run(run_sdk_server(config))
            
            # Check if restart was requested
            if exit_code == RESTART_EXIT_CODE:
                print(f"\n🔄 Restart requested (attempt {restart_count}/{MAX_RESTARTS})")
                continue  # Restart the loop
            
            # Normal exit or error
            return exit_code
            
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            return 0
        except Exception as e:
            print(f"\n❌ Error: {e}")
            return 1
    
    print(f"\n⚠️ Maximum restart attempts ({MAX_RESTARTS}) reached. Giving up.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
