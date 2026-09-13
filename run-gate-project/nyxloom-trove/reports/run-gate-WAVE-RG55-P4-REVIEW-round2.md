# run-gate-WAVE-RG55-P4 — adversarial review, round 2 (repair verification)

**Verdict: ACCEPT-conditional — B1, B2, B3, B4 and S1–S5 all PASS, RW-46a and
RW-46b PASS, all three gates reproduce; ONE outstanding item (B5, contract
§4.3a vs. the shipped `meta.expected`) plus 6 non-blocking findings.** B5 is a
one-line consumer change + one test, OR a controller amendment of §4.3a — it
does not need a review round of its own and can ride the r2-survivor commit.
**This verdict still excludes `assay-r2`**, which has not run (controller-
scheduled, RW-42): the ACCEPT remains "pending the r2 survivor table", exactly
as in round 1.

**Reviewer:** the same fresh Opus session that wrote round 1 (never a fork of
the implementer or the controller). Method this round: read RW-43 and RW-46 on
`main` → the full repair diff `0bb3bbeb..4fa46b03` (12 files, 2500 insertions)
→ re-ran every round-1 probe **verbatim** against the repair tip → 20 planted
mutants (validated: every run's pytest summary line checked, a baseline green
run first — see "Mutation probes" for why that matters this round) → the three
gates → only then the LOG/REPORT/BRIEF-4.

**Tip reviewed:** `rg55-followups-run-gate` @ `4fa46b03`, base for the diff
`rg55-run-gate-client` (round-1 tip `0bb3bbeb` for the repair delta). Worktree
clean before and after my runs (`git status --porcelain` empty both times).

**Host rule honoured.** PSI checked before every launch (memory `full avg10`
≤ 0.5% at each launch). One lane at a time, serial, `nice -n 19 ionice -c 3`:
`selftest` **once**, `assay-r1` once, `assay-r3` once. **`assay-r2` never
touched** (P2's own r2 is still live on the host — pid 1499541 — and I left it
alone). No container created by me at any point (`docker ps` before the
selftest: no gate containers). `cgprofile-host-daemon` still DOWN and I did not
start it, so the **daemon-path probe is skipped again** — every live number
below is the rusage path. `scripts/cgroup-profiler/`, `ciu/`, `/workspaces/
dstdns` and the other worktrees untouched; no commits; mutation probes ran
against private copies in my scratchpad, never the worktree.

**Side effects:** three more `.run-gate/history.json` entries (selftest,
assay-r1, assay-r3), a regenerated `coverage.json`/`.coverage`, a regenerated
`.assay/verdict-r1.json` (my r1 PASSed on `4fa46b03`), assay-state additions.
`run-gate.footprint.json` **not** rewritten (I never ran `--write`). Note for
the controller: the committed manifest's `assay-r1` row was distilled partly
from MY round-1 gate runs — see S6.

---

## Gates I reproduced on `4fa46b03`

| lane | result |
|---|---|
| `./run-gate.py selftest` | **PASS** — `1143 passed, 4 skipped … in 212.14s`; `diff-coverage OK: 1066/1066 changed executable lines covered (100.0%); branches 394/394 taken`; exit 0 |
| `./run-gate.py --base rg55-run-gate-client assay-r1` | **PASS**, **first attempt** (no flake), commit `4fa46b03…`; exit 0 |
| `./run-gate.py assay-r3` | **PASS** — `canary: 2 rejected, 0 survived`; exit 0 |
| `./run-gate.py assay-r2` | **NOT RUN** (forbidden by my dispatch; slot still occupied) |

Matches the implementer's reported verdicts exactly (1143/100%/100%).

---

## Per-blocker checklist

### B1 — rusage numbers come from the lane's own child — **PASS**

*Structural:* `import resource` is gone and `grep -c 'resource.getrusage('
run-gate.py` = **0**. The path is `subprocess.Popen` → `os.wait4(proc.pid, 0)`
→ `os.waitstatus_to_exitcode(status)` (`run-gate.py:7838-7852`). The round-1
defect class (a high-water mark over every reaped child) is **eliminated by
construction**, not merely patched — there is one `ru`, for one pid.

*My `["true"]`-lane probe, repeated verbatim:*

