"""MCP resource lock for scheduled tasks.

Simple resource locking based on MCP server names.
No configuration needed - system internally knows which MCP servers require exclusive access.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from kimi_cli.soul.toolset import KimiToolset


@dataclass(frozen=True)
class MCPResourceLock:
    """Resource lock for a specific MCP server."""
    server_name: str


class MCPResourceLockManager:
    """Manages exclusive locks for MCP servers.
    
    System internally maintains a list of MCP servers that require exclusive access.
    When a scheduled task is about to execute, it checks which MCP tools it might use
    and acquires locks accordingly.
    
    Built-in exclusive MCP servers:
    - midscene-web: Browser automation (shares lock with puppeteer, playwright)
    - midscene-android: Android device control
    - puppeteer: Browser automation (shares lock with midscene-web)
    - playwright: Browser automation (shares lock with midscene-web)
    """
    
    # System-defined exclusive MCP server groups
    # Servers in the same group share the same lock
    EXCLUSIVE_GROUPS: dict[str, list[str]] = {
        "browser": [
            "midscene-web",
            "puppeteer", 
            "playwright",
            "selenium",
            "chrome",
        ],
        "android": [
            "midscene-android",
            "adb",
            "uiautomator",
            "appium-android",
        ],
        "ios": [
            "midscene-ios",
            "xcode",
            "appium-ios",
        ],
    }
    
    def __init__(self):
        """Initialize MCP resource lock manager."""
        self._locks: dict[str, asyncio.Lock] = {}
        self._server_to_group: dict[str, str] = {}
        
        # Build server -> group mapping
        for group_name, servers in self.EXCLUSIVE_GROUPS.items():
            self._locks[group_name] = asyncio.Lock()
            for server in servers:
                self._server_to_group[server.lower()] = group_name
    
    def get_lock_group(self, server_name: str) -> str | None:
        """Get the lock group for an MCP server.
        
        Args:
            server_name: MCP server name (e.g., "midscene-web")
            
        Returns:
            Lock group name or None if not exclusive
        """
        # Direct match
        group = self._server_to_group.get(server_name.lower())
        if group:
            return group
        
        # Partial match (e.g., "midscene-web__Tap" -> "midscene-web")
        for server, grp in self._server_to_group.items():
            if server_name.lower().startswith(server):
                return grp
        
        return None
    
    def is_exclusive_server(self, server_name: str) -> bool:
        """Check if an MCP server requires exclusive access.
        
        Args:
            server_name: MCP server name or tool name (e.g., "midscene-web" or "midscene-web__Tap")
            
        Returns:
            True if exclusive access required
        """
        return self.get_lock_group(server_name) is not None
    
    def detect_required_locks_from_toolset(self, toolset: KimiToolset) -> list[str]:
        """Detect which lock groups are needed based on available MCP tools.
        
        This is called when a scheduled task starts executing and we know
        which MCP tools are available in the toolset.
        
        Args:
            toolset: The toolset available to the task
            
        Returns:
            List of lock group names that need to be acquired
        """
        from kimi_cli.soul.toolset import MCPTool
        
        required_groups: set[str] = set()
        
        # Get all MCP tools from toolset
        for tool in toolset.tools:
            if isinstance(tool, MCPTool):
                server_name = getattr(tool, '_server_name', '')
                group = self.get_lock_group(server_name)
                if group:
                    required_groups.add(group)
                    logger.debug(f"Detected exclusive MCP server '{server_name}' -> group '{group}'")
        
        return sorted(required_groups)
    
    async def acquire(self, groups: list[str]) -> None:
        """Acquire locks for the given groups (in sorted order to avoid deadlock).
        
        Args:
            groups: List of lock group names
        """
        sorted_groups = sorted(set(groups))
        
        for group in sorted_groups:
            if group in self._locks:
                logger.debug(f"Acquiring MCP resource lock for group: {group}")
                await self._locks[group].acquire()
                logger.info(f"Acquired MCP resource lock for group: {group}")
    
    def release(self, groups: list[str]) -> None:
        """Release locks for the given groups.
        
        Args:
            groups: List of lock group names
        """
        # Release in reverse order
        sorted_groups = sorted(set(groups), reverse=True)
        
        for group in sorted_groups:
            if group in self._locks:
                lock = self._locks[group]
                if lock.locked():
                    lock.release()
                    logger.info(f"Released MCP resource lock for group: {group}")
    
    def get_lock_stats(self) -> dict[str, bool]:
        """Get current lock status for all groups.
        
        Returns:
            Dict mapping group name to locked status
        """
        return {
            group: lock.locked() 
            for group, lock in self._locks.items()
        }


# Global instance
_mcp_lock_manager: MCPResourceLockManager | None = None


def get_mcp_lock_manager() -> MCPResourceLockManager:
    """Get global MCP lock manager instance."""
    global _mcp_lock_manager
    if _mcp_lock_manager is None:
        _mcp_lock_manager = MCPResourceLockManager()
    return _mcp_lock_manager
