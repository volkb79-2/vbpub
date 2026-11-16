#!/usr/bin/env python3
"""Select the R2 candidates whose mutation sites fall inside a release diff (PWMCP-71).

The `r2` lane plans the WHOLE target. A release-to-release campaign usually wants only the
code that release changed, which is a supported SUBSET run: the candidates left out are
reported not started and run-gate records the run as `selective`, so it is never a
whole-target R2 PASS.

Two steps that are easy to get wrong by hand, and are therefore computed here:

* reading the NEW-file line ranges out of a `git diff -U0` hunk header
  (`@@ -a,b +c,d @@` -> lines ``c .. c + d - 1``; ``d`` omitted means one line, and a pure
  deletion hunk ``d == 0`` covers no lines at all);
* matching a candidate's ``path``/``lineno`` from ``assay plan r2 --json`` against those
  ranges, with the diff's ``a/``/``b/`` prefixes stripped.

**The two path forms.** A real plan carries REPOSITORY-ROOT-relative paths
(``pwmcp/client/src/pwmcp_client/cli.py``), because assay anchors every path on the git
repository top, never on the project directory it was invoked from — measured, not
assumed, and pinned by `tests/fixtures/r2-plan/real-plan-slice.json`. A `git diff` can
arrive in that same form (``git diff -U0 <base>..<release> -- <path>`` from the project
directory) or in a PROJECT-relative form (``git diff --relative``, or a diff produced
from inside the project directory). The two do not match, so this tool resolves the form
EXPLICITLY and says which one it used:

1. ``--project-prefix P`` states the fact (the project directory inside the repository,
   e.g. ``pwmcp/``); diff paths are then prefixed with it.
2. Otherwise the paths are taken AS GIVEN (the repository-root form).
3. If that selects nothing and the plan makes the project prefix UNAMBIGUOUS (every diff
   path suffix-matches exactly one plan path, and every such match shares one prefix), the
   tool uses that prefix and DISCLOSES it on stderr. It is a derived fact, printed, never
   a silent guess.
4. Otherwise it fails and lists the forms it tried, with the two remedies.

Usage (on the release checkout):

    assay plan r2 --json > plan.json
    git diff -U0 <base>..<release> -- <source paths> > release.diff
    python3 tools/r2/select_changed_candidates.py --plan plan.json --diff release.diff \\
        --out .run-gate/only-candidates-<scope>.txt

The output is one candidate id per line, in plan order, ready for
``run-gate r2 --only-candidates FILE``. It prints a one-line summary (selected/planned) to
stderr so the campaign evidence can record it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Diff path (prefix stripped) -> inclusive NEW-file line ranges the diff touched.

    The path form is whatever the diff carries: repository-root-relative for a plain
    ``git diff`` from the project directory, project-relative for ``git diff --relative``.
    ``resolve_path_form`` is what reconciles it with the plan.
    """

    ranges: dict[str, list[tuple[int, int]]] = {}
    path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t", 1)[0].strip()
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
            if path is not None:
                ranges.setdefault(path, [])
            continue
        if path is None or not line.startswith("@@"):
            continue
        match = HUNK.match(line)
        if match is None:
            raise ValueError(f"unparseable hunk header: {line}")
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        if count <= 0:  # a pure deletion touches no line in the new file
            continue
        ranges[path].append((start, start + count - 1))
    return {p: r for p, r in ranges.items() if r}


def select_candidates(plan: dict, ranges: dict[str, list[tuple[int, int]]]) -> list[str]:
    """Candidate ids whose (path, lineno) fall inside a changed range, in plan order."""

    selected: list[str] = []
    candidates = plan.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("the plan has no 'candidates' list (is this `assay plan r2 --json` output?)")
    for candidate in candidates:
        path = candidate.get("path")
        lineno = candidate.get("lineno")
        candidate_id = candidate.get("id")
        if not (isinstance(path, str) and isinstance(lineno, int) and isinstance(candidate_id, str)):
            raise ValueError(f"malformed candidate in the plan: {candidate!r}")
        for low, high in ranges.get(path, ()):
            if low <= lineno <= high:
                selected.append(candidate_id)
                break
    return selected


def _plan_paths(plan: dict) -> set[str]:
    candidates = plan.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("the plan has no candidates, so there is nothing to select")
    paths = {candidate.get("path") for candidate in candidates}
    if not all(isinstance(path, str) and path for path in paths):
        raise ValueError("every plan candidate must carry a non-empty 'path'")
    return {path for path in paths if isinstance(path, str)}


