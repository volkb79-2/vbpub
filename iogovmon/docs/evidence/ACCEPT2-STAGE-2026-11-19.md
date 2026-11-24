# iogovmon acceptance-2: fill stage on r1002 (2026-11-18/19)

**Verdict: usable, with two recorded limits.** The stage ran cleanly. A0 gave a baseline on all six lanes. Every safety rail held: pressure skips, COW headroom LIMIT-REACHED at B4, and a cleanup ending `ok=True`. The data supports two findings. First, a full disk slows the bench lanes by about 20-30 % at the median. Second, the recognizer did not raise one change event during the stage, because it reads window medians only (IGM-20). The `r_cold` probe population also changes with fill (IGM-19), so its latency series is not comparable across fill levels. Nothing is discarded. Use the `r_cold` medians across phases only together with the yield table below.

## Run

- Host r1002: a thin 512 GiB virtio disk `vda`; `/` is ext4 (vda3) and `/srv/cow` is btrfs (vda4). There is no protected workload.
- iogovmon 0.1.0, recommend-only, config `.cond-v2`: `window_hours=1`, `bench_interval_h=1`, `read_max_attempts=128`, `bench_pressure_some_max=3.0`, budgets 256 MiB read / 2 GiB write per day.
- T0 2026-11-18T01:19Z. Units `iogov-a2-stage` and `iogov-a2-refprobe`.
- Phases: A-pre, then A0 (6 h baseline), then B1 (fill `/srv/cow` to 50 %), B2 (`/srv/cow` to 80 % and `/` to 60 %), B3 (two bursts of small-file churn plus 4 KiB random overwrites), B4 (fill to the COW headroom limit), B5a (delete, `/srv/cow` remounted `nodiscard`, cron `fstrim` disabled) and B5b (discard restored, `fstrim -v` on both filesystems). Each B phase is followed by a quiet tail plus at least two quiet windows.
- Stage cleanup 2026-11-19T04:08Z, `ok=True`. The `fstrim.timer` and the cron line were restored, the cron file is identical to its sha256, and the discard mount option was restored.
- The iogovmond tick/bench timers ran on, recommend-only, until 2026-11-24T05:40Z. They were then stopped and disabled (see "Timers").

## Evidence

The raw set lives outside the repository in the controller evidence store, `~/controller/evidence/iogovmon-accept2-stage/` (gitignored host path; the binaries are not committed):

| file | sha256 |
|---|---|
| `accept2-files.tgz`: `/root/iogovmon-accept2` (stage logs, `stage-summary.txt`, `snap/` 25 phase snapshots, `calib*.jsonl`, refprobe CSVs, stage/refprobe scripts and configs), `/var/lib/iogovmon`, `/etc/iogovmon` | `7d5dd36d3ae245f584a86e380ff4eb752e92d9605253fcbe32456f5383556286` |
| `systemd-journal-iogov-units-20261117-20261124.jsonl.gz`: `journalctl -o json` for the iogovmond-tick/bench, iogov-a2-stage and iogov-a2-refprobe units (10,929 records) | `d13d473f22bf82bc0790056dd01406c387aa8d584fb6fa40c299e7f205f9ac07` |
| `iogovmon-state-final.tgz`: `/var/lib/iogovmon` and `/etc/iogovmon` after the timer stop | see `SHA256SUMS` beside it |

`stats.py` (in the same directory) computes every table below from the iogovmon journal (`journal-2026-11.jsonl`: 8,533 records, 1,607 ticks, 130 benches), `stage-phases.json` and the refprobe CSVs. Latency percentiles pool the per-sample values that the records carry: all `r_cold`/`r_witness` reads, and the `lat_us` sample of each bench lane. Throughput percentiles take one value per valid bench run, so with 1-2 runs per phase, p95 and p99 just restate the maximum. The post-stage row (2026-11-19T04:08Z..11-24) holds the recommend-only ticks after the stage.

## Results

### Bench lanes vs fill (p50 / p95 / p99)

