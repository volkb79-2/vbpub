"""Blind-review combined-axis attacks on P23's committed-snapshot state
machine, plus the two audit gaps the migration left behind.

Everything here is REVIEWER-owned. It never edits, imports from, or restates
the byte-locked packet under ``nyxloom-trove/carve-assets/P23/``; each fixture
is built independently, from real ``git`` state, so a defect in one cannot
make the other look green.

The module covers seven things the landed suite did not:

1. **The locked ``FourSiteAdapter`` defect (SB-P23-01), adjudicated.** Two
   locked cases fail because the packet's own adapter records
   ``lineno=index + 1`` for every ``<`` it finds, while its generated source
   puts every ``<`` on line 2 -- so P21's frozen ``_validate_sites``
   correctly refuses the batch. That refusal is the CORRECT behaviour, and
   the max-1/max contract it hides ("at max-1 and max, execute all sites")
   is proved here against a CONFORMING adapter, plus a negative that pins
   the refusal itself. The locked assets are untouched.
2. **A diverged, symbolic ``judge.base`` (new combined axis).** No packet
   axis varies ``judge.base`` at all. A P22 snapshot carries one commit's
   object closure and no refs, so both convenient readings of the handoff's
   own "using the relocated lane and original ``judge.base``" -- passing the
   symbolic name into the snapshot, or ``rev-parse``-ing it to the base
   branch's tip -- name something the snapshot cannot contain.
3. **One blob at two paths, executed as two mutants (new combined axis).**
   The packet routes A-196's identity-multiplicity axis to P29/P30; A-191
   binds it to P23 NOW, and nothing exercised it through R2's own
   replacement units.
4. **A mutant that dirties -- or commits inside -- its OWN snapshot.** The
   terminal table's "mutant path is payload-free, not ``crashed``" row, its
   "unit commits" sibling, and their shared "no later unit" consequence had
   no oracle in either suite.
5. **The direct R0-only path's own plan identity and ``HEAD_CHANGED``
   terminal**, both of which lost their only oracle when P23 moved the
   tests that used to reach them onto the higher-rigor path.
6. **R2 target selection keeps all four landed gates** (phase 2). Narrowing
   it to source-root containment made a changed non-Python file under a
   source root collapse the whole verdict, and made a changed ``test_*.py``
   a real mutant identity.
7. **A canary half that dirties its own snapshot**, for both the control and
   the transform, with the control case additionally proving the transform
   half is never materialised afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest
from conftest import GitRepo, make_lane, make_r1_judge, make_r2_judge, make_r3_judge

from assay import git as git_module
from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import CanaryConfig, JudgeConfig, MutationConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import MutationSite, line_for_offset

MOMENT = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def _clock() -> datetime:
    return MOMENT


class CountingMonotonic:
    """A strictly increasing injected monotonic source (AUTHORING §3b.A: no
    wall clock, no sleep). The step is far smaller than any lane budget here,
    so nothing in this module can expire by accident."""

    def __init__(self, *, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        observed = self.value
        self.value += self.step
        return observed


def _python_judge(
    repo: GitRepo,
    *,
    base: str,
    max_mutants: int = 3,
    source_root: str = "pkg",
) -> JudgeConfig:
    return make_r2_judge(
        language="python",
        source_root_paths=(repo.path / source_root,),
        base=base,
        mutation=MutationConfig(
            jobs=1, max_mutants=max_mutants, operators=("python:compare-swap",)
        ),
    )


def _seed_python_project(repo: GitRepo, *, head_source: str) -> tuple[str, str]:
    """A real two-commit Python project with a committed, tracked support
    file the command can scribble on and a git-ignored coverage artifact."""
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f(x):\n    return 0\n")
    repo.write("support.txt", "committed support\n")
    base_rev = repo.commit_all("base")
    repo.write("pkg/mod.py", head_source)
    head_rev = repo.commit_all("introduce mutation sites")
    return base_rev, head_rev


# --- 1. the locked FourSiteAdapter defect (SB-P23-01), adjudicated -----------


class _ConformingMultiSiteAdapter:
    """The locked ``FourSiteAdapter``'s shape with its TWO defects removed:
    each site's ``lineno`` is DERIVED from its own byte offset
    (:func:`~assay.mutation.line_for_offset`) instead of guessed from the
    site's index, and the three target-selection members every real
    :class:`~assay.adapters.base.LanguageAdapter` carries are actually
    declared, so R2's own landed
    :func:`~assay.mutation.resolve_mutation_targets` gates can run against
    it. Everything else -- one site per ``<``, ``compare-swap``, the
    ``limit`` slice -- is identical."""

    name = "conforming-multi-site"
    source_globs = ("*.py",)
    excluded_dir_names = frozenset()
    # (P34/W3) `run_lane`'s own external-tool preflight (A-253) now reads
    # this attribute unconditionally for every resolved adapter, before
    # this test's R2 body ever runs -- so it needs a real value the same
    # way every shipped adapter already declares one, not a target-
    # selection member this class happened to omit before that reader
    # existed.
    external_tools: tuple[str, ...] = ()

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def generate_mutation_sites(self, text, lines, *, operators, limit):
        raw = text.encode("utf-8")
        offsets = [index for index, value in enumerate(raw) if value == ord("<")]
        sites = tuple(
            MutationSite(
                start_byte=offset,
                end_byte=offset + 1,
                replacement=b"<=",
                lineno=line_for_offset(raw, offset),
                operator="python:compare-swap",
                description="< to <=",
            )
            for offset in offsets
        )
        return sites[:limit]


class _MisreportedLinenoAdapter(_ConformingMultiSiteAdapter):
    """The locked adapter's own defect, isolated: ``lineno = index + 1``,
    which is only ever right when each ``<`` happens to sit on its own
    line."""

    name = "misreported-lineno"

    def generate_mutation_sites(self, text, lines, *, operators, limit):
        raw = text.encode("utf-8")
        offsets = [index for index, value in enumerate(raw) if value == ord("<")]
        sites = tuple(
            MutationSite(
                start_byte=offset,
                end_byte=offset + 1,
                replacement=b"<=",
                lineno=index + 1,
                operator="python:compare-swap",
                description="< to <=",
            )
            for index, offset in enumerate(offsets)
        )
        return sites[:limit]


@pytest.mark.parametrize("site_count", [2, 3], ids=["max-minus-one", "max"])
def test_max_minus_one_and_max_execute_every_discovered_site(
    git_repo: GitRepo, site_count: int
):
    """The contract the two locked failures hide: at ``max_mutants - 1`` and
    at exactly ``max_mutants``, EVERY discovered site is executed -- no
    truncation, no sentinel, one process per site plus the baseline.

    Proved against a conforming adapter over the identical generated source
    the locked case uses (``x < 0 and x < 1 ...``, so every site really does
    share one line -- the shape that makes an index-derived ``lineno``
    wrong)."""
    conditions = " and ".join(f"x < {value}" for value in range(site_count))
    source = f"def check(x):\n    return {conditions}\n"
    base_rev, head_rev = _seed_python_project(git_repo, head_source=source)
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=_python_judge(git_repo, base=base_rev),
        argv=("check",),
    )
    calls: list[str] = []

    def process(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        calls.append("baseline" if text == source else "mutant")
        return subprocess.CompletedProcess(
            list(argv), 0 if text == source else 1, "", ""
        )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=_ConformingMultiSiteAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert calls == ["baseline", *("mutant" for _ in range(site_count))]
    r2 = verdict.claims[1]
    assert r2.status is Outcome.PASS
    assert r2.mutation.candidate_count == site_count
    assert r2.mutation.total == site_count
    assert len(r2.mutation.killed) == site_count
    # every killed identity names its REAL line, not its submission index
    assert {outcome.lineno for outcome in r2.mutation.killed} == {2}


def test_an_adapter_that_misreports_a_site_line_is_refused_before_any_mutant(
    git_repo: GitRepo,
):
    """The negative half, and the exact reason the two locked cases are red:
    P21's frozen ``_validate_sites`` refuses a batch whose recorded
    ``lineno`` is not the line its ``start_byte`` really starts on. R2 renders
    the discovery-contract terminal, R0's own real claim survives, and no
    mutant process or replacement snapshot is ever started.

    This is a defect in the locked fixture's adapter, NOT in P23: weakening
    the validator to accept it would let an adapter publish mutant identities
    that point at lines the splice never touched."""
    source = "def check(x):\n    return x < 0 and x < 1\n"
    base_rev, head_rev = _seed_python_project(git_repo, head_source=source)
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=_python_judge(git_repo, base=base_rev),
        argv=("check",),
    )
    calls: list[str] = []

    def process(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        calls.append("baseline" if text == source else "mutant")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=_MisreportedLinenoAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert calls == ["baseline"], "no mutant may run from an unvalidated batch"
    assert verdict.claims[0].status is Outcome.PASS
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.ERROR,
        ReasonCode.MUTATION_DISCOVERY_FAILED,
    )
    assert r2.mutation is None


# --- 2. a diverged, symbolic judge.base (reviewer-owned combined axis) -------


def test_a_diverged_symbolic_judge_base_resolves_to_its_forkpoint(
    git_repo: GitRepo,
):
    """Combined axis the packet never pairs: a SYMBOLIC ``judge.base`` whose
    branch has DIVERGED from the lane commit, crossed with the higher-rigor
    committed-snapshot route.

    A P22 snapshot holds one commit's reachable object closure -- no refs, and
    none of a diverged branch's own unique history. Two convenient readings of
    the handoff's "using the relocated lane and original ``judge.base``" both
    die here, and BOTH were killed by running them:

    * passing ``"trunk"`` straight into the snapshot -- the snapshot has no
      such ref, so ``rev-parse`` fails and R1 renders ``ERROR``/``GIT_FAILED``;
    * resolving it with a plain ``rev-parse`` against the consumer instead of
      the merge base -- ``trunk``'s tip is not an ancestor of the lane commit,
      so its object is absent from the closure and the in-snapshot diff fails
      the same way.

    The landed behaviour resolves the fork point ONCE, against the consumer's
    real repository, before any snapshot exists -- a value that is an ancestor
    of the lane commit BY CONSTRUCTION and therefore always inside the closure.
    """
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f():\n    return 1\n")
    fork_point = repo.commit_all("fork point")

    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/mod.py", "def f():\n    return 1\n\n\ndef g():\n    return 2\n")
    head_rev = repo.commit_all("feature adds g()")

    # `main` moves ON AFTER the fork, so its tip is NOT an ancestor of HEAD.
    repo.git("checkout", "-q", "main")
    repo.write("unrelated.txt", "main moved on\n")
    diverged_tip = repo.commit_all("main diverges")
    repo.git("checkout", "-q", "feature")

    assert diverged_tip != fork_point
    assert repo.head() == head_rev
    ancestry = subprocess.run(
        ["git", "-C", str(repo.path), "merge-base", "--is-ancestor", diverged_tip, head_rev]
    )
    assert ancestry.returncode != 0, "the base branch tip must really be unreachable"

    judge = make_r1_judge(
        language="python",
        source_root_paths=(repo.path / "pkg",),
        base="main",  # SYMBOLIC, and diverged
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("check",))

    def process(argv, *, env, cwd, timeout):
        document = {
            "files": {
                "pkg/mod.py": {
                    "executed_lines": [1, 2, 4, 5],
                    "missing_lines": [],
                    "excluded_lines": [],
                }
            }
        }
        (Path(cwd) / "cov.json").write_text(json.dumps(document), encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=repo.path,
        project_root=repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    r1 = verdict.claims[1]
    assert (r1.status, r1.reason_code) == (Outcome.PASS, None)
    assert r1.coverage.considered == 1
    assert r1.coverage.executable == 2, "the fork-point diff, not an empty one"
    # The artifact binds the FORK POINT, never the declared symbolic name and
    # never the diverged tip -- P16's "the FULL resolved comparison commit".
    assert verdict.judgment.resolved.base == fork_point


# --- 3. one blob at two paths stays two replacement units (A-191) ------------


def test_identical_bytes_at_two_paths_stay_two_independent_mutants(
    git_repo: GitRepo,
):
    """Combined axis: git's blob-sharing (one OID at two paths, which P22's
    own review found in real fixtures) crossed with R2's replacement units.

    A-191 binds this to P23 NOW ("treats snapshot data per tree entry and
    repo-top-relative path, never as an OID-to-path map"); the packet's axis
    table routes the identity-multiplicity witness forward to P29/P30, so
    nothing exercised it through R2. A source cache keyed by content, or a
    ``materialize_replacement`` result memoized by blob OID, collapses these
    two paths into one target and reports a fully-tested change while one
    path never ran.
    """
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/a.py", "def f(x):\n    return 0\n")
    repo.write("pkg/b.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("base")
    shared = "def f(x):\n    return x > 0\n"
    repo.write("pkg/a.py", shared)
    repo.write("pkg/b.py", shared)
    head_rev = repo.commit_all("identical bytes at two paths")

    assert repo.git("rev-parse", f"{head_rev}:pkg/a.py") == repo.git(
        "rev-parse", f"{head_rev}:pkg/b.py"
    ), "the fixture must really share one blob OID"

    lane = make_lane(
        rigor=("R0", "R2"),
        judge=_python_judge(repo, base=base_rev, max_mutants=10),
        argv=("check",),
    )
    observed: list[tuple[str, str]] = []

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        pair = (
            (cwd / "pkg/a.py").read_text(encoding="utf-8"),
            (cwd / "pkg/b.py").read_text(encoding="utf-8"),
        )
        observed.append(pair)
        mutated = [text for text in pair if text != shared]
        return subprocess.CompletedProcess(list(argv), 0 if not mutated else 1, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=repo.path,
        project_root=repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    r2 = verdict.claims[1]
    assert r2.status is Outcome.PASS
    assert r2.mutation.candidate_count == 2
    assert r2.mutation.total == 2
    assert [outcome.path for outcome in r2.mutation.killed] == ["pkg/a.py", "pkg/b.py"]
    # Each replacement unit changed EXACTLY one of the two identical paths --
    # never both (one shared write) and never neither (a collapsed cache).
    assert [sum(1 for text in pair if text != shared) for pair in observed[1:]] == [1, 1]
    assert observed[0] == (shared, shared), "the baseline unit is unmodified"


# --- 3b. R2 target selection keeps all four landed gates (phase-2 repair) ----


def _seed_mixed_source_root(repo: GitRepo, *, extra: dict[str, tuple[str, str]]):
    """A real two-commit project whose declared source root ``pkg`` contains
    one genuine module plus whatever *extra* ``{path: (base, head)}`` files
    the caller wants changed alongside it."""
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f(x):\n    return 0\n")
    for rel, (base_text, _head) in extra.items():
        repo.write(rel, base_text)
    base_rev = repo.commit_all("base")
    repo.write("pkg/mod.py", "def f(x):\n    return x > 0\n")
    for rel, (_base, head_text) in extra.items():
        repo.write(rel, head_text)
    head_rev = repo.commit_all("change the module and its neighbours")
    return base_rev, head_rev


def _r2_paths(claim) -> list[str]:
    if claim.mutation is None:
        return []
    return sorted(
        {
            outcome.path
            for bucket in ("killed", "survived", "crashed", "budget_exceeded")
            for outcome in getattr(claim.mutation, bucket)
        }
    )


def test_a_changed_non_source_file_under_a_source_root_is_not_a_target(
    git_repo: GitRepo,
):
    """Reproduced phase-2 defect: R2 target selection had been narrowed to
    source-root containment alone, dropping the adapter's ``source_globs``
    gate. A changed ``pkg/NOTES.md`` then reached
    ``PythonAdapter.generate_mutation_sites``, which raises
    ``MutationDiscoveryError`` on unparseable text -- so an ordinary
    repository that measured normally before P23 collapsed to a whole-verdict
    ``ERROR``/``MUTATION_DISCOVERY_FAILED``.

    This is the false-refusal shape, not a false PASS: nothing about the
    Python module changed, and the lane still cannot render its R2 claim."""
    base_rev, head_rev = _seed_mixed_source_root(
        git_repo,
        extra={"pkg/NOTES.md": ("# Notes\n", "# Notes\n\nprose that is not python\n")},
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=_python_judge(git_repo, base=base_rev, max_mutants=20),
        argv=("check",),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            list(argv), 0, "", ""
        ),
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    r2 = verdict.claims[1]
    assert r2.reason_code is not ReasonCode.MUTATION_DISCOVERY_FAILED
    assert (r2.status, r2.reason_code) == (Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED)
    assert _r2_paths(r2) == ["pkg/mod.py"]


def test_a_changed_test_file_under_a_source_root_is_never_mutated(
    git_repo: GitRepo,
):
    """The second half of the same repair, and the false-EVIDENCE direction:
    with only the containment gate, a changed ``pkg/test_mod.py`` became a
    real mutant identity in the R2 payload. A suite that "kills" that mutant
    killed it by having its own test broken -- an experiment about the test
    file, reported as evidence about the code under test.

    ``is_test_path`` is the adapter's own answer and is exactly why
    ``resolve_mutation_targets`` adds it on top of R1's ``_is_considered``."""
    base_rev, head_rev = _seed_mixed_source_root(
        git_repo,
        extra={
            "pkg/test_mod.py": (
                "def test_f():\n    assert 0 == 0\n",
                "def test_f():\n    assert f(1) > 0\n",
            )
        },
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=_python_judge(git_repo, base=base_rev, max_mutants=20),
        argv=("check",),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            list(argv), 0, "", ""
        ),
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    r2 = verdict.claims[1]
    assert r2.mutation.candidate_count == 1
    assert _r2_paths(r2) == ["pkg/mod.py"]
    assert PythonAdapter().is_test_path("pkg/test_mod.py"), "the fixture must be a test path"


def test_a_changed_file_outside_every_source_root_is_still_never_a_target(
    git_repo: GitRepo,
):
    """The gate that was already there, kept as a control so the repair
    cannot be satisfied by dropping selection entirely: a changed file
    outside every declared source root contributes no candidate."""
    base_rev, head_rev = _seed_mixed_source_root(
        git_repo,
        extra={"other/elsewhere.py": ("y = 0\n", "y = 1 > 0\n")},
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=_python_judge(git_repo, base=base_rev, max_mutants=20),
        argv=("check",),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            list(argv), 0, "", ""
        ),
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert _r2_paths(verdict.claims[1]) == ["pkg/mod.py"]


# --- 4. a mutant that dirties its OWN snapshot ------------------------------


def _lane_with_scribbling_mutant(repo: GitRepo, base_rev: str, original: str):
    judge = _python_judge(repo, base=base_rev, max_mutants=10)
    return make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))


