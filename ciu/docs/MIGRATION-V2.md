# CIU v1 → v2 Migration Guide

Normative behavior: [SPEC.md](SPEC.md). Where this guide and SPEC conflict, SPEC
wins. dstdns stays on the v1 wheel until this guide is complete (hard-cut
policy, no compat flag).

---

## §0 What Changed at a Glance

See **SPEC Appendix C** for the full delta summary.

Key removals (greenfield — no aliases, no fallbacks):
- Env flattening: `ENV_<KEY>` / `UPPER_SNAKE` placeholders, `flatten_dict`
- Directives: `ASK_VAULT_ONCE` (→ `GEN_TO_VAULT`), `DERIVE` (→ `secret()` or hook)
- `[secrets.local]` / `[secrets.state]` in `ciu.toml`
- Top-level `[env]` (→ `[<root>.env]`)
- `eval()` on `enabled` expressions (→ named flag in `[deploy.control]`)
- `[deploy.groups]` / `--groups` (→ `[deploy.profiles]` / `--profile`)
- Hook env-update returns + per-point function names (→ structured return only)
- `vault_env_pre_hook` pattern (→ S4.16 built-in token source order)
- `SERVICE_CONFIG_*` constants, relative `./vol-*` emission
- `bare-metal` / `local` ENV_TYPE values (→ `native`)

Key additions: secrets-as-files, generated overlay, `ASK_FILE`, `#field` Vault
selector, `expose_env` / `mode` / `uid` inline options, configfile mounts +
`secret()`, host profiles + `topology_overrides` + `CIU_HOST_PROFILE`, numeric
phases, three hook points with structured-only returns, hostdir inline options
+ helper-container provisioning, `ciu secrets` subcommands, exit-code contract,
leak scan, native-host parity.

### File renames (unified `ciu.` prefix, S1.8/S8.5)

Every CIU file now groups under a `ciu.` prefix; rename on migration (the rename
is the only change for most files — content is unaffected):

