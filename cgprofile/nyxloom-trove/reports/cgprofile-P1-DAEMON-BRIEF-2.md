# cgprofile-P1-DAEMON — checkpoint BRIEF 2 (E-008)

Written at a clean boundary (one green-gate commit landed this session,
LOG/REPORT just written) rather than at a context/tool-call limit —
session 2 used roughly 40 tool calls total and was nowhere near either
E-008 threshold, but C3 (DAMON `KdamondPool`) is a genuinely separate,
red-first, fake-sysfs-tested stateful module that needs `lib/damon.py`
re-read in full plus the classifier source
(`scripts/damon-analysis/lib/damon_analysis.py`) read for the first time —
starting it with a half-spent budget would mean either rushing the
red-first test or under-reading the classifier before porting it. Landing
C2 solidly, gated and documented, is a better handoff.

## State as of `b2481a34`

- Worktree: `/workspaces/vbpub/.worktrees/rg55-profiler-daemon`, branch
  `rg55-profiler-daemon`.
- Commits: `688ea7bb` (C0, RW-3), `ba7a3167` (C1, `lib/summary.py`),
  `b2481a34` (C2, `lib/subtree.py`). All green:
  `tools/gate.sh coverage` exit 0, 957/957 tests, 100%/100% line+branch;
  `run-gate.py r3` exit 0, 7/7 canaries rejected, `history_eligible: true`
  against `b2481a34`.
- `r2` (assay mutation) still not yet run — intentionally, per the handoff
  ("once before your return"); this is not the return.
- No live-acceptance, ciu, cmru, or Docker-image work started.
- **Controller ruling RW-7 accepted** (session 2 start, no action needed):
  "the last read of `memory.peak`/`pids.peak`" in contract §7 means the
  last *successful* read, confirming C1's `_last_present` implementation.
  No code change resulted; recorded in REPORT's decision-asks section as
  resolved.
- A small self-correction is recorded in both the LOG and REPORT: the C2
  commit message and an early draft of both records mis-added the 5 test
  classes' sizes to "33 tests" — the real, `pytest --collect-only`-verified
  count is 24 (4+4+5+2+9). The commit message itself was NOT amended (this
  package's own rule: never amend, always a new commit) — the correction
  lives only in the LOG/REPORT text. No test content is wrong, only that
  one arithmetic claim in the original commit message and first REPORT
  draft.

## What to read before touching C3 (this successor: fresh Sonnet, checkpoint
clause armed)

1. This BRIEF, then `cgprofile-P1-DAEMON-LOG.md` session 2's entry and the
   matching `-REPORT.md` C2 section — they record exactly what was and was
   not read this session, and why.
2. **`lib/damon.py` in full, again** — session 1 read it in full (session 1
   LOG line 15), session 2 did not re-open it (LOG session-2 orientation
   list explicitly flags this). C3 rewrites its `DamonSession`
   construction path, so read it fresh rather than trusting either
   predecessor's summary of it.
3. `scripts/damon-analysis/lib/damon_analysis.py`'s `Classifier` — never
   read by either session. The handoff (C3) says: "Classification
   hot/warm/cold/idle per aggregation using the classifier already
   reachable from this tier (`lib/damon.py`'s own, or a stdlib port of
   `scripts/damon-analysis/lib/damon_analysis.py` `Classifier` — pure
   Python; attribute it)." First check whether `lib/damon.py` already has
   a usable classifier (session 1's read might have seen one — check its
   own notes/the module itself) before assuming a fresh port from
   `damon-analysis` is needed.
4. `tests/test_damon.py` in full — flagged unread by BOTH prior sessions.
   The existing `DamonSession` test conventions (fake sysfs tree shape,
   injected clock if any) are what `KdamondPool`'s new tests must match.
5. **The plan of record's §4.4** (DAMON multiplexing) was read in session
   2 (LOG confirms) — trust it, no need to re-open, but re-read the
   handoff's own C3 paragraph fresh since it is denser than the plan
   summary.
6. Still not read by any session (unneeded for C0-C2, needed before
   C4/C6/C7): `DESIGN.md` §1/§3/§4.4/§4.7/§4.8/§6-§6a, `ATTACH-GUIDE.md`,
   `README.md`, `lib/sampler.py`, `lib/store.py`, `lib/events.py`,
   `lib/limits.py`, `lib/access.py` (whole), `lib/caps.py`,
   `tests/test_sampler.py`, the pwmcp shape (handoff read-list item 5), ciu
   governance lines (item 6), `dev-interactive.slice.in` (item 7). C3 only
   strictly needs items 2-4 above; the rest can wait for whichever session
   reaches C4 (`lib/sampler.py`/`lib/access.py` are load-bearing for
   `serve.py`) or C6/C7.

