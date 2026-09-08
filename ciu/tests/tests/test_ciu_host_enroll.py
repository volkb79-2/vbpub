"""S14.7 / CIU-93 — `ciu host enroll` and the round-trip inventory writer.

Oracle map (handoff `ciu-P52-ciu93-host-enroll.md` Work item 6):

* **O1** — `TestStep1`: key pair at the S14.3a paths with modes 0700/0600, the
  printed block carries the exact public key + the version-pinned installer URL
  + the step-2 command, NOTHING is written to the inventory, and a second step 1
  without `--replace` is refused with no further filesystem change.
* **O4** — `TestStep2`: a wrong `--fingerprint` refuses and the inventory file is
  byte-identical before/after; the right one writes exactly S14.7c's row while a
  deliberately hostile fixture file survives byte-for-byte.
* **O5** — `TestControlledWrongImplementations`: the three wrong
  implementations, each pinned as its own test against the SPECIFIC oracle it
  must break (row-before-check, key material on stdout, whole-file rewrite).
* **O6** — `TestRenderedInstaller`: the committed `ciu/get.py`'s `enroll --help`
  flag set, its byte-identity with a fresh `cmru get-py --project ciu` render,
  and the release coordinates baked into `host_enroll` vs ciu's own `cmru.toml`.

The end-to-end chain oracle (step 1's printed one-liner → a real `get.py enroll`
run → step 2's keyscan/login) lives in `TestEnrollEndToEnd`, which needs a real
sshd and therefore a container; it skips where docker is unavailable.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ciu import host_enroll
from ciu.host_enroll import EnrollError
from ciu.hosts import load_hosts, resolve_hosts_file, write_host_row

CIU_ROOT = Path(__file__).resolve().parents[2]

# A deliberately hostile inventory: a multi-line string whose BODY looks like a
# table header, hand-aligned whitespace, comments in three positions, a quoted
# host name, a sub-table under the host being edited, and a table AFTER the
# hosts that must not move.
HOSTILE_FIXTURE = '''\
# vbpub host inventory — hand written, every byte here is the operator's
schema_note = """
decoy — this is a STRING, not a table:
[deploy.hosts.decoy]
ssh_host = "must-never-be-parsed-as-a-row"
"""

[deploy]
# a comment that belongs to [deploy]
some_flag = true

[deploy.hosts.core1]
ssh_host   =   "core1.example"      # aligned by hand, keep the comment
ssh_user = "root"
bundle_dir = "/opt/dstdns/current"
known_host = "ssh-ed25519 OLDHOSTKEY"

[deploy.hosts.core1.admin]
ssh_user = "admin"

[deploy.hosts."rs 1002"]
ssh_host = "rs1002.example"

[registry.ghcr]
url = "ghcr.io"
'''


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo root with a minimal, REAL global config (not a stubbed dict)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "ciu.global.defaults.toml.j2").write_text(
        '[deploy]\nproject_name = "demo"\n\n'
        '[topology.external]\npublic_fqdn = "ctl.example"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("CIU_HOSTS_FILE", raising=False)
    return root


@pytest.fixture
def config():
    return {"deploy": {"project_name": "demo"},
            "topology": {"external": {"public_fqdn": "ctl.example"}}}


@pytest.fixture
def real_host_key(tmp_path):
    """A REAL ed25519 host key + its real ssh-keygen fingerprint.

    Generated with the platform's ssh-keygen so the fingerprints these tests
    compare are OpenSSH's own, never a re-implementation's.
    """
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen unavailable")
    path = tmp_path / "hostkey"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-C", "fixture"],
        check=True, capture_output=True,
    )
    algo, blob = path.with_suffix(".pub").read_text().split()[:2]
    listing = subprocess.run(
        ["ssh-keygen", "-lf", str(path.with_suffix(".pub"))],
        check=True, capture_output=True, text=True,
    ).stdout
    fingerprint = next(tok for tok in listing.split() if tok.startswith("SHA256:"))
    return algo, blob, fingerprint


def _capture(out_lines):
    return "\n".join(out_lines)


def _collector():
    lines: list[str] = []
    return lines, lines.append


def _step1(repo, config, name="rs1002", **kw):
    lines, out = _collector()
    kw.setdefault("version", "7.11.0")
    code = host_enroll.enroll_step1(repo, name, config=config, out=out, **kw)
    return code, _capture(lines)


def _fake_keyscan(monkeypatch, algo, blob, *, extra=""):
    """Pin `ssh-keyscan`'s output — no live network call in a unit test."""
    def fake_run(argv, **kwargs):
        if argv[0] == "ssh-keyscan":
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"# comment line\n\nhost {algo} {blob}\n{extra}", stderr=""
            )
        return _real_run(argv, **kwargs)

    _real_run = host_enroll._run
    monkeypatch.setattr(host_enroll, "_run", fake_run)


# ---------------------------------------------------------------------------
# O1 — step 1
# ---------------------------------------------------------------------------


