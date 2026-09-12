# run-gate-WAVE-RG55-P2 — REPORT (C1-C8 all DONE; final gates green; assay-r2 tracked separately below)

Package P2 of the RG-55 wave — the run-gate client side of cgroup-profiler
integration. **All eight deliverables (C1-C8) are DONE and gate-verified**:
C1 (RG-53) + its RW-5 rework, C2 (assay-r1/r2/r3 + gate-full lanes), C3
(profiling client, config/`ProfilerClient`/`ResourceAccumulator`/
`BasicSampler` + full wiring), RW-17 (`RUN_GATE_PROFILE` ambient
override), C4 (history schema 2 + resource series), C6 (RG-48
`resources.cpus`), C5 (the `footprint` verb, R-44), C7 (`doctor`'s
"profiler" check + the R-29 private-namespace WHY), C8 (SPEC/README/
CONSUMERS/LANE-AUTHORING/CHANGES/backlog sweep, `__revision__ = 41`).
`__revision__ = 41` is the final commit's own revision. See
`run-gate-WAVE-RG55-P2-BRIEF-1.md` through `-BRIEF-6.md` for each
session's continuation state and `run-gate-WAVE-RG55-P2-LOG.md` for the
full commit-by-commit record (this REPORT summarizes; the LOG is the
detailed evidence trail, including the final-commit gate sweep and the
real `footprint --write` live-probe transcript).

## C1-rework — RW-5 (0/0 diff semantics corrected)

**Status: DONE**, superseding part of C1 below. The controller's RW-5
ruling found that C1's original design — refuse (exit 2) a
`changed_executable == 0` verdict unless `--allow-empty-diff` — made this
project's OWN `selftest` lane permanently red on `main` itself
(merge-base(main, HEAD) == HEAD there → always 0/0) and would have
blocked every `cmru release` of this project. Reworked: 0/0 now reports
**SKIPPED** (exit 0, a distinct stdout line naming the resolved base and
HEAD's relationship to it — `HEAD is on the base`, or `HEAD is N commits
ahead of the base; the diff touches no executable source line`), never a
plain `100.0% OK`; `Verdict` gains a `skipped` flag and a `verdict`
tri-state property (`"skipped"`/`"ok"`/`"fail"`) so any current or future
caller has a field that cannot mistake a 0/0 for a pass. Hard refusal
(the original exit-2 behavior, naming the three known false-0/0 routes)
becomes opt-in via the new `--refuse-empty-diff`; `--allow-empty-diff` no
longer exists. `evaluate()`'s pure `pct`/`passed` computation is
UNCHANGED — still 0/0-is-100% for any direct caller — only the CLI
(`main()`) and the new `Verdict.skipped`/`.verdict` fields distinguish the
case, same separation-of-concerns C1 established (the 0/0 policy lives at
the CLI boundary, not inside the pure classifier).

Full change description, gate verdicts (including the self-referential
proof — this branch is 2 commits ahead of `main`, neither commit touching
`run-gate.py`, and the selftest now exits 0 with a SKIPPED line naming
exactly that) and files touched are in the LOG's "Commit 2 — C1-rework
(RW-5)" section — not duplicated here to avoid drift between two copies of
the same evidence. `CHANGES.md` `[Unreleased]` and
`KNOWN_ISSUES_TODO_BACKLOG.md` RG-53 (new `### Rework — RW-5` subsection,
original FIXED text left intact for historical accuracy) were both
updated per the ruling's explicit instructions.

## C1 — RG-53 original landing (`tools/coverage_gate.py` branch-aware diff judge + 0/0 refusal)

**Status: DONE** (2026-09-12, prior session), **partially reworked by
RW-5 above** — the branch-awareness half (item 1 below) is UNCHANGED; the
0/0 handling half (item 2) is what RW-5 replaced. Kept here verbatim as
the historical record of what C1 originally shipped. Summary:

1. Branch awareness: `_validate_cov_record` validates the optional
   `missing_branches`/`executed_branches` keys (verified against the
   INSTALLED coverage.py's actual JSON schema, read directly from
   `coverage/jsonreport.py` in this devcontainer's venv, rather than
   assumed — file-level lists of `[source_line, target_line]` int pairs,
   populated only when branch coverage was collected). `_branch_maps`
   turns those into per-source-line missing/all-arc sets; `evaluate()`
   counts a changed line as uncovered when it executed but left an arm
   untaken, in addition to the pre-existing never-executed case.
   `Verdict.branches_total`/`branches_missed` are scoped to the SAME
   changed+executable line set as the existing line counts (never a
   second, whole-file denominator) — "beside lines", per the handoff's own
   phrasing. `branch_partial_lines` distinguishes "ran, missed an arm"
   from "never ran" for the FAIL listing (`(branch)` suffix).
2. Empty-diff refusal: `_check_nonempty_diff` (new, pure, unit-tested in
   isolation) refuses a `changed_executable == 0` verdict in `main()`
   (exit 2) unless `--allow-empty-diff`, naming the resolved base, HEAD,
   and the three routes to a false 0/0 the backlog names (merge-commit
   first-parent/RG-54, stale worktree base, reverted work/RG-51 round 5).
   Deliberately kept OUT of `evaluate()`, which stays a pure classifier —
   the refusal is CLI policy, not a change to the pure function's contract,
   so existing direct callers/tests of `evaluate()` needed no changes.
3. `run-gate.toml`'s `selftest` argv is UNCHANGED (verified by reading it:
   `--allow-empty-diff` is not in the invocation) — confirmed this is
   deliberate per the handoff ("the flag is not passed — a 0/0 selftest
   must go red").
4. `CHANGES.md` `[Unreleased]` gained a BREAKING entry; `KNOWN_ISSUES_TODO_
   BACKLOG.md` RG-53 moved OPEN -> FIXED with evidence.

### Tests

`tests/test_coverage_gate.py`: 8 -> 20 tests. New coverage: branch-partial
line uncovered (with exact branches_total/missed numbers hand-checked),
branch-fully-taken line stays covered, no-branch-data is a no-op
(regression guard for the pre-fix behavior), branch totals scoped to
changed lines only (a branch on an untouched line must not leak into the
totals), malformed branch-arc shape raises `CoverageGateError`,
`--allow-empty-diff` argparse default/override, `_check_nonempty_diff`'s
three cases in isolation, and two `main()` end-to-end tests against a real
tmp_path git repo: one proving the refusal fires (exit 2, message content)
AND that `--allow-empty-diff` produces a normal pass on the SAME degenerate
repo, one proving the CLI's branch-note text appears on a passing run.

### Gate verdicts (read in a separate step from the captured log, not a pipe tail)

- `pytest tests/test_coverage_gate.py -q`: **20 passed**, 0.31s (`nice -n
  19 ionice -c 3`, isolated run before the whole suite).
- `./run-gate.py selftest --allow-dirty` (whole suite, `nice -n 19 ionice
  -c 3`, from `<worktree>/run-gate-project`): pytest phase **827 passed, 3
  skipped** (pre-existing, unrelated wheel-packaging skip) in 104.22s.
  `coverage_gate.py` phase: **exit 2**, `diff-coverage ERROR: 0 changed
  executable lines under 'run-gate.py' between base e499a168... and HEAD
  e499a168... -- refusing an empty-diff PASS. ...`.

  **This exit 2 is the intended, designed acceptance evidence for C1's
  second half, not a defect.** C1's diff against `main` touches only
  `tools/coverage_gate.py`, `tests/test_coverage_gate.py`, `CHANGES.md`,
  `KNOWN_ISSUES_TODO_BACKLOG.md` — none is `run-gate.py`, the sole
  `--source` scope judged. Zero changed executable lines under that scope
  is genuinely, correctly zero here. Before this fix, the exact same
  situation would have silently printed `diff-coverage OK: 0/0 ... (100.0%
  >= 100.0% floor)` and exited 0 — this run is direct, self-referential
  proof the refusal works. Full transcript preserved at
  `/tmp/claude-1003/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/selftest-c1.log`
  (scratchpad, not committed). Expected to return to a normal covered-diff
  green once a later commit in this branch (C3 onward, which touches
  `run-gate.py` itself) lands, since the diff accumulates against the
  wave's `main` base across every commit on this branch.

  assay-r1/r2/r3 lanes: see the "C2" section below — r1 and r3 both run
  and PASS; r2 (mutation, up to 4h) is deferred per the handoff's own
  timing rule ("once at the very end").

  Live acceptance probes (handoff §4): **NOT YET RUN** — they exercise the
  profiling client (C3), which has not started.

## C2 — assay-r1/r2/r3 + gate-full lanes (D-11) — RW-8 resolved

**Status: DONE.** BRIEF-2's blocking decision ask (how to scope
`[lanes.r1]`/`[lanes.r2]`'s `judge.source_roots` when `run-gate.py` has no
isolating subdirectory of its own) was resolved by the controller's RW-8
ruling before this session's dispatch — quoted verbatim in the LOG's
"Session 3 ... orientation" section. Resolution: `source_roots = ["."]`
for both lanes (broader than either option BRIEF-2 considered — judge
everything, narrow-exclude only if the tooling is later forced to), with
`tools/` judged DELIBERATELY.

