"""Carve-quality lint, rules L1-L12 (SPEC docs/SPEC.md §6). PACKAGE P01.

INTERFACE CONTRACT (frozen):

- lint_file(path, cfg) -> list[LintFinding]; lint_project(cfg) ->
  dict[str(relpath), list[LintFinding]].
- A parse/schema failure IS finding L1 (severity error) and short-circuits
  the other rules for that file.
- severity: 'error' blocks the carve (exit 1 in CLI); 'warning' reports only.
- Every rule appends findings with its rule id, message, path, and the most
  specific 1-based line it can attribute (frontmatter key line or body line);
  line=None when not attributable.

RULE SEMANTICS (implement exactly; the golden corpus in
tests/fixtures/handoffs/ encodes the P69/P78/P84 incident classes):

L1  error   schema-valid frontmatter; id matches filename stem; project
            matches cfg.project_id; depends_on task refs resolve to an
            existing handoff file or existing statefile (decision D-refs are
            not resolvable here and are skipped); no date-like string in the
            body that is >30 days from today AND labeled as authored date
            (heuristic: lines starting 'Date:' / 'date:').
L2  error   every gate id in frontmatter.gates and oracle.gate exists in
            cfg.gates. ADDITIONALLY: any fenced code block in the body whose
            first word is 'pytest' or 'python' followed by '-m pytest' and
            that is NOT preceded within 3 lines by a declared gate's argv
            rendering is flagged (bare-gate heuristic).
L3  error   >=1 oracle (schema enforces), and each oracle.negative is
            non-trivial: not equal (case-insensitive, stripped) to 'none',
            'n/a', '', or a copy of oracle.observable.
L4  warning contract/oracle enumeration guard: if the body or an
            oracle.observable contains r'\\b(every|all)\\s+\\w+ (field|
            record|column|key|property)s?\\b' (case-insens.) AND that same
            oracle's observable lists >=2 quoted/backticked identifiers,
            flag: 'universal contract with enumerated oracle subset (P78)'.
L5  error   implementer handoffs must not contain reviewer-only
            deliverables: body regex (case-insens.) for 'DECISIONS-INBOX'
            outside a 'do not'/'never' sentence, 'merge --no-ff', 'update
            STATUS.md', 'git merge ' as an instruction to the implementer.
L6  error   oracle deferral: body or oracles matching (case-insens.)
            r'(controller|reviewer|review pass|frontier) (will|should|can)
            (validate|verify|re-?run|confirm)' flags 'acceptance delegated
            to another role (P84)'.
L7  error   every repo-relative path referenced in frontmatter (scope.touch,
            scope.forbid, source.ref) must either exist under cfg.root, be
            declared in scope.touch (files to be created are exempt: only
            source.ref and context references must exist), and any path
            starting with '../' or an absolute path outside cfg.root is
            flagged 'non-resolving reference (P69)'. For the body: markdown
            links/inline code containing '/'-paths starting with '../' or
            '/workspaces/<other-repo>' are flagged as warning.
L8  error   escalate_if entries containing introspective phrasing
            (r'(reflect|consider whether|feel|expertise|confident)') are
            flagged 'non-mechanical escalation trigger (P51)'.
L9  error   if any scope.touch glob/path matches cfg.infra_globs, the
            effective mutexes (fm.effective_mutexes()) must include 'stack'.
L10 warning body+frontmatter token estimate (len(text)//4) over 6000 ->
            warning; over 12000 -> error. Message includes the estimate.
L11 error   body must contain (case-insens.) a worktree path mention
            ('worktree'), a branch name ('branch'), an out-of-scope/forbid
            mention ('out of scope' or 'forbid'), and a context section
            ('context to read' or 'read first').
L12 error   body must contain the BLOCKED rule marker 'BLOCKED:' and must
            not instruct violating project policy (heuristic: 'skip the
            gate', 'without running', 'ignore lint' -> flag).
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import frontmatter, paths
from .config import ProjectConfig
from .types import LintFinding, utc_now


def lint_file(path: Path, cfg: ProjectConfig) -> list[LintFinding]:
    """All findings for one handoff file (see module contract)."""
    findings = []

    # Try to parse the file
    try:
        fm, body = frontmatter.parse_handoff(path)
    except frontmatter.HandoffParseError as e:
        # L1 error: parse/schema failure
        return [LintFinding(
            rule="L1",
            severity="error",
            message=f"parse/schema error: {'; '.join(e.errors)}",
            path=str(path),
            line=e.line
        )]

    # Now that we have parsed FM and body, run all lint rules
    # Store the full text for later checks
    full_text = path.read_text(encoding="utf-8")

    # L1: Schema valid (already done above), id matches filename, project matches, deps resolve, no stale dates
    _check_l1(findings, path, fm, body, cfg)

    # L2: Gate ids exist, no bare pytest
    _check_l2(findings, path, fm, body, cfg)

    # L3: At least 1 oracle with non-trivial negatives
    _check_l3(findings, path, fm)

    # L4: No enumerated oracle under universal contract
    _check_l4(findings, path, fm, body)

    # L5: No reviewer-only deliverables
    _check_l5(findings, path, body)

    # L6: No oracle deferral
    _check_l6(findings, path, fm, body)

    # L7: Paths resolve
    _check_l7(findings, path, fm, body, cfg)

    # L8: Escalate-if mechanical
    _check_l8(findings, path, fm)

    # L9: Infra touches require stack mutex
    _check_l9(findings, path, fm, cfg)

    # L10: Size limits
    _check_l10(findings, path, full_text)

    # L11: Body contains required sections
    _check_l11(findings, path, body)

    # L12: Body contains BLOCKED marker
    _check_l12(findings, path, body)

    # Sort findings by rule then line
    findings.sort(key=lambda f: (f.rule, f.line or 9999))

    return findings


def lint_project(cfg: ProjectConfig) -> dict[str, list[LintFinding]]:
    """relpath -> findings for every discovered handoff (frontmatter.discover_handoffs)."""
    results = {}
    for handoff_path in frontmatter.discover_handoffs(cfg):
        rel_path = str(handoff_path.relative_to(cfg.root))
        results[rel_path] = lint_file(handoff_path, cfg)
    return results


def has_blocking(findings: list[LintFinding]) -> bool:
    return any(f.severity == "error" for f in findings)


# ----- L1 -----
def _check_l1(findings: list[LintFinding], path: Path, fm, body: str, cfg: ProjectConfig) -> None:
    """Check: id matches filename, project matches cfg, deps resolve, no stale dates."""
    # ID matches filename stem
    expected_id = path.stem
    if fm.id != expected_id:
        findings.append(LintFinding(
            rule="L1",
            severity="error",
            message=f"id '{fm.id}' does not match filename stem '{expected_id}'",
            path=str(path)
        ))

    # Project matches
    if fm.project != cfg.project_id:
        findings.append(LintFinding(
            rule="L1",
            severity="error",
            message=f"project '{fm.project}' does not match config '{cfg.project_id}'",
            path=str(path)
        ))

    # Check depends_on resolution
    state_dir = paths.state_dir(cfg.project_id)
    for dep_id in fm.depends_on:
        if dep_id.startswith("D-"):
            # Decision refs are not resolvable here, skip
            continue
        # Task refs should resolve to a handoff or statefile
        dep_file = cfg.root / f"handoff/{dep_id}.md"
        dep_state = state_dir / f"{dep_id}.json" if state_dir.exists() else None
        if not dep_file.exists() and (not dep_state or not dep_state.exists()):
            findings.append(LintFinding(
                rule="L1",
                severity="error",
                message=f"depends_on task ref '{dep_id}' does not resolve to a handoff or statefile",
                path=str(path)
            ))

    # Check for stale date-like strings in body labeled as authored date
    # Heuristic: lines starting with 'Date:' or 'date:'
    today = utc_now().date()
    for i, line in enumerate(body.split("\n"), 1):
        if re.match(r"^\s*[Dd]ate:\s*", line):
            # Try to extract a date
            date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", line)
            if date_match:
                try:
                    date = datetime.strptime(
                        f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}",
                        "%Y-%m-%d"
                    ).date()
                    days_old = (today - date).days
                    if days_old > 30:
                        findings.append(LintFinding(
                            rule="L1",
                            severity="error",
                            message=f"date-like string '{date}' is {days_old} days old (>30)",
                            path=str(path),
                            line=i
                        ))
                except ValueError:
                    pass


# ----- L2 -----
def _check_l2(findings: list[LintFinding], path: Path, fm, body: str, cfg: ProjectConfig) -> None:
    """Check: gate ids exist, no bare pytest/python -m pytest."""
    # Check gate ids
    all_gate_ids = set(fm.gates)
    for oracle in fm.oracles:
        all_gate_ids.add(oracle.gate)

    for gate_id in all_gate_ids:
        if gate_id not in cfg.gates:
            findings.append(LintFinding(
                rule="L2",
                severity="error",
                message=f"gate id '{gate_id}' not declared in project.toml",
                path=str(path)
            ))

    # Check for bare pytest/python -m pytest blocks
    # Build a set of gate argv strings for matching
    gate_argv_strs = {}
    for gate_id, gate_def in cfg.gates.items():
        gate_argv_strs[gate_id] = " ".join(gate_def.argv)

    lines = body.split("\n")
    i = 0
    while i < len(lines):
        # Find fenced code blocks
        if lines[i].startswith("```"):
            fence_start = i
            i += 1
            # Look for the closing fence
            while i < len(lines):
                if lines[i].startswith("```"):
                    # Found closing fence
                    fence_content = "\n".join(lines[fence_start + 1:i])
                    # Check if first word is pytest or python -m pytest
                    first_words = fence_content.strip().split()[:4]
                    is_bare_pytest = False
                    if first_words and first_words[0] == "pytest":
                        is_bare_pytest = True
                    elif len(first_words) >= 3 and first_words[0] == "python" and first_words[1] == "-m" and first_words[2] == "pytest":
                        is_bare_pytest = True

                    if is_bare_pytest:
                        # Check if preceded within 3 lines by a gate argv
                        preceded = False
                        check_start = max(0, fence_start - 3)
                        for j in range(check_start, fence_start):
                            for gate_id, gate_argv in gate_argv_strs.items():
                                if gate_argv in lines[j]:
                                    preceded = True
                                    break
                            if preceded:
                                break

                        if not preceded:
                            findings.append(LintFinding(
                                rule="L2",
                                severity="error",
                                message="bare pytest/python -m pytest block not preceded by declared gate argv",
                                path=str(path),
                                line=fence_start + 1
                            ))
                    i += 1
                    break
                i += 1
        else:
            i += 1


# ----- L3 -----
def _check_l3(findings: list[LintFinding], path: Path, fm) -> None:
    """Check: >=1 oracle (schema enforces), non-trivial negatives."""
    trivial_negatives = {"none", "n/a", ""}
    for oracle in fm.oracles:
        normalized_negative = oracle.negative.lower().strip()
        if normalized_negative in trivial_negatives:
            findings.append(LintFinding(
                rule="L3",
                severity="error",
                message=f"oracle '{oracle.id}' has trivial negative '{oracle.negative}'",
                path=str(path)
            ))
        elif normalized_negative == oracle.observable.lower().strip():
            findings.append(LintFinding(
                rule="L3",
                severity="error",
                message=f"oracle '{oracle.id}' negative is a copy of observable",
                path=str(path)
            ))


# ----- L4 -----
def _check_l4(findings: list[LintFinding], path: Path, fm, body: str) -> None:
    """Check: no enumerated oracle under universal contract."""
    universal_pattern = r"\b(every|all)\s+\w+\s+(field|record|column|key|property)s?\b"

    # Check body for universal pattern
    body_has_universal = bool(re.search(universal_pattern, body, re.IGNORECASE))

    # For each oracle, check if observable has universal pattern
    for oracle in fm.oracles:
        oracle_has_universal = bool(
            re.search(universal_pattern, oracle.observable, re.IGNORECASE)
        )

        # Count quoted/backticked identifiers in observable
        identifiers = re.findall(r"[`\"]([^`\"]+)[`\"]", oracle.observable)

        if (body_has_universal or oracle_has_universal) and len(identifiers) >= 2:
            findings.append(LintFinding(
                rule="L4",
                severity="warning",
                message="universal contract with enumerated oracle subset (P78)",
                path=str(path)
            ))


# ----- L5 -----
def _check_l5(findings: list[LintFinding], path: Path, body: str) -> None:
    """Check: no reviewer-only deliverables."""
    # Check for DECISIONS-INBOX outside do not/never context
    decisions_pattern = r"DECISIONS-INBOX"
    merge_ff_pattern = r"merge\s+--no-ff"
    status_pattern = r"update\s+STATUS\.md"
    git_merge_pattern = r"git\s+merge\s+"

    patterns = [
        (decisions_pattern, "DECISIONS-INBOX"),
        (merge_ff_pattern, "merge --no-ff"),
        (status_pattern, "update STATUS.md"),
        (git_merge_pattern, "git merge"),
    ]

    for pattern, label in patterns:
        for match in re.finditer(pattern, body, re.IGNORECASE):
            # Check if this match is in a "do not" or "never" context
            start = max(0, match.start() - 100)
            context = body[start:match.end() + 50]
            is_negated = bool(re.search(r"\b(do\s+not|never)\b", context, re.IGNORECASE))

            if not is_negated:
                findings.append(LintFinding(
                    rule="L5",
                    severity="error",
                    message=f"reviewer-only deliverable '{label}' in implementer handoff",
                    path=str(path)
                ))


# ----- L6 -----
def _check_l6(findings: list[LintFinding], path: Path, fm, body: str) -> None:
    """Check: no oracle deferral."""
    deferral_pattern = r"\b(controller|reviewer|review\s+pass|frontier)\s+(will|should|can)\s+(validate|verify|re-?run|confirm)"

    # Check body
    if re.search(deferral_pattern, body, re.IGNORECASE):
        findings.append(LintFinding(
            rule="L6",
            severity="error",
            message="acceptance delegated to another role (P84)",
            path=str(path)
        ))

    # Check oracles
    for oracle in fm.oracles:
        if re.search(deferral_pattern, oracle.observable, re.IGNORECASE) or \
           re.search(deferral_pattern, oracle.negative, re.IGNORECASE):
            findings.append(LintFinding(
                rule="L6",
                severity="error",
                message=f"oracle '{oracle.id}' acceptance delegated to another role (P84)",
                path=str(path)
            ))


# ----- L7 -----
def _check_l7(findings: list[LintFinding], path: Path, fm, body: str, cfg: ProjectConfig) -> None:
    """Check: paths resolve."""
    # Check scope.touch and scope.forbid
    for touch_path in fm.scope.touch:
        _check_path_resolution(findings, path, touch_path, cfg, is_touch=True)

    for forbid_path in fm.scope.forbid:
        _check_path_resolution(findings, path, forbid_path, cfg, is_touch=False)

    # Check source.ref
    if fm.source.ref:
        _check_path_resolution(findings, path, fm.source.ref.split("#")[0], cfg, is_touch=False)

    # Check body for markdown links/inline code
    # Look for /paths starting with ../ or /workspaces/<other-repo>
    other_repo_pattern = r"/workspaces/(?!dstdns)[a-z0-9_-]+"
    relative_up_pattern = r"\.\./[a-z0-9/_.-]+"

    for match in re.finditer(other_repo_pattern, body):
        findings.append(LintFinding(
            rule="L7",
            severity="warning",
            message=f"cross-repo reference '{match.group()}' may not resolve",
            path=str(path)
        ))

    for match in re.finditer(relative_up_pattern, body):
        findings.append(LintFinding(
            rule="L7",
            severity="warning",
            message=f"relative-up path '{match.group()}' may escape repo",
            path=str(path)
        ))


def _check_path_resolution(findings: list[LintFinding], path: Path, check_path: str, cfg: ProjectConfig, is_touch: bool) -> None:
    """Helper to check if a path resolves."""
    if check_path.startswith("/") or check_path.startswith("../"):
        findings.append(LintFinding(
            rule="L7",
            severity="error",
            message=f"non-resolving reference '{check_path}' (P69)",
            path=str(path)
        ))
        return

    full_path = cfg.root / check_path
    # Files to be created (in scope.touch) are exempt
    if not is_touch and not full_path.exists():
        findings.append(LintFinding(
            rule="L7",
            severity="error",
            message=f"path '{check_path}' does not exist",
            path=str(path)
        ))


# ----- L8 -----
def _check_l8(findings: list[LintFinding], path: Path, fm) -> None:
    """Check: escalate_if triggers are mechanical."""
    introspective_pattern = r"\b(reflect|consider\s+whether|feel|expertise|confident)\b"
    for trigger in fm.escalate_if:
        if re.search(introspective_pattern, trigger, re.IGNORECASE):
            findings.append(LintFinding(
                rule="L8",
                severity="error",
                message=f"non-mechanical escalation trigger (P51): '{trigger}'",
                path=str(path)
            ))


# ----- L9 -----
def _check_l9(findings: list[LintFinding], path: Path, fm, cfg: ProjectConfig) -> None:
    """Check: infra touches require stack mutex."""
    for touch_path in fm.scope.touch:
        for infra_glob in cfg.infra_globs:
            if fnmatch.fnmatch(touch_path, infra_glob):
                effective_mutexes = fm.effective_mutexes()
                if "stack" not in effective_mutexes:
                    findings.append(LintFinding(
                        rule="L9",
                        severity="error",
                        message=f"scope.touch '{touch_path}' matches infra glob but no stack mutex",
                        path=str(path)
                    ))
                break


# ----- L10 -----
def _check_l10(findings: list[LintFinding], path: Path, full_text: str) -> None:
    """Check: size limits."""
    tokens = len(full_text) // 4
    message = f"handoff size {tokens} tokens"

    if tokens > 12000:
        findings.append(LintFinding(
            rule="L10",
            severity="error",
            message=message,
            path=str(path)
        ))
    elif tokens > 6000:
        findings.append(LintFinding(
            rule="L10",
            severity="warning",
            message=message,
            path=str(path)
        ))


# ----- L11 -----
def _check_l11(findings: list[LintFinding], path: Path, body: str) -> None:
    """Check: body contains required sections."""
    body_lower = body.lower()
    missing = []

    if "worktree" not in body_lower:
        missing.append("worktree path mention")
    if "branch" not in body_lower:
        missing.append("branch name mention")
    if "out of scope" not in body_lower and "forbid" not in body_lower:
        missing.append("out-of-scope/forbid mention")
    if "context to read" not in body_lower and "read first" not in body_lower:
        missing.append("context section")

    if missing:
        findings.append(LintFinding(
            rule="L11",
            severity="error",
            message=f"body missing: {', '.join(missing)}",
            path=str(path)
        ))


# ----- L12 -----
def _check_l12(findings: list[LintFinding], path: Path, body: str) -> None:
    """Check: BLOCKED marker present, no policy violations."""
    # Check for BLOCKED: marker
    if "BLOCKED:" not in body:
        findings.append(LintFinding(
            rule="L12",
            severity="error",
            message="body must contain 'BLOCKED:' marker",
            path=str(path)
        ))

    # Check for policy violations
    violations = [
        ("skip the gate", "skip the gate"),
        ("without running", "without running"),
        ("ignore lint", "ignore lint"),
    ]

    for pattern, label in violations:
        if re.search(pattern, body, re.IGNORECASE):
            findings.append(LintFinding(
                rule="L12",
                severity="error",
                message=f"body instructs violating policy: '{label}'",
                path=str(path)
            ))
