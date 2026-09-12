# run-gate-WAVE-RG55-P4 — implementer LOG

Package P4 (run-gate follow-ups: RG-57, RG-58, RG-59, RG-60, RG-61) of the
RG-55 wave, third track (RW-27). Fresh Sonnet implementer, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate` (branch
`rg55-followups-run-gate`), HEAD at dispatch `186461de` (the P2 package's
ACCEPTED close-out tip). Handoff:
`/workspaces/vbpub/run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-HANDOFF.md`.

## Orientation

Read, in order: the handoff (full); the controller log at the WORKTREE's
own base (`186461de`, stops at RW-23/the P1+P2 dispatch table) — then,
finding the handoff cites rulings (RW-24, RW-26, RW-27) not present at that
base, re-read the CURRENT controller log from the main checkout
(`/workspaces/vbpub/run-gate-project/...`, 283 lines — commit `808560d3`),
which is where RW-24..RW-27 actually live (the log file itself moved ahead
of this package's base tip on `main` between P2's close-out and this
dispatch); backlog entries RG-57..RG-61 (full, lines 4356-4609);
`RG55-INTERFACE-CONTRACT.md` full (§1 transport/IO, §2 verbs, §3 Summary
schema, §4 run-gate obligations, §5 daemon safety, §7 computation rules
incl. the RW-21 absolute-counters subsection); SPEC.md R-39 (full,
re-attach), R-43/R-43a-h (full), R-44/R-44a-e (full); `run-gate.py`:
`_validate_lane` (377-560), `resolve_environment`/`resolve_profile_settings`
(795-830, 642-719), `ProfilerClient`/`ResourceAccumulator` (1017-1394),
`start_lane_profiling`/`tick_lane_profiling`/`finish_lane_profiling`/
`print_profile_warning`/`print_profile_session_line`/
`print_profile_plan_dry_run`/`print_footprint_line` (1632-1834),
`container_state` (2982-3034), `series_stats`/`RESOURCE_SERIES_GETTERS`/
`_lane_stats`/`build_footprint_manifest` (3080-3394), `cmd_doctor`'s
profiler section (5181-5269), `run_container_lane` (6596-6814),
`run_exec_lane` (6890-7062 pre-edit), `run_bare_host_lane` (7065-7105
pre-edit), `usage()` head (7117-7130). Cross-checked `cmd_doctor`'s actual
daemon-not-running WARN text against the backlog's RG-59 quote (they do NOT
match verbatim — the backlog quote is a paraphrase; treated the HANDOFF's
own literal text as authoritative, factored into one shared constant/
function both sites use, confirmed no existing test pins the OLD literal
wording beyond the substrings "not running"/"ciu up"). Read
`tests/test_run_gate.py`'s `TestExecLaneProfilingWiring`,
`TestAwaitContainerProfilingWiring`, `TestReattachProfilingWiring`,
`TestProfilerClient`, `TestDoctorProfilerCheck` classes (full) as the
fixture/helper vocabulary this package's own tests reuse (`fake_docker`,
`fake_docker_executing`, `set_cgprofile_plan`, `plant_inflight`,
`make_repo`/`make_history_repo`, `RG55_FIXTURES`/`RG55_CONTAINER_ID`).

Orientation call count: ~30 tool calls (reads + greps + two host-load/
worktree-creation bash calls) before the first edit.

Host load at dispatch: PID 2415767 (P2's `assay-r2`, bare-host) ALIVE;
container `run-gate-vbpub-r2-2315801-1789214565` (P1's r2) also present.
Per the handoff's binding HOST LOAD section: targeted pytest files only,
serial, `nice -n 19 ionice -c 3`, no whole-suite run and no assay lane
while pid 2415767 lives.

## Commit 1 — C1 (RG-60): exec-lane inflight record

`run_exec_lane` now writes the same inflight record `run_container_lane`
writes (schema/lane/container/container_id/started_at/started_epoch/
owner_pid/owner_start/boot_id/pid_ns/commit/worktree/project_dir/verdict/
progress/revision/profile_token/profile_daemon/profile_session), at the
point in its own lifecycle after the profiling session (if any) is
established and before the `docker exec` itself begins, cleared in the
SAME `finally` that finishes profiling (mirrors `run_container_lane`'s
RW-1 rule: the two facts a record asserts stop being true at the same
instant). Written UNCONDITIONALLY (profiled or not) — RW-1's rule that a
client can die mid-run regardless of `[profile]`.

**Decision-ask handled per RW-9** (recorded here, not left to stop the
package): `container_state()` (the real `docker inspect` that resolves the
persistent runner's numeric id) was, in the pre-existing code, called ONLY
when profiling is enabled — an ambiguous inspect failure there raises
`GateInfraError` (`fail_infra`), aborting the whole lane. A first cut of
this change called `container_state()` UNCONDITIONALLY so the inflight
record's `container_id` field would always be populated, which turned that
same `fail_infra` abort into a NEW failure mode for the profiling-DISABLED
case too (caught immediately: it broke the pre-existing
`test_run_exec_lane_tolerates_no_run_record` test, whose fake docker shim
has no `inspect` case at all because the original code never called it
when profiling was off). Reading R-04/R-36h's spirit ("a profiling-adjacent
fact-gathering step must never be able to fail a lane that was never going
to be profiled") as controlling here: reverted to calling
`container_state()` only inside the profiling-enabled branch, exactly as
before, and the inflight write now happens AFTER both branches converge,
using `container_id = ""` when profiling was disabled (or never attempted
a real inspect). This preserves the pre-existing failure semantics for the
profiling-enabled path byte-for-byte and adds no new fatal path for the
disabled one.

Not wired into `resolve_inflight`/re-attach (RG-60's own scope, per the
backlog entry: "the record's shape is already defined... this wires it
into the second lane kind that currently lacks it, not a new design" — a
later invocation's re-attach/promotion machinery stays container-lane-only
for this wave).

SPEC.md R-43a/R-43f updated to drop the "RG-60: an exec lane writes no
inflight record today" caveat now that it is fixed.

### Tests

`tests/test_run_gate.py`, new class `TestExecLaneInflightRecord` (3 tests):
- `test_record_exists_when_exec_begins_and_cleared_after` — a `Popen` spy
  confirms the record is ON DISK (right lane/container/profile_token) at
  the exact moment `docker exec` starts, and gone after the call returns
  (the oracle sketch's own first bullet, proven by disk state at the
  instant of the exec rather than trusted call order).
- `test_killed_client_leaves_the_record_on_disk` — plants a record via
  `write_inflight_record` directly with no matching `clear_inflight_record`
  call (the literal simulation of "the client died before its `finally`
  ran") and asserts it survives (the oracle sketch's second bullet).
- `test_record_written_even_when_profiling_disabled` — the record still
  exists mid-exec and is still cleared after, with `profile_plan.enabled =
  False` (this is what would have caught the decision-ask regression above
  had it been written before the first fix attempt; written after, as
  belt-and-suspenders regression coverage).

### Gate verdicts (targeted, host-load-constrained; full package gates run
at close-out per the handoff)

```
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestExecLaneInflightRecord or TestExecLaneProfilingWiring or \
        TestReattachProfilingWiring or TestAwaitContainerProfilingWiring" -q
