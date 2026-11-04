"""Fake-root oracles for scripts/mdt-cgprofile-install.sh (MDT-055).

The installer runs against a throwaway root (MDT_CGP_ROOT) with a fake python (`-m venv` builds a
venv of scripts), a fake `pip`, `cgpd`, `cgpctl` and `systemctl`. Nothing here starts, stops or reads
the live cgprofile daemon, a socket or the real /opt, /etc or systemd.

Covered: fresh install, no-op rerun without a restart, an empty or mismatching pin refused, an upgrade
that restarts exactly once after a drain, drain refusal with the sessions named (exit 3, old state
kept), --force-restart, config-only change as reload with the overrides file untouched, the immutable
version directory, rollback without a download, pruning, ENABLE=0, and the version-to-keys table.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mdt-cgprofile-install.sh"

FAKE_PYTHON = r"""#!/usr/bin/env bash
# fake python3: `-m venv DIR` builds a venv of scripts; everything else is the real python3.
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/pip" <<'PIP'
#!/usr/bin/env bash
venv="$(cd "$(dirname "$0")/.." && pwd)"
echo "pip $*" >> "$FAKE_STATE/pip.log"
links=""; spec=""
while [ "$#" -gt 0 ]; do
  case "$1" in --find-links) links="$2"; shift 2 ;; --no-index|--quiet|install) shift ;; *) spec="$1"; shift ;; esac
done
version="${spec#cgprofile==}"
[ -f "$links/cgprofile-$version-py3-none-any.whl" ] || { echo "no cgprofile wheel in $links" >&2; exit 1; }
ls "$links"/cli_extended-*-py3-none-any.whl >/dev/null || { echo "no cli_extended wheel" >&2; exit 1; }
cat > "$venv/bin/cgpd" <<CGPD
#!/usr/bin/env bash
case "\$1" in
  --version) echo "cgpd $version" ;;
  config) if grep -q bogus "\$4"; then echo "unknown key" >&2; exit 2; fi; echo "config ok: \$4" ;;
  unit) if [ "\$2" = check ]; then exit \${FAKE_UNIT_CHECK_RC:-0}; fi
        echo "# unit \$4 rev=\${FAKE_UNIT_REV:-1}"; echo "ExecStart=/opt/cgprofile/current/venv/bin/cgpd run" ;;
esac
CGPD
cat > "$venv/bin/cgpctl" <<CGPCTL
#!/usr/bin/env bash
case "\$1" in
  sessions) cat "\$FAKE_STATE/sessions.json" ;;
  doctor) cat "\$FAKE_STATE/doctor.json" 2>/dev/null || echo '{"checks":[{"name":"socket","status":"ok","summary":"x"}]}' ;;
  status) printf '{"schema":1,"ok":true,"daemon":{"version":"%s"},"config":{"last_reload_error":null}}' "\$(cat "\$FAKE_STATE/daemon_version")" ;;
esac
CGPCTL
chmod +x "$venv/bin/cgpd" "$venv/bin/cgpctl"
PIP
  chmod +x "$3/bin/pip"
  exit 0