def derive_project_prefix(
    ranges: dict[str, list[tuple[int, int]]], plan_paths: set[str]
) -> str | None:
    """The single project prefix that maps EVERY diff path onto exactly one plan path.

    ``None`` when the diff is not uniformly project-relative: an ambiguous or partial
    match is not a derivation, and the caller must state ``--project-prefix`` instead.
    """

    prefixes: set[str] = set()
    for diff_path in ranges:
        matches = [path for path in plan_paths if path.endswith("/" + diff_path)]
        if len(matches) > 1:
            return None  # two plan paths end with this diff path: no single answer
        if not matches:
            continue  # a changed file the lane does not judge; it selects nothing either way
        prefixes.add(matches[0][: len(matches[0]) - len(diff_path)])
    return prefixes.pop() if len(prefixes) == 1 else None


def prefix_ranges(
    ranges: dict[str, list[tuple[int, int]]], prefix: str
) -> dict[str, list[tuple[int, int]]]:
    """Re-key a project-relative diff under the plan's repository-root paths.

    ``prefix`` is canonicalised to one trailing slash: ``--project-prefix pwmcp`` and
    ``pwmcp/`` are the same fact about the checkout, so neither form may concatenate into
    ``pwmcpclient/`` nor double a slash. A prefix a diff already carries is left alone.
    """

    prefix = prefix.rstrip("/") + "/" if prefix else ""
    return {
        path if path.startswith(prefix) else prefix + path: line_ranges
        for path, line_ranges in ranges.items()
    }


def resolve_path_form(
    plan: dict,
    ranges: dict[str, list[tuple[int, int]]],
    project_prefix: str | None,
) -> tuple[dict[str, list[tuple[int, int]]], dict | None, list[str]]:
    """Decide which path form the diff is in, and say so.

    Returns ``(ranges_to_use, disclosure, forms_tried)``: ``disclosure`` is printed when a
    form other than "as given" is used, and ``forms_tried`` is what the refusal lists.
    """

    plan_paths = _plan_paths(plan)
    tried = [f"as given (repository-root-relative): {len(ranges)} diff path(s), "
             f"{len(select_candidates(plan, ranges))} matched"]
    if project_prefix is not None:
        prefixed = prefix_ranges(ranges, project_prefix)
        return prefixed, None, tried + [f"--project-prefix {project_prefix!r}: applied as stated"]
    if select_candidates(plan, ranges):
        return ranges, None, tried
    prefix = derive_project_prefix(ranges, plan_paths)
    if prefix is not None:
        prefixed = prefix_ranges(ranges, prefix)
        matched = len(select_candidates(plan, prefixed))
        disclosure = {
            "form": "project-relative",
            "project_prefix": prefix,
            "matched": matched,
        }
        tried.append(f"project-relative: derived prefix {prefix!r} from the plan, {matched} matched")
        return prefixed, disclosure, tried
    tried.append("project-relative: no single project prefix could be derived from the plan")
    return ranges, None, tried


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", required=True, type=Path, help="`assay plan r2 --json` output")
    parser.add_argument("--diff", required=True, type=Path, help="`git diff -U0 <base>..<release>` output")
    parser.add_argument("--out", required=True, type=Path, help="candidate-id list for --only-candidates")
    parser.add_argument(
        "--project-prefix",
        default=None,
        metavar="PREFIX",
        help=(
            "the project directory inside the repository, e.g. 'pwmcp/' -- state it when the diff "
            "is PROJECT-relative (`git diff --relative`, or a diff made inside the project "
            "directory). Diff paths are then prefixed with it to match the plan's "
            "repository-root-relative paths. Omitted: the diff is read as given, and a "
            "project-relative form is derived from the plan only when that is unambiguous, "
            "with the derived prefix disclosed on stderr"
        ),
    )
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    ranges = changed_line_ranges(args.diff.read_text(encoding="utf-8"))
    ranges, disclosure, forms_tried = resolve_path_form(plan, ranges, args.project_prefix)
    selected = select_candidates(plan, ranges)
    planned = len(plan["candidates"])
    if not selected:
        print(
            f"no candidate falls inside the changed lines of {args.diff.name}; "
            "check the diff base and the paths (a changed-line scope is empty when the release "
            "touched no mutated line)",
            file=sys.stderr,
        )
        for form in forms_tried:
            print(f"  tried {form}", file=sys.stderr)
        print(
            "  the plan's paths are repository-root-relative; if the diff is project-relative, "
            "either produce it from the repository root (git diff -U0 <base>..<release> -- "
            "<project-relative path from the root>) or pass --project-prefix <project dir>",
            file=sys.stderr,
        )
        return 1
    if disclosure is not None:
        print(
            f"diff paths are {disclosure['form']}: prefixed them with "
            f"{disclosure['project_prefix']!r} (derived from the plan, not stated; pass "
            "--project-prefix to say so explicitly)",
            file=sys.stderr,
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(f"{candidate_id}\n" for candidate_id in selected), encoding="utf-8")
    print(f"selected {len(selected)} of {planned} candidates -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
