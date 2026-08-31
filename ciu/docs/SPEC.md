# CIU Specification

| | |
|---|---|
| **Status** | Active |
| **Version** | 5.0.0 |
| **Date** | 2026-08-24 |
| **Supersedes** | docs/CONFIG.md + docs/CIU.md + docs/CIU-DEPLOY.md as normative sources (those become non-normative guides) |

This document is the **single normative contract** for CIU. Where any other
document, example, or code comment conflicts with this specification, this
specification wins.

**Package versioning.** The `ciu` wheel is versioned with SemVer derived from git tags
(`ciu-vX.Y.Z`; see `/docs/VERSIONING.md`). The wheel and this SPEC are
**independently versioned**: the package ships features faster than the SPEC
is revised, so a package MAJOR bump does NOT require a corresponding SPEC
MAJOR bump. The SPEC is revised when the *normative contract* changes (new
requirement, withdrawn requirement, or behavioral change to an existing one).
Package releases that add features within an existing contract, fix bugs, or
improve internals do not require a SPEC revision.

> **Reconciliation note (2026-08-24):** the SPEC was previously at 5.0.0 while
> the package had advanced to 7.0.0. This was flagged as a drift; after review,
> the decision is to **decouple** rather than renumber. The SPEC remains at
> 5.0.0 as the current normative contract. Package versions 6.x–7.x shipped
> features (worktree lifecycle, branch hygiene, vendor provenance, init
> scaffolding) that are already documented here under their S-section IDs.
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
  `docker-compose.yml` for a plain `docker compose up` / `ciu up --dir <stack> --shipped` path.
  CIU runs it but NEVER renders or overwrites it (S8.5).
- **DooD** — docker-outside-of-docker: the CIU process runs in a container
  (devcontainer) while the Docker daemon runs on the host.
- **Logical path** — a path as seen by the CIU process (`REPO_ROOT`-based).
  **Physical path** — the same location as seen by the Docker daemon
  (`PHYSICAL_REPO_ROOT`-based).

---

## S1 — Project & path model

- **S1.1** CIU MUST resolve the repo root in this order: `--define-root`
  (alias `--root-folder`), given explicitly, ALWAYS wins outright — returned
  immediately, with no consistency check against `REPO_ROOT` (explicit intent
  is not second-guessed by a shell variable). Otherwise CIU walks up from the
  working directory to the nearest dir containing `ciu.global.defaults.toml.j2`.
  When that walk-up SUCCEEDS: an ambient `REPO_ROOT` that AGREES with the
  derived root is used silently; one that DISAGREES is a REFUSAL — a
  `[S1.1]`-tagged error naming both paths and the three remedies (unset
  `REPO_ROOT`, pass `--define-root` explicitly, or `cd` into the intended
  repo) — CIU MUST NOT silently prefer either value on a genuine disagreement,
  since this decides which repo destructive verbs (`worktree rm`,
  `branches -y`, `clean`) operate on. Only when the walk-up finds **nothing**
  does CIU fall back to `REPO_ROOT` from the environment, then to the working
  directory itself (the ultimate fallback).

  **Correction (2026-08-25, CIU-53).** This precedence previously read
  `--define-root → REPO_ROOT env → walk-up`, and `dev.resolve_repo_root`
  additionally implemented an even earlier ordering (`REPO_ROOT` env checked
  *before* `--define-root`) that violated this SPEC's own documented contract.
  Both are corrected together: the code now matches a walk-up-first order, and
  that order itself closes a masked-default gap the *previously documented*
  contract still had — under the old order, an ambient `REPO_ROOT` from an
  unrelated sourced `ciu.env` silently outranked a successful walk-up
  derivation from where the operator was actually standing, the same
  masked-default hazard family S2.7 already closes for the derived identity
  tuple (CIU-41). See `docs/DESIGN-GUIDE.md` ("Why `dev`/`worktree` refuse an
  ambient REPO_ROOT that disagrees with the derived root").
- **S1.2** A repo whose `ciu.global.defaults.toml.j2` sets
  `standalone_root = true` is a standalone root: CIU MUST refuse to run with a
  `REPO_ROOT` that does not match that directory. The guard MUST be evaluated by
  walking up from the **invocation directory** (cwd, or the `--dir` target) — NOT
  from the already-resolved repo root, which may itself be the contaminated value
  the guard exists to reject. Every entrypoint that resolves a root (`render`,
  `up`, `down`, `check`, `graph`, …) MUST apply this check identically via the
  single `enforce_standalone_root(invocation_dir)` helper. Recommended for every
  independent repo; omit it only for a project intentionally operated as a nested
  sub-tree of a larger CIU root (where `REPO_ROOT` legitimately points at an
  ancestor).
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
  | `PHYSICAL_REPO_ROOT` | env override; else per-repo longest-prefix match of `REPO_ROOT` in `/proc/self/mountinfo` (correct for multi-repo devcontainers, 2026-07-15); else `devcontainer.local_folder` label via `docker ps` (container-origin fallback); native host: `= REPO_ROOT` |
  | `DOCKER_GID` | `stat` of `/var/run/docker-host.sock` or `/var/run/docker.sock`, else `getent group docker` |
  | `CONTAINER_UID` / `CONTAINER_GID` | current user UID / `DOCKER_GID` |
  | `DOCKER_NETWORK_INTERNAL` | `<repo-name>-<instance-id>-network` (instance-id = path hash) |
  | `ENV_TYPE` | `devcontainer` \| `native` \| `github-actions` (v1's `bare-metal` and post-create's `local` unify as `native`) |
  | `PUBLIC_IP`/`PUBLIC_FQDN`/`PUBLIC_TLS_*` | config → ipify → reverse DNS → `localhost` fallback (S2.3 gates whether required) |

  **Refined precedence for the derived identity tuple (CIU-41).** The
  blanket "a pre-set environment value always wins" rule does NOT apply to
  the derived identity values (`REPO_NAME`, `INSTANCE_ID`,
  `DOCKER_NETWORK_INTERNAL`) during **generation** — the same refined
  precedence already applied to `PHYSICAL_REPO_ROOT`. A shell that sourced a
  DIFFERENT checkout's `ciu.env` carries that checkout's network name; a
  generate run in this workspace must never adopt it (the 2026-07
  `PHYSICAL_REPO_ROOT` leak family, dstdns P111 F2 live reproduction).
  Contract: during generation each identity value is derived from THIS
  physical root alone; an ambient value is adopted only when it EQUALS the
  derived one; on mismatch the derived value is written and a stderr warning
  names the ignored ambient value and the S16.1 remedy. Within the same run,
  bootstrap steps that follow a generation (network creation, devcontainer
  attach, S16 cross-checks) act on the just-written file's identity, parsed
  by exact path — never re-derived from ambient state or re-found via an
  ambient `REPO_ROOT`. Read-path precedence of already-generated workspaces
  (ambient wins when consistent) is unchanged.

  **Refined precedence for `PUBLIC_FQDN` (CIU-47).** The same masked-default
  family, host-derived rather than path-derived: a shell that sourced a main
  checkout's `ciu.env` carries that checkout's `PUBLIC_FQDN`, and generate
  adopted it bare — making it the fresh worktree's recorded public name.
  Contract: during generation `PUBLIC_FQDN` is derived from THIS workspace's
  own inputs first (rendered `ciu.global.toml` `infrastructure.public_fqdn`
  — absent or UNREADABLE counts as no entry; a non-string value likewise —
  else reverse DNS of the detected public IP); an ambient `PUBLIC_FQDN` is
  adopted only when it EQUALS the derived one, or when the detection path
  yielded NO independently sourced value to compare against (no usable
  config entry and reverse DNS produced nothing — an offline host; there,
  the pre-set value remains the legitimate manual override, adopted
  silently). Residual, documented and out of CIU-47's scope: the derivation's
  IP input itself keeps the plain pre-set-wins read, so a stale ambient
  `PUBLIC_IP` from another checkout's record feeds the reverse-DNS
  derivation on the same host. On a real
  mismatch the derived value is written and a stderr warning names the
  ignored ambient value. Post-generation bootstrap steps act on the
  just-written file's value (`PUBLIC_FQDN` is in
  `GENERATED_IDENTITY_KEYS`); read-path precedence of already-generated
  workspaces is unchanged. `PUBLIC_IP` and `PUBLIC_TLS_*` keep the plain
  pre-set-wins read (out of CIU-47's filed scope).

- **S2.8** `ciu env generate` is the **single bootstrap entry point** and
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
  if absent, defaults apply only) + optional gitignored
  `ciu.global.worktree.toml.j2` (S3.1b, merged last) → rendered
  `ciu.global.toml` (gitignored);
  per stack `ciu.defaults.toml.j2` (committed, full defaults) +
  `ciu.toml.j2` (**committed sparse override**, see S3.1a; optional, **not
  auto-created** — if absent, defaults apply only) → rendered `ciu.toml`
  (gitignored). The per-stack override mirrors the global override exactly:
  CIU never copies defaults into it. (Before CIU-8, CIU auto-created
  `ciu.toml.j2` as a full copy of defaults; that generated intermediate then
  shadowed later edits to the committed defaults and survived `clean`. There is
  no generated intermediate now, so nothing can go stale.)

- **S3.1a** Override constraints — apply identically to the global override
  (`ciu.global.toml.j2`), worktree-local override
  (`ciu.global.worktree.toml.j2`), and per-stack override (`ciu.toml.j2`):
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
  4. **Committed overrides are not auto-created**: CIU never generates the
     committed global or stack override. Create it
     manually in the repository with only the structural overrides needed; an
     absent override is the normal case (defaults apply alone). `clean`/`--reset`
     remove rendered outputs but MUST NOT remove a committed override.

- **S3.1b** The optional `<ciu-root>/ciu.global.worktree.toml.j2` is a sparse,
  non-secret, gitignored input for one checkout. It is merged after every
  committed global layer and before stack defaults. Managed lifecycle commands
  may create its initial `ciu.instance.service_profiles` and
  `ciu.instance.shared_infra` values; operators may add ordinary sparse global
  overrides afterward. `ciu clean` and `ciu env generate` MUST preserve it
  (`ciu clean --vanilla`, S6.4b, is the one explicit opt-in that removes it).
  Worktree configuration MUST NOT be appended to generated `ciu.env`.

  **CIU-owned `[ciu.instance.generated]` (CIU-60, normative).** `ciu env
  generate` MUST additionally upsert one table into this file, named
  `[ciu.instance.generated]`, carrying exactly six snake_case keys —
  `repo_name`, `instance_id`, `network`, `physical_repo_root`, `repo_root`,
  `public_fqdn` — from the SAME in-memory values that invocation wrote into
  `ciu.env`. They are never re-derived and `ciu.env` is never read back to
  produce them: a second derivation could disagree with the first, and nothing
  would catch it. The table exists so a Jinja TEMPLATE can read these facts
  from the merged config chain (`{{ ciu.instance.generated.physical_repo_root }}`,
  like any other config value) instead of from ambient process environment —
  S3.2's `env` context is still raw `os.environ`, which hooks stopped trusting
  in S9.3 and templates never did. There is deliberately NO bespoke Jinja
  global for these facts: every value is backed by a file an operator can
  `cat`. Rules:
  1. **CIU owns exactly this table.** It is rewritten in full on every
     `env generate`, so a hand-edit to a key INSIDE it is silently
     overwritten — mirroring S16.1a's "do not hand-edit" precedent, and unlike
     every other byte of the file. The block carries that warning inline.
  2. **Every other byte is preserved verbatim.** The write is a text-level
     surgical replace of the region from the `[ciu.instance.generated]` header
     line to the next line beginning a table at column 0, MINUS that region's
     trailing run of blank/comment lines (which belongs to the following
     table). Hand-authored comments, blank-line spacing, key ordering and
     unrelated tables anywhere else in the file survive unchanged. A
     parse-whole-file-then-re-serialize round-trip is NOT permitted: it would
     round-trip every value correctly while destroying every comment in a file
     this section documents as operator-editable.
  3. **Idempotent.** A second `env generate` over an unchanged workspace
     produces a byte-identical file — upsert, never append; never two tables.
  4. **Not gated on an S16 instance record.** The read side
     (`render_global_chain`) reads this file unconditionally by exact path for
     every `repo_root`, so the write side MUST NOT gate either; gating would
     leave the primary/main checkout — the common case — on the old
     ambient-environment path.
  5. The file is created if absent, carrying the same header comment the
     managed-lifecycle writer uses.

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
  `service`, `env`, `state`, `auto_generated`, `secrets`, `governance`,
  `infrastructure` (S15.10; `infrastructure` added at ciu-P21 review —
  `workspace_env.py`'s `_detect_public_fqdn` reads a top-level
  `[infrastructure].public_fqdn` directly off the rendered global config,
  S2.7/CIU-47). Collision = abort. (dstdns's vault stack root key `vault`
  collides with global `[vault.paths]` and must be renamed, e.g. `vault_core`
  — see Appendix B.2.) `local_stack` (V8-PREP-4 groundwork; see
  [CIU-V8-TESTING-GATE-PROPOSAL.md §1.16](CIU-V8-TESTING-GATE-PROPOSAL.md))
  is a recognized, **preferred** root key name — deliberately NOT a member
  of this reserved set (membership here would make it forbidden, the
  opposite of "preferred") — that a stack MAY use instead of a
  directory-derived name. This is purely additive: naming a root table
  `[local_stack]` requires no other change, and a stack that keeps its
  existing directory-derived root key is completely unaffected. The eventual
  V8 breaking step (deferred, not shipped here) is making `local_stack` the
  ONLY accepted root key and relocating hooks to
  `[local_stack.<svc>.hooks]`.
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
- **S3.11** A consumer MAY declare `[deploy].landscape_id` in global config
  (opt-in; absence is legal) as the shared identity of one deployment
  landscape — e.g. a consumer renders its Consul KV root
  `dstdns/<landscape_id>/…` and mesh ACL tags from it. When present, CIU MUST
  validate it on the **final merged** global config — after the committed
  chain and the worktree overlay (S3.1b) — and it MUST match
  `^[a-z][a-z0-9-]{0,62}$` (a DNS-label-safe slug: lowercase letter first,
  then lowercase letters, digits, or hyphens). Violation = abort naming the
  key and the pattern. Validation is once-per-render, never per chain
  directory, so a later layer that corrects an earlier value is honored. This
  value is distinct from the configfile render context's `instance_id`
  (S7.5b) — a per-service replica index, not the workspace `INSTANCE_ID`
  (S2) and not landscape-scoped.
- **S3.12** Deployment-selection facts are exposed to templates and hooks as
  the **`ciu`** mapping (CIU-44): `ciu.selected_profiles` — the ordered named
  profiles this invocation resolved (empty list = the default all-phases
  profile; never a fabricated name) — and `ciu.deployed_stacks` — the FULL
  stack set this invocation deploys (declaration order, deduped), not merely
  the stack currently being rendered, so a template in any stack can derive
  "is upstream X deployed". Contract:
  1. Availability: every deployment render — `ciu up`/`--deploy` (per-stack
     engine pipeline), the preflight/`--render-toml`/`--check`/`--graph`
     renders, `ciu dev` (which selects exactly its one target stack), and the
     per-stack re-renders inside `ciu clean`. Outside these renders the two
     fact keys are absent from the context: a template referencing them
     elsewhere fails loudly (Jinja UndefinedError) rather than silently
     seeing an empty selection.
  2. The facts MERGE into the config's own `[ciu]` table (workspace switches
     such as `auto_connect_network` live there) — never replace it; the keys
     `ciu.selected_profiles` / `ciu.deployed_stacks` are RESERVED for this
     purpose. They are computed ONCE per invocation from the resolved profile +
     selection and threaded unchanged to every render and hook — templates
     and hooks can never disagree.
  3. Hooks receive the same facts on the HookContext (S9.3) as
     `selected_profiles` / `deployed_stacks` tuples, plus this workspace's
     identity from its own `ciu.env` parsed by exact path (S2.7 authority):
     `instance_id` and `network` (None when absent). Hooks MUST NOT read
     identity from ambient environment state.
  4. Nothing is persisted: no `ciu.*` value is written to `ciu.env` (S2.7
     machine-identity layer stays generated facts only) and none is exported
     into the compose process env — the render context and the hook context
     are the only surfaces. A stale selection can therefore never masquerade
     as a fresh one.
