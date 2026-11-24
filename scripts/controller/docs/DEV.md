# Remote dev tool (`scripts/controller/dev`)

Compile, test, lint and run a project's code before the commit, in the capped, network-less container of that
project's run-gate environment, on a gate host. The contract is `docs/design/agent-dev/DESIGN.md`; this file is the
operator and implementer guide. **A dev run is NOT gate evidence**: only a registered lane on the exact committed
head is. Report "dev: ...", never a gate result.

```
scripts/controller/dev [--dry-run] -p <project> <verb> [args]
scripts/controller/dev [--dry-run|--this] gc
```

## Verbs and profiles

`-p` takes a key of [`dev-profiles.toml`](../dev-profiles.toml) (the project directory): `hostobs`,
`run-gate-project`, `cmru`, `libraries/cli-extended`, `fleetd`, `assay`, `tls-edge`, `ciu`, `scripts/controller`,
`scripts/netcup`. Every container property (memory, cpus, pids, time, user, ulimits, mounts, env, setup) lives in
that file, layered `[defaults]` < `[kinds.<kind>]` < profile. A profile's lane, environment and workdir are read
from the project's `run-gate.toml` at call time, and `tests/test_dev.py` checks that every `setup`, `env` and
`partial_args` word occurs verbatim there.

- Python: `test [-- ARGS]` (setup first; `partial_args`, e.g. cmru's `--cli-case-partial`, only when ARGS are given),
  `lint`, `run -- CMD...`, `shell`.
- Rust: `build`, `check`, `clippy`, `fmt`, `test [--no-nextest]`, `example CRATE NAME [ARGS]`, `gdb`, `strace`,
  `run -- CMD...`, `shell`.
- All: `status`, `stop`, `gc`.

Examples: `dev -p cmru test -- tests/test_x.py -k name`, `dev -p hostobs test -p hostobs-store`,
`dev --dry-run -p hostobs clippy` (prints the plan, runs nothing).

## What happens on a call

1. Preflight (one hx call): the host user is 1003:1003 (else exit 3), and the host's image id (for Rust also the
   `tester-unified-rust-dev` image and the base-image label it records) against the cockpit's. A missing or different
   image is exit 2 naming the tag, the ids and `scripts/controller/push-image.sh` (which needs the main controller's
   grant); the tool never pulls or builds.
2. Your worktree (committed head as a thin bundle, plus patch and untracked files up to 20 MiB) is synced to
   `~/agent-dev/<hex12>/work` on the host, under a per-worktree lock; a second call for the same worktree exits 3.
3. Admission (`host-admission/1`, see `scripts/netcup/runner/README.md`): one start at a time through a ticket; the
   call waits while the host's memory PSI `full` avg10 is >= 3, prints progress every 60 s, and exits 75 after
   `AGENT_DEV_WAIT_S` (900) with nothing run. Unreadable PSI is exit 75 at once.
4. The container starts (`--network none --cap-drop ALL --init`, private PID namespace, memory reservation with an
   8 GiB ceiling and unlimited swap) and its output and exit code stream back.

First and last stderr line: `AGENT-DEV <proj> <host> <sha12>[+dirty]: NOT GATE EVIDENCE`.

## Exit codes

The command's own code propagates. The tool's own are printed as `dev: exit N` before the closing banner:
2 usage, refusal or image problem; 3 transport, identity (wrong host user) or the same worktree busy; 75 not
admitted (host memory PSI too high or unreadable; retry once, then report); 124 time limit (the in-container or host
runtime limit; the container is removed).

## Environment

`AGENT_DEV_HOST` (`v1001`; `r1002` only when listed in `CONTROLLER_REMOTE_GATE_HOSTS`), `AGENT_DEV_MEMORY`
(soft reservation 512m..8g), `AGENT_DEV_CPUS` (0..3), `AGENT_DEV_PIDS` (<= 1024), `AGENT_DEV_MAX_S` (<= 14400),
`AGENT_DEV_WAIT_S` (0..86400, default 900). Bounds are enforced by the tool and cannot be lifted by a profile. There is no local mode (`--local` exits 2: the cockpit daemon is the production daemon) and
no image override. `HOSTOBS_DEV_{HOST,MEMORY,CPUS,PIDS,MAX_S}` are mapped by the `hostobs/tools/dev` shim; any other
`HOSTOBS_DEV_*` exits 2. Test seams (never set them by hand): `AGENT_DEV_HX`, `AGENT_DEV_TEST_{PSI_DIR,CLOCK,PROFILES}`.

## Housekeeping

`dev gc` (from the cockpit) removes the state of worktrees that are gone: volumes `agent-dev-cache-*-<hex>`
(and legacy `hostobs-dev-*`) and `~/agent-dev/<hex>`, when the hex is absent from `git worktree list` and unused
for 3 days, or unused for 30 days. It skips held locks and live containers and never touches `~/checkouts`.
`dev gc --dry-run` lists; `dev gc --this` cleans this worktree only (what `hostobs/tools/dev clean` maps to).
Code that signals pids (kill, pkill, hand mutants) belongs in `dev run`, never in the devcontainer.
