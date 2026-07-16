# nyxloom-P43-dead-stub-guard — independent frontier review (merge gate)

Reviewer: Opus 4.8, fresh session. Single-task packet.
Date: 2026-07-16. Commit reviewed: `3dcfc65`.

## Verdict

**APPROVED after one review-fix.**

The guard is real. I mutation-tested it rather than trusting that it passes,
and it fails loudly in exactly the situations it exists to catch — including a
faithful reconstruction of the original P43 incident (a new enum member wired
nowhere). O1 holds as written, and holds under adversarial probing.

O2 held only as *text*. The backlog item and the comment ref both exist, so the
observable was literally satisfied, but nothing in the gate enforced the link
the oracle asks for. I closed that gap on the branch (one additive test plus a
helper, `tests/test_types.py` only — already in `scope.touch`). It is a wiring
omission in the guard, not a design problem, which is why this is fix-and-approve
rather than a rejection.

## Git state (verified, not taken from the receipt)

Per the packet's standing warning, I did not trust the receipt's fields and
checked git directly:

- `git log main..feat/nyxloom-P43-dead-stub-guard` → exactly one commit, `3dcfc65`.
- `git status` in `.worktrees/feat/nyxloom-P43-dead-stub-guard` → clean, no
  uncommitted work (matches the packet's UNCOMMITTED section).
- Files touched vs `main`: `src/nyxloom/types.py`, `tests/test_types.py`,
  `nyxloom-trove/backlog.md` — exactly `scope.touch`.
- Forbidden files (`daemon.py`, `reconcile.py`, `adapters.py`): **untouched**.
  The `escalate_if` schema-deletion path was not taken, so no schema change was
  in scope and none was made. Correct call — reserving was the better option.

Note: `main`'s `5b59e17` ("P42 component field + P43 dead-stub guard") is the
**carve** commit — it adds handoffs and STANDARD.md only. The P43 implementation
exists solely on the feat branch. No overlap, no double-application.

## Gate — re-run by me, not read from a report

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../.worktrees/feat/nyxloom-P43-dead-stub-guard/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

Green at `3dcfc65` (exit 0), and green again after my fix (exit 0).
`tests/test_types.py` is collected and runs under the real gate: 4 tests at
`3dcfc65`, 6 after the fix.

Aside, not P43's doing: this suite prints progress dots but no `N passed`
summary line under `-q`. Exit status is the trustworthy signal here. Worth
knowing for anyone grepping gate output for "passed" — a `set -e; ... | grep
passed` gate wrapper would false-negative on a green run.

## O1 — every Role dispatched or reserved: CONFIRMED, non-hollow

The oracle explicitly warns against a hollow guard that merely asserts
`RESERVED_ROLES <= set(Role)`. This one does not. I verified the scan reads real
dispatch sites by breaking things and confirming the tests notice:

| Mutation | Expected | Result |
|---|---|---|
| Add unwired `TRIAGE = "triage"` to `Role` (the original P43 scenario) | fail | **fails** — `Role.TRIAGE is neither dispatched ... nor in RESERVED_ROLES — silent stub` |
| Remove `SELF_REVIEW` from `RESERVED_ROLES` | fail | **fails** (2 tests) |
| Break `_DISPATCH_RE` so the scan matches nothing | fail | **fails** — the `IMPLEMENTER` anchor catches it |

The third case is the one that matters: it proves the `IMPLEMENTER` anchor is
load-bearing and the partition isn't passing by construction.

Both required anchors are present and correct. The scan finds the three real
dispatch sites in `daemon.py` (`role=Role.CARVER` L1251, `role=Role.IMPLEMENTER`
L1472, `role=Role.FRONTIER_REVIEW` L1810).

One thing I checked specifically, because it would have been an easy way to make
the guard silently hollow: `reconcile.py` contains no dispatch sites at all —
only comparisons like `attempt.role == Role.IMPLEMENTER`. The `role=Role\.(\w+)`
regex correctly does **not** match `role == Role.X` (the spaces and second `=`
prevent it), so those comparisons are not miscounted as dispatch. Scanning
`reconcile.py` is therefore a no-op today, but it is what the handoff asked for
and it is the right place for a future dispatch leg to appear. Keeping it is
correct.

## O2 — reserved role is TRACKED, not silently parked: gap found, fixed

O2 asks for a backlog item *and* says: "Test/asserts: RESERVED_ROLES's comment
references this backlog id, and backlog.md contains the item", gated on
`tester-unified`.

Present at `3dcfc65`:
- `nyxloom-trove/backlog.md` has the `B-self-review-leg` item, well-written and
  correctly describing the deferred decision (dispatch site + state-machine leg
  + WIP-slot question, or delete as YAGNI).
- `RESERVED_ROLES`'s comment cites `nyxloom-trove/backlog.md: B-self-review-leg`.

Missing: any *mechanical* enforcement of that link. I proved the gap rather than
asserting it — I added a second reserved role with no backlog ref and no backlog
item:

```python
RESERVED_ROLES: frozenset[Role] = frozenset({
    Role.SELF_REVIEW,  # nyxloom-trove/backlog.md: B-self-review-leg
    Role.TRIAGE,
})
```

The gate stayed **green (exit 0)**. That is O2's negative verbatim — "reserved
but untracked — still a silent stub, just relabelled". P43's whole thesis is
that the guard must not depend on review vigilance; O2's half depended entirely
on it.

**Fix (mine, on the branch):** `tests/test_types.py` gains
`_reserved_backlog_refs()` (parses the `RESERVED_ROLES` block for
`# nyxloom-trove/backlog.md: <id>` comments) plus two tests:

- `test_every_reserved_role_cites_a_live_backlog_item` — every member has a ref,
  and every cited id resolves to a real `- **<id>` item in `backlog.md`.
- `test_self_review_cites_the_self_review_leg_backlog_item` — the non-hollow
  anchor, so the check can't pass on an empty ref set.

Mutation-tested my own fix to the same standard I held theirs to:

| Mutation | Result |
|---|---|
| Reserve `TRIAGE` with no backlog ref | **fails** (was green before the fix) |
| Cite `B-does-not-exist` | **fails** (2 tests) |
| Delete the `B-self-review-leg` item, keep the ref | **fails** (2 tests) |

Baseline: 6 passed, full gate exit 0.

## Other things I checked and found clean

- **Backlog id convention.** `B-self-review-leg` is a slug, not the `- **B<N>`
  shape the handoff's prose suggested. This is *correct*, not a deviation: the
  recent entries (`B-carve-backpressure`, `B-intake-over-ntfy`) use slugs, and
  `B16` in that same file documents *why* — numbered ids collide under
  concurrent carving. The implementer followed the live convention over the
  handoff's stale hint. Good judgement.
- **Claim accuracy (no overclaim).** The backlog says SELF_REVIEW is "defined in
  the enum + statefile schema". Verified: `"self-review"` is in the role enum of
  both `schemas/statefile.schema.json` and `src/nyxloom/schemas/statefile.schema.json`.
- **`Role[name]` lookup** is by member name, matching what the regex captures.
  No `KeyError` path from the current sources.
- **Path resolution.** `REPO_ROOT = Path(__file__).resolve().parent.parent`
  resolves to `nyxloom/`, so both the `src/nyxloom/*.py` and (in my addition)
  `nyxloom-trove/backlog.md` reads are correct under the gate's `cd`.

## Residual risk (accepted, not blocking)

The scan is textual, so `role=Role.SELF_REVIEW` appearing inside a *comment or
docstring* in `daemon.py`/`reconcile.py` would read as a dispatch site and let a
stub through. Fixing this properly means an AST walk, which is a real step up in
complexity for a speculative failure mode, and the handoff explicitly steers to
a static text scan (`escalate_if` forbids import-and-run). The mistake this
guard exists to catch is *forgetting* to wire a role, not *faking* a dispatch
site in a comment — text scanning is proportionate. Noting it so a future reader
knows the boundary was chosen, not missed.

## What I changed

- `tests/test_types.py` — added `_reserved_backlog_refs()` +
  `test_every_reserved_role_cites_a_live_backlog_item` +
  `test_self_review_cites_the_self_review_leg_backlog_item` (O2 enforcement).
- This report.

No production code touched by me; the implementer's `types.py` and `backlog.md`
stand as written.

VERDICT: APPROVED
