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
verb is `cmru version`. Help from `cmru`,
including nested verbs, begins with the generated CMRU identity. CMRU
configuration diagnostics put that identity on line 1; shared
`cli-extended` usage/refusal diagnostics put the actionable error first and
then show relevant generated help, which begins with the same identity. Use
`--help` or `cmru help <verb>`; CMRU's shared grammar deliberately does not add
a separate short `-h` spelling.
`python3 -m cmru.handlers` is a bootstrap-only library adapter (the first-wheel
build), not a separately versioned operator entrypoint, so it is outside this
top-level identity surface. Project steps use `cmru handler <verb>`.

### Building the first wheel from source

For a checkout with no published `cli-extended` or CMRU release, build both
wheels from this repository, then install them yourself (the script only builds
and prints commands; no `cmru` command exists until you run them). Run from the
repository root, with `ESTATE_PYTHON` naming the Python that will own `cmru`:

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+bootstrap.source \
  CMRU_BOOTSTRAP_CLI_EXTENDED=source ./cmru/build-initial-standalone.sh
. cmru/dist/bootstrap-artifacts.env
"${ESTATE_PYTHON:?set ESTATE_PYTHON to the Python that will own the cmru command}" \
  -m pip install --no-index --no-deps --force-reinstall \
  "$BOOTSTRAP_CLI_EXTENDED_WHEEL" "$BOOTSTRAP_CMRU_WHEEL"
export PATH="$(dirname "$ESTATE_PYTHON"):$PATH"
cmru --version
```

The bootstrap refuses, before any Docker command, when a file that goes into the wheel differs
from `HEAD`: tracked edits, untracked files, or Git-ignored files that would still be packaged,
under `cmru/pyproject.toml`, `cmru/src`, `libraries/worktree/src` and, in source mode, the
`cli-extended` library. It lists up to twenty paths; commit, stash or remove them and rerun (an
unreadable Git state also refuses). Other dirt in the checkout does not block it.

The source mode is explicit, creates a `+bootstrap.source` library wheel at
CMRU's declared dependency floor, and never activates as a fallback. The
temporary CMRU version is pinned whether or not the checkout has a CMRU tag, so
the controller can never be mistaken for a release. The script verifies
the wheel-builder image label and rebuilds its configured tag FROM the local
`tester-base` tag (itself built first, from public upstream inputs, when absent) if the image is
absent or belongs to another build. Before
any Docker/Buildx work it verifies, on the Docker host and for the exact slice it
then passes to Docker, that the build slice and the gates slice
(`CMRU_TESTER_CGROUP_PARENT`, declared from `$CGROUP_PARENT_DEV_GATES`) are real
loaded units; a typo'd, unloaded or unprobeable slice refuses and nothing is
launched. It records the exact wheel paths and digests it built in
`cmru/dist/bootstrap-artifacts.env`, which the block above sources.
The [zero-release procedure](../../docs/BOOTSTRAP-FROM-ZERO.md) covers the
tester image and project release order after this bootstrap.

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
template_revision = 5

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
  { label = "build wheel", argv = ["cmru", "handler", "wheel-build", "--cwd", "."], cwd = "." },
]

[steps.push]
quiet = true
commands = [
  { label = "publish wheel", argv = ["cmru", "handler", "wheel-publish", "--prefix", "example-wheel", "--cwd", ".", "--notes-env", "EXAMPLE_RELEASE_NOTES"], cwd = "." },
]
```

Project steps call handlers as `cmru handler <verb> ...`, never
`python3 -m cmru.handlers`: inside a release transaction `cmru` resolves to the
launcher bound to the running cmru (its own library roots), while the module form
resolves cmru from whatever interpreter and `PYTHONPATH` the step inherits.
`cmru standards` flags the module form in project steps.

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
token_source = "gh"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order    = ["example-wheel"]
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

`cmru --help` lists top-level CMRU commands, while
`cmru help get-py` or `cmru get-py --help` prints the exact delegated grammar.

### Installer extensions (project-owned `get.py` commands)

The rendered `get.py` is the generic transactional installer
(`install`/`update`/`status`/`rollback`) and nothing else. A project that needs its own
command declares it as a fragment and lists it in its `cmru.toml`:

```text
[project.installer]
extensions = ["installer/mycmd.py"]   # project-relative, .py, no "..", must exist at render time
```

`cmru get-py` inlines each fragment verbatim at the template's `# @@EXTENSIONS@@` marker, in
declared order, between `# --- extension: <path> sha256=<digest> ---` and
`# --- end extension: <path> ---` lines. The result is still ONE file, byte-identical across
renders. A fragment must:

- parse, and import only the standard library (it brings its own `import` lines);
- define top-level names that collide with neither the template nor another fragment;
- use only the template names listed in the rendered file's `EXTENSION_API` tuple
  (`EXIT_CONFIG`, `EXIT_FAIL`, `EXIT_PREREQ`, `_EXTENSIONS`, `_c`, `_current_version`,
  `_root_dir`, `do_install`, `fatal`, `hr`, `info`, `ok`, `warn`);
- register with a top-level `_EXTENSIONS.append(register)`, where
  `register(subparsers) -> {command: handler}` adds its parser(s) and `handler(args, token)`
  runs the command. A command name that duplicates a core or another extension's command exits 2.

These render-time checks (which also refuse star imports, `global`/`nonlocal` of a template
name, and module-scope rebinding of template names, and which read annotations) are a
contract/lint guard over repo-owned fragments, **not a security boundary**: `globals()`,
`getattr` and `exec` remain possible, so fragments must be reviewed like any other code.

`EXTENSION_API` is a **stability contract**: changing or removing a name in it requires
updating every in-repo fragment in the same change (today: ciu's `ciu/installer/enroll.py`).
Re-render and commit the project's `get.py` after editing a fragment; ciu's
`TestRenderedInstaller` byte-identity test guards the committed file against drift.
Host enrollment (`get.py enroll`, a root-run `authorized_keys` writer) is ciu's fragment, so a
project that renders `get.py` without `extensions` gets no enrollment code.

## Authoring an installer for your project

The rendered `get.py` is a fail-closed, transactional installer you ship as a release asset
(normative contract: SPEC S6.1-S6.17). To give your project one:

1. **Configure** `[project.installer]` in the project's `cmru.toml` (all paths are relative to
   the release root; `asset_suffix` is always `.tar.xz`):

   ```text
   [project.myproj.installer]
   install_dir_system = "/opt/myproj"        # absolute, normalised, >= 2 components (not /opt); user scope uses install_dir_user
   install_dir_user   = "myproj"
   asset_suffix       = ".tar.xz"
   entrypoint         = "scripts/adapter.py" # optional project adapter (bootstrap/apply/rollback)
   launchers          = ["myproj"]           # optional: <root>/bin/myproj -> current venv
   # manifest_pubkey  = "<56-char minisign public key>"   # optional: require signed releases

   [[project.myproj.installer.wheels]]       # optional: wheels installed offline into a venv
   path         = "vendor/myproj-*.whl"
   distribution = "myproj"
   ```

