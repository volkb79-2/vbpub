# Agent dev container: decision note (P-plat-01)

Implements PLAN-platform B1-B3 and B9. Mechanics are those of `hostobs/tools/dev` (see `tester-unified-rust/docs/DEV.md`). M1 = v1001, 2026-11-24.

## 1. Home (a)
- `scripts/controller/dev` is bash: at most ~250 lines on top of the moved tools/dev code.
- `dev_profile.py` (stdlib tomllib) resolves a profile to NUL-separated argv.
- `hostobs/tools/dev` becomes an exec shim (≤20 lines; `HOSTOBS_DEV_*` maps to `AGENT_DEV_*`, `clean` to `gc --this`).
- P-fd-05 ports it to the fleetd host agent; the CLI, profiles and `host-slots/1` stay.

## 2. Profiles (b)
`scripts/controller/dev-profiles.toml`, keyed by project dir. Fields: `kind` (python/rust), `lane`, `environment` (root or project run-gate.toml; owns the image), `dev_image` (Rust), `workdir`, `setup` (argv list), `env`, `cache` (0-1 path), `memory`, `verbs` (argv templates; a user word stays one argument).

Verbs. Python: `test -- <args>` (setup, then `-m pytest <args>`), `lint` (default compileall + shellcheck), `run -- CMD`, `shell`. Rust: the tools/dev verbs (build, check, clippy, fmt, test/nextest, example, gdb, strace, run, shell). All: status, stop, gc.

Main lanes: hostobs and run-gate-project `selftest`; cmru `assay`; cli-extended and fleetd `r0-r1`; assay `tooling-tests`; tls-edge `tests`; ciu `ciu`; scripts/controller and scripts/netcup `suite`; agctl its first lane (P-plat-09).

Python setup:
- **Lanes with `kind="assay"`:** `env` and the interpreter are read from the assay.toml lane at call time. For cmru that is `PYTHONPATH=src:../libraries/worktree/src`, `PYTHONDONTWRITEBYTECODE=1` and `/opt/tester-venv/bin/python` (`$TESTER_VENV`). The profile may only add keys.
- **Shell-text setup is declared.** For cmru it is survivor-regressions' `pip install … --editable ../assay`.
- **`partial_args`** are added when `test` gets selectors. For cmru this is `--cli-case-partial`; without it its conftest exits 4.

Agreement test (`tests/test_dev.py`): one profile per project; the lane exists; environment and workdir equal the lane's; every `setup`/`env`/`partial_args` word occurs verbatim in that project's run-gate.toml argv; Rust uses `tester-unified-rust` with a `dev_image`.

## 3. Names (c)
- `<hex12>`: the first 12 hex digits of sha256 of the worktree's physical toplevel path (as tools/dev).
- `<proj>`: the dir with `/` turned into `-` and `libraries-` dropped.
- Container `agent-dev-<proj>-<hex12>`; volume `agent-dev-cache-<proj>-<hex12>`; dir `~/agent-dev/<hex12>/` (`work`, `lock`, `cid`, `up-*`, `last-use`).
- One checkout per worktree, serialised by `lock`.
- Legacy `hostobs-dev-*` state is removed once, by exact name.

## 4. Hosts (d)
- v1001 by default. r1002 needs `AGENT_DEV_HOST=r1002` and r1002 in `CONTROLLER_REMOTE_GATE_HOSTS` (the controller sets it per lease; no lease registry exists). Others: exit 2.
- **No local mode:** `--local` exits 2 even with `AGENT_DEV_ALLOW_LOCAL=1`. The cockpit's docker is the production host's daemon. The devcontainer is cockpit-only: 8 GiB cap, and PID 1 dies when its `sleep` is killed.

## 5. Tree shipping
- As tools/dev: thin bundle (against the host's last head or nearest `~/checkouts` seed), binary patch plus untracked tar (≤20 MiB, else exit 2), reset hardened checkout, `.git` read-only.
- The tool only reads `~/checkouts`; the sub-controller keeps a fresh main seed there (M1: 7.6 s bundle on a stale seed).
- Batch: 2 hx calls (preflight, upload+run); tty: 3. About 2.5 s each; B4 caches identity.

## 6. Images (k, l)
- **(k)** Preflight requires the host's image id (Rust: `dev_image`, whose label `…rust-dev.base-image-id` must also equal the environment image's host id) to equal the cockpit's; if the cockpit lacks the tag, the host id is printed. Missing or different: exit **2** before admission (a retry cannot fix it), naming tag, ids and `push-image.sh`. Never pulls or builds.
- **(l)** A push is an image change: the main controller's explicit grant FIRST, even within a measurement lease. Then the controller or sub-controller runs `scripts/controller/push-image.sh <host> <tag>` after a rebuild merges and before first use, pushing the id the environment resolves to in the cockpit. r1002 included (no tester-unified image yet). No silent prune: removal by exact id, with a grant.

