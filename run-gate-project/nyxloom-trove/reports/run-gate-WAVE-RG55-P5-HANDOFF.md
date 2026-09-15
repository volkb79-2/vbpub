# run-gate-WAVE-RG55-P5 — run-gate client v1.1 (RG-63 transport/watch/placement, RG-56 admission)

**Implementer:** FRESH Sonnet session, checkpoint clause on (HARD).
Fourth run-gate package of the RG-55 wave (rulings RW-29..RW-35). Records:
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P5-{LOG,REPORT,BRIEF-n}.md`.
Release after review: run-gate **23.9.0**, `__revision__ = 43` (the
controller runs `cmru release` and installs the wheel).

## Where you work

```
git -C /workspaces/vbpub worktree add .worktrees/rg55-client-v11 -b rg55-client-v11 <base>
```

`<base>` is named in the dispatch message: `main` after P4 (run-gate 23.8.0,
rev 42) AND P6 (cgprofile 1.1.0) have merged. Work ONLY inside that
worktree, project dir `run-gate-project/` (`run_gate.py` → `run-gate.py`,
one inode; tests import `run_gate`). Never touch `scripts/cgroup-profiler/`
(consume its fixtures and docs only), `ciu/src/`,
`modern-debian-tools-python-debug/`, `/workspaces/dstdns`, the adoption
brief (controller's).

## Orientation (in this order, before any edit)

1. Controller log rulings RW-5, RW-6, RW-12, RW-17, RW-21, RW-27, **RW-29..
   RW-35**.
2. Design of record `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`
   §2 D-17..D-26, §3, **§4 (the example flow — you implement the run-gate
   half)**, A1 (D-27..D-29), A2 (D-30).
3. Contract `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
   §1–§7 and **§8 (v1.1) — §8.9 is your obligations list**; goldens
   `fixtures/rg55/` incl. `socket/`, `watch-*`, `start-placed-v1.1`,
   `host-v1.1`, `status-v1.1`, `summary-v1.1` (P6 wrote them; you assert
   bytes, never re-derive).
4. `run-gate-WAVE-RG55-P2-REPORT.md`, `-P4-REPORT.md` (what exists:
   `ProfilerClient`, `_ctl`, `BasicSampler`, `ResourceAccumulator`, the
   rusage path, inflight records for every lane kind, `footprint`,
   `doctor`), `scripts/cgroup-profiler/docs/PROTOCOL.md` (P6: request
   shapes per verb), `cgprofile-P6-FOLLOWUPS-REPORT.md` (probe evidence).
5. `SPEC.md` R-04, R-09, R-29, R-30, R-36, R-39, R-40, R-41, R-43 a–i,
   R-44; `CONSUMERS.md` §6; `LANE-AUTHORING.md`; `CHANGES.md`;
   backlog RG-56 (exists) — RG-63 you file (C1).

## Deliverables (commit each separately, in this order)

**C1 — backlog rows.** File **RG-63** (policy author / watch consumer /
transport seam / placement requests — mechanism, proposed contract, oracle
sketch per row convention, provenance RW-29..RW-35) and update RG-56's
"next wave" wording to "this package (P5)". No code.

**C2 — transport seam (D-30, §8.1).** `[profile] transport = "auto" |
"exec" | "socket"` (default `auto`; env `RUN_GATE_PROFILE_TRANSPORT`
overrides, invalid → refuse at load like every other key) and
`socket_path` (default `/run/cgprofile/ctl.sock`). `ProfilerClient` keeps
ONE request builder and ONE response parser behind a two-line carrier
seam: exec = today's `docker exec … cgprofile ctl <verb> … --json` argv;
socket = stdlib `socket.socket(AF_UNIX)` connect → write exactly one §8.1
request line (`{"verb", "args", "contract": 1}`, args per PROTOCOL.md) →
read to EOF → parse; per-verb §1.5 numbers become socket timeouts. `auto`
= socket when the path exists AND `version` answers on it within 5 s, else
exec (decided once per process, disclosed in the "profiler …" line as
`via socket|exec`). Every failure on either carrier stays R-36h (warning,
never a verdict change) — plant exceptions in both carriers. `doctor`
probes BOTH and prints which is live and why the other is not (path
absent / permission denied / no answer / docker exec failed). `--dry-run`
discloses the carrier. Goldens `fixtures/rg55/socket/*-request.json` are
asserted byte-for-byte against what your builder emits for each verb.

