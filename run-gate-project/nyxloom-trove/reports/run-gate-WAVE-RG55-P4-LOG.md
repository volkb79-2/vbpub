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
