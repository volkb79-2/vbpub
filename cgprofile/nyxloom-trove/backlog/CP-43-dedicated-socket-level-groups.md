---
kind: backlog-entry
schema_version: 1
id: CP-43
title: "Dedicated access groups for each cgprofile socket level"
status: fixed
type: "feature"
component: "daemon"
context_estimate: "small"
provenance: "No-Docker-groups amendment to PLAN-2026-11-HOST-SERVICE-SOCKETS.md, 2026-11-07; cgprofile config, installer and doctor contract"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-11-04"
closed_date: "2026-11-07"
closed_reason: "Superseded by the no-Docker-groups amendment in PLAN-2026-11-HOST-SERVICE-SOCKETS.md; cgpd enforces the dedicated-group contract"
---

## Contract

The control and observer sockets use separate, dedicated cgprofile groups.
Neither level may use the group named `docker`, GID 0, the docker GID, or the
other level's GID. `cgpd` validates both levels from flags, saved
`install.toml`, existing `cgpd.toml`, and wizard answers, with no override.
Host-setup owns group uniqueness across services because cgpd can validate only
its own levels.

The control group grants the control API. It is independent of Docker API
access; there is no Docker relay group in the cgpd control-group policy. The
observer group grants only read-only observer operations. `/run/cgprofile` and
its level directories remain root-owned `0755`, so traversal does not grant
socket access by itself.

The installer accepts repeatable `--add-user LEVEL=USER`, validates that each
account exists, adds users with `gpasswd -a`, and records the memberships for
reruns. Reinstalls retain prior users and add new ones. Uninstall never removes
users individually; deleting cgpd-created groups remains an explicit
`--remove-groups` action.

`cgpd doctor groups` checks that both groups exist, neither GID is 0 or the
docker GID, the GIDs are distinct, and current members include the users listed
in `install.toml`. It reports names, GIDs, and member lists. The `cgpd -t`
config-test option validates the same group policy before service startup and
is used by `ExecStartPre`.

## Completed in the no-Docker-groups amendment

- daemon config and installer validation covers each input source and refuses
  unsafe groups without an override;
- `cgpd install --add-user` is repeatable, uses `gpasswd -a`, records users,
  lists additions during dry-run, and does not remove user memberships;
- service doctor reports GIDs and members, including recorded users;
- docs, CLI specs, changelog, and mutation-oriented regression tests describe
  the same contract;
- failed deferred uninstall reports manual group, version-tree, and record
  cleanup steps instead of relying on a cgpd binary that may have been removed.

The 2026-11-04 proposal to use `mdt-docker` for the control socket was
superseded by the no-Docker-groups amendment. Cross-service uniqueness remains
a host-setup check; this service never tries to validate another project's
level groups.
