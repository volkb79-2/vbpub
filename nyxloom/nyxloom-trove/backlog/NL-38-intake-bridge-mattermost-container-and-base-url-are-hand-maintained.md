---
kind: backlog-entry
schema_version: 1
id: NL-38
title: "[intake_bridge] container and base_url in nyxloom.toml are hand-maintained copies of the mattermost instance id and went stale when ciu regenerated it"
status: open
type: "bugfix"
severity: "medium"
provenance: "controller session 2026-10-07 (operator report: Mattermost not responding correctly; mm_reachability.py live run)"
filed_date: "2026-10-07"
---

## Observed

On 2026-10-06 04:23 the nyxloom checkout's ciu instance identity was regenerated with the schema-2 identity file (`ciu.instance.generated.toml`, `instance_id` `1dd3d1` -> `3oqua1`). The production Mattermost stack came back as `nyxloom-3oqua1-mattermost`; its data survived because it lives in stack-dir bind mounts.

`nyxloom/nyxloom-trove/nyxloom.toml` `[intake_bridge]` still named `container = "nyxloom-1dd3d1-mattermost"` and `base_url = "http://nyxloom-1dd3d1-mattermost:8065"`. Both the `mmctl` and `rest` transports would target a container that no longer exists. Mattermost itself was healthy: `mm_reachability.py` reported 39 PASS and 0 FAIL against the stack's own config.

The values were corrected by hand on 2026-10-07. The same hand-maintained pattern remains in:
- `nyxloom/pwmcp-instance/README.md` (`nyxloom-1dd3d1-nyxloomd-net`, `nyxloom-1dd3d1-pwmcp`);
- `nyxloom/mattermost/README.md` examples;
- `nyxloom/mattermost/docker-compose.yml` line 25 comment (`nyxloom-1dd3d1-mattermost` as an example; `ciu.compose.yml.j2` itself carries no `1dd3d1`);
- `nyxloom/docs/plan-benchmark-ingest.md`, `plan-logging.md` and `plan-next-batches.md` (`nyxloom-1dd3d1-pwmcp` / `nyxloom-1dd3d1-nyxloomd` in operational steps; historical plans, so decide per file whether to rewrite or leave dated);
- `nyxloomd/ciu.toml` `container_prefix`, not examined.

## Expected

The bridge derives the container name and base URL from the mattermost stack's rendered config (`nyxloom/mattermost/ciu.toml` `container_prefix` plus the app name and port), or from `ciu.instance.generated.toml`, at load time. A stale explicit override fails loudly with the expected name. Docs use `<instance_id>` placeholders instead of a literal id.

## Oracles

- With an explicit stale `container` in `nyxloom.toml`, loading the bridge config refuses with a message naming the running container.
- With the keys omitted, the bridge resolves the current container name from the stack config.
- `mm_reachability.py` (or a sibling check) can verify the nyxloom.toml consumer values against the live container.

## Design (controller ruling 2026-10-07)

Ruling: `nyxloom.toml` must not reference a volatile container name. `[intake_bridge]` is service discovery, not lane configuration (only `[gates.*]` consumes the central run-gate lanes). ciu is the single source of truth for instance identity, and this need is independent of the planned run-gate-into-ciu-v8 merge. Every claim below was checked on 2026-10-07 against the files cited; items marked INFERRED were not run.

### (a) `base_url` via a stable network alias: already works by default, only network membership is open

