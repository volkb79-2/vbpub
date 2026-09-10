"""CLI-level tests for the custom top-level help/usage screen (version
banner + verbs grouped by purpose, alphabetized within each group -- see
cli.py's `_VERB_GROUPS`/`_print_top_level_help`/`_classify_top_level_
invocation`). argparse's own default subparsers rendering is a flat,
unordered, unexplained comma list of all ~32 verb names with no version
banner; these tests cover the replacement screen AND its mechanical
sync-with-the-real-parser guarantee -- not the individual subcommands'
own behavior (see test_cli.py/test_cli_extract.py for that).
"""

from __future__ import annotations

import io

from nyxloom import __version__, cli
from nyxloom.cli import _VERB_GROUPS, _build_parser, _classify_top_level_invocation


# ---------------------------------------------------------------------------
# The sync-check: every verb _build_parser() actually registers must have
# exactly one group in _VERB_GROUPS, and vice versa. This is the mechanical
# guarantee the operator asked for -- it must fail the moment a 33rd verb is
# added without a matching group entry, not silently render an incomplete
# screen.
# ---------------------------------------------------------------------------

def test_every_registered_verb_has_exactly_one_group():
    _, subparsers = _build_parser()
    registered = set(subparsers.choices)

    grouped = [v for verbs in _VERB_GROUPS.values() for v in verbs]
    grouped_set = set(grouped)

    missing = sorted(registered - grouped_set)
    assert not missing, (
        f"verb(s) registered in _build_parser but missing from _VERB_GROUPS: "
        f"{missing} -- add each to exactly one group")

    stale = sorted(grouped_set - registered)
    assert not stale, (
        f"_VERB_GROUPS names verb(s) _build_parser no longer registers: "
        f"{stale} -- remove them")

    duplicates = sorted({v for v in grouped if grouped.count(v) > 1})
    assert not duplicates, (
        f"verb(s) listed in more than one _VERB_GROUPS group: {duplicates}")


def test_sync_check_has_teeth_when_a_verb_is_unmapped():
    """Prove the sync-check actually fails, not just passes by construction:
    drop one real verb from a COPY of _VERB_GROUPS and confirm the same
    assertion this module runs above would catch it."""
    _, subparsers = _build_parser()
    registered = set(subparsers.choices)

    tampered = {name: list(verbs) for name, verbs in _VERB_GROUPS.items()}
    # Remove "extract" from wherever it lives -- it's a real registered verb.
    removed = False
    for verbs in tampered.values():
        if "extract" in verbs:
            verbs.remove("extract")
            removed = True
            break
    assert removed, "test fixture assumption broken: 'extract' isn't grouped"

    grouped_set = {v for verbs in tampered.values() for v in verbs}
    missing = sorted(registered - grouped_set)
    assert missing == ["extract"], (
        "tampering with a copy of _VERB_GROUPS should surface exactly the "
        f"removed verb as unmapped, got: {missing}")


def test_every_top_level_verb_has_nonempty_help():
    """A verb added to _build_parser without `help=...` would otherwise
    ship silently undocumented in the grouped screen (and in argparse's
    own default rendering, which also reads this same attribute)."""
    _, subparsers = _build_parser()
    verb_help = {a.dest: a.help for a in subparsers._choices_actions}

    undocumented = sorted(
        verb for verb in subparsers.choices
        if not (verb_help.get(verb) or "").strip()
    )
    assert not undocumented, (
        f"verb(s) with no (or empty) help= text: {undocumented}")


# ---------------------------------------------------------------------------
# Rendered output
# ---------------------------------------------------------------------------

def test_top_level_help_screen_includes_version_and_every_group():
    parser, subparsers = _build_parser()
    buf = io.StringIO()
    cli._print_top_level_help(parser, subparsers, file=buf)
    out = buf.getvalue()

    assert f"nyxloom {__version__}" in out
    for group_name in _VERB_GROUPS:
        assert group_name in out
    for verb in subparsers.choices:
        assert verb in out


def test_top_level_help_screen_alphabetizes_within_each_group():
    parser, subparsers = _build_parser()
    buf = io.StringIO()
    cli._print_top_level_help(parser, subparsers, file=buf)
    lines = buf.getvalue().splitlines()

    for group_name, verbs in _VERB_GROUPS.items():
        header = f"  {group_name}:"
        assert header in lines, f"missing group header: {header!r}"
        start = lines.index(header) + 1
        seen = []
        for line in lines[start:]:
            if not line.startswith("    "):
                break
            seen.append(line.strip().split()[0])
        expected = sorted(v for v in verbs if v in subparsers.choices)
        assert seen == expected, (
            f"group {group_name!r} not alphabetized: {seen} != {expected}")


def test_bare_invocation_exits_2_and_prints_grouped_help_to_stderr(capsys):
    exit_code = cli.main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"nyxloom {__version__}" in captured.err
    assert "project & workflow lifecycle:" in captured.err


def test_top_level_help_flag_exits_0_and_prints_to_stdout(capsys):
    for argv in (["-h"], ["--help"]):
        exit_code = cli.main(argv)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert f"nyxloom {__version__}" in captured.out
        assert "Commands (grouped by purpose" in captured.out


def test_unknown_command_exits_2_and_names_the_bad_token(capsys):
    exit_code = cli.main(["bogus-verb"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "bogus-verb" in captured.err
    assert f"nyxloom {__version__}" in captured.err


def test_valid_verb_still_dispatches_normally(capsys):
    # `version` takes no args and has no filesystem/registry dependency --
    # a clean way to prove a known verb is untouched by the new top-level
    # interception (it must NOT be classified "unknown"/"bare"/"help").
    exit_code = cli.main(["version"])
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    assert out == __version__


# ---------------------------------------------------------------------------
# _classify_top_level_invocation unit coverage (the routing the above
# end-to-end cases exercise through cli.main()).
# ---------------------------------------------------------------------------

def test_classify_bare_argv():
    assert _classify_top_level_invocation([], {"version"}) == ("bare", None)
    assert _classify_top_level_invocation(["--debug"], {"version"}) == ("bare", None)


def test_classify_help_before_any_command():
    assert _classify_top_level_invocation(["-h"], {"version"}) == ("help", None)
    assert _classify_top_level_invocation(["--help"], {"version"}) == ("help", None)
    assert _classify_top_level_invocation(["--debug", "-h"], {"version"}) == ("help", None)


def test_classify_unknown_command():
    assert _classify_top_level_invocation(["nope"], {"version"}) == ("unknown", "nope")
    assert _classify_top_level_invocation(["--debug", "nope"], {"version"}) == ("unknown", "nope")


def test_classify_known_command_dispatches():
    assert _classify_top_level_invocation(["version"], {"version"}) == ("dispatch", None)
    assert _classify_top_level_invocation(["--debug", "version"], {"version"}) == ("dispatch", None)


def test_classify_leaves_subcommand_help_to_argparse():
    # `nyxloom extract --help` -- "extract" is a known verb, so this must
    # dispatch normally and let extract's OWN subparser handle "--help",
    # not get swallowed as a top-level help request.
    assert _classify_top_level_invocation(
        ["extract", "--help"], {"extract"}) == ("dispatch", None)
