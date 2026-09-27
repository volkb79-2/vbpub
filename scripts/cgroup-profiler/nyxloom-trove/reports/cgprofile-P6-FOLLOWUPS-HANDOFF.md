# cgprofile-P6-FOLLOWUPS — daemon follow-ups (CP-4..CP-7, cgprofile.slice, CP-2 socket carrier, CP-8 watch, CP-9 placement)

**Implementer:** FRESH Sonnet session, checkpoint clause on (HARD, see below).
Second cgroup-profiler package of the RG-55 wave (controller rulings RW-27,
RW-29..RW-35; operator-authorised 2026-09-12: "build everything").
Records: `scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-{LOG,REPORT,BRIEF-n}.md`.
Release after review: cgprofile **1.1.0** (the controller runs `cmru release`).

## Where you work

```
git -C /workspaces/vbpub worktree add .worktrees/rg55-followups-cgprofile -b rg55-followups-cgprofile rg55-profiler-daemon
```

Base = the tip of branch `rg55-profiler-daemon` (P1, `16f3a29f` at dispatch;
P1 is finishing its mutation run and review — it may gain a few commits).
Work ONLY inside that worktree, project dir `scripts/cgroup-profiler/`.
You never touch `.worktrees/rg55-profiler-daemon`, `run-gate-project/run-gate.py`,
`ciu/src/`, `modern-debian-tools-python-debug/`, `/workspaces/dstdns`. The
controller tells you when to `git merge rg55-profiler-daemon` (or `main`
once P1 is merged) before your final gates.

## Orientation (in this order, before any edit)

1. Controller log `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
   — rulings RW-3, RW-7, RW-13..RW-16, RW-19, RW-21, RW-28, **RW-30, RW-31,
   RW-34, RW-35 (this package's decisions)**.
2. Design of record `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`
   — §2 D-17..D-26, §3, §4 (the example flow you are implementing the
   daemon half of), **A1 (D-27..D-29)**, **A2 (D-30)**.
3. Contract `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
   §1–§7 (v1, what P1 built) and **§8 (v1.1 — your spec)**; the mirror in
   `scripts/cgroup-profiler/docs/` must stay byte-identical (a test asserts it).
4. Backlog rows `scripts/cgroup-profiler/nyxloom-trove/backlog/CP-2, CP-4,
   CP-5, CP-6, CP-7` (mechanism + oracle sketch each); `INDEX.md`.
5. P1's `cgprofile-P1-DAEMON-REPORT.md` and `-LOG.md` (what exists:
   `lib/serve.py` serve loop + `ctl`, `lib/damon.py` KdamondPool,
   `lib/subtree.py` token resolver, `lib/summary.py` SummaryAccumulator,
   `lib/store.py`, the ciu templates, `cmru.toml`, `run-gate.toml` lanes).
6. `DESIGN.md`, `README.md` "Running the daemon", `docs/ATTACH-GUIDE.md`.
Run your own bounded greps (`head`/`cut` every output; never dump a whole
file into your context).

## Deliverables (commit each separately, in this order)

