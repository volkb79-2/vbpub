"""Tests for `get.py enroll` (CIU S14.7 / KI-24), now ciu-owned.

History: these tests lived in cmru's ``tests/test_installer.py`` while the enroll
code sat in cmru's generic ``get.py.tmpl``. Decision O4 (cmru program 2026-10,
package W1-CIU-ENROLL) moved the code to ``ciu/installer/enroll.py``, inlined into
``ciu/get.py`` by cmru's ``[project.installer] extensions`` mechanism; the tests
moved with it and now run against the COMMITTED, rendered ``ciu/get.py`` (kept
equal to a fresh render by ``test_ciu_host_enroll.py::TestRenderedInstaller``).

Covers: CLI shape, key parsing, authorized_keys line handling, the fail-fast
ORDER, and, in a fixture container this file builds and tears down itself, the
real effects on a real system (user, key, modes, ownership, host-key
fingerprints).

Stdlib + tmp files only for everything except the container tests, which are
integration tests by nature; they run against a fixture image this file owns,
never a live host. Direct local runs may skip when Docker is unavailable; the
registered ``enroll`` lane (ciu/run-gate.toml) sets ``CIU_ENROLL_REQUIRED=1``,
making missing prerequisites and fixture-build failures fatal.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Iterator, Optional
from unittest import mock

import pytest

try:  # only the container preflight needs cmru; everything else is self-contained
    from cmru import tester_gate
except ImportError:  # pragma: no cover - depends on the environment
    tester_gate = None

CIU_ROOT = Path(__file__).resolve().parents[2]
GET_PY = CIU_ROOT / "get.py"

# A syntactically real ed25519 public key line (32-byte key blob, base64) — it
# is never used to authenticate anything, only appended and read back.
ENROLL_TEST_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIJ4tOoyRAvbBiHFB5zFCFtOijSMxU9BzM3sB0K9RRppQ"
    " ciu@control.example"
)
ENROLL_TEST_KEY_TYPE = "ssh-ed25519"
ENROLL_TEST_KEY_B64 = ENROLL_TEST_KEY.split()[1]

# A second, genuinely different key — used only to simulate a key rotation
# (adversarial review finding: appending a different key for an already-
# enrolled user must warn, not silently leave two valid identities).
ENROLL_TEST_KEY_2 = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIBw2P3n2sVQe6ce6t2m1u4wynAV4CmimGgAqZ0rrz+lI"
    " ciu@control-rotated.example"
)

# Exactly the flags KI-24's proposed contract names, and nothing else (O6).
ENROLL_EXPECTED_FLAGS = {
    "-h", "--help",
    "--authorized-key", "--controller", "--user", "--name", "--from",
    "--docker", "--no-install", "--scope",
}


def _render_enroll_ns() -> dict:
    """Exec the committed ``ciu/get.py`` (not as __main__) and return its namespace."""
    ns: dict = {}
    exec(compile(GET_PY.read_text(encoding="utf-8"), str(GET_PY), "exec"), ns)
    return ns


def _enroll_args(**kw):
    """A fully-populated enroll Namespace (argparse defaults spelled out)."""
    values = dict(
        command="enroll",
        authorized_key=ENROLL_TEST_KEY,
        controller="control.example.net",
        user="ciu",
        name=None,
        from_pattern=None,
        docker=False,
        no_install=False,
        scope="system",
        manifest_pubkey=None,
        version=None,
        variant=None,
        config=None,
    )
    values.update(kw)
    return argparse.Namespace(**values)


class TestEnrollCLIShape:
    """O6: the rendered script's ``enroll --help`` lists exactly KI-24's flags."""

    def _help(self, script: Path, *argv: str) -> str:
        result = subprocess.run(
            [sys.executable, str(script), *argv],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def _flags_in(self, help_text: str) -> set:
        # argparse wraps long help; a flag is only a flag at a token boundary.
        return set(re.findall(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9-]*)", help_text))

    def test_enroll_help_lists_exactly_the_contract_flags(self):
        # The usage line, which is where a missing/extra flag shows first.
        text = self._help(GET_PY, "enroll", "--help")
        usage = text.split("options:")[0]
        assert self._flags_in(usage) | {"-h", "--help"} == ENROLL_EXPECTED_FLAGS
        assert self._flags_in(text) == ENROLL_EXPECTED_FLAGS

    def test_enroll_required_flags_are_required(self):
        text = self._help(GET_PY, "enroll", "--help")
        usage = text.split("options:")[0]
        # required ⇒ unbracketed; optional ⇒ bracketed
        assert "--authorized-key KEY" in usage
        assert "[--authorized-key" not in usage
        assert "--controller FQDN" in usage
        assert "[--controller" not in usage
        for optional in ("--user USER", "--name NAME", "--from PATTERN",
                         "--docker", "--no-install"):
            assert f"[{optional}]" in usage, usage

    def test_enroll_defaults_documented(self):
        text = self._help(GET_PY, "enroll", "--help")
        assert "default: ciu" in text
        assert "default: system" in text
        assert "{system,user}" in text

    def test_top_level_help_lists_enroll_beside_the_others(self):
        text = self._help(GET_PY, "--help")
        assert "enroll" in text
        for existing in ("install", "update", "status", "rollback"):
            assert existing in text

    def test_main_dispatches_enroll_with_the_parsed_args_and_token(self, monkeypatch):
        """main() routes `enroll` to do_enroll, exactly as it routes the other four."""
        ns = _render_enroll_ns()
        seen: dict = {}
        ns["do_enroll"] = lambda args, token: seen.update(args=args, token=token)
        for other in ("do_install", "do_update", "do_rollback", "do_status"):
            ns[other] = lambda *a, **k: pytest.fail("wrong subcommand dispatched")
        monkeypatch.setenv("CMRU_GITHUB_TOKEN", "tok")
        argv = ["get.py", "enroll",
                "--authorized-key", ENROLL_TEST_KEY,
                "--controller", "control.example.net",
                "--user", "deploy", "--name", "web-01",
                "--from", "10.0.0.0/8", "--docker", "--no-install",
                "--scope", "user"]
        with mock.patch.object(sys, "argv", argv):
            ns["main"]()
        args = seen["args"]
        assert seen["token"] == "tok"
        assert (args.command, args.user, args.name, args.scope) == (
            "enroll", "deploy", "web-01", "user")
        assert args.from_pattern == "10.0.0.0/8"
        assert args.docker is True and args.no_install is True
        assert args.authorized_key == ENROLL_TEST_KEY
        assert args.controller == "control.example.net"

    def test_enroll_defaults_when_only_required_flags_given(self, monkeypatch):
        ns = _render_enroll_ns()
        seen: dict = {}
        ns["do_enroll"] = lambda args, token: seen.update(args=args)
        monkeypatch.delenv("CMRU_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        argv = ["get.py", "enroll",
                "--authorized-key", ENROLL_TEST_KEY,
                "--controller", "control.example.net"]
        with mock.patch.object(sys, "argv", argv):
            ns["main"]()
        args = seen["args"]
        assert args.user == "ciu"
        assert args.scope == "system"
        assert args.name is None and args.from_pattern is None
        assert args.docker is False and args.no_install is False

    def test_get_py_cli_render_carries_enroll(self, tmp_path):
        """The `cmru get-py ciu` path renders a script whose enroll flags match.

        Needs a cmru that knows ``extensions``; skipped against an older one.
        """
        try:
            from cmru.config import InstallerConfig
            from cmru.getpy import getpy_main
        except ImportError as exc:  # a missing cmru must be RED, never a silent skip
            pytest.fail(
                "cmru is not importable; put ../cmru/src (and ../libraries/cli-extended/src, "
                f"../libraries/worktree/src) on PYTHONPATH: {exc}"
            )
        assert "extensions" in InstallerConfig.__dataclass_fields__, (
            "the importable cmru predates `[project.installer] extensions`"
        )
        out_file = tmp_path / "rendered-get.py"
        rc = getpy_main([
            "ciu", "--config", str(CIU_ROOT.parent / "cmru.orchestration.toml"),
            "--output", str(out_file),
        ])
        assert rc == 0
        text = self._help(out_file, "enroll", "--help")
        assert self._flags_in(text) == ENROLL_EXPECTED_FLAGS
        assert out_file.read_text(encoding="utf-8") == GET_PY.read_text(encoding="utf-8")

    def test_committed_installer_exposes_enroll_through_main(self, capsys, monkeypatch):
        """``main()`` runs check_prerequisites BEFORE parse_args, so the parser is
        exercised in process with that (unrelated) gate stubbed out."""
        ns = _render_enroll_ns()
        ns["check_prerequisites"] = lambda: None
        with mock.patch.object(sys, "argv", ["get.py", "enroll", "--help"]):
            with pytest.raises(SystemExit) as exc:
                ns["main"]()
        assert exc.value.code == 0
        assert self._flags_in(capsys.readouterr().out) == ENROLL_EXPECTED_FLAGS


class TestEnrollKeyParsing:
    """KI-24 step 1: the --authorized-key value must parse, or EXIT_CONFIG."""

    def _parse(self, ns, raw):
        return ns["_parse_authorized_key"](raw)

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    @pytest.mark.parametrize("ktype", [
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    ])
    def test_accepted_key_types(self, ns, ktype):
        parsed = self._parse(ns, f"{ktype} AAAAB3NzaC1kZXN0 someone@somewhere")
        assert parsed == (ktype, "AAAAB3NzaC1kZXN0", "someone@somewhere")

    def test_comment_is_optional(self, ns):
        assert self._parse(ns, "ssh-ed25519 AAAAB3NzaC1kZXN0") == (
            "ssh-ed25519", "AAAAB3NzaC1kZXN0", "")

    def test_multiword_comment_preserved(self, ns):
        assert self._parse(ns, "ssh-ed25519 AAAAB3NzaC1kZXN0 my laptop key")[2] == (
            "my laptop key")

    def test_surrounding_whitespace_tolerated(self, ns):
        assert self._parse(ns, "  ssh-ed25519 AAAAB3NzaC1kZXN0  \n")[0] == "ssh-ed25519"

    @pytest.mark.parametrize("bad", [
        "",
        "   ",
        "ssh-ed25519",
        "ssh-dss AAAAB3NzaC1kZXN0",
        "not-a-key-type AAAAB3NzaC1kZXN0",
        "ssh-ed25519 not+valid+base64!!",
        'from="10.0.0.1",ssh-ed25519 AAAAB3NzaC1kZXN0',
        "ssh-ed25519 AAAAB3NzaC1kZXN0\nssh-ed25519 AAAAB3NzaC1kZXN1",
    ])
    def test_rejected_with_exit_config(self, ns, bad):
        with pytest.raises(SystemExit) as exc:
            self._parse(ns, bad)
        assert exc.value.code == 2, bad

    def test_options_rejection_names_the_from_flag(self, ns, capsys):
        with pytest.raises(SystemExit):
            self._parse(ns, 'from="10.0.0.1",ssh-ed25519 AAAAB3NzaC1kZXN0')
        assert "--from" in capsys.readouterr().err


class TestEnrollAuthorizedKeysLine:
    """The line this subcommand writes, and the reader that decides idempotency."""

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    def test_line_without_from(self, ns):
        assert ns["_build_key_line"]("ssh-ed25519", "AAAA", "c@h", None) == (
            "ssh-ed25519 AAAA c@h")

    def test_line_with_from_separates_options_by_whitespace_not_comma(self, ns):
        """A comma-joined options field makes the key unreadable to sshd.

        KI-24's text spells this ``from="P",<type> …``; OpenSSH advances past the
        options to the first unquoted whitespace, so that form hides the key type
        inside the options and public-key auth fails. See _build_key_line's own
        docstring for the measured sshd result behind this assertion.
        """
        assert ns["_build_key_line"]("ssh-ed25519", "AAAA", "c@h", "10.0.0.0/8") == (
            'from="10.0.0.0/8" ssh-ed25519 AAAA c@h')

    def test_line_without_comment(self, ns):
        assert ns["_build_key_line"]("ssh-rsa", "AAAA", "", None) == "ssh-rsa AAAA"

    def test_split_plain_line(self, ns):
        assert ns["_ak_split_line"]("ssh-ed25519 AAAA c@h") == (
            "", "ssh-ed25519", "AAAA", "c@h")

    def test_split_line_with_options(self, ns):
        assert ns["_ak_split_line"]('from="10.0.0.1" ssh-ed25519 AAAA c@h') == (
            'from="10.0.0.1"', "ssh-ed25519", "AAAA", "c@h")

    def test_split_tolerates_the_malformed_comma_joined_form_on_read(self, ns):
        """We never WRITE this shape, but a pre-existing one must still be seen."""
        assert ns["_ak_split_line"]('from="10.0.0.1",ssh-ed25519 AAAA c@h') == (
            'from="10.0.0.1"', "ssh-ed25519", "AAAA", "c@h")

    def test_split_comma_form_with_several_options(self, ns):
        assert ns["_ak_split_line"](
            'no-pty,from="10.0.0.1",ssh-ed25519 AAAA') == (
            'no-pty,from="10.0.0.1"', "ssh-ed25519", "AAAA", "")

    def test_split_comma_form_needs_key_material_after_it(self, ns):
        assert ns["_ak_split_line"]('from="10.0.0.1",ssh-ed25519') is None

    def test_split_is_quote_aware(self, ns):
        """An options field may hold whitespace inside quotes; str.split() tears it."""
        line = 'command="/bin/echo hi there",from="10.0.0.1" ssh-ed25519 AAAA c@h'
        assert ns["_ak_split_line"](line) == (
            'command="/bin/echo hi there",from="10.0.0.1"',
            "ssh-ed25519", "AAAA", "c@h")

    def test_split_honours_backslash_escapes_inside_quotes(self, ns):
        line = 'command="echo \\" x" ssh-ed25519 AAAA'
        assert ns["_ak_split_line"](line) == (
            'command="echo \\" x"', "ssh-ed25519", "AAAA", "")

    @pytest.mark.parametrize("ignored", [
        "", "   ", "# a comment", "\t# indented comment",
        "garbage-with-no-key", 'from="x" also-garbage',
    ])
    def test_uninteresting_lines_are_skipped(self, ns, ignored):
        assert ns["_ak_split_line"](ignored) is None


class TestEnrollOrdering:
    """O3 and the fail-fast order: nothing happens before the prerequisites pass."""

    def _poison(self, ns, calls):
        """Replace every step after the prerequisites with a tripwire."""
        def tripwire(label):
            def _fail(*_a, **_kw):
                calls.append(label)
                raise AssertionError(
                    f"{label} ran before/despite a failing prerequisite check")
            return _fail
        ns["do_install"] = tripwire("do_install")
        ns["_enroll_ensure_user"] = tripwire("_enroll_ensure_user")
        ns["_enroll_install_key"] = tripwire("_enroll_install_key")
        ns["_gh_request"] = tripwire("_gh_request")
        ns["resolve_latest_tag"] = tripwire("resolve_latest_tag")
        ns["_host_key_fingerprints"] = tripwire("_host_key_fingerprints")

    def test_missing_ssh_server_exits_prereq_names_openssh_server(self, capsys):
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: None
        with mock.patch.object(os, "geteuid", return_value=0):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(), token=None)
        assert exc.value.code == 3
        assert "openssh-server" in capsys.readouterr().err
        assert calls == [], f"steps reached before the prerequisite gate: {calls}"

    def test_missing_ssh_server_reaches_no_network_call(self):
        """Zero network I/O: urllib itself is poisoned, not merely unasserted."""
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: None

        def no_network(*_a, **_kw):
            raise AssertionError("network I/O attempted before prerequisites passed")

        with mock.patch.object(ns["urllib"].request, "build_opener", new=no_network), \
                mock.patch.object(ns["urllib"].request, "urlopen", new=no_network), \
                mock.patch.object(os, "geteuid", return_value=0):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(), token=None)
        assert exc.value.code == 3

    def test_non_root_exits_prereq_before_anything_else(self, capsys):
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        with mock.patch.object(os, "geteuid", return_value=1000):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(), token=None)
        assert exc.value.code == 3
        assert "root" in capsys.readouterr().err
        assert calls == []

    def test_unparsable_key_exits_config_before_install(self, capsys):
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        with mock.patch.object(os, "geteuid", return_value=0):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(authorized_key="ssh-dss AAAA"), token=None)
        assert exc.value.code == 2
        assert calls == []


class TestEnrollInstallStep:
    """KI-24 step 2: install runs verbatim, before the user, unless --no-install."""

    def _instrument(self, ns):
        order: list = []
        seen: dict = {}

        def fake_install(args, token):
            order.append("install")
            seen["scope"] = args.scope
            seen["pubkey"] = args.manifest_pubkey
            seen["token"] = token

        class _Entry:
            pw_dir = "/home/ciu"
            pw_uid = 1001
            pw_gid = 1001
            pw_shell = "/bin/bash"

        def fake_user(user, want_docker):
            order.append("user")
            seen["user"] = user
            seen["docker"] = want_docker
            return _Entry()

        def fake_key(entry, parsed, from_pattern):
            order.append("key")
            seen["parsed"] = parsed
            seen["from"] = from_pattern
            return Path("/home/ciu/.ssh/authorized_keys")

        ns["do_install"] = fake_install
        ns["_enroll_ensure_user"] = fake_user
        ns["_enroll_install_key"] = fake_key
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        ns["_host_key_fingerprints"] = lambda: [
            ("/etc/ssh/ssh_host_ed25519_key.pub",
             "256 SHA256:abc root@h (ED25519)", "SHA256:abc"),
        ]
        ns["_host_addresses"] = lambda: ["10.1.2.3"]
        ns["_current_version"] = lambda root: "ciu-v1.2.3"
        return order, seen

    def test_install_runs_before_user_and_key(self, capsys):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(scope="system", manifest_pubkey="RWS1"),
                            token="tok")
        assert order == ["install", "user", "key"]
        assert seen["scope"] == "system"
        assert seen["pubkey"] == "RWS1"
        assert seen["token"] == "tok"

    def test_scope_is_propagated_to_install(self):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(scope="user"), token=None)
        assert seen["scope"] == "user"

    def test_no_install_skips_install_but_still_enrolls(self, capsys):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(no_install=True), token=None)
        assert order == ["user", "key"]
        assert "skipping the install step" in capsys.readouterr().out

    def test_defaults_user_ciu_and_passes_from_pattern(self):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(no_install=True, from_pattern="10.0.0.0/8"),
                            token=None)
        assert seen["user"] == "ciu"
        assert seen["from"] == "10.0.0.0/8"
        assert seen["parsed"] == (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64,
                                  "ciu@control.example")


class TestEnrollReport:
    """KI-24 step 5: what enroll prints is what the operator confirms."""

    def _instrument(self, ns, *, addresses, fingerprints):
        class _Entry:
            pw_dir = "/home/ciu"
            pw_uid = 1001
            pw_gid = 1001
            pw_shell = "/bin/bash"

        ns["do_install"] = lambda args, token: None
        ns["_enroll_ensure_user"] = lambda user, want_docker: _Entry()
        ns["_enroll_install_key"] = (
            lambda entry, parsed, from_pattern: Path("/home/ciu/.ssh/authorized_keys"))
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        ns["_host_key_fingerprints"] = lambda: fingerprints
        ns["_host_addresses"] = lambda: addresses
        ns["_current_version"] = lambda root: "ciu-v1.2.3"

    FPS = [
        ("/etc/ssh/ssh_host_ecdsa_key.pub", "256 SHA256:ecdsafp root@h (ECDSA)",
         "SHA256:ecdsafp"),
        ("/etc/ssh/ssh_host_ed25519_key.pub", "256 SHA256:edfp root@h (ED25519)",
         "SHA256:edfp"),
    ]

    def test_prints_every_host_key_addresses_user_version(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3", "192.168.0.9"], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        out = capsys.readouterr().out
        assert "SHA256:ecdsafp" in out and "SHA256:edfp" in out
        assert "10.1.2.3" in out and "192.168.0.9" in out
        assert "UNCONFIRMED" in out
        assert "ciu-v1.2.3" in out
        assert "control.example.net" in out

    def test_completion_command_uses_ed25519_fingerprint_and_name(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3"], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        out = capsys.readouterr().out
        assert ("ciu host enroll web-01 --ssh-host 10.1.2.3 "
                "--fingerprint SHA256:edfp") in out

    def test_no_name_prints_a_placeholder_never_a_guess(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3"], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name=None), token=None)
        out = capsys.readouterr().out
        assert "ciu host enroll <NAME> --ssh-host 10.1.2.3" in out
        assert "replace <NAME>" in out

    def test_no_addresses_prints_a_placeholder(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=[], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        out = capsys.readouterr().out
        assert "--ssh-host <ADDRESS>" in out
        assert "replace <ADDRESS>" in out

    def test_no_host_keys_warns_and_keeps_the_command_substitutable(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3"], fingerprints=[])
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        captured = capsys.readouterr()
        assert "ssh_host_*_key.pub" in captured.err
        assert "--fingerprint SHA256:<ed25519 fingerprint>" in captured.out


class TestEnrollHostProbes:
    """The two shell-outs enroll makes: ssh-keygen -lf and hostname -I."""

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    def test_fingerprints_shell_out_to_ssh_keygen(self, ns, tmp_path, monkeypatch):
        pub = tmp_path / "ssh_host_ed25519_key.pub"
        pub.write_text("ssh-ed25519 AAAA root@h\n")
        monkeypatch.setattr(ns["_glob"], "glob", lambda pat: [str(pub)])

        def fake_run(cmd, **kw):
            assert cmd == ["ssh-keygen", "-lf", str(pub)]
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = "256 SHA256:deadbeef root@h (ED25519)\n"
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_key_fingerprints"]() == [
                (str(pub), "256 SHA256:deadbeef root@h (ED25519)", "SHA256:deadbeef"),
            ]

    def test_fingerprint_failure_warns_and_skips_that_key(self, ns, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ns["_glob"], "glob", lambda pat: ["/etc/ssh/broken.pub"])

        def fake_run(cmd, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "is not a public key file"
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_key_fingerprints"]() == []
        assert "ssh-keygen" in capsys.readouterr().err

    def test_addresses_from_hostname_dash_i(self, ns):
        def fake_run(cmd, **kw):
            assert cmd == ["hostname", "-I"]
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = "10.1.2.3 192.168.0.9 \n"
            return r

        with mock.patch.object(shutil, "which", return_value="/bin/hostname"), \
                mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_addresses"]() == ["10.1.2.3", "192.168.0.9"]

    def test_addresses_absent_hostname_warns_and_returns_empty(self, ns, capsys):
        with mock.patch.object(shutil, "which", return_value=None):
            assert ns["_host_addresses"]() == []
        assert "hostname" in capsys.readouterr().err

    def test_addresses_failed_hostname_warns_and_returns_empty(self, ns, capsys):
        def fake_run(cmd, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "boom"
            return r

        with mock.patch.object(shutil, "which", return_value="/bin/hostname"), \
                mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_addresses"]() == []
        assert "hostname -I failed" in capsys.readouterr().err

    def test_find_sshd_prefers_path(self, ns):
        with mock.patch.object(shutil, "which", return_value="/usr/local/sbin/sshd"):
            assert ns["_find_sshd"]() == "/usr/local/sbin/sshd"

    def test_find_sshd_falls_back_to_usr_sbin(self, ns, monkeypatch):
        real_exists = Path.exists

        def fake_exists(self):
            if str(self) == "/usr/sbin/sshd":
                return True
            return real_exists(self)

        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(Path, "exists", fake_exists):
            assert ns["_find_sshd"]() == "/usr/sbin/sshd"

    def test_find_sshd_absent_is_none(self, ns):
        real_exists = Path.exists

        def fake_exists(self):
            if str(self) == "/usr/sbin/sshd":
                return False
            return real_exists(self)

        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(Path, "exists", fake_exists):
            assert ns["_find_sshd"]() is None


# ─── enroll: real execution against real system state (O2 / O3) ───────────────
#
# There is no prior "spin up a container, run the installer, assert on real
# system state" pattern in this repo; this is it. The fixture image is built and
# owned here (never a live host), every container is torn down in a finally, and
# a direct local run skips if Docker or the estate's cgroup tier is unavailable.
# The registered lane sets CIU_ENROLL_REQUIRED=1 to turn those conditions into
# failures so the O2/O3 oracle cannot disappear from release evidence.

ENROLL_FIXTURE_IMAGE = "ciu-enroll-fixture:local"
ENROLL_FIXTURE_DOCKERFILE = """\
FROM debian:bookworm-slim
RUN apt-get update \\
 && apt-get install -y --no-install-recommends \\
        openssh-server python3 iproute2 hostname passwd \\
 && rm -rf /var/lib/apt/lists/* \\
 && ssh-keygen -A
"""


def _docker_unavailable_reason() -> Optional[str]:
    if shutil.which("docker") is None:
        return "docker CLI not present"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return f"docker daemon unreachable: {probe.stderr.strip()[:200]}"
    # AGENTS.md "Host cgroup placement": no hardcoded fallback slice — a
    # gate fixture we cannot place on the host's dedicated gates tier is one
    # we do not start next to production.
    slice_name = os.environ.get("CGROUP_PARENT_DEV_GATES", "").strip()
    if not slice_name:
        return "CGROUP_PARENT_DEV_GATES unset — refusing an unplaced container"
    if tester_gate is None:
        return "cmru.tester_gate not importable — cannot verify CGROUP_PARENT_DEV_GATES"
    try:
        probe_image = tester_gate.resolve_cgroup_probe_image(None)
    except SystemExit as exc:
        return f"could not verify CGROUP_PARENT_DEV_GATES unit: {exc}"
    exists, note = tester_gate.check_slice_unit(slice_name, probe_image, slice_name)
    if exists is not True:
        return f"could not verify CGROUP_PARENT_DEV_GATES unit on the Docker host: {note}"
    return None


@pytest.mark.skipif(tester_gate is None, reason="cmru.tester_gate not importable")
@pytest.mark.parametrize(
    ("probe_result", "note"),
    [
        (False, "LoadState=not-found"),
        (False, "FragmentPath is empty"),
        (None, "could not determine"),
    ],
)
def test_enroll_preflight_rejects_gate_slice_not_verified_on_docker_host(
    monkeypatch, probe_result, note,
):
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "typo-or-transient.slice")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs:
        subprocess.CompletedProcess(["docker", "info"], 0, "", ""),
    )
    monkeypatch.setattr(tester_gate, "resolve_cgroup_probe_image", lambda _image: "probe:image")
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda *_args: (probe_result, note))

    assert note in _docker_unavailable_reason()


@pytest.mark.skipif(tester_gate is None, reason="cmru.tester_gate not importable")
def test_enroll_preflight_accepts_loaded_gate_slice_with_fragment(monkeypatch):
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs:
        subprocess.CompletedProcess(["docker", "info"], 0, "", ""),
    )
    monkeypatch.setattr(tester_gate, "resolve_cgroup_probe_image", lambda _image: "probe:image")
    observed = []
    monkeypatch.setattr(
        tester_gate, "check_slice_unit",
        lambda *args: (observed.append(args) or (True, "loaded with fragment")),
    )

    assert _docker_unavailable_reason() is None
    assert observed == [("dev-gates.slice", "probe:image", "dev-gates.slice")]


def _skip_or_fail_enroll_preflight(reason: str) -> None:
    if os.environ.get("CIU_ENROLL_REQUIRED") == "1":
        pytest.fail(f"registered enrollment gate preflight failed: {reason}", pytrace=False)
    pytest.skip(f"enroll container oracle needs docker ({reason})")


@pytest.fixture(scope="session")
def enroll_fixture_image() -> str:
    reason = _docker_unavailable_reason()
    if reason:
        _skip_or_fail_enroll_preflight(reason)
    present = subprocess.run(
        ["docker", "image", "inspect", ENROLL_FIXTURE_IMAGE],
        capture_output=True, text=True,
    )
    if present.returncode != 0:
        with tempfile.TemporaryDirectory() as ctx:
            (Path(ctx) / "Dockerfile").write_text(ENROLL_FIXTURE_DOCKERFILE)
            built = subprocess.run(
                ["docker", "build", "-t", ENROLL_FIXTURE_IMAGE, ctx],
                capture_output=True, text=True, timeout=900,
            )
            if built.returncode != 0:
                _skip_or_fail_enroll_preflight(
                    "could not build the enroll fixture image: "
                    f"{built.stderr.strip()[-400:]}"
                )
    return ENROLL_FIXTURE_IMAGE


def test_registered_enroll_lane_fails_when_container_prerequisites_are_missing(monkeypatch):
    monkeypatch.setenv("CIU_ENROLL_REQUIRED", "1")
    monkeypatch.setattr(
        sys.modules[__name__], "_docker_unavailable_reason",
        lambda: "docker CLI not present",
    )

    with pytest.raises(pytest.fail.Exception, match="registered enrollment gate preflight failed"):
        enroll_fixture_image.__wrapped__()


def test_registered_enroll_lane_fails_when_fixture_image_build_fails(monkeypatch):
    monkeypatch.setenv("CIU_ENROLL_REQUIRED", "1")
    monkeypatch.setattr(
        sys.modules[__name__], "_docker_unavailable_reason", lambda: None,
    )
    results = iter([
        mock.Mock(returncode=1, stderr="missing fixture image"),
        mock.Mock(returncode=1, stderr="image build failed"),
    ])
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))

    with pytest.raises(pytest.fail.Exception, match="could not build the enroll fixture image"):
        enroll_fixture_image.__wrapped__()


@pytest.fixture()
def rendered_get_py() -> Path:
    """The committed, rendered installer (the artifact operators actually run)."""
    return GET_PY


@contextlib.contextmanager
def _enroll_container(image: str, script: Path, *, network: str = "bridge") -> Iterator[str]:
    """A short-lived fixture container carrying the rendered installer.

    Placed on the estate's gate cgroup tier with a 2 GiB hard RAM cap and ample
    swap; named for operator visibility
    and removed in a finally so a failing assertion never leaves one running.
    """
    slice_name = os.environ["CGROUP_PARENT_DEV_GATES"]
    container = f"ciu-enroll-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    started = subprocess.run(
        ["docker", "run", "-d", "--init", f"--name={container}",
         f"--cgroup-parent={slice_name}",
         "--cpus=3", "--memory=2g", "--memory-swap=16g",
         f"--network={network}",
         image, "sleep", "600"],
        capture_output=True, text=True,
    )
    assert started.returncode == 0, f"{container}: {started.stderr}"
    try:
        copied = subprocess.run(
            ["docker", "cp", str(script), f"{container}:/tmp/get.py"],
            capture_output=True, text=True,
        )
        assert copied.returncode == 0, copied.stderr
        yield container
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def _cexec(container: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, *argv],
        capture_output=True, text=True, timeout=300,
    )


def _enroll_in(container: str, *extra: str) -> subprocess.CompletedProcess:
    return _cexec(
        container, "python3", "/tmp/get.py", "enroll",
        "--no-install",                       # the install step needs GitHub; kept hermetic
        "--controller", "test.example",
        "--user", "deployer",
        "--authorized-key", ENROLL_TEST_KEY,
        *extra,
    )


class TestEnrollAgainstRealSystem:
    """O2/O3 — the rendered enroll run for real, asserted on real system state."""

    def test_o2_user_key_modes_ownership_and_fingerprint(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            first = _enroll_in(c)
            assert first.returncode == 0, first.stdout + first.stderr

            # the user exists for real
            ident = _cexec(c, "id", "-u", "deployer")
            assert ident.returncode == 0, ident.stderr

            # exactly one matching line
            keys = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert keys.returncode == 0, keys.stderr
            lines = [ln for ln in keys.stdout.splitlines() if ln.strip()]
            assert lines == [ENROLL_TEST_KEY]

            # modes + ownership, read off the real filesystem
            stat_out = _cexec(
                c, "stat", "-c", "%a %U %G %n",
                "/home/deployer/.ssh", "/home/deployer/.ssh/authorized_keys",
            )
            assert stat_out.returncode == 0, stat_out.stderr
            assert stat_out.stdout.splitlines() == [
                "700 deployer deployer /home/deployer/.ssh",
                "600 deployer deployer /home/deployer/.ssh/authorized_keys",
            ]

            # the printed fingerprint equals ssh-keygen run independently HERE
            independent = _cexec(
                c, "ssh-keygen", "-lf", "/etc/ssh/ssh_host_ed25519_key.pub")
            assert independent.returncode == 0, independent.stderr
            expected_fp = next(
                tok for tok in independent.stdout.split() if tok.startswith("SHA256:"))
            assert expected_fp in first.stdout
            assert f"--fingerprint {expected_fp}" in first.stdout
            assert "/etc/ssh/ssh_host_ed25519_key.pub" in first.stdout

            # the addresses are printed and explicitly UNCONFIRMED
            addrs = _cexec(c, "hostname", "-I")
            assert addrs.returncode == 0, addrs.stderr
            for addr in addrs.stdout.split():
                assert addr in first.stdout
            assert "UNCONFIRMED" in first.stdout

            # re-run: still exactly one line, reported, not an error
            second = _enroll_in(c)
            assert second.returncode == 0, second.stdout + second.stderr
            keys_again = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in keys_again.stdout.splitlines() if ln.strip()] == [
                ENROLL_TEST_KEY]
            assert "not duplicated" in second.stdout
            assert "already exists" in second.stdout

    def test_o2_rotating_to_a_different_key_warns_and_appends_alongside(
        self, enroll_fixture_image, rendered_get_py
    ):
        """A real rotation (different --authorized-key, same user) must not
        silently leave two valid keys with no visibility — it may append
        (KI-24 never restricts a user to one key), but it must warn."""
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            first = _enroll_in(c)
            assert first.returncode == 0, first.stdout + first.stderr

            rotated = _enroll_in(c, "--authorized-key", ENROLL_TEST_KEY_2)
            assert rotated.returncode == 0, rotated.stdout + rotated.stderr

            # both keys are now present — this is the coexistence the warning
            # exists to surface, not silently hide
            keys = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert keys.returncode == 0, keys.stderr
            lines = [ln for ln in keys.stdout.splitlines() if ln.strip()]
            assert lines == [ENROLL_TEST_KEY, ENROLL_TEST_KEY_2]

            # and the operator was actually told about it
            combined = rotated.stdout + rotated.stderr
            assert "other" in combined.lower() and "key" in combined.lower()
            assert "ciu@control.example" in combined  # names the pre-existing one

    def test_o2_from_pattern_written_and_conflicting_options_refused(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            first = _enroll_in(c, "--from", "10.0.0.0/8")
            assert first.returncode == 0, first.stdout + first.stderr
            keys = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in keys.stdout.splitlines() if ln.strip()] == [
                f'from="10.0.0.0/8" {ENROLL_TEST_KEY}']

            # identical re-run is a no-op
            same = _enroll_in(c, "--from", "10.0.0.0/8")
            assert same.returncode == 0, same.stdout + same.stderr
            still = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in still.stdout.splitlines() if ln.strip()] == [
                f'from="10.0.0.0/8" {ENROLL_TEST_KEY}']
            assert "not duplicated" in same.stdout

            # same key material, DIFFERENT options → a refusal, not a second line
            conflict = _enroll_in(c, "--from", "192.168.0.0/16")
            assert conflict.returncode == 2, conflict.stdout + conflict.stderr
            assert "DIFFERENT options" in conflict.stderr
            after = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in after.stdout.splitlines() if ln.strip()] == [
                f'from="10.0.0.0/8" {ENROLL_TEST_KEY}']

            # dropping --from entirely is the same conflict, not a silent append
            dropped = _enroll_in(c)
            assert dropped.returncode == 2, dropped.stdout + dropped.stderr
            after2 = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert len([ln for ln in after2.stdout.splitlines() if ln.strip()]) == 1

    def test_written_key_actually_authenticates_against_a_real_sshd(
        self, enroll_fixture_image, rendered_get_py
    ):
        """The point of the whole subcommand: the controller can now log in.

        This is also the oracle behind the one deviation from KI-24's literal
        text — the restricted line is written `from="P" <type> …`, not
        `from="P",<type> …`. The comma-joined form fails here (measured:
        "Permission denied (publickey)"), because sshd reads everything up to
        the first unquoted whitespace as options.
        """
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            keygen = _cexec(c, "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", "/root/id_probe")
            assert keygen.returncode == 0, keygen.stderr
            pub = _cexec(c, "cat", "/root/id_probe.pub")
            assert pub.returncode == 0, pub.stderr
            public_key = pub.stdout.strip()

            enrolled = _cexec(
                c, "python3", "/tmp/get.py", "enroll", "--no-install",
                "--controller", "test.example", "--user", "deployer",
                "--from", "127.0.0.1", "--authorized-key", public_key,
            )
            assert enrolled.returncode == 0, enrolled.stdout + enrolled.stderr

            started = _cexec(c, "sh", "-c",
                             "mkdir -p /run/sshd && /usr/sbin/sshd -p 2222")
            assert started.returncode == 0, started.stderr
            login = _cexec(
                c, "ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                "-o", "ConnectionAttempts=10",
                "-i", "/root/id_probe", "-p", "2222", "deployer@127.0.0.1",
                "echo", "ENROLLED_LOGIN_OK",
            )
            assert login.returncode == 0, login.stdout + login.stderr
            assert "ENROLLED_LOGIN_OK" in login.stdout

    def test_o3_no_ssh_server_exits_prereq_and_changes_nothing(
        self, enroll_fixture_image, rendered_get_py
    ):
        # --network none: this container cannot reach anything, so a run that
        # exits EXIT_PREREQ here also proves nothing was fetched on the way.
        with _enroll_container(enroll_fixture_image, rendered_get_py,
                               network="none") as c:
            removed = _cexec(c, "rm", "-f", "/usr/sbin/sshd")
            assert removed.returncode == 0, removed.stderr
            assert _cexec(c, "test", "-e", "/usr/sbin/sshd").returncode != 0

            result = _enroll_in(c)
            assert result.returncode == 3, result.stdout + result.stderr
            assert "openssh-server" in result.stderr

            # and it did NOT do any of the later steps
            assert _cexec(c, "id", "-u", "deployer").returncode != 0
            assert _cexec(c, "test", "-e", "/home/deployer").returncode != 0

    def test_docker_flag_refused_when_the_group_is_absent(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            assert _cexec(c, "getent", "group", "docker").returncode != 0

            result = _enroll_in(c, "--docker")
            assert result.returncode == 3, result.stdout + result.stderr
            assert "docker" in result.stderr
            # refused BEFORE creating the user — no half-enrolled host
            assert _cexec(c, "id", "-u", "deployer").returncode != 0

    def test_docker_flag_adds_the_user_when_the_group_exists(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            created = _cexec(c, "groupadd", "docker")
            assert created.returncode == 0, created.stderr

            result = _enroll_in(c, "--docker")
            assert result.returncode == 0, result.stdout + result.stderr
            groups = _cexec(c, "id", "-nG", "deployer")
            assert "docker" in groups.stdout.split(), groups.stdout

    def test_existing_user_is_left_untouched(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            made = _cexec(c, "useradd", "--create-home", "--shell", "/bin/sh",
                          "deployer")
            assert made.returncode == 0, made.stderr

            result = _enroll_in(c)
            assert result.returncode == 0, result.stdout + result.stderr
            shell = _cexec(c, "getent", "passwd", "deployer")
            assert shell.stdout.strip().endswith("/bin/sh"), shell.stdout
            assert "already exists" in result.stdout