class TestStep1:
    def test_o1_key_pair_modes_printed_block_and_no_inventory_write(self, repo, config):
        hosts_file = repo / ".ciu.hosts.toml"
        code, out = _step1(repo, config)
        assert code == 0

        private, public = host_enroll.key_paths(repo, "rs1002")
        assert private.exists() and public.exists()
        assert stat.S_IMODE(private.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert stat.S_IMODE(public.stat().st_mode) == 0o644

        # the EXACT public key text, the pinned URL, the step-2 command
        pubkey = public.read_text().strip()
        assert pubkey in out
        assert pubkey.endswith("ciu@ctl.example:demo")
        assert (
            "https://github.com/volkb79-2/vbpub/releases/download/ciu-v7.11.0/get.py"
            in out
        )
        assert "ciu host enroll rs1002 --ssh-host" in out
        assert "--fingerprint" in out

        # NOTHING written to the inventory (S14.7a)
        assert not hosts_file.exists()
        assert load_hosts(repo) == {}

    def test_o1_second_step1_without_replace_is_refused_and_changes_nothing(
        self, repo, config
    ):
        _step1(repo, config)
        private, public = host_enroll.key_paths(repo, "rs1002")
        write_host_row(
            repo / ".ciu.hosts.toml", "rs1002",
            {"ssh_host": "x", "ssh_user": "ciu", "ssh_key": str(private),
             "known_host": "ssh-ed25519 K"},
        )
        before = (repo / ".ciu.hosts.toml").read_bytes()
        before_key = private.read_bytes()

        with pytest.raises(EnrollError, match=r"\[S14.7\].*already enrolled"):
            _step1(repo, config)

        assert (repo / ".ciu.hosts.toml").read_bytes() == before
        assert private.read_bytes() == before_key
        assert public.exists()

    def test_replace_rotates_the_key_pair(self, repo, config):
        _step1(repo, config)
        private, _public = host_enroll.key_paths(repo, "rs1002")
        write_host_row(
            repo / ".ciu.hosts.toml", "rs1002",
            {"ssh_host": "x", "ssh_user": "ciu", "ssh_key": str(private),
             "known_host": "ssh-ed25519 K"},
        )
        first = private.read_bytes()
        code, out = _step1(repo, config, replace=True)
        assert code == 0
        assert private.read_bytes() != first
        assert "--replace" in out

    def test_non_default_user_and_port_appear_in_the_step2_command(self, repo, config):
        _code, out = _step1(repo, config, user="ops", port=2222)
        assert "--user ops" in out
        assert "--port 2222" in out

    def test_from_and_docker_are_passed_to_the_target_one_liner(self, repo, config):
        _code, out = _step1(repo, config, from_pattern="10.0.0.0/8", docker=True)
        assert "--from 10.0.0.0/8" in out
        assert "--docker" in out

    def test_installer_url_override_is_used_verbatim(self, repo, config):
        _code, out = _step1(
            repo, config, installer_url_override="https://mirror.example/get.py"
        )
        assert "https://mirror.example/get.py" in out
        assert "releases/download" not in out


class TestControllerAndInstallerUrl:
    def test_flag_wins_over_config(self, config):
        assert host_enroll.resolve_controller("flag.example", config) == "flag.example"

    def test_config_public_fqdn_is_used_when_no_flag(self, config):
        assert host_enroll.resolve_controller(None, config) == "ctl.example"

    @pytest.mark.parametrize(
        "cfg",
        [
            {},
            {"topology": "not-a-table"},
            {"topology": {}},
            {"topology": {"external": "not-a-table"}},
            {"topology": {"external": {}}},
            {"topology": {"external": {"public_fqdn": "   "}}},
        ],
    )
    def test_missing_controller_is_a_refusal_never_a_guess(self, cfg):
        with pytest.raises(EnrollError, match="does not guess"):
            host_enroll.resolve_controller(None, cfg)

    def test_released_version_pins_the_url(self):
        url = host_enroll.installer_url("7.11.0")
        assert url.endswith("/ciu-v7.11.0/get.py")
        assert "latest" not in url

    @pytest.mark.parametrize(
        "version", ["7.11.1.dev3+g1234567", "7.11.0+dirty", "unknown"]
    )
    def test_unreleased_version_refuses_rather_than_printing_a_404(self, version):
        with pytest.raises(EnrollError, match="unreleased ciu"):
            host_enroll.installer_url(version)

    def test_step1_surfaces_the_unreleased_refusal(self, repo, config):
        with pytest.raises(EnrollError, match="unreleased ciu"):
            _step1(repo, config, version="7.12.0.dev1+gabc")


class TestKeyGenerationFailures:
    def test_ssh_keygen_failure_is_a_tagged_refusal(self, repo, config, monkeypatch):
        monkeypatch.setattr(
            host_enroll, "_run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"),
        )
        with pytest.raises(EnrollError, match=r"\[S14.7\] ssh-keygen failed"):
            host_enroll.generate_key_pair(repo, "rs1002", "ciu@a:b")

    def test_ssh_keygen_failure_with_no_stderr_still_names_the_exit(
        self, repo, monkeypatch
    ):
        monkeypatch.setattr(
            host_enroll, "_run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 3, "", ""),
        )
        with pytest.raises(EnrollError, match="no output"):
            host_enroll.generate_key_pair(repo, "rs1002", "ciu@a:b")


class TestAbort:
    def test_abort_removes_a_pending_key_pair_and_its_directory(self, repo, config):
        _step1(repo, config)
        private, public = host_enroll.key_paths(repo, "rs1002")
        lines, out = _collector()
        assert host_enroll.enroll_abort(repo, "rs1002", out=out) == 0
        assert not private.exists() and not public.exists()
        assert not private.parent.exists()
        assert "removed" in _capture(lines)

    def test_abort_keeps_a_directory_that_still_holds_other_host_secrets(
        self, repo, config
    ):
        _step1(repo, config)
        private, _public = host_enroll.key_paths(repo, "rs1002")
        (private.parent / "ts_authkey").write_text("x", encoding="utf-8")
        _lines, out = _collector()
        host_enroll.enroll_abort(repo, "rs1002", out=out)
        assert private.parent.exists()
        assert (private.parent / "ts_authkey").exists()

    def test_abort_refuses_for_an_enrolled_host(self, repo, config):
        _step1(repo, config)
        private, _public = host_enroll.key_paths(repo, "rs1002")
        write_host_row(
            repo / ".ciu.hosts.toml", "rs1002",
            {"ssh_host": "x", "ssh_user": "ciu", "ssh_key": str(private),
             "known_host": "ssh-ed25519 K"},
        )
        with pytest.raises(EnrollError, match="not a pending enrollment"):
            host_enroll.enroll_abort(repo, "rs1002")

    def test_abort_with_nothing_pending_is_a_refusal(self, repo):
        with pytest.raises(EnrollError, match="nothing to abort"):
            host_enroll.enroll_abort(repo, "never-enrolled")


# ---------------------------------------------------------------------------
# keyscan / fingerprint / login proof
# ---------------------------------------------------------------------------


class TestKeyscanAndFingerprint:
    def test_keyscan_drops_comments_blanks_and_short_rows(self, monkeypatch):
        monkeypatch.setattr(
            host_enroll, "_run",
            lambda argv, **kw: subprocess.CompletedProcess(
                argv, 0,
                "# comment\n\ntruncated-row\nhost ssh-ed25519 AAAA\n", ""),
        )
        assert host_enroll.keyscan("h", 22) == [("ssh-ed25519", "AAAA")]

    def test_keyscan_with_no_keys_refuses(self, monkeypatch):
        monkeypatch.setattr(
            host_enroll, "_run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "no route"),
        )
        with pytest.raises(EnrollError, match="no host key"):
            host_enroll.keyscan("h", 2222)

    def test_keyscan_with_no_keys_and_no_stderr_still_refuses(self, monkeypatch):
        monkeypatch.setattr(
            host_enroll, "_run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""),
        )
        with pytest.raises(EnrollError, match="no output"):
            host_enroll.keyscan("h", 22)

    def test_fingerprint_matches_openssh_for_a_real_key(self, real_host_key):
        algo, blob, expected = real_host_key
        assert host_enroll.fingerprint_of(algo, blob) == expected

    def test_fingerprint_of_a_broken_key_refuses(self):
        with pytest.raises(EnrollError, match="could not fingerprint"):
            host_enroll.fingerprint_of("ssh-ed25519", "not-base64")

    def test_fingerprint_without_a_sha256_token_refuses(self, monkeypatch):
        monkeypatch.setattr(
            host_enroll, "_run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "256 MD5:xx", ""),
        )
        with pytest.raises(EnrollError, match="no SHA256 fingerprint"):
            host_enroll.fingerprint_of("ssh-ed25519", "AAAA")


