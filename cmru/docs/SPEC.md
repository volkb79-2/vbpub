# cmru SPEC

CIU conventions apply: section numbers are stable identifiers (S-numbers).
Breaking changes to a section bump the wheel MAJOR and include the S-ID in the changelog.
RFC 2119 key words (MUST, SHOULD, MAY, etc.) are normative.

---

## S-CLI — CLI at a glance (the intuitive contract)

cmru is one CLI over a monorepo of independently-versioned **projects**. Everything a user
touches is named `cmru.*` so the association is unambiguous.

### Verbs, in the order you use them

```
cmru status                 # 1. preview: what changed + the next version (read-only)
cmru release                # 2. the one-shot: detect → tag → push tag → build → publish
   ├─ cmru build            #    (same two steps, split out: artifact only)
   └─ cmru publish          #    (upload artifact + .sha256 to the release)
cmru cleanup --remove-assets 30d   # 3. prune old releases/images (optional)

cmru resolve --project P    # consumer: highest-semver published version  (read-only)
cmru get     --project P    # consumer: emit a standalone installer       (read-only)
cmru run     [--build --push ...]  # escape hatch: run explicit steps × projects
cmru run-step --config C --step S  # raw single-step runner (rarely needed)
```

**S-CLI.1** `release` is the normal path and MUST be idempotent: re-running it on a HEAD
that already carries a project's tag re-uses that tag (finishes a half-done release) rather
than minting a new one.

**S-CLI.2** `status` and `release` MUST operate only on the orchestrated set
(`orchestration.project_order`); a project is released only once it is listed there.

**S-CLI.3** Verbs that write to the host (`release`, `build`, `publish`, `run`) MUST be
clearly distinguished in `--help` from read-only verbs (`status`, `resolve`, `get`).

### File conventions (all `cmru.`-prefixed)

| File | Tracked? | Purpose |
|---|---|---|
| `cmru.toml` | committed | The one config (S2 schema): github, targets, orchestration, projects. **No secrets.** |
| `cmru.secret.toml` | gitignored | Token only: `[github] token = "…"`. Optional — env wins (see S2.4). |
| `cmru.sample.toml` | committed | Template for `cmru.toml` (no secrets). |
| `cmru.vars` | gitignored | Generated `KEY=VALUE` build vars a step emits for later steps (was `.release-vars`). |
| `<project>/cmru.build.toml` | committed | Per-project step config consumed by that project's build script (was `build-push.toml`). |
| `cmru.py` | committed | Repo-root entry point (`./cmru.py <verb>` ≡ `cmru <verb>`); `cmru.*.sh` shims wrap each verb. |

**S-CLI.4** The names `release.toml`, `release.sample.toml`, `.release-vars`,
`build-push.toml`, `release-all.py`, `release-runner.py` are **retired and removed** — no
legacy remains. The only release entry points are `cmru.py` (and the `cmru.*.sh` shims) or
the installed `cmru` console script.

---

## S0 — Terminology

| Term | Definition |
|---|---|
| **project** | A named unit of releasable work within a monorepo (e.g., `ciu`, `tls-edge`, `pwmcp`). |
| **artifact** | The published output of a build step: `wheel`, `oci`, `tarball`, or `bundle`. |
| **prefix** | The per-project tag prefix, e.g., `tls-edge-v`. Uniquely identifies a project on the Releases page. |
| **tag** | An immutable git tag of the form `<prefix><semver>`, e.g., `tls-edge-v0.2.0`. |
| **release** | A GitHub Releases entry whose `tag_name` equals a `<prefix><semver>` tag. |
| **sidecar** | A `.sha256` file uploaded alongside an artifact containing its `sha256sum -c`-compatible checksum. |
| **latest.json** | A thin pointer file (`<prefix>latest/latest.json`) recording the highest-semver tag, no asset duplication. |
| **runner** | The cmru component that executes a single build step in a reproducible, logged environment. |
| **host** | A release storage provider implementing the `ReleaseHost` interface (S11). |
| **resolver** | The cmru component that returns `{version, tag, asset, sha256, url}` for the highest-semver release. |
| **get.py** | A per-project emitted Python 3 bootstrap installer implementing the S6 contract (ships inside the artifact). |
| **delegated step** | A commodity operation (sign, SBOM, changelog, package) delegated to an external OSS tool (S7). |

---

## S1 — Project & Artifact Model

cmru manages N independent projects, each with its own semver line, all sharing **one** GitHub Releases page per repository.

**S1.1** Each project has a `prefix` that MUST be unique within the repository. Tags take the form `<prefix><semver>` (e.g., `tls-edge-v0.2.0`). Tags are immutable once pushed; updating a tag is a violation of this SPEC.

**S1.2** Supported artifact types:

| Type | Description | Source |
|---|---|---|
| `wheel` | Python distribution wheel (`.whl`) | `python -m build` |
| `oci` | Container image | Docker buildx bake (or built-in handler, see S14) |
| `tarball` | Archive (`.tar.xz`, `.tar.gz`) | `tar` + custom build |
| `bundle` | Deterministic release bundle (`.tar.xz`) + `manifest.json` + `manifest.json.minisig` | project allowlist + cmru bundler |

**S1.3** Each **GitHub-Release** profile (`wheel`/`bundle`/`tarball`) MUST upload:
- The artifact file itself (immutable, content-addressed by version+hash in release notes).
- A `.sha256` sidecar containing one line in `sha256sum -c` format.
(The `oci-image` profile creates no GitHub Release — see S-REL.)

