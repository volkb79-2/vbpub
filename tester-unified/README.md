# tester-unified

`tester-unified` is the vbpub gate environment. The devcontainer is only the
cockpit used to launch it; pytest and the actual gate command run in the image.

Build the Run-Gate wheel from the same checkout first, then use the supported
image builder to install those exact wheel bytes into the image. The wheel
version and SHA-256 are checked during the build, and CIU's source-match
preflight verifies that the installed wheel modules match the selected
checkout.

The default base is the published MDT devcontainer image. When no vbpub image
has been released yet, `--base-image` selects the upstream Python devcontainer
so the gate image has no first-party image dependency:

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CMRU=0.0.0+bootstrap.source \
  CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh
cp libraries/cli-extended/dist/cli_extended-*.whl tester-unified/local-wheel/
CLI_EXTENDED_SHA256='<source-wheel sha256 printed by the bootstrap command>'
tester-unified/build-image.sh \
  --base-image mcr.microsoft.com/devcontainers/python:3.14-trixie \
  --cli-extended-resolve local \
  --cli-extended-wheel-sha256 "$CLI_EXTENDED_SHA256" \
  --wheel-dir run-gate-project/dist \
  --tag tester-unified:bootstrap
```

The Dockerfile installs and checks its required host-facing tools on either
base, installing only what the base lacks: the MDT base already carries Docker's
`docker-ce-cli` and `docker-compose-plugin` and keeps them, while the upstream
Python base gets Debian's `docker-cli` and `docker-compose`. CMRU, Assay, project sources, and the exact Run-Gate wheel still come
from the selected vbpub checkout. Set the lane image to its immutable ID before
running release gates:

```bash
RUN_GATE_TESTER_UNIFIED_IMAGE="$(docker image inspect --format '{{.Id}}' tester-unified:bootstrap)"
export RUN_GATE_TESTER_UNIFIED_IMAGE
```

For a release candidate, set the exact planned SemVer before building the
Run-Gate wheel. This 23.x-to-24.0.0 transition uses:

```bash
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=24.0.0 \
  cmru handler wheel-build --cwd run-gate-project
