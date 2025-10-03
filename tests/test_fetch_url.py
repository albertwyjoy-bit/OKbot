"""Tests for WebFetch tool."""

import pytest
from kosong.tooling import ToolError, ToolOk

from kimi_cli.tools.web.fetch import FetchURL, Params


@pytest.mark.asyncio
async def test_fetch_url_basic_functionality(fetch_url_tool: FetchURL) -> None:
    """Test basic WebFetch functionality."""
    # Test with a reliable website that has content
    test_url = "https://github.com/MoonshotAI/Moonlight/issues/4"

    result = await fetch_url_tool(Params(url=test_url))

    # Should succeed
    assert isinstance(result, ToolOk)
    assert isinstance(result.output, str)

    # Should have content
    assert "Typo: adamw vs adamW" in result.output
    assert "The default parameter value" in result.output


@pytest.mark.asyncio
async def test_fetch_url_invalid_url(fetch_url_tool: FetchURL) -> None:
    """Test fetching from an invalid URL."""
    result = await fetch_url_tool(
        Params(url="https://this-domain-definitely-does-not-exist-12345.com/")
    )

    # Should fail with network error
    assert isinstance(result, ToolError)
    assert "network error" in result.message.lower()


@pytest.mark.asyncio
async def test_fetch_url_404_url(fetch_url_tool: FetchURL) -> None:
    """Test fetching from a URL that returns 404."""
    result = await fetch_url_tool(
        Params(url="https://github.com/MoonshotAI/non-existing-repo/issues/1")
    )

    # Should fail with HTTP error
    assert isinstance(result, ToolError)
    assert "404" in result.message or "failed to fetch" in result.message.lower()


@pytest.mark.asyncio
async def test_fetch_url_malformed_url(fetch_url_tool: FetchURL) -> None:
    """Test fetching from a malformed URL."""
    result = await fetch_url_tool(Params(url="not-a-valid-url"))

    # Should fail
    assert isinstance(result, ToolError)


@pytest.mark.asyncio
async def test_fetch_url_empty_url(fetch_url_tool: FetchURL) -> None:
    """Test fetching with empty URL."""
    result = await fetch_url_tool(Params(url=""))

    # Should fail
    assert isinstance(result, ToolError)


@pytest.mark.asyncio
async def test_fetch_url_javascript_driven_site(fetch_url_tool: FetchURL) -> None:
    """Test fetching from a JavaScript-driven site that may not work with trafilatura."""
    result = await fetch_url_tool(Params(url="https://www.moonshot.ai/"))

    # This may fail due to JavaScript rendering requirements
    # If it fails, should indicate extraction issues
    if isinstance(result, ToolError):
        assert "failed to extract meaningful content" in result.message.lower()
