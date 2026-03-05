from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from kimi_cli.soul.toolset import get_current_tool_call_or_none
from kimi_cli.utils.aioqueue import Queue
from kimi_cli.utils.logging import logger
from kimi_cli.wire.types import DisplayBlock


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    id: str
    tool_call_id: str
    sender: str
    action: str
    description: str
    display: list[DisplayBlock]
    mandatory: bool = False


type Response = Literal["approve", "approve_for_session", "reject"]


# ContextVar for background task YOLO mode override.
# asyncio.create_task() copies the current context, so setting this inside a background
# task function only affects that task's local context copy — the main agent is unaffected.
_background_yolo_mode: ContextVar[bool] = ContextVar("background_yolo_mode", default=False)

# Allowed tools in plan mode (read-only tools + WriteFile for plan editing + PlanExit)
PLAN_MODE_ALLOWED_TOOLS = frozenset({
    "ReadFile", "ReadMediaFile", "Grep", "Glob", "SearchWeb", "FetchURL",
    "WriteFile",  # Allowed but restricted to plan file only
    "PlanExit",  # Allow model to exit plan mode
})


class ApprovalState:
    def __init__(self, yolo: bool = False):
        self.yolo = yolo
        self.auto_approve_actions: set[str] = set()  # TODO: persist across sessions
        """Set of action names that should automatically be approved."""
        self.plan_mode: bool = False
        """Whether plan mode is enabled. In plan mode, only read-only tools are allowed."""
        self.plan_file_path: str | None = None
        """The path to the plan file that can be edited in plan mode."""


