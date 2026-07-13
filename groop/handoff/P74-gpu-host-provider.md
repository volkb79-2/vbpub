# P74 - GPU host provider (optional plugins bucket)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P1 (merged), P71 (merged - the exemplar to copy)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** GPU facts cannot be read without a subprocess or a vendor library (see "Source of truth" - if the sysfs surface genuinely cannot carry the five metrics, write BLOCKED with what it does expose; do NOT reach for `nvidia-smi`)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **roadmap-driven**.
docs/ROADMAP.md's "Optional plugins / future surfaces" bucket is (GPU, ZFS, CIU).
P71 drained ZFS from it this wave -- the first package ever carved from that
bucket. GPU is the next-cheapest slice and follows P71's now-proven shape almost
exactly: a read-only host provider over a stable kernel file tree, host-scoped
metrics, a conditional banner segment, and no per-cgroup attribution claim.
Carving it keeps the bucket draining instead of the queue orbiting the report/
daemon areas the last four waves have all lived in.
-->

## Goal

Teach groop that GPU memory exists. On a host with a discrete GPU, VRAM pressure
and GPU utilization are invisible to every metric groop currently collects -- an
operator debugging a stalled workload on a GPU box gets no signal at all.

Add host-level GPU metrics and a banner annotation. This claims **nothing**
per-cgroup: the kernel does not attribute GPU memory to cgroups (DRM cgroup
support is not something we can rely on), and inventing an attribution would
violate the same boundary P19/P23 drew for zram, P37 for network loss, and P71
for ARC.

## The exemplar is P71 - follow it closely

This package is deliberately shaped like the one that just landed. Read it first
and copy its structure:

- `src/groop/collect/host.py` - `_zfs_arc_metrics()` / `_parse_arcstats()`: how a
  host fact is read from a kernel file, parsed defensively, and turned into
  `MetricValue`s that degrade to `unavail_kernel` rather than fabricating `0`.
- `src/groop/collect/collector.py` - `_apply_zfs_arc_rate()`: how a *derived*
  host metric gets its previous sample from `Collector._delta` /
  `_prev_counters`. **Per-instance state, not a module global.** P71's review
  caught exactly that mistake; do not repeat it.
- `src/groop/registry.py` - the `host_zfs_arc_*` block: name, unit, kind,
  locality `local`, branch_policy `n/a`, aggregatable `False`, `sources` naming
  the file, and a real `glossary` sentence (it is user-visible F1 help, and it is
  ASCII).
- `src/groop/ui/banner.py` - `_zfs_arc_line()`: a conditional segment, appended
  only when the hardware is present. No new panel, no new hotkey.
- `groop/tests/test_zfs_arc.py` - the six-oracle test shape, and in particular how
  the banner test asserts the **rendered cell**, not the substring.

## Source of truth

`/sys/class/drm/card*/device/` - the kernel DRM sysfs tree. **No subprocesses. No
`nvidia-smi`. No vendor Python libraries.** This is a sysfs read, exactly as P71
was a procfs read.

Vendor-dependent, and that is the interesting part of this package:

| fact | amdgpu | i915 / xe | nvidia (proprietary) |
|---|---|---|---|
| VRAM total | `mem_info_vram_total` | absent (shared) | absent from sysfs |
| VRAM used | `mem_info_vram_used` | absent | absent |
| busy percent | `gpu_busy_percent` | absent | absent |

So: **amdgpu exposes what we need; i915/xe and the nvidia proprietary driver do
not.** Do not paper over this. The honest outcome is a provider that reports real
numbers on amdgpu, `unavail_kernel` everywhere else, and a `vendor` fact that says
which. A provider that pretends to work on nvidia by shelling out is the failure
mode this handoff forbids.

The review host almost certainly has **no discrete GPU**, so the absent path is the
one the controller can validate live -- which is exactly why the present path must
rest on exact-value fixture assertions (oracle 1).

## Required Contracts

1. **New host metrics in the registry**, following `host_zfs_arc_*` exactly:
   `host_gpu_vram_total` (bytes), `host_gpu_vram_used` (bytes),
   `host_gpu_busy_pct` (%), `host_gpu_count` (count). Registry-absent metrics MUST
   NOT appear in a frame (CONTRACTS §3).
2. **Absent GPU is not an error, and not a zero.** No DRM cards, or a card whose
   driver exposes none of these files: `v=None`, `src="unavail_kernel"`. Never
   `0`. Never `0.0`. This is CONTRACTS §8 and it is the single most likely thing
   to get wrong (a host with a GPU at 0% busy and a host with no GPU are not the
   same host).
