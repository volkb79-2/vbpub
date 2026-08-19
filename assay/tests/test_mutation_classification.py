"""P34/W5 -- the widened classification table (carve §3.6, A-279): when --
and only when -- the lane declares ``judge.mutation.equivalence_artifact``,
:func:`~assay.mutation.run_mutation` classifies each attempted mutant as a
function of ``(exit outcome, artifact-present, artifact-bytes)`` rather than
exit status alone, populates the ``equivalent`` bucket, and (when
``kill_signal_artifact`` is ALSO declared) reads the killed mutant's own
mechanism string verbatim -- reclassifying a killed mutant with no signal
file to ``crashed``, since the lane's own declared attribution contract was
not met for it.

**O8's own inertness is the load-bearing property**: every test in this
module passes ``equivalence_artifact``/``kill_signal_artifact`` explicitly
where it wants the widened path, and the dedicated inertness test below
proves the UNDECLARED path (every real Python/Go lane through this build,
A-227) still renders the exact four-bucket, kill-signal-free artifact it
always has.

Fixtures use the real :class:`~assay.adapters.python.PythonAdapter` and
``python:bool-const-flip`` -- the table itself is deliberately language-free
(carve §3.6: "it contains zero SQL knowledge"), so a real SQL fixture would
prove nothing about this module that a real Python one does not; SQL's own
end-to-end proof lives in ``test_cli_run.py``
(``test_run_evaluates_a_real_r2_pass_end_to_end_for_sql``, P34/W6).
"""

from __future__ import annotations

import subprocess
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay import mutation
from assay.adapters.python import PythonAdapter
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import MutationTarget, run_mutation
from assay.runner import execute_command

#: One flag per row of the classification table (carve §3.6), keyed by its
#: own `lineno` so the fake process runner below can decide EVERY mutant's
#: behaviour -- including which bytes (if any) it writes to the declared
#: `equivalence_artifact` -- from the mutated file's own content, never from
#: submission order or position (AUTHORING.md §3b.A's own discipline, the
#: same one `test_mutation_isolation.py` already applies one bucket over).
_TEXT = (
    "def flags():\n"
    "    a = True\n"  # line 2: PASS, artifact present & DIFFERENT -> survived
    "    b = True\n"  # line 3: FAIL, artifact present & DIFFERENT -> killed
    "    c = True\n"  # line 4: PASS, artifact ABSENT               -> crashed
    "    d = True\n"  # line 5: FAIL, artifact ABSENT               -> crashed
    "    e = True\n"  # line 6: PASS, artifact present & SAME       -> equivalent
    "    f = True\n"  # line 7: FAIL, artifact present & SAME       -> equivalent
    "    g = True\n"  # line 8: ERROR (crash)                       -> crashed
    "    h = True\n"  # line 9: BUDGET_EXCEEDED (command timeout)   -> budget_exceeded
    "    return a, b, c, d, e, f, g, h\n"
)
_TARGETS = (
    MutationTarget(
        path="pkg/flags.py", text=_TEXT, lines=frozenset(range(2, 10))
    ),
)
_OPERATORS = ("python:bool-const-flip",)
_ARTIFACT = "dump.txt"
_BASELINE_BYTES = b"BASELINE-DUMP"
_DIFFERENT_BYTES = b"MUTANT-DUMP"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _seed_repo(tmp_path: Path, name: str) -> GitRepo:
    repo = GitRepo(path=tmp_path / name)
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    # A-140/A-175: the declared artifact/signal paths must be git-ignored --
    # each mutant's own post-run dirty check refuses the whole run if its
    # command leaves an un-ignored file behind, mirroring the identical
    # `.gitignore` requirement `test_runner_run_lane_r2.py`'s own coverage
    # fixtures already carry for `cov.json`.
    repo.write(".gitignore", f"{_ARTIFACT}\nsignal.txt\n")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    return repo


class _SynchronousExecutor:
    """The identical minimal fake executor `test_mutation_isolation.py`
    uses: ``submit`` runs immediately, no thread, no timing -- deterministic
    on every machine, and still a genuine test of per-job attribution
    because each `Future` is tied to its own ``(job, index)`` pair."""

    def __enter__(self) -> "_SynchronousExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def submit(self, fn, *args):
        future: Future = Future()
        future.set_result(fn(*args))
        return future


