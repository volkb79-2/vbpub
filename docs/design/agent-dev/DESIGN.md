# Agent dev container: decision note (P-plat-01)

PLAN-platform B1-B3, B9, on the `hostobs/tools/dev` mechanics. M1 = v1001, 2026-11-24.

## 1. Home (a)
- `scripts/controller/dev` is bash: at most ~250 lines on top of the moved tools/dev code.
- `dev_profile.py` (stdlib tomllib) resolves a profile to NUL-separated argv.
- `hostobs/tools/dev` is an exec shim (≤20 lines; `HOSTOBS_DEV_*` maps to `AGENT_DEV_*`, `clean` to `gc --this`).
- P-fd-05 ports it to the fleetd host agent; the CLI, profiles and `host-admission/1` stay.

## 2. Profiles (b)
`scripts/controller/dev-profiles.toml`, keyed by the project dir `-p` takes. **Every container property lives there**, layered `[defaults]` < `[kinds.<kind>]` < profile: `memory`, `memory_max`, `cpus`, `pids`, `max_s`, `user`, `ulimits`, `stop_timeout`, `mounts`, `prefix`, `env`. Profile-only: `kind` (python/rust), `lane`, `environment` (owns the image), `dev_image` (Rust), `workdir`, `cache` (0-1 path), `setup` (argv list), `partial_args`; a kind adds `verbs` (argv templates). The fixed safety invariants (8) are not keys: naming one exits 2.

Verbs. Python: `test -- <args>` (setup, then `-m pytest <args>`), `lint` (default compileall + shellcheck), `run -- CMD`, `shell`. Rust: the tools/dev verbs (build, check, clippy, fmt, test/nextest, example, gdb, strace, run, shell). All: status, stop, gc.

Main lanes: hostobs and run-gate-project `selftest`; cmru `assay`; cli-extended and fleetd `r0-r1`; assay `tooling-tests`; tls-edge `tests`; ciu `ciu`; scripts/controller and scripts/netcup `suite`; agctl its first lane (P-plat-09).

Python setup:
- **Lanes with run-gate `kind="assay"`:** `env` and the interpreter are read from the assay.toml lane at call time. For cmru: `[lanes.cmru]`. The profile may only add keys.
- **Shell-text setup is declared.** For cmru it is survivor-regressions' `pip install … --editable ../assay`.
- **`partial_args`** are added when `test` gets any argument. For cmru this is `--cli-case-partial`; without it its conftest exits 4.

Agreement test (`tests/test_dev.py`): one profile per project; the lane exists; environment and workdir equal the lane's; every `setup`/`env`/`partial_args` word occurs verbatim in that project's run-gate.toml argv; Rust uses `tester-unified-rust` with a `dev_image`.

## 3. Names (c)
- `<hex12>`: the first 12 hex digits of sha256 of the worktree's physical toplevel path (as tools/dev).
- `<proj>`: the dir with `/` turned into `-` and `libraries-` dropped.
- Container `agent-dev-<proj>-<hex12>`; volume `agent-dev-cache-<proj>-<hex12>`; dir `~/agent-dev/<hex12>/` (`work`, `lock`, `cid`, `up-*`, `last-use`).
- One checkout per worktree, serialised by `lock`.
- Legacy `hostobs-dev-*` state: `dev gc` removes it by exact name, same rules.

## 4. Hosts (d)
- v1001 by default. r1002 needs `AGENT_DEV_HOST=r1002` and r1002 in `CONTROLLER_REMOTE_GATE_HOSTS` (set per lease). Others: exit 2.
- **No local mode:** `--local` exits 2 even with `AGENT_DEV_ALLOW_LOCAL=1`. Cockpit docker is the production daemon.

## 5. Tree shipping
- As tools/dev: thin bundle (against the host's last head or nearest `~/checkouts` seed), binary patch plus untracked tar (≤20 MiB, else exit 2), reset hardened checkout, `.git` read-only.
- The tool only reads `~/checkouts`; the sub-controller keeps a fresh main seed there (M1: 7.6 s bundle on a stale seed).
- Batch: 2 hx calls (preflight, upload+run); tty: 3. About 2.5 s each.

## 6. Images (k, l)
- **(k)** Preflight requires the host's image id (Rust: `dev_image`, whose label `…rust-dev.base-image-id` must also equal the environment image's host id) to equal the cockpit's; if the cockpit lacks the tag, presence alone is checked and the host id printed. Missing or different: exit **2** before admission, naming tag, ids and `push-image.sh`. Never pulls or builds.
- **(l)** A push is an image change: the main controller's explicit grant FIRST, even within a measurement lease. Then the controller or sub-controller runs `scripts/controller/push-image.sh <host> <tag>` (Rust: both images) after a rebuild merges and before first use, pushing the id the environment resolves to in the cockpit; a cockpit image rebuilt from an unmerged branch is never pushed. r1002 included. No silent prune: removal by exact id, with a grant.

