"""Additional S14.6 push/activation boundary contracts.

These are deliberately separate from ``test_ciu_thin_deploy``: they exercise
failure cleanup and shell-argument boundaries with deterministic transport
fakes, never an SSH server or a network.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import activate as act


def test_make_tarball_removes_created_tempfile_when_tar_creation_fails(tmp_path, monkeypatch):
    """A failed local archive build must not leak the transient bundle file."""
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text("print('ok')")
    tarball = tmp_path / "created-by-mkstemp.tar.gz"
    fd = os.open(tarball, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    monkeypatch.setattr(act.tempfile, "mkstemp", lambda **_kwargs: (fd, str(tarball)))

    def fail_open(*_args, **_kwargs):
        raise OSError("local archive disk full")

    monkeypatch.setattr(act.tarfile, "open", fail_open)
    with pytest.raises(OSError, match="disk full"):
        act.make_tarball(str(source))
    assert not tarball.exists()


def test_push_scp_removes_local_tarball_when_remote_mkdir_fails(tmp_path, monkeypatch):
    """Remote setup failure still releases the local artifact in the finally path."""
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text("print('ok')")
    made: dict[str, str] = {}
    real_make_tarball = act.make_tarball

    def capture_tarball(local_dir, *, excludes=None):
        path = real_make_tarball(local_dir, excludes=excludes)
        made["path"] = path
        return path

    monkeypatch.setattr(act, "make_tarball", capture_tarball)
    monkeypatch.setattr(act, "ssh_exec", lambda *_args, **_kwargs: 23)
    monkeypatch.setattr(act, "scp_file", lambda *_args, **_kwargs: pytest.fail("scp must not run"))

    assert act._push_scp({}, str(source), "/srv/app", config={}, repo_root=tmp_path) == 23
    assert not Path(made["path"]).exists()


def test_activation_quotes_bundle_and_selection_as_individual_shell_arguments(tmp_path, monkeypatch):
    """S14.6 selection values must stay data, not become remote shell syntax."""
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        act,
        "ssh_exec",
        lambda _host, argv, *, config, repo_root: captured.setdefault("argv", argv) and 0,
    )
    bundle = "/srv/releases/blue green; touch /tmp/pwned"
    selection = ["--profile", "web; touch /tmp/pwned", "two words"]

    assert act.run_activation(
        {"activate": "sh deploy/activate.sh"},
        "apply",
        config={},
        repo_root=tmp_path,
        bundle_dir=bundle,
        remaining=selection,
    ) == 0
    assert len(captured["argv"]) == 1
    # Parse exactly as a POSIX shell would: each hostile value is still one word.
    assert shlex.split(captured["argv"][0]) == [
        "cd", bundle, "&&", "sh", "deploy/activate.sh", "apply", *selection,
    ]


def test_bootstrap_never_receives_apply_selection_and_apply_failure_propagates(tmp_path, monkeypatch):
    """Bootstrap is setup-only; only apply sees selection and its rc is preserved."""
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(act, "push_bundle", lambda *_args, **_kwargs: calls.append(("push", None)) or 0)

    def activation(_cfg, verb, **kwargs):
        calls.append((verb, kwargs.get("remaining")))
        return 17 if verb == "apply" else 0

    monkeypatch.setattr(act, "run_activation", activation)
    rc = act.run_thin_up(
        {"activate": "sh deploy/activate.sh"},
        config={},
        repo_root=tmp_path,
        bundle_dir="/srv/app",
        bootstrap=True,
        remaining=["--profile", "web"],
    )
    assert rc == 17
    assert calls == [
        ("push", None),
        ("bootstrap", None),
        ("apply", ["--profile", "web"]),
    ]