def _decide_by_mutated_flag(repo_path: Path, *, kill_signal_artifact: str | None = None):
    """The fake `process_runner` driving every row of the table: the
    BASELINE call (``cwd == repo_path``) always PASSes and writes nothing --
    `run_mutation` never re-runs the baseline (A-119), so this branch exists
    only so a bug that DID call it back would be visible rather than
    silently mistaken for a mutant. Every MUTANT call reads which flag its
    own snapshot's file actually flipped and decides exit status AND
    artifact bytes from THAT real, isolated content."""

    def decide(argv, *, env, cwd, timeout):
        cwd_path = Path(cwd)
        if cwd_path == repo_path:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        text = (cwd_path / "pkg" / "flags.py").read_text(encoding="utf-8")

        def write_artifact(data: bytes) -> None:
            (cwd_path / _ARTIFACT).write_bytes(data)

        def write_signal(data: bytes) -> None:
            assert kill_signal_artifact is not None
            (cwd_path / kill_signal_artifact).write_bytes(data)

        if "a = False" in text:  # PASS, artifact DIFFERENT -> survived
            write_artifact(_DIFFERENT_BYTES)
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        if "b = False" in text:  # FAIL, artifact DIFFERENT -> killed
            write_artifact(_DIFFERENT_BYTES)
            if kill_signal_artifact is not None:
                write_signal(b"  ERROR: check constraint violated  \n")
            return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")
        if "c = False" in text:  # PASS, artifact ABSENT -> crashed
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        if "d = False" in text:  # FAIL, artifact ABSENT -> crashed
            return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")
        if "e = False" in text:  # PASS, artifact SAME as baseline -> equivalent
            write_artifact(_BASELINE_BYTES)
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        if "f = False" in text:  # FAIL, artifact SAME as baseline -> equivalent
            write_artifact(_BASELINE_BYTES)
            return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")
        if "g = False" in text:  # ERROR -- crashed, unaffected by the artifact
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if "h = False" in text:  # BUDGET_EXCEEDED -- unaffected by the artifact
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        raise AssertionError(f"unexpected mutated content in {cwd}: {text!r}")

    return decide


def _run(
    tmp_path: Path,
    *,
    kill_signal_artifact: str | None = None,
) -> mutation.Mutation:
    lane = make_lane(argv=("pytest", "-q"))
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    decide = _decide_by_mutated_flag(repo.path, kill_signal_artifact=kill_signal_artifact)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS
    plan = make_plan(lane)
    deadline = make_deadline()

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=50,
            operators=_OPERATORS,
            process_runner=decide,
            clock=_clock,
            executor_factory=lambda jobs: _SynchronousExecutor(),
            equivalence_artifact=_ARTIFACT,
            kill_signal_artifact=kill_signal_artifact,
            baseline_equivalence=_BASELINE_BYTES,
        )
    assert result is not None and not isinstance(result, str)
    return result


# =============================================================================
# The classification table itself (carve §3.6), every row, one real run
# =============================================================================


def test_every_row_of_the_classification_table_when_equivalence_artifact_is_declared(
    tmp_path: Path,
):
    """Six of the eight rows in one run (the two ``BUDGET_EXCEEDED``/
    ``ERROR`` rows are already `_classify_mutant_result`'s OWN unaffected
    branches, exercised here too so the widened function's ``ERROR``/
    ``BUDGET_EXCEEDED`` arms are proven reachable and unaffected, not
    merely assumed): PASS+absent, FAIL+absent -> ``crashed``; PASS+equal,
    FAIL+equal -> ``equivalent``; PASS+different -> ``survived``;
    FAIL+different -> ``killed`` (this bucket's own dedicated, richer test
    is below -- this run proves it happens ALONGSIDE every other row in the
    same claim, not in isolation)."""
    result = _run(tmp_path)

    assert result.candidate_count == 8
    assert result.total == 8
    assert [o.lineno for o in result.survived] == [2]
    assert [o.lineno for o in result.killed] == [3]
    assert sorted(o.lineno for o in result.crashed) == [4, 5, 8]
    assert sorted(o.lineno for o in result.equivalent) == [6, 7]
    assert [o.lineno for o in result.budget_exceeded] == [9]
    # unattributed: no `kill_signal_artifact` was declared for this run.
    assert result.killed[0].kill_signal is None


