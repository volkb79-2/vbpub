"""B064 -- the R0/R1 phase-boundary progress stream, and its heartbeat.

Before this item `--progress PATH` had exactly one producer, four layers
down inside `mutation.run_mutation`, so a lane that declared no R2 was
handed an EMPTY file and a lane that looked hung for nine minutes stayed
illegible. The stream is now opened once, for the whole lane, by
`cli._cmd_run` -- around BOTH `run_lane` and its own `write_verdict`,
because `verdict_written` is the R0/R1 terminal and happens after `run_lane`
has already returned.

Two rulings this file is written against, both settled before the item was
built and neither re-derived here:

* **Stall detection stays with the CALLER** (run-gate RG-36). assay makes
  the signal rich enough for an external watcher to compute staleness; it
  never becomes the watcher and gains no stall threshold of its own.
* **The heartbeat is a PURE TIME-BASED tick.** It does not read the child's
  stdout/stderr, count bytes, track activity or know which tool is running.
  A per-language live-test-progress adapter is B073 -- deliberately deferred,
  deliberately larger, and deliberately not started here.
"""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import GitRepo

from assay import mutation, runner
from assay.cli import main


_R0_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false
"""

_R1_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "cp coverage-fixture.json coverage.json"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["pkg"]
fail_under = 0.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "coverage.json" }
base = "base"
"""


def _events(destination: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in destination.read_text(encoding="utf-8").splitlines()
    ]


def _r0_repo(repo: GitRepo) -> str:
    repo.write("assay.toml", _R0_LANE)
    repo.commit_all("lane")
    return str(repo.path / "assay.toml")


def test_an_r0_only_lane_now_emits_its_own_phase_stream(git_repo: GitRepo, tmp_path):
    """The headline: `--progress` on an R0 lane used to write nothing at all.

    It now writes the phases assay itself owns and can timestamp without
    knowing anything about the runner -- which is B064's own measured claim
    about why R0/R1 progress is cheap and merely unwired.
    """
    lane_file = _r0_repo(git_repo)
    destination = tmp_path / "progress.jsonl"

    assert main(["run", "unit", "--file", lane_file, "--progress", str(destination)]) == 0

    names = [event["event"] for event in _events(destination)]
    assert names == ["run", "command_started", "command_finished", "verdict_written"]


def test_the_direct_r0_path_never_claims_a_snapshot_it_did_not_take(
    git_repo: GitRepo, tmp_path
):
    """The vocabulary is CLOSED, and a closed vocabulary is only worth
    anything if a name in it is a measurement.

    An R0-only lane takes `run_lane`'s DIRECT branch (A-189), which measures
    the consumer's live tree: there is no snapshot anywhere in it. Emitting
    `snapshot_materialized` to make the two dispatch branches look alike
    would be the first lie in an artifact whose whole job is to say what is
    happening right now. `coverage_parsed` is absent for the same reason --
    this lane parses no coverage.
    """
    lane_file = _r0_repo(git_repo)
    destination = tmp_path / "progress.jsonl"

    main(["run", "unit", "--file", lane_file, "--progress", str(destination)])

    names = {event["event"] for event in _events(destination)}
    assert "snapshot_materialized" not in names
    assert "coverage_parsed" not in names
    assert {event["phase"] for event in _events(destination) if "phase" in event} == {
        "direct"
    }


