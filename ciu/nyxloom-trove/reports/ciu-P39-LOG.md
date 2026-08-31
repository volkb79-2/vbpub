# ciu-P39 — CIU-63: `lint_graph` recognizes `stack:*` refs without a self-declared `provides`

**Branch:** `fix/ciu-P39-lint-graph-stack-refs` · **Base at start:** `a78a0046`
· **Final base:** `main` at `384993b6` (rebased three times total — see
"Rebase history" below; the final rebase, done in response to review,
dropped three self-authored commits entirely per the reviewer's explicit
prescription).

## Commit 1 (original session) — CIU-63 fix

`lint_graph`'s "every required ref is provided" pass (`src/ciu/provisioning.py`)
treated a `stack:<path>:healthy|completed` ref exactly like every other ref
kind: satisfied only when some stack's `provides` array literally contained
the string. That ref kind is actually resolved by the live probe
(`_probe_stack`, `docker inspect` against the referenced stack's own
container), which never reads a `provides` declaration — exactly what the
cycle-detection pass below it already knew, via `_STACK_RE` +
`_resolve_declared_stack_path`.

Fix: before flagging a `requires` entry as unprovided, match it against
`_STACK_RE` and resolve the captured stack name via
`_resolve_declared_stack_path(name, stacks.keys())`; a ref resolving to a
real declared stack is satisfied without appearing in any `provides` array.
Every other ref kind, and a `stack:*` ref that does NOT resolve to a real
declared stack, keep today's exact behavior.

Touched: `src/ciu/provisioning.py` (`lint_graph` only), `docs/SPEC.md`
(S13.2/S13.3 notes), `tests/tests/test_ciu_provisioning.py` (5 tests at that
point). Manual controlled-wrong-implementation sanity check performed before
committing: temporarily reverted the special-case, reran the three positive
tests, all three failed with the exact `"but nobody provides it"` message,
confirming the fix is load-bearing; restored and reran green before
committing. This part of the work was independently reviewed and returned
**ACCEPT-conditional** with **no changes requested to the fix itself** — "the
cleanest single change in this batch."

## Original-session detour (superseded by the review, see below)

The original session also investigated why the real gate's R0 (whole-command
exit 0) failed, root-caused it to a `PYTHONDONTWRITEBYTECODE=1` env var
declared in `assay.toml [lanes.ciu].env` interacting with two hardcoded
`sys.dont_write_bytecode is False` assertions in `test_ciu_deploy_actions.py`,
filed that as CIU-78, and fixed it directly. That work turned out to
duplicate a fix landed independently and concurrently on `main`
(`aa6cf1fd`) under a different local variable name (`ambient` vs. this
session's `saved_dont_write_bytecode`) — a genuine parallel discovery of the
same bug, not a mistake in the diagnosis. Per the reviewer's explicit
prescription (see "Review round" below), all three of this session's CIU-78
commits were **dropped entirely** during the final rebase; main's version of
both `test_ciu_deploy_actions.py` and `KNOWN_ISSUES_TODO_BACKLOG.md` was
taken wholesale. Nothing in the CIU-63 fix itself ever depended on them.

## Rebase history

1. Rebased once onto `main` at `b8102bc2` (a stale `run-gate.toml` assay
   pin, pre-existing, fixed independently by a concurrent agent) before the
   first gate run.
2. Rebased again onto `main` at `858766d1` (CIU-76/CIU-77 filed by a
   concurrent agent) before filing CIU-78, to get an accurate next backlog
   ID.
3. **Review round — final rebase**, onto `main` at `384993b6` (which by then
   included `aa6cf1fd`, the concurrent CIU-78 fix, and `eb023f24`, CIU-76's
   real fix via a `now:` override to `apply_lease`, both landed through
   ciu-P36's own merge). Performed as `git reset --hard main` followed by
   `git cherry-pick f1b5a2a5` (formerly `1bd735db`, the CIU-63 fix commit
   only) — non-interactively, since interactive rebase is unavailable to
   this agent. Cherry-pick was clean, zero conflicts (the CIU-63 fix never
   touched any file the intervening main commits also touched). Verified
   post-rebase: `git diff main -- tests/tests/test_ciu_deploy_actions.py
   KNOWN_ISSUES_TODO_BACKLOG.md` was empty — confirming the dropped CIU-78
   commits' files now match main exactly, no hand-merge attempted, per the
   reviewer's explicit instruction not to.

## Review round — what changed

Coordinator's review verdict: **ACCEPT-conditional.** The CIU-63 fix itself
needed no changes. Three blockers (all merge hygiene, not fix correctness)
and two non-blocking discretionary notes:

1. **Stale base** — fixed by the final rebase above.
2. **CIU-78 collision** — fixed by dropping this session's three CIU-78
   commits entirely during the rebase (not hand-merged); main's versions of
   both files taken wholesale.
3. **Backlog inversion** — this session had marked CIU-78 FIXED (now moot,
   dropped) while leaving CIU-63 itself OPEN. Corrected: CIU-63's own row
   marked FIXED, recording that shape (b) [`lint_graph` recognizes the ref]
   was implemented rather than shape (a) [document the wart] — see the new
   commit below. This replaces the dropped CIU-78 row rather than adding to
   it, per the reviewer's framing.

Non-blocking, addressed at this session's discretion (reviewer flagged
both, left the call to this session):

- `provisioning.py:171`'s new resolver call used `_STACK_RE.match`; changed
  to `.fullmatch` to match `parse_ref`'s own style at `:74` (functionally
  identical given the fully-anchored pattern — a pure consistency cleanup).
  The pre-existing cycle-detection pass's own `.match` call (line ~184,
  untouched, not part of this fix) was deliberately left as-is — the
  reviewer's citation was specifically against `parse_ref`'s `:74`, and
  touching pre-existing unrelated code beyond what was cited would be
  unrequested scope expansion.
