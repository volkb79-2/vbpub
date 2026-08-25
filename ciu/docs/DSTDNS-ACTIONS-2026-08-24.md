# dstdns Actionable Items from CIU Reconciliation — 2026-08-24

These are changes the **dstdns** project should make with the **existing**
CIU config schema (no v8 required). Ordered by priority.

---

## DSTDNS-1: Remove dead `[ciu]` keys

**File:** `ciu.global.defaults.toml.j2`

Remove these lines from `[ciu]`:
```toml
workspace_env_file = "ciu.env"   # CIU never reads this; hardcoded
```

Replace the old `fail_fast = true` with the new closed-vocabulary key:
```toml
exit_on = "WARN"    # exit on warnings OR errors (strictest)
```
This preserves dstdns's current fail-fast intent while using the new
declarative vocabulary (CIU-QOL-2).

`repo_root` and `physical_repo_root` in `[ciu]` are informational values
exposed to templates. They can stay, but note that CIU does not enforce them.

---

## DSTDNS-2: Replace `${VAR:-fallback}` patterns with `${VAR:?message}`

**Files:** all `ciu.compose.yml.j2` templates

Search for `:-` patterns that provide silent fallbacks for values that have
an authoritative source. Per AGENTS.md doctrine, a default is legitimate only
when it is correct in the absence of information.

Known offenders:
```yaml
user: "${CONTAINER_UID:-0}:${CONTAINER_GID:-${DOCKER_GID:-0}}"
```
These should be:
```yaml
user: "${CONTAINER_UID:?source ciu.env}:${DOCKER_GID:?source ciu.env}"
```
(UID/GID 0 may be legitimate for some containers — audit each case.)

---

## DSTDNS-3: Move service identity out of global `[service.*]` into per-stack defaults

**Priority:** Medium — reduces typo risk, eliminates duplication
**Effort:** Medium (~35 stacks)

Currently `ciu.global.defaults.toml.j2` carries ~40 entries like:
```toml
[service.applications.controller.controller]
name = "controller"
image_name = "controller"
image_tag = "latest"
internal_port = 8080
```

And each stack references them via verbose Jinja paths:
```toml
name = "{{ service.applications.controller.controller.name }}"
```

A typo in any path segment silently renders empty. Move these three fields
directly into each stack's `ciu.defaults.toml.j2`:

```toml
# applications/controller/ciu.defaults.toml.j2
[controller]
name = "controller"
image_name = "controller"
image_tag = "latest"
internal_port = 8080
```

Keep the global registry ONLY for cross-stack topology references
(`topology.services.*`) and for services consumed by other stacks but not
deployed by them.

---

## DSTDNS-4: Adopt CIU's `instances = N` configfile fan-out

**Files:** `applications/worker-io/ciu.defaults.toml.j2`, `applications/worker-db/ciu.defaults.toml.j2`, and their compose templates

Currently worker-io declares TWO separate configfile sections:
```toml
["worker_io"."worker-io-1".configfile.app]
["worker_io"."worker-io-2".configfile.app]
```

Replace with CIU's built-in fan-out:
```toml
[worker_io.instances]
count = 2

[worker_io.configfile.app]    # one section; CIU fans out to worker-io-1, worker-io-2
template = "config.toml.j2"
target = "/etc/worker-io/config.toml"
instances = 2
```

Then update the compose template to iterate using the same count instead of
a manual `{% for %}` loop. This eliminates duplication between configfile
sections and compose service generation.

---

## DSTDNS-5: Add per-service health-timeout for slow services

**Priority:** Low
**Depends on:** CIU-QOL-8

Authentik takes ~240s; workers take ~5s. Once CIU supports per-service
`health_timeout`, add it to authentik and vault phase entries so they don't
force a global timeout high enough to waste time on fast services.

Until then, authentik/vault use `health = false` or a high global timeout.

---

## DSTDNS-6: Add provisioning refs to single-stack validation awareness

**Status:** Already fixed upstream (CIU-QOL-1)

Once the fix is deployed, malformed `requires = [...]` entries will be
caught during `ciu up --dir <stack>` as well as profile-mode up. No dstdns
action needed beyond upgrading CIU when released.

---

## DSTDNS-7: Add worktree concurrency budget

**File:** `ciu.global.defaults.toml.j2`

Add to prevent resource exhaustion when many worktree instances run:
```toml
[ciu.worktree]
max_concurrent_instances = 3
```

---

## DSTDNS-8: Plan for v8 migration items

These require v8 schema changes and should be tracked for when v8 lands:

| Item | Current | V8 target |
|---|---|---|
| App-domain tables at top level | `[workflow]`, `[pubsub]`, `[authentik]`, etc. | Declared via `ciu.user_tables` |
| Build version literals | Scattered across Dockerfiles/bake files | `[build.python]` + `ciu refresh` |
| Topology mixing endpoints/routes/external | One namespace | Split into endpoints + routes |
| Phase-based ordering | Manual `[deploy.phases.phase_N]` | Computed from init_requires graph |
| Profile conflates selection+topology | Single concept | Service groups + deployment profiles |