**C1 — CP-4** the run-id flake: widen the random suffix (8 hex) AND make
the test deterministic about what it asserts (uniqueness over 50 draws at
< 1e-6 collision probability, stated in the test's docstring); every
consumer of the id format updated (grep `\[0-9a-f\]{4}` / the format doc in
the contract §1.8 is `s-<stamp>-<4 hex>` — session ids stay 4 hex; only
`new_run_id` (the `run`/`attach` collector's id) changes; say so).

**C2 — CP-5** `events.jsonl` gets real rows: reuse `lib.events.Detector`
(the collector's own detector) inside the daemon's sampler on the same
cadence; one row per detected event (`limit_drift`, `memory_high_breach`,
`oom_kill`, refault burst) with the sample tick's timestamp; the Summary's
`events` counters stay computed exactly as today (goldens byte-identical —
prove it); `ctl report` renders them as markers (the renderer already can).

**C3 — CP-7** the manifest's `limits` table: resolve effective limits with
`lib.limits.effective()` for the session's cgroup at `start` (read-only
walk of the ancestor chain) and store them; `analyze.py` proposal checks
then work on daemon reports (one test with an oversubscribed fixture).

**C4 — CP-6** DAMON series in the report: `lib/analyze.py` gains a
`damon.jsonl` reader; `report_html` charts hot/warm/cold/idle bytes as one
figure; absent file → no figure, no error.

**C5 — `cgprofile.slice` (D-29) + `ctl host` §8.5.** Add
`scripts/cgroup-profiler/infra/cgprofile.slice` (`MemoryMin=128M`,
`MemoryHigh=768M`, `MemoryMax=1G`, `CPUWeight=100`, `IOWeight=50`, a
`Description` naming RG-55 D-29) + `infra/README.md` (operator install:
copy to `/etc/systemd/system/`, `daemon-reload`, `systemctl start`; what
happens when it is NOT installed: systemd creates the slice transiently and
unbounded, `ctl host` says so). The ciu compose template AUTHORS
`cgroup_parent: cgprofile.slice` (replacing the interactive-tier parent;
governance honours authored values — render from the standalone root and
show it in the REPORT). `ctl host` gains `gates_slice` and `daemon_slice`
exactly per contract §8.5 (`present: false` shape when absent; the gates
slice name comes from `serve --gates-slice`, default `dev-gates.slice`
under `dev.slice`). Goldens: `fixtures/rg55/host-v1.1.json`.

**C6 — CP-2 socket carrier (D-30, §8.1, §8.6).** The serve loop's listener
`/run/cgprofile/ctl.sock` becomes host-visible: the compose template
bind-mounts host `/run/cgprofile` → `/run/cgprofile`; at start the daemon
`chmod 0770` the directory and sets the SOCKET's owner to `root` and its
group to THE DIRECTORY'S gid (the host's `tmpfiles.d` entry `d
/run/cgprofile 0770 root docker -` shipped by mdt host-setup P8 is the
source of truth; a root:root directory means "socket carrier root-only
until host-setup is installed" — log one INFO line, exec keeps working),
mode `0660`. Request line on the socket is exactly §8.1
`{"verb": …, "args": {…}, "contract": 1}` — align the in-image `ctl` →
serve request shape to it (ctl is the reference translator; document the
`args` key names per verb in `docs/PROTOCOL.md`, new). Peer credentials:
`SO_PEERCRED`; uid 0 always allowed; `CGPROFILE_ALLOW_UIDS` (comma list,
compose env) restricts to the listed uids; refused → `peer-refused` per
§8.1 and close. `ctl version` gains `transports` per §8.6. Goldens:
`fixtures/rg55/socket/<verb>-request.json` and `<verb>-response.json` for
every verb (P5 codes the run-gate socket client against these bytes).
Parity test: one pytest that runs every verb through BOTH carriers against
a serve loop on a temp socket (exec carrier simulated by invoking `ctl`
in-process against the same socket) and diffs the JSON.

**C7 — CP-8 watch role (D-27, §8.2, §8.4).** File the backlog row CP-8
first (same shape as CP-5; provenance RW-30/RW-34). `start` gains
`--progress-stream`, `--idle-bound`, `--ceiling`, `--on-stall`
(`bad-policy` on unparsable values, session NOT started). Per session the
sampler maintains the §8.4 `liveness` block (activity = any of: a new
progress-stream line, cpu growth ≥ 1 s over the trailing 30 s, io bytes
growth; `stream.path` read through `/proc/<first token pid>/root/<path>`
— bounded reads: tail the last 64 KiB, parse the last complete line; the
`plan` event's `expect_next_event_within_s` is the cadence hint; terminal
events = `verdict`, `end`, `done`, `summary`); the idle clock PAUSES while
gates-slice or host memory `full avg10 > 5` (`paused_for_seconds`,
`pause_reason`). State machine exactly §8.4 (ok / stalled / hung / runaway
/ throttled / over_ceiling); `--on-stall kill` → `cgroup.kill` on the leaf
when placed, else SIGKILL to every pid of the token subtree (you have pid
host); verdict recorded as `watch {state, verdict, reason, readings,
policy}`; `status` and `stop` return it; Summary gains `liveness`/`watch`
(§8.7). `ctl watch <session>` streams §8.2 lines on both carriers (over
exec: `ctl watch` holds the socket open and forwards each line with a
flush; `--watch-interval` default 30, clamped [5, 300]; exactly one `end`).
Tests: a fake clock + fake sampler drive every state transition; a real
end-to-end test with a `sleep`-based token subtree that goes silent →
`stalled` → killed under `kill`, reported under `report`; pause under a
fake PSI reading; goldens `fixtures/rg55/watch-*.json`, `status-v1.1.json`,
`summary-v1.1.json` (v1 goldens byte-identical).

**C8 — CP-9 placement (D-20, D-25, §8.3).** File CP-9 first. `start
--place --memory-high --memory-max --cpu-weight`: create
`<gates slice>/rg-<token>`; if the gates slice's `cgroup.subtree_control`
lacks `memory`/`cpu`/`pids`, write `+memory +cpu +pids` to it (RW-35: the
ONLY non-leaf write, `+` only, never `-`); apply the caps; READ BACK into
`applied`; migrate every pid the token resolver discovers (`cgroup.procs`
of the leaf) — also pids found later; on `stop` move survivors back to the
container's scope and `rmdir` the leaf (retry 3× over 3 s; report
`placement.error` if it cannot); refusals per §8.3/§8.8 never fail
`start`. D-15 stays a whitelist: extend `WRITABLE_ROOTS`/the write guard so
the ONLY writable cgroup paths are `<gates slice>/cgroup.subtree_control`
(`+` values), `<gates slice>/rg-*/{cgroup.procs,memory.high,memory.max,
cpu.weight,cgroup.kill}`, the original scope's `cgroup.procs` (move-back
only) and `rmdir <gates slice>/rg-*`; a test plants a write to any other
cgroup file through the guard and proves the refusal. Goldens
`fixtures/rg55/start-placed-v1.1.json`.

**C9 — close-out.** `docs/PROTOCOL.md` complete; README "Running the
daemon" (slice unit, socket, host prerequisite), ATTACH-GUIDE lane section
(watch policy, placement), DESIGN.md (D-27..D-30 summary + pointers);
`CHANGES.md` `[Unreleased]` one line per CP id; backlog rows CP-2, CP-4..
CP-9 → FIXED with commit hashes; version string `1.1.0` wherever P1 set
`1.0.0` (grep); contract mirror byte-identical; `INDEX.md` regenerated the
way the project does it.

## Live probes (yours; the singleton is NOT yours)

The container `cgprofile-host-daemon` belongs to the controller (it may be
down, or up from `main`, at any time — never start, stop or `ciu down` it).
Probe with YOUR OWN instance from the image you build: `docker run --rm -d
--name cgprofile-p6-probe` with the same flags the compose template
renders (privileged, private PID/cgroup namespaces, `--network none`,
`--cgroup-parent cgprofile.slice`, read-only host `/proc` at `/hostproc`,
read-only host cgroup v2 at `/sys/fs/cgroup`, `CGPROFILE_PROC_ROOT=/hostproc`,
`-v /tmp/cgprofile-p6:/run/cgprofile`
— a SCRATCH host directory, never the real `/run/cgprofile`), remove it in
a `finally`. Probes to record in the REPORT: every verb over both carriers
diffed (from a throwaway `cmru-enroll-fixture:local` container that mounts
`/tmp/cgprofile-p6` — `docker` group membership of the probe's user
matters, show it); a placed exec-mode probe (80 MiB lane) → leaf exists
during, `applied` read back, gone after `stop`; a watch probe (`sleep`
subtree, `--idle-bound 20 --on-stall kill`) → `stalled`/`killed` in ≤ 60 s;
`ctl host` showing `gates_slice.present` (true only if the operator has
installed P8's unit; either value is fine, report what you see).

## Gates (verdict in a SEPARATE step, never a pipe tail)

While iterating: `nice -n 19 ionice -c 3 python3 -m pytest tests/<file> -q`
(targeted files, SERIAL). Package gates on the final tip via the lanes
registered in `scripts/cgroup-profiler/run-gate.toml` (read it): the
r0/r1 lane and the r3 canary after each cluster at most once per cluster,
the r2 mutation lane LAST and once (`judge.mutation.budget_per_candidate
= "600s"` is already set — keep it), `--base rg55-profiler-daemon` (or
`main` after the controller announces P1's merge). 100% line AND branch on
every changed line. Every r2 survivor: a killing test or a written
equivalent-mutant justification in the REPORT (RW-20/RW-22). One resume
after triage.

## Records

LOG: one entry per commit (self-hash rule) + a running tool-call counter.
REPORT: per-deliverable evidence — the oracle each CP row sketched and the
test that implements it, mutation-check transcripts (≥ 3 planted mutants
per cluster, which test caught each), the live probe transcripts (both
carriers diff, placement, watch), the docs disposition table, the survivor
table, an E-002 telemetry section. Claim only what you ran.

## Checkpoint clause (E-008, HARD)

ARM at ~120k context or ~60 tool calls (whichever first); CUT at the next
coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster
end; never on a red gate). At the cut: `cgprofile-P6-FOLLOWUPS-BRIEF-n.md`
(state, tip hash, what remains, exact next command) + a self-authored
retention prompt, commit, return. Never push past ~90 calls. The
controller dispatches a FRESH successor from the brief. Long lanes (r2)
are launched UNTRACKED (`nohup … > <scratchpad>/p6-r2.log 2>&1 & disown`)
with a cheap tracked `until ! kill -0 <pid>; do sleep 60; done` watcher
(RW-26: the Claude Code low-memory guard kills tracked background commands).

## BLOCKED protocol

Decision asks NEVER stop the package (RW-9): write the ask into the LOG
under "Decision asks", take the option the contract §8 / the design doc
already favours, mark it, continue. Return early only for a red gate you
cannot make green honestly or a contradiction between two binding
documents — name both.

## HOST LOAD (binding)

8 cores, 16 GiB, shared with a PRODUCTION game server; PSI is the signal
(`/proc/pressure/{cpu,memory}`; back off when memory `full avg10` > 5).
Two mutation runs are live estate-wide when you start: P1's container
`run-gate-vbpub-r2-…` and P2's bare-host `assay-r2` (pid 2415767). While
EITHER is alive: targeted pytest files ONLY, serial, `nice -n 19 ionice -c 3`;
no whole-suite run, no run-gate lane, no image build. After both exit:
the r0/r1 lane, r3, then r2 only when `pgrep -af 'assay-r2|assay.cli run r2'`
and `docker ps` show no other mutation run. ≤ 2 gate containers
estate-wide; `docker update --cpus=3` after any launch; remove in a
`finally`. No container may use host PID/cgroup/network namespace modes.

Commit trailers (every commit): `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`
and `Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
Edit tool for file changes. Claim only what you ran — a fresh adversarial
reviewer verifies every claim.
