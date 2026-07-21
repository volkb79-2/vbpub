# cmru — Configurable Multi Release Utility

One release CLI for a **monorepo of independently-versioned products** that share a
**single** GitHub Releases page. cmru gives each product its own `<prefix><semver>` tag line and a monorepo-safe per-product "latest" (GitHub's repo-global *Latest* badge can only point at one release; cmru's resolver fixes that).

cmru is **just the orchestrator**: it owns the generic git/host mechanics (tags, commits, GitHub Releases, ghcr pruning, the `latest.json` pointer) and calls each project's own `build`/`push`/`clean` step commands for the artifact-specific work. No project logic is hardcoded in cmru.

## Install

```bash
pip install -e cmru          # provides the `cmru` console script
# or, from the repo root, with no install:
./cmru.py <verb>             # ≡ cmru <verb>   (discoverable cmru.*.sh shims wrap each verb)
```

## The model: two independent axes (S-REL)

A release is governed by two orthogonal choices, so the *same* versioning can publish very differently:

1. **Versioning** — `version.strategy`: `scm` | `counter` | `file:PATH` | `delegated` |  `none`. Computes the version string and whether cmru owns a git tag.
2. **Publish profile** — `artifacts = [...]`: one or more artifact profiles, each a preset
   capability bundle. A project may list **several** (their capabilities union).

| profile | git tag | GitHub Release + assets | ghcr push | `latest.json` | commit generated |
|---|:--:|:--:|:--:|:--:|:--:|
| `wheel` | ✓ | ✓ | — | ✓ | — |
| `bundle` | ✓ | ✓ | — | ✓ | — |
| `tarball` | ✓ | ✓ | — | ✓ | — |
| `oci-image` | — | — | ✓ | — | ✓ |

So a **wheel** (`ciu`, `cmru`) gets a semver tag + GitHub Release + `latest.json`; an **OCI image** (`modern-debian-tools-python-debug`) is pushed to ghcr with **no git tag and no Release** (its version is the image tag / `BUILD_DATE`), and cmru commits the regenerated manifests; GHCR package visibility is then reconciled to the source repository visibility; **pwmcp** emits *both* (`["oci-image", "bundle"]`).

`[project.X.release]` overrides a preset: `git_tag = false`, or `commit_generated = ["<project-relative path>"]` for build outputs cmru should commit.

## Verbs

```bash
cmru status                       # preview changed projects + next versions (read-only)
cmru release                      # one-shot: clean-gate → tag → push → build → publish
cmru release --dry-run            # show tags only, no writes
cmru release --project ciu        # one project
cmru build   --project <name>     # run the project's build step
cmru publish --project <name>     # run the project's push step
cmru resolve --project <name>     # resolve the current "latest" (version/tag/url/sha256)
cmru cleanup --remove-assets 30d  # prune old Releases / ghcr versions
cmru --help                       # all verbs, with a TYPICAL WORKFLOW block
```

`release` is idempotent: it detects changed projects, tags the tag-minting ones, then builds+publishes each by its profile (wheel → Release; oci-image → ghcr + manifest commit; delegated → the project self-versions).

## Reproducibility & the commit model

Before building, cmru requires the project's tracked source to be **clean** — commit first so the artifact maps to a committed state (and a wheel gets a clean `X.Y.Z` from setuptools-scm). cmru auto-commits **only** the declared `commit_generated` outputs (mechanical, e.g. OCI manifests) — never your hand-edited source.

## Config & secrets

| file | committed? | purpose |
|---|---|---|
| `cmru.toml` | yes | the one config (projects, profiles, orchestration) — **no secrets** |
| `cmru.sample.toml` | yes | template |
| `cmru.secret.toml` | no (gitignored) | `[github] token = "…"` overlay (optional; env wins) |
| `<project>/cmru.build.toml` | yes | per-project step config a project's build script reads |
| `cmru.vars` | no (gitignored) | `KEY=VALUE` build vars a step emits for later steps |

**Token resolution (S2.4):** `$GITHUB_PUSH_PAT` → `$GITHUB_TOKEN` → `cmru.secret.toml [github].token` → `cmru.toml [github].token` (discouraged). Never commit a token.

**Why `cmru.vars` is gitignored (and not a missing "starting point"):** it is a *generated scratchpad* — a build step writes computed values (e.g. pwmcp's playwright-driven version) for a *later* step in the **same** run to read. The committed starting point is git tags + `VERSION` files + `cmru.toml`; `cmru status`/`release` read those and never read `cmru.vars`. A fresh clone regenerates it on the next build. Committing it would turn a derived cache into an authoritative-looking input that drifts from the tags — the opposite of reproducible.

## Built-in profiles ("batteries included")

For a standard `wheel` project, declaring the profile is enough — cmru runs its own `build`/`push`/`validate` (see `cmru/handlers.py`), so the project needs **no release scripts**. cmru itself is the dogfood (`[project.cmru]` has `artifacts = ["wheel"]` and zero `[steps.*]`; only a `CMRU_RELEASE_NOTES` string). The single project-specific input is the release-notes text. An explicit `[project.X.steps.<step>]` always overrides the built-in — the escape hatch for multi-wheel repos, bespoke validation, or extra assets.

The built-in `oci-image` profile supports the normal Buildx bake load/push flow. Its
`[project.X.oci].repack = true` switch is currently **experimental and fail-closed**:
cmru rejects it before authentication or Docker work. The complete safety and
production-equivalence requirements are tracked in [SPEC S14.3](docs/SPEC.md#s143--repack-flow-experimental-fail-closed).

## Differentiators

1. **N products, one Releases page** via per-product `prefix` (`ciu-v…`, `pwmcp-v…`).
2. **Per-product "latest"** — `cmru resolve` returns the highest-semver release for a prefix; `<prefix>-latest` holds a thin `latest.json` pointer, not a duplicated asset.
3. **Profile-driven publishing** — wheels, OCI images, bundles and tarballs each release correctly from one config, with cmru as the generic orchestrator.
4. **Per-interpreter variants** (S-REL.6) — a `bundle`/`tarball` may declare `[[project.X.variants]]` so one tag publishes one asset per variant (`<tag>-<variant><suffix>`); the generated `get.py` installer selects one explicitly with `--variant NAME`. Zero declared variants keeps the single-asset path unchanged.

## cmru vs ciu

cmru is the **outer loop** (build-to-release: version + publish across products). Its sibling **ciu** is the **inner loop** (build-to-run: build local images and run a stack on this host). They overlap only in that both can trigger a docker build — over the *same* `docker-bake.hcl`, for different ends (ciu `--load`s + runs; cmru pushes). Full map, incl. the border question: [`../docs/ciu-vs-cmru.md`](../docs/ciu-vs-cmru.md).

## More

- Full contract & rationale: [`docs/SPEC.md`](docs/SPEC.md) — start at *S-CLI* and *S-REL*.
- Monorepo tooling overview: [`../docs/RELEASE-TOOLING.md`](../docs/RELEASE-TOOLING.md).
- Release-modes design/plan: [`../docs/plan-cmru-release-modes.md`](../docs/plan-cmru-release-modes.md).
