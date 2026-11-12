<!-- Evidence scrub 2026-11-10: environment-specific host identities, addresses, controller paths, and key material were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
# mounttrace — known issues, TODO, and backlog

Created 2026-11-08 from the independent review of `46d14e8ca` (mounttrace 3.2.0 analyser, guide, VM test lane;
[mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md), verdict MERGE, no blocker). IDs are `MT-NN`,
allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`mounttrace.md`](mounttrace.md) (the guide); the
supported command is the installed wheel entry point, with the stdlib-only core
in [`src/mounttrace_core.py`](src/mounttrace_core.py). A FIXED entry means code,
tests, and docs landed together.

## Status

| ID | Summary | Priority | Status |
|---|---|---|---|
| MT-01 | Output prints mount paths and symlink targets raw: a tenant-controlled mount name can inject terminal escape sequences | Should-fix | FIXED (`round 2: direct output-site tests; all 11 review mutants killed`) |
| MT-02 | The VM-lane guard (`systemd-detect-virt --vm` + `MOUNTTRACE_VM=1` + euid 0) accepts any KVM guest, not only the test guest; guide 9.6 overstates it | Should-fix | FIXED (`round 2: every refusal branch and acceptance tested`) |
| MT-03 | No namespace-free gate lane covers the tests that can run without user namespaces | Should-fix | FIXED (`scripts/mounttrace/run-gate.toml`, `suite` lane) |
| MT-04 | The shared harness's `MDT_VM_PROJECT_DIR` handling has no test in `test_vm_test_boundary.py` | Should-fix | FIXED (`592b163d1`) |
| MT-05 | Four surviving hand mutants show unit-level test gaps (`inside()` boundary, absolute-symlink restart, DIVERGENT alert, `\r` in a path) | Minor | FIXED (`3839b97fb`) |
| MT-06 | Guide section 8/9.1 omits that path probing can trigger autofs or block on a hung NFS/FUSE mount, and that mount options print verbatim | Minor | FIXED (`592b163d1`) |
| MT-07 | Review nits: VERSION/CHANGES, `--watch` ESRCH text, `--no-resolve` in the state audit, harness comment, `scripts/README.md`, Python 3.9 unverified, commit trailer | Nit | FIXED (`3839b97fb`, `592b163d1`) |
| MT-08 | CLI restructuring (P1 a): subcommands explain/compare/audit/predict/soulmask, `[PID:]PATH` for view-selecting operands, `--no-probe`, Report object and `--json` | Should-fix | FIXED (`c0c0302f0`, tests `6794cf18f`, review fixes `ae5bfafa3`) |
| MT-09 | Human rendering (P1 b): 120-column rules, one mount table, collapsed noise, golden tests at COLUMNS=120/80/non-TTY, guide update | Should-fix | ACCEPTED (`04a224fab`; VM lane 67+67 PASS in 536 s; [live report](docs/evidence/mt-close-vm-LIVE-REPORT.md)) |
| MT-10 | P2: `--watch` change line, clear errors, `soulmask` bundles the volumes-root audit | Minor | FIXED (`c0c0302f0`, tests `6794cf18f`, review fixes `ae5bfafa3`) |
| MT-11 | Live-acceptance fixes and the second-review error-matrix/backlog-citation nits | Should-fix (acceptance); minor nits | FIXED / ACCEPTED (`04a224fab`; VM lane 67+67 PASS with 5 expected unprivileged skips and 0 root skips; Wings live check used the documented stand-in) |
| MT-12 | Optional P3 output/input proposals from the CLI analysis | Optional | CLOSED BY DESIGN (`bff533daf`; analysis says P3 is “leaning no”) |
| MT-13 | Adopt cli-extended for the CLI layer without adding third-party imports or subprocesses to the analysis core | Should-fix | FIXED (branch `mt-clix-20261110`; 41 reviewed cases; conformance audit and suite gate evidence in [mt-clix-REPORT.md](docs/evidence/mt-clix-REPORT.md)) |

---

## MT-01 — terminal-escape injection through mount paths and symlink targets

