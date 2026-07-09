from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from conftest import fixture_frame, fixture_root
from groop.config import GroopConfig, SnapshotConfig
from groop.model import Frame
from groop.record.reader import RecordReader
from groop.record.ring import HistoryRing
from groop.snapshot.bundle import _extract_archive, create, inspect_bundle


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def _extract(bundle: Path, dst: Path) -> Path:
    out = dst / "bundle"
    out.mkdir()
    _extract_archive(bundle, out)
    return out


def test_create_snapshot_bundle_contains_bounded_frames_and_manifest(tmp_path: Path) -> None:
    config = GroopConfig(snapshots=SnapshotConfig(dir=tmp_path, frames=3))
    frame = _frame_at(103.0)
    bundle = create(
        "soulmask.slice/soulmask-paks.slice",
        HistoryRing.from_config(config),
        frame,
        config,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        previous_frames=[_frame_at(100.0), _frame_at(101.0), _frame_at(102.0)],
        providers_status={"network": {"source": "fixture"}},
        systemctl_show="MemoryHigh=300M\n",
        docker_inspect={"Id": "abc", "Name": "demo", "Config": {"Env": ["TOKEN=x"], "Labels": {"secret": "y"}}},
    )

    root = _extract(bundle, tmp_path)
    frames = list(RecordReader(root / "frames.jsonl"))
    assert [frame.ts for frame in frames] == [101.0, 102.0, 103.0]
    assert (root / "entity" / "cgroup" / "memory.current").is_file()
    assert (root / "entity" / "cgroup" / "ancestors" / "soulmask-slice" / "memory.low").is_file()
    assert (root / "entity" / "systemctl-show.txt").read_text() == "MemoryHigh=300M\n"

    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["entity_key"] == "soulmask.slice/soulmask-paks.slice"
    assert manifest["privacy_redacted"] is False
    for item in manifest["files"]:
        path = root / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]

    summary = inspect_bundle(bundle)
    assert "status: ok" in summary
    assert "frames: 3" in summary
    assert "privacy_redacted: False" in summary
    assert "notable_files:" in summary
    assert "entity/systemctl-show.txt" in summary
    assert "hash_failures: -" in summary


def test_snapshot_redacts_docker_environment_and_labels(tmp_path: Path) -> None:
    config = GroopConfig(snapshots=SnapshotConfig(dir=tmp_path, redact=True))
    bundle = create(
        "",
        HistoryRing.from_config(config),
        fixture_frame(),
        config,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        docker_inspect={
            "Id": "abc",
            "Name": "/container",
            "Image": "image:latest",
            "Config": {"Env": ["SECRET=value"], "Labels": {"secret": "value"}, "User": "1000"},
            "Mounts": [{"Source": "/private"}],
        },
    )

    root = _extract(bundle, tmp_path)
    docker = json.loads((root / "entity" / "docker-inspect.json").read_text())
    assert docker["Config"] == {"User": "1000"}
    assert "Mounts" not in docker
    assert json.loads((root / "manifest.json").read_text())["privacy_redacted"] is True


def test_snapshot_handles_empty_previous_history(tmp_path: Path) -> None:
    config = GroopConfig(snapshots=SnapshotConfig(dir=tmp_path, frames=10))
    bundle = create(
        "missing.scope",
        HistoryRing.from_config(config),
        fixture_frame(),
        config,
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
    )

    root = _extract(bundle, tmp_path)
    assert len(list(RecordReader(root / "frames.jsonl"))) == 1
    assert "status: ok" in inspect_bundle(bundle)


def test_snapshot_does_not_overwrite_same_second_bundle(tmp_path: Path) -> None:
    config = GroopConfig(snapshots=SnapshotConfig(dir=tmp_path))
    first = create("", HistoryRing.from_config(config), fixture_frame(), config, now=123.0)
    second = create("", HistoryRing.from_config(config), fixture_frame(), config, now=123.0)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_snapshot_inspect_cli(tmp_path: Path) -> None:
    config = GroopConfig(snapshots=SnapshotConfig(dir=tmp_path))
    bundle = create("", HistoryRing.from_config(config), fixture_frame(), config, cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "groop.cli", "snapshot", "inspect", str(bundle)],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert "status: ok" in proc.stdout
    assert "frames: 1" in proc.stdout


def test_snapshot_inspect_reports_hash_failures(tmp_path: Path) -> None:
    config = GroopConfig(snapshots=SnapshotConfig(dir=tmp_path))
    bundle = create("", HistoryRing.from_config(config), fixture_frame(), config, cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch")
    root = _extract(bundle, tmp_path)
    (root / "entity" / "systemctl-show.txt").write_text("corrupted\n")
    corrupt = tmp_path / "corrupt.tar"
    with tarfile.open(corrupt, "w") as tar:
        for path in sorted(root.rglob("*")):
            tar.add(path, arcname=path.relative_to(root).as_posix())

    summary = inspect_bundle(corrupt)

    assert "status: hash-mismatch:1" in summary
    assert "hash_failures: entity/systemctl-show.txt" in summary
