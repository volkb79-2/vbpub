---
kind: backlog-entry
schema_version: 1
id: NL-10
title: "nyxloom doctor's cgroup-slice-missing check only inspects gate argv, never a project's own ciu config/compose cgroup_parent"
status: open
type: "feature"
severity: "medium"
component: "nyxloom-doctor"
provenance: "nyxloom-P103, Work item 8, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

nyxloom's `doctor` already implements a critical `cgroup-slice-missing`
finding (`src/nyxloom/doctor.py:80-86, 787, 813-831`): for every registered
project, it scans that project's GATE ARGV for a `--cgroup-parent=<slice>`
and asserts the named systemd unit really exists, because a missing slice
name fails OPEN to an unbounded transient cgroup (verified 2026-07-27,
`infra/slices/README.md`). It never inspects a project's own ciu config or
compose files for a `cgroup_parent` value declared there.

That gap is exactly how the fictional `nyxloom.slice` survived undetected in
`ntfy`'s own template (`ntfy/ciu.compose.yml.j2`, fixed by nyxloom-P103): the
name was declared in a stack's `[<root>.runtime]`/compose config, not in any
gate argv, so `doctor`'s existing check never looked at it. Separately, ciu's
OWN slice-existence preflight (`[S15.G9-1]`) SKIPS entirely inside a
devcontainer (systemd is not PID 1), so nothing in the normal `ciu up` path
would have caught it either — `doctor` is the only mechanism structurally
positioned to run this check from outside that constraint (it runs on the
host devcontainer, same limitation, but is the natural place to extend once
this class of gap is named).

**Companion recommendation, folded in from nyxloom-P103's "Gate argv"
section:** `tests/test_render.py:1451-1505`
(`test_nyxloomd_compose_template_and_sibling_mounts_agree`) already
demonstrates the in-repo pattern for parsing a stack's `ciu.compose.yml.j2`
template alongside its plain-compose sibling and asserting they agree — but
only for `nyxloomd`'s VOLUME sources, and only for `nyxloomd`. Extending that
same pattern to the `ntfy` template/sibling pair (checking `cgroup_parent`
and the governance-mirrored keys, not just volumes) would turn nyxloom-P103's
config fix into a permanent gate assertion, so a future edit that reintroduces
a stale placement key in one file but not its sibling fails `tester-unified`
directly instead of relying on a human noticing.

## Why nyxloom owns it

`doctor` is nyxloom's own host-level diagnostic tool
(`src/nyxloom/doctor.py`); `tests/test_render.py` is nyxloom's own gate
suite. Both extensions are nyxloom tooling work, not a ciu defect — ciu's S15
governance and S15.G9 preflight behaved exactly as specified throughout
nyxloom-P103's probes.

## Proposed contract

1. Extend `doctor`'s `cgroup-slice-missing` check (or add a sibling check
   alongside it) to also scan each registered project's rendered ciu
   config/compose output for a directly-declared `cgroup_parent` (both the
   ciu-rendered path and any plain-compose sibling), not only gate argv, and
   assert the named slice exists under the same critical/warning/info
   severities `doctor` already uses for the gate-argv case.
2. Extend `tests/test_render.py`'s template/sibling-agreement pattern to the
   `ntfy` pair (`ntfy/ciu.compose.yml.j2` vs `ntfy/docker-compose.yml`),
   asserting governance-relevant keys (`cgroup_parent`, `mem_limit`, etc.)
   stay in step, not just volume sources — this is the permanent gate
   assertion nyxloom-P103 itself could not add without also changing the
   gate's meaning (this package changes zero Python by design).

## Oracles

- A project whose config/compose declares a `cgroup_parent` naming a
  nonexistent slice produces a `cgroup-slice-missing` (or equivalent new)
  `doctor` finding, without requiring that slice name to also appear in any
  gate argv.
- A deliberately-reintroduced mismatch between `ntfy/ciu.compose.yml.j2` and
  `ntfy/docker-compose.yml` on a governance-relevant key fails
  `tests/test_render.py` under `tester-unified`.

## SPEC ownership

`src/nyxloom/doctor.py:80-86,787,813-831` (existing gate-argv check);
`tests/test_render.py:1451-1505` (existing template/sibling pattern);
`infra/slices/README.md` (fail-open hazard, verified 2026-07-27). Originating
handoff: `nyxloom-trove/handoffs/nyxloom-P103-ciu-governance-standalone-roots.md`
Work item 8 and its "Gate argv" section.

## Updates