def test_an_r1_lane_emits_the_snapshot_and_coverage_phases(git_repo: GitRepo, tmp_path):
    """The higher-rigor branch DOES take a snapshot and DOES parse coverage,
    so it emits both -- same closed vocabulary, more of it."""
    git_repo.write("assay.toml", _R1_LANE)
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/thing.py", "def thing():\n    return 1\n")
    git_repo.write(".gitignore", "coverage.json\n")
    git_repo.write(
        "coverage-fixture.json",
        json.dumps(
            {
                "meta": {"version": "7.0.0"},
                "files": {
                    "pkg/thing.py": {
                        "executed_lines": [1, 2],
                        "missing_lines": [],
                        "excluded_lines": [],
                    }
                },
            }
        ),
    )
    git_repo.commit_all("lane")
    git_repo.git("checkout", "-q", "-b", "base")
    git_repo.git("checkout", "-q", "-b", "feature")
    lane_file = str(git_repo.path / "assay.toml")
    destination = tmp_path / "progress.jsonl"

    main(["run", "unit", "--file", lane_file, "--progress", str(destination)])

    names = [event["event"] for event in _events(destination)]
    assert names[:5] == [
        "run",
        "snapshot_materialized",
        "command_started",
        "command_finished",
        "coverage_parsed",
    ]
    assert names[-1] == "verdict_written"


def test_verdict_written_reports_where_the_verdict_went(git_repo: GitRepo, tmp_path):
    """The terminal record, and the reason the stream is opened one layer
    above `run_lane`: `write_verdict` runs after `run_lane` returns, so a
    writer opened inside it could never emit this."""
    lane_file = _r0_repo(git_repo)
    destination = tmp_path / "progress.jsonl"
    verdict_path = tmp_path / "verdict.json"

    main(
        [
            "run",
            "unit",
            "--file",
            lane_file,
            "--progress",
            str(destination),
            "--verdict-json",
            str(verdict_path),
        ]
    )

    terminal = _events(destination)[-1]
    assert terminal["event"] == "verdict_written"
    assert terminal["outcome"] == "PASS"
    assert terminal["exit_code"] == 0
    assert terminal["destination"] == str(verdict_path)


def test_no_verdict_destination_is_reported_as_null_not_omitted(
    git_repo: GitRepo, tmp_path
):
    """A-028's no-artifact mode is a real choice, not a missing field: the
    verdict is still FINAL, and saying where it went is the honest way to
    report that nothing was written."""
    lane_file = _r0_repo(git_repo)
    destination = tmp_path / "progress.jsonl"

    main(["run", "unit", "--file", lane_file, "--progress", str(destination)])

    terminal = _events(destination)[-1]
    assert terminal["destination"] is None


def test_a_progress_path_inside_a_visible_tree_refuses_before_any_work(
    git_repo: GitRepo, tmp_path
):
    """(Round-1 SF-5.) B064 made `--progress` write on EVERY tier, which
    turned a previously-harmless invocation into an immediate red.

    Before this wave, `--progress <non-ignored path inside the work tree>` on
    an R0/R1 lane wrote nothing at all -- the `r2_declared` gate saw to that
    -- so the run passed. After it, the file is created and the lane refuses
    ITSELF on its very first run with a bare `NO_MEASUREMENT`/`DIRTY_TREE`,
    which says nothing about the flag that caused it.

    The same wave built the preflight that answers this (`git.path_is_ignored`,
    for `--state-dir`) and did not extend it one flag over. It is extended
    here: refused before any work, naming the cause and the fix, exactly as
    its sibling does.
    """
    lane_file = _r0_repo(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            lane_file,
            "--progress",
            str(git_repo.path / ".assay" / "progress-unit.jsonl"),
        ],
        stderr=err,
    )

    assert code != 0
    message = err.getvalue()
    assert "DIRTY_TREE" in message, message
    assert "--progress" in message, message
    assert not (git_repo.path / ".assay").exists(), (
        "refused BEFORE the destination's parent was created"
    )
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_gitignored_progress_path_inside_the_tree_is_accepted(
    git_repo: GitRepo, tmp_path
):
    """The estate's own convention (run-gate RG-33/RG-13's gitignore
    obligation) keeps `.assay/` ignored, and that case must keep working --
    the refusal is about VISIBILITY to git, never about location."""
    git_repo.write("assay.toml", _R0_LANE)
    git_repo.write(".gitignore", ".assay/\n")
    git_repo.commit_all("lane with an ignored .assay/")
    destination = git_repo.path / ".assay" / "progress-unit.jsonl"

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--progress",
                str(destination),
            ]
        )
        == 0
    )
    assert _events(destination)
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_progress_path_reached_through_a_symlink_hop_refuses_too(
    git_repo: GitRepo, tmp_path
):
    """SF-1's containment fix serves both flags, because both ask the same
    question through the same helper."""
    lane_file = _r0_repo(git_repo)
    hop_parent = tmp_path / "outside"
    hop_parent.mkdir()
    (hop_parent / "hop").symlink_to(git_repo.path)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            lane_file,
            "--progress",
            str(hop_parent / "hop" / "sneaky.jsonl"),
        ],
        stderr=err,
    )

    assert code != 0
    assert "DIRTY_TREE" in err.getvalue(), err.getvalue()
    assert not (git_repo.path / "sneaky.jsonl").exists()
    assert git_repo.git("status", "--porcelain").strip() == ""


