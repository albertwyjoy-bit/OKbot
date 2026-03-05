from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pydantic
from jinja2 import Environment as JinjaEnvironment
from jinja2 import StrictUndefined, TemplateError, UndefinedError
from kaos.path import KaosPath
from kosong.tooling import Toolset

from kimi_cli.agentspec import load_agent_spec
from kimi_cli.auth.oauth import OAuthManager
from kimi_cli.config import Config
from kimi_cli.exception import MCPConfigError, SystemPromptTemplateError
from kimi_cli.llm import LLM
from kimi_cli.memory import MemoryAgent, ObservationInput, ObservationType, SummaryInput
from kimi_cli.session import Session
from kimi_cli.skill import Skill, discover_skills_from_roots, index_skills, resolve_skills_roots
from kimi_cli.soul.approval import Approval
from kimi_cli.soul.denwarenji import DenwaRenji
from kimi_cli.soul.toolset import KimiToolset
from kimi_cli.utils.environment import Environment
from kimi_cli.utils.logging import logger
from kimi_cli.utils.path import list_directory

if TYPE_CHECKING:
    from fastmcp.mcp_config import MCPConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltinSystemPromptArgs:
    """Builtin system prompt arguments."""

    KIMI_NOW: str
    """The current datetime."""
    KIMI_WORK_DIR: KaosPath
    """The absolute path of current working directory."""
    KIMI_WORK_DIR_LS: str
    """The directory listing of current working directory."""
    KIMI_AGENTS_MD: str  # TODO: move to first message from system prompt
    """The content of AGENTS.md."""
    KIMI_SKILLS: str
    """Formatted information about available skills."""
    KIMI_MEMORY_CONTEXT: str
    """Context from memory system (relevant past observations and summaries)."""


async def load_agents_md(work_dir: KaosPath) -> str | None:
    paths = [
        work_dir / "AGENTS.md",
        work_dir / "agents.md",
    ]
    for path in paths:
        if await path.is_file():
            logger.info("Loaded agents.md: {path}", path=path)
            return (await path.read_text()).strip()
    logger.info("No AGENTS.md found in {work_dir}", work_dir=work_dir)
    return None


