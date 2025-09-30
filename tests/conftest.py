"""Test configuration and fixtures."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from kimi_cli.agent import BuiltinSystemPromptArgs
from kimi_cli.denwarenji import DenwaRenji
from kimi_cli.tools.bash import Bash
from kimi_cli.tools.dmail import SendDMail
from kimi_cli.tools.file.glob import Glob
from kimi_cli.tools.file.grep import Grep
from kimi_cli.tools.file.read import ReadFile
from kimi_cli.tools.file.replace import StrReplaceFile
from kimi_cli.tools.file.write import WriteFile
from kimi_cli.tools.todo import SetTodoList


@pytest.fixture
def temp_work_dir() -> Generator[Path]:
    """Create a temporary working directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def builtin_args(temp_work_dir: Path) -> BuiltinSystemPromptArgs:
    """Create builtin arguments with temporary work directory."""
    return BuiltinSystemPromptArgs(
        ENSOUL_WORK_DIR=temp_work_dir,
        ENSOUL_WORK_DIR_LS="Test ls content",
        ENSOUL_AGENTS_MD="Test agents content",
    )


@pytest.fixture
def denwa_renji() -> DenwaRenji:
    """Create a DenwaRenji instance."""
    return DenwaRenji()


@pytest.fixture
def send_dmail_tool(denwa_renji: DenwaRenji) -> SendDMail:
    """Create a SendDMail tool instance."""
    return SendDMail(denwa_renji)


@pytest.fixture
def set_todo_list_tool() -> SetTodoList:
    """Create a SetTodoList tool instance."""
    return SetTodoList()


@pytest.fixture
def bash_tool() -> Bash:
    """Create a Shell tool instance."""
    return Bash()


@pytest.fixture
def read_file_tool(builtin_args: BuiltinSystemPromptArgs) -> ReadFile:
    """Create a ReadFile tool instance."""
    return ReadFile(builtin_args)


@pytest.fixture
def glob_tool(builtin_args: BuiltinSystemPromptArgs) -> Glob:
    """Create a Glob tool instance."""
    return Glob(builtin_args)


@pytest.fixture
def grep_tool() -> Grep:
    """Create a Grep tool instance."""
    return Grep()


@pytest.fixture
def write_file_tool(builtin_args: BuiltinSystemPromptArgs) -> WriteFile:
    """Create a WriteFile tool instance."""
    return WriteFile(builtin_args)


@pytest.fixture
def str_replace_file_tool(builtin_args: BuiltinSystemPromptArgs) -> StrReplaceFile:
    """Create a StrReplaceFile tool instance."""
    return StrReplaceFile(builtin_args)