```
run-gate: footprint tiny: peak 36 MiB, … [source: rusage-maxrss] (peak <= floor)
history.json → peak_bytes 37769216 (36.02 MiB), floor_bytes 37838848 (36.09 MiB),
               peak_at_floor True, source rusage-maxrss
```

The number is still ~36 MiB — but it is now *this child's own* `ru_maxrss`,
and the record and every disclosure surface say it is at or below run-gate's
own RSS floor, i.e. unmeasurable beyond that floor. That is RW-46b's ruling
applied exactly, and it is the honest answer to round 1's B1: the measurement
is right and its limit is disclosed rather than hidden.

*Differential proof the number now tracks the lane:* a probe lane whose child
allocates 300 MiB records `peak 309 MiB` with **no** floor note
(`peak_at_floor` false) in the same project and the same run-gate process.

*Exit-code parity:* a lane that SIGKILLs itself reports `run-gate: lane
'killed' exit -9` — byte-identical to `subprocess.run(...).returncode` (-9),
verified side by side. `os.waitstatus_to_exitcode` is therefore not a
behaviour change for signalled lanes.

*R-36h:* `Popen` itself is deliberately unguarded (a bad argv[0] must fail the
lane exactly as before); the `wait4` bracket is guarded twice — an ordinary
`Exception` degrades to `ru = None` with the child still reaped via
`proc.wait()`, a `BaseException` kills, reaps and re-raises. The second arm is
pinned by a real test (`test_wait4_interrupted_kills_the_child_and_reraises`
plants `KeyboardInterrupt` inside `os.wait4`, then asserts the lane's own
`sleep 5` child was reaped — `poll() is not None`), the first by
`test_ru_is_none_never_fabricates_a_profile` (see B2).

### B2 — the rusage arithmetic now has oracles — **PASS**

`TestBareHostRusageArithmetic` calls `finish_bare_host_profiling` directly with
a `_FakeRusage(ru_maxrss=12345, ru_utime=1.25, ru_stime=0.5)` and asserts
`peak_bytes == 12345 * 1024`, `cpu.seconds == 1.75`, `cores_avg ==
approx(1.75/2)`, plus the null discipline. Every round-1 survivor dies:

| round-1 survivor | replanted as | result | killing test |
|---|---|---|---|
| M2 `* 1024` → `* 1` | MA | **KILLED** | `test_exact_arithmetic_from_a_known_rusage` |
| M3 cpu absolute-vs-delta | MB (drop `ru_stime`) | **KILLED** | `test_exact_arithmetic_from_a_known_rusage` |
| M1 `ru_after` → `ru_before` | — | **STRUCTURALLY GONE** (one `ru`, nothing to swap) — confirmed by grep, not taken on trust |
| N4 boolop `or` → `and` | MC | **KILLED** | `test_ru_is_none_never_fabricates_a_profile` |
| N13 exec-record `==` → `!=` | MD | **KILLED** | `test_profile_session_recorded_only_on_the_daemon_path` |

### B3 — foreign inflight records are refused — **PASS**

Both writers stamp `runner` (`run-gate.py:7295` container, `run-gate.py:7608`
exec) and `resolve_inflight` checks it **first, before any docker call and
before RW-14's owner questions** (`run-gate.py:6921-6941`). My round-1 probe,
re-run verbatim with the exact payload `run_exec_lane` now writes:

```
--- container-lane invocation, --fresh=False (DRY RUN) ---
run-gate: the inflight record for lane 'suite' names container
  'dstdns-98535c-test-runner', written by runner 'exec' — foreign record —
  refusing to attach, follow, collect, or remove it. …delete <path>…
--- container-lane invocation, --fresh=True (DRY RUN) ---
(identical refusal)
```

Live (`dry_run=False`): the same refusal, `resolve_inflight` returns `None`
(runs fresh), and my instrumented docker shim recorded **no docker invocation
at all** before the refusal. Backward compatibility verified in the same probe:
a record with **no** `runner` key still takes the old container path
(re-attach), which is correct — the container path was the only pre-RG-60
writer. SPEC `R-39a`/`R-39f`/`R-43f` describe exactly this. Guard mutants
inverted (MF) and removed (MG) are each killed by all three of
`test_dry_run_refuses_a_record_written_by_the_exec_runner`,
`test_live_run_refuses_a_foreign_record_and_runs_fresh_instead`,
`test_fresh_refuses_to_remove_a_foreign_record`.