# --- the heartbeat -------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event) -> None:
        self.events.append(dict(event))


def _stream(recorder: _Recorder) -> mutation.ProgressStream:
    return mutation.ProgressStream(
        recorder, clock=lambda: datetime.now(timezone.utc)
    )


def test_the_heartbeat_ticks_while_a_command_runs_and_stops_when_it_returns():
    recorder = _Recorder()
    with runner._command_heartbeat(
        _stream(recorder), interval_seconds=0.02, phase="baseline"
    ):
        time.sleep(0.35)
    ticks = [event for event in recorder.events if event["event"] == "command_running"]
    assert len(ticks) >= 3, recorder.events
    assert all(tick["phase"] == "baseline" for tick in ticks)
    # (B064's named collision, resolved deliberately) `elapsed_s` stays
    # run-relative on EVERY record; the heartbeat's own "how long has THIS
    # command been running" is a different quantity with a different name.
    assert all("command_elapsed_s" in tick for tick in ticks)
    assert all("elapsed_s" in tick for tick in ticks)

    settled = len(recorder.events)
    time.sleep(0.15)
    assert len(recorder.events) == settled, "the tick outlived its command"


def test_a_heartbeat_write_failure_stops_the_heartbeat_and_nothing_else():
    """A diagnostic tick that could kill a measured lane would be worse than
    no tick at all.

    (Round-1 SF-6.) This test used to assert nothing at all -- "reaching here
    is the assertion" -- so it would have passed identically had
    `_command_heartbeat` never started a thread, never called the writer, or
    been a bare `yield`. It now pins both halves of the claim: a write WAS
    attempted (proving the thread ran and reached the writer), and NO second
    attempt followed it (proving the failure retired the thread instead of
    spinning on a broken destination for the rest of the lane).
    """
    attempts: list[float] = []

    def explode(event):
        attempts.append(time.monotonic())
        raise OSError("the progress filesystem went away")

    stream = mutation.ProgressStream(explode, clock=lambda: datetime.now(timezone.utc))
    with runner._command_heartbeat(stream, interval_seconds=0.02, phase="baseline"):
        # Long enough for a healthy heartbeat to have ticked many times over.
        time.sleep(0.3)

    assert len(attempts) == 1, (
        f"expected exactly one attempted write before the heartbeat retired, "
        f"got {len(attempts)}"
    )


def test_no_stream_and_no_interval_start_no_thread():
    recorder = _Recorder()
    with runner._command_heartbeat(None, interval_seconds=0.01, phase="baseline"):
        time.sleep(0.05)
    with runner._command_heartbeat(
        _stream(recorder), interval_seconds=None, phase="baseline"
    ):
        time.sleep(0.05)
    assert recorder.events == []


