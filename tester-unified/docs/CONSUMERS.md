# tester-unified consumers

## Running a project gate

From a vbpub checkout or linked worktree, keep the gate command after `--` so
its argv is passed without a second shell parse:

```bash
tester-unified/run --workdir run-gate-project -- ./run-gate.py selftest
tester-unified/run --workdir run-gate-project -- ./run-gate.py --base BASE_SHA assay-r1
```

For a linked worktree, invoke that worktree's launcher or give an absolute
workdir. The launcher derives the enclosing workspace bind and records the
exact commit, temp root, and container facts it used.

To place evidence somewhere else, use an explicit path:

```bash
tester-unified/run --evidence-dir /workspaces/vbpub/.assay/my-gate \
  --workdir run-gate-project -- ./run-gate.py doctor
```

Do not invoke pytest in the cockpit and do not reproduce the launch with a
hand-written `docker run`. If the launcher refuses pressure, cgroup, mount,
socket, image, or concurrency preflight, correct that fact and retry; the
refusal is not a product pass or failure.

See [what the launcher guarantees](../README.md) and
[why those guarantees are required](DESIGN-GUIDE.md#cockpit-and-gate-boundary).
