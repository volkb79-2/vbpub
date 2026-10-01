# P111 self-review

- All 25 cases assert complete values, call structures, rendered output, or
  exact exception text/type.
- No target function is mocked. Collector/config/resolver/path/current-reader
  seams are injected and paired with complete outputs and exact calls.
- The resolved-name case proves one collector sweep, one resolver call, the
  complete entity map, exact cgroup path/encoding, and parsed integer result.
- Direct cgroup-key cases prove resolution is skipped and read/parse failures
  return exactly `None`.
- Both `Exception` repairs are covered on ordinary failures and opposed by
  exact `KeyboardInterrupt` propagation cases.
- Bool rejection is pinned independently for memory and CPU without weakening
  positive numeric behavior.
- No substring, partial-field, membership-only, non-None, range, length-only,
  hollow, duplicate, sleep, host-state, pragma, omit, or mutation claim remains.
- Two complete xdist runs from the exact clean commit have identical empty
  whole-file records and identical `4/4` changed-line results.
- Scope contains only the P111 handoff/reports, update source, and new tests.