fi
exec python3 "$@"
"""

FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE_STATE/systemctl.log"
case "$1" in
  is-active) [ -f "$FAKE_STATE/active" ]; exit $? ;;
  start|restart)
    if [ "$2" = cgpd.service ]; then
      touch "$FAKE_STATE/active"
      readlink "$MDT_CGP_ROOT/opt/cgprofile/current" > "$FAKE_STATE/daemon_version"
    fi ;;
esac
exit 0
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Host:
    """One throwaway host: fake root, wheel source directory, config file, state directory."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.root = tmp / "root"
        self.state = tmp / "state"
        self.source = tmp / "wheels"
        for path in (self.root, self.state, self.source):
            path.mkdir()
        (self.state / "sessions.json").write_text('{"schema":1,"ok":true,"sessions":[]}')
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name, body in (("python3-fake", FAKE_PYTHON), ("systemctl-fake", FAKE_SYSTEMCTL)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.python, self.systemctl = bin_dir / "python3-fake", bin_dir / "systemctl-fake"
        self.config = tmp / "host-setup.env"
        self.values: dict[str, str] = {}
        self.fixed_clix = self.add_wheel("cli_extended-0.5.1-py3-none-any.whl")

    def add_wheel(self, name: str, content: bytes | None = None) -> str:
        data = content if content is not None else f"wheel {name}".encode()
        (self.source / name).write_bytes(data)
        return sha(data)

    def release(self, version: str, content: bytes | None = None) -> str:
        return self.add_wheel(f"cgprofile-{version}-py3-none-any.whl", content)

    def configure(self, version: str | None, digest: str | None = None, **extra: str) -> None:
        values = {
            "CGPROFILE_ENABLE": "1",
            "CGPROFILE_WHEEL": f"{version}@sha256:{digest}" if version else "",
            "CGPROFILE_CLI_EXTENDED_WHEEL": f"0.5.1@sha256:{self.fixed_clix}",
            "CGPROFILE_WHEEL_SOURCE": str(self.source),
            "CGPROFILE_DRAIN_TIMEOUT": "1s",
            "CGPROFILE_GATES_SLICE": "dev-gates.slice",
            "CGPROFILE_OBSERVE_SLICES": "",
            "CGPROFILE_ALLOW_UIDS": "",
        }
        values.update(extra)
        self.values = values
        self.config.write_text("".join(f'{k}="{v}"\n' for k, v in values.items()))

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        environ = dict(os.environ)
        environ.update(
            MDT_CGP_ROOT=str(self.root), MDT_CGP_PYTHON=str(self.python), MDT_CGP_SYSTEMCTL=str(self.systemctl),
            MDT_CGP_POLL_SECONDS="0", MDT_CGP_READY_SECONDS="2", FAKE_STATE=str(self.state),
        )
        environ.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT), "--config", str(self.config), *args], capture_output=True, text=True, env=environ
        )

    def systemctl_log(self) -> list[str]:
        path = self.state / "systemctl.log"
        return path.read_text().splitlines() if path.exists() else []

    def restarts(self) -> int:
        return sum(1 for line in self.systemctl_log() if line in ("systemctl restart cgpd.service", "systemctl start cgpd.service"))

    @property
    def opt(self) -> Path:
        return self.root / "opt" / "cgprofile"

    def current(self) -> str | None:
        link = self.opt / "current"
        return os.readlink(link) if link.is_symlink() else None

    def versions(self) -> list[str]:
        if not self.opt.exists():
            return []
        return sorted(p.name for p in self.opt.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))


class CgprofileInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))

    def install(self, version: str, **extra: str) -> subprocess.CompletedProcess:
        digest = self.host.release(version)
        self.host.configure(version, digest, **extra)
        return self.host.run()

    def test_fresh_install_pins_renders_and_starts(self) -> None:
        result = self.install("2.0.0", CGPROFILE_ALLOW_UIDS="1003, 1004", CGPROFILE_OBSERVE_SLICES="dev-gates.slice dev-background.slice")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        host = self.host
        self.assertEqual(host.current(), "2.0.0")
        manifest = (host.opt / "2.0.0" / "MANIFEST").read_text()
        self.assertIn(f"cgprofile_sha256={host.values['CGPROFILE_WHEEL'].split('sha256:')[1]}", manifest)
        self.assertIn("cgprofile_wheel=cgprofile-2.0.0-py3-none-any.whl", manifest)
        pip_log = (host.state / "pip.log").read_text()
        self.assertIn("--no-index", pip_log)
        self.assertIn("cgprofile==2.0.0", pip_log)
        config = (host.root / "etc/cgprofile/cgpd.toml").read_text()
        self.assertIn('gates_slice = "dev-gates.slice"', config)
        self.assertIn('observe_slices = ["dev-gates.slice", "dev-background.slice"]', config)
        self.assertIn("allow_uids = [1003, 1004]", config)
        for unit in ("cgpd.service", "cgprofile.slice"):
            self.assertIn(f"# unit {unit}", (host.root / "etc/systemd/system" / unit).read_text())
        self.assertIn("ExecStart=/opt/cgprofile/current/venv/bin/cgpd run", (host.root / "etc/systemd/system/cgpd.service").read_text())
        log = host.systemctl_log()
        self.assertIn("systemctl enable cgprofile.slice cgpd.service", log)
        self.assertEqual(host.restarts(), 1)
        self.assertEqual(os.readlink(host.root / "usr/local/bin/cgpctl"), "/opt/cgprofile/current/venv/bin/cgpctl")
        self.assertEqual(os.stat(host.root / "etc/cgprofile/cgpd.toml").st_mode & 0o777, 0o644)

    def test_a_rerun_without_drift_does_not_restart(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        before = self.host.systemctl_log()
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no drift", result.stdout)
        self.assertEqual(self.host.restarts(), 1)
        self.assertFalse([line for line in self.host.systemctl_log()[len(before):] if "restart" in line or "start" in line or "reload" in line])

    def test_an_empty_pin_is_refused_while_enabled_and_nothing_changes(self) -> None:
        self.host.configure(None)
        result = self.host.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("CGPROFILE_WHEEL is empty while CGPROFILE_ENABLE=1", result.stderr)
        self.assertEqual(self.host.versions(), [])
        self.assertEqual(self.host.systemctl_log(), [])

    def test_a_malformed_or_one_x_pin_is_refused(self) -> None:
        self.host.configure("2.0.0", "abc")
        self.assertIn("not <version>@sha256", self.host.run().stderr)
        digest = self.host.release("1.2.0")
        self.host.configure("1.2.0", digest)
        result = self.host.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("1.x is the ciu container deployment", result.stderr)

    def test_a_digest_mismatch_is_refused_before_anything_is_unpacked(self) -> None:
        self.host.release("2.0.0")
        self.host.configure("2.0.0", "0" * 64)
        result = self.host.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match the pin", result.stderr)
        self.assertFalse((self.host.state / "pip.log").exists(), "pip must not have run")
        self.assertEqual(self.host.versions(), [])
        self.assertEqual(self.host.systemctl_log(), [])

    def test_an_upgrade_drains_then_restarts_exactly_once_and_keeps_the_old_version(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        result = self.install("2.0.1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no live sessions, proceeding", result.stdout)
        self.assertEqual(self.host.current(), "2.0.1")
        self.assertEqual(self.host.versions(), ["2.0.0", "2.0.1"])
        self.assertEqual(self.host.restarts(), 2)
        self.assertEqual((self.host.state / "daemon_version").read_text().strip(), "2.0.1")

    def test_live_sessions_at_the_deadline_refuse_and_keep_the_old_daemon(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        (self.host.state / "sessions.json").write_text(json.dumps({"schema": 1, "ok": True, "sessions": [
            {"session": "s-abc", "state": "live", "owner_uid": 1003, "elapsed_seconds": 42.5},
        ]}))
        restarts = self.host.restarts()
        result = self.install("2.0.1")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("live session=s-abc owner_uid=1003 age=42s", result.stderr)
        self.assertEqual(self.host.current(), "2.0.0", "the old current stays")
        self.assertEqual(self.host.restarts(), restarts, "no restart")
        self.assertEqual((self.host.opt / "PENDING").read_text().strip(), "2.0.1")
        self.assertTrue((self.host.opt / "2.0.1" / "MANIFEST").exists(), "the new version directory is installed and waiting")
        self.assertIn("ExecStart", (self.host.root / "etc/systemd/system/cgpd.service").read_text())
        self.assertIn("rev=1", (self.host.root / "etc/systemd/system/cgpd.service").read_text())

    def test_force_restart_proceeds_and_names_the_aborted_sessions(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        (self.host.state / "sessions.json").write_text(json.dumps({"sessions": [{"session": "s-x", "state": "live", "owner_uid": 7, "elapsed_seconds": 3}]}))
        self.install("2.0.1")
        result = self.host.run("--force-restart")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("session=s-x owner_uid=7 age=3s", result.stdout)
        self.assertEqual(self.host.current(), "2.0.1")
        self.assertFalse((self.host.opt / "PENDING").exists())

    def test_a_config_only_change_reloads_and_never_touches_the_overrides_file(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        overrides = self.host.root / "var/lib/cgprofile/overrides.toml"
        overrides.parent.mkdir(parents=True)
        overrides.write_text("max_sessions = 4\n")
        restarts = self.host.restarts()
        digest = self.host.values["CGPROFILE_WHEEL"].split("sha256:")[1]
        self.host.configure("2.0.0", digest, CGPROFILE_ALLOW_UIDS="1003")
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.host.restarts(), restarts)
        self.assertIn("systemctl reload cgpd.service", self.host.systemctl_log())
        self.assertIn("allow_uids = [1003]", (self.host.root / "etc/cgprofile/cgpd.toml").read_text())
        self.assertEqual(overrides.read_text(), "max_sessions = 4\n")

    def test_a_changed_unit_restarts(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        restarts = self.host.restarts()
        result = self.host.run(FAKE_UNIT_REV="2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.host.restarts(), restarts + 1)
        self.assertIn("systemctl daemon-reload", self.host.systemctl_log())
        self.assertIn("rev=2", (self.host.root / "etc/systemd/system/cgpd.service").read_text())

    def test_the_same_version_with_another_digest_is_refused(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        other = self.host.release("2.0.0", b"a different build")
        self.host.configure("2.0.0", other)
        result = self.host.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("immutable", result.stderr)
        self.assertEqual(self.host.current(), "2.0.0")

    def test_a_rejected_config_changes_nothing_live(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        restarts = self.host.restarts()
        digest = self.host.values["CGPROFILE_WHEEL"].split("sha256:")[1]
        # the fake cgpd refuses any config containing "bogus": a slice name can smuggle it in
        self.host.configure("2.0.0", digest, CGPROFILE_GATES_SLICE="bogus.slice")
        before = (self.host.root / "etc/cgprofile/cgpd.toml").read_text()
        result = self.host.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("cgpd config check refused", result.stderr)
        self.assertEqual((self.host.root / "etc/cgprofile/cgpd.toml").read_text(), before)
        self.assertEqual(self.host.restarts(), restarts)

    def test_rollback_to_the_previous_version_needs_no_download(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        old_pin = dict(self.host.values)
        self.assertEqual(self.install("2.0.1").returncode, 0)
        for wheel in self.host.source.glob("cgprofile-*"):
            wheel.unlink()
        self.host.configure("2.0.0", old_pin["CGPROFILE_WHEEL"].split("sha256:")[1])
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.host.current(), "2.0.0")
        self.assertEqual((self.host.state / "daemon_version").read_text().strip(), "2.0.0")

    def test_only_the_two_newest_versions_and_current_are_kept(self) -> None:
        for version in ("2.0.0", "2.0.1", "2.0.2"):
            self.assertEqual(self.install(version).returncode, 0)
        self.assertEqual(self.host.versions(), ["2.0.1", "2.0.2"])

    def test_disabled_leaves_an_existing_install_alone(self) -> None:
        self.assertEqual(self.install("2.0.0").returncode, 0)
        log = self.host.systemctl_log()
        self.host.configure("2.0.0", None, CGPROFILE_ENABLE="0")
        result = self.host.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("left alone", result.stdout)
        self.assertEqual(self.host.systemctl_log(), log)
        self.assertEqual(self.host.current(), "2.0.0")

    def test_an_absent_enable_key_means_not_managed(self) -> None:
        self.host.config.write_text('MDT_PSI_GUARD="report"\n')
        result = self.host.run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("not managed", result.stdout)
        self.assertFalse(self.host.opt.exists())

    def test_the_installer_is_wired_into_install_sh_and_the_example(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("scripts/mdt-cgprofile-install.sh", install)
        self.assertIn("--cgprofile-only", install)
        self.assertIn("--force-cgprofile-restart", install)
        example = (ROOT / "host-setup.env.example").read_text()
        for key in ("CGPROFILE_ENABLE", "CGPROFILE_WHEEL", "CGPROFILE_CLI_EXTENDED_WHEEL", "CGPROFILE_WHEEL_SOURCE", "CGPROFILE_DRAIN_TIMEOUT"):
            self.assertRegex(example, rf"(?m)^{key}=")


class CgprofileValidatorTests(unittest.TestCase):
    """The wizard's data-only validators (install.sh --validate-config) for the CGPROFILE_* keys."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util
        import sys

        sys.path.insert(0, str(ROOT))
        spec = importlib.util.spec_from_file_location("mdt_wizard_cgp", ROOT / "mdt-host-setup-wizard.py")
        cls.wizard = importlib.util.module_from_spec(spec)
        sys.modules["mdt_wizard_cgp"] = cls.wizard
        assert spec.loader is not None
        spec.loader.exec_module(cls.wizard)

    def test_every_cgprofile_key_of_the_example_has_a_strict_validator(self) -> None:
        validators = self.wizard._config_value_validators()
        example = self.wizard.parse_env_file((ROOT / "host-setup.env.example").read_text(), strict=True)
        keys = sorted(k for k in example if k.startswith("CGPROFILE_"))
        self.assertEqual(len(keys), 8, keys)
        self.assertEqual([k for k in keys if k not in validators], [])
        for key in keys:
            self.assertIsNone(validators[key](example[key]), key)  # the shipped defaults are valid

    def test_pin_grammar(self) -> None:
        pin = self.wizard.validate_cgprofile_pin
        self.assertIsNone(pin(""))
        self.assertIsNone(pin("2.0.0@sha256:" + "a" * 64))
        for bad in ("2.0.0", "2.0.0@sha256:abc", "2.0.0@sha256:" + "A" * 64, "latest@sha256:" + "a" * 64):
            self.assertIsNotNone(pin(bad), bad)

    def test_an_enabled_service_needs_both_pins_and_a_two_x_version(self) -> None:
        relation = self.wizard.cgprofile_relation_errors
        good = {"CGPROFILE_ENABLE": "1", "CGPROFILE_WHEEL": "2.0.0@sha256:" + "a" * 64,
                "CGPROFILE_CLI_EXTENDED_WHEEL": "0.5.1@sha256:" + "b" * 64}
        self.assertEqual(relation(good), [])
        self.assertEqual(relation({**good, "CGPROFILE_ENABLE": "0", "CGPROFILE_WHEEL": ""}), [])
        self.assertTrue(any("CGPROFILE_WHEEL is empty" in e for e in relation({**good, "CGPROFILE_WHEEL": ""})))
        self.assertTrue(any("CGPROFILE_CLI_EXTENDED_WHEEL is empty" in e for e in relation({**good, "CGPROFILE_CLI_EXTENDED_WHEEL": ""})))
        self.assertTrue(any("2.x or newer" in e for e in relation({**good, "CGPROFILE_WHEEL": "1.2.0@sha256:" + "a" * 64})))

    def test_other_grammars(self) -> None:
        w = self.wizard
        self.assertIsNone(w.validate_cgprofile_drain_timeout("10m"))
        for bad in ("10", "0m", "10min", ""):
            self.assertIsNotNone(w.validate_cgprofile_drain_timeout(bad), bad)
        self.assertIsNone(w.validate_cgprofile_wheel_source(""))
        self.assertIsNone(w.validate_cgprofile_wheel_source("/srv/wheels"))
        self.assertIsNone(w.validate_cgprofile_wheel_source("https://mirror.example/releases/download"))
        for bad in ("http://x", "relative/dir", "/srv/../etc", "https://x y"):
            self.assertIsNotNone(w.validate_cgprofile_wheel_source(bad), bad)
        self.assertIsNone(w.validate_cgprofile_slices("dev-gates.slice dev-background.slice"))
        self.assertIsNotNone(w.validate_cgprofile_slices("dev-gates"))
        self.assertIsNotNone(w.validate_cgprofile_slice("a--b.slice"))
        self.assertIsNone(w.validate_cgprofile_uids("1003, 1004 0"))
        self.assertIsNotNone(w.validate_cgprofile_uids("1003;rm"))


CHECK_SH = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")


def shell_function(source: str, name: str) -> str:
    import re

    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source)
    assert match, f"{name}() not found"
    return match.group(0)


class CgprofileCheckTests(unittest.TestCase):
    """check.sh's check_cgprofile_service against a fake-root install (no live daemon, no live socket)."""

    FUNCTION = shell_function(CHECK_SH, "check_cgprofile_service")

    def setUp(self) -> None:
        import socket

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))
        digest = self.host.release("2.0.0")
        self.host.configure("2.0.0", digest, CGPROFILE_ALLOW_UIDS="1003")
        self.assertEqual(self.host.run().returncode, 0)
        self.run_dir = self.host.root / "run" / "cgprofile"
        (self.run_dir / "observer").mkdir(parents=True)
        self.sockets = []
        for path in (self.run_dir / "ctl.sock", self.run_dir / "observer" / "resource-observer.sock"):
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(str(path))
            self.sockets.append(sock)
            self.addCleanup(sock.close)
        (self.host.state / "active").touch()

    def check(self, **env: str) -> tuple[list[str], list[str]]:
        host = self.host
        script = (
            'ok(){ echo "OK $*"; }; warn(){ echo "WARN $*"; }; fail(){ echo "FAIL $*"; }; info(){ echo "INFO $*"; }\n'
            f'CONF="{host.config}"; . "$CONF"\n{self.FUNCTION}\ncheck_cgprofile_service\n'
        )
        environ = dict(os.environ)
        environ.update(
            CGP_OPT=str(host.opt), CGP_ETC=str(host.root / "etc/cgprofile"), CGP_UNIT_DIR=str(host.root / "etc/systemd/system"),
            CGPROFILE_DIR=str(self.run_dir), CGP_SYSTEMCTL=str(host.systemctl), CGP_ANALYZE="true", CGP_INSTALLER=str(SCRIPT),
            FAKE_STATE=str(host.state), MDT_CGP_ROOT=str(host.root),
        )
        environ.update(env)
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=environ)
        lines = result.stdout.splitlines()
        return [l for l in lines if l.startswith("FAIL")], [l for l in lines if l.startswith("WARN")]

    def test_a_healthy_pinned_install_has_no_failure(self) -> None:
        failures, warnings = self.check()
        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])

    def test_a_different_pin_than_current_fails(self) -> None:
        digest = self.host.release("2.0.1")
        self.host.configure("2.0.1", digest, CGPROFILE_ALLOW_UIDS="1003")
        failures, _ = self.check()
        self.assertTrue(any("current points at '2.0.0', the pin is 2.0.1" in f for f in failures), failures)

    def test_config_drift_fails(self) -> None:
        digest = self.host.values["CGPROFILE_WHEEL"].split("sha256:")[1]
        self.host.configure("2.0.0", digest, CGPROFILE_ALLOW_UIDS="1003 1004")
        failures, _ = self.check()
        self.assertTrue(any("config drift" in f for f in failures), failures)

    def test_unit_drift_fails(self) -> None:
        failures, _ = self.check(FAKE_UNIT_CHECK_RC="2")
        self.assertTrue(any("unit drift" in f for f in failures), failures)

    def test_a_tampered_manifest_digest_fails(self) -> None:
        manifest = self.host.opt / "2.0.0" / "MANIFEST"
        manifest.write_text(manifest.read_text().replace("cgprofile_sha256=", "cgprofile_sha256=0"))
        failures, _ = self.check()
        self.assertTrue(any("MANIFEST" in f for f in failures), failures)

    def test_a_missing_socket_and_an_inactive_service_fail(self) -> None:
        (self.run_dir / "ctl.sock").unlink()
        (self.host.state / "active").unlink()
        failures, _ = self.check()
        self.assertTrue(any("ctl.sock is not a socket" in f for f in failures), failures)
        self.assertTrue(any("cgpd.service is not active" in f for f in failures), failures)

    def test_a_failing_doctor_check_fails_and_an_overrides_warning_only_warns(self) -> None:
        (self.host.state / "doctor.json").write_text(json.dumps({"checks": [
            {"name": "slice", "status": "fail", "summary": "unbounded"},
            {"name": "overrides", "status": "warn", "summary": "max_sessions shadows the file"},
        ]}))
        failures, warnings = self.check()
        self.assertTrue(any("failing checks: slice" in f for f in failures), failures)
        self.assertTrue(any("max_sessions shadows" in w for w in warnings), warnings)
        (self.host.state / "doctor.json").write_text(json.dumps({"checks": [
            {"name": "overrides", "status": "warn", "summary": "max_sessions shadows the file"},
        ]}))
        failures, warnings = self.check()
        self.assertEqual(failures, [])
        self.assertEqual(len(warnings), 1)

    def test_a_daemon_on_the_wrong_version_fails(self) -> None:
        (self.host.state / "daemon_version").write_text("1.2.0")
        failures, _ = self.check()
        self.assertTrue(any("does not report version 2.0.0" in f for f in failures), failures)

    def test_disabled_only_discloses(self) -> None:
        self.host.configure("2.0.0", None, CGPROFILE_ENABLE="0")
        failures, warnings = self.check()
        self.assertEqual((failures, warnings), ([], []))


if __name__ == "__main__":
    unittest.main()
