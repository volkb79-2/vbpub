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
never a live host. The real-system tests run only through CIU's registered
``enroll`` lane inside ``cmru tester-gate --enable-docker``. They skip from the
ordinary suite and refuse if the required isolated-Docker endpoint is absent.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Iterator, Optional
from unittest import mock

import pytest

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

# Enrollment flags exposed by the rendered CIU installer (CIU-93/O6 and CIU-218).
ENROLL_EXPECTED_FLAGS = {
    "-h", "--help",
    "--authorized-key", "--controller", "--user", "--name", "--from",
    "--forced-command", "--agent-path", "--fleetd-group", "--agent-policy",
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
        forced_command=False,
        agent_path=None,
        fleetd_group=None,
        agent_policy=None,
        remove_restricted=False,
        no_install=False,
        scope="system",
        manifest_pubkey=None,
        version=None,
        variant=None,
        config=None,
    )
    values.update(kw)
    return argparse.Namespace(**values)


def _fake_ecdsa_key_encodings():
    """Return canonical and noncanonical aliases for an obviously fake blob."""
    key_type = b"ecdsa-sha2-nistp256"
    curve = b"nistp256"
    point = b"\x04" + b"\x01" * 64
    blob = (
        struct.pack(">I", len(key_type)) + key_type
        + struct.pack(">I", len(curve)) + curve
        + struct.pack(">I", len(point)) + point
    )
    canonical = base64.b64encode(blob).decode("ascii")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    padding = len(canonical) - len(canonical.rstrip("="))
    assert padding in (1, 2)
    final_position = len(canonical) - padding - 1
    final_index = alphabet.index(canonical[final_position])
    alternate = (
        canonical[:final_position] + alphabet[final_index | 1]
        + canonical[final_position + 1:]
    )
    assert alternate != canonical
    assert base64.b64decode(alternate, validate=True) == blob
    return key_type.decode("ascii"), blob, canonical, alternate


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
                         "--forced-command", "--agent-path PATH", "--fleetd-group GROUP",
                         "--agent-policy PATH", "--docker", "--no-install"):
            assert f"[{optional}]" in usage, usage

    def test_remove_restricted_has_only_name_and_user(self):
        text = self._help(GET_PY, "remove-restricted", "--help")
        usage = text.split("options:")[0]
        assert "--name NAME" in usage
        assert "[--user USER]" in usage
        assert "--authorized-key" not in text
        assert "--controller" not in text

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
        assert args.forced_command is False
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
        assert args.forced_command is False
        assert args.agent_path is None and args.fleetd_group is None
        assert args.agent_policy is None

    def test_main_dispatches_remove_restricted_subcommand(self, monkeypatch):
        ns = _render_enroll_ns()
        seen: dict = {}
        ns["do_enroll"] = lambda args, token: seen.update(args=args)
        with mock.patch.object(sys, "argv", [
            "get.py", "remove-restricted", "--name", "web-01", "--user", "deploy",
        ]):
            ns["main"]()
        args = seen["args"]
        assert args.command == "remove-restricted"
        assert args.remove_restricted is True
        assert args.name == "web-01" and args.user == "deploy"

    def test_main_dispatches_forced_command_inputs_without_a_command_string(self, monkeypatch):
        ns = _render_enroll_ns()
        seen: dict = {}
        ns["do_enroll"] = lambda args, token: seen.update(args=args, token=token)
        ns["check_prerequisites"] = lambda: None
        argv = [
            "get.py", "enroll", "--authorized-key", ENROLL_TEST_KEY,
            "--controller", "control.example.net", "--name", "web-01",
            "--no-install", "--forced-command", "--agent-path",
            "/usr/bin/fleetd-agent", "--fleetd-group", "campaigns",
            "--agent-policy", "/etc/fleetd/agent.toml",
        ]
        with mock.patch.object(sys, "argv", argv):
            ns["main"]()
        args = seen["args"]
        assert args.forced_command is True
        assert args.agent_path == "/usr/bin/fleetd-agent"
        assert args.fleetd_group == "campaigns"
        assert args.agent_policy == "/etc/fleetd/agent.toml"

    def test_regular_enrollment_parser_requires_controller_with_valid_key(self, capsys):
        ns = _render_enroll_ns()
        argv = ["get.py", "enroll", "--authorized-key", ENROLL_TEST_KEY]
        with mock.patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit) as exc:
                ns["main"]()

        assert exc.value.code == 2
        assert "--controller" in capsys.readouterr().err

    def test_regular_enrollment_still_requires_nonempty_key_at_runtime(
        self, monkeypatch, capsys,
    ):
        ns = _render_enroll_ns()
        args = _enroll_args(authorized_key="", controller="control.example.net")
        monkeypatch.setattr(ns["os"], "geteuid", lambda: 0)
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        with pytest.raises(SystemExit) as exc:
            ns["do_enroll"](args, None)
        assert exc.value.code == 2
        assert "--authorized-key is empty" in capsys.readouterr().err

    def test_get_py_cli_render_carries_enroll(self, tmp_path, monkeypatch):
        """The `cmru installer render ciu` path renders a script whose enroll flags match.

        Needs a cmru that knows ``extensions`` (fails, not skips, against an older one).
        The orchestration config interpolates ``${CGROUP_PARENT_DEV_GATES}``; the render
        does not use the slice, so pin a value instead of depending on the caller's env.
        """
        monkeypatch.setenv(
            "CGROUP_PARENT_DEV_GATES", os.environ.get("CGROUP_PARENT_DEV_GATES") or "dev-gates.slice"
        )
        try:
            from cmru.config import InstallerConfig
            from cmru.getpy import installer_main
        except ImportError as exc:  # a missing cmru must be RED, never a silent skip
            pytest.fail(
                "cmru is not importable; put ../cmru/src (and ../libraries/worktree/src) on "
                f"PYTHONPATH and install the released cli-extended wheel: {exc}"
            )
        assert "extensions" in InstallerConfig.__dataclass_fields__, (
            "the importable cmru predates `[project.installer] extensions`"
        )
        out_file = tmp_path / "rendered-get.py"
        rc = installer_main([
            "render", "ciu", "--config", str(CIU_ROOT.parent / "cmru.orchestration.toml"),
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
        'restrict,command="/usr/bin/fleetd-agent forced-command --group worker" '
        'ssh-ed25519 AAAAB3NzaC1kZXN0',
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


class TestFleetdForcedCommand:
    """CIU installs one fixed agent argv; the agent owns the request grammar."""

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    @pytest.fixture(autouse=True)
    def _isolated_target_state(self, ns, tmp_path):
        ns["_ENROLL_STATE_DIR"] = tmp_path / "ciu-state"
        ns["_ENROLL_LOCK_PATH"] = tmp_path / "ciu-locks" / "authorized-keys.lock"

    @pytest.mark.parametrize("args,expected", [
        (_enroll_args(
            forced_command=True, name="web-01", agent_path="/usr/bin/fleetd-agent",
            fleetd_group="campaigns",
        ), "/usr/bin/fleetd-agent forced-command --group campaigns"),
        (_enroll_args(
            forced_command=True, name="web-01", agent_path="/usr/bin/fleetd-agent",
            fleetd_group="campaigns", agent_policy="/etc/fleetd/agent.toml",
        ), "/usr/bin/fleetd-agent forced-command --group campaigns "
           "--policy /etc/fleetd/agent.toml"),
    ])
    def test_builds_only_the_fixed_fleetd_agent_command(self, ns, args, expected):
        assert ns["_forced_command_from_args"](args) == expected

    @pytest.mark.parametrize("agent_path", [
        "fleetd-agent", "/usr/bin/../bin/fleetd-agent", "/usr//bin/fleetd-agent",
        "/usr/bin/fleetd-agent/", "/usr/bin/fleetd agent", "/usr/bin/\"agent\"",
        "/usr/bin/fleetd\\agent", "/usr/bin/fleetd;id", "/usr/bin/$agent",
        "/usr/bin/fleetd-agent\nid", "/usr/bin/\x01agent",
    ])
    def test_refuses_unsafe_agent_paths(self, ns, agent_path):
        args = _enroll_args(
            forced_command=True, name="web-01", agent_path=agent_path,
            fleetd_group="campaigns",
        )
        with pytest.raises(SystemExit) as exc:
            ns["_forced_command_from_args"](args)
        assert exc.value.code == 2

    @pytest.mark.parametrize("group", [
        "", "-bad", "bad group", 'bad"group', "bad;id", "bad\nname", "a" * 129,
    ])
    def test_refuses_groups_outside_fleetd_grammar(self, ns, group):
        args = _enroll_args(
            forced_command=True, name="web-01", agent_path="/usr/bin/fleetd-agent",
            fleetd_group=group,
        )
        with pytest.raises(SystemExit) as exc:
            ns["_forced_command_from_args"](args)
        assert exc.value.code == 2

    @pytest.mark.parametrize("agent_policy", [
        "relative/policy", "/etc/../policy", "/etc//policy", "/etc/policy/",
        "/etc/fleetd bad.toml", "/etc/fleetd\\agent.toml", "/etc/policy;id",
        "/etc/policy\nfile", "/etc/\x01policy",
    ])
    def test_refuses_unsafe_policy_paths(self, ns, agent_policy):
        args = _enroll_args(
            forced_command=True, name="web-01", agent_path="/usr/bin/fleetd-agent",
            fleetd_group="campaigns", agent_policy=agent_policy,
        )
        with pytest.raises(SystemExit) as exc:
            ns["_forced_command_from_args"](args)
        assert exc.value.code == 2

    def test_mode_needs_explicit_path_group_name_and_cannot_combine_from(self, ns):
        missing_path = _enroll_args(forced_command=True, name="web-01", fleetd_group="g")
        missing_group = _enroll_args(forced_command=True, name="web-01",
                                     agent_path="/usr/bin/fleetd-agent")
        missing_name = _enroll_args(forced_command=True, agent_path="/usr/bin/fleetd-agent",
                                    fleetd_group="g")
        with pytest.raises(SystemExit):
            ns["_forced_command_from_args"](missing_path)
        with pytest.raises(SystemExit):
            ns["_forced_command_from_args"](missing_group)
        with pytest.raises(SystemExit):
            ns["_forced_command_from_args"](missing_name)
        with pytest.raises(SystemExit):
            ns["_forced_command_from_args"](_enroll_args(
                forced_command=True, name="web-01", agent_path="/usr/bin/fleetd-agent",
                fleetd_group="g", from_pattern="10.0.0.0/8",
            ))
        with pytest.raises(SystemExit):
            ns["_forced_command_from_args"](_enroll_args(agent_path="/usr/bin/fleetd-agent"))
        with pytest.raises(SystemExit):
            ns["_forced_command_from_args"](_enroll_args(
                forced_command=True, name="web-01", agent_path="/usr/bin/fleetd-agent",
                fleetd_group="g", from_pattern="",
            ))

    def test_restricted_refuses_but_ordinary_mode_appends_past_unparseable_line(
        self, ns, tmp_path, capsys,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = (
            f'broken="quote {ENROLL_TEST_KEY_TYPE} {ENROLL_TEST_KEY_B64}\n'
        ).encode()
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )
        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

        ns["_enroll_install_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            None,
        )
        assert "Key appended" in capsys.readouterr().out
        assert authorized.read_bytes() == original + (
            f"{ENROLL_TEST_KEY_TYPE} {ENROLL_TEST_KEY_B64} ciu@control.example\n"
        ).encode()

    def test_ordinary_conflicting_options_diagnostic_matches_origin_main(self, ns, tmp_path,
                                                                          capsys):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        authorized.write_text(
            f'from="10.0.0.0/8" {ENROLL_TEST_KEY_TYPE} '
            f'{ENROLL_TEST_KEY_B64} ciu@control.example\n',
            encoding="utf-8",
        )
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                None,
            )

        assert exc.value.code == 2
        assert capsys.readouterr().err == (
            f' err {authorized} already carries this key under DIFFERENT options.\n'
            '  present: from="10.0.0.0/8"\n'
            '  wanted:  (none)\n'
            'Refusing to append a second entry for the same key material — '
            'resolve the conflict by hand and re-run.\n'
        )
        assert authorized.read_text(encoding="utf-8").startswith(
            f'from="10.0.0.0/8" '
        )

    @pytest.mark.parametrize("marked", [False, True])
    def test_ordinary_install_refuses_decoded_key_on_restricted_line_without_state(
        self, ns, tmp_path, capsys, marked,
    ):
        key_type, _blob, canonical, alternate = _fake_ecdsa_key_encodings()
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        marker = f" {ns['_restricted_marker']('web-01')}" if marked else ""
        original = (
            f'restrict,command="{command}" {key_type} {canonical} operator{marker}\n'
        ).encode()
        authorized.write_bytes(original)
        ns["_ENROLL_STATE_DIR"] = tmp_path / "missing-state"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_key"](entry, (key_type, alternate, "ordinary"), None)

        assert exc.value.code == 2
        assert f"line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    @pytest.mark.parametrize("mismatch", ["type", "blob"])
    def test_ordinary_install_allows_unrelated_restricted_key_material(
        self, ns, tmp_path, capsys, mismatch,
    ):
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        if mismatch == "type":
            existing = ENROLL_TEST_KEY
            supplied_type, _blob, supplied_b64, _alias = _fake_ecdsa_key_encodings()
        else:
            existing = ENROLL_TEST_KEY_2
            supplied_type, supplied_b64 = ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64
        restricted = f'restrict,command="{command}" {existing}\n'.encode()
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        authorized.write_bytes(restricted)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        ns["_enroll_install_key"](
            entry, (supplied_type, supplied_b64, "ordinary"), None,
        )

        captured = capsys.readouterr()
        assert captured.out == f"  ok Key appended to {authorized}.\n"
        existing_comment = existing.rsplit(" ", 1)[-1]
        assert captured.err == (
            f"warn {authorized} already has 1 other key(s) for this user "
            f"({existing_comment}) — appending this one ALONGSIDE them, not replacing. "
            "If this was meant to be a rotation, remove the old key by hand.\n"
        )
        assert authorized.read_bytes() == restricted + (
            f"{supplied_type} {supplied_b64} ordinary\n".encode()
        )

    @pytest.mark.parametrize("state_kind", ["not-directory", "symlink", "unreadable"])
    def test_ordinary_install_does_not_read_restricted_state_and_keeps_legacy_bytes(
        self, ns, tmp_path, monkeypatch, capsys, state_kind,
    ):
        state_path = tmp_path / "ciu-state"
        if state_kind == "not-directory":
            state_path.write_bytes(b"invalid state directory")
        elif state_kind == "symlink":
            target = tmp_path / "state-target"
            target.mkdir()
            state_path.symlink_to(target, target_is_directory=True)
        else:
            state_path.mkdir()
            state_path.chmod(0)
        ns["_ENROLL_STATE_DIR"] = state_path
        monkeypatch.setitem(
            ns, "_open_enroll_state_dir",
            lambda **_kwargs: pytest.fail("ordinary mode must not inspect CIU state"),
        )

        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = b"not parseable by CIU\xff"
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        try:
            ns["_enroll_install_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                None,
            )
        except SystemExit as exc:
            status = exc.code
        else:
            status = 0

        captured = capsys.readouterr()
        assert status == 0
        assert captured.out == f"  ok Key appended to {authorized}.\n"
        assert captured.err == ""
        assert authorized.read_bytes() == original + (
            b"\n" if not original.endswith(b"\n") else b""
        ) + f"{ENROLL_TEST_KEY_TYPE} {ENROLL_TEST_KEY_B64} ciu@control.example\n".encode()

    def test_restricted_line_is_atomic_idempotent_and_preserves_unrelated_bytes(
        self, ns, tmp_path,
    ):
        home = tmp_path / "home"
        home.mkdir()
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir()
        authorized = ssh_dir / "authorized_keys"
        original = b"# keep CRLF\r\nnot-a-key\xff"
        authorized.write_bytes(original)
        original_inode = authorized.stat().st_ino
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"

        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", command,
        )
        marker = ns["_restricted_marker"]("web-01")
        restricted_line = (
            f'restrict,command="{command}" {ENROLL_TEST_KEY_TYPE} '
            f'{ENROLL_TEST_KEY_B64} ciu@control.example {marker}\n'
        ).encode()
        expected = original + b"\n" + restricted_line
        assert authorized.read_bytes() == expected
        assert authorized.stat().st_ino != original_inode
        assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(authorized.stat().st_mode) == 0o600
        assert authorized.stat().st_uid == os.getuid()
        assert authorized.stat().st_gid == os.getgid()

        inode_after_first = authorized.stat().st_ino
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", command,
        )
        assert authorized.read_bytes() == expected
        assert authorized.stat().st_ino == inode_after_first

    def test_same_managed_key_updates_the_fixed_command_without_duplicate_blob(
        self, ns, tmp_path,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        unrelated = b"# keep\r\n"
        authorized.write_bytes(unrelated)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        key = (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example")
        first = "/usr/bin/fleetd-agent forced-command --group old-group"
        second = "/usr/bin/fleetd-agent forced-command --group new-group"

        ns["_enroll_install_restricted_key"](entry, key, "web-01", first)
        first_inode = authorized.stat().st_ino
        ns["_enroll_install_restricted_key"](entry, key, "web-01", second)

        result = authorized.read_bytes()
        assert result.startswith(unrelated)
        assert result.count(ENROLL_TEST_KEY_B64.encode()) == 1
        assert b"old-group" not in result and b"new-group" in result
        assert result.count(ns["_restricted_marker"]("web-01").encode()) == 1
        assert authorized.stat().st_ino != first_inode

    def test_restricted_install_refuses_existing_unrestricted_key_and_names_line(
        self, ns, tmp_path, capsys,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = (ENROLL_TEST_KEY + "\r\n").encode()
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )
        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    def test_unrestricted_install_refuses_existing_restricted_key_and_names_line(
        self, ns, tmp_path, capsys,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", command,
        )
        original = authorized.read_bytes()
        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                None,
            )
        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    def test_rotation_replaces_only_the_prior_managed_restricted_line(self, ns, tmp_path):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        unrelated = b"# unrelated\r\nssh-rsa AAAA manual\r\n"
        authorized.write_bytes(unrelated)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", command,
        )
        rotated_type, rotated_b64, rotated_comment = ENROLL_TEST_KEY_2.split(maxsplit=2)
        ns["_enroll_install_restricted_key"](
            entry, (rotated_type, rotated_b64, rotated_comment), "web-01", command,
        )
        result = authorized.read_bytes()
        assert result.startswith(unrelated)
        assert ENROLL_TEST_KEY_B64.encode() not in result
        assert rotated_b64.encode() in result
        assert result.count(ns["_restricted_marker"]("web-01").encode()) == 1

    def test_distinct_names_keep_each_recorded_restricted_entry(self, ns, tmp_path):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "first"), "web-01", command,
        )
        ns["_enroll_install_restricted_key"](
            entry, ns["_parse_authorized_key"](ENROLL_TEST_KEY_2), "web-02", command,
        )

        result = authorized.read_bytes()
        assert result.count(ENROLL_TEST_KEY_B64.encode()) == 1
        assert ENROLL_TEST_KEY_2.split()[1].encode() in result
        assert result.count(ns["_restricted_marker"]("web-01").encode()) == 1
        assert result.count(ns["_restricted_marker"]("web-02").encode()) == 1

    def test_unrecorded_marker_on_otherwise_unrelated_line_is_refused(
        self, ns, tmp_path, capsys,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        marker = ns["_restricted_marker"]("web-01")
        original = (
            b"# retained\r\nnot-a-key\xff\r\nssh-rsa AAAA "
            + marker.encode() + b"\r\n"
        )
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )
        assert exc.value.code == 2
        assert "line 3" in capsys.readouterr().err
        assert authorized.read_bytes() == original

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_remove_restricted_keys"](entry, "web-01")
        assert exc.value.code == 2
        assert "line 3" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    @pytest.mark.parametrize("operation", ["remove", "rotate"])
    def test_unrecorded_forced_command_shape_is_refused(self, ns, tmp_path, capsys,
                                                        operation):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        original = (
            f'restrict,command="{command}" {ENROLL_TEST_KEY}\n'
        ).encode()
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            if operation == "remove":
                ns["_enroll_remove_restricted_keys"](entry, "web-01")
            else:
                ns["_enroll_install_restricted_key"](
                    entry,
                    ns["_parse_authorized_key"](ENROLL_TEST_KEY_2),
                    "web-01", command,
                )

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    def test_removal_refuses_structurally_valid_marker_without_state_record(
        self, ns, tmp_path, capsys,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        marker = ns["_restricted_marker"]("web-01")
        forged = (
            f'restrict,command="{command}" {ENROLL_TEST_KEY_TYPE} '
            f'{ENROLL_TEST_KEY_B64} operator-owned {marker}\n'
        ).encode()
        authorized.write_bytes(forged)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_remove_restricted_keys"](entry, "web-01")

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == forged

    def test_lost_state_file_refuses_to_remove_the_recorded_shape(self, ns, tmp_path, capsys):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        original = authorized.read_bytes()
        state_dir = Path(ns["_ENROLL_STATE_DIR"])
        state_files = list(state_dir.iterdir())
        assert len(state_files) == 1
        state_files[0].unlink()

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_remove_restricted_keys"](entry, "web-01")

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    @pytest.mark.parametrize("orphan_kind", ["marker", "shape"])
    def test_pending_remove_missing_record_refuses_unrecorded_marker_or_shape(
        self, ns, tmp_path, capsys, orphan_kind,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "recorded"),
            "web-01", command,
        )
        committed = ns["_load_enroll_state"](entry, "web-01")
        pending = ns["_make_enroll_state"](
            entry, "web-01", "pending-remove", record=committed["record"],
        )
        ns["_save_enroll_state"](entry, "web-01", pending)
        if orphan_kind == "marker":
            orphan = (
                f"not-a-key {ns['_restricted_marker']('web-01')}\n"
            ).encode()
        else:
            other_type, other_b64, _other_comment = ENROLL_TEST_KEY_2.split(maxsplit=2)
            orphan = (
                f'restrict,command="{command}" {other_type} {other_b64} orphan\n'
            ).encode()
        authorized.write_bytes(orphan)

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_remove_restricted_keys"](entry, "web-01")

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == orphan
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "pending-remove"

    def test_remove_crash_before_key_replace_retries_without_losing_record(
        self, ns, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "fixture"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        before = authorized.read_bytes()
        real_write = ns["_write_restricted_authorized_keys"]

        def crash_before_replace(*_args, **_kwargs):
            raise RuntimeError("simulated removal crash before key replacement")

        monkeypatch.setitem(ns, "_write_restricted_authorized_keys", crash_before_replace)
        with pytest.raises(RuntimeError, match="before key replacement"):
            ns["_enroll_remove_restricted_keys"](entry, "web-01")
        assert authorized.read_bytes() == before
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "pending-remove"

        monkeypatch.setitem(ns, "_write_restricted_authorized_keys", real_write)
        assert ns["_enroll_remove_restricted_keys"](entry, "web-01") == 1
        assert authorized.read_bytes() == b""
        assert ns["_load_enroll_state"](entry, "web-01") is None

    def test_remove_crash_after_key_replace_clears_pending_state_on_retry(
        self, ns, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "fixture"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        real_save = ns["_save_enroll_state"]
        saves = 0

        def crash_when_clearing(*args, **kwargs):
            nonlocal saves
            saves += 1
            if saves == 2:
                raise RuntimeError("simulated removal crash after key replacement")
            return real_save(*args, **kwargs)

        monkeypatch.setitem(ns, "_save_enroll_state", crash_when_clearing)
        with pytest.raises(RuntimeError, match="after key replacement"):
            ns["_enroll_remove_restricted_keys"](entry, "web-01")
        assert authorized.read_bytes() == b""
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "pending-remove"

        monkeypatch.setitem(ns, "_save_enroll_state", real_save)
        assert ns["_enroll_remove_restricted_keys"](entry, "web-01") == 0
        assert authorized.read_bytes() == b""
        assert ns["_load_enroll_state"](entry, "web-01") is None

    def test_rotation_crash_before_key_replace_retries_with_old_line_intact(
        self, ns, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "original"), "web-01", command,
        )
        before = authorized.read_bytes()
        real_write = ns["_write_restricted_authorized_keys"]
        rotated = ns["_parse_authorized_key"](ENROLL_TEST_KEY_2)

        def crash_before_replace(*_args, **_kwargs):
            raise RuntimeError("simulated rotation crash before key replacement")

        monkeypatch.setitem(ns, "_write_restricted_authorized_keys", crash_before_replace)
        with pytest.raises(RuntimeError, match="before key replacement"):
            ns["_enroll_install_restricted_key"](entry, rotated, "web-01", command)
        assert authorized.read_bytes() == before
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "prepared"

        monkeypatch.setitem(ns, "_write_restricted_authorized_keys", real_write)
        ns["_enroll_install_restricted_key"](entry, rotated, "web-01", command)
        assert authorized.read_bytes().count(rotated[1].encode()) == 1
        assert ENROLL_TEST_KEY_B64.encode() not in authorized.read_bytes()
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "committed"

    def test_rotation_crash_after_key_replace_finishes_from_prepared_state(
        self, ns, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "original"), "web-01", command,
        )
        real_save = ns["_save_enroll_state"]
        saves = 0
        rotated = ns["_parse_authorized_key"](ENROLL_TEST_KEY_2)

        def crash_when_committing(*args, **kwargs):
            nonlocal saves
            saves += 1
            if saves == 2:
                raise RuntimeError("simulated rotation crash after key replacement")
            return real_save(*args, **kwargs)

        monkeypatch.setitem(ns, "_save_enroll_state", crash_when_committing)
        with pytest.raises(RuntimeError, match="after key replacement"):
            ns["_enroll_install_restricted_key"](entry, rotated, "web-01", command)
        after_replace = authorized.read_bytes()
        assert after_replace.count(rotated[1].encode()) == 1
        assert ENROLL_TEST_KEY_B64.encode() not in after_replace
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "prepared"

        monkeypatch.setitem(ns, "_save_enroll_state", real_save)
        ns["_enroll_install_restricted_key"](entry, rotated, "web-01", command)
        assert authorized.read_bytes() == after_replace
        assert ns["_load_enroll_state"](entry, "web-01")["status"] == "committed"

    def test_duplicate_recorded_line_refuses_removal_without_changing_bytes(
        self, ns, tmp_path, capsys,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        original = authorized.read_bytes()
        authorized.write_bytes(original + original.splitlines(keepends=True)[-1])
        duplicate = authorized.read_bytes()

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_remove_restricted_keys"](entry, "web-01")

        assert exc.value.code == 2
        assert "line" in capsys.readouterr().err
        assert authorized.read_bytes() == duplicate

    def test_add_then_remove_restores_original_file_without_final_newline(self, ns, tmp_path):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = b"# operator record without final newline"
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        assert authorized.read_bytes().startswith(original + b"\n")

        assert ns["_enroll_remove_restricted_keys"](entry, "web-01") == 1
        assert authorized.read_bytes() == original

    def test_state_is_prepared_before_key_write_and_repairs_first_crash_half(
        self, ns, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = b"# retained without newline"
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        real_write = ns["_write_restricted_authorized_keys"]

        def fail_before_key_commit(*args, **kwargs):
            raise RuntimeError("simulated crash before key commit")

        monkeypatch.setitem(ns, "_write_restricted_authorized_keys", fail_before_key_commit)
        with pytest.raises(RuntimeError, match="before key commit"):
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )
        assert authorized.read_bytes() == original
        state_dir = Path(ns["_ENROLL_STATE_DIR"])
        state_files = list(state_dir.iterdir())
        assert len(state_files) == 1
        assert state_files[0].stat().st_uid == os.geteuid()
        assert stat.S_IMODE(state_files[0].stat().st_mode) == 0o600

        monkeypatch.setitem(ns, "_write_restricted_authorized_keys", real_write)
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        assert authorized.read_bytes().count(ENROLL_TEST_KEY_B64.encode()) == 1

    def test_crash_after_key_commit_repairs_from_prepared_state_without_duplicate(
        self, ns, tmp_path, monkeypatch,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        real_save = ns.get("_save_enroll_state")
        assert real_save is not None
        saves = 0

        def fail_after_key_commit(*args, **kwargs):
            nonlocal saves
            saves += 1
            if saves == 2:
                raise RuntimeError("simulated crash after key commit")
            return real_save(*args, **kwargs)

        monkeypatch.setitem(ns, "_save_enroll_state", fail_after_key_commit)
        with pytest.raises(RuntimeError, match="after key commit"):
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )
        committed_bytes = authorized.read_bytes()
        assert committed_bytes.count(ENROLL_TEST_KEY_B64.encode()) == 1

        monkeypatch.setitem(ns, "_save_enroll_state", real_save)
        ns["_enroll_install_restricted_key"](
            entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
            "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        assert authorized.read_bytes() == committed_bytes
        assert len(list(Path(ns["_ENROLL_STATE_DIR"]).iterdir())) == 1

    @pytest.mark.parametrize("replace_inode", [False, True])
    def test_restricted_writer_refuses_authorized_keys_change_after_read(
        self, ns, tmp_path, monkeypatch, replace_inode,
    ):
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = b"# original contents\n"
        changed = b"# external writer changed this file\n"
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        def change_after_read(path, _expected):
            if replace_inode:
                replacement = tmp_path / "replacement"
                replacement.write_bytes(changed)
                os.replace(replacement, path)
            else:
                path.write_bytes(changed)

        monkeypatch.setitem(ns, "_enroll_before_authorized_keys_commit", change_after_read)
        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )

        assert exc.value.code == 2
        assert authorized.read_bytes() == changed

    @pytest.mark.parametrize("restricted", [False, True])
    def test_both_enrollment_modes_hold_the_shared_lock_through_commit(
        self, ns, tmp_path, monkeypatch, restricted,
    ):
        import fcntl

        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        saw_locked = []

        def check_lock(_path, _expected):
            lock_path = Path(ns["_ENROLL_LOCK_PATH"])
            fd = os.open(lock_path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                saw_locked.append(True)
            finally:
                os.close(fd)

        monkeypatch.setitem(ns, "_enroll_before_authorized_keys_commit", check_lock)
        if restricted:
            ns["_enroll_install_restricted_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                "web-01", "/usr/bin/fleetd-agent forced-command --group campaigns",
            )
        else:
            ns["_enroll_install_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                None,
            )
        assert saw_locked == [True]

    def test_authorized_keys_lock_retries_nonblocking_until_lock_is_available(
        self, ns, tmp_path, monkeypatch,
    ):
        import fcntl

        real_flock = ns["fcntl"].flock
        attempts = []
        waits = []
        now = [0.0]

        def contended_then_acquired(fd, operation):
            attempts.append(operation)
            if operation == fcntl.LOCK_UN:
                return real_flock(fd, operation)
            if len([op for op in attempts if op != fcntl.LOCK_UN]) < 3:
                raise BlockingIOError()
            return real_flock(fd, operation)

        monkeypatch.setattr(ns["fcntl"], "flock", contended_then_acquired)
        monkeypatch.setitem(ns, "_ENROLL_LOCK_TIMEOUT_SECONDS", 2.0)
        monkeypatch.setitem(ns, "_ENROLL_LOCK_RETRY_SECONDS", 0.5)
        monkeypatch.setitem(ns, "_enroll_lock_clock", lambda: now[0])

        def advance_clock(seconds):
            waits.append(seconds)
            now[0] += seconds

        monkeypatch.setitem(ns, "_enroll_lock_wait", advance_clock)
        with ns["_authorized_keys_lock"]():
            assert Path(ns["_ENROLL_LOCK_PATH"]).exists()

        assert [op for op in attempts if op != fcntl.LOCK_UN] == [
            fcntl.LOCK_EX | fcntl.LOCK_NB,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        ]
        assert waits == [0.5, 0.5]

    def test_authorized_keys_lock_timeout_names_recovery_and_changes_nothing(
        self, ns, tmp_path, monkeypatch, capsys,
    ):
        import fcntl

        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = b"# existing operator entry\r\n"
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        lock_path = Path(ns["_ENROLL_LOCK_PATH"])
        now = [0.0]
        waits = []

        def always_contended(_fd, operation):
            if operation == fcntl.LOCK_UN:
                pytest.fail("a lock that was never acquired must not be unlocked")
            raise BlockingIOError()

        monkeypatch.setattr(ns["fcntl"], "flock", always_contended)
        monkeypatch.setitem(ns, "_ENROLL_LOCK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setitem(ns, "_ENROLL_LOCK_RETRY_SECONDS", 0.25)
        monkeypatch.setitem(ns, "_enroll_lock_clock", lambda: now[0])

        def advance_clock(seconds):
            waits.append(seconds)
            now[0] += seconds

        monkeypatch.setitem(ns, "_enroll_lock_wait", advance_clock)
        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_key"](
                entry, (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example"),
                None,
            )

        assert exc.value.code == 2
        error = capsys.readouterr().err
        assert str(lock_path) in error
        assert "timed out" in error.lower()
        assert "check" in error.lower() and "retry" in error.lower()
        assert waits == [0.25, 0.25, 0.25, 0.25]
        assert authorized.read_bytes() == original

    @pytest.mark.parametrize("key_type,key_b64", [
        (ENROLL_TEST_KEY_TYPE, "AAAA"),
        ("ssh-rsa", ENROLL_TEST_KEY_B64),
    ])
    def test_restricted_install_refuses_malformed_or_mismatched_supplied_blob(
        self, ns, tmp_path, key_type, key_b64,
    ):
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        authorized = home / ".ssh" / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (key_type, key_b64, "fixture"), "web-01",
                "/usr/bin/fleetd-agent forced-command --group campaigns",
            )

        assert exc.value.code == 2
        assert not authorized.exists()

    def test_restricted_install_refuses_noncanonical_base64_for_supplied_key(
        self, ns, tmp_path,
    ):
        key_type, _blob, _canonical, alternate = _fake_ecdsa_key_encodings()
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        authorized = home / ".ssh" / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (key_type, alternate, "fixture"), "web-01",
                "/usr/bin/fleetd-agent forced-command --group campaigns",
            )

        assert exc.value.code == 2
        assert not authorized.exists()

    def test_restricted_install_detects_equivalent_unrestricted_base64_encoding(
        self, ns, tmp_path, capsys,
    ):
        key_type, _blob, canonical, alternate = _fake_ecdsa_key_encodings()
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        original = f"{key_type} {alternate} operator\n".encode()
        authorized.write_bytes(original)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_restricted_key"](
                entry, (key_type, canonical, "fixture"), "web-01",
                "/usr/bin/fleetd-agent forced-command --group campaigns",
            )

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    def test_ordinary_install_detects_equivalent_key_in_recorded_restricted_entry(
        self, ns, tmp_path, capsys,
    ):
        key_type, _blob, canonical, alternate = _fake_ecdsa_key_encodings()
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        ns["_enroll_install_restricted_key"](
            entry, (key_type, canonical, "fixture"), "web-01",
            "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        original = authorized.read_bytes()

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_key"](entry, (key_type, alternate, "fixture"), None)

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    def test_ordinary_install_detects_reencoded_blob_in_recorded_restricted_entry(
        self, ns, tmp_path, capsys,
    ):
        key_type, _blob, canonical, alternate = _fake_ecdsa_key_encodings()
        home = tmp_path / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        authorized = ssh_dir / "authorized_keys"
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        ns["_enroll_install_restricted_key"](
            entry, (key_type, canonical, "fixture"), "web-01",
            "/usr/bin/fleetd-agent forced-command --group campaigns",
        )
        authorized.write_bytes(authorized.read_bytes().replace(
            canonical.encode(), alternate.encode(),
        ))
        original = authorized.read_bytes()

        with pytest.raises(SystemExit) as exc:
            ns["_enroll_install_key"](entry, (key_type, canonical, "fixture"), None)

        assert exc.value.code == 2
        assert "line 1" in capsys.readouterr().err
        assert authorized.read_bytes() == original

    def test_restricted_writer_refuses_symlinked_ssh_or_authorized_keys(
        self, ns, tmp_path,
    ):
        command = "/usr/bin/fleetd-agent forced-command --group campaigns"
        entry_args = (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64, "ciu@control.example")

        home = tmp_path / "ssh-home"
        home.mkdir()
        outside_ssh = tmp_path / "outside-ssh"
        outside_ssh.mkdir()
        (home / ".ssh").symlink_to(outside_ssh, target_is_directory=True)
        entry = argparse.Namespace(pw_dir=str(home), pw_uid=os.getuid(), pw_gid=os.getgid())
        with pytest.raises(SystemExit):
            ns["_enroll_install_restricted_key"](entry, entry_args, "web-01", command)
        assert list(outside_ssh.iterdir()) == []

        home2 = tmp_path / "key-home"
        ssh_dir = home2 / ".ssh"
        ssh_dir.mkdir(parents=True)
        outside_keyfile = tmp_path / "outside-authorized-keys"
        outside_keyfile.write_bytes(b"leave me byte-identical")
        (ssh_dir / "authorized_keys").symlink_to(outside_keyfile)
        entry2 = argparse.Namespace(pw_dir=str(home2), pw_uid=os.getuid(), pw_gid=os.getgid())
        with pytest.raises(SystemExit):
            ns["_enroll_install_restricted_key"](entry2, entry_args, "web-01", command)
        assert outside_keyfile.read_bytes() == b"leave me byte-identical"


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
# Direct invocation does not get the isolated Docker daemon. The registered
# release lane runs this suite inside tester-unified with CMRU's DinD sidecar;
# CIU_ENROLL_REQUIRED=1 makes missing wiring fatal so O2/O3 cannot disappear
# from release evidence behind a skip.

ENROLL_FIXTURE_IMAGE = "ciu-enroll-fixture:local"
ENROLL_FIXTURE_DOCKERFILE = """\
# mirror.gcr.io serves the same digest-pinned content without Docker Hub's per-IP pull limit
# (the enroll gate runs in an isolated DinD sidecar with no registry credentials).
FROM mirror.gcr.io/library/debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171
RUN apt-get update \\
 && apt-get install -y --no-install-recommends \\
        openssh-server python3 iproute2 hostname passwd \\
 && rm -rf /var/lib/apt/lists/* \\
 && ssh-keygen -A
"""


def _docker_unavailable_reason() -> Optional[str]:
    if os.environ.get("CIU_ENROLL_REQUIRED") != "1":
        return "enrollment container oracles run only through the registered CIU release gate"
    if os.environ.get("DOCKER_HOST", "").strip() != "tcp://localhost:2375":
        return "registered enrollment gate requires CMRU tester-gate --enable-docker"
    if not os.environ.get("CGROUP_PARENT_DEV_GATES", "").strip():
        return "CMRU did not forward CGROUP_PARENT_DEV_GATES into tester-unified"
    if shutil.which("docker") is None:
        return "docker CLI not present"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return f"CMRU's isolated nested Docker daemon is unreachable: {probe.stderr.strip()[:200]}"
    return None


@pytest.mark.parametrize(
    "docker_host", [None, "unix:///var/run/docker.sock", "tcp://host:2375"],
)
def test_enroll_preflight_requires_the_registered_dind_endpoint(monkeypatch, docker_host):
    monkeypatch.setenv("CIU_ENROLL_REQUIRED", "1")
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    if docker_host is None:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
    else:
        monkeypatch.setenv("DOCKER_HOST", docker_host)
    assert "tester-gate --enable-docker" in _docker_unavailable_reason()


def test_enroll_preflight_requires_the_forwarded_gates_slice(monkeypatch):
    monkeypatch.setenv("CIU_ENROLL_REQUIRED", "1")
    monkeypatch.setenv("DOCKER_HOST", "tcp://localhost:2375")
    monkeypatch.delenv("CGROUP_PARENT_DEV_GATES", raising=False)

    assert "did not forward CGROUP_PARENT_DEV_GATES" in _docker_unavailable_reason()


def test_registered_enroll_lane_fails_closed_when_dind_endpoint_is_missing(monkeypatch):
    monkeypatch.setenv("CIU_ENROLL_REQUIRED", "1")
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    with pytest.raises(pytest.fail.Exception, match="registered enrollment gate preflight failed"):
        enroll_fixture_image.__wrapped__()


def test_enroll_preflight_accepts_the_registered_tester_gate_endpoint(monkeypatch):
    monkeypatch.setenv("CIU_ENROLL_REQUIRED", "1")
    monkeypatch.setenv("DOCKER_HOST", "tcp://localhost:2375")
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs:
        subprocess.CompletedProcess(["docker", "info"], 0, "", ""),
    )
    assert _docker_unavailable_reason() is None


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

    The outer tester-gate and its DinD sidecar are placed under the verified
    estate gates slice. This inner fixture is bounded inside that sidecar and
    removed in a finally so a failing assertion never leaves one running.
    """
    container = f"ciu-enroll-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    started = subprocess.run(
        ["docker", "run", "-d", "--init", f"--name={container}",
         "--cpus=1.5", "--memory=1g",
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

            started = _cexec(
                c, "sh", "-c",
                "mkdir -p /run/sshd && chown root:root /run/sshd "
                "&& chmod 0755 /run/sshd && /usr/sbin/sshd -p 2222",
            )
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