23 passed, 969 deselected, 1 warning in 6.37s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "Inflight or inflight or ContainerLane or container_lane" -q
62 passed, 930 deselected, 1 warning in 8.00s
```

Commit: `a6716422`.

## Commit 2 — C2 (RG-59): live-run daemon-absent warning names the real cause

`ProfilerClient._ctl`'s `json.JSONDecodeError` branch (fired both when a
daemon container is absent/stopped — `docker exec` itself fails, stdout is
empty — and when a RUNNING daemon returns genuinely malformed stdout) now
tells the two apart by matching docker's own `stderr_tail` against
`_stderr_names_daemon_not_running()` (`"no such container"`/`"is not
running"`, case-folded per RG-44's own precedent — docker's wording casing
is not portable across hosts/versions). New shared function
`daemon_not_running_reason(daemon_name)` is the ONE place both
`cmd_doctor`'s "profiler daemon" WARN and `_ctl`'s live-run reason get this
text from, so the two surfaces cannot drift apart again. "produced
unparsable stdout" stays reserved for the genuinely-malformed-response case.

**Note on exact wording** (documented, not silently deviating): the
backlog's own quote of `cmd_doctor`'s "already good" text
("profiler daemon not running — basic in-lane sampling only (start it: cd
scripts/cgroup-profiler && ciu up)") does not byte-match the code as
written before this commit (`f"{daemon_name!r} not running — every lane
falls back to basic (in-lane) sampling (...)"`) — confirmed by reading the
actual source, not just the backlog prose. Treated as a paraphrase.
`daemon_not_running_reason()`'s own text — `"'<name>' not running (start
it: cd scripts/cgroup-profiler && ciu up)"` — carries the same cause and
the same remedy in both places; `print_profile_warning`'s existing
`{reason} — {suffix}` composition (suffix = `"basic in-lane sampling
only"` for the live-run line) means embedding that suffix INSIDE the
shared reason string too would have printed it twice on the live-run line,
so the shared string holds the cause+remedy only and each call site
supplies its own trailing clause.

