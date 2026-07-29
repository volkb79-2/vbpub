"""Focused defensive contracts for the public hostdir creation API."""

from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _config(hostdir: dict, *, uid: str = "1001", gid: str = "1002") -> dict:
    return {
        "deploy": {"env": {"shared": {"CONTAINER_UID": uid, "DOCKER_GID": gid}}},
        "service": {"name": "api", "hostdir": hostdir},
    }


def test_invalid_entry_ownership_overrides_fall_back_to_configured_defaults(tmp_path):
    seen = []
    config = _config({"data": {"path": "vol-api-data", "uid": "not-a-uid", "gid": "bad-gid"}})

    engine.create_hostdirs(
        config,
        tmp_path,
        repo_root=tmp_path,
        physical_root=tmp_path,
        chown_fn=lambda path, uid, gid: seen.append((path, uid, gid)),
    )

    assert (tmp_path / "vol-api-data").is_dir()
    assert seen == [((tmp_path / "vol-api-data").resolve(), 1001, 1002)]


def test_missing_seed_directory_fails_closed_with_typed_error(tmp_path):
    config = _config({"data": {"path": "vol-api-data", "seed": "does-not-exist"}})

    with pytest.raises(ValueError, match=r"\[S6\.6\] hostdir seed directory not found:"):
        engine.create_hostdirs(
            config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
            chown_fn=lambda *_args: None,
        )

    destination = tmp_path / "vol-api-data"
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_regular_file_hostdir_fails_closed_without_replacement_or_copy(tmp_path):
    destination = tmp_path / "vol-api-data"
    destination.write_text("keep this file")
    config = _config({"data": "vol-api-data"})

    with pytest.raises(ValueError, match=r"\[S6\.3\] Path exists and is not a directory:"):
        engine.create_hostdirs(
            config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
            chown_fn=lambda *_args: None,
        )

    assert destination.is_file()
    assert destination.read_text() == "keep this file"
    assert config["service"]["hostdir"]["data"] == "vol-api-data"


def test_ownership_seam_receives_values_and_mode_is_applied(tmp_path, monkeypatch):
    chown_calls = []
    chmod_calls = []
    config = _config({"data": {"path": "vol-api-data", "uid": 2001, "gid": 2002, "mode": "0750"}})

    monkeypatch.setattr(engine.os, "chmod", lambda path, mode: chmod_calls.append((path, mode)))
    engine.create_hostdirs(
        config,
        tmp_path,
        repo_root=tmp_path,
        physical_root=tmp_path,
        chown_fn=lambda path, uid, gid: chown_calls.append((path, uid, gid)),
    )

    created = (tmp_path / "vol-api-data").resolve()
    assert chown_calls == [(created, 2001, 2002)]
    assert chmod_calls == [(created, 0o750)]
