# Control-plane service MemoryMin values

The default debian-install-v2 profile gives the host's control-plane services
fixed reclaim-protection floors. These values do not scale with installed RAM.
They are written as per-service drop-ins under
`/etc/systemd/system/<unit>.d/90-vbpub-memory-min.conf` and are checked against
`systemctl show -p MemoryMin <unit>` after installation.

| Unit | MemoryMin | Evidence and scope |
|---|---:|---|
| `ssh.service` | 32 MiB | Unmeasured per service; Debian base unit used and configured by the installer. |
| `docker.service` | 256 MiB | Unmeasured per service; fixed floor for Docker Engine installed by the default profile. |
| `containerd.service` | 128 MiB | Unmeasured per service; fixed floor for `containerd.io` installed with Docker. |
| `systemd-journald.service` | 128 MiB | Unmeasured per service; Debian base unit configured by the installer. |
| **Total** | **544 MiB (570,425,344 bytes)** | Within the requested 450 MB–1.5 GB band. |

The 2026-11-14 read-only measurement supplied with fleetd FD-007 reports about
450–500 MB hot RSS for the whole control plane on two 8 GiB hosts. The
installer's `LT-MEM-REPORT.md` records host memory tuning, not per-service RSS.
The host-setup memory source in `mdt-cockpit/host-setup/mdt_capacity.py`
describes an aggregate `system.slice` working set, not attribution to these
four units. Accordingly, the per-service values above are conservative fixed
policy choices and are all marked unmeasured. `M`, `MiB`, and the byte sum use
systemd's 1024-based memory units, as specified by
[systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).

FD-007's fleetd-reserve service list, expressed as actual systemd unit names,
is `ssh.service`, `docker.service`, `containerd.service`,
`systemd-journald.service`, `cgprofiled.service`, and
`mdt-psi-guard.service` (psi-guard). This debian-install-v2 table contributes
the first four. If `run_docker_install=false`, this installer does not
install/configure Docker or containerd and omits those two units from its
drop-ins and readback.

`cgprofiled.service` is installed by the cgprofile package, outside this
installer. Its repository unit has separate `MemoryHigh`/`MemoryMax` settings,
and its host containment is `cgprofile.slice`; neither is a
debian-install-v2 service-level fact. `mdt-psi-guard.service` is installed by
mdt-cockpit host-setup. Both are therefore omitted from this installer's sum
and drop-ins; their owning packages provide their own service-level facts for
the six-unit fleetd-reserve list.

These drop-ins contain only `MemoryMin`; debian-install-v2 does not emit
`MemoryMax`, `MemoryHigh`, or `MemoryLow` for the listed services. Existing
completed installs are not modified by this change. The two measured 8 GiB
hosts need a separately scheduled, operator-approved in-place drop-in
application or reinstall; neither action was performed here.
