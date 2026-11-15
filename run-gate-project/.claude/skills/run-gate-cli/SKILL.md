---
name: run-gate-cli
description: The run-gate CLI itself — lane discovery, judging a worktree, comparison bases, native-R2 worker selection and campaigns, KSM and network policy, daemon admission, selective Assay requests, history/footprint/doctor, closed exit codes, environment contract. Use for run-gate mechanics that aren't specific to the nyxloom pipeline (that's nyxloom-carve/nyxloom-merge-p's job).
---

> **Source version: run-gate 25.6.0** (the installed wheel; check
> `run-gate --version`, and `run-gate --help` from inside the project directory —
> run-gate refuses to print usage outside a project with a `run-gate.toml`).
> The source module's `__revision__` is 68 at this release (record provenance
> only; the wheel SemVer is the compatibility identity). A copied launcher or an
> older wheel may lack a flag below — the help screen and the lane table are the
> authority.

> **MANDATE.** `run-gate <lane>` is the ONLY way to execute a project's test
> suite for gating purposes. Never invoke `pytest`/`npm test`/a raw
> `docker exec` directly against a project's cockpit/devcontainer venv and
> call it a gate result — "green in the devcontainer" carries the base
> image's pins, not the app's. Never hand-derive a changed-line comparison
> base with `git merge-base` when a lane declares its own base derivation —
> passing `--base` to such a lane is refused by name.

# run-gate CLI

## Discover lanes

```bash
run-gate --list                     # name<TAB>kind<TAB>environment (three columns, stable)
run-gate --list --json              # R-55: kind, environments, members, derived rigor
run-gate --help                     # version + lane table (clean_tree, budget, memory, resources) + flags
```
There is no per-lane `--help`. Lane definitions live in the project's
`run-gate.toml`, plus any lanes inherited from the nearest enclosing
`run-gate.root.toml`; never hardcode a lane name from memory — lane sets change.

## Run a lane

```bash
run-gate <lane>                                  # invoking checkout, judged in place
run-gate <lane> --worktree /path/to/.worktrees/x # config, judgment and execution all come from that tree
run-gate <lane> --allow-dirty                     # lifts run-gate's OWN clean-tree refusal only
run-gate <lane> --base <ref>                      # only a lane that DELEGATES its base
run-gate <lane> --dry-run                         # print the full plan (docker argv, mounts, slice, inner cmd)
run-gate <lane> --check-env                       # env-reference drift sweep (advisory) + assay toolchain fitness (FAIL=exit 2)
run-gate <lane> --fresh                           # RG-35: drop a previous client's container and run anew
run-gate <lane> --ksm | --no-ksm                  # per-run KSM override (see below)
run-gate <command-lane> -- path/to/file            # accepts_args = true appends argv after --
run-gate <command-lane> --workers N|auto          # RG-146 xdist workers (lane must declare workers + {workers})
```

## Judging a worktree (the one thing an agent gets wrong)

`--worktree PATH` overrides the judged worktree AND the source of every config
read: run-gate preserves the invoking project's path relative to its Git
toplevel and loads `<worktree>/<project-relative>/run-gate.toml` plus its nearest
ancestor `run-gate.root.toml`. A missing selected config is a refusal naming the
path — the invoking checkout's lanes are never substituted. This applies to
`--help`, `--list`, `doctor`, `history`, `footprint`, `status` and `--check-env`
too, and each names the tree it answered for, so a query can never report
another tree's data under this tree's name.

Two consequences worth stating:

- Everything (history, inflight records, `.run-gate/`, the Assay store) is
  per judged worktree, so two worktrees' gates never contend, and a worktree's
  history is not the main checkout's.
- **Container lanes are refused on the plain main checkout** by default
  (`RUN_GATE_ALLOW_MAIN_CHECKOUT=1` or `--allow-main-checkout` permits it):
  every git-ignored file would be visible inside the container. Exec-mode lanes
  are never affected.

**Comparison base.** `--base` reaches an assay lane whose inventory reports
`base_source = "request"` (as `--request-base`) and a conjunction lane carrying
a `{base}` token; a lane that does not delegate refuses it by name. Omitted, the
base comes from: the `base_ref`/`fork_point_sha` recorded in the worktree's
`ciu.worktree-instance.json` (used only when the branch still matches, the ref
still resolves in that tree to a LOCAL branch, the merge-base still EQUALS the
recorded fork point, the base has not absorbed HEAD, and there is a real diff),
else the tree's own merge-base with its upstream. A record that cannot be used
is reported (`run-gate: ignoring <record>: <why>`) rather than silently rotting.
Never hand-pick a base as a "fix"; a guessed base is a false green.

**Through a ciu worktree:**

