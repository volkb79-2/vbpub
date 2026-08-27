from __future__ import annotations

import io
import json
import math
import re
import time
from pathlib import Path

from conftest import GitRepo

from assay.cli import main


_LANE = """\
schema_version = 2

[lanes.real]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false
{probe}
"""


def _run(
    repo: GitRepo,
    tmp_path: Path,
    *,
    probe: str,
    budget: str = "1m",
    stderr: "io.StringIO | None" = None,
) -> tuple[int, dict]:
    repo.write(".gitignore", "*.json\nciu.global.toml\n")
    lane = _LANE.format(probe=probe).replace('budget = "1m"', f'budget = "{budget}"')
    repo.write("assay.toml", lane)
    repo.commit_all("lane")
    target = tmp_path / "verdict.json"
    code = main(
        ["run", "real", "--file", str(repo.path / "assay.toml"), "--verdict-json", str(target)],
        stderr=stderr,
    )
    return code, json.loads(target.read_text(encoding="utf-8"))


def test_a_passing_environment_probe_allows_the_lane(git_repo: GitRepo, tmp_path):
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/bin/sh", "-c", "exit 0"]',
    )

    assert (code, document["outcome"]) == (0, "PASS")


def test_a_failing_environment_probe_refuses_before_the_lane(git_repo: GitRepo, tmp_path):
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/bin/sh", "-c", "exit 7"]',
    )

    assert (code, document["outcome"], document["reason_code"]) == (
        2,
        "ERROR",
        "BAD_LANE_CONFIG",
    )


def test_environment_probe_runs_normally_with_a_resolvable_infrastructure_fact(
    git_repo: GitRepo, tmp_path
):
    """(B025 round 2, N-W1 follow-up) Round 2 review found the probe's plan
    resolution never forwarding `infrastructure_source`/`infrastructure_
    environment` at all was fatal even for a RESOLVABLE `derived:` fact --
    not just the unresolvable case the sibling test below covers, which
    would have refused either way and so could not by itself prove the
    forward actually happened. `ciu.global.toml` is gitignored (`_run`'s own
    `.gitignore`), matching real ciu usage, so its presence does not trip
    the pre-run dirty-tree check."""
    (git_repo.path / "ciu.global.toml").write_text(
        "[deploy]\nimage = 'postgres:18'\n", encoding="utf-8"
    )
    code, document = _run(
        git_repo,
        tmp_path,
        probe=(
            'environment_command = ["/bin/sh", "-c", "exit 0"]\n\n'
            "[lanes.real.infrastructure]\n"
            'image = "derived:deploy.image"\n'
        ),
    )

    assert (code, document["outcome"]) == (0, "PASS")


def test_environment_probe_refuses_cleanly_when_infrastructure_is_unresolvable(
    git_repo: GitRepo, tmp_path
):
    """(B025 round 2, N-W1) The probe's own plan resolution never forwarded
    `infrastructure_source`/`infrastructure_environment` at all -- a lane
    pairing `environment_command` with a `derived:` fact crashed
    unconditionally, resolvable or not, unlike every other call site in this
    module. Now forwarded, and wrapped in the same refuse-cleanly pattern."""
    code, document = _run(
        git_repo,
        tmp_path,
        probe=(
            'environment_command = ["/bin/sh", "-c", "exit 0"]\n\n'
            "[lanes.real.infrastructure]\n"
            'image = "derived:deploy.image"\n'
        ),
    )

    assert (code, document["outcome"], document["reason_code"]) == (
        2,
        "ERROR",
        "BAD_LANE_CONFIG",
    )
    assert document["env_effective_incomplete"] is True


