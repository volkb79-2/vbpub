# mounttrace changes

This file records the shipped interface and maintenance status. `VERSION` in
`mounttrace.py` remains the single version source; this work does not create a
new release or bump the version.

## 3.2.0 CLI work

| Item | Status | Commits |
|---|---|---|
| MT-08 CLI, `[PID:]PATH`, `Report`, and JSON | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-09 bounded human output and collapsed detail | Code complete; VM/Wings acceptance owed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-10 watch, error messages, and Soulmask volume audit | Fixed | `c0c0302f0`, `6794cf18f`, review fixes `ae5bfafa3` |
| MT-11 second-review no-VM nits | Citation correction present; full command/phase error matrix being closed in this work | `ae5bfafa3` |
| MT-11 QEMU VM and real Wings-tree acceptance | Open; owed to the test-host controller | — |

## Unreleased maintenance

- `predict` treats an absent leaf as a normal future mountpoint and omits the
  lexical-analysis note for that expected case.
- Overlay `upperdir` findings identify the writable backing layer, say that it
  is not a volume, and label the value as recorded in mountinfo.
- MT-11's automated error matrix covers all five commands at initial read and
  watch refresh for bare ENOENT, ESRCH, and PermissionError. QEMU VM and Wings
  acceptance remain outside this closeout.
- P3 ideas from `mounttrace-output-cli-ANALYSIS.md` are not adopted: row
  deduplication obscures mount multiplicity, offline mountinfo input introduces
  a second data-source interface, color conflicts with the analysis's no-color
  rule, and CJK terminal-cell width needs a display-width dependency or custom
  width table. The analysis itself labels P3 “leaning no.”
