# mounttrace design guide

## Terminal output

mountinfo escapes whitespace and backslashes in paths; mounttrace decodes those
values so it can resolve the same names the kernel sees. The values remain raw
for filesystem lookup and comparison, then `safe_text()` renders C0/C1
controls visibly at the output boundary. Embedded newlines and tabs in paths
and option values become `\xNN`, while the report's own line breaks and tabs
keep their layout. This keeps diagnostics readable without changing the
analysed mount data.

See [the complete guide](../mounttrace.md#91-requirements-and-safety) for the
output contract and safety limits.