```

For an ordinary local image, the wheel's scm-derived version is sufficient:

```bash
cmru handler wheel-build --cwd run-gate-project
```

After either build, build and load the image from that exact wheel. The builder
requires the configured `$CGROUP_PARENT_DEV_GATES` (or an explicit
`--cgroup-parent`), checks the host slice through CMRU's `tester-gate` probe,
and passes the same parent to Buildx. When `--base-image` is supplied, the
unprivileged preflight container uses that image. This lets the first tester
image be built before `tester-unified:local` exists. The separate host-systemd
probe remains digest-pinned and pull-disabled. The builder verifies the loaded
image labels still name the exact wheel version and SHA-256:

When called inside a CMRU release step, CMRU injects the tester resource
settings. For a direct shell invocation, export the required
`CMRU_TESTER_MEMORY`, `CMRU_TESTER_MEMORY_SWAP`, `CMRU_TESTER_CPUS`,
`CMRU_TESTER_PIDS_LIMIT`, and `CMRU_TESTER_CGROUP_PROBE_IMAGE` values from
`cmru.orchestration.toml`; the probe image must already be present locally.

```bash
tester-unified/build-image.sh --wheel-dir run-gate-project/dist --tag tester-unified:local
```

The image labels and `/etc/tester-unified-run-gate-wheel.json` record that
wheel's version and SHA-256. The builder verifies the loaded labels, and
Run-Gate's release lane checks the in-container identity against its
commit-bound receipt before running the suite.

`cmru handler wheel-build` runs the project build in CMRU's declared
`wheel-builder` container. From this repository root, CMRU resolves the image
from the enclosing CMRU configuration and refuses if no builder is declared;
the configured host cgroup placement and Docker access must also be available.
Do not substitute a local host-Python wheel build.

Image contents worth knowing (KI-52 / BG-05, 2026-10):

- **git maintenance is foreground-only.** The image sets
  `git config --system maintenance.autoDetach false` and `gc.autoDetach false`
  (asserted at build time). Detached `git gc --auto` daemons are orphaned to
  PID 1; a gate makes thousands of commits, and on 2026-10-05 that left 19,108
  zombies and exhausted the host pids ceiling. This is defence in depth: the
  launchers (`tester-unified/run`, `cmru tester-gate`) also run under
  `docker run --init` with an explicit `--pids-limit`. A caller that sets
  `GIT_CONFIG_NOSYSTEM=1` (assay's hermetic git environment) bypasses
  `/etc/gitconfig` and must set the same keys in its own git config (assay
  backlog B147).
- **Estate-internal packages never come from an index.**
  `tester-unified/gen-requirements.py` derives the third-party closure from the
  copied `pyproject.toml` files and REFUSES any estate-internal name
  (`worktree`, `cmru`, `assay`, `ciu`, ...) or direct-URL
  requirement, because the closure goes to a PyPI-default `pip` and an
  unclaimed or look-alike name would be installed into an image that is handed
  the host Docker socket. The one exception is `cli-extended` (cmru declares
  `cli-extended>=0.3.0`): the generator SKIPS that line, so no cli-extended
  requirement ever reaches pip, and the image installs the RELEASED wheel by
  default: `fetch-cli-extended.py` downloads the asset PINNED in the Dockerfile
  (`CLI_EXTENDED_WHEEL_URL` + `CLI_EXTENDED_WHEEL_SHA256` defaults, currently the
  released 0.3.0 wheel; to bump, copy `url`/`sha256` from
  `cli-extended-latest/latest.json` into those defaults and rebuild).
  `--cli-extended-resolve latest` is the explicit opt-in that reads the
  pointer instead (also pass `--no-cache`).
  `--cli-extended-resolve local` on `build-image.sh` is the ZERO-RELEASE path (three modes
  in all: `pinned` default, `latest`, `local`; see `docs/BOOTSTRAP-FROM-ZERO.md`):
  with no release to fetch, build the wheel with cmru's own wheel builder
  (`CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh` logs its
  sha256 and leaves it in `libraries/cli-extended/dist/`), copy exactly that one
  `cli_extended-*.whl` into `tester-unified/local-wheel/` (git-ignored) and pass
  `--cli-extended-wheel-sha256 <that sha256>`. The supported builder forwards
  the explicit mode and digest to BuildKit. The
  digest is checked with `sha256sum`, the wheel is installed `--no-index
  --no-deps` before cmru, and the build asserts it carries a PEP 440 local
  version segment such as `+bootstrap.source` (pinned/latest assert the opposite).
  It is never a fallback: a missing release
  in `pinned` mode simply fails. Redirects must stay https on a GitHub
  host. Either way it requires and verifies the sha256, and the wheel is installed
  `--no-index --no-deps` BEFORE cmru. cmru is built offline from the COPYed
  `cmru/` and `libraries/worktree/` sources (`pip wheel --no-index --no-deps
  --no-build-isolation` in a throwaway venv for its pinned build backend) with a
  `+tester.unified` local version. Run-Gate is separately installed from the
  exact wheel supplied by `build-image.sh`. The build asserts that
  `cli_extended` and `worktree` import from `/opt/tester-venv`, that cmru is the
  local build and that cli-extended matches the selected resolve mode. Gate lanes put only
  `src:../libraries/worktree/src` on `PYTHONPATH`; cli_extended always comes from
  the installed, digest-verified wheel (released or explicit local bootstrap).
  The repo-root `.dockerignore` whitelists exactly the
  inputs used by the image build. CMRU is built as an internal wheel from the
  copied repository source and installed into `/opt/tester-venv`. See the
  [design rationale](docs/DESIGN-GUIDE.md#the-installed-run-gate-cli) and
  [consumer command](docs/CONSUMERS.md#invoking-run-gate-from-a-cmru-step).

- **Run-Gate is installed from the exact wheel.** The builder copies the single
  wheel into a named BuildKit context, verifies its SHA-256 before and during
  the image build, installs those bytes into `/opt/tester-venv`, and verifies that
  `run-gate --version` agrees with installed distribution metadata. Run-Gate
  remains a stdlib-only package. Its source is not rebuilt from mutable files
  inside the image. The image contains no project-local `run-gate.py` CLI script or
  revision-marker compatibility shim; it uses the installed wheel.

Inside the image, Run-Gate reports the exact SemVer from the installed wheel.
The image digest and the CIU source-match preflight bind that package identity
to the checkout under test; no synthetic local version suffix is used.

Run a gate through the canonical launcher:

```bash
tester-unified/run --workdir run-gate-project -- run-gate selftest
```

The launcher requires the cockpit-provided
`CGROUP_PARENT_DEV_GATES`; it never invents a slice. It derives the
Docker host's workspace path from mountinfo, dual-mounts that workspace,
creates a disposable host-backed temp root, mounts it at `/tmp`, and exports
that path as `TMPDIR`/`TMP`/`TEMP` (when the workspace itself is rooted at
`/tmp`, the collision-free target is `/var/tmp/tester-unified`); it mounts the
Docker socket for nested contract tests,
selects `/opt/tester-venv`, and verifies uid 1003, the cgroup parent, a 3-CPU
cap, workdir, environment, and all required mounts. Runs are detached with
Docker's init reaper, so orphaned descendants are reaped before they can
accumulate against the gate cgroup's PID ceiling. Their inspect data, logs,
Docker wait status, launch PSI, and job marker are kept below the judged
worktree's ignored `.assay/tester-unified-runs/`.

The image includes Assay's declared build backend and a writable
`/opt/tester-venv`. Internal vbpub `run-gate.toml` lanes omit a versioned
Assay artifact, and run-gate installs the selected worktree's `assay/` source
there at lane runtime. Rebuild this image when Assay's `[build-system]`
requirements change.

Pass `--network none` for an offline gate. The launcher verifies Docker's
accepted network mode and records it in `launch.txt`; without this option it
preserves Docker's existing network selection. See the
[offline gate rationale](docs/DESIGN-GUIDE.md#offline-gates).

The complete option vocabulary is `--workdir PATH`, `--evidence-dir PATH`, `--network none`,
`-h`/`--help`, and the required `--` command separator. The image, uid, CPU
cap, and cgroup environment variable are deliberately not per-run options.

See [why the boundary is shaped this way](docs/DESIGN-GUIDE.md#cockpit-and-gate-boundary)
and the [worked adoption commands](docs/CONSUMERS.md#running-a-project-gate).