2. **Publish a bundle** per release, next to its checksum. The release assets are
   `<tag>.tar.xz` (`<tag>-<variant>.tar.xz` for variants), `<tag>.tar.xz.sha256`
   (`<64 hex>  <name>`) and `get.py`. The name is NOT yours to choose: `get.py` requests exactly
   `<tag><asset_suffix>` (for tag `myproj-v1.2.3` and `asset_suffix = ".tar.xz"`:
   `myproj-v1.2.3.tar.xz`, the same as `cmru.release.bundle_asset_name("myproj", "1.2.3",
   ".tar.xz")`), and `cmru.release.publish_versioned` publishes your file under the name you
   built it with. A project that builds its bundle with `bundle.toml` therefore writes the
   `v` in:

   ```toml
   schema_version = 1

   [archive]
   name_template = "myproj-v{version}.tar.xz"   # NOT "myproj-{version}.tar.xz"
   ```

   `tests/test_installer_asset_names.py` reads every in-repo project that declares
   `[project.installer] asset_suffix` and fails when its `bundle.toml` `name_template` or
   `tarball-publish --glob` does not produce that name (a project that publishes no bundle yet,
   ciu, is listed with its backlog id, CIU-99). A release published under another name
   (pwmcp up to `1.64.0-r2` shipped `pwmcp-<version>.tar.xz`) is NOT installable by `get.py`: it
   requests exactly `<tag><asset_suffix>`, and a 404 is a refusal that names the requested asset
   (operator decision 2026-10-21, PWMCP-08: no discovery of other asset names; such releases are
   left as they are). Consumers pin a release published under the contract name.

   The tarball has exactly ONE top-level directory that holds
   `manifest.json` (and `manifest.json.minisig` when signed), the wheels under their globbed
   paths, and the project files. The manifest is JSON with `schema_version: 1`, `tag`/`version`
   if present equal to the release tag, one `{"sha256": ..., "wheel": ..., "size": ...}` entry
   per configured wheel distribution (key = distribution name), and a `files` map
   `{relpath: {"sha256": ..., "size": ..., "mode": ...}}` that MUST cover the `entrypoint` and
   EVERY other regular file in the bundle: the installer refuses a bundle with a member that is
   not listed there (wheels are hashed through their distribution entry instead), and a
   symlink or hardlink only when its target is a listed file. A project whose release is a
   plain tarball writes that manifest with `cmru handler bundle-manifest --name NAME
   --tag TAG --root <staged top-level dir>` as the last step before `tar` (tls-edge's
   `scripts/build-artifact.sh` does); `cmru.manifest.build_manifest(..., bundle_root=DIR)`
   embeds the same `files` map for wheel-based bundles. A bad tree (symlink, special file,
   missing root) or an invalid `--tag` is a one-line `[ERROR]` and a non-zero exit (1 for the
   tree, 2 for the tag), never a traceback. Manifest `files` keys must be normalised relative
   paths: `../x`, `..`, `/abs`, `a//b`, `./a`, NUL and backslash are refused.
3. **Render and commit** `cmru get-py myproj --config cmru.toml --output get.py`.
4. **Users** run the installer. It runs as root, so lead with the verified, chained form: a
   private temp directory (no predictable `get.py` in the current directory), HTTPS only, and a
   checksum that must pass before anything runs (`&&` stops the chain on any failure). Take
   `<sha256-of-get.py>` from a source you already trust (your own release notes or controller):
   cmru does not publish a checksum for `get.py`.

   ```sh
   d=$(mktemp -d) && cd "$d" && curl -fsSLo get.py --proto '=https' https://github.com/<owner>/<repo>/releases/download/<tag>/get.py && echo "<sha256-of-get.py>  get.py" | sha256sum -c - && sudo python3 get.py install --version <tag>
   ```

   The pipe form is **unverified**: nothing checks the script before root runs it, and
   `curl -f ... | sudo python3 -` exits 0 on a 404 (empty input is an empty program), so a
   failed download looks like a successful install. Use it only for a throwaway host. Stdin
   carries the script, so a token for a private repo comes from `--github-token-file` or
   `GITHUB_TOKEN`, never `--github-token-stdin`:

   ```sh
   curl -fsSL https://github.com/<owner>/<repo>/releases/download/<tag>/get.py \
     | sudo python3 - install --version <tag>
   ```

   Then `python3 get.py update [--version TAG]`, `status` and `rollback [--version TAG]`.
   `--version` installs exactly that tag; without it the highest release is resolved. Before the
   first update after a migration from the old layout there is no rollback target (`rollback`
   says so, exit 1).
5. **Security properties.** Fail-closed on any verification error (exit 1, nothing changed);
   SHA-256 sidecar checked before extraction; HTTPS only with an allowlisted host on every
   redirect hop and the token never forwarded across a redirect; wheels installed offline with
   `--require-hashes` into a per-release venv; a crash never leaves a half-built release live.
   Crash recovery: each transaction starts by deleting leftover `.incomplete` release
   directories, EXCEPT on a pre-W1 (legacy-layout) install, where that prune is skipped and the
   old releases survive until the new release is live (a failed migration leaves the legacy
   previous release in place).
   **Signing is optional but absolute once enabled:** with `manifest_pubkey` the installer needs
   `minisign`, verifies `manifest.json.minisig` with the pinned key, and requires the signed
   trusted comment to be exactly

   ```text
   project=<name> tag=<tag> manifest_sha256=<sha256 of manifest.json>
   ```

   (replay protection: it binds the signature to this tag and these manifest bytes). Sign with
   `cmru.manifest.build_trusted_comment(project=, tag=, manifest_path=)` and
   `cmru.delegated.minisign_sign(...)` from a project-owned release step; `cmru release` does not
   sign automatically yet (SPEC S6.15).

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
is the bootstrap-only component CLI (the first-wheel build runs before an
installed `cmru` exists); project contracts use `cmru handler <verb>` instead.
`cmru.bundle` and `cmru.runner` are library modules; they do not expose module
commands. The `cmru.cli` module alias is retired. Use `cmru run --step NAME` for
direct single-step CLI work, and use the documented Python functions to compose
bundle or runner behavior:

```sh
# Preview one configured project step without running it.
.venv-cmru/bin/cmru run --config ./cmru.toml --step build --dry-run

# Show the inputs to a declared wheel handler without launching its build.
.venv-cmru/bin/python -m cmru.handlers wheel-build --cwd . --dry-run
```

`cmru run --step` reads the same project configuration and step declaration used
by CMRU orchestration; it does not define a second step format. The bundle
library reads its dedicated bundle TOML. Its loader rejects unknown keys and
wrong TOML value types at each table boundary; see S9.4a in the spec.

```python
from pathlib import Path

from cmru.bundle import run_bundle
from cmru.runner import run_step

# These calls execute configured work. run_step may remove declared clean
# directories and runs the step commands; run_bundle removes dist_dir first.
# Use cmru run --step NAME --dry-run when you need to inspect project step effects.
run_step(Path("cmru.toml"), "build")
archive = run_bundle(Path("bundle.toml"))
```

Prefer the declared project-step commands or these documented entrypoints over
copying CMRU implementation code. Do not import private helpers as an API. For
operator commands, use the installed `cmru` script. The bundle module is a library, and the runner module's supported CLI
is `cmru run --step NAME`.

