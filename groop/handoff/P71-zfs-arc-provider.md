# P71 - ZFS ARC host provider (optional plugins bucket)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P1 (merged), P3 (merged), P19 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** a named contract cannot be met as specified; ARC memory cannot be represented without changing `Frame`/`MetricValue` (it can — use `host_*` metrics + `host_meta`, as P19/P23/P34 did)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **roadmap-driven**.
docs/ROADMAP.md "Remaining Estimate" has carried an "Optional plugins / future
surfaces (GPU, ZFS, CIU grouping/actions)" bucket for the entire life of the
project and has never had a single package carved from it — it is the exact
"queue orbits recently-reviewed areas forever" drift §8 was written to stop.
This is the cheapest, most testable slice in that bucket: ZFS ARC is a pure
read of a stable procfs kstat file, it fits the existing host-metric pattern
that P19 (zswap/zram), P23 (per-device zram), and P34 (host device banner)
already established, and it answers a question groop currently answers WRONG on
any ZFS host — see below.
-->

## Goal

Teach groop that ARC memory exists. On a ZFS host, the ARC can hold many GB of
RAM that `MemAvailable` counts as *unavailable*, so groop's host memory banner
today reports severe memory pressure on a host that is actually fine — the ARC
will evict under pressure. Add host-level ZFS ARC metrics and a banner annotation
so the operator can see "12 GB of that 'used' memory is reclaimable ARC."

This is a read-only host provider. It claims **nothing** per-cgroup: the kernel
does not attribute ARC to cgroups, and inventing an attribution would violate the
same boundary P19/P23 drew for zram and P37 drew for network loss.

## Context To Read First

- **The exemplar to imitate is P19 + P23**: `src/groop/collect/host.py` (how host
  facts are read and turned into `host_*` MetricValues), `src/groop/registry.py`
  (how `host_zswap_*` metrics are declared, incl. `glossary` — the F1 help and
  docs are GENERATED from it), and how P23 put per-device zram rows into
  `Frame.host_meta` rather than inventing a new frame field.
- `groop/CONTRACTS.md` §4 (`Frame.host`, `Frame.host_meta`, `MetricValue.src`),
  §8 (degradation: every read wraps errors into `unavail_*`, never fabricates 0).
- `src/groop/ui/banner.py` — where host facts are rendered.
- `groop/docs/COMPRESSED-SWAP.md` — the precedent for documenting a memory-backend
  policy and its metric semantics honestly.
- Do **not** read daemon, DAMON, BPF, actions, report, or MCP code.

## Source of truth

`/proc/spl/kstat/zfs/arcstats` — a stable, world-readable, three-column kstat
table (`name type data`). The fields this package needs:

| kstat field | meaning |
|---|---|
| `size` | current ARC size in bytes |
| `c` | current ARC target size |
| `c_max` | ARC maximum (the tunable operators actually care about) |
| `c_min` | ARC minimum |
| `hits`, `misses` | cumulative counters -> hit ratio |

The file is absent on non-ZFS hosts. **That is the common case and must be the
well-tested one.**

## Required Contracts

1. **New host metrics in the registry**, following the `host_zswap_*` pattern
   exactly (name, unit, kind, locality `local`, branch_policy `n/a`,
   aggregatable `False`, `sources` naming the kstat path, and a real `glossary`
   sentence — it is user-visible help text, not a placeholder):
   `host_zfs_arc_size` (bytes), `host_zfs_arc_target` (bytes),
   `host_zfs_arc_max` (bytes), `host_zfs_arc_min` (bytes),
   `host_zfs_arc_hit_ratio` (ratio).
   Registry-absent metrics MUST NOT appear in a frame (CONTRACTS §3) — so adding
   them to the registry is what makes them legal, and every one of them must
   actually be produced or explicitly `unavail_*`.
2. **Absent ZFS is not an error, and not a zero.** On a host with no
   `/proc/spl/kstat/zfs/arcstats`, the metrics are emitted with
   `src="unavail_kernel"` and `v=None`. Never `0`. This is the CONTRACTS §8
   no-zero-fabrication rule and it is the single most likely thing to get wrong.
3. **Input trust (standing contract).** The kstat file is parsed defensively:
   every value is validated (`isinstance`/`try-except`) before use. A malformed,
   truncated, or short-read file degrades to `unavail_kernel` for the affected
   metrics — it does not raise, and it does not poison the whole frame.
