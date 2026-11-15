# Rust toolchain qualification fixtures

`nextest-failure.txt` and `libtest-failure.txt` are exact output lines captured
by the registered `rust-r1-qualification` Run-Gate lane from the fixture crate
in `r1-workspace/`. The lane runs in `tester-unified-rust`, with networking
disabled, and deliberately sets `ASSAY_RUST_FIXTURE_FORCE_FAIL=1` for those two
commands. The fixture therefore records real nextest and libtest output; it is
not a hand-written imitation. `toolchain.txt` records the tool versions from
the same capture run.

The lane also runs real `cargo llvm-cov nextest` against the crate, parses its
LCOV through the Rust adapter and captures the full output under ignored
`.assay/rust-r1-real/coverage.info`. The stable branch-unavailable fixture
from the measured hostobs run remains
[`s2-sampler-lcov.info`](../../../docs/rust-r0/evidence/s2-sampler-lcov.info);
it retains the observed 13 source records and zero-only branch markers.
