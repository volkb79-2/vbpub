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

NO MEASUREMENT (exit 3, 2026-07-27): "0/0 changed executable lines covered
(100.0%)" reads identically whether the gate measured everything and found
nothing to cover, or measured NOTHING at all -- and the second case is a real
failure mode, not a hypothetical: a gate run against an uncommitted tree (the
diff is computed from committed HEAD, so working-tree edits are invisible to
it) and a gate run where `--base main` resolves to `main` itself (no delta by
construction) both render that exact string. Neither is a tool failure
(`CoverageGateError`, exit 2) and neither is a real verdict (0 or 1) -- they
are a THIRD outcome: the measurement never happened, so no percentage
computed from it means anything. `_check_measurable` is the guard, checked
before `evaluate` ever runs (so `evaluate` stays pure and never sees either
failure mode); it does NOT fire on a genuinely empty delta with a clean,
committed tree -- a docs-only commit still passes at 0/0, exactly as before.
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


class NoMeasurementError(Exception):
    """No measurement was possible at all -- distinct from BOTH a rendered
    verdict (`Verdict`/`evaluate`, exit 0 or 1) and a broken tool
    (`CoverageGateError`, exit 2). Raised by `_check_measurable` for a
    precondition that makes any diff-coverage percentage meaningless before
    `evaluate` is even called: uncommitted changes under `--source` (the
    `base..HEAD` diff cannot see them) or a resolved base identical to HEAD
    (no delta, by construction). The CLI maps this to its own exit code (3).
    Conflating this with exit 2 ("the tool broke, maybe retry") or with a
    passing verdict ("nothing to fix") is exactly the ambiguity this module
    exists to eliminate -- see the module docstring's "NO MEASUREMENT"
    section."""


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
    # GA5 2026-07-27: changed lines coverage.py EXCLUDED from measurement --
    # i.e. carrying `pragma: no cover`. (Every mention of that token in this
    # module deliberately OMITS the leading '#'. coverage.py's exclude regex
    # matches the full token anywhere on a line -- including inside a comment
    # or a string literal -- so writing it out in full would exclude the very
    # line explaining the feature. This guard caught exactly that on its own
    # first run: three lines of prose about pragmas, excluded by their own
    # example. Do not "fix" the missing '#'.)
    # Deliberately a separate bucket from
    # `uncovered`, because it is a different failure with a different remedy:
    # an uncovered line is untested, an excluded line is *unmeasured by
    # request*. Excluded lines appear in neither `executed_lines` nor
    # `missing_lines`, so before this they fell out of BOTH the numerator and
    # the denominator -- adding a pragma made the percentage go UP, and no
    # coverage floor, however strict, could ever surface them.
    excluded: dict[str, set[int]] = field(default_factory=dict)
    allow_excluded: bool = False

    @property
    def excluded_count(self) -> int:
        return sum(len(v) for v in self.excluded.values())

    @property
    def passed(self) -> bool:
        if self.excluded and not self.allow_excluded:
            return False
        return self.pct >= self.fail_under