### B4 — `doctor`'s daemon WARN names both fallbacks — **PASS**

Live on the repair tip:

```
run-gate: doctor: [WARN] profiler daemon: 'cgprofile-host-daemon' not running
  (start it: cd scripts/cgroup-profiler && ciu up) — container/exec lanes fall
  back to basic (in-lane) sampling, bare-host lanes to coarse rusage
  accounting (R-43i)
```

Reverting the wording (MM) is killed by `test_daemon_not_running_warns_by_name`.

---

## RW-46a (lock-dir isolation) — **PASS**

*Acceptance bar, run as the coordinator specified:* I snapshotted every
`/tmp/run-gate-{exec,shared}-*` entry before the whole selftest (7467 entries)
and after (7467). **New entries created by the suite: zero.** The only `/tmp`
file the run touched is the pre-existing `run-gate-gitconfig` (by design,
`GIT_CONFIG_GLOBAL`). Independently: my 17-mutant batch ran the suite 17 more
times and created nothing under host `/tmp` either.

*Root cause, verified in the source:* both
`test_unusable_lock_path_is_infra_failure_not_traceback` sites now `rmdir()` in
a `finally` (`tests/test_run_gate.py:3454`, `:4097`), and `tests/conftest.py`'s
autouse `isolate_shared_lock_dir` asserts on teardown — after **every** test —
that no directory-shaped entry survives under the isolated dir. Production
semantics are unchanged: `SHARED_LOCK_DIR = "/tmp"` is still the default,
`_lock_dir()` merely allows the `RUN_GATE_LOCK_DIR` override (same family as
`RUN_GATE_CGROUPFS_ROOT`/`RUN_GATE_PROC_ROOT`), re-read per call so subprocess
invocations inherit it. Making the override a no-op (MP) is killed by 13 tests
including `test_shared_infra_serializes_concurrent_gates` and
`test_sorted_acquisition_kills_abba` — i.e. the suite genuinely depends on the
isolation rather than tolerating it.

*Doctor check, live:* `[INFO] stale coordination locks: 2604 entries older than
1 day under /tmp, 306 as a DIRECTORY (never legitimate — corruption …) — report
only, doctor never deletes`. Report-only confirmed by reading the code (no
unlink/rmdir anywhere in section 8).

*Attribution of the remaining host-/tmp growth (not this package):* 70 lock
directories were created under `/tmp` after 19:00 today, the newest at 19:53,
all named `run-gate-{exec-myproj-dev1,shared-dir}-<pid>` — the signature of the
two offending tests running from a checkout **without** this fix. P2's worktree
(`.worktrees/rg55-run-gate-client`, rev 41) has no `tests/conftest.py` and is
running `assay-r2` right now, which re-runs this suite per candidate. So the
leak continues from P2's tree until this package merges — worth the controller
knowing, and an argument for merging P4 before P2's r2 concludes (or simply
accepting the residue).

## RW-46b (`floor_bytes`/`peak_at_floor`) — **PASS**

`_self_rss_bytes()` reads `/proc/self/statm` field index 1 (resident) × page
size, honours `RUN_GATE_PROC_ROOT`, returns `None` (never 0) on any malformed
read; it is called **immediately before `Popen`** and only on the rusage path
(`run-gate.py:7828-7832`). Mutants: field `1 → 0` (MJ) killed by
`test_reads_resident_pages_times_page_size` (+2); "floor never computed" (MK)
killed by `test_real_proc_wires_a_real_floor_end_to_end`; the comparison
`<=` → `<` (MI) killed by `test_peak_at_or_below_floor_is_flagged_true`. Both
sides of the comparison are tested with the other parameter at its default
(`test_peak_at_or_below_floor_is_flagged_true`,
`test_peak_strictly_above_floor_is_flagged_false`,
`test_floor_bytes_omitted_leaves_peak_at_floor_none_not_fabricated` — `None`,
never fabricated as `False`). Live end-to-end: `peak_at_floor True` for the
`["true"]` lane, `False` for the 300 MiB lane, disclosed on the live footprint
line, in `history`, in the manifest and in `doctor`.

