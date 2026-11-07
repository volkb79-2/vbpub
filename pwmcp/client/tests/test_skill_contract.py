"""Keep the packaged skill synchronized with the pinned MCP tools and CLI grammar."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from cli_extended import RegisteredCli
from pwmcp_client.cli import build_cli

CLIENT = Path(__file__).resolve().parents[1]
PWMCP = CLIENT.parent
SKILL = CLIENT / "src" / "pwmcp_client" / "skills" / "pwmcp-fetch" / "SKILL.md"
TOOL_CONTRACT = Path(__file__).resolve().parent / "fixtures" / "playwright-mcp-skill-tools-0.0.80.json"


def _all_option_strings(registered: RegisteredCli) -> set[str]:
    """Return option spellings across this CLI and its delegated command groups."""

    options: set[str] = set()
    cli = registered
    parsers = [cli.parser, *cli.command_parsers.values()]
    for parser in parsers:
        for action in parser._actions:
            options.update(action.option_strings)
    for delegate in cli.delegates.values():
        options.update(_all_option_strings(delegate))
    return options


def test_skill_tool_names_and_required_click_argument_match_pinned_server_contract() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    tools = json.loads(TOOL_CONTRACT.read_text(encoding="utf-8"))
    dockerfile = (PWMCP / "containers" / "pwmcp" / "Dockerfile").read_text(encoding="utf-8")
    pinned = re.search(r"^ARG PLAYWRIGHT_MCP_VERSION=([^\s]+)$", dockerfile, re.MULTILINE)
    assert pinned is not None
    assert tools["package"] == "@playwright/mcp"
    assert tools["version"] == pinned.group(1)
    assert "mcp` SDK `2.3.0`" in skill
    assert "2-tuple `(read, write)`" in skill

    server_tools = {tool["name"]: tool for tool in tools["tools"]}
    skill_tools = set(re.findall(r"\bbrowser_[a-z][a-z0-9_]*\b", skill))
    assert skill_tools
    assert skill_tools <= server_tools.keys()
    assert server_tools["browser_navigate"]["required_arguments"] == ["url"]
    assert server_tools["browser_evaluate"]["required_arguments"] == ["function"]
    click_arguments = re.findall(
        r'await\s+session\.call_tool\(\s*"browser_click"\s*,\s*(\{.*?\})\s*\)',
        skill,
        re.DOTALL,
    )
    assert click_arguments
    required_click = set(server_tools["browser_click"]["required_arguments"])
    assert all(required_click <= ast.literal_eval(arguments).keys() for arguments in click_arguments)


def test_skill_cli_flags_exist_in_the_registered_pwmcp_parser() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    skill_flags = set(re.findall(r"--[a-z][a-z0-9-]*", skill))
    # These belong to `playwright codegen` or the server startup warning, not pwmcp itself.
    external_flags = {"--save-storage", "--isolated"}
    assert skill_flags - external_flags <= _all_option_strings(build_cli())
