#!/usr/bin/env python3
"""Mechanical diff-coverage gate for run-gate-project.

Fails the release gate when a change adds or edits *executable* lines of
run-gate.py that no test exercises. The project's TOTAL line+branch coverage
is ~47% today (909 stmts / 444 branches accumulated before any floor existed);
a total --cov-fail-under=100 therefore cannot pass yet. This gate enforces the
floor the estate actually applies to legacy code (topos/nyxloom pattern):
every NEW changed line must be covered, same-commit — the ratchet toward the
total floor, never an excuse against it (the total-100 campaign is its own
backlog entry).

Adapted from topos/tools/coverage_gate.py (the thinnest estate copy, itself
derived from nyxloom's D-064-L2 building block). Kept as a standalone script —
never imports from sibling projects.

# MIGRATION PENDING (2026-08-06, estate-wide) — this file is scheduled to be
# REPLACED by a shared library, not maintained here indefinitely.
#
# `coverage_gate.py` exists FOUR times across the estate and every copy has
# diverged: nyxloom 455 lines, dstdns 804, topos 299, plus srdm’s Go
# `tools/covergate`. The extraction is specified in
# `nyxloom/nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md`.
# Until that library exists: keep using this copy. Do NOT start migrating.

Base resolution serves both phases:
  * feature branch (HEAD is a normal tip) → diff vs merge-base(base, HEAD)
  * post-merge (HEAD has ≥2 parents) → diff vs its FIRST parent

RG-53 (2026-09-12): a changed line that ran but left an `if`/`for`/etc. arm
untaken now counts as uncovered too (`missing_branches`, populated whenever
the coverage JSON was produced with `--cov-branch` — this project's own
`selftest` lane already passes it, so the floor was previously decorative
for branches).

RW-5 (2026-09-12, RG-55 wave controller ruling, reworking RG-53's first
landing): a diff that changes zero executable lines under `--source` is
reported as SKIPPED (exit 0, `verdict: "skipped"` — never `"ok"`, never a
bare `100.0%`), not silently treated as a 100% pass and not refused by
default either. The false-green hazard RG-51/RG-54 describe is a WRONG
BASE hiding real source changes; the judge cannot tell that apart from
"this change genuinely touches no source line" by the zero alone, so the
zero is made visible and NAMED (which base, and how HEAD relates to it)
instead of being hidden behind a plain `100.0% OK` or blocking every
gate run that happens not to touch the judged file (e.g. `./run-gate.py
selftest` on `main` itself, where merge-base(main, HEAD) == HEAD). Hard
refusal (exit 2, naming the three known routes to a false 0/0) is now
OPT-IN via `--refuse-empty-diff`; `--allow-empty-diff` no longer exists.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
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
    """Normalize a path to the canonical `<source_prefix>/...` tail.

    The prefix is matched as a directory boundary: after the common prefix is
    found, the next character must be '/' or end-of-string, so that a prefix
    ``run-gate-project`` does NOT match ``run-gate-project-other/mod.py`` (the
    ``-`` would follow instead of ``/`` or EOS).
    """
    n = os.path.normpath(path).replace(os.sep, "/")
    i = n.find(source_prefix)
    if i == -1:
        return n
    tail = n[i + len(source_prefix):]
    if tail and not tail.startswith("/"):
        # Prefix match is a substring, not a directory boundary — retry
        # from the next character.
        j = n.find(source_prefix, i + 1)
        if j != -1:
            i, tail = j, n[j + len(source_prefix):]
            if tail and not tail.startswith("/"):
                return n
    return n[i:] if tail == "" or tail.startswith("/") else n


def _validate_cov_record(path: str, record: dict) -> None:
    """Validate a coverage record's executed_lines and missing_lines.

    Both must be lists of ints (possibly empty). Raises CoverageGateError
    if the shape is wrong, preventing malformed data from silently yielding
    a green verdict. Coverage.py guarantees this shape, so a deviation means
    the JSON was tampered with, misread, or produced by a non-standard tool.
    """
    if not isinstance(record, dict):
        raise CoverageGateError(
            f"coverage record for {path} is {type(record).__name__}, expected object"
        )
    for key in ("executed_lines", "missing_lines"):
        val = record.get(key)
        if val is None:
            raise CoverageGateError(
                f"coverage record for {path} is missing {key!r}"
            )
        if not isinstance(val, list):
            raise CoverageGateError(
                f"coverage record for {path}: {key!r} is {type(val).__name__}, "
                f"expected list"
            )
        for item in val:
            if not isinstance(item, int):
                raise CoverageGateError(
                    f"coverage record for {path}: {key!r} contains "
                    f"{type(item).__name__} ({item!r}), expected int"
                )
    # RG-53: branch-arc keys are OPTIONAL (present only when the coverage
    # JSON was produced with branch measurement on) but, when present, must
    # be a list of [source_line, target_line] int pairs — coverage.py's own
    # shape (jsonreport.py `_convert_branch_arcs`). Absent/empty means "no
    # branch data", never "malformed"; a record that HAS the key but the
    # wrong shape is still tampered/misread data, same as the line keys.
    for key in ("missing_branches", "executed_branches"):
        val = record.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            raise CoverageGateError(
                f"coverage record for {path}: {key!r} is {type(val).__name__}, "
                f"expected list"
            )
        for item in val:
            if (
                not isinstance(item, (list, tuple))
                or len(item) != 2
                or not all(isinstance(x, int) for x in item)
            ):
                raise CoverageGateError(
                    f"coverage record for {path}: {key!r} contains a "
                    f"malformed arc {item!r}, expected [source_line, target_line]"
                )


@dataclass
class Verdict:
    uncovered: dict[str, set[int]]
    changed_executable: int
    covered: int
    pct: float
    fail_under: float
    files_missing_coverage: list[str] = field(default_factory=list)
    # RG-53: branch totals, scoped to the SAME changed+executable lines the
    # line-level counts above cover (never the whole file) — "beside lines",
    # not a second independent denominator.
    branches_total: int = 0
    branches_missed: int = 0
    # Changed lines that DID execute but left an arm untaken — a subset of
    # `uncovered`, kept separately so callers can distinguish "never ran"
    # from "ran, one branch didn't" without re-deriving it.
    branch_partial_lines: dict[str, set[int]] = field(default_factory=dict)
    # RW-5: True exactly when `changed_executable == 0` — a diff that
    # touches no executable line under `--source`. `pct`/`passed` stay the
    # pure 0/0-is-100% classification (unchanged, so existing callers of
    # `evaluate()` see identical numbers); THIS is the flag a caller must
    # check first, because a 0/0 must never be reported or serialized as a
    # plain 100% pass.
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return self.pct >= self.fail_under

    @property
    def verdict(self) -> str:
        """Machine-readable tri-state for any JSON/report surface: never
        derive pass/fail from `pct`/`passed` alone for that purpose — a 0/0
        diff must always read `"skipped"` here, never `"ok"` (RW-5)."""
        if self.skipped:
            return "skipped"
        return "ok" if self.passed else "fail"


def _branch_maps(cov: dict) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Per-source-line branch-arc maps from one coverage.py file record.

    Returns `(missing_by_line, all_by_line)`: `missing_by_line[ln]` is the
    set of untaken target lines from source line `ln`; `all_by_line[ln]` is
    every target (taken or not) from `ln`. A record with no branch data
    (coverage collected without `--cov-branch`, or `missing_branches` absent)
    yields two empty maps — every changed line's branch check is then a
    no-op and behavior is identical to the pre-RG-53 line-only gate.
    """
    missing_by_line: dict[int, set[int]] = defaultdict(set)
    all_by_line: dict[int, set[int]] = defaultdict(set)
    for src, tgt in cov.get("missing_branches") or ():
        missing_by_line[src].add(tgt)
        all_by_line[src].add(tgt)
    for src, tgt in cov.get("executed_branches") or ():
        all_by_line[src].add(tgt)
    return missing_by_line, all_by_line


