# cgprofile-P1-DAEMON — checkpoint BRIEF 3 (E-008)

Written at a clean boundary (one green-gate commit landed this session,
LOG/REPORT just written) rather than at a context/tool-call limit — this
session used roughly 45-50 tool calls and was not near either E-008
threshold, but C4 (`lib/serve.py`) is explicitly called out by the handoff
and by BRIEF-2 as large: a threading session server, an in-memory +
on-disk registry, retention, SIGTERM/SIGINT handling, and a single
host-mutation write-helper boundary with its own dedicated test. Landing
C3 solidly, gated and documented, is a better handoff than starting C4
with a half-spent budget.

## State as of `0cdfe89b`

- Worktree: `/workspaces/vbpub/.worktrees/rg55-profiler-daemon`, branch
  `rg55-profiler-daemon`.
- Commits so far: `688ea7bb` (C0, RW-3), `ba7a3167` (C1, `lib/summary.py`),
  `b2481a34` (C2, `lib/subtree.py`), `0cdfe89b` (C3, `lib/damon.py`
  `KdamondPool`). All green:
  - `run-gate.py r0-r1` exit 0 against `0cdfe89b` (clean tree):
    979 tests, 100%/100% line+branch across every module in
    `pyproject.toml`'s coverage `source` (`lib` + `cgprofile`).
  - `run-gate.py r3` exit 0 against `0cdfe89b`: 7/7 canaries rejected.
  - `.run-gate/history.json` confirms both lanes `"history_eligible":
    true"` at commit `0cdfe89b1a19ab921f44addd7921d4916789281a` (read in a
    separate step, matching `git rev-parse HEAD`).
- `r2` (assay mutation lane) still **not run** — intentionally, per the
  handoff ("once before your return"); this is not the return.
- No live-acceptance, ciu, cmru, or Docker-image work started.
- C3 delivered: `KdamondPool` (lowest-free-index acquire, never-shrink-
  while-another-index-lives release, baseline-restore-only-on-full-drain);
  `DamonSession` gained `pool=`, `recommit_targets(pids)`, `thresholds`
  (the contract's `damon.thresholds` block), `last_class_bytes` (the flat
  bytes-by-class dict `SummaryAccumulator.add_sample(damon=...)` expects).
  `collect()` now reuses one `Classifier` built at `__enter__` from the
  session's own configured thresholds. The pre-existing solo-index
  `DamonSession` path (`pool=None`, the default) is untouched — all 43
  pre-C3 tests pass verbatim.
- **Scoping note recorded (not a decision ask, RW-9 applied):** the
  handoff's C3 paragraph's "DAMON absent/read-only → status unavailable,
  session continues" is left to C4. `DamonSession.__enter__` already
  raises `DamonSessionError` on unavailability (unchanged, pre-existing
  behaviour); turning that into
  `SummaryAccumulator.mark_damon_unavailable(reason)` belongs to
  `lib/serve.py`'s per-session sampler thread, which C3 does not touch.
  **This successor must implement that catch in C4** — see "What C4 needs
  from C3" below.

## What to read before touching C4 (fresh successor, checkpoint clause
armed)

1. This BRIEF, then `cgprofile-P1-DAEMON-LOG.md` session 3's entry and the
   matching `-REPORT.md` "C3" section — they record exactly what was read
   this session and the full KdamondPool design reasoning (don't
   re-derive it from the diff alone).
2. **Not yet read by ANY session — required before C4:**
   - `DESIGN.md` §1 (tier split), §3 (run directory), §4.4 (`sampler.py`
     contract), §6/§6a (environment facts, known limitations).
   - `ATTACH-GUIDE.md` in full, `README.md` in full (verb table C9 will
     extend).
   - `lib/sampler.py` in full — C4's per-session thread wraps
     `Sampler`; know its exact constructor/`.sample()`/cadence contract
     before designing the thread loop around it.
   - `lib/store.py` in full — the series files C4 must write
     (`samples.jsonl.gz`, `host.jsonl`, `damon.jsonl`, `events.jsonl`,
     `manifest.json`, `summary.json`).
   - `lib/events.py` in full — `memory.events.local`/limit-drift watcher
     C4's sampler loop feeds.
   - `lib/access.py` in full — `have_host_cgroup_view()` is what C5's
     `serve` refuses-to-start-without check calls; C4's `SessionServer`
     likely also needs it or something adjacent for `ctl host`.
   - `lib/metrics.py`'s `sample_cgroup`/`sample_host` signatures (grepped
     already by session 2 for `read_cgroup_pids`, but the full sampling
     call shape for feeding `summary.py` was not confirmed).
   - `lib/targets.py`'s `find_container_cgroup` (both cgroup drivers) —
     `ctl start --target containerid:<hex>` resolves through this.
   - One example test file for socket/threading conventions if any
     already exist in this tree (`grep -rl socket tests/` first — may be
     none yet, in which case C4 sets the convention).
