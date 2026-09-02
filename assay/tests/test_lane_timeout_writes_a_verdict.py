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
import io
import json
from pathlib import Path
from typing import Iterator

import pytest
from conftest import FakeAdapter, GitRepo, make_lane, make_r1_judge

from assay import runner
from assay.cli import main
from assay.errors import AssayError, Outcome, ReasonCode

ADAPTER = FakeAdapter()

#: A budget short enough that the deadline is certainly expired by the time
#: the command's own timeout fires, and a command far slower than it. Both
#: are real: no clock is stubbed anywhere in this module.
BUDGET = "1s"
SLOW_COMMAND = "sleep 30"


def _lane_file(*, rigor: list[str], base: str | None = None) -> str:
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
budget = "{BUDGET}"
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
