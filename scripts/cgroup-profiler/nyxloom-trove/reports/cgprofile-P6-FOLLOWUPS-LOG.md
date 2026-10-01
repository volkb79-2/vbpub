# cgprofile-P6-FOLLOWUPS — implementer LOG

Package: cgprofile-P6-FOLLOWUPS (RG-55 wave, package P6). Worktree
`.worktrees/rg55-followups-cgprofile`, branch `rg55-followups-cgprofile`,
base `16f3a29f` (P1 tip at dispatch, RW-35). This LOG covers implementer
session 1 (fresh Sonnet, checkpoint clause HARD).

Tool-call counter: updated at each entry below (self-counted from the
session transcript, includes reads/greps/edits/bash).

## Orientation (session 1)

Read in full or via bounded grep, in the handoff's order: the handoff
itself; host PSI + `docker ps` (confirmed both mutation runs live —
`run-gate-vbpub-r2-2315801-…` container, `assay-r2` pid 2415767 — targeted
serial pytest only for this whole session, no run-gate lane, no image
build); controller log RW-30, RW-31, RW-32 (skimmed, P8's), RW-33
(skimmed, P7's), RW-34, RW-35 in full (`run-gate-WAVE-RG55-CONTROLLER-LOG.md`
lines 327-480); contract `RG55-INTERFACE-CONTRACT.md` §8 in full from
`main` (lines 398-560 — the v1.1 spec: §8.1 carriers, §8.2 watch, §8.3
placement, §8.4 liveness, §8.5 gates_slice, §8.6 transports, §8.7 summary
additions, §8.8 error codes, §8.9 consumer obligations); backlog row CP-4
in full; `lib/store.py` `new_run_id` + `RunDir` header docstring;
project-wide grep for `[0-9a-f]{4}` (two hits only: `lib/serve.py`'s
`_SESSION_ID_RE`, session ids — untouched per the handoff; `tests/test_store.py`'s
`RUN_ID_RE` — updated); grep for `run-YYYYmmdd`/`new_run_id`/`xxxx` mentions
across the project to find every doc consumer of the run-id format
(`DESIGN.md` line 283, `ATTACH-GUIDE.md` line 129 — both updated;
`cgprofile.py` line 519 calls `new_run_id()` with no hardcoded width,
untouched).

**Read-list items NOT read this session** (deferred to whoever picks up
C2 onward, flagged honestly per the handoff's §5 instruction): design doc
`DESIGN-2026-09-12-liveness-placement-admission.md` (§2 D-17..D-26, §3, §4,
A1 D-27..D-29, A2 D-30) — only reached second-hand via RW-30/RW-31/RW-34's
summaries in the controller log, not read directly; backlog rows CP-2,
CP-5, CP-6, CP-7, `INDEX.md`; P1's `-REPORT.md`/`-LOG.md` (only skimmed
the dispatch table row); `README.md` "Running the daemon"; `docs/ATTACH-GUIDE.md`
beyond the one grep hit; `lib/serve.py`, `lib/damon.py`, `lib/subtree.py`,
`lib/summary.py` (beyond the one grep) — not read at all this session.
C2 (CP-5) needs `lib/events.py` (module docstring only seen) and the
sampler cadence in `lib/serve.py` read in full before editing.

Orientation call count: ~23 tool calls (handoff + host checks + rulings +
contract §8 + backlog CP-4 + store.py + format-consumer greps) before the
first edit.

## C1 — CP-4 (run-id flake), commit TBD at write time

Widened `lib.store.new_run_id`'s random suffix from `os.urandom(2)` (4 hex
chars, 65536-value space) to `os.urandom(4)` (8 hex chars,
4294967296-value space). Docstring states the birthday-paradox arithmetic
both before (≈1.8% collision chance across 50 draws — the CP-4 backlog
row's own math, which is how it was observed to flake once in the wild)
and after (`50*49/(2*16**8) ≈ 2.85e-7`, under the handoff's `< 1e-6`
requirement) and says explicitly that session ids (`s-<stamp>-<4 hex>`,
`lib/serve.py`) are a separate format, untouched.

Updated every consumer of the format found by grep: `tests/test_store.py`
`RUN_ID_RE` (4 -> 8 hex in the regex) and
`test_new_run_id_is_unique_even_for_the_same_instant`'s docstring (states
the same before/after math, per the handoff's "deterministic about what
it asserts" instruction — the test body itself is unchanged: 50 draws,
exact-uniqueness assertion, now backed by a collision probability low
enough that a flake is not a realistically expected outcome); `DESIGN.md`
line 283 and `ATTACH-GUIDE.md` line 129 (`xxxx` -> `xxxxxxxx` placeholder
in the run-id shape shown to readers). Confirmed no other `os.urandom(2)`
call exists in the project and no other file matches the id-format grep.

Ran `tests/test_store.py` (30 passed) and `tests/test_serve.py` (92
passed, session-id path untouched, sanity check) serially under
`nice -n 19 ionice -c 3`. Full-suite / coverage / mutation gates deferred
to the final gate pass (HOST LOAD: targeted files only while the two
mutation runs are live).

Decision asks: none — CP-4's own backlog row already named both candidate
directions (widen vs. relax-the-assertion) and the handoff picked "widen"
explicitly, so no ambiguity to log.

Committed `376bb9cb`. Tool-call counter at commit: ~27.

## C2 — CP-5 (events.jsonl real rows), commit TBD at write time

Read `lib/events.py`'s `Detector` in full (`observe`/`_observe_cgroup`,
`_counter_event`/`_threshold_event` edge-triggering + hysteresis,
`topology`, `limits_changed`) and `cgprofile.py`'s `cmd_collect` (the
collector's own wiring: `Detector(DetectorConfig(), limit_map, roles)`
built once from `{t.cgroup: limits_mod.effective(...)}`, then
`detector.observe(prev, record, dt)` called from `on_sample` with
`dt = record["mono"] - prev_mono`, appended to `run.append("events", ...)`;
`detector.topology(...)` on membership changes). Read `lib/serve.py`'s
`_Session` dataclass, `_create_session_locked` (the `events.jsonl` touch
site, mislabeled "CP-4" in a comment — actually CP-5, per the backlog row;
fixed), `_on_session_sample` in full (the discovery-cadence `due` split
between the token/no-token pid-resolution paths, the `with sess.lock:`
block computing `live_cpu_cores_recent` from `_prev_cpu_usage_usec`/
`_prev_mono`). Confirmed `lib/summary.py`'s `SummaryAccumulator`/
`_events_block` never references `events.jsonl` or `Detector` at all — its
`events` counters are computed purely from raw cgroup metric deltas across
`add_sample` calls, independent of this deliverable, per the backlog row's
own claim.

Wiring: `_Session` gains `detector: Optional[events_mod.Detector]`,
`_prev_record: Optional[Dict]` (the previous tick's full sample record,
the same `{"cg": {cgroup: metrics}, "t":…, "mono":…}` shape `Detector.observe`
expects, since `sample_fn` in `_session_loop` already builds records that
shape for `lib.analyze.to_frame`/RW-14 — reused directly, no new shape
introduced), `last_effective_limits: Optional[limits_mod.Effective]`.
`_create_session_locked` resolves `limits_mod.effective(cgroup, root, flags)`
once at session start and builds `Detector(DetectorConfig(), {cgroup: initial},
roles={cgroup: "subject"})` — same call shape as `cmd_collect`, one target.
`_on_session_sample` gains two call sites: (1) inside the existing `due`
block (same discovery cadence as pid resolution — a walk of the ancestor
limits chain every tick at a 0.25s interval floor would be wasteful, and
nothing in the contract requires sub-discovery-cadence limit_drift latency),
re-resolve effective limits and call `detector.limits_changed(cgroup, old,
new, t, mono)`, appending any returned events; (2) inside the `with
sess.lock:` block, call `detector.observe(sess._prev_record, record,
dt_since_prev)` when a previous record exists, appending returned events
OUTSIDE the lock (file I/O, matching the existing pattern for `samples`/
`host`/`damon` appends which also happen post-lock).

