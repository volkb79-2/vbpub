---
kind: backlog-entry
schema_version: 1
id: CP-34
title: "_bind unlinks whatever sits at the socket path and silently steals a live daemon's listener; refuse a non-socket and a still-answering peer"
status: open
type: "bugfix"
severity: "low"
component: "daemon"
context_estimate: "small"
provenance: "Pre-release deep review of the 1.1.0 wave branch, 2026-10-27 (fable-cgprofile-20261027); lib/serve.py _bind"
filed_by: "Claude (cgprofile 1.1.0 pre-release review)"
spec_owner: "cgroup-profiler daemon"
priority: 3
filed_date: "2026-10-27"
---

## Observed (code reading)

`SessionServer._bind` does `if os.path.exists(self.socket_path): os.unlink(self.socket_path)` before binding.

- If the path is a regular file (an operator's stray `touch`, a misconfigured bind) it is deleted; if it is a
  directory, `os.unlink` raises `IsADirectoryError`/`PermissionError` out of `serve_forever` with no message naming
  the cause, and the compose `restart: unless-stopped` loop hides it.
- If another daemon instance is still listening there (the singleton is enforced only by the Compose container
  NAME; a `cgprofile serve --socket /run/cgprofile/ctl.sock` started by hand on the host, or during a `ciu up`
  race with a container that has not fully exited, is not), the newer process unlinks the older one's socket node:
  the old daemon keeps running with its sessions, DAMON slots and placement leaves but is unreachable, and every
  `ctl` call now talks to the new one. The slice guard (B4) and the DAMON pool guard do not catch this.

The observer socket path already has a stricter identity check (`_unlink_observer_socket_if_owned`); the control
socket predates it.

## Required contract

- Before unlinking, `lstat` the path: refuse to start (exit 2, message naming the path and its type) when it is not
  a socket node.
- When it IS a socket, attempt one bounded `connect()`; if a peer accepts, refuse to start ("another daemon answers
  at <path>") rather than unlink; only a dead socket node (ECONNREFUSED) is removed.
- Log one line naming what was removed.

## Oracles

A regular file at the socket path refuses with exit 2; a live listener at the path refuses and the original listener
is still connectable afterwards; a dead socket node is removed and the daemon starts.
