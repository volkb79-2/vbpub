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
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import (
    GitRepo,
    TESTS_ROOT,
    completed_process,
    make_lane,
    prepared_snapshot,
    timeout_expired,
)

from assay import runner, safeio
from assay.adapters.rust import RustAdapter
from assay.config import JudgeConfig, MutationConfig, ResultReportConfig
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
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


def _run(git_repo: GitRepo, lane, fake, *, clock=lambda: MOMENT):
    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=RustAdapter(),
        assay_version="0.1.0",
        process_runner=fake,
        clock=clock,
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


def test_a_crashed_package_is_an_execution_failure(git_repo: GitRepo, tmp_path: Path):
    base = _seed(git_repo, "crashed-f4")
    lane = _lane(git_repo, base, source_roots=("a/src", "b/src"))
    fake = Fake(_stage(tmp_path, "crashed-f4"), _exit_code("crashed-f4"))

    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is ReasonCode.EXEC_FAILED


def test_a_workspace_root_below_the_git_top_is_the_anchor(git_repo: GitRepo, tmp_path: Path):
    prefix = "sub/crate1"
    base = _seed(git_repo, "subdir", prefix=prefix)
    lane = _lane(git_repo, base, prefix=prefix)
    fake = Fake(_stage(tmp_path, "subdir"), _exit_code("subdir"))

    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.FAIL
    assert r2.reason_code is ReasonCode.MUTANTS_SURVIVED


# --- refusals: every one is an ERROR that verify accepts -----------------------


def _refused(verdict, reason: ReasonCode = ReasonCode.UNREADABLE_ARTIFACT):
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is reason
    return r2


def test_a_non_empty_out_at_reservation_is_refused(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    real = safeio.reserve_directory_output

    def littered(root, artifact, **kwargs):
        (Path(root) / artifact).mkdir(parents=True, exist_ok=True)
        (Path(root) / artifact / "junk.txt").write_text("x", encoding="utf-8")
        return real(root, artifact, **kwargs)

    monkeypatch.setattr(runner.safeio, "reserve_directory_output", littered)
    lane, fake = _case(git_repo, tmp_path, "indiff")

    verdict = _run(git_repo, lane, fake)

    _refused(verdict)
    assert fake.calls == 0


def test_a_tracked_out_is_refused_before_the_command_runs(git_repo: GitRepo, tmp_path: Path):
    # The materialised tracked file makes `<out>` non-empty, which the
    # reservation refuses before the tracked-path check is reached (that
    # BAD_LANE_CONFIG branch is pinned in test_runner_cargo_mutants_wiring.py).
    git_repo.write(".gitignore", "")
    git_repo.write(f"{OUT}/keep.txt", "tracked\n")
    git_repo.commit_all("track the output directory")
    base = git_repo.head()
    git_repo.write("src/lib.rs", "pub fn f() {}\n")
    git_repo.write("Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    git_repo.commit_all("subject")
    lane = _lane(git_repo, base)
    fake = Fake(_stage(tmp_path, "indiff"), 0)

    verdict = _run(git_repo, lane, fake)

    _refused(verdict)
    assert fake.calls == 0


def test_a_stale_rotated_directory_is_refused(git_repo: GitRepo, tmp_path: Path):
    def rotated(cwd: Path) -> None:
        (cwd / OUT / "mutants.out.old").mkdir()

    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)
    fake = Fake(_stage(tmp_path, "indiff"), 0, before=rotated)

    _refused(_run(git_repo, lane, fake))


def test_a_report_outside_the_command_window_is_refused(git_repo: GitRepo, tmp_path: Path):
    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)
    fake = Fake(_stage(tmp_path, "indiff", window=(-100, -50)), 0)

    _refused(_run(git_repo, lane, fake))


def test_an_out_replaced_by_a_symlink_is_refused(git_repo: GitRepo, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"

    def symlinked(cwd: Path) -> None:
        shutil.copytree(_stage(tmp_path, "indiff", label="linked"), elsewhere / "mutants.out")
        shutil.rmtree(cwd / OUT)
        os.symlink(elsewhere, cwd / OUT)

    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)
    fake = Fake(None, 0, before=symlinked)

    _refused(_run(git_repo, lane, fake))


def test_an_out_renamed_away_and_recreated_is_refused(git_repo: GitRepo, tmp_path: Path):
    def renamed(cwd: Path) -> None:
        os.rename(cwd / OUT, cwd / "moved")
        (cwd / OUT).mkdir()

    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)
    fake = Fake(_stage(tmp_path, "indiff"), 0, before=renamed)

    _refused(_run(git_repo, lane, fake))


