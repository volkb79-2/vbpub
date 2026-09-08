# io.cost integration — device-wide latency-QoS layer under dev.slice

**Status: PLAN ONLY — not carved, not dispatched, not implemented.** Written on
explicit operator request ("write the plan ready to impl but dont start it")
after an ad-hoc live-host investigation established the rlat/wlat evidence
this plan needed. Read `CGROUP-NOTES.md` "io.cost vs BFQ — an option this
host doesn't use (yet)" FIRST — that section is the authoritative mechanism
reference (exact `io.cost.model`/`io.cost.qos` write syntax, the field table,
the elevator/IOWeight traps, the `io.max`-composes-not-competes proof). This
plan does not repeat that content; it only adds what CGROUP-NOTES.md itself
flagged as missing: "What's new provisioning work, not yet built."

## Correction to carry forward

An earlier live check this session mis-reported `io.cost.model`/`io.cost.qos`
as absent on the live host. Re-checked precisely: both files **exist** at the
root cgroup (`CONFIG_BLK_CGROUP_IOCOST=y`, `io` controller active in
`cgroup.subtree_control`) and are **empty** (0 bytes) — kernel support is
real and already proven working (the Aug 29 `iocost_coef_gen.py` calibration
runs under `/root/iocost-results/` succeeded), it's just that no device has
ever had a model/qos line written. This is a materially lower-risk starting
point than "unsupported" — there is no kernel-capability question left open,
only a provisioning-and-policy one.

## What this session's live investigation actually settled

1. **The rlat/wlat half CGROUP-NOTES.md flagged as unmeasured is now
   measured**, informationally: a representative-load `fio` probe (1200
   read / 600 write IOPS held exactly, 4k random, 70/30 mix, 120s,
   `--direct=1`, run on the host's default/root cgroup placement — NOT
   `dev.slice` (carries the `io.max` throttle, would skew the number) and
   NOT `dev-background.slice` (contended, produced a real cgroup OOM kill on
   the first attempt at this rate)) against `/dev/mapper/gstammtisch--vg-root`:

   | | read | write |
   |---|---|---|
   | p50 | 5.1 μs | 5.0 μs |
   | p90 | 18.3 μs | 17.0 μs |
   | p95 | 22.9 μs | 22.4 μs |
   | p99 | 42.2 μs | 41.7 μs |
   | p99.9 | 276 μs | 249 μs |

   This is evidence for a target, not a target itself — picking `rlat`/`wlat`
   is a policy call (§ Design decisions below proposes one). The raw JSON
   this run produced lived in a throwaway `/tmp` path and is already gone;
   this table is the only surviving record until this plan's Step 1 gives it
   a durable home.

2. **Freshness/staleness for calibration data is hardware-keyed, not
   time-keyed** (explicit operator ruling this session, correcting an
   earlier draft that proposed a 30-day TTL matching `mdt-io-baseline.py`'s
   own convention): "io performance does not change if the hardware did not
   change — if you want evidence, record the hardware found during
   benchmark time." Any cache-freshness check this plan adds must compare
   recorded hardware facts (device model/size, kernel version), never a
   clock.

