from __future__ import annotations

import json
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pytest

from conftest import (
    GitRepo,
    Project,
    make_deadline,
    make_lane,
    make_plan,
    make_r2_judge,
    prepared_snapshot,
)

from assay.adapters.python import PythonAdapter
from assay.config import LaneConfigError, MutationConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay import liveness
from assay import mutation
from assay.mutation import (
    Mutation,
    MutantOutcome,
    MutationTarget,
    collect_mutation_sites,
    run_mutation,
)
from assay import mutation as mutation_module
from assay.runner import CommandResult, execute_command


_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    return a, b\n"
)
_TARGETS = (
    MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3})),
)


def _repo(tmp_path):
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    return repo


def test_progress_events_are_emitted_for_baseline_and_every_candidate(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        if "b = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        raise AssertionError(f"unexpected content: {text!r}")

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
        )

    assert result is not None and not isinstance(result, str)
    lines = progress_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    # B031/A-320: a `run` header opens the stream. The file is opened for
    # APPEND and never truncated, so without a per-run commit/timestamp a
    # tailing monitor cannot attribute a `candidate_index: 0` to a run.
    assert events[0]["event"] == "run"
    assert len(events[0]["commit"]) == 40
    assert events[0]["started"].startswith("20")
    # (B065) The header is emitted at stream OPEN, before a snapshot exists
    # and before the sites have been collected, so `candidate_total` is
    # honestly `null` there -- a header deferred until the total is known is
    # a header that arrives after the records it exists to attribute. The
    # real total arrives on `candidates`, the moment it IS known.
    assert events[0]["candidate_total"] is None
    # `lane` is `null` on this path and only on this path: a direct
    # `run_mutation` call has no Lane in view, which is the reader's signal
    # that the header's lane-level bounds are UNKNOWN, not unbounded.
    assert events[0]["lane"] is None
    # (B091/D-23) The sweep's OWN first record, ahead of `candidates`: what
    # the baseline actually measured, and the per-candidate bound it
    # produced. This lane passed no `budget_per_candidate_seconds` and no
    # `budget_per_candidate_auto`, so the legacy shape applies -- nothing
    # derived, nothing bounded.
    assert events[1]["event"] == "plan"
    assert events[1]["baseline_s"] >= 0.0
    assert events[1]["budget_per_candidate_s"] is None
    assert events[1]["derived"] is False
    # (B091/D-23, P7 A4) Neither of the two liveness measurements nor the
    # baseline's own liveness events path was passed at all -- both new
    # `plan` fields are honestly `None`, and no `test` event is forwarded
    # (a lane that never wires liveness must never fabricate baseline test
    # detail it does not have).
    assert events[1]["slowest_test_s"] is None
    assert events[1]["expect_next_event_within_s"] is None
    assert not any(event["event"] == "test" for event in events)
    assert events[2] == {
        **events[2],
        "event": "candidates",
        "candidate_total": 2,
        "selected_total": 2,
        "pending_total": 2,
    }
    assert events[-1]["event"] == "end"
    assert events[-1]["buckets"]["killed"] == 1
    assert events[-1]["buckets"]["survived"] == 1
    events = events[3:-1]
    assert [event["candidate_index"] for event in events] == [-1, 0, 1]
    assert all(event["candidate_total"] == 2 for event in events[1:])
    assert events[0]["event"] == "baseline"
    for index, event in enumerate(events[1:], start=0):
        assert event["path"] == "pkg/flags.py"
        assert len(event["candidate_id"]) == 64
        assert event["operator"] == "python:bool-const-flip"
        # B031/A-320: this is the WHOLE MUTATED FILE's digest, and is named
        # for what it is. Under `replacement_sha256` it collided with the
        # verdict's own same-named field, which digests the replacement TEXT.
        assert event["mutated_file_sha256"]
        assert "replacement_sha256" not in event
        assert isinstance(event["elapsed_seconds"], float)
        # (B091/D-23, P7 A4) `liveness_events_dir` was never passed either
        # -- `tests_completed` is honestly `None`, not `0` (a `0` would
        # claim liveness ran and genuinely saw no test).
        assert event["tests_completed"] is None
    assert events[1]["outcome_bucket"] == "killed"
    assert events[2]["outcome_bucket"] == "survived"


# --- B091/D-23, P7 A4: progress stream gains per-test liveness detail -----


def test_plan_event_reports_slowest_test_s_and_expect_next_event_within_s(
    tmp_path,
):
    """(B091/D-23, P7 A4) Both new `plan` fields are `run_mutation`'s own
    caller-supplied facts, READ BACK verbatim onto the wire -- never
    recomputed here (that computation belongs to `assay.liveness.
    baseline_slowest_test_s`/`compute_expect_next_event_within_s`, called
    once by the caller, per BRIEF-5's own "read it back rather than
    re-deriving" instruction).
    """
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            liveness_slowest_test_s=6.0,
            liveness_expect_next_event_within_s=18.0,
        )
    assert result is not None and not isinstance(result, str)
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    plan_event = next(event for event in events if event["event"] == "plan")
    assert plan_event["slowest_test_s"] == 6.0
    assert plan_event["expect_next_event_within_s"] == 18.0