- The compose service key is `mattermost` (`nyxloom/mattermost/ciu.compose.yml.j2:68`). It sets `container_name`/`hostname` to `{{ mattermost.container_prefix }}-mattermost` (`:70-71`) and declares NO `aliases:` anywhere in the file (grep for `aliases` returns nothing).
- Compose gives every service its service key as a DNS alias on each network it joins. Measured on the live container: `docker inspect nyxloom-3oqua1-mattermost` shows `Aliases = ['nyxloom-3oqua1-mattermost', 'mattermost']` on BOTH `nyxloom-3oqua1-mattermost_internal` and `ingress_public`. So `http://mattermost:8065` already resolves today; no template edit is needed to create the alias. State this plainly: only network membership matters.
- Which network: the stack-owned private bridge `internal` (`ciu.compose.yml.j2:362-366`, attached by the app service at `:334-335`; the live name is `<compose project>_internal` = `nyxloom-3oqua1-mattermost_internal`). It holds exactly the app and its db (`docker network inspect`: members `nyxloom-3oqua1-mattermost`, `nyxloom-3oqua1-mattermost-db`). Use `internal`, NOT `ingress_public`: the live stack has `expose_public = true` (`mattermost/ciu.toml:4`, rendered; template default `ciu.defaults.toml.j2:41`), so the app is also on the SHARED `ingress_public` network (`ciu.compose.yml.j2:368-370`), where the alias `mattermost` is not unique per instance (a second instance of this stack would carry the same alias there). On `internal` the alias is unambiguous per compose project.
- Is the bridge or any consumer attached today: NO. Verified: the devcontainer (`dstdns-devcontainer-vb`) is attached to `bridge` plus four other networks and neither `nyxloom-3oqua1-mattermost_internal` nor `ingress_public`; `getent hosts mattermost nyxloom-3oqua1-mattermost` prints nothing here. The `nyxloomd` stack declares only its own `nyxloomd-net` (`nyxloomd/ciu.compose.yml.j2:106-110`, `:145-148`), no mattermost network, and no nyxloomd container is running now (`docker ps`). `mattermost/README.md:249-264` and `:937-942` already say this (the `rest` transport only works from inside the `_internal` network). Consequence: the alias fix alone makes the `rest` base_url stable but not reachable; the consumer must also join `<project>_internal`, which still embeds the id in the network name. Joining is therefore an attach-by-resolved-name step (see CIU-118 follow-up), not a literal in nyxloom.toml.
- The `mmctl` transport does not read `base_url` at all (`src/nyxloom/intake_bridge.py:389-391` requires only container/team/channel); `base_url` matters only for `rest` (`:433`, request built at `:437`).
- Related volatility the alias does not cure: the provisioning hook persists webhook URLs with the id baked in (`mattermost/hooks/post_compose_provision.py:567` builds `http://{container_prefix}-mattermost:{port}`; `:864` stores `{base}/hooks/<id>`). Those secrets go stale on the same identity change (recorded in ciu CIU-132 item 4). Out of scope here; switching `internal` to the alias at `:567` is a candidate follow-up, INFERRED safe, not tested.

### (b) the mmctl `docker exec` target, resolved at runtime

- Today the target is the literal `bc.container` (`src/nyxloom/intake_bridge.py:393`, `argv = ["docker", "exec", str(bc.container), MMCTL, ...]`), copied from `[intake_bridge].container` (`src/nyxloom/config.py:170`, loaded at `:569-578`).
- ciu names the compose project `{deploy.project_name}-{deploy.environment_tag}-{stack dir basename}` (`ciu/src/ciu/engine.py:948-968`; config-less fallback `:970`). With nyxloom's `environment_tag = "$INSTANCE_ID"` (`nyxloom/ciu.global.defaults.toml.j2:22`) that is `nyxloom-3oqua1-mattermost`, matching the live label `com.docker.compose.project=nyxloom-3oqua1-mattermost` with `com.docker.compose.service=mattermost` (verified by `docker inspect`). The project name therefore ALSO contains the id; a pure label lookup needs a discriminator other than the literal name.
- Order of resolution (design):
  1. `ciu resolve --profile default --stack mattermost --json` ALREADY EXISTS (CIU-118, ciu 7.15.3; see below) and returns `compose_project` and `container_name` for the current checkout's instance. Verified on the live checkout: `compose_project = nyxloom-3oqua1-mattermost`, `container_name = nyxloom-3oqua1-mattermost`. This is the preferred source and contradicts "once one exists": it exists now. Whether the process running the bridge has the `ciu` CLI is NOT verified (INFERRED: the bridge runs where the docker socket is, as `README.md:249` says).
  2. Fallback without ciu: `docker ps --filter label=com.docker.compose.service=mattermost --filter label=com.docker.compose.project.working_dir=<ciu root> --format {{.Names}}`. The `working_dir` label is `/workspaces/vbpub/nyxloom` on the live container (verified); whether it is the logical or physical path under another DooD layout is INFERRED, not checked. Zero or more than one match refuses (c).
  3. Never read the id out of `ciu.instance.generated.toml` and re-derive names: SPEC-V8 states the derivation "is not a consumer contract" (`ciu/docs/SPEC-V8.md` S4.1.1, line 177).
- Where it lives in code: `IntakeBridgeConfig.container`/`base_url` default to None; add a resolver in `intake_bridge.py` that fills them at poll time when unset. `is_configured` (`:390`, `:433`) must be evaluated after resolution.

### (c) fail loud

