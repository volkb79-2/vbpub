# CIU v2 — Documentation Index

## Start here

| Document | Purpose |
|---|---|
| [FEATURES.md](FEATURES.md) | **Canonical feature list + CLI surface.** Capability matrix, the v3 `ciu <verb>` reference, common workflows, and the edge cases worth knowing. |

## Normative

| Document | Purpose |
|---|---|
| [SPEC.md](SPEC.md) | **Single normative contract.** All `S<section>.<n>` IDs live here. On any conflict between SPEC and any other document, SPEC wins. |

## User Guides (non-normative)

| Document | Audience |
|---|---|
| [CIU.md](CIU.md) | Single-stack CLI users: quick start, 17-step pipeline, stack authoring, hooks, secret directives, dev loop |
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
| `test-repo/applications/workers/` | Replicated service: ONE configfile section fanned out to `worker-1`/`worker-2` (S5.3 / CIU-2), plus a `[workers.dev]` dev-loop profile (S5a / CIU-5) |
| `test-repo/ciu.global.defaults.toml.j2` | Full annotated global config: `[ciu]`, `[deploy]`, phases, host profiles, `topology_overrides` |

## Build and Install

```bash
# Editable install (development)
pip install -e /path/to/vbpub/ciu

# Build + publish the wheel via cmru's built-in handler (from the repo root)
cmru build   --project ciu   # python -m build --wheel
cmru publish --project ciu   # requires GITHUB_PUSH_PAT, GITHUB_USERNAME, GITHUB_REPO
```

### Installing a released wheel from GitHub Releases

The release scheme uses **immutable `ciu-v<version>` releases** as the source
of truth. Each release carries the wheel and a `.whl.sha256` sidecar.

```bash
# Install the latest release (substitute the resolved version)
pip install https://github.com/<owner>/<repo>/releases/download/ciu-v<version>/ciu-<version>-py3-none-any.whl

# Pin a specific version
pip install https://github.com/<owner>/<repo>/releases/download/ciu-v2.0.1/ciu-2.0.1-py3-none-any.whl

# Verify integrity before installing
curl -LO https://github.com/<owner>/<repo>/releases/download/ciu-v<version>/ciu-<version>-py3-none-any.whl
curl -LO https://github.com/<owner>/<repo>/releases/download/ciu-v<version>/ciu-<version>-py3-none-any.whl.sha256
sha256sum -c ciu-<version>-py3-none-any.whl.sha256
pip install ciu-<version>-py3-none-any.whl
```

**Resolving "latest":** the highest-semver `ciu-v*` release is the latest. The
`ciu-latest` tag exists only as a thin redirect (`latest.json` manifest) — it
does not carry a copy of the wheel. Use `cmru resolve --project ciu` to
resolve the current version and print the download URLs:

```bash
cmru resolve --project ciu
```

## Running Tests

```bash
python3 run-ciu-tests.py
# or directly:
python3 -m pytest tests/tests/ -q
```