def _scribble_on_mutation(original: str, calls: list[str]):
    """A process runner that writes to a TRACKED support file only when it
    is looking at mutated source -- so the baseline unit stays clean and the
    dirt is unambiguously the mutant's own."""

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        text = (cwd / "pkg/mod.py").read_text(encoding="utf-8")
        if text != original:
            calls.append("mutant")
            (cwd / "support.txt").write_text("the mutant scribbled\n", encoding="utf-8")
        else:
            calls.append("baseline")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    return process


def test_a_mutant_that_dirties_its_snapshot_is_payload_free_never_crashed(
    git_repo: GitRepo,
):
    """A-195's mutant half, which neither suite reached: the disposability of
    a snapshot does not launder a unit that no longer names its own commit.

    The mutant's own post-run check must stop the whole R2 claim as the
    unchanged payload-free ``NO_MEASUREMENT``/``DIRTY_TREE`` pair -- never
    folded into P21's ``crashed``/``EXEC_FAILED`` bucket (which would let a
    self-dirtying command look like an ordinary broken mutant), and never a
    partial ``killed`` sample. The consumer's own repository stays byte- and
    status-identical throughout."""
    original = "def f(x):\n    return x > 0\n"
    base_rev, head_rev = _seed_python_project(git_repo, head_source=original)
    before_head = git_repo.head()
    before_tree = git_repo.git("write-tree").strip()
    calls: list[str] = []

    verdict = runner.run_lane(
        _lane_with_scribbling_mutant(git_repo, base_rev, original),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_scribble_on_mutation(original, calls),
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert calls == ["baseline", "mutant"]
    assert verdict.claims[0].status is Outcome.PASS
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.DIRTY_TREE,
    )
    assert r2.mutation is None
    assert verdict.judgment is None
    assert git_repo.head() == before_head
    assert git_repo.git("write-tree").strip() == before_tree
    assert git_module.dirty_paths(git_repo.path) == ()
    assert (git_repo.path / "support.txt").read_text(
        encoding="utf-8"
    ) == "committed support\n"


