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
from debian_install_v2.config import Config, ConfigError, load_config
from debian_install_v2.installer import HostIdentityRefusal, Installer, InstallerError
from debian_install_v2.state import StateStore
from debian_install_v2.tests.test_btrfs_root import (
    VALID_PARTITIONING,
    BoundaryActions,
    RecordingState,
    _btrfs_installer,
    _resume_btrfs_installer,
)

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


# ------------------------------------------------------- installer.py 343..856
#
# Those survivors all live in the report / resume / btrfs-subvolume machinery.
# The tests drive the public method that contains the line, through the same
# fake boundary the neighbouring tests use (`BoundaryActions` validates every
# argv and runs nothing on the host), and assert the observable result the one
# mutated token changes.

BTRFS_TOP = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE,FSROOT", "/.top")
ROOT_ENTRY = "UUID=root / btrfs noatime,compress=zstd:3 0 0"


def test_installer_line_343_one_bracket_in_the_root_source_is_a_subvolume_suffix(
    tmp_path: Path,
) -> None:
    """installer.py:343 `if "[" in source or "]" in source`.

    Or->And only reports the btrfs-subvolume suffix when BOTH bracket kinds
    appear. A source carrying just one bracket is still a mangled findmnt
    source (real suffixes look like `/dev/vda2[/@os-...]`); the original names
    the subvolume-suffix refusal, while the mutant falls through to the
    unrelated "cannot derive root disk and partition number" error.
    """
    findmnt = ("/usr/bin/findmnt", "-n", "-o", "SOURCE,FSTYPE", "--nofsroot", "/")
    actions = BoundaryActions(outputs={findmnt: "/dev/vda2] ext4\n"})
    installer = Installer(Config(state_dir=str(tmp_path / "state")), actions, inspect_host=False)

    with pytest.raises(HostIdentityRefusal, match="subvolume suffix"):
        installer._discover_root()


def _btrfs_report_plan(**overrides) -> dict:
    plan = {
        "release": "trixie",
        "root_fs": "btrfs",
        "root_device": "/dev/vda2",
        "new_root_size_sectors": 0,
        "root_shrink_check": "not_applicable",
        "root_policy": "untouched",
        "partitioning": {"root": "*", "reserve_gb": 0, "iobench_raw_gb": False},
        "data_partitions": [],
        "data_subvolumes": [{"name": "@cow", "mount": "/srv/cow", "nocow": False}],
        "iobench_partition": None,
        "plan_table": [],
        "swap_policy": "off",
        "swap_total_gb": None,
        "swap_retained_before_root_gb": None,
        "swap_after_root_gb": None,
        "swap_partition_count": 0,
        "swap_partitions": [],
        "sfdisk_plan": "",
    }
    plan.update(overrides)
    return plan


def _ext4_report_plan(**overrides) -> dict:
    plan = {
        "release": "trixie",
        "root_fs": "ext4",
        "root_device": "/dev/vda3",
        "new_root_size_sectors": 16777216,
        "root_shrink_check": "not required",
        "root_policy": "resize",
        "partitioning": {"root": "*", "reserve_gb": 0, "iobench_raw_gb": False},
        "data_partitions": [
            {"device": "/dev/vda4", "start": 0, "sectors": 1, "partlabel": "MDT_COW"},
            {"device": "/dev/vda5", "start": 0, "sectors": 1, "partlabel": "iobench-raw"},
        ],
        "data_subvolumes": [],
        "iobench_partition": None,
        "plan_table": [{"entry": "root", "gib": 8.0, "start": 100}],
        "swap_policy": "on",
        "swap_total_gb": 8,
        "swap_retained_before_root_gb": 0.0,
        "swap_after_root_gb": 8.0,
        "swap_partition_count": 4,
        "swap_partitions": [{"device": "/dev/vda6", "start": 200, "sectors": 524288, "label": "vbpub-swap1"}],
        "sfdisk_plan": "/dev/vda6 type=0657fd6d start=200 size=524288",
    }
    plan.update(overrides)
    return plan


