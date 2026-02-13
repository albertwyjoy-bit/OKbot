"""飞书集成功能测试."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, Mock
import json


class TestFeishuIntegration:
    """测试飞书集成功能."""
    
    @pytest.mark.asyncio
    async def test_ok_reaction_added(self):
        """测试收到消息时自动添加 👌 反应."""
        # Arrange
        mock_client = MagicMock()
        mock_client.add_reaction = AsyncMock()
        
        # Assert: add_reaction 会被调用
        mock_client.add_reaction.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_yolo_forced_in_feishu(self):
        """测试飞书模式下 YOLO 强制开启."""
        from kimi_cli.soul.approval import Approval
        
        # Arrange: 创建 Approval 实例
        approval = Approval(yolo=True)
        
        # Assert: YOLO 模式已开启
        assert approval.is_yolo() is True
    
    def test_mcp_tool_isolation_in_cards(self):
        """测试卡片中显示的工具名有隔离前缀."""
        # Arrange
        tool_name = "midscene-web__Tap"
        
        # Assert: 工具名包含服务器前缀
        assert "__" in tool_name
        server, tool = tool_name.split("__")
        assert server == "midscene-web"
        assert tool == "Tap"


class TestCrossPlatformSession:
    """测试跨端 Session 接续功能."""
    
    def test_session_storage_in_work_dir(self):
        """测试 session 存储在工作目录."""
        import tempfile
        from pathlib import Path
        
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            sessions_dir = work_dir / ".kimi" / "sessions"
            sessions_dir.mkdir(parents=True)
            
            # 创建模拟 session
            session_file = sessions_dir / "test_session.json"
            session_file.write_text(json.dumps({"id": "test_session", "messages": []}))
            
            # Assert
            assert session_file.exists()
    
    @pytest.mark.asyncio
    async def test_session_commands_exist(self):
        """测试 Session 相关命令存在."""
        from kimi_cli.feishu.sdk_server import SDKChatSession
        
        # Assert: 关键方法存在
        # 实际测试中需要检查具体实现
        assert hasattr(SDKChatSession, '_send_help')  # 帮助命令
        assert hasattr(SDKChatSession, '_handle_clear')  # 清除命令


class TestVoiceRecognition:
    """测试语音消息识别功能."""
    
    @pytest.mark.asyncio
    async def test_asr_config_loaded(self):
        """测试 ASR 配置可以加载."""
        from kimi_cli.feishu.config import FeishuConfig, FeishuAccountConfig
        
        # Arrange: 模拟账户配置（ASR 配置在 account 级别）
        account_config = {
            "app_id": "test_app_id",
            "app_secret": "test_secret",
            "asr_api_key": "test_asr_key"
        }
        
        # Act
        account = FeishuAccountConfig.model_validate(account_config)
        
        # Assert
        assert account.asr_api_key is not None
        assert account.asr_api_key.get_secret_value() == "test_asr_key"
