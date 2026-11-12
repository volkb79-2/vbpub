<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/mounttrace-REVIEW.md -->
<!-- relation: mounttrace review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Review: 46d14e8ca feat(mounttrace): mount/propagation/btrfs analyser 3.2.0, guide, VM test lane

Reviewer: independent (Sonnet 5.5), 2026-11-08. Worktree `<worktree>`
(not modified; all runs in a git-archive scratch copy under `<controller-scratch>/rv-mt/`).

## Verdict: MERGE (no blocker); 4 should-fix follow-ups, 7 nits

Nothing in the analyser writes, mounts, spawns processes, or touches the host; the VM lane never attaches
a loop device, runs mkfs or mounts on the host; no hygiene problem. The should-fix items are a weak "am I
in the test VM" guard, terminal-escape output injection, no gate registration, and no test for the shared
harness change.

## (1) What the commit adds vs claims

Diff stat is 8 files, +2612 / -7 (not +2767): `scripts/mounttrace/{README.md, mounttrace.md (727 lines),
mounttrace.py (1105), tests/test_mounttrace.py (547), testing/vm/run-vm-tests.sh (117),
testing/vm/setup-vm-lab.sh (49)}` plus the shared harness `scripts/debian-install-v2/testing/vm/{run-vm-harness.sh (+39), README.md (+16)}`.
Everything the message promises is there. Not claimed in the message but present: the harness change is a
real behaviour addition to a harness the debian-install-v2 gate depends on (it is guarded, see (3)).

- Version: `3.2.0` exists only as `VERSION` in `mounttrace.py:57` (+ `--version`). No VERSION file, no CHANGES,
  no docs mention it. Coherent (single source) but unlike sibling script projects (`netcup`, `truenas` carry
  a `VERSION`). Nit N1.
- Commit trailer is `Co-Authored-By: Claude Opus 5.5`, the estate convention is `Claude Sonnet <noreply@anthropic.com>`. Nit N2
  (foreign session; not worth rewriting a cherry-picked commit).
- README/guide accuracy: guide 9.1 says it "never mounts, unmounts, enters namespaces or runs other
  programs": verified TRUE (no `subprocess`/`os.system`/`Popen`, imports are argparse, collections, dataclasses,
  fcntl, os, posixpath, re, stat, struct, sys, time). Guide 9.6 says the root tests "refuse to run anywhere but in that
  guest (`MOUNTTRACE_VM=1` plus `systemd-detect-virt --vm`)": overstated, see S1.

### Gate / lane registration (asked: report)
- `scripts/mounttrace/run-gate.toml`: does NOT exist. Not registered in `run-gate.root.toml` (it has only
  environments, no per-project lanes) and not in `cmru.orchestration.toml` (project graph has none of the `scripts/*`
  projects except via their own toolchains; `scripts/debian-install-v2` and `scripts/netcup` are also absent there, so
  that part is consistent with the other script projects). No `assay.toml`. No CI workflow lists it (`.github/workflows`
  has only two `.disabled` files). Siblings with a lane: `scripts/{damon-analysis,debian-install-v2,<private-host>-guide,netcup,telegram,truenas}/run-gate.toml`.
  Net: the 547-line suite is only reachable by hand or by the VM script. S3.
- The shared harness change IS covered by an existing lane: `scripts/debian-install-v2/debian_install_v2/tests/test_vm_test_boundary.py`
  (21 tests) passes against the commit (see (3)).

## (2) Analyser code review (`mounttrace.py`)

READ-ONLY: confirmed by grep + read.
- No `subprocess`, `os.system`, `Popen`, `shell=`, `eval`/`exec` calls, `sudo` (only in docstring examples, lines 37-42),
  no `unlink/rmdir/chmod/chown/rename/mkdir/shutil`, no `mount/umount/unshare/setns`, no btrfs subvolume/send/receive/
  balance/scrub/defrag. Every `open()` is read-only (`mountinfo_file`, line 173; state file, lines 776 and 986).