def evaluate(
    added: dict[str, set[int]],
    coverage_files: dict[str, dict],
    source_prefix: str = "run-gate-project",
    fail_under: float = 100.0,
) -> Verdict:
    """Pure heart: intersect changed lines with coverage classification.

    A changed line counts toward the denominator only if coverage deems it
    executable (in executed ∪ missing). Changes to files outside source_prefix
    (this directory IS the source tree) are ignored. Non-Python files under the
    source prefix are also ignored (coverage.py measures only .py modules).

    Each coverage record is validated: missing_lines and executed_lines must
    be lists of ints. A record with wrong types (e.g. strings, None) is
    treated as a CoverageGateError to prevent silent green verdicts from
    malformed data.
    """
    prefix = os.path.normpath(source_prefix).replace(os.sep, "/")
    cov_by_norm: dict[str, dict] = {
        _rel_to_source(k, prefix): v for k, v in coverage_files.items()
    }
    total_changed_exec = 0
    total_covered = 0
    branches_total = 0
    branches_missed = 0
    uncovered: dict[str, set[int]] = {}
    branch_partial_lines: dict[str, set[int]] = {}
    files_missing: list[str] = []
    for path, lines in added.items():
        npath = _rel_to_source(path, prefix)
        if not (npath == prefix or npath.startswith(prefix + "/")):
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
        # Validate coverage record shape: executed_lines and missing_lines
        # must be lists of ints (and, if present, the branch-arc keys must be
        # int pairs). This prevents malformed data from silently yielding a
        # green verdict.
        _validate_cov_record(npath, cov)
        missing = set(cov.get("missing_lines", []))
        executed = set(cov.get("executed_lines", []))
        executable = missing | executed
        changed_exec = lines & executable
        missing_by_line, all_by_line = _branch_maps(cov)
        # RG-53: a changed line that DID execute but left an arm untaken
        # (present in `missing_by_line`, absent from the never-executed
        # `missing` set) counts as uncovered too — the whole point of
        # `--cov-branch` for the diff judge.
        line_never_ran = changed_exec & missing
        branch_partial = {
            ln for ln in (changed_exec - missing) if missing_by_line.get(ln)
        }
        unc = line_never_ran | branch_partial
        total_changed_exec += len(changed_exec)
        total_covered += len(changed_exec) - len(unc)
        if unc:
            uncovered[npath] = unc
        if branch_partial:
            branch_partial_lines[npath] = branch_partial
        for ln in changed_exec:
            arms = all_by_line.get(ln)
            if arms:
                branches_total += len(arms)
                branches_missed += len(missing_by_line.get(ln, ()))
    pct = 100.0 if total_changed_exec == 0 else 100.0 * total_covered / total_changed_exec
    return Verdict(
        uncovered=uncovered,
        changed_executable=total_changed_exec,
        covered=total_covered,
        pct=pct,
        fail_under=fail_under,
        files_missing_coverage=sorted(files_missing),
        branches_total=branches_total,
        branches_missed=branches_missed,
        branch_partial_lines=branch_partial_lines,
        skipped=total_changed_exec == 0,
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
    # RG-40: diff against the WORKING TREE, not HEAD. The coverage.json this
    # gate cross-references is generated from whatever bytes are actually on
    # disk (`--allow-dirty` runs the suite there directly) -- diffing against
    # committed HEAD instead reports added-line numbers for a DIFFERENT set
    # of bytes than the ones coverage was measured against, offset by
    # whatever the working tree added above them (measured live: the same
    # code read 175/177 (98.9%) dirty vs 153/153 (100.0%) once committed).
    # A single-ref `git diff <base_rev>` compares base_rev to the working
    # tree directly (staged + unstaged), which is byte-identical to `base_rev
    # HEAD` on a CLEAN tree (working tree == HEAD there) and correct on a
    # dirty one -- unconditional, no dirty/clean branch to keep in sync.
    out = _git(
        repo,
        ["diff", "--relative", "--unified=0", base_rev, "--", source],
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


def _is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    """`True` iff `ancestor` is an ancestor of (or equal to) `descendant`.

    `git merge-base --is-ancestor` exits 0 (yes) or 1 (no) as ordinary,
    non-error outcomes — only >1 is a real git failure — so this does NOT
    go through `_git`, which treats any nonzero exit as `CoverageGateError`.
    """
    proc = subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True, text=True,
    )
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    raise CoverageGateError(
        f"git merge-base --is-ancestor failed ({proc.returncode}): "
        f"{proc.stderr.strip()[:200]}"
    )


def _base_relation(repo: str, base_rev: str, head_rev: str) -> str:
    """Describe HEAD's relationship to the resolved base, for the 0/0
    notice (RW-5). "HEAD is on the base" when HEAD is an ancestor of (or
    equal to) the resolved base — the exact shape `./run-gate.py selftest`
    hits on `main` itself, where merge-base(main, HEAD) == HEAD. Otherwise
    HEAD is some N commits ahead of the base and none of those N commits
    touched an executable source line under `--source`.
    """
    if _is_ancestor(repo, head_rev, base_rev):
        return "HEAD is on the base"
    count = _git(repo, ["rev-list", "--count", f"{base_rev}..{head_rev}"]).strip()
    return (
        f"HEAD is {count} commits ahead of the base; the diff touches no "
        "executable source line"
    )


def _empty_diff_notice(
    base_rev: str,
    source: str,
    relation: str,
    refuse_empty_diff: bool,
) -> tuple[str, str]:
    """RW-5: describe a 0/0 changed-executable-lines verdict.

    Caller must already know `changed_executable == 0` before calling this
    (it does no such check itself). Returns `(verdict, message)`:

    - Default (`refuse_empty_diff=False`): `("skipped", <line>)` — the CLI
      prints `<line>` to STDOUT and exits 0. The false-green hazard RG-51/
      RG-54 describe is a WRONG BASE hiding real source changes; the judge
      cannot tell that apart from "this change genuinely touches no source
      line" by the zero alone, so the zero is made VISIBLE and named
      (which base, how HEAD relates to it) rather than silently printing a
      plain 100% pass OR blocking every gate run that happens not to touch
      the judged file.
    - Opt-in (`refuse_empty_diff=True`, `--refuse-empty-diff`): `("error",
      <line>)` — the CLI prints `<line>` to STDERR and exits 2, naming the
      three known routes to a false 0/0 (none of them "nothing to judge"):
      a merge commit's first-parent base already containing every changed
      line (RG-54), a stale worktree/base ref that no longer reflects real
      history (RG-51), or reverted work that cancels out the very lines it
      re-touches (RG-51 round 5).

    Pure: takes the already-resolved `relation` string (see `_base_relation`)
    rather than doing git I/O itself, so it is unit-testable without a repo.
    """
    base_short = base_rev[:10]
    if refuse_empty_diff:
        return "error", (
            f"diff-coverage ERROR: 0 changed executable lines under {source!r} "
            f"between base {base_short} and HEAD ({relation}) -- refusing an "
            "empty-diff PASS (--refuse-empty-diff). Known routes to 0/0, none "
            "of them \"nothing to judge\": (1) HEAD is a merge commit and "
            "first-parent base resolution already contains every changed "
            "line (RG-54); (2) a stale worktree/base ref that no longer "
            "reflects real history (RG-51); (3) reverted work that cancels "
            "out the very lines it re-touches (RG-51 round 5)."
        )
    return "skipped", (
        f"diff-coverage SKIPPED: 0 changed executable lines under {source!r} "
        f"between {base_short} and HEAD ({relation})"
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run-gate-project/tools/coverage_gate.py",
        description="Fail when changed source lines are uncovered.",
    )
    p.add_argument("--coverage-json", required=True,
                    help="path to `coverage json` output")
    p.add_argument("--base", default="main",
                    help="ref the change is measured against (default: main)")
    p.add_argument("--source", default="run-gate-project/run-gate.py",
                   help="source path prefix (default: run-gate-project/run-gate.py)")
    p.add_argument("--fail-under", type=float, default=100.0,
                   help="minimum %% of changed executable lines (default: 100)")
    p.add_argument("--repo", default=".",
                    help="git repo/worktree (default: cwd)")
    p.add_argument("--refuse-empty-diff", action="store_true", default=False,
                   help="treat a 0/0 changed-executable-lines diff as a hard "
                        "failure (exit 2), naming the three known routes to "
                        "a false 0/0, instead of the default SKIPPED notice "
                        "(exit 0) -- opt-in (RW-5)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        base_rev = _resolve_base(args.repo, args.base)
        head_rev = _git(args.repo, ["rev-parse", "HEAD"]).strip()
        added = _git_added_lines(args.repo, base_rev, args.source)
        coverage_files = _load_coverage(args.coverage_json)
    except CoverageGateError as exc:
        print(f"diff-coverage ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        v = evaluate(added, coverage_files, args.source, args.fail_under)
    except CoverageGateError as exc:
        print(f"diff-coverage ERROR: {exc}", file=sys.stderr)
        return 2

    if v.changed_executable == 0:
        try:
            relation = _base_relation(args.repo, base_rev, head_rev)
        except CoverageGateError as exc:
            print(f"diff-coverage ERROR: {exc}", file=sys.stderr)
            return 2
        kind, message = _empty_diff_notice(
            base_rev, args.source, relation, args.refuse_empty_diff
        )
        print(message, file=sys.stderr if kind == "error" else sys.stdout)
        return 2 if kind == "error" else 0

    branch_note = (
        f"; branches {v.branches_total - v.branches_missed}/{v.branches_total} taken"
        if v.branches_total
        else ""
    )
    if v.passed:
        print(
            f"diff-coverage OK: {v.covered}/{v.changed_executable} changed "
            f"executable lines covered ({v.pct:.1f}% ≥ {v.fail_under:.1f}% floor)"
            f"{branch_note}"
        )
        return 0

    print(
        f"diff-coverage FAIL: {v.covered}/{v.changed_executable} changed executable "
        f"lines covered ({v.pct:.1f}% < {v.fail_under:.1f}% floor){branch_note}. "
        "Uncovered changed lines:"
    )
    for path in sorted(v.uncovered):
        tag = " [file unmeasured]" if path in v.files_missing_coverage else ""
        partial = v.branch_partial_lines.get(path, set())
        rendered = ", ".join(
            f"{ln}(branch)" if ln in partial else str(ln)
            for ln in sorted(v.uncovered[path])
        )
        print(f"  {path}:{tag} [{rendered}]")
    print("Add a test that exercises these lines, or mark a genuinely "
          "unreachable line with `# pragma: no cover`.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
