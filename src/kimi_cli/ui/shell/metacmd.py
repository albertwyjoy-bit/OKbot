import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, overload

from kosong.base.message import Message
from rich.panel import Panel

import kimi_cli.prompts.metacmds as prompts
from kimi_cli.agent import load_agents_md
from kimi_cli.logging import logger
from kimi_cli.soul.context import Context
from kimi_cli.soul.kimisoul import KimiSoul
from kimi_cli.soul.message import system
from kimi_cli.ui.shell.console import console
from kimi_cli.utils import aio
from kimi_cli.utils.changelog import CHANGELOG, format_release_notes

if TYPE_CHECKING:
    from kimi_cli.ui.shell import ShellApp

type MetaCmdFunc = Callable[["ShellApp", list[str]], None]


class MetaCommand(NamedTuple):
    name: str
    description: str
    func: MetaCmdFunc
    aliases: list[str]

    def slash_name(self):
        """/name (aliases)"""
        if self.aliases:
            return f"/{self.name} ({', '.join(self.aliases)})"
        return f"/{self.name}"


# primary name -> MetaCommand
_meta_commands: dict[str, MetaCommand] = {}
# primary name or alias -> MetaCommand
_meta_command_aliases: dict[str, MetaCommand] = {}


def get_meta_command(name: str) -> MetaCommand | None:
    return _meta_command_aliases.get(name)


def get_meta_commands() -> list[MetaCommand]:
    """Get all unique primary meta commands (without duplicating aliases)."""
    return list(_meta_commands.values())


@overload
def meta_command(func: MetaCmdFunc, /) -> MetaCmdFunc: ...


@overload
def meta_command(
    *,
    name: str | None = None,
    aliases: Sequence[str] | None = None,
) -> Callable[[MetaCmdFunc], MetaCmdFunc]: ...


def meta_command(
    func: MetaCmdFunc | None = None,
    *,
    name: str | None = None,
    aliases: Sequence[str] | None = None,
) -> (
    MetaCmdFunc
    | Callable[
        [MetaCmdFunc],
        MetaCmdFunc,
    ]
):
    """Decorator to register a meta command with optional custom name and aliases.

    Usage examples:
      @meta_command
      def help(app: App, args: list[str]): ...

      @meta_command(name="run")
      def start(app: App, args: list[str]): ...

      @meta_command(aliases=["h", "?", "assist"])
      def help(app: App, args: list[str]): ...
    """

    def _register(f: MetaCmdFunc):
        primary = name or f.__name__
        alias_list = list(aliases) if aliases else []

        # Create the primary command with aliases
        cmd = MetaCommand(
            name=primary,
            description=(f.__doc__ or "").strip(),
            func=f,
            aliases=alias_list,
        )

        # Register primary command
        _meta_commands[primary] = cmd
        _meta_command_aliases[primary] = cmd

        # Register aliases pointing to the same command
        for alias in alias_list:
            _meta_command_aliases[alias] = cmd

        return f

    if func is not None:
        return _register(func)
    return _register


@meta_command(aliases=["quit"])
def exit(app: "ShellApp", args: list[str]):
    """Exit the application"""
    # should be handled by `App`
    raise NotImplementedError


@meta_command(aliases=["h", "?"])
def help(app: "ShellApp", args: list[str]):
    """Show help information"""
    console.print(
        Panel(
            f"Send message to {app.soul.name} to get things done!\n\n"
            "Meta commands are also available:\n\n"
            + "\n".join(
                f"  {command.slash_name()}: {command.description}"
                for command in get_meta_commands()
            ),
            border_style="wheat4",
            expand=False,
            padding=(1, 2),
        )
    )


@meta_command(name="release-notes")
def release_notes(app: "ShellApp", args: list[str]):
    """Show release notes"""
    text = format_release_notes(CHANGELOG)
    with console.pager(styles=True):
        console.print(Panel.fit(text, border_style="wheat4", title="Release Notes"))


@meta_command(name="init")
def init(app: "ShellApp", args: list[str]):
    """Analyze the codebase and generate an `AGENTS.md` file"""
    soul_bak = app.soul
    if not isinstance(soul_bak, KimiSoul):
        console.print("[bold red]Failed to analyze the codebase.[/bold red]")
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info("Running `/init`")
        console.print("[bold]Analyzing the codebase...[/bold]")
        tmp_context = Context(file_backend=Path(temp_dir) / "context.jsonl")
        app.soul = KimiSoul(
            soul_bak._agent,
            soul_bak._agent_globals,
            context=tmp_context,
            loop_control=soul_bak._loop_control,
        )
        ok = app._run(prompts.INIT)

        if ok:
            console.print(
                "[bold]Codebase analyzed successfully! "
                "An [underline]AGENTS.md[/underline] file has been created.[/bold]"
            )
        else:
            console.print("[bold red]Failed to analyze the codebase.[/bold red]")

    app.soul = soul_bak
    agents_md = load_agents_md(soul_bak._agent_globals.builtin_args.KIMI_WORK_DIR)
    system_message = system(
        "The user just ran `/init` meta command. "
        "The system has analyzed the codebase and generated an `AGENTS.md` file. "
        f"Latest AGENTS.md file content:\n{agents_md}"
    )
    aio.run(app.soul._context.append_message(Message(role="user", content=[system_message])))


@meta_command(name="clear")
def clear(app: "ShellApp", args: list[str]):
    """Clear the context"""
    if not isinstance(app.soul, KimiSoul):
        console.print("[bold red]Failed to clear the context.[/bold red]")
        return

    aio.run(app.soul._context.revert_to(0))
    console.print("[bold]Context cleared successfully.[/bold]")