### Tests

`tests/test_run_gate.py`, `TestProfilerClient` (3 new tests, direct
`subprocess.run` monkeypatches rather than the shell-shim fixture — the
shim has no stderr-injection knob and adding one would touch shared
fixture infrastructure many other tests depend on):
- `test_daemon_not_running_names_the_real_cause` — docker's real
  "... is not running" stderr, empty stdout → reason names "not running"/
  "ciu up", never "unparsable".
- `test_no_such_container_also_names_the_real_cause` — the OTHER docker
  wording ("No such container") also matches.
- `test_malformed_stdout_from_a_running_daemon_keeps_the_wording` — a
  RUNNING daemon's genuine garbage stdout is NOT reclassified; "unparsable"
  stays, "not running" does not appear.

### Gate verdicts (targeted)

```
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestProfilerClient or TestDoctorProfilerCheck" -q
30 passed, 965 deselected, 1 warning in 3.92s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "profil or Profil" -q
139 passed, 856 deselected, 1 warning in 7.76s
```

Commit: `b5e4a9c6`.

## Commit 3 — C3 (RG-58): bare-host `stall_timeout` load-time + doctor WARN

RW-27a's ruling: D5's option 2 (warn, never refuse). New shared function
`bare_host_stall_timeout_inert_reason(lane_name)` — the ONE wording both
`_validate_lane` (a load-time `run-gate: WARNING ...` on every invocation
that loads a bare-host lane declaring `stall_timeout`) and `cmd_doctor`
(a new "2d" per-lane check, WARN severity, never FAIL) use. Pure string
comparison (`table.get("environment") == BARE_HOST_ENV`) — `bare-host` is
a reserved built-in name resolved directly by `resolve_environment`, no
`[environments.bare-host]` table lookup needed at validation time.
Config still loads; exit code unchanged; container/exec lanes untouched
(confirmed this project's own `run-gate.toml` already has no
`stall_timeout` on any of its bare-host lanes — RW-23b's prior fix — so
this ships with zero new noise on `./run-gate.py doctor` here).

### Tests

`tests/test_run_gate.py`, new class `TestBareHostStallTimeoutWarning`
(4 tests): load-time warning present exactly once (naming the lane and
"bare-host"), config still loads with the key intact; `doctor` WARN with
the SAME wording, exit code 0 (never FAIL); container-lane and exec-lane
configs declaring the same key produce NO such warning at load.

### Gate verdicts (targeted)

```
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestBareHostStallTimeoutWarning or TestStallTimeoutLaneKey or TestDoctorProfilerCheck" -q
21 passed, 977 deselected, 1 warning in 4.18s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestConfigValidation or Doctor or TestHostLane" -q
74 passed, 924 deselected, 1 warning in 9.81s
```

## Session 2 (fresh successor, picking up from BRIEF-1 at tip `e698835f`)

