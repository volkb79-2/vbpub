---
kind: backlog-entry
schema_version: 1
id: CP-55
title: "cgpd does not implement PROTOCOL 10.4 scope observe: cgroup: and slice: start targets"
status: open
type: "feature"
component: "daemon"
priority: 3
provenance: "cgp2 package 3, 2026-11-04: the package's verb list did not include them; cgpctl already accepts the targets on the wire"
filed_by: "cgp2-pkg3"
filed_date: "2026-11-04"
---

## Problem

`docs/PROTOCOL.md` section 10.4 defines `scope observe` and `cgroup:` / `slice:` targets for `start`. `cgpctl start` sends them, but the contract-2
`start` in `serve.py` handles only the targets of the package 3 verb list, so these targets are not served.

## Expected

The daemon resolves `cgroup:<path>` and `slice:<name>` targets, applies the ownership and allow-list rules of 10.4, and records them as observe sessions.

## Oracles

Goldens for `start` with each target kind; an out-of-hierarchy path is refused; ownership applies as for process targets.
