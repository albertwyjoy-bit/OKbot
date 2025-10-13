import getpass
import json
from hashlib import md5
from pathlib import Path
from typing import override

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from pydantic import BaseModel, ValidationError

from kimi_cli.logging import logger
from kimi_cli.share import get_share_dir
from kimi_cli.ui.shell.metacmd import get_meta_commands


class MetaCommandCompleter(Completer):
    """A completer that:
    - Shows one line per meta command in the form: "/name (alias1, alias2)"
    - Matches by primary name or any alias while inserting the canonical "/name"
    - Only activates when the current token starts with '/'
    """

    @override
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # Only consider the last token (allowing future arguments after a space)
        last_space = text.rfind(" ")
        token = text[last_space + 1 :]
        if not token.startswith("/"):
            return

        typed = token[1:]
        typed_lower = typed.lower()

        for cmd in sorted(get_meta_commands(), key=lambda c: c.name):
            names = [cmd.name] + list(cmd.aliases)
            if typed == "" or any(n.lower().startswith(typed_lower) for n in names):
                yield Completion(
                    text=f"/{cmd.name}",
                    start_position=-len(token),
                    display=cmd.slash_name(),
                    display_meta=cmd.description,
                )


_kb = KeyBindings()


@_kb.add("enter", filter=has_completions)
def accept_completion(event: KeyPressEvent) -> None:
    """Accept the first completion when Enter is pressed and completions are shown."""
    buff = event.current_buffer
    if buff.complete_state and buff.complete_state.completions:
        # Get the current completion, or use the first one if none is selected
        completion = buff.complete_state.current_completion
        if not completion:
            completion = buff.complete_state.completions[0]
        buff.apply_completion(completion)


class _HistoryEntry(BaseModel):
    content: str


def _load_history_entries(history_file: Path) -> list[_HistoryEntry]:
    entries: list[_HistoryEntry] = []
    if not history_file.exists():
        return entries

    try:
        with history_file.open(encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "Failed to parse user history line; skipping: {line}",
                        line=line,
                    )
                    continue
                try:
                    entries.append(_HistoryEntry.model_validate(record))
                except ValidationError:
                    logger.warning(
                        "Failed to validate user history entry; skipping: {line}",
                        line=line,
                    )
                    continue
    except OSError as exc:
        logger.warning(
            "Failed to load user history file: {file} ({error})",
            file=history_file,
            error=exc,
        )

    return entries


class CustomPromptSession:
    def __init__(self):
        history_dir = get_share_dir() / "user-history"
        history_dir.mkdir(parents=True, exist_ok=True)
        work_dir_id = md5(str(Path.cwd()).encode()).hexdigest()
        self._history_file = (history_dir / work_dir_id).with_suffix(".jsonl")

        history_entries = _load_history_entries(self._history_file)
        history = InMemoryHistory()
        for entry in history_entries:
            history.append_string(entry.content)

        self._session = PromptSession(
            message=FormattedText([("bold", f"{getpass.getuser()}✨ ")]),
            prompt_continuation=FormattedText([("fg:#4d4d4d", "... ")]),
            completer=MetaCommandCompleter(),
            complete_while_typing=True,
            key_bindings=_kb,
            history=history,
        )

    def prompt(self) -> str:
        """Prompt for user input with stdout patching."""
        with patch_stdout():
            result = str(self._session.prompt()).strip()
        self._append_history_entry(result)
        return result

    def _append_history_entry(self, text: str) -> None:
        entry = _HistoryEntry(content=text.strip())
        if not entry.content:
            return

        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with self._history_file.open("a", encoding="utf-8") as f:
                f.write(entry.model_dump_json(ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(
                "Failed to append user history entry: {file} ({error})",
                file=self._history_file,
                error=exc,
            )
