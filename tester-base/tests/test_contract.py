"""Contract of the tester-base image (T4), stdlib only.

The driver runs on the bare host and starts the exact CANDIDATE image named by
TESTER_BASE_IMAGE in its own container: gate tier cgroup parent, offline, `--init`,
uniquely named, removed BY EXACT NAME. There is no default image; an unset variable
is a configuration error. Nothing here sleeps: every check is one synchronous
`docker run` that returns when its process exits.

The negative assertions encode T2 (what the base deliberately does NOT carry); a
tool appearing there is a change of contract, not a convenience.
"""
from __future__ import annotations

import itertools
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHELLCHECK = re.search(
    r"^ARG SHELLCHECK_VERSION=(\S+)$", (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.MULTILINE
).group(1)
_PID = os.getpid()
_COUNTER = itertools.count(1)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. The contract lane tests the exact image it is given and has "
            "no default; run `TESTER_BASE_IMAGE=<ref> ./run-gate.py contract`."
        )
    return value


IMAGE = _require("TESTER_BASE_IMAGE")
GATES = _require("CGROUP_PARENT_DEV_GATES")


def run(script: str) -> subprocess.CompletedProcess:
    name = f"tester-base-contract-{_PID}-{next(_COUNTER)}"
    try:
        return subprocess.run(
            ["docker", "run", "--rm", "--name", name, "--init", "--network", "none",
             "--cgroup-parent", GATES, "--entrypoint", "bash", IMAGE, "-c", script],
            capture_output=True, text=True, check=False, timeout=600,
        )
    finally:
        # --rm removes it on exit; this exact-name removal covers a timeout/kill path.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


class PositiveContract(unittest.TestCase):
    def check(self, script: str) -> str:
        result = run(script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_python_is_3_14_at_the_conventional_path(self) -> None:
        out = self.check("/usr/local/bin/python3.14 -c 'import sys; print(sys.version_info[:2])'")
        self.assertEqual(out.strip(), "(3, 14)")

    def test_docker_client_runs_and_is_the_cli_only(self) -> None:
        self.assertRegex(self.check("docker --version"), r"^Docker version \d+\.\d+")

    def test_minisign_runs(self) -> None:
        self.assertRegex(self.check("minisign -v"), r"minisign\s+\d+\.\d+")

    def test_shellcheck_is_the_pinned_version(self) -> None:
        self.assertIn(f"version: {PINNED_SHELLCHECK}", self.check("shellcheck --version"))

    def test_git_is_the_debian_2_47_line(self) -> None:
        self.assertRegex(self.check("git --version"), r"^git version 2\.47\.")

    def test_the_compiler_stays_for_sdist_builds(self) -> None:
        self.check("gcc --version && g++ --version")

    def test_locale_is_c_utf8(self) -> None:
        self.assertEqual(self.check('printf %s "$LANG"'), "C.UTF-8")


class NegativeContract(unittest.TestCase):
    """T2: what the base must NOT contain."""

    def absent(self, command: str) -> None:
        result = run(f"command -v {command}")
        self.assertNotEqual(result.returncode, 0, f"{command} is present: {result.stdout}")

    def test_tools_that_belong_to_other_images_are_absent(self) -> None:
        for command in ("crane", "regctl", "tc", "systemd-tmpfiles", "fdisk", "zsh", "nvim", "node", "php", "claude"):
            with self.subTest(command=command):
                self.absent(command)

    def test_docker_has_neither_buildx_nor_compose(self) -> None:
        for sub in ("buildx", "compose"):
            with self.subTest(plugin=sub):
                result = run(f"docker {sub} version")
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_no_vscode_home_no_virtualenv_no_ksm_preload(self) -> None:
        result = run('test ! -e /home/vscode && test -z "${VIRTUAL_ENV:-}" && test -z "${LD_PRELOAD:-}"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_user_is_created(self) -> None:
        result = run("getent passwd 1003 && exit 1; exit 0")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_apt_lists_are_not_left_in_the_layer(self) -> None:
        result = run("test \"$(find /var/lib/apt/lists -type f ! -name lock | wc -l)\" = 0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DockerfilePins(unittest.TestCase):
    """Static: the pins and verifications that make the build reproducible and safe."""

    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    def test_from_is_digest_pinned_python_3_14_trixie_not_slim(self) -> None:
        match = re.search(r"^FROM (\S+)$", self.text, re.MULTILINE)
        self.assertRegex(match.group(1), r"^python:3\.14-trixie@sha256:[0-9a-f]{64}$")

    def test_the_base_pin_records_tag_digest_created_and_how(self) -> None:
        for needle in ("digest", "created", "how read", "14 days"):
            self.assertIn(needle, self.text)

    def test_docker_key_is_verified_by_fingerprint(self) -> None:
        self.assertRegex(self.text, r"ARG DOCKER_APT_KEY_FPR=[0-9A-F]{40}")
        self.assertIn('test "${actual}" = "${DOCKER_APT_KEY_FPR}"', self.text)

    def test_shellcheck_is_pinned_by_sha256_for_both_architectures(self) -> None:
        self.assertEqual(len(re.findall(r"ARG SHELLCHECK_SHA256_\w+=[0-9a-f]{64}", self.text)), 2)
        self.assertIn("sha256sum -c -", self.text)

    def test_no_recommended_plugins_are_installed(self) -> None:
        self.assertIn("--no-install-recommends docker-ce-cli minisign", self.text)

    def test_the_build_needs_no_estate_artifact(self) -> None:
        self.assertNotRegex(self.text, r"(?m)^(?:COPY|ADD)\b")
        self.assertNotIn("ghcr.io/volkb79-2", self.text.replace("org.opencontainers", ""))


if __name__ == "__main__":
    unittest.main()
