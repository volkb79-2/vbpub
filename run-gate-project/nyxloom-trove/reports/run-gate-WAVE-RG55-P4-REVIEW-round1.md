# run-gate-WAVE-RG55-P4 — adversarial review, round 1

**Verdict: ACCEPT-conditional** — 4 blockers (B1–B4), 12 non-blocking findings
(S1–S12), 2 decision asks for the controller. **This verdict covers everything
EXCEPT `assay-r2`**, which has not run (the project's mutation lane was
occupied; RW-42 schedules it separately). An ACCEPT after the repairs below is
therefore still "ACCEPT pending the r2 survivor table", which arrives as a
fix-verification round.

**Reviewer:** fresh Opus xhigh session, never a fork of the implementer or the
controller. Blind-first, in this order: the review handoff → controller log
RW-26/RW-27/RW-27a/RW-27b/RW-28/RW-39/RW-40/RW-41/RW-42 → the backlog entries
RG-57..RG-61 **as filed on the base** (`git show rg55-run-gate-client:…`) →
`RG55-INTERFACE-CONTRACT.md` §2/§3/§4 → the P4 implementer handoff → the full
diff `rg55-run-gate-client...0bb3bbeb`, every file type → my own live probes and
mutation probes. `-LOG.md`/`-REPORT.md`/`-BRIEF-n.md` were opened only after all
of that (Phase 2).

**Tip reviewed:** `rg55-followups-run-gate` @ `0bb3bbeb`, base
`rg55-run-gate-client` (P4 branched from `186461de`, merged `647a2cc6` at
`0c782601`). Diff: 15 files, 3496 insertions. Worktree clean at review time and
still clean at the end (`git status --porcelain` empty both times).

**Host rule honoured.** PSI checked before every launch (memory `full avg10`
never above 1.1% at launch; peak observed 1.94% mid-run). One lane at a time,
serial, `nice -n 19 ionice -c 3`, all bare-host: `selftest` **once**,
`assay-r1` once, `assay-r3` once. **`assay-r2` never touched.** No container was
created by me (`docker ps` showed exactly one gate container throughout — P1's
`run-gate-vbpub-r2-680904-…`, left alone). `cgprofile-host-daemon` is **DOWN**;
I did not start or stop it, so the **daemon-path live probe was skipped** — every
live number below is from the rusage path. No `--cgroupns=host`/`--pid=host`;
`scripts/cgroup-profiler/`, `ciu/`, `/workspaces/dstdns` and the other worktrees
untouched. All mutation probes ran against a **copy** of the project in my
scratchpad, never the worktree.

**Side effects I must disclose** (all in the P4 worktree, all untracked/ignored,
none touching a tracked file): three new `.run-gate/history.json` entries
(`selftest`, `assay-r1`, `assay-r3`), a regenerated `coverage.json`/`.coverage`,
a regenerated `.assay/verdict-r1.json` (my r1 run PASSed on `0bb3bbeb`, clean
tree), and content-addressed additions under
`/workspaces/vbpub/.run-gate/assay-state/…`. `run-gate.footprint.json` was
**not** rewritten — I ran `footprint` read-only, never `--write`.

---

## Gates I reproduced myself (verdict read in a separate step every time)

| lane | result |
|---|---|
| `./run-gate.py selftest` | **PASS** — `1114 passed, 3 skipped … in 148.63s`; `diff-coverage OK: 1008/1008 changed executable lines covered (100.0%); branches 376/376 taken`; `lane 'selftest' exit 0` |
| `./run-gate.py --base rg55-run-gate-client assay-r1` | **PASS** — `r1: PASS (exit 0)`, commit `0bb3bbeb…`; `lane 'assay-r1' exit 0` |
| `./run-gate.py assay-r3` | **PASS** — `median-not-mean ok`, `median-not-mean-series-stats ok`, `canary: 2 rejected, 0 survived`; `lane 'assay-r3' exit 0` |
| `./run-gate.py assay-r2` | **NOT RUN** (mutation lane occupied; forbidden by my dispatch) |

