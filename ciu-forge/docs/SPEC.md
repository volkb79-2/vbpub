# ciu-forge SPEC

CIU conventions apply: section numbers are stable identifiers (S-numbers).
Breaking changes to a section bump the wheel MAJOR and include the S-ID in the changelog.
RFC 2119 key words (MUST, SHOULD, MAY, etc.) are normative.

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
| **runner** | The ciu-forge component that executes a single build step in a reproducible, logged environment. |
| **host** | A release storage provider implementing the `ReleaseHost` interface (S11). |
| **resolver** | The ciu-forge component that returns `{version, tag, asset, sha256, url}` for the highest-semver release. |
| **get.sh** | A per-project emitted bootstrap script implementing the S6 contract. |
| **delegated step** | A commodity operation (sign, SBOM, changelog, package) delegated to an external OSS tool (S7). |

---

## S1 — Project & Artifact Model

ciu-forge manages N independent projects, each with its own semver line, all sharing **one** GitHub Releases page per repository.

**S1.1** Each project has a `prefix` that MUST be unique within the repository. Tags take the form `<prefix><semver>` (e.g., `tls-edge-v0.2.0`). Tags are immutable once pushed; updating a tag is a violation of this SPEC.

**S1.2** Supported artifact types:

| Type | Description | Source |
|---|---|---|
| `wheel` | Python distribution wheel (`.whl`) | `python -m build` |
| `oci` | Container image | Docker buildx bake |
| `tarball` | Archive (`.tar.xz`, `.tar.gz`) | `tar` + custom build |
| `bundle` | Browser-automation bundle (`.zip`) or similar composite | project-specific bundler |

**S1.3** Each release MUST upload:
- The artifact file itself (immutable, content-addressed by version+hash in release notes).
- A `.sha256` sidecar containing one line in `sha256sum -c` format.

**S1.4** OCI images MUST record the pushed manifest digest (`sha256:…`) in the release notes.

**S1.5** N projects, one Releases page is the first differentiator. The `prefix` mechanism is the key: the resolver (S5) and get.sh (S6) filter by prefix, so projects never interfere with each other.

---

## S2 — Config Schema

ciu-forge reads a single TOML file per repository (default: `ciu-forge.toml` at the repo root). During migration a project MAY `include = "build-push.toml"` to merge a legacy config.

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

[project.<name>.getsh]
install_dir = "/opt/<name>-src"   # default install root
preserve    = [                   # config files preserved across updates
  "<name>/config.toml",
]

