# agent-cli image — one container per dispatched agent session

Implements **D-G5** of `docs/plan-resource-governance.md`.

## Why

An agent session is currently a forked **process** (`wrapper.launch_detached`
→ `os.fork()`). That has three consequences we want to end:

1. **No cgroup placement is possible.** `--cgroup-parent` is a Docker flag; a
   forked child just inherits the daemon's cgroup. A runaway agent is
   unbounded.
2. **The only sandbox is whatever the CLI implements**, and those differ per
   tool and are not under our control. Concretely: codex emitted `BLOCKED` this
   session because its internal sandbox treated the worktree's `.git` as
   read-only, so it could neither commit nor gate its own work — correct
   behaviour from codex, but a capability we needed.
3. **Session state lives wherever the CLI puts it**, entangled with whatever
   tree it happened to run in.

Containerising fixes all three at once: the container is the boundary (so the
CLI's own sandbox can be relaxed), it gets a real leaf slice, and session state
moves to a named volume independent of any worktree.

## Build

Pass the **real host uid/gid** — the agent writes into a bind-mounted worktree
owned by the host user, and a uid mismatch produces files the host cannot
manage plus a git index the agent cannot write.

```bash
docker build \
  --build-arg AGENT_UID="$(id -u)" \
  --build-arg AGENT_GID="$(id -g)" \
  -t nyxloom-agent-cli:local \
  nyxloom/infra/agent-cli/
```

## Auth — a one-time INSTALL step, never part of dispatch

`claude` and `codex` are subscription-backed and need an interactive login
once. Do it against the persistent volume so every later session inherits it:

```bash
docker volume create nyxloom-agent-state

docker run --rm -it \
  -v nyxloom-agent-state:/home/agent \
  nyxloom-agent-cli:local bash
# inside:  claude   → complete login
#          codex login
#          (reasonix / opencode: API keys, if used)
```

This belongs in the local install routine alongside slice installation. A
dispatch path must never hit an interactive login prompt — a headless agent
facing one hangs until its timeout and reports a confusing failure.

Also set a git identity in the volume, or the agent cannot commit:

```bash
git config --global user.name  "nyxloom-agent"
git config --global user.email "agent@nyxloom.local"
```

> Not cosmetic. The DR8 package found that `git revert` fails outright in a
> container with no global git config — which is why `_revert_reviewer_repair`
> passes `-c user.name=/-c user.email=` explicitly. Setting it once here means
> future code does not have to remember.

## Run (shape the daemon will use)

```bash
docker run --rm \
  --cgroup-parent=nyxloom-agents-<task>.slice \
  --memory=<host-owned> --memory-swap=<generous> \
  -v nyxloom-agent-state:/home/agent \
  -v /path/to/.worktrees/<task>:/work \
  nyxloom-agent-cli:local \
  <argv from adapters.build_dispatch>
```

Only the task's own worktree is mounted. Nothing else from the host is visible.

⚠️ **A missing or misspelled slice name does NOT error** — systemd silently
creates a transient slice with **no limits at all** (verified 2026-07-27). So
`--cgroup-parent` typos fail *open*, which is why `nyxloom doctor` must assert
every configured slice exists as a real unit and fail closed when it does not.

## Version pinning

The CLI versions are build args, pinned on purpose. An agent CLI changing
behaviour under a running factory is indistinguishable from a model
regression, and would be attributed to the wrong layer — capability scores
(D-R2b) are only meaningful when the harness is held constant.

Current pins: `codex 0.145.0` · `reasonix 1.17.12` · `opencode-ai 1.18.1` ·
`claude` via its native installer (not version-pinnable through npm).

The `node:26-slim` base major must match the toolchain the CLIs were validated
against (host: node v26.5.0 / npm 11.17.0). A mismatch fails the build rather
than silently running on an untested runtime.
