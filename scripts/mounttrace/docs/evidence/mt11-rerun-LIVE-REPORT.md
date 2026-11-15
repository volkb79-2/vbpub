<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-08; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/mt11-rerun-LIVE-REPORT.md -->
<!-- relation: mounttrace live acceptance; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# mounttrace MT-11 acceptance re-run on the fixed head, live on <host> (2026-11-09)

STATUS: COMPLETE (all three items run; host cleaned to its starting state; queue flag removed 19:53Z).
Commit under test: `origin/main` = `cc16de7f2f23a2350aa6886071d51d88ee71c6ea` (fetched 19:3xZ, confirmed equal to the requested head; includes the mt-opt and mt-live-fix merges).
Shipped by `git archive cc16de7f2... scripts/mounttrace scripts/debian-install-v2/testing downloads` plus the repo-local `downloads/debian-13-genericcloud-amd64.qcow2` (sha512 matches `downloads/SHA512SUMS`), tar sha256 `04af5d07...a1bdf` identical on both sides; the guest `mounttrace.py` sha256 `b3116c1c...67ca` equals `git show cc16de7f2:scripts/mounttrace/mounttrace.py`.
Host <host> (4 cores / 8 GB, user `runner`, identity-checked by the wrapper on every call), work dir `/var<temp-path>` (removed at the end).
Guest: Debian 13 cloud, kernel `6.12.115+deb13.1-cloud-amd64`, QEMU/TCG (`systemd-detect-virt` = qemu), 4 vCPU, 3072 MB, no loop devices or privileged containers on the host itself.
Queue: flag `host-sat-queue.disabled.<host>` touched 19:32Z, removed 19:53Z; see the incident note.
Raw artifacts: `<controller-program-dir>/reports/mt11-rerun-LIVE-artifacts/` (`mt-vm-lane-rerun.log` 395 lines, `shapes-{tty,tty80,pipe,pipe120,pipe80,controls}.txt`, `watch-pipe-file.txt`, `watch-extra.txt`).

## Verdict table

| Item | Verdict |
|---|---|
| 1a VM lane `testing/vm/run-vm-tests.sh`, unprivileged phase | **PASS**: `Ran 64 tests in 57.026s`, `OK (skipped=5)`; the 5 skips are the `VmRootTests` (btrfs x3, Docker x2: "needs root inside the test VM"), expected in this phase |
| 1b VM lane, root phase (`MOUNTTRACE_VM=1 MOUNTTRACE_REQUIRE_VM_TESTS=1`) | **PASS**: `Ran 64 tests in 89.093s`, `OK`, 0 skips, 0 failures, 0 errors (btrfs-on-loop and Docker scenarios ran) |
| 1c whole lane | **PASS**: final line `== PASS: all mounttrace tests passed in the guest (mt-test-2322095)`, exit 0 |
| 2a Wings/host/game compare/audit/predict/soulmask/explain shapes (guide 10.3), COLUMNS=120 tty, COLUMNS unset non-TTY, COLUMNS=120 pipe | **PASS**: max line width 120 in all three; classes and exit codes as documented |
| 2b same at 80 columns (pty `stty cols 80` and `COLUMNS=80` pipe) | **PASS**: max content width 80 (the pty file shows 82 only because `sudo` on a pty echoes a literal `^@` in front of the first line of each run, 2 chars, the same artifact as the first run) |
| 2c UUID-bearing tails survive at 80 columns | **PASS** (details below); two documented limits remain |
| 2d categorical values not clipped | **PASS**: every `...` in every output is inside a path (0 lines with an ellipsis and no `/`, at 80 and 120); `NOT LINKED`, `different`, `HOST-ONLY`, `PID-ONLY`, view labels and propagation moved complete to labeled lines when the column did not fit |
| 3 `--watch` output through a file or pipe (the first-run bug) | **PASS** (W1-W8; Ctrl-C status W9b PASS) |
| Real Wings tree | **ABSENT** on <host> (as in the first run); stand-in only |

## 1. VM lane

Commands (host side, environment as in the first run: `CGROUP_PARENT_DEV_GATES=dev-gates.slice CGROUP_PARENT_DEV_INTERACTIVE=dev-interactive.slice BUILDX_BUILDER=mdt-managed`):

```
mkdir -p $D/repo/scripts/mounttrace/artifacts/vm-lab        # D=/var<temp-path> (plain directory, as the lane only needs it to exist)
cd $D/repo/scripts/mounttrace/testing/vm
bash ./run-vm-tests.sh --check-inputs-only
  == test inputs present (6 files): mounttrace.py README.md mounttrace.md docs/CONSUMERS.md docs/DESIGN-GUIDE.md testing/vm/run-vm-tests.sh
nohup bash ./run-vm-tests.sh > $D/mt-lane.log 2>&1 &         # started 19:35:11Z, PASS line before 19:44Z (about 9 minutes including the sealed base)
```

