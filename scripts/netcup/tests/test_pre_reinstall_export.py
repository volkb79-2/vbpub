"""The destructive-reinstall evidence exporter is exercised with fake SSH only."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import subprocess
import sys
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


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("state", b'{"password": "sentinel-json"}\n'),
        ("comments.jsonc", b'{\n  // operator note\n  "PAT": "sentinel-jsonc",\n}\n'),
        ("host.env", b'API_KEY=sentinel-api\nPAT=sentinel-pat\nPASS=sentinel-pass\nPASSWD=sentinel-passwd\nPRIVATE=sentinel-private\n'),
        ("service.ini", b'password = "sentinel-ini"\n'),
        ("auth.conf", b'Authorization: Bearer sentinel-bearer\n'),
        ("auth.json", b'{"Authorization": "Bearer sentinel-json-bearer"}\n'),
        ("url.txt", b'endpoint=https://operator:sentinel-url@host.example/path\n'),
        ("args.txt", b'--password sentinel-arg --api-key=sentinel-arg-key\n'),
        ("private.pem", b'-----BEGIN OPENSSH PRIVATE KEY-----\nsentinel-key\n-----END OPENSSH PRIVATE KEY-----\n'),
    ],
)
def test_redaction_covers_secret_pair_shapes_in_any_filename(name, raw):
    exporter = _load_exporter()
    cleaned = exporter.redact_export_bytes(f"arbitrary/path/{name}", raw)
    assert b"sentinel-" not in cleaned
    assert (
        b"***REDACTED***" in cleaned
        or b"***REDACTED PRIVATE KEY***" in cleaned
        or name == "url.txt" and b"https://host.example/path" in cleaned
    )


def test_redaction_refuses_nul_binary_content():
    exporter = _load_exporter()
    with pytest.raises(ValueError, match="binary|NUL"):
        exporter.redact_export_bytes("state", b"ordinary\x00data")


def test_remote_collector_enforces_identity_redaction_scratch_and_size_cap(tmp_path):
    exporter = _load_exporter()
    root = tmp_path / "host-root"
    (root / "etc/vbpub").mkdir(parents=True)
    (root / "etc/mdt").mkdir(parents=True)
    (root / "etc/ssh").mkdir(parents=True)
    (root / "var/lib/iogovmon/state").mkdir(parents=True)
    (root / "var/lib/iogovmon/scratch").mkdir(parents=True)
    (root / "home/runner/runner-scripts").mkdir(parents=True)
    (root / "etc/vbpub/server-id").write_text("804027\n", encoding="utf-8")
    (root / "etc/fstab").write_text("UUID=safe / ext4 defaults 0 1\n", encoding="utf-8")
    (root / "etc/ssh/sshd_config").write_text("PasswordAuthentication no\n", encoding="utf-8")
    for source, content in (
        ("etc/hosts", "127.0.0.1 localhost\n"),
        ("etc/containerd/config.toml", "version = 2\n"),
        ("etc/docker/daemon.json", "{}\n"),
        ("etc/mdt-cow-root.sh", "#!/bin/sh\n"),
        ("etc/vbpub/bootstrap.env", "BOOTSTRAP_MODE=runner\n"),
    ):
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for source in (
        "etc/cloud/cloud.cfg.d",
        "etc/udev/rules.d",
        "etc/sudoers.d",
        "etc/sysctl.d",
        "etc/systemd/system",
    ):
        directory = root / source
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "saved.conf").write_text("saved\n", encoding="utf-8")
    (root / "etc/mdt/host-capacity.json").write_text(
        '{\n  // capacity note\n  "password": "sentinel-jsonc",\n  "API_KEY": "sentinel-api",\n  "Authorization": "Bearer sentinel-json-bearer"\n}\n',
        encoding="utf-8",
    )
    (root / "etc/mdt/too-large.txt").write_bytes(b"x" * 257)
    (root / "home/runner/runner-scripts/runner.env").write_text(
        "COCKPIT_CONFIG_DIR=/home/runner/mdt-cockpit-runner-config-test\n"
        "COCKPIT_HOME_DIR=/home/runner/mdt-cockpit-runner-home-test\n",
        encoding="utf-8",
    )
    (root / "home/runner/mdt-cockpit-runner-config-test").mkdir(parents=True)
    (root / "home/runner/mdt-cockpit-runner-config-test/config.ini").write_text(
        "Authorization: Bearer sentinel-bearer\n", encoding="utf-8"
    )
    (root / "home/runner/mdt-cockpit-runner-home-test").mkdir(parents=True)
    (root / "home/runner/mdt-cockpit-runner-home-test/settings").write_text("safe\n", encoding="utf-8")
    (root / "var/lib/iogovmon/state/state.json").write_text('{"state": "ok"}\n', encoding="utf-8")
    (root / "var/lib/iogovmon/scratch/fio.dat").write_bytes(b"scratch-data")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "stub"
    stub.write_text(
        r'''#!/bin/sh
case "$(basename "$0")" in
  hostname) printf '%s' "${FAKE_FQDN:-v1001.vxxu.de}" ;;
  hostnamectl) printf 'v1001' ;;
  lsblk) if [ "$1" = '-dn' ]; then printf 'vda'; else printf '%s' '{"blockdevices":[]}'; fi ;;
  sfdisk) printf 'label: gpt' ;;
  blkid) printf '/dev/vda3: UUID=example' ;;
  docker) printf 'campaign-cache' ;;
esac
''',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    for command in ("hostname", "hostnamectl", "lsblk", "sfdisk", "blkid", "docker"):
        (bindir / command).symlink_to(stub)

    remote = exporter.remote_collector("v1001", max_file_bytes=256)
    remote = remote.replace('ROOT = Path("/")', f"ROOT = Path({str(root)!r})")
    script = tmp_path / "collector.py"
    script.write_text(remote, encoding="utf-8")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    completed = subprocess.run(
        [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:gz") as archive:
        names = set(archive.getnames())
        files = {name: archive.extractfile(name).read() for name in names if archive.getmember(name).isfile()}
    assert "home/runner/mdt-cockpit-runner-config-test/config.ini" in names
    assert "home/runner/mdt-cockpit-runner-home-test/settings" in names
    assert "etc/ssh/sshd_config" not in names
    for expected_name in (
        "etc/hosts",
        "etc/containerd/config.toml",
        "etc/docker/daemon.json",
        "etc/mdt/host-capacity.json",
        "etc/mdt-cow-root.sh",
        "etc/vbpub/bootstrap.env",
        "etc/cloud/cloud.cfg.d/saved.conf",
        "etc/rules.d/saved.conf",
        "etc/sudoers.d/saved.conf",
        "etc/sysctl.d/saved.conf",
        "etc/systemd-system/saved.conf",
    ):
        assert expected_name in names
    assert not any("scratch" in name for name in names)
    notes = files["meta/collection.txt"].decode()
    assert "scratch" in notes and "skipped" in notes
    assert "too-large.txt" in notes and "257" in notes and "256" in notes
    all_bytes = b"\n".join(files.values())
    for secret in (b"sentinel-jsonc", b"sentinel-api", b"sentinel-bearer", b"sentinel-json-bearer"):
        assert secret not in all_bytes

    mismatch_env = dict(env, FAKE_FQDN="wrong.example")
    mismatch = subprocess.run(
        [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=mismatch_env, check=False,
    )
    assert mismatch.returncode != 0
    assert mismatch.stdout == b""


@pytest.mark.parametrize("name", ["/etc/fstab", "../etc/fstab", "root/escape", "not-allowlisted/file"])
def test_archive_member_names_refuse_absolute_parent_and_unlisted_roots(name):
    exporter = _load_exporter()
    with pytest.raises(ValueError, match="unsafe|allowlist"):
        exporter._safe_member_name(name)


def test_extract_redacted_refuses_non_regular_members_and_export_removes_partial_tree(tmp_path):
    exporter = _load_exporter()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        member = tarfile.TarInfo("etc/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "fstab"
        archive.addfile(member)
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(ValueError, match="non-regular"):
        exporter._extract_redacted(data.getvalue(), destination)

    with pytest.raises(RuntimeError, match="read-only evidence export failed"):
        exporter.export_snapshot(
            "v1001", "20261107", root=tmp_path / "failed",
            runner=lambda argv, **kwargs: CompletedProcess(argv, 1, stdout=b"", stderr=b"fake refusal"),
        )
    assert not (tmp_path / "failed/v1001/pre-reinstall-20261107").exists()
