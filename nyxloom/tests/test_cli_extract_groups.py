"""Pin the option-group membership of `nyxloom-harness extract` (SUCCESSOR-2).

Every extract option belongs to exactly one of five named groups, shown in
`--help` in a fixed order. A new option that is not placed in a group (or is
placed in a sixth, ad-hoc group) fails here.
"""

from __future__ import annotations

import subprocess
import sys

from nyxloom import cli
from nyxloom.cli_registry import EXTRACT_GROUPS, harness_cli

SOURCE, CONTENT, RENDER, DERIVED, OUTPUT = EXTRACT_GROUPS

# flag (primary spelling) -> group. Deprecated aliases stay in their group.
EXPECTED = {
    SOURCE: {
        "--opencode-session", "--format", "--epochs", "--since", "--since-file", "--until",
        "--max-compactions", "--max-time-minutes",
        "--follow", "--interval", "--bell", "--on-attention", "--notify-project",
        "--attention-min-chars",
    },
    CONTENT: {
        "--profile", "--answer-length", "--max-checkpoints", "--max-words",
        "--include-thinking", "--show-api-errors", "--show-compaction-content",
        "--tool-calls", "--tool-errors", "--show-tool-calls", "--show-tool-call-intent",
        "--strip-stale-wakeups", "--redact-pattern", "--prose-only", "--no-prose",
    },
    RENDER: {
        "--strip-cd-prefix", "--no-strip-cd-prefix", "--path-aliases", "--edit-calls",
        "--read-calls", "--effect-calls", "--timestamps", "--timestamp-gap-minutes",
        "--blank-lines", "--gap-marker", "--min-gap-records", "--show-gap-source",
        "--show-timestamps", "--timestamp-format", "--extract-metadata",
    },
    DERIVED: {
        "--ledger", "--effect-pattern", "--no-default-effect-patterns", "--stop-state",
        "--no-ledger", "--no-stop-state",
        "--preset", "--successor-brief", "--order", "--brief-max-chars", "--task",
        "--task-file",
    },
    OUTPUT: {"--json", "--jsonl", "--render-markdown", "--highlight"},
}


def _parser(verb: str):
    return harness_cli().command_parsers[verb]


def _titles_by_primary_flag(parser) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for group in parser._action_groups:
        for action in group._group_actions:
            if action.option_strings:
                found.setdefault(action.option_strings[0], []).append(group.title)
    return found


def test_group_order_is_the_documented_order():
    assert EXTRACT_GROUPS == (
        "Source & range", "Content selection", "Rendering & compression",
        "Derived sections", "Output",
    )
    help_text = _parser("extract").format_help()
    positions = [help_text.index(f"\n{title}:\n") for title in EXTRACT_GROUPS]
    assert positions == sorted(positions)


def test_every_extract_option_is_in_exactly_one_expected_group():
    placed = _titles_by_primary_flag(_parser("extract"))
    # Shared (global) options live in the same groups on every verb; take them
    # from a sibling verb so this test does not hard-code the shared list.
    shared = set(_titles_by_primary_flag(_parser("search")))
    local = {flag: titles for flag, titles in placed.items() if flag not in shared}
    for flag, titles in local.items():
        assert len(titles) == 1, f"{flag} is in several groups: {titles}"
    expected = {flag: group for group, flags in EXPECTED.items() for flag in flags}
    assert len(expected) == sum(len(flags) for flags in EXPECTED.values()), "flag listed twice"
    actual = {flag: titles[0] for flag, titles in local.items()}
    # Primary spelling may be an alias-first tuple; compare on the first flag only.
    missing = {f for f in expected if f not in actual and not _alias_present(f, placed)}
    assert not missing, f"documented flags missing from extract: {missing}"
    for flag, title in actual.items():
        want = expected.get(flag) or _expected_for_alias(flag, expected, _parser("extract"))
        assert want is not None, f"{flag} is not assigned to any group (ungrouped option)"
        assert title == want, f"{flag}: group {title!r}, expected {want!r}"
        assert title in EXTRACT_GROUPS


def _alias_present(flag, placed):
    return flag in placed


def _expected_for_alias(flag, expected, parser):
    # `--answer-length, --long-threshold`: the registry's first spelling is
    # primary; an alias-first parser still maps through any spelling.
    for group in parser._action_groups:
        for action in group._group_actions:
            if flag in action.option_strings:
                for spelling in action.option_strings:
                    if spelling in expected:
                        return expected[spelling]
    return None


def test_deprecated_aliases_keep_their_flags_and_group():
    placed = {}
    for group in _parser("extract")._action_groups:
        for action in group._group_actions:
            for spelling in action.option_strings:
                placed[spelling] = group.title
    assert placed["--long-threshold"] == CONTENT
    assert placed["--checkpoints"] == CONTENT
    assert placed["--max-lifecycle-markers"] == SOURCE
    assert placed["--insert-blank-lines"] == RENDER
    assert placed["--show-tool-calls"] == CONTENT


def test_help_shows_the_preset_expansion():
    help_text = _parser("extract").format_help()
    flat = " ".join(help_text.split())
    # Argparse wraps and may hyphenate long words; compare on the de-wrapped text.
    flat = flat.replace("- ", "-")
    from nyxloom.session_extract.presets import PRESETS

    assert set(PRESETS) == {"watch", "successor", "review", "ledger"}
    for preset in PRESETS.values():
        # --help defines every preset with its exact option set, and shows one
        # example line for it.
        assert preset.expansion in flat, f"{preset.name} expansion missing from --help"
        assert flat.count(f"--preset {preset.name} ") + flat.count(f"--preset {preset.name}") >= 2
        examples = [line for line in help_text.splitlines()
                    if line.strip().startswith("nyxloom-harness extract SESSION_LOG --preset "
                                               f"{preset.name}")]
        assert len(examples) == 1, f"{preset.name}: expected one example line"


def test_help_runs_as_a_real_process_with_all_groups():
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['nyxloom-harness','extract','--help'];"
         "from nyxloom.cli_harness import main; main()"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    for title in EXTRACT_GROUPS:
        assert f"{title}:" in result.stdout