def test_baseline_test_events_are_forwarded_right_after_plan_never_per_candidate(
    tmp_path,
):
    """(B091/D-23, P7 A4, RW-33) Every `test` event in the BASELINE's own
    liveness side file is translated onto the progress stream, verbatim,
    `phase: "baseline"`, in file order, right after `plan` and before
    `candidates` -- a `session_finish` record and a torn last line are both
    silently excluded, and no candidate's own execution ever emits a `test`
    event of its own (RW-33: baseline only).
    """
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"
    baseline_events_path = tmp_path / "baseline.ndjson"
    baseline_events_path.write_text(
        '{"event": "test", "nodeid": "pkg/test_a.py::test_one", "outcome": "passed", "duration_s": 0.5, "t": 0}\n'
        '{"event": "test", "nodeid": "pkg/test_a.py::test_two", "outcome": "failed", "duration_s": 5.0, "t": 1}\n'
        '{"event": "session_finish", "exitstatus": 0, "t": 2}\n'
        '{"event": "test", "nodeid": "pkg/test_a.py::test_thr',  # torn last line
        encoding="utf-8",
    )

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            liveness_baseline_events_path=baseline_events_path,
        )
    assert result is not None and not isinstance(result, str)
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    plan_index = next(
        index for index, event in enumerate(events) if event["event"] == "plan"
    )
    candidates_index = next(
        index for index, event in enumerate(events) if event["event"] == "candidates"
    )
    forwarded = events[plan_index + 1 : candidates_index]
    assert [event["event"] for event in forwarded] == ["test", "test"]
    assert [event["nodeid"] for event in forwarded] == [
        "pkg/test_a.py::test_one",
        "pkg/test_a.py::test_two",
    ]
    assert [event["phase"] for event in forwarded] == ["baseline", "baseline"]
    assert forwarded[0]["outcome"] == "passed"
    assert forwarded[0]["duration_s"] == 0.5
    assert forwarded[1]["outcome"] == "failed"
    assert forwarded[1]["duration_s"] == 5.0
    # RW-33: baseline only -- no candidate record carries a "test" event.
    candidate_events = [event for event in events if event["event"] == "candidate"]
    assert len(candidate_events) == 2
    assert all(event["event"] != "test" for event in candidate_events)


def test_candidate_progress_event_gains_tests_completed_from_its_own_events_file(
    tmp_path,
):
    """(B091/D-23, P7 A4) `tests_completed` is read back, per candidate,
    from that candidate's own liveness side file -- located via
    `assay.liveness.candidate_events_path`, the SAME free function a real
    `LivenessRunner` uses to name where IT writes, so this reader and that
    writer can never disagree about the path. This fake `process_runner`
    stands in for `LivenessRunner` and writes to the identical path a real
    one would, keyed off the SAME `cwd` `execute_plan` hands it -- proving
    the reader/writer path agreement, not just the counting logic alone.
    """
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"
    events_dir = tmp_path / "liveness-candidates"

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        killed = "a = False" in text
        events_path = liveness.candidate_events_path(events_dir, Path(cwd))
        events_path.parent.mkdir(parents=True, exist_ok=True)
        test_count = 2 if killed else 1
        with events_path.open("w", encoding="utf-8") as stream:
            for index in range(test_count):
                stream.write(
                    json.dumps(
                        {
                            "event": "test",
                            "nodeid": f"pkg/test_flags.py::test_{index}",
                            "outcome": "failed" if killed else "passed",
                            "duration_s": 0.1,
                        }
                    )
                    + "\n"
                )
            stream.write(json.dumps({"event": "session_finish", "exitstatus": 0}) + "\n")
        return subprocess.CompletedProcess(
            list(argv), returncode=1 if killed else 0
        )

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            liveness_events_dir=events_dir,
        )
    assert result is not None and not isinstance(result, str)
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    candidate_events = [event for event in events if event["event"] == "candidate"]
    assert len(candidate_events) == 2
    by_bucket = {
        event["outcome_bucket"]: event["tests_completed"] for event in candidate_events
    }
    assert by_bucket == {"killed": 2, "survived": 1}
    # A session_finish record must never be counted as a test.
    assert set(by_bucket.values()) == {2, 1}


def test_tests_completed_is_read_from_the_resolved_run_cwd_on_a_lane_declaring_cwd(
    tmp_path,
):
    """(B091 round-1 blocker B3) The regression the sibling test above is
    structurally incapable of seeing: it uses a lane with no `cwd`, where
    the writer's key (`resolve_run_cwd(project_root, plan)`, what
    `execute_plan` hands `LivenessRunner`) and the reader's old key
    (`snapshot.project_root`) coincide by accident.

    With `cwd = "sub"` declared they are two different directories. Before
    the fix the reader looked for a file the writer never wrote, and
    `count_test_events` returned `0` for EVERY candidate -- the one value
    `run_mutation`'s own contract and CONSUMERS' `candidate` row define as
    the opposite fact ("the plugin ran and genuinely saw no test"), so the
    defect reported a measurement it had not made.

    The fake `process_runner` writes to `liveness.candidate_events_path(
    events_dir, Path(cwd))` -- the SAME free function, fed the SAME `cwd`
    a real `LivenessRunner` is handed -- so this asserts reader/writer
    path AGREEMENT, not merely that some counter counted.
    """
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    # Repo-top-relative, exactly like `MutationTarget.path` -- independent
    # of `cwd_declared`, which only moves where the COMMAND runs.
    repo.write("sub/pkg/flags.py", _TEXT)
    repo.commit_all("add flags under sub/")
    targets = (
        MutationTarget(path="sub/pkg/flags.py", text=_TEXT, lines=frozenset({2, 3})),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"
    events_dir = tmp_path / "liveness-candidates"
    seen_cwds: list[Path] = []

    def decide(argv, *, env, cwd, timeout):
        seen_cwds.append(Path(cwd))
        if Path(cwd) == repo.path / "sub":
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        killed = "a = False" in text
        events_path = liveness.candidate_events_path(events_dir, Path(cwd))
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("w", encoding="utf-8") as stream:
            for index in range(2 if killed else 1):
                stream.write(
                    json.dumps(
                        {
                            "event": "test",
                            "nodeid": f"pkg/test_flags.py::test_{index}",
                            "outcome": "failed" if killed else "passed",
                            "duration_s": 0.1,
                        }
                    )
                    + "\n"
                )
            stream.write(json.dumps({"event": "session_finish", "exitstatus": 0}) + "\n")
        return subprocess.CompletedProcess(list(argv), returncode=1 if killed else 0)

    lane = make_lane(argv=("pytest", "-q"), cwd="sub")
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=targets,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            liveness_events_dir=events_dir,
        )
    assert result is not None and not isinstance(result, str)
    # The lane really did run one directory down, for the baseline and for
    # every candidate -- otherwise this test would pass for the wrong reason.
    assert all(path.name == "sub" for path in seen_cwds)
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    candidate_events = [event for event in events if event["event"] == "candidate"]
    assert len(candidate_events) == 2
    by_bucket = {
        event["outcome_bucket"]: event["tests_completed"] for event in candidate_events
    }
    assert by_bucket == {"killed": 2, "survived": 1}