[project.<name>.delegated]
sign      = false                 # cosign sign
sbom      = false                 # syft + grype
changelog = false                 # git-cliff
nfpm      = false                 # nfpm deb/rpm
```

**S2.3** Unknown keys MUST be rejected (fail-fast). All required fields MUST be present or the config is invalid.

---

## S3 — Single Runner Contract

Every build step MUST be executed through the ciu-forge runner. The orchestrator MUST NOT invoke build commands directly.

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

**S4.1** `ciu-forge publish` uploads the artifact and sidecar to the release host (S11).

**S4.2** Before uploading, ciu-forge MUST compute `sha256sum` of the artifact and write a `.sha256` sidecar in `sha256sum -c` compatible format (one line: `<hash>  <filename>`).

**S4.3** The release notes body MUST include the artifact's SHA-256 digest and, for OCI artifacts, the manifest digest.

**S4.4** If `latest_json = true`, ciu-forge MUST create or update `<prefix>latest/latest.json` (see S5.3) as a separate release (or asset on a `<prefix>latest` tag).

**S4.5** Dev builds (untagged commits, version contains `.dev`) MUST NOT mint a `<prefix>-v` release. They MAY upload to a `<prefix>-dev` pre-release slot.

**S4.6** `target_commitish` in the GitHub release MUST be set to the commit SHA at build time.

---

## S5 — Resolver

The resolver implements differentiator #2: highest-semver selection, replacing GitHub's single repo-global "Latest" badge.

**S5.1** `ciu-forge resolve --project <name>` returns `{version, tag, asset, sha256, url}` for the highest-semver release matching `prefix`.

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

**S5.4** Fallback if latest.json is absent or stale: scan releases via host API, filter by prefix, select max semver.

**S5.5** `--format` flag: `json` (default), `env` (shell-sourceable `KEY=value` lines), `url` (bare download URL).

---

## S6 — get.sh Contract

The emitted `get.sh` is differentiator #4: a per-project bootstrap that handles install, update, version pin, and user-config preservation.

**S6.1** `ciu-forge get-sh --project <name>` emits a standalone bash script to stdout. The project's get.sh is the rendered output of `templates/get.sh.tmpl`.

**S6.2** The emitted script MUST implement:
1. **resolve** — call resolver (S5) to find the highest-semver tag (or honour `<PREFIX>_VERSION` pin).
2. **download** — fetch artifact + sidecar from the release host.
3. **verify** — `sha256sum -c <sidecar>` MUST pass before any extraction. Verification is non-optional.
4. **install/update** — extract to `install_dir`; write `VERSION` file.
5. **preserve** — on update, back up files listed in `[project.getsh].preserve` and restore them if the new artifact does not supply them.

**S6.3** Air-gapped fallback: if `<PREFIX>_INSTALL_VIA=git`, use `git sparse-checkout` to install. No checksum verification in git mode (the user is trusting the git transport). MUST warn.

**S6.4** The script MUST require `python3 >= 3.11` (for the semver resolver inline script).

**S6.5** All dependency checks (curl, sha256sum, python3, project-specific deps) MUST run before any network I/O.

**S6.6** `<PREFIX>_VERSION` env var pins the install to a specific tag (bare semver or full tag).

---

## S7 — Delegated Steps

Commodity concerns are delegated to external OSS tools and MUST NOT be reimplemented in ciu-forge.

**S7.1** Delegated tools:

| Key | Tool | Purpose |
|---|---|---|
| `sign` | `cosign` | Sign artifacts (keyless or key-based) |
| `sbom` | `syft` + `grype` | SBOM generation and vulnerability scan |
| `changelog` | `git-cliff` | Changelog from conventional commits |
| `nfpm` | `nfpm` | Build `.deb` / `.rpm` packages |

**S7.2** If a delegated tool is absent and `required = false` (default), ciu-forge MUST skip that step silently (or with a one-line note at `--verbose`).

**S7.3** If a delegated tool is absent and `required = true`, ciu-forge MUST exit 3 (S8).

**S7.4** Delegated tools MUST be called via subprocess; their output MUST be captured to the step log.

---

## S8 — Exit Codes

ciu-forge uses a four-value exit code scheme identical to CIU S10.3:

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

**S9.4** Given the same source commit and toolchain pin, two independent builds MUST produce byte-identical artifacts (deterministic build contract).

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
| V09 | No unknown keys at any config level | 2 |
| V10 | `github.token` present or `GITHUB_TOKEN` env var set (for publish) | 3 |
| V11 | All `required_env` vars present before step execution | 3 |
| V12 | All `required = true` delegated tools present before step execution | 3 |

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

**S12.1** `ciu-forge status` performs a dry-run: for each project, reports whether the subtree changed since last `<prefix>-v*` tag and what version would be minted.

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

**S12.7** `ciu-forge release` MUST refuse to run on a dirty working tree.

**S12.8** Commit/tag ordering: for `file` strategy — write VERSION, stage, commit, then tag. For `scm`/`counter` — tag HEAD directly. In all cases: tag first, then build, then publish.

---

## S13 — Reserved / Out of Scope

The following are explicitly **out of scope** for ciu-forge v1 and MUST NOT be implemented:

- macOS/Windows code signing (Authenticode, Apple notarization).
- FTP/SFTP deploy targets (e.g., netcup `deploy.zip`). These are deploy operations, not releases.
- New release hosts beyond GitHub v1 (fast-follow, via S11 interface only).
- Vendoring any delegated tool (S7).
- Reimplementing changelog generation, SBOM, or signing logic.
