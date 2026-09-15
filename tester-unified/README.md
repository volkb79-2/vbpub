# tester-unified

`tester-unified` is the vbpub gate environment. The devcontainer is only the
cockpit used to launch it; pytest and the actual gate command run in the image.

Build the image from the repository root:

```bash
docker build -f tester-unified/Dockerfile -t tester-unified:local .
```

Run a gate through the canonical launcher:

```bash
tester-unified/run --workdir run-gate-project -- ./run-gate.py selftest
```

The launcher requires the cockpit-provided
`CGROUP_PARENT_DEV_BACKGROUND`; it never invents a slice. It derives the
Docker host's workspace path from mountinfo, dual-mounts that workspace,
creates a disposable host-backed temp root, mounts it at the non-repository
path `/var/tmp/tester-unified`, and exports that path as `TMPDIR`/`TMP`/`TEMP`;
it mounts the Docker socket for nested contract tests,
selects `/opt/tester-venv`, and verifies uid 1003, the cgroup parent, a 3-CPU
cap, workdir, environment, and all required mounts. Runs are detached and
their inspect data, logs, Docker wait status, launch PSI, and job marker are
kept below the judged worktree's ignored `.assay/tester-unified-runs/`.

The complete option vocabulary is `--workdir PATH`, `--evidence-dir PATH`,
`-h`/`--help`, and the required `--` command separator. The image, uid, CPU
cap, and cgroup environment variable are deliberately not per-run options.

See [why the boundary is shaped this way](docs/DESIGN-GUIDE.md#cockpit-and-gate-boundary)
and the [worked adoption commands](docs/CONSUMERS.md#running-a-project-gate).
