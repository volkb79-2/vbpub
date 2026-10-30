# wings-cgroups — TODO / backlog

## CLOSED 2026-10-30 — rebuild v1.13.3 provenance and forward-port v1.13.4

The running container `ptero-wings-patched-133-wings-1` still uses
`wings-local:1.13.3-cgroup.6` (image ID
`sha256:abd25f6ee6fadd865ba06d2b2192bfb466b5b5e0280751b78de76f273abf5468`).
Its Wings binary reports `v1.13.3-cgroup.6`, SHA-256
`8d41e704662c815c67ef3427dc92757a2c25c8685afb1db0346aca0e893a080f`. The
original image has no OCI labels, so its original series digest is unknown.
Using the current pinned v1.13.3 series, Go 1.24.11, the Dockerfile flags, and
`VERSION=1.13.3-cgroup.6` reproduced that binary byte-for-byte.

The provenance rebuild is local-only: `wings-local:1.13.3-cgroup.7` has image
ID `sha256:eb10b720d51694acbfe8e777020ba83c65172caf3bdd710757e6f3f6a5797e97`,
series digest `358e0116e1ceca1296daa156ea2b4e82ebf0719197d9731c6ee950523d028d66`,
and binary SHA-256
`0af71bce2de452a9a515b99ee5eac5318332c46e55fc5a542f2b15e106222941`. Its
binary SHA differs from `.6` because the embedded version value changed from
`.6` to `.7`.

Official Wings `v1.13.4` is pinned to
`747bc4393daf5d95fdf61d76382f5e23749cffe9`. The forward-port retains all 11
original Change-Ids, with two v1.13.4-specific guard patches. The full
build/vet/test workflow passed in the capped Go 1.27.2 container. An initial
patched `server` test panic was absent on vanilla v1.13.4 and was fixed by the
nil-configuration guard; the corrected suite passes.

One later full rerun had a single failure in the SFTP per-account connection
limit test. The vanilla `.4` SFTP tests and the immediate patched retry passed;
the next full patched workflow also passed. The cause is undetermined; see the
report if this test fails again.

The local-only `wings-local:1.13.4-cgroup.1` image has ID
`sha256:a4ceb4632f3e93b8c1c87b7e01d71211de590d27a90b5df954d5890cc4f554ec`,
series digest `923d9296e08ab5dcbaaec6a722625c99f0e14a968f424685e8c48dfc452257c7`,
and binary SHA-256
`ff35d939a2986b7813a6c37c7f5acdf6406a4df14e08f7a8b3c513c3c0ccb54c`.