- New test `test_lint_graph_ambiguous_bare_selector_with_no_provides_still_errors`:
  the existing ambiguous-selector test short-circuited through the
  `ref in all_provided` fast path (its stack self-declared a `provides`
  entry) and never actually reached this fix's own resolver call. The new
  test removes every `provides` declaration so an ambiguous basename
  genuinely exercises `_resolve_declared_stack_path`'s ambiguous-match
  branch from `lint_graph`'s new call site.

Both landed in one follow-up commit, deliberately separate from the
already-reviewed fix commit so that commit's content stays exactly what the
reviewer examined and accepted.

## Commits, final state (`git log --format='%H %s' 384993b6..HEAD`, oldest first)

1. `f1b5a2a5` — `fix(ciu): CIU-63 -- lint_graph's requires-satisfied pass recognizes stack:* refs`
   (identical content to the originally-reviewed `1bd735db`; hash changed
   only because the cherry-pick landed on a different parent)
2. `bde6256f` — `fix(ciu): CIU-63 review follow-up -- fullmatch consistency + ambiguous-selector coverage gap`
3. `04097c4c` — `backlog(ciu): mark CIU-63 FIXED -- ciu-P39, shape (b) implemented`
4. (this LOG/REPORT commit)

## Final gate run

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P39-lint-graph-stack-refs`,
run synchronously (blocking, no backgrounding) from inside `ciu/`, against
commit `04097c4c93e0286fbea50d63cd10573b973652b0`. **`ciu: PASS (exit 0)`.**
Verdict JSON: `outcome: PASS`, `R0: PASS`, `R1: PASS` (100.0% changed-line +
branch coverage, 15/15 executable statements, 4/4 branches). Full verbatim
output in `ciu-P39-REPORT.md`.

## Deliberately not touched

- `src/ciu/worktree.py` / `apply_lease` (CIU-76) — already fixed on `main`
  by ciu-P36 (`eb023f24`), picked up cleanly via the rebase; this package
  never touched it directly.
- `tests/tests/test_ciu_deploy_actions.py` (CIU-78) — already fixed on
  `main` by a concurrent agent (`aa6cf1fd`); this package's own independent
  fix was dropped per review, main's version taken wholesale, verified
  byte-identical post-rebase.
- `_probe_stack` and every other live-probe function — untouched throughout,
  per the original brief.
- `docs/CONFIG.md`'s worked example (`db_init.provides =
  ["stack:infra/db-init:completed"]`) — still valid and harmless after this
  fix (declaring it is simply no longer *required*); not named in scope.
