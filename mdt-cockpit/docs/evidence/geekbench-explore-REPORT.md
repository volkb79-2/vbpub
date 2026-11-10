<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/geekbench-explore-REPORT.md -->
<!-- relation: Geekbench exploration; verbatim source text follows -->

# geekbench-explore REPORT (can Geekbench run in ~1/10th of the time; real runs on r1002 and v1001)

Date 2026-11-04 (host clocks). No repo files other than this report and `reports/geekbench-fixtures/` were written. Nothing committed (new untracked files in the release-program directory, which is outside the vbpub repo). The HB-1 worktree was not touched.

## Verdict
- **The 1/10th goal is NOT achievable by legitimate means.** The free Geekbench 6.7.0 and 7.1.0 binaries document exactly three options (`--cpu`, `--sysinfo`, `--unlock EMAIL KEY`). There is no section, workload, iteration or delay option. A faster run needs either a Pro licence (the options a Pro build might add are not documented in the free binary; see "Not verifiable" below) or modifying the binary or its data files, which I did not attempt and which I am not recommending.
- **The runtime does not matter for a once-per-host measurement.** Recommendation below.

## Options (read from `--help` of the real binaries on r1002)
| Option | GB 6.7.0 | GB 7.1.0 | Effect |
|---|---|---|---|
| `-h, --help` | yes | yes | print usage |
| `--unlock EMAIL KEY` | yes | yes | unlock Pro with a licence (we have none) |
| `--cpu` | yes | yes | run the CPU benchmark (also the default with no option) |
| `--sysinfo` | yes | yes | print system information and exit (about 1 s) |
| section / workload / iteration / delay / no-upload / save | none | none | not offered |

Both `--help` outputs end with: "If no options are given, the default action is to run the CPU benchmark." The GB7 banner when running states: "Geekbench 7 requires an active internet connection and automatically uploads benchmark results to the Geekbench Browser. Upgrade to Geekbench 7 Pro to enable offline use and unlock other features". The tarballs contain only binaries and data files (GB6: `geekbench6`, `geekbench_x86_64`, `geekbench_avx2`, `geekbench.plar`, `geekbench-workload.plar`; GB7: `geekbench7`, the same two arch binaries, `geekbench.plxr`, `geekbench-workload.plxr`, `libtensorrt_rtx.so`). **There is no README, licence or EULA file in either tarball.**

Archive sha256: 6.7.0 `4a7b77d7bf517bfcf0c68a75a6fcd6177f535d78bc65406535607ebfbf98137f`; 7.1.0 `411b72a9c851b69e6f91d390649d419e2e625f475e3983880169651ca27a8672` (matches the HB-1 report).

## Not verifiable (be explicit)
- **Online docs and EULA could not be read.** `www.geekbench.com` and `browser.geekbench.com` return a Cloudflare "Just a moment" challenge (HTTP 403) to curl from the devcontainer and from both hosts, and the PWMCP browser returned an empty page or timed out. The Wayback copy returned 404. So **I have no EULA text to quote**, and I will not quote it from memory. The brief's modification and reverse-engineering quote is therefore missing: the operator (or a browser session) should read https://www.geekbench.com/legal/ . I did not look inside the binary or data files for licence strings, as that edges toward reverse engineering.
- Whether a Pro licence adds section or workload selection options is unverified (the free binary does not list any). I did not probe undocumented flags.

## Where the wall time goes (real timestamps, GB 7.1.0, stdout pipe, line-timestamped)
| Phase | r1002 | v1001 |
|---|---|---|
| start to first output (loading, sysinfo; output is block-buffered up to here) | 6.6 s | 6.6 s |
| Single-Core section (17 workloads, 7 to 22 s each) | 215 s | 246 s |
| Multi-Core section | 248 s | 245 s |
| upload (to `Upload succeeded`) | under 1 s | under 1 s |
| **total** | **470.5 s** | **496.9 s** |

- GB 6.7.0 on r1002: 363 and 372 s up to the upload attempt (Single ~190 s, Multi ~170 s). No inter-workload delay is visible in the stdout; per-workload line spacing includes any internal gap, and the free binary has no option to change it.
- Warm-up and upload are negligible. Essentially all time is the 17 + roughly 17 workloads. One whole section is about 4 minutes, so even "single-core only" (which does not exist as an option) would be about half, not a tenth. A tenth is about 47 s, less than three workloads.
- Note: only some Multi-Core workloads print a "Running" line in the GB7 output (8 of 17); the rest are not announced.