Timeline in the log: `== building sealed base mounttrace-ready-v2` (line 1), `== sealed base ready` (172), `== test round mt-test-2322095 on a fresh overlay of mounttrace-ready-v2` (173), `== unprivileged (user-namespace scenarios)` (191), unprivileged result (290-292), `== root in the guest: btrfs + Docker scenarios REQUIRED` (293), root result (392-394), `== PASS` (395).

```
Ran 64 tests in 57.026s
OK (skipped=5)
== root in the guest: btrfs + Docker scenarios REQUIRED (a skip is a failure)
Ran 64 tests in 89.093s
OK
== PASS: all mounttrace tests passed in the guest (mt-test-2322095)
```

Compared with the first run (54 tests, 4 failures + 4 errors in the unprivileged phase, root phase never reached by the lane): the test input manifest copied the four documents (no `FileNotFoundError`), the four stale MT-09 assertions pass, `test_tucking_prediction_matches_kernel` passes, and the watch tests (`test_watch_first_pass_is_visible_on_a_file_before_sleep`, `test_watch_flushes_first_and_changed_reports_to_buffered_stdout`, `test_watch_reports_change_and_vanished_pid`, `..._ctrl_c_returns_last_status`) pass in both phases. Ten more tests than before (64 vs 54).

Footprint: the lane needed about 2.8 GiB under `vm-lab/images` (`case-b-base` 0.34 GB, `case-b-ready-base` 1.0 GB, `mounttrace-ready-v2-base` 1.5 GB) plus 0.34 GB `downloads`; free disk never fell below 5.4 GiB (86 percent used at the peak), 6.7 GiB after I deleted the two build-input images, 8.6 GiB after final cleanup (same as at start). Memory stayed under 4 GB used.

## 2. Wings-tree stand-in, current CLI

No real Wings tree exists on <host> (nothing under `/var/lib/pterodactyl`, no wings unit); the stand-in is the first run's, rebuilt by the same script (`standin-setup.sh`, from `mt11-iobench-LIVE-artifacts`) in a fresh guest from the sealed base: three instances `volumes/<uuid>` with A `3f9a1c2e-7b44-4d10-9a3e-5c8d1e0f2a61`, B `3f9a1c2f-...-2a61` (differs from A only in the first group), C `3f9a1c2e-...-2a62` (differs from A only in the last character), shared tmpfs RAMDISK bound into every `WS/Content/Paks`, a per-instance tmpfs on every `WS/Saved/Logs`, state file `/run/soulmask_tmpfs.state`, Docker containers by exact name `mt-accept-wings` (`/var/lib/pterodactyl` bound rslave) and `mt-accept-game-a|b|c` (volume bound rprivate to `<home>`); post-start events: C's tmpfs unmounted on the host (ghost), B got a new tmpfs `WS/Saved/late` (host-only for rprivate). `docker inspect` printed `/var/lib/pterodactyl -> /var/lib/pterodactyl : rslave` and `.../volumes/<A> -> <home> : rprivate`.

Commands (11 per mode, guide 10.3 shapes; script `acc-shapes.sh`): `soulmask $WINGS --volumes-root $V`; `audit $V/$X "$GAME_X:<home>"` for A, B, C; `audit $V "$WINGS:$V"`; `compare $V/$X/WS/Saved/Logs "$WINGS:..." "$GAME_X:<home>/WS/Saved/Logs"` for A, B, C; `predict $V/$B/WS/Saved/new "$WINGS" "$GAME_B"`; `explain $V/$A/WS/Saved/Logs --submounts`; `compare` of the three Logs paths in the host view. Modes: real pty 120 (`script`, `stty cols 120`), real pty 80, pipe with `COLUMNS` unset (non-TTY), pipe `COLUMNS=120`, pipe `COLUMNS=80`.

Behaviour (identical across all five modes): exit codes `3,0,3,3,0,0,0,3,0,0,3` in the order above; soulmask ALERT for C's missing host mount plus the LEFTOVER `soulmask-late`; audit A `LAYER` only (exit 0), audit B `HOST-ONLY WS/Saved/late` (exit 3), audit C `PID-ONLY WS/Saved/Logs` (exit 3); compare A/B `OK ... same in all 3 inspected views` with Wings `linked` and game `NOT LINKED`; compare C `DIFFERENT 1 of 2` (exit 3); predict `OK appears in 1 of 2 inspected views`. As in the first run, as documented.