**Priority:** should-fix. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) S2.

**Finding.** `main()` sets only `errors='backslashreplace'` (`mounttrace.py:1054-1057`), which handles surrogates, not control characters.
mountinfo octal-escapes ESC/BEL, but `unescape()` (`mounttrace.py:71`) turns them back into raw bytes and every `print` emits them. Reproduced: a mount
named `evil<ESC>]0;PWNED<BEL>x` created inside a user namespace came out of `mounttrace --submounts` byte for byte. The tool is meant to run as root on a host
against game containers whose tenants control mount names (with CAP_SYS_ADMIN) and symlink targets (the `resolved -> ...` line).

**Fix.** Sanitize in one place: in `main()` wrap `sys.stdout`/`sys.stderr` so `[\x00-\x08\x0b-\x1f\x7f-\x9f]` becomes `\xNN` (keep `\n` and `\t`), or pass each
printed path through a `safe()` helper. Add a unit test with an ESC in a mountpoint name and in a symlink target.

**Close when.** The test passes and `od -c` on the output of the reproduction shows no byte below `0x20` other than newline and tab.

**Round-2 status: FIXED.** Direct `safe_text()` checks now pin C0/C1, DEL, line-feed/tab behavior, ordinary Unicode, and bidi/format characters; a `StringIO` test pins `TerminalSafeStream`. Namespace-free tests cover banner/resolved paths, alert and `PI_ERROR`, prediction paths, CLI error paths, both stream wrappers, and vanished-PID output. The mount-name integration case includes DEL and U+0085 and checks that injected fragments never begin an output line. All 11 review mutants were applied individually and killed.

## MT-02 — the VM guard accepts any KVM guest

**Priority:** should-fix. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) S1.

**Finding.** `tests/test_mounttrace.py:93-104` (`vm_root_unavailable()`) accepts a `systemd-detect-virt --vm` answer of `qemu` or `kvm`, plus the environment variable
`MOUNTTRACE_VM=1`, plus euid 0. Every KVM guest passes, including the estate's own netcup hosts (KVM VPSs). `sudo MOUNTTRACE_VM=1 python3 tests/test_mounttrace.py` on such
a host would attach a loop device, mkfs a btrfs image, mount in the host namespace (`host_ns=True` Docker scenarios) and `docker run` on the live daemon. The guide
(9.6) says the root tests "refuse to run anywhere but in that guest", which is not what is checked. The variable makes it opt-in, so it is not blocking, but the
2026-09-09 loop-device incident makes a real proof worth having.

**Fix.** The sealed base carries a marker that exists only in the test guest (the `run-vm-tests.sh` build step writes `/etc/mounttrace-test-vm` with a nonce) and
`vm_root_unavailable()` requires it in addition. Reword guide 9.6 to state what is checked. Consider not honouring `MOUNTTRACE_VM` from the ambient environment (the lane
passes it explicitly through `sudo env`).

**Close when.** On a KVM guest without the marker the root tests skip with a reason naming the marker, a test covers that refusal, and guide 9.6 matches the code.

**Round-2 status: FIXED.** The sealed `mounttrace-ready-v2` base carries a nonempty nonce marker. A table-driven test covers non-root, missing virt tool, wrong virt type, missing/empty/non-ASCII marker, missing opt-in, and the accepting case. The marker is documented as a presence check, not authenticated guest identity.

## MT-03 — no gate lane registered for the project

**Priority:** should-fix. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) S3.

**Finding.** There was no `scripts/mounttrace/run-gate.toml`, no `assay.toml`, and the project was in neither `run-gate.root.toml` nor `cmru.orchestration.toml`. The complete test file is reachable directly and from the QEMU script, but tester-unified blocks the namespace setup used by topology scenarios.

**Fix.** Register a `suite` lane for the namespace-free subset using the tester venv; keep namespace-dependent topology and root-only device tests in their existing direct/VM paths.

**Close when.** The registered lane passes its namespace-free tests and README/guide accurately describe the subset and VM coverage.

