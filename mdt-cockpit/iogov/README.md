# iogov moved: see `iogovmon/`

The IO governor recorder that lived here is now its own project, **[`/iogovmon`](../../iogovmon/README.md)** (distribution `iogovmon`, console scripts
`iogovmond` and `iogovmonctl`, units `iogovmond-*` plus `iogovmond.service` in `iogovmon.slice`, own cmru release `iogovmon-v*`, independent of
mdt-cockpit; host-tuning program WP-IGM-a, 2026-11-05).
Nothing in mdt-cockpit imports it. This directory holds only this pointer; the design note is
[`iogovmon/docs/DESIGN-GUIDE.md`](../../iogovmon/docs/DESIGN-GUIDE.md), the former `iogov` backlog items MDT-028..MDT-031 are IGM-04..IGM-07 in
[`iogovmon/BACKLOG.md`](../../iogovmon/BACKLOG.md), and the background documents (`IO-GOVERNOR-DESIGN.md`, `IO-GOVERNOR-CANDIDATES.md`,
`IO-GOVERNOR-SECOND-OPINION-2026-10-27.md`) stay in [`../docs/`](../docs/).