- Current behaviour with a stale explicit `container`: `docker exec` fails and the bridge reports only `mmctl post list exited <rc>: <stderr[:200]>` (`intake_bridge.py:411`) or, on a missing docker binary, `mmctl post list failed: <ExceptionName>` (`:405`). The `rest` path reports `mattermost REST read failed: URLError` (`:450`) with no host named. Neither names the running container (read, not run: running it would require editing nyxloom.toml).
- Design: when `container` or `base_url` is set explicitly, check it once per poll. For `container`, `docker inspect <name>` must succeed and be running; for `base_url`, the host part must equal the resolved container name, the resolved service alias, or an alias in the resolved container's network settings. If not, raise `BridgeError` naming the explicit value and the actual candidate(s) from (b), e.g. `[intake_bridge] container 'nyxloom-1dd3d1-mattermost' is not running; the running mattermost service for this checkout is 'nyxloom-3oqua1-mattermost' (compose project nyxloom-3oqua1-mattermost)`. No fallback to the candidate: an explicit override that does not match refuses (same no-silent-reroute principle as `resolve_reader`, `:484-495`).

### (d) migration

Once (a)+(b) exist: delete `container` and `base_url` from `nyxloom/nyxloom-trove/nyxloom.toml:245` and `:254` and the comment block `:246-253` that tells operators to hand-maintain them; leave a one-line comment pointing at this entry. Until then keep the literals but treat them as a known hazard. `mm_reachability.py` should then also check what the bridge resolves (see CIU-118 follow-up for the second consumer).

### (e) stale `1dd3d1` references: NL-38's list is INCOMPLETE

Method: `git grep -n 1dd3d1` over the tracked tree on 2026-10-07, excluding `CHANGES*`, `**/reports/**`, `**/archive/**`. Already recorded above: `pwmcp-instance/README.md`, `mattermost/README.md`, `mattermost/docker-compose.yml:25`, `docs/plan-benchmark-ingest.md`, `docs/plan-logging.md`, `docs/plan-next-batches.md`, `nyxloomd/ciu.toml` ("not examined"). Not recorded, found by the re-grep:

- `nyxloom/nyxloomd/ciu.toml:5` `container_prefix = "nyxloom-1dd3d1"` — a TRACKED file (last touched in 4037e4e05), unlike the gitignored rendered `mattermost/ciu.toml` (`.gitignore:216` `**/ciu.toml`), so it is a committed literal that contradicts the template's derived prefix (`nyxloomd/ciu.defaults.toml.j2:19`). Examined now: it is stale. Whether ciu overwrites it on render is INFERRED, not checked.
- `nyxloom/nyxloomd/docker-compose.yml:64` (`container_name: nyxloom-1dd3d1-nyxloomd`) and `:188` (`name: nyxloom-1dd3d1-nyxloomd-net`) — the pre-rendered plain-compose copy; the `.j2` is derived.
- `nyxloom/nyxloomd/systemd/README.md:18`.
- `nyxloom/docs/runtime-process-model.md:114`, `:125`, `:194`.
- `nyxloom/ntfy/README.md:35,46,48,49,50,52`.
- `nyxloom/pwmcp-instance/ciu.compose.yml.j2:8,12` and `ciu.defaults.toml.j2:6` (comments, but they name the id as if fixed).
- `nyxloom/nyxloom-trove/nyxloom.toml:251` (the history comment; goes away with (d)).
- `nyxloom/mattermost/README.md` is far larger than "examples": 41 lines carry the id (`grep -c`; e.g. `:28`, `:215`, `:251-264`, `:418-542`, `:685`, `:769-770`, `:814-862`, `:881-917`, `:939-965`), including runnable commands and a Python snippet with a literal `BASE` (`:965`).

Excluded as history/records, left dated: `ciu/KNOWN_ISSUES_TODO_BACKLOG.md:4974-4988` (CIU-132), `ciu/handoff/ciu-physical-root-{LOG,REPORT}.md`. The opposite direction also exists: `3oqua1` is a literal in `nyxloom/nyxloom-trove/nyxloom.toml:245,248,251,254` only (plus CHANGES and backlog prose); those four are what (d) removes. Per-file rule: runbooks and READMEs get `<instance_id>` placeholders or a `ciu resolve` one-liner; dated `docs/plan-*.md` stay as written.

## Related

ciu CIU-115 (schema-2 instance identity); CIU-104 (`environment_tag = "$INSTANCE_ID"`); ciu CIU-118 (`ciu resolve --json`, shipped; its 2026-10-07 follow-up is the service-discovery gap this entry needs); ciu CIU-132 (persisted webhook URLs embed the id).