def test_an_r2_orchestration_fault_starts_no_r3_unit(git_repo: GitRepo):
    """The terminal table's "no later unit" / "no R3" consequence of the row
    above, on a lane declaring ``R0,R2,R3``.

    Reviewer repair: this reproduced a real defect. R2 correctly rendered the
    payload-free ``NO_MEASUREMENT``/``DIRTY_TREE`` pair, and then R3 went on
    to materialise TWO more snapshots and publish a judged
    ``FAIL``/``CANARY_SURVIVED`` claim with a full ``judgment.r3`` -- a
    positive canary verdict sitting beside a claim that says the lane could
    not be measured at all. The lane must stop instead, carrying the same
    unchanged pair forward with no canary unit and no judgment.
    """
    original = "def f(x):\n    return x > 0\n"
    base_rev, head_rev = _seed_python_project(git_repo, head_source=original)
    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(git_repo.path / "pkg",),
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=MutationConfig(jobs=1, max_mutants=10, operators=("python:compare-swap",)),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
        base=base_rev,
    )
    lane = make_lane(rigor=("R0", "R2", "R3"), judge=judge, argv=("check",))
    calls: list[str] = []

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=_scribble_on_mutation(original, calls),
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert calls == ["baseline", "mutant"], "no canary control or transform may start"
    assert [claim.rigor for claim in verdict.claims] == ["R0", "R2", "R3"]
    assert verdict.claims[0].status is Outcome.PASS
    for claim in verdict.claims[1:]:
        assert (claim.status, claim.reason_code) == (
            Outcome.NO_MEASUREMENT,
            ReasonCode.DIRTY_TREE,
        )
        assert claim.mutation is None
        assert claim.canary is None
    assert verdict.judgment is None
    assert git_module.dirty_paths(git_repo.path) == ()


