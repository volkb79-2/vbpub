# run-gate-WAVE-RG55-P2 — REPORT (partial: C1, C1-rework, C2 all DONE; C3-C8 deferred)

Package P2 of the RG-55 wave. This REPORT covers deliverable C1 (RG-53)
and its RW-5 rework (DONE), and C2 (assay-r1/r2/r3 + gate-full lanes,
DONE — RW-8 resolved the judge-table scoping decision BRIEF-2 left
blocking); C3-C8 are deferred — see `run-gate-WAVE-RG55-P2-BRIEF-1.md`
(first predecessor's brief), `run-gate-WAVE-RG55-P2-BRIEF-2.md` (second
session's continuation brief, incl. the now-resolved C2 decision ask's
full evidence trail), `run-gate-WAVE-RG55-P2-BRIEF-3.md` (this session's
continuation brief for C3-C8), and `run-gate-WAVE-RG55-P2-LOG.md` for the
commit-by-commit record.

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

**Open, unmade decision carried forward from BRIEF-3, NOT resolved this
session (deferred to the wiring session, C3's remainder):**
`await_container`'s polling-granularity shape — shrink the shared
`proc.wait(timeout=...)` interval when profiling is active (option 1) vs.
a separate background-thread sampler mirroring `LogStreamWatch` (option
2). BRIEF-3's own lean favors option 2 (lower regression risk against
`await_container`'s heavily adversarially-reviewed stall/re-attach logic);
this session did not need to decide it since no wiring was attempted.

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
- **C3 (profiling client, R-43) — PARTIAL.** Config layer, `ProfilerClient`,
  `ResourceAccumulator` (proven byte-exact against the golden fixtures),
  `BasicSampler`, and `generate_profile_token()` are DONE and gate-verified
  (commit `4d684920`, 100% line+branch diff-coverage, assay-r1/r3 PASS).
  NOT done: wiring any of it into `await_container`/`run_container_lane`/
  `run_exec_lane`/`run_bare_host_lane`/`follow_container`/
  `resolve_inflight` — five of the most heavily adversarially-reviewed
  functions in this file, per `__revision__ = 40`'s own changelog — plus
  disclosure lines (contract §4.6), `--dry-run` profile-plan text, and the
  runtime consequence of `enabled: false`/lane opt-out (the config exists
  and is tested; nothing reads it at run time yet). See `BRIEF-4.md` for
  the concrete continuation state.
- **C4 (history schema 2, R-36)**, **C5 (`footprint` verb, R-44)**, **C6
  (RG-48, `resources.cpus`)**, **C7 (`doctor` profiler check)**, **C8
  (docs/spec/backlog/revision sweep, `__revision__ = 41`)** — not started;
  each depends on C3's schema/constants existing first.
- Live acceptance probes (handoff §4) — blocked on C3, not yet attempted.
- RG-56 (admission control) and RG-57 (bare-host attribution) remain filed,
  untouched, per the handoff's explicit instruction not to re-file or
  design them in this package.

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

Records only (this session, uncommitted at time of writing, committed
with this checkpoint):
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
- `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-4.md` (new)
