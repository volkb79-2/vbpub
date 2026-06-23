# CIU v2 reference demo (`test-repo/`)

A miniature [dstdns](../docs/SPEC.md)-shaped workspace that exercises **every v2
feature** with **public images**, so a live `docker compose up` smoke works on
any machine. It doubles as the integration fixture (`tests/tests/`) and as
documentation-by-example: every file is annotated with the spec IDs (`S#.#`) it
demonstrates. Read the templates alongside [`docs/SPEC.md`](../docs/SPEC.md).

> Paths below are relative to this directory (`test-repo/`).

## What each stack demonstrates

| Stack | Root key | Image | Spec features |
|---|---|---|---|
| `infra/vault` | `vault_core` | `hashicorp/vault:1.17` (DEV mode) | bootstrap provider; `GEN_LOCAL` root token (a vault stack MUST NOT use `*_VAULT`, **S7.6**); S4.18 entrypoint wrapper; `post_compose` hook persists `root_token`+`initialized` to `[state]` (**S9.4**) → feeds the **S4.16** token source #3 for later stacks; root key renamed off the reserved `vault` namespace (**S3.7 / B.2**) |
| `infra/redis-core` | `redis_core` | `redis:7-alpine` | third-party image, no `*_FILE` support → S4.18 wrapper for `--requirepass` + healthcheck (**SPEC B.1** verbatim); `GEN_TO_VAULT` (**S4.2/S4.11**); auto hostdir (**S6.1/S6.2**) |
| `infra/db-core` | `db_core` | `postgres:16-alpine` | the `*_FILE` convention (`POSTGRES_PASSWORD_FILE`, **S4.17**) — no wrapper; fixed-UID hostdir `{uid=70, mode="0770"}` (**S6.7a**); `GEN_TO_VAULT` |
| `applications/app-config` | `app_config` | `python:3.12-alpine` | **own app**: all four non-Vault directives — `GEN_LOCAL`, `ASK_EXTERNAL`, `GEN_EPHEMERAL`, `ASK_FILE` (**S4.2**); configfile mount + `secret()` (**S5**); `pre_compose` hook `apply_to_config` (**S9.4**); env-as-pointer boundary (**S5.5**); runs its **full** pipeline under `--dry-run` with no Vault |
| `applications/workers` | `workers` | `python:3.12-alpine` | **replicated service**: `instances = 2` → compose keys `worker-1`/`worker-2`; ONE base configfile section `[workers.worker.configfile.main]` **fans out** to both instances (**S5.3 / CIU-2**) instead of one section per replica; `secret()` in the configfile is the S4.20 configfile consumption channel; carries a `[workers.dev]` dev-loop profile (**S5a / CIU-5**) |
| `shipped-example` | — | `alpine:3` | **dual shipping** (**S8.5/S8.6**): a hand-written, committed `docker-compose.yml` with **no** CIU config; run it plainly or via `ciu --shipped` |

Global config (`ciu.global.defaults.toml.j2`) demonstrates: phases in numeric
order (**S7.1**), the `enabled = "enable_app"` string control flag (**S7.2** +
`[deploy.control]`), host **profiles** with `topology_overrides` (**S7.4 /
S7.5a**), the `[vault.paths]` map + `topology.services.vault` address (**S4.16**),
label prefix (**S6.4**), and health timings (**S7.7**).

### Host profiles (S7.4) vs compose profiles (S7.5a)

"Host profile" (`[deploy.profiles.*]`, selected with `--profile`) decides **which
stacks run on this host**. Compose `profiles` (`compose_profiles`) decide **which
services inside a stack** are activated. They are distinct concepts.

