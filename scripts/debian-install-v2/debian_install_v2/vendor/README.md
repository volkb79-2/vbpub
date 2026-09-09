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
script yet — see the design note for why (the safe integration needs to
reuse the installer's existing partition plan/apply/verify/rollback
machinery rather than writing new raw sfdisk calls, and a real constraint
in this script's own `--testdev` handling needs a decision first: it
resolves the elevator/nomerges sysfs paths as `/sys/block/<basename of
--testdev>/queue/...`, which is only correct for a *whole disk* device
(e.g. `/dev/vda`) — pointed at a *partition* (e.g. `/dev/vda5`), that path
doesn't exist and the script fails outright. `--testdev` against a whole
disk is explicitly documented as destroying everything on it, which rules
out pointing it at the live root disk directly).

Runtime dependencies this script itself requires regardless of which mode
is used (its own startup check, unconditional): `findmnt`, `pv`, `dd`,
`fio`. `pv` is easy to miss — it's only used by the testfile-mode path
internally, but the presence check runs either way.