def test_a_judged_r2_failure_still_lets_r3_run(git_repo: GitRepo):
    """The control that makes the test above an oracle rather than a blanket
    refusal: a REAL judged R2 outcome (a surviving mutant) is a measurement,
    not an orchestration fault, so both canary halves still run and R3 still
    renders its own judged claim."""
    original = "def f(x):\n    return x > 0\n"
    base_rev, head_rev = _seed_python_project(git_repo, head_source=original)
    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(git_repo.path / "pkg",),
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=MutationConfig(jobs=1, max_mutants=10, operators=("python:compare-swap",)),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
        base=base_rev,
    )
    lane = make_lane(rigor=("R0", "R2", "R3"), judge=judge, argv=("check",))
    units: list[str] = []

    def process(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "pkg/mod.py").read_text(encoding="utf-8")
        units.append("unmodified" if text == original else "modified")
        # nothing is ever caught: the mutant SURVIVES and the canary too
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    # baseline, one surviving mutant, canary control, canary transform
    assert units == ["unmodified", "modified", "unmodified", "modified"]
    r2, r3 = verdict.claims[1], verdict.claims[2]
    assert (r2.status, r2.reason_code) == (Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED)
    assert len(r2.mutation.survived) == 1
    assert (r3.status, r3.reason_code) == (Outcome.FAIL, ReasonCode.CANARY_SURVIVED)
    assert r3.canary is not None
    assert verdict.judgment.r2 is not None and verdict.judgment.r3 is not None


