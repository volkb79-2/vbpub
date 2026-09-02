"""B028 (DA-D10) — a lane-wide ``LANE_TIMEOUT`` writes a verdict artifact.

``LaneDeadline.remaining()`` (`runner.py:216`) raises a bare
``AssayError``/``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` the instant the lane-wide
deadline has expired, and it is the ONE seam every timing check in this
codebase reads through — roughly sixteen call sites across ``runner.py``,
``mutation.py`` and ``canary.py``. B028's finding was that such a raise
escapes uncaught, so a lane that simply runs past ``budget_seconds`` exits
non-zero with **no verdict artifact at all**, even when one was reserved.

DA-D10's shape: **one outer catch per higher-rigor entry point and one for
direct R0's own loop** — never one wrapper per call site, which is the shape
B025 used for a narrower, single-cause failure and which would have to be
re-applied to every new timing check forever.

What this module proves, in the order the ruling states it:

1. the reserved ``--verdict-json`` is WRITTEN, through the installed CLI,
   with a real ``budget_seconds`` and a genuinely slow command (never a fake
   clock: the claim is about a lane that really ran out of time);
2. the document is schema-valid — ``assay verify`` accepts it;
3. the timeout is never MASKED. A cleanup failure after a completed run
   already replaces the highest higher-rigor claim with ``ERROR``/
   ``GIT_FAILED`` (A-193/A-194); when the cleanup failed *because the lane
   ran out of time*, ``GIT_FAILED`` is a false cause and the claim carries
   ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` instead.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
from pathlib import Path
from typing import Iterator

import pytest
from conftest import FakeAdapter, GitRepo, make_lane, make_r1_judge

from assay import runner
from assay.cli import LABEL_GRACE_SECONDS, _cmd_run, build_parser, main
from assay.errors import AssayError, Outcome, ReasonCode

ADAPTER = FakeAdapter()

#: A budget short enough that the deadline is certainly expired by the time
#: the command's own timeout fires, and a command far slower than it. Both
#: are real: no clock is stubbed anywhere in this module.
BUDGET = "1s"
SLOW_COMMAND = "sleep 30"


#: (DA-R9/SF-1) A budget so short that the deadline is already spent before
#: `run_lane` reaches ANY of its own catches -- R-1's round-1 probe value.
EXHAUSTED_BUDGET = "0.001s"


def _lane_file(
    *, rigor: list[str], base: str | None = None, budget: str = BUDGET
) -> str:
    judge = ""
    if base is not None:
        judge = f"""
