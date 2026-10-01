"""B071 — a ``crashed`` candidate's mutation-state record carries the
subprocess output that explains WHY.

Every mutant's ``CommandResult`` already carries bounded
``stdout_tail``/``stderr_tail`` (``execute_command``, ``runner.py``,
``COMMAND_TAIL_BYTES``). ``_execute_mutation_jobs`` had that result in scope
at every classification site and persisted only ``outcome_bucket`` plus
identity fields, so once a candidate's wave completed the captured text was
gone — a genuine omission rather than a missing feature, because
``verdict.py`` already defines ``result_stdout_tail``/``result_stderr_tail``
as first-class payload names and ``verify.py`` already round-trips them for
other claim types.

**The repro here is the real one**, not a synthetic assertion: dstdns's
``cw2b_schema`` lane, ``sql:drop-unique`` turning
``CONSTRAINT uq_work_units_id_operation UNIQUE (id, operation)`` into
``CONSTRAINT uq_work_units_id_operation CHECK (true)``. A later ``FOREIGN
KEY`` in the same DDL file needs that exact uniqueness, so applying the
mutated schema fails; the lane declares an ``equivalence_artifact`` and the
failed apply never writes one, which is what lands the candidate in
``crashed`` rather than ``killed``. Recovering the reason took a by-hand
re-run of the exact byte-range mutation — to read text ``execute_command``
had already captured during the original run.

Scope, per the wave's ruling: the ``crashed`` bucket only, and the
mutation-state record only. ``write_progress``'s payload is deliberately
untouched, and ``killed``/``survived``/``budget_exceeded`` records keep
exactly the shape and size they had.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import (
    MUTATION_STATE_RECORD_LIMIT,
    MutationTarget,
    run_mutation,
)
from assay.runner import COMMAND_TAIL_BYTES, CommandResult, execute_command

#: The message the original investigation had to reproduce by hand. Postgres
#: emits it on stderr when the FOREIGN KEY that follows the dropped UNIQUE
#: constraint cannot find a matching unique index.
_REAL_STDERR = (
    'ERROR:  there is no unique constraint matching given keys for '
    'referenced table "work_units"\n'
)

_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    return a, b\n"
)
_TARGETS = (MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3})),)

_EQUIVALENCE_ARTIFACT = ".assay/schema-dump.sql"
_BASELINE_EQUIVALENCE = b"CREATE TABLE work_units (id uuid, operation text);\n"


def _repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    return repo


def _records(state_root: Path) -> list[dict]:
    """(B066) Records live directly under the caller's own *state_root*. The
    `.assay/mutation-state/` tail is only the DEFAULT root `run_lane`
    composes when no `--state-dir` is given."""
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(state_root.glob("*.json"))
    ]


def _run(
    repo: GitRepo,
    tmp_path: Path,
    *,
    state_root: Path,
    process_runner,
    equivalence: bool,
):
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0
        ),
    )
    assert baseline.outcome is Outcome.PASS

    extra = {}
    if equivalence:
        extra = {
            "equivalence_artifact": _EQUIVALENCE_ARTIFACT,
            "baseline_equivalence": _BASELINE_EQUIVALENCE,
        }

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        return run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(make_lane(argv=("pytest", "-q"))),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=process_runner,
            clock=lambda: datetime.now(timezone.utc),
            state_root=state_root,
            resume=True,
            **extra,
        )


def _ddl_apply_that_fails_on_the_dropped_constraint(argv, *, env, cwd, timeout):
    """The ``uq_work_units_id_operation`` shape: applying the mutated schema
    fails and therefore writes NO equivalence artifact, which is what makes
    the candidate ``crashed`` rather than ``killed``.
    """
    # `execute_command` runs its child under `text=True`, so both streams
    # reach `_bounded_tail` already decoded -- the fixture matches.
    return subprocess.CompletedProcess(
        list(argv), returncode=1, stdout="applying 03c-create-workflow-core.sql\n",
        stderr=_REAL_STDERR,
    )


def test_a_crashed_candidates_record_carries_the_stderr_that_explains_it(
    tmp_path: Path,
):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"

    result = _run(
        repo,
        tmp_path,
        state_root=state_root,
        process_runner=_ddl_apply_that_fails_on_the_dropped_constraint,
        equivalence=True,
    )

    assert not isinstance(result, str)
    assert len(result.crashed) == result.total > 0

    records = _records(state_root)
    assert records, "the run wrote no mutation-state records at all"
    for record in records:
        assert record["outcome_bucket"] == "crashed"
        # The whole point of the entry: the reason is IN the record, not
        # recoverable only by re-running the mutation by hand.
        assert "no unique constraint matching given keys" in record["result_stderr_tail"]
        assert "work_units" in record["result_stderr_tail"]
        assert record["result_stdout_tail"] == "applying 03c-create-workflow-core.sql\n"


def test_a_killed_candidates_record_is_unaffected_in_shape_and_size(tmp_path: Path):
    """Acceptance criterion 2: tails are opt-in BY BUCKET. A killed
    candidate's meaning is already carried by the pass/fail split, so its
    record must be byte-for-byte the shape it was."""
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"

    def suite_catches_the_mutant(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(
            list(argv), returncode=1, stdout="2 failed, 450 passed\n",
            stderr="assertion failed\n",
        )

    result = _run(
        repo,
        tmp_path,
        state_root=state_root,
        process_runner=suite_catches_the_mutant,
        equivalence=False,
    )

    assert not isinstance(result, str)
    assert len(result.killed) == result.total > 0

    records = _records(state_root)
    assert records
    for record in records:
        assert record["outcome_bucket"] == "killed"
        assert "result_stderr_tail" not in record
        assert "result_stdout_tail" not in record


def test_a_survived_candidates_record_is_unaffected_too(tmp_path: Path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"

    def suite_never_notices(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(
            list(argv), returncode=0, stdout="452 passed\n", stderr=""
        )

    result = _run(
        repo,
        tmp_path,
        state_root=state_root,
        process_runner=suite_never_notices,
        equivalence=False,
    )

    assert not isinstance(result, str)
    assert len(result.survived) == result.total > 0
    for record in _records(state_root):
        assert record["outcome_bucket"] == "survived"
        assert "result_stderr_tail" not in record
        assert "result_stdout_tail" not in record


def test_a_crashed_record_still_resumes(tmp_path: Path):
    """The record is read back by ``_load_validated_state_record`` on the
    next ``--resume``. The added keys are not among the ones it validates,
    and an older record without them must keep resuming — so the round trip
    is proven, not assumed from "extra keys are tolerated"."""
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"

    first = _run(
        repo,
        tmp_path,
        state_root=state_root,
        process_runner=_ddl_apply_that_fails_on_the_dropped_constraint,
        equivalence=True,
    )
    assert not isinstance(first, str)

    executed: list[str] = []

    def must_not_run(argv, *, env, cwd, timeout):
        if Path(cwd) != repo.path:
            executed.append(str(cwd))
        return subprocess.CompletedProcess(list(argv), returncode=0)

    resumed = _run(
        repo,
        tmp_path / "second",
        state_root=state_root,
        process_runner=must_not_run,
        equivalence=True,
    )
    assert not isinstance(resumed, str)
    assert executed == [], "a resumed candidate must not be re-executed"
    assert len(resumed.crashed) == first.total


# --- the size argument, pinned rather than left as arithmetic ------------------


def test_two_maximal_tails_still_fit_the_readers_own_record_limit():
    """``_crash_diagnostic_tails`` introduces no new bound: the tails are
    already capped at ``COMMAND_TAIL_BYTES`` each. This pins that the worst
    case a maximal pair can serialize to still fits
    ``MUTATION_STATE_RECORD_LIMIT``, so the reader can never be handed a
    record it refuses as oversized.

    The adversarial fixture is the most expensive escape ``json.dump``'s
    default ``ensure_ascii=True`` has: a character that costs 6 bytes.
    """
    from assay import mutation

    worst = "\x7f" * COMMAND_TAIL_BYTES  # 1 byte encoded, 6 bytes as 
    result = CommandResult.__new__(CommandResult)
    object.__setattr__(result, "stdout_tail", worst)
    object.__setattr__(result, "stderr_tail", worst)

    tails = mutation._crash_diagnostic_tails("crashed", result)
    payload = {
        "schema_version": 1,
        "candidate_id": "0" * 64,
        "path": "infra/db-init/init-scripts/03c-create-workflow-core.sql",
        "operator": "sql:drop-unique",
        "replacement_sha256": "0" * 64,
        "source_sha256": "0" * 64,
        "lineno": 189,
        "description": "UNIQUE -> CHECK (true)",
        "outcome_bucket": "crashed",
        **tails,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(encoded) < MUTATION_STATE_RECORD_LIMIT, len(encoded)


def test_the_helper_emits_nothing_for_every_non_crashed_bucket():
    from assay import mutation

    result = CommandResult.__new__(CommandResult)
    object.__setattr__(result, "stdout_tail", "out")
    object.__setattr__(result, "stderr_tail", "err")
    for bucket in ("killed", "survived", "budget_exceeded", "equivalent"):
        assert mutation._crash_diagnostic_tails(bucket, result) == {}


def test_an_empty_stream_is_recorded_as_empty_not_omitted():
    """``""`` means "the stream was empty", which is itself a diagnosis and
    is not the same fact as a record written by a build that had no tails at
    all."""
    from assay import mutation

    result = CommandResult.__new__(CommandResult)
    object.__setattr__(result, "stdout_tail", "")
    object.__setattr__(result, "stderr_tail", "boom\n")
    assert mutation._crash_diagnostic_tails("crashed", result) == {
        "result_stdout_tail": "",
        "result_stderr_tail": "boom\n",
    }


def test_absent_tails_are_omitted_rather_than_written_as_null():
    from assay import mutation

    result = CommandResult.__new__(CommandResult)
    object.__setattr__(result, "stdout_tail", None)
    object.__setattr__(result, "stderr_tail", None)
    assert mutation._crash_diagnostic_tails("crashed", result) == {}