def test_a_canary_transform_that_dirties_its_snapshot_is_a_payload_free_r3(
    git_repo: GitRepo,
):
    """The same A-195 rule on the R3 half the landed suite also never
    reached: the transform's own snapshot check must stop the claim as the
    unchanged payload-free pair rather than publish a canary judgment derived
    from a tree that no longer names its own commit."""
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f():\n    return 1\n")
    repo.write("support.txt", "committed support\n")
    head_rev = repo.commit_all("seed a canary target")
    judge = make_r3_judge(
        language="python",
        source_root_paths=(repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(rigor=("R0", "R3"), judge=judge, argv=("check",))
    original = "def f():\n    return 1\n"
    units: list[str] = []

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        text = (cwd / "pkg/mod.py").read_text(encoding="utf-8")
        if text != original:
            units.append("transform")
            (cwd / "support.txt").write_text("transform scribbled\n", encoding="utf-8")
        else:
            units.append("clean")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=repo.path,
        project_root=repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert units == ["clean", "clean", "transform"], "baseline, control, transform"
    assert verdict.claims[0].status is Outcome.PASS
    r3 = verdict.claims[1]
    assert (r3.status, r3.reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.DIRTY_TREE,
    )
    assert r3.canary is None
    assert verdict.judgment is None
    assert git_module.dirty_paths(repo.path) == ()
    assert (repo.path / "support.txt").read_text(
        encoding="utf-8"
    ) == "committed support\n"


# --- 5. the direct R0-only path's own identity and terminal ------------------


@pytest.mark.parametrize(
    ("relative_project", "expected_prefix"),
    [(".", PurePosixPath(".")), ("apps/p", PurePosixPath("apps/p"))],
    ids=["repo-root-project", "nested-project"],
)
def test_the_direct_r0_plan_carries_its_exact_repo_relative_prefix(
    git_repo: GitRepo,
    monkeypatch: pytest.MonkeyPatch,
    relative_project: str,
    expected_prefix: PurePosixPath,
):
    """A-193: ``project_prefix`` is ``None`` ONLY where repository identity
    genuinely does not exist yet (:mod:`assay.cli`'s pre-adapter refusal).
    Direct ``run_lane`` already knows both facts by the time it executes, so
    its own plan must state them -- ``None`` there would be exactly the
    shadowing default AGENTS.md names, a literal standing in for a value with
    an authoritative source, and the handoff says so in as many words
    ("Direct ``run_lane`` already has repo/project identity and must pass its
    exact prefix").

    ``CommandPlan`` is not a verdict field, so the observable is the frozen
    seam itself: ``execute_command`` must "resolve once (passing that prefix),
    then call ``execute_plan``". The spy WRAPS the real function, so the run it
    observes is the real one -- both namespaces the map allows are covered
    (``.`` at the repository top, ``apps/p`` one directory down)."""
    repo = git_repo
    repo.write("apps/p/marker.txt", "nested project\n")
    head_rev = repo.commit_all("add a nested project")
    project_root = repo.path if relative_project == "." else repo.path / relative_project

    real_execute_plan = runner.execute_plan
    observed: list[tuple[PurePosixPath | None, Path]] = []

    def spy(plan, *, cwd, timeout, **kwargs):
        observed.append((plan.project_prefix, Path(cwd)))
        return real_execute_plan(plan, cwd=cwd, timeout=timeout, **kwargs)

    monkeypatch.setattr(runner, "execute_plan", spy)

    verdict = runner.run_lane(
        make_lane(argv=("/bin/sh", "-c", "exit 0")),
        commit=head_rev,
        repo=repo.path,
        project_root=project_root,
        adapter=None,
        assay_version="0.1.0",
        clock=_clock,
    )

    assert verdict.outcome is Outcome.PASS
    assert observed == [(expected_prefix, project_root)]


def test_the_direct_r0_pre_run_dirty_refusal_also_carries_its_prefix(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """The refusal half of the rule above: ``run_lane``'s own direct
    dirty-tree refusal happens AFTER repository identity is known, so the plan
    it records states it too -- ``refuse_lane``'s ``project_prefix`` default
    stays ``None`` for :mod:`assay.cli`'s genuinely pre-repository refusal,
    which is an honest absence rather than an invented ``.``."""
    repo = git_repo
    repo.write("untracked.txt", "dirt\n")

    real_resolve = runner.resolve_command_plan
    observed: list[PurePosixPath | None] = []

    def spy(lane, **kwargs):
        plan = real_resolve(lane, **kwargs)
        observed.append(plan.project_prefix)
        return plan

    monkeypatch.setattr(runner, "resolve_command_plan", spy)

    def process(argv, *, env, cwd, timeout):
        raise AssertionError("a dirty tree must be refused before the command")

    verdict = runner.run_lane(
        make_lane(argv=("check",)),
        commit=repo.head(),
        repo=repo.path,
        project_root=repo.path,
        adapter=None,
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
    )

    assert (verdict.claims[0].status, verdict.claims[0].reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.DIRTY_TREE,
    )
    assert observed == [PurePosixPath(".")]


def test_a_project_root_outside_its_own_repository_is_refused(
    git_repo: GitRepo, tmp_path: Path
):
    """The containment half of the same conversion (the higher-rigor state
    machine's own step 1.2, "validate containment/project prefix"). A project
    root that is not under its repository top has no repo-relative identity at
    all, so there is nothing honest to put in the plan -- refused with a typed
    ``ERROR``/``BAD_LANE_CONFIG`` rather than an invented ``.`` or a bare
    ``ValueError`` from ``Path.relative_to`` deep inside the conversion."""
    outside = tmp_path / "not-in-the-repo"
    outside.mkdir()

    with pytest.raises(AssayError) as caught:
        runner.run_lane(
            make_lane(argv=("check",)),
            commit=git_repo.head(),
            repo=git_repo.path,
            project_root=outside,
            adapter=None,
            assay_version="0.1.0",
            process_runner=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("nothing may run")
            ),
            clock=_clock,
        )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


@pytest.mark.parametrize(
    "budget", [True, 0.0, -1.0, float("inf"), float("nan"), "5s"]
)
def test_lane_deadline_refuses_a_budget_that_is_not_positive_and_finite(budget):
    """The skeleton's own ``LaneDeadline.start`` guards, which nothing
    exercised: a boolean, a non-number, a non-finite or a non-positive budget
    is a programmer error, not a lane that silently gets an infinite one."""
    with pytest.raises(ValueError, match="budget_seconds"):
        runner.LaneDeadline.start(budget_seconds=budget, monotonic=lambda: 0.0)


@pytest.mark.parametrize("reading", [True, float("inf"), float("nan"), "0"])
def test_lane_deadline_refuses_a_monotonic_source_that_returns_nonsense(reading):
    """Its sibling guard, one field over: the injected clock is sampled ONCE
    at start, and a reading that cannot be added to a budget must be refused
    there rather than producing a deadline that never expires."""
    with pytest.raises(ValueError, match="monotonic clock"):
        runner.LaneDeadline.start(budget_seconds=5.0, monotonic=lambda: reading)


def test_a_canary_control_that_dirties_its_snapshot_is_a_payload_free_r3(
    git_repo: GitRepo,
):
    """The control half of the transform case above -- the earlier of the two
    canary units, so this additionally proves the transform half is never
    started once the control's own snapshot stopped naming its commit."""
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/mod.py", "def f():\n    return 1\n")
    repo.write("support.txt", "committed support\n")
    head_rev = repo.commit_all("seed a canary target")
    judge = make_r3_judge(
        language="python",
        source_root_paths=(repo.path / "pkg",),
        canary=CanaryConfig(mechanism="import-break", target="pkg/mod.py"),
    )
    lane = make_lane(rigor=("R0", "R3"), judge=judge, argv=("check",))
    units: list[str] = []

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        units.append("unit")
        if len(units) == 2:  # the CONTROL half, after the lane's own baseline
            (cwd / "support.txt").write_text("control scribbled\n", encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=repo.path,
        project_root=repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert len(units) == 2, "the transform half must never be materialised"
    assert verdict.claims[0].status is Outcome.PASS
    r3 = verdict.claims[1]
    assert (r3.status, r3.reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.DIRTY_TREE,
    )
    assert r3.canary is None
    assert verdict.judgment is None
    assert git_module.dirty_paths(repo.path) == ()


def test_the_direct_r0_path_still_detects_a_command_that_moves_head(
    git_repo: GitRepo,
):
    """P20/A-178's direct-path ``HEAD_CHANGED`` terminal lost its only oracle
    in P23: the test that used to reach it declares ``R0,R1`` and now runs
    the committed-snapshot path instead, where the SNAPSHOT's own check
    fires. The live-tree branch this one exercises is the registered
    self-hosting gate's own code path, so it needs an R0-only witness of its
    own.

    A command that COMMITS leaves a perfectly clean tree, so a dirt-only
    check passes while the repository the verdict names has moved."""
    repo = git_repo
    repo.write("pkg/mod.py", "def f():\n    return 1\n")
    recorded_head = repo.commit_all("seed a project")
    command = (
        "printf 'def f():\\n    return 2\\n' > pkg/mod.py && "
        "git add -A && "
        "git -c user.name=cmd -c user.email=cmd@example.invalid "
        "commit -q -m 'the command committed'"
    )
    lane = make_lane(argv=("/bin/sh", "-c", command), env_passthrough=("PATH",))

    verdict = runner.run_lane(
        lane,
        commit=recorded_head,
        repo=repo.path,
        project_root=repo.path,
        adapter=None,
        assay_version="0.1.0",
        clock=_clock,
    )

    assert repo.head() != recorded_head, "the attack must really move HEAD"
    assert git_module.dirty_paths(repo.path) == (), "and must leave a clean tree"
    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.HEAD_CHANGED
    assert [claim.rigor for claim in verdict.claims] == ["R0"]
    assert json.loads(verdict.to_json())["commit"] == recorded_head


def test_a_mutant_that_commits_inside_its_snapshot_is_head_changed(
    git_repo: GitRepo,
):
    """A-195's other half, and the one a dirt-only check cannot see: a
    command that COMMITS leaves its snapshot perfectly clean while the tree
    the mutant was judged against is no longer the one it was named for.

    The terminal table's "unit commits" row makes this the payload-free
    ``NO_MEASUREMENT``/``HEAD_CHANGED`` pair -- distinct from ``DIRTY_TREE``,
    and still never folded into ``crashed``. The consumer's own repository is
    untouched throughout: the commit happened inside a disposable snapshot."""
    original = "def f(x):\n    return x > 0\n"
    base_rev, head_rev = _seed_python_project(git_repo, head_source=original)
    before_head = git_repo.head()
    units: list[str] = []

    # Change a tracked file and COMMIT it, so the snapshot ends perfectly
    # clean while its HEAD no longer names the commit it was built from --
    # the exact shape a dirt-only post-run check cannot see.
    commit_cmd = (
        "printf 'the mutant rewrote this\\n' > support.txt && "
        "git add -A && "
        "git -c user.name=cmd -c user.email=cmd@example.invalid "
        "commit -q -m 'the mutant committed'"
    )

    def process(argv, *, env, cwd, timeout):
        cwd = Path(cwd)
        text = (cwd / "pkg/mod.py").read_text(encoding="utf-8")
        if text != original:
            units.append("mutant")
            # a real `git commit`, inside the mutant's own snapshot only
            subprocess.run(
                ["/bin/sh", "-c", commit_cmd],
                cwd=cwd,
                env={"PATH": os.environ["PATH"]},
                capture_output=True,
                check=True,
            )
        else:
            units.append("baseline")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    verdict = runner.run_lane(
        _lane_with_scribbling_mutant(git_repo, base_rev, original),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process,
        clock=_clock,
        monotonic=CountingMonotonic(),
    )

    assert units == ["baseline", "mutant"]
    assert verdict.claims[0].status is Outcome.PASS
    r2 = verdict.claims[1]
    assert (r2.status, r2.reason_code) == (
        Outcome.NO_MEASUREMENT,
        ReasonCode.HEAD_CHANGED,
    )
    assert r2.mutation is None
    assert git_repo.head() == before_head, "the consumer's HEAD never moves"
    assert git_module.dirty_paths(git_repo.path) == ()
