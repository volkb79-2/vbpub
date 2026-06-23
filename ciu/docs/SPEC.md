# CIU v4 Specification

| | |
|---|---|
| **Status** | Active |
| **Version** | 4.2.0 |
| **Date** | 2026-06-23 |
| **Supersedes** | docs/CONFIG.md + docs/CIU.md + docs/CIU-DEPLOY.md as normative sources (those become non-normative guides) |

This document is the **single normative contract** for CIU v4. Where any other
document, example, or code comment conflicts with this specification, this
specification wins.

**Package versioning.** The `ciu` wheel is versioned with SemVer derived from git tags
(`ciu-vX.Y.Z`; see `/docs/VERSIONING.md`). The wheel's **MAJOR tracks this SPEC's MAJOR** —
a breaking change to this contract bumps both. ciu **MAJOR bumped to `4.0.0`** for the
breaking release (Seam 4: multi-profile env var rename + repeatable `--profile`),
superseding the `3.x` line (last tag `ciu-v3.1.0`). The current minor is
**4.2.0** (adds provisioning graph S13 + SSH transport S14).
Untagged commits build as `X.Y.Z.devN+g<sha>`.

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY**
are to be interpreted as described in RFC 2119.

Every normative requirement carries a stable ID (`S<section>.<n>`). Tests and
commit messages reference these IDs. IDs are never renumbered; withdrawn
requirements are marked *(withdrawn)*.

---

## S0 — Terminology

- **Workspace / repo root** — the directory identified by `REPO_ROOT`, holding
  `ciu.global.defaults.toml.j2` and `ciu.env`.
- **Stack** — a directory containing `ciu.defaults.toml.j2` and
  `ciu.compose.yml.j2`; the unit `ciu` renders and starts.
- **Stack root key** — the single non-reserved top-level TOML table of a stack
  config (e.g. `redis_core`).
- **Service** — one compose service; a stack MAY contain several
  (`[<root>.<service>]` subsections).
- **Directive** — a string (or inline table) in a `secrets` table declaring how
  a secret value is obtained.
- **Materialization** — writing the resolved secret value to its secret file.
- **Overlay** — the CIU-generated compose file
  `<stack>/.ciu/ciu.compose.overlay.yml` containing top-level `secrets:`
  definitions and configfile mounts.
- **CIU compose** — the stack author's compose template `ciu.compose.yml.j2`
  and its gitignored rendered output `ciu.compose.yml` (the file CIU runs).
- **Shipped compose** — an OPTIONAL, maintainer-authored, committed
  `docker-compose.yml` for a plain `docker compose up` / `ciu --shipped` path.
  CIU runs it but NEVER renders or overwrites it (S8.5).
- **DooD** — docker-outside-of-docker: the CIU process runs in a container
  (devcontainer) while the Docker daemon runs on the host.
- **Logical path** — a path as seen by the CIU process (`REPO_ROOT`-based).
  **Physical path** — the same location as seen by the Docker daemon
  (`PHYSICAL_REPO_ROOT`-based).

---

## S1 — Project & path model

- **S1.1** CIU MUST resolve the repo root in this order: `--define-root`
  (alias `--root-folder`) → `REPO_ROOT` from the environment → walk-up from the
  working directory to the nearest dir containing `ciu.global.defaults.toml.j2`.
- **S1.2** A repo whose `ciu.global.defaults.toml.j2` sets
  `standalone_root = true` is a standalone root: CIU MUST refuse to run with a
  `REPO_ROOT` that does not match that directory.
- **S1.3** Two path namespaces exist (DooD): logical (`REPO_ROOT`) and physical
  (`PHYSICAL_REPO_ROOT`). Everything CIU hands to the Docker daemon as a bind
  source (hostdirs, secret files, configfiles) MUST be a physical path.
- **S1.4** One helper (`to_physical_path(logical) -> physical`) MUST implement
  the mapping `PHYSICAL_REPO_ROOT / relpath(p, REPO_ROOT)`. Paths outside
  `REPO_ROOT` (e.g. `/etc/letsencrypt/...`) pass through unchanged.
- **S1.5** Before the first bind-source is handed to the daemon in a run, CIU
  MUST preflight that `PHYSICAL_REPO_ROOT` is reachable by the daemon
  (e.g. probe with a one-shot container or verify the
  `devcontainer.local_folder` label). On failure CIU MUST abort with a message
  naming `PHYSICAL_REPO_ROOT` and the named-volume-workspace cause.
- **S1.6** Per-stack machine-owned artifacts live in `<stack>/.ciu/`
  (secret files, rendered configfiles, overlay, lock). Project-scoped secrets
  live in `<repo-root>/.ciu/secrets/`. Humans MUST NOT edit `.ciu/` content.
- **S1.7** `**/.ciu/` MUST be gitignored. At startup CIU MUST verify via
  `git check-ignore` (when inside a git work tree) that its `.ciu/` paths are
  ignored, and abort if not.
- **S1.8** Rendered outputs (`ciu.global.toml`, `ciu.toml`,
  `ciu.compose.yml`) and the auto-created override templates
  (`ciu.global.toml.j2`, `ciu.toml.j2`) are gitignored (see `.gitignored.ciu`).
  A maintainer-authored `docker-compose.yml` for the shipped path (S8.5) is
  the one compose-shaped file that is **committed**, not ignored.
- **S1.9** CIU MUST run identically in three execution environments:
  **devcontainer** (DooD), **native host**, and **CI**. On a native host
  `PHYSICAL_REPO_ROOT == REPO_ROOT` and `to_physical_path` is the identity;
  devcontainer-only steps (network self-attach S2.8, the S1.5 preflight's
  named-volume concern) MUST no-op cleanly outside a devcontainer. No
  feature may assume a devcontainer.

## S2 — Workspace environment (`ciu.env`)

- **S2.1** `ciu.env` at the repo root is the authoritative workspace
  environment. CIU MUST generate it when missing and MUST regenerate it on
  `--generate-env`.
- **S2.2** Required keys (always): `REPO_ROOT`, `PHYSICAL_REPO_ROOT`,
  `DOCKER_NETWORK_INTERNAL`, `CONTAINER_UID`, `DOCKER_GID`. Missing or empty
  required keys MUST abort the run.
- **S2.3** `PUBLIC_FQDN`, `PUBLIC_TLS_CRT_PEM`, `PUBLIC_TLS_KEY_PEM` are
  required **only** when `ciu.require_fqdn` / `ciu.require_certs` is true.
  Both flags default to **false** (resolves the v1 docs/code mismatch).
- **S2.4** When `ciu.require_certs = true`, CIU MUST validate that the files
  `PUBLIC_TLS_CRT_PEM` and `PUBLIC_TLS_KEY_PEM` **as given** exist and are
  readable by `DOCKER_GID`. CIU MUST NOT re-derive certificate paths from
  parent directories (kills the v1 `live/live` path bug by design).
- **S2.5** `0` is a valid `CONTAINER_UID`/`CONTAINER_GID`/`DOCKER_GID`.
  Numeric env values MUST be validated as integers with falsy-safe checks
  (`is None` / `== ""`, never truthiness).
- **S2.6** All `ciu.env` keys remain visible to `docker compose` `${VAR}`
  interpolation (the compose process env inherits `os.environ`, see S8.2).
- **S2.7** `ciu.env` is the **machine identity layer** — detected facts
  about this machine, not project configuration (project configuration is
  TOML; TOML may reference machine facts via `$VAR` expansion, S3.2).
  Every key is autodetected; a pre-set environment value always wins:

  | Key | Detection (when not pre-set) |
  |---|---|
  | `REPO_ROOT` | resolved per S1.1 |
  | `PHYSICAL_REPO_ROOT` | `devcontainer.local_folder` label via `docker ps`; native host: `= REPO_ROOT` |
  | `DOCKER_GID` | `stat` of `/var/run/docker-host.sock` or `/var/run/docker.sock`, else `getent group docker` |
  | `CONTAINER_UID` / `CONTAINER_GID` | current user UID / `DOCKER_GID` |
  | `DOCKER_NETWORK_INTERNAL` | `<repo-name>-<instance-id>-network` (instance-id = path hash) |
  | `ENV_TYPE` | `devcontainer` \| `native` \| `github-actions` (v1's `bare-metal` and post-create's `local` unify as `native`) |
  | `PUBLIC_IP`/`PUBLIC_FQDN`/`PUBLIC_TLS_*` | config → ipify → reverse DNS → `localhost` fallback (S2.3 gates whether required) |

