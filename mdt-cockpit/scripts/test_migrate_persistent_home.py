"""Regression tests for full-home migration and archive safety."""
from __future__ import annotations

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "templates"))
import migrate_persistent_home as migration  # noqa: E402


def _archive(entries: list[tuple[str, str, bytes | str | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, kind, payload in entries:
            member = tarfile.TarInfo(name)
            if kind == "dir":
                member.type = tarfile.DIRTYPE
                member.mode = 0o700
                archive.addfile(member)
            elif kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = str(payload)
                archive.addfile(member)
            elif kind == "fifo":
                member.type = tarfile.FIFOTYPE
                member.mode = 0o600
                archive.addfile(member)
            else:
                data = bytes(payload or b"")
                member.size = len(data)
                member.mode = 0o600
                archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def _home_archive() -> bytes:
    return _archive(
        [
            ("./", "dir", None),
            (".claude/", "dir", None),
            (".claude/settings.json", "file", b"{\"theme\":\"dark\"}\n"),
            (".local/", "dir", None),
            (".local/bin/", "dir", None),
            (".local/bin/my-cli", "file", b"#!/bin/sh\n"),
            (".user-link", "symlink", ".claude/settings.json"),
            (".oh-my-zsh/", "dir", None),
            (".oh-my-zsh/custom/", "dir", None),
            (".oh-my-zsh/custom/plugins/local.zsh", "file", b"alias ll='ls -l'\n"),
            (".venv/", "dir", None),
            (".venv/bin/python", "file", b"image python runtime"),
            (".venv-py311/", "dir", None),
            (".venv-py311/bin/python", "file", b"secondary runtime"),
            (".ssh-host/", "dir", None),
            (".ssh-host/id_ed25519", "file", b"host key must not be copied"),
            (".codex/packages/standalone/1.0.0", "file", b"image Codex runtime"),
            (".codex/config.toml", "file", b"model = 'user-setting'\n"),
        ]
    )


def test_archive_scan_excludes_image_payloads_and_preserves_user_state() -> None:
    payload = _home_archive()
    plan = migration.scan_archive(io.BytesIO(payload))

    assert plan.files == 4
    assert plan.symlinks == 1
    assert plan.bytes == sum(
        len(value)
        for value in (
            b'{"theme":"dark"}\n',
            b"#!/bin/sh\n",
            b"alias ll='ls -l'\n",
            b"model = 'user-setting'\n",
        )
    )
    assert plan.excluded["image-owned runtime"][0] == 4
    assert plan.excluded["read-only native host SSH bind"][0] == 2
    assert plan.excluded["standalone Codex runtime payload"][0] == 1
    assert plan.excluded["image-owned Oh My Zsh distribution"][0] == 1


def test_archive_copy_remaps_zsh_custom_and_preserves_modes_and_symlinks(
    tmp_path: Path,
) -> None:
    target = tmp_path / "home"
    target.mkdir(mode=0o700)
    plan = migration.scan_archive(io.BytesIO(_home_archive()), target)

    assert (target / ".claude/settings.json").read_text() == '{"theme":"dark"}\n'
    assert (target / ".local/bin/my-cli").stat().st_mode & 0o777 == 0o600
    assert (target / ".oh-my-zsh-custom/plugins/local.zsh").is_file()
    assert os.readlink(target / ".user-link") == ".claude/settings.json"
    assert not (target / ".venv").exists()
    assert not (target / ".ssh-host").exists()
    assert not (target / ".codex/packages/standalone").exists()
    assert plan.files == 4


def test_archive_rejects_path_traversal() -> None:
    with pytest.raises(migration.MigrationError, match="unsafe path"):
        migration.scan_archive(io.BytesIO(_archive([("../outside", "file", b"bad")])) )


def test_archive_rejects_writes_through_symlinks(tmp_path: Path) -> None:
    unsafe = _archive(
        [
            ("redirect", "symlink", "/tmp/outside"),
            ("redirect/secret", "file", b"must not escape"),
        ]
    )
    target = tmp_path / "home"
    target.mkdir()
    with pytest.raises(migration.MigrationError, match="traverses a symlink"):
        migration.scan_archive(io.BytesIO(unsafe), target)


def test_apply_installs_marker_only_after_complete_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "mdt--mounted-folders"
    home = parent / "home-vscode"
    payload = _home_archive()
    monkeypatch.setattr(migration, "home_source_path", lambda: home)
    monkeypatch.setattr(migration, "require_stopped_container", lambda *_args: None)

    @contextmanager
    def fake_archive(_docker: str, _container: str):
        yield io.BytesIO(payload)

    monkeypatch.setattr(migration, "docker_home_archive", fake_archive)
    assert migration.migrate("fixture-container", apply=True) == 0

    assert (home / ".claude/settings.json").is_file()
    assert (home / ".oh-my-zsh-custom/plugins/local.zsh").is_file()
    marker = json.loads((home / migration.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["schema_version"] == migration.SCHEMA_VERSION
    assert marker["complete"] is True
    assert marker["source_container"] == "fixture-container"
    assert not (home / ".venv").exists()


def test_apply_refuses_a_nonempty_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "mdt--mounted-folders" / "home-vscode"
    home.mkdir(parents=True)
    (home / "keep.txt").write_text("user data", encoding="utf-8")
    monkeypatch.setattr(migration, "home_source_path", lambda: home)
    monkeypatch.setattr(migration, "require_stopped_container", lambda *_args: None)

    with pytest.raises(migration.MigrationError, match="not empty"):
        migration.migrate("fixture-container", apply=True)
    assert (home / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_running_container_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        returncode = 0
        stdout = "true\n"
        stderr = ""

    monkeypatch.setattr(migration.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(migration.MigrationError, match="must be stopped"):
        migration.require_stopped_container("docker", "running-container")


def test_apply_refuses_special_entries_without_creating_a_home_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "mdt--mounted-folders"
    home = parent / "home-vscode"
    payload = _archive([(".runtime/fifo", "fifo", None)])
    monkeypatch.setattr(migration, "home_source_path", lambda: home)
    monkeypatch.setattr(migration, "require_stopped_container", lambda *_args: None)

    @contextmanager
    def fake_archive(_docker: str, _container: str):
        yield io.BytesIO(payload)

    monkeypatch.setattr(migration, "docker_home_archive", fake_archive)
    with pytest.raises(migration.MigrationError, match="unsupported special entries"):
        migration.migrate("fixture-container", apply=True)

    assert not home.exists()
    assert not parent.exists()


def test_apply_refuses_same_size_file_changes_between_scan_and_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "mdt--mounted-folders"
    home = parent / "home-vscode"
    archives = iter(
        [
            _archive([(".config/model", "file", b"model-a")]),
            _archive([(".config/model", "file", b"model-b")]),
        ]
    )
    monkeypatch.setattr(migration, "home_source_path", lambda: home)
    monkeypatch.setattr(migration, "require_stopped_container", lambda *_args: None)

    @contextmanager
    def changing_archive(_docker: str, _container: str):
        yield io.BytesIO(next(archives))

    monkeypatch.setattr(migration, "docker_home_archive", changing_archive)
    with pytest.raises(migration.MigrationError, match="contents changed between scan and copy"):
        migration.migrate("fixture-container", apply=True)

    assert not home.exists()
    assert not list(parent.glob(".home-vscode-migration-*"))
