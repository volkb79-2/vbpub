"""Static checks for scripts/netcup/runner: valid shell, host-agnostic, gitignored env file."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "runner"
SCRIPTS = sorted(p for p in RUNNER.iterdir() if p.suffix == ".sh" or p.name in {"run-job", "sync-from-devcontainer"})
START_LAUNCHERS = sorted(RUNNER.glob("start-cockpit-runner*.sh"))
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


def _docker_run_argv(launcher: Path, tmp_path: Path) -> list[str]:
    text = launcher.read_text()
    start = text.index("docker run ")
    end_marker = ' "$COCKPIT_IMAGE" sleep infinity\n'
    end = text.index(end_marker, start) + len(end_marker)
    command = text[start:end]

    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_log = tmp_path / "docker-argv.jsonl"
    fake_docker = bindir / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_ARGV_LOG'], 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DOCKER_ARGV_LOG": str(argv_log),
        "COCKPIT_NAME": "mdt-cockpit-runner",
        "COCKPIT_HOME_DIR": "/home/runner/cockpit-home",
        "COCKPIT_TMP_DIR": "/home/runner/cockpit-tmp",
        "COCKPIT_CONFIG_DIR": "/home/runner/cockpit-config",
        "COCKPIT_IMAGE": "mdt-cockpit:test",
        "INTERACTIVE_SLICE": "dev-interactive.slice",
        "BACKGROUND_SLICE": "dev-background.slice",
        "GATES_SLICE": "dev-gates.slice",
        "MDT_HOST_DOCKER_GID": "991",
        "CGPROFILE_CONTROL_GID": "992",
        "TOPOS_OBSERVE_GID": "993",
        "MDT_BUILDKIT_GID": "994",
        "RUNNER_HOME": "/home/runner",
        "COW_ROOT": "/srv/cow/mdt-cow",
        "MDT_DOCKER_API_SOCKET": "/run/docker-api/docker.sock",
        "MDT_DOCKER_API_SOCKET_DIR": "/run/docker-api",
        "CGPROFILE_CONTROL_SOCKET": "/run/cgprofile/control/cgprofile.sock",
        "CGPROFILE_CONTROL_SOCKET_DIR": "/run/cgprofile/control",
        "CGPROFILE_OBSERVER_SOCKET": "/run/cgprofile/observer/cgprofile.sock",
        "CGPROFILE_OBSERVER_SOCKET_DIR": "/run/cgprofile/observer",
        "TOPOS_OBSERVE_SOCKET": "/run/topos/observe/topos.sock",
        "TOPOS_OBSERVE_SOCKET_DIR": "/run/topos/observe",
        "MDT_BUILDKIT_SOCKET": "/run/mdt-buildkitd/buildkit.sock",
        "MDT_BUILDKIT_SOCKET_DIR": "/run/mdt-buildkitd",
    }
    prelude = ("set -Eeuo pipefail\n"
               "cfg=$COCKPIT_CONFIG_DIR\nhome=$COCKPIT_HOME_DIR\ntmpd=$COCKPIT_TMP_DIR\n")
    run = subprocess.run(["bash", "-c", prelude + command], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return json.loads(argv_log.read_text().strip())


@pytest.mark.parametrize("launcher", START_LAUNCHERS, ids=lambda path: path.name)
def test_every_cockpit_launcher_starts_the_container_with_unless_stopped_restart(launcher, tmp_path):
    argv = _docker_run_argv(launcher, tmp_path)
    assert argv[0] == "run"
    assert argv.count("--restart") == 1
    restart = argv.index("--restart")
    assert argv[restart + 1] == "unless-stopped"


def test_the_cockpit_launcher_argv_guard_is_not_vacuous():
    assert START_LAUNCHERS