**S1.6** The `bundle` artifact is a **triple**: a deterministic `<name>.tar.xz` archive
(byte-identical across builds from the same commit and `SOURCE_DATE_EPOCH`), a canonical
`manifest.json` (Seam 3 schema; see S9.5), and a detached Ed25519 signature
`manifest.json.minisig` (see S7). The manifest is the root of authenticity for remote
deployment: it pins every content-addressed asset (wheel sha256, image digest) so the
installer (SPEC A) can verify the entire release transitively from a single trusted
signature check.

A `bundle` (or `tarball`) MAY additionally declare **per-interpreter variants** (S-REL.6):
one release tag then carries N distinct triples, one per variant (e.g. a `py39` and a
`py311` bundle, each with version-locked C-extension wheels). Each variant's assets are
named `<tag>-<variant>.tar.xz` (+ its `.sha256`, and for `bundle` its `manifest.json` +
`manifest.json.minisig`). With **no** declared variants the artifact is a single
`<tag>.tar.xz` triple exactly as before.

**S1.4** OCI images are published to a registry (ghcr) with a dated immutable tag plus a
floating `:latest`; their manifest digest is the content address. They are **not**
git-tagged and create **no** GitHub Release (S-REL).

**S1.5** N projects, one Releases page is the first differentiator. The `prefix` mechanism is the key: the resolver (S5) and get.py (S6) filter by prefix, so projects never interfere with each other.

---

## S-REL — Release model (two axes)

A `cmru release` is governed by **two independent axes**, so the same versioning can drive
very different publishing:

**S-REL.1 — Versioning** (`[project.X.version].strategy`): `scm` | `counter` | `file:PATH`
| `delegated` | `none`. Determines the version string and whether cmru owns a git tag.
`none` = no version/tag at all (identity is the artifact's own tag, e.g. an OCI image
tag / BUILD_DATE); `delegated` = the project's own scripts mint the tag.

**S-REL.2 — Publish profile** (`[project.X].artifacts`): a list of artifact profiles. Each
profile expands to a capability set; a project may list **several** (their capabilities
union). Presets:

| profile | git tag | GitHub Release + assets | registry push | latest.json | commit generated |
|---|:--:|:--:|:--:|:--:|:--:|
| `wheel` | ✓ | ✓ | — | ✓ | — |
| `bundle` | ✓ | ✓ | — | ✓ | — |
| `tarball` | ✓ | ✓ | — | ✓ | — |
| `oci-image` | — | — | ✓ ghcr | — | ✓ |

`oci` is an alias for `oci-image`. Example — pwmcp emits both:
`artifacts = ["oci-image", "bundle"]`.

For `oci-image`, cmru provides a built-in handler (S14) that manages `build` and `push`
steps — projects MAY omit `[steps.*]` and rely on the handler instead of delegated scripts.

**S-REL.3 — cmru is the orchestrator; the project owns the *how*.** cmru only performs the
**generic** git/host side-effects it can do for any project — mint+push `<prefix><semver>`,
commit declared generated paths, push the commit. The artifact-specific work (build the
wheel/image/bundle, create the GitHub Release + upload assets, push to ghcr, write
`latest.json`) is performed by the **project's own `build`/`push` step commands** (or, for
`oci-image`, by cmru's built-in handler — see S14). cmru never hardcodes a project's file
paths.

**S-REL.4 — delegated publication is source-first and fail-closed.** After the delegated
build step, cmru commits any tracked changes confined to the project subtree and performs
`git push origin HEAD` before the publish step, even when the build made no new diff. A
non-fast-forward or any other source-push failure MUST abort publication so the remote host
cannot create an immutable release tag from a tree different from the one that was built.

**S-REL.4 — Overrides & guards** (`[project.X.release]`): `git_tag = false/true` overrides
the profile's tag capability; `commit_generated = ["<project-relative path>", …]` lists
build outputs cmru must `git add`+commit after `build` (e.g. mdt's
`package-manifests-versioned`). An `oci-image`-only project paired with a tagging strategy
(`scm`/`counter`/`file`) is a config error (exit 2) — OCI images are not git-tagged.

**S-REL.5 — Reproducibility / commit model.** Before building, cmru requires the project's
tracked source to be clean (commit first → the artifact maps to a committed state; wheels
get a clean `X.Y.Z` from setuptools-scm). cmru auto-commits **only** the declared
`commit_generated` outputs (mechanical), never hand-edited source. OCI flow: clean-gate →
build (resolver regenerates manifests pre-build, bake embeds them) → commit
`commit_generated` → push commit → push images. Wheel flow: clean-gate → tag at HEAD →
build → push tag → (project step) Release + asset + `latest.json`.

**S-REL.6 — Multi-variant releases (per-interpreter artifact matrix).** A `bundle` or
`tarball` project MAY declare N named **variants** so that ONE release tag publishes one
artifact per variant. This exists for artifacts that cannot be interpreter-agnostic — e.g.
a bundle that carries version-locked C-extension wheels, where a single archive cannot serve
both a py39 and a py311 host.

