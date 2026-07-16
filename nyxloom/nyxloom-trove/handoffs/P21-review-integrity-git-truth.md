# P21 — review integrity: git state is truth, receipts lie

> Tier: sonnet5-high · Date: 2026-07-15 · Read handoff/STANDING.md. Encodes a
> user directive (2026-07-15): "experience shows the commit requirement is
> often not honored, so uncommitted content should be reviewed/taken into
> account" — and the live P93 lesson that a receipt claimed
> `head_commit: null, files_touched: []` while the branch actually held a real
> commit (`b5e4477`). The reviewer must trust GIT STATE, not receipt fields.
> Independent of P20 (that is the transition apply path); this is the review
> packet + dispatch prompt + receipt consumption.

## Owned paths
- `src/nyxloom/daemon.py` — the FRONTIER_REVIEW packet builder (~1050-1096)
  and the receipt-consumption path (~994-1000, `REVIEW_RECORDED` / the
  implementer done-receipt mapping).
- `src/nyxloom/adapters.py` — the implementer dispatch prompt (~line 131,
  the "uncommitted work is discarded" line).
- `tests/test_daemon.py`, `tests/test_adapters.py`.
- Do NOT touch `storage.py`/`types.py` frozen core, `reconcile.py`, or the
  transition apply path (that is P20).

## Changes
1. **Review packet captures uncommitted worktree state, not just committed.**
   Today the packet writes only `git diff <default>...feat/<task>` (COMMITTED).
   Also capture, per task, from the task's worktree
   (`cfg.root / cfg.worktree_root / feat/<task>` — same derivation
   DispatchImplementer uses at daemon.py:832): `git -C <wt> status --porcelain`
   and `git -C <wt> diff` (unstaged) + `git -C <wt> diff --cached` (staged but
   uncommitted). Write them into `packet.md` under clearly separated headings
   "COMMITTED (default...branch)" and "UNCOMMITTED (worktree — may be lost on
   teardown; REVIEW IT)". If the worktree is absent (already torn down),
   note that explicitly rather than silently omitting.

2. **Review-packet instructions gain a git-truth clause.** Add to the reviewer
   role text: "Verify actual git state — run `git log <default>..feat/<task>`
   and `git status` in the worktree. Do NOT trust the receipt's
   `head_commit` / `files_touched` / `oracles` fields: they have been observed
   null/empty even when real work was committed (live P93 lesson). If the
   worktree holds UNCOMMITTED changes, review them too — the implementer's
   commit discipline is not guaranteed; do not treat uncommitted work as
   nonexistent."

3. **Receipt consumption cross-checks the branch.** Where a done-receipt is
   mapped (daemon ~994-1000), if `receipt.head_commit` is null/empty, derive
   the real head by comparing `git rev-parse feat/<task>` against
   `<default>` — if the branch is ahead, record the real HEAD (do not treat
   the task as empty/no-work on a lying null). Keep this defensive and
   read-only (no writes to the branch).

4. **Reframe the implementer prompt (adapters.py:131).** Replace "uncommitted
   work is discarded" with a truthful, still-firm line, e.g.: "Commit ALL your
   work on the branch before finishing. Uncommitted work will be surfaced to
   review but risks loss on worktree teardown — committing is required for a
   clean review." Keep the pressure to commit; stop asserting a falsehood.

## Oracles
1. A review packet built for a task whose worktree has an uncommitted change
   contains that change under an "UNCOMMITTED" heading in `packet.md`.
2. The reviewer role text in `packet.md` contains the git-truth /
   don't-trust-the-receipt clause.
3. A done-receipt with `head_commit=null` on a branch that has a real commit
   ahead of default results in the REAL commit being recorded (not null / not
   treated as empty). A branch with NO commits ahead still records null/none.
4. The implementer dispatch prompt no longer contains the literal string
   "uncommitted work is discarded"; it contains the reframed commit
   instruction. (Assert on `build_dispatch`'s returned prompt.)
5. Full suite green.

## Rules
STANDING.md applies. Do not commit — receipt-only final; REPORT to
`handoff/reports/P21-REPORT.md`. Notification-injection boundary still holds:
uncommitted diff text goes into the local review PACKET only (a file the
reviewer agent reads), never into notification bodies.
