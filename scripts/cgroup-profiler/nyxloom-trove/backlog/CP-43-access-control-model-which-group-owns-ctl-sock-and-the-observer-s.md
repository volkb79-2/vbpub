---
kind: backlog-entry
schema_version: 1
id: CP-43
title: "Access-control model for the cgprofile sockets under the mdt-docker regime: ctl.sock group, observer group, who may connect"
status: open
type: "docs"
component: "daemon"
context_estimate: "small"
provenance: "Operator decision 2026-11-04 (mdt-docker gid 2751; cgprofile as a product); PROTOCOL.md section 4 (authorisation), CONSUMERS.md native R2 observer, host-setup mdt-cgprofile.conf and mdt-cgprofile-observer.conf"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-11-04"
---

## Problem

Today access to `ctl.sock` is "the group that owns `/run/cgprofile`", which host-setup sets to `docker` (`d /run/cgprofile 0770 root docker`).
The new fixed group `mdt-docker` (gid 2751, mdt-cockpit MDT-053) replaces the host `docker` gid in every cockpit-side access path, so the
model must be restated once: which group guards which socket, who is allowed, who is refused, and what happens off the mdt host.

## Facts (verified in the tree, 2026-11-04)

- `ctl.sock`: `/run/cgprofile/ctl.sock`, mode `0660` root:`<directory gid>`, inside a `0770` directory. The daemon never chooses the group;
  it re-asserts `0770` on the directory and `root:<dir gid>` `0660` on the socket at every start (PROTOCOL.md section 4). A `root:root`
  directory (no host-setup) makes the socket root-only and the daemon logs one INFO line and keeps serving (the exec carrier is unaffected).
- Second gate: `SO_PEERCRED` with `CGPROFILE_ALLOW_UIDS`: uid 0 is always allowed, listed uids are allowed, others get `peer-refused`;
  unreadable credentials are refused when an allowlist is set (fail closed). A malformed allowlist refuses to start.
- Verbs on `ctl.sock` start, stop, mark, place and kill-on-stall for sessions, so it is a control surface (the daemon may write
  `cgroup.kill` only on exact gates-slice container cgroups).
- Observer: `/run/cgprofile/observer/resource-observer.sock` in a root-owned `0751` directory of group `cgprofile-observer`
  (host-setup `mdt-cgprofile.sysusers` + `mdt-cgprofile-observer.conf`), read-only measurement (contracts 2 and 3), `SO_PEERCRED` required,
  the same `CGPROFILE_ALLOW_UIDS` policy. run-gate bind-mounts the directory read-only into the lane and forwards the observer gid as a
  supplementary group; it never mounts `ctl.sock` or the Docker socket into the lane.
- run-gate and assay assert (negatively) that the observer gid differs from the docker-socket gid (`run_gate.py:8700-8712`,
  `assay/tools/resource-observer-gid.sh:56-75`).

## Decision (recommendation, approved by the operator's mdt-docker decision; revisit in review)

| socket | directory | group | mode | who connects |
|---|---|---|---|---|
| `ctl.sock` | `/run/cgprofile` | **`mdt-docker`** (2751) | 0770 dir, 0660 socket | uid 0; members of `mdt-docker` (cockpits and the runner, which already hold the relay socket under that group); optional uid allowlist |
| `resource-observer.sock` | `/run/cgprofile/observer` | `cgprofile-observer` (dynamic system gid) | 0751 dir | gate lanes only (gid forwarded by run-gate); never a cockpit user group |

Justification:

1. Everyone who may drive `ctl.sock` today is a cockpit or runner that mounts the Docker relay socket, i.e. already root-equivalent on the host.
   One group ("may drive the host through the cockpit's relay") is the honest model; tying `ctl.sock` to the host `docker` gid kept it
   coupled to a number that differs per host. `mdt-docker` is fixed, so consumers derive nothing.
2. The daemon needs no change: it already adopts the directory's gid. Only host-setup's tmpfiles rule changes
   (`d /run/cgprofile 0770 root mdt-docker -`).
3. The observer stays on its own group deliberately. Lanes are untrusted-ish code; their access must be measurement-only and must never
   be a group that also opens `ctl.sock` or the Docker API. The existing negative assertion is widened to "observer gid differs from
   BOTH `mdt-docker` and the host `docker` gid" (MDT-053, RG-152, B212).
4. Host `docker` group users who are not in `mdt-docker` lose direct `ctl.sock` access; their tools fall back to the existing exec carrier
   (`docker exec` into the daemon, run_gate.py:688,711) while the container deployment exists, then (native service, CP-44) they
   need `mdt-docker` membership or an `CGPROFILE_ALLOW_UIDS` entry plus a directory group that admits them. Document this one
   consequence in CONSUMERS.

How the groups are created (host-setup, MDT-053 and MDT-055): `units/mdt-docker.sysusers` (`g mdt-docker 2751 - -`) and the existing
`mdt-cgprofile.sysusers` (`g cgprofile-observer - - -`, dynamic gid), applied by `systemd-sysusers`; `units/mdt-cgprofile.conf` becomes
`d /run/cgprofile 0770 root mdt-docker -` and `mdt-cgprofile-observer.conf` keeps `d /run/cgprofile/observer 0751 root cgprofile-observer -`
(both via `systemd-tmpfiles --create`). `install.sh` refuses if gid 2751 is taken by another name, and if the observer gid equals
either docker-class gid.

Off the mdt host (acceptance criterion from the operator, 2026-11-04): cgprofile must keep working where host-setup never ran. The daemon
must not require any group name; a directory owned by any group works (it adopts the gid) and `root:root` degrades to root-only with one
INFO line. Clients (run-gate, assay) derive gids by `stat` of the socket directories they use and never by name.

## Acceptance criteria

- PROTOCOL.md section 4 and CONSUMERS.md carry the table above and the off-mdt rules, and the old "docker group" wording is gone.
- A unit test on the daemon: directory group any gid (2751, 999, 0) is adopted unchanged; the observer socket gid is the observer
  directory's gid.
- A host-setup test (MDT-053) pins the two tmpfiles/sysusers files; a client test (RG-152, B212) pins the widened negative assertion.
- A non-mdt test: daemon started in a tmpdir with an arbitrary group serves a client of that group with no `mdt-docker` anywhere.

## Dependencies and order

Docs and the daemon test can land with CP-42. host-setup changes ride MDT-053. Unblocks RG-152 and B212.

## 2.0 plan amendment (2026-11-04, operator-accepted defaults)

- The exec-carrier fallback in decision point 4 does not survive 2.0: callers outside the ctl group have no fallback by design. `cgpctl doctor`
  reports `socket-permission` and prints the remedy (socket group by `stat`, caller groups, the `allow_uids` key); see `docs/CGPCTL-SPEC.md`
  sections 1 and 2.4.
- A second, daemon-side gate is added on top of the directory group: session ownership by `SO_PEERCRED` uid (`stop` / `export` of another uid's
  session is root-only, `docs/PROTOCOL.md` section 10.5). It is hygiene, not a boundary, because the ctl group is root-equivalent on mdt hosts;
  the documents say so.
- `CGPROFILE_ALLOW_UIDS` becomes the `allow_uids` key of `/etc/cgprofile/cgpd.toml`, reloadable by SIGHUP.
- The client can point at any socket (`--socket`, `CGPCTL_SOCKET`), so a non-default directory or group works with no code assumption.