---

## S1–S5

| finding | verdict | evidence |
|---|---|---|
| S1 rusage caveat on the live `footprint` line and `history` | **PASS** (behaviour) | live: `run-gate: footprint tiny: peak 36 MiB … [source: rusage-maxrss] (peak <= floor)`; `history tiny`: `PEAK 36 MiB … [source: rusage-maxrss] (peak <= floor)`; `history assay-r3` (not floor-bound): `… [source: rusage-maxrss]` only. Oracle gap on two of the four notes — S7/S8 below |
| S2 RG-59 over-match | **PASS** | a RUNNING daemon's own crash (`stdout` = traceback, `stderr` = `cgprofile.errors.TargetError: target container 9f01 is not running`, exit 1) now reads `produced unparsable stdout` — round 1's false positive is gone. Both **real** docker wordings captured live on this host (`Error response from daemon: container … is not running`, exit 1; `… No such container: …`, exit 1) still produce the daemon-not-running reason, so the true positive is preserved. Reverting to the substring matcher (MQ) is killed by `test_a_running_daemons_own_crash_is_never_misreported_as_not_running` + `test_docker_reserved_exit_code_names_the_real_cause_even_with_no_recognizable_prefix` |
| S3 circular killed-client test | **PASS** | removing `run_exec_lane`'s `write_inflight_record` call (ME) now fails `test_killed_client_leaves_the_record_on_disk` itself (plus 2 siblings) — round 1's version stayed green under the same mutation |
| S4 stale comment at `run-gate.py:8136` | **PASS** | rewritten to state RG-57's actual behaviour, including that the bare-host daemon path uses the same token; `grep "categorically unprofiled"` now only matches the rev-41 historical note in the revision header |
| S5 RG-61 item-8 counts | **PASS** | recounted by me at the tip: `TestBareHostStallTimeoutWarning` 3, `TestBareHostProfilingWiring` 17, `TestExecLaneInflightRecord` 4, `TestResolveSelfContainerIdDirectBranches` 6 — every backlog claim now matches, and RG-61's own earlier correction is annotated ("true when written," not overwritten) rather than silently rewritten |

---

## Mutation probes (round 2)

20 mutants planted against a private full copy of the project; every run
validated (a baseline run first, and each run's pytest summary line checked —
**this matters: my first batch this round was invalidated by a foreign agent
deleting `tests/` from a shared scratchpad path, which turned every run into a
false "KILLED". I re-ran everything in a private directory with a summary-line
assertion.** Nothing of that touched the worktree).

**Killed (14):** MA `*1024→*1`, MB cpu drops `ru_stime`, MC boolop `or→and`,
MD exec-record `==→!=`, ME exec inflight write removed, MF/MG B3 guard
inverted/removed, MI `peak_at_floor <=→<`, MJ statm field, MK floor never
computed, ML raw wait status as exit code (killed by
`test_resolve_self_container_id_raising_never_aborts_the_lane`), MM doctor
wording, MP lock-dir override ignored, MQ RG-59 matcher reverted.

**Survived (3)** — all three are *missing assertions on disclosure/stamp
values*, not behaviour defects (each behaviour verified live by me):

- **MH** — `run_exec_lane`'s `"runner": "exec"` changed to `"container"`
  survives the **full suite** (1029 passed). The B3 guard is well tested; the
  stamp it depends on is not. → S7.
- **MN** — dropping `[source: rusage-maxrss]` from `print_footprint_line`
  survives the full suite (1028 passed, with only the two artefacts below
  deselected). → S8.
- **MO** — `_lane_stats`' `any(...)` → `all(...)` for the history caveat flags
  survives the full suite (1029 passed). → S8.

Two tests were deselected in the full-suite runs and neither is a P4 defect:
`TestPointerLinkageEstate::test_cmru_toml_id_matches_orchestration_key` (fails
in any out-of-repo copy) and `TestExecModeMutex::test_a_real_lane_run_never_
touches_host_tmp` (see S9 — it failed once during my probes because of a
*concurrent* foreign writer, which is the flake I am flagging).

---

## The one outstanding item