- **S3.13** (V8-PREP-1 groundwork, additive) A consumer MAY declare
  `ciu.user_tables = [...]` in global config (opt-in; absence is legal) as
  the list of top-level global-config tables that consumer domain templates
  own. Each declared name MUST be a bare-key-safe string
  (`^[A-Za-z0-9_-]+$`), MUST be unique within the list, and MUST NOT already
  be one of `RESERVED_GLOBAL_TABLES` — the top-level tables CIU itself reads
  at global scope today: `ciu`, `deploy`, `topology`, `vault`, `registry`,
  `governance`, `service`, `auto_generated`, `infrastructure` (the last
  added at ciu-P21 review: `workspace_env.py` reads a top-level
  `[infrastructure].public_fqdn` directly off the rendered global config,
  bypassing `render_global_chain` entirely — a reminder that this set's
  membership must be traced through every reader of the RENDERED config,
  not only `render_global_chain`'s own call sites).
  `RESERVED_GLOBAL_TABLES` is a
  narrower, purpose-specific set, DISTINCT from S3.7's
  `RESERVED_GLOBAL_NAMESPACES` (which instead governs stack ROOT KEY
  collisions — a different question). When `ciu.user_tables` is declared,
  CIU validates the FINAL merged global config (same timing as S3.11 —
  after the committed chain and the worktree overlay): every top-level key
  that is neither in `RESERVED_GLOBAL_TABLES` nor listed in
  `ciu.user_tables` is a single collective `ValueError` naming every
  offending key. Absence of `ciu.user_tables` is a complete no-op — a
  config that never declares it is byte-for-byte unaffected, regardless of
  what top-level tables it has. This is the additive V8 groundwork step
  only; the eventual V8 breaking step (deferred, not shipped here) is
  defaulting `ciu.user_tables` to empty so an undeclared table is ALWAYS an
  error.
- **S3.14** (V8-PREP-3 narrowed groundwork, additive) A consumer MAY declare
  a global `[service.<stack_name>]` identity registry entry per stack — the
  identity half of the V8 two-level `stack.service` hierarchy
  ([CIU-V8-TESTING-GATE-PROPOSAL.md §1.15/§3.1](CIU-V8-TESTING-GATE-PROPOSAL.md),
  rev 1.4, commit `4440c17e`). Absence of `[service.*]` entirely (or an empty
  table) is a complete no-op. When present, each `[service.<stack_name>]`
  entry MUST be a TOML table containing ONLY `type` (required, closed
  vocabulary `CIU` \| `COMPOSE` \| `EXTERNAL` \| `IN_PROCESS`), `location`
  (a repo-relative directory path; REQUIRED for `CIU`/`COMPOSE`, FORBIDDEN —
  a configuration error, not silently ignored — for `EXTERNAL`/
  `IN_PROCESS`), and `description` (optional string). For `CIU`, `location`
  MUST name a directory containing `ciu.defaults.toml.j2`; for `COMPOSE`, a
  directory containing `docker-compose.yml`. **Any other key at
  `[service.<stack_name>]` scope, and any nested table under it — which is
  exactly the shape the deferred per-service realness sub-table layer
  (proposal §3.2: `[service.<stack>.<svc>.<level>]`) would take — is
  REJECTED**, naming the stack and the offending key. This is deliberate:
  accepting-and-ignoring that layer now would let a consumer ship configs
  the real V8 rewrite could not safely reinterpret without a
  silent-acceptance migration trap; refusing it instead keeps that layer
  free for V8 to define on its own terms. Validated once, on the FINAL
  merged global config (same timing as S3.11/S3.13).
- **S3.15** (V8-PREP-3 narrowed groundwork) When `[service.*]` (S3.14) is
  non-empty, `ciu check` MUST additionally run a WARN-only, two-directional
  consistency lint — NEVER a refusal, NEVER exit 2 — comparing every
  registry entry's `location` against what the currently-selected
  profile/phase actually deploys: (1) a registered `location` that no
  selected profile/phase deploys is named in a warning; (2) a deployed
  stack path with no corresponding `[service.*]` entry is named in a
  SEPARATE warning. Both directions are advisory transitional states, not
  defects — per the proposal's own §1.16 mapping rule 1, an entry with no
  live consumer, or a live stack with no registry entry, are both
  legitimate while a repo migrates toward the full V8 model. This lint is
  itself registry-declaration-gated: when `[service.*]` is absent or empty,
  its code path is not entered at all.

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
  (S4.10), `consumed_by = "hook"` (S4.20), `produced_by = "<profile>"`
  (ASK_VAULT only; S13.6). Any other value, or an unparseable directive,
  = abort.
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
  deletes store files after confirmation (`-y` skips). `ciu clean` does not
  delete secret files; use `ciu secrets reset` explicitly when that is intended.
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
- **S5.7** A configfile section MAY declare an optional
  `schema = "<path relative to the stack dir>"` key: a JSON Schema (Draft
  2020-12) for the rendered config (CIU-37). The consumer's generated schema
  is the source; CIU performs no schema authoring, defaulting, or coercion.
  Declaration errors are caught in the same key-validation block as
  `template`/`target`/`instances`, BEFORE any render: a non-path or missing
  schema file aborts immediately. v1 validates **TOML targets only**: the
  rendered bytes are parsed with `tomllib` and validated against the schema
  immediately after the atomic write (S8.4) and before the mount is emitted
  (S5.3). A violation fails the run with a tagged error naming the service,
  the configfile (with its per-instance suffix when `instances > 1`), and the
  offending KEY PATH (jsonschema's `absolute_path` joined with '.'); the
  invalid rendered file is removed so it is never consumable. `jsonschema` is
  an OPTIONAL dependency (`ciu[schema]`): when a schema is declared and it is
  not importable, the run fails loudly pointing at the extra — never a silent
  skip. With no schema key declared anywhere, the library is never imported.
  This runs on the up/dev path (engine step 12); the `ciu render` verb renders
  TOML configs only and does not validate configfiles.

### S5.3a — Directory-level mount, not file-level (hardening)

The rendered file is written to `<stack>/.ciu/rendered/<service>/<target's own
directory structure, minus its leading '/'>/<target's own basename>` — e.g.
`target = "/etc/app/config.toml"` renders to
`.ciu/rendered/<service>/etc/app/config.toml` — and the overlay (S5.3)
bind-mounts that file's **parent directory** over `target`'s **parent
directory**, not the file over the file.

**Why:** a single-file bind mount whose host source does not yet exist at
container-start time is silently auto-created by Docker as a **directory**
(a long-standing Docker behavior, not a CIU bug) — which then makes the
*container-side* path a directory too, breaking any app that expects to
`open()` it as a file (observed live: "Is a directory" on a config-loading
crash-loop, traced to a stray root-owned directory at the rendered file's
path from an earlier failed/raced render). Mounting the file's **parent
directory** instead sidesteps this class of failure entirely, because
`render_configfiles` always `mkdir(parents=True)`s that directory
unconditionally, before ever attempting the file write — so the host source
of a directory-level mount is guaranteed to exist by the time `docker compose
up` runs, regardless of whether the render itself later succeeds. If the
file inside is still missing for some other reason, the app sees a mundane
"file not found" instead of "is a directory".

**Consolidation:** multiple `configfile` sections for one service that target
the **same** directory share the same staging directory (both are rendered
under the same mirrored path) and therefore consolidate into a **single**
directory mount — not one mount per file. `configfile` sections for one
service with **different** target directories get separate, independent
staging directories (and separate mounts), since the mirrored-path scheme
naturally buckets by the target's own directory structure.

**Stale-file guard:** the first time `render_configfiles` writes into a given
staging directory during one call, it first removes any files/symlinks
already there. This prevents a file left behind by an earlier render (whose
`target`/`cfgname` has since changed) from persisting in the staging
directory and being silently exposed into the container by the directory
mount, even though nothing in the current config asked for it.

**Caveat:** because the whole parent directory is now the mount (not just the
one file), it is `read_only: true` in full — an app that expects to write
*other* files into that same container directory at runtime will find the
whole directory read-only, not just the configfile itself. This matches the
pre-existing intent (config directories are not meant to be app-writable) but
is a slightly wider read-only surface than the old file-level bind.

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

  **DooD path routing (CIU-9, normative).** `vol-*` removal MUST resolve the
  physical path (S1.4) *before* deciding how to remove the directory, not
  merely as a `PermissionError` fallback. When `to_physical_path(vol_dir) !=
  vol_dir` (a DooD context, S1.4/S1.9), removal MUST go through the S6.5 root
  helper container against the **physical** path unconditionally — the local
  attempt on the logical path MUST be skipped entirely, never merely tried
  first. Rationale: in DooD the operator's logical-path view is not
  necessarily the same directory the Docker daemon bind-mounted; when the
  hostdir is owned by the operator's own UID:GID (not a fixed-image UID —
  S6.7 Pattern (a) does not apply), a local `shutil.rmtree` on the logical
  path *succeeds without error*, so a naive `except PermissionError` fallback
  never fires and the daemon-visible directory is never touched — `--reset`
  reports success while leaving stale state in place. Only when logical ==
  physical (true native host, S1.9) does a local removal apply, with the
  existing `PermissionError` → S6.5 helper degrade for fixed-UID data.

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

  **Identity-scoped network removal (S6.4a, CIU-43, normative).** `clean`
  also removes the workspace identity network and each selected stack's
  compose ``<project>_default`` network; v1's "network removal is NOT
  performed" posture is withdrawn — it leaked one identity-scoped network per
  teardown while printing `clean complete` (two live dstdns reproductions,
  P111 F4 / P116 O9). Semantics:
  1. The workspace identity network name is read from THIS workspace's own
     `ciu.env` by exact path (S2.7 authority), never from ambient state; the
     selected stacks' compose-created networks are enumerated EXACTLY by the
     compose project label (covering `<project>_default` AND custom-named
     compose networks) — never inferred from a naming convention. An ABSENT
     `ciu.env` means there is no workspace identity network (a checkout where
     `ciu env generate` was never run) and is not an error; a `ciu.env` that
     is PRESENT but unreadable — unreadable by any of the three distinct ways
     it can be, an OS read error, a non-UTF-8 byte, or a malformed entry
     (CIU-62) — leaves the name INDETERMINATE, which is named in the output
     and fails the clean. Indeterminacy is never folded into "this workspace
     has no identity network", which would drop the network from both the
     removal pass and the clause-5 survivor check at once.
  2. Lingering endpoints are disconnected before removal; an endpoint that
     cannot be disconnected is NAMED and fails the clean — a network is never
     silently kept.
  3. Existence is decided from Docker STATE via an exact-name
     `network ls` filter whose non-zero exit ALWAYS means daemon failure:
     an UNVERIFIABLE network or volume set fails the clean (`invariant
     unverifiable`) — indeterminacy never folds into "gone".
  4. **Instance-vs-main split:** a checkout carrying an S16 worktree-instance
     lifecycle record is ephemeral — ALL of its identity-scoped networks are
     removed unconditionally. The MAIN workspace deliberately keeps its own
     workspace network (the devcontainer stays attached to it), but the keep
     is named in the output and in the final success line; stack
     ``*_default`` networks are removed for both classes.
  5. The S6.4 post-clean invariant extends to networks: every targeted
     network must be gone (Docker STATE re-inspection decides, never command
     diagnostic text); any survivor that was not a declared keep exits
     non-zero.
  6. The project volume pass gains a compose-label enumeration per selected
     stack's S8.7 compose project, catching named volumes that carry the bare
     project prefix without the instance tag (e.g. ``<project>-vault-data``);
     the label filter is exact per project, never a broad glob.
   7. **Config-less stack coverage (CIU-46, normative).** When
     `deploy.project_name`/`environment_tag` are absent, a shipped stack
     still runs — under the workspace-identity compose project (S8.7). Clean
     MUST enumerate exactly what up named: each existing selected stack
     contributes its `engine.identity_compose_project_name` to every
     compose-label pass (containers via `ps -a --filter
     label=com.docker.compose.project=<project>`, volumes and networks as
     above). Returning no projects for such a selection (the pre-CIU-46
     behavior) silently skipped the stack's `*_default` network and
     label-prefixed volumes over a printed `clean complete`. A missing or
     key-less `ciu.env` REFUSES the enumeration (exit 2, before ANY teardown)
     — never a silent empty set. Tagged selections keep the S8.7 scoped
     names, unchanged. Container enumeration failure on this path is
     INDETERMINATE and fails the clean (`invariant unverifiable`, symmetric
     with volumes/networks) — it is never folded into "nothing to remove";
     the TAGGED post-clean check keeps its pre-CIU-46 docker-gone-mid-clean
     degradation to a warning, documented S6.4 behavior.

  **Workspace reset: `clean --vanilla` (S6.4b, CIU-60, normative).** `ciu
  clean` without `--vanilla` MUST leave `ciu.global.toml` (rendered), `ciu.env`
  and `ciu.global.worktree.toml.j2` completely untouched — S3.1b already makes
  preserving the last of those a requirement, and the other two are the
  workspace's rendered config and machine identity. `--vanilla` is a purely
  ADDITIVE opt-in that removes exactly those three, and nothing else, for a
  full reset to freshly-CLONED state (committed inputs — the defaults and the
  committed override — are never in scope). Semantics:
  1. It runs LAST, after every pass S6.4/S6.4a describes.
  2. It runs ONLY when those passes all succeeded. A clean that failed keeps
     all three and says so: `ciu.env` is the workspace identity a retry and
     any manual cleanup resolve from, and removing it over a half-torn-down
     workspace would take away the record naming what is still standing.
  3. An already-absent file is a silent no-op for that file — `--vanilla` over
     an already-vanilla workspace succeeds. A file that is present and cannot
     be removed is an ERROR and fails the clean: a `--vanilla` that left one
     standing did not do what it said.
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

## S7 — Orchestration (`ciu up`)

### Phases

- **S7.1** Phase tables MUST be named `phase_<uint>` and are executed in
  **numeric** order. Any other key under `[deploy.phases]`, or non-string
  group/profile entries, = abort at validation (kills the v1 lexicographic
  sort and `.startswith` int crash by design).
- **S7.2** `services = [{ path, name, enabled, health?, shipped?, profiles?,
  env_overrides? }]`.
  `name` is a human-readable stack label for logs and summaries; it MUST NOT
  be interpreted as a Compose service or container identity. `enabled` MUST
  be a boolean or the **name** of a flag in
  `[deploy.control]` (string). Unknown flag name = abort. Expressions are
  forbidden (v1 `eval()` is withdrawn). Optional `health` MUST be a boolean
  and defaults to `true`; `false` excludes an intentionally ephemeral stack
  from orchestration health target resolution without disabling deployment.
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
- **S7.5** CLI: `ciu up --profile <name>` (repeatable; comma form also
  accepted: `--profile core,db`); default from `CIU_SERVICES_PROFILE` in
  `ciu.env` (comma-separated ordered list, e.g. `core,db,worker-io`).
  `CIU_HOST_PROFILE` is **retired** (not aliased): if set, CIU MUST emit a
  deprecation error to stderr and exit 2 — it is never used as a fallback.
  `[deploy.groups]` and `--groups` do **not** exist in the current CLI (greenfield — no
  aliases, no fallbacks); the validator rejects `[deploy.groups]` with a
  pointer to profiles.
  **Composition rules (Seam 4):**
  - **Union, order-preserving, deduped:** `phases`, `stacks`, and
    `compose_profiles` from all selected profiles are unioned preserving
    first-seen order and deduplicating repeats. Phase execution order
    remains numeric (S7.1 `ordered_phases`).
  - **Per-stack pseudo-phases:** profile `stacks` append after the numbered
    phases as ONE pseudo-phase PER STACK (`profile_extra_stacks:<n>`), in
    list order — so the deploy loop's per-phase provisioning probe runs
    just-in-time for each stack and cross-stack `provides`→`requires`
    chains inside one profile (e.g. db-core → db-init) work on a
    greenfield `up`. Profile stack order is therefore meaningful.
  - **Narrowing:** a profile with `stacks` but no `phases` key contributes
    ZERO phases — selecting only stacks-only profiles deploys exactly their
    stacks (e.g. `--profile core,db` per S7.5a), never the full phase set.
    Only a profile with NEITHER key (a pure env/topology override profile)
    means "all phases", and it absorbs the union. An empty phase union
    stays empty; consumers MUST distinguish `None` (unrestricted) from an
    empty set (no phases) — falsy checks silently widen the selection.
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
- **S7.5c** *Deploy layouts.* `[deploy.layouts.<name>]` is a named
  host→bundles plan: `environment` (REQUIRED, one closed value of
  `dev|test|staging|prod` — the durable home of a deployment's environment,
  dstdns D-105 Q2), an ordered `[deploy.layouts.<name>.hosts.<host>]` table
  whose `bundles = [<profile names>]` lists S7.4 profiles, and optional
  `description`. `ciu up --layout <name>` drives the SPEC-J push (S14.2) to
  each host **in declaration order**; every host's remote command runs with
  `CIU_SERVICES_PROFILE` set to that host's bundles (joined by comma) and
  `CIU_LAYOUT` / `CIU_LAYOUT_HOST` / `CIU_DEPLOY_ENVIRONMENT` exported into
  the remote command environment (consumers read them via `{{ env.* }}` /
  `$VAR`). Validation (all before any transport opens, all tagged
  `[S7.5c]`): unknown layout; missing or non-closed `environment`; unknown
  bundle (naming layout+host+bundle); unknown host (naming layout+host);
  an empty or non-table `hosts`. A host failure **aborts** the sequence (no
  continue-on-error in v1), naming the failed host and the not-yet-deployed
  remainder. `--layout` is mutually exclusive with `--host`, `--profile`,
  `--dir`, `--thin`, `--bootstrap` and `--rollback` (the layout owns host
  order and bundles; a passthrough `--profile` would silently override the
  exported `CIU_SERVICES_PROFILE` under S7.5 CLI precedence, and the other
  four have no meaning without a local `--host` push). This exclusion MUST be
  resolved the way the REMOTE parser resolves it — `deploy.parse_args` runs
  with argparse's default `allow_abbrev=True`, so **any unambiguous
  abbreviation of a forbidden flag, in either the `--flag value` or
  `--flag=value` form, is the same flag and MUST be refused identically**
  (`--prof=core` is `--profile`). A refusal names the resolved flag, exits 2,
  and MUST happen before any host is resolved from the inventory, so no
  transport is opened. `ciu layouts` lists declared layouts (name,
  environment, ordered hosts) without validating them — `ciu up --layout` is
  the validating consumer.
- **S7.5d** *Unified `instances` fan-out (V8-PREP-6, ciu-P24).* A service MAY
  declare `[<root>.<service>] instances = N` (positive integer), a sibling of
  its `configfile` table, not inside it — validated identically to S7.5b's
  per-configfile `instances` (positive int, not a bool). It serves two
  purposes:
  - **Agreement anchor:** a configfile under the service that ALSO declares
    its own `instances` value (S7.5b) must AGREE with the service-level
    value. A disagreement is a `[S7.5d]`-tagged `ValueError` naming the
    service, the configfile, and both conflicting values — never a silent
    preference of one over the other. Agreement (equal values), or the
    absence of a service-level value, leaves the configfile-level value's
    existing S7.5b behavior byte-identical.
  - **`ciu.instances` compose context (O2):** `render_compose`'s template
    context gains `ciu.instances` — a `{service_name: N}` mapping, merged
    into the `ciu` table the same way `ciu.selected_profiles`/
    `ciu.deployed_stacks` already are (S3.12/CIU-44). A service is a key in
    this mapping when EITHER its service-level value, or (absent that) the
    largest of its own configfiles' declared values, resolves to N > 1 — a
    service resolving to N ≤ 1, or declaring nothing at all anywhere, is
    simply ABSENT (never present with a value of 1). `ciu.instances` itself
    is likewise absent from context entirely when NO service anywhere
    resolves above 1 (S3.12's existing fail-loud pattern: a template
    referencing `ciu.instances` with nothing declared gets a Jinja
    `UndefinedError`, not a silently empty mapping). A compose template
    checking `'api' in ciu.instances` is the sanctioned way to ask "does
    this service fan out at all"; `{% for i in range(1, ciu.instances.api +
    1) %}` naming `api-{{ i }}` is the sanctioned loop (matching S7.5b's own
    1-based `<service>-<index>` convention exactly — CIU does not generate
    compose service blocks itself; the template author's own loop still
    does, now driven by one CIU-resolved count instead of a hand-maintained
    value).
  - **Duplicate-mount post-render refusal (O3):** immediately after a
    compose render whose `ciu.instances` is non-empty, CIU asserts, for
    EVERY service in that mapping, that the rendered compose document
    contains exactly `<service>-1`..`<service>-N` and does **not** also
    contain a bare `<service>` key. A violation is a `[S7.5d]`-tagged
    `ValueError` naming the service, the declared count, and what compose
    keys were actually found instead. This is deliberately narrower than,
    and the OPPOSITE direction of, S5.3's existing base-selector `[WARN]`
    (which fires when a configfile selector names a service compose does
    not have at all) — that check is unaffected. This assertion runs only
    for services that are keys in `ciu.instances`; an ordinary
    single-instance service with a numeric-looking name is never touched.
- **S7.5e** *Migration-safety refusal: a configfile can never silently
  inherit the service-level default (V8-PREP-6, ciu-P24).* A configfile
  section that OMITS its own `instances` key, under a service that DOES
  declare an explicit S7.5d `instances` value > 1, is a `[S7.5e]`-tagged
  refusal naming the service, the configfile, and the declared value — it
  is never silently treated as "use the service-level count." **Why:**
  before V8-PREP-6, a configfile omitting `instances` had exactly ONE way to
  end up correctly mounted into every compose replica of a multi-instance
  service: a single shared render, fanned out at overlay time by the S5.3
  base-selector mechanism. The moment a service's `instances` key becomes a
  meaningful S7.5d declaration, that same omission is genuinely ambiguous —
  does the author want to opt into the new per-instance render (S7.5b), or
  keep the old single-shared-render behavior? Silently picking the new
  reading would be a silent behavior change for any consumer already shaped
  this way (this is precisely the shape the `applications/workers`
  test-repo demo had before its ciu-P24 migration — see CHANGES.md). The
  remedy is always one of: declare the SAME `instances` value explicitly on
  the configfile (opts into the new per-instance render), or don't declare
  a service-level value at all (keeps the configfile's existing S5.3
  base-selector behavior, unaffected). A service-level value of exactly `1`
  never triggers this refusal (below the fan-out threshold, indistinguishable
  from no default at all).
- **S7.6** Validation: if the active selection includes stacks with
  `*_VAULT` directives, the vault stack MUST be in an earlier phase of the
  same selection **or** a Vault token/address MUST resolve via S4.16 —
  checked before any phase runs.

### Health & readiness

- **S7.7** The health gate passes only when every checked service reports
  `healthy`. `starting`/pending counts as **not passed**; the gate polls
  until `--health-timeout` then fails (exit 1). Services without a
  healthcheck are reported as `no-healthcheck` (warning), not as passing
  silently. A phase service entry MAY set an optional `health_timeout =
  "<duration>"` key (e.g. `"300s"`, same duration-string convention as
  `[deploy.health].timeout`) to override that shared default for its own
  container(s) (CIU-QOL-8). This is not merely a per-service number: every
  container in a health gate call is polled to **its own deadline** within
  one shared poll loop, not a single deadline shared by the whole call. A
  container whose (short) `health_timeout` elapses without ever reporting
  `healthy` is locked into its final status and reported failed at that
  deadline — it does NOT keep waiting behind another, unrelated container's
  longer (legitimately slow) deadline in the same call. Symmetrically, a
  genuinely slow container with a longer override is polled all the way to
  its own deadline and is not spuriously failed by a shorter timeout meant
  for other containers. A selection where no entry declares `health_timeout`
  resolves each container's budget from the two sources below.

  **The gate's budget is a different question from a probe's timeout
  (CIU-67, normative).** `[deploy.health].timeout` is the Docker
  `HEALTHCHECK` field — how long ONE probe attempt may run, correctly a few
  seconds. It is NOT the gate's overall budget for a container to reach
  `healthy` at all, and CIU no longer reads it as one. Each container's gate
  budget resolves most-specific-first:

  1. the phase entry's own `health_timeout` (above);
  2. `[deploy.health].gate_timeout`, the global lever for this question,
     when declared;
  3. otherwise DERIVED from that container's own rendered healthcheck as
     `start_period + retries × interval` — Docker's own worst case for a
     container still legitimately converging: the full grace period during
     which failures do not count, plus the consecutive retries a post-grace
     probe sequence needs to become conclusive. Fields the healthcheck omits
     use Docker's documented defaults (`interval` 30s, `retries` 3,
     `start_period` 0s); a service declaring no healthcheck at all gets the
     same 90s, which it never waits on (it classifies `no-healthcheck`, a
     READY status that resolves on the gate's first poll).

  Live-reproduced before this rule existed: a deliberate `timeout = "5s"`
  gave a container whose own healthcheck declared `start_period = 240s` a
  five-second gate budget, failing it on every fresh deploy while it was
  converging normally.

  **The gate is self-selecting (CIU-68(a), normative).** It runs when
  `--deploy --healthcheck` are both requested, AND whenever any stack in the
  selection declares a `stack:<name>:healthy|completed` requirement — the
  gate is the only mechanism that makes such a requirement reliable across a
  phase boundary, so declaring one is read as declaring the need for it. A
  selection with no such ref is unchanged and pays nothing. CIU announces
  the auto-enable, naming the ref responsible. Before this, a bare `ciu up`
  — the invocation this project's own docs prescribe — never gated, and
  neither `--deploy` nor `--healthcheck` appeared in `ciu up --help` for an
  operator to discover; both are now listed there.

  **A requirement reported `starting` is polled, not failed (CIU-68(b),
  normative).** The per-phase live provisioning probe polls a requirement
  whose probe reports a RETRYABLE non-satisfaction — `stack:*:healthy`
  reporting `starting` (inside its own `start_period`), and
  `stack:*:completed` whose container is still running — up to
  `[deploy.health].gate_timeout` (or the same 90s default), at the gate's
  own 5s cadence. Every other non-satisfaction still fails PROMPTLY: an
  absent container, an `unhealthy` one, a non-zero exit, an unavailable
  daemon and an unparseable state will not resolve on their own, and polling
  them would only make real misconfigurations slow. Live-reproduced: a fresh
  `ciu up` failed at phase_2 because `stack:infra/vault:healthy` reported
  `starting` on its one and only probe; vault reported healthy moments
  later, unobserved.

  `ciu health --preflight` parses `CMD`/`CMD-SHELL` healthchecks and
  probes only external executables in the declared image. Shell builtins,
  control-flow tokens, numeric arguments, and quoted `python -c`/`node -e`
  source MUST NOT be treated as executable names; `--strict` exits 1 only for
  a genuinely missing probed executable. A direct executable declared as a
  distroless image's entrypoint counts as present even when the image has no
  shell with which to run `command -v`. For orchestration health, each
  selected stack's rendered Compose model is authoritative: CIU checks every
  active `services.*.container_name`, applying the same entry-level and
  host-level Compose profiles as deployment. CIU MUST NOT derive runtime
  identity from the phase service's display `name`. Missing rendered Compose
  and active services without a concrete `container_name` are authoring errors;
  a missing expected container fails the gate. A phase service with
  `health = false` contributes no targets. If every selected service is so
  excluded, CIU reports that no health-enabled containers were selected and
  the gate passes without invoking an empty poll.

  A phase service MAY also set `one_shot = true` (ciu-P23, V8-PREP-5) to
  DECLARE it as a run-to-completion stack — validated shape only
  (`deploy_pkg.phases.service_one_shot`, defaults `false`); this package does
  NOT wire it into the health gate's own polling loop above (a future,
  larger change once a real caller needs that behavior). The one live
  consumer today is `provisioning.py`'s `:healthy` probe (S13.2), which reads
  a target stack's own `one_shot` declaration to warn when it is referenced
  via `:healthy` instead of the dedicated `:completed` terminal.
- **S7.8** Container lookups MUST use exact names or anchored name/label filters,
  never substring matches.

### Registry

- **S7.9** When `deploy.registry.url` is set, CIU MUST verify that
  credentials for that registry exist (Docker config `auths`/`credHelpers`
  lookup); v1's `docker login --get-credentials` invocation is withdrawn.
  Verification failure aborts before compose runs.

### Status reporting

- **S7.10** `ciu status [--profile NAME] [--json]` (CIU-QOL-6) is a
  READ-ONLY report over the SAME selection chain `ciu up --profile` uses
  (`load_global_config` → `resolve_profiles` → `build_selection`). For every
  selected stack it resolves the compose project using S8.7's exact
  tags-present/tags-absent branching (per stack — never a bespoke
  re-derivation), inspects that project's containers, and classifies each
  container's health via the S7.7 vocabulary
  (healthy/starting/unhealthy/no-healthcheck/not-found). A stack directory
  absent on disk MUST be reported with `compose_project: null` and an empty
  container list — never dropped from the output; a resolved compose
  project with zero containers is a legitimate "not started" result. A
  Docker daemon that cannot be reached MUST abort the command with a
  non-zero exit and a clear message — it MUST NOT render as an empty or
  healthy-looking report; the two determinations (unreachable daemon vs.
  nothing running) MUST NOT collapse into the same output. `--json` emits
  `{schema_version: 1, profile, stacks: [{path, name, phase_key,
  compose_project, containers: [{name, status, image}]}]}`. `ciu status`
  never invokes `docker compose up/down/build/exec` — it is read-only by
  contract, not merely by default.

- **S7.11** `ciu bake [targets ...] [--no-cache]` / `ciu bake --profile NAME
  [--no-cache]` (CIU-QOL-7) unifies the two build entry points around ONE
  selection model. With NO `--profile`, behaviour is byte-identical to the
  pre-existing form: explicit positional targets (or `all`, when none are
  given) go straight to `docker buildx bake ... --load`. With `--profile`,
  the target list instead comes from the SAME selection chain S7.10 uses
  (`load_global_config` → `resolve_profiles` → `build_selection`), reduced
  to the final path component of every selected `applications/`/`tools/`
  entry — so `ciu bake --profile X` builds exactly the images `ciu up
  --profile X` would deploy (an empty resulting target list still bakes
  `all`, matching the no-`--profile` default). `--profile` and explicit
  positional targets are mutually exclusive (S10.3 exit 2) — which one
  would silently "win" is not obvious to a reader and would invite a
  divergent-build bug. `--no-cache` combines with either mode unchanged.
  Every invocation still carries S17.1's revision stamp.

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
- **S8.1a** *`--project-directory` (CIU-71).* Every compose lifecycle
  invocation this section and S8.6/S8.7 describe (native `up`, `--shipped`
  `up`, and the reset/down path) MUST additionally pass
  `--project-directory <repo_root>` — the same `REPO_ROOT`/`--define-root`
  value already resolved for that invocation (S1). Without it, `docker
  compose` resolves every relative path IN the compose file — a service's
  `build.context` chief among them — against the **compose file's own
  directory**, not the repo root.

  **This is a deliberate exception, not an extension of an existing
  convention.** CIU's OTHER relative paths (hostdir `vol-*` directories —
  `create_hostdirs`'s `_resolve_entry`/`_seed`, `engine.py`; `ASK_FILE`
  secret sources — `materialize.py`'s locator resolution; configfile
  `schema`/`template` paths — `composefile.py`'s configfile render, whose
  own error text says "relative to the stack dir") all resolve
  **stack-dir-relative**, not repo-root-relative. `build.context` is made
  repo-root-relative instead because that is what a Dockerfile `COPY` of a
  repo-shared asset needs — the same reasoning a plain (non-CIU)
  `docker compose --project-directory` user would apply. The two
  conventions genuinely differ within one stack: a `hostdir.path` and a
  `build_context` sitting in the same `ciu.defaults.toml.j2` resolve
  against two different base directories.

  **`dockerfile:` moves with `context:`, not independently.** Compose
  resolves `build.dockerfile` relative to `build.context`, never relative to
  `--project-directory` directly (the same rule `ciu dev`'s own
  `_build_dev_image`, `src/ciu/dev.py`, already applies:
  `Path(context) / dockerfile`). Once `context` becomes repo-root-relative,
  an unset or bare `dockerfile: Dockerfile` now resolves against the repo
  root, not the stack dir — a stack author who moves `build_context` back to
  a plain repo-root-relative form after this change MUST move `dockerfile`
  the same way, or author it repo-root-relative outright.

  **`.env` also relocates.** `docker compose` loads a bare `.env` file (for
  its own `${VAR}` interpolation of the compose YAML, and as one source of
  container environment when nothing overrides it) from the **project
  directory**, not the compose file's directory — so `--project-directory`
  changes which `.env` a stack picks up too, the same way it changes
  `build.context`. CIU itself is unaffected: it never relies on a bare
  `.env` file and always passes the compose process environment explicitly
  (S8.2, `env=compose_env`, which docker compose's variable substitution
  already sees as the highest-precedence source). A consumer stack with its
  OWN stack-local `.env` (authored outside CIU's secret/configfile
  mechanisms) is the only affected case; **this relocation is accepted as
  the correct behavior, not compensated for with a second `--env-file
  <stack_dir>/.env` flag** — adding one would reintroduce exactly the kind
  of file-existence-dependent implicit behavior CIU's own secrets/configfile
  design otherwise avoids, for a usage pattern (bare `.env` beside a CIU
  stack) CIU does not itself support or encourage.

  `--project-directory` does not change where docker compose derives the
  *project name* from — that stays `-p` (S8.7) — it only changes how
  relative paths and `.env` resolution inside/around the compose file
  resolve.
- **S8.2** The compose process environment is exactly: `os.environ`
  (which includes the sourced `ciu.env`) + `PWD` + `COMPOSE_PROFILES`
  (when set by profile/service) + `expose_env` secrets (S4.19).
  **TOML config flattening into env is withdrawn** — `flatten_dict` /
  `ENV_<KEY>` / `UPPER_SNAKE` placeholders no longer exist. All non-secret
  values reach the compose file via Jinja2 at render time.
- **S8.2a** *`env_required` declarations (V8-PREP-7, additive).* A service
  MAY declare `[<root>.<service>] env_required = [...]` — a list of
  non-empty strings, each matching `^[A-Za-z_][A-Za-z0-9_]*$` (a valid
  shell/env variable name), duplicates within one service's own list
  rejected. Shape-validated whenever present (`composefile.resolve_env_required`);
  absent anywhere is a complete no-op (zero behavior change). This adds NO
  new Jinja context variable and NO new template-facing mechanism: `{{
  env.* }}` already receives the full process environment on every render
  path (`config_model.render_jinja2_text`'s callers,
  `composefile.render_compose`, `composefile.render_configfiles` all build
  `{"env": dict(os.environ)}` today) — the only new behavior is the
  presence CHECK below, not variable access. This package deliberately does
  **not** touch `${VAR:-fallback}` handling in compose templates — that
  withdrawal is the separate, genuinely breaking QOL-10 item, out of scope
  here.

  The presence check (`composefile.check_env_required`) runs against the
  dict `composefile.compose_process_env` itself builds and returns — i.e.
  AFTER secret materialization, IMMEDIATELY BEFORE the compose invocation
  (S8.3 step 16) — never against `os.environ` alone or any
  pre-materialization environment: a variable supplied only via a secret's
  `expose_env` (S4.19) is injected only into that returned dict, it never
  touches the real process environment, so checking earlier or against
  `os.environ` produces a false "missing" failure for exactly that case.
  Missing variables across ALL declared services in one compose invocation
  are collected into ONE error naming every missing `service.VARIABLE`
  pair (mirroring `config_model.expand_env_vars_or_fail`'s collective-error
  style) — never a stop on the first miss. A declared variable present but
  set to the empty string counts as missing, the same convention
  `expand_env_vars_or_fail` uses.

  A template can already rely on the machine-identity keys
  `workspace_env.REQUIRED_KEYS_CORE` (`REPO_ROOT`, `PHYSICAL_REPO_ROOT`,
  `DOCKER_NETWORK_INTERNAL`, `CONTAINER_UID`, `DOCKER_GID`) and
  `workspace_env.GENERATED_IDENTITY_KEYS` (`REPO_NAME`, `INSTANCE_ID`,
  `DOCKER_NETWORK_INTERNAL`, `PUBLIC_FQDN`) via `{{ env.* }}` without
  declaring them in `env_required` — see the `ciu.env` Key Provenance Table
  in docs/CONFIG.md.

  **Not yet wired to the live `ciu up` pipeline.** `compose_process_env`
  gains two new optional keyword-only parameters, `config` and `root_key` —
  when BOTH are given, the shape validation and presence check above run;
  omitted (the only way `engine.py`'s real call site invokes it today), it
  is inert. `engine.py` is out of this package's scope (it is the ONLY call
  site with both the merged stack config and the post-materialization env
  in hand at the same point) — passing its already-computed `merged`/
  `root_key` through to this call is a one-line change deferred to the real
  V8 cutover.
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
  rename, or otherwise overwrite that file. `ciu clean` (S6.4) removes
  `ciu.compose.yml` and the overlay but MUST NOT remove a hand-written
  `docker-compose.yml`. This lets a project offer two deploy paths
  side-by-side: the CIU-managed path (`ciu.compose.yml` + overlay, with
  secrets/configfiles/hostdirs) and the plain path (`docker-compose.yml`).
- **S8.6** *Shipped-compose passthrough.* `ciu up --dir <stack> --shipped` runs a maintainer's
  pre-shipped compose (default `docker-compose.yml`; override with `-f`)
  **through** CIU without requiring a stack config (`ciu.defaults.toml.j2`)
  and without the secret / overlay / configfile steps. It MUST still:
  load `ciu.env` (S2), render the global chain for the `auto_connect_network`
  setting, ensure/attach the workspace network (S2.8), run the DooD preflight
  (S1.5), then `docker compose -f <file> up -d` with the same cwd/project/
  `--project-directory` (S8.1a) convention as the native path. The compose
  process env is S8.2 minus
  `expose_env` secrets (none are resolved). `--dry-run` stops before the
  compose up. Profile-based `ciu up` exposes the same path per service via a boolean
  `shipped` key in `[deploy.phases.*].services` (default `false`; non-bool =
  abort, S7.2); a `shipped` service participates in phases and the health
  gate exactly like a native stack.
- **S8.7** *Instance-scoped compose project.* Every compose lifecycle
  invocation (`up` — native and shipped — and the reset/down path) MUST pass
  `-p {deploy.project_name}-{deploy.environment_tag}-{stack_dirname}` —
  alongside `--project-directory` (S8.1a), a separate flag governing path
  resolution rather than project naming. Without
  it, docker compose derives the project from the stack directory BASENAME,
  which is identical for every checkout/worktree of a repo — so a second
  instance's `up` ADOPTS the first instance's containers (same project +
  service, changed `container_name`) and removes them (2026-07-16 dstdns
  multi-stack incident). The scoping pair is the same one that already scopes
  container names (S7.7/S8.7). **There is no compose invocation without an
  explicit `-p` anywhere in CIU (CIU-46 cutover):** shipped mode derives the
  identity project whenever the config lacks the naming pair; the reset/down
  path requires `deploy.project_name` outright (it names label scopes too)
  and derives the identity project only in the partial-pair case
  (`environment_tag` absent). The derivation:
  WORKSPACE-IDENTITY project `{REPO_NAME}-{INSTANCE_ID}-{stack_basename}`
  from THIS checkout's own `ciu.env`, parsed by EXACT path (S2.7 authority —
  never ambient shell state). The withdrawn pre-CIU-46 behavior let docker
  derive the cwd BASENAME: identical for every checkout/worktree of a repo,
  so it both collided across instances (the 2026-07-16 dstdns multi-stack
  incident class) and was unenumerable by clean, whose S6.4a passes then
  skipped the stack over a printed `clean complete`. The identity name is
  computed by `engine.identity_compose_project_name` — the SAME function
  clean calls — so up and clean name a project identically by construction;
  it normalizes exactly like docker compose's own rule (lowercase,
  `[a-z0-9_-]`) and refuses (`[S8.7]`, exit 2) when `ciu.env` is missing or
  lacks the identity keys: a deployment that cannot be NAMED must not start,
  and a teardown that cannot be named must refuse, never skip. The native
  `up` path REQUIRES the naming pair outright. **Migration guard:** before `up`, CIU MUST detect containers of
  THIS instance (name-prefix `{project}-{env_tag}-`) still carrying the
  legacy dir-derived project label and abort with one-time migration
  instructions; `CIU_ADOPT_LEGACY_PROJECT=1` instead removes them (bind-
  mounted state survives) and proceeds. Reset's Step-4 component-label
  cleanup remains project-independent and also clears legacy containers.

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
  The context also carries the deployment-selection facts and workspace
  identity (S3.12 / CIU-44): `ctx.selected_profiles` / `ctx.deployed_stacks`
  (tuples; `None` outside a deployment render) and `ctx.instance_id` /
  `ctx.network` (from this workspace's own `ciu.env` by exact path, or
  `None`). Hooks read identity/selection from these fields — never from
  ambient environment state (S9.4 forbids env mutation; ambient reads are the
  CIU-41 contamination vector).
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
- **S9.5** **Optional preflight entry point** — a hook module (or `Hook`
  class) MAY define `validate_config(config, ctx) -> list` beside its `run`.
  CIU calls it during `ciu check` (S13.4a) **and, since CIU-64, during
  `ciu up`'s own automatic static preflight (S13.4c)** — the same
  side-effect-free call in both cases. `run()` is never called by either.
  Contract:
  - **Return type is a `list` of findings**, empty list = valid. A `None`
    return is tolerated as "no findings"; a `str`/`bytes` return, or any
    non-list/tuple type, is a contract violation and is reported as such.
    It is NOT a boolean: a `True`/`False` return is a violation, never read
    as a verdict.
  - **Each finding carries a severity (CIU-65).** Two shapes are accepted:
    - a bare **message string** — severity `ERROR`. This is the pre-CIU-65
      shape and its meaning is UNCHANGED, so no existing hook changes
      weight;
    - a **2-element `tuple` or `list`** `(severity, message)` whose severity
      is `WARN` or `ERROR`, matched case- and whitespace-insensitively
      (`str(value).strip().upper()`) — the same normalization S10.7's
      `ciu.exit_on` already applies to this vocabulary. Lists are accepted
      alongside tuples so a hook assembling findings from JSON or a
      comprehension is not trapped.

    The finding vocabulary is exactly `WARN` and `ERROR` — deliberately a
    SUBSET of S10.7's `WARN`/`ERROR`/`NEVER`, because `NEVER` is a
    *threshold* ("abort at nothing"), not a property a finding can have. Any
    other severity value is REFUSED as its own ERROR finding naming the
    accepted vocabulary; it is never guessed at. Defaulting an unrecognized
    severity to `WARN` would let a hook author's typo (`"warning"`,
    `"Error!"`) silently downgrade a blocking finding to an advisory note.
  - **What each severity does.** `ERROR` findings mark the
    `hooks-preflight` stage failed: `ciu check` exits 2, and `ciu up`'s
    S13.4c preflight refuses before anything starts. `WARN` findings are
    recorded as stage NOTES: they appear in the prose report as
    `note: [WARN] …` and in the `--json` envelope's `notes` array (carrying
    the same `stack`/`hook` keys a finding does), and they change NO exit
    code and block NO deploy. A `WARN` that still blocked would make the two
    tiers one tier. This routing is deliberately NOT wired through S10.7's
    `ciu.exit_on`/`$CIU_EXIT_ON`: a hook's static findings must not change
    the machine-readable `--json` verdict according to ambient shell state,
    which is the coupling S9.3/CIU-41 removed from hooks in the first place.
  - It receives the **same merged, guarded config** (S4.21 — secrets appear
    as `SecretGuard` objects, so a preflight can confirm a secret is
    *declared* by name without ever seeing a value) and a `HookContext` with
    the same `stack_dir`/`repo_root`/`selected_profiles`/`deployed_stacks`/
    `instance_id`/`network` fields a real `run()` would receive.
  - `ctx.secret_file(name)` raises `KeyError` for **every** name during
    `ciu check` — no secret was materialized, so no store file exists to
    name (S13.4a). A preflight needing a real materialized secret **path**
    (not merely the fact that the secret is declared) cannot run at check
    time; this is a documented limitation, not a defect. `ctx.wait_healthy`
    / `ctx.wait_tcp` are `None` at check time for the same reason — nothing
    is running, and a preflight must not perform I/O anyway.
  - It MUST NOT execute side effects (no Docker, no network, no writes).
    Like S9.4's env rule this is a contract, not a sandbox.
  - An exception escaping its body is reported as **that hook's own**
    preflight failure, naming the exception; it never aborts the rest of the
    check run.
  - Defining it is entirely optional. A hook without one is skipped by the
    preflight stage — that is a note, not an error.

## S10 — CLI surface (delta to v1)

- **S10.1** `ciu` exposes only the verb dispatcher documented by `ciu --help`:
  `version`, `init`, `env`, `render`, `profiles`, `up`, `down`, `clean`,
  `health`, `layouts`, `diagnose`, `bake`, `ksm`, `dev`, `secrets`, `check`,
  `graph`, `ssh`, `iops-baseline`, `worktree`, `capabilities`, `host-secrets`,
  and `provenance`. `ciu version` is the sole
  public version query; the former top-level `ciu --version` option is
  withdrawn. Single-stack execution is `ciu up --dir PATH`; this public form forwards the
  remaining single-stack engine flags (for example `--render-toml`, `--reset`,
  and `--print-context`). Profile-based orchestration is `ciu up --profile
  NAME`; environment generation is `ciu env generate`. Flat `ciu -d …` forms
  are not a public surface. `ciu env print` (CIU-60) is the read-only
  companion to `ciu env generate`: it prints the ALREADY-WRITTEN `ciu.env` as
  shell `export KEY='value'` lines for `eval "$(ciu env print)"`, generates
  nothing, and refuses loudly (naming `ciu env generate`) when `ciu.env` is
  absent. It is NOT named `apply` or `source` and MUST NOT be documented as
  applying the environment into the calling shell: a subprocess structurally
  cannot mutate its parent shell's environment, and naming it that way would
  document a capability that cannot exist.
- **S10.2** Profile selection is `ciu up --profile <name>` (S7.5); `--groups`
  does not exist (S7.5, greenfield). Per-service `shipped = true` (S8.6)
  routes a stack through its pre-shipped `docker-compose.yml`.
- **S10.3** Exit codes: `0` success · `1` runtime failure (compose, health,
  hooks, vault I/O) · `2` configuration/validation error (S3/S4/S7 static
  checks, argparse) · `3` environment/bootstrap error (S1/S2: missing env
  keys, DooD preflight, dependencies).
- **S10.4** Flat verb CLI (`ciu <verb> …`): each verb's `-h`/`--help` MUST
  print that verb's **own** synopsis and options, never a withdrawn flat
  argparse surface. Help is verb-scoped (CIU-7). Verbs: `env`, `render`,
  `profiles`, `layouts` (S7.5c), `up`, `down`, `clean`, `health`, `bake`,
  `dev` (S5a), `secrets`, `capabilities` (S16.5), `host-secrets` (S14.3a),
  `check` (S13), `graph` (S13), `ssh` (S14), `iops-baseline` (S15.9), `ksm`
  (S15.17), `worktree` (S16), `provenance` (S17), and `init` (S19).
  The global modifier `--host <name>`
  (S14) is accepted on `up`, `down`, `health`, and `render`; `--thin`
  (with `--host`) selects the docker-optional push→activate path on `up` and
  `health` (S14.6). `up --host --thin` also accepts `--bootstrap`/`--rollback`.
  The modifiers that SELECT a verb's code path — `--host` on `up`/`down`/
  `health`/`render`, `--dir` and `--layout` (S7.5c) on `up` — MUST route
  identically whether written `--flag value` or `--flag=value`, **and MUST
  additionally route every abbreviation that the parser they would otherwise
  fall through to would itself accept**. A modifier spelling that fails to
  route is a defect, not a syntax error, whenever the fall-through is silent:
  `ciu-deploy` declares `--host` for its help text but never reads it (S10.2),
  so `--host=NAME` — and equally `--hos NAME` or `--ho=NAME` — once parsed
  cleanly downstream with the host discarded, turning an intended SPEC-J push
  into a LOCAL deploy of the active profile at exit 0. `--layout` and `--dir`
  do not exist in that parser at all, so every abbreviation of them already
  fails loudly (exit 2, nothing deployed) and they route on the exact and `=`
  forms only — widening them would resolve, in CIU, an abbreviation that
  `ciu-deploy` itself treats as ambiguous (`--d` against `--define-root`).
  This asymmetry is normative and MUST be pinned by a test against the real
  `ciu-deploy` parser rather than asserted in prose.
  A sub-subcommand with its own parser (`env generate`) keeps its argparse help.
  `--log-prefix-time-short` is a global presentation flag accepted before or after the
  verb (before a `--` passthrough boundary): it prefixes CIU's existing `[INFO]`, `[WARN]`,
  and `[ERROR]` records with `HH:MM:SS`. Interactive terminals colour the severity token
  green/yellow/red; non-TTY streams remain ANSI-free for logs and machine processing.
- **S10.5** `ciu diagnose [--project NAME] [--logs N] [--json]` is a strictly
  read-only Docker diagnostic. It selects CIU-labelled containers (optionally
  one project), inspects state without restart/remediation, and correlates:
  `.State.OOMKilled`, exit 137, unhealthy history, restart count,
  RAM/memory+swap limits, and at most `N` recent log lines per container.
  Known signatures include Redis Pub/Sub channel ACL denial, memory
  exhaustion, full storage, and native crashes. It MUST print a concrete
  remedy per finding and MUST NOT print container environment or secret
  values. Exit `0` means no error-severity findings, `1` means findings were
  reported, and `2` means Docker/argument/decoding failure. `--json` emits a
  stable list of `{severity,container,code,summary,remedy}` objects.
- **S10.6** Exit-on-severity policy (`warn_policy.py`): some
  conditions CIU detects are configuration SMELLS rather than unconditional
  breakage (first case: S15.16's mem_min ancestor-chain gap) — real, worth
  surfacing loudly, but an operator may already know about and accept the
  risk for one run. `warn_policy.warn_or_raise(message)` prints the message
  tagged by severity, then exits per the configured threshold.

  A consumer MAY declare `[ciu].exit_on` as one of three closed values:

  | Value | Behavior |
  |-------|----------|
  | `"WARN"` | Exit on any warning OR error (strictest) |
  | `"ERROR"` | Exit on errors only; warnings logged and run continues (**default**) |
  | `"NEVER"` | Never abort via this policy; all findings logged |

  Resolution order: config declaration → `$CIU_EXIT_ON` env var → default
  `"ERROR"`. An explicit config declaration ALWAYS wins over the env variable.
  Invalid values are a configuration error naming the key and vocabulary.

  The previous boolean `ciu.fail_fast` and env `CIU_WARNINGS_AS_ERRORS`
  are withdrawn. Existing consumers using `fail_fast = true` should migrate
  to `exit_on = "WARN"`; those using `fail_fast = false` should migrate to
  `exit_on = "ERROR"` or `"NEVER"` depending on intent.

  Env-var-only fallback mirrors every other ambient `CIU_*` toggle in
  this codebase (`CIU_SKIP_DOOD_PREFLIGHT`, `CIU_ADOPT_LEGACY_PROJECT`,
  unambiguous opt-out rather than merely failing to opt in. Env-var-only, no
  dedicated CLI flag: this mirrors every other ambient `CIU_*` toggle in
  `CIU_SSH_INSECURE_TOFU`), none of which have one either — CIU's per-verb
  argparse surface (S10.4) is built as individual small parsers per verb,
  not a shared parent parser, so one flag meant to apply everywhere is
  cheaper and more consistent as an env var. **Not** universally applied to
  every existing `[WARN]`/error site in CIU — see
  `docs/DESIGN-NOTES.md` D6 for the surveyed candidates and why some
  (S15.G9-1's missing-slice abort; S15.13's forward-compat unknown-key
  warning) are deliberately left as-is.

  **Config-driven policy:** when `[ciu].exit_on` is declared in global config,
  it is the authoritative source — the env variable cannot override it,
  preventing ambient shell state from silently weakening a project's stated
  posture. When absent, `$CIU_EXIT_ON` provides a per-invocation override;
  when neither is set, the default `"ERROR"` applies.

## S11 — Validation catalog (static, pre-execution)

Checked after merge, before reset/hostdirs/hooks: S3.5 single root key ·
S3.7 namespace collision · S4.1/S4.5 directive placement · S4.4 directive
shape · S4.6 name uniqueness/pattern · S4.20 declared-vs-consumed ·
S5.4 unknown `secret()` name · S6.1 hostdir value shape · S7.1 phase
naming · S7.2 enabled flags + `shipped` bool (S8.6) ·
S7.5 `[deploy.groups]` rejection · S7.6 vault ordering · S2.2/S2.3 env keys ·
S1.7 gitignore (incl. the auto-created override templates `ciu.toml.j2` /
`ciu.global.toml.j2`) · S15.2 governance shape (`enabled` bool,
`exempt_services` list-of-strings) · S13.6 `produced_by` grammar
(ASK_VAULT-only inline key, non-empty string) and its producer preflight ·
S8.7 compose-naming refusals (missing/key-less `ciu.env`, non-round-tripping
stack dirname for config-less naming). Each failure reports the spec ID it
enforces.
S7.5c layout shape (`environment` closed vocabulary, non-empty hosts table,
every host in inventory, every bundle resolves, no empty bundles) ·
S16.7 exec-target declaration shape (alias Git-safe, exactly
stack/service/workdir/requires_worktree_mount keys) ·
S17.5 `[deploy.provenance].vendor_images` type checks (list of non-empty
strings) · S13 provisioning ref grammar validation on every render path
(including single-stack `--dir` mode via engine.main_execution).
`config_model.validate_declared_features` (QOL-11) runs the layout/
exec-target/vendor_images checks EAGERLY on every render path — single-stack
`engine.main_execution` and profile-mode `deploy.action_check` — not only
when that run's own `--layout`/exec/provenance command invokes the specific
feature; the underlying checks are unchanged, only their reach is widened.

## S12 — Extension points (reserved, not implemented)

Generation parameters (`length`, `charset`), `transform`, additional secret
providers (`ASK_SOPS`, `ASK_AWS`, ...), per-profile compose-file additions,
`governance.cpu_limit` / `governance.cpu_reservation`,
`deploy.layouts.<name>.hosts.<host>.environment_tag` override.
Parsers reject unknown options today (S4.7).

The former reservation `ciu.instance.shared_infra.services[*].aliases` is
**withdrawn, not implemented as reserved** (CIU-52). Its premise — that a
reference-side service could be named as a property of an entry in
`shared_infra.services` — does not hold: `services` names the JOINING
instance's own diverging containers, so an `aliases` sub-key there could only
ever have addressed the joiner's own copy of a service. Reference-service
addressing ships instead as the independent, alias-keyed
`ciu.instance.shared_infra.ref_services` table — see S16.1.

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
| `pg:role/<name>` | Postgres login role exists | `psql` → `pg_roles` (default `postgres` db), in the container of the stack that **provides** the ref |
| `pg:db/<name>` | Postgres database exists | `psql` → `pg_database`, in the container of the stack that **provides** the ref |
| `pg:schema/<name>` | Schema exists in the **application** database | `psql -d <registry.postgresql.database>` → `information_schema.schemata`, in the container of the stack that **provides** the ref |
| `minio:user/<name>` | MinIO service account exists | `mc admin user info local <name>`, in the container of the stack that **provides** the ref |
| `consul:token/<svc>` | Consul ACL token exists in Vault | Vault read at `registry.consul.token_vault_path` (default `consul/acl/tokens/{svc}`; override via `[registry.consul] token_vault_path = "…"`) |
| `stack:<name>:healthy` | Another container is up+healthy | `docker inspect .State` |
| `stack:<name>:completed` | Another container **ran to completion** (exit 0) | `docker inspect .State` — `Running == false` and `ExitCode == 0`; NEVER reads `.State.Health` (V8-PREP-5, ciu-P23) |

**`pg:`/`minio:` probe target: the PROVIDER's container (CIU-70, ciu-P40).**
A `pg:` or `minio:` probe execs into the container of the stack whose
`provides` list carries the exact ref being probed. **Your Postgres/MinIO
service may be keyed anything** — `pg`, `db`, `postgres_primary`,
`skywalking-oap-db` — and two Postgres services in one deployment are each
probed in their own container. Until ciu-P40 both probes hardcoded the
LITERAL service keys `postgres` and `minio`, a requirement no released
version of this spec or CONFIG.md ever stated, so any other key produced a
probe against a container that does not exist. Resolution rules:

- The provider's declared stack path resolves to a container name the same
  way a `stack:<selector>:healthy` ref's selector does (declared path →
  final segment → `{project}-{env_tag}-<segment>`; see the slash-bearing
  selector note below). Two declared paths sharing a final segment therefore
  still collapse onto one container name — that is CIU-66, unchanged here.
- **No stack provides the ref** → not satisfied, with that as the reason
  (`no stack provides '<ref>' — cannot resolve a container to probe`). It is
  never a literal-service-key guess. The static lint (S13.3) normally
  catches this first, since every `requires` must be provided.
- **Providers resolving to genuinely different containers** → not satisfied,
  naming both; CIU refuses rather than picking one.
- The requires/provides graph a probe resolves against spans **every**
  rendered stack in the run, not just the phase currently being probed —
  the provider of a cross-phase ref is in an earlier phase by construction.

**`pg:`/`minio:` failure reasons distinguish absence from unreachability
(CIU-70, ciu-P40).** `psql -tAc` exits **0** for a query that ran and matched
nothing, so exit 0 is the only status from which "the role/db/schema
genuinely does not exist" follows, and that is the only case worded that way
(`pg role 'api' does not exist (query ran in '<container>', no matching
row)`). A `docker exec` that never ran the command — `No such container`, or
a stopped container — reports `container '<name>' unavailable (<why>) — pg
role 'api' was NOT checked`, and any other non-zero `psql` status (server
starting, connection refused, auth) reports `could not be checked`. The same
container-unavailable split applies to `minio:user/<name>`; `mc`'s own
non-zero verdict keeps its existing `MinIO user '<name>' not found (rc=N)`
wording, because for `mc` the non-zero status *is* the answer.

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

**`stack:<name>:healthy` one-shot support (DEPRECATED, ciu-P23).** A
container without a Docker healthcheck is satisfied when it is *running*. A
one-shot container (e.g. a `db-init` / `controller_ddl` init-container) that
has **exited 0** is also treated as satisfied — the probe reads
`State.ExitCode == 0` as a clean completion. Only a non-zero exit code or a
container not found is a failure. This fallback has a false-positive gap: a
container WITH a healthcheck that reports `healthy` and only later exits
still satisfies `:healthy` (the `status == 'healthy'` branch is checked
first and never re-examines `Running`), so a container that finished
minutes ago and a container still mid-run can be indistinguishable to a
`:healthy` probe. Every time this fallback is the reason a probe is
satisfied, CIU now prints a `[WARN]` naming the ref and suggesting
`stack:<name>:completed` — the fallback's BEHAVIOR is unchanged (removing it
is V8's own breaking step, not this one), only the warning is new.

**`stack:<name>:completed` (V8-PREP-5, ciu-P23).** A dedicated terminal for
run-to-completion (one-shot) stacks: satisfied when `State.Running == false`
**and** `State.ExitCode == 0`; any other state — still running, or exited
non-zero — is not satisfied, and a missing container is not satisfied
exactly like `:healthy`'s missing-container case. Unlike `:healthy`,
`:completed` NEVER inspects `.State.Health` under any code path, which is
exactly the false-positive gap described above: it cannot be fooled by a
healthcheck that reported healthy before the container exited, because it
never looks at the healthcheck at all. Use `:completed` for any stack whose
phase entry declares `one_shot = true` (see `deploy.phases.<key>.services[]`
in CONFIG.md) instead of `:healthy`.

**Slash-bearing selectors now resolve (O4, ciu-P23).** A selector containing
`/` (e.g. `stack:infra/db-init:healthy`) is resolved against the known set of
declared stack paths (`deploy.phases.*.services[].path` and
`deploy.profiles.*.stacks[]`, aggregated across the whole config); when it
matches a known path, that path's final segment (the same bare-name
convention every slash-free selector already used) is what is actually
looked up as a container. Before ciu-P23 this selector form was
**guaranteed-broken**: the raw slash-bearing string was passed straight into
the container-name builder, which can never match a real container name. A
slash-free selector's resolution is unchanged. An UNKNOWN slash-bearing path
(no declared stack matches it) still resolves to nothing — a genuine typo
still surfaces as "container not found," it is not silently reinterpreted.

**`one_shot = true` cross-reference (O3, ciu-P23).** When a `:healthy` probe
targets a stack whose OWN phase entry declares `one_shot = true`
(`deploy.phases.<key>.services[].one_shot`, see CONFIG.md), CIU prints a
`[WARN]` suggesting the requiring stack switch that ref to `:completed`. This
fires during LIVE PROBING (`probe_ref`/`provisioning.probe_ref`, i.e. `ciu up`
and `ciu check --live`) — it is not a separate static `ciu check` stage
(unlike ciu-P22's `[service.*]` registry lint), since wiring a new static
stage requires touching `deploy.py`'s `action_check`, which is out of scope
for this package. `one_shot` itself is declaration + shape validation only
in this package (`deploy_pkg.phases.service_one_shot`); it does not change
the deploy loop's post-up wait behavior (see CONFIG.md for the key's shape).

### S13.3 — Preflight model (lint-vs-probe split)

Two independent checks run at different times:

1. **Static lint** (`lint=True, probe=False`) — runs **once up-front** for
   the full selection, before any phase starts. Checks: every `requires` entry
   is provided by some stack in the selection; no dependency cycle among
   `stack:<name>:healthy`/`:completed` references. This is a pure config
   check — no Docker or Vault I/O. Exit 2 on failure.

   **Cycle detection resolves bare selectors to full stack paths (O5,
   ciu-P23).** `lint_graph`'s dependency graph is keyed by full repo-relative
   stack path, but the only selector form that has ever resolved to a real
   container is a bare basename (a slash-bearing selector was
   guaranteed-broken until O4 above). Before ciu-P23, a `stack:<bare-name>:…`
   ref never matched a full-path graph key, so a genuine cross-stack cycle
   declared the ONLY way that actually worked for probing silently passed
   lint. `lint_graph` now resolves a bare (or slash-bearing) selector to its
   declared stack path — using the same exact-match-then-unique-basename
   rule as O4's container-name resolution — before adding the graph edge, so
   such a cycle is now correctly detected. An ambiguous basename (two
   declared stacks share it) or a selector matching no declared stack stays
   exactly as unresolved as before this fix: it is never guessed at.

2. **Live probe** (`lint=False, probe=True`) — runs **per-phase**, immediately
   before that phase deploys, after all earlier phases are already up. CIU
   probes only the `requires` of stacks in the current phase. This means on a
   greenfield `ciu up`, providers from phase 1 are running before phase 2's
   requirements are probed — no `--no-preflight` needed.

Both checks are skipped under `--dry-run` (nothing is running to probe) and
under `--no-preflight` (break-glass flag). If the full run is `--no-preflight`,
both checks are bypassed entirely.

- **S13.4** `ciu check [--profile NAME] [--live] [--json]` — validates the
  configuration without deploying (see S13.4a for the full stage list; the
  provisioning graph is one of its stages). Without `--live`: static checks
  only. With `--live`: additionally probes live state for each `requires`
  entry. Exit code: `0` clean · `1` **live probe failure only** · `2` any
  static configuration/validation error. Safe to run in CI against a running
  stack.

### S13.4a — `ciu check`'s config-validation pipeline

`ciu check` walks the whole config pipeline **in memory and side-effect-free**.
It creates no hostdir, materializes no secret, writes no rendered
compose/overlay/configfile, executes no hook `run()`, writes no `__pycache__`
beside an imported hook, and never contacts Docker. This is what makes it a
substitute for using `ciu up --dry-run` as a validation tool: **`--dry-run`
still creates hostdirs and still runs `pre_secrets`/`pre_compose`/
`post_compose` hooks for real**, skipping only `docker compose up`.

Stages, in order, each reusing the same function the real pipeline
(`engine.main_execution`) calls at the corresponding step:

| Stage | Validates | Failure |
|---|---|---|
| `render` | Jinja2 + `$VAR` + TOML parse of the global chain and every selected stack (already performed to produce the check's input) | 2 |
| `shape` | single root key (S3.5), reserved-namespace collision (S3.7), plus the global declared-feature check (S11) | 2 |
| `secrets` | directive grammar and name uniqueness (S4/S4.6), placement (S4.1/S4.5) | 2 |
| `provisioning` | `requires`/`provides` grammar (S13), graph lint, cycles | 2 |
| `governance` | governance table shape and layering (S15.2/S15.10). Resolution only — no cgroup is created and no systemd slice is probed | 2 |
| `configfile` | per-configfile declaration shape, template file existence, absolute `target`, declared `schema` file existence (S5.1). **Existence only — nothing is rendered** | 2 |
| `registry` | the two `[registry.*]` values CIU itself reads, plus any consumer-declared `validate_registry` (S13.4b). **Global scope: runs once per run, not once per stack** — its findings are the only ones carrying no `stack` key | 2 |
| `hooks-load` | every declared hook file exists and exports `run` or a `Hook` class with `run` (S9.1/S9.2). Each file is imported **exactly once per run**, and `run` is located but never called | 2 |
| `hooks-preflight` | each hook's optional `validate_config(config, ctx)` (S9.5) | 2 |
| `compose-render` | full compose-template render against the S4.21-guarded config, in memory. A template that stringifies a secret aborts here | 2 |
| `leak-scan` | S4.22 scan of the rendered text. **Structurally vacuous at check time** — nothing was materialized, so there are no values to search for; the barrier that actually bites here is S4.21's guard in `compose-render` | 2 |
| `consumption` | declared-vs-consumed secret cross-check (S4.20). An undeclared reference is a failure; a declared-but-unconsumed secret is a **warning**, matching the real pipeline's own Step-14 behaviour — and the check cannot see the S5 configfile consumption channel without rendering | 2 (undeclared) / warn (unconsumed) |
| `service-registry` | (ciu-P22, not part of the V8 proposal's own stage numbering — appended last) `[service.*]` identity registry (S3.14) two-directional consistency lint against the selected profile/phase's actual deployment (S3.15). **Registry-declaration-gated**: skipped entirely when `[service.*]` is absent/empty | warn only, both directions |

**Render fidelity.** Two pipeline steps `ciu check` does not run nevertheless
supply values a compose template legitimately reads, so their *pure* halves
are applied to the in-memory config before the render: Step 7's
`auto_generated.*` (S3.9 — build metadata and UID/GID), and Step 8's rewrite
of each `[<…>.hostdir]` declaration to its absolute path (S6.1/S6.2/S1.4).
**No directory is created, seeded, chowned or chmod'ed** — only the path
string is computed. Without this the render stage would fail on most real
stacks for reasons that are artifacts of the check rather than defects in the
config. When `CONTAINER_UID`/`DOCKER_GID` are absent the `auto_generated`
values are skipped with a **note**, not a failure: that is an S2 bootstrap
condition (exit 3), already enforced by `bootstrap_workspace_env`, and does
not belong in the exit-2 configuration taxonomy. A hostdir declaration whose
shape is unusable is left untouched for the real pipeline's own Step-8 error
to name — `ciu check` has no S6 stage.

**Stage coverage:** `ciu check` implements the V8 proposal's stages 1-12.
Stage 7 (`registry`) is scoped to what CIU actually reads — see S13.4b, which
states plainly what it does and does **not** check.

Every stage failure above is exit `2`. Exit `1` is reserved for `--live`'s
live probe failures and is never produced by a static stage; when a static
stage has already failed, the live probe is not attempted at all.

`--json` writes ONE versioned object: `schema_version`, `operation`
(`"config-check"`), `status` (`"pass"`/`"fail"`), `profile`, and `stages` — a
**list** in pipeline order, each entry `{stage, status, findings, notes}`,
where a finding carries `message` plus `stack`/`hook` when scoped to one —
and, since CIU-65, a NOTE carries the same three keys, so a WARN-severity
`validate_config` finding names its hook exactly as an ERROR one does. A
`--live` run additionally carries a top-level `live` key
(`{status, unsatisfied}`) — deliberately not a stage, because it is the one
failure class that maps to exit 1. Under `--json` the action emits no prose
of its own; the orchestrator's own `[INFO]` lines still precede the document
on stdout, exactly as for `ciu graph --format json`.

### S13.4c — `ciu up`'s automatic static preflight (CIU-64, normative)

`ciu up` runs S13.4a's **static** pipeline (never `--live`) before STEP 1 —
before any hostdir is created, any secret materialized, or any container
started — and **refuses on any ERROR-severity finding**, exiting `2`
(S10.3 configuration/validation), the same way it already refuses on an
`[S7.x]` provisioning-graph failure. This is on by DEFAULT: no flag opts in.

Why it is safe to run unconditionally: S13.4a is side-effect-free by
construction (no hostdir, no secret, no compose/overlay write, no hook
`run()`, no Docker contact), which is the property that makes it a preflight
rather than a second deploy. It reuses the SAME rendered selection the deploy
preflights already computed — one render, not two.

- It runs FIRST among `ciu up`'s preflights, so an operator with a
  configuration defect gets the whole S13.4a report in one pass instead of
  being stopped by whichever narrower preflight fires first.
- It runs under `--dry-run` too: a dry run exists precisely to find this
  class of defect, and the check is side-effect-free either way.
- **WARN-severity findings (S9.5) never refuse.** They are printed as
  `note: [WARN] …` and the deploy proceeds. Surfacing them visibly is
  required; swallowing them is not an option, and neither is blocking on
  them.
- `--skip-check` is the break-glass escape, mirroring `--no-preflight`'s
  precedent for the live per-stack requires probe. It ANNOUNCES itself with
  a `[WARN]` line naming what was skipped — a silently skipped gate is a
  gate that is not there.

Rationale: before CIU-64, both the graph lint and every hook's
`validate_config` ran ONLY on the explicit `ciu check` verb, so an operator
deploying straight from `ciu up` got neither — the "relies on someone
remembering" shape CIU's own `--check-env`/`validate_config` machinery exists
to eliminate elsewhere.

- **S13.5** `ciu graph [--format mermaid|dot|json] [--profile NAME] [--phases N,M]`
  — renders the requires/provides dependency graph to STDOUT (no deploy). Edges
  go consumer → provider (the stack whose `provides` contains the ref). A
  requirement that nobody provides is drawn dashed to an `UNPROVIDED` sentinel so
  gaps are visually obvious. Diagnostics go to the logger (stderr); only the
  graph itself goes to stdout so it can be piped directly into documentation.

### S13.4b — `[registry.*]` schema validation (`ciu check` stage 7)

`[registry.*]` is free-form, project-specific cross-stack metadata (S3.7
reserves the namespace; CONFIG.md documents the tables). CIU reads **exactly
two values** out of it, and validates **exactly those two**:

| Key | Type | Constraint | Read by |
|---|---|---|---|
| `[registry.postgresql].database` | string | non-empty | `_probe_pg` — the `psql -d` target of a `pg:schema/<name>` probe (S13.2) |
| `[registry.consul].token_vault_path` | string | non-empty; a valid `str.format` template whose only placeholder is `{svc}` | `_probe_consul` — the Vault path of a `consul:token/<svc>` probe (S13.2) |

Both keys are OPTIONAL; a table may carry any number of other keys, which are
**never** constrained. Every constraint above is grounded in what the probe
does with the value: an unbalanced brace raises `ValueError` from
`str.format`, which the probe does not catch (the probe run dies); any other
placeholder raises `KeyError`/`IndexError`, which the probe catches and then
silently substitutes the default `consul/acl/tokens/{svc}`, reading a
different Vault path than the operator declared. The presence of `{svc}` is
deliberately **not** required — a constant path substitutes cleanly and the
probe demands nothing more.

**CIU ships no model for any other registry table.** The V8 proposal §2.6
sketched models for five provisioning kinds (PostgreSQL, Redis, MinIO,
Consul, Vault); CIU has never read a Redis, MinIO, Vault, or
PostgreSQL-users registry shape, so there is nothing in this repo to validate
against and no model is invented. A guessed schema that is wrong REJECTS
legitimate consumer configs, which is strictly worse than no schema.

**Consumer-owned shapes** (the proposal's Option C) get one additive
extension point. The global config declares a module:

```toml
[ciu]
registry_validator = "infra/registry_validate.py"
```

whose module-level `validate_registry(config) -> list[str]` receives the
whole global config and returns error strings (empty list — or `None` — means
OK). It is imported with bytecode writing suppressed, so stage 7 keeps
S13.4a's side-effect-freedom; `run()`-style execution never happens. A
missing file, an import-time exception, a missing or non-callable
`validate_registry`, a raising validator, or a non-list return are each
reported as a finding rather than aborting the run — including the
`str`-is-iterable trap S9.5 names (a bare string is ONE malformed return, not
one finding per character).

**Optional dependency.** Model validation requires `pydantic >= 2`, shipped
as the optional extra `ciu[registry]`. The import is lazy: when neither
validated table is declared, pydantic is never imported. When one IS declared
and pydantic is absent, `ciu check` FAILS with a finding naming the extra and
its install command — never a silent skip (the same rule S5.7 applies to
`ciu[schema]`). The consumer extension point does not depend on pydantic and
runs either way.

**Scope.** Stage 7 is a GLOBAL-config stage: `registry` is an S3.7 reserved
global namespace (a stack config carrying its own top-level `[registry]`
table already fails S3.5 at stage 2, as a second non-reserved root key), and
the probes read it off the profile config. It therefore runs **once per
`ciu check` run**, including when the selection is empty, and its findings
carry no `stack` key. Failures are exit `2`, like every other static stage.

### S13.6 — Cross-profile producer declaration (`produced_by`, CIU-42)

A stack's `ASK_VAULT` directive may declare, beside the directive (S4.4
inline table), the profile whose deployment PROVISIONS the value at its Vault
path:

```toml
[controller.secrets]
bootstrap_token = { directive = "ASK_VAULT:authentik/bootstrap_token", produced_by = "identity" }
```

The declaration turns an opaque late failure into an upfront refusal. Without
it, a partial selection that excludes the producing profile sails through
every preflight and fails at the consuming stack's materialization with only
the bare path name (`[S4.2] ... absent`) — twice reproduced live in dstdns
P111/P112 (`core,db` selections needing identity-profile paths, resolved in
the field by seeding placeholders). With it:

- **Grammar:** `produced_by` is valid ONLY on `ASK_VAULT` — a producer
  declaration says the path is provisioned by another profile's deployment,
  which only an ASK_VAULT read can depend on (`GEN_TO_VAULT` self-produces;
  local/ephemeral kinds have no cross-profile producer). Value: a non-empty
  string; anything else is a `[S13.6]` grammar abort. A bare-string
  directive has no producer and behaves exactly as before.
- **Preflight:** `producer_preflight` runs beside `vault_preflight` on the
  orchestration deploy path (`ciu up` with phases/profiles — the same surface
  vault_preflight covers; a single-stack `ciu up --dir` dispatch runs neither,
  a pre-existing scope limit stated here rather than hidden). Producer
  PRESENCE is judged by DEPLOYED STACKS, not the profile label: the producer
  passes when any stack it deploys — its `stacks` list or its phases'
  services — is in the selection. An alias profile deploying the same stacks
  therefore satisfies it, and a `--phases` filter that narrowed the
  producer's stacks out still refuses. When no declared producer's stacks are
  selected, it refuses `[S13.6]` naming EVERY unmet tuple: stack, secret
  name, Vault path, producer profile, its stack set, the current selection,
  and both remedies — deploy the producer profile or its stacks, or seed the
  path out-of-band. The default selection (all phases) never refuses:
  everything's provisioning runs.
- **Typo protection:** a `produced_by` naming an undefined profile is a
  configuration error EVEN under the default selection — a typo'd declaration
  must fail loudly, never silently protect nothing. The check walks the
  SELECTED stacks' renders; declarations in non-selected stacks validate when
  those stacks deploy.
- **Scope:** shipped stacks have no CIU secrets surface (S8.6) and are
  skipped; undeclared ASK_VAULT directives keep today's behavior exactly; all
  violations in one run are reported TOGETHER — never one per run.
- **Controlled wrong implementation:** dropping the declaration lookup makes
  the preflight pass where it must refuse — the refusal demonstrably comes
  from the declaration, not from the path.

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

`--thin` selects the **docker-optional push→activate** path (S14.6) instead of
render-on-target: it pushes an artifact and runs the project's shell activation
contract, needing no Docker/Python on the target. It composes with `--host`.

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
| `docker_optional` | No | Advisory flag: this host has no Docker; deploy it with `--thin` (S14.6). CIU nudges (stderr) if the docker `--host` path is used on it, but does not block. |
| `activate` | Yes† | S14.6 activation contract — a string entrypoint (CIU appends the verb) or a per-verb table (`bootstrap`/`apply`/`health`/`rollback`). †Required only for `--thin`. |
| `push_mode` | No | `auto` (default) \| `rsync` \| `scp`. `auto` tries rsync, falls back to tar+scp when rsync is absent on the control host or target (S14.6). |
| `bundle_excludes` | No | List of top-level paths excluded from the pushed bundle (default `[".git"]`). Applied identically to the rsync and tar+scp paths. |
| `secrets` | No | S14.3a — host-scoped local secret directives subtable (ASK_EXTERNAL / GEN_LOCAL only). |

Before any `--host` transport, CIU MUST load the repository's global
configuration successfully. An unreadable or invalid configuration is an exit-2
error; it MUST NOT be replaced with an empty mapping, because host credentials
may be resolved through that configuration.

`[deploy.hosts.<name>.admin]` subtable overrides `ssh_user` / `ssh_key` for the
higher-privilege access plane (`ciu ssh <host> --admin`).

### S14.3a — Host-scoped local secrets (CIU-35)

`[deploy.hosts.<name>.secrets]` is a **host-scoped** secret table for material
that must resolve **before any Vault exists on the target** — the SSH
bootstrap key, a Tailscale single-use authkey, and similar. It is the existing
S4 secret machinery pointed at a new namespace, not a new secret system; once
the host is adopted the same values are movable to Vault by the existing
directives.

- **Closed set.** Each entry is parsed with the existing `S4.2` directive
  grammar (`parse_value`, shared verbatim) and ONLY `ASK_EXTERNAL` and
  `GEN_LOCAL` are accepted at host scope. Any other kind — `ASK_VAULT`,
  `GEN_TO_VAULT`, `ASK_FILE`, `GEN_EPHEMERAL` — is refused with a tagged
  `[S14.3a]` error naming host, entry and reason (Vault-dependent and
  ephemeral kinds are meaningless before a host is adopted). A grammar
  violation is refused the same way.
- **Store namespace.** Materialization writes to
  `<project-store>/hosts/<host>/<entry_name>` (`<repo>/.ciu/secrets/hosts/…`),
  dirs `0700`, atomic write + flock (S4.9/S4.10/S4.26 reused). The per-stack
  global-uniqueness rule S4.6 deliberately does NOT apply across host
  namespaces: two hosts MAY declare the same entry name without collision.
- **Resolution order** is the existing behaviour, reused: `ASK_EXTERNAL`
  resolves from `env[locator]` → `CIU_SECRET_<NAME>` → existing store file →
  interactive prompt (TTY and not `-y`) → tagged `[S4.13]` abort; `GEN_LOCAL`
  reuses the existing store file or generates a fresh token. The shared
  grammar still requires `GEN_LOCAL:<locator>`'s payload; at host scope the
  store path is the entry name, so the locator is documented as **inert**.
- **Explicit-only, values never printed.** `ciu host-secrets <host>
  [--materialize | --list | --path <name>] [-y]` is the only materialization
  path: `--materialize` resolves all declared entries and prints store file
  paths, `--list` prints entry names + store-file existence, `--path` prints
  one store path (for feeding a bootstrap command over `ciu ssh`). Values are
  never printed, and nothing materializes implicitly inside `ssh` / `up
  --host` — the consumer's own bootstrap script decides when.
- **Transport isolation.** `get_host` validates the subtable (a malformed
  table aborts any flow touching the host) but **pops** it before returning:
  a caller asking for connection facts never receives secret directives.

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

### S14.6 — Docker-optional push→activate (`--thin`, normative)

The render-on-target path (S14.2) needs Docker **and** a full Python/CIU install
on the target. That does not fit a shared **Passenger webhoster** — an SSH shell
with only POSIX `sh` + `tar`/`unzip` + `touch`, no Docker and no general-purpose
Python. `ciu up --host <name> --thin` deploys such a host by splitting the deploy
into a **push** and a pluggable **activation contract**. CIU owns transport +
inventory + host-key pinning + Vault-key resolution; the *project* owns
activation (supplied as shell via the `activate` host key). The default docker
`--host` path (S14.2) is untouched — `--thin` is a parallel branch.

**Push.** An artifact (the repo tree, minus `bundle_excludes`, default `.git`)
is shipped to `bundle_dir`. Strategy is `push_mode`:

- **S14.6a** `auto` (default) tries `rsync` (S14.2 machinery, `--exclude` per
  `bundle_excludes`); when `rsync` is absent on the **control host**
  (`FileNotFoundError`) **or** on the **target** (ssh exit code `127`), CIU falls
  back to: build a `tar.gz` locally, `scp` it to `bundle_dir`, then remote
  `tar xzf … -C bundle_dir && rm …`. The fallback needs only `sh`/`tar` on the
  target and rides the ssh daemon's own scp/sftp subsystem (no user-space
  transfer tool on the target). `rsync` forces rsync-only; `scp` forces
  tar+scp-only. Both strategies honour `bundle_excludes`, so they ship an
  identical tree.

**Activation contract.** Instead of the hardcoded `ciu render && ciu up`, CIU
runs one of four verbs — `bootstrap | apply | health | rollback` (the same shape
as the cmru ProjectAdapter) — over `ssh_exec`, as
`cd <bundle_dir> && <activation-cmd> [selection…]` (ONE argv element, so the
remote login shell parses the `cd`/`&&` chain — same rule as S14.2). The
`activate` host key supplies the command(s):

- **S14.6b** A **string** entrypoint — CIU appends the verb:
  `activate = "sh deploy/activate.sh"` becomes `sh deploy/activate.sh apply`.
- **S14.6c** A **per-verb table** — explicit commands:
  `[deploy.hosts.<name>.activate]` with `bootstrap`/`apply`/`health`/`rollback`
  entries (e.g. `apply = "touch tmp/restart.txt"` for a Passenger restart).

CLI-to-verb mapping:

- **S14.6d** `ciu up --host X --thin` pushes, then runs `apply`. `--bootstrap`
  runs `bootstrap` before `apply` (first-time host setup). `--rollback` runs
  `rollback` only (revert to previous release; **no** fresh push).
  `--bootstrap`/`--rollback` are mutually exclusive and require `--thin`.
- **S14.6e** `ciu health --host X --thin` runs the `health` verb (no push).
- Trailing selection flags (e.g. `--profile apps`) are appended to the `apply`
  and `health` verbs so the activation script can act on them.

**S14.6f** A `--thin` deploy whose host declares no `activate` key (or whose
activate table lacks the requested verb) fails fast with exit code **2** and an
actionable message — CIU never guesses an activation command. The S14.4
host-key/Vault/temp-file security envelope applies unchanged to the scp/tar
fallback (fail-closed pinning; key material never logged; mode-0600 temp files
cleaned up in `finally`).

## S15 — Stack-wide resource governance (cgroups)

A stack MAY declare `[<root>.governance]` (stack-scoped per S3.6, like
`[<root>.secrets]`/`[<root>.hooks]` — **not** a top-level `[governance]` in
the stack's own `ciu.toml`, which S3.5 would reject as a second non-reserved
top-level key there) to opt every service of the stack into host-level
cgroup placement and resource ceilings, without the stack author
hand-writing `cgroup_parent`/`mem_limit`/`memswap_limit`/`blkio_config` on
each service.
This is **opt-in and purely additive**: a stack that declares no
`governance` table of its own, and for which no global default resolves
(S15.10), behaves exactly as before — CIU does not even parse/log anything
for it (S15.7).

A **top-level `[governance]` table IS valid in `ciu.global.toml`** (the
global config file, not a stack's `ciu.toml`) — it is a reserved global
namespace exactly like `deploy`/`ciu`/`registry` (S3.7) and serves as the
universal default across every stack that declares none of its own (S15.10).

### S15.1 — Declaration

```toml
[<root>.governance]
enabled = false                 # opt-in; default false
cgroup_parent = ""              # "" = resolve $CGROUP_PARENT_DEV_BACKGROUND (ambient,
                                 # devcontainer.json's containerEnv — see AGENTS.md);
                                 # explicit value always wins. No hardcoded fallback:
                                 # enabled=true with neither set is a [S15.2] error.
