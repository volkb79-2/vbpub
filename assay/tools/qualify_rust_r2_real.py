"""Qualify Assay's ingested Rust R2 lane against REAL ``cargo mutants`` (B245 U7).

An executable qualification script, not a pytest test: the registered Run-Gate
lane ``rust-r2-qualification`` supplies ``tester-unified-rust`` (cargo-mutants,
cargo-nextest, rustc) and invokes ``main``. For each case it builds a throwaway
git repository from the committed U0 inputs (``docs/rust-b245-u0/inputs``), writes
an assay lane (R0 + R2, ``cargo-mutants-outcomes``, the ``{assay.changed_diff}``
placeholder, ``changed_lines`` against the v1 commit), runs it through the assay
runner IN PROCESS against the real binary, and asserts the verdict, the verdict's
mutation counts against the committed fixture (``tests/fixtures/rust/cargo-mutants``)
and that ``assay verify`` accepts the document.

Exit codes: 0 every selected case matched, 1 at least one deviated (fail closed:
a case that raised or produced no verdict is a deviation), 2 a prerequisite is
missing (``cargo``, ``cargo-mutants``, ``cargo-nextest``, a ``27.`` cargo-mutants,
an unknown ``--case``). Evidence (default ``.assay/rust-r2-real``): ``summary.json``
(per-case verdicts, counts, deviations and wall seconds), ``versions.txt`` and one
verdict plus one log per case.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assay import __version__, runner
from assay.adapters.rust import RustAdapter
from assay.config import load_lane_file
from assay.verify import verify_document


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUTS = PROJECT_ROOT / "docs/rust-b245-u0/inputs"
FIXTURES = PROJECT_ROOT / "tests/fixtures/rust/cargo-mutants"
DEFAULT_EVIDENCE = PROJECT_ROOT / ".assay/rust-r2-real"
SUPPORTED_MAJOR = "27."
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
# A member directory called `a` or `b` at a workspace root matches no mutant under
# --in-diff (RUST-B245-U0-MEASUREMENTS.md); the `ws` input is rebuilt with these names.
RENAMED = {"a": "crate_a", "b": "crate_b"}


@dataclass(frozen=True)
class Case:
    name: str
    source: str                       # input crate/workspace directory (relative to docs inputs or the fixtures)
    fixture: str | None               # committed fixture the counts are compared with
    outcome: str | None = None        # expected verdict outcome (None: not asserted)
    r0: str = "PASS"
    r2: tuple[str, str | None] | None = None   # expected R2 (status, reason); None: no R2 claim expected
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


CASES: tuple[Case, ...] = (
    Case("full", "subject", "full", outcome="BUDGET_EXCEEDED",
         r2=("BUDGET_EXCEEDED", "LANE_TIMEOUT"), tolerant_timeout=True),
    Case("indiff", "subject", "indiff", v2="subject-v2", outcome="FAIL",
         r2=("FAIL", "MUTANTS_SURVIVED")),
    Case("subdir", "subject", "subdir", v2="subject-v2", prefix="sub/crate1", outcome="FAIL",
         r2=("FAIL", "MUTANTS_SURVIVED")),
    Case("zero-mutants", "subject", None, v2="subject-cm", lock_fixture="zero-mutants", report_absent=True,
         r2=("INCONCLUSIVE", "NO_MUTANTS")),
    Case("basefail-test", "subject-bf", None, outcome="FAIL", r0="FAIL", lock_fixture="basefail-test"),
    Case("basefail-build", "subject", None, outcome="FAIL", r0="FAIL", lock=False),
    Case("crashed-f4", "ws", "crashed-f4", r2=("ERROR", "EXEC_FAILED"),
         roots=("crate_a/src", "crate_b/src")),
    Case("w-true", "ws", "w-true", outcome="PASS", r2=("PASS", None),
         extra=("--test-workspace=true",), roots=("crate_a/src", "crate_b/src")),
    Case("ws-sub", "ws", "w-true", outcome="PASS", r2=("PASS", None), prefix="hostobs", lock_fixture="w-true",
         extra=("--test-workspace=true",), roots=("crate_a/src", "crate_b/src"), unmeasured=True),
    Case("methods", "methods", "methods", outcome="PASS", r2=("PASS", None)),
    Case("paths", "paths", "paths", outcome="FAIL", r2=("FAIL", "MUTANTS_SURVIVED")),
    Case("unicode", "unicode", "unicode", outcome="PASS", r2=("PASS", None),
         from_fixture_subject=True),
)


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )


def _git(repo: Path, *args: str) -> str:
    result = _run(
        ["git", "-c", "user.email=qualify@example.invalid", "-c", "user.name=qualify",
         "-c", "core.quotepath=off", *args],
        cwd=repo,
    )
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


def _claim(document: dict[str, Any], rigor: str) -> dict[str, Any] | None:
    return next((c for c in document.get("claims", []) if c.get("rigor") == rigor), None)


def _counts(claim: dict[str, Any] | None) -> dict[str, int] | None:
    mutation = (claim or {}).get("mutation")
    if not isinstance(mutation, dict):
        return None
    return {name: len(mutation.get(name) or []) for name in BUCKETS}


def check_case(case: Case, document: dict[str, Any], *, base: str, head: str) -> tuple[list[str], dict[str, Any]]:
    """The deviations of one verdict from its expectation, plus what was observed."""
    problems: list[str] = []
    r0, r2 = _claim(document, "R0"), _claim(document, "R2")
    observed_counts = _counts(r2)
    seen: dict[str, Any] = {
        "outcome": document.get("outcome"),
        "r0": (r0 or {}).get("status"),
        "r2": None if r2 is None else [r2.get("status"), r2.get("reason_code")],
        "counts": observed_counts,
    }
    for problem in verify_document(document):
        problems.append(f"assay verify refused the verdict: {problem}")
    if (r0 or {}).get("status") != case.r0:
        problems.append(f"R0 was {seen['r0']!r}, expected {case.r0!r}")
    expected_r2 = case.r2
    expected_counts = fixture_counts(case.fixture) if case.fixture else None
    if case.tolerant_timeout and observed_counts is not None and expected_counts is not None:
        # The timeout mutant is the one timing-dependent case: it may land in survived/killed.
        expected_r2 = ("BUDGET_EXCEEDED", "LANE_TIMEOUT") if observed_counts["budget_exceeded"] else ("FAIL", "MUTANTS_SURVIVED")
    if expected_r2 is not None:
        status, reason = expected_r2
        if r2 is None or r2.get("status") != status or r2.get("reason_code") != reason:
            problems.append(f"R2 was {seen['r2']!r}, expected {[status, reason]!r}")
    elif r2 is not None and r2.get("status") == "PASS":
        problems.append("a failed baseline must not carry a passing R2 claim")
    outcome = case.outcome
    if case.tolerant_timeout and outcome is not None and expected_r2 is not None:
        outcome = expected_r2[0]
    if outcome is not None and document.get("outcome") != outcome:
        problems.append(f"verdict outcome was {document.get('outcome')!r}, expected {outcome!r}")
    judgment = (document.get("judgment") or {}).get("r2") or {}
    if bool(judgment.get("report_absent")) != case.report_absent:
        problems.append(f"judgment.r2.report_absent was {judgment.get('report_absent')!r}, expected {case.report_absent}")
    if case.r2 is not None and r2 is not None:
        changed = judgment.get("changed_diff") or {}
        want = {"base_rev": base, "head_rev": head, "relative_to": case.prefix or "."}
        got = {key: changed.get(key) for key in want}
        if got != want:
            problems.append(f"judgment.r2.changed_diff was {got!r}, expected {want!r}")
    if expected_counts is not None and observed_counts is not None:
        problems += _compare_counts(case, observed_counts, expected_counts)
    elif expected_counts is not None:
        problems.append("the verdict carries no mutation payload to compare with the fixture")
    return problems, seen


def _compare_counts(case: Case, got: dict[str, int], want: dict[str, int]) -> list[str]:
    wanted = {name: want[name] for name in BUCKETS}
    if not case.tolerant_timeout:
        return [] if got == wanted else [f"mutation counts {got!r} differ from fixture {case.fixture}: {wanted!r}"]
    # Only the fixture's timeout mutant may move (to killed or survived), never anything else.
    moved = wanted["budget_exceeded"] - got["budget_exceeded"]
    ok = (
        got["crashed"] == wanted["crashed"]
        and 0 <= moved <= wanted["budget_exceeded"]
        and sum(got.values()) == sum(wanted.values())
        and got["killed"] >= wanted["killed"] and got["survived"] >= wanted["survived"]
    )
    return [] if ok else [f"mutation counts {got!r} are outside the timeout tolerance of fixture {case.fixture}: {wanted!r}"]


def run_case(case: Case, scratch: Path, evidence: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"case": case.name, "unmeasured": case.unmeasured, "deviations": []}
    started = time.monotonic()
    log = io.StringIO()
    try:
        repo = scratch / case.name / "repo"
        base, head = build_repo(case, repo)
        # The lane file must be committed (assay refuses an uncommitted policy); it names the
        # base, so it is the last commit and the head is the commit that holds it.
        (repo / "assay.toml").write_text(lane_toml(case, base), encoding="utf-8")
        _git(repo, "add", "assay.toml")
        _git(repo, "commit", "-q", "-m", "the assay lane")
        head = _git(repo, "rev-parse", "HEAD")
        verdict_path = evidence / "verdicts" / f"{case.name}.json"
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.unlink(missing_ok=True)
        # `assay run` refuses `rust` at R2 until the registry wires it (K15, design U8), so
        # the runner is driven directly, exactly as the U5 tests do, with the real adapter.
        lane = load_lane_file(repo / "assay.toml").lane(LANE)
        verdict = runner.run_lane(
            lane, commit=head, repo=repo, project_root=repo, adapter=RustAdapter(),
            assay_version=__version__, diagnostics=log,
        )
        verdict_path.write_text(verdict.to_json(), encoding="utf-8")
        code = verdict.exit_code
        result["exit_code"] = code
        if not verdict_path.is_file():
            result["deviations"].append(f"no verdict was written")
        else:
            document = json.loads(verdict_path.read_text(encoding="utf-8"))
            if document.get("exit_code") != code:
                result["deviations"].append(f"the runner returned {code}, the verdict says {document.get('exit_code')!r}")
            problems, seen = check_case(case, document, base=base, head=head)
            result["deviations"] += problems
            result["observed"] = seen
    except Exception as exc:  # fail closed: a crashed case is a deviation, never a skip
        result["deviations"].append(f"{type(exc).__name__}: {exc}")
    result["wall_seconds"] = round(time.monotonic() - started, 1)
    (evidence / "logs").mkdir(parents=True, exist_ok=True)
    (evidence / "logs" / f"{case.name}.txt").write_text(log.getvalue(), encoding="utf-8")
    result["ok"] = not result["deviations"]
    return result


def probe_tools() -> tuple[list[str], list[str]]:
    """(problems, version lines): the missing binaries, or an unqualified major."""
    problems = [f"{name} is not on PATH" for name in ("cargo", "cargo-mutants", "cargo-nextest", "git")
                if shutil.which(name) is None]
    versions: list[str] = []
    if problems:
        return problems, versions
    for command in (["cargo", "mutants", "--version"], ["cargo", "nextest", "--version"], ["rustc", "--version"]):
        probe = _run(command, cwd=PROJECT_ROOT)
        if probe.returncode != 0:
            problems.append(f"{' '.join(command)} exited {probe.returncode}")
        versions.append(probe.stdout.strip().splitlines()[0] if probe.stdout.strip() else "")
    if not problems and not versions[0].split()[-1].startswith(SUPPORTED_MAJOR):
        problems.append(f"cargo-mutants {versions[0]!r} is outside the qualified major {SUPPORTED_MAJOR}x")
    return problems, versions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", default=[], metavar="NAME", help="run only this case (repeatable)")
    parser.add_argument("--list", action="store_true", help="print the case names and exit")
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
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
    problems, versions = probe_tools()
    if problems:
        for problem in problems:
            print(f"qualify_rust_r2_real: {problem}", file=sys.stderr)
        return 2
    evidence: Path = args.evidence_dir
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "versions.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")
    results = []
    with tempfile.TemporaryDirectory(prefix="assay-rust-r2-") as scratch:
        for case in selected:
            outcome = run_case(case, Path(scratch), evidence)
            results.append(outcome)
            print(f"{case.name}: {'ok' if outcome['ok'] else 'DEVIATED'} ({outcome['wall_seconds']}s)", flush=True)
            for deviation in outcome["deviations"]:
                print(f"  - {deviation}", flush=True)
    summary = {
        "ok": all(r["ok"] for r in results),
        "tools": versions,
        "wall_seconds_total": round(sum(r["wall_seconds"] for r in results), 1),
        "cases": results,
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
