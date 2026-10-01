# Vendored Linux io.cost coefficient generator

`iocost_coef_gen.py` is a verbatim copy of the Linux kernel tool fetched on
2026-09-09 from `https://raw.githubusercontent.com/torvalds/linux/master/tools/cgroup/iocost_coef_gen.py`
(sha256 `7be1ffde0780b867271ca700abd3a80a5a1c965421dcb4f4e54661f8352f80ee`
at fetch time). Copyright (C) 2019 Tejun Heo, Andy Newell, Facebook; GPL-2.0.
Re-verify the source hash against upstream before replacing this copy.

The shared runtime artifact is `../../tools/iocost_coef_gen.py`. It is
generated from this pristine source and the ordered patch series below by
`../../tools/build-iocost-generator.py`. Both MDT host setup and
`iocost-calibrate.sh` consume that same generated file. Do not edit the
generated artifact directly.

## Carried patches

1. `0001-testdev-resolve-partition-to-parent-for-sysfs.patch` resolves the
   whole-disk scheduler/`nomerges` sysfs path for a raw `--testdev` partition,
   while fio still targets the named partition. Raw mode remains destructive
   to its target; use only a dedicated partition.
2. `0002-shared-lvm-file-target-support.patch` handles the host's LVM/dm
   scheduler path, supports an explicit `--testfile` for a mounted filesystem,
   tolerates unsupported no-COW flags, and uses argument arrays for file paths
   so special characters cannot become shell commands. File-target mode
   rewrites the chosen disposable scratch file; it refuses mismatched existing
   paths instead of unlinking or replacing them.

The generator header records the source and patch-series hashes. Run
`python3 ../../tools/build-iocost-generator.py --check` to verify that the
committed shared artifact matches this source and patch queue. Rebuild it with
`python3 ../../tools/build-iocost-generator.py` after changing either input.

Runtime dependencies are `findmnt`, `pv`, `dd`, and `fio`; `pv` is required
even for raw-device mode because the generator checks all commands at startup.
The v2 installer does not yet invoke calibration automatically: integrating
testfile mode with partition planning, mounting, verification, and rollback
remains a separate safety review. See `../../IO-BENCHMARK-DESIGN.md`.
