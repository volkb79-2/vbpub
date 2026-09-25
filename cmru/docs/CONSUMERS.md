# Consuming cmru — making your product releasable

This is the **HOW** for adopting cmru. The **WHAT** is [`../README.md`](../README.md); the
normative **WHY** is [`SPEC.md`](SPEC.md) (the `S-…` clause IDs cited below). If you are not
sure cmru is even the right tool for an artifact, read the border question first:
[`../../docs/ciu-vs-cmru.md`](../../docs/ciu-vs-cmru.md) — *is this artifact published for
external consumption? yes → cmru; no → ciu.*

cmru versions, tags, builds, and publishes **independently-versioned products that share one
GitHub Releases page**. You make a product releasable by giving it a portable `cmru.toml` and
one entry in the nearest CMRU root's `cmru.orchestration.toml`. cmru owns the generic source mechanics
(isolated worktrees, generated history, tags); your project owns every release *phase* command
explicitly.

Before selecting a release verb, a consumer can inspect the installed build
with this pasteable probe:

    cmru --version

It prints one `cmru <version>` line on stdout and exits 0. The equivalent native
verb is `cmru version`. Help, usage, and configuration diagnostics from
`cmru`, `cmru-agent`, and `cmru-controller`, including nested verbs, begin with
their CMRU headline as line 1; normal command output is unchanged.
The documented `python3 -m cmru.handlers` calls are explicit project-step
library adapters rather than a separately versioned operator entrypoint, so
they are outside this top-level identity surface.

---

## 1. The two files

A product is releasable when two files exist. Nothing is auto-discovered beyond them.

**`<project>/cmru.toml`** — travels with the product; its complete release/build/test/publish
contract. In an orchestrated estate, repository and registry facts live in the central file
below. Secrets never live here. Minimal wheel example (see
[`../../cmru.project.sample.toml`](../../cmru.project.sample.toml) for the annotated original):

```toml
schema_version = 1

[runtime]
kind = "none" # use "ciu" only when the project owns declared CIU roots

[project]
id = "example-wheel"
description = "Example wheel project"
prefix = "example-wheel-v"        # the tag prefix cmru owns; SemVer follows it
artifacts = ["wheel"]             # an output INVENTORY, not a behaviour switch
template_revision = 4

[project.version]
strategy = "scm"
bump = "conventional"             # version derived from Conventional Commits since the last tag

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]

[versions]
age_window_days = 21 # project policy for its local copy of pypi.requests

[versions.discovery]
scope = "shipped" # this is the default; set to "all" to include test/build groups
pypi_extras = []   # list only Python extras this product ships

[versions.targets."pypi.requests"]
mode = "single"
constraint = ">=2.31.0,<3.0.0"

[versions.targets."pypi.requests".pypi]
name = "requests"
registry = "https://pypi.org"

# Optional: age-check a non-SemVer image tag and record its registry digest.
[versions.targets."oci.node"]
mode = "single"
constraint = "*"

[versions.targets."oci.node".oci]
image = "docker.io/library/node"
tag = "26-slim"
selection = "rolling"

# Go module target example for a project with a Go helper or submodule.
[versions.targets."go.golang.org.x.text"]
mode = "single"
constraint = ">=0.20.0,<1.0.0"

[versions.targets."go.golang.org.x.text".go]
module = "golang.org/x/text"
proxy = "https://proxy.golang.org"

# Add only outputs the release gate actually writes; these are retained separately
# from publishable artifacts and bound to the gated commit.
# evidence_paths = ["coverage.json"]

# Every phase is project-owned and explicit. The gate always runs through tester-unified.
[steps.run-tests]
quiet = true
commands = [
  { label = "gate", argv = ["cmru", "tester-gate", "--cwd", ".", "--", "/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"], cwd = "." },
]

[steps.build]
quiet = true
commands = [
  { label = "build wheel", argv = ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."], cwd = "." },
]

[steps.push]
quiet = true
commands = [
  { label = "publish wheel", argv = ["python3", "-m", "cmru.handlers", "wheel-publish", "--prefix", "example-wheel", "--cwd", ".", "--notes-env", "EXAMPLE_RELEASE_NOTES"], cwd = "." },
]
```