| v1 name | v2 name |
|---|---|
| `ciu-global.defaults.toml.j2` | `ciu.global.defaults.toml.j2` |
| `ciu-global.toml.j2` / `ciu-global.toml` | `ciu.global.toml.j2` / `ciu.global.toml` |
| `docker-compose.yml.j2` | `ciu.compose.yml.j2` |
| `docker-compose.yml` (CIU's rendered output) | `ciu.compose.yml` |
| `.env.ciu` | `ciu.env` |
| `.ciu/docker-compose.ciu.yml` (overlay) | `.ciu/ciu.compose.overlay.yml` |

`ciu.defaults.toml.j2` / `ciu.toml.j2` / `ciu.toml` are unchanged. Update
`.gitignore` from the new [`.gitignored.ciu`](../.gitignored.ciu); external
scripts (devcontainer `post-create.sh`, etc.) that referenced `.env.ciu` or
sourced it now use `ciu.env`.

**Dual shipping (optional, S8.5–S8.6):** because CIU now renders to
`ciu.compose.yml`, the name `docker-compose.yml` is freed for a hand-written
compose you MAY commit for a plain `docker compose up` / `ciu --shipped` path.
You don't have to add one — but if a stack already had a hand-edited compose you
wanted to keep runnable as-is, commit it as `docker-compose.yml` and mark its
deploy service `shipped = true`.

### Validator-driven workflow

The fastest path from v1 to v2 is to let the validator tell you what to fix:

```bash
# 1. Upgrade wheel on a branch
pip install ciu==2.x

# 2. Render TOML per stack — every [S-xx] error identifies an exact fix
ciu -d <stack> --render-toml

# 3. After fixing stack config, validate the orchestrator
ciu-deploy --render-toml --profile <profile>

# 4. Dry-run (full pipeline, no containers)
ciu -d <stack> --dry-run

# 5. Live
ciu -d <stack>
```

Each validation error in step 2 references a spec ID; look it up in SPEC.md to
understand the contract and use the section in this guide that matches it.

---

## Inventory to Migrate (dstdns, measured 2026-06)

| Item | Count | Recipe |
|---|---|---|
| `GEN_TO_VAULT:` directives | 32 | §2 (placement check), §3 (consumption) |
| `ASK_VAULT:` directives | 24 | §2, §3 |
| `ASK_EXTERNAL:` directives | 11 | §2; prompts now persist [S4.13] |
| `GEN_LOCAL:` directives | 5 | §4 (project store; values regenerate ONCE) |
| `${*_SECRETS_*}` / `${ENV_*}` placeholders in compose templates | all stacks | §3 |
| Stacks (root keys) | 30 | §5 (one rename: `vault`) |
| `[deploy.groups]` | 6 groups | §6 (become profiles) |
| Hooks (`vault_env_pre_hook`, post-compose seeds) | ~5 scripts | §7 |

---

## §1 Pre-flight

- [ ] Upgrade wheel to ciu 2.x on a **branch**; run `ciu --render-toml` per
  stack — the v2 validator [S11] lists every violation with its spec ID.
- [ ] Add `**/.ciu/` to `.gitignore` [S1.7 aborts otherwise].
- [ ] Regenerate `ciu.env` (`ciu --generate-env`); note `PUBLIC_*` keys are
  now required only when `require_fqdn`/`require_certs` is enabled [S2.3].

---

## §2 Move Misplaced Directives into `secrets` Tables [S4.1, S4.5]

Directives are recognized **only** in `[<root>.secrets]` /
`[<root>.<service>.secrets]`; anywhere else they are a hard error.

Known dstdns offender (find others via the validator):

```toml
# applications/controller/ciu.defaults.toml.j2  (v1 — REMOVED IN V2)
[controller.consul]
token = "GEN_TO_VAULT:{{ vault.paths.consul_controller_token }}"   # misplaced

# v2
[controller.secrets]
consul_token = "GEN_TO_VAULT:{{ vault.paths.consul_controller_token }}"
```

Also: every `ASK_VAULT_ONCE:` → `GEN_TO_VAULT:` (identical semantics [S4.3]);
`DERIVE:` has no v2 equivalent — move composite values into a configfile
template using `secret()` (§8), or a hook.

---

## §3 Replace Flattened Secret Placeholders [S8.2, S4.17–S4.19]

`${<ROOT>_SECRETS_<NAME>}` and `${ENV_<KEY>}` placeholders no longer exist —
env flattening is withdrawn [S8.2].

For each `${<ROOT>_SECRETS_<NAME>}` in a `ciu.compose.yml.j2`:

1. Add `secrets: [<name>]` to the consuming service.
2. Pick the consumption pattern:
   - Image supports files → `XYZ_PASSWORD_FILE=/run/secrets/<name>`
     (postgres, mariadb, grafana, …);
   - No file support → `sh -c '… "$(cat /run/secrets/<name>)" …'` wrapper
     (redis: see SPEC Appendix B.1 — covers `--requirepass` **and** the
     healthcheck). Test-repo: `test-repo/infra/redis-core/ciu.compose.yml.j2`
   - Last resort → `expose_env = "<NAME>"` on the secret and keep `${NAME}`
     [S4.19; logged, discouraged].
3. Delete the old `environment:` line that injected the secret.

Non-secret `${FLATTENED}` and `${ENV_*}` references: replace with the direct
Jinja2 value `{{ <root>.<path> }}`.
`${BUILD_VERSION}` → `{{ auto_generated.build_version }}` [S3.9].

---

## §4 GEN_LOCAL: One-Time Value Regeneration [S4.9]

v1 stored GEN_LOCAL values in `ciu.toml [secrets.local]` — and rotated them
every run (v1 bug A1). v2 stores them as files in `<repo-root>/.ciu/secrets/`.
There is **no value carry-over**: on first v2 run each GEN_LOCAL secret is
generated fresh, **then stays stable forever**. For the 5 dstdns uses (registry
password etc.): plan one re-init of the consuming service, or pre-seed the
store file manually with the current value before the first run.

---

## §5 Stack Shape [S3.5–S3.7]

- [ ] Rename root key `vault` → `vault_core` (collides with global
  `[vault.paths]`, S3.7); update compose template references and any
  cross-stack reads of `infra/vault/ciu.toml`.
  Test-repo: `test-repo/infra/vault/ciu.defaults.toml.j2`
- [ ] Move any top-level `[env]` under the root key (`[<root>.env]`) —
  dstdns already complies.
- [ ] Delete `[secrets.local]`/`[secrets.state]` sections from rendered
  `ciu.toml` files [S4.24] — or simply delete the rendered files and re-render.

---

## §6 Groups → Host Profiles [S7.4–S7.5]

```toml
# v1 (REMOVED IN V2)             # v2
[deploy.groups]                   [deploy.profiles.core_infra]
infra = ["phase_1", "phase_2"]    phases = ["phase_1", "phase_2"]

                                  [deploy.profiles.workers_host_b]
                                  phases = ["phase_4"]
                                  [deploy.profiles.workers_host_b.topology_overrides.services.vault]
                                  internal_host = "host-a.tailnet.example"
```

`[deploy.groups]` and `--groups` are **removed** (greenfield, S7.5) — the
validator rejects them with a pointer to profiles. Per host, set
`CIU_HOST_PROFILE` in that host's `ciu.env` and order the runs manually
(core infra host first — S7.5a). Phase keys must be `phase_<int>` strings;
numeric ordering is now guaranteed [S7.1]. `enabled` expressions become
`[deploy.control]` flag names [S7.2].

---

## §7 Hooks [S9, S4.16]

- **`vault_env_pre_hook.py` is deleted** — no v2 equivalent. The built-in
  token source order (`VAULT_TOKEN` env → `vault.token_file` → vault stack
  `[state]`) replaces it; remove it from every `pre_compose` list (~5 stacks).
  Test-repo: `test-repo/infra/vault/post_compose_vault.py` — shows how the token
  is persisted to `[state]` for the S4.16 resolver.
- Hook points are renamed/split: `[<root>.hooks]` now has `pre_secrets`
  (before secret resolution — provider bootstrap only), `pre_compose`
  (after materialization), `post_compose`. Classify each remaining v1
  pre-compose hook into the right slot.
- Hook modules expose `run(config, ctx)` (function or `Hook` class with `run`).
  v1 function names `pre_compose_hook` / `PostComposeHook` are withdrawn.
- **Returns are structured-only** [S9.4]: the v1 plain `{KEY: value}`
  env-update form does nothing in v2 — rewrite hooks that exported env
  (`VAULT_TOKEN`, seeded values) to use `apply_to_config` / `persist: "state"`.
  Test-repo: `test-repo/applications/app-config/pre_compose_app.py` (apply_to_config),
  `test-repo/infra/vault/post_compose_vault.py` (persist: "state").
- Hook files listed but missing now **abort** [S9.2] — prune dead entries.
- Hooks receive the merged config with secret **guard objects** [S9.3, S4.21];
  any hook reading `config[...]['secrets'][...]` expecting a value will see a
  guard abort — switch to `ctx.secret_file(name)` or `/run/secrets` inside a
  container.

---

## §7a Storage: Fixed-UID Images and Init Containers [S6.5–S6.7]

- Stacks using chown-init-containers (db-core pattern): delete the init
  container; declare the requirement on the hostdir instead:
  ```toml
  data = { uid = 999, gid = "$DOCKER_GID", mode = "0770" }
  ```
  CIU provisions it [S6.5] and the operator keeps access through the docker
  group. Test-repo: `test-repo/infra/db-core/ciu.defaults.toml.j2`
- timescaledb/postgres-style exclusive-ownership data with no need for
  host-side file access: switch to a compose **named volume** in the template
  [S6.7b]; `--reset` still cleans it via `down -v`.
- Initial content previously copied by init containers: use hostdir
  `seed = "<dir>"` (first creation only, S6.6).
- `post-create.sh` / `env-workspace-setup-generate.sh`: replace their
  detection/generation/TLS-probe logic with a single `ciu --generate-env`
  call [S2.8]; keep only aliases/SSH/IDE concerns.

---

## §8 Own Apps → Mounted TOML Config [S5] — Optional, Per App

Per app (controller first, as the worked example — SPEC B.3):

1. Add `[<root>.<svc>.configfile.app] template/target`.
2. Write `config.toml.j2` using config values + `secret('name')` for DSNs.
   Test-repo: `test-repo/applications/app-config/config.toml.j2`
3. Shrink compose `environment:` to bootstrap pointers
   (`APP_CONFIG=/etc/.../config.toml`).
4. App change: read TOML at the `APP_CONFIG` path (replaces the
   `APP__SECTION__KEY` env dataclass loader); keep env override support if
   desired.

This step is independent of §1–§7 and can roll out app-by-app; until then an
app keeps its env interface with secrets via `*_FILE` / wrapper / `expose_env`.

---

## §9 Verification Checklist (Per Stack)

- [ ] `ciu -d <stack> --dry-run` passes validation, leak scan included.
- [ ] `ciu -d <stack>` twice → `diff` of `.ciu/secrets/` is empty
  (idempotency, S4.11) and `ciu.compose.yml` contains no secret value.
- [ ] `docker exec <c> cat /run/secrets/<name>` matches the store file.
- [ ] `ciu-deploy --profile <name> --healthcheck` honest (`starting` ≠ pass) [S7.7].
