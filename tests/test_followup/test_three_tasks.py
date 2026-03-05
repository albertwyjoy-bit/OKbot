"""Test three concurrent background tasks - Iran, US, Israel war news search."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from kimi_cli.soul.followup import TaskManager, TaskStatus, SubagentTask


class TestThreeConcurrentTasks:
    """Test case simulating: 
    '创建三个后台任务来分别深度搜索伊朗、美国、以色列关于战争的报道'"""

    @pytest.fixture(autouse=True)
    def reset_task_manager(self):
        """Reset TaskManager singleton before each test."""
        TaskManager._instance = None
        yield
        TaskManager._instance = None

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        agent = MagicMock()
        agent.name = 'searcher'
        agent.runtime = MagicMock()
        agent.runtime.session.id = 'test-session'
        agent.runtime.config = MagicMock()
        agent.runtime.config.loop_control = MagicMock()
        agent.runtime.config.loop_control.max_steps_per_turn = 10
        agent.runtime.config.loop_control.max_retries_per_step = 3
        agent.runtime.config.loop_control.max_ralph_iterations = 0
        agent.runtime.config.loop_control.reserved_context_size = 1000
        agent.toolset = MagicMock()
        agent.toolset.tools = []
        return agent

    @pytest.mark.asyncio
    async def test_three_concurrent_tasks(self, mock_agent):
        """Test running three background tasks concurrently."""
        
        task_manager = TaskManager()
        session_id = 'test-session'
        
        # 模拟三个搜索任务的 run_soul 函数
        async def iran_search(soul, prompt, ui_loop, cancel_event):
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart
            
            # 模拟搜索步骤
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='Step 1: Searching Iran war news...')]
            ))
            await asyncio.sleep(0.05)
            
            await soul.context.append_message(Message(
                role='tool',
                content=[TextPart(text='Found 15 articles about Iran conflict')],
                tool_call_id='call_iran_1'
            ))
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='Analysis: Iran-Israel tensions escalated in 2024...')]
            ))
            
            # 最终结果
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='FINAL: Iran situation - High tension, regional conflict ongoing')]
            ))
        
        async def us_search(soul, prompt, ui_loop, cancel_event):
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='Step 1: Searching US war involvement...')]
            ))
            await asyncio.sleep(0.08)  # 稍微慢一点
            
            await soul.context.append_message(Message(
                role='tool',
                content=[TextPart(text='Found 20 articles about US military actions')],
                tool_call_id='call_us_1'
            ))
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='FINAL: US situation - Active military presence, diplomatic efforts ongoing')]
            ))
        
        async def israel_search(soul, prompt, ui_loop, cancel_event):
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='Step 1: Searching Israel conflict news...')]
            ))
            await asyncio.sleep(0.03)  # 最快完成
            
            await soul.context.append_message(Message(
                role='tool',
                content=[TextPart(text='Found 12 articles about Israel-Gaza conflict')],
                tool_call_id='call_isr_1'
            ))
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='FINAL: Israel situation - Gaza conflict continues, international pressure')]
            ))
        
        # 创建三个任务
        tasks = []
        countries = [
            ('Iran', iran_search),
            ('US', us_search),
            ('Israel', israel_search),
        ]
        
        for country, run_fn in countries:
            task = SubagentTask(
                session_id=session_id,
                description=f'Search {country} war news',
                subagent_name='searcher',
                agent=mock_agent,
                prompt=f'Search for {country} war news',
            )
            await task.run_in_background(run_soul_fn=run_fn)
            task_manager.add_task(session_id, task)
            tasks.append((country, task))
        
        print(f"\nStarted 3 background tasks:")
        for country, task in tasks:
            print(f"  - {country}: {task.task_id} (status: {task.status.value})")
        
        # 等待所有任务完成
        await asyncio.gather(*[t[1].wait() for t in tasks])
        
        print(f"\nAll tasks completed!")
        
        # 验证每个任务的状态和输出
        for country, task in tasks:
            print(f"\n{'='*60}")
            print(f"Task: {country}")
            print(f"Task ID: {task.task_id}")
            print(f"Status: {task.status.value}")
            print(f"Log file: {task.output_file}")
            print(f"File exists: {task.output_file.exists()}")
            
            if task.output_file.exists():
                content = task.output_file.read_text()
                lines = content.strip().split('\n')
                print(f"File size: {len(content)} chars, {len(lines)} lines")
                print(f"\nContent preview:")
                for i, line in enumerate(lines[:5], 1):
                    print(f"  {i}. {line[:80]}...")
                if len(lines) > 5:
                    print(f"  ... ({len(lines)-5} more lines)")
                
                # 验证关键内容
                assert 'FINAL' in content, f"{country} task should have FINAL result"
                assert 'Step 1' in content, f"{country} task should have intermediate steps"
        
        # 验证所有任务文件都存在（任务完成后会从 TaskManager 移除，这是预期行为）
        print(f"\n{'='*60}")
        print(f"Summary: 3 tasks completed and removed from TaskManager")
        
        # 任务完成后会自动从 TaskManager 移除，所以 list_tasks 返回空
        # 但我们可以验证文件都存在
        for country, task in tasks:
            assert task.output_file.exists(), f"{country} task output file should exist"
            assert task.status == TaskStatus.COMPLETED, f"{country} task should be completed"

    @pytest.mark.asyncio
    async def test_task_output_shows_full_content(self, mock_agent):
        """Test that TaskOutput shows full content including intermediate steps."""
        from kimi_cli.tools.multiagent.task_management import TaskOutput, TaskOutputParams
        
        task_manager = TaskManager()
        session_id = 'test-session'
        
        # 创建一个任务并手动写入复杂输出
        async def complex_task(soul, prompt, ui_loop, cancel_event):
            from kosong.message import Message
            from kimi_cli.wire.types import TextPart
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='=== PHASE 1: Data Collection ===')]
            ))
            await soul.context.append_message(Message(
                role='tool',
                content=[TextPart(text='Collected 50 data points')],
                tool_call_id='call_1'
            ))
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='=== PHASE 2: Analysis ===')]
            ))
            await soul.context.append_message(Message(
                role='tool',
                content=[TextPart(text='Analysis complete: 3 key findings')],
                tool_call_id='call_2'
            ))
            
            await soul.context.append_message(Message(
                role='assistant',
                content=[TextPart(text='=== FINAL REPORT ===\nComplete analysis with all findings')]
            ))
        
        task = SubagentTask(
            session_id=session_id,
            description='Complex analysis',
            subagent_name='analyzer',
            agent=mock_agent,
            prompt='Do complex analysis',
        )
        
        await task.run_in_background(run_soul_fn=complex_task)
        task_manager.add_task(session_id, task)
        await task.wait()
        
        # 使用 TaskOutput 工具查询
        mock_runtime = MagicMock()
        mock_runtime.session.id = session_id
        
        tool = TaskOutput(mock_runtime)
        params = TaskOutputParams(task_id=task.task_id)
        
        result = await tool(params)
        
        print(f"\nTaskOutput result:")
        print('=' * 60)
        print(result.output)
        print('=' * 60)
        
        # 验证输出包含完整内容
        assert 'PHASE 1' in result.output, "Should show Phase 1"
        assert 'PHASE 2' in result.output, "Should show Phase 2"
        assert 'FINAL REPORT' in result.output, "Should show final report"
        assert '50 data points' in result.output, "Should show tool results"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
