"""B043 — the EXECUTION half of the lane-level ``cwd``: the lane command,
every R2 candidate re-execution and every R3 canary run enter the same
resolved directory, and nothing else re-roots.

``test_config_lane_cwd.py`` owns the load half. This module owns the property
that made B043 worth a schema bump: *one* declared directory, honoured at
every site that starts a process on the lane's behalf. The obvious way to get
that wrong is four independent joins that drift; the oracle here is therefore
not "the code looks right" but a REAL command that appends its own ``$PWD`` to
a log outside the snapshot, run through the real runner, with every recorded
line asserted afterwards. A site that forgot the join writes a line that does
not end in ``/app`` and the assertion names it.

The R2 proof is stronger than the log alone: the lane's command greps a path
that is spelled RELATIVE to the declared cwd (``src/mod.py``, not
``app/src/mod.py``), so a mutant executed at the snapshot root cannot find the
file at all. The single generated mutant is killed mechanically, by the real
command, or the test fails.

Negative controls are the point of the last section: ``environment_command``
is a probe of the INVOKING environment (B010/DESIGN-GUIDE §4), not the lane
command, and a declared artifact path stays project-root-relative (A-271, one
path grammar). Both would be silently wrong if the join had been put one layer
too low.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import GitRepo, make_lane, make_r2_judge, make_r3_judge

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import CanaryConfig, MutationConfig
from assay.errors import Outcome, ReasonCode

_MUTATION = MutationConfig(jobs=1, max_mutants=50, operators=("python:compare-swap",))


def _pwd_log(tmp_path: Path) -> Path:
    """A log file OUTSIDE any snapshot and outside the consumer repository.

    Deliberately not inside the repo: a file the lane's command writes into
    the snapshot would either have to be gitignored (changing what the run
    measures) or would trip the post-command dirt check, and either way the
    evidence would be entangled with the thing it is evidence about.
    """
    return tmp_path / "pwd-log.txt"


def _logging(command: str) -> str:
    """*command*, preceded by an append of the shell's own ``$PWD``."""
    return f'printf "%s\\n" "$PWD" >> "$PWDLOG"; {command}'


def _recorded(log: Path) -> list[str]:
    assert log.exists(), "the lane's command never ran at all"
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


def _seed_compare_swap_site_under(repo: GitRepo, prefix: str) -> tuple[str, str]:
    """``_seed_compare_swap_site``'s two-commit fixture, moved under
    *prefix* -- the monorepo shape ``cwd`` exists for."""
    repo.write(".gitignore", "cov.json\n")
    repo.write(f"{prefix}/src/mod.py", "def f(x):\n    return 0\n")
    base_rev = repo.commit_all("add mod.py")
    repo.write(f"{prefix}/src/mod.py", "def f(x):\n    return x > 0\n")
    head_rev = repo.commit_all("introduce a compare-swap site")
    return base_rev, head_rev


# --------------------------------------------------------------------------
# The lane command itself
# --------------------------------------------------------------------------


def test_the_lane_command_runs_in_the_declared_cwd(git_repo: GitRepo, tmp_path: Path):
    log = _pwd_log(tmp_path)
    git_repo.write("app/keep.txt", "x\n")
    git_repo.commit_all("add app/")
    lane = make_lane(
        rigor=("R0",),
        argv=("/bin/sh", "-c", _logging("exit 0")),
        env={"PWDLOG": str(log)},
        cwd="app",
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS
    recorded = _recorded(log)
    assert recorded and all(entry.endswith("/app") for entry in recorded), recorded


def test_without_a_declared_cwd_the_command_runs_at_the_project_root(
    git_repo: GitRepo, tmp_path: Path
):
    """The control. Without it, a test asserting ``/app`` proves only that
    the command ran SOMEWHERE ending in ``app`` -- this pins the default."""
    log = _pwd_log(tmp_path)
    git_repo.write("app/keep.txt", "x\n")
    git_repo.commit_all("add app/")
    lane = make_lane(
        rigor=("R0",),
        argv=("/bin/sh", "-c", _logging("exit 0")),
        env={"PWDLOG": str(log)},
    )

    runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    recorded = _recorded(log)
    assert recorded and not any(entry.endswith("/app") for entry in recorded), recorded


def test_the_verdict_records_the_declared_cwd(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("app/keep.txt", "x\n")
    git_repo.commit_all("add app/")
    lane = make_lane(
        rigor=("R0",),
        argv=("/bin/sh", "-c", "exit 0"),
        cwd="app",
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.cwd_declared == "app"
    assert verdict.to_dict()["cwd_declared"] == "app"


def test_a_lane_declaring_no_cwd_records_no_key(git_repo: GitRepo):
    """Absent, never ``"."`` -- the whole reason ``cwd_declared`` is
    independently optional rather than a member of the lane-resolved
    all-present-or-all-absent group."""
    git_repo.write("keep.txt", "x\n")
    git_repo.commit_all("seed")
    lane = make_lane(rigor=("R0",), argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.cwd_declared is None
    assert "cwd_declared" not in verdict.to_dict()


# --------------------------------------------------------------------------
# The snapshot path, and the commit-bound refusal
# --------------------------------------------------------------------------


def test_a_cwd_absent_from_the_resolved_commit_is_refused_naming_the_commit(
    git_repo: GitRepo, tmp_path: Path
):
    """The load-time check proved the directory exists in the CHECKOUT. This
    is the other fact: the snapshot holds committed objects only, so an
    untracked directory is genuinely absent from it -- and this is the check
    that can name the commit, so it is the one that does."""
    git_repo.write("src/mod.py", "x = 1\n")
    git_repo.commit_all("seed")
    # Present in the checkout, never committed: exactly the state the loader
    # cannot distinguish and this check can.
    (git_repo.path / "build").mkdir()
    head = git_repo.head()
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=("/bin/sh", "-c", "exit 0"),
        cwd="build",
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=head,
            mutation=_MUTATION,
        ),
    )

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG


# --------------------------------------------------------------------------
# R2 -- every candidate re-execution
# --------------------------------------------------------------------------


def test_every_r2_candidate_runs_in_the_declared_cwd(git_repo: GitRepo, tmp_path: Path):
    """Two independent proofs in one run.

    The log proves every process the lane started -- the baseline and the one
    mutant -- entered ``<snapshot>/app``. The KILL proves it mechanically:
    the command greps ``src/mod.py``, spelled relative to the declared cwd,
    so a mutant executed at the snapshot root would not find the file, grep
    would fail, and the mutant would be recorded killed for the WRONG reason
    -- which the log then contradicts.
    """
    log = _pwd_log(tmp_path)
    base_rev, _head = _seed_compare_swap_site_under(git_repo, "app")
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=("/bin/sh", "-c", _logging('grep -q "x > 0" src/mod.py')),
        env={"PWDLOG": str(log)},
        cwd="app",
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "app" / "src",),
            base=base_rev,
            mutation=_MUTATION,
        ),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS, verdict.reason_code
    r2_claim = next(claim for claim in verdict.claims if claim.rigor == "R2")
    assert r2_claim.mutation is not None
    assert len(r2_claim.mutation.killed) == 1
    assert r2_claim.mutation.survived == ()
    recorded = _recorded(log)
    # baseline + one mutant, and every one of them in the declared directory.
    assert len(recorded) >= 2, recorded
    assert all(entry.endswith("/app") for entry in recorded), recorded


