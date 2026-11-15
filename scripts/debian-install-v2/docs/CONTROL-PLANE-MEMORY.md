# Control-plane service MemoryMin values

The default debian-install-v2 profile gives the host's control-plane services
reclaim-protection floors sized to each service's **measured hot working set**.
These values do not scale with installed RAM. Per-service drop-ins are written
under `/etc/systemd/system/<unit>.d/90-vbpub-memory-min.conf`, one derived
slice drop-in under `/etc/systemd/system/system.slice.d/90-vbpub-memory-min.conf`,
and both are checked against `systemctl show -p MemoryMin <unit>` after
installation.

**THE VALUES ARE MEASURED, NOT PLACEHOLDERS.** They come from the 2026-11-15
per-service hot-working-set measurement on the two 8 GiB test hosts (report
`control-plane-hotset-REPORT.md`, evidence directory `control-plane-hotset`,
outside this repository). Each floor is 1.25 × the service's measured hot set
`H`: file pages re-read within 60 s after `memory.reclaim`, plus anonymous
pages referenced within 300 s under a probe workload. The per-service value
taken here is the **max of the two hardware flavors**, so one table serves
both hosts rather than a per-host table.

| Unit | MemoryMin | Evidence and scope |
|---|---:|---|
| `ssh.service` | 10 MiB | Measured `H` on both hosts; 1.25 × `H` rounded up. |
| `docker.service` | 210 MiB | Measured on v1001 (clean at 1.25 × `H`); r1002 inferred between measured 1.0 × and 1.5 × `H`. |
| `containerd.service` | 92 MiB | Measured; holds the container shims, so it grows with running containers. |
| `systemd-journald.service` | 24 MiB | Measured `H`; r1002 `H` was tiny and its floor is set by the replication maximum (inferred upward). |
| `cgprofiled.service` | 4 MiB | Measured `H`; redundant in protection terms because `cgprofile.slice` already has `MemoryMin=128M`, kept so fleetd's reserve rule sees a positive per-service fact. |
| `mdt-psi-guard.service` | 5 MiB | Measured `H`; absent on v1001 (see optional-unit handling). |
| **Total** | **345 MiB (361,758,720 bytes)** | Sum of the six floors above. |
| **`system.slice`** | **373 MiB (391,118,848 bytes)** | Derived, not hand-written: the floors of the units that live under `system.slice` (`ssh` 10, `docker` 210, `containerd` 92, `journald` 24, `psi-guard` 5 = 341 MiB) plus 32 MiB headroom. |

`M`, `MiB`, and the byte sums use systemd's 1024-based memory units, as
specified by
[systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).

## What is measured and what is inferred

| Claim | Status |
|---|---|
| Per-service hot set `H` for all six services (both hosts) | MEASURED (phase 1, idle host, probe workloads). |
| Floors at 1.25 × `H` on v1001 | MEASURED (high confidence): the floors were monotonic — 1.5 ×, 1.25 × and 1.0 × `H` stayed clean, 0.75 × `H` left 2–8 MiB residual, 0.6 × and 0.4 × `H` broke; 1.0 × `H` was clean in three independent steps. |
| Floors at 1.25 × `H` on r1002 | INFERRED (medium-low confidence): r1002 already carries `MemoryLow=1G` on `system.slice` from mdt-cockpit host-setup, and the kernel's active-list promotion made later no-floor steps nearly refault-free, so later steps could not discriminate. The bracket is measured: 1.5 × `H` clean in every run, 1.0 × `H` left 11–66 MiB docker refaults, 0.5 × and 0.25 × `H` as bad as no floor. |
| `system.slice` floor with 32 MiB headroom | Headroom is INFERRED: other `system.slice` children (logind, dbus, udevd, hostnamed — about 1–3 MiB hot each) were not measured individually; the experiment used 64 MiB, and the report calls 16–32 MiB sufficient. |
| Unit floors need a `system.slice` floor | MEASURED on v1001 (see below). |
| The floors are a bound under many containers | NO — INFERRED, not measured: `docker` and `containerd` hot sets grow with the number of running containers. Treat the table as a base. |

**Re-measure when the Docker or containerd version changes**, or when a host
runs a materially different container count. The measurement method and scripts
live with the report's evidence directory (`tools/hotset.py`); a re-measurement
uses the same protocol and replaces this table, not a new hand-picked one.

## Why `system.slice` gets its own floor

The kernel caps a child's effective `memory.min` by what its ancestors hand
out. Both test hosts mount cgroup2 with `memory_recursiveprot` (this installer's
`cgroup2-flags.service` restores it if a remount strips it), so while
`system.slice.memory.min` is 0 the per-unit floors protect nothing. Measured on
v1001: with 1.5 × `H` unit floors but no slice floor, docker refaulted 231–251
MiB and containerd 112/50, journald 59/90 MiB — the range of the unprotected
baselines; with the same unit floors plus a `system.slice` floor, docker
refaulted 0–4 MiB, containerd 0 and journald 0. r1002 was inconclusive for that
specific test because its pre-existing `MemoryLow=1G` already protects the
slice softly; the v1001 result is treated as the general rule.

