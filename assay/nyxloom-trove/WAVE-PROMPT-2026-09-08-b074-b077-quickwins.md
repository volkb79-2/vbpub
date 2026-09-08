# Wave prompt — B074 + B077 quick-wins bundle (2026-09-08)

Branch: `feat/assay-b074-b077-quickwins-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b074-b077-quickwins`
Controller log: `assay/nyxloom-trove/reports/assay-WAVE-B074-B077-CONTROLLER-LOG.md`

**This branches off `main` AFTER B070 (verdict schema v11) has already
merged.** You are building on top of v11 — `judgment.r2.discarded` is now
an array of `mutant_outcome` records, `VERDICT_SCHEMA_VERSION == 11`.
Neither item in this wave touches the mutation/verdict schema, so there
should be no interaction with B070's surface, but if you find one, stop
and flag it rather than guessing.

**No verdict-schema change in this wave.** `assay verify` is unaffected by
B074 (it's a config-gated relaxation of a lane-config refusal) and by B077
(it's a git-boundary error-message improvement). Do these TWO items, in
order, and nothing else — do not fold in any other backlog entry.

## Context to read first

1. `assay/nyxloom-trove/4-backlog.md`, search `## B074` and `## B077` — read
   each item's own full section (measured baseline, proposed change,
   acceptance checklist). This prompt sequences and rules the open forks;
   it does not repeat every detail.
2. `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b068-quickwins.md` — an
   earlier quick-wins bundle on this project, for the shape of how several
   smaller items get sequenced and reported together in one wave.

## Item 1 — B074: let an explicitly declared `judge.targets` entry opt out of the test-path veto

**The problem, in one line**: `_resolve_whole_target` (`evaluate.py:1044`)
refuses `BAD_LANE_CONFIG` for ANY target path matching
`_TEST_FILE_RE` (`adapters/python.py` — `(^|/)(tests/|test_[^/]*\.py$|
conftest\.py$)`), even when the target was an EXPLICIT, reviewed,
per-lane declaration (`judge.targets`), not a swept path from a diff. This
wrongly blocks projects whose deployed library code happens to live under
a `tests/` directory (dstdns's own `tests/_harness/` is the confirmed
repro — real production-shaped harness code, `COPY`-ed into a container
image and executed as a real service, currently ungradeable by
`whole_target` at all).

**Ruling — Shape 1 from the backlog entry (the preferred one, smaller and
keeps a guard-rail)**: add `judge.allow_test_path_targets = true` (default
`false`). This flag applies ONLY inside `_resolve_whole_target` — do NOT
touch `evaluate.py:428`'s sweep-side `is_test_path` check or
`mutation.py:470`'s; both stay exactly as strict as they are today. A lane
setting the flag is asserting "the paths I named are library code despite
their location," and the flag's presence in the verdict's resolved
judgment makes that claim auditable (see acceptance below).

The OTHER four `_resolve_whole_target` gates (symlink, source-root
containment, regular-file, adapter-recognised-source) are UNCHANGED and
must still apply with or without the flag — this entry only relaxes the
test-path veto, nothing else.

**Acceptance, from the backlog entry directly:**
- a `whole_target` lane naming `tests/<...>/lib.py` WITH the flag set is
  JUDGED, reaching a real `PASS`/`FAIL` on its coverage floor rather than
  `BAD_LANE_CONFIG`;
- the SAME lane WITHOUT the flag still refuses `BAD_LANE_CONFIG`, naming
  the target and the test-path gate — the controlled comparison proving
  the flag is what changed the outcome;
- a `changed_lines` lane over a diff touching that same file still SKIPS
  it (sweep-side behavior untouched) — proves the relaxation is scoped to
  explicit targets only;
- a target that is a genuine test file (`test_foo.py`, `conftest.py`)
  still refuses even WITH the flag (the filename alternatives in
  `_TEST_FILE_RE` stay in force; only the `tests/`-segment alternative is
  what a declared target can override — if you find this ambiguous,
  make the call and state it in the LOG, don't leave it undecided);
- the flag appears in the verdict's resolved judgment, so a reviewer can
  see that a graded target was one assay would otherwise have refused.

## Item 2 — B077: a symlink-through-the-judged-tree destination gets a named refusal instead of raw git stderr

**The problem, in one line**: a `--state-dir`/`--progress` destination
that resolves INSIDE the judged tree via a symlink — even when the actual
target is correctly gitignored, i.e. a consumer who configured this
exactly right — currently surfaces git's own raw
`fatal: pathspec '<path>' is beyond a symbolic link` as a bare
`ERROR`/`GIT_FAILED`, rather than a message naming what's actually wrong.
Filed by B070's own round-2 reviewer while re-deriving a DIFFERENT,
already-fixed containment bug (SF-1, `b5532895`) — this is a distinct
failure mode (git's own pathspec resolution refusing to traverse a
symlink), not assay's own containment check.

**Fix shape (from the backlog entry, likely reusable)**: before letting
git's own pathspec resolution raise, probe whether the resolved
destination traverses a symlink whose target is inside the judged tree,
and if so refuse `ERROR`/`GIT_FAILED` (or a more specific reason code, if
one already exists for "destination configuration is unreachable through
git, not just unsafe") naming the symlink and the traversal — mirroring
the diagnostic-message discipline `_linked_worktree_gap()` (B068) and the
round-1 N2 fix (`b5532895`) both established.

**Acceptance, from the backlog entry directly:**
- a `--state-dir`/`--progress` destination reached through a symlink whose
  target is INSIDE the judged tree (and correctly gitignored) refuses with
  a message naming the symlink and the traversal, not a raw
  `fatal: pathspec ... is beyond a symbolic link` passthrough;
- the two ALREADY-correct outcomes stay correct: a destination genuinely
  outside the repository, and one reached with no symlink involved, are
  both unaffected;
- a regression test reproduces the reviewer's exact repro (a symlink
  inside the tree pointing at a gitignored location, both `--state-dir`
  and `--progress`) and confirms the new message.

## Binding constraints (both items)

- `assay verify` is unaffected by either item.
- `docs/CONSUMERS.md` gets the new `allow_test_path_targets` flag
  documented with a worked example (this project's standing rule: a
  lane-declarable capability is incomplete without a paste-able example).
- Read the registered gate's verdict from its own log markers after it
  finishes, never a piped exit code.
- Host: 8 cores shared with a production game server, and this wave is
  running IN PARALLEL with a sibling wave (B078, a different worktree,
  same host) by explicit operator authorization — the usual single-agent/
  single-gate directive is deliberately reversed for these two waves only.
  This makes host-load discipline MORE important, not less:
  - `docker ps --no-trunc` AND `pgrep -af tester-unified-gate.sh` before
    starting anything — if either shows an existing gate container/process
    from ANY session, WAIT for it rather than racing it.
  - The moment your own gate container starts, `docker update --cpus=3` on
    it — **identify it by the worktree path in its own launch argv,
    never by recency or process-list position** — a peer session's
    container starting near the same time as yours is expected this wave,
    not a sign something is wrong. Never cap or touch a container you
    cannot positively identify as your own.
  - Serial pytest under `nice -n 19 ionice -c 3`. Never run a build
    concurrently with your own running test suite.
- **Do NOT write your LOG/REPORT into the tree before your final gate
  run** — run the gate on a clean commit; write LOG/REPORT only after a
  green verdict.
- **Checkpoint clause (E-008):** if you cross ~120k context tokens or ~60
  tool calls, cut at the next coherent boundary (green gate > commit >
  LOG/REPORT write; never a red gate) and write a continuation brief to
  `assay/nyxloom-trove/reports/assay-WAVE-B074-B077-BRIEF.md` plus a
  self-authored `/compact`-retention prompt, commit, and stop.
- Commit trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- When both items are done and the registered gate is green, write
  `assay/nyxloom-trove/reports/assay-WAVE-B074-B077-LOG.md` (what you did,
  per item, with commit hashes) and
  `assay/nyxloom-trove/reports/assay-WAVE-B074-B077-REPORT.md`
  (acceptance-box status per item, with evidence), then stop — do not
  dispatch a reviewer yourself, the controller does that next.