def test_a_probe_that_exhausts_its_budget_reports_a_timeout_not_a_config_error(
    git_repo: GitRepo, tmp_path
):
    """B032/A-321. `8a2a4731` discarded `execute_plan`'s own classification
    and hardcoded `ERROR`/`BAD_LANE_CONFIG` for EVERY probe failure, so a
    probe that genuinely exhausted its budget -- which `execute_plan`
    classifies correctly as `BUDGET_EXCEEDED`/`LANE_TIMEOUT` -- was recorded
    as an operator config error at exit 2 instead of exit 4.

    A gate that retries `BUDGET_EXCEEDED` but hard-fails `BAD_LANE_CONFIG`
    (the estate's own run-gate shape) then does exactly the wrong thing on a
    real timeout. This is the one distinction A-321 rules must not collapse.

    Reproduced through the installed CLI before the fix:
      `budget = "30s"`, `environment_command = sleep 45`
      -> `ERROR`/`BAD_LANE_CONFIG`, exit 2.

    Round-2 review (blocker 1): this is also the exact fixture that exposed
    `_report_probe_refusal` hardcoding the fixed 30s `PROBE_BUDGET_SECONDS`
    into the rendered message even when the LANE's own remaining budget (2s
    here) was the tighter, actually-enforced bound -- a false claim about
    which cap fired. A real subprocess is driven end to end (not a source
    grep, see the sibling test below): the wall-clock elapsed time proves
    the process was killed near the 2s lane budget, nowhere near the 30s
    cap, and the message's own rendered number must track that.
    """
    err = io.StringIO()
    started = time.monotonic()
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/bin/sh", "-c", "sleep 45"]',
        budget="2s",
        stderr=err,
    )
    elapsed = time.monotonic() - started

    assert (code, document["outcome"], document["reason_code"]) == (
        4,
        "BUDGET_EXCEEDED",
        "LANE_TIMEOUT",
    )
    assert "LANE_TIMEOUT" in err.getvalue()
    assert "the lane's own command never started" in err.getvalue()

    # Behavioral proof, not a cosmetic one: killed near the 2s lane budget,
    # nowhere near the 45s sleep or the 30s probe cap.
    assert elapsed < 15.0, f"probe ran {elapsed:.1f}s -- the 2s lane budget did not bind"

    match = re.search(r"within its ([0-9.]+)s preflight window", err.getvalue())
    assert match, f"no preflight-window number rendered: {err.getvalue()!r}"
    rendered = float(match.group(1))
    # The lane's remaining budget (~2s), NOT the fixed 30s probe cap -- a
    # tolerance is required because `deadline.remaining()` is measured wall
    # time, minus whatever setup overhead ran before the probe started.
    assert rendered < 30.0, f"rendered {rendered}s -- looks like the fixed cap, not the budget"
    assert math.isclose(rendered, 2.0, abs_tol=1.0), f"rendered {rendered}s, expected ~2s"
    assert "the 30s probe cap" in err.getvalue()


def test_a_probe_refusal_writes_b010s_clear_message_to_stderr(
    git_repo: GitRepo, tmp_path
):
    """B032/A-322. B010's ask, verbatim: refuse with "this lane's declared
    environment does not match the invoking one; run via `<declared
    wrapper>`" *instead of surfacing the suite's raw traceback*.

    What `8a2a4731` shipped wrote **0 bytes** to stderr (measured) and
    emitted a generic `BAD_LANE_CONFIG` whose `argv_effective` names the
    LANE's own command -- which never ran -- so the message actively
    misleads. The verdict stays as closed as it was (no free-text field,
    A-138/A-170); the diagnosis goes to the caller's stream.
    """
    err = io.StringIO()
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/bin/sh", "-c", "exit 7"]',
        stderr=err,
    )

    assert (code, document["outcome"], document["reason_code"]) == (
        2,
        "ERROR",
        "BAD_LANE_CONFIG",
    )
    message = err.getvalue()
    assert message, "B010's entire ask was a clear message; 0 bytes is not one"
    assert "declared environment does not match the invoking one" in message
    assert "the probe exited 7" in message
    # The DECLARED WRAPPER, not the lane argv the old refusal pointed at.
    assert "Run via the declared wrapper: /bin/sh -c 'exit 7'" in message


def test_a_probe_that_cannot_be_executed_says_so_and_stays_bad_lane_config(
    git_repo: GitRepo, tmp_path
):
    """A-321's collapse half: a missing binary (`ERROR`/`EXEC_FAILED` from
    `execute_plan`) keeps rendering as `ERROR`/`BAD_LANE_CONFIG` -- it means
    the same actionable thing to a consumer as a nonzero exit -- but what
    separates the two is now carried as text, not by widening the closed
    reason-code vocabulary."""
    err = io.StringIO()
    code, document = _run(
        git_repo,
        tmp_path,
        probe='environment_command = ["/nonexistent/probe-binary"]',
        stderr=err,
    )

    assert (code, document["outcome"], document["reason_code"]) == (
        2,
        "ERROR",
        "BAD_LANE_CONFIG",
    )
    assert "the probe command could not be executed" in err.getvalue()


