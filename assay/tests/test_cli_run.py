"""``assay run`` — CLI-level wiring for O1/O2/O3: executes exactly the
declared (plus permitted-appended) argv, resolves the commit via real git,
maps outcomes to exit codes, and writes the verdict artifact only when
``--verdict-json`` is given.

Fixture-level exactness (O4) lives in ``test_runner_run_lane.py`` (the real
pipeline, unit-level) and ``test_standalone.py`` (the installed wheel), both
against the same production functions this CLI path calls; this module
proves the WIRING — argument parsing, the ``--`` append convention, commit
resolution, R1 dispatch through the CLI's own built-in registry, and the
artifact-emission on/off/stdout switch — end to end through
:func:`assay.cli.main`, using a REAL git repository (:func:`conftest.git_repo`)
and a REAL child process throughout (no injected process_runner reaches this
file at all).

**P17: every lane file must be COMMITTED before ``assay run``, not merely
written.** The whole-worktree-clean requirement (work item 3, closing sol
finding 6 for R0 too: "every assay run invocation records HEAD and runs the
live tree regardless of rigor level") means an uncommitted ``assay.toml`` —
the older convention every test in this module used through P16 — now trips
``NO_MEASUREMENT``/``DIRTY_TREE`` before the lane's own command ever runs.
:func:`_write_and_commit_lane` is the one place that changed.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import R0_LANE, R1_LANE, GitRepo, set_key, why_invalid
from jsonschema import Draft202012Validator

from assay.cli import main


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.name != ".git"}


def _write_and_commit_lane(repo: GitRepo, text: str) -> Path:
    path = repo.write("assay.toml", text)
    repo.commit_all("add assay.toml")
    return path


# --- a passing command --------------------------------------------------------


def test_run_executes_a_passing_lane_and_exits_zero(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 0
    assert err == ""
    assert "package: PASS (exit 0)" in out
    assert git_repo.head() in out


def test_run_resolves_the_commit_via_real_git(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    document = json.loads(out)
    assert document["commit"] == git_repo.head()


# --- a failing command ---------------------------------------------------------


def test_run_executes_a_failing_lane_and_exits_one(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 7"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 1
    assert "package: FAIL/COMMAND_FAILED (exit 1)" in out


# --- artifact emission: on/off/stdout (A-028) ---------------------------------


def test_run_without_verdict_json_creates_no_artifact(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)
    before = snapshot(git_repo.path)

    run(["run", "package", "--file", str(path)])

    assert snapshot(git_repo.path) == before


def test_run_writes_the_verdict_atomically_to_the_given_path(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)
    target = tmp_path / "verdict.json"

    code, out, err = run(
        ["run", "package", "--file", str(path), "--verdict-json", str(target)]
    )

    assert code == 0
    assert target.is_file()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["outcome"] == "PASS"
    assert document["lane"] == "package"
    assert list(validator.iter_errors(document)) == []


def test_run_writes_verdict_json_to_stdout_when_dash(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 7"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 1
    document = json.loads(out)  # the WHOLE of stdout must parse as JSON
    assert document["outcome"] == "FAIL"
    assert document["reason_code"] == "COMMAND_FAILED"


def test_run_writes_the_verdict_to_a_file_path_on_a_non_pass_outcome_too(
    git_repo: GitRepo, tmp_path: Path
):
    # O3's own text names PASS, FAIL, ERROR, NO_MEASUREMENT and BUDGET_EXCEEDED
    # explicitly -- a writer (or a CLI gate in front of it) that only writes on
    # success would leave every adverse verdict's artifact absent, which a
    # consumer polling for the file would misread as "assay never ran" rather
    # than "assay ran and failed".
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 7"]')
    path = _write_and_commit_lane(git_repo, lane)
    target = tmp_path / "fail-verdict.json"

    code, out, err = run(
        ["run", "package", "--file", str(path), "--verdict-json", str(target)]
    )

    assert code == 1
    assert target.is_file(), "a non-PASS outcome must still write its artifact"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["outcome"] == "FAIL"


# --- argv append via `--` (A-036, A-095) --------------------------------------


def test_run_rejects_an_append_without_allow_argv_append(git_repo: GitRepo, tmp_path: Path):
    marker = tmp_path / "APPEND_RAN"
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", \'touch "$0"\']')
    lane = set_key(lane, "allow_argv_append", "false")
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--", str(marker)])

    assert code == 2, "ERROR is exit 2"
    assert "package: ERROR/EXEC_FAILED (exit 2)" in out
    assert not marker.exists(), "the process must never have started"


def test_run_permits_an_append_when_allowed(git_repo: GitRepo, tmp_path: Path):
    marker = tmp_path / "APPEND_RAN"
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", \'touch "$0"\']')
    lane = set_key(lane, "allow_argv_append", "true")
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--", str(marker)])

    assert code == 0
    assert marker.exists()


# --- this build evaluates R0 and Python R1 only (P17) --------------------------


def _r1_lane_writing_a_marker(marker: Path) -> str:
    """An R1 lane whose command's ONLY observable effect is creating
    *marker* -- so "did the command run?" is a filesystem fact, not a
    call count on an injected seam this module deliberately never uses."""
    return set_key(R1_LANE, "argv", f'["/bin/sh", "-c", "touch {marker}"]')


def test_run_refuses_a_lane_declaring_r2_without_running_it(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """R2 lands in P18. A-139: the CLI's own registry says Python reaches
    R1 and nothing else, so an R2 lane is refused BEFORE its command runs
    -- and, because HEAD is already resolved by then, as a COMPLETE
    artifact (work item 3's "every later terminal path must emit a
    complete artifact"), never a bare exception.

    Both halves are the point. Before A-139 the registry was consulted for
    the literal ``"R1"`` only, so this lane ran its command to completion
    and was refused afterwards by ``assemble_verdict``, with the side
    effects already committed and nothing for a consumer to read."""
    marker = tmp_path / "the-command-ran"
    lane = set_key(_r1_lane_writing_a_marker(marker), "rigor", '["R0", "R1", "R2"]')
    lane += "\n[lanes.package.judge.mutation]\njobs = 2\noperators = ["
    lane += '"compare-swap"]\n'
    path = _write_and_commit_lane(git_repo, lane)
    for name in ("src", "scripts"):
        (git_repo.path / name).mkdir(exist_ok=True)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists(), "the lane's command ran despite an unreachable rigor"
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert [(c["rigor"], c["status"]) for c in document["claims"]] == [
        ("R0", "ERROR"),
        ("R1", "ERROR"),
        ("R2", "ERROR"),
    ]
    assert document["commit"] == git_repo.head()
    assert "R2" in err


def test_run_refuses_an_unregistered_language_at_r1_with_a_real_artifact(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """Go ships (``adapters/go.py``) but has no producer path wired to any
    rigor level yet (P22) -- the CLI's own built-in registry never
    advertises it, so this is refused BEFORE the lane's command runs, the
    same as an entirely unknown language string would be. A-139: HEAD is
    known at that point, so the refusal is an artifact, not an exception
    that leaves a consumer holding only an exit code."""
    marker = tmp_path / "the-command-ran"
    lane = set_key(_r1_lane_writing_a_marker(marker), "language", '"go"')
    path = _write_and_commit_lane(git_repo, lane)
    for name in ("src", "scripts"):
        (git_repo.path / name).mkdir(exist_ok=True)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert [(c["rigor"], c["status"]) for c in document["claims"]] == [
        ("R0", "ERROR"),
        ("R1", "ERROR"),
    ]
    # P16's iff-invariant: no coverage claim rendered, so no policy recorded.
    assert document.get("judgment") is None
    assert "go" in err


def test_run_refuses_r3_without_r1_declared_at_all(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """The level the pre-A-139 gate could not see: R3 declared with NO R1
    beside it, so the old ``if "R1" in lane.rigor`` guard skipped the
    registry entirely. The refusal must name R3 -- proving the loop reached
    the declared level rather than a hardcoded one."""
    marker = tmp_path / "the-command-ran"
    lane = set_key(R0_LANE, "argv", f'["/bin/sh", "-c", "touch {marker}"]')
    lane = set_key(lane, "rigor", '["R0", "R3"]')
    lane += (
        "\n[lanes.package.judge]\n"
        'language = "python"\nsource_roots = ["src"]\n'
        "\n[lanes.package.judge.canary]\nsite = \"src/mod.py\"\n"
    )
    (git_repo.path / "src").mkdir(exist_ok=True)
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert [(c["rigor"], c["status"]) for c in document["claims"]] == [
        ("R0", "ERROR"),
        ("R3", "ERROR"),
    ]
    assert "R3" in err


def test_run_evaluates_a_real_r1_pass_end_to_end(git_repo: GitRepo, tmp_path: Path):
    """The full CLI wiring for a Python R1 PASS: a real two-commit diff,
    a real (hand-written, not pytest-produced -- that is O1's own job in
    test_standalone.py) coverage-py-json artifact, and a real
    ``git merge-base`` resolving ``judge.base``."""
    (git_repo.path / "src").mkdir()
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write(
        "src/mod.py", "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    )
    git_repo.commit_all("add g")
    # The lane's OWN command writes cov.json when it runs -- work item 4
    # means a PRE-seeded copy would be removed before the command even
    # starts, so this test's PASS only proves anything if the artifact is a
    # real product of THIS invocation, never data smuggled in beforehand.
    cov_json = json.dumps(
        {
            "files": {
                "src/mod.py": {
                    "executed_lines": [1, 2, 4, 5],
                    "missing_lines": [],
                    "excluded_lines": [],
                }
            }
        }
    )
    write_cov = f"cat > cov.json <<'EOF'\n{cov_json}\nEOF"
    lane = f"""\
schema_version = 1

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(write_cov)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = {{ format = "coverage-py-json", artifact = "cov.json" }}
base = "{base_rev}"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"
    assert document["claims"][1]["rigor"] == "R1"
    assert document["claims"][1]["coverage"]["pct"] == 100.0
    assert document["judgment"]["r1"]["base"] == base_rev
    # judgment.r1.source_roots must be the DECLARED relative string ("src"),
    # never judge.source_root_paths' resolved absolute form -- through a
    # real assay.toml load the two genuinely differ, unlike the make_r1_judge
    # test helper (conftest.py), which stores the same absolute string in
    # both fields and so could not catch this the other way.
    assert document["judgment"]["r1"]["source_roots"] == ["src"]


# --- structural failures: no verdict can even be built -----------------------


def test_run_maps_an_unknown_lane_name_to_a_clean_error(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "nonexistent", "--file", str(path)])

    assert code == 2
    assert "no lane named" in err


def test_run_fails_cleanly_when_the_project_root_is_not_a_git_repo(tmp_path: Path):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = tmp_path / "assay.toml"
    path.write_text(lane, encoding="utf-8")

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 2
    assert "ERROR/GIT_FAILED" in err


# --- whole-worktree cleanliness (P17, sol finding 6) --------------------------


def test_run_refuses_a_dirty_tree_even_for_an_r0_only_lane(git_repo: GitRepo):
    """Sol finding 6's own words: "every assay run invocation records HEAD
    and runs the live tree regardless of rigor level." An uncommitted file
    OUTSIDE any declared judge (there is none, this is R0-only) still
    refuses the whole run before the command executes."""
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)
    git_repo.write("uncommitted.txt", "dirty\n")

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 3, "NO_MEASUREMENT is exit 3"
    assert "package: NO_MEASUREMENT/DIRTY_TREE (exit 3)" in out


# --- CLI structure -------------------------------------------------------------


def test_run_requires_a_lane_positional_argument():
    with pytest.raises(SystemExit) as excinfo:
        main(["run"])
    assert excinfo.value.code == 2