The runtime declaration is mandatory and closed. Paste `kind = "none"` for a
self-contained project step; use `kind = "ciu"` when the step deliberately
uses CIU's isolated workspace/runtime adapter. CMRU passes
`CMRU_WORKSPACE_ID`, `CMRU_WORKSPACE_PATH`, and `CMRU_SOURCE_GIT_ROOT` to the
step, so its evidence can name the candidate that produced it. It never
guesses from a `docker` command.

**`<cmru-root>/cmru.orchestration.toml`** — central coordination; project commands remain in
project files. The nearest file found while walking ancestors establishes the CMRU root and may
serve several repositories below it. Each entry points at a portable project-local
`cmru.toml` (see [`../../cmru.orchestration.sample.toml`](../../cmru.orchestration.sample.toml)):

```toml
schema_version = 1

[github]
owner = "your-github-owner"
repo = "your-repository"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order    = ["example-wheel"]
default_projects = ["example-wheel"]
default_steps    = ["run-tests", "build", "push"]
execution_mode   = "project-first"

[orchestration.project.example-wheel]
config = "example-wheel/cmru.toml"
depends_on = []

[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = ["example-wheel-latest"]
ghcr_packages = ["*"]
ghcr_delete_packages = []

[versions]
age_window_days = 14

[versions.discovery]
scope = "shipped"

[versions.targets."pypi.requests"]
mode = "single"
constraint = ">=2.31.0,<3.0.0"

[versions.targets."pypi.requests".pypi]
name = "requests"
registry = "https://pypi.org"
```

The two snippets above are a complete loadable pair: save the project snippet as
`example-wheel/cmru.toml` and the central snippet as `cmru.orchestration.toml`, then run
`cmru standards`. For a standalone project that has no central file, use the annotated
[`cmru.project.sample.toml`](../../cmru.project.sample.toml), which keeps its own
`[github]` and `[targets]` facts.

The project example also declares a Go module target. Its project needs a module file such as:

```go
module example.com/example-wheel

go 1.22
```

The Go resolver reads `.info` commit times from the module proxy and warns that this timestamp
is the upstream VCS commit time rather than the proxy publication time. The warning also appears
when this evidence causes an age-window refusal. Rolling OCI checks ignore
Docker attestation descriptors marked `vnd.docker.reference.type = "attestation-manifest"`,
then require usable age evidence for every runnable platform in the image index.

`cmru.toml` is one grammar for every verb (`S-CLI`/`S2`, KI-03/KI-05). Unknown fields, a
committed `[github].token`, retired central `[projects]`/`[registry]` tables, or an omitted
required field are **rejected with exit 2**, not ignored. Validate before you rely on anything:

```
cmru standards        # conformance of every declared project's contract
```

## Using a supply-chain age window

The pair above declares one shared `pypi.requests` target with a 14-day root age window and a
project-local copy with a 21-day window. Shared target state is written to
`cmru.orchestration.toml`; the project's resolved state is written to its `cmru.toml`. The
project-local window applies because that project redeclares the target. A project-level
`age_window_days` by itself does not change shared root targets.

For `cmru versions init`, package names and ranges come from the project's declared discovery
scope. `scope = "shipped"` is the default: it includes `requirements.in`, Python
`project.dependencies`, the Python extras listed in the project's `pypi_extras`, npm
`dependencies`/`optionalDependencies`/`peerDependencies`, and Go `go.mod` requirements. Add
project-relative `.in` or `.txt` paths to `requirements_files` when a shipped install uses a
different requirements manifest. Project-specific manifest paths and extra lists belong in the
project's `cmru.toml`; the root may set only the shared `scope` default.

To include test and build declarations too, set `scope = "all"` in that project's
`[versions.discovery]`. This also reads all Python optional extras and PEP 735 dependency groups,
`build-system.requires`, conventional root `requirements*.in`/`requirements*.txt` files and
files below `requirements/`, and npm `devDependencies`. Go module requirements are included in
both scopes because `go.mod` does not distinguish runtime and test-only modules reliably. `all`
means all supported declarations in these manifests; it does not cover apt packages, GitHub
release assets, dynamic installer scripts, or image tags that cannot be represented by CMRU's
version grammar. Unsupported syntax is still reported by `init`.