3. **Input trust.** Every value read is validated (`isinstance`/`try-except`)
   before use. A malformed or truncated sysfs file degrades that metric to
   `unavail_kernel`; it does not raise and does not poison the frame.
4. **Multi-GPU is real.** `card0` and `card1` both exist on plenty of hosts.
   `host_gpu_count` counts DRM render cards; VRAM total/used **sum** across cards
   that report them; `host_gpu_busy_pct` is the **max** across cards (a mean hides
   one pegged GPU, which is the thing the operator needs to see). State this in
   the glossary -- an aggregate whose rule is undocumented is a trap.
5. **Skip non-GPU DRM nodes.** `/sys/class/drm/` also contains connectors
   (`card0-DP-1`) and control nodes. Match render cards only; a naive `card*` glob
   double-counts and is an automatic review reject.
6. **`host_meta["gpu"]` for the detail** (per-card vendor, driver, VRAM, busy) if
   you want drill-down, following P23/P71's `host_meta` precedent -- not new
   `Frame` fields.
7. **Banner annotation, no new UI surface.** A segment only when a GPU is present,
   e.g. `GPU 4.2G/8.0G (busy 37%)`. Multi-GPU shows the aggregate per contract 4.
8. **No per-cgroup GPU claim** anywhere -- metrics, diagnostics, banner, or docs.
   Say so explicitly in the docs, as COMPRESSED-SWAP.md does for zram and P71 did
   for ARC.

## Acceptance Oracles (numbered, adversarial)

Fixtures are plain file trees under `tests/fixtures/sysfs/drm/` -- same pattern as
the existing sysfs/procfs fixture trees. `collect_host` already takes `sys_root`
as a parameter precisely so this substitutes cleanly.

1. **Present-GPU fixture (amdgpu):** exact expected values for all four metrics.
   Assert the numbers, not presence.
2. **Absent-GPU (no `/sys/class/drm`, and separately: an empty one):** all four
   metrics `v=None, src="unavail_kernel"`. Assert `v is None` explicitly -- a test
   that only checks the key exists passes against a fabricated `0`.
3. **Driver without the files (i915 fixture):** card present, `mem_info_*` absent
   -> VRAM metrics `unavail_kernel`, `host_gpu_count` still counts the card. This
   is the case that separates "no GPU" from "a GPU I cannot read", and they must
   not render identically.
4. **Multi-GPU:** two amdgpu cards -> VRAM sums, busy is the max, count is 2.
   Engineer the fixture so a mean and a max differ (e.g. 10% and 90%) -- a test
   where they coincide proves nothing.
5. **Connector nodes are not counted:** a fixture containing `card0`, `card0-DP-1`,
   and `card0-HDMI-A-1` yields `host_gpu_count == 1`.
6. **Malformed sysfs** (non-numeric `mem_info_vram_used`, truncated
   `gpu_busy_percent`): degrades to `unavail_kernel`, does not raise, rest of the
   frame intact.
7. **Banner:** the rendered banner contains the GPU segment with its exact cells on
   the GPU fixture and does **not** contain it on the absent fixture. Assert the
   rendered artifact, not the substring "GPU" (P71's review lesson).
8. **Golden frames:** non-GPU fixtures must be unaffected. If an existing golden
   changes, you have violated contract 2.

## Out Of Scope

- Per-cgroup / per-process GPU attribution (do not attempt).
- `nvidia-smi`, NVML, ROCm SMI, any vendor library, any subprocess.
- GPU temperature, power, fan, clocks -- a possible successor, not this package.
- Diagnostics rules that act on GPU pressure (needs a rule design and evidence
  first).
- CIU grouping/actions (the remaining item in this roadmap bucket).

## Docs

`docs/ARCHITECTURE.md` (module map), `groop/README.md` (work-package row),
`docs/ROADMAP.md` (mark GPU landed in the Optional-plugins bucket; note CIU
remains), `docs/STATUS.md`. If the vendor-support matrix needs more than two
sentences, give it a short section in the docs rather than burying it in the
glossary -- "why does this say unavailable on my nvidia box" is the first question
an operator will ask.

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/<new gpu test file> -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
PYTHONPATH=groop/src python3 -m groop.cli --once --json    # must still work on this GPU-less host
python3 -m py_compile <changed files>
git diff --check
```

State in the REPORT which environment each result came from, and confirm the
absent-GPU path was validated live on the review host.