**C3 — gates slice default (D-19, D-24).** Ephemeral lane containers get
`--cgroup-parent "$CGROUP_PARENT_DEV_GATES"` when that variable is set and
non-empty (unset → today's behaviour, disclosed once by `doctor` as "gates
slice: not configured — lanes run under the default parent"); a docker
`run` failure whose stderr names the cgroup parent is reported with a
WARNING naming mdt host-setup (`dev-gates.slice` not installed) and the
lane FAILS as any other docker failure (never silently re-run elsewhere).
SPEC R-30 amended; CONSUMERS names the variable; `doctor` shows it.

**C4 — policy author (D-27, §8.4).** At `ctl start` run-gate passes:
`--progress-stream <path as the lane sees it>` for lanes that have one
(assay lanes: the lane's progress NDJSON; command lanes with a log stream:
that path; else omitted); `--idle-bound <stall_timeout>` when the lane
declares `stall_timeout`, else `auto`; `--ceiling auto`; `--on-stall kill`
when the lane declares `stall_timeout` (today's R-40 semantics keep their
teeth, now enforced by the daemon), else `report`. The "profile session"
disclosure line gains `policy idle {n|auto} ceiling {n|auto} on-stall
{kill|report}`. `bad-policy` (exit 2) is a warning + basic fallback like
any other `start` failure.

**C5 — watch consumer (D-27/D-28, §8.2, §8.9-2).** One `ctl watch
<session>` per daemon session: over exec a long-lived `docker exec …
cgprofile ctl watch … --json` `Popen`, over the socket one connection; a
single reader thread feeds a queue; the existing wait tick drains it (no
second poller; RW-12's tick shape unchanged; the reader thread is the ONLY
thread and its death is a warning). Each `reading` updates the inflight
record's `watch` field; a `verdict` with `killed` → the lane's R-40 stall
exit path (same exit code as today's stall) with the daemon's `reason` in
the record and on stderr; `reported` → ONE WARNING line, no verdict
change; `end` without a verdict → nothing. Idle timeout 3 × watch interval
→ re-attach once, then fall back. Without a daemon (or when `watch`
fails), the in-process ProgressWatch is the fallback and now judges
progress per D-22: idle bound `auto` = max(300 s, 3 × the stream's own
`expect_next_event_within_s` from its `plan` event) and the clock PAUSES
while `/proc/pressure/memory` `full avg10 > 5`; disclosed once ("in-process
watch — daemon unavailable"). SPEC R-40 is rewritten as R-40a (daemon
watch) + R-40b (in-process fallback) — RG-36/RG-41 stall semantics stay
the documented behaviour for lanes that declare `stall_timeout`.

**C6 — placement requests (D-20, §8.3, §8.9-3).** Exec and bare-host
lanes only (ephemeral containers stay docker-capped): `--place
--memory-high <request> --memory-max <ceiling>`; request = the lane's
`resources.memory` (new key, `budget`-style size grammar `512M`/`2G`)
else the footprint manifest's `memory_peak_bytes.median × 1.5` (disclosed
"derived") else no placement (disclosed "no request"); ceiling =
`resources.memory_max` else `min(2 × request, host.gates_slice.
memory_max_bytes)`. `placement.applied` (read back by the daemon) lands in
the record; `place-refused:*` is a WARNING, the lane runs unplaced;
`throttled` readings → ONE WARNING ("lane throttled at memory.high —
raise resources.memory or the gates slice"). R-43 gains sub-rules;
LANE-AUTHORING documents `resources.memory`/`memory_max`; `--dry-run`
prints the placement plan.

**C7 — RG-56 admission (D-6 → decided here: wait-then-proceed, never
refuse).** Before starting a lane with a daemon: `ctl host`; if
`gates_slice.present` and `memory_current_bytes + request >
0.9 × memory_max_bytes` (request per C6; ephemeral lanes use the manifest
median or `resources.memory`), wait up to `[profile] admission_wait`
(duration grammar, default `"10m"`) polling every 30 s with one stdout line
per minute (`run-gate: admission: gates slice {cur} MiB of {max} MiB used,
lane needs {req} MiB — waiting ({elapsed})`); after the wait proceed with
ONE WARNING. No daemon or no gates slice → admission skipped, one INFO
line. Exit codes unchanged. Tests drive it with a fake `ctl host` and a
fake clock.

**C8 — records.** Inflight and history entries gain `watch` and
`placement` (nullable); history schema STAYS 2 (additive — re-prove RG-27's
two traps on the new keys); `history --json` prints them; `stats` and
`footprint` unchanged in v1.1.

**C9 — close-out.** SPEC R-40a/b, R-43 j–l, R-45 (admission), Rev bump;
README; CONSUMERS §6 (transport, gates slice, admission); LANE-AUTHORING;
CHANGES `[Unreleased]` per RG id; RG-56 + RG-63 → FIXED with evidence;
`__revision__ = 43`; `usage()`; the `doctor` output in the REPORT.

## Live probes (yours; daemon state is announced in the dispatch message)

The real daemon `cgprofile-host-daemon` (1.1.0, controller-managed) should
be UP; do not start/stop it. This devcontainer has NO `/run/cgprofile`
mount (rebuild pending) so `auto` resolves to exec here — prove the socket
carrier from a throwaway probe container that mounts `/run/cgprofile`
(`tester-unified:local`, `--group-add <host docker gid>`, the tip's
`run-gate.py` + a 100 MiB command lane, `transport = "socket"`) and diff
its record against the same lane over exec. Then: a stalling command lane
(`sleep 600` with `stall_timeout = "30s"`) → daemon verdict `killed`,
run-gate's stall exit code, the reason in the record; a bare-host lane
with `resources.memory = "256M"` → `placement.applied` in the record and
the leaf visible in `ctl status` during the run; `doctor` output. Remove
every probe container in a `finally`.

## Gates (verdict in a SEPARATE step, never a pipe tail)

Targeted files while iterating (`nice -n 19 ionice -c 3 python3 -m pytest
tests/<file> -q`, SERIAL). Final tip, each once, in order: `./run-gate.py
selftest` (RG-53: 100% line AND branch on every changed line vs `--base
<base>`), `assay-r1`, `assay-r3`, LAST `assay-r2` (`budget_per_candidate =
"900s"` is set; keep it), `gate-full` if present. Survivors: a killing test
or a written equivalent-mutant justification (RW-20/RW-22). One resume.

## Records

LOG per commit (self-hash rule) + tool-call counter. REPORT: per-deliverable
evidence (oracle → test), mutation-check transcripts (≥ 3 planted mutants
per cluster), the live probe transcripts (exec/socket diff, kill, placement,
doctor), docs disposition table, survivor table, E-002 telemetry. Claim
only what you ran.

## Checkpoint clause (E-008, HARD)

ARM at ~120k context or ~60 tool calls; CUT at the next coherent boundary
(green gate > commit > LOG/REPORT write > edit-cluster end; never red);
`run-gate-WAVE-RG55-P5-BRIEF-n.md` + retention prompt, commit, return.
Never past ~90 calls. Long lanes UNTRACKED (`nohup … & disown`) + a cheap
tracked `until ! kill -0 <pid>; do sleep 60; done` watcher (RW-26).

## BLOCKED protocol

Decision asks never stop the package (RW-9): LOG "Decision asks", take
the option contract §8 / the design doc favours, mark it, continue. Return
early only for a red gate you cannot make green honestly or a
contradiction between two binding documents — name both.

## HOST LOAD (binding)

8 cores, 16 GiB, shared with a PRODUCTION game server; PSI is the signal
(`/proc/pressure/{cpu,memory}`; back off when memory `full avg10` > 5).
Serial pytest under `nice -n 19 ionice -c 3`; ≤ 2 gate containers
estate-wide (`docker ps` first); `docker update --cpus=3` after any launch;
one mutation run per project estate-wide (`pgrep -af 'assay-r2|assay.cli
run r2'`); never `--cgroupns=host`/`--pid=host`; every probe container
removed in the same step.

Commit trailers (every commit): `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
and `Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
Edit tool for file changes. Claim only what you ran — a fresh adversarial
reviewer verifies every claim.
