"""
Tests for the Memory System
"""

import asyncio
import os
import tempfile
from datetime import datetime
import pytest

from kimi_cli.memory import (
    MemoryAgent,
    ObservationInput,
    ObservationType,
    SummaryInput,
    SearchFilters,
    format_observation_for_embedding,
    format_summary_for_embedding,
)
from kimi_cli.memory.schema import MemoryDatabase


class TestMemoryDatabase:
    """Tests for MemoryDatabase (SQLite backend)"""

    def test_create_observation(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = MemoryDatabase(f.name)
            
            obs_input = ObservationInput(
                session_id="test-session-001",
                type=ObservationType.BUGFIX,
                title="Fixed authentication bug",
                subtitle="Token validation was failing",
                facts=["JWT token expired", "Added refresh logic"],
                narrative="The authentication system was rejecting valid tokens...",
                concepts=["auth", "jwt", "token"],
                files_modified=["src/auth.py"],
                tool_name="StrReplaceFile",
                prompt_number=1,
            )
            
            obs_id = db.insert_observation(obs_input, embedding=None)
            assert obs_id > 0
            
            obs = db.get_observation(obs_id)
            assert obs is not None
            assert obs.session_id == "test-session-001"
            assert obs.type == ObservationType.BUGFIX
            assert obs.title == "Fixed authentication bug"
            assert obs.concepts == ["auth", "jwt", "token"]

    def test_create_summary(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = MemoryDatabase(f.name)
            
            summary_input = SummaryInput(
                session_id="test-session-001",
                request="Fix login bug",
                investigated="Found issue in token validation",
                learned="JWT needs refresh mechanism",
                completed="Fixed auth.py",
                next_steps="Test the fix",
                prompt_number=1,
            )
            
            summary_id = db.insert_summary(summary_input, embedding=None)
            assert summary_id > 0
            
            summary = db.get_summary(summary_id)
            assert summary is not None
            assert summary.session_id == "test-session-001"
            assert summary.request == "Fix login bug"
            assert summary.completed == "Fixed auth.py"

    def test_get_by_session(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = MemoryDatabase(f.name)
            
            # Insert multiple observations
            for i in range(3):
                obs = ObservationInput(
                    session_id="session-001",
                    type=ObservationType.CHANGE,
                    title=f"Change {i}",
                    prompt_number=i,
                )
                db.insert_observation(obs)
            
            # Insert for different session
            db.insert_observation(ObservationInput(
                session_id="session-002",
                type=ObservationType.CHANGE,
                title="Other session",
            ))
            
            results = db.get_observations_by_session("session-001")
            assert len(results) == 3

    def test_metadata_filter(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db = MemoryDatabase(f.name)
            
            # Insert various types
            db.insert_observation(ObservationInput(
                session_id="session-001",
                type=ObservationType.BUGFIX,
                title="Bug fix",
                concepts=["auth"],
            ))
            db.insert_observation(ObservationInput(
                session_id="session-001",
                type=ObservationType.FEATURE,
                title="New feature",
                concepts=["api"],
            ))
            db.insert_observation(ObservationInput(
                session_id="session-002",
                type=ObservationType.BUGFIX,
                title="Other bug",
            ))
            
            # Filter by type
            ids = db.metadata_filter_observations(types=["bugfix"])
            assert len(ids) == 2
            
            # Filter by session and type
            ids = db.metadata_filter_observations(
                session_id="session-001",
                types=["bugfix"]
            )
            assert len(ids) == 1


class TestObservationExtraction:
    """Tests for observation extraction from tool results"""

    def test_format_observation(self):
        obs = ObservationInput(
            session_id="test",
            type=ObservationType.BUGFIX,
            title="Fix auth",
            subtitle="JWT issue",
            facts=["Token expired", "Refresh added"],
            narrative="Details here",
            concepts=["auth", "jwt"],
            files_modified=["auth.py"],
        )
        
        text = format_observation_for_embedding(obs)
        assert "Fix auth" in text
        assert "JWT issue" in text
        assert "Token expired" in text
        assert "auth.py" in text

    def test_format_summary(self):
        summary = SummaryInput(
            session_id="test",
            request="Fix bug",
            completed="Fixed auth.py",
            learned="JWT needs refresh",
        )
        
        text = format_summary_for_embedding(summary)
        assert "Fix bug" in text
        assert "Fixed auth.py" in text
        assert "JWT needs refresh" in text


class TestMemoryAgent:
    """Tests for MemoryAgent"""

    @pytest.mark.asyncio
    async def test_agent_lifecycle(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            agent = MemoryAgent.create(
                db_path=f.name,
                embedding_provider="kimi",
            )
            
            # Start
            await agent.start()
            assert agent._initialized
            
            # Queue observation (fire and forget) - don't wait for embedding
            obs_input = ObservationInput(
                session_id="test-session",
                type=ObservationType.DISCOVERY,
                title="Test observation",
            )
            # Don't wait - embedding will fail without API key
            await agent.queue_observation(obs_input, wait=False)
            
            # Queue summary (don't wait to avoid timeout without API key)
            summary_input = SummaryInput(
                session_id="test-session",
                request="Test request",
                completed="Test completed",
            )
            # Don't wait - embedding will fail without API key
            await agent.queue_summary(summary_input, wait=False)
            
            # Give a moment for queue processing
            await asyncio.sleep(0.1)
            
            # Stop
            await agent.stop()
            assert not agent._initialized

    @pytest.mark.asyncio
    async def test_get_stats(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            agent = MemoryAgent.create(db_path=f.name)
            await agent.start()
            
            stats = agent.get_stats()
            assert "total_observations" in stats
            assert "total_summaries" in stats
            assert "queue_size" in stats
            
            await agent.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
