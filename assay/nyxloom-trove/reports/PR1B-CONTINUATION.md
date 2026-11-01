# PR1b continuation — 2026-11-01

## Scope completed

PR1b adds bounded native R2 candidate scheduling up to the selected `jobs`
count. Workers execute through the PR1a window-host seam; a coordinator-owned
`LimitLedger` samples each start and end boundary. Every positive resource
counter delta is attributed to every candidate window whose sequence range
overlaps it, including visible ancestor-local events. The existing termination
classifier preserves AP-1 precedence: PASS remains survived, while an
overlapping limit event makes a failing candidate crashed.

Candidate state and progress are committed by the coordinator in candidate
index order. The bounded queue submits no more than N candidates at once;
resume identity omits runtime `jobs`, and the WP7 retry tail stays serial.
SIGTERM/KeyboardInterrupt cancels pending ledger requests, terminates live
window hosts, and leaves the uncommitted in-flight suffix unclassified.
`--jobs` is refused with `--candidates-file` so a pilot cannot silently
discard the campaign worker override; candidate-file pilots retain their
separate `--pilot-jobs 1` policy.

Native R2 config now accepts `judge.mutation.jobs_max` with `jobs` as an alias;
the default is one, and `assay run --jobs N` validates `1 <= N <= jobs_max`.
Parallel execution requires a workspace reporting `cheap_clone`, reserved
for `jobs + spares + 1` live clones (`spares = 0` in this release). The
progress run event adds `jobs` and `attribution`; candidate and retry events
add `worker_index`. Verdict schema v16 remains unchanged, so its jobs field
stays at one. B157 is closed; B202 tracks the separate WP8/v17 verdict work.

The requested plan/review references `COW-PLAN-OPUS-2026-10-30.md`,
`reports/ASSAY-OPUS-REVIEW-AND-PLAN-2026-10-31.md`, and
`codex/assay-ap-common.md` were not present in this checkout. Implementation
followed the user's PR1b contract, the available PR1a continuation, and the
available backlog entries B165, B171, and B186–B188. No WP8/v17 verdict fields,
RS1/RS2, waiver edits, or B171 descendant identity changes were included.

## Verification

- Focused regression batch: **448 passed in 58.95s**. It covered scheduler
  outcome parity for killed, survived, crashed, budget-exceeded, and hung
  candidates; state/progress ordering; cross-jobs resume; no-cheap-clone
  refusal; interruption with two in-flight hosts; retry/deadline behavior;
  docs examples and anchors; B105 controls; untrusted JSON; and result-report
  wiring.
- Follow-up docs and CLI suite after the final pilot wording: **116 passed in
  24.13s**.
- `test_limit_ledger.py`: **24 passed**, with **100% line and branch coverage**
  (133 statements, 34 branches).
- Pyflakes over modified production and test modules: clean.
- `git diff --check`: clean.
- Verified the three sorted whole-source inventories
  (`self-qualification`, `self-qualification-preflight`, and
  `self-qualification-provisional`) include `_limit_ledger.py`. The B105
  exclusion-map controls passed after updating the changed `mutation.py` line
  numbers.

## Gate and live-host limits

The registered `self-qualification-preflight` and `tester-unified` gates were
not run because this task explicitly prohibits `run-gate` and Docker. The
focused ledger tests establish 100% coverage for the new module, but they do
not establish the preflight lane's package-wide 100% line-and-branch result.

Parallel windows use the existing lane cgroup and its visible ancestors; this
change does not move workers into a different cgroup. Since those counters are
shared, overlap is intentionally attributed conservatively. The scheduler
tests fake the PR1a host seam and verify cancellation with two in-flight
workers. A live tester-unified run could not verify actual kernel cgroup event
increments, ancestor visibility, btrfs clone reservation, or OS-level
termination of multiple child hosts. Those registered-gate and live-host
acceptance checks remain open.

The worktree started at `b514b169e95d67156fbcb4510481e4a1b7cb1482`, which was
the task's declared base; no merge from `main` was needed.
