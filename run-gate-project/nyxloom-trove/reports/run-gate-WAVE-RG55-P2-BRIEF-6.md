# run-gate-WAVE-RG55-P2 — BRIEF-6 (successor continuation)

RW-17 and C4 are now FULLY DONE (commits `b7771be1`, `d17f9899`). This
brief hands you C5 through C8, plus the assay-r2 lane at the very end.

## Where things stand

Worktree: `/workspaces/vbpub/.worktrees/rg55-run-gate-client`, branch
`rg55-run-gate-client`, project dir `run-gate-project/`. Work ONLY there
— verify with `git -C /workspaces/vbpub status --porcelain --
run-gate-project/` after every commit; it must stay empty (double-checkout
hazard, named in every prior brief).

Git log at hand-off:
```
d17f9899 feat(rg55-p2): C4 -- history schema 2, series_stats, resource columns (R-36)
b7771be1 fix(rg55-p2): RW-17 -- replace the test-only profiling kill switch with an operator-facing RUN_GATE_PROFILE override
a5a3023b docs(rg55-p2): checkpoint after C3 fully done + live-probed -- LOG, REPORT, BRIEF-5
38089fe6 fix(rg55-p2): basic-path final sample must not record a total docker-exec failure as data
d8003d36 feat(rg55-p2): C3 wiring -- token, daemon/basic orchestration, ephemeral+exec flows, re-attach/promote profiling rules
```

