from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnType

from kimi_cli.soul.denwarenji import DenwaRenji, DenwaRenjiError, DMail

NAME = "SendDMail"


class SendDMail(CallableTool2):
    name: str = NAME
    description: str = (Path(__file__).parent / "dmail.md").read_text()
    params: type[DMail] = DMail

    def __init__(self, denwa_renji: DenwaRenji, **kwargs):
        super().__init__(**kwargs)
        self._denwa_renji = denwa_renji

    @override
    async def __call__(self, params: DMail) -> ToolReturnType:
        try:
            self._denwa_renji.send_dmail(params)
        except DenwaRenjiError as e:
            return ToolError(
                output="",
                message=f"Failed to send D-Mail. Error: {str(e)}",
                brief="Failed to send D-Mail",
            )
        return ToolOk(output="")
