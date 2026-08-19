# cmru — Configurable Multi Release Utility

One release CLI for a **monorepo of independently-versioned products** that share a
**single** GitHub Releases page. cmru gives each product its own `<prefix><semver>` tag line and a monorepo-safe per-product "latest" (GitHub's repo-global *Latest* badge can only point at one release; cmru's resolver fixes that).

cmru is **just the orchestrator**: it owns the generic git/host mechanics (tags, commits, GitHub Releases, ghcr pruning, the `latest.json` pointer) and calls each project's own `build`/`push`/`clean` step commands for the artifact-specific work. No project logic is hardcoded in cmru.

## Install

```bash
pip install -e .             # provides the `cmru` console script
```

The installed `cmru` executable is portable: run it from a project directory
or repository root, or pass `--config /path/to/cmru.toml`. It reads
`cmru.toml` in the current directory when present and otherwise reads the
current directory's `cmru.orchestration.toml`; it never searches parent
directories. From the vbpub repository root, use `--config cmru.orchestration.toml`
when an explicit estate configuration is needed.

To build CMRU itself before any CMRU wheel is installed, use the supported
fresh-checkout bootstrap script. It imports handlers from `src`; the wheel bytes
are built in the dedicated `wheel-builder` image, so the host does not need the
`build` package:

```bash
cd /workspaces/vbpub/cmru
./build-initial-standalone.sh
```

The image is defined by [`wheel-builder/Dockerfile`](../wheel-builder/Dockerfile).
The script prints the manual virtual-environment install commands after it produces
the wheel; once installed, all subsequent builds use the `cmru` console script.

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
cmru dependencies                 # show + preflight the project dependency graph
cmru dependencies --write         # refresh its generated root-TOML comment block
cmru tool-deps                    # verify declared tool dependencies: integrity/authenticity/freshness
cmru tool-deps --allow-stale-tool-deps   # proceed despite a stale (behind-latest) pin
cmru tool-deps --refresh assay    # explicit, deliberate re-vendor + pin/hash update (never automatic)
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

