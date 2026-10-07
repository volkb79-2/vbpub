"""Keep the CLI contract in SPEC aligned with the shipped registrations.

W2-PKG5 (CLI-T3/T4): the hand-kept grammar inventory, built-in grammar, mutating-label and
semantic-audit tests were DELETED. Their work is done by:

* ``cli-extended surface check`` (asserted below): the generated S-CLI.9 region, the manifest
  ``docs/cli-surface.json`` and the reviewed catalog ``docs/cli-review.toml`` must match the real
  registry (spellings, help groups, positionals, constraints, aliases, hidden flags);
* the catalog's reviewed cases, each linked to ``tests/test_cli_review_cases.py`` through
  ``@pytest.mark.cli_case`` by the cli-extended pytest plugin (``tests/conftest.py``);
* ``tests/test_w2_pkg5_exploration_verbs.py`` (T5), which proves the read-only verbs leave the
  tree unchanged, replacing the circular group-implies-mutating label test.

What is left here are the registry facts those mechanisms do not cover.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from cmru.cli import _build_cli as build_cmru_cli
from cmru.handlers import handlers_cli


SPEC = Path(__file__).parents[1] / "docs" / "SPEC.md"
PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"

def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return {
        spelling
        for action in parser._actions
        for spelling in action.option_strings
    }


def _registered_parsers(registry):
    yield registry.parser
    yield from registry.command_parsers.values()
    for child in registry.delegates.values():
        yield from _registered_parsers(child)


def test_surface_check_reports_no_findings():
    from cli_extended import check_cli_surface
    from cli_extended.config import load_project_config

    project = load_project_config(PYPROJECT)
    assert [cli.id for cli in project.clis] == ["cmru"]
    cli = project.clis[0]
    report = check_cli_surface(
        build_cmru_cli(),
        review_path=cli.review,
        manifest_path=cli.manifest,
        spec_path=cli.spec,
        findings_path=cli.findings,
    )
    # Strict: no tolerance. A stale manifest, spec region, catalog row or finding fails here
    # (the CLI-EXT-26 tolerance, KI-61, was removed once cli-extended 0.3.0 shipped the fix).
    assert list(report.findings) == [], report.render()


def test_one_cli_entry_covers_root_and_handlers_module_adapter():
    """`python -m cmru.handlers` builds the registry the root mounts as `cmru handler`."""
    handlers = handlers_cli()
    root = build_cmru_cli()
    assert handlers.identity.command_name == root.identity.command_name == "cmru"
    assert set(handlers.command_parsers) == set(root.delegates["handler"].command_parsers)


def test_registered_boolean_flags_default_off():
    for registry in (build_cmru_cli(), handlers_cli()):
        assert registry.parser.allow_abbrev is False
        for parser in _registered_parsers(registry):
            assert parser.allow_abbrev is False, parser.prog
            for action in parser._actions:
                if isinstance(action, argparse._StoreTrueAction):
                    assert action.default is not True, (
                        f"{parser.prog}: {action.option_strings} defaults on"
                    )


def test_dispatch_shells_do_not_show_common_options_as_grammar():
    # The top-level delegate parsers are dispatch shells. Their common options are not accepted
    # by the child CLI and must not appear as false grammar.
    cmru = build_cmru_cli()
    for name in cmru.delegates:
        if name == "skills":
            continue  # library-built shell: its --json is real (skills check/list emit it)
        parser = cmru.command_parsers[name]
        assert "--json" not in _option_strings(parser), name
        assert "--progress" not in _option_strings(parser), name


def test_user_facing_cli_docs_link_to_the_canonical_spec_anchor():
    spec = SPEC.read_text(encoding="utf-8")
    assert "### S-CLI.9: Canonical CLI grammar and semantic audit" in spec
    for path in (SPEC.parents[1] / "README.md", SPEC.parent / "DESIGN-GUIDE.md"):
        assert "#s-cli9-canonical-cli-grammar-and-semantic-audit" in path.read_text(encoding="utf-8"), path


# ---- the retained "Verb semantics" table must not go stale -------------------------------------

# Registered leaves that deliberately have no VERB-LEVEL semantic row: their whole contract is
# grammar, which the generated S-CLI.9 region and the reviewed catalog already record.
_GRAMMAR_ONLY = {
    "skills check", "skills install", "skills list", "skills uninstall",  # library-built verbs
    "doctor",  # read-only environment report; contract lives in its own tests
}
_SEMANTIC_START, _SEMANTIC_END = "<!-- cmru-cli-semantic-audit:start -->", "<!-- cmru-cli-semantic-audit:end -->"


def _registered_leaves(registry, prefix: str = "") -> set[str]:
    leaves: set[str] = set()
    for name in registry.command_parsers:
        child = registry.delegates.get(name)
        if child is not None and child.command_parsers:
            leaves |= _registered_leaves(child, f"{prefix}{name} ")
        else:
            leaves.add(f"{prefix}{name}")
    return leaves


def _semantic_rows(spec_text: str) -> set[str]:
    """The verb path of every row, taking the first of ``a; python -m b`` alternatives."""
    table = spec_text.split(_SEMANTIC_START, 1)[1].split(_SEMANTIC_END, 1)[0]
    rows: set[str] = set()
    for line in table.splitlines():
        if not line.startswith("| cmru "):
            continue  # header, separator or a malformed row (caught by the content test)
        surface = line.split("|")[1].strip()
        first = surface.split(";")[0].strip().removeprefix("cmru ").strip()
        rows.add(first)
    return rows


def test_every_verb_the_semantics_table_names_is_registered():
    registered = _registered_leaves(build_cmru_cli()) | {"common controls"}
    rows = _semantic_rows(SPEC.read_text(encoding="utf-8")) - {"common controls"}
    assert rows <= registered, sorted(rows - registered)
    assert len(rows) >= 25  # the table is still the 30-row table, not an empty shell


def test_every_registered_verb_has_a_semantics_row_or_is_grammar_only():
    registered = _registered_leaves(build_cmru_cli())
    rows = _semantic_rows(SPEC.read_text(encoding="utf-8"))
    assert not (registered - rows - _GRAMMAR_ONLY), sorted(registered - rows - _GRAMMAR_ONLY)
    # the exemption list itself may not go stale
    assert _GRAMMAR_ONLY <= registered, sorted(_GRAMMAR_ONLY - registered)
    assert not (_GRAMMAR_ONLY & rows), sorted(_GRAMMAR_ONLY & rows)


def test_a_stale_semantics_table_is_detected():
    """Plant: a row naming a removed verb, and a registered verb losing its row."""
    text = SPEC.read_text(encoding="utf-8")
    registry = build_cmru_cli()
    assert "| cmru tool-deps |" in text
    ghost = text.replace("| cmru tool-deps |", "| cmru ghost-verb |", 1)
    rows = _semantic_rows(ghost)
    assert "ghost-verb" in rows and not rows <= (_registered_leaves(registry) | {"common controls"})
    assert "tool-deps" not in rows
    assert "tool-deps" in (_registered_leaves(registry) - rows - _GRAMMAR_ONLY)
