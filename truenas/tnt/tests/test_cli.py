import json
import os
import stat

import pytest

from tnt.cli import build_cli, identity, main


def run(capsys, args, opener=None, **kw):
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
    assert "tls: insecure" in out and "key_file_problem: None" in out
    code, out, _ = run(capsys, ["profile", "show", "--config", str(config), "--json"])
    assert json.loads(out)["name"] == "lab"
    assert json.loads(out)["api_key"] == "***"
    os.chmod(json.loads(out)["api_key_file"], 0o644)
    code, out, _ = run(capsys, ["profile", "show", "lab", "--config", str(config), "--json"])
    assert "group/other" in json.loads(out)["key_file_problem"]


def test_profile_show_masks_inline_password_json(capsys, tmp_path):
    path = tmp_path / "tnt.toml"
    path.write_text('''schema_version = 1
[profiles.lab]
url = "wss://lab.example.invalid/api/current"
username = "operator"
auth = "password"
password = "fake-password-material"
''')
    path.chmod(0o600)
    code, out, _ = run(capsys, ["profile", "show", "lab", "--config", str(path), "--json"])
    assert code == 0
    assert json.loads(out)["password"] == "***"
    assert "fake-password-material" not in out


def test_profile_errors_are_one_line_with_exit_1(capsys, tmp_path):
    code, out, err = run(capsys, ["profile", "list", "--config", str(tmp_path / "nope.toml")])
    assert code != 0 and "no profile file" in err and "Traceback" not in err


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
    path.write_text('schema_version = 1\n[profiles.a]\nurl = "ws://127.0.0.1:1/api/current"\nusername = "u"\nauth = "api_key"\n')
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
    key = next(p for p in config.parent.iterdir() if p.suffix == ".key")
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
    assert main(["profile", "list", "--config", str(config)]) == 0


def test_python_dash_m_entry_point():
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-m", "tnt", "--version"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip()
