from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from assay import liveness_resources
from assay.liveness_resources import (
    RESOURCE_SNAPSHOT_SCHEMA_VERSION,
    compare_resource_snapshots,
    read_liveness_resources,
)


_PRESSURE_ROWS = {
    "cpu": ("some",),
    "memory": ("some", "full"),
    "io": ("some", "full"),
}


def _put_psi(root: Path, scope: str, name: str, *, base: int = 0) -> None:
    directory = root / "pressure" if scope == "host" else root
    directory.mkdir(parents=True, exist_ok=True)
    rows = _PRESSURE_ROWS[name]
    (directory / (name if scope == "host" else f"{name}.pressure")).write_text(
        "".join(
            f"{row} avg10=0.00 avg60=0.00 avg300=0.00 total={base + index}\n"
            for index, row in enumerate(rows)
        )
        + "future avg10=0.00 total=999\n",
        encoding="ascii",
    )


def _snapshot(
    tmp_path: Path,
    *,
    pid: int = 314,
    group_path: str = "/dev-gates/rg-test",
    mount_root: str = "/",
    mountpoint: str | None = None,
    counter: int = 0,
) -> tuple[Path, Path]:
    proc_root = tmp_path / "proc"
    if mountpoint is None:
        mountpoint = str(tmp_path / "sys" / "fs" / "cgroup")
    (proc_root / str(pid)).mkdir(parents=True)
    (proc_root / "self").mkdir()
    (proc_root / str(pid) / "cgroup").write_text(
        f"0::{group_path}\n", encoding="ascii"
    )
    (proc_root / "self" / "mountinfo").write_text(
        f"29 23 0:24 {mount_root} {mountpoint} rw,nosuid - cgroup2 cgroup rw\n",
        encoding="ascii",
    )
    for name in _PRESSURE_ROWS:
        _put_psi(proc_root, "host", name, base=counter)

    decoded_root = mount_root.replace("\\040", " ")
    decoded_mountpoint = mountpoint.replace("\\040", " ")
    try:
        visible_group = Path(group_path).relative_to(decoded_root)
    except ValueError:
        # The outside-visible-mount refusal test only needs a safe, unrelated
        # fixture directory; the reader must refuse before opening it.
        visible_group = Path("fixture-outside")
    group = Path(decoded_mountpoint) / visible_group
    for name in _PRESSURE_ROWS:
        _put_psi(group, "cgroup", name, base=counter)
    (group / "cpu.stat").write_text(
        f"usage_usec {counter}\nnr_periods {counter}\ninvalid\n"
        f"nr_throttled x\nthrottled_usec {counter} extra\n"
        f"nr_throttled {counter}\nthrottled_usec {counter}\n",
        encoding="ascii",
    )
    return proc_root, group


def _available() -> dict[str, Any]:
    return {
        "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION,
        "status": "available",
        "cgroup_identity": "test",
        "host_psi": {
            "cpu": {"some": 10},
            "memory": {"some": 20, "full": 30},
            "io": {"some": 40, "full": 50},
        },
        "cgroup_psi": {
            "cpu": {"some": 60},
            "memory": {"some": 70, "full": 80},
            "io": {"some": 90, "full": 100},
        },
        "cgroup_cpu": {"nr_throttled": 2, "throttled_usec": 200},
    }


def test_read_liveness_resources_maps_candidate_cgroup_and_decodes_mount_escapes(
    tmp_path: Path,
) -> None:
    mountpoint = tmp_path / "sys cgroup"
    proc_root, group = _snapshot(
        tmp_path,
        group_path="/slice name/rg-test",
        mount_root="/slice\\040name",
        mountpoint=str(mountpoint).replace(" ", "\\040"),
        counter=7,
    )

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["schema_version"] == 1
    assert result["status"] == "available"
    assert result["cgroup_identity"]
    assert result["host_psi"]["memory"] == {"some": 7, "full": 8}
    assert result["cgroup_psi"]["io"] == {"some": 7, "full": 8}
    assert result["cgroup_cpu"] == {"nr_throttled": 7, "throttled_usec": 7}
    assert group.exists()


def test_private_cgroup_namespace_root_maps_to_mountpoint(tmp_path: Path) -> None:
    proc_root, group = _snapshot(tmp_path, group_path="/")

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["status"] == "available"
    assert result["cgroup_identity"]
    assert (group / "cpu.pressure").is_file()


