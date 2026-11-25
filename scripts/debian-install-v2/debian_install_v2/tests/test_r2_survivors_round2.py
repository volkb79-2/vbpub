"""Round two of the R2-survivor regressions: the `iobench-install-runner` allow-list.

Each test here pins ONE changed line of the root-run runner allow-list in
`debian_install_v2/actions.py` (lines 189, 191, 193, 195, 197, 200 of
c17b2f5f9) against the `Or->And` mutation R2 left alive.

The mutation swaps `a or b` for `a and b`, so the refusal then only fires when
BOTH halves of the condition fail. Every test therefore breaks exactly ONE
half (with the other half satisfied): the unmutated code refuses, the mutant
lets the argv through and the assertion sees the planned (root-run) command.
A test that broke both halves would be refused by the mutant too and would
prove nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from debian_install_v2 import actions as actions_module
from debian_install_v2.actions import ActionError, HostActions

# The runner path the installer builds: `<scripts/debian-install-v2>/tools/...`.
RUNNER = str(
    Path(actions_module.__file__).resolve().parents[1]
    / "tools"
    / "iobench-install-runner"
)
PARTUUID = "12345678-1234-1234-1234-123456789012"


def _argv(state_dir: str, output: str, *, profile: str = "quick",
          target: str = "/dev/vda4", size: str = "8388608",
          partuuid: str = PARTUUID, executable: str = RUNNER) -> list[str]:
    """The exact 13-argument argv the installer plans (installer.py:3727-3747)."""
    return [
        executable,
        "--profile", profile,
        "--target", target,
        "--expect-size", size,
        "--expect-partuuid", partuuid,
        "--state-dir", state_dir,
        "--output", output,
    ]


def _valid_paths(tmp_path: Path) -> tuple[str, str]:
    """The one state/output pair the installer builds for the `quick` rung."""
    state_dir = str(tmp_path / "state" / "iobench" / "quick")
    output = str(tmp_path / "state" / "io-benchmark.json")
    return state_dir, output


def _assert_refused(argv: list[str], message: str) -> None:
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match=message):
        actions.run(argv)
    assert actions.planned == []


def _assert_accepted(argv: list[str]) -> None:
    actions = HostActions(dry_run=True)
    actions.run(argv)
    assert actions.planned[-1].argv == tuple(argv)


def test_runner_accepts_the_installer_built_argv(tmp_path: Path) -> None:
    """Positive control: the valid 13-argument argv is planned, not refused.

    Without it a "refused" assertion could be satisfied by an argv that no
    legal call ever builds.
    """
    state_dir, output = _valid_paths(tmp_path)
    _assert_accepted(_argv(state_dir, output))


def test_actions_line_189_refuses_another_executable_of_the_same_length(
    tmp_path: Path,
) -> None:
    """actions.py:189 `if argv[0] != expected or len(argv) != 13`.

    Or->And means the pair check only fires when the length ALSO differs, so a
    wrong executable (e.g. any other absolute path of the same basename) with
    the correct 13 arguments would be handed to root. Here only the executable
    is wrong: the length stays 13, so the unmutated `or` refuses and the
    mutant plans the foreign program.
    """
    state_dir, output = _valid_paths(tmp_path)
    _assert_refused(
        _argv(state_dir, output, executable=str(tmp_path / "iobench-install-runner")),
        "iobench installer invocation",
    )


def test_actions_line_191_refuses_an_unreleased_profile_behind_the_right_flag(
    tmp_path: Path,
) -> None:
    """actions.py:191 `if argv[1] != "--profile" or argv[2] not in _IOBENCH_PROFILES`.

    Only the profile is wrong (`thorough` is not a released rung); the flag is
    the exact `--profile` and the state directory is even named after the
    profile, so the later `<state_dir>/iobench/<rung>` checks all pass. The
    unmutated `or` refuses on the vocabulary alone; the mutant would plan the
    rung.
    """
    state_dir = str(tmp_path / "state" / "iobench" / "thorough")
    output = str(tmp_path / "state" / "io-benchmark.json")
    _assert_refused(
        _argv(state_dir, output, profile="thorough"),
        "not one of the released rungs",
    )


def test_actions_line_193_refuses_a_target_outside_dev(tmp_path: Path) -> None:
    """actions.py:193 `if argv[3] != "--target" or not re.fullmatch(r"/dev/...")`.

    The `--target` flag is exact; only the device value is not a /dev/ path.
    The unmutated `or` refuses it, the mutant hands it to the root-run runner.
    """
    state_dir, output = _valid_paths(tmp_path)
    _assert_refused(
        _argv(state_dir, output, target=str(tmp_path / "vda4")),
        "target is not a block-device path",
    )


def test_actions_line_195_refuses_a_zero_size(tmp_path: Path) -> None:
    """actions.py:195 `if argv[5] != "--expect-size" or not re.fullmatch(r"[1-9][0-9]*")`.

    The flag is exact; only the size value is out of the accepted form (a
    leading zero). The unmutated `or` refuses, the mutant accepts the size.
    """
    state_dir, output = _valid_paths(tmp_path)
    _assert_refused(
        _argv(state_dir, output, size="08388608"),
        "size is invalid",
    )


def test_actions_line_197_refuses_a_short_partuuid(tmp_path: Path) -> None:
    """actions.py:197 `if argv[7] != "--expect-partuuid" or not re.fullmatch("36")`.

    The flag is exact; only the PARTUUID value is malformed. The unmutated `or`
    refuses it, the mutant accepts any string as the partition identity check.
    """
    state_dir, output = _valid_paths(tmp_path)
    _assert_refused(
        _argv(state_dir, output, partuuid="1234"),
        "PARTUUID is invalid",
    )


def test_actions_line_200_refuses_a_relative_state_directory(
    tmp_path: Path,
) -> None:
    """actions.py:200 `if not Path(value).is_absolute() or ".." in Path(value).parts`.

    Only the absolute-path half fails: the state directory is relative (and
    the output is its consistent sibling, so the later pair checks still
    match). The unmutated `or` refuses the relative path; the mutant (which
    only refuses when BOTH halves fail) plans it, and a root-run program would
    then receive a path that resolves against the caller's cwd.
    """
    state_dir = "state/iobench/quick"
    output = "state/io-benchmark.json"
    _assert_refused(
        _argv(state_dir, output),
        "state-dir path is invalid",
    )
