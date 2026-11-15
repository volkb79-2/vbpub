<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/DSTDNS-CMRU-ADOPTION-MEMO-2026-11-05.md -->
<!-- relation: external-project cmru adoption -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# dstdns: adopting cmru as the release and version tool (design memo, 2026-11-05)

Status: design proposal, no code changed. Scope: `<checkout>` main `c25c53d1` plus the unmerged
`tools-adoption-20261105` branch (`d9d2a7e6`). cmru 7.2.0 is installed. ciu is 7.19/7.20, run-gate 24.3/25.0,
and assay is 11.0.0 on the branch.

Tags used below: **[F]** is a fact with a source, **[D]** is a decision this memo makes, **[E]** is an estimate,
and **[Q]** is a genuine operator question (all in section 6.5).

Sources read:
- dstdns: `CLAUDE.md`, `AGENTS.md`, `nyxloom-trove/GUIDE.md`, `DECISIONS-IN-FORCE.md`, `cmru.toml`, `docker-bake.hcl`,
  `requirements.txt`, `run-gate.toml`, `assay.toml`, every `ciu.defaults.toml.j2`/compose template that declares an
  image, `docs/DEPLOYMENT-GUIDE.md`, `docs/testing/{RIGOR-COVERAGE-POLICY,COVERAGE-MAP}.md`, `docs/plan-repair-and-advancement-2026-10.md`,
  and the archived SPEC F and `plan-cmru-remote-deployment.md`.
- cmru: `SPEC.md` (S0-S2, S5, S6, S12, S14-S16), `CONSUMERS.md`, `DESIGN-GUIDE.md`, `RELEASE-TRANSACTIONS.md`, and source
  (`config.py`, `cli.py` cleanup).
- Config examples: vbpub `cmru.orchestration.toml`, and `cmru.toml` for pwmcp, tls-edge, nyxloom, cgroup-profiler and cmru.
- ciu: `CONFIG.md` (layering), `CONSUMERS.md` §25 (CIU-117), `SPEC.md` S14 (`--host`).
- Reports: `DSTDNS-BENEFIT-ANALYSIS-2026-11-04.md`, `dstdns-tools-adoption-REPORT.md`, and the <host>/<host> reinstall reports.

---

## 0. Decisions at a glance

1. **[D] dstdns is ONE cmru project, `dstdns`, prefix `dstdns-v`, released in lockstep.** One version covers:
   - the 6 product images,
   - the 2 test images,
   - the ddcli wheel,
   - the deploy bundle.

   Per-service projects are infeasible: cmru watch paths cannot leave the project directory, and every service
   depends on `libs/` and `applications/base`. They are also undesirable, because §4.1 greenfield means no
   mixed-version compatibility is ever built or tested (section 1.2).
2. **[D] Versioning is `scm` + `bump = "conventional"`.** The first release is `--set-version 1.0.0`.
   - **MAJOR means "not upgradable in place"**: DDL, a config contract or the result-payload contract changed, so
     the host needs a fresh landscape.
   - MINOR is `feat`. PATCH is everything else.
   - A release-gate guard refuses a non-major release whose DDL or config schema changed since the previous tag
     (section 2.5).
3. **[D] What cmru determines:**
   - whether a release is due (`cmru status`);
   - the product version (conventional commits);
   - every third-party version (`cmru versions`: 14-day window, rolling tags recorded by digest), for images,
     PyPI and npm.

   Bake decides what physically rebuilds, through its layer cache. vbpub tools stay exempt from the age window (D-672).
4. **[D] Every image reference moves to one global `[images.<key>]` table.** The table lives in
   `ciu.global.defaults.toml.j2`, and every stack reads it.
   - Source mode builds `dstdns/<svc>:local`.
   - A release bundle overrides the table with `ghcr.io/volkb79-2/dstdns-<svc>:<V>@sha256:…` and digest-pinned
     third-party refs.
   - Hosts pull digests and never build.
5. **[D] Deployment stays push-only (D-097/D-574), but it pushes a released bundle, never a working tree.**
   1. The cockpit installs the bundle with the cmru-rendered `get.py`.
   2. It runs `ciu up --host <h>` from the installed `stack/` directory.
   3. Rollback is `get.py rollback` plus `ciu up --host` within one major. A major boundary needs a fresh landscape.