def test_a_tampered_member_diff_is_refused(git_repo: GitRepo, tmp_path: Path):
    staged = _stage(tmp_path, "indiff")
    victim = next((staged / "diff").iterdir())
    victim.write_text(
        victim.read_text(encoding="utf-8").replace("n % 2", "n % 3"), encoding="utf-8"
    )
    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)

    _refused(_run(git_repo, lane, Fake(staged, 0)))


def test_an_absent_report_with_exit_zero_never_passes(git_repo: GitRepo, tmp_path: Path):
    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)

    # Until U5c synthesises the zero-mutant INCONCLUSIVE/NO_MUTANTS, an absent
    # report fails closed as an unreadable artifact (which `verify` accepts).
    verdict = _run(git_repo, lane, Fake(None, 0))

    _refused(verdict)
    assert verdict.outcome is not Outcome.PASS


def test_the_directory_is_consumed_exactly_once(git_repo: GitRepo, tmp_path: Path, monkeypatch):
    real = safeio.DirectoryArtifactReservation.consume
    calls: list[int] = []

    def spy(self, **kwargs):
        calls.append(1)
        return real(self, **kwargs)

    monkeypatch.setattr(safeio.DirectoryArtifactReservation, "consume", spy)
    lane, fake = _case(git_repo, tmp_path, "indiff")

    _run(git_repo, lane, fake)

    assert calls == [1]


def test_a_timed_out_run_is_budget_exceeded_and_never_consumed(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    real = safeio.DirectoryArtifactReservation.consume
    calls: list[int] = []
    monkeypatch.setattr(
        safeio.DirectoryArtifactReservation,
        "consume",
        lambda self, **kw: calls.append(1) or real(self, **kw),
    )

    def times_out(argv, *, env, cwd, timeout):
        raise timeout_expired(argv, timeout)

    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)

    verdict = _run(git_repo, lane, times_out)

    assert verdict.outcome is not Outcome.PASS
    assert calls == []


# --- _directory_report_error, branch by branch ---------------------------------


def _present(unusable: str | None) -> runner._DirectoryPresent:
    artifact = safeio.DirectoryArtifact(
        artifact=f"{OUT}/mutants.out", identity=(1, 2), report=b"{}", members={}
    )
    return runner._DirectoryPresent(artifact=artifact, summary=None, unusable=unusable)


def test_a_refusal_is_returned_as_the_very_error():
    refusal = AssayError("no", outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT)

    assert runner._directory_report_error(runner._DirectoryRefused(error=refusal), OUT) is refusal


def test_an_absent_directory_fails_closed_as_unreadable_until_u5c():
    error = runner._directory_report_error(runner._DirectoryAbsent(), OUT)

    assert error is not None
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert repr(OUT) in str(error)
    assert "no outcomes.json" in str(error)


def test_a_directory_never_consumed_is_an_execution_failure():
    error = runner._directory_report_error(runner._DirectoryNotConsumed(), OUT)

    assert error is not None
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.EXEC_FAILED


def test_an_unusable_present_report_is_unreadable():
    error = runner._directory_report_error(_present("bad shape"), OUT)

    assert error is not None
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "bad shape" in str(error)


def test_a_usable_present_report_has_no_error():
    assert runner._directory_report_error(_present(None), OUT) is None


# --- _cargo_workspace_root over a real prepared snapshot -----------------------

PACKAGE = '[package]\nname = "p"\nversion = "0.1.0"\n'
WORKSPACE = '[workspace]\nmembers = ["crate1"]\n'


def _root(git_repo: GitRepo, tmp_path: Path, cwd: str, *, edit=None) -> Path:
    scratch = tmp_path / "snapshot"
    scratch.mkdir()
    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            if edit is not None:
                edit(snapshot.root)
            found = runner._cargo_workspace_root(
                snapshot.root / cwd, snapshot.root, prepared, timeout=60.0
            )
            assert found == snapshot.root / found.relative_to(snapshot.root)
            return found.relative_to(snapshot.root)