**Round-2 status: FIXED with the `suite` lane.** `tester-unified` self-skips user-namespace scenarios through `userns_available()` and runs the namespace-free subset. README, guide §9.6, `docs/CONSUMERS.md`, and `docs/DESIGN-GUIDE.md` describe the direct unprivileged command, the 10-test lane subset, and the QEMU VM lane.

## MT-04 — `MDT_VM_PROJECT_DIR` has no test in the harness guard file

**Priority:** should-fix. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) S4.

**Finding.** Commit `46d14e8ca` adds `MDT_VM_PROJECT_DIR` to the shared harness `scripts/debian-install-v2/testing/vm/run-vm-harness.sh` (validation `case` at lines 82-86,
fingerprint at 218-222, lazy `resolve_vm_lab` at 203-209). The logic reads correct (`/r/scripts/x` accepted; `/r/scripts`, `/r/scripts/a/b`, non-`scripts/` paths rejected; unset
leaves fingerprint, names and mounts unchanged), but the guard file `scripts/debian-install-v2/debian_install_v2/tests/test_vm_test_boundary.py` (21 tests, all pass) has no
case for it, and the debian-install-v2 gate depends on that harness.

**Fix.** Add three cases to `test_vm_test_boundary.py`: accepted path, rejected path shapes, and unset leaving the default fingerprint unchanged.

**Close when.** The three cases pass and a hand mutation of the `case` pattern fails one of them.

**Status: FIXED in `592b163d1`.** Tests cover accepted `scripts/mounttrace`, rejected path shapes, and the unset default fingerprint/mounts; changing the guard's `*/*` pattern to `*` caused the accepted-path test to fail.

## MT-05 — unit-level test gaps from surviving mutants

**Priority:** minor. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) section 3 (N7).

**Finding.** Of 14 hand mutants, 8 were killed and 6 survived. Four are cheap unit-level gaps: M3 `inside()` prefix boundary (`mounttrace.py:184`, bare `startswith` instead of
`prefix.rstrip('/') + '/'`), M4 `canonicalize` absolute symlink no longer restarts at the root (line 242), M7 DIVERGENT alert removed (line 760), M10 `newline='\n'` replaced by universal
newlines (line 173, a `\r` in a path). M11 (`BTRFS_EMPTY_SUBVOL_DIR_OBJECTID`, line 64) and M12 (`BTRFS_ROOT_SUBVOL_RDONLY`, line 62) are covered only by the VM-lane tests, by design.

**Fix.** Add: sibling `/data` vs `/data2` in a topology test; an absolute symlink into another root; a DIVERGENT audit; a mountpoint containing `\015`.

**Close when.** M3, M4, M7 and M10 re-applied one at a time each fail the suite.

**Status: FIXED in `3839b97fb`.** The sibling-prefix, absolute-symlink, divergent-audit, and CR mount-path tests were added. Each named mutant was reapplied alone and killed by the focused mounttrace test file.

## MT-06 — guide note on autofs, hung mounts and verbatim options

**Priority:** minor. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) N5 and N3.

**Finding.** Path probing is `stat`/`lstat`/`open(dir)` of the target path (`canonicalize`, `stat_identity`, `btrfs_subvol_info`, `btrfs_boundaries`). On an autofs trigger this can cause
an automount and on a hung NFS/FUSE mount it can block. Guide section 8 (Limits) and 9.1 say "read-only", which is true for the mount table but not for autofs. Separately, mount sources and
superblock options print verbatim (`desc(..., with_opts=True)`, `mounttrace.py:577`): overlay prints the full lowerdir/upperdir list, CIFS/NFS can show user names and server addresses.

**Fix.** One sentence each in the guide: the autofs/hung-mount caveat in section 8 and 9.1, and the verbatim-options note (inherent to a diagnostic tool).

**Close when.** Both sentences are in `mounttrace.md`.

**Status: FIXED in `592b163d1`.** Guide §8 and §9.1 now explain autofs triggers, potentially blocking NFS/FUSE probes, and verbatim mount sources/options.

## MT-07 — review nits

