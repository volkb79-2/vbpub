---
schema_version: 1
id: nyxloom-P38-dashboard-bridge-network
project: nyxloom
title: "Dashboard on a ciu bridge network (devcontainer/VS Code reachable, no host-net)"
tier: sonnet5-high
input_revision: "5a4a4aa"
depends_on: [nyxloom-P37-tini-supervisor-crash-safety]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/config.py"
    - "src/nyxloom/daemon.py"
    - "nyxloomd/ciu.compose.yml.j2"
    - "nyxloomd/docker-compose.yml"
    - "tests/test_daemon.py"
oracles:
  - id: O1
    observable: "The HTTP surface bind address is CONFIGURABLE via a new `policy.http_bind` (config.Policy, default `\"127.0.0.1\"` — safe/loopback by default). daemon.py binds `http.server.ThreadingHTTPServer((cfg.policy.http_bind, port), ...)` at daemon.py:1854 instead of the hardcoded `\"127.0.0.1\"`. A test asserts the server binds to the configured address (default 127.0.0.1; overridable to 0.0.0.0)."
    negative: "the bind stays hardcoded to 127.0.0.1, so the dashboard is only ever reachable from the daemon's own netns — the devcontainer can never reach it (2026-07-16 finding)."
    gate: tester-unified
  - id: O2
    observable: "The nyxloomd stack moves OFF host-networking onto a ciu-owned bridge network, and binds the dashboard on it: in BOTH `nyxloomd/ciu.compose.yml.j2` and `nyxloomd/docker-compose.yml`, `network_mode: host` is removed, the service joins a ciu bridge network (with a stable container alias), `http_bind` is set to `0.0.0.0` (safe now: it's a private bridge, NOT the host — the user accepted trusting the docker network, no token), and the healthcheck targets the bind address. The docker.sock mount (DooD for agent gates) stays. A test asserts the compose files no longer contain `network_mode: host` and set the 0.0.0.0 bind."
    negative: "0.0.0.0 bind is left on host-networking (exposes the dashboard on the LAN) — the exact thing that must NOT happen; the bind change and the bridge move are inseparable."
    gate: tester-unified
  - id: O3
    observable: "`nyxloom doctor`'s dashboard-URL line reflects reachability: it prints the bridge-reachable address (container alias / the bind) rather than only 127.0.0.1, or documents both (host-loopback still works from the daemon host; the alias works from a co-networked container). A test asserts doctor's output names the reachable host."
    negative: "doctor still prints only 127.0.0.1, misleading a devcontainer operator who cannot reach it there."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "moving off host-networking breaks the daemon's DooD agent-dispatch (docker.sock) or the physical-path repo binds — if host-net is actually load-bearing for those, STOP and file a D-decision (the analysis in docs/runtime-process-model.md §3 says it is NOT, but verify)"
  - "the devcontainer cannot be made to join the chosen bridge network from within this repo's scope (that join is devcontainer-side config) — implement the nyxloomd side, document the devcontainer join step in the REPORT, do not expand scope"
---

# P38 — Dashboard on a ciu bridge network (devcontainer-reachable, still local-only)

Makes the read-only dashboard reachable from the devcontainer (so VS Code
auto-forwards it) WITHOUT exposing it to the LAN, per the user's decision to
trust the internal docker network (no bearer token). See
`docs/runtime-process-model.md` §3 for the full topology analysis. Pairs with
P37 (tini) in the same rebuild — both edit the nyxloomd compose, hence the
dependency.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P38-dashboard-bridge-network` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `docs/runtime-process-model.md` §3 — the host-net vs bridge topology, why the
  devcontainer can't see host-loopback, and why option-1 (shared bridge) is the
  fix. Confirms host-net is NOT load-bearing for DooD/repo-binds.
- `src/nyxloom/daemon.py` ~1807-1858 — the HTTP server setup; the hardcoded
  `("127.0.0.1", port)` bind at ~1854 is what you make configurable.
- `src/nyxloom/config.py` `class Policy` (~92-116) — add `http_bind: str =
  "127.0.0.1"` next to `http_port`; confirm the toml loader reads it.
- `nyxloomd/ciu.compose.yml.j2` (~31 `network_mode: host`, ~52 healthcheck) +
  `nyxloomd/docker-compose.yml` — move to a ciu bridge network + set the bind.
  Look at how a CONSUMER stack joins a shared ciu network (e.g. the pwmcp
  `[*.consumer]` pattern referenced in the repo's CLAUDE.md) for the idiom.
- `src/nyxloom/cli.py` `cmd_doctor` — the dashboard-URL print (added 2026-07-16);
  update it for O3.

## Work

1. `config.Policy`: add `http_bind: str = "127.0.0.1"`; wire the toml loader.
2. `daemon.py`: bind on `cfg.policy.http_bind` (min-port project's, matching the
   existing http_port selection).
3. `nyxloomd/ciu.compose.yml.j2` + `docker-compose.yml`: drop `network_mode:
   host`, join a ciu bridge network with a stable alias, set the daemon's
   `http_bind` to `0.0.0.0` (private bridge), point the healthcheck at it, keep
   the docker.sock + repo binds. Update the header comment.
4. `cmd_doctor`: print the bridge-reachable dashboard address (O3).
5. `tests/test_daemon.py`: prove O1/O2/O3.
6. REPORT: document the devcontainer-side step to join the network (ciu consumer
   config) so VS Code forwarding works end-to-end.

## Scope / forbid

Touch ONLY the five files in `scope.touch`. The 0.0.0.0 bind MUST ship together
with the host-net→bridge move (never 0.0.0.0 on host-net). Keep DooD + repo binds.

## BLOCKED rule

If host-networking turns out to be load-bearing for DooD/repo-binds (contra the
design note), or the bridge move needs a product call, STOP — write
`BLOCKED: <reason>` to the LOG, commit, exit; raise a `D-<NNN>`.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
