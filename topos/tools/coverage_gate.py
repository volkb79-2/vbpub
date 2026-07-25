#!/usr/bin/env python3
"""Mechanical diff-coverage gate for topos.

Tests the changed-line coverage model: fail the gate when a change adds or edits
*executable* source lines that no test exercises. Pure core (parse_added_lines,
evaluate) takes plain data and is unit-testable; I/O boundary (git, coverage-json)
is thin and isolated.

Adapted from nyxloom/src/nyxloom/coverage_gate.py (the D-064-L2 building block).
Kept as a standalone script — never imports from the sibling nyxloom project.

Base resolution serves both phases:
  * feature branch (HEAD is a normal tip) → diff vs merge-base(base, HEAD)
  * post-merge (HEAD has ≥2 parents) → diff vs its FIRST parent
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

# New-side hunk header: `@@ -a,b +c,d @@`.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class CoverageGateError(Exception):
    """A gate I/O boundary failed (git or coverage-json), distinct from a
    coverage *verdict* failure — CLI maps it to exit 2."""


def parse_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Walk `git diff --unified=0` output → {new-side path: {added line nums}}.

    Only new-side additions count: a `+` body line; pure deletions and contexts
    are ignored. Deleted files contribute nothing.
    """
    added: dict[str, set[int]] = {}
    current: str | None = None
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                current = target[2:] if target.startswith("b/") else target
            continue
        if line.startswith("--- "):
            continue
        m = _HUNK_RE.match(line)
        if m:
            new_lineno = int(m.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            added.setdefault(current, set()).add(new_lineno)
            new_lineno += 1
        elif line.startswith("-"):
            continue
        else:
            new_lineno += 1  # context line (only with -U>0)
    return added


def _rel_to_source(path: str, source_prefix: str) -> str:
    """Normalize a path to the canonical `<source_prefix>/...` tail."""
    n = os.path.normpath(path).replace(os.sep, "/")
    i = n.find(source_prefix)
    return n[i:] if i != -1 else n


@dataclass
class Verdict:
    uncovered: dict[str, set[int]]
    changed_executable: int
    covered: int
    pct: float
    fail_under: float
    files_missing_coverage: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.pct >= self.fail_under


def evaluate(
    added: dict[str, set[int]],
    coverage_files: dict[str, dict],
    source_prefix: str = "topos/src/topos",
    fail_under: float = 100.0,
) -> Verdict:
    """Pure heart: intersect changed lines with coverage classification.

    A changed line counts toward the denominator only if coverage deems it
    executable (in executed ∪ missing). Changes to files outside source_prefix
    (tests, docs, config) are ignored. Non-Python files under the source prefix
    are also ignored (coverage.py measures only .py modules).
    """
    prefix = os.path.normpath(source_prefix).replace(os.sep, "/")
    cov_by_norm: dict[str, dict] = {
        _rel_to_source(k, prefix): v for k, v in coverage_files.items()
    }
    total_changed_exec = 0
    total_covered = 0
    uncovered: dict[str, set[int]] = {}
    files_missing: list[str] = []
    for path, lines in added.items():
        npath = _rel_to_source(path, prefix)
        if not npath.startswith(prefix):
            continue
        if not npath.endswith(".py"):
            continue
        cov = cov_by_norm.get(npath)
        if cov is None:
            if lines:
                uncovered[npath] = set(lines)
                total_changed_exec += len(lines)
                files_missing.append(npath)
            continue
        missing = set(cov.get("missing_lines", []))
        executed = set(cov.get("executed_lines", []))
        executable = missing | executed
        changed_exec = lines & executable
        unc = changed_exec & missing
        total_changed_exec += len(changed_exec)
        total_covered += len(changed_exec & executed)
        if unc:
            uncovered[npath] = unc
    pct = 100.0 if total_changed_exec == 0 else 100.0 * total_covered / total_changed_exec
    return Verdict(
        uncovered=uncovered,
        changed_executable=total_changed_exec,
        covered=total_covered,
        pct=pct,
        fail_under=fail_under,
        files_missing_coverage=sorted(files_missing),
    )


# --------------------------------------------------------------------------- #
# thin I/O boundary
# --------------------------------------------------------------------------- #

def _git(repo: str, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise CoverageGateError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def _resolve_base(repo: str, base: str) -> str:
    tokens = _git(repo, ["rev-list", "--parents", "-n", "1", "HEAD"]).split()
    if len(tokens) >= 3:
        return tokens[1]
    return _git(repo, ["merge-base", base, "HEAD"]).strip()


def _git_added_lines(repo: str, base_rev: str, source: str) -> dict[str, set[int]]:
    out = _git(
        repo,
        ["diff", "--relative", "--unified=0", base_rev, "HEAD", "--", source],
    )
    return parse_added_lines(out)


def _load_coverage(path: str) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageGateError(f"cannot read coverage json {path}: {exc}") from exc
    files = data.get("files")
    if not isinstance(files, dict):
        raise CoverageGateError(f"coverage json {path} has no 'files' object")
    return files


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="topos/tools/coverage_gate.py",
        description="Fail when changed source lines are uncovered (P96).",
    )
    p.add_argument("--coverage-json", required=True,
                    help="path to `coverage json` output")
    p.add_argument("--base", default="main",
                    help="ref the change is measured against (default: main)")
    p.add_argument("--source", default="topos/src/topos",
                    help="source path prefix (default: topos/src/topos)")
    p.add_argument("--fail-under", type=float, default=100.0,
                    help="minimum %% of changed executable lines (default: 100)")
    p.add_argument("--repo", default=".",
                    help="git repo/worktree (default: cwd)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        base_rev = _resolve_base(args.repo, args.base)
        added = _git_added_lines(args.repo, base_rev, args.source)
        coverage_files = _load_coverage(args.coverage_json)
    except CoverageGateError as exc:
        print(f"diff-coverage ERROR: {exc}", file=sys.stderr)
        return 2

    v = evaluate(added, coverage_files, args.source, args.fail_under)
    if v.passed:
        print(
            f"diff-coverage OK: {v.covered}/{v.changed_executable} changed "
            f"executable lines covered ({v.pct:.1f}% ≥ {v.fail_under:.1f}% floor)"
        )
        return 0

    print(
        f"diff-coverage FAIL: {v.covered}/{v.changed_executable} changed executable "
        f"lines covered ({v.pct:.1f}% < {v.fail_under:.1f}% floor). Uncovered changed lines:"
    )
    for path in sorted(v.uncovered):
        tag = " [file unmeasured]" if path in v.files_missing_coverage else ""
        print(f"  {path}:{tag} {sorted(v.uncovered[path])}")
    print("Add a test that exercises these lines, or mark a genuinely "
          "unreachable line with `# pragma: no cover`.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