The implementer's three claimed verdicts reproduce exactly. The `selftest`
judge's `--base main` is confirmed (it judges the whole wave diff, not P4's
increment) — that is a lane-config fact, not a P4 defect.

---

## BLOCKERS

### B1 — the rusage path's `memory.peak_bytes` is not the lane's number at all (RG-57/R-43i, contract §1.7)

`run-gate.py:2029`

```python
            "peak_bytes": ru_after.ru_maxrss * 1024,
```

Every other field on this path is a **delta** (`run-gate.py:2011-2012`:
`ru_after.ru_utime - ru_before.ru_utime + …`). `ru_maxrss` is taken **absolute**
— and `getrusage(RUSAGE_CHILDREN).ru_maxrss` is a monotone high-water mark over
**every child the process has ever reaped**, not a per-call value and never
decreasing. Verified directly:

```
start children maxrss KiB: 0
after 200MB child: 215800
before small child: 215800 after small child: 215800 delta: 0
```

so a lane child smaller than something run-gate already reaped is credited with
the larger number. On this code path run-gate always has such children: on the
daemon-absent path `resolve_self_container_id()` has just run `docker inspect`
and `ProfilerClient.version()` a `docker exec` (docker CLI ≈ 30 MiB RSS,
measured), plus git; and even with **no docker at all** every child is forked
from run-gate itself, so the fork image is a floor.

Live probe, a throw-away project whose lane argv is literally `["true"]`:

```
run-gate: footprint tiny: peak 36 MiB, …
history.json → resources.memory.peak_bytes = 37761024   ("true", docker present)
history.json → resources.memory.peak_bytes = 37261312   ("true", docker absent from PATH)
```

`/bin/true` did not use 35–36 MiB. The recorded figure is run-gate's own process
image plus its own tooling children. It then flows, unmarked, into:
`history`'s `PEAK` median (verified live), `run-gate.footprint.json` (the tracked
artifact this wave's goal names), `doctor`'s footprint drift check, and
`profile_meta()`'s `meta.expected.memory_peak_median_bytes`
(`run-gate.py:1674-1678`) — i.e. into what RG-56/P5 will use for admission
control. For `selftest` (268 MiB) the pytest child dominates so the committed
number is roughly right; for any lane lighter than ~40 MiB it is pure tool
overhead reported as the lane's peak.