## Score relation to the published table
No subset mode exists, so the question is moot: only the full run produces a comparable score. Any self-made subset (running fewer workloads) would not be a Geekbench score and could not be compared with the published table or with `BASELINE_SCORE`.

## Real runs (GB 7.1.0, full free run with upload)
Measurement conditions: each host had two R2 campaign lane containers in `dev.slice/dev-gates.slice` (load average about 4 before) plus `ksmd`. `cpu.idle=1` was set on `dev.slice/dev-gates.slice` for the run (the only dev lane slice above 0.2 CPU; `dev-interactive` is excluded by design, buildkitd and background were idle). Original values `cpu.weight=20`, `cpu.idle=0`; both restored and verified after each run (`restore_ok: true`, final read `idle=0 w=20`). Campaigns were not stopped or frozen.

| | r1002 | v1001 |
|---|---|---|
| CPU model | AMD EPYC 9645 96-Core Processor | AMD EPYC-Genoa Processor |
| vCPUs | 4 (1 thread per core, KVM) | 4 (KVM) |
| **GB7 Single-Core** | **1673** | **1367** |
| **GB7 Multi-Core** | **3521** | **3431** |
| wall | 470.5 s | 496.9 s |
| steal fraction (/proc/stat delta) | 0.0000 | 0.0095 |
| CPU busy fraction during run | 0.963 | 0.899 |
| CPU PSI some avg10 before / after | 6.55 / 46.98 | see raw `conditions.json` (before about 17 at launch) |
| load average 1m before / after | 4.71 / 7.99 | not recorded in this summary (about 4.4 at launch) |
| result URL / claim link | exist (claim link only in the 0600 file on the host) | exist (same) |

