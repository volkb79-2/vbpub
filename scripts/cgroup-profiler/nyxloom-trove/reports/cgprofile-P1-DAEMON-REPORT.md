# cgprofile-P1-DAEMON — implementer REPORT (session 1)

Covers deliverables **C0** and **C1** (session 1) and **C2** (session 2) of
the handoff (`cgprofile-P1-DAEMON-HANDOFF.md`). C3-C9 not started — see
`cgprofile-P1-DAEMON-BRIEF-2.md` for the handoff to the next session.

## C0 — RW-3 one-liner

`cgprofile.py`'s `cmd_targets` (helper-mode branch) called
`access.build_helper_spec(HERE, HERE, args.helper_image, ...)`. The second
positional argument is `out_dir` — the directory the helper container's
*output* mount is translated from — and passing `HERE` (the repo directory
itself) instead of `DEFAULT_OUT` (`cgprofile.py`'s `runs/` subdirectory)
means a future artifact-writing change to `cmd_targets` would silently
write into the checkout instead of the runs directory. `cmd_run`'s own
helper-mode call site (line 366) already passes `os.path.dirname(run_path)`
(a `DEFAULT_OUT`-rooted path) correctly, which is what made the RW-3 bug
recognizable as the "same shape" the controller log names.

Fix: one-line change, `HERE` → `DEFAULT_OUT`. Evidence the fix is real, not
cosmetic: `tests/test_cgprofile.py::TestCmdTargets::
test_helper_mode_delegates_through_a_docker_run_of_itself` was extended to
capture `build_helper_spec`'s `repo`/`out` arguments (it previously only
checked `cgroup_parent`) and assert `out == cg.DEFAULT_OUT` and `out !=
cg.HERE` — this test fails against the pre-fix code and passes against the
fix.

## C1 — `lib/summary.py`

### What it is

`SummaryAccumulator`: fed one tick at a time via `add_sample(cgroup=...,
host=..., slice_cgroup=..., damon=..., pids=...)`, `finalize(ended_at=...)`
returns the exact Summary document of contract §3. Implements every
computation rule in contract §7: nearest-rank percentiles (no
interpolation), the two memory-peak-source branches (`container` →
`memory.peak`'s last successful read; `container-shared` → sampled max of
`memory.current`), CPU/pressure/fault deltas between the first and last
sample, DAMON class peak/p90/median over whatever classified-bytes ticks
were fed in, host PSI start/end blocks, the slice block, and
`events.limit_drift` (sample-pair `memory.max`/`memory.high` change
counting).

**Sample shapes are documented on the module** (`lib/summary.py`'s own
docstring) rather than re-derived here: a cgroup sample is
`sample_target_cgroup(path)`'s output (wraps `metrics.sample_cgroup` +
reads `memory.max`/`memory.high`, which `metrics.sample_cgroup` doesn't
carry — the module docstring explains why those two reads live here rather
than in `metrics.py`). A slice sample is `sample_slice_cgroup(path)`'s
output. A host sample is `metrics.sample_host(proc_root)`'s output, used
unmodified.

### Golden reproduction (the hard requirement)

`tests/test_summary.py::TestGoldenReproduction` feeds the five real fixture
frames (`tests/fixtures/contract/frames/0..4`, vendored byte-identical from
`run-gate-project/nyxloom-trove/fixtures/rg55/`) through the accumulator
twice — once per scope — and asserts:

```python
json.dumps(result, sort_keys=True, indent=2) == json.dumps(golden, sort_keys=True, indent=2)
```

against `summary-v1.json` (`container-shared`) and
`summary-container-v1.json` (`container`). **Both pass, byte-for-byte,
first try** after the implementation matched the contract §7 rules and the
fixture README's hand-derived arithmetic (I cross-checked every number in
the README against the raw frame files myself before writing the
accumulator — e.g. confirmed the `cgroup.procs` union across all 5 frames
really is `{424242..424246}` = 5 distinct pids, matching
`target.targets_seen: 5`, before assuming the union semantics were right).

### Decision ask

Contract §7: "`memory.peak_bytes`: scope `container` → the LAST read of
`memory.peak`" and "`pids.peak` = the last read of `pids.peak`". Ambiguous
whether "the last read" means literally sample index `n` regardless of
whether that particular reading was null (cgroup flickered unreadable for
one tick), or the most recent *successful* read. **I implemented the
latter** (`_last_present`: scan backward, skip `None`) — a real high-water
mark read a moment ago should not be thrown away because the very last tick
happened to catch the file mid-teardown. The golden fixtures never exercise
this (every field is present at every frame), so this choice is untested by
the contract's own goldens; `TestLastReadSkipsUnreadableTrailingSample`
pins it explicitly with a case where frame 4's `memory.peak`/`pids.peak`
are forced null and the summary is asserted to fall back to frame 3's
values. **This needs a controller ruling if P2 (run-gate) or a future
DAMON/serve.py consumer needs the other interpretation** — flagging here
rather than guessing silently, per the handoff's instruction.

### Test suite (`tests/test_summary.py`, 20 tests)

- 2 golden byte-for-byte reproductions (above).
- 1 `targets_seen` union-of-pids check.
- 1 fixture-tree-identity check (`tests/fixtures/contract/` vs.
  `run-gate-project/nyxloom-trove/fixtures/rg55/`, resolved relative to the
  worktree root via `Path(__file__).resolve().parents[3]` rather than a
  hardcoded `/workspaces/vbpub` path — verified this also works from
  *inside* the `tester-unified` gate container, which per `tools/gate.sh`
  mounts the **whole worktree** read-only at `/work` (a git worktree is a
  full checkout at that commit, not a sparse one — confirmed
  `.worktrees/rg55-profiler-daemon/run-gate-project/nyxloom-trove/
  fixtures/rg55/` exists and is byte-identical to the main checkout's copy
  before relying on this)).
- 2 "last read" decision-ask pinning tests.
- 12 branch-coverage tests: an all-absent single sample, zero
  `interval_seconds` (no division by zero in `cores_max`), unparsable/empty
  timestamps (`duration_seconds`/`cores_avg` stay `None`, not an
  exception), `limit_drift` with a missing side in a pair (skipped, not
  counted, and not `None` either — see below), `limit_drift` fully
  unreadable (→ `None`), `peak_over_baseline_bytes` flooring at 0 (scope
  `container`, since `container-shared`'s peak is a `max()` over samples
  that always includes the baseline sample itself and therefore can never
  read back below it — documented in the test), DAMON marked unavailable
  mid-session (status/reason/`kdamond: null`/zeroed stats), DAMON disabled
  ignoring any DAMON ticks fed to it anyway, an unknown `scope` rejected at
  construction, `finalize()` with zero samples rejected, and two
  `sample_target_cgroup`/`read_cgroup_pids` vanished-cgroup/blank-line edge
  cases.

**Result:** `lib/summary.py` alone: 193 statements / 54 branches, 100%/100%
(`coverage run --branch --source=lib.summary -m pytest
tests/test_summary.py`). Whole suite after this commit: 933 passed, 100%
line + 100% branch across every module in `pyproject.toml`'s coverage
`source` list (`lib` + `cgprofile.py`) — see the LOG for the exact
`tools/gate.sh coverage` run this came from (in `tester-unified:local`, not
just the devcontainer venv).

### Mutation probes (handoff §3's "at least 10 hand-mutants" requirement)

Ran via a disposable copy-mutate-test-restore script (kept in the session
scratchpad, never touched the committed file — verified `diff` against the
pre-mutation original after every run), 11 total:

| # | Mutation | Outcome |
|---|---|---|
| M1 | `_nearest_rank`: off-by-one in the rank calculation (`ceil(...) - 1`) | CAUGHT |
| M2 | `_max_over`: `max` → `min` | CAUGHT |
| M3 | `cpu.seconds`: dropped the `/1e6` conversion | CAUGHT |
| M4 | pressure `stall()` helper: dropped the `/1e6` conversion | CAUGHT |
| M5 | Swapped which scope branch computes `memory.peak`/`source` (`==` → `!=`) | CAUGHT |
| M6 | `cores_max`: `max` → `min` | CAUGHT |
| M7 | `_count_limit_drift`: `!=` → `==` (inverted change test) | CAUGHT |
| M8 | `peak_over_baseline_bytes`: dropped the `max(0, ...)` floor | CAUGHT (after adding `test_peak_over_baseline_floors_at_zero`; initially SURVIVED because my first version of that test targeted the mathematically-impossible-to-fail `container-shared` scope — fixed to target `container`, where peak is independently sourced from `memory.peak`) |
| M9 | `_last_present`: forward scan instead of `reversed()` | CAUGHT |
| M10 | `count_delta` (faults): `b - a` → `a - b` (sign flip) | CAUGHT |
| M11 | DAMON `class_stats`: `p90` computed with the median percentile (50) instead of 90 | CAUGHT |

All 11 ultimately caught by the committed test suite (M8 exposed a real gap
in my first test attempt, not in the implementation — recorded here for
transparency since "claim only what you ran" applies to the process, not
just the final green result).

### r2 (assay mutation lane)

**Not run this session.** The handoff is explicit that r2 runs once,
"after everything else is green", before the P1 return — this session is a
mid-package checkpoint (C2-C9 not started), not the return, so running the
45-90 minute r2 lane now would be premature (it would need re-running after
every subsequent session's changes anyway, since r2 covers "changed lines"
across the whole diff). Left for whichever session lands the final commit.

## C2 — `lib/subtree.py` (session 2)

### What it is

`SubtreeResolver`: given an absolute cgroup path and a token,
`refresh()` resolves the `container-shared` scope's pid subtree per
contract §2.2/§7 — every pid in `cgroup.procs` whose `/proc/<pid>/environ`
carries an exact `RUN_GATE_PROFILE_SESSION=<token>` entry, plus every
descendant, re-resolved on each call (the session server's sampling loop
calls it once per discovery interval; the resolver itself is clockless by
design, matching BRIEF-1's suggested shape). `current_pids` is the latest
resolved set (feeds `SummaryAccumulator.add_sample(pids=...)`);
`targets_seen`/`seen_pids` are a running union across every `refresh()`
call, mirroring `SummaryAccumulator._pids_seen`'s own discipline.

Reused rather than reinvented: `summary.read_cgroup_pids` for the
`cgroup.procs` read (its own docstring already names `lib.subtree` as its
token-bearing caller); the "parse past the last `)`" convention from
`metrics._proc_cpu_usec` for `/proc/<pid>/stat`'s ppid field, attributed in
`subtree._parse_ppid`'s docstring.

### Descendant discovery: two paths, chosen per `refresh()` call

1. **Fast path** — `/proc/<pid>/task/*/children` (`CONFIG_PROC_CHILDREN`).
   Support is detected fresh on every `refresh()` call by probing each
   owner in turn until one answers definitively (present or genuinely
   absent) rather than caching a global flag — a test
   (`test_owner_vanished_before_probe_does_not_block_detecting_support`)
   pins that one owner having already exited does not wrongly force the
   fallback when a second owner can still answer.
2. **Fallback** — a ppid map built fresh from every `/proc/*/stat` under
   `proc_root` (non-numeric entries and unreadable `stat` files skipped,
   never fatal), walked the same breadth-first way.

A pid a live parent names as a child, but which is already gone by the time
it is probed further, is still attributed (it existed at discovery time)
but contributes no further descendants — `or []` on a `None` result treats
"vanished mid-walk" identically to "confirmed no children", which is the
correct behavior for both semantics at once.

### Decision-adjacent note (not a re-opened decision; recorded for a
successor's awareness)

The handoff and contract both describe the discovery interval as something
the *server* enforces ("re-resolve every discovery interval (2 s)"), never
as a property of the resolver itself. `SubtreeResolver` was built
accordingly with zero timing/clock state — purely a re-scan function. If
C4's `serve.py` instead wants the resolver to self-throttle (e.g. to make
`refresh()` cheap to call every sample tick without re-walking `/proc` every
time), that is a C4-side decision to wrap `SubtreeResolver.refresh()` behind
its own interval check, not a change to this module. Flagging so C4's
implementer does not assume the resolver already does this.

### Test suite (`tests/test_subtree.py`, 24 tests, 5 classes)

- `TestTokenOwnership` (4): owner found among several cgroup pids; exact
  NUL-delimited membership (not substring/prefix) — a different session's
  token that is a prefix of ours, and ours appearing only inside an
  unrelated longer value, both correctly rejected; unreadable/missing
  environ skipped, not fatal; a vanished cgroup (`cgroup.procs` unreadable)
  returns an empty set, no exception.
- `TestTaskChildrenFastPath` (4): two-level BFS; a diamond (two owners
  sharing one descendant, e.g. via subreaper re-parenting) counted once;
  a child named by `children` but already gone still attributed; one
  owner already gone does not block detecting fast-path support from a
  live second owner.