6. **[D] Release gate (synchronous, in the candidate's own ciu instance): R0 + R1 + R3 + a bound review record.**
   - **Staging** is post-publish and gates promotion to production:
     - <host> runs the live lanes against the released images;
     - <host> rehearses the bundle deploy path.
   - **R2** runs asynchronously on the latest release on <host>. Survivors get a root cause and then a patch release.
7. **[D] Rigor record: a committed `rigor/<tag>.json` per release plus `rigor/index.json`, owned by a generic
   `cmru rigor` verb.** The same record serves every vbpub tool, and main always shows what each release proved.
8. **[F→D] Precondition: vbpub's `cmru cleanup` is owner-wide for GHCR.**
   - The vbpub root `cmru.orchestration.toml` has `ghcr_packages = ["*"]`, and `cleanup_ghcr` lists every container
     package of owner `volkb79-2` (`cmru/src/cmru/cli.py:1400-1425`, `:1531-1586`).
   - It then deletes every version older than the cutoff.
   - So once `dstdns-*` packages exist, a vbpub cleanup would delete dstdns images that hosts still run.
   - This must be fixed in cmru (scope `*` to packages linked to the configured repository) before the first dstdns
     image push (V1).
9. **[F] The current `dstdns/cmru.toml` is invalid under cmru 7.x.** `cmru standards` reports
   `[ERROR] cmru.toml.schema_version must be exactly 1`. It uses the retired `[project.dstdns]`/`cwd` form, and
   its installer section points at the superseded SPEC F pull adapter. P1 replaces it.

---

## 1. Inventory of releasable units

### 1.1 Every unit and its disposition

| Unit | What it is today | Disposition | Published as |
|---|---|---|---|
| `controller` | bake target, `applications/controller` | **product image** | `ghcr.io/volkb79-2/dstdns-controller:<V>` |
| `worker-io` | bake target | **product image** | `dstdns-worker-io:<V>` |
| `worker-db` | bake target | **product image** | `dstdns-worker-db:<V>` |
| `webapp-server` | bake target | **product image** | `dstdns-webapp-server:<V>` |
| `webapp-ui-react` | bake target (node toolchain to nginx) | **product image** | `dstdns-webapp-ui-react:<V>` |
| `docker-stats-exporter` | bake target, `infra/` (FROM app-base) | **product image** (observability profile) | `dstdns-docker-stats-exporter:<V>` |
| `base-app` (`dstdns-app-base`) | internal bake stage | **internal**: a bake context, never pushed | (none) |
| `test-runner` | bake target, FROM app-base | **test artifact of the release**: needed for async R2 and staging live lanes on other hosts | `dstdns-test-runner:<V>` (never deployed) |
| `seeded-targets` | bake target, test fixture | **test artifact of the release**: staging live lanes need it | `dstdns-seeded-targets:<V>` (never deployed) |
| `ddcli` wheel | `applications/ddcli/pyproject.toml`, static `0.1.0` | **product wheel**: lockstep, because it is coupled to the API | inside the bundle (`wheels/ddcli-<V>-py3-none-any.whl`); `get.py` installs it with a `ddcli` launcher |
| `ddcli` image | bake target FROM the multi-GB devcontainer image, in no deploy profile | **internal tool**: moves from `all-services` to `all-tools` | (none) |
| `admin-debug` image | bake target FROM the devcontainer image | **internal tool** (cockpit peer); remote diagnosis uses `ciu exec` | (none) |
| `libs/common`, `libs/common_config`, the app `pyproject.toml`s | built into images (`common-wheel-builder` stage) | **internal**; their static versions are not release identifiers | (none) |
| `webapp-ui-react` npm package | built into its image | **internal** | (none) |
| Deploy bundle | does not exist; `ciu up --host` rsyncs the working tree today | **product bundle**: a ciu root with deployable stacks, hooks, `infra/db-init` scripts, `conf/`, generated release pins, vendored ciu + ddcli wheels, deploy docs | `dstdns-v<V>.tar.xz` + `.sha256` + in-bundle `manifest.json`; extra assets `get.py` and `images.json` |
| Deployable stacks | `infra/{tailscale-node,vault,consul-server,redis-core,db-core,db-init,authentik,otel-aggregator,otel-collector-node,docker-stats-exporter}`, `applications/{controller,worker-io,worker-db,webapp-server,webapp-ui-react}`, `infra-global/{reverse-proxy,skywalking}` | **in the bundle** (profiles core, db, identity, observability, apps, worker-io, worker-db, external) | (via the bundle) |
| Dev, test and CI stacks | `tools/{test-runner,admin-debug}`, `infra/{pwmcp,seeded-targets,mock-targets,schema-gate-pg,cadvisor}`, `infra-global/{github-runner,github-runner-webhook,webhook-listener,registry-lightweight}` | **excluded from the bundle** | (none) |
| `CHANGES.md` | does not exist | **generated by cmru** at the repo root (release-history marker), also the release body | release notes |
| Operator docs | `docs/DEPLOYMENT-GUIDE.md`, `VAULT-AND-SECRETS.md`, `STARTUP-SEQUENCE.md`, `CONFIGURATION.md`, stack READMEs | **copied into the bundle** (`docs/`) | (via the bundle) |
| Vendored `tools/assay/*.pyz` | a consumed vbpub artifact | **not released**: an input recorded in the rigor record | (none) |
| `tools/ksm-optin`, `scripts/*`, `retired-legacy/`, `legacy-experiments/`, `projects/python-version-test/`, untracked `applications/webapp-ui/` | host helpers and history | **none** | (none) |

### 1.2 Why one project, not one per service

- **[F] Watch paths are project-relative only.** cmru rejects `..` in `project.version.paths`
  (`cmru/src/cmru/config.py:578-608`), and `watch_paths = [cwd] + paths` (`cli.py:811-815`).
  - A per-service project at `applications/controller/` could not watch `libs/common/`, `applications/base/`,
    `requirements.txt` or `infra/db-init/`.
  - A change there would therefore never re-release the services that contain it: a silent wrong answer.
- **[F] The services share runtime contracts**: the result-payload version (D-058), the operation registry, one DB
  schema, and one Consul KV layout.
- **[F] §4.1 forbids compatibility shims.** Mixed-version landscapes are therefore unsupported by construction, and
  independent service versions would only advertise combinations nobody tests.
- **[D] The project root is the repository root** (`cmru.toml` beside `cmru.orchestration.toml`).
- **Shortcoming:** change detection then sees every commit, including docs-only and `nyxloom-trove/` commits, so
  `cmru status` always reports dstdns as changed.
  - Release cadence is the controller's decision anyway (section 2.8).
  - V2 files a cmru `project.version.exclude_paths` so that `status` reports only product changes.

### 1.3 Components that are versioned but not released by dstdns

| Component class | Version authority | Recorded where |
|---|---|---|
| Third-party images: postgres/timescale, redis, vault, consul, authentik, minio, pgadmin, adminer, alpine, otel, cadvisor, tailscale, skywalking, nginx, node, python | `cmru versions` OCI targets (14-day window; rolling tags plus digest) | `versions/oci-images.json`; digests repeated in the bundle's `images.json` |
| PyPI deps of the images | `cmru versions` PyPI targets, with `constraints/constraints.txt` consumed by `Dockerfile.base` and the runner. **[F]** today `applications/base/Dockerfile.base:50-73` installs bare names, so every bake re-resolves PyPI | committed constraints |
| npm deps (`webapp-ui-react`) | `cmru versions` npm targets (`package.json` + lockfile) | committed lockfile |
| vbpub tools (ciu, run-gate, assay, cmru, cgprofile, pwmcp, the devcontainer base image) | latest release, no age window (D-672); `requirements.txt` floors | the rigor record `tools` block; the ciu wheel version also in the bundle manifest |

---

## 2. cmru configuration, versioning and the release flow

### 2.1 `cmru.orchestration.toml` (repo root, new)

```toml
schema_version = 1

[github]
owner = "volkb79-2"
repo = "dstdns"
owner_type = "user"
token_source = "gh"            # gh login needs repo + write:packages on volkb79-2/dstdns

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order = ["dstdns"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"

# BuildKit endpoint: ambient from the mdt devcontainer (AGENTS §4.9). Required, no fallback (§4.2a).
[orchestration.defaults.env]
BUILDX_BUILDER = "${BUILDX_BUILDER}"
BUILDKIT_HOST = "${BUILDKIT_HOST}"

[orchestration.project.dstdns]
config = "cmru.toml"
depends_on = []                # vbpub providers live in another repository (see V3)

[cleanup]
release_tag_prefixes = ["*"]   # repo-scoped: only dstdns releases exist here
keep_release_tags = ["dstdns-latest"]
ghcr_packages = []             # NEVER "*": cmru lists packages OWNER-wide (section 3.6)
ghcr_delete_packages = []

[versions]
age_window_days = 14

[versions.discovery]
scope = "all"                  # the gate image installs test deps too; they get the window as well
```

### 2.2 `cmru.toml` (repo root, replaces the legacy file; `cmru.sample.toml` is deleted)

```toml
schema_version = 1

[runtime]
kind = "ciu"                   # the gate step owns an explicit ciu up / run-gate / ciu clean sequence

[env]
DSTDNS_RELEASE_NOTES = "## dstdns\n\nDeploy bundle + service images. Install on a cockpit with `get.py install`, deploy with `ciu up --host` from <root>/stack (docs/DEPLOYMENT-GUIDE.md). Image digests: images.json."

[project]
id = "dstdns"
description = "DST-DNS platform: service images, deploy bundle and the ddcli client."
template_revision = 5
prefix = "dstdns-v"
artifacts = ["oci-image", "tarball"]   # the ddcli wheel ships inside the tarball

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
evidence_paths = [".assay", "release-evidence"]   # verdicts + gate.py's summary; gitignored

[project.installer]                  # used by the cockpit, never by a target host
install_dir_system = "/srv/dstdns-release"
install_dir_user = "dstdns-release"
asset_suffix = ".tar.xz"
required_commands = ["python3", "docker", "git"]
manifest_name = "manifest.json"
launchers = ["ciu", "ddcli"]
stable_dir = "stack"                 # <root>/stack mirrors the active release: the ciu root to deploy from
stub_launchers = ["ciu"]
# manifest_pubkey = "<P10 sets it; signed releases become mandatory before the first production deploy>"

[[project.installer.wheels]]
path = "wheels/ciu-*.whl"
distribution = "ciu"
# ...plus cli-extended, Jinja2, MarkupSafe, PyYAML and tomli_w (ciu's closure, as tls-edge does)
# and ddcli with click, rich, requests and pyyaml. Single interpreter: the cockpit is cp314.

[[project.installer.wheels]]
path = "wheels/ddcli-*.whl"
distribution = "ddcli"

[steps.prepare]
quiet = true
commands = [
  { label = "dstdns: release preflight (review binding, tool floors, rigor waivers)", argv = ["python3", "scripts/release/preflight.py"], cwd = "." },
  { label = "dstdns: resolved third-party versions still hold", argv = ["cmru", "versions", "check", "dstdns"], cwd = "." },
]

[steps.run-tests]
quiet = true
commands = [
  { label = "dstdns: R0+R1+R3 release gate in the candidate's own ciu instance", argv = ["python3", "scripts/release/gate.py"], cwd = "." },
]

[steps.build]
quiet = true
env_command = ["python3", "scripts/release/env.py"]   # VERSION, REGISTRY, BUILD_VERSION, SOURCE_DATE_EPOCH from CMRU_RELEASE_TAG/COMMIT
commands = [
  { label = "dstdns: bake release + test images and the ddcli wheel", argv = ["cmru", "handler", "oci-image-build", "--cwd", ".", "--bake-file", "docker-bake.hcl", "--bake-target", "release-all"], cwd = "." },
  { label = "dstdns: verify images (labels = tag/commit, non-root, import smoke)", argv = ["python3", "scripts/release/artifacts.py", "verify-images"], cwd = "." },
  { label = "dstdns: stage the bundle and prove it with ciu check", argv = ["python3", "scripts/release/artifacts.py", "stage-bundle", "--check-only"], cwd = "." },
  { label = "dstdns: render get.py", argv = ["cmru", "installer", "render", "dstdns", "--output", "dist/get.py"], cwd = "." },
]

[steps.push]
quiet = true
env_command = ["python3", "scripts/release/env.py"]
commands = [
  { label = "dstdns: push release + test images", argv = ["cmru", "handler", "oci-image-push", "--cwd", ".", "--bake-file", "docker-bake.hcl", "--bake-target", "release-all"], cwd = "." },
  { label = "dstdns: read pushed digests, pin, build the bundle", argv = ["python3", "scripts/release/artifacts.py", "bundle"], cwd = "." },
  # The body handler is the LAST command, and there is only one (CONSUMERS "Image-only releases").
  { label = "dstdns: publish bundle + get.py + images.json", argv = ["cmru", "handler", "tarball-publish", "--prefix", "dstdns", "--cwd", ".", "--glob", "dstdns-v*.tar.xz", "--version-file", "dist/VERSION", "--notes-env", "DSTDNS_RELEASE_NOTES", "--extra-asset", "dist/get.py", "--extra-asset", "dist/images.json"], cwd = "." },
]

# [versions.targets.*]: one per third-party image, plus init-generated PyPI/npm targets (P3). Examples:
[versions.targets."oci.postgres-init"]
mode = "single"
constraint = ">=18.0.0,<19.0.0"
[versions.targets."oci.postgres-init".oci]
image = "docker.io/library/postgres"
tag = "{version}-alpine"             # replaces the floating "18-alpine"

[versions.targets."oci.timescaledb"]
mode = "single"
constraint = "*"
[versions.targets."oci.timescaledb".oci]
image = "docker.io/timescale/timescaledb-ha"
tag = "pg18.4-ts2.27.1"
selection = "rolling"                # non-SemVer: literal tag, digest recorded

[versions.targets."oci.node-toolchain"]
mode = "single"
constraint = "*"
[versions.targets."oci.node-toolchain".oci]
image = "docker.io/library/node"
tag = "current-trixie-slim"
selection = "rolling"                # the bake `pull = true` moving tag becomes a recorded digest
```

**Notes on the skeleton:**
- **[D] No `tester-gate` and no `wheel-build`.**
  - dstdns gates in its own `test-runner` through run-gate (AGENTS §4.9), so no `CMRU_TESTER_*` inputs are needed.
  - The ddcli wheel is a bake target (`ddcli-wheel`, `output = ["type=local,dest=dist/wheels"]`), so it uses the
    same governed BuildKit and pins.
  - This avoids a cross-repository `wheel-builder` image.
- **[F]** `env_command` and the handler grammar are cmru S2/S14.
- **[F]** Only one release-body handler is allowed per push step (CONSUMERS §"Image-only releases").
- **[D] No moving `latest` image tag** (D-673's lesson). The `dstdns-latest` GitHub pointer (`latest.json`) is the
  only moving reference, and production does not use it (section 3.3).

### 2.3 `docker-bake.hcl` changes

- **[D]** New variables:
  - `VERSION` (default `local`);
  - `REGISTRY` (default empty, which means local `dstdns/<svc>:local`);
  - `BUILD_VERSION = VERSION`.
- **[D]** Release tags are `${REGISTRY}/dstdns-<svc>:${VERSION}`. This also fixes the inconsistent
  `${REGISTRY}/${NAMESPACE}/webapp-ui-react` form.
- **[D]** OCI labels `org.opencontainers.image.{version,revision,source,created}` come from `env.py`.
- **[D]** New groups:
  - `release-images`: the 6 product images;
  - `release-test-images`: `test-runner`, `seeded-targets`;
  - `release-all`: both groups plus `ddcli-wheel`.
- **[D]** `ddcli` moves to `all-tools`.
- **[D]** `NODE_TOOLCHAIN_IMAGE` and `SEEDED_PYTHON_BASE_IMAGE` take their values from the cmru-resolved record. A
  config test compares them, because HCL cannot read TOML (the existing `test_seeded_targets_bake.py` pattern).

### 2.4 Tags, names and pointers

| Item | Value |
|---|---|
| Git tag / GitHub release | `dstdns-v<MAJOR.MINOR.PATCH>` (cmru-minted, never by hand, S12.2c) |
| Moving pointer | `dstdns-latest/latest.json`: highest published release, for `cmru resolve dstdns` |
| Production pointer | `rigor/index.json` `"stable": "<V>"` (section 3.3), set only by promotion |
| Images | `ghcr.io/volkb79-2/dstdns-<svc>:<V>`, always deployed as `…:<V>@sha256:<digest>` |
| Bundle assets | `dstdns-v<V>.tar.xz`, `.sha256`, `get.py`, `images.json` (later `rigor.json` and `dstdns-v<V>-r2-evidence.tar.xz`, section 5) |

### 2.5 Versioning policy

- **[D] Strategy `scm`.** The version is dstdns's own, and `external:` is for derived versions (pwmcp follows
  Playwright). `file:` would add a bump commit for nothing.
- **[D] Bumps:**
  - `feat:` gives MINOR.
  - `!` or `BREAKING CHANGE` gives MAJOR.
  - Everything else gives PATCH (S12.4).
  - `--major`/`--minor` are controller overrides that must be recorded in the release's D-record.
- **[D] MAJOR has an operational meaning: "this release cannot be applied in place".** It applies when any of these
  changed since the previous tag:
  - `infra/db-init/init-scripts` (DDL);
  - an app config JSON schema (`gen-config-schema.py` output);
  - the Consul KV layout;
  - the result-payload contract version.
- **Guard (mechanical):**
  - `scripts/release/gate.py` reads the protected `CMRU_RELEASE_GATE_VERSION` (cmru S2.4) and diffs those paths
    against the previous `dstdns-v*` tag.
  - It refuses (exit 1, before any lane) when they changed and the planned bump is not a major.
  - The fix is `--major` on the re-run.
  - **[D]** This makes the deploy rule in section 3.5 safe without trusting commit discipline alone.
- **[D] First release:** `cmru release dstdns --set-version 1.0.0`. cmru has no 0.x semantics: a `!` at 0.x would
  jump to 1.0.0 anyway. Majors will be frequent under §4.1 greenfield, and that is honest: each one is a data reset.
- **[D] Commit convention (GUIDE §4.4 addition):**
  - A package's `--no-ff` merge commit carries a conventional subject.
  - Mark `!` exactly when the package triggers the guard above.
  - Leave other subjects as they are: the default is patch.
- **[D] Internal pyproject versions:**
  - The internal `pyproject.toml`/`package.json` versions become a fixed `0.0.0`, with a comment pointing to the
    product version.
  - ddcli becomes `dynamic = ["version"]` through setuptools-scm, with `root = "../.."` and
    `tag_regex = "^dstdns-v(?P<version>…)$"`.
  - `BUILD_VERSION` (the config-schema publish key, `appconfig/bootstrap.py`) becomes the product version for
    release deploys.
  - **[F]** Today ciu overrides it with its git hash (`ciu/engine.py:265`, compose `BUILD_VERSION={{ auto_generated.build_version }}`).
  - A bundle has no `.git`, so P4 moves the compose templates to one `[product].version` key. Source mode sets that
    key from the commit, and the release pins set it to `<V>` (section 3.1).

### 2.6 Product version vs component versions

- **[D] Lockstep.** `<V>` is the version of every dstdns-built artifact: 6 images, 2 test images, the ddcli wheel and
  the bundle.
- An unchanged service still gets the new tag. Usually the bake cache makes it the same layers, but it gets a new
  manifest digest, because the labels change.
- **Component versions are facts recorded per release, never independent lines:**
  - third-party image digests are in `images.json` (role `third-party`) and in the bundle's pins;
  - PyPI/npm come from committed constraints and the lockfile;
  - vbpub tools are in the rigor record (`tools`), and the vendored ciu wheel is in the bundle manifest.

### 2.7 Release flow (one `cmru release dstdns`)

The flow follows the cmru frame **prepare, gate, backup-push, tag, build, push, promote** (S-REL.5):

1. **prepare** (in the isolated `<worktree>*` candidate):
   - cmru generates `CHANGES.md`.
   - `preflight.py` checks:
     - (a) the release review record `nyxloom-trove/reviews/release/<prev-tag>..<sha>.md` exists, says
       `Verdict: ACCEPT`, and ends exactly at the candidate's source commit, so nothing merged after the review;
     - (b) the installed run-gate, assay and ciu satisfy `requirements.txt`;
     - (c) every entry in `release/rigor-waivers.toml` is unexpired for the planned version.
   - `cmru versions check` runs.
2. **run-tests:** `gate.py` (section 4.2).
3. **tag**, then **build:**
   - `env.py` verifies `CMRU_RELEASE_COMMIT == HEAD` and the tag, as cmru S2.4 requires of Git-derived artifacts.
   - Bake `release-all --load`, then verify the images.
   - Stage the bundle and run `ciu check` on it, with no Docker.
4. **push:**
   - Push the images.
   - `docker buildx imagetools inspect` gives the digests, which are written to `dist/images.json`.
   - Generate the bundle's release `ciu.global.toml.j2` (pins), then run `cmru handler bundle-manifest`.
   - Build a deterministic `tar.xz` (`SOURCE_DATE_EPOCH` = commit time) and run `ciu check` again.
   - Run `tarball-publish`, which writes the body from `CHANGES.md` and moves `dstdns-latest`.
5. **promote:** cmru pushes the candidate (only `CHANGES.md` changed) to `main`.
6. **After the release** (controller runbook, outside cmru until V4 ships `cmru rigor`):
   - record R0, R1 and R3;
   - staging (section 3.4);
   - promotion;
   - queue the async R2 run (section 4.4).

**[D] Cadence:** the controller releases on demand when `main` is green and a coherent set of packages has landed,
and always after each wave. **[D] Where:** the release transaction runs on the dev cockpit, which holds the `gh`
credentials, serially under the host load rule. R2 and staging run on <host>/<host>.

---

## 3. How deployment consumes released versions

### 3.1 Image reference model

- **[F]** Today each stack declares `image_name`/`image_tag` in its own `ciu.defaults.toml.j2`, and own images are
  `latest`.
- **[F]** ciu's merge chain is: global defaults, global overrides, instance, identity, **stack defaults**, stack
  overrides (`ciu/docs/CONFIG.md:96-99`). A global override therefore cannot replace a key a stack declares.
- **[F]** ciu has no Jinja include: `Environment()` has no loader (`ciu/config_model.py:479`).
- **[D] One global table**, in `ciu.global.defaults.toml.j2`, read by every stack template:

  ```toml
  [images.controller]
  ref = "dstdns/controller:local"                 # source mode: built from this checkout
  [images.postgres_init]
  ref = "docker.io/library/postgres:18.4-alpine"  # cmru-resolved tag (test-enforced, see below)
  ```

- **[D] Stack templates** write `image: {{ images.controller.ref }}`. The stack defaults render with the
  global-config context, so they can read the table.
- **[D] Release mode:** the bundle's generated `ciu.global.toml.j2`, which replaces the dev override file in the
  bundle, sets three things:
  - `ref = "ghcr.io/volkb79-2/dstdns-controller:1.4.0@sha256:…"` for own images;
  - `ref = "…:<tag>@sha256:…"` for third-party images;
  - `[deploy.registry] url = "ghcr.io"`, so ciu's S7.9 credential preflight runs on the target.
- **[D] Drift tests:**
  - `test_image_pins_match_cmru_versions.py`: every third-party `[images.*].ref` tag equals
    `versions/oci-images.json`. This is the D-673 pin lint, now with a version authority.
  - `test_bake_targets_match_images_table.py`: every own image key is a bake target, and the reverse.
- **[D]** It also removes D-363's shortcoming: a worktree-local instance tag becomes an instance-layer override of
  `[images.<svc>].ref`.
- **[F]** CIU-117 auto-suffixes project-built refs in linked worktrees. P2 must prove that suffixing still applies
  when the ref comes from a global table (oracle in P2).

### 3.2 Bundle contents

The bundle is a ciu root:
- `ciu.global.defaults.toml.j2`, plus the generated release `ciu.global.toml.j2` (pins only);
- the deployable stacks from section 1.1, with their hooks;
- `infra/db-init/init-scripts`, `conf/`, and exactly the `scripts/` files those hooks import;
- `wheels/` (ciu and its closure, ddcli and its closure);
- `docs/` (operator subset), `CHANGES.md` and `manifest.json`.

**[D]** The allowlist lives in `release/bundle.toml`. Tests, `nyxloom-trove`, dev stacks, `.ciu/`, rendered files,
secrets and `vol-*` are refused by `cmru bundle`'s own exclusions plus the allowlist.

**[D]** The proof of completeness is mechanical: `ciu check` from the extracted bundle in an empty directory (build
and push steps), then the <host> rehearsal (section 3.4).

### 3.3 Resolver, cockpit install and the stable channel

- `cmru resolve dstdns --format env` gives the newest published `<V>` (latest.json, S5).
- **[D] Production never deploys "latest".** It deploys `rigor/index.json` `stable`, which only a promotion commit
  sets (section 3.4 step 4).
- **[D] Cockpit install:**
  1. Run `get.py install --version <V>` on the dev cockpit (and on the <host> cockpit for staging).
  2. It verifies the bundle's sha256 and manifest, installs the wheels into a private venv (`ciu`, `ddcli`
     launchers), and makes `<root>/stack` the active release.
  3. `get.py` keeps the previous release for `rollback` (S6).
