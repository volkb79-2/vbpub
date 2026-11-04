---
kind: backlog-entry
schema_version: 1
id: CP-44
title: "Retire the ciu deployment of cgprofile-host-daemon after one release with a dated migration window"
status: open
type: "cleanup"
component: "release"
context_estimate: "small"
provenance: "Operator decision 2026-11-04 (native service from a released wheel, retire the ciu deployment); platform design memo section 4 step 5"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler release"
priority: 3
filed_date: "2026-11-04"
---

## Problem

Two deployments of one daemon (ciu container, host-setup service) would leave an ambiguous owner of `/run/cgprofile/ctl.sock` and the
observer socket, and keep the checkout-dependent upgrade path (`docs/CONSUMERS.md:127-195`; r1002's two uncommitted edits).

## Decision record

Operator decision 2026-11-04: B (native service) is the end state; the ciu deployment of the host daemon is retired after one release.
No alias and no dual-ownership period beyond the dated window.

## Design

1. The first release carrying CP-41/CP-42 documents the migration with a dated window (proposal: one release cycle, never open-ended).
2. Migration per host (r1002 first via netcup NC-05, then the shared host in an idle window): `ciu down` the host profile, install the
   wheel through host-setup (MDT-055), verify sockets, groups and version, run the run-gate and assay probes.
3. After the window: delete `ciu.compose.yml.j2`, the host profile of `ciu.toml.j2`, `ciu.defaults.toml.j2` daemon sections,
   the hand-install steps of `infra/README.md`/`cgprofile.slice` (host-setup owns the slice now), the RUNBOOK hand edits, and the
   `docs/CONSUMERS.md` "Deploy a published daemon image with CIU" chapter. The OCI image stays only if the report tier or a consumer needs it
   (decide in the entry that carries the deletion; do not keep it for the daemon).
4. The daemon refuses to start when another live daemon answers on the socket (CP-34 covers the steal case) so a forgotten container is
   an explicit failure, not a silent takeover.

## Acceptance criteria

- No file under `scripts/cgroup-profiler` names ciu deployment of the host daemon after the window; docs describe only the native service.
- A grep test (like the existing docs-sync tests) pins that the retired chapters and files are gone.
- Live: both hosts migrated and `version.transports` plus the revision label match the pinned wheel.

## Dependencies and order

After MDT-055 is live on both hosts and one release has shipped with the window announcement. Last of the cgprofile packages.

## 2.0 plan amendment (2026-11-04, operator-accepted defaults)

- The migration procedure, the dated window, the rollback inside it (`ciu up --profile host` at the last 1.2.x image) and the session-volume
  decision (drop; retention is 14 days; Q9) are specified in `docs/NATIVE-DEPLOYMENT.md` section 8.
- The single-daemon guard of design point 4 is `cgpd run` preflight step 6 (`docs/CGPCTL-SPEC.md` section 3.1, `NATIVE-DEPLOYMENT.md` section 3.5)
  and ships with CP-48, not with this entry.
- The OCI image is not built for 2.0.0 unless the report tier needs it; deletion list in `NATIVE-DEPLOYMENT.md` section 8 (plan package 9: release
  as 2.0.1, or 2.1.0 if anything is added). The helper and probe images are dropped (section 7).