def test_progress_writer_refuses_a_directory_destination_with_output_write_failed(
    tmp_path,
):
    """B031/A-320 round 2, blocker 2. A bad `--progress` destination -- most
    commonly an existing directory, which `--progress ""` resolves to (the
    CWD is itself a directory) -- used to raise a bare `IsADirectoryError`
    from `path.open("a")` here. Uncaught, that escaped as a plain `OSError`
    all the way up through `run_mutation` to `runner.run_lane`'s broad
    `except OSError:`, which relabels ANY escaped OSError as
    `ERROR`/`GIT_FAILED` -- a cause that has nothing to do with what
    actually happened; the exact mislabelled-cause class B032 was filed to
    close, reopened on this new flag. `progress_writer` now raises the same
    typed refusal `--verdict-json` gives for the identical mistake, naming
    the path -- so a consumer who calls it directly (never through the
    CLI's own early `validate_progress_destination` preflight, see
    test_environment_preflight.py) still gets an honest cause.
    """
    directory = tmp_path / "a-directory"
    directory.mkdir()

    with pytest.raises(AssayError) as excinfo:
        with mutation.progress_writer(directory):
            pass  # pragma: no cover - never reached; open() itself refuses

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert str(directory) in str(excinfo.value)


def test_progress_writer_refuses_when_its_parent_cannot_be_created(tmp_path):
    """Sibling of the directory-destination case above: the OTHER OSError
    site in `progress_writer` (`path.parent.mkdir(parents=True,
    exist_ok=True)`, needed because -- unlike `--verdict-json` -- a progress
    destination's parent tree is created on demand) gets the same typed
    refusal, not a bare `NotADirectoryError`.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    bad_path = blocker / "child" / "progress.jsonl"

    with pytest.raises(AssayError) as excinfo:
        with mutation.progress_writer(bad_path):
            pass  # pragma: no cover - never reached; mkdir() itself refuses

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert str(bad_path) in str(excinfo.value)


def test_resume_reuses_completed_records_without_rerunning(tmp_path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"
    lane = make_lane(argv=("pytest", "-q"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    calls: list[str] = []

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        calls.append(text)
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        first = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            state_root=state_root,
            resume=True,
        )
    assert first.total == 2
    # (B066) records live directly under the caller's own state root now.
    assert len(list(state_root.glob("*.json"))) == 2
    assert len(calls) == 2

    calls.clear()
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        resumed = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("completed candidates must not run again")
            ),
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=None,
            state_root=state_root,
            resume=True,
        )

    assert calls == []
    assert resumed.total == 2
    assert {item.identity for item in first.killed} == {
        item.identity for item in resumed.killed
    }
    assert {item.identity for item in first.survived} == {
        item.identity for item in resumed.survived
    }


# --- B091/D-23, P7 A5: --rejudge / --rejudge-outcome -----------------------


def _seed_resumed_state(tmp_path, state_root, progress_path=None):
    """The exact two-candidate setup `test_resume_reuses_completed_records_
    without_rerunning` uses (one killed via `a = False`, one survived via
    `b = False`), factored out so every rejudge test below shares it rather
    than re-deriving the fixture. Returns `(repo, lane, scratch, baseline,
    first_result)`.
    """
    repo = _repo(tmp_path)
    lane = make_lane(argv=("pytest", "-q"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        first = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            state_root=state_root,
            resume=True,
        )
    assert first.total == 2
    assert len(first.killed) == 1 and len(first.survived) == 1
    return repo, lane, scratch, baseline, first


def _candidate_id_by_outcome_bucket(state_root: Path, outcome_bucket: str) -> str:
    """The persisted `candidate_id` (a sha256 hex digest -- what `--rejudge`
    actually takes) of the ONE record in *state_root* whose own
    `outcome_bucket` matches. `MutantOutcome.identity` is a DIFFERENT,
    tuple-shaped identity (path/span/hash/operator) -- not the digest
    string `run_mutation`'s resume mechanism keys records by -- so this
    reads the real candidate id back from the record itself rather than
    guessing at a conversion between the two.
    """
    matches = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    matches = [record for record in matches if record["outcome_bucket"] == outcome_bucket]
    assert len(matches) == 1, matches
    return matches[0]["candidate_id"]


def test_rejudge_ids_drops_only_the_named_record_and_reexecutes_it(tmp_path):
    """(B091/D-23, P7 A5) `--rejudge <id>` drops exactly the named
    candidate's resume record before the store is consulted -- it
    re-executes against the CURRENT judging suite, proven here by a suite
    that now kills what the FIRST run recorded as `survived`, while the
    other, un-rejudged candidate resumes without re-executing at all.
    """
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    repo, lane, scratch, baseline, first = _seed_resumed_state(tmp_path, state_root)
    survived_id = _candidate_id_by_outcome_bucket(state_root, "survived")

    calls: list[str] = []

    def strengthened(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        calls.append(text)
        # A genuine test-suite strengthening: BOTH mutants would now be
        # killed if actually re-executed -- proving this is real execution,
        # never a replayed stale verdict.
        return subprocess.CompletedProcess(list(argv), returncode=1)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        rejudged = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=strengthened,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
            rejudge_ids=frozenset({survived_id}),
        )

    # Exactly one candidate re-executed -- the rejudged one.
    assert len(calls) == 1
    assert "b = False" in calls[0]
    assert rejudged.total == 2
    # The un-rejudged (killed) candidate resumed as-is; the rejudged one
    # (previously survived) is now ALSO killed, by real re-execution.
    assert len(rejudged.killed) == 2
    assert len(rejudged.survived) == 0
    assert {item.identity for item in first.killed}.issubset(
        {item.identity for item in rejudged.killed}
    )


def test_rejudge_outcome_drops_records_matching_the_named_bucket(tmp_path):
    """The same drop as `--rejudge`, selected by the record's own
    persisted `outcome_bucket` instead of an explicit id.
    """
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    repo, lane, scratch, baseline, first = _seed_resumed_state(tmp_path, state_root)

    calls: list[str] = []

    def strengthened(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        calls.append(text)
        return subprocess.CompletedProcess(list(argv), returncode=1)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        rejudged = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=strengthened,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
            rejudge_outcomes=frozenset({"survived"}),
        )

    assert len(calls) == 1
    assert "b = False" in calls[0]
    assert len(rejudged.killed) == 2
    assert len(rejudged.survived) == 0


def test_rejudge_ids_and_rejudge_outcomes_are_a_union(tmp_path):
    """Naming the SAME candidate through both selections at once is not an
    error and does not double-count it -- exercises both optional
    parameters together, each at a non-default value.
    """
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    repo, lane, scratch, baseline, first = _seed_resumed_state(tmp_path, state_root)
    survived_id = _candidate_id_by_outcome_bucket(state_root, "survived")

    calls: list[str] = []

    def strengthened(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        calls.append(str(cwd))
        return subprocess.CompletedProcess(list(argv), returncode=1)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        rejudged = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=strengthened,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
            rejudge_ids=frozenset({survived_id}),
            rejudge_outcomes=frozenset({"survived"}),
        )

    assert len(calls) == 1  # not re-executed twice for matching both.
    assert len(rejudged.killed) == 2


def test_rejudge_unknown_id_refuses_before_any_execution(tmp_path):
    """(B091/D-23, P7 A5, B088) An id that does not match any of THIS run's
    own current candidate identities -- a typo, or the mutant's own source
    bytes changed since the id was recorded -- refuses outright, before a
    single record is even loaded, rather than silently doing nothing.
    """
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    repo, lane, scratch, baseline, first = _seed_resumed_state(tmp_path, state_root)

    def must_not_run(argv, *, env, cwd, timeout):
        raise AssertionError("a refused rejudge must never execute anything")

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        with pytest.raises(
            mutation.MutationStateError,
            match="not present in this lane's current candidate set",
        ):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=must_not_run,
                clock=lambda: datetime.now(timezone.utc),
                state_root=state_root,
                resume=True,
                rejudge_ids=frozenset({"0" * 64}),
            )


def test_resume_progress_event_gains_rejudged_total(tmp_path):
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    repo, lane, scratch, baseline, first = _seed_resumed_state(tmp_path, state_root)
    survived_id = _candidate_id_by_outcome_bucket(state_root, "survived")
    progress_path = tmp_path / ".assay" / "second.progress.jsonl"

    def strengthened(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        return subprocess.CompletedProcess(list(argv), returncode=1)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=strengthened,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
            state_root=state_root,
            resume=True,
            rejudge_ids=frozenset({survived_id}),
        )

    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    resume_event = next(event for event in events if event["event"] == "resume")
    assert resume_event["resumed_total"] == 1
    assert resume_event["rejected_total"] == 0
    assert resume_event["rejudged_total"] == 1


def test_run_mutation_refuses_rejudge_ids_without_resume(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        with pytest.raises(ValueError, match="requires resume=True"):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=lambda: datetime.now(timezone.utc),
                rejudge_ids=frozenset({"a" * 64}),
            )


def test_run_mutation_refuses_an_unknown_rejudge_outcome_bucket_name(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state_root = tmp_path / "state-root"
    state_root.mkdir()

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        with pytest.raises(ValueError, match="unknown bucket"):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=lambda: datetime.now(timezone.utc),
                state_root=state_root,
                resume=True,
                rejudge_outcomes=frozenset({"bogus"}),
            )


def test_resume_raises_on_a_state_record_whose_source_hash_contradicts_its_own_filename(
    tmp_path,
):
    """(B021) The record's filename IS its candidate id, which is itself
    derived from (among other things) the source hash -- so a record whose
    own `source_sha256` field disagrees with what its filename encodes is
    contradicting the identity it is filed under, which is corruption or
    hand-editing, never a routine event. A GENUINE source change never
    reaches this code path at all: it produces a different candidate id,
    hence a different filename, hence the old record is simply absent
    (silently reruns, as it always has). This test hand-tampers a record's
    `source_sha256` field while keeping its filename -- the exact shape of
    corruption, and the pre-B021 disposition here was backwards: it treated
    this as a silent rerun and, in doing so, was blind to tampering."""
    repo = _repo(tmp_path)
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    lane = make_lane(argv=("pytest", "-q"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
        )

    stale_path = next(state_root.glob("*.json"))
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    stale["source_sha256"] = "0" * 64
    stale_path.write_text(json.dumps(stale), encoding="utf-8")

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        with pytest.raises(mutation.MutationStateError, match="stale source_sha256"):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=2,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=lambda: datetime.now(timezone.utc),
                state_root=state_root,
                resume=True,
            )


def test_resume_reruns_a_state_record_after_a_routine_schema_version_bump(tmp_path):
    """(B021) The other half of the corrected disposition: `schema_version`
    is the one required key NOT folded into the candidate id, so it is the
    only one that can legitimately mismatch without the record being
    corrupt -- a routine bump of `MUTATION_STATE_SCHEMA_VERSION`. That must
    be a silent rerun (a cache miss), never a lane-wide failure -- the
    pre-B021 disposition raised here, which meant every consumer's existing
    resume store (`.assay/mutation-state/` then; wherever `--state-dir` puts
    it since B066) became `ERROR`/`UNREADABLE_ARTIFACT` on their very next
    `--resume` after an upgrade, until they manually deleted it."""
    repo = _repo(tmp_path)
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    lane = make_lane(argv=("pytest", "-q"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
        )

    stale_path = next(state_root.glob("*.json"))
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    stale["schema_version"] = stale["schema_version"] + 1000
    stale_path.write_text(json.dumps(stale), encoding="utf-8")

    calls: list[str] = []

    def deciding_recorder(*args, **kwargs):
        text = (Path(kwargs["cwd"]) / "pkg" / "flags.py").read_text(encoding="utf-8")
        calls.append(text)
        return decide(*args, **kwargs)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=deciding_recorder,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
        )

    assert calls != [], "a schema_version bump must rerun, never fail the whole lane"
    assert result.total == 2


def test_operator_and_shard_selection_are_deterministic_and_disjoint(tmp_path):
    text = (
        "def f(x, y):\n"
        "    a = True\n"
        "    if not x:\n"
        "        c = x > y\n"
        "    d = bool(y)\n"
    )
    target = MutationTarget(path="pkg/mixed.py", text=text, lines=frozenset({2, 3, 4, 5, 6}))
    both = collect_mutation_sites(
        (target,),
            adapter=PythonAdapter(),
            operators=(
                "python:compare-swap",
                "python:bool-const-flip",
                "python:falsy-swap",
        ),
        limit=10,
    )
    assert isinstance(both, tuple) and len(both) >= 2
    filtered_only = collect_mutation_sites(
        (target,),
        adapter=PythonAdapter(),
        operators=("python:compare-swap",),
        limit=10,
    )
    assert {job.site.operator for job in filtered_only} == {"python:compare-swap"}

    identities = [mutation.candidate_id(job) for job in both]
    selected = {
        index: mutation.select_mutation_shard(identities, index=index, count=2)
        for index in range(2)
    }
    assert set(selected[0]).isdisjoint(selected[1])
    assert sorted(selected[0] + selected[1]) == list(range(len(both)))
    assert selected == {
        index: mutation_module.select_mutation_shard(identities, index=index, count=2)
        for index in range(2)
    }


def _shard_summary(index: int, count: int, candidate_ids: list[str]):
    return {
        "schema_version": 1,
        "lane": "package",
        "commit": "a" * 40,
        "shard_index": index,
        "shard_count": count,
        "candidate_ids": candidate_ids,
    }


def test_shard_merge_accepts_exact_disjoint_exhaustive_coverage():
    # These specific digests are not arbitrary: `merge_mutation_shards` now
    # (B012/B023 remediation) recomputes each candidate's own deterministic
    # shard assignment and refuses a document that claims the wrong one, so
    # "4" * 64 must actually assign to shard 0/2 and "1" * 64 to shard 1/2.
    ids = ["4" * 64, "1" * 64]
    merged = mutation_module.merge_mutation_shards(
        [_shard_summary(0, 2, ids[:1]), _shard_summary(1, 2, ids[1:])]
    )
    assert merged == tuple(ids)


@pytest.mark.parametrize(
    "documents",
    [
        lambda: [_shard_summary(0, 2, ["1" * 64]), _shard_summary(1, 2, ["1" * 64])],
        lambda: [_shard_summary(0, 2, ["1" * 64]), _shard_summary(1, 2, ["1" * 64]), _shard_summary(0, 2, [])],
        lambda: [_shard_summary(0, 2, ["1" * 64])],
    ],
)
def test_shard_merge_refuses_duplicate_or_missing_input(documents):
    with pytest.raises(mutation.MutationStateError):
        mutation_module.merge_mutation_shards(documents())


def test_mutation_carries_no_progress_artifact_field_anywhere(tmp_path):
    """B031/A-320: `mutation.progress_artifact` is GONE -- dataclass, wire
    payload and JSON Schema together, not left as an inert field.

    `8a2a4731` added it to the dataclass and the schema and never wrote a
    single producer for it, so every real verdict this build has ever emitted
    omitted it while `.assay/<lane>.progress.jsonl` sat on disk unreferenced.
    Its only schema-legal spelling was a repo-tree-relative path -- i.e.
    exactly the consumer-worktree location B006(b)/A-292 forbid and B031(a)
    reproduced as a live `DIRTY_TREE` defect. The progress destination is now
    named by the consumer (`assay run --progress PATH`), the way
    `--verdict-json`'s destination already is, and assay does not record a
    destination its caller chose.
    """
    import json as _json

    outcome = MutantOutcome(
        path="pkg/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="0" * 64,
        operator="python:bool-const-flip",
        description="True->False",
    )
    assert "progress_artifact" not in Mutation.__dataclass_fields__
    with pytest.raises(TypeError):
        Mutation(
            candidate_count=1,
            total=1,
            killed=(outcome,),
            progress_artifact="../escape.jsonl",
        )
    payload = Mutation(candidate_count=1, total=1, killed=(outcome,)).to_dict()
    assert "progress_artifact" not in payload

    from conftest import SCHEMA_PATH

    assert "progress_artifact" not in SCHEMA_PATH.read_text(encoding="utf-8")
    del _json


def test_shard_candidate_ids_are_validated_for_disjointness_and_shape():
    candidate = mutation_module.candidate_id(
        collect_mutation_sites(
            _TARGETS,
            adapter=PythonAdapter(),
            operators=("python:bool-const-flip",),
            limit=1,
        )[0]
    )
    outcome = MutantOutcome(
        path="pkg/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="0" * 64,
        operator="python:bool-const-flip",
        description="True->False",
    )
    payload = Mutation(
        candidate_count=1,
        total=1,
        killed=(outcome,),
        candidate_ids=(candidate,),
    )
    assert payload.to_dict()["candidate_ids"] == [candidate]

    with pytest.raises(ValueError, match="candidate_ids contains a duplicate"):
        Mutation(
            candidate_count=1,
            total=1,
            killed=(outcome,),
            candidate_ids=(candidate, candidate),
        )
    with pytest.raises(ValueError, match="candidate_ids entry must be"):
        Mutation(
            candidate_count=1,
            total=1,
            killed=(outcome,),
            candidate_ids=("short",),
        )


def test_per_candidate_budget_marks_one_mutant_and_continues(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(budget_seconds=30.0),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            budget_per_candidate_seconds=0.05,
        )

    assert result is not None and not isinstance(result, str)
    assert len(result.budget_exceeded) == 1
    assert len(result.survived) == 1
    assert result.total == 2


# --- B091/D-23: judge candidates by progress, not by time (A1) -------------


def test_auto_budget_per_candidate_seconds_formula():
    """(B091/D-23) `max(3 x baseline, baseline + 60s)` -- both regimes."""
    # A slow baseline: 3x dominates (3*100=300 > 100+60=160).
    assert mutation_module.auto_budget_per_candidate_seconds(100.0) == 300.0
    # A near-instant baseline: the +60s floor dominates (3*1=3 < 1+60=61).
    assert mutation_module.auto_budget_per_candidate_seconds(1.0) == 61.0
    # The exact crossover (3x == x+60 at x=30) -- either formula agrees.
    assert mutation_module.auto_budget_per_candidate_seconds(30.0) == 90.0
    assert mutation_module.auto_budget_per_candidate_seconds(0.0) == 60.0


def test_baseline_wall_seconds_reads_started_and_ended():
    baseline_plan = make_plan(make_lane(argv=("pytest", "-q")))
    result = CommandResult(
        plan=baseline_plan,
        outcome=Outcome.PASS,
        reason_code=None,
        returncode=0,
        started="2026-09-12T00:00:00+00:00",
        ended="2026-09-12T00:01:30+00:00",
    )
    assert mutation_module.baseline_wall_seconds(result) == 90.0


def test_run_mutation_auto_budget_is_derived_and_drives_enforcement(tmp_path):
    """(B091/D-23, A1) `budget_per_candidate_auto=True` derives the number
    from the measured baseline, uses it to bound every candidate exactly as
    an explicit duration would, and reports it back on both the `plan`
    progress event and `Mutation.budget_per_candidate_derived_s` -- one
    computation, read twice, never two independent ones.
    """
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS
    expected = mutation_module.auto_budget_per_candidate_seconds(
        mutation_module.baseline_wall_seconds(baseline)
    )
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(budget_seconds=30.0),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            budget_per_candidate_auto=True,
            progress_artifact=progress_path,
        )

    assert result is not None and not isinstance(result, str)
    # The pre-existing "a = False" -> TimeoutExpired candidate still trips
    # `budget_exceeded` regardless of the DERIVED number's exact value: this
    # fake runner decides on file content, not on the timeout it was handed
    # -- the SAME evidence `test_per_candidate_budget_marks_one_mutant_and_
    # continues` reads for an explicit duration, reused here to prove the
    # derived path enforces exactly the same way.
    assert len(result.budget_exceeded) == 1
    assert len(result.survived) == 1
    assert result.budget_per_candidate_derived_s == expected

    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    plan_event = next(event for event in events if event["event"] == "plan")
    assert plan_event["derived"] is True
    assert plan_event["budget_per_candidate_s"] == expected
    assert plan_event["baseline_s"] >= 0.0


def test_run_mutation_refuses_auto_and_an_explicit_seconds_together(tmp_path):
    """(B091/D-23) The two are mutually exclusive: exactly one source for
    the number that both bounds every candidate and is reported back.
    """
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        with pytest.raises(ValueError, match="two sources for the one number"):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=lambda: datetime.now(timezone.utc),
                budget_per_candidate_seconds=5.0,
                budget_per_candidate_auto=True,
            )


def test_run_mutation_refuses_a_non_boolean_auto_flag(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        with pytest.raises(ValueError, match="budget_per_candidate_auto must be a boolean"):
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=make_plan(lane),
                deadline=make_deadline(),
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=10,
                operators=("python:bool-const-flip",),
                process_runner=decide,
                clock=lambda: datetime.now(timezone.utc),
                budget_per_candidate_auto="yes",  # type: ignore[arg-type]
            )


@pytest.mark.parametrize(
    "declared,expected",
    [
        (None, None),
        ("auto", None),
        ("none", None),
        ("45s", 45.0),
    ],
)
def test_declared_budget_per_candidate_seconds_treats_auto_and_none_as_unknown(
    declared, expected, tmp_path
):
    """(B091/D-23) `runner._declared_budget_per_candidate_seconds` backs the
    progress `run` header, emitted before any baseline has run -- an omitted
    key and the explicit `"auto"`/`"none"` spellings are all honestly
    unknown/absent THERE (the real auto number is reported later, by the
    `plan` event); only an explicit duration is a real number this early.
    """
    from assay import runner as runner_module

    mutation_config = MutationConfig(
        jobs=1,
        max_mutants=10,
        operators=("python:compare-swap",),
        budget_per_candidate=declared,
    )
    judge = make_r2_judge(
        source_root_paths=(tmp_path / "pkg",), mutation=mutation_config
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge)
    assert runner_module._declared_budget_per_candidate_seconds(lane) == expected


def test_plan_event_reports_none_derived_false_when_budget_per_candidate_is_unset(
    tmp_path,
):
    """Neither `budget_per_candidate_seconds` nor `budget_per_candidate_auto`
    passed at all (the legacy direct-call shape) -- the `plan` event still
    fires, honestly reporting no bound and `derived: false`.
    """
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"

    def decide(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
        )
    assert result is not None and not isinstance(result, str)
    assert result.budget_per_candidate_derived_s is None
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    plan_event = next(event for event in events if event["event"] == "plan")
    assert plan_event["budget_per_candidate_s"] is None
    assert plan_event["derived"] is False


def test_plan_reports_candidates_without_executing(tmp_path):
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    (project.root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")
    repo = GitRepo(path=project.root)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(
        "assay.toml",
        """
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.package.judge.mutation]
jobs = 2
max_mutants = 10
operators = ["python:bool-const-flip"]
budget_per_candidate = "30s"
""",
    )
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", _TEXT.replace("a = True", "a = False"))
    repo.commit_all("base flag")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", _TEXT)
    repo.write("pkg/extra.py", "value = 1\n")
    repo.commit_all("restore flag and add extra")

    from assay.cli import main

    out = io.StringIO()
    exit_code = main(["plan", "package", "--file", str(project.root / "assay.toml")], stdout=out)
    payload = json.loads(out.getvalue())

    # B030/A-319. This fixture genuinely yields ONE `python:bool-const-flip`
    # candidate (`pkg/flags.py`'s `a = True`, restored on `feature` against a
    # `base` that flipped it) -- the assertions below asserted `0`/`{}`/`[]`
    # until B030, having been written to match observed output rather than
    # the requirement, and so froze `_cmd_plan`'s phantom-scratch-root bug as
    # correct behaviour. The one candidate here is the SAME candidate a real
    # `assay run` of this lane kills.
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["candidate_count"] == 1
    assert payload["by_operator"] == {"python:bool-const-flip": 1}
    assert payload["by_file"] == {"pkg/flags.py": 1}
    assert payload["estimated_serial_seconds"] == 30.0
    assert payload["estimated_wall_seconds"] == 15.0
    assert [candidate["path"] for candidate in payload["candidates"]] == ["pkg/flags.py"]
    assert [candidate["operator"] for candidate in payload["candidates"]] == [
        "python:bool-const-flip"
    ]
    assert [candidate["description"] for candidate in payload["candidates"]] == [
        "True->False"
    ]
    assert len(payload["candidates"][0]["id"]) == 64
    # The SAME identity `assay.mutation.candidate_id` derives for the job a
    # real run of this lane executes -- plan's answer is the run's answer,
    # which is the whole point of the verb.
    from assay import mutation as _mutation
    from assay.adapters.python import PythonAdapter

    source = (project.root / "pkg" / "flags.py").read_text(encoding="utf-8")
    target = _mutation.MutationTarget(
        path="pkg/flags.py",
        text=source,
        lines=frozenset(range(1, source.count("\n") + 2)),
    )
    jobs = _mutation.collect_mutation_sites(
        (target,),
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=11,
    )
    assert payload["candidates"][0]["id"] == _mutation.candidate_id(jobs[0])


def test_plan_whole_target_lane_plans_its_declared_target(tmp_path):
    """B030/A-319's second oracle: a `mode = "whole_target"` lane must PLAN,
    not fail naming a scratch directory that never existed.

    Before the fix this refused with `ERROR`/`BAD_LANE_CONFIG`: "mutation
    target 'pkg/flags.py' is outside judge.source_roots
    ['/tmp/assay-plan-seed-.../unused/pkg']".
    """
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    (project.root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")
    repo = GitRepo(path=project.root)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(
        "assay.toml",
        """
