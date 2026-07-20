"""Mechanical diff-coverage gate — D-064 layer 2 (docs/spec-flow-stages.md:199).

Test-completeness enforcement without an LLM: fail the gate when a change adds or
edits *executable* source lines that no test exercises. Implementer-generated
tests are structurally happy-path-biased, so a new branch/guard often ships with
zero coverage (the B5 `_attempt_scan` eligibility-tuple gap is the motivating
case). This is the deterministic floor that catches it — it "would have caught
the B5 gap" the way self_review's negative-check (layer 1) does, but mechanically.

Two proven building blocks, no reinvention of either:
  * `git diff --relative -U0` tells us exactly which NEW-side line numbers a change
    added/edited, per source file (the classic added-line hunk walk);
  * `coverage.py`'s JSON report (`coverage json`) already classifies every line as
    executed / missing / non-executable. We never re-derive "is this line code?" —
    a line the change touched that is in NEITHER executed nor missing is a comment
    or blank and is correctly ignored (the load-bearing discriminator: editing a
    docstring is not an uncovered-code event).

The verdict is the intersection: a changed line that coverage reports as *missing*
(executable-but-not-run) is an uncovered change → fail, unless the covered
fraction still clears an explicit `--fail-under` floor (the "reliable floor
coverage-% can enforce" the spec names; default 100 = every changed line must run).

The pure core (`parse_added_lines`, `evaluate`) takes plain data — diff text and a
coverage-files mapping — so it is unit-tested against crafted inputs with no git or
coverage install needed. `_resolve_base` / `_git_added_lines` / `_load_coverage` are
the thin I/O boundary the gate command drives:

    coverage run --source=src/nyxloom -m pytest tests -q \\
      && coverage json -o cov.json -q \\
      && python -m nyxloom.coverage_gate --base main --coverage-json cov.json

Base resolution makes BOTH gate phases meaningful with one command:
  * feature branch (HEAD is a normal tip) → diff vs merge-base(base, HEAD): the
    fork-point delta the branch introduced (implementation-time enforcement);
  * post-merge (HEAD is the merge commit, ≥2 parents) → diff vs its FIRST parent:
    exactly the merged delta, re-verified against the merged tree's own test run.
An empty delta (nothing changed under --source) is a clean pass, never a false fail.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

# New-side hunk header: `@@ -a,b +c,d @@`. We only need the new-side (+c,d); the
# count defaults to 1 when omitted (`@@ -a +c @@`).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class CoverageGateError(Exception):
    """A gate I/O boundary failed (git or coverage-json), distinct from a
    coverage *verdict* failure — the CLI maps it to exit 2, not 1."""


def parse_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Walk `git diff --unified=0` output → {new-side path: {added line nums}}.

    Only new-side additions count: a `+` body line is an added/edited line at the
    running new-side number; a `-` body line (a pure deletion) advances nothing on
    the new side and is ignored; a deleted file (`+++ /dev/null`) contributes no
    added lines. Paths keep git's `a/`|`b/` stripped; with `--relative` they are
    already relative to the invocation cwd."""
    added: dict[str, set[int]] = {}
    current: str | None = None
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            else:
                # strip a leading `b/` (git's default dst prefix); tolerate its
                # absence (e.g. `--no-prefix`).
                current = target[2:] if target.startswith("b/") else target
            continue
        if line.startswith("--- "):
            continue  # old-side header; never a source path we count
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
            continue  # deletion: no new-side advance
        else:
            new_lineno += 1  # context line (only appears with -U>0); advance
    return added


def _rel_to_source(path: str, source_prefix: str) -> str:
    """Normalize any spelling of a source path to the canonical
    `<source_prefix>/...` tail, so a git path (`nyxloom/src/nyxloom/x.py`,
    relative to the vbpub repo root) and a coverage-json key (`src/nyxloom/x.py`,
    relative to the nyxloom cwd) and an absolute path all compare equal. First
    occurrence of the prefix wins; if the prefix is absent the normalized path is
    returned unchanged (it will simply not match any source file)."""
    n = os.path.normpath(path).replace(os.sep, "/")
    i = n.find(source_prefix)
    return n[i:] if i != -1 else n


@dataclass
class Verdict:
    uncovered: dict[str, set[int]]  # source path -> uncovered changed line nums
    changed_executable: int         # count of changed lines coverage deems code
    covered: int                    # of those, how many executed
    pct: float                      # 100*covered/changed_executable (100 if none)
    fail_under: float
    files_missing_coverage: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.pct >= self.fail_under


def evaluate(
    added: dict[str, set[int]],
    coverage_files: dict[str, dict],
    source_prefix: str = "src/nyxloom",
    fail_under: float = 100.0,
) -> Verdict:
    """Pure heart: intersect changed lines with coverage classification.

    A changed line counts toward the denominator only if coverage deems it
    executable (in executed ∪ missing); of those, membership in `missing` means
    uncovered. Changes to files outside `source_prefix` (tests, docs, config) are
    ignored — the gate enforces coverage of SOURCE, not of everything touched.

    B63 2026-07-20 (false positive found by the gate's own run): a changed file
    under `source_prefix` that is not PYTHON is ignored too. coverage.py measures
    `.py` modules and nothing else, so a data file living beside the code — a
    JSON schema, a template, a .toml fixture — can never appear in the coverage
    report. Treating its absence as "unmeasured, therefore uncovered" flagged
    edits to `schemas/nyxloom-config.schema.json` as a test-coverage failure, a
    verdict no test could ever clear. Unmeasur*able* is not unmeasur*ed*."""
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
            continue  # not a source file — no coverage obligation
        if not npath.endswith(".py"):
            continue  # not measurable by coverage.py at all — see docstring
        cov = cov_by_norm.get(npath)
        if cov is None:
            # A source file changed but coverage never saw it. With
            # `coverage run --source=<prefix>` every source file appears (even at
            # 0% run), so this means the file is genuinely unmeasured — treat its
            # changed lines as uncovered (cannot prove they ran) and surface it
            # loudly rather than silently passing untested new code.
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
# thin I/O boundary (git + coverage-json), driven by the gate command
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
    """Merge commit (HEAD has ≥2 parents) → its FIRST parent (the merged delta,
    post-merge); otherwise merge-base(base, HEAD) (the fork-point delta, feature
    branch). See the module docstring for why one command serves both phases."""
    tokens = _git(repo, ["rev-list", "--parents", "-n", "1", "HEAD"]).split()
    if len(tokens) >= 3:  # HEAD sha + ≥2 parent shas
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
        prog="python -m nyxloom.coverage_gate",
        description="Fail when changed source lines are uncovered (D-064-L2).",
    )
    p.add_argument("--coverage-json", required=True,
                   help="path to `coverage json` output for the test run")
    p.add_argument("--base", default="main",
                   help="ref the change is measured against (default: main)")
    p.add_argument("--source", default="src/nyxloom",
                   help="source path prefix the gate enforces (default: src/nyxloom)")
    p.add_argument("--fail-under", type=float, default=100.0,
                   help="minimum %% of changed executable lines covered (default: 100)")
    p.add_argument("--repo", default=".",
                   help="git repo/worktree to run diff in (default: cwd)")
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