mem_limit = "1g"                # default per service
mem_swap_limit = "17g"          # Docker's own combined mem+swap total, NOT swap alone
                                 # (17g here = 1g mem_limit + 16g swap headroom)
mem_reservation = "256m"        # memory.low — ancestor-chain caveat, see S15.16 WARNING
read_iops = 0                   # 0 = derive (S15.4); explicit nonzero value wins
write_iops = 400
io_weight = 0                   # 0 = not set (S15.14); else 10..1000
read_bps = 0                    # 0 = uncapped (S15.15)
write_bps = 0                   # 0 = uncapped (S15.15)
device = ""                     # "" = autodetect (S15.5); explicit value wins
baseline_path = ""              # "" = S15.4 search order; explicit path wins
exempt_services = []            # service names to skip entirely
ksm_optin = ""                  # "" = off (S15.11); path to a universal LD_PRELOAD shim
mem_min = ""                    # "" = not declared; checked not injected — READ THE S15.16 WARNING
```

### S15.2 — Defaults and merge

Unlike the rest of CIU's config (free-form TOML, no key-level schema), the
`governance` table has code-level defaults (`ciu.governance.GOVERNANCE_DEFAULTS`)
because it drives generated compose keys, not pass-through template values.
The stack's declared table is shallow-merged over the defaults above — a
stack sets only the keys it wants to change from the defaults table; any key
it omits falls through. There is no further nesting: every key is a scalar
or a flat list. Two shape checks abort (exit 2) regardless of the no-schema
rule, because they gate a boolean branch and an iteration respectively:
`enabled` MUST be a boolean (a truthy/falsy string like `"false"` would
silently misbehave) and `exempt_services` MUST be a list of strings. A third
check is scoped to one key: `io_weight` (S15.14) MUST be `0` or in `10..1000`
(Docker's own `blkio_config.weight` range) — a value outside that range
would otherwise surface as a `docker compose up` failure far from the typo
that caused it.

### S15.3 — Injection and author-key precedence

When `enabled = true`, the overlay generator (`composefile.generate_overlay`)
injects into **every service enumerated in the rendered base compose file**
(`_compose_service_blocks`, the same enumeration S5.3 configfile fan-out
uses), except services named in `exempt_services` (skipped entirely — no
keys injected):

| Injected key | Source |
|---|---|
| `cgroup_parent` | `governance.cgroup_parent` |
| `mem_limit` | `governance.mem_limit` |
| `memswap_limit` | `governance.mem_swap_limit` — the Compose Specification's actual key has no underscore between "mem" and "swap" (Docker's own combined mem+swap total, not swap alone, so "1g RAM + 16g swap" is `mem_swap_limit = "17g"` in the config table); without this key Docker's stock default applies instead (2x `mem_limit`) |
| `mem_reservation` | `governance.mem_reservation` |
| `blkio_config` | `device_read_iops`/`device_write_iops` (device resolves, S15.5), plus `device_read_bps`/`device_write_bps` when `read_bps`/`write_bps` are nonzero (S15.15), plus `weight` when `io_weight` is nonzero (S15.14, independent of device resolution) — the whole key is omitted only when NONE of those apply |

**Precedence: the stack author's rendered compose always wins.** For each
service, the overlay generator parses that service's block in the
already-rendered `ciu.compose.yml` text (`compose_yaml_text`, already
available to `generate_overlay` — S8.1's rationale for a separate overlay
applies identically here: this is machine-derived wiring, not a template
mutation) and skips any of the five keys above **already present on that
service**. Precedence is per **top-level compose key**, not a deep merge of
`blkio_config`'s sub-fields — an author who sets `blkio_config` at all (even
partially) fully owns that key for that service; governance will not merge
into it. A service with every one of the five keys already author-set
receives no governance fragment at all (and does not count toward
`services_injected` in the S15.7 log line).

This mirrors S4.17/S8.1's separate-overlay rationale: the rendered
`ciu.compose.yml` remains byte-exact stack-author output; all governance
wiring — like secret/configfile wiring — lives only in the generated overlay.

### S15.4 — `read_iops` derivation

`read_iops = 0` (the default) means "derive": CIU reads a shell-style
io-baseline file (`RIOPS_MAX=<int>`, written by `ciu iops-baseline` — S15.9 —
or by an external host measurement) and computes `RIOPS_MAX * 2 / 3`
(integer division ≈ 66%). Host cgroup tooling caps the same device in the same
60–80% band, so container and non-CIU workloads apply a consistent fraction of
measured disk capacity.

**Baseline file resolution order** (CIU ships as a wheel to arbitrary hosts,
so the location must not couple to any single host's tooling; the **first
existing file wins**):

1. governance table key `baseline_path` (when non-empty)
2. env `CIU_GOV_BASELINE_PATH` (when set)
3. `/var/lib/ciu/io-baseline.env` (neutral default; `ciu iops-baseline`
   writes here)
4. `/var/lib/mdt/io-baseline.env` (mdt host-setup's baseline, when that
   companion is installed)

Step 4 is a search **candidate, not a dependency**: CIU never requires mdt: it
reuses a measurement already present on the host rather than saturating the
disk a second time to learn the same number. It ranks last so an explicit
`ciu iops-baseline` always wins.

A configured but non-existent path (steps 1–2) falls through to the next
candidate — resolution is by existence, not by declaration. If no candidate
exists, or the resolved file has no `RIOPS_MAX` line, CIU falls back to
`200` and logs a notice as part of the S15.7 summary line (never a silent
fallback; the no-file note lists the searched paths). Any nonzero
`read_iops` in the stack config is explicit and always wins over derivation.

**Measurement provenance (`MEASURE_METHOD`).** The file format is a handful of
`KEY=VALUE` lines and says nothing about how the numbers were produced — but
different fio invocations of "randread 4k" do not measure the same thing, so a
reader that assumes one and gets the other derives a cap that is wrong in a
direction it cannot see. Writers therefore emit a `MEASURE_METHOD` token, and
derivation reports it:

| Token | Measurement | Bias |
|---|---|---|
| `burst-v1` | `ciu iops-baseline`: 1G span, 10s, **no** `ramp_time` | reads **high** on a VM — the window includes the cache-warm burst, and `direct=1` bypasses the guest page cache but not the hypervisor's |
| `sustained-v3` | mdt host-setup `mdt-io-baseline.py`: 4G span, 10s ramp + 40s measure, incompressible buffers | conservative; the better input to a cap |

A file with **no** marker predates it or came from a third tool: CIU still
derives from it, and the note says `method=UNKNOWN` rather than implying a
provenance it cannot verify. An unrecognised token derives too, flagged
`UNRECOGNISED`. Neither is an error — the derived value is usable, its
confidence is simply lower, and the S15.7 summary line carries that.

### S15.5 — `device` autodetection

`device = ""` (the default) means autodetect: CIU runs
`findmnt -no SOURCE --target /var/lib/docker` and resolves a partition
source to its parent disk (`/dev/vda1` → `/dev/vda`, `/dev/nvme0n1p1` →
`/dev/nvme0n1`; LVM/mapper sources and already-whole-disk paths pass through
unchanged) — `blkio_config` device paths cgroup-v2 `io.max` accounting on
this host applies at the whole-disk level, not per-partition. An explicit
`device` value in the stack config always wins over autodetection. If
autodetection fails for any reason (`findmnt` missing, non-Linux, non-zero
exit, unparseable/non-`/dev` output), `blkio_config` is skipped entirely for
every service **this run** (cgroup_parent/mem_limit/memswap_limit/
mem_reservation are still injected) and the S15.7 summary line names the
failure.

### S15.6 — `ciu env generate` integration

`ciu env generate` (`workspace_env.generate_ciu_env`) additionally derives
`CIU_GOV_READ_IOPS` (via the same S15.4 formula, always in "derive" mode —
`ciu.env` is the machine-identity layer, S2.7, with no per-stack `read_iops`
override reachable there) and writes it into `ciu.env` for shell/template
consumption. This is a convenience export only: the overlay generator
(S15.3/S15.4) reads the baseline file and `findmnt` directly and does **not**
depend on `ciu.env` carrying this value — governance still works correctly
on a stack run without a preceding `ciu env generate`/regen. A pre-set
`CIU_GOV_READ_IOPS` in the environment always wins (S2.7).

### S15.7 — Logging

Exactly one summary line is logged per `generate_overlay` call **when the
stack declares a `governance` table at all** (present-but-`enabled = false`
still logs one "disabled" line; a stack with no `governance` table logs
nothing and pays no computation cost — S15 is fully zero-footprint for the
overwhelming majority of stacks that never opt in). When enabled, the line
names every resolved value (`cgroup_parent`, `mem_limit`, `mem_swap_limit`,
`mem_reservation`, declared `mem_min` (S15.16, or "not declared"), resolved `read_iops` + its
source, `write_iops`, `io_weight` (S15.14, or "not set"), `read_bps`/
`write_bps` (S15.15, or "uncapped"), resolved `device` + its source or
failure reason, `ksm_optin` (S15.11, or "off"), and the count of services
injected vs. exempted).

### S15.8 — Rationale: `cgroup_parent` requires a pre-existing systemd slice

`cgroup_parent` only *places* a container's cgroup under the named systemd
slice; it does not itself create or configure that slice. With the systemd
cgroup driver (this host: Debian 13, docker 29, cgroup v2), a named slice
that has **no** corresponding static unit file (e.g.
`/etc/systemd/system/dev-background.slice`) is **implicitly, transiently
created by systemd on first reference** — with no resource limits of its
own (no `MemoryMax`, `IOWeight`, `CPUWeight`, etc.). In that case
`cgroup_parent = "dev-background.slice"` still groups the stack's containers
together under that name, but the host-level ceiling the operator intended
(defined in a real slice unit, provisioned out-of-band — see
`modern-debian-tools-python-debug/host-setup/` for a worked example of
authoring such units) silently does not apply: the containers run
**unconfined** at the slice level even though the compose file "looks"
governed. This is a
degradation, not a failure — `composefile.generate_overlay` still has no way
to detect a missing systemd unit from inside a container-facing overlay
generator (no host access there); **S15.12 closes this gap at deploy time
instead**, where profile-based `ciu up` *does* have host access, before any container
starts. `mem_limit`/`memswap_limit`/`mem_reservation`/`blkio_config` are
per-container (not slice-dependent) and always apply regardless.

### S15.9 — `ciu iops-baseline` (self-contained measurement)

`ciu iops-baseline [--path PATH] [--runtime N] [--force]` measures the
disk's randread IOPS ceiling with fio and writes the S15.4 baseline file —
so a wheel-installed CIU can produce its own baseline with no external
script. It is **explicit opt-in only**: CIU MUST NOT run it automatically
(not from `ciu env generate`, not from the overlay generator). Default
output is the neutral S15.4 location `/var/lib/ciu/io-baseline.env`
(`--path` overrides); the file carries `RIOPS_MAX=<int>`,
`RIOPS_ENGINE=<engine>` (so a psync-derived number is identifiable later),
`MEASURE_METHOD=burst-v1` and `MEASURED_AT=<UTC>` — written atomically
(tmp + `os.replace`).

`MEASURE_METHOD` is mandatory output (S15.4 "Measurement provenance"): the
arguments in requirement 4 below are an **unramped 1G/10s** measurement, which
a reader cannot distinguish from a sustained one by looking at the numbers.
The marker is what lets derivation say so.

Behavioral requirements (each learned from a live incident or a fio
footgun):

1. **fio absent** (`shutil.which("fio")` is `None`): print a clear notice
   ("fio not installed — skipped; derivation will use fallback 200") and
   exit **0** without writing anything.
2. **Engine**: use `--ioengine=libaio` (checked via `fio --enghelp`). When
   libaio is unavailable, fall back to `psync` **with a warning** that the
   result is queue-depth-1 latency, not the device's ceiling — fio's default
   psync engine silently caps iodepth at 1.
3. **JSON parsing**: fio runs with `--output=<tmpfile> --output-format=json`
   and the result is parsed **from the first `{`** in that file — fio
   prepends human `note: ...` lines even into the output file, which breaks
   a naive `json.load` (hit live on the origin host).
4. **fio arguments**: `--name=riops-baseline --size=1G --rw=randread
   --bs=4k --direct=1 --iodepth=32 --numjobs=1 --time_based --runtime=<N>`
   (default runtime 10 s). The scratch test file lives alongside the output
   path (or `/var/tmp` when that directory is not writable) and is ALWAYS
   deleted afterward, success or failure.
5. **Freshness**: an existing result younger than 30 days is kept (notice +
   exit 0) unless `--force`.
6. **Impact warning**: the command prints that it generates ~`runtime`
   seconds of saturating read I/O before running — do not run it while
   latency-sensitive workloads are active.

Exit codes (S10.3): `0` success or benign no-op (fio absent, fresh result
kept) · `1` measurement/write failure (fio non-zero, unparseable JSON,
unwritable output) · `2` invalid arguments.

### S15.10 — Global default (`ciu.global.toml` top-level `[governance]`)

Writing `[<root>.governance]` into every stack that wants the same policy is
pure boilerplate once a host has more than a couple of stacks. CIU resolves
governance for a stack from two layers, shallow-merged, last-wins —
`GOVERNANCE_DEFAULTS` (S15.2) -> global `[governance]` -> stack
`[<root>.governance]` (`governance.resolve_stack_governance`):

1. **Global default**: a bare top-level `[governance]` table in
   `ciu.global.toml` (reserved namespace, S3.7), when present, is the BASE
   layer.
2. **Stack-scoped table**, from either the stack's own `ciu.toml` or
   `ciu.global.toml`'s root-key-scoped section (`[<root>.governance]`) — both
   already folded into `merged[root_key]` by the existing S3.3 merge, so this
   layer is unchanged from S15.1–S15.3 — is applied OVER the base layer, key
   by key.
3. **Neither present anywhere**: governance stays disabled, exactly as
   before this section existed (no behavior change for hosts that don't use
   it).

**CIU-13 (fixed 2026-08-03).** This used to be an all-or-nothing choice
rather than a merge: a stack that declared its own `[<root>.governance]`
table, however small, fully replaced the global default — resolved against
`GOVERNANCE_DEFAULTS` alone, never inheriting anything from the global table.
Reported live by dstdns: the global table set `enabled = true` plus
`cgroup_parent`/`ksm_optin`/`mem_limit`/`device`; a stack restated only
`mem_limit` to raise it for one test-runner container. Because the stack
table "won" wholesale, every other global key — including `enabled` —
vanished, and the merge against `GOVERNANCE_DEFAULTS` (`enabled = false`)
resolved to **governance silently disabled**: the container came back with
`CgroupParent=` and `Memory=0`, completely unconfined, on a host whose whole
point is bounding dev-tier workloads. The log line
(`[GOVERNANCE] disabled ([<root>.governance].enabled is false)`) is
indistinguishable from a deliberate opt-out, so nothing about the run
signaled that a one-key tuning edit had turned governance off entirely.

Making S15.10 itself a merge layer (mirroring S15.2's own merge rule exactly)
fixes this: a stack now only needs to restate the keys it actually wants to
change, and every other key still comes from the global policy. The one-key
`mem_limit` override above now raises the limit while keeping `enabled`,
`cgroup_parent`, `ksm_optin`, and `device` exactly as the global table set
them. A stack can still opt out entirely by restating `enabled = false`
itself — that key wins the merge like any other, it just no longer happens
**by accident** as a side effect of setting an unrelated key.

Practical effect (unchanged from before the fix): a host with N stacks that
all want the same governance policy writes it **once**, as `[governance]` in
`ciu.global.toml`; a stack that needs to differ restates only the keys that
actually differ — including, now, just one key of a many-key policy.

### S15.11 — KSM opt-in injection (`ksm_optin`)

Governance key `ksm_optin` (default `""` = off; requires
`enabled = true`): either the literal `"builtin"` (S15.17 — CIU builds and
caches the shim it ships; the recommended value) or a repo-relative or absolute
path to a **universal** (dependency-free, built `-nostdlib`, zero `DT_NEEDED`)
LD_PRELOAD shim that calls `prctl(PR_SET_MEMORY_MERGE)` (kernel ≥ 6.4). When
set, the overlay injects into every non-exempt service:

- `environment: ["LD_PRELOAD=/opt/ksm/ksm-optin.so"]`
- a read-only bind of the shim's PHYSICAL path (S1.3/S1.4) to
  `/opt/ksm/ksm-optin.so`.

The configured path is fail-closed: before CIU emits the overlay, the resolved
physical path MUST be an existing regular file (`Path.is_file()`). A missing
path, directory, or broken symlink is a configuration error (S10.3 exit 2)
naming `governance.ksm_optin` and the resolved path. CIU MUST NOT rely on
Docker's bind-mount behavior, which would otherwise create a phantom directory
and leave `LD_PRELOAD` ineffective. Set `ksm_optin = ""` to disable the opt-in.

Rules: the shim MUST be dependency-free — a libc-linked shim is FATAL
under the other libc's loader (measured: glibc `ld.so` exits 127 on a
musl-linked preload; a zero-dependency `.so` loads under both).
Statically-linked programs never run a dynamic loader, so the injection
is inert for them. `environment`/`volumes` are MERGE keys in the overlay
(appended to configfile mounts, per-key env merge in compose) — the
S15.3 author-precedence rule for scalar keys does not apply.
`exempt_services` opts individual services out. The one-line S15.7
summary includes `ksm_optin=<path|off>`.

### S15.17 — CIU-shipped shim, built on demand (`ksm_optin = "builtin"`)

CIU ships the shim SOURCE as package data and builds it on demand, so a
consumer no longer maintains its own copy of a build artifact whose
correctness is subtle (S15.11's dependency-free rule) and whose absence is
silent (CIU-14). `ksm_optin = "builtin"` is the sentinel; every other
non-empty value keeps its S15.11 meaning as a consumer-supplied path, so this
is strictly additive.

Build (`ciu ksm build [--force]`, or implicitly during render when no cached
artifact exists):

- The artifact is cached at `<repo>/.ciu/ksm/ksm-optin-<machine>-<digest>.so`,
  where `<machine>` is `uname -m` and `<digest>` is a SHA-256 prefix of the
  shipped source. Arch keying prevents silently reusing an x86-64 object on
  aarch64; the digest makes a CIU upgrade that changes the shim
  self-invalidating.
- It MUST live under the repo root. The path becomes a Docker bind SOURCE, so
  it is translated by S1.3/S1.4; a location outside the repo (e.g.
  `$XDG_CACHE_HOME`) would pass through untranslated and address a host path
  that does not exist, which Docker then creates as an empty directory — CIU-14
  again, from the other direction.
- The compile runs in a container (`gcc:13-bookworm`), so the HOST needs no
  toolchain — the requirement is Docker, not gcc. Its bind source is the
  daemon-visible path of the cache dir; passing the container-local path would
  write the output where the caller cannot see it and appear to succeed.
- The build is **verified before the artifact is usable**: a non-empty 64-bit
  ELF object with ZERO `DT_NEEDED` entries. A build that fails verification is
  DELETED, never cached — a libc-linked shim is not a degraded shim, it is a
  container that will not start under the other libc. An artifact that cannot
  be parsed is refused too: "could not verify" MUST NOT be treated as "verified
  good". A cache HIT is re-verified on every use for the same reason.
- Every failure (no Docker, image unavailable offline, compile error, timeout,
  failed verification) is a configuration/environment error naming the cause;
  CIU never proceeds with an unverified shim.

### S15.18 — Ad-hoc KSM override (`--ksm` / `--no-ksm`, `CIU_KSM`)

A **run-scoped** override of `governance.ksm_optin`, for the case where the
configured policy is right but this one invocation should differ (measuring the
delta, reproducing a report, deploying onto a host without KSM). It **never
writes to the TOML layer** — the configured value is untouched on disk and the
very next run uses it again.

`ciu up --ksm` / `--no-ksm` set the ambient `CIU_KSM`, which
`governance.resolve_ksm_optin` reads: the flag and the env var are one
resolution point, not two that can disagree, and every other ambient `CIU_*`
toggle already works this way. Passing both flags is an error (exit 2).

`CIU_KSM` accepts `builtin`/`1`/`on`/`true`/`yes` (inject the shipped shim),
`0`/`off`/`false`/`no`/empty (**passthrough** — inject nothing), or any other
value as an explicit shim path.

**`off` is PASSTHROUGH, not "KSM disabled".** All CIU decides is whether to
INJECT its own opt-in; it cannot un-opt-in a process. If the consumer's image
enables KSM itself — its entrypoint calls `prctl`, or the application does —
`off` changes nothing and the containers stay merged. There is deliberately no
force-off value: implementing one (`prctl(PR_SET_MEMORY_MERGE, 0)` from a
wrapper) would be BEST-EFFORT at most, since the image's own code runs after
CIU's and can re-enable. A value that reads as a guarantee CIU cannot keep is
worse than its absence. **Unset returns the configured value unchanged** — the override is opt-in
and never invents a policy the config did not state. The S15.7 notes line
reports the EFFECTIVE value, so a run under an override says so.

### S15.19 — Per-service memory policy (`governance.memory_profile`)

`ksm_optin` (S15.11) decides WHETHER a shim is injected for the estate;
`memory_profile` decides PER SERVICE which strategy applies, because the right
choice is a property of the **image**, not of the estate.

```toml
[governance.memory_profile.default]
ksm = "preload"                      # preload | off
[governance.memory_profile.services.otel-collector-node]
ksm = "off"
```

Precedence: `services.<name>.ksm` → `default.ksm` → `"preload"`. That final
fallback is a genuine POLICY default (§4.2a's legitimate case): with governance
enabled and a shim configured, injecting is correct absent any statement, and it
substitutes for no fact held elsewhere.

`ksm = "off"` suppresses ONLY the KSM injection; the service keeps
`cgroup_parent`/`mem_limit`/`blkio_config`. This is the finer-grained control
`exempt_services` (which removes a service from ALL governance) could never
express. An unknown strategy is a hard error, never a silent fallback to the
default — `ksm = "wraper"` quietly yielding preload is exactly the
silent-wrong-answer this layer exists to prevent.

### S15.20 — The exec-wrapper (`ksm = "wrapper"`)

`LD_PRELOAD` needs a dynamic loader in the target, so it cannot reach a
statically-linked binary. The exec-wrapper can: it calls
`prctl(PR_SET_MEMORY_MERGE)` and then `execve`s the real program, and the flag
**survives `execve`** (measured) — so the workload runs opted in whatever its
linkage.

CIU ships the wrapper SOURCE and builds it on demand into `.ciu/ksm/`, with the
same arch+digest cache keying and verify-before-use rules as the shim (S15.17).
One difference: the wrapper may link libc freely. The shim's zero-`DT_NEEDED`
rule exists because it must load inside *another process* under either libc; the
wrapper only has to RUN.

**Wrapping means RE-STATING the original entrypoint.** Compose's `entrypoint:`
REPLACES the image's ENTRYPOINT — there is no prepend directive. Measured:
`[wrapper]` alone works only for an image that declares no ENTRYPOINT (its CMD
then flows through as arguments); for an image that declares one it fails
outright, `execvp` on the first CMD token, because CMD is arguments and not a
program. `[wrapper, *original]` works in both cases and is what CIU emits.

CIU discovers the original with `docker image inspect`. An image it cannot
inspect is a **refusal**, never a guess: re-stating an entrypoint we could not
read would either drop the original (the container never starts) or invent one.
A memory optimisation must never risk that.

**Drift.** Re-stating FREEZES the entrypoint into rendered compose, so an image
later rebuilt with a different one leaves the deployed container invoking the
OLD command — silently. CIU records an entrypoint fingerprint at render and
`check_entrypoint_drift` compares it, reporting drift AND reporting an
uninspectable image as unverified rather than unchanged (the CIU-15 lesson).
Known limit, stated because it is otherwise invisible: the comparison is of the
entrypoint ARRAY only, so `["/entrypoint.sh"]` whose *script contents* change is
byte-identical here and is not detected.

**`wrapper` is opt-in per service and is never a default.** `preload` is
additive (an env var and a read-only bind) and inert when it does not apply, so
its failure mode is "no benefit"; `wrapper` replaces the entrypoint, so its
failure mode is "the container does not start". Choose it by MEASURING
`ksm_merge_any` on the workload PID — not by guessing which images are static.
Measurement on one estate found `LD_PRELOAD`'s reach far wider than assumed
(one dynamically-linked process anywhere in the startup chain opts in and every
static binary it later `exec`s inherits the flag), so few services need it
there. That is an estate fact, not a CIU one: a consumer whose images are
static-only has no other way to opt in. Full data: dstdns
`docs/KSM-OPTIN-MEASUREMENTS.md`; design discussion: `docs/DESIGN-NOTES.md` D8.

### S15.12 — Named-slice existence preflight (D-G9 check 1)

Closes the S15.8 gap: for every selected stack whose resolved governance
table is `enabled = true`, `ciu up` first resolves the effective
`cgroup_parent` (`governance.resolve_cgroup_parent` — explicit stack config,
else the ambient `$CGROUP_PARENT_DEV_BACKGROUND`, else `ValueError`: S15.2's
no-hardcoded-fallback rule applies here too) and, when that resolves to a
`*.slice` name, probes the target host's systemd for that unit **before any
deploy phase starts** (`deploy.governance_slice_preflight`, alongside
`vault_preflight` / `provisioning_preflight` / `registry_preflight`).
**There is no more "CIU-shipped default, skip it" exemption** —
`GOVERNANCE_DEFAULTS["cgroup_parent"]` no longer hardcodes a slice name, so
every resolved slice gets the same check, including whatever the ambient
default resolves to (previously the single most common, and single
unchecked, case):

- **systemd present, slice loaded** (`systemctl show <slice>
  --property=LoadState` reports `LoadState=loaded`) — pass, one `[INFO]`
  line per slice.
- **systemd present, slice NOT loaded** — fail closed: `ValueError`
  (`[S15.G9-1]`, S10.3 exit 2) naming the missing slice and every stack that
  would place a container under it, before `up` starts anything. This is the
  case S15.8 describes: Docker/systemd would otherwise auto-create the slice
  transiently with no limits, and the deploy would proceed as if it were
  governed.
- **no `systemctl` on the host at all** (`shutil.which("systemctl") is
  None`), **or `systemctl` is present but systemd is not actually PID 1 in
  this mount namespace** (`governance._systemd_is_pid1()` is `False` —
  `sd_booted(3)`'s own check, `/run/systemd/system` existence) — skip with
  an `[INFO]` note, not a failure: a non-systemd host (CI runner, macOS, a
  minimal container) cannot honor governance slices either way, so there is
  nothing to enforce. The second case matters in practice: several
  devcontainer base images ship a `systemctl` at that path which, when
  systemd isn't running, prints a fixed human-readable notice and exits `0`
  rather than erroring or being absent — checking `which()` alone would
  read that notice as a definitive `LoadState=`/`MemoryMin=` **absence**
  (i.e. "missing"/"no floor") instead of "cannot tell," turning an
  inconclusive environment into a false `[S15.G9-1]`/`[S15.16]` abort. Found
  and fixed 2026-08-03 while auditing whether these preflights work from
  inside the project's own devcontainer (they didn't, until this check was
  added).
- Multiple stacks naming the **same** slice are checked once, not once per
  stack (`governance.check_slice_unit`).

Skipped entirely under `--no-preflight` (the same break-glass flag
`provisioning_preflight` honors) — an explicit operator opt-out, not a
default.

### S15.13 — Unknown-key warning in `[<root>.governance]` (D-G9 check 2)

S15.2 keeps `resolve_config` permissive by design (unknown keys pass
through unchanged — a newer stack config running against an older CIU is a
legitimate case, and a hard reject there would break forward-compat). But a
silently-swallowed unknown key is also exactly how a typo
(`cgroup_parnet`) or a key meant for a different table goes unnoticed
indefinitely. `resolve_config` now prints one `[WARN] [S15.2]` line naming
every key present in the raw table that is not in `GOVERNANCE_DEFAULTS`,
while still returning normally — the DEFAULT behavior stays a warning, never
a raise, so forward-compat is preserved. An opt-in `strict_unknown_keys=True`
keyword turns the same condition into a `ValueError` instead, for callers
that want to hard-fail on it; nothing in CIU sets this by default.

### S15.14 — Proportional IO share (`io_weight`)

`io_weight` (default `0` = not set) declares the container's share of the
block device's IO bandwidth relative to its siblings, on Docker/compose's
own `blkio_config.weight` scale (`10..1000`; validated at S15.2). When
nonzero, the overlay injects `blkio_config.weight = io_weight` into every
non-exempt service — independent of whether a `device` resolves at all
(S15.5 is only relevant to the per-device `device_read_iops`/
`device_write_iops`/`device_read_bps`/`device_write_bps` fields; `weight`
applies to the whole container).

**CAVEAT — this key can be silently inert.** cgroup-v2 has more than one IO
controller, and only ONE is active per block device at a time. `io.weight`
is the **iocost** controller's proportional-share file; if the device's
active IO scheduler is instead **BFQ** (a common default), BFQ reads its
OWN file, `io.bfq.weight` (a different scale, `1..1000` with its own
default of `100`), and does not consult `io.weight` at all — a container
can have `io_weight = 1000` and see **zero** effect on a BFQ-scheduled
device. CIU cannot detect the active scheduler from inside a container-facing
overlay generator (the same class of gap S15.8 documents for
`cgroup_parent`); this is documentation, not a runtime check. Verify which
controller is active with `cat /sys/block/<dev>/queue/scheduler` (bfq in
brackets means BFQ is active) before relying on `io_weight` for anything
that matters, and inspect the container's own `io.bfq.weight` cgroup file
post-start if so.

### S15.15 — Bandwidth caps (`read_bps` / `write_bps`)

`read_bps` / `write_bps` (bytes/sec; default `0` = uncapped) declare
per-device bandwidth ceilings, injected as `blkio_config.device_read_bps` /
`device_write_bps` — the same `device` (S15.5) and per-device list shape as
the pre-existing `read_iops`/`write_iops` keys, and gated the same way: no
resolved device means no `device_read_bps`/`device_write_bps` fields (device
resolution failure is reported once, in the S15.7 summary line, same as
today for the iops caps).

Unlike `read_iops`, there is no baseline-derived default for either key: the
`ciu iops-baseline` measurement (S15.9) and its `RIOPS_MAX`-derived formula
(S15.4) are IOPS-specific — a bandwidth ceiling pulled from that number
would not be measuring the thing it caps. `0` (uncapped) is therefore the
only honest default until a bandwidth-specific baseline exists; both keys
are explicit-opt-in-only.

### S15.16 — Declared memory floor (`mem_min`) and its preflight (D-G9 check 3)

> **⚠ WARNING — read this before trusting a "MemoryMin= OK" verdict.**
> cgroup v2's memory protection is NOT a single-level check: per the
> kernel's own documentation, *"effective min/low boundary is limited by
> memory.min/memory.low values of **all ancestor cgroups**"* — a floor set
> on one slice provides ZERO real protection if **any** cgroup above it in
> the chain, all the way to the cgroup root, has no floor of its own
> (memory.min/low default to `0` = no protection, and `0` anywhere in the
> chain caps everything below it to `0`, regardless of what a deeper level
> declares). This preflight therefore walks the **entire** ancestor chain
> (`governance.slice_ancestor_chain` — systemd's dash-derived naming makes
> this a pure string operation, no D-Bus tree lookup needed —
> `governance.check_memory_min_ancestor_chain`), not just the one slice
> `cgroup_parent` resolves to; it still can't correct the kernel's own
> full proportional-overcommit-under-contention math, so treat a pass as
> "every ancestor budgets at least this much" (exact in the common
> uncontended case, conservative otherwise), not as a bit-for-bit kernel
> simulation.
>
> **This is not hypothetical — it is this project's OWN default state.**
> `modern-debian-tools-python-debug/host-setup/units/dev.slice.in` (the
> parent of both dev-tier slices) sets ONLY IO ceilings — no
> `MemoryMin`/`MemoryLow` at all. `dev-background.slice.in` sets
> `MemoryHigh`/`MemoryMax`/`MemorySwapMax` but **no `MemoryMin` and no
> `MemoryLow`**. `dev-interactive.slice.in` sets `MemoryLow` but still no
> `MemoryMin`. So on a host running the shipped mdt dev-tier config exactly
> as-is, `memory.min` protection is a complete no-op **anywhere** under
> `dev.slice`, and this preflight will correctly report it as such (`FAIL —
> dev.slice: MemoryMin=infinity`), not silently pass because
> `dev-background.slice` itself happens to be configured. The identical
> caveat applies to the PRE-EXISTING `mem_reservation` key (`memory.low` —
> a "best-effort" protection with the exact same ancestor-chain rule, but
> which this preflight does NOT check — S15.16 covers `mem_min` only): it
> has been injected into every governed container's compose config since
> before `mem_min` existed, but under `dev-background.slice` as shipped
> today, it has never had any real kernel effect either, for the same
> reason.
>
> Closing this for real means EVERY ancestor from the cgroup root down to
> the placed slice needs its own nonzero `MemoryMin=`/`MemoryLow=` — a
> one-time, host-wide, shared-unit change (editing `dev.slice`/
> `dev-background.slice`/`dev-interactive.slice` themselves), **not**
> something any single stack's `[<root>.governance]` table, or a fix inside
> CIU, can provide per-deploy. See `docs/DESIGN-NOTES.md` D1/D3/D4/D5 for the
> fuller design discussion, including why this is specific to the two
> protection knobs (`memory.min`/`memory.low`) and not e.g. `mem_limit`/
> `cpu_weight`/`io_weight`.

`mem_min` (a Docker-style size string — `"2g"`, `"512m"` — or `""` = not
declared) states an INTENDED cgroup-v2 `memory.min` floor for the stack.
Unlike every other governance key, it is **never injected into the
overlay**: Docker/compose has no field for a per-container `memory.min` (the
closest existing keys, `mem_limit`/`mem_reservation`, map to `memory.max`/
`memory.low` — a ceiling and a soft floor, not the hard floor `memory.min`
provides). A floor only has effect at the SLICE a container is placed
under (S15.8), and CIU's `cgroup_parent` only PLACES a container there — it
never configures that slice's own resource properties. `mem_min` is
therefore stated intent, checked rather than enforced.

`governance_slice_preflight` (`deploy.py`, the same function that runs
S15.12's slice-existence check) additionally collects every enabled stack's
declared `mem_min`, converts it to bytes (`governance.parse_size_to_bytes`
— an invalid size string aborts immediately, `[S15.16]`, naming the stack
and the unparseable value), and — for every resolved slice that DID pass the
S15.12 existence check (a missing slice already aborts on its own; checking
`mem_min` for a slice that doesn't exist would add nothing) — walks that
slice's **full ancestor chain**, derived purely from its name
(`governance.slice_ancestor_chain`; e.g. `dev-background.slice` →
`["dev-background.slice", "dev.slice"]`), probing every level's live
`MemoryMin=` via `systemctl show <unit> --property=MemoryMin`
(`governance.check_memory_min_ancestor_chain`, built on the single-slice
primitive `governance.check_slice_memory_min`). ANY inadequate link anywhere
in the chain fails the whole check — the failure message lists every
inadequate ancestor found, not just the first, so an operator sees the full
picture in one preflight run. Two or more stacks sharing one slice with
different declared `mem_min` values are checked ONCE, against the
**maximum** of the declared floors (the slice must satisfy the stricter
expectation to satisfy both).

Outcomes mirror S15.12's shape exactly:

- **No `systemctl` on the host, or `systemctl` present but systemd is not
  PID 1 here** — skip with an `[INFO]` note (same rationale and same
  `_systemd_is_pid1()` check as S15.12: a non-systemd host, or a
  devcontainer's non-systemd `systemctl` shim, cannot honor a slice-level
  floor either way).
- **`MemoryMin=` meets or exceeds the required bytes** — pass, one `[INFO]`
  line per slice.
- **`MemoryMin=` is `infinity`, `0`, unparseable, or below the required
  bytes anywhere in the ancestor chain** — `[S15.16]` naming every
  under-provisioned slice, the stacks that declared a floor for it, and the
  required byte count, via `warn_policy.warn_or_raise` (S10.6): always
  logged as `[WARN]`; by DEFAULT (`ciu.exit_on` = `"ERROR"`) it does NOT
  raise. `ciu.exit_on = "WARN"` makes it raise (`ValueError`, S10.3 exit
  2) for an operator who wants this fail-fast; `"NEVER"` suppresses even
  the WARN-level abort entirely for this and every other S10.6 site.
  `infinity` and
  `0` are both treated as "no floor" — systemd reports `0` for both an
  explicit "no protection" and a genuinely unset property, and there is no
  way to tell those apart from outside the unit, so this fails toward the
  safer reading. (An unparseable `mem_min` **size string** itself — not the
  live property — is a separate, unconditional `ValueError`: a typo in the
  stack's own config is a shape error, not a judgment call, so it is never
  softened by S10.6.)

The abort message is deliberately explicit about WHO can fix this and how:
add a matching `MemoryMin=` to the slice's systemd unit (a drop-in under
`/etc/systemd/system/<slice>.d/`, then `systemctl daemon-reload`), or lower/
remove the `mem_min` declaration if no floor is actually required. A
host-side companion — e.g. `modern-debian-tools-python-debug`'s
`host-setup`, which already provisions parameterized dev-tier slice units —
can automate this provisioning, but **CIU never depends on one being
present**: `mem_min` is meaningless (and silently un-preflighted, same as
the general S15.12 "no systemctl" skip) on any host without a systemd cgroup
driver, and CIU functions with or without such a companion either way.

Skipped entirely under `--no-preflight`, same as S15.12 (they share one
function and one break-glass flag).

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
`CIU_SERVICES_PROFILE`, numeric phases, three hook points
(`pre_secrets`/`pre_compose`/`post_compose`) with structured-only returns,
hostdir inline options (`uid`/`gid`/`mode`/`seed`) + helper-container
provisioning (S6.5), `ciu secrets` subcommands, exit-code contract,
leak scan, native-host parity (S1.9), `ciu env generate` as the single
bootstrap (S2.8), unified `ciu.`-prefixed file naming (`ciu.global.*`,
`ciu.compose.yml[.j2]`, `ciu.env`, `.ciu/ciu.compose.overlay.yml`),
dual shipping — `ciu.compose.yml` alongside an optional committed
`docker-compose.yml` + `ciu up --dir <stack> --shipped` / per-service `shipped` (S8.5–S8.6),
**4.2**: declarative `requires`/`provides` provisioning graph (S13) with
`pg:schema/<name>` kind, configurable `consul:token` Vault path, one-shot
`stack:<name>:healthy` support, per-phase live probing, `ciu check` / `ciu
graph` verbs; and SSH remote transport (S14) — `ciu ssh`, `ciu up/down/health/render
--host`, render-on-target push-deploy, the docker-optional `--thin` push→activate
contract (S14.6), fail-closed host-key pinning, optional
`paramiko` extra (`pip install ciu[ssh]`); and stack-wide resource governance
(S15) — opt-in `[<root>.governance]` injects `cgroup_parent`/`mem_limit`/
`mem_reservation`/`blkio_config` into every enumerated service via the
overlay (author-set keys always win), with baseline-derived `read_iops` and
autodetected blkio `device`, zero-footprint for stacks that don't opt in.
Migration recipes: docs/MIGRATION-V2.md.

---

## S16 — Worktree instances (`ciu worktree`)

A git worktree of a CIU repo is already a distinct runtime: `INSTANCE_ID` is a
hash of the PHYSICAL repo path (S2), so a second checkout gets its own network,
container prefix and volumes. `ciu worktree` is the verb that composes what CIU
already knows into one operation.

- **`worktree create LOGICAL [--name DISPLAY | --prefix PREFIX --feature FEATURE] [--branch BRANCH] [--path PATH] [...]`** — creates a new managed checkout.
  Generated names are UTC `<prefix>-<YYYYMMDD_HHMMSS>-<feature>`; generated
  branch and directory basename are identical, with a suffix only on an actual
  same-second collision under the Git-family allocation lock.
- **`worktree ensure LOGICAL [...]`** — returns an exact ready match without
  rewriting it, creates when absent, or resumes only a mechanically recognized
  CIU-owned partial allocation. Any requested identity mismatch refuses.
- **`worktree adopt LOGICAL PATH [...]`** — the sole operation allowed to take
  ownership of a registered unmanaged linked checkout.
- **`worktree add NAME [...]`** — retained human shorthand for create with
  logical/display/branch/directory basename all equal to NAME. It does NOT deploy: `add`
  prepares an instance, it does not decide you want it running. `--shared-infra`
  joins the new instance's declared diverging services onto an existing
  reference instance's shared network (S16.1).
- **`worktree rm NAME [-y] [--force]`** — runs `ciu clean` INSIDE the worktree
  under that worktree's own `ciu.env`, and only then `git worktree remove`.
  **The order is normative.** `ciu down` preserves
  volumes, so it strands `vol-*` dirs owned by image UIDs that an unprivileged
  `rm -rf` cannot delete; and removing the checkout first destroys the rendered
  config that tells CIU what to clean. A failed clean ABORTS the removal unless
  `--force`.
- **`worktree list`** — registered worktrees, primary marked.

Every managed linked checkout has an atomic, non-secret
`<target-ciu-root>/ciu.worktree-instance.json` (schema v1, or v2 once it
carries an explicit ownership lease — S16.9). It records the family-scoped
logical identity, display/branch/Git-path facts, exact Git-root-to-CIU-root
offset, allocation time, base reference, lifecycle state
(`allocating | ready | recovery-required`), and runtime identity once derived.
Current HEAD is inspected from Git and is never frozen in the record. The
record owns identity/lifecycle; `ciu.global.worktree.toml.j2` owns local
configuration; `ciu.env` owns generated machine facts. Each fact has one
authority.

Create/adopt admission rejects an occupied logical identity, path, or active
branch before allocation. CIU first writes an `allocating` record into a
`--no-checkout` linked worktree, so interruption remains attributable; it then
checks out the base and generates identity-only `ciu.env`. Before any network
bootstrap it rejects duplicate family `INSTANCE_ID`/network values and an
already-existing exact Docker network (independent-clone collision). Docker
absence is valid for local-only projects; a present but failing Docker endpoint
is not treated as absence. Only full env bootstrap followed by an unchanged
identity marks the record `ready`; failure writes one closed recovery status.

Environment generation and clean run as subprocesses at the exact target CIU
root (which may be nested below the Git worktree root).
In-process would violate S1.1 (`--define-root` must agree with `REPO_ROOT`,
which describes the PRIMARY checkout) and, for generation, would derive the new
instance's identity from the old instance's environment. The worktree's
`ciu.env` is read by explicit path, never via a search that consults
`$REPO_ROOT` — that search would find the PRIMARY's file and operate on the
wrong instance.

### S16.1 — Shared-infra join for worktree instances (CIU-22)

`worktree add NAME --shared-infra REF --shared-infra-services S1[,S2]
--shared-infra-ref-projects R1[,R2] --profile P1[,P2]` joins only the new
instance's declared DIVERGING-tier services onto an EXISTING reference
worktree's shared-infra network, instead of standing up a second copy of
heavy, rarely-diverging infrastructure (identity, secrets, observability,
reverse-proxy). `REF` is resolved by the same basename-or-absolute-path
grammar `find_worktree` already uses. The three shared-infra flags and a
non-empty `--profile` are an ALL-OR-NOTHING group — no mode may infer a
tier from a compose file, and a partial group is an add-time refusal before
any side effect. The OPTIONAL fourth flag `--shared-infra-ref-services`
(S16.1a) joins that same group: optional, but never standalone.

**Validation happens at `add` time; joining happens at `ciu up` time.** `add`
never deploys (S16's existing rule, unchanged): it resolves REF, reads its
explicit `ciu.env` for `DOCKER_NETWORK_INTERNAL`, and proves EVERY declared
reference Compose project (`--shared-infra-ref-projects`) has a running
container on that network — AND-combined, never OR, and scoped to both the
network and the exact project label, so a bare labelled-container count
elsewhere on the host is never mistaken for liveness. Only then does it
create the checkout and record the resolved intent under
`[ciu.instance.shared_infra]` in the new worktree's OWN
`ciu.global.worktree.toml.j2`. The actual `docker network connect` calls happen
later, in the new worktree's own process, after `docker compose up`
succeeds — never during `add`, and never before Compose has brought this
instance's own stack up on its own network.

**The new instance keeps its own `DOCKER_NETWORK_INTERNAL` throughout.**
Only the declared diverging-tier service containers gain a SECOND network
membership, via imperative `docker network connect` calls outside compose
(precedent: `_connect_devcontainer_to_network`) — never a compose
`networks:` declaration (inert: CIU writes no such key anywhere in `src/`),
never every container in the project, and never a reference-tier container.

**The post-up join re-validates everything before any side effect.** `ciu
up` re-resolves the recorded REF against the current `git worktree list`
(catches removal since `add`), re-reads its `ciu.env`, and refuses if its
network changed or a declared reference project now equals the joining
instance's own compose project. It re-runs the same AND-combined liveness
check `add` used (catches a reference stopped between verbs), then requires
a RUNNING container for every declared service in THIS compose project
(matched by `com.docker.compose.project`/`com.docker.compose.service`
labels) before touching the reference network at all.

**Already-exists detection is Docker STATE, not Docker diagnostic TEXT.** On
every non-zero `docker network connect` result, CIU re-inspects that
network's membership for the target container ID: present means a
successful CONCURRENT no-op (this invocation never connected it, so it is
not added to rollback); absent means a genuine failure. Matching Docker's
human-readable error string was explicitly rejected across review — it is
unreproducible and unspecified across Docker versions.

**Rollback is scoped to only this invocation's own successful connects.** A
genuine connect failure disconnects, in REVERSE order, only the container
IDs THIS call connected with a zero return — never a pre-existing member,
never a concurrent no-op. CIU never runs `docker compose down` on this
failure: the instance's own stack stays up, on its own network, observably
not joined, so the operator can restore the reference and retry, or
explicitly `ciu down`.

The gate (`tester-unified:local`) has no Docker socket, so every branch
above — liveness, target discovery, the concurrent-connect state check, and
rollback — is proven against a scripted fake at the `procutil.docker`
boundary.

**`--shipped` (S8.5/S8.7) shared-infra joins on a config-less stack (CIU-46
amendment).** The join once refused with `[S16.1]` when the deploy tags were
unset, on the grounds that Compose's privately derived project name was a
value CIU itself never learns. CIU-46 withdraws that premise along with the
basename fallback: a config-less shipped stack's project is now COMPUTED
(`engine.identity_compose_project_name`, from THIS checkout's `ciu.env`) and
passed as `-p`, so CIU knows exactly what up named and the join scopes its
`com.docker.compose.project=<...>` filters to that same value. A checkout
that cannot produce the identity name refuses earlier (`[S8.7]`), so no
unscoped case remains. The `[S16.1]` cannot-derive refusal is withdrawn as
unreachable; an unmatched filter still just finds zero containers and fails
the existing "no running container" check harmlessly.

#### S16.1a — Reference-service addressing: `shared_infra.ref_services` (CIU-52)

**`services` and `ref_projects` describe DIFFERENT instances and are NOT
paired.** This is normative and load-bearing, because reading it backwards
produces a plausible-looking configuration that is actively wrong:
`--shared-infra-services` names THIS (joining) instance's OWN diverging-tier
containers — the ones that gain a second network membership — while
`--shared-infra-ref-projects` names the REFERENCE's compose projects and is
consulted only for AND-combined liveness. Neither is a per-entry property of
the other; the two lists may differ in length and have no index
correspondence. Consequently NEITHER can name a REFERENCE-side service, and
an address for one MUST NOT be inferred from either — inferring an alias from
`services` would point this instance's own copy of a service at the
reference's copy of it.

A reference-side service is therefore a THIRD, independent axis, declared by
the OPTIONAL `--shared-infra-ref-services ALIAS[,ALIAS=REF_SERVICE,...]` flag
and recorded as an alias-keyed table:

```toml
[ciu.instance.shared_infra.ref_services.vault]
service = "vault"                  # the REFERENCE's compose/service key
container = "dstdns-98535c-vault"  # CIU-derived; never hand-typed
port = 8200                        # only when the reference declares one
```

The alias is the value's identity — it becomes `topology.services.<alias>` in
THIS instance's own configuration — and a TOML table key enforces its
uniqueness structurally. A bare flag item means "alias equals the reference's
service key"; `alias=ref_service` is the rename form. Two aliases MAY name one
reference service. The flag is optional but NOT standalone: it joins the
existing all-or-nothing group, and omitting it reproduces the pre-CIU-52
behavior exactly — byte-identical overlay text and not one additional Docker
call at either verb.

**The container name is DERIVED, then AUTHENTICATED, at `add` time — once.**
CIU renders the REFERENCE's own global config chain read-only
(`write_rendered=False`, so nothing is ever written into a checkout CIU does
not own) and under the REFERENCE's own `ciu.env` (`environ=<ref env>`, so the
calling process's ambient environment never reaches the reference's
templates), then applies the same `container_name()` derivation
(`{project}-{env_tag}-{service}`, S7.7/S7.8) every deployment uses. The
derivation source is the reference's own configuration and nothing else —
never string surgery on `ref_projects`, which names projects rather than
services. The derived name is then proven to be a container RUNNING right now
on the reference's network under that service label; only then is it written
into this instance's `[topology.services.<alias>]` block (`internal_host`, and
`internal_port` only when the reference declares one — CIU invents neither).
That query is deliberately NOT additionally scoped by
`com.docker.compose.project`: the container NAME already carries the
reference's `project_name`/`environment_tag`, which IS the authenticating
fact, and a reference may legitimately run a shared service under a project
the operator never needed to declare for liveness.

Authentication is what makes the derivation trustworthy rather than merely
plausible, and it MUST distinguish three outcomes, never two: the container is
live (proceed), the container is absent (a staleness refusal naming both the
computed name and the names actually found), or the question could not be
ANSWERED — an unreachable daemon, a missing binary, a non-zero `docker ps` —
which is its OWN loud failure and MUST NOT collapse into an empty result. An
empty live-set reported for an unanswerable query would state a determination
CIU never made.

**The record is write-once, so `ciu up` re-verifies it.** The join re-runs the
identical live-name query for every recorded entry, inside the
every-precondition-before-any-side-effect region — after the reference
liveness check, BEFORE this instance's own target discovery, and before any
`docker network connect` — so a reference re-created under a new identity
between verbs is caught rather than silently addressed. Nothing has been
connected at that point, so a refusal has nothing to roll back.

Because the emitted block lands in the worktree overlay, which merges LAST
(S3.1b), it overrides any committed `internal_host` default of the same key
while a committed `internal_port` the overlay does not write survives — and it
is read through the ordinary `topology.services.<name>` consumer path
(S4.16/S7.4), so no separate lookup mechanism exists.

### S16.3 — Worktree instance concurrency budget (CIU-24)

A repository's `ciu worktree` family shares one host, so nothing before this
section capped how many instances could be deployed at once. `ciu up` (both
the native path and `--shipped`) now enforces an optional cap immediately
around its real `docker compose up` call, before any container starts.

**The sole file-level configuration source** is the PRIMARY *Git* worktree's
own CIU configuration root's global table:

```toml
[ciu.worktree]
max_concurrent_instances = 3
```

"Primary Git worktree" (`worktree.primary_worktree_root`, the entry
`git worktree list` marks primary) and "this process's own CIU configuration
root" (`REPO_ROOT`, resolved by `dev.py:resolve_repo_root`'s CIU-marker walk)
are NOT the same path in a monorepo — the CIU marker can sit below the git
top-level (this project's own `ciu/` under the `vbpub` git root is exactly
this shape). `worktree.primary_ciu_root(repo_root)` derives the offset once —
`repo_root.resolve().relative_to(git_toplevel(repo_root))` — and re-applies
that SAME offset to the registered primary worktree, never assuming a linked
worktree's raw git path equals its own CIU root. `ciu up` renders only that
derived primary CIU root, with `config_model.render_global_chain(root, root,
write_rendered=False)` — never the git root, an intermediate global layer, or
the CURRENT (possibly linked) worktree's own branch, which could carry a
conflicting policy. `render_global_chain`'s narrow no-global-configuration
`ValueError` (an absent `ciu.global.defaults.toml.j2` at the primary root) is
the only render error treated as "no file policy"; every other render error
(bad TOML, a secret-scan violation) still aborts loudly.

This is deliberately **not** a `[governance]` / `[<root>.governance]` value
and does **not** participate in CIU-13's global/stack governance merge
(`governance.resolve_stack_governance`): an instance cap constrains every
worktree in the repository's git family, never a property of the single
stack being launched — a stack raising its own budget could starve every
sibling instance on the host, which is exactly the fail-open CIU-13 fixed for
a different value and must not be reintroduced here.

`CIU_MAX_CONCURRENT_WORKTREES`, when present in the process environment, is a
positive decimal integer (`[1-9][0-9]*`, no sign/leading zero/decimal
point/surrounding whitespace) that OVERRIDES the validated file value for
that process; the file table is validated even when the ambient override is
also present — an ambient override never masks an invalid file table. There
is no `0 == unlimited` sentinel: only absence at BOTH sources means no cap,
in which case `ciu up` makes no Docker call and takes no lock at all. An
unknown `[ciu.worktree]` key, or a `max_concurrent_instances` that is not a
positive integer (including `true`/`false`, which are `int` subclasses in
Python and are explicitly rejected), fails loudly.

A CIU root that is not inside a git work tree at all (no worktree family is
even possible) is treated the same as an absent global template: no file
policy, consulted the same way a no-configuration render is — silently
skipped rather than raised — mirroring this module's own pre-existing
precedent for exactly this condition (`engine._check_gitignore`, S1.7). An
explicit `CIU_MAX_CONCURRENT_WORKTREES` ambient override is still honoured
even outside git; `worktree_budget_slot` itself then refuses loudly the
moment it tries to enumerate worktrees for a cap it cannot actually honour,
rather than silently treating a real ambient request as "no cap".

**The deployment classifier.** Candidates are exclusively the entries in
`git worktree list --porcelain`; the primary is always included. A candidate
is *registered* only when its own `<git-worktree>/<ciu-root-offset>/ciu.env` exists, parses,
and supplies a distinct, non-empty `DOCKER_NETWORK_INTERNAL` — the same file
location managed lifecycle writes to and S16.1's shared-infra join already
reads from. For each registered candidate,
its own CIU root is `entry.path / offset` and its own stack is that root plus
the caller's relative `stack_rel` — a literal path append that retains every
component of a nested CIU root. A candidate stack genuinely absent from a
sibling's checked-out branch is not deployed: it is skipped with one
deterministic `[INFO] [S16.3]` note, and no Docker query is made for it —
never a hard error. Before any lock is taken, each remaining candidate's
global config is rendered against ONLY that candidate's own explicit
`ciu.env` mapping (never the caller's ambient environment, via
`render_global_chain`'s new `environ=` parameter), and its exact Compose
project is derived with `engine.compose_project_name` (a lazy import, to
avoid the `worktree` ↔ `engine` cycle this wiring introduces). A present
candidate stack whose config or project identity cannot be rendered is a
loud `[S16.3]` failure, never evidence of an inactive instance.

A candidate is *deployed* only when `docker ps --filter
label=com.docker.compose.project=<that-exact-candidate-project> --format
{{.Networks}}` lists a container carrying **its own** network — a
value-qualified filter, never a bare label-presence check. This is load
bearing against S16.1's shared-infra join: a joined child container may list
the reference instance's network too, but it always carries the CHILD's own
project label, never the reference's, so it can never make the reference
candidate appear deployed. Docker unavailable or non-zero, a malformed
eligible `ciu.env`, or a duplicate `DOCKER_NETWORK_INTERNAL` across
candidates is a loud `[S16.3]` error, never an empty/zero count. Containers
of a worktree no longer registered with git do not count (stale-orphan
reaping is CIU-25's job, not this one's).

**The locked critical section.** All of the above — candidate translation,
per-candidate env/config rendering, project derivation — happens BEFORE any
lock is acquired. Only the Docker queries, the count decision, and the
caller's own `docker compose up` execution happen while
`<git-common-dir>/ciu-worktree-budget.lock` is held (`fcntl.flock(...,
LOCK_EX)`), shared by every worktree in the family regardless of which one
takes it. If the CURRENT instance's own network is already deployed, `ciu up`
proceeds even at or over cap (an already-running instance may be reconciled
after the policy is later lowered); otherwise a count `>= cap` refuses before
Compose ever starts, naming the observed count, the cap, and the current
network. The lock is released in every case — success, refusal, or a Compose
failure — leaving no separate reservation artifact to leak. Two overlapping
cold starts in the same family are therefore serialized: the second waiter
re-counts under the lock only after the first's Compose start has made its
own network observably live, and is refused if that would exceed the cap.

**Wiring.** Both `engine.main_execution` and `engine.run_shipped` resolve the
cap only on the real Compose-start path, immediately before they enter their
own budgeted Compose executor call, passing the current `DOCKER_NETWORK_INTERNAL` and
`working_dir.relative_to(repo_root)`. `--dry-run` and `--render-toml` never
resolve the cap, reach the executor, or make a budget Docker/lock call. A
`worktree.WorktreeError` from the budget slot (candidate resolution failure
or a capacity refusal) is translated to `ComposeError`, the same translation
S16.1's join already uses, so a multi-stack `ciu deploy` run fails only this
one stack. S16.1's post-up shared-infra join runs strictly AFTER the budget
context exits (never nested inside it): it starts no new instance, so
holding the family-wide capacity lock across an arbitrary sequence of
post-up network connects would only add unrelated contention without making
the count/start decision any safer.

The gate (`tester-unified:local`) has no Docker socket, so the classifier,
the lock discipline, and both engine call sites are proven against the same
scripted `worktree.procutil.docker` fake S16.1's tests use; the
lock's held-continuously-across-the-executor discipline is proven by wrapping
the real `fcntl.flock` and recording every transition, and genuine two-thread
contention (real `fcntl.flock`, no sleeps) proves the second waiter of two
simultaneous cold starts re-counts and refuses once the first's deployment
becomes visible.

### S16.4 — Structured JSON documents (D-009)

`ciu worktree inspect LOGICAL --json`, `ciu worktree list --json`, the
lifecycle verbs (`create`/`ensure`/`adopt`/`add`) with `--json`,
`ciu worktree lease --json` (S16.9) and `ciu worktree rm --json` each emit
**exactly one JSON document on stdout** (`schema_version: 1`); diagnostics go
to stderr. The `operation` vocabulary is closed (`create | ensure | adopt |
add | inspect | lease | list | remove`) and the `status` vocabulary is closed
(`allocating | ready | recovery-required | removed`); a `recovery-required`
instance additionally carries a closed `recovery_status`
(`checkout-incomplete | env-generation-failed | runtime-collision`). The
persisted instance record is nested under `instance`
(`WorktreeInstanceRecord.to_dict()` — v1, or v2 with a `lease` key per
S16.9); current Git facts are nested under `git`.

Git facts are freshly read from Git, never inferred from a name or a stale
record: `git.registered` (the record's checkout is a current registered
worktree), `git.path`, `git.branch` (or `(detached)`), `git.detached`,
`git.primary`, `git.head`, and `git.dirty` (`git status --porcelain`). A record
whose checkout is no longer registered, or whose status cannot be read, is a
refusal — never a repaired or guessed value; a missing logical record is a
refusal; a duplicate or mismatched record is a refusal (S16's existing
family-scan rules). `list --json` emits an array of the same per-instance
documents, primary first in git's own order.

Removal captures the validated pre-state (the instance record, when managed)
and emits success only after BOTH `ciu clean` and `git worktree remove`
complete; a failure is the existing `WorktreeError` identifying the retained
resources — never a success document.

### S16.5 — Capability discovery (D-009)

`ciu capabilities [--json]` exposes a **separately versioned, closed
allowlist** of shipped machine contracts (`schema_version: 1`,
`capabilities`: sorted identifiers). Consumers allowlist these identifiers
instead of inferring features from SemVer. An identifier is added only when
its code path ships in the same release. Shipped identifiers:
`worktree.branches.v1`, `worktree.identity.v1`, `worktree.inspect.v1`,
`worktree.lease.v1`, `worktree.lifecycle-json.v1`, `worktree.reap.v1`,
`worktree.up.v1`, `worktree.exec-local.v1`, and `worktree.exec-target.v1`.

### S16.6 — Exact selected-worktree control (`worktree up` / `worktree exec`)

`ciu worktree up LOGICAL` and `ciu worktree exec LOGICAL -- ARGV...` operate
on **exactly one** selected `ready` managed record. A missing record, or a
record in `allocating`/`recovery-required`, refuses — no child starts.

Both build the child environment from the target's OWN exact
`<record.ciu_root>/ciu.env` (parsed, never sourced through a shell): the
ambient process environment MINUS every CIU root/identity/network/profile key
(`REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `DOCKER_NETWORK_INTERNAL`, `INSTANCE_ID`,
`REPO_NAME`, `CIU_SERVICES_PROFILE`), then overlaid with the parsed target
values. The parsed target must carry `REPO_ROOT`, `PHYSICAL_REPO_ROOT`,
`INSTANCE_ID`, `DOCKER_NETWORK_INTERNAL`, and `REPO_NAME`, and each must match
the selected record/root: a missing key, a `REPO_ROOT` other than the record's
CIU root, or an `INSTANCE_ID`/network differing from the record is a refusal,
never a fallback and never a sibling's value.

