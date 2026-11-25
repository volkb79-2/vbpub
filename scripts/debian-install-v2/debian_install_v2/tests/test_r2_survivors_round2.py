"""Round two of the R2-survivor regressions.

The survivors come from the R2 run on c17b2f5f9 (`--base fdf60ffdc`, mode
`changed_lines`): a mutated changed line that every test then in the tree
still passed. Every test here pins ONE such line and names it in its docstring
by the c17b2f5f9 line number.

Section 1 -- `actions.py` 189/191/193/195/197/200, the
`iobench-install-runner` allow-list. The mutation is `Or->And`, so the refusal
then only fires when BOTH halves of the condition fail. Every test therefore
breaks exactly ONE half (with the other half satisfied): the unmutated code
refuses, the mutant lets the argv through and the assertion sees the planned
(root-run) command. A test that broke both halves would be refused by the
mutant too and would prove nothing.

Section 2 -- `config.py` 502/508/509 (503/509/510 in this tree: three doc
comment lines were added above them) and `layout_policy.py` 236/476/664/729.
These are boundary tests: each calls the public loader or `disk_spec` and
asserts the value or refusal the mutated comparison/constant gets wrong.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from debian_install_v2 import actions as actions_module
from debian_install_v2 import layout_policy as lp
from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import ConfigError, load_config

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


SUBVOLUMES_WITHOUT_NOCOW = {
    "root": "*",
    "reserve_gb": 0,
    "iobench_raw_gb": False,
    "data": {"kind": "subvolumes", "volumes": [{"name": "@cow", "mount": "/srv/cow"}]},
}
# An ext4 root plan with the raw benchmark partition explicitly disabled, so
# `spec.iobench_raw_gb is None` is the whole story.
EXT4_NO_RAW_IOBENCH = {
    "root": "*",
    "reserve_gb": 0,
    "iobench_raw_gb": False,
    "data": {"kind": "none"},
}


# ------------------------------------------------------------- config.py swaps


def test_config_line_502_accepts_the_upper_bound_swap_count() -> None:
    """config.py:502 `not minimum_swap_partitions <= config.swap_partition_count <= 64`.

    LtE->Lt turns the inclusive ceiling into an exclusive one, so a plan that
    asks for the documented maximum of 64 swap partitions is refused by the
    mutant and loaded by the original. 65 is out of range either way; it is
    asserted so the boundary is pinned from both sides.
    """
    assert load_config(raw_json='{"swap_partition_count": 64}').swap_partition_count == 64
    with pytest.raises(ConfigError, match=r"swap_partition_count must be an integer from 1 to 64"):
        load_config(raw_json='{"swap_partition_count": 65}')


def test_config_line_508_the_zero_swap_rule_is_scoped_to_the_btrfs_schema() -> None:
    """config.py:508 `config.schema_version == BTRFS_SCHEMA_VERSION`.

    Eq->NotEq inverts the schema scope of the zero-swap rule. Schema 2 with
    `swap_partition_count=0` and `swap_policy=on` is a plan that owns no swap
    partition while telling the installer to lay swap out: the original refuses
    it, the mutant's first conjunct is false so the whole guard is skipped and
    the config loads.
    """
    with pytest.raises(ConfigError, match="swap_partition_count may be 0 only when swap_policy=off"):
        load_config(
            raw_json='{"schema_version": 2, "swap_partition_count": 0, "swap_policy": "on"}'
        )


def test_config_line_509_zero_swap_needs_swap_policy_off_and_a_nonzero_count_stays_legal() -> None:
    """config.py:509 `and config.swap_partition_count == 0`.

    Eq->NotEq under schema 2 does two wrong things at once: it lets the
    zero-count/swap-on plan through (first assertion) and it refuses an
    ordinary schema-2 plan that keeps swap on with a positive count (second
    assertion, which the original accepts).
    """
    with pytest.raises(ConfigError, match="swap_partition_count may be 0 only when swap_policy=off"):
        load_config(
            raw_json='{"schema_version": 2, "swap_partition_count": 0, "swap_policy": "on"}'
        )
    optimistic = load_config(
        raw_json='{"schema_version": 2, "swap_partition_count": 4, "swap_policy": "on"}'
    )
    assert optimistic.swap_partition_count == 4


# --------------------------------------------------------- layout_policy.py


def test_layout_policy_line_476_an_absent_nocow_key_means_copy_on_write() -> None:
    """layout_policy.py:476 `nocow = item.get("nocow", False)`.

    False->True makes an OMITTED `nocow` key mean NOCOW. The key is optional
    (only `name` and `mount` are required), so a subvolume that leaves it out
    would silently be created with chattr +C and get the installer's
    NOCOW-vs-config reconciliation applied to it. The original resolves the
    absent key to False; the mutant resolves it to True.
    """
    config = load_config(
        raw_json=json.dumps(
            {
                "schema_version": 2,
                "swap_policy": "off",
                "swap_partition_count": 0,
                "partitioning": SUBVOLUMES_WITHOUT_NOCOW,
            }
        )
    )
    assert config.partitioning == SUBVOLUMES_WITHOUT_NOCOW
    spec = lp.disk_spec(
        config.partitioning,
        root_fs="btrfs",
        schema_version=config.schema_version,
        swap_policy=config.swap_policy,
        swap_partition_count=config.swap_partition_count,
    )
    assert spec.data.kind == "subvolumes"
    assert spec.data.volumes[0].name == "@cow"
    assert spec.data.volumes[0].nocow is False


def test_layout_policy_line_664_ext4_without_the_benchmark_needs_no_raw_partition() -> None:
    """layout_policy.py:664 `if run_io_benchmark and spec.iobench_raw_gb is None`.

    And->Or drops the `run_io_benchmark` half, so an ext4 plan whose raw
    benchmark partition is disabled (`iobench_raw_gb=false`, hence
    `spec.iobench_raw_gb is None`) is refused as `ext4_iobench_requires_raw_partition`
    although nothing asks for an iobench partition. The original accepts that
    plan; the refusal is asserted for the case that genuinely needs the raw
    partition so both halves of the condition are pinned.
    """
    spec = lp.disk_spec(
        EXT4_NO_RAW_IOBENCH,
        root_fs="ext4",
        schema_version=2,
        swap_policy="on",
        swap_partition_count=4,
        run_io_benchmark=False,
    )
    assert spec.iobench_raw_gb is None
    with pytest.raises(lp.LayoutPolicyError, match="ext4_iobench_requires_raw_partition"):
        lp.disk_spec(
            EXT4_NO_RAW_IOBENCH,
            root_fs="ext4",
            schema_version=2,
            swap_policy="on",
            swap_partition_count=4,
            run_io_benchmark=True,
        )


def test_layout_policy_line_729_an_omitted_run_io_benchmark_leaves_the_benchmark_off() -> None:
    """layout_policy.py:729 `run_io_benchmark: bool = False` (disk_spec keyword default).

    False->True makes every caller that passes `root_fs` without the keyword
    look like a benchmark run, and the ext4 contract then refuses the plan
    (`ext4_iobench_requires_raw_partition`) because no raw partition is
    reserved. Product callers pass `run_io_benchmark` explicitly
    (installer.py:2589 `_disk_spec`); plan_preview.py:101 and wizard.py:349
    omit `root_fs` as well, so this default is observable only through the
    public signature, which is exactly what this test pins.
    """
    spec = lp.disk_spec(
        EXT4_NO_RAW_IOBENCH,
        root_fs="ext4",
        schema_version=2,
        swap_policy="on",
        swap_partition_count=4,
    )
    assert spec.iobench_raw_gb is None


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
