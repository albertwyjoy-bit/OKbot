from pydantic import BaseModel, Field


class DMail(BaseModel):
    message: str = Field(description="The message to send.")
    checkpoint_id: int = Field(description="The checkpoint to send the message back to.", ge=0)


class DenwaRenjiError(Exception):
    pass


class DenwaRenji:
    def __init__(self):
        self._pending_dmail: DMail | None = None
        self._n_checkpoints: int = 0

    def set_n_checkpoints(self, n_checkpoints: int):
        self._n_checkpoints = n_checkpoints

    def send_dmail(self, dmail: DMail):
        if self._pending_dmail is not None:
            raise DenwaRenjiError("Only one D-Mail can be sent at a time")
        if dmail.checkpoint_id < 0:
            raise DenwaRenjiError("The checkpoint ID can not be negative")
        if dmail.checkpoint_id >= self._n_checkpoints:
            raise DenwaRenjiError("There is no checkpoint with the given ID")
        self._pending_dmail = dmail

    def fetch_pending_dmail(self) -> DMail | None:
        pending_dmail = self._pending_dmail
        self._pending_dmail = None
        return pending_dmail