**Priority:** nit. Source: [mounttrace-REVIEW.md](docs/evidence/mounttrace-REVIEW.md) N1, N2, N4, N6, N8, N9 and the Python claim.

1. N1: `3.2.0` exists only as `VERSION` in `mounttrace.py:57`; siblings (`netcup`, `truenas`) carry a `VERSION` file and CHANGES. Add them or document the single source.
2. N2: the commit trailer is `Co-Authored-By: Claude Opus 5.5`; the estate convention is the Sonnet trailer. Not worth rewriting a merged commit; use the convention from here on.
3. N4: the `--watch` loop catches only `FileNotFoundError` for a vanished PID (line 1088); a just-dead process can raise `ProcessLookupError` (ESRCH), giving `mounttrace: [Errno 3] No such process`. Catch both.
4. N6: `audit_state_file` canonicalizes state-file targets with symlink resolution even under `--no-resolve` (line 817, `canonicalize(t, None)`), unlike the other paths.
5. N8: the harness comment at `scripts/debian-install-v2/testing/vm/run-vm-harness.sh:352-354` ("tracked placeholder folders") is now true only for the default project.
6. N9: `scripts/README.md` does not list the project.
7. The `Python >= 3.9` claim has no 3.10+ syntax by grep but was not run on a 3.9 interpreter.

**Close when.** Each item is fixed or struck out with a reason.

**Status: FIXED in `3839b97fb`, `592b163d1`, and round 2.** `VERSION` remains the sole version source because mounttrace has no separate release/CHANGES convention; no patch bump was indicated. The watch loop reports an absent filename through the exception text, and `--no-resolve` is tested for both target and RAMDISK records. The shared harness describes the project-created CoW symlink accurately; focused boundary tests cover its host bind path, state-project fingerprint, and missing-path remedy. The scripts index is corrected, and the runtime `Python >= 3.9` claim was narrowed to source grammar: the available runtime is 3.14, so 3.9 runtime compatibility remains unverified.

## MT-08 — CLI restructuring (P1 a): subcommands, `[PID:]PATH` for view-selecting operands, `--no-probe`, a `Report` object and `--json`

**Priority:** should-fix (umbrella; land after MT-01..MT-07 are merged, same file). Filed 2026-11-09. Source: [mounttrace-output-cli-ANALYSIS.md](docs/ANALYSIS-output-cli-2026-11-08.md) (Headline, F1, F8, "CLI proposal", "Prioritized change list" items 1-2).

**Finding.** All line numbers below are from origin/main `2f7f386cc`; re-resolve them after the `mt-backlog` merge. `PATH` is a required argument, so every audit, prediction and state check first prints a full report on a path nobody asked about (20-50 lines before the answer; one overlay line is 2,543 characters). The seven options that combine into modes (`--pid`, `--host-path`, `--peer`, `--audit-prefix H[=C]`, `--predict`, `--state-file`, `--volumes-root`, plus `--all-hidden`, which equals `PATH=/`) hide the real set of questions. `--no-resolve` only skips `canonicalize()`; the stat and btrfs probes still touch the path, so a hung autofs/NFS mount is not protected (MT-06). Human output is built from strings directly (`Findings` at `mounttrace.py:559-568`, `desc()` 571-578), so tests assert wording and there is no machine interface.

**Fix.** View-selecting operands use `[PID:]PATH` (plain absolute path = the invoking process's view, called `host`; `PID:PATH` = inside that process's root and mount namespace). `explain` and every `compare` operand accept this form; `audit` takes a host `PREFIX` and required `PID:PREFIX`; `predict PATH` is host-only; `soulmask` takes PIDs plus `--state-file` and `--volumes-root` options. The five subcommands are `explain [PID:]PATH [--submounts]`, `compare REF OTHER...`, `audit PREFIX PID:PREFIX`, `predict PATH PID...`, and `soulmask PID... [--state-file F] [--volumes-root D]`. Global options `--json` (NDJSON per pass under `--watch`), `-v`, `--watch SECONDS`, `--no-probe` (the replacement for `--no-resolve`: skips path/filesystem probes but still reads the Soulmask state file; tests fail on stat/lstat/readlink/ioctl), `--version`. Drop old option modes without aliases: `--all-hidden`, `--host-path`, `--peer`, `--audit-prefix`, and `--predict`; `--state-file` and `--volumes-root` remain options only under `soulmask`. Collect output in a `Report` object (verdict, finding(level, cls, text, next), kv, table; from `Findings`) that renders in the order header, verdict, findings, tables and that `--json` dumps unchanged. `compare` returns 3 for DIFFERENT; operational and usage errors return 1 and 2. Tests assert JSON fields plus one JSON key-snapshot.

