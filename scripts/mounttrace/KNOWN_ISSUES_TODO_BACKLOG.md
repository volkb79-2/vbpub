# mounttrace — known issues, TODO, and backlog

Created 2026-11-08 from the independent review of `46d14e8ca` (mounttrace 3.2.0 analyser, guide, VM test lane;
`/home/vscode/vbpub-release-program-20261027/reports/mounttrace-REVIEW.md`, verdict MERGE, no blocker). IDs are `MT-NN`,
allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`mounttrace.md`](mounttrace.md) (the guide); the tool is [`mounttrace.py`](mounttrace.py).
A FIXED entry means code, tests, and docs landed together.

## Status

| ID | Summary | Priority | Status |
|---|---|---|---|
| MT-01 | Output prints mount paths and symlink targets raw: a tenant-controlled mount name can inject terminal escape sequences | Should-fix | FIXED (`3839b97fb`) |
| MT-02 | The VM-lane guard (`systemd-detect-virt --vm` + `MOUNTTRACE_VM=1` + euid 0) accepts any KVM guest, not only the test guest; guide 9.6 overstates it | Should-fix | FIXED (`3839b97fb`) |
| MT-03 | No gate lane covers the 547-line suite; it runs only by hand or from the VM script | Should-fix | FIXED (`592b163d1`, documented fallback) |
| MT-04 | The shared harness's `MDT_VM_PROJECT_DIR` handling has no test in `test_vm_test_boundary.py` | Should-fix | FIXED (`592b163d1`) |
| MT-05 | Four surviving hand mutants show unit-level test gaps (`inside()` boundary, absolute-symlink restart, DIVERGENT alert, `\r` in a path) | Minor | FIXED (`3839b97fb`) |
| MT-06 | Guide section 8/9.1 omits that path probing can trigger autofs or block on a hung NFS/FUSE mount, and that mount options print verbatim | Minor | FIXED (`592b163d1`) |
| MT-07 | Review nits: VERSION/CHANGES, `--watch` ESRCH text, `--no-resolve` in the state audit, harness comment, `scripts/README.md`, Python 3.9 unverified, commit trailer | Nit | FIXED (`3839b97fb`, `592b163d1`) |

---

## MT-01 — terminal-escape injection through mount paths and symlink targets

**Priority:** should-fix. Source: `reports/mounttrace-REVIEW.md` S2.

**Finding.** `main()` sets only `errors='backslashreplace'` (`mounttrace.py:1054-1057`), which handles surrogates, not control characters.
mountinfo octal-escapes ESC/BEL, but `unescape()` (`mounttrace.py:71`) turns them back into raw bytes and every `print` emits them. Reproduced: a mount
named `evil<ESC>]0;PWNED<BEL>x` created inside a user namespace came out of `mounttrace --submounts` byte for byte. The tool is meant to run as root on a host
against game containers whose tenants control mount names (with CAP_SYS_ADMIN) and symlink targets (the `resolved -> ...` line).

**Fix.** Sanitize in one place: in `main()` wrap `sys.stdout`/`sys.stderr` so `[\x00-\x08\x0b-\x1f\x7f-\x9f]` becomes `\xNN` (keep `\n` and `\t`), or pass each
printed path through a `safe()` helper. Add a unit test with an ESC in a mountpoint name and in a symlink target.

**Close when.** The test passes and `od -c` on the output of the reproduction shows no byte below `0x20` other than newline and tab.

**Status: FIXED in `3839b97fb`.** A shared `safe_text()` renderer escapes C0/C1 controls at all printed values and the stdout/stderr boundary preserves only report layout newlines/tabs. The topology integration test injects ESC, BEL and newline into mount paths, symlink targets, and state-file output, checks report/audit/compare/predict/submount/hidden sites, and asserts there are no raw controls except layout LF. The option rendering test also checks unchanged parsed option data.

## MT-02 — the VM guard accepts any KVM guest

**Priority:** should-fix. Source: `reports/mounttrace-REVIEW.md` S1.

