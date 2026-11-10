# mounttrace changes

This file records the shipped interface and maintenance status. `VERSION` in
`mounttrace.py` remains the single version source; this work does not create a
new release or bump the version.

## 3.2.0 CLI work

| Item | Status | Commits |
|---|---|---|
| MT-08 CLI, `[PID:]PATH`, `Report`, and JSON | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-09 bounded human output and collapsed detail | Accepted by the live VM lane | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3`, F7/P1.4 follow-up `e8b318a6b`, live fixes `9d4ebce3f`, evidence `cc16de7f2` |
| MT-10 watch, error messages, and Soulmask volume audit | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-11 citation nit | Fixed on main; MT-08..MT-10 rows cite `ae5bfafa3` | `3d9f722ad` |
| MT-11 command/phase error matrix | Fixed; 30 command, phase, and error combinations | `e8b318a6b` |
| MT-11 fixed-branch QEMU VM rerun | Fixed/accepted: 64+64 tests passed on r1002; Wings stand-in passed at 120/80/non-TTY and through files/pipes | `9d4ebce3f`, evidence `cc16de7f2` |
| MT-12 P3 sibling-row collapse | Deferred by design; preserve mount multiplicity | `bff533daf` |
| MT-12 offline `--mountinfo FILE` | Deferred by design; retain live mountinfo input | `bff533daf` |
| MT-12 color | Deferred by design; the analysis says no color | `bff533daf` |
| MT-12 CJK width | Deferred by design; no display-cell width table/dependency | `bff533daf` |

## Unreleased maintenance

- `predict` treats an absent leaf as a normal future mountpoint and omits the
  lexical-analysis note for that expected case (`e8b318a6b`).
- Overlay `upperdir` findings identify the writable backing layer, say that it
  is not a volume, and label the value as recorded in mountinfo (`e8b318a6b`).
- MT-11's automated error matrix covers all five commands at initial read and
  watch refresh for bare ENOENT, ESRCH, and PermissionError (`e8b318a6b`). The
  fixed-head r1002 acceptance rerun (`cc16de7f2`) passed the VM lane in both
  phases: unprivileged 64 tests with five expected root-test skips, then root
  64 tests with no skips. The Wings-shaped live checks also passed at 120 and
  80 columns, non-TTY, and `--watch` through files and pipes. r1002 has no real
  Wings tree: the checks used the documented stand-in, and a real-tree look
  remains an owed observation if the operator has one. Evidence:
  [mt11-rerun-LIVE-REPORT.md](docs/evidence/mt11-rerun-LIVE-REPORT.md).
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
