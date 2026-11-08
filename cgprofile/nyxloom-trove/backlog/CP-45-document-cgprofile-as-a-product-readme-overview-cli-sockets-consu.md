---
kind: backlog-entry
schema_version: 1
id: CP-45
title: "Document cgprofile as a product: README overview of what it is, CLI, sockets, consumers, access and daemon protection"
status: fixed
type: "docs"
component: "docs"
context_estimate: "small"
provenance: "Operator request 2026-11-04 (document cgprofile as a product); README, docs/PROTOCOL.md, docs/CONSUMERS.md, ATTACH-GUIDE.md and DESIGN.md exist but no single overview"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler docs"
priority: 3
filed_date: "2026-11-04"
---

## Problem

The facts about cgprofile are spread over README (one-shot profiling story), PROTOCOL.md (wire and authorisation), CONSUMERS.md (adoption,
observer, windows), ATTACH-GUIDE.md (gate wiring) and DESIGN.md (module contracts). A newcomer cannot learn in one page what the product is,
which sockets exist, who may use them, who consumes it and how the daemon is protected. The operator asked for it as a product.

## Design (README overview, linking, not duplicating)

A top-of-README "Product overview" section, each part two to six lines and a link to the normative doc:

1. **What it is.** Profiles a container or cgroup over time (CPU, memory, IO, swap, pressure, DAMON), resolves effective limits over the
   whole ancestor chain, correlates phase marks, reports; also watches neighbours; also a daemon that run-gate and assay talk to per lane.
2. **CLI.** The `cgprofile` entry point: `run`, `attach`, `mark`, `report`, `targets`, `doctor`, `serve`, `ctl` (daemon verbs: sessions,
   watch, placement, report), `--version` identity; a one-line example each; the observer client lives in consumers, not the CLI.
3. **Sockets.** `ctl.sock` (`/run/cgprofile/ctl.sock`, control, contract 1) and the observer socket
   (`/run/cgprofile/observer/resource-observer.sock`, read-only measurement, contracts 2 and 3), plus the permanent `docker exec` carrier;
   table of paths, modes, groups. Link CP-43's model.
4. **Consumers.** run-gate (ctl sessions, observer for lanes), assay (observer contract 3), mdt host-setup (installs, pins and protects the
   service, owns the directory and groups), devcontainers and runners (bind `/run/cgprofile`). One line per consumer with its contract.
5. **Access.** Directory group decides who may connect; `SO_PEERCRED` plus `CGPROFILE_ALLOW_UIDS`; observer is a separate group by design;
   off-mdt behavior (root-only when the directory is root:root).
6. **Protection of the host.** The service unit's slice and limits (CP-42), the `cgroup.kill` rule (exact gates-slice container cgroups only),
   restart policy; what a missing or refusing daemon looks like to a lane.
7. **Install.** Native service via host-setup (MDT-055) after the window; wheel and image identity.

## Acceptance criteria

- The README section exists and each statement cites its source file; a docs-sync test checks that the socket paths, group names and verb
  names in the section match PROTOCOL.md and `cgprofile --help` (so it cannot rot).
- No contradicting text remains in README/CONSUMERS (the old "docker group" wording is handled by CP-43).

## Dependencies and order

Write after CP-42 and CP-43 settle the facts (protection and groups); the CLI and consumer parts can land earlier.

## 2.0 plan amendment (2026-11-04, operator-accepted defaults)

- The overview describes the 2.0 product, not the 1.x one: CLI part 2 lists the `cgpctl` verbs of `docs/CGPCTL-SPEC.md` and `cgpd`; sockets part 3
  drops the exec carrier and names ctl contract 2 (`PROTOCOL.md` section 10); install part 7 points at `docs/NATIVE-DEPLOYMENT.md`.
- Delivery moves into CP-49 (plan package 5) so it is written once, after the CLI and daemon exist; the docs-sync test additionally compares the
  README verb list with the `cgpctl --help` surface and the config key table with `cgpd config check`'s key set.

## Updates

**2026-11-04** FIXED in cgp2 package 5 (branch feat-cgp2-pkg5-20261104): the README "Product overview" has the seven parts, each linking its normative
document. `tests/test_docs_sync.py` compares the README verb tables with the `cgpctl` and `cgpd` cli-extended registries (names and documented flags),
the config-key table with the `daemon_config` schema (keys, defaults, reload versus restart), and the socket paths and the `cgprofile-observer` group
with `ctlwire`, PROTOCOL.md and NATIVE-DEPLOYMENT.md, with a mutation self-test per comparison.