The documentation states the opposite of what the code does, in four places:
SPEC `R-43i` ("`resource.getrusage(RUSAGE_CHILDREN)` accounting **bracketed
around the lane's own child** … the largest SINGLE child's RSS"), `CHANGES.md`
("bracketed around the lane's own child"), `LANE-AUTHORING.md` ("the largest
SINGLE child process"), and `doctor`'s own INFO line ("the largest single CHILD
process's RSS"). The LOG says "delta'd in `finish_bare_host_profiling`" — true
of CPU only. Nothing in the suite pins it: mutant **M1** (`ru_after` →
`ru_before`) **SURVIVED** the whole targeted suite.

This is the honesty rule the whole RG-55 wave is built on (contract §1.7: absent
means unknown, **never fabricated**), applied to the one number the package
exists to produce.

**Prescription (all four parts):**
1. Decide the peak honestly. If `ru_after.ru_maxrss > ru_before.ru_maxrss`, this
   child pushed the high-water mark and that value IS its peak. If it is equal,
   this child's peak is only known to be `<= ru_before.ru_maxrss` — record
   `memory.peak_bytes: null` (contract §1.7's own case) with a named
   `profile_error`/disclosure, never the stale larger number.
2. Disclose the irreducible floor: even when the delta is positive the figure
   includes the forked interpreter image (~35 MiB here), so it is an **upper**
   bound on the child's own RSS, not a cgroup measurement. Say so where the
   number is defined (`R-43i`) rather than only "largest single child".
3. Correct SPEC `R-43i`, `CHANGES.md`, `LANE-AUTHORING.md` and `doctor`'s INFO
   text to describe what is actually measured: the high-water mark over every
   child this run-gate process reaped, this lane's child included.
4. Oracle: a test that reaps a deliberately large child before the lane and
   asserts the lane's small child is NOT credited with it (stubbing
   `run_gate.resource.getrusage` with known before/after values is the cheapest
   shape and also discharges B2).

If, after 1–2, a lane like `assay-r3` yields `peak_bytes: null` more often than
not, that is the honest outcome — see decision ask D2.

### B2 — the rusage arithmetic has no oracle; three surviving mutants r2 cannot reach

The diff-coverage judge is green (1008/1008 lines, 376/376 branches) because the
lines execute — no assertion pins their values. The only numeric assertions on
the rusage summary are `peak_bytes > 0` and `cpu["seconds"] >= 0`
(`tests/test_run_gate.py`, `test_daemon_absent_falls_back_to_rusage_and_
discloses_why`). I ran hand-built mutants against the full targeted suite
(`-k "BareHost or ExecLane or ResolveSelfContainerId or ProfilerClient or
Footprint or Doctor or Profil"`, on a scratch copy):

| mutant | result |
|---|---|
| M1 `peak_bytes = ru_before.ru_maxrss * 1024` | **SURVIVED** |
| M2 `* 1024` → `* 1` (the KiB→bytes conversion) | **SURVIVED** |
| M3 cpu = absolute `ru_after.ru_utime + ru_after.ru_stime` (no delta) | **SURVIVED** |
| N4 `mode != "rusage" or ru_before is None or ru_after is None` → `and` | **SURVIVED** |
| N13 exec record `profile_session … if mode == "daemon"` → `!=` | **SURVIVED** |
| M4–M10, N1–N3, N5–N12 (14 others) | KILLED |

M2 is the single unit conversion the backlog entry and the handoff both called
out by name ("Linux reports KiB"); a mutant that drops it ships a peak 1024×
too small and every test stays green. **`assay-r2` cannot close this**: that
lane's operator set is `compare-swap`, `boolop-swap`, `bool-const-flip`,
`falsy-swap` (`assay.toml`) — it never generates an arithmetic or literal
mutation, so M1/M2/M3 are structurally out of its reach. N4 and N13 ARE within
its reach and should be expected in the survivor table.

**Prescription:** a direct test that stubs `run_gate.resource.getrusage` to
return known `ru_maxrss`/`ru_utime`/`ru_stime` before and after and asserts the
exact bytes, the exact `cpu.seconds` and the exact `cores_avg` (kills M1/M2/M3);
sharpen `test_getrusage_raising_never_aborts_the_lane` to assert the recorded
`profile_error` names the getrusage failure rather than only
`profile_error is not None` (kills N4); add a daemon-path exec-lane assertion
that the inflight record's `profile_session` equals the session `ctl start`
returned (kills N13).

### B3 — the new exec-lane inflight record is indistinguishable from a container-lane record, and the container path will act on it destructively (RG-60, R-39/RW-14)

`run-gate.py:7367-7395` writes a record whose `container` field is the
**persistent runner's name** — a long-lived container run-gate never created and
does not own — into `.run-gate/inflight/<lane>.json`, the same single slot keyed
only by `(project_dir, lane_name)` that `run_container_lane` reads through
`resolve_inflight()` (`run-gate.py:6696`, called at `6961`). Nothing in the
payload says which runner wrote it. Before this package an exec lane wrote no
record at all, so this was unreachable; it is new risk introduced here.

Fed exactly the payload `run_exec_lane` now writes (owner pid dead, commit
matching, runner reported running), run-gate's own reconciliation — **dry run,
its own disclosure of what a live run would do** — says:

```
run-gate: DRY RUN: an inflight record names container dstdns-98535c-test-runner
  (started 2026-09-12T10:00:00Z, state running) — a live run would re-attach to it
run-gate: DRY RUN: an inflight record names container dstdns-98535c-test-runner
  (started 2026-09-12T10:00:00Z, state running) — a live run would remove
  dstdns-98535c-test-runner and run anew          (with --fresh)
```

"Re-attach" ends in `await_container`'s `finally` → `docker rm -f <name>`
(`run-gate.py:6521`); `--fresh` removes it outright (`run-gate.py:6843`). The
trigger is a lane whose `environment` flips from a `mode = "exec"` environment to
an ephemeral-container one between a crashed run and the next one — exactly the
kind of config move this estate makes (RG-43's `host`/`bare-host` swap was an
estate-wide `environment =` change). The blast radius is a shared CIU runner:
the same class as the live container-hijack incident CIU-104 was filed for.

Two aggravating details: the record is written **unconditionally**, so a
profiling-disabled exec lane writes `container_id: ""` (`run-gate.py:7327`),
which defeats `resolve_inflight`'s own S2 guard ("a different container now wears
this name", `run-gate.py:6738-6749`) because that guard is skipped when the
recorded id is falsy; and `load_inflight_record`'s schema check cannot help —
the schema is identical.

**Prescription:** stamp the writer into the payload (e.g. `"runner": "exec"`
alongside `schema`) and make the container path refuse to act on a record that is
not its own: disclose by name (R-05) and return "nothing to attach to", never
re-attach, follow, collect, `rm -f`, or clear it. A distinct filename
(`inflight/<lane>.exec.json`) is an equally acceptable, even cheaper, fix. Add a
test: a container-lane invocation facing an exec-written record touches no
container and removes nothing.

### B4 — `doctor`'s profiler-daemon WARN now states something this package made false

`run-gate.py:5559-5563`

```python
            record("WARN", "profiler daemon",
                   f"{daemon_not_running_reason(daemon_name)} — every lane "
                   f"falls back to basic (in-lane) sampling")
```

After RG-57, a bare-host lane **never** falls back to basic sampling — RW-27b
forbids a `BasicSampler` on that path, by design, and the code degrades to
rusage instead. run-gate-project's own five lanes are all bare-host, so on this
very project `doctor` tells the operator the one thing that is guaranteed not to
happen. The line was edited by this package (RG-59 factored its first half into
`daemon_not_running_reason()`), and the implementer's own REPORT quotes the
output verbatim without catching it. This is precisely the defect class RG-59
exists to end: a warning naming the wrong mechanism.

**Prescription:** name both fallbacks — e.g. "… — container/exec lanes fall back
to basic (in-lane) sampling, bare-host lanes to coarse rusage accounting
(R-43i)". Assert the wording in the existing `TestDoctorProfilerCheck`.

---

## NON-BLOCKING (S1–S12)

**S1 — the rusage caveat is missing from two of the four surfaces the number
shows on.** `R-43i` promises disclosure "next to any median it produces", and
`footprint`'s table and `doctor` do carry `[source: rusage-maxrss]`. The live
per-run line and the `history` verb do not:

```
run-gate: footprint tiny: peak 36 MiB, p90 -, - cores avg, …        (print_footprint_line)
      PEAK 36 MiB (max 36 MiB)  +BASE -  HOT p90 -  CORES -  STALL - (history)
```

Both are read far more often than `footprint --write`. Add the same suffix when
the entry's `memory.source == "rusage-maxrss"`.

**S2 — RG-59 over-matches: a RUNNING daemon can be reported as absent.**
`_stderr_names_daemon_not_running()` (`run-gate.py:1084-1089`) matches the
stderr tail alone, with no regard for stdout or the exit code. The handoff's own
attack-surface item asked for exactly this check; it fails:

```
stdout = "Traceback (most recent call last): …"      (a RUNNING daemon crashing)
stderr = "cgprofile.errors.TargetError: target container 9f01 is not running"
→ reason: 'cgprofile-host-daemon' not running (start it: cd scripts/cgroup-profiler && ciu up)
```

Docker's own pre-exec failure has **empty** stdout and a non-zero return code;
requiring `not proc.stdout.strip()` (and/or `proc.returncode != 0`) alongside the
stderr signal removes the ambiguity. Add the oracle the backlog sketched for the
negative case.

**S3 — `test_killed_client_leaves_the_record_on_disk` is circular.** It calls
`write_inflight_record()` with a hand-built payload and asserts
`load_inflight_record()` reads it back; it never enters `run_exec_lane`, and it
stays green with RG-60 entirely reverted. The REPORT and the backlog's RG-60
entry both describe it as a killed-client survival proof. The container lane has
a real precedent to mirror —
`TestReattachAcrossADeadClient::test_a_killed_client_leaves_a_container_the_next_
run_re_attaches_to` spawns a real client subprocess, `client.kill()`s it and then
asserts on disk. (The class's actual proof is
`test_record_exists_when_exec_begins_and_cleared_after`, which is a good test.)

**S4 — a stale comment now says the opposite of the code, in the one place the
token plan is built.** `run-gate.py:8136-8137`: "even though a bare-host lane
never uses the token: RG-57 makes bare-host categorically unprofiled regardless
of `[profile]`." This is the RG-61 drift class, inside the RG-61 commit.

**S5 — RG-61 item 8's own test counts are still wrong.** Actual at the tip:
`TestBareHostStallTimeoutWarning` = 3, `TestBareHostProfilingWiring` = 14,
`TestExecLaneInflightRecord` = 3, `TestResolveSelfContainerIdDirectBranches` = 6.
The backlog's RG-58 entry says "(4 tests)"; its RG-57 entry says "9 tests total";
the LOG says "`TestBareHostProfilingWiring` (+9 tests)" where the real increment
is +7 (7 → 14); and the RG-61 entry claims both were audited and corrected to
"3" and "7" — the 7 was true at `c37b6e94`, not at the tip. Recount and restate,
or drop the counts (a count that rots is worth less than a class name).

**S6 — the shared interface contract was not amended for what this package
emits.** `RG55-INTERFACE-CONTRACT.md` §3 enumerates `"method": "daemon"`,
`"source": "memory.peak" | "sampled-max"` and `scope` as
`"container" | "container-shared"`; §4.5's manifest block has no `source` key.
rev 42 writes `method: "rusage"`, `source: "rusage-maxrss"`, `scope: null` and a
new manifest `source` key. Nothing breaks today (the daemon never produces these),
but the contract is the shared truth P1 and any other consumer read. See D1.

**S7 — self-identity is inferred, not verified.** `resolve_self_container_id()`
(`run-gate.py:1729`) reads `/etc/hostname` and resolves it through
`docker inspect <hostname>` — a **name** lookup. It succeeds here only because
this devcontainer's hostname (`dstdns-devcontainer-vb`) also happens to be its
container name. On a plain host whose `/etc/hostname` collides with some
container's name, run-gate would start a `container-shared` session against an
unrelated container and record its numbers as this lane's. Cheap hardening:
accept the hostname only when it looks like a docker-assigned id (12/64 hex), or
cross-check the inspected container against something this process can observe,
and otherwise skip the daemon path with the disclosed reason the function already
has a slot for.

**S8 — `resources.duration_seconds` is whole-second on the rusage path.** It is
computed from `_iso_utc(time.time())` stamps (`run-gate.py:2010`), so the probe
lane recorded `duration_seconds: 0.0` (and therefore `cores_avg: null`) for a run
the record's own `duration` field measured at 0.1 s. Sub-second and a few-second
lanes lose `cores_avg` entirely and any lane's cpu-rate is quantised. Use a
float/monotonic bracket (or reuse the record's own duration) for this field.

**S9 — `--dry-run` on a bare-host lane rehearses only the half that usually does
not happen.** `run-gate: DRY RUN — profile plan: daemon 'cgprofile-host-daemon',
scope 'container-shared', damon on, token env RUN_GATE_PROFILE_SESSION` — with no
daemon running (the default state here) the live run takes the rusage path and
injects no token. One clause ("…, or coarse rusage accounting if the daemon is
unreachable / this process is not in a container") makes the rehearsal honest.

**S10 — the exec record is written after `ctl start`, unlike the container
lane's.** `run_container_lane` writes the record with `profile_token` **before**
any daemon call and adds `profile_session` in a second write
(`run-gate.py:7095-7101`, `7150-7155`) precisely so a client that dies during
`start` still leaves something behind; `run_exec_lane` writes once, after `start`
has answered (`run-gate.py:7395`). A death in that window leaks a daemon session
with nothing on disk naming it. The handoff prescribed this ordering, so this is
a residual-gap note, not a deviation — worth one line in `R-43f`.

**S11 — intel for the r2 survivor table (pre-existing, not P4's).** Mutating
`run_container_lane`'s own `"profile_token": profile_plan["token"] if profiling
else None` to `if not profiling` **SURVIVED** the targeted suite. That line is
P2's, but it sits in a file r2 mutates wholesale, so expect it in the survivor
table; it deserves a test rather than an equivalent-mutant justification.

**S12 — two small asymmetries.** (a) `start_bare_host_profiling()` has no
`if not plan["enabled"]` guard of its own (`start_lane_profiling` does), so a
direct caller can profile a disabled lane; `main()` gates it today, which is why
this is cosmetic. (b) `_fmt_footprint_row`'s `[source: rusage-maxrss]` suffix
extends past the header's last column, so the table header no longer describes
the full row width.

---

## DECISION ASKS (controller)

**D1 — amend `RG55-INTERFACE-CONTRACT.md`, or record deliberately that it stays
frozen.** rev 42 emits three values and one manifest key the contract does not
describe (S6). Contract amendments are the controller's (RW-27's own P5 note
makes that explicit). Related: `meta.expected` — the four-key object run-gate
sends the daemon at `ctl start` — now carries a rusage-derived
`memory_peak_median_bytes` with no provenance field, so RG-56's admission control
cannot tell a cgroup-measured expectation from a rusage-derived one. If P5 is to
consume it, the provenance belongs in the contract now, while §2.2 is being
touched anyway.

**D2 — after B1, may a rusage lane be footprint-ineligible?** RW-27b ruled
rusage entries footprint-eligible "with the source caveat printed". If B1 is
fixed as prescribed, a light lane will sometimes record `peak_bytes: null` and
will then be OMITTED from `run-gate.footprint.json` (absent = unknown, `R-44`'s
own rule). I read that as consistent with RW-27b and with the wave's honesty
rule, but it changes what "footprint-eligible" delivers for short lanes, so it
should be ruled rather than assumed.

---

## What I verified, and what I could not

**Verified against the code and live runs:** RG-60's record is written before the
exec and cleared on every exit path (Popen-spy test reproduced, plus mutants M6/M7
killed); RG-59's two branches share one constant with `doctor` and the
malformed-stdout wording is preserved (M4/N10 killed); RG-58 warns exactly once
per load (`--list`, `doctor`, `history`, `<lane> --dry-run` each printed it once)
and `doctor` carries the matching WARN from the same function, exit code 0,
container/exec lanes untouched; RG-57's opt-outs (`RUN_GATE_PROFILE=off` →
`profile_error: "disabled (RUN_GATE_PROFILE=off)"`, nothing printed, nothing
started) and `--dry-run` (no session started, no token) behave as specified; the
token is injected into the child's environment on the daemon path only
(N7/M10 killed); R-36h containment holds under planted exceptions in the self-id
resolver, `ctl version`, `ctl start`, `getrusage` and `finish_bare_host_
profiling` (each test asserts the lane's own distinctive exit code survives);
RG-61's eight items are each present in the diff; the `footprint --write`
transcript in `CONSUMERS.md` matches the committed
`run-gate.footprint.json` and reproduces in shape on a read-only `footprint` run
(all three bare-host lanes now carry `[source: rusage-maxrss]`); `assay.toml`
carries RW-28's `budget_per_candidate = "900s"`; `__revision__ = 42` with a CHANGES
entry per RG id.

**Could not verify:** the daemon path end-to-end (`cgprofile-host-daemon` is
down and my dispatch forbids starting it) — `method: daemon`,
`scope: container-shared`, `targets_seen >= 1` and the DEVCONTAINER-WIDE line are
proven only by fixture-driven tests, not by a live session; and `assay-r2`
(not run — the survivor table is the fix-verification round's subject).

---

## Round-1 summary for the controller

The package does what RW-27/RW-27a/RW-27b asked, the containment discipline
(R-36h) is genuinely good, the three gates that could run are green and reproduce,
and the documentation sweep is real work rather than a checkbox. The blockers are
about the *number* the package now publishes (B1/B2), one new destructive-adjacent
surface (B3) and one sentence that this package itself made false (B4) — all four
have small, well-defined repairs. I expect one repair round plus the r2 survivor
table to close this.
