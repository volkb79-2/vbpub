# run-gate-WAVE-RG55-P8 — LOG

One entry per commit (self-hash rule: a commit's own hash lands in the NEXT
entry). Package: mdt host-setup `dev-infra.slice` / `dev-gates.slice`.
Worktree `.worktrees/mdt-dev-slices`, branch `mdt-dev-slices`, base
`11ac5d67` (main).

## Orientation

Read in full: DESIGN-2026-09-12-liveness-placement-admission.md (all of §1-4,
§6-7 for context); host-setup/README.md, CGROUP-NOTES.md (full read —
"Per-container memory.min guarantees" section is load-bearing for M1, see
Decision ask below), host-setup.env.example, install.sh, units/dev.slice.in,
dev-interactive.slice.in, dev-background.slice.in,
dev-memory_min_guaranteed.slice.in, scripts/mdt-apply-dev-caps.sh,
scripts/check.sh, scripts/mdt-slice-audit.py (header only — generic
dev.slice-subtree walker, needs no change), mdt-host-setup-wizard.py (main()
orchestration + write flow only — template-surgery over full example text,
needs no change since new keys are simply untouched/preserved).
`modern-debian-tools-python-debug/templates/devcontainer.json`
(`containerEnv` block — the actual CGROUP_PARENT_DEV_* export path),
`DEVCONTAINER-LIFECYCLE.md` "Host resource governance" section.
mdt project gate: `modern-debian-tools-python-debug/run-gate.toml` (host-lane
`py_compile` smoke only, no pytest wired in yet).
Memory: `mdt-host-setup-companion.md`, `soulmask-memory-pressure-findings.md`
(confirms the D-19 motivation: gates currently share `dev-background.slice`
with the ~4 GiB dstdns stack; "the user's own instinct was a separate
dev-gates.slice; that remains unbuilt").

## Decision ask #1 (RW-9 — logged, not blocking)

CGROUP-NOTES.md's own documented ancestor-chain rule ("Per-container
memory.min guarantees" section) applies to `dev-infra.slice`'s
`MemoryMin=256M` (M1) exactly as it already applies to
`dev-memory_min_guaranteed.slice`'s ceiling: `dev.slice`'s own `MemoryMin`
must hand down at least as much as the SUM of every child that declares its
own `MemoryMin`, or the surplus/shortfall either leaks to siblings or leaves
a child's floor silently inert (the exact two rejected-alternative rows in
that section's table). The design doc's §3 layout table lists
`dev-infra.slice`'s `MemoryMin=256M` as a fixed, always-on default (not
opt-in like the guaranteed tier), so `dev.slice.in`'s own `MemoryMin` cannot
stay pinned to `DEV_MEMORY_MIN_GUARANTEED_CEILING` alone once `dev-infra`
exists — it would leave the daemon's floor silently inert, defeating D-18's
explicit point ("the only way a floor is real on this host"). Neither the
design doc nor the handoff's M1 text calls this out explicitly.

Taking the design's own established invariant (never re-deriving the VALUES
256M/768M/1G/100/50, only correctly wiring the ancestor arithmetic that makes
256M real): `dev.slice.in`'s `MemoryMin` becomes a render-time SUM of
`DEV_MEMORY_MIN_GUARANTEED_CEILING` (opt-in, default empty=0) and
`DEV_INFRA_MEMORY_MIN` (always-on default 256M), computed in `install.sh`
from the two source vars (never a third independently-set value — same
anti-drift discipline the existing doc already insists on for the two-var
case). `dev-memory_min_guaranteed.slice.in` itself is untouched (still pinned
to `DEV_MEMORY_MIN_GUARANTEED_CEILING` alone). `check.sh`'s existing
ancestor-mismatch check is extended to cover the new three-way arithmetic.
Continuing without stopping (RW-9).

## Commits

### M1 — dev-infra.slice / dev-gates.slice units + env keys (hash: see next entry)

