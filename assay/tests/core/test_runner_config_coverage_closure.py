"""Whole-source R1 gap closure for runner/config/resource_limits/isolation.

Each test drives one previously unexercised guard and asserts its specific
refusal or returned value.
"""

from __future__ import annotations

import pytest
from conftest import R0_LANE, Project, set_key

from assay.config import load_lane_file
from assay.errors import LaneConfigError

_ISOLATION = '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
_JUDGE = '\n[lanes.package.judge]\nlanguage = "python"\nsource_roots = ["src"]\nbase = "main"\n'


def _mutation_lane(extra: str, *, argv: str | None = None) -> str:
    body = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    if argv is not None:
        body = set_key(body, "argv", argv)
    return (
        body
        + _ISOLATION
        + _JUDGE
        + '\n[lanes.package.judge.mutation]\njobs = 1\nmax_mutants = 50\n'
        'operators = ["python:compare-swap"]\n'
        + extra
    )


# --- as_declared emits the liveness tuning keys -----------------------------


def test_as_declared_carries_liveness_window_and_floor(project: Project):
    path = project.write(
        _mutation_lane('liveness_cpu_window = "30s"\nliveness_idle_floor = "10s"\n')
    )
    mutation = load_lane_file(path).lane("package").judge.mutation
    declared = mutation.as_declared()
    assert declared["liveness_cpu_window"] == "30s"
    assert declared["liveness_idle_floor"] == "10s"
    assert mutation.liveness_cpu_window_s == 30.0
    assert mutation.liveness_idle_floor_s == 10.0


# --- liveness duration validation --------------------------------------------


@pytest.mark.parametrize(
    "extra,message",
    [
        ("liveness_cpu_window = 5\n", "judge.mutation.liveness_cpu_window"),
        ('liveness_cpu_window = "4s"\n', "must be at least 5s"),
        ('liveness_idle_floor = "2h"\n', "must be at most 1h"),
        (
            'liveness = false\nliveness_cpu_window = "30s"\n',
            "has no effect when judge.mutation.liveness = false",
        ),
    ],
)
def test_liveness_tuning_refusals(project: Project, extra: str, message: str):
    with pytest.raises(LaneConfigError, match=message):
        load_lane_file(project.write(_mutation_lane(extra)))


def test_liveness_tuning_on_a_non_pytest_lane_is_refused(project: Project):
    path = project.write(_mutation_lane('liveness_idle_floor = "10s"\n', argv='["tox"]'))
    with pytest.raises(LaneConfigError, match="liveness can never activate"):
        load_lane_file(path)


# --- [defaults] guards -------------------------------------------------------


def test_defaults_must_be_a_table(project: Project):
    text = R0_LANE.replace("schema_version = 2\n", "schema_version = 2\ndefaults = 3\n", 1)
    with pytest.raises(LaneConfigError, match="'defaults' must be a table"):
        load_lane_file(project.write(text))


@pytest.mark.parametrize(
    "table,message",
    [
        ("bogus = 1\n", r"unknown \[defaults\] key\(s\): bogus"),
        ("", "missing required key 'env_passthrough'"),
    ],
)
def test_defaults_table_refusals(project: Project, table: str, message: str):
    text = R0_LANE + "\n[defaults]\n" + table
    with pytest.raises(LaneConfigError, match=message):
        load_lane_file(project.write(text))


# --- file source root traversal ---------------------------------------------


def test_file_source_root_with_dotdot_still_loads_when_no_symlink(project: Project):
    project.file("src/mod.py", "x = 1\n")
    text = _mutation_lane("").replace('source_roots = ["src"]', 'source_roots = ["src/../src/mod.py"]')
    lane = load_lane_file(project.write(text)).lane("package")
    assert lane.judge is not None


def test_non_finite_parsed_liveness_duration_is_refused(
    project: Project, monkeypatch: pytest.MonkeyPatch
):
    # parse_duration itself never yields inf; the guard is defence in depth.
    from assay import config as config_module

    monkeypatch.setattr(config_module, "parse_duration", lambda declared: float("inf"))
    path = project.write(_mutation_lane('liveness_idle_floor = "10s"\n'))
    with pytest.raises(LaneConfigError, match="'judge.mutation.liveness_idle_floor' must be a finite duration"):
        load_lane_file(path)


