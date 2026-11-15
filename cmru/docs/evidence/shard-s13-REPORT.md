<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-07; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/shard-s13-REPORT.md -->
<!-- relation: CMRU R2 shard report; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Shard s13 (cmru R2) resumed and finished on the controller: REPORT

Date 2026-11-08 (17:43Z-23:52Z). Controller-side Sonnet agent. Files: `<controller-program-dir>/shard-move-s13/`.
No credentials printed. Only s13 touched; <protected-host>, other shards and other hosts untouched; Docker actions only by exact name of the s13 container.

## Verdict

- **s13 is complete: all 277 candidates judged, lane verdict `FAIL / MUTANTS_SURVIVED` (exit 1), a real verdict file exists.**
  killed 274, survived 3, budget_exceeded 0, crashed 0, hung 0, equivalent 0.
- Of the 127 that were pending at the move: 124 killed, 3 survived. The 150 judged on <host> (all killed) were not re-run.
- 3 survivors, all `cmru/src/cmru/transaction.py`, operator `python:compare-swap`: `Eq->NotEq` (`efbfc63c...`), `Gt->GtE`
  (`39e93a25...`), `IsNot->Is` (`a6a3ec8d...`). Full records: `shard-move-s13/final/survivors.json`.
- The assay campaign closeout says `incomplete, planned 277, completed 277, pending 0, blocker coverage_not_reverified`
  (same closeout wording as s4). It is analysis only; the lane verdict stays FAIL/MUTANTS_SURVIVED. Resuming again would
  only repeat the baselines and the same verdict, so no further run was started. Supervisor ended itself (`final-exit` = 1).
- Evidence kept in `shard-move-s13/final/`: `verdict-cmru-r2-s13.json` (written 23:52:28Z), `progress-cmru-r2-s13.jsonl`
  (all 277 candidate events, 2 `resume` events, `resume_merged`, `end`), `profile-cmru-r2-s13.jsonl`, the campaign
  analysis `232986a4...campaign.json/.txt`, `survivors.json`.

## Why <host> refused and why the controller could do it (no change to the lane or to identity)

- <host> refused because its new cockpit has no `/run/cgprofile/ctl.sock` (run-gate 25.0.2 preflights the observer socket
  when a lane declares `resource_observer = "cgprofile"`).
- `assay-r2-s13` and `assay-r2-s4` declarations are IDENTICAL (`cmru/run-gate.toml`: kind assay, environment tester-unified,
  `resource_observer = "cgprofile"`, clean_tree, budget 7h, jobs 2, resources memory 4g / swap 16g / candidate_ram 1536m /
  pids 8192 / cpus 3). s4 did not avoid the observer: the s4 log line "in-process watch - profiling disabled" is only the
  `RUN_GATE_PROFILE=off` effect (the s13 log from <host> before the reinstall has the same line). s4 worked on the controller
  because the controller HAS the socket: the devcontainer sees `/run/cgprofile/ctl.sock` (srw-rw---- root:994, vscode is in
  group 994) and the daemon container `cgprofile-host-daemon` is up. The lane's docker argv bind-mounts
  `/run/cgprofile/observer` read-only and adds `--group-add 990` (host paths; the daemon was not touched, nothing was mounted
  into any running container). No lane edit, no env change that is part of the identity.

## What ran