| phase | r_seq MiB/s | w_fresh_qd1 MiB/s | w_fresh_qd4 MiB/s | w_over_4k us |
|---|---|---|---|---|
| A0-baseline | 236 / 240 / 240 | 74 / 77 / 77 | 110 / 114 / 114 | 840 / 1100 / 1264 |
| B1-quiet (cow 50 %) | 237 / 246 / 246 | 80 / 82 / 82 | 119 / 123 / 123 | 704 / 989 / 1139 |
| B2-quiet (cow 80 %, / 60 %) | 188 / 188 / 188 | 64 / 66 / 66 | 87 / 88 / 88 | 949 / 1544 / 1658 |
| B3-c1-quiet (after churn) | 178 | 58 | 94 | 968 / 1352 / 1756 |
| B3-c2-quiet (after churn) | 178 / 181 / 181 | 61 / 62 / 62 | 76 / 86 / 87 | 1004 / 1655 / 1825 |
| B4-quiet (headroom limit) | 211 / 225 / 227 | 86 / 88 / 88 | 100 / 101 / 101 | 862 / 1143 / 1219 |
| B5a-quiet (deleted, nodiscard) | 204 / 243 / 246 | 60 / 62 / 63 | 88 / 95 / 95 | 844 / 1082 / 1208 |
| B5b-quiet (after fstrim) | 218 / 233 / 235 | 73 / 74 / 74 | 82 / 91 / 92 | 743 / 1142 / 1368 |
| post-stage (5 days) | 224 / 253 / 261 | 74 / 85 / 93 | 98 / 120 / 128 | 727 / 1254 / 4442 |

- The degradation lasts from B2 to B3: `r_seq` falls by 25 %, `w_fresh_qd4` by 20-30 %, and `w_over_4k` rises by 15-20 % at the median.
- Recovery after B4 is only partial. B4, which reached its limit on headroom, gives back some of the loss.
- Delete plus fstrim brings `w_over_4k` back to about the baseline. `w_fresh_qd4` stays 10-25 % low.

### Reference probe on `/srv/cow` (refprobe, us, p50 / p95 / p99)

| phase | read-hot | read-big (24 GiB random) | read-fill | wr_sync |
|---|---|---|---|---|
| A0-baseline | 180 / 509 / 614 | 233 / 598 / 755 | - | 4205 / 5739 / 7837 |
| B1-quiet | 184 / 516 / 601 | 243 / 608 / 752 | 315 / 673 / 797 | 4594 / 6026 / 8796 |
| B2-quiet | 208 / 548 / 668 | 272 / 658 / 873 | 360 / 712 / 875 | 4386 / 6661 / 9105 |
| B3-c1-quiet | 207 / 540 / 641 | 273 / 665 / 919 | **670 / 75990 / 80438** | 2164 / 3374 / 4473 |
| B3-c2-quiet | 199 / 534 / 647 | 260 / 620 / 794 | 633 / 1702 / **79204** | 2187 / 3410 / 5127 |
| B4-quiet | 187 / 525 / 633 | 244 / 609 / 802 | 618 / 3285 / **79176** | 2068 / 2869 / 4022 |
| B5b-fstrim (30 s) | 24486 / 76171 / 81322 | 9498 / 74764 / 79355 | - | 79139 / 390812 / 418516 |
| B5b-quiet | 187 / 525 / 617 | 241 / 624 / 808 | - | 2703 / 4294 / 5437 |
| post-stage | 190 / 516 / 632 | 242 / 600 / 768 | - | 4074 / 5913 / 8421 |

- **Tail-only shift.** After the B3 churn, reads of the churned fill data develop an ~80 ms tail (p95/p99) while the median moves only about 2x. This is the case the median-only recognizer cannot see (IGM-20).
- **Trim hurts its neighbours.** During `fstrim` (117 GiB on `/` in 14 s, 213 GiB on `/srv/cow` in 17 s), every co-tenant read stalls to 25-80 ms and synchronous writes stall to 0.4 s. Whatever trims in bulk is therefore an IO event in its own right. It must not fall into a measurement window, and it must not overlap with a latency-sensitive workload.
- `wr_sync` is faster after B3 (4.2 ms down to 2.1 ms) and stays faster until B5b, then returns to about 4 ms after the stage. The cause is not established here. The refprobe file itself was rewritten during the stage, so this is not a disk capability change, and it is not used as one.

### `r_cold` latency and the probe-population confound

`r_cold` reads random blocks of `vda` and keeps only non-zero ones. On a thin disk the non-zero population is whatever data exists, so the fill changes what the lane measures:

| phase | r_cold p50 / p95 / p99 us | non-zero yield (n / attempts) |
|---|---|---|
| A0-baseline | 558 / 723 / 929 | 10.4 % (800 / 7718) |
| B1-quiet | 576 / 780 / 966 | 30.7 % |
| B2-quiet | 632 / 840 / 990 | 57.5 % |
| B3-c2-quiet | 600 / 726 / 872 | 63.6 % |
| B4-quiet | 602 / 755 / 912 | 73.9 % |
| B5a-quiet | 590 / 772 / 881 | 75.0 % |
| B5b-quiet (after fstrim) | 567 / 757 / 950 | 10.3 % |
| post-stage | 560 / 736 / 1222 | 10.4 % (15334 / 148030) |

