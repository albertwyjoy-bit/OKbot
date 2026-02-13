"""MCP 热更新功能测试."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from pathlib import Path
import json
import tempfile

from kimi_cli.soul.toolset import KimiToolset, MCPTool
from kimi_cli.soul.kimisoul import KimiSoul
from kimi_cli.soul.agent import Runtime


class TestMCPHotReload:
    """测试 MCP 热更新功能."""
    
    @pytest.fixture
    def toolset(self):
        return KimiToolset()
    
    @pytest.fixture
    def mock_runtime(self):
        runtime = MagicMock(spec=Runtime)
        runtime.config.mcp.client.tool_call_timeout_ms = 30000
        return runtime
    
    @pytest.mark.asyncio
    async def test_reload_mcp_tools_removes_existing(self, toolset, mock_runtime):
        """测试重载时正确移除现有 MCP 工具."""
        # Arrange: 添加一些现有工具
        mock_tool = MagicMock(spec=MCPTool)
        mock_tool.name = "test__tool1"
        toolset._tool_dict["test__tool1"] = mock_tool
        toolset._tool_dict["builtin_tool"] = MagicMock()  # 非 MCP 工具应保留
        
        # Act: 模拟重载（无新配置）
        with patch.object(toolset, 'cleanup', new_callable=AsyncMock):
            with patch.object(toolset, 'load_mcp_tools', new_callable=AsyncMock):
                with patch.object(toolset, 'wait_for_mcp_tools', new_callable=AsyncMock):
                    await toolset.reload_mcp_tools([], mock_runtime)
        
        # Assert: MCP 工具被移除，内置工具保留
        assert "test__tool1" not in toolset._tool_dict
        assert "builtin_tool" in toolset._tool_dict
    
    @pytest.mark.asyncio
    async def test_mcp_tool_name_isolation(self):
        """测试 MCP 工具名隔离（server__tool 格式）."""
        # Arrange: 模拟两个服务器的同名工具
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        mock_tool1 = MagicMock()
        mock_tool1.name = "Tap"
        mock_tool1.description = "Tool from server1"
        mock_tool1.inputSchema = {}
        
        mock_tool2 = MagicMock()
        mock_tool2.name = "Tap"  
        mock_tool2.description = "Tool from server2"
        mock_tool2.inputSchema = {}
        
        with patch('fastmcp.Client') as MockClient:
            MockClient.return_value = mock_client
            mock_client.list_tools = AsyncMock(side_effect=[[mock_tool1], [mock_tool2]])
            
            toolset = KimiToolset()
            mock_runtime = MagicMock()
            mock_runtime.config.mcp.client.tool_call_timeout_ms = 30000
            
            # Act: 加载两个 MCP 服务器
            from fastmcp.mcp_config import MCPConfig
            config1 = MCPConfig(mcpServers={"midscene-web": {"url": "http://localhost:3001"}})
            config2 = MCPConfig(mcpServers={"midscene-android": {"url": "http://localhost:3002"}})
            
            await toolset.load_mcp_tools([config1, config2], mock_runtime, in_background=False)
            await toolset.wait_for_mcp_tools()
            
            # Assert: 工具名被隔离
            assert "midscene-web__Tap" in toolset._tool_dict
            assert "midscene-android__Tap" in toolset._tool_dict
            assert len([t for t in toolset._tool_dict.values() if isinstance(t, MCPTool)]) == 2


class TestSkillsHotReload:
    """测试 Skills 热更新功能."""
    
    @pytest.mark.asyncio
    async def test_reload_skills_updates_system_prompt(self):
        """测试 Skills 重载更新 system prompt."""
        # Arrange: 模拟 KimiSoul
        mock_agent = MagicMock()
        mock_runtime = MagicMock()
        mock_agent.runtime = mock_runtime
        mock_runtime.skills = {}
        
        # 使用 __new__ 避免调用 __init__
        soul = object.__new__(KimiSoul)
        soul._agent = mock_agent
        soul._runtime = mock_runtime
        soul._context = MagicMock()
        
        # Act: 重载 skills
        with patch.object(mock_runtime, 'reload_skills', new_callable=AsyncMock, 
                         return_value=(3, "skill1, skill2, skill3")):
            count, skills_str = await soul.reload_skills()
            
            # Assert
            assert count == 3
            mock_agent.refresh_system_prompt.assert_called_once()


class TestSlashCommands:
    """测试热更新 Slash 命令."""
    
    @pytest.mark.asyncio
    async def test_update_mcp_command_exists(self):
        """测试 /update-mcp 命令存在."""
        from kimi_cli.soul.slash import registry
        
        # Assert
        assert "update-mcp" in registry._commands
        cmd = registry._commands["update-mcp"]
        assert cmd is not None
        
    @pytest.mark.asyncio
    async def test_update_skill_command_exists(self):
        """测试 /update-skill 命令存在."""
        from kimi_cli.soul.slash import registry
        
        # Assert
        assert "update-skill" in registry._commands
        cmd = registry._commands["update-skill"]
        assert cmd is not None
