<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/swap-bench-window-REVIEW3.md -->
<!-- relation: swap benchmark review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# swap-bench-window.draft.sh revision 2: independent safety review 3 (2026-11-08)

Reviewer: fresh read-only session. No host, ssh, docker, VM or controller device touched. Work dir `<controller-scratch>/rv-swapbench3/`
(own copy of the stub/unshare harness in `sim/`, logs `b9.log`, `b9b*.log`, `b7r/b7s`, `b1-b6.rv.log`).

## Verdict: READY-FOR-V1001

No blocking finding. No path reaches `wipefs`, `dd` or `mkswap` while the device is listed in `/proc/swaps`, and a failed or timed-out
swapoff is never followed by a wipe. Three non-blocking hardening items (N1 recommended) below.

Verified inputs: script sha256 `ef74155e5104d4d434982822ba6c3cf3dfa3887050ec2d4dddf8dc586a7a69b2` (matches), `.orig2` sha256 `71f23ac8...482f0` (matches).
`bash -n` clean, `shellcheck -s bash` clean (shellcheck at /usr/local/bin). Diff read in full (4 hunks + F1/F3/F5 + state-create change).

## (1) Can any path reach `wipefs` while swapoff runs / device active?

No. The only `wipefs` call is line 177 (`run wipefs -a`). In `--commit` mode it is reachable only through line 155-166 + 167 + 170-173:
- `rc=0` from `timeout ... swapoff` is required (any non-zero rc ends in `exit 1` at line 165, whatever `swapoff_running`/`active` say). `timeout`
  returns 0 only if swapoff itself exited 0, i.e. the swapoff syscall completed; a timed-out run returns 124/137 even if swapoff completes later.
  The wipe decision therefore does not depend on the process-name detection at all, so a false negative of `swapoff_running()` cannot cause a wipe.
- then `active` must be false (167), PARTUUID/size/swap UUID re-read must equal the recorded values (170), `active` again (173).
- last safety net: `wipefs` opens the device exclusively and gets EBUSY on a swap-active device (rehearsal R1, and the harness `wipefs` stub models it).
- dry-run prints only; `end` never calls `wipefs`.
`swapoff_running()` is used only in the failure branch (157) and in `end` (189). Order in the failure branch is correct: process check first, then `active`
(once no swapoff process exists, the kernel state is final: listed = re-inserted/never left, not listed = gone).

How the process is detected (lines 78-85): scan `/proc/[0-9]*`, `comm == swapoff` and cmdline (NULs to spaces) contains `swapoff <dev> ` (trailing space prevents
vda9/vda90 and vda1/vda10 confusion). No pid file, no pgrep. The flock (fd 9, inherited by the swapoff child) is an independent second signal for begin's own
orphan: confirmed, `end` and `begin` refuse naming `.lock` while the orphan lives (case C1).
- begin's own swapoff: reliable (argv is exactly `swapoff /dev/vdaN`). A zombie has an empty cmdline, so no stuck false positive from an unreaped one.
- pid reuse between the `comm` read and the `cmdline` read: harmless (a new process only matches if it is really a `swapoff <dev>`); C4 confirms a process
  named `sleep` whose argv contains `swapoff /dev/vda9 ` and a `swapoff /dev/vda8` are NOT matched (no false positive, no stuck state).
- false negatives for a swapoff started by someone else (operator/systemd): see N1. They are non-destructive (see below).

## (2) State-file handling and recovery after STILL RUNNING