`worktree up` invokes CIU's existing up entry point as a subprocess in
`record.ciu_root` under that environment; `worktree exec` runs the exact argv
(after a mandatory `--` separator) with no shell in that root. Both propagate
the child's exact exit code — never a wrapper-masked value. `exec` never
starts, cleans, or renders anything implicitly; the presence of `--` and of at
least one argv element is enforced, so a leading-dash argument is never
misparsed as a CIU flag.

### S16.7 — Declared worktree container targets (`exec --target`)

`ciu worktree exec LOGICAL --target ALIAS -- ARGV...` runs exact argv (no
shell) inside the ONE already-running container for a DECLARED target of the
selected instance. There is no arbitrary service-selection escape hatch:
targets are declared in the instance's own global config as
`[ciu.worktree.exec_targets.<alias>]` with exactly four keys — `stack`
(required non-empty string), `service` (required non-empty string), `workdir`
(required absolute container workdir), and `requires_worktree_mount`
(boolean, default **true**; false is the only opt-out). The alias is a
Git-safe single component. Unknown keys, unknown aliases, empty strings, or
malformed booleans refuse before any Docker call.

Flow: resolve the selected `ready` record and its exact environment (S16.6);
render the target's own global chain WITHOUT writing under that environment;
derive the exact Compose project (`engine.compose_project_name`), service
(declared), and network (the instance's own `DOCKER_NETWORK_INTERNAL`);
require **exactly one already-running container** for that project/service/
network — zero or multiple refuse, and `up` is NEVER started implicitly.
`docker ps`/`inspect` filters use the exact labels/network, never a
service/container-name substring.

The worktree-mount proof (default): `docker inspect` the container's mount
records and require a bind mount whose host `Source` equals the selected Git
worktree's PHYSICAL path (translated with the target's own REPO_ROOT /
PHYSICAL_REPO_ROOT) and whose container `Destination` contains the declared
`workdir` (path-component containment). The comparison uses only Docker's own
reported namespaces — never a local filesystem predicate on a path belonging
to the other namespace. A wrong mount (e.g. the primary checkout mounted while
a linked checkout is selected) refuses. `requires_worktree_mount = false`
permits a deliberate non-source utility container without weakening
project/service/network uniqueness.

