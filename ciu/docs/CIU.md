# CIU — Single-Stack User Guide

CIU renders a stack's TOML templates, resolves secrets, executes hooks, generates
the compose overlay, and starts one stack via `docker compose`. For multi-stack
orchestration see [CIU-DEPLOY.md](CIU-DEPLOY.md). Normative contract:
[SPEC.md](SPEC.md).

---

## Quick Start (against test-repo)

```bash
# 1. Bootstrap the workspace env (once per machine / on reset)
cd test-repo
ciu --generate-env
source ciu.env

# 2. Render TOML only (preflight / debugging)
ciu -d infra/redis-core --render-toml

# 3. Dry-run — full pipeline except `docker compose up` [S8.3 steps 1–15]
ciu -d infra/redis-core --dry-run

# 4. Start the stack
ciu -d infra/redis-core

# 5. Inspect the secrets table (names, directives, store paths — never values)
ciu -d infra/redis-core secrets list       # [S4.25]

# 6. Rotate a local secret (deletes its store file)
ciu -d infra/redis-core secrets reset --name redis_password   # [S4.25]

# 7. Full reset — containers + volumes + rendered files (keeps secrets)
ciu -d infra/redis-core --reset            # [S6.4]

# 8. Reset everything including secret store files
ciu -d infra/redis-core --reset --secrets  # [S4.25, S6.4]
```

---

## CLI Reference

CIU ships one console entrypoint, **`ciu`**, a flat verb dispatcher. The full
verb table and legacy-flag mapping live in **[FEATURES.md](FEATURES.md)**; the
common verbs are:

| Verb | Purpose |
|---|---|
| `ciu env [generate]` | show / regenerate `ciu.env` |
| `ciu render` | render global + stack config (TOML only) |
| `ciu up [--profile N \| --dir PATH]` | render + secrets + `compose up` |
| `ciu down` | stop containers (volumes preserved) |
| `ciu clean` | complete teardown (S6.4 invariant) |
| `ciu health [--preflight]` | health gate / image tool probe |
| `ciu diagnose` | read-only OOM/exit/health/log diagnosis (S10.5) |
| `ciu bake` · `ciu dev <stack>` | build prod image · run the dev loop (S5a) |
| `ciu secrets list\|reset` | inspect / delete secret store files |

`ciu <verb> -h` prints that verb's own options. The remainder of this section
documents the **single-stack engine flags** (the `ciu -d <stack> …` form, which
`ciu up --dir`/`ciu render`/`ciu clean` wrap) — useful when driving one stack
directly.

### Stack selection

| Flag | Meaning |
|---|---|
| `-d PATH` / `--dir PATH` | Stack directory (default: cwd) |
| `--define-root PATH` | Override repo root; disables walk-up [S1.1] |
| `--root-folder PATH` | Alias for `--define-root` |

### Run modes

| Flag | Stops after |
|---|---|
| `--render-toml` | Step 3 — TOML render only [S8.3] |
| `--dry-run` | Step 15 — overlay written, compose skipped [S8.3] |
| `--print-context` | After merge — prints redacted JSON [S4.23] |
| `--generate-env` | Regenerates `ciu.env` then continues [S2.8] |
| `--shipped` | Run a pre-shipped `docker-compose.yml` through CIU [S8.6] (see Dual shipping) |

### Skips / cleanup

| Flag | Effect |
|---|---|
| `--reset` | `compose down -v`, remove `vol-*`, rendered files [S6.4] |
| `--reset --secrets` | Also deletes secret store files [S4.25] |
| `--skip-hostdir-check` | Skip hostdir creation (cleanup mode) |
| `--skip-hooks` | Skip all hook points |
| `--skip-secrets` | Skip secret resolution + overlay (compose fails if secrets consumed) |
| `-y` / `--yes` | Non-interactive; absent prompts abort instead of asking |
| `-f NAME` | Compose template filename (default: `ciu.compose.yml.j2`) |