# =============================================================================
# The killed row's own dedicated proof -- the feature's headline outcome
# =============================================================================


def test_the_killed_row_carries_its_kill_signal_verbatim_and_a_missing_signal_reclassifies_to_crashed(
    tmp_path: Path,
):
    """THE test that exercises the classification table's headline outcome:
    a mutation genuinely CAUGHT by the suite, under DECLARED attribution.

    Row asserted: ``FAIL`` + artifact present & DIFFERENT from the
    baseline's own bytes -> ``killed``, carrying the command's own
    ``kill_signal`` string, bounded, decoded, STRIPPED, and recorded
    VERBATIM (carve §3.6) -- proven here against a real single-use
    `safeio` reservation, not a hand-built `MutantOutcome`.

    Paired within the SAME run: the OTHER kill-shaped mutant (``d``, FAIL +
    artifact absent) never reaches ``killed`` at all here either (still
    ``crashed``, the artifact-absent row) -- and critically, mutant ``b``'s
    OWN kill_signal file is genuinely written by ITS OWN command
    invocation, never by ``d``'s, proving the per-mutant reservation is not
    shared or leaked across mutants in the same run.
    """
    result = _run(tmp_path, kill_signal_artifact="signal.txt")

    assert [o.lineno for o in result.killed] == [3]
    killed = result.killed[0]
    assert killed.kill_signal == "ERROR: check constraint violated"
    # `d` (FAIL, artifact absent) is `crashed` regardless of attribution --
    # the artifact-absent rule outranks kill_signal entirely (§3.6: no
    # measurement happened at all, so there is nothing to attribute).
    assert 5 in [o.lineno for o in result.crashed]


def test_a_killed_mutant_with_no_kill_signal_file_reclassifies_to_crashed(tmp_path: Path):
    """The negative half of the headline row, isolated: DECLARED
    attribution (`kill_signal_artifact` set) requires a signal on EVERY
    killed entry (§3.6/A-223e) -- a mutant that would otherwise land in
    ``killed`` (FAIL, artifact present & different) but whose command never
    wrote the signal file did not meet the lane's own declared contract,
    and is reclassified ``crashed`` instead. Built directly (not through
    `_run`'s shared fixture) so the ONE mutant under test is unambiguous."""
    lane = make_lane(argv=("pytest", "-q"))
    text = "def flags():\n    b = True\n    return b\n"
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(".gitignore", f"{_ARTIFACT}\nsignal.txt\n")
    repo.write("pkg/flags.py", text)
    repo.commit_all("add flags")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    def decide(argv, *, env, cwd, timeout):
        cwd_path = Path(cwd)
        if cwd_path == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        mutated = (cwd_path / "pkg" / "flags.py").read_text(encoding="utf-8")
        assert "b = False" in mutated
        # writes the equivalence artifact (so this mutant is NOT `crashed`
        # for the unrelated artifact-absent reason) but NEVER the kill
        # signal file -- the one fact this test isolates.
        (cwd_path / _ARTIFACT).write_bytes(_DIFFERENT_BYTES)
        return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS
    plan = make_plan(lane)
    deadline = make_deadline()
    target = MutationTarget(path="pkg/flags.py", text=text, lines=frozenset({2}))

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=(target,),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=50,
            operators=_OPERATORS,
            process_runner=decide,
            clock=_clock,
            equivalence_artifact=_ARTIFACT,
            kill_signal_artifact="signal.txt",
            baseline_equivalence=_BASELINE_BYTES,
        )

    assert result is not None and not isinstance(result, str)
    assert result.killed == ()
    assert len(result.crashed) == 1
    assert result.crashed[0].lineno == 2


# =============================================================================
# Inertness (O8): the undeclared lane is byte-identical, before and after
# =============================================================================


