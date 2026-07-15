# P08 — doctor (drift audit) + rebuild

> Tier: haiku · Depends-on: interfaces of frontmatter/lint (P01) and
> decisions (P07) — monkeypatch them in tests as noted · Read first:
> handoff/STANDING.md, src/handoffctl/doctor.py (docstring = normative
> check list), docs/SPEC.md §14, docs/EVOLUTION.md.

## Owned files
- `src/handoffctl/doctor.py`
- `tests/test_doctor.py`

## Cross-package rule
`doctor_project` CALLS `frontmatter.discover_handoffs`, `frontmatter.
parse_handoff`, `lint.lint_project`, `decisions.open_ids` — those are P01/
P07 code that may be unimplemented while you work. In tests, monkeypatch
exactly these four with canned returns (e.g. lint.lint_project → {} for a
clean run; → {'handoff/x.md': [LintFinding(rule='L2', severity='error',
message='m', path='handoff/x.md')]} for the handoff-lint case). Your
implementation must degrade per-check: if a helper raises
NotImplementedError, SKIP that check and emit a single DoctorFinding
(kind='check-unavailable', severity='info', refs=[the module name]) —
doctor must be useful mid-evolution.

## Oracles (each finding class gets a triggering test + a clean negative)
1. Clean `sample_project` with all helpers monkeypatched clean and one
   COMPLETED-consistent statefile → doctor_project returns NO finding with
   severity in {'critical','error'} (info allowed).
2. replay-divergence: create a task via storage events, then hand-edit its
   statefile json (change notes) → finding kind 'replay-divergence',
   severity 'critical', refs contains the task id.
3. handoff-lint (monkeypatched as above) → 'handoff-lint', error.
4. dangling-dep: canned frontmatters with depends_on ['ghost'] → error.
5. orphan-worktree: `git worktree add .worktrees/feat/zombie -b feat/zombie`
   in sample repo, no matching task → warning naming 'feat/zombie'.
6. missing-worktree: ACTIVE task whose attempt.worktree points at a
   nonexistent dir → warning.
7. stale-receipt: attempt RUNNING in statefile + receipt.json in its
   attempt dir → warning.
8. unbound-evidence: statefile MERGED with merge_commit None → warning.
9. legacy-lock: touch `<root>/docs/.STACK_LOCK` → warning.
10. stale-pause: pause flag mtime forced 8 days old (os.utime) → info.
11. orphan-statefile: statefile whose handoff_path missing, state QUEUED →
    warning; same but COMPLETED → NO finding (terminal exemption).
12. decision-hold: QUEUED task depends_on ['D-002'], decisions.open_ids →
    {'D-002'} → info with refs ['D-002'].
13. `rebuild('demo')`: after the divergence of oracle 2 → diffs list
    contains 'demo-…: notes'; write=False leaves the file untouched;
    write=True: statefile now matches replay AND a .bak file exists with
    the pre-write content.
14. `doctor_all` returns a dict keyed by every registered project.

## Guidance
- Finding messages: short fixed templates + ids only (payload injection
  rule applies to doctor output too — it lands on the dashboard).
- git calls: subprocess with ['git','-C',root,...], never shell.
- Diff computation for rebuild: recursive dict compare emitting dotted
  paths, cap 50 (docstring).
