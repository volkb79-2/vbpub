"""B047 item 5: ``helpers[]`` gets its first PRODUCER, and the correspondence
rule gets exactly one definition.

Until this wave ``Verdict.helpers`` was validated and never populated -- P33
shipped no helper-invoking adapter, so its own docstring says "P33 only
VALIDATES this array". A-217's Go statement-position oracle is the first real
one, and A-395 rules that the end-to-end proof must be a REAL toolchain
(``tests/qualification/test_go_r1_real.py``, opt-in, no Go in either gate
image). This module is the half that does NOT need a toolchain: the envelope
wiring, its two refusals, and the one rule both the producer and the schema
read.

**What is deliberately NOT weakened here.** ``test_cli_run.py``'s
``"helpers" not in document`` stays exactly as it is (A-395): it is true for
every adapter that invokes no helper, and it is the control that makes the
positive assertion mean anything. The pair below -- omission for a lane with
no helper, presence for one with -- is the same pair at the
``assemble_verdict`` seam.

**A-407's regression test lives at the bottom of this module**, driven
through the real :func:`assay.runner.run_lane` rather than through
``assemble_verdict``: the defect it pins is that a helper recorded before a
judge refusal outlived the claim, so the wiring guard fired and the consumer
was told about ``helpers[]`` instead of about the artifact -- with no verdict
document written at all. It needs no toolchain (a synthetic
``requires_statement_attribution`` adapter over a ``go-cover``-shaped
profile), so the REGISTERED gate exercises it; the same two shapes are
proven through the shipped zipapp against real Go in
``tests/qualification/test_go_r1_real.py``.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, fixed_clock, make_lane, make_r1_judge

from assay import runner
from assay.adapters.base import HelperInvocation, StatementBlockReport
from assay.errors import AssayError, Outcome, ReasonCode
from assay.output import reserve_verdict_output
from assay.statement_attribution import StatementBlock
from assay.verdict import (
    HELPER_ROLES,
    Claim,
    Coverage,
    Helper,
    Judgment,
    JudgmentR1,
    JudgmentResolved,
    Mutation,
    supported_helper_roles,
)


def _r0_pass_result(tmp_path: Path):
    lane = make_lane(
        name="package", rigor=("R0", "R1"), argv=("/bin/sh", "-c", "exit 0"), env={}
    )
    clock = fixed_clock(
        datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 12, 0, 1, tzinfo=timezone.utc),
    )
    return lane, runner.execute_command(lane, cwd=tmp_path, clock=clock)


def _r1_pass_claim() -> Claim:
    coverage = Coverage(
        covered=1, executable=1, pct=100.0, considered=1,
        exclusion_capability="reported",
        missing_lines={}, files_missing_coverage=(),
    )
    return Claim(
        rigor="R1", source="computed", status=Outcome.PASS,
        verified_by_assay=True, coverage=coverage,
    )


def _r1_error_claim() -> Claim:
    """The payload-free shape a voided R1 leaves behind."""
    return Claim(
        rigor="R1", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.GIT_FAILED,
    )


def _r1_judgment() -> Judgment:
    return Judgment(
        resolved=JudgmentResolved(
            language="go", source_roots=("internal",), base="a" * 40,
        ),
        r1=JudgmentR1(
            coverage_format="go-cover",
            coverage_artifact=".assay/cover.out",
            fail_under=100.0,
            allow_excluded=False,
        ),
    )


def _go_helper() -> Helper:
    return Helper(
        role="statement-positions",
        tool="go",
        resolved_path="/usr/local/go/bin/go",
        identity="go version go1.25.14",
    )


# --- the single correspondence rule ------------------------------------------


def test_supported_helper_roles_names_only_what_a_claim_set_can_support():
    """The rule itself. ``executable-code`` takes EITHER payload, which is
    the asymmetry a second copy of this table would most easily get wrong."""
    assert supported_helper_roles(()) == frozenset()
    assert supported_helper_roles((_r1_error_claim(),)) == frozenset()
    assert supported_helper_roles((_r1_pass_claim(),)) == frozenset(
        {"statement-positions", "executable-code"}
    )


def test_every_declared_helper_role_is_covered_by_the_rule():
    """Vacuity guard with a real job: :data:`HELPER_ROLES` is documented as
    extensible ("a fourth role is an additive enum change"), and a role added
    there but not to the rule would silently become unsupportable by every
    claim set -- a producer's entry refused with no matching requirement to
    read. This fails the day that happens instead of on the next real run."""
    r2_judged = Claim(
        rigor="R2", source="computed", status=Outcome.PASS,
        verified_by_assay=True, mutation=Mutation(candidate_count=0, total=0),
    )
    reachable = supported_helper_roles((_r1_pass_claim(), r2_judged))

    assert reachable, "no role is reachable at all, so nothing below is tested"
    assert reachable == set(HELPER_ROLES), (
        "a role exists that no claim set can support, so a producer recording "
        f"it would be refused with no requirement to satisfy: "
        f"{sorted(set(HELPER_ROLES) - reachable)}"
    )


# --- the producer seam --------------------------------------------------------


def test_the_runner_supplies_the_ROLE_and_the_adapter_never_does():
    """A-397 (c) split ``HelperInvocation`` (what ran) from
    :class:`~assay.verdict.Helper` (the wire shape) so the adapter layer never
    imports :mod:`assay.verdict`. The consequence worth pinning is that the
    ROLE is a fact of the call site: ``statement_blocks`` is the only method
    that returns one of these, so its invocations are
    ``statement-positions`` by construction. An adapter that could name its
    own role could claim ``mutation-sites`` from a coverage hook, and the
    schema would then demand an R2 payload for work that produced an R1 one."""
    sink: list[Helper] = []
    record = runner._record_statement_position_helper(sink)

    record(
        HelperInvocation(
            tool="go",
            resolved_path="/usr/local/go/bin/go",
            identity="go version go1.25.14",
        )
    )

    assert [helper.role for helper in sink] == ["statement-positions"]
    assert sink[0].tool == "go"
    assert sink[0].resolved_path == "/usr/local/go/bin/go"
    assert sink[0].identity == "go version go1.25.14"
    # `HelperInvocation` carries no role at all -- the assertion above would
    # be satisfiable by copying one if it did.
    assert not hasattr(
        HelperInvocation(tool="go", resolved_path="/x", identity="y"), "role"
    )


# --- assemble_verdict ---------------------------------------------------------


def test_a_lane_that_invoked_no_helper_OMITS_the_field(tmp_path: Path):
    """A-230a, and the control for the test below. Every Python, SQL and
    JavaScript lane in this build lands here."""
    lane, result = _r0_pass_result(tmp_path)

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="1" * 40,
        result=result,
        claims=(runner.build_r0_claim(result), _r1_pass_claim()),
        assay_version="0.1.0",
        judgment=_r1_judgment(),
    )

    assert verdict.helpers is None
    assert "helpers" not in verdict.to_dict()


def test_a_helper_alongside_its_own_judged_claim_reaches_the_document(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="2" * 40,
        result=result,
        claims=(runner.build_r0_claim(result), _r1_pass_claim()),
        assay_version="0.1.0",
        judgment=_r1_judgment(),
        helpers=(_go_helper(),),
    )

    assert verdict.helpers == (_go_helper(),)
    document = verdict.to_dict()
    assert document["helpers"] == [
        {
            "role": "statement-positions",
            "tool": "go",
            "resolved_path": "/usr/local/go/bin/go",
            "identity": "go version go1.25.14",
        }
    ]


def test_a_helper_with_no_correspondingly_judged_claim_is_refused_LOUDLY(
    tmp_path: Path,
):
    """The guard's whole point is that it is an :class:`AssayError` and not
    the bare ``ValueError`` :meth:`Verdict._check_helpers` would raise, which
    no caller catches. A silent FILTER here was the rejected alternative: it
    would also swallow a genuine wiring defect, and the one legitimate way to
    end up holding a helper whose claim was voided drops the entry at the
    site that voided it (see the next test)."""
    lane, result = _r0_pass_result(tmp_path)

    with pytest.raises(AssayError, match="describes work that judged nothing") as caught:
        runner.assemble_verdict(
            lane=lane,
            commit="3" * 40,
            result=result,
            claims=(runner.build_r0_claim(result), _r1_error_claim()),
            assay_version="0.1.0",
            helpers=(_go_helper(),),
        )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "statement-positions" in str(caught.value)


# --- the one legitimate way a helper outlives its claim -----------------------


def test_voiding_r1_after_a_cleanup_failure_voids_its_helper_with_it(
    tmp_path: Path,
):
    """A-193/A-194's "outer scratch cleanup alone fails" path. The oracle
    really ran, and then the claim it produced was replaced by a payload-free
    ``ERROR``/``GIT_FAILED``. Keeping the entry would build a verdict the
    schema refuses; dropping it is done where the payload is taken away,
    using the same :func:`supported_helper_roles` the schema validates with."""
    lane, result = _r0_pass_result(tmp_path)
    outcome = runner._PreparedOutcome(
        result=result,
        claims=(runner.build_r0_claim(result), _r1_pass_claim()),
        judgment=_r1_judgment(),
        ended="2026-09-01T12:00:02+00:00",
        helpers=(_go_helper(),),
    )

    voided = runner._replace_highest_higher_rigor_claim_with_git_failed(lane, outcome)

    assert voided.helpers == ()
    r1 = next(claim for claim in voided.claims if claim.rigor == "R1")
    assert r1.status is Outcome.ERROR and r1.reason_code is ReasonCode.GIT_FAILED
    # And the verdict it produces is constructible, which is the outcome that
    # actually matters: this is the state that would otherwise raise.
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="4" * 40,
        result=voided.result,
        claims=voided.claims,
        assay_version="0.1.0",
        judgment=voided.judgment,
        helpers=voided.helpers,
    )
    assert verdict.helpers is None
    assert "helpers" not in verdict.to_dict()


# --- A-407: the OTHER way a helper outlives its claim -------------------------

#: One `go-cover`-shaped block over lines 3-7. The oracle below reports the
#: same block ending on line 9 instead, which is `attribute_statements`'
#: "the profile and the source are not the same revision" case (A-391) --
#: the shape a STALE profile really has.
_PROFILE_TEXT = "mode: count\npkg/mod.zzz:3.22,7.2 2 1\n"


@dataclass(frozen=True, kw_only=True)
class _StaleOracleAdapter:
    """A ``requires_statement_attribution`` adapter whose oracle answers
    honestly about a source the profile does not match.

    Synthetic and deliberately not Go: the defect under test is in
    :mod:`assay.runner`'s claim assembly, not in any language. What matters
    is only the ORDER -- ``statement_blocks`` returns (so the helper is
    recorded) and the join then refuses -- which is every judge refusal that
    happens downstream of the oracle.
    """

    name: str = "zzz"
    source_globs: tuple[str, ...] = ("*.zzz",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    requires_statement_attribution: bool = True
    external_tools: tuple[str, ...] = ()

    def for_project(self, *, repo_top: Path, project_root: Path) -> "_StaleOracleAdapter":
        return self

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        return key

    def statement_spans(self, text: str):
        return None

    def statement_blocks(self, repo_top, rel_paths, *, remaining=None):
        return StatementBlockReport(
            blocks_by_path={
                path: (
                    StatementBlock(
                        start_line=3,
                        start_col=22,
                        end_line=9,
                        end_col=2,
                        num_stmts=2,
                        stmt_lines=(4, 6),
                    ),
                )
                for path in rel_paths
            },
            helper=HelperInvocation(
                tool="blocky-oracle",
                resolved_path="/usr/bin/blocky",
                identity="blocky version 1.2.3",
            ),
        )


def _stale_profile_lane(git_repo: GitRepo) -> tuple[object, str]:
    """A real two-commit repository whose lane command really writes the
    profile above, and the head commit to judge it at."""
    git_repo.write(".gitignore", "cov.out\n")
    git_repo.write("pkg/mod.zzz", "".join(f"line {n}\n" for n in range(1, 11)))
    base_rev = git_repo.commit_all("seed the source the oracle reads")
    git_repo.write(
        "pkg/mod.zzz", "".join(f"line {n}\n" for n in range(1, 11)) + "line 11\n"
    )
    head_rev = git_repo.commit_all("change it, so R1 has something to judge")
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(
            source_root_paths=(git_repo.path / "pkg",),
            coverage_format="go-cover",
            coverage_artifact="cov.out",
            base=base_rev,
        ),
        argv=("/bin/sh", "-c", f"cat > cov.out <<'EOF'\n{_PROFILE_TEXT}EOF"),
    )
    return lane, head_rev


def test_a_judge_that_refuses_after_the_oracle_ran_reports_ITS_reason_not_the_wiring(
    git_repo: GitRepo, tmp_path: Path
):
    """**BLOCKER R2-1 / A-407.** The helper is recorded the instant
    ``statement_blocks`` returns (``_attribute_statements_for_lane``), and
    the join then refuses -- so before A-407 the R1 claim was correctly
    payload-free, the helper entry outlived it, and ``assemble_verdict``'s
    B047-item-5 wiring guard raised past ``run_lane``. The consumer got
    ``ERROR/BAD_LANE_CONFIG: lane 'package' recorded helper role(s)
    ['statement-positions'] …`` -- a message about assay's own ``helpers[]``
    array -- instead of ``UNREADABLE_ARTIFACT``, and no verdict document at
    all, because ``run_lane`` never returned to the line that writes one.

    Three assertions, and each fails on its own before the fix: the verdict
    exists (``run_lane`` returned), it carries the JUDGE's reason code, and
    it omits ``helpers`` because the payload that entry described was taken
    away.

    Measured in-image against real Go on the same day, both shapes, through
    the shipped zipapp: ``tests/qualification/test_go_r1_real.py``'s
    ``test_a_stale_go_profile_refuses_through_the_cli_and_writes_a_verdict``
    and its ``//line`` sibling. This one needs no toolchain, which is why it
    is here: the registered gate runs it."""
    lane, head_rev = _stale_profile_lane(git_repo)

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=_StaleOracleAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT, (
        "the consumer must be told the profile and the source disagree, not "
        "that assay recorded a helpers[] entry it could not place"
    )
    r1 = next(claim for claim in verdict.claims if claim.rigor == "R1")
    assert r1.status is Outcome.ERROR
    assert r1.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r1.coverage is None, "an ERROR claim is payload-free (A-136)"
    assert verdict.helpers is None
    assert "helpers" not in verdict.to_dict()
    # R0's own claim is untouched: the command really ran and really wrote
    # the artifact. Without this the test would pass on a run that never
    # reached the oracle at all, which is the vacuity it has to exclude.
    r0 = next(claim for claim in verdict.claims if claim.rigor == "R0")
    assert r0.status is Outcome.PASS


def test_that_refusal_really_reaches_a_verdict_ARTIFACT(
    git_repo: GitRepo, tmp_path: Path
):
    """The half a returned object cannot prove. ``run_lane`` raising is
    exactly what stopped ``--verdict-json`` being written, so this repeats
    the CLI's own two-step -- reserve, then
    :func:`~assay.runner.write_verdict` -- and reads the document back off
    the filesystem. A consumer's whole record of this run is that file."""
    lane, head_rev = _stale_profile_lane(git_repo)
    target = tmp_path / "verdict.json"

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=_StaleOracleAdapter(),
        assay_version="0.1.0",
    )
    with reserve_verdict_output(str(target), stdout=io.StringIO()) as destination:
        runner.write_verdict(verdict, destination)

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "UNREADABLE_ARTIFACT"
    assert "helpers" not in document