### Secrets subcommand

```
ciu -d <stack> secrets list            # print table: name / directive / store path / exists
ciu -d <stack> secrets reset           # interactive confirmation unless -y
ciu -d <stack> secrets reset --name X  # single secret
```

### Exit codes [S10.3]

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Runtime failure (compose, health, hooks, Vault I/O) |
| `2` | Configuration / validation error (static checks: S3/S4/S11, argparse) |
| `3` | Environment / bootstrap error (missing env keys, DooD preflight, S1/S2) |

### Runtime diagnosis [S10.5]

`ciu diagnose` complements the health gate: health says whether the stack is
ready; diagnosis explains common reasons it is not. It reads Docker metadata,
health history, resource limits, and only a bounded log tail. It never reads
container environment variables, restarts a service, or applies a remedy.

```bash
ciu diagnose                         # all CIU-labelled containers
ciu diagnose --project myapp --logs 200
ciu diagnose --project myapp --json # automation/report attachment
```

Findings include definitive Docker OOM flags, probable SIGKILL/137 cases,
unhealthy commands and their latest output, restart loops, swap-disabled
container limits, Redis channel-ACL mistakes, disk exhaustion, and segfaults.
The command intentionally distinguishes evidence from inference; for example,
exit 137 is reported as a probable OOM only when Docker did not retain a
definitive `OOMKilled=true` flag.

`ciu health --preflight --strict` is the complementary static runtime check:
it shell-parses rendered `CMD`/`CMD-SHELL` healthchecks and verifies that each
external executable exists in its image. Quoted inline programs and shell
builtins such as `exit` are not probes. This catches a real `wget`-in-a-distroless
image error without false reports for `exit 1` or Python source text.
Direct healthcheck commands matching a distroless image's declared entrypoint
are recognized without requiring that image to contain a shell.

---

## Dual shipping [S8.5–S8.6]

CIU renders its compose to **`ciu.compose.yml`** (gitignored), never to
`docker-compose.yml`. That frees a maintainer to commit a hand-written
`docker-compose.yml` in the same stack directory for admins who prefer plain
`docker compose up` — CIU never overwrites it, and `--reset` never deletes it.

Two ways to run the shipped file:

```bash
docker compose up -d              # plain — no CIU involvement at all
ciu --shipped -d <stack>         # through CIU: adds the wiring below
```

`ciu --shipped` is a passthrough that **skips** the stack config requirement and
all secret / overlay / configfile steps, but still:

- loads `ciu.env` so the compose file's `${VAR}` interpolation resolves the same
  machine facts (UID/GID, network name, physical paths) as the native path,
- ensures and (in a devcontainer) attaches the workspace network [S2.8],
- runs the DooD reachability preflight [S1.5],
- then `docker compose -f docker-compose.yml up -d` (override the filename with
  `-f`). `--dry-run` stops before the compose up.

In `ciu-deploy`, set `shipped = true` on a service in
`[deploy.phases.*].services` to route that stack through its
`docker-compose.yml`; it still participates in phase ordering and the health
gate (see [CIU-DEPLOY.md](CIU-DEPLOY.md)).

When to prefer the shipped path: trivial stacks, or environments that need a
literal `docker-compose.yml` for other tooling. Otherwise the CIU path
(`ciu.compose.yml` + overlay) gives you secrets, configfiles, and host-aware
paths — see the value proposition in the [project README](../README.md).

---

## Dev loop (`ciu dev`) [S5a]

`ciu bake` builds the **production** image. A stack that also needs an iterative
dev loop — a hot-reload server, and/or a pre-build chain coupled to a *live*
service — declares it as `[<root>.dev]` and runs it with `ciu dev <stack>`. CIU
stays build-tool-agnostic: the profile describes any dev server (Vite, Next,
`uvicorn --reload`), no framework specifics live in CIU [CIU-5].