schema_version = 2

[lanes.whole]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.whole.isolation]
snapshot_selection = "repository"

[lanes.whole.judge]
language = "python"
source_roots = ["pkg"]
fail_under = 0.0
allow_excluded = false
require_branch = false
mode = "whole_target"
# No `base`: B033/A-325 refuses it as inert config on a whole-target lane at
# every rigor, R2 included -- neither tier resolves a comparison commit.
targets = ["pkg/flags.py"]

[lanes.whole.judge.coverage]
format = "cobertura"
artifact = "cov.xml"

[lanes.whole.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
""",
    )
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    repo.git("checkout", "-q", "-b", "base")
    repo.git("checkout", "-q", "-b", "feature")

    from assay.cli import main

    out = io.StringIO()
    exit_code = main(["plan", "whole", "--file", str(project.root / "assay.toml")], stdout=out)
    payload = json.loads(out.getvalue())

    assert exit_code == 0
    assert payload["status"] == "ok"
    # BOTH of `_TEXT`'s bool constants: whole-target mode judges the whole
    # declared file, not a diff's changed lines (the diff-mode fixture above
    # sees only the one restored line).
    assert payload["candidate_count"] == 2
    assert payload["by_file"] == {"pkg/flags.py": 2}


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("pkg/tests/test_flags.py", "is a test path"),
        ("pkg/notes.md", "not adapter-recognised source"),
        ("pkg/gone.py", "does not exist as a regular file"),
        # Round-2 review: R2 had no symlink gate of its own and fell through
        # to `read_regular_file`, which refuses a symlink as
        # ERROR/GIT_FAILED -- a repository failure for what is a lane-config
        # mistake. R1 has always named it; R2 now does too, first, before
        # anything that resolves.
        ("pkg/alias.py", "is a symlink"),
    ],
)
def test_a_whole_target_entry_that_fails_a_gate_is_refused_not_dropped(
    tmp_path, target, expected
):
    """B033/A-325: `_mutation_targets_whole` used to `continue` silently past
    an excluded directory, a non-matching source glob and a test path, so a
    lane declaring two targets could report PASS having mutated one -- and
    nothing in the verdict named the other. It now refuses by name, exactly
    as R1's `evaluate._resolve_whole_target` always did. Driven through
    `assay plan`, which calls the same resolver.
    """
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    (project.root / "pkg" / "tests").mkdir()
    (project.root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")
    (project.root / "pkg" / "tests" / "test_flags.py").write_text(
        _TEXT, encoding="utf-8"
    )
    (project.root / "pkg" / "notes.md").write_text("notes\n", encoding="utf-8")
    (project.root / "pkg" / "alias.py").symlink_to("flags.py")
    repo = GitRepo(path=project.root)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(
        "assay.toml",
        f"""
schema_version = 2

[lanes.whole]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = {{ MOCK_MODE = "true" }}
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.whole.isolation]
snapshot_selection = "repository"