- Caveat: the Single-Core scores differ by 22 percent between two nominally similar Genoa-class VMs; v1001 shows nonzero steal and ran under the busier PSI. The campaigns shared the 4 vCPUs even with `cpu.idle=1` (the "after" PSI of 47 on r1002 is largely Geekbench's own 4-thread load). Treat these as "measured under campaign load with lane slices at idle weight", not as clean idle-host numbers. If a clean number matters, run once with the campaigns paused (not done: instruction was not to stop them).
- Baseline relation: HB-1 GB7 baseline is 2500, so x_ref = x * 1673 / 2500 = 0.669 x on r1002 and 0.547 x on v1001.

### Findings about the environment (affect the design)
1. **mdt-psi-guard undoes `cpu.idle`.** `/usr/local/sbin/mdt-psi-guard.py` writes `cpu.weight` (1 under pressure, then restores the original) on `dev-gates`, `dev-background`, `dev-buildkitd`. In the kernel a `cpu.weight` write resets `cpu.idle` to 0, and leaving idle resets the weight to 100. On r1002 the first launch lost idle within minutes. Fix used: the run script re-asserts `cpu.idle=1` every 1 s while running (`idle_reasserted` shows 1 re-assert per run) and writes `cpu.idle` first, then the original `cpu.weight` at restore. I also corrected `dev-gates` `cpu.weight` back to 20 on r1002 after a 30 s smoke run had left it at 100 (restore bug in my first script; fixed, so later restores are exact). Anyone using `cpu.idle` on these slices must expect this.
2. **GB 6.7.0 cannot upload from r1002**: two full runs (363 s and 372 s) ended with `unknown error (internal code 35)` at the upload step. Free GB6 prints no scores before the upload, so **GB6 produced no scores, no result URL, no claim link** (HB-1 parser output: all `None`; fixture saved). GB 7.1.0 uploaded fine from both hosts. `browser.geekbench.com` resolves to Cloudflare; the code-35 failure reproduced twice, so I did not retry. The full GB6 comparison run therefore failed; GB7 is the working major on these hosts today.
3. **The HB-1 `--resolve` / result-page fallback cannot work here**: the result page returns the Cloudflare 403 challenge to curl with the tool's User-Agent from both hosts (saved as `result-page.html` in the 0600 host dir; it is the challenge page, not a result). No real GB7 result-page HTML could be captured, so there is no result-page fixture. The stdout path (scores in the "Benchmark Summary") is the only usable source, and it works.

## HB-1 parser verification against real GB7 output
`parse_stdout` imported by path from the HB-1 worktree (`cbda960d5`), run on the sanitised real stdout:
- r1002: version 7.1.0, single_core 1673, multi_core 3521, result_url `https://browser.geekbench.com/v7/cpu/1000001`, claim_url found, result_major 7.
- v1001: version 7.1.0, single_core 1367, multi_core 3431, result_url `.../v7/cpu/1000002`, claim_url found, result_major 7.
So **yes: both scores, the result URL and the claim URL are extracted, and the assumed v7 URL shape was right.** The real layout is the GB6 layout ("Benchmark Summary", "  Single-Core Score   N"). The `ASSUMED-SHAPE` GB7 fixture in HB-1 can be replaced by these. Real GB7 output differs in one respect worth a test: the startup banner (internet-required notice, Pro upsell text, a `--unlock <email>` line) precedes the System Information block.
For the GB6 failure fixture the parser returns all `None` (no scores, no URL), so `measure()` would raise "neither scores nor a result URL" (exit 3): correct behaviour, but HB-2 should treat exit 3 as warned/retry later rather than as install failure.

## Recommendation (does the runtime matter, and the minimum-viable alternative)
- Once per host, about 8 minutes of wall time once in the host lifetime is not a correctness or cost problem. The risk is not the length but (a) contention with campaigns (scores 20 percent apart here) and (b) install blocking or timing out.
- Recommended minimum-viable design:
  1. Run **after** install completes, **detached** (systemd transient unit or `setsid nohup`), not inside the install critical path; the install records `cpu-benchmark: pending`.
  2. Run at low impact on others but not starved: the tool already refuses at CPU PSI above its threshold; HB-3 should keep `--max-psi 5` on a quiet host and retry later (a timer with backoff) instead of running under load. Do not rely on `cpu.idle` on dev slices while mdt-psi-guard is active (finding 1).
  3. Pin **GB 7.1.0 + sha256** (GB6 upload is broken from these hosts today); keep the 1800 s timeout (observed 470 to 500 s on 4 vCPU KVM; QEMU TCG will be much longer).
  4. Treat upload failure and the Cloudflare-blocked result page as `warned`; re-measure later, do not fabricate a score.
  5. Do not look for a shorter run: no legitimate subset exists, a partial run is not comparable, and the full run is a one-off.

## Artifacts
- On the hosts (root only, mode 0600 files in a 0700 dir; contain the raw stdout including the claim link, conditions with PSI/steal/cgroup values): `/var/lib/mdt/host-facts/geekbench-explore/{gb7-7.1.0,gb6-6.7.0-attempt1,gb6-6.7.0-attempt2}/` on r1002; `.../gb7-7.1.0/` on v1001. I did not write `cpu-benchmark.json` or `cpu-benchmark.claim.json` (those belong to the installer); the explore directory is the claim-link store for these runs. Scratch dirs `/var/tmp/gb-explore` (tarballs, binaries, ~1 GB each) removed on both hosts.
- Fixtures (sanitised: claim key replaced by `REDACTEDKEY`, result ids replaced by 1000001 / 1000002, Model/Motherboard/BIOS lines redacted; CPU name kept because it is what the tool records): `<controller-program-dir>/reports/geekbench-fixtures/`
  - `gb7-7.1.0-stdout-real-r1002.txt`, `gb7-7.1.0-stdout-real-v1001.txt`
  - `gb7-7.1.0-stdout-timestamped-real-r1002.txt`, `...-v1001.txt` (seconds since start, tab, line)
  - `gb6-6.7.0-stdout-real-r1002-upload-failed.txt`
  - `gbrun.py` (the run wrapper: idle set, 1 s re-assert, always-restore, conditions, timestamps)
  - No result-page HTML fixture (blocked, see finding 3).
- No claim link appears in this report or in any repo file.

## Deviations
- First launch on both hosts was aborted after about 4 minutes (my first wrapper lost `cpu.idle` to the psi-guard on r1002); relaunched with the re-assert fix. My kill command also hit its own shell, harmless; the restore handler ran and values were verified. No scores from the aborted runs, nothing uploaded.
- GB6 second run was a retry of the failed upload, not a second host.
- No backlog ids filed. Suggest filing against run-gate/mdt host-setup: psi-guard `cpu.weight` writes reset `cpu.idle` (document or write idle-aware), and against HB-1/HB-2: result-page fallback is Cloudflare-blocked, GB6 upload fails with code 35.