```toml
# applications/webapp-ui/ciu.defaults.toml.j2
[webapp_ui.dev]
image      = "node:22-alpine"
# contract-coupled pre-build: pull the LIVE backend's OpenAPI, then codegen types
prebuild   = ["npm run fetch:openapi", "npm run gen:api"]
command    = "npm run dev"                 # the long-running dev server (HMR)
port       = 5173                          # publish the HMR port
mount      = ["./:/app", "/app/node_modules"]   # source bind + anon node_modules
depends_on = ["webapp-server"]             # wait_healthy before prebuild (CIU-4)
# image OR build = { context = ".", dockerfile = "Dockerfile", target = "dev" }
```

```bash
ciu dev applications/webapp-ui            # render → wait deps → prebuild → dev server
ciu dev applications/webapp-ui --no-prebuild   # re-run the server only
```

What `ciu dev` does [S5a.2]: renders the stack config, validates the profile
(`[S5a]` shape errors exit 2), waits for each `depends_on` service to be healthy
(exit 1 on timeout — start it with `ciu up` first), resolves the image (`image`
or builds `build`), then runs prebuild steps **and** `command` in one ephemeral
`--rm` container — `sh -c '<prebuild…> && exec <command>'`. The single container
means prebuild output lands in the same mounted source tree the server serves,
and a failed prebuild aborts before the server starts. `network` defaults to the
stack's `deploy.network_name`, so prebuild steps reach the live `depends_on`
service. `ciu dev` is the local loop only — it is not part of the
`up`/`down`/`clean` lifecycle and writes no rendered/overlay artifacts.

> Edge cases: `port` accepts an int, `"host:container"`, or a list; `image` and
> `build` are mutually sufficient (give one); without `depends_on` the loop
> starts immediately; a missing `command` or both-`image`-and-`build`-absent is a
> hard `[S5a]` abort.

---

## 17-Step Pipeline [S8.3]

The table below is the execution order for each `ciu -d <stack>` run.
`--render-toml` stops after step 3; `--dry-run` stops before step 16.

| Step | What happens | Spec |
|---|---|---|
| 1 | Load `ciu.env`; abort on missing required keys | S2.1–S2.2 |
| 2 | Render global chain: `ciu.global.defaults.toml.j2` → `ciu.global.toml.j2` → merged `ciu.global.toml` | S3.1–S3.3 |
| 3 | Render stack: `ciu.defaults.toml.j2` → `ciu.toml.j2` → `ciu.toml` (preserving `[state]`) | S3.1–S3.4 |
| 4 | Deep-merge global + stack config | S3.3 |
| 5 | Static validation (single root key, directive placement, name uniqueness, phase keys…) | S11 |
| 6 | Optional reset (`--reset`) | S6.4 |
| 7 | Auto-generate `auto_generated.*` (build_version, build_time, uid, gid, docker_gid) | S3.9 |
| 8 | Pre-create hostdirs with ownership; run seed copy if first creation | S6.1–S6.6 |
| 9 | **`pre_secrets` hooks** — provider bootstrap (unseal Vault, fetch dynamic creds) | S9.1, S8.3 |
| 10 | Resolve + materialize secrets to store files | S4.8–S4.16 |
| 11 | **`pre_compose` hooks** — runs after materialization; may read store files | S9.1, S9.3 |
| 12 | Render configfile templates (secret() available) | S5.1–S5.4 |
| 13 | Render `ciu.compose.yml` (secrets replaced by guard objects) | S4.21, S8.1 |
| 14 | Leak scan: abort if a secret value appears in rendered compose | S4.22 |
| 15 | Generate overlay `.ciu/ciu.compose.overlay.yml` (secrets + configfile mounts) | S4.17, S5.5 |
| 16 | `docker compose -f ciu.compose.yml -f .ciu/ciu.compose.overlay.yml up -d` | S8.1 |
| 17 | **`post_compose` hooks** | S9.1 |

---

## Stack Authoring Guide

### Root key rules [S3.5–S3.7]

