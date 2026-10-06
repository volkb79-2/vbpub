# tester-unified

`tester-unified` is the vbpub gate environment. The devcontainer is only the
cockpit used to launch it; pytest and the actual gate command run in the image.

Build the image from the repository root:

```bash
docker build -f tester-unified/Dockerfile -t tester-unified:local .
```

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
  requirement ever reaches pip, and the image installs the RELEASED wheel
  instead: `fetch-cli-extended.py` downloads the asset PINNED in the Dockerfile
  (`CLI_EXTENDED_WHEEL_URL` + `CLI_EXTENDED_WHEEL_SHA256` defaults, currently the
  released 0.2.0 wheel; to bump, copy `url`/`sha256` from
  `cli-extended-latest/latest.json` into those defaults and rebuild).
  `--build-arg CLI_EXTENDED_RESOLVE=latest` is the explicit opt-in that reads the
  pointer instead (add `--no-cache`).
  `--build-arg CLI_EXTENDED_RESOLVE=local` is the ZERO-RELEASE path (three modes
  in all: `pinned` default, `latest`, `local`; see `docs/BOOTSTRAP-FROM-ZERO.md`):
  with no release to fetch, build the wheel with cmru's own wheel builder
  (`CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh` logs its
  sha256 and leaves it in `libraries/cli-extended/dist/`), copy exactly that one
  `cli_extended-*.whl` into `tester-unified/local-wheel/` (git-ignored) and pass
  `--build-arg CLI_EXTENDED_WHEEL_SHA256=<that sha256>` (the existing arg). The
  digest is checked with `sha256sum`, the wheel is installed `--no-index
  --no-deps` before cmru, and the build asserts it carries a `+local` version
  (pinned/latest assert the opposite). It is never a fallback: a missing release
  in `pinned` mode simply fails. Redirects must stay https on a GitHub
  host. Either way it requires and verifies the sha256, and the wheel is installed
  `--no-index --no-deps` BEFORE cmru. cmru is built offline from the COPYed
  `cmru/` and `libraries/worktree/` sources (`pip wheel --no-index --no-deps
  --no-build-isolation`, in a throwaway venv holding only its pinned build
  backends) with a `+tester.unified` local version, and the build asserts that
  `cli_extended` and `worktree` import from `/opt/tester-venv`, that cmru is the
  local build and that cli-extended is the release. Gate lanes put only
  `src:../libraries/worktree/src` on `PYTHONPATH`; cli_extended always comes from
  the installed release. The repo-root `.dockerignore` whitelists exactly the
  files this needs.

Run a gate through the canonical launcher:

```bash
tester-unified/run --workdir run-gate-project -- ./run-gate.py selftest
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
