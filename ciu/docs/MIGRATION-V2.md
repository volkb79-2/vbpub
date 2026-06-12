# CIU v1 → v2 Migration Guide

> **Status: SKELETON** — recipes are outlined against the real dstdns
> inventory; finalized in Stage 3 alongside the rebuilt demo project.
> Normative behavior: [SPEC.md](SPEC.md). dstdns stays on the v1 wheel until
> this guide is final (hard-cut policy, no compat flag).

## Inventory to migrate (dstdns, measured 2026-06)

| Item | Count | Recipe |
|---|---|---|
| `GEN_TO_VAULT:` directives | 32 | §2 (placement check), §3 (consumption) |
| `ASK_VAULT:` directives | 24 | §2, §3 |
| `ASK_EXTERNAL:` directives | 11 | §2; prompts now persist (SPEC S4.13) |
| `GEN_LOCAL:` directives | 5 | §4 (project store; values regenerate ONCE) |
| `${*_SECRETS_*}` / `${ENV_*}` placeholders in compose templates | all stacks | §3 |
| Stacks (root keys) | 30 | §5 (one rename: `vault`) |
| `[deploy.groups]` | 6 groups | §6 (become profiles) |
| Hooks (`vault_env_pre_hook`, post-compose seeds) | ~5 scripts | §7 |

## §1 Pre-flight

- [ ] Upgrade wheel to ciu 2.x on a **branch**; run `ciu --render-toml` per
  stack — the v2 validator (SPEC S11) lists every violation with its spec ID.
- [ ] Add `**/.ciu/` to `.gitignore` (S1.7 aborts otherwise).
- [ ] Regenerate `.env.ciu` (`ciu --generate-env`); note `PUBLIC_*` keys are
  now required only when `require_fqdn`/`require_certs` is enabled (S2.3).

## §2 Move misplaced directives into `secrets` tables (S4.1, S4.5)

Directives are recognized **only** in `[<root>.secrets]` /
`[<root>.<service>.secrets]`; anywhere else they are a hard error.

Known dstdns offender (find others via the validator):

```toml
# applications/controller/ciu.defaults.toml.j2  (v1)
[controller.consul]
token = "GEN_TO_VAULT:{{ vault.paths.consul_controller_token }}"   # ← misplaced

# v2
[controller.secrets]
consul_token = "GEN_TO_VAULT:{{ vault.paths.consul_controller_token }}"
```

Also: every `ASK_VAULT_ONCE:` → `GEN_TO_VAULT:` (identical semantics, S4.3);
`DERIVE:` has no v2 equivalent — move composite values into a configfile
template using `secret()` (§8), or a hook.

## §3 Replace flattened secret placeholders (S8.2, S4.17–S4.19)

For each `${<ROOT>_SECRETS_<NAME>}` in a `docker-compose.yml.j2`:

1. Add `secrets: [<name>]` to the consuming service.
2. Pick the consumption pattern:
   - image supports files → `XYZ_PASSWORD_FILE=/run/secrets/<name>`
     (postgres, mariadb, grafana, ...);
   - no file support → `sh -c '… "$(cat /run/secrets/<name>)" …'` wrapper
     (redis: see SPEC Appendix B.1 — covers `--requirepass` **and** the
     healthcheck);
   - last resort → `expose_env = "<NAME>"` on the secret and keep `${NAME}`
     (S4.19; logged, discouraged).
3. Delete the old `environment:` line that injected the secret.

Non-secret `${FLATTENED}` and `${ENV_*}` references (rare; secrets were the
main user): replace with the direct Jinja value `{{ <root>.<path> }}`.
`${BUILD_VERSION}` → `{{ auto_generated.build_version }}` (S3.9).

## §4 GEN_LOCAL: one-time value regeneration (S4.9)

v1 stored GEN_LOCAL values in `ciu.toml [secrets.local]` — and rotated them
every run (v1 bug). v2 stores them as files in `<repo-root>/.ciu/secrets/`.
There is **no value carry-over**: on first v2 run each GEN_LOCAL secret is
generated fresh, **then stays stable forever**. For the 5 dstdns uses
(registry password etc.): plan one re-init of the consuming service, or
pre-seed the store file manually with the current value before the first run.

## §5 Stack shape (S3.5–S3.7)

- [ ] Rename root key `vault` → `vault_core` (collides with global
  `[vault.paths]`, S3.7); update its compose template references and any
  cross-stack reads of `infra/vault/ciu.toml`.