Execution is `docker exec -w WORKDIR CONTAINER -- ARGV...` (no shell), and the
exact exit code is returned.

### S16.8 — Worktree branch hygiene (`ciu worktree branches`, CIU-25 git half)

CIU-25's grounded-staleness demand, delivered for the GIT layer: a crashed
dispatcher or forgotten teardown most often leaves a fully-merged branch and
its checkout behind, and nothing grounded said so. `ciu worktree branches
[--base REF] [-y] [--json]` surveys every LOCAL branch against *base*
(default `main` — the same policy default `worktree add --base` ships).
*base* MUST name a LOCAL BRANCH — a SHA or remote-tracking ref refuses
`[S16.8]`: classification and the destructive prune reason about branch
NAMES, and a SHA anchor once let the anchor branch itself classify prunable
(adversarial-review finding). Classification is a CLOSED seven-value
vocabulary:

- **`base`** — the branch measured against; never touched.
- **`mainline`** — the repository's DEFAULT branch: origin/HEAD's target,
  or — only when that ref is unresolvable — literally `main`/`master`
  (documented policy fallback). Never pruned, even when the survey runs
  against another base and the mainline happens to be fully merged there:
  "clean up merged branches" can never mean deleting a mainline.
- **`current`** — somebody's working context, even when merged: the PRIMARY
  checkout's branch, **or the branch of the checkout the command was INVOKED
  FROM**. The invoking checkout is never a candidate in its own run — a
  self-referential prune once removed the operator's own working directory
  mid-loop, after which the next Git call failed on a vanished cwd and the
  whole operation aborted (ciu-P28).