Orientation: BRIEF-1 (full — the distilled C4 design, absorbed verbatim per
its own "DROP: the full orientation-reading narrative" instruction, no
re-read of the controller log/backlog/contract from scratch), the P4-HANDOFF
(full), this LOG's Commits 1-3 (full), controller log RW-26/RW-27(a,b)/RW-28
(current `main`, commit `a9c6d72a` at time of read — confirmed `186461de`
still the tip of `rg55-run-gate-client`, NOT yet merged to `main`, so the
final gate base stays `--base 186461de` per the handoff's `else` clause, no
merge-base rewrite needed for C4/C5). Host load re-checked before every
pytest/gate command per the binding rule: PID 2415767 (P2's `assay-r2`) and
container `run-gate-vbpub-r2-2315801-1789214565` (P1's r2) both ALIVE
throughout this entire session — every pytest run this session was targeted,
serial, `nice -n 19 ionice -c 3`; no whole-suite run and no assay lane were
attempted at all.

Orientation call count: ~14 tool calls (the four required reads, a
`git log`/`git branch --is-ancestor` check for the P2-merge question, the
host-load re-check, and the `run_bare_host_lane`/`start_lane_profiling`/
`finish_lane_profiling`/`ProfilerClient`/`container_state`/
`build_footprint_manifest`/`cmd_doctor` reading pass) before the first edit —
far below BRIEF-1's own ~30-call orientation, because the brief's own design
section replaced almost all of the reading a from-scratch session would have
needed.

## Commit 4 — C4 (RG-57): bare-host lanes are profiled

Implemented BRIEF-1's design exactly as worked out, both sub-paths:

- **Daemon path**: new `resolve_self_container_id(docker)` (never raises,
  `ProfilerClient._ctl`'s own style — reads `/etc/hostname`, confirms via a
  DIRECT `docker inspect`, NEVER `container_state()` since that one calls
  `fail_infra()`/raises on an ambiguous docker failure) and new
  `start_bare_host_profiling(...)` (parallels `start_lane_profiling` but
  resolves the target via the self-id helper, scope is ALWAYS
  `container-shared`, and on ANY failure returns `mode: "rusage"` —
  NEVER constructs a `BasicSampler`, RW-27b's explicit rule). On `start`
  success the state is shaped identically to `start_lane_profiling`'s own
  return, so `finish_lane_profiling(state)` is reused VERBATIM for the
  daemon sub-case via the new `finish_bare_host_profiling` dispatcher. A
  bare-host-only disclosure line ("... is DEVCONTAINER-WIDE ...") follows
  the session line on the daemon path only, per the handoff.
- **Rusage path**: `resource.getrusage(RUSAGE_CHILDREN)` (stdlib, new
  `import resource` alongside the other stdlib imports) bracketed
  immediately around `run_bare_host_lane`'s own `subprocess.run`, delta'd
  in `finish_bare_host_profiling` into a hand-built schema-1 Summary:
  `method: "rusage"`, `memory.peak_bytes = ru_maxrss * 1024`,
  `memory.source = "rusage-maxrss"`, `cpu.seconds`/`cpu.cores_avg` from the
  utime+stime delta, `scope: None` (the design-pass rationale BRIEF-1
  recorded verbatim: rusage measures via `wait4()`, not a cgroup read, so
  no contract `scope` value is honest — flagged per the brief as a
  decision-ask-already-resolved, not reopened here), everything else
  getrusage cannot supply left `None` (never fabricated). Confirmed (by
  reading, not assuming) `RESOURCE_SERIES_GETTERS`/`series_stats`/
  `_lane_stats`/`build_footprint_manifest` need zero code changes — a
  correctly-shaped rusage Summary already flows through every one of them
  via `.get()`.
- **`source` disclosure**: `build_footprint_manifest` gains one new
  per-lane key, `"source"` (pulled from the most-recent-profiled entry's
  `memory.source`, same `next(...)` read `scope`/`method` already use);
  `_fmt_footprint_row` appends `[source: rusage-maxrss]` to a lane's row
  ONLY when that source is `"rusage-maxrss"` (every other lane's row is
  byte-identical to before — confirmed by the full `TestFootprintVerbCLI`
  sweep staying green); `cmd_doctor`'s footprint drift/staleness section
  (check 6) gains one consolidated `INFO "footprint source"` record naming
  every such lane, so an operator reading `doctor` output learns the
  caveat without cross-referencing `footprint`'s own table.
- **R-36h containment — a decision-ask resolved, not left open**: the
  BRIEF-1 pseudocode's own `ru_before = resource.getrusage(...) if mode ==
  "rusage" else None` sits OUTSIDE any try/except, which contradicts R-36h's
  own prose two paragraphs earlier in the SAME brief ("the WHOLE profiling
  attempt (self-id resolve, `ctl start`, OR the `getrusage` calls) must be
  wrapped so an unexpected exception never escapes to abort the lane's own
  `subprocess.run`" — explicit "must", governing over an illustrative code
  sketch prefaced "Structure (worked out, follow this shape)"). Resolved by
  guarding EACH `getrusage` call individually (`ru_before` before the
  child's own `try:`, `ru_after` inside the `finally:` but before the inner
  `try:`) rather than the literal one-liner shown: on a planted failure,
  `ru_before`/`ru_after` stay `None`, and `finish_bare_host_profiling`'s
  pre-existing "attempted but never got going" fallback (the SAME shape
  `finish_lane_profiling`'s own analogous fallback already has) degrades to
  `resources: None` with the ALREADY-recorded warning (from
  `start_bare_host_profiling`'s own degrade reason, since a getrusage
  failure's OWN message loses the `warning or` short-circuit to whichever
  reason got there first) rather than propagating out of a `finally` block
  and crashing the whole invocation. Proven directly:
  `test_getrusage_raising_never_aborts_the_lane` monkeypatches
  `run_gate.resource.getrusage` to always raise and asserts the lane's own
  exit code (a deliberately distinctive `3`, from `["sh", "-c", "exit 3"]`)
  survives untouched. `resolve_self_container_id` raising is the OTHER
  planted exception the handoff asks for
  (`test_resolve_self_container_id_raising_never_aborts_the_lane`) — this
  one degrades to a STILL-VALID rusage profile (the crash only means no
  daemon target was found; the getrusage bracket around the lane's own
  child runs regardless and succeeds), which the test asserts directly
  (`resources["method"] == "rusage"`, `profile_error is None`, the WARNING
  text on stderr) rather than assuming a null record.
- **Disclosure-gating bug found and fixed during this same commit (not a
  separate decision-ask — caught by the test suite before the commit, so
  no regression ever landed)**: a first draft called
  `print_profile_warning`/`print_host_pressure_line`/
  `print_profile_session_line` UNCONDITIONALLY after the profiling
  if/else, rather than only inside the `else:` (profiling-ENABLED) branch
  the way `run_container_lane`/`run_exec_lane` both already do — this
  printed a host-PSI line even with `RUN_GATE_PROFILE=off` (caught
  immediately by the rewritten `test_disabled_prints_nothing_new`, red on
  the first run). Fixed by moving all four print statements (the three
  above plus the DEVCONTAINER-WIDE line) inside the `else:` branch,
  matching the established pattern exactly; the dry-run branch keeps its
  OWN separate, pre-existing `print_host_pressure_line()` call (bare, no
  `profiler_status` argument — nothing real has been attempted on that
  branch) so a dry run's disclosure is unchanged from before this package.
- **`RUN_GATE_PROFILE_SESSION` token injection**: only on the daemon path
  (`run_env = dict(os.environ); run_env[PROFILE_TOKEN_ENV] = ...`, passed
  as `subprocess.run(argv, ..., env=run_env)`) — the rusage path starts no
  daemon session for the token to identify, so `run_env` stays `None`
  (`subprocess.run(..., env=None)` is identical to omitting `env`
  entirely — confirmed no behavior change for the rusage/disabled paths).
  `test_daemon_path_targets_self_id_scope_and_injects_token` proves the
  injection with a `subprocess.run` spy (matching the file's own existing
  `real_run = subprocess.run; def spy(...)` pattern) rather than trusting
  argv construction alone; a companion
  `test_rusage_mode_never_injects_a_token` proves the negative.
- **`cmd_footprint`'s `--write` refusal message** ("bare-host lanes are
  never profiled, RG-57") was now FALSE the moment this commit's code
  shipped — fixed as part of this same commit (a functional-correctness
  fix caused directly by this commit's own behavior change, not a docs-
  prose drift C5's sweep owns) rather than left stale until C5.
- **`--dry-run`**: `print_profile_plan_dry_run(profile_plan,
  "container-shared")` added to the dry-run branch — confirmed by reading
  the pre-C4 function fresh (per BRIEF-1 item 4's own instruction not to
  trust its "currently MISSING" claim without verifying): it WAS missing,
  confirmed, now fixed.
- **`RUN_GATE_PROFILE=off`/`profile = false` opt-out**: confirmed
  structurally unchanged (the `profiling = bool(...)` gate is untouched);
  the disabled-path `run_record["profile_error"]` literal string ("bare-
  host lanes are not profiled (RG-57)") — now FALSE per this commit's own
  change — is reworded via `finish_bare_host_profiling`'s `mode ==
  "disabled"` branch to `profile_plan.get("disabled_reason", "disabled")`,
  the same wording every OTHER lane kind's disabled path already uses (no
  bare-host-specific string left).
- Docs (SPEC R-43b/R-43i, R-36 series, CONSUMERS.md, LANE-AUTHORING.md,
  the backlog's RG-57 entry) deliberately NOT touched here — C5 owns the
  whole documentation sweep, per BRIEF-1 item 7, so nothing re-drifts
  before this commit's real behavior was known.

### Tests

`tests/test_run_gate.py`:
- `TestBareHostProfilingWiring` REWRITTEN (3 pre-existing tests updated for
  the new behavior + 6 new): `test_daemon_absent_falls_back_to_rusage_and_
  discloses_why` (the natural, UNMOCKED `resolve_self_container_id` path —
  `fake_docker`'s shim has no `inspect)` case, so real stdout is empty
  regardless of this test process's own `/etc/hostname`, deterministic by
  construction rather than by monkeypatching the helper away),
  `test_disabled_prints_nothing_new` (updated expected `profile_error`
  string), `test_dry_run_has_no_run_record` (updated to also assert the
  now-present profile-plan disclosure), `test_daemon_path_targets_self_id_
  scope_and_injects_token`, `test_rusage_mode_never_injects_a_token`,
  `test_resolve_self_container_id_raising_never_aborts_the_lane`,
  `test_getrusage_raising_never_aborts_the_lane` (the two R-36h plants).
- `TestFootprintManifestBuild.test_a_lane_with_one_profiled_pass_matches_
  contract_shape` updated (exact-dict-equality test) to include the new
  `"source": "sampled-max"` key `SUMMARY_V1`'s fixture actually carries.
- `TestFootprintVerbCLI.test_bare_host_rusage_run_makes_write_stop_
  refusing` — the "consequence to exploit" the handoff and BRIEF-1 both
  name: a REAL `main(["suite"])` run (not a synthetic
  `record_profiled_run` injection) against an all-bare-host project, then
  `main(["footprint", "--write"])` no longer refusing, the manifest's
  `source`/`scope`/`method` fields matching the rusage shape, and
  `cmd_doctor` disclosing the same caveat in its own "footprint source"
  INFO record.

### Gate verdicts (targeted, host-load-constrained; full package gates run
at close-out per the handoff — PID 2415767 and the P1 r2 container were
BOTH still alive for the entirety of this commit's work, confirmed by
re-checking before every command below)

```
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestBareHostProfilingWiring" -q
7 passed, 995 deselected, 1 warning in 1.30s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "Profil or profil or Footprint or footprint or Doctor" -q
212 passed, 790 deselected, 1 warning in 17.86s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "BareHost or Inflight or inflight or ExecLane or ContainerLane or Usage or usage or StallTimeout" -q
99 passed, 903 deselected, 1 warning in 13.39s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestFootprintVerbCLI" -q
13 passed, 990 deselected, 1 warning in 4.05s

$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py \
    -k "TestFresh or fresh or Dry or dry_run or DryRun or TestUxSurface" -q
54 passed, 949 deselected, 1 warning in 9.34s
```

No whole-suite run and no assay lane attempted this commit (host load rule,
PID 2415767 alive throughout) — the RG-53 judge's own line-AND-branch
coverage verdict on C4's changed lines is a package-gate item, run once at
close-out per the handoff, not per-commit.

**Note on commit trailer (procedural, not a decision-ask):** C1-C3 (session
1) trailed `Co-Authored-By: Claude Fable 5.1`. This session's own live
system-reminder — addressed to this session specifically, and explicit that
it "replaces Claude Code's own earlier attribution guidance, such as a
previous copy of this reminder" — names `Co-Authored-By: Claude Sonnet 5`
for commits made from here on, same `Claude-Session` URL as C1-C3 (this is
a continuation of the same session thread under an updated model identity,
not a different session). C4/C5 use the current reminder's trailer text;
flagging the visible difference here so a reviewer diffing commit messages
across this package does not read it as an authorship error.