def test_mountinfo_parser_skips_unrelated_malformed_records(tmp_path: Path) -> None:
    proc_root, _group = _snapshot(tmp_path)
    (proc_root / "self" / "mountinfo").write_text(
        "malformed\n"
        "29 23 0:24 / /not/cgroup rw - tmpfs tmpfs rw\n"
        f"29 23 0:24 / {tmp_path}/sys/fs/cgroup rw - cgroup2 cgroup rw\n",
        encoding="ascii",
    )

    assert read_liveness_resources(314, proc_root=proc_root)["status"] == "available"


@pytest.mark.parametrize(
    ("mount_root", "mountpoint"),
    [("relative", "/sys/fs/cgroup"), ("/", "relative")],
)
def test_nonabsolute_cgroup_mount_fields_are_ignored(
    tmp_path: Path, mount_root: str, mountpoint: str
) -> None:
    proc_root, _group = _snapshot(tmp_path)
    (proc_root / "self" / "mountinfo").write_text(
        f"29 23 0:24 {mount_root} {mountpoint} rw - cgroup2 cgroup rw\n",
        encoding="ascii",
    )

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["reason"] == "cgroup-v2-mount-unavailable"


def test_relative_cgroup_escape_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        liveness_resources,
        "_read_cgroup_path",
        lambda pid, proc_root: liveness_resources.PurePosixPath("/safe/../escape"),
    )
    monkeypatch.setattr(
        liveness_resources,
        "_read_cgroup2_mount",
        lambda proc_root: (
            liveness_resources.PurePosixPath("/safe"),
            tmp_path / "cgroup",
        ),
    )

    with pytest.raises(liveness_resources.ResourceSnapshotError, match="path-invalid"):
        liveness_resources._candidate_cgroup_dir(314, tmp_path / "proc")


@pytest.mark.parametrize("pid", [0, -1, True, "314"])
def test_invalid_pid_is_explicitly_unavailable(tmp_path: Path, pid: Any) -> None:
    result = read_liveness_resources(pid, proc_root=tmp_path / "missing")

    assert result == {
        "schema_version": 1,
        "status": "unavailable",
        "reason": "candidate-pid-invalid",
    }


def test_unreadable_candidate_identity_is_not_zero_pressure(tmp_path: Path) -> None:
    result = read_liveness_resources(314, proc_root=tmp_path / "missing")

    assert result["status"] == "unavailable"
    assert result["reason"] == "candidate-cgroup-unreadable"


def test_unreadable_mountinfo_is_explicitly_unavailable(tmp_path: Path) -> None:
    proc_root, _group = _snapshot(tmp_path)
    (proc_root / "self" / "mountinfo").unlink()

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["reason"] == "cgroup-mountinfo-unreadable"


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        ("1:cpu:/x\n", "candidate-cgroup-v2-identity-unavailable"),
        ("0::relative\n", "candidate-cgroup-path-invalid"),
        ("0::/a/../b\n", "candidate-cgroup-path-invalid"),
        ("0::/x\n0::/y\n", "candidate-cgroup-v2-identity-unavailable"),
    ],
)
def test_invalid_candidate_cgroup_identity_is_unavailable(
    tmp_path: Path, contents: str, reason: str
) -> None:
    proc_root, _group = _snapshot(tmp_path)
    (proc_root / "314" / "cgroup").write_text(contents, encoding="ascii")

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["status"] == "unavailable"
    assert result["reason"] == reason


def test_candidate_cgroup_outside_visible_mount_is_unavailable(tmp_path: Path) -> None:
    proc_root, _group = _snapshot(
        tmp_path, group_path="/other/rg-test", mount_root="/visible"
    )

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["status"] == "unavailable"
    assert result["reason"] == "candidate-cgroup-outside-visible-mount"


def test_missing_cgroup_mount_is_unavailable(tmp_path: Path) -> None:
    proc_root, _group = _snapshot(tmp_path)
    (proc_root / "self" / "mountinfo").write_text(
        "29 23 0:24 / /sys/fs/cgroup rw - tmpfs tmpfs rw\n", encoding="ascii"
    )

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["status"] == "unavailable"
    assert result["reason"] == "cgroup-v2-mount-unavailable"


