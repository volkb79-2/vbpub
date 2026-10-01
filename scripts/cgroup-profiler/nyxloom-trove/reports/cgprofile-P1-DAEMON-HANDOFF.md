# cgprofile-P1-DAEMON — handoff (RG-55 wave, package P1)

**Controller:** vbpub session (Fable 5.1). **Implementer:** fresh Sonnet.
**Worktree:** `/workspaces/vbpub/.worktrees/rg55-profiler-daemon` (branch
`rg55-profiler-daemon`, from `main` at the P0 freeze commit). **Project dir:**
`scripts/cgroup-profiler/` inside that worktree. Work ONLY there. Read this
file from the MAIN checkout path if it is not in your worktree.

Plan of record: `/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
(read §1–§3, §5 "P1", §6–§10). Contract (FROZEN, incl. §7 computation
rules): `/workspaces/vbpub/run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
(a verbatim copy is at `scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md`).
Golden fixtures: `/workspaces/vbpub/run-gate-project/nyxloom-trove/fixtures/rg55/`
(read its `README.md` first). Controller log:
`/workspaces/vbpub/run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
(rulings RW-n bind you; RW-3 is your first commit).

Every decision below is DECIDED. Do not re-open D-1..D-16 of the plan. A
decision ask that the plan, the contract and the rulings do not settle goes
into your REPORT §"Decision asks" and your return message — never decide a
product question on silence, never stop on one either: do everything that
does not depend on it.

---

## 1. Read list (in this order, in full unless a range is given)

1. The plan sections named above; the contract; `fixtures/rg55/README.md`.
2. `scripts/cgroup-profiler/DESIGN.md` §1 (tier split — the daemon is
   COLLECTOR tier: stdlib only, by contract), §3, §4.4 sampler, §4.7 damon,
   §4.8 caps, §6/§6a; `ATTACH-GUIDE.md` in full; `README.md`.
3. `lib/util.py`, `lib/metrics.py`, `lib/sampler.py`, `lib/targets.py`,
   `lib/damon.py`, `lib/store.py`, `lib/events.py`, `lib/limits.py`,
   `lib/access.py`, `lib/caps.py` (to know what you must NOT call),
   `cgprofile.py` (whole), `tests/conftest.py` + one sampler test file +
   one damon test file (conventions: fake sysfs/proc roots via parameters,
   injected clocks).
4. `pyproject.toml` (coverage `fail_under = 100`, `source` list —
   ADD every new module there), `run-gate.toml`, `assay.toml`,
   `tools/gate.sh`, `tools/canary-run.sh`, `nyxloom-trove/nyxloom.toml`.
5. The pwmcp shape (image + ciu stack + cmru): `pwmcp/cmru.toml`,
   `pwmcp/build-push.py`, `pwmcp/docker-bake.hcl`, `pwmcp/bundle.toml`,
   `pwmcp/ciu.global.defaults.toml.j2`, `pwmcp/ciu.defaults.toml.j2`,
   `pwmcp/ciu.compose.yml.j2`, `pwmcp/ciu.toml.j2`, `pwmcp/README.md`
   (the "sub-stack vs standalone root" paragraph), root
   `cmru.orchestration.toml`.
6. ciu: `ciu/src/ciu/governance.py` lines 290–340 (`resolve_cgroup_parent`)
   and ~1395–1445 (fragment injection honours author-set keys), and how a
   `.j2` template reads an environment variable (grep ciu docs / pwmcp
   templates for `env`/`environ`/`lookup`); `ciu --help` for `ciu up/down/
   check/render` from a standalone root.
7. `modern-debian-tools-python-debug/host-setup/units/dev-interactive.slice.in`
   (the tier the daemon lives in) and memory rule text in the plan §7.

## 2. Deliverables (commit order; the gate is green after every commit)

**C0 — RW-3.** Re-apply the one-liner: `cgprofile.py` `cmd_targets`, helper
spec must use `DEFAULT_OUT` not `HERE` as its second argument (see the
controller log). Own commit, message names RW-3.

**C1 — `lib/summary.py`: the incremental accumulator implementing contract §7
exactly.** Input: successive sample dicts (the cgroup files of the target
scope, the slice's files, host `/proc/pressure/*` + loadavg + meminfo, the
optional DAMON class bytes); output: the Summary object of contract §3 with
the exact key set. Percentiles are nearest-rank over stored per-sample
values (store the ints; 1/s for hours is fine). Null discipline per §1.7/§7.
Frame-driven tests MUST reproduce `summary-v1.json` (scope
`container-shared`) and `summary-container-v1.json` (scope `container`)
byte-for-byte after `json.dumps(sort_keys=True, indent=2)`.

**C2 — `lib/subtree.py`: token subtree resolver.** Given a cgroup path and a
token: pids = `cgroup.procs`; a pid belongs to the lane if its
`/proc/<pid>/environ` (NUL-separated, read with `errors="replace"`) carries
`RUN_GATE_PROFILE_SESSION=<token>`, plus every descendant (walk
`/proc/<pid>/task/*/children` when present, else a ppid map from
`/proc/*/stat`, parsing past the last `)`). Re-resolve every discovery
interval (2 s); `targets_seen` = distinct pids ever attributed. Proc root
injectable for tests. Unreadable environ (zombie, vanished) = skip, never
fail.

**C3 — DAMON multiplexing (`lib/damon.py`).** Introduce `KdamondPool` that
owns `nr_kdamonds`: allocate the lowest free index (growing `nr_kdamonds`
only when no free index exists), NEVER shrink `nr_kdamonds` below the
highest live index (shrinking tears down higher-indexed kdamonds — the
existing teardown comment), mark an index free on session stop and reuse
it. `DamonSession` takes an index from the pool; targets = the subtree's
pids as vaddr targets, re-committed on topology change. Classification
hot/warm/cold/idle per aggregation using the classifier already reachable
from this tier (`lib/damon.py`'s own, or a stdlib port of
`scripts/damon-analysis/lib/damon_analysis.py` `Classifier` — pure Python;
attribute it). Report thresholds in the summary. DAMON absent / sysfs
read-only → `status: "unavailable"`, reason string, session continues.
Fake-sysfs tests: two concurrent sessions get indices 0 and 1; stopping 0
while 1 lives does NOT write `nr_kdamonds`; a third session reuses 0;
stopping all shrinks back to the pre-daemon value.

**C4 — `lib/serve.py`: the session server.** `SessionServer` listening on a
Unix socket (`/run/cgprofile/ctl.sock`, path injectable), JSON-lines
request/response, one thread per session running `lib/sampler.Sampler`
with a FIXED cadence (hot = idle = `interval`; the adaptive back-off is
off for daemon sessions — the contract's rules assume a fixed cadence),
sampling the target cgroup (`metrics.sample_cgroup` with all groups), its
slice, and the host (`metrics.sample_host`), feeding `summary.py` and
`store.py` (series: `samples.jsonl.gz`, `host.jsonl`, `damon.jsonl`,
`events.jsonl`, `manifest.json`, `summary.json` on stop). Registry: live
sessions in memory + `manifest.json` on disk; on daemon restart, sessions
that were live are finalized as `aborted: daemon-restarted` with their
partial summary. Idempotent `start` by (container id, token); idempotent
`stop`; `max_sessions` (default 16) → `too-many-sessions`. Retention on
every stop: keep the newest `--keep-sessions` (200) finished sessions and
drop finished sessions older than `--keep-days` (14); never a live one.
`status` and `host` per contract §2.3/§2.4 (slices: `dev.slice`, every
`*.slice` directly under it, plus `--observe-slices`). SIGTERM/SIGINT →
stop every session (summaries written as `aborted: daemon-stopped`), close
the socket, exit 0. Host-mutation boundary: ONE write helper with a
`WRITABLE_ROOTS` allowlist = {sessions dir, `/sys/kernel/mm/damon/admin`};
a test asserts every write in `lib/serve.py`/`lib/damon.py` goes through
it; `serve` refuses `--cap` and never imports `caps.TempCaps`.

**C5 — CLI.** `cgprofile serve [--sessions DIR] [--socket PATH]
[--damon-default on|off] [--interval 1.0] [--keep-sessions 200]
[--keep-days 14] [--observe-slices a.slice,b.slice] [--max-sessions 16]`
(runs as PID 1 in the container; refuses `--cap`, refuses to run without
a host cgroup view — `access.have_host_cgroup_view()` — naming the flags
the container needs). `cgprofile ctl <version|start|status|host|stop|report|gc>`
per contract §2, always emitting ONE JSON document on stdout, exit codes
per §1.3; `ctl` is a thin socket client with a 25 s socket timeout. Every
golden response fixture (`version-v1`, `start-v1`, `status-v1`,
`stop-v1`, `error-v1`, `host-v1`) is reproduced by a round-trip test over a
real Unix socket in a temp dir with fake cgroupfs/proc roots.

**C6 — image.** `scripts/cgroup-profiler/Dockerfile`: base `python:3.14-slim`
(DESIGN.md §6 names 3.14 — if that tag cannot be pulled here, use the
newest 3.x slim available and record it), copy `cgprofile.py lib/ docs/`,
build a venv from `requirements.txt` (the lock) for the report tier only,
a `/usr/local/bin/cgprofile` wrapper (collector verbs on system python,
`report` on the venv — mirror the `cgprofile` bash shim's split), OCI
labels incl. `org.opencontainers.image.revision`, `ENTRYPOINT
["cgprofile","serve"]`. `build-push.py` + `docker-bake.hcl` in the pwmcp
shape (`--build` tags `cgprofile:local` and
`ghcr.io/volkb79-2/cgprofile:<ver>`; `--push` only with the version env).
No network fetch at runtime; everything installed at build.

**C7 — ciu stack (standalone root in `scripts/cgroup-profiler/`).**
`ciu.global.defaults.toml.j2` + `ciu.defaults.toml.j2` + `ciu.compose.yml.j2`
+ `ciu.toml.j2` (+ `ciu.global.toml.j2` if the pwmcp shape needs it):
`deploy.project_name = "cgprofile"`, `deploy.environment_tag = "host"`
(a DELIBERATE host singleton — write the comment: one daemon per host,
never per instance; CIU-104's collision reasoning applies in reverse);
service `daemon`: image from the defaults table (`cgprofile:local` for
dev, ghcr tag for release), `container_name: cgprofile-host-daemon`,
`privileged: true`, `pid: host`, `cgroupns: host`, `network_mode: none`,
`restart: unless-stopped`, volume `cgprofile-sessions:/var/lib/cgprofile`,
and `cgroup_parent` AUTHORED in the service block from the INTERACTIVE
tier (`$CGROUP_PARENT_DEV_INTERACTIVE`; find the ciu-supported way for a
template to read the environment — pwmcp/ciu docs show it — and REFUSE at
render when it is unset: no hardcoded slice fallback, AGENTS.md rule).
Verify with `ciu check`/`ciu render` that governance did not override the
authored `cgroup_parent` and did not inject a memory cap you did not want
(set `mem_limit` explicitly to a sane value, e.g. `1g`, and `memswap_limit`
accordingly — the daemon must not be OOM-killed mid-session). If ciu
refuses `privileged`/`pid`/`cgroupns` at render or at `up`, STOP that
sub-task, document the exact refusal in the REPORT, provide
`tools/daemon-run.sh` with the identical `docker run` line as the fallback,
and continue — the controller files CIU-107.
README section "Running the daemon" (`ciu up` from this dir; `docker exec
cgprofile-host-daemon cgprofile ctl version --json`; where sessions live;
how to render a report; retention) and ATTACH-GUIDE section "Profiling a
run-gate lane" (by container id + token; by session id).

**C8 — cmru registration.** `scripts/cgroup-profiler/cmru.toml` (`project.id
= "cgroup-profiler"`, prefix `cgprofile-v`, `artifacts = ["oci-image"]`
(+ `"wheel"` only if it is trivial — the CLI is a script, not a package,
so probably not), version strategy scm — the first release is cut by the
controller with `--set-version 1.0.0`), `run-tests` step = `./run-gate.py
r0-r1` then `./run-gate.py r3` (the r2 mutation lane is a PRE-MERGE lane
you run once and record; it is not the release gate — say so in a
comment), `build` = image build, `push` = image push; register
`[orchestration.project.cgroup-profiler]` in the root
`cmru.orchestration.toml` (and the two ordering lists). `cmru status
--project cgroup-profiler` must not crash (read-only; do NOT run `cmru
release`).

**C9 — backlog + docs housekeeping.** `nyxloom-trove/backlog/` entries
CP-1..3 already exist (P0); add CP-4.. for anything you defer, via the
`backlog` skill. Update `DESIGN.md` (new modules in §2 layout and §4
contracts, the daemon in §1's tier story), `README.md` verb table.

## 3. Tests and gates (100% line AND branch is the existing bar; keep it)

- `tools/gate.sh <worktree> coverage` (the r0-r1 lane) green: it runs the
  suite in `tester-unified:local` with coverage `fail_under = 100`
  line+branch over `lib` + `cgprofile`; every new module is in
  `pyproject.toml [tool.coverage.run] source`. Use `nice -n 19 ionice -c 3
  ./run-gate.py --worktree <worktree> r0-r1` from the project dir; read the
  verdict in a SEPARATE step from the log.
- `./run-gate.py r3` (canary) green.
- `./run-gate.py r2` (assay mutation of changed lines, 45 m budget) ONCE
  before your return, after everything else is green; record the verdict
  (`.assay/verdict-r2.json` outcome, survivors) in the REPORT. If it
  exceeds budget, raise the lane budget to 90m in `run-gate.toml` with a
  comment and re-run once; report either way.
- Contract fixture identity: copy `run-gate-project/nyxloom-trove/fixtures/
  rg55/` into `tests/fixtures/contract/` and add a test that the two trees
  are byte-identical (so drift is caught in BOTH projects).
- Red-first where a wrong implementation is expressible: the current
  `DamonSession` (shrinks `nr_kdamonds` on exit) IS the controlled wrong
  implementation for C3 — write the two-session test first, watch it fail.
- Mutation probes you run yourself before the r2 lane: at least 10
  hand-mutants on `summary.py` (swap a max for a min, off-by-one in
  nearest-rank, forget a `/1e6`) — every one must be caught by the
  frame-driven golden tests; record the list.

## 4. Live acceptance (yours; host rule §6 binds)

1. `python3 build-push.py --build` → `cgprofile:local` (no suite running
   concurrently).
2. `ciu up` from the project dir (or `tools/daemon-run.sh` if C7 blocked);
   `docker inspect cgprofile-host-daemon --format '{{.HostConfig.CgroupParent}} {{.HostConfig.Privileged}} {{.HostConfig.PidMode}} {{.HostConfig.CgroupnsMode}} {{.HostConfig.NetworkMode}}'`
   must print the interactive slice, true, host, host, none.
3. `docker exec cgprofile-host-daemon cgprofile ctl version --json` → damon
   available? Record the answer verbatim.
4. Ephemeral probe: `TOK=$(python3 -c 'import secrets;print(secrets.token_hex(16))')`;
   `docker run -d --name rg55-p1-probe --cgroup-parent=dev-background.slice --memory=256m -e RUN_GATE_PROFILE_SESSION=$TOK --entrypoint sh cmru-enroll-fixture:local -c 'python3 -c "b=bytearray(100*1024*1024); import time; time.sleep(12)"'`
   (that image exists locally and has python3); `ctl start --target
   containerid:$(docker inspect --format '{{.Id}}' rg55-p1-probe) --scope
   container --token $TOK --damon on --meta '{"lane":"probe","project":"p1",
   "worktree":"w","commit":null,"run_gate_revision":0,"kind":"command",
   "expected":null}'`; wait 10 s; `ctl status`; `ctl stop` → summary: peak
   ≥ 100 MiB, cpu.seconds > 0, damon status on with hot_bytes.peak > 0 (or
   `unavailable` with the reason — report which); `ctl report <s>` renders.
5. Shared probe: `docker run -d --name rg55-p1-shared --cgroup-parent=dev-background.slice --entrypoint sh cmru-enroll-fixture:local -c 'sleep 600'`;
   start a session with `--scope container-shared --token $TOK2`; then
   `docker exec -e RUN_GATE_PROFILE_SESSION=$TOK2 rg55-p1-shared python3 -c 'b=bytearray(80*1024*1024); import time; time.sleep(8)'`;
   stop → `targets_seen ≥ 1`, `peak_over_baseline_bytes ≥ 70 MiB`,
   `source: "sampled-max"`.
6. Overhead: during probe 4, `docker stats --no-stream cgprofile-host-daemon`
   three times and `/proc/loadavg` before/after; with DAMON on vs off
   (two runs). Table in the REPORT.
7. `finally`: `docker rm -f rg55-p1-probe rg55-p1-shared`; `ciu down` the
   daemon (main's `ciu up` after merge is the canonical instance).

## 5. Records

`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-LOG.md`
(one entry per commit: hash, what, gate result), `…-REPORT.md` (per
deliverable evidence; the golden reproduction; DAMON availability and
overhead table; ciu render mechanics and any refusal; the r2 verdict;
mutation-probe list; decision asks; anything deferred with its CP-n id),
`…-BRIEF-n.md` at each checkpoint. Also the LOG must state your
orientation call count and which read-list items were wrong or missing.

## 6. HOST LOAD (binding)

8 cores shared with a production game server; PSI (`/proc/pressure/*`) is
the signal, never `free`/`uptime` alone. pytest SERIAL only (never `-n`),
always `nice -n 19 ionice -c 3`; targeted test files while iterating, the
whole suite at most once per checkpoint and once before the return. Gate
containers: at most 2 across the estate — check `docker ps --format
'{{.Image}}'` for `tester-unified:local` before starting yours, run
`docker update --cpus=3 <id>` right after launch, remove in a `finally`.
No image build concurrent with a suite run. The daemon container is exempt
from the count but must be idle before an overhead measurement. No container
may use `--cgroupns=host`, `--pid=host`, or `--net=host`; the daemon and helper
use explicit read-only host proc/cgroup binds with private namespaces.

## 7. Rules

- Edit files with the Edit tool; never sed/python rewrite scripts.
- `git -C <worktree> add <new>`; `git -C <worktree> commit -F <msgfile>
  --only -- <paths>`; never `cd /workspaces/vbpub` before a git command;
  never a bare `git stash`. Trailers on every commit:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
  After adding a package: `git -C <worktree> ls-files scripts/cgroup-profiler/<dir> | wc -l`.
- Forbid: editing `run-gate-project/`, `ciu/src/`, anything under
  `/workspaces/dstdns`; any host-mutating write (caps, cgroup limits) from
  the daemon; `cmru release`; pushing; merging.
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate); write `…-BRIEF-n.md` + a
  `/compact` retention prompt, commit, return — the controller dispatches
  a fresh successor.
- Return message: commits (hash + subject), gate verdicts (each lane, exit
  code, coverage numbers), live-probe results (peak, damon status, overhead
  table), the ciu render outcome, decision asks, deferred items. Claim only
  what you ran — a fresh adversarial reviewer verifies.
