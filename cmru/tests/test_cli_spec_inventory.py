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

# The one problem `surface check` may still report: the library's `register_skills_verbs`
# builds its child registry without the consumer's global options (cli-extended backlog entry
# filed by W2-PKG5). The test tolerates ONLY this item, so a stale manifest, spec region,
# catalog row or finding still fails it; once the library forwards the options this set
# is simply never reported.
_KNOWN_LIBRARY_GAP = {
    "incomplete parser syntax: cmru skills: delegated parser does not register inherited "
    "global option(s): --log-prefix-time-short",
}


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


def test_surface_check_reports_nothing_beyond_the_known_library_gap():
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
    assert set(report.findings) <= _KNOWN_LIBRARY_GAP, report.render()


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
