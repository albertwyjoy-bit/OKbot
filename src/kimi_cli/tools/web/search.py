from pathlib import Path
from typing import override

import aiohttp
from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnType
from pydantic import BaseModel, Field, ValidationError

from kimi_cli.tools.utils import load_desc

SEARCH_BASE_URL = "https://search.saas.moonshot.cn/v1/search"


class Params(BaseModel):
    query: str = Field(description="The query text to search for.")
    limit: int = Field(
        description=(
            "The number of results to return. "
            "Typically you do not need to set this value. "
            "When the results do not contain what you need, "
            "you probably want to give a more concrete query."
        ),
        default=5,
        ge=1,
        le=20,
    )
    include_content: bool = Field(
        description=(
            "Whether to include the content of the web pages in the results. "
            "It can consume a large amount of tokens when this is set to True. "
            "You should avoid enabling this when `limit` is set to a large value."
        ),
        default=False,
    )


class MoonshotSearch(CallableTool2[Params]):
    name: str = "WebSearch"
    description: str = load_desc(Path(__file__).parent / "search.md", {})
    params: type[Params] = Params

    def __init__(self, *, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self._api_key = api_key

    @override
    async def __call__(self, params: Params) -> ToolReturnType:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                SEARCH_BASE_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "text_query": params.query,
                    "limit": params.limit,
                    "enable_page_crawling": params.include_content,
                    "timeout_seconds": 20,
                },
            ) as response,
        ):
            if response.status != 200:
                return ToolError(
                    message=(
                        f"Failed to search. Status: {response.status}. "
                        "This may indicates that the search service is currently unavailable."
                    ),
                    brief="Failed to search",
                )

            try:
                results = Response(**await response.json()).search_results
            except ValidationError as e:
                return ToolError(
                    message=(
                        f"Failed to parse search results. Error: {e}. "
                        "This may indicates that the search service is currently unavailable."
                    ),
                    brief="Failed to parse search results",
                )

        output = ""
        for i, result in enumerate(results):
            if i > 0:
                output += "---\n\n"
            output += f"{result.title}\n{result.url}\nSummary: {result.snippet}\n\n"
            if result.content:
                output += f"{result.content}\n\n"

        return ToolOk(output=output)


class SearchResult(BaseModel):
    content: str
    date: str
    icon: str
    mime: str
    site_name: str
    snippet: str
    title: str
    url: str


class Response(BaseModel):
    search_results: list[SearchResult]