**Decisions implemented.** (1) `compare` exits 3 when objects are DIFFERENT; `explain` and `predict` do not use status 3, and `--watch` Ctrl-C returns the last pass's status. (2) The `host` label means the invoking process's namespace. (3) MT-01's shared `safe_text()` and `TerminalSafeStream` remain; MT-09 applies `safe_text()` per cell before measuring widths and keeps the stream wrapper as the backstop.

**Close when.** The five subcommands replace the old option modes; `explain` and every `compare` operand accept `[PID:]PATH`, while `audit` takes `PREFIX PID:PREFIX`, `predict PATH` is host-only, and `soulmask` takes PIDs plus its state-file and volumes-root options. `--no-probe` skips path/filesystem probes (the `soulmask` state file is still read), a test patches `stat`/`lstat`/`readlink`/`ioctl` to fail, `--json` carries the same Report, tests assert fields rather than wording, and guide sections 0, 9.2-9.5 and 10.3 mapping (B `soulmask $wings_pid`, C `audit ...`, D `compare ...`, E `predict ...`, F `--watch 1 soulmask ...`) are updated.

**Status: FIXED in `c0c0302f0`; CLI/JSON contract tests in `6794cf18f`; second-review fixes in `ae5bfafa3`.** The five commands, with `[PID:]PATH` for view-selecting operands, replace the old modes without aliases. JSON has one stable top-level key snapshot plus field assertions. The namespace-free RAMDISK and btrfs tests prove `--no-probe` avoids path/filesystem probes while `soulmask` still reads its state file. The final exact-head `run-gate suite` passed with 32 tests, 22 environment skips and 21 subtests.

## MT-09 — human rendering (P1 b): 120-column rules, one mount table, collapsed noise, golden tests, guide update

**Priority:** should-fix (umbrella; depends on MT-08). Filed 2026-11-09. Source: [mounttrace-output-cli-ANALYSIS.md](docs/ANALYSIS-output-cli-2026-11-08.md) (F2-F7, "120-column rules", mock-ups, change-list items 3-5, Tests).

**Finding.** No output fits 120 columns (`plain path`: widest 166; `/ --submounts`: 2543; `--audit-prefix`: 293; `--state-file`: 360). There is no verdict line (a healthy run says nothing, the EROFS fact is a NOTE on line 13 with exit 0), one mount is one line of 8-10 fields reused everywhere, long shared prefixes repeat in every row, vocabulary differs (`INVOKING PROCESS`/`HOST`/`invoking view`, `[rw]`/`rw`/`RO(mount+superblock)`, propagation spelled three ways, Python `False` leaks as `same object: False`), and ALERT lines are indented (`Findings.alert`, line 563) so `grep '^ALERT'` finds nothing although guide 9.3 says they start with `ALERT:`.

