# RG-55 → dstdns adoption brief (lane resource profiling)

**Status: DRAFT — written by the RG-55 controller, NOT dispatched.** dstdns
adoption is deliberately LATER (operator decision at the RG-55 interview,
2026-09-12: "test and finish here and start rollout to dstdns only once we
are sure we have polished tools"). This brief becomes the dispatch input for
that rollout. Fields marked `⟨P3⟩` are filled at the end of the RG-55 wave
(released versions, measured DAMON overhead).

| item | value |
|---|---|
| run-gate release carrying RG-55 | `⟨P3⟩ 23.7.0 (rev 41)` |
| cgroup-profiler daemon release | `⟨P3⟩ cgprofile-v1.0.0`, host singleton `cgprofile-host-daemon` |
| measured DAMON overhead on a profiled lane | `⟨P3⟩` |
| plan of record | `run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md` (D-1..D-16) |
| interface contract | `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` |
| consumer contract (the authoritative "what a consumer does") | run-gate `CONSUMERS.md` §6 "Profiling (RG-55)", "The footprint manifest"; `LANE-AUTHORING.md` "Let `run-gate.footprint.json` set the budget" |
| rulings that shape the consumer view | controller log RW-11 (basic-path file list), RW-12 (tick shape), RW-17 (`RUN_GATE_PROFILE` override), RW-21 (scope `container` absolute counters), RW-24 (byte medians nearest-rank) |

## 1. What dstdns gets, in its own terms

The interview's driver: *"information about resource requirements will decide
if a new run can be started or need to be queued"* and *"anticipating hot RAM
need is key for us"*. RG-55 delivers the MEASUREMENT half of that; RG-56 (next
wave) spends it on admission.

- **Every container lane is profiled by default**, best-effort (SPEC R-36h /
  R-43): profiling can never change a verdict, never raise, never add a
  blocking wait. Two paths:
  - **daemon path** (precise): run-gate asks the host singleton
    `cgprofile-host-daemon` via `docker exec … cgprofile ctl start/stop` —
    exact `memory.peak`, CPU seconds and cores, PSI deltas, `memory.events.
    local`, and a DAMON hot/warm/cold set over time (the "how much RAM is
    really hot" signal).
  - **basic path** (fallback, no daemon): run-gate samples the lane's cgroup
    itself every 5 s (RW-12) — peak from `memory.peak` for ephemeral lanes,
    sampled max for exec lanes; no hot-set.
- **Per-run records** land in the per-instance, gitignored history store
  (`.run-gate/history.json`, schema 2, old schema-1 entries migrate in place):
  `duration_seconds`, `memory.peak_bytes`, `cpu_seconds`, `cpu_cores_avg`,
  stall seconds, plus `method` (`daemon`/`basic`), `scope`, `source`
  (`memory.peak` / `sampled-max`). `./run-gate.py history [--json]` shows
  them; `stats` gives nearest-rank p50 for byte series (RW-24).
- **The committed footprint manifest** `run-gate.footprint.json` (next to
  `run-gate.toml`, **TRACKED**) is the machine-and-human-readable distillate:
  per lane, the median peak/CPU/duration over profiled PASS runs. Written by
  `./run-gate.py footprint --write`; refused while no lane has a profiled
  PASS run; lanes without one are omitted (absent is never zero).
  `doctor` flags drift (> `[footprint] tolerance_pct`, default 25 %) and
  staleness (> `max_age_days`, default 30). The run path feeds the lane's
  committed median to the daemon as `meta.expected` and prints
  `| manifest <n> MiB` in the disclosure line.
- **RG-48** ships in the same release: `resources.cpus` per lane → a real
  `docker run --cpus`; `doctor` warns on `-n auto`/`--workers auto` argv.
- **Series and diagrams stay on the daemon** (`cgprofile ctl report`): only
  the distillate is committed, as decided at the interview.

## 2. dstdns today vs. the two lane models

dstdns's `run-gate.toml` declares one environment, `test-runner`
(`mode = "exec"` into the long-lived `dstdns-<tag>-test-runner` container),
and every lane (`test-runner`, `assay-dlq`, `schema`, `scale-admission`, …)
runs in it. Under RG-55 that is **scope `container-shared`**:

- peak = sampled max of the SHARED container minus the baseline read just
  before the lane starts (`peak_over_baseline_bytes`); `source: sampled-max`;
  the daemon attributes the lane's processes through the
  `RUN_GATE_PROFILE_SESSION` token in the exec environ (`targets_seen`).
- Anything else running in that container at the same time pollutes the
  number. run-gate's exec mutex (R-41) already serialises exec lanes of ONE
  project; a second dstdns checkout sharing the same runner container is not
  covered — profile with the stack otherwise quiet.
- No `memory.events.local`/OOM attribution and a container-wide hot-set.

The plan's D-decisions design for **per-lane ephemeral containers** (exact
`memory.peak`, per-lane DAMON, clean OOM attribution). The recommended
evolution, in dstdns's own pace:

