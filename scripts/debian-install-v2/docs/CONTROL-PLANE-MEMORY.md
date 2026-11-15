# Control-plane service MemoryMin values

The default debian-install-v2 profile gives the host's control-plane services
fixed reclaim-protection floors. These values do not scale with installed RAM.
They are written as per-service drop-ins under
`/etc/systemd/system/<unit>.d/90-vbpub-memory-min.conf` and are checked against
`systemctl show -p MemoryMin <unit>` after installation.

**The values below are policy placeholders, not measurements.** The operator
wants the floors to cover only each service's hot working set (so the host stays
responsive under thrashing); the total below is considered too large and the
values will be replaced by a follow-up from a per-service hot-set measurement.
Tests pin the table-versus-rendered equality, not these numbers.

| Unit | MemoryMin | Evidence and scope |
|---|---:|---|
| `ssh.service` | 32 MiB | Unmeasured per service; Debian base unit used and configured by the installer. |
| `docker.service` | 256 MiB | Unmeasured per service; fixed floor for Docker Engine installed by the default profile. |
| `containerd.service` | 128 MiB | Unmeasured per service; fixed floor for `containerd.io` installed with Docker. |
| `systemd-journald.service` | 128 MiB | Unmeasured per service; Debian base unit configured by the installer. |
| `cgprofiled.service` | 64 MiB | Unmeasured per service; unit is installed by cgprofile's `cgprofiled install`. |
| `mdt-psi-guard.service` | 32 MiB | Unmeasured per service; unit is installed by mdt-cockpit `host-setup/install.sh`. |
| **Total** | **640 MiB (671,088,640 bytes)** | Within the requested 450 MB–1.5 GB band. |

The 2026-11-14 read-only measurement supplied with fleetd FD-007 reports about
450–500 MB hot RSS for the whole control plane on two 8 GiB hosts. The
installer's `LT-*-REPORT.md` evidence, including `LT-MEM-REPORT.md`, contains
no per-service RSS measurements; LT-MEM records host memory tuning.
The host-setup memory source in `mdt-cockpit/host-setup/mdt_capacity.py`
describes an aggregate `system.slice` working set, not attribution to these
six units. Accordingly, the per-service values above are conservative fixed
policy choices and are all marked unmeasured. The 640 MiB total exceeds the
measured 450–500 MB whole-plane RSS because the floors are deliberately
over-reserved for fleetd's capacity calculation. `M`, `MiB`, and the byte sum use
systemd's 1024-based memory units, as specified by
[systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).

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
`mdt-cockpit/host-setup/units/mdt-psi-guard.service`. Neither owner currently
sets service-level `MemoryMin`; DI2's drop-ins supply those values when the
units are present.

DI2 does not change existing slice or service limits. cgprofile's
`cgprofile.slice` template has `MemoryMin=128M`, `MemoryHigh=768M`, and
`MemoryMax=1G`; its service has `MemoryHigh=512M`, `MemoryMax=768M`, and
`MemorySwapMax=0`. The 64 MiB service floor is below the existing slice floor.
The psi-guard unit template has no memory directives. DI2's added drop-ins
contain only the per-service `MemoryMin` values in the table.

The mandatory units in the default DI2 profile are `ssh.service`,
`systemd-journald.service`, `docker.service`, and `containerd.service`. The
Docker pair is omitted when `run_docker_install=false`. `cgprofiled.service`
and `mdt-psi-guard.service` are optional: verification checks their exact
`MemoryMin` when systemd reports them loaded, and skips only
`LoadState=not-found` or `LoadState=masked`. A missing load-state result or a
loaded unit with a missing, zero, or wrong `MemoryMin` still fails verification.
The fact collector must treat an absent optional unit as a missing fact, never
invent a zero-valued or configured value; fleetd's current all-six-positive
rule then uses its documented profile constant until all six facts are present.

The kernel caps a service's effective `memory.min` by its ancestors' protection;
this installer sets no `MemoryMin` on `system.slice`, so reclaim protection (as
distinct from the reported number) is unconfirmed until checked on a live host
(DIV2-BL-09).

These DI2 drop-ins contain only `MemoryMin`; debian-install-v2 does not emit
`MemoryMax`, `MemoryHigh`, or `MemoryLow` for the listed services. Existing
completed installs are not modified by this change. The two measured 8 GiB
hosts need a separately scheduled, operator-approved in-place drop-in
application or reinstall; neither action was performed here.
