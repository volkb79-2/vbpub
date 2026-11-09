# mounttrace — known issues, TODO, and backlog

Created 2026-11-08 from the independent review of `46d14e8ca` (mounttrace 3.2.0 analyser, guide, VM test lane;
`/home/vscode/vbpub-release-program-20261027/reports/mounttrace-REVIEW.md`, verdict MERGE, no blocker). IDs are `MT-NN`,
allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`mounttrace.md`](mounttrace.md) (the guide); the tool is [`mounttrace.py`](mounttrace.py).
A FIXED entry means code, tests, and docs landed together.

## Status

| ID | Summary | Priority | Status |
|---|---|---|---|
| MT-01 | Output prints mount paths and symlink targets raw: a tenant-controlled mount name can inject terminal escape sequences | Should-fix | OPEN |
| MT-02 | The VM-lane guard (`systemd-detect-virt --vm` + `MOUNTTRACE_VM=1` + euid 0) accepts any KVM guest, not only the test guest; guide 9.6 overstates it | Should-fix | OPEN |
| MT-03 | No gate lane covers the 547-line suite; it runs only by hand or from the VM script | Should-fix | OPEN |
| MT-04 | The shared harness's `MDT_VM_PROJECT_DIR` handling has no test in `test_vm_test_boundary.py` | Should-fix | OPEN |
| MT-05 | Four surviving hand mutants show unit-level test gaps (`inside()` boundary, absolute-symlink restart, DIVERGENT alert, `\r` in a path) | Minor | OPEN |
| MT-06 | Guide section 8/9.1 omits that path probing can trigger autofs or block on a hung NFS/FUSE mount, and that mount options print verbatim | Minor | OPEN |
| MT-07 | Review nits: VERSION/CHANGES, `--watch` ESRCH text, `--no-resolve` in the state audit, harness comment, `scripts/README.md`, Python 3.9 unverified, commit trailer | Nit | OPEN |
| MT-08 | CLI restructuring (P1 a): subcommands explain/compare/audit/predict/soulmask, one `[PID:]PATH` syntax, `--no-probe`, Report object and `--json` | Should-fix | OPEN |
| MT-09 | Human rendering (P1 b): 120-column rules, one mount table, collapsed noise, golden tests at COLUMNS=120/80/non-TTY, guide update | Should-fix | OPEN |
| MT-10 | P2: `--watch` change line, clear errors, `soulmask` bundles the volumes-root audit | Minor | OPEN |

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

## MT-03 — no gate lane registered for the project

**Priority:** should-fix. Source: `reports/mounttrace-REVIEW.md` S3.

**Finding.** There is no `scripts/mounttrace/run-gate.toml`, no `assay.toml`, and the project is in neither `run-gate.root.toml` nor `cmru.orchestration.toml`. Siblings with a
lane: `scripts/{damon-analysis,debian-install-v2,gstammtisch-guide,netcup,telegram,truenas}/run-gate.toml`. The 547-line suite (`tests/test_mounttrace.py`, 18 unprivileged
tests that passed in the devcontainer) is reachable only by hand or by `testing/vm/run-vm-tests.sh`.

**Fix.** Add `scripts/mounttrace/run-gate.toml` for the unprivileged suite after checking that `unshare -rm` works in the tester-unified container. If it does not,
state in the README that the suite is VM-lane-only and that no gate covers it.

**Close when.** Either a registered lane runs the unprivileged tests and passes, or the README says explicitly that no gate covers the project.

## MT-04 — `MDT_VM_PROJECT_DIR` has no test in the harness guard file

**Priority:** should-fix. Source: `reports/mounttrace-REVIEW.md` S4.

**Finding.** Commit `46d14e8ca` adds `MDT_VM_PROJECT_DIR` to the shared harness `scripts/debian-install-v2/testing/vm/run-vm-harness.sh` (validation `case` at lines 82-86,
fingerprint at 218-222, lazy `resolve_vm_lab` at 203-209). The logic reads correct (`/r/scripts/x` accepted; `/r/scripts`, `/r/scripts/a/b`, non-`scripts/` paths rejected; unset
leaves fingerprint, names and mounts unchanged), but the guard file `scripts/debian-install-v2/debian_install_v2/tests/test_vm_test_boundary.py` (21 tests, all pass) has no
case for it, and the debian-install-v2 gate depends on that harness.

**Fix.** Add three cases to `test_vm_test_boundary.py`: accepted path, rejected path shapes, and unset leaving the default fingerprint unchanged.

**Close when.** The three cases pass and a hand mutation of the `case` pattern fails one of them.

## MT-05 — unit-level test gaps from surviving mutants

**Priority:** minor. Source: `reports/mounttrace-REVIEW.md` section 3 (N7).

