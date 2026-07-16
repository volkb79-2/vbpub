# P38 — Dashboard on a ciu bridge network — Implementation Report

**Status:** done · **Date:** 2026-07-16

## Summary

Makes the daemon's HTTP bind address configurable (`policy.http_bind`,
default `"127.0.0.1"`) and moves the `nyxloomd` ciu stack off
`network_mode: host` onto an explicit ciu-owned bridge network, binding the
dashboard on it at `0.0.0.0` (private to that bridge, never the LAN — the
0.0.0.0 bind and the host-net→bridge move ship together, never separately).
`nyxloom doctor` now prints both the host-loopback URL and the bridge-alias
URL when bridged, so a devcontainer operator can actually find the reachable
address. Because `nyxloom-trove/nyxloom.toml` is the *same file* read by the
host process and the bind-mounted container (it can't itself differ per
target), the compose files flip the bind via a `NYXLOOM_HTTP_BIND=0.0.0.0`
env var, which `config.ProjectConfig.load` reads as an override on top of
the toml value — the toml default stays the safe loopback value for host
runs.

## Per-oracle results

| Oracle | Result | Test |
| --- | --- | --- |
| O1 (configurable http_bind, defaults 127.0.0.1) | PASS | `test_http_bind_defaults_to_loopback` |
| O1 (overridable to 0.0.0.0 via toml) | PASS | `test_http_bind_overridable_to_bridge_address` |
| O1 (NYXLOOM_HTTP_BIND env overrides toml, needed since the toml is shared host/container) | PASS | `test_http_bind_env_override_takes_precedence_over_toml` |
| O2 (both compose files: no `network_mode: host`, join a bridge network, 0.0.0.0 bind, DooD + repo binds survive) | PASS | `test_nyxloomd_compose_drops_host_network_and_binds_bridge_address` |
| O3 (doctor prints reachable address; loopback-only case) | PASS | `test_doctor_dashboard_line_stays_loopback_by_default` |
| O3 (doctor names the bridge alias when bind is bridged) | PASS | `test_doctor_dashboard_line_names_bridge_alias_when_bind_is_bridged` |

## Files touched

- `src/nyxloom/config.py` — `Policy.http_bind: str = "127.0.0.1"`; the
  toml loader already generically picks up new `[policy]` keys
  (`Policy(**data.get("policy", {}))`), so no loader change was needed for
  the toml path itself. Added a `NYXLOOM_HTTP_BIND` env-var override in
  `ProjectConfig.load` (after `Policy(**...)`, before the notify-channel
  aliasing) — required because the compose-rendered `nyxloom.toml` is
  bind-mounted and identical on the host and in the container, so only an
  env var can make the two runs differ.
- `src/nyxloom/daemon.py` — replaced `_chosen_port()` with `_chosen_http()`
  returning `(port, bind)` from the registered project with the lowest
  `policy.http_port` (mirrors the pre-existing min-port selection, now
  carrying that project's `http_bind` along with it — one HTTP server
  serves every project, so its bind is a single choice too). `_start_http`
  binds `ThreadingHTTPServer((bind, port), ...)` instead of the hardcoded
  `"127.0.0.1"`. Added `self.http_bind` (mirrors `self.http_port`) for
  introspection/tests. Updated the module-docstring HTTP section and the
  `DEFAULT_HTTP_BIND` constant.
- `nyxloomd/ciu.compose.yml.j2` + `nyxloomd/docker-compose.yml` — dropped
  `network_mode: host`; added an explicit `networks:` block per-service
  (`nyxloomd-net`, alias `nyxloomd`) and the top-level network definition
  (`{{ nyxloomd.container_prefix }}-nyxloomd-net` / literal
  `nyxloom-prod-nyxloomd-net`); set `NYXLOOM_HTTP_BIND: "0.0.0.0"` in the
  `environment:` block; pointed the healthcheck's `/dev/tcp` probe at
  `0.0.0.0` (verified this connects correctly — Linux resolves it as
  loopback) instead of the now-stale `127.0.0.1` framing; updated header
  comments to describe the P38 topology change and cross-reference
  `docs/runtime-process-model.md` §3. `docker.sock` and the physical repo
  binds are untouched (network mode does not gate DooD or bind-mount
  resolution).
