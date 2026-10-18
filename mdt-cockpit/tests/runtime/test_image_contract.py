"""11a - the image contract, with no mounts.

Everything here runs in a plain container from the candidate image as its default
user (`vscode`). Expected locations are derived from the repository's own
declarations (`ai-cli-tools.list`, `pip/wheels.list`), not retyped, so adding a tool
without a place for it fails here.
"""
from __future__ import annotations

import re
import unittest

import harness

MODE_DIRECTORY = {
    "root": "/opt/mdt/bin",          # standalone binaries (D4)
    "npm-image": "/opt/mdt/npm/bin",  # npm packages
    "venv": "/opt/mdt/venv/bin",     # the primary venv
}
# Tools that install more than one command.
EXTRA_COMMANDS = {"antigravity": ("agy",)}
# Tools without a `--version` flag: the harmless subcommand that proves the program starts
# (claudelink treats an unknown flag as a usage error and prints its help banner).
PROBE_ARGS = {"claudelink": "help"}
# First-party wheels that are libraries, not CLIs: checked by import instead.
LIBRARY_ONLY = {"cli-extended": "cli_extended"}


def _declared_ai_tools() -> dict[str, str]:
    tools: dict[str, str] = {}
    for raw in (harness.ROOT / "ai-cli-tools.list").read_text(encoding="utf-8").splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        tool, _, mode = line.partition("|")
        tools[tool.strip()] = mode.strip()
    return tools


def _declared_first_party() -> list[str]:
    names = []
    for raw in (harness.ROOT / "pip" / "wheels.list").read_text(encoding="utf-8").splitlines():
        line = raw.partition("#")[0].strip()
        if line:
            names.append(line)
    return names