For example, `example-wheel/requirements.in` can contain:

```requirements
requests>=2.31.0,<3.0.0
```

Then preview and apply the explicit refresh:

```bash
cmru versions init all --dry-run
cmru versions init all
cmru versions resolve all --dry-run
cmru versions resolve all
cmru versions check all --json
```

`init` derives targets from the selected requirements files, `pyproject.toml` dependencies,
`package.json` dependency tables, and `go.mod` require entries. Go entries marked `// indirect`
are included because they are explicit module graph requirements too. CMRU reports manifest
entries whose syntax, shape, or source cannot be resolved (including Python environment markers)
instead of silently treating them as managed. OCI image targets are declared explicitly; by
default an OCI `tag` template contains exactly one `{version}` placeholder and keeps any suffix
or prefix literal (for example, `v{version}-alpine`). `selection = "semver"` is the default.
For rolling or codename tags, use `selection = "rolling"` with the literal tag. Rolling selection requires `constraint = "*"` and
a single OCI source; CMRU records the registry manifest digest and reports a refresh when that
digest changes. SemVer selection remains the default.

Source tables use the closed family values `.pypi`, `.npm`, `.go`, and `.oci`. PyPI/npm tables
use `name` and `registry`; Go tables use `module` and `proxy`; OCI tables use a fully-qualified
`image` and `tag` template. Use `mode = "single"` for one source or `mode = "aligned"` for a
version shared by multiple sources. The constraint is a comma-separated SemVer-compatible range
(`>=`, `>`, `<=`, `<`, `==`, `!=`, `^`, `~`, `~=`, or `*`). Exact `version` overrides require a
`reason`; an override newer than the age cutoff also requires a future `expires` date.

For a private source, add either `token_env` or the pair `username_env` and `password_env` to that
source table. These values name environment variables; the credentials themselves stay outside
the config. Registry and Go proxy URLs must use HTTPS and must not include credentials, a query, or a
fragment. CMRU exits 3 when a named variable is unset or empty. `cmru versions init` uses the
public default registries and does not infer private credentials.

Release timestamp evidence depends on the source. PyPI uses release-file upload time; npm uses
the registry's per-version time. Go uses module proxy `.info` `Time`, which represents the VCS
commit time rather than proxy publication time; CMRU prints a warning whenever it uses this
evidence. OCI uses registry HTTP `Last-Modified` when available; otherwise it uses the
publisher-supplied `org.opencontainers.image.created` manifest annotation or image-config
`created` time. CMRU warns when it uses this fallback and records the chosen timestamp and its
`age_source` with each result so the source of age evidence is reviewable. If a fresh fallback
timestamp causes an age-window refusal, that refusal includes the same warning. An image-created
time is not registry publication time. Missing or malformed timestamps fail closed.

Registry clients follow HTTPS redirects, including redirects to signed blob storage. They retain
`Authorization` only when the redirect stays on the same HTTPS origin (same scheme, host, and
port). CMRU strips it when the origin changes and refuses HTTP downgrades or redirect URLs with
embedded credentials or fragments. If a redirected host requires credentials, configure that
host's canonical HTTPS endpoint in the source table.

`resolve` writes a dated `constraints/constraints-YYYYMMDD.txt` plus the stable
`constraints/constraints.txt` for PyPI targets in Python projects; set `PIP_CONSTRAINT` or pass
`-c` to consume it. npm targets update direct package versions, overrides, and the lockfile with
scripts disabled. When an override or looser per-target cutoff needs a package-specific age
exception, npm 11.5.0 or newer is required. Go targets update `go.mod`/`go.sum`; in active Go
workspace mode, the Go tool may also update `go.work`/`go.work.sum`. CMRU snapshots those files
before `go get` and restores them if a later writer fails. For pseudo-versions, CMRU checks a
version named by the target constraint and uses the proxy's `@latest` fallback when no listed
version matches. If the proxy reports different commit times for the same version through `.info`
and `@latest`, CMRU refuses the result. OCI targets
write a dated and stable JSON record at `versions/oci-images-YYYYMMDD.json` and
`versions/oci-images.json`; each record has
`schema_version`, `generated_by`, `resolved_at`, and a `targets` mapping containing image, chosen
version/tag, timestamp, evidence source, and override reason. Existing built-in constraints and
OCI outputs are overwritten only when they carry CMRU's generated marker.

