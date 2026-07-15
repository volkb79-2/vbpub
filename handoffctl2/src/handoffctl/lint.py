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

from pathlib import Path

from .config import ProjectConfig
from .types import LintFinding


def lint_file(path: Path, cfg: ProjectConfig) -> list[LintFinding]:
    """All findings for one handoff file (see module contract)."""
    raise NotImplementedError


def lint_project(cfg: ProjectConfig) -> dict[str, list[LintFinding]]:
    """relpath -> findings for every discovered handoff (frontmatter.discover_handoffs)."""
    raise NotImplementedError


def has_blocking(findings: list[LintFinding]) -> bool:
    return any(f.severity == "error" for f in findings)