@dataclass(slots=True, kw_only=True)
class Runtime:
    """Agent runtime."""

    config: Config
    oauth: OAuthManager
    llm: LLM | None  # we do not freeze the `Runtime` dataclass because LLM can be changed
    session: Session
    builtin_args: BuiltinSystemPromptArgs
    denwa_renji: DenwaRenji
    approval: Approval
    labor_market: LaborMarket
    environment: Environment
    skills: dict[str, Skill]
    _skills_dir_override: KaosPath | None = None  # Store for reload
    memory_agent: MemoryAgent | None = None  # Memory system agent

    async def reload_skills(self) -> tuple[int, str]:
        """Reload skills from disk and update builtin_args.
        
        Returns:
            Tuple of (number of skills loaded, formatted skills string)
        """
        from kimi_cli.skill import resolve_skills_roots, discover_skills_from_roots, index_skills
        
        # Discover skills again
        skills_roots = await resolve_skills_roots(
            self.session.work_dir, 
            skills_dir_override=self._skills_dir_override
        )
        skills = await discover_skills_from_roots(skills_roots)
        skills_by_name = index_skills(skills)
        
        # Update skills dict
        self.skills.clear()
        self.skills.update(skills_by_name)
        
        # Format skills for system prompt
        skills_formatted = "\n".join(
            (
                f"- {skill.name}\n"
                f"  - Path: {skill.skill_md_file}\n"
                f"  - Description: {skill.description}"
            )
            for skill in skills
        ) or "No skills found."
        
        # Update builtin_args - create new instance since dataclass is frozen
        object.__setattr__(
            self, 
            'builtin_args', 
            BuiltinSystemPromptArgs(
                KIMI_NOW=datetime.now().astimezone().isoformat(),
                KIMI_WORK_DIR=self.builtin_args.KIMI_WORK_DIR,
                KIMI_WORK_DIR_LS=self.builtin_args.KIMI_WORK_DIR_LS,
                KIMI_AGENTS_MD=self.builtin_args.KIMI_AGENTS_MD,
                KIMI_SKILLS=skills_formatted,
                KIMI_MEMORY_CONTEXT=self.builtin_args.KIMI_MEMORY_CONTEXT,
            )
        )
        
        logger.info("Reloaded {count} skill(s)", count=len(skills))
        return len(skills), skills_formatted

    @staticmethod
    async def create(
        config: Config,
        oauth: OAuthManager,
        llm: LLM | None,
        session: Session,
        yolo: bool,
        skills_dir: KaosPath | None = None,
    ) -> Runtime:
        ls_output, agents_md, environment = await asyncio.gather(
            list_directory(session.work_dir),
            load_agents_md(session.work_dir),
            Environment.detect(),
        )

        # Discover and format skills
        skills_roots = await resolve_skills_roots(session.work_dir, skills_dir_override=skills_dir)
        skills = await discover_skills_from_roots(skills_roots)
        skills_by_name = index_skills(skills)
        logger.info("Discovered {count} skill(s)", count=len(skills))
        skills_formatted = "\n".join(
            (
                f"- {skill.name}\n"
                f"  - Path: {skill.skill_md_file}\n"
                f"  - Description: {skill.description}"
            )
            for skill in skills
        )

        # Initialize memory agent if enabled
        memory_agent = None
        memory_context = ""
        if config.memory.enabled:
            try:
                db_path = config.memory.db_path
                memory_agent = MemoryAgent.create(
                    db_path=db_path,
                    embedding_provider=config.memory.provider,
                    llm_client=llm.chat_provider if llm else None,
                    project=str(session.work_dir),  # 🔴 传递 project（参考 claude-mem）
                )
                # Start memory agent and load context for continuing sessions
                await memory_agent.start()
                memory_context = await memory_agent.on_session_start(session.id)
                if memory_context:
                    logger.debug("Loaded memory context for session: {session_id}", 
                               session_id=session.id)
                logger.info("Memory system initialized with provider: {provider}", 
                           provider=config.memory.provider)
            except Exception as e:
                logger.warning("Failed to initialize memory system: {e}", e=e)
                memory_agent = None

        return Runtime(
            config=config,
            oauth=oauth,
            llm=llm,
            session=session,
            builtin_args=BuiltinSystemPromptArgs(
                KIMI_NOW=datetime.now().astimezone().isoformat(),
                KIMI_WORK_DIR=session.work_dir,
                KIMI_WORK_DIR_LS=ls_output,
                KIMI_AGENTS_MD=agents_md or "",
                KIMI_SKILLS=skills_formatted or "No skills found.",
                KIMI_MEMORY_CONTEXT=memory_context or "",
            ),
            denwa_renji=DenwaRenji(),
            approval=Approval(yolo=yolo),
            labor_market=LaborMarket(),
            environment=environment,
            skills=skills_by_name,
            _skills_dir_override=skills_dir,
            memory_agent=memory_agent,
        )

    def copy_for_fixed_subagent(self) -> Runtime:
        """Clone runtime for fixed subagent."""
        # Subagent should not have access to parent agent's memory context
        subagent_builtin_args = BuiltinSystemPromptArgs(
            KIMI_NOW=datetime.now().astimezone().isoformat(),
            KIMI_WORK_DIR=self.builtin_args.KIMI_WORK_DIR,
            KIMI_WORK_DIR_LS=self.builtin_args.KIMI_WORK_DIR_LS,
            KIMI_AGENTS_MD=self.builtin_args.KIMI_AGENTS_MD,
            KIMI_SKILLS=self.builtin_args.KIMI_SKILLS,
            KIMI_MEMORY_CONTEXT="",  # subagent does not have memory context
        )
        return Runtime(
            config=self.config,
            oauth=self.oauth,
            llm=self.llm,
            session=self.session,
            builtin_args=subagent_builtin_args,
            denwa_renji=DenwaRenji(),  # subagent must have its own DenwaRenji
            approval=self.approval.share(),
            labor_market=LaborMarket(),  # fixed subagent has its own LaborMarket
            environment=self.environment,
            skills=self.skills,
            _skills_dir_override=self._skills_dir_override,
            memory_agent=None,  # subagent does not have memory
        )

    def copy_for_dynamic_subagent(self) -> Runtime:
        """Clone runtime for dynamic subagent."""
        # Subagent should not have access to parent agent's memory context
        subagent_builtin_args = BuiltinSystemPromptArgs(
            KIMI_NOW=datetime.now().astimezone().isoformat(),
            KIMI_WORK_DIR=self.builtin_args.KIMI_WORK_DIR,
            KIMI_WORK_DIR_LS=self.builtin_args.KIMI_WORK_DIR_LS,
            KIMI_AGENTS_MD=self.builtin_args.KIMI_AGENTS_MD,
            KIMI_SKILLS=self.builtin_args.KIMI_SKILLS,
            KIMI_MEMORY_CONTEXT="",  # subagent does not have memory context
        )
        return Runtime(
            config=self.config,
            oauth=self.oauth,
            llm=self.llm,
            session=self.session,
            builtin_args=subagent_builtin_args,
            denwa_renji=DenwaRenji(),  # subagent must have its own DenwaRenji
            approval=self.approval.share(),
            labor_market=self.labor_market,  # dynamic subagent shares LaborMarket with main agent
            environment=self.environment,
            skills=self.skills,
            _skills_dir_override=self._skills_dir_override,
            memory_agent=None,  # subagent does not have memory
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Agent:
    """The loaded agent."""

    name: str
    system_prompt: str
    toolset: Toolset
    runtime: Runtime
    """Each agent has its own runtime, which should be derived from its main agent."""
    
    # Store for refreshing system prompt
    _system_prompt_path: Path = None  # type: ignore
    _system_prompt_args: dict[str, str] = None  # type: ignore
    
    def refresh_system_prompt(self) -> str:
        """Reload system prompt with updated builtin_args from runtime.
        
        Returns:
            The refreshed system prompt string.
        """
        if self._system_prompt_path is None:
            raise ValueError("Agent was not created with system prompt path stored")
        
        new_prompt = _load_system_prompt(
            self._system_prompt_path,
            self._system_prompt_args,
            self.runtime.builtin_args,
        )
        object.__setattr__(self, "system_prompt", new_prompt)
        return new_prompt


class LaborMarket:
    def __init__(self):
        self.fixed_subagents: dict[str, Agent] = {}
        self.fixed_subagent_descs: dict[str, str] = {}
        self.dynamic_subagents: dict[str, Agent] = {}

    @property
    def subagents(self) -> Mapping[str, Agent]:
        """Get all subagents in the labor market."""
        return {**self.fixed_subagents, **self.dynamic_subagents}

    def add_fixed_subagent(self, name: str, agent: Agent, description: str):
        """Add a fixed subagent."""
        self.fixed_subagents[name] = agent
        self.fixed_subagent_descs[name] = description

    def add_dynamic_subagent(self, name: str, agent: Agent):
        """Add a dynamic subagent."""
        self.dynamic_subagents[name] = agent


async def load_agent(
    agent_file: Path,
    runtime: Runtime,
    *,
    mcp_configs: list[MCPConfig] | list[dict[str, Any]],
) -> Agent:
    """
    Load agent from specification file.

    Raises:
        FileNotFoundError: When the agent file is not found.
        AgentSpecError(KimiCLIException, ValueError): When the agent specification is invalid.
        SystemPromptTemplateError(KimiCLIException, ValueError): When the system prompt template
            is invalid.
        InvalidToolError(KimiCLIException, ValueError): When any tool cannot be loaded.
        MCPConfigError(KimiCLIException, ValueError): When any MCP configuration is invalid.
        MCPRuntimeError(KimiCLIException, RuntimeError): When any MCP server cannot be connected.
    """
    logger.info("Loading agent: {agent_file}", agent_file=agent_file)
    agent_spec = load_agent_spec(agent_file)

    system_prompt = _load_system_prompt(
        agent_spec.system_prompt_path,
        agent_spec.system_prompt_args,
        runtime.builtin_args,
    )

    # load subagents before loading tools because Task tool depends on LaborMarket on initialization
    for subagent_name, subagent_spec in agent_spec.subagents.items():
        logger.debug("Loading subagent: {subagent_name}", subagent_name=subagent_name)
        subagent = await load_agent(
            subagent_spec.path,
            runtime.copy_for_fixed_subagent(),
            mcp_configs=mcp_configs,
        )
        runtime.labor_market.add_fixed_subagent(subagent_name, subagent, subagent_spec.description)

    toolset = KimiToolset(plan_mode_check=lambda: runtime.approval.state.plan_mode)
    tool_deps = {
        KimiToolset: toolset,
        Runtime: runtime,
        # TODO: remove all the following dependencies and use Runtime instead
        Config: runtime.config,
        BuiltinSystemPromptArgs: runtime.builtin_args,
        Session: runtime.session,
        DenwaRenji: runtime.denwa_renji,
        Approval: runtime.approval,
        LaborMarket: runtime.labor_market,
        Environment: runtime.environment,
    }
    tools = agent_spec.tools
    if agent_spec.exclude_tools:
        logger.debug("Excluding tools: {tools}", tools=agent_spec.exclude_tools)
        tools = [tool for tool in tools if tool not in agent_spec.exclude_tools]
    toolset.load_tools(tools, tool_deps)

    # Auto-register memory tools if memory is enabled
    if runtime.memory_agent:
        try:
            from kimi_cli.tools.memory_tools import (
                SearchMemory,
                TimelineMemory,
                GetObservations,
                SaveMemory,
            )
            toolset.add(SearchMemory(runtime))
            toolset.add(TimelineMemory(runtime))
            toolset.add(GetObservations(runtime))
            toolset.add(SaveMemory(runtime))
            logger.debug("Memory tools auto-registered (memory enabled)")
        except Exception as e:
            logger.warning("Failed to auto-register memory tools: {e}", e=e)

    if mcp_configs:
        validated_mcp_configs: list[MCPConfig] = []
        if mcp_configs:
            from fastmcp.mcp_config import MCPConfig

            for mcp_config in mcp_configs:
                try:
                    validated_mcp_configs.append(
                        mcp_config
                        if isinstance(mcp_config, MCPConfig)
                        else MCPConfig.model_validate(mcp_config)
                    )
                except pydantic.ValidationError as e:
                    raise MCPConfigError(f"Invalid MCP config: {e}") from e
        await toolset.load_mcp_tools(validated_mcp_configs, runtime)

    return Agent(
        name=agent_spec.name,
        system_prompt=system_prompt,
        toolset=toolset,
        runtime=runtime,
        _system_prompt_path=agent_spec.system_prompt_path,
        _system_prompt_args=agent_spec.system_prompt_args,
    )


def _load_system_prompt(
    path: Path, args: dict[str, str], builtin_args: BuiltinSystemPromptArgs
) -> str:
    logger.info("Loading system prompt: {path}", path=path)
    system_prompt = path.read_text(encoding="utf-8").strip()
    logger.debug(
        "Substituting system prompt with builtin args: {builtin_args}, spec args: {spec_args}",
        builtin_args=builtin_args,
        spec_args=args,
    )
    env = JinjaEnvironment(
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        variable_start_string="${",
        variable_end_string="}",
        undefined=StrictUndefined,
    )
    try:
        template = env.from_string(system_prompt)
        return template.render(asdict(builtin_args), **args)
    except UndefinedError as exc:
        raise SystemPromptTemplateError(f"Missing system prompt arg in {path}: {exc}") from exc
    except TemplateError as exc:
        raise SystemPromptTemplateError(f"Invalid system prompt template: {path}: {exc}") from exc
