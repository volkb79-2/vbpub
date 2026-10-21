# iobench CONSUMERS (how to adopt it)

Every block below is pasteable; the docs-sync test (`IOB-DOC-02`) runs each `bash` block's `iobench` invocation through the CLI's `--dry-run` parser and
parses each `json` block with the shipped loader. WHAT is in the [README](../README.md); WHY in the [DESIGN-GUIDE](DESIGN-GUIDE.md); the contract in the [SPEC](SPEC.md).

## 0. Install

In this repo (zero dependencies besides `fio` and Python 3):

```bash
install -d /usr/local/lib/iobench && cp -r libraries/iobench/src/iobench /usr/local/lib/iobench/
PYTHONPATH=/usr/local/lib/iobench python3 -m iobench --version
```

Out of repo: the cmru-released wheel or `iobench.pyz` (with its `.sha256`), never PyPI. Needs `fio` >= 3.30 (JSON output with `clat_ns` percentiles).

## 1. Prepare the scratch target (once per host)

iobench writes to a **dedicated** block device or partition and refuses anything else. The prepared target carries an iobench marker in its first MiB.

```bash
lsblk -b -o NAME,SIZE,PARTUUID,FSTYPE,MOUNTPOINTS /dev/vda
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 7b2d0c1e-07
iobench target show /dev/vda7 --json
```

`target init` refuses a mounted, swap-active, signature-bearing (`blkid -p`) or root-disk target unless **both** `--i-own-this-device` and the matching
`--expect-size` / `--expect-partuuid` (or `--expect-serial` when the device reports one) are given. Without a marker no `calibrate`, `matrix` or probe command
will open the device.

## 2. Calibrate (once per host or storage class)

```bash
iobench calibrate --target /dev/vda7 --mount-dir /var/lib/iobench/work --profile standard --confirm-quiet-window
```

* Takes 30 min (`standard`), 12 min (`quick`), 75 min (`thorough`). It is a hard wall-clock budget; skipped stages are recorded, never shortened silently.
* On a live host it refuses with production containers running unless `--confirm-quiet-window` (it saturates the disk).
* Output: `/var/lib/iobench/calibration.json`. Re-run on an identity change, or when `iobench check` reports `recalibrate_recommended`.

## 3. Matrix iteration (the fast path)

```bash
iobench matrix --target /dev/vda7 --mount-dir /var/lib/iobench/work --confirm-quiet-window
iobench matrix --target /dev/vda7 --mount-dir /var/lib/iobench/work --knob max_sectors_kb --candidates 64,128,256 --repeat
```

The first form runs 6 cells (~10-15 min) and writes `/var/lib/iobench/result.json`; the second compares one knob (~3-4 min with a repeat). Every runtime
setting it changes is journalled in `/run/iobench/restore.json` and restored on exit, signal or crash (and by the next start). It never writes `io.max`.

## 4. Read the result (the wizard and `check.sh` use only this)

```bash
iobench check --json; echo "exit=$?"
iobench show --proposals --json
iobench show --proposals --section writeback --json
```

Exit codes of `check`: `0` current and plausible, `3` stale (identity mismatch), `4` no result, `5` corrupt or unsupported schema, `6` only an aborted run exists,
`7` current identity but not plausible. `check` also prints `warnings` (scheduler or RAM changed, `recalibrate_recommended`, result older than 180 days) that
never invalidate.

```json
{"state": "current", "reasons": [], "warnings": ["scheduler changed: bfq -> mq-deadline (sweep was run under bfq)"], "result": "/var/lib/iobench/result.json", "calibration_id": "cal-20261021T031500Z-7b2d0c1e"}
```

The `proposals` object (schema 1):