Correct and sufficient. After STILL RUNNING the state stays (line 158 prints, 165 exits). Operator: wait until `pgrep -x swapoff` is empty (the lock frees at the same
moment), re-run `end` dry-run, then `end --commit`. Tested (C1): during the orphan `end` and a second `begin` refuse via the lock, state kept; after the orphan finished
(device left `/proc/swaps`, signature intact) `end` restored (dd marker MiB, mkswap -L -U, swapon -a, verify, archive). If the kernel instead re-inserted the device,
`end` reports "nothing to restore" and archives (T5b/N5b path, also N5b in the rehearsal). Failure variants all keep the state: rc!=0 with device gone (C3: kept, `end` restores),
blkid error after swapoff (C5b: ABORT, no wipefs call, state kept), state present + device inactive + not wiped (C5: `end` dry-run and commit restore).
The runbook text (patch lines for "swapoff timeout is best effort", "Close ALWAYS", manual recovery) matches this behaviour. One wording nit: "the orphan holds the lock" is true only for
begin's own swapoff child (see N1 for a foreign one).

## (3) F5 identity re-check (lines 168-173)

Re-reads `blkid -s PARTUUID`, `blockdev --getsize64` and `blkid -p -s UUID` from the device right before `run wipefs`, then `active` again. All three helpers end in `|| true`
and return an empty string on a command error, so an error compares unequal to the recorded value: fail closed (C5b: blkid exiting 4 after swapoff gives ABORT, no wipefs call, state kept).
PARTUUID mismatch and re-activation in the gap: ABORT as well (rehearsal N5/N5b, re-run via the matrix logs). PARTLABEL is not re-read at this point (the task text lists it): not needed,
PARTUUID is the GPT entry's unique id and was checked against the PARTLABEL at the start; see N3.

## (4) F3 required tools

Up-front: `fuser` (line 66) and `flock findmnt lsblk blkid blockdev wipefs mkswap swapon swapoff timeout awk grep dd sync install` (68-70), before any state change (the loop runs before the `case`).
Verified by removing each in turn from PATH (begin --commit and end): every one refuses with rc 2, no state dir created, no stub call (b7r N3b, b7s N3c for flock/fuser/findmnt).
`pgrep` is not used by the script (only mentioned in operator messages), `unshare` irrelevant. Not checked but used: `cat`, `tr`, `ls`, `basename`, `id`, `date`, `mv`, `rm` (see N2).

## (5) Matrix results

