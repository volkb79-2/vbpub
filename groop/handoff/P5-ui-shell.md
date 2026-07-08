# P5 — Textual UI shell (banner, table/tree, drill-down)

**Cut:** v1. **Depends:** P1 merged (develop against `--once --json` frames and
golden fixtures; live loop wiring finalized in P7). Branch: `feat/groop-p5-ui`.
Follow `groop/README.md` workflow protocol.

## Goal

The read-only Textual application: first screen answers "is the host healthy,
what's the biggest pressure source, is the protected workload safe" in under
five seconds; the table is supporting detail, not the interface.

## Spec references

§3.0 (banner + HOST verdict + TOP PRESSURE mock — implement that layout),
§3.1 (container view ⇄ tree view hot-toggle, row identity), §3.2 (columns from
REGISTRY — never hardcode a column), §3.3 (width tiers + job profiles:
triage/memory/network/governance/damon), §3.4 (drill-down page layout),
§3.9/§8 (hotkeys), §3.10 (F1 glossary GENERATED from registry), §6.1 (ui/ is
the only textual importer), §7 (config: colors, tiers, profiles, thresholds).

## Scope — in

1. `ui/app.py`: Textual app; consumes an injected frame source (iterator of
   `Frame`) — live collector, replay reader, or fixture list; the UI must not
   know which (spec §3.8 requirement).
2. `ui/banner.py`: host verdict line (OK/WARN/CRIT from thresholds), host
   CPU/MEM/PSI/zswap/disk/net summary, TOP PRESSURE top-3 list (sort by the
   `pressure` metric if present in the frame — P6 supplies it; render "n/a"
   gracefully when absent).
3. `ui/table.py` + `ui/tree.py`: flat container view and cgroup tree view,
   hot-toggle (F5); columns driven by REGISTRY + active profile; adaptive
   width tiers per §3.3; branch rows display per-metric branch_policy and the
   header shows the aggregation mode (§3.2); tier row accents from config.
4. `ui/drill.py`: full-screen detail per §3.4: metric groups with source/
   confidence chips, process list (from P1 procs), governance block (P4 data),
   sparklines from the ring when available (P2 — degrade to "no history" if
   absent), findings panel placeholder (P6 fills).
5. `ui/keys.py`: hotkey table §8; profile switching; sort; search/filter;
   read-only — NO action keys wired to anything mutating.
6. Non-root presentation: `unavail_perm` values render as dimmed `–` with a
   one-line banner notice ("running unprivileged — N fields unavailable").
7. Startup < 1s to first paint on the reference host (spec §9); sample loop
   off the UI thread (Textual worker).
8. Tests: Textual pilot tests for view toggle, profile switch, drill-down
   open/close on fixture frames; a snapshot test of the banner rendering.

## Scope — out

Diagnostics computation (P6), DAMON panels (P8), incident key (P10), any
mutating action, charts beyond sparklines (chart overlay polish can land in
P7 if time allows).

## Acceptance

- `groop` on fixture frames: banner verdict + top-pressure render; toggle,
  profiles, drill-down work; `groop --replay <golden>` plays through the SAME
  code path.
- No textual import outside `ui/` (a test greps for this).
- pytest green incl. pilot tests; report per README protocol.
