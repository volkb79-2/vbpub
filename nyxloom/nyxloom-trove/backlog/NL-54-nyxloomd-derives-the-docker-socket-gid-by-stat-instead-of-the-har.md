---
kind: backlog-entry
schema_version: 1
id: NL-54
title: "nyxloomd derives the Docker socket gid by stat of the socket it mounts instead of the hard-coded docker_gid = 994"
status: open
type: "bugfix"
severity: "medium"
component: "nyxloomd"
provenance: "Operator decision 2026-11-04 (mdt-docker gid 2751; every consumer derives the gid by stat on the socket it mounts); platform design memo section 2"
filed_date: "2026-11-04"
---

## Observed mechanism

`nyxloom/nyxloomd/ciu.defaults.toml.j2:27` hard-codes `docker_gid = 994` and `ciu.compose.yml.j2:63` renders it as the daemon container's
`group_add` ("sock access for agent gates"). 994 is a number from one host's `docker` group. With the fixed estate group `mdt-docker` (gid 2751)
on the host relay socket (mdt-cockpit MDT-053) the value is wrong on the cockpit-facing host, and on any other host it is wrong whenever that host's
docker gid differs.

## Decision record

Operator decision 2026-11-04 (binding): consumers derive the docker gid by `stat` on the socket they mount; ciu removes its `DOCKER_GID` key (CIU-160).
Requirement from the operator: repo tools must keep working when a user does NOT use the mdt cockpit.

## Expected

- The compose template takes the gid from a value ciu computes by `stat` of the effective mounted socket (the ciu derive function of CIU-160),
  or the render step reads the socket's gid itself; no `docker_gid` default in `ciu.defaults.toml.j2` and no literal number anywhere in the tree.
- A stale `docker_gid` key in an existing nyxloomd config is refused with a message naming the replacement (no alias).
- Document the rule once in `nyxloomd` docs and `CONSUMERS.md`: the daemon needs only the docker access of its environment.

## Acceptance

- A render test with a fake socket of gid 2751 and another with gid 118 produces `group_add` with exactly that gid; a grep test finds no literal 994.
- **Non-mdt path:** a render test on a plain host (socket `/var/run/docker.sock`, any gid, no mdt paths) works; nothing in the template names
  `mdt-docker` or an mdt group.
- Live: nyxloomd reaches the relay socket in the cockpit setup without any host-side number being configured.

## Dependencies and order

After CIU-160 (the derive function) and mdt-cockpit MDT-053/054 for the live check; package 4 of the docker-gid migration.