# --- isolation: iter_paths_under_any ----------------------------------------

import os
import shutil
import socket
import subprocess
from pathlib import Path, PurePosixPath

from assay import resource_limits
from assay.config import IsolationConfig
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec, prepare_snapshot
from assay.resource_limits import ResourceLimitObservationError


def _tiny_repo(tmp_path: Path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.invalid",
    }
    git = shutil.which("git") or "git"

    def run(*args: str) -> str:
        done = subprocess.run(
            [git, "-C", str(repo), *args], env=env, capture_output=True, check=True
        )
        return done.stdout.decode().strip()

    run("init", "-q")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("a = 1\n")
    (repo / "b.py").write_text("b = 1\n")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return SnapshotSpec(
        repo_top=repo.resolve(),
        commit=run("rev-parse", "HEAD"),
        project_prefix=PurePosixPath("."),
        scratch_root=scratch.resolve(),
        snapshot_policy=IsolationConfig(
            snapshot_selection="repository", unsafe_symlink_omissions=()
        ),
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )


def test_iter_paths_under_any_roots(tmp_path: Path) -> None:
    with prepare_snapshot(_tiny_repo(tmp_path), timeout=600.0) as prepared:
        # an exact-leaf root yields that leaf and nothing else
        assert list(prepared.iter_paths_under_any((PurePosixPath("b.py"),))) == [
            PurePosixPath("b.py")
        ]
        with pytest.raises(ValueError, match="root must be PurePosixPath"):
            list(prepared.iter_paths_under_any(("b.py",)))  # type: ignore[arg-type]
        ticks: list[int] = []
        assert list(prepared.iter_paths_under_any((), check_deadline=lambda: ticks.append(1) or 0.0)) == []
        assert ticks == [1]
        assert list(prepared.iter_paths_under_any(())) == []


# --- resource_limits ---------------------------------------------------------


def _cgroup_files(tmp_path: Path, mount_line: str) -> tuple[Path, Path]:
    cgroup = tmp_path / "proc-cgroup"
    cgroup.write_text("0::/worker/lane\n", encoding="utf-8")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(mount_line + "\n", encoding="utf-8")
    return cgroup, mountinfo


def test_cgroup2_mount_without_superblock_options_is_malformed(tmp_path: Path) -> None:
    cgroup, mountinfo = _cgroup_files(
        tmp_path, f"31 23 0:28 / {tmp_path} ro - cgroup2 cgroup"
    )
    with pytest.raises(ResourceLimitObservationError, match="malformed cgroup mount record"):
        resource_limits.read_current_cgroup_counters(
            cgroup_file=cgroup, mountinfo_file=mountinfo
        )


def test_unscannable_candidate_directory_cannot_prove_leaf(tmp_path: Path) -> None:
    with pytest.raises(ResourceLimitObservationError, match="cannot prove the localevents candidate"):
        resource_limits._require_no_candidate_subgroups(tmp_path / "absent")


def test_local_events_without_regular_events_refuse() -> None:
    with pytest.raises(ResourceLimitObservationError, match="pids.events.local is present without"):
        resource_limits._effective_local_events(
            None,
            {"max": 1},
            regular_is_local=False,
            required=frozenset({"max"}),
            controller="pids",
        )


def test_observer_socket_missing_then_appears_as_non_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "obs.sock"
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        target.write_text("not a socket")

    monkeypatch.setattr(resource_limits.time, "sleep", fake_sleep)
    with pytest.raises(ResourceLimitObservationError, match="not a Unix socket"):
        resource_limits.read_resource_observer_counters(str(target))
    assert len(sleeps) == 1


def test_observer_request_deadline_expiry_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "o.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(target))
        monkeypatch.setattr(resource_limits, "_RESOURCE_OBSERVER_REQUEST_TIMEOUT_SECONDS", 0.0)
        with pytest.raises(ResourceLimitObservationError, match="request timed out"):
            resource_limits.read_resource_observer_counters(str(target))
    finally:
        server.close()


# --- runner: small guards ----------------------------------------------------

from datetime import datetime, timezone
from types import SimpleNamespace

from conftest import completed_process, fixed_clock, make_lane

from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
from assay.process_tree import ProcessTreeSpawnError

_NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 8, 7, 12, 0, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "30"])
def test_campaign_deadline_refuses_a_non_finite_monotonic_clock(bad) -> None:
    lane_deadline = runner.LaneDeadline(expires_at=75.0, monotonic=lambda: 30.0)
    with pytest.raises(ValueError, match="monotonic_now must be a finite real number"):
        runner.campaign_bounded_deadline(
            lane_deadline, expires_at_utc=_LATER, wall_now=_NOW, monotonic_now=bad
        )


def test_timeout_cleanup_survives_a_failing_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []

    class FakeProc:
        pid = 4242
        args = ["x"]
        returncode = None

        def wait(self):
            raise OSError("reap failed")

    def timing_out(proc, timeout):
        raise subprocess.TimeoutExpired(["x"], 1.0)

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(runner, "_wait_child", timing_out)
    monkeypatch.setattr(runner.liveness, "register_live_group", lambda pid: None)
    monkeypatch.setattr(runner.liveness, "_killpg_group", lambda pid, sig: killed.append(pid))
    with pytest.raises(subprocess.TimeoutExpired):
        runner.default_process_runner(["x"], env={}, cwd=Path("."), timeout=1.0)
    assert killed and set(killed) == {4242}


def test_process_tree_spawn_error_is_a_candidate_level_exec_failure(tmp_path: Path) -> None:
    def spawn_fails(argv, *, env, cwd, timeout):
        error = ProcessTreeSpawnError(2, "No such file")
        error.process_tree_evidence = {"leader_status": "not-started"}
        raise error

    result = runner.execute_command(
        make_lane(argv=("/bin/true",)),
        cwd=tmp_path,
        process_runner=spawn_fails,
        clock=fixed_clock(_NOW, _LATER),
    )
    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert result.stdout_tail == "" and result.stderr_tail == ""
    assert result.process_tree_evidence == {"leader_status": "not-started"}


def test_outliving_descendants_turn_a_zero_exit_into_exec_failed(tmp_path: Path) -> None:
    def leaves_orphans(argv, *, env, cwd, timeout):
        proc = completed_process(argv, 0, stdout="out-text", stderr="err-text")
        proc.process_tree_evidence = {"outliving_descendants": 2, "clean": False}
        return proc

    result = runner.execute_command(
        make_lane(argv=("/bin/true",)),
        cwd=tmp_path,
        process_runner=leaves_orphans,
        clock=fixed_clock(_NOW, _LATER),
    )
    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert result.returncode is None
    assert result.stdout_tail == "out-text"
    assert result.stderr_tail == "err-text"
    assert result.process_tree_evidence == {"outliving_descendants": 2, "clean": False}


def test_absent_source_roots_have_no_exact_file_keys_and_nothing_to_require(tmp_path: Path) -> None:
    assert runner._source_root_file_keys(None, repo_top=tmp_path, project_root=tmp_path) == ()
    assert (
        runner._require_exact_source_roots_tracked(
            repo=tmp_path,
            commit="0" * 40,
            project_root=tmp_path,
            project_prefix=PurePosixPath("."),
            source_roots=None,
        )
        is None
    )


def test_mutation_context_root_outside_the_prepared_repository_is_refused(tmp_path: Path) -> None:
    adapter = SimpleNamespace(prepare_mutation_context=lambda *a, **k: None)
    with pytest.raises(AssayError, match="outside the prepared repository") as failure:
        runner._mutation_context_sources(
            prepared=None,  # type: ignore[arg-type]
            snapshot_repo_top=tmp_path / "snapshot",
            source_root_paths=[tmp_path / "elsewhere"],
            adapter=adapter,  # type: ignore[arg-type]
            deadline=runner.LaneDeadline(expires_at=75.0, monotonic=lambda: 30.0),
        )
    assert failure.value.reason_code is ReasonCode.BAD_LANE_CONFIG


# --- runner: cold-witness R2 baseline branches (real CLI, stubbed seams) -----

import dataclasses
import io
import json
import sys

from conftest import GitRepo
from test_b114_cold_witness_real_runs import _seed_campaign

from assay import cli, mutation, safeio
from assay import runner as runner_module


class _Proxy:
    """Delegate to *base*, overriding only the named attributes."""

    def __init__(self, base, **overrides):
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)


