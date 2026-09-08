---
kind: backlog-entry
schema_version: 1
id: NL-6
title: "nyxloom's and pwmcp's ciu roots declare no [governance]: nyxloomd/ntfy/pwmcp-instance containers start unconfined on the shared host (dstdns's root declares it); v7 stop-gap until ciu v8 [ciu] inherit"
status: fixed
type: "bugfix"
severity: "medium"
component: "ciu-config"
provenance: "ciu v8 design session 2026-09-03: ciu/docs/CIU-V8-HANDOFF-2026-09-03.md (v7 question), proposal rev 3.2 §4.10 item 22; origin: nyxloom P97-P99 held design prompt (meta-root + autostart, rejected)"
spec_owner: "ciu docs/SPEC.md S15.10; root AGENTS.md host cgroup placement; SPEC-V8 draft.5 S3.1.5"
filed_date: "2026-09-03"
closed_date: "2026-09-08"
closed_reason: "nyxloom-P103: declared [governance] on both nyxloom and pwmcp ciu roots (dev-background.slice, per-stack mem_limit overrides) and retired the fictional nyxloom.slice from ntfy/nyxloomd's own templates and the plain-compose sibling; the ciu governance mechanism itself needed no change."
---

## Observed mechanism and reproduction

**Corrected 2026-09-08 by nyxloom-P103** — this entry's core claim (neither root
declared `[governance]`, so nyxloomd/ntfy/pwmcp-instance started unconfined) was
TRUE and is now FIXED. Three of the entry's own supporting claims were,
however, FALSE at the `input_revision` this package fixed against, and must
not survive unchanged now that the entry is closed:

1. **"the rendered compose carries no `cgroup_parent` on any service" — FALSE.**
   `ntfy/ciu.compose.yml:14` carried `cgroup_parent: nyxloom.slice`, emitted by
   ntfy's OWN `{% if ntfy.runtime.cgroup_parent %}` template block (now
   deleted). The service was not unplaced — it was placed in a slice that was
   never installed anywhere on the host, so systemd auto-created an unbounded
   transient cgroup for it (the fail-open case
   `docs/plan-resource-governance.md` documents). `nyxloomd`, `pwmcp-instance`
   and both pwmcp stacks genuinely carried no placement at all, as claimed.
2. **"`ciu render` → the rendered compose" — FALSE.** `ciu render` renders
   **TOML only** (`ciu/src/ciu/deploy.py:1677-1693`); it never writes a compose
   file or an overlay. The real oracle is `ciu up --dry-run`, which does render
   the compose and the governance overlay (`<stack>/.ciu/ciu.compose.overlay.yml`,
   `composefile.py:1407-1413`) without starting any container.
3. **"`ciu check` passes the governance stage" as an oracle — a hollow
   oracle.** That stage is shape-only (`deploy.py:2746-2755`) and **already
   passed** at `input_revision`, with zero governance declared anywhere. A
   check that passes on the broken state proves nothing about the fix.

**The mechanism this entry's own "Proposed contract" missed entirely:** ciu
governance **never overwrites a compose key the stack author already
emits** (`governance.py:1016-1038`; `SPEC.md:2917-2923` states it normatively).
Adding the root `[governance]` table alone (as originally proposed) would have
left `ntfy` — the one service actually running — still pinned to the fictional
`nyxloom.slice`, because its own template already set `cgroup_parent`. Fixing
this required deleting the author-set Jinja block and TOML keys in
`ntfy/{ciu.compose.yml.j2,ciu.defaults.toml.j2}`, `nyxloomd/{ciu.defaults.toml.j2,ciu.toml}`,
and inlining the same caps into `ntfy/docker-compose.yml` (the plain-compose
path the live container was actually deployed from, which receives no ciu
overlay at all) — not merely declaring the root table.

Original reproduction, unchanged and still accurate for the broken state:
`grep -c governance` on `nyxloom/ciu.global.defaults.toml.j2`,
`pwmcp/ciu.global.defaults.toml.j2` and every stack's `ciu.toml` → 0 hits each,
while dstdns's site file already declared it. After `ciu up`,
`docker inspect <nyxloomd container> --format '{{.HostConfig.CgroupParent}}'`
printed an empty string.