Every stack config MUST have **exactly one** non-reserved top-level key. The
reserved names are: `state`, `ciu`, `deploy`, `topology`, `registry`, `vault`,
`consul`, `service`, `env`, `auto_generated`, `secrets`. Use `snake_case`.

```toml
# Good — root key is redis_core
[redis_core]
stack_name = "redis-core"

# Bad — [vault] collides with the global [vault.paths] table
# [vault]   ← rename to vault_core (see test-repo/infra/vault/ciu.defaults.toml.j2)
```

Subsections under the root key [S3.6]:

```
[<root>]                     — stack-level scalars
[<root>.env]                 — stack-scoped env (key/value, not secrets)
[<root>.hooks]               — hook lists (pre_secrets / pre_compose / post_compose)
[<root>.secrets]             — stack-wide secrets
[<root>.<service>]           — per-service scalars
[<root>.<service>.hostdir]   — bind-mount paths
[<root>.<service>.secrets]   — per-service secrets
[<root>.<service>.configfile.<name>]  — configfile mount
```

> `[env]` at the top level is invalid; always `[<root>.env]`. [S3.6]

---

### Hostdir patterns [S6.1–S6.7]

**Simple auto-generated path** — empty string auto-generates
`<stack>/vol-<service>-<purpose>`, owned `CONTAINER_UID:DOCKER_GID` mode `0775`:

```toml
# test-repo/infra/redis-core/ciu.defaults.toml.j2
[redis_core.redis.hostdir]
data = ""
```

**Fixed-UID image** (e.g. postgres uid 70) — grant the service UID exclusive
ownership while the operator keeps access via the docker group [S6.7a]:

```toml
# test-repo/infra/db-core/ciu.defaults.toml.j2
[db_core.postgres.hostdir]
data = { path = "", uid = 70, mode = "0770" }
```

CIU provisions the ownership directly or via a helper container when the CIU
process lacks privilege [S6.5]. No init containers needed.

**Named volume** (no host visibility required) — author a `volumes:` entry in
the compose template and omit the `hostdir` declaration entirely [S6.7b]. CIU
does not manage named-volume contents; `--reset` cleans them via `down -v`.

**Seed** — copy initial content (config trees, bootstrap data) into a newly
created hostdir [S6.6]:

```toml
[myapp.app.hostdir]
data = { path = "", seed = "seed-data" }   # seed-data/ relative to stack dir
```

The seed is applied **once** on first creation; pre-existing dirs are never
re-seeded.

After merge, every hostdir value exposed to templates is an **absolute physical
path** [S6.2] — emit it directly as the bind source:

```yaml
volumes:
  - {{ redis_core.redis.hostdir.data }}:/data   # absolute physical path [S6.2]
```

---

### Configfile mounts (`configfile` + `secret()`) [S5]

For own applications, mount a rendered config file instead of injecting
individual env vars:

**Stack config** (`test-repo/applications/app-config/ciu.defaults.toml.j2`):

```toml
[app_config.app.configfile.main]
template = "config.toml.j2"
target = "/etc/app/config.toml"
```

**Config template** (`test-repo/applications/app-config/config.toml.j2`):

```toml
[auth]
api_key = "{{ secret('api_key') }}"    # [S5.4] — only place secret values may appear
license  = "{{ secret('license') }}"

[build]
version = "{{ auto_generated.build_version }}"   # [S3.9]
```

For a single service, the compose service key usually equals the `<service>`
component of the configfile section path — here `app` from
`[app_config.app.configfile.main]` [S5.3]. For replicated services, one base
section such as `[app_config.worker.configfile.main]` fans out to rendered
compose keys `worker-1`, `worker-2`, and so on. The mount appears in the
overlay, not in `ciu.compose.yml`:

```yaml
# ciu.compose.yml.j2
environment:
  - APP_CONFIG={{ app_config.app.configfile.main.target }}   # [S5.5] — pointer only
```