- **[F]** The repository is private. `get.py` needs a token on the cockpit, but target hosts never touch GitHub
  releases.

### 3.4 Hosts and the promotion pipeline

| Host | Role | What runs there |
|---|---|---|
| dev cockpit (this host, shares a production game server) | build and release | `cmru release`; the synchronous release gate (serial, `--cpus 3`, PSI-checked) |
| **<host>** (`<host>`, Debian 13, 4 vCPU / 7.75 GiB, btrfs CoW, ciu remote verified) | **staging: live qualification** | A checkout of tag `dstdns-v<V>` in its cockpit. The release pins are written to the instance layer (`scripts/release/pins.py apply --manifest`), then `ciu clean --yes`, then `ciu up --deploy --healthcheck` (pull only, no bake), then the released runner. Lanes: `release`, `component-integration`, `vertical-corpus-e2e`, `fault-injection`, `scale-admission-live`, `p194-io-fault-live`, `seeded-distribution`, `p138-analytics-live`, the stack-health suite, and `ui` (advisory under D-393) |
| **<host>** (`<host>`, same class) | **staging: deploy rehearsal; R2 host** | 1. A fresh install from the bundle through the production path (`ciu up --host <host>` from the cockpit's `<root>/stack`) plus stack health. 2. An in-place upgrade rehearsal from the current `stable` when it is the same major. 3. The async R2 campaign (section 4.4). |
| production (<private-host> / <private-host> / …) | runs `stable` only | Unchanged procedure: `ciu up --host <h> --profile …` from the `stable` bundle. **Held until Q2.** |
| `<protected-host>` | **never** (operator: protected) | none |

- **[F]** `docs/DEPLOYMENT-GUIDE.md` §5 still lists `<private-host>` (= `<host>`, per the archived SPEC F §5) as
  the "observability and shared-services anchor". Since its 2026-11-04 reinstall it is a disposable full-power test
  host, and <host> is missing. P8 corrects the table.
- **Promotion:**
  1. A release is published.
  2. <host> qualification is green.
  3. The <host> rehearsal is green.
  4. A promotion commit sets `stable = "<V>"` and the `staging` and `promotion` blocks of `rigor/<tag>.json`.
- **[D]** A release that fails staging stays published, is never promoted, and is superseded by a fix release.

### 3.5 Pinning, rollback and upgrade

- **Pinning:** every deployed ref is `tag@digest` from the bundle (section 3.1). A host never builds.
- **[D] Same-major upgrade or rollback:**
  1. `get.py install --version <target>`, or `get.py rollback`.
  2. `ciu up --host <h> --deploy --healthcheck` from `<root>/stack`.
- **[D] Across a major:** a fresh landscape on that host (`ciu clean --yes`, then `up`).
  - In dev and staging this is AGENTS §4.1a.
  - On production it destroys data. **Q1 decides** whether that is acceptable or whether releases must first gain
    forward migrations. Until then, no production deploy happens.
- **[D] Per-host facts live in each target's own `ciu.global.instance.toml.j2`** in `bundle_dir`, kept by
  `bundle_excludes`. These facts are `landscape_id`, the public FQDN and the governance slice names.
  - The committed dev `ciu.global.toml.j2` carries this host's governance values (`dev-background.slice`,
    `/dev/vda`) and must not reach production, so it is not in the bundle.
  - P8 proves on <host> that rsync keeps the instance file. If it does not, that is a ciu item.

