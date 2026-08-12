# cmru — Configurable Multi Release Utility

One release CLI for a **monorepo of independently-versioned products** that share a
**single** GitHub Releases page. cmru gives each product its own `<prefix><semver>` tag line and a monorepo-safe per-product "latest" (GitHub's repo-global *Latest* badge can only point at one release; cmru's resolver fixes that).

cmru is **just the orchestrator**: it owns the generic git/host mechanics (tags, commits, GitHub Releases, ghcr pruning, the `latest.json` pointer) and calls each project's own `build`/`push`/`clean` step commands for the artifact-specific work. No project logic is hardcoded in cmru.

## Install

```bash
pip install -e cmru          # provides the `cmru` console script
# or, from the repo root, with no install:
./cmru.py <verb>             # vbpub estate wrapper: supplies cmru.orchestration.toml
```

The installed `cmru` executable is portable: run it from a project directory
or pass `--config /path/to/cmru.toml`. Only this repository's `./cmru.py`
knows the explicit root orchestration path; it never makes the reusable CLI
search parent directories.

## The model: declared outputs and explicit behavior

Each project declares its released output vocabulary in `artifacts = [...]` (`wheel`,
`oci-image`, `tarball`, or `bundle`). That is a machine-readable inventory for release
history and retained artifacts—not a profile that injects build, Docker, or publishing
behavior. Every action is an explicit project step.

`[project.version].strategy` determines version discovery. `[project.release].git_tag`
separately states whether CMRU mints and pushes an annotated Git tag. `build_step` names
the step which makes retained outputs. `commit_generated` lists the only mechanical tracked
outputs CMRU may commit before its gate. This permits a GitHub wheel release, an image-only
registry publication, or a combined image+bundle release without hidden behavior.

## Verbs

```bash
cmru status                       # preview changed projects + next versions (read-only)
cmru release                      # isolated: prepare → gate → integrate → tag → build → publish
cmru release --dry-run            # show tags only, no writes
cmru release --project ciu        # one project
cmru changelog --project assay --backfill-tag assay-v0.1.0  # catalog a pre-history release
cmru standards                    # strict config + project-framework conformance
cmru standards --project pwmcp --update  # safely update CMRU-owned revision markers
cmru build   --project <name>     # isolated local build; retains logs/artifacts, then removes worktree
cmru worktrees                    # list retained failed build/release worktrees
cmru publish --project <name>     # low-level caller-worktree push step
cmru resolve --project <name>     # resolve the current "latest" (version/tag/url/sha256)
cmru cleanup --remove-assets 30d  # prune old Releases / ghcr versions
cmru cleanup --project ciu --delete-unmanaged-release-tag ciu-wheel-latest --dry-run
cmru cleanup --project ciu --delete-unmanaged-release-tag ciu-wheel-latest --yes
cmru cleanup --project ciu --delete-build-output <commit-date>_<commit> --dry-run
cmru cleanup --discard-build-worktree /path/reported/by/cmru --yes
cmru version                      # print the CMRU version
cmru --help                       # all verbs, with a TYPICAL WORKFLOW block
```

