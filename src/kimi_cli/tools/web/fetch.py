from pathlib import Path
from typing import override

import aiohttp
import trafilatura
from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnType
from pydantic import BaseModel, Field

from kimi_cli.tools.utils import load_desc

MAX_OUTPUT_LENGTH = 50_000


class Params(BaseModel):
    url: str = Field(description="The URL to fetch content from.")


class FetchURL(CallableTool2[Params]):
    name: str = "FetchURL"
    description: str = load_desc(Path(__file__).parent / "fetch.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params) -> ToolReturnType:
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    params.url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                        ),
                    },
                ) as response,
            ):
                if response.status >= 400:
                    return ToolError(
                        message=(
                            f"Failed to fetch URL. Status: {response.status}. "
                            f"This may indicate the page is not accessible or the server is down."
                        ),
                        brief=f"HTTP {response.status} error",
                    )

                html = await response.text()
        except aiohttp.ClientError as e:
            return ToolError(
                message=(
                    f"Failed to fetch URL due to network error: {str(e)}. "
                    "This may indicate the URL is invalid or the server is unreachable."
                ),
                brief="Network error",
            )

        if not html:
            return ToolOk(
                output="",
                message="The response body is empty.",
                brief="Empty response body",
            )

        extracted_text = trafilatura.extract(
            html,
            include_comments=True,
            include_tables=True,
            include_formatting=False,
            output_format="txt",
            with_metadata=True,
        )

        if not extracted_text:
            return ToolError(
                message=(
                    "Failed to extract meaningful content from the page. "
                    "This may indicate the page content is not suitable for text extraction, "
                    "or the page requires JavaScript to render its content."
                ),
                brief="No content extracted",
            )

        message = "The returned content is the main text content extracted from the page."
        if len(extracted_text) > MAX_OUTPUT_LENGTH:
            extracted_text = extracted_text[:MAX_OUTPUT_LENGTH] + "..."
            message += f" Output truncated to {MAX_OUTPUT_LENGTH} characters."

        return ToolOk(
            output=extracted_text,
            message=message,
        )


if __name__ == "__main__":
    import asyncio

    async def main():
        fetch_url_tool = FetchURL()
        result = await fetch_url_tool(Params(url="https://trafilatura.readthedocs.io/en/latest/"))
        print(result)

    asyncio.run(main())
