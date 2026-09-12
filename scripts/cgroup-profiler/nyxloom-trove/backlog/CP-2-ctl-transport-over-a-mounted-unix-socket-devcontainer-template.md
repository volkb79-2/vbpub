---
kind: backlog-entry
schema_version: 1
id: CP-2
title: "ctl transport over a mounted Unix socket (devcontainer template mount) as an alternative to docker exec"
status: open
type: "feature"
severity: "low"
provenance: "RG-55 wave plan sec5 P0, D-2, 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

RG-55's chosen transport (plan D-2) is `docker exec cgprofile-host-daemon
cgprofile ctl <verb> --json` — a new process spawn per call, from any
devcontainer/worktree/instance. The plan explicitly notes the daemon "also
listens on a Unix socket inside its container so a mounted-socket transport
can be added later without daemon changes" (D-2) — i.e. the daemon-side
seam already exists (it has to: `ctl` itself talks to `serve` over
`/run/cgprofile/ctl.sock` inside the container, contract §1.1), but nothing
mounts that socket out to a caller today.

## Why cgroup-profiler owns it

The daemon's own socket and its exposure (or non-exposure) to the host are
entirely inside the `cgprofile serve`/ciu-stack definition; a consumer
cannot add this without the daemon publishing the socket path as a bind
mount.

## Proposed contract

Not designed here. Sketch: the daemon's ciu-managed stack (a standalone
root, host-singleton, `scripts/cgroup-profiler/ciu.*.j2`) mounts
`/run/cgprofile/ctl.sock` to a well-known host path (or a devcontainer
template mount, per this entry's title) so `cgprofile ctl` running OUTSIDE
the daemon's own container can connect directly instead of via `docker exec
cgprofile-host-daemon cgprofile ctl ...`. Motivation: one fewer process
spawn per call (relevant at run-gate's 30-second `status` poll cadence,
contract §4.2), and it stops requiring the caller to have `docker exec`
access to the daemon container specifically (useful for a future non-vbpub
consumer, or a locked-down CI runner). Must preserve every safety property
of D-15 (no docker socket inside the daemon, no host-mutating write) — a
mounted ctl socket is a read/write control channel to the daemon and needs
the same scrutiny D-15's reviewer already gave the docker-exec transport.

## Oracles

Not yet written — needs the transport design first. Candidate: a fake-socket
test proving `cgprofile ctl` picks the mounted-socket path when present and
falls back to `docker exec` when it is not (so existing consumers, incl.
run-gate's contract-frozen `docker exec` invocation, keep working
unmodified — this is additive, not a breaking transport change).

## SPEC ownership

`scripts/cgroup-profiler/DESIGN.md`, `ATTACH-GUIDE.md` ("Profiling a
run-gate lane by container id / session" section, RG-55 plan §5 P1), RG-55
contract §1.1 (transport rules would need a new subsection for the
alternative transport, not a change to the existing one).

## Provenance

RG-55 wave plan §5 P0, D-2 ("the daemon also listens on a Unix socket
inside its container so a mounted-socket transport can be added later
without daemon changes"), filed by the P0 controller package, 2026-09-12.

## Updates