### 3.6 Registry credentials and retention

- **[F]** GHCR package visibility mirrors the private repository (`cmru/ghcr.py`), so every target needs a
  `read:packages` login.
- **[D]** That credential is a ciu host-scoped `ASK_EXTERNAL` secret (`ciu secrets host`, S14.3a), and
  `[deploy.registry] url` makes ciu verify it before deploy (S7.9).
- **Retention hazard [F]:** see decision 8.
  - **[D]** dstdns keeps `ghcr_packages = []`: no GHCR cleanup of its own until cmru can keep versions that a
    `stable` or deployed release references.
  - **[D]** The vbpub fix (V1) must land before the first dstdns image push.

---

## 4. Gate design

### 4.1 Lane to tier mapping

| Tier | When | Lanes (from `run-gate.toml` / `assay.toml`) | Blocks |
|---|---|---|---|
| **R0** | release gate (sync) | the composite `gate` members `schema`, `run-gate-docs`, `test-runner`, `assay` (mock R0), `assay-dlq`, `frontend-unit`, `ui_unit` (JS, changed lines vs the previous tag) | release |
| **R1** | release gate (sync) | new sequence **`release-r1`**: all **39 whole-target R0+R1 assay lanes** **[F]**. That includes the 10 orphans no gate runs today: `redirect_chain`, `schema_substrate`, `data_access`, `provider_resolution`, `network_type`, `geodns_comparison`, `cdn_detection`, `host_label_extraction`, `auth`, `progress_reporter`. It also includes the five schema-runtime R1 lanes. The 8 request-base (changed-lines) R1 lanes stay package-scoped. `frontend-coverage-direct` is excluded while RG-45 makes it flaky (named in the waiver file). | release |
| **R3** | release gate (sync) | new assay lane **`release_r3`**: the mock-suite argv, `rigor = ["R0","R3"]`, `[judge.canary] mechanism = "import-break"` on `libs/common/src/common/appconfig/loader.py` (every service and the mock suite import it; P6 proves this with one deliberate run), the cmru S16 pattern. **[F]** `nyxloom gate verify`, which the RIGOR policy names, is not a verb in nyxloom 0.12.0 (`nyxloom gate` is an invalid choice). The policy's "never R3 in a lane" sentence changes to "R3 runs per release via `release_r3`". | release |
| review | before prepare | a release-scope review (diff since the previous tag), bound to the candidate's source commit by `preflight.py` | release |
| artifact checks | build and push steps | image labels, users and import smoke; `ciu check` on the staged and extracted bundle | publish |
| **live (R0, live)** | staging, post-publish | <host> qualification and the <host> rehearsal (section 3.4) | promotion |
| **R2** | async, after release, **latest release only** | all whole-target R2 lanes changed since the watermark, on `test-runner-r2` (ephemeral, cgprofile observer, CoW, `--jobs 3`); `sql-mutation` (`cw2b_schema`) stays `jobs_max = 1` (D-377) | nothing; survivors spawn fix releases |