## 7. Admission (e): `host-slots/1`
P-plat-02 writes this contract once, in `scripts/netcup/runner/README.md`. run-job, remote-gate.py and `fleet host start` adopt it in P-fd-05.
- **Files:** `~/host-slots/` holds `ticket`, `slot.1..N` and an informational `slot.N.who`.
- **Budget:** min(MemoryHigh, MemoryMax) of `dev-gates.slice` (its `/etc` drop-ins), ignoring `infinity`: v1001 6656 MiB, r1002 4096 MiB.
- **Slots:** 512 MiB each, N = ⌊budget/512⌋. Weight = ⌈memory cap/512⌉, clamped to 1..N.
- **Admission:** take the `ticket` flock; every 10 s, wait while memory-some or io-full PSI avg10 > 25 (CPU PSI never refuses) or MemAvailable − weight < 1024 MiB; take free slots non-blocking, keeping partial holdings (FIFO); hold the ticket 20 s past container start.
- **Exit 75:** at once if PSI, meminfo or budget is unreadable; else after `AGENT_DEV_WAIT_S` (900 s; progress every 60 s).
- **ssh drop:** the kernel frees a flock when its last holder, the host-side wrapper, exits. Its EXIT/HUP/PIPE/TERM trap removes the container (by cid) before the slot fds close, so a free slot means no container. On a drop the slot stays held while the container runs (an EPIPE'd client triggers the trap). SIGKILL is bounded by the in-container `timeout` and `dev stop`.
- **Transition:** v1 keeps tools/dev's weight-1 controller-side slot until P-fd-05, so dev and remote-gate.sh lanes queue together.
- **Same worktree:** a second call exits 3 (non-blocking `lock`).

## 8. Container (f, i)
- **Flags:** `--rm --init --pull never --network none --cap-drop ALL --security-opt no-new-privileges --user 1003:1003 --ulimit core=0:0 --pids-limit 1024 --cpus 2 --cgroup-parent dev-gates.slice`, `--memory` = `--memory-swap`, under `nice 10 ionice -c3 timeout 3600`.
- **Memory:** Rust 3g (weight 6, so 2 fit on v1001). Python 1g (weight 2; M1 cmru peak was 66 MB).
- **Bounds:** cpus in (0,3], memory 512m-4g, pids ≤1024, time ≤14400 s. Anything else exits 2.
- **PID namespace:** private (never `--pid=host`), tini as PID 1: kill/pkill reach only the container, never the cockpit's `sleep`. So pid-signalling tests and hand mutants run here, with no capability.
- **SYS_PTRACE:** for `gdb`/`strace` only. Under uid 1003 and no-new-privileges it only widens Docker's seccomp allowlist (`process_vm_readv`, `kcmp`).

## 9. Output, exit, gc (g, h)
- **Banner:** `AGENT-DEV <proj> <host> <sha12>[+dirty]: NOT GATE EVIDENCE`, first and last stderr line; `+dirty` = non-empty patch or tar.
- **Exit codes:** the command's propagates; the tool's own are 2 usage/refusal/image, 3 transport/identity/busy, 75 not admitted, 124 time limit.
- **`dev gc [--dry-run|--this]`:** a hex is stale if absent from `git worktree list` with `last-use` > 3 days, or `last-use` > 30 days. Removes `agent-dev-cache-*-<hex>` (exact name) and `~/agent-dev/<hex>`; skips held locks and live containers; never touches `~/checkouts`.

## 10. CTL-50 brief text (j)
```
Dev runs (CTL-50):
- Before every commit run `scripts/controller/dev -p <proj> <verb> ...` (e.g. `test -- -q tests/test_x.py`, `clippy`, `fmt --check`), one call at a time, never parallel or backgrounded.
- Exit 75 = host busy, nothing ran: retry once; if 75 again, stop and report it.
- A dev run is NOT evidence; only registered lanes on the exact committed head are. Report "dev: ...", never a gate result.
- Never call hx.sh, ssh, docker or remote-gate directly; never run pytest, cargo or test scripts in the devcontainer.
- Code that signals pids (kill, pkill, process groups, hand mutants) runs only in the dev container.
- Until scripts/controller/dev exists in your worktree: hostobs/tools/dev for Rust; for Python report "no dev run".
```
