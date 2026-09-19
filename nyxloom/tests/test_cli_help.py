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

import pytest

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

    assert out.splitlines()[0] == cli.cli_headline()
    for group_name in _VERB_GROUPS:
        assert group_name in out
    for verb in subparsers.choices:
        assert verb in out


def test_subcommand_help_and_argument_errors_start_with_the_headline(capsys):
    assert cli.main(["status", "--not-a-status-option"]) == 2
    assert capsys.readouterr().err.splitlines()[0] == cli.cli_headline()

    assert cli.main(["status", "--help"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == cli.cli_headline()

def test_nested_missing_required_positional_starts_with_the_headline(capsys):
    assert cli.main(["extract"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == cli.cli_headline()
    assert "usage: nyxloom extract" in captured.err
    assert "SESSION_LOG" in captured.err

def test_nested_help_starts_with_the_headline(capsys):
    assert cli.main(["extract", "--help"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[0] == cli.cli_headline()
    assert "usage: nyxloom extract" in captured.out

def test_nested_unknown_argument_starts_with_the_headline(capsys):
    assert cli.main(["extract", "SESSION_LOG", "--not-an-option"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == cli.cli_headline()


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
    assert cli.cli_headline() in captured.err
    assert "project & workflow lifecycle:" in captured.err


def test_top_level_help_flag_exits_0_and_prints_to_stdout(capsys):
    for argv in (["-h"], ["--help"]):
        exit_code = cli.main(argv)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert cli.cli_headline() in captured.out
        assert "Commands (grouped by purpose" in captured.out


def test_top_level_version_flag_exits_0_and_is_quiet_on_stderr(capsys):
    assert cli.main(["--version"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"nyxloom {__version__}\n"
    assert captured.err == ""


def test_unknown_command_exits_2_and_names_the_bad_token(capsys):
    exit_code = cli.main(["bogus-verb"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "bogus-verb" in captured.err
    assert cli.cli_headline() in captured.err


def test_unrecognized_top_level_flag_hits_argparses_own_error_path(capsys):
    # Unlike an unrecognized COMMAND (the "unknown" classify branch above,
    # intercepted before argparse ever runs), a lone unrecognized top-level
    # OPTION starts with "-" so _classify_top_level_invocation defers it to
    # argparse via ("dispatch", None). With exit_on_error=False, argparse's
    # own parse_args() raises argparse.ArgumentError for this ("unrecognized
    # arguments: --bogus-flag") rather than calling self.error() directly --
    # this is the one real path that reaches main()'s
    # `except argparse.ArgumentError:` branch, which prints argparse's OWN
    # default usage (not the custom grouped screen) and returns 2.
    exit_code = cli.main(["--bogus-flag"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: nyxloom" in captured.err
    assert captured.err.splitlines()[0] == cli.cli_headline()
    assert "Commands (grouped by purpose" not in captured.err


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


# ---------------------------------------------------------------------------
# A known subcommand's OWN --help / argument errors (operator-reported,
# 2026-09-10): main()'s generic `except (SystemExit, argparse.ArgumentError)`
# around parser.parse_args() used to catch a subparser's own ALREADY-CORRECT
# -h/--help exit (SystemExit(0)) and a missing/bad-argument .error() exit
# (SystemExit(2)) alike, print the WRONG top-level help on top of the
# correct output every time, and turn --help's own clean exit 0 into 2
# (`e.code or 2` -- 0 is falsy). Both are real user-visible bugs distinct
# from _print_top_level_help itself, which only ever fires for a bare/
# top-level-help/unknown-command invocation and was never wrong.
# ---------------------------------------------------------------------------

def test_subcommand_help_exits_0_shows_only_its_own_usage_and_carries_a_banner(capsys):
    exit_code = cli.main(["extract-report", "--help"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "usage: nyxloom extract-report" in captured.out
    assert cli.cli_headline() in captured.out
    assert "--detailed" in captured.out
    # The wrong top-level verb list must not appear alongside the correct,
    # targeted help.
    assert "Commands (grouped by purpose" not in captured.out


def test_subcommand_missing_required_arg_shows_only_its_own_error(capsys):
    exit_code = cli.main(["extract-report"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage: nyxloom extract-report" in captured.err
    assert "the following arguments are required: SESSION_LOG" in captured.err
    # No redundant top-level dump appended after the real, targeted error.
    assert "Commands (grouped by purpose" not in captured.err


def test_every_subcommand_help_carries_the_version_banner():
    _, subparsers = _build_parser()
    for verb, sub in subparsers.choices.items():
        assert sub.description == f"nyxloom {__version__}", verb


def test_reasonix_is_a_public_format_for_extract_surfaces():
    _, subparsers = _build_parser()
    for verb in ("extract", "extract-lossless", "extract-debug"):
        action = subparsers.choices[verb]._option_string_actions["--format"]
        assert "reasonix" in action.choices
    # Report and session-family discovery have no Reasonix contract yet:
    # neither source-backed usage records nor lineage metadata were supplied.
    for verb in ("extract-report", "extract-sessions"):
        action = subparsers.choices[verb]._option_string_actions["--format"]
        assert "reasonix" not in action.choices