**[D] Release sequence lane:**

```toml
[lanes.release-gate]
kind = "sequence"
lanes = ["gate", "release-r1", "release_r3"]
stop_on = "FAIL"
```

A config test asserts that `release-r1` lists every whole-target R1 assay lane except the waived ones. This makes a
new R1 lane join the release automatically and kills the orphan class.

**[D] Waivers** (`release/rigor-waivers.toml`):
- Each entry has `lane`, `reason`, `plan_item` and `expires_before = "<version>"`.
- The cmru KI-79 window pattern applies: an expired waiver refuses in prepare.
- A waived lane is still run, and its result is recorded. Only a waived lane may fail.
- The rigor record lists it, so main shows the gap.
- **[F]** First candidates: `worker_io_dns_operations` (R1 FAIL, 1040/1218 lines) and any orphan that is red on
  its first run.

### 4.2 `scripts/release/gate.py` (the `run-tests` step)

1. Run the MAJOR guard (section 2.5) and refuse before anything starts.
2. Check PSI and `run-gate admission show`, then wait or refuse. The host is shared with production.
3. Build the candidate's runner with `ciu bake test-runner`. CIU-117 scopes the tag to this linked worktree.
4. Start the stack: `ciu up --profile core --profile db --profile runner --deploy --healthcheck` in the candidate (GUIDE §3.4 shape).
5. Run `run-gate release-gate --worktree . --base <previous dstdns-v tag commit>`, with the root commit for the
   first release. Read each verdict in a separate step and run `assay verify` on each verdict file.
