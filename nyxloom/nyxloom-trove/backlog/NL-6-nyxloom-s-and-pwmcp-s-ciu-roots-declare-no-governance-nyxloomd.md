---
kind: backlog-entry
schema_version: 1
id: NL-6
title: "nyxloom's and pwmcp's ciu roots declare no [governance]: nyxloomd/ntfy/pwmcp-instance containers start unconfined on the shared host (dstdns's root declares it); v7 stop-gap until ciu v8 [ciu] inherit"
status: open
type: "bugfix"
severity: "medium"
component: "ciu-config"
provenance: "ciu v8 design session 2026-09-03: ciu/docs/CIU-V8-HANDOFF-2026-09-03.md (v7 question), proposal rev 3.2 §4.10 item 22; origin: nyxloom P97-P99 held design prompt (meta-root + autostart, rejected)"
spec_owner: "ciu docs/SPEC.md S15.10; root AGENTS.md host cgroup placement; SPEC-V8 draft.5 S3.1.5"
filed_date: "2026-09-03"
---

## Observed mechanism and reproduction

ciu v7 applies resource governance only from a root's own `[governance]` table (ciu `docs/SPEC.md` S15.10 global default, shallow-merged with `[<root>.governance]` per stack); v7 roots are islands — nothing inherits between `vbpub/nyxloom`, `vbpub/pwmcp` and dstdns.

Source check 2026-09-03: `grep -c governance` on `nyxloom/ciu.global.defaults.toml.j2`, `nyxloom/ciu.global.toml`, `pwmcp/ciu.global.defaults.toml.j2`, `pwmcp/ciu.global.toml.j2` and on `nyxloomd/ciu.toml`, `ntfy/ciu.toml`, `pwmcp-instance/ciu.toml`, `pwmcp/ciu.toml` → 0 hits each. dstdns's site file declares it (`/workspaces/dstdns/ciu.global.toml.j2:110`: `[governance] enabled = true cgroup_parent = "dev-background.slice"`, plus the KSM opt-in).

Consequence: every container `ciu up` starts for nyxloom's stacks (`nyxloomd`, `ntfy`, `pwmcp-instance`) and for pwmcp's carries no `cgroup_parent`, no memory cap and no CPU weight from ciu — Docker's unconfined default — on a host that runs production game servers next to dev work (memory `host-shared-with-production-load-rule`; root `AGENTS.md` "Host cgroup placement for spawned containers": *any container you or a tool starts must be placed on the host, never left at Docker's unconfined default*). The run-gate side (`tester-unified` gate containers) is governed separately through `$CGROUP_PARENT_DEV_BACKGROUND` and lane `resources`; this entry is about the DEPLOYED stacks.

Reproduction: `cd /workspaces/vbpub/nyxloom && ciu render` → the rendered compose carries no `cgroup_parent`/`mem_limit`/`cpu_shares` on any service; after `ciu up`, `docker inspect <nyxloomd container> --format '{{.HostConfig.CgroupParent}}'` prints an empty string.

## Why nyxloom owns it

The mechanism exists in ciu v7 (S15.10); the root simply does not declare it — a consumer configuration gap, not a ciu defect. ciu v7 is maintenance-only by the 2026-09-03 decision (v8 is built as the new subproject `vbpub/ciu8`); v8's `[ciu] inherit` (SPEC-V8 draft.5 S3.1.5; proposal rev 3.2 §4.3.14, §4.10 item 22) will let one vbpub-root file carry the table for every subproject, and this entry is the v7 stop-gap it retires. The same edit applies to `pwmcp/` (a separate root): do it in the same commit or note there.

Origin: the nyxloom P97–P99 thread's held design prompt ("monorepo-wide shared governance defaults + worktree-scoped tester-unified stacks via a meta-root with `autostart`") — answered in the ciu v8 design set: no meta-root, no `autostart`; `[ciu] inherit` for policy, a per-project two-file tester stack over `tester-unified/` for the tester (`ciu/docs/CIU-V8-HANDOFF-2026-09-03.md`).

## Proposed contract

Add a `[governance]` table to `nyxloom/ciu.global.defaults.toml.j2` (committed defaults layer, so every worktree instance gets it) — and the same to `pwmcp/ciu.global.defaults.toml.j2` — copied from dstdns's: `enabled = true`, `cgroup_parent = "dev-background.slice"` (or `""`, which ciu v7 resolves from `$CGROUP_PARENT_DEV_BACKGROUND`), the KSM opt-in if wanted, and per-stack `[<root>.governance]` memory caps sized for `nyxloomd`/`ntfy`/`pwmcp-instance`. Mark the table with a one-line comment: *copied per root; retired by ciu v8 `[ciu] inherit`*. No new ciu v7 feature; no meta-root; no change to run-gate's gate governance.

## Oracles

- `ciu render` in `nyxloom/`: every non-exempt service block of the rendered compose carries `cgroup_parent` equal to the declared slice and a `mem_limit` (ciu v7 S15 injection); `ciu check` passes the governance stage (slice `LoadState=loaded`, `memory_min` headroom).
- Live: after `ciu up`, `docker inspect --format '{{.HostConfig.CgroupParent}}'` of the `nyxloomd` and `ntfy` containers prints the slice; `docker inspect --format '{{.HostConfig.Memory}}'` is non-zero.
- Controlled wrong implementation: the same table with `enabled = false` (or the table added only to the gitignored site layer, so a fresh worktree lacks it) must make the first oracle fail — the oracle is over the rendered compose of a FRESH worktree, not the primary checkout.
- pwmcp: the same two oracles on `pwmcp/`.

## SPEC ownership

ciu v7 `docs/SPEC.md` S15.10 (global default `[governance]`), S15.13 (unknown-key WARN), S15.19 (memory profile); root `AGENTS.md` "Host cgroup placement for spawned containers"; the v8 replacement is SPEC-V8 draft.5 S3.1.5 (`[ciu] inherit`) and S16.11.1 (a zero-instance monorepo root may carry `[governance]`).
