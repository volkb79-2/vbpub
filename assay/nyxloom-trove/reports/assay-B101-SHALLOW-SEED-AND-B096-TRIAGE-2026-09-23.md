# B101 shallow seed — what it is, why, and the `assay-b096` triage (2026-09-23)

Controller analysis for the operator. Companion to backlog B101/B102
(`ffa1264a`). All numbers measured on dstdns `f1b179be` (5,105 commits).

## 1. What a P22 snapshot is and why one exists at all

An R1+ lane never runs in the consumer's checkout. `isolation.prepare_snapshot`
builds a private bare **seed** repository, and every run unit (the baseline run,
and for R2 every mutant) gets its own fresh **materialization**: a private
`.git` (`_copy_objects` copies the seed pack, never hardlinks/alternates) plus
a worktree written from the commit's manifest, HEAD written as the bare commit
OID (`isolation.py:833`) — no branches, no tags.

"We already run the test against a commit" — the snapshot IS how that happens.
A commit is only a name; running against it needs its files on disk somewhere
that (a) is not the live checkout (dirty, may move mid-run, must never be
written by a mutant or a hostile command), (b) is independent per unit (a
mutant must not leak into its sibling), and (c) is still a git repository,
because assay itself asks git questions inside it:

- post-command `git status` (`DIRTY_TREE`) and `HEAD` check (`HEAD_CHANGED`) —
  did the lane's command modify tracked files or commit?
- `changed_lines` mode: `git diff <base> HEAD` for the added-line set, and the
  `BASE_IS_HEAD` guard.
- per-mutant integrity checks in R2 (`mutation.py:1748`).

`git archive` (files only) was rejected in A-185: no `.git`, and it executes a
committed hostile filter.

## 2. Full vs shallow seed — the only open question is how much of git's database goes in

| Seed content | Objects | Pack bytes | Grows with |
|---|---|---|---|
| Today: full reachable closure | 44,007 | 38.3 MiB (limit counts 1084 MiB uncompressed) | every commit, forever |
| Judged commit only | 2,634 | 16.2 MiB | tree size only |
| Judged commit + a base 40 commits back | — | 21.6 MiB | tree size only |

The pack is copied once per materialization, i.e. once per mutant in R2.

**Shallow seed** = git's own shallow-clone mechanism (as in `git clone
--depth 1`): the seed holds the objects of the judged commit (and, for
`changed_lines`, the pre-resolved base commit), and a `.git/shallow` file lists
those commits as intentional history boundaries so git treats the missing
parents as "cut here", not corruption. Inside it: `status`, `diff base HEAD`,
`rev-parse HEAD`, checkout all work. `git log` stops at the boundary; and
**`merge-base` between two shallow roots fails** (no common ancestor visible).

What full history buys today: only ancestor walks. Tag/branch-based
versioning (`git describe`, setuptools-scm) already cannot work, because the
snapshot has no refs. dstdns: 105/121 lanes `whole_target` (no history need),
16 `changed_lines` (need the base commit, not the history between).

**Two ways to close B101, honestly compared:**

1. *Fix the accounting only, keep full history:* bound the pack (already
   `max_pack_bytes`), drop/raise the history-counted `max_total_object_bytes`
   and `max_objects`, add an explicit judged-tree bytes ceiling. Unblocks
   immediately, zero semantic change; cost still grows with history (pack
   38 MiB now, copied per unit).
2. *Shallow seed (decided direction):* cost bounded by tree size forever,
   ~2.4x less copying per unit today, limits never re-crossed by history.
   Needs the in-snapshot base handling below; per-lane `full` opt-in stays.

## 3. `assay-b096` triage

Branch: 7 commits, 2026-09-13..14, RG-55 controller, worker Luna xhigh.
B096 itself (derive `--rejudge-outcome` help from `MUTATION_BUCKETS`) **is on
main** (CHANGES). What is NOT on main is a gate repair on top of it:

- `84baffb4 fix(assay): carry resolved bases through P22 snapshots` — adds
  `measurability.check_resolved_base_is_head` and threads the pre-snapshot
  resolved base OID into `evaluate_r1` / the R2 diff path, so nothing inside the
  snapshot re-runs `git.resolve_base`. Plus P25 harness diagnostics
  (`qualify_topos.py`: keep artifact + stream tails on scenario mismatch) and
  focused regressions.
- Motivation was a red P25 `declared-base-as-tag` scenario, first attributed to
  re-resolving a symbolic tag inside the ref-free snapshot.
- The branch's own final checkpoint (BRIEF-4) disproved that attribution: with
  the fix, the scenario's R1 claim PASSes with the correct resolved OID, and the
  red is `FAIL/COMMAND_FAILED` from a Topos UI test timing out
  (`textual.pilot.WaitForScreenTimeout`, 1 failed / 2922 passed) — a
  load-sensitive test, not an assay defect. The branch stopped there.

**Is the bug live on main?** No, not with full history: main passes the
already-resolved OID into `check_base_is_head` inside the snapshot, and
`resolve_base(OID)` = `merge-base OID HEAD`, which works because the full
ancestry is present.

**Why it is needed anyway:** under a shallow seed, that in-snapshot
`resolve_base` breaks twice — `merge-base` across two shallow roots fails, and
`rev-list --parents -n1 HEAD` no longer sees a merge commit's parents (so the
first-parent rule silently becomes merge-base). `check_resolved_base_is_head`
(consume the carried OID, never re-resolve inside the snapshot) is exactly the
seam the shallow seed requires. It is also correct on its own under full
history (no behavior change; one fewer git round-trip per unit).

**Disposition:** port `84baffb4` (product change + tests + P25 diagnostics)
onto current main as the first package of the B101 implementation; do not
merge the branch (its checkpoint docs record a superseded diagnosis). The
Topos UI timeout is a separate, contention-sensitive test issue for the Topos
owner (operator rule 2026-09-14: pressure-affected runs are infrastructure/
inconclusive, never a product verdict).