def test_a_lane_without_an_equivalence_artifact_is_byte_identical_before_and_after(
    tmp_path: Path,
):
    """(carve §3.6/A-279's own inertness requirement) With NO
    ``equivalence_artifact`` declared -- every real Python/Go lane through
    this build (A-227) -- `run_mutation` takes the UNCHANGED
    `_classify_mutant_result` path: the same four buckets, no `equivalent`
    entries ever, and `kill_signal` stays `None` on every entry including a
    genuine kill. Called with every P34 parameter at its default (never
    even naming `equivalence_artifact=None` explicitly), so this proves the
    EXISTING call shape -- the one every pre-P34 caller still uses -- is
    untouched, not merely that passing `None` behaves the same as passing
    `None`."""
    lane = make_lane(argv=("pytest", "-q"))
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    decide = _decide_by_mutated_flag(repo.path)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS
    plan = make_plan(lane)
    deadline = make_deadline()

    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=50,
            operators=_OPERATORS,
            process_runner=decide,
            clock=_clock,
            executor_factory=lambda jobs: _SynchronousExecutor(),
        )

    assert result is not None and not isinstance(result, str)
    assert result.candidate_count == 8
    assert result.total == 8
    # PASS -> survived, FAIL -> killed, unconditionally: `c`/`e` (PASS) join
    # `a` in survived, `d`/`f` (FAIL) join `b` in killed -- the exit-status-
    # only mapping the widened table would otherwise have split by artifact
    # presence/bytes.
    assert sorted(o.lineno for o in result.survived) == [2, 4, 6]
    assert sorted(o.lineno for o in result.killed) == [3, 5, 7]
    assert [o.lineno for o in result.crashed] == [8]
    assert [o.lineno for o in result.budget_exceeded] == [9]
    assert result.equivalent == ()
    assert all(o.kill_signal is None for o in result.killed)


# =============================================================================
# A baseline that declares an artifact and does not write it (runner-level;
# see also test_runner_run_lane_r2.py) -- run_mutation's OWN half: it must
# never be called with equivalence_artifact set and baseline_equivalence
# unresolved, so it refuses that combination outright rather than silently
# comparing every mutant's bytes against None (A-279).
# =============================================================================


def test_run_mutation_refuses_a_declared_artifact_with_no_baseline_bytes(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    decide = _decide_by_mutated_flag(repo.path)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    plan = make_plan(lane)
    deadline = make_deadline()

    with pytest.raises(ValueError, match="baseline_equivalence is None"):
        with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=plan,
                deadline=deadline,
                targets=_TARGETS,
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=50,
                operators=_OPERATORS,
                process_runner=decide,
                clock=_clock,
                equivalence_artifact=_ARTIFACT,
                baseline_equivalence=None,
            )


def test_run_mutation_accepts_a_declared_artifact_with_real_baseline_bytes_the_control(
    tmp_path: Path,
):
    """The must-succeed control for the refusal above: the identical
    declaration, with real resolved baseline bytes, proceeds normally."""
    result = _run(tmp_path)
    assert result.total == 8


# =============================================================================
# The per-mutant reservation: arm() unlinks a pre-planted stale artifact
# =============================================================================
#
# A truly fresh P22 replacement snapshot can never itself contain a stale
# artifact (carve §3.6: "each mutant runs in its own fresh replacement
# snapshot materialised from the committed seed... the artifacts are
# untracked, so they are absent at the start of every mutant" -- proven
# structurally by `assay.isolation`'s own `tempfile.mkdtemp` construction,
# not asserted here). What IS this module's own responsibility is that its
# reservation call site goes through `safeio`'s reserve-then-arm discipline
# -- which is what actually performs the unlink -- rather than a raw
# read that a stale byte could leak through. Proven directly against the
# private reservation helper with a plain directory, mirroring exactly how
# `tests/test_safeio.py` itself proves `arm()`'s own unlink contract; no
# public `run_mutation` fixture could exercise "a stale file already sat at
# this path when the reservation was taken" at all, since nothing above
# this layer can ever construct that precondition through the shipped
# surface.


def test_arm_artifact_reservation_unlinks_a_preplanted_stale_file(tmp_path: Path):
    stale = b"stale schema dump -- must never leak into a comparison"
    (tmp_path / "dump.txt").write_bytes(stale)

    reservation = mutation._arm_artifact_reservation(tmp_path, "dump.txt", limit=1024)

    # `arm()` already unlinked the pre-existing regular file BEFORE this
    # point -- nothing wrote a replacement, so the read is a genuine
    # "absent", never the stale bytes leaking through.
    assert reservation.consume() is None


