# mounttrace changes

This file records the shipped interface and maintenance status. `VERSION` in
`mounttrace.py` remains the single version source; this work does not create a
new release or bump the version.

## 3.2.0 CLI work

| Item | Status | Commits |
|---|---|---|
| MT-08 CLI, `[PID:]PATH`, `Report`, and JSON | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-09 bounded human output and collapsed detail | Code complete; VM/Wings acceptance owed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3`, F7/P1.4 follow-up `e8b318a6b` |
| MT-10 watch, error messages, and Soulmask volume audit | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-11 citation nit | Fixed on main; MT-08..MT-10 rows cite `ae5bfafa3` | `3d9f722ad` |
| MT-11 command/phase error matrix | Fixed; 30 command, phase, and error combinations | `e8b318a6b` |
| MT-11 QEMU VM and real Wings-tree acceptance | Open; owed to the test-host controller | — |
| MT-12 P3 sibling-row collapse | Deferred by design; preserve mount multiplicity | `e8b318a6b` |
| MT-12 offline `--mountinfo FILE` | Deferred by design; retain live mountinfo input | `e8b318a6b` |
| MT-12 color | Deferred by design; the analysis says no color | `e8b318a6b` |
| MT-12 CJK width | Deferred by design; no display-cell width table/dependency | `e8b318a6b` |

## Unreleased maintenance

- `predict` treats an absent leaf as a normal future mountpoint and omits the
  lexical-analysis note for that expected case (`e8b318a6b`).
- Overlay `upperdir` findings identify the writable backing layer, say that it
  is not a volume, and label the value as recorded in mountinfo (`e8b318a6b`).
- MT-11's automated error matrix covers all five commands at initial read and
  watch refresh for bare ENOENT, ESRCH, and PermissionError (`e8b318a6b`). QEMU
  VM and Wings acceptance remain owed to the host controller.
- P3 ideas from `mounttrace-output-cli-ANALYSIS.md` are not adopted: row
  deduplication obscures mount multiplicity, offline mountinfo input introduces
  a second data-source interface, color conflicts with the analysis's no-color
  rule, and CJK terminal-cell width needs a display-width dependency or custom
  width table. The analysis itself labels P3 “leaning no.”
