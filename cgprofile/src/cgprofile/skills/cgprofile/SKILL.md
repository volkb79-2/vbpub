---
name: cgprofile
description: Profile a container, cgroup or slice's real resource use over time (CPU, memory, IO, swap, pressure) with cgprofile and cgprofilectl, resolve its effective limits, and check for collateral damage to a neighbour. Use for host-contention diagnosis, capacity questions before capping a container, or an unexplained slowdown or OOM; never guess these numbers from docker stats.
---

# cgprofile (end-user client) and cgprofilectl (daemon client)

**Mandate.** Do not answer "why is my gate slow" or "is this container
memory-starved" by reading `docker stats`, `free` or `memory.current` by hand.
A cgroup's own `memory.max` is rarely the number that binds it; the ancestor
chain decides. Start a session and read its summary.

Two commands from one wheel: `cgprofilectl` talks to the host daemon (`status`,
`host`, `sessions`, `show`, `start`, `stop`, `watch`, `prune`, `config`, `doctor`);
`cgprofile` is the local client (`run`, `mark`, `report`, `analyze`, `skills`).

## Check the environment first

```bash
command -v cgprofilectl && cgprofilectl --version
cgprofilectl doctor            # socket, group membership, versions, report extra
```

`doctor` names the failing check and the fix (join the socket's group, start
`cgprofiled`, or load the host endpoint record). Exit 3 from a connected verb means the daemon is
unreachable; the error line carries one cause tag: `socket-not-mounted`,
`socket-missing`, `daemon-refusing`, `socket-permission`, `contract-unsupported`.
`cgprofiled install` publishes `CGPROFILE_CONTROL_SOCKET` and
`CGPROFILE_OBSERVER_SOCKET`. Client verbs require the control variable unless
`--socket PATH` is supplied; `CGPCTL_SOCKET` is removed. An unset endpoint exits
2 and names `CGPROFILE_CONTROL_SOCKET`. In an existing shell, load the published
values with `set -a; . /etc/host-endpoints.env`.

## Profile something already running (host daemon)

```bash
id=$(cgprofilectl start --target container:web --meta '{"lane":"my-check"}')
cgprofilectl watch "$id"                  # until the session ends; --json for lines
cgprofilectl stop "$id"                   # prints the summary; add --json for the document
cgprofile report "$id" --out ./report     # export + render locally (needs the report extra)
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
cgprofile run --phase build -- make             # direct mode: needs cgroupfs
cgprofile mark "restoring database"             # inside the command; needs only CGPROFILE_RUN_DIR
cgprofile report --run-dir ./cgprofile-runs/run-...
cgprofile analyze ./cgprofile-runs/run-...      # the proposals only, no files
```

In a devcontainer without host cgroups `run` exits 2 with
`direct mode needs cgroupfs; use 'cgprofilectl start' against the host daemon`.
`mark` is stdlib-only and exits 2 when `CGPROFILE_RUN_DIR` is unset.

## Reading results

Open `report.md` first (verdicts and proposals), then `report.html` for the
interactive charts. Read the effective limits in the summary before proposing
a cap. The report needs `pip install 'cgprofile[report]'`; without it
`cgprofile report ID --export-only DIR` still saves the raw parts.