**Fix.** Implement the ten rules of the analysis: width W = `$COLUMNS`, else the TTY width, else 120, below 80 laid out as 80, `-v` disables truncation, JSON never truncated; first two lines state what was asked and the answer; two levels only (ALERT and NOTE) at column 0 with one `next` line per ALERT class; one mount table `MOUNTPOINT ID FSTYPE SOURCE FSROOT RO PROPAGATION` (SOURCE/FSROOT shrink first, ID/FSTYPE/RO/propagation never truncated); paths cut in the middle keeping the end, row keys never truncated, audit paths relative to the prefix; collapsed by default and expanded by `-v`: overlay `lowerdir` (`layers=N` plus a writable-layer NOTE), full superblock options, parent chain, empty sections (one line `hidden: none on or below this path`), OK audit rows (count), views that receive nothing; one vocabulary in human and JSON (views `host` / `pid N`; `private|shared(N)|slave(N)|shared(N)+slave(M)|from(N)|unbindable`; RO `rw|ro|ro:sb`; `same`/`different`; `linked|NOT LINKED|tucked`; non-alerting class `LAYER`); user-controlled strings escaped by the single per-cell helper before measuring; no colour. Replace `desc()` with one table renderer; keep the logic of `Mount.propagation` 107-118, `ro_label` 132-140, `btrfs_label` 303-311, `hidden_reason` 422-433, `place_event` 487-507, `link_status` 510-552 and return structured fields.

**Tests.** 6-8 golden tests from synthetic View fixtures (style at `tests/test_mounttrace.py:205-218`) with `--no-probe` at COLUMNS=120 and 80 plus a non-TTY run (must give 120); one generic check that every user-namespace scenario's output stays <= COLUMNS; one VM-lane run at the end. Verify on a real Wings tree on <host>/<host> that middle-cut paths do not hide the only difference between `volumes/<uuid>/WS/Saved/Logs` paths.