Compose env carries only bootstrap pointers [S5.5]: the config-file path, early
log level, TZ. Everything else lives in the mounted file.

---

### Secret directives [S4.2]

Directives are recognized **only** inside tables named `secrets` under the root
key [S4.1]. The six directives:

| Directive | Behavior | Persistence |
|---|---|---|
| `ASK_VAULT:<path>[#field]` | Read from Vault KV2; fail if absent | Vault |
| `GEN_TO_VAULT:<path>` | Create-if-missing in Vault, then read | Vault |
| `GEN_LOCAL:<name>` | Create-if-missing in project store | `<repo-root>/.ciu/secrets/<name>` |
| `ASK_EXTERNAL:<key>` | Env `<key>` / `CIU_SECRET_<NAME>` / interactive prompt, then cached | Stack store |
| `ASK_FILE:<path>` | Pre-provisioned file; referenced in place (no copy) | External file |
| `GEN_EPHEMERAL` | New value every run | None |

All four non-`ASK_FILE` directives may be written as a plain string or as an
inline table with optional fields [S4.4]:

```toml
[app_config.secrets]
api_key    = "GEN_LOCAL:demo/app_api_key"
license    = "ASK_EXTERNAL:CIUDEMO_LICENSE"
run_nonce  = "GEN_EPHEMERAL"
ca_bundle  = "ASK_FILE:files/demo-ca.pem"

# Inline-table form with options:
db_pass = { directive = "ASK_VAULT:demo/db_password", mode = "0444", uid = 999 }

# expose_env: opt-in escape hatch for images with no *_FILE support [S4.19]
legacy_pass = { directive = "ASK_VAULT:demo/legacy", expose_env = "LEGACY_PASS" }
```

`expose_env` is per-secret, opt-in, and discouraged — CIU logs a notice for
each exposed secret. It is invalid on `ASK_FILE`.

Secret names follow `[a-z][a-z0-9_]*` and must be unique across all `secrets`
tables of one stack [S4.6].

---

### Secret consumption ladder [S4.17–S4.19]

For each secret, pick the consumption pattern in order of preference:

1. **`*_FILE` convention** — image supports `POSTGRES_PASSWORD_FILE`:
   ```yaml
   secrets: [postgres_password]
   environment:
     - POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password
   ```
   Reference: `test-repo/infra/db-core/ciu.compose.yml.j2`

2. **Wrapper pattern** — image has no `*_FILE` support [S4.18]:
   ```yaml
   secrets: [redis_password]
   entrypoint: ["sh", "-c"]
   command:
     - exec redis-server --requirepass "$(cat /run/secrets/redis_password)"
   ```
   Reference: `test-repo/infra/redis-core/ciu.compose.yml.j2`

3. **`secret()` in a configfile template** — composite value (DSN, URL) [S5.4]:
   ```toml
   dsn = "postgresql://user:{{ secret('db_pass') }}@host:5432/db"
   ```
   Reference: `test-repo/applications/app-config/config.toml.j2`

4. **`expose_env`** — last resort; keeps `${ENV_NAME}` interpolation [S4.19].
   Discouraged; logged.

The overlay (`.ciu/ciu.compose.overlay.yml`) always declares the secret-file
sources; the compose template declares only consumption (`secrets: [name]`).

---

### Hook authoring [S9]

Three hook points exist under `[<root>.hooks]` [S9.1]:

| Point | Runs at step | Typical use |
|---|---|---|
| `pre_secrets` | 9 — before secret resolution | Unseal Vault, fetch dynamic provider credentials |
| `pre_compose` | 11 — after materialization | Read store files; set `apply_to_config` values visible to configfile templates |
| `post_compose` | 17 — after containers start | Record initialized state (e.g. vault bootstrap token) |

**Required interface**: every hook module exposes `run(config, ctx) -> dict`.
A `Hook` class with a `run` method is also accepted. v1 function names
(`pre_compose_hook`, `PostComposeHook`) are withdrawn [S9.1].

