# cgroup-profiler design guide

This document records why the RG-55 daemon behaves as it does. The user-facing
feature list is in [`README.md`](../README.md); worked adoption belongs in
[`CONSUMERS.md`](CONSUMERS.md).

## Daemon safety and placement

The daemon is a host-view observer. D-15 deliberately excludes host mutation:
`serve` has no capability-changing option, does not import `TempCaps`, has no
Docker socket, and writes only its session volume plus the DAMON admin sysfs
state required to observe DAMON. A consumer that needs a cap change must use a
separate, explicitly authorized tool; adding a hidden fallback here would make
the observer change the workload it is measuring.

Host visibility does not require joining host namespaces. Both daemon and
one-shot helper keep private PID/cgroup namespaces and bind the host cgroup
tree read-only; the host proc tree is also bound read-only at `/hostproc` and
selected through `CGPROFILE_PROC_ROOT`. The daemon checks that PID 1 in that
view belongs to a PID namespace distinct from the daemon's before serving.
Host-visible PIDs from `/hostproc` are useful for reading process details, but
they cannot be matched against `cgroup.procs` from the private PID namespace:
the kernel translates host tasks to PID 0 there. Accordingly, container names,
labels, and helper-mode `self` are resolved on the Docker-aware caller to full
container IDs; the observer locates those IDs in the read-only cgroup tree.
Token-scoped sessions find the exact token in host `/proc` and walk its
process descendants there, rather than treating a namespace-local PID as a
host PID. This keeps observations explicit and fail-closed without namespace
sharing or a Docker socket in the daemon. Token roots remain constrained to
the selected target cgroup; only their descendants may be attributed after
moving elsewhere. The DAMON sysfs and session directory remain the only
intended writes.

### Private namespaces and host views

`self` in helper mode means the container that invoked the CLI, not the
short-lived helper. The driver resolves its own full Docker ID before
re-exec; the helper can then locate that container by its cgroup leaf without
guessing from `/proc/self/cgroup`. A caller-visible `pid:N` is accepted only
when its cgroup namespace matches the caller's. The caller carries the PID-
and cgroup-namespace inodes, namespace-local PID, process start time, and
namespace-relative cgroup path under the caller container ID. In the
helper's host `/proc` view, the resolver matches those facts and exact cgroup
membership, rechecks the process start time, and requires exactly one match
before returning the helper-visible PID for proc sampling and DAMON. A
mismatched namespace, changed or unavailable identity, unsafe path, or
ambiguous match is a refusal, not a guess from a numeric PID. See the worked
command in
[`CONSUMERS.md`](CONSUMERS.md#resolve-a-target-from-a-cockpit).

The short-lived helper-placement probe runs under the injected interactive
slice only after confirming that it matches the cockpit container's actual
Docker parent. It asks host systemd for `LoadState`, `FragmentPath`, and
`ControlGroup` over a read-only DBus socket mount and confirms the host cgroup
directory exists. This is necessary because an unknown slice can be silently
materialized as a transient unit; checking only for a cgroup directory would
certify the unsafe state the probe is meant to catch.

The DAMON pool treats the `nr_kdamonds` value read at pool creation as an
ownership boundary. Indices below that baseline are foreign. New sessions use
only indices proven to have been created after the baseline, and teardown
touches only indices that the pool actually handed out. If the baseline cannot
be read, acquisition refuses; index zero is never a placeholder default.

The same rule applies to a bare session: it must prove that its requested
index is the first slot beyond the observed count before it configures or turns
the kdamond on. This keeps a pre-existing monitor on even when a new session
fails part-way through setup.

## Session identity and start semantics

The non-null token is the opt-in idempotency key `(container_id, token)` used by
run-gate retries. `token = null` has no registry key: two starts represent two
independent observations and compete for `max_sessions`.

Start reads target, host, slice, and attributed PIDs once, then records those
values as sample zero before the sampling thread begins. This preserves the
baseline and makes an immediate stop a valid one-sample summary. Monotonic
sample timestamps are retained; `cores_max` uses each positive adjacent
timestamp delta and becomes null when a needed timestamp or delta is not
usable.

## Contract boundary

The wire contract is major version 1 and summary documents use schema 1. A
reachable Unix socket is not enough: `ctl` validates that the response is an
object with a boolean `ok`, contract major 1, and the shape for the requested
verb before printing it. Valid daemon errors remain exit 2; malformed or
incompatible peer responses are daemon faults (exit 3).

```json
{
  "ok": true,
  "contract": 1,
  "cgprofile": "1.0.0",
  "daemon": {
    "name": "cgprofile-host-daemon",
    "started_at": "2026-09-12T10:15:00Z",
    "damon": "available",
    "damon_default": "on",
    "sessions_live": 0,
    "max_sessions": 16
  }
}
```

`ctl stop` reports `series.damon: null` when DAMON was off, unavailable,
restarted before a sample, or otherwise has no persisted classified sample.
It reports `"damon.jsonl"` only when that evidence exists. No response field
turns an unreadable metric into zero.

## Release identity

The daemon image version is the exact `cgprofile-v<version>` CMRU tag at
`HEAD`. The build script reads the prefix from `cmru.toml`, rejects ambiguous
or malformed matching tags, and supplies the resolved version to the OCI tag,
image label, CLI identity, and daemon self-description. This keeps build
metadata and live probes on one release fact rather than asking operators to
type a second version. Untagged local images are marked `0.0.0-dev`; publish
without an exact tag or explicit manual version refuses. No version lookup
depends on network access at runtime.

## Decisions intentionally left outside this repair

This repair does not invent policy for the S1–S5 follow-up surfaces. Those
decisions remain with the controller and their existing design records.