def test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it(
    git_repo: GitRepo, tmp_path, monkeypatch
):
    """B032/A-322's dead-cap half, driven behaviorally (round-2 review N1).

    `runner.py` used to set `budget_seconds=min(30.0, deadline.remaining())`
    on the probe plan and then pass `timeout=deadline.remaining()` -- the
    FULL lane budget -- to `execute_plan`, which reads its `timeout=`
    ARGUMENT and ignores `plan.budget_seconds` entirely. Measured on `main`:
    an `environment_command` of `sleep 45` under `budget = "5m"` ran the
    whole 45s and then PASSED.

    Round-1's fix test proved this at the seam by grepping `runner.py`'s
    source text for the three literals that make both values come from the
    same expression -- a text oracle, green on a fix that is correct in
    text but wrong in effect (the exact failure shape B032's audit report
    called out). This drives a REAL subprocess instead: `PROBE_BUDGET_
    SECONDS` is patched down so the CAP -- not the lane's own much larger
    remaining budget -- is unambiguously the binding constraint, and the
    real elapsed wall-clock time and exit code are asserted, not just the
    rendered message.
    """
    from assay import runner

    assert runner.PROBE_BUDGET_SECONDS == 30.0  # the real, unpatched default
    monkeypatch.setattr(runner, "PROBE_BUDGET_SECONDS", 3.0)

    err = io.StringIO()
    started = time.monotonic()
    code, document = _run(
        git_repo,
        tmp_path,
        # Sleeps well past the 3s (patched) cap; the lane budget below is
        # nearly 7x that, so ONLY the cap can be what stops this.
        probe='environment_command = ["/bin/sh", "-c", "sleep 20"]',
        budget="20s",
        stderr=err,
    )
    elapsed = time.monotonic() - started

    assert (code, document["outcome"], document["reason_code"]) == (
        4,
        "BUDGET_EXCEEDED",
        "LANE_TIMEOUT",
    )
    # Killed near the 3s cap, nowhere near the 20s sleep or the 20s lane
    # budget -- proves the cap (not the budget, not the sleep) fired.
    assert elapsed < 12.0, f"probe ran {elapsed:.1f}s -- the 3s cap did not bind"

    message = err.getvalue()
    # remaining() at probe time is comfortably > 3.0 (20s budget, one probe
    # dispatch's worth of setup), so min(3.0, remaining) is EXACTLY 3.0 --
    # unlike the budget-bound case, no float-tolerance is needed here.
    assert "within its 3s preflight window" in message, message
    assert "the 3s probe cap" in message, message


