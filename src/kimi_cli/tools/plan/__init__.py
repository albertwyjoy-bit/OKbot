"""Plan mode tools."""

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.soul.approval import Approval
from kimi_cli.tools.utils import ToolResultBuilder


class PlanExitParams(BaseModel):
    """Parameters for PlanExit tool."""
    
    message: str = Field(
        default="",
        description="Optional message to include when exiting plan mode",
    )


class PlanExit(CallableTool2[PlanExitParams]):
    """Exit plan mode and proceed with execution."""
    
    name: str = "PlanExit"
    description: str = """Exit plan mode and return to normal execution mode.
    
Call this tool ONLY after:
1. You have completed a comprehensive plan in `~/.kimi/plans/{session_id}.md`
2. The user has reviewed and explicitly approved the plan
3. The user has indicated they are ready to proceed with execution

This will disable plan mode restrictions and allow all tools to be used normally.
The plan file will be preserved for reference during execution.

DO NOT call this tool until the user explicitly confirms they want to proceed."""
    params: type[PlanExitParams] = PlanExitParams
    
    def __init__(self, approval: Approval):
        super().__init__()
        self._approval = approval
    
    async def __call__(self, params: PlanExitParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        
        # Check if in plan mode
        if not self._approval.is_plan_mode():
            return builder.error(
                "Not in plan mode. Use `/plan` slash command to enter plan mode.",
                brief="Not in plan mode"
            )
        
        # Get plan file path before exiting
        plan_file = self._approval.get_plan_file_path()
        
        # Request user approval before exiting plan mode
        # Uses request_mandatory() to bypass YOLO mode - plan mode exit always
        # requires explicit user approval regardless of YOLO setting
        approved = await self._approval.request_mandatory(
            sender=self.name,
            action="exit_plan_mode",
            description="Exit plan mode and proceed with execution",
        )
        
        if not approved:
            return builder.error(
                "User rejected the request to exit plan mode. "
                "You remain in plan mode. Use PlanExit again when user is ready to proceed.",
                brief="Exit plan mode rejected by user"
            )
        
        # Exit plan mode (toolset reads state directly from approval.state)
        self._approval.set_plan_mode(False)
        
        # Build success message
        message = "✅ **Exited Plan Mode**\n\n"
        if plan_file:
            message += f"Plan file preserved: `{plan_file}`\n\n"
        message += "All tools are now available for execution."
        
        if params.message:
            message += f"\n\nNote: {params.message}"
        
        return builder.ok(message)


# Export
__all__ = ["PlanExit"]