3. Re-read the handoff's C4 paragraph fresh (it is dense: registry,
   idempotency by (container id, token), `max_sessions`, retention,
   restart-finalizes-orphans, SIGTERM/SIGINT, the `WRITABLE_ROOTS`
   allowlist single-write-helper requirement). Re-read contract §1.5
   (timeouts — the daemon MUST answer `stop` within 30s for a session of
   ANY length, meaning the summary must already be complete
   incrementally, never recomputed from series at stop — this is exactly
   why `SummaryAccumulator` is an incremental accumulator, not a
   post-hoc reducer) and §2.2/§2.3/§2.5 (`start`/`status`/`stop` response
   shapes) and §5 (daemon safety: refuses `--cap`, never imports
   `caps.TempCaps`, `--network none`, no docker socket inside the daemon).

## What C4 needs from C3 (the actual wiring, not yet written)

- Construct ONE `KdamondPool()` at `SessionServer` startup (module- or
  instance-level, whichever `serve.py`'s own shape ends up being) and pass
  it as `pool=` to every `DamonSession` the server creates — never let two
  sessions share a bare, non-pooled `DamonSession` (that reproduces the
  red-first bug this session fixed).
- Per-session sampler thread: on session start with `--damon on` (or the
  daemon default), attempt `DamonSession(targets, pool=pool, ...)` and
  `.__enter__()` inside a `try/except` — on `DamonSessionError` (or
  `available()` being False before even trying), call
  `summary_acc.mark_damon_unavailable(str(exc))` and continue the session
  WITHOUT damon (never fail the whole session over this — contract-mandated).
- Each aggregation interval: call `session.collect()`, then
  `summary_acc.add_sample(damon=session.last_class_bytes)`.
  `summary_acc` itself must be constructed with `damon_enabled=True,
  damon_kdamond=session.kdamond_idx, damon_thresholds=session.thresholds`
  (all already exposed by C3).
- On topology change (subtree resolver discovers new pids at the
  discovery interval): call `damon_session.recommit_targets(list(new_pids))`
  — a no-op-safe call even if the new pid list is momentarily empty.
- On session stop (or daemon SIGTERM finalizing an orphan): tear the
  `DamonSession` down via its own `__exit__`/context-manager protocol (NOT
  by calling `pool.release()` directly — the session owns that call
  internally).

## Standing rules (unchanged, re-stated for a fresh session)

- Work ONLY in this worktree, project dir `scripts/cgroup-profiler/`.
- Edit tool only for source changes — never sed/python rewrite scripts (a
  system-reminder may suggest otherwise for an unrelated reason each
  session; this package's own standing rule and the cross-repo memory
  rule "Edit with apply_patch" override it every time — sessions 2 and 3
  both flagged this so a successor does not second-guess it again).
- `git -C <worktree> commit -F <msgfile> --only -- <paths>`; both current
  trailers (`Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` +
  the `Claude-Session:` line from THIS session's own top-level attribution
  reminder — it may carry a different session id than session 3's; use
  whatever the CURRENT reminder says, it explicitly supersedes older
  guidance including the handoff §7's original "Claude Fable 5.1" line).
  Gate green after every commit; read the verdict in a SEPARATE step from
  the log, never a pipe tail (LESSONS L4).
- Forbid: `run-gate-project/`, `ciu/src/`, `/workspaces/dstdns`, any
  host-mutating write from the daemon, `cmru release`, push, merge.
- Host load: check `docker ps --format '{{.Image}}'` for `tester-unified`
  before every gate run (cap 2 across the estate), check
  `/proc/pressure/memory` and `/proc/pressure/cpu` (PSI, never
  `load`/`free` alone), serial pytest only, `nice -n 19 ionice -c 3`, at
  most one whole-suite run per checkpoint plus one before the eventual
  return (a pre-commit dirty-tree sanity run plus a post-commit clean-tree
  recorded run, as sessions 2 and 3 both did, is the established pattern
  and does not count as two separate checkpoints' worth).
- Checkpoint again at ~120k context or ~60 tool calls (whichever first),
  at the next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate). C4 is large enough that it may
  need its OWN internal checkpoint before C5 — if so, cut after a green
  sub-cluster (e.g. the registry + idempotent start/stop landed and
  tested, before the retention/SIGTERM/write-helper pieces) and say
  exactly what remains in the next BRIEF.

## `/compact`-style retention prompt (if this session's context is
compacted rather than ended)

KEEP: the state-as-of-`0cdfe89b` summary above; the "what C4 needs from
C3" wiring list (pool construction, per-session try/except around
DamonSession entry, `last_class_bytes`/`thresholds` wiring into
`SummaryAccumulator`, `recommit_targets` on topology change, teardown via
the session's own context manager); the read-list for C4 (especially:
`lib/sampler.py`, `lib/store.py`, `lib/events.py`, `lib/access.py`,
`DESIGN.md` §4.4); the standing rules block (Edit-tool-only, trailers,
host load, checkpoint threshold). DROP: the C1/C2/C3 golden-fixture and
branch-coverage arithmetic and the exact list of C3's 21 new test names
(fully recorded in REPORT, re-derivable from `tests/test_damon.py` itself
if ever needed again); the red-first probe's exact assertion text (the
REPORT's summary — "0 instead of 2" — is enough); the blow-by-blow of
which specific tool calls were spent on orientation.
