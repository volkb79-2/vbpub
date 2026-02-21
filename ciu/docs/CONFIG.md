# CIU Configuration Specification (Authoritative)

This document defines the configuration structure and rendering behavior used by CIU and CIU Deploy. It is aligned to the current packaged implementation.

## Quick Start (User-Facing)

1. Generate workspace env and source it:
  - env-workspace-setup-generate.sh
  - source .env.ciu
2. Render configs (recommended for debugging and preflight):
  - ciu --render-toml
3. Deploy stacks (orchestration):
  - ciu-deploy --deploy

## File Roles (Rendered vs Templates)

- ciu-global.defaults.toml.j2 (template, committed): canonical global defaults
- ciu-global.toml.j2 (template, committed): full editable copy of defaults (override layer)
- ciu-global.toml (rendered, gitignored): runtime global config (source of truth)
- <stack>/ciu.defaults.toml.j2 (template, committed): stack-specific overrides
- <stack>/ciu.toml (rendered, gitignored): runtime stack config
- <stack>/docker-compose.yml.j2: service compose template
- <stack>/docker-compose.yml: rendered compose

CIU reads templates, renders, and writes the rendered files. CIU Deploy reads rendered ciu-global.toml for orchestration.

## Render & Merge Model (Behavioral Contract)

1. Jinja2 render TOML templates (global, then stack) using the current config context.
2. Expand environment placeholders ($VAR, ${VAR}) in rendered TOML. Missing values fail-fast.
3. Parse TOML and deep-merge:
  - Global defaults + global overrides → ciu-global.toml
  - Stack defaults + stack overrides → ciu.toml
  - Merged config = deep-merge(global, stack)
4. Resolve secret directives (Vault/local/external) into the merged config.
5. Generate hostdir paths and create directories.
6. Render docker-compose.yml from the merged config.
7. Build docker compose env by flattening the merged config (details below).

There is no _template suffix expansion stage in the current implementation. All template logic uses Jinja2 + env expansion.

## Global Configuration Sections

### [ciu]
Engine-level settings for CIU behavior:
- ciu.repo_root
- ciu.physical_repo_root
- ciu.workspace_env_file
- ciu.fail_fast

### [deploy]
Orchestrator settings used by CIU Deploy:
- deploy.project_name (required)
- deploy.environment_tag (required)
- deploy.network_name (required; can be set via DOCKER_NETWORK_INTERNAL)
- deploy.log_level

Subsections:
- [deploy.labels] (label prefix and policy)
- [deploy.health] (interval/timeout/retries/start_period)
- [deploy.resources] (optional resource policies)
- [deploy.registry] (url + namespace for registry mode)
- [deploy.env.defaults] (global env injected into all services)
- [deploy.env.shared] (shared values for templates)
- [deploy.groups] (named phase sets)
- [deploy.phases.*] (ordered deployment plan)

### [topology.*]
Canonical routing/service topology used by tooling (url_builder, reverse proxy, external health checks):
- [topology.external] (public_fqdn, base_url, ports)
- [topology.services.*] (service metadata)
- [topology.routes.*] (external route paths and enable flags)

### [registry.*]
Central registries for service-level bootstrap and hooks:
- [registry.postgresql] and [registry.postgresql.users]
- [registry.redis.acl] and [registry.redis.users.*]

### [vault.paths]
Single registry of Vault secret paths referenced by directive-based secrets.

### [consul.whitelist.*]
Whitelist for Consul KV seeding by hooks:
- [consul.whitelist]
- [consul.whitelist.services.*]

## Stack Configuration Sections (Per Stack)

### [env]
Flat key/value list for stack-scoped secrets/shared values (no nesting). These values are flattened into compose env as ENV_<KEY>.

### [hooks]
Hook lists are defined under the stack root key (see below) as:
- <stack_key>.hooks.pre_compose
- <stack_key>.hooks.post_compose

### [service.*] (Global Service Definitions)
Service definitions are stored under [service.<category>.<project>.<service>] in ciu-global.defaults.toml.j2.
CIU extracts these and exposes them at top-level keys for Jinja2 templates.

