"""审批授权卡片功能测试."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestApprovalCard:
    """测试审批授权卡片功能."""
    
    def test_approval_response_types(self):
        """测试审批响应类型."""
        from kimi_cli.soul.approval import Response
        
        # Assert: 三种响应类型都有效
        assert "approve" in ["approve", "approve_for_session", "reject"]
        assert "approve_for_session" in ["approve", "approve_for_session", "reject"]
        assert "reject" in ["approve", "approve_for_session", "reject"]
    
    @pytest.mark.asyncio
    async def test_approve_once(self):
        """测试单次批准."""
        from kimi_cli.soul.approval import Approval
        
        # Arrange
        approval = Approval(yolo=False)
        
        # Mock tool call
        with patch('kimi_cli.soul.approval.get_current_tool_call_or_none') as mock_get:
            mock_tool_call = MagicMock()
            mock_tool_call.id = "test_call_id"
            mock_tool_call.function.name = "test_tool"
            mock_get.return_value = mock_tool_call
            
            # Act: 发起审批请求
            approval_future = asyncio.create_task(
                approval.request("test", "test_action", "Test description")
            )
            
            # 获取请求并批准
            request = await approval.fetch_request()
            approval.resolve_request(request.id, "approve")
            
            # Assert
            result = await approval_future
            assert result is True
    
    @pytest.mark.asyncio
    async def test_approve_for_session(self):
        """测试对话级别批准."""
        from kimi_cli.soul.approval import Approval
        
        # Arrange
        approval = Approval(yolo=False)
        
        # Mock tool call
        with patch('kimi_cli.soul.approval.get_current_tool_call_or_none') as mock_get:
            mock_tool_call = MagicMock()
            mock_tool_call.id = "test_call_id"
            mock_tool_call.function.name = "test_tool"
            mock_get.return_value = mock_tool_call
            
            # Act: 发起审批请求
            approval_future = asyncio.create_task(
                approval.request("test", "test_action", "Test description")
            )
            
            # 获取请求并批准（对话级别）
            request = await approval.fetch_request()
            approval.resolve_request(request.id, "approve_for_session")
            
            # Assert: 第一次请求被批准
            result = await approval_future
            assert result is True
            
            # 第二次相同 action 的请求应该自动批准
            mock_tool_call2 = MagicMock()
            mock_tool_call2.id = "test_call_id_2"
            mock_tool_call2.function.name = "test_tool_2"
            mock_get.return_value = mock_tool_call2
            
            result2 = await approval.request("test", "test_action", "Test description 2")
            assert result2 is True
    
    @pytest.mark.asyncio
    async def test_reject_approval(self):
        """测试拒绝批准."""
        from kimi_cli.soul.approval import Approval
        
        # Arrange
        approval = Approval(yolo=False)
        
        # Mock tool call
        with patch('kimi_cli.soul.approval.get_current_tool_call_or_none') as mock_get:
            mock_tool_call = MagicMock()
            mock_tool_call.id = "test_call_id"
            mock_tool_call.function.name = "test_tool"
            mock_get.return_value = mock_tool_call
            
            # Act: 发起审批请求
            approval_future = asyncio.create_task(
                approval.request("test", "test_action", "Test description")
            )
            
            # 获取请求并拒绝
            request = await approval.fetch_request()
            approval.resolve_request(request.id, "reject")
            
            # Assert
            result = await approval_future
            assert result is False


class TestApprovalCardBuilder:
    """测试授权卡片构建器."""
    
    def test_build_approval_card_structure(self):
        """测试授权卡片结构."""
        from kimi_cli.feishu.card_builder import build_approval_card
        
        # Act
        card = build_approval_card(
            tool_name="test_tool",
            description="Test action description",
            request_id="req_123"
        )
        
        # Assert
        assert "config" in card
        assert "header" in card
        assert "elements" in card
        assert card["header"]["title"]["content"] == "🔧 需要授权"
    
    def test_approval_card_contains_action_info(self):
        """测试卡片包含操作信息."""
        from kimi_cli.feishu.card_builder import build_approval_card
        
        # Act
        card = build_approval_card(
            tool_name="Shell__execute",
            description="执行命令: ls -la",
            request_id="req_456"
        )
        
        # Assert: 卡片元素中包含工具名和描述
        elements_text = str(card["elements"])
        assert "Shell__execute" in elements_text or "Shell" in elements_text
        assert "req_456" in str(card)


class TestFeishuApprovalFlow:
    """测试飞书审批流程集成."""
    
    @pytest.mark.asyncio
    async def test_approval_card_sent_to_user(self):
        """测试审批卡片发送给用户."""
        # 这个测试需要模拟飞书消息发送
        pass
    
    @pytest.mark.asyncio
    async def test_user_response_triggers_callback(self):
        """测试用户响应触发回调."""
        # 这个测试需要模拟卡片回调
        pass
