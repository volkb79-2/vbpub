# run-gate-WAVE-RG55-P4 — run-gate follow-ups (RG-57, RG-58, RG-59, RG-60, RG-61)

**Implementer:** FRESH Sonnet session, checkpoint clause on. Third track of
the RG-55 wave (controller ruling RW-27, operator-authorised 2026-09-12:
"start work on new backlog entries you filed as well and fold them in").
Records: `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-{LOG,REPORT,BRIEF-n}.md`.

## Where you work

```
git -C /workspaces/vbpub worktree add .worktrees/rg55-followups-run-gate -b rg55-followups-run-gate 186461de
```

Base `186461de` = the P2 package's ACCEPTED close-out tip (branch
`rg55-run-gate-client`, review round 2 ACCEPT). Work ONLY inside that
worktree, project dir `run-gate-project/` (`run_gate.py` → `run-gate.py`,
one inode; tests import `run_gate`). Nobody else works in your worktree;
you never touch `.worktrees/rg55-run-gate-client`, `.worktrees/
rg55-profiler-daemon`, `scripts/cgroup-profiler/`, `ciu/`,
`/workspaces/dstdns`. P2 may still receive a few survivor-triage test
commits; the controller will tell you when to `git merge
rg55-run-gate-client` (or `main` once P2 is merged) before your final gates.

## Orientation (in this order, before any edit)

1. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
   — Rulings RW-5, RW-6, RW-8, RW-9, RW-11, RW-12, RW-17, RW-21, RW-24,
   RW-26, **RW-27 (this package's decisions)**.
2. `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` entries RG-57, RG-58,
   RG-59, RG-60, RG-61 — the five deliverables, each with mechanism,
   proposed contract and oracle sketch. They are the spec; RW-27 settles
   the open choices.
3. `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §2, §4.1,
   §4.3 (token, scope `container-shared`, subtree resolver);
   `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-REPORT.md`
   (what P2 built: `ProfilerClient`, `BasicSampler`, `ResourceAccumulator`,
   history schema 2, `footprint`, `doctor` profiler check, RW-17 override).
4. `SPEC.md` R-04, R-29, R-30, R-33, R-35a, R-36, R-39, R-41, R-43 a–h,
   R-44; `CONSUMERS.md` §6 + "The footprint manifest"; `LANE-AUTHORING.md`
   resources section; `CHANGES.md` `[Unreleased]`.
5. `run-gate.py`: `run_bare_host_lane`, `run_exec_lane`,
   `run_container_lane`, `write_inflight_record`, `ProfilerClient._ctl`,
   `cmd_doctor`, `load_config`/lane validation, `usage()`.

## Deliverables (commit each separately, in this order)

**C1 — RG-60** exec-lane inflight record: `run_exec_lane` writes the same
inflight record `run_container_lane` writes (after the profiling session is
established, before the exec begins) and clears it on every exit path
(`finally`). Tests mirror the container-lane wiring tests; one test kills
the client mid-run (simulated) and asserts the record survives on disk.
SPEC R-39/R-43f wording follows the code.

**C2 — RG-59** live-run warning names the real cause: when `_ctl`'s docker
exec fails and `stderr_tail` matches docker's own "No such container" /
"is not running", the warning says `profiler daemon not running — basic
in-lane sampling only (start it: cd scripts/cgroup-profiler && ciu up)`
(the SAME text `cmd_doctor` uses — factor one constant, do not duplicate
the string); "produced unparsable stdout" stays reserved for a running
daemon's malformed response. Two tests, one per branch.

**C3 — RG-58** (RW-27a: option 2, warn — never refuse): a bare-host lane
declaring `stall_timeout` gets ONE load-time `run-gate: WARNING lane
<name>: stall_timeout is inert on a bare-host lane (no watch, no timer;
R-40 applies to container/exec lanes only)` and a matching `doctor` WARN
(same constant). Config still loads, exit code unchanged. Tests: load-time
warning present exactly once, doctor WARN, container/exec lanes untouched.

**C4 — RG-57** bare-host lanes ARE profiled (RW-27b: both halves, exactly
as the entry's "Proposed contract" + "Alternative" describe):
- Daemon path: export `RUN_GATE_PROFILE_SESSION=<token>` into the bare-host
  child's environment (the same per-invocation token generator P2 uses);
  resolve run-gate's OWN container id once per process (`/etc/hostname`
  first, `docker inspect` of that hostname to confirm; if neither yields a
  container id — run-gate not in a container — the daemon path is skipped
  with a disclosed reason); `ctl start --target containerid:<self>
  --scope container-shared --token <token>`; `stop` after `wait()`; the
  summary is stored exactly like an exec lane's `container-shared` numbers
  (`method: daemon`, `scope: container-shared`, `source: sampled-max`),
  disclosed as devcontainer-wide.
- Daemon-absent path: `resource.getrusage(RUSAGE_CHILDREN)` delta around
  `wait()` → `method: "rusage"`, `memory.peak_bytes = ru_maxrss * 1024`
  (Linux reports KiB), `source: "rusage-maxrss"` (largest single child,
  NOT a sum — say so in SPEC and `history` docs), `cpu_seconds = utime +
  stime`, `cpu_cores_avg` derived, everything getrusage cannot give
  (`pressure`, `damon`, `events`, `host`) is `null`, never fabricated.
  NO basic cgroup sampler on this path (RW-27b keeps the entry's shape).
- R-36h discipline unchanged: any failure in either path is a warning and
  the lane's verdict/exit code stay the lane's. Plant exceptions in both
  paths in tests and prove it.
- History schema stays 2; `method` gains the value `rusage`; `stats`,
  `history --json`, `footprint` treat rusage entries as profiled PASS runs
  (they carry duration, cpu and a peak) — `footprint` and `doctor` print
  `source: rusage-maxrss` next to such medians so a reader knows the
  caveat. SPEC R-43 gains a sub-rule for bare-host lanes (R-43i); R-36
  series doc updated; CONSUMERS §6 and LANE-AUTHORING updated; RG-57 →
  FIXED with evidence; `RUN_GATE_PROFILE=off` and `profile = false` still
  opt out; `--dry-run` discloses the plan; conjunction lanes unchanged.
- Consequence to exploit: run-gate-project's own five lanes are bare-host,
  so after C4 `./run-gate.py selftest` itself records a profile and
  `footprint --write` STOPS refusing for this project — that produces the
  REAL transcript RG-61 item 5 needs and the tracked
  `run-gate-project/run-gate.footprint.json` the wave's goal names.

**C5 — RG-61** the eight-item documentation sweep, all eight, after the
code above so nothing re-drifts; item 5's transcript is captured from a
real `footprint --write` on this project (after C4 + one profiled
selftest PASS); item 8 adds commit hashes to the FIXED entries you touch.
`usage()` gains `RUN_GATE_PROC_ROOT`. `__revision__ = 42`; every CHANGES
`[Unreleased]` entry names its RG id and the rev bump.

## Gates (verdict in a SEPARATE step, never a pipe tail)

While iterating: `nice -n 19 ionice -c 3 python3 -m pytest tests/<file> -q`
(targeted files, SERIAL). Package gates on the final tip, each at most
once, in this order: `./run-gate.py selftest` (bare-host; RG-53 judge —
100% line AND branch on every changed line vs `--base rg55-run-gate-client`
after the merge the controller announces, else `--base 186461de`),
`./run-gate.py --base <same> assay-r1`, `assay-r3`, and LAST `assay-r2`
(mutation, `jobs = 2` is already in `assay.toml`). Every survivor: a test
that kills it, or a written equivalent-mutant justification in the REPORT
(RW-20/RW-22 — the justification shows WHY behaviour is identical). One
resume after triage (`.assay/mutation-state/` resumes by candidate
content). `gate-full` if it exists in `run-gate.toml`.

## Records

LOG: one entry per commit (self-hash rule: the hash lands in the NEXT
commit's entry). REPORT: per-deliverable evidence — the oracle each backlog
entry sketched and the test that implements it, mutation-check
transcripts, the live `footprint --write` transcript, the docs disposition
table, the survivor table, an E-002 telemetry section (orientation call
count, what was missing from this handoff). Claim only what you ran.

## Checkpoint clause (E-008)

ARM at ~120k context or ~60 tool calls (whichever first); CUT at the next
coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster
end; never on a red gate). At the cut: `run-gate-WAVE-RG55-P4-BRIEF-n.md`
(state, tip hash, what remains, exact next command) + a self-authored
retention prompt, commit, return. The controller dispatches a FRESH
successor from the brief. Long lanes (assay-r2) are launched UNTRACKED
(`nohup … > <scratchpad>/p4-r2.log 2>&1 & disown`) with a cheap tracked
`until ! kill -0 <pid>; do sleep 60; done` watcher (RW-26: the Claude Code
low-memory guard kills tracked background commands).

## BLOCKED protocol

Decision asks NEVER stop the package (RW-9): write the ask into the LOG
under "Decision asks", take the option the backlog entry or RW-27 already
favours, mark it, continue. Return early only for a red gate you cannot
make green honestly or a contradiction between two binding documents —
name both.

## HOST LOAD (binding)

8 cores, 16 GiB, shared with a PRODUCTION game server; PSI is the signal
(`/proc/pressure/{cpu,memory}`; back off when memory `full avg10` > 5).
Two mutation runs are live estate-wide when you start: P1's container
`run-gate-vbpub-r2-…` (until ~15:30Z) and P2's bare-host `assay-r2`
(pid 2415767, until ~16:20Z). While pid 2415767 is alive: targeted pytest
files ONLY, serial, `nice -n 19 ionice -c 3`; no whole-suite run, no assay
lane. After it exits: the whole selftest at most once, then r1/r3, then r2
only when `pgrep -af 'assay-r2|assay.cli run r2'` shows no other mutation
run. ≤ 2 gate containers estate-wide (`docker ps` for `tester-unified:local`
first); `docker update --cpus=3` after any launch; remove in a `finally`.
Never `--cgroupns=host`/`--pid=host`. Any container you create for a probe
is removed in the same step.

Commit trailers (every commit): `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
and `Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
Edit tool for file changes. Claim only what you ran — a fresh adversarial
reviewer verifies every claim.
