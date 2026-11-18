"""B245 U5a-2 sub-step C: the cargo-mutants directory artifact, end to end
through ``run_lane`` -- the post-run state mapping, the cargo workspace root
and the per-mutant-diff content tier.

The lane's command is a fake process runner that copies a REAL committed
cargo-mutants 27.1.0 ``mutants.out`` (``tests/fixtures/rust/cargo-mutants/<case>``)
into the reserved ``<out>`` directory, retimed into the run's constant clock:
the fixture was captured on another day and the window rule reads the tool's own
``start_time``/``end_time``. The case's ``subject/`` is the committed repository
the snapshot reads, and ``<out>`` sits in a committed ``.gitignore``.

Every terminal a test produces is round-tripped through ``verify_document``:
a runner state that yields a verdict ``verify`` rejects is a bug.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, TESTS_ROOT, completed_process, make_lane

from assay import runner
from assay.adapters.rust import RustAdapter
from assay.config import JudgeConfig, MutationConfig, ResultReportConfig
from assay.errors import Outcome, ReasonCode
from assay.verify import verify_document

CASES = TESTS_ROOT / "fixtures" / "rust" / "cargo-mutants"
FORMAT = "cargo-mutants-outcomes"
MOMENT = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
OUT = "out"


def _stamp(seconds: int) -> str:
    moment = MOMENT + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def _stage(
    tmp_path: Path,
    case: str,
    *,
    label: str | None = None,
    window: tuple[int, int] = (-1, 1),
    drop_timeout: bool = False,
) -> Path:
    """A retimed COPY of a case's ``mutants.out`` (the fixture is never edited)."""
    staged = tmp_path / "staged" / (label or case) / "mutants.out"
    shutil.copytree(CASES / case / "mutants.out", staged)
    report = staged / "outcomes.json"
    document = json.loads(report.read_text(encoding="utf-8"))
    document["start_time"], document["end_time"] = (_stamp(window[0]), _stamp(window[1]))
    if drop_timeout:
        kept = []
        for outcome in document["outcomes"]:
            if outcome["summary"] == "Timeout":
                (staged / outcome["diff_path"]).unlink()
            else:
                kept.append(outcome)
        document["outcomes"] = kept
        document["total_mutants"] -= 1
        document["timeout"] = 0
    report.write_text(json.dumps(document), encoding="utf-8")
    return staged


def _exit_code(case: str) -> int:
    return int((CASES / case / "exit-code.txt").read_text(encoding="utf-8").strip())


def _seed(git_repo: GitRepo, case: str, *, prefix: str = "") -> str:
    """Commit the case's subject under *prefix*; returns the base revision
    (the commit before it), so every line of the subject is an added line."""
    git_repo.write(".gitignore", f"{OUT}\n")
    git_repo.commit_all("gitignore the output directory")
    base = git_repo.head()
    subject = CASES / case / "subject"
    for source in sorted(p for p in subject.rglob("*") if p.is_file()):
        target = git_repo.path / prefix / source.relative_to(subject)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    git_repo.commit_all("add the subject")
    return base


def _lane(
    git_repo: GitRepo,
    base: str,
    *,
    prefix: str = "",
    source_roots: tuple[str, ...] | None = None,
):
    roots = source_roots or (f"{prefix}/src" if prefix else "src",)
    artifact = f"{prefix}/{OUT}" if prefix else OUT
    judge = JudgeConfig(
        language="rust",
        source_roots=roots,
        source_root_paths=tuple((git_repo.path / root).resolve() for root in roots),
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=MutationConfig(format=FORMAT, artifact=artifact, fail_under=100.0),
        canary=None,
        base=base,
        mode="changed_lines",
        targets=None,
    )
    return make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        cwd=prefix or None,
        argv=("cargo", "mutants", "--output", OUT),
        result_report=ResultReportConfig(format=FORMAT, path=OUT),
    )


class Fake:
    """The lane's command: copies a staged ``mutants.out`` into ``<out>``."""

    def __init__(self, staged: Path | None, exit_code: int, *, before=None, after=None):
        self.staged = staged
        self.exit_code = exit_code
        self.before = before
        self.after = after
        self.calls = 0

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls += 1
        cwd = Path(cwd)
        if self.before is not None:
            self.before(cwd)
        if self.staged is not None:
            shutil.copytree(self.staged, cwd / OUT / "mutants.out")
        if self.after is not None:
            self.after(cwd)
        return completed_process(argv, self.exit_code, stdout="", stderr="")


def _run(git_repo: GitRepo, lane, fake):
    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=RustAdapter(),
        assay_version="0.1.0",
        process_runner=fake,
        clock=lambda: MOMENT,
    )
    assert verify_document(verdict.to_dict()) == []
    return verdict


def _claim(verdict, rigor: str):
    return next(c for c in verdict.claims if c.rigor == rigor)


def _case(git_repo: GitRepo, tmp_path: Path, case: str, **stage):
    base = _seed(git_repo, case)
    return _lane(git_repo, base), Fake(_stage(tmp_path, case, **stage), _exit_code(case))


# --- the real cases, end to end ------------------------------------------------


def test_a_timeout_outranks_survivors(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _case(git_repo, tmp_path, "full")

    verdict = _run(git_repo, lane, fake)

    assert _claim(verdict, "R0").status is Outcome.PASS
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.BUDGET_EXCEEDED
    assert r2.reason_code is ReasonCode.LANE_TIMEOUT
    assert verdict.outcome is Outcome.BUDGET_EXCEEDED
    assert fake.calls == 1


def test_without_the_timeout_the_survivors_fail_the_floor(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _case(git_repo, tmp_path, "full", label="full-no-timeout", drop_timeout=True)

    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.FAIL
    assert r2.reason_code is ReasonCode.MUTANTS_SURVIVED
    assert verdict.outcome is Outcome.FAIL


@pytest.mark.parametrize("case", ["indiff", "paths", "methods", "unicode"])
def test_the_real_cases_are_judged_and_verify(git_repo: GitRepo, tmp_path: Path, case: str):
    lane, fake = _case(git_repo, tmp_path, case)

    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    if case in ("indiff", "paths"):
        assert r2.status is Outcome.FAIL
        assert r2.reason_code is ReasonCode.MUTANTS_SURVIVED
    else:
        assert r2.status is Outcome.PASS


@pytest.mark.parametrize("case", ["basefail-test", "basefail-build"])
def test_a_failed_baseline_fails_r0_decided_by_the_report(
    git_repo: GitRepo, tmp_path: Path, case: str
):
    lane, fake = _case(git_repo, tmp_path, case)

    verdict = _run(git_repo, lane, fake)

    r0 = _claim(verdict, "R0")
    assert r0.status is Outcome.FAIL
    assert verdict.outcome is Outcome.FAIL