**Finding.** Of 14 hand mutants, 8 were killed and 6 survived. Four are cheap unit-level gaps: M3 `inside()` prefix boundary (`mounttrace.py:184`, bare `startswith` instead of
`prefix.rstrip('/') + '/'`), M4 `canonicalize` absolute symlink no longer restarts at the root (line 242), M7 DIVERGENT alert removed (line 760), M10 `newline='\n'` replaced by universal
newlines (line 173, a `\r` in a path). M11 (`BTRFS_EMPTY_SUBVOL_DIR_OBJECTID`, line 64) and M12 (`BTRFS_ROOT_SUBVOL_RDONLY`, line 62) are covered only by the VM-lane tests, by design.

**Fix.** Add: sibling `/data` vs `/data2` in a topology test; an absolute symlink into another root; a DIVERGENT audit; a mountpoint containing `\015`.

**Close when.** M3, M4, M7 and M10 re-applied one at a time each fail the suite.

## MT-06 — guide note on autofs, hung mounts and verbatim options

**Priority:** minor. Source: `reports/mounttrace-REVIEW.md` N5 and N3.

**Finding.** Path probing is `stat`/`lstat`/`open(dir)` of the target path (`canonicalize`, `stat_identity`, `btrfs_subvol_info`, `btrfs_boundaries`). On an autofs trigger this can cause
an automount and on a hung NFS/FUSE mount it can block. Guide section 8 (Limits) and 9.1 say "read-only", which is true for the mount table but not for autofs. Separately, mount sources and
superblock options print verbatim (`desc(..., with_opts=True)`, `mounttrace.py:577`): overlay prints the full lowerdir/upperdir list, CIFS/NFS can show user names and server addresses.

**Fix.** One sentence each in the guide: the autofs/hung-mount caveat in section 8 and 9.1, and the verbatim-options note (inherent to a diagnostic tool).

**Close when.** Both sentences are in `mounttrace.md`.

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

## MT-08 — CLI restructuring (P1 a): subcommands, one `[PID:]PATH` syntax, `--no-probe`, a `Report` object and `--json`

**Priority:** should-fix (umbrella; land after MT-01..MT-07 are merged, same file). Filed 2026-11-09, status OPEN. Source: `/home/vscode/vbpub-release-program-20261027/reports/mounttrace-output-cli-ANALYSIS.md` (Headline, F1, F8, "CLI proposal", "Prioritized change list" items 1-2).

**Finding.** All line numbers below are from origin/main `2f7f386cc`; re-resolve them after the `mt-backlog` merge. `PATH` is a required argument, so every audit, prediction and state check first prints a full report on a path nobody asked about (20-50 lines before the answer; one overlay line is 2,543 characters). The seven options that combine into modes (`--pid`, `--host-path`, `--peer`, `--audit-prefix H[=C]`, `--predict`, `--state-file`, `--volumes-root`, plus `--all-hidden`, which equals `PATH=/`) hide the real set of questions. `--no-resolve` only skips `canonicalize()`; the stat and btrfs probes still touch the path, so a hung autofs/NFS mount is not protected (MT-06). Human output is built from strings directly (`Findings` at `mounttrace.py:559-568`, `desc()` 571-578), so tests assert wording and there is no machine interface.

**Fix.** One path syntax everywhere, `[PID:]PATH` (plain absolute path = the invoking process's view, called `host`; `PID:PATH` = inside that process's root and mount namespace), and five subcommands: `explain [PID:]PATH [--submounts]`, `compare REF OTHER...`, `audit PREFIX PID:PREFIX`, `predict PATH PID...`, `soulmask PID... [--state-file F] [--volumes-root D]`. Global options `--json` (NDJSON per pass under `--watch`), `-v`, `--watch SECONDS`, `--no-probe` (the rename of `--no-resolve`: read only `/proc/*/mountinfo`, no stat/lstat/ioctl; gates `canonicalize` 203, `stat_identity` 250, `btrfs_subvol_info` 266, `btrfs_boundaries` 314), `--version`. Dropped without aliases (operator rule): `--all-hidden`, `--host-path`, `--peer`. Collect output in a `Report` object (verdict, finding(level, cls, text, next), kv, table; from `Findings`) that renders in the order header, verdict, findings, tables and that `--json` dumps unchanged. Exit codes stay 0/3/1/2. Tests move to `--json` field assertions plus one JSON key-snapshot test (about 45 assertions at `tests/test_mounttrace.py:188-543` match human wording today). Cheaper alternative (size S, not recommended): make `PATH` optional only.