New: `units/dev-infra.slice.in` (MemoryMin=256M/MemoryHigh=768M/MemoryMax=1G/
CPUWeight=100/IOWeight=50, no ManagedOOM), `units/dev-gates.slice.in`
(MemoryHigh=4G/MemoryMax=6G/MemorySwapMax=32G/CPUWeight=20/IOWeight=10,
ManagedOOMMemoryPressure=kill like dev-background). Env keys +
reasoned comment blocks in `host-setup.env.example`
(DEV_INFRA_MEMORY_MIN/_HIGH/_MAX/_CPU_WEIGHT/_IO_WEIGHT,
DEV_GATES_MEMORY_HIGH/_MAX/_SWAP_MAX/_CPU_WEIGHT/_IO_WEIGHT/
_OOM_PRESSURE_LIMIT), each citing the design doc and the 16 GiB-host/
re-tune-from-data framing M1 asked for.

Also (Decision ask #1 above): `units/dev.slice.in`'s `MemoryMin` changed
from a direct `@DEV_MEMORY_MIN_GUARANTEED_CEILING@` reference to
`@DEV_SLICE_MEMORY_MIN@`, a render-time-computed sum (wired in M2's
install.sh commit, since it is render logic) of that var and the new
always-on `DEV_INFRA_MEMORY_MIN` — without this, dev-infra.slice's 256M
floor would be silently inert per CGROUP-NOTES.md's own documented
ancestor-chain rule. `units/dev-memory_min_guaranteed.slice.in`'s
cross-reference comment updated to describe the sum instead of a 1:1 pin
(its own MemoryMin directive is unchanged — still reads
DEV_MEMORY_MIN_GUARANTEED_CEILING directly).

### M2 — install.sh render/install + placement report + check.sh (hash: 16f3a476 = M1)

`install.sh`: `_bytes_of()` helper (K/M/G/T -> bytes, base 1024, matching
systemd's own unit-file suffix convention) + `DEV_SLICE_MEMORY_MIN` computed
before any rendering happens; both new slices added to `RENDER_VARS`,
rendered (`units/dev-infra.slice.in` -> `/etc/systemd/system/dev-infra.slice`,
same for `dev-gates`), and started alongside the existing tiers. Top-of-file
description comment updated to list every installed slice.

`scripts/mdt-apply-dev-caps.sh`: a new read-only report block (placement is
create-time only, CGROUP-NOTES.md #1 — this never moves anything) — if a
container named `cgprofile-host-daemon` is present, WARN naming its actual
`HostConfig.CgroupParent` when it is not `dev-infra.slice`.

`scripts/check.sh`: `dev-infra`/`dev-gates` added to the existence/
ActiveState loop; two new "effective values" sections printing
`memory.min`/`memory.high`/`memory.max`/`cpu.weight`/`io.weight`/
`io.bfq.weight` (dev-infra) and `memory.high`/`memory.max`/
`memory.swap.max`/... (dev-gates) straight from cgroupfs. The
`dev-memory_min_guaranteed.slice` ancestor-chain check is generalized from a
1:1 pin to a 3-way sum (`dev.slice` MemoryMin == `dev-infra.slice` +
`dev-memory_min_guaranteed.slice`, FAIL naming the actual mismatch direction
either way) — matches the M1 `DEV_SLICE_MEMORY_MIN` change. Container-
placement informational section's hint text updated to name the two new
tiers' expected occupants.

`systemd-analyze verify` was NOT added — grepped the whole host-setup/ tree
first (M2 text: "if it is there already"); it is not used anywhere in this
project today, so nothing to extend.

Verification before commit: `bash -n` on all three scripts (clean) and
`shellcheck` (default severity) on all three — zero NEW findings; every
finding shellcheck reports (SC2015 x7, SC2181 x1) is on a pre-existing line
outside my diff, confirmed by line number against `git diff`.