1. Adopt as-is (exec lanes, shared scope) — already useful for admission
   (the peak-over-baseline of the unit suite and of `assay-dlq` are the
   numbers RG-56 needs).
2. Move the heavy, stack-independent lanes (`test-runner` unit suite,
   mutation lanes) to an ephemeral environment (`mode` ephemeral, same
   image, `resources.memory`/`resources.cpus` sized off the manifest).
3. Keep exec for lanes coupled to the live stack (`schema`, anything that
   needs the stack network/DSN) unless dstdns wants them ephemeral on the
   stack network.

## 3. Adoption checklist (the future implementer's task list)

Work happens in a `ciu worktree` off the LATEST dstdns `main`, never in the
primary checkout; the RG-55 wave never touched `/workspaces/dstdns`.

1. **Install run-gate `⟨P3⟩23.7.0`** in the dstdns devcontainer the way dstdns
   installs run-gate today (its `nyxloom-trove/GUIDE.md`); confirm
   `run-gate --help` prints rev 41. Read run-gate `CHANGES.md` `[23.7.0]` —
   two BREAKING notes: (a) `footprint` is now a reserved lane name (dstdns has
   no such lane; verify), (b) RG-53 makes run-gate's OWN diff-coverage judge
   count untaken branches — only relevant if a dstdns lane calls run-gate's
   `coverage_gate`; dstdns's assay-judged lanes are unaffected.
2. **Daemon presence is host infra, not a dstdns concern**: it is brought up
   from vbpub (`cd /workspaces/vbpub/scripts/cgroup-profiler && ciu up`) as
   the ciu-managed singleton in the interactive tier; dstdns never starts,
   stops or declares it. A dstdns runner without `docker exec` rights to the
   host daemon (locked-down CI, sandboxed agent) sets `RUN_GATE_PROFILE=off`
   (RW-17) instead of eating one failed exec per tick; `doctor`'s
   "profiler" check reports reachability either way.
3. **`./run-gate.py doctor`** in the worktree: profiler reachability, the
   `[profile]` effective settings, RG-48 `-n auto` warnings, the R-29 checks
   that were inert before this wave.
4. **Run each lane once profiled**, one at a time, stack quiet (host rule:
   PSI is the signal, ≤ 2 gate containers estate-wide, `--cpus 3`); read
   `./run-gate.py history` and check `method`, `scope`, `source`,
   `targets_seen` are what §2 predicts. Any surprise about the TOOL is filed
   in vbpub's run-gate backlog (cross-repo convention), never worked around
   locally.
5. **After a handful of profiled PASS runs per lane**: `./run-gate.py
   footprint --write`, commit `run-gate.footprint.json`, and size
   `resources.memory`/`resources.cpus` off it with headroom
   (`LANE-AUTHORING.md`). Re-run `footprint --write` when a lane's shape
   changes; `doctor` tells you when it is stale or drifted.
6. **Record** in dstdns's own docs (`docs/testing/RIGOR-COVERAGE-POLICY.md`
   or the GUIDE) that the manifest is the lane budget's source of truth and
   that `.run-gate/` stays gitignored.
7. **Hand RG-56 its requirements**: dstdns is the motivating consumer for
   admission. The brief for that wave needs, from dstdns: which lanes may run
   concurrently, the host's memory ceiling to schedule against, and whether
   queueing is per project or estate-wide.

## 4. Out of scope for this adoption (by ruling)

- Admission/queueing on the registry — **RG-56**, next wave.
- Bare-host lanes record no profile — **RG-57** (dstdns has none today).
- Per-lane containers for dstdns — §2 step 2, dstdns's call and pace.

## 5. Dispatch shape when the operator says go

One Sonnet implementer (fresh, checkpoint clause on) in the dstdns `ciu
worktree`, this brief + run-gate `CONSUMERS.md` §6 + `LANE-AUTHORING.md` as
the handoff, the HOST LOAD block verbatim; one FRESH Opus xhigh adversarial
reviewer with live probes (the profiled lane runs, the manifest, `doctor`);
merge on ACCEPT under dstdns's own pipeline rules. No dstdns code change is
required for step 1 of §2 — the first package is configuration, one manifest
commit and documentation.
