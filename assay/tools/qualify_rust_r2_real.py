"""Qualify Assay's ingested Rust R2 lane against REAL ``cargo mutants`` (B245 U7).

An executable qualification script, not a pytest test: the registered Run-Gate
lane ``rust-r2-qualification`` supplies ``tester-unified-rust`` (cargo-mutants,
cargo-nextest, rustc) and invokes ``main``. For each case it builds a throwaway
git repository from the committed U0 inputs (``docs/rust-b245-u0/inputs``), writes
an assay lane (R0 + R2, ``cargo-mutants-outcomes``, the ``{assay.changed_diff}``
placeholder, ``changed_lines`` against the v1 commit), runs it through the assay
runner IN PROCESS against the real binary, and asserts the verdict outcome, exit
code, R0/R2 claims, the verdict's mutation counts against the committed fixture
(``tests/fixtures/rust/cargo-mutants``), the recorded changed diff and that
``assay verify`` accepts the document.

Preflight (``--preflight``, about seconds, no mutation is run): imports everything
the run needs (``assay.runner``, ``assay.verify``, ``assay.config``,
``assay.adapters.rust``), checks the tool versions (python >= 3.11, git, cargo,
rustc, cargo-nextest and a ``27.`` cargo-mutants), then for every selected case
builds its repository, produces the very diff assay hands to ``--in-diff`` (the
``git diff`` of ``runner._produce_changed_diff``, including the ``--- /dev/null``
new-file shape and git's trailing TAB after ``+++ b/src/with space.rs``) and runs
the REAL ``cargo mutants --list --json --in-diff <diff>``: the listed mutant count
must equal the committed fixture's ``mutants.json`` (0 for ``zero-mutants``). The
main run calls the same preflight first and aborts with exit 2 when it fails
(``--no-preflight`` skips the import and listing checks; the tool probes always
run); it also compares each verdict's ``judgment.r2.changed_diff.sha256`` with the
preflight diff, so the diff assay produced is the one the preflight listed.

Exit codes: 0 every selected case matched (preflight: all checks passed), 1 at
least one deviated (fail closed: a case that raised or produced no verdict is a
deviation; preflight: an import failed or a listing deviated), 2 a prerequisite is
missing (``cargo``, ``cargo-mutants``, ``cargo-nextest``, a ``27.`` cargo-mutants,
python < 3.11, an unknown ``--case``; the main run also exits 2 when its preflight
failed). Evidence (default ``.assay/rust-r2-real``): ``summary.json`` (per-case
verdicts, counts, deviations and wall seconds), ``preflight.json``, ``versions.txt``,
one verdict plus one log per case and, for every deviating case,
``deviations/<case>/`` with the lane file, the changed diff and the real
``assay-out`` (``mutants.out``) of the run (files over 1 MiB are skipped, 16 MiB
in total).

Known risks for the first real run (diagnose; none is a known defect):

* The ``full`` case's timeout mutant may leave descendant processes that outlive
  it: assay then reports ``outliving_descendants`` and ``ERROR``/``EXEC_FAILED``
  for ``full`` only. Read ``process_tree_evidence`` in the saved verdict
  (``verdicts/full.json``); the other cases are unaffected.
* ``ws-sub`` (a workspace root BELOW the git top with several members) and the
  workspace member ``a`` of the original ``ws`` input were never measured by U0;
  the ``ws`` inputs are renamed ``crate_a``/``crate_b`` here because a member
  directory ``a``/``b`` matches no mutant under ``--in-diff``. A ``ws-sub``
  deviation is information about that unmeasured shape, not necessarily a defect.
* ``--timeout 20`` is a wall-clock bound per mutant: under host load a mutant
  that is normally killed or survives can time out. Re-run on a quiet host before
  reading a timing-dependent deviation as a defect.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUTS = PROJECT_ROOT / "docs/rust-b245-u0/inputs"
FIXTURES = PROJECT_ROOT / "tests/fixtures/rust/cargo-mutants"
DEFAULT_EVIDENCE = PROJECT_ROOT / ".assay/rust-r2-real"
SUPPORTED_MAJOR = "27."
MIN_PYTHON = (3, 11)
OUT = "assay-out"
LANE = "r2"
TOOL_ARGS = (
    "--test-tool", "nextest", "--jobs", "1", "--timeout", "20",
    "--cargo-arg=--locked", "--cargo-arg=--offline",
)
# Variables the lane passes to cargo; only those set here are declared.
PASSTHROUGH = (
    "PATH", "HOME", "TMPDIR", "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN",
    "CARGO_NET_OFFLINE", "CARGO_TERM_COLOR", "NO_COLOR", "LANG", "LC_ALL",
)
BUCKETS = ("killed", "survived", "crashed", "budget_exceeded")
# The verdict outcome -> the process exit code (assay's closed vocabulary).
EXIT_CODES = {"PASS": 0, "FAIL": 1, "ERROR": 2, "BUDGET_EXCEEDED": 4, "INCONCLUSIVE": 5}
# The timeout mutant of `full` may move to survived/killed; the lane then judges FAIL.
MOVED_TIMEOUT = ("FAIL", "MUTANTS_SURVIVED")
# A member directory called `a` or `b` at a workspace root matches no mutant under
# --in-diff (RUST-B245-U0-MEASUREMENTS.md); the `ws` input is rebuilt with these names.
RENAMED = {"a": "crate_a", "b": "crate_b"}
# Bounds of the evidence copied for a deviating case.
EVIDENCE_FILE_LIMIT = 1024 * 1024
EVIDENCE_TOTAL_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class Case:
    name: str
    source: str                       # input crate/workspace directory (relative to docs inputs or the fixtures)
    fixture: str | None               # committed fixture the counts are compared with
    outcome: str                      # expected verdict outcome (the NOMINAL one for a tolerant case)
    r2: tuple[str, str | None]        # expected R2 (status, reason); nominal for a tolerant case
    r0: str = "PASS"
    v2: str | None = None             # input whose src/lib.rs replaces v1's in the second commit
    prefix: str = ""                  # the workspace root below the git top
    lock: bool = True                 # commit the fixture's Cargo.lock
    extra: tuple[str, ...] = ()       # more cargo-mutants arguments
    report_absent: bool = False
    tolerant_timeout: bool = False    # the timing-dependent timeout mutant of `full`
    unmeasured: bool = False          # a shape the U0 captures did not run
    from_fixture_subject: bool = False  # source is the fixture's own subject/ directory
    roots: tuple[str, ...] = ("src",)
    lock_fixture: str | None = None   # fixture whose Cargo.lock is committed (default: `fixture`)
    no_payload: bool = False          # a failed baseline: no mutation payload, no judgment, fixture lists mutants only
    listing: int | None = None        # mutants `--list` must print when there is no fixture (zero-mutants)


CASES: tuple[Case, ...] = (
    Case("full", "subject", "full", "BUDGET_EXCEEDED", ("BUDGET_EXCEEDED", "LANE_TIMEOUT"),
         tolerant_timeout=True),
    Case("indiff", "subject", "indiff", "FAIL", ("FAIL", "MUTANTS_SURVIVED"), v2="subject-v2"),
    Case("subdir", "subject", "subdir", "FAIL", ("FAIL", "MUTANTS_SURVIVED"), v2="subject-v2",
         prefix="sub/crate1"),
    Case("zero-mutants", "subject", None, "INCONCLUSIVE", ("INCONCLUSIVE", "NO_MUTANTS"), v2="subject-cm",
         lock_fixture="zero-mutants", report_absent=True, listing=0),
    Case("basefail-test", "subject-bf", "basefail-test", "FAIL", ("FAIL", "COMMAND_FAILED"), r0="FAIL",
         lock_fixture="basefail-test", no_payload=True),
    Case("basefail-build", "subject", "basefail-build", "FAIL", ("FAIL", "COMMAND_FAILED"), r0="FAIL",
         lock=False, no_payload=True),
    Case("crashed-f4", "ws", "crashed-f4", "ERROR", ("ERROR", "EXEC_FAILED"),
         roots=("crate_a/src", "crate_b/src")),
    Case("w-true", "ws", "w-true", "PASS", ("PASS", None),
         extra=("--test-workspace=true",), roots=("crate_a/src", "crate_b/src")),
    Case("ws-sub", "ws", "w-true", "PASS", ("PASS", None), prefix="hostobs", lock_fixture="w-true",
         extra=("--test-workspace=true",), roots=("crate_a/src", "crate_b/src"), unmeasured=True),
    Case("methods", "methods", "methods", "PASS", ("PASS", None)),
    Case("paths", "paths", "paths", "FAIL", ("FAIL", "MUTANTS_SURVIVED")),
    Case("unicode", "unicode", "unicode", "PASS", ("PASS", None), from_fixture_subject=True),
)


@lru_cache(maxsize=1)
def _assay() -> SimpleNamespace:
    """Everything the run imports from assay (ImportError propagates to the caller)."""
    from assay import __version__, config, runner, verify
    from assay.adapters.rust import RustAdapter

    return SimpleNamespace(
        version=__version__, runner=runner, verify_document=verify.verify_document,
        load_lane_file=config.load_lane_file, RustAdapter=RustAdapter,
    )


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )


GIT = ("git", "-c", "user.email=qualify@example.invalid", "-c", "user.name=qualify", "-c", "core.quotepath=off")


def _git(repo: Path, *args: str) -> str:
    result = _run([*GIT, *args], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} exited {result.returncode}:\n{result.stdout}")
    return result.stdout.strip()


def fixture_counts(name: str) -> dict[str, int]:
    """The committed fixture's mutants as assay buckets (a Test ``Failure(100)`` is
    the only kill: PROVENANCE.md, crashed-f4)."""
    document = json.loads((FIXTURES / name / "mutants.out/outcomes.json").read_text(encoding="utf-8"))
    counts = dict.fromkeys((*BUCKETS, "unviable"), 0)
    for outcome in document["outcomes"]:
        summary = outcome["summary"]
        if "Mutant" not in (outcome.get("scenario") or {}):
            continue
        test = [p["process_status"] for p in outcome["phase_results"] if p["phase"] == "Test"]
        if summary == "CaughtMutant":
            killed = bool(test) and test[0] == {"Failure": 100}
            counts["killed" if killed else "crashed"] += 1
        elif summary == "MissedMutant":
            counts["survived"] += 1
        elif summary == "Timeout":
            counts["budget_exceeded"] += 1
        elif summary == "Unviable":
            counts["unviable"] += 1
        else:
            raise AssertionError(f"fixture {name}: unmapped summary {summary!r}")
    return counts


def expected_listing(case: Case) -> int | None:
    """How many mutants ``cargo mutants --list --in-diff`` prints for the case: the
    fixture's ``mutants.json`` (every generated mutant, whatever its later fate)."""
    if case.fixture:
        return len(json.loads((FIXTURES / case.fixture / "mutants.out/mutants.json").read_text(encoding="utf-8")))
    return case.listing