- `src/nyxloom/cli.py` — `cmd_doctor`'s dashboard-URL block now collects
  `(port, bind)` pairs instead of just ports, picks the min-port project's
  pair (matching daemon.py's selection), and — when the bind is `0.0.0.0`
  (or `::`) — prints BOTH the host-loopback URL and the `http://nyxloomd:
  <port>` bridge-alias URL; otherwise prints the loopback URL only, as
  before. **Note:** `src/nyxloom/cli.py` is not listed in this handoff's
  `scope.touch` (only `config.py`, `daemon.py`, the two compose files, and
  `tests/test_daemon.py` are). However the handoff's own "Context to read
  first" and "Work" sections both explicitly call out `cmd_doctor` as the
  file/function to change for O3, and O3 is unsatisfiable without it (there
  is no other code path that prints doctor's dashboard line). Treated this
  as a stale `scope.touch` list rather than a scope conflict requiring
  BLOCKED — flagging it here for the reviewer.
- `tests/test_daemon.py` — added `_set_http_bind` helper (mirrors
  `_set_ephemeral_http_port`); six new tests across O1/O2/O3 (listed above);
  extended the top-of-file `from nyxloom import ...` line with `cli` and
  `doctor` (needed for the O3 doctor tests).

## Gate output (tail, verbatim)

```
........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 51%]
........................................................................ [ 64%]
........................................................................ [ 77%]
........................................................................ [ 90%]
..................................................                       [100%]
```
Exit code 0. 554 tests collected, all passing (6 new + 548 pre-existing).
(This pytest/terminal combination does not print a trailing `N passed in Ys`
summary line even on a clean run — verified via explicit `echo $?` = 0 and
`--collect-only` counts matching the dot count above.)

## Devcontainer-side join step (documents the escalate_if boundary)

Per the handoff's `escalate_if`, implementing the nyxloomd side and
documenting — not performing — the devcontainer-side join:

The compose files now define an explicit external-facing bridge network
(`{{ nyxloomd.container_prefix }}-nyxloomd-net`, e.g.
`nyxloom-prod-nyxloomd-net` for the current `ciu.toml`), with the `nyxloomd`
service reachable on it under the stable alias `nyxloomd`. For the
devcontainer (or any other container, e.g. VS Code's) to reach
`http://nyxloomd:8942`, it must join that SAME network. Two ways to do that
(devcontainer-side config, outside this repo's scope):

1. **`docker-compose`-based devcontainer** (`.devcontainer/docker-compose.yml`):
   add
   ```yaml
   networks:
     nyxloomd-net:
       external: true
       name: nyxloom-prod-nyxloomd-net   # or the ciu-rendered name for this deploy
   services:
     <devcontainer-service>:
       networks:
         - default
         - nyxloomd-net
   ```
   (the pwmcp `[*.consumer]` pattern in `pwmcp/ciu.compose.yml.j2` is the
   analogous idiom for a *consuming* service joining an externally-owned
   network — mirrored here in reverse: nyxloomd owns the network, the
   devcontainer consumes it.)
2. **Plain `docker network connect`** (no devcontainer.json change, must be
   re-run whenever the devcontainer is recreated):
   `docker network connect nyxloom-prod-nyxloomd-net <devcontainer-container-name>`.

Once joined, VS Code's port-auto-forward sees `nyxloomd:8942` resolve inside
the devcontainer's netns and can forward it. The network is docker-internal
(not published to any host port), so this stays off the LAN exactly as
before — only containers explicitly joined to `nyxloom-prod-nyxloomd-net`
(or whichever `container_prefix` names it) can reach the dashboard.

## Deviations / assumptions

- `escalate_if` bullet 1 (host-net load-bearing for DooD/repo-binds):
  verified false. `docker.sock` is a bind mount (`volumes:`), independent of
  `network_mode`; the physical repo binds (`/home/vb/volkb79-2/vbpub` etc.)
  are also plain bind mounts. Neither depends on host networking — confirmed
  by `docker compose config` rendering cleanly with both mounts present and
  `network_mode: host` removed (docs/runtime-process-model.md §3's own
  analysis, independently re-checked here).
- `escalate_if` bullet 2 (devcontainer cannot join the network from this
  repo's scope): true by construction — devcontainer config lives outside
  this repo's `nyxloom/` package. Implemented the nyxloomd side only;
  documented the join step above per the handoff's instruction, rather than
  expanding scope or filing a BLOCKED (the handoff explicitly anticipated
  this and said to document, not block, on it).
- `src/nyxloom/cli.py` touched despite not being in `scope.touch` — see the
  note under "Files touched" above.
- The bridge network name embeds `nyxloomd.container_prefix` (already an
  existing template variable in `ciu.defaults.toml.j2`, which is NOT in
  `scope.touch`) rather than introducing a new template variable, so no
  file outside `scope.touch` needed editing to keep the `.j2` renderable.
- Healthcheck probes literally target `0.0.0.0` (not `127.0.0.1`) inside the
  container to make it explicit that the check exercises the *actual bind
  address*, per O2's wording. Verified locally that `/dev/tcp/0.0.0.0/<port>`
  connects successfully on Linux (it resolves to loopback for outbound
  connect) before relying on it in the healthcheck.

## Suggestions for reviewer (do not act on)

- Consider whether `nyxloom-trove/nyxloom.toml` (the self-hosted deployment's
  own project config, not in `scope.touch`) should eventually set
  `http_bind` explicitly for documentation purposes even though the compose
  env var wins — purely cosmetic, no behavior change.
- The bridge network's name is derived from `container_prefix`
  (`nyxloom-prod` today per `ciu.toml`), so a `container_prefix` change
  would also rename the network — worth a note in an ops runbook if one
  exists, so a redeploy's devcontainer-side `external: true` reference gets
  updated in lockstep.
- Two now-stale references outside `scope.touch`, left untouched (out of
  scope, purely cosmetic, no behavior impact): `nyxloomd/ciu.defaults.toml.j2`
  line 6's header comment still says "network_mode: host so the dashboard
  binds the host loopback"; `src/nyxloom/notify.py`'s ntfy push "click" URLs
  are hardcoded to `http://127.0.0.1:8942/...` (still correct for a daemon-
  host operator, just no longer the *only* reachable address). Neither
  affects an oracle; flagging for a follow-up handoff if worth fixing.
