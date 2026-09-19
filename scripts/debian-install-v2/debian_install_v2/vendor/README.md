# Vendored: `iocost_coef_gen.py`

Verbatim copy of the Linux kernel's own iocost calibration tool, fetched
2026-09-09 from `https://raw.githubusercontent.com/torvalds/linux/master/
tools/cgroup/iocost_coef_gen.py` (sha256
`7be1ffde0780b867271ca700abd3a80a5a1c965421dcb4f4e54661f8352f80ee` at fetch
time — re-verify against a fresh fetch before assuming this is still
current). Copyright (C) 2019 Tejun Heo, Andy Newell, Facebook; licensed
under the Linux kernel's GPL-2.0 (no per-file SPDX header, inherits the
kernel tree's default license as a `tools/` script).

Not modified from upstream — do not hand-edit this file. If a fix is
needed, get it from upstream and re-vendor, or carry the divergence as an
explicit, documented patch in the code that *invokes* this script rather
than editing the vendored copy in place.

Vendored (not installed via apt: it isn't packaged standalone by Debian —
it lives only in the kernel source tree) because it's the tool `resctl-
demo`/real io.cost deployments actually use to calibrate `io.cost.model`'s
`rbps`/`rseqiops`/`rrandiops`/`wbps`/`wseqiops`/`wrandiops` fields — see
`../../TODO.md` or the io-benchmark design note (once filed) for why this
was chosen over writing a bespoke fio job.

**Not yet wired into the installer.** `debian_install_v2/config.py` already
has the `run_io_benchmark`/`io_benchmark_duration_s`/`io_benchmark_max_size_gb`
config fields staged for this, but nothing in `installer.py` invokes this
script yet — see `../../IO-BENCHMARK-DESIGN.md` for why (the safe
integration needs to reuse the installer's existing partition plan/apply/
verify/rollback machinery rather than writing new raw sfdisk calls).

## Local patch: `0001-testdev-resolve-partition-to-parent-for-sysfs.patch`

`--testdev` resolves the elevator/`nomerges` sysfs paths as
`/sys/block/<basename of --testdev>/queue/...`, which is only correct for
a *whole disk* device (e.g. `/dev/vda`) — pointed at a *partition* (e.g.
`/dev/vda5`), that sysfs path doesn't exist (only `/sys/block/vda/vda5`
does) and the script fails outright. `--testdev` against a *whole disk* is
separately, correctly documented as destroying everything on it, which is
exactly why a partition target matters here — carving a throwaway
partition from swap's own free space keeps this from ever touching live
data.

The fix is a 9-line patch, not a design change: reuse the same
partition→whole-device resolution `dir_to_dev()` already does for its
*other* mode (`glob.glob('/sys/block/*/' + devname)`), applied to
`--testdev`'s `devname` too, but only for the two sysfs paths — `devno`
and `testfile` (what fio/the elevator-restore logic actually read from)
stay exactly the partition given on the command line. Elevator/nomerges
genuinely are whole-queue properties shared across every partition of a
disk, so widening only those two lookups is correct, not a workaround.

**How to use it**: apply on top of the pristine `iocost_coef_gen.py`
above at whatever point the script is actually deployed/invoked — never
hand-edit the vendored copy itself. `patch -p1 < 0001-*.patch` (or
`git apply`) from `scripts/debian-install-v2/`. **To check for upstream
drift** before ever re-vendoring or trusting this again: `patch --dry-run
-p1 < debian_install_v2/vendor/0001-testdev-resolve-partition-to-parent-for-sysfs.patch`
from `scripts/debian-install-v2/` — a clean dry-run means the patch still
applies as understood; a failure means upstream changed this exact region
and the patch needs a fresh look before use, not a forced/fuzzy apply.

Runtime dependencies this script itself requires regardless of which mode
is used (its own startup check, unconditional): `findmnt`, `pv`, `dd`,
`fio`. `pv` is easy to miss — it's only used by the testfile-mode path
internally, but the presence check runs either way.