**Close when.** Every default line is <= W in all golden tests, the guide (sections 0, 9, 9.3's ALERT claim, 10.3) is updated in the same change, and the VM lane passes once.

**Status: ACCEPTED at `04a224fab326d8728792b9a737f32a0bd7563ef5` (2026-11-09).** Code landed in `c0c0302f0`; golden and width-contract tests in `6794cf18f`; second-review fixes in `ae5bfafa3`; F7/P1.4 missing-path and overlay-context follow-up in `e8b318a6b`; live-acceptance fixes in `9d4ebce3f`. The final fixed-head <host> run recorded in
[mt-close-vm-LIVE-REPORT.md](docs/evidence/mt-close-vm-LIVE-REPORT.md) passed the six-input manifest preflight and the VM lane in both phases (`Ran 67 tests`, unprivileged `OK (skipped=5)`; root `Ran 67 tests`, `OK` with 0 skips and 0 failures), ending with the whole-lane PASS marker in 536 s. The earlier Wings-shaped stand-in checks at `cc16de7f2` passed at 120 and 80 columns, non-TTY, and `--watch` through files and pipes. Synthetic golden tests cover COLUMNS=120, COLUMNS=80 and non-TTY output. At 80 columns an arbitrary UUID group hidden in the middle cut remains a documented limit with `-v` and JSON remedies; a golden pins that prefix-only UUID differences have identical default compare headers and distinct verbose headers.

## MT-10 — P2 follow-ups: `--watch` change line, clear errors, `soulmask` bundles the volumes-root audit

**Priority:** minor (umbrella; after MT-08). Filed 2026-11-09. Source: [mounttrace-output-cli-ANALYSIS.md](docs/ANALYSIS-output-cli-2026-11-08.md) (P2, F7, open question 6).

**Finding and fix.** (1) `--watch` reprints everything with only a banner: print `--- <time> mount tables changed: <view> +#id ... -#id ...` before each report and return the last pass's status on Ctrl-C (`main` 1083-1096). (2) A missing PID gives a raw `[Errno 2]` / `[Errno 3]`, and the `--watch` loop catches only `FileNotFoundError` (covers MT-07 item 3): clear errors for a vanished PID and for permission (`main` 1074-1101). (3) `soulmask` bundles the volumes-root audit with the state-file audit and defaults the state file to `/run/soulmask_tmpfs.state`.

**Decision implemented.** `soulmask` bundles the volumes-root audit with the state-file audit and defaults the state path to `/run/soulmask_tmpfs.state`.

**Close when.** The change line, the error texts and the bundled `soulmask` have tests, and the guide's `--watch` and soulmask sections match.

**Status: FIXED in `c0c0302f0`; watch, error and Soulmask tests in `6794cf18f`; second-review fixes in `ae5bfafa3`.** Watch emits a mount-change summary before each changed report, Ctrl-C returns the last status, vanished and inaccessible PIDs get actionable errors, and `soulmask` combines state and volume audits with the `/run/soulmask_tmpfs.state` default. README, design guide, consumer examples and the command guide describe the behavior. The final exact-head suite result was 32 tests passed, 22 environment skips and 21 subtests.

## MT-11 — live-acceptance fixes and the second-review test/citation nits

**Priority:** should-fix for the acceptance; minor for the two review nits. Filed 2026-11-09, status OPEN. Source:
[mt-cli-REVIEW2.md](docs/evidence/mt-cli-REVIEW2.md) (Nits 1-2 and "Unchecked / owed"), MT-09 above, and
`TESTING-ESTATE-CHECKLIST.md` (mounttrace row).

**Status: FIXED / ACCEPTED at `04a224fab326d8728792b9a737f32a0bd7563ef5` (2026-11-09).** The citation nit was fixed on main in `3d9f722ad`: MT-08..MT-10 cite the review-fix commit `ae5bfafa3`, and the counts cite the review's final exact-head result. The five-command, two-phase error matrix was added in `e8b318a6b`; it covers bare ENOENT, ESRCH, and PermissionError. The final fixed-head VM lane run then passed on <host> (67+67 tests; unprivileged `OK (skipped=5)`, root `OK` with 0 skips and 0 failures), ending with the whole-lane PASS marker in 536 s.

**Finding.** The independent re-review of `ae5bfafa3` found all six first-review blockers fixed, but left two non-blocking nits. First, the automated error matrix at
`scripts/mounttrace/tests/test_mounttrace.py:531` and `:563` exercises explain under watch and explain/compare outside watch; it does not parameterize explain,
compare, audit, predict and soulmask across both phases. Independent probes showed the current command-specific behavior works, so this is a test-strength gap rather
than a present behavior failure. Second, the MT-08..MT-10 rows and status paragraphs cited only `c0c0302f0` and `6794cf18f`, omitting `ae5bfafa3`; that citation correction
is landed in this filing.

The review also correctly did not run the QEMU VM lane or a real Wings-tree check because it had no host access. The later <host> live run found no real Wings tree
on that host and used a faithful in-guest stand-in. Its output findings are now covered by synthetic 80-column golden tests for the distinguishing UUID-bearing
`volumes/<uuid>/WS/Saved/Logs` suffix.

**Closed no-VM work (`e8b318a6b`).** Parameterized the error matrix over `explain`, `compare`, `audit`, `predict`, and `soulmask`, at initial read and watch refresh, with bare
ENOENT, ESRCH, and PermissionError. It asserts exit 1, no raw errno text, and PID-specific versus multi-view errors. The citation correction remains recorded above.

**Live acceptance.** The final fixed-head run on 2026-11-09 used
`04a224fab326d8728792b9a737f32a0bd7563ef5` on <host>. The six-input
manifest preflight passed, followed by 67 unprivileged tests (`OK (skipped=5)`)
and 67 root tests (`OK`, 0 skips, 0 failures); the whole lane ended with its
PASS marker after 536 s. Full evidence and artifacts are in
[mt-close-vm-LIVE-REPORT.md](docs/evidence/mt-close-vm-LIVE-REPORT.md)
and [mt-close-vm-LIVE-artifacts/](docs/evidence/mt-close-vm-LIVE-artifacts/).
The earlier Wings-shaped checks at `cc16de7f2` used the documented stand-in
because <host> has no real Wings tree. A real Wings-tree observation remains
optional when an operator has one available; it is not part of the acceptance
that MT-11 defined, which is met.

**Live-acceptance fixes (2026-11-09, `9d4ebce3f`).**

- `--watch` flushes every emitted human line and NDJSON object. The first pass is
  observable on a block-buffered file before the sleep; change lines use the
  same path.
- The VM lane reads `tests/test-inputs.txt`, validates every file before any
  image build or boot, and copies exactly those files plus `tests/`. The old
  four-file documentation omission cannot recur silently.
- Four stale MT-09 scenario assertions now match the real contract: the two
  audit scenarios and Docker rslave scenario invoke `audit -v` to test the raw
  `OK` row rather than weakening to the collapsed count; tucking asserts
  `ctr-own` in `PATH WALK` and the overmounting `host-late` in `HIDDEN MOUNTS`;
  the slave scenario's private mount path is the prefix-relative `c`, not `/c`.
- Crowded tables move path cells to a labeled full-width line before middle
  cutting, so UUID-bearing tails remain visible at 80 columns. Status, view,
  result, link and same-object values move complete to labeled lines instead of
  becoming prefixes. A first-UUID-group difference hidden by the default
  middle cut remains a documented limit; `-v` and JSON retain the full path.
  Exact golden output covers 120, 80, and non-TTY rendering.
- `compare` rows now include the compared input path. This distinguishes
  same-view inputs, including three host-only paths, whose view label alone is
  correctly but ambiguously `host`.

## MT-12 — optional P3 proposals from the output/CLI analysis

**Priority:** optional. Source: `mounttrace-output-cli-ANALYSIS.md`, “Prioritized change list,” P3 (marked “leaning no”).

**By-design closes (`bff533daf`).**

- Identical sibling-row collapse: not adopted; keep one row per mount so mount multiplicity remains visible.
- `--mountinfo FILE` offline input: not adopted; keep the interface centered on the inspected process's live mountinfo.
- Color: not adopted; the analysis's rendering rule 9 explicitly says no color.
- CJK display-cell width: not adopted; the renderer remains standard-library-only and currently measures Python string length.

**Status: CLOSED BY DESIGN (`bff533daf`).** These optional items were not recommendations to implement; the analysis itself leans against all four.

## MT-13 — adopt cli-extended for the CLI layer

**Priority:** should-fix. Filed 2026-11-10 from the operator directive
"mounttrace should adopt cli-extended".

**Decision.** Keep option (b): a standard-library-only analysis core plus a thin
cli-extended front end. The wheel declares the released
`cli-extended>=0.6.0` dependency and a `mounttrace` console entry point.
`src/mounttrace_core.py` remains importable without cli-extended and never
starts a subprocess. The former copy-one-script CLI deployment is retired;
hosts install the wheel and dependency into a venv from verified GitHub
Release artifacts with `--no-index`.

**Implementation.** Parser registration, grouped and command help, identity,
version agreement, common controls, error formatting, unexpected-exception
policy and dispatch live in `src/mounttrace_cli/cli.py`. Exit statuses remain 0
clean, 3 ALERT/DIFFERENT, 1 operational error, and 2 usage error. `-v` remains
compatible, while `--verbose`/`--debug` map to the same human-detail setting.
Compact JSON is written directly from the core renderer so goldens retain their
bytes. `--version`, `version`, help and malformed-invocation behavior are
tested through the real development executable.

**Evidence.** The generated surface has 41 active reviewed cases backed by
`tests/test_cli_review_cases.py`; all are accepted and have effect rationales
and exact test links. `cli-extended surface check` and `cli-extended audit`
pass the mechanical checks; AC-19 and AC-20 are not applicable with reasons in
the task report. `run-gate suite` runs the complete test directory, and the VM
runner carries `src/` plus the selected cli-extended source explicitly. The VM
lane must be rerun on <host> with a rebuilt base because pytest is now part of
the guest package set.

**Close condition met when merged and the final-head report gates pass.**

## MT-14 — tests must scope process-wide `os.*` syscall fakes

Pytest 9.2 calls `os.fstat` during capture and teardown. The mounttrace
canonicalization and no-probe tests originally replaced shared `os.stat` and
`os.lstat` for every path. They now match only the fixture paths under test
and delegate all other calls to the saved real functions, preserving the
missing-leaf, permission-warning, and no-touch oracles. Future process-wide
`os.*`, `builtins.open`, or `io.open` patches must follow the same rule.
