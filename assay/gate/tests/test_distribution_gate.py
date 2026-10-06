"""Ordinary regression coverage for the P24 registered-gate transformation.

`tools/tester-unified-gate.sh` itself only runs for real inside the
`tester-unified` container (the registered gate the controller owns) — this
project's own instructions are explicit that an implementer/reviewer must not
invoke it directly. These tests instead exercise the script's REAL shell
functions directly (sourced from the actual file, not reimplemented), using
real `git`/`pip`/venvs against small synthetic fixtures, so a regression in
the committed-clone topology, the hash-bound build closure, the placeholder-
version refusal, or the diagnostic/marker control flow is caught by the
ordinary suite the registered gate collects (`pytest tests -q ...`).

P24 (A-198-A-201): the four things proved here are exactly the four the
handoff calls out as invisible to a convenient-but-wrong implementation --
ignored residue entering the build, an unclosed build backend silently
reproducing the old `0.0.0` placeholder, a diagnostic rerun laundering a red
self-hosted lane into a zero exit, and a version mismatch between the
self-hosted lane's own emitted artifact and the wheel actually installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
from gate.tests.support import PROJECT_ROOT

GATE_SCRIPT = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"
DISTRIBUTION = PROJECT_ROOT / "gate" / "distribution"
LOCKED_ASSETS = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P24"
AMBIENT_TESTER_VENV_PYTHON = "/opt/tester-venv/bin/python"


def _resolve_ambient_interpreter() -> Path:
    """The real gate image always has /opt/tester-venv; this cockpit never
    does. Mirror conftest.py's own `_build_backend_home` fallback so these
    tests exercise the identical functions in both places."""
    real = Path(AMBIENT_TESTER_VENV_PYTHON)
    return real if real.exists() else Path(sys.executable)


@pytest.fixture(scope="session")
def gate_functions(tmp_path_factory) -> Path:
    """A private copy of the gate script's FUNCTION DEFINITIONS ONLY (the
    entry-point dispatch at the bottom is dropped so sourcing it never
    auto-runs outer/inner mode). Outside the real container the hardcoded
    `/opt/tester-venv/bin/python` is swapped for this session's own ambient
    interpreter -- never written back to the tracked script, only to a
    session-scoped tmp_path copy.
    """
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    marker = "# --- entry points"
    assert marker in source, "gate script no longer has the expected entry-point marker"
    body = source.split(marker, 1)[0]

    if not Path(AMBIENT_TESTER_VENV_PYTHON).exists():
        # 6 since B145: its live low-pids probe and cgroup-visible SQL runner add references to the
        # image's interpreter; `build_lint_venv` resolves the same base prefix
        # the build/run venvs are cut from, so the lint closure is built by the
        # image's own interpreter and not by whatever is first on PATH.
        occurrences = body.count(AMBIENT_TESTER_VENV_PYTHON)
        assert occurrences == 6, (
            f"expected exactly 6 uses of {AMBIENT_TESTER_VENV_PYTHON} in the "
            f"function definitions, found {occurrences}; update this test's "
            "substitution if the script changed"
        )
        body = body.replace(AMBIENT_TESTER_VENV_PYTHON, str(_resolve_ambient_interpreter()))

    out = tmp_path_factory.mktemp("gate-functions") / "gate-functions.sh"
    out.write_text(body, encoding="utf-8")
    return out


def _host_environ() -> dict[str, str]:
    """The caller's environment minus the CD50 opt-in (CD54): a shell that exports
    `ASSAY_GATE_ALLOW_SHARED_HOST` must not flip a test's result. Tests of the opt-in
    set it explicitly on the environment they pass."""
    return {k: v for k, v in os.environ.items() if k != "ASSAY_GATE_ALLOW_SHARED_HOST"}


def run_bash(
    snippet: str,
    *,
    gate_functions: Path,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    script = f"set -euo pipefail\nsource '{gate_functions}'\n{snippet}\n"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_host_environ() if env is None else env,
        timeout=timeout,  # failsafe only; no assertion depends on elapsed time
    )


def _make_synthetic_untagged_repo(root: Path) -> None:
    """A tiny repo shaped like the real monorepo: a tracked `assay/` subdir
    (pyproject.toml + src/), no tag -- mirrors the real gate's own topology
    without depending on the live vbpub tree's size or history.
    """
    (root / "assay").mkdir()
    shutil.copyfile(PROJECT_ROOT / "pyproject.toml", root / "assay" / "pyproject.toml")
    shutil.copytree(
        PROJECT_ROOT / "src",
        root / "assay" / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, timeout=30)
    subprocess.run(["git", "add", "assay"], cwd=root, check=True, timeout=30)
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay",
        "GIT_AUTHOR_EMAIL": "assay@example.invalid",
        "GIT_COMMITTER_NAME": "Assay",
        "GIT_COMMITTER_EMAIL": "assay@example.invalid",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "untagged"], cwd=root, env=identity, check=True, timeout=30
    )


# --- static checks -----------------------------------------------------------


def test_gate_script_has_valid_bash_syntax() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(GATE_SCRIPT)], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, proc.stderr


def test_gate_driver_git_wrapper_overrides_local_maintenance_settings(tmp_path: Path) -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    assert not any(line.lstrip().startswith("git ") for line in source.splitlines())
    start = source.index("assay_git() {")
    end = source.index("\n}\n", start) + 2
    helper = tmp_path / "assay-git.sh"
    helper.write_text(source[start:end] + "\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, timeout=30)
    keys = ("maintenance.auto", "maintenance.autoDetach", "gc.autoDetach")
    for key in keys:
        subprocess.run(["git", "-C", str(repo), "config", "--local", key, "true"], check=True, timeout=30)

    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; for key in maintenance.auto maintenance.autoDetach gc.autoDetach; do assay_git -C "$2" config --get "$key"; done',
            "assay-git-test",
            str(helper),
            str(repo),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["false", "false", "false"]


def test_registered_self_gate_tracks_container_by_owned_id_and_wait_exit(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The inner tester container is named, detached, logged, waited, and removed.

    A live Assay registered-gate run is still required to accept this Docker
    argv against the daemon; this fake proves construction and status plumbing.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "docker-calls.jsonl"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_CALLS'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['run', '-d']:\n"
        "    cid = os.environ.get('DOCKER_CID_VALUE', 'a' * 64)\n"
        "    pathlib.Path(os.environ['DOCKER_REMOVED_FILE']).unlink(missing_ok=True)\n"
        "    name = args[args.index('--name') + 1]\n"
        "    owner = args[args.index('--label') + 1].split('=', 1)[1]\n"
        "    pathlib.Path(os.environ['DOCKER_NAME_FILE']).write_text(name, encoding='ascii')\n"
        "    pathlib.Path(os.environ['DOCKER_OWNER_FILE']).write_text(owner, encoding='ascii')\n"
        "    if os.environ.get('DOCKER_CID_ON_FAILED_LAUNCH') == '1' or not os.environ.get('DOCKER_RUN_STATUS'):\n"
        "        pathlib.Path(args[args.index('--cidfile') + 1]).write_text(cid + '\\n', encoding='ascii')\n"
        "    print(cid)\n"
        "    if os.environ.get('DOCKER_RUN_STATUS'):\n"
        "        raise SystemExit(int(os.environ['DOCKER_RUN_STATUS']))\n"
        "elif args[:1] == ['ps']:\n"
        "    cid = next(value[3:] for value in args if value.startswith('id='))\n"
        "    removed = pathlib.Path(os.environ['DOCKER_REMOVED_FILE'])\n"
        "    if cid == os.environ.get('DOCKER_CID_VALUE', 'a' * 64) and not removed.exists():\n"
        "        name = pathlib.Path(os.environ['DOCKER_NAME_FILE']).read_text(encoding='ascii')\n"
        "        owner = os.environ.get('DOCKER_INSPECT_OWNER_OVERRIDE') or pathlib.Path(os.environ['DOCKER_OWNER_FILE']).read_text(encoding='ascii')\n"
        "        print(cid + '|' + name + '|' + owner)\n"
        "elif args[:2] == ['logs', '--follow']:\n"
        "    print('fake nested gate output')\n"
        "elif args[:1] == ['wait']:\n"
        "    print(os.environ['DOCKER_WAIT_STATUS'])\n"
        "elif args[:2] == ['rm', '-f']:\n"
        "    pathlib.Path(os.environ['DOCKER_REMOVED_FILE']).write_text(args[-1], encoding='ascii')\n"
        "else:\n"
        "    raise SystemExit(90)\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = {
        **_host_environ(),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls_path),
        "DOCKER_NAME_FILE": str(tmp_path / "tester-name.txt"),
        "DOCKER_OWNER_FILE": str(tmp_path / "tester-owner.txt"),
        "DOCKER_REMOVED_FILE": str(tmp_path / "tester-removed.txt"),
        "DOCKER_WAIT_STATUS": "17",
        "CGROUP_PARENT_DEV_BACKGROUND": "dev-background.slice",
        "ASSAY_GATE_EXPECTED_COMMIT": HEX40,
    }

    proc = run_bash(
        'run_registered_tester_container "/workspaces/vbpub/.worktrees/test" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 17, proc.stdout + proc.stderr
    assert "fake nested gate output" in proc.stdout
    assert "ASSAY_GATE_CONTAINER_EXIT=17" in proc.stdout
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    run = next(argv for argv in calls if argv[:2] == ["run", "-d"])
    assert run[2] == "--cidfile"
    assert run[3].endswith("/container.cid")
    assert run[4] == "--label"
    assert run[5].startswith("assay.gate.owner=")
    assert run[6] == "--name"
    name = run[7]
    assert name in proc.stdout
    assert "--init" in run
    assert "--cgroupns=host" in run
    assert "--cgroup-parent=dev-gates.slice" in run
    assert "CGROUP_PARENT_DEV_GATES=dev-gates.slice" in run
    assert f"ASSAY_GATE_EXPECTED_COMMIT={HEX40}" in run
    assert "CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice" in run
    assert "--network=none" in run
    assert "type=bind,src=/host/vbpub,dst=/workspaces/vbpub" in run
    assert not any("docker.sock" in value for value in run)
    assert run.index("tester-unified:local") < run.index("bash")
    container_id = "a" * 64
    assert ["logs", "--follow", container_id] in calls
    assert ["wait", container_id] in calls
    assert calls[-2] == ["ps", "--all", "--no-trunc", "--filter", f"id={container_id}", "--format", '{{.ID}}|{{.Names}}|{{.Label "assay.gate.owner"}}']
    assert calls[-1] == ["rm", "-f", container_id]

    calls_path.unlink()
    env["DOCKER_RUN_STATUS"] = "18"
    failed_launch = run_bash(
        'run_registered_tester_container "/workspaces/vbpub/.worktrees/test" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )
    assert failed_launch.returncode != 0
    failed_calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert not any(argv[:2] == ["rm", "-f"] for argv in failed_calls)

    calls_path.unlink()
    env["DOCKER_CID_ON_FAILED_LAUNCH"] = "1"
    accepted_but_transport_failed = run_bash(
        'run_registered_tester_container "/workspaces/vbpub/.worktrees/test" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )
    assert accepted_but_transport_failed.returncode != 0
    failed_calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert failed_calls[-1] == ["rm", "-f", container_id]

    calls_path.unlink()
    env["DOCKER_INSPECT_OWNER_OVERRIDE"] = "f" * 64
    foreign_cidfile = run_bash(
        'run_registered_tester_container "/workspaces/vbpub/.worktrees/test" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )
    assert foreign_cidfile.returncode != 0
    assert "resolves to unexpected container" in foreign_cidfile.stderr
    foreign_calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert not any(argv[:2] == ["rm", "-f"] for argv in foreign_calls)
    foreign_run = next(argv for argv in foreign_calls if argv[:2] == ["run", "-d"])
    foreign_cidfile_path = Path(foreign_run[3])
    assert foreign_cidfile_path.parent.exists(), "preserve a planted foreign ID for recovery"


@pytest.mark.parametrize("ps_mode", ["error", "uninterruptible"])
def test_registered_tester_log_follower_has_a_bound(
    tmp_path: Path, gate_functions: Path, ps_mode: str
) -> None:
    fake_bin = tmp_path / "log-bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "log-docker-calls.jsonl"
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_CALLS'], 'a', encoding='utf-8') as out: out.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['run', '-d']:\n"
        "    cid = 'b' * 64\n"
        "    pathlib.Path(os.environ['DOCKER_REMOVED_FILE']).unlink(missing_ok=True)\n"
        "    pathlib.Path(os.environ['DOCKER_NAME_FILE']).write_text(args[args.index('--name') + 1], encoding='ascii')\n"
        "    pathlib.Path(os.environ['DOCKER_OWNER_FILE']).write_text(args[args.index('--label') + 1].split('=', 1)[1], encoding='ascii')\n"
        "    pathlib.Path(args[args.index('--cidfile') + 1]).write_text(cid + '\\n', encoding='ascii')\n"
        "    print(cid)\n"
        "elif args[:1] == ['ps']:\n"
        "    cid = next(value[3:] for value in args if value.startswith('id='))\n"
        "    if not pathlib.Path(os.environ['DOCKER_REMOVED_FILE']).exists():\n"
        "        name = pathlib.Path(os.environ['DOCKER_NAME_FILE']).read_text(encoding='ascii')\n"
        "        owner = pathlib.Path(os.environ['DOCKER_OWNER_FILE']).read_text(encoding='ascii')\n"
        "        print(cid + '|' + name + '|' + owner)\n"
        "elif args[:2] == ['logs', '--follow']:\n"
        "    time.sleep(60)\n"
        "elif args[:1] == ['wait']:\n"
        "    print('0')\n"
        "elif args[:2] == ['rm', '-f']:\n"
        "    pathlib.Path(os.environ['DOCKER_REMOVED_FILE']).write_text(args[-1], encoding='ascii')\n"
        "else:\n"
        "    raise SystemExit(90)\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    fake_ps = fake_bin / "ps"
    fake_ps.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$PS_MODE" == error ]]; then exit 41; fi\n'
        'printf "D\\n"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_ps.chmod(0o755)
    wait_marker = tmp_path / "wait-was-called"
    env = {
        **_host_environ(),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls_path),
        "DOCKER_NAME_FILE": str(tmp_path / "log-tester-name.txt"),
        "DOCKER_OWNER_FILE": str(tmp_path / "log-tester-owner.txt"),
        "DOCKER_REMOVED_FILE": str(tmp_path / "log-tester-removed.txt"),
        "DOCKER_WAIT_STATUS": "0",
        "PS_MODE": ps_mode,
        "WAIT_MARKER": str(wait_marker),
    }
    source = gate_functions.read_text(encoding="utf-8")
    production_wait = 'wait_for_container_log_follower "$_assay_gate_logs_pid" 30'
    assert production_wait in source
    short_functions = tmp_path / "gate-functions-short-ordinary-log-wait.sh"
    short_functions.write_text(source.replace(production_wait, production_wait[:-2] + "1 1"), encoding="utf-8")

    proc = run_bash(
        'wait() { if command kill -0 "$1" 2>/dev/null; then printf "live\\n" > "$WAIT_MARKER"; else printf "exited\\n" > "$WAIT_MARKER"; fi; return 0; }\n'
        'run_registered_tester_container "/workspaces/vbpub/.worktrees/test" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=short_functions,
        env=env,
        timeout=15,
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "could not collect logs" in proc.stderr and "exit 124" in proc.stderr
    assert not wait_marker.exists() or wait_marker.read_text(encoding="utf-8") != "live\n"
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert calls[-1] == ["rm", "-f", "b" * 64]


def test_log_follower_collects_saved_wait_status_after_ps_reports_no_pid(
    tmp_path: Path, gate_functions: Path
) -> None:
    wait_marker = tmp_path / "wait-status-collected"
    proc = run_bash(
        'ps() { return 1; }\n'
        'kill() { if [[ "$1" == "-0" ]]; then return 1; fi; command kill "$@"; }\n'
        'wait() { printf "%s\\n" "$1" > "$WAIT_MARKER"; return 23; }\n'
        "wait_for_container_log_follower 12345 1",
        gate_functions=gate_functions,
        env={**_host_environ(), "WAIT_MARKER": str(wait_marker)},
    )

    assert proc.returncode == 23
    assert wait_marker.read_text(encoding="utf-8").strip() == "12345"


_FAKE_B145_ID = "d" * 64
_FAKE_B145_FOREIGN_ID = "e" * 64


def _b145_docker(fake_bin: Path) -> None:
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_CALLS'], 'a', encoding='utf-8') as out: out.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['run', '-d']:\n"
        "    name = args[args.index('--name') + 1]\n"
        "    owner = args[args.index('--label') + 1].split('=', 1)[1]\n"
        "    cidfile = pathlib.Path(args[args.index('--cidfile') + 1])\n"
        "    pathlib.Path(os.environ['FAKE_B145_NAME_FILE']).write_text(name, encoding='ascii')\n"
        "    pathlib.Path(os.environ['FAKE_B145_OWNER_FILE']).write_text(owner, encoding='ascii')\n"
        "    pathlib.Path(os.environ['FAKE_B145_CIDFILE_RECORD']).write_text(str(cidfile), encoding='utf-8')\n"
        "    pathlib.Path(os.environ['FAKE_B145_REMOVED_FILE']).unlink(missing_ok=True)\n"
        "    if os.environ.get('FAKE_B145_RUN_CONFLICT') == '1':\n"
        "        if os.environ.get('FAKE_B145_PLANT_CID') == '1': cidfile.write_text(os.environ['FAKE_B145_FOREIGN_ID'] + '\\n', encoding='ascii')\n"
        "        print('Conflict. The container name ' + chr(34) + '/' + name + chr(34) + ' is already in use', file=sys.stderr)\n"
        "        raise SystemExit(125)\n"
        "    cid = os.environ.get('FAKE_B145_ID', 'd' * 64)\n"
        "    cidfile.write_text(cid + '\\n', encoding='ascii')\n"
        "    print(cid)\n"
        "elif args[:1] == ['ps']:\n"
        "    cid = next(value[3:] for value in args if value.startswith('id='))\n"
        "    if cid == os.environ.get('FAKE_B145_ID', 'd' * 64) and not pathlib.Path(os.environ['FAKE_B145_REMOVED_FILE']).exists():\n"
        "        name = pathlib.Path(os.environ['FAKE_B145_NAME_FILE']).read_text(encoding='ascii')\n"
        "        owner = os.environ.get('FAKE_B145_INSPECT_OWNER') or pathlib.Path(os.environ['FAKE_B145_OWNER_FILE']).read_text(encoding='ascii')\n"
        "        print(cid + '|' + name + '|' + owner)\n"
        "    elif cid == os.environ.get('FAKE_B145_FOREIGN_ID', 'e' * 64):\n"
        "        name = pathlib.Path(os.environ['FAKE_B145_NAME_FILE']).read_text(encoding='ascii')\n"
        "        print(cid + '|' + name + '|' + ('f' * 64))\n"
        "elif args[:1] == ['wait']:\n"
        "    print(os.environ.get('FAKE_B145_WAIT_STATUS', '0'))\n"
        "elif args[:1] == ['logs']:\n"
        "    print('B145 bounded probe log')\n"
        "elif args[:2] == ['rm', '-f']:\n"
        "    if os.environ.get('FAKE_B145_REMOVE_FAIL_ONCE') == '1' and not pathlib.Path(os.environ['FAKE_B145_REMOVE_FAILED_FILE']).exists():\n"
        "        pathlib.Path(os.environ['FAKE_B145_REMOVE_FAILED_FILE']).touch()\n"
        "        raise SystemExit(91)\n"
        "    if args[-1] == os.environ.get('FAKE_B145_ID', 'd' * 64): pathlib.Path(os.environ['FAKE_B145_REMOVED_FILE']).touch()\n"
        "else:\n"
        "    raise SystemExit(90)\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)


def _b145_env(fake_bin: Path, tmp_path: Path, calls_path: Path) -> dict[str, str]:
    return {
        **_host_environ(),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls_path),
        "FAKE_B145_ID": _FAKE_B145_ID,
        "FAKE_B145_FOREIGN_ID": _FAKE_B145_FOREIGN_ID,
        "FAKE_B145_NAME_FILE": str(tmp_path / "b145-container-name.txt"),
        "FAKE_B145_OWNER_FILE": str(tmp_path / "b145-owner-token.txt"),
        "FAKE_B145_CIDFILE_RECORD": str(tmp_path / "b145-cidfile-path.txt"),
        "FAKE_B145_REMOVED_FILE": str(tmp_path / "b145-removed.txt"),
        "FAKE_B145_REMOVE_FAILED_FILE": str(tmp_path / "b145-remove-failed.txt"),
    }


def test_b145_probe_is_capped_placed_and_waited_before_logs(
    tmp_path: Path, gate_functions: Path
) -> None:
    fake_bin = tmp_path / "probe-bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "probe-docker-calls.log"
    _b145_docker(fake_bin)
    env = _b145_env(fake_bin, tmp_path, calls_path)

    proc = run_bash(
        'run_b145_low_pids_probe "/workspaces/vbpub/.worktrees/assay-b145" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "B145 bounded probe log" in proc.stdout
    assert "ASSAY_GATE_PHASE=b145-low-pids-accepted" in proc.stdout
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 6
    run = calls[0]
    assert run[:2] == ["run", "-d"]
    assert run[run.index("--name") + 1].startswith("run-gate-assay-b145-pids-")
    assert run[run.index("--cidfile") + 1].endswith("/container.cid")
    assert run[run.index("--label") + 1].startswith("assay.b145-probe.owner=")
    assert "--init" in run
    assert "--cgroupns=host" in run
    assert "--pids-limit=32" in run
    assert "--cgroup-parent=dev-gates.slice" in run
    assert "CGROUP_PARENT_DEV_GATES=dev-gates.slice" in run
    assert "ASSAY_B145_LOW_PIDS_PROBE=1" in run
    assert "type=bind,src=/host/vbpub,dst=/host/vbpub" in run
    assert "type=bind,src=/host/vbpub,dst=/workspaces/vbpub" in run
    assert "tester-unified:local" in run
    probe_command = run[run.index("bash") + 2]
    assert probe_command.startswith(
        "git -c maintenance.auto=false -c maintenance.autoDetach=false "
        "-c gc.autoDetach=false config --global safe.directory"
    )
    assert "test_low_pids_limit_event_cannot_become_a_kill" in run
    container_name = run[run.index("--name") + 1]
    assert calls[1][:2] == ["ps", "--all"]
    assert calls[2] == ["wait", _FAKE_B145_ID]
    assert calls[3] == ["logs", _FAKE_B145_ID]
    assert calls[4][:2] == ["ps", "--all"]
    assert calls[5] == ["rm", "-f", _FAKE_B145_ID]
    assert container_name in run


def test_b145_bounded_wait_acceptance_exercises_timeout_and_force_remove(
    tmp_path: Path, gate_functions: Path
) -> None:
    fake_bin = tmp_path / "timeout-bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "timeout-docker-calls.log"
    timeouts_path = tmp_path / "timeout-calls.log"
    _b145_docker(fake_bin)
    timeout = fake_bin / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$TIMEOUT_CALLS"\n'
        'while [[ "$1" == --* || "$1" =~ ^[0-9]+s$ ]]; do shift; done\n'
        'if [[ "$1" == docker && "$2" == wait ]]; then exit 124; fi\n'
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    env = {
        **_b145_env(fake_bin, tmp_path, calls_path),
        "TIMEOUT_CALLS": str(timeouts_path),
    }

    proc = run_bash(
        'run_b145_bounded_wait_acceptance_probe "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ASSAY_GATE_PHASE=b145-bounded-wait-accepted" in proc.stdout
    docker_calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert len(docker_calls) == 4
    run = docker_calls[0]
    assert run[:2] == ["run", "-d"]
    assert run[run.index("--name") + 1].startswith("run-gate-assay-b145-wait-")
    assert run[run.index("--cidfile") + 1].endswith("/container.cid")
    assert run[run.index("--label") + 1].startswith("assay.b145-probe.owner=")
    assert "--init" in run
    assert "--cgroupns=host" in run
    assert "--pids-limit=16" in run
    assert "--cgroup-parent=dev-gates.slice" in run
    assert "CGROUP_PARENT_DEV_GATES=dev-gates.slice" in run
    assert "type=bind,src=/host/vbpub,dst=/host/vbpub" in run
    assert "type=bind,src=/host/vbpub,dst=/workspaces/vbpub" in run
    assert "tester-unified:local" in run
    assert "sleep 60" in run
    probe_id = _FAKE_B145_ID
    assert docker_calls[1][:2] == ["ps", "--all"]
    assert docker_calls[2][:2] == ["ps", "--all"]
    assert docker_calls[3] == ["rm", "-f", probe_id]
    timeout_calls = timeouts_path.read_text(encoding="utf-8").splitlines()
    assert any(f"1s docker wait {probe_id}" in call for call in timeout_calls)
    assert any(f"20s docker rm -f {probe_id}" in call for call in timeout_calls)


def test_b145_low_pids_wait_timeout_logs_and_force_removes(
    tmp_path: Path, gate_functions: Path
) -> None:
    fake_bin = tmp_path / "timeout-bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "timeout-docker-calls.log"
    timeouts_path = tmp_path / "timeout-calls.log"
    _b145_docker(fake_bin)
    timeout = fake_bin / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$TIMEOUT_CALLS"\n'
        'while [[ "$1" == --* || "$1" =~ ^[0-9]+s$ ]]; do shift; done\n'
        'if [[ "$1" == docker && "$2" == wait ]]; then exit 124; fi\n'
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    env = {
        **_b145_env(fake_bin, tmp_path, calls_path),
        "TIMEOUT_CALLS": str(timeouts_path),
    }

    proc = run_bash(
        'run_b145_low_pids_probe "/workspaces/vbpub/.worktrees/assay-b145" '
        '"/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode != 0
    assert "exceeded 150s" in proc.stderr
    assert "B145 bounded probe log" in proc.stdout
    docker_calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert docker_calls[0][:2] == ["run", "-d"]
    assert docker_calls[1][:2] == ["ps", "--all"]
    assert docker_calls[2] == ["logs", _FAKE_B145_ID]
    assert docker_calls[3][:2] == ["ps", "--all"]
    assert docker_calls[4] == ["rm", "-f", _FAKE_B145_ID]
    timeout_calls = timeouts_path.read_text(encoding="utf-8").splitlines()
    assert any(f"150s docker wait {_FAKE_B145_ID}" in call for call in timeout_calls)
    assert any(f"20s docker rm -f {_FAKE_B145_ID}" in call for call in timeout_calls)


@pytest.mark.parametrize("plant_cid", [False, True])
def test_b145_name_conflict_never_removes_by_name_or_unverified_cid(
    tmp_path: Path, gate_functions: Path, plant_cid: bool
) -> None:
    fake_bin = tmp_path / "conflict-bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "conflict-docker-calls.jsonl"
    _b145_docker(fake_bin)
    env = _b145_env(fake_bin, tmp_path, calls_path)
    env["FAKE_B145_RUN_CONFLICT"] = "1"
    env["FAKE_B145_PLANT_CID"] = "1" if plant_cid else "0"

    proc = run_bash(
        'run_b145_bounded_wait_acceptance_probe "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode != 0
    assert "already in use" in proc.stderr
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    assert calls[0][:2] == ["run", "-d"]
    assert not any(call[:2] == ["rm", "-f"] for call in calls)
    if plant_cid:
        assert any(call[:2] == ["ps", "--all"] for call in calls)
        assert "resolves to unexpected container" in proc.stderr
    else:
        assert len(calls) == 1
        assert "refusing name-based removal" in proc.stderr
    cidfile = Path(Path(env["FAKE_B145_CIDFILE_RECORD"]).read_text(encoding="utf-8"))
    assert cidfile.parent.exists(), "retain launch evidence when Docker refuses the generated name"
    assert cidfile.exists() is plant_cid


@pytest.mark.parametrize(
    ("probe_function", "arguments", "wait_rc", "expected_error"),
    [
        (
            "run_b145_bounded_wait_acceptance_probe",
            '"/host/vbpub" "dev-gates.slice"',
            143,
            "bounded docker wait probe returned 143",
        ),
        (
            "run_b145_low_pids_probe",
            '"/workspaces/vbpub/.worktrees/assay-b145" '
            '"/host/vbpub" "dev-gates.slice"',
            124,
            "exceeded 150s",
        ),
    ],
)
def test_b145_probe_cleanup_failure_is_reported_and_exit_trap_retries(
    tmp_path: Path,
    gate_functions: Path,
    probe_function: str,
    arguments: str,
    wait_rc: int,
    expected_error: str,
) -> None:
    fake_bin = tmp_path / "retry-bin"
    fake_bin.mkdir()
    calls_path = tmp_path / "retry-docker-calls.log"
    timeouts_path = tmp_path / "retry-timeout-calls.log"
    _b145_docker(fake_bin)
    timeout = fake_bin / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$TIMEOUT_CALLS"\n'
        'while [[ "$1" == --* || "$1" =~ ^[0-9]+s$ ]]; do shift; done\n'
        'if [[ "$1" == docker && "$2" == wait ]]; then exit "$WAIT_RC"; fi\n'
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    env = {
        **_b145_env(fake_bin, tmp_path, calls_path),
        "TIMEOUT_CALLS": str(timeouts_path),
        "FAKE_B145_REMOVE_FAIL_ONCE": "1",
        "WAIT_RC": str(wait_rc),
    }

    proc = run_bash(
        f"trap cleanup_assay_gate_container EXIT\n{probe_function} {arguments}",
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode != 0
    assert expected_error in proc.stderr
    assert "was not safely removed; recovery scratch is retained" in proc.stderr
    assert "failed to remove owned B145 probe container" in proc.stderr
    if probe_function == "run_b145_low_pids_probe":
        assert "force-removed" not in proc.stderr
    docker_calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    removals = [call for call in docker_calls if call[:2] == ["rm", "-f"]]
    assert len(removals) == 2
    assert removals[0] == ["rm", "-f", _FAKE_B145_ID]
    assert removals[0] == removals[1]


def test_gate_script_passes_shellcheck_when_available() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed in this environment")
    proc = subprocess.run(
        [shellcheck, str(GATE_SCRIPT)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_gate_script_preserves_required_markers_and_hardens_the_build() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    for phase in (
        "b145-bounded-wait-accepted",
        "wheel-installed",
        "attestation-hardened",
        "self-hosted-lane-passed",
        "analysis-lane-passed",
        "independent-self-hosting-passed",
        "pyflakes-clean",
    ):
        marker = f"ASSAY_GATE_PHASE={phase}"
        assert f"echo '{marker}'" in source, f"missing required phase marker: {marker}"
    assert "echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'" in source

    # A-475/A-477: the cross-project and historical-schema phases are retired.
    # Bare substrings, anywhere in the script, not only `echo '...'` lines.
    for retired in (
        "topos-qualified",
        "cmru-b006a-qualified",
        "verdict-v5-accepted",
        "verdict-v6-v12-hard-cut-verified",
        "verdict-v13-p25-successors-verified",
        "verdict-v13-successors-verified",
        "lane-schema-v2-successors-verified",
        "qualify_topos.py",
        "qualify_cmru_b006a.py",
        "carve-assets/P33",
        "carve-assets/W",
    ):
        assert retired not in source, f"retired gate reference reappeared: {retired}"

    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]
    attestation = inner.index("ASSAY_GATE_PHASE=attestation-hardened")
    self_hosted = inner.index('run_self_hosted_lane "$worktree"')
    analysis = inner.index('run_analysis_lane "$worktree"')
    witness = inner.index('run_independent_witness "$scratch"')
    assert attestation < self_hosted < analysis < witness

    for required in (
        "--network=none",
        "--no-local",
        "--no-checkout",
        "--require-hashes",
        "--no-build-isolation",
        "--no-index",
        "--cgroup-parent=",
        "--cgroupns=host",
    ):
        assert required in source, f"missing required gate flag: {required}"

    for forbidden in ("setuptools_home", 'PYTHONPATH="$setuptools_home"'):
        assert forbidden not in source, f"stale ambient-backend route reappeared: {forbidden}"


def test_p26_installed_wheel_acceptance_has_its_test_closure_before_pytest() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]

    wheel = inner.index("ASSAY_GATE_PHASE=wheel-installed")
    closure = inner.index("write_tester_closure_pth")
    purity = inner.index("require_installed_purity")
    acceptance = inner.index("ASSAY_P26_PROJECT_ROOT")
    marker = inner.index("ASSAY_GATE_PHASE=attestation-hardened")
    self_host = inner.index("run_self_hosted_lane")

    assert wheel < closure < purity < acceptance < marker < self_host


def test_pyproject_build_system_matches_the_locked_five_pin_closure() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert tuple(pyproject["build-system"]["requires"]) == (
        "setuptools==84.0.0",
        "wheel==0.47.0",
        "setuptools-scm==10.0.5",
        "packaging==26.3",
        "vcs-versioning==2.2.4",
    )


def test_production_distribution_assets_are_byte_identical_to_locked_carve_assets() -> None:
    assert (DISTRIBUTION / "build-requirements.txt").read_bytes() == (
        LOCKED_ASSETS / "build-requirements.txt"
    ).read_bytes()
    assert (DISTRIBUTION / "build-wheelhouse-manifest.json").read_bytes() == (
        LOCKED_ASSETS / "wheelhouse-manifest.json"
    ).read_bytes()

    locked_wheels = sorted((LOCKED_ASSETS / "wheelhouse").glob("*.whl"))
    assert len(locked_wheels) == 5, locked_wheels
    for wheel in locked_wheels:
        production_wheel = DISTRIBUTION / "build-wheelhouse" / wheel.name
        assert production_wheel.read_bytes() == wheel.read_bytes()
    assert {p.name for p in (DISTRIBUTION / "build-wheelhouse").glob("*.whl")} == {
        p.name for p in locked_wheels
    }


# --- real clone / build mechanics --------------------------------------------


def test_exact_oid_clone_excludes_ignored_residue(tmp_path: Path, gate_functions: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _make_synthetic_untagged_repo(worktree)

    # Ignored residue that exists in the WORKING TREE only -- never committed,
    # exactly what a stray local build would leave behind.
    egg_info = worktree / "assay" / "src" / "assay.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_bytes(b"residue")
    pycache = worktree / "assay" / "src" / "assay" / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "config.cpython-314.pyc").write_bytes(b"residue")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"\n'
        f'echo "CLONE_HEAD=$(git -C "{scratch}/clone" rev-parse HEAD)"',
        gate_functions=gate_functions,
    )
    assert proc.returncode == 0, proc.stderr

    expected_oid = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert f"CLONE_HEAD={expected_oid}" in proc.stdout

    clone_assay = scratch / "clone" / "assay"
    assert clone_assay.is_dir()
    assert not (clone_assay / "src" / "assay.egg-info").exists()
    assert not list((clone_assay / "src" / "assay").rglob("__pycache__"))


def test_clone_head_mismatch_is_a_hard_failure(tmp_path: Path, gate_functions: Path) -> None:
    """A worktree whose HEAD cannot be resolved (e.g. no commits yet) must
    refuse rather than silently clone something else."""
    worktree = tmp_path / "empty-worktree"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=worktree, check=True, timeout=30)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"', gate_functions=gate_functions
    )
    assert proc.returncode != 0


def test_closure_build_produces_a_real_non_placeholder_dev_identity(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _make_synthetic_untagged_repo(worktree)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"\n'
        f'build_offline_closure_venvs "{scratch}" "{DISTRIBUTION}"\n'
        f'wheel="$(build_one_wheel "{scratch}")"\n'
        f'version="$(require_real_wheel_version "{scratch}" "$wheel")"\n'
        f'echo "VERSION=$version"\n',
        gate_functions=gate_functions,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    version_line = next(line for line in proc.stdout.splitlines() if line.startswith("VERSION="))
    version = version_line.split("=", 1)[1]
    assert version not in {"0.0.0", "0+unknown"}
    assert ".dev" in version and "+g" in version  # a real setuptools-scm dev identity


def test_ambient_only_build_without_the_closure_is_refused_as_a_placeholder(
    tmp_path: Path, gate_functions: Path
) -> None:
    """Reviewer attack: backend present ambiently + missing locked plugin --
    an ambient fallback must not save the build; the wheel it produces (the
    same documented `0.0.0` gap, A-069) must be refused, not silently shipped.
    """
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _make_synthetic_untagged_repo(worktree)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ambient = _resolve_ambient_interpreter()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"\n'
        f'"{ambient}" -m venv "{scratch}/build-venv"\n'
        f'"{scratch}/build-venv/bin/python" -m pip install --quiet '
        f'--no-index --find-links "{DISTRIBUTION}/build-wheelhouse" '
        f'"setuptools==84.0.0" "wheel==0.47.0"\n'
        f'wheel="$(build_one_wheel "{scratch}")"\n'
        f'require_real_wheel_version "{scratch}" "$wheel"\n',
        gate_functions=gate_functions,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "placeholder" in proc.stderr


# --- self-hosted lane: markers and diagnostic-laundering resistance --------


def test_self_hosted_lane_failure_is_never_laundered_into_success(
    tmp_path: Path, gate_functions: Path
) -> None:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    stub_assay = stub_dir / "assay"
    stub_assay.write_text("#!/usr/bin/env bash\necho 'stub assay: deliberately failing' >&2\nexit 7\n")
    stub_assay.chmod(0o755)
    stub_python = stub_dir / "python"
    stub_python.write_text(
        "#!/usr/bin/env bash\necho 'stub diagnostic rerun (also fails)' >&2\nexit 3\n"
    )
    stub_python.chmod(0o755)

    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    wheel = tmp_path / "assay-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"not really a wheel, but a real file to hash")

    env = {**_host_environ(), "PATH": f"{stub_dir}:{os.environ['PATH']}"}
    proc = run_bash(
        f'run_self_hosted_lane "{worktree}" "{scratch}" "9.9.9" "{wheel}"',
        gate_functions=gate_functions,
        env=env,
    )
    assert proc.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in proc.stdout
    assert "ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red" in proc.stderr


def _run_analysis_lane(tmp_path: Path, gate_functions: Path, *, stub_body: str):
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(parents=True)
    stub_assay = stub_dir / "assay"
    stub_assay.write_text("#!/usr/bin/env bash\n" + stub_body)
    stub_assay.chmod(0o755)
    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return run_bash(
        f'run_analysis_lane "{worktree}" "{scratch}"',
        gate_functions=gate_functions,
        env={**_host_environ(), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )


def test_analysis_lane_failure_is_never_laundered_into_success(
    tmp_path: Path, gate_functions: Path
) -> None:
    proc = _run_analysis_lane(
        tmp_path, gate_functions, stub_body="echo 'stub assay: deliberately failing' >&2\nexit 7\n"
    )
    assert proc.returncode != 0
    assert "ASSAY_GATE_PHASE=analysis-lane-passed" not in proc.stdout
    assert "ASSAY_GATE_DIAGNOSTIC=analysis-lane-red" in proc.stderr


def test_analysis_lane_marker_needs_the_lane_and_the_verifier_to_pass(
    tmp_path: Path, gate_functions: Path
) -> None:
    # `assay run analysis` passes; `assay verify` then refuses the verdict.
    refused = _run_analysis_lane(
        tmp_path / "refused",
        gate_functions,
        stub_body='[ "$1" = verify ] && exit 9\nexit 0\n',
    )
    assert refused.returncode != 0
    assert "ASSAY_GATE_PHASE=analysis-lane-passed" not in refused.stdout

    accepted = _run_analysis_lane(tmp_path / "accepted", gate_functions, stub_body="exit 0\n")
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.count("ASSAY_GATE_PHASE=analysis-lane-passed") == 1


def test_self_hosted_lane_requires_the_emitted_version_to_match_the_installed_one(
    tmp_path: Path, gate_functions: Path
) -> None:
    fixture = _self_hosted_lane_fixture(tmp_path, version="9.9.9")

    matching = run_bash(
        fixture.invocation(version="9.9.9"),
        gate_functions=gate_functions,
        env=fixture.env,
    )
    assert matching.returncode == 0, matching.stderr
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" in matching.stdout

    mismatched = run_bash(
        fixture.invocation(version="1.0.0"),
        gate_functions=gate_functions,
        env=fixture.env,
    )
    assert mismatched.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in mismatched.stdout
    assert "emitted assay_version" in mismatched.stderr


@dataclass(frozen=True)
class _SelfHostedLaneFixture:
    worktree: Path
    scratch: Path
    wheel: Path
    env: dict

    def invocation(self, *, version: str, wheel: Path | None = None) -> str:
        return (
            f'run_self_hosted_lane "{self.worktree}" "{self.scratch}" '
            f'"{version}" "{wheel or self.wheel}"'
        )


#: Shell preamble every `assay` stub in this module shares: resolve the
#: verdict path the way the real CLI does — the token after `--verdict-json` —
#: instead of by argv position. See `_self_hosted_lane_fixture`'s docstring.
_VERDICT_PATH_FROM_ARGV = (
    'out=""; prev=""\n'
    'for arg in "$@"; do\n'
    '  if [ "$prev" = "--verdict-json" ]; then out="$arg"; fi\n'
    '  prev="$arg"\n'
    "done\n"
    'if [ -z "$out" ]; then echo "stub: no --verdict-json in argv" >&2; exit 64; fi\n'
)


def _self_hosted_lane_fixture(
    tmp_path: Path, *, version: str, digest: str | None = None
) -> _SelfHostedLaneFixture:
    """A stub `assay` that writes a COMPLETE self-hosted-lane verdict.

    B018/A-327: the stub must now emit `judge_provenance` too, and by default
    it emits the honest one -- the real sha256 of the wheel file this fixture
    writes -- so `require_emitted_judge_provenance`'s positive branch is
    exercised by a value the stub did not simply echo back from the gate.
    *digest* overrides it to drive the refusal branch.

    The stub finds its output path by READING the argv for `--verdict-json`
    rather than by position. It used to write to `$5`, with a note here saying
    so — and that note had to move once already when
    `--require-judge-provenance` was added, and would have moved again when
    A-429 added `--resume --progress`. A stub whose contract is "the value
    after `--verdict-json`" is the same contract the real CLI has, and it does
    not break every time the gate's invocation grows a flag.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(exist_ok=True)
    wheel = tmp_path / f"assay-{version}-py3-none-any.whl"
    wheel.write_bytes(f"a real file to hash, for {version}".encode())
    recorded = digest or hashlib.sha256(wheel.read_bytes()).hexdigest()

    document = json.dumps(
        {
            "assay_version": version,
            "judge_provenance": {
                "name": "assay",
                "version": version,
                "artifact": "wheel",
                "digest_algorithm": "sha256",
                "digest": recorded,
            },
        }
    )
    stub_assay = stub_dir / "assay"
    stub_assay.write_text(
        "#!/usr/bin/env bash\n"
        + _VERDICT_PATH_FROM_ARGV
        + f"printf '%s' {shlex.quote(document)} > \"$out\"\n"
        "exit 0\n"
    )
    stub_assay.chmod(0o755)

    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True, exist_ok=True)
    scratch = tmp_path / "scratch"
    run_venv_bin = scratch / "run-venv" / "bin"
    run_venv_bin.mkdir(parents=True, exist_ok=True)
    python = run_venv_bin / "python"
    if not python.exists():
        python.symlink_to(sys.executable)

    return _SelfHostedLaneFixture(
        worktree=worktree,
        scratch=scratch,
        wheel=wheel,
        env={**_host_environ(), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )


def test_self_hosted_lane_binds_the_recorded_digest_to_the_installed_wheel(
    tmp_path: Path, gate_functions: Path
) -> None:
    """**B018/A-327's gate-level oracle.** The gate hashes the wheel itself,
    with `sha256sum`, on the host side; the artifact's recorded digest must
    equal that number. Both halves are asserted here differentially: the
    honest stub passes, and a stub recording any other digest -- a plausible
    64-hex one, not obvious garbage -- is refused naming both values."""
    honest = _self_hosted_lane_fixture(tmp_path / "honest", version="9.9.9")
    good = run_bash(
        honest.invocation(version="9.9.9"), gate_functions=gate_functions, env=honest.env
    )
    assert good.returncode == 0, good.stderr
    assert "ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel" in good.stdout

    forged = _self_hosted_lane_fixture(
        tmp_path / "forged", version="9.9.9", digest="ab" * 32
    )
    bad = run_bash(
        forged.invocation(version="9.9.9"), gate_functions=gate_functions, env=forged.env
    )
    assert bad.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in bad.stdout
    assert "the installed wheel's own sha256" in bad.stderr


def test_self_hosted_lane_refuses_a_verdict_carrying_no_judge_identity(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The absence half. An installed wheel always identifies itself, so a
    self-hosted verdict without `judge_provenance` means the lane did not run
    the wheel this gate built -- which is the one thing self-hosting exists to
    prove."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    stub_assay = stub_dir / "assay"
    stub_assay.write_text(
        "#!/usr/bin/env bash\n"
        + _VERDICT_PATH_FROM_ARGV
        + 'printf \'{"assay_version": "9.9.9"}\' > "$out"\nexit 0\n'
    )
    stub_assay.chmod(0o755)
    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    run_venv_bin = scratch / "run-venv" / "bin"
    run_venv_bin.mkdir(parents=True)
    (run_venv_bin / "python").symlink_to(sys.executable)
    wheel = tmp_path / "assay-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"a real file to hash")

    proc = run_bash(
        f'run_self_hosted_lane "{worktree}" "{scratch}" "9.9.9" "{wheel}"',
        gate_functions=gate_functions,
        env={**_host_environ(), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )
    assert proc.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in proc.stdout
    assert "emitted no judge_provenance" in proc.stderr


def test_the_self_hosted_lane_demands_the_judge_identity_from_assay_itself(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The gate does not merely CHECK the recorded identity afterwards; it
    asks for it up front, with the same flag a CIU V8 gate request would
    pass. Asserted on the invocation the stub actually receives, never read
    off the script's source."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    argv_log = tmp_path / "argv.log"
    stub_assay = stub_dir / "assay"
    stub_assay.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$@" > "{argv_log}"\n'
        + _VERDICT_PATH_FROM_ARGV
        + 'printf \'{"assay_version": "9.9.9"}\' > "$out"\n'
        "exit 0\n"
    )
    stub_assay.chmod(0o755)
    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    (scratch / "run-venv" / "bin").mkdir(parents=True)
    (scratch / "run-venv" / "bin" / "python").symlink_to(sys.executable)
    wheel = tmp_path / "assay-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"a real file to hash")

    run_bash(
        f'run_self_hosted_lane "{worktree}" "{scratch}" "9.9.9" "{wheel}"',
        gate_functions=gate_functions,
        env={**_host_environ(), "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )
    assert argv_log.read_text().splitlines() == [
        "run",
        "tester-unified",
        "--require-judge-provenance",
        # (A-429) the estate-wide pair, on the one `assay run` run-gate does
        # not drive. Asserted on the invocation the stub RECEIVED, so this is
        # the flags actually reaching assay, not a substring of the script.
        "--resume",
        "--progress",
        f"{scratch}/progress-tester-unified.jsonl",
        "--verdict-json",
        f"{scratch}/verdict.json",
    ]


# --- B024/DA-R7: the pyflakes lint phase and its own hash-bound closure -----


LINT_WHEELHOUSE = DISTRIBUTION / "lint-wheelhouse"
LINT_REQUIREMENTS = DISTRIBUTION / "lint-requirements.txt"
LINT_MANIFEST = DISTRIBUTION / "lint-wheelhouse-manifest.json"


@pytest.fixture(scope="session")
def lint_venv(tmp_path_factory, gate_functions: Path) -> Path:
    """A real `lint-venv` built by the gate's OWN `build_lint_venv`, from the
    committed offline wheelhouse with `--require-hashes`. Built once per
    session because every assertion below wants the same closure the gate
    installs, not a pip-resolved approximation of it."""
    scratch = tmp_path_factory.mktemp("lint-scratch")
    proc = run_bash(
        f'build_lint_venv "{scratch}" "{DISTRIBUTION}"',
        gate_functions=gate_functions,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return scratch


def test_lint_requirements_pin_the_wheels_that_are_actually_committed() -> None:
    """The pin, the manifest and the bytes on disk must agree. A wheelhouse
    whose hash line does not match its own file is a closure that will only
    fail inside the network-less container, where nobody can fetch a fix."""
    lines = [
        line.strip()
        for line in LINT_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == 1, lines
    assert lines[0].startswith("pyflakes==3.4.0 --hash=sha256:"), lines[0]

    manifest = json.loads(LINT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    entries = manifest["requirements"]
    assert len(entries) == 1, entries

    wheels = sorted(LINT_WHEELHOUSE.glob("*.whl"))
    assert [w.name for w in wheels] == [entries[0]["filename"]]

    payload = wheels[0].read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == entries[0]["sha256"]
    assert len(payload) == entries[0]["size"]
    assert f"--hash=sha256:{digest}" in LINT_REQUIREMENTS.read_text(encoding="utf-8")


def test_the_lint_closure_is_a_third_venv_and_never_the_build_or_run_venv() -> None:
    """A-198's five-wheel build closure is an assertion about what can enter
    the wheel. A linter is neither a build input nor a runtime dependency, so
    it gets its own venv and the build closure's assertion stays exactly what
    it was."""
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    lint_fn = source.split("build_lint_venv() {", 1)[1].split("\n}\n", 1)[0]

    assert "$scratch/lint-venv" in lint_fn
    assert "build-venv" not in lint_fn
    assert "run-venv" not in lint_fn
    assert "--require-hashes" in lint_fn
    assert "--no-index" in lint_fn
    assert "lint-wheelhouse" in lint_fn

    build_fn = source.split("build_offline_closure_venvs() {", 1)[1].split("\n}\n", 1)[0]
    assert "pyflakes" not in build_fn
    assert "lint" not in build_fn

    run_fn = source.split("run_lint_phase() {", 1)[1].split("\n}\n", 1)[0]
    assert "$scratch/lint-venv/bin/python" in run_fn
    # The judged bytes are the private exact-OID clone's, never the caller's
    # bind-mounted worktree.
    assert "$scratch/clone/assay/src/assay" in run_fn
    # (B062) `tests/` joined the scope, and `tests/fixtures/` is pruned out of
    # it by name -- both are asserted here so a future edit cannot quietly
    # drop either half back to B024's narrow scope.
    assert "$scratch/clone/assay/tests" in run_fn
    assert "$scratch/clone/assay/tests/fixtures" in run_fn
    assert "-prune" in run_fn
    # (B123) `gate/tests` is its own array, so an empty expansion is refused.
    assert "$scratch/clone/assay/gate/tests" in run_fn


def test_the_lint_phase_runs_after_the_suite_and_marks_itself() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "echo 'ASSAY_GATE_PHASE=pyflakes-clean'" in source

    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]
    self_host = inner.index("run_self_hosted_lane")
    witness = inner.index("run_independent_witness")
    lint = inner.index("run_lint_phase")
    assert self_host < witness < lint


def _seed_clean_tests_tree(scratch: Path) -> Path:
    """(B062) The lint phase now lints `tests/` too and refuses a clone that
    has none, so every scratch clone in this module needs one. Returns the
    directory so a caller can plant into it."""
    tests = scratch / "clone" / "assay" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_seed.py").write_text(
        "def test_seed() -> None:\n    assert True\n", encoding="utf-8"
    )
    _seed_clean_analysis_tree(scratch)
    _seed_clean_gate_tests_tree(scratch)
    return tests


def _seed_clean_gate_tests_tree(scratch: Path) -> Path:
    """(B123) The lint phase also lints `gate/tests/` and refuses a clone that has
    none. Returns the seeded file so a caller can plant into it."""
    gate_tests = scratch / "clone" / "assay" / "gate" / "tests"
    gate_tests.mkdir(parents=True)
    seed = gate_tests / "test_gate_seed.py"
    seed.write_text("def test_gate_seed() -> None:\n    assert True\n", encoding="utf-8")
    return seed


def _seed_clean_analysis_tree(scratch: Path) -> Path:
    """(A-478) The lint phase also lints `analysis/` and refuses a clone that has
    none. Returns the seeded source file so a caller can plant into it."""
    analysis = scratch / "clone" / "assay" / "analysis" / "src" / "assay_analysis"
    analysis.mkdir(parents=True)
    seed = analysis / "cli.py"
    seed.write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    return seed


def test_a_planted_unused_import_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """The whole point of the phase. A clean tree passes and emits the marker
    exactly once; the same tree with one unused import fails, names the file
    and line, and emits NO marker -- a phase that cannot go red is wiring, not
    a gate."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    module = package / "config.py"
    module.write_text("import json\n\n\ndef load(text: str) -> object:\n    return json.loads(text)\n", encoding="utf-8")
    _seed_clean_tests_tree(scratch)
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    clean = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert clean.returncode == 0, clean.stderr
    assert clean.stdout.count("ASSAY_GATE_PHASE=pyflakes-clean") == 1

    module.write_text(
        "import json\nimport os\n\n\ndef load(text: str) -> object:\n    return json.loads(text)\n",
        encoding="utf-8",
    )
    planted = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert planted.returncode != 0
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in planted.stdout
    assert "'os' imported but unused" in planted.stdout + planted.stderr
    assert "config.py" in planted.stdout + planted.stderr


def test_an_undefined_name_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """pyflakes' whole rule set is the F-rule set, and the rule that pays for
    the phase is F821: a name that does not exist at runtime, which a test
    suite only catches on the branch that executes it."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "errors.py").write_text(
        "def refuse(reason: str) -> str:\n"
        "    if reason == 'x':\n"
        "        return REASON_TABLE[reason]\n"
        "    return reason\n",
        encoding="utf-8",
    )
    _seed_clean_tests_tree(scratch)
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert proc.returncode != 0
    assert "undefined name 'REASON_TABLE'" in proc.stdout + proc.stderr


def test_a_planted_unused_import_in_a_TEST_module_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """(B062) The proof the widened scope is real and not decorative: the
    identical plant, in `tests/` instead of `src/assay`, has to redden the
    phase too. Before B062 this passed, because the phase never looked."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    tests = _seed_clean_tests_tree(scratch)
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    clean = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert clean.returncode == 0, clean.stderr
    assert clean.stdout.count("ASSAY_GATE_PHASE=pyflakes-clean") == 1

    (tests / "test_seed.py").write_text(
        "import os\n\n\ndef test_seed() -> None:\n    assert True\n", encoding="utf-8"
    )
    planted = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert planted.returncode != 0
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in planted.stdout
    assert "'os' imported but unused" in planted.stdout + planted.stderr
    assert "test_seed.py" in planted.stdout + planted.stderr


def test_a_planted_unused_import_in_the_analysis_package_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """(A-478) `analysis/` is inside the lint scope: the identical plant, in
    `analysis/src/assay_analysis` instead of `src/assay`, reddens the phase."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _seed_clean_tests_tree(scratch)
    seed = scratch / "clone" / "assay" / "analysis" / "src" / "assay_analysis" / "cli.py"
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    clean = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert clean.returncode == 0, clean.stderr
    assert clean.stdout.count("ASSAY_GATE_PHASE=pyflakes-clean") == 1

    seed.write_text("import os\n\n\ndef main() -> int:\n    return 0\n", encoding="utf-8")
    planted = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert planted.returncode != 0
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in planted.stdout
    assert "'os' imported but unused" in planted.stdout + planted.stderr
    assert "assay_analysis/cli.py" in planted.stdout + planted.stderr


def test_a_planted_unused_import_in_a_gate_tests_module_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """(B123) `gate/tests/` is inside the lint scope: the identical plant, in
    `gate/tests` instead of `tests`, reddens the phase."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _seed_clean_tests_tree(scratch)
    seed = scratch / "clone" / "assay" / "gate" / "tests" / "test_gate_seed.py"
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    clean = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert clean.returncode == 0, clean.stderr
    assert clean.stdout.count("ASSAY_GATE_PHASE=pyflakes-clean") == 1

    seed.write_text("import os\n\n\ndef test_gate_seed() -> None:\n    assert True\n", encoding="utf-8")
    planted = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert planted.returncode != 0
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in planted.stdout
    assert "'os' imported but unused" in planted.stdout + planted.stderr
    assert "test_gate_seed.py" in planted.stdout + planted.stderr


def test_a_clone_with_no_gate_tests_tree_refuses_rather_than_linting_nothing(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _seed_clean_tests_tree(scratch)
    shutil.rmtree(scratch / "clone" / "assay" / "gate")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert proc.returncode != 0
    assert "lint phase found no gate/tests sources to lint" in proc.stdout + proc.stderr
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in proc.stdout


def test_a_clone_with_no_analysis_tree_refuses_rather_than_linting_nothing(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _seed_clean_tests_tree(scratch)
    shutil.rmtree(scratch / "clone" / "assay" / "analysis")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert proc.returncode != 0
    assert "lint phase found no analysis sources to lint" in proc.stdout + proc.stderr
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in proc.stdout


def test_the_fixtures_tree_is_pruned_and_an_unparseable_fixture_stays_green(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """(B062) `tests/fixtures/mutation/python/broken.py` is DELIBERATELY
    unparseable -- the mutation suite needs it to prove how assay reports a
    source file it cannot parse -- so pyflakes can never pass over it. The
    prune is what makes widening the scope possible at all; without it the
    phase would be permanently red on a file whose brokenness is the point.

    The fixture planted here is byte-identical in KIND to the real one (a
    `def` with a syntax error), so this test measures the prune rather than
    a hand-picked file's happenstance."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    tests = _seed_clean_tests_tree(scratch)
    broken = tests / "fixtures" / "mutation" / "python"
    broken.mkdir(parents=True)
    (broken / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    # A plain unused import in the same pruned tree: the prune is a scope
    # decision, not a syntax-error exemption.
    (broken / "unused.py").write_text("import os\n", encoding="utf-8")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("ASSAY_GATE_PHASE=pyflakes-clean") == 1
    assert "invalid syntax" not in proc.stdout + proc.stderr


def test_a_clone_with_no_tests_tree_refuses_rather_than_linting_nothing(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """(B062) The failure mode a `find`-built file list invites: if `tests/`
    is missing or renamed, an empty expansion would lint `src/assay` alone
    and still emit the clean marker -- the widened scope silently gone. The
    phase refuses instead."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert proc.returncode != 0
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in proc.stdout


def test_the_shipped_source_tree_is_pyflakes_clean(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """The gate's own assertion, brought forward into the ordinary suite so a
    finding is visible in `pytest tests` instead of only after a nine-minute
    container run. Runs the identical locked pyflakes over the identical
    package the gate lints.

    (B062) `tests/` is symlinked in alongside `src/assay`, so this is now the
    in-suite guard for BOTH trees -- a new unused import in a test module
    turns this red in seconds instead of in the container."""
    scratch = tmp_path / "scratch"
    (scratch / "clone" / "assay" / "src").mkdir(parents=True)
    (scratch / "clone" / "assay" / "src" / "assay").symlink_to(PROJECT_ROOT / "src" / "assay")
    (scratch / "clone" / "assay" / "tests").symlink_to(PROJECT_ROOT / "tests")
    (scratch / "clone" / "assay" / "analysis").symlink_to(PROJECT_ROOT / "analysis")
    (scratch / "clone" / "assay" / "gate").mkdir()
    (scratch / "clone" / "assay" / "gate" / "tests").symlink_to(PROJECT_ROOT / "gate" / "tests")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_self_hosted_lane_is_invoked_with_resume_and_progress() -> None:
    """(A-429) The 2026-09-02 operator directive, now estate policy: EVERY
    ``assay run`` in the estate passes ``--resume --progress <path>``
    (vbpub ``AGENTS.md``; run-gate SPEC R-38 / RG-33, run-gate rev 33).

    run-gate appends both to every assay-kind lane it drives. This gate calls
    ``assay run`` itself, so it is the one invocation in the estate a
    run-gate change cannot reach — which is exactly why it is asserted here
    rather than assumed. Both flags are no-ops on this R0 lane by assay's own
    contract, and that is the point: the shape is uniform whether or not a
    lane has anything to checkpoint.

    The progress path must stay under the gate's ``$scratch``. A progress file
    written into the worktree would be an untracked path in the tree the lane
    is judging, i.e. a self-inflicted ``DIRTY_TREE``.
    """
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    invocation = source.split("assay run tester-unified", 1)[1].split("; then", 1)[0]

    assert "--resume" in invocation, invocation
    assert '--progress "$scratch/progress-tester-unified.jsonl"' in invocation, (
        invocation
    )
    assert "--require-judge-provenance" in invocation, invocation
    assert '--verdict-json "$scratch/verdict.json"' in invocation, invocation
    # Exactly one such invocation: a second, unflagged one would defeat this.
    assert source.count("assay run tester-unified") == 1, source.count(
        "assay run tester-unified"
    )


# --- S1: the same-commit receipt and the gate-entry host check (B123, CD32) ----

RECEIPT_RELATIVE = Path("assay") / ".assay" / "registered-gate" / "tester-unified.json"
HEX40 = "0123456789abcdef0123456789abcdef01234567"


def _receipt_worktree(tmp_path: Path) -> tuple[Path, str, str]:
    """A throwaway git worktree with one commit: (path, commit, tree)."""
    worktree = tmp_path / "wt"
    (worktree / "assay").mkdir(parents=True)
    (worktree / "assay" / "file.txt").write_text("one\n", encoding="utf-8")
    _git_commit(worktree, "one", init=True)
    return worktree, _rev(worktree, "HEAD"), _rev(worktree, "HEAD^{tree}")


def _git_commit(worktree: Path, message: str, *, init: bool = False) -> None:
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay",
        "GIT_AUTHOR_EMAIL": "assay@example.invalid",
        "GIT_COMMITTER_NAME": "Assay",
        "GIT_COMMITTER_EMAIL": "assay@example.invalid",
    }
    if init:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=worktree, check=True, timeout=30)
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True, timeout=30)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message], cwd=worktree, env=identity, check=True, timeout=30
    )


def _rev(worktree: Path, spec: str) -> str:
    return subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", spec], check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def _docker_stub(tmp_path: Path, ps_output: str) -> tuple[dict[str, str], Path]:
    """A PATH stub `docker`: it logs its argv, and `ps` prints ``ps_output``.
    Anything but `ps` is a hard failure, so a test sees a stray call."""
    fake_bin = tmp_path / "stub-bin"
    fake_bin.mkdir()
    log = tmp_path / "docker-calls.log"
    stub = fake_bin / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_STUB_LOG"\n'
        'if [[ "$1" == ps ]]; then printf "%s" "$DOCKER_STUB_PS"; exit 0; fi\n'
        "exit 91\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        **_host_environ(),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_STUB_LOG": str(log),
        "DOCKER_STUB_PS": ps_output,
    }
    return env, log


def test_the_receipt_is_the_exact_four_key_document_with_mode_0644(tmp_path: Path, gate_functions: Path) -> None:
    worktree = tmp_path / "wt"
    tree = "f" * 64  # a sha256-format object name is accepted too

    proc = run_bash(
        f'write_registered_gate_receipt "{worktree}" "{HEX40}" "{tree}"', gate_functions=gate_functions
    )

    assert proc.returncode == 0, proc.stderr
    receipt = worktree / RECEIPT_RELATIVE
    assert receipt.read_text(encoding="utf-8") == (
        f'{{"schema_version": 1, "lane": "tester-unified", "commit": "{HEX40}", "tree": "{tree}"}}\n'
    )
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "lane": "tester-unified",
        "commit": HEX40,
        "tree": tree,
    }
    assert (receipt.stat().st_mode & 0o777) == 0o644
    assert sorted(p.name for p in receipt.parent.iterdir()) == ["tester-unified.json"]


@pytest.mark.parametrize(
    ("commit", "tree"),
    [
        (HEX40.upper(), HEX40),
        (HEX40, HEX40.upper()),
        (HEX40[:39], HEX40),
        (HEX40, HEX40[:39]),
        (HEX40 + "0" * 23, HEX40),
        (HEX40, HEX40 + "0" * 25),
        ("", HEX40),
        ("main", HEX40),
    ],
)
def test_a_malformed_object_id_writes_no_receipt(tmp_path: Path, gate_functions: Path, commit: str, tree: str) -> None:
    worktree = tmp_path / "wt"

    proc = run_bash(
        f'write_registered_gate_receipt "{worktree}" "{commit}" "{tree}"', gate_functions=gate_functions
    )

    assert proc.returncode != 0
    assert not (worktree / "assay").exists()


def test_clearing_the_receipt_removes_it_and_tolerates_none(tmp_path: Path, gate_functions: Path) -> None:
    worktree = tmp_path / "wt"
    receipt = worktree / RECEIPT_RELATIVE
    receipt.parent.mkdir(parents=True)
    receipt.write_text("stale\n", encoding="utf-8")

    first = run_bash(f'clear_registered_gate_receipt "{worktree}"', gate_functions=gate_functions)
    second = run_bash(f'clear_registered_gate_receipt "{worktree}"', gate_functions=gate_functions)

    assert first.returncode == 0 and second.returncode == 0
    assert not receipt.exists()


def test_finishing_a_green_gate_writes_the_receipt_then_one_complete_marker(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)

    proc = run_bash(f'finish_registered_gate "{worktree}" "{commit}" "{tree}"', gate_functions=gate_functions)

    assert proc.returncode == 0, proc.stderr
    receipt = worktree / RECEIPT_RELATIVE
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "lane": "tester-unified",
        "commit": commit,
        "tree": tree,
    }
    assert proc.stdout.splitlines() == [
        f"ASSAY_REGISTERED_GATE_RECEIPT={receipt}",
        "ASSAY_REGISTERED_GATE_COMPLETE=1",
    ]


def test_finishing_after_head_moved_writes_no_receipt_and_no_complete_marker(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    (worktree / "assay" / "file.txt").write_text("two\n", encoding="utf-8")
    _git_commit(worktree, "two")

    proc = run_bash(f'finish_registered_gate "{worktree}" "{commit}" "{tree}"', gate_functions=gate_functions)

    assert proc.returncode != 0
    assert "HEAD changed during the registered gate; no receipt" in proc.stderr
    assert not (worktree / RECEIPT_RELATIVE).exists()
    assert "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT" not in proc.stdout


def test_finishing_with_a_different_tree_at_the_same_head_is_refused(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, _tree = _receipt_worktree(tmp_path)

    proc = run_bash(f'finish_registered_gate "{worktree}" "{commit}" "{HEX40}"', gate_functions=gate_functions)

    assert proc.returncode != 0
    assert not (worktree / RECEIPT_RELATIVE).exists()
    assert "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout


def test_a_red_container_leaves_no_receipt_even_when_a_stale_one_existed(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    stale = worktree / RECEIPT_RELATIVE
    stale.parent.mkdir(parents=True)
    stale.write_text("a receipt from an earlier green run\n", encoding="utf-8")
    env, log = _docker_stub(tmp_path, "")

    proc = run_bash(
        "run_registered_tester_container() { return 7; }\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 7, proc.stdout + proc.stderr
    assert not stale.exists()
    assert "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_RECEIPT" not in proc.stdout


def test_a_red_container_that_wrote_a_valid_receipt_itself_leaves_none(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The container has the worktree bind-mounted read-write, so code under test
    can write a perfectly valid receipt; a red exit must still leave none."""
    worktree, commit, tree = _receipt_worktree(tmp_path)
    env, log = _docker_stub(tmp_path, "")

    proc = run_bash(
        'run_registered_tester_container() { write_registered_gate_receipt "$1" '
        '"$(git -C "$1" rev-parse HEAD)" "$(git -C "$1" rev-parse "HEAD^{tree}")"; return 7; }\n'
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 7, proc.stdout + proc.stderr
    assert not (worktree / RECEIPT_RELATIVE).exists()
    assert "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout


def test_the_inner_run_refuses_a_head_other_than_the_captured_commit(tmp_path: Path, gate_functions: Path) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    ok = run_bash(f'ASSAY_GATE_EXPECTED_COMMIT="{commit}" require_expected_head "{worktree}"', gate_functions=gate_functions)
    bad = run_bash(f'ASSAY_GATE_EXPECTED_COMMIT="{HEX40}" require_expected_head "{worktree}"', gate_functions=gate_functions)
    assert ok.returncode == 0, ok.stderr
    assert bad.returncode != 0
    assert "worktree HEAD is not the commit the host captured" in bad.stderr


def test_a_green_container_yields_the_receipt_and_exactly_one_complete_marker(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    env, log = _docker_stub(tmp_path, "")

    proc = run_bash(
        "run_registered_tester_container() { echo stubbed-tester; }\n"
        "run_sql_qualification() { :; }\n"  # W5: the SQL phase has its own tests (test_qualify_sql.py, T7)
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = worktree / RECEIPT_RELATIVE
    assert json.loads(receipt.read_text(encoding="utf-8"))["commit"] == commit
    assert proc.stdout.splitlines() == [
        "stubbed-tester",
        f"ASSAY_REGISTERED_GATE_RECEIPT={receipt}",
        "ASSAY_REGISTERED_GATE_COMPLETE=1",
    ]


def test_a_busy_host_makes_the_gate_inconclusive_before_anything_else_happens(
    tmp_path: Path, gate_functions: Path
) -> None:
    """CD32: one `docker ps`, no waiting. The receipt from an earlier green run is
    untouched (it is cleared only once this run really starts), the tester never
    launches, and no other docker call is made."""
    worktree, commit, tree = _receipt_worktree(tmp_path)
    earlier = worktree / RECEIPT_RELATIVE
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b'{"an earlier": "receipt"}\n')
    before = earlier.read_bytes()
    env, log = _docker_stub(tmp_path, "run-gate-x\nrun-gate-y\nsome-other-container\n")

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=host busy — rerun: run-gate-x,run-gate-y" in proc.stderr
    assert earlier.read_bytes() == before
    assert "LAUNCHED" not in proc.stdout
    assert "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and calls[0].startswith("ps "), calls


def test_a_host_running_only_other_containers_proceeds_to_the_tester(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    env, log = _docker_stub(tmp_path, "other-container\nnot-run-gate-x\n")

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        "run_sql_qualification() { :; }\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "LAUNCHED" in proc.stdout
    assert (worktree / RECEIPT_RELATIVE).is_file()


def test_the_shared_host_opt_in_lets_other_projects_gates_run_alongside_and_prints_them(
    tmp_path: Path, gate_functions: Path
) -> None:
    """CD50 (G1): `ASSAY_GATE_ALLOW_SHARED_HOST=1` tolerates other projects' `run-gate-*`
    containers, names them on stdout and runs the normal gate; the receipt is unchanged."""
    worktree, commit, tree = _receipt_worktree(tmp_path)
    env, log = _docker_stub(tmp_path, "run-gate-x\nrun-gate-y\nother\n")
    env["ASSAY_GATE_ALLOW_SHARED_HOST"] = "1"

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        "run_sql_qualification() { :; }\n"
        "run_b145_bounded_wait_acceptance_probe() { :; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "LAUNCHED" in proc.stdout
    assert "ASSAY_GATE_SHARED_HOST=run-gate-x,run-gate-y" in proc.stdout.splitlines()
    receipt = worktree / RECEIPT_RELATIVE
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "lane": "tester-unified",
        "commit": commit,
        "tree": tree,
    }
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and calls[0].startswith("ps "), calls


def test_the_shared_host_opt_in_still_refuses_another_assay_gate(tmp_path: Path, gate_functions: Path) -> None:
    """CD50 (G2): one assay gate at a time, opt-in or not."""
    worktree, commit, tree = _receipt_worktree(tmp_path)
    earlier = worktree / RECEIPT_RELATIVE
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b'{"an earlier": "receipt"}\n')
    before = earlier.read_bytes()
    env, log = _docker_stub(tmp_path, "run-gate-assay-selfhosted-1-2-3\nrun-gate-x\n")
    env["ASSAY_GATE_ALLOW_SHARED_HOST"] = "1"

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=host busy — rerun: run-gate-assay-selfhosted-1-2-3" in proc.stderr
    assert "run-gate-x" not in proc.stderr
    assert earlier.read_bytes() == before
    assert "LAUNCHED" not in proc.stdout
    assert "ASSAY_GATE_SHARED_HOST" not in proc.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1 and calls[0].startswith("ps "), calls


def test_a_shared_host_opt_in_value_other_than_empty_or_one_is_refused_before_docker(
    tmp_path: Path, gate_functions: Path
) -> None:
    """CD50 (G3): a typo never launches, never touches the receipt and never reaches docker."""
    worktree, commit, tree = _receipt_worktree(tmp_path)
    earlier = worktree / RECEIPT_RELATIVE
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b'{"an earlier": "receipt"}\n')
    before = earlier.read_bytes()
    env, log = _docker_stub(tmp_path, "")
    env["ASSAY_GATE_ALLOW_SHARED_HOST"] = "yes"

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "ASSAY_GATE_ALLOW_SHARED_HOST must be unset, empty or 1" in proc.stderr
    assert earlier.read_bytes() == before
    assert "LAUNCHED" not in proc.stdout
    assert not log.exists()  # zero docker calls


def test_a_failing_docker_ps_is_inconclusive_and_leaves_the_receipt(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    earlier = worktree / RECEIPT_RELATIVE
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b'{"an earlier": "receipt"}\n')
    fake_bin = tmp_path / "failing-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)
    env = {**_host_environ(), "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        "run_b145_low_pids_probe() { :; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=host check failed (docker ps) — rerun" in proc.stderr
    assert "LAUNCHED" not in proc.stdout
    assert earlier.read_bytes() == b'{"an earlier": "receipt"}\n'


def test_the_entry_section_ends_with_run_registered_gate_and_only_the_finisher_says_complete() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")

    assert source.rstrip().splitlines()[-1] == 'run_registered_gate "$worktree" "$host_repo_root" "$cgroup_parent"'
    assert source.count("echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'") == 1
    finisher = source.split("finish_registered_gate() {", 1)[1].split("\n}\n", 1)[0]
    assert "echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'" in finisher
