"""Unit tests for hooks/post_compose_users.py with a fake docker (no daemon)."""
from __future__ import annotations

import importlib.util
import stat
import subprocess
import types
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "post_compose_users.py"


def _load():
    spec = importlib.util.spec_from_file_location("_jitsi_users_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def world(tmp_path, monkeypatch):
    hook = _load()
    secrets = tmp_path / ".ciu" / "secrets" / "jitsi"
    secrets.mkdir(parents=True)
    names = ["admin", "dummy1"]
    for n in names:
        (secrets / f"user_{n}").write_text(f"pw-{n}-SECRETVALUE")
    calls: list[dict] = []

    def fake_run(argv, input=None, **kw):
        calls.append({"argv": argv, "input": input})
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    cfg = {"jitsi": {
        "container_prefix": "jitsi-x", "public_host": "jitsi.example.test",
        "auth": {"enabled": True, "xmpp_domain": "meet.jitsi"},
        "users": [{"name": n, "secret": f"user_{n}"} for n in names],
    }}
    ctx = types.SimpleNamespace(
        wait_healthy=lambda svc, timeout_s=1: True,
        secret_file=lambda n: secrets / n, stack_dir=tmp_path,
    )
    return hook, cfg, ctx, calls, tmp_path


def test_password_goes_over_stdin_never_argv_or_stdout(world, capsys):
    hook, cfg, ctx, calls, _ = world
    hook.run(cfg, ctx)
    assert len(calls) == 2
    for c in calls:
        assert c["argv"][:4] == ["docker", "exec", "-i", "jitsi-x-prosody"]
        assert "SECRETVALUE" not in " ".join(c["argv"])
        assert "SECRETVALUE" in c["input"]
    out = capsys.readouterr().out
    assert "SECRETVALUE" not in out
    assert "credentials_file=" in out


def test_credentials_file_is_0600_in_0700_dir_and_rebuilt_idempotently(world):
    hook, cfg, ctx, calls, root = world
    hook.run(cfg, ctx)
    first = (root / ".ciu" / "credentials.txt").read_text()
    hook.run(cfg, ctx)
    f = root / ".ciu" / "credentials.txt"
    assert f.read_text() == first
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert len(calls) == 4  # register is re-issued, same end state


def test_failure_does_not_leak_password(world, monkeypatch):
    hook, cfg, ctx, _, _ = world

    def bad(argv, input=None, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout=input, stderr=input)

    monkeypatch.setattr(hook.subprocess, "run", bad)
    with pytest.raises(hook.ProvisionError) as e:
        hook.run(cfg, ctx)
    assert "SECRETVALUE" not in str(e.value)


def test_not_healthy_aborts(world):
    hook, cfg, ctx, calls, _ = world
    ctx.wait_healthy = lambda svc, timeout_s=1: False
    with pytest.raises(hook.ProvisionError):
        hook.run(cfg, ctx)
    assert calls == []


def test_auth_disabled_registers_nothing(world):
    hook, cfg, ctx, calls, _ = world
    cfg["jitsi"]["auth"]["enabled"] = False
    assert hook.run(cfg, ctx) == {}
    assert calls == []


def test_validate_config_flags_bad_users(world):
    hook, cfg, ctx, _, _ = world
    cfg["jitsi"]["secrets"] = {"user_admin": {}}
    cfg["jitsi"]["users"] = [
        {"name": "Bad Name", "secret": "user_admin"},
        {"name": "admin", "secret": "nope"},
    ]
    problems = hook.validate_config(cfg, ctx)
    assert any("invalid username" in p for p in problems)
    assert any("not declared" in p for p in problems)


def test_dummy_users_skipped_by_default_and_registered_when_enabled(world):
    hook, cfg, ctx, calls, _ = world
    cfg["jitsi"]["users"][1]["dummy"] = True
    hook.run(cfg, ctx)
    assert len(calls) == 1 and calls[0]["argv"][-2] == "admin"
    cfg["jitsi"]["auth"]["dummy_users"] = True
    hook.run(cfg, ctx)
    assert len(calls) == 3
