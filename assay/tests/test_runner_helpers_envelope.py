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
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane

from assay import runner
from assay.adapters.base import HelperInvocation
from assay.errors import AssayError, Outcome, ReasonCode
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