def _cold(repo: GitRepo, config: Path, tmp_path: Path, *, manifest: bool = True):
    (repo.path / ".assay").mkdir(exist_ok=True)
    verdict = repo.path / ".assay/verdict.json"
    argv = [
        "run", "package", "--file", str(config), "--cold-witness",
        "--progress", str(repo.path / ".assay/progress.jsonl"),
        "--state-dir", str(repo.path / ".assay/state"),
        "--verdict-json", str(verdict),
    ]
    manifest_path = repo.path / ".assay/r2-manifest.txt"
    if manifest:
        argv += ["--r2-manifest", str(manifest_path)]
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdout=stdout, stderr=stderr)
    return code, stderr.getvalue(), verdict, manifest_path


def _r2(verdict: Path) -> dict:
    document = json.loads(verdict.read_text(encoding="utf-8"))
    return next(claim for claim in document["claims"] if claim["rigor"] == "R2")


@pytest.fixture
def cold_repo(git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    attempt_log = tmp_path / "attempts.log"
    config = _seed_campaign(git_repo, attempt_log, max_mutants=3)
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))
    return git_repo, config


def _intercept_r2_unit(monkeypatch: pytest.MonkeyPatch, transform) -> None:
    real = runner_module._execute_snapshot_unit

    def wrapper(**kwargs):
        unit = real(**kwargs)
        if kwargs.get("progress_phase") == "r2-baseline":
            return transform(unit)
        return unit

    monkeypatch.setattr(runner_module, "_execute_snapshot_unit", wrapper)


def _refused_with(verdict: Path, fragment: str) -> None:
    claim = _r2(verdict)
    assert claim["status"] == "ERROR"
    assert claim["reason_code"] == "BAD_LANE_CONFIG"
    assert fragment in claim["detail"]


@pytest.mark.parametrize(
    "outcome,reason,fragment",
    [
        (Outcome.FAIL, ReasonCode.COMMAND_FAILED, "no-coverage R2 baseline did not pass"),
    ],
)
def test_r2_baseline_that_does_not_pass_is_refused(
    cold_repo, tmp_path, monkeypatch, outcome, reason, fragment
):
    repo, config = cold_repo
    _intercept_r2_unit(
        monkeypatch,
        lambda unit: _Proxy(
            unit,
            result=dataclasses.replace(unit.result, outcome=outcome, reason_code=reason),
        ),
    )
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, fragment)


def test_r2_baseline_reaching_the_lane_deadline_is_a_budget_timeout(
    cold_repo, tmp_path, monkeypatch
):
    repo, config = cold_repo
    _intercept_r2_unit(
        monkeypatch,
        lambda unit: _Proxy(
            unit,
            result=dataclasses.replace(
                unit.result,
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
            ),
        ),
    )
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    claim = _r2(verdict)
    assert claim["status"] == "BUDGET_EXCEEDED"
    assert "reached the lane deadline" in claim["detail"]


def test_r2_baseline_assay_lane_timeout_is_converted_to_a_baseline_timeout(
    cold_repo, tmp_path, monkeypatch
):
    repo, config = cold_repo
    real = runner_module._execute_snapshot_unit

    def wrapper(**kwargs):
        if kwargs.get("progress_phase") == "r2-baseline":
            raise AssayError(
                "slow",
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
            )
        return real(**kwargs)

    monkeypatch.setattr(runner_module, "_execute_snapshot_unit", wrapper)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    claim = _r2(verdict)
    assert claim["status"] == "BUDGET_EXCEEDED"
    assert "ran out of lane time" in claim["detail"]


@pytest.mark.parametrize(
    "reason,detail_fragment",
    [
        (ReasonCode.DIRTY_TREE, "left a dirty Git worktree"),
        (ReasonCode.HEAD_CHANGED, "changed HEAD"),
    ],
)
def test_r2_baseline_post_reason_is_typed_and_reason_specific(
    cold_repo, tmp_path, monkeypatch, reason, detail_fragment,
):
    # The R2 baseline's own post-run Git check (dirty tree / moved HEAD) is a
    # NO_MEASUREMENT AssayError carrying that reason, never a bare TypeError from
    # raising a ReasonCode.
    repo, config = cold_repo
    _intercept_r2_unit(
        monkeypatch, lambda unit: _Proxy(unit, post_reason=reason)
    )
    code, stderr, verdict, _manifest = _cold(repo, config, tmp_path)
    assert code == Outcome.NO_MEASUREMENT.exit_code, stderr
    claim = _r2(verdict)
    assert claim["status"] == "NO_MEASUREMENT"
    assert claim["reason_code"] == reason.value
    assert detail_fragment in claim["detail"]
    assert "BaseException" not in stderr