Optional Jinja2 outputs let a project render another format from the same resolution. Each
`versions.outputs.<id>` table has `template`, `path`, and `dated_path`; both output paths are
relative to the project, and `dated_path` contains `{date}`. The template receives `resolved_at`
and a `targets` mapping with each selected version, override/reason/expiry, owner, cutoff, and
per-source version, timestamp, evidence source, and OCI tag. These explicitly configured files
are replaced on resolve. Install CMRU with the `versions-templates` extra to use this feature.

For Python targets, CMRU runs `uv pip compile` with the selected direct packages pinned and the
target cutoff supplied as `--exclude-newer`; project-specific cutoffs use uv's per-package age
option. Consume these committed constraints as native inputs rather than editing them by hand.

Resolved state and native artifacts do not change during `cmru build`, `cmru release`, or a gate.
Review and commit the `resolve` diff as an ordinary source change before relying on the update.

---

## 2. The gate environment — the one thing that trips up first-timers

Your `tester-gate` step needs container/resource inputs that are **not usually set in your
project's own `cmru.toml [env]`**. The estate supplies them once, for every project, in
`cmru.orchestration.toml` under **`[orchestration.defaults.env]`** (a project's own `[env]`
overrides a value only where it has a genuinely different requirement). The required set:

| variable | meaning |
|---|---|
| `CMRU_TESTER_UNIFIED_IMAGE` | the tester-unified gate container image |
| `CMRU_TESTER_MEMORY` | gate container memory ceiling (no default — refuses unbounded) |
| `CMRU_TESTER_MEMORY_SWAP` | combined mem+swap total (Docker semantics) |
| `CMRU_TESTER_CPUS` | gate container CPU ceiling |
| `CMRU_TESTER_CGROUP_PROBE_IMAGE` | host-systemd slice probe image |
| `CMRU_TESTER_CGROUP_PARENT` | required host gates slice (`${CGROUP_PARENT_DEV_GATES}`) |
| `CMRU_TESTER_DIND_IMAGE` | **only** with `--enable-docker` (nested Docker daemon) |
| `CMRU_WHEEL_BUILDER_IMAGE` | required by `wheel-build` |

These reach the step through `cmru release`. **`cmru standards` checks this exact set** against
your declared config statically, and at runtime `tester-gate` validates the same set up front
(SPEC `S2.6a`, KI-17): if anything is missing it aborts **once, naming every missing variable
together**, before any container spins up — and the message names
`cmru.orchestration.toml`, not your project's `cmru.toml`. (In terse messages the estate writes
this env block as "`[env]`" for short; the literal table in the orchestration file is
`[orchestration.defaults.env]`.)

### Reproducing a gate step by hand

When a release goes red, you copy the failing step's `argv` and run it directly. Export the
orchestration env block first — the preflight will tell you the complete list in one shot if you
forget, but it is faster to set it before the first try:

```sh
export CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local \
       CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
       CMRU_TESTER_CGROUP_PROBE_IMAGE=debian:trixie-slim
# then run the step's argv
```

### The CMRU R0-R3 gate

For the vbpub CMRU checkout, use the project entrypoint rather than a cockpit
pytest command:

```sh
./run-gate.py --list
./run-gate.py assay
./run-gate.py gate
```