## Next deliverable: C3 — DAMON multiplexing (`KdamondPool`)

Per the handoff: `lib/damon.py` gets a `KdamondPool` that owns
`nr_kdamonds` — allocate the lowest free index (grow `nr_kdamonds` only
when no free index exists), **never shrink below the highest live index**
(the existing `DamonSession` teardown path that shrinks `nr_kdamonds` on
exit is the *controlled wrong implementation* the handoff explicitly names
— write the two-session red test FIRST, watch it fail against the current
code, then fix). Freed indices are reused. `DamonSession` takes an index
from the pool; targets = the subtree's pids (from C2's
`SubtreeResolver.current_pids`, already built and ready to wire in) as
vaddr targets, re-committed on topology change. Classification
hot/warm/cold/idle per aggregation interval, thresholds reported in the
summary (contract §3's `damon.thresholds` — `SummaryAccumulator` already
consumes a `{"hot": int, "warm": int, "cold": int, "idle": int}` bytes dict
per tick via `add_sample(damon=...)`, per its own module docstring — C3's
job is producing that dict per aggregation interval, not changing
`summary.py`). DAMON absent or sysfs read-only → `status: "unavailable"`,
reason string, session continues (does not fail).

**Required fake-sysfs tests** (per the handoff, red-first): two concurrent
sessions get indices 0 and 1; stopping session 0 while 1 still lives does
**not** write `nr_kdamonds` (must stay at whatever value session 1's index
requires); a third session started after that reuses index 0; stopping all
sessions shrinks `nr_kdamonds` back to the pre-daemon value.

**Suggested approach** (not binding, same discipline as C1/C2): read
`lib/damon.py`'s current `DamonSession` end-to-end first to find its exact
`nr_kdamonds` read/write points before designing `KdamondPool`'s API
around them — do not guess the shape from the handoff prose alone.

## Then C4 (`lib/serve.py`) → C5 (CLI) → C6 (image) → C7 (ciu) → C8 (cmru)
→ C9 (docs/backlog), in the handoff's own order — do not reorder.

## Standing rules (unchanged, re-stated for a fresh session)

- Work ONLY in this worktree, project dir `scripts/cgroup-profiler/`.
- Edit tool only for source changes — never sed/python rewrite scripts (a
  system-reminder in session 2 suggested otherwise for a different, unrelated
  reason; it was correctly overridden by this package's own standing rule
  and the cross-repo memory rule "Edit with apply_patch" — flagging so a
  successor does not second-guess this if the same reminder appears again).
- `git -C <worktree> commit -F <msgfile> --only -- <paths>`; both trailers
  (session 2 used the CURRENT top-level attribution reminder's trailers —
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` +
  `Claude-Session: .../session_01YBJzBA7KyG4ayu5ndNf9Hx` — NOT the
  handoff §7's older "Claude Fable 5.1" line, since the attribution
  reminder explicitly states it replaces earlier attribution guidance; a
  successor should do the same unless a newer reminder says otherwise).
  Gate green after every commit.
- Forbid: `run-gate-project/`, `ciu/src/`, `/workspaces/dstdns`, any
  host-mutating write from the daemon, `cmru release`, push, merge.
- Host load: check `docker ps --format '{{.Image}}'` for `tester-unified`
  before every gate run (cap 2 across the estate), serial pytest only,
  `nice -n 19 ionice -c 3`, at most one whole-suite run per checkpoint plus
  one before the eventual return.
- Checkpoint again at ~120k context or ~60 tool calls (whichever first),
  at the next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate).

## `/compact`-style retention prompt (if this session's context is
compacted rather than ended)

KEEP: the state-as-of-`b2481a34` summary above; the "what to read before C3"
list (especially: re-read `lib/damon.py`, read `tests/test_damon.py` and
the damon-analysis `Classifier` for the first time); the C3 required-tests
list (red-first, the exact 4 assertions: indices 0/1, no shrink while 1
lives, index 0 reused, full shrink on empty); the standing rules block
(especially the trailer/attribution note and the Edit-tool-only rule); the
exact gate commands and their known-green results so they are not re-run
speculatively (`tools/gate.sh <worktree> coverage` → 957/957, 100%/100%;
`run-gate.py --worktree <worktree> r3` → 7/7 rejected,
`history_eligible: true` against `b2481a34`). DROP: the C1/C2 golden-fixture
and branch-coverage arithmetic (fully recorded in REPORT, re-derivable from
the modules' own tests if ever needed again); the exact list of C2's 24
test names (the REPORT's class-by-class summary is enough); the blow-by-blow
of the 33-vs-24 test-count self-correction (recorded for transparency, not
operationally needed going forward — the number is 24, five classes, done).
