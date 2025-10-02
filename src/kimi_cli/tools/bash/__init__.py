import asyncio
from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnType
from pydantic import BaseModel, Field

from kimi_cli.tools.utils import load_desc

MAX_TIMEOUT = 5 * 60
MAX_OUTPUT_LENGTH = 50_000


class Params(BaseModel):
    command: str = Field(description="The bash command to execute.")
    timeout: int = Field(
        description=(
            "The timeout in seconds for the command to execute. "
            "If the command takes longer than this, it will be killed."
        ),
        default=60,
        ge=1,
        le=MAX_TIMEOUT,
    )


class Bash(CallableTool2[Params]):
    name: str = "Bash"
    description: str = load_desc(Path(__file__).parent / "bash.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params) -> ToolReturnType:
        output = []

        def stdout_cb(line: bytes):
            line_str = line.decode()
            output.append(line_str)

        def stderr_cb(line: bytes):
            line_str = line.decode()
            output.append(line_str)

        try:
            exitcode = await _stream_subprocess(
                params.command, stdout_cb, stderr_cb, params.timeout
            )
            # TODO: truncate/compress the output if it is too long
            output_str = "".join(output)
            message = (
                "Command executed successfully."
                if exitcode == 0
                else f"Command failed with exit code: {exitcode}."
            )
            if len(output_str) > MAX_OUTPUT_LENGTH:
                output_str = output_str[:MAX_OUTPUT_LENGTH] + "..."
                message += f" Output truncated to {MAX_OUTPUT_LENGTH} characters."
            if exitcode == 0:
                return ToolOk(output=output_str, message=message)
            return ToolError(
                output=output_str,
                message=message,
                brief=f"Failed with exit code: {exitcode}",
            )
        except TimeoutError:
            output_str = "".join(output)
            return ToolError(
                output=output_str,
                message=f"Command killed by timeout ({params.timeout}s)",
                brief=f"Killed by timeout ({params.timeout}s)",
            )


async def _stream_subprocess(command: str, stdout_cb, stderr_cb, timeout: int) -> int:
    async def _read_stream(stream, cb):
        while True:
            line = await stream.readline()
            if line:
                cb(line)
            else:
                break

    # FIXME: if the event loop is cancelled, an exception may be raised when the process finishes
    process = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_stream(process.stdout, stdout_cb),
                _read_stream(process.stderr, stderr_cb),
            ),
            timeout,
        )
        return await process.wait()
    except TimeoutError:
        process.kill()
        raise
