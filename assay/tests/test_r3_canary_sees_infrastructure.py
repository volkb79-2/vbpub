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

from conftest import GitRepo

from assay.cli import main
from assay.errors import Outcome

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


def test_the_legacy_standalone_canary_forwards_the_same_two_parameters():
    """DA-R6's second half: ``execute_command`` and
    ``canary.run_python_canary`` accept and forward
    ``infrastructure_source``/``infrastructure_environment``.

    ``run_python_canary`` is public API and is the path B029's defect was
    really confined to. A signature check is the honest test for it here: the
    parameters exist, they default to ``None`` (so every pre-existing caller
    is unchanged), and the docstring no longer claims the function accepts
    neither.
    """
    import inspect

    from assay import canary, runner

    for function in (runner.execute_command, canary.run_python_canary):
        parameters = inspect.signature(function).parameters
        for name in ("infrastructure_source", "infrastructure_environment"):
            assert name in parameters, (function.__name__, name)
            assert parameters[name].default is None, (function.__name__, name)

    doc = runner.execute_command.__doc__ or ""
    assert "accepts no" not in doc, (
        "execute_command's docstring still states the defect B029 filed"
    )