- **`managed-instance`** — its checkout carries a CIU-managed instance record
  (S16.2), at ANY lifecycle state. **Never removed by `-y`**, whatever its
  mergedness: this is the GIT half of CIU-25 and its removal step is a bare
  `git worktree remove`, so removing a managed checkout without `ciu clean`
  FIRST destroys the rendered config that tells CIU what to clean — orphaning
  containers/volumes/networks and stranding root-owned `vol-*` directories no
  unprivileged operator can delete (ciu-P28; the exact hazard S16.4's
  clean-then-remove ordering exists to prevent). The survey's hint names the
  disposal command, `ciu worktree rm NAME`, which runs `ciu clean` first.
- **`prunable`** — Git PROVES nothing would be lost: zero commits not in
  base (`rev-list --count base...branch`), and either no checkout or a CLEAN,
  non-primary, non-invoking, unmanaged one. Only this category is ever
  removed.
- **`merged-dirty`** — merged, but its checkout carries uncommitted changes;
  listed with attributes so a human rules on the dirt first.
- **`unmerged`** — has work not in base; keep.

Every branch reports its attributes: `checkout` path, `ahead`/`behind`
(commit counts vs base), `changed_files` (diff vs the merge-base),
`last_commit_at`/`last_commit_subject`, `dirty`, and its ciu instance linkage
(`logical_name` + lifecycle `state` when a managed record's checkout sits
there). No age heuristic, no process-lifetime inference, no basename
similarity — the estate rule: removal only on proof, survey otherwise.

