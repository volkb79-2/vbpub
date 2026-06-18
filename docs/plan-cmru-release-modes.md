# Plan: cmru multi-capable release modes + fresh re-release

**Status:** PROPOSED 2026-06-18 — awaiting go-ahead. Decisions captured from the
user (Q1/Q2/Q3 below). Destructive steps (P0) need an explicit final confirm.

Worktree: optional. The cmru changes are surgical; do them on `main` directly unless
the user prefers a branch. If a branch is wanted:
`git worktree add -b feat/cmru-release-modes /tmp/vbpub-release-modes origin/main`.

---

## 1. Problem

cmru conflates **two independent axes** into one `version.strategy` field:

1. **Versioning** — how the version string is computed (`scm` / `counter` / `file` /
   `delegated`) and whether cmru owns the git tag.
2. **Publishing** — what a release *emits* (git tag? GitHub Release + assets? ghcr
   image push? `-latest` pointer? committed generated files?).

Because of this, the release flow ([cli.py:1067](../cmru/src/cmru/cli.py#L1067)) only
skips tagging for `strategy == "delegated"` and otherwise mints a `<prefix><semver>`
tag — so **modern-debian-tools-python-debug** (`artifact="oci"`, `strategy="scm"`) got
semver-tagged like a wheel (`modern-debian-tools-python-debug-v0.1.0`), which is wrong:
mdt's deliverable is the **OCI image on ghcr** + committed manifests, never a GitHub
Release. The `artifact` field exists ([cli.py:55](../cmru/src/cmru/cli.py#L55)) but
drives **no behavior**.

Also: **cmru has no README.md**.

## 2. Decisions (from user)

- **Q1 — Release model:** Named publish **profiles** (preset capability bundles),
  *but a project may emit several outputs* → the underlying model is a **capability
  set**; profiles are presets that **union** when a project lists more than one.
- **Q2 — OCI release:** push images + **commit manifests**, **no git tag, no GitHub
  Release**. Plus a first-class **commit/reproducibility model** (see §4).
- **Q3 — Tags:** **start completely fresh** — full reset of release tags/Releases, then
  a clean re-release with the new cmru.
- **Q4 — Separation of concerns:** *cmru is just the tool around it.* cmru owns the
  generic lifecycle + git/host mechanics; **the project owns the artifact-specific HOW**
  via step commands. `build`/`push`/`clean` are **symmetric project-defined steps**: a
  `build` generates the project's artifacts *and* its manifests; a `clean <version>`
  deletes the project's *own* referenced files for that version. cmru passes the
  version/tag and handles git (tag/commit/Release) + host (ghcr) generically — it never
  hardcodes a project's file paths.

## 3. Design — two axes

### 3.1 Axis A: Versioning (unchanged enum, clarified)
`scm` | `counter` | `file:PATH` | `delegated` | **`none`** (NEW: no version tag at all;
identity is the artifact's own tag, e.g. OCI BUILD_DATE). Determines the version string
and whether cmru mints/owns a git tag.

### 3.2 Axis B: Publish profile (NEW)
A project declares one or more **artifacts**; each maps to a preset **capability set**.
Capabilities:

| capability | meaning |
|---|---|
| `git_tag` | mint `<prefix><semver>` at HEAD (once per release, project-level) |
| `github_release` | create a GitHub Release for the tag |
| `github_assets` | upload artifact file(s) + `.sha256` to the Release |
| `registry_push` | push OCI image(s) to ghcr (dated + `:latest`) |
| `latest_pointer` | maintain `<project>-latest` `latest.json` thin pointer |
| `commit_generated` | `git add`+commit declared generated paths after build |

### 3.3 Named profiles (presets)

| profile | git_tag | github_release+assets | registry_push | latest_pointer | commit_generated |
|---|:--:|:--:|:--:|:--:|:--:|
| `wheel`     | ✓ | ✓ wheel+sha256 | — | ✓ | — |
| `oci-image` | — | — | ✓ ghcr | — | ✓ (manifests) |
| `bundle`    | ✓ | ✓ bundle+sha256 | — | ✓ | — |
| `tarball`   | ✓ | ✓ tarball+sha256 | — | ✓ | — |

**Multiple outputs union.** Example — **pwmcp** emits an OCI image *and* a stack bundle:
`artifacts = ["oci-image", "bundle"]` → caps = `registry_push` ∪ `git_tag` ∪
`github_release` ∪ `github_assets` ∪ `latest_pointer`. (pwmcp keeps `delegated`
versioning — its scripts own the `pwmcp-v<pw>-r<N>` tag.)

### 3.4 Config schema (cmru.toml)

```toml
[project.ciu]
artifacts = ["wheel"]                 # → wheel profile
[project.ciu.version]
strategy  = "scm"

[project.modern-debian-tools-python-debug]
artifacts = ["oci-image"]             # → oci-image profile: ghcr push + commit manifests
[project.modern-debian-tools-python-debug.version]
strategy  = "none"                    # no semver tag; version = BUILD_DATE
[project.modern-debian-tools-python-debug.release]
commit_generated = ["package-manifests-versioned"]   # paths to auto-commit post-build

[project.pwmcp]
artifacts = ["oci-image", "bundle"]   # emits both
[project.pwmcp.version]
strategy  = "delegated"
```

- **Back-compat:** singular `artifact = "wheel|oci|bundle|tarball"` still parses and maps
  to `artifacts = [<profile>]` (`oci`→`oci-image`). `[project.X.release]` overrides any
  preset capability per project.
- Profiles live in code as the single source of truth; `[project.X.release]` only
  overrides.

### 3.5 Lifecycle steps are project-owned; cmru orchestrates (Q4)

cmru defines a fixed lifecycle and supplies the **generic** mechanics; each project
supplies the **specific** commands. The symmetry is the point:

| lifecycle step | project supplies (`[project.X.steps.<step>]` argv) | cmru supplies (generic) |
|---|---|---|
| `build`  | build artifact(s) + (re)generate manifests | commit `commit_generated` paths |
| `push`   | publish artifact(s) to their home (ghcr, …) | mint `git_tag`, create GitHub Release + assets + `.sha256`, update `latest_pointer`, push commit/tag |
| `clean`  | *(optional)* delete this project's referenced files for `$CMRU_VERSION` | delete the GitHub Release + tag, prune ghcr versions (keep `<project>-latest`), commit the deletion |

- The project's step commands receive the resolved version/tag/build-date via env
  (`CMRU_VERSION`, `CMRU_TAG`, `CMRU_PREFIX`, `BUILD_DATE`) — so a project's `clean`
  script *finds its own files* (it knows its layout); cmru never hardcodes paths.
- **`steps.clean` is optional.** Wheel-type projects have no referenced files, so their
  cleanup is fully handled by cmru's generic part (delete Release + tag + ghcr) with
  zero project config. Only projects with referenced files (mdt manifests) define a
  `clean` step.
- **Symmetry of commits:** `build` *generates* files → cmru commits them;
  `clean` *deletes* files → cmru commits the deletion. cmru only ever commits the
  declared generated paths, never hand-edited source.

## 4. Reproducibility & commit model (answers the Q2 commit question)

Two rules, applied per profile:

1. **Pre-build source-clean gate (all profiles).** Before building, cmru verifies the
   project's **tracked source paths are clean** (no uncommitted changes), *excluding*
   any `commit_generated` paths. If dirty → **fail** with
   `commit or stash <paths> before releasing` (override: `--allow-dirty`, which yields a
   `.devN+dirty` wheel and is non-release). This is *why* "commit before cmru" — it
   guarantees the artifact corresponds to a committed state (and setuptools-scm emits a
   clean `X.Y.Z`). cmru already has a whole-tree version of this
   ([version.py:326](../cmru/src/cmru/version.py#L326)); we **scope it to the project**.
2. **cmru auto-commits generated outputs only — never source.** The user owns source
   commits (and their messages); cmru only commits mechanical build outputs declared in
   `commit_generated`.

**Wheel flow:** clean-gate → `git_tag` at HEAD → build (scm sees tag → `X.Y.Z`) →
push tag → Release + wheel + `.sha256` → update `latest.json`.

**OCI flow:** clean-gate (source, *excluding* `package-manifests-versioned/`) → build
(the resolver writes host manifests **pre-build**
[resolve-devcontainers-release.py:1433](../modern-debian-tools-python-debug/scripts/resolve-devcontainers-release.py#L1433),
bake embeds them, `--load`) → cmru commits the `commit_generated` paths
(`chore(mdt): release manifests <BUILD_DATE>`) → push commit → `registry_push`
(`bake --push`, dated + `:latest`). **No tag, no Release.** The committed manifests are
byte-identical to what the image embedded (resolver wrote them once; nothing mutates
them between embed and commit), so provenance links resolve. Edge cases in §7.

## 5. Phases

- **P0 — Fresh reset (DESTRUCTIVE; confirm scope first).** Delete release tags +
  GitHub Releases so we can re-release cleanly. **Scope to confirm:** (a) all `*-v*`
  tags + their Releases; (b) `*-latest` tags + Releases; (c) old split tags
  (`pwmcp-{client,server,shared}-v0.1.0`, mdt `-v0.1.0`); (d) **ghcr image versions** —
  prune too, or keep? Default proposal: delete all `*-v*` + `*-latest` + Releases;
  **keep** ghcr images (immutable, re-pushed on re-release). Local + origin.
- **P1 — cmru core.** Add the publish-profile/capability model + per-profile dispatch in
  the release flow; project-scoped clean-gate; `commit_generated`; `version none`.
  Unit tests for profile expansion + dispatch.
- **P2 — cmru.toml.** Migrate every project to `artifacts`/profiles + `[project.X.release]`.
- **P3 — mdt.** `oci-image` profile, `strategy="none"`, `commit_generated`. `status`
  shows BUILD_DATE as the "version".
- **P4 — cmru/README.md.** Product overview, install, the two-axis model + profile table,
  quickstart, link to SPEC.
- **P5 — (carry) manifest content restructure.** Ship cmru in the image (mirror the ciu
  wheel-install path) + list it; move the full sha256 digest list to an appendix at the
  doc end; add the missing **Python libraries** section; harmonize host manifest
  categories with the in-image `devcontainer-manifest-*.md`
  ([manifest_sections.py](../modern-debian-tools-python-debug/scripts/manifest_sections.py)).
- **P6 — (carry) generalized `cmru cleanup` spec + impl.** Per §3.5: cmru's generic
  cleanup prunes old GitHub Releases AND ghcr package versions AND deletes the git tag
  (keeping `<project>-latest`); the **referenced-manifest/`*.md` deletion is delegated to
  the project's optional `steps.clean`** (invoked with `$CMRU_VERSION`), and cmru commits
  the result. No hardcoded per-project paths in cmru. Edge cases: dry-run default,
  keep-latest, never delete the resolver pointer, age-vs-count retention, idempotent
  (missing target ≠ error), empty-clean ≠ empty commit.
- **P7 — Re-release all** from `main` with new cmru; verify tags / `-latest` / ghcr
  `:latest` match expectations.

## 6. mdt v0.1.0 tag

`modern-debian-tools-python-debug-v0.1.0` is a **tag only** (no Release; API 404).
Delete on origin + local as the first concrete act of P0:
`git push origin :refs/tags/<tag>` + `git tag -d <tag>`.

## 7. Edge cases / guards (prevent bugs)

- **Profile ∩ versioning conflicts:** `oci-image` requires `git_tag=false`; if a config
  pairs `oci-image` with `strategy=scm`, **error** at load (not silently tag).
- **Multi-output tag arbitration:** one tag per release even with several artifacts;
  `git_tag` is the union (true if *any* output needs it). Release notes list all assets.
- **OCI clean-gate must exclude `commit_generated`** or the gate trips on the freshly
  generated manifests. Already handled by change-detection excludes
  ([version.py](../cmru/src/cmru/version.py)) — extend to `commit_generated`.
- **`commit_generated` with nothing changed** → no empty commit (skip if `git diff
  --cached --quiet`).
- **Fresh-reset idempotency:** deleting a non-existent tag/Release must not fail the run.
- **latest_pointer never deleted by cleanup** (P6): keep-list always includes
  `<project>-latest`.
- **Re-pushing ghcr after reset:** `:latest` re-points; dated tags are immutable — same
  BUILD_DATE on the same day reuses the counter suffix (`-2`, `-3`).

## 8. Resolved (2026-06-18)

1. **ghcr scope:** FULL wipe — delete all `*-v*` + `*-latest` tags, all GitHub Releases,
   AND all ghcr image versions. Local + origin.
2. **Branch:** direct on `main` (greenfield/disposable philosophy).
3. **Sequencing:** **core first** — P1–P4 + P0 + P7 in this push; **defer P5 + P6** to a
   follow-up.
4. **Operational ordering (safety):** build & verify the new cmru (P1–P4) *before* the
   destructive wipe, then P0 wipe → P7 re-release. Never leave a "wiped but no tool"
   window. Delete the stray `mdt-v0.1.0` tag as part of P0.
