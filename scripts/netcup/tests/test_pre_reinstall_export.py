"""The destructive-reinstall evidence exporter is exercised with fake SSH only."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path
from subprocess import CompletedProcess

import pytest


NETCUP_DIR = Path(__file__).resolve().parents[1]
EXPORTER_PATH = NETCUP_DIR / "pre-reinstall-export.py"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("netcup_pre_reinstall_export", EXPORTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def test_export_calls_api_guard_before_pinned_ssh_and_checks_remote_identity(tmp_path):
    exporter = _load_exporter()
    data = _archive({
        "partition/sfdisk-d.txt": b"label: gpt\n",
        "partition/lsblk-b-J.json": b'{"blockdevices": []}\n',
        "partition/blkid.txt": b"/dev/vda3: UUID=example\n",
        "etc/fstab": b"UUID=example / ext4 defaults 0 1\n",
        "installer/state/state.json": b'{"stage": "complete"}\n',
        "iobench/result.json": b'{"schema": 1}\n',
        "iogovmon/state.json": b'{"schema": 1}\n',
        "docker/volume-names.txt": b"campaign-cache\n",
        "etc/mdt/host-setup.env": b"HOST_ROLE=dedicated-dev\nACCESS_TOKEN=do-not-copy\n",
        "runner/runner.env": b"RUNNER_USER=runner\nRUNNER_PASSWORD=also-do-not-copy\n",
    })
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CompletedProcess(argv, 0, stdout=data, stderr=b"")

    exported = exporter.export_snapshot(
        "v1001", "20261107", root=tmp_path, runner=fake_run
    )

    assert len(calls) == 1
    command = calls[0][0][-1]
    assert command.index("v1001_api_check") < command.index("v1001_ssh")
    assert str(exporter.ENV_HELPER) in command
    assert "v1001_ssh root@\"$V1001_IP\"" in command
    assert 'checked(["hostname", "-f"])' in exporter.REMOTE_COLLECTOR
    assert 'checked(["hostnamectl", "--static"])' in exporter.REMOTE_COLLECTOR
    assert "/etc/vbpub/server-id" in exporter.REMOTE_COLLECTOR
    assert exporter.REMOTE_COLLECTOR.index("facts = identity()") < exporter.REMOTE_COLLECTOR.index(
        '("partition/sfdisk-d.txt", ["sfdisk", "-d", "/dev/vda"])'
    )
    assert exported == tmp_path / "v1001" / "pre-reinstall-20261107"


def test_export_redacts_sensitive_values_and_hashes_every_exported_data_file(tmp_path):
    exporter = _load_exporter()
    data = _archive({
        "etc/mdt/host-setup.env": (
            b"HOST_ROLE=dedicated-dev\nACCESS_TOKEN=sentinel-token\n"
            b"ROOT_PASSWORD=sentinel-password\nAPI_SECRET=sentinel-api-secret\nCOMMENT=harmless\n"
        ),
        "runner/runner.env": b"RUNNER_USER=runner\n",
        "iogovmon/state.json": b'{"credential_secret": "sentinel-json-secret"}\n',
        "logs/output.txt": (
            b"safe\n-----BEGIN OPENSSH PRIVATE KEY-----\n"
            b"sentinel-private-key\n-----END OPENSSH PRIVATE KEY-----\n"
        ),
    })

    exported = exporter.export_snapshot(
        "r1002", "20261107", root=tmp_path,
        runner=lambda argv, **kwargs: CompletedProcess(argv, 0, stdout=data, stderr=b""),
    )
    files = [path for path in exported.rglob("*") if path.is_file()]
    manifest = exported / "SHA256SUMS"
    assert manifest in files
    manifest_rows = manifest.read_text(encoding="utf-8").splitlines()
    expected_names = sorted(path.relative_to(exported).as_posix() for path in files if path != manifest)
    assert [row.split("  ", 1)[1] for row in manifest_rows] == expected_names
    for row in manifest_rows:
        digest, name = row.split("  ", 1)
        assert hashlib.sha256((exported / name).read_bytes()).hexdigest() == digest

    content = b"\n".join(path.read_bytes() for path in files)
    for secret in (
        b"sentinel-token", b"sentinel-password", b"sentinel-api-secret", b"sentinel-json-secret",
        b"sentinel-private-key", b"BEGIN OPENSSH PRIVATE KEY",
    ):
        assert secret not in content
    assert b"HOST_ROLE=dedicated-dev" in content


@pytest.mark.parametrize("host", ["nano1", "../v1001", ""])
def test_export_rejects_non_allowlisted_host(tmp_path, host):
    exporter = _load_exporter()
    with pytest.raises(ValueError, match="r1002 or v1001"):
        exporter.export_snapshot(host, "20261107", root=tmp_path, runner=lambda *a, **k: None)


@pytest.mark.parametrize("date", ["2026-11-07", "../20261107", "20260230", ""])
def test_export_rejects_invalid_date_before_any_ssh(tmp_path, date):
    exporter = _load_exporter()
    calls = []
    with pytest.raises(ValueError, match="YYYYMMDD"):
        exporter.export_snapshot("v1001", date, root=tmp_path, runner=lambda *a, **k: calls.append(a))
    assert calls == []


def test_remote_collection_lists_volume_names_without_inspecting_contents():
    exporter = _load_exporter()
    remote = exporter.REMOTE_COLLECTOR
    assert '["docker", "volume", "ls", "--format", "{{.Name}}"]' in remote
    assert "docker volume inspect" not in remote
    assert '["sfdisk", "-d", "/dev/vda"]' in remote
    assert '["lsblk", "-b", "-J", "/dev/vda"]' in remote
    assert '["blkid"]' in remote
