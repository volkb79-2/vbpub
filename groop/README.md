# groop — host pressure inspector and cgroup forensics TUI

Implementation home for the tool specified in
`TUI-SPEC.md` (the **spec**; §-references in all handoff docs point there).
Read `CONTRACTS.md` before writing any code — it defines the interfaces every
package codes against.

Release cut (spec §0.1): **v0** collector proof → **v1** read-only TUI →
**v1.5** DAMON → **v2** BPF/daemon/actions. This directory carries v1 + v1.5.
Stack: Python; Textual allowed ONLY under `src/groop/ui/` (spec §6.1, §6.4).

## Quickstart

```bash
pip install -e groop/
groop --once --json
groop
groop --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step
```

Use `--config PATH` to point at an alternate TOML config, `--profile NAME` to
override the active UI column profile for one run, and `--record FILE` to record
the live TUI stream to JSONL while you inspect it.

## Work packages

| Pkg | Title | Cut | Depends on | Handoff doc |
|-----|-------|-----|------------|-------------|
| P1 | Collector core + metric registry (`--once --json`) | v0 | — | `handoff/P1-collector-core.md` |
| P2 | Record / replay / history ring | v1 | P1 | `handoff/P2-record-replay.md` |
| P3 | Network providers (host truth + netns) | v1 | P1 | `handoff/P3-network-providers.md` |
| P4 | Origin / drift detection | v1 | P1 | `handoff/P4-origin-drift.md` |
| P5 | Textual UI shell (banner, table/tree, drill-down) | v1 | P1 | `handoff/P5-ui-shell.md` |
| P6 | Diagnostics engine (pressure score + rules) | v1 | P1; UI panel needs P5 | `handoff/P6-diagnostics.md` |
| P7 | v1 integration + acceptance + packaging | v1 | P2–P6 | `handoff/P7-integration.md` |
| P8 | DAMON passive (detection, columns, panel) | v1.5 | P1, P5 | `handoff/P8-damon-passive.md` |
| P9 | DAMON controlled vaddr session | v1.5 | P8 | `handoff/P9-damon-control.md` |
| P10 | Incident snapshots | v1.5 | P2, P5 | `handoff/P10-incident-snapshots.md` |
| P11 | DAMON paddr host mode (banner heat bar + status page) | v1.5 | P8, P9 | `handoff/P11-damon-paddr.md` |

## Start order

1. **P1 first, alone.** Everything depends on it; its golden JSONL fixtures
   become the test input for every other package. Review + merge before
   fanning out.
2. **Wave 2 (parallel): P2, P3, P4.** Independent of each other; each consumes
   P1's model/registry and fixtures only.
3. **P5** can start with wave 2 (it can develop against P1's `--once --json`
   output and replay fixtures); **P6** any time after P1 (its UI panel lands
   via P5/P7).
4. **P7 last for v1** — after P2–P6 are merged. Runs the spec §9 acceptance
   criteria.
5. **v1.5: P8 → P9 → P11** (strictly ordered — P11 reuses P9's controlled-
   session machinery); **P10** any time after P2+P5 — parallel to P8 is fine.

## Workflow protocol (every package agent MUST follow this)

- **Worktree + branch**: work in a dedicated git worktree on a feature branch
  named `feat/groop-<pkg>-<slug>`, e.g.
  `git worktree add -b feat/groop-p1-collector /tmp/vbpub-groop-p1-collector main`.
  The worktree MUST be outside this main checkout, under `/tmp`, and MUST branch
  from local `main`. Never commit package work directly to `main`.
- **Scope**: touch only `groop/**`. No edits to other vbpub areas, no host
  changes, no root, no docker mutations. The collector reads live
  `/sys/fs/cgroup` only in ad-hoc manual testing; automated tests use
  fixtures.
- **Contracts are frozen**: if your package needs an interface change in
  `CONTRACTS.md`, propose it in your report — do NOT silently change shared
  interfaces. Additive, package-private code is yours to shape.
- **Quality gates before handover**: `python3 -m pytest groop/tests -q` green;
  `python3 -m py_compile` clean on all new files; `groop --once --json`
  (or the package's own entry point) demonstrably runs.
- **Engineering bar**: keep package code modern, typed where it clarifies
  contracts, and DRY. Shared behavior belongs in `src/groop/` helpers, not in
  copied package-local parsers or serializers. Tests should cover behavior and
  edge cases, not just import smoke.
- **Handover**: finish with (a) focused commits on the feature branch, the
  last one summarizing the package; (b) a report file
  `groop/handoff/reports/<PKG>-REPORT.md` containing: what was built, deviations
  from the handoff doc, proposed contract changes (if any), test evidence
  (command + output tail), known gaps/open items; (c) your final message =
  that report, so review + merge can proceed without archaeology.
- **Controller review**: the session controller reviews the branch diff,
  validates the report, runs the relevant gates from a clean checkout, fixes or
  sends back issues, then merges to `main` with a focused merge/commit. Later
  packages branch only after their declared dependencies are merged.

## Reference deployment

gstammtisch (Debian 13, cgroup v2, zswap, Pterodactyl/Wings game server).
Degradation on other hosts must be graceful (spec §6.3), but no distro matrix
work before v2 (spec §10).