6. Write `release-evidence/gate.json` (lanes, exits, verdict sha256s, tool versions, config hashes). This is the
   seed of the rigor record.
7. In `finally`, run `ciu clean --yes` and then confirm zero leftover containers with the instance prefix. The exit
   code is the gate verdict.

**[E] Wall time:**
- `gate` is 18-28 min (history 1103-1706 s).
- `release-r1` is 39 lanes run serially, about 1.5-3 h, and is not measured as a set.
- `release_r3` is about 2x the mock suite, roughly 20 min.
- Bring-up and teardown add 5-10 min.
- Total: **about 2.5-4 h per release**, and per-release work only.

### 4.3 Live qualification (staging)

These lanes are the product-level R0 for images and for deployment. They must run against released digests, so they
cannot run before publish.

- **[D]** The `fresh-landscape` lane stays the post-merge check on main (D-673). Its guard refuses non-main trees, so
  it cannot run in the cmru candidate anyway.
- The <host> qualification is the release-time from-scratch proof with released images.

### 4.4 Async R2: latest release only, survivors lead to a fix release

- **Trigger:** after promotion, or after publish if staging is still running, the controller runs
  `scripts/release/r2_campaign.py` on <host>. It:
  1. checks out the tag;
  2. pulls the released `dstdns-test-runner:<V>@digest` and retags it as the runner image;
  3. runs every whole-target R2 lane whose targets changed since `nyxloom-trove/r2-watermark` with `--jobs 3`,
     `confirm_kills` (assay 12, B204/B205/B208) and a CoW state root;
  4. closes out with `assay analyze campaign` (RIGOR "Closeout").
- **[D] Latest only:**
  - A campaign that started finishes on its tag. Its resume state, keyed by source bytes, carries unchanged kills
    to the next tag.
  - The next campaign always starts on the newest release.
  - Releases that never got a campaign are recorded `R2: superseded`.
- **[D] The watermark becomes a release tag.** It advances only on a fully green campaign (RIGOR "Async R2").
- **Survivors:**
  1. Triage each survivor as equivalent (recorded with its reason) or real.
  2. For a real one, find the root cause: a missing assertion, or a product bug found by the assertion gap.
  3. Run a MAIN-GREEN-style fix package (D-604).
  4. Its `fix:` commit makes the next release a patch.
  5. Cut that patch release immediately (the fast fix release); it reruns R2 on the latest tag.
  6. The original record gets `survivors: n, fixed_in: dstdns-v<V.patch>`.
- **[F] Coverage today is partial:**
  - Only `inf_events_obs_r2` sits on `test-runner-r2` (tools-adoption branch).
  - 66 mock-only R2 lanes need moving.
  - 13 service-needing lanes wait for RG-102/CIU-141 (ciu v8).
  - The record states R2 per component, so "partial" is visible on main.
- **[E] Time:** the 58 h serial budget ceiling becomes about 20-25 h at `--jobs 3`, which is one <host> overnight
  window per release cadence. The real candidate counts are still unmeasured (`assay plan` in P9).

---

## 5. Rigor-evidence record

**Purpose.** For any release, and for `main` at any moment, a reader (human or tool) can see:
- which rigor levels completed;
- on which commit;
- with which tool versions and gate configuration;
- what is waived or pending.

Nobody has to reconstruct this from logs or a 10-run-capped `.run-gate/history.json`.

