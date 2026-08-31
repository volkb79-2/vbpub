"""B046 — R2 by evidence INGESTION, end to end through the real runner,
against the REAL committed StrykerJS artifact.

The primary fixture here is a genuine
``mutation-testing-report-schema`` document produced by a real StrykerJS
10.0.0 run over ``tests/fixtures/coverage/probe-js`` (recipe and pinned
versions in ``tests/fixtures/mutation/PROVENANCE.md``) — not a heredoc, not a
hand-built dict (A-334). Wave A's own lesson, one tier up: a real CLI run
through a test double still does not prove a claim the double never
exercised, and B049 was found precisely because a real tool was finally run.

The lane's command does not run Stryker — ``tester-unified`` has no Node
(DESIGN-GUIDE §10) — but it does write the real artifact, at the declared
path, from inside the snapshot, through the same reservation any real
producer would. The one substitution is ``projectRoot``: the committed report
names the absolute directory the original run happened in, and a report is
only valid for the directory THIS run's command executed in, so the command
rewrites that one field to its own ``$PWD``. That substitution is itself
under test — ``test_a_report_from_another_project_root_is_refused`` removes it
and asserts the refusal.

The lane declares ``cwd = "app"`` throughout, which is not incidental: an
ingested report's ``projectRoot`` is the directory the TOOL ran in and its
``files`` keys are relative to that, so the B043/B046 coupling is exercised by
every test here rather than by a special case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import GitRepo, make_lane, make_r2_judge

from assay import runner
from assay.adapters.javascript import JavaScriptAdapter
from assay.config import JudgeConfig, MutationConfig
from assay.errors import Outcome, ReasonCode

REAL_REPORT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "mutation"
    / "mutation-report-json.probe-js-stryker.json"
)

#: The lane's command rewrites this placeholder to its own ``$PWD``. `sed` is
#: used rather than an unquoted heredoc on purpose: the report embeds the full
#: TypeScript SOURCE of every measured file, and a shell would expand a `$` in
#: a template literal inside it.
PLACEHOLDER = "__ASSAY_TEST_PROJECT_ROOT__"

ARTIFACT = "app/reports/mutation.json"


def _report_document() -> dict:
    return json.loads(REAL_REPORT.read_text(encoding="utf-8"))


def _stage_report(tmp_path: Path, document: dict, *, name: str = "report.json") -> Path:
    """Write *document* OUTSIDE the repository, with the placeholder in place
    of ``projectRoot``, ready for the lane's command to copy in."""
    staged = tmp_path / name
    staged.write_text(json.dumps(document), encoding="utf-8")
    return staged


def _seed_repo(git_repo: GitRepo, document: dict) -> None:
    """A repo whose SECOND commit adds every file the report measured, with
    the exact text the report itself carries.

    An empty first commit means every line of every measured file is an ADDED
    line, so all of the report's mutants fall inside a ``changed_lines``
    scope. That is a real diff, not a bypass: it is the shape of a branch that
    introduces a new module, and it makes the scope intersection observable
    rather than incidental.
    """
    git_repo.write(".gitignore", "app/reports\n")
    git_repo.commit_all("gitignore the mutation report")
    for key, record in document["files"].items():
        git_repo.write(f"app/{key}", record["source"])
    git_repo.commit_all("add the measured sources")


def _lane(
    *,
    git_repo: GitRepo,
    staged: Path,
    mode: str = "changed_lines",
    targets=None,
    base: str,
):
    judge = JudgeConfig(
        language="javascript",
        source_roots=("app/src",),
        source_root_paths=((git_repo.path / "app" / "src").resolve(),),
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=MutationConfig(
            format="mutation-report-json",
            artifact=ARTIFACT,
            fail_under=100.0,
        ),
        canary=None,
        base=None if mode == "whole_target" else base,
        mode=mode,
        targets=targets,
    )
    return make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        cwd="app",
        env={"STAGED_REPORT": str(staged), "PLACEHOLDER": PLACEHOLDER},
        argv=(
            "/bin/sh",
            "-c",
            'mkdir -p reports && sed "s|$PLACEHOLDER|$PWD|" "$STAGED_REPORT" '
            "> reports/mutation.json",
        ),
    )