**Finding.** `tests/test_mounttrace.py:93-104` (`vm_root_unavailable()`) accepts a `systemd-detect-virt --vm` answer of `qemu` or `kvm`, plus the environment variable
`MOUNTTRACE_VM=1`, plus euid 0. Every KVM guest passes, including the estate's own netcup hosts (KVM VPSs). `sudo MOUNTTRACE_VM=1 python3 tests/test_mounttrace.py` on such
a host would attach a loop device, mkfs a btrfs image, mount in the host namespace (`host_ns=True` Docker scenarios) and `docker run` on the live daemon. The guide
(9.6) says the root tests "refuse to run anywhere but in that guest", which is not what is checked. The variable makes it opt-in, so it is not blocking, but the
2026-09-09 loop-device incident makes a real proof worth having.

**Fix.** The sealed base carries a marker that exists only in the test guest (the `run-vm-tests.sh` build step writes `/etc/mounttrace-test-vm` with a nonce) and
`vm_root_unavailable()` requires it in addition. Reword guide 9.6 to state what is checked. Consider not honouring `MOUNTTRACE_VM` from the ambient environment (the lane
passes it explicitly through `sudo env`).

**Close when.** On a KVM guest without the marker the root tests skip with a reason naming the marker, a test covers that refusal, and guide 9.6 matches the code.

**Status: FIXED in `3839b97fb`.** The sealed `mounttrace-ready-v2` base now carries a nonempty nonce marker, the guard refuses a KVM guest without it with a marker-specific reason, the refusal is tested, and guide §9.6 describes all required checks.

## MT-03 — no gate lane registered for the project

**Priority:** should-fix. Source: `reports/mounttrace-REVIEW.md` S3.

**Finding.** There is no `scripts/mounttrace/run-gate.toml`, no `assay.toml`, and the project is in neither `run-gate.root.toml` nor `cmru.orchestration.toml`. Siblings with a
lane: `scripts/{damon-analysis,debian-install-v2,gstammtisch-guide,netcup,telegram,truenas}/run-gate.toml`. The 547-line suite (`tests/test_mounttrace.py`, 18 unprivileged
tests that passed in the devcontainer) is reachable only by hand or by `testing/vm/run-vm-tests.sh`.

**Fix.** Add `scripts/mounttrace/run-gate.toml` for the unprivileged suite after checking that `unshare -rm` works in the tester-unified container. If it does not,
state in the README that the suite is VM-lane-only and that no gate covers it.

**Close when.** Either a registered lane runs the unprivileged tests and passes, or the README says explicitly that no gate covers the project.

**Status: FIXED in `592b163d1` by the documented fallback.** The registered `tester-unified` attempt under the shared flock failed at its namespace preflight with `unshare: Operation not permitted`; the unusable lane declaration was removed, and the README and guide explicitly state that the suite is VM-only and no Run-Gate lane covers it.

## MT-04 — `MDT_VM_PROJECT_DIR` has no test in the harness guard file

**Priority:** should-fix. Source: `reports/mounttrace-REVIEW.md` S4.

**Finding.** Commit `46d14e8ca` adds `MDT_VM_PROJECT_DIR` to the shared harness `scripts/debian-install-v2/testing/vm/run-vm-harness.sh` (validation `case` at lines 82-86,
fingerprint at 218-222, lazy `resolve_vm_lab` at 203-209). The logic reads correct (`/r/scripts/x` accepted; `/r/scripts`, `/r/scripts/a/b`, non-`scripts/` paths rejected; unset
leaves fingerprint, names and mounts unchanged), but the guard file `scripts/debian-install-v2/debian_install_v2/tests/test_vm_test_boundary.py` (21 tests, all pass) has no
case for it, and the debian-install-v2 gate depends on that harness.

**Fix.** Add three cases to `test_vm_test_boundary.py`: accepted path, rejected path shapes, and unset leaving the default fingerprint unchanged.

**Close when.** The three cases pass and a hand mutation of the `case` pattern fails one of them.

**Status: FIXED in `592b163d1`.** Tests cover accepted `scripts/mounttrace`, rejected path shapes, and the unset default fingerprint/mounts; changing the guard's `*/*` pattern to `*` caused the accepted-path test to fail.

## MT-05 — unit-level test gaps from surviving mutants

**Priority:** minor. Source: `reports/mounttrace-REVIEW.md` section 3 (N7).