**[D] Owner:** a generic cmru verb, because the same record is wanted for every vbpub tool (cmru's own
gate-provisional markers, cgprofile's "not R2-qualified" prose, the KI-79 waivers):

- `cmru rigor record <project> --tag <tag> --level R0|R1|R3|R2|staging|promotion --from <evidence-json>` writes or updates `<project-dir>/rigor/<tag>.json` and regenerates `rigor/index.json`.
- `cmru rigor show [<project>] [--json]` prints the current guarantees.
- `cmru rigor check` runs offline in tests. It checks: the schema validates; every local `<prefix>*` tag has a
  record; the index matches the newest tag; no record claims a level whose verdict hash it lacks.
- `rigor/` joins the release-control files that S12.2 change detection ignores, so recording never makes a project
  "changed".
- R0/R1/R3 cannot be inside the tagged commit, because the gate runs after it. cmru therefore writes the record as a
  release asset (`rigor.json`, bound to the tag) and appends it as a commit to the promote push. Later levels (R2,
  staging, promotion) are ordinary `chore(rigor):` commits on main made by the same verb.

**Record shape** (`urn:cmru:rigor-record:1`):

```json
{
  "schema": "urn:cmru:rigor-record:1",
  "project": "dstdns",
  "release": {"tag": "dstdns-v1.4.0", "version": "1.4.0", "commit": "<40-hex>",
              "published_at": "2026-11-20T10:12:00Z",
              "artifacts": {"images.json": "sha256:…", "dstdns-v1.4.0.tar.xz": "sha256:…"}},
  "tools": {"cmru": "7.3.0", "run-gate": "25.0.1", "ciu": "7.20.0", "cgprofile_daemon": "2.0.0",
            "assay": {"version": "12.0.0", "pyz_sha256": "…", "verdict_schema": "urn:assay:schema:verdict:18"},
            "test_runner_image": "ghcr.io/volkb79-2/dstdns-test-runner:1.4.0@sha256:…"},
  "config": {"run-gate.toml": "sha256:…", "assay.toml": "sha256:…", "pytest.ini": "sha256:…",
             "release/rigor-waivers.toml": "sha256:…"},
  "levels": {
    "R0": {"status": "pass", "completed_at": "…", "lanes": [{"lane": "gate", "exit": 0, "verdicts": {"mock": "sha256:…"}}]},
    "R1": {"status": "pass-with-waivers",
           "components": {"controller": "pass", "worker-io": "waived", "libs/common": "pass"},
           "waived": [{"lane": "worker_io_dns_operations", "plan_item": "…", "expires_before": "1.6.0"}]},
    "R3": {"status": "pass", "canaries": [{"lane": "release_r3", "mechanism": "import-break", "target": "libs/common/src/common/appconfig/loader.py"}]},
    "R2": {"status": "pending | running | pass | survivors | superseded | partial",
           "host": "<host>", "base_watermark": "dstdns-v1.3.0", "lanes_run": 0, "lanes_not_runnable": 13,
           "survivors": 0, "fixed_in": null, "evidence_asset": null}
  },
  "review": {"path": "nyxloom-trove/reviews/release/dstdns-v1.3.0..<sha>.md", "verdict": "ACCEPT"},
  "staging": {"<host>": {"status": "pass", "lanes": {"release": 0, "component-integration": 0, "ui": "advisory-fail"}},
              "<host>": {"fresh": "pass", "upgrade_from": "1.3.2", "upgrade": "pass"}},
  "promotion": {"stable": true, "at": "…", "commit": "<sha>"}
}
```

**`rigor/index.json`** holds `latest`, `stable` and, per release, a one-line summary (highest completed level per
component and the R2 status). That is what "main shows the guarantees" means: one file, diffable, test-checked.

**Evidence bytes are never committed.** Verdicts go into release assets (`rigor.json` and the later
`…-r2-evidence.tar.xz`), and the record holds their sha256 values.

**[D] Interim:** dstdns does not hand-roll a parallel format. Until V4 ships, `gate.py` writes the same JSON shape to
`release-evidence/gate.json`, which is retained by cmru `evidence_paths`, and the controller copies it into `rigor/`
with an ordinary commit. V4 then takes over unchanged files.

---

## 6. Migration plan

### 6.1 Packages (dstdns side; each one worktree, Sonnet implementer, blind review, run-gate gate)

| Id | Scope | Depends on | Size | Oracle / gate |
|---|---|---|---|---|
| **P0** | Merge `tools-adoption-20261105` (assay 11, run-gate 25 / ciu 7.20 floors, `test-runner-r2`). Already built, needs review and the composite gate from its own runner | none | S | `run-gate gate` green in the worktree's runner; post-merge gate |
| **P1** | Replace `cmru.toml`/`cmru.sample.toml` with section 2.1/2.2 (steps may name scripts landing in P4-P6; until then `cmru release` is not run). `.gitignore` gets `cmru.secret.toml`, `dist/`, `release-evidence/`. GUIDE §4.4 commit convention. `tests/config/test_cmru_config.py` (strict load, prefix, no `ghcr_packages="*"`) | P0 | S | `cmru standards` exit 0; `cmru status` prints a plan; the config test |
| **P2** | `[images.*]` global table; every stack template reads it; own refs become `dstdns/<svc>:local`; fix the `docker-stats-exporter`/`webapp-ui-react` naming outliers; `[product].version` for `BUILD_VERSION`; extend `test_image_tags_pinned.py` | P0 | M | `ciu check`; config tests; CIU-117 suffix proven in a throwaway worktree (`ciu bake` tag names the instance); post-merge `fresh-landscape` |
| **P3** | `cmru versions`: OCI targets for every third-party image (fixes the floating `18-alpine`, `8-alpine`, skywalking `postgres:alpine`, `nginx:alpine`, `registry:2`, `webhook-listener:latest`); `applications/base/requirements.in` plus service extras; `versions init/resolve`; `PIP_CONSTRAINT` in `Dockerfile.base`, the worker-io Dockerfile and `tools/test-runner/Dockerfile`; npm lock refresh; `test_image_pins_match_cmru_versions.py`. Also deletes the dead `cmru_*` Vault policies, tokens, `auto_config` and reverse-proxy bearer fallback (A12-F20; the pull agent was rejected by D-097/D-574) | P2 | M | the pin test; `cmru versions check` exit 0; full bake; `fresh-landscape` |
| **P4** | `docker-bake.hcl` release groups, names and labels (section 2.3); `ddcli-wheel` target; ddcli dynamic version; internal versions to `0.0.0`; `scripts/release/{env,artifacts}.py` (`verify-images`, `stage-bundle`, `bundle`); `release/bundle.toml` allowlist | P2, P3 | M | `stage-bundle --check-only` then `ciu check` from an empty dir; unit tests for `env.py` (refuses a tag/commit mismatch); `cmru run dstdns --step build --dry-run` |
| **P5** | `release-r1` sequence + membership test; `release_r3` assay lane; `release-gate` sequence; `release/rigor-waivers.toml`; RIGOR-COVERAGE-POLICY R3 paragraph; first full run of all 39 R1 lanes to seed the waivers | P0 | M | every orphan executed once; `release_r3` proven to FAIL on a planted break and PASS clean; `run-gate --list` shows the three lanes |
| **P6** | `scripts/release/{preflight,gate}.py`: review binding, MAJOR guard, PSI/admission, `ciu bake`/`up`/`clean` in `finally`, `release-evidence/gate.json` | P1, P5 | M | unit tests with a fake runner; MAJOR guard red/green fixtures; one dry `cmru release dstdns --dry-run` |
| **P7** | Test-runner and seeded-targets push (part of `release-all`); `scripts/release/pins.py apply` (manifest to instance layer) for staging checkouts | P4 | S | `pins.py` unit tests; `ciu check` with applied pins |
| **P8** | Deploy path: `[project.installer]` (section 2.2); `get.py` render; host rows for <host>/<host> (gitignored, documented); GHCR read credential as an `ASK_EXTERNAL` host secret; per-host instance file kept by `bundle_excludes`; DEPLOYMENT-GUIDE rewritten (release deploy, stable channel, rollback, the host table corrected); decide `scripts/bootstrap.py` (remove its installer role; it survives only if tailscale `prepare` still calls it) | P4, P7, ciu fixes for CIU-168..171 | M | <host>: `get.py install`, `ciu up --host <host>` from `<root>/stack`, stack health, `get.py rollback`, `ciu clean --yes` (no leftover by exact name) |
| **P9** | `scripts/release/r2_campaign.py` + watermark-as-tag; move the 66 mock-only R2 lanes to `test-runner-r2` in batches of about 15 (P9a-d); delete the 5 measured zero-candidate lanes (RIGOR rule); `assay plan` counts recorded in COVERAGE-MAP | P0, run-gate 25.0.1 (RG-154), assay 12 | M x4 | per batch: each lane starts once on <host>; `inf_events_obs_r2 --jobs 3` matches the serial pilot buckets (29 killed / 3 survived) |
| **P10** | Signed releases: minisign key (operator custody), the push step signs `manifest.json`, `manifest_pubkey` pinned into `get.py` | P8 | S | `get.py` refuses an unsigned or tampered bundle on <host> |
| **P11** | **First release** `dstdns-v1.0.0` (controller): release review, then `cmru release dstdns --set-version 1.0.0`, interim rigor record, <host> qualification, <host> rehearsal, promotion commit, then the first R2 campaign | P1-P9 (P10 before any production deploy) | n/a | the rigor record shows R0/R1/R3 pass (with named waivers), staging pass, `stable = 1.0.0` |
| **P12** | Adopt `cmru rigor` (replace the interim copy step) | V4 | S | `cmru rigor check` in `tests/config` |

**Parallelism:**
- P1, P2 and P5 can start together after P0.
- P3 and then P4 follow P2.
- P6 needs P1 and P5. P8 needs the ciu fixes.
- The agent cap stays at D-636 (2).

### 6.2 vbpub-side items (file in each tool's backlog first; none is filed by this memo)

| Id | Tool | Item | Blocks |
|---|---|---|---|
| **V1** | cmru | `cleanup` with `ghcr_packages = ["*"]` selects every package of the OWNER, not of the configured repository (`cli.py:1400-1425`, `:1537`), then deletes versions by age regardless of tags. Fix: scope `*` to packages whose `repository.full_name` is the configured repo, and never delete a version carrying a tag in `keep_release_tags`-like keep rules. **Until it ships, vbpub's root config must list its packages explicitly.** | the first dstdns image push |
| V2 | cmru | `project.version.exclude_paths` (project-relative) so that a repository-root project can ignore `docs/`, `nyxloom-trove/` and `rigor/` in change detection | nothing (quality of `cmru status`) |
| V3 | cmru | S15 cross-repository providers (`provider = {github = "volkb79-2/vbpub", prefix = "assay-v"}`), so dstdns's vendored assay pyz and bundled ciu wheel get the integrity, authenticity and freshness checks | nothing (today `test_assay_pin_integrity.py` and `.assay-inbox` cover it) |
| **V4** | cmru | `cmru rigor record/show/check` + schema `urn:cmru:rigor-record:1` (section 5); estate tools adopt it too | P12 |
| V5 | ciu | Land the CIU-168..171 remote-verb fixes (branch `ops-<host>-20261104`) in a release | P8 |
| V6 | nyxloom | RIGOR and TESTING-METHODOLOGY name `nyxloom gate verify` for R3, but the verb does not exist in 0.12.0. Either ship it or rewrite the doctrine to "assay canary lanes" | doc truth only |

### 6.3 Risks

- **Release-gate load on the shared production host.** It runs 2.5-4 h with 7 infra containers and the runner.
  - Mitigation: serial lanes, `--cpus 3`, PSI checks, no other gates (memory `host-shared-with-production-load-rule`).
  - Fallback: run the release transaction from the <host> cockpit with a `gh` login there.
- **Long instance names.** The candidate worktree basename `cmru-release-<timestamp>-dstdns-<id>` becomes the ciu
  container prefix. P6 must check that no container hostname exceeds 63 characters (the vbpub SPEC-V8 D.5 length note).
- **First R1 run.** 10 orphan R1 lanes have never run in a gate, so expect some reds. They become waivers with
  expiry versions, not silent exclusions.
- **Bundle completeness.** Hooks may import repo modules outside the allowlist. `ciu check` from an empty directory
  plus the <host> rehearsal catch it.
- **Majors are frequent** under greenfield. This is acceptable in staging and needs Q1 for production.
- **R2 is partial until ciu v8** (13 service-needing lanes). The record shows it per component and never as a pass.
- **cmru release mechanics** (memory 2026-11-04): `cmru release` refuses an editable install and needs a
  wheel-installed cmru with a recorded `direct_url`. A remote BuildKit cannot see local base images, but dstdns
  bases are in-bake contexts (`target:base-app`), so that does not apply.

### 6.4 Operator actions (no decision needed, only hands)

- `gh auth refresh -s read:packages,write:packages` for the release account on the cockpit.
- A fine-grained `read:packages` token per target host (stored with `ciu secrets host`).
- A minisign key pair (P10); the private key stays with the operator.

### 6.5 Open questions for the operator

1. **Production data across a MAJOR.**
   - **Option a:** the §4.1a "landscape is disposable" rule extends to production, and a major release means a
     fresh database on production hosts.
   - **Option b:** releases must gain forward migrations before the first production deploy, and a migration tool
     becomes a new work item.

   This sets what MAJOR means operationally. Until it is answered, no production deploy happens.
2. **Production host set and timing.** DEPLOYMENT-GUIDE §5 lists <private-host>, <private-host> (=<host>, now a disposable test
   host), <private-host> and <protected-host> (protected, never touched). Which hosts run dstdns `stable`, and is there a
   production deploy before the auth-always-on edge (D-674) lands?
3. **GHCR visibility.** dstdns images default to private, mirroring the repository, so every host needs a read
   token. Confirm that, or choose public images. That is a product-exposure call, not a mechanism.

Everything else in this memo is decided above and recorded as D-records in the merge of each package.
