# cgprofile P6 — round-7 survivor-oracle fix-verification addendum

**Disposition: ACCEPT for both test-only mutation oracles, separately.** Each
commit is a direct child of P6 final base
`45f575bd0d732df140009f78b1665cd5f338d9ee`; they are sibling commits,
not a combined tested tip. This note supplements, but does not replace, the
separate round-7 B1–B3 fix-verification addendum. Neither commit changes
production code or the older R2 judged tree `175ed5d84274637fba2679b452e0704422ff58bc`.

## Candidate 185 — `bfa93e6216d4fbfa162a997beef822a6075b4ab0`

The new `test_unit_path_parses_stdout_from_a_real_successful_command` uses an
executable temporary `busctl` substitute, not a mocked `subprocess.run`. It
emits a successful, well-formed `GetUnit` object-path reply and asserts that
`_systemd_unit_path` returns the path. The exact `capture_output=True` to
`False` mutant leaves the helper without captured stdout, so it cannot return
that path. This asserts observable behavior rather than the flag spelling.
The controller reports restored pass, exact-mutant failure, restored pass;
the focused helper file passed 120 tests on restored code.

## Candidate 236 — `936531125fbfb8860fccf6c13ed093daea033a72`

The added parameter supplies empty stdout with a successful command status
to `_systemd_string_array_property` and requires `None`: no authoritative
array reply is not an empty array. With the exact `or` to `and` mutant in the
short-reply guard, `fields[0]` is accessed on an empty list and raises
`IndexError` instead of returning the fail-closed unknown result. The
controller reports 8/8 focused cases passed on restored code, failure under
that exact mutant, and pass again after restoration.

These are source-and-oracle review conclusions; the reported mutant runs were
not rerun by this reviewer. Neither individual commit constitutes a combined
tip or a passing current-tip R2/full gate. The B1–B3 ACCEPT-conditional
disposition remains unchanged.
