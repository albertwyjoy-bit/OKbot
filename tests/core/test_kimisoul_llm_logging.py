from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from kosong.message import Message
from kimi_cli.soul.kimisoul import KimiSoul


def _make_soul(tmp_path: Path) -> KimiSoul:
    soul = object.__new__(KimiSoul)
    soul._runtime = SimpleNamespace(session=SimpleNamespace(work_dir=tmp_path))
    return soul


def test_llm_message_logging_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("KIMI_CLI_LOG_LLM_MESSAGES", raising=False)

    soul = _make_soul(tmp_path)

    assert soul._should_log_llm_messages() is False


def test_llm_message_logging_enabled_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("KIMI_CLI_LOG_LLM_MESSAGES", "1")

    soul = _make_soul(tmp_path)

    assert soul._should_log_llm_messages() is True


@pytest.mark.asyncio
async def test_log_llm_messages_respects_toggle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("KIMI_CLI_LOG_LLM_MESSAGES", raising=False)

    soul = _make_soul(tmp_path)
    await soul._log_llm_messages(
        "system",
        [Message(role="user", content="hello")],
    )
    assert not (tmp_path / "llm.log").exists()

    monkeypatch.setenv("KIMI_CLI_LOG_LLM_MESSAGES", "1")
    await soul._log_llm_messages(
        "system",
        [Message(role="user", content="hello")],
    )
    assert (tmp_path / "llm.log").exists()