**Finding.** Of 14 hand mutants, 8 were killed and 6 survived. Four are cheap unit-level gaps: M3 `inside()` prefix boundary (`mounttrace.py:184`, bare `startswith` instead of
`prefix.rstrip('/') + '/'`), M4 `canonicalize` absolute symlink no longer restarts at the root (line 242), M7 DIVERGENT alert removed (line 760), M10 `newline='\n'` replaced by universal
newlines (line 173, a `\r` in a path). M11 (`BTRFS_EMPTY_SUBVOL_DIR_OBJECTID`, line 64) and M12 (`BTRFS_ROOT_SUBVOL_RDONLY`, line 62) are covered only by the VM-lane tests, by design.

**Fix.** Add: sibling `/data` vs `/data2` in a topology test; an absolute symlink into another root; a DIVERGENT audit; a mountpoint containing `\015`.

**Close when.** M3, M4, M7 and M10 re-applied one at a time each fail the suite.

**Status: FIXED in `3839b97fb`.** The sibling-prefix, absolute-symlink, divergent-audit, and CR mount-path tests were added. Each named mutant was reapplied alone and killed by the focused mounttrace test file.

## MT-06 — guide note on autofs, hung mounts and verbatim options

**Priority:** minor. Source: `reports/mounttrace-REVIEW.md` N5 and N3.

**Finding.** Path probing is `stat`/`lstat`/`open(dir)` of the target path (`canonicalize`, `stat_identity`, `btrfs_subvol_info`, `btrfs_boundaries`). On an autofs trigger this can cause
an automount and on a hung NFS/FUSE mount it can block. Guide section 8 (Limits) and 9.1 say "read-only", which is true for the mount table but not for autofs. Separately, mount sources and
superblock options print verbatim (`desc(..., with_opts=True)`, `mounttrace.py:577`): overlay prints the full lowerdir/upperdir list, CIFS/NFS can show user names and server addresses.

**Fix.** One sentence each in the guide: the autofs/hung-mount caveat in section 8 and 9.1, and the verbatim-options note (inherent to a diagnostic tool).

**Close when.** Both sentences are in `mounttrace.md`.

**Status: FIXED in `592b163d1`.** Guide §8 and §9.1 now explain autofs triggers, potentially blocking NFS/FUSE probes, and verbatim mount sources/options.

## MT-07 — review nits

**Priority:** nit. Source: `reports/mounttrace-REVIEW.md` N1, N2, N4, N6, N8, N9 and the Python claim.

1. N1: `3.2.0` exists only as `VERSION` in `mounttrace.py:57`; siblings (`netcup`, `truenas`) carry a `VERSION` file and CHANGES. Add them or document the single source.
2. N2: the commit trailer is `Co-Authored-By: Claude Opus 5.5`; the estate convention is the Sonnet trailer. Not worth rewriting a merged commit; use the convention from here on.
3. N4: the `--watch` loop catches only `FileNotFoundError` for a vanished PID (line 1088); a just-dead process can raise `ProcessLookupError` (ESRCH), giving `mounttrace: [Errno 3] No such process`. Catch both.
4. N6: `audit_state_file` canonicalizes state-file targets with symlink resolution even under `--no-resolve` (line 817, `canonicalize(t, None)`), unlike the other paths.
5. N8: the harness comment at `scripts/debian-install-v2/testing/vm/run-vm-harness.sh:352-354` ("tracked placeholder folders") is now true only for the default project.
6. N9: `scripts/README.md` does not list the project.
7. The `Python >= 3.9` claim has no 3.10+ syntax by grep but was not run on a 3.9 interpreter.

**Close when.** Each item is fixed or struck out with a reason.

**Status: FIXED in `3839b97fb` and `592b163d1`.** `VERSION` remains the sole version source because mounttrace has no separate release/CHANGES convention; no patch bump was indicated. The watch loop handles ESRCH, state audit honors `--no-resolve`, the shared harness comment and scripts index are corrected, and both commits carry the Codex trailer. The runtime `Python >= 3.9` claim was narrowed to source grammar: Python 3.9 grammar was checked, the available gate runtime is 3.14, and 3.9 runtime compatibility is explicitly unverified rather than claimed.