The `assay` lane installs Assay from the selected worktree, snapshots the
repository with the three declared Topos fixture omissions, and judges R0
(full tests), R1 (100% line and branch coverage), and R3 (import-break
canary). It omits native Assay R2 because a post-merge release candidate is
already at `origin/main`, so the configured `main` base would produce no
mutation candidates. The `gate` lane supplies R2 through its separate
changed-source mutation campaign, based on the nearest ancestor `cmru-v*` tag;
when that source diff is empty, it records a skipped result instead of claiming
mutants ran. The serial R2 campaign caps each candidate at 120 seconds, stops
after its first failed test (`--maxfail=1`), and resumes from its progress
file; passing full-suite runs still execute every test. Every Assay invocation
resumes and writes
`.assay/progress-cmru.jsonl`; its verdict is `.assay/verdict-cmru.json`.
The disposable controls include the Topos and nyxloom CMRU manifests consumed
by CMRU's estate-adoption test, so the full suite remains runnable in each
baseline and mutant copy.
`gate` also runs CMRU's total-coverage, cause-sensitive canary, and
real-enrollment evidence lanes.

---

## 3. The release flow, and what an isolated transaction is

```
cmru status                       # what would release, and at what version bump
cmru release <name>     # one source-first transaction: gate → tag → build → publish → promote
cmru release                      # changed projects, one transaction per Git family (S-CLI.5a)
```

CMRU resolves the selected project's release config from the transaction's
isolated source snapshot. A central orchestration file inside that snapshot
already points to snapshot paths; an external central file maps its registered
project paths from the source Git root.

`cmru release` never publishes from your working tree (`S-CLI.5`). It fetches `origin/main`,
refuses local-only `main` commits the snapshot would omit, and creates a temporary worktree at
that exact remote commit, named (`S-CLI.5b`, KI-16, ciu-aligned):

```
.worktrees/cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>
       e.g. .worktrees/cmru-release-20260819_143022-assay-abc123
```

Flat, chronologically sortable, and **the branch name is byte-for-byte the directory name** —
the same 1:1 scheme ciu uses. A successful release removes the worktree; a **failure retains
it** for diagnosis and prints its exact path. The origin candidate branch is also retained. CMRU
records the allocator's canonical identity input so the visible six-character token and the
structured workspace context remain the same fact across resume and cleanup.
publishes from the exact gated candidate SHA and only then fast-forwards `origin/main`; if a
concurrent update rejects that final promotion, CMRU does not rebase the candidate or create a
source revert. Inspect the retained candidate and resolve the external publication explicitly
before abandoning it. List and clean retained ones:

```
cmru worktrees                                   # every retained failed build/release worktree
cmru cleanup --discard-build-worktree <PATH> --yes
```

For a script, use `cmru worktrees --json`. `prunable: true` reports Git's
worktree-registration marker; it does not prove the checkout directory is
absent or visible in the current filesystem namespace. The `source_commit`
field preserves Git's reported HEAD even for prunable entries. CMRU withholds
resume/discard commands for those entries; consumers should likewise validate
the exact checkout before acting on it.

When the central CMRU root registers projects from independent Git repositories, the same
selection is dispatched as one transaction per Git family. Each repository therefore gets its
own lock, candidate branch, workspace identity, and promotion result; cross-repository release
is coordinated in order but is not one atomic Git commit.

`cmru build X` is the local-inspection sibling: it runs prepare/gate/build in a
retained `cmru-build-…` worktree and **never publishes** (KI-10). Do not expect
`cmru build` then `cmru publish` to ship the reviewed artifact — use `cmru release` for that.

After a release transaction, CMRU also cleans up the caller's local `main` when it can. If
that checkout is dirty, including with tracked or untracked files, ignored files, or ignored
directories, CMRU refuses the cleanup rebase before invoking either rebase command and leaves
the files and local ref untouched.
This warning does not put caller edits into the immutable remote snapshot, and
`--allow-uncommitted` does not change that boundary. From a clean caller checkout, use the
remedy printed by CMRU:

```sh
git status --short --ignored
git stash -a                 # or commit/move the caller changes
git rebase origin/main
git stash pop                # only if the stash is the desired work
```