def test_malformed_pressure_or_missing_cpu_counters_is_unavailable(
    tmp_path: Path,
) -> None:
    proc_root, group = _snapshot(tmp_path)
    (proc_root / "pressure" / "memory").write_text(
        "some avg10=0.00 total=not-a-number\nfull avg10=0.00 total=2\n",
        encoding="ascii",
    )
    result = read_liveness_resources(314, proc_root=proc_root)
    assert result["reason"] == "memory-psi-malformed"

    # Restore PSI and make cgroup CPU counters incomplete.
    _put_psi(proc_root, "host", "memory")
    (group / "cpu.stat").write_text("usage_usec 1\n", encoding="ascii")
    result = read_liveness_resources(314, proc_root=proc_root)
    assert result["reason"] == "candidate-cpu-stat-incomplete"


@pytest.mark.parametrize(
    ("scope", "name", "reason"),
    [
        ("host", "cpu", "cpu-psi-unreadable"),
        ("cgroup", "io", "io-psi-incomplete"),
    ],
)
def test_missing_or_incomplete_pressure_rows_are_unavailable(
    tmp_path: Path, scope: str, name: str, reason: str
) -> None:
    proc_root, group = _snapshot(tmp_path)
    target = (
        proc_root / "pressure" / name
        if scope == "host"
        else group / f"{name}.pressure"
    )
    if scope == "host":
        target.unlink()
    else:
        target.write_text("some avg10=0.00 total=1\n", encoding="ascii")

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["status"] == "unavailable"
    assert result["reason"] == reason


def test_missing_cgroup_cpu_stat_is_unavailable(tmp_path: Path) -> None:
    proc_root, group = _snapshot(tmp_path)
    (group / "cpu.stat").unlink()

    result = read_liveness_resources(314, proc_root=proc_root)

    assert result["reason"] == "candidate-cpu-stat-unreadable"


def test_compare_requires_a_pair_of_complete_same_identity_snapshots() -> None:
    current = _available()

    assert compare_resource_snapshots(None, current) == ("unknown", {})
    assert compare_resource_snapshots(current, {**current, "schema_version": 2}) == (
        "unknown",
        {},
    )
    assert compare_resource_snapshots(
        current, {**current, "schema_version": RESOURCE_SNAPSHOT_SCHEMA_VERSION - 1}
    ) == ("unknown", {})
    assert compare_resource_snapshots(
        current, {**current, "status": "unavailable"}
    ) == ("unknown", {})
    assert compare_resource_snapshots(
        current, {**current, "cgroup_identity": "other"}
    ) == ("unknown", {})
    assert compare_resource_snapshots(current, _available()) == ("clear", {})


@pytest.mark.parametrize("source", ["host", "throttle"])
def test_negative_counters_are_not_a_clear_interval(source: str) -> None:
    snapshot = _available()
    if source == "host":
        snapshot["host_psi"]["memory"]["full"] = -1
    else:
        snapshot["cgroup_cpu"]["nr_throttled"] = -1

    assert compare_resource_snapshots(snapshot, snapshot) == ("unknown", {})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(host_psi=None),
        lambda item: item["host_psi"].pop("cpu"),
        lambda item: item.update(cgroup_psi=None),
        lambda item: item["host_psi"]["cpu"].update(some=True),
        lambda item: item["host_psi"]["cpu"].update(some=11.0),
        lambda item: item["host_psi"]["cpu"].update(some=9),
        lambda item: item.update(cgroup_cpu=None),
        lambda item: item["cgroup_cpu"].pop("nr_throttled"),
        lambda item: item["cgroup_cpu"].update(throttled_usec=False),
        lambda item: item["cgroup_cpu"].update(throttled_usec=199),
    ],
)
def test_counter_incompleteness_or_reset_is_unknown(mutate) -> None:
    previous = _available()
    current = _available()
    mutate(current)

    assert compare_resource_snapshots(previous, current) == ("unknown", {})


@pytest.mark.parametrize(
    ("scope", "name", "row", "key"),
    [
        ("host_psi", "cpu", "some", "host_psi.cpu.some_us"),
        ("host_psi", "memory", "full", "host_psi.memory.full_us"),
        ("cgroup_psi", "io", "some", "cgroup_psi.io.some_us"),
        ("cgroup_cpu", None, "nr_throttled", "cgroup_cpu.nr_throttled"),
        ("cgroup_cpu", None, "throttled_usec", "cgroup_cpu.throttled_usec"),
    ],
)
def test_any_positive_counter_delta_marks_interval_stalled(
    scope: str, name: str | None, row: str, key: str
) -> None:
    previous = _available()
    current = _available()
    if name is None:
        current[scope][row] += 1
    else:
        current[scope][name][row] += 1

    assert compare_resource_snapshots(previous, current) == ("stalled", {key: 1})