**Context object** (`ctx`): provides `ctx.secret_file(name) -> Path` — the
store-file path for a resolved secret [S9.3]. `pre_secrets` hooks run before
values exist; they must not call `ctx.secret_file`.

The context also carries two **readiness helpers** [S9.3, CIU-4]. `post_compose`
hooks run immediately after `docker compose up -d` (step 17) with no implicit
health gate, so a hook that talks to a service must wait for it itself:

| Helper | Signature | Use |
|---|---|---|
| `ctx.wait_healthy` | `wait_healthy(service, *, timeout_s=120.0) -> bool` | Poll the service's container Docker health until `healthy` (`no-healthcheck` counts as ready). |
| `ctx.wait_tcp` | `wait_tcp(host, port, *, timeout_s=30.0) -> bool` | Dependency-free port probe for images with no healthcheck. |

```python
# A redis ACL hook waits for the port instead of racing startup:
def run(config: dict, ctx) -> dict:
    if not ctx.wait_healthy("redis-core"):        # or ctx.wait_tcp("redis-core", 6379)
        raise RuntimeError("redis-core did not become healthy in time")
    # ... now safe to connect and apply ACLs ...
    return {}
```

Both return `False` on timeout (the hook decides whether that is fatal); neither
is available to `pre_*` hooks (containers do not exist yet).

**Structured return** [S9.4]:

```python
# test-repo/infra/vault/post_compose_vault.py
def run(config: dict, ctx) -> dict:
    token = ctx.secret_file("root_token").read_text().strip()
    return {
        "initialized": {
            "value": True,
            "apply_to_config": True,   # visible to later hooks/templates
            "persist": "state",        # written to [state] in ciu.toml
        },
        "root_token": {
            "value": token,
            "persist": "state",        # S4.16 token source #3
        },
    }
```

`persist: "state"` is the **only** persistence destination; it writes the value
under `[state]` in `ciu.toml`. `apply_to_config` merges the value into the
in-memory config so later hooks, configfile templates, and the compose template
see it.

Hooks MUST NOT mutate `os.environ`; the v1 plain `{KEY: value}` env-update form
is withdrawn [S9.4]. A listed hook file that does not exist aborts the run [S9.2].

**State persistence** [S3.4]:

```python
# test-repo/applications/app-config/pre_compose_app.py
def run(config: dict, ctx) -> dict:
    return {
        "app_config.runtime_note": {
            "value": "set-by-hook",
            "apply_to_config": True,   # visible in config.toml.j2 at step 12
        }
    }
```

---

## Workspace Environment (`ciu.env`) [S2]

`ciu.env` is the machine identity layer — autodetected facts about this machine,
not project configuration [S2.7]. Generate it once:

```bash
ciu --generate-env
source ciu.env
```

Always-required keys [S2.2]:

| Key | Detected from |
|---|---|
| `REPO_ROOT` | `--define-root` → `REPO_ROOT` env → walk-up to `ciu.global.defaults.toml.j2` |
| `PHYSICAL_REPO_ROOT` | `devcontainer.local_folder` label (DooD); native: `= REPO_ROOT` |
| `DOCKER_NETWORK_INTERNAL` | `<repo-name>-<instance-id>-network` |
| `CONTAINER_UID` / `CONTAINER_GID` | current user UID / `DOCKER_GID` |
| `DOCKER_GID` | `stat /var/run/docker-host.sock`; fallback `getent group docker` |

`PUBLIC_FQDN` and `PUBLIC_TLS_*` are required **only** when
`ciu.require_fqdn` / `ciu.require_certs` is true (both default false) [S2.3].

Add to `.gitignore`:

```
**/.ciu/
ciu.env
ciu.global.toml
ciu.global.toml.j2
**/ciu.toml
**/ciu.toml.j2
**/ciu.compose.yml
```

See `.gitignored.ciu` at the repo root for the ready-to-copy list [S1.8].
