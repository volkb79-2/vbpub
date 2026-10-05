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
verb is `cmru version`. Help from `cmru`, `cmru-agent`, and `cmru-controller`,
including nested verbs, begins with the generated CMRU identity. CMRU
configuration diagnostics put that identity on line 1; shared
`cli-extended` usage/refusal diagnostics put the actionable error first and
then show relevant generated help, which begins with the same identity. Use
`--help` or `cmru help <verb>`; CMRU's shared grammar deliberately does not add
a separate short `-h` spelling.
The documented `python3 -m cmru.handlers` calls are explicit project-step
library adapters rather than a separately versioned operator entrypoint, so
they are outside this top-level identity surface.

---

## Git version for local tag inspection

Release and cleanup workflows that inspect local tags require Git 2.43 or newer. Before using
those verbs, check the Git executable in the environment that runs CMRU:

```bash
git --version
# Require git version 2.43.0 or later.
```

An older Git cannot distinguish a missing local tag from a failed ref lookup. For a real release
that includes tagged projects, CMRU checks support before creating its candidate or starting a
project gate. If selected projects span independent repositories, CMRU reads each selected
project's tag policy from its fetched `origin/main` snapshot and checks all tagged repositories
before starting any family release; each family launcher fetches again and refuses if `origin/main`
moved, ensuring it uses the checked commit. If that happens, rerun the release so the full set of
families is preflighted again. Cleanup checks support while preparing the preview, before any
planned action is applied. See the
[design rationale](DESIGN-GUIDE.md#local-tag-inspection-requires-git-243).

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

The `cmru get-py` command uses the template bundled inside the installed wheel, so an
adopter can render an installer from any working directory after installing CMRU:

```sh
cmru get-py example-wheel --config /path/to/cmru.orchestration.toml --output ./get.py
```

The wheel also installs `cmru-agent` and `cmru-controller`; those are independent companion
CLIs with their own registered verbs. `cmru --help` lists top-level CMRU commands, while
`cmru help get-py` or `cmru get-py --help` prints the exact delegated grammar.

## Using the wheel and component interfaces

Install the approved CMRU wheel into the Python environment that will execute
your project steps. The wheel installs the three console scripts and includes
the CMRU modules, registered `cli-extended` grammar, and shared `worktree`
library. This lets a consumer use CMRU functionality without copying its
implementation into the project:

```sh
python3 -m venv .venv-cmru
.venv-cmru/bin/python -m pip install /path/to/approved-cmru-wheel.whl
.venv-cmru/bin/cmru --version
.venv-cmru/bin/cmru --help
```

Use installed console scripts for operator workflows. `python -m cmru.handlers`
is the supported component CLI because project contracts and the first-wheel
bootstrap need it. `cmru.bundle` and `cmru.runner` are library modules; they do
not expose module commands. The `cmru.cli`, `cmru.agent.cli`, and
`cmru.controller.cli` module aliases are retired. Use `cmru run-step` for
direct single-step CLI work, and use the documented Python functions to compose
bundle or runner behavior:

```sh
# Preview one configured project step without running it.
.venv-cmru/bin/cmru run-step --config ./cmru.toml --step build --dry-run

# Show the inputs to a declared wheel handler without launching its build.
.venv-cmru/bin/python -m cmru.handlers wheel-build --cwd . --dry-run
```

`cmru run-step` reads the same project configuration and step declaration used
by CMRU orchestration; it does not define a second step format. The bundle
library reads its dedicated bundle TOML. Its loader rejects unknown keys and
wrong TOML value types at each table boundary; see S9.4a in the spec.

```python
from pathlib import Path

from cmru.bundle import run_bundle
from cmru.runner import run_step

# These calls execute configured work. run_step may remove declared clean
# directories and runs the step commands; run_bundle removes dist_dir first.
# Use cmru run-step --dry-run when you need to inspect project step effects.
run_step(Path("cmru.toml"), "build")
archive = run_bundle(Path("bundle.toml"))
```

Prefer the declared project-step commands or these documented entrypoints over
copying CMRU implementation code. Do not import private helpers as an API. For
operator commands, use the installed `cmru`, `cmru-agent`, or `cmru-controller`
script. The bundle module is a library, and the runner module's supported CLI
is `cmru run-step`.

A real `wheel-build` handler invocation requires a Git worktree and a configured
`CMRU_WHEEL_BUILDER_IMAGE`; the dry-run example only displays accepted inputs.
See the wheel-build contract in [the CMRU spec](SPEC.md).
The bundle contract guarantees normalized deterministic output for `xztar`;
other supported archive formats use the platform archive writer.

The CMRU wheel also bundles `worktree`, the shared stable API for generic Git
workspace identity, records, leases, and lifecycle operations. It is an
internal dependency embedded in the CMRU wheel, not a separately released
package. Projects that need these primitives can use them without parsing Git
porcelain or rebuilding generic checkout lifecycle logic; CMRU itself retains
release and transaction policy. Follow the separate
[worktree consumer guide](../../libraries/worktree/CONSUMERS.md) for its
pasteable examples and complete contract.

A project command that invokes `cmru` uses the runtime launcher created for the
transaction. CMRU prepends it to the project command's `PATH`, protects the
binding from project environment overrides, and verifies that it reports the
same CMRU version as the transaction before launching the command. A mismatched
runtime fails before the project command starts; see KI-11 in the
[known-issues backlog](../KNOWN_ISSUES_TODO_BACKLOG.md).

## Running configured steps and previewing cleanup

`cmru run` uses the selected project's configured `default_steps` when no step
flags are supplied. In the example above that includes `push`, so preview the
resolved plan before running the default set:

```sh
cmru run example-wheel --dry-run
cmru run example-wheel --build --dry-run
```

The first command previews configured defaults; the second previews only the
explicit `build` step. `--dry-run` prints selected projects, command argv and
working directories, declared cleanup, and unresolved dynamic-environment
helpers without starting project commands. To apply the configured defaults,
run `cmru run example-wheel` after reviewing the preview.

Every `cmru cleanup` mode also displays its pending actions and asks before it
changes local or remote state. `--yes` accepts the plan shown by that same
invocation. Use `--dry-run` to inspect actions without applying them:

```sh
cmru cleanup --remove-assets 30d --dry-run
cmru cleanup --remove-assets 30d --yes
cmru cleanup example-wheel --delete-build-output <commit-date>_<commit> --dry-run
cmru cleanup example-wheel --delete-build-output <commit-date>_<commit> --yes
```

The preview freezes the action list. After confirmation CMRU applies the captured release IDs,
package IDs, tags, and local record/worktree identities; it does not discover new targets. For
each Git tag it also captures the exact local and remote object IDs. A tag created after the
preview or moved to another object is left for a later preview. For age-based cleanup, the cutoff
is computed once, so an asset that becomes old while the prompt is open remains for a later
preview.

If configured cleanup runs `steps.clean`, CMRU commits only paths that become dirty during that
step. Files already dirty when the confirmed plan starts are left out of that commit so cleanup
does not sweep caller edits into its generated commit. It commits generated files even when the
cleanup policy selected no release tags for deletion.

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

A release timestamp exactly at the age cutoff is eligible; a timestamp later
than the cutoff is too new. This boundary rule also applies to literal rolling
OCI tags.

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
| `CMRU_TESTER_CPUS` | finite decimal CPU ceiling of at least `0.00001`; smaller values would remove Docker's per-container bound |
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
The CPU setting must be a finite decimal of at least `0.00001`; CMRU passes it through Docker's
`--cpus` option and checks the value before privileged host slice/IO probes. It does not also
set `--cpu-period`, because Docker rejects those two CPU controls together. Smaller positive
values, zero, non-finite values, and values Docker cannot represent are refused instead of
being rounded to an absent CPU limit.
`--memory`, `--memory-swap`, and `--cpus` bound the tester workload. With `--enable-docker`,
the DinD sidecar is also placed under `CMRU_TESTER_CGROUP_PARENT`, but currently has no separate
per-container CPU or memory cap; the policy decision is open in the canonical CLI audit.

### Reproducing a gate step by hand

When a release goes red, you copy the failing step's `argv` and run it directly. Export the
orchestration env block first — the preflight will tell you the complete list in one shot if you
forget, but it is faster to set it before the first try:

```sh
export CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local \
       CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
       CMRU_TESTER_CGROUP_PROBE_IMAGE=debian:trixie-slim \
       CMRU_TESTER_CGROUP_PARENT="${CGROUP_PARENT_DEV_GATES:?CGROUP_PARENT_DEV_GATES is required}"
# then run the step's argv
```

To inspect the constructed command without launching containers, add `--dry-run` to the
copied `tester-gate` argv. The preview also shows the DinD startup command when that option
is enabled. It skips the privileged host slice and IO checks, so a dry-run is not evidence
that the host will accept the configured cgroup parent or device limits; the actual run
performs those checks before starting the gate. Every probe, sidecar, and gate container is
placed under `CMRU_TESTER_CGROUP_PARENT`.

### The CMRU R0-R3 gate

For the vbpub CMRU checkout, use the project entrypoint rather than a cockpit
pytest command:

```sh
./run-gate.py --list
./run-gate.py gate
```

The `assay` lane installs Assay from the selected worktree, snapshots the
repository with the three declared Topos fixture omissions, and judges R0
(full tests), R1 (100% line and branch coverage), and R3 (import-break
canary). It omits native Assay R2 because a post-merge release candidate is
already at `origin/main`, so the configured `main` base would produce no
mutation candidates. The `gate` lane supplies R2 through its separate
changed-source mutation campaign, based on the highest-version published
`cmru-v*` tag in the candidate's full ancestry;
the registered `gate` lane queries every CMRU release tag on origin through
CMRU's credential-scoped Git transport, then passes token-free tag and commit
facts to the `cmru-mutation` tester environment as
`CMRU_ASSAY_BASELINE_FACTS`.
The mutation lane checks those facts against its HEAD and requires the selected
tag to match the configured Assay R1 base. On an untagged candidate, that
ancestor must also be the latest published CMRU release. On a tagged-HEAD
rerun, every release tag at HEAD is excluded and the selected ancestor remains
the highest-version published release in the remaining ancestry; the latest
published tag may be one of the verified tags at HEAD. It
verifies every local CMRU release tag at HEAD against its exact origin commit,
including older tag names that point to the same commit. It checks Assay's
effective comparison commit: if Assay resolves a merge's first
parent after the tag, CMRU source roots must be unchanged across
that gap. A missing tag or mismatch fails the gate. Run the registered
`./run-gate.py gate` lane to prepare fresh origin facts immediately before
mutation. When that source diff is empty,
it records a skipped result bound to the candidate HEAD instead of claiming
mutants ran. If rerun at a release-tagged HEAD, the checker excludes every
local CMRU release tag at HEAD and selects the highest-version published tag
from its full ancestry, matching the
pinned R1 baseline. The next release candidate advances its R1 pin to the
newly tagged release. The just-tagged commit retains its preceding pin, so
rerunning its gate uses the same baseline. The serial R2 campaign caps each
candidate at 120 seconds and stops after its first failed test (`--maxfail=1`). Its mutation
outcomes are in `.assay/mutation-cmru.json`; the append-only
`.assay/progress-mutation-cmru.jsonl` stream reports progress. Resume reuses killed outcomes only
when the tests and copied fixture closure exactly match the recorded run. Any test or fixture
change requires a new campaign. Passing full-suite runs still execute every test. Every Assay
invocation resumes and writes `.assay/progress-cmru.jsonl`; its verdict is
`.assay/verdict-cmru.json`.
The disposable controls include the Topos and nyxloom CMRU manifests consumed
by CMRU's estate-adoption test, so the full suite remains runnable in each
baseline and mutant copy.
`gate` also runs CMRU's total-coverage, cause-sensitive canary, and
real-enrollment evidence lanes. It starts with the KI-26 installed-wheel
acceptance lane, which builds the wheel, installs it into a fresh isolated venv,
and invokes `cmru get-py` outside the source checkout.
The registered real-enrollment lane requires Docker, the configured host-probe image, a loaded
fragment-backed gates cgroup slice on the Docker host, and a successful fixture-image build; missing
prerequisites or a failed build are lane failures, not skips. CMRU's privileged systemd probe
checks the daemon host. Standalone local test runs may skip that container oracle when prerequisites
are unavailable.

The host `gate` lane keeps CMRU publisher credentials out of tester-unified:
it points visible root/project `cmru.secret.toml` overlays at private host
backups outside the repository mount for the registered lane sequence, strips
publisher-token and extra-mount variables from nested runner calls, and
restores the files when the gate exits, including on SIGTERM or SIGHUP. If restoration
fails, the gate reports and retains the private backup path for recovery. If a credential is
atomically replaced during the gate, CMRU preserves the replacement and fails rather than
overwriting it with saved bytes. The host uses CMRU's scoped Git auth to prepare origin facts for
the mutation lane; those facts contain tag names and commit IDs, not credentials. See the
[gate credential design](DESIGN-GUIDE.md#keeping-release-credentials-out-of-gate-containers).
Use the aggregate `gate` lane while a CMRU secret overlay is available in the
mounted repository. Running an individual tester component lane bypasses this
host wrapper and is suitable for secret-free checkouts.

---

## 3. The release flow, and what an isolated transaction is

```
cmru status                       # what would release, and at what version bump
cmru release <name>     # one source-first transaction: gate → tag → build → publish → promote
cmru release                      # changed projects, one transaction per Git family (S-CLI.5a)
```

For an HTTPS GitHub `origin`, keep the repository credential in the ignored CMRU-root secret
file:

```toml
# <cmru-root>/cmru.secret.toml
schema_version = 1

[github]
token = "replace-with-your-GitHub-token"
```

Do not put `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, or `CMRU_GIT_AUTH_TOKEN` in project,
orchestration, or step `[env]` tables. CMRU reserves these names so declared values cannot
replace the credential selected for publication and Git transport. Set a token in the invoking
environment or in the ignored secret file above.

When `origin` matches the configured `[github] owner/repo`, CMRU uses the resolved repository
token (this root value unless an invocation environment variable overrides it) for its own Git
fetches and pushes. Publisher API calls use the resolved project token, which falls back to this
root token unless that project has an override. CMRU does not put the token in the URL,
arguments, or persistent Git config. Project-local publisher overrides are not passed to CMRU's
Git operations. For credential-bearing Git calls, CMRU disables local Git hooks because they
inherit the token-bearing process environment; the registered release gates remain the release
checks. Local hook-capable operations such as commit, revert, and rebase still run configured
hooks, with `GITHUB_PUSH_PAT`, `GITHUB_TOKEN`, and `CMRU_GIT_AUTH_TOKEN` removed from the Git
child environment. SSH and other-host remotes keep their configured Git authentication. If no
repository token resolves, Git's configured helpers and SSH authentication remain in effect.
The local ancestry probes used by `cmru abandon` also remove publisher token variables from
the Git child environment. Secret files
written before `schema_version` was introduced remain readable. See the [transport
rationale](DESIGN-GUIDE.md#git-transport-authentication).

CMRU resolves the selected project's release config from the transaction's
isolated source snapshot. A central orchestration file inside that snapshot
already points to snapshot paths; an external central file maps its registered
project paths from the source Git root. If an in-repository orchestration change
moved a project config since your caller checkout, CMRU reads its policy and
places the copied project secret overlay at the path recorded in the snapshot.
Project config symlinks may point within the Git family; the resolved target
must keep the `cmru.toml` filename accepted by the config loader. CMRU preserves
the selected repository link path when it checks the snapshot and when it
starts the transaction child, so a caller checkout still pointing at an older
symlink target cannot override the committed target. A selected `--config` link
may use another basename when its tracked target is named `cmru.toml` or
`cmru.orchestration.toml`; the target name determines the config kind.

`cmru release` never publishes from your working tree (`S-CLI.5`). It fetches `origin/main`,
refuses local-only `main` commits the snapshot would omit, and creates a temporary worktree at
that exact remote commit, named (`S-CLI.5b`, KI-16, ciu-aligned):

```
.worktrees/cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>
       e.g. .worktrees/cmru-release-20260819_143022-assay-abc123
```

Before selecting changed projects, CMRU must successfully read their Git history. If a tag,
pathspec, or repository state makes `git log` fail, the release plan stops with Git's diagnostic
instead of treating the project as unchanged.

Flat, chronologically sortable, and **the branch name is byte-for-byte the directory name** —
the same 1:1 scheme ciu uses. A successful release removes the worktree; a **failure retains
it** for diagnosis and prints its exact path. The origin candidate branch is also retained. CMRU
records the allocator's canonical identity input so the visible six-character token and the
structured workspace context remain the same fact across resume and cleanup.
publishes from the exact gated candidate SHA and only then promotes it to `origin/main`; if a
concurrent update rejects that final promotion, CMRU merges `origin/main` into the candidate when
the project's own paths are untouched (bounded retries), otherwise it stops. It never rebases the
candidate or creates a source revert. A build failure after the tag push rolls the tag back (the
candidate stays resumable); once publishing began the tag is kept. Inspect the retained candidate and resolve the external publication explicitly
before abandoning it. List and clean retained ones:

```
cmru worktrees                                   # every retained failed build/release worktree
cmru cleanup --discard-build-worktree <PATH> --yes
cmru abandon --dry-run                           # show all retained release candidates, no writes
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --dry-run
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --yes
cmru abandon cmru-release-20260924_120000-example-a1b2c3 \
  --config /path/to/cmru.orchestration.toml
```

For a tagged project, the exact release tag must be on origin before CMRU invokes its build or
publisher. A failed push makes CMRU recheck origin. If origin contains the exact candidate tag, CMRU continues;
if origin confirms the tag is absent, CMRU removes that exact local tag, stops before build or
publish, and retains a pre-tag candidate that can be resumed with its printed `resume:` command.
If origin cannot be checked, CMRU retains the tag and candidate for inspection. If origin has
the same tag name at a different object, CMRU also retains the local tag and candidate for
inspection. CMRU does not automatically retry a post-tag publication step; `--resume` is for
retained pre-tag candidates. When CMRU proves that a failed tag push left no origin ref and
removes the local tag, the saved resume command can retry that release; CMRU permits a freshly
generated annotated tag object for the same tag name in that case.
On resume, CMRU checks tag-push attempts and recorded release results against origin. A tag that
may have been pushed without a completed result, or a recorded result whose source commit is not
in `origin/main`, causes a refusal and keeps the candidate for inspection. A release-plan refusal
during resume also keeps the existing worktree and origin backup branch.

For a script, use `cmru worktrees --json`. `prunable: true` reports Git's
worktree-registration marker; it does not prove the checkout directory is
absent or visible in the current filesystem namespace. The `source_commit`
field preserves Git's reported HEAD even for prunable entries. CMRU withholds
resume/discard commands for those entries; consumers should likewise validate
the exact checkout before acting on it. Release records also include
`project_scope_state` and `project_scope`; build records omit them. The state is
one of `recorded`, `missing`, or `unreadable`. Use the exact project-name array
only when the state is `recorded`; the scope is `null` for `missing` and
`unreadable`, and automation must not construct a resume command for either
state. These fields are read from the shared Git sidecar without probing the
listed worktree path.

### Resuming a retained release

Run `cmru worktrees` to see the candidate's recorded project scope and
scope-safe `resume:` command. If the failed release used an external config,
pass that same `--config PATH` again; CMRU does not record its path. You can also
run `cmru release --resume PATH`; CMRU reads the saved scope. If you provide a
project target, it must match the saved scope exactly. A legacy candidate with
no scope record requires you to inspect the candidate and pass its explicit
target, with the same external config if one was used. If the legacy checkout has
no shared CMRU ownership record, CMRU adopts it only when Git identifies it as a
registered worktree in the source repository and its release-progress sidecar is
valid, with its progress commit an ancestor of the candidate. Without that
evidence CMRU refuses resume; inspect the retained checkout before starting a
fresh release. A candidate whose scope cannot be read has no safe resume command
until that metadata can be inspected.
Before resuming, keep copied root and project `cmru.secret.toml` paths in the
candidate as regular files, not symlinks. CMRU refuses unsafe credential paths
instead of following them outside the candidate; inspect and repair the path
before retrying. CMRU reads `project.release.git_tag` from the committed retained
candidate before starting its child, so a candidate policy change receives the
same Git-version preflight as a new release.

For JSON automation, a recorded release looks like this (the commit is a full
Git object ID in actual output):

```json
{
  "branch": "cmru-release-20261001_120000-cmru-abcdef",
  "path": "/repo/.worktrees/cmru-release-20261001_120000-cmru-abcdef",
  "prunable": false,
  "project_scope": ["cmru"],
  "project_scope_state": "recorded",
  "purpose": "release",
  "source_commit": "0123456789abcdef0123456789abcdef01234567"
}
```

The closed `project_scope_state` values are `recorded`, `missing`, and
`unreadable`. Treat only `recorded` with `prunable: false` as eligible to resume;
for that state, pass every name from `project_scope` as the explicit target.
Repeat the original external `--config PATH` when applicable. The field names
and closed state values are part of the supported JSON interface.

`cmru abandon` addresses retained **release** transactions. With no branch it displays the
complete retained release set; an optional branch selects one exact managed branch name.
It reports each transaction's scope, worktree, and known origin candidate/tag references,
then asks once before changing anything. `--yes` confirms that complete displayed set.
`--dry-run` does not remove a local or remote ref, worktree, or sidecar. CMRU refuses to
abandon if release results, a released tag, origin promotion, an untagged publisher, or
missing/stale metadata makes publication state uncertain. A refusal lists known refs and
release coordinates when present. It also refuses if a successful origin branch lookup omits
`origin/main` or returns an invalid object ID, because promotion status cannot then be determined.
Abandonment removes the candidate checkout (including its in-worktree logs and
artifacts), candidate branch, and transaction sidecars. The main source
history and `origin/main` are not rewritten. If an external orchestration file selected a
multi-project release, pass that same file with `--config` so CMRU can evaluate every project
in the recorded scope.

New candidates carry an exact origin tag snapshot from before release work began. For new
candidates, CMRU compares the full origin tag-ref inventory for release
prefixes in the recorded scope, so an added, removed, or retargeted scoped ref blocks abandonment
even if it is not reachable from the candidate. A tag from another project's prefix does not
block recovery. CMRU removes a local tag only when its
transaction sidecar records an attempt to push that exact tag object and the origin snapshot proves
the tag is new. An unexplained local-only tag in the selected scope blocks abandonment. Inspect
the tag and, only if it is an unneeded local ref absent from origin, remove it with Git before
retrying:

```bash
cmru worktrees
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --dry-run
TAG=example-v1.2.3
git show "$TAG"
git tag -d "$TAG"
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --dry-run
```

Older candidates have no tag snapshot; if a scoped remote tag is reachable from one, CMRU cannot
classify it and withholds cleanup. Review the candidate and publication coordinates before
deciding whether to resume or retain it.

If you intend to complete that release, use the exact `resume:` command shown by
`cmru worktrees` after confirming the candidate and publication state.
After confirmation, CMRU rechecks the inspected branch and tag refs. It deletes the origin
candidate with a lease against the captured object ID and removes local state only after verifying
that deletion.

`cmru cleanup` is separate. It applies remote cleanup policy to GitHub Release records and
their assets, matching Git tags, and GHCR package versions. It does not abandon a local
release transaction or delete the `cmru-release-*` candidate branch. For a confirmed cleanup
plan, CMRU re-fetches each captured Release ID and skips that record if its tag, update time,
asset inventory, or displayed eligibility changed. It also re-fetches each GHCR version ID and
skips it if its update time or container tag set changed, or if it no longer meets the age
policy. A whole-package deletion is included only when the package exists in the preview; before
the name-addressed GitHub deletion request, CMRU re-fetches the package and skips it if its ID
changed. A GitHub 404 for a package or version is ambiguous: it can mean absent or inaccessible.
CMRU skips the action and reports that it cannot verify cleanup; check that the configured
credential can read the package before treating it as absent. With `ghcr_packages = ["*"]`, the
GitHub listing selects only packages visible to that credential. When a Release deletion is
skipped, CMRU leaves its matching Git tag too. If CMRU reports malformed output while listing
origin tags, check `git ls-remote --tags origin` for tab-separated object-ID/ref records matching
the configured prefix, then run a fresh cleanup preview; CMRU stops before applying a plan from an
incomplete listing. A confirmed
`steps.clean` runs after the planned deletions and receives the highest-semver
Release that survived them; the preview's `CMRU_VERSION` is an estimate. Run a new preview to
review changed records. See the [cleanup design](DESIGN-GUIDE.md#remote-cleanup-and-local-transaction-abandonment)
for the remaining API race boundary.

When the central CMRU root registers projects from independent Git repositories, the same
selection is dispatched as one transaction per Git family. Each repository therefore gets its
own lock, candidate branch, workspace identity, and promotion result; cross-repository release
is coordinated in order but is not one atomic Git commit.

`cmru build X` runs prepare/gate/build in a retained `cmru-build-…` worktree
and records its outputs with a source identity and SHA-256 inventory. To publish
those exact retained bytes, use the ID printed by `cmru build`:

```sh
cmru build example-wheel
cmru publish example-wheel --build-output <ID-from-build-result>
```

The build worktree must have a clean recorded source tree after prepare and
build. Put expected untracked generated outputs such as `dist/` in the project's
`.gitignore`; do not ignore source paths to hide edits. Ignore rules do not hide
changes to tracked files, so a build that modifies tracked files remains
ineligible. CMRU keeps a dirty build record for inspection but refuses to
publish it, including with `--dry-run`.

Before pushing, CMRU verifies the retained manifest and the exact set, size, and
digest of every file. Built-in wheel and tarball handlers consume those
inventoried files. They require the existing versioned GitHub Release and tag;
for a stable version, the tag must resolve to the retained source commit. They
also require the existing `-latest` Release and tag, which are updated in place
without deleting or recreating the tag. CMRU creates no Git refs or Release
records. CMRU captures each Release ID and tag commit, then checks those exact
identities before changing Release metadata and before each asset delete or
upload. GitHub has no atomic conditional-update API, so a remote change after a
successful check can race the next request. After staging each selected file,
the built-in handler verifies its size and digest against `build.json` before
the first GitHub request; a changed copy stops publication. For a stable retained build, CMRU leaves `-latest` pointing at a newer
version instead of moving it backward; it checks the highest version again after
uploading the retained versioned assets. A custom `push` step must publish files under
`CMRU_BUILD_OUTPUT_ROOT`, compare any staged copy with `build.json` before upload,
must not rebuild from the caller checkout, and must not create or move Git refs
or promote a source branch. Generated checksum and
latest-pointer files use temporary copies so the retained build record stays
valid for another publish. `--dry-run` checks local evidence and renders the
step but does not query remote tag availability. Use `cmru release` for the
source-first tag/build/publish/promote workflow.

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

- **Project CMRU commands bind to the active runtime** (KI-11). When a project step invokes
  `cmru`, CMRU places a transaction-owned launcher first in `PATH`, protects that binding from
  project overrides, and verifies the invoked runtime identity before running the command. A
  failed or mismatched identity stops the step before its command starts.

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
- **Durable post-tag publish resume** does not exist: `--resume` can continue a retained
  *pre-tag* worktree only after corrections are committed there; prepare and the required gate
  rerun, and the corrected candidate commit is what ships. It is not a post-tag retry (KI-06).
- **`release --from-candidate`** (create and promote a Git source release from a separately
  retained candidate) is not the artifact-publishing interface. Use `cmru publish --build-output
  ID` to send retained local build bytes to existing release/tag targets without creating or
  moving Git refs or promoting a branch.

---

## See also

- [`../README.md`](../README.md) — the model, verbs, and templates (WHAT).
- [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md) — why contextual roots, central facts, selection, and native release logging work this way.
- [`SPEC.md`](SPEC.md) — the normative contract (WHY); start at *S-CLI*, *S-REL*, *S2*.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — running cmru against the estate during development.
- [`../../docs/ciu-vs-cmru.md`](../../docs/ciu-vs-cmru.md) — which tool owns an artifact.