_R2_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.unit.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
"""


def _r2_repo(repo: GitRepo) -> None:
    repo.write("assay.toml", _R2_LANE)
    repo.write("pkg/flags.py", "a = True\n")
    repo.commit_all("lane")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", "a = False\n")
    repo.commit_all("base flag")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", "a = True\n")
    repo.commit_all("restore flag")


def test_an_r2_lane_run_twice_with_nothing_changed_passes_both_times(
    git_repo: GitRepo, tmp_path
):
    """B031/A-320's headline regression, through the installed CLI.

    `8a2a4731` wrote `.assay/<lane>.progress.jsonl` unconditionally for every
    R2 lane into `Path(".assay")` -- the CONSUMER's live worktree. Measured on
    `main`: run 1 PASSes and leaves `.assay/unit.progress.jsonl` untracked;
    run 2, with the consumer having changed nothing at all, fails
    `NO_MEASUREMENT`/`DIRTY_TREE` because `git.dirty_paths()` now returns that
    path. An R2 lane passed once and then refused forever.

    Progress is opt-in and consumer-directed now, so the default writes
    nothing and the lane's own second run is clean.
    """
    _r2_repo(git_repo)
    lane_file = str(git_repo.path / "assay.toml")

    first = main(["run", "unit", "--file", lane_file])
    assert not (git_repo.path / ".assay").exists(), (
        "assay must not create anything in the consumer's live worktree"
    )
    assert git_repo.git("status", "--porcelain").strip() == ""

    second = main(["run", "unit", "--file", lane_file])
    assert first == second, (first, second)
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_progress_goes_exactly_where_the_consumer_asked_and_nowhere_else(
    git_repo: GitRepo, tmp_path
):
    """B031/A-320: `--progress PATH` is the only way a progress artifact is
    written, and PATH is the consumer's, resolved against the invoking CWD --
    never `.assay/<lane>.progress.jsonl` derived by interpolating an
    unvalidated lane name (a lane named `"../../../pwned/esc"` is a legal
    quoted TOML key, and that spelling wrote NDJSON three directories above
    the project root while still reporting PASS)."""
    _r2_repo(git_repo)
    destination = tmp_path / "elsewhere" / "progress.jsonl"

    main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--progress",
            str(destination),
        ]
    )

    assert not (git_repo.path / ".assay").exists()
    assert git_repo.git("status", "--porcelain").strip() == ""
    events = [
        json.loads(line)
        for line in destination.read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["event"] == "run"
    assert len(events[0]["commit"]) == 40
    assert [event["event"] for event in events[:2]] == ["run", "baseline"]


def test_a_progress_destination_that_is_a_directory_refuses_before_any_repository_work(
    git_repo: GitRepo, tmp_path
):
    """B031/A-320 round 2, blocker 2.

    `$ assay run nested --progress /an/existing/directory` used to run the
    ENTIRE lane and only then surface `ERROR`/`GIT_FAILED` -- a cause with
    nothing to do with what actually happened (the `IsADirectoryError` from
    `progress_writer`'s `path.open("a")`, deep inside R2 execution, escaping
    into `run_lane`'s broad `except OSError:`). A-320 claims `--progress`
    behaves "exactly like `--verdict-json`'s" destination handling; the CLI
    now validates it at the SAME OUTPUT-RESERVATION step, before HEAD is
    even resolved, and gives the same typed `ERROR`/`OUTPUT_WRITE_FAILED`
    `--verdict-json` gives for the identical mistake against a directory.
    """
    _r2_repo(git_repo)
    existing_directory = tmp_path / "already-here"
    existing_directory.mkdir()

    err = io.StringIO()
    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--progress",
            str(existing_directory),
        ],
        stderr=err,
    )

    assert code == 2
    message = err.getvalue()
    assert "OUTPUT_WRITE_FAILED" in message
    assert "GIT_FAILED" not in message
    assert str(existing_directory) in message
    assert "not an ordinary regular file" in message
    # Never got as far as writing anything to the destination -- refused
    # before the lane's command, or even HEAD resolution, ran at all.
    assert list(existing_directory.iterdir()) == []


def test_an_empty_progress_destination_refuses_cleanly_not_as_zero_bytes_of_silence(
    git_repo: GitRepo, tmp_path
):
    """B031/A-320 round 2, blocker 2.

    `$ assay run nested --progress ""` resolved to `.`, the invoking CWD --
    itself an existing directory -- and produced ZERO bytes of diagnosis
    (measured) before this fix: a bare `ERROR`/`GIT_FAILED` with no message
    at all reaching the operator. It is now refused with the same clear,
    named cause as any other bad `--progress` destination.
    """
    _r2_repo(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--progress",
            "",
        ],
        stderr=err,
    )

    assert code == 2
    message = err.getvalue()
    assert message, "an empty --progress destination must not be zero bytes of diagnosis"
    assert "OUTPUT_WRITE_FAILED" in message
    assert "GIT_FAILED" not in message


def test_a_progress_destination_whose_parent_does_not_yet_exist_is_still_created(
    git_repo: GitRepo, tmp_path
):
    """The early `validate_progress_destination` preflight (blocker 2's fix)
    must not regress the documented, pre-existing behavior that
    `progress_writer` creates missing parent directories on demand -- unlike
    `--verdict-json`, which requires its parent to already exist. A
    destination that does not exist YET is not a mistake; only an existing
    NON-regular destination is.
    """
    _r2_repo(git_repo)
    destination = tmp_path / "not" / "yet" / "created" / "progress.jsonl"
    assert not destination.parent.exists()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--progress",
            str(destination),
        ]
    )

    assert code in (0, 1)  # PASS or FAIL are both fine; only refusal is wrong here
    assert destination.exists()
    assert destination.read_text(encoding="utf-8").strip() != ""
