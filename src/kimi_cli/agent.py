import importlib
import inspect
import string
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from kosong.base.chat_provider import ChatProvider
from kosong.tooling import CallableTool, SimpleToolset, Toolset
from kosong.utils.typing import JsonType
from pydantic import BaseModel, Field

from kimi_cli.config import Config
from kimi_cli.denwarenji import DenwaRenji
from kimi_cli.llm import LLM
from kimi_cli.logging import logger
from kimi_cli.metadata import Session


class AgentSpec(BaseModel):
    """Agent specification."""

    name: str = Field(..., description="Agent name")
    system_prompt_path: Path = Field(..., description="System prompt path")
    system_prompt_args: dict[str, str] = Field(
        default_factory=dict, description="System prompt arguments"
    )
    tools: list[str] = Field(default_factory=list, description="Tools")


class BuiltinSystemPromptArgs(NamedTuple):
    """Builtin system prompt arguments."""

    ENSOUL_NOW: str
    """The current datetime."""
    ENSOUL_WORK_DIR: Path
    """The current working directory."""
    ENSOUL_WORK_DIR_LS: str
    """The `ls -la` output of current working directory."""
    ENSOUL_AGENTS_MD: str
    """The content of AGENTS.md."""


class AgentGlobals(NamedTuple):
    """Agent globals."""

    config: Config
    llm: LLM
    builtin_args: BuiltinSystemPromptArgs
    denwa_renji: DenwaRenji
    session: Session


class Agent(NamedTuple):
    """The loaded agent."""

    name: str
    system_prompt: str
    toolset: Toolset


def get_agents_dir() -> Path:
    return Path(__file__).parent / "agents"


def load_agent(
    agent_file: Path,
    globals_: AgentGlobals,
) -> Agent:
    """
    Load agent from specification file.

    Raises:
        ValueError: If the agent spec is not valid.
    """

    assert agent_file.is_file(), "expect agent file to exist"
    with open(agent_file, encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    version = data.get("version", 1)
    if version != 1:
        raise ValueError(f"Unsupported agent spec version: {version}")

    agent_spec = AgentSpec(**data.get("agent", {}))
    agent_spec.system_prompt_path = agent_file.parent.joinpath(agent_spec.system_prompt_path)

    system_prompt = _load_system_prompt(agent_spec, globals_.builtin_args)

    tool_deps = {
        AgentGlobals: globals_,
        LLM: globals_.llm,
        ChatProvider: globals_.llm.chat_provider,
        BuiltinSystemPromptArgs: globals_.builtin_args,
        Session: globals_.session,
        DenwaRenji: globals_.denwa_renji,
    }
    toolset, bad_tools = _load_tools(agent_spec, tool_deps, globals_.config.tool_configs)
    if bad_tools:
        raise ValueError(f"Invalid tools: {bad_tools}")

    return Agent(
        name=agent_spec.name,
        system_prompt=system_prompt,
        toolset=toolset,
    )


def _load_system_prompt(agent_spec: AgentSpec, builtin_args: BuiltinSystemPromptArgs) -> str:
    system_prompt = agent_spec.system_prompt_path.read_text().strip()
    logger.debug(
        "Substituting system prompt with builtin args: {builtin_args}, spec args: {spec_args}",
        builtin_args=builtin_args,
        spec_args=agent_spec.system_prompt_args,
    )
    return string.Template(system_prompt).substitute(
        builtin_args._asdict(), **agent_spec.system_prompt_args
    )


def _load_tools(
    agent_spec: AgentSpec,
    dependencies: dict[type[Any], Any],
    configs: dict[str, dict[str, JsonType]],
) -> tuple[Toolset, list[str]]:
    toolset = SimpleToolset()
    bad_tools = []
    for tool_path in agent_spec.tools:
        kwargs = configs.get(tool_path, {})
        tool = _load_tool(tool_path, dependencies, **kwargs)
        if tool:
            toolset += tool
        else:
            bad_tools.append(tool_path)
    logger.debug("Loaded tools: {tools}", tools=toolset.tools)
    if bad_tools:
        logger.error("Bad tools: {bad_tools}", bad_tools=bad_tools)
    return toolset, bad_tools


def _load_tool(tool_path: str, dependencies: dict[type[Any], Any], **kwargs) -> CallableTool | None:
    logger.debug("Loading tool: {tool_path}", tool_path=tool_path)
    module_name, class_name = tool_path.rsplit(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    cls = getattr(module, class_name, None)
    if cls is None:
        return None
    args = []
    for param in inspect.signature(cls).parameters.values():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            # once we encounter a keyword-only parameter, we stop injecting dependencies
            break
        # all positional parameters should be dependencies to be injected
        if param.annotation not in dependencies:
            raise ValueError(f"Tool dependency not found: {param.annotation}")
        args.append(dependencies[param.annotation])
    return cls(*args, **kwargs)


def load_agents_md(work_dir: Path) -> str | None:
    paths = [
        work_dir / "AGENTS.md",
        work_dir / "agents.md",
    ]
    for path in paths:
        if path.is_file():
            logger.debug("Loaded agents.md: {path}", path=path)
            return path.read_text().strip()
    logger.debug("No AGENTS.md found")
    return None