`release` detects changed projects, runs their explicit prepare/gate/promote/tag/build/push
contract in dependency order, and retains a failed transaction for diagnosis. A retained
transaction is the pre-publish debug/recovery path; see
[KI-06](KNOWN_ISSUES_TODO_BACKLOG.md#ki-06--durable-post-tag-publication-resume--open-scoped-deliberately).
`build` is local-consumption/diagnostic only; do not chain it to `publish` expecting its
retained artifact record to be published. The safe end-to-end verb is `release`; the deliberate
design question is tracked in [KI-10](KNOWN_ISSUES_TODO_BACKLOG.md#ki-10--cmru-build-artifacts-cannot-safely-feed-cmru-publish--open-decision-required).

`cleanup --delete-unmanaged-release-tag TAG` is deliberately narrow migration maintenance:
it requires a project scope and `--yes` (or `--dry-run`), accepts only that project's
namespace, deletes the exact GitHub Release, and leaves its Git tag untouched. It cannot
be mistaken for policy cleanup of normal immutable `<project>-v<semver>` releases.

## Logging and live diagnostics

Use the root wrapper directly—no `2>&1 | tee ...` is required:

```bash
./cmru.release.sh --project assay
```

It overwrites the root `cmru.release.log` with the complete release transcript.
The terminal stays readable: CMRU reports command labels, duration, known test-framework
success evidence, and concise failure excerpts. Detailed subprocess output is line-flushed to
the audit log and the transaction-local project files such as
`assay/logs/cmru/run-tests.log`. On a successful release those project logs disappear with
the worktree by default; `--retain-logs-on-release` moves them to
`assay/logs/cmru-release/<immutable-tag>/`. `--retain-artifacts-on-release` moves the
declared directories into `assay/artifacts/<immutable-tag>/` and writes a hash inventory in
`release.json` before the worktree is removed.

```bash
./cmru.release.sh --project modern-debian-tools-python-debug --show-run-details
./cmru.release.sh --project assay --log-append
```

`--show-run-details` also streams raw Docker/test output to the terminal. `--log-append`
preserves the prior root and per-step logs, adding an exact `---` divider before the new run.
CMRU sets `PYTHONUNBUFFERED=1` for Python child processes and flushes every received line;
non-Python tools must still flush their own output.

## Project framework and templates

Each project owns one complete `cmru.toml` contract: identity, versioning, release artifacts,
environment, and every runner step. That file is portable to a fresh repository root. A
monorepo's `cmru.orchestration.toml` contains only selection, order/dependencies, cleanup, and
an explicit `auth_project`; it cannot contain project commands. `template_revision = 2` lets
`cmru standards` identify stale adoption without inventing project behavior. Ready-to-copy
examples are [`templates/cmru.toml.tmpl`](templates/cmru.toml.tmpl) and
[`templates/cmru.orchestration.toml.tmpl`](templates/cmru.orchestration.toml.tmpl).

`cmru standards --update` changes only those CMRU-owned markers. It never rewrites a project’s
build/publish commands; a remaining warning is a real policy decision to review.
Runner controls live in the project’s `[steps.<name>]` table, require explicit `quiet`, and
require `quiet = true` for the normal summary-only transcript; use `--show-run-details` when
live subprocess output is required. They reject unknown keys. Put project-only data beneath `[project_metadata]` so a misspelled
execution setting fails before it can alter a release. `cmru.build.toml`, shell sourcing, and
configuration aliases are retired; there is no compatibility parser.

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

Use `[project.release] changelog = "docs/CHANGES.md"` only to choose another
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
the branch/worktree for diagnosis; success removes both (after optional evidence retention).

`cmru build` uses the same remote snapshot and transaction mechanics but stops before every
release action. On success it copies project logs to
`<project>/logs/<commit-date>_<full-commit>/` and declared artifact directories to
`<project>/artifacts/<commit-date>_<full-commit>/`, writes `build.json` with a SHA-256
inventory and a `publication: forbidden` marker, then removes the worktree. These records are
gitignored local consumption outputs, not release candidates and not inputs to `cmru publish`.
If the build or retention fails, CMRU keeps the exact `cmru/build/<id>` worktree and prints its
path. Run `cmru worktrees` to discover retained build/release worktrees, then use
`cmru cleanup --discard-build-worktree <path> --yes` only after inspection. An existing output
coordinate is never overwritten; remove it explicitly with
`cmru cleanup --project <name> --delete-build-output <id> --yes` before rebuilding that source.

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
| `<project>/cmru.toml` | yes | complete portable project contract — **no secrets** |
| `cmru.orchestration.toml` | yes | optional monorepo ordering/dependencies/cleanup only |
| `<project>/cmru.secret.toml` | no (gitignored) | `[github] token = "…"` overlay (optional; env wins) |
| `cmru.vars` | no (gitignored) | `KEY=VALUE` build vars a step emits for later steps |

**Token resolution (S2.4):** `$GITHUB_PUSH_PAT` → `$GITHUB_TOKEN` → the selected
project's gitignored `cmru.secret.toml [github].token`. A committed `cmru.toml`
token is rejected.
The monorepo selects this credential source explicitly with `orchestration.auth_project`; it is
never inferred from release order. Never commit a token.

**Why `cmru.vars` is gitignored (and not a missing "starting point"):** it is a *generated scratchpad* — a build step writes computed values (e.g. pwmcp's playwright-driven version) for a *later* step in the **same** run to read. The committed starting point is git tags + `VERSION` files + `cmru.toml`; `cmru status`/`release` read those and never read `cmru.vars`. A fresh clone regenerates it on the next build. Committing it would turn a derived cache into an authoritative-looking input that drifts from the tags — the opposite of reproducible.

## Reusable project-step commands

`python3 -m cmru.handlers` is a small command library, not an implicit profile system.
The templates show explicit `wheel-build` and `wheel-publish` calls, while projects such as
MDT and pwmcp retain their own image/bundle commands. This is useful for third-party
consumers: install a pinned CMRU wheel, copy the project template, and either compose the
library commands or use a project-owned tool. CMRU never guesses which choice is correct.

The OCI helper has an explicit normal Buildx bake load/push command. Its `--repack` argument
is intentionally fail-closed while production-equivalence evidence is absent; use a
project-owned, tested flow such as MDT's for real OCI repacking.

## Differentiators

1. **N products, one Releases page** via per-product `prefix` (`ciu-v…`, `pwmcp-v…`).
2. **Per-product "latest"** — `cmru resolve` returns the highest-semver release for a prefix; `<prefix>-latest` holds a thin `latest.json` pointer, not a duplicated asset.
3. **Explicit publication contracts** — wheels, OCI images, bundles and tarballs use one
   strict runner grammar, while each project owns its artifact-specific commands.
4. **Per-interpreter variants** (S-REL.6) — a `bundle`/`tarball` may declare `[[project.variants]]` so one tag publishes one asset per variant (`<tag>-<variant><suffix>`); the generated `get.py` installer selects one explicitly with `--variant NAME`. Zero declared variants keeps the single-asset path unchanged.

## cmru vs ciu

cmru is the **outer loop** (build-to-release: version + publish across products). Its sibling **ciu** is the **inner loop** (build-to-run: build local images and run a stack on this host). They overlap only in that both can trigger a docker build — over the *same* `docker-bake.hcl`, for different ends (ciu `--load`s + runs; cmru pushes). Full map, incl. the border question: [`../docs/ciu-vs-cmru.md`](../docs/ciu-vs-cmru.md).

## More

- Full contract & rationale: [`docs/SPEC.md`](docs/SPEC.md) — start at *S-CLI* and *S-REL*.
- Monorepo tooling overview: [`../docs/RELEASE-TOOLING.md`](../docs/RELEASE-TOOLING.md).
- Release-modes design/plan: [`../docs/plan-cmru-release-modes.md`](../docs/plan-cmru-release-modes.md).