- [ ] Move any top-level `[env]` under the root key (`[<root>.env]`) —
  dstdns already complies.
- [ ] Delete `[secrets.local]`/`[secrets.state]` sections from rendered
  `ciu.toml` files (obsolete, S4.24) — or simply delete the rendered files
  and re-render.

## §6 Groups → host profiles (S7.4–S7.5)

```toml
# v1                                  # v2
[deploy.groups]                       [deploy.profiles.core_infra]
infra = ["phase_1", "phase_2"]        phases = ["phase_1", "phase_2"]

                                      [deploy.profiles.workers_host_b]
                                      phases = ["phase_4"]
                                      [deploy.profiles.workers_host_b.topology_overrides.services.vault]
                                      internal_host = "host-a.tailnet.example"
```

`[deploy.groups]` and `--groups` are **removed** (greenfield, S7.5) — the
validator rejects them with a pointer to profiles. Per host, set
`CIU_HOST_PROFILE` in that host's `.env.ciu` and order the runs manually
(core infra host first — S7.5a). Phase keys must be `phase_<int>` strings;
numeric ordering is now guaranteed (S7.1). `enabled` expressions become
`[deploy.control]` flag names (S7.2).

## §7 Hooks (S9, S4.16)

- **`vault_env_pre_hook.py` is deleted** — no v2 equivalent. The built-in
  token source order (`VAULT_TOKEN` env → `vault.token_file` → vault stack
  `[state]`) replaces it; remove it from every `pre_compose` list (~5
  stacks).
- Hook points are renamed/split: `[<root>.hooks]` now has `pre_secrets`
  (before secret resolution — provider bootstrap only), `pre_compose`
  (after materialization), `post_compose`. Classify each remaining v1
  pre-compose hook into the right slot.
- Hook modules expose `run(config, ctx)` (function or `Hook` class); v1's
  `pre_compose_hook`/`PostComposeHook` names are gone.
- **Returns are structured-only** (S9.4): the v1 plain `{KEY: value}`
  env-update form does nothing in v2 — rewrite hooks that exported env
  (`VAULT_TOKEN`, seeded values) to `apply_to_config` / `persist: "state"`.
- Hook files listed but missing now **abort** (S9.2) — prune dead entries.
- Hooks no longer see plaintext secrets in config (S9.3) — any hook reading
  `config[...]['secrets'][...]` must switch to `ctx.secret_file(name)` or
  `/run/secrets`.

## §7a Storage: fixed-UID images & init containers (S6.5–S6.7)

- Stacks using chown-init-containers (db-core pattern): delete the init
  container; declare the requirement on the hostdir instead, e.g.
  `data = { uid = 999, gid = "$DOCKER_GID", mode = "0770" }` — CIU
  provisions it (directly or via helper container, S6.5) and the operator
  keeps access through the docker group.
- timescaledb/postgres-style exclusive-ownership data with no need for
  host-side file access: switch to a compose **named volume** in the
  template (S6.7b); `--reset` still cleans it via `down -v`.
- Initial content previously copied by init containers: use hostdir
  `seed = "<dir>"` (first creation only, S6.6).
- `post-create.sh` / `env-workspace-setup-generate.sh`: replace their
  detection/generation/TLS-probe logic with a single `ciu --generate-env`
  call (S2.8); keep only aliases/SSH/IDE concerns.

## §8 Own apps → mounted TOML config (S5) — optional, per app

Per app (controller first, as the worked example — SPEC B.3):

1. Add `[<root>.<svc>.configfile.app] template/target`.
2. Write `config.toml.j2` using config values + `secret('name')` for DSNs.
3. Shrink compose `environment:` to bootstrap pointers
   (`APP_CONFIG=/etc/.../config.toml`).
4. App change: read TOML at the `APP_CONFIG` path (replaces the
   `APP__SECTION__KEY` env dataclass loader); keep env override support if
   desired.

This step is independent of §1–§7 and can roll out app-by-app; until then an
app keeps its env interface with secrets via `*_FILE`/wrapper/`expose_env`.

## §9 Verification checklist (per stack)

- [ ] `ciu -d <stack> --dry-run` passes validation, leak scan included.
- [ ] `ciu -d <stack>` twice → `diff` of `.ciu/secrets/` is empty
  (idempotency, S4.11) and `docker-compose.yml` contains no secret value.
- [ ] `docker exec <c> cat /run/secrets/<name>` matches the store file.
- [ ] `ciu-deploy --profile <name> --healthcheck` honest (pending ≠ pass).
