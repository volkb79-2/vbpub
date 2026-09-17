# P91 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Port-forward provenance (2026-09-08)

This package is a **port-forward**, not a fresh implementation. The design,
scope, and oracles below are unchanged from the original 2026-07-15 delivery
on `feat/groop-P91-persistent-capped-history`; only paths, module names, and
two files' surrounding context changed.

- **Original branch:** `feat/groop-P91-persistent-capped-history` @ `b720f4d9`
  ("groop-P91: persistent capped history (controller-rescued; false-limit
  misclassification corrected)"), dated 2026-07-15.
- **Original independent review:** `939b839d` ("review(groop-P91): APPROVE —
  independent merge-gate review"), same date. Verdict **APPROVE**, no code
  changes required; the reviewer independently re-ran both declared gates
  (`groop-suite`: 1697 passed, zero skips; `py-compile`: clean) from a fresh
  venv against the branch head, and adversarially checked all ten oracles
  against the tests for hollowness. Full text ported forward as
  `P91-REVIEW.md` in this directory.
- **Why it never merged:** the groop→topos rename (`2860d3f5`,
  "refactor: rename groop -> topos (deep, vbpub)") landed the next day,
  2026-07-16, while the branch was still on the pre-rename `groop/` path
  layout. It was never rebased or ported, and sat orphaned for ~8 weeks.
- **Verified before porting:** the current handoff spec
  (`topos/nyxloom-trove/handoffs/topos-P91-persistent-capped-history.md`, on
  main today) is byte-identical in substance to the branch's original spec
  (`groop/handoff/groop-P91-persistent-capped-history.md`) — only the id,
  project, touch paths, gate name (`groop-suite`→`topos-suite`), and
  worktree/branch naming changed mechanically. Design/scope/oracles
  unchanged. This port therefore carries forward the original review's
  approval rather than seeking a fresh design review; a fresh **adversarial
  review of the port itself** (in particular the two hand-reconciled files
  below) is the next step and has not happened yet — this branch is not
  merged.
- **New branch:** `feat/topos-P91-persistent-capped-history`, worktree
  `/workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history`,
  created off `main` @ `3dd08b12`.

### Merge-tree analysis (before porting)

`git merge-tree --write-tree main feat/groop-P91-persistent-capped-history`
(tree `3ae7e0e9`) partitioned the branch's `main...HEAD` diff (13 files,
+2276/−14) into three classes:

1. **Pure path-rename conflicts** (`CONFLICT (file location)`, content
   untouched by main): `groop/handoff/reports/P91-{LOG,REPORT,REVIEW}.md`,
   `P91-measure-history{,-scaled}.py` → `topos/nyxloom-trove/reports/...`;
   `groop/src/groop/daemon/persist.py` →
   `topos/src/topos/daemon/persist.py`; `groop/tests/test_persist_history.py`
   → `topos/tests/test_persist_history.py`. Recreated at the new path with
   `groop`→`topos` substituted throughout (module names, imports,
   docstrings, path literals — see "New-file substitution" below).
2. **Cleanly auto-merged** (git's line-based 3-way merge applied with no
   conflict, confirmed by diffing a `groop`→`topos`-renamed copy of the
   merge-base against main's current file and finding **zero** drift beyond
   the mechanical rename): `topos/MEASUREMENTS.md`, `topos/docs/DAEMON.md`,
   `topos/src/topos/config.py`, `topos/src/topos/query/__init__.py`,
   `topos/src/topos/query/source.py`. Pulled directly from tree `3ae7e0e9`,
   then grepped for leftover `groop` tokens (git's merge doesn't rename
   *contents*, only applies the diff textually — a few string literals and
   doc-prose module references the branch's diff introduced still said
   `groop`; see below).
3. **Real content conflicts** (`CONFLICT (content)`) requiring manual
   reconciliation: `topos/src/topos/cli.py` and
   `topos/src/topos/daemon/__init__.py`. Detail below.

### Leftover-`groop` cleanup in the auto-merged files

Grepping `MEASUREMENTS.md`, `docs/DAEMON.md`, and `config.py` post-merge
found four leftover mentions git's textual merge had carried forward
verbatim (it merges diffs, it does not rename identifiers inside them):

