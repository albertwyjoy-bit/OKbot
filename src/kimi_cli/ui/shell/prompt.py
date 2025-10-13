import getpass
from typing import override

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout

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


class CustomPromptSession:
    def __init__(self):
        self._session = PromptSession(
            message=FormattedText([("bold", f"{getpass.getuser()}✨ ")]),
            prompt_continuation=FormattedText([("fg:#4d4d4d", "... ")]),
            completer=MetaCommandCompleter(),
            complete_while_typing=True,
            key_bindings=_kb,
        )

    def prompt(self) -> str:
        """Prompt for user input with stdout patching."""
        with patch_stdout():
            return str(self._session.prompt()).strip()