- `core_infra` → phases 1–2 (Vault + data). Deploy on host A first.
- `workers` → phase 3 (the app). Its `topology_overrides.services.vault` shows
  the **S7.5a** cross-host mechanism (here it points at the same dev address; on
  a real host B it would point at host A's externally reachable Vault).
- `all` → every phase, single-host convenience.

## How to run

```sh
# 0. ASK_EXTERNAL (S4.13): app-config's `license` secret must be supplied.
export CIUDEMO_LICENSE=demo            # or: export CIU_SECRET_LICENSE=demo

# 1. Bootstrap the machine-identity env file (ciu.env) — single entry point (S2.8).
ciu --generate-env

# 2. Bring up the shared backbone (Vault, then Redis + Postgres) — host A.
ciu-deploy --deploy --profile core_infra

# 3. Bring up the application tier — host B in a multi-host setup (S7.5a).
ciu-deploy --deploy --profile workers

#    Single host? Just use the `all` profile instead of steps 2+3:
#    ciu-deploy --deploy --profile all

# 4. Inspect a stack's secrets (names/locators/store paths only — never values, S4.25).
ciu secrets list -d applications/app-config

# 5. Tear down (containers, volumes, rendered artifacts).
ciu-deploy --clean -y
#    Reset a single stack incl. its [state] and (optionally) secret files:
#    ciu --reset --secrets -y -d infra/vault
```

`ciu-deploy --render-toml --profile all` renders every stack's `ciu.toml`
without starting anything (**S8.3** step 3) — handy for reviewing the merged
config.

### Provisioning graph (S13)

The infra stacks declare `provides` and the application stacks declare `requires`
— typed refs in each stack's **root-key table** (e.g. `[db_core]`), not a
`[stack]` table. The fixture exercises every ref kind: `vault:secret/…`,
`pg:role/…`, `pg:db/…`, `pg:schema/…` (4.2), `minio:user/…`, `consul:token/…`
(4.2 — config-driven Vault path via `[registry.consul] token_vault_path`), and
`stack:<name>:healthy` (a one-shot init container that has **exited 0** counts as
satisfied). The graph is self-consistent, so:

```sh
ciu check                       # static lint of the requires/provides graph (no deploy)
ciu check --live                # also probe live state (run after the stack is up)
ciu graph                       # render as Mermaid (default) — pipe into a Markdown doc
ciu graph --format dot | dot -Tsvg > graph.svg
ciu graph --format json         # machine-readable {stacks, edges}
```

On a greenfield `ciu up` the static lint runs once up-front and the live probe
runs **per-phase** (after each provider phase is up), so no `--no-preflight` is
needed. See SPEC S13 for the full grammar and probe semantics.

### Dual shipping (S8.5/S8.6)

`shipped-example/` carries only a hand-written `docker-compose.yml` — the
"plain" path a maintainer can ship alongside the CIU path. CIU renders its own
`ciu.compose.yml` and never touches `docker-compose.yml`:

```sh
docker compose -f shipped-example/docker-compose.yml up -d   # no CIU at all
ciu --shipped -d shipped-example                             # through CIU
```

`ciu --shipped` still loads `ciu.env`, ensures the network, and runs the DooD
preflight before `docker compose up` — so `${DOCKER_NETWORK_INTERNAL}` in the
shipped file resolves to the same machine-identity value the CIU path uses. In
`ciu-deploy`, a service entry with `shipped = true` routes that stack through
its `docker-compose.yml` while still honoring phases and the health gate.

### Notes

- `CIUDEMO_LICENSE` (or `CIU_SECRET_LICENSE`) **must** be set before running
  app-config — it is the `ASK_EXTERNAL` demo (**S4.13**); without it the run
  aborts (exit 2) rather than inventing a value.
- The Vault stack runs in **dev mode** (in-memory, auto-unsealed) so the live
  smoke needs no manual init/unseal. Production would use a real backend + an
  unseal `pre_secrets` hook.
- Everything under any `.ciu/` directory, plus `ciu.global.toml`, `ciu.toml`,
  and `ciu.compose.yml`, is machine-generated and gitignored (**S1.6–S1.8**).
