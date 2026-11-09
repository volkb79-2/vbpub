# Design guide

## CLI and report

The CLI has one command per question because the old option modes made a
target path mandatory even for audits, predictions, and state checks. Every
path uses `[PID:]PATH`: a plain absolute path is in the invoking process's
view, and a PID prefix selects that process's root and mount namespace. This
keeps the host/process distinction explicit at every comparison boundary.

The commands are `explain`, `compare`, `audit`, `predict`, and `soulmask`.
`compare` returns status 3 for different objects so scripts can treat a
difference as a useful result. `explain` and `predict` report observations and
do not use status 3. Former root-level option modes have no aliases; a CLI
alias would preserve the ambiguity this interface is meant to remove. The
state-file and volumes-root overrides belong to the `soulmask` command.

Each pass produces one `Report`: a question header, verdict, ALERT/NOTE
findings, key/value facts, and named tables. Human output and `--json` consume
that same object, so machine consumers do not scrape prose and the two formats
cannot disagree about the result. `--watch --json` emits one JSON object per
pass (NDJSON).

`soulmask` combines the state-file check with the live volumes-root audit:
state entries are claims about mounts, so the command checks both against the
same host and PID mount tables. Its state-file default is
`/run/soulmask_tmpfs.state`; override it with `--state-file FILE` on `soulmask`
when setup writes the file elsewhere. `--watch` re-reads the inspected mount tables and state file,
prints a change line before a changed report, and Ctrl-C returns the last
report's status.

`--no-probe` exists for environments where even a path lookup can trigger
autofs or block on a network filesystem. It reads the selected mountinfo files
for topology, skips symlink resolution, `stat`, `lstat`, and btrfs ioctls, and
still reads the Soulmask state file when that command is selected. The result
uses mountinfo evidence, which can establish mount topology and filesystem
paths but cannot prove inode identity.

`predict` models a mount that may not exist yet. It suppresses only the expected
missing-leaf lexical note; permission and other resolution warnings remain
visible. This keeps the prediction focused on where the future mount attaches.

## Human rendering

The renderer lays out tables instead of composing long mount descriptions.
It answers the question and gives the verdict first, then shows findings and
details. ALERT and NOTE are the only finding levels; a finding's optional
`next` field carries its concrete follow-up into both formats. Collapsed
details avoid repeating parent chains, long overlay lowerdirs, empty sections,
rows that agree, and views receiving no mount. `-v` expands those details,
prints the full report header, and disables truncation. Source text equal to
the filesystem type displays as `-`;
the mount table preserves the complete ID, filesystem type, RO state and
propagation label even if they need labeled continuation lines at narrow widths.
An overlay `upperdir` note labels the value as the overlay's writable backing
layer, not a volume, and shows the value exactly as mountinfo reports it. The
reported string is metadata; mounttrace does not resolve it through the
inspected process's path view.

Before width calculation, every table cell passes through `safe_text()`. The
`TerminalSafeStream` wrapper remains a final guard for diagnostics and any
output that is not a table cell. This order keeps terminal controls from
changing layout while preserving the original values in the analysis and JSON
data.

## Terminal output

Mountinfo escapes whitespace and backslashes in paths; mounttrace decodes those
values so it can resolve the same names the kernel sees. The values remain raw
for filesystem lookup and comparison. At the terminal boundary,
`safe_text()` renders C0/C1 controls as `\xNN` and selected bidi/format
characters as `\uNNNN`. Embedded newlines and tabs in paths and option values
become visible escapes, while report line breaks and indentation retain their
layout. JSON serialization escapes controls as JSON data.

## Test gates

`run-gate suite` runs the namespace-free subset in `tester-unified`; that
container blocks user namespaces, so topology scenarios self-skip there. The
registered QEMU test-VM lane runs the complete suite, including root-only
btrfs and Docker scenarios. Its marker is only a presence check and does not
authenticate the guest.
