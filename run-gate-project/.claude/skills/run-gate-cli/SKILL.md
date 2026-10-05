---
name: run-gate-cli
description: The run-gate CLI itself — lane discovery, worktree scope, request bases, native sequences, daemon admission, selective Assay requests, evidence, footprint controls, closed exit codes, and environment contract. Use for run-gate mechanics that aren't specific to the nyxloom pipeline (that's nyxloom-carve/nyxloom-merge-p's job).
---

> **Source version:** this documentation describes run-gate rev 49, which is
> unreleased. An installed wheel or copied launcher may be older; check
> `run-gate --version` and `run-gate --help` before relying on a newer option.

> **MANDATE.** `run-gate <lane>` is the ONLY way to execute a project's test
> suite for gating purposes. Never invoke `pytest`/`npm test`/a raw
> `docker exec` directly against a project's cockpit/devcontainer venv and
> call it a gate result — "green in the devcontainer" carries the base
> image's pins, not the app's (see the consuming repo's own "cockpit vs.
> gating test runner" doctrine). Never hand-derive a changed-line comparison
> base with `git merge-base` when a lane declares its own base derivation —
> passing `--base` to such a lane is refused by name.

# run-gate CLI

## Discover lanes

```bash
run-gate --list                    # machine-readable: name<TAB>kind<TAB>environment
run-gate <lane-name-from-list> --help   # not a real per-lane help; use --list + run-gate.toml instead
```
There is no per-lane `--help`; lane definitions live in `run-gate.toml`
(and any shared repo-root `run-gate.root.toml`) — never hardcode a lane name from
memory without checking it still exists there (lane sets change).

## Run a lane

```bash
run-gate <lane>                                  # invoking checkout, judged in place
run-gate <lane> --worktree /path/to/.worktrees/x  # config, judgment and execution all come from that tree
run-gate <lane> --allow-dirty                     # bypass run-gate's OWN clean-tree refusal only —
                                                   # assay lanes still enforce assay's own clean-tree
                                                   # rule afterwards; two independent layers
run-gate <lane> --base <ref>                      # only for a lane that DELEGATES its base
                                                   # (an assay lane with base_source="request", or a
                                                   # conjunction lane with a {base} token) — refused
                                                   # by name on a lane that derives its own base
run-gate <lane> --dry-run                         # print full execution plan (docker argv, mounts,
                                                   # slice, inner command), exit 0, start NOTHING judged
run-gate <lane> --check-env                       # advisory env-reference drift sweep + assay-lane
                                                   # toolchain fitness check (FAIL there exits 2)
run-gate <lane> --fresh                           # RG-35: kill+restart a container lane whose previous
                                                   # client died and left the container running, instead
                                                   # of re-attaching to it. Refused on host/exec lanes.
run-gate <command-lane> -- path/to/file            # accepts_args = true appends argv after --
run-gate <assay-lane> --reuse-from verdict.json    # assay-only selective R2 reuse
run-gate <assay-lane> --rejudge ID --rejudge ID    # repeatable, assay-only selection
run-gate <assay-lane> --rejudge-outcome FAIL       # assay-only
```

`--worktree` selects the lane config beside that worktree's project path
relative to its Git root. Missing config is an error; the invoking checkout's
lane table is never substituted. The run header prints `config: <path>` and
history stores its path and SHA-256.

Every assay lane is invoked with `--resume --progress
.assay/progress-<assay_lane>.jsonl --state-dir <checkout>/.run-gate/assay-state/<project-path>`.
The first two options require Assay 2.4.1; durable `--state-dir` requires
Assay 5.2.0. Keep `.run-gate/assay-state/` across retries so mutation resume
data survives deletion of an ephemeral worktree. Before Assay starts,
run-gate checks that the state root exists and is writable as the lane user;
a missing mount is NOT_RUN/`state-mount`. Container environments can declare
`state_root` when the durable mount has another in-container path. Use
`run-gate doctor` to check it per assay environment. The consumer contract is documented in
[`CONSUMERS.md`](../../../CONSUMERS.md#resume-progress-and-durable-assay-state).

## Pre-flight, history, resource footprint (RG-9/RG-27/RG-55) — the known-gap subcommands

These three verbs are the ones most likely to be missed because they're not
part of the "run a lane" flow — reach for them explicitly:

```bash
run-gate doctor [--worktree PATH]
```
Checks docker, cgroup slices, mountinfo, git, images, the linked-worktree
host-lane git view, unprefixed relative script paths in container-command
lanes, AND each assay lane's toolchain fitness (asked of the judge itself —
this starts short read-only probe containers, one per environment+judge).
Judges nothing, writes nothing. **Run this BEFORE debugging a mysterious
gate failure** — most "why is this lane broken" investigations are actually
an environment/toolchain gap doctor would have named directly.

```bash
run-gate history [LANE] [--worktree PATH] [--json]
```
What each lane most recently did — ANY outcome, dirty or aborted runs
included — plus its bounded per-commit duration series. Reads the store
(`<project>/.run-gate/history.json` in the judged worktree), runs no lane,
decides no policy. Use this instead of re-running a lane just to see its
last result, or to distinguish "this lane is flaky" from "this lane has
never passed."

```bash
run-gate footprint [LANE] [--worktree PATH] [--json] [--write]
run-gate footprint mutation --include-failed
run-gate footprint --write --lane mutation --lane schema --include-failed
```
The resource cost each lane's history has actually MEASURED — peak memory,
+baseline, hot-set p90, CPU cores, memory-full stall, duration — distilled
from PASS + history-eligible runs. `--include-failed` adds completed profiled
FAIL runs; aborted, timed-out, dirty and unprofiled runs remain excluded.
Without `--write`, only prints the numbers. `--write` persists to the tracked
`run-gate.footprint.json`; repeatable `--lane NAME` merges only those entries
and requires an existing schema-1 manifest. Without `--lane`, it writes the
full selection.

## Exit codes

The gate uses a closed result table: PASS = 0, FAIL = 1, ERROR = 2,
NOT_RUN = 3, BUDGET_EXCEEDED = 4. The raw command or Assay status is retained
in history and JSON; it never becomes the gate process status by passthrough.
Configuration errors are ERROR, while `no-headroom`, a missing base, and an
environment that cannot run the lane are NOT_RUN.

## Native sequences and imported Assay lanes

A `kind = "sequence"` declares ordered member lanes and can stop on the first
non-PASS result. `[project].trunk` derives `HEAD^1` for a merge at the trunk
tip; run-gate resolves the base once and passes it only to members that
request one. External Assay consumers can declare
`import = { environment = "test-runner", lanes = "all" }` under `[assay]`
to import exact names from `assay lanes --json`. See the consumer examples
for [sequences](../../../CONSUMERS.md#native-sequences-and-trunk-bases) and
[Assay imports](../../../CONSUMERS.md#shared-assay-lane-imports).

## Daemon-wide count admission

Admission is disabled by default. With a project-local `[admission]
enabled = true` and an explicit, already-local `ticket_image`, publish a
daemon-wide limit once with:

```bash
run-gate admission set --max-concurrent 2 --unreadable-policy refuse
run-gate admission show
run-gate schema --admission-wait 10m
run-gate schema --override-admission
```

Set/show and enabled runs require a local Docker Unix endpoint. Docker-name
tickets order gates across worktrees; `--admission-wait` defaults to ten
minutes, then yields NOT_RUN/`no-headroom`. `--override-admission` is recorded
on the ticket and result. The image is never pulled implicitly. See the
[operator guide](../../../CONSUMERS.md#daemon-wide-gate-admission).

## Liveness, never a guessed total (RG-36/RG-41)

An assay lane's liveness comes from its progress file
(`.assay/progress-<lane>.jsonl`, polled every 30s while the container runs);
a command lane's liveness is its own stdout stream. `stall_timeout` (a
`budget`-grammar lane key) fires ONLY on silence that long while the
container is still running — never on total elapsed time. `budget` is the
hard elapsed-time bound. It starts after shared and exec runner locks are
acquired and, when enabled, after daemon admission, so queue time does not
consume the lane's mutation budget.

## Environment contract (fail-fast, no silent defaults)

`CGROUP_PARENT_DEV_GATES` (required for container lanes' cgroup slice),
`RUN_GATE_EXTRA_MOUNTS`, `RUN_GATE_MOUNT_ALIAS`, `RUN_GATE_EVIDENCE_DIR`,
`RUN_GATE_CGROUPFS_ROOT`, `RUN_GATE_PROC_ROOT`, `RUN_GATE_LOCK_DIR`,
`RUN_GATE_PROFILE`. Every one of these DERIVEs, READs, or FAILs — never a
silently-wrong default; if a lane fails complaining about one of these, that
is a real environment gap, not a bug to route around with a hardcoded value.

## Where SSOT actually lives

`run-gate.toml` is the SSOT for lane/test definitions. A consuming project's
own pipeline config (e.g. nyxloom's `[gates.*]` entries) should be a POINTER
only — a one-liner naming a single lane — never a place to add suite argv,
sequencing, or coverage flags. If multiple lanes must run together, declare
the conjunction IN `run-gate.toml` and point the consumer at that one lane.
`run-gate validate-pointers CONSUMER.toml [--root DIR]` certifies every
pointer names a real lane.