Widths (measured over every output line, excluding my `###` echo lines): tty120 max 120, pipe unset max 120, pipe120 max 120, pipe80 max 80, tty80 max 80 (plus the 2-char `^@` artifact).

Excerpts at 80 columns (`COLUMNS=80`, pipe; full text in `shapes-pipe80.txt`):

```
audit /var/lib/pterodactyl/volumes/3f9a...0f2a62 in host vs 1294:<home>
ALERT PID-ONLY: WS/Saved/Logs: the PID view has a mount with no matching host
  mount
STATUS   CLASS    PATH             HOST    PID 1294     SAME      LINK
PID-ONLY PID-ONLY WS/Saved/Logs                         different NOT LINKED
HOST #29 ext4 /dev/vda1
PID 1294 #494 tmpfs soulmask-pi-3f9a1c2e

compare /var/lib/pterodactyl/volumes/3f...a3e-5c8d1e0f2a62/WS/Saved/Logs in host
DIFFERENT 1 of 2 compared views differ
VIEW  MOUNT FSTYPE SOURCE FSROOT OBJECT DEV:INODE   RO PROPAGATION LINK   SAME
      #362  ext4                        254:1:26088 rw slave(1)    linked same
VIEW pid 1074
SOURCE /dev/vda1
FSROOT /var/lib/pterodactyl
OBJECT /var/lib/pterodactyl/volumes/3f9...4-4d10-9a3e-5c8d1e0f2a62/WS/Saved/Logs
      #494  tmpfs         /      /      0:56:1      rw private
VIEW pid 1294
SOURCE soulmask-pi-3f9a1c2e
LINK NOT LINKED
SAME different
predict a new host mount at /var/lib/pt...44-4d10-9a3e-5c8d1e0f2a61/WS/Saved/new
MOUNTPOINT /var/lib/pterodactyl/volumes...4-4d10-9a3e-5c8d1e0f2a61/WS/Saved/late
```

Result against the first run: at 80 columns the OBJECT value was `/va...gs` or `/v...gs` (tail lost). Now it is the labeled line `OBJECT /var/lib/pterodactyl/volumes/3f9...4-4d10-9a3e-5c8d1e0f2a62/WS/Saved/Logs`, with `...2a62` and the whole `WS/Saved/Logs` visible, and instance A versus C (differ in the last character) are distinguishable in every header (`...0f2a61` vs `...0f2a62`), in OBJECT, in `MOUNTPOINT`, in `predict` and in `explain`. The categorical values that did not fit in the table (`pid 1294`, `NOT LINKED`, `different`) moved complete to labeled lines (`VIEW pid 1294`, `LINK NOT LINKED`, `SAME different`). At 120 columns nothing is cut except the long OBJECT value, which moves to its own `OBJECT` line, complete. Controls: `-v` at `COLUMNS=80` prints the full paths and the full table (`shapes-controls.txt`); `--json` is untruncated.

Limits that remain (consistent with `mounttrace.md:553-556`, not contradictions):
1. At 80 columns the header cuts the interior of the UUID: `compare /var/lib/pterodactyl/volumes/3f...a3e-5c8d1e0f2a61/WS/Saved/Logs in host` is the same text for instances A and B (they differ only in the first group). The distinguishing `SOURCE soulmask-pi-3f9a1c2f` and the PID columns remain; `-v` shows the whole path. With random v4 UUIDs this needs the last 7 hex digits to collide.
2. The three-path host-only `compare $A $B $C` labels both result rows `host` (VIEW column) and shows an OBJECT for only one of them, so the rows cannot be matched to paths without `-v`. Same at 120 columns. I found no sentence in `mounttrace.md` promising per-path labels there; filed as an observation for the controller (not verified against the design guide).

## 3. `--watch` through a file or pipe (inside the guest, mounttrace.py of cc16de7f2)

Script `acc-watch.sh` and `w8.sh`; output in `watch-pipe-file.txt` and `watch-extra.txt`. Previously: `timeout 6 python3 mounttrace.py --json --watch 0.2 soulmask $PID --volumes-root /tmp > <temp-path>` gave 0 bytes, and a pipe to `head -c 100` gave nothing.