# --------------------------------------------------------------------------
# R3 -- both canary halves
# --------------------------------------------------------------------------


def test_both_r3_canary_halves_run_in_the_declared_cwd(
    git_repo: GitRepo, tmp_path: Path
):
    log = _pwd_log(tmp_path)
    git_repo.write("app/pkg/__init__.py", "")
    git_repo.write("app/pkg/mod.py", "def f():\n    return 1\n")
    git_repo.write(
        "app/tests/test_mod.py",
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    )
    git_repo.commit_all("add pkg under app/")
    lane = make_lane(
        rigor=("R0", "R3"),
        argv=(
            "/bin/sh",
            "-c",
            _logging(f'exec "{sys.executable}" -m pytest tests -q'),
        ),
        env={"PWDLOG": str(log), "PYTHONDONTWRITEBYTECODE": "1"},
        env_passthrough=("PATH",),
        cwd="app",
        judge=make_r3_judge(
            source_root_paths=(git_repo.path / "app" / "pkg",),
            canary=CanaryConfig(mechanism="import-break", target="app/pkg/mod.py"),
        ),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.outcome is Outcome.PASS, verdict.reason_code
    r3_claim = next(claim for claim in verdict.claims if claim.rigor == "R3")
    assert r3_claim.canary is not None
    assert r3_claim.canary.control_outcome is Outcome.PASS
    assert r3_claim.canary.transformed_outcome is Outcome.FAIL
    recorded = _recorded(log)
    # baseline + control half + transformed half.
    assert len(recorded) >= 3, recorded
    assert all(entry.endswith("/app") for entry in recorded), recorded


# --------------------------------------------------------------------------
# Nothing else re-roots (A-271)
# --------------------------------------------------------------------------


def test_the_environment_command_probe_keeps_the_invoking_cwd(
    git_repo: GitRepo, tmp_path: Path
):
    """B010's probe runs in the INVOKING environment before any snapshot
    work; ``cwd`` is the LANE command's declaration and says nothing about
    it. This is the one call site that builds its own ``CommandPlan`` by
    hand, which is how it gets the right answer by construction."""
    probe_log = tmp_path / "probe-log.txt"
    lane_log = _pwd_log(tmp_path)
    git_repo.write("app/keep.txt", "x\n")
    git_repo.commit_all("add app/")
    lane = make_lane(
        rigor=("R0",),
        argv=("/bin/sh", "-c", _logging("exit 0")),
        env={"PWDLOG": str(lane_log)},
        cwd="app",
    )
    lane = type(lane)(
        **{
            **{
                field: getattr(lane, field)
                for field in lane.__dataclass_fields__
            },
            "environment_command": (
                "/bin/sh",
                "-c",
                f'printf "%s\\n" "$PWD" >> "{probe_log}"',
            ),
        }
    )

    runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    probed = [
        line for line in probe_log.read_text(encoding="utf-8").splitlines() if line
    ]
    assert probed and not any(entry.endswith("/app") for entry in probed), probed
    assert all(entry.endswith("/app") for entry in _recorded(lane_log))
