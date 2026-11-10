# mounttrace changes

This file records the shipped interface and maintenance status. `VERSION` in
`mounttrace.py` remains the single version source; this work does not create a
new release or bump the version.

## [Unreleased]

### Upgrade notes: 3.2.0 CLI restructuring

Version 3.2.0 replaces the former root-level option modes with five
subcommands. Existing `--all-hidden`, `--host-path`, `--peer`,
`--audit-prefix`, and `--predict` invocations must be rewritten; no compatibility
aliases remain.

| Command | Use |
|---|---|
| `explain [PID:]PATH` | Explain one path's mounts, write target, and hidden mounts. `--submounts` includes visible mounts below it. |
| `compare REF OTHER...` | Compare one reference path with one or more other paths. Each path may select a different filesystem object or a different process view. It exits 3 when the results are `DIFFERENT`, 0 when they match, and 1 on an operational error. |
| `audit PREFIX PID:PREFIX` | Compare mountpoints below a host prefix with the same tree in a process view. |
| `predict PATH PID...` | Predict where a new mount at a host path will appear in each listed process view. |
| `soulmask PID...` | Audit the Soulmask state file and volumes root together. `--state-file FILE` and `--volumes-root DIR` belong to this command. |

Path operands that select a process view use `[PID:]PATH`: an absolute path
means the invoking process's view, while `PID:/path` selects that process's root
and mount namespace. `explain` and every `compare` operand accept this form.
`audit` instead takes a host `PREFIX` and a required `PID:PREFIX`;
`predict PATH` is host-only; `soulmask` takes PIDs plus its
`--state-file FILE` and `--volumes-root DIR` options; `soulmask` does not take
view-selecting path operands.
`--no-probe` replaces `--no-resolve`; it skips symlink, stat/lstat, and btrfs
probes, although `soulmask` still reads its state file. `--json` emits the same
`Report` object as human output, and `--watch --json` emits one object per pass
(NDJSON).

Human output now defaults to 120 columns for non-TTY output, uses the terminal
or `$COLUMNS` when available, and lays out at a minimum of 80 columns. `-v`
expands collapsed details and disables truncation. `explain` and `predict`
report observations and do not use exit status 3; any ALERT report, including
a `compare` difference, uses 3. Operational errors return 1 and usage errors
return 2.

### 3.2.0 delivery status

| Item | Status | Commits |
|---|---|---|
| MT-08 CLI, `[PID:]PATH`, `Report`, and JSON | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-09 bounded human output and collapsed detail | Accepted: final VM lane PASS at `04a224fab` (67+67 tests) | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3`, F7/P1.4 follow-up `e8b318a6b`, live fixes `9d4ebce3f`, evidence `04a224fab` |
| MT-10 watch, error messages, and Soulmask volume audit | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-11 citation nit | Fixed on main; MT-08..MT-10 rows cite `ae5bfafa3` | `3d9f722ad` |
| MT-11 command/phase error matrix | Fixed; 30 command, phase, and error combinations | `e8b318a6b` |
| MT-11 final fixed-head QEMU VM acceptance | Fixed/accepted: six-input preflight, 67+67 tests, whole-lane PASS on r1002 in 536 s | `9d4ebce3f`, evidence `04a224fab` |
| MT-12 P3 sibling-row collapse | Deferred by design; preserve mount multiplicity | `bff533daf` |
| MT-12 offline `--mountinfo FILE` | Deferred by design; retain live mountinfo input | `bff533daf` |
| MT-12 color | Deferred by design; the analysis says no color | `bff533daf` |
| MT-12 CJK width | Deferred by design; no display-cell width table/dependency | `bff533daf` |

### Unreleased maintenance

- `predict` treats an absent leaf as a normal future mountpoint and omits the
  lexical-analysis note for that expected case (`e8b318a6b`).
- Overlay `upperdir` findings identify the writable backing layer, say that it
  is not a volume, and label the value as recorded in mountinfo (`e8b318a6b`).
- MT-11's automated error matrix covers all five commands at initial read and
  watch refresh for bare ENOENT, ESRCH, and PermissionError (`e8b318a6b`).
  The final fixed-head r1002 acceptance run
  (`04a224fab326d8728792b9a737f32a0bd7563ef5`, 2026-11-09) passed the six-input
  manifest preflight, then ran 67 unprivileged tests with `OK (skipped=5)` for
  the five expected root-only cases and 67 root tests with `OK`, 0 skips, and
  0 failures. The whole lane ended with its PASS marker in 536 s. The earlier
  Wings-shaped checks at `cc16de7f2` passed at 120 and 80 columns, non-TTY, and
  through files and pipes. r1002 has no real Wings tree, so those checks used
  the documented stand-in; a real Wings-tree observation remains optional when
  an operator has one. Evidence:
  `/home/vscode/vbpub-release-program-20261027/reports/mt-close-vm-LIVE-REPORT.md`
  and `/home/vscode/vbpub-release-program-20261027/reports/mt-close-vm-LIVE-artifacts/`.
- `--watch` flushes every emitted human line and NDJSON object, including the
  first pass and change lines. A block-buffered file or pipe therefore receives
  the first report before the sleep.
- The VM lane's external test inputs come from `tests/test-inputs.txt`; the lane
  validates the complete list before image build or boot and copies exactly
  those files plus `tests/`.
- At 80 columns, crowded path cells move to labeled full-width lines before the
  middle cut, preserving UUID-bearing path tails. Categorical values move whole
  to labeled lines rather than being clipped. A UUID first-group difference
  hidden in the middle cut remains a documented limit with `-v` and JSON as
  the exact-path remedies. Golden tests cover 120, 80, and non-TTY output.
- `compare` tables include the compared input `PATH`, so multiple host-only
  paths no longer rely on the identical `host` view label to identify rows.
- The VM guest input manifest includes `testing/vm/run-vm-tests.sh` because
  the copied guest test module executes its `--check-inputs-only` entry. A test
  pins the complete six-file project-root inventory that the guest tests read or
  execute; the lane copies `tests/` plus every manifest-listed file.
- The stale MT-09 topology/Docker assertions now use expanded audit rows where
  raw `OK` rows are under test, find a hidden overmount in `HIDDEN MOUNTS`, and
  use the defined prefix-relative `PATH` value `c` rather than the old `/c`.
- P3 ideas from `mounttrace-output-cli-ANALYSIS.md` are not adopted: row
  deduplication obscures mount multiplicity, offline mountinfo input introduces
  a second data-source interface, color conflicts with the analysis's no-color
  rule, and CJK terminal-cell width needs a display-width dependency or custom
  width table. The analysis itself labels P3 “leaning no.”

<!-- cmru: release history -->