```
W1 NDJSON to a FILE (same repro, soulmask --json --watch 0.2, timeout 6):  exit=124 (timeout kill) bytes=5872 lines=1   {"schema":1,"command":"soulmask",...   PASS
W2 NDJSON to a PIPE (| head -c 100):                                        bytes=100  {"schema":1,"command":"soulmask","header":"soulmask audit /var/lib/pterodactyl/volumes","verdict":{"   PASS
W3 human report to a FILE (explain --watch 0.2, timeout 6):                 bytes=598, begins "host /var/lib/pterodactyl/volumes/3f9a...2a61/WS/Saved/Logs mnt:..."   PASS
W4 first pass through a PIPE with a 30 s interval:                          first line after 0.85 s   PASS (not after 30 s)
W8 file size 3 s into a --watch 30 run (process still sleeping):           --json bytes_at_3s=1231, human bytes_at_3s=598   PASS
W5 change line on a FILE (tmpfs mounted under the watched path after 2 s, unmounted 1 s later):
     11:--- 2026-11-09 19:48:57 mount tables changed: host +#430
     26:--- 2026-11-09 19:48:59 mount tables changed: host -#430            PASS
W6 NDJSON on a FILE with the same mount/unmount: 3 objects (first pass, added, removed), each valid JSON   PASS
W7 vanished PID (explain $PID:/ --watch 0.3, PID killed after 1.5 s):      "mounttrace: pid 1866 vanished (process exited or container restarted; look up the new PID)"  exit=1   PASS
W9b Ctrl-C (SIGINT via timeout --preserve-status -s INT, audit with a ghost game container, human to file): exit=3 (the last pass's status)   PASS
```
The first attempt at W9 sent `pkill -INT` to a shell background job, whose SIGINT is ignored in a non-interactive shell; the python process did not react and I killed it (my test artifact, not a product finding; the correct form W9b passed). W9c in the same artifact is my own usage mistake (an `audit` with no PID argument), ignore it. SIGTERM (from `timeout`) kills the process with 124/143 as usual; the docs promise only the Ctrl-C status.

Docs matched: `mounttrace.md:39` and `:532` (one NDJSON object per pass), `:561-564` (each human line and NDJSON object flushed, including the first pass and a change line, visible on a redirected file or pipe).

## Contradictions with docs / code

None found in this run that block acceptance. Previously reported contradictions are gone: lane copy set (`tests/test-inputs.txt` manifest, 6 files, checked before any boot), `--watch` flush (`mounttrace.md:561-564` holds), stale MT-09 assertions. Smaller notes:
- `testing/vm/run-vm-tests.sh` header (and the log line) says the one-time base build takes "15-40 min" on TCG; here the whole lane, base included, took about 9 minutes (the runner image, apt and Docker layers were warm). Cosmetic.
- `KNOWN_ISSUES_TODO_BACKLOG.md:24` and `:197-199` (MT-11 "OPEN only for the controller's VM-lane rerun on <host> at the fixed head") can now be updated with this result: commit `cc16de7f2`, unprivileged `64 tests OK (skipped=5)`, root `64 tests OK, 0 skips`. I did not edit the repo.

## Incident note (my own)

Flag set at 19:32Z; the queue loop had already decided to launch `sat-r0-r1-20261109T193245-<host>` (19:32:46Z, `scripts/debian-install-v2:r0-r1`, verdict PASS in 69 s, done 19:33:57Z), and `<host>.current` was absent at my check only because of that race. My first lane attempt (started about 19:33:30Z) overlapped it for about 40 s, while only the cached runner-image build and a harness container were running. I noticed at 19:34Z, killed my own process group, removed my own container `debian-install-vm-harness-34fb7dddc6e9` by exact name, wiped my own `vm-lab`, and restarted only after the queue logged `disabled flag present: not launching` (19:34:27Z). The queue lane passed regardless. Lesson for the next window: touch the flag, then wait for the NEXT log line `disabled flag present: not launching` (not just an absent `<host>.current`).

## Cleanup (verified)

Guest `mt-acc2` destroyed; runner container `debian-install-vm-harness-34fb7dddc6e9` removed by exact name; image `debian-install-vm:34fb7dddc6e9` (built by my run) removed; `/var<temp-path>` deleted; after cleanup `docker ps` shows only `mdt-buildkitd` and `mdt-cockpit-runner` (as at start), no qemu process, `/` at 78 percent with 8.6 GiB free (as at start), flag `host-sat-queue.disabled.<host>` removed 19:53Z (the queue then resumes by itself).

## Unverified

- A REAL Wings tree: absent on <host>; everything in section 2 is the stand-in.
- Behaviour on a live Wings node under production mount churn and a very large `volumes/` tree (hundreds of instances); only 3 instances and a short mount table were exercised.
- Instances whose UUIDs differ only in the first group, at 80 columns, from the compare/audit header alone (limit 1; needs `-v`/`--json`).
- The `--watch` bug was re-checked for stdout redirected to a file and to a pipe; not for a socket or a pty with a slow reader.
- Which per-path labels the three-path host-only `compare` is meant to show (limit 2).
