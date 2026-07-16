# P11 — DAMON paddr host mode: banner heat bar + host-memory status page

**Cut:** v1.5. **Depends:** P8 (classification/panel code), P9 (controlled-
session machinery: confirmation modal, ownership markers, audit log).
Parallel to P10 is fine. Branch: `feat/topos-p11-damon-paddr`.
Follow `topos/README.md` workflow protocol.

## Goal

A MANUALLY started, topos-owned `paddr` DAMON session that measures physical
DRAM heat for the WHOLE host — the one signal vaddr cannot supply (85–95% of
vaddr-reported bytes are unmapped address-space gaps; every paddr byte is real
backed RAM). Feeds exactly two surfaces: the banner's system-wide
hot/warm/cold heat bar and a host-memory status page. Never per-entity, never
auto-started in this cut.

## Spec references

§3.6(d) — the authoritative design (read it fully: sampling 400ms / aggregation
8s, coexists with (c)'s vaddr contexts as a separate kdamond, same conflict/
idx-allocation/atexit discipline); §3.0 (banner heat bar slot); §0.1 (v1.5
scope line; auto-start via `[damon] paddr_enabled` is v2 — OUT for you);
§5 (data source row); §6.5 (confirmation + audit, as P9).

## Scope — in

1. `damon/paddr.py`: start/stop a topos-owned paddr context on a FREE kdamond
   (reuse P9's slot allocation + ownership-marker + audit-log machinery —
   factor shared helpers out of P9's module rather than copying; that refactor
   is in-scope and does not count as a contracts change). Config attrs from
   `[damon]` (defaults per §3.6d: sample 400ms, aggr 8s). Refuse when no free
   kdamond; never touch foreign sessions (P8's detection tells you whose is
   whose).
2. Classification: host-level hot/warm/cold byte totals from the paddr
   region snapshots (thresholds shared with P8's `[damon]` config); emitted as
   HOST metrics (`host_damon_hot_bytes` etc.) in `Frame.host` — registry
   entries with locality/subtree semantics = n/a (host scope), aggregatable
   false, glossary explaining the vaddr-vs-paddr distinction in two sentences.
3. Banner integration: when host paddr metrics are present (from OUR session
   OR a detected foreign paddr session via P8 — both count), the banner shows
   the hot/warm/cold bar with a freshness age; absent → the bar simply isn't
   rendered (no nagging).
4. Host-memory status page (drill-down on the banner / hotkey): region-size
   histogram as text bars, session parameters, session owner (topos vs
   foreign), start/stop controls (root + typed confirmation via P9's modal),
   overhead note.
5. Recording: host paddr metrics flow through frames/recordings like
   everything else (replay shows the heat bar).
6. Tests: fixture paddr sysfs tree (reuse P8 fixture conventions); ownership
   refusal test (foreign paddr session → read-only, no stop offered);
   free-slot refusal; classification math golden test.

## Scope — out

Auto-start / `paddr_enabled` config plumbing (v2), per-entity attribution of
paddr data (impossible without pagemap work — spec §3.6d), DAMOS schemes,
any reclamation action.

## Acceptance

- On the reference host (root): start paddr session from the status page →
  banner heat bar appears within ~2 aggregation periods; stop restores the
  kdamond slot; a concurrently running foreign vaddr session is untouched
  (P8/P9's coexistence guarantees hold — test alongside one).
- Non-root: bar renders if a foreign paddr session exists; start control
  hidden.
- Audit log entries for start/stop; pytest green; report per README protocol.
