"""Keep the CLI contract in SPEC aligned with the shipped registrations."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from cmru.agent.cli import _build_cli as build_agent_cli
from cmru.bundle import bundle_cli
from cmru.cli import _build_cli as build_cmru_cli
from cmru.controller.cli import _build_cli as build_controller_cli
from cmru.handlers import handlers_cli
from cmru.runner import runner_cli


SPEC = Path(__file__).parents[1] / "docs" / "SPEC.md"


def _marked_table(start: str, end: str) -> list[list[str]]:
    text = SPEC.read_text(encoding="utf-8")
    body = text.split(start, 1)[1].split(end, 1)[0]
    rows = []
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and set(cells[0]) <= {"-", ":"}:
            continue
        if cells and cells[0] not in {"Surface", "Entry point", "Entry point family"}:
            rows.append(cells)
    return rows


def _argument_shape(parser: argparse.ArgumentParser) -> str:
    values = []
    for action in parser._actions:
        if action.option_strings:
            continue
        if action.nargs == "?":
            values.append(f"{action.dest}?")
        elif action.nargs in ("*", "+", argparse.REMAINDER):
            values.append(f"{action.dest}{action.nargs}")
        else:
            values.append(action.dest)
    return ", ".join(values) if values else "—"


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return {
        spelling
        for action in parser._actions
        for spelling in action.option_strings
    }


def _registered_surfaces(registry, prefix: str):
    """Flatten registered verbs and nested delegated registries to leaf parsers."""
    if not registry.command_parsers:
        yield prefix, registry.parser
        return

    for name, parser in registry.command_parsers.items():
        path = f"{prefix} {name}"
        child = registry.delegates.get(name)
        if child is not None and child.command_parsers:
            yield from _registered_surfaces(child, path)
        elif child is not None:
            yield path, child.parser
        else:
            yield path, parser


def _inventory() -> dict[str, tuple[str, set[str]]]:
    return {
        surface: (arguments, set() if options == "—" else set(options.split("; ")))
        for surface, arguments, options in _marked_table(
            "<!-- cmru-cli-grammar:start -->", "<!-- cmru-cli-grammar:end -->"
        )
    }


def test_spec_cli_inventory_matches_registered_surfaces_and_options():
    common_rows = _marked_table(
        "<!-- cmru-cli-common:start -->", "<!-- cmru-cli-common:end -->"
    )
    common = {
        family: set(flags.split("; "))
        for family, flags in common_rows
    }
    assert set(common) == {"CMRU and CMRU module adapters", "cmru-agent", "cmru-controller"}
    cmru_flags = common["CMRU and CMRU module adapters"]
    agent_flags = common["cmru-agent"]
    controller_flags = common["cmru-controller"]

    actual = {}
    for surface, parser in _registered_surfaces(build_cmru_cli(), "cmru"):
        actual[surface] = ("CMRU and CMRU module adapters", parser)
    for surface, parser in _registered_surfaces(build_agent_cli(), "cmru-agent"):
        actual[surface] = ("cmru-agent", parser)
    for surface, parser in _registered_surfaces(build_controller_cli(), "cmru-controller"):
        actual[surface] = ("cmru-controller", parser)

    # These are executable module-only adapters in addition to the installed
    # console scripts. Their actual registries are the source of their grammar.
    for surface, parser in _registered_surfaces(bundle_cli(), "python -m cmru.bundle"):
        actual[surface] = ("CMRU and CMRU module adapters", parser)
    for surface, parser in _registered_surfaces(runner_cli(), "python -m cmru.runner"):
        actual[surface] = ("CMRU and CMRU module adapters", parser)
    for surface, parser in _registered_surfaces(handlers_cli(), "python -m cmru.handlers"):
        actual[surface] = ("CMRU and CMRU module adapters", parser)

    inventory = _inventory()
    assert set(inventory) == set(actual), (
        f"SPEC surfaces differ from cli-extended registrations; "
        f"missing={sorted(set(actual) - set(inventory))}, "
        f"stale={sorted(set(inventory) - set(actual))}"
    )

    for surface, (family, parser) in actual.items():
        arguments, local_options = inventory[surface]
        assert arguments == _argument_shape(parser), (
            f"{surface}: SPEC positional shape {arguments!r} does not match "
            f"registered {_argument_shape(parser)!r}"
        )
        expected_options = common[family] | local_options
        registered_options = _option_strings(parser)
        assert registered_options == expected_options, (
            f"{surface}: SPEC option spellings differ; "
            f"missing={sorted(registered_options - expected_options)}, "
            f"stale={sorted(expected_options - registered_options)}"
        )


def test_spec_builtin_help_and_version_grammar_matches_library_help():
    expected = {
        "cmru": build_cmru_cli(),
        "cmru-agent": build_agent_cli(),
        "cmru-controller": build_controller_cli(),
    }
    documented = {
        entrypoint: set(commands.split("; "))
        for entrypoint, commands in _marked_table(
            "<!-- cmru-cli-builtins:start -->", "<!-- cmru-cli-builtins:end -->"
        )
    }
    assert set(documented) == set(expected)
    for entrypoint, registry in expected.items():
        assert documented[entrypoint] == {"help [VERB]", "version"}
        generated_help = registry.parser.format_help().lower()
        assert f"{entrypoint} help [verb]" in generated_help
        assert f"{entrypoint} version" in generated_help


def test_semantic_audit_covers_every_inventory_surface_and_option():
    rows = _marked_table(
        "<!-- cmru-cli-semantic-audit:start -->",
        "<!-- cmru-cli-semantic-audit:end -->",
    )
    by_surface: dict[str, str] = {}
    for surfaces, semantic_result, _result in rows:
        for surface in surfaces.split("; "):
            assert surface not in by_surface, f"duplicate semantic audit row for {surface}"
            by_surface[surface] = semantic_result

    inventory = _inventory()
    assert set(inventory) <= set(by_surface), (
        "semantic audit lacks surfaces: " + ", ".join(sorted(set(inventory) - set(by_surface)))
    )
    for surface, (_arguments, options) in inventory.items():
        mentioned = set(re.findall(r"--[A-Za-z0-9_-]+", by_surface[surface]))
        assert options <= mentioned, (
            f"{surface}: semantic result omits options "
            + ", ".join(sorted(options - mentioned))
        )

    common_rows = _marked_table(
        "<!-- cmru-cli-common:start -->", "<!-- cmru-cli-common:end -->"
    )
    for family, flags in common_rows:
        family_audit = next(
            semantic
            for surfaces, semantic, _result in rows
            if (family == "CMRU and CMRU module adapters" and surfaces == "CMRU common controls")
            or (family != "CMRU and CMRU module adapters" and surfaces == "Agent/controller common controls")
        )
        assert set(flags.split("; ")) <= set(re.findall(r"--[A-Za-z0-9_-]+", family_audit)), family


def test_user_facing_cli_docs_link_to_the_canonical_spec_anchor():
    spec = SPEC.read_text(encoding="utf-8")
    assert '<a id="s-cli-grammar-audit"></a>' in spec
    for path in (SPEC.parents[1] / "README.md", SPEC.parent / "DESIGN-GUIDE.md"):
        assert "#s-cli-grammar-audit" in path.read_text(encoding="utf-8"), path