**Deployment remains open and was not performed.** No production container,
Compose stack, or remote image registry was changed. The later service update
procedure is in [`wings-patchstack/CONSUMERS.md`](../wings-patchstack/CONSUMERS.md#rebuild-and-deploy-later); build, test, image labels, and patch-level results
are in the [forward-port report](../wings-patchstack/reports/forward-port-v1.13.3-to-v1.13.4.md).

The original `.6` OCI provenance cannot be recovered from the unlabeled image;
binary reproducibility establishes the executable build inputs, not an
original image digest. The existing Wings CI matrix still targets the `.3`
series and needs a separate workflow update outside this patchstack-only task.

---

## Design question: does the per-server `wings-<uuid>.slice` need to exist at all?

Affects: `v1-legacy` patch 0004 (`docker.per_server_slices`, T3b in
`STRATEGY.md`) — the currently-deployed mechanism.

**Finding (live, gstammtisch host, 2026-09-08, soulmask periodic-stall
investigation):** patch 0004 places every server under a derived
`wings-<dashless-uuid>.slice`, and the container's own cgroup lives one
level *below* that, as a nested `docker-<container-id>.scope`. `memory.min`/
`memory.low` set on the `wings-<uuid>.slice` layer only reaches the actual
container process if the host's cgroup2 mount carries
`memory_recursiveprot` — without it, cgroup v2 requires every level to
redeclare protection itself, so the parent's value is silently discarded for
the child with **no other symptom**: `systemctl show` and the cgroupfs file
both still report the configured number. Confirmed via `cgroup-profiler`'s
effective-limits computation on this exact host: it computed `strict_min=0B`
for both Soulmask containers at a point where `memory_recursiveprot` had
been stripped, versus their true intended 476.9M / 2.9G had recursion held.

This is not a one-off: `wings-cgroups/CGROUP-SEMANTICS.md` §5 already
documents the flag being stripped by a runtime remount once (observed
2026-07-17), the mdt host-setup companion's sweep timer comment dates
another occurrence (2026-08-28), and it happened again live tonight
(restored by the same timer's self-heal at 13:07:57 CEST, mid-investigation)
— most plausibly triggered by one of the `--privileged`/`--cgroupns=host`
container escapes routinely used for host diagnostics on this box (see
`modern-debian-tools-python-debug/TODO.md`'s host-escape-helper request,
filed the same session). Whatever the trigger, the pattern recurs on a
timescale of weeks, and each occurrence silently defeats every per-server
floor on the host until the next sweep catches it.

**Design question this raises:** T3b's own doc (`STRATEGY.md` §T3b) already
notes "Wings recreates the container on every server start, and the slice is
re-ensured at the same moment" — i.e. Wings already re-applies slice
properties at exactly the moment it would need to re-apply container-level
properties too. That weakens the usual justification for needing a *separate
parent slice* purely for lifecycle reasons (surviving container recreation):
if Wings is already doing the ensure-work at container-create time regardless,
it could set `memory.min`/`memory.low` directly on the container's own
`docker-<id>.scope` at that same moment, which would be unconditionally
effective at that leaf with no dependency on `memory_recursiveprot` at all.

**Counter-consideration (not a resolution, just the other side):** the
per-server slice still does real work beyond kernel enforcement — it's the
declarative, inspectable unit that `memory_min_budget` + `budget_policy`
(clamp/refuse/distribute) arithmetic operates on and that an admin can
`systemctl show wings-<uuid>.slice -p MemoryMin` to audit a server's
committed floor independent of whether a container is currently running.
Dropping the slice entirely would need something else to hold that
bookkeeping role.

**Ask:** evaluate whether `memory.min`/`memory.low` (not `memory.high`/`max`,
which are fine as pure container-scope settings already) should be set
**directly on the container's own cgroup scope** — either instead of the
parent slice, or in addition to it as a belt-and-suspenders duplicate — so
the floor's actual kernel enforcement no longer depends on a host-wide mount
flag that has now drifted three times on this host alone.

**Recommendation (sharpened after discussion, 2026-09-08): make
`budget_policy: distribute` the default, not just an alternative to
consider.** Under `distribute`, Wings' own `internal/cgroups` code does no
budget arithmetic at all — it just assigns whatever the egg/panel
`WINGS_CG_MEMORY_MIN`/`_LOW` values define directly onto each server's
slice, honestly, with nothing to clamp or shrink. The aggregate safety net
moves entirely to the kernel: the host sets the real total budget once,
directly, as `wings.slice`'s own `MemoryMin` (independent of Wings, e.g. via
`setup-cgroups.sh`/`systemctl set-property`), and cgroup v2's native
proportional-sharing algorithm — already exercised the moment
`memory_recursiveprot` is set, no new code needed — arbitrates among
children if their declared floors collectively exceed that ceiling. This is
a net simplification of the T3b patch surface, not just a policy flip: less
Wings-side code to maintain, no clamp-specific arithmetic left to get wrong.
The per-server slice still holds the bookkeeping/audit role from the
counter-consideration above; only the shrinking logic goes away.

Note for whoever picks this up: no live evidence establishes what Wings'
own `clamp` arithmetic actually computes in practice. The specific
500M/3000M (and later 4.75G/3.25G) values observed live tonight were the
operator's own manual `systemctl set-property` overrides, not Wings-computed
clamp output — don't cite this file as evidence of what clamp itself
produces without separately reading/testing that code path.

_Captured 2026-09-08 from the live gstammtisch soulmask-stall investigation;
filed by Claude per operator request. No `CHANGES.md` or managed nyxloom
backlog exists in this project — this file mirrors the same TODO.md
convention used for the two other same-session filings
(`modern-debian-tools-python-debug/TODO.md`,
`scripts/debian-install-v2/TODO.md`)._

---

## CLOSED 2026-09-08 — the per-server slice is retired

**Decision.** The answer to the design question above is no: the
`wings-<uuid>.slice` layer does not need to exist, and it is gone. Patch 0004
was rewritten. Placement is flat — `HostConfig.CgroupParent = wings.slice` —
and Wings applies the same property set
(`MemoryMin`/`MemoryLow`/`MemoryHigh`/`MemoryMax`/`CPUWeight`/`IOWeight`)
directly to the container's own `docker-<id>.scope` with D-Bus
`SetUnitProperties`. Wings never creates a unit; it only sets properties on the
one Docker already made. The tree is `wings.slice → docker-<id>.scope`.

This resolves the finding that opened this file: the floors now live on the leaf
that owns the pages, so they no longer depend on `memory_recursiveprot` at all.
The flag stays a prerequisite for the node tier's own `wings.slice` floors and
for any admin-managed slice reached via `WINGS_CGROUP_PARENT` — it just stopped
being the single point of silent failure for every per-server floor on the node.

**The `distribute` recommendation went further than recommended.** Rather than
making `budget_policy: distribute` the default, the knob is gone: `budget_policy`,
`clamp`, `refuse` and `memory_min_budget` were all removed. There is no budget
arithmetic in Wings any more. What remains is what `distribute` already was —
apply the requested floors as stated, correct nothing — with the kernel's own
cgroup-v2 proportional-by-usage sharing (`CGROUP-SEMANTICS.md` Rule 5) doing the
arbitration, and zero Wings code participating in it. One informational log line
survives as a tripwire: if the floors Wings currently has applied across live
scopes would sum above `wings.slice`'s own `MemoryMin` (read live from systemd),
it says so, and changes nothing. The note above about no live evidence for what
`clamp` actually computed is now moot — that code no longer exists.

**The counter-consideration was accepted, not refuted.** The per-server slice
really did hold a bookkeeping role, and losing it is a real cost of this
decision. The audit surface moves to `systemctl show docker-<id>.scope`, which
exists **only while the container is running**: there is no longer any way to
ask systemd what floor a *stopped* server is committed to. The committed intent
now lives only in the egg variables and `config.yml` — declarative, but not
inspectable through the same channel that reports what the kernel is actually
enforcing. Nothing replaced it. If that bookkeeping is ever needed back, it
needs a new home, not the slice.

A second accepted cost, new with this shape: the scope does not exist until the
container's init process launches, so the properties can only be applied **after**
`ContainerStart()`. The few-millisecond window in which a container runs with
only the tier's protection is known and deliberate, not a bug to be raced away.

**What shipped:** the 0001–0009 series — see the table in
`../wings-patchstack/README.md`, which also argues the two retirements that came
with this pass (the old 0005, `io_bfq_weight`; the old 0011,
`WINGS_CG_RAMDISK_UNITS`). Deployment is `v1-legacy/SETUP.md`; the semantics are
`CGROUP-SEMANTICS.md`; the decision record entry is `STRATEGY.md` §T3b's
2026-09-08 addendum.