def _run(git_repo: GitRepo, lane):
    return runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=JavaScriptAdapter(),
        assay_version="0.1.0",
    )


@pytest.fixture
def ingested(git_repo: GitRepo, tmp_path: Path):
    """The happy path, materialised once: the real report, a real repo, a real
    run. Returns ``(verdict, document)``."""
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    staged = _stage_report(tmp_path, document)
    _seed_repo(git_repo, document)
    base = git_repo.git("rev-parse", "HEAD~1").strip()
    verdict = _run(git_repo, _lane(git_repo=git_repo, staged=staged, base=base))
    return verdict, document


# --------------------------------------------------------------------------
# The happy path, over the real artifact
# --------------------------------------------------------------------------


def test_the_real_stryker_report_becomes_a_judged_r2_claim(ingested):
    """21 Killed, 19 Survived, 69 NoCoverage — the exact counts in the
    committed artifact, arrived at by assay's own scope computation rather
    than by reading the tool's score."""
    verdict, _document = ingested
    r2 = next(claim for claim in verdict.claims if claim.rigor == "R2")
    assert r2.mutation is not None
    assert len(r2.mutation.killed) == 21
    # NoCoverage maps to `survived` -- a mutant no test exercised is not
    # killed -- so the bucket is 19 + 69.
    assert len(r2.mutation.survived) == 88
    assert r2.mutation.crashed == ()
    assert r2.mutation.budget_exceeded == ()
    assert r2.mutation.equivalent == ()
    assert r2.mutation.total == 109
    assert r2.mutation.candidate_count == 109
    assert r2.status is Outcome.FAIL
    assert r2.reason_code is ReasonCode.MUTANTS_SURVIVED


def test_the_judgment_records_the_ingested_producer_and_its_tool(ingested):
    verdict, _document = ingested
    assert verdict.judgment is not None
    r2 = verdict.judgment.r2
    assert r2 is not None
    assert r2.producer == "ingested"
    assert r2.producer_tool is not None
    # Copied from the report's own `framework`, never from `helpers[]`
    # (A-230a/A-361): assay did not invoke Stryker.
    assert r2.producer_tool.name == "StrykerJS"
    assert r2.producer_tool.version == "10.0.0"
    assert r2.producer_tool.report_schema_version == "1.0"
    # A-230a: `helpers[]` records tools assay ITSELF invoked and resolved on
    # PATH. Assay did not invoke Stryker -- the lane's own argv did -- so the
    # producer must not appear there, and putting it there would launder a
    # DECLARED fact into a verified one.
    assert not any(
        helper.name == "StrykerJS" for helper in (verdict.helpers or ())
    )


def test_assays_own_policy_fields_are_absent_not_backfilled(ingested):
    """A-360: assay chose no operators, no concurrency and no ceiling for a
    run it did not orchestrate, so those fields are ABSENT rather than filled
    from the report."""
    verdict, _document = ingested
    r2 = verdict.judgment.r2
    assert r2.jobs is None
    assert r2.max_mutants is None
    assert r2.operators is None
    assert r2.equivalence_artifact is None
    assert r2.kill_attribution == "unattributed"
    wire = verdict.to_dict()["judgment"]["r2"]
    for absent in ("jobs", "max_mutants", "operators", "equivalence_artifact"):
        assert absent not in wire


def test_the_observed_operators_are_on_the_wire_one_per_mutant(ingested):
    """The operators are not lost by being absent from the policy: they are
    recorded per mutant, namespaced, verbatim from the tool."""
    verdict, _document = ingested
    r2 = next(claim for claim in verdict.claims if claim.rigor == "R2")
    operators = {
        outcome.operator
        for bucket in ("killed", "survived")
        for outcome in getattr(r2.mutation, bucket)
    }
    assert operators == {
        "stryker:ArithmeticOperator",
        "stryker:ArrayDeclaration",
        "stryker:BlockStatement",
        "stryker:BooleanLiteral",
        "stryker:ConditionalExpression",
        "stryker:EqualityOperator",
        "stryker:LogicalOperator",
        "stryker:ObjectLiteral",
        "stryker:StringLiteral",
    }