class Approval:
    def __init__(self, yolo: bool = False, *, state: ApprovalState | None = None):
        self._request_queue = Queue[Request]()
        self._requests: dict[str, tuple[Request, asyncio.Future[bool]]] = {}
        self._state = state or ApprovalState(yolo=yolo)

    @property
    def state(self) -> ApprovalState:
        """The approval state shared across all approval instances."""
        return self._state

    def share(self) -> Approval:
        """Create a new approval queue that shares state (yolo + auto-approve + plan_mode)."""
        return Approval(state=self._state)

    def set_yolo(self, yolo: bool) -> None:
        self._state.yolo = yolo

    def is_yolo(self) -> bool:
        return self._state.yolo

    def set_plan_mode(self, enabled: bool, plan_file_path: str | None = None) -> None:
        """Set plan mode state.
        
        Args:
            enabled: Whether to enable plan mode
            plan_file_path: The path to the plan file that can be edited
        """
        self._state.plan_mode = enabled
        self._state.plan_file_path = plan_file_path if enabled else None

    def is_plan_mode(self) -> bool:
        """Check if plan mode is enabled."""
        return self._state.plan_mode

    def get_plan_file_path(self) -> str | None:
        """Get the plan file path if in plan mode."""
        return self._state.plan_file_path

    def is_plan_file(self, path: str) -> bool:
        """Check if the given path is the plan file.
        
        Args:
            path: The file path to check.
            
        Returns:
            True if the path matches the plan file path, False otherwise.
        """
        if not self._state.plan_mode or not self._state.plan_file_path:
            return False
        
        try:
            from pathlib import Path
            requested = Path(path).expanduser().resolve()
            allowed = Path(self._state.plan_file_path).expanduser().resolve()
            return requested == allowed
        except Exception:
            return False

    async def request_write_approval(
        self,
        sender: str,
        path: str,
        description: str,
        display: list[DisplayBlock] | None = None,
    ) -> bool:
        """Request approval for a write operation.
        
        In plan mode, write operations to the plan file are auto-approved.
        Other writes in plan mode are rejected.
        
        Args:
            sender: The name of the sender tool.
            path: The file path being written.
            description: The description of the action.
            display: Optional display blocks.
            
        Returns:
            True if approved, False otherwise.
        """
        # Plan mode: only allow writing to the plan file
        if self._state.plan_mode:
            if self.is_plan_file(path):
                logger.debug(
                    "Auto-approving write to plan file in plan mode: {path}",
                    path=path,
                )
                return True
            logger.info(
                "Write to {path} rejected in plan mode (only plan file allowed)",
                path=path,
            )
            return False
        
        # Normal mode: use regular approval flow
        return await self.request(sender, "write", description, display)

    async def request(
        self,
        sender: str,
        action: str,
        description: str,
        display: list[DisplayBlock] | None = None,
    ) -> bool:
        """
        Request approval for the given action. Intended to be called by tools.

        Args:
            sender (str): The name of the sender.
            action (str): The action to request approval for.
                This is used to identify the action for auto-approval.
            description (str): The description of the action. This is used to display to the user.

        Returns:
            bool: True if the action is approved, False otherwise.

        Raises:
            RuntimeError: If the approval is requested from outside a tool call.
        """
        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            raise RuntimeError("Approval must be requested from a tool call.")

        logger.debug(
            "{tool_name} ({tool_call_id}) requesting approval: {action} {description}",
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
            action=action,
            description=description,
        )

        if self._state.yolo or _background_yolo_mode.get():
            return True

        if action in self._state.auto_approve_actions:
            return True

        return await self._request_approval(
            tool_call_id=tool_call.id,
            sender=sender,
            action=action,
            description=description,
            display=display,
        )

    async def request_mandatory(
        self,
        sender: str,
        action: str,
        description: str,
        display: list[DisplayBlock] | None = None,
    ) -> bool:
        """
        Request mandatory approval - bypasses YOLO mode and auto-approve settings.
        
        This is used for critical operations that always require explicit user confirmation,
        such as exiting plan mode.

        Args:
            sender (str): The name of the sender.
            action (str): The action to request approval for.
            description (str): The description of the action.
            display: Optional display blocks.

        Returns:
            bool: True if the action is approved, False otherwise.

        Raises:
            RuntimeError: If the approval is requested from outside a tool call.
        """
        tool_call = get_current_tool_call_or_none()
        if tool_call is None:
            raise RuntimeError("Approval must be requested from a tool call.")

        logger.debug(
            "{tool_name} ({tool_call_id}) requesting mandatory approval: {action} {description}",
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
            action=action,
            description=description,
        )

        # Always request approval, bypassing YOLO and auto-approve settings
        if _background_yolo_mode.get():
            return True
        return await self._request_approval(
            tool_call_id=tool_call.id,
            sender=sender,
            action=action,
            description=description,
            display=display,
            mandatory=True,
        )

    async def _request_approval(
        self,
        tool_call_id: str,
        sender: str,
        action: str,
        description: str,
        display: list[DisplayBlock] | None = None,
        mandatory: bool = False,
    ) -> bool:
        """
        Internal method to create and wait for an approval request.

        Args:
            tool_call_id: The ID of the current tool call.
            sender: The name of the sender.
            action: The action to request approval for.
            description: The description of the action.
            display: Optional display blocks.
            mandatory: Whether this is a mandatory request that bypasses YOLO mode.

        Returns:
            bool: True if approved, False otherwise.
        """
        request = Request(
            id=str(uuid.uuid4()),
            tool_call_id=tool_call_id,
            sender=sender,
            action=action,
            description=description,
            display=display or [],
            mandatory=mandatory,
        )
        approved_future = asyncio.Future[bool]()
        self._request_queue.put_nowait(request)
        self._requests[request.id] = (request, approved_future)
        return await approved_future

    async def fetch_request(self) -> Request:
        """
        Fetch an approval request from the queue. Intended to be called by the soul.
        """
        while True:
            request = await self._request_queue.get()
            if request.action in self._state.auto_approve_actions:
                # the action is not auto-approved when the request was created, but now it should be
                logger.debug(
                    "Auto-approving previously requested action: {action}", action=request.action
                )
                self.resolve_request(request.id, "approve")
                continue

            return request

    def resolve_request(self, request_id: str, response: Response) -> None:
        """
        Resolve an approval request with the given response. Intended to be called by the soul.

        Args:
            request_id (str): The ID of the request to resolve.
            response (Response): The response to the request.

        Raises:
            KeyError: If there is no pending request with the given ID.
        """
        request_tuple = self._requests.pop(request_id, None)
        if request_tuple is None:
            raise KeyError(f"No pending request with ID {request_id}")
        request, future = request_tuple

        logger.debug(
            "Received approval response for request {request_id}: {response}",
            request_id=request_id,
            response=response,
        )
        match response:
            case "approve":
                future.set_result(True)
            case "approve_for_session":
                self._state.auto_approve_actions.add(request.action)
                future.set_result(True)
            case "reject":
                future.set_result(False)
