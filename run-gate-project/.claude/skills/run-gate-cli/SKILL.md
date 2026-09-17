---
name: run-gate-cli
description: The run-gate CLI itself — lane discovery, --worktree/--base/--dry-run/--fresh flags, doctor/history/footprint subcommands, exit codes, environment contract. Use for run-gate mechanics that aren't specific to the nyxloom pipeline (that's nyxloom-carve/nyxloom-merge-p's job) — e.g. diagnosing a gate failure, checking lane history/resource footprint, or pre-flighting an environment.
---

> **Tool versions as of last verified update (2026-09-17):** run-gate
> `23.8.0` (`pip show run-gate`), rev 42 banner. Re-verify against
> `run-gate --help` (top-level usage + full lane table) and `run-gate gate
> --help`-equivalent (flags/liveness/history/environment sections) if
> anything below drifts — this build's help text is comprehensive; this
> skill distills it so an agent doesn't need the round-trip in the common
> case.

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
(and any shared repo-root `run-gate.toml`) — never hardcode a lane name from
memory without checking it still exists there (lane sets change).

## Run a lane

```bash
run-gate <lane>                                  # invoking checkout, judged in place
run-gate <lane> --worktree /path/to/.worktrees/x  # judge — and execute lanes IN — a different tree
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
```

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
```
The resource cost each lane's history has actually MEASURED — peak memory,
+baseline, hot-set p90, CPU cores, memory-full stall, duration — distilled
from PASS + history-eligible runs. Without `--write`, only prints the
numbers; `--write` persists them to `run-gate.footprint.json` (tracked,
committed) and REFUSES (exit 2) if no lane has a completed profiled run yet.
A `LANE` filter is refused together with `--write` (a partial write would
silently drop every other lane's data). Use this to answer "how much memory
does this lane actually need" before capping a container, instead of
guessing or reading a stale hand-written estimate.

## Exit codes

The lane's own status passes through unchanged. Reserved: `2` =
configuration/refusal (bad key, unknown lane, dirty tree, preflight
failure), `3` = execution infrastructure (docker/git/mountinfo couldn't do
its job). A red gate is one of these three, never ambiguous — read the exit
code before reading the log.

## Liveness, never a guessed total (RG-36/RG-41)

An assay lane's liveness comes from its progress file
(`.assay/progress-<lane>.jsonl`, polled every 30s while the container runs);
a command lane's liveness is its own stdout stream. `stall_timeout` (a
`budget`-grammar lane key) fires ONLY on silence that long while the
container is still running — never on total elapsed time. `budget` itself
stays advisory throughout; don't read a budget-exceeded disclosure as a
hard failure unless the lane also hit `stall_timeout`.

## Environment contract (fail-fast, no silent defaults)

`CGROUP_PARENT_DEV_BACKGROUND` (required for container lanes' cgroup slice),
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