- The only ioctl is `BTRFS_IOC_GET_SUBVOL_INFO` (line 287), read-only, unprivileged, on an fd opened
  `O_RDONLY|O_DIRECTORY|O_NONBLOCK` (line 282), directories only. Constant verified: `_IOR(0x94,60,504)=0x81f8943c`;
  struct offsets (name at 8..264, flags at 288, three UUIDs at 296/312/328) match `struct btrfs_ioctl_get_subvol_info_args`.
- Symlink handling is component-wise through `/proc/PID/root` (`canonicalize`, lines 205-248), with a 40-link cap and
  absolute links restarting at that process's root. Good design.
- Unreadable `/proc` entries: `PermissionError` on lstat gives a lexical fallback with a NOTE (lines 232-235); `stat_identity`
  and `btrfs_subvol_info` return None on OSError; a missing `/proc/PID/mountinfo` or an unreadable state file gives
  `mounttrace: [Errno N] ...`, rc=1, no traceback (verified live: `--pid 999999`, `--state-file /root`, `--pid 1` as non-root).
  Parse failures raise `RuntimeError`, caught in `main`.
- Output: no secrets handled. It prints mount sources and superblock options verbatim (`desc(..., with_opts=True)`, line 577),
  which on overlay prints the full lowerdir/upperdir list and on CIFS/NFS can show user names and server addresses. That is
  inherent to a diagnostic tool; N3 asks for one line in the guide.

Findings:

- **S2 (should-fix) terminal-escape injection through mount paths / symlink targets.** `main()` only sets
  `errors='backslashreplace'` (lines 1054-1057), which handles surrogates, not control characters. mountinfo octal-escapes
  ESC/BEL, but `unescape()` (line 22) turns them back into raw bytes and every `print` emits them raw. Reproduced:
  a mount at `evil<ESC>]0;PWNED<BEL>x` inside a user namespace came out of `mounttrace --submounts` byte for byte
  (`od -c`: `033 ] 0 ; P W N E D \a`). The tool is meant to be run as root on a host against game containers, whose
  tenants control mount names (with CAP_SYS_ADMIN) and symlink targets (the `resolved -> ...` line). Fix: sanitize in one
  place, e.g. in `main()` replace `sys.stdout/stderr` with a small wrapper that maps `[\x00-\x08\x0b-\x1f\x7f-\x9f]`
  to `\xNN` (keep `\n` and `\t`), or run each `print` argument through a `safe()` for paths. Add a test with an ESC in a mountpoint name.
- N4 (nit) `--watch` loop catches only `FileNotFoundError` for a vanished PID (line 1088); reading `/proc/PID/mountinfo`
  of a just-dead process can raise `ProcessLookupError` (ESRCH), which falls to the outer handler
  (`mounttrace: [Errno 3] No such process`, rc=1): same outcome, less helpful text. Catch `(FileNotFoundError, ProcessLookupError)`.
- N5 (nit) Path probing is `stat`/`lstat`/`open(dir)` of the target path (`canonicalize`, `stat_identity`, `btrfs_subvol_info`,
  `btrfs_boundaries`). On an autofs trigger this can cause an automount, on a hung NFS/FUSE mount it can block. Not mentioned
  in guide section 8 (Limits) or 9.1 ("read-only" is true for the mount table, not for autofs). Add one sentence.
- N6 (nit) `audit_state_file` canonicalizes the state-file targets with symlink resolution even under `--no-resolve`
  (line ~800: `canonicalize(t, None)`), inconsistent with the other paths.
- Python >= 3.9 claim: no 3.10+ syntax found by grep (no `match`, `X | Y` annotations, `removeprefix`, `zip(strict=)`),
  but no 3.9 interpreter was available to run it (only 3.13/3.14 here). Unverified.

## (3) Tests

`python3 scripts/mounttrace/tests/test_mounttrace.py -v` (scratch copy, `nice -n 10`, 4 s): **Ran 23, OK, 18 passed,
5 skipped** (the 5 `VmRootTests`: btrfs x3, docker x2, skip reason "MOUNTTRACE_VM=1 is set only inside the test VM").
All 18 passed in this devcontainer (unprivileged user namespaces work here). The 5 VM-only tests were NOT run
(the VM lane is a 15-40 min TCG build and was out of scope under the host cap); the commit message's "23/23 unprivileged
and 23/23 as root" is therefore unverified by me.

