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
from assay.config import CanaryConfig, IsolationConfig, MutationConfig
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


# --------------------------------------------------------------------------
# cwd x isolation.link_paths (fix round 1) — the SNAPSHOT ESCAPE
#
# The two keys were individually correct and composed into a real escape.
# `_plant_link_paths` plants its symlinks from `_build`, AFTER `_verify`
# (A-370), so by the time the commit-bound `cwd` check ran there was a live
# symlink at `<snapshot>/deps` pointing at `<checkout>/deps`. That check was
# `run_cwd.is_dir()` — a filesystem test, which FOLLOWS the link and answers
# True — so an untracked, gitignored directory was accepted as commit-bound
# and the lane's command executed in the consumer's real working tree, writing
# to it for real.
#
# The fix decides the question from the commit's own manifest
# (`isolation.Snapshot.tracked_directories`), the same oracle `_plant_link_paths`
# rule 2 already consults, so no symlink can enter the answer. `assay.config`
# additionally refuses the declaration pair outright, which is why the lane
# below is built through `make_lane` rather than loaded from a file: this is
# the layer that must hold even if that one is ever bypassed.
# --------------------------------------------------------------------------


def _link_isolation(*paths: str) -> IsolationConfig:
    return IsolationConfig(
        snapshot_selection="repository",
        unsafe_symlink_omissions=(),
        link_paths=paths,
    )


def test_an_untracked_cwd_reached_through_a_link_path_is_refused_not_followed(
    git_repo: GitRepo, tmp_path: Path
):
    """The reproduction, and the proof the escape is closed.

    Three independent witnesses, because "the verdict says ERROR" alone would
    not distinguish a closed escape from a differently-worded one: the lane is
    refused, the command's own `$PWD` log does not exist (it never started),
    and the file that command would have created is absent from the CHECKOUT
    — which is where it landed before the fix.
    """
    log = _pwd_log(tmp_path)
    # `deps` must be covered by a COMMITTED .gitignore or `_plant_link_paths`'
    # own cleanliness rule (A-371) refuses first, and this test would then pass
    # for the wrong reason. No trailing slash: A-372.
    git_repo.write(".gitignore", "cov.json\ndeps\n")
    git_repo.write("src/mod.py", "x = 1\n")
    git_repo.commit_all("seed")
    head = git_repo.head()
    # Present in the invoking checkout, absent from the commit: the state the
    # planted symlink used to disguise.
    deps = git_repo.path / "deps"
    deps.mkdir()
    (deps / "canary.txt").write_text("a real dependency closure\n", encoding="utf-8")

    lane = make_lane(
        rigor=("R0", "R2"),
        argv=("/bin/sh", "-c", _logging('printf escaped > escaped.txt')),
        env={"PWDLOG": str(log)},
        cwd="deps",
        isolation=_link_isolation("deps"),
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
    assert not log.exists(), "the lane's command ran; it must never have started"
    assert not (deps / "escaped.txt").exists(), (
        "the lane's command wrote into the CONSUMER'S OWN CHECKOUT — the "
        "snapshot escape this test exists to close"
    )
    # B041(b) rule 6, one assertion wide: teardown left the linked closure
    # alone even on this refusal path.
    assert (deps / "canary.txt").read_text(encoding="utf-8") == (
        "a real dependency closure\n"
    )


def test_the_same_lane_without_link_paths_is_refused_identically(
    git_repo: GitRepo, tmp_path: Path
):
    """The negative control that makes the test above about the PAIR.

    Strip `link_paths` and nothing else: the cwd is still untracked and is
    still refused with the same terminal. Without this, a reader could not
    tell whether the refusal above came from the escape being closed or from
    `link_paths` being refused for some unrelated reason of its own.
    """
    log = _pwd_log(tmp_path)
    git_repo.write(".gitignore", "cov.json\ndeps\n")
    git_repo.write("src/mod.py", "x = 1\n")
    git_repo.commit_all("seed")
    head = git_repo.head()
    (git_repo.path / "deps").mkdir()

    lane = make_lane(
        rigor=("R0", "R2"),
        argv=("/bin/sh", "-c", _logging("exit 0")),
        env={"PWDLOG": str(log)},
        cwd="deps",
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
    assert not log.exists()


def test_a_TRACKED_cwd_still_composes_with_a_link_path_beside_it(
    git_repo: GitRepo, tmp_path: Path
):
    """The positive half: the fix refuses the escape, not the feature.

    `cwd = "app"` (a committed directory) with `link_paths = ["app/node_modules"]`
    (an uncommitted dependency closure linked in beside its sources) is the
    exact shape B041(b) and B043 were built to compose into, and it is what a
    manifest-based check keeps working where a blanket "cwd may not coexist
    with link_paths" rule would have broken it.

    The command proves BOTH halves from inside the snapshot: it reads the
    linked marker through a path relative to the declared cwd, and it logs a
    `$PWD` asserted afterwards to end in `/app` and to be outside the
    consumer's checkout entirely.
    """
    log = _pwd_log(tmp_path)
    git_repo.write(".gitignore", "cov.json\napp/node_modules\n")
    git_repo.write("app/src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add app/")
    git_repo.write("app/src/mod.py", "def f(x):\n    return x > 0\n")
    head = git_repo.commit_all("introduce a compare-swap site")
    modules = git_repo.path / "app" / "node_modules"
    modules.mkdir(parents=True)
    (modules / "marker.txt").write_text("linked\n", encoding="utf-8")

    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(
            "/bin/sh",
            "-c",
            _logging(
                'grep -q linked node_modules/marker.txt && grep -q "x > 0" src/mod.py'
            ),
        ),
        env={"PWDLOG": str(log)},
        cwd="app",
        isolation=_link_isolation("app/node_modules"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "app" / "src",),
            base=base_rev,
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

    assert verdict.outcome is Outcome.PASS, verdict.reason_code
    assert verdict.snapshot_policy is not None
    assert verdict.snapshot_policy.link_paths == ("app/node_modules",)
    recorded = _recorded(log)
    assert len(recorded) >= 2, recorded
    assert all(entry.endswith("/app") for entry in recorded), recorded
    assert not any(
        entry.startswith(f"{git_repo.path}/") for entry in recorded
    ), f"a process ran inside the consumer's own checkout: {recorded}"
    # The link is the snapshot's, never the checkout's own directory.
    assert (modules / "marker.txt").exists()