- **S2.8** `ciu --generate-env` is the **single bootstrap entry point** and
  MUST perform: detect + write `ciu.env` → ensure `DOCKER_NETWORK_INTERNAL`
  exists → attach the devcontainer to it (devcontainer only; the network
  need not pre-exist the devcontainer — attachment is dynamic via the host
  daemon, so there is **no** chicken-and-egg) → TLS accessibility probe via
  one-shot container (when `PUBLIC_TLS_*` set). Environment-setup scripts
  (e.g. dstdns's `post-create.sh` / `env-workspace-setup-generate.sh`)
  SHOULD delegate to it and keep only non-CIU concerns (shell aliases, SSH
  agent, IDE settings); CIU is the sole implementation of detection
  (today's logic is duplicated across `workspace_env.py` and those scripts —
  the script copies are retired).

## S3 — Configuration model

### Files and layering

- **S3.1** File roles:
  `ciu.global.defaults.toml.j2` (committed, full defaults) +
  `ciu.global.toml.j2` (**committed sparse override**, see S3.1a; optional —
  if absent, defaults apply only) → rendered `ciu.global.toml` (gitignored);
  per stack `ciu.defaults.toml.j2` (committed, full defaults) +
  `ciu.toml.j2` (**committed sparse override**, see S3.1a; optional, **not
  auto-created** — if absent, defaults apply only) → rendered `ciu.toml`
  (gitignored). The per-stack override mirrors the global override exactly:
  CIU never copies defaults into it. (Before CIU-8, CIU auto-created
  `ciu.toml.j2` as a full copy of defaults; that generated intermediate then
  shadowed later edits to the committed defaults and survived `clean`. There is
  no generated intermediate now, so nothing can go stale.)

- **S3.1a** Override constraints — apply identically to the global override
  (`ciu.global.toml.j2`) and the per-stack override (`ciu.toml.j2`):
  1. **Secret-free**: CIU MUST scan the raw template text before rendering.
     Any PEM key/certificate block (`-----BEGIN`) or sensitive key name
     (`password`, `token`, `secret`, `api_key`, `credential`, …) paired
     with a literal string value that is not a `{{ env.VAR }}` or `$VAR`
     reference causes an immediate abort (exit 2). All sensitive values
     MUST use environment variable references.
  2. **Sparse**: SHOULD contain only keys that differ from defaults. Keys
     absent from the override fall through from defaults automatically.
  3. **Merge semantics**: override values replace scalars; tables merge
     recursively. Lists in the override replace the defaults list entirely
     (no concatenation). Key deletion is not supported — use the falsy
     equivalent (`false`, `""`, `[]`) to disable a default.
  4. **Not auto-created**: CIU never generates either override file. Create it
     manually in the repository with only the structural overrides needed; an
     absent override is the normal case (defaults apply alone). `clean`/`--reset`
     remove rendered outputs but MUST NOT remove a committed override.

- **S3.2** Render pipeline per template: Jinja2 render (context = config
  merged so far + `env` = process environment) → `$VAR`/`${VAR}` expansion
  (missing/empty value = abort, naming the variable and source file) → TOML
  parse (syntax error = abort with file and position).
- **S3.3** Merge chain: global defaults → global overrides → (for nested
  roots between repo root and the stack, nearest-last) → stack defaults →
  stack overrides. Deep merge is key-level; tables merge recursively; scalars
  and **lists replace** (no concatenation). Each directory's global config is
  processed exactly once; the chain runs from repo root down to and including
  the stack directory (fixes the v1 double-merge/leaf-skip bug).
- **S3.4** Re-rendering a stack MUST preserve exactly one section from the
  previous `ciu.toml`: the top-level `[state]` table. No other section
  survives re-render. (`[secrets.*]` persistence is withdrawn; see S4.)
  Re-rendering happens on **every run** (S8.3); `[state]` survives those.
  `--reset` deletes the rendered `ciu.toml` — and with it `[state]` — along
  with the stack's volumes (S6.4): state describes the data (e.g. Vault's
  `initialized`/`root_token`), so destroying the data MUST destroy the
  state. Secret store files follow the separate S4.25 rule.

### Stack shape

- **S3.5** A stack config MUST have **exactly one** non-reserved top-level
  key (the stack root key). The only reserved top-level key is `state`.
  Violation = abort listing the offending keys.
- **S3.6** Stack-scoped sections live **under the root key**:
  `[<root>.env]`, `[<root>.hooks]`, `[<root>.secrets]`,
  `[<root>.<service>]`, `[<root>.<service>.hostdir]`,
  `[<root>.<service>.secrets]`, `[<root>.<service>.configfile.<name>]`.
  A top-level `[env]` is invalid (v1 docs showed it; v1 code rejected it —
  the code behavior is ratified).
- **S3.7** The stack root key MUST NOT collide with a reserved global
  namespace: `ciu`, `deploy`, `topology`, `registry`, `vault`, `consul`,
  `service`, `env`, `state`, `auto_generated`, `secrets`. Collision = abort.
  (dstdns's vault stack root key `vault` collides with global `[vault.paths]`
  and must be renamed, e.g. `vault_core` — see Appendix B.2.)
- **S3.8** TOML keys use `snake_case`; hyphens in Docker names belong in
  `name` fields. The v1 directory→service auto-exposure
  (`[service.<cat>.<proj>.<svc>]` lifted to a top-level key by path
  matching) is **withdrawn**: stacks reference the global `[service.*]`
  registry directly in their TOML templates
  (`name = "{{ service.infra.redis_core.redis.name }}"`), which is what
  dstdns already does — the auto-exposure was redundant with it and
  silently no-op'd on any path mismatch.
- **S3.9** `auto_generated` (build_version, build_time, uid, gid, docker_gid)
  is computed each run and exposed to templates. Templates MUST use
  `{{ auto_generated.* }}` (not `${BUILD_VERSION}` interpolation).
- **S3.10** Hyphenated path components map to underscores for key lookup
  (v1 behavior ratified).

## S4 — Secrets

### Grammar

- **S4.1** Secret declarations are recognized **only** inside tables named
  `secrets` located under the stack root key (`[<root>.secrets]` or
  `[<root>.<service>.secrets]`). Global config MUST NOT contain `secrets`
  tables (abort if it does).
- **S4.2** Exactly six directives exist:

  | Directive | Verb semantics | Provider / locus |
  |---|---|---|
  | `ASK_VAULT:<path>[#<field>]` | must exist, read each run | Vault KV2 |
  | `GEN_TO_VAULT:<path>` | create-if-missing, then read | Vault KV2 |
  | `GEN_LOCAL:<name>` | create-if-missing | project file store |
  | `ASK_EXTERNAL:<key>` | must be supplied, then cached | operator / CI |
  | `ASK_FILE:<path>` | must exist, referenced in place | pre-provisioned file |
  | `GEN_EPHEMERAL` | new value every run | run-scoped |

  Verbs: `ASK_*` = fail-fast if the source cannot supply the value;
  `GEN_*` = generate once, idempotent thereafter; `GEN_EPHEMERAL` is the only
  always-fresh form.
- **S4.3** Withdrawn from v1: `ASK_VAULT_ONCE` (semantically identical to
  `GEN_TO_VAULT`) and `DERIVE` (composite values are served by `secret()` in
  configfile templates, S5.4; cryptographic transforms belong in hooks).
- **S4.4** A secrets-table value MUST be either a directive string or an
  inline table `{ directive = "...", ... }` with OPTIONAL keys:
  `expose_env = "<ENV_NAME>"` (S4.16), `mode = "0444"`, `uid = <int>`
  (S4.10). Any other value, or an unparseable directive, = abort.