def test_no_coverage_mutants_are_listed_by_position_not_buried_in_a_count(
    ingested,
):
    """69 ``NoCoverage`` mutants, but 31 distinct untested POSITIONS: ten of
    them share ``src/format.ts`` line 34 alone. The field lists PLACES, not
    mutants — repeating a line once per mutant would turn a list of places
    into a disguised count, and the model refuses the duplicate outright."""
    verdict, document = ingested
    expected = {
        (f"app/{key}", mutant["location"]["start"]["line"])
        for key, record in document["files"].items()
        for mutant in record["mutants"]
        if mutant["status"] == "NoCoverage"
    }
    r2 = verdict.judgment.r2
    assert {(p.path, p.lineno) for p in r2.survived_uncovered} == expected
    assert len(r2.survived_uncovered) == 31
    # Every one of them is a position the `survived` bucket really records --
    # the same relation `verify.py` re-derives at the raw layer.
    survived = {
        (outcome.path, outcome.lineno)
        for outcome in next(
            claim for claim in verdict.claims if claim.rigor == "R2"
        ).mutation.survived
    }
    for position in r2.survived_uncovered:
        assert (position.path, position.lineno) in survived
    assert list(r2.survived_uncovered) == sorted(
        r2.survived_uncovered, key=lambda p: p.sort_key
    )


def test_wire_paths_are_repo_relative_and_carry_the_lane_cwd(ingested):
    """The report's keys are ``src/format.ts``, relative to the directory the
    tool ran in. The wire path is repo-top-relative — the same spelling a
    natively generated mutant carries — so it is ``app/src/format.ts``. This
    is the B043 coupling: without the declared ``cwd`` the prefix could not be
    known."""
    verdict, _document = ingested
    r2 = next(claim for claim in verdict.claims if claim.rigor == "R2")
    paths = {outcome.path for outcome in r2.mutation.survived}
    assert all(path.startswith("app/src/") for path in paths), paths
    assert "app/src/format.ts" in paths


def test_the_verdict_verifies_against_the_shipped_schema_and_the_raw_layer(
    ingested,
):
    """The whole point of the tier vocabulary: an INGESTED verdict is a
    first-class document, not a special case that only assay can read."""
    from assay.verify import verify_document

    verdict, _document = ingested
    failures = verify_document(verdict.to_dict())
    assert failures == [], failures


def test_lines_without_candidates_records_where_the_tool_declined(ingested):
    verdict, _document = ingested
    r2 = verdict.judgment.r2
    assert r2.lines_without_candidates
    mutated = {
        (outcome.path, outcome.lineno)
        for bucket in ("killed", "survived")
        for outcome in getattr(
            next(claim for claim in verdict.claims if claim.rigor == "R2").mutation,
            bucket,
        )
    }
    for position in r2.lines_without_candidates:
        assert (position.path, position.lineno) not in mutated


# --------------------------------------------------------------------------
# whole_target scope
# --------------------------------------------------------------------------


def test_whole_target_scope_counts_only_declared_target_files(
    git_repo: GitRepo, tmp_path: Path
):
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    staged = _stage_report(tmp_path, document)
    _seed_repo(git_repo, document)

    verdict = _run(
        git_repo,
        _lane(
            git_repo=git_repo,
            staged=staged,
            mode="whole_target",
            targets=("app/src/orphan.ts",),
            base="unused",
        ),
    )

    r2 = next(claim for claim in verdict.claims if claim.rigor == "R2")
    assert r2.mutation is not None
    paths = {
        outcome.path
        for bucket in ("killed", "survived")
        for outcome in getattr(r2.mutation, bucket)
    }
    assert paths == {"app/src/orphan.ts"}
    # `src/orphan.ts` carries exactly two mutants in the committed report.
    assert r2.mutation.total == 2


# --------------------------------------------------------------------------
# The refusals — each its own named terminal
# --------------------------------------------------------------------------


