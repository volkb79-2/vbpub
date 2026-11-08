"""Static checks for scripts/netcup/runner: valid shell, host-agnostic, gitignored env file."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "runner"
SCRIPTS = sorted(p for p in RUNNER.iterdir() if p.suffix == ".sh" or p.name in {"run-job", "sync-from-devcontainer"})
# Values that identify one specific host or one reinstall of it; they belong in runner.env only.
HOST_SPECIFIC = [r"r1002", r"799611", r"vxxu\.de", r"\b152\.53\.", r"SHA256:[A-Za-z0-9+/]{20,}", r"v2202511"]


def test_every_runner_script_is_valid_bash():
    assert SCRIPTS
    for script in SCRIPTS:
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_tracked_runner_files_are_host_agnostic():
    for path in RUNNER.iterdir():
        if path.is_dir() or path.name == "remote-gate.py":
            continue  # remote-gate.py is controller-side and carries the two-host allowlist by design
        text = path.read_text()
        for pattern in HOST_SPECIFIC:
            assert not re.search(pattern, text), f"{path.name} contains host-specific {pattern!r}"


def test_scripts_load_the_env_through_lib_and_guard_identity():
    for script in SCRIPTS:
        text = script.read_text()
        if script.name in {"run-job", "sync-from-devcontainer", "lib.sh", "cow-select.sh"}:
            continue  # run as the runner after provisioning (no host values), or a pure stdin-driven library sourced by provision-runner.sh
        assert "lib.sh" in text, script.name
        assert "identity_check" in text, script.name


def test_env_example_declares_every_variable_lib_requires():
    example = (RUNNER / "runner.env.example").read_text()
    lib = (RUNNER / "lib.sh").read_text()
    required = re.search(r"for _v in ([A-Z_ \\\n]+); do", lib).group(1).replace("\\", " ").split()
    for name in required:
        assert re.search(rf"^{name}=", example, re.M), name


def test_real_env_file_is_ignored_and_uid_default_matches_tester_lane_user():
    ignored = subprocess.run(["git", "check-ignore", "-q", str(RUNNER / "runner.env")], cwd=RUNNER).returncode
    assert ignored == 0
    assert re.search(r"^RUNNER_UID=1003\b", (RUNNER / "runner.env.example").read_text(), re.M)


def test_provisioning_installs_the_runner_scripts_into_the_documented_directory():
    text = (RUNNER / "provision-runner.sh").read_text()
    assert '"$RUNNER_HOME/runner-scripts"' in text and '"$here"/*.sh' in text and '"$runner_env"' in text  # ~/runner-scripts, runner.env included, mode 0600
    assert "setup-cockpit-run-gate.sh" in (RUNNER.parent / "CONSUMERS.md").read_text()
    assert "~/runner-scripts/setup-cockpit-run-gate.sh" in (RUNNER.parent / "CONSUMERS.md").read_text()