```bash
cd <repo>/.worktrees/<name> && eval "$(ciu env print)"
flock "/tmp/${REPO_NAME}-${INSTANCE_ID}-testrunner.lock" \
  run-gate <lane> --worktree "$PWD"        # lane still runs in tester-unified, never on the cockpit
```
`flock` is a cheap outer lock only; run-gate serializes two consumers of the
SAME container itself (RG-39) and refuses to hijack a live owner's container.
See the **ciu-stack** skill for creation, capping and teardown.

## Native R2 worker selection and flexible campaigns

```bash
run-gate <assay-r2-lane> --jobs N|auto        # R-53; lane may declare jobs = N|"auto"
run-gate <assay-r2-lane> --shard 1/3          # RG-265, assay >= 12.2.0
run-gate <assay-r2-lane> --merge-shards 3     # STRICT: every shard store must exist
run-gate <assay-r2-lane> --merge-state DIR    # repeatable: copy same-judge records from another store
run-gate <assay-r2-lane> --carry-over DIR --carry-over-reason TEXT
run-gate <assay-r2-lane> --only-candidates FILE [--exclude-candidates FILE]
run-gate <assay-r2-lane> --rejudge ID --rejudge-outcome FAIL|hung|budget_exceeded...
run-gate <assay-r2-lane> --rejudge-contaminated   # serially rejudge the last run's contaminated budget_exceeded set
```

`--jobs auto` selects
`max(1, min(jobs_max, floor(cpu_cap × r2_oversubscribe), floor(memory_budget / candidate_rss_p95), candidates_remaining))`
and prints which candidate-RAM source it used (declared, measured p95, or the
1 GiB default) plus the selected count; memory is never oversubscribed.
`r2_oversubscribe` (`[resources]`, decimal string `"1.0"`..`"4.0"`, default
`"1.0"`, `"1.5"` recommended for I/O-heavy suites) is the only way to place more
workers than enforced CPUs. Parallel selection also requires a cheap-clone
(Cowtree btrfs) workspace; without a confirmable one, auto clamps to 1 and an
explicit count above one refuses. `--jobs` is forwarded to Assay only when the
inventory's version is at or above 11.0.0 (floor for the flex flags: 12.2.0).

A shard runs as its own instance `<lane>.shard<K>of<N>`: its own in-flight
record, container name, history entry, `.assay/verdict-...shard<K>of<N>.json`
and `.run-gate/assay-shards/.../<K>of<N>` store (Assay locks one store per run,
so concurrent shards cannot share one). Every selective run is recorded with an
`r2_flex` block and a PARTIAL claim, so it never stands in for a plain lane
result; every flex path must be under the checkout or the judged worktree,
because the lane mounts only those — copy a foreign store under
`<checkout>/.run-gate/` first. `--merge-shards` refuses when any shard store is
missing rather than merging a partial set.

`--shard K/N` runs instance `<lane>.shard<K>of<N>` with its own inflight record,
container name, history key, artifacts and store; it refuses an exec-mode or
`resources.shared` lane and an ephemeral image absent from the host. Every flex
run is recorded `selective` and NEVER counts as a plain lane result — `PARTIAL:`
means some candidates were never started.

`--workers`/`--scaling`/`--xdist-parity` are the command-lane xdist controls
(RG-146/RG-147) and are distinct from `--jobs`: a command lane needs
`workers = N|"auto"`, `workers_max`, `xdist_safe = true` and the `{workers}`
token. A parallel run that exits 1 is re-run once at one worker; if that passes
the result is `FAIL`/`XDIST_ISOLATION` (printed `INCONCLUSIVE`), excluded from
history.

## Pre-flight, history, resource footprint, occupancy

```bash
run-gate doctor [--worktree PATH]      # docker, slices, mountinfo, git, images, host-lane git view
run-gate history [LANE] [--worktree PATH] [--json]
run-gate footprint [LANE] [--worktree PATH] [--json] [--write] [--include-failed]
run-gate status [--worktree PATH] [--json]
```
`doctor` judges nothing and writes nothing but DOES start short read-only probe
containers (one inventory probe per environment+judge, one batched `command -v`
probe, one Assay state-root probe). Run it before debugging a mysterious failure.
`history` reports what each lane last did — ANY outcome, dirty and aborted runs
included — from `<project>/.run-gate/history.json` in the JUDGED tree, and the
store must be git-ignored (run-gate refuses to dirty the tree). `footprint`
reports measured cost (peak memory, +baseline, hot-set p90, CPUs, memory-full
stall, duration) from PASS + history-eligible runs; `--include-failed` adds
completed profiled FAILs; `--write` persists `run-gate.footprint.json` (tracked)
and refuses when no lane has a completed profiled run. `status` reads this tree's
inflight records, host-wide exec-runner locks and the admission tickets.

## Native sequences, imports, daemon admission