3. **The existing per-slice `IOWeight` values need no retune.** CGROUP-NOTES.md's
   "trap" section warns that a weight hand-picked above 100 to fight BFQ's
   compression curve (e.g. `wings.slice`'s `IOWeight=7800`) becomes ~10x too
   strong once `io.cost` reads the same file uncompressed. Checked live:
   this host's three weights are `DEV_INTERACTIVE_IO_WEIGHT=100`,
   `DEV_BACKGROUND_IO_WEIGHT=10`, `DEV_BUILDKITD_IO_WEIGHT=50` — none above
   100, so none were ever compression-compensated. This host is in the safe
   case; the retune step CGROUP-NOTES.md warns about is not required here,
   but the mechanism this plan builds must still carry the general warning
   (a future host, or a future hand-edit, could reintroduce it).

4. **The 40-60%-of-baseline per-slice utilization cap (`DEV_IO_CAP_PCT`/
   `SWEEP_IO_CAP_PCT`) is unaffected and untouched by this plan.** Per
   CGROUP-NOTES.md "`io.max` still applies — the two mechanisms compose,
   they don't compete": `io.max` is blk-throttle, a separate rq-qos policy
   from `io.cost`'s latency-triggered throttling, scheduler-independent.
   This plan is strictly additive underneath the existing cap, not a
   replacement for it. (Directly answers the operator's own earlier
   question — "can we still apply the like 40-60% utilization cap for a
   slice?" — yes, unmodified.)

## Design decisions this plan makes (for review, not yet acted on)

- **D1 — Hybrid, not a wholesale switch.** Keep `io.max` exactly as-is.
  ADD `io.cost` as a device-global latency-QoS layer underneath it, opt-in
  and OFF by default — same posture as `DEV_MEMORY_MIN_GUARANTEED_CEILING`
  (CIU-P50): a new capability ships inert until an operator deliberately
  turns it on, never a nonzero default pushed onto an existing host.
- **D2 — The elevator switch is real and device-wide** (CGROUP-NOTES.md
  "The elevator has to leave BFQ" — `io.cost` needs `none`, not additive
  with BFQ, one or the other per device). This is the single biggest-blast-
  radius step in this plan: it changes how EVERY cgroup's IO is scheduled on
  the target device, not just newly-opted-in ones. `io.bfq.weight` becomes
  inert for that device the moment this flips (matching what already
  happens on NVMe hosts per the shipped udev rule's own vd*/sd*-only scope)
  — the existing `IOWeight=` values do not vanish, they simply get read by
  `io.cost`'s plain `io.weight` file instead of BFQ's compressed one (D1's
  "no retune needed here" finding depends on this).
- **D3 — Calibration data gets a canonical, host-setup-owned home.**
  Currently `/root/iocost-results/*.env` is an ad-hoc location from a manual
  run, not read by anything. Introduce `IOCOST_MODEL_ENV` (new
  `host-setup.env` key, mirroring `IO_BASELINE_ENV`'s existing shape) as the
  canonical path, e.g. `/var/lib/mdt/iocost-model.env`, holding the model
  coefficients PLUS the hardware facts D2/ruling-2 requires (device
  model/size string, kernel version, calibration timestamp) — never just the
  six numbers alone, so a later "is this still valid" check has something to
  compare against.
- **D4 — `rlat`/`wlat` stay a manual, wizard-guided entry — never
  auto-derived from the `fio` percentile table alone.** Consistent with how
  `DEV_MEMORY_MIN_GUARANTEED_CEILING` was deliberately kept manual-with-
  guidance rather than auto-computed: the percentile table informs the
  operator's choice (this plan's own § "What this session settled" #1 is
  exactly that evidence), it does not become a formula that silently picks
  a number. Default is empty (mechanism fully inert — CGROUP-NOTES.md's own
  explicit caution: "write the model, leave `enable=0`... do not guess").
- **D5 — `rpct`/`wpct` (95.00) and `min`/`max` vrate bounds (1/100, i.e.
  effectively unclamped) are hardcoded constants, not new prompted keys**
  for this first cut — CGROUP-NOTES.md's own worked example already uses
  these exact values, and every existing new-mechanism precedent in this
  codebase (D1 above) favors minimal new config surface. A future round can
  promote either to a real key if a host ever needs to deviate; don't build
  that tunable speculatively.
- **D6 — Lives in `modern-debian-tools-python-debug/host-setup/`, not
  `debian-install-v2`.** `_configure_zswap()` (debian-install-v2,
  `installer.py:520`, `templates.py:89` `ZSWAP_SERVICE`) is CGROUP-NOTES.md's
  cited pattern to mirror — a `Type=oneshot`/`RemainAfterExit=yes` unit
  doing raw `ExecStart=/bin/sh -c 'echo ... > /sys/...'` writes, `WantedBy=
  sysinit.target` — but that's a reference for the unit's SHAPE, not its
  location. debian-install-v2 is one-time OS provisioning (partitioning,
  swap, zswap at first boot); this is ongoing dev-tier IO governance, same
  domain as `dev.slice`'s IO caps and the memory-min-guaranteed ceiling —
  host-setup's own domain, using its established `units/*.in` +
  `install.sh` render convention, not a new debian-install-v2 Python action.
  `iocost-calibrate.sh`/`iocost_coef_gen.py` stay where they are
  (`scripts/debian-install-v2/tools/`) and are invoked manually from a full
  repo checkout — NOT vendored into host-setup — because calibration is a
  rare, hardware-tied, one-time-per-host operation (D3), unlike the routine
  `mdt-io-baseline.py` sweep host-setup already owns standalone. Vendoring a
  copy for a rare manual step would be speculative packaging work this plan
  explicitly declines.

## Scope

### In scope

1. **`IOCOST_MODEL_ENV` cache convention** (new `host-setup.env` key +
   doc), a hand-authored-then-committed-by-operator file for now (produced
   by manually running `iocost-calibrate.sh` from a repo checkout and
   copying its output into the canonical path) — no code writes this file
   automatically in this first cut, matching D6's "rare manual step" framing.
2. **New unit `host-setup/units/mdt-iocost-config.service.in`**, rendered
   by `install.sh` like every other `.in` template. Shape (mirrors
   `ZSWAP_SERVICE`, D6):
   - `ConditionPathExists=` guards: the calibration env file, AND a new
     `DEV_IOCOST_ENABLE` gate (empty/unset ⇒ unit renders but the
     `ConditionPathExists` short-circuits it to an inert no-op — matches the
     "render() drops a directive whose value resolves to empty" convention
     `install.sh` already uses elsewhere, but here inverted: the unit itself
     always installs, it just never fires when the gate is off. Needs a
     concrete decision at implementation time: literal `ConditionPathExists`
     against a sentinel written only when the operator opts in, OR
     `install.sh` skips installing the unit file entirely when the gate is
     empty (simpler, matches how `mdt-dev-cap-watcher.py` is only installed
     `if [ "$INOTIFY_OK" = 1 ]`, `install.sh:227-229`) — **recommend the
     latter**, it's the established idiom in this exact file already).
   - `ExecStart` sequence: switch the target device's elevator to `none`
     (device resolved from `IO_DEV_PATH`'s already-established auto-discovery,
     same as the BFQ scheduler section, `install.sh:231-234`); resolve
     `<major>:<minor>` from that device node (`stat -c '%t:%T'`, hex →
     decimal); write `io.cost.model` from the six coefficients in
     `IOCOST_MODEL_ENV`; conditionally write `io.cost.qos` with `enable=1`
     ONLY when both `DEV_IOCOST_RLAT_USEC` and `DEV_IOCOST_WLAT_USEC` are
     non-empty (D4) — otherwise write it with `enable=0` (model present,
     inert, matches CGROUP-NOTES.md's own recommended intermediate state).
   - `Before=`/`After=` ordering: after the device is available, before
     `dev.slice`/child slices start consuming it (mirror `dev.slice.in`'s
     own `Before=slices.target`).
3. **New `host-setup.env.example` keys**: `DEV_IOCOST_ENABLE` (empty=off),
   `IOCOST_MODEL_ENV` (path, mirrors `IO_BASELINE_ENV`), `DEV_IOCOST_RLAT_USEC`,
   `DEV_IOCOST_WLAT_USEC` (both empty=inert per D4). New env-file section
   placed after "Baseline benchmark cache" (depends on `IO_DEV_PATH` +
   calibration data both existing, same as that section's own dependency
   shape).
4. **`check.sh` verification section**, modeled on the memory-min-guaranteed
   inert-branch precedent (F3 fix, this session): report elevator state per
   `IO_DEV_PATH`'s device, `io.cost.model`/`qos` presence and `enable` value,
   and FAIL (not warn) on the specific drift case that precedent exists to
   catch: `DEV_IOCOST_ENABLE` set but the unit didn't fire (model absent) or
   fired with a stale/mismatched model (devno doesn't match `IO_DEV_PATH`'s
   current resolution — a device could change across a disk replacement).
5. **`mdt-host-setup-wizard.py` new interactive section** (mirrors
   `step_memory_min_guaranteed()`'s shape and tone exactly — explain
   mechanism, explain where/why, ground in this host's own live data, default
   to fully inert): offer to run/import calibration (point at the manual
   `iocost-calibrate.sh` command rather than shelling out to a different
   subproject, D6), then — only if a model is present — walk `DEV_IOCOST_ENABLE`,
   `DEV_IOCOST_RLAT_USEC`, `DEV_IOCOST_WLAT_USEC` with this session's own
   percentile table format as a worked example of how to read a `fio`
   latency probe's output, not as a numeric default to silently accept
   (D4).
6. **CGROUP-NOTES.md update**: the "What's blocking turning this on today"
   paragraph (line ~484) currently says the `rlat`/`wlat` half "has **not**"
   been measured — stale after this session's own investigation (§ "What
   this session's live investigation actually settled" #1). Update it to
   point at this plan instead of restating the now-resolved gap.

### Out of scope (explicitly, so a future reader doesn't assume it's covered)

- Any change to `DEV_IO_CAP_PCT`/`SWEEP_IO_CAP_PCT` or `mdt-apply-dev-caps.sh`'s
  `io.max` logic — D1/finding 4.
- Auto-running `iocost-calibrate.sh` from host-setup — D6.
- A formula that derives `rlat`/`wlat` from the percentile table — D4.
- `rpct`/`wpct`/`min`/`max` as operator-tunable keys — D5.
- Anything in `debian-install-v2` — D6.
- Rollback tooling beyond a documented manual procedure (revert the udev
  rule's device match / re-enable BFQ / set `enable=0` in `io.cost.qos` /
  disable+stop the new unit) — this is a first cut, not a full undo command.

## Execution plan for whenever this is picked up

1. Carve as a single package (all changes are one subproject, unlike the
   CIU-94/95 + wizard cross-repo split earlier this session) — normal
   `carve` skill flow, `nyxloom-trove/handoffs/` frontmatter+oracles.
2. Dispatch one fresh implementer (Sonnet is plausible — mechanical unit
   template + env keys + wizard section following three very close existing
   precedents — but the elevator-switch blast radius (D2) argues for Opus's
   design judgment on the `ConditionPathExists`-vs-conditional-install
   decision flagged as open in scope item 2).
3. Fresh adversarial review (never a fork) — this touches boot-time device
   scheduling, the review should specifically probe: what happens on a host
   with NO calibration data and `DEV_IOCOST_ENABLE` set anyway (must degrade
   safely, never half-apply); what happens if `IO_DEV_PATH` re-resolves to a
   different device between calibration time and unit-run time (D3's
   hardware-facts check should catch this, verify it actually does); does
   `check.sh`'s new section correctly distinguish "opted out" from "opted in
   but broken" (the exact bug class F3 fixed for the memory-min-guaranteed
   check this session).
4. Gate: mdt's `smoke` lane (`py_compile`) plus a manual live-host
   verification pass (this plan's own investigative tooling — the nsenter
   host-escape mechanism, `dmesg`/`journalctl` diagnosis approach — already
   proven this session) rather than a purely inert unit-file review, since
   the actual elevator switch can only be verified live.
5. Merge to `main` (mdt's full ship bar, no separate release step — same as
   the wizard round-2 package this session).
6. Update CGROUP-NOTES.md per scope item 6 as part of the same package, not
   a follow-up.

## Verification (once implemented)

- `cat /sys/block/vda/queue/scheduler` shows `[none]`, not `[bfq]`, only when
  `DEV_IOCOST_ENABLE` is set.
- `cat /sys/fs/cgroup/io.cost.model` / `io.cost.qos` show the expected devno
  and (only with both `RLAT`/`WLAT` set) `enable=1`.
- `check.sh`'s new section reports correctly across all three states: fully
  off (no findings), model-loaded-but-inert (`enable=0`, informational not a
  failure — matches CGROUP-NOTES.md's own recommended safe intermediate),
  and fully live.
- A live `fio` re-probe (same methodology as this session: default/root
  cgroup placement, representative load, `--direct=1`) after `enable=1`
  should show the SAME OR BETTER tail latency than this session's baseline
  table under contention — if it doesn't, the `rlat`/`wlat` target was
  picked wrong, not a code bug, and the operator's own manual entry (D4)
  is where that gets corrected, not the mechanism.