[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = {{ format = "coverage-py-json", artifact = "cov.json" }}
base = "{base}"
"""
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = {json.dumps(rigor)}
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(SLOW_COMMAND)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "{budget}"
allow_argv_append = false
{judge}"""


def _seed(git_repo: GitRepo) -> str:
    (git_repo.path / "src").mkdir(exist_ok=True)
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write(
        "src/mod.py", "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    )
    git_repo.commit_all("add g")
    return base_rev


def _run_to_reserved_path(
    git_repo: GitRepo, lane_text: str, destination: Path
) -> tuple[int, str]:
    path = git_repo.write("assay.toml", lane_text)
    git_repo.commit_all("add assay.toml")
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", str(destination)],
        stdout=out,
        stderr=err,
    )
    return code, err.getvalue()


# --- 1 + 2: the artifact exists and verifies, on BOTH dispatch paths --------


def test_a_direct_r0_lane_that_runs_out_of_time_still_writes_its_verdict(
    git_repo: GitRepo, tmp_path: Path
):
    """B028's own reproduction, measured: an R0-only lane (A-189 keeps it on
    the direct live-tree path, never the snapshot state machine) whose
    command outlives ``budget_seconds``.

    Before this fix the command's own timeout was converted correctly into a
    ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` ``CommandResult`` — and then the
    very next ``deadline.remaining()``, the post-command dirt guard, raised
    uncaught. Exit 4 with nothing written to the reserved path: the operator
    was told the lane timed out and given no document saying so.
    """
    _seed(git_repo)
    destination = tmp_path / "verdict.json"

    code, err = _run_to_reserved_path(
        git_repo, _lane_file(rigor=["R0"]), destination
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code, err
    assert destination.exists(), (
        "the reserved --verdict-json was never written: "
        f"exit {code}, stderr {err!r}"
    )
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["outcome"] == "BUDGET_EXCEEDED", document
    assert document["reason_code"] == "LANE_TIMEOUT", document
    r0 = [claim for claim in document["claims"] if claim["rigor"] == "R0"]
    assert r0 and r0[0]["reason_code"] == "LANE_TIMEOUT", document


def test_a_higher_rigor_lane_that_runs_out_of_time_still_writes_its_verdict(
    git_repo: GitRepo, tmp_path: Path
):
    """The other dispatch path, through ``_run_higher_rigor_lane``.

    Measured at the pre-fix tip and found ALREADY correct — its single outer
    ``try`` (`runner.py:3819`) spans base resolution, the scratch root and
    the whole snapshot block, and its ``except AssayError`` already returns a
    refusal verdict. This test is therefore a REGRESSION GUARD, not a
    red-first proof, and it is here because DA-D10 names both entry points
    and a reviewer cannot tell "already covered" from "untested" without it.
    """
    base_rev = _seed(git_repo)
    destination = tmp_path / "verdict.json"

    code, err = _run_to_reserved_path(
        git_repo, _lane_file(rigor=["R0", "R1"], base=base_rev), destination
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code, err
    assert destination.exists(), err
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["outcome"] == "BUDGET_EXCEEDED", document
    assert document["reason_code"] == "LANE_TIMEOUT", document
    assert {claim["rigor"] for claim in document["claims"]} == {"R0", "R1"}, document


@pytest.mark.parametrize("rigor", [["R0"], ["R0", "R1"]])
def test_a_budget_already_spent_before_run_lane_still_writes_its_verdict(
    git_repo: GitRepo, tmp_path: Path, rigor: list[str]
):
    """DA-R9 / R-1 round 1's SF-1, on BOTH dispatch paths.

    ``budget = "0.001s"`` is spent before ``run_lane`` reaches either of
    B028's own catches: R-1 captured the escape at ``git.repo_top`` ->
    ``git._run_bounded`` -> ``LaneDeadline.remaining``, upstream of the
    direct-R0 ``try`` and of ``_run_higher_rigor_lane``'s outer catch alike.
    The error reached ``main()``'s handler, which printed the line and
    returned exit 4 having written nothing -- identically on the pre-B028
    build, so this was never something B028 regressed; it is the part of
    DA-D10's intent that the two in-``run_lane`` catches structurally cannot
    reach.

    One ``except AssayError`` scoped to ``LANE_TIMEOUT`` in
    ``cli._run_reserved`` covers both paths, because both go through the one
    ``run_lane`` call. The document it writes is the ordinary payload-free
    refusal ``refuse_lane`` builds on every declared level.
    """
    base_rev = _seed(git_repo)
    destination = tmp_path / "verdict.json"

    code, err = _run_to_reserved_path(
        git_repo,
        _lane_file(
            rigor=rigor,
            base=base_rev if "R1" in rigor else None,
            budget=EXHAUSTED_BUDGET,
        ),
        destination,
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code, err
    assert destination.exists(), (
        "the reserved --verdict-json was never written: "
        f"exit {code}, stderr {err!r}"
    )
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["outcome"] == "BUDGET_EXCEEDED", document
    assert document["reason_code"] == "LANE_TIMEOUT", document
    assert {claim["rigor"] for claim in document["claims"]} == set(rigor), document
    for claim in document["claims"]:
        assert claim["reason_code"] == "LANE_TIMEOUT", document
    # The line still says WHY, exactly once (B053/DA-R3's bar).
    assert err.count("assay: BUDGET_EXCEEDED/LANE_TIMEOUT: ") == 1, err


@pytest.mark.parametrize("rigor", [["R0"], ["R0", "R1"]])
def test_a_timeout_inside_run_lane_but_above_its_own_catches_writes_a_verdict(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    rigor: list[str],
):
    """The second half of DA-R9's handler: a ``LANE_TIMEOUT`` raised inside
    ``run_lane`` but ABOVE both of B028's catches -- ``git.repo_top`` is one
    such call, before the direct-R0 ``try`` opens.

    The window is real but not reachable by choosing a budget: it is the few
    milliseconds between the CLI's own ``head_rev`` and ``run_lane``'s first
    internal catch, and a test that raced for it would be flaky by
    construction. So the deadline expiry is injected at that exact seam --
    ``runner.run_lane`` itself raising what ``LaneDeadline.remaining`` raises.
    That is a stub of assay's OWN function, never of an external system
    (A-334); what is under test is the CLI's handler, and the exception it
    must handle is constructed from the real ``LaneDeadline`` vocabulary.
    """
    base_rev = _seed(git_repo)
    destination = tmp_path / "verdict.json"

    def _timed_out(*args: object, **kwargs: object):
        raise AssayError(
            "the lane-wide deadline expired",
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
        )

    monkeypatch.setattr(runner, "run_lane", _timed_out)

    code, err = _run_to_reserved_path(
        git_repo,
        _lane_file(rigor=rigor, base=base_rev if "R1" in rigor else None),
        destination,
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code, err
    assert destination.exists(), err
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["outcome"] == "BUDGET_EXCEEDED", document
    assert document["reason_code"] == "LANE_TIMEOUT", document
    assert {claim["rigor"] for claim in document["claims"]} == set(rigor), document
    # The commit label is a REAL resolved commit here: this seam is past the
    # CLI's own `head_rev`, so nothing had to be recovered.
    assert document["commit"] == git_repo.head(), document
    assert err.count("assay: BUDGET_EXCEEDED/LANE_TIMEOUT: ") == 1, err


def test_a_non_timeout_error_out_of_run_lane_is_never_laundered_into_a_verdict(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The handler is scoped to ``LANE_TIMEOUT`` alone. Anything else still
    propagates to ``main()``'s handler: a bug turned into a verdict is the
    silent green this project exists to remove."""
    _seed(git_repo)
    destination = tmp_path / "verdict.json"

    def _other(*args: object, **kwargs: object):
        raise AssayError(
            "something else entirely",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        )

    monkeypatch.setattr(runner, "run_lane", _other)

    code, err = _run_to_reserved_path(git_repo, _lane_file(rigor=["R0"]), destination)

    assert code == Outcome.ERROR.exit_code, err
    assert not destination.exists(), (
        "a non-timeout error was converted into a verdict artifact"
    )


@pytest.mark.parametrize("rigor", [["R0"], ["R0", "R1"]])
def test_the_timed_out_verdict_is_one_assay_verify_accepts(
    git_repo: GitRepo, tmp_path: Path, rigor: list[str]
):
    """DA-D10 asks for "a real, schema-valid verdict artifact". Schema-valid
    is not an assertion about keys a test happened to check — it is what
    ``assay verify`` says, which re-derives every claim's status
    independently of the model that wrote it.
    """
    base_rev = _seed(git_repo)
    destination = tmp_path / "verdict.json"

    _run_to_reserved_path(
        git_repo,
        _lane_file(rigor=rigor, base=base_rev if "R1" in rigor else None),
        destination,
    )
    assert destination.exists()

    out, err = io.StringIO(), io.StringIO()
    code = main(["verify", str(destination)], stdout=out, stderr=err)
    assert code == 0, err.getvalue() + out.getvalue()


def test_the_refusal_names_the_deadline_on_the_diagnostics_stream(
    git_repo: GitRepo, tmp_path: Path
):
    """B053's emitter covers this refusal too, exactly once — the direct-R0
    catch is a new conversion site and every conversion site announces."""
    _seed(git_repo)
    destination = tmp_path / "verdict.json"

    _, err = _run_to_reserved_path(git_repo, _lane_file(rigor=["R0"]), destination)

    lines = [
        line
        for line in err.splitlines()
        if line.startswith("assay: BUDGET_EXCEEDED/LANE_TIMEOUT: ")
    ]
    assert len(lines) == 1, err


# --- 2b (A-425/DA-R13): the recovered commit label is itself bounded --------


def _run_to_reserved_path_with_grace(
    git_repo: GitRepo,
    lane_text: str,
    destination: Path,
    *,
    label_grace_seconds: float,
) -> tuple[int, str]:
    """``_run_to_reserved_path``, with A-425's grace supplied as a PARAMETER.

    ``main()`` has no such parameter and never will -- the grace is policy,
    not an operator control -- so this helper enters at ``_cmd_run``, which
    takes it, and then reproduces ``main()``'s own two-line boundary
    (`cli.py:307-312`: announce through the one emitter, return the error's
    exit code) so that what the test observes is exactly what a real
    ``assay run`` observes. Nothing is stubbed: the lane, the repository,
    the budget and the ``git rev-parse`` are all real (A-334).
    """
    path = git_repo.write("assay.toml", lane_text)
    git_repo.commit_all("add assay.toml")
    out, err = io.StringIO(), io.StringIO()
    args = build_parser().parse_args(
        ["run", "package", "--file", str(path), "--verdict-json", str(destination)]
    )
    try:
        code = _cmd_run(
            args, [], out, err, label_grace_seconds=label_grace_seconds
        )
    except AssayError as exc:
        runner.announce_refusal(exc, diagnostics=err)
        code = exc.exit_code
    return code, err.getvalue()


def test_the_default_grace_is_the_documented_policy_constant():
    """DA-R13 named the constant and the reason; both are load-bearing.

    A grace that any caller could set from the command line would be an
    operator control over how long assay may hang after a budget expired,
    which is the opposite of what the bound is for; a grace with no stated
    reason would be DESIGN-GUIDE §5's invented default. So: one module-level
    constant, defaulted into both functions, documented where it is defined.
    """
    from assay import cli

    assert cli.LABEL_GRACE_SECONDS == 2.0
    for function in (cli._cmd_run, cli._run_reserved):
        parameter = inspect.signature(function).parameters["label_grace_seconds"]
        assert parameter.default == cli.LABEL_GRACE_SECONDS, function
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, function


@pytest.mark.parametrize("rigor", [["R0"], ["R0", "R1"]])
def test_a_grace_that_also_expires_writes_no_verdict_and_says_which_read_failed(
    git_repo: GitRepo, tmp_path: Path, rigor: list[str]
):
    """A-425/DA-R13's second path, on BOTH dispatch paths.

    The budget is already spent before ``run_lane`` (the ``0.001s`` probe),
    so the handler runs; the grace is ``0.0``, so the label read it makes is
    refused by ``LaneDeadline.remaining`` before ``git rev-parse`` is even
    started. Three things must then hold, and each is a separate ruling:

    * **no verdict is written** -- the label is the one field
      :func:`runner.refuse_lane` cannot be given, and a fabricated commit is
      the one thing this project must never emit;
    * **the one line says the LABEL could not be read**, not merely that the
      lane timed out: the operator's next move is Git, not ``budget``;
    * **the exit code is unchanged** from the pre-A-425 build -- the outcome
      and reason code are still the ORIGINAL timeout's, because the lane
      really did run out of time and the failed label read does not rename
      what happened to the lane.

    ``0.0`` arrives through the function parameter, never a monkeypatch: the
    thing under test is the bound, and a stubbed clock would test the stub.
    """
    base_rev = _seed(git_repo)
    destination = tmp_path / "verdict.json"

    code, err = _run_to_reserved_path_with_grace(
        git_repo,
        _lane_file(
            rigor=rigor,
            base=base_rev if "R1" in rigor else None,
            budget=EXHAUSTED_BUDGET,
        ),
        destination,
        label_grace_seconds=0.0,
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code, err
    assert not destination.exists(), (
        "a verdict was written without a commit label that could be read"
    )
    assert err.count("assay: BUDGET_EXCEEDED/LANE_TIMEOUT: ") == 1, err
    assert "commit label" in err, err
    assert "0.0s grace" in err, err


def test_the_grace_bounds_the_label_read_rather_than_leaving_it_unbounded(
    git_repo: GitRepo, tmp_path: Path
):
    """The control for the test above, and the whole point of A-425.

    With the DEFAULT grace the identical lane writes its verdict carrying the
    REAL ``HEAD`` -- so the ``0.0`` result above is the bound doing its job,
    not the recovery path being broken. Both halves are the same code path
    with one number changed, which is what makes the bound observable at all.
    """
    _seed(git_repo)
    destination = tmp_path / "verdict.json"

    code, err = _run_to_reserved_path_with_grace(
        git_repo,
        _lane_file(rigor=["R0"], budget=EXHAUSTED_BUDGET),
        destination,
        label_grace_seconds=LABEL_GRACE_SECONDS,
    )

    assert code == Outcome.BUDGET_EXCEEDED.exit_code, err
    assert destination.exists(), err
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["commit"] == git_repo.head(), document
    assert "commit label" not in err, err


# --- 3: the timeout is never masked as a cleanup failure --------------------


def _factory_raising_on_exit(exc: AssayError):
    """A scratch-root factory whose TEARDOWN fails.

    This is the seam A-193/A-194's cleanup-failure rule was written for: the
    lane's own work completed and produced a ``_PreparedOutcome``, and only
    the outer scratch cleanup then failed. It is assay's own seam, not a
    stand-in for an external system, so A-334 does not apply — the claim
    under test is assay's disposition rule, not any tool's behaviour.
    """

    @contextlib.contextmanager
    def factory() -> Iterator[Path]:
        with runner.default_scratch_root() as root:
            yield root
        raise exc

    return factory


def _r1_lane_for(git_repo: GitRepo, base_rev: str):
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    return make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", "printf '{}' > cov.json"),
    )


def _seed_zzz(git_repo: GitRepo) -> tuple[str, str]:
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nNO-CODE\n")
    head_rev = git_repo.commit_all("add pkg head")
    return base_rev, head_rev


def test_a_cleanup_that_failed_because_time_ran_out_says_so(git_repo: GitRepo):
    """DA-D10's "never masks the timeout".

    A-193/A-194 replace the highest declared higher-rigor claim with
    ``ERROR``/``GIT_FAILED`` when outer scratch cleanup alone fails. When
    that cleanup failed because the LANE-WIDE DEADLINE expired, ``GIT_FAILED``
    names a Git failure that did not happen and hides the one fact the
    operator needs. The pair carried is the exception's own.
    """
    base_rev, head_rev = _seed_zzz(git_repo)
    diagnostics = io.StringIO()

    verdict = runner.run_lane(
        _r1_lane_for(git_repo, base_rev),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        scratch_root_factory=_factory_raising_on_exit(
            AssayError(
                "the lane-wide deadline expired",
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
            )
        ),
        diagnostics=diagnostics,
    )

    r1 = [claim for claim in verdict.claims if claim.rigor == "R1"]
    assert r1, verdict.claims
    assert r1[0].status is Outcome.BUDGET_EXCEEDED, r1[0]
    assert r1[0].reason_code is ReasonCode.LANE_TIMEOUT, r1[0]
    assert verdict.outcome is Outcome.BUDGET_EXCEEDED, verdict
    # R0 completed and is untouched: only the highest higher-rigor claim is
    # replaced, exactly as A-193/A-194 already specify.
    r0 = [claim for claim in verdict.claims if claim.rigor == "R0"]
    assert r0 and r0[0].status is Outcome.PASS, r0


def test_a_cleanup_that_failed_for_any_other_reason_still_says_git_failed(
    git_repo: GitRepo,
):
    """The control. A-193/A-194's rule is UNCHANGED for every cause that is
    not the lane-wide deadline — this is a narrowing of one case, not a
    replacement of the rule.
    """
    base_rev, head_rev = _seed_zzz(git_repo)

    verdict = runner.run_lane(
        _r1_lane_for(git_repo, base_rev),
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        scratch_root_factory=_factory_raising_on_exit(
            AssayError(
                "the scratch root could not be removed",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.GIT_FAILED,
            )
        ),
    )

    r1 = [claim for claim in verdict.claims if claim.rigor == "R1"]
    assert r1 and r1[0].status is Outcome.ERROR, r1
    assert r1[0].reason_code is ReasonCode.GIT_FAILED, r1