- At baseline, nine reads in ten land on unallocated (zero) blocks. With the fill, three in four land on freshly written fill data.
- After the delete, the blocks stay non-zero until `fstrim` discards them (B5a: 75 %). Only then does the population return to the original data (B5b: 10.3 %).
- The `r_cold` median moves by at most +13 %, but it measures a different population at each fill level. A capability change and a population change cannot be told apart from it.
- At the default `read_max_attempts=36` and `read_samples_per_tick=12`, a 1 h window at a 5-10 % yield gets 22-43 reads against `min_samples["r_cold"]=30`. So the windows go `insufficient` on a thinly used disk. That is why `.cond-v2` uses 128 attempts (IGM-19).

### Recognizer

- During the stage: `baseline_established` on all six lanes and no other event. None of the shifts above (-25 % `r_seq`, -30 % `w_fresh_qd4`, +20 % `w_over_4k`, the 80 ms tail on fill reads) produced a `shift_detected`. Each one is either a median drift below the CUSUM allowance (`cusum_k = 0.5` log2, which ignores drift under ~1.4x) or sits in the tail only (IGM-20).
- After the stage, there was one real event: `shift_detected` on `w_over_4k` (up about 1.8x) at 2026-11-23T11:00Z, cleared at 15:00Z ("recovered"). Its cause is not attributed here. r1002 was shared with other topics' work from 11-23 on.

## Discard facts on r1002 and the debian-install-v2 default

- `/` (ext4) is mounted without `discard`. Trim comes from two places: the daily cron `33 10 * * * root /sbin/fstrim -a` in `/etc/cron.d/kernel`, and the weekly `fstrim.timer`. Both are active, so a trim can run twice in a week.
- `/srv/cow` (btrfs) is mounted `discard=async`. fstab carries no discard option, so this is the btrfs default on a device that advertises discard (`discard_max_bytes` 2 GiB).
- Consequence for the debian-install-v2 default: keep continuous `discard=async` on btrfs, and do not add a daily bulk `fstrim -a` on top of the weekly timer. A bulk trim stalls co-tenant IO for tens of seconds (above). On ext4 data volumes, one weekly `fstrim.timer` run is the single trim source. This is a recommendation to the debian-install-v2 owner, not a change here.

## Timers and IGM-01

- The post-stage journal runs from 2026-11-19T04:08Z to 11-24T05:40Z: more than 5 days, 1,320 ticks and 107 valid benches, recommend-only.
- It is kept as IGM-01's "48 h of ticks with the journal" evidence (the `iogovmon-state-final.tgz` above). The byte/PSI budgets in that window are verified when IGM-01 is closed. This report does not close IGM-01.
- On 2026-11-24T05:40Z, `iogovmond-tick.timer` and `iogovmond-bench.timer` were stopped and disabled. This frees r1002 for other leases without background IO.
- Kept on the host: the install, `/etc/iogovmon` and `/var/lib/iogovmon`.
- Removed with `refprobe.py --cleanup` after the copy: the refprobe probe files (`/srv/cow/iogov-refprobe` 25 GiB and `/var/tmp/iogov-refprobe` 1 GiB) and the refprobe CSVs.
- Verified clean before the stop: `fstrim.timer` active, the cron line restored, the discard option restored, no synth or fill directories, and no stage/refprobe process running.

## Re-run notes

- **Lease.** The stage needs r1002 (or another host with a separate `/srv/cow` mount) for about 28 h of exclusive IO quiet.
- **Tooling.** The tooling is `stage-v3.1.py`, `refprobe.py`, `synth_shift_check.py` and `test_stage.py`; P-hostobs-21 imports it into `iogovmon/tools/accept/`. The stage is resumable from `stage-state.json`.
- **Stopping.** STOP files: `/root/iogovmon-accept2/STOP` for the stage and `REFPROBE-STOP` for refprobe.
- **Cleanup.** `stage --cleanup`, then `refprobe.py --cleanup` once its lock is free.
- **Before re-running:**
  - set `read_max_attempts` from the expected yield (IGM-19);
  - add a tail statistic, or accept the documented limit (IGM-20);
  - keep bulk trims out of measurement windows;
  - read the systemd journal of a long run in `--since`/`--until` windows under `nice`/`ionice`. One unwindowed `journalctl -o json` over a 470 MB journal raised memory PSI full to 3.7 for a few seconds during this copy.