- **S4.5** A string matching `^(ASK_VAULT|GEN_TO_VAULT|GEN_LOCAL|ASK_EXTERNAL|ASK_FILE|GEN_EPHEMERAL)\b`
  found **outside** a secrets table MUST abort (catches misplaced
  directives, e.g. dstdns's `[controller.consul].token`). No other heuristic
  (e.g. v1's `^[A-Z][A-Z0-9_]+:` regex) is applied — `LOG_LEVEL:INFO` style
  values are plain data everywhere.
- **S4.6** The TOML key is the **secret name**: pattern
  `[a-z][a-z0-9_]*`, unique across all secrets tables of one stack
  (collision = abort). The name is the compose secret name and the
  `/run/secrets/<name>` filename.
- **S4.7** One canonical parser module (`secret_directives`) defines the
  grammar; engine and deploy MUST import it. Future providers extend via
  `VERB_PROVIDER` naming (e.g. `ASK_SOPS`); generation parameters
  (`length`, `charset`) and `transform` are reserved extension points —
  parsers MUST reject them until specified.

### Resolution & materialization

- **S4.8** Default generated value: `secrets.token_urlsafe(32)`.
- **S4.9** Secret files: per-stack store `<stack>/.ciu/secrets/<name>`;
  `GEN_LOCAL` uses the **project store** `<repo-root>/.ciu/secrets/<name>`
  (its `<name>` MAY contain `/` namespacing) so unrelated stacks can share a
  generated secret without Vault. Files hold the raw value, no trailing
  newline, written atomically (`tmp` + `os.replace`).
- **S4.10** Store dirs are mode `0700`. Secret files default to mode `0440`,
  owner `CONTAINER_UID:DOCKER_GID`; per-secret `mode`/`uid` override for
  images with fixed internal UIDs. If CIU lacks privilege to chown it MUST
  emit a clear warning naming the file and required ownership, and continue
  (degraded), not crash.
- **S4.11** Idempotency: `GEN_LOCAL` — if the store file exists its content
  IS the value (the file is the persistence; no TOML state). `GEN_TO_VAULT` —
  read the Vault path; only if absent generate and write. `ASK_VAULT` — read;
  absent = abort. Re-running CIU MUST be byte-stable for all `GEN_*` secrets
  except `GEN_EPHEMERAL`.
- **S4.12** Rotation is **out of scope**: rotate in the provider (Vault),
  then redeploy. Materialized files are refreshed from the provider on every
  run; running containers see new values only on container recreate
  (bind-mounted inode semantics — this is the specified behavior).
- **S4.13** `ASK_EXTERNAL:<key>`: value from env `<key>`, else env
  `CIU_SECRET_<NAME>`, else interactive prompt; non-interactive (`-y` or no
  TTY) with no value = abort. The answer persists to the stack store file;
  subsequent runs reuse it without prompting. The v1 behavior of passing the
  literal directive string through is forbidden.
- **S4.14** `ASK_FILE:<path>`: relative paths resolve against the stack dir;
  the file MUST exist and be readable (else abort). The overlay references
  the file **in place** (no copy into `.ciu/`); repo-internal paths are
  remapped per S1.4, external absolute paths used verbatim.
- **S4.15** Vault KV2 payloads: writes store `{"value": <secret>}` only (v1
  suffix-based aliasing withdrawn). Reads: take `value` if present; else a
  single-key payload's sole value; else `#<field>` selects the key (S4.2);
  else abort listing available keys and suggesting `#<field>`.
- **S4.16** Vault address comes from `topology.services.vault` (internal
  host/port) unless overridden by an active profile's `topology_overrides`
  (S7.4). Vault token source order: `VAULT_TOKEN` env → file named by
  `vault.token_file` config → the local vault stack's `ciu.toml [state]`
  (current `vault_env_pre_hook` mechanism). No token + vault-backed
  directives present = abort before any container is started.

### Consumption

- **S4.17** CIU generates `<stack>/.ciu/ciu.compose.overlay.yml` (the
  overlay) declaring every secret of the stack:
  `secrets: { <name>: { file: <physical path> } }`, plus configfile mounts
  (S5.5). CIU runs
  `docker compose -f ciu.compose.yml -f .ciu/ciu.compose.overlay.yml ...`.
  Templates declare consumption only: `services.<svc>.secrets: [<name>]` and
  read `/run/secrets/<name>` — the *_FILE convention
  (`POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`) where the image
  supports it.
- **S4.18** Images without file support use the documented wrapper pattern:
  `entrypoint`/`command`/`healthcheck` wrapped in
  `sh -c '... "$(cat /run/secrets/<name>)" ...'` (worked example B.1).
- **S4.19** Escape hatch: a secret with `expose_env = "<ENV_NAME>"` is
  additionally injected into the **compose process env** under that name so
  `${ENV_NAME}` interpolation works. This is per-secret, opt-in, and
  discouraged; CIU MUST log a notice naming each exposed secret.
  `expose_env` is invalid on `ASK_FILE` (CIU never loads the file's content,
  so there is no value to expose) — rejected at parse time.
- **S4.20** CIU MUST warn when a declared secret is consumed by no channel,
  and abort when any channel references an undeclared secret name. Consumption
  channels are: rendered compose `services.*.secrets`, S5 configfile templates
  that call `secret('<name>')`, and explicit hook consumption marked on the
  secret declaration with `consumed_by = "hook"`.

### Leak prevention

- **S4.21** In the **compose template** render context, resolved secret
  values are replaced by guard objects: any attempt to stringify one aborts
  the run naming the secret and pointing to `secrets:`/`/run/secrets` usage.
  Configfile templates (S5.4) are the only place secret values can be
  embedded, via the explicit `secret('<name>')` function.
- **S4.22** After rendering, CIU MUST scan `ciu.compose.yml` (and the
  overlay) for every resolved secret value of length ≥ 8 and abort on a hit,
  naming the secret (never printing the value). Rendered configfiles are
  exempt from the scan but MUST be mode `0440` and mounted read-only.
- **S4.23** `--print-context` and all logs MUST render secrets as
  `<secret:<name>>`; plaintext secret values appear in no CIU output.
- **S4.24** Plaintext secrets MUST NOT be written into `ciu.toml` (v1's
  `[secrets.local]` / `[secrets.state]` are withdrawn; migration drops them).

### Lifecycle commands

- **S4.25** `ciu secrets list` prints name, directive kind, provider locator,
  store path, exists/missing — never values. `ciu secrets reset [--name X]`
  deletes store files after confirmation (`-y` skips). `ciu --reset` keeps
  secret files unless `--secrets` is also given.
- **S4.26** Per-stack runs serialize on an exclusive lock
  `<stack>/.ciu/lock`; the project secret store uses
  `<repo-root>/.ciu/lock` for `GEN_LOCAL` writes.

## S5 — Config-file mounts (own apps)

- **S5.1** Section `[<root>.<service>.configfile.<cfgname>]` with keys:
  `template` (path relative to the stack dir), `target` (absolute path in
  the container), optional `mode` (default `0440`).
- **S5.2** CIU renders the template with the merged config context to
  `<stack>/.ciu/rendered/<service>/<cfgname>` (any text format; TOML is the
  convention for own apps).
- **S5.3** The overlay (S4.17) bind-mounts the rendered file read-only at
  `target` for that service, using the physical path. If the rendered compose
  file has a service key exactly equal to the `<service>` component of the
  configfile section path, the mount attaches to that service. Otherwise, the
  section is treated as a base service selector and fans out to every rendered
  compose service key named `<service>-<positive-int>` (1-based:
  `worker-1`, `worker-2`, … as CIU emits for instance-indexed services). If
  neither form exists, CIU preserves the selector as written for compose to
  diagnose **and logs a `[WARN]`** naming the selector — the mount would
  otherwise target a phantom service that no container receives (CIU-2).
- **S5.4** Configfile templates additionally receive `secret(name) -> str`,
  valid only for names declared in the stack's secrets tables (unknown name =
  abort). This is the sanctioned home for composite values (DSNs, URLs
  embedding credentials) — see worked example B.3.
- **S5.5** Container env SHOULD then carry only bootstrap pointers: the
  config file path (e.g. `APP_CONFIG=/etc/app/config.toml`), early log
  level, TZ — per the boundary rule "env = pointers, files = config,
  secrets = files".
- **S5.6** v1's unused `SERVICE_CONFIG_DEFAULTS`/`SERVICE_CONFIG_ACTIVE`
  constants are withdrawn.

## S5a — Dev-loop profile (`ciu dev`)

`ciu bake` builds the **production** image; some stacks also need an iterative
**dev loop** that a production build does not model — a hot-reload server
(Vite/Next/`uvicorn --reload`) and/or a contract-coupled pre-build chain that
depends on a *live* service (e.g. fetch a running backend's OpenAPI → codegen
types → start the dev server). S5a declares that loop declaratively and
build-tool-agnostically; CIU carries no npm/Vite/uvicorn specifics (CIU-5).

- **S5a.1** A stack MAY declare `[<root>.dev]` with keys: `command` (required —
  the long-running dev-server command); one of `image` (a base image) **or**
  `build` (a `{context, dockerfile, target, tag}` table); optional `prebuild`
  (ordered list of shell commands run before `command`, aborting on the first
  failure); `port` (int, `"host:container"` string, or list — published ports);
  `mount` (list of `docker -v` specs — source bind + anonymous volumes);
  `depends_on` (list of service names gated on health before prebuild, reusing
  the S9.3 readiness probe); `workdir` (default `/app`); `env` (table);
  `network` (defaults to the stack's `deploy.network_name`).
- **S5a.2** `ciu dev <stack>` renders the stack config (S3), validates the
  profile (shape errors abort with `[S5a]`, exit 2), waits for each `depends_on`
  service to become healthy (exit 1 on timeout), resolves the image (uses
  `image` or builds from `build`), then runs prebuild steps and `command` in a
  **single** ephemeral `--rm` container with the source bind-mounted and `port`
  published — `sh -c '<prebuild…> && exec <command>'`, so generated files land
  in the served tree and a failed prebuild never starts the server.
- **S5a.3** `--no-prebuild` re-runs only the dev server (skips prebuild);
  `--profile` selects the host profile for rendering; `--define-root` overrides
  the repo root. The verb is for the local dev loop only — it is **not** part of
  the `up`/`down`/`clean` lifecycle and creates no rendered/overlay artifacts.

## S6 — Hostdirs & permissions

- **S6.1** `[<root>.<service>.hostdir]` keys name purposes. A value is
  either a path string — empty auto-generates
  `<stack>/vol-<service-name>-<purpose>`, non-empty used as given (absolute
  allowed) — or an inline table
  `{ path = "", uid = <int>, gid = <int>, mode = "0770", seed = "<dir>" }`
  overriding the S6.3 defaults per directory.
- **S6.2** After merge, every hostdir value exposed to templates is an
  **absolute physical path** (S1.4) — templates emit it directly as the bind
  source; the v1 relative `./vol-*` emission is withdrawn (unifies the path
  model with secrets/configfiles under DooD).
- **S6.3** CIU pre-creates hostdirs mode `0775`, owner
  `CONTAINER_UID:DOCKER_GID`; UID/GID `0` is valid (S2.5). Pre-existing dirs
  with compatible ownership/permissions pass; incompatible = abort with the
  observed owner/group/mode.
- **S6.4** `--reset` removes `vol-*` dirs **of the stack directory** (resolved
  against the stack dir, never the process cwd), rendered outputs, and
  stack containers; orphan cleanup uses the anchored label filter
  `<prefix>.component=<service>`. A `vol-*` removal the operator lacks
  privilege for — an image-UID-owned subtree from S6.7 Pattern (a), e.g.
  postgres/pgAdmin data — MUST degrade to the S6.5 root helper container so the
  wipe completes; it MUST NOT abort on `Permission denied` and leave data
  un-wiped (the daemon is root even when the operator is not).

  **Teardown completeness (CIU-3).** Teardown MUST be exhaustive — a partial
  "clean" that leaves persisted state behind silently desynchronises a
  disposable-greenfield rebuild (a stale Vault token vs a freshly-bootstrapped
  Consul, a stale Postgres role vs a regenerated password). Therefore:
  1. `docker compose down` runs with **`-v --remove-orphans`** so one-shot
     init/sidecar containers (e.g. `*-vault-init`, `Exited (0)`) declared in the
     project but outside the current selection are removed — an exited sidecar
     otherwise pins the project's named volumes through teardown.
  2. The project container sweep MUST include **exited** containers
     (`docker ps -a`), not running only; an exited container is invisible to a
     plain `docker ps` yet still pins volumes. (`--stop` keeps running-only.)
  3. **Post-clean invariant (normative):** after `clean` completes, **zero**
     project-labelled containers (any state) **and zero** project-prefixed
     named volumes remain. A surviving volume is an **error**, not a warning
     (it almost always means a container still references it); `clean` exits
     non-zero and names the survivors and the likely cause.
- **S6.5** Ownership/permission operations (chown/chmod on hostdirs, secret
  files) run directly when the CIU process has the privilege; otherwise CIU
  MUST perform them automatically via a one-shot helper container
  (`docker run --rm -v <physical-dir>:/t alpine chown/chmod ...`) — the
  daemon has root even when the operator does not. This replaces the v1
  per-stack chown-init-container pattern; stacks SHOULD NOT carry init
  containers for ownership fixes.
- **S6.6** `seed = "<dir>"` (relative to the stack dir): on **first
  creation only**, the directory tree is copied into the hostdir and given
  the hostdir's ownership (via S6.5 when needed). Pre-existing hostdirs are
  never re-seeded — provisioning initial content (config trees, bootstrap
  data) without an init container.
- **S6.7** *Storage patterns (informative).* Images that demand a fixed
  internal UID and exclusive data ownership (postgres/timescaledb, uid 999):
  **(a)** hostdir with `{ uid = 999, gid = <DOCKER_GID>, mode = "0770" }` —
  the service owns the data while the operator keeps access through the
  docker group (the v1 chown-to-service-uid approach locked the operator
  out); CIU provisions it via S6.5. **(b)** A compose **named volume**
  authored in the template when host visibility is not needed — the image's
  entrypoint initializes ownership itself; CIU does not manage named-volume
  contents (created by compose, removed by `--reset`'s `down -v`).
  Decision rule: need to inspect/back up files from the host → (a);
  otherwise prefer (b).

## S7 — Orchestration (`ciu-deploy`)

### Phases

- **S7.1** Phase tables MUST be named `phase_<uint>` and are executed in
  **numeric** order. Any other key under `[deploy.phases]`, or non-string
  group/profile entries, = abort at validation (kills the v1 lexicographic
  sort and `.startswith` int crash by design).
- **S7.2** `services = [{ path, name, enabled, profiles?, env_overrides? }]`.
  `enabled` MUST be a boolean or the **name** of a flag in
  `[deploy.control]` (string). Unknown flag name = abort. Expressions are
  forbidden (v1 `eval()` is withdrawn).
- **S7.3** A failed stack start (non-zero compose exit, timeout, missing
  dir) MUST mark the phase failed: remaining services in that phase and all
  later phases are skipped, exit code 1, and the summary lists
  deployed/failed/skipped. With `--ignore-errors` execution continues but the
  final exit code MUST still be 1. Helper `error()` MUST NOT terminate the
  process from within actions (single exit point at the CLI layer).

### Host profiles

- **S7.4** `[deploy.profiles.<name>]`: `phases = ["phase_1", ...]` and/or
  `stacks = ["infra/redis-core", ...]`; optional
  `compose_profiles = [...]` (joined into `COMPOSE_PROFILES`),
  `env_overrides = { K = "V" }`, and `[deploy.profiles.<name>.topology_overrides]`
  deep-merged over `topology.*` while the profile is active (cross-host
  addressing: host B's profile points `topology.services.vault` at host A's
  external address).
- **S7.5** CLI: `ciu-deploy --profile <name>` (repeatable; comma form also
  accepted: `--profile core,db`); default from `CIU_SERVICES_PROFILE` in
  `ciu.env` (comma-separated ordered list, e.g. `core,db,worker-io`).
  `CIU_HOST_PROFILE` is **retired** (not aliased): if set, CIU MUST emit a
  deprecation error to stderr and exit 2 — it is never used as a fallback.
  `[deploy.groups]` and `--groups` do **not** exist in v3 (greenfield — no
  aliases, no fallbacks); the validator rejects `[deploy.groups]` with a
  pointer to profiles.
  **Composition rules (Seam 4):**
  - **Union, order-preserving, deduped:** `phases`, `stacks`, and
    `compose_profiles` from all selected profiles are unioned preserving
    first-seen order and deduplicating repeats. Phase execution order
    remains numeric (S7.1 `ordered_phases`).
  - **Override merge + conflict:** `env_overrides` and `topology_overrides`
    from all selected profiles are deep-merged in list order. If two profiles
    set the same key to **different** values → CIU MUST fail before any
    render or Docker mutation with exit code 2, naming the key and both
    conflicting profiles. Equal repeated values are accepted silently.
  - **CLI precedence:** if any `--profile` is given on the CLI, the CLI
    list **fully overrides** the env list (they are NOT merged).
- **S7.5a** *Multi-host workflow.* Each host carries a clone of the project,
  its own generated `ciu.env` (machine identity, S2.7), and a
  `CIU_SERVICES_PROFILE` (ordered list). The admin orders execution
  manually across hosts (e.g. `--profile core,db` on host A **first**, then
  `--profile worker-io` on host B whose `topology_overrides` points
  Vault/Postgres/Redis addresses at host A's externally reachable
  endpoints). Cross-host reachability (published ports, VPN/tailnet) is the
  operator's responsibility; CIU's S7.6 validation tells host B *before
  starting anything* whether its Vault address+token resolve. "Service
  profile" (`deploy.profiles`, which stacks run here) and compose `profiles`
  (`compose_profiles`, which services inside a stack are activated) are
  distinct concepts and MUST be documented side-by-side.
- **S7.5b** *Dynamic per-instance configfile selector.* A configfile section
  (under `[<root>.<service>.configfile.<name>]`) MAY declare
  `instances = N` (positive integer). When present, `render_configfiles`
  emits *N* rendered files and mounts (one per 1-based index). Each render
  context additionally exposes `instance_index` (1-based int) and
  `instance_id` (`"<service>-<index>"`). Single-instance configfiles (no
  `instances` key, or `instances = 1`) behave identically to before.
- **S7.6** Validation: if the active selection includes stacks with
  `*_VAULT` directives, the vault stack MUST be in an earlier phase of the
  same selection **or** a Vault token/address MUST resolve via S4.16 —
  checked before any phase runs.

### Health & readiness

- **S7.7** The health gate passes only when every checked service reports
  `healthy`. `starting`/pending counts as **not passed**; the gate polls
  until `--health-timeout` then fails (exit 1). Services without a
  healthcheck are reported as `no-healthcheck` (warning), not as passing
  silently.
- **S7.8** Container lookups MUST use anchored name/label filters
  (`^<project>-<env>-<name>$` or label equality), never substring matches.

### Registry

- **S7.9** When `deploy.registry.url` is set, CIU MUST verify that
  credentials for that registry exist (Docker config `auths`/`credHelpers`
  lookup); v1's `docker login --get-credentials` invocation is withdrawn.
  Verification failure aborts before compose runs.

## S8 — Compose execution

- **S8.1** Per stack, the compose invocation is
  `docker compose -f ciu.compose.yml -f .ciu/ciu.compose.overlay.yml up -d`
  (the overlay is omitted only when the stack declares no secrets and no
  configfiles).

  *Why a separate overlay instead of injecting into the rendered
  `ciu.compose.yml` (informative):* both files are generated, but by
  different authors. `ciu.compose.yml` is the byte-exact output of the
  **stack author's** template — when it is wrong, the template is wrong;
  nothing else touched it. The overlay is **machine-derived wiring**
  (secret-store and configfile paths that embed `PHYSICAL_REPO_ROOT`, i.e.
  facts CIU detects, not facts authors write). Injecting that wiring into
  the rendered file would require parsing and mutating the template's YAML
  output — destroying anchors/aliases (`x-defaults: &service-defaults`),
  comments, and ordering, and blurring the template-bug/CIU-bug boundary —
  and templates would need a correct hand-written DooD path remap in every
  stack otherwise. The `-f base -f overlay` merge is compose's native
  mechanism; the overlay is also the single file a security review reads to
  see every secret exposure of a stack.
- **S8.2** The compose process environment is exactly: `os.environ`
  (which includes the sourced `ciu.env`) + `PWD` + `COMPOSE_PROFILES`
  (when set by profile/service) + `expose_env` secrets (S4.19).
  **TOML config flattening into env is withdrawn** — `flatten_dict` /
  `ENV_<KEY>` / `UPPER_SNAKE` placeholders no longer exist. All non-secret
  values reach the compose file via Jinja2 at render time.
- **S8.3** Pipeline order per stack:

  1. load env (S2) → 2. render global chain → 3. render stack → 4. merge →
  5. validate (S11) → 6. optional reset → 7. auto-generate →
  8. hostdirs (S6, incl. seed) → 9. **`pre_secrets` hooks** →
  10. resolve + materialize secrets (S4) → 11. **`pre_compose` hooks** →
  12. render configfiles (S5) → 13. render compose template →
  14. leak scan (S4.22) → 15. generate overlay → 16. compose up →
  17. **`post_compose` hooks**.

  Rationale for two pre hook points: `pre_secrets` runs **before** secret
  resolution for provider bootstrap (unsealing Vault, fetching dynamic
  provider credentials); `pre_compose` runs **after** materialization so a
  hook that needs a secret value reads its store file (S9.3) — v1's single
  pre-compose point could not serve both needs. Hooks precede configfile
  rendering so `apply_to_config` updates are visible to configfile
  templates. `--render-toml` stops after step 3; `--dry-run` stops before
  step 16 (everything else runs, including the leak scan).

  Step 17 runs **immediately** after `compose up` (step 16) — CIU does not
  implicitly block the whole step on a global health gate. A service-touching
  `post_compose` hook owns its own readiness wait using the helpers CIU
  provides on the context (S9.3): `ctx.wait_healthy(<service>)` /
  `ctx.wait_tcp(<host>, <port>)`. This avoids every hook re-implementing a poll
  loop while keeping CIU agnostic about which services a given hook touches
  (CIU-4).
- **S8.4** On any abort, CIU restores the process working directory and does
  not leave partial overlay/configfile artifacts referenced by a previous
  successful overlay (atomic replace per file).

### Dual shipping

- **S8.5** CIU's rendered compose output is `ciu.compose.yml` at the stack
  root (rendered from `ciu.compose.yml.j2`, gitignored). A maintainer MAY
  additionally commit a hand-written `docker-compose.yml` in the same stack
  directory for a plain `docker compose up` path; CIU MUST NOT render to,
  rename, or otherwise overwrite that file. `--reset` (S6.4) removes
  `ciu.compose.yml` and the overlay but MUST NOT remove a hand-written
  `docker-compose.yml`. This lets a project offer two deploy paths
  side-by-side: the CIU-managed path (`ciu.compose.yml` + overlay, with
  secrets/configfiles/hostdirs) and the plain path (`docker-compose.yml`).
- **S8.6** *Shipped-compose passthrough.* `ciu --shipped` runs a maintainer's
  pre-shipped compose (default `docker-compose.yml`; override with `-f`)
  **through** CIU without requiring a stack config (`ciu.defaults.toml.j2`)
  and without the secret / overlay / configfile steps. It MUST still:
  load `ciu.env` (S2), render the global chain for the `auto_connect_network`
  setting, ensure/attach the workspace network (S2.8), run the DooD preflight
  (S1.5), then `docker compose -f <file> up -d` with the same cwd/project
  convention as the native path. The compose process env is S8.2 minus
  `expose_env` secrets (none are resolved). `--dry-run` stops before the
  compose up. `ciu-deploy` exposes the same path per service via a boolean
  `shipped` key in `[deploy.phases.*].services` (default `false`; non-bool =
  abort, S7.2); a `shipped` service participates in phases and the health
  gate exactly like a native stack.

## S9 — Hooks

- **S9.1** Three hook points exist under `[<root>.hooks]`: `pre_secrets`,
  `pre_compose`, `post_compose` (lists of script paths relative to the
  stack dir; see S8.3 for placement and rationale). A module provides
  `run(config, ctx) -> dict` (function) or a `Hook` class with that `run`;
  v1's per-point function/class names are withdrawn.
- **S9.2** A listed hook file that does not exist MUST abort (v1 warned and
  continued — withdrawn).
- **S9.3** Hooks receive the merged config with secret guard objects (S4.21).
  A `pre_compose`/`post_compose` hook needing a secret value reads the
  store file (`ctx.secret_file(name)`) or `/run/secrets` inside a
  container; `pre_secrets` hooks run before values exist by definition.
  The context additionally provides two **readiness helpers** (CIU-4) so a
  `post_compose` hook can wait for a service it touches instead of racing
  startup:
  - `ctx.wait_healthy(service, *, timeout_s=120.0) -> bool` — resolve *service*
    to its project-scoped container (`<project>-<env>-<service>`) and poll its
    Docker health (via `classify`) until `healthy`/`no-healthcheck`, returning
    `True`, or `False` on timeout. `no-healthcheck` counts as ready (nothing to
    wait on).
  - `ctx.wait_tcp(host, port, *, timeout_s=30.0) -> bool` — dependency-free port
    probe for images that expose no Docker healthcheck; `True` on first
    successful connect, `False` on timeout.
  Both are wired by the engine; a hook MUST NOT hand-roll a poll loop where a
  helper suffices.
- **S9.4** Return contract — structured form **only**:
  `{ "<dotted.path>": { "value": ..., "apply_to_config": bool, "persist": "state" } }`.
  `apply_to_config` mutates the in-memory merged config (visible to later
  hooks, configfiles, the compose template); `persist: "state"` additionally
  writes the value under the stack's `[state]` (the only persistable
  destination). v1's plain `{KEY: value}` env-update form is **withdrawn**:
  hooks MUST NOT mutate the process environment, and CIU applies no env
  updates from hook returns (hook→pipeline communication goes through
  config/state; the v1 `VAULT_TOKEN`-export hook is superseded by the
  S4.16 built-in token source order).

## S10 — CLI surface (delta to v1)

- **S10.1** `ciu`: unchanged flags `-d/-f/-y/--dry-run/--print-context/
  --render-toml/--define-root/--root-folder/--skip-hostdir-check/
  --skip-hooks/--skip-secrets/--generate-env/--update-cert-permission/
  --version/--reset`; new `--secrets` (with `--reset`, S4.25), new
  `--shipped` (S8.6 — run the pre-shipped `docker-compose.yml`; `-f`
  overrides the file name), and subcommand `ciu secrets list|reset` (S4.25).
  `--skip-secrets` skips materialization and overlay generation (compose will
  fail if the template consumes secrets — cleanup-mode only). `-f` defaults to
  `ciu.compose.yml.j2`; the rendered output is `ciu.compose.yml` (S8.5).
- **S10.2** `ciu-deploy`: new `--profile <name>` (S7.5); `--groups` removed
  (S7.5, greenfield); per-service `shipped = true` (S8.6) routes a stack
  through its pre-shipped `docker-compose.yml`; all other v1 actions retained.
- **S10.3** Exit codes: `0` success · `1` runtime failure (compose, health,
  hooks, vault I/O) · `2` configuration/validation error (S3/S4/S7 static
  checks, argparse) · `3` environment/bootstrap error (S1/S2: missing env
  keys, DooD preflight, dependencies).
- **S10.4** v3 flat verb CLI (`ciu <verb> …`): each verb's `-h`/`--help` MUST
  print that verb's **own** synopsis and options, never the legacy `ciu-deploy`
  argparse surface (which still exposes withdrawn flags such as
  `--deploy`/`--stop`). Help is verb-scoped (CIU-7). Verbs: `env`, `render`,
  `profiles`, `up`, `down`, `clean`, `health`, `bake`, `dev` (S5a), `secrets`,
  `check` (S13), `graph` (S13), `ssh` (S14). The global modifier `--host <name>`
  (S14) is accepted on `up`, `down`, `health`, and `render`; `--thin` is reserved
  on `up --host` (not yet implemented, exits 1 with a clear message).
  A sub-subcommand with its own parser (`env generate`) keeps its argparse help.

## S11 — Validation catalog (static, pre-execution)

Checked after merge, before reset/hostdirs/hooks: S3.5 single root key ·
S3.7 namespace collision · S4.1/S4.5 directive placement · S4.4 directive
shape · S4.6 name uniqueness/pattern · S4.20 declared-vs-consumed ·
S5.4 unknown `secret()` name · S6.1 hostdir value shape · S7.1 phase
naming · S7.2 enabled flags + `shipped` bool (S8.6) ·
S7.5 `[deploy.groups]` rejection · S7.6 vault ordering · S2.2/S2.3 env keys ·
S1.7 gitignore (incl. the auto-created override templates `ciu.toml.j2` /
`ciu.global.toml.j2`). Each failure reports the spec ID it enforces.

## S12 — Extension points (reserved, not implemented)

Generation parameters (`length`, `charset`), `transform`, additional secret
providers (`ASK_SOPS`, `ASK_AWS`, ...), per-profile compose-file additions.
Parsers reject unknown options today (S4.7).

## S13 — Provisioning model (`requires` / `provides`)

Stacks MAY declare dependency relationships declaratively so CIU can validate
them before deploying. This feature is **opt-in and purely additive**: a stack
that declares neither `requires` nor `provides` behaves exactly as before.

### S13.1 — Declaration

`requires` and `provides` are typed-reference lists declared **inside the
stack's root-key table** (e.g. `[db_core]`, `[authentik]`) — NOT inside a
`[stack]` table (which CIU does not read for this purpose). The deploy and check
paths read `root_section.get("requires")` / `root_section.get("provides")` where
`root_section` is the stack's single non-reserved top-level key.

```toml
# infra/db-core/ciu.defaults.toml.j2
[db_core]
provides = [
  "pg:db/dstdns",
  "pg:role/controller",
  "pg:schema/controller",
  "minio:user/worker-io",
  "vault:secret/db/postgres/controller_password",
]

# infra/authentik/ciu.defaults.toml.j2
[authentik]
requires = [
  "pg:role/authentik",
  "vault:secret/db/postgres/authentik_password",
  "stack:db-init:healthy",
]
```

### S13.2 — Typed-reference grammar

Each entry MUST match one of these forms (validated by `config_model._REF_RE`
and `provisioning.parse_ref`):

| Ref | Means | Live probe |
|---|---|---|
| `vault:secret/<path>` | KV-v2 secret exists at that path | Vault `read` |
| `pg:role/<name>` | Postgres login role exists | `psql` → `pg_roles` (default `postgres` db) |
| `pg:db/<name>` | Postgres database exists | `psql` → `pg_database` |
| `pg:schema/<name>` | Schema exists in the **application** database | `psql -d <registry.postgresql.database>` → `information_schema.schemata` |
| `minio:user/<name>` | MinIO service account exists | `mc admin user info local <name>` |
| `consul:token/<svc>` | Consul ACL token exists in Vault | Vault read at `registry.consul.token_vault_path` (default `consul/acl/tokens/{svc}`; override via `[registry.consul] token_vault_path = "…"`) |
| `stack:<name>:healthy` | Another container is up+healthy | `docker inspect .State` |

**`pg:schema` note.** `information_schema.schemata` is per-database, not
cluster-global. CIU therefore connects with `psql -d <db>` where `<db>` comes
from `registry.postgresql.database`. The default-database probe used for
`pg:role` and `pg:db` (the `postgres` db) would never see application schemas.

**`consul:token` Vault path.** The path is config-driven. Default:
`consul/acl/tokens/{svc}` (e.g. `consul:token/myapp` → `consul/acl/tokens/myapp`).
Override in the global config:

```toml
[registry.consul]
token_vault_path = "consul/{svc}/token"   # e.g. stores at consul/myapp/token
```

**`stack:<name>:healthy` one-shot support.** A container without a Docker
healthcheck is satisfied when it is *running*. A one-shot container (e.g. a
`db-init` / `controller_ddl` init-container) that has **exited 0** is also
treated as satisfied — the probe reads `State.ExitCode == 0` as a clean
completion. Only a non-zero exit code or a container not found is a failure.

### S13.3 — Preflight model (lint-vs-probe split)

Two independent checks run at different times:

1. **Static lint** (`lint=True, probe=False`) — runs **once up-front** for
   the full selection, before any phase starts. Checks: every `requires` entry
   is provided by some stack in the selection; no dependency cycle among
   `stack:<name>:healthy` references. This is a pure config check — no Docker
   or Vault I/O. Exit 2 on failure.

2. **Live probe** (`lint=False, probe=True`) — runs **per-phase**, immediately
   before that phase deploys, after all earlier phases are already up. CIU
   probes only the `requires` of stacks in the current phase. This means on a
   greenfield `ciu up`, providers from phase 1 are running before phase 2's
   requirements are probed — no `--no-preflight` needed.

Both checks are skipped under `--dry-run` (nothing is running to probe) and
under `--no-preflight` (break-glass flag). If the full run is `--no-preflight`,
both checks are bypassed entirely.

- **S13.4** `ciu check [--profile NAME] [--live]` — validates the graph
  without deploying. Without `--live`: runs only the static lint. With `--live`:
  additionally probes live state for each `requires` entry. Exit code: `0`
  clean · `1` live probe failure · `2` graph lint error. Safe to run in CI
  against a running stack.

- **S13.5** `ciu graph [--format mermaid|dot|json] [--profile NAME] [--phases N,M]`
  — renders the requires/provides dependency graph to STDOUT (no deploy). Edges
  go consumer → provider (the stack whose `provides` contains the ref). A
  requirement that nobody provides is drawn dashed to an `UNPROVIDED` sentinel so
  gaps are visually obvious. Diagnostics go to the logger (stderr); only the
  graph itself goes to stdout so it can be piped directly into documentation.

## S14 — Remote SSH transport (`ciu ssh` / `--host`)

CIU provides an **optional SSH transport** for two complementary surfaces:
an operator/agent **access plane** (`ciu ssh`) and a **push-deploy** mode
(`ciu up/down/health/render --host`). The transport lives in the `ciu` package
so every consuming repo gets it identically; each repo supplies only its own
host inventory. SSH is a **bootstrap and repair** path; the pull-based
convergence model (SPEC G/H) remains the steady-state loop.

### S14.1 — `ciu ssh <host> [--admin] [-- <cmd...>]`

Open an interactive shell or run a one-shot command on a remote host:

```bash
ciu ssh core1                          # interactive shell (allocates a PTY)
ciu ssh core1 -- docker ps             # one-shot; output streamed; exit code propagated
ciu ssh core1 -- ciu up --dir infra/redis-core
```

`--admin` merges the `[deploy.hosts.<name>.admin]` subtable (higher-privilege
key/user) over the base host config before connecting.

### S14.2 — `ciu up --host <name>` (push-deploy, render-on-target)

Push-deploys a stack from the control host to a remote target using a
**render-on-target** strategy:

1. **Bundle-sync** — `rsync` the repo tree to the host's `bundle_dir`
   (e.g. `/opt/<project>/current`).
2. **Remote render + run** — over SSH: `cd <bundle_dir> && ciu env generate && ciu render && ciu up`.

Secrets resolve **on the target**, so no resolved secret value ever transits the
control host or the wire. The same verb accepts all normal selection flags after
the host option:

```bash
ciu up   --host core1 --profile infra
ciu up   --host core1 --dir infra/db-core
ciu down --host core1 --profile apps
ciu health --host core1
ciu render --host core1
```

`--thin` is reserved for a future render-on-control/ship-rendered path and is
**not yet implemented** — it exits 1 with a clear error message.

### S14.3 — Host inventory

Host inventory lives in a **render-safe file** — never touched by `ciu render`
or `ciu clean`. Lookup precedence (first found wins):

1. `$CIU_HOSTS_FILE` environment variable
2. `<repo>/.ciu.hosts.toml` (gitignored)
3. `~/.ciu/hosts.toml` (user-global)

Table form `[deploy.hosts.<name>]` (top-level `[hosts.<name>]` is also
accepted for the user-global file). Keys:

| Key | Required | Description |
|---|---|---|
| `ssh_host` | Yes | Hostname, IP, or Tailscale MagicDNS name |
| `ssh_user` | No | Remote user (default `root`) |
| `ssh_port` | No | Port (default `22`) |
| `ssh_key` | Yes | Filesystem path OR `ASK_VAULT:<path>[#field]` — never committed |
| `known_host` | Yes* | Pinned host public key (e.g. `ssh-ed25519 AAAA…`) |
| `bundle_dir` | No | Remote path for bundle-sync (default `/opt/ciu/current`) |

`[deploy.hosts.<name>.admin]` subtable overrides `ssh_user` / `ssh_key` for the
higher-privilege access plane (`ciu ssh <host> --admin`).

### S14.4 — Security requirements

- **S14.4a** Host-key pinning is **fail-closed**: a connection (including
  `rsync`) is **refused** when no `known_host` is pinned, unless
  `CIU_SSH_INSECURE_TOFU=1` is set in the environment. This flag is a
  documented bootstrap-only escape hatch and MUST NOT be set in automation.
- **S14.4b** Key material is never logged. CIU logs only key paths (never
  key content or resolved secrets). Vault-resolved keys are written to a
  mode-`0600` temp file and deleted in a `finally` block.
- **S14.4c** For non-default ports, the `known_host` entry MUST use the
  `[host]:port` form (e.g. `[core1.example.com]:2222 ssh-ed25519 AAAA…`),
  matching OpenSSH's known-hosts format. CIU constructs this automatically when
  writing the temp known-hosts file.

### S14.5 — Packaging

paramiko is an **optional dependency**: `pip install ciu[ssh]` (pulls
`paramiko>=5.0` → `cryptography`). The default transport uses subprocess
`ssh`/`rsync` (zero added Python dependencies; requires `openssh-client` on
the host). `import ciu` works with paramiko absent — the subprocess transport
is the fallback. Set `CIU_SSH_TRANSPORT=paramiko` to force paramiko when it is
installed.

---

## Appendix A — v1 findings disposition

**Dies by design** (no code fix needed beyond implementing v2):

| # | v1 finding | Killed by |
|---|---|---|
| A1 | GEN_LOCAL regenerates every run (state not preserved, wrapped shape) | S4.9/S4.11 — file is the persistence |
| A2 | `flatten_dict` mangles any `env`-named table (`deploy.env` → dict-repr) | S8.2 — flatten withdrawn |
| A3 | `[env]` list values become Python reprs; comma-join doesn't round-trip | S8.2 — flatten withdrawn |
| A4 | `^[A-Z][A-Z0-9_]+:` false-positives (`LOG_LEVEL:INFO` aborts) | S4.1/S4.5 — exact prefixes, table-scoped |
| A5 | ASK_EXTERNAL/DERIVE pass literal directive string into env | S4.13 (fail-fast), S4.3 (DERIVE withdrawn) |
| A6 | Secret state keyed by last path segment collides | S4.6 — name-keyed, uniqueness enforced |
| A7 | Lexicographic phase sort (`phase_10` < `phase_2`) | S7.1 — numeric order mandated |
| A8 | Int phase keys crash `.startswith` | S7.1 — string `phase_<uint>` validated |
| A9 | `ASK_VAULT_ONCE` ambiguity | S4.3 — withdrawn |
| A10 | `require_certs` doubles `live/` → can never pass | S2.4 — validate given paths directly |
| A11 | Secrets leak into `ciu.toml` / print-context | S4.23/S4.24 |
| A12 | docs/code/example `[env]` placement contradiction | S3.6 |
| A13 | Single-root-key vs multi-service tension | S3.5/S3.6 — multi-service via `[<root>.<service>]`, ratified |
| A14 | Vault payload alias keys break round-trip | S4.15 — `{"value"}` only + `#field` |

**Direct fixes** (Stage 2, each with a regression test naming the spec ID):

| # | v1 finding | Spec anchor |
|---|---|---|
| B1 | `execute_deployment_phase` ignores `start_stack` failure | S7.3 |
| B2 | Health gate ignores `pending` | S7.7 |
| B3 | `eval()` on `enabled`, NameError silently disables | S7.2 |
| B4 | `stop_deployment` NameError (`infra_containers`) | S7.3 (summary path) |
| B5 | `extract_vault_value` rejects external multi-key payloads | S4.15 |
| B6 | `docker login --get-credentials` (nonexistent flag) | S7.9 |
| B7 | `container_gid or docker_gid` falsy-replaces GID 0 | S2.5/S6.3 |
| B8 | `error()` `sys.exit` defeats `--ignore-errors` | S7.3 |
| B9 | Unanchored vault container filter | S7.8 |
| B10 | cwd/`os.environ` leaks on error paths | S8.4 |
| B11 | Global config double-merge / leaf-dir skip in chain | S3.3 |
| B12 | Hook-file-missing only warns | S9.2 |
| B13 | Vault addr/token recomputed per loop iteration; global re-rendered N+2× | (architecture, Stage 1) |
| B14 | `reset_service` globs cwd instead of stack dir | S6.4 |

## Appendix B — Worked examples (hand-converted from dstdns)

### B.1 — `infra/redis-core` (third-party image, no `*_FILE` support)

`ciu.defaults.toml.j2`:

```toml
[redis_core]
stack_name = "redis-core"

[redis_core.redis]
name = "{{ service.infra.redis_core.redis.name }}"
internal_port = {{ service.infra.redis_core.redis.internal_port }}
image_name = "{{ service.infra.redis_core.redis.image_name }}"
image_tag = "{{ service.infra.redis_core.redis.image_tag }}"

[redis_core.redis.hostdir]
data = ""

[redis_core.secrets]
redis_password = "GEN_TO_VAULT:{{ vault.paths.redis_password }}"

[redis_core.hooks]
post_compose = ["./post_compose_redis.py"]
# v1's vault_env_pre_hook.py is gone: the Vault token resolves via the
# built-in source order (S4.16), no env-exporting hook needed.
```

`ciu.compose.yml.j2` (delta to v1 — wrapper pattern per S4.18; `${REDIS_CORE_SECRETS_REDIS_PASSWORD}` placeholders are gone):

```yaml
services:
  {{ redis_core.redis.name }}:
    image: {{ redis_core.redis.image_name }}:{{ redis_core.redis.image_tag }}
    secrets: [redis_password]
    entrypoint: ["sh", "-c"]
    command:
      - >
        exec redis-server
        --requirepass "$(cat /run/secrets/redis_password)"
        --appendonly yes --appendfsync everysec
    healthcheck:
      test: ["CMD", "sh", "-c",
             "redis-cli -a \"$(cat /run/secrets/redis_password)\" ping | grep PONG"]
    volumes:
      - {{ redis_core.redis.hostdir.data }}:/data   # absolute physical path (S6.2)
```

CIU generates `.ciu/ciu.compose.overlay.yml`:

```yaml
secrets:
  redis_password:
    file: /host/path/dstdns/infra/redis-core/.ciu/secrets/redis_password
```

### B.2 — `infra/vault` (bootstrap stack; root key renamed per S3.7)

```toml
[vault_core]                       # was [vault] — collided with global [vault.paths]
stack_name = "vault"

[vault_core.server]
name = "{{ service.infra.vault.vault.name }}"
internal_port = {{ service.infra.vault.vault.internal_port }}
image_name = "{{ service.infra.vault.vault.image_name }}"
image_tag = "{{ service.infra.vault.vault.image_tag }}"

[vault_core.hooks]
post_compose = ["./post_compose_vault.py"]

[state]                            # written by post_compose_vault.py (S9.1)
initialized = false
root_token = ""
unseal_key = ""
```

Bootstrap rules exercised: the vault stack itself declares **no**
`*_VAULT` directives (S7.6); after `post_compose_vault.py` persists
`root_token` into `[state]` (via the S9.4 `persist: "state"` return), later
stacks resolve their token through the S4.16 source order — the v1
`vault_env_pre_hook` env juggling has no v2 equivalent and is deleted.
Resetting this stack (`--reset`) destroys the Vault data volume **and**
its `[state]` together (S3.4): the next run re-initializes Vault and
post_compose writes fresh state.

### B.3 — `applications/controller` (own app, mounted TOML config + DSN)

`ciu.defaults.toml.j2` (delta: consul token moved into the secrets table per
S4.5; configfile section added per S5):

```toml
[controller]
name = "{{ service.applications.controller.controller.name }}"
# ... image/name/port keys unchanged ...

[controller.secrets]
redis_password = "ASK_VAULT:{{ vault.paths.redis_password }}"
postgres_password = "ASK_VAULT:{{ vault.paths.postgres_controller_password }}"
bootstrap_token = "GEN_TO_VAULT:{{ vault.paths.controller_bootstrap_token }}"
consul_token = "GEN_TO_VAULT:{{ vault.paths.consul_controller_token }}"   # was [controller.consul].token

[controller.controller.configfile.app]
template = "config.toml.j2"
target = "/etc/controller/config.toml"
```

`config.toml.j2` (configfile template — the only place `secret()` exists, S5.4):

```toml
[database]
dsn = "postgresql+asyncpg://{{ controller.database.user }}:{{ secret('postgres_password') }}@{{ controller.database.host }}:{{ controller.database.port }}/{{ controller.database.database }}"

[redis]
url = "redis://:{{ secret('redis_password') }}@{{ controller.redis.host }}:{{ controller.redis.port }}/0"
worker_io_queue_key = "{{ controller.redis.worker_io_queue_key }}"

[consul]
address = "{{ controller.consul.address }}"
token = "{{ secret('consul_token') }}"

[app]
log_level = "{{ controller.settings.log_level }}"
build_version = "{{ auto_generated.build_version }}"
```

`ciu.compose.yml.j2` env shrinks to bootstrap pointers (S5.5):

```yaml
services:
  {{ controller.name }}:
    secrets: [bootstrap_token]
    environment:
      - CONTROLLER_CONFIG=/etc/controller/config.toml
      - CONTROLLER__LOGGING__LEVEL={{ controller.settings.log_level }}
```

The 20-line `CONTROLLER__*` env block is replaced by the mounted file; the
app-side change (read TOML at `CONTROLLER_CONFIG`) is dstdns work tracked in
MIGRATION-V2.md.

## Appendix C — v1 → v2 delta summary

Removed (greenfield — no aliases, no fallbacks): env flattening
(`ENV_*`/`UPPER_SNAKE` placeholders), `DERIVE`, `ASK_VAULT_ONCE`,
`[secrets.local]`/`[secrets.state]` in `ciu.toml`, top-level `[env]`,
`eval()` enabled-expressions, `SERVICE_CONFIG_*` constants, Vault payload
alias keys, relative `./vol-*` emission, `[deploy.groups]`/`--groups`,
hook env-update returns + per-point hook function names,
`vault_env_pre_hook` pattern, `bare-metal`/`local` env-type names
(→ `native`).
Added: secrets-as-files + generated overlay, `ASK_FILE`, `#field` Vault
selector, inline-table secret options (`expose_env`/`mode`/`uid`),
configfile mounts + `secret()`, host profiles + `topology_overrides` +
`CIU_HOST_PROFILE`, numeric phases, three hook points
(`pre_secrets`/`pre_compose`/`post_compose`) with structured-only returns,
hostdir inline options (`uid`/`gid`/`mode`/`seed`) + helper-container
provisioning (S6.5), `ciu secrets` subcommands, exit-code contract,
leak scan, native-host parity (S1.9), `--generate-env` as the single
bootstrap (S2.8), unified `ciu.`-prefixed file naming (`ciu.global.*`,
`ciu.compose.yml[.j2]`, `ciu.env`, `.ciu/ciu.compose.overlay.yml`),
dual shipping — `ciu.compose.yml` alongside an optional committed
`docker-compose.yml` + `ciu --shipped` / per-service `shipped` (S8.5–S8.6),
**4.2**: declarative `requires`/`provides` provisioning graph (S13) with
`pg:schema/<name>` kind, configurable `consul:token` Vault path, one-shot
`stack:<name>:healthy` support, per-phase live probing, `ciu check` / `ciu
graph` verbs; and SSH remote transport (S14) — `ciu ssh`, `ciu up/down/health/render
--host`, render-on-target push-deploy, fail-closed host-key pinning, optional
`paramiko` extra (`pip install ciu[ssh]`).
Migration recipes: docs/MIGRATION-V2.md.