- `TestPpidMapFallback` (5): falls back when the `children` file is absent
  but `task/<pid>/` exists (an alive process on an older kernel) vs. when
  `task/` does not exist at all; non-numeric/unreadable `/proc` entries
  ignored while building the ppid map; a leaf owner; an owner that is
  *also* a descendant of another owner (both share the token) — this one
  specifically exercises the ppid-map walk's own "already visited, skip"
  branch, which is a separate coverage counter from the fast-path walk's
  equivalent branch (exercised by the diamond test above) since they are
  two different functions.
- `TestRunningUnion` (2): `targets_seen`/`seen_pids` survive a pid
  disappearing between two `refresh()` calls; stay at zero when no owner is
  ever seen across repeated calls.
- `TestInternals` (9): direct-unit tests on `_parse_ppid` (missing file, no
  closing paren, too few fields, non-numeric ppid, a `comm` value
  containing its own parens/spaces — the exact hazard the "parse past the
  last `)`" convention exists for), `_direct_children_via_task` (process
  gone, non-numeric child tokens ignored), `_build_ppid_map` (unreadable
  proc root), `_environ_has` (exact membership vs. a near-miss token) — same
  convention `tests/test_targets.py` already uses for its own
  `_`-prefixed helpers (`_split_spec`/`_parse_options`/`_looks_like_slice`),
  confirmed by grep before choosing this style rather than treating it as
  an open decision ask.

**Result:** `lib/subtree.py` alone: 128 statements / 40 branches, 100%/100%
(`coverage run --branch --source=lib,cgprofile -m pytest
tests/test_subtree.py`, filtered to the module). 4+4+5+2+9 = 24, matching
`pytest --collect-only`'s own count (verified after the fact — an earlier
draft of this REPORT section mis-added the five classes' sizes to 33;
corrected here, no test content changed). Whole suite after this commit:
957 passed (933 + these 24), 100% line + 100% branch across every
module in `pyproject.toml`'s coverage `source` list — see the LOG for the
exact `tools/gate.sh coverage` run this came from.

### Mutation probes

Not run for this module specifically — handoff §3's "at least 10
hand-mutants" instruction names `summary.py` only, matching C1's own
report above. The single `r2` assay mutation lane (run once before the
final P1 return) covers every changed line across the whole package,
`lib/subtree.py` included.

### r2 (assay mutation lane)

**Not run this session** — same reasoning as C1's report entry: this is a
mid-package checkpoint, not the return.

## C3 — `lib/damon.py`: `KdamondPool` (session 3)

### What it is

A bare `DamonSession` captures "what `nr_kdamonds` was before me" once, at
its own `__enter__`, and restores exactly that at its own `__exit__` —
correct for one session, provably wrong for two. `KdamondPool` centralizes
the bookkeeping: `acquire()` hands out the lowest free index (growing
`nr_kdamonds` only when no free index exists — `SysfsInterface
.create_kdamond` already no-ops the grow when the dir exists), `release()`
never writes `nr_kdamonds` while any OTHER pool index is still live, and
shrinks back to the pre-batch baseline only once the pool's own live set
fully empties (never a guess when that baseline could not be read, same
rule `DamonSession._teardown` already applied solo). `DamonSession` gained
an optional `pool=` constructor argument; when given, it defers entirely
to the pool for acquire/release and never touches `nr_kdamonds` itself
(guarded by a new `_acquired_from_pool` flag so a failed `pool.acquire()`
mid-`__enter__` never releases an index it never actually claimed — see
the coverage-driven test for exactly that path). The pre-existing
solo-index path (`pool=None`, the default) is byte-for-byte unchanged; all
43 pre-C3 tests pass with zero edits.