def _report_message(tmp_path: Path, monkeypatch, plan: dict) -> str:
    installer = Installer(
        Config(state_dir=str(tmp_path / "state")), BoundaryActions(), inspect_host=False
    )
    monkeypatch.setattr(installer, "show_plan", lambda: plan)
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda _installer: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda _facts: "")
    return installer._initial_report_message()


SORTED_PARTITIONING = '{"iobench_raw_gb": false, "reserve_gb": 0, "root": "*"}'


def test_installer_line_426_a_btrfs_root_reports_the_retained_iso_layout(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:426 `if plan.get("root_fs") == "btrfs"` (root_detail).

    Eq->NotEq renders a btrfs plan as an ext4 resize ("-> 0 sectors") and an
    ext4 plan as the retained btrfs layout. Only the btrfs branch is asserted
    here (line 449 pins the matching top-level branch).
    """
    message = _report_message(tmp_path, monkeypatch, _btrfs_report_plan())
    assert "ISO-installed btrfs layout retained" in message
    assert "-> 0 sectors" not in message


def test_installer_line_438_btrfs_data_is_reported_instead_of_a_partition_table(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:438 `if plan.get("root_fs") == "btrfs"` (partitioning details).

    Eq->NotEq sends a btrfs plan down the ext4 branch, which then announces
    "Partitioning: ... 0 planned data/raw partition(s)" for a plan that has no
    partition table at all.
    """
    message = _report_message(tmp_path, monkeypatch, _btrfs_report_plan())
    assert "Btrfs data:" in message
    assert "Partitioning:" not in message


def test_installer_line_440_btrfs_data_json_is_key_sorted(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:440 `json.dumps(plan["partitioning"], sort_keys=True)` (btrfs).

    True->False renders the operator's dict in its insertion order
    (root, reserve_gb, iobench_raw_gb), so two equivalent configs would print
    differently. The plan's dict is deliberately not in sorted order.
    """
    message = _report_message(tmp_path, monkeypatch, _btrfs_report_plan())
    assert SORTED_PARTITIONING in message


def test_installer_line_445_partitioning_json_is_key_sorted(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:445 `json.dumps(plan["partitioning"], sort_keys=True)` (ext4).

    True->False prints the ext4 plan's partitioning object in insertion order
    instead of the canonical sorted rendering.
    """
    message = _report_message(tmp_path, monkeypatch, _ext4_report_plan())
    assert "Partitioning:" in message
    assert SORTED_PARTITIONING in message


def test_installer_line_449_a_btrfs_root_has_no_sfdisk_table(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:449 `if plan.get("root_fs") == "btrfs"`.

    Eq->NotEq falls into the ext4 table branch and prints "Resolved disk
    table (GiB):" plus an empty sfdisk plan for a btrfs root that never gets
    a partition table. `sfdisk_plan` is present in the plan so the mutant
    produces the wrong table instead of a KeyError.
    """
    message = _report_message(tmp_path, monkeypatch, _btrfs_report_plan())
    assert "Btrfs root: ISO layout retained; no sfdisk plan" in message
    assert "Resolved disk table" not in message


def test_installer_line_457_swap_off_plans_report_no_swap_allocation(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:457 `if plan["swap_policy"] == "on"`.

    Eq->NotEq adds a "Swap allocation:" line to a swap-off ext4 plan (the
    retained/after figures are then formatted from zeros, so the mutant prints
    a real line rather than crashing).
    """
    plan = _ext4_report_plan(
        swap_policy="off", swap_total_gb=None, swap_partition_count=0,
        swap_partitions=[], swap_retained_before_root_gb=0.0, swap_after_root_gb=0.0,
    )
    message = _report_message(tmp_path, monkeypatch, plan)
    assert "Swap allocation" not in message
    assert "Swap partition:" not in message


def test_installer_line_660_a_completed_rerun_still_reports_stage2_complete(
    tmp_path: Path,
) -> None:
    """installer.py:660 `self._stage2_complete = True` (completed-rerun early return).

    The marker is created AFTER the installer was constructed, so the
    constructor's own `_completion_marker.exists()` check is False and line
    660 is the only thing that reports the completed state. True->False leaves
    `stage2_complete` False although the run is a completed no-op.
    """
    installer = _btrfs_installer(tmp_path)
    marker = Path(installer.config.state_dir) / "stage2_done"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()

    assert installer.resume() is False
    assert installer.stage2_complete is True


def _failing_resume_installer(tmp_path: Path, monkeypatch, stage2) -> Installer:
    installer = _resume_btrfs_installer(tmp_path, BoundaryActions(), monkeypatch)
    monkeypatch.setattr(installer, "_stage2", stage2)
    return installer


def _ext4_installer(tmp_path: Path, monkeypatch) -> Installer:
    """An ext4, schema-2 installer wired to a real StateStore and the fake boundary."""
    config = Config(
        schema_version=2,
        swap_policy="on",
        swap_partition_count=4,
        credential_mode="systemd",
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
    )
    installer = Installer(config, BoundaryActions(), inspect_host=False)
    installer.root_fs = "ext4"
    installer.root_disk = "vda"
    installer.root_partition_path = "/dev/vda3"
    installer.root_number = 3
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    installer.state = store
    monkeypatch.setattr(installer, "_secure_bootstrap_files", lambda: None)
    return installer


def test_installer_line_739_a_post_stage2_failure_keeps_the_completion_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:739 `stage2_returned = True`.

    True->False makes the failure path believe stage2 never returned, so an
    exception from the controller-key step (which runs only after stage2
    succeeded and the marker was written) unlinks the completion marker and
    tells the next run the install failed.
    """
    installer = _failing_resume_installer(tmp_path, monkeypatch, lambda: None)
    marker = Path(installer.config.state_dir) / "stage2_done"
    installer._stage2 = marker.touch
    monkeypatch.setattr(
        installer, "_remove_controller_ssh_key",
        lambda: (_ for _ in ()).throw(RuntimeError("key removal boom")),
    )

    with pytest.raises(RuntimeError, match="key removal boom"):
        installer.resume()
    assert marker.exists()


def test_installer_line_744_a_completed_stage2_is_reported_as_complete(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:744 `self._stage2_complete = True` (successful-stage2 path).

    True->False leaves an ext4 resume that just wrote its completion marker
    reporting `stage2_complete` False, so the caller believes a finished run
    is still incomplete.
    """
    installer = _ext4_installer(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "_stage2", lambda: None)

    assert installer.resume() is True
    assert installer.stage2_complete is True


def test_installer_line_783_a_failure_after_a_returned_stage2_keeps_the_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:783 `if self._is_btrfs_root() and not stage2_returned`.

    And->Or unlinks the completion marker for ANY btrfs failure, including one
    from the retained-key prune that runs only after stage2 returned and the
    marker exists. The original keeps the marker because stage2 itself did
    finish.
    """
    installer = _failing_resume_installer(tmp_path, monkeypatch, lambda: None)
    marker = Path(installer.config.state_dir) / "stage2_done"
    installer._stage2 = marker.touch
    monkeypatch.setattr(installer, "_controller_key_retained", lambda: True)
    monkeypatch.setattr(
        installer, "_prune_stale_controller_keys",
        lambda: (_ for _ in ()).throw(RuntimeError("prune boom")),
    )

    with pytest.raises(RuntimeError, match="prune boom"):
        installer.resume()
    assert marker.exists()


def test_installer_line_784_unlink_tolerates_an_absent_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:784 `self._completion_marker.unlink(missing_ok=True)`.

    True->False makes the failure path raise FileNotFoundError from the unlink
    when stage2 failed before any marker existed, hiding the real stage2 error
    from the operator and from the failure notification.
    """
    def stage2_boom() -> None:
        raise RuntimeError("stage2 boom")

    installer = _failing_resume_installer(tmp_path, monkeypatch, stage2_boom)
    assert not (Path(installer.config.state_dir) / "stage2_done").exists()

    with pytest.raises(RuntimeError, match="stage2 boom"):
        installer.resume()


def test_installer_line_785_a_failed_stage2_is_not_complete(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:785 `self._stage2_complete = False` (failure path).

    False->True reports a failed stage2 as complete even though its exception
    propagated and the state records the failure.
    """
    def stage2_boom() -> None:
        raise RuntimeError("stage2 boom")

    installer = _failing_resume_installer(tmp_path, monkeypatch, stage2_boom)
    with pytest.raises(RuntimeError, match="stage2 boom"):
        installer.resume()
    assert installer.stage2_complete is False


def test_installer_line_820_a_skip_step_without_detail_is_not_summarised(
    tmp_path: Path,
) -> None:
    """installer.py:820 `... and step.get("detail")`.

    And->Or accepts a skipped step that carries no `detail` (its `get` returns
    None), then formats `step["detail"]` and raises KeyError instead of
    silently leaving that step out of the skip summary.
    """
    installer = _btrfs_installer(
        tmp_path, state=RecordingState({"root_resize": {"status": "skipped"}})
    )
    assert installer._completion_skip_summary() == ""


def test_installer_line_856_show_plan_carries_the_configured_partitioning(
    tmp_path: Path,
) -> None:
    """installer.py:856 `"partitioning": self.config.partitioning or {}`.

    Or->And turns any non-empty partitioning dict into `{}` (a truthy dict
    `and {}` is `{}`), so the btrfs plan preview loses the layout the operator
    configured.
    """
    installer = _btrfs_installer(tmp_path, partitioning=VALID_PARTITIONING)
    assert installer.show_plan()["partitioning"] == VALID_PARTITIONING


# -------------------------------------------------- installer.py 3803..4135..5397
#
# The same fake boundary: `BoundaryActions` validates every argv and runs
# nothing on the host, and the private method that contains the line is called
# directly so the observable effect of exactly that token is asserted.

FINDMNT_COW = ("/usr/bin/findmnt", "-rn", "-o", "SOURCE,FSTYPE,TARGET,FSROOT", "/srv/cow")


def _btrfs_health_gate(tmp_path: Path, monkeypatch, *, existing: bool, size: int) -> Installer:
    log = tmp_path / "stage2.log"
    if existing:
        log.write_text("x" * size, encoding="utf-8")
    installer = _btrfs_installer(
        tmp_path,
        actions=BoundaryActions(),
        config_overrides={"stage2_output": str(log)},
    )
    monkeypatch.setattr(installer, "_health_gate_zswap", lambda: None)
    monkeypatch.setattr(installer, "_health_gate_iocost", lambda: None)
    return installer


def test_installer_line_3803_a_missing_stage2_log_is_a_named_refusal(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:3803 `not (output_file.is_file() and output_file.stat().st_size >= 0)`.

    And->Or makes the guard evaluate `output_file.stat()` for a file that does
    not exist, so the health gate dies with FileNotFoundError instead of its
    own "stage2 log does not exist" InstallerError. `dry_run` is False here,
    which is the only branch where the guard runs at all.
    """
    installer = _btrfs_health_gate(tmp_path, monkeypatch, existing=False, size=0)

    with pytest.raises(InstallerError, match="stage2 log does not exist"):
        installer._health_gate_swap_devices()


def test_installer_line_3803_an_existing_empty_stage2_log_passes_the_health_gate(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:3803 the `>= 0` comparison (GtE->Gt).

    A stage-two log that exists and is empty (size 0) satisfies the original
    bound; `> 0` refuses it, so the health gate fails on a perfectly ordinary
    just-created log.
    """
    installer = _btrfs_health_gate(tmp_path, monkeypatch, existing=True, size=0)

    installer._health_gate_swap_devices()

    step = installer.state.data["steps"]["health_gate"]
    assert step["status"] == "success"


def test_installer_line_4044_recovering_nocow_is_a_dangerous_action(
    tmp_path: Path,
) -> None:
    """installer.py:4044 the `dangerous=True` of the NOCOW-recovery chattr call.

    True->False records `chattr +C` on an existing subvolume as a harmless
    action, so an operator confirming a plan would not see it as one of the
    mutations that change stored data placement.
    """
    show = ("/usr/bin/btrfs", "subvolume", "show", "/.top/@cow")
    lsattr = ("/usr/bin/lsattr", "-d", "/.top/@cow")
    find = (
        "/usr/bin/find", "/.top/@cow",
        "-mindepth", "1", "-maxdepth", "1", "-print", "-quit",
    )
    chattr = ("/usr/bin/chattr", "+C", "/.top/@cow")
    actions = BoundaryActions(
        outputs={show: "Name: @cow\n", lsattr: "---------------------- /.top/@cow\n"},
        existing={"/.top/@cow"},
    )
    installer = _btrfs_installer(tmp_path, actions=actions)

    installer._ensure_btrfs_subvolume("@cow", True)

    recovered = [action for action in actions.planned if action.argv == chattr]
    assert recovered and recovered[0].dangerous is True


def test_installer_line_4065_a_commented_root_line_is_not_a_fstab_entry(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4065 `if not stripped or stripped.startswith("#")`.

    Or->And only skips a line that is BOTH empty and comment-marked, so a
    commented-out root entry is parsed as a real one: `#UUID=1234 / btrfs ...`
    has fields[1] == "/" and fields[2] == "btrfs" and the mutant returns it as
    the root source. The original raises its named missing-entry refusal.
    """
    installer = _btrfs_installer(tmp_path)
    monkeypatch.setattr(
        installer, "_read_fstab_lines", lambda: ["#UUID=1234 / btrfs defaults 0 0"]
    )

    with pytest.raises(InstallerError, match="btrfs_root_fstab_entry_missing"):
        installer._root_btrfs_fstab()


def test_installer_line_4068_a_four_field_root_line_is_a_root_entry(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4068 the `len(fields) >= 4` comparison (GtE->Gt).

    An fstab root line carrying exactly the four fields the parser needs
    (source, mount, type, options) is an ordinary entry; `> 4` insists on a
    dump/pass pair it never reads and refuses the line.
    """
    installer = _btrfs_installer(tmp_path)
    monkeypatch.setattr(installer, "_read_fstab_lines", lambda: ["UUID=x / btrfs defaults"])

    assert installer._root_btrfs_fstab() == ("UUID=x", "defaults")


def test_installer_line_4068_a_short_line_is_not_parsed_as_a_root_entry(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4068 the first `and` (And->Or): `>= 4 and fields[1] == "/"`.

    Or lets a three-field line through on its mount column alone, and the
    parser then indexes fields[3] and dies with IndexError instead of the
    named missing-entry refusal.
    """
    installer = _btrfs_installer(tmp_path)
    monkeypatch.setattr(installer, "_read_fstab_lines", lambda: ["UUID=x / btrfs"])

    with pytest.raises(InstallerError, match="btrfs_root_fstab_entry_missing"):
        installer._root_btrfs_fstab()


def test_installer_line_4068_a_non_root_mount_is_not_a_root_entry(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4068 the second `and` (And->Or): `fields[1] == "/" and fields[2] == "btrfs"`.

    Or accepts a btrfs line whose mount is some other directory (`/srv`),
    reporting that data mount as the root source and option set.
    """
    installer = _btrfs_installer(tmp_path)
    monkeypatch.setattr(
        installer, "_read_fstab_lines", lambda: ["UUID=x /srv btrfs defaults 0 0"]
    )

    with pytest.raises(InstallerError, match="btrfs_root_fstab_entry_missing"):
        installer._root_btrfs_fstab()


def test_installer_line_4076_empty_option_parts_are_dropped_from_the_subvolume_entry() -> None:
    """installer.py:4076 `if part and not part.startswith("subvol=")`.

    And->Or keeps an empty comma component (an empty string is falsy, so the
    second half alone is true) and would also keep a stale `subvol=` option
    next to the one it appends. The rendered fstab line must carry neither an
    empty component nor a second subvol= option.
    """
    installer = Installer.__new__(Installer)  # no host state needed by the helper
    volume = lp.DataVolume("@cow", lp.STAR, "btrfs", "/srv/cow", None, nocow=False)

    entry = installer._btrfs_subvolume_fstab_entry(
        "UUID=root", "noatime,,compress=zstd:3,subvol=@os-000001", volume
    )

    assert entry == (
        "UUID=root /srv/cow btrfs noatime,compress=zstd:3,user_subvol_rm_allowed,"
        "subvol=@cow 0 0"
    )
    assert ",," not in entry


def _subvolume_rewrite(tmp_path: Path, monkeypatch, fstab_lines: list[str], actions) -> Installer:
    """Drive the real `_configure_btrfs_subvolumes` fstab rewrite with stubs."""
    installer = _btrfs_installer(tmp_path, partitioning=VALID_PARTITIONING, actions=actions)
    monkeypatch.setattr(installer, "_require_btrfs_top_level", lambda: None)
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        installer, "_ensure_btrfs_subvolume", lambda name, nocow: f"/.top/{name}"
    )
    monkeypatch.setattr(installer, "_read_fstab_lines", lambda: list(fstab_lines))
    installer._configure_btrfs_subvolumes(installer._data_area_cfg())
    return installer


def test_installer_line_4099_a_commented_fstab_entry_survives_the_rewrite(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4099 the first `and` (And->Or): `line.strip() and not line.lstrip().startswith("#")`.

    Or treats a comment line as rewritable because `line.strip()` is truthy
    alone, so a commented-out entry for a configured mount is deleted from
    /etc/fstab even though its mount name merely matches.
    """
    actions = BoundaryActions(errors={FINDMNT_COW: ActionError("not mounted")})
    installer = _subvolume_rewrite(
        tmp_path, monkeypatch,
        [ROOT_ENTRY, "# /srv/cow btrfs defaults 0 0"], actions,
    )

    assert "# /srv/cow btrfs defaults 0 0" in actions.files["/etc/fstab"].splitlines()


def test_installer_line_4100_a_single_token_fstab_line_is_kept(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4100 the `and` before `len(line.split()) >= 2` (And->Or).

    Or drops any non-comment line as soon as either the non-comment half or
    the two-field half holds; a stray single-token line is then silently
    removed from the rewritten /etc/fstab.
    """
    actions = BoundaryActions(errors={FINDMNT_COW: ActionError("not mounted")})
    installer = _subvolume_rewrite(
        tmp_path, monkeypatch, [ROOT_ENTRY, "UUID=x"], actions,
    )

    assert "UUID=x" in actions.files["/etc/fstab"].splitlines()


def test_installer_line_4100_a_two_field_entry_for_a_wanted_mount_is_replaced(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4100 the `>= 2` comparison (GtE->Gt).

    A two-field line naming a configured mount is the stale entry this rewrite
    exists to replace; `> 2` keeps it next to the fresh entry, leaving two
    lines for the same mount.
    """
    actions = BoundaryActions(errors={FINDMNT_COW: ActionError("not mounted")})
    installer = _subvolume_rewrite(
        tmp_path, monkeypatch, [ROOT_ENTRY, "UUID=x /srv/cow"], actions,
    )

    assert "UUID=x /srv/cow" not in actions.files["/etc/fstab"].splitlines()


def test_installer_line_4101_an_entry_for_another_mount_is_kept(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4101 the `and` before `line.split()[1] in wanted` (And->Or).

    Or drops every line that merely has two fields, so unrelated entries
    (`/var/lib/data`) are deleted from /etc/fstab by a subvolume rewrite that
    never had anything to do with them.
    """
    actions = BoundaryActions(errors={FINDMNT_COW: ActionError("not mounted")})
    installer = _subvolume_rewrite(
        tmp_path, monkeypatch, [ROOT_ENTRY, "UUID=x /var/lib/data ext4 defaults 0 0"], actions,
    )

    assert "UUID=x /var/lib/data ext4 defaults 0 0" in actions.files["/etc/fstab"].splitlines()


def test_installer_line_4121_a_mismatched_mount_target_is_refused(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4121 the `or` before `row[2] != mount`.

    Or->And pairs the target check with the FSROOT check, so a findmnt row
    for a DIFFERENT target (`/srv/cowX`) with the right FSROOT (`/@cow`) is
    accepted as the configured mount and no mount action is planned.
    """
    actions = BoundaryActions(outputs={
        FINDMNT_COW: "/dev/vda2[/@cow] btrfs /srv/cowX /@cow\n",
    })
    installer = _btrfs_installer(tmp_path, partitioning=VALID_PARTITIONING, actions=actions)
    monkeypatch.setattr(installer, "_require_btrfs_top_level", lambda: None)
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        installer, "_ensure_btrfs_subvolume", lambda name, nocow: f"/.top/{name}"
    )
    monkeypatch.setattr(installer, "_read_fstab_lines", lambda: [ROOT_ENTRY])

    with pytest.raises(InstallerError, match="btrfs_subvolume_mount_differs"):
        installer._configure_btrfs_subvolumes(installer._data_area_cfg())
    assert ("/usr/bin/mount", "/srv/cow") not in [a.argv for a in actions.planned]


def test_installer_line_4124_the_refusal_quotes_the_findmnt_row(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4124 `found = " ".join(row) or "<empty>"`.

    Or->And replaces any non-empty row with the literal "<empty>", so the
    refusal an operator reads no longer says which mount was actually found.
    """
    row = "/dev/vda2[/@rootfs] btrfs /srv/cow /@rootfs"
    actions = BoundaryActions(outputs={FINDMNT_COW: row + "\n"})
    installer = _btrfs_installer(tmp_path, partitioning=VALID_PARTITIONING, actions=actions)
    monkeypatch.setattr(installer, "_require_btrfs_top_level", lambda: None)
    monkeypatch.setattr(installer, "_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        installer, "_ensure_btrfs_subvolume", lambda name, nocow: f"/.top/{name}"
    )
    monkeypatch.setattr(installer, "_read_fstab_lines", lambda: [ROOT_ENTRY])

    with pytest.raises(InstallerError, match="btrfs_subvolume_mount_differs") as exc_info:
        installer._configure_btrfs_subvolumes(installer._data_area_cfg())
    assert row in str(exc_info.value)
    assert "<empty>" not in str(exc_info.value)


def test_installer_line_4135_mounting_a_data_subvolume_is_dangerous(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:4135 the `dangerous=True` of the `mount` planned action.

    True->False records the mount of a top-level data subvolume as a harmless
    action, hiding a real mount mutation from the plan a caller inspects.
    """
    actions = BoundaryActions(errors={FINDMNT_COW: ActionError("not mounted")})
    _subvolume_rewrite(tmp_path, monkeypatch, [ROOT_ENTRY], actions)

    planned = [action for action in actions.planned if action.argv == ("/usr/bin/mount", "/srv/cow")]
    assert planned and planned[0].dangerous is True


def test_installer_line_5397_a_data_kind_none_plan_does_not_configure_a_data_area(
    tmp_path: Path, monkeypatch,
) -> None:
    """installer.py:5397 `if self._data_area_cfg().kind != "none"`.

    NotEq->Eq inverts the guard so the btrfs stage two runs the whole data-area
    configuration (packages, partitioning, fstab) exactly for the plan that
    declares it has no data area.
    """
    installer = _btrfs_installer(
        tmp_path,
        partitioning={
            "root": "*", "reserve_gb": 0, "iobench_raw_gb": False,
            "data": {"kind": "none"},
        },
    )
    for name in (
        "_configure_control_plane_memory_min", "_packages", "_configure_zswap",
        "_configure_cgroup2_flags", "_configure_ksm", "_configure_oomd",
        "_configure_fstrim", "_configure_auto_reboot", "_configure_iocost",
        "_health_gate_swap_devices", "_verify_control_plane_memory_min",
        "_run_cpu_benchmark", "_release_apt_timers", "_cleanup_cloud_init_cache",
    ):
        monkeypatch.setattr(installer, name, lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(installer, "_configure_data_area", lambda: calls.append(True))

    installer._stage2_btrfs()

    assert calls == []