## Why nyxloom owns it

The mechanism exists in ciu v7 (S15.10); the root simply does not declare it — a consumer configuration gap, not a ciu defect. ciu v7 is maintenance-only by the 2026-09-03 decision (v8 is built as the new subproject `vbpub/ciu8`); v8's `[ciu] inherit` (SPEC-V8 draft.5 S3.1.5; proposal rev 3.2 §4.3.14, §4.10 item 22) will let one vbpub-root file carry the table for every subproject, and this entry is the v7 stop-gap it retires. The same edit applies to `pwmcp/` (a separate root): do it in the same commit or note there.

Origin: the nyxloom P97–P99 thread's held design prompt ("monorepo-wide shared governance defaults + worktree-scoped tester-unified stacks via a meta-root with `autostart`") — answered in the ciu v8 design set: no meta-root, no `autostart`; `[ciu] inherit` for policy, a per-project two-file tester stack over `tester-unified/` for the tester (`ciu/docs/CIU-V8-HANDOFF-2026-09-03.md`).

## Proposed contract

Add a `[governance]` table to `nyxloom/ciu.global.defaults.toml.j2` (committed defaults layer, so every worktree instance gets it) — and the same to `pwmcp/ciu.global.defaults.toml.j2` — copied from dstdns's: `enabled = true`, `cgroup_parent = "dev-background.slice"` (or `""`, which ciu v7 resolves from `$CGROUP_PARENT_DEV_BACKGROUND`), the KSM opt-in if wanted, and per-stack `[<root>.governance]` memory caps sized for `nyxloomd`/`ntfy`/`pwmcp-instance`. Mark the table with a one-line comment: *copied per root; retired by ciu v8 `[ciu] inherit`*. No new ciu v7 feature; no meta-root; no change to run-gate's gate governance.

## Oracles

**Corrected 2026-09-08 by nyxloom-P103** — the two oracles above are hollow for
the reasons tabulated in "Observed mechanism" (`ciu render` never touches the
compose; `ciu check`'s governance stage passes on the broken state too). The
real oracles this entry was closed against are nyxloom-P103's O1-O5
(`nyxloom-trove/reports/nyxloom-P103-REPORT.md` has verbatim command output for
each):

- **O1**: committed literals (`cgroup_parent = "dev-background.slice"`,
  `device = "/dev/vda"`) on both roots, THEN `ciu up --profile default
  --dry-run --define-root "$PWD"` exits 0 and both stacks' rendered overlays
  (`<stack>/.ciu/ciu.compose.overlay.yml`) carry exactly one
  `cgroup_parent: dev-background.slice` line, with the `[GOVERNANCE]` log line
  reporting `device=/dev/vda (explicit)` and `services_injected=1 exempt=0`.
- **O2/O2b**: `ntfy` is governed by ciu ALONE — the template no longer emits a
  placement key at all, the rendered compose is clean, the fictional
  `nyxloom.slice` literal is gone from every config/compose file under both
  roots, AND the plain-compose sibling `ntfy/docker-compose.yml` (which
  receives no ciu overlay) carries the same caps inline.
- **O3**: the SAME check independently on `pwmcp/` — v7 roots are islands, so
  nyxloom's dry-run proves nothing about pwmcp.
- **O4**: three DISTINCT `mem_limit` values in one dry-run (`ntfy` 2g,
  `nyxloomd` 6g, `pwmcp-instance` 4g via `[pwmcp.governance]`), proving the
  per-stack override tables are real and not a root-value coincidence.
- **O5**: MUTATION-CHECKED by hand — `enabled = false` removes the injection
  entirely; moving the table to an untracked site-layer file while deleting it
  from the committed defaults template makes O1 fail once that site file is
  removed, proving the DEFAULTS layer (not a gitignored site layer) is what a
  fresh worktree actually gets.

## SPEC ownership

ciu v7 `docs/SPEC.md` S15.10 (global default `[governance]`), S15.13 (unknown-key WARN), S15.19 (memory profile); root `AGENTS.md` "Host cgroup placement for spawned containers"; the v8 replacement is SPEC-V8 draft.5 S3.1.5 (`[ciu] inherit`) and S16.11.1 (a zero-instance monorepo root may carry `[governance]`).