**Open decisions (operator veto pending; defaults taken).** (1) `compare` exits 3 when the objects are DIFFERENT (changes behavior; `explain` and `predict` never exit 3; `--watch` Ctrl-C returns the last pass's status). (2) `host` is the wrong word inside a container; accepted, the header shows the invoking process's namespace. (3) Already answered by the `mt-backlog` branch: MT-01 landed as the shared `safe_text()` (`mounttrace.py:63` there) plus a `TerminalSafeStream` wrapper around `sys.stdout` and `sys.stderr` (`:79-89`, wrapped in `main()` at `:1068-1072`), not as a per-cell `safe()` helper; MT-09 must therefore apply `safe_text()` per cell BEFORE measuring widths (the stream wrapper only escapes at write time, after layout) and keep the stream wrapper as the backstop.

**Close when.** The five subcommands and `[PID:]PATH` replace the old option modes, `--no-probe` reads only mountinfo (a test patches `os.stat`/`os.lstat`/`ioctl` to fail), `--json` carries the same Report, the tests assert fields rather than wording, and the guide sections 0, 9.2-9.5 and 10.3 mapping (B `soulmask $wings_pid`, C `audit ...`, D `compare ...`, E `predict ...`, F `--watch 1 soulmask ...`) are updated in the same change.

## MT-09 — human rendering (P1 b): 120-column rules, one mount table, collapsed noise, golden tests, guide update

**Priority:** should-fix (umbrella; depends on MT-08). Filed 2026-11-09, status OPEN. Source: `reports/mounttrace-output-cli-ANALYSIS.md` (F2-F7, "120-column rules", mock-ups, change-list items 3-5, Tests).

**Finding.** No output fits 120 columns (`plain path`: widest 166; `/ --submounts`: 2543; `--audit-prefix`: 293; `--state-file`: 360). There is no verdict line (a healthy run says nothing, the EROFS fact is a NOTE on line 13 with exit 0), one mount is one line of 8-10 fields reused everywhere, long shared prefixes repeat in every row, vocabulary differs (`INVOKING PROCESS`/`HOST`/`invoking view`, `[rw]`/`rw`/`RO(mount+superblock)`, propagation spelled three ways, Python `False` leaks as `same object: False`), and ALERT lines are indented (`Findings.alert`, line 563) so `grep '^ALERT'` finds nothing although guide 9.3 says they start with `ALERT:`.

**Fix.** Implement the ten rules of the analysis: width W = `$COLUMNS`, else the TTY width, else 120, below 80 laid out as 80, `-v` disables truncation, JSON never truncated; first two lines state what was asked and the answer; two levels only (ALERT and NOTE) at column 0 with one `next` line per ALERT class; one mount table `MOUNTPOINT ID FSTYPE SOURCE FSROOT RO PROPAGATION` (SOURCE/FSROOT shrink first, ID/FSTYPE/RO/propagation never truncated); paths cut in the middle keeping the end, row keys never truncated, audit paths relative to the prefix; collapsed by default and expanded by `-v`: overlay `lowerdir` (`layers=N` plus a writable-layer NOTE), full superblock options, parent chain, empty sections (one line `hidden: none on or below this path`), OK audit rows (count), views that receive nothing; one vocabulary in human and JSON (views `host` / `pid N`; `private|shared(N)|slave(N)|shared(N)+slave(M)|from(N)|unbindable`; RO `rw|ro|ro:sb`; `same`/`different`; `linked|NOT LINKED|tucked`; non-alerting class `LAYER`); user-controlled strings escaped by the single per-cell helper before measuring; no colour. Replace `desc()` with one table renderer; keep the logic of `Mount.propagation` 107-118, `ro_label` 132-140, `btrfs_label` 303-311, `hidden_reason` 422-433, `place_event` 487-507, `link_status` 510-552 and return structured fields.

**Tests.** 6-8 golden tests from synthetic View fixtures (style at `tests/test_mounttrace.py:205-218`) with `--no-probe` at COLUMNS=120 and 80 plus a non-TTY run (must give 120); one generic check that every user-namespace scenario's output stays <= COLUMNS; one VM-lane run at the end. Verify on a real Wings tree on r1002/v1001 that middle-cut paths do not hide the only difference between `volumes/<uuid>/WS/Saved/Logs` paths.

**Close when.** Every default line is <= W in all golden tests, the guide (sections 0, 9, 9.3's ALERT claim, 10.3) is updated in the same commit, and the VM lane passes once.

## MT-10 — P2 follow-ups: `--watch` change line, clear errors, `soulmask` bundles the volumes-root audit

**Priority:** minor (umbrella; after MT-08). Filed 2026-11-09, status OPEN. Source: `reports/mounttrace-output-cli-ANALYSIS.md` (P2, F7, open question 6).

**Finding and fix.** (1) `--watch` reprints everything with only a banner: print `--- <time> mount tables changed: <view> +#id ... -#id ...` before each report and return the last pass's status on Ctrl-C (`main` 1083-1096). (2) A missing PID gives a raw `[Errno 2]` / `[Errno 3]`, and the `--watch` loop catches only `FileNotFoundError` (covers MT-07 item 3): clear errors for a vanished PID and for permission (`main` 1074-1101). (3) `soulmask` bundles the volumes-root audit with the state-file audit and defaults the state file to `/run/soulmask_tmpfs.state`.

**Open decision (default taken).** Should `soulmask` bundle the audit or stay a pure state audit chained as `audit ... && soulmask ...`? Default: bundle; the operator may veto before implementation.

**Close when.** The change line, the error texts and the bundled `soulmask` have tests, and the guide's `--watch` and soulmask sections match.