def test_a_sub_floor_heartbeat_is_refused_by_name_before_any_work(
    git_repo: GitRepo, tmp_path
):
    """Refused, never clamped. Silently substituting a different interval
    than the one an operator asked for is how a configuration mistake
    survives to become a mystery in someone's log."""
    lane_file = _r0_repo(git_repo)
    destination = tmp_path / "progress.jsonl"
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            lane_file,
            "--progress",
            str(destination),
            "--progress-heartbeat",
            "1",
        ],
        stderr=err,
    )

    assert code != 0
    assert "floor" in err.getvalue(), err.getvalue()
    assert not destination.exists(), "refused before the destination was opened"


def test_a_non_numeric_heartbeat_is_refused(git_repo: GitRepo, tmp_path):
    lane_file = _r0_repo(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            lane_file,
            "--progress",
            str(tmp_path / "progress.jsonl"),
            "--progress-heartbeat",
            "soon",
        ],
        stderr=err,
    )

    assert code != 0
    assert "number of seconds" in err.getvalue(), err.getvalue()


def test_the_heartbeat_flag_is_a_no_op_without_a_destination(
    git_repo: GitRepo, tmp_path
):
    """Same shape `--progress` already has: no destination, nothing to do.
    The default must not arm a thread with nowhere to write."""
    lane_file = _r0_repo(git_repo)

    assert main(["run", "unit", "--file", lane_file, "--progress-heartbeat", "5"]) == 0
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_real_run_emits_a_real_tick_through_the_real_flag(
    git_repo: GitRepo, tmp_path
):
    """(Round-1 N7.) Every other heartbeat test drives `_command_heartbeat`
    directly or asserts a CLI refusal; none proved the WIRING -- that
    `--progress-heartbeat` actually reaches a running lane and puts a
    `command_running` record in the file. Proven here end to end, on a real
    child that outlives one interval.
    """
    git_repo.write("assay.toml", _R0_LANE.replace("exit 0", "sleep 7; exit 0"))
    git_repo.commit_all("a lane whose command outlives one heartbeat interval")
    destination = tmp_path / "progress.jsonl"

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--progress",
                str(destination),
                "--progress-heartbeat",
                "5",
            ]
        )
        == 0
    )

    events = _events(destination)
    ticks = [event for event in events if event["event"] == "command_running"]
    assert ticks, [event["event"] for event in events]
    assert ticks[0]["phase"] == "direct"
    assert ticks[0]["command_elapsed_s"] >= 5.0
    # Command-relative and run-relative are genuinely different quantities,
    # and the run started before the command did.
    assert ticks[0]["elapsed_s"] >= ticks[0]["command_elapsed_s"]
    # Cancelled with the command: no tick may follow `command_finished`.
    names = [event["event"] for event in events]
    assert names.index("command_finished") > max(
        index for index, name in enumerate(names) if name == "command_running"
    )


def test_the_default_and_the_floor_are_the_documented_ones():
    assert runner.PROGRESS_HEARTBEAT_DEFAULT_SECONDS == 60.0
    assert runner.PROGRESS_HEARTBEAT_FLOOR_SECONDS == 5.0


# --- the closed vocabulary ----------------------------------------------


def test_the_progress_vocabulary_is_enforced_not_merely_documented():
    """B064's acceptance says the phase vocabulary is CLOSED. A table
    nothing enforces is a comment, not a vocabulary."""
    recorder = _Recorder()
    stream = _stream(recorder)
    with pytest.raises(ValueError) as caught:
        stream.emit({"event": "tests_are_going_well"})
    assert "closed progress vocabulary" in str(caught.value)
    assert recorder.events == []


def test_every_record_is_enriched_once_centrally():
    """B065's fields ride on `ProgressStream`, so a producer cannot forget
    them and two producers cannot disagree about what `elapsed_s` measures.
    """
    recorder = _Recorder()
    stream = _stream(recorder)
    stream.emit_run_header(commit="0" * 40, lane="unit", rigor=("R0",), budget_s=120.0)
    stream.emit({"event": "command_started"})
    assert [event["event"] for event in recorder.events] == ["run", "command_started"]
    assert all(
        "emitted_at" in event and "elapsed_s" in event for event in recorder.events
    )
    assert recorder.events[0]["elapsed_s"] <= recorder.events[1]["elapsed_s"]


