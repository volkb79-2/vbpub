"""B074 at the LANE level — `judge.allow_test_path_targets` driven through a
real `assay.toml` and the real `cli.main`, not through either consumer's
function signature.

**Why this file exists, stated plainly.** Round-1 adversarial review replaced
BOTH of the flag's forwardings in `runner.py` with a literal `False` --

* `evaluate_r1` -> `evaluate_targets` (R1's coverage target resolution)
* `_run_prepared_lane` -> `JudgmentR1` (the effective policy the verdict records)

-- and the entire 4,359-test suite stayed green. The whole feature was
deleted at the plumbing level and nothing noticed, because every test proved
it one layer down, at `_resolve_whole_target(..., allow_test_path_targets=
True)`. B074's own acceptance lines say "**a lane** naming `tests/<...>/lib.py`
WITH the flag set is JUDGED" and "the flag appears in **the verdict**"; those
are sentences about a lane and an artifact, and this module is where they are
defended. `runner.py` gained a third forwarding in the fix round
(`_mutation_targets_whole`, R2's twin of the same gate), covered here too.

Every test below drives `cli.main(["run", ...])` end to end and reads the
verdict JSON it wrote. Each must fail if any forwarding is mutated to a
constant -- verified by doing exactly that before this file was committed.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import GitRepo

from assay.cli import main

#: A `coverage-py-json` artifact covering every line of the harness module
#: below, written by the lane's own argv rather than committed -- a committed
#: artifact is stale evidence, and assay's dirty-tree precondition would refuse
#: an uncommitted one that git can see (hence the `.gitignore` in `_seed`).
_COVERAGE_JSON = json.dumps(
    {
        "meta": {"format": 3, "version": "7.15.3", "branch_coverage": False},
        "files": {
            "tests/_harness/lib.py": {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "excluded_lines": [],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 2,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                    "excluded_lines": 0,
                },
            }
        },
    }
)

#: The R1 half of the lane. `{flag}` is substituted with either the
#: `allow_test_path_targets` line or nothing at all, which is the ONLY
#: difference between the positive and its controlled negative.
_R1_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "cat cov.src > cov.json"]
env = {{}}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["tests"]
fail_under = 100.0
allow_excluded = false
mode = "whole_target"
targets = ["tests/_harness/lib.py"]
{flag}
[lanes.unit.judge.coverage]
format = "coverage-py-json"
artifact = "cov.json"
"""

#: The same lane with R2 declared alongside R1, for the fix round's ruling
#: that the flag reaches `runner._mutation_targets_whole` too. `exit 0` after
#: the artifact write means every mutant survives, which is deliberate and
#: irrelevant: what this file measures is whether R2 RESOLVES the declared
#: target at all, not what it concludes about it.
_R1_R2_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "cat cov.src > cov.json"]
env = {{}}
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["tests"]
fail_under = 100.0
allow_excluded = false
mode = "whole_target"
targets = ["tests/_harness/lib.py"]
{flag}
[lanes.unit.judge.coverage]
format = "coverage-py-json"
artifact = "cov.json"