- `config.py`: `PersistConfig.dir` default was `Path("/var/lib/groop/history")`
  → fixed to `/var/lib/topos/history`.
- `docs/DAEMON.md`: two narrative module references,
  `` `groop.daemon.persist.PersistentHistoryStore` `` and `` `groop.query` ``
  → `topos.daemon.persist.PersistentHistoryStore` / `topos.query`; also
  corrected the report cross-reference `handoff/reports/P91-REPORT.md` →
  `nyxloom-trove/reports/P91-REPORT.md` (the file's real location post-rename).
- `MEASUREMENTS.md`: the O10 section's repro command block said `cd groop` /
  `handoff/reports/P91-measure-history*.py` → `cd topos` /
  `nyxloom-trove/reports/P91-measure-history*.py`. Added a one-paragraph note
  at the top of that section stating the recorded numbers are the original
  2026-07-15 measurement carried forward unchanged (not re-run), since the
  store's design/defaults/workload are byte-identical across the rename.

### New-file substitution (`persist.py`, `test_persist_history.py`, report artifacts)

Word-boundary `groop`→`topos` / `Groop`→`Topos` / `GROOP`→`TOPOS` substitution
across all seven newly-added files, plus two path fixups not caught by that
regex: the compound identifier `GroopConfig`→`ToposConfig` (test file, three
call sites — the regex's word boundary sits *inside* the identifier, between
`Groop` and `Config`, both being word characters, so the plain regex missed
it), and `handoff/<name>.md` / `handoff/reports/` path references throughout
the ported `.md`/`.py` files → `nyxloom-trove/handoffs/<name>.md` /
`nyxloom-trove/reports/` (the directory itself moved in the same rename,
distinct from the groop→topos token). Verified zero remaining
case-insensitive `groop` matches in every ported file after the fixups.

`persist.py`'s only substantive diff from the original branch blob is its two
import lines (`from groop.config/model import ...` →
`from topos.config/model import ...`); `test_persist_history.py`'s diff is
purely import lines, the `/var/lib/groop/history` config-round-trip literal,
`GroopConfig`, and the docstring's handoff-spec path — no test logic or
assertion changed.

`P91-REPORT.md` and `P91-REVIEW.md` below are the **original 2026-07-15
documents**, `groop`→`topos` substituted for consistency with the current
tree (including branch-name and worktree-path mentions, which — after
substitution — now correctly read as *this* branch's name/path). Where that
substitution would otherwise misrepresent history (e.g. a quoted parent-commit
message that literally said `docs(groop): ...`), no attempt was made to
un-substitute it back to a mixed groop/topos state; the quoted string is
cosmetically normalized like everything else in these two documents, and this
provenance section is the authoritative record of what was literally true at
the time (branch name, commit hashes, gate name).

### Real content conflict 1 — `topos/src/topos/cli.py`

Two independent things collided at nearly the same anchor line, `main`
diverged from what P91 needed to attach to:

- **Mechanical (not a real conflict):** the branch's diff still said
  `from groop.daemon import (...)` / `from groop.model import
  frame_to_jsonable`; main's current file already reads `from topos.daemon
  import (...)` / `from topos.model import frame_to_jsonable` at the same
  position. Comparing un-renamed branch content against already-renamed main
  content is why git flagged the whole file, not evidence of independent
  edits to the import block. Resolved by adding `PersistentHistoryStore` to
  the `topos.daemon` import tuple and `Frame` to the `topos.model` import
  (`Iterator` was already imported on main; P91 needs both).
- **Real (independent edits, adjacent lines):** confirmed by diffing a
  `groop`→`topos`-renamed copy of the merge-base's `cli.py` against main's
  current `cli.py` — main's *only* change anywhere near the `serve` command's
  shutdown path (the rest of the drift is unrelated `--json`/`--table`
  report/query output-format work, `PaddrLifecycleOutcome.DISABLED` dead-code
  removal, and the groop→topos import renames) is a bare `return 0` added
  **immediately after** `server.server_close()` inside `if args.command ==
  "serve":`. P91's branch independently inserted its persistent-history
  flush/close block **immediately before** that same `server.server_close()`
  call. The two hunks sit one line apart around a shared anchor, so git's
  merge could not place them independently and flagged content conflict.
  **Resolution:** applied both, in the branch's original relative order —
  the persist-flush block goes first, `server.server_close()` unchanged,
  main's `return 0` unchanged immediately after it:

  ```python
  if collector_stopped:
      health_registry.mark_stopped("collector", detail="collector stopped")
  if config.history.daemon.enabled:
      health_registry.mark_stopping("persistent_history", detail="flushing persistent history")
      history_store.close()
      health_registry.mark_stopped("persistent_history", detail="persistent history flushed")
  server.server_close()
  return 0
  ```

  Also re-applied, at their original relative positions (both unaffected by
  main's drift): the `history_store = PersistentHistoryStore(config.history.daemon)`
  construction + component-health startup wiring (`record_degraded` /
  `record_success` / `mark_disabled`) placed right after
  `health_registry.mark_starting("collector", ...)`, before `frame_stop =
  threading.Event()`; and the `frame_stream` tee (`_persisting_frame_stream`)
  feeding `FrameBroker` instead of `live_frame_stream(...)` directly, plus the
  new `_persisting_frame_stream` generator function itself, inserted verbatim
  before `_main_daemon`.
- Verified `history_store`/`config` are in scope wherever the cleanup runs:
  the `try/finally` wrapping `broker.start(); server.serve_forever()` opens at
  line 1868, well after `history_store` is assigned at line 1629 — no path
  reaches the `finally` block without having created the store first (same
  structure the original branch relied on).

### Real content conflict 2 — `topos/src/topos/daemon/__init__.py`

Diffing a `groop`→`topos`-renamed copy of the merge-base's
`daemon/__init__.py` against main's current file found **zero** drift —
main has not touched this file at all since the fork beyond the mechanical
rename. The "content conflict" here was entirely the same mechanical
false-positive as cli.py's import block: the branch's diff still said
`from groop.daemon.persist import (...)`. Resolution was a clean cherry-pick
of the branch's own diff, module-name-substituted, at the identical relative
position (the `persist` import block inserted between the existing
`paddr_lifecycle` and `component_health` imports; five `__all__` entries —
`GapRange`, `PersistentHistoryStore`, `PersistStoreError`, `SegmentInfo`,
`StoreStats` — inserted at the same alphabetical slots the branch used,
which still validate against the current, unchanged `__all__` list). Net
diff: **+12/−0**, matching the original branch's `daemon/__init__.py | 12 +`
stat exactly.

### Closing the topos-suite diff-coverage gate (new since the original approval)

`topos-suite`'s declared argv includes `topos/tools/coverage_gate.py --base
main`, a 100%-floor diff-coverage check on changed executable lines. This
was bootstrapped by `topos-P96` ("feat(topos-P96): bootstrap max-standard
branch-coverage gate") — chronologically AFTER P91's 2026-07-15 approval
under the old `groop-suite` gate, which had no such check. The original
review's "1697 passed, zero skips" and the branch's own "31 tests, every
oracle O1-O9" were both true and sufficient for THAT gate; they were never
going to satisfy a floor that did not exist yet.

First gate run (commit `ecd671fe`, the pure port before any coverage work):
2953 passed, zero skips, but diff-coverage **82.7%** (440/532 changed
lines). Closing that gap took three more rounds and surfaced one real
implementation bug, not just missing tests:

1. **Bug: `persistent_history` was never a registered component.**
   `daemon/component_health.py`'s `COMPONENT_NAMES` tuple (owned by P47;
   neither the original P91 branch nor this port's cli.py/daemon/__init__.py
   reconciliation touched this file) has always been `("collector",
   "bpf_snapshot_bridge", "paddr_lifecycle")`. `ComponentHealthRegistry`
   pre-populates `self._records` from `COMPONENT_NAMES` at construction, and
   `_update()` silently no-ops (`if record is None: return`) for any name
   not in that dict; `snapshot()` only iterates `COMPONENT_NAMES` too. Every
   `record_success`/`record_degraded`/`mark_disabled`/`mark_stopping`/
   `mark_stopped("persistent_history", ...)` call the P91 wiring makes —
   in the **original 2026-07-15 groop branch too**, since this file was
   last touched by P47 and P91 never modified it — was therefore a no-op:
   the health reporting silently never worked. The original review's
   adversarial check ("Confirmed every ComponentHealthRegistry method the
   new daemon serve wiring calls ... exists") verified the methods don't
   crash, but not that the component name they're called with is actually
   registered — a real, if narrow, gap in that review's own adversarial
   coverage. **Fix:** added `"persistent_history"` to `COMPONENT_NAMES`
   (`component_health.py`, commit `c5bd5a1a`). Every other consumer reads
   this tuple dynamically (client.py's wire-protocol decode zips against
   `len(COMPONENT_NAMES)`; the component-health tests iterate it), so the
   only fallout was five test files with hand-built 3-component health
   fixtures or hardcoded `== 3` counts that needed a fourth entry:
   `test_daemon_client_protocol_boundaries.py`,
   `test_daemon_client_final_boundaries.py`,
   `test_last_coverage_boundaries.py`, `test_misc_long_tail_boundaries.py`,
   `test_daemon_component_health.py` (the last one also needed a fourth
   worker thread added to its concurrent-update test, since that test
   asserts every registered component reaches HEALTHY/FAILED by the end —
   `persistent_history` would otherwise sit at its untouched DISABLED
   default and fail that assertion). All fixed; every one of these files'
   own tests still pass (verified locally, see below).
2. **Real test gaps in `persist.py`'s recovery/error-handling code**, added
   across three commits (`c5bd5a1a`, `301c7909`, `c9d70520`): three new
   `topos daemon serve` CLI-level tests (healthy/degraded/disabled startup
   reporting + shutdown flush, driving `_main_daemon` through one
   `server.serve_forever()` iteration the same way
   `test_cli_daemon_lifecycle_boundaries.py` does); `PersistConfig`'s
   `segment_frames`/`checkpoint_frames` validation; store-construction
   `TypeError` guard; `store.close()`; `iter_segments()`;
   `read_frames(since_ts=, until_ts=)`'s windowing (real public surface
   `PersistentHistoryFrameSource.from_store` already exposes, previously
   never exercised with non-default values); a post-recovery
   corrupted-segment read skip (the exact narrow race the original REPORT's
   "Deviations" section already documented but never tested); orphan-segment
   re-parse recovery (the index-write-lost-mid-crash path, also
   REPORT-documented but untested) including direct unit tests of
   `_inspect_segment_file`'s five structural-validation branches (blank
   line, missing header, segment_id mismatch, wrong record type,
   header-only/zero-frame segment) and `read_segment_frames`'s sibling
   blank-line skip; quarantine filename-collision and rename-failure
   handling; `_load_index_cache`'s wrong-schema-version and
   non-dict-segment-entry guards; `_validate_segment`'s
   becomes-unreadable-mid-scan OSError path; a flush()-time disk failure
   distinct from O7's existing publish-time case; an append()-time
   mid-write failure that must abort the still-open active writer
   (distinct from O7's publish-time case, where `_active` is already
   cleared before the failure); `_SegmentWriter.abort()`'s
   close()-failure guard; `_evict_locked(publish_index=True)`, which is
   never invoked with `True` by either of the two current internal call
   sites (both always pass `False` and republish the index themselves
   unconditionally right after) but is directly callable and worth proving
   works; and a strengthened zstd round-trip test with enough frames in one
   active segment to force the compressor to emit output before
   `finalize()`'s own flush (the original 5-frame/2-per-segment test never
   accumulated enough data to do so).
3. **Two lines marked genuinely unreachable**, each with an inline comment
   explaining why, not just a bare pragma: `flush()`'s `if self._active is
   not None:` guard inside its `except OSError` handler — traced to
   `_publish_active_locked()` always clearing `self._active` to `None` as
   its first action, before any fallible I/O, on every call path into it
   (both from `_append_locked` and from `flush()` itself, both always under
   `self._lock`), so the guard can never observe a non-`None` value there;
   contrast with `append()`'s own near-identical guard, which **is**
   reachable (a failure can occur while `self._active` is still live,
   inside `write_frame`/`checkpoint`, before any publish is attempted) and
   now has a dedicated test. And `read_segment_frames`'s final bare
   `raise` in its zstd-error fallback (some exception that is neither one
   of the explicitly-caught types nor zstd's own error class) — mirrors the
   pre-existing pragma already on `_inspect_segment_file`'s identical
   fallback, present in the original approved code, unmodified by this
   port.

Progression: 82.7% (440/532) → 97.3% (514/528, after the COMPONENT_NAMES fix
and the first wiring/store-internals test batch) → 99.4% (525/528, after the
orphan-segment/`_inspect_segment_file`/index-cache/mid-scan tests) →
**100.0% (528/528)**, confirmed by a clean dockerized `run-gate.py
topos-suite --worktree` run against commit `c9d70520` (2986 passed, zero
skips, 78.84s; `diff-coverage OK: 528/528 changed executable lines covered
(100.0% >= 100.0% floor)`) — see `P91-REPORT.md`'s "Gate results
(port-forward)" section for the verbatim final output.

Host-side full-suite comparisons (no docker; this environment's host venv
lacks the `zstandard`/`mcp` `[dev]` extras, so every one of these runs is a
fast pre-commit sanity check, never treated as the gate) were run
side-by-side against unmodified `main` in the identical host environment
after every commit in this sequence, specifically to catch any regression
the `COMPONENT_NAMES` change might cause elsewhere: the failure set was
**identical by name** on both trees every time (`test_acceptance.py`'s
MCP-dependent smoke tests, `test_p98_record_coverage.py`'s zstd-dependent
tests, and one stale-installed-package-version assertion in
`test_record.py` — all pre-existing host-environment gaps, none touching
`component_health.py`, `persist.py`, or `cli.py`), while the worktree's
passing count grew commit-over-commit as new tests landed (2825 → 2854 →
final). No regression was found at any point.

### Port-forward validation

```bash
cd /workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history
python3 -c "import ast; ast.parse(open('topos/src/topos/cli.py').read())"                    # OK
python3 -c "import ast; ast.parse(open('topos/src/topos/daemon/__init__.py').read())"          # OK
PYTHONPATH=topos/src:topos python3 -c "import topos.cli, topos.daemon, topos.query"            # OK

# Host smoke run (no docker; host venv lacks the zstandard/mcp [dev] extras,
# so this is NOT the gate, only a fast pre-commit sanity check):
PYTHONPATH=topos/src:topos python3 -m pytest topos/tests/test_persist_history.py -q
# 30 passed, 1 skipped (zstandard not installed on host) in 1.09s

PYTHONPATH=topos/src:topos python3 -m pytest topos/tests -q -k "cli or daemon or config or query"
# worktree: 5 failed, 973 passed, 8 skipped
# main (same host env, unmodified, for comparison): 5 failed, 963 passed, 8 skipped
# The 5 failures are IDENTICAL by name on both trees (4x test_acceptance.py
# MCP smoke tests needing the `mcp` extra not installed on this host, 1x
# test_record.py version-string check against a stale installed package
# version) -- pre-existing host-environment gaps, not a regression. +10
# passing tests on the worktree vs main matches the new persist-history file
# (this keyword filter does not match all 31 new tests, only a subset whose
# names contain "config"/"daemon"/"cli"/"query").
```

Actual gate results (dockerized `tester-unified` environment, via
`topos/run-gate.py`) are recorded in `P91-REPORT.md`'s "Gate results
(port-forward)" section below, after the port-forward addendum.

## Blockers

None. No `BLOCKED` trigger fired during the port.

## Handoff Checklist

- [x] Report file written (port-forward addendum + original REPORT ported).
- [x] Log file current.
- [x] Tests/compile/smoke recorded: both declared gates (`topos-suite`,
      `py-compile`) green from a clean dockerized `run-gate.py` run against
      the final commit — see `P91-REPORT.md`'s "Gate results (port-forward)".
- [x] Known gaps documented (see original REPORT's "Known gaps / follow-ups",
      unchanged by the port).
- [x] Feature branch committed.
- [ ] Independent adversarial review — not yet done; this branch is not
      merged. Four things specifically need a second look, beyond the usual
      re-verification of the ported O1-O10 tests: (1) the cli.py/
      daemon/__init__.py reconciliation itself (both "Real content conflict"
      subsections above); (2) the `component_health.py` `COMPONENT_NAMES`
      fix and whether the five test-fixture edits it forced are faithful to
      each test's original intent, not just numerically patched; (3) the
      two `# pragma: no cover` exclusions' unreachability reasoning; (4)
      whether the ~35 new tests added to close the diff-coverage floor are
      substantive (the same hollowness check the original P91-REVIEW.md
      applied to the first 31 — see "Adversarial probes" in that document
      for the standard).

---

# Original implementation log (2026-07-15, ported from the `groop/` tree; paths and module names renamed `groop`→`topos` for consistency — see the provenance section above for what that substitution touched)

## Context

- Branch: feat/topos-P91-persistent-capped-history
- Worktree: /workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history
- Base commit: 133cd16 (this branch's parent; `docs(topos): P64 baseline comparison LOG/REPORT`)
- Package: P91 recoverable age-and-byte-capped daemon history
- Current objective: D-005's persistent tier — segment/index store, atomic
  publication, corruption-safe recovery, disk-full degradation, P88 query
  integration, O10 resource measurement.

## Timeline

```text
2026-07-15
- Action: Read the handoff (contracts, oracles, escalate-if), docs/ROADMAP.md,
  docs/DECISIONS-INBOX.md D-005/D-008, docs/DAEMON.md Retention, TUI-SPEC.md
  §3.5/§9 (accepted [history]/[history.daemon] config schema), P88 report
  (query/source.py FrameSource boundary), existing HistoryConfig/broker.py
  (RAM tier is a plain deque, decoupled from HistoryConfig), record/writer.py
  + reader.py (existing JSON-lines + zstd streaming pattern to model segments
  on).
- Result: No BLOCKED trigger. Design: segments are self-contained JSON-lines
  (+ optional zstd) files, written to a temp path, fsynced, then
  os.replace()'d into segments/ — atomic by construction, so a crash mid-write
  can never leave a torn segment visible. index.json republished the same
  way. Recovery always re-hashes every segment against its last recorded
  checksum (bounded by the byte cap, not by uptime) rather than trusting
  content parseability alone — a corrupted-but-still-parseable segment must
  not be silently accepted. Gaps are computed structurally from the retained
  segment-id sequence (no separate persisted gap state needed). No rollup/
  downsampling: canonical frames are persisted once (Contract 2 forbids a
  second aggregation engine); D-005's "older rollups" language is a
  provisional recommendation, not the accepted decision text.
- Follow-up: implement config.py PersistConfig + HistoryConfig reconciliation,
  daemon/persist.py store, query/source.py adapter, cli.py daemon-serve
  wiring, tests, O10 measurement, docs.
```

```text
2026-07-15 (later)
- Action: implemented `PersistConfig` (config.py) — nested under
  `HistoryConfig.daemon`, matching TUI-SPEC.md's already-recorded
  `[history.daemon]` schema (max_size_mb, max_age_days, compression) plus
  segment_frames/checkpoint_frames/fsync/dir_mode/file_mode. Reconciled
  `full_resolution_seconds` default 14400 -> 300 and `downsample_retention_hours`
  4 -> 24 per Contract 1 and TUI-SPEC's already-accepted D-005 defaults.
- Files changed: src/topos/config.py
- Result: `python3 -c "from topos.config import load; print(load(None).history)"`
  confirms new defaults; digest() covers the new section.
- Follow-up: store implementation.
```

```text
2026-07-15 (later)
- Action: implemented `topos.daemon.persist` (PersistentHistoryStore,
  SegmentInfo, GapRange, StoreStats, PersistStoreError). Manually exercised
  append/flush/restart-recovery/byte-cap-eviction/torn-write/disk-full/
  corrupt-middle-segment scenarios via ad hoc scripts before writing formal
  tests, to shake out design bugs cheaply.
- Result: found and fixed a real correctness bug — a segment whose bytes were
  corrupted post-hoc (index has a recorded checksum, actual bytes now differ)
  was falling through to a content-reparse path that can still succeed if the
  corruption happens to leave syntactically valid JSON (e.g. mangling the
  "schema_version" key name without touching "type"/"segment_id"). Fixed:
  a checksum mismatch against a *recorded* index entry is now always treated
  as corruption (quarantined), never re-validated by content parse. Content
  parse is reserved for orphan segments with no recorded checksum at all (the
  index-write-lost-mid-crash recovery path).
- Files changed: src/topos/daemon/persist.py
- Follow-up: P88 query integration, formal tests.
```

```text
2026-07-15 (later)
- Action: added `PersistentHistoryFrameSource` to `topos/query/source.py`
  (+ `__init__.py` export), consuming `PersistentHistoryStore.read_frames()`
  directly — no second recovery/corruption-handling path, the store is the
  sole source of truth for what's retained/evicted/gapped. Verified against
  `run_query` manually (gap/eviction/coverage meta) before formalizing tests.
- Files changed: src/topos/query/source.py, src/topos/query/__init__.py
- Result: gap and eviction metadata propagate correctly through P88's
  existing `run_query` with no changes to engine.py/semantics.py.
- Follow-up: daemon-serve wiring.
```

```text
2026-07-15 (later)
- Action: wired the store into `cli.py _main_daemon serve` — creates the
  store from `config.history.daemon`, tees the live frame stream to it via a
  new `_persisting_frame_stream` generator (so the RAM tier and disk tier see
  exactly the same canonical frames in the same order, Contract 2), records
  component health (`persistent_history`: disabled/degraded/healthy), and
  flushes+closes the store in the existing shutdown `finally` block.
- Files changed: src/topos/cli.py, src/topos/daemon/__init__.py
- Result: `python3 -c "import topos.cli"` clean; integration test proves the
  RAM tier (FrameBroker) and disk tier receive the identical frame sequence.
- Follow-up: full oracle-driven test suite.
```

```text
2026-07-15 (later)
- Action: wrote `tests/test_persist_history.py` — 31 tests covering O1-O9
  explicitly (age cap, byte cap, both-simultaneously x2, restart recovery x2,
  torn segment write, torn index write, corrupt middle segment x2, disk-full
  x3, query gap/eviction truth x2, byte-deterministic recovery x2 incl.
  compressed), PersistConfig validation, config TOML round-trip, file
  permissions, and the RAM/disk dual-feed integration test.
- Commands:
  /tmp/handoffctl-gate-manual/bin/python -m pytest tests/test_persist_history.py -q -> 31 passed
- Result: all pass, including the corruption-detection fix from the earlier
  manual exploration.
- Follow-up: O10 measurement, full-suite gate.
```

```text
2026-07-15 (later)
- Action: added lifetime write counters to the store (lifetime_bytes_written,
  lifetime_raw_bytes_written, lifetime_frames_written,
  lifetime_index_bytes_written) for O10/D-005's "write rate" reporting
  requirement. Wrote nyxloom-trove/reports/P91-measure-history.py (fixture scale,
  ~42.7 KB/frame) and P91-measure-history-scaled.py (D-005 production scale,
  ~447 KiB/frame, entities replicated to hit that target) — synthetic 24h
  workloads (17280 appends @ 5s interval) with small per-metric jitter
  (fixed-seed `random.Random`) so frames aren't byte-identical (which would
  give an unrealistically high compression ratio).
- Commands:
  python3 nyxloom-trove/reports/P91-measure-history.py         -> 22s wall, 18.5 MiB on disk, 35.5x compression
  python3 nyxloom-trove/reports/P91-measure-history-scaled.py  -> 200s wall, 161.2 MiB on disk, 43.9x compression
- Result: both scales stay within the 256 MiB byte cap with zero eviction
  needed for a full 24h window; CPU stays under 0.25% of one core averaged
  over a real day at either scale; max RSS 28.8-30.5 MB (burst-mode ceiling).
  Recorded in MEASUREMENTS.md. Decision: keep `PersistConfig.enabled=False`
  by default in this delivery regardless of the favorable measurement —
  flipping a disk-write-by-default daemon behavior is treated as a distinct
  product decision, not one this measurement alone authorizes (see REPORT
  "Deviations / decisions").
- Follow-up: docs (DAEMON.md), full-suite gate, LOG/REPORT finalization.
```

```text
2026-07-15 (later)
- Action: updated docs/DAEMON.md "Retention" section to describe the
  implemented store (was previously aspirational/D-005-only prose).
- Files changed: docs/DAEMON.md
- Commands:
  cd /workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history && \
    V=/tmp/handoffctl-gate-b1c4b05f; $V/bin/python -m pytest topos/tests -q
    -> 1697 passed in 186.82s (zero-skip full suite, correct CWD matching the
       declared gate exactly)
  python3 -m py_compile <all touched .py files>  -> clean
  git diff --check                                -> clean
- Result: full gate green. No BLOCKED trigger fired at any point.
- Follow-up: write REPORT, commit.
```

## Decisions

- Decision: no rollup/downsampling in the persistent tier; canonical frames
  are persisted once, full resolution, for the whole 24h/256MiB window.
  Reason: Required Contract 2 explicitly forbids the store being "a second
  report/query engine"; the existing `downsample_interval_seconds`/
  `downsample_retention_hours` HistoryConfig fields are already unused
  vestigial fields (grep confirms no consumer beyond digest serialization),
  and D-005's "older rollups" language is a provisional recommendation in the
  *options* discussion, not the accepted DECISION text (which only commits to
  "batched compressed disk segments... under simultaneous byte+age caps").
  Impact: segment-level eviction granularity (see next decision) is the sole
  mechanism bounding disk usage; no separate rollup format/aggregation math
  to validate or keep in sync with report.py/query/semantics.py.
- Decision: eviction is keyed on each segment's OLDEST frame timestamp
  (`first_ts`), not newest, and operates at whole-segment granularity.
  Reason: guarantees the O1 invariant "no frame older than the age cap remains
  queryable" unconditionally regardless of segment size — evicting on
  `first_ts` can only evict *early* (removing some frames that are still
  technically within the cap), never *late*. Evicting on `last_ts` would let
  a segment's oldest frames sit past the cap until its newest frame also
  crosses it, which fails O1's negative case.
  Impact: default `segment_frames=360` (30 min at 5s) bounds this "evicted
  early" slack to a small fraction of the 24h default age cap. Tests use
  `segment_frames=1` for exact frame-level assertions.
- Decision: a segment's recorded index checksum, once present, is always
  authoritative for corruption detection — a mismatch is corruption,
  full stop, never re-validated by re-parsing the file's current content.
  Reason: found via manual testing that a corrupted-but-still-syntactically-
  valid segment (e.g. mangled JSON key names that don't touch the "type"
  field the parser branches on) would otherwise silently pass content-parse
  validation despite its bytes provably differing from what was durably
  published. Content-parse fallback is reserved *only* for orphan segments
  that have no index entry at all (the index-write-lost-mid-crash recovery
  path), where there is no recorded checksum to compare against.
  Impact: `_validate_segment` in daemon/persist.py; covered by
  `test_o6_corrupt_middle_segment_is_quarantined_with_explicit_gap` and the
  `test_read_segment_frames_raises_typed_error_on_corrupt_input` typed-error
  test.
- Decision: gaps are computed structurally from the retained segment-id
  sequence (`_compute_gaps_locked`), not from a separately persisted gap log.
  Reason: an id discontinuity between two *retained* segments is
  unambiguously a mid-stream loss (eviction only ever removes the oldest end,
  never punches a hole in the middle), so the two surviving neighbours'
  own `last_ts`/`first_ts` are sufficient to bound the gap exactly, with zero
  extra persisted state and no risk of the gap log and the segment list
  drifting out of sync across restarts.
  Impact: a *leading* hole (oldest retained segment's id > the very first
  ever assigned) is intentionally folded into the single `evicted` boolean
  rather than a precise range, matching the existing
  `DaemonHistoryFrameSource.evicted` convention in query/source.py — we
  cannot always distinguish "cleanly aged out" from "lost" at the leading
  edge without additional bookkeeping that the reduced design deliberately
  avoids (see "Known gaps" in the REPORT).
- Decision: `PersistConfig.enabled` defaults to `False`, unchanged, even
  though the O10 measurement supports enabling it.
  Reason: Required Contract 6's literal fallback ("if the accepted budget is
  not met, ship it configured off...") implies the converse is a genuine
  choice, not an automatic flip; treating "write to disk by default" as a
  distinct, human-approved product decision is the more conservative,
  reversible-by-default choice for an already-distributed daemon.
  Impact: `topos daemon serve` behaves identically to pre-P91 unless an
  operator explicitly sets `[history.daemon] enabled = true`.

## Blockers

None. No BLOCKED trigger fired.

## Validation

```bash
cd /workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history
V=/tmp/handoffctl-gate-b1c4b05f
$V/bin/python -m pytest topos/tests -q
# 1697 passed in 186.82s (0:03:06)

cd topos
python3 -m py_compile $(find src/topos tests -name '*.py')
# clean

git diff --check
# clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