Hand mutations (each applied alone to a copy, whole file re-run; "killed" = suite fails):

| # | Mutation | Result |
|---|---|---|
| M1 | mountinfo `split(' ')` to `split()` (line ~160) | killed |
| M2 | slave marked as sending back (propagation direction flag) | killed |
| M5 | tucking branch in `place_event` removed | killed |
| M6 | alert exit code 3 changed to 0 | killed |
| M8 | hidden-parent check in `hidden_reason` dropped | killed |
| M9 | mountinfo octal unescape base 8 to 10 | killed |
| M13 | PID-ONLY alert removed | killed |
| M14 | superblock `ro` detection returns False | killed |
| M3 | `inside()` prefix boundary (`prefix.rstrip('/') + '/'` to bare `startswith(prefix)`), line 184 | **SURVIVED** |
| M4 | `canonicalize`: absolute symlink no longer restarts at root, line 242 | **SURVIVED** |
| M7 | DIVERGENT alert removed, line 760 | **SURVIVED** |
| M10 | `newline='\n'` to universal newlines, line 173 (a `\r` in a path) | **SURVIVED** |
| M11 | `BTRFS_EMPTY_SUBVOL_DIR_OBJECTID` 2 to 3, line 64 | survived here (only the skipped VM test covers it) |
| M12 | `BTRFS_ROOT_SUBVOL_RDONLY` flag bit, line 62 | survived here (same) |

14 mutants, 8 killed, 6 survived (2 of those are VM-lane-only by design). The 4 real unit-level gaps (M3, M4, M7, M10)
are cheap to close with ParsingTests/Topology cases (sibling `/data` vs `/data2`; an absolute symlink into another root; a
DIVERGENT audit; a mountpoint containing `\015`). N7.

Shared-harness regression check: `debian_install_v2/tests/test_vm_test_boundary.py` (scratch copy of the commit tree,
`--cli-case-partial`): **21 passed**. But the commit adds NO test for `MDT_VM_PROJECT_DIR` itself (validation `case` at
run-vm-harness.sh:82-86, fingerprint at 218-222, lazy `resolve_vm_lab` at 203-209). I read them: logic is correct
(`/r/scripts/x` accepted; `/r/scripts`, `/r/scripts/a/b`, non-scripts rejected; unset leaves fingerprint, names and mounts
unchanged). S4: add 3 cases to `test_vm_test_boundary.py` since that file is the harness's guard.

### VM lane shell review
- `bash -n`: ok for `run-vm-tests.sh`, `setup-vm-lab.sh`, `run-vm-harness.sh`. `shellcheck -x`: only SC2016 (info) at
  `run-vm-tests.sh:71` (single-quoted guest script, intentional). Clean otherwise.
- HOST safety: no `losetup`, `mkfs`, `mount`, `swapon`, `qemu`, `truncate` anywhere in `testing/vm/*.sh` or in the harness diff. The only
  loop-device/mkfs/mount commands are in `tests/test_mounttrace.py` `BTRFS_SETUP` (line ~421) and run only through `run_root()`
  after `vm_root_unavailable()`, i.e. inside the guest. The unprivileged scenarios use `unshare -rm` (private mount namespace),
  which does not touch the host table (verified: the run left no mounts).
- Root requirement: the lane needs no host root (QEMU/TCG in the governed runner container); `setup-vm-lab.sh` only does
  `mkdir -p`/`chmod 0700`/`ln -s` under `$MDT_COW_ROOT` after verifying it exists and is btrfs, never creates the root, refuses a
  non-empty real dir at the link path, idempotent.