```bash
run-gate <sequence-lane>                       # kind = "sequence": ordered members, stop on first non-PASS
run-gate validate-pointers CONSUMER.toml [--root DIR]   # RG-2: certify every pointer names a real lane
run-gate admission set --max-concurrent 2 [--replace] [--unreadable-policy refuse]
run-gate admission show
run-gate <lane> --admission-wait 10m --override-admission
```
A sequence derives `HEAD^1` for a merge at the `[project].trunk` tip and passes a
resolved base only to members that request one. `import = { environment = ...,
lanes = "all" }` under `[assay]` imports exact names from `assay lanes --json`.
Admission is disabled by default, requires the project's `[admission] enabled`
and an already-local `ticket_image`, and needs a local Docker Unix endpoint; the
wait default is ten minutes, then NOT_RUN/`no-headroom`. The image is never
pulled implicitly.

## KSM, network and io_sync (25.5/25.6)

- **KSM (RG-273).** An ephemeral lane container opts in to the tester-base
  image's advertised CIU KSM shim **by default**. Opt out with a per-run
  `--no-ksm`, `RUN_GATE_KSM=0`, or lane `ksm = false`; `RUN_GATE_KSM=1` forces it
  on. Other values refuse at load. An image without the label (for example an
  ARM64 image) continues with one notice; other inspect failures stay
  infrastructure errors. Per AGENTS.md, KSM sharing is not lane overcommit
  capacity — do not count it.
- **Network policy (RG-256).** Closed `network = "none" | "default"` at
  environment and lane scope, lane precedence; `"none"` adds `--network none` to
  disposable lane containers. An explicit network setting on a non-ephemeral mode
  refuses.
- **io_sync.** `strict` (built-in default), `relaxed`, and lane-only `durable`.
  Both `tester-unified` and cmru's `cmru-mutation` environment set `relaxed`;
  `--io-sync strict|relaxed` overrides per invocation, and relaxed runs are
  excluded from history. Durable lanes keep their own policy.

## Liveness, never a guessed total (RG-36/RG-41)

An assay lane's liveness comes from its progress file
(`.assay/progress-<assay_lane>.jsonl`, polled every 30 s) and a command lane's
from its own `docker logs -f --timestamps` stream; the run discloses which source
it used. `stall_timeout` (a `budget`-grammar lane key) fires ONLY on that many
seconds of silence while the container is still running — never on total elapsed
time — and is legal on both kinds. `budget` is the hard elapsed-time bound and
starts after locks (and admission), so queue time is not charged to it. Every
assay lane is invoked with `--resume --progress .assay/progress-<assay_lane>.jsonl
--state-dir <checkout>/.run-gate/assay-state/<project-path>`; keep
`.run-gate/assay-state/` across retries so mutation resume survives an ephemeral
worktree. Before Assay starts, run-gate checks that the state root exists and is
writable as the lane user; a container environment may declare `state_root` when
the durable mount has another in-container path. The `--resume`/`--progress`
flags floor the judge at Assay 2.4.1, durable `--state-dir` at Assay 5.2.0.

## Exit codes

Closed table: PASS = 0, FAIL = 1, ERROR = 2, NOT_RUN = 3, BUDGET_EXCEEDED = 4.
The lane's raw command/Assay status is retained in history and JSON but never
becomes the process status by passthrough. Configuration and
execution-infrastructure failures are ERROR; `no-headroom`, a missing base and an
environment that cannot run the lane are NOT_RUN; only a declared hard budget
yields BUDGET_EXCEEDED. A selective/partial Assay run legitimately exits 4.

## Environment contract (fail-fast, no silent defaults)

`CGROUP_PARENT_DEV_GATES` (required for container lanes' cgroup slice, after the
lane's own `cgroup_slice`/`cgroup_slice_env`), `RUN_GATE_EXTRA_MOUNTS`,
`RUN_GATE_MOUNT_ALIAS`, `RUN_GATE_KSM`, `RUN_GATE_ALLOW_MAIN_CHECKOUT`,
`RUN_GATE_EVIDENCE_DIR`, `RUN_GATE_CGROUPFS_ROOT`, `RUN_GATE_PROC_ROOT`,
`RUN_GATE_LOCK_DIR`, `RUN_GATE_PROFILE`, and `CGPROFILE_CONTROL_SOCKET` /
`CGPROFILE_OBSERVER_SOCKET` when profiling or an observer lane is enabled
(`RUN_GATE_PROFILE=off` opts out of profiling but not of the observer preflight).
Every one DERIVEs, READs, or FAILs — never a silently-wrong default. A lane that
complains about one has a real environment gap; do not route around it with a
hardcoded value.

## Where SSOT actually lives

`run-gate.toml` is the SSOT for lane/test definitions; judgment policy belongs to
assay (`assay.toml`). A consumer's own pipeline config is a POINTER naming one
lane — never a place to add suite argv, sequencing or coverage flags. If several
lanes must run together, declare the conjunction in `run-gate.toml` and point the
consumer at that single lane (`validate-pointers` certifies it).