[lanes.unit.judge.mutation]
jobs = 1
max_mutants = 4
operators = ["python:bool-const-flip"]
"""

_FLAG_LINE = "allow_test_path_targets = true\n"


def _seed(repo: GitRepo, lane_template: str, *, flag: bool) -> Path:
    """B074's reproduced layout as a REAL repository: deployed library code
    under a `tests/` segment, its own coverage artifact gitignored, and a
    lane that names it as a whole target."""
    repo.write(
        "tests/_harness/lib.py",
        "def serve(enabled=True):\n    return 1 if enabled else 0\n",
    )
    repo.write("cov.src", _COVERAGE_JSON)
    repo.write(".gitignore", "cov.json\n")
    repo.write("assay.toml", lane_template.format(flag=_FLAG_LINE if flag else ""))
    repo.commit_all("a whole-target lane over deployed library code under tests/")
    return repo.path / "assay.toml"


def _run(lane_file: Path, verdict: Path) -> tuple[int, str]:
    err = io.StringIO()
    code = main(
        ["run", "unit", "--file", str(lane_file), "--verdict-json", str(verdict)],
        stderr=err,
    )
    return code, err.getvalue()


def _claims(verdict: Path) -> dict[str, dict]:
    document = json.loads(verdict.read_text(encoding="utf-8"))
    return {claim["rigor"]: claim for claim in document["claims"]}


# --- acceptance line 1: "a LANE naming tests/<...>/lib.py ... is JUDGED" ----


def test_a_lane_with_the_flag_judges_the_target_and_passes(
    git_repo: GitRepo, tmp_path: Path
):
    """Kills the `evaluate_r1` -> `evaluate_targets` forwarding mutant: with
    that forwarding pinned to `False` this lane refuses
    `ERROR`/`BAD_LANE_CONFIG` instead of reaching PASS."""
    lane_file = _seed(git_repo, _R1_LANE, flag=True)
    verdict = tmp_path / "verdict.json"

    code, err = _run(lane_file, verdict)

    assert code == 0, err
    document = json.loads(verdict.read_text(encoding="utf-8"))
    assert document["outcome"] == "PASS", document
    r1 = _claims(verdict)["R1"]
    assert r1["status"] == "PASS", r1
    # judged, not vacuously passed: the declared target's own lines
    assert r1["coverage"]["executable"] == 2
    assert r1["coverage"]["covered"] == 2
    assert r1["coverage"]["considered"] == 1


def test_the_same_lane_without_the_flag_refuses_bad_lane_config(
    git_repo: GitRepo, tmp_path: Path
):
    """The controlled negative, differing from the test above by exactly one
    line of `assay.toml`. Without it the PASS above proves only that this
    lane can pass, never that the flag is why."""
    lane_file = _seed(git_repo, _R1_LANE, flag=False)
    verdict = tmp_path / "verdict.json"

    code, err = _run(lane_file, verdict)

    assert code != 0
    assert "BAD_LANE_CONFIG" in err, err
    assert "tests/_harness/lib.py" in err, err
    assert "judge.allow_test_path_targets" in err, err


# --- acceptance line 5: "the flag appears in the verdict" -------------------


def test_the_verdict_records_the_effective_policy(
    git_repo: GitRepo, tmp_path: Path
):
    """Kills the `_run_prepared_lane` -> `JudgmentR1` forwarding mutant: with
    that pinned to `False` the key is never emitted and this fails, while the
    lane still passes -- which is exactly why the PASS above cannot stand in
    for this assertion."""
    lane_file = _seed(git_repo, _R1_LANE, flag=True)
    verdict = tmp_path / "verdict.json"

    assert _run(lane_file, verdict)[0] == 0
    r1 = json.loads(verdict.read_text(encoding="utf-8"))["judgment"]["r1"]
    assert r1["allow_test_path_targets"] is True
    assert r1["mode"] == "whole_target"
    assert r1["targets"] == ["tests/_harness/lib.py"]


def test_a_lane_that_did_not_opt_in_emits_no_such_key_and_still_verifies(
    git_repo: GitRepo, tmp_path: Path
):
    """The additive claim, at lane level: a non-opting lane's `judgment.r1`
    carries the exact key set it carried before B074 existed, and `assay
    verify` accepts it."""
    repo = git_repo
    repo.write("src/mod.py", "def serve(enabled=True):\n    return 1 if enabled else 0\n")
    repo.write(
        "cov.src",
        json.dumps(
            {
                "meta": {"format": 3, "version": "7.15.3", "branch_coverage": False},
                "files": {
                    "src/mod.py": {
                        "executed_lines": [1, 2],
                        "missing_lines": [],
                        "excluded_lines": [],
                        "summary": {
                            "covered_lines": 2,
                            "num_statements": 2,
                            "percent_covered": 100.0,
                            "missing_lines": 0,
                            "excluded_lines": 0,
                        },
                    }
                },
            }
        ),
    )
    repo.write(".gitignore", "cov.json\n")
    repo.write(
        "assay.toml",
        _R1_LANE.format(flag="")
        .replace('source_roots = ["tests"]', 'source_roots = ["src"]')
        .replace('targets = ["tests/_harness/lib.py"]', 'targets = ["src/mod.py"]'),
    )
    repo.commit_all("an ordinary whole-target lane that never opts in")
    verdict = tmp_path / "verdict.json"

    assert _run(repo.path / "assay.toml", verdict)[0] == 0
    r1 = json.loads(verdict.read_text(encoding="utf-8"))["judgment"]["r1"]
    assert "allow_test_path_targets" not in r1, r1
    assert sorted(r1) == [
        "allow_excluded",
        "coverage_artifact",
        "coverage_format",
        "fail_under",
        "mode",
        "require_branch",
        "targets",
    ]
    assert main(["verify", str(verdict)]) == 0


# --- the fix round's ruling: the flag reaches R2's twin of the gate ---------


def test_an_r1_r2_lane_with_the_flag_resolves_the_target_at_BOTH_tiers(
    git_repo: GitRepo, tmp_path: Path
):
    """Controller ruling (round-1 decision ask): `judge.allow_test_path_targets`
    reaches `runner._mutation_targets_whole` too.

    R1 PASSes with the policy recorded, and R2 no longer refuses
    `BAD_LANE_CONFIG` -- it resolves the declared target and reaches a real
    mutation judgment. (`exit 0` per mutant means they all survive, so that
    judgment is `FAIL`/`MUTANTS_SURVIVED`; a real verdict about the target is
    the point, not a green one.)

    Kills the `_mutation_targets_whole` forwarding mutant: pinned to `False`,
    R2 goes back to `ERROR`/`BAD_LANE_CONFIG`.
    """
    lane_file = _seed(git_repo, _R1_R2_LANE, flag=True)
    verdict = tmp_path / "verdict.json"

    _run(lane_file, verdict)

    claims = _claims(verdict)
    assert claims["R1"]["status"] == "PASS", claims["R1"]
    document = json.loads(verdict.read_text(encoding="utf-8"))
    assert document["judgment"]["r1"]["allow_test_path_targets"] is True
    r2 = claims["R2"]
    assert r2.get("reason_code") != "BAD_LANE_CONFIG", (
        "R2 must resolve a declared target the lane vouched for, not refuse it"
    )
    # a real mutation judgment about the target, not a refusal to look at it:
    # candidates were generated FROM the declared test-path target, which is
    # only possible once R2's own gate admitted it
    assert r2["status"] in ("PASS", "FAIL"), r2
    assert r2["mutation"]["candidate_count"] >= 1, r2


def test_an_r1_r2_lane_without_the_flag_refuses_at_both_tiers(
    git_repo: GitRepo, tmp_path: Path
):
    """The controlled negative for the ruling. R1 refuses first (it runs
    first), so R2's own refusal is asserted directly against
    `_mutation_targets_whole` below rather than inferred from this run --
    what this test pins is that removing the flag does NOT leave either tier
    permissive."""
    lane_file = _seed(git_repo, _R1_R2_LANE, flag=False)
    verdict = tmp_path / "verdict.json"

    code, err = _run(lane_file, verdict)

    assert code != 0
    assert "BAD_LANE_CONFIG" in err, err
    assert "tests/_harness/lib.py" in err, err


def test_the_r2_gate_itself_refuses_a_test_path_target_without_the_flag(
    tmp_path: Path,
):
    """`_mutation_targets_whole` directly, because R1 refuses first on a real
    lane and would mask it. Both halves of R2's own three-way split:

    * no flag -> refused, naming the target AND the flag as the remedy (the
      diagnostic gap round-1 review found: the old message named neither);
    * flag, but a test FILENAME -> still refused, on R1's own terms.
    """
    from assay.adapters.python import PythonAdapter
    from assay.errors import AssayError, Outcome, ReasonCode
    from assay.runner import _mutation_targets_whole

    harness = tmp_path / "tests" / "_harness"
    harness.mkdir(parents=True)
    (harness / "lib.py").write_text("x = 1\n", encoding="utf-8")
    (harness / "test_lib.py").write_text("y = 2\n", encoding="utf-8")

    class _Prepared:
        pass

    def _call(target: str, *, flag: bool):
        return _mutation_targets_whole(
            prepared=_Prepared(),
            snapshot_repo_top=tmp_path,
            project_prefix=Path("."),
            deadline=None,
            adapter=PythonAdapter(),
            source_root_paths=(tmp_path / "tests",),
            targets=(target,),
            allow_test_path_targets=flag,
        )

    with pytest.raises(AssayError) as exc:
        _call("tests/_harness/lib.py", flag=False)
    assert exc.value.outcome is Outcome.ERROR
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "judge.allow_test_path_targets" in str(exc.value)

    with pytest.raises(AssayError) as exc:
        _call("tests/_harness/test_lib.py", flag=True)
    assert exc.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "FILENAME" in str(exc.value)
    assert "does not override" in str(exc.value)