Without `-y` there are NO side effects: the survey carries an explicit hint
naming how many branches `-y` would remove, plus how to dispose of any
`managed-instance` branches it deliberately refuses.

**Every destructive Git command of the `-y` pass runs from the PRIMARY
worktree, never from the invoking checkout.** `git branch -d` judges
mergedness against the HEAD of the worktree it runs in, so invoking
`branches -y` from a linked checkout that was behind the mainline used to
report fully-merged branches as "not fully merged" — `removed: []` — while
their checkouts had already been destroyed (ciu-P28, reproduced end-to-end).
The primary's HEAD is the anchor the base-sanity guard already validates, so
the check and the operation now judge against the same HEAD.

The destructive pass is gated THREE times against the half-prune failure
(destroy a checkout, then have Git refuse the deletion — divergent mergedness
definitions): (1) **base sanity** — `-y` refuses `[S16.8]` unless the base tip
IS, or is an ancestor of, the primary checkout's HEAD or the origin/HEAD
target (surveying any base stays allowed; pruning demands one Git agrees
with); (2) **per-candidate upstream pre-check** — a branch tracking an
upstream that does not contain it is moved to `failed` BEFORE its checkout is
touched, with the reason; (3) **per-candidate HEAD pre-check** — a branch with
no upstream that is not contained in the PRIMARY checkout's HEAD (reachable
when base sanity passed via the origin/HEAD target alone) is likewise moved to
`failed` before its checkout is touched. Then exactly the remaining `prunable`
category is removed — per branch, `git worktree remove` FIRST (Git re-verifies
cleanliness itself) then `git branch -d` (Git re-verifies mergedness); any
residual refusal moves that branch to `failed` WITH Git's reason and the prune
continues. **No failure escapes the per-branch loop**: an unexpected raise
becomes that branch's named `failed` reason and the remaining candidates are
still processed, so a document is always returned — an unhandled mid-loop
error once returned none at all and silently left later candidates
unprocessed (ciu-P28). The document is then RE-SURVEYED so its counts and
branches report the post-prune truth, never the stale pre-prune snapshot.

The output names every `removed:` and `FAILED:` branch, and a `partial` prune
exits non-zero — a partial success is never silent. **That exit-code decision
is made once, above the output-format branch, so `--json` and human output
exit identically** (it previously lived inside the human arm only, and
`--json` reported exit 0 on the same partial document). The document is
versioned (`schema_version: 2` — bumped from 1 by the widened category
vocabulary, the same fail-closed rule S17.3 applies; operation `branches` /
`branches-prune`, status `survey`/`pruned`/`partial`) under the S16.4 envelope
conventions; capability id `worktree.branches.v1`.

The Docker-resource half of CIU-25 (containers/volumes of a crashed
instance) remains OPEN as a DETECT/REAP verb. S16.9 below ships the two
substrates it requires — an explicit ownership lease and attributable
resource labels — and nothing else: no detector, no destructive verb.

### S16.9 — Ownership lease and resource labels (CIU-25 substrate)

CIU-25's constraint is that staleness must never be INFERRED from age,
basename similarity or a missing local process: a long-lived worktree can be
entirely legitimate. So ownership is DECLARED, in the instance's own record,
or it is not known. This section defines what is declared and how resources
are attributed. **It defines no destruction of anything.**

#### The `lease` field — record schema v2

The worktree-instance record (S16, `ciu.worktree-instance.json`) gains ONE
optional field, `lease`, and with it a schema version 2:

```json
"lease": {
  "holder":          "ciu@<hostname>:<INSTANCE_ID>",
  "acquired_at_utc": "2026-08-25T12:00:00Z",
  "renewed_at_utc":  "2026-08-25T12:00:00Z",
  "expires_at_utc":  "2026-08-26T12:00:00Z",
  "mode":            "held"
}
```

`mode` is a closed two-value vocabulary and it GOVERNS the expiry:

| `mode` | `expires_at_utc` | Meaning |
|---|---|---|
| `held` | **REQUIRED** (non-null) | a bounded claim; the only fact a future reap may read as "the operator's claim has lapsed" |
| `perpetual` | **FORBIDDEN** (must be `null`) | an explicit unbounded claim — "this instance is long-lived on purpose" |

A mismatch (`held` with a null expiry, `perpetual` with one) is a tagged
`[S16.9]` validation refusal, not a repaired value. Every lease timestamp is
ISO-8601 with an **explicit UTC offset**, written in the same `...Z` form
`created_at_utc` already uses; a naive, offset-less timestamp anywhere in a
lease is refused rather than parsed as local time, because a lease whose
expiry is ambiguous by up to a day is worse than no lease at all once a
destructive verb reads it. `holder` reuses the identity CIU already has: the
`INSTANCE_ID` from the workspace's own `ciu.env` (S2) and the host name
`ciu.env`'s `DEVCONTAINER_NAME` records — no new identity mechanism.

**v1 and v2 coexist permanently, and a READ never upgrades a record.** A
schema-v1 record (no `lease` key at all) is read successfully and is
`lease: None` in memory; `worktree inspect`/`worktree list` leave it
byte-identical on disk. `schema_version: 2` becomes true only when an
operation that legitimately MUTATES the lease writes it (acquire/renew via
`ciu up`, or the `ciu worktree lease` verb). A v2 record MUST carry `lease`
and a v1 record must NOT — the key-set check is schema-dependent, so neither
shape can be half-written. `lease: null` at v2 is meaningfully different from
a v1 record: it says "this instance participates in leasing and currently
claims nothing", where v1 says "this record predates leasing entirely".

#### `[ciu.worktree].lease_ttl_hours`

The existing repository-wide `[ciu.worktree]` table (S16.3, read from ONLY
the primary CIU root, closed key set, unknown keys refused) gains ONE
recognized key: `lease_ttl_hours`, a positive number.

**Absent means no lease is ever acquired** — not "a default TTL". A consumer
who configures nothing keeps exactly the pre-S16.9 behavior and takes on zero
new expiry risk for anything already running. A default TTL would start
silently expiring leases on instances whose operators never opted in, which
is the failure mode this estate's "defaults are hazards" rule exists for.
There is deliberately no ambient override: a lease's lifetime is repository
policy, not something one shell can quietly shorten.

#### Lifecycle

- **`ciu up`** — when `lease_ttl_hours` IS configured AND this checkout is a
  MANAGED worktree instance (it carries a lifecycle record), acquires or
  RENEWS a `held` lease expiring at `now + lease_ttl_hours`. A renewal
  preserves `acquired_at_utc`; losing it would erase the only evidence of how
  long the instance has actually been owned. This happens BEFORE the compose
  invocation: a run that crashes halfway has still created containers, and
  claiming slightly too early is recoverable (`ciu clean` clears it) where
  claiming too late is the orphaned-resource hole CIU-25 exists to close. A
  PRIMARY or unmanaged checkout, and every `--dry-run`, claims nothing.
- **`ciu clean`** and **`ciu worktree rm`** — clear the lease (`lease: null`)
  **ON SUCCESS ONLY**. A clean that failed any pass, and a `--force` removal
  over a failed clean, leave the lease exactly as it was: the lease is the
  evidence that something may still own these resources, and erasing it on a
  failed teardown would manufacture "unowned" out of "we do not know". An
  instance that never leased is not dragged up to v2 by a teardown, and a
  record too malformed to parse is a WARN, not a clean failure — the resources
  clean certifies removed are removed, and an uncleared lease reads as still
  owned, which is the safe direction.
- **`ciu worktree lease LOGICAL (--extend DURATION | --perpetual | --release)
  [--json]`** — the explicit operator verb. `--extend` sets `held` with a new
  expiry `now + DURATION` (CIU's one duration grammar: `24h`, `90m`, `3600`),
  `--perpetual` sets `mode: perpetual`, `--release` unconditionally sets
  `lease: null`. Exactly one mode is required. It touches only the record and
  runs NO Docker query, so it works identically on a stopped instance —
  extending or dropping a claim is independent of whether containers happen to
  be running. `--json` emits one S16.4 document with operation `lease`.

#### Ownership labels

For a MANAGED worktree instance, `ciu up` stamps two labels on every
container, volume and network it CREATES:

| Label | Value |
|---|---|
| `ciu.instance` | that workspace's `INSTANCE_ID` |
| `ciu.repo-root` | that workspace's `PHYSICAL_REPO_ROOT` |

Both are read from **that workspace's OWN generated `ciu.env`, by exact
path** — never from the ambient process environment, which a shell that
sourced a sibling checkout's `ciu.env` carries (the CIU-41 contamination
species, closed repeatedly across this codebase). Mislabeling would be worse
than not labeling: it attributes one instance's containers to another
checkout's root, which is precisely the input a future destructive verb
reads. A managed instance whose `ciu.env` names no `INSTANCE_ID`/
`PHYSICAL_REPO_ROOT` REFUSES rather than stamping a guess.

