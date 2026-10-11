# B132–B134 controller log — 2026-10-11

This log records the exact-tree gate follow-up and the remaining serialized
acceptance work for the provisional Assay package.

## Registered gate at `3d8b662ca4ac2dc3ceaf6322a3d43371350852aa`

The CIU-managed worktree was clean and attached to
`assay-b132-b134-review-fixes`. The registered command was
`ASSAY_GATE_ALLOW_SHARED_HOST=1 ./run-gate.py --worktree
/workspaces/vbpub/.worktrees/assay-b132-b134-review-fixes tester-unified`.
The host admission check passed with `dev-gates.slice` at
2,125,209,600 bytes of a 4,294,967,296-byte limit and a 2 GiB reservation.

The gate's B145 bounded-wait and low-pids probes passed, the exact-OID wheel
built, and the attestation phase passed. The full suite completed in 831.62
seconds with 9,020 passed, 21 skipped, and two failures. Run-Gate recorded
`FAIL` after 868 seconds. The outer log is
`/tmp/assay-tester-unified-3d8b662ca4ac2dc3-20261011T1018Z.log`; Run-Gate's
preserved lane log is
`/tmp/run-gate/lanes/tester-unified/cd781062cd73077b045e0dbebd5b22d9.log`.

The failures were test-oracle defects, not a demonstrated product regression:

- The stuck-probe test called a function replaced by `tests/conftest.py`'s
  autouse fixture with an always-available stub. Its fake worker never reached
  the production deadline path.
- The fatal-queue test released held candidates when the fatal path started,
  before the queue's `wait()` returned the fatal future. A held candidate could
  therefore complete first and free a slot for one more submission.

Sol xhigh reviewed the failures and accepted the test-only repair in commit
`6fc0e86f291c4e90c6778fd74ed7c7f556827895`. The observer test restores the
saved production function and substitutes only its local thread dependency.
The queue test releases held candidates after `wait()` returns the exact fatal
future. `git diff --check` passed before commit. No test or gate has run on
`6fc0e86f` yet.

## Shared-host mutation campaigns

The third tester container overlapped two other R2 campaigns. Run-Gate's
controller record `RW-565` says both ended with `ERROR/EXEC_FAILED` after an
unattributed shared-ancestor resource event. The timing is recorded there; it
does not establish which workload caused the event. Their partial candidate
records remain infrastructure errors and must not be converted to kills or
survivors.

At `10:34:03Z`, the controller resumed one cgprofile R2 campaign in
`run-gate-vbpub-r2-1274146-1791714842`. Its rough runtime projection is about
17 hours, with a 24-hour lane budget. To keep mutation evidence interpretable,
do not overlap another test container or mutation campaign with it. Respect
the controller's 25-minute progress-check interval unless an error or likely
completion warrants an earlier check.

## Remaining sequence

After that R2 campaign ends and the host has no other active gate container:

1. Reconcile this CIU worktree with the then-current `main` while preserving
   its recorded branch and identity; confirm with `ciu worktree inspect`.
2. Run registered `tester-unified` on the exact integrated tip. Then run the
   B105 preflight and the B105 bounded pilot, followed by the analysis-package
   pilot, one mutation campaign at a time.
3. Record each pilot as a bounded measurement, not a full B105 or B131 R2
   qualification. Update the reports and backlog, then complete the final
   exact-tree gate and review before merge and release.