Use the repository's one convenience wrapper directly—no `2>&1 | tee ...` is required:

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
./cmru.release.sh --project assay --log-prefix-time-short
```

`--show-run-details` also streams raw Docker/test output to the terminal. `--log-append`
preserves the prior root and per-step logs, adding an exact `---` divider before the new run.
CMRU sets `PYTHONUNBUFFERED=1` for Python child processes and flushes every received line;
non-Python tools must still flush their own output. `--log-prefix-time-short` adds
`HH:MM:SS` before CMRU's existing severity prefix. INFO/WARN/ERROR are colour-coded only on
an interactive terminal; `cmru.release.log` and pipes remain plain ANSI-free text.

## Project framework and templates

Each project owns one complete `cmru.toml` contract: identity, versioning, release artifacts,
environment, and every runner step. That file is portable to a fresh repository root. A
monorepo's `cmru.orchestration.toml` contains only selection, order/dependencies, cleanup, and
no project commands. Repository credentials are defined separately at the repository root.
`template_revision = 4` lets
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

The stock `tester-gate` command additionally requires an explicit tester image, memory,
combined memory/swap, CPU ceiling, and host-systemd probe image in `[env]`. A gate that
uses `--enable-docker` must also declare its nested-Docker image. The stock `wheel-build`
handler requires an explicit wheel-builder image. These are release inputs, not CMRU
defaults; pin immutable digests in a production contract.

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
and creates a temporary `cmru/release/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>` worktree at that
exact commit — chronologically sortable, its `.worktrees/` directory name the branch's own
`/`→`-` transform. A local `main` behind the remote is warned about but safe because the
remote is authoritative. This matters because setuptools-scm sees the
whole Git worktree: a harmless edit in another project can otherwise make a wheel dirty.

cmru ignores every uncommitted caller path, including a selected project's files: none can
enter the remote snapshot. It instead rejects only committed local `main` changes not yet on
`origin/main`, since those are easy to mistake for released source. Before touching any
project, cmru also checks the release plan itself against `origin` in two ways a purely local
read cannot: a project's latest tag must actually be published there under the SAME name AND
pointing at the SAME commit (never a local-only or same-named-but-different-object hand-made
tag), and `origin` must not carry a newer matching tag this local clone never fetched — either
always aborts with a named remedy. If a verified tag's commit is exactly the snapshot commit,
that's the ordinary state right after a completed release: cmru reports it and moves on, never
an error. Only a tag strictly *ahead* of the snapshot — pushed, but not yet in this snapshot's
history, almost always a half-completed prior release — aborts with a named remedy
(`--allow-tag-ahead-of-head` downgrades only that one deliberately; `--allow-tag-at-head` is a
deprecated alias). Any such plan-time refusal is a clean, typed failure that discards the
just-created worktree — never retains it, since no project's cycle ever started. In the
transaction worktree, cmru runs each changed project's required `run-tests` gate, then
fast-forwards `origin/main` from the validated branch before creating tags or publishing.
If another writer advanced remote main, the release fails before publication. A failure keeps
the branch/worktree for diagnosis; success removes both (after optional evidence retention).

`cmru build` uses the same remote snapshot and transaction mechanics but stops before every
release action. On success it copies project logs to
`<project>/logs/<commit-date>_<full-commit>/` and declared artifact directories to
`<project>/artifacts/<commit-date>_<full-commit>/`, writes `build.json` with a SHA-256
inventory and a `publication: forbidden` marker, then removes the worktree. These records are
gitignored local consumption outputs, not release candidates and not inputs to `cmru publish`.
If the build or retention fails, CMRU keeps the exact
`cmru/build/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>` worktree and prints its
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

## Tool dependencies

A project's OWN tests/tooling may consume a first-party artifact released by ANOTHER
project in the same estate — cmru's own `run-tests` step runs a pinned
`tools/assay/assay-1.0.0.pyz` zipapp, for example. `assay` independently
`depends_on = ["cmru"]` for release ORDER, so declaring the reverse edge there would be a
cycle; that is exactly why the relationship is resolved by vendoring a pinned artifact
instead, and exactly why nothing previously expressed it — cmru could silently test
against a version of assay far behind what assay itself ships, with no signal to anyone.

`[[project.tool_dependencies]]` in `cmru.toml` makes that edge explicit:

```toml
[[project.tool_dependencies]]
project = "assay"                          # a first-party project in this estate
version = "1.0.0"                          # the pinned version
path    = "tools/assay/assay-1.0.0.pyz"    # project-relative path to the vendored artifact
sha256  = "6224f784f96f5ad9d10264a69dd69594639959c5eda847dcede822a7adc515bf"
```

`cmru dependencies` reports it as a third edge kind (`tool`, alongside `declared` and
`artifact`) but — deliberately, and permanently — never validates it against
`project_order`: routing it through that same check would make cmru→assay a cycle
against assay→cmru and refuse to load a config that is not actually broken.

`cmru tool-deps` runs three DISTINCT checks per declared dependency, never conflated in
either code or their messages:

* **Integrity** — do the vendored bytes match the recorded `sha256`? Local only, no
  network, always resolvable.
* **Authenticity** — does that hash equal the digest of the PUBLISHED release asset, for
  that project and exact pinned version? A file named `assay-1.0.0.pyz` is not thereby
  assay 1.0.0 — the published bytes are downloaded and hashed; the filename only picks
  which asset to fetch, never evidence of authenticity by itself.
* **Freshness** — is the pin the HIGHEST released version for that project? The staleness
  check, independent of authenticity: a pin can be authentic and simultaneously stale.

A stale or mismatched tool dependency is an **error by default** — both for `cmru
tool-deps` and inside `cmru release`'s own preflight (same phase as the tag-verification
preflight, before any project's cycle starts; scoped to only the projects this run
actually releases; runs identically for `--dry-run`). `--allow-stale-tool-deps` overrides
staleness only — there is no override for an integrity or authenticity failure. A fresh
clone with nothing released yet, or an unreachable network, is reported as a THIRD,
explicit `unresolved` outcome — never as a pass, and never as a failure. `cmru tool-deps
--refresh assay` re-vendors from the latest published release and rewrites the pin + hash
deliberately; nothing here ever refreshes automatically.

**This verification never runs during `pytest`/`cmru tester-gate`.** That is not a
performance shortcut — it is the entire reason a pinned artifact is vendored instead of
fetched: the test suite stays hermetic, reproducible, and bootstrappable from a bare
clone with no network at all. See [SPEC.md S15](docs/SPEC.md#s15--tool-dependencies-declaration--verification)
for the full contract.

## Config & secrets

| file | committed? | purpose |
|---|---|---|
| `<project>/cmru.toml` | yes | complete portable project contract — **no secrets** |
| `cmru.orchestration.toml` | yes | optional monorepo ordering/dependencies/cleanup only |
| `cmru.secret.toml` | no (gitignored) | repository credential document: `[github] token = "…"` |
| `<project>/cmru.secret.toml` | no (gitignored) | optional same-shaped project override, deep-merged over the root secret |
| `cmru.vars` | no (gitignored) | `KEY=VALUE` build vars a step emits for later steps |

**Token resolution (S2.4):** `$GITHUB_PUSH_PAT` → `$GITHUB_TOKEN` → deep merge the
repository-root `cmru.secret.toml` with the selected project's optional
`cmru.secret.toml` (the project `[github].token` wins). A committed `cmru.toml` token
is rejected. Never commit a token.

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