### B5 — the shipped `meta.expected` does not carry `source`, but contract §4.3a (main, RW-43) says it does

`RG55-INTERFACE-CONTRACT.md` §4.3a on `main` — added by RW-43 specifically to
settle my round-1 decision ask D1 — states: "`meta.expected` fed into `start`
carries `"source"` (the manifest's) so admission (RG-56) knows the provenance",
under the heading "run-gate ≥ 23.8.0", i.e. this release.

`footprint_manifest_lane_expected()` (`run-gate.py:2939-2944`) still returns the
four-key object only:

```python
    return {
        "memory_peak_median_bytes": …, "hot_set_p90_bytes": …,
        "cpu_cores_avg": …, "duration_median_s": …,
    }
```

and SPEC R-43/R-44 likewise describe four keys. So `main`'s contract documents
a consumer behaviour rev 42 does not have. No runtime harm today (the daemon
stores unknown meta keys verbatim, and RG-56 is a later package), but the
manifest now mixes cgroup-measured and rusage-derived expectations and the
provenance is exactly what the contract says must travel with them.

**Prescription — either:** (a) add `"source": lane.get("source")` (and, worth
considering alongside it, `"peak_at_floor"`) to the `expected` object, one
assertion in the existing `profile_meta`/`expected` test, one SPEC clause; or
(b) the controller amends §4.3a to attribute that clause to P5/RG-56 rather
than to 23.8.0. Either is fine by me; shipping neither leaves `main`'s contract
untrue. This does **not** need its own review round — closing it in the r2
round is enough.

---

## NON-BLOCKING (S6–S11)

**S6 — the committed manifest and the CONSUMERS transcript are one generation
stale, and the prose overstates them.** `run-gate.footprint.json` was
regenerated at 19:42 (`from_commit ec6cdc07`), before the final r1/r3 gate runs
at 19:55/19:57. Consequence: the `assay-r1` row's two contributing PASS entries
are the **pre-repair getrusage-era** runs of 17:01 and 17:32 (one of them
mine, from round 1), and `assay-r3`'s likewise mix eras; both rows carry
`"peak_at_floor": null`. Only the `selftest` row is built from wait4-era runs.
The new CONSUMERS paragraph nevertheless says the capture is "after the round-2
rusage rewrite — `memory.peak_bytes` is now `os.wait4()`'s exact per-child
accounting … rather than the earlier `getrusage(RUSAGE_CHILDREN)` figure", and
that "none of these three lanes' most-recently-profiled run is floor-bound …
each one's own child genuinely exceeded run-gate's own resident size" — but for
two of the three rows the record says `null` = **unknown**, not "not
floor-bound". Fix: re-run `footprint --write` now that the store holds wait4
entries for all three lanes (19:55/19:57 both have `floor_bytes` and
`peak_at_floor: false`), paste the new transcript, and state plainly that the
medians span the method change.

**S7 — nothing asserts the exec writer's `runner` stamp value** (mutant MH
survives the full suite). B3's protection is two halves — the stamp and the
guard — and only the guard is pinned. One line in
`TestExecLaneInflightRecord::test_record_exists_when_exec_begins_and_cleared_
after` (`assert seen["record"]["runner"] == "exec"`, next to the existing
`lane`/`container`/`profile_token` assertions) closes it; a matching
`== "container"` assertion in the container-lane wiring test closes the other
half. Note a string-literal mutation is also outside `assay-r2`'s operator set
(`compare-swap`/`boolop-swap`/`bool-const-flip`/`falsy-swap`), so r2 will not
catch this either.

**S8 — two of the four new disclosure notes are unpinned** (mutants MN, MO
survive the full suite): `print_footprint_line`'s `[source: rusage-maxrss]`
segment, and `_lane_stats`' `any(...)` rule that makes `history` disclose the
caveat when *any* contributing entry carries it (the mixed-mode case the
comment explicitly designs for). The `(peak <= floor)` halves of both lines are
pinned (MI/MK died). Two assertions — one on a live footprint line, one on a
history series that mixes a daemon entry with a rusage entry — close both.

