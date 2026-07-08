# Modern Debian Tools + Python Debug Release Summary — 2026-07-08

Summary of the `20260708-2` release batch — the first release using the parallel
repack flow (`REPACK_JOBS=3`) at zstd compression level 9 (previous releases:
sequential, level 14).

## Where the findings live

- Run log (failed first attempt): `modern-debian-tools-python-debug/logs/release-20260708.log`
- Run log (successful retry): `modern-debian-tools-python-debug/logs/release-20260708-retry.log`
- Resource monitor log (30 s sampling across both attempts): `modern-debian-tools-python-debug/logs/release-monitor-20260708.log`
- Package doc indexes:
  - `modern-debian-tools-python-debug/package-manifests-versioned/README.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md`
- Versioned package pages (this batch):
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.11-20260708-2.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.14-20260708-2.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.14-php8.5-20260708-2.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-php8.5-20260708-2.md`

## What changed

- `scripts/release-repack.sh` now runs `REPACK_JOBS=3` parallel workers and
  passes `--compression-level 9` to docker-repack (was: sequential, level 14).
  This release validates both changes — see the Performance section.
- Host prerequisites were installed for the first time on gstammtisch itself
  (previous releases ran the repack inside the mdt devcontainer):
  `skopeo 1.18.0` via apt, `docker-repack 0.5.0` to `/usr/local/bin`.
- Operational fix discovered during this release: the host `/tmp` is a 7.9 G
  tmpfs. The repack flow must run with `TMPDIR` pointing at disk-backed
  scratch (used: `modern-debian-tools-python-debug/build/repack-tmp`), both
  because 3 parallel OCI layouts exceed 7.9 G and because tmpfs fill consumes
  RAM on a 16 G host that also runs the game server.

## Release batch

Build date / release date: `20260708-2`

The first attempt (batch id `20260708`) built all four images successfully but
failed in the repack stage with ENOSPC on the tmpfs `/tmp` (see Performance).
The retry re-resolved to `20260708-2` and completed; only `20260708-2` was
pushed. The `20260708` images exist only in the local daemon.

Targets covered:

- `trixie-py311-vsc`
- `trixie-py314-vsc`
- `trixie-py314-php85`
- `trixie-py314-php85-vsc`

## Confirmed pushes

9 tags pushed (per-target tag counts from the run log: 2 + 3 + 2 + 2), alias
digests verified against the versioned tags via `skopeo inspect --raw`:

- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.11-20260708-2`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.11-latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-20260708-2`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-php8.5-20260708-2`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-php8.5-latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-20260708-2`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest`

GHCR package visibility synced to `public` for both packages after the push.

## Repack results

`REPACK_TARGET_SIZE=2GB`, `REPACK_COMPRESSION_LEVEL=9`, `REPACK_JOBS=3`.
Sizes below are from the pushed manifests (`skopeo inspect --raw`, sum of
layer sizes); the loaded size is the uncompressed `docker images` size.

| Target | Loaded (docker) | Pushed (compressed) | Layers | Layer sizes |
| --- | ---: | ---: | ---: | --- |
| `trixie-py311-vsc` | 6.94 GB | 2.120 GB (1.97 GiB) | 3 | 55.6 MiB / 289.3 MiB / 1.64 GiB |
| `trixie-py314-vsc` | 6.96 GB | 2.123 GB (1.98 GiB) | 3 | 55.4 MiB / 293.0 MiB / 1.64 GiB |
| `trixie-py314-php85` | 6.61 GB | 2.059 GB (1.92 GiB) | 3 | 49.2 MiB / 228.8 MiB / 1.65 GiB |
| `trixie-py314-php85-vsc` | 7.05 GB | 2.160 GB (2.01 GiB) | 3 | 56.4 MiB / 324.9 MiB / 1.64 GiB |

Compression ratio ≈ 30.5–31.1 % of the loaded size (≈ 3.2–3.3×) on every
target.

## Performance — parallel repack, zstd level 9

### Runtimes

Attempt 1 (failed, batch `20260708`), all times UTC:

- Wall: `04:01:08` → `04:39:02` (37 m 54 s)
- Bake (`--load`): `04:02:59` → `04:34:13` (31 m 14 s) — partial warm cache
  from 2026-07-07; the new BUILD_DATE invalidates the manifest layers and each
  bake re-exports 4 × ~7 GB into the daemon.
- Repack: aborted after ~4 m 49 s — all 3 parallel workers hit
  `No space left on device` writing their source OCI layouts into the 7.9 G
  tmpfs `/tmp`.

Attempt 2 (successful, batch `20260708-2`, `TMPDIR` on disk):

- Wall: `04:40:42` → `05:29:50` (49 m 08 s)
- Resolve env: `04:40:42` → `04:42:18` (~1 m 36 s)
- Bake (`--load`): `04:42:18` → `05:09:00` (26 m 42 s) — again not fully
  cached because the date bump to `20260708-2` re-ran the manifest layers +
  export.
- Repack + push, all 4 targets: `05:09:00` → `05:29:48` (**20 m 48 s**)
- Push step: no-op (repack flow) + GHCR visibility sync (~2 s).

Per-target repack (worker logs are deleted on success; timestamps captured
mid-run):