- **Declaration** (`[[project.<name>.variants]]`, S2): each entry has a required, filename-safe
  `name` (V22), and optional `build_arg` (a build-time knob the project's build step consumes)
  and `label` (a human description surfaced by the installer). **Zero declared variants ⇒ the
  exact single-asset behaviour of prior versions** (no naming, latest.json, or installer change).
- **Asset naming** is deterministic: `<prefix>-v<version>-<name><suffix>` (e.g.
  `naf-v1.0.0-py39.tar.xz`), each with its `.sha256` sidecar, and for `bundle` a per-variant
  `manifest.json` + `manifest.json.minisig`, all uploaded under the single `<prefix>-v<version>`
  release. cmru MUST NOT publish two variants under two different tags.
- **latest.json** records the full variant list and every hash (see S5.3), so a consumer can
  enumerate and verify variants without listing the release's assets.
- **Resolution** is by `(tag, variant)`: `find_artifact(..., variant=<name>, suffix=<suffix>)`
  narrows a multi-variant `dist/` to exactly one file, so a build that produced several
  `<prefix>-v*` artifacts no longer trips the ">1 match" guard; genuine duplicates within a
  single variant still error.
- **Selection is explicit at install time** (S6.12): the target host has no interpreter to
  auto-detect, so the operator names the variant. cmru MUST NOT pick a silent default.

The single-asset publish keystone (`publish_versioned`) is unchanged; the variant matrix is a
separate keystone (`publish_versioned_variants`) so the legacy path is provably untouched.

---

## S2 — Config Schema

cmru reads `cmru.toml` at the repo root (override with `--config` or `RELEASE_MANAGER_CONFIG`).
Secrets are **never** in `cmru.toml`: the token is resolved per S2.4. Per-project build
details that a project's own build script needs live in `<project>/cmru.build.toml`.

**S2.1** The config MUST be validated on startup. An invalid config MUST cause an exit 2 (S8).

**S2.2** Top-level tables:

```toml
[github]
owner      = "volkb79-2"          # required
repo       = "vbpub"              # required
owner_type = "user"               # required: "user" | "org"
token      = "..."                # required for publish; read from env if omitted

[orchestration]
log_dir = "logs"                  # optional

[targets]
host     = "github"               # required: provider for releases
registry = ["ghcr.io"]            # list: image registries to push to (S11)

[cleanup]
max_age_days = 90                 # optional; applies to draft/pre-release assets

[project.<name>]
prefix      = "<name>-v"          # required: tag prefix
artifact    = "wheel"             # required: wheel | oci | tarball | bundle
scm_dist    = "<name>"            # optional: python dist name (for wheel type)
cwd         = "<name>/"           # required: build working directory

[project.<name>.version]
strategy = "scm"                  # required: scm | file:PATH | counter
paths    = ["<name>/"]            # paths to watch for changes (change detection)
bump     = "conventional"         # conventional | patch

[project.<name>.steps.<step>]
# See S3 for the runner contract fields.

[project.<name>.publish]
source      = "dist/*.whl"        # glob for artifact file(s)
latest_json = true                # emit latest.json pointer

[project.<name>.resolve]
asset_glob = "*.whl"              # glob to match asset in release

[project.<name>.installer]         # inputs for the emitted get.py installer (S6)
install_dir_system = "/opt/<name>"        # system-scope root
install_dir_user   = "<name>"             # leaf under $XDG_DATA_HOME/<name>
asset_suffix       = ".tar.xz"            # release asset filename suffix
entrypoint         = "scripts/adapter.py" # project adapter, relative to release root (optional)
required_commands  = ["python3", "docker", "minisign"]   # checked pre-network (exit 3)
preserve           = ["shared/host.toml"] # paths kept in <root>/shared/ across updates
manifest_name      = "manifest.json"      # manifest file inside the bundle
signature_name     = "manifest.json.minisig"  # minisign signature for manifest

[[project.<name>.installer.wheels]]  # bundled wheels to install into private venv
path         = "vendor/cmru-*.whl"  # glob inside the release bundle
distribution = "cmru"               # pip distribution name

[[project.<name>.installer.wheels]]
path         = "vendor/ciu-*.whl"
distribution = "ciu"

# Optional per-interpreter variants (S-REL.6). Zero entries ⇒ single-asset behaviour.
# Each variant publishes one asset (<prefix>-v<version>-<name><suffix>) under the same tag;
# the operator selects one at install time via get.py --variant <name>.
[[project.<name>.variants]]
name      = "py39"                 # required: filename-safe token (V22); used in the asset name
build_arg = "PYTHON_VERSION=3.9"   # optional: build knob the project's build step consumes
label     = "Python 3.9 (glibc)"   # optional: human description shown by the installer prompt

[[project.<name>.variants]]
name      = "py311"
build_arg = "PYTHON_VERSION=3.11"
label     = "Python 3.11 (glibc)"

# NOTE: [project.<name>.getsh] is REMOVED (V09 rejects it — migrate to [installer]).

[project.<name>.delegated]
sign      = false                 # cosign sign
sbom      = false                 # syft + grype
changelog = false                 # git-cliff
nfpm      = false                 # nfpm deb/rpm

# OCI image handler config (see S14):
#[project.<name>.oci]
#repack              = false
#repack_target_size  = "2GB"
#repack_compression  = 9
```

**S2.3** Unknown keys MUST be rejected (fail-fast). All required fields MUST be present or the config is invalid.

**S2.4** Token resolution order (first hit wins), so `cmru.toml` stays secret-free:
1. `GITHUB_PUSH_PAT` env var, then `GITHUB_TOKEN` env var.
2. `cmru.secret.toml` → `[github].token` (a gitignored overlay next to `cmru.toml`).
3. `[github].token` in `cmru.toml` itself — DISCOURAGED; allowed only for throwaway repos.

If none is found and a write verb is invoked, cmru MUST exit 3 (V10).

---

## S3 — Single Runner Contract

Every build step MUST be executed through the cmru runner. The orchestrator MUST NOT invoke build commands directly.

**S3.1** Required runner capabilities:

| Capability | Description |
|---|---|
| `login` | Pre-step registry/host authentication |
| `required_env` | Fail if listed env vars are absent (exit 3, S8) |
| `clean_dirs` | Wipe output directories before build |
| `env_command` | Shell command to source additional env vars |
| `bake --set` | Inject build args into Docker buildx bake |
| `no_cache` | Force cache invalidation for reproducible builds |
| `per-step logs` | Each step writes to its own log file |
| `reproducible-env` | Set `SOURCE_DATE_EPOCH` from HEAD commit timestamp |

**S3.2** Step config fields (under `[project.<name>.steps.<step>]`):

```toml
command       = ["docker", "buildx", "bake"]  # or string for shell
login         = ["ghcr.io"]
required_env  = ["GITHUB_TOKEN"]
clean_dirs    = ["dist/"]
env_command   = "source .env"
no_cache      = false
bake_targets  = ["image"]
bake_set      = ["*.tags=ghcr.io/foo/bar:latest"]
```

**S3.3** The runner MUST set `SOURCE_DATE_EPOCH` to the Unix timestamp of the HEAD commit before every step.

**S3.4** Step logs MUST be written to `<log_dir>/<project>/<step>.log`.

---

## S4 — Publish

**S4.1** `cmru publish` uploads the artifact and sidecar to the release host (S11).

**S4.2** Before uploading, cmru MUST compute `sha256sum` of the artifact and write a `.sha256` sidecar in `sha256sum -c` compatible format (one line: `<hash>  <filename>`).

**S4.3** The release notes body MUST include the artifact's SHA-256 digest and, for OCI artifacts, the manifest digest.

**S4.4** If `latest_json = true`, cmru MUST create or update `<prefix>latest/latest.json` (see S5.3) as a separate release (or asset on a `<prefix>latest` tag).

**S4.5** Dev builds (untagged commits, version contains `.dev`) MUST NOT mint a `<prefix>-v` release. They MAY upload to a `<prefix>-dev` pre-release slot.

**S4.6** `target_commitish` in the GitHub release MUST be set to the commit SHA at build time.

**S4.7** OCI publishes to GHCR SHOULD reconcile package visibility with the source repository visibility after push, on a **best-effort** basis. The repository visibility is authoritative (a public repo should yield public GHCR packages). **Platform limitation (verified 2026-06-21):** GitHub exposes **no REST or GraphQL API** to change a container package's visibility — the `PATCH …/packages/container/<name>` route returns `404`, classic PATs have **no `admin:packages` scope** (only `read:`/`write:`/`delete:packages`), and **fine-grained PATs cannot use the Packages API at all** (github/roadmap#558). Therefore the reconciler MUST treat a failed visibility change as a **non-fatal warning** — it MUST NOT fail a release whose image already pushed — and MUST emit the one-time manual remediation: *Your packages → `<pkg>` → Package settings → Danger Zone → Change visibility*. Visibility set once in the UI **persists across all future pushes**, so this is a one-time action per package, not per release.

---

## S5 — Resolver

The resolver implements differentiator #2: highest-semver selection, replacing GitHub's single repo-global "Latest" badge.

**S5.1** `cmru resolve --project <name>` returns `{version, tag, asset, sha256, url}` for the highest-semver release matching `prefix`.

**S5.2** Semver comparison MUST be numeric-aware per segment: `r10 > r2 > r1` (not lexicographic).

**S5.3** If `latest.json` exists for the project, the resolver SHOULD use it as the primary source (one API call vs. paginated scan). Format:

```json
{
  "version": "0.2.0",
  "tag": "tls-edge-v0.2.0",
  "asset": "tls-edge-v0.2.0.tar.xz",
  "sha256": "<hex>",
  "url": "https://github.com/…/releases/download/tls-edge-v0.2.0/tls-edge-v0.2.0.tar.xz"
}
```

For a **multi-variant** release (S-REL.6) the pointer instead records a `variants` array —
one entry per interpreter variant, each with its own `asset`, `sha256`, `url`, and optional
`label` — and carries no single top-level `asset`/`sha256` (there is no single artifact):

```json
{
  "project": "naf",
  "version": "1.0.0",
  "tag": "naf-v1.0.0",
  "variants": [
    {"name": "py39",  "asset": "naf-v1.0.0-py39.tar.xz",  "sha256": "<hex>", "url": "https://…/naf-v1.0.0-py39.tar.xz",  "label": "Python 3.9"},
    {"name": "py311", "asset": "naf-v1.0.0-py311.tar.xz", "sha256": "<hex>", "url": "https://…/naf-v1.0.0-py311.tar.xz", "label": "Python 3.11"}
  ]
}
```

**S5.4** Fallback if latest.json is absent or stale: scan releases via host API, filter by prefix, select max semver.

**S5.5** `--format` flag: `json` (default), `env` (shell-sourceable `KEY=value` lines), `url` (bare download URL).

---

## S6 — get.py Contract (Transactional Installer)

The emitted `get.py` is a per-project **transactional** bootstrap that handles install,
update, rollback, and status. Unlike a curl-only bootstrap, `get.py` ships **inside** the
release artifact, so `<project> update` works out of the box. Configuration lives in
`[project.<name>.installer]` (see S2).

**S6.1** `cmru get-py --project <name> --config cmru.toml` emits a standalone Python 3
installer to stdout. The output is a rendering of `templates/get.py.tmpl` with
`[[VARNAME]]` placeholders replaced from the `[installer]` config. The rendering is
deterministic (byte-identical for identical config). Any unreplaced `[[...]]` placeholder
triggers a warning.

**S6.2** Commands emitted:

```
get.py install  --config HOST.toml [--version TAG] [--scope system|user]
get.py update   [--version TAG] [--scope system|user]
get.py status   [--scope system|user]
get.py rollback [--version TAG] [--scope system|user]
```

**S6.3** Transactional pipeline (install / update):

1. **Pre-flight** — check `required_commands` BEFORE any network I/O (exit 3 if missing).
2. **Resolve** — resolve the highest-semver `TAG_PREFIX*` release via the GitHub Releases API,
   or use `--version`. Public requests carry **no** Authorization header. Private assets are
   resolved by API asset-ID with the Authorization header stripped before the CDN redirect.
3. **Download** — fetch `<tag><asset_suffix>` + its `.sha256` sidecar. For a multi-variant
   release the selected variant (S6.12) changes the asset name to
   `<tag>-<variant><asset_suffix>` (+ matching `.sha256`).
4. **Verify SHA256** — recompute and compare; mismatch → exit 1, before extraction.
5. **Verify minisign** — if `--manifest-pubkey` is supplied (or pubkey in host config),
   extract `manifest_name` + `signature_name` from the bundle and run
   `minisign -Vm manifest.json -P <pubkey>` (or `-p <pubkey-file>`). Failure → exit 1.
6. **Stage** — extract into `<root>/releases/<tag>.staging/` with `filter="data"` (py≥3.12)
   plus a pre-scan that rejects: absolute paths, `..` traversal, device nodes, absolute
   symlinks, and symlink/hardlink traversal escapes.
7. **Install wheels** — if `installer.wheels` is non-empty, create `<root>/venv` via
   `python3 -m venv` and `venv/bin/pip install --no-index <wheel>` for each glob match.
   Wheel sha256s from the manifest are verified before pip install (exit 1 on mismatch).
8. **Invoke adapter** (`bootstrap` on install, `apply` on update) — if `entrypoint` is set.
   Non-zero exit aborts before the `current` swap (previous release stays live).
9. **Atomic swap** — `os.symlink` to a temp name + `os.replace` onto `current`.
10. **Finalize** — rename `.staging` → final release dir; prune old releases (keep 2 by default).

**S6.4** Release layout:

```
<root>/releases/<tag>/    # immutable dir per installed version
<root>/current            # symlink → releases/<current-tag>  (atomic swap)
<root>/shared/            # preserved config/state (never inside releases/)
<root>/venv/              # private interpreter; bundled wheels live here
```

`<root>` = `install_dir_system` (system scope) or `$XDG_DATA_HOME/<install_dir_user>` /
`~/.local/share/<install_dir_user>` (user scope).

**S6.5** Preserve: files in `installer.preserve` are copied to `<root>/shared/` before
staging and symlinked back into the new release dir after extraction. They survive across
updates and rollbacks.

**S6.6** Rollback: `get.py rollback [--version TAG]` re-points `current` to the previous
(or named) release dir and re-runs the adapter with `action=rollback`.

**S6.7** Scope-exclusive lock (`flock` on `<root>/.lock`) serialises concurrent invocations.
SIGINT/SIGTERM handler cleans up staging dir and releases the lock.

**S6.8** Adapter invocation contract (Seam 1):

```
<root>/venv/bin/python <root>/current/<entrypoint> <action> \
    --release-root <root>/releases/<tag> \
    --config <root>/shared/host.toml \
    --manifest <root>/releases/<tag>/manifest.json
```

`<action>` ∈ `{bootstrap, apply, health, rollback}`. Non-zero adapter exit → exit 1.
The GitHub token is **stripped** from the child-process environment.

**S6.9** The installer is Python 3 **stdlib-only** (urllib/tarfile/hashlib/argparse/fcntl);
no third-party dependencies. `minisign`, `docker`, and the project adapter are shelled out.

**S6.10** Auth (token) precedence: `--github-token` (warns: leaks via ps/history) >
`--github-token-file FILE` (rejected if loose perms / wrong owner) > `--github-token-stdin`
> `CMRU_GITHUB_TOKEN` / `GITHUB_TOKEN` env. Token is never logged in full.

**S6.11** `install_dir_user` degrades gracefully: if `entrypoint` is empty and `wheels`
is empty, no adapter is called and no venv is created (tls-edge minimal path).

**S6.6** `--version <TAG>` pins the install to a specific tag (bare semver or full tag). Arguments go to the right side of the pipe (`curl … | sudo python3 - install --version …`), so there is no env-var-across-pipe footgun.

**S6.12** **Variant selection (multi-variant releases, S-REL.6).** When the emitted `get.py`
carries a non-empty `VARIANTS` list, the operator MUST select one at install/update time —
the target webhoster has no interpreter to auto-detect, so there is **no silent default**.
Resolution order (first hit wins):

1. `--variant NAME` — explicit; rejected with the available list if unknown (exit 2).
2. A variant remembered from a prior install (persisted at `<root>/shared/.variant`), so
   `update` stays on the host's interpreter unless `--variant` overrides it.
3. On an interactive TTY: a numbered prompt listing each variant's `name` (and `label`).
4. Otherwise: a fatal error (exit 2) that lists the available variants.

The chosen variant is written to `<root>/shared/.variant` (preserved across updates) and
drives the download asset name (`<tag>-<variant><asset_suffix>`, S6.3 step 3). When `VARIANTS`
is empty every step above is skipped and the installer behaves **byte-for-byte** as before
(single asset `<tag><asset_suffix>`).

---

## S7 — Delegated Steps

Commodity concerns are delegated to external OSS tools and MUST NOT be reimplemented in cmru.

**S7.1** Delegated tools:

| Key | Tool | Purpose |
|---|---|---|
| `sign` | `cosign` | OCI image signing (keyless or key-based); optional defense-in-depth for v1 |
| `minisign` | `minisign` | Detached Ed25519 signing of `manifest.json` (the bundle release manifest) |
| `sbom` | `syft` + `grype` | SBOM generation and vulnerability scan |
| `changelog` | `git-cliff` | Changelog from conventional commits |
| `nfpm` | `nfpm` | Build `.deb` / `.rpm` packages |

**S7.5** `minisign` manifest signing (`[project.<name>.delegated.minisign]`):

- **Sign**: `minisign -S -s <secret_key> -m manifest.json -t "<trusted_comment>"` →
  produces `manifest.json.minisig`.
- **Verify**: `minisign -Vm manifest.json -p <public_key>` → exit 0 only if Ed25519
  signature AND trusted comment both verify.
- **Trusted comment** (tamper-evident, signed): `project=<name> tag=<tag> manifest_sha256=<hex>`.
  The `manifest_sha256` binds the signature to the exact manifest bytes.
- **Key generation** (one-time, operator responsibility):
  `minisign -G -p minisign.pub -s minisign.key`
  The **secret key** (`minisign.key`) is a release-time secret: resolve from an env var
  or a gitignored file — **never committed**, never in `cmru.toml` (same discipline as
  the GitHub token, S2.4). The **public key** (`minisign.pub`) is published and
  distributed to hosts as part of the deployment enrollment seed.
- cosign remains available for **optional** in-registry image signing as later
  defense-in-depth; it is not used in v1 for the manifest.

**S7.2** If a delegated tool is absent and `required = false` (default), cmru MUST skip that step silently (or with a one-line note at `--verbose`).

**S7.3** If a delegated tool is absent and `required = true`, cmru MUST exit 3 (S8).

**S7.4** Delegated tools MUST be called via subprocess; their output MUST be captured to the step log.

---

## S8 — Exit Codes

cmru uses a four-value exit code scheme identical to CIU S10.3:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Build or publish failure (artifact error, upload failed, tag push failed) |
| `2` | Configuration error (missing required field, unknown key, parse error) |
| `3` | Missing prerequisite (required env var absent, required delegated tool absent) |

---

## S9 — Reproducibility

**S9.1** `SOURCE_DATE_EPOCH` MUST be set to the Unix timestamp of the HEAD commit before every build step (runner responsibility, S3.3).

**S9.2** OCI image labels `org.opencontainers.image.created` etc. MUST be sourced from HEAD commit metadata, not `date`.

**S9.3** For the `scm` versioning strategy, the clean version string (no `.dev`) is only emitted on an annotated tag. Untagged builds MUST produce a dev suffix.

**S9.4** Given the same source commit and toolchain pin, two independent builds MUST produce byte-identical artifacts (deterministic build contract). For the `bundle` profile specifically:

- Archive membership comes from an explicit git-tracked allowlist (never a recursive walk).
- Every `TarInfo` is normalized: `mtime = SOURCE_DATE_EPOCH`; `uid = gid = 0`;
  `uname = gname = ""`; mode = `0o644` (files) / `0o755` (executable files);
  members sorted by path in byte order.
- Compression: `tarfile` with `mode="w:xz"` (fixed format; no timestamp in container).
- Hard excludes applied belt-and-suspenders: `.git`, `.ciu`, rendered `*.toml`, `ciu.env`,
  `minisign.key`, `__pycache__`, `*.pyc`, `*.log`, `*.pem/.key/.crt` and similar.
  The source-package metadata file `pyproject.toml` is the deliberate exception when
  its parent path is explicitly allowlisted (for example, a bundled companion client).
- The production `run_bundle()` path MUST use this normalized writer for `xztar`; it
  must not bypass membership filtering through `copytree`/`make_archive`.
- A **build-twice gate** in the test suite asserts identical sha256 across two builds from
  the same `SOURCE_DATE_EPOCH`; flipping the epoch asserts the digest changes.

**S9.5** `manifest.json` MUST be serialized canonically so it is itself deterministic:
UTF-8, `sort_keys=True`, `separators=(",", ":")` (compact, no spaces), trailing newline.
Two builds of the same inputs MUST produce byte-identical `manifest.json`. The `created`
field is derived from `SOURCE_DATE_EPOCH` (`datetime.fromtimestamp(epoch, tz=UTC)`), never
wall-clock time.

---

## S10 — Validation Catalog

_This section enumerates all config validation rules. Each rule references the section that defines the requirement._

| ID | Rule | Exit |
|---|---|---|
| V01 | `[github].owner` is present and non-empty | 2 |
| V02 | `[github].repo` is present and non-empty | 2 |
| V03 | `[github].owner_type` is `"user"` or `"org"` | 2 |
| V04 | `[targets].host` is a known provider (S11) | 2 |
| V05 | Each `[project.<name>]` has a unique `prefix` | 2 |
| V06 | `artifact` is one of `wheel\|oci\|tarball\|bundle` | 2 |
| V07 | `version.strategy` is `scm`, `file:<path>`, or `counter` | 2 |
| V08 | `version.bump` is `conventional` or `patch` | 2 |
| V09 | No unknown keys at any config level (including `[getsh]` — retired; use `[installer]`) | 2 |
| V10 | `github.token` present or `GITHUB_TOKEN` env var set (for publish) | 3 |
| V11 | All `required_env` vars present before step execution | 3 |
| V12 | All `required = true` delegated tools present before step execution | 3 |
| V13 | `[installer].install_dir_system` is required when `[installer]` is present | 2 |
| V14 | `[installer].install_dir_user` is required when `[installer]` is present | 2 |
| V15 | `[installer.wheels[*]].path` and `.distribution` are required | 2 |
| V16 | `installer.required_commands` are checked before network I/O (exit 3) | 3 |
| V17 | Token file for `--github-token-file` must be owned by current user and chmod 600 | 2 |
| V22 | `[[project.<name>.variants]].name` is present, unique, and filename-safe (`[A-Za-z0-9][A-Za-z0-9._-]*`); unknown variant keys are rejected | 2 |

---

## S11 — Targets & Host Abstraction

**S11.1** `ReleaseHost` interface. Any release host provider MUST implement:

```python
class ReleaseHost:
    def create_release(self, tag, name, body, commitish, draft, prerelease) -> str: ...
    def upload_asset(self, release_id, path, content_type) -> str: ...
    def list_releases(self, prefix) -> list[dict]: ...
    def resolve_latest(self, prefix) -> dict: ...
    def download_url(self, tag, asset_name) -> str: ...
```

**S11.2** v1 ships only the GitHub implementation. Gitea/Forgejo and S3/MinIO object-store are fast-follow; new hosts MUST implement S11.1, not be hard-coded.

**S11.3** `[targets].registry` is a list of OCI registries. The runner MUST push one image to each registry in a single `docker buildx bake` invocation using bake's tag matrix.

**S11.4** GH Enterprise is nearly free: `api_base` is already a parameter on the GitHub implementation.

---

## S12 — Versioning & Release Trigger

**S12.1** `cmru status` performs a dry-run: for each project, reports whether the subtree changed since last `<prefix>-v*` tag and what version would be minted.

**S12.2** Change detection: a project is eligible for release iff `git log <last_tag>..HEAD -- <paths>` is non-empty. If no prior tag exists, the project is always eligible (first release).

**S12.3** `<paths>` defaults to `[project.<name>.cwd]`. Additional shared paths MAY be listed in `version.paths`.

**S12.4** Version bump rules (in priority order):
1. `--set-version <v>` — explicit override.
2. `--major` / `--minor` — force bump level.
3. `conventional` strategy: scan commits since last tag; `feat:` → minor, `BREAKING CHANGE` or `!` → major, all else → patch.
4. `patch` strategy: always increment patch.

**S12.5** Versioning strategies:

| Strategy | Mechanism | Commit? |
|---|---|---|
| `scm` | Tag HEAD; setuptools_scm reads it | No extra commit |
| `file:<PATH>` | Write version to file, commit, then tag | Yes (one bump commit) |
| `counter` | Find latest `-r<N>` suffix, increment; tag HEAD | No extra commit |

**S12.6** Dev builds: when HEAD is untagged, the version MUST be `X.Y.Z.devN+g<hash>`. These MUST NOT produce a `<prefix>-v` tag or immutable release.

**S12.7** `cmru release` MUST refuse to run on a dirty working tree.

**S12.8** Commit/tag ordering: for `file` strategy — write VERSION, stage, commit, then tag. For `scm`/`counter` — tag HEAD directly. In all cases: tag first, then build, then publish.

---

## S14 — OCI Image Handler (Built-in)

Design a built-in handler for the `oci-image` artifact profile. Currently, OCI projects use
delegated custom scripts (build-push.py, release-repack.sh). The new handler makes this a
first-class cmru capability.

### S14.1 — Handler Profile

When a project declares `artifacts = ["oci-image"]`, cmru's built-in handler takes over the
`build` and `push` steps — no explicit `[steps.*]` needed. The handler is registered in
`handlers.py` as `cmd_oci_image_build()` and `cmd_oci_image_push()`.

### S14.2 — Build Flow (no repack)

1. Resolve token → `docker login ghcr.io` (reuses `runner.py`'s existing `_docker_login()`).
2. Run `docker buildx bake -f docker-bake.hcl <target> --load` (or to OCI layout output directly).
3. No skopeo needed — the image is in the Docker daemon (or exported to OCI layout).

### S14.3 — Repack Flow (experimental, fail closed)

The built-in repack flow is **not production-ready and MUST NOT run**. A configuration
with `[project.X.oci].repack = true`, or a direct built-in handler invocation with
`--repack`, MUST fail with exit 2 before login, Docker execution, or scratch-path
mutation. Project-owned explicit steps remain the escape hatch for experiments, but
are not claimed to be equivalent to cmru's production OCI publish path.

The built-in path may be enabled only after it meets all of this definition of done:

1. Every invocation owns unique, automatically-cleaned scratch storage; no global
   `/tmp/oci-src` or `/tmp/oci-dst` can leak state between projects or runs.
2. BuildKit output and repacker input/output formats are proven compatible. OCI image
   layout directories and OCI tar archives MUST be distinguished and converted
   deliberately rather than treated as interchangeable paths.
3. The build/repack/push work runs through the governed builder and resource policy,
   including configured CPU, memory, I/O priority, and bounded repack concurrency.
4. The result passes structural OCI validation and runtime smoke validation, including
   expected platform, config, labels, entrypoint, filesystem semantics, and startup.
5. After push, cmru resolves the registry reference and verifies the final manifest
   digest against the validated local repacked artifact.
6. The design SHOULD build the image only once. If tooling forces a second build, that
   cost and the equivalence proof MUST be explicit; a silent build-on-push fallback is
   forbidden.

### S14.4 — Auth Flow (no more .ghcr-auth.json)

The current fragmented auth (cmru token → `docker login` for Docker, separate `REGISTRY_AUTH_FILE` for skopeo) is unified:

1. cmru resolves the GitHub token (S2.4 order: env → cmru.secret.toml → cmru.toml).
2. `runner.py`'s `_docker_login()` runs `docker login ghcr.io -u <owner> --password-stdin` with the token.
3. Docker credential store holds the auth. Both `docker buildx bake --push` and `docker-repack` (if used) read Docker credentials natively — no `.ghcr-auth.json` required.
4. The `.ghcr-auth.json` file and `REGISTRY_AUTH_FILE` references are deprecated.

### S14.5 — Preflight Prerequisites

Before the build step runs, the handler MUST validate:

| Tool | Condition | Error |
|---|---|---|
| `docker` | on PATH | exit 3, "docker not found" |
| `docker buildx` | `docker buildx version` succeeds | exit 3, "buildx not available" |
| `docker-repack` | reserved for a future enabled repack flow | `repack = true` currently fails earlier with exit 2 (S14.3) |
| GitHub token | resolved (S2.4) | exit 3 (V10) |

These checks happen in `runner.py` or a new `prerequisites` step.

### S14.6 — TOML Config Schema

Add these optional keys under `[project.<name>.oci]`:

```toml
[project.<name>.oci]
repack              = false       # reserved; true is rejected while S14.3 is experimental
repack_target_size  = "2GB"       # --target-size for docker-repack
repack_compression  = 9           # zstd compression level (1-22, default 9)
```

`repack = false` selects the supported simple bake flow (S14.2). `repack = true` is
accepted as a documented schema key but rejected by validation until the S14.3
production-equivalence definition of done is met.

### S14.7 — Push Step (separate from build)

Push is a **separate step** from build, executed by `cmd_oci_image_push()`:

- **Non-repack mode**: runs `docker buildx bake -f <bake_file> <target> --push` — the standard bake push.
- **Repack mode**: disabled and fail-closed as specified by S14.3. It MUST NOT
  reinterpret a local OCI path as a Docker build context, silently fall back to a
  non-repack bake push, or perform a second unvalidated build.

Tags pushed:
- Immutable tag: `<debian>-py<python>-<image_version>` (from build args, not git).
- Floating tag: `<debian>-py<python>-latest`.
- Tags are pushed to each registry in `[targets].registry`.
- The handler also commits the `commit_generated` paths (manifest files) after build.

### S14.8 — Validation Rules

Add to the validation catalog (S10):

| ID | Rule | Exit |
|---|---|---|
| V18 | `[project.<name>.oci].repack_target_size` must be a valid size string (e.g. "2GB", "500MB") when `repack = true` | 2 |
| V19 | `[project.<name>.oci].repack_compression` must be 1-22 when `repack = true` | 2 |
| V20 | No legacy `.ghcr-auth.json` or `REGISTRY_AUTH_FILE` references in new projects | 2 |
| V21 | Built-in `[project.<name>.oci].repack = true` is rejected while S14.3 remains experimental | 2 |

### S14.9 — Migration Path

Existing projects can opt in incrementally:

1. Keep their existing `[steps.build]` and `[steps.push]` commands as-is.
2. Add `[project.<name>.oci]` config and remove `[steps.*]` to use the built-in handler.
3. The `.ghcr-auth.json` file and skopeo remain available for manual use but are no longer required by the cmru release path.

---

## S13 — Reserved / Out of Scope

The following are explicitly **out of scope** for cmru v1 and MUST NOT be implemented:

- macOS/Windows code signing (Authenticode, Apple notarization).
- FTP/SFTP deploy targets (e.g., netcup `deploy.zip`). These are deploy operations, not releases.
- New release hosts beyond GitHub v1 (fast-follow, via S11 interface only).
- Vendoring any delegated tool (S7).
- Reimplementing changelog generation, SBOM, or signing logic.