def _fail_nth_injection(monkeypatch: pytest.MonkeyPatch, nth: int) -> None:
    real = runner_module.inject_witness_plugin
    calls: list[int] = []

    def wrapper(*args, **kwargs):
        calls.append(1)
        injected = real(*args, **kwargs)
        if len(calls) == nth:
            return dataclasses.replace(injected, active=False)
        return injected

    monkeypatch.setattr(runner_module, "inject_witness_plugin", wrapper)


@pytest.mark.parametrize(
    "nth,fragment",
    [
        (1, "the coverage command cannot load the trusted pytest receipt plugin"),
        (2, "the R2 baseline cannot load the trusted pytest receipt plugin"),
    ],
)
def test_inactive_witness_plugin_injection_is_refused(
    cold_repo, tmp_path, monkeypatch, nth, fragment
):
    repo, config = cold_repo
    _fail_nth_injection(monkeypatch, nth)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, fragment)


def test_non_sequential_coverage_baseline_command_is_not_eligible(
    cold_repo, tmp_path, monkeypatch
):
    repo, config = cold_repo
    monkeypatch.setattr(runner_module, "supports_sequential_pytest", lambda *a, **k: False)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, "cold witness is not eligible: not a sequential pytest command")


def test_non_sequential_r2_command_under_committed_config_is_refused(
    cold_repo, tmp_path, monkeypatch
):
    repo, config = cold_repo
    monkeypatch.setattr(mutation, "supports_sequential_pytest", lambda *a, **k: False)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, "the R2 command is not sequential")


def test_r2_baseline_collection_differing_from_coverage_baseline_is_refused(
    cold_repo, tmp_path
):
    repo, config = cold_repo
    repo.write(
        "tests/test_cov_only.py",
        "import coverage\n\nif coverage.Coverage.current() is not None:\n"
        "    def test_present_only_in_the_coverage_baseline():\n        pass\n",
    )
    repo.commit_all("drop one test from the no-coverage baseline only")
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(
        verdict, "no-coverage R2 baseline proof failed: the no-coverage R2 baseline test collection differs"
    )


@pytest.mark.parametrize(
    "served,fragment",
    [
        (None, "the no-coverage R2 baseline manifest is unreadable"),
        (b"pkg::a", "the no-coverage R2 baseline manifest is unreadable"),
        (b"\xff\xfe\n", "the no-coverage R2 baseline manifest is unreadable"),
        (b"only::one\n", "the no-coverage R2 manifest digest does not match its receipt"),
    ],
)
def test_r2_manifest_sidecar_defects_are_refused(
    cold_repo, tmp_path, monkeypatch, served, fragment
):
    repo, config = cold_repo
    real = safeio.read_bounded_file

    def wrapper(root, artifact, **kwargs):
        if artifact == "r2-manifest.txt":
            return served
        return real(root, artifact, **kwargs)

    monkeypatch.setattr(runner_module.safeio, "read_bounded_file", wrapper)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, fragment)


