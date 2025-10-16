"""Tests for the shell file mention completer."""

from pathlib import Path

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from kimi_cli.ui.shell.prompt import FileMentionCompleter


def _completion_texts(completer: FileMentionCompleter, text: str) -> list[str]:
    document = Document(text=text, cursor_position=len(text))
    event = CompleteEvent(completion_requested=True)
    return [completion.text for completion in completer.get_completions(document, event)]


def test_top_level_paths_skip_ignored_names(tmp_path: Path):
    """Only surface non-ignored entries when completing the top level."""
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".DS_Store").write_text("")
    (tmp_path / "README.md").write_text("hello")

    completer = FileMentionCompleter(tmp_path)

    texts = _completion_texts(completer, "@")

    assert "src/" in texts
    assert "README.md" in texts
    assert "node_modules/" not in texts
    assert ".DS_Store" not in texts


def test_directory_completion_continues_after_slash(tmp_path: Path):
    """Continue descending when the fragment ends with a slash."""
    src = tmp_path / "src"
    src.mkdir()
    nested = src / "module.py"
    nested.write_text("print('hi')\n")

    completer = FileMentionCompleter(tmp_path)

    texts = _completion_texts(completer, "@src/")

    assert "src/" in texts
    assert "src/module.py" in texts


def test_completed_file_short_circuits_completions(tmp_path: Path):
    """Stop offering fuzzy matches once the fragment resolves to an existing file."""
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Agents\n")

    nested_dir = tmp_path / "src" / "kimi_cli" / "agents"
    nested_dir.mkdir(parents=True)
    (nested_dir / "README.md").write_text("nested\n")

    completer = FileMentionCompleter(tmp_path)

    texts = _completion_texts(completer, "@AGENTS.md")

    assert not texts


def test_limit_is_enforced(tmp_path: Path):
    """Respect the configured limit when building top-level candidates."""
    for index in range(10):
        (tmp_path / f"dir{index}").mkdir()
    for index in range(10):
        (tmp_path / f"file{index}.txt").write_text("x")

    limit = 8
    completer = FileMentionCompleter(tmp_path, limit=limit)

    texts = _completion_texts(completer, "@")

    assert len(set(texts)) == limit


def test_at_guard_prevents_email_like_fragments(tmp_path: Path):
    """Ignore `@` that are embedded inside identifiers (e.g. emails)."""
    (tmp_path / "example.py").write_text("")

    completer = FileMentionCompleter(tmp_path)

    texts = _completion_texts(completer, "email@example.com")

    assert not texts