```json
{
  "writeback": {
    "dirty_bytes": {"value": 16777216, "rule": "R1", "inputs": {"W_s": 2.0, "R_drain_bps": 10500000, "inflight_bytes": 1048576}, "confidence": "measured+confirmed"},
    "dirty_background_bytes": {"value": 4194304, "rule": "R1b", "inputs": {"dirty_bytes": 16777216}, "confidence": "ratio-inferred"},
    "nr_requests": {"value": 16, "rule": "R2", "inputs": {"qd90": 8}, "confidence": "measured+confirmed"},
    "max_sectors_kb": {"value": 64, "rule": "R3", "inputs": {"R_drain_bps": 10500000, "read_ratio": 0.21, "F": 0.25}, "confidence": "measured", "flags": ["read_floor_violated"]}
  },
  "caps": {"dev_aggregate": {"RIOPS": 6500, "WIOPS": 5900, "RBPS": 1000000000, "WBPS": 9800000}, "rule": "R5"},
  "iocost_model": {"rbps": 290000000, "rseqiops": 4900, "rrandiops": 4900, "wbps": 6291456, "wseqiops": 1536, "wrandiops": 1536, "rule": "R6"},
  "iocost_qos": {"rpct": 95, "rlat": 5000, "wpct": 90, "wlat": 25000, "min": 100, "max": 300, "rule": "R6"}
}
```

A value is a number or the word `kernel` ("leave the kernel's own value; the device is fast enough"). A proposal with `flags` or `confidence` other than
`measured+confirmed` is shown with that label, never silently defaulted. `iobench` never persists anything; host-setup renders and verifies the persistent files.

## 5. mdt-cockpit wizard (MDT-HS-05 and the io.cost wizard, D-IOC-4)

* The wizard **asks whether to run the benchmark, default yes (a configurable key)**; it never starts one silently. It calls `iobench calibrate` if `iobench check --calibration` says no
  current calibration exists, then `iobench matrix`.
* Proposal precedence for each key: **benchmark (only when `check` exits 0) > the configured value > `kernel`**.
* Latency source (D-IOC-3): the governor's histogram when one is running, otherwise `proposals.iocost_qos` (S7).
* The io.cost model and bounds come from `proposals.iocost_model` and `proposals.iocost_qos`; the worst/best plateaus come from `ceilings.jsonl` (below).
* `check.sh` uses `iobench check --json` and reports OK / MISMATCH / "not managed" / "could not read" distinctly.

## 6. Ceiling history (governor or timer probe)

```bash
iobench ceilings add --dir write --bps 14680064 --duration-s 600 --source governor
iobench ceilings summary --window-days 30 --json
```

```json
{"write": {"worst": 6291456, "median": 14680064, "best": 18874368, "n": 41}, "read": {"worst": 290000000, "median": 1300000000, "best": 1400000000, "n": 12}, "window_days": 30, "history": "history"}
```

`worst` and `best` feed `min = 100` and `max = ceil(110 * best / worst)`. With no history the summary says `"history": "single-run"` and uses the run's own windows.

## 7. debian-install-v2 (installer profile)

The installer owns the throwaway tail partition lifecycle (create, mkfs, restore) exactly as today; it passes the partition to iobench:

```bash
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 7b2d0c1e-07
iobench calibrate --target /dev/vda7 --mount-dir /mnt/iobench --profile quick --installer
```

`--installer` skips the live-host refusals (pre-production) and allows the fresh-block rung (`blkdiscard`). The installer persists nothing from iobench; it
records `result.json` and reads `proposals`.

## 8. Dry run and planning

```bash
iobench calibrate --target /dev/vda7 --mount-dir /var/lib/iobench/work --profile standard --dry-run
```

prints the stage plan, time budget and the exact fio argv per stage, and writes nothing.

## 9. What a refusal looks like

`error: refused: /dev/vda4 is swap-active (swapon); no iobench marker. iobench target init needs --i-own-this-device with --expect-size and --expect-partuuid.` (exit 2).
`error: refused: production containers are running (3); rerun with --confirm-quiet-window in an agreed window.` (exit 2).
