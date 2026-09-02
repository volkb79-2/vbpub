"""Host-scoped local secrets (S14.3a / CIU-35).

The EXISTING secret machinery pointed at a new namespace: only ASK_EXTERNAL +
GEN_LOCAL are legal at host scope, values are never printed, nothing
materializes implicitly, and the transport dict never sees directives.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import ciu.deploy as REAL_DEPLOY  # captured BEFORE any fixture stubs sys.modules
from ciu.hosts import get_host, get_host_secrets
from ciu.secrets.materialize import (
    host_secret_store,
    materialize_host_secrets,
    project_store,
)


HOSTS_TOML = """\
[deploy.hosts.devbox]
ssh_host = "devbox"
ssh_key = "/key"
bundle_dir = "/opt/app"

[deploy.hosts.devbox.secrets]
ts_authkey = "ASK_EXTERNAL:TS_AUTHKEY"
ssh_bootstrap_key = "GEN_LOCAL:host-bootstrap"

[deploy.hosts.badhost]
ssh_host = "bad"

[deploy.hosts.badhost.secrets]
vaulted = "ASK_VAULT:secret/host/ts"

[deploy.hosts.web]
ssh_host = "web"
"""


def _write_hosts(tmp_path: Path, text: str = HOSTS_TOML) -> Path:
    hosts_file = tmp_path / ".ciu.hosts.toml"
    hosts_file.write_text(text)
    return hosts_file


# ---------------------------------------------------------------------------
# hosts.py — declaration, closed set, pop
# ---------------------------------------------------------------------------


def test_get_host_secrets_parses_allowed_kinds(tmp_path):
    _write_hosts(tmp_path)
    specs = get_host_secrets(tmp_path, "devbox")
    assert set(specs) == {"ts_authkey", "ssh_bootstrap_key"}
    assert specs["ts_authkey"].kind == "ASK_EXTERNAL"
    assert specs["ts_authkey"].locator == "TS_AUTHKEY"
    assert specs["ssh_bootstrap_key"].kind == "GEN_LOCAL"


def test_get_host_secrets_no_subtable_returns_empty(tmp_path):
    _write_hosts(tmp_path, "[deploy.hosts.web]\nssh_host = 'web'\n")
    assert get_host_secrets(tmp_path, "web") == {}


def test_get_host_secrets_missing_host(tmp_path):
    _write_hosts(tmp_path)
    with pytest.raises(ValueError, match=r"\[SPEC J\] Host 'ghost' not found"):
        get_host_secrets(tmp_path, "ghost")


def test_get_host_secrets_no_hosts_file(tmp_path):
    with pytest.raises(ValueError, match=r"\[SPEC J\] No hosts file found"):
        get_host_secrets(tmp_path, "devbox")


@pytest.mark.parametrize(
    ("directive", "kind"),
    [
        ("ASK_VAULT:secret/host/ts", "ASK_VAULT"),
        ("GEN_TO_VAULT:secret/host/ts", "GEN_TO_VAULT"),
        ("ASK_FILE:/etc/passwd", "ASK_FILE"),
        ("GEN_EPHEMERAL", "GEN_EPHEMERAL"),
    ],
)
def test_get_host_secrets_refuses_vault_ephemeral_file_kinds(tmp_path, directive, kind):
    _write_hosts(
        tmp_path,
        f"[deploy.hosts.web]\nssh_host = 'web'\n"
        f"[deploy.hosts.web.secrets]\nbad = \"{directive}\"\n",
    )
    with pytest.raises(
        ValueError,
        match=rf"\[S14.3a\] host 'web', entry 'bad': directive '{kind}' is not allowed at host scope",
    ):
        get_host_secrets(tmp_path, "web")


def test_get_host_secrets_refuses_grammar_violation(tmp_path):
    _write_hosts(
        tmp_path,
        "[deploy.hosts.web]\nssh_host = 'web'\n"
        "[deploy.hosts.web.secrets]\nbad = \"NOT_A_DIRECTIVE:x\"\n",
    )
    with pytest.raises(ValueError, match=r"\[S14.3a\] host 'web', entry 'bad':"):
        get_host_secrets(tmp_path, "web")


def test_get_host_secrets_pasted_value_never_reaches_the_error_message(tmp_path):
    """P11-B1 (review): a pasted value instead of a directive (e.g. a Tailscale
    authkey) must NOT flow into the raised message. Upstream
    `directives.parse_value` echoes the unrecognized token verbatim in its
    '[S4.2] Unknown directive' error; hosts.py must NOT interpolate that
    message — only a fixed, non-leaking reason."""
    fake_secret_value = "tskey-auth-kFAKESECRETVALUE1234567890abcdef"
    _write_hosts(
        tmp_path,
        "[deploy.hosts.web]\nssh_host = 'web'\n"
        f"[deploy.hosts.web.secrets]\nbad = \"{fake_secret_value}\"\n",
    )
    with pytest.raises(ValueError) as exc_info:
        get_host_secrets(tmp_path, "web")
    message = str(exc_info.value)
    assert fake_secret_value not in message
    assert "tskey-auth-k" not in message  # no partial leak either
    assert message == (
        "[S14.3a] host 'web', entry 'bad': not a recognized secret directive "
        "— value not shown"
    )


def test_get_host_pops_secrets_from_transport_dict(tmp_path):
    _write_hosts(tmp_path)
    host_cfg = get_host(tmp_path, "devbox")
    assert "secrets" not in host_cfg
    assert host_cfg["ssh_host"] == "devbox"


def test_get_host_still_validates_secrets(tmp_path):
    _write_hosts(tmp_path)
    with pytest.raises(ValueError, match=r"\[S14.3a\] host 'badhost', entry 'vaulted'"):
        get_host(tmp_path, "badhost")


# ---------------------------------------------------------------------------
# materialize_host_secrets — store namespace + existing resolution order
# ---------------------------------------------------------------------------


def test_host_secret_store_path_namespace(tmp_path):
    assert host_secret_store(tmp_path, "devbox", "ts_authkey") == (
        project_store(tmp_path) / "hosts" / "devbox" / "ts_authkey"
    )


def test_materialize_ask_external_from_env(tmp_path, monkeypatch):
    _write_hosts(tmp_path)
    monkeypatch.setenv("TS_AUTHKEY", "tskey-single-use-abc")
    monkeypatch.delenv("CIU_SECRET_TS_AUTHKEY", raising=False)
    specs = get_host_secrets(tmp_path, "devbox")
    res = materialize_host_secrets(
        tmp_path, "devbox", specs, assume_yes=True, env=os.environ
    )
    store = host_secret_store(tmp_path, "devbox", "ts_authkey")
    assert store.read_bytes() == b"tskey-single-use-abc"
    assert res["ts_authkey"].file == store


def test_materialize_ask_external_from_ciu_secret_env(tmp_path, monkeypatch):
    _write_hosts(tmp_path)
    monkeypatch.setenv("CIU_SECRET_TS_AUTHKEY", "via-ciu-secret")
    monkeypatch.delenv("TS_AUTHKEY", raising=False)
    specs = get_host_secrets(tmp_path, "devbox")
    materialize_host_secrets(tmp_path, "devbox", specs, assume_yes=True, env=os.environ)
    assert host_secret_store(tmp_path, "devbox", "ts_authkey").read_bytes() == b"via-ciu-secret"


def test_materialize_ask_external_reuses_store_file(tmp_path, monkeypatch):
    _write_hosts(tmp_path)
    monkeypatch.delenv("TS_AUTHKEY", raising=False)
    monkeypatch.delenv("CIU_SECRET_TS_AUTHKEY", raising=False)
    store = host_secret_store(tmp_path, "devbox", "ts_authkey")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"cached-value")
    specs = get_host_secrets(tmp_path, "devbox")

    def _boom(_prompt):
        raise AssertionError("prompt must not be called when cached")

    res = materialize_host_secrets(
        tmp_path, "devbox", specs, assume_yes=False, env=os.environ, prompt_fn=_boom
    )
    assert res["ts_authkey"].value == "cached-value"


def test_materialize_ask_external_prompt_path(tmp_path, monkeypatch):
    _write_hosts(tmp_path)
    monkeypatch.delenv("TS_AUTHKEY", raising=False)
    monkeypatch.delenv("CIU_SECRET_TS_AUTHKEY", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    specs = get_host_secrets(tmp_path, "devbox")
    res = materialize_host_secrets(
        tmp_path, "devbox", specs, assume_yes=False, env=os.environ,
        prompt_fn=lambda _p: "typed-secret",
    )
    assert res["ts_authkey"].value == "typed-secret"
    assert host_secret_store(tmp_path, "devbox", "ts_authkey").read_bytes() == b"typed-secret"


def test_materialize_ask_external_non_interactive_aborts(tmp_path, monkeypatch):
    _write_hosts(tmp_path)
    monkeypatch.delenv("TS_AUTHKEY", raising=False)
    monkeypatch.delenv("CIU_SECRET_TS_AUTHKEY", raising=False)
    specs = get_host_secrets(tmp_path, "devbox")
    with pytest.raises(ValueError, match=r"\[S4.13\]"):
        materialize_host_secrets(tmp_path, "devbox", specs, assume_yes=True, env=os.environ)


def test_materialize_gen_local_generates_then_reuses(tmp_path):
    _write_hosts(tmp_path)
    specs = get_host_secrets(tmp_path, "devbox")
    gen_only = {k: v for k, v in specs.items() if v.kind == "GEN_LOCAL"}
    first = materialize_host_secrets(tmp_path, "devbox", gen_only, assume_yes=True, env=os.environ)
    store = host_secret_store(tmp_path, "devbox", "ssh_bootstrap_key")
    assert store.exists()
    first_value = first["ssh_bootstrap_key"].value
    assert len(first_value) > 0
    second = materialize_host_secrets(tmp_path, "devbox", gen_only, assume_yes=True, env=os.environ)
    assert second["ssh_bootstrap_key"].value == first_value


def test_materialize_two_hosts_same_entry_no_collision(tmp_path):
    _write_hosts(
        tmp_path,
        "[deploy.hosts.a]\nssh_host = 'a'\n[deploy.hosts.a.secrets]\nkey = 'GEN_LOCAL:a'\n"
        "[deploy.hosts.b]\nssh_host = 'b'\n[deploy.hosts.b.secrets]\nkey = 'GEN_LOCAL:b'\n",
    )
    specs_a = get_host_secrets(tmp_path, "a")
    specs_b = get_host_secrets(tmp_path, "b")
    ra = materialize_host_secrets(tmp_path, "a", specs_a, assume_yes=True, env=os.environ)
    rb = materialize_host_secrets(tmp_path, "b", specs_b, assume_yes=True, env=os.environ)
    assert ra["key"].file != rb["key"].file
    assert ra["key"].file.parent == host_secret_store(tmp_path, "a", "key").parent
    assert rb["key"].file.parent == host_secret_store(tmp_path, "b", "key").parent


def test_materialize_empty_entries_noop(tmp_path):
    assert materialize_host_secrets(tmp_path, "devbox", {}, assume_yes=True, env=os.environ) == {}


# ---------------------------------------------------------------------------
# CLI — ciu host-secrets
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    _write_hosts(tmp_path)
    return tmp_path


def _run(monkeypatch, argv):
    from ciu import cli
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def test_cli_list_prints_names_and_existence_without_values(cli_env, monkeypatch, capsys):
    store = host_secret_store(cli_env, "devbox", "ts_authkey")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"secret-value")
    assert _run(monkeypatch, ["host-secrets", "devbox", "--list"]) == 0
    out = capsys.readouterr().out
    assert "ts_authkey  present" in out
    assert "ssh_bootstrap_key  absent" in out
    assert "secret-value" not in out


def test_cli_list_no_secrets(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "web", "--list"]) == 0
    assert "(no host secrets declared)" in capsys.readouterr().out


def test_cli_list_accepts_define_root_with_no_ambient_repo_root(monkeypatch, capsys, tmp_path):
    """ciu-P45 / CIU-54: `host-secrets` previously ignored --define-root
    entirely (bare REPO_ROOT-or-cwd fallback); now it resolves via
    `deploy.resolve_repo_root` (S1.1) like every other verb."""
    monkeypatch.delenv("REPO_ROOT", raising=False)
    _write_hosts(tmp_path)
    assert _run(monkeypatch, [
        "host-secrets", "devbox", "--list", "--define-root", str(tmp_path),
    ]) == 0
    out = capsys.readouterr().out
    assert "ts_authkey  absent" in out


def test_cli_list_refuses_when_repo_root_not_set_and_no_define_root(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert _run(monkeypatch, ["host-secrets", "devbox", "--list"]) == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "REPO_ROOT not set" in err


def test_cli_path_prints_store_file(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "devbox", "--path", "ts_authkey"]) == 0
    assert capsys.readouterr().out.strip() == str(host_secret_store(cli_env, "devbox", "ts_authkey"))


def test_cli_path_unknown_entry_exits_2(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "devbox", "--path", "nope"]) == 2
    assert "declares no secret 'nope'" in capsys.readouterr().err


def test_cli_materialize_from_env_prints_path_not_value(cli_env, monkeypatch, capsys):
    monkeypatch.setenv("TS_AUTHKEY", "tskey-top-secret")
    monkeypatch.delenv("CIU_SECRET_TS_AUTHKEY", raising=False)
    assert _run(monkeypatch, ["host-secrets", "devbox", "--materialize"]) == 0
    out = capsys.readouterr().out
    assert "ts_authkey -> " in out
    assert "ssh_bootstrap_key -> " in out
    assert "tskey-top-secret" not in out
    assert "top-secret" not in out


def test_cli_materialize_non_interactive_abort_exits_2(cli_env, monkeypatch, capsys):
    monkeypatch.delenv("TS_AUTHKEY", raising=False)
    monkeypatch.delenv("CIU_SECRET_TS_AUTHKEY", raising=False)
    assert _run(monkeypatch, ["host-secrets", "devbox", "--materialize", "-y"]) == 2
    assert "[S4.13]" in capsys.readouterr().err


def test_cli_materialize_no_secrets(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "web", "--materialize"]) == 0
    assert "no secrets declared" in capsys.readouterr().out


def test_cli_materialize_unknown_host_exits_2(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "ghost", "--materialize"]) == 2
    assert "Host 'ghost' not found" in capsys.readouterr().err


def test_cli_requires_host(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "--list"]) == 2
    assert "host-secrets <host>" in capsys.readouterr().err


def test_cli_requires_exactly_one_mode(cli_env, monkeypatch, capsys):
    assert _run(monkeypatch, ["host-secrets", "devbox"]) == 2
    assert "exactly one of --materialize, --list, --path" in capsys.readouterr().err
    assert _run(monkeypatch, ["host-secrets", "devbox", "--list", "--materialize"]) == 2
    assert "exactly one of --materialize, --list, --path" in capsys.readouterr().err


def test_up_host_with_secrets_does_not_materialize(cli_env, monkeypatch, capsys, tmp_path):
    """S14.3a is explicit-only: transport verbs never materialize host secrets."""
    import ciu.transport_ssh as transport
    seen = {"exec": [], "sync": []}
    monkeypatch.setattr(
        transport, "ssh_exec",
        lambda cfg, argv, **kw: seen["exec"].append((cfg, argv)) or 0,
    )
    monkeypatch.setattr(
        transport, "ssh_sync",
        lambda cfg, local, target, **kw: seen["sync"].append((cfg, local, target)) or 0,
    )
    monkeypatch.setitem(
        sys.modules, "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root: {},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "devbox"])
    from ciu import cli
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert len(seen["sync"]) == 1 and len(seen["exec"]) == 1
    store = host_secret_store(cli_env, "devbox", "ts_authkey")
    assert not store.exists()
    assert "TS_AUTHKEY" not in capsys.readouterr().out
