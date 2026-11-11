"""The CMRU release wrapper delegates only the selected Assay gate commands."""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "tools" / "release-gate.sh"
REPO_ROOT = PROJECT_ROOT.parent
CMRU_FILE = PROJECT_ROOT / "cmru.toml"
RUN_GATE_FILE = PROJECT_ROOT / "run-gate.toml"
WORKFLOW_FILE = PROJECT_ROOT / "nyxloom-trove" / "WORKFLOW.md"


FAKE_REMOTE_GATE = r'''#!/usr/bin/env python3
import json
import os
import signal
import sys
from pathlib import Path

args = sys.argv[1:]
remote_out = Path(os.environ["REMOTE_GATE_OUT"])
config = json.loads((remote_out.parent / "fake-config.json").read_text(encoding="utf-8"))
calls_path = remote_out / "remote-calls.jsonl"

def record_call(argv):
    with calls_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(argv) + "\n")

if args[0] == "cancel":
    record_call(args)
    (remote_out / "remote-cancelled.txt").write_text("cancelled", encoding="utf-8")
    (remote_out / "remote-cancel-env-secrets.json").write_text(
        json.dumps({
            key: os.environ.get(key) is not None
            for key in (
                "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
                "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
            )
        }),
        encoding="utf-8",
    )
    if config.get("signal_mode"):
        pid = int((remote_out / "remote-child.pid").read_text(encoding="utf-8"))
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError:
            pass
    raise SystemExit(int(config.get("cancel_status", 0)))

if args[0] != "run" or "--detach" in args:
    raise SystemExit("release-gate must use attached remote-gate mode")
record_call(args)
name = args[args.index("--name") + 1]
lane = args[3]
run_dir = remote_out / name
run_dir.mkdir(parents=True, exist_ok=False)
if config.get("write_meta", True):
    (run_dir / "meta.json").write_text(
        json.dumps({"sha": config["meta_sha"]}), encoding="utf-8"
    )
if config.get("write_result", True):
    (run_dir / "result.json").write_text(
        json.dumps({
            "lane": lane,
            "verdict": config.get("verdict", "PASS"),
            "exit_code": int(config.get("lane_status", 0)),
        }),
        encoding="utf-8",
    )
(remote_out / "remote-env-secrets.json").write_text(
    json.dumps({
        key: os.environ.get(key) is not None
        for key in (
            "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
            "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
        )
    }),
    encoding="utf-8",
)
(remote_out / "remote-env-keys.json").write_text(
    json.dumps(sorted(os.environ)), encoding="utf-8"
)
(remote_out / "remote-child.pid").write_text(str(os.getpid()), encoding="utf-8")
if config.get("signal_mode"):
    released = False
    def release(_sig, _frame):
        global released
        released = True
    def handle_signal(sig, _frame):
        (remote_out / "remote-signal.txt").write_text(str(sig), encoding="utf-8")
        while not released:
            signal.pause()
        (remote_out / "remote-child-finished.txt").write_text("finished", encoding="utf-8")
        raise SystemExit(128 + sig)
    signal.signal(signal.SIGUSR1, release)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    with open(config["ready_fifo"], "w", encoding="utf-8") as stream:
        stream.write("ready\n")
    while True:
        signal.pause()
remote_status = config.get("remote_status")
if remote_status is None:
    remote_status = config.get("lane_status", 0)
raise SystemExit(int(remote_status))
'''


