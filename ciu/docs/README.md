# CIU v2 — Documentation Index

## Normative

| Document | Purpose |
|---|---|
| [SPEC.md](SPEC.md) | **Single normative contract.** All `S<section>.<n>` IDs live here. On any conflict between SPEC and any other document, SPEC wins. |

## User Guides (non-normative)

| Document | Audience |
|---|---|
| [CIU.md](CIU.md) | Single-stack CLI users: quick start, 17-step pipeline, stack authoring, hooks, secret directives |
| [CIU-DEPLOY.md](CIU-DEPLOY.md) | Multi-stack orchestration: actions, host profiles, multi-host workflow, phases, health gate |
| [CONFIG.md](CONFIG.md) | Configuration reference: file roles, layering, all sections + spec IDs, directive table, `ciu.env` key provenance |

## Migration

| Document | Audience |
|---|---|
| [MIGRATION-V2.md](MIGRATION-V2.md) | Projects migrating from v1: inventory, per-item recipes, validator-driven workflow, verification checklist |

## Contributors

| Document | Audience |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Contributors: module map, invariants, data-flow sketch |
| [plans/V2-PACKETS.md](plans/V2-PACKETS.md) | Work-packet history and stage gates |

## Demo Repo

`test-repo/` — the living reference implementation. Every pattern in the guides
has a corresponding file here:

| Stack | Shows |
|---|---|
| `test-repo/infra/vault/` | Bootstrap stack, `GEN_LOCAL`, `[state]` persistence, `post_compose` hook, S4.18 wrapper |
| `test-repo/infra/redis-core/` | `GEN_TO_VAULT`, S4.18 wrapper pattern, auto-generated hostdir |
| `test-repo/infra/db-core/` | `GEN_TO_VAULT`, `*_FILE` convention, fixed-UID hostdir [S6.7a] |
| `test-repo/applications/app-config/` | All non-Vault directives, configfile mount + `secret()`, `pre_compose` hook, `apply_to_config` |
| `test-repo/ciu.global.defaults.toml.j2` | Full annotated global config: `[ciu]`, `[deploy]`, phases, host profiles, `topology_overrides` |

## Build and Install

```bash
# Editable install (development)
pip install -e /path/to/vbpub/ciu

# Build wheel
python -m pip wheel . -w dist

# Publish wheel to GitHub Releases
python3 publish-wheel.py    # requires GITHUB_PUSH_PAT, GITHUB_USERNAME, GITHUB_REPO
```

## Running Tests

```bash
python3 run-ciu-tests.py
# or directly:
python3 -m pytest tests/tests/ -q
```