- Original b1-b6 (60+ cases) re-run on this exact sha in my own copy of the harness: normalized diff against the rehearsal `*.final2.log` files is empty for all six (identical).
- N1/N1b/N3/N3b re-run: as reported (octal margin refused like the default margin; each missing tool refuses).
- Six new cases of my own around the new branch (logs `b9.log`, `b9b.log`, `b9b2.log`; no kill-by-name, `timeout` around every run; harness gotcha: stubs share the host PID namespace, so parallel cases
  see each other's fake swapoff processes, the valid results are the sequential reruns):

| Case | Result |
|---|---|
| C1 swapoff child alive after KILL (rc=137, device still listed) | begin: STILL RUNNING, state kept, rc 1; `end` and second `begin` refused by `.lock`; after the orphan finished, dry-run `end` and `end --commit` restore and archive |
| C2a swapoff by another process, plain `swapoff /dev/vda9`, `end` | refused ("a swapoff of /dev/vda9 is still running"), state kept; after it finished `end` restores |
| C2b-e swapoff by another process as `swapoff -v /dev/vda9`, `swapoff /dev/vda8 /dev/vda9`, `swapoff /dev/disk/by-partuuid/...`, `swapoff -a` (sequential reruns, `b9b.log` D/E and `b9b2.log` -v/two-arg; the first parallel run showed refusals only because of cross-case process pollution and is not valid; the "variant" echo in `b9b2.log` is printed one iteration late) | all four NOT detected: device still listed with the recorded identity, so `end` prints "nothing to restore" and ARCHIVES the state while that swapoff is still running; when it finishes the device is out of swap with intact signature and no state file. Non-destructive (no wipefs/dd/mkswap ran; `swapon /dev/vda9` or `mkswap -L -U` by hand restores), but misleading. See N1 |
| C3 swapoff finished meanwhile (rc 255, device gone from `/proc/swaps`) | state KEPT, rc 1; `end` restores, archives |
| C4 pid-reuse/false-positive class: swapoff of `/dev/vda8` and a `sleep` with `swapoff /dev/vda9 ` in argv | not matched, `end` not refused for a running swapoff |
| C5 state present, device not active and not wiped | `end` dry-run lists dd/mkswap/swapon, `end --commit` restores, state archived; (C5b) blkid error after swapoff: ABORT, no wipefs call, state kept |
| C6 `end` with wrong `--expect-partuuid` while window open | refused rc 2, state kept |

## (6) Runbook patch

`div2-runbook-swap-benchmark.patch` (sha256 `1bd02ff1...0c37`) applies cleanly (`patch -p1 --dry-run`, no fuzz) to `reports/div2-reinstall-RUNBOOK.md` sha256 `94fea79f...a95b`
(the unmodified file in the release-program reports dir). It pins `ef74155e5104d4d434982822ba6c3cf3dfa3887050ec2d4dddf8dc586a7a69b2` in two places and contains no `71f23ac8`.
Caveat: that runbook is NOT tracked in `origin/main` (`artifacts/` is gitignored, `**/artifacts/` at .gitignore:382; `git ls-tree origin/main` finds no `div2-reinstall-RUNBOOK.md` in any /workspaces repo),
so "origin/main's runbook copy" could not be literally checked; the patch applies to the only copy that exists.

## Findings

Blocking: none.

N1 (recommended, non-blocking; script lines 78-85, 82): the arg match `swapoff $dev ` misses a foreign swapoff given as `swapoff -v DEV`, `swapoff -a`, `swapoff A DEV` (device not first)
or a by-partuuid/by-uuid/by-label name (systemd's own `swapoff` for a swap unit uses a by-partuuid path). Effect: `end` can say "nothing to restore" and archive the state while that swapoff
still runs (device then ends up out of swap, no state file). No wipe/mkswap risk (they need `active` false). Fix: treat any live non-zombie `swapoff` process as relevant:
```bash
swapoff_running() {
  local d
  for d in /proc/[0-9]*; do
    [ "$(cat "$d/comm" 2>/dev/null || true)" = swapoff ] || continue
    [ -n "$(tr -d '\0' < "$d/cmdline" 2>/dev/null || true)" ] && return 0   # non-empty cmdline: not a zombie
  done
  return 1
}
```
Cost: a swapoff of an unrelated device delays `end` until it ends (seconds). Also reword the "nothing to restore" text (line 196) to not claim "no swapoff ... is still running" as fact. Operators should simply not run any other swapoff during a window.

N2 (nit; line 68): add `cat tr ls basename id date mv rm` to the tool loop. `cat`/`tr` missing makes `swapoff_running` read false and `ls` missing makes the holders check read empty (both fail-open). Irrelevant on Debian (same coreutils package as the already-required `timeout`/`dd`/`install`), but costs nothing. Optionally pin `PATH=/usr/sbin:/usr/bin:/sbin:/bin` after line 24 so a sudo/user PATH cannot substitute a tool (the Devcontainer-clobbered-tools lesson applies to hosts too; the runbook runs it as root from the installed copy).

N3 (nit; lines 170-173): add `lsblk -dno PARTLABEL` to the F5 re-check (cheap, matches what `end` verifies), and use the low-level probe (`blkid -p -s PART_ENTRY_UUID`) instead of the cache-capable `blkid -s PARTUUID` for the re-read. Rehearsal R4 shows both agree, so this is hardening only.

N4 (nit, runbook): run `begin --commit` inside `tmux`/`screen` (or `nohup`) so an SSH drop cannot SIGHUP the script between swapoff and wipe; the outcome of a kill is safe (state kept, lock rule) but needs a manual `end`. The runbook already states the orphan/lock rule.

N5 (nit): STILL RUNNING message says "the orphan holds the lock"; true only for begin's own swapoff (a foreign swapoff does not hold `.lock`; `end` then relies on the process scan, see N1).
