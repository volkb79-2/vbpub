"""Keep the CLI contract in SPEC aligned with the shipped registrations."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from cmru.cli import _build_cli as build_cmru_cli
from cmru.handlers import handlers_cli


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


def _registered_surface_groups(registry, prefix: str):
    """Return the cli-extended semantic group attached to each rendered leaf."""
    catalog = registry.parser.catalog
    if not registry.command_parsers:
        group = catalog.verbs[0].group if catalog and catalog.verbs else "STANDALONE COMMAND"
        yield prefix, group
        return

    groups = {
        verb.name: verb.group
        for verb in (catalog.verbs if catalog is not None else ())
    }
    for name in registry.command_parsers:
        path = f"{prefix} {name}"
        child = registry.delegates.get(name)
        if child is not None and child.command_parsers:
            yield from _registered_surface_groups(child, path)
        elif child is not None:
            # A single-command delegate is an implementation detail of this
            # top-level verb; the parent verb owns its help-catalog group.
            yield path, groups.get(name, "")
        else:
            yield path, groups.get(name, "")


def _inventory() -> dict[str, tuple[str, str, set[str]]]:
    return {
        surface: (
            group,
            arguments,
            set() if options == "—" else set(options.split("; ")),
        )
        for surface, group, arguments, options in _marked_table(
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
    assert set(common) == {"cmru and handlers module adapter"}

    actual = {}
    for registry, prefix, family in (
        (build_cmru_cli(), "cmru", "cmru and handlers module adapter"),
    ):
        assert registry.parser.allow_abbrev is False, prefix
        groups = dict(_registered_surface_groups(registry, prefix))
        for surface, parser in _registered_surfaces(registry, prefix):
            assert parser.allow_abbrev is False, surface
            actual[surface] = (family, parser, groups[surface])

    # This is the one supported component module CLI: active project-step and
    # first-wheel bootstrap contracts use it.
    for registry, prefix in ((handlers_cli(), "python -m cmru.handlers"),):
        assert registry.parser.allow_abbrev is False, prefix
        groups = dict(_registered_surface_groups(registry, prefix))
        for surface, parser in _registered_surfaces(registry, prefix):
            assert parser.allow_abbrev is False, surface
            actual[surface] = ("cmru and handlers module adapter", parser, groups[surface])

    inventory = _inventory()
    assert set(inventory) == set(actual), (
        f"SPEC surfaces differ from cli-extended registrations; "
        f"missing={sorted(set(actual) - set(inventory))}, "
        f"stale={sorted(set(inventory) - set(actual))}"
    )

    for surface, (family, parser, group) in actual.items():
        documented_group, arguments, local_options = inventory[surface]
        assert documented_group == group, (
            f"{surface}: SPEC help group {documented_group!r} differs from registered {group!r}"
        )
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


def _registered_parsers(registry):
    yield registry.parser
    yield from registry.command_parsers.values()
    for child in registry.delegates.values():
        yield from _registered_parsers(child)


def _check_behavior_labels(registry, *, path=""):
    """Check help metadata against the semantic category in the CLI spec."""
    catalog = registry.parser.catalog
    if catalog is None:
        return
    mutating_groups = {
        "MODIFICATION", "MIXED OPERATIONS", "MAINTENANCE", "AUTHENTICATION / SETUP",
    }
    for verb in catalog.verbs:
        surface = f"{path} {verb.name}".strip()
        expected = verb.group in mutating_groups
        assert verb.mutating is expected, (
            f"{surface}: group {verb.group!r} and mutating help metadata disagree"
        )
        child = registry.delegates.get(verb.name)
        if child is None:
            continue
        if child.parser.catalog is None:
            # Single-command delegates surface their behavior in their own help.
            text = child.parser.format_help()
            assert ("Behavior: mutating" in text) is expected, surface
        else:
            _check_behavior_labels(child, path=surface)


def test_registered_boolean_flags_default_off_and_help_marks_mutating_verbs():
    registries = (
        (build_cmru_cli(), "cmru"),
        (handlers_cli(), "python -m cmru.handlers"),
    )
    for registry, prefix in registries:
        _check_behavior_labels(registry, path=prefix)
        for parser in _registered_parsers(registry):
            for action in parser._actions:
                if isinstance(action, argparse._StoreTrueAction):
                    assert action.default is not True, (
                        f"{prefix} {parser.prog}: {action.option_strings} defaults on"
                    )

    # The top-level delegate parsers are dispatch shells. Their common options
    # are not accepted by the child CLI and must not appear as false grammar.
    cmru = build_cmru_cli()
    for name in cmru.delegates:
        parser = cmru.command_parsers[name]
        assert "--json" not in _option_strings(parser), name
        assert "--progress" not in _option_strings(parser), name


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
    for surface, (_group, _arguments, options) in inventory.items():
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
            if family == "cmru and handlers module adapter" and surfaces == "CMRU common controls"
        )
        assert set(flags.split("; ")) <= set(re.findall(r"--[A-Za-z0-9_-]+", family_audit)), family


def test_user_facing_cli_docs_link_to_the_canonical_spec_anchor():
    spec = SPEC.read_text(encoding="utf-8")
    assert "### S-CLI.9: Canonical CLI grammar and semantic audit" in spec
    for path in (SPEC.parents[1] / "README.md", SPEC.parent / "DESIGN-GUIDE.md"):
        assert "#s-cli9-canonical-cli-grammar-and-semantic-audit" in path.read_text(encoding="utf-8"), path
