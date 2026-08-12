# cmru — Configurable Multi Release Utility

One release CLI for a **monorepo of independently-versioned products** that share a
**single** GitHub Releases page. cmru gives each product its own `<prefix><semver>` tag line and a monorepo-safe per-product "latest" (GitHub's repo-global *Latest* badge can only point at one release; cmru's resolver fixes that).

cmru is **just the orchestrator**: it owns the generic git/host mechanics (tags, commits, GitHub Releases, ghcr pruning, the `latest.json` pointer) and calls each project's own `build`/`push`/`clean` step commands for the artifact-specific work. No project logic is hardcoded in cmru.

## Install

```bash
pip install -e cmru          # provides the `cmru` console script
# or, from the repo root, with no install:
./cmru.py <verb>             # ≡ cmru <verb>   (discoverable cmru.*.sh shims cover common release verbs)
```

## The model: two independent axes (S-REL)

A release is governed by two orthogonal choices, so the *same* versioning can publish very differently:

1. **Versioning** — `version.strategy`: `scm` | `counter` | `file:PATH` | `external:VAR` | `none`. Computes the version string and whether cmru owns a git tag.
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
cmru release                      # isolated: prepare → gate → integrate → tag → build → publish
cmru release --dry-run            # show tags only, no writes
cmru release --project ciu        # one project
cmru changelog --project assay --backfill-tag assay-v0.1.0  # catalog a pre-history release
cmru standards                    # strict config + project-framework conformance
cmru standards --project pwmcp --update  # safely update CMRU-owned revision markers
cmru build   --project <name>     # run the project's build step
cmru publish --project <name>     # run the project's push step
cmru resolve --project <name>     # resolve the current "latest" (version/tag/url/sha256)
cmru cleanup --remove-assets 30d  # prune old Releases / ghcr versions
cmru --help                       # all verbs, with a TYPICAL WORKFLOW block
```

`release` detects changed projects, tags the tag-minting ones, then builds+publishes each by
its profile (wheel → Release; oci-image → ghcr + provenance commit). A retained transaction
is the pre-tag debug/recovery path. CMRU deliberately does not claim to complete a failed
post-tag publish yet; see [KI-06](KNOWN_ISSUES_TODO_BACKLOG.md#ki-06--durable-post-tag-publication-resume--open-scoped-deliberately).

## Logging and live diagnostics

Use the root wrapper directly—no `2>&1 | tee ...` is required:

```bash
./cmru.release.sh --project assay
```

It overwrites the root `cmru.release.log` with the complete release transcript.
The terminal stays readable: CMRU reports command labels, duration, known test-framework
success evidence, and concise failure excerpts. Detailed subprocess output is line-flushed to
both that audit log and stable per-project files such as `logs/assay/run-tests.log`; successful
release-worktree cleanup does not remove them.

```bash
./cmru.release.sh --project modern-debian-tools-python-debug --show-run-details
./cmru.release.sh --project assay --log-append
```

`--show-run-details` also streams raw Docker/test output to the terminal. `--log-append`
preserves the prior root and per-step logs, adding an exact `---` divider before the new run.
CMRU sets `PYTHONUNBUFFERED=1` for Python child processes and flushes every received line;
non-Python tools must still flush their own output.

## Project framework and templates

CMRU’s central `cmru.toml` is the project integration point. Each project carries
`template_revision = 1`, which lets `cmru standards` identify stale adoption without inventing
project behavior. A bespoke local runner config uses the matching two-line revision header in
`cmru.build.toml`. Ready-to-copy examples are
[`templates/project-release.toml.tmpl`](templates/project-release.toml.tmpl) and
[`templates/cmru.build.toml.tmpl`](templates/cmru.build.toml.tmpl).

`cmru standards --update` changes only those CMRU-owned markers. It never rewrites a project’s
build/publish commands; a remaining warning is a real policy decision to review.
Project-local runner controls are strict too: required `project_root`, `release_config`, and
`log_dir`, explicit `quiet`, and no unknown execution keys. Put project-only data beneath
`[project_metadata]` so a misspelled runner setting fails before it can alter a release.

## Release history is automatic

Every CMRU-managed project gets a project-local `CHANGES.md` by default. No project
script, config opt-in, or pre-created file is required. During `cmru release`, CMRU
derives the project-scoped git range, writes one marked entry, commits it with any
declared mechanical inputs, and runs the release gate against that commit. A tagged
release is headed by its pending version; an image-only release is headed
by the source revision it describes and advances a persisted source cursor. Generated
history and other declared mechanical outputs are excluded from the next source range.
If an image's private `prepare` step changed declared provenance but has no new source
commit, CMRU records a metadata-only history entry for that real new image; a clean
retained resume adds nothing.

Use `[project.X.release] changelog = "docs/CHANGES.md"` only to choose another
project-relative filename. `changelog = false` is the deliberate, reviewable opt-out.
Never add the usual `CHANGES.md` opt-in just to enable the feature—it is already on.

For a release that was published before this default existed, use the migration helper:

```bash
cmru changelog --project assay --backfill-tag assay-v0.1.0
git diff -- assay/CHANGES.md
git commit --only -m "docs(assay): backfill v0.1.0 release history" -- assay/CHANGES.md
```

It does not move the immutable tag. The resulting entry is visibly marked
`backfilled-after-release`; all future entries are source-first and carried by their
release tag.

## Reproducibility & the commit model

## Isolated release transactions

Run `cmru release` from your ordinary checkout—even if unrelated work is in progress.
cmru fetches `origin/main`, rejects local-only `main` commits that the snapshot would omit,
and creates a temporary `cmru/release/<id>` worktree at that exact commit. A local `main`
behind the remote is warned about but safe because the remote is authoritative. This matters
because setuptools-scm sees the
whole Git worktree: a harmless edit in another project can otherwise make a wheel dirty.

cmru ignores every uncommitted caller path, including a selected project's files: none can
enter the remote snapshot. It instead rejects only committed local `main` changes not yet on
`origin/main`, since those are easy to mistake for released source. In the transaction
worktree, cmru runs each changed project's required `run-tests` gate, then
fast-forwards `origin/main` from the validated branch before creating tags or publishing.
If another writer advanced remote main, the release fails before publication. A failure keeps
the branch/worktree for diagnosis; success removes both.

`steps.prepare` is for deterministic source preparation, such as resolving an upstream
version. It may change only paths declared in `release.commit_generated`; cmru commits those
mechanical outputs before the gate. Use `version.strategy = "external:VAR"` when prepare
writes a derived version into `<project>/cmru.vars`: cmru reads it and owns the annotated tag.
Never use a build or publish step to make an unreviewed source commit.
See [the release-transaction guide](docs/RELEASE-TRANSACTIONS.md) for recovery,
project-author requirements, and the current gate-adoption audit.

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