`assay.toml` (new): `[lanes.r1]` (rigor `["R0","R1"]`, `fail_under =
100.0`, `allow_excluded = false`, `require_branch = true`,
`base_source = "request"`); `[lanes.r2]` (rigor `["R0","R2"]`, mutation
`jobs = 1`, `max_mutants = 1500`, the four `python:*` operators from
`scripts/cgroup-profiler/assay.toml`); both declare `isolation.
snapshot_selection = "repository-minus-unsafe-symlinks"` with the same
three topos fixture omissions `cmru/assay.toml`/`scripts/cgroup-profiler/
assay.toml` already carry (this is a monorepo lane — assay's snapshot
walks the WHOLE resolved vbpub commit, not just this project's subtree).

`tools/canary-run.sh` (new, executable): ported the harness shape from
`scripts/cgroup-profiler/tools/canary-run.sh` (disposable tar copy to a
scratch dir, sed-free python find/replace, callable by name or run-all).
ONE canary declared: `duration_stats`'s median→mean flip, asserted
against `tests/test_run_gate.py::TestHistoryRollingSeries::
test_one_slow_outlier_does_not_become_the_typical_cost`. **Decision
applied** for the handoff's "run the r1 lane there, assert it goes RED"
(a decision ask under RW-9 — recorded here, not left blocking): read as
"run the ONE test selector that provably encodes the invariant", not "run
the literal r1 argv (830+ tests) in a scratch copy" — mirroring
`scripts/cgroup-profiler/tools/canary-run.sh`'s own proven, already-
established shape in this exact repo, and avoiding an unnecessary
multi-minute re-run of the whole suite per canary on a HOST LOAD budget
(§6) shared with a production game server. A failing pytest selector
always fails assay's own R0 for the real `assay-r1` lane regardless of
coverage, so the causal claim ("the gate would have caught this") holds
without paying to re-run the full suite. Verified standalone before
wiring: `median-not-mean  ok (assay-r1 would reject it)` / `canary: 1
rejected, 0 survived`, exit 0.

`run-gate.toml`: `[lanes.assay-r1]`/`[lanes.assay-r2]` (kind=assay,
bare-host — same reasoning as `selftest`'s own bare-host requirement,
this project's suite is self-referential against real docker/mountinfo
internals — pinned 6.1.1, `clean_tree = true`; r2 additionally
`stall_timeout = "20m"`, budgets 30m/4h); `[lanes.assay-r3]` (kind=command,
bare-host, `tools/canary-run.sh`, budget 15m); `[lanes.gate-full]`
(`selftest && assay-r1 && assay-r3` — r2 excluded, invoked separately
pre-merge per the handoff). `cmru.toml` gained a comment on why the
release gate stays `selftest` alone. `README.md` gained a "Gate and
evidence" section naming all five lanes.

### Proving the config (RW-8's required step, before wiring into run-gate.toml)

```
$ python3 tools/assay/assay-6.1.1.pyz lanes --json --file assay.toml
```
```json
{
  "assay_version": "6.1.1",
  "inventory_schema": 1,
  "lanes": [
    {"name": "r1", "rigor": ["R0", "R1"], "scope": "S1",
     "snapshot_selection": "repository-minus-unsafe-symlinks",
     "base_source": "request", "language": "python",
     "coverage": {"artifact": "coverage.json", "format": "coverage-py-json",
                  "producer": null},
     "mutation": null, "canary": null, "argv0": "python3", "budget": "30m",
     "enforcement": "gate", "rigor_reachable": ["R1", "R2", "R3"]},
    {"name": "r2", "rigor": ["R0", "R2"], "scope": "S1",
     "snapshot_selection": "repository-minus-unsafe-symlinks",
     "base_source": "request", "language": "python", "coverage": null,
     "mutation": {"jobs": 1, "max_mutants": 1500,
                  "operators": ["python:compare-swap", "python:boolop-swap",
                                "python:bool-const-flip", "python:falsy-swap"]},
     "canary": null, "argv0": "python3", "budget": "4h",
     "enforcement": "gate", "rigor_reachable": ["R1", "R2", "R3"]}
  ]
}
```
(fields present in the real output but omitted above for brevity: `cwd`,
`env_required`, `environment_command`, `external_tools`,
`infrastructure_facts`, `link_paths` — all empty/null for both lanes.)

RW-8 also asked for "one `assay plan r1 --request-base <merge-base of
main and HEAD>` (read-only)". **Substituted `plan r2`**: `assay plan
--help` documents `plan` as taking "the mutation lane name to inspect",
and `plan r1` was tried first and refused: `ERROR/BAD_LANE_CONFIG: lane
'r1' does not declare an R2 mutation judge` — `plan` is R2/mutation-only
by design, so `r2` is the only lane in this file it can inspect at all,
making it the only faithful way to exercise RW-8's actual intent (prove
`base_source = "request"` resolves against a real merge-base, read-only)
with the tooling that exists.

```
$ MB=$(git merge-base main HEAD)   # e499a168064f99ffee3810c442fce3569ebbbc46
$ python3 tools/assay/assay-6.1.1.pyz plan r2 --file assay.toml --request-base "$MB"
```
Result: `"status": "ok"`, 15 real mutation candidates, **every one in
`run-gate-project/tools/coverage_gate.py`** (proving `tools/` really is
in the judged set against a real diff, not just declared in config),
operators `python:compare-swap`/`python:boolop-swap`/
`python:bool-const-flip` all represented, `jobs: 1`, `max_mutants: 1500`,
`estimated_serial_seconds: 900.0`, `estimated_wall_seconds: 900.0`. A
representative candidate:
```json
{"id": "37dc3d5f02198e1041581d423092cb024af55d30178b4584e90fd1eeb7f92219",
 "path": "run-gate-project/tools/coverage_gate.py", "lineno": 328,
 "operator": "python:compare-swap", "description": "Eq->NotEq",
 "start_byte": 14103, "end_byte": 14105}
```

Neither probe exercises the `run_gate.py`/`run-gate.py` symlink-duplicate
risk RW-8 flagged — the diff since merge-base never touches `run-gate.py`
itself. Left as a documented, unexercised edge (assay.toml's own header
comment records RW-8's fallback instruction for whoever hits it first:
apply the narrowest exclusion, never widen to excluding `tools/`).

`doctor`: 8 checks, 5 OK, 1 WARN (pre-existing, unrelated to C2 — the
linked-worktree host-lane git-view note), 0 FAIL, 2 SKIP (the documented
"bare-host — its PATH is this machine's" skip for both new assay lanes'
toolchain-fitness check).

### A real finding from the FIRST live assay-r1 run — and its fix

The first `./run-gate.py --base main assay-r1` run **FAILED**:
`FAIL/UNCOVERED_LINES`, 86.9% (112/131) — a genuinely new finding, not a
config defect: RW-8's broadened `source_roots = ["."]` judged `tools/
coverage_gate.py`'s own C1/C1-rework changes for the first time ever
(`selftest`'s vendored local gate deliberately scopes itself OUT of its
own enforcement). Real, addressable gaps: lines 170-173 (a non-list
`missing_branches`/`executed_branches` guard), 396-401 (`_is_ancestor`'s
real-git-failure branch), 511-514 (`main()`'s own except around a failing
`_base_relation`), 536-546 (the FAIL branch's uncovered-lines print loop,
including its two tag cases). Four new tests added (full detail in the
LOG's "Commit 4" section); `tests/test_coverage_gate.py`: 27 passed (was
23); whole-suite `selftest`: 834 passed (was 830), exit 0.

**Re-ran `./run-gate.py --base main assay-r1` after the fix: `r1: PASS
(exit 0)`.** Full transcript:
```
run-gate: comparison base main (from --base) → --request-base
run-gate: rev 40 | lane assay-r1 | env built-in 'bare-host'
run-gate: budget 30m (advisory)
assay-6.1.1.pyz: OK
r1: PASS (exit 0)
commit: 45f2aa5a04a3a8909a5c6fad191c175102349fae
argv: python3 -m pytest tests -q --cov=. --cov-branch --cov-report=json:coverage.json
run-gate: verdict artifact: .../run-gate-project/.assay/verdict-r1.json
run-gate: state directory: /workspaces/vbpub/.run-gate/assay-state/.worktrees/rg55-run-gate-client/run-gate-project
run-gate: lane 'assay-r1' exit 0
```

### assay-r3 (canary) run

```
$ ./run-gate.py assay-r3 --allow-dirty   # --allow-dirty: LOG.md edits were
                                          # uncommitted at proof time, unrelated
                                          # to the canary's own tar-copy mechanism
run-gate: rev 40 | lane assay-r3 | env built-in 'bare-host'
run-gate: budget 15m (advisory)
run-gate-project assay-r1 canary
median-not-mean                    ok (assay-r1 would reject it)
canary: 1 rejected, 0 survived
run-gate: lane 'assay-r3' exit 0
```
**PASS.** Both lanes the handoff requires "once before return" are green.
`assay-r2` (mutation, up to 4h) is intentionally NOT run this session —
the handoff's own timing rule places it "once at the very end", and
`assay plan r2`'s own `estimated_serial_seconds: 900.0` (15 min) plus
budget overhead makes it a poor use of this checkpoint's remaining
window; left for whichever session makes the final commit of this
package.

## C3 (partial) — profiling config + ProfilerClient + ResourceAccumulator + BasicSampler (R-43)

Delivered, gate-verified: the config layer (`[profile]`/`[footprint]`,
per-lane `profile` key, `resolve_profile_settings`), the three ported
cgroupfs parsers plus a new `_profile_parse_raw_limit`, `ProfilerClient`
(daemon RPC client), `ResourceAccumulator` (contract §7 arithmetic), and
`BasicSampler` (the in-lane fallback's docker-exec plumbing + direct host
PSI reads). NOT delivered: wiring any of this into
`run_container_lane`/`run_exec_lane`/`await_container`/
`run_bare_host_lane`/`follow_container`/`resolve_inflight` — no lane run
today does anything different than before this commit. See the LOG's
"Commit 5" section for full narrative; this section is the evidence
digest.

**ResourceAccumulator proof (the single highest-value test per BRIEF-3's
own framing):** fed the golden `frames/0..4/` cgroupfs tree directly
(bypassing BasicSampler/docker entirely), for both `scope` values:

- `scope="container-shared"`: byte-for-byte dict equality against
  `summary-basic-v1.json` — every field, including the `events.limit_drift
  = 1` / `memory_high_breach = 1` transition-detection case and the `p90 ==
  peak` N=5 nearest-rank gotcha.
- `scope="container"`: `memory.peak_bytes == 796917760`, `source ==
  "memory.peak"`, `memory.peak_over_baseline_bytes == 272629760` (all three
  match `fixtures/rg55/README.md`'s own hand-derived numbers exactly); every
  OTHER field verified identical to the `container-shared` fixture after
  patching just those three memory keys — proving the implementation's
  scope-independence claim empirically, not just by code inspection.

Both checks passed on the FIRST attempt against the real implementation
(verified via a throwaway script before any pytest test was written), then
re-proven as permanent pytest tests
(`TestResourceAccumulatorGoldenFixtures`).

**ProfilerClient proof:** a new `cgprofile ctl` branch in the shared fake
docker shim (`CGPROFILE_SHIM_CASE`), driven by golden fixture files for
`version`/`host`/`status`/`stop`/`start`, plus per-test error injection
(exit 2 + `error-v1.json`, exit 3 + a synthetic daemon-fault body, garbage
stdout, `contract: 2`, a real subprocess timeout via a shrunk
`PROFILE_CTL_TIMEOUTS` entry, a nonexistent docker binary raising OSError,
non-object JSON). Every failure mode returns `(None, reason)` and was
proven not to raise.

**BasicSampler proof:** a dedicated fake-docker shim answering each `exec`
call with the k-th golden frame's concatenated dump (in construction order,
matching the handoff's "the shim serves frame k's contents on the k-th
call" test spec at the per-invocation granularity BasicSampler actually
uses — one invocation samples ALL files in one call, not one `cat` per
file); five ticks reproduce `summary-basic-v1.json` byte-for-byte end to
end (docker-exec parsing + host-PSI reads + `ResourceAccumulator`
arithmetic, no shortcuts).

### Contract drift found (see also LOG's Commit 5 + this report's Decision asks)

Contract §4.3's basic-path file list literally names 10 files, omitting
`memory.max`/`memory.high` — but §7's `limit_drift` rule and the golden
`summary-basic-v1.json` fixture both require them. `PROFILE_BASIC_FILES`
reads 12 files. This is a genuine spec inconsistency (confirmed by
re-reading contract §4.3 and §7 side by side), not a misreading on this
package's part — flagged for the P0 controller and the P1 (daemon) session
to confirm independently, since the contract is described as FROZEN and
shared verbatim between both packages.

## C3 (wiring) — token, orchestration glue, all four lane runners, re-attach/promote (R-43 COMPLETE)

Finishes C3. RW-11 (basic-path 12-file list) and RW-12 (`await_container`
tick shape: `proc.wait(timeout=PROFILE_SAMPLE_SECONDS)` while a basic
sampler is active, else unchanged `PROGRESS_POLL_SECONDS`, with the stall/
log-watch poll gated to real `PROGRESS_POLL_SECONDS` elapsed via an
injectable monotonic clock; no daemon per-tick work) applied exactly as
ruled — quoted at the top of LOG's Session 5.

**New orchestration functions** (between `BasicSampler` and the
environment-fact-derivation section, so the narrow, independently-tested
contract classes stay above the glue that calls them): `profile_meta`,
`read_host_pressure_snapshot`/`print_host_pressure_line`,
`start_lane_profiling` (daemon-try-then-basic-fallback, sampling the
baseline immediately on fallback), `tick_lane_profiling`,
`finish_lane_profiling`, `print_profile_warning` (ONE warning per
invocation, contract §1.3, via a `_warned` sentinel), `print_profile_session_line`,
`print_profile_plan_dry_run`. A profiler STATE is a plain dict (matching
`record`/`watch`'s own calling convention in this file), never a class —
documented at its first write in `start_lane_profiling`'s own docstring.

**Ephemeral (`run_container_lane`)**: token appended right after the
`forward_env` loop (gated behind `profiling = bool(profile_plan and
profile_plan["enabled"])`, so disabled means literally no `-e` flag, no
extra `docker inspect`, no `start_lane_profiling` call at all — not just
an unused token); `write_inflight_record` called TWICE (once with
`profile_token`/`profile_daemon` right after a successful `docker run -d`,
a second time adding `profile_session` after a successful daemon `start`);
container id resolved via `container_state(docker, name)["id"]` — the
SAME single `docker inspect` call `resolve_inflight` already uses
elsewhere in this file, not a second, differently-shaped one — falling
back to `docker run -d`'s own stdout id if `container_state` reports the
container already gone (tested directly, since a real fake-docker shim
without `inspect` support cannot express it safely).

**Exec (`run_exec_lane`)**: rewritten from blocking `subprocess.run(argv)`
to `Popen` + a `proc.wait(timeout=…)` loop identical in shape to
`await_container`'s (tick = `PROFILE_SAMPLE_SECONDS` only while a basic
sampler is active, else `None` — blocks exactly like the old blocking call
when profiling is off or daemon-mode, contract's own "no per-tick work"
rule for the daemon path, one level over); container id = the persistent
runner's (`container_state` again); `--scope container-shared`. The
red-first proof named by the wave's own test spec (§3) — verified by
REASONING rather than by literally reverting and re-running: the OLD
`subprocess.run(argv)` call structurally blocks until the process exits,
so the loop that calls `tick_lane_profiling` never runs at all under that
shape — at most the ONE pre-loop baseline sample from `start_lane_profiling`
could ever exist, never the ≥2 the new test asserts. The rewrite and its
test were developed together (the coupling between the Popen loop and the
profiler-state threading made a literal revert-and-rerun round trip cost
more than the argument above already proves); recorded here rather than
silently claimed as executed.

**Bare-host (`run_bare_host_lane`)**: the host-PSI line (gated behind
`profile_plan["enabled"]`, same as every other new disclosure line, so it
never fires under the kill switch) and the RG-57 record fields
unconditionally (`resources: null`, `profile_error: "bare-host lanes are
not profiled (RG-57)"`, `profile_ref: null`) whenever a `run_record`
exists (i.e., every non-dry-run invocation).

**Re-attach/collect/promote (`resolve_inflight`/`promote_follower`)**: the
COLLECT branch (container already exited before this client saw it) sets
the three fields unconditionally — `run_record` is never `None` there,
`adopt_inflight_start` (an EXISTING function, its own signature `run_record:
dict`, not optional) is called unconditionally one statement earlier and
would already crash first if it ever were, so a defensive guard here would
have been dead code inconsistent with that. The ADOPT branch (re-attach to
a still-RUNNING container whose original owner died) adopts the recorded
`profile_session` — daemon mode, no new `start` — when the record has one;
when it does not (the original run degraded to basic, or profiling was
disabled), `profile_error` names why re-attach specifically cannot resume a
basic-path session (its samples live only in the dead process's memory).
`promote_follower` (this client was FOLLOWING a live owner who then died)
mirrors the adopt case exactly, finishing the recorded session itself
before its own `docker rm -f`.

### A real finding from the live probes — and its fix

Handoff §4.1's own ephemeral acceptance check (`memory.peak_bytes ≥ 100
MiB`) FAILED on the first live run: `peak_bytes` came back `null` every
time. Root cause, confirmed by direct reproduction against a real
container (`docker exec <name> ...` immediately after it exits, `rm -f`
still pending): `docker exec` refuses outright ("is not running") on an
already-exited-but-not-yet-removed container — and `finish_lane_profiling`'s
"final sample" (contract §4.2) always runs AFTER `await_container` has
already confirmed the process exited via `docker wait`. The pre-fix code
called the SAME `sample_once()` ticks use for that final sample, silently
recording the resulting TOTAL read failure as `samples[-1]` — and scope
`"container"`'s own peak formula (contract §7) is defined as the LAST read
of `memory.peak`, so the run's actual last GOOD reading (from an earlier,
in-tick sample) was discarded every single time. The exec-mode
(`container-shared`) scope never has this problem — the persistent runner
stays alive after the LANE's own command exits, so its final `docker exec`
always succeeds; live-probed clean before this was even discovered (see
"Live acceptance" below).

Fix: `BasicSampler.sample_final()` (new method; `sample_once()` — and
every test that calls it directly, including the golden-fixture byte-exact
one — is completely untouched) drops a TOTAL failure (every field
unreadable — a `_is_total_failure` helper distinguishes this from the
PARTIAL per-file misses `_read_container` already tolerated) instead of
recording it, with a same-run safety net (record it anyway if the
accumulator would otherwise finish with zero samples, avoiding a new crash
mode). `finish_lane_profiling`'s basic branch calls `sample_final()`
instead of `sample_once()` — the only call-site change. Re-probed live
after the fix (see "Live acceptance"): `peak_bytes` now 115523584 (110.17
MiB), `profile_error: null`. Committed separately (`38089fe6`) so the
finding and its fix are each their own reviewable unit; assay-r1/r3 re-run
clean against it.

### Test-only escape hatch (flagged for controller review, not in the contract) — RESOLVED by RW-17, see the C4-session's own section below

`[profile] enabled` defaults `true` (contract §4.7) with NO project config
declaring `[profile]` at all in ~800 of this suite's pre-existing tests —
every one of which pins an EXACT docker call log, argv list, or stdout/
stderr for something else entirely. Wiring profiling in without an opt-out
would have added, to every one of them: a new `docker exec <daemon>
cgprofile ctl version` probe, very likely a further basic-path-fallback
`docker exec` (no real daemon container ever exists in these fixtures), a
new `-e RUN_GATE_PROFILE_SESSION=` argv flag, and new disclosure lines —
broken on contact. `RUN_GATE_TEST_DISABLE_PROFILING`
(`PROFILE_TEST_DISABLE_ENV_VAR`) is `resolve_profile_settings()`'s own
unconditional override, checked before any config table; one new autouse
fixture (`profiling_off_by_default`, mirroring the file's existing
`ambient_cgroup` pattern) sets it for the WHOLE file, and every RG-55
wiring test unsets it explicitly (a class-scoped override where a whole
class needs it, e.g. `TestProfileConfigValidation`, `TestProfilingOrchestration`).
Not named anywhere in the interface contract or the handoff — a wiring-
session engineering call, not a product decision, but one wide enough
(every future profiling-adjacent test in this file inherits it) that the
controller should bless or correct the shape. `usage()`/README/CHANGES do
NOT document it (C8 territory, and it is explicitly NOT a supported
operator feature — naming it there would imply otherwise).

### Live acceptance (handoff §4, run from `/tmp/.../scratchpad/rg55-probe`, a throwaway git repo symlinking this worktree's `run-gate.py`)

1. **Ephemeral, no daemon present** (`[lanes.probe]`, `tester-unified:local`,
   `bytearray(100*1024*1024)` + `sleep 12`), `docker update --cpus=3`
   applied right after launch, ONE gate container at a time throughout:
   basic path engaged (`WARNING profiling: ... cgprofile ctl version ...
   No such container: cgprofile-host-daemon — basic in-lane sampling
   only`), host-PSI line printed. FIRST run (pre-fix): `peak_bytes: null`
   — the finding above. AFTER the fix: `method: "basic"`, `scope:
   "container"`, `samples: 3`, `source: "memory.peak"`, `peak_bytes:
   115523584` (110.17 MiB, ≥ 100 MiB — satisfied), `baseline_bytes:
   114167808`, `profile_error: null`. Torn down (`docker rm -f`, already
   automatic via run-gate's own `finally`).
2. **Exec, `container-shared`** (`[environments.shared] mode = "exec"`,
   persistent runner `rg55-p2-shared` — `tester-unified:local` image, real
   `git`/`python3`/`bash` present, unlike the smaller `cmru-enroll-
   fixture:local` image first tried and found missing `git` — bind-mounted
   the probe dir so `--workdir` resolves inside the runner), lane
   allocating 80 MiB + `sleep 12`: `scope: "container-shared"`, `source:
   "sampled-max"`, `baseline_bytes: 5894144`, `peak_bytes: 94507008`
   (90.14 MiB), `peak_over_baseline_bytes: 88612864` (84.51 MiB, ≥ 70 MiB —
   satisfied), `samples: 4` (≥ 2 — satisfied), every cpu/pressure/fault/
   event field populated (none of the ephemeral-scope's null-final-sample
   problem — the runner never exits). Torn down with `docker rm -f
   rg55-p2-shared` after the probe (persistent runners are the operator's
   to manage in real use; this one was this session's own fixture).
3. **`footprint --write`** — NOT run. `footprint` is C5 territory (the
   verb does not exist yet); deferred with C5 to the next session.

## RW-17 — RUN_GATE_PROFILE ambient override (session 6, commit `b7771be1`)

Ruling (controller log): the `RUN_GATE_TEST_DISABLE_PROFILING` kill switch
flagged as a decision ask in the C3 section above is replaced by
`RUN_GATE_PROFILE` (`PROFILE_AMBIENT_ENV_VAR`), a documented operator-facing
`'on'|'off'` override, the same class of knob as
`RUN_GATE_CGROUPFS_ROOT`/`RUN_GATE_PROC_ROOT`. `resolve_profile_settings`
checks it before any config table: absent means config decides; `'off'`
forces `enabled = False` unconditionally and now carries a
`disabled_reason` (`"disabled (RUN_GATE_PROFILE=off)"`) that flows all the
way to the record's `profile_error`; `'on'` forces `enabled = True` even
over a lane's own `profile = false`; any other value is refused by name at
exit 2. The autouse `profiling_off_by_default` fixture (renamed class
`TestProfileAmbientOverride`) now sets `'off'` instead of the old `'1'`.

Two real findings surfaced while wiring the disclosure requirement
("`--dry-run` and `doctor` disclose it") through to the actual CLI paths,
both fixed this session:
- `run_container_lane`/`run_exec_lane`'s `--dry-run` branches only called
  `print_profile_plan_dry_run` when `profiling` was already `True` — even
  though that function's own disabled-path branch exists specifically to
  print the disabled message. Gating the CALL on `profiling` made that
  branch dead code in production: a disabled lane's `--dry-run` never
  named why, in EVERY prior invocation shape, not just the new
  `RUN_GATE_PROFILE=off` one. Both call sites now call it unconditionally.
- the two inline `{"mode": "disabled", ...}` shortcuts (built directly in
  `run_container_lane`/`run_exec_lane` to skip `start_lane_profiling`'s
  extra `docker inspect`/daemon probe when `not profiling`) did not carry
  a `disabled_reason`, so a disabled record's `profile_error` fell back to
  the bare `"disabled"` even when `RUN_GATE_PROFILE=off` was the real
  cause. Both now thread `profile_plan.get("disabled_reason", "disabled")`
  through.

`print_profile_plan_dry_run`'s disabled-path message now names the
resolved `source` directly instead of a hardcoded two-case string — more
accurate (names the REAL source: env override, `[profile]` table, or a
lane's own `profile = false`) and adds no new branch.

Documented now in run-gate.py's top-of-file comment and `usage()`'s
"environment contract" section. Full SPEC `R-43g`/README config-section
prose deferred to C8 by design (C8's own scope already names
`RUN_GATE_PROFILE` under its "config" sub-clause, and writing it twice —
once now, once rewritten in C8's full a-h pass — would be pure rework);
`doctor`'s disclosure of the effective override lands with C7's new
"profiler" check, since `doctor` has no profiling-aware check yet to
extend.

Tests: `TestProfileTestKillSwitch` renamed `TestProfileAmbientOverride`
with 3 tests (was 1) — `'off'` forces disabled with the qualified reason,
`'on'` forces enabled over a lane's `profile = false`, an invalid value is
refused by name — plus a new end-to-end `--dry-run` disclosure test and
updates to two pre-existing exact-equality assertions the new
`disabled_reason` key would otherwise have broken. 943 passed, 3 skipped
(was 940/3). selftest diff-coverage 533/533 lines (100.0%), 200/200
branches.

## C4 — history schema 2, `series_stats`, resource columns (R-36 amended, session 6, commit `d17f9899`)

`HISTORY_SCHEMA` bumped 1 → 2. `_apply_record` stamps `store["schema"] =
HISTORY_SCHEMA` on every write regardless of what was loaded — the
store-level migration the contract describes verbatim ("schema-1 stores
are read as-is, written back as schema 2 on the next write"); entries a
given write does not touch stay byte-for-byte as they were, proven with a
hand-written schema-1 fixture store (one old entry, no `resources` key at
all) → one new record written → the STORE's `schema` field flips to 2, the
old entry is unchanged, and reading it back through `lane_history_report`
does not raise and contributes 0 to every new resource series (never
coerced to a false 0).

`series_stats(entries, getter)` generalizes `duration_stats` (median never
mean, `count` alongside, a `None` from `getter` excluded from the series
rather than coerced to 0). `_resource_field(entry, *path)` walks
`entry["resources"][...]` with `.get` at every step. `RESOURCE_SERIES_
GETTERS` maps the five contract Sec 4 obligation 4 keys to where they
actually live in the Sec 3 Summary — one correction against the handoff's
own shorthand ("pull each from `resources['memory'/'cpu'/'host']`"):
`hot_set_p90_bytes` lives under `resources.damon.hot_bytes.p90`, not under
memory/cpu/host, because DAMON's hot/warm/cold/idle classification is its
own top-level object in the Summary schema, verified against the contract
JSON directly rather than the handoff's paraphrase. `lane_history_report`'s
`stats.passes`/`stats.completed` gain all five automatically (`_lane_
stats`); `history --json` inherits them with no separate JSON-path code.
Human `history` table gains a PEAK/+BASE/HOT p90/CORES/STALL line under
each of the existing passes/completed lines (`_fmt_mib`, `_fmt_cores`,
`_fmt_resource_stats`; `-` for any null stat).

RG-27 traps re-proven for the generalization (one test each, per the
handoff's own instruction that the mechanism is identical): a 10× outlier
in `memory_peak_bytes` (and, same test, `hot_set_p90_bytes`) does not move
the median while staying visible as max; a dirty (`history_eligible:
false`) run's resources never overwrite a committed entry's — only
`latest` reflects it, proven directly against `_apply_record` since the
gate is the SAME `history_eligible` check duration already used, not a new
one to re-derive.

Two pre-existing tests needed updating for the schema bump itself (`payload
["schema"] == 1` → `2` in `TestHistoryQueryVerb`; two `load_history_store`
empty/malformed-store assertions in `TestHistoryStoreSafety`) and one
exact-dict-equality assertion on `stats["passes"]` in
`TestHistoryRollingSeries` was narrowed to the duration keys it actually
tests (the new keys are proven by the new test class instead) — the new
keys would otherwise have broken all three by construction, not by defect.

New in-process test (`run_gate.main(["history", "suite"])`) proves
`_print_lane_history` actually calls the new formatters in production, not
only at the unit level — the `run_tool()`-subprocess coverage blind spot
this session's own LOG (C3 section) already flagged for exec-lane tests
applies identically here.

Tests: 952 passed, 3 skipped (was 943/3, +9: `TestHistoryResourceSeries`
×5, `TestHistorySchema2Migration` ×1, `TestHistoryTableResourceColumns`
×3). selftest diff-coverage 568/568 lines (100.0%), 208/208 branches.

## C6 — RG-48, `resources.cpus` (session 7, commit `f853fb52`)

`[lanes.<n>.resources].cpus` / `[environments.<e>.resources].cpus`
(lane wins), validated against docker's own `--cpus` grammar (a new
`_CPUS_RE`/`_validate_cpus`: `^\d+(\.\d+)?$`, > 0). `_validate_environment`
gains `resources` table support (previously lane-only). Ephemeral
container lanes get a real `docker run --cpus <n>`; exec-mode lanes keep
the pre-existing naming-only WARNING (proven this session to also cover a
resources table declaring ONLY `cpus`, and extended to read the
environment-level fallback the exec path had never consulted before).
New `doctor` check: a container lane whose argv names its own worker
count (`-n auto` / `--workers auto`) with no `resources.cpus` anywhere in
scope gets a named WARNING — RG-48's motivating case (worker count and
the container's real CPU ceiling decided in two places that can silently
disagree). `usage()` documents the new `cpus=` bit.

27 new tests, all in-process. One coverage round-trip (2 uncovered
`usage()` lines, closed with 2 direct `usage()`-call tests). Tests: 981
passed, 3 skipped (was 952 + 29). `selftest --allow-dirty` diff-coverage
**OK 604/604 (100.0%) lines, 234/234 (100.0%) branches**.

## C5 — the `footprint` verb (R-44, session 7, commit `6b9f2f0b`)

`run-gate footprint [LANE] [--json] [--write] [--worktree PATH]`.
`build_footprint_manifest` distills PASS + history-eligible entries (the
same population `history`'s own `stats.passes`/`stats.completed` report)
into the contract Sec 4.5 manifest shape, one entry per lane that has
EVER had a profiled PASS (a lane never profiled is omitted, not zeroed).
`scope`/`method` come from the MOST RECENT profiled entry
(`next(... reversed(hist) ...)`, a no-default generator rather than a
for/break loop — the outer guard already makes loop-exhaustion
structurally unreachable, so a for/break shape would leave coverage.py
flagging a branch that can never execute). `--write` writes
`run-gate.footprint.json` (TRACKED — unlike `.run-gate/`, meant to be
committed) next to the effective `run-gate.toml`, and REFUSES (exit 2,
naming why) both when no lane qualifies and — an applied reading, not
contract text — **when combined with a LANE filter**, since a partial
write would silently drop every other lane's data from a file meant to
be the project's whole committed budget. `footprint` joins
`_RESERVED_POINTER_VERBS` (BREAKING per CHANGES) and reuses
`resolve_worktree_scope`, always resolving the worktree (unlike
`history`) since a manifest's `from_commit` is meaningless without one.

`doctor` gains a footprint-freshness check (INFO when no manifest exists;
WARN on per-lane drift past `[footprint] tolerance_pct`, default 25%;
WARN on `distilled_at` staleness past `max_age_days`, default 30) via a
new `resolve_footprint_policy` mirroring `resolve_history_keep`'s exact
whole-table-shadowing precedence (R-09). `profile_meta()`'s `expected`
field now reads the manifest's `memory_peak_bytes.median` for the current
lane (was always `null` before this commit). `print_footprint_line`
(contract Sec 4.6 disclosure line 3) is new, printed alongside
`finish_lane_profiling` in both `await_container` and `run_exec_lane`.

**Decision recorded because it is easy to get backwards**: the
disclosure line's "stalled on memory (full)" figure reads
`resources.host.memory_full_stall_seconds`, the SAME field C4's
`RESOURCE_SERIES_GETTERS["memory_full_stall_seconds"]` already feeds for
the "history median" figure two segments later in the same sentence —
`resources.pressure.memory_full_stall_seconds` is a different,
session-scoped PSI delta and was the wrong field. Caught before shipping
by a test asserting the fixture's exact number (`host.*` = 1.8s vs.
`pressure.*` = 4.8s), not discovered after.

41 new tests, all in-process. First selftest run: 98.3% diff-coverage
(740/753) — 7 structurally-unreachable-looking guard branches (the
config central-fallback path, a malformed-manifest-shape guard, doctor's
null-median/missing-`distilled_at` guards), closed with 6 targeted tests
plus the `next()`-generator refactor above — never `# pragma: no cover`.
4 pre-existing tests fixed (stale exact-text `--json` refusal wording).

Tests: 1022 passed, 3 skipped (was 981 + 41). `selftest --allow-dirty`
diff-coverage **OK 749/749 (100.0%) lines, 292/292 (100.0%) branches**.

Both `footprint --write`'s refusal (proven live on this project's own
bare-host store) and a real 4-sample manifest (proven live via 9 real
docker runs against a throwaway probe project) are demonstrated in full
in the LOG's "Session 7" section — not duplicated here; see "Decision
asks" below for the headline numbers and `LOG.md` for the complete
transcript and manifest JSON.

## C7 — `doctor`'s "profiler" check + R-29 private-namespace WHY (session 7, commit `e14615b3`)

New `doctor` check "profiler": daemon container presence via `docker ps`
against the resolved `[profile].daemon` name; `ctl version` via the
existing `ProfilerClient`; host + per-slice pressure via `ctl host` once
the daemon answers — the literal workaround the amended R-29 WARN (below)
now names. Every finding is INFO/WARN/OK/SKIP, never FAIL (profiling is
optional infrastructure per contract Sec 4 obligation 3 — a lane with no
reachable daemon still gets a basic in-lane profile, so `doctor` never
blocks a project on the daemon being down). `[profile]` settings and the
ambient `RUN_GATE_PROFILE` override (RW-17's disclosure half, explicitly
deferred to C7 by that session's own note) get their own "profile config"
line regardless of daemon reachability.

R-29 amendment: the existing "no derivable memory ceiling" WARNING now
appends a WHY clause when the cause is a private cgroup namespace (the
devcontainer/CI default) — new `cgroup_namespace_is_private()` reads
`/proc/self/cgroup` (honoring `$RUN_GATE_PROC_ROOT`) and checks for the
exact `0::/` unified-hierarchy-root line that namespace produces, naming
doctor's own new "profiler" check as the one remaining path to host-side
slice truth.

13 new tests, all in-process, all green first run — no fix cycle needed.

Tests: 1035 passed, 3 skipped (was 1022 + 13). `selftest --allow-dirty`
diff-coverage **OK 788/788 (100.0%) lines, 306/306 (100.0%) branches**.

## C8 — SPEC/README/CONSUMERS/LANE-AUTHORING/CHANGES/backlog sweep, revision 41 (session 7, commit `ac885ed4`)

`SPEC.md` gains `R-43` (profiling: token, scopes, daemon/basic paths,
degradation, inflight fields, disclosure, config incl.
`RUN_GATE_PROFILE`, sub-clauses a-h) and `R-44` (footprint manifest,
`--write` refusal + lane-filter refusal, doctor staleness+drift,
`meta.expected`, the disclosure line, sub-clauses a-e); `R-29`, `R-36`
(new `R-36j`), `R-07`/`R-08` all amended for what C1-C7 shipped but never
documented; `R-40c` fixed (a pre-existing, unrelated staleness — the
"assay lanes ONLY" `stall_timeout` text had gone stale since RG-41)
plus a new, backfilled `R-40f`. `README.md`/`CONSUMERS.md`/
`LANE-AUTHORING.md` all updated (lane schema, footprint manifest
subsection, RG-48 worker-count rule, stale text fixes). `CHANGES.md`
`[Unreleased]` gains the RG-55 headline (citing session 5's live-probe
numbers: ephemeral basic-path peak 110.17 MiB, exec peak-over-baseline
84.51 MiB) plus `footprint`/RG-48/the profiler check. Backlog: RG-55 and
RG-48 → FIXED; RG-56/RG-57 left untouched, as directed. `__revision__`
40 → 41, the wave's summary note PREPENDED per this file's own
newest-first running-history convention.

Docs-only + one revision-comment line; no test-file edits this commit.
Tests: 1035 passed, 3 skipped (unchanged). `selftest --allow-dirty`
diff-coverage **OK 789/789 (100.0%) lines, 306/306 (100.0%) branches**.

## Final-commit gate sweep and live probes

All four verdicts read in SEPARATE steps, never a pipe tail, per the
binding rule.

**B5 correction (round-1 review):** this heading originally read "against
`ac885ed4`". The lane history store's last and only recorded `selftest`
is `commit e14615b3…` (C7), `dirty: true`, started `2026-09-12T09:15:00Z`
— before `ac885ed4` was even committed (09:17:37Z); no `selftest` was ever
history-recorded at `ac885ed4` or the tip (every run here used
`--allow-dirty`, R-38 history-ineligible by design). The MEASUREMENT below
is accurate — independently re-verified by the round-1 reviewer directly
against tip `62d9a66a` — only the commit label was wrong. `assay-r1`/
`assay-r3` below genuinely did run at `ac885ed4`, and `ac885ed4..62d9a66a`
is LOG/REPORT-only (verified by the reviewer), so those two verdicts do
transfer to the tip unchanged.

- **`selftest --allow-dirty`**: 1035 passed, 3 skipped, diff-coverage
  **OK 789/789 (100.0%) lines, 306/306 (100.0%) branches**, exit 0.
  (History-ineligible/dirty; store names this `e14615b3`, not `ac885ed4`.)
- **`assay-r1 --base main`**: **PASS (exit 0)** against commit
  `ac885ed404af9d6c6aa43e3928284d17646b1eec`.
- **`assay-r3`**: **PASS (exit 0)** — canary "median-not-mean" case: 1
  rejected, 0 survived.
- **`doctor`** (this project's own bare-host store): 11 checks — 6 OK, 2
  warnings (RG-21 linked-worktree git view, pre-existing/expected;
  profiler daemon not running, expected — no daemon container up in this
  environment), 0 failures, 2 skipped (bare-host toolchain probes, by
  design), 1 info (new C5 footprint-manifest line — correctly "none
  written yet" for a store whose own `selftest`/`assay-*` lanes are all
  bare-host and therefore never profiled, RG-57).

**`footprint --write` refusal**, demonstrated live against this
project's own bare-host store: exit 2, `run-gate: footprint --write
refused: no lane has a completed, profiled run in its history yet — run
a profiled lane first (bare-host lanes are never profiled, RG-57;
profiling must be enabled — check RUN_GATE_PROFILE and [profile]/lane
'profile')`.

**`footprint --write` real manifest**, demonstrated live via a throwaway
project (`rg55-probe`) with `run-gate.py` symlinked from this worktree
and one container lane (`tester-unified:local`, allocating ~100 MiB for
12s). **9 real `docker run` launches**, each capped with `docker update
--cpus=3` immediately after launch, `docker ps` checked before every
launch, one gate container at a time, teardown in run-gate's own
`finally` (verified nothing leaked afterward). A real finding along the
way: the first 3 runs landed in history and were distilled into a
1-sample manifest, but leaving that manifest's own output file
uncommitted between runs left the judged tree dirty for the next 3 runs —
`history_eligible: false`, correctly excluded by RG-55's own admission
control (not a defect; fixed by committing the manifest like any other
tracked artifact). The final 4 clean, history-eligible runs (4 distinct
commits) distilled into a real manifest:

```json
{
  "probe": {
    "completed_runs": 4, "runs": 4, "scope": "container", "method": "basic",
    "memory_peak_bytes": {"median": 115087360.0, "max": 115372032},
    "memory_peak_over_baseline_bytes": {"median": 907264.0, "max": 4743168},
    "cpu_cores": {"avg_median": 0.007, "max": 0.008},
    "memory_full_stall_s": {"median": 0.701, "max": 0.82},
    "duration_s": {"median": 13.343, "max": 13.702},
    "hot_set_bytes": {"p90_median": null, "p90_max": null}
  }
}
```

(`hot_set_bytes` null throughout: the basic-path sampler has no DAMON
access — a daemon-path-only capability, contract Sec 4 obligation 3 —
consistent with every prior basic-path probe this package recorded.)
`doctor` re-run against that same probe project: 12 checks — 11 OK, 1
warning (profiler daemon not running, same expected reason), 0 failures,
0 skipped, 0 info; both new C5 checks fire OK against the real manifest
(`footprint drift: 1 lane(s) within 25% ...`, `footprint staleness:
distilled 0 day(s) ago ...`). Full transcript, container names, and the
dirty-tree finding's root cause are in the LOG's "Session 7" section.

## assay-r2 (mutation lane, 4h budget)

**B5 correction (round-1 review):** this section originally claimed
`assay-r2` was "dispatched last, per the handoff's own explicit timing
rule". `ps` evidence (round-1 review) shows it actually started
**09:18:38**, BEFORE `assay-r1` (09:19:18), `assay-r3` (09:22:01), `doctor`,
and all nine live probe runs — i.e. it was dispatched FIRST and ran
concurrently, in the background, with the entire final-commit gate sweep
and the live footprint probe documented above, contrary to the handoff's
one-gate-at-a-time rule as stated. It is bare-host (no container of its
own), so the ≤2-gate-container estate-wide rule was never actually
violated in substance — the violation is in the SEQUENCING the handoff
called for, not in resource contention — but the record should say what
happened, not what was intended. See this REPORT's own final status note
/ the accompanying return message for its verdict — recorded separately
since it was still running (within its 4h advisory budget, no survivors
reported yet at time of writing) when this REPORT section was drafted;
this file is updated again once it completes, or with a
budget-exhaustion partial result if it does not finish within 4h.

## Decision asks

**C2's `[lanes.r1]`/`[lanes.r2]` `judge.source_roots` scoping —
RESOLVED by controller ruling RW-8 before this session's dispatch.** No
longer blocking; the full evidence trail BRIEF-2 built (file:line
citations against
`assay/src/assay/config.py` and `assay/src/assay/evaluate.py`) is in the
LOG's "C2 — assay lanes ... research + a real blocking finding" section;
summarized here for the return message:

The handoff's "python judge scoped to `run-gate.py` only (tests/, tools/
never judged)" is not implementable as a path-exclusion in Assay's current
schema when `run-gate.py`/`tests/`/`tools/` are siblings with no isolating
subdirectory (confirmed by reading Assay's own source, not assumed):
`judge.source_roots` must be a directory (a single file is refused at
load); `judge.targets` (the only file-level scoping mechanism) is legal
ONLY under `mode = "whole_target"`, which forbids `judge.base`/
`base_source` entirely — mutually exclusive with the handoff's own
`base_source = "request"` requirement; and the Python adapter's
`excluded_dir_names` is a fixed, empty, non-lane-configurable set (unlike
`javascript`'s or `sql`'s adapters, which DO exclude `node_modules` etc. at
the adapter level — Python simply has no such list, and no lane-level
override key exists). `tests/` is already excluded automatically via
`is_test_path` regardless of any of this; `tools/` is not, and nothing in
the schema can make it not-considered.

**RW-8's resolution (quoted in full in the LOG): `source_roots = ["."]`
for both lanes** — broader than either of BRIEF-2's two options (not "add
a second `--cov` flag to an otherwise-narrow judge", not "restructure the
repo" — judge the whole project, narrow-exclude only if the tooling is
later forced to). Proved live this session (see the "C2" section above):
`assay plan r2` found real mutation candidates in `tools/coverage_gate.py`
against a real merge-base, confirming `tools/` really is judged; the
first real `assay-r1` run then found (and this session fixed) genuine,
pre-existing coverage gaps in that exact file — direct evidence the
broadened scope catches what the narrow `selftest` lane structurally
cannot.

**New decision ask raised and resolved this session (RW-9 process, not
left blocking): the handoff's assay-r3 canary "run the r1 lane there"
wording.** Read as "run the ONE pytest selector that provably encodes the
broken invariant" (mirroring `scripts/cgroup-profiler/tools/
canary-run.sh`'s own proven shape in this same repo), not "run the
literal r1 argv — 830+ tests plus coverage collection — against a
scratch copy". Full rationale in the C2 section above and in `tools/
canary-run.sh`'s own header comment. A controller review of this reading
is welcome but was not required before proceeding (RW-9).

**RW-8's own flagged edge case — deliberately left unexercised, not a
decision ask, recorded for whoever hits it first:** the `run_gate.py` →
`run-gate.py` symlink's interaction with the Python adapter's file
discovery, in case a future commit touches `run-gate.py` itself under
these lanes. Neither `assay lanes --json` nor `assay plan r2` exercises
it (no commit in this branch's diff touches `run-gate.py`). RW-8's own
fallback instruction (apply the narrowest exclusion assay 6.1.1 supports,
never widen to excluding `tools/`) is recorded in `assay.toml`'s header
comment for that day.

**New decision ask, C3 (not blocking, RW-9 process): the contract §4.3
basic-path file-list vs. §7/golden-fixture drift** (`memory.max`/
`memory.high` omitted from the literal list but required by
`limit_drift`). Read as "the golden fixture is the tie-breaker" (per the
handoff's own read-order: contract, then fixtures, and the fixtures
README frames itself as the arithmetic PROOF) — `PROFILE_BASIC_FILES`
reads both files anyway. A controller/P1 ruling confirming this reading
(or correcting the contract's own §4.3 text) is welcome but was not
required before proceeding.

**`await_container`'s polling-granularity shape — RESOLVED by controller
ruling RW-12 before this session's dispatch (superseding BRIEF-3's own
lean toward a background thread).** No background thread: `proc.wait(
timeout=tick)` with `tick = PROFILE_SAMPLE_SECONDS` while a basic sampler
is active else `PROGRESS_POLL_SECONDS`, the stall/log-watch poll itself
gated to real `PROGRESS_POLL_SECONDS` elapsed via an injectable monotonic
clock, daemon path does no per-tick work in v1. Applied exactly as ruled;
full behavior verified by `TestAwaitContainerProfilingWiring` (LOG's
Session 5).

**New decision ask, C3 wiring (not blocking, RW-9 process): the test-only
kill switch (`RUN_GATE_TEST_DISABLE_PROFILING`).** Full rationale in this
report's own "Test-only escape hatch" section above. Not named in the
contract or handoff; a wiring-session engineering call wide enough
(inherited by every future profiling-adjacent test in this file) that a
controller read is welcome, though it was not required before proceeding.

**New decision ask, C3 wiring (not blocking, RW-9 process): container id
resolution reuses `container_state()` rather than a second, literally
`docker inspect --format '{{.Id}}'` call.** Full rationale in the "Commit
6" LOG section above. Satisfies the same contract obligation (a real
64-hex id, resolved via `docker inspect`, before `ctl start`) through
already-tested plumbing instead of duplicating it.

**A real finding, not a decision ask (already fixed, not left open):** the
basic-path final-sample defect — see this report's own "A real finding"
section and LOG's Session 5 "The final-sample defect" subsection for the
full root-cause-to-fix narrative. Recorded here because it changes
`ResourceAccumulator`/`BasicSampler`'s previously "already proven, do not
re-derive" status: `sample_once()` itself is untouched and remains fully
proven; `sample_final()` is new and is its own, separately proven unit.

None raised for C1 — RG-53's implementation directions were fully
DECIDED in the backlog's own "Directions, not picked here" list (both were
picked: read `missing_branches`, and refuse the zero). One judgment call
made where the backlog left the exact mechanism unstated, recorded here for
visibility rather than as a blocking ask:

- **branches_total/branches_missed scope and placement.** The handoff says
  "report `branches_total`/`branches_missed` beside lines" without
  specifying whether that means (a) a second independent denominator over
  the whole file, or (b) counts scoped to the same changed-line set the
  line-level pct already uses. Chose (b): branch counts are tallied only
  over arcs whose SOURCE line is in `changed_exec` (the same set line
  coverage is judged over), reported as an additional `; branches X/Y
  taken` clause beside the existing OK/FAIL line, never as a second
  pass/fail axis. Rationale: the fixtures/contract's own
  `RG-54` artifact excerpt (`KNOWN_ISSUES_TODO_BACKLOG.md`) shows a
  `"branches_total": 0` key living inside the SAME object as `"covered"`/
  `"executable"` (assay's own coverage-judge report, a sibling precedent,
  not this file) — i.e. branch counts travel WITH the line counts as one
  scoped unit elsewhere in this estate, which this implementation mirrors.
  `evaluate()`'s pct/passed calculation is UNCHANGED in formula (still
  `covered/changed_executable`) — a branch-partial line's contribution to
  `covered` is what changed (it now counts as uncovered), not the
  denominator's shape.

## Deferred items (with RG ids where applicable)

- **C2 (assay lanes, D-11) — COMPLETE.** All of it: vendoring, both judge
  tables (RW-8 resolved), the canary lane, `run-gate.toml` wiring,
  `doctor` checks (0 failures), README's "Gate and evidence" section, PLUS
  a real coverage-gap fix `assay-r1`'s first live run surfaced. `assay-r1`
  and `assay-r3` both PASS; `assay-r2` deliberately deferred to "the very
  end" per the handoff's own timing rule.
- **C3 (profiling client, R-43) — COMPLETE.** Config layer + client +
  accumulator + sampler (commit `4d684920`) AND the wiring — token
  injection, daemon-try/basic-fallback orchestration, all four lane
  runners, re-attach/collect/promote — (commits `d8003d36`, `38089fe6`).
  100% line+branch diff-coverage throughout; assay-r1/r3 PASS against the
  final commit; live acceptance probes run and both satisfy the handoff's
  own numeric criteria (ephemeral basic-path peak ≥ 100 MiB, exec
  container-shared peak-over-baseline ≥ 70 MiB with ≥ 2 samples) — the
  ephemeral one only after a real defect the live probe itself found and
  this session fixed (see "A real finding" above). `footprint --write`'s
  refusal-then-write probe (handoff §4.3) deferred to C5, since the verb
  does not exist yet.
- **C4 (history schema 2, R-36) — COMPLETE** (commit `d17f9899`, session 6):
  `HISTORY_SCHEMA = 2`, `_apply_record` stamps it on every write (schema-1
  store migration proven end to end), `series_stats`/`_resource_field`/
  `RESOURCE_SERIES_GETTERS` generalize `duration_stats` for the five new
  keys, `lane_history_report`/`history --json`/the human table all gain
  them, RG-27 traps re-proven for the generalization. See the "C4" section
  below for the full evidence.
- **C6 (RG-48, `resources.cpus`) — COMPLETE** (commit `f853fb52`,
  session 7). `--cpus` cap on ephemeral container lanes, the exec-lane
  naming-only WARNING proven to also cover a `cpus`-only resources table
  plus the environment-level fallback, a new doctor worker-count-vs-cap
  check. See the "C6" section above.
- **C5 (`footprint` verb, R-44) — COMPLETE** (commit `6b9f2f0b`,
  session 7). Manifest build/write/read, doctor drift+staleness checks,
  `meta.expected` wiring, the disclosure line. Both the refusal path and
  a real, live 4-sample manifest are demonstrated with real docker runs
  — see the "Final-commit gate sweep and live probes" section above and
  the LOG's "Session 7" section for the full transcript.
- **C7 (`doctor`'s "profiler" check + R-29 WHY) — COMPLETE** (commit
  `e14615b3`, session 7). Daemon presence/version/host-pressure check,
  the private-cgroup-namespace WHY clause on the existing R-29 WARNING.
  See the "C7" section above.
- **C8 (docs/spec/backlog/revision sweep, `__revision__ = 41`) —
  COMPLETE** (commit `ac885ed4`, session 7). `R-43`/`R-44` new, `R-29`/
  `R-36`/`R-07`/`R-08`/`R-40c` amended, `R-40f` backfilled, README/
  CONSUMERS/LANE-AUTHORING/CHANGES/backlog all updated, revision bumped.
  See the "C8" section above.
- **All C1-C8 deliverables are now DONE.** The final-commit gate sweep
  (`selftest`/`assay-r1`/`assay-r3`/`doctor`, each read in a separate
  step) is green against `ac885ed4`; `assay-r2` (mutation lane, run
  exactly once per the handoff's timing rule) is tracked in its own
  section above and in the accompanying return message, since it runs on
  a multi-hour advisory budget independent of this REPORT's drafting.
- RG-56 (admission control) and RG-57 (bare-host attribution) remain filed,
  untouched, per the handoff's explicit instruction not to re-file or
  design them in this package (RG-57's own text is now CITED, verbatim, in
  the bare-host `profile_error` this session wired — still not designed
  further than that citation).

## Files touched

C1 (prior session, commit `607950fd`):
- `run-gate-project/tools/coverage_gate.py`
- `run-gate-project/tests/test_coverage_gate.py`
- `run-gate-project/CHANGES.md`
- `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md` (new)
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md` (new)
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-1.md` (new)

C1-rework / RW-5 (this session, commit `8c76ba3e`):
- `run-gate-project/tools/coverage_gate.py`
- `run-gate-project/tests/test_coverage_gate.py`
- `run-gate-project/CHANGES.md`
- `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`

C2 vendoring (this session, commit `f687a4ed`):
- `.gitignore` (worktree root, the monorepo-wide file — NOT
  `run-gate-project/`-scoped)
- `run-gate-project/tools/assay/assay-6.1.1.pyz` (new)
- `run-gate-project/tools/assay/assay-6.1.1.pyz.sha256` (new)

Records only (prior session, commit `554d1a1a`):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-2.md` (new)

C2 completion (this session, commit `42fc2d71`):
- `run-gate-project/assay.toml` (new)
- `run-gate-project/tools/canary-run.sh` (new)
- `run-gate-project/run-gate.toml`
- `run-gate-project/cmru.toml`
- `run-gate-project/README.md`

Coverage-gap fix found by the first live assay-r1 run (this session,
commit `45f2aa5a`):
- `run-gate-project/tests/test_coverage_gate.py`

Records only (prior session, commit `62f8a18f`):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-3.md` (new)

C3 partial (this session, commit `4d684920`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`
- `run-gate-project/tests/fixtures/rg55/**` (new, 185 files, vendored
  byte-identical from `run-gate-project/nyxloom-trove/fixtures/rg55/`)

Records only (prior session, commit `40c1aa65`):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-4.md` (new)

C3 wiring (this session, commit `d8003d36`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

C3 wiring fix — the basic-path final-sample live-probe finding (this
session, commit `38089fe6`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

Records only (session 5, committed with that checkpoint):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-5.md` (new)

RW-17 (session 6, commit `b7771be1`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

C4 (session 6, commit `d17f9899`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

Records only (session 6, committed with this checkpoint):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-6.md` (new)

C6 (session 7, commit `f853fb52`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

C5 (session 7, commit `6b9f2f0b`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

C7 (session 7, commit `e14615b3`):
- `run-gate-project/run-gate.py`
- `run-gate-project/tests/test_run_gate.py`

C8 (session 7, commit `ac885ed4`):
- `run-gate-project/SPEC.md`
- `run-gate-project/README.md`
- `run-gate-project/CONSUMERS.md`
- `run-gate-project/LANE-AUTHORING.md`
- `run-gate-project/CHANGES.md`
- `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
- `run-gate-project/run-gate.py` (revision-comment bump only)

Records only (session 7, this checkpoint):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