def test_the_workspace_above_the_cwd_is_the_root(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("Cargo.toml", WORKSPACE)
    git_repo.write("crate1/Cargo.toml", PACKAGE)
    git_repo.commit_all("workspace")

    assert _root(git_repo, tmp_path, "crate1") == Path(".")


def test_a_nested_workspace_nearest_the_cwd_wins(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("Cargo.toml", WORKSPACE)
    git_repo.write("sub/Cargo.toml", WORKSPACE)
    git_repo.write("sub/crate1/Cargo.toml", PACKAGE)
    git_repo.commit_all("nested workspaces")

    assert _root(git_repo, tmp_path, "sub/crate1") == Path("sub")


def test_a_lone_package_is_its_own_root(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("crate1/Cargo.toml", PACKAGE)
    git_repo.commit_all("lone package")

    assert _root(git_repo, tmp_path, "crate1") == Path("crate1")


def test_the_cwd_itself_may_be_the_workspace_root(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("Cargo.toml", WORKSPACE)
    git_repo.commit_all("workspace at the top")

    assert _root(git_repo, tmp_path, ".") == Path(".")


def test_no_manifest_at_all_is_a_lane_config_error(git_repo: GitRepo, tmp_path: Path):
    with pytest.raises(LaneConfigError, match="no cargo workspace root"):
        _root(git_repo, tmp_path, ".")


def test_a_package_above_the_cwd_without_a_workspace_is_not_a_root(
    git_repo: GitRepo, tmp_path: Path
):
    git_repo.write("Cargo.toml", PACKAGE)
    git_repo.write("deep/er/readme.txt", "x\n")
    git_repo.commit_all("package above, no manifest at the cwd")

    with pytest.raises(LaneConfigError, match="no cargo workspace root"):
        _root(git_repo, tmp_path, "deep/er")


def test_an_unparseable_manifest_is_a_lane_config_error(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("Cargo.toml", "[package\nname = \n")
    git_repo.commit_all("broken manifest")

    with pytest.raises(LaneConfigError, match="cannot parse the committed cargo manifest"):
        _root(git_repo, tmp_path, ".")


def test_a_non_utf8_manifest_is_a_lane_config_error(git_repo: GitRepo, tmp_path: Path):
    (git_repo.path / "Cargo.toml").write_bytes(b"[package]\nname = \"\xff\xfe\"\n")
    git_repo.commit_all("binary manifest")

    with pytest.raises(LaneConfigError, match="cannot parse the committed cargo manifest"):
        _root(git_repo, tmp_path, ".")


def test_a_manifest_edited_by_the_command_is_ignored(git_repo: GitRepo, tmp_path: Path):
    git_repo.write("Cargo.toml", WORKSPACE)
    git_repo.write("crate1/Cargo.toml", PACKAGE)
    git_repo.commit_all("workspace")

    def edit(root: Path) -> None:
        (root / "Cargo.toml").write_text(PACKAGE, encoding="utf-8")
        (root / "crate1" / "Cargo.toml").write_text(WORKSPACE, encoding="utf-8")

    assert _root(git_repo, tmp_path, "crate1", edit=edit) == Path(".")


# --- the workspace root is resolved BEFORE the command runs (review finding 1) --
#
# A refusal found after R0 passed would leave a verdict `verify` rejects (an R2
# ERROR/BAD_LANE_CONFIG beside a passing R0, whose mutation buckets re-derive to
# PASS). So an unusable manifest is a lane-level refusal, like a tracked `<out>`.


def _break_manifest(git_repo: GitRepo, how: str) -> None:
    manifest = git_repo.path / "Cargo.toml"
    if how == "missing":
        git_repo.git("rm", "-q", "Cargo.toml")
    elif how == "unparseable":
        git_repo.write("Cargo.toml", "[package\nname = \n")
    elif how == "non-utf8":
        manifest.write_bytes(b"[package]\nname = \"\xff\xfe\"\n")
    elif how == "oversized":
        git_repo.write("Cargo.toml", "# " + "x" * (runner._MAX_CARGO_MANIFEST_BYTES + 1) + "\n")
    else:
        assert how == "symlink"
        manifest.unlink()
        manifest.symlink_to("Cargo.lock")
    git_repo.commit_all(f"manifest: {how}")


@pytest.mark.parametrize(
    "how", ["missing", "unparseable", "non-utf8", "oversized", "symlink"]
)
def test_an_unusable_manifest_refuses_the_whole_lane_before_the_command(
    git_repo: GitRepo, tmp_path: Path, how: str
):
    base = _seed(git_repo, "indiff")
    _break_manifest(git_repo, how)
    lane = _lane(git_repo, base)
    fake = Fake(_stage(tmp_path, "indiff"), _exit_code("indiff"))

    verdict = _run(git_repo, lane, fake)

    assert verdict.outcome is Outcome.ERROR
    assert {claim.reason_code for claim in verdict.claims} == {ReasonCode.BAD_LANE_CONFIG}
    assert fake.calls == 0


def test_a_top_level_workspace_key_is_not_a_workspace_table(
    git_repo: GitRepo, tmp_path: Path
):
    git_repo.write("Cargo.toml", "workspace = true\n")
    git_repo.write("crate1/Cargo.toml", PACKAGE)
    git_repo.commit_all("a bare workspace key above the package")

    assert _root(git_repo, tmp_path, "crate1") == Path("crate1")


# --- a backward wall-clock step (review finding 2) --------------------------------


def test_a_backward_clock_step_is_an_unusable_report_not_a_crash(
    git_repo: GitRepo, tmp_path: Path
):
    # The wall clock steps back once, while the command runs: the command's
    # `ended` is earlier than its `started`, which `ReportWindow` rejects.
    stepped = {"back": False}

    def clock():
        if stepped["back"]:
            stepped["back"] = False
            return MOMENT - timedelta(seconds=2)
        return MOMENT

    lane, fake = _case(git_repo, tmp_path, "indiff")
    fake.before = lambda _cwd: stepped.update(back=True)

    verdict = _run(git_repo, lane, fake, clock=clock)

    # Fail closed: no usable report, so the exit code (2, survivors) decides R0
    # and R2 is not judged from the report.
    assert _claim(verdict, "R0").status is Outcome.FAIL
    assert _claim(verdict, "R2").status is Outcome.FAIL
    assert _claim(verdict, "R2").reason_code is ReasonCode.COMMAND_FAILED
    assert verdict.outcome is Outcome.FAIL


def test_an_inverted_window_is_recorded_as_an_unusable_report():
    raw = (CASES / "indiff" / "mutants.out" / "outcomes.json").read_bytes()
    artifact = safeio.DirectoryArtifact(
        artifact=f"{OUT}/mutants.out", identity=(1, 2), report=raw, members={}
    )

    class Reservation:
        def consume(self, *, members):
            return artifact

    handle = runner._DirectoryReportHandle(Reservation())

    state = handle.consume_once(
        members=lambda _raw: [],
        result_report_format=FORMAT,
        started_at=MOMENT,
        ended_at=MOMENT - timedelta(seconds=1),
    )

    assert isinstance(state, runner._DirectoryPresent)
    assert state.summary is None
    assert "wall-clock window is invalid" in (state.unusable or "")


# --- a usable report decides R0, whatever the exit code (review finding 3) -------


@pytest.mark.parametrize("exit_code", [1, 70, -9, 4])
def test_a_usable_fresh_report_decides_r0_whatever_the_exit_code(
    git_repo: GitRepo, tmp_path: Path, exit_code: int
):
    # `cargo mutants` exits 2 for survivors, 3 for timeouts, 4 for a failing
    # baseline, 1 for usage errors, 70 for internal errors; a signal is
    # negative. For this format the report is truth (B078, like vitest-json):
    # the exit code only decides R0 when no usable report exists.
    base = _seed(git_repo, "indiff")
    lane = _lane(git_repo, base)

    verdict = _run(git_repo, lane, Fake(_stage(tmp_path, "indiff"), exit_code))

    assert _claim(verdict, "R0").status is Outcome.PASS
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.FAIL
    assert r2.reason_code is ReasonCode.MUTANTS_SURVIVED


# --- the consumption memo never outlives a refusal (review finding 4) ------------


def test_a_refused_consumption_leaves_no_parser_memo(tmp_path: Path):
    parser = runner.mutation_parsers.cargo_mutants_outcomes
    raw = (CASES / "indiff" / "mutants.out" / "outcomes.json").read_bytes()
    refusal = AssayError(
        "member missing", outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
    )

    class Reservation:
        def consume(self, *, members):
            members(raw)
            raise refusal

    parser.load_document.cache_clear()
    handle = runner._DirectoryReportHandle(Reservation())

    state = handle.consume_once(
        members=parser.diff_paths,
        result_report_format=FORMAT,
        started_at=MOMENT,
        ended_at=MOMENT,
    )

    assert isinstance(state, runner._DirectoryRefused)
    assert state.error is refusal
    assert parser.load_document.cache_info().currsize == 0


# --- a tracked `<out>`, end to end (review finding 5) ----------------------------


def test_a_tracked_out_refuses_at_the_reservation_with_the_real_detail(
    git_repo: GitRepo, tmp_path: Path
):
    git_repo.write(".gitignore", "")
    git_repo.write(f"{OUT}/keep.txt", "tracked\n")
    git_repo.commit_all("track the output directory")
    base = git_repo.head()
    git_repo.write("src/lib.rs", "pub fn f() {}\n")
    git_repo.write("Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    git_repo.commit_all("subject")
    fake = Fake(_stage(tmp_path, "indiff"), 0)

    verdict = _run(git_repo, _lane(git_repo, base), fake)

    r2 = _refused(verdict)
    assert "not empty" in (r2.detail or "")
    assert fake.calls == 0
