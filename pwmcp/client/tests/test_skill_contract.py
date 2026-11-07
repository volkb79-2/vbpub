"""Keep the packaged skill synchronized with the pinned MCP tools and CLI grammar."""

from __future__ import annotations

import ast
import json
import re
import shlex
from pathlib import Path

from cli_extended import RegisteredCli
from pwmcp_client.cli import build_cli

CLIENT = Path(__file__).resolve().parents[1]
PWMCP = CLIENT.parent
SKILL = CLIENT / "src" / "pwmcp_client" / "skills" / "pwmcp-fetch" / "SKILL.md"
TOOL_CONTRACT = Path(__file__).resolve().parent / "fixtures" / "playwright-mcp-skill-tools-0.0.80.json"


def _option_strings(parser: object) -> set[str]:
    return {
        option
        for action in getattr(parser, "_actions", [])
        for option in action.option_strings
    }


def _options_for_command_path(registered: RegisteredCli, path: list[str]) -> set[str]:
    """Return global and verb-local options for one exact CLI route."""

    options = _option_strings(registered.parser)
    current = registered
    for name in path:
        delegate = current.delegates.get(name)
        if delegate is not None:
            current = delegate
            options.update(_option_strings(current.parser))
            continue
        parser = current.command_parsers.get(name)
        assert parser is not None, f"pwmcp verb or subcommand {name!r} is missing"
        options.update(_option_strings(parser))
        return options
    raise AssertionError(f"incomplete pwmcp command path: {path!r}")


def _skill_cli_invocations(
    skill: str, registered: RegisteredCli
) -> list[tuple[list[str], set[str]]]:
    """Read pwmcp command paths and flags from shell examples in the skill."""

    invocations: list[tuple[list[str], set[str]]] = []
    blocks = re.findall(r"```(?:bash|sh)?\s*\n(.*?)```", skill, re.DOTALL)
    for block in blocks:
        flattened = re.sub(r"\\\s*\n\s*", " ", block)
        for line in flattened.splitlines():
            try:
                words = shlex.split(line.strip())
            except ValueError:
                continue
            if len(words) < 2 or words[0] != "pwmcp" or words[1].startswith("-"):
                continue
            path = [words[1]]
            if words[1] in registered.delegates and len(words) > 2 and not words[2].startswith("-"):
                path.append(words[2])
            flags = {word.partition("=")[0] for word in words if word.startswith("--")}
            invocations.append((path, flags))
    return invocations


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
    registered = build_cli()
    invocations = _skill_cli_invocations(skill, registered)
    assert invocations
    for path, flags in invocations:
        assert flags <= _options_for_command_path(registered, path), (path, flags)


def test_skill_cli_flags_are_checked_against_their_own_verb() -> None:
    registered = build_cli()
    eval_options = _options_for_command_path(registered, ["eval"])
    inspect_options = _options_for_command_path(registered, ["inspect"])
    assert "--timeout" in eval_options
    assert "--format" not in eval_options
    assert "--format" in inspect_options
    assert "--timeout" not in inspect_options
