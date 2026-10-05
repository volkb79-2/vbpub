# RG-84 live acceptance — 2026-10-05

Source: `run-gate.py` rev 55, worktree commit `6882167d0e99`.

Image: `tester-unified:local`, image ID
`sha256:35257388242c02f9810b386fc2206cb209a5fa545a5d034c5aa2ecbbd44e6ace`.
Both probes used a detached container under `dev-gates.slice`, uid/gid
`1003:994`, the image's `/home/tester` and XDG identity, dual mounts of the
checkout at its physical and `/workspaces/vbpub` paths, and
`git config --global safe.directory '*'`. Memory PSI immediately before the
PID 1 probe was `full avg60=0.57`, below the host limit of 2.

## PID 1 refusal

The container ran the run-gate Python command as PID 1, without Docker init.
It exited before lane dispatch. `docker wait` returned `2`, and the log was:

```text
run-gate: run-gate is PID 1 with no init: start the container with `--init` (Docker) or `init: true` (Compose)
{"admission": null, "assay_outcome": null, "exit_code": null, "log_path": null, "members": null, "reason": "pid1-without-init", "verdict": "ERROR"}
```

Docker inspection confirmed `HostConfig.Init=<nil>` and
`HostConfig.CgroupParent=dev-gates.slice`.

## Process-limit event

A scratch Git checkout declared a `bare-host` command lane that attempted to
start 256 short-lived child processes, reaped every child it started, and
returned zero. The detached tester container used `--init` and
`--pids-limit=64`; Docker inspection confirmed that limit and the
`dev-gates.slice` parent. Run-gate reported:

```text
run-gate: lane 'lowpids': cgroup resource event during execution (pids.events max +1; lane verdict forced to ERROR)
run-gate: lane 'lowpids' verdict ERROR; exit_code 0; reason lane 'lowpids': cgroup resource event during execution (pids.events max +1; lane verdict forced to ERROR)
RUN_GATE_EXIT=2
```

The scratch lane's history record retained `verdict: ERROR`, raw `exit_code: 0`,
and the `pids.events max +1` reason. The container was removed after log and
history inspection. The scratch checkout and its history were removed too.

These live probes pass the PID 1 and process-limit oracles. The registered
`selftest` lane, including its cgroup parser and `memory.events:oom_kill` unit
cases, remains pending until the CMRU tester-gate launcher supports an init
reaper (KI-52).
