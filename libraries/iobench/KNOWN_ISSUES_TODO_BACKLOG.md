# iobench known issues and TODO backlog

Entries IOB-BL-01..38 are retained in [BACKLOG.md](BACKLOG.md). New entries continue here.

## IOB-BL-39 — 2026-11-08 fill-rate figures are invalid (FIXED, IB-FILL)

The fill MB/s values in the 2026-11-08 r1002 and controller reports (`ib-vq-REPORT.md`, `ib-vq-rerun-REPORT.md`, `ib-vq-controller-REPORT.md`) are not valid device or filesystem measurements. In 23 of 23 runs, `fill_bps / R_est` was `10/41 = 0.244`: `devio.py` always sent `--time_based`, so fio repeated the `io_size`-bounded fill until `--runtime` instead of completing the planned chunk once. The same argv mistake affected every other `io_size`-bounded write job and `rearm-prep`; dependent PLA-09 comparisons from those runs are invalid too.

The builder now emits `--time_based` only when `io_size` is absent. FakeFio repeats an `io_size` job when the actual argv contains `--time_based`, and the regression replays the `10/41` signature before proving a corrected 10 MiB chunk writes 10 MiB in about 10 seconds at 1 MiB/s. README, DESIGN-GUIDE, CONSUMERS and SPEC explain the behavior and mark the historical rates invalid.

**Close-when:** the regression reproduces `10/41` from legacy argv and rejects that signature from the generated bounded-fill argv; reported bytes match `io_size` within tolerance and elapsed time is about `io_size / rate`; the project R0–R1 gate passes on the clean final head; the current user docs label the 2026-11-08 figures invalid.