4. **Hit ratio is derived, not faked.** `hits`/`misses` are cumulative counters.
   Compute the ratio as a rate over the interval using the existing
   raw-counter/reset machinery (CONTRACTS §4 rate/reset contract: on counter
   regression emit `v=None` and reseed — never a negative or absurd rate). Carry
   the raw values in `MetricValue.raw`, like every other counter in the codebase.
   A lifetime-cumulative ratio computed as `hits/(hits+misses)` is **not** what
   this contract asks for and will be review-rejected.
5. **Banner annotation, no new UI surface.** The host banner gains an ARC segment
   only when ZFS is present (e.g. `ARC 12.4G/16G (hit 94%)`). No new panel, no new
   hotkey, no layout redesign. Textual stays confined to `src/groop/ui/`.
6. **No per-cgroup ARC claim anywhere** — not in metrics, not in diagnostics, not
   in the banner, not in the docs. Say so explicitly in the docs, the way
   COMPRESSED-SWAP.md says it for zram.
7. **`host_meta` for the detail, if you need it.** If you want to surface the raw
   kstat fields for drill-down, put them under `Frame.host_meta["zfs_arc"]`
   (additive, consumers tolerate absence — CONTRACTS §4), not in new `Frame`
   fields.

## Acceptance Oracles (numbered, adversarial)

Fixtures are plain files under `tests/fixtures/procfs/zfs/` — same pattern as the
existing cgroupfs/procfs fixture trees. The collector already takes `proc_root` as
a parameter precisely so this substitutes cleanly.

1. **Present-ZFS fixture:** a realistic `arcstats` file yields the five metrics
   with exact expected values (assert the numbers, not just presence).
2. **Absent-ZFS (no file):** all five metrics are `v=None, src="unavail_kernel"`.
   Assert `v is None` explicitly — a test that only checks the key exists would
   pass against a fabricated `0`.
3. **Malformed kstat** (truncated mid-line, a non-numeric `data` column, a missing
   `size` row): degrades to `unavail_kernel` for affected metrics, does not raise,
   and the rest of the frame is intact.
4. **Hit-ratio rate over two sweeps:** two fixture reads with known
   `hits`/`misses` deltas produce the exact expected ratio for the interval — and
   a test where the counters go *backwards* (pool export/import) asserts `v=None`
   and a reseed, not a negative ratio.
5. **Banner:** rendered banner text contains the ARC segment on the ZFS fixture
   and does **not** contain it on the absent-ZFS fixture. Assert on the rendered
   output (P41's standing lesson: assert the observable artifact).
6. **Golden frames:** if `--once --json` output changes for existing fixtures
   (it should NOT — non-ZFS fixtures must be unaffected), regenerate the goldens
   via the documented command in this same package and say so in the REPORT.
   If the goldens *do* change for a non-ZFS fixture, you have violated contract 2.

## Out Of Scope

- Per-cgroup ARC attribution (impossible; do not attempt).
- ZFS pool health, scrub status, dataset-level stats, `zpool`/`zfs` command
  execution. **No subprocesses at all** — this is a procfs read.
- L2ARC, ZIL, SLOG metrics (a possible successor; not this package).
- GPU and CIU providers (the other two items in the same roadmap bucket).
- Diagnostics rules that *act* on ARC size (a possible successor: "ARC is
  squeezing your workload" needs a real rule design and evidence first).
- Changing the memory-pressure score or any existing diagnostic.

## Docs

`docs/ARCHITECTURE.md` (module map: the new provider/reader),
`groop/README.md` (work-package table row), `docs/ROADMAP.md` (mark the ZFS item
of the Optional-plugins bucket as landed and note GPU/CIU remain),
`docs/STATUS.md`. If the ARC-vs-MemAvailable interaction needs more than two
sentences, give it a short section in `docs/COMPRESSED-SWAP.md` — that document
already owns "what does 'used memory' actually mean on this host".

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/<new zfs test file> -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
PYTHONPATH=groop/src python3 -m groop.cli --once --json    # must still work on a non-ZFS host (this one)
python3 -m py_compile <changed files>
git diff --check
```

State in the REPORT which environment each result came from. Note that the review
host has **no ZFS**, so the absent-path is the one the controller can validate
live — the present-path rests on your fixtures, which is exactly why oracle 1
asserts exact values.