**Gate state at hand-off (both against `d17f9899`):**
- `nice -n 19 ionice -c 3 python3 -m pytest tests/ -q`: **952 passed, 3
  skipped**, ~114s.
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty`:
  diff-coverage **OK 568/568 (100.0%) lines, 208/208 (100.0%) branches**,
  exit 0.
- `assay-r1`/`assay-r3`: last run and PASSED against `38089fe6` (C3's
  final commit) — NOT re-run against `b7771be1`/`d17f9899` yet. Re-run
  both before your own final commit (they need a CLEAN tree — commit
  first); if either surfaces a diff-coverage or mutation gap from RW-17/C4
  specifically, fix it in place.
- `assay-r2`: still deliberately NOT run. Run it ONCE, at the very end of
  the WHOLE package, by whichever session makes the final commit. If that
  is you: run it last, under `nice`, bare-host, serial, record the
  verdict + survivors, kill survivors with tests or justify each in the
  REPORT.
- Live acceptance (handoff §4.1/§4.2): both already satisfied (session 5).
  §4.3 (`footprint --write`, both the refusal case on `selftest`'s own
  bare-host store and the real-write case on the probe project's store) is
  yours as part of C5.

## What NOT to re-read

Everything BRIEF-1..5 already marked read/skipped stays that way. Also now
settled, do not re-derive or re-litigate:

- RW-17's shape (`PROFILE_AMBIENT_ENV_VAR` / `RUN_GATE_PROFILE`,
  `disabled_reason` threading, the two dead-branch `--dry-run` fixes) —
  APPLIED, gate-verified, documented in `usage()`. Full SPEC `R-43g`/
  README config-section prose is explicitly YOUR job now, folded into C8's
  existing "config incl. `RUN_GATE_PROFILE`" scope — do not write it twice.
- C4's shape (`series_stats`/`_resource_field`/`RESOURCE_SERIES_GETTERS`,
  the schema-stamp-on-every-write migration mechanism, the
  `hot_set_p90_bytes` → `resources.damon.hot_bytes.p90` correction against
  the handoff's own shorthand) — all proven, all wired. C5 CONSUMES this
  (`series_stats`, `RESOURCE_SERIES_GETTERS`, `_fmt_mib`/`_fmt_cores`) for
  the footprint manifest's own numbers; it does not need to touch C4's
  code, only call into it.

## Current line-number anchors (verify with `grep -n` before editing regardless — this file drifts every commit)

- `HISTORY_SCHEMA = 2`: **111**
- `_validate_environment`: **241** (its `_check_keys` call is where C6
  adds a `resources` sub-table it does not currently accept at all)
- `LANE_KEYS`: **277**
- `_validate_lane`: **300** (the `resources` block's `_check_keys` a few
  lines below — `{"memory", "memory_swap", "cpu_weight", "io_weight",
  "shared"}` — is where C6 adds `"cpus"`)
- `profile_meta`: **1360** (`"expected": None` is the line C5 changes to
  read the footprint manifest)
- `print_profile_session_line`: **1523**
- `print_profile_plan_dry_run`: **1528** (RW-17-updated; C5's footprint
  disclosure line is a NEW sibling function, `print_footprint_line`,
  called from the same call sites `print_profile_session_line` already
  has — see "One thing to know" in BRIEF-5, still true, still unprinted)
- `_write_json_atomic`: **2185** (the exact atomic-write pattern C5's
  `--write` reuses verbatim)
- `series_stats`: **2735**; `_resource_field`: **2756**;
  `RESOURCE_SERIES_GETTERS`: **2776**; `_lane_stats`: **2787**;
  `lane_history_report`: **2794**; `_fmt_mib`: **2819**
- `cmd_history`: **2886** (C5's `cmd_footprint` is a natural sibling,
  placed right after it)
- `_RESERVED_POINTER_VERBS = {"doctor", "validate-pointers", "history"}`:
  **3309** — C5 adds `"footprint"` here (BREAKING, note in CHANGES)
- `cmd_doctor`: **4341** (C5's divergence/staleness warnings, C6's `-n
  auto`/`--workers auto` check, C7's whole new "profiler" check all live
  inside this function; search it for the EXISTING R-29 slice-read WARN
  text before adding C7's WHY-appended version)
- `run_container_lane`: **5852**; `run_exec_lane`: **6115** (both need
  C6's `--cpus` argv addition, mirroring the existing `mem_cap`/`--memory`
  pattern already in each; both already read `profile_plan`/print the
  RW-17-fixed dry-run disclosure — do not re-touch that path)
- `usage()`: **6300** (C5 adds the `footprint` verb's help text next to
  `history`'s own entry; C6/RW-17 already added their own lines)
- `main()` argparse: `add_argument` calls start **6495**; the
  `args.lane == "history"` branch (the exact pattern to copy for
  `footprint`, including its `--worktree` read-scope resolution and its
  `--json`-vs-every-other-verb refusal at **6543**) starts **6582**; the
  `--fresh` refusal list `(None, "doctor", "history", "validate-pointers")`
  at **6537** needs `"footprint"` added too (footprint starts no
  container, same reasoning as `history`/`doctor`)
- `__revision__ = 40`: **15** (bump to 41 in C8, comment prepended in the
  same style — read a couple of existing per-revision notes first, they
  are long and specific by design)

## Remaining work (handoff §2 C5-C8, verbatim scope; BRIEF-5 already restated it in full — re-read there, not duplicated here)

Read BRIEF-5's own C5/C6/C7/C8 sections again (they are unchanged, still
accurate) alongside the handoff's §2 text and contract §4.5/§4.6/§4.7.
Sequencing note for THIS brief only:

1. **C5 first** (`footprint` verb, R-44) — depends on C4, which is now
   done. Includes the footprint disclosure line (contract §4.6 line 3),
   deliberately still unprinted. `doctor`'s divergence/staleness warnings
   are part of C5, not C7 (C7 is the SEPARATE new "profiler" check).
2. **C6** (RG-48, `resources.cpus`) — independent of C5, can be done
   before or after it; ordered second here only because it is smaller.
3. **C7** (`doctor` profiler check) — independent of C5/C6's doctor
   changes (different check, additive); do it after both so `cmd_doctor`
   only needs touching once more per session, not twice.
4. **C8** (docs/spec/backlog/revision) LAST, once C5-C7's shipped code is
   the ground truth C8 writes SPEC `R-43`/`R-44`/README/CONSUMERS/
   LANE-AUTHORING/CHANGES/backlog/`__revision__ = 41`/`usage()` from.
   Remember RW-17's own deferred doc debt (SPEC `R-43g`, README config
   section) folds into this pass — do not forget it, it is not in BRIEF-5
   because BRIEF-5 predates RW-17.

Then **assay-r2 once**, then `selftest`, `assay-r1 --base main`,
`assay-r3`, and `doctor` green on the final commit, and `footprint --write`
exercised once more (real manifest in the REPORT).

## Test debt (unchanged guidance from BRIEF-5, restated because it bit nobody yet but will)

`run_gate.main([...])` in-process for anything diff-coverage must see —
NEVER `run_tool()`'s subprocess for that purpose (coverage.py does not
instrument a spawned child interpreter). This session's own C4 work added
one more in-process proof (`TestHistoryTableResourceColumns::
test_history_table_in_process_prints_the_resource_line`) as a template —
copy that pattern for C5's footprint table/JSON output.

## Process notes (unchanged from BRIEF-1..5, restated)

- Edit tool only; `git -C <worktree> commit -F <msgfile> --only --
  <paths>`; both trailers — match whatever YOUR session's own current
  attribution instruction says, not a prior brief's copy.
- `nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` from
  `<worktree>/run-gate-project`, verdict read in a SEPARATE step from the
  captured log (not a pipe tail). Targeted `pytest tests/test_run_gate.py
  -k <cluster>` while iterating (whole suite ~110-120s).
- `assay-r1`/`assay-r3` need a CLEAN tree (commit first).
- HOST LOAD (handoff §6): 8 cores shared with a production game server;
  serial pytest only; at most 2 gate containers estate-wide (`docker ps
  --format '{{.Image}}'` for `tester-unified:local` first); `docker update
  --cpus=3` right after launching any container for live probes; teardown
  in `finally`.
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate). C5 alone is a large deliverable
  (new verb, new manifest writer, doctor warnings, run-path
  `meta.expected` wiring, the footprint disclosure line) — a sanctioned
  mid-deliverable cut, if you need one, is "the `footprint` verb + its
  tests green, before wiring `doctor`/the run-path reads of the manifest
  in" — say so explicitly in your own BRIEF-7 if you take it.

## Retention prompt (paste into your own `/compact` if you need to compact mid-session)

```
KEEP: RW-17 and C4 are FULLY DONE, commits b7771be1/d17f9899, both
gate-verified (selftest 100% line+branch each time; whole suite 952
passed 3 skipped after C4). RW-17: RUN_GATE_PROFILE ('on'|'off') replaced
the old test-only kill switch, resolve_profile_settings applies 'off'
first (short-circuit, carries disabled_reason) then config/lane, then
'on' last (overrides even lane profile=false); two dead-branch --dry-run
bugs found+fixed same commit; doctor disclosure + full SPEC R-43g/README
prose deferred to C7/C8 on purpose. C4: HISTORY_SCHEMA=2, _apply_record
stamps it on every write (the real migration mechanism -- setdefault
alone does not upgrade an existing schema:1), series_stats/
_resource_field/RESOURCE_SERIES_GETTERS generalize duration_stats for 5
new keys, hot_set_p90_bytes reads resources.damon.hot_bytes.p90 (corrected
from the handoff's own memory/cpu/host shorthand -- verified against
contract Sec 3 directly). Current line-number anchors for everything C5-C8
touches (HISTORY_SCHEMA 111, series_stats 2735, cmd_history 2886,
_RESERVED_POINTER_VERBS 3309, cmd_doctor 4341, run_container_lane 5852,
run_exec_lane 6115, usage() 6300, main() history-verb branch 6582,
__revision__ 15) -- re-verify with grep before editing regardless. The
run_tool()-subprocess coverage blind spot (use run_gate.main() in-process
for anything diff-coverage must see) -- C4's own
test_history_table_in_process_prints_the_resource_line is the template.
Which of C5/C6/C7/C8/assay-r2 you have finished, gate-verified, and
committed. HOST LOAD container-count state.
DROP: the full RW-17/C4 source-code listings (re-readable in one call from
run-gate.py directly); the RW-17 dead-branch bug-hunt narrative in full
(keep only "both --dry-run call sites were gating print_profile_plan_
dry_run on `profiling`, fixed same commit as RW-17" as the pointer); the
C4 test-authoring arithmetic-mistake story (keep only "fixed before the
whole-suite run" as the pointer).
```
