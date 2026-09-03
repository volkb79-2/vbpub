"""B049/A-408 -- a lane's own tool that deletes and recreates its output
directory is NAMED, never folded into "your command wrote nothing".

The guard lives in exactly one place, :meth:`assay.safeio.OutputReservation.
consume` (``_refuse_if_parent_was_replaced``), so every consumer of a
reservation inherits it by construction rather than by repetition. The five
shipped call sites are ``runner.py``'s coverage read, its SQL-R2
``equivalence_artifact`` read and its ingested-mutation-report read, and
``mutation.py``'s per-mutant equivalence and kill-signal reads.

This module proves the rule at three levels, deliberately: the seam itself
(``consume``), the coverage read end to end through ``run_lane`` with a real
directory-recreating fake tool (B049's own asked-for regression test -- no
Vitest, no JavaScript, the mechanism is language-free), and
``assay.mutation``'s absent-read path, which is the site whose fold is
WORST: an absent read there is classified ``crashed`` for a mutant whose
command ran perfectly.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import FakeAdapter, GitRepo, fixed_clock, make_lane, make_r1_judge

from assay import mutation, runner, safeio
from assay.errors import AssayError, Outcome, ReasonCode

ADAPTER = FakeAdapter()
LIMIT = 65536

MOMENT_A = datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 9, 2, 9, 0, 1, tzinfo=timezone.utc)
MOMENT_C = datetime(2026, 9, 2, 9, 0, 2, tzinfo=timezone.utc)


def _replace_directory(directory: Path, *, then_write: Path, payload: str) -> None:
    """The 15-line fake tool, in Python: exactly what Vitest's default
    ``coverage.clean = true`` does, and what any ``rm -rf out && mkdir out``
    build step does -- remove the whole output directory, recreate a
    same-named one, and write a COMPLETE artifact into it. The artifact is
    genuinely on disk at the declared path when this returns; only the
    inode assay reserved is gone."""
    shutil.rmtree(directory)
    directory.mkdir()
    then_write.write_text(payload, encoding="utf-8")


# --- the seam itself ----------------------------------------------------------


def test_consume_refuses_when_the_held_parent_directory_was_replaced(tmp_path: Path):
    out = tmp_path / "reports"
    out.mkdir()
    reservation = safeio.reserve_output(tmp_path, "reports/coverage-final.json", limit=LIMIT)
    reservation.arm()

    _replace_directory(out, then_write=out / "coverage-final.json", payload="{}")
    assert (out / "coverage-final.json").read_text(encoding="utf-8") == "{}", (
        "precondition: the artifact really is complete on disk at the declared path"
    )

    with pytest.raises(AssayError) as excinfo:
        reservation.consume()
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    message = str(excinfo.value)
    assert "reports/coverage-final.json" in message
    assert "'reports'" in message, "the message names the directory that was replaced"
    assert "deleted and recreated" in message
    assert "clean" in message, "the message prescribes the fix"


def test_consume_still_reads_an_artifact_written_into_the_reserved_directory(tmp_path: Path):
    """The legitimate state, constructed before the refusal ships
    (AGENTS.md's "a refusal that blocks work must have its legitimate state
    constructed"): a tool that writes into the directory assay reserved --
    the overwhelmingly common case -- is unaffected."""
    out = tmp_path / "reports"
    out.mkdir()
    reservation = safeio.reserve_output(tmp_path, "reports/coverage-final.json", limit=LIMIT)
    reservation.arm()
    (out / "coverage-final.json").write_text("{}", encoding="utf-8")

    assert reservation.consume() == b"{}"


def test_consume_still_reports_a_genuinely_absent_artifact_as_absent(tmp_path: Path):
    """The other direction of the same check: an intact reserved directory
    with nothing written into it is still the truthful ``None`` P20/A-174
    defines, never the new refusal. Without this the guard would be a
    superset refusal (AGENTS.md anti-pattern 4)."""
    out = tmp_path / "reports"
    out.mkdir()
    reservation = safeio.reserve_output(tmp_path, "reports/coverage-final.json", limit=LIMIT)
    reservation.arm()

    assert reservation.consume() is None


def test_a_top_level_artifact_whose_project_root_was_replaced_names_the_root(tmp_path: Path):
    """``dirname`` of a top-level artifact is empty; the message must still
    read as a sentence rather than naming ``''``."""
    root = tmp_path / "project"
    root.mkdir()
    reservation = safeio.reserve_output(root, "cov.json", limit=LIMIT)
    reservation.arm()

    _replace_directory(root, then_write=root / "cov.json", payload="{}")

    with pytest.raises(AssayError) as excinfo:
        reservation.consume()
    assert "the project root" in str(excinfo.value)


# --- site 1: the coverage read, end to end through the real pipeline -----------


def _seed(repo: GitRepo) -> tuple[str, str]:
    repo.write(".gitignore", "reports/\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = repo.commit_all("head")
    return base_rev, head_rev


_COVERAGE_PAYLOAD = (
    '{"files": {"pkg/mod.zzz": {"executed_lines": [1, 2], '
    '"missing_lines": [], "excluded_lines": []}}}'
)


def test_run_lane_r1_names_the_replaced_directory_instead_of_reporting_no_coverage(
    git_repo: GitRepo,
):
    """B049's headline: before this fix the identical lane reported
    ``NO_MEASUREMENT``/``EMPTY_COVERAGE`` with a message asserting "the
    lane's command exited without writing the declared artifact at all" --
    over a complete, correctly-keyed artifact that was on disk at the
    declared path the whole time."""
    base_rev, head_rev = _seed(git_repo)
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        base=base_rev,
        coverage_artifact="reports/cov.json",
    )
    # The fake tool, as a real command: remove the directory assay reserved,
    # recreate it, write a complete artifact into the new one.
    script = (
        "rm -rf reports && mkdir reports && cat > reports/cov.json <<'EOF'\n"
        f"{_COVERAGE_PAYLOAD}\nEOF"
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", script))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.PASS, "R0 really ran and really passed"
    r1 = verdict.claims[1]
    assert r1.status is Outcome.ERROR
    assert r1.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r1.reason_code is not ReasonCode.EMPTY_COVERAGE


def test_run_lane_r1_passes_when_the_same_tool_writes_into_the_reserved_directory(
    git_repo: GitRepo,
):
    """The control: identical lane, identical payload, no ``rm -rf`` -- so
    the refusal above is caused by the directory replacement and by nothing
    else about this shape."""
    base_rev, head_rev = _seed(git_repo)
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        base=base_rev,
        coverage_artifact="reports/cov.json",
    )
    script = f"cat > reports/cov.json <<'EOF'\n{_COVERAGE_PAYLOAD}\nEOF"
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", script))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[1].status is Outcome.PASS
    assert verdict.claims[1].coverage.pct == 100.0


# --- site 3: mutation.py's absent-read -> `crashed` fold -----------------------


def test_read_kill_signal_refuses_a_replaced_directory_instead_of_folding_to_crashed(
    tmp_path: Path,
):
    """``_read_kill_signal`` returns ``None`` for a genuinely absent
    artifact, and the caller reclassifies that mutant to ``crashed``
    (A-223e). A directory-recreating step inside a lane's own mutation
    tooling would therefore report every mutant as CRASHED for commands
    that ran perfectly. With the guard, the read refuses and the whole
    claim stops on a named cause."""
    out = tmp_path / "signals"
    out.mkdir()
    reservation = safeio.reserve_output(tmp_path, "signals/kill.txt", limit=LIMIT)
    reservation.arm()

    _replace_directory(out, then_write=out / "kill.txt", payload="assertion\n")

    with pytest.raises(AssayError) as excinfo:
        mutation._read_kill_signal(reservation)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "deleted and recreated" in str(excinfo.value)


def test_read_kill_signal_still_returns_none_for_a_genuinely_absent_signal(tmp_path: Path):
    out = tmp_path / "signals"
    out.mkdir()
    reservation = safeio.reserve_output(tmp_path, "signals/kill.txt", limit=LIMIT)
    reservation.arm()

    assert mutation._read_kill_signal(reservation) is None