The installer therefore writes `MemoryMin` on `system.slice` too. The value is
**computed in code from the per-service table** — sum of the floors of the
units that live under `system.slice`, plus 32 MiB headroom — and is never a
second hand-written number. `cgprofiled.service` runs under
`cgprofile.slice`, not `system.slice`, so its floor does not enter that sum.
When `run_docker_install=false`, the Docker and containerd floors are omitted
from the profile and the derived slice value drops with them.

The installer's `system.slice` drop-in contains `MemoryMin` only. It does not
touch the `MemoryLow` (or any `MemoryHigh`/`MemoryMax`) that another owner may
already have set on `system.slice` — for example mdt-cockpit host-setup's
`system.slice.d/50-mdt-dedicated-dev.conf` `MemoryLow=1G` on r1002, which stays
as it is.

**Configured versus effective.** Verification compares the *configured* value
that `systemctl show -p MemoryMin` reports; it does not read the cgroup's
effective protection. The kernel silently clamps effective protection (to the
ancestors' floors and to what the cgroup actually uses), so a matching readback
does not by itself prove that pages are protected. The effect was measured on
v1001 only (see above). The floors are fixed values with no check against
installed RAM (DIV2-BL-12); they protect at most the pages the cgroups really
use. The derived slice value counts `mdt-psi-guard.service` (5 MiB) even on a
host where that optional unit is absent (v1001), a deliberate small
over-reservation so the value does not depend on host state.

## Known limit: floors above about 460 MiB were not explained

In the same measurement, the steps with the largest `system.slice` floors
(608 MiB and 585 MiB) showed journald and docker refaults that the 430–455 MiB
steps did not, although the units' `memory.current` stayed far below their
floors. Candidate causes (journal file churn from `journalctl -t` scans,
previous-step residue) were not isolated, so the anomaly is open. The installer
enforces this as a ceiling: a derived `system.slice` value above
**460 MiB** makes the configuration step refuse rather than ship, so a future
table change cannot silently cross into the unexplained range. The current
derived value is 373 MiB. Tracking: DIV2-BL-10.

## Optional units, fleetd's reserve rule, and what remains open

FD-007's fleetd-reserve service list, expressed as actual systemd unit names,
is `ssh.service`, `docker.service`, `containerd.service`,
`systemd-journald.service`, `cgprofiled.service`, and
`mdt-psi-guard.service` (psi-guard). debian-install-v2 writes all six
drop-ins, including for the two optional services it does not install. The
drop-in for an absent optional unit is inert. `cgprofiled.service` is installed
by cgprofile's `cgprofiled install` from
`cgprofile/src/cgprofile/cli/installer.py`; its base unit is
`cgprofile/src/cgprofile/data/cgprofiled.service.in`, which also has
`MemoryHigh` and `MemoryMax` owned by cgprofile. `mdt-psi-guard.service` is
installed by `mdt-cockpit/host-setup/install.sh` from
`mdt-cockpit/host-setup/units/mdt-psi-guard.service`. Neither owner sets
service-level `MemoryMin`; DI2's drop-ins supply those values when the units
are present.

The mandatory units in the default DI2 profile are `ssh.service`,
`systemd-journald.service`, `docker.service`, and `containerd.service`. The
Docker pair is omitted when `run_docker_install=false`. `cgprofiled.service`
and `mdt-psi-guard.service` are optional: verification checks their exact
`MemoryMin` when systemd reports them loaded, and skips only
`LoadState=not-found` or `LoadState=masked`. A missing load-state result or a
loaded unit with a missing, zero, or wrong `MemoryMin` still fails verification.
`system.slice` is a built-in slice and always exists, so its derived floor is
verified unconditionally.

The fact collector must treat an absent optional unit as a missing fact, never
invent a zero-valued or configured value. fleetd's reserve rule now resolves
one term per listed service (FD-007c, landed): an absent optional unit is
declared absent from that host's facts and contributes 0, every other service
contributes its reported floor or this table's documented default, and the
fixed profile constant is used only when no per-service fact exists at all.
v1001 has no psi-guard unit, so it now contributes 0 for that service plus the
five measured floors, rather than the 768 MiB constant.

What remains open:

- **Per-container increment** (DIV2-BL-11): docker/containerd floors are a
  base for a small container count; an increment for N running containers has
  not been measured.
- **Large-floor anomaly** (DIV2-BL-10): explained above, enforced as a ceiling
  until it is understood.
- **No RAM sanity bound** (DIV2-BL-12): the floors are not compared with
  installed RAM; measured only on 8 GiB hosts.
- **r1002 confidence**: the 1.25 × `H` figure on that host flavor is inferred
  between measured 1.0 × and 1.5 × `H`; only a re-measurement on that flavor
  can raise the confidence.
- **Retrofit**: existing completed installs are not modified by this change and
  need a separately scheduled, operator-approved in-place drop-in application
  or reinstall (DIV2-BL-08).

DI2 emits no `MemoryMax`, `MemoryHigh`, or `MemoryLow` for any of its units or
for `system.slice`. The psi-guard unit template has no memory directives; the
`cgprofile.slice` template has `MemoryMin=128M`, `MemoryHigh=768M`,
`MemoryMax=1G`, and `cgprofiled.service` has `MemoryHigh=512M`,
`MemoryMax=768M`, `MemorySwapMax=0` — all owned by cgprofile, and the 4 MiB
service floor sits below the existing slice floor.
