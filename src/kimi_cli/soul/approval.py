import asyncio

from kimi_cli.soul.event import ApprovalRequest, ApprovalResponse
from kimi_cli.utils.logging import logger


class Approval:
    def __init__(self, yolo: bool = False):
        self._request_queue = asyncio.Queue[ApprovalRequest]()
        self._yolo = yolo

    async def request(
        self,
        sender: str,
        action: str,
        extra: dict[str, str] | None = None,
    ) -> bool:
        """Request approval for the given action. Intended to be called by tools."""
        logger.debug(
            "Requesting approval: {sender} {action} {extra}",
            sender=sender,
            action=action,
            extra=extra,
        )
        if self._yolo:
            return True

        # TODO(approval): remember choice from the previous request

        request = ApprovalRequest(sender, action, extra)
        self._request_queue.put_nowait(request)
        response = await request.wait()
        logger.debug("Received approval response: {response}", response=response)
        match response:
            case ApprovalResponse.APPROVE:
                return True
            case ApprovalResponse.REJECT:
                return False
            case _:
                raise ValueError(f"Unknown approval response: {response}")

    async def fetch_request(self) -> ApprovalRequest:
        """
        Fetch an approval request from the queue. Intended to be called by the soul.
        """
        return await self._request_queue.get()
