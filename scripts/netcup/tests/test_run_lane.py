"""run-lane uses a selectable in-cockpit Run-Gate binary."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "runner"
SCRIPT = RUNNER / "run-lane.sh"
COMMIT = "a" * 40


def run_lane(tmp_path, *, run_gate_bin=None, extra_env=None):
    runner = tmp_path / "runner"
    bindir = tmp_path / "bin"
    runner.mkdir()
    bindir.mkdir()
    shutil.copy(SCRIPT, runner / "run-lane.sh")
    (runner / "lib.sh").write_text(
        'identity_check() { :; }\nCOCKPIT_NAME=mdt-cockpit-runner\n'
    )
    argv_file = tmp_path / "docker-argv.json"
    docker = bindir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "open(os.environ['DOCKER_ARGV_FILE'], 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DOCKER_ARGV_FILE": str(argv_file),
        **(extra_env or {}),
    }
    if run_gate_bin is not None:
        env["RUN_GATE_BIN"] = run_gate_bin
    result = subprocess.run(
        ["bash", str(runner / "run-lane.sh"), COMMIT, "proj", "r2", "--jobs", "2"],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, json.loads(argv_file.read_text()) if argv_file.exists() else []


def test_run_gate_binary_defaults_to_the_established_cockpit_path(tmp_path):
    result, argv = run_lane(tmp_path)
    assert result.returncode == 0, result.stderr
    assert argv[0:3] == ["exec", "-u", "vscode"]
    assert argv[argv.index("mdt-cockpit-runner") + 1:] == [
        "nice",
        "-n",
        "19",
        "/home/vscode/.local/bin/run-gate",
        "r2",
        "--jobs",
        "2",
    ]


def test_run_gate_binary_and_campaign_labels_are_selectable(tmp_path):
    result, argv = run_lane(
        tmp_path,
        run_gate_bin="/home/vscode/.venvs/gate-tools2540/bin/run-gate",
        extra_env={"RUN_GATE_JOB": "t3-demo-s2", "RUN_GATE_CAMPAIGN": "t3-demo-s2"},
    )
    assert result.returncode == 0, result.stderr
    assert "-e" in argv
    assert "RUN_GATE_JOB=t3-demo-s2" in argv
    assert "RUN_GATE_CAMPAIGN=t3-demo-s2" in argv
    assert argv[-4:] == [
        "/home/vscode/.venvs/gate-tools2540/bin/run-gate",
        "r2",
        "--jobs",
        "2",
    ]


def test_run_gate_binary_must_be_an_absolute_cockpit_path(tmp_path):
    result, argv = run_lane(tmp_path, run_gate_bin="relative/run-gate")
    assert result.returncode == 2
    assert "RUN_GATE_BIN must be an absolute path inside the cockpit" in result.stderr
    assert argv == []