[lanes.whole.judge]
language = "python"
source_roots = ["pkg"]
mode = "whole_target"
targets = ["pkg/flags.py", "{target}"]

[lanes.whole.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
""",
    )
    repo.commit_all("add sources")

    from assay.cli import main

    out = io.StringIO()
    err = io.StringIO()
    exit_code = main(
        ["plan", "whole", "--file", str(project.root / "assay.toml")],
        stdout=out,
        stderr=err,
    )

    assert exit_code != 0
    message = err.getvalue()
    assert "BAD_LANE_CONFIG" in message
    assert expected in message
    # The refusal NAMES the declared target -- the whole point: a bare
    # BAD_LANE_CONFIG leaves the consumer guessing which of N entries failed.
    assert target in message


def _write_plan_fixture(
    tmp_path: Path, *, budget_per_candidate_line: str = 'budget_per_candidate = "30s"\n'
) -> Path:
    """Same fixture as `test_plan_reports_candidates_without_executing`,
    factored out so the `--shard`/`--operators` CLI-level tests below don't
    duplicate its setup. *budget_per_candidate_line* defaults to the
    original explicit "30s" so every pre-existing caller is byte-unchanged;
    B091/D-23 callers pass `""` (omitted) or an explicit `"auto"`/`"none"`
    line instead.
    """
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    (project.root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")
    repo = GitRepo(path=project.root)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(
        "assay.toml",
        """
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.package.judge.mutation]
jobs = 2
max_mutants = 10
operators = ["python:bool-const-flip"]
"""
        + budget_per_candidate_line,
    )
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", _TEXT.replace("a = True", "a = False"))
    repo.commit_all("base flag")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", _TEXT)
    repo.write("pkg/extra.py", "value = 1\n")
    repo.commit_all("restore flag and add extra")
    return project.root / "assay.toml"


def test_plan_accepts_a_valid_shard(tmp_path):
    """(B012 remediation) `assay plan --shard` has its own dry bounds-check
    block, independent of `assay run`'s -- exercised here through the
    installed CLI at the zero-based index the config/schema/docs all use."""
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out = io.StringIO()
    exit_code = main(["plan", "package", "--shard", "0/2", "--file", str(path)], stdout=out)
    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["shard"] == "0/2"


def test_plan_refuses_an_out_of_range_shard_with_a_clean_exit_not_a_crash(tmp_path):
    """(B012 remediation, D-6) The dry bounds-check call in `_cmd_plan` used
    to sit outside any try/except, so an out-of-range `--shard` raised a
    bare `ValueError` uncaught by `main()`'s `except AssayError` -- a
    traceback, not an exit code."""
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    exit_code = main(["plan", "package", "--shard", "5/2", "--file", str(path)], stdout=out, stderr=err)
    assert exit_code != 0
    assert "shard index 5 is outside" in err.getvalue()
    assert out.getvalue() == ""


def test_plan_refuses_a_malformed_shard_spelling(tmp_path):
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    exit_code = main(["plan", "package", "--shard", "not-a-shard", "--file", str(path)], stdout=out, stderr=err)
    assert exit_code != 0
    assert "--shard must have the form INDEX/COUNT" in err.getvalue()


def test_plan_refuses_an_unknown_operator_with_a_clean_exit_not_a_crash(tmp_path):
    """(B012 remediation, D-6) `_cmd_plan`'s own `--operators` validation
    raises `LaneConfigError` too -- confirming the missing import fix covers
    both `_cmd_run` and `_cmd_plan`, which validate independently."""
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    exit_code = main(
        ["plan", "package", "--operators", "bogus:does-not-exist", "--file", str(path)],
        stdout=out,
        stderr=err,
    )
    assert exit_code != 0
    assert "unknown mutation operators" in err.getvalue()


def test_plan_config_requires_a_duration(tmp_path):
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    project.write(
        """
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]
base = "main"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
budget_per_candidate = "nonsense"
"""
    )
    from assay.config import load_lane_file

    with pytest.raises(LaneConfigError, match="budget_per_candidate"):
        load_lane_file(project.root / "assay.toml")


@pytest.mark.parametrize(
    "budget_per_candidate_line", ["", 'budget_per_candidate = "auto"\n', 'budget_per_candidate = "none"\n']
)
def test_plan_estimates_with_the_60s_fallback_for_every_non_duration_spelling(
    tmp_path, budget_per_candidate_line
):
    """(B091/D-23) `assay plan` never executes anything, so it cannot measure
    the baseline "auto" would derive from -- an omitted key, an explicit
    "auto", and the explicit "none" opt-out must all fall back to the same
    60s-per-candidate ESTIMATE an undeclared bound always used, rather than
    crashing inside `parse_duration` on a spelling that was never a
    duration.
    """
    from assay.cli import main

    path = _write_plan_fixture(
        tmp_path, budget_per_candidate_line=budget_per_candidate_line
    )
    out = io.StringIO()
    exit_code = main(["plan", "package", "--file", str(path)], stdout=out)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["status"] == "ok"
    # 1 candidate x 60s fallback, / 2 jobs for the wall estimate.
    assert payload["estimated_serial_seconds"] == 60.0
    assert payload["estimated_wall_seconds"] == 30.0