def test_arm_artifact_reservation_still_reads_a_genuine_write_the_must_succeed_control(
    tmp_path: Path,
):
    """The paired control: the identical stale-then-armed sequence, but
    with a REAL write landing after `arm()` -- proving the unlink does not
    also break the ordinary case where the command genuinely writes its
    output."""
    (tmp_path / "dump.txt").write_bytes(b"stale schema dump")

    reservation = mutation._arm_artifact_reservation(tmp_path, "dump.txt", limit=1024)
    (tmp_path / "dump.txt").write_bytes(b"fresh dump")

    assert reservation.consume() == b"fresh dump"


# =============================================================================
# `_read_kill_signal`: bounded, UTF-8, stripped, verbatim
# =============================================================================


def test_read_kill_signal_decodes_strips_and_records_verbatim(tmp_path: Path):
    # Written AFTER arming, exactly like the real flow: `arm()` runs BEFORE
    # the command that produces the signal file ever starts.
    reservation = mutation._arm_artifact_reservation(tmp_path, "signal.txt", limit=1024)
    (tmp_path / "signal.txt").write_bytes(b"  ERROR: check constraint violated  \n")

    assert mutation._read_kill_signal(reservation) == "ERROR: check constraint violated"


def test_read_kill_signal_returns_none_for_a_missing_artifact_the_control(tmp_path: Path):
    reservation = mutation._arm_artifact_reservation(tmp_path, "signal.txt", limit=1024)

    assert mutation._read_kill_signal(reservation) is None


def test_read_kill_signal_treats_empty_after_strip_as_no_signal(tmp_path: Path):
    reservation = mutation._arm_artifact_reservation(tmp_path, "signal.txt", limit=1024)
    (tmp_path / "signal.txt").write_bytes(b"   \n\n  ")

    assert mutation._read_kill_signal(reservation) is None


def test_read_kill_signal_refuses_invalid_utf8(tmp_path: Path):
    reservation = mutation._arm_artifact_reservation(tmp_path, "signal.txt", limit=1024)
    (tmp_path / "signal.txt").write_bytes(b"\xff\xfe not valid utf-8")

    with pytest.raises(AssayError) as excinfo:
        mutation._read_kill_signal(reservation)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_killed_mutant_whose_kill_signal_is_not_valid_utf8_is_fatal(tmp_path: Path):
    """The end-to-end shape of the decode failure above: a mutant's own
    command writes an undecodable kill_signal file, and `run_mutation`
    raises rather than silently discarding or mis-recording it -- the same
    "structural failure, not a per-mutant classification" precedent A-195
    already sets for a mutant that leaves Git-visible dirt."""
    lane = make_lane(argv=("pytest", "-q"))
    text = "def flags():\n    b = True\n    return b\n"
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(".gitignore", f"{_ARTIFACT}\nsignal.txt\n")
    repo.write("pkg/flags.py", text)
    repo.commit_all("add flags")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    def decide(argv, *, env, cwd, timeout):
        cwd_path = Path(cwd)
        if cwd_path == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")
        (cwd_path / _ARTIFACT).write_bytes(_DIFFERENT_BYTES)
        (cwd_path / "signal.txt").write_bytes(b"\xff\xfe not valid utf-8")
        return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS
    plan = make_plan(lane)
    deadline = make_deadline()
    target = MutationTarget(path="pkg/flags.py", text=text, lines=frozenset({2}))

    with pytest.raises(AssayError) as excinfo:
        with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
            run_mutation(
                baseline=baseline,
                prepared=prepared,
                plan=plan,
                deadline=deadline,
                targets=(target,),
                adapter=PythonAdapter(),
                jobs=1,
                max_mutants=50,
                operators=_OPERATORS,
                process_runner=decide,
                clock=_clock,
                equivalence_artifact=_ARTIFACT,
                kill_signal_artifact="signal.txt",
                baseline_equivalence=_BASELINE_BYTES,
            )
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