def test_r2_manifest_destination_that_cannot_be_created_is_a_write_error(
    cold_repo, tmp_path
):
    repo, config = cold_repo
    (repo.path / ".assay").mkdir(exist_ok=True)
    blocker = repo.path / ".assay/blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    (repo.path / ".assay").mkdir(exist_ok=True)
    verdict = repo.path / ".assay/verdict.json"
    code = cli.main(
        [
            "run", "package", "--file", str(config), "--cold-witness",
            "--r2-manifest", str(blocker / "manifest.txt"),
            "--state-dir", str(repo.path / ".assay/state"),
            "--verdict-json", str(verdict),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code != 0
    claim = _r2(verdict)
    assert claim["status"] == "ERROR"
    assert "could not atomically write R2 manifest" in claim["detail"]


@pytest.mark.parametrize("temp_already_gone", [True, False])
def test_r2_manifest_write_failure_releases_descriptor_and_temp_file(
    cold_repo, tmp_path, monkeypatch, temp_already_gone
):
    repo, config = cold_repo
    real = os.fdopen
    closed: list[int] = []
    real_close = os.close

    def fake_fdopen(fd, *args, **kwargs):
        temps = list((repo.path / ".assay").glob(".assay-r2-manifest-*"))
        if not temps:
            return real(fd, *args, **kwargs)
        if temp_already_gone:
            for temp in temps:
                temp.unlink()
        fake_fdopen.temps = temps
        raise OSError("fdopen failed")

    fake_fdopen.temps = []

    def tracking_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "fdopen", fake_fdopen)
    monkeypatch.setattr(runner_module.os, "close", tracking_close)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    assert "could not atomically write R2 manifest" in _r2(verdict)["detail"]
    assert fake_fdopen.temps and closed
    assert not list((repo.path / ".assay").glob(".assay-r2-manifest-*"))


def test_cold_witness_without_a_manifest_destination_still_completes_the_baseline(
    cold_repo, tmp_path
):
    repo, config = cold_repo
    code, _stderr, verdict, manifest_path = _cold(repo, config, tmp_path, manifest=False)
    assert code in (0, 1), _stderr
    assert not manifest_path.exists()
    assert _r2(verdict)["mutation"] is not None


def test_cold_shape_refusal_blocks_the_lane_before_any_baseline(cold_repo, tmp_path):
    repo, config = cold_repo
    text = config.read_text(encoding="utf-8")
    edited = text.replace('env = { PYTHONPATH', 'env = { PYTEST_ADDOPTS = "-q", PYTHONPATH', 1)
    assert edited != text
    config.write_text(edited, encoding="utf-8")
    repo.commit_all("pytest addopts in lane env")
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, "cold witness is not eligible: pytest environment option")


def test_r2_manifest_without_cold_witness_is_refused(cold_repo, tmp_path, monkeypatch):
    # The CLI refuses this combination itself; the runner re-checks it so a
    # direct caller cannot bypass the guard.
    repo, config = cold_repo
    (repo.path / ".assay").mkdir(exist_ok=True)
    monkeypatch.setattr(
        cli, "_resolve_r2_manifest", lambda args, root: repo.path / ".assay/m.txt"
    )
    verdict = repo.path / ".assay/verdict.json"
    code = cli.main(
        ["run", "package", "--file", str(config), "--verdict-json", str(verdict)],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code != 0
    claim = json.loads(verdict.read_text(encoding="utf-8"))["claims"][0]
    assert claim["reason_code"] == "BAD_LANE_CONFIG"
    assert "--r2-manifest requires --cold-witness" in claim["detail"]


def test_cold_witness_without_its_plugin_directory_is_refused(
    cold_repo, tmp_path, monkeypatch
):
    repo, config = cold_repo
    real = runner_module._run_prepared_lane

    def without_witness_dir(*args, **kwargs):
        kwargs["witness_dir"] = None
        return real(*args, **kwargs)

    monkeypatch.setattr(runner_module, "_run_prepared_lane", without_witness_dir)
    code, _stderr, verdict, _m = _cold(repo, config, tmp_path)
    assert code != 0
    _refused_with(verdict, "the lane-run plugin directory is unavailable")


def test_cold_witness_on_a_non_r2_lane_is_refused(git_repo: GitRepo, tmp_path):
    config = git_repo.write(
        "assay.toml",
        "schema_version = 2\n\n[lanes.package]\nscope = \"S1\"\nrigor = [\"R0\"]\n"
        "enforcement = \"gate\"\n"
        f"argv = [{json.dumps(sys.executable)}, \"-c\", \"pass\"]\n"
        "env = {}\nenv_passthrough = [\"PATH\"]\nbudget = \"5m\"\nallow_argv_append = false\n",
    )
    git_repo.commit_all("r0 lane")
    verdict = tmp_path / "verdict.json"
    code = cli.main(
        ["run", "package", "--file", str(config), "--cold-witness",
         "--verdict-json", str(verdict)],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code != 0
    claim = json.loads(verdict.read_text(encoding="utf-8"))["claims"][0]
    assert claim["reason_code"] == "BAD_LANE_CONFIG"
    assert "--cold-witness requires a native Python R2 lane" in claim["detail"]
