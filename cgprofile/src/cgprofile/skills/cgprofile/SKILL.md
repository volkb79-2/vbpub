---
name: cgprofilectl
description: Profile a container, cgroup or slice's real resource use over time (CPU, memory, IO, swap, pressure) with cgprofilectl, resolve its effective limits, and check for collateral damage to a neighbour. Use for host-contention diagnosis, capacity questions before capping a container, or an unexplained slowdown or OOM; never guess these numbers from docker stats.
---

# cgprofilectl (cgprofile client)

**Mandate.** Do not answer "why is my gate slow" or "is this container
memory-starved" by reading `docker stats`, `free` or `memory.current` by hand.
A cgroup's own `memory.max` is rarely the number that binds it; the ancestor
chain decides. Start a session and read its summary.

## Check the environment first

```bash
command -v cgprofilectl && cgprofilectl --version
cgprofilectl doctor            # socket, group membership, versions, report extra
```

`doctor` names the failing check and the fix (join the socket's group, start
`cgprofiled`, bind-mount `/run/cgprofile`). Exit 3 from any verb means the daemon is
unreachable; the error line carries one cause tag: `socket-not-mounted`,
`socket-missing`, `daemon-refusing`, `socket-permission`, `contract-unsupported`.
`CGPCTL_SOCKET` or `--socket PATH` selects another socket.

## Profile something already running (host daemon)

```bash
id=$(cgprofilectl start --target container:web --meta '{"lane":"my-check"}')
cgprofilectl watch "$id"                  # until the session ends; --json for lines
cgprofilectl stop "$id"                   # prints the summary; add --json for the document
cgprofilectl report "$id" --out ./report  # export + render locally (needs the report extra)
```

- Targets: `containerid:<64hex>`, `container:NAME` (resolved client-side with
  docker), `cgroup:/abs/path`, `slice:NAME.slice` (the last two are read-only
  observation). `start --dry-run` prints the request and starts nothing.
- `cgprofilectl status`, `host`, `sessions [--state live|done|all] [--mine]`,
  `show ID` read daemon state; every one takes `--json` (one document with
  `"schema": 1`).
- `cgprofilectl prune --dry-run` lists what retention would delete; `prune --yes` applies it.
- `config show` reads the daemon's settings; `config set|reset` are uid 0 only.

## Profile one command locally (no daemon)

```bash
cgprofilectl run --phase build -- make          # direct mode: needs cgroupfs
cgprofilectl mark "restoring database"          # inside the command; needs only CGPROFILE_RUN_DIR
cgprofilectl report --run-dir ./cgprofile-runs/run-...
```

In a devcontainer without host cgroups `run` exits 2 with
`direct mode needs cgroupfs; use 'cgprofilectl start' against the host daemon`.
`mark` is stdlib-only and exits 2 when `CGPROFILE_RUN_DIR` is unset.

## Reading results

Open `report.md` first (verdicts and proposals), then `report.html` for the
interactive charts. Read the effective limits in the summary before proposing
a cap. The report needs `pip install 'cgprofile[report]'`; without it
`report ID --export-only DIR` still saves the raw parts.
