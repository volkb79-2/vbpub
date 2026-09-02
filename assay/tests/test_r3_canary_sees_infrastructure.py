"""B029 (DA-D11, resolved by measurement under DA-R6) — an R3 canary's
side-run resolves the lane's infrastructure facts.

B029 predicted a **misattributed** R3 claim: a lane declaring a ``derived:``
infrastructure fact that resolves perfectly for its main command would report
``ERROR``/``BAD_LANE_CONFIG`` on R3, because ``runner.execute_command`` — the
function ``assay.canary``'s side-run resolves its second ``CommandPlan``
through — accepted no ``infrastructure_source``/``infrastructure_environment``
at all, so ``resolve_command_plan`` raised for any ``derived:`` fact
regardless of whether it was resolvable.

**Measured, and the prediction does not hold for the shipped path.** The R3
path a lane actually takes is ``canary.run_isolated_canary``, driven from
``runner._run_prepared_lane``, and it receives an ALREADY-EXECUTED
``unit.result`` from the snapshot-unit machinery — which resolves
infrastructure — so it never reaches ``execute_command``. Driven through the
installed CLI on a real R3 lane with a real ``ciu.global.toml`` and a
``derived:`` fact, the R3 claim is ``PASS``, and a suite that reads the fact
out of ``os.environ`` passes inside the canary's own control half. B029's
defect was confined to the LEGACY standalone ``canary.run_python_canary``
path, which is public API and which DA-R6 says to fix anyway.

This module is therefore a REGRESSION GUARD written after the measurement,
not a red-first proof. It exists because "already correct" and "untested" are
indistinguishable to a reviewer, and because the coupling it pins — the
canary's side-run seeing the same infrastructure world as the lane's main
command — is exactly what a future refactor of the isolated path could break
silently.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import MappingProxyType

from conftest import GitRepo, make_lane

from assay.cli import main
from assay.config import Lane
from assay.errors import Outcome, ReasonCode

#: The fact's name is its environment-variable name, by B013's own rule.
FACT_NAME = "ASSAY_B029_FACT"
FACT_VALUE = "assay-b029.slice"


def _seed_r3_project_with_a_derived_fact(git_repo: GitRepo) -> Path:
    """A real pytest package, a real ``ciu.global.toml``, and a lane whose
    suite REFUSES unless the derived fact reached its environment.

    The assertion inside the suite is what makes this a test of the canary's
    side-run rather than of the lane's main command: the canary runs the same
    argv in its own control half, so a control that PASSES is direct evidence
    the fact was present there too.
    """
    git_repo.write(".gitignore", "ciu.global.toml\n__pycache__/\n")
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f():\n    return 1\n")
    git_repo.write(
        "tests/test_mod.py",
        "import os\n\n"
        "from pkg.mod import f\n\n\n"
        "def test_f():\n"
        "    assert f() == 1\n\n\n"
        "def test_the_infrastructure_fact_reached_this_process():\n"
        f"    assert os.environ[{FACT_NAME!r}] == {FACT_VALUE!r}\n",
    )
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R3"]
enforcement = "gate"
argv = [{json.dumps(sys.executable)}, "-m", "pytest", "tests", "-q"]
env = {{ PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.infrastructure]
{FACT_NAME} = "derived:deploy.cgroup_parent"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]

[lanes.package.judge.canary]
mechanism = "import-break"
target = "pkg/mod.py"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("seed the R3 lane")
    # Gitignored, exactly as ciu itself keeps it (A-293): rendered state, not
    # committed state. Written AFTER the commit so the tree stays clean.
    git_repo.write(
        "ciu.global.toml", f'[deploy]\ncgroup_parent = "{FACT_VALUE}"\n'
    )
    return path


def test_an_r3_lane_with_a_resolvable_derived_fact_judges_its_canary(
    git_repo: GitRepo, tmp_path: Path
):
    """The claim B029 asks for, through the installed CLI: ``PASS``/``FAIL``
    on the actual canary outcome, never ``ERROR``/``BAD_LANE_CONFIG`` on an
    infrastructure cause that is not real.
    """
    path = _seed_r3_project_with_a_derived_fact(git_repo)
    destination = tmp_path / "verdict.json"

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", str(destination)],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.PASS.exit_code, err.getvalue()
    document = json.loads(destination.read_text(encoding="utf-8"))
    r3 = [claim for claim in document["claims"] if claim["rigor"] == "R3"]
    assert r3, document
    assert r3[0]["status"] == "PASS", r3
    assert r3[0].get("reason_code") is None, r3
    canary = r3[0]["canary"]
    # The control half ran the suite -- the suite that refuses without the
    # fact -- and passed. That is the measurement: the canary's own side-run
    # saw the lane's infrastructure world.
    assert canary["control_outcome"] == "PASS", canary
    assert canary["transformed_outcome"] == "FAIL", canary
    assert canary["observed_reason_code"] == "COMMAND_FAILED", canary
    assert canary["mechanism"] == "import-break", canary


def _legacy_path_fixture(git_repo: GitRepo) -> tuple[Lane, Path, str]:
    """A real repo, a real rendered CIU state file, and a lane declaring a
    ``derived:`` fact, for the LEGACY standalone path.

    The command is the assertion: it imports the target module AND refuses
    unless the derived fact is in its environment, so a run that passes is
    direct evidence the resolved value reached the child process. Import-break
    on the target then makes the transformed half fail on the import, which is
    the mechanism's own expected cause.
    """
    git_repo.write(".gitignore", "ciu.global.toml\n__pycache__/\n")
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f():\n    return 1\n")
    base_commit = git_repo.commit_all("seed the legacy-path package")
    # Gitignored rendered state, exactly as ciu keeps it (A-293), written
    # after the commit so the tree stays clean.
    source = git_repo.write(
        "ciu.global.toml", f'[deploy]\ncgroup_parent = "{FACT_VALUE}"\n'
    )
    script = (
        "import os\n"
        "import pkg.mod\n"
        f"assert os.environ[{FACT_NAME!r}] == {FACT_VALUE!r}\n"
        "assert pkg.mod.f() == 1\n"
    )
    lane = make_lane(
        rigor=("R0",),
        argv=(sys.executable, "-c", script),
        env=MappingProxyType({"PYTHONDONTWRITEBYTECODE": "1"}),
        env_passthrough=("PATH",),
        infrastructure={FACT_NAME: "derived:deploy.cgroup_parent"},
    )
    return lane, source, base_commit


def test_execute_command_resolves_a_derived_fact_into_the_environment_that_runs(
    git_repo: GitRepo,
):
    """DA-R6's second half, half one — and R-1 round 1's BLOCKER 2.

    The test this replaces asserted parameter NAMES, DEFAULTS and a docstring
    substring. R-1 measured that deleting both forwards from
    ``runner.execute_command`` and from ``canary._run_pipeline`` left the
    whole suite green (mutants ``m1``/``m2``): a signature check cannot fail
    for a dropped forward, and DA-R6 asked for a regression guard.

    So this asserts the VALUE, at both ends: the resolved fact is on the
    plan's ``env_effective``, and the child process — which refuses without
    it — passed. Delete the forward at ``runner.py``'s
    ``resolve_command_plan`` call and this goes red twice over: the resolver
    raises ``ERROR``/``BAD_LANE_CONFIG`` for a ``derived:`` fact with no
    source, exactly the misattribution B029 filed.
    """
    from assay import runner

    lane, source, _ = _legacy_path_fixture(git_repo)

    result = runner.execute_command(
        lane,
        cwd=git_repo.path,
        infrastructure_source=source,
        infrastructure_environment={},
    )

    assert result.plan.env_effective[FACT_NAME] == FACT_VALUE, result.plan
    assert result.outcome is Outcome.PASS, (result.outcome, result.stderr_tail)


def test_the_legacy_standalone_canary_runs_both_halves_in_the_lanes_own_world(
    git_repo: GitRepo,
):
    """DA-R6's second half, half two: ``canary.run_python_canary``.

    The control half runs the same argv the lane declares — the one that
    refuses without the fact — so ``control_outcome == PASS`` IS the
    measurement that the canary's own side-run resolved the lane's
    infrastructure. Drop the forward in ``canary._run_pipeline`` (R-1's
    ``m2``) and the control cannot pass: ``resolve_command_plan`` refuses the
    ``derived:`` fact before anything launches.
    """
    from assay import canary
    from assay.adapters.python import PythonAdapter

    lane, source, base_commit = _legacy_path_fixture(git_repo)

    result = canary.run_python_canary(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base_commit=base_commit,
        target_path="pkg/mod.py",
        adapter=PythonAdapter(),
        mechanism=canary.MECHANISM_IMPORT_BREAK,
        infrastructure_source=source,
        infrastructure_environment={},
    )

    assert result.control_outcome is Outcome.PASS, result
    assert result.transformed_outcome is Outcome.FAIL, result
    assert result.observed_reason_code is ReasonCode.COMMAND_FAILED, result
