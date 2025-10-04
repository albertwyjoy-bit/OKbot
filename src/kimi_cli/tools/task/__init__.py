from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnType
from pydantic import BaseModel, Field

from kimi_cli.agent import Agent, AgentGlobals, get_agents_dir, load_agent
from kimi_cli.context import Context
from kimi_cli.event import EventQueue, RunEnd, StepInterrupted
from kimi_cli.soul import MaxStepsReached, Soul
from kimi_cli.tools.utils import load_desc
from kimi_cli.utils.message import message_extract_text
from kimi_cli.utils.path import next_available_rotation

# Maximum continuation attempts for task summary
MAX_CONTINUE_ATTEMPTS = 1


CONTINUE_PROMPT = """
Your previous response was too brief. Please provide a more comprehensive summary that includes:

1. Specific technical details and implementations
2. Complete code examples if relevant
3. Detailed findings and analysis
4. All important information that should be aware of by the caller
""".strip()


SUBAGENTS = {
    "koder": (
        get_agents_dir() / "koder" / "sub.yaml",
        "Good at general software engineering tasks.",
    ),
}


SUBAGENTS_MD = "\n".join(
    f"- `{name}`: {description}" for name, (_, description) in SUBAGENTS.items()
)


class Params(BaseModel):
    description: str = Field(description="A short (3-5 word) description of the task")
    subagent_name: str = Field(
        description="The name of the specialized subagent to use for this task"
    )
    prompt: str = Field(
        description=(
            "The task for the subagent to perform. "
            "You must provide a detailed prompt with all necessary background information "
            "because the subagent cannot see anything in your context."
        )
    )


class Task(CallableTool2[Params]):
    name: str = "Task"
    description: str = load_desc(
        Path(__file__).parent / "task.md",
        {
            "SUBAGENTS_MD": SUBAGENTS_MD,
        },
    )
    params: type[Params] = Params

    def __init__(self, agent_globals: AgentGlobals, **kwargs):
        super().__init__(**kwargs)
        self._agent_globals = agent_globals
        self._session = agent_globals.session
        self._subagents: dict[str, Agent] = {}

        # load all subagents
        for subagent_name, (agent_file, _) in SUBAGENTS.items():
            self._subagents[subagent_name] = load_agent(agent_file, agent_globals)

    async def _get_subagent_history_file(self) -> Path:
        """Generate a unique history file path for subagent."""
        main_history_file = self._session.history_file
        subagent_base_name = f"{main_history_file.stem}_sub"
        main_history_file.parent.mkdir(parents=True, exist_ok=True)  # just in case
        sub_history_file = await next_available_rotation(
            main_history_file.parent / f"{subagent_base_name}{main_history_file.suffix}"
        )
        assert sub_history_file is not None
        return sub_history_file

    @override
    async def __call__(self, params: Params) -> ToolReturnType:
        if params.subagent_name not in self._subagents:
            return ToolError(
                message=f"Subagent not found: {params.subagent_name}",
                brief="Subagent not found",
            )
        agent = self._subagents[params.subagent_name]
        try:
            result = await self._run_subagent(agent, params.prompt)
            return result
        except Exception as e:
            return ToolError(
                message=f"Failed to run subagent: {e}",
                brief="Failed to run subagent",
            )

    async def _run_subagent(self, agent: Agent, prompt: str) -> ToolReturnType:
        """Run subagent with optional continuation for task summary."""
        subagent_history_file = await self._get_subagent_history_file()
        context = Context(file_backend=subagent_history_file)
        soul = Soul(
            agent,
            agent_globals=self._agent_globals,
            context=context,
            loop_control=self._agent_globals.config.loop_control,
        )

        async def _visualize(event_queue: EventQueue):
            while True:
                event = await event_queue.get()
                if isinstance(event, StepInterrupted | RunEnd):
                    break

        try:
            await soul.run(prompt, _visualize)
        except MaxStepsReached as e:
            return ToolError(
                message=(
                    f"Max steps {e.n_steps} reached when running subagent. "
                    "Please try splitting the task into smaller subtasks."
                ),
                brief="Max steps reached",
            )

        _error_msg = (
            "The subagent seemed not to run properly. Maybe you have to do the task yourself."
        )

        # Check if the subagent context is valid
        if len(context.history) == 0 or context.history[-1].role != "assistant":
            return ToolError(message=_error_msg, brief="Failed to run subagent")

        final_response = message_extract_text(context.history[-1])

        # Check if response is too brief, if so, run again with continuation prompt
        n_attempts_remaining = MAX_CONTINUE_ATTEMPTS
        if len(final_response) < 200 and n_attempts_remaining > 0:
            await soul.run(CONTINUE_PROMPT, _visualize)

            if len(context.history) == 0 or context.history[-1].role != "assistant":
                return ToolError(message=_error_msg, brief="Failed to run subagent")
            final_response = message_extract_text(context.history[-1])

        return ToolOk(output=final_response)
