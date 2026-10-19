# tester-base: known issues and backlog

## TB-01 - unpinned Debian `docker-compose` on top of tester-base: CLOSED 2026-10-19 (INT-01 D-006)

Closed by tester-unified installing Docker's own `docker-compose-plugin` at an exact version with a pinned
sha256 (never Debian's `docker-compose`); a test refuses an unpinned or Debian compose install. See
tester-unified/docs/DESIGN-GUIDE.md "Userland added on top of the base".

## TB-02 - the release graph has no tester-base gate-prerequisite edge (INT-01 A-12, 2026-10-19)

`cmru.orchestration.toml` lists `tester-base <- none` and nothing depends on it, although every gate image is
`FROM tester-base`. Harmless while the default is the local tag. When the defaults switch to the published
digest (tester-base/docs/CONSUMERS.md "After the first release"), the release order must put tester-base
before run-gate-project and cmru; add the edge with that switch.

## TB-03 - a dedicated cgroup-probe image would let tester-unified drop systemd (INT-01 D-004, 2026-10-19)

tester-unified keeps the real `systemctl` client (Debian's `systemd` package) because the cgroup-profiler
placement probe (`scripts/cgroup-profiler/tools/gate.sh`, `lib/access.py` `_PLACEMENT_PROBE`) runs
`systemctl show <slice>` inside the tester image against the host system bus. Decision: keep it for now. The
alternative is a small dedicated probe image carrying only the systemctl/busctl client, used by the
cgroup probes (gate.sh, access.py, shared-ramdisk cgroup-parent.sh), after which tester-unified could drop
`systemd` (and the `systemd-tmpfiles` it puts on PATH). Needs a decision on who builds and pins that image.

