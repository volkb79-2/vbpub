"""Static checks for scripts/netcup/runner: valid shell, host-agnostic, gitignored env file."""
from __future__ import annotations

import json
import os
import re
import shutil
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


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_the_gates_policy_script_is_shellcheck_clean():
    """`bash -n` accepts an unquoted array expansion (SC2068: element `a b` splits into two units) and an assignment
    nothing reads (SC2034); this script grew both when it learned to decide per unit. The check is scoped to this
    script on purpose: other runner scripts carry their own older findings, which are not this work item's to churn."""
    subprocess.run(["shellcheck", "-x", "-P", str(RUNNER), str(RUNNER / "provision-gates-limits.sh")], check=True)


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
        if script.name == "host-sat-queue.sh":
            assert "scripts/controller/lib.sh" in text
            assert "controller_init" in text
            continue  # controller-kit queue, not a runner.env consumer
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
    # The launcher validates its values before `docker rm -f`, so the extracted command needs the same prelude lines
    # (read from the script itself, never restated here: a mutant in the validation must be observable).
    validation = text[text.index('oom_adj=${COCKPIT_OOM_SCORE_ADJ:--500}'):text.index('docker rm -f')]
    prelude = ("set -Eeuo pipefail\n"
               "cfg=$COCKPIT_CONFIG_DIR\nhome=$COCKPIT_HOME_DIR\ntmpd=$COCKPIT_TMP_DIR\n") + validation
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


def test_cockpit_launcher_protects_the_driver_with_a_negative_oom_score(tmp_path):
    """The cockpit drives the lanes, so the global OOM killer must not prefer it (paper: the 2026-11-16 host keeps its own
    control plane at -900; the cockpit takes the weaker -500). A score of 0 is the state the paper recorded as unprotected."""
    argv = _docker_run_argv(RUNNER / "start-cockpit-runner.sh", tmp_path)
    scores = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--oom-score-adj=")]
    assert scores == ["-500"]  # 0 is exactly the unprotected state the measurement recorded
    assert not any(arg == "--oom-score-adj" for arg in argv)  # the value travels with the flag, never as a loose argument


def test_cockpit_launcher_oom_score_can_be_overridden_but_not_to_an_unsafe_value(tmp_path):
    launcher = RUNNER / "start-cockpit-runner.sh"
    text = launcher.read_text()
    assert "${COCKPIT_OOM_SCORE_ADJ:--500}" in text  # one measured default, overridable by the runner env
    assert '--oom-score-adj="$oom_adj"' in text and "oom_adj=${COCKPIT_OOM_SCORE_ADJ:--500}" in text
    assert "-1000" in text and "1000" in text  # the accepted range is stated and enforced


@pytest.mark.parametrize(("value", "rc"), [
    ("-250", 0),        # a measured weaker score is allowed
    ("-1000", 0),       # the strongest allowed
    ("1000", 0),        # the weakest allowed
    ("-1001", 2),       # below the allowed range
    ("1001", 2),        # above the allowed range
    ("abc", 2),         # not an integer
])
def test_cockpit_launcher_validates_its_oom_score_override(tmp_path, value, rc):
    """The check runs on the real script text (extracted, not restated): an override outside -1000..1000 must be refused."""
    text = (RUNNER / "start-cockpit-runner.sh").read_text()
    block = text[text.index("oom_adj=${COCKPIT_OOM_SCORE_ADJ:--500}"):text.index("docker rm -f")]
    log = tmp_path / "score"
    script = f'set -Eeuo pipefail\n{block}\nprintf "%s\\n" "$oom_adj" > {str(log)}\n'
    run = subprocess.run(["bash", "-c", script], env={**os.environ, "COCKPIT_OOM_SCORE_ADJ": value}, capture_output=True, text=True)
    assert run.returncode == rc, (value, run.stdout, run.stderr)
    if rc == 0:
        assert log.read_text().strip() == value
    else:
        assert "between -1000 and 1000" in run.stderr


def test_cockpit_launcher_binds_vbpub_checkout_at_assay_workspace_path(tmp_path):
    argv = _docker_run_argv(RUNNER / "start-cockpit-runner.sh", tmp_path)
    mount = "type=bind,source=/home/runner/checkouts/vbpub,target=/workspaces/vbpub,consistency=cached"
    matches = [index for index in range(len(argv) - 1) if argv[index:index + 2] == ["--mount", mount]]
    assert len(matches) == 1


def test_the_cockpit_launcher_argv_guard_is_not_vacuous():
    assert START_LAUNCHERS


def _readme_probe_argv() -> list[str]:
    """The oom.group probe line of the runner guide, tokenised (the one `docker run` in a fenced block)."""
    text = (RUNNER / "README.md").read_text()
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("docker run ")]
    assert len(lines) == 1, lines
    return lines[0].split()


def test_the_documented_oom_group_probe_uses_an_image_the_runner_already_has():
    """Measured on v1001 (2026-11-16): the guide's probe said `alpine`, which the host did not have, so the documented
    check silently pulled an image — and a runner may have no network at all. It now names `tester-base:local`, the tag
    `build-tester-base.sh` leaves on every runner, and pins `--pull=never` so a missing image is an error instead of a fetch."""
    argv = _readme_probe_argv()
    build = (RUNNER / "build-tester-base.sh").read_text()
    assert "-t tester-base:local" in build  # the tag really is what provisioning produces
    assert "tester-base:local" in argv, argv
    assert "alpine" not in argv
    assert "--pull=never" in argv
    # the probe must be placed IN the tier and must not hide the host's view behind a namespace
    assert "--cgroup-parent" in argv and argv[argv.index("--cgroup-parent") + 1] == '"$s"'
    assert not [arg for arg in argv if arg.startswith("--cgroupns") or arg.startswith("--pid")]


def test_the_probe_is_documented_with_its_exact_removal():
    """A throwaway container in the gates tier must be removed by its exact name and never left behind, and the guide says
    when it is safe to run at all."""
    text = (RUNNER / "README.md").read_text()
    assert "--name probe-oomgroup" in text
    assert "docker rm -f probe-oomgroup" in text
    assert "only while the tier is otherwise quiet" in " ".join(text.split())
