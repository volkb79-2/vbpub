import json
import os
import stat

import pytest

from tnt.cli import build_cli, identity, main


def run(capsys, args, opener=None, **kw):
    args = list(args)
    # The shared test fixture creates a conventional sibling secrets file.
    # Pass it explicitly because production config and secrets lookup is independent.
    if "--config" in args and "--secrets" not in args:
        config_path = args[args.index("--config") + 1]
        secrets_path = os.path.join(os.path.dirname(config_path), "tnt.secrets.toml")
        if os.path.isfile(secrets_path):
            args.extend(["--secrets", secrets_path])
    cli = build_cli(opener=opener) if opener else build_cli()
    code = cli.run(argv=args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_version(capsys):
    code, out, _ = run(capsys, ["--version"])
    assert code == 0 and out.strip()
    assert identity().command == "tnt"


def test_profile_list_and_show_never_print_keys(capsys, config):
    code, out, _ = run(capsys, ["profile", "list", "--config", str(config), "--json"])
    rows = json.loads(out)
    assert code == 0 and [r["name"] for r in rows] == ["lab", "throwaway"] and rows[0]["default"]
    assert "notarealkey" not in out
    code, out, _ = run(capsys, ["profile", "list", "--config", str(config)])
    assert "* lab" in out and "tls=insecure" in out
    code, out, _ = run(capsys, ["profile", "show", "throwaway", "--config", str(config)])
    assert "tls: insecure" in out and "credentials_configured: True" in out
    code, out, _ = run(capsys, ["profile", "show", "--config", str(config), "--json"])
    assert json.loads(out)["name"] == "lab"
    assert json.loads(out)["credentials_configured"] is True
    assert "fakefakefake" not in out


def test_profile_command_rejects_credentials_in_non_secret_config(capsys, tmp_path):
    path = tmp_path / "tnt.toml"
    path.write_text('''schema_version = 1
[hosts.lab]
url = "wss://lab.example.invalid/api/current"
username = "operator"
auth = "password"
password = "fake-password-material"
''')
    path.chmod(0o600)
    code, out, err = run(capsys, ["profile", "show", "lab", "--config", str(path), "--json"])
    assert code != 0 and "credentials belong in tnt.secrets.toml" in err
    assert "fake-password-material" not in out


def test_profile_errors_are_one_line_with_exit_1(capsys, tmp_path):
    code, out, err = run(capsys, ["profile", "list", "--config", str(tmp_path / "nope.toml")])
    assert code != 0 and "no config file" in err and "Traceback" not in err


def test_cli_requires_a_named_host_when_multiple_hosts_have_no_default(capsys, config, opener):
    text = config.read_text().replace('default_host = "lab"\n', "")
    config.write_text(text)
    config.chmod(0o600)
    code, out, err = run(capsys, ["disks", "--config", str(config)], opener)
    assert code != 0 and "lab, throwaway" in err and "--host NAME" in err
    code, out, err = run(capsys, ["disks", "--host", "lab", "--config", str(config), "--json"], opener)
    assert code == 0 and json.loads(out)


def test_hosts_cli_add_rename_default_and_remove_use_the_paired_files(capsys, config, monkeypatch):
    from tnt import profile as prof
    from tnt.host_store import load_host_store

    monkeypatch.setattr("tnt.cli.getpass.getpass", lambda _prompt: "1-created-placeholder")
    code, out, err = run(capsys, ["hosts", "add", "staging", "--config", str(config),
                                  "--url", "wss://staging.example.invalid/api/current", "--username", "operator",
                                  "--auth", "api_key", "--make-default"])
    assert code == 0 and "Added host staging" in out and "1-created-placeholder" not in out + err
    hosts, default, entries = load_host_store(prof.resolve_paths(config, config.with_name("tnt.secrets.toml")))
    assert default == "staging" and "staging" in hosts
    assert entries["staging"].api_key.reveal() == "1-created-placeholder"

    code, out, err = run(capsys, ["hosts", "edit", "staging", "--config", str(config), "--rename", "edge",
                                  "--description", "Renamed connection"])
    assert code == 0 and "Updated host edge" in out
    hosts, default, entries = load_host_store(prof.resolve_paths(config, config.with_name("tnt.secrets.toml")))
    assert default == "edge" and "staging" not in hosts and hosts["edge"].description == "Renamed connection"
    assert entries["edge"].api_key.reveal() == "1-created-placeholder"

    code, out, err = run(capsys, ["hosts", "remove", "edge", "--config", str(config), "--confirm", "edge"])
    assert code == 0 and "Removed host edge" in out
    assert "edge" not in load_host_store(prof.resolve_paths(config, config.with_name("tnt.secrets.toml")))[0]


def test_hosts_show_masks_secret_values_and_path_fields(capsys, config):
    code, out, err = run(capsys, ["hosts", "show", "lab", "--config", str(config), "--json"])
    result = json.loads(out)
    assert code == 0 and result["credentials_configured"] is True
    assert "1-fakefakefake" not in out and "id_ed25519" not in out


def test_hosts_edit_tracks_ssh_facts_clear_and_default_confirmation(capsys, config):
    from tnt.host_store import load_host_store
    from tnt.profile import resolve_paths

    code, out, err = run(capsys, ["hosts", "edit", "lab", "--config", str(config),
                                  "--ssh-user", "admin", "--ssh-key-file", "/tmp/first-key",
                                  "--ssh-key-file", "/tmp/second-key", "--ssh-known-hosts", "/tmp/known_hosts",
                                  "--tag", "primary", "--tag", "production", "--description", "Updated lab"])
    assert code == 0 and "Updated host lab" in out
    profile = load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[0]["lab"]
    assert profile.ssh_user == "admin" and profile.ssh_key_files == ("/tmp/first-key", "/tmp/second-key")
    assert profile.ssh_known_hosts == "/tmp/known_hosts"
    assert profile.tags == ("primary", "production") and profile.description == "Updated lab"

    code, out, err = run(capsys, ["hosts", "edit", "lab", "--config", str(config), "--clear-ssh-keys"])
    assert code == 0 and not load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[0]["lab"].ssh_key_files
    code, out, err = run(capsys, ["hosts", "set-default", "throwaway", "--config", str(config)])
    assert code == 0 and load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[1] == "throwaway"
    code, out, err = run(capsys, ["hosts", "remove", "lab", "--config", str(config), "--confirm", "other"])
    assert code != 0 and "also removes its stored credential" in err
    code, out, err = run(capsys, ["hosts", "edit", "missing", "--config", str(config), "--description", "x"])
    assert code != 0 and "unknown host" in err


def test_hosts_cli_supports_password_and_private_key_file_without_echo(capsys, config, monkeypatch):
    from tnt.host_store import load_host_store
    from tnt.profile import resolve_paths

    monkeypatch.setattr("tnt.cli.getpass.getpass", lambda _prompt: "placeholder-password-value")
    code, out, err = run(capsys, ["hosts", "add", "password-host", "--config", str(config),
                                  "--url", "wss://password-host.example.invalid/api/current", "--username", "operator",
                                  "--auth", "password", "--description", "Password host"])
    assert code == 0 and "placeholder-password-value" not in out + err
    entries = load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[2]
    assert entries["password-host"].password.reveal() == "placeholder-password-value"

    code, out, err = run(capsys, ["hosts", "add", "key-file-host", "--config", str(config),
                                  "--url", "wss://key-file-host.example.invalid/api/current", "--username", "operator",
                                  "--api-key-file", "/tmp/tnt-placeholder-key"])
    assert code == 0 and "tnt-placeholder-key" not in out + err
    entry = load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[2]["key-file-host"]
    assert entry.api_key is None and entry.api_key_file == "/tmp/tnt-placeholder-key"

    code, out, err = run(capsys, ["hosts", "edit", "key-file-host", "--config", str(config),
                                  "--api-key-file", "/tmp/another-placeholder-key"])
    assert code == 0
    assert load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[2]["key-file-host"].api_key_file == "/tmp/another-placeholder-key"

    monkeypatch.setattr("tnt.cli.getpass.getpass", lambda _prompt: "")
    code, out, err = run(capsys, ["hosts", "add", "empty-secret", "--config", str(config),
                                  "--url", "wss://empty-secret.example.invalid/api/current", "--username", "operator"])
    assert code != 0 and "must not be empty" in err


def test_hosts_cli_insecure_ack_and_connection_test_are_read_only(capsys, config, opener, server, monkeypatch):
    import builtins
    from tnt.host_store import load_host_store
    from tnt.profile import resolve_paths

    monkeypatch.setattr(builtins, "input", lambda _prompt: "INSECURE")
    monkeypatch.setattr("tnt.cli.getpass.getpass", lambda _prompt: server.secret().reveal())
    code, out, err = run(capsys, ["hosts", "add", "temporary", "--config", str(config),
                                  "--url", "wss://temporary.example.invalid/api/current", "--username", "operator",
                                  "--tls", "insecure"])
    assert code == 0 and "verification will be disabled" in err
    assert load_host_store(resolve_paths(config, config.with_name("tnt.secrets.toml")))[0]["temporary"].insecure
    opened = []
    closed = []

    def read_only_opener(profile, **kwargs):
        assert kwargs["allow_write"] is False
        session = opener(profile, **kwargs)
        assert session.rpc.read_only
        original_close = session.close
        session.close = lambda: (closed.append(True), original_close())
        opened.append(session)
        return session

    code, out, err = run(capsys, ["hosts", "test", "temporary", "--config", str(config)], read_only_opener)
    assert code != 0 and "pass --insecure" in err
    assert not opened
    code, out, err = run(capsys, ["hosts", "test", "temporary", "--config", str(config), "--insecure", "--json"], read_only_opener)
    assert code == 0 and json.loads(out)["name"] == "temporary" and closed == [True]
    code, out, err = run(capsys, ["hosts", "test", "lab", "--config", str(config), "--json"], opener)
    result = json.loads(out)
    assert code == 0 and result["generation"] == "v27" and result["name"] == "lab"
    assert "1-fakefakefake" not in out + err
    code, out, err = run(capsys, ["hosts", "test", "missing", "--config", str(config)], opener)
    assert code != 0 and "unknown host" in err


def test_cli_refuses_api_key_file_for_password_auth(capsys, config):
    code, out, err = run(capsys, ["hosts", "add", "bad-file-auth", "--config", str(config),
                                  "--url", "wss://bad-file-auth.example.invalid/api/current", "--username", "operator",
                                  "--auth", "password", "--api-key-file", "/tmp/key"])
    assert code != 0 and "only with auth=api_key" in err


def test_hosts_add_validation_and_confirmation_refusal_and_hosts_list(capsys, config, monkeypatch):
    import builtins

    code, out, err = run(capsys, ["hosts", "add", "missing-facts", "--config", str(config)])
    assert code != 0 and "requires --url and --username" in err

    monkeypatch.setattr(builtins, "input", lambda _prompt: "nope")
    code, out, err = run(capsys, ["hosts", "add", "insecure-refused", "--config", str(config),
                                  "--url", "wss://insecure-refused.example.invalid/api/current",
                                  "--username", "operator", "--tls", "insecure"])
    assert code != 0 and "was not confirmed" in err

    code, out, err = run(capsys, ["hosts", "list", "--config", str(config), "--json"])
    assert code == 0 and [row["name"] for row in json.loads(out)] == ["lab", "throwaway"]


def test_tui_starts_with_picker_when_multiple_hosts_have_no_default(capsys, config, monkeypatch):
    text = config.read_text().replace('default_host = "lab"\n', "")
    config.write_text(text)
    config.chmod(0o600)
    captured = []

    import tnt.tui.app as app_mod

    def run_app(self, **_kwargs):
        captured.append((self.session, self.session_controller.active_host, sorted(self.session_controller.hosts)))

    monkeypatch.setattr(app_mod.TntApp, "run", run_app)
    code, out, err = run(capsys, ["tui", "--config", str(config)])
    assert code == 0 and captured == [(None, None, ["lab", "throwaway"])]


def test_disks_json_and_table(capsys, config, opener):
    code, out, _ = run(capsys, ["disks", "--config", str(config), "--json"], opener)
    rows = {r["name"]: r for r in json.loads(out)}
    assert code == 0 and rows["sdc"]["free_upper_bound_bytes"] == 256 * 1024 ** 3 and rows["sdd"]["partitions"] is None
    code, out, _ = run(capsys, ["disks", "--config", str(config)], opener)
    assert out.splitlines()[0].split() == ["NAME", "SIZE", "TYPE", "MODEL", "SERIAL", "POOL", "PARTS", "FREE", "(UPPER", "BOUND)"]
    assert "<= 256.0 GiB" in out and " ? " in out + " "


def test_disks_with_insecure_profile_needs_the_flag(capsys, config):
    code, out, err = run(capsys, ["disks", "--config", str(config), "--profile", "throwaway"])
    assert code != 0 and ("pass --insecure" in err or "no API key" in err)


def test_disks_refuses_ws_without_the_test_hook(capsys, tmp_path, server):
    path = tmp_path / "p.toml"
    path.write_text('schema_version = 1\n[hosts.a]\nurl = "ws://127.0.0.1:1/api/current"\nusername = "u"\nauth = "api_key"\n')
    path.chmod(0o600)
    code, out, err = run(capsys, ["disks", "--config", str(path)])
    assert code != 0 and "ws://" in err


def test_doctor_offline_and_online(capsys, config, opener, server):
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--json"], opener)
    checks = {c["name"]: c for c in json.loads(out)["checks"]}
    assert code == 0 and checks["connect"]["status"] == "skip" and checks["credentials"]["status"] == "ok"
    assert checks["python"]["status"] == checks["deps"]["status"] == checks["profiles"]["status"] == "ok"
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--online", "--json"], opener)
    checks = {c["name"]: c for c in json.loads(out)["checks"]}
    assert code == 0 and checks["connect"]["status"] == "ok" and checks["connect"]["details"]["generation"] == "v27"


def test_doctor_reports_failures_without_a_profile_file(capsys, tmp_path):
    code, out, _ = run(capsys, ["doctor", "--config", str(tmp_path / "none.toml"), "--json"])
    checks = {c["name"]: c for c in json.loads(out)["checks"]}
    assert checks["profiles"]["status"] == "warn" and checks["credentials"]["status"] == "skip"


def test_doctor_key_check_fails_on_loose_mode(capsys, config):
    key = config.parent / "lab.key"
    key.write_text("1-notarealkey\n")
    key.chmod(0o600)
    secrets = config.parent / "tnt.secrets.toml"
    secrets.write_text(f'''schema_version = 1
[hosts.lab]
api_key_file = "{key}"

[hosts.throwaway]
api_key = "1-throwaway-placeholder"
''')
    secrets.chmod(0o600)
    key.chmod(0o644)
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--json"])
    checks = {c["name"]: c for c in json.loads(out)["checks"]}
    assert code == 1 and checks["credentials"]["status"] == "fail" and "notarealkey" not in out


def test_doctor_connect_failure(capsys, config):
    from tnt.errors import TransportError

    def failing(profile, **kw):
        raise TransportError("auth", "login failed")
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--online", "--check", "connect", "--json"], failing)
    assert code == 1 and json.loads(out)["checks"][0]["status"] == "fail"


def test_doctor_without_textual_warns(capsys, config, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "textual":
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--check", "deps", "--json"])
    assert code == 0 and json.loads(out)["checks"][0]["status"] == "warn"


def test_doctor_without_client_fails(capsys, config, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "truenas_api_client":
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--check", "deps", "--json"])
    assert code == 1 and json.loads(out)["checks"][0]["status"] == "fail"


def test_doctor_python_too_old(capsys, config, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "version_info", (3, 9, 0, "final", 0))
    code, out, _ = run(capsys, ["doctor", "--config", str(config), "--check", "python", "--json"])
    assert code == 1 and json.loads(out)["checks"][0]["status"] == "fail"


def test_tui_verb_without_textual_is_a_clean_failure(capsys, config, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.endswith("tui.app") or name == "textual.app":
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    code, out, err = run(capsys, ["tui", "--config", str(config)])
    assert code == 2 and "pip install" in err


def test_tui_verb_opens_the_app_and_always_closes_the_session(capsys, config, opener, monkeypatch):
    closed = []
    ran = []

    def counting(profile, **kw):
        session = opener(profile, **kw)
        original = session.close
        session.close = lambda: (closed.append(1), original())
        ran.append(kw)
        return session
    import tnt.tui.app as app_mod
    monkeypatch.setattr(app_mod.TntApp, "run", lambda self, **k: ran.append(self.session.banner()))
    code, out, _ = run(capsys, ["tui", "--config", str(config), "--allow-write"], counting)
    assert code == 0 and closed == [1] and ran[0]["allow_write"] is True and "WRITE ENABLED" in ran[1]


def test_main_uses_the_real_opener(capsys, config, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tnt"])
    assert main(["profile", "list", "--config", str(config), "--secrets",
                 str(config.with_name("tnt.secrets.toml"))]) == 0


def test_python_dash_m_entry_point():
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-m", "tnt", "--version"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip()
def test_python_module_entrypoint_delegates_to_main(monkeypatch):
    import runpy
    import sys
    import tnt.cli

    monkeypatch.setattr(tnt.cli, "main", lambda: 23)
    monkeypatch.setattr(sys, "argv", ["tnt", "--help"])
    try:
        runpy.run_module("tnt.__main__", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 23
    else:
        raise AssertionError("module entry point must exit with the CLI status")