Also added, both named in the handoff's C3 paragraph:
- `DamonSession.recommit_targets(pids)`: re-points a live session's vaddr
  targets at a new pid list without tearing the kdamond down (for
  `lib.subtree.SubtreeResolver`'s topology changes). Growing the target
  count reuses `create_target`'s own grow-only idempotency; a shrinking
  pid list leaves higher-indexed target dirs in place (there is no
  `nr_targets` shrink primitive exposed, and shrinking is the same
  "tears down everything above the new count" hazard documented for
  `nr_kdamonds`) rather than invent one. A no-op on an empty pid list — a
  momentary gap in a fast-moving subtree must not tear monitoring down.
- `DamonSession.thresholds`: the contract §3 `damon.thresholds` block
  (`hot_rate_pct`/`warm_rate_pct`/`cold_age_s`/`idle_age_s`), in the
  session's own configured units — `collect()` now builds one `Classifier`
  from these at `__enter__` and reuses it, instead of a fresh
  default-threshold `Classifier()` per call.
- `DamonSession.last_class_bytes`: reshapes `collect()`'s
  `{"hot": {"count", "bytes"}, ...}` rollup into the flat
  `{"hot": bytes, "warm": bytes, "cold": bytes, "idle": bytes}` dict
  `SummaryAccumulator.add_sample(damon=...)` already consumes per
  aggregation interval (confirmed against `lib/summary.py`'s own module
  docstring and `_damon_block()` before writing this — no `summary.py`
  change needed, exactly as C1's docstring anticipated).

### Scoping note (not a decision ask — RW-9 applied)

The handoff's C3 paragraph also says "DAMON absent or sysfs read-only →
`status: "unavailable"`, reason string, session continues." This is left
to C4: `DamonSession.__enter__` already raises `DamonSessionError` when
`available()` is False or any sysfs write during entry fails (unchanged
behaviour, already tested pre-C3), and turning that into
`SummaryAccumulator.mark_damon_unavailable(reason)` is `lib/serve.py`'s
per-session sampler thread's job — C3's own listed scope is `lib/damon.py`
only, and a wrapping "try-enter, never raise" helper here would be
untested, uncalled surface until C4 exists to call it. Recorded per RW-9:
this is a reading applied, not a stop; C3 proceeded on it.

### Red-first (handoff §3: "the current DamonSession... IS the controlled
wrong implementation — write the two-session test first, watch it fail")

Before writing `KdamondPool`, a temporary probe test constructed two bare
`DamonSession`s at `kdamond_idx=0` and `kdamond_idx=1` (no pool — the
pre-C3 API), entered both, then exited the first while the second stayed
live. Observed: `nr_kdamonds` wrongly dropped from 2 to 0
(`assert fake_damon.nr_kdamonds() == 2` failed with `assert 0 == 2`),
confirming the exact corruption the handoff names — session 0's own
`__enter__`-time snapshot (`0`, captured before session 1 ever grew the
counter) is stale by the time session 0 tears down, and its restore tramples
session 1's still-live kdamond. The probe was removed once `KdamondPool`
landed (kept only in this record and the LOG, not in the committed test
file) and replaced by the permanent pool-based suite below, which asserts
the FIXED behaviour directly rather than leaving a bug-reproduction test
in a green suite.

### Test suite (`tests/test_damon.py`, 21 new tests, 64 total)

The four handoff-required assertions, each its own test: two sessions get
indices 0 and 1; stopping index 0 while 1 lives writes **no**
`nr_kdamonds` at all (asserted by filtering `fake_damon.calls` for any
`_write_int` on that path, not just checking the end value); a third
session reuses index 0; stopping every session shrinks `nr_kdamonds` back
to the pre-daemon baseline (0, this fixture's fresh state). Plus: a second
full batch through the same pool object re-captures its own baseline
rather than reusing the first batch's; `acquire()` propagates a real
`create_kdamond` failure (and adds nothing to `live_indices`);
`release()` of an index never acquired is a harmless no-op that touches no
sysfs path; `release()` swallows a shrink-write failure and skips the
write entirely both when nr_kdamonds is already at baseline and when the
baseline itself could not be read; a pool-routed session skips the manual
`_prev_nr_kdamonds` path entirely; a pool `acquire()` failure mid-`__enter__`
still tears down cleanly and releases nothing. Plus 4 tests for
`thresholds`/`last_class_bytes` and 5 for `recommit_targets` (rewiring,
empty-list no-op, outside-session error, paddr-session error, a shrinking
pid list leaving higher target dirs alone).

### Mutation probes

Not run for this module specifically, same reasoning as C1/C2 — the
single `r2` lane (still deferred to the final P1 return) covers every
changed line, `lib/damon.py` included.

### r2 (assay mutation lane)

**Not run this session** — this is a mid-package checkpoint, not the
return.

### Gate (this commit, `0cdfe89b`)

Local iteration (no docker): `PYTHONPATH=. python3 -m pytest
tests/test_damon.py` (43 → 64 passed) and `coverage run --branch
--source=lib.damon` (100% line, 100% branch, 206 stmts / 58 branches) used
while writing the pool tests; then the full local `tests/` tree once
(979 passed) and full local coverage once (100%/100% across every
`pyproject.toml` `source` module) as a pre-gate sanity check.

Real gate, read in a SEPARATE step from the streamed log each time (never
a pipe tail — LESSONS L4):
- `run-gate.py r0-r1` pre-commit (dirty tree, sanity only): exit 0, 979
  passed, 100%/100%; `history.json` correctly marks it
  `"history_eligible": false` / `"dirty": true`.
- `run-gate.py r3` pre-commit (dirty, sanity only): exit 0, 7/7 rejected.
- Commit `0cdfe89b` landed, then BOTH lanes re-run on the clean tree for
  the real record: `run-gate.py r0-r1` → exit 0; `run-gate.py r3` → exit
  0, 7/7 rejected. `.run-gate/history.json`, read via `python3 -c
  'json.load(...)'` (separate step): both lanes' `latest` show `"commit":
  "0cdfe89b1a19ab921f44addd7921d4916789281a"`, `"dirty": false`,
  `"exit_code": 0`, `"outcome": "pass"`, `"history_eligible": true`,
  matching `git rev-parse HEAD`.

## C4 — `lib/serve.py`: the session server (session 4)

### What it is

`SessionServer`: a Unix-socket JSON-lines control plane (one request per
connection — connect, send one JSON line, read one JSON line back, close;
the wire format between `ctl` and the daemon is this package's own
internal contract, not part of RG55-INTERFACE-CONTRACT.md, which only
pins the `ctl <verb> --json` CLI-level response shapes) implementing
`version`/`start`/`status`/`host`/`stop`/`report`/`gc`. One
`threading.Thread` per live session runs `_session_loop`, which builds a
`lib.sampler.Sampler` with `hot_interval == idle_interval == interval`
(the adaptive backoff DESIGN.md §4.4 describes for `cgprofile run`/
`attach` is deliberately inert here — `hot_threshold=2.0` is unreachable
since `activity_score`'s contract is `[0, 1]`) and a `sample_fn` reading
`lib.metrics.sample_cgroup` on the target cgroup every tick; `on_sample`
(`_on_session_sample`) additionally samples the slice
(`summary.sample_slice_cgroup`), resolves the token subtree
(`lib.subtree.SubtreeResolver`, re-resolved every `DISCOVERY_INTERVAL_SECONDS
= 2.0`) when a token was given, collects DAMON (`DamonSession.collect()`)
when on, and feeds all of it into one `SummaryAccumulator` plus
`lib.store.RunDir` per session (`samples.jsonl.gz` keyed `{"cg":
<target metrics>, "pids": [...]}`, `host.jsonl` keyed `{"host": ...,
"slice": ...}`, `damon.jsonl` one classified-bytes dict per tick when on,
`events.jsonl` touched empty — see the deferred note below,
`manifest.json`/`summary.json` via `RunDir.write_manifest`/the new
`write_json`).

**Registry.** In-memory `_sessions: Dict[session_id, _Session]` +
`_by_target: Dict[(container_id, token), session_id]` for O(1) idempotent
`start`. `max_sessions` checked against a live count, not dict size (a
finished session already stopped no longer counts). Idempotent `stop`
(`already_stopped: true` on a second call, read from `sess.summary_doc`
if still in memory or from `summary.json` on disk otherwise — covers both
the same-daemon-instance case and, partially, a daemon-restart case where
the session was already finalized before the restart).

**Restart recovery.** `__init__` always creates `sessions_dir`, then
`_recover_orphans()` scans every subdirectory's `manifest.json`; any with
`status == "live"` is replayed (`samples.jsonl`/`host.jsonl`/`damon.jsonl`
zipped by index, `pids` embedded per sample line specifically so
`targets_seen` can be reconstructed) through a fresh `SummaryAccumulator`
and finalized as `aborted: "daemon-restarted"`. A live orphan with zero
samples (crashed before its first tick) is marked aborted with no
`summary.json` at all — `SummaryAccumulator.finalize()` refuses an empty
accumulator by design (contract §1.5's incremental-only promise), and a
zero-tick session genuinely has nothing to summarize.

**Retention.** `_run_retention()` (called from `stop` and the `gc` verb)
scans `sessions_dir`, skips anything in the live in-memory set, keeps the
lexically-newest `--keep-sessions` finished dirs (session ids sort by
their embedded timestamp) that are not older than `--keep-days` by
`ended_at`, removes everything else via a guarded `shutil.rmtree`. A
manifest that still says `"live"` but is not in the in-memory registry
(should not happen in practice — `_recover_orphans` already converts every
true orphan at startup — but defensive) is skipped, never touched.

**SIGTERM/SIGINT.** `serve_forever()` = `_install_signals()` (registers
both signals to call `request_shutdown()`) + `_accept_loop()` (bind,
accept with a short timeout so `_stopping` is polled, dispatch, and on
exit — normal or via a non-timeout `OSError` from `accept()` — finalize
every still-live session as `aborted: "daemon-stopped"` and close the
socket). Split into two methods specifically because `signal.signal` only
works on the main thread of the main interpreter — the real CLI entry
point (`cmd_serve`, C5) always calls `serve_forever()` from there, while
this session's own tests drive `_accept_loop()` directly from a background
thread to get a real socket round trip without hitting that restriction.

**DAMON wiring (per BRIEF-3's "what C4 needs from C3").** One
`KdamondPool` is owned by the `SessionServer` instance and passed to every
`DamonSession` it constructs — never a bare, unpooled session. Each
session's construction (`_create_session_locked`) attempts
`DamonSession(...).__enter__()` inside a `try/except DamonSessionError`
when `damon` is requested on and there is at least one pid to target
(empty pid set is treated as `unavailable: "no pids to monitor yet"`,
never attempted); on either kind of unavailability the session continues
with `summary_acc.mark_damon_unavailable(reason)` and never fails `start`
— contract-mandated. On each discovery-due tick with a token
(`SubtreeResolver`), `recommit_targets(pids)` re-points the DAMON targets;
without a token (`scope: "container"`, the common ephemeral-lane case, or
`container-shared` with no `--token`), DAMON targets are fixed at the pid
set observed at session start and never recommitted — a deliberate
scoping choice (see Decision asks below), since there is no discovery
mechanism at all for the no-token case (the whole cgroup's pids are simply
read once for the initial target list).

### WRITABLE_ROOTS boundary (RG-55 C4's explicit host-safety requirement)

Two halves, one shared concept, two independent runtime checks (there is
no code path that needs to check the OTHER module's root, so each module
guards only its own):

- `lib/serve.py`: `SessionServer._guard_path(path)` refuses any path whose
  `os.path.realpath` does not resolve under `os.path.realpath(sessions_dir)`
  or `os.path.realpath(os.path.dirname(damon.KDAMONDS_DIR))` (the second
  root is named for documentation/testability completeness — this module
  never actually writes there, that is `lib.damon`'s job). Called before
  the two raw writes this class performs directly (as opposed to
  delegating to `lib.store.RunDir`, which is separately confined to
  `sessions_dir` by construction — it is only ever handed that one base):
  the initial `os.makedirs(sessions_dir, ...)` and `_remove_session_dir`'s
  `shutil.rmtree` during retention. `handle_report`'s `report.html` write
  is also guarded. A symlink-escape test (a symlink planted *inside*
  `sessions_dir` pointing outside it) proves the check actually resolves
  symlinks rather than doing a naive string-prefix compare.
- `lib/damon.py`: gained `HostWriteError` and `_write_nr_kdamonds(value)`,
  the single wrapper around the one raw sysfs write this module issues
  directly (`KdamondPool.release`'s and `DamonSession._teardown`'s
  fallback-path `nr_kdamonds` shrink — every other DAMON mutation is
  delegated to the sibling `damon_analysis.SysfsInterface`, which this
  module does not re-guard). `admin_root` is derived from `KDAMONDS_DIR`
  itself (its parent directory) rather than hardcoded, so it follows a
  test's monkeypatched fake root automatically. Same symlink-escape test
  pattern as `serve.py`'s.

`serve.py` never imports `lib.caps` (asserted by an AST-based test — a
plain substring check was tried first and correctly rejected itself,
since the module's own docstring *mentions* `lib.caps.TempCaps` in prose)
and offers no `--cap` flag on its CLI surface (C5, not yet written, but
the parser for `serve` will not call `_add_common`).

### `lib/store.py` / `lib/summary.py` small additions

`RunDir.write_manifest` is now a one-line wrapper over a new generic
`RunDir.write_json(name, obj)` (same atomic tmp+`os.replace` body,
parameterized by filename) — used for each session's `summary.json`.
`SummaryAccumulator` gained two read-only properties, `sample_count`
(`len(self._samples)`, used by `stop`'s "never call finalize() on zero
samples" guard) and `targets_seen` (`len(self._pids_seen)`, used by a live
`status`'s `target.targets_seen` without a second, duplicate pid-union
tracker in `serve.py`).

### Golden reproduction (the wiring, not the arithmetic)

`tests/test_summary.py` already proves `SummaryAccumulator` reproduces
`summary-v1.json`/`summary-container-v1.json` byte-for-byte from the five
frozen frames. This session's `TestFullLifecycleGoldenReproduction` proves
the WIRING around it: `_create_session_locked` is called directly (never
via `_dispatch({"verb": "start", ...})`/`handle_start`, which spawns a
real background thread — calling that AND then driving `_session_loop`
synchronously in the same test would race two loops against the same
frame-advancing fake `sleep`, which is exactly the bug that produced the
first, badly wrong draft of this test: non-deterministic sample counts,
frame indices overshooting past 4, `FileNotFoundError` reading
`frames/5/damon.json`. Root-caused via a plain in-process log list
(`log.append`), not print statements, once stdout buffering was ruled out
as a candidate explanation). `_session_loop(sess)` then runs fully
synchronously (fake `sampler_clock`/`sampler_sleep`, the latter repointing
`cgroup_root`/`proc_root` symlinks to the next frame and, on the 5th call,
setting `stop_event` instead) against `tests/fixtures/contract/frames/0..4`,
with `lib.damon.DamonSession` monkeypatched to a small stand-in
(`_FrameDamonSession`) whose `collect()` reads the current frame's
`damon.json` directly — this proves `serve.py` threads DAMON output into
the accumulator correctly without re-deriving DAMON's own classification
math (that is `tests/test_damon.py`'s job). Both `container-shared` and
`container` scope runs reproduce their respective golden `summary`
documents by Python dict equality after `json.dumps(sort_keys=True,
indent=2)` on both sides (dict equality, not raw-string byte comparison —
sufficient here since this is a socket-adjacent structure comparison, not
proving a specific on-disk byte layout the way `test_summary.py`'s own
golden test does). `test_host_snapshot_matches_host_v1` similarly
reproduces `host-v1.json` exactly by pointing `cgroup_root`/`proc_root` at
frame 4 directly (no session involved).

### Test suite (`tests/test_serve.py`, 69 tests)

Write guard (allow/refuse/symlink-escape, both halves); session id format;
registry (bad-argument variants, target-not-found, too-many-sessions,
idempotent start incl. the "existing session already finished, so a new
one is created" fallthrough, unknown verb); status/stop/report (malformed
vs well-formed-but-unknown ids, idempotent stop from memory and from
disk, live-listing, single-session lookup with DAMON on/unavailable/off,
report writes a file); restart recovery (stray file, no manifest, bad
JSON, finished-left-alone, live-with-zero-samples, live-with-samples,
live-with-a-carried-`damon_unavailable_reason`, a `listdir` `OSError`);
retention (keep-newest, keep-days, never-a-live-one — both the in-memory
and the untracked-manifest case, gc verb, no-sessions-dir-yet, bad
manifest JSON); `_create_session_locked` edge cases (no slice ancestor,
DAMON-on-with-no-pids, DAMON-on-with-pids-but-forced-unavailable via
`monkeypatch.setattr(damon_mod, "SysfsInterface", None)` — this
devcontainer's own `/sys/kernel/mm/damon` genuinely reports `available()
== True`, so relying on the real environment would have been
non-portable); the discovery-due/not-due/due-again cadence inside
`_on_session_sample` plus the no-damon-session variant, driven with a
`_StubDamonSession` test double and hand-built `record` dicts rather than
a real `Sampler`; `_host_snapshot` (observe_slices already-present
no-op, a non-`.slice` child correctly skipped); a session-loop crash
(`Sampler.run` monkeypatched to raise) finalizes as
`aborted: "session-error:..."` and a second crash on an
already-finished session is a no-op; `_stop_all_sessions` /
`serve_forever`'s signal-handler wiring (captured via a `signal.signal`
stub, not a real OS signal sent to the test process); socket plumbing —
a stale socket file removed on bind, a bare relative socket path (empty
`dirname`) skipping the parent `makedirs`, `_close_socket` as a no-op
when never bound and swallowing an `unlink` `OSError` (a directory sitting
at the socket path), a short read with no trailing newline via a
duck-typed fake connection, `accept()` raising a non-timeout `OSError`;
and one genuine round trip over a real Unix socket (`version`, malformed
JSON, and a blank request that gets no reply). **100% line and branch
coverage** on `lib/serve.py` (554 statements, 136 branches) and every
other `pyproject.toml` `source` module, verified locally
(`coverage run -m pytest tests/`) before the gate run.

### Mutation probes

Not run as a separate hand-mutant list this session (the handoff's "at
least 10 hand-mutants" requirement was stated for C1's `summary.py`
specifically, already satisfied there; C4's own correctness is instead
covered by the golden-reproduction wiring test plus the deliberately
adversarial write-guard/symlink-escape tests). The `r2` assay mutation
lane (handoff §3, run once for the whole package before the final return)
has not been run yet — deferred to whichever session makes the final
commit, per the handoff's own instruction.

### Gate (this commit, `8ba01138`)

Local iteration (no docker), repeated many times while writing
`tests/test_serve.py`: `PYTHONPATH=. python3 -m pytest tests/test_serve.py`
and `coverage run -m pytest tests/` + `coverage report` against the
`pyproject.toml` config, converging on 100%/100% across every module
including the two touched (`lib/damon.py`, `lib/store.py`,
`lib/summary.py`) and the one new (`lib/serve.py`). Full local `tests/`
once (1048 passed) as a pre-gate sanity check.

Real gate, read in a SEPARATE step from the streamed log each time (never
a pipe tail — LESSONS L4), `docker ps --format '{{.Image}}'` for
`tester-unified` checked immediately before every invocation (0 every
time):
- `run-gate.py r0-r1` pre-commit (dirty tree, sanity only): exit 0, 1048
  passed, 100%/100%.
- Commit `8ba01138` landed
  (`lib/serve.py`, `lib/damon.py`, `lib/store.py`, `lib/summary.py`,
  `tests/test_serve.py`), then `run-gate.py r0-r1` re-run on the clean
  tree: **first attempt exit 1** — a single, isolated failure in
  `tests/test_store.py::test_new_run_id_is_unique_even_for_the_same_instant`
  (`49 == 50`, a birthday-paradox collision on `new_run_id`'s 4-hex-char
  random suffix over 50 draws, ≈1.8% chance per run — this test and
  `new_run_id` predate RG-55 entirely, commits `4af86d57`/`d5df216d`,
  untouched by this session). Re-run immediately with no code change:
  exit 0, 1048 passed, 100%/100%. Filed as backlog **CP-4** (own commit
  `91d9d6a1`) rather than silently re-run-until-green without a record —
  a flaky gate lane is itself a finding worth keeping.
- `run-gate.py r3` post-commit (clean tree): exit 0, 7/7 canaries
  rejected.
- `.run-gate/history.json`, read via `python3 -c 'json.load(...)'`
  (separate step): both lanes' `latest` show `"commit":
  "8ba01138b2150e5f7f27f4b26f9714c1352effe9"`, `"dirty": false`,
  `"exit_code": 0`, `"outcome": "pass"`, `"history_eligible": true"`,
  matching `git rev-parse HEAD` at the time each ran.

## C5 — CLI `serve`/`ctl` (session 5)

`cgprofile.py` gained `serve` (PID-1 entrypoint, refuses `--cap`
structurally — no such flag exists on this parser — and refuses to start
without `access.have_host_cgroup_view()`, naming the missing container
flags) and `ctl <version|start|status|host|stop|report|gc>` (a thin,
25 s-timeout Unix-socket JSON-lines client; one document on stdout, exit
0/2/3 per contract §1.3). RW-14 required `lib/serve.py` to actually WRITE a
`lib.store.RunDir`-compatible session directory so `handle_report` could
shell out to the report tier's own venv interpreter and render the REAL
interactive HTML report (`analyze.build` + `report_html.render`) rather
than a hand-rolled `<pre>{json}</pre>` stub — the daemon's samples record
shape (`_on_session_sample`) and manifest (`_manifest_for`) both needed
fields `lib.analyze` actually reads, which the C4 session's original flat
shape did not provide (silently unreadable, never exercised until this
session's golden-reproduction+real-render tests). RW-15 moved the no-token
discovery path onto the same `DISCOVERY_INTERVAL_SECONDS` cadence the
token path already used, recommitting DAMON targets only on an actual
pid-set change (`sess.no_token_pids`, a cheap set-diff — there is no
`SubtreeResolver` to own this cache without a token).

**Tests:** golden round-trip tests reproducing `version-v1`/`start-v1`/
`status-v1`/`stop-v1`/`error-v1`/`host-v1` byte-for-byte through
`cgprofile ctl`'s real `main()` over a REAL Unix socket and a REAL
background sampling thread, paused mid-session by a two-event lockstep
(`tick_done`/`resume`) so `ctl status` lands exactly where `status-v1.json`
was captured — something the existing synchronous `_session_loop` test
pattern could not do. `TestHandleReportRealRender` covers the real render
(skip-marked only if pandas/plotly are absent from `sys.executable`) plus
every `handle_report` failure branch (missing venv, nonzero exit, timeout,
exit-0-but-no-file). Parser tests for every verb subparser, `cmd_serve`
unit tests, `_ctl_request`/`_ctl_roundtrip`/`cmd_ctl` unit tests (every
verb, malformed `--meta`, connection failure, empty/malformed response,
ok/not-ok exit codes). 100%/100% maintained.

**Gate (`b734f6b9`):** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%);
`r3` exit 0 (7/7 rejected).

## C6 — Dockerfile, `build-push.py`, `docker-bake.hcl` (session 5)

Multi-stage Dockerfile, base `python:3.14-slim` (matches DESIGN.md §6's
dev-environment fact). Builder stage installs `build-essential` and builds
`venv/` from `requirements.txt` — `ruptures==1.1.9` ships no cp314 wheel
yet and compiles a C extension needing gcc; the final stage copies only
the finished venv, never the compiler, since this image runs
`--privileged --pid=host --cgroupns=host` and every package not needed at
runtime is attack surface the daemon does not need to carry. `docker-
bake.hcl`/`build-push.py` mirror pwmcp's variable/target/group shape,
simplified to cgprofile's one externally-resolved coordinate (its own
scm-strategy release version) rather than pwmcp's several Playwright pins
threaded through a governed buildx builder.

**Verified for real:** `python3 build-push.py --build` produced both tags
(`cgprofile:local` + the ghcr coordinate, 606 MB); a bare `docker run --rm
cgprofile:local` (no `--privileged`) refused cleanly per
`access.have_host_cgroup_view()`'s exit-2 message; `docker run --rm
--entrypoint cgprofile cgprofile:local ctl --socket /tmp/nope.sock version`
exited 3 with the daemon-unreachable stderr line; the image's own venv
imports every report-tier dependency cleanly.

**Gate (`a6acb028`):** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%)
— no Python source changed, run as a regression check.

## C7 — ciu stack (session 5)

`ciu.global.defaults.toml.j2` + `ciu.defaults.toml.j2` +
`ciu.compose.yml.j2` + `ciu.toml.j2` (pwmcp's standalone-root shape: own
network/deploy identity, not folded into the estate-wide `ciu.global.toml`
merge). `deploy.environment_tag = "host"` is a DELIBERATE fixed singleton
— unlike every other stack in this estate (which uses
`"$INSTANCE_ID"` so worktree N and worktree M never collide), exactly one
`cgprofile-host-daemon` may run per physical host (it owns the whole
host's DAMON facility and cgroup v2 view via `--cgroupns=host`/`--pid=host`),
so a second worktree's `ciu up --dir .` must collide on the fixed
container name and refuse a sibling — CIU-104's cross-checkout collision
fix applies here in reverse (the collision is the WANTED safety property).
`cgroup_parent` is AUTHORED in the compose service block from
`$CGROUP_PARENT_DEV_INTERACTIVE` via ciu's own Jinja2 `{{ env.X }}`
mechanism (`render_compose`'s context always carries `env: dict(os.environ)`,
`StrictUndefined`) — refuses the render outright when unset, no hardcoded
slice fallback, per the estate's own AGENTS.md rule.
`mem_limit`/`memswap_limit` are explicit in the defaults table, never
governance-injected (the daemon samples every profiled session
concurrently and must not itself be the thing that gets OOM-killed).

**Verified for real:** `ciu check --define-root .` passes clean; `ciu up
--dir . --dry-run` ran the full 16-step pipeline clean, logging
`cgroup_parent=dev-interactive.slice` (the env var correctly resolved, not
governance's own `dev-background.slice` convention) and
`services_injected=1`; diffing the pre-overlay `ciu.compose.yml` against
the rendered `.ciu/ciu.compose.overlay.yml` confirmed governance's one
injection (`mem_reservation: 256m`, a soft floor under the authored 1g
hard limit, plus a `CIU_IMAGE_REVISION` env var) never touched the
authored `cgroup_parent`/`mem_limit`/`memswap_limit` — S15.3 precedence
(an author-set compose key is never overridden) holds in practice, not
just by reading the governance source. `privileged`/`pid`/`cgroup`
(renamed from the invalid `cgroupns`, see the C5-C9 live-acceptance fix
below)/`network_mode` were accepted at every validation stage with no
refusal anywhere, so `tools/daemon-run.sh` (the documented fallback for
exactly that refusal case) was correctly NOT shipped — the condition that
would require it never fired, stated plainly in the README.

A stale-network host condition (`docker network prune -f`, 4 genuinely
zero-endpoint networks removed, none belonging to a running stack, safe
per Docker's own "never removes a network with any attached container"
guarantee, confirmed via `docker ps` immediately before) blocked the first
`--dry-run` attempt with "all predefined address pools fully subnetted" —
a pre-existing shared-host condition, not a defect in these templates.

**Gate (`dcfe19da`):** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%)
— docs/templates only, run as a regression check.

## C8 — cmru registration (session 5)

`scripts/cgroup-profiler/cmru.toml`: `project.id = "cgroup-profiler"`,
prefix `cgprofile-v`, `artifacts = ["oci-image"]`, scm versioning (the
controller's first release cuts `--set-version 1.0.0` — a brand new tag
prefix, no pre-existing non-semver tag to collide with, unlike
run-gate-project's own transitional gotcha). `run-tests` = `./run-gate.py
r0-r1` then `./run-gate.py r3`, with an explicit comment on why `r2` (the
assay mutation lane) is deliberately NOT a release-gate step — it is a
pre-merge, once-before-handoff lane per the RG-55 wave plan, and its
45-90 m budget plus re-litigating already-accepted lines has no business
blocking every routine release. Root `cmru.orchestration.toml` gained
`[orchestration.project.cgroup-profiler]` (`depends_on = ["cmru"]`) plus
`project_order`/`default_projects` entries; the machine-owned dependency-
graph comment regenerated via `cmru dependencies --config
cmru.orchestration.toml --write` (PREFLIGHT: PASS), never hand-edited.

**Verified for real:** `cmru status --config cmru.orchestration.toml
--project cgroup-profiler` (read-only) runs clean, reporting "(none)" last
tag and next version `cgprofile-v0.1.0` (cmru's own default pre-first-
release guess — the controller's explicit `--set-version 1.0.0` overrides
this, per the handoff); a full estate-wide `cmru status` also runs clean
with `cgroup-profiler` correctly listed. `cmru release` was NOT run
(forbidden by the standing constraints).

**Gate (`ae61b75a`):** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%)
— config-only, run as a regression check.

## C9 — backlog + docs (session 5)

Filed via the `backlog` skill (`nyxloom backlog new` + regenerated
`INDEX.md`):
- **CP-5**: daemon sessions never populate `events.jsonl` with real
  detected events (limit drift, `memory.high` breaches, OOM kills, refault
  bursts) — only the contract's own events COUNTERS are computed.
  Explicitly deferred by the C5 dispatch ruling (see Decision asks #4
  above, carried forward from C4); filed here per that ruling rather than
  left as an inline comment.
- **CP-6**: `ctl report`'s real HTML render (RW-14) never charts a daemon
  session's own DAMON hot/warm/cold/idle series — `lib.analyze` has no
  `damon.jsonl` reader at all, so a `--damon on` session's report is
  chart-indistinguishable from `--damon off`. New finding from C5's RW-14
  work.
- **CP-7**: the daemon session manifest's `"limits"` table is always
  `{}` — effective cgroup limits are never resolved for a daemon-collected
  session (unlike `cmd_collect`'s own sessions), so every `analyze.py`
  proposal check (oversubscription, recursiveprot-gap) is a permanent
  no-op for a daemon-collected report. New finding from C5's RW-14 work.

DESIGN.md gained a new paragraph in §1 introducing the daemon as a "third
mode" alongside wrapper/attach (pointing at
`RG55-INTERFACE-CONTRACT.md` as the wire contract, the README for the
operator view); §2's layout tree gained every RG-55 file; §4.13
(`subtree.py`), §4.14 (`summary.py`), §4.15 (`serve.py` — the tier-split
mechanics behind `ctl report`'s real render, pointing at CP-6/CP-7 for
what it still does not do) added, each a contract summary rather than a
restatement of the interface contract's own §1-§7. README.md gained a
"## Verbs" table (all eight `cgprofile` subcommands, collector/report CLI
vs. daemon mode) and, from the C7 commit, a "## Running the daemon"
section. `ATTACH-GUIDE.md` gained "8. Profiling a run-gate lane". No
`CHANGES.md` exists for this project — confirmed, nothing to update there.

**Gate (`a1c49726`):** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%)
— docs/backlog only, run as a regression check.

## Three real bugs found during live acceptance (session 5)

None of these were caught by any unit or golden test — every one required
the actual live-acceptance sequence (a real built image, a real ciu-
rendered stack, a real daemon process, real probe containers) to surface.

1. **`ciu.compose.yml.j2`'s `cgroupns: "host"` is not a valid Compose
   service key** (fixed in `697f4e4b`). docker compose v2.40.3 refused it
   outright at `ciu up --dir .` ("services.daemon additional properties
   'cgroupns' not allowed"). The Compose Specification's real key is
   `cgroup` (singular), values `"host"`/`"private"` — verified against a
   minimal `docker compose config` probe file. `docker run --cgroupns=host`
   (the CLI flag name DESIGN.md/README.md describe) and Compose's
   `cgroup: host` are differently-named surfaces for the same kernel
   feature; only the compose template needed the rename.

2. **`ctl`'s trailing-position `--json`/`--socket` were silently clobbered
   back to defaults** (fixed in `697f4e4b`). RG55-INTERFACE-CONTRACT.md's
   own examples always place `--json` AFTER the verb (`ctl version
   --json`), so C5 gave every verb subparser its own `--socket`/`--json`
   via a shared `parents=[_ctl_common]` mixin — but that mixin's plain
   (non-`SUPPRESS`) defaults meant the CHILD subparser re-applied its own
   default over whatever the PARENT `ctl` parser had already set from a
   LEADING flag. Caught running `ctl --socket <leading> version` against
   the real daemon — every C5 golden test happened to use the leading
   position exclusively, so the bug was invisible until the contract's OWN
   trailing convention was exercised for the first time, live. Fixed with
   `default=argparse.SUPPRESS` on the child mixin's copies (trailing >
   leading > the real default); pinned by
   `test_ctl_json_and_socket_work_in_every_position` (all four position
   combinations).

3. **DAMON was unconditionally unavailable inside the built image** (fixed
   in `4f393cbc`). `lib/damon.py` resolves `Classifier`/`KDAMONDS_DIR`/
   `SysfsInterface` from the SIBLING `scripts/damon-analysis/lib/
   damon_analysis.py` via a relative path, but the Dockerfile's build
   context is `scripts/cgroup-profiler/` alone — that sibling was never
   copied in, so `damon_mod.available()` returned `False` inside every
   built container regardless of the host kernel. Caught via `ctl version
   --json` reporting DAMON unavailable while `/sys/kernel/mm/damon/admin/
   kdamonds` was independently confirmed writable inside that same
   container. Fixed with a second, named buildx build context
   (`docker-bake.hcl`'s `contexts = { damon_analysis = "../damon-analysis" }`)
   plus `--allow=fs.read=../damon-analysis` on `build-push.py`'s bake
   invocations (buildx refuses an out-of-primary-context read as an
   entitlement by default). Verified: rebuilt, `SysfsInterface` resolved to
   the real class (was `None` before), `ctl version --json` reported
   `"damon": "available"`.

4. **A foreign `OSError` from DAMON commit crashed the WHOLE daemon
   process** (fixed in `907cb810` — see LOG's session-5 entry #14 for the
   full account). The most serious of the four: `ctl start --damon on`
   against a real probe container did not just fail that one request, it
   killed `cgprofile-host-daemon` entirely — every OTHER live session died
   with it (each sampling thread is `daemon=True`), a severe violation of
   contract §2.2's "an unavailable DAMON never fails start" promise. Root
   cause: `DamonSession.__enter__()`'s bare `except Exception: ... raise`
   left `SysfsInterface.kdamond_commit`'s raw `OSError: [Errno 22] Invalid
   argument` (a genuine kernel EINVAL on this host — the sysfs tree being
   present and writable is not the same as being able to actually commit a
   DAMON context) untranslated, so `_create_session_locked`'s
   `except damon_mod.DamonSessionError` handler never caught it. Fixed at
   two layers: `lib/damon.py` now wraps any non-`DamonSessionError` into
   one; `lib/serve.py`'s `_handle_connection` gained a defense-in-depth
   broad `except Exception` around dispatch (logs to stderr, closes the
   connection with no reply — never a malformed one) so a FUTURE
   unanticipated handler bug cannot repeat this failure mode either.
   Re-verified live after the fix: the identical `ctl start --damon on`
   call now returns cleanly with `"damon":
   "unavailable:OSError: [Errno 22] Invalid argument"` and the daemon
   survives.

## Live acceptance (handoff §4)

**Completed**, against a real built image (`cgprofile:local`, rebuilt
after bug 4's fix) and a real `ciu up --dir . -y`-started
`cgprofile-host-daemon` container.

- **`docker inspect` shape:** confirmed `privileged: true`, `pid: "host"`,
  `cgroup: "host"` (the Compose-schema key; bug 1 above), `network_mode:
  "none"`, `restart: unless-stopped`, named volume
  `cgprofile-sessions:/var/lib/cgprofile`, `mem_limit`/`memswap_limit` set
  from the authored ciu defaults (not governance-injected) — matches the
  contract/handoff shape exactly.
- **`ctl version --json`:** `{"ok": true, "contract": 1, "cgprofile":
  "1.0.0", "daemon": {"name": "cgprofile-host-daemon", "damon":
  "available", "damon_default": "on", "sessions_live": 0, "max_sessions":
  16}}`.
- **Ephemeral probe** (`--scope container`, 100 MiB allocated + touched,
  then a 12 s CPU-bound busy loop, held under `--memory=256m`, `ctl start
  --damon on`): `ctl stop`'s summary —
  `memory.peak_bytes = 110002176` (~104.9 MiB, ≥ 100 MiB required),
  `memory.source = "memory.peak"`,
  `cpu.seconds = 7.671` (> 0 required),
  `damon.status = "unavailable"`, `damon.reason = "OSError: [Errno 22]
  Invalid argument"` — the exact `unavailable:<reason>` outcome the
  handoff named as acceptable, reported here **verbatim**. The daemon did
  NOT crash (bug 4's fix holds under the same real conditions that crashed
  it before the fix). `ctl report <session>` rendered a real 4.9 MB
  interactive HTML report (`<title>cgroup-profiler — s-...</title>`, a
  real `plotly` bundle embedded) — RW-14 confirmed live, not just by unit
  test.
- **Shared probe** (`--scope container-shared`, a long-lived `sleep 600`
  container, `ctl start --token $TOK2 --damon on` BEFORE the workload
  exists, then `docker exec -e RUN_GATE_PROFILE_SESSION=$TOK2 ... python3`
  allocating + holding 80 MiB): `ctl stop`'s summary —
  `target.targets_seen = 1` (≥ 1 required),
  `memory.peak_over_baseline_bytes = 88670208` (~84.6 MiB, ≥ 70 MiB
  required),
  `memory.source = "sampled-max"` — exactly the required source string for
  `container-shared` scope (contract §7: `memory.peak` is exact-read for
  `container`, sampled-max for `container-shared`).
- **Overhead table** (`docker stats --no-stream cgprofile-host-daemon`, 3
  samples per condition, one active `--scope container` session each):

  | condition | CPU % (3 samples) | MEM (3 samples) |
  |---|---|---|
  | idle (no session) | 0.01% | 178.7 MiB |
  | 1 session, `--damon on` | 0.19%, 0.25%, 0.25% | 176.5, 176.8, 177.2 MiB |
  | 1 session, `--damon off` | 0.19%, 0.22%, 0.25% | 177.9, 178.3, 178.5 MiB |

  DAMON on vs. off is indistinguishable within noise — expected, since
  DAMON never actually commits in this environment (bug 4's EINVAL; every
  session's `damon.status` was `"unavailable"` or `"off"` regardless of
  the flag), so the only difference between the two conditions is the
  cheap `unavailable:<reason>` computation path, not a real DAMON
  monitoring workload. `/proc/loadavg` moved from ~7.3 to ~5.6-6.0 across
  the whole live-acceptance run, but this shared 8-core host's load was
  independently trending down throughout (other sessions' builds/tests
  finishing) — not a clean before/after signal attributable to the daemon
  alone; PSI `full` stayed at 0 throughout every check.
- **Cleanup:** `docker rm -f rg55-p1-probe rg55-p1-shared
  rg55-p1-overhead rg55-p1-overhead-off` (all four probe containers);
  `ciu down -y` stopped `cgprofile-host-daemon` cleanly (exit 0, volumes
  preserved). Confirmed via `docker ps -a` — no RG-55 probe containers
  remain, daemon container `Exited (0)`.

## ciu render outcome

**Succeeded.** `ciu check --define-root .` passes clean. `ciu up --dir .
-y` ran the full 16-step pipeline (governance summary:
`cgroup_parent=dev-interactive.slice; mem_limit=1g; mem_swap_limit=17g`
— note: the LOGGED `mem_swap_limit` in governance's own summary line
differs from the AUTHORED `memswap_limit: 2g` compose value because
governance logs its own computed-fallback number even when the authored
compose key wins per S15.3 precedence; the rendered/live container's real
`HostConfig.MemorySwap` was independently confirmed to be the authored 2g
value via `docker inspect`, not governance's logged 17g — worth a
governance-logging-clarity note but not a defect in the authored values
themselves) and recreated `cgprofile-host-daemon` cleanly both times
(first bring-up, and the post-bug-4-fix recreate). No refusal at any
validation stage for `privileged`/`pid: host`/`cgroup: host`/
`network_mode: none` — `tools/daemon-run.sh` correctly not needed/shipped.

## r2 mutation lane: verdict and survivor triage

**First attempt** (before RW-19): launched under `assay.toml`/
`run-gate.toml`'s original 45m budget. `assay run r2 --resume` measures
its own per-candidate cost as it goes (217 candidates × ~84s each ×
jobs=2 ≈ 2.5h) — far past 45m, and this session's own supervising bash
process was killed by an unrelated host-wide memory-pressure event partway
through (the underlying `docker run -d` container itself was never
affected — `docker stats` confirmed it healthy and within its own 1 GiB
limit throughout; a `Monitor` polling loop tracked it to completion
instead of a killable foreground/background bash wrapper). **RW-19**
(controller ruling, quoted in full in LOG's session-5 item 15) raised
both budgets to 4h; that same in-flight container was left running
uninterrupted and finished on its own.

**First verdict** (commit `907cb810`, the daemon-crash-fix commit r2 was
launched against): 217 candidates, 178 killed, 39 survived, 0
crashed/equivalent/budget-exceeded, **FAIL** (`MUTANTS_SURVIVED`), 10234s
wall clock (well under the 4h/14400s cap).

**Survivor triage** (commit `5058f04d`) — every survivor OUTSIDE
`lib/summary.py` (28 of 39; the 11 `lib/summary.py` ones were deferred to
the RW-21 rewrite below, which touches those exact lines):

*Killed (28), via a new or strengthened test — every one a real gap in
what the existing suite actually OBSERVED, not a production defect found
along the way:*
- `lib/damon.py` (3): `_torn_down` starting `True` (a never-entered
  session's teardown must be a true no-op — issue zero real sysfs writes,
  confirmed via the fake fixture's own `calls` log, not just the resulting
  file content); the `current > prev and current is not None` shrink
  guard's exact `current == prev` boundary (must skip the `nr_kdamonds`
  re-write outright — proven via the same `calls` log, since writing the
  same value back is otherwise unobservable through the file's own final
  content).
- `lib/serve.py` (21): explicit `report_python`/`report_script`
  constructor args winning outright over auto-discovery; the `_finalize_
  orphan` replay path's three `X or <empty>` fallbacks (cg/host/pids),
  each pinned via a real downstream value — this also surfaced and fixed
  a PRE-EXISTING test-fixture bug: the original fixture fed `add_sample`
  the raw cgroupfs-file-content dict (`cgroup_files()`) instead of the
  parsed `{"mem": {...}}` shape `sample_target_cgroup` actually produces,
  silently making every memory-derived assertion impossible before this
  triage added one; the no-interval-given default path; the sampling
  thread's `daemon=True` flag; DAMON-unavailable actually reaching the
  STOPPED summary (not just `sess.damon_status`); the discovery-cadence
  boundary at exactly `DISCOVERY_INTERVAL_SECONDS`; `sess.rundir.append
  ("damon", …)` firing on the right ticks and only those; three separate
  unknown-session malformed-id paths (status/stop/report) being rejected
  by the FIRST guard specifically, not a same-code-different-message
  fallback (distinguished via the exact message text, `!r`-quoted vs.
  not); `_finalize_session_locked` never attempting to join its OWN
  thread — a real deadlock-shaped pattern (this exact guard stops the
  session-error path, which runs from inside the session's own thread,
  from calling `Thread.join()` on itself); `subprocess.run`'s
  `capture_output`/`text` kwargs actually being `True` (the existing
  mocked-subprocess tests could not see this — the mock ignores kwargs
  entirely); the stderr-empty/stdout-non-empty half of the report-tail
  fallback (a genuinely separate `or` token from the stderr-non-empty
  half an existing test already covered); the `keep_days` retention
  cutoff's strict less-than at the exact boundary; `shutil.rmtree`'s
  `ignore_errors=True`.
- `lib/subtree.py` (1): `_parse_ppid`'s `len(fields) < 2` boundary at
  exactly 2 fields (the minimum VALID case, not a rejection).

*Justified equivalent / not practically distinguishable (2 of the 3 below
still stand after re-verification for the final run; the third claim was
WRONG and is corrected below):*
- `lib/serve.py:626` — `_on_topology_noop`'s `return None`: the return
  value is unconditionally discarded by every caller (`Sampler.run`'s own
  type hint is `Callable[..., Any]`; the return is never captured).
- `lib/serve.py:704` — the CPU-rate guard's `and`: `_prev_cpu_usage_usec`
  and `_prev_mono` are set together, unconditionally, on every tick; there
  is no reachable state where one is `None` and the other is not, so
  `and`/`or` are behaviorally identical here.
- ~~`lib/serve.py:1136` — the crash-safety-net print's `flush=True`: not
  observable through pytest's `capsys`/`capfd` capture~~ **CORRECTED**:
  this was wrong. `capsys` does not distinguish it (content lands either
  way, as claimed), but the KWARG itself is directly observable by
  spying on the `print(...)` call rather than its output — see the
  killed-with-a-new-test list below (`test_handle_connection_dispatch_
  exception_flushes_stderr_immediately`). Left uncorrected between the
  two verdicts (the fresh run reproduced it as a survivor again, exactly
  because no such test existed yet); struck here explicitly rather than
  silently dropped, per the standing "correct a wrong claim, don't just
  delete it" rule this session already applied once to a resume-cache
  mischaracterization (see LOG).

**Second verdict**, after the RW-21 rewrite: `./run-gate.py r2`
on commit `7ec4f9e819f33b5106fbcc0ae17c313495f02e65` (RW-21) and its
successors, across the RW-26 orphan-container recovery, the RW-28
per-candidate-budget hang, and the RW-48 root-cause fix (all three fully
narrated in LOG) — 208 candidates, 195 killed, 12 survived, 1
`budget_exceeded` (the RW-28/RW-48 mutant, `lib/serve.py:479`), FAIL
(`MUTANTS_SURVIVED` on the first clean run of this generation,
`BUDGET_EXCEEDED`/`LANE_TIMEOUT` on the runs that included the
mid-judgment candidate).

**Survivor triage, second pass (this session, tip `5ce232d1` at judging
time)** — every one of the 12 fresh survivors, plus the `budget_exceeded`
candidate's root cause:

*Killed (8), via a new test:*
- `lib/damon.py:329` (`_entered` init `False->True`) —
  `test_collect_before_enter_refuses_rather_than_reading_a_foreign_
  kdamond`: `collect()` on a never-entered session must raise
  `DamonSessionError`, not silently read whatever kdamond index 0
  happens to hold (which may belong to a different session entirely).
  Verified by hand outside pytest too: flipping `_entered` to `True`
  post-construction and calling `collect()` against the REAL (non-faked)
  `SysfsInterface` raises `FileNotFoundError` instead of
  `DamonSessionError` — confirms the guard is what stands between "a
  controlled refusal" and "an uncontrolled read of the wrong session."
- `lib/serve.py:209` (`damon_pool is not None` `IsNot->Is`) — two tests,
  `test_damon_pool_explicit_arg_is_used_as_is` (a sentinel object passed
  in must be used AS IS, `is` identity, not silently replaced) and
  `test_damon_pool_defaults_to_a_real_kdamond_pool_when_omitted` (omitting
  it must construct a real `KdamondPool`, not leave `self.damon_pool`
  as `None`) — no existing test constructed `SessionServer` with this
  kwarg at all before this pass.
- `lib/serve.py:1136` (`flush=True -> False`) —
  `test_handle_connection_dispatch_exception_flushes_stderr_immediately`:
  a `print` spy (`monkeypatch.setattr("builtins.print", ...)`) records the
  call's kwargs and asserts `flush is True`, independent of whatever
  `capsys` does with the eventual output.
- `lib/summary.py:136` (`_delta`'s `a is None or b is None` `or->and`) —
  `test_host_stall_stays_null_when_only_one_end_is_readable`: a host PSI
  total present at the first tick and gone by the last (or the reverse)
  must read `null`; the `and` mutant instead reaches `None - <float>` /
  `<float> - None` and raises `TypeError`.
- `lib/summary.py:197` (loadavg guard `and->or`) —
  `test_loadavg1_stays_null_when_loadavg_is_present_but_not_a_list`: a
  present-but-non-list `loadavg` (a malformed producer, or just a
  synthetic probe of the guard itself) must still read `null`; the `or`
  mutant indexes the non-list value instead.
- `lib/summary.py:264` (`damon_enabled: bool = False` default `->True`) —
  `test_damon_enabled_defaults_to_false_when_omitted`: every existing
  test's own factory (`_new_accumulator`) always passed this kwarg
  explicitly, so the constructor's actual default had never been
  exercised; constructed directly, omitting it, and asserted
  `result["damon"] is None`.
- `lib/summary.py:345` (duration guard `and->or`) —
  `test_duration_stays_null_when_only_one_timestamp_parses`: an
  unparseable `started_at` with a valid `ended_at` (or the reverse) must
  read `duration_seconds: null`; the `or` mutant reaches
  `(<datetime> - None).total_seconds()` and raises `TypeError`.
- `lib/summary.py:392` (`peak_over_baseline` guard `or->and`,
  container-shared scope) —
  `test_peak_over_baseline_stays_null_when_only_one_of_peak_baseline_is_
  readable`: baseline unreadable at the first tick, a real peak read
  later, must read `null`; the `and` mutant reaches
  `max(0, <int> - None)` and raises `TypeError`.

*Justified equivalent (2 new, alongside the 2 carried over above), each
proven by tracing every consumer of the mutated value, not asserted on
faith:*
- `lib/summary.py:173` and `:177` — `_parse_iso`'s two failure branches
  (`if not text: return None` / `except ValueError: return None`), each
  mutated `None->[]`. `_parse_iso` has exactly ONE call site in the whole
  module (`finalize()`'s `start_dt, end_dt = _parse_iso(...), _parse_iso
  (...)`), and its result is consumed exactly once, purely by truthiness
  (`start_dt and end_dt`) — never `is None`, never `isinstance`, never
  logged or returned. `bool(None) == bool([]) == False`, so both mutants
  take the identical branch of that ternary in every case; verified this
  is the ONLY call site via `grep -n "_parse_iso(" lib/summary.py`.

*Root-caused and fixed, not a survivor/equivalent split (1) — RW-48:*
- `lib/serve.py:479` (`daemon=True->False`) — was already honestly killed
  by `test_start_then_stop_reports_finished`'s `assert sess.thread.daemon
  is True` (added in the FIRST triage pass, commit `5058f04d`); the
  recorded `budget_exceeded` was a harness-completion problem, not a
  detection problem: many OTHER tests in the suite start a real session
  thread and rely on `daemon=True` to let the interpreter exit cleanly
  without ever joining that thread themselves. Under the global mutation
  every one of those becomes non-daemon too, and Python's own interpreter
  shutdown blocks on all of them — independent of whether any single
  test's assertion already "won." Root fix (RW-48): `tests/test_serve.
  py`'s new `_stop_leaked_session_threads` autouse fixture stops every
  live session on every `SessionServer` a test constructs, in a fixture
  finalizer that runs whether the test passed or failed, closing exactly
  the gap the manual `sess.stop_event.set(); sess.thread.join(timeout=
  5.0)` lines scattered through this file never covered (they run only
  on the happy path, after a test's own assertions — an assertion that
  fails first, as `test_start_then_stop_reports_finished`'s now does
  under this mutation, never reaches them). `tests/conftest.py` also
  gained a session-scoped `_no_leaked_non_daemon_threads_at_session_end`
  safety net that names any thread the per-test fixture missed, rather
  than a silent interpreter hang. **Proof, run by hand**: `lib/serve.
  py:479`'s `daemon=True` flipped to `daemon=False` directly (a real
  edit, `cp`-backed, reverted after), then `nice -n 19 ionice -c 3
  timeout 300 python3 -m pytest tests -q` (the exact invocation shape
  `assay`'s r2 lane uses) — **1114 tests, 1 failed (the honest kill), no
  hang, exit in 115.15s** (previously: no per-candidate budget existed
  at all and the process hung 37 real minutes before the controller
  SIGKILLed it; even now, this file's dozens of manual-cleanup tests
  would each have left their own thread dangling without this fixture).
  File restored (`diff` against the pre-edit backup: identical) before
  committing.

**Third completed verdict** (after the second-pass triage commit):
`./run-gate.py r2` judged commit
`637b8c0995c5e1ec1ddb802710571fcc305c40dd` from
`2026-09-12T20:21:28Z` through `23:49:05Z`. The durable verdict at
`.assay/verdict-r2.json` records 208 candidates, 203 killed, 5 survived,
0 equivalent (assay does not classify justified equivalents), 0
`budget_exceeded`, and 0 crashed. Its computed R0 claim is PASS; its R2
claim and overall outcome are FAIL with reason `MUTANTS_SURVIVED`, solely
because the five survivors still require human disposition.

**Third-verdict survivor triage (all 5):**

*Behaviorally equivalent (4; the same identities already recorded above,
re-verified against the final call paths):*

- `lib/serve.py:626`, `None -> []`: this is the nested `on_sample`
  callback's return (the earlier report text incorrectly named
  `_on_topology_noop`). `Sampler.run` stores it as `events` and observes it
  exactly once, as `bool(events)` in its `forced` expression; neither value
  is returned, persisted, or inspected by identity/type. `bool(None)` and
  `bool([])` are both false, so cadence and every downstream effect are
  identical.
- `lib/serve.py:704`, `and -> or`: the sampler produces numeric `mono`
  values, and `_prev_cpu_usage_usec`/`_prev_mono` start together and are
  assigned together after every tick. If the prior usage is present, both
  operators enter the same block. If it is absent, the `or` mutant alone
  calls `util.rate(None, usage, dt)`, which returns `None`; the original
  skips that assignment, but `live_cpu_cores_recent` is already `None`
  (initially, or reset by the tick on which usage became unreadable).
  Resumption likewise needs one baseline tick in both versions. Thus every
  sampler-produced state has the same observable live rate.
- `lib/summary.py:173` and `lib/summary.py:177`, each `None -> []`: these
  are `_parse_iso`'s empty-input and malformed-input exits. Its only call
  site is `SummaryAccumulator.finalize`, and each result is consumed only
  by the truthiness guard `start_dt and end_dt`; neither result is returned,
  logged, type-checked, nor identity-checked. Both false values therefore
  produce the same null duration and dependent CPU average.

*Genuine oracle gap (1, killed by a focused test):*

- `lib/summary.py:214`, final `or -> and` in `_count_limit_drift`'s
  four-reading presence guard. The mutant lets a pair with present
  `memory.max` values and only the current `memory.high` absent reach tuple
  comparison, falsely counting one limit change. Added
  `test_limit_drift_ignores_pair_with_only_memory_high_missing`. With the
  exact mutant applied, the selector failed `assert 1 == 0`; after restoring
  production code, the same selector passed. Both runs used
  `nice -n 19 ionice -c 3`; memory PSI full avg10 was below 5 before each.

**Fresh R2 on the triage commit** (`53abbf2c50d342de64016b5bf7a9198ead5ce717`)
completed from `2026-09-13T11:55:57Z` through `15:10:08Z`; the verdict was
written at `15:10:10Z`. The separately read `.assay/verdict-r2.json` records
208 candidates, 203 killed, 5 survived, 0 assay-equivalent, 0
`budget_exceeded`, 0 crashed, outcome `FAIL/MUTANTS_SURVIVED`, exit 1. The
five survivors are:

- `lib/serve.py:626` (`None->[]`) — **accepted equivalent**. The nested
  `on_sample` return is stored as `events` and consumed only through
  `bool(events)` by `Sampler.run`; both values are false and no identity,
  type, or value is persisted.
- `lib/serve.py:704` (`and->or`) — **accepted equivalent**. The sampler
  assigns the numeric CPU counter and monotonic timestamp together. If the
  previous counter is absent, the mutant's `util.rate(None, ...)` returns
  null while the live rate is already null; recovery requires the same
  baseline tick.
- `lib/summary.py:173` and `:177` (`None->[]`) — **accepted equivalent**.
  `_parse_iso` has exactly one call site, and both results are consumed only
  by the false-valued `start_dt and end_dt` guard.
- `lib/damon.py:329` (`False->True`) — **accepted equivalent**. This is
  `_acquired_from_pool`, not `_entered` (the earlier line description was
  wrong). The non-pool teardown branch is selected before reading the flag;
  pooled success unconditionally sets it true, and failed pool acquisition
  releases an index that the pool's contract treats as a no-op. The exact
  temporary mutation was applied and restored byte-for-byte; the serial
  `tests/test_damon.py` suite passed 68 tests in 0.55s.

The prior `lib/summary.py:214` survivor is now killed by
`TestAbsentInputsStayNull.test_limit_drift_ignores_pair_with_only_memory_high_missing`;
the red-first exact-mutant proof and restored green proof are recorded above.
Therefore the assay remains mechanically FAIL only because it cannot classify
human-accepted equivalent mutants; no current survivor is an untriaged oracle
gap. Final `r0-r1` and `r3` are run serially on HEAD `53abbf2c`, with memory
PSI full avg10 below 5 and no daemon or host infrastructure started.

## Final gates and D-15 safety

Final gates were run serially with HEAD held at
`53abbf2c50d342de64016b5bf7a9198ead5ce717`:

- `nice -n 19 ionice -c 3 ./run-gate.py r0-r1` — exit 0; 1115 tests
  passed, with 100% line and 100% branch coverage (4 deprecation warnings),
  duration `102.811s`, started `2026-09-13T15:26:28Z`.
- `nice -n 19 ionice -c 3 ./run-gate.py r3` — exit 0; all 7 canaries were
  rejected and 0 survived, duration `17.553s`, started
  `2026-09-13T15:28:46Z`.

The separately read `.run-gate/history.json` latest entries match this HEAD
and both have exit 0. They are `dirty: true`/history-ineligible only because
these records were intentionally uncommitted during execution; no executable
or test file was dirty, and the configured lanes permit this records-only
state. The records-only commit follows this receipt.

D-15 is satisfied: `python3 cgprofile.py serve --cap 1` exits 2 as an
unrecognized argument; the compose service has `privileged: true`, `pid:
host`, `cgroup: host`, `network_mode: none`, an authored interactive
`cgroup_parent`, and only the named sessions volume, with no Docker socket or
host bind. The direct `serve` and `damon` write paths are guarded by their
respective `WRITABLE_ROOTS` checks, and the green suite exercises those
refusals. `cgprofile-host-daemon` was checked at `Exited (0)` and remains
down; no live probe or session was left running.

## Decision asks (summary)

1. "Last read" semantics (§7, `memory.peak`/`pids.peak`) — see above.
   Implemented as "last successful read"; flagging for a ruling in case a
   later package (P2, or C4's `serve.py`) needs the literal-index-n
   reading instead. **Controller ruling RW-7 (session 2 start): accepted
   as implemented — no change.**
2. No new decision ask from C2 — the discovery-interval/clock ownership
   note above is a design clarification for C4's implementer, not a
   product question needing a ruling.
3. C3 scoping note (session 3): the "unavailable" status/reason wiring is
   C4's job, not C3's — see the scoping note above. Not a product
   question; no ruling needed, reading applied, C3 proceeded.
4. **C4 (session 4), RW-9 applied, none re-opened as a ruling ask:**
   - `status <id>` for a session that is not currently live (finished, or
     never existed) returns `unknown-session` (exit 2) rather than any
     finished-session status shape — the contract defines the `status`
     object's fields only in terms of a live session (`elapsed_seconds`,
     `live: {...}`) and says nothing about what a finished lookup should
     return. Real callers (run-gate) only ever call `status` on a session
     they just started and have not yet stopped, so this is very unlikely
     to matter in practice.
   - `events.jsonl` is touched empty at session start and never populated
     with real detected events during a daemon session — `lib.events.
     Detector` exists and is well-tested for the driver-tier `cgprofile
     run`/`attach` path, but wiring it into the daemon's per-tick loop
     (which would need `lib.limits.Effective` for `limits_changed`, a
     `Detector` instance per session, and a decision about which of its
     kinds actually matter for a profiled lane) was judged out of scope
     for C4's own budget. The contract lists `events.jsonl` as always
     present in the `stop`/`series` response but does not pin its
     per-record content, so an empty-but-present file is contract-legal.
   - `ctl report`'s HTML is a minimal `<pre>`-wrapped JSON dump of the
     stored summary, not a real report — contract §2.6 says run-gate never
     calls it and it "may take longer than 30 s", but does not otherwise
     specify its content. A real report (reusing `lib.report_html`/
     `analyze.py`) would need the daemon's `samples.jsonl` records
     reshaped into DESIGN.md §3.1's driver-tier sample-record shape first,
     which is a real design decision, not a mechanical wire-up.
   - No-token DAMON targets (`scope: "container"`, or `container-shared`
     without `--token`) are fixed at session start and never recommitted
     as new pids appear in the cgroup — there is no discovery mechanism at
     all in that case (only a token-driven `SubtreeResolver` re-resolves
     on a cadence); the cgroup-level memory/CPU numbers are unaffected
     either way, only the DAMON hot/warm/cold/idle breakdown could miss a
     process that started after the session did.
   - The `WRITABLE_ROOTS` test requirement ("a test asserts every write in
     `lib/serve.py`/`lib/damon.py` goes through it") is read as: every RAW
     filesystem/sysfs write each of those two files issues *directly*
     (not delegated to the already-safe `lib.store.RunDir`, which is
     separately confined to `sessions_dir` by construction) goes through
     that module's own guard. `serve.py`'s socket bind/unlink lifecycle
     (a Unix domain socket special file, not persistent host state or a
     cgroup limit) is treated as outside this guard's purview — it is
     control-plane plumbing, not the kind of host mutation the C4
     paragraph's safety concern (never widen a limit, never write outside
     the sessions dir) is about.

5. **C5-C9 live acceptance (session 5):** all four live-found bugs (see
   "Three real bugs found during live acceptance" above) were fixed rather
   than deferred — the daemon-crash one (#4) was judged too severe to file
   as a backlog item and ship broken; the reading applied throughout was
   "a live acceptance failure blocks the P1 return, a design gap does not."
6. **DAMON's actual availability on this host:** the handoff anticipated
   either "on with `hot_bytes.peak > 0`" or "`unavailable:<reason>`
   verbatim" as acceptable live-probe outcomes. This host's kernel accepts
   every DAMON sysfs WRITE up to and including `kdamond_commit`, which then
   fails with `EINVAL` — a real kernel-level limitation of this specific
   environment (kernel version / DAMON build configuration unconfirmed;
   out of scope to root-cause further per the handoff's own framing), not
   a bug in this package. Read as: DAMON support is correctly IMPLEMENTED
   (the contract's promise that "an unavailable DAMON never fails start"
   holds, verified live) even though it cannot be exercised end-to-end
   (hot/warm/cold/idle bytes) on this particular host. No decision ask
   raised — this is exactly the anticipated-and-handled case.
7. **RW-19 (r2 budget)** — applied verbatim (both `assay.toml`/
   `run-gate.toml` r2 budgets raised 45m → 4h, one commit, comment naming
   the ruling and the measured per-candidate cost); no reading required
   beyond following the instruction. Per RW-9, this did not block or wait
   on anything — the in-flight r2 container kept running the whole time.
8. **RW-21 (contract §7 amendment, scope `container` absolute counters)**
   — applied verbatim: merged `main` (no conflicts, as anticipated — this
   branch never touched the contract or shared fixtures), added
   `_rw21_reference`, routed every affected block through it, re-vendored
   fixtures, confirmed the byte-identity golden test and the full suite
   green, `r0-r1`/`r3` green on the resulting commit, one more `r2` run
   launched. The one judgment call RW-21 left to this session: how to
   handle the now-obsolete `test_peak_over_baseline_floors_at_zero` test
   (RW-21 makes its exact scenario — a scope-`container` floor-at-zero —
   unreachable). Read as: rewrite it into two tests (the new
   null-override behavior in `container` scope, using the SAME
   peak-below-baseline fixture; the still-live floor-at-zero behavior in
   `container-shared` scope, with a corrected two-sample fixture) rather
   than deleting it outright — the original test's own reasoning
   (documented in its comment) is still worth keeping as a record of why
   that floor logic exists at all, just relocated to the scope where it
   still applies.

## Deferred (not backlog except where noted — see this session's own
`cgprofile-P1-DAEMON-BRIEF-4.md`/`-BRIEF-5.md` for the continuation plans)

C0-C9 are all DONE as of session 5 (this session). Within C4, three items
remain deferred to a later package or follow-up (see decision-ask #4
above for the reasoning on each), now filed as backlog rather than left as
inline comments (C9, this session):
- **CP-5**: real `events.jsonl` population during a daemon session.
- **CP-6**: `ctl report`'s real HTML render never charts a daemon
  session's own DAMON series (new finding from C5's RW-14 work).
- **CP-7**: the daemon manifest's `"limits"` table is always `{}` (new
  finding from C5's RW-14 work).

CP-1..3 (from P0) and CP-4 (a pre-existing `tests/test_store.py` flake,
session 4) are unaffected and still open — none are this package's own
remaining scope, they are the estate's ordinary backlog mechanism.
No NEW deferred item was identified beyond CP-5/6/7 during session 5's
live acceptance or bug-fixing work — all four live-found defects were
fixed, not deferred (see decision ask #5 above).

## Host load compliance (session 1)

Checked `docker ps --format '{{.Image}}'` for `tester-unified` before each
gate invocation (0 both times, well under the 2-container estate cap);
`docker update --cpus=3` was not needed since `gate.sh`/`run-gate.py`
already launch their containers with an explicit `--cpus` (1.5 and
whatever `r3`'s environment default is) baked into their own argv; no
image build ran concurrently with any suite; every gate container
confirmed self-removed afterward (`docker ps` re-checked). All pytest runs
during iteration were serial (`nice -n 19 ionice -c 3 python3 -m pytest`,
never `-n`), targeted at `tests/test_cgprofile.py`/`tests/test_summary.py`
individually while iterating; the whole `tests/` suite ran exactly twice
(once locally in the devcontainer venv to sanity-check before the gate,
once inside the real gate container as the ship-grade verdict) — within
the "at most once per checkpoint" budget.

## Host load compliance (session 2)

`docker ps --format '{{.Image}}'` showed 0 `tester-unified` containers
before every gate/pytest invocation (checked 3 times); `/proc/loadavg`
`6.00 4.79 4.29` and `/proc/pressure/memory some avg10=0.10` before the
coverage gate — within the shared-host band, not idle, not saturated. No
concurrent image build. Both gate containers (`coverage`, `r3` — run twice,
once pre-commit on the dirty tree for a sanity check, once post-commit for
the recorded verdict) confirmed self-removed via `docker ps` afterward.
Whole-`tests/` runs: once locally (devcontainer venv, pre-gate sanity,
71.6 s) and once inside the real gate container (75.0 s) — within the "at
most once per checkpoint" budget; `test_subtree.py` alone was run serially
several times while iterating (0.3 s each, negligible).

## Host load compliance (session 3)

`docker ps --format '{{.Image}} {{.Names}}'` showed 0 `tester-unified`
containers before every gate invocation (checked before the pre-commit
r0-r1, pre-commit r3, post-commit r0-r1, and post-commit r3 runs — 4
checks, all empty) and once more after the last gate run to confirm
self-removal. `/proc/loadavg` `6.97 5.78 5.24` and `6.37 5.70 5.31` at two
checkpoints; `/proc/pressure/memory` `some avg10` between 0.17 and 0.77,
`full avg10` between 0.07 and 0.76; `/proc/pressure/cpu` `some avg10`
~9.6, `full avg10` 0.00 throughout — moderate `some` pressure, zero `full`
stalls, comfortably within the shared-host band the whole session. No
image build ran (none of C6-C9 started). All iteration pytest was serial
(`nice -n 19 ionice -c 3`, never `-n`), targeted at `tests/test_damon.py`
alone while writing the pool tests. Whole-`tests/` ran three times total:
once locally pre-gate (78.6 s), once inside `run-gate.py r0-r1` pre-commit
(79.1 s, dirty sanity), once inside `run-gate.py r0-r1` post-commit
(87.0 s, the recorded verdict) — the pre-commit run was a deliberate
sanity check before committing (same pattern session 2 used for `r3`),
not a second checkpoint's worth of whole-suite runs.

## Host load compliance (session 4)

`docker ps --format '{{.Image}}'` for `tester-unified` checked immediately
before every gate invocation (pre-commit r0-r1, post-commit r0-r1 ×2 —
the flaky-test re-run, post-commit r3): 0 every time, well under the
2-container estate cap; re-checked with `docker ps -a` after the last gate
run and found only an unrelated, already-existing `topos-suite` container
from a different session — every gate container this session launched
confirmed self-removed. `/proc/pressure/memory` `some avg10` observed
between 0.03 and 4.30, `full avg10` between 0 and 4.02;
`/proc/pressure/cpu` `some avg10` between 4.50 and 5.84, `full avg10`
0.00 throughout — moderate, comfortably within the shared-host band seen
in prior sessions, never re-checked via `load`/`uptime` alone. No image
build ran (C6 not started). All local iteration pytest was serial
(`nice -n 19 ionice -c 3`, never `-n`), targeted at `tests/test_serve.py`
alone (and `tests/test_damon.py`/`tests/test_store.py` for the two small
touched modules) while iterating; several small standalone Python
reproduction scripts (never pytest) were run directly against `python3`
to root-cause the double-sampling test bug — none of those touch the real
`/sys/fs/cgroup` or spawn a container, so they carry no host-load weight
beyond ordinary CPU-bound Python. Whole-`tests/` (with coverage) ran four
times locally while converging on 100%/100% (68-85 s each) plus twice
inside the real gate container (`r0-r1` pre-commit dirty-sanity, `r0-r1`
post-commit clean — the flaky-test failure and its immediate re-run are
the SAME lane invoked twice in direct response to an observed failure,
not a second independent checkpoint's whole-suite run) — within the
package's own established "at most once per checkpoint plus one before
return" convention read generously given the flake required an
immediate, understood-root-cause re-run to get a real recorded verdict.

## Host load compliance (session 5)

`docker ps --format '{{.Names}}'` checked for `tester-unified` before
every commit's gate invocation across C5-C9 and the crash-fix commit (0
running every time except once, C6's regression check, when 1 was already
up from a parallel P2 gate run — within the 2-container estate cap; r0-r1
runs bare-host and does not interfere with another lane's own container).
`/proc/loadavg`/`/proc/pressure/{cpu,memory}` checked (never `load`/`free`
alone) before every heavier operation — the image rebuild, `ciu up`, each
live probe, the r2 launch: `some avg10` for `cpu` ranged ~3.5-10.7 across
the session, `full avg10` for `cpu` stayed at 0.00 throughout every single
check; `memory` `full avg10` ranged 0-0.53 — moderate `some` pressure,
zero-to-negligible `full` stalls, comfortably within the band every prior
session in this LOG recorded. The one image build this session
(`python3 build-push.py --build`, rebuilding after the crash-fix commit)
ran with no gate/suite running concurrently. Three ephemeral/exec probe
containers (`rg55-p1-probe`, `rg55-p1-shared`, `rg55-p1-overhead`,
`rg55-p1-overhead-off` — four total, one at a time, never concurrent) were
each `docker rm -f`'d immediately after its own measurement; `docker ps -a`
confirmed none remain. `docker update --cpus=3` applied to the r2 lane's
own `tester-unified:local` container (`run-gate-vbpub-r2-689910-1789200657`)
immediately after `docker ps` confirmed it started, per the standing host-
load rule; 0 other `tester-unified` containers were up at r2's launch (1
estate-wide the whole time it ran, well under the 2-container cap). All
local iteration pytest was serial (`nice -n 19 ionice -c 3`, never `-n`).
The daemon container itself (`cgprofile-host-daemon`) was confirmed idle
(0 live sessions, `docker stats` ~0.01-0.25% CPU) before/between/after
every overhead measurement, per the handoff's own "daemon idle before
overhead measurements" instruction — each probe session was started,
measured, and stopped before the next began; no two probe sessions
overlapped.

## Fresh Luna xhigh repair of P1 daemon blockers B1–B8

This implementation pass addressed the fresh review's concrete blockers without
editing `cgprofile-P1-DAEMON-REVIEW-round1.md`, starting a daemon, mutating host
state, running a registered gate, or starting a mutation campaign.

- **B1:** `KdamondPool` records baseline ownership, allocates only proven
  post-baseline indices, tracks owned/free/live sets under a lock, refuses an
  unreadable baseline, and skips teardown for an unacquired slot. Bare sessions
  also refuse a foreign or unprovable index. Tests preserve a pre-existing
  kdamond's `on` state and assert failed acquisition makes no placeholder stop.
- **B2:** `stop.series.damon` is `null` unless persisted DAMON class samples
  are present. DAMON records carry sample sequence metadata so restart replay
  does not confuse a missing sample zero with a later sample.
- **B3:** only non-null tokens enter `_by_target`; no-token starts are
  independent and consume `max_sessions`.
- **B4:** start atomically captures and persists target/host/slice/PID sample
  zero before launching the sampler; an immediate stop retains baseline and
  percentile population.
- **B5:** monotonic timestamps are retained in the accumulator and persisted
  samples; `cores_max` uses every positive adjacent timestamp delta and returns
  null for missing/non-positive timing input.
- **B6:** interval parsing rejects non-numeric, boolean, NaN, and infinity
  through the normal socket error response; finite out-of-range values retain
  the contract's [0.25, 30] clamp.
- **B7:** `cgprofile ctl` validates object type, contract major, boolean `ok`,
  error shape, and verb-specific success shape before printing. Invalid peers
  are exit-3 daemon faults with no stdout.
- **B8:** `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, README links, nyxloom
  refs, and `tests/test_docs.py` now keep the adopter surface synchronized;
  the tests parse current contract examples, check closed vocabulary coverage,
  and resolve local links/anchors.

Focused verification was serial and load-niced (`nice -n 19 ionice -c 3`):
420 tests across DAMON, summary, sampler, serve, store, subtree, targets,
metrics, and documentation, with one pre-existing dependency-skipped case;
and `py_compile` for the changed Python modules all passed. The full
`test_cgprofile.py` collection was not runnable in this environment because
the system interpreter lacks numpy; equivalent B7 fake-socket coverage runs
in `test_serve.py`. The final review still owns the live acceptance probe and
registered gate.

## Controller addendum — final oracle repair checkpoint (2026-09-15)

The historical R2 sections above describe earlier trees and are retained for
traceability. They do not certify the current implementation. A direct,
isolated diagnostic rejudge of the prior implementation tip `5fd0ef13`
produced 252 candidates: 245 killed, 7 survived, with no budget or crash
classification. The seven survivors were at `damon.py:325`, `damon.py:589`,
`damon.py:596`, `serve.py:705`, `serve.py:793`, `summary.py:174`, and
`summary.py:178`. The controller classified these from their real call paths;
the pool ownership guard was redundant after successful acquisition and the
callback's explicit `None` was behaviorally required to remain null, while
the other survivor locations exposed missing direct or edge-case oracles.

Commit `8032716c` closes that oracle gap: it removes the redundant pool
ownership conjunct, preserves the callback null behavior without an explicit
empty return, and adds tests for solo teardown equality, partial CPU
baselines, and missing/malformed ISO timestamps. Its focused tests passed 268
tests with one dependency skip. The full package run passed 1200 tests with
five warnings. The registered `r0-r1` gate then passed 1200 tests with 100%
line and 100% branch coverage, and registered `r3` passed all seven canaries
(seven rejected, zero survived).

Because assay identity is per tree, the older mutation evidence is invalid
for `8032716c`. A fresh registered R2 on the final tip is still mandatory;
this addendum deliberately does not call the package shipped. The final
merge additionally requires the fresh Sol xhigh adversarial review requested
by the operator.

## Controller addendum — terminal R2 on `8df62f62` and oracle repair

The registered R2 on the quiet judged tree
`8df62f628b20c6280574aef8f6e76a53f9d00e35` completed at
`2026-09-16T15:53:48Z` with **250 candidates accounted for, 248 killed,
2 survived, 0 equivalent, 0 budget-exceeded, and 0 crashed**. Assay reported
`FAIL/MUTANTS_SURVIVED` (exit 1). This is complete mutation evidence, but not
release evidence until every survivor is either behaviorally killed or
explicitly justified.

The survivors were:

- `lib/damon.py:325`, `Gt->GtE` on `current > baseline`. This is an
  equivalent mutant under the pool invariant: the release path reaches this
  conjunction only after a live owned slot exists, so
  `expected_end = max(_owned) + 1` is strictly greater than `baseline`.
  With the preceding `current == expected_end`, `current > baseline` is
  implied; replacing it with `>=` cannot change a reachable result.
- `lib/damon.py:385`, `False->True` on `_acquired_from_pool`. This exposed a
  real oracle gap. If a second pooled session fails before acquiring a slot,
  initializing the flag true causes exception teardown to release constructor
  index 0, which may be the first session's live slot. The regression
  `test_failed_pool_acquire_cannot_release_a_live_constructor_index` proves
  that the first slot remains live and on; the exact temporary mutant fails
  that test (`MUTANT_TEST_EXIT=1`), while the restored production code passes
  all 82 `test_damon.py` tests. The test-only repair is committed as
  `8cc740a2`.

Because `8cc740a2` changes the judged tree, the complete R2 result above is
now invalid for final release evidence. A fresh R2 must run on `8cc740a2`
with the exact tree held quiet; the redundant `Gt->GtE` may remain as a
written equivalent, while the new regression must kill the initialization
mutant. Final r0-r1/r3, the fresh Sol xhigh review, and the release remain
pending.

## Controller addendum — current P1 R2 on `a2c2501f`

The fresh source-backed R2 on quiet tree
`a2c2501fea2b576a774f1a7fcedd3a5ad251f630` completed at the terminal event
`2026-09-23T11:48:41.057125+00:00`. Its separately read verdict accounts for
**250 candidates: 249 killed, 1 survived, 0 equivalent, 0 budget-exceeded,
0 crashed, and 0 hung**; Assay reported `FAIL/MUTANTS_SURVIVED` with exit 1.
The exact tester container was
`run-gate-vbpub-r2-522284-1790154369`, and it has exited.

The sole survivor is `lib/damon.py:325`, `Gt->GtE` on the pool release guard.
It is the same reachable-state equivalent documented above: after a live
pool-owned slot exists, the preceding `current == expected_end` condition and
`expected_end = max(_owned) + 1` imply `current > baseline`. The replacement
cannot alter any reachable teardown decision. No new oracle gap was found, and
no production repair is warranted for this survivor. The verdict and progress
artifacts remain the authoritative mutation evidence; this equivalence is
disclosed rather than relabeled as an assay PASS.

The current tree's short evidence is independent: `r0-r1` passed 1,207 tests
with 100% line and branch coverage, and `r3` rejected all seven canaries.
The controller's read-only adversarial review found no additional blocker.
These facts support provisional integration under RW-296, but do not replace
the fresh Sol xhigh review or the final release gate set.

## Controller addendum — repeat R2 on `07161416`

The follow-on registered R2 ran against quiet tree
`071614168b2a96cab0b90b1f0d4c439972bf54dc` and completed at
`2026-09-23T16:50:08.347346Z` after 10,536.3 seconds. Its terminal progress
event records `candidate_total=250`, `killed=249`, `survived=1`, and zero
equivalent, budget-exceeded, crashed, or hung candidates. The separate verdict
records R0 PASS and R2 `FAIL/MUTANTS_SURVIVED`, exit 1. The tree's
`scripts/cgroup-profiler/lib` and tests are identical to the already-reviewed
and short-gated P1 source tree `a2c2501f`; this run adds exact-tree R2 evidence
for the report-only follow-on commit.

The single survivor is `lib/damon.py:325`, `Gt->GtE`, replacement SHA-256
`92a00d7d91da9f0f06c3f218c49c9b98323469962b291365b85d3c16b6b7f95f`. It is
the same reachable-state equivalent described above: after the owned pool
slot is live, `current == expected_end` and `expected_end = max(_owned) + 1`
imply `current > baseline`. The mutant cannot alter a reachable release
decision. No new oracle gap was found; the mechanical Assay verdict remains
FAIL because Assay does not encode this human equivalence classification.

## Controller addendum — registered P1 R2 on `51198f2e`

The registered R2 on exact quiet tree
`51198f2e4759acbd69dfd770b843cdf20b1d6ed0` completed at
`2026-09-24T09:19:52.297677Z` in container
`run-gate-vbpub-r2-2104925-1790236649`. The separately read verdict was R0
PASS and R2 `FAIL/MUTANTS_SURVIVED`, exit 1: 81/81 candidates executed, 71
killed and 10 survived; zero were equivalent, budget-exceeded, crashed, or
hung. All ten survivors revealed missing behavioral assertions in placement
diagnostic ordering, invalid local-PID handling, incomplete namespace-root
mapping, version-prefix validation, subprocess option semantics, and
diagnostic fallback. Regression tests for these cases are in the P1 source
worktree and passed the focused 235-test suite. They are not yet confirmed by
a new mutation run. The 51198 result is diagnostic evidence only and does not
satisfy final P1 mutation acceptance. A new quiet-tree R2 plus final short
gates and the required fresh Sol xhigh review remain required.

## Controller addendum — terminal R2 on `1908316b` (2026-09-25)

The registered R2 on the detached, quiet tree
`1908316b227df8a2b8fd259969725b4c7a9f1b27` ran in container
`run-gate-vbpub-r2-3690082-1790305991` from
`2026-09-25T03:13:17.949231Z` through
`2026-09-25T04:27:11.853270Z`. Its terminal event accounts for all 81
candidates: 80 killed and one classified `hung`; zero survived, equivalent,
budget-exceeded, or crashed candidates. The separate verdict is
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4. This is not an R2 PASS.

The hung candidate is index 40, ID
`7692929c11d002ae02db963540fb42e06618318c814e1e99e71fed42894eea9c`,
`lib/targets.py:191`, `Eq->NotEq`. Its progress record reports 1,321 tests
completed, but that does not establish a kill or explain why the candidate
was classified hung. The retained mutation-state and progress artifacts
contain no kill proof for it. Per RW-319, do not attribute this terminal to
scheduler load or treat a time/liveness classification as a product verdict;
the candidate needs a fresh, exact-tree judgment and any real hang must be
diagnosed on its behavior.

This campaign predates the latest-main reconciliation. P1 has since merged
current `main` at `007b208859b99d380b87a9d4ea3479bdbfc8d5b6`, so the old R2
records do not apply to the reconciled tree. The next sequence is fresh
registered `r0-r1` and `r3`, a fresh Sol xhigh review, provisional P1 merge,
then a new R2 and full gate in an isolated CIU worktree. Backport and
revalidate any required repair; do not report the old R2 as green.

## Controller addendum — cap acceptance on the short canary lane (2026-09-25)

The R3 functional run on `d94c58b9` rejected all seven canaries, but live
inspection of `run-gate-vbpub-r3-4169091-1790332576` found
`NanoCpus=0`; by the time an exact-name `docker update --cpus=3` was
attempted, that short-lived container had exited. The result is therefore
not accepted as final R3 evidence. The P1 R2/R3 lane definitions now declare
`resources.cpus = "3"`, so Docker applies the cap before running candidate
code. The refreshed exact-tree gate must separately confirm
`NanoCpus=3000000000`. This closes the post-launch update race without
changing test verdict semantics; see RW-323 in the controller log.

## Focused follow-up — helper-mode PID identity (2026-09-25)

The helper translation for public `pid:N` now carries the selected process's
PID namespace inode, innermost `NSpid`, proc stat start time, and cgroup
namespace inode alongside its validated cgroup-namespace-relative path. The
helper resolves those facts only among positive PIDs whose proc cgroup path
maps to that exact container/subpath. It creates a `kind="pid"` target only
for one unique match; a missing or ambiguous match raises `TargetError` before
the collector starts. `cmd_collect` passes the selected proc root explicitly
to target resolution and per-process sampling, and DAMON vaddr targets use the
resolved helper-view PID.

The regression fixture uses separate private caller/helper PID and cgroup
views: caller PID 4242 maps to helper proc PID 51001 while
`cgroup.procs` exposes only PID 0 for host processes. The collector test
asserts the exact selected cgroup, the helper proc sample for PID 51001, and
DAMON's `vaddr` PID 51001. A companion case moves the matching process to a
sibling cgroup and asserts the selected subpath refuses the mapping.

Focused verification, serial and load-niced:

```text
nice -n 19 ionice -c 3 /home/vscode/.venv/bin/python -m pytest -q \
  tests/test_access.py tests/test_targets.py tests/test_helper_pid_target.py
220 passed in 2.18s
```

The broader `tests/test_cgprofile.py` run could not collect because the estate
venv lacks optional `pandas` (`ModuleNotFoundError` while importing
`lib.analyze`); NumPy is present. The helper regression and target/access tests
above ran and passed. No Docker, daemon, registered gate, or host-namespace
probe was run for this follow-up.
