# tester-unified consumers

## Running a project gate

From a vbpub checkout or linked worktree, keep the gate command after `--` so
its argv is passed without a second shell parse:

```bash
tester-unified/run --workdir run-gate-project -- ./run-gate.py selftest
tester-unified/run --workdir run-gate-project -- ./run-gate.py --base BASE_SHA assay-r1
```

Assay's offline wheel/self-hosting phases can run through the same launcher:

```bash
tester-unified/run --network none --workdir "$WORKTREE/assay" -- \
  bash "$WORKTREE/assay/tools/tester-unified-gate.sh" --inner "$WORKTREE"
```

`WORKTREE` is the absolute, committed checkout to judge. The `--inner` driver
builds its exact-OID wheel and temporary environments within this container;
it launches no second outer gate container. Preserve the resulting launcher
directory and consume it with `assay analyze launcher DIRECTORY
--expected-commit HEAD` or as a named input to `assay analyze receipt`.
`--network none` is the only explicit network selection, is verified against
Docker inspect and recorded in `launch.txt`. See
[why offline gates share the launcher](DESIGN-GUIDE.md#offline-gates).

For a linked worktree, invoke that worktree's launcher or give an absolute
workdir. The launcher derives the enclosing workspace bind and records the
exact commit, temp root, and container facts it used.

The image must be rebuilt from the same checkout after changing Assay's
`[build-system]` requirements. Internal vbpub lanes leave `assay_command` and
`pins` out of `run-gate.toml`; run-gate installs the selected worktree's
`assay/` source into the image's writable `/opt/tester-venv` and the verdict
records the runtime version and source commit.

To place evidence somewhere else, use an explicit path:

```bash
tester-unified/run --evidence-dir /workspaces/vbpub/.assay/my-gate \
  --workdir run-gate-project -- ./run-gate.py doctor
```

Do not invoke pytest in the cockpit and do not reproduce the launch with a
hand-written `docker run`: the launcher owns the detached `--init` reaper as
well as the cgroup, identity, mounts, and wait/log collection. If the launcher
refuses pressure, cgroup, mount,
socket, image, or concurrency preflight, correct that fact and retry; the
refusal is not a product pass or failure.

See [what the launcher guarantees](../README.md) and
[why those guarantees are required](DESIGN-GUIDE.md#cockpit-and-gate-boundary).
