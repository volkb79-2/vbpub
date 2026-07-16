# P19 — nyxloom as a ciu root stack; controller in a container

> Tier: sonnet5-high · Date: 2026-07-15 · User directive: ciu global lives
> in `nyxloom/`; `ntfy` becomes one stack; the controller/daemon moves
> to `nyxloom/controller/` as its OWN ciu project stack with a
> Dockerfile (run the daemon in a container, not directly). nyxloom
> becomes its own ciu ROOT. Read handoff/STANDING.md. This is an
> infra/restructure package — the daemon PYTHON code is unchanged; what
> changes is packaging + deployment. Independent of P16/P17/P18 (different
> files) — may run in parallel with them.

## Owned paths
- NEW `nyxloom/ciu.global.defaults.toml.j2` + `ciu.global.toml.j2` (root)
- MOVE ntfy to a stack under the root (it already has ciu.* files — retarget
  them to inherit the root global instead of its own standalone global;
  keep the external ntfy-data volume + configfile overlay).
- NEW `nyxloom/controller/`: `Dockerfile`, `ciu.defaults.toml.j2`,
  `ciu.compose.yml.j2`, a small `README.md`.
- `docs/DEPLOY.md` (how to bring the root up).
- Do NOT touch `src/nyxloom/**` logic (only add a console entry if
  needed) or the tests.

## The controller container (the hard part — get these right)
The daemon LAUNCHES agents in git worktrees and needs, inside the container:
- **docker.sock** (the dstdns test-runner gate is `docker exec`), mounted;
  run as a uid in the docker group (reuse the ciu.env DOCKER_GID pattern).
- **the consumer repos** bind-mounted at the SAME paths the registry uses
  (`/workspaces/vbpub`, `/workspaces/dstdns`) — the daemon's worktree paths
  and PHYSICAL_REPO_ROOT must resolve; document that the container shares
  the host bind layout (DooD).
- **the CLI binaries** the routes call: claude / codex / opencode / reasonix
  on PATH inside the image (install in the Dockerfile or bind from host),
  AND their auth: `~/.claude`, `~/.codex`, `~/.reasonix`, `~/.config/opencode`
  mounted read-write (sessions/tokens live there).
- **the XDG state dir** ($XDG_STATE_HOME/nyxloom) on a persistent volume
  or host bind — the event log / statefiles / leases / www MUST survive
  container restart (disk-authoritative invariant).
- **NTFY_TOKEN / NTFY_CMD_TOKEN / (P18) decision token** as Docker secrets,
  not env literals.
- HTTP dashboard port published loopback-only (or via tls-edge labels if a
  human wants remote access — a follow-up, default loopback).
- Base image: `FROM dstdns-app-base` is NOT right (that's the app runtime).
  Use a python:3.13-slim or the mdt base + install the four CLIs + git.
  Whatever base — INHERIT the KSM opt-in if mdt-derived (the daemon+agents
  are memory users too).

## Bootstrapping note (call it out in the README)
The controller container is managed BY ciu, yet the daemon it runs dispatches
agents that touch the host docker. That is fine (ciu owns the container
lifecycle; the daemon owns work dispatch) but means: `ciu up` in
nyxloom/controller starts the daemon; the daemon does NOT manage its own
container. Stopping the factory = `ciu down` (or the daemon's own SIGTERM,
which leaves detached wrappers running — document that a container stop does
NOT kill in-flight agent wrappers, matching today's restart-safe behavior).

## Oracles (this is infra — oracles are deploy-shaped)
1. `ciu up --dir . ` (or the documented root command) from nyxloom/
   renders and starts BOTH the ntfy stack and the controller stack under one
   root global; container `nyxloom-<env>-controller` reaches healthy.
2. Inside the controller container: `docker ps` works (sock reachable),
   `git -C /workspaces/dstdns status` works (repo mounted), `claude --version`
   works (CLI+auth present), and `nyxloom status --project topos` reads
   the persisted state (state volume mounted).
3. Restart the controller container: state intact (events/statefiles), an
   in-flight agent wrapper started before the restart is STILL running after
   (detached-survival invariant) and its receipt is collected.
4. Secrets: NTFY tokens are Docker secrets (not visible in `docker inspect`
   env); the dashboard binds loopback.
5. ntfy stack still deploys under the new root and keeps its external volume.

## Rules
STANDING.md applies. This is a real infra lift — if a required mount/auth
path cannot be threaded (e.g. a CLI that refuses containerized auth), STOP
and BLOCKED with the specific blocker rather than shipping a container that
can't actually dispatch. Do not commit. REPORT to
handoff/reports/P19-REPORT.md; receipt-only final.