def _refused(git_repo: GitRepo, tmp_path: Path, document: dict):
    staged = _stage_report(tmp_path, document)
    _seed_repo(git_repo, _report_document())
    base = git_repo.git("rev-parse", "HEAD~1").strip()
    verdict = _run(git_repo, _lane(git_repo=git_repo, staged=staged, base=base))
    return next(claim for claim in verdict.claims if claim.rigor == "R2")


def test_a_report_from_another_project_root_is_refused(
    git_repo: GitRepo, tmp_path: Path
):
    """B046 non-repudiation (iii). The committed artifact's own absolute
    ``projectRoot`` is left in place — so this is literally the real report,
    describing the directory it was really produced in, presented to a lane
    that ran somewhere else."""
    document = _report_document()  # projectRoot NOT replaced
    claim = _refused(git_repo, tmp_path, document)
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert claim.mutation is None


def test_a_pending_mutant_anywhere_refuses_the_whole_report(
    git_repo: GitRepo, tmp_path: Path
):
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document["files"]["src/orphan.ts"]["mutants"][0]["status"] = "Pending"
    claim = _refused(git_repo, tmp_path, document)
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_unknown_status_is_refused_rather_than_dropped(
    git_repo: GitRepo, tmp_path: Path
):
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document["files"]["src/orphan.ts"]["mutants"][0]["status"] = "Vaporised"
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_in_scope_ignored_mutant_is_refused(git_repo: GitRepo, tmp_path: Path):
    """A-377: the v9 wire has no field that can state "the tool was told to
    skip this". Dropping it would let a gate be made green from inside the
    mutation tool's own config; folding it into ``discarded`` would report a
    suppressed mutant as one that failed to COMPILE."""
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document["files"]["src/orphan.ts"]["mutants"][0]["status"] = "Ignored"
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_unknown_schema_major_is_refused(git_repo: GitRepo, tmp_path: Path):
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document["schemaVersion"] = "99.0"
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_file_key_outside_the_declared_source_roots_is_refused(
    git_repo: GitRepo, tmp_path: Path
):
    """"An artifact from elsewhere" — refused, never quietly skipped. Skipping
    would let a report about a DIFFERENT project be judged as this one and
    score zero mutants, which is a PASS-shaped answer to a question nobody
    asked."""
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document["files"]["elsewhere/thing.ts"] = {
        "language": "typescript",
        "source": "export const x = 1\n",
        "mutants": [],
    }
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_mutant_with_no_replacement_is_refused(git_repo: GitRepo, tmp_path: Path):
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document["files"]["src/orphan.ts"]["mutants"][0].pop("replacement")
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_report_with_no_framework_is_refused(git_repo: GitRepo, tmp_path: Path):
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    document.pop("framework")
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_an_absent_project_root_is_refused(git_repo: GitRepo, tmp_path: Path):
    """Assay's OWN added requirement (A-375): the upstream schema makes
    ``projectRoot`` optional, and only ``schemaVersion``/``thresholds``/
    ``files`` required."""
    document = _report_document()
    document.pop("projectRoot")
    claim = _refused(git_repo, tmp_path, document)
    assert claim.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_command_that_writes_no_report_is_no_measurement_not_a_pass(
    git_repo: GitRepo, tmp_path: Path
):
    """The silent green this whole project exists to remove: an ingested R2
    lane whose evidence never appeared must not read as PASS."""
    document = _report_document()
    document["projectRoot"] = PLACEHOLDER
    staged = _stage_report(tmp_path, document)
    _seed_repo(git_repo, document)
    base = git_repo.git("rev-parse", "HEAD~1").strip()
    lane = _lane(git_repo=git_repo, staged=staged, base=base)
    silent = make_lane(
        rigor=("R0", "R2"),
        judge=lane.judge,
        cwd="app",
        argv=("/bin/sh", "-c", "exit 0"),
    )

    verdict = _run(git_repo, silent)

    claim = next(c for c in verdict.claims if c.rigor == "R2")
    assert claim.status is Outcome.NO_MEASUREMENT
    assert claim.reason_code is ReasonCode.EMPTY_COVERAGE
    assert verdict.outcome is not Outcome.PASS
