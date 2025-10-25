import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from inline_snapshot import snapshot

from kimi_cli.agentspec import DEFAULT_AGENT_FILE, load_agent_spec


@pytest.fixture
def agent_file_extending() -> Generator[Path, Any, Any]:
    """Create an agent configuration file that extends another."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create base agent
        base_agent = tmpdir / "base.yaml"
        base_agent.write_text("""
version: 1
agent:
  name: "Base Agent"
  system_prompt_path: ./system.md
  tools: ["kimi_cli.tools.think:Think"]
""")

        # Create system.md
        system_md = tmpdir / "system.md"
        system_md.write_text("Base system prompt")

        # Create extending agent
        extending_agent = tmpdir / "extending.yaml"
        extending_agent.write_text("""
version: 1
agent:
  extend: ./base.yaml
  name: "Extended Agent"
  system_prompt_args:
    CUSTOM_ARG: "custom_value"
""")

        yield extending_agent


def test_load_agent_spec_extension(agent_file_extending: Path):
    """Test loading agent spec with extension."""
    spec = load_agent_spec(agent_file_extending)

    assert spec.name == "Extended Agent"
    assert spec.tools == ["kimi_cli.tools.think:Think"]


def test_load_agent_spec_default_extension():
    """Test loading agent spec with default extension."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create extending agent
        extending_agent = tmpdir / "extending.yaml"
        extending_agent.write_text("""
version: 1
agent:
  extend: default
  system_prompt_args:
    CUSTOM_ARG: "custom_value"
  exclude_tools:
    - "kimi_cli.tools.web:SearchWeb"
    - "kimi_cli.tools.web:FetchURL"
""")

        spec = load_agent_spec(extending_agent)

        assert spec.name == snapshot("")
        assert spec.system_prompt_path == DEFAULT_AGENT_FILE.parent / "system.md"
        assert spec.system_prompt_args == snapshot(
            {"ROLE_ADDITIONAL": "", "CUSTOM_ARG": "custom_value"}
        )
        assert spec.tools == snapshot(
            [
                "kimi_cli.tools.task:Task",
                "kimi_cli.tools.think:Think",
                "kimi_cli.tools.todo:SetTodoList",
                "kimi_cli.tools.bash:Bash",
                "kimi_cli.tools.file:ReadFile",
                "kimi_cli.tools.file:Glob",
                "kimi_cli.tools.file:Grep",
                "kimi_cli.tools.file:WriteFile",
                "kimi_cli.tools.file:StrReplaceFile",
                "kimi_cli.tools.web:SearchWeb",
                "kimi_cli.tools.web:FetchURL",
            ]
        )
        assert spec.exclude_tools == snapshot(
            ["kimi_cli.tools.web:SearchWeb", "kimi_cli.tools.web:FetchURL"]
        )
        assert spec.subagents is not None
        assert "koder" in spec.subagents


def test_load_agent_spec_unsupported_version():
    """Test loading agent spec with unsupported version raises ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        agent_yaml = tmpdir / "agent.yaml"
        agent_yaml.write_text("""
version: 2
agent:
  name: "Test Agent"
  system_prompt_path: ./system.md
  tools: ["kimi_cli.tools.think:Think"]
""")

        with pytest.raises(ValueError, match="Unsupported agent spec version: 2"):
            load_agent_spec(agent_yaml)


def test_load_agent_spec_nonexistent_file():
    """Test loading nonexistent agent spec file raises AssertionError."""
    nonexistent = Path("/nonexistent/agent.yaml")
    with pytest.raises(AssertionError, match="expect agent file to exist"):
        load_agent_spec(nonexistent)
