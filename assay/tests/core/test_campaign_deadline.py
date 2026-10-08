"""B117 campaign deadlines bind run identity, plans, and resumable state."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import ctypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, R0_LANE, make_lane, make_plan

from assay import cli, liveness, runner
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode


UTC_NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def _document(**overrides):
    value = {
        "schema": "assay-campaign-deadline/1",
        "campaign": "campaign-1",
        "commit": "a" * 40,
        "git_tree": "b" * 40,
        "lanes": ["package"],
        "assay_version": cli.__version__,
        "wheel_sha256": None,
        "plan_sha256": {"package": None},
        "created_at_utc": "2026-10-07T12:00:00Z",
        "expires_at_utc": "2026-10-07T13:00:00Z",
    }
    value.update(overrides)
    return value


def _init(repo: GitRepo, deadline: Path, *, campaign: str = "campaign-1", hours: str = "1", wheel_sha256: str | None = None):
    lane = R0_LANE.replace(
        'argv = ["pytest", "tests/unit", "-q"]',
        'argv = ["/bin/true"]',
    )
    config = repo.write("assay.toml", lane)
    if repo.git("status", "--porcelain"):
        repo.commit_all("add campaign lane")
    args = [
        "campaign", "init", "--campaign", campaign, "--lane", "package",
        "--hours", hours, "--file", str(config), "--out", str(deadline),
    ]
    if wheel_sha256 is not None:
        args.extend(("--wheel-sha256", wheel_sha256))
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(args, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture(autouse=True)
def _clear_termination_state():
    runner._reset_termination_for_tests()
    yield
    runner._reset_termination_for_tests()


def test_campaign_deadline_parser_requires_exact_unique_key_wire():
    raw = json.dumps(_document(), sort_keys=True).encode("utf-8")
    parsed, returned_raw, expiry = cli._parse_campaign_deadline_bytes(
        raw, absolute=Path("/tmp/deadline.json"), lane="package"
    )
    assert parsed == _document()
    assert returned_raw == raw
    assert expiry == datetime(2026, 10, 7, 13, 0, 0, tzinfo=timezone.utc)

    duplicate = raw[:-1] + b', "campaign": "second"}'
    with pytest.raises(LaneConfigError, match="unique-key JSON"):
        cli._parse_campaign_deadline_bytes(
            duplicate, absolute=Path("/tmp/deadline.json"), lane="package"
        )

    extra = json.dumps({**_document(), "unreviewed": True}).encode()
    with pytest.raises(LaneConfigError, match="exactly the declared fields"):
        cli._parse_campaign_deadline_bytes(
            extra, absolute=Path("/tmp/deadline.json"), lane="package"
        )


def test_campaign_deadline_limits_the_lane_deadline_and_requires_utc():
    lane_deadline = runner.LaneDeadline(expires_at=75.0, monotonic=lambda: 30.0)
    bounded = runner.campaign_bounded_deadline(
        lane_deadline,
        expires_at_utc=UTC_NOW + timedelta(seconds=90),
        wall_now=UTC_NOW,
        monotonic_now=30.0,
    )
    assert bounded.expires_at == 75.0

    earlier = runner.campaign_bounded_deadline(
        lane_deadline,
        expires_at_utc=UTC_NOW + timedelta(seconds=10),
        wall_now=UTC_NOW,
        monotonic_now=30.0,
    )
    assert earlier.expires_at == 40.0
    assert earlier.remaining() == 10.0

    with pytest.raises(ValueError, match="wall_now must be timezone-aware UTC"):
        runner.campaign_bounded_deadline(
            lane_deadline,
            expires_at_utc=UTC_NOW,
            wall_now=UTC_NOW.replace(tzinfo=None),
            monotonic_now=30.0,
        )


def test_campaign_init_reuses_same_identity_without_extending_expiry(
    git_repo: GitRepo, tmp_path: Path
):
    deadline = tmp_path / "deadline.json"
    code, _, err = _init(git_repo, deadline)
    assert code == Outcome.PASS.exit_code, err
    first = deadline.read_bytes()
    first_doc = json.loads(first)

    code, _, err = _init(git_repo, deadline, hours="2")
    assert code == Outcome.PASS.exit_code, err
    assert deadline.read_bytes() == first
    assert json.loads(deadline.read_bytes())["expires_at_utc"] == first_doc["expires_at_utc"]
    assert set(first_doc) == cli._CAMPAIGN_DEADLINE_KEYS
    assert first_doc["commit"] == git_repo.head()
    assert first_doc["plan_sha256"] == {"package": None}


@pytest.mark.parametrize(
    ("second_campaign", "second_wheel"),
    [("campaign-2", "a" * 64), ("campaign-1", "c" * 64)],
)
def test_campaign_init_refuses_reusing_path_for_a_different_campaign_identity(
    git_repo: GitRepo,
    tmp_path: Path,
    second_campaign: str,
    second_wheel: str,
):
    deadline = tmp_path / "deadline.json"
    code, _, err = _init(git_repo, deadline, wheel_sha256="a" * 64)
    assert code == Outcome.PASS.exit_code, err
    original = deadline.read_bytes()

    code, _, err = _init(
        git_repo,
        deadline,
        campaign=second_campaign,
        wheel_sha256=second_wheel,
    )
    assert code == Outcome.ERROR.exit_code
    assert "different identity" in err
    assert deadline.read_bytes() == original


def test_campaign_state_records_must_match_the_persisted_deadline_hash(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / ("a" * 64 + ".json")).write_text(
        json.dumps({"campaign_deadline_sha256": "b" * 64}), encoding="utf-8"
    )
    with pytest.raises(LaneConfigError, match="not bound to the campaign deadline"):
        cli._campaign_state_records_match(state_dir, "c" * 64)


def test_run_checks_campaign_identity_before_expiry_and_before_the_lane_command(
    git_repo: GitRepo, tmp_path: Path
):
    marker = tmp_path / "lane-ran"
    lane = R0_LANE.replace(
        'argv = ["pytest", "tests/unit", "-q"]',
        f'argv = ["/bin/sh", "-c", "touch {marker}"]',
    )
    config = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add campaign lane")
    deadline_path = tmp_path / "deadline.json"
    code = cli.main(
        [
            "campaign", "init", "--campaign", "campaign-1", "--lane", "package",
            "--hours", "1", "--file", str(config), "--out", str(deadline_path),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == Outcome.PASS.exit_code

    document = json.loads(deadline_path.read_text(encoding="utf-8"))
    document["assay_version"] = "deliberately-stale-version"
    document["expires_at_utc"] = "2025-01-01T00:00:00Z"
    document["created_at_utc"] = "2024-01-01T00:00:00Z"
    deadline_path.write_text(json.dumps(document), encoding="utf-8")
    verdict_path = tmp_path / "verdict.json"
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(
        [
            "run", "package", "--file", str(config), "--campaign-deadline",
            str(deadline_path), "--verdict-json", str(verdict_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == Outcome.ERROR.exit_code
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict["outcome"] == "ERROR"
    assert verdict["reason_code"] == ReasonCode.BAD_LANE_CONFIG.value
    assert "assay_version expected deliberately-stale-version" in stderr.getvalue()
    assert not marker.exists()


def test_expired_campaign_with_matching_identity_writes_timeout_verdict_without_launch(
    git_repo: GitRepo, tmp_path: Path
):
    marker = tmp_path / "lane-ran"
    lane = R0_LANE.replace(
        'argv = ["pytest", "tests/unit", "-q"]',
        f'argv = ["/bin/sh", "-c", "touch {marker}"]',
    )
    config = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add campaign lane")
    deadline_path = tmp_path / "deadline.json"
    code, _, err = _init(git_repo, deadline_path)
    assert code == Outcome.PASS.exit_code, err

    document = json.loads(deadline_path.read_text(encoding="utf-8"))
    document["created_at_utc"] = "2024-01-01T00:00:00Z"
    document["expires_at_utc"] = "2025-01-01T00:00:00Z"
    deadline_path.write_text(json.dumps(document), encoding="utf-8")
    verdict_path = tmp_path / "expired-verdict.json"
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(
        [
            "run", "package", "--file", str(config), "--campaign-deadline",
            str(deadline_path), "--verdict-json", str(verdict_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict["outcome"] == Outcome.BUDGET_EXCEEDED.value
    assert verdict["reason_code"] == ReasonCode.LANE_TIMEOUT.value
    assert verdict["commit"] == git_repo.head()
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["return", "hung", "timeout", "oserror", "assay-error"])
def test_execute_plan_termination_overrides_every_runner_exit_path(
    tmp_path: Path, failure: str
):
    plan = make_plan(make_lane(argv=("/bin/true",)))

    def process_runner(argv, *, env, cwd, timeout):
        runner.request_termination()
        if failure == "return":
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        if failure == "hung":
            raise liveness.LivenessHungExpired(
                argv,
                timeout=timeout,
                output=b"",
                stderr=b"",
                resource_evidence={},
            )
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout, output=b"", stderr=b"")
        if failure == "oserror":
            raise OSError("injected launch failure")
        raise AssayError(
            "injected git failure",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        )

    with pytest.raises(AssayError) as caught:
        runner.execute_plan(
            plan,
            cwd=tmp_path,
            timeout=60.0,
            process_runner=process_runner,
        )
    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    if failure == "assay-error":
        assert isinstance(caught.value.__cause__, AssayError)


def test_execute_plan_checks_termination_before_calling_the_runner(tmp_path: Path):
    runner.request_termination()
    plan = make_plan(make_lane(argv=("/bin/true",)))
    called = False

    def process_runner(argv, *, env, cwd, timeout):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    with pytest.raises(AssayError) as caught:
        runner.execute_plan(
            plan,
            cwd=tmp_path,
            timeout=60.0,
            process_runner=process_runner,
        )
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert not called


def test_termination_handler_is_reentrant_and_sweeps_registered_groups(
    monkeypatch, capfd
):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(liveness, "_killpg_group", lambda pgid, sig: killed.append((pgid, sig)))
    groups = (10**9 + 1, 10**9 + 2)
    for group in groups:
        liveness.register_live_group(group)

    with liveness._LIVE_GROUPS_LOCK:
        cli._termination_signal_handler(signal.SIGTERM, None)

    assert runner.termination_requested()
    assert set(killed) == {(group, signal.SIGKILL) for group in groups}
    captured = capfd.readouterr()
    assert captured.err == (
        "assay: termination requested (signal 15); stopping and writing an incomplete verdict\n"
    )


def test_termination_handler_does_not_block_on_registry_owned_by_another_thread(
    monkeypatch,
):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(liveness, "_killpg_group", lambda pgid, sig: killed.append((pgid, sig)))
    group = 10**9 + 3
    second_group = 10**9 + 4
    liveness.register_live_group(group)
    liveness.register_live_group(second_group)
    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with liveness._LIVE_GROUPS_LOCK:
            locked.set()
            assert release.wait(5)

    helper = threading.Thread(target=hold_lock, daemon=True)
    helper.start()
    assert locked.wait(5)
    try:
        cli._termination_signal_handler(signal.SIGTERM, None)
        assert liveness._DEFERRED_TERMINATION_SWEEP is True
        assert set(killed) == {
            (group, signal.SIGKILL),
            (second_group, signal.SIGKILL),
        }
    finally:
        release.set()
        helper.join(timeout=5)
    assert not helper.is_alive()

    liveness.unregister_live_group(second_group)
    assert killed.count((group, signal.SIGKILL)) == 2
    assert killed.count((second_group, signal.SIGKILL)) == 1
    assert liveness._DEFERRED_TERMINATION_SWEEP is False


def test_termination_handlers_only_install_on_the_main_thread():
    before = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    worker_result: list[object] = []

    def install_from_worker():
        worker_result.append(cli._install_termination_handlers())

    worker = threading.Thread(target=install_from_worker, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert callable(worker_result[0])
    worker_result[0]()
    assert {signum: signal.getsignal(signum) for signum in before} == before

    restore = cli._install_termination_handlers()
    try:
        assert all(
            signal.getsignal(signum) == cli._termination_signal_handler
            for signum in before
        )
    finally:
        restore()
    assert {signum: signal.getsignal(signum) for signum in before} == before


def test_termination_kills_a_group_registered_after_the_signal():
    sleeper = subprocess.Popen(
        ["/usr/bin/python3", "-c", "import time; time.sleep(600)"],
        start_new_session=True,
    )
    try:
        runner.request_termination()
        liveness.register_live_group(sleeper.pid)
        assert sleeper.wait(timeout=5) == -signal.SIGKILL
    finally:
        if sleeper.poll() is None:
            liveness._killpg_group(sleeper.pid, signal.SIGKILL)
            sleeper.wait(timeout=5)


def test_oom_counter_reads_only_one_valid_nonnegative_value(tmp_path: Path):
    events = tmp_path / "memory.events"
    events.write_text("low 0\nhigh 1\noom 2\noom_kill 3\noom_group_kill 0\n", encoding="ascii")
    assert liveness.oom_kill_count(events) == 3

    events.write_text("oom_kill 0\noom_kill 1\n", encoding="ascii")
    assert liveness.oom_kill_count(events) is None
    events.write_text("oom_kill -1\n", encoding="ascii")
    assert liveness.oom_kill_count(events) is None
    events.write_text("oom_kill invalid\n", encoding="ascii")
    assert liveness.oom_kill_count(events) is None


def test_oom_counter_reports_unreadable_or_non_ascii_events_as_unavailable(
    tmp_path: Path,
):
    missing = tmp_path / "missing-memory.events"
    assert liveness.oom_kill_count(missing) is None

    events = tmp_path / "memory.events"
    events.write_bytes(b"oom_kill \xff\n")
    assert liveness.oom_kill_count(events) is None


def _proc_state_and_start_time(pid: int) -> tuple[str, str] | None:
    try:
        record = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None
    tail = record.rsplit(")", 1)[1].split()
    return tail[0], tail[19]


def _set_child_subreaper(enabled: int) -> int:
    """Set PR_SET_CHILD_SUBREAPER and return the prior process setting."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.addressof(previous), 0, 0, 0) != 0:  # PR_GET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "prctl(PR_GET_CHILD_SUBREAPER) failed")
    if libc.prctl(36, enabled, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")
    return previous.value


def _reap_adopted_child(pid: int) -> None:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited == pid:
            return
        time.sleep(0.01)
    raise AssertionError(f"adopted process {pid} was not reaped")


def test_default_runner_timeout_kills_and_reaps_its_inherited_pipe_group(
    tmp_path: Path, monkeypatch
):
    previous_subreaper = _set_child_subreaper(1)
    pid_file = tmp_path / "timeout-processes.json"
    child_code = (
        "import json, os, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)']); "
        "stat=lambda p: open(f'/proc/{p}/stat').read().rsplit(')',1)[1].split()[19]; "
        "open(sys.argv[1],'w').write(json.dumps({'child':os.getpid(),"
        "'child_start':stat(os.getpid()),'grandchild':child.pid,"
        "'grandchild_start':stat(child.pid)})); time.sleep(600)"
    )

    def timeout_after_launch(proc, timeout):
        end = time.monotonic() + 5
        while not pid_file.is_file() and time.monotonic() < end:
            time.sleep(0.01)
        if not pid_file.is_file():
            raise AssertionError("runner child did not start its grandchild")
        raise subprocess.TimeoutExpired(
            proc.args, timeout, output=b"partial-out", stderr=b"partial-err"
        )

    monkeypatch.setattr(runner, "_wait_child", timeout_after_launch)
    observed: dict[str, int | str] = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            runner.default_process_runner(
                [sys.executable, "-c", child_code, str(pid_file)],
                env=os.environ.copy(),
                cwd=tmp_path,
                timeout=5.0,
            )
        observed = json.loads(pid_file.read_text(encoding="utf-8"))
        assert caught.value.output == b"partial-out"
        assert caught.value.stderr == b"partial-err"
        assert _proc_state_and_start_time(int(observed["child"])) is None
        _reap_adopted_child(int(observed["grandchild"]))
        assert _proc_state_and_start_time(int(observed["grandchild"])) is None
        assert int(observed["child"]) not in liveness._LIVE_GROUPS
    finally:
        if not observed and pid_file.is_file():
            observed = json.loads(pid_file.read_text(encoding="utf-8"))
        if observed:
            _reap_adopted_child(int(observed["grandchild"]))
        _set_child_subreaper(previous_subreaper)


def test_default_runner_kills_same_group_descendant_after_normal_exit(
    tmp_path: Path
):
    previous_subreaper = _set_child_subreaper(1)
    pid_file = tmp_path / "normal-processes.json"
    child_code = (
        "import json, os, subprocess, sys; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "stat=lambda p: open(f'/proc/{p}/stat').read().rsplit(')',1)[1].split()[19]; "
        "open(sys.argv[1],'w').write(json.dumps({'child':os.getpid(),"
        "'child_start':stat(os.getpid()),'grandchild':child.pid,"
        "'grandchild_start':stat(child.pid)}))"
    )
    observed: dict[str, int | str] = {}
    try:
        result = runner.default_process_runner(
            [sys.executable, "-c", child_code, str(pid_file)],
            env=os.environ.copy(),
            cwd=tmp_path,
            timeout=5.0,
        )
        observed = json.loads(pid_file.read_text(encoding="utf-8"))
        assert result.returncode == 0
        assert _proc_state_and_start_time(int(observed["child"])) is None
        _reap_adopted_child(int(observed["grandchild"]))
        assert _proc_state_and_start_time(int(observed["grandchild"])) is None
        assert int(observed["child"]) not in liveness._LIVE_GROUPS
    finally:
        if not observed and pid_file.is_file():
            observed = json.loads(pid_file.read_text(encoding="utf-8"))
        if observed:
            _reap_adopted_child(int(observed["grandchild"]))
        _set_child_subreaper(previous_subreaper)


def test_termination_before_head_read_uses_unbounded_label_grace(
    git_repo: GitRepo, tmp_path: Path
):
    config = git_repo.write("assay.toml", R0_LANE.replace(
        'argv = ["pytest", "tests/unit", "-q"]', 'argv = ["/bin/true"]'
    ))
    git_repo.commit_all("add lane")
    verdict_path = tmp_path / "termination-verdict.json"
    runner.request_termination()

    code = cli.main(
        ["run", "package", "--file", str(config), "--verdict-json", str(verdict_path)],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert code == Outcome.BUDGET_EXCEEDED.exit_code
    assert verdict["outcome"] == Outcome.BUDGET_EXCEEDED.value
    assert verdict["reason_code"] == ReasonCode.LANE_TIMEOUT.value
    assert verdict["commit"] == git_repo.head()