Naming rules:
- Use underscores in TOML keys (worker_io) even if directory names use hyphens (worker-io).
- Use the name field for Docker/hostnames that include hyphens.

Example:
```
[service.applications.worker_io.worker_io]
name = "worker-io"
internal_port = 8080
```

CIU exposes this as:
- worker_io.name
- worker_io.internal_port

### [service.env] (Tier 3)
Service-specific environment variables rendered via Jinja2 loops.

### [service.hostdir]
All bind-mount host directories are defined here so CIU can pre-create them with correct ownership.
Empty values auto-generate ./vol-<service>-<purpose>.

### [state] and [secrets.local]
Runtime state and locally generated secrets (GEN_LOCAL) are persisted in stack ciu.toml only.

## Stack Root Key (Critical)

CIU expects exactly one top-level stack key in stack config (other than state). That key owns hooks and stack-specific sections.
Example stack root key: db_core, worker_io, controller.

## Secret Directives (Supported)

CIU resolves these directives before docker-compose rendering:

### Vault-backed directives

These directives create or read secrets in Vault and MUST be treated as Vault-managed:

- GEN_TO_VAULT:<path> — generate once, store in Vault
- ASK_VAULT:<path> — read from Vault
- ASK_VAULT_ONCE:<path> — generate-once semantics (rotated on reset)

### Local/external directives

These directives are NOT Vault-managed:

- GEN_LOCAL:<path> — generate locally and persist in stack ciu.toml only
- GEN_EPHEMERAL — generate per run, never persisted
- ASK_EXTERNAL:<key> — prompt or read from env at runtime
- DERIVE:<algo>:<source> — deterministic derived secret

**Important**: GEN_TO_VAULT is Vault-backed. Local persistence is GEN_LOCAL.

Plaintext secrets are never written to docker-compose.yml; placeholders are used instead (see below).

## Compose Environment Flattening (Authoritative)

CIU builds the docker compose environment by flattening the merged config:

- Nested keys are joined with underscores and uppercased.
- Lists are joined with commas.
- The [env] section is special-cased to emit ENV_<KEY> entries.
- PWD is injected automatically.

Example:
```
db_core.secrets.postgres_superuser_password
→ DB_CORE_SECRETS_POSTGRES_SUPERUSER_PASSWORD

[env]
REDIS_PASSWORD = "GEN_TO_VAULT:..."
→ ENV_REDIS_PASSWORD
```

Templates should reference secrets via ${<FLATTENED_KEY>} placeholders so rendered compose files never contain plaintext.

## Template Rules (Jinja2)

- All configuration values must come from TOML (no hardcoded values in templates).
- Use deploy.* and top-level service keys in templates.
- Missing values are fatal (fail-fast).
- File mounts must use ${PWD}/file or ${PHYSICAL_REPO_ROOT}/... to avoid Docker creating directories.

## Fail-Fast Requirements

- .env.ciu must define REPO_ROOT, PHYSICAL_REPO_ROOT, DOCKER_NETWORK_INTERNAL, CONTAINER_UID, DOCKER_GID, PUBLIC_FQDN, PUBLIC_TLS_*.
- deploy.project_name and deploy.environment_tag must be set.
- Stack config must have a single root key.

## Minimal Example (Stack)

Stack config (infra/db-core/ciu.defaults.toml.j2):
```
[db_core]

[env]
POSTGRES_PASSWORD = "GEN_TO_VAULT:{{ vault.paths.postgres_superuser }}"

[db_core.postgres.hostdir]
data = ""
logs = ""
```

Compose template (infra/db-core/docker-compose.yml.j2):
```
services:
  {{ db_core.postgres.name }}:
   <<: *service-defaults
   environment:
    - POSTGRES_PASSWORD=${DB_CORE_SECRETS_POSTGRES_SUPERUSER_PASSWORD}
   volumes:
    - {{ db_core.postgres.hostdir.data }}:/var/lib/postgresql
```