def _size_bytes(text: str) -> float:
    match = re.fullmatch(r"([0-9.]+)\s*([kMGT]?B)", text.strip())
    if not match:
        raise AssertionError(f"unparseable layer size {text!r}")
    scale = {"B": 1, "kB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[match.group(2)]
    return float(match.group(1)) * scale


class Contract:
    def test_the_build_mode_label_is_present_and_agrees_with_the_image_tag(self) -> None:
        """An absent label must never read as "release": the provenance tests skip release
        images, so a missing label would silently switch them all off."""
        self.assertIn(
            harness.BUILD_MODE_LABEL, self.image.labels,
            f"{self.image.ref} has no {harness.BUILD_MODE_LABEL} label",
        )
        mode = self.image.labels[harness.BUILD_MODE_LABEL]
        self.assertIn(mode, ("release", "local-dev"), f"unknown build mode {mode!r}")
        tag = self.image.ref.rsplit("/", 1)[-1].partition("@")[0]
        self.assertIn(":", tag, f"{self.image.ref} has no tag to compare with the build mode; pass the tag")
        has_dev_segment = re.search(r"-dev(-|$)", tag.split(":", 1)[1]) is not None
        if mode == "local-dev":
            self.assertTrue(has_dev_segment, f"a local-dev image's tag must contain '-dev': {self.image.ref}")
        else:
            self.assertFalse(has_dev_segment, f"a release image's tag must not contain '-dev': {self.image.ref}")

    def test_home_is_empty_and_owned_by_vscode(self) -> None:
        result = self.sh("ls -A /home/vscode | wc -l; stat -c '%U:%G' /home/vscode; id -un")
        self.assertOk(result)
        count, owner, user = result.stdout.split()
        self.assertEqual(count, "0", "the image's home must contain nothing")
        self.assertEqual(owner, "vscode:vscode")
        self.assertEqual(user, "vscode", "the image's default user")

    def test_vscode_is_a_member_of_group_mdt_with_the_policy_gid(self) -> None:
        result = self.sh("id -nG vscode; getent group mdt; id -nG")
        self.assertOk(result)
        member_of, group_entry, current = result.stdout.splitlines()
        self.assertIn("mdt", member_of.split())
        self.assertEqual(group_entry.split(":")[2], "2750", "MDT_GID default (a documented policy choice)")
        self.assertIn("mdt", current.split(), "the running process, not only /etc/group")

    def test_mdt_group_contract_holds_in_a_running_container(self) -> None:
        # An independent re-implementation of the contract (not the in-image script's
        # own predicates), so a bug in mdt-perms cannot hide a violation from itself.
        result = self.sh(
            "bad_group=$(find /opt/mdt /srv ! -type l ! -group mdt -print -quit); "
            "bad_dir=$(find /opt/mdt /srv -type d \\( ! -perm -2000 -o ! -perm -0070 \\) -print -quit); "
            "bad_file=$(find /opt/mdt /srv -type f ! -perm -0020 -print -quit); "
            "world=$(find /opt/mdt /srv ! -type l -perm -0002 -print -quit); "
            "echo \"group=[$bad_group]\"; echo \"dir=[$bad_dir]\"; echo \"file=[$bad_file]\"; "
            "echo \"world=[$world]\"; stat -c '%a %U:%G' /opt/mdt /srv; mdt-perms check /opt/mdt /srv; echo rc=$?"
        )
        self.assertOk(result)
        self.assertEqual(
            result.stdout.splitlines(),
            ["group=[]", "dir=[]", "file=[]", "world=[]", "2775 root:mdt", "2775 root:mdt", "rc=0"],
            result.output,
        )

    def test_the_managed_roots_exist(self) -> None:
        result = self.sh(
            "for d in /opt/mdt/bin /opt/mdt/npm /opt/mdt/venv /opt/mdt/venvs /opt/mdt/python "
            "/srv/pwmcp-instance; do test -d \"$d\" || echo \"missing $d\"; done"
        )
        self.assertOk(result)
        self.assertEqual(result.stdout.strip(), "")

    def test_ai_cli_tools_resolve_to_their_d4_location_and_run(self) -> None:
        commands: list[tuple[str, str, str]] = []
        for tool, mode in _declared_ai_tools().items():
            self.assertIn(mode, MODE_DIRECTORY, f"{tool}: no D4 location is defined for mode {mode!r}")
            for command in (tool, *EXTRA_COMMANDS.get(tool, ())):
                commands.append((tool, command, MODE_DIRECTORY[mode]))
        self._check_commands(commands)

    def test_first_party_clis_resolve_to_the_primary_venv_and_run(self) -> None:
        commands = [
            (name, name, "/opt/mdt/venv/bin")
            for name in _declared_first_party()
            if name not in LIBRARY_ONLY
        ]
        self._check_commands(commands)
        for name, module in LIBRARY_ONLY.items():
            result = self.sh(f"python -c 'import {module}; print({module}.__file__)'")
            self.assertOk(result, f"import of {name}")
            self.assertTrue(result.stdout.strip().startswith("/opt/mdt/venv/"), result.stdout)

    def test_the_pwmcp_cli_is_present_and_runs(self) -> None:
        self._check_commands([("pwmcp", "pwmcp", "/srv/pwmcp-instance/bin")])

    def _check_commands(self, commands: list[tuple[str, str, str]]) -> None:
        script = (
            "for c in " + " ".join(f"'{command}'" for _, command, _ in commands) + "; do "
            "p=$(command -v \"$c\" || echo MISSING); "
            "case \"$c\" in " + " ".join(f"{tool}) arg={arg};;" for tool, arg in PROBE_ARGS.items()) + " *) arg=--version;; esac; "
            "out=$(\"$c\" $arg 2>&1 </dev/null); rc=$?; "
            "printf '%s|%s|%s|%s\\n' \"$c\" \"$p\" \"$rc\" \"$(printf '%s' \"$out\" | grep -m1 '[^[:space:]]' | cut -c1-80)\"; "
            "done"
        )
        result = self.sh(script)
        self.assertOk(result)
        seen = {}
        for line in result.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                seen[parts[0]] = parts
        for tool, command, directory in commands:
            with self.subTest(tool=tool, command=command):
                self.assertIn(command, seen, result.output)
                _, path, rc, first_line = seen[command]
                self.assertEqual(path, f"{directory}/{command}", f"{command} must resolve to its D4 location")
                self.assertEqual(rc, "0", f"`{command} {PROBE_ARGS.get(command, '--version')}` failed: {first_line}")
                self.assertTrue(first_line.strip(), f"`{command}` printed nothing")

    def test_python_is_the_primary_venv_without_claiming_activation(self) -> None:
        result = self.sh(
            "python -c 'import sys; print(sys.prefix)'; command -v python; "
            "if [ -z \"${VIRTUAL_ENV+x}\" ]; then echo VIRTUAL_ENV-unset; else echo VIRTUAL_ENV=$VIRTUAL_ENV; fi"
        )
        self.assertOk(result)
        self.assertEqual(
            result.stdout.splitlines(),
            ["/opt/mdt/venv", "/opt/mdt/venv/bin/python", "VIRTUAL_ENV-unset"],
        )

    def test_location_variables(self) -> None:
        result = self.sh(
            "echo \"npm=$NPM_CONFIG_PREFIX\"; echo \"uv=$UV_PYTHON_INSTALL_DIR\"; "
            "echo \"login-zsh=$(bash -lc 'echo ${ZSH-unset}')\"; test -d /opt/mdt/oh-my-zsh && echo omz-dir || echo no-omz-dir"
        )
        self.assertOk(result)
        lines = dict(line.split("=", 1) if "=" in line else (line, "") for line in result.stdout.splitlines())
        self.assertEqual(lines["npm"], "/opt/mdt/npm")
        self.assertEqual(lines["uv"], "/opt/mdt/python")
        if self.is_vsc:
            self.assertEqual(lines["login-zsh"], "/opt/mdt/oh-my-zsh")
            self.assertIn("omz-dir", lines)
        else:
            self.assertEqual(lines["login-zsh"], "unset")
            self.assertIn("no-omz-dir", lines)

    def test_no_layer_exists_only_to_change_permissions_of_the_mdt_tree(self) -> None:
        """O2: a recursive chgrp/chmod over earlier-layer content copies it up (a layer of
        /opt/mdt size). The mdt tree is constructed with the right ownership instead."""
        history = harness._docker(
            ["history", "--no-trunc", "--format", "{{.Size}}\t{{.CreatedBy}}", self.image.ref]
        )
        self.assertEqual(history.returncode, 0, history.stderr)
        offenders = []
        for line in history.stdout.decode("utf-8", "replace").splitlines():
            size, _, created_by = line.partition("\t")
            if (
                re.search(r"\b(chgrp|chmod|chown)\s+(?:-\w+\s+)*-[A-Za-z]*R[^;&|\n]*?(?:/opt/mdt|/srv)", created_by)
                and _size_bytes(size) >= 50e6
            ):
                offenders.append((size, created_by[:160]))
        self.assertEqual(offenders, [], "permission-only layer(s) of /opt/mdt size")


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
