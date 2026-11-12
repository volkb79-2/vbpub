---
name: cgprofilectl
description: Use the cgprofile host daemon to inspect or profile a container, cgroup or slice, check access, and diagnose endpoint setup. Use for host resource observations and session control; use cgprofile for local command profiling and reports.
---

# cgprofilectl (host daemon client)

`cgprofilectl` talks to the native `cgprofiled` service over its control socket.
The service installer publishes the endpoint path and group IDs through
`augeaswrap.endpoints`:

```bash
cgprofiled install --wheel PIN --cli-extended-wheel PIN \
  --augeaswrap-wheel PIN --enable
cgprofilectl doctor
cgprofilectl status
```

The client requires `CGPROFILE_CONTROL_SOCKET` unless a command supplies the
explicit `--socket PATH` override. It has no built-in path and does not accept
`CGPCTL_SOCKET`. If the variable is unset, start a new login session or load
the host endpoint file:

```bash
set -a
. /etc/host-endpoints.env
set +a
```

`cgprofilectl doctor` checks the control endpoint environment and compares
`CGPROFILE_OBSERVER_SOCKET` with the path advertised by the daemon. The
service-side `cgprofiled doctor` checks the published fragment against its
configured socket paths and resolved group IDs. See
[`docs/CONSUMERS.md`](../../../../docs/CONSUMERS.md#install-and-run-the-native-service)
for installation and container examples.

Use daemon verbs to observe a running target:

```bash
cgprofilectl start --target container:web --meta '{"lane":"my-check"}'
cgprofilectl sessions --state live
cgprofilectl watch SESSION_ID
cgprofilectl stop SESSION_ID
```

Use `cgprofile` for local `run`, `mark`, `report`, `analyze`, and `skills`
verbs. Control access requires the configured control group; observer-group
membership grants only read-only observer operations.
