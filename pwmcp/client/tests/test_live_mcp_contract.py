"""Opt-in live checks for the pinned @playwright/mcp Streamable HTTP contract."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pwmcp_client.mcp_client import StreamableHttpClient, decode_tool_value

FIXTURE = Path(__file__).parent / "fixtures" / "playwright-mcp-skill-tools-0.0.80.json"


@pytest.mark.pwmcp_live
def test_live_pinned_mcp_tools_list_and_call_response_shapes() -> None:
    endpoint = os.environ.get("PWMCP_LIVE_MCP_URL")
    if not endpoint:
        pytest.skip("set PWMCP_LIVE_MCP_URL to run the pinned @playwright/mcp integration check")
    page_url = os.environ.get("PWMCP_LIVE_PAGE_URL", "https://example.com")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected_tools = {tool["name"]: tool for tool in fixture["tools"]}

    with StreamableHttpClient(endpoint, timeout=45) as client:
        for name in ("browser_navigate", "browser_evaluate"):
            tool = client.tools.get(name)
            assert tool is not None, f"pinned MCP server does not expose {name}"
            schema = tool.get("inputSchema")
            assert isinstance(schema, dict)
            assert schema.get("required", []) == expected_tools[name]["required_arguments"]

        navigated = client.call_tool("browser_navigate", {"url": page_url})
        navigate_content = navigated.get("content")
        assert isinstance(navigate_content, list) and navigate_content
        assert all(isinstance(block, dict) and isinstance(block.get("text"), str) for block in navigate_content)

        evaluated = client.call_tool(
            "browser_evaluate",
            {"function": "() => ({title: document.title, href: location.href})"},
        )
        evaluate_content = evaluated.get("content")
        assert isinstance(evaluate_content, list) and evaluate_content
        assert all(isinstance(block, dict) and isinstance(block.get("text"), str) for block in evaluate_content)
        value = decode_tool_value(evaluated)
        assert isinstance(value, dict)
        assert isinstance(value.get("title"), str)
        assert value.get("href") == page_url