def _environment(tmp_path: Path, *, host: str = "v1001") -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_python = sys.executable
    fake_remote = fake_bin / "remote-gate.py"
    fake_remote.write_text(FAKE_REMOTE_GATE, encoding="utf-8")
    fake_remote.chmod(0o755)

    python = fake_bin / "python3"
    python.write_text(
        f"#!{real_python}\n"
        "import os\n"
        "import sys\n"
        f"fake_remote = {str(fake_remote)!r}\n"
        f"real_python = {real_python!r}\n"
        "if sys.argv[1:2] == ['../scripts/netcup/runner/remote-gate.py']:\n"
        "    argv = [real_python, fake_remote, *sys.argv[2:]]\n"
        "else:\n"
        "    argv = [real_python, *sys.argv[1:]]\n"
        "os.execv(real_python, argv)\n",
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
    remote_out = tmp_path / "remote-runs"
    remote_out.mkdir()
    (tmp_path / "fake-config.json").write_text(
        json.dumps(
            {
                "meta_sha": commit,
                "verdict": "PASS",
                "lane_status": 0,
                "write_meta": True,
                "write_result": True,
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        # The self-hosted lane scrubs HOME (env_passthrough is PATH only) and
        # release-gate.sh forwards HOME under `set -u`: the helper must not
        # depend on the caller having one.
        "HOME": os.environ.get("HOME") or str(tmp_path),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REMOTE_GATE_OUT": str(remote_out),
        "CMRU_RELEASE_GATE_COMMIT": commit,
    }
    if host:
        env["ASSAY_RELEASE_GATE_HOST"] = host
    else:
        env.pop("ASSAY_RELEASE_GATE_HOST", None)
    return env


def _fake_config(env: dict[str, str]) -> Path:
    return Path(env["REMOTE_GATE_OUT"]).parent / "fake-config.json"


def _configure_fake(env: dict[str, str], **updates: object) -> None:
    path = _fake_config(env)
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update(updates)
    path.write_text(json.dumps(config), encoding="utf-8")


def _remote_calls(env: dict[str, str]) -> list[list[str]]:
    path = Path(env["REMOTE_GATE_OUT"]) / "remote-calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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


def test_pre_merge_tooling_lane_binds_the_release_attempt_failure_groups() -> None:
    lane = tomllib.loads(RUN_GATE_FILE.read_text(encoding="utf-8"))["lanes"][
        "tooling-tests"
    ]
    command = lane["argv"][2]
    for test_path in (
        "gate/tests/test_b105_report_check.py",
        "gate/tests/test_release_gate.py",
        "gate/tests/test_distribution_gate.py",
        "gate/tests/test_self_qualification_container.py",
        "tests/core/test_docs_examples_and_vocabulary.py",
        # B533: cross-cutting contracts the 12.2.0 release attempt found red.
        "tests/core/test_termination.py",
        "tests/core/test_termination_alignment.py",
        "tests/core/test_canary_receipt.py",
        "tests/core/test_trust_boundary.py",
        "tests/core/test_dataclass_contract.py",
        "tests/core/test_b105_source_coverage_controls.py",
    ):
        assert test_path in command

    workflow = " ".join(WORKFLOW_FILE.read_text(encoding="utf-8").split())
    assert "run-gate tooling-tests" in workflow
    # B534: judge and analysis sources need their R1 coverage lane before merge.
    assert "## Pre-merge coverage gate for judge and analysis sources (B534)" in workflow
    assert "rg -q '^assay/src/'" in workflow
    assert "bash tools/release-gate.sh self-qualification-provisional" in workflow
    assert "rg -q '^assay/analysis/src/'" in workflow
    assert "bash tools/release-gate.sh tester-unified" in workflow
    for trigger in (
        "assay/tools/**",
        "assay/gate/**",
        "assay/assay.toml",
        "assay/README.md",
        "assay/docs/DESIGN-GUIDE.md",
        "assay/docs/CONSUMERS.md",
    ):
        assert trigger in workflow


def test_release_gate_requires_the_host_selector(tmp_path: Path) -> None:
    env = _environment(tmp_path, host="")
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert proc.stderr.strip() == (
        "assay release-gate: ASSAY_RELEASE_GATE_HOST is unset; "
        "set it to local, v1001, or r1002"
    )
    assert not _remote_calls(env)


@pytest.mark.parametrize("home", [None, ""])
def test_remote_release_gate_refuses_an_unset_or_empty_home_before_running(
    tmp_path: Path, home: str | None
) -> None:
    env = _environment(tmp_path)
    if home is None:
        env.pop("HOME")
    else:
        env["HOME"] = home
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert proc.stderr.strip() == (
        "assay release-gate: HOME is unset; remote-gate needs "
        "~/.config/remote-gate/hosts.json and ~/.ssh"
    )
    assert not _remote_calls(env)


@pytest.mark.parametrize(
    ("verdict", "lane_status", "expected_status"),
    [("PASS", 0, 0), ("FAIL", 1, 1), ("FAIL", 0, 3), ("UNKNOWN", 0, 3)],
)
def test_release_gate_checks_remote_verdict_and_returns_lane_status(
    tmp_path: Path, verdict: str, lane_status: int, expected_status: int
) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, verdict=verdict, lane_status=lane_status)
    proc = _run(tmp_path, "self-qualification-provisional", env)

    assert proc.returncode == expected_status, proc.stderr
    assert (f"refusing self-qualification-provisional verdict {verdict}" in proc.stderr) is (
        verdict != "PASS"
    )
    args = _remote_calls(env)[0]
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


def test_release_gate_scrubs_publisher_and_gh_credentials_from_remote_gate(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    credentials = {
        "GITHUB_PUSH_PAT": "publisher-secret",
        "GITHUB_TOKEN": "github-token-secret",
        "CMRU_GIT_AUTH_TOKEN": "git-auth-secret",
        "GH_TOKEN": "gh-token-secret",
        "GH_ENTERPRISE_TOKEN": "gh-enterprise-secret",
    }
    env.update(credentials)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 0, proc.stderr
    observed = json.loads(
        (Path(env["REMOTE_GATE_OUT"]) / "remote-env-secrets.json").read_text(
            encoding="utf-8"
        )
    )
    assert observed == {key: False for key in credentials}
    observed_keys = set(
        json.loads(
            (Path(env["REMOTE_GATE_OUT"]) / "remote-env-keys.json").read_text(
                encoding="utf-8"
            )
        )
    )
    # Python's locale coercion may add LC_CTYPE when starting with env -i.
    allowed_keys = {"PATH", "HOME", "REMOTE_GATE_OUT", "LC_CTYPE"}
    if "REMOTE_GATE_HOSTS" in env:
        allowed_keys.add("REMOTE_GATE_HOSTS")
    if env.get("SSH_AUTH_SOCK"):
        allowed_keys.add("SSH_AUTH_SOCK")
    assert observed_keys == allowed_keys


def test_release_gate_refuses_a_remote_sha_mismatch(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, meta_sha="f" * 40)
    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert "remote-gate meta.json SHA mismatch" in proc.stderr
    assert "expected " + env["CMRU_RELEASE_GATE_COMMIT"] in proc.stderr


def test_release_gate_refuses_a_commit_that_does_not_match_head(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    actual_head = env["CMRU_RELEASE_GATE_COMMIT"]
    expected = "f" * 40 if not actual_head.startswith("f") else "e" * 40
    env["CMRU_RELEASE_GATE_COMMIT"] = expected

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert expected in proc.stderr
    assert actual_head in proc.stderr
    assert "does not match worktree HEAD" in proc.stderr
    assert not _remote_calls(env)


def test_release_gate_rejects_remote_vs_lane_exit_mismatch(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, remote_status=0, lane_status=1)

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    assert "remote-gate exit status 0 differs from result.json lane exit code 1" in proc.stderr


def test_release_gate_propagates_remote_failure_without_result_files(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    _configure_fake(env, remote_status=75, write_meta=False, write_result=False)

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 75
    assert "remote-gate produced no valid result" in proc.stderr
    assert len(_remote_calls(env)) == 1
    assert not (Path(env["REMOTE_GATE_OUT"]) / "remote-cancelled.txt").exists()


def test_release_gate_cancels_unattended_remote_job_after_failed_polls(
    tmp_path: Path,
) -> None:
    env = _environment(tmp_path)
    secret_names = (
        "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
        "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
    )
    env.update({name: f"secret-{name}" for name in secret_names})
    _configure_fake(env, remote_status=3, write_meta=True, write_result=False)

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 3
    calls = _remote_calls(env)
    assert len(calls) == 2
    assert calls[0][0] == "run"
    assert calls[1][0] == "cancel"
    assert calls[1][1] == calls[0][calls[0].index("--name") + 1]
    assert "attempting to cancel unattended remote run" in proc.stderr
    assert (Path(env["REMOTE_GATE_OUT"]) / "remote-cancelled.txt").exists()
    # The cancel call of the unattended-job path runs under the same credential
    # scrub as the run call (review lows-1 F3).
    cancel_secrets = json.loads(
        (Path(env["REMOTE_GATE_OUT"]) / "remote-cancel-env-secrets.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(cancel_secrets[name] is False for name in secret_names)


@pytest.mark.parametrize(
    ("sig", "expected_status"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_release_gate_forwards_real_signals_and_cancels_before_waiting(
    tmp_path: Path,
    sig: signal.Signals,
    expected_status: int,
) -> None:
    env = _environment(tmp_path)
    secret_names = (
        "GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN",
        "GH_TOKEN", "GH_ENTERPRISE_TOKEN",
    )
    env.update({name: f"secret-{name}" for name in secret_names})
    ready_fifo = tmp_path / "remote-ready.fifo"
    os.mkfifo(ready_fifo)
    _configure_fake(env, signal_mode=True, ready_fifo=str(ready_fifo))
    wait_hook = tmp_path / "bash-env.sh"
    wait_calls = tmp_path / "wrapper-wait-calls.txt"
    wait_reaped = tmp_path / "wrapper-wait-completed.txt"
    wait_hook.write_text(
        "wait() {\n"
        '  local child_pid=${1:-unknown} status=0\n'
        '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_CALLS"\n'
        '  builtin wait "$@" || status=$?\n'
        '  printf "%s\\n" "$child_pid" >>"$ASSAY_WAIT_REAPED"\n'
        '  return "$status"\n'
        "}\n",
        encoding="utf-8",
    )
    env.update(
        {
            "BASH_ENV": str(wait_hook),
            "ASSAY_WAIT_CALLS": str(wait_calls),
            "ASSAY_WAIT_REAPED": str(wait_reaped),
        }
    )
    ready_fd = os.open(ready_fifo, os.O_RDWR | os.O_NONBLOCK)
    stdout_path = tmp_path / "wrapper.stdout"
    stderr_path = tmp_path / "wrapper.stderr"
    stdout_stream = stdout_path.open("w", encoding="utf-8")
    stderr_stream = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["/bin/bash", str(SCRIPT), "tester-unified"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=stdout_stream,
        stderr=stderr_stream,
        start_new_session=True,
    )
    try:
        ready, _, _ = select.select([ready_fd], [], [], 15)
        assert ready, "fake remote-gate did not reach its signal-ready handshake"
        assert os.read(ready_fd, 32) == b"ready\n"
        proc.send_signal(sig)
        proc.wait(timeout=15)
    finally:
        os.close(ready_fd)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            proc.wait(timeout=15)
        stdout_stream.close()
        stderr_stream.close()

    stdout = stdout_path.read_text(encoding="utf-8")
    stderr = stderr_path.read_text(encoding="utf-8")

    assert proc.returncode == expected_status, (stdout, stderr)
    assert (Path(env["REMOTE_GATE_OUT"]) / "remote-signal.txt").read_text(
        encoding="utf-8"
    ) == str(sig.value)
    assert (Path(env["REMOTE_GATE_OUT"]) / "remote-child-finished.txt").exists()
    child_pid = (Path(env["REMOTE_GATE_OUT"]) / "remote-child.pid").read_text(encoding="utf-8")
    assert wait_calls.read_text(encoding="utf-8").splitlines().count(child_pid) >= 2
    assert child_pid in wait_reaped.read_text(encoding="utf-8").splitlines()
    run_secrets = json.loads((Path(env["REMOTE_GATE_OUT"]) / "remote-env-secrets.json").read_text(encoding="utf-8"))
    cancel_secrets = json.loads((Path(env["REMOTE_GATE_OUT"]) / "remote-cancel-env-secrets.json").read_text(encoding="utf-8"))
    assert all(run_secrets[name] is False and cancel_secrets[name] is False for name in secret_names)
    calls = _remote_calls(env)
    assert [call[0] for call in calls] == ["run", "cancel"]
    assert "remote cancellation was requested" in stderr


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
    assert not _remote_calls(env)
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
    assert not _remote_calls(env)


def test_local_release_gate_stays_available_without_home(tmp_path: Path) -> None:
    env = _environment(tmp_path, host="local")
    env.pop("HOME")
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    command = fake_bin / "bash"
    command.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" > "$FAKE_LOCAL_COMMAND"\n'
        'exit "${FAKE_LOCAL_STATUS:-17}"\n',
        encoding="utf-8",
    )
    command.chmod(0o755)
    env["FAKE_LOCAL_COMMAND"] = str(tmp_path / "local-command.txt")

    proc = _run(tmp_path, "tester-unified", env)

    assert proc.returncode == 17
    assert Path(env["FAKE_LOCAL_COMMAND"]).read_text(encoding="utf-8").strip() == (
        "tools/tester-unified-gate.sh .."
    )
    assert not _remote_calls(env)