- Cleanup: `flock -n` per-checkout lock (line ~50); EXIT traps destroy the prep VM (`build_base`) and the test VM unless `--keep`;
  each round on a throw-away overlay of the sealed base; test side `run_root` removes containers by exact name
  (`docker rm -f mounttrace-test-<pid>-<x>`, in line with the operator's docker rule), unmounts the temp base, rmtree.
  Residual: a SIGKILLed scenario leaks mounts/containers only inside the guest, which is destroyed anyway.
- **S1 (should-fix) the VM guard is weaker than documented.** `tests/test_mounttrace.py:93-104` accepts any `systemd-detect-virt --vm`
  of `qemu|kvm` plus the env var `MOUNTTRACE_VM=1` plus euid 0, and the guide (9.6) calls this "refuse to run anywhere but in that
  guest". Every KVM guest passes it, including the estate's own netcup hosts (<host>/<host> are KVM VPSs) and the cockpit's host if it
  is virtualised. Running `sudo MOUNTTRACE_VM=1 python3 tests/test_mounttrace.py` there would attach a loop device, mkfs a btrfs image,
  mount in the host namespace (`host_ns=True` Docker scenarios) and `docker run` on the live daemon. The env var makes it opt-in, so not
  blocking, but given the 2026-09-09 loop-device incident make the proof real: have the sealed base carry a marker that only exists in the
  test guest (e.g. `run-vm-tests.sh` build step writes `/etc/mounttrace-test-vm` containing a nonce, and `vm_root_unavailable()` requires it
  in addition), and reword the guide to say what is actually checked. Also consider not honouring `MOUNTTRACE_VM` from the ambient environment
  (the lane passes it explicitly via `sudo env`).

## (4) Repo hygiene

- `git ls-files scripts/mounttrace` = exactly the 6 files promised; working tree has the same 6 (no silently ignored files; no `core/`
  directory in the project, so the root `.gitignore` `core` trap is not involved). `artifacts/` is ignored by `**/artifacts/`
  (`.gitignore:381`), which is intended: `artifacts/vm-lab` is created by `setup-vm-lab.sh`. Note `scripts/debian-install-v2/artifacts/vm-lab/{.gitignore,README.md}`
  placeholders are tracked there; mounttrace deliberately has none (the harness `die`s with "run setup first" via `run-vm-tests.sh:46`).
  Harness comment at `run-vm-harness.sh:352-354` ("tracked placeholder folders") is now only true for the default project (nit N8).
- Modes: `mounttrace.py`, `tests/test_mounttrace.py`, both shell scripts are 100755; docs 100644. Fine (test file executable is harmless).
- No generated artifacts, no binaries (largest file `mounttrace.md`, 45 KB), no credentials (grep for password/token/secret/key/ghp_/AKIA: none).
- `scripts/README.md` does not list the new project (nit N9, optional).

## (5) Docs links
All relative links resolve: `mounttrace.py`, `tests/test_mounttrace.py`, `../<private-host>-guide/{BTRFS-ROLLOUT.md,SOULMASK-TMPFS.md,files/usr/local/sbin/soulmask_tmpfs-toggle.sh}`.
`mdt-cockpit/docs/BTRFS-COW.md` exists with sections 5 ("Contract for tools") and 6.2 ("Worktree scratch (ciu)") as cited. Guide section
numbers cited by the README (9.6/9.7) exist. README says "`artifacts/vm-lab` is an ignored symlink": correct.

## Summary of follow-ups (none blocking)
- S1 VM guard: require a guest-only marker, fix the guide wording (`tests/test_mounttrace.py:93-104`, guide 9.6).
- S2 Sanitize control characters in all output (`mounttrace.py:1054-1057`); add a test.
- S3 Register a lane: add `scripts/mounttrace/run-gate.toml` for the unprivileged suite (needs a check that `unshare -rm` works in the
  tester-unified container; if not, document it as VM-lane-only) or state explicitly in the README that no gate covers it.
- S4 Add `MDT_VM_PROJECT_DIR` cases to `debian_install_v2/tests/test_vm_test_boundary.py`.
- Nits N1 VERSION file/CHANGES, N2 trailer, N3 guide note on verbatim options output, N4 `ProcessLookupError` in `--watch`,
  N5 autofs/hung-mount note in Limits, N6 `--no-resolve` in state audit, N7 four cheap unit tests for surviving mutants,
  N8 harness comment, N9 `scripts/README.md` entry.