def test_the_run_header_is_emitted_at_most_once():
    recorder = _Recorder()
    stream = _stream(recorder)
    stream.emit_run_header(commit="0" * 40)
    stream.emit_run_header(commit="0" * 40)
    assert len(recorder.events) == 1


# --- B065: what a reader can compute from the file ALONE ------------------


_B065_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.unit.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
budget_per_candidate = "30s"
"""


def test_a_reader_with_only_the_progress_file_computes_rate_eta_and_age(
    git_repo: GitRepo, tmp_path
):
    """B065's acceptance, on a REAL run rather than a fixture.

    The numbers a caller actually needs -- completion rate, an ETA for the
    work that remains, and how long ago the last record arrived -- are all
    derivable from this file with no clock of the reader's own beyond "now",
    and they agree with the verdict's counts and the run's measured wall
    time. run-gate RG-36's stall detection is built on exactly these; assay
    supplies them and does not become the watcher.
    """
    git_repo.write("assay.toml", _B065_LANE)
    git_repo.write("pkg/flags.py", "a = True\n")
    git_repo.commit_all("lane")
    git_repo.git("checkout", "-q", "-b", "base")
    git_repo.write("pkg/flags.py", "a = False\n")
    git_repo.commit_all("base flag")
    git_repo.git("checkout", "-q", "-b", "feature")
    git_repo.write("pkg/flags.py", "a = True\n")
    git_repo.commit_all("restore flag")

    destination = tmp_path / "progress.jsonl"
    verdict_path = tmp_path / "verdict.json"
    started = time.time()
    main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--progress",
            str(destination),
            "--verdict-json",
            str(verdict_path),
        ]
    )
    measured_wall_seconds = time.time() - started

    events = _events(destination)
    header = events[0]
    assert header["event"] == "run"

    # --- the bounds, without opening the lane file ----------------------
    assert header["budget_s"] == 120.0
    assert header["budget_per_candidate_s"] == 30.0
    assert header["lane"] == "unit"
    assert header["rigor"] == ["R0", "R2"]

    # --- rate ------------------------------------------------------------
    candidates = [event for event in events if event["event"] == "candidate"]
    sweep_sizes = next(event for event in events if event["event"] == "candidates")
    assert candidates, events
    assert len(candidates) == sweep_sizes["pending_total"]
    completed = len(candidates)
    span = candidates[-1]["elapsed_s"] - header["elapsed_s"]
    rate = completed / span if span > 0 else float("inf")
    assert rate > 0

    # --- ETA -------------------------------------------------------------
    remaining = sweep_sizes["candidate_total"] - completed
    eta_seconds = remaining / rate if rate != float("inf") else 0.0
    assert eta_seconds >= 0.0

    # --- last-event age --------------------------------------------------
    last = events[-1]
    assert last["event"] == "verdict_written"
    age = (
        datetime.now(timezone.utc)
        - datetime.fromisoformat(last["emitted_at"])
    ).total_seconds()
    assert 0.0 <= age < 60.0, (age, last["emitted_at"])

    # --- agreement with the verdict and with measured wall time ----------
    terminal = next(event for event in events if event["event"] == "end")
    assert sum(terminal["buckets"].values()) == completed
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2_claim = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    verdict_counts = {
        bucket: len(r2_claim["mutation"].get(bucket, []))
        for bucket in terminal["buckets"]
    }
    assert verdict_counts == terminal["buckets"]
    assert last["outcome"] == document["outcome"]
    # `elapsed_s` is measured from the header, which is emitted after the
    # process starts, so it can only ever be under the wall time the caller
    # measured around the whole invocation.
    assert 0.0 < last["elapsed_s"] <= measured_wall_seconds + 1.0