**Real bug caught by the CP-5 oracle test itself, before it went green**:
first draft gated the event-detection `dt` on the SAME condition as the
CPU-rate calc (`sess._prev_cpu_usage_usec is not None`) — wrong, because
`usage_usec` can legitimately be absent on a tick while there is still a
valid previous record to diff against; this silently skipped `observe()`
on every such tick. Split into a `dt_since_prev` (gated only on
`sess._prev_mono is not None`, mirroring `cmd_collect`'s own unconditional
`dt = record["mono"] - prev_mono`) used for event detection, keeping the
narrower `_prev_cpu_usage_usec`-gated check for the CPU-rate metric only.
Caught by `tests/test_serve.py::test_on_session_sample_appends_real_events_jsonl_rows`
failing (`memory_high_breach` missing from the second tick's events) on
first run — see that test's own docstring, written to be red against
exactly the "wired in but never actually appends" failure mode the CP-5
backlog row describes.

New test: `test_on_session_sample_appends_real_events_jsonl_rows` — CP-5's
own oracle (a `memory.high` change mid-run must produce a `limit_drift`
row, not just the Summary's counter) plus a plain `memory_high_breach`
`observe()`-path event between the same two ticks, proving both Detector
entry points are wired. Fixture: `simple_server`'s existing `simple_root`
fixture, `write_cgroup`/`cgroup_files` (already imported) to mutate
`memory.high` on disk between two `_on_session_sample` calls 3.0 mono
seconds apart (>= `DISCOVERY_INTERVAL_SECONDS`=2.0, so both ticks are
discovery-due).

Byte-identical goldens proof (handoff requirement): rather than writing a
new bespoke golden-diff test, relied on the EXISTING
`TestFullLifecycleGoldenReproduction` class in `tests/test_serve.py` (drives
a real session through `_on_session_sample` against the frozen contract
fixtures and asserts the resulting summary matches `summary-v1.json`/
`summary-container-v1.json` byte-for-byte) — it still passes unchanged
after this C2 diff, which is exactly the proof the handoff asks for: the
Summary's own `events` counters are wired through `summary_acc.add_sample`,
untouched by anything in this commit.

Ran serially under `nice -n 19 ionice -c 3`: `tests/test_serve.py` (93
passed, was 92 — the +1 is the new test), `tests/test_events.py`,
`tests/test_summary.py`, `tests/test_limits.py` (87 passed, no
regressions). Full-suite / coverage / mutation gates deferred to the final
gate pass per the handoff (targeted files only while the two mutation runs
are live — reconfirmed live at this checkpoint: `run-gate-vbpub-r2-…`
container present, `assay-r2` pid 2415767 present, PSI memory `full avg10`
1.31, well under the 5 back-off threshold).

Decision asks: (1) limit-drift polling cadence — the contract doesn't
specify one; chose the existing discovery cadence (`DISCOVERY_INTERVAL_SECONDS`,
2.0s) over per-tick, matching the daemon's own established reasoning
(RW-15's comment on why pid re-discovery isn't per-tick either) rather than
inventing a third cadence. (2) `roles` passed to `Detector` as
`{cgroup: "subject"}` explicitly rather than omitted — `_role()` already
defaults to `"subject"` for an unknown key, so behaviourally identical
either way; explicit for readability, matching `cmd_collect`'s own
explicit-roles style. Neither blocked the package (RW-9) — logged per the
BLOCKED protocol's "write the ask into the LOG... continue" instruction.

Tool-call counter at C2 close: ~65 (past the ~60 ARM threshold — this is
the coherent boundary the checkpoint clause cuts at: green gate on the
targeted files, LOG written, about to commit).

## Session 2 (fresh successor) — orientation, tool-call counter reset to ~1

Read in full, in the order the BRIEF-2/HANDOFF specified: BRIEF-2 (this
session's continuation point); HANDOFF (all sections, deliverables C3-C9);
design doc `DESIGN-2026-09-12-liveness-placement-admission.md` in FULL
(§1-§7, A1 D-27..D-29, A2 D-30 — this session's own first direct read, per
BRIEF-2's explicit flag that session 1 only saw it second-hand); contract
`RG55-INTERFACE-CONTRACT.md` on `main` in full (§1-§9, confirmed §8 v1.1
amendment matches BRIEF-2's summary — no drift). HOST LOAD rechecked at
session start: memory PSI `full avg10=0.04` (well under 5); BOTH named
mutation runs still live (`run-gate-vbpub-r2-2315801-1789214565` container
`Up 3 hours`; `assay-r2` pid 2415767 confirmed alive via `kill -0`) — per
the handoff's binding HOST LOAD instruction, staying in targeted-pytest-
only mode, no image build, no run-gate lane, for this entire session.

## C3 — CP-7 (manifest `limits` table), commit TBD at write time

Read the CP-7 backlog row, `lib/serve.py`'s `_manifest_for`/
`_create_session_locked` (confirmed BRIEF-2's steer: `initial_effective_limits`
computed once at session start via `limits_mod.effective()` and already
stored on `sess.last_effective_limits` for CP-5's drift detection — no
second `effective()` call needed), `cgprofile.py`'s `_limits_snapshot`
(module-level function, shape `{"resolved": {...}, "described": [...],
"fingerprint": "..."}`, the exact schema `lib/analyze.py`'s
`_effective_limits`/proposal checks already expect per that module's own
`manifest["limits"]` docstring comment) and `cmd_collect`'s own
`"limits": {cg: _limits_snapshot(limits_mod, eff) for cg, eff in
limit_map.items()}` line — the shape to reproduce verbatim, keyed by the
session's one cgroup.

**Change:** `_manifest_for` now writes `"limits": {sess.cgroup:
cg_module._limits_snapshot(limits_mod, sess.last_effective_limits)} if
sess.last_effective_limits is not None else {}` instead of the
unconditional `{}` RW-14/P1 deliberately left as a scoping decision (now
formalized as CP-7). `cgprofile.py` is imported lazily INSIDE
`_manifest_for` (`import cgprofile as cg_module`) rather than at module
level, mirroring `cgprofile.py`'s own `cmd_serve`'s lazy `from lib import
serve as serve_mod` — confirmed no existing `lib/*.py` module imports
`cgprofile.py` at module level (grep), and `cgprofile.py`'s top level never
imports `lib.serve`, so this stays a one-directional, call-time-only
dependency; `tests/test_serve.py`/`tests/test_cgprofile.py` already both
`import cgprofile as cg` at module level today, so re-importing it lazily
from inside `lib.serve` during a test run is exactly the already-proven-safe
pattern, not a new risk.

**Structural finding, documented inline in `_manifest_for`'s own comment
and in the new test's docstring:** a daemon session profiles exactly ONE
cgroup, so `manifest["limits"]` always has exactly one entry.
`_check_oversubscription` (`lib/analyze.py`) needs >= 2 sibling entries
under a shared parent to produce anything — it is a **permanent no-op for
every daemon session**, this fix included, a fact the CP-7 backlog row's
own oracle sketch ("assert... includes the `oversubscribed:` finding")
did not account for. `_check_recursiveprot_gap` needs only ONE cgroup
entry and is the real, honest oracle this fix unlocks — used instead,
with a documented reason, per the handoff's "claim only what you ran"
discipline (gaming a synthetic 2-entry fixture that no real daemon session
could ever produce was rejected as dishonest evidence).

**Test evidence** (`tests/test_serve.py`, new `TestManifestLimitsTable`
class, 3 tests):
1. `test_manifest_limits_keyed_by_cgroup_matches_limits_snapshot_shape` —
   independently recomputes the expected `_limits_snapshot` value (not
   read back off `sess`, so a mutant returning a stale/cached value would
   still be caught) and asserts byte-for-byte manifest equality; also
   asserts `protection_mode == "strict"` (this suite's `simple_proc`
   fixture has no `/proc/mounts`, so `mount_flags()` returns `set()` —
   matches this estate's real documented default, `soulmask-memory-
   pressure-findings`).
2. `test_manifest_limits_is_empty_dict_when_no_effective_limits_were_resolved`
   — `sess.last_effective_limits = None` (the crash-finalize path,
   RW-14, could in principle call `_manifest_for` before that field is
   set) degrades to `{}`, never raises.
3. `test_daemon_session_manifest_unlocks_recursiveprot_gap_proposal`
   (skipped if report deps absent, matching the existing `_HAS_REPORT_DEPS`
   convention) — builds a fake tree where `dev-background.slice` declares
   `memory_min=100MiB` and the leaf scope declares the default `memory_min=0`;
   runs a real session end to end through `lib.analyze.build(sess.rundir)`;
   asserts `recursiveprot-gap:<cgroup>` is present and `oversubscribed:*`
   is absent (proving the structural-impossibility claim above, not just
   asserting it in a comment).

**Mutation check (manual, 3 planted mutants, targeted file only — the r2
lane stays off-limits this session):**
- M1 — revert `"limits"` to unconditional `{}` (the exact pre-fix bug):
  caught by tests 1 and 3 (`AssertionError`, missing `recursiveprot-gap:*`
  proposal / wrong shape).
- M2 — key the dict by `sess.slice_cgroup` instead of `sess.cgroup` (wrong
  cgroup identity, still non-empty): caught by tests 1 and 3 (same failure
  shape — the recursiveprot check reads `analysis.limits[sess.cgroup]`
  specifically, so a wrongly-keyed entry is invisible to it).
- M3 — invert the None-guard (`is not None` -> `is None`, so the branch
  fires backwards): caught by all 3 tests (the always-limits-table path
  degrades to `{}` when limits ARE available, and vice versa).
All three mutants applied via a Python patch/restore against a file
backup (not `git checkout`, which — logged as a real mistake below —
would discard uncommitted work, not just the planted mutant) and reverted
before the next step each time; `diff` against the pre-mutation backup
confirmed byte-identical restoration after the last mutant.

**Self-caught mistake (logged per "claim only what you ran," including
mistakes):** the FIRST mutation round (M1) was reverted with `git checkout
-- lib/serve.py`, which — since C3's edits were not yet committed —
discarded the entire C3 diff, not just the planted mutant. Caught
immediately by re-running the targeted tests (`grep '"limits":'
lib/serve.py` showed the pre-fix `{}` still present, and `_manifest_for`
had no `cg_module` import). Re-applied both edits from scratch, re-ran the
full `TestManifestLimitsTable` class green, then switched the remaining
two mutation rounds (M2, M3) to a `cp`-backup/restore discipline instead
of `git checkout`, confirmed by `diff` after the last restore.

Ran serially under `nice -n 19 ionice -c 3`: `tests/test_serve.py` — 96
passed (93 + 3 new). `tests/test_analyze.py` + `tests/test_limits.py` —
154 passed, no regressions. Full run: `tests/test_serve.py` +
`tests/test_analyze.py` + `tests/test_limits.py` together — 250 passed.

Decision asks: none — CP-7's own "Proposed contract... not designed here"
sketch and the handoff's C3 description agreed exactly on reusing
`sess.last_effective_limits` and `_limits_snapshot`'s shape; no BLOCKED
protocol needed.

Tool-call counter at C3 close: ~46 (session-2 count, reset from session 1;
includes the orientation reads, the M1 git-checkout mistake and its
recovery). Continuing to C4.

## C4 — CP-6 (DAMON series in report), commit TBD at write time

Read the CP-6 backlog row, `lib/analyze.py`'s `to_frame`/`build_series`/
`build`/`_CHART_META` registry, `lib/model.py`'s `Series`/`Analysis`
(`groups()`, `series_in()`, `PANELS`), `lib/report_html.py`'s
`build_figure`/`GROUP_LABELS`/`UNIT_LABELS`, `lib/serve.py`'s
`_on_session_sample` (`damon_bytes = sess.damon_session.last_class_bytes`,
`rundir.append("damon", damon_bytes)`), `lib/damon.py`'s `last_class_bytes`
property, and `tests/fixtures/contract/frames/*/damon.json` +
`frames.json` (the golden fixture set `TestFullLifecycleGoldenReproduction`
already drives a real session through).

**Two findings that changed the plan from the backlog's own "this is real
work, not a small patch" framing:**
1. `lib/model.py`'s `PANELS` tuple already ends with `"damon"`, and
   `lib/report_html.py`'s `GROUP_LABELS`/`UNIT_LABELS` already have a
   `"damon": "DAMON"` entry — `build_figure` is fully generic over
   `analysis.groups()`/`series_in(group)`, so the entire subplot/legend/
   colour/resampling/hover pipeline already handles a "damon" panel with
   ZERO changes needed to `report_html.py`. The backlog's sketch ("panel-
   group machinery... would then need to know about a 'damon' panel
   group") was written before noticing this was already wired, just
   never fed. This significantly narrowed C4's real surface to
   `lib/analyze.py` alone.
2. `damon.jsonl` rows carry NO timestamp of their own (confirmed against
   the fixture: `{"hot": ..., "warm": ..., "cold": ..., "idle": ...}`,
   no `t`/`mono`) — but `lib.damon.DamonSession.last_class_bytes` never
   returns `None` (only zeros before the first real aggregation window),
   and `_on_session_sample` appends one `damon.jsonl` row in the SAME
   call as the matching `samples.jsonl` row whenever DAMON is on at all —
   so row *i* of `damon.jsonl` is tick *i* of the session's own order by
   construction, with no gaps. This makes positional pairing against the
   raw (native-resolution) sample index honest, not a hack.

**Change:** `lib/analyze.py` gains `_DAMON_CLASSES` and `_damon_series(run,
index, target)` — reads `damon.jsonl` via the existing `_read_safe`
helper, returns `[]` on an absent file or no subject target (no error,
per the backlog's own "absent file -> no figure, no error"), otherwise one
`Series` per class (`key="damon.<class>_bytes"`, `group="damon"`,
`unit="bytes"`) paired against `df.index` (truncated to `min(len(rows),
len(index))` from the front — documented as the honest degrade if the two
streams were ever to fall out of lockstep). `build()` now computes
`damon_subject` (the first non-observer target's cgroup, since DAMON
classifies the profiled SUBJECT, never an observer) and folds
`_damon_series(...)` into `series` before the existing by-cgroup relabel
loop — reusing that loop's own relabeling/`summarise_descendants`
machinery unchanged rather than adding a parallel path.

**Oracle (CP-6's own, verified literally, not just "data was read"):**
`tests/fixtures/contract/frames/{0..4}/damon.json` hot-class bytes are
`[157286400, 188743680, 230686720, 199229440, 178257920]`. New test class
`TestDamonSeriesInReport` in `tests/test_serve.py` (3 tests, guarded by
the existing `_HAS_REPORT_DEPS` skip convention):
1. `test_analysis_series_carries_the_exact_frame_hot_bytes` — drives
   `_run_full_lifecycle` (DAMON on, all 5 frames), re-opens the session
   directory as a fresh read-only `store.RunDir`, asserts
   `analysis.series_in("damon")` has exactly the 4 expected keys and
   `damon.hot_bytes.v == [157286400, ...]` (the exact fixture values, not
   a shape check).
2. `test_absent_damon_jsonl_yields_no_damon_series_and_no_error` —
   `damon="off"` session (never touches `damon.jsonl` at all, per
   `_on_session_sample`'s own `if damon_bytes is not None` guard) ->
   `analyze.build()` does not raise, `series_in("damon") == []`,
   `"damon" not in analysis.groups()`.
3. `test_rendered_html_figure_json_carries_the_hot_trace_values` — the
   real oracle: `report_html.render()` to a real HTML file, asserts
   `"cgp-figure"`/`"Plotly.newPlot"` (matching `TestHandleReportRealRender`'s
   own "real render, not a stub" convention), `"DAMON hot bytes"` (the
   relabeled trace name) and every one of the 5 exact hot-byte integers
   present in the embedded plotly JSON.

**Mutation check (manual, 3 planted mutants, `cp`-backup/restore
discipline — NOT `git checkout`, per the C3 self-caught mistake logged
above):**
- M1 — `_damon_series` returns `[]` unconditionally (first line):
  caught by tests 1 and 3.
- M2 — invert the subject-target filter (`!= "observer"` ->
  `== "observer"`), so `damon_subject` resolves to `None` for a
  daemon session's single subject target: caught by tests 1 and 3
  (same failure shape — no damon series at all).
- M3 — drop the `_bytes` suffix from the Series key
  (`f"damon.{cls}_bytes"` -> `f"damon.{cls}"`): caught by test 1 (the
  exact-key-set assertion).
`diff` against the pre-mutation backup confirmed byte-identical
restoration after the last mutant.

Ran serially under `nice -n 19 ionice -c 3` (PSI checked before/after:
`full avg10` ranged 0.12-7.21 over this deliverable, briefly crossing the
5% back-off threshold once mid-deliverable — no test was mid-flight at
that moment, so no action was needed beyond noting it and rechecking
before the next run, which is what happened): `tests/test_serve.py` +
`tests/test_analyze.py` + `tests/test_report_html.py` + `tests/test_model.py`
together — 306 passed, no regressions.

Decision asks: none — the backlog's "not designed here" sketch and the
handoff's C4 description both pointed at the same shape (`_CHART_META`-
style registry extension, one figure per DAMON class); the two findings
above narrowed HOW to build it, not WHAT to build.

Tool-call counter at C4 close: ~72 (session-2 running total). Approaching
the ~90 hard ceiling with C5-C9 still open — cutting after this commit per
the checkpoint clause's "green gate > commit > LOG/REPORT write" ordering;
BRIEF-3 follows.

## Session 3 (fresh Sonnet, checkpoint clause HARD)

Tool-call counter reset for this session (self-counted, includes
reads/greps/edits/bash).

### Orientation (session 3)

Read in full, in the order the dispatch prompt specified: BRIEF-3 (the
worktree state, session-2 findings, what remains, exact next command);
the HANDOFF on `main` in full; contract §8 in full on `main` (§8.1-§8.9,
confirmed byte-identical to what session 1's LOG already quoted); design
doc A1 (D-27..D-29) and A2 (D-30) directly on `main`; controller log
RW-30, RW-31, RW-32 (skimmed, P8's), RW-33 (skimmed, P7's), RW-34, RW-35,
RW-36 (skimmed, P7's), RW-37 (P8 round 2 — the one fact flagged for P6:
the host tmpfiles entry is `mdt-cgprofile.conf`, mdt's, not this
package's to write — noted for C6, not relevant to C5). HOST LOAD
recheck (worktree exists, tip `36859c77`, PSI `full avg10` 0.06 at
session start, `docker ps` — `run-gate-vbpub-r2-2315801-…` still showed
`Up 4 hours` at the FIRST check but later `docker ps -a` during the C5
close-out check showed it `Exited (1) 4 minutes ago`; `assay-r2` pid
2415767 alive via `kill -0` throughout, confirmed a SEPARATE package's r2
lane via `pgrep -af` — not P1's or P2's own — so the "while EITHER is
alive" rule stayed binding the whole session regardless of the container
exiting mid-session).

`ciu.compose.yml.j2` read in full (the C5-relevant target session 2
flagged as unread): found the daemon service's `cgroup_parent:
"{{ env.CGROUP_PARENT_DEV_INTERACTIVE }}"` plus its comment explaining
the OLD (pre-A1) reasoning — this is exactly what D-29/RW-30 supersedes.
`ciu.defaults.toml.j2`/`ciu.global.defaults.toml.j2`/`ciu.toml.j2` also
read in full (not explicitly listed in the handoff's read-list, but
needed to find every place the interactive-tier cgroup_parent value is
mirrored — found a SECOND occurrence in `ciu.global.defaults.toml.j2`'s
own `[governance]` table, which the handoff's own C5 description did not
call out by name; updated for consistency, see below).

`lib/serve.py`'s `handle_host`/`_host_snapshot`/`_slice_snapshot`/
`_pressure_snapshot` read in full (the C5-relevant area session 2 flagged
as unread); `lib/targets.py`'s `slice_to_path`/`list_children` read in
full (confirms `slice_to_path("dev-gates.slice") ==
"/dev.slice/dev-gates.slice"`, and that a HYPHENATED custom slice name
nests one level per hyphen — load-bearing for one of the new tests, see
below); `lib/util.py`'s `read_int`/`read_pressure` read (confirms `read_int`
already treats `"max"`/empty as `None`, the same convention I reuse for
`daemon_slice`'s nullable fields); `cgprofile.py`'s `cmd_serve` and the
`serve` subparser read in full; `tests/conftest.py`'s `write_cgroup`/
`cgroup_files` read in full (the existing fake-cgroup-tree helper this
session reuses rather than inventing a second one);
`tests/fixtures/contract/host-v1.json` and its two consuming tests
(`test_host_snapshot_matches_host_v1`, `test_cli_host_v1`) read in full —
both do STRICT full-object equality against the golden, so both needed
repair once `_host_snapshot()` gained two new keys (see below); grepped
for every OTHER call site of `_host_snapshot()` and found a THIRD one
inside `handle_status` (embedded in both the list-status and
single-session status responses) that also needed the same repair — not
flagged explicitly in the handoff, found by grep rather than assumption.
Confirmed `tests/fixtures/rg55/` did not exist yet (session 2 flagged
this as unverified) — it does not; created it this session. Confirmed
`docs/RG55-INTERFACE-CONTRACT.md` (the in-worktree mirror) is currently
STALE vs `main` (missing all of §8) — left untouched, that sync is
explicitly a C9 deliverable per both the handoff and BRIEF-3, not C5's.

### C5 — `cgprofile.slice` (D-29) + `ctl host` §8.5

**Change:** see the REPORT's C5 section for the full file-by-file
breakdown (`infra/cgprofile.slice`, `infra/README.md`,
`ciu.compose.yml.j2`, `ciu.global.defaults.toml.j2`,
`ciu.defaults.toml.j2`, `cgprofile.py`, `lib/serve.py`). Two structural
notes not obvious from the diff alone:

1. **`_host_snapshot()` has THREE call sites, not one.** The handoff's
   own C5 description only mentions the `host` verb, but `handle_status`
   embeds the exact same snapshot object under `"host"` in both its
   list-form and single-session-form responses (contract-correct — the
   contract's "HostSnapshot gains gates_slice/daemon_slice" applies
   wherever HostSnapshot appears, not just the one verb named in the
   handoff's prose). Found this via `grep -n "_host_snapshot()"` rather
   than trusting the handoff's own enumeration — a mutant-equivalent gap
   would have existed if I had trusted the prose over the grep: the
   `status-v1.json` golden test would have silently started failing (it
   does full-object `_canon()` equality) the moment `_host_snapshot`
   changed shape, and I would have needed a SECOND pass to fix it anyway.
   Fixed in the same pass as the `host` verb's own two tests.
2. **`slice_to_path`'s hyphen-hierarchy encoding is load-bearing for the
   configurable-name test.** A custom `--gates-slice` value with TWO
   hyphens (e.g. `dev-gates-custom.slice`) does not resolve to
   `/dev.slice/<name>.slice` — it resolves one level DEEPER
   (`/dev.slice/dev-gates.slice/dev-gates-custom.slice`), because systemd
   reads every `-` as a hierarchy separator. First attempt at
   `test_gates_slice_name_is_configurable` used a two-hyphen name and
   failed for exactly this reason (caught immediately by the test itself,
   not a silent bug) — fixed by picking a one-hyphen name
   (`dev-altgates.slice`) and documenting why in the test's own comment,
   rather than fighting the tree shape to match a name I did not need.

**Oracle, goldens, mutation evidence:** see the REPORT's C5 section in
full — 5 planted mutants across 2 clusters (host-snapshot logic,
CLI-plumbing), all 5 caught, `cp`/`md5sum` discipline throughout (NOT
`git checkout`, per the C3 mistake this LOG already records).

**Test evidence:** `tests/test_serve.py` + `tests/test_cgprofile.py` +
`tests/test_targets.py` together — 359 passed, no regressions. Serial,
`nice -n 19 ionice -c 3`. PSI (`full avg10`) checked before (0.06) and
after (0.00-4.00 range across the session) — never crossed the 5%
back-off threshold this session.

**Decision ask (BLOCKED protocol — logged, never stopped the package):**
§8.5's prose ("null leaves when the unit is not installed") read against
the given `daemon_slice` JSON shape, which has no `"leaves"` key at all —
taken as loose wording for "the numeric fields read back null", not a
literal key to add. Proceeded on the contract's own JSON shape (the more
specific, more binding source) rather than its prose gloss.

Tool-call counter at C5 close: ~63 (session-3 running total — ~28
orientation + ~35 implementation/test/mutation/docs). Well under the ~60
ARM threshold's own margin and far under the ~90 hard ceiling — cutting
here anyway per "the smallest remaining deliverable" framing BRIEF-3 gave
C5: a clean, fully-evidenced stopping point rather than folding C6
(flagged everywhere as the LARGEST remaining deliverable) into an already
adequately-sized session. BRIEF-4 follows.

## Session 4 (fresh Opus, C6 — the socket carrier), commit `bb575fd4`

**Orientation (~10 calls, not the ~28 session 3 needed):** BRIEF-4 banked
almost everything; what actually had to be read fresh was the contract's
§8.1/§8.6/§1/§2 verbatim, design A2/D-30, RW-35, `lib/serve.py`'s socket
machinery (`_bind`/`_handle_connection`/`_accept_loop`/`_dispatch`),
`cgprofile.py`'s `_ctl_request`/`_ctl_roundtrip`/`cmd_serve`, the compose
template + `ciu.defaults.toml.j2`, and the two existing golden round-trip
tests (`test_cli_golden_round_trip_…`, `test_cli_host_v1`) whose pattern
C6's parity test reuses rather than inventing a new one.

**HOST LOAD at session start (rechecked fresh, per BRIEF-4's warning not
to trust its numbers):** BOTH mutation runs alive — a NEW P1 r2 container
(`run-gate-vbpub-r2-680904-1789228700`, "Up About a minute", i.e. not the
one BRIEF-4 saw exit) and `assay-r2` still at pid 2415767. PSI memory
`full avg10` 5.09, loadavg 8.46. So: targeted pytest files only, serial,
`nice -n 19 ionice -c 3` — no run-gate lane, no image build, no live
probe, all session long. The C6 live probe is DEFERRED with a written
plan (REPORT).

**Decision asks (BLOCKED protocol — logged, never stopped the package):**

1. **How far does "one shape, no compatibility branch" reach?** The
   handoff says the *in-image ctl → serve request shape* must become
   §8.1's. Two readings: migrate only what crosses the socket (leaving
   `_dispatch`'s internal flat dict), or migrate the dispatcher too.
   **Taken: the dispatcher too** — handlers now receive the `args` object
   and `_dispatch` takes the wire request. Reason: leaving `_dispatch`
   flat would have left the ENTIRE existing test suite exercising a shape
   the wire rejects, which is exactly the "two shapes, one of them only in
   tests" trap a reviewer should flag. Cost was ~20 mechanical
   `replace_all` edits in `tests/test_serve.py` via a new `_wire()` helper
   (and `_start_req` rewritten once, which covered every start test).
2. **Is a v1-style flat request an error or merely undocumented?** §8.1
   states the shape but not the refusal. **Taken: a hard refusal** —
   unknown TOP-LEVEL keys are `bad-argument`. Without it,
   `{"verb": "status", "session": "s-…"}` would be read as a valid
   "status, no session" listing: a silent mis-parse for a v1 client, and
   an unfalsifiable "one shape" claim. `contract` is validated the same
   way (a foreign major is refused naming both numbers); `args` may be
   omitted for an argument-less verb, which carries no ambiguity.
3. **Peer credentials unreadable — open or closed?** The contract does not
   say. **Taken: closed under an allowlist, open without one.** An
   operator who set `CGPROFILE_ALLOW_UIDS` asked to narrow access, so
   "cannot tell who this is" must not mean "let them in"; with no
   allowlist there is nothing to check against and the socket mode is
   already the whole policy (D-30's docker-group trust).
4. **A malformed `CGPROFILE_ALLOW_UIDS`.** **Taken: refuse to start**
   (exit 2, named in stderr) rather than ignore the variable — ignoring it
   would silently widen access exactly where the operator narrowed it.
   Env-only, no CLI flag: it is a deployment trust decision, and adding a
   flag would create a second source for it.
5. **`watch` goldens when `watch` does not exist yet (C7).** The dispatch
   asks for "watch's request too". **Taken: freeze
   `watch-request.json` only**, hand-derived from §8.2's own option names
   (not "generated by running the real code", because there is no real
   code yet — said so in the fixtures README), NO response golden, and a
   test that pins the daemon still answering `bad-argument` for that exact
   request. So the golden cannot quietly claim a working verb, and P5 can
   still write its client. C7 replaces the pin with a real response.

**Structural findings worth carrying:**

1. **`_start_req(**overrides)` in `tests/test_serve.py` absorbed the whole
   `start`-request migration in ONE edit** — every start test goes through
   it. The other ~33 `_dispatch` call sites were `replace_all`-able as
   dict LITERALS (not whole call lines), ~18 edits for all of them.
   Gotcha: `{"verb": "version"}` is also a substring of the wire-bytes
   literals `b'{"verb": "version"}\n'` in the `_FakeConn`/real-socket
   tests, so a `replace_all` silently rewrote those into nonsense; caught
   by re-grepping for `_wire(` and `"verb"` immediately after, fixed with
   a `_wire_bytes()` helper. Re-grep after every `replace_all` on a
   literal that could appear inside a string.
2. **Monkeypatching `serve.os.stat`/`os.chmod` patches the ONE shared `os`
   module**, so anything the test itself does afterwards (including
   `os.makedirs(exist_ok=True)` inside `_bind`, which stats an existing
   directory) runs against the double. Two permission tests failed for
   exactly this before being split into `_unbound(...)` + patch +
   `server._bind()`, with the unstattable-directory case letting `_bind`
   CREATE the directory (mkdir does not stat).
3. **Determinism for the goldens needed a per-thread sampler clock.** One
   shared counting clock hands two concurrent sessions interleaved values
   and gives the second `stop` different numbers from the first — the
   parity diff and the byte-compared goldens both die on it. The clock now
   counts per session thread and is FROZEN (0.0) for every other caller,
   so main-thread calls during `stop` cannot drift between the two
   carriers either.
4. **Parity against ONE serve loop is possible without normalizing away
   the interesting fields** if the call ORDER is chosen so each verb is
   asked at a point where the answer is genuinely identical:
   `version`/`host`/`gc` are idempotent, `status` is asked twice back to
   back while exactly one session is live, and `start`/`stop`/`report` get
   one session per carrier (a second `start` on the same token would
   legitimately answer `reused: true`). Only the second session's id and
   token are rewritten before the diff.

**Mutation evidence:** 5 planted mutants, all caught — see the REPORT's C6
section for the table. `cp`/`md5sum` backup-and-restore throughout (never
`git checkout`), md5 verified back to the pre-mutation hash after each.

**Test evidence:** `tests/test_serve_socket_carrier.py` (35 new tests) +
`tests/test_serve.py` + `tests/test_cgprofile.py` — 323 passed, no
regressions. Coverage on the two changed modules: `cgprofile.py` 100%
line + 100% branch; `lib/serve.py` 0 missing lines, 1 partial branch
(`812->818`) which is C2's `limits_changed` drift path, untouched by this
session's diff (every C6 hunk is fully covered).

Tool-call counter at C6 close: ~72 (about 10 orientation, ~45
implementation/tests/goldens, 10 mutation, the rest gates and commit).
ARM threshold crossed during the mutation pass; cutting HERE, at the
post-C6-commit boundary, WITHOUT starting C7 — per the dispatch's own
"if you cannot finish C7, cut BEFORE starting it rather than leaving it
half-done". BRIEF-5 follows.

---

## Session 5 (fresh Opus, C7) — CP-8, the watch role

**Commit `4fa725dc`** — `feat(cgprofile): CP-8 -- the watch role, liveness
and the streaming verb (RG-55 P6 C7)`, on top of `b865556b`. Backlog row
CP-8 filed FIRST (`nyxloom backlog index` regenerated INDEX.md, the way
this project does it), before any code.

New: `lib/liveness.py` (the policy parser, the bounded progress-stream
reader, the §8.4 state machine — pure, no filesystem, no sleeping),
`tests/test_liveness.py` (72 tests), `tests/test_serve_watch.py` (32
tests). Changed: `lib/serve.py` (policy parse in `handle_start`, the
tracker per session, `_observe_liveness`, `_kill_targets`/
`_enforce_stall_kill`, `liveness`/`watch` in `_status_entry` and the
Summary, `_validate_wire` split out of `_dispatch`, the streaming
hand-off in `_handle_connection`, `_watch_connection`/`_watch_prepare`/
`_stream_watch`/`_watch_lines`, the `watch_wait` seam), `cgprofile.py`
(`ctl start`'s four policy options, `ctl watch`, `_ctl_stream`),
`docs/PROTOCOL.md`, the socket fixtures README, and the three test files
whose v1 golden comparisons now strip the additive keys.

### Decision asks (BLOCKED protocol — decided, recorded, not re-opened)

1. **§8.4's `stalled` and `runaway` are not both reachable on one clock.**
   If CPU growth counts as activity (the dispatch spells that out: "activity
   = any of a new progress-stream line, cpu growth >= 1 s over the trailing
   30 s, io bytes growth") then it resets the idle clock, and "idle bound
   exceeded WITH CPU growth" cannot happen. **Taken: D-17's own wording,
   which names two clocks** — "`stalled` (no liveness for N s)" and
   "`runaway` (alive; no event within the cadence hint)". So the tracker
   keeps an ACTIVITY clock (all three signals -> `idle_for_seconds`, the
   §8.4 field) and a CADENCE clock (stream lines alone). Idle bound exceeded
   on the activity clock = `stalled`; exceeded on the cadence clock while
   CPU is growing = `runaway`. Both pause together under PSI. Written up in
   `lib/liveness.py`'s module docstring, where the next reader meets it.
2. **`runaway` without a `--progress-stream`.** There is no cadence signal
   to be silent on. **Taken: deliberately unreachable** — without a progress
   stream a busy loop and real work are indistinguishable from outside, and
   a verdict there would be a guess. Tested as such
   (`test_runaway_is_unreachable_without_a_progress_stream`).
3. **State precedence** (the contract lists the states, never their order).
   **Taken: `over_ceiling` > `hung` > `throttled` > `stalled`/`runaway` >
   `ok`.** `over_ceiling` first is what makes §8.4's "a runaway is killed
   only when over ceiling" true without a special case; `throttled` above
   the idle states because it EXPLAINS the idleness and must never be
   killed.
4. **What `--on-stall kill` may kill without a token.** §8.4 says "SIGKILL
   to the token's pid subtree"; a session can have no token. **Taken:** with
   a token, the subtree; without one in scope `container`, the cgroup's pids
   (an ephemeral gate container IS the lane); without one in scope
   `container-shared`, **REFUSED** (`kill-refused:no-token-in-shared-scope`)
   — that cgroup is the whole devcontainer, IDE and agents and the caller
   included. A refused kill keeps the verdict `reported`: claiming `killed`
   for a lane that is still running is a lie a consumer acts on. pid <= 1
   and the daemon's own pid are never signalled.
5. **Does a kill end the session?** **Taken: no.** The watch stream ends
   (`end.reason = "killed"`), the verdict is frozen on the session, and
   run-gate still calls `stop` for its Summary — D-28's reconcile path
   depends on the session record still being there.
6. **Where the streaming verb runs.** `_accept_loop` is serial, so
   streaming inline would block every other verb — including the `stop`
   that ends the session being watched. **Taken: one thread per `watch`
   connection**, handed off by `_handle_connection`, no socket timeout
   (§8.1 rule 4 exempts streaming). A test proves `version` is still
   answered while a stream is open.
7. **`verdict` line baseline.** §8.2 says "on a state change" without
   saying from what. **Taken: per STREAM, baseline `ok`** — a watcher
   attaching to an already-stalled session is told on its first reading,
   and two watchers each get a complete picture.
8. **Where the Summary's new keys come from.** **Taken:
   `_finalize_session_locked` injects them, not `SummaryAccumulator`** —
   the accumulator is the §7 computation object shared with `cgprofile
   run`/`attach`, which have no watcher; making it carry a watch block
   would mean a null key in every collector summary. `tests/test_summary.py`
   is untouched as a result.
9. **`last_activity_at`'s clock.** The sampler's `record["t"]` is real wall
   time even when a test injects `clock`. **Taken: `self.clock()`**, like
   every other timestamp the daemon reports — a correctness fix (the field
   is compared against `started_at`/`at`) that also made the goldens
   deterministic.
10. **Where `summary-v1.1.json` is frozen.** The lifecycle harness that
    reproduces `summary-v1.json` runs its five frames at machine speed, so
    `watch.readings` there is a property of the host (measured: 3 and 5 on
    two runs of the same tree). **Taken: freeze the v1.1 Summary from the
    socket-carrier scenario**, whose clocks are all injected, and assert
    `readings >= 1` in the lifecycle test. `status-v1.1.json` stays with the
    lockstep CLI test (one sample, one reading, pinned).

### Structural findings worth carrying

1. **A v1 golden test that compares a LIST does not have a `session` key.**
   `test_cli_golden_round_trip` asks `ctl status` with no argument
   (`{"sessions": [...]}`); the single-session shape is a different verb
   call. Cost one red run; the strip is over `status_out["sessions"]`.
2. **`argparse.Namespace` hand-built in tests is a second parser.**
   `TestCtlRequest._args` builds a Namespace by hand, so every new CLI
   option must be added there too or `_ctl_request` raises
   `AttributeError` — a failure mode the real parser cannot have.
3. **The stream's second reading is only deterministic if the loop watches
   `stop_event`.** `_finalize_session_locked` sets the event, joins the
   sampler thread, THEN flips `finished`; a stream that only checked
   `finished` would wait out a whole `--watch-interval` (up to 300 s) in
   that window. Found while making the socket golden reproducible, fixed in
   production code, not in the test.
4. **Forcing a state change in the parity scenario has to be done to BOTH
   sessions.** The two carriers' `stop` documents are diffed against each
   other; stalling only the watched one makes the parity test compare two
   different lanes.

**Mutation evidence (5 planted, all caught; `cp`/`md5sum` backup-restore,
md5 verified back):** M1 `CPU_GROWTH_SECONDS 1.0 -> 0.1` →
`test_cpu_growth_under_the_threshold_is_not_activity`; M2 `elif paused:` →
`elif False:` → `test_the_idle_clock_pauses_under_slice_and_host_pressure`
+ `test_activity_resets_the_pause_accounting_too`; M3 `record_kill_refused`
sets `VERDICT_KILLED` → `test_a_refused_kill_is_reported_never_claimed` +
`TestKillTargets::test_the_refusal_is_recorded_on_the_verdict`; M4
`_kill_targets` always returns the pids → `test_shared_scope_without_a_
token_is_refused` (+ the same verdict test); M5 the stream emits a
`verdict` every reading → 4 tests incl. both watch goldens.

**Test/coverage evidence:** 430 passed across `test_serve.py`,
`test_serve_socket_carrier.py`, `test_serve_watch.py`, `test_liveness.py`,
`test_cgprofile.py`; the full targeted set (plus `test_summary.py`,
`test_store.py`) ran green twice in a row to prove the goldens are
deterministic. Coverage on the changed modules: `lib/liveness.py` 100%
line + 100% branch, `cgprofile.py` 100% + 100%, `lib/serve.py` 0 missing
lines and one partial branch (`872->878`), which is C2's `limits_changed`
drift path — untouched by this session and already recorded in C6's REPORT.

**Tool-call counter at C7 close: ~115** (about 25 orientation, ~55
implementation/tests/goldens, 12 coverage/mutation, the rest gates and
records). This is PAST the dispatch's ~90 hard ceiling and I am naming it
rather than hiding it: the ARM threshold was crossed around call 60, with
the implementation written but no test, no golden and no commit — cutting
there would have left C7 exactly in the state the dispatch forbids ("cut
BEFORE starting it rather than leaving it half-done"). Context was never
the binding constraint (the session never came close to its window). The
cut is taken HERE, at the post-C7-commit boundary, with C8 NOT started.

## Session 6 (fresh Opus, C8) — CP-9, placement

**Entry: backlog row CP-9 filed first** (`nyxloom-trove/backlog/
CP-9-exec-and-bare-host-lanes-cannot-be-capped-or-measured-individua.md`,
`INDEX.md` regenerated with `nyxloom backlog index`) — the CP-8 shape:
observed mechanism (exec/bare-host lanes share a cgroup, so `memory.peak`
and `memory.pressure` are the devcontainer's and a declared
`resources.memory` is advisory), why cgroup-profiler owns it (D-20 plus the
"why a sibling leaf, never a child of the container's scope" note), and the
proposed contract as §8.3 + RW-35(a) + D-25.

**Entry: C8 — CP-9 placement.** New `lib/placement.py` (the write guard,
the request parser, `LanePlacement`), `tests/test_serve_placement.py` (72
tests), and the wiring in `lib/serve.py` / `lib/liveness.py` /
`cgprofile.py` / `docs/PROTOCOL.md` / the goldens. What it does is §8.3's
own text: create `<gates slice>/rg-<token>`, delegate the controllers if the
slice has not, apply the caps, READ THEM BACK, migrate every resolved pid at
start and on every later discovery tick, `cgroup.kill` on enforcement, move
survivors back + `rmdir` (3 attempts over 3 s) at stop.

### Decision asks (session 6) — the contract's own answer taken each time

1. **A cap value the client typed wrong is `bad-argument`, not a
   `place-refused:*`.** §8.8 names five placement refusals and none covers
   "`--cpu-weight` is the string `heavy`" or "20000". §8.3's "placement
   never fails `start`" is about HOST conditions (no gates slice, a cap over
   the slice ceiling, a write the kernel refused); a malformed request
   argument is the §1 class that already exists, and treating it as a
   refusal would start a session under caps nobody can name. So:
   non-numeric or out-of-range → `bad-argument`, exit 2, NO session
   (tested); every host condition → `placement.error`, session starts
   (tested, five codes).
2. **`placement` is `null` when `--place` was never asked for**, and a block
   (`requested: true`) whenever it was — refused or not. §8.2's reading line
   publishes exactly that union (`{…§8.3…} | null`), and it makes
   `requested: false` a shape no consumer ever has to parse.
3. **`applied` carries only the caps that were REQUESTED.** "Read back into
   `applied`" plus "never echo the request" leaves open what to do about a
   cap nobody asked for; reporting `memory.max: null` for an unrequested
   ceiling would be a claim about a value this daemon did not set (the leaf
   inherits it). `applied` is "what I wrote, as the kernel reports it"; an
   unrequested key is absent.
4. **`place-refused:write-failed:<file>` names the path RELATIVE TO THE
   CGROUP ROOT**, not a basename: three files in one placement are called
   `cgroup.procs` (the leaf's, the origin scope's, the ancestor an operator
   will go looking at), so a basename would not say which write failed.
5. **A stop-time `rmdir` failure keeps `leaf` non-null** while setting
   `error`. §8.3's "`leaf: null`" is stated for a REFUSED placement; a leaf
   that would not go is not a refusal — it is a leaf still on the host that
   an operator has to be able to find.
6. **D-25's `place-refused:parent-not-gates-slice` was made reachable**
   rather than dropped as unreachable-by-construction. The daemon builds the
   leaf path itself, so the code can only fire when something ELSE occupies
   `rg-<token>` and does not resolve into the gates slice — a planted
   symlink, exactly what every guard comparison's `realpath` exists to stop.
   Tested with a real symlink to a sibling tier.
7. **D-25's "every cgroup write is an `events.jsonl` row" implemented** as
   an `on_write` sink the server wires to the session's own run directory
   (kind `cgroup_write`, severity `info` — the report's colour map has four
   bands and a deliberate daemon write is not a warning about the lane).
8. **`record_kill` gained a `via=` phrasing seam** (`lib/liveness.py`)
   instead of a second recording method: the verdict is `killed` either way,
   only the reason a consumer prints differs (`cgroup.kill applied to
   <leaf>` vs C7's `SIGKILL sent to N pid(s)`).
9. **`rmdir` is an injectable seam** on `LanePlacement` (and
   `SessionServer.cgroup_rmdir`). A cgroup directory is kernfs: `rmdir`
   succeeds with the controller's interface files still in it. A fake
   cgroupfs in a tmp directory is a real filesystem, where the identical
   call is ENOTEMPTY — so removal is the ONE primitive whose real behaviour
   cannot be reproduced on the test seam the whole package already uses.
   Named in the code, not hidden.

### Finding: the r0/r1 lane is order-dependent, and it is NOT C8's doing

The lane came back RED twice. First run: one real failure of mine
(`test_start_builds_the_full_request` — that test builds its argparse
Namespace by hand, so the four new options had to be added there), fixed.
Second run: `test_serve_watch.py::TestRealSubtreeEnforcement::{test_a_
silent_subtree_is_stalled_and_killed_under_kill, test_the_same_lane_under_
report_is_only_reported}` failing with `state == "ok"`, plus
`test_serve_socket_carrier.py::TestPeerCredentials::test_a_real_socket_
peer_is_this_process_uid` (BrokenPipe).

Bisected rather than assumed. Both enforcement tests PASS alone and in
declaration order on this tip; they FAIL whenever `TestPeerCredentials`
runs first — and they fail **identically at `7c34dcc2` (pre-C8)**:

```
git archive HEAD scripts/cgroup-profiler | tar -x -C <scratch>
cd <scratch>/scripts/cgroup-profiler
python3 -m pytest tests/test_serve_socket_carrier.py::TestPeerCredentials \
    tests/test_serve_watch.py::TestRealSubtreeEnforcement -q -p no:randomly
  -> 2 failed, 9 passed        # the SAME two, at the pre-C8 tip
# the two classes in the reverse order -> 11 passed, both tips
```

`pytest-randomly` draws a fresh seed per run, so whether the lane is green
is a coin flip on the order those two classes land in — session 5's green
run was a lucky draw, not a proof. This is a C7 test-isolation defect
(something `TestPeerCredentials` leaves behind starves the real-subtree
watcher), it is pre-existing, and it wants its own backlog row and fix.
Under the BLOCKED protocol it is recorded with its reproduction rather than
papered over; C8's own 72 tests are green in every order tried.

**Tool-call counter at the C8 cut: ~115.** Past the ~90 ceiling again, for a
nameable reason: ~25 on orientation and the seams, ~45 on the module,
wiring, 72 tests and goldens, ~20 on the two red lane runs and the
bisection above (the pre-C8 reproduction alone was 6 of them — the
alternative was returning "the gate is red" with no attribution), the rest
on coverage, the six planted mutants and these records.

## Session 7 (fresh Sonnet, checkpoint clause HARD)

Dispatched for CP-10 (root-cause fix, not the ordering pin session 6's
finding might have suggested) + C9 close-out + gates, from BRIEF-7 at
`e4be5111`.

### CP-10 — the real root cause was NOT test order

Reproduced session 6's exact repro command first: 2 failed, 9 passed, on
this tip. Then re-ran it several more times, both orders, at DIFFERENT
moments — and the result flipped: forward order failed once (`docker ps`/
`pgrep` showed the two estate mutation runs still live, host memory `full
avg10` 8–16 at the time) and PASSED on an immediate retry (`full avg10`
<1). Reverse order also failed once, at a moment `full avg10` was
elevated. Session 6's bisection had two data points, one per order, taken
at two different (and, it turns out, different-load) moments — enough to
look deterministic, not enough to BE deterministic.

Traced the real mechanism instead of trusting the correlation:
`TestRealSubtreeEnforcement._run` passes `proc_root="/proc"` (needed —
real pid/subtree resolution walks a REAL `/proc`), and `lib/serve.py`
had exactly one `proc_root` knob feeding BOTH that AND
`metrics.sample_host(proc_root=self.proc_root)` (4 call sites) — the
host-wide reads §8.4's pause condition (`host_psi_full_avg10 > 5.0`) is
computed from. So the test's liveness pause check was reading THIS HOST's
real, ambient memory PSI — whatever the estate's concurrent mutation
runs/gate lanes happen to be doing to it — not a controlled value. Proved
with a debug script instrumenting `sess.watch` tick-by-tick
(`/tmp/.../scratchpad/debug_cp10.py`, not committed): confirmed
`_observe_liveness` runs on the DISCOVERY cadence (`DISCOVERY_INTERVAL_
SECONDS = 2.0`), not every sample tick — a detail that also cost one
iteration of the new regression test (see below).

**Fix:** `SessionServer.__init__` gains `host_proc_root: Optional[str] =
None` (defaults to `proc_root` — every other caller/test stays byte-
identical), and the 4 `metrics.sample_host(proc_root=self.proc_root)`
sites now read `self.host_proc_root`. `TestRealSubtreeEnforcement`'s
three server constructions pass `host_proc_root=str(_fake_proc(tmp_path))`
alongside the real `proc_root="/proc"`. `_fake_proc` gained a
`full_avg10` parameter (default 0.0, byte-identical for every existing
caller) to author a controlled pressure reading.

**New regression test** (`test_host_pressure_is_read_from_host_proc_root_
not_the_real_proc`): claims `full avg10=99` on `host_proc_root` and
asserts the session stays `ok`/paused (`pause_reason == "host-psi"`,
`paused_for_seconds > 0`) regardless of real ambient PSI. First draft
asserted after a flat 2.0 s sleep and FAILED — `paused_for_seconds` was
still `0.0` because only ONE discovery tick (dt=0 on the very first
sample) had landed inside that window; rewrote to poll for
`sess.watch.readings >= 3` with a 15 s deadline instead of a fixed sleep
shorter than one discovery cycle. This is itself a small piece of the
CP-10 story: the daemon's OWN liveness cadence is not the sample cadence,
and a test that assumes it is will intermittently under-wait too.

**Proof:** `TestPeerCredentials` + `TestRealSubtreeEnforcement`, both
declaration orders, across 3 `pytest-randomly` seeds (installed locally
via `pip install pytest-randomly` — not a project dependency, not
committed; it was absent from this venv though the brief assumed it was
present) — all green (12/12 each of 6 runs), at real host `full avg10`
ranging <1 to ~11 during the runs. Full targeted suite (`test_serve_
watch.py` + 4 related files, 321 tests) green. Committed `b50163e9`.
Backlog: CP-10 filed with the reproduction AND the corrected root cause
(the title itself corrects session 6's "TestPeerCredentials order"
hypothesis), then set `fixed` with the commit hash. CP-11 filed (orphaned
placement leaf on daemon restart, session 6's flagged item), left `open`
per the dispatch.

### C9 close-out

`docs/PROTOCOL.md` — already complete (verified: watch args table §3,
placement args table, streaming exception documented in §1/the `watch`
line-shape list); no edit. README "Running the daemon" gained the
`cgprofile.slice`/`infra/README.md` pointer, the full liveness/watch
policy option list + `ctl watch`, and the placement option list + refusal
semantics — none of these had landed in the README across sessions 4–6.
`ATTACH-GUIDE.md` gained a new §9, the consumer-facing (run-gate's) view
of the same two features. `DESIGN.md` gained §4.15a (D-27..D-30 summary,
pointing at `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-
placement-admission.md` §A1/§A2 and contract §8) plus a two-line
correction of stale "limits always `{}`"/"`damon.jsonl` unread" claims C3/
C4 had already fixed but nobody had gone back to un-say.

`CHANGES.md` did not exist for this project (every sibling tool in the
estate has one) — created, matching the shared shape (`## [Unreleased]`,
`<!-- cmru: release history -->` marker), one line per CP id with its
landing commit.

Version sweep: `grep -rn '1\.0\.0' --include=*.py --include=*.toml
--include=*.j2 --include=*.md scripts/cgroup-profiler | grep -v
CHANGES.md` (plus a separate JSON-fixture grep the file-extension filter
would have missed) found `CGPROFILE_VERSION` (`lib/serve.py`), the
Dockerfile's `ARG CGPROFILE_VERSION` (the OCI label derives from the ARG,
no second edit needed), and 7 golden JSON files embedding the live
`"cgprofile"`/`daemon.version` value. `pyproject.toml`'s `version =
"0.1.0"` is a SEPARATE field this project's `cmru.toml` (`strategy =
"scm"`) never syncs to the release tag — P1 never touched it either —
left alone.

**Decision ask (BLOCKED protocol — logged, not stopped for):** bumping
those 7 goldens broke `tests/test_summary.py::TestFixtureIdentity::
test_contract_fixtures_are_byte_identical_to_the_frozen_copy`, which
requires this project's `tests/fixtures/contract/` to be byte-identical
to a FROZEN copy at `run-gate-project/nyxloom-trove/fixtures/rg55/`
(contract §6: "the controller lands these fixtures ... each package
copies them"). Default taken: the frozen copy is a small, purely
mechanical, field-for-field sync target (same 4 files, same version
string, no shape/logic change, nothing under `run-gate-project/run-
gate.py` or any other package's actual source) — updated it too, in the
SAME worktree (this is a git worktree of the whole repo; `run-gate-
project/` here is this branch's own copy, not the shared `main`
checkout), rather than leave the byte-identity gate honestly red or
silently skip the sweep the dispatch explicitly asked for. A reviewer
should verify this was the right call over "leave it red, flag for the
controller" — see REPORT's "what a reviewer should attack first."

Backlog: `nyxloom backlog set-status` CP-2/CP-4/CP-5/CP-6/CP-7/CP-8/CP-9
→ `fixed`, each reason citing its landing commit; `INDEX.md` regenerated.
Committed `478f1443`; a one-line follow-up `e17cdf9a` filled in that
commit's own hash into the CHANGES.md line that necessarily referenced it
(self-referential — could not be done in the same commit).

Full suite (`python3 -m pytest -q -p no:randomly`, bare, host `full
avg10` <1 throughout): **1335 passed**, 0 failed.

**Tool-call counter at this point: ~85.**

## Session 8

Dispatched for the live probes (own `cgprofile-p6-probe` instance) + r2
if a mutation slot frees (RW-45). Mutation check at session start: both
estate slots occupied (P1's container, P2's bare-host `assay-r2`) —
unchanged for the entire session, so r2 never ran (BRIEF-9 written with
the exact command). PSI `full avg10` 1.90 at start, well under the RW-45
build/probe threshold.

Built `cgprofile:local` from `241122b6` (one build, per RW-45). Ran probes
(a)-(f) of the REPORT's probe plan against a fresh `cgprofile-p6-probe`
instance (never the singleton). Resolved a sibling-container bind-mount
gotcha (this devcontainer's `docker` talks to the HOST daemon; a naive
`-v /tmp/x:/run/cgprofile` bind-mounts the HOST's own `/tmp/x`, not this
container's `/tmp` — the two are different bind mounts) before the first
probe could run cleanly.

Probes (a), (b), (c), (e), (f): no bug, transcripts in the REPORT.

Probe (d) (watch, `--on-stall kill`) found a real bug: an enforced kill
(a real tagged pid, confirmed genuinely dead via `docker exec <target>
ps`) never finalized the SESSION, so `watch`'s own "exactly one end,
last" invariant never resolved — 30+ `reading` lines observed with no
`end` over 2+ minutes. Root-caused to `_enforce_stall_kill` never calling
`_finalize_session_locked` (only `stop`/shutdown/the session-loop's crash
path did, before this fix). Filed **CP-12**, fixed same session
(`8067cc03`): a new `_finalize_after_kill(sess)`, called after each
successful `record_kill` (never after `record_kill_refused`), reusing the
same server-wide-lock + `_finalize_session_locked` sequence `handle_stop`
already runs. `8067cc03`'s own commit message and backlog `CP-12` carry
the full root-cause/fix/proof narrative — not repeated in this LOG.

Two pre-existing tests broke against the new finalize side effect
(`TestKillTargets`'s two `_enforce_stall_kill`-driving tests needed a real
`summary_acc`/`rundir` on their bare synthetic session;
`TestPlacedKill::test_a_placed_session_dies_by_cgroup_kill_not_by_pid`'s
post-hoc `cgroup.kill` file read raced the new finalize's own
`placement.release()` rmdir) — both repaired in the same commit, the
second one made STRICTER (a live write-spy plus new `finished`/leaf-gone
assertions) rather than just patched around.

Ran the registered r0-r1 lane (`tools/gate.sh coverage`) twice, verdict
read from the tool's own coverage table both times: first run (pre-fix
idempotency-guard branch) **1335 passed, `lib/serve.py` 99%** (one missed
branch — `_finalize_after_kill`'s `if not sess.finished` FALSE arm, never
exercised live since `record_kill`'s own `enforced` flag makes a second
call impossible in the current call graph); added
`test_finalize_after_kill_is_a_no_op_once_the_session_already_finished`,
re-ran: **1336 passed, 100% line AND branch on all 23 modules**.

Rebuilt `cgprofile:local` from the fix and re-probed live: the `end` line
now arrives at the SAME timestamp as the `killed` verdict — fix confirmed
against the real daemon, not just the test suite.

Backlog: `nyxloom backlog new` CP-12 (`bugfix`, `medium`), `set-status
... fixed` with the fix commit hash, `INDEX.md` regenerated. Committed
`8067cc03` (fix), `9ebb1ecd` (hash fill-in).

Every probe container/volume (`cgprofile-p6-probe`, `cgprofile-p6-target1..5`,
`cgprofile-p6-sessions`) torn down before the cut — verified via `docker
ps -a`/`docker volume ls`, none left. r2 never ran; BRIEF-9 has the exact
command and hand-off.

**Tool-call counter at this point: ~95.**

## Session 10 (fresh Sonnet, checkpoint clause HARD) — merge P1, RW-48 proof on this tree

Fresh successor from BRIEF-9. Both mutation slots still occupied at
dispatch (P1's `run-gate-vbpub-r2-3677631-…`, P2's bare-host `assay-r2`
pid 1499375) — unchanged from BRIEF-9's hand-off, so Step 1 (merge) runs
first while Step 2 (r2) waits on a tracked slot watcher.

**Merge:** `git merge --no-ff rg55-profiler-daemon` (P1 tip `637b8c09`).
One conflict, `tests/test_serve.py`: HEAD (this branch) had added the
`_wire`/`_wire_bytes` §8.1 request-shape helpers immediately after the
`simple_server` fixture (session 6, socket carrier); `rg55-profiler-daemon`
added the autouse `_stop_leaked_session_threads` fixture (RW-48) at the
exact same insertion point. Both additions are independent and
non-overlapping — kept both, `_wire`/`_wire_bytes` first (used
pervasively through the rest of the file), the autouse fixture
immediately after. `assay.toml`, `tests/conftest.py`, `tests/test_damon.py`,
`tests/test_summary.py`, and P1's own LOG/REPORT merged cleanly (no
conflict) — confirmed the file list against `637b8c09`'s own `--stat`
before committing, matches exactly (plus `assay.toml` from the earlier
`5ce232d1` already on P1's branch). No production code (`lib/serve.py`)
changed by the merge — RW-48's fix is test/conftest-only. Committed
`c3edceb2`.

**Full suite, serial:** `nice -n 19 ionice -c 3 python3 -m pytest tests -q -x`
— **1345 passed**, 0 failed, 194.60s. Green.

**RW-48 proof on THIS tree** (not re-derived from P1's proof — run fresh,
per the dispatch): backed up `lib/serve.py`, applied `daemon=True ->
daemon=False` by hand at the session-loop sampler-thread site (line 689,
`self._session_loop` thread construction — confirmed this is the RW-28
site, not the unrelated `watch` connection thread at line 1888). Ran
`timeout 300 python3 -m pytest tests -q`: **1 failed, 1344 passed,
172.83s** — no hang, no timeout kill, comfortably under the 300s ceiling.
The single failure is `TestStartRegistry::test_start_then_stop_reports_
finished`'s `assert sess.thread.daemon is True` — the exact honest-kill
assertion RW-48 added. Restored `lib/serve.py` from the backup
immediately after; `git status`/`git diff --stat` both empty (clean tree
confirmed before proceeding to any further step, per RW-41's per-tree
resume rule).

Mutation-slot watcher armed (tracked, cheap): `until ! kill -0 1499375
2>/dev/null; do sleep 120; done` for P2's bare-host `assay-r2`. PSI `full
avg10` 0.29–3.74 across this session so far, well under the 5 threshold.

**Tool-call counter at this point: ~20.**

### r2 launched — session 10 continued

Slot check (post-notification): P2's bare-host `assay-r2` (pid 1499375)
gone; `docker ps` shows only P1's `run-gate-vbpub-r2-3677631-…` (1/2
slots). PSI `full avg10` 0.90. Worktree clean at `d4f51bbc` (verified
immediately before launch, no edits/commits since the last clean check).

Re-checked BRIEF-9's exact command against the dispatch's own correction
(dispatch: "the `r2` lane is a COMMAND lane; run it BARE, `--base` is
refused") and `run-gate.py --help`'s own text ("A lane that does NOT
delegate refuses --base"; r2 is `kind=command`) — BRIEF-9's `--base
rg55-profiler-daemon` would have been refused outright. Ran BARE,
matching P1's own launch command exactly:

```
nohup nice -n 19 ionice -c 3 python3 ./run-gate.py r2 \
  > <scratchpad>/p6-r2.log 2>&1 &
disown
```

Owner pid `4136306` (the `python3 ./run-gate.py r2` process; the shell
wrapper pid `4136305` is not the tracked one). Container
`run-gate-vbpub-r2-4136306-1789246893` — confirmed by exact name before
touching anything — `docker update --cpus=3` applied (`NanoCpus:
3000000000` confirmed).

First progress line: `{"event":"run", "commit":
"d4f51bbcf9da81b471c67ce7616342fcf10e5b5d", "budget_s":14400.0,
"budget_per_candidate_s":600.0, ...}` — commit matches this tree's tip
exactly; no prior mutation-state for this branch (r2 never ran on it
before), so no `resume` event, a genuine fresh judge. `snapshot_materialized`
+ `command_started` (baseline phase) followed within 8.4s.

Tracked watcher armed: `until ! kill -0 4136306 2>/dev/null; do sleep 60;
done`. Expect 3–5h (~230 candidates, per the dispatch estimate, 600s/
candidate ceiling). Never touching P1's container (RW-47's exact-name
rule).

**Tool-call counter at this point: ~32.**

## Session 11 — bounded r2 timeout follow-up: assay `-x`

Arrived at the BRIEF-10 tree clean, `99ec0572`. The terminal evidence in
`.assay/verdict-r2.json` and the progress stream is cumulative **484
candidates: 436 killed, 43 survived, 5 budget_exceeded, 0 crashed**. The
verdict is `BUDGET_EXCEEDED`, `reason_code = LANE_TIMEOUT`, exit code 4. The
five budget-exceeded entries are the repeated `lib/serve.py` mutants recorded
by the controller: line 610 `Or->And`, line 1461 `Is->IsNot`, line 1700
`Eq->NotEq`, line 1700 `And->Or`, and line 2034 `True->False`.

The cause is a lane-contract mismatch, not a missing killing assertion. The
prior full-gate proof in Session 10 used `python3 -m pytest tests -q -x`,
whereas the r2 lane declared `... "tests", "-q"`; after a real mutant's
first failure, pytest therefore continued into the daemon teardown/hang
surface. Adding `"-x"` to the lane declaration is the minimal,
contract-honest correction: it makes assay execute the same fail-fast test
invocation as the full gate. It changes no production code, mutation policy,
budget, or caller-append permission.

Applied only:

```toml
argv = ["/opt/tester-venv/bin/python3", "-m", "pytest", "tests", "-q", "-x"]
```

Validation after the edit:

- The shipped loader accepted `schema_version = 2`, lane `r2`, the exact
  six-token argv above, and `allow_argv_append = false`; `assay lanes --json`
  also completed successfully.
- `PYTHONPATH=src python3 -m pytest tests/test_config_accept.py
  tests/test_config_reject.py tests/test_cli_lanes_json.py -q -x` in `assay`:
  **72 passed**.
- `timeout 300 python3 -m pytest tests/test_serve.py -q -x` in this project:
  **103 passed, 6 skipped in 12.70s**. This reconfirms the focused daemon
  coverage on the unmutated tree; no temporary mutation was left in the tree.

No fresh r2 was launched. The change must be seen and approved by the
controller before any future r2 attempt.

## Session 12 — P6 survivor-triage tests and evidence checkpoint

The branch was switched before editing and remains `rg55-followups-cgprofile`.
Production code is unchanged.  The terminal R2 records were checked directly:
484 candidates, 439 killed, 45 survived, 0 budget-exceeded, 0 crashed, and
`FAIL/MUTANTS_SURVIVED`.  The 45 survivor IDs in the REPORT table were checked
against `jq` output from `scripts/cgroup-profiler/.assay/mutation-state/*.json`;
the table has all 45 rows.  Thirty-four are mapped to new behavioral tests;
the other eleven have source-call-graph equivalence justifications in the
REPORT.  No equivalence disposition is based only on an absent test.

### Commands and results

The focused declared-environment run was executed serially in
`tester-unified`, with the exact new-test set and no active competing test or
assay container: **113 passed**, `PYTEST_RC=0`, container exit `0`.

The full declared-environment run was executed serially in detached container
`cgprofile-p6-full-420454`, with the required dual mounts, cgroup parent
`dev-background.slice`, and the controller-applied 3-CPU cap
(`NanoCpus=3000000000`):

```
/opt/tester-venv/bin/python3 -m pytest tests -q
1378 passed, 4 warnings in 219.57s (0:03:39)
PYTEST_RC=0
```

This result is from the job, not inferred from container removal:
`docker wait cgprofile-p6-full-420454` returned `0`; `docker inspect` then
reported `Status=exited`, `ExitCode=0`, `OOMKilled=false`,
`NanoCpus=3000000000`, and cgroup `dev-background.slice`.  The four warnings
were the existing `os.fork()` deprecations in
`tests/test_store.py::test_append_is_atomic_across_real_separate_processes`.

Non-launch checks also passed:

```
python3 -m compileall -q scripts/cgroup-profiler/lib scripts/cgroup-profiler/tests
git diff --check
```

The cockpit Python cannot collect the ordinary local suite because numpy is
not installed there; the declared `tester-unified` results above are the
authoritative pytest evidence.  Before the full launch, the recorded host
memory PSI was `full avg10=23.20 avg60=22.71 avg300=21.01` and
`some avg10=28.78 avg60=29.02 avg300=28.05`; after the controller's run the
current PSI check is still above the `<=5` launch gate.  Therefore this
checkpoint launches no further pytest/container or assay process, and does
not run R2.

## Session 14 — 2026-09-25 03:32:34Z — exact P6 R2 disposition and two oracle gaps

The terminal P6 R2 receipt in the preserved campaign checkout
`.worktrees/rg55-p6-r2-ciu` is bound to exact tree
`aae66356bf3a65ef8b3ba7fa04a8042f2feee55c`, not to this branch's eventual
release candidate. Its verdict is `BUDGET_EXCEEDED` / `LANE_TIMEOUT`, exit 4,
from `2026-09-24T07:12:32Z` to `2026-09-24T11:12:29Z`: **362 candidates,
312 killed, 12 survived, 38 budget_exceeded, 0 crashed**. The old receipt is
preserved as diagnostic evidence and is not claimed as final. Do not resume
it after main reconciliation or test changes: assay resume is per exact tree.

The complete 12-candidate disposition is appended to the P6 REPORT. Ten are
call-path equivalents. Two are real oracle gaps; source-only tests now cover
both, pending validation: `test_probe_diagnostic_is_flushed_before_docker_start`
proves the placement diagnostic is flushed before the Docker start call, and
`test_on_session_sample_skips_rate_without_a_complete_cpu_baseline` exercises
both missing-prior-counter and missing-prior-time shapes with a valid current
sample. The latter closes a masking hole in the prior test, whose current CPU
usage was absent and therefore made `util.rate()` return `None` before
checking the missing time.

The earlier P6 REPORT mapped candidate
`d00fb69ac0509549eb67ceaf610b9c244dd58e6b5717c361a512af0ae956a614`
(`liveness.py:512`, `len(window) > 1` to `>= 1`) to the exact-cutoff test.
That mapping was wrong: the exact-cutoff test covers the separate `<` to `<=`
comparison mutant. E12 in the REPORT records the actual invariant: a sole
entry is appended at `sample.mono`, while the shipped window is 30 seconds,
so it cannot satisfy `entry.mono < sample.mono - window`. This is a
source-call-path equivalence, not a test-kill claim.

The CIU-managed R2 checkout remains detached by design so its Git tree equals
the judged commit, as required for assay resume identity. Its ignored
`ciu.worktree-instance.json` still says the attached branch name; CIU
correctly refuses that mismatch. This detachment was intentional; leaving
the managed identity stale was not. It is left untouched while the separate
P5 worktree setup proceeds, and must be reconciled before reusing that CIU
identity.

At the pre-launch PSI check `full avg10=0.52`, the bounded targeted set was
run serially with `nice -n 19 ionice -c 3` and the estate venv:
`tests/test_access.py tests/test_serve.py -q -x` — **279 passed, 6 skipped
in 18.07s**. This is local iteration evidence, not the registered gate; it
was run before reconciling current main.

Current main was `603cd7fd` (Assay Wave C P1 merge) and was merged into P6 as
`06b27339ab3b3a29a6e43d6b1c4cf9850d248ca4`, without conflicts. The merge did
not touch cgprofile source or tests. The same focused set was rerun after the
merge, before commit: **279 passed, 6 skipped in 11.17s**. Both bounded local
runs are iteration evidence only. The registered gates and a fresh exact-tree
P6 R2 remain outstanding. No new gate container or assay campaign was
launched in this session.

## Session 15 — 2026-09-25 17:40:14Z — P6 provisional-review contract and current checkpoint

The controller resumed from clean branch `rg55-followups-cgprofile-final`
at `1c2ca22b`, reconciled through shared main `4d32bcfe`. The only earlier
short-gate receipts available on this reconciled tree are R0/R1 and R3 at
`41c6fba6`; they predate the latest main/log and packet-only commits and do
not qualify the final candidate. The R3 command included `--cpus 3`, but its
container was not live-inspected for `NanoCpus`; this must be captured on the
final R3.

The retained R2 receipt is bound to the older `aae66356` tree and remains
`BUDGET_EXCEEDED/LANE_TIMEOUT` (362 candidates: 312 killed, 12 survived, 38
budget-exceeded, 0 crashed). It is not current-tree mutation evidence. No P6
R2 campaign is active in this final worktree. Per RW-296, P6 may proceed to a
provisional merge after exact-tip R0/R1 + R3, complete changed-line/branch
coverage, the required live probes, and fresh independent Sol ACCEPT. This
does not permit cgprofile 1.1.0 release, install, or `ciu up`; replacement P6
R2 and the registered full gate remain release blockers.

To make that boundary explicit, this checkpoint adds BRIEF-11, updates the P6
review handoff's round/ruling list and provisional-integration instructions,
and updates the P55 Sol review packet. These are review/process corrections,
not code changes. The exact short gates must be run after all checkpoint files
are committed, with the candidate HEAD quiet. Fresh Sol round 3 follows those
gates; if Sol commits any repair, resume that same live reviewer and rerun
affected gates on the repair tip.

P1's separate R2 campaign is outside this tree. At the last observation
(17:18:19Z), candidate 0/121 had been killed and its exact container was
running with a 3-CPU cap in loaded `dev-gates.slice`; the next observation is
not due before 17:43:19Z. No early check is warranted absent an error or
expected completion.

## Session 16 — 2026-09-25 — Sol xhigh final adversarial review, round 3

BLOCKED: The required own-image live daemon/carrier, placement and watch
probes cannot run within the review packet's host safety boundary. A bounded
read-only host-systemd probe reported `cgprofile.slice LoadState=loaded` but
empty `FragmentPath` and `ControlGroup`; that does not verify an installed,
active, bounded daemon parent, and Docker can silently auto-create an
unlimited transient slice. The reviewer did not launch the daemon under an
unverified slice or use forbidden host namespace modes. See
`cgprofile-P6-FOLLOWUPS-REVIEW-round3.md` for the exact probe, findings,
repairs, registered short-gate receipts, and remaining release evidence.

The reviewed implementation tip before this LOG/report-only commit is
`5ef6436051125f98c5f4ac65c7fa6d5d971bbfc7`. Its registered R0/R1
passed 1,662 tests with 100% line and branch coverage; R3 rejected 7/7
canaries. The old R2 at `aae66356` is not current-tree evidence. Current-tree
R2 and the full gate remain intentionally pending for provisional code review
and remain mandatory before release. No provisional merge or release is
approved by this BLOCKED verdict.

### 2026-09-26 18:12:40Z — exact-tree R2 triage

The replacement R2 on quiet tree `b3df5602` ended at
`2026-09-26T05:53:00.824604+00:00` with 312/312 candidates accounted for:
300 killed, 12 survived, zero equivalent/hung/crashed/budget-exceeded,
`FAIL/MUTANTS_SURVIVED`, exit 1. The survivor table and equivalence proofs
are in the REPORT. Two survivors are real oracle gaps: the private-PID
placement bridge's non-ESRCH guard (`placement.py:570`) and the fail-closed
PID identity exception (`serve.py:1296`). Focused regression tests were
added for both; no production behavior was changed. The new test tree must
be rejudged, and the b3df5602 receipt is diagnostic only.

### 2026-09-26 23:06:22Z — replacement R2 result on 6540f877

The detached run on quiet commit `6540f87761a66ff933c8bb45f81d8ac9117f407b`
ended at `2026-09-26T21:53:35.217876Z` with
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4. Its separate verdict and progress
stream account for all 312 candidates: 301 killed, 10 survived, one hung,
zero budget-exceeded, zero crashed. The ten survivors match the ten
contract-equivalent dispositions in REPORT Session 17; both real oracle gaps
from that run were killed by the two new regression tests.

The hung record is candidate
`0a38e7d8ab99ea0483119223cf9b8e38184e85124ca37f631ee264fed7a135d1`,
`lib/liveness.py:530` (`is not` → `is` in `_observe_io`), with 138.953 seconds
and 675 completed tests. The gate profile reports 135.1 seconds of
memory-full stall. No last pytest node or lower-level timeout reason was
preserved, so the cause remains unresolved; the profile correlation is not
causal proof. Keep the exact state record and treat the R2 as non-passing.
P6 must reconcile the P1 repair before its final gates and final R2.

## Session 19 — 2026-09-28 23:08:07Z — repair stop-time survivor restoration

Inspection of the private-PID systemd bridge found an unsafe asymmetry: lane
PIDs could be moved into the gates leaf through systemd, but `stop` still read
host PIDs from the daemon's namespace-local `cgroup.procs` and silently
ignored move-back failures. A stop could therefore claim release and remove
the leaf while a live process remained stranded or its location was unknown.

Commit `738bf1f5cb8ea52b751078052d52a551feee485e` repairs this path. With a
verified host-proc view it enumerates the leaf's host-visible tasks, rechecks
that each task still belongs to the leaf, tries the exact original
`cgroup.procs` first, and falls back only on `ESRCH` to
`AttachProcessesToUnit` for the nearest systemd unit/subgroup derived from the
absolute original cgroup path. It verifies the resulting host-proc membership
and records successful systemd-mediated cgroup writes. An unresolved task,
unsafe mapping, or failed move leaves the placement unreleased and leaf
intact, with the origin write failure exposed; it is not silently certified
as cleanup.

Pre-commit focused verification passed: `tests/test_serve_placement.py` —
**122 passed**. Branch-aware coverage of `lib/placement.py` reported **398/398
statements and 166/166 branches (100%/100%)**. A broader cockpit-venv suite
could not collect because optional `pandas` and `matplotlib` dependencies are
absent; this is not registered gate evidence. No registered gate, fresh R2,
daemon live probe, merge, or release was run in this session.

The branch is now at `738bf1f5`; its merge-base remains `4d32bcfe`, while
shared main has advanced to `87c13eff`. The stop-time systemd fallback needs
an exact-tree live probe before provisional integration: verify a surviving
host PID returns to its original systemd scope, the D-25 event is emitted,
and the leaf is removed only after membership verification; also verify the
fail-closed path. Reconcile current main, run exact-tip short gates and full
changed-area coverage, then use a fresh caller-configured GPT-6-Sol xhigh
review (new series starts at round 5). The latest prior R2 remains
`6540f877` `BUDGET_EXCEEDED/CANDIDATE_HUNG`; it does not qualify the updated
tree. Current-tree R2 and the registered full gate remain release blockers.

## Session 20 — 2026-09-28 23:38:31Z — reconcile current main and resume gates

The P6 branch now contains current main at
3a8bbe54068d46f34652b2ed52d19a7cddb53dd6 through merge
b53c5ffa1c55415a93f36beac63d320e478ebbfb. Conflict resolution retained
the P6 stop-time restoration behavior and main's controller rulings through
RW-362. The root and daemon-side RG-55 interface contracts compare
byte-identically. The prior focused tests and placement coverage predate this
reconciliation; P6 still needs exact-tip registered gates, changed-area
line+branch coverage, live probes, fresh Sol review, R2, and the full gate.

The separate B107 tester-unified gate started at 23:36:14Z from CIU worktree
.worktrees/rg55-b107-ciu-anchor-20260928/.worktrees/rg55-b107-full-gate-20260928
on exact tree 3a8bbe54. At the 23:38:31Z progress check (2m17s after
kickoff), container
run-gate-assay-selfhosted-3634378-21136-1790638575 had a verified 3-CPU
cap under dev-gates.slice; wheel-install and multiple verdict-schema
phases were progressing. The gate has not produced a final verdict. Prior
comparable tester-unified gates took about 19–21 minutes; next observation is
deferred to its expected completion window near 23:55–23:57Z.

### 2026-09-29 01:49:26Z — fail closed on direct PID migration errors

Reviewing the private-PID migration path found that `LanePlacement.migrate`
treated every non-`ESRCH` `cgroup.procs` write error as if the process had
vanished. An `EPERM` therefore skipped systemd fallback (correctly) but also
left `enforcement_failed` false; an empty leaf could be returned with
`placement.error=null` and `pids_moved=0`. That collapses a real refusal into
a successful placement status.

The path now marks non-`ESRCH` write failures as enforcement failures and logs
the PID/error. The existing `write-failed:<leaf>/cgroup.procs` result then
causes an empty leaf to be abandoned; `EPERM` is never routed through the
systemd namespace bridge. `test_non_esrch_write_failure_is_not_sent_to_systemd`
passed individually (1 passed), and the full `test_serve_placement.py` file
passed **122 tests** in 17.18s. This is local focused evidence only; exact-tip
registered short gates, changed-line and branch coverage, a live private-PID
placement/refusal probe, fresh Sol round 5, current-tree R2, and the full gate
remain outstanding. The separate B107 Assay tester-unified gate later passed
as recorded in controller RW-373; that receipt is not P6 evidence.

### 2026-09-29 01:58:10Z — exact-tip R0/R1 found one uncovered diagnostic branch

The registered `run-gate r0-r1` on exact tree
`e4241e39445ce1c074568b727f0b927a5e6103c7` completed in 140.991s with exit
2. The full suite passed **1,705 tests**, but the branch-aware judge failed
the 100% threshold: `lib/placement.py` was 401/402 statements and 167/168
branches, with line 599 missing (the new non-`ESRCH` diagnostic logger path).
The run-gate history record confirms this exact tree and exit. Profiling was
coarse rusage because `cgprofile-host-daemon` was down; the functional result
is the coverage failure, not a profiler outcome.

The regression now supplies a log sink and asserts the refusal message,
covering the diagnostic path without weakening the behavioral assertions.
The updated `test_serve_placement.py` file passed 122 tests in 17.00s. This
test change is committed at `e7bca65e`; main's RW-377 record is integrated at
`4af3d3c0`. The exact-tip registered R0/R1 and R3, changed-area line/branch
coverage, live probes, fresh Sol round 5, replacement R2, and full gate remain
outstanding.

## Session 24 — 2026-09-29 04:41:22Z — repair round-5 blockers

On candidate base `0eb2686c`, round 5 (`cgprofile-P6-FOLLOWUPS-REVIEW-
round5.md`) rejected six issues. The repair worktree now addresses them:

- B1: stall enforcement no longer signals host PID numbers. Shared-scope
  enforcement requires the caller's explicit successful placement leaf;
  container-scope enforcement writes only `cgroup.kill` on the exact runtime
  cgroup whose full ID matches beneath the verified bounded gates slice.
- B2: placement and `ctl host` presence require a loaded authored systemd
  slice at the expected cgroup path and positive finite memory/CPU capacity.
- B3: the four version-bumped frozen run-gate fixture copies now match the
  P6 source copies; the run-gate byte-identity oracle passed.
- B4: empty fields in a configured UID allowlist now fail closed.
- B5: fractional/non-finite caps and malformed typed wire fields now return
  `bad-argument` instead of truncating, throwing, or terminating the accept
  loop.
- B6: request lines have a size limit and one absolute 25-second deadline;
  a regression now includes a valid newline delivered after that deadline.

Additional defensive repair: if the exact container `cgroup.kill` write
succeeds but its subsequent audit-row sink raises, enforcement remains
reported as successful and the audit failure is logged; it is not converted
into a session-thread error.

Focused evidence so far: the latest placement/socket-carrier run passed 217
tests in 16.57s; a prior focused run passed 295 placement/watch/liveness tests;
another passed 355 access/serve/socket tests with 6 skips. The run-gate golden
byte-identity test passed (1 test). These are local test results only. The
root and daemon contract mirrors compare byte-identically. Final exact-tip
registered R0/R1 and R3, changed-area branch-aware coverage, current-tree R2,
full gate, live probes, measured DAMON overhead, and fresh Sol review remain
open. Current main at `038644c0` was reconciled in merge `9a472dec`; the
binding RW-379/RW-380 rulings are retained and the branch's colliding gate
receipt is now a non-ruling evidence note. No merge or release is authorized
by these local results.

## Session 25 — 2026-09-29 04:48:35Z — reconcile current main and check host gates

P6 repair commit `da066287` was merged with current main `038644c0` at
`9a472dec`. The only textual conflict was the controller log: the P6 branch
had used the number RW-379 for its earlier gate receipt, while main assigns
RW-379 and RW-380 to binding stall-kill policy. The gate receipt is preserved
as a plain P6 evidence note; main's two rulings remain authoritative. The
working tree is clean at the merge commit. The root and daemon contract
mirrors remain byte-identical.

Read-only host checks through `host-escape` verified `dev-gates.slice` is
loaded from `/etc/systemd/system/dev-gates.slice` at
`/dev.slice/dev-gates.slice`, with `CPUQuotaPerSecUSec=5s` (500%),
`MemoryHigh=1048576000`, and `MemoryMax=1610612736`. `cgprofile.slice` is
loaded from `/etc/systemd/system/cgprofile.slice` at `/cgprofile.slice`, with
`MemoryHigh=805306368` and `MemoryMax=1073741824`; its CPU quota is unlimited.
The host system bus socket exists. The host daemon unit probe returned
inactive, and no `cgprofile-host-daemon` container appeared in the cockpit's
`docker ps` listing; no existing service/container was started, stopped, or
internally inspected or changed. At the adjacent host sample, memory
`full avg10=0.00` and load
average was 3.05. An existing mutation container and unrelated running
containers were left untouched.

These facts remove the missing-loaded-slice blocker for a private-namespace
review probe, but do not themselves constitute a live daemon/carrier/kill or
DAMON-overhead result. Exact-tip short gates and 100% changed-area coverage
must run after this checkpoint commit; then perform the live probes, fresh Sol
review, current-tree R2, and full gate. No provisional merge or release yet.

## Session 26 — 2026-09-29 05:11:05Z — coverage repair after exact-tip R0/R1

The registered `r0-r1` on clean tree
`2d555fe12516fb14d5cd8104d28563f089322056` exited 2 after 108.44 seconds.
All **1,779 tests passed**, but branch-aware coverage was 99%: 6,351/6,375
statements covered and 2,233/2,252 branches fully covered (24 missed
statements and 19 partial branches). The run-gate history record independently
confirms the exact commit, clean tree, and exit. Misses were in
`lib/placement.py` lines 222, 224, 341–342, 836–841, and 882, plus partial
arcs at 477, 496, and 504; and in `lib/serve.py` lines 373, 375, 729–731,
738–740, 885–887, 1061, 1423, 1452, 1469–1470, 2001–2003, 2169–2173, and
2183, including a partial branch at 891–894.

The current repair adds focused behavioral cases for those paths and removes
an unreachable no-session audit-callback branch by defining that callback only
when a session directory exists. The fake placement fixture now creates the
`cgroup.kill` interface file before checking that an incomplete placement
leaves it unchanged. Local iteration of `test_serve_placement.py` and
`test_serve_socket_carrier.py` passed **236 tests in 21.82 seconds**. This is
not registered coverage evidence; final clean-tip `r0-r1` and `r3` remain
required after committing these documentation and test changes.

## Session 27 — 2026-09-29 05:18:09Z — close the last partial coverage branch

The registered `r0-r1` on clean tree
`d5fd17eb5073efd2b5c58515b2559e0b1b1ef9dd` exited 2 after 121.64 seconds.
All **1,798 tests passed** in 116.70 seconds and all **6,375 statements** were
covered. Branch coverage retained one partial arc: `lib/placement.py`
`836→841`, the incomplete-placement refusal when the optional logger is
absent. The independent history receipt confirms the tree, clean state, and
exit.

A regression now verifies that the logger-absent path returns refusal and
leaves the fake `cgroup.kill` value unchanged. Both logger-present and
logger-absent cases pass locally (**2 passed, 179 deselected**); this is not a
registered gate result. Re-run exact-tip `r0-r1` and `r3` after committing the
test and evidence update.

## Session 28 — 2026-09-29 05:36:02Z — short lanes green on f4872603

On clean exact tree `f487260365d61dfb807bf9cedcd57bbc313831ef`, registered
`r0-r1` passed: **1,799 tests**, four warnings, **6,375/6,375 statements and
2,252/2,252 branches**. Pytest took 110.22s; the separate run-gate history
receipt records exit 0 and 115.22s total duration. Its test container
`cgprofile-gate-79262-1790659181` was capped at 3 CPUs under `dev-gates.slice`.

On the same clean tree, registered `r3` passed in 10.393s; all seven
canaries were rejected and none survived. Its container
`run-gate-vbpub-r3-82699-1790659370` was placed under `dev-gates.slice` with
three CPUs. The daemon was down: `r0-r1` used coarse rusage and `r3` basic
in-lane sampling, not DAMON. These receipts predate this session's review-
handoff documentation correction, so repeat both short lanes on the final
handoff/evidence tip. Required live probes and fresh Sol round 6 remain open.

## Round-6 review — 2026-09-29 — mechanical host-unit block

BLOCKED: The private-PID daemon's required live placement, stop restoration,
and shared-scope watch kill cannot be accepted on this host because the
authored `dev-gates.slice` reports `Delegate=no`; the host system manager
refuses `AttachProcessesToUnit` with `Process migration not available on
non-delegated units.` The required host-unit remedy belongs to
`modern-debian-tools-python-debug/`, which this review is forbidden to edit.
The reviewer did not alter the host unit, use a host namespace, or launch a
mutation campaign. See `cgprofile-P6-FOLLOWUPS-REVIEW-round6.md` for the
code-side verifier and absolute-subpath repairs, targeted tests, exact
owned-container cleanup, and evidence limits. The P1 mutation container
`run-gate-vbpub-mutation-3747550-1790644029` was left untouched.

The first post-repair registered `r0-r1` on clean tree `bfcf6936`
exited 2 despite 1,811 passing tests: `lib/serve.py:2189-2192` was
uncovered (6,393 total statements, 2 missed; 2,258 branches fully
covered). The exact gate container `cgprofile-gate-136173-1790662077`
read back `NanoCpus=3000000000`, parent `dev-gates.slice`; launch
memory `full avg10=0.00`. A deterministic partial-request timeout
oracle was added and passed locally. The registered lanes must be
rerun on the resulting committed tree; the host-unit block remains.

On clean `e48d1d3f`, registered `r0-r1` exited 0: 1,812 tests,
6,393/6,393 statements, 2,258/2,258 branches. Registered `r3` exited
0 with 7/7 canaries rejected. The independent history rows match that
commit and clean state. Both gates used the normal R-36h fallback because
the main daemon is down. The artifact update following these receipts
changes HEAD, so repeat both short lanes on its exact final tip.

## Session 32 — 2026-10-01 01:03:50Z — current host-unit preflight supplied by operator

The operator supplied the result of this read-only query run directly on the
host: `systemctl show dev-interactive.slice dev-gates.slice cgprofile.slice --property=LoadState,ControlGroup,Delegate,CPUQuotaPerSecUSec,MemoryMax --no-pager`.
The current values are:

* `dev-interactive.slice`: loaded at `/dev.slice/dev-interactive.slice`,
  `Delegate=no`, `CPUQuotaPerSecUSec=5s`, `MemoryMax=8589934592`.
* `dev-gates.slice`: loaded at `/dev.slice/dev-gates.slice`, `Delegate=no`,
  `CPUQuotaPerSecUSec=5s`, `MemoryMax=1610612736`.
* `cgprofile.slice`: loaded at `/cgprofile.slice`, `Delegate=no`,
  `CPUQuotaPerSecUSec=infinity`, `MemoryMax=1073741824`.

This is useful current parent-unit evidence, but does not prove the D-31
transient delegated scope can be created beneath `dev-gates.slice`, that its
`Delegate=yes` and exact parent are read back, or that stop restores every
process before systemd retires it. Those remain reviewer-owned live probes.
No host mutation, `host-escape`, or namespace join was performed. The same
values are recorded in controller ruling RW-398.

## Session 33 — 2026-10-01 01:12:45Z — focused P6 placement and socket regressions

At exact worktree tip `298e4157e9a5cb96fefea6d976dbf50691fb008d`, the serial,
load-niced local command `pytest -q tests/test_serve_placement.py
tests/test_serve_socket_carrier.py` passed: **406 passed in 33.16 s**. It
covers the round-7 scope-retirement/cap-readback fixes and unterminated socket
request fix. This is targeted cockpit evidence only; it does not replace the
registered exact-tip `r0-r1`, `r3`, doctor, or required live scope/restore
probe. Those gates remain pending, so no review fix-verification or merge is
claimed.

## Session 34 — 2026-10-01 02:49:33Z — rerun focused P6 blocker regressions

On exact committed tip `28345cecb440fa8e6dc566ec98f0f44ad8e091bc`,
`nice -n 19 ionice -c 3 python3 -m pytest tests/test_serve_placement.py
tests/test_serve_socket_carrier.py -q` passed serially: **406 passed in
32.29 s**. This confirms the focused B1–B3 regressions on the current P6
checkout, but not the registered package gates or reviewer-owned live
delegated-scope restoration probe. R0/R1, R3, doctor, exact-tree mutation,
and full-gate evidence remain outstanding.

## Session 35 — 2026-10-01 03:10:51Z — isolate the registry-push target (CP-14)

A release-path audit found that `docker-bake.hcl` put the local
`cgprofile:local` alias and the versioned GHCR image on one target, while
`build-push.py --push` selected the `all` group with `--push`. A read-only
`docker buildx bake --print all` confirmed both references were emitted;
Docker's unqualified-name rules resolve the local alias to Docker Hub. Filed
CP-14 before the repair. The Bake definition now has separate
`cgprofile-local` and `cgprofile-release` targets: the normal `all` group is
local-only, and publication selects only the versioned GHCR target. CP-14 is
marked fixed.

Oracles on this worktree: `docker buildx bake --print cgprofile-release`
resolved exactly `ghcr.io/volkb79-2/cgprofile:1.0.0`; `docker buildx bake
--print all` resolved exactly `cgprofile:local`; focused
`tests/test_build_push.py` passed **3 tests** in 0.27 s. This does not replace
the registered R0/R1, R3, reviewer live probes, exact-tree R2, or full gate;
the current tree remains unjudged by those lanes.

## Session 36 — 2026-10-01 03:13:31Z — record existing nyxloom assert-vocabulary block

`nyxloom lint` on this project exits 1 because
`nyxloom-trove/nyxloom.toml:[gates.coverage].asserts` declares
`coverage-floor`, which the current nyxloom schema does not yet accept. The
global backlog already tracks this as `nyxloom/nyxloom-trove/backlog/NL-25`
(truthful whole-project coverage-floor vocabulary). Replacing it with
`changed-line-coverage` would misstate this package's whole-project 100%
line-and-branch gate, so no local approximation was made. The one lint finding
is recorded as an existing cross-tool limitation, separate from CP-14; no
nyxloom source or backlog files were changed here.

## Session 37 — 2026-10-01 04:35:27Z — restore P6 mutation-oracle regressions

Reviewing the P6 campaign history against the current `8723f3064` checkout
showed that the two regression tests committed in `6540f8776` are absent from
the current test files: the non-ESRCH write-failure/systemd-bridge oracle and
the unreadable-PID-identity refusal oracle. Restored both in this checkpoint.
This is a test-only repair; no product behavior changed. The focused tests have
not yet been run on this checkpoint, and no current-tree R2 result is claimed.

The `6540f8776` R2 campaign reported in Session 18 is
`BUDGET_EXCEEDED/CANDIDATE_HUNG` (312 candidates accounted for, 301 killed,
10 survived, 1 hung); it is not a passing result and is stale for this tree.
The earlier `b3df5602` survivor table in Session 17 is diagnostic only. Run
focused tests after the serialized P1 review probes finish, then run all
required P6 gates and a fresh R2 on one quiet exact tree. Do not reuse either
historical receipt.
