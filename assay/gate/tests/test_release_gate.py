"""The CMRU release wrapper delegates only the selected Assay gate commands."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "tools" / "release-gate.sh"
REPO_ROOT = PROJECT_ROOT.parent
CMRU_FILE = PROJECT_ROOT / "cmru.toml"


FAKE_REMOTE_GATE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if "--detach" in args:
    raise SystemExit("release-gate must use attached remote-gate mode")
name = args[args.index("--name") + 1]
lane = args[3]
run_dir = Path(os.environ["REMOTE_GATE_OUT"]) / name
run_dir.mkdir(parents=True, exist_ok=False)
sha = os.environ.get("FAKE_META_SHA", os.environ["CMRU_RELEASE_GATE_COMMIT"])
verdict = os.environ.get("FAKE_VERDICT", "PASS")
lane_status = int(os.environ.get("FAKE_LANE_STATUS", "0"))
(run_dir / "meta.json").write_text(json.dumps({"sha": sha}), encoding="utf-8")
(run_dir / "result.json").write_text(
    json.dumps({"lane": lane, "verdict": verdict, "exit_code": lane_status}),
    encoding="utf-8",
)
Path(os.environ["FAKE_REMOTE_CALL"]).write_text(json.dumps(args), encoding="utf-8")
raise SystemExit(int(os.environ.get("FAKE_REMOTE_STATUS", str(lane_status))))
'''


def _environment(tmp_path: Path, *, host: str = "v1001") -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_remote = fake_bin / "remote-gate.py"
    fake_remote.write_text(FAKE_REMOTE_GATE, encoding="utf-8")
    fake_remote.chmod(0o755)

    real_python = sys.executable
    python = fake_bin / "python3"
    python.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "../scripts/netcup/runner/remote-gate.py" ]]; then\n'
        '  shift\n'
        '  exec "$FAKE_REMOTE_GATE" "$@"\n'
        "fi\n"
        f"exec {real_python!r} \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)

    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_REMOTE_GATE": str(fake_remote),
        "FAKE_REMOTE_CALL": str(tmp_path / "remote-call.json"),
        "REMOTE_GATE_OUT": str(tmp_path / "remote-runs"),
        "CMRU_RELEASE_GATE_COMMIT": commit,
    }
    if host:
        env["ASSAY_RELEASE_GATE_HOST"] = host
    else:
        env.pop("ASSAY_RELEASE_GATE_HOST", None)
    return env


def _run(tmp_path: Path, lane: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), lane],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_cmru_release_steps_use_the_delegating_wrapper() -> None:
    commands = tomllib.loads(CMRU_FILE.read_text(encoding="utf-8"))["steps"]["run-tests"]["commands"]

    assert commands[0]["argv"] == ["bash", "tools/release-gate.sh", "tester-unified"]
    assert commands[1]["argv"] == [
        "bash", "tools/release-gate.sh", "self-qualification-provisional"
    ]
    assert commands[0]["cwd"] == commands[1]["cwd"] == "."


def test_release_gate_requires_the_host_selector(tmp_path: Path) -> None:
    env = _environment(tmp_path, host="")
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert proc.stderr.strip() == (
        "assay release-gate: ASSAY_RELEASE_GATE_HOST is unset; "
        "set it to local, v1001, or r1002"
    )
    assert not Path(env["FAKE_REMOTE_CALL"]).exists()


@pytest.mark.parametrize(
    ("verdict", "lane_status", "expected_status"),
    [("PASS", 0, 0), ("FAIL", 1, 1)],
)
def test_release_gate_checks_remote_verdict_and_returns_lane_status(
    tmp_path: Path, verdict: str, lane_status: int, expected_status: int
) -> None:
    env = _environment(tmp_path)
    env["FAKE_VERDICT"] = verdict
    env["FAKE_LANE_STATUS"] = str(lane_status)
    env["REMOTE_GATE_SECRET"] = "SENTINEL_CREDENTIAL"
    proc = _run(tmp_path, "self-qualification-provisional", env)

    assert proc.returncode == expected_status, proc.stderr
    assert ("refusing self-qualification-provisional verdict FAIL" in proc.stderr) is (
        verdict != "PASS"
    )
    args = json.loads(Path(env["FAKE_REMOTE_CALL"]).read_text(encoding="utf-8"))
    assert args[:4] == [
        "run",
        "v1001",
        str(REPO_ROOT),
        "self-qualification-provisional",
    ]
    assert args[args.index("--project-dir") + 1] == "assay"
    assert args[args.index("--repo") + 1] == "vbpub"
    name = args[args.index("--name") + 1]
    commit = env["CMRU_RELEASE_GATE_COMMIT"]
    assert name.startswith(f"assay-rel-{commit[:12]}-self-qualification-provisional-")
    assert re.fullmatch(r"assay-rel-[0-9a-f]{12}-self-qualification-provisional-[0-9a-z]{7}", name)
    assert len(name) <= 61
    assert "--detach" not in args
    assert env["REMOTE_GATE_SECRET"] not in proc.stdout + proc.stderr


def test_release_gate_refuses_a_remote_sha_mismatch(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    env["FAKE_META_SHA"] = "f" * 40
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert "remote-gate meta.json SHA mismatch" in proc.stderr
    assert "expected " + env["CMRU_RELEASE_GATE_COMMIT"] in proc.stderr


@pytest.mark.parametrize("payload_kind", ("host", "lane"))
def test_release_gate_rejects_host_and_lane_injection(
    tmp_path: Path, payload_kind: str
) -> None:
    marker = tmp_path / f"{payload_kind}-injection-ran"
    host = f"v1001;touch {marker}" if payload_kind == "host" else "v1001"
    lane = "tester-unified" if payload_kind == "host" else f"tester-unified;touch {marker}"
    env = _environment(tmp_path, host=host)
    proc = _run(tmp_path, lane, env)

    assert proc.returncode == 2
    assert ("ASSAY_RELEASE_GATE_HOST" in proc.stderr) is (payload_kind == "host")
    assert ("unsupported release-gate lane" in proc.stderr) is (payload_kind == "lane")
    assert not Path(env["FAKE_REMOTE_CALL"]).exists()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("lane", "expected_command"),
    [
        ("tester-unified", "tools/tester-unified-gate.sh .."),
        ("self-qualification-provisional", "self-qualification-provisional"),
    ],
)
def test_local_release_gate_execs_the_existing_lane_command(
    tmp_path: Path, lane: str, expected_command: str
) -> None:
    env = _environment(tmp_path, host="local")
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    for command in ("bash", "run-gate"):
        path = fake_bin / command
        path.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$*" > "$FAKE_LOCAL_COMMAND"\n'
            'exit "${FAKE_LOCAL_STATUS:-17}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
    env["FAKE_LOCAL_COMMAND"] = str(tmp_path / "local-command.txt")
    proc = _run(tmp_path, lane, env)

    assert proc.returncode == 17
    assert Path(env["FAKE_LOCAL_COMMAND"]).read_text(encoding="utf-8").strip() == expected_command
    assert not Path(env["FAKE_REMOTE_CALL"]).exists()