A real rebase conflict is reported as a conflict, separately from a dirty-checkout refusal;
another clean-rebase failure is reported as undetermined rather than guessed to be a conflict.
The same diagnostic is printed after a successful release, a plan refusal, or a child failure;
the primary release result and retained-worktree behavior remain unchanged. See the normative
[`S-CLI.5a`](SPEC.md#s-cli5a--projects-release-one-after-another-not-in-a-shared-batch) contract
and the [caller-main cleanup operations](RELEASE-TRANSACTIONS.md#caller-main-cleanup).

### Retaining gate evidence

If the release gate writes commit-bound evidence, add exact files or directories to
`[project.release].evidence_paths` in the complete `cmru.toml` example above (for example,
`evidence_paths = ["coverage.json", ".assay"]`).

CMRU moves those paths into `<project>/evidence/cmru-release/<immutable-id>/` after the whole
release succeeds and writes `evidence.json` with the gated source commit and SHA-256 hashes.
The paths must be project-relative, contain no `..`, and contain no symlink component; a
missing or unsafe declared path fails retention and keeps the release worktree available for
inspection. Use `--discard-evidence-on-release` only when deliberately discarding those
outputs. `evidence_paths` is separate from `artifact_dirs`: evidence proves the gate's input
commit and is not offered to a publisher.

### Previewing an external version

For a project whose version is discovered by a prepare step, declare
`strategy = "external:VAR"` and the generated files under
`release.commit_generated`. `cmru release PROJECT --dry-run` then runs that
declared version query inside its disposable candidate and prints the resulting
release plan. The candidate is removed afterward and no gate, tag, build, push,
or promotion occurs. A prepare command must remain deterministic and may write
only its declared outputs; CMRU refuses any other write.

---

## 4. Failure modes worth knowing before your first release

- **Never hand-tag a cmru-managed project** (KI-12). cmru owns `tag` in its own pipeline, so a
  manual tag is indistinguishable from a completed release — and an unpushed hand-made tag at
  `HEAD` silently produced an *empty* release plan in the incident that filed KI-12. cmru now
  checks the plan against `origin` (the baseline tag must be pushed, under the same name, at the
  same commit) and refuses otherwise with a named remedy. Let cmru create every tag.

- **Pin the cmru you run, and prove it matches the engine** (KI-11). A project step whose `argv`
  begins `cmru …` resolves through the worker's `PATH`, which can be an *older installed wheel*
  than the source engine driving the transaction. Until KI-11 is resolved, install the last
  verified cmru wheel into the gate environment before an estate release and treat a
  source-vs-installed version mismatch as a preflight failure — do not paper over it per project.

- **GHCR package visibility is a one-time UI step** (KI-01). For an OCI product, the first push
  cannot set the package public via any API (a platform limitation); cmru logs a one-time `WARN`
  with the remediation and does **not** fail the release. Set it once in the package settings;
  it persists across all later pushes.

- **A delegated third-party tool step is not a silent option** (KI-04, `S7`). SBOM/signing/etc.
  are not yet a config feature; cmru rejects that surface rather than pretend a step ran.

---

## 5. What cmru does *not* (yet) ship

Adopt with these boundaries in mind — each is a deliberate, fail-closed gap, tracked in
[`../KNOWN_ISSUES_TODO_BACKLOG.md`](../KNOWN_ISSUES_TODO_BACKLOG.md):

- **OCI repack** is guarded off for production (`--repack` exits 2 before any side effect) until
  it proves single-build + registry-digest equivalence (KI-02, `S14`).
- **Durable post-tag publish resume** does not exist: `--resume` is for investigating a retained
  *pre-tag* worktree, not an automatic post-tag retry (KI-06).
- **`release --from-candidate`** (promote a separately-built, remotely-evidenced artifact) is
  deliberately postponed; a local `build.json` explicitly forbids publication (KI-10).

---

## See also

- [`../README.md`](../README.md) — the model, verbs, and templates (WHAT).
- [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md) — why contextual roots, central facts, selection, and native release logging work this way.
- [`SPEC.md`](SPEC.md) — the normative contract (WHY); start at *S-CLI*, *S-REL*, *S2*.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — running cmru against the estate during development.
- [`../../docs/ciu-vs-cmru.md`](../../docs/ciu-vs-cmru.md) — which tool owns an artifact.
