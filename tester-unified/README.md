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
`CGROUP_PARENT_DEV_GATES`; it never invents a slice. It derives the
Docker host's workspace path from mountinfo, dual-mounts that workspace,
creates a disposable host-backed temp root, mounts it at `/tmp`, and exports
that path as `TMPDIR`/`TMP`/`TEMP` (when the workspace itself is rooted at
`/tmp`, the collision-free target is `/var/tmp/tester-unified`); it mounts the
Docker socket for nested contract tests,
selects `/opt/tester-venv`, and verifies uid 1003, the cgroup parent, a 3-CPU
cap, workdir, environment, and all required mounts. Runs are detached and
their inspect data, logs, Docker wait status, launch PSI, and job marker are
kept below the judged worktree's ignored `.assay/tester-unified-runs/`.

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