- Wave 1 (`trixie-py311-vsc`, `trixie-py314-vsc`, `trixie-py314-php85`)
  started `05:09:00`; docker-repack `Completed` at `05:17:34` / `05:17:39` /
  `05:17:37` → **≈ 8 m 35 s per target** including the skopeo
  daemon-to-OCI export. Pushes followed (finish order: py314-vsc, php85,
  py311-vsc).
- `trixie-py314-php85-vsc` started when the first worker slot freed
  (~05:19–05:21) and finished, including pushes, by `05:29:48`
  (≤ ~11 min end-to-end).

### Comparison to 2026-07-07 (sequential, level 14)

From `cmru.release14-repack.log`:

| Metric | 2026-07-07 (seq, L14) | 2026-07-08 (3 jobs, L9) | Change |
| --- | ---: | ---: | --- |
| Repack+push, all 4 targets | ≈ 2 h 09 m (08:52:38 → 11:01:46) | 20 m 48 s | **≈ 6.2× faster** |
| docker-repack per target | ≈ 23–24 min | ≈ 8.6 min | ≈ 2.7× faster |
| Layer count per image | 3 | 3 | unchanged |

Compressed size cost of level 9 vs level 14 (L14 figures are the layer sums
reported by the 07-07 worker log, which rounds the large layer to 0.1 GiB, so
deltas are approximate):

| Target | L14 pushed | L9 pushed | Delta |
| --- | ---: | ---: | ---: |
| `trixie-py311-vsc` | ≈ 1.84 GiB | 1.97 GiB | ≈ +7.5 % |
| `trixie-py314-vsc` | ≈ 1.84 GiB | 1.98 GiB | ≈ +7.3 % |
| `trixie-py314-php85` | ≈ 1.82 GiB | 1.92 GiB | ≈ +5.6 % |
| `trixie-py314-php85-vsc` | ≈ 1.86 GiB | 2.01 GiB | ≈ +8.0 % |

Verdict on the tradeoff: ~6–8 % larger images for a ~6× faster repack stage —
the intended speed-over-size tradeoff is confirmed and looks right for this
release cadence.

### CPU / IO monitoring verdict

30 s sampling in `logs/release-monitor-20260708.log` (172 samples,
04:00:53–05:30:33 UTC), host: 8 vCPU / 15 GiB / vda behind dm-crypt.

- **Device utilization peak**: vda `%util` 75.6 % (05:15:26, parallel
  compression phase). The disk itself never saturated. Peak read
  821 MB/s-equivalent (rkB/s at 04:36:12), peak write 624 MB/s-equivalent
  (05:11:56) in 1 s iostat windows.
- **Buildkit scope vs caps**: peak interval rates 2 304 r-IOPS / 3 005 w-IOPS,
  104 MB/s read / 173 MB/s write — far below the configured `io.max`
  (72 138 r-IOPS / 46 956 w-IOPS / 1 639 MB/s read / 393 MB/s write). **Caps
  were not hit**; no buildkit throttling occurred.
- **Game (WSServer) pressure**: quiet through the bake. Two disturbance
  windows:
  1. `04:35–04:40` (attempt 1 tmpfs fill + ENOSPC): io.pressure full avg10 up
     to 19.11, memory.pressure full avg10 up to 29.66 — the tmpfs consumed RAM
     until the workers died. **During this exact window the Soulmask server
     container was recreated**: wings processed a panel-side `restart` power
     action at `04:35:35Z` (new container `359e67b42414` started `04:36:33Z`,
     58 s boot, healthy since; old scope `543fd54cb640` gone). Wings logged no
     crash-detection message, and no equivalent restart ran at the same time
     on 2026-07-07, so the initiator is unattributed (panel schedule, operator,
     or an automation reacting to the pressure) — but the coincidence with the
     tmpfs fill is noted. The monitor loop re-resolved the container each
     sample, so pressure data spans both container instances.
  2. `05:12–05:16` (attempt 2, 3 workers × 8 zstd threads hashing/compressing):
     **peak game io.pressure full avg10 = 32.25** and **memory.pressure full
     avg10 = 46.59** at 05:14:48, with host load1 = 67.92 on 8 CPUs. Pressure
     fell back below 1 by 05:15:58 — a severe but short (~2–3 min) stall
     burst, not sustained across the 21 min repack window.
- **As expected?** Partially. Bake phase: yes — game undisturbed, buildkit far
  under its caps. Parallel repack phase: **no** — the repack workers run in
  the invoking user session (user.slice), outside the io.max-capped
  buildkit/devcontainer scopes, and `ionice -c3` / `nice -n 19` did not
  prevent the memory-reclaim stall while 24 zstd threads were active on 8
  CPUs of a 15 GiB host.

Follow-ups suggested by the data (not applied in this release):

1. Set `REPACK_CONCURRENCY` (docker-repack `--concurrency`) to 2–3 so the
   three workers do not stack 24 compression threads.
2. Run the repack stage inside a capped cgroup (besteffort/dev scope) instead
   of the raw user session.
3. Make the disk-backed `TMPDIR` part of the release flow itself instead of
   operator environment (the host tmpfs `/tmp` cannot hold the parallel OCI
   layouts, and filling it converts disk pressure into memory pressure
   against the game).
