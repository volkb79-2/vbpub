# mounttrace design guide

## Terminal output

mountinfo escapes whitespace and backslashes in paths; mounttrace decodes those
values so it can resolve the same names the kernel sees. The values remain raw
for filesystem lookup and comparison, then `safe_text()` renders C0/C1
controls as `\xNN` and selected bidi/format characters as `\uNNNN` at the
output boundary. Embedded newlines and tabs in paths and option values become
`\xNN`, while the report's own line breaks and tabs keep their layout. This
keeps diagnostics readable without changing the analysed mount data.

See [the complete guide](../mounttrace.md#91-requirements-and-safety) for the
output contract and safety limits.

## Test gates

`run-gate suite` runs the namespace-free subset in `tester-unified`; that
container blocks user namespaces, so topology scenarios self-skip there. The
same Python test file can be run directly with `python3 tests/test_mounttrace.py`
to exercise unprivileged topology scenarios on hosts that allow user and mount
namespaces. The registered QEMU test-VM lane runs the complete suite, including
root-only btrfs and Docker scenarios. Its marker is only a presence check and
does not authenticate the guest.