1. Export verified: tar sha256 `7b305416...3e02` = recorded value; `tree/SHA256SUMS` (9368 files) 0 failures. Checkout
   `c7d07240825e5ca28c45194556444d17e910ba92` git clean, progress file 6556 lines, 150 unique candidate events (all killed),
   inflight dir held only `inflight.lock` (<host>'s stop was graceful, so no stale owner record to move aside), no verdict file.
2. `cp -a` to `<worktree>` (as for s4); verified against the manifest (only
   `.git/index` differs, a git stat cache refreshed by `git status`). Campaign dir copy: `shard-move-s13/<host>-campaign-copy/`.
3. Image `tester-unified:r25-d38b3324fba5` = `sha256:d38b3324fba56ecc...` (printed by run-gate at lane start), run-gate 25.0.2 from
   `<controller-home>/.venvs/run-gate-25.0.2`, `RUN_GATE_ALLOW_MAIN_CHECKOUT=1 RUN_GATE_PROFILE=off`, `--jobs 2`, nice 19 / ionice.
4. `run-s13-controller.sh` (s4 supervisor plus a name-based container lookup so the `docker update` limits really apply:
   memory 3g, swap 16g, cpus 2, applied within 10 s of each start). Guard inside the script: memory PSI some avg10 > 25 x3,
   MemAvailable < 1 GiB x3, swap-used growth > 6 GiB x3, host `oom_kill` counter growth, container `OOMKilled`; any trip ->
   `PAUSED`, `docker stop` of the exact container. `watch-ps.sh` logged the top RSS processes every 20 s.

## Three attempts (state was never at risk)

| Try | Start | End | Result |
|---|---|---|---|
| 1 | 17:43Z | 17:52Z | R0 baseline FAILED on one flaky cmru test, no candidate run, no `resume` event, assay-state unchanged (1283 files = export) |
| 2 | 17:53Z | 21:29Z | baseline PASS, `resume` 18:12:00Z, 96 candidates, then guard trip (below) |
| 3 | 21:31Z | 23:52Z | baseline PASS, `resume` 21:52:12Z, remaining 31 done, `end` 23:42:07Z, verdict 23:52:28Z |

- **Try 1 flaky test**: `tests/test_installed_wheel_subprocess.py::test_the_refusal_names_at_most_twenty_paths_and_the_remedy`, returncode 141
  (SIGPIPE) instead of 2. Cause: `cmru/build-initial-standalone.sh:206` `printf '%s\n' "$2" | head -n 20 | sed ...` under
  `set -o pipefail` with 30 listed paths: `head` can close the pipe before `printf` is done. A timing race (the same test passed in
  the 12:59Z baseline on <host> and in tries 2 and 3). Same command re-run, nothing changed. Try 1 logs: `try1-baseline-flake/`.
  **Backlog finding for the controller to file (cmru `KNOWN_ISSUES_TODO_BACKLOG.md`): SIGPIPE race in the refusal listing of
  `build-initial-standalone.sh`; a lane baseline can fail on it at random.**
- **Try 2 guard trip 21:29:16Z**: memory PSI some avg10 29.8 / 28.4 / 42.4 on three consecutive 20 s samples, MemAvailable
  about 5 GiB (not low). `watch-ps.log` attributes the spike to other processes on the controller (a 1.5 GiB RSS process
  started 21:28 and two more Node-style `MainThread` processes), not to the shard (container 1.35 GiB of 3 GiB, python workers).
  Guard stopped the container (state resumable, inflight dir clean), I waited for PSI < 8 for 3 samples, archived the logs
  (`try2-guard-stopped-2129Z/`) and relaunched at 21:31Z. Loss: in-flight candidates plus about 20 min of double baseline.
  Not seen again afterwards.

## Resume proof

Both `resume` events: `candidate_total 277, rejected_total 0, rejudged_total 0`.
- 18:12:00Z: `resumed_total 150`, following `candidates` event `pending_total 127, selected_total 277`.
- 21:52:12Z: `resumed_total 246`, `pending_total 31`.
- `judge_sha256` = `cc3e8e623a7ff1077a51bc719e714be39b3eec8e7084e850d779a663c27f6f39` in the <host> event (12:59Z) and in
  both controller events: BYTE-IDENTICAL. Never a new 277 baseline. (127 pending rather than 126: the export holds 150 unique
  judged candidates; the "151" in the <host> log is the candidate index counter.)

## Monitoring (guard samples every 20 s, 1097 samples, `samples.csv`)

Memory PSI some avg10: max 42.4 (the trip), mean 3.5. MemAvailable min 3.9 GiB. Swap used 12.8-17.8 GiB (host-wide, includes
other tenants; shard growth well under the 6 GiB rule). `oom_kill` counter constant (6) for the whole run: no OOM. Throughput
was about 0.2-0.5 candidates/min (candidate wall 230-600 s while the controller disk shared io PSI full avg10 up to ~13),
i.e. about 6 h for 127 candidates; per-candidate budget (~1717 s) was never approached (budget_exceeded 0).

## State left behind

Nothing is running: no s13 container (run-gate removed it), supervisor, guard or watcher. Checkout
`<worktree>` (347 MB), `<host>/r2-handover-20261108/` (export), campaign copies and
all logs are intact; nothing was cleaned up. <host>'s own copy `campaigns/cmru-r2c-s13.pre-reinstall-20261108` and its restored
checkout are stale and must not be resumed (the controller copy is the live one and is finished). The <host> cgprofile 3.0.0
cut-over is not needed for s13 any more. Remaining R2 shards on <host> (s14 not started) are outside this task.