def _copy_source(case: Case, source: str, destination: Path) -> None:
    root = (FIXTURES / case.fixture / "subject") if case.from_fixture_subject else INPUTS / source
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root)
        if relative.name == "Cargo.lock":
            continue
        parts = list(relative.parts)
        if case.source == "ws" and parts[0] in RENAMED:
            parts[0] = RENAMED[parts[0]]
        target = destination.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        if case.source == "ws":
            data = data.replace(b'members = ["a", "b"]', b'members = ["crate_a", "crate_b"]')
            data = data.replace(b'path = "../a"', b'path = "../crate_a"')
        target.write_bytes(data)
    if case.lock:
        lock = FIXTURES / (case.lock_fixture or case.fixture or "") / "subject/Cargo.lock"
        destination.joinpath("Cargo.lock").write_bytes(lock.read_bytes())


def lint_workspace(root: Path) -> None:
    """Refuse the measured hazard: a member directory named ``a``/``b`` at the root."""
    for name in RENAMED:
        if (root / name / "Cargo.toml").is_file():
            raise AssertionError(f"member directory {name!r} at {root} matches no mutant under --in-diff")


def build_repo(case: Case, repo: Path) -> tuple[str, str]:
    """Create the throwaway repository; returns (base, head)."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", ".")
    (repo / ".gitignore").write_text(f"{OUT}\n", encoding="utf-8")
    (repo / "README").write_text("qualification repository\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "gitignore the output directory")
    base = _git(repo, "rev-parse", "HEAD")
    workspace = repo / case.prefix if case.prefix else repo
    workspace.mkdir(parents=True, exist_ok=True)
    _copy_source(case, case.source, workspace)
    lint_workspace(workspace)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "v1")
    head = _git(repo, "rev-parse", "HEAD")
    if case.v2 is not None:
        base = head
        shutil.copyfile(INPUTS / case.v2 / "src/lib.rs", workspace / "src/lib.rs")
        _git(repo, "commit", "-q", "-a", "-m", "v2")
        head = _git(repo, "rev-parse", "HEAD")
    return base, head


def lane_toml(case: Case, base: str) -> str:
    passthrough = [name for name in PASSTHROUGH if name in os.environ]
    argv = ["cargo", "mutants", "--in-diff", "{assay.changed_diff}", *TOOL_ARGS, *case.extra, "--output", OUT]
    roots = [f"{case.prefix}/{root}" if case.prefix else root for root in case.roots]
    artifact = f"{case.prefix}/{OUT}" if case.prefix else OUT
    lines = [
        "schema_version = 2", "", f"[lanes.{LANE}]", 'scope = "S1"', 'rigor = ["R0", "R2"]',
        'enforcement = "gate"',
    ]
    if case.prefix:
        lines.append(f'cwd = "{case.prefix}"')
    lines += [
        f"argv = {json.dumps(argv)}", "env = {}", f"env_passthrough = {json.dumps(passthrough)}",
        'budget = "30m"', "allow_argv_append = false", "",
        f"[lanes.{LANE}.isolation]", 'snapshot_selection = "repository"', "",
        f"[lanes.{LANE}.judge]", 'language = "rust"', f"source_roots = {json.dumps(roots)}",
        f'base = "{base}"', "", f"[lanes.{LANE}.judge.mutation]", 'format = "cargo-mutants-outcomes"',
        f'artifact = "{artifact}"', "fail_under = 100.0", "", f"[lanes.{LANE}.result_report]",
        'format = "cargo-mutants-outcomes"', f'path = "{OUT}"', "",
    ]
    return "\n".join(lines)


def changed_diff(case: Case, repo: Path, base: str, head: str) -> bytes:
    """The patch assay hands to ``--in-diff`` (``runner._produce_changed_diff``): git
    diff with explicit prefixes, no renames, ``--relative`` to the workspace root
    below the git top, restricted to the lane's source roots."""
    pathspecs = [f"{case.prefix}/{root}" if case.prefix else root for root in case.roots]
    result = subprocess.run(
        [*GIT, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--src-prefix=a/", "--dst-prefix=b/",
         "--no-renames", *([f"--relative={case.prefix}"] if case.prefix else []), base, head, "--", *pathspecs],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff exited {result.returncode}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def _claim(document: dict[str, Any], rigor: str) -> dict[str, Any] | None:
    return next((c for c in document.get("claims", []) if c.get("rigor") == rigor), None)


def _counts(claim: dict[str, Any] | None) -> dict[str, int] | None:
    mutation = (claim or {}).get("mutation")
    if not isinstance(mutation, dict):
        return None
    return {name: len(mutation.get(name) or []) for name in BUCKETS}


def check_case(
    case: Case, document: dict[str, Any], *, base: str, head: str, diff_sha256: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """The deviations of one verdict from its expectation, plus what was observed."""
    problems: list[str] = []
    r0, r2 = _claim(document, "R0"), _claim(document, "R2")
    observed_counts = _counts(r2)
    seen: dict[str, Any] = {
        "outcome": document.get("outcome"),
        "exit_code": document.get("exit_code"),
        "r0": (r0 or {}).get("status"),
        "r2": None if r2 is None else [r2.get("status"), r2.get("reason_code")],
        "counts": observed_counts,
    }
    for problem in _assay().verify_document(document):
        problems.append(f"assay verify refused the verdict: {problem}")
    if (r0 or {}).get("status") != case.r0:
        problems.append(f"R0 was {seen['r0']!r}, expected {case.r0!r}")
    expected_r2, outcome = case.r2, case.outcome
    expected_counts = fixture_counts(case.fixture) if case.fixture else None
    if (
        case.tolerant_timeout and observed_counts is not None
        and expected_counts is not None and observed_counts["budget_exceeded"] == 0
    ):
        # The timeout mutant is the one timing-dependent case: it may land in survived/killed.
        expected_r2, outcome = MOVED_TIMEOUT, MOVED_TIMEOUT[0]
    status, reason = expected_r2
    if r2 is None or r2.get("status") != status or r2.get("reason_code") != reason:
        problems.append(f"R2 was {seen['r2']!r}, expected {[status, reason]!r}")
    if document.get("outcome") != outcome:
        problems.append(f"verdict outcome was {document.get('outcome')!r}, expected {outcome!r}")
    if document.get("exit_code") != EXIT_CODES[outcome]:
        problems.append(f"verdict exit_code was {document.get('exit_code')!r}, expected {EXIT_CODES[outcome]!r} ({outcome})")
    judgment = (document.get("judgment") or {}).get("r2") or {}
    if bool(judgment.get("report_absent")) != case.report_absent:
        problems.append(f"judgment.r2.report_absent was {judgment.get('report_absent')!r}, expected {case.report_absent}")
    if not case.no_payload:
        changed = judgment.get("changed_diff") or {}
        want = {"base_rev": base, "head_rev": head, "relative_to": case.prefix or "."}
        if diff_sha256 is not None:
            want["sha256"] = diff_sha256
        got = {key: changed.get(key) for key in want}
        if got != want:
            problems.append(f"judgment.r2.changed_diff was {got!r}, expected {want!r}")
    if case.no_payload:
        # A failed baseline judges no mutants: the fixture lists them (mutants.json) but ran none.
        if observed_counts is not None:
            problems.append(f"a failed baseline must carry no mutation payload, got {observed_counts!r}")
        if expected_counts is not None and any(expected_counts[name] for name in BUCKETS):
            problems.append(f"fixture {case.fixture} holds mutant outcomes {expected_counts!r}, a failed baseline has none")
    elif expected_counts is not None and observed_counts is not None:
        problems += _compare_counts(case, observed_counts, expected_counts)
    elif expected_counts is not None:
        problems.append("the verdict carries no mutation payload to compare with the fixture")
    return problems, seen


def _compare_counts(case: Case, got: dict[str, int], want: dict[str, int]) -> list[str]:
    wanted = {name: want[name] for name in BUCKETS}
    if not case.tolerant_timeout:
        return [] if got == wanted else [f"mutation counts {got!r} differ from fixture {case.fixture}: {wanted!r}"]
    # Only the fixture's timeout mutant may move (to killed or survived), never anything else.
    # (got budget_exceeded <= wanted follows from the sum and the two lower bounds.)
    ok = (
        got["crashed"] == wanted["crashed"]
        and sum(got.values()) == sum(wanted.values())
        and got["killed"] >= wanted["killed"] and got["survived"] >= wanted["survived"]
    )
    return [] if ok else [f"mutation counts {got!r} are outside the timeout tolerance of fixture {case.fixture}: {wanted!r}"]


def _copy_bounded(source: Path, destination: Path, budget: list[int]) -> int:
    """Copy *source* (a directory) under *destination*; skips files over the per-file
    limit and stops at the total limit. Returns the number of files skipped."""
    skipped = 0
    for path in sorted(source.rglob("*")):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            if size > EVIDENCE_FILE_LIMIT or size > budget[0]:
                skipped += 1
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            budget[0] -= size
        except OSError:
            skipped += 1
    return skipped


def preserve_deviation(case: Case, repo: Path, runner_scratch: Path, evidence: Path) -> dict[str, Any]:
    """Copy what a deviating case leaves behind (lane file, changed diff, the real
    ``assay-out``) into ``<evidence>/deviations/<case>/``, bounded."""
    destination = evidence / "deviations" / case.name
    budget = [EVIDENCE_TOTAL_LIMIT]
    copied: list[str] = []
    skipped = 0
    try:
        destination.mkdir(parents=True, exist_ok=True)
        lane_file = repo / "assay.toml"
        if lane_file.is_file():
            shutil.copyfile(lane_file, destination / "assay.toml")
            copied.append("assay.toml")
        for root, directories, _files in os.walk(runner_scratch):
            for name in list(directories):
                if name in (OUT, "changed-diff"):
                    directories.remove(name)
                    relative = (Path(root) / name).relative_to(runner_scratch)
                    skipped += _copy_bounded(Path(root) / name, destination / relative, budget)
                    copied.append(relative.as_posix())
                elif name in (".git", "target"):
                    directories.remove(name)
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "copied": copied}
    return {"copied": copied, "skipped_files": skipped, "dir": str(destination)}


def run_case(case: Case, scratch: Path, evidence: Path, *, diff_sha256: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"case": case.name, "unmeasured": case.unmeasured, "deviations": []}
    started = time.monotonic()
    log = io.StringIO()
    repo = scratch / case.name / "repo"
    runner_scratch = scratch / case.name / "runner"

    @contextlib.contextmanager
    def keep_scratch() -> Iterator[Path]:
        runner_scratch.mkdir(parents=True, exist_ok=True)
        yield runner_scratch.resolve()

    try:
        base, head = build_repo(case, repo)
        # The lane file must be committed (assay refuses an uncommitted policy); it names the
        # base, so it is the last commit and the head is the commit that holds it.
        (repo / "assay.toml").write_text(lane_toml(case, base), encoding="utf-8")
        _git(repo, "add", "assay.toml")
        _git(repo, "commit", "-q", "-m", "the assay lane")
        head = _git(repo, "rev-parse", "HEAD")
        result.update(base=base, head=head)
        verdict_path = evidence / "verdicts" / f"{case.name}.json"
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.unlink(missing_ok=True)
        # `assay run` refuses `rust` at R2 until the registry wires it (K15, design U8), so
        # the runner is driven directly, exactly as the U5 tests do, with the real adapter.
        assay = _assay()
        lane = assay.load_lane_file(repo / "assay.toml").lane(LANE)
        captured = [0]
        capture_budget = [EVIDENCE_TOTAL_LIMIT]

        def capturing_runner(argv, *, env, cwd, timeout):
            # The snapshot (and the real `mutants.out` in it) is gone when the run ends:
            # keep a bounded copy of the tool's output directory for a later deviation.
            try:
                return assay.runner.default_process_runner(argv, env=env, cwd=cwd, timeout=timeout)
            finally:
                if "mutants" in argv and (Path(cwd) / OUT).is_dir():
                    captured[0] += 1
                    _copy_bounded(Path(cwd) / OUT, runner_scratch / "captured" / str(captured[0]) / OUT, capture_budget)

        verdict = assay.runner.run_lane(
            lane, commit=head, repo=repo, project_root=repo, adapter=assay.RustAdapter(),
            assay_version=assay.version, diagnostics=log, scratch_root_factory=keep_scratch,
            process_runner=capturing_runner,
        )
        verdict_path.write_text(verdict.to_json(), encoding="utf-8")
        code = verdict.exit_code
        result["exit_code"] = code
        if not verdict_path.is_file():
            result["deviations"].append("no verdict was written")
        else:
            document = json.loads(verdict_path.read_text(encoding="utf-8"))
            if document.get("exit_code") != code:
                result["deviations"].append(f"the runner returned {code}, the verdict says {document.get('exit_code')!r}")
            problems, seen = check_case(case, document, base=base, head=head, diff_sha256=diff_sha256)
            result["deviations"] += problems
            result["observed"] = seen
    except Exception as exc:  # fail closed: a crashed case is a deviation, never a skip
        result["deviations"].append(f"{type(exc).__name__}: {exc}")
    result["wall_seconds"] = round(time.monotonic() - started, 1)
    (evidence / "logs").mkdir(parents=True, exist_ok=True)
    (evidence / "logs" / f"{case.name}.txt").write_text(log.getvalue(), encoding="utf-8")
    if result["deviations"]:
        result["evidence"] = preserve_deviation(case, repo, runner_scratch, evidence)
    result["ok"] = not result["deviations"]
    return result


def probe_tools() -> tuple[list[str], list[str]]:
    """(problems, version lines): the missing binaries, an old python or an unqualified major."""
    problems = [f"{name} is not on PATH" for name in ("cargo", "cargo-mutants", "cargo-nextest", "git")
                if shutil.which(name) is None]
    if sys.version_info < MIN_PYTHON:
        problems.append(f"python {sys.version.split()[0]} is older than {'.'.join(map(str, MIN_PYTHON))}")
    versions: list[str] = []
    if problems:
        return problems, versions
    for command in (["cargo", "mutants", "--version"], ["cargo", "nextest", "--version"], ["rustc", "--version"],
                    ["cargo", "--version"], ["git", "--version"]):
        probe = _run(command, cwd=PROJECT_ROOT)
        if probe.returncode != 0:
            problems.append(f"{' '.join(command)} exited {probe.returncode}")
        versions.append(probe.stdout.strip().splitlines()[0] if probe.stdout.strip() else "")
    versions.append(f"python {sys.version.split()[0]}")
    if not problems and not (versions[0].split() or [""])[-1].startswith(SUPPORTED_MAJOR):
        problems.append(f"cargo-mutants {versions[0]!r} is outside the qualified major {SUPPORTED_MAJOR}x")
    return problems, versions


def _list_mutants(case: Case, workspace: Path, diff: Path) -> tuple[int | None, str]:
    """(count, problem): the REAL ``cargo mutants --list --json --in-diff <diff>``."""
    env = {name: os.environ[name] for name in PASSTHROUGH if name in os.environ}
    command = ["cargo", "mutants", "--list", "--json", "--in-diff", str(diff),
               "--cargo-arg=--locked", "--cargo-arg=--offline", *case.extra]
    done = subprocess.run(
        command, cwd=workspace, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if done.returncode != 0:
        return None, f"`{' '.join(command[:4])} ...` exited {done.returncode}: {done.stderr.strip()[-400:]}"
    text = done.stdout.strip()
    try:
        listed = json.loads(text) if text else []
    except json.JSONDecodeError as exc:
        return None, f"`--list --json` printed no JSON ({exc}): {text[:200]!r}"
    if not isinstance(listed, list):
        return None, f"`--list --json` printed {type(listed).__name__}, expected a list"
    return len(listed), ""


def preflight_case(case: Case, scratch: Path) -> dict[str, Any]:
    """Build the case's repository, produce assay's diff and list the real mutants."""
    entry: dict[str, Any] = {"case": case.name, "deviations": []}
    started = time.monotonic()
    try:
        repo = scratch / "preflight" / case.name / "repo"
        base, head = build_repo(case, repo)
        diff = changed_diff(case, repo, base, head)
        text = diff.decode("utf-8", errors="replace")
        entry["diff_sha256"] = hashlib.sha256(diff).hexdigest()
        entry["diff_bytes"] = len(diff)
        entry["diff_shape"] = "new-file" if "--- /dev/null" in text else "modify"
        entry["tab_suffixed_header"] = any(
            line.startswith("+++ b/") and line.endswith("\t") for line in text.splitlines())
        if not diff:
            entry["deviations"].append("assay's changed diff is empty")
        diff_path = scratch / "preflight" / case.name / "in-diff.patch"
        diff_path.write_bytes(diff)
        workspace = repo / case.prefix if case.prefix else repo
        count, problem = _list_mutants(case, workspace, diff_path)
        entry["mutants_listed"] = count
        want = expected_listing(case)
        entry["mutants_expected"] = want
        if problem:
            entry["deviations"].append(problem)
        elif want is not None and count != want:
            entry["deviations"].append(f"`cargo mutants --list` printed {count} mutants, the committed fixture lists {want}")
    except Exception as exc:  # fail closed
        entry["deviations"].append(f"{type(exc).__name__}: {exc}")
    entry["seconds"] = round(time.monotonic() - started, 1)
    return entry


def preflight(selected: list[Case], scratch: Path, *, listing: bool = True) -> dict[str, Any]:
    """The checks that need no mutation run. ``missing`` (a prerequisite: exit 2),
    ``deviations`` (an import failed or a listing deviated: exit 1), per-case reports."""
    missing, versions = probe_tools()
    report: dict[str, Any] = {"tools": versions, "missing": missing, "deviations": [], "cases": []}
    if not listing:
        return report
    try:
        _assay()
    except ImportError as exc:
        report["deviations"].append(f"cannot import assay ({exc}); check PYTHONPATH")
    if missing:
        return report
    for case in selected:
        entry = preflight_case(case, scratch)
        report["cases"].append(entry)
        report["deviations"] += [f"{case.name}: {problem}" for problem in entry["deviations"]]
    return report


def _print_preflight(report: dict[str, Any]) -> None:
    for entry in report["cases"]:
        shape = f"{entry.get('diff_shape')}, {entry.get('diff_bytes')} bytes" if "diff_shape" in entry else "no diff"
        listed = f"{entry.get('mutants_listed')} mutants (expected {entry.get('mutants_expected')})"
        print(f"preflight {entry['case']}: {'ok' if not entry['deviations'] else 'DEVIATED'} - {shape}, {listed} ({entry['seconds']}s)", flush=True)
    for problem in report["deviations"]:
        print(f"  - {problem}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", default=[], metavar="NAME", help="run only this case (repeatable)")
    parser.add_argument("--list", action="store_true", help="print the case names and exit")
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--preflight", action="store_true",
                        help="only check imports, tool versions and the real `cargo mutants --list --in-diff` of each case (seconds)")
    parser.add_argument("--no-preflight", action="store_true",
                        help="skip the import and listing checks before the run (the tool probes always run)")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(case.name for case in CASES))
        return 0
    names = {case.name for case in CASES}
    unknown = sorted(set(args.case) - names)
    if unknown:
        print(f"unknown case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    selected = [case for case in CASES if not args.case or case.name in args.case]
    evidence: Path = args.evidence_dir
    with tempfile.TemporaryDirectory(prefix="assay-rust-r2-") as scratch:
        report = preflight(selected, Path(scratch), listing=not args.no_preflight)
        if report["missing"] or report["deviations"]:
            _print_preflight(report)
            if not report["missing"] or args.preflight:
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / "preflight.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            for problem in report["missing"]:
                print(f"qualify_rust_r2_real: {problem}", file=sys.stderr)
            # missing tool -> 2; a deviation -> 1 on --preflight, 2 (abort) before the main run
            return 2 if report["missing"] or not args.preflight else 1
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "versions.txt").write_text("\n".join(report["tools"]) + "\n", encoding="utf-8")
        if not args.no_preflight:
            _print_preflight(report)
            (evidence / "preflight.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.preflight:
            return 0
        shas = {entry["case"]: entry["diff_sha256"] for entry in report["cases"]}
        results = []
        for case in selected:
            outcome = run_case(case, Path(scratch), evidence, diff_sha256=shas.get(case.name))
            results.append(outcome)
            print(f"{case.name}: {'ok' if outcome['ok'] else 'DEVIATED'} ({outcome['wall_seconds']}s)", flush=True)
            for deviation in outcome["deviations"]:
                print(f"  - {deviation}", flush=True)
    summary = {
        "ok": all(r["ok"] for r in results),
        "tools": report["tools"],
        "wall_seconds_total": round(sum(r["wall_seconds"] for r in results), 1),
        "cases": results,
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