class TestSelectHostKey:
    def test_matching_fingerprint_selects_that_key(self):
        scanned = [("ssh-rsa", "R", "SHA256:r"), ("ssh-ed25519", "E", "SHA256:e")]
        assert host_enroll.select_host_key("h", scanned, "SHA256:e") == ("ssh-ed25519", "E")

    def test_mismatch_names_both_fingerprints(self):
        scanned = [("ssh-ed25519", "E", "SHA256:e")]
        with pytest.raises(EnrollError) as exc:
            host_enroll.select_host_key("h", scanned, "SHA256:other")
        assert "SHA256:e" in str(exc.value) and "SHA256:other" in str(exc.value)
        assert "man in the middle" in str(exc.value)

    def test_non_tty_without_fingerprint_is_refused(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        with pytest.raises(EnrollError, match="--fingerprint is required"):
            host_enroll.select_host_key(
                "h", [("ssh-ed25519", "E", "SHA256:e")], None, out=lambda *_: None
            )

    def test_tty_confirmation_pins_the_typed_fingerprint(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        lines, out = _collector()
        picked = host_enroll.select_host_key(
            "h", [("ssh-ed25519", "E", "SHA256:e")], None,
            out=out, input_fn=lambda _p: " SHA256:e ",
        )
        assert picked == ("ssh-ed25519", "E")
        assert "SHA256:e" in _capture(lines)

    def test_tty_confirmation_refuses_a_wrong_answer(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        with pytest.raises(EnrollError, match="does not match any key"):
            host_enroll.select_host_key(
                "h", [("ssh-ed25519", "E", "SHA256:e")], None,
                out=lambda *_: None, input_fn=lambda _p: "",
            )


class TestProveLogin:
    def test_success_returns_quietly(self):
        host_enroll.prove_login(
            {"ssh_user": "ciu"}, config={}, repo_root=Path("/"), name="h",
            exec_fn=lambda *a, **k: 0,
        )

    def test_missing_ciu_binary_names_the_reinstall_flag(self):
        with pytest.raises(EnrollError, match="--no-install"):
            host_enroll.prove_login(
                {"ssh_user": "ciu"}, config={}, repo_root=Path("/"), name="h",
                exec_fn=lambda *a, **k: 127,
            )

    def test_refused_login_names_the_user_and_the_pin(self):
        with pytest.raises(EnrollError, match="pinned host key was rejected"):
            host_enroll.prove_login(
                {"ssh_user": "ops"}, config={}, repo_root=Path("/"), name="h",
                exec_fn=lambda *a, **k: 255,
            )

    def test_default_exec_fn_is_the_real_transport_ssh_exec(self, monkeypatch):
        """The production path (no injected exec_fn) must reach
        ``transport_ssh.ssh_exec`` — the one function carrying S14.4a's
        fail-closed pinning and the temp-known-hosts mechanism."""
        from ciu import transport_ssh

        seen = {}

        def fake_exec(host_cfg, argv, *, config, repo_root):
            seen["argv"] = argv
            seen["known_host"] = host_cfg.get("known_host")
            return 0

        monkeypatch.setattr(transport_ssh, "ssh_exec", fake_exec)
        host_enroll.prove_login(
            {"ssh_user": "ciu", "known_host": "ssh-ed25519 K"},
            config={}, repo_root=Path("/"), name="h",
        )
        assert seen == {"argv": ["ciu", "version"], "known_host": "ssh-ed25519 K"}


# ---------------------------------------------------------------------------
# O4 — step 2
# ---------------------------------------------------------------------------


class TestStep2:
    def _prepared(self, repo, config, hosts_text=None):
        _step1(repo, config)
        hosts_file = repo / ".ciu.hosts.toml"
        if hosts_text is not None:
            hosts_file.write_text(hosts_text, encoding="utf-8")
        return hosts_file

    def test_o4_wrong_fingerprint_refuses_and_writes_nothing(
        self, repo, config, monkeypatch, real_host_key
    ):
        algo, blob, _fp = real_host_key
        hosts_file = self._prepared(repo, config, HOSTILE_FIXTURE)
        before = hosts_file.read_bytes()
        _fake_keyscan(monkeypatch, algo, blob)

        with pytest.raises(EnrollError, match="man in the middle"):
            host_enroll.enroll_step2(
                repo, "rs1002", config=config, ssh_host="rs1002.example",
                fingerprint="SHA256:definitely-not-it",
                out=lambda *_: None, exec_fn=lambda *a, **k: 0,
            )
        assert hosts_file.read_bytes() == before

    def test_o4_right_fingerprint_writes_the_row_and_preserves_every_other_byte(
        self, repo, config, monkeypatch, real_host_key
    ):
        algo, blob, fingerprint = real_host_key
        hosts_file = self._prepared(repo, config, HOSTILE_FIXTURE)
        _fake_keyscan(monkeypatch, algo, blob)
        lines, out = _collector()

        code = host_enroll.enroll_step2(
            repo, "rs1002", config=config, ssh_host="rs1002.example", user="ops",
            fingerprint=fingerprint, out=out, exec_fn=lambda *a, **k: 0,
        )
        assert code == 0

        after = hosts_file.read_text(encoding="utf-8")
        # byte-for-byte survival of everything that was already there
        assert after.startswith(HOSTILE_FIXTURE)

        private, _public = host_enroll.key_paths(repo, "rs1002")
        row = load_hosts(repo)["rs1002"]
        assert row == {
            "ssh_host": "rs1002.example",
            "ssh_user": "ops",
            "ssh_key": str(private),
            "known_host": f"{algo} {blob}",
        }
        assert "ssh_port" not in row  # only written when != 22
        # the operator's own tables are untouched
        doc = tomllib.loads(after)
        assert doc["deploy"]["hosts"]["core1"]["bundle_dir"] == "/opt/dstdns/current"
        assert doc["deploy"]["hosts"]["core1"]["admin"] == {"ssh_user": "admin"}
        assert doc["registry"]["ghcr"] == {"url": "ghcr.io"}
        assert "decoy" not in doc["deploy"]["hosts"]
        assert f"ciu ssh rs1002 -- ciu version" in _capture(lines)

    def test_non_default_port_is_written_and_pins_in_the_S14_4c_form(
        self, repo, config, monkeypatch, real_host_key
    ):
        algo, blob, fingerprint = real_host_key
        self._prepared(repo, config)
        _fake_keyscan(monkeypatch, algo, blob)
        pinned = {}

        def capture(host_cfg, argv, *, config, repo_root):
            pinned.update(host_cfg)
            return 0

        host_enroll.enroll_step2(
            repo, "rs1002", config=config, ssh_host="rs1002.example", port=2222,
            fingerprint=fingerprint, out=lambda *_: None, exec_fn=capture,
        )
        assert load_hosts(repo)["rs1002"]["ssh_port"] == 2222

        from ciu.transport_ssh import _known_hosts_file

        path = _known_hosts_file(pinned)
        try:
            assert Path(path).read_text().startswith("[rs1002.example]:2222 ")
        finally:
            os.unlink(path)

    def test_step2_without_a_step1_key_refuses(self, repo, config):
        with pytest.raises(EnrollError, match="Run 'ciu host enroll rs1002'"):
            host_enroll.enroll_step2(
                repo, "rs1002", config=config, ssh_host="a", fingerprint="SHA256:x",
                out=lambda *_: None, exec_fn=lambda *a, **k: 0,
            )

    def test_step2_on_an_enrolled_host_refuses_without_replace(
        self, repo, config, monkeypatch, real_host_key
    ):
        algo, blob, fingerprint = real_host_key
        hosts_file = self._prepared(repo, config)
        _fake_keyscan(monkeypatch, algo, blob)
        host_enroll.enroll_step2(
            repo, "rs1002", config=config, ssh_host="a", fingerprint=fingerprint,
            out=lambda *_: None, exec_fn=lambda *a, **k: 0,
        )
        before = hosts_file.read_bytes()
        with pytest.raises(EnrollError, match="already has a row"):
            host_enroll.enroll_step2(
                repo, "rs1002", config=config, ssh_host="b", fingerprint=fingerprint,
                out=lambda *_: None, exec_fn=lambda *a, **k: 0,
            )
        assert hosts_file.read_bytes() == before

    def test_replace_rotates_ssh_key_and_known_host_in_place(
        self, repo, config, monkeypatch, real_host_key
    ):
        algo, blob, fingerprint = real_host_key
        hosts_file = self._prepared(repo, config, HOSTILE_FIXTURE)
        _step1(repo, config, name="core1", replace=True)
        _fake_keyscan(monkeypatch, algo, blob)
        host_enroll.enroll_step2(
            repo, "core1", config=config, ssh_host="core1.example",
            fingerprint=fingerprint, replace=True,
            out=lambda *_: None, exec_fn=lambda *a, **k: 0,
        )
        after = hosts_file.read_text(encoding="utf-8")
        row = load_hosts(repo)["core1"]
        assert row["known_host"] == f"{algo} {blob}"
        assert row["bundle_dir"] == "/opt/dstdns/current"      # operator's key kept
        assert "# aligned by hand, keep the comment" in after  # its comment kept
        assert '[deploy.hosts."rs 1002"]' in after
        assert "must-never-be-parsed-as-a-row" in after

    def test_a_failed_login_proof_leaves_the_inventory_untouched(
        self, repo, config, monkeypatch, real_host_key
    ):
        algo, blob, fingerprint = real_host_key
        hosts_file = self._prepared(repo, config, HOSTILE_FIXTURE)
        before = hosts_file.read_bytes()
        _fake_keyscan(monkeypatch, algo, blob)
        with pytest.raises(EnrollError, match="not installed"):
            host_enroll.enroll_step2(
                repo, "rs1002", config=config, ssh_host="a", fingerprint=fingerprint,
                out=lambda *_: None, exec_fn=lambda *a, **k: 127,
            )
        assert hosts_file.read_bytes() == before


# ---------------------------------------------------------------------------
# the round-trip writer itself
# ---------------------------------------------------------------------------


class TestRoundTripWriter:
    def test_append_into_a_missing_file_creates_it(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        write_host_row(path, "h", {"ssh_host": "a", "ssh_user": "ciu"})
        assert path.read_text() == '[deploy.hosts.h]\nssh_host = "a"\nssh_user = "ciu"\n'

    def test_append_into_a_comment_only_file_does_not_add_a_stray_blank(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text("# nothing but a comment\n", encoding="utf-8")
        write_host_row(path, "h", {"ssh_host": "a"})
        assert path.read_text().startswith("# nothing but a comment\n\n[deploy.hosts.h]")

    def test_append_adds_the_missing_trailing_newline_first(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text('[deploy.hosts.x]\nssh_host = "x"', encoding="utf-8")
        write_host_row(path, "h", {"ssh_host": "a"})
        assert '\nssh_host = "x"\n\n[deploy.hosts.h]\n' in path.read_text()

    def test_a_top_level_hosts_file_keeps_its_own_form(self, tmp_path):
        """Writing [deploy.hosts.x] into a top-level [hosts.*] file would make
        load_hosts prefer the new table and stop seeing every existing row."""
        path = tmp_path / "hosts.toml"
        path.write_text('[hosts.old]\nssh_host = "old.example"\n', encoding="utf-8")
        write_host_row(path, "new", {"ssh_host": "new.example"})
        doc = tomllib.loads(path.read_text())
        assert set(doc["hosts"]) == {"old", "new"}
        assert "deploy" not in doc

    def test_a_deploy_hosts_file_wins_over_a_top_level_one(self, tmp_path):
        path = tmp_path / "hosts.toml"
        path.write_text(
            '[hosts.shadow]\nssh_host = "s"\n\n[deploy.hosts.real]\nssh_host = "r"\n',
            encoding="utf-8",
        )
        write_host_row(path, "new", {"ssh_host": "n"})
        doc = tomllib.loads(path.read_text())
        assert set(doc["deploy"]["hosts"]) == {"real", "new"}

    def test_existing_row_without_replace_is_refused(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text('[deploy.hosts.h]\nssh_host = "a"\n', encoding="utf-8")
        with pytest.raises(ValueError, match=r"\[S14.7\].*--replace"):
            write_host_row(path, "h", {"ssh_host": "b"})

    def test_a_managed_key_the_row_no_longer_sets_is_dropped(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text(
            '[deploy.hosts.h]\nssh_host = "a"\nssh_port = 2222\nssh_user = "root"\n',
            encoding="utf-8",
        )
        write_host_row(path, "h", {"ssh_host": "a", "ssh_user": "ciu"}, replace=True)
        doc = tomllib.loads(path.read_text())
        assert "ssh_port" not in doc["deploy"]["hosts"]["h"]

    def test_replace_inserts_new_keys_before_trailing_blank_lines(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text(
            '[deploy.hosts.h]\nssh_host = "a"\n\n\n[registry.x]\nurl = "u"\n',
            encoding="utf-8",
        )
        write_host_row(
            path, "h", {"ssh_host": "a", "known_host": "ssh-ed25519 K"}, replace=True
        )
        text = path.read_text()
        assert 'known_host = "ssh-ed25519 K"\n\n\n[registry.x]' in text

    def test_an_unparseable_inventory_is_refused_not_edited(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text("this is not = = toml\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid TOML"):
            write_host_row(path, "h", {"ssh_host": "a"})
        assert path.read_text() == "this is not = = toml\n"

    def test_a_dotted_key_row_cannot_be_edited_in_place_and_says_so(self, tmp_path):
        """A row spelled `deploy.hosts.h = {...}` (an inline table) has no
        header line to edit — refuse and say what to do, never guess."""
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text('deploy.hosts.h = { ssh_host = "a" }\n', encoding="utf-8")
        with pytest.raises(ValueError, match="could not be located"):
            write_host_row(path, "h", {"ssh_host": "b"}, replace=True)

    def test_existing_file_mode_is_preserved(self, tmp_path):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text('[deploy.hosts.x]\nssh_host = "x"\n', encoding="utf-8")
        os.chmod(path, 0o600)
        write_host_row(path, "h", {"ssh_host": "a"})
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_an_interrupted_write_leaves_no_temp_file_and_no_damage(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / ".ciu.hosts.toml"
        path.write_text('[deploy.hosts.x]\nssh_host = "x"\n', encoding="utf-8")
        before = path.read_bytes()

        def boom(_src, _dst):
            raise OSError("interrupted")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="interrupted"):
            write_host_row(path, "h", {"ssh_host": "a"})
        assert path.read_bytes() == before
        assert list(tmp_path.iterdir()) == [path]


class TestRoundTripInternals:
    """The scanner and the verifier, directly — these are the parts a naive
    per-key regex would get wrong, so they are pinned on their own."""

    def test_multi_line_strings_hide_table_headers_from_the_scanner(self):
        from ciu.hosts import _classify_lines

        kinds = [k for k, _p in _classify_lines(HOSTILE_FIXTURE)]
        headers = [
            p for k, p in _classify_lines(HOSTILE_FIXTURE) if k == "header"
        ]
        assert ["deploy", "hosts", "decoy"] not in headers
        assert ["deploy", "hosts", "rs 1002"] in headers
        assert kinds.count("header") == 5

    def test_a_literal_multi_line_string_is_tracked_too(self):
        from ciu.hosts import _classify_lines

        text = "a = '''\n[deploy.hosts.x]\n'''\n[real]\n"
        headers = [p for k, p in _classify_lines(text) if k == "header"]
        assert headers == [["real"]]

    def test_an_unterminated_multi_line_string_swallows_the_rest(self):
        from ciu.hosts import _classify_lines

        headers = [p for k, p in _classify_lines('a = """\n[x]\n') if k == "header"]
        assert headers == []

    def test_array_of_tables_headers_are_recognised(self):
        from ciu.hosts import _classify_lines

        headers = [p for k, p in _classify_lines("[[items]]\nn = 1\n") if k == "header"]
        assert headers == [["items"]]

    @pytest.mark.parametrize(
        "line",
        ["[unclosed\n", "[not a key path!]\n", "[]\n", "= 5\n"],
    )
    def test_lines_that_are_neither_a_header_nor_a_usable_key_are_inert(self, line):
        """Includes the unterminated-string cases: the scanner must run off the
        end of the line without hanging or mis-classifying what follows."""
        from ciu.hosts import _classify_lines

        assert [k for k, _p in _classify_lines(line)] == ["other"]

    @pytest.mark.parametrize(
        "line", ['a = "unterminated\n', "a = 'unterminated\n"]
    )
    def test_an_unterminated_single_line_string_does_not_leak_state(self, line):
        """The scanner must run off the end of an unterminated string without
        hanging and without leaving the next line inside a string."""
        from ciu.hosts import _classify_lines

        assert [k for k, _p in _classify_lines(line + "[real]\n")] == ["key", "header"]

    def test_escaped_quotes_and_comment_markers_do_not_confuse_the_scanner(self):
        from ciu.hosts import _classify_lines

        text = 'a = "he said \\" # not a comment"\n[real]\n'
        headers = [p for k, p in _classify_lines(text) if k == "header"]
        assert headers == [["real"]]

    def test_comment_start_ignores_hashes_inside_strings(self):
        from ciu.hosts import _comment_start

        assert _comment_start('k = "a # b"') is None
        assert _comment_start("k = 'a # b'  # real") == 13
        assert _comment_start('k = "a \\" # b"') is None
        assert _comment_start("k = 1") is None
        assert _comment_start('k = "unterminated') is None

    def test_toml_path_of_rejects_a_non_path_fragment(self):
        from ciu.hosts import _toml_path_of

        assert _toml_path_of("a.b") == ["a", "b"]
        assert _toml_path_of('"quoted key"') == ["quoted key"]
        assert _toml_path_of("not a path!") is None

    def test_verifier_refuses_an_unparseable_result(self):
        from ciu.hosts import _verify_round_trip

        with pytest.raises(ValueError, match="would not parse"):
            _verify_round_trip("", "= =\n", ["deploy", "hosts", "h"], {})

    def test_verifier_refuses_when_the_row_is_absent(self):
        from ciu.hosts import _verify_round_trip

        with pytest.raises(ValueError, match="does not contain the row"):
            _verify_round_trip("", "[other]\n", ["deploy", "hosts", "h"], {})

    def test_verifier_refuses_when_a_written_key_did_not_survive(self):
        from ciu.hosts import _verify_round_trip

        with pytest.raises(ValueError, match="did not survive"):
            _verify_round_trip(
                "", '[deploy.hosts.h]\nssh_host = "wrong"\n',
                ["deploy", "hosts", "h"], {"ssh_host": "right"},
            )

    def test_verifier_refuses_when_an_operator_key_would_be_lost(self):
        from ciu.hosts import _verify_round_trip

        before = '[deploy.hosts.h]\nssh_host = "a"\nbundle_dir = "/opt/x"\n'
        after = '[deploy.hosts.h]\nssh_host = "a"\n'
        with pytest.raises(ValueError, match="the operator's own key"):
            _verify_round_trip(before, after, ["deploy", "hosts", "h"],
                               {"ssh_host": "a"})

    def test_verifier_refuses_when_anything_outside_the_row_changed(self):
        from ciu.hosts import _verify_round_trip

        before = '[registry]\nurl = "a"\n'
        after = '[registry]\nurl = "b"\n\n[deploy.hosts.h]\nssh_host = "a"\n'
        with pytest.raises(ValueError, match="outside this host's own row"):
            _verify_round_trip(before, after, ["deploy", "hosts", "h"],
                               {"ssh_host": "a"})

    def test_without_path_tolerates_a_path_that_is_not_there(self):
        from ciu.hosts import _without_path

        assert _without_path({"a": 1}, ["x", "y", "z"]) == {"a": 1}
        assert _without_path({"a": "scalar"}, ["a", "b"]) == {"a": "scalar"}

    def test_row_at_returns_none_for_a_scalar_or_a_gap(self):
        from ciu.hosts import _row_at

        assert _row_at({"a": {"b": 1}}, ["a", "b"]) is None
        assert _row_at({"a": {}}, ["a", "b"]) is None

    def test_resolve_hosts_file_follows_the_reader_precedence(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("CIU_HOSTS_FILE", raising=False)
        assert resolve_hosts_file(tmp_path) == tmp_path / ".ciu.hosts.toml"
        (tmp_path / ".ciu.hosts.toml").write_text("", encoding="utf-8")
        assert resolve_hosts_file(tmp_path) == tmp_path / ".ciu.hosts.toml"
        override = tmp_path / "elsewhere.toml"
        override.write_text("", encoding="utf-8")
        monkeypatch.setenv("CIU_HOSTS_FILE", str(override))
        assert resolve_hosts_file(tmp_path) == override


# ---------------------------------------------------------------------------
# O5 — the three controlled wrong implementations
# ---------------------------------------------------------------------------


class TestControlledWrongImplementations:
    def test_wrong_1_writing_the_row_before_the_fingerprint_check_is_caught(
        self, repo, config, monkeypatch, real_host_key
    ):
        """Wrong implementation 1: write first, verify later. The oracle that
        must break is `test_o4_wrong_fingerprint_refuses_and_writes_nothing`,
        so the ORDER is asserted directly: nothing may touch the inventory
        before `select_host_key` has returned."""
        algo, blob, _fp = real_host_key
        _step1(repo, config)
        hosts_file = repo / ".ciu.hosts.toml"
        hosts_file.write_text(HOSTILE_FIXTURE, encoding="utf-8")
        _fake_keyscan(monkeypatch, algo, blob)

        events: list[str] = []
        real_select = host_enroll.select_host_key
        real_write = host_enroll.write_host_row

        def spy_select(*a, **k):
            events.append("select")
            return real_select(*a, **k)

        def spy_write(*a, **k):
            events.append("write")
            return real_write(*a, **k)

        monkeypatch.setattr(host_enroll, "select_host_key", spy_select)
        monkeypatch.setattr(host_enroll, "write_host_row", spy_write)
        monkeypatch.setattr(
            host_enroll, "prove_login",
            lambda *a, **k: events.append("prove"),
        )
        host_enroll.enroll_step2(
            repo, "rs1002", config=config, ssh_host="a",
            fingerprint=host_enroll.fingerprint_of(algo, blob),
            out=lambda *_: None,
        )
        assert events == ["select", "prove", "write"]

    def test_wrong_2_private_key_material_never_reaches_any_output(
        self, repo, config, monkeypatch, real_host_key, capsys
    ):
        """Wrong implementation 2: printing/logging the private half. Grep
        oracle over EVERYTHING both steps emit — the verb's own `out`, plus
        real stdout and stderr."""
        algo, blob, fingerprint = real_host_key
        lines, out = _collector()
        host_enroll.enroll_step1(
            repo, "rs1002", config=config, version="7.11.0", out=out
        )
        private, _public = host_enroll.key_paths(repo, "rs1002")
        secret = private.read_text(encoding="utf-8")
        first_line = secret.splitlines()[0]
        body = "".join(secret.splitlines()[1:-1])

        _fake_keyscan(monkeypatch, algo, blob)
        host_enroll.enroll_step2(
            repo, "rs1002", config=config, ssh_host="a", fingerprint=fingerprint,
            out=out, exec_fn=lambda *a, **k: 0,
        )
        captured = capsys.readouterr()
        haystack = _capture(lines) + captured.out + captured.err

        assert first_line == "-----BEGIN OPENSSH PRIVATE KEY-----"
        assert first_line not in haystack
        assert body[:40] not in haystack
        # the PATH is fine to print (S14.4b logs paths, never material)
        assert str(private) in haystack

    def test_wrong_3_a_whole_file_rewrite_is_caught_by_byte_comparison(
        self, tmp_path
    ):
        """Wrong implementation 3: `tomllib.load` → mutate → `tomli_w.dump`.
        It produces semantically equal TOML and would pass any parse-based
        check, so the oracle is byte comparison — which this pins."""
        import tomli_w

        path = tmp_path / ".ciu.hosts.toml"
        path.write_text(HOSTILE_FIXTURE, encoding="utf-8")
        write_host_row(path, "new", {"ssh_host": "n"})
        correct = path.read_text(encoding="utf-8")

        # the wrong implementation, run for real
        path.write_text(HOSTILE_FIXTURE, encoding="utf-8")
        doc = tomllib.loads(HOSTILE_FIXTURE)
        doc["deploy"]["hosts"]["new"] = {"ssh_host": "n"}
        path.write_text(tomli_w.dumps(doc), encoding="utf-8")
        rewritten = path.read_text(encoding="utf-8")

        assert tomllib.loads(correct) == tomllib.loads(rewritten)  # same MEANING
        assert correct.startswith(HOSTILE_FIXTURE)                 # ours preserves
        assert not rewritten.startswith(HOSTILE_FIXTURE)           # theirs does not
        assert "# aligned by hand, keep the comment" not in rewritten


# ---------------------------------------------------------------------------
# O6 — the rendered installer + the release coordinates
# ---------------------------------------------------------------------------


class TestRenderedInstaller:
    GET_PY = CIU_ROOT / "get.py"

    def test_committed_get_py_exists_and_is_executable(self):
        assert self.GET_PY.is_file()
        assert self.GET_PY.stat().st_mode & stat.S_IXUSR

    def test_enroll_help_lists_exactly_the_ki24_flag_set(self):
        result = subprocess.run(
            [sys.executable, str(self.GET_PY), "enroll", "--help"],
            capture_output=True, text=True, check=True,
        )
        for flag in (
            "--authorized-key", "--controller", "--user", "--name", "--from",
            "--docker", "--no-install", "--scope",
        ):
            assert flag in result.stdout, flag

    def test_the_key_line_uses_the_whitespace_from_form_not_a_comma(self):
        """cmru KI-24 measured live that `from="P",<type> …` is REJECTED by a
        real sshd (the key type lands inside the options field). The rendered
        installer must therefore emit `from="P" <type> …`."""
        text = self.GET_PY.read_text(encoding="utf-8")
        assert 'return f\'from="{from_pattern}" {body}\'' in text

    def test_release_coordinates_match_ciu_own_cmru_toml(self):
        cmru_toml = CIU_ROOT / "cmru.toml"
        if not cmru_toml.exists():
            pytest.skip("ciu/cmru.toml not available")
        doc = tomllib.loads(cmru_toml.read_text(encoding="utf-8"))
        assert doc["github"]["owner"] == host_enroll.INSTALLER_REPO_OWNER
        assert doc["github"]["repo"] == host_enroll.INSTALLER_REPO_NAME
        assert doc["project"]["prefix"] == host_enroll.INSTALLER_TAG_PREFIX
        assert "installer" in doc["project"]

    def test_release_publishes_get_py_as_an_asset(self):
        cmru_toml = CIU_ROOT / "cmru.toml"
        if not cmru_toml.exists():
            pytest.skip("ciu/cmru.toml not available")
        doc = tomllib.loads(cmru_toml.read_text(encoding="utf-8"))
        argv = doc["steps"]["push"]["commands"][0]["argv"]
        assert "--extra-asset" in argv
        assert argv[argv.index("--extra-asset") + 1] == "get.py"

    def test_render_is_byte_identical_to_the_committed_file(self):
        """O6's byte-identity half. It needs cmru's `get.py.tmpl`, which the
        installed cmru wheel does not ship (`[tool.setuptools.package-data]`
        packages only `templates/*.toml`), so it runs against a cmru source
        checkout when one is reachable and skips when it is not."""
        pytest.importorskip("cmru")
        from cmru import getpy

        template = getpy._TEMPLATE_PATH
        if not template.exists():
            template = CIU_ROOT.parent / "cmru" / "templates" / "get.py.tmpl"
        if not template.exists():
            pytest.skip("cmru's get.py.tmpl is not reachable from this checkout")
        if "def do_enroll" not in template.read_text(encoding="utf-8"):
            pytest.skip(
                "the reachable cmru get.py.tmpl predates KI-24 (no enroll "
                "subcommand), so it cannot have rendered the committed get.py"
            )

        from cmru.config import load_forge_config

        cfg = load_forge_config(CIU_ROOT / "cmru.toml")
        proj = cfg.projects["ciu"]
        ins = proj.installer
        rendered = getpy.render_get_py(
            project_name="ciu",
            repo_owner=cfg.github.owner,
            repo_name=cfg.github.repo,
            tag_prefix=proj.prefix,
            asset_suffix=ins.asset_suffix,
            install_dir_system=ins.install_dir_system,
            install_dir_user=ins.install_dir_user,
            entrypoint=ins.entrypoint or "",
            required_commands=ins.required_commands or None,
            preserve_paths=ins.preserve or None,
            wheel_specs=[(w.path, w.distribution) for w in ins.wheels] or None,
            manifest_name=ins.manifest_name,
            signature_name=ins.signature_name,
            template_path=template,
        )
        assert rendered == self.GET_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI — the `host` verb group
# ---------------------------------------------------------------------------


def _cli(monkeypatch, argv):
    from ciu import cli

    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.fixture
def pinned_version(monkeypatch):
    """Pin the control host's OWN reported ciu version for the CLI tests.

    Without this the outcome depends on how the interpreter running the suite
    got its ciu: a released wheel reports `7.11.0` and the verb prints a real
    release-asset URL, while a source checkout reports a setuptools-scm
    `.dev`/local version, for which `installer_url` deliberately REFUSES
    (S14.7d: version-pinned, never a URL that 404s). Both behaviours are
    tested — here, and in
    `test_an_unreleased_control_version_refuses_and_names_installer_url` —
    but neither may depend on the environment the gate happens to run in.
    """
    from ciu import cli

    monkeypatch.setattr(cli, "get_cli_version", lambda: "7.11.0")


@pytest.mark.usefixtures("pinned_version")
class TestHostVerbDispatch:
    def test_step1_through_the_cli(self, repo, monkeypatch, capsys):
        monkeypatch.setenv("REPO_ROOT", str(repo))
        assert _cli(monkeypatch, ["host", "enroll", "rs1002"]) == 0
        out = capsys.readouterr().out
        assert "ciu@ctl.example:demo" in out
        assert host_enroll.key_paths(repo, "rs1002")[0].exists()

    def test_define_root_is_honoured(self, repo, monkeypatch, capsys):
        monkeypatch.delenv("REPO_ROOT", raising=False)
        assert _cli(
            monkeypatch,
            ["host", "enroll", "rs1002", "--define-root", str(repo)],
        ) == 0
        assert host_enroll.key_paths(repo, "rs1002")[0].exists()

    def test_step2_through_the_cli(self, repo, monkeypatch, capsys, real_host_key):
        algo, blob, fingerprint = real_host_key
        monkeypatch.setenv("REPO_ROOT", str(repo))
        _cli(monkeypatch, ["host", "enroll", "rs1002"])
        capsys.readouterr()
        _fake_keyscan(monkeypatch, algo, blob)
        from ciu import transport_ssh

        monkeypatch.setattr(transport_ssh, "ssh_exec", lambda *a, **k: 0)
        assert _cli(monkeypatch, [
            "host", "enroll", "rs1002", "--ssh-host", "a.example",
            "--fingerprint", fingerprint,
        ]) == 0
        assert load_hosts(repo)["rs1002"]["ssh_host"] == "a.example"

    def test_abort_through_the_cli(self, repo, monkeypatch, capsys):
        monkeypatch.setenv("REPO_ROOT", str(repo))
        _cli(monkeypatch, ["host", "enroll", "rs1002"])
        assert _cli(monkeypatch, ["host", "enroll", "rs1002", "--abort"]) == 0
        assert not host_enroll.key_paths(repo, "rs1002")[0].exists()

    def test_abort_rejects_other_enrollment_flags(self, repo, monkeypatch, capsys):
        monkeypatch.setenv("REPO_ROOT", str(repo))
        assert _cli(monkeypatch, [
            "host", "enroll", "rs1002", "--abort", "--ssh-host", "a",
        ]) == 2
        assert "takes no other enrollment flags" in capsys.readouterr().err

    def test_fingerprint_without_ssh_host_is_refused(self, repo, monkeypatch, capsys):
        monkeypatch.setenv("REPO_ROOT", str(repo))
        assert _cli(monkeypatch, [
            "host", "enroll", "rs1002", "--fingerprint", "SHA256:x",
        ]) == 2
        assert "needs --ssh-host" in capsys.readouterr().err

    def test_an_enroll_error_exits_2_with_the_tagged_message(
        self, repo, monkeypatch, capsys
    ):
        monkeypatch.setenv("REPO_ROOT", str(repo))
        assert _cli(monkeypatch, [
            "host", "enroll", "rs1002", "--ssh-host", "a", "--fingerprint", "SHA256:x",
        ]) == 2
        err = capsys.readouterr().err
        assert "[ERROR] [S14.7]" in err and "step 1" in err

    def test_an_unreleased_control_version_refuses_and_names_installer_url(
        self, repo, monkeypatch, capsys
    ):
        """A source-checkout control host has no release asset to pin, so the
        verb refuses instead of printing a URL that 404s (S14.7d)."""
        from ciu import cli

        monkeypatch.setattr(cli, "get_cli_version", lambda: "7.12.0.dev4+gabcdef")
        monkeypatch.setenv("REPO_ROOT", str(repo))
        assert _cli(monkeypatch, ["host", "enroll", "rs1002"]) == 2
        err = capsys.readouterr().err
        assert "unreleased ciu" in err and "--installer-url" in err

    def test_installer_url_makes_an_unreleased_control_host_usable(
        self, repo, monkeypatch, capsys
    ):
        from ciu import cli

        monkeypatch.setattr(cli, "get_cli_version", lambda: "7.12.0.dev4+gabcdef")
        monkeypatch.setenv("REPO_ROOT", str(repo))
        assert _cli(monkeypatch, [
            "host", "enroll", "rs1002",
            "--installer-url", "https://mirror.example/ciu/get.py",
        ]) == 0
        assert "https://mirror.example/ciu/get.py" in capsys.readouterr().out

    def test_verb_help_is_the_host_block_not_the_top_level_usage(
        self, monkeypatch, capsys
    ):
        assert _cli(monkeypatch, ["host", "--help"]) == 0
        out = capsys.readouterr().out
        assert "ciu host enroll <name>" in out
        assert "CIU_SSH_INSECURE_TOFU is never set by this verb" in out

    def test_the_verb_never_sets_the_tofu_escape_hatch(
        self, repo, monkeypatch, capsys, real_host_key
    ):
        """S14.7d: the fingerprint flow is the SECURE alternative to
        CIU_SSH_INSECURE_TOFU, not a backdoor around it."""
        algo, blob, fingerprint = real_host_key
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        assert _cli(monkeypatch, ["host", "enroll", "rs1002"]) == 0
        _fake_keyscan(monkeypatch, algo, blob)
        from ciu import transport_ssh

        monkeypatch.setattr(transport_ssh, "ssh_exec", lambda *a, **k: 0)
        assert _cli(monkeypatch, [
            "host", "enroll", "rs1002", "--ssh-host", "a", "--fingerprint", fingerprint,
        ]) == 0
        assert "CIU_SSH_INSECURE_TOFU" not in os.environ

        # Neither new module mutates the process environment AT ALL, which is
        # the structural reason the escape hatch cannot be set from here (the
        # name itself appears only in the docstring explaining that).
        sources = (
            (CIU_ROOT / "src" / "ciu" / "host_enroll.py").read_text(encoding="utf-8")
            + (CIU_ROOT / "src" / "ciu" / "hosts.py").read_text(encoding="utf-8")
        )
        assert "os.environ[" not in sources
        assert "environ.setdefault" not in sources
        assert "putenv" not in sources


# ---------------------------------------------------------------------------
# The end-to-end chain oracle: step 1's printed one-liner → a REAL `get.py
# enroll` run on a REAL sshd → step 2's own keyscan / fingerprint / login.
#
# This is the oracle that proves the two halves FIT, rather than that each half
# works alone. It needs a real sshd, so it runs in a container it builds and
# tears down itself, and skips where docker is unavailable (the gate's own
# tester-unified container has no docker socket — cmru KI-25 tracks exactly
# this structural invisibility for KI-24's sibling oracles).
#
# The install half of `get.py enroll` is deliberately skipped (`--no-install`,
# exactly as cmru KI-24's own TestEnrollAgainstRealSystem does) and the target's
# `ciu` is a stub: ciu publishes only a wheel today, so no `ciu-v<version>.tar.xz`
# release bundle exists for get.py to download (ciu CIU-99). What IS proven for
# real here: the printed one-liner parses and runs; the key lands in the deploy
# user's authorized_keys; the fingerprint the target prints is the one step 2
# pins; the login works over the generated key with that host key pinned; and
# both outcomes of the `ciu version` probe (missing → refuse, present → write).
# ---------------------------------------------------------------------------

FIXTURE_DOCKERFILE = """\
FROM python:3.11-slim
RUN apt-get update \\
 && apt-get install -y --no-install-recommends openssh-server \\
 && rm -rf /var/lib/apt/lists/* \\
 && mkdir -p /run/sshd \\
 && ssh-keygen -A
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""

FIXTURE_IMAGE = "ciu-enroll-fixture:test"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, text=True
    ).returncode == 0


docker_required = pytest.mark.skipif(
    not _docker_available(), reason="docker is not available in this environment"
)


@pytest.fixture(scope="session")
def enroll_fixture_image():
    build = subprocess.run(
        ["docker", "build", "-t", FIXTURE_IMAGE, "-"],
        input=FIXTURE_DOCKERFILE, capture_output=True, text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"could not build the enroll fixture image: {build.stderr[-400:]}")
    return FIXTURE_IMAGE


@pytest.fixture
def enroll_container(enroll_fixture_image):
    run = subprocess.run(
        ["docker", "run", "-d", "--rm", enroll_fixture_image],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"could not start the enroll fixture container: {run.stderr}")
    cid = run.stdout.strip()
    # Host-load rule: this container is capped the moment it exists.
    subprocess.run(["docker", "update", "--cpus=1", cid], capture_output=True)
    try:
        ip = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        yield cid, ip
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def _one_liner_args(step1_output: str) -> list[str]:
    """Extract the target-side argv from step 1's OWN printed one-liner."""
    import shlex as _shlex

    line = next(
        ln for ln in step1_output.splitlines() if ln.strip().startswith("| sudo python3 -")
    )
    return _shlex.split(line.strip())[len("| sudo python3 -".split()):]


@docker_required
class TestEnrollEndToEnd:
    def test_step1_one_liner_runs_for_real_and_step2_then_completes(
        self, repo, config, enroll_container
    ):
        cid, ip = enroll_container
        get_py = CIU_ROOT / "get.py"

        # ---- step 1, on the control host ---------------------------------
        _code, step1_out = _step1(repo, config, user="deployer")

        # ---- the printed one-liner, run for real on the target -----------
        subprocess.run(
            ["docker", "cp", str(get_py), f"{cid}:/tmp/get.py"], check=True,
            capture_output=True,
        )
        argv = _one_liner_args(step1_out)
        assert argv[0] == "enroll"
        enroll = subprocess.run(
            ["docker", "exec", cid, "python3", "/tmp/get.py", *argv, "--no-install"],
            capture_output=True, text=True,
        )
        assert enroll.returncode == 0, enroll.stdout + enroll.stderr

        # the key really landed, exactly once, for the user step 1 named
        keys = subprocess.run(
            ["docker", "exec", cid, "cat", "/home/deployer/.ssh/authorized_keys"],
            capture_output=True, text=True, check=True,
        ).stdout
        public = host_enroll.key_paths(repo, "rs1002")[1].read_text().strip()
        assert [ln for ln in keys.splitlines() if ln.strip()] == [public]

        # ---- the fingerprint is CHAINED from the target's own output -----
        fingerprint = next(
            tok for line in enroll.stdout.splitlines() if "ed25519" in line
            for tok in line.split() if tok.startswith("SHA256:")
        )

        # ---- step 2, first WITHOUT ciu on the target ---------------------
        hosts_file = repo / ".ciu.hosts.toml"
        with pytest.raises(EnrollError, match="ciu is not installed"):
            host_enroll.enroll_step2(
                repo, "rs1002", config=config, ssh_host=ip, user="deployer",
                fingerprint=fingerprint, out=lambda *_: None,
            )
        assert not hosts_file.exists()   # S14.7c: nothing written before the proof

        # ---- a wrong fingerprint is refused against the REAL host --------
        with pytest.raises(EnrollError, match="man in the middle"):
            host_enroll.enroll_step2(
                repo, "rs1002", config=config, ssh_host=ip, user="deployer",
                fingerprint="SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                out=lambda *_: None,
            )
        assert not hosts_file.exists()

        # ---- now give the target a `ciu`, and complete ------------------
        subprocess.run(
            ["docker", "exec", cid, "sh", "-c",
             "printf '#!/bin/sh\\necho ciu 7.11.0\\n' > /usr/local/bin/ciu "
             "&& chmod 0755 /usr/local/bin/ciu"],
            check=True, capture_output=True,
        )
        lines, out = _collector()
        assert host_enroll.enroll_step2(
            repo, "rs1002", config=config, ssh_host=ip, user="deployer",
            fingerprint=fingerprint, out=out,
        ) == 0

        row = load_hosts(repo)["rs1002"]
        assert row["ssh_host"] == ip
        assert row["ssh_user"] == "deployer"
        assert row["known_host"].split()[0] == "ssh-ed25519"
        assert row["ssh_key"] == str(host_enroll.key_paths(repo, "rs1002")[0])

        # ---- and `ciu ssh <name>` now works, for real --------------------
        from ciu.hosts import get_host
        from ciu.transport_ssh import ssh_exec

        assert ssh_exec(
            get_host(repo, "rs1002"), ["ciu", "version"],
            config=config, repo_root=repo,
        ) == 0
        assert "ciu ssh rs1002 -- ciu version" in _capture(lines)