def evaluate(
    added: dict[str, set[int]],
    coverage_files: dict[str, dict],
    source_prefix: str = "src/nyxloom",
    fail_under: float = 100.0,
    allow_excluded: bool = False,
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
    verdict no test could ever clear. Unmeasur*able* is not unmeasur*ed*.

    GA5 2026-07-27: changed lines carrying `pragma: no cover` are collected
    separately and fail the gate unless `allow_excluded`. Unmeasur*able* is not
    unmeasur*ed* (above) — but a pragma is neither: it is a request to stop
    measuring, and honouring it on a CHANGED line lets a change opt out of the
    very gate it must pass. This was found in the wild: an implementer agent
    added 11 pragmas to a file that had zero, and the gate reported "22/22
    changed executable lines, 100%" on a 532-line diff. Nothing about a
    percentage floor can catch that, because the exclusions shrink the
    denominator. Pre-existing pragmas on UNCHANGED lines are untouched — only
    lines this diff actually added or modified are held to it."""
    prefix = os.path.normpath(source_prefix).replace(os.sep, "/")
    cov_by_norm: dict[str, dict] = {
        _rel_to_source(k, prefix): v for k, v in coverage_files.items()
    }
    total_changed_exec = 0
    total_covered = 0
    uncovered: dict[str, set[int]] = {}
    excluded: dict[str, set[int]] = {}
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
        # GA5: an excluded line is in NEITHER executed nor missing, so it would
        # otherwise vanish from the ratio entirely. Collect it before the
        # intersection below silently drops it.
        exc = lines & set(cov.get("excluded_lines", []))
        if exc:
            excluded[npath] = exc
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
        excluded=excluded,
        allow_excluded=allow_excluded,
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


def _head_rev(repo: str) -> str:
    return _git(repo, ["rev-parse", "HEAD"]).strip()


def _dirty_paths_under_source(repo: str, source: str) -> list[str]:
    """Paths with uncommitted changes -- staged OR unstaged OR untracked --
    scoped to `source`. `git status --porcelain` reports the index AND the
    worktree in one pass (unlike `git diff`, which only ever shows one side),
    so a staged-but-uncommitted change (`git add` with no `git commit`) is
    caught exactly like an unstaged one: both are equally invisible to a
    `base..HEAD` diff. The `--` pathspec scopes the query to what the gate
    actually measures, so a dirty file OUTSIDE `source` (an in-progress doc
    edit, say) never trips this.

    `git status --porcelain` ALWAYS reports paths relative to the repo TOP
    LEVEL, never to cwd (unlike `git diff --relative`, which is why
    `_git_added_lines` above passes that flag explicitly) -- when nyxloom
    self-hosts as a subdirectory of the vbpub monorepo, that means a raw
    status line reads `nyxloom/src/nyxloom/x.py`, not `src/nyxloom/x.py`.
    Route every path through `_rel_to_source` (the same normalizer
    `evaluate` uses for coverage-json keys) so callers always see the
    `source`-prefixed spelling regardless of where the repo top level sits."""
    prefix = os.path.normpath(source).replace(os.sep, "/")
    out = _git(repo, ["status", "--porcelain", "--", source])
    paths: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:  # rename/copy: "old -> new" -- only the new path still exists
            path = path.split(" -> ", 1)[1]
        paths.add(_rel_to_source(path, prefix))
    return sorted(paths)


def _check_measurable(repo: str, base_rev: str, source: str) -> None:
    """Raise `NoMeasurementError` when nothing downstream (the added-lines
    diff, the coverage intersection) could produce a verdict worth trusting,
    however it happens to render. Two independent preconditions, checked
    BEFORE any diff is computed and before `evaluate` ever runs:

    1. Uncommitted changes under `source` -- a `base_rev..HEAD` diff cannot
       see them, so "0 changed lines" from that diff would mean "the diff
       can't see what's actually being tested", not "nothing changed".
    2. `base_rev == HEAD` -- there is no delta between the two sides being
       diffed, by construction (this is what `--base main` resolving TO
       `main` itself produces), so any percentage computed from it is
       vacuous regardless of what it says.

    A genuinely empty delta on a clean, committed tree (a docs-only or
    test-only commit) trips NEITHER check and reaches `evaluate` normally,
    which is where the legitimate 0/0 pass is decided -- this function only
    ever rules out measurements that could not have been trustworthy in the
    first place."""
    dirty = _dirty_paths_under_source(repo, source)
    if dirty:
        raise NoMeasurementError(
            f"{len(dirty)} uncommitted file(s) under {source} -- the gate "
            f"diffs committed HEAD, so these are invisible. Commit, then "
            f"re-run. Affected: {', '.join(dirty)}"
        )
    head_rev = _head_rev(repo)
    if base_rev == head_rev:
        raise NoMeasurementError(
            f"resolved base ({base_rev[:12]}) IS HEAD -- there is no delta "
            f"to measure. --base should resolve to an ANCESTOR of HEAD (the "
            f"branch's fork point); check that HEAD is not already the tip "
            f"of that ref."
        )


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
    p.add_argument("--allow-excluded", action="store_true",
                   help="permit `pragma: no cover` on CHANGED lines (default: "
                        "off -- a pragma on a changed line fails the gate). Opting "
                        "in belongs in the project's declared gate argv, where it "
                        "is a visible, reviewable config change.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        base_rev = _resolve_base(args.repo, args.base)
        _check_measurable(args.repo, base_rev, args.source)
        added = _git_added_lines(args.repo, base_rev, args.source)
        coverage_files = _load_coverage(args.coverage_json)
    except NoMeasurementError as exc:
        print(f"diff-coverage NO MEASUREMENT: {exc}", file=sys.stderr)
        return 3
    except CoverageGateError as exc:
        print(f"diff-coverage ERROR: {exc}", file=sys.stderr)
        return 2

    v = evaluate(added, coverage_files, args.source, args.fail_under,
                 allow_excluded=args.allow_excluded)
    if v.passed:
        note = (f" [{v.excluded_count} changed line(s) excluded by pragma, "
                f"allowed via --allow-excluded]" if v.excluded else "")
        print(
            f"diff-coverage OK: {v.covered}/{v.changed_executable} changed "
            f"executable lines covered ({v.pct:.1f}% ≥ {v.fail_under:.1f}% floor)"
            f"{note}"
        )
        return 0

    if v.excluded and not v.allow_excluded:
        print(
            f"diff-coverage FAIL: {v.excluded_count} changed line(s) are EXCLUDED "
            f"from coverage by `pragma: no cover`:"
        )
        for path in sorted(v.excluded):
            print(f"  {path}: {sorted(v.excluded[path])}")
        print("A pragma on a CHANGED line opts that line out of the very gate the "
              "change has to pass -- and because excluded lines leave the "
              "denominator, adding one makes the reported percentage go UP. Test "
              "the line instead. If it is genuinely unreachable, opt in "
              "deliberately by adding --allow-excluded to the project's declared "
              "gate argv: a visible, reviewable config change, not a comment.")
    if v.pct < v.fail_under:
        print(
            f"diff-coverage FAIL: {v.covered}/{v.changed_executable} changed executable "
            f"lines covered ({v.pct:.1f}% < {v.fail_under:.1f}% floor). Uncovered changed lines:"
        )
        for path in sorted(v.uncovered):
            tag = " [file unmeasured]" if path in v.files_missing_coverage else ""
            print(f"  {path}:{tag} {sorted(v.uncovered[path])}")
        print("Add a test that exercises these lines.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