**S9 — `test_a_real_lane_run_never_touches_host_tmp` asserts on shared host
state and will flake.** It snapshots *every* `/tmp/run-gate-{exec,shared}-*`
name before and after a lane run and demands the sets be equal — but other
checkouts on this host legitimately write there (P2's r2 is doing it right
now). It failed exactly that way during one of my mutation runs, in a run where
the planted mutant had nothing to do with locks. Fix: compare only entries
attributable to this test (its container name already embeds the pid), e.g.
assert no new entry matching `run-gate-exec-{self._container_name()}*`, and
leave foreign names out of the comparison. Filing this alongside RG-62's two
flakes would be consistent.

**S10 — `Popen` is never told the child was reaped by `os.wait4`.** After
`wait4`, `proc.returncode` stays `None`, so `Popen.__del__` emits
`ResourceWarning: subprocess <pid> is still running` (reproduced with
`python3 -W error::ResourceWarning ./run-gate.py tiny`) and the object is put on
`subprocess._active` for a reap that can never succeed. Harmless today
(ResourceWarning is off by default and the later `waitpid` failure is handled
internally), but it is noise in any consumer that runs with warnings enabled.
One line: `proc.returncode = code` after `os.waitstatus_to_exitcode`.

**S11 — the exit-code arm of the RG-59 matcher now over-fires on 126/127.** A
`docker exec` that reaches the container but cannot execute the command (OCI
runtime failure, exit 126/127 — reproduced live on this host) is reported as
"'cgprofile-host-daemon' not running (start it: … ciu up)", though the daemon
container *is* running and the remedy is wrong (a broken image/PATH, not a
stopped container). Narrower reading: 125 = "the docker CLI/daemon could not
start the command" → daemon-absent; 126/127 → "the daemon container is running
but `cgprofile` could not be started in it". Much smaller than the round-1
false positive this arm replaced, and strictly better than before, so
non-blocking.

---

## RG-62 — is the filing honest? **Yes, with one wording correction**

I verified both claims independently:

1. `TestEstateBudgetTimeoutPairing` accumulates into a class-level
   `PAIRINGS_SEEN` list from a parametrized sibling test and then asserts
   `len(...) >= 3` in an aggregate test (`tests/test_run_gate.py:6637`,
   `:6688-6691`); `pytest-randomly 5.0.0` is installed and nothing in this
   repo's pytest config or the lane argv passes `-p no:randomly`. The
   order-dependence is real and the prescriptions are the right three.
2. `test_tree_state_is_sampled_before_the_lane_not_after`
   (`tests/test_run_gate.py:7980-7990`) plants `time.monotonic() - 4.0` and
   asserts `duration_seconds == 4.0` exactly, with nothing but in-process code
   between the two — under scheduler delay it rounds to 4.001. Genuine timing
   flake, nothing to do with P4's paths.

Neither test is touched by this package's diff (`git diff
rg55-run-gate-client 4fa46b03 -- tests/test_run_gate.py` has no hunk at either
site), and my own `assay-r1` passed first try, consistent with
"non-deterministic". **Correction for the entry's opening sentence:** "neither
touching any file this package's own diff modified" is not accurate — both
tests live in `tests/test_run_gate.py`, which this diff modifies heavily. The
body's own later phrasing ("not touched by this package's diff") is the true
claim; the opening should match it.

---

## What I could not verify

The daemon path end-to-end (`cgprofile-host-daemon` is still down and I may not
start it): `method: daemon`, `scope: container-shared`, `targets_seen >= 1` and
the DEVCONTAINER-WIDE line remain fixture-proven only. And `assay-r2` — the
survivor table is round 3's subject.

## Summary for the controller

Every round-1 blocker is genuinely fixed, and the fixes are of the kind that
cannot regress quietly: B1 is structural (the borrowed-number class is gone by
construction, with the residual fork/COW floor now measured and disclosed
rather than hidden), B2/B3/B4 are pinned by tests that die under the exact
mutations that survived round 1, and RW-46a is proven by a before/after
snapshot of real `/tmp` around a full 1143-test run. The session's own records
are unusually honest — session 4 flagged the residual floor itself instead of
claiming the bar was met, and RG-62 is a correct, well-evidenced filing.
B5 is the only thing standing between this and an unconditional ACCEPT, and it
is a one-liner or a controller amendment. After that, and the r2 survivor
table, I would merge this.