## 7. Admission (e): `host-admission/1`
Written once in `scripts/netcup/runner/README.md`; run-job, remote-gate.py and `fleet host start` adopt it in P-fd-05.
- **Overcommit is intended:** no MemAvailable check, no memory-weighted slots. The ONLY rule: wait while memory PSI `full` avg10 >= 3 (CPU, io, memory-`some` never block).
- **Ticket:** `~/host-admission/ticket` (flock) serialises starts, taken after lock and sync. Held 20 s from admission (`( exec 9>&-; sleep 20 ) &` keeps its fd; the settle starts just before `docker run`) so the next start sees this container's load.
- **Wait:** PSI every 10 s, progress every 60 s, exit 75 at `AGENT_DEV_WAIT_S` (900); unreadable PSI: 75 at once.
- **ssh drop:** in batch mode the docker client runs in the background and the shell `wait`s, so a HUP/INT/PIPE/TERM to the remote shell runs the EXIT trap at once: it removes the container (by cid) before the lock and ticket fds go. An ssh drop that sends NO signal is the remaining window: it is noticed on the container's next output (EPIPE) or at the in-container `timeout` (`max_s`); the container and the per-worktree lock stay until then (a retry exits 3) and `dev stop` frees them. In tty mode the shell waits for `script` in the foreground; the trap runs when it returns. A SIGKILL of the wrapper is bounded the same way.
- **Cockpit slot:** the weight-1 slot shared with remote-gate.sh is gone; dev and lanes meet only in the host's PSI.
- **Known limit:** remote-gate lanes and legacy `hostobs-dev-*` containers share `dev-gates.slice` without the ticket.
- **Same worktree:** a second call exits 3 (non-blocking `lock`).

## 8. Container (f, i)
- **Fixed by the tool:** `--rm --init --pull never --network none --cap-drop ALL --security-opt no-new-privileges --cgroup-parent dev-gates.slice`, exact names, `nice 10 ionice -c3 timeout`.
- **From the profile:** `--user 1003:1003 --ulimit core=0:0 --pids-limit 1024 --cpus 2 --stop-timeout`.
- **Memory:** `--memory-reservation=<memory>` (Rust 3g, Python 1g; M1 cmru peak 66 MB), `--memory=<memory_max>` (default 8g: finite on purpose; cgroup v2 reclaims to swap before any OOM), `--memory-swap=-1`.
- **Bounds:** cpus in (0,3], memory 512m-8g, memory_max 1g-16g and >= memory, pids <= 1024, time <= 14400 s, user not root; else exit 2.
- **PID namespace:** private (never `--pid=host`), tini as PID 1: kill/pkill reach only the container; pid-signalling tests run here.
- **SYS_PTRACE:** `gdb`/`strace` only (with uid 1003 it only widens seccomp).

## 9. Output, exit, gc (g, h)
- **Banner:** `AGENT-DEV <proj> <host> <sha12>[+dirty]: NOT GATE EVIDENCE`, first and last stderr line; `+dirty` = non-empty patch or tar.
- **Env:** `AGENT_DEV_{HOST,MEMORY,CPUS,PIDS,MAX_S,WAIT_S}` (MEMORY = the soft reservation); seams `AGENT_DEV_HX`, `AGENT_DEV_TEST_{PSI_DIR,CLOCK,PROFILES}`; no image override; other `HOSTOBS_DEV_*` exit 2.
- **Exit codes:** the command's propagates; the tool's own, printed as `dev: exit N` before the closing banner, are 2 usage/refusal/image, 3 transport/identity/busy, 75 not admitted (memory PSI full avg10 >= 3 until `AGENT_DEV_WAIT_S`, or PSI unreadable; nothing ran), 124 time limit.
- **`dev gc [--dry-run|--this]`:** a hex is stale if absent from `git worktree list` with `last-use` > 3 days, or `last-use` > 30 days. Removes `agent-dev-cache-*-<hex>` (exact name) and `~/agent-dev/<hex>`; skips held locks and live containers; never touches `~/checkouts`.

## 10. CTL-50 brief text (j)
```
Dev runs (CTL-50):
- Before every commit run `scripts/controller/dev -p <proj> <verb> ...`, one call at a time, never parallel or backgrounded.
- Exit 75 = host busy, nothing ran: retry once; if 75 again, stop and report it.
- A dev run is NOT evidence; only registered lanes on the exact committed head are. Report "dev: ...", never a gate result.
- Never call hx.sh, ssh, docker, push-image.sh or remote-gate directly; never run pytest, cargo or test scripts in the devcontainer.
- Code that signals pids (kill, pkill, process groups, hand mutants) runs only in the dev container.
- Until scripts/controller/dev exists in your worktree: hostobs/tools/dev for Rust; for Python report "no dev run".
```