A real `wheel-build` handler invocation requires a Git worktree and a configured
`CMRU_WHEEL_BUILDER_IMAGE`. It reads an explicit environment value first, then derives
the value from the nearest enclosing orchestration config or standalone project config;
it refuses if neither source declares one. The dry-run example only displays accepted
inputs. See the wheel-build contract in [the CMRU spec](SPEC.md) and the
[configuration resolution design](DESIGN-GUIDE.md#direct-wheel-build-uses-the-declared-image).
Before it launches the builder, `wheel-build` verifies on the Docker host that its slice
(`CMRU_DOCKER_CGROUP_PARENT`, else `$CGROUP_PARENT_DEV_BACKGROUND`) is an installed unit. It
needs `CMRU_TESTER_CGROUP_PROBE_IMAGE` (digest-pinned, present locally) and
`CMRU_TESTER_CGROUP_PARENT`, which a registered project already inherits from
`cmru.orchestration.toml`:

```sh
export CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice CGROUP_PARENT_DEV_GATES=dev-gates.slice
cmru handler wheel-build --cwd run-gate-project   # a typo'd slice name exits 3 and starts nothing
```

`cmru publish --from-checkout` additionally requires the local `<prefix>-v<version>` tag to
match origin's tag (`git ls-remote` agrees with `git rev-parse <tag>^{commit}`); after an
accidental `git tag -f`, run `git tag -d <tag> && git fetch --tags --force origin`.
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
cmru run example-wheel --step build --dry-run
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
| `CMRU_TESTER_PIDS_LIMIT` | positive integer `--pids-limit` for the gate container (no default; without it the ceiling is the host's systemd `DefaultTasksMax`, shared with production) |
| `CMRU_TESTER_CGROUP_PROBE_IMAGE` | host-systemd slice probe image; runs privileged with host PID, so it must be digest-pinned (`repo@sha256:<64 hex>`) and present locally (`--pull=never`) |
| `CMRU_TESTER_CGROUP_PARENT` | required host gates slice (`${CGROUP_PARENT_DEV_GATES}`) |
| `CMRU_TESTER_DIND_IMAGE` | **only** with `--enable-docker` (nested Docker daemon); privileged, so digest-pinned and local like the probe image |
| `CMRU_TESTER_DIND_MEMORY`, `CMRU_TESTER_DIND_CPUS`, `CMRU_TESTER_DIND_PIDS_LIMIT` | **only** with `--enable-docker`: the sidecar's own memory / CPU / pids limits |
| `CMRU_WHEEL_BUILDER_IMAGE` | resolved by `wheel-build` from an explicit environment value or the selected CMRU config |

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
`--memory`, `--memory-swap`, `--cpus` and `--pids-limit` bound the tester workload. With
`--enable-docker`, the DinD sidecar is also placed under `CMRU_TESTER_CGROUP_PARENT` and has its
own required `--dind-memory`/`--dind-cpus`/`--dind-pids-limit` (decided 2026-10-05).

The gate workload and the DinD sidecar run under `--init`, every container has an exact name
(`cmru-tester-<uuid8>`, `cmru-probe-<uuid8>`, `cmru-tester-dind-<12 hex>`), and SIGTERM/SIGHUP
stop and remove them by that name. After your command exits, cmru reads the container's own
`pids.events` and `memory.events` (copied by an in-container wrapper to
`.cmru/tester-gate-events-<uuid>.txt` in your worktree, removed afterwards): a missing file, a
non-zero `pids.events max` or a non-zero `memory.events oom_kill` makes the step exit **3**
(infrastructure failure, naming the counter) even if your command exited 0. A malformed
events file is the same failure. Otherwise your command's own exit status is returned unchanged.
`.cmru/` is git-ignored in vbpub; add it to your project's ignore file if the gate runs against a
worktree you also diff.

Two consequences to know about:
- **`oom_kill > 0` anywhere in the gate's cgroup fails the step with exit 3, even when the
  command exited 0.** That includes tests that provoke an OOM kill on purpose; run such tests
  outside `tester-gate` (or in a child with its own limit) rather than expecting a pass.
- **uid requirement.** The wrapper runs as the image's user (uid 1003 in `tester-unified`) and
  must be able to create `.cmru/tester-gate-events-<uuid>.txt` in your mounted worktree. If the
  host user that owns the worktree and the image uid do not match so that the directory is not
  writable, the file is never written and every gate ends exit 3 with "events file is missing";
  fix the ownership (the launcher creates `.cmru/` as the invoking host user) rather than
  ignoring the failure.

### Reproducing a gate step by hand

When a release goes red, you copy the failing step's `argv` and run it directly. Export the
orchestration env block first — the preflight will tell you the complete list in one shot if you
forget, but it is faster to set it before the first try:

```sh
export CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local \
       CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
       CMRU_TESTER_PIDS_LIMIT=4096 \
       CMRU_TESTER_CGROUP_PROBE_IMAGE="$(docker image inspect --format '{{index .RepoDigests 0}}' debian:trixie-slim)" \
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

For the vbpub CMRU checkout, from `cmru/` use the installed Run-Gate CLI
rather than a cockpit pytest command:

```sh
run-gate --list
run-gate gate
```

The CMRU aggregate requires a stable Run-Gate wheel at SemVer `24.0.0` or later.
`run-gate --version` reports its SemVer; the CMRU gate verifies that it matches
the installed wheel metadata and refuses development, local-suffix, or older builds
before any CMRU sub-lane starts. It also requires a non-editable wheel install,
resolves and hashes the published Run-Gate wheel for that installed version,
checks that the installed executable module bytes match the wheel, and verifies
that the installed wheel's `direct_url.json` digest and configured
`tester-unified` image's version and wheel-digest labels match those exact
bytes. The installed version need not be the newest published version as
long as it meets the declared minimum. The gate rechecks the
image ID and labels around every lane, and pins each lane to that image ID. The
snapshot manifest records the CLI,
installed wheel, published wheel, and tester image identities; resume refuses
if any of them changed.

The native-R2 `mutation` lane requests contract-2 event evidence from
`cgprofile`. The `installed-wheel`, `installed-wheel-live`, `ref-probe`,
`assay`, `coverage`, and `canary` lanes declare 2 GiB memory and an
8192-process ceiling but do not collect event evidence; KI-77 tracks adding
working observation to those command lanes. The release step for the current
6.2.0 candidate runs `run-gate gate-provisional`, which skips the long mutation
lane and records that deferral in `.assay/mutation-postponed-cmru.json` under
KI-79. The deferral is authorized only for CMRU >= 6.2.0 and < 6.3.0 (the 6.2 patch line) or >= 7.0.0 and < 7.1.0 (the 7.0 patch line;
6.3.x, 7.1.0 and everything else refuse), so a 6.2.x or 7.0.x release is an ordinary patch release; a
bump that computes a version outside those windows is refused by the gate. Every release deferred this way is without an R2 pass; one full R2 run is owed before 6.3.0 or 7.1.0, whichever is first (KI-79). That release
therefore has no mutation event evidence. Read its result from
`cmru/.assay/release-gate-snapshot.json`: `"state": "provisional"` with a `deferred_lanes`
entry for `mutation` (never `"passed"`), for example
`jq '{state, deferred_lanes: [.deferred_lanes[] | {lane, tracking_id, follow_up_lane}]}'`.
The later mutation campaign needs a branch created at the published release tag (so the exact tagged source is judged), the CGProfile host observer deployed and reachable, and the D-4 capacity evidence the Run-Gate follow-up lane requires (defined by the Run-Gate RG-55 program, not by CMRU). Run `run-gate
gate` when full R2 evidence is required (the recorded `follow_up_lane` is `gate`); that lane
includes the mutation campaign. If cleanup fails after the lanes pass, the gate exits non-zero and
leaves `"state": "failed"` with an empty `deferred_lanes` and no marker; an invocation refused
before a new gate starts leaves the previous run's marker and manifest untouched.

For a CMRU repository with no published stable `cmru-vX.Y.Z` tag, the
protected `gate-provisional` release workflow first verifies that origin was
successfully queried and found empty. It then substitutes the whole-source
`cmru-first-release` lane for the ordinary changed-line `assay` lane. The
first-release marker identifies `run-gate assay-first-release-r2` as the
pending mutation campaign. Run it against the released source commit after the
first tag exists:

```sh
cd cmru
run-gate assay-first-release-r2
```

This follow-up uses a literal whole-source target list, so it requires no
previous CMRU tag. An inaccessible origin is an error and never selects this
path. The automated choice and its exact-commit restriction are described in
the [first-release design](DESIGN-GUIDE.md#first-release-has-no-historical-diff-baseline).

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
`run-gate gate` lane to prepare fresh origin facts immediately before
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
when the tests, copied fixture closure, and timeout exactly match the recorded run. Changing
the timeout requires a new campaign because it changes whether a candidate could have completed.
Any test or fixture change also requires a new campaign. Resume also requires a
clean Assay source tree and unchanged code under every package root declared by
Assay's `pyproject.toml` (including `analysis/src/` when present). Passing
full-suite runs still execute every test. CMRU compares package history in
Assay's own Git worktree, so `--repo-root` and `--assay-source` may name
different linked worktrees safely. Malformed package metadata, unsupported
external data-file mappings, and ignored files under package roots refuse
closed; only generated bytecode caches and editable-install metadata are
ignored. Every Assay
invocation resumes and writes `.assay/progress-cmru.jsonl`; its verdict is
`.assay/verdict-cmru.json`.
Each mutant command starts in its own process group while CMRU acts as a Linux
child subreaper. On timeout or normal exit, CMRU removes and reaps descendants
before resource counters are sampled or another mutant starts, including
helpers that call `setsid()` or double-fork. A normal leader exit with leftover
descendants is recorded as `crashed`; the campaign continues after cleanup,
but this attempt cannot count as killed or survived. A timeout and any
observed cgroup resource event are also inconclusive. The evidence file uses
schema 5 and records `candidate_isolation = "process-tree/1"` plus process-tree
proof for each run; resume reuses a kill only when that proof, the unchanged
timeout, and clean resource counters are present. Older evidence schemas are refused.
The record names `assay_distribution_version` as package metadata and
`assay_source_commit` as the exact source identity. CMRU checks Assay source
cleanliness with a sanitized Git process before import, binds the import to a
declared package root, and repeats cleanliness and both HEAD checks after the
baseline control. Ignored untracked source or test files and Git index entries
marked assume-unchanged or skip-worktree refuse the campaign. Known generated
bytecode, pytest and Hypothesis caches, coverage outputs, and editable-install metadata are
allowed. Unsupported layouts such as `py-modules` or named `package-dir`
mappings refuse closed; configured package roots cannot contain symlinks.
Before installing Assay editable, the mutation, coverage, and canary lanes run
`tools/check_assay_source.py`. It rejects uncommitted or ignored files anywhere
in the Assay checkout that could affect package building; only declared
runtime state, bytecode/pytest/Hypothesis caches, and coverage outputs are
excluded. The mutation lane then
copies tracked CMRU inputs once into a private frozen template. The known-good
control and each mutant run in a fresh copy of that same template, so later
edits to the live checkout cannot silently change the tests or shared-library
fixtures during a long campaign. Run-Gate state, Assay state, and coverage
outputs are excluded from the frozen inputs. Candidate Python bytecode uses a
private cache directory, isolated from cached bytecode in the checkout.
The disposable controls include the Topos and nyxloom CMRU manifests consumed
by CMRU's estate-adoption test, so the full suite remains runnable in each
baseline and mutant copy.
`gate` also runs CMRU's total-coverage and cause-sensitive canary lanes. It
starts with the KI-26 installed-wheel acceptance lane, which builds the wheel,
installs it into a fresh isolated venv, and invokes `cmru get-py` outside the
source checkout. (The former real-enrollment lane moved to ciu with `get.py
enroll`; see "Installer extensions" below.)

Release worktrees never contain a credential file: the launcher hands the resolved
token to its transaction child over a private pipe (see the
[credential handoff](DESIGN-GUIDE.md#keeping-release-credentials-out-of-gate-containers)).
The aggregate release gate requires a linked worktree with no tracked changes.
It creates an attached sidecar worktree at that exact commit and runs each
registered lane from the sidecar, so the caller's checkout is never mounted.
Ignored files, nested virtual environments, and secrets created in the caller
after snapshot creation stay outside the tester view. A live host-only canary
proves this from inside the `installed-wheel-live` tester lane (a fresh manifest
bound to that very snapshot; a stale `running` manifest left by a killed gate does
not count). The gate judges only code bound to the claimed commit: it refuses an
untracked or ignored helper, a symlink, or a tracked helper edited since HEAD
under the directories it imports from, before any lane starts. Run `git status`
and commit your change first; a refusal names the files. It watches the protected
Git refs it owns, and its `ref-probe` lane proves that detector from inside the
real container. New refs and fast-forward moves of `main` are allowed; moving or
deleting an existing release tag, or rewriting `main` non-fast-forward, fails the
gate. Unrelated existing branch refs are not checked because the common Git
directory remains writable.

CMRU refuses a tracked `cmru.secret.toml` and any configured Git filter before
creating the snapshot. It validates lane output, retains only Run-Gate state
and named evidence, restores the exact commit between lanes, and copies only
that evidence back to the caller. The sidecar stays attached for resume and is
removed with its release candidate. The host uses CMRU's scoped Git auth to
prepare origin facts for mutation; those facts contain tag names and commit
IDs, not credentials. Nested runner calls have publisher-token and
extra-mount variables removed. See the
[gate credential design](DESIGN-GUIDE.md#keeping-release-credentials-out-of-gate-containers).
Running an individual tester component lane bypasses this boundary and should
use a secret-free checkout. `run-gate installed-wheel` stays runnable that
way; `installed-wheel-live` is the aggregate gate's own variant and refuses unless
it runs inside that gate's fresh snapshot manifest. During each lane CMRU also
refuses changes to existing release tags or a non-fast-forward change to `main`;
new refs and unrelated branch refs are not checked because Run-Gate keeps the
common Git directory writable.

### Running the release gate from a host whose `python3` is not the estate environment

`run-gate gate` and `run-gate gate-provisional` start a host-side controller
(`python3 -I -B cmru/tools/run_release_gate.py`) that needs CMRU's declared dependencies
(`cli-extended`). What you do:

1. Install the released `cmru` wheel and its dependencies into the estate Python
   environment, offline, as the CMRU README's Install section describes (for example
   `pip install --no-index --find-links <wheelhouse> cmru`), and put that environment's
   `bin` directory on `PATH` so `command -v cmru` finds it. Do not install into the system
   interpreter just to satisfy the gate, and do not copy `cli_extended` into a checkout.
2. Run the registered lane as usual from the project directory:

   ```sh
   cd cmru
   run-gate gate-provisional
   ```

3. If the host's `python3` is not that environment, the controller says so and continues. It
   switches when it cannot import a declared dependency **or** imports one whose installed
   version does not satisfy the declared requirement (for example `has cli-extended 0.2.0
   installed, which does not satisfy cli-extended>=0.3.0`):
   `cmru-release-gate: /usr/local/bin/python3 cannot import cli_extended; running the
   controller under the installed CMRU environment's interpreter <venv>/bin/python
   (named by the installed cmru launcher <venv>/bin/cmru)`. Nothing else changes; the lanes
   are still `tester-unified` containers started through `run-gate`.

   When the gate is started by `cmru release` (its `run-tests` step), you do nothing:
   CMRU reports its own interpreter to the step (`CMRU_INTERNAL_PYTHON`, shown as
   `provided by the CMRU release runtime`), because the `cmru` first on `PATH` inside a
   release is CMRU's temporary shell wrapper, which names no environment. Never export that
   variable yourself; it is honoured only inside the release workflow and ignored in a shell.

It stops (exit 1, before any lane, with `cmru-release-gate:` and the same remedy) when there
is no `cmru` on `PATH`, when it is not a Python console script (for example a shell wrapper
in a manual run), when that
environment lacks a declared dependency or carries one below the declared floor (reinstall
the wheel with its dependencies; the message names the installed version), or when
the declaration in `pyproject.toml` cannot be read. Check which environment would be used
with `head -1 "$(command -v cmru)"`. See the
[design note](DESIGN-GUIDE.md#the-bare-host-gate-controller-runs-in-the-installed-cmru-environment-ki-81)
for why it works this way.

### Retained gate snapshots

Every gated commit leaves one attached `cmru-gate-*` worktree and branch beside its
source checkout, so a failed or interrupted gate can resume. Inspect and clean them
with the supported verb, never with `git worktree remove`:

```bash
cmru gate-snapshots                        # list: removable / retained / stale-record, with the reason
cmru gate-snapshots --remove --dry-run     # preview exactly what would be removed
cmru gate-snapshots --remove               # remove the snapshots whose source moved on; drop stale records
cmru gate-snapshots --remove --include-current   # also the resumable one for the current HEAD
```

A `stale-record` is a record whose snapshot directory is gone AND whose worktree Git no longer registers (nor lists its branch anywhere, so a path only invisible from this mount namespace is never called stale); `--remove` drops just the record, its derived Assay state and a leftover branch that still points at the recorded commit. Two lookalike states stay `retained`, each with its own reason: the directory is gone but Git still registers it (`git worktree prune` after confirming), and the directory exists but Git does not register it (inspect it).

It keeps a snapshot whose gate is still running, whose lease holder lives in another
PID namespace and cannot be proven dead from where you are (run the verb where the gate
ran), or whose source checkout merely cannot be seen from this namespace while Git still
registers it. Repeating it is safe; each removal is re-judged from fresh facts.

### Release bodies and curated notes

The GitHub Release body for a tagged release is the generated install block followed by
your changelog's exact section for that version. What you do:

1. Keep `[project.release] changelog` at its default (`CHANGES.md`) or point it at a
   project-relative file; `changelog = false` opts out and publishes the generated body
   alone.
2. Put the curated notes (Added, Fixed, Migration, ...) in the version's own section,
   which CMRU creates as `## [X.Y.Z] - YYYY-MM-DD` with a `<!-- cmru: generated -->`
   marker and a `<!-- cmru: source-end=<40-hex> -->` cursor:

   ```text
   ## [2.0.0] - 2026-10-13
   <!-- cmru: generated -->
   <!-- cmru: source-end=0123456789abcdef0123456789abcdef01234567 -->

   ### Added
   - Your note, in your words.

   ### Migration
   1. Replace `old` with `new`.
   ```

3. Check it before you release: `cmru release <name> --dry-run` prints the complete body
   between `----- begin release body -----` and `----- end release body -----`; for a wheel
   or tarball the artifact file name and SHA256 are the only placeholders (an image-only
   release has none, see below). The real publisher builds the body with the same function.

A missing, undated, duplicated, empty or wrong-version section refuses before the gate,
tag or upload with a message naming the file and the sections it did find. Do not edit a
generated section by hand without moving its cursor: if project commits land after
`source-end`, CMRU must regenerate the section and refuses (exit 4, listing the lines
it would drop) rather than lose your notes. Set `source-end` to the full SHA of the final
commit that changed the project (excluding the changelog) in the same commit as the edit,
and a current section is kept byte-identical. The publish step must run through
`cmru release` / `cmru publish`, which pass the protected `CMRU_RELEASE_NOTES_SOURCE`
declaration; a handler started by hand without it refuses and prints the two values it
accepts (`changelog:<project-relative path>` or `none`).

**Pending notes and migration guidance: write them under `## [Unreleased]` (KI-78).**
Whatever is under `## [Unreleased]` in the project's configured changelog (`CHANGES.md` by
default; any `release.changelog` path works) is folded into the generated dated version
section in every tagged release candidate before the gate and tag:

```markdown
## [Unreleased]

### Migration
1. Run `tool migrate --yes` before upgrading; it rewrites `tool.toml`.
```

`cmru release <name> --dry-run` previews the body with those notes inside the dated section
when a CMRU GitHub Release-body handler will publish it. For `--no-build`, and for an image
push without `oci-image-release`, it previews the tagged changelog section instead; no
GitHub Release body is published. In every case the release candidate commits the folded section before the gate,
and its tag carries those notes. The candidate reaches `main` only after a successful publish
and promotion. A failed gate, build, publish or promotion leaves the source branch's
notes under `[Unreleased]`. This removes the need to park notes outside the changelog
(KI-58). HTML comments under `[Unreleased]` stay there and are not published; to show a
marker such as `<!-- cmru: generated -->` in a note, quote it in backticks or a code fence,
and it moves with the note verbatim. An unclosed `<!--` inside `[Unreleased]` refuses the release
before any change, because the text after it would otherwise vanish from both the changelog and
the release body. CMRU also reads direct `cmru publish --from-checkout` release notes from the
matching local release tag, never from later mutable changelog edits.

**Carrying forward notes that were parked outside a changelog (KI-58).** Notes that were
already removed from `## [Unreleased]` can either be put back under `[Unreleased]` (above)
or, to review them as part of the history, moved into the pending version's section in ONE
commit on `main` before releasing:
author `## [X.Y.Z] - <date>` with the generated marker, `source-end` set as above, and the
parked entries under their `### Added/Changed/Fixed/Documentation` groups; leave
`[Unreleased]` empty. Pin the version with `--set-version X.Y.Z` if the planner could
choose another. Then run `cmru release <name> --dry-run` from the pushed `main` and check
that the body ends with exactly those entries and that nothing refuses. A project whose
publish step is an image push without `oci-image-release` has no GitHub Release body; its
notes are still present in the tagged changelog.

#### Image-only releases: publish the body with `oci-image-release`

A project whose immutable artifact is an OCI image that its own build step pushes (nothing
is uploaded to GitHub) gets a GitHub Release body when its `push` step runs
`oci-image-release` AFTER the image push. Paste the command into the step that already
pushes the image:

```toml
schema_version = 1

# Excerpt of the project's cmru.toml; the rest is as in the example at the top of this guide.
[steps.push]
quiet = true
commands = [
  { label = "push image", argv = ["cmru", "handler", "oci-image-push", "--cwd", ".", "--bake-file", "docker-bake.hcl", "--bake-target", "release"], cwd = "." },
  { label = "release notes", argv = ["cmru", "handler", "oci-image-release", "--prefix", "example-image", "--image", "ghcr.io/your-owner/example-image", "--cwd", "."], cwd = "." },
]
```

What it does and requires:

- It must be the **last command** of the `push` step, after the command that pushes the
  image, because it publishes externally visible metadata. Pass `--cwd .` with `cwd = "."`
  as above: `--cwd` is resolved from the command's own `cwd` and must land on the project
  root (the directory of the project's `cmru.toml`, which holds the changelog). Use only
  relative paths; an absolute one would bake one candidate's location into a file every
  candidate shares and is refused.
- **Checked before anything happens.** For every selected project, CMRU validates the
  command before any `prepare` step (including the `external:`-version `prepare` that a dry
  run executes), gate, tag or image push, and `cmru release --dry-run` runs the same check
  and refuses identically (the changelog is left untouched). It refuses: a missing, duplicate, empty or unknown option (the only
  options are `--prefix`, `--image` and `--cwd`, each exactly once, `--flag value` or
  `--flag=value`); a `--prefix` other than the project's release prefix without `-v`; an
  `--image` with a tag or digest or not shaped `<registry>/<repository>` with an **explicit
  registry** (first component contains a `.` or `:port`, or is `localhost`: `ghcr.io/you/app`,
  `myhost:5000/app`, `localhost/app`; `registry/team` is refused because Docker would pull it
  from Docker Hub, so write `docker.io/team/app` for Docker Hub); a `--cwd` that is
  not the project root; any command after it in the push step (for example an image push
  listed after it); a project with `release.git_tag = false`; a push step that sets
  `bake_set_prefix`/`bake_set_vars` or `no_cache_env`, which make the runner append
  arguments to every command in the step; and another CMRU release-body handler in the same
  step.
- The image tag is the release version, taken from the protected `CMRU_RELEASE_TAG`
  (`example-image-v1.4.0` becomes `ghcr.io/your-owner/example-image:1.4.0`). Run it only
  through `cmru release`, which supplies `CMRU_RELEASE_TAG`, `CMRU_RELEASE_COMMIT` and
  `CMRU_RELEASE_NOTES_SOURCE`; started by hand it refuses and names the missing value. Do
  not export them.
- The body opens with the exact coordinate and its pull command, then carries the tagged
  changelog section (the same notes the tag holds):

  ```text
  **Image:** `ghcr.io/your-owner/example-image:1.4.0`

  Pull: `docker pull ghcr.io/your-owner/example-image:1.4.0`

  ## [1.4.0] - 2026-10-15
  ...
  ```

  There is no artifact name or SHA256. `cmru release <name> --dry-run` prints this very
  body, complete and without placeholders.
- It writes release metadata only. It does not build or push the image (the step before it
  does), it never uploads, deletes or replaces an asset, and a retry simply rewrites the
  body of the same release. It requires the release tag to be on the remote at exactly the
  candidate commit (`cmru release` pushes it first) and refuses, before changing anything,
  if the tag is absent or points elsewhere or if an existing release targets another commit.
  For an existing release it re-fetches the release and re-checks its ID, tag, commit target
  and the remote tag target immediately before it edits the body, and sends nothing if any of
  them changed.
- **What it cannot prevent.** GitHub cannot make the tag check atomic with creating or
  editing a release, so a tag or release change that happens in that instant is detected only
  by the read-back after the write. After every create or update the release is read back and
  must carry the same ID and tag and **exactly the intended body**, and the tag must still be
  at the release commit (and any full-SHA `target_commitish` must be that commit); the handler
  succeeds only then. If the read-back differs (a replaced release, an overwritten body, a moved
  tag or target), is missing or malformed, or cannot be reached, the error says the release
  **was written**, that nothing was rolled back, and to inspect the release and the tag before
  retrying; the release may exist with the new, or someone else's, body. A create reads its release
  by ID with a finite number of retries for "not yet visible". **An HTTP error, a transport
  failure, or a malformed success response after the POST/PATCH was sent is treated as possibly
  applied:** the handler never resends, reads the release back, returns success only if the exact
  ID, tag, body and target are verified, and otherwise reports `has an UNKNOWN outcome ... MAY OR MAY
  NOT have been written and nothing was rolled back`. Inspect the release and the tag (for example
  `gh release view <tag>` and `git ls-remote origin refs/tags/<tag>`) before any retry. The validation above stops bad configuration from
  reaching a release; it does not make publication atomic, and it does not undo an image
  that was already pushed if a later step fails.
- The push step may hold only one body handler. Combining `oci-image-release` with
  `wheel-publish`, `tarball-publish` or a second `oci-image-release` is refused before the
  release starts, because the later handler would overwrite the earlier body.
- `--no-build` still publishes nothing (no image, no body); its dry run previews the changelog
  section. A push step that only pushes the image keeps the notes in the tagged changelog and
  has no GitHub Release body.

#### Retry a tagged OCI release body

If an image push succeeded but the configured `oci-image-release` command failed, retry only
the GitHub Release metadata from a clean checkout at the exact release tag:

```sh
cmru publish example-image --from-checkout \
  --release-tag example-image-v1.4.0 \
  --secret-file /absolute/path/to/main/cmru.secret.toml
```

`--secret-file` names the ignored root `cmru.secret.toml`, which normally lives only in the main
checkout, not in a linked or retained worktree. A relative path is resolved from the directory you
invoke `cmru` in, so `./cmru.secret.toml` from a retained worktree names a file that does not exist
there and CMRU refuses it. Pass the main checkout's absolute path, or omit the option when
`GITHUB_PUSH_PAT`/`GITHUB_TOKEN` is set for the invocation or the current checkout has its own root
secret.

CMRU verifies that the explicit local tag peels to `HEAD` and that origin has the same tag at
the same commit, then runs only the project's validated final `oci-image-release` command.
It does not rebuild or push the OCI image. Use this dry run to inspect the tagged handler
command and local identity; it does not query origin:

```sh
cmru publish example-image --from-checkout \
  --release-tag example-image-v1.4.0 \
  --secret-file /absolute/path/to/main/cmru.secret.toml --dry-run
```

The retry reads
the changelog from the tagged commit, so later working-tree text is not published. See the
[design rationale](DESIGN-GUIDE.md#retrying-a-tagged-oci-release-body-runs-only-the-metadata-handler).

Why it works this way: see the
[design note](DESIGN-GUIDE.md#image-only-releases-get-a-body-through-oci-image-release-ki-78).

---

## 3. The release flow, and what an isolated transaction is

```
cmru status                       # what would release, and at what version bump
cmru release <name>     # one source-first transaction: gate → tag → build → publish → promote
cmru release                      # changed projects, one transaction per Git family (S-CLI.5a)
```

### Scoping Conventional Commits in a multi-project release

When `version.strategy = "conventional"`, a recognized project scope localizes that commit's
feature or breaking bump. For example, `feat(assay)!: change verdict format` requests an Assay
major bump if the commit also changes Run-Gate. Use a configured project name or alias as the
scope. An unscoped breaking subject keeps its existing effect on all changed projects. An
unknown scope also keeps the historical path-based effect, so a typo cannot hide a breaking
change. `cmru status`, release tags, generated changelog headings, and release notes all use the
same configured project-scope registry. Check the plan with `cmru status`; use
`cmru release <project> --set-version <version>`
when the release requires an explicit version. See the [scope-bump design](DESIGN-GUIDE.md#known-project-scopes-localize-version-bumps).

An actual release requires the `cmru` command to come from a non-editable CMRU
wheel. Install the exact wheel built from the committed release-controller
source, then invoke the ordinary release verb:

```sh
python -m pip install --force-reinstall /path/to/cmru-<version>-py3-none-any.whl
cmru --version
cmru release <project>
```

`cmru release --dry-run` remains available from a source checkout. A real
release refuses editable or source-tree execution and records the controller
version, wheel digest, and installed-file digest in `cmru.release.log` and the
transaction log. The [release-transaction guide](RELEASE-TRANSACTIONS.md#bootstrap-when-the-release-engine-changes-in-the-same-wave)
shows how to build that bootstrap wheel when the release engine itself changed.

Declare where the credential comes from in the committed `[github]` table (mandatory, KI-94):

```toml
schema_version = 1

[github]
owner = "acme"
repo = "estate"
owner_type = "user"
token_source = "gh"      # or "cmru"
```

`"gh"` takes the token of the user's `gh auth login` (`gh auth token --hostname github.com`):
no secret file and no `--secret-file` in any worktree. For GHCR work run
`gh auth refresh -h github.com -s read:packages,write:packages,delete:packages` once (`delete:packages` is for `cmru cleanup`). `"cmru"` keeps today's
behaviour: for an HTTPS GitHub `origin`, keep the repository credential in the ignored
CMRU-root secret file (or `GITHUB_PUSH_PAT`/`GITHUB_TOKEN`):

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

When a release starts from a linked worktree that has no root secret, CMRU may report the
corresponding `cmru.secret.toml` path in one unambiguous `main` worktree. It checks the path
exists but never reads that other file automatically. To opt in, repeat the same release with
`--secret-file /absolute/path/to/main/cmru.secret.toml`; the environment token still wins, and
the parent passes the resolved credential to the transaction child through its private pipe.
When path inference is unavailable or ambiguous, supply `--secret-file PATH` yourself or set
`GITHUB_PUSH_PAT`/`GITHUB_TOKEN` for that invocation.
If a feature-source dry run cannot inspect or fetch the private origin branch, the same
main-worktree secret advice is included when CMRU can identify the file.

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
moved a project config since your caller checkout, CMRU reads its policy from the
path recorded in the snapshot; the project secret overlay is read from your caller
checkout on the host and handed to the child, never copied into the snapshot.
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
cmru abandon <ABSOLUTE-PATH> --yes
cmru abandon --dry-run                           # show all retained release candidates, no writes
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --dry-run
cmru abandon cmru-release-20260924_120000-example-a1b2c3 --yes
cmru abandon cmru-release-20260924_120000-example-a1b2c3 \
  --config /path/to/cmru.orchestration.toml
```

### Release from a pushed feature worktree

The `--from-worktree` flow (KI-82) has shipped since cmru 6.2.0; an older installed CMRU
does not list it in `cmru release --help`. It supports one Git family per release and requires a
clean worktree on a named non-main branch. CMRU fetches that exact `origin/<branch>` and refuses
unless its tip equals local `HEAD`; commit and push all intended work to the same-named remote
branch first. The release can be ahead of or diverged from both local and remote `main`. A
detached `HEAD`, `main` itself, `--allow-uncommitted` and `--ahead-check-ref` (or its old `--ref`
spelling) are refused in this mode: the source is exactly the clean checked-out commit, and
local `main` is never compared or moved.

```sh
# From the clean feature/foo worktree, after pushing its current commit:
git push origin HEAD:refs/heads/feature/foo
cmru release ciu --from-worktree \
  --secret-file /absolute/path/to/main/cmru.secret.toml
```

Omit `--secret-file` when credentials are available in the environment, the selected worktree's
secret, or SSH/Git configuration. If CMRU reports an inferred main-tree secret path, pass that
exact path explicitly only when you intend to use it. CMRU does not push the source
`feature/foo` branch. It creates a separate generated branch, such as
`cmru-release-<timestamp>-ciu-<workspace-id>`, and pushes that exact name to origin as its
durable candidate. CMRU creates that branch only when the origin name is unused. During resume,
if the candidate ref has moved or is no longer an ancestor of the local candidate, CMRU refuses
the update and retains the transaction; inspect and reconcile the branch without a force push.

To retry a retained pre-tag feature-source candidate, run the `resume:` command CMRU printed,
from the same Git family:

```sh
cmru release --resume /path/to/repo/.worktrees/<candidate-worktree>
```

CMRU reads the feature-source identity recorded with the candidate and re-applies
`--from-worktree` itself, so a resumed candidate is never promoted to `main`. A feature-source
resume refuses `--allow-uncommitted` and `--ahead-check-ref`; `--from-worktree --resume` naming a
default `origin/main` candidate is refused; and a candidate that has already published every
selected project is refused, because the next step is merging its PR and running `cmru abandon`.

After successful publication, CMRU retains the local candidate worktree and both candidate
refs. Open a PR from the generated candidate branch to `main`, then merge it with a fast-forward
or merge commit so the tagged commit remains in `main`'s ancestry. The release may be public
while that PR is open or blocked; squash and rebase do not preserve the released commit ID.
After the merge, run `cmru abandon <candidate-branch> --dry-run` from a checkout with the
credentials needed for origin access (normally the main checkout, where the root secret lives),
then pass `--yes` to retire the local transaction and any remaining candidate ref. `abandon` has no `--secret-file`
(only `release` and `publish` accept it);
for retirement from another worktree, use the main checkout or set the Git credential environment
for that invocation. Retirement preserves release tags and public assets. If the host already
deleted the PR head after merge, the same command can retire the local transaction: CMRU verifies
that the exact final candidate commit is an ancestor of fetched `origin/main` and rechecks that
fact after confirmation. An absent branch without that ancestry proof is withheld. An unmerged
published candidate is deliberately withheld from abandonment.
See the [design rationale](DESIGN-GUIDE.md#release-from-an-origin-backed-feature-worktree-ki-82)
for why publication before PR merge is accepted and why squash/rebase integration is unsupported.

For a tagged project, the exact release tag must be on origin before CMRU invokes its build or
publisher. If a step fails after CMRU creates the local tag but before it pushes it (the candidate
backup push, the re-gate of a `file:` version commit, or a candidate check), CMRU deletes that
never-pushed local tag at its exact object, so the printed `resume:` command can retry. A failed push makes CMRU recheck origin. If origin contains the exact candidate tag, CMRU continues;
if origin confirms the tag is absent, CMRU removes that exact local tag, stops before build or
publish, and retains a pre-tag candidate that can be resumed with its printed `resume:` command.
If origin cannot be checked, CMRU retains the tag and candidate for inspection. If origin has
the same tag name at a different object, CMRU also retains the local tag and candidate for
inspection. CMRU does not automatically retry a post-tag publication step; `--resume` is for
retained pre-tag candidates. When CMRU proves that a failed tag push left no origin ref and
removes the local tag, the saved resume command can retry that release; CMRU permits a freshly
generated annotated tag object for the same tag name in that case.

For tagged release artifact steps, CMRU supplies `CMRU_RELEASE_COMMIT` as the
exact gated candidate commit and `CMRU_RELEASE_TAG` as its tag. It clears both
ambient values from every other project step, and project and step configuration
cannot override these protected values. The pair is source-consistency metadata,
not an authentication token or proof of CMRU provenance.

During a release gate, CMRU supplies the candidate SHA as the protected
`CMRU_RELEASE_GATE_COMMIT`. Release preparation steps can use it to bind a
pre-tag receipt to the exact candidate. CMRU clears ambient values on other
steps, and project configuration cannot set this name.
For CMRU-derived releases, the gate also receives the transaction's exact
planned version as protected `CMRU_RELEASE_GATE_VERSION`. A gate that validates
a prepared changelog can compare its first release section with this value
before CMRU creates the tag. External-version projects do not receive a
CMRU-planned version.

A build that derives artifact identity from Git should check `HEAD` and the local
tag against both values, then build from that exact commit. Invoke `cmru release`
for publication; do not set the values manually.

CMRU verifies that tracked source still matches the candidate around the gate,
build, and publish steps. It refuses `assume-unchanged` and `skip-worktree`
index flags, which can make Git status hide edits. Build outputs and logs should
be ignored or declared as commit-bound evidence; do not have a release step
rewrite tracked source.

Put this check in a tagged artifact step before building. It verifies both the
checked-out commit and the local tag after peeling annotated tags:

```sh
set -eu
: "${CMRU_RELEASE_COMMIT:?CMRU did not supply the candidate commit}"
: "${CMRU_RELEASE_TAG:?CMRU did not supply the release tag}"
head=$(git rev-parse --verify 'HEAD^{commit}')
tag_commit=$(git rev-parse --verify "refs/tags/${CMRU_RELEASE_TAG}^{commit}")
test "$head" = "$CMRU_RELEASE_COMMIT"
test "$tag_commit" = "$CMRU_RELEASE_COMMIT"
```

**A resume keeps the first attempt's version (D-001).** CMRU records each project's exact
version before the first release side effect, and every resume reuses it. Do not repeat the
version flag:

```sh
# The first attempt chose CMRU 6.2.0 with --minor and failed before tagging:
cmru release cmru --minor --from-worktree
# Resume it; it releases 6.2.0, never a recomputed 6.1.1:
cmru release --resume /path/to/repo/.worktrees/<candidate-worktree>
```

The resume log names the version it kept, as `Resuming with the recorded version plan: cmru
6.2.0`. A flag that selects a different version, such as `--set-version 9.9.9` here, refuses
with exit 4 before the candidate, a tag, or a publication changes. Repeating a flag that
selects the same version is accepted. A candidate created by an older CMRU has no recorded plan.
It resumes only when its committed release-history commit proves the version; otherwise CMRU
refuses and you abandon it (`cmru abandon <candidate-branch>`) and start a new release.
See the [design rationale](DESIGN-GUIDE.md#a-retry-keeps-its-recorded-version-plan-d-001).

On resume, CMRU checks tag-push attempts and recorded release results against origin. A tag that
may have been pushed without a completed result, or a recorded result whose source commit is not
in `origin/main` (for a feature-source candidate: not in its origin candidate branch), causes a refusal and keeps the candidate for inspection. A release-plan refusal
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

### Releasing without gates (`--skip-gates`)

When the declared `run-tests` gate genuinely cannot run, the operator may release without it. The
reason is required and is published:

```bash
cmru release ciu --skip-gates "operator: re-publish after release cleanup" --dry-run   # preview first
cmru release ciu --skip-gates "operator: re-publish after release cleanup"
```

The release prints `[WARN] release gates skipped by operator: <reason>`, shows `run-tests` as
`skipped (operator: <reason>)` in its plan, records the choice in the transaction, and adds
`Released without gates (operator): <reason>` to each project's GitHub Release body. Everything else
(prepare, build, push, tag, integration, the clean-tree and tool-dependency checks) runs as usual, and a
project that declares no `run-tests` step is still refused. If the release fails and you resume it,
use plain `cmru release --resume PATH`: the recorded choice applies, and `--skip-gates` with `--resume`
is a usage error (exit 2). The reason may be at most 500 characters.

If the final `oci-image-release` body step fails after the tag was pushed, retry it with
`cmru publish <project> --from-checkout --release-tag <tag>`. That retry restates the ungated
line from the retained transaction (or from the existing Release body); when neither states it, it
refuses (exit 4) and you declare the fact: `--gates-ran`, or
`--gates-skipped "operator: re-publish after release cleanup"`.

The rationale is in the
[design guide](DESIGN-GUIDE.md#an-ungated-release-is-an-explicit-recorded-flag-cmru-skip-01).

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
A resumed release needs no credential file in the candidate: the resuming launcher
resolves the credential on the host from your checkout and hands it to the resumed
child over the same private pipe, so the retained worktree stays secret-free. CMRU reads `project.release.git_tag` from the committed retained
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
on the commit your checkout (or feature-branch worktree) is on, pushed or not;
`--from-origin-main` builds fetched `origin/main` instead and `--allow-uncommitted`
builds the committed state past uncommitted edits to the project. It records its outputs with a source identity and SHA-256 inventory. To publish
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

A non-empty plain `## [Unreleased]` section is folded into every tagged version section before
the gate (KI-78; this replaced the KI-30 refusal). A CMRU GitHub Release-body handler also
publishes it in the body; `--no-build` and other release publishers keep it in the tagged
changelog without a GitHub Release body. A stale generated history section that holds hand
edits is refused rather than regenerated
over them (KI-68); see
[release history behaviour changes](RELEASE-TRANSACTIONS.md#release-history-behaviour-changes-rel-02-rel-10).
To hand-curate a pending generated section, advance its `source-end` marker in the same final
commit (so it counts as current); curated release notes belong there (see "Release bodies and
curated notes" above), other text may be curated after the release.

After a release transaction, CMRU also cleans up the caller's local `main` when it can. If
that checkout is dirty with tracked or untracked (non-ignored) changes, CMRU refuses the cleanup
rebase before invoking either rebase command and leaves the files and local ref untouched.
Ordinary ignored build output does not block it; an ignored file blocks only where `origin/main`
would add a file at that exact path.
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
[`S-CLI.5a`](SPEC.md#s-cli5a--default-originmain-projects-release-one-after-another-not-in-a-shared-batch) contract
and the [caller-main cleanup operations](RELEASE-TRANSACTIONS.md#caller-main-cleanup).

### Retaining gate evidence

If the release gate writes commit-bound evidence, add exact files or directories to
`[project.release].evidence_paths` in the complete `cmru.toml` example above (for example,
`evidence_paths = ["coverage.json", ".assay"]`).

CMRU moves those paths into `<project>/evidence/cmru-release/<immutable-id>/` after the whole
release succeeds and writes `evidence.json` with the gated source commit and SHA-256 hashes.
The paths must be project-relative, contain no `..`, and contain no symlink component; a
missing or unsafe declared path fails retention and keeps the release worktree available for
inspection. Use `release --discard evidence` only when deliberately discarding those
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

- **OCI repack** is unavailable: the `--repack` option was removed from the handler verbs while
  KI-02 is open and returns when it is fixed, once it proves single-build + registry-digest equivalence (KI-02, `S14`).
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
