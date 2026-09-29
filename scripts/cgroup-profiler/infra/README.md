# infra/ — host-side units the cgprofile deployment ships but does not install

The daemon container never writes to the host's systemd config (it has no
volume access to `/etc/systemd/system`, and the RW-35(c)/D-25 write
whitelist is cgroup files only) — installing a unit here is always an
OPERATOR step, done once per host, before or independently of `ciu up`.

## `cgprofile.slice` (D-29)

The daemon's own top-level containment — see the unit file's own header
comment for the full design rationale (why top-level, why no `ManagedOOM`,
the `MemoryMin` sizing argument). Precedent: `srdm.slice`
(`shared-ramdisk-depot-manager/systemd/srdm.slice`), `nyxloom-daemon.slice`
(`nyxloom/infra/slices/nyxloom-daemon.slice`) — same pattern, a daemon
package shipping a REFERENCE unit the operator installs, never one the
software installs itself.

### Install

```sh
sudo cp infra/cgprofile.slice /etc/systemd/system/
sudo systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/cgprofile.slice
```

No `systemctl start`/`enable` step: unlike a `.service`, a `.slice` unit
needs no activation — it becomes real the first time anything (systemd
itself, or `docker run --cgroup-parent cgprofile.slice`) references the
name. `daemon-reload` alone is enough for a subsequently-started daemon
container to land inside the properly-limited slice instead of an
implicitly-created unbounded one.

Do this **before** `ciu up` renders and starts the daemon stack for the
first time on a given host, so the very first container already lands
under the real limits rather than an unbounded implicit slice that a later
`daemon-reload` cannot retroactively fix for an already-running container
(cgroup parent is chosen at container creation; moving a live container to
a different slice needs a restart either way).

### What happens when it is NOT installed

`ciu.compose.yml.j2` authors `cgroup_parent: cgprofile.slice` unconditionally
(D-29: no environment variable, no hardcoded-slice-name fallback debate —
this one value is authored outright). Docker/systemd do not refuse an
unknown parent slice name: systemd auto-vivifies `cgprofile.slice`
implicitly the first time it is referenced, with **no** `MemoryMin`,
`MemoryHigh`, `MemoryMax`, `CPUWeight`, or `IOWeight` applied — the daemon
runs completely unprotected from host memory pressure, and can itself be
picked as an OOM victim under it.

This is reported, never silently accepted:
- `ctl host`'s `daemon_slice` block (`RG55-INTERFACE-CONTRACT.md` §8.5)
  reports exactly what is on disk — `memory_min_bytes`/`memory_high_bytes`
  read back as the cgroup v2 defaults (`0` / unset) instead of this unit's
  authored values.
- `doctor` (this package's own, and run-gate's consumer-side `doctor`, P5)
  read those numbers and say "cgprofile.slice has no unit: no memory
  floor" rather than inferring installation state from anything this
  container cannot itself see (there is no bind-mount of
  `/etc/systemd/system`, so "is the unit file present" is never answerable
  from inside the daemon — only "what do the resulting cgroup files say").

The daemon still starts and serves every verb normally either way —
this unit is a resilience improvement, never a hard prerequisite.

## Distinct from mdt's `dev-gates.slice`

`dev-gates.slice` (mdt host-setup, P8) is a DIFFERENT slice with a
different owner and a different install path (mdt's own `install.sh`) —
it contains the LANES the daemon profiles and placement's `rg-<token>`
leaves (C8), not the daemon itself. `ctl host`'s `gates_slice` block reads
that slice (name configurable via `serve --gates-slice`, default
`dev-gates.slice`) — this package only READS it, never installs or writes
its unit. `ctl host` reports `gates_slice.present: true` only when systemd
verifies the expected loaded, non-transient unit and its cgroup has finite
positive memory and CPU ceilings; a directory by itself reports absent.
Placement applies the same check and refuses an unverified capacity object
without failing profiling. See mdt host-setup's own `README.md`/`AGENTS.md`
for that side.

## Host system bus

The daemon's private PID namespace cannot write a host-visible PID directly to
`cgroup.procs`. The compose stack therefore bind-mounts the host system bus
socket read-only and uses only systemd's `AttachProcessesToUnit` method for
that one placement bridge. Before `ciu up`, verify the host prerequisite:

```sh
test -S /run/dbus/system_bus_socket
```

The bind declares `create_host_path: false`; a missing socket refuses the
daemon deployment instead of silently turning into an empty directory. The
daemon remains in private PID, cgroup, and network namespaces.