The labels are emitted as a separate compose fragment,
`<stack>/.ciu/ciu.compose.ownership.yml`, merged as an additional `-f` and
removed by `reset_service` alongside every other generated artifact. It is
deliberately NOT folded into `ciu.compose.overlay.yml` (S4.17/S8.1): that
overlay is omitted entirely when a stack has no secrets, configfiles,
governance or image revisions to wire, and the plainest possible stack's
containers still have to be attributable.

Two things are deliberately NOT labeled. **A PRIMARY/unmanaged checkout's
resources** — widening a future destructive verb's field of view to include
the main workspace is a decision that needs its own package, not a side
effect of this one. **`external: true` volumes and networks** — `ciu up` did
not create them (the workspace network is created by `ciu env generate`,
S2.6), they outlive the project, and compose rejects extra keys on them.

#### Still open

**Shipped-mode labels.** `ciu up --shipped` (S8.5) DOES acquire/renew the
lease — a pure record write, with the same claim-before-creation rule — but is
deliberately NOT label-stamped. The labels are a generated compose fragment,
and `ciu clean` skips `reset_service` for a shipped stack (its compose file is
maintainer-owned, S8.5/S8.6), so a fragment written under a vendored stack's
`.ciu/` would never be removed. Closing this needs a shipped-stack artifact
lifecycle, not a one-line addition here.

**Detection and destruction.** Shipped in **S16.10** (`ciu worktree reap`),
which consumes exactly the substrate above.

### S16.10 — Docker-resource reap (`ciu worktree reap`, CIU-25 docker half)

`ciu worktree reap [-y] [--category C1,C2] [--dry-run] [--json]` is the
Docker-side twin of S16.8's branch hygiene, and it is the only CIU verb that
deletes resources it did not create in the same command. One rule governs the
whole section: **CIU destroys only what it can PROVE it owns and PROVE
nothing still claims.** Age, directory-basename similarity and "no process is
running" are not inputs to any decision here — the CIU-25 backlog entry names
all three as wrong, because a long-lived worktree is legitimate and a stopped
instance is not an abandoned one. Ownership is DECLARED, by S16.9's lease and
`ciu.instance`/`ciu.repo-root` labels, or it is not known; what is not known
is not destroyed.

#### The closed category vocabulary

A *resource group* is one compose project plus its volumes and networks (plus,
for a known identity, its S2.6 identity network, which compose never creates).
Every group collapses into **exactly one** of seven categories — never zero,
never two:

| Category | Proof | Does `-y` act on it? |
|---|---|---|
| `owned` | a record claims it and its lease is held/perpetual/unconfigured — OR no record does, but a REGISTERED checkout's own `ciu.env` declares that `INSTANCE_ID` | **never** |
| `lease-expired` | a readable record whose `held` lease's `expires_at_utc` is in the past at survey time (S16.9) | yes |
| `checkout-missing` | unclaimed as `orphaned`, AND the group's own `ciu.repo-root` label names a directory that is not currently present | yes |
| `orphaned` | labelled `ciu.instance=<id>` for an id matching no record and no registered checkout | yes |
| `partial-cleanup` | the attributed record DECLARES `state: "recovery-required"` | yes |
| `unattributable` | no `ciu.instance` label and no identity-form compose project name | **never** |
| `ambiguous` | the attribution does not resolve to exactly one TRUSTWORTHY record: several identities claim the group, several records claim the identity, or the one record that does is contradicted by Git | **never** |

Precedence is a single first-match-wins chain, and its order encodes two
policies: every un-provable attribution resolves to a never-destroyed
category *before* any destructible one is considered, and among the
destructible ones the most operationally specific fact wins.

`partial-cleanup` is deliberately narrower than the original CIU-25 sketch,
which also counted "a group with some (not all) of its resources present".
That clause is **withdrawn** as both undecidable and unsafe: nothing anywhere
records what "all" would be for a group (a stack that declares no volumes and
an instance that ran `ciu env generate` but never `ciu up` both read as "some,
not all"), and `ciu down` preserves volumes on purpose — so an owned,
valid-leased, merely-stopped instance would have qualified and lost its data.

**Known limitation, `checkout-missing`'s proof:** filesystem absence
(`Path(root).is_dir()` returning False) cannot distinguish a genuinely
deleted checkout from one on a currently-unavailable mount (network
filesystem down, disk not yet attached). Either reads identically — no
record, no registered checkout, an absent path — and `checkout-missing`
routes to direct Docker removal rather than `ciu clean` (S16.10's own
routing table), since no surviving checkout exists to run `clean` in. A
checkout on a mount that is merely temporarily down would misclassify this
way; a `--perpetual` lease does not protect it, because the lease lives
inside the same now-unreadable record. This is inherent to filesystem
evidence, not a gap this verb can close by itself — mitigated by `reap`
being opt-in, defaulting to a read-only survey, and `--dry-run` being the
documented first step before any `-y`.

`checkout-missing` is a *refinement* of `orphaned`, not a separate licence:
a group reaches either test only once no record and no checkout claims its
id, which is what licenses removal; the label only decides which message the
operator reads. The instance record lives INSIDE the checkout, so a vanished
checkout takes its record with it — `ciu.repo-root` is the only durable,
checkout-external evidence of where an instance used to live. In a
devcontainer that label is a HOST path this container may not be able to see;
the classification is safety-neutral for exactly the reason above.

Two resource classes are deliberately **not surveyed at all**, so no flag
combination can name them: anything compose did not create (no
`com.docker.compose.project` label), and any loose network that matches
neither a `ciu.instance` label nor a known identity's network name. A
consequence, stated plainly: the identity network of an instance whose
checkout AND record are both gone carries no label (it is created by
`ciu env generate`, outside compose) and is therefore out of reap's reach by
design — remove it by hand.

#### The CLI surface

Without `-y` the verb is a **pure survey** — every Docker call is a read-only
enumeration, nothing on disk is written, and a schema-v1 record is not even
re-serialized. The document carries no timestamp of its own, so two
consecutive surveys of an unchanged host are byte-identical.

`--category` can only ever NARROW what `-y` acts on. Its selectable
vocabulary is exactly `checkout-missing,lease-expired,orphaned,partial-cleanup`
— which is also the default. `--category owned`, `--category unattributable`
and `--category ambiguous` are **refusals (exit 2)**, not selections: there is
deliberately no flag anywhere that forces a protected category, because those
three categories mean precisely that no proof of ownership exists.

`-y --dry-run` prints the exact remediation commands and executes none of them.

#### The reap transaction

Per group, in strict order, aborting that group on the first failure:

1. **If the checkout still exists and its `ciu.env` is readable, delegate to
   `ciu clean -y` in that checkout** and stop there. `clean` is authoritative:
   it knows the rendered config, the `vol-*` hostdirs and the privileged
   removal helper a bare `docker rm` knows nothing about. This is the ciu-P28
   hotfix lesson made binding — a reap that touches a MANAGED instance goes
   through clean-then-remove, never a bare resource deletion. A clean that
   FAILS is reported; CIU never second-guesses it with a direct removal.
2. **Otherwise** remove the exact resources the survey enumerated (full
   container IDs, exact volume and network names), containers → volumes →
   networks.
3. **Before removing any network**, two independent guards, either of which
   alone means "leave it standing": any container still joined that this pass
   did not just remove (the S16.1 shared-infra case — another instance holds a
   second membership), or any other surveyed group of the same identity not
   yet disposed of (one workspace's several stacks share one identity network;
   the last one out turns off the light). A network already removed by a
   concurrent operator is a no-op, not a failure.

One group's failure is never the whole sweep's: it lands in `failed` with the
real error text, every other targeted group is still processed, and a document
is always returned. **The returned document is a post-pass RE-SURVEY**, not
the pre-pass plan.

**The identity-completeness interlock.** A registered checkout that carries an
instance record file but from which CIU can read no identity — neither from
the record nor from its `ciu.env` — makes the survey `identity_complete:
false`. While that holds, the `orphaned` category is DISARMED: an id that
looks unclaimed may simply be the one that could not be read, and a corrupted
record on a live instance must never make that instance's own labelled
resources look reapable. The refusal is loud (that group lands in `failed`,
status `partial`, exit 1), never a silent skip. Only `orphaned` is disarmed —
the record-backed categories never depended on that premise.

#### Document and exit codes

`schema_version: 1`, `operation: "reap"`, `status` from the closed vocabulary
`survey` / `dry-run` / `reaped` / `partial`. `counts` is keyed by **all seven**
categories including the zero-valued ones. `findings` carries every record
inconsistency the survey met (`ciu worktree reap` uses a non-raising sibling of
the family record scan — a survey that dies on one bad record is useless
exactly when it is most needed). The CLI exits 0 on `survey`/`dry-run`/
`reaped`, **1 on `partial`** (decided once, above the output-format branch, so
`--json` can never disagree with the human output), and 2 on a refusal.

Capability id: `worktree.reap.v1` (shipped alongside `worktree.lease.v1` —
reaping is only ever as safe as the ownership signal it consults).

## S17 — Image provenance

### S17.1 — Stamping

`ciu bake` (and `--build`) set `org.opencontainers.image.revision` on every
image, from the source revision including a `-dirty` suffix for an unclean tree.
The suffix is load-bearing: a dirty build that claimed a clean commit would be a
more convincing version of the problem, not a fix. When the revision is unknown
(not a git checkout) NOTHING is stamped — a label reading `dev` looks like an
answer and would be trusted as one.

### S17.2 — Enforcement at TEST time (`ciu provenance`, fail CLOSED)

`ciu provenance [--ignore-mismatch] [--no-preflight] [--json]` refuses (S10.3 exit 2) when a
RUNNING container's image carries a revision label differing from the commit
under test. `deploy.verify_running_provenance` builds the verdict (S17.3);
`cli._provenance` is the SOLE place that turns it into a refusal, a warning,
or `--json`'s document (`verify_running_provenance` itself never raises and
never prints, so the two output modes cannot mix on one stream).

**This is a test-time gate, not a deploy-time one, and the distinction is the
design.** At deploy the question is "did I remember to bake?", which the
operator discovers immediately. The question that produces bad EVIDENCE is asked
later, against a stack that is already up: *does this passing integration run
describe the code I think it does?* By then the containers are running, so the
thing to inspect is the image each RUNNING container actually has — not what a
compose file declares it would use. `ciu up` therefore does NOT perform this
check.

Scoped to containers whose compose project starts with this instance's
`<project>-<env_tag>` prefix, so a sibling worktree instance (S16) —
legitimately running a different commit — is never reported as this instance
being stale. Without a derivable instance identity the command REFUSES to answer
(exit 2) rather than return a host-wide verdict that is wrong in both
directions.

Scope is self-selecting, and the non-refusals are as normative as the refusal:

- Only labelled images are checked. CIU's bake is the only thing that sets the
  label, so external images (`postgres:16`) are skipped without maintaining a
  list of "ours".
- An **unlabelled** image is skipped silently: it is external or pre-S17, and
  absence is not evidence of mismatch. (`docker --format` renders a missing key
  as the literal `<no value>`; that is treated as absent.) CIU-39 adds one
  refinement: a consumer MAY declare vendor images (`[deploy.provenance]
  vendor_images`, S17.5) — a running reference EQUAL to a declaration is
  `vendor-pinned`, making `verified-match` reachable for all-vendor
  deployments instead of permanently `not-verified-no-evidence`.
- An **absent** image is not a mismatch — that is compose's failure to report.
- A **dirty** working tree WARNS and does not refuse. Uncommitted changes are in
  no artifact anywhere, so nothing can match; refusing would fire on every
  dev-loop deploy and be disabled permanently, and a rule nobody can keep
  enforces nothing.

`--ignore-mismatch` (alias `--force`) downgrades the refusal to a warning.
`--no-preflight` is an explicit break-glass bypass: it prints an informational
line, returns 0, and performs no configuration, Git, or Docker access. It cannot
be combined with `--json`, because the command then produced no provenance
verdict and must not manufacture a machine-readable evidence document. It has the
same bypass meaning as CIU's sibling preflights.

### S17.3 — Machine-readable provenance verdict (`ciu provenance --json`, CIU-20)

`deploy.verify_running_provenance` ALWAYS builds and returns a
`ProvenanceResult` — never bare `None`, never raising internally. Its fields,
in wire order: `schema_version` (constant `2` as of CIU-39; the seven
CIU-20-era documents were schema `1` and remain the historical record),
`instance`, `commit_under_test`, `tree_state`, `containers`, `overall`.

- `commit_under_test` is `get_git_hash()`'s return value VERBATIM (the
  `-dirty` suffix, if any, lives ONLY here).
- `tree_state` is DERIVED from that same string (`.endswith("-dirty")` →
  `dirty`, `== "dev"` → `not-a-checkout`, else `clean`) — never set
  independently, so the two fields cannot contradict.
- `containers` is `list[{name, image, labelled_revision, status}]`, sorted by
  `name` ascending, when — and ONLY when — enumeration ran (`docker ps`
  succeeded); JSON `null` in every case where no container-level verdict was
  formed (identity refused, dirty tree, non-checkout, or enumeration could
  not run). `labelled_revision` is JSON `null` when unknown, NEVER `""`.
  `status` is one of `match` / `mismatch` / `unlabelled` / `vendor-pinned`
  (CIU-39).
- `overall` is one of SIX closed values, decided in this order: (1) identity
  (`project`/`env_tag`) unresolved → `refused-no-identity` (every other field
  null), emitted by `cli._provenance` BEFORE `verify_running_provenance` is
  even called — there is no `project_prefix` yet to scope a check with; (2)
  `commit_under_test == "dev"` → `not-verified-unknown` (`containers` null);
  (3) `commit_under_test` ends in `-dirty` → `not-verified-dirty` (`containers`
  null); (4) enumeration could NOT run (`docker ps` raised or returned
  non-zero) → `not-verified-no-evidence` (`containers` null); (5) enumeration
  ran, ≥1 container `mismatch` → `mismatch` (`containers` the sorted list);
  (6) enumeration ran, ≥1 `match` or `vendor-pinned` and ZERO `mismatch` →
  `verified-match` (`containers` the list) — a green verdict is NEVER emitted
  from zero checked containers; (7) enumeration ran but produced NEITHER a
  match/vendor-pin NOR a mismatch (empty, or all undeclared-unlabelled) →
  `not-verified-no-evidence` (`containers` the possibly-empty list).

### S17.5 — Declared vendor baseline (`[deploy.provenance]`, CIU-39)

An image ciu never built has no `org.opencontainers.image.revision` label, is
`unlabelled`, and never contributes `match` — so a deployment whose
containers are all vendor artifacts (dstdns runs vault, authentik, consul
this way) could never leave `not-verified-no-evidence`, and `verified-match`
was unreachable live. The contract:

```toml
[deploy.provenance]
vendor_images = [
  "hashicorp/vault:1.15",
  "ghcr.io/goauthentik/server:2024.2.2",
  "hashicorp/consul:1.18",
]
```

- **Reference equality is the whole verdict for a declared image.** A running
  container whose image EQUALS a declared entry is `vendor-pinned` — judged
  by reference, NEVER by this repo's commit (an upstream revision label
  belongs to the upstream build; the label, if any, is still reported
  verbatim in the document). Equality is compared on CANONICAL references
  (Docker's own normalization: registry-host case-insensitivity, the
  implicit `docker.io`/`library/` defaults) so `nginx:1` and
  `docker.io/library/nginx:1` are the same pin; tags/digests stay
  case-sensitive verbatim. Digest pinning is deliberately NOT this
  feature: it adds a pin-file maintenance surface no consumer has today; the
  declaration says "this exact reference is expected to be third-party", and
  says no more.
- **Drift is a mismatch.** A container whose canonical image NAME matches a
  declared entry at any OTHER reference (`vault:1.15` declared,
  `vault:1.16` running) is `mismatch` — the declaration vouches for one
  artifact; a different one is running. Prose names the declaration ("not
  the declared vendor reference"), never a commit comparison that cannot
  apply.
- **The escape hatch is visible, not green.** Undeclared unlabelled images
  stay `unlabelled` in the document and contribute nothing to the verdict —
  declaring vendor images cannot HIDE a forgotten bake of an OWN image (it
  remains listed, per container, as `unlabelled`). What a pin DOES change is
  the overall: a tree that was `not-verified-no-evidence` (WARN) becomes
  `verified-match` once at least one container agrees with an expectation
  and none disagree — the same semantics an own-image `match` always had.
  Only falsely declaring one's own image as vendor escapes entirely, which
  is auditable config. Malformed `[deploy.provenance]` or `vendor_images`
  (not a list of non-empty strings) refuses exit 2: a silently ignored
  declaration would certify exactly the deployment it was written to vouch
  for.
- **Schema bump.** The widened closed vocabularies make every verdict
  `schema_version: 2`. Strict consumers refuse unknown members (fail-closed);
  the seven schema-1 fixtures remain frozen as the historical grammar record.

The `null` (enumeration did not/could not run) vs `[]` (enumeration ran,
found nothing informative) distinction is load-bearing: collapsing both to
`[]`, as the pre-S17.3 code did, let a docker-less host emit
`verified-match` with `containers: []` — a green provenance document
attesting nothing.

`ciu provenance --json` is `store_true` (matching `ciu diagnose --json`'s
shape exactly — NOT `[PATH|-]`) and prints ONLY the JSON document to stdout,
no prose mixed in. `cli._provenance` is the ONLY place that decides
prose/raise/warn behaviour from the verdict, and does so identically to the
pre-S17.3 CLI for every case that was already correctly handled (a genuine
mismatch, a dirty tree, a non-checkout, a verified match); the ONE case whose
prose is new is `not-verified-no-evidence`, which did not exist as a
distinguishable case before S17.3 and must never print "provenance OK".

### S17.4 — In-container revision exposure (CIU-21)

Every service's rendered overlay carries `CIU_IMAGE_REVISION=<revision>` in
its `environment`, where `<revision>` is read back from THAT service's OWN
image's `org.opencontainers.image.revision` label (S17.1) — never from
`get_git_hash()`, which is the host tree's CURRENT view, not the RUNNING
image's baked truth; comparing one against the other is a check that can
never fail, which is worse than no check.

The map is built ONCE per render/up pass in `engine.py` (already
docker-aware), immediately before Step 15's `generate_overlay` call, by
calling `deploy._image_revision_label` per service — never reimplementing its
label lookup. A service with no baked label, or a `build:` service with no
baked `image:` yet, is OMITTED from the map rather than given a placeholder
(mirrors S17.1: a value that looks like an answer would be trusted as one).
The map is passed into `composefile.generate_overlay` as a new
`image_revisions` keyword — plain data — so `composefile.py` gains NO
docker/procutil/subprocess import; the docker lookup lives entirely in
`engine.py`.

The injection is UNCONDITIONAL: it happens regardless of
`governance.enabled` and is not subject to `governance.exempt_services`
(S15's gate governs RESOURCE governance only). `environment` is an
append-never-clobber MERGE key (S15.11's KSM precedent) — an assignment
instead of an append would silently drop a co-located `LD_PRELOAD` entry, the
exact CIU-14 failure class one call site over. A non-empty `image_revisions`
map writes an overlay even when every other reason to write one is absent —
such a stack now gets an overlay where it previously had none, so
`engine.reset_service`'s existing `overlay_path.exists()` check newly
includes it in `docker compose down`'s `-f` args.

`--json` grammar and `S17.4`'s exposure are independent: neither depends on
the other, and a project may adopt either alone.

## S18 — Implementation gate (Assay-backed)

The implementation gate is the boundary between "green in the devcontainer
venv" (never a ship signal) and a reviewable verdict. It runs inside
`tester-unified` and is **judged by the released Assay CLI artifact**, never
by imported Assay source and never by a nyxloom evidence-judgment command.

- **S18.1** *Pinned, verified artifact.* The gate consumes the Assay CLI from
  a hash-pinned, vendored zipapp (`tools/assay/assay-<version>.pyz` + a
  `.sha256` sidecar in the same directory). Before every run the gate MUST
  verify the pin (`sha256sum -c`); a failed verification fails the gate. The
  artifact is the released, immutable `assay-v*` build (built from the wheel,
  not `src/`). tester-unified deliberately does not bake an ambient Assay
  version.
- **S18.2** *Lane contract.* `assay.toml` declares the `ciu` lane: the full
  suite under pytest-cov with a 100% whole-source line AND branch fail-under
  (`run-ciu-tests.py`) as the lane command, inside Assay's isolated snapshot
  (`snapshot_selection = "repository-minus-unsafe-symlinks"`, declaring the
  monorepo's three absolute-target security-fixture symlinks verbatim). The
  lane also declares R1: Assay judges the changed-line floor on
  `base..HEAD` (`fail_under = 100.0`, `require_branch = true`,
  `allow_excluded = false`) from the lane's coverage artifact. A new unsafe
  symlink anywhere in the repository reds the lane until its owner declares
  or untracks it (fail-closed).
- **S18.3** *Cgroup (fail-closed).* The gate resolves the container slice
  ONLY from `$CGROUP_PARENT_DEV_BACKGROUND` — no literal slice, no fallback;
  an absent variable is a hard error. Before `docker run` it verifies the
  named systemd slice is `LoadState=loaded` on the host; an unloaded or
  missing slice fails the gate. A typo'd slice name that systemd silently
  auto-creates as a transient slice is an operator error, not a value CIU
  invents.
- **S18.4** *Status and evidence.* The gate's exit status IS the Assay job's
  own exit status (no wrapper/pipe masking: the lane command's failure, or
  the verify step's failure, propagates as the gate's failure). The verdict
  is retained at `.assay/verdict-ciu.json` in the bound worktree (gitignored)
  for review. A lane run against a working tree with untracked, unignored
  files is refused (`NO_MEASUREMENT`/`DIRTY_TREE`) — the gate diffs committed
  objects, so the tree must be clean.
- **S18.5** *Manual reproduction.* Hand-run of the gate follows vbpub
  AGENTS.md "Manual tester-unified gate runs — the four traps": cgroup env
  passthrough, dual-path mount for worktree gitfiles, `safe.directory`, and
  the detached run form.

## S19 — Repository scaffolding (`ciu init`)

`ciu init [--project-name NAME] [--environment-tag TAG] [--stacks A,B]`
generates a minimal CIU-enabled repository layout: a validated
`ciu.global.defaults.toml.j2` (project identity, network from
`$DOCKER_NETWORK_INTERNAL`, health timings), gitignore entries (`ciu.env`,
`ciu.global.toml`, `**/.ciu/`, `**/ciu.compose.yml`), and optional stack
skeletons under `applications/<name>/` (defaults + compose template with one
GEN_LOCAL secret). Templates ship INSIDE the wheel (`ciu/templates/`) so a
plain `pip install ciu` carries them. Validation-first: the global template
is rendered through the real Jinja step and TOML-parsed BEFORE anything is
written; an existing target file is never overwritten — the run refuses
naming every existing target. It does NOT run `ciu env generate` (side
effects stay with the operator); the printed next steps say to.

- **S19.1** Hook template library (`ciu init --hooks NAME1,NAME2`,
  ciu-P20/CIU-QOL-13). CIU ships a small library of copyable hook
  implementations under `ciu.hook_templates` (`src/ciu/hook_templates/`,
  inside the wheel like `templates/`). Each module is a plain S9.1 hook:
  a module-level `template_revision: int` (starts at 1, incremented by the
  template's own author on every behavioral change to its `run`/
  `validate_config` bodies — comment/docstring-only edits do not need a
  bump), a `run(config, ctx) -> dict` (S9.1/S9.4), and an OPTIONAL
  `validate_config(config, ctx) -> list` (S9.5, findings as message strings
  or `(severity, message)` pairs) — the identical contracts, not a
  template-specific variant.
  `--hooks NAME1,NAME2` (composable with `init`'s other flags) copies each
  named template's file, byte-for-byte, into **every** stack scaffolded by
  that same `ciu init` invocation, at `<stack_dir>/hooks/<name>.py` (a
  relative path consistent with S9.1's "script paths relative to the stack
  dir" — a stack that declares it would write
  `[<root>.hooks].post_compose = ["./hooks/<name>.py"]` itself; `ciu init`
  does not write that declaration for you). The copy is prefixed with a
  one-line stamp comment naming the template and the `template_revision` it
  was copied at, in the exact, stably-parseable format
  `# ciu-hook-template: <filename> rev=<N>` — a future revision-comparison
  feature (tracked as a CIU-QOL-13 follow-up) reads this line to tell a
  consumer's stamped copy apart from a newer upstream revision.
  An unknown name in `--hooks` is a configuration error (exit 2, S10.3),
  naming the unknown name(s) and the available list, refused BEFORE any
  file is written; `--hooks` with no `--stacks` target to copy into is the
  same class of error. The existing "never overwrite an existing target"
  rule (this section, above) applies identically to copied hook files —
  they are validated and written through the same file list as every other
  scaffolded file, never special-cased into a silent overwrite.
  CIU ships exactly ONE reference template today, `post_compose_db.py`
  (generic database-readiness + secret-declaration demo); the backlog's
  other named templates (Vault/Consul/Redis/Authentik/Tailscale-shaped) are
  explicitly deferred to future packages once grounded against a real
  consumer's existing hook — see `nyxloom-trove/reports/
  ciu-P20-hook-template-library-LOG.md`.
