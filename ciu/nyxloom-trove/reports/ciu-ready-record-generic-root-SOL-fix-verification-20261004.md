# CIU generic-root ready record — Sol fix verification (2026-10-04)

**Reviewed tree:** `fix/ciu-ready-record-generic-root-20261004` at
`467152fd7c7dea4b9949712b6dfb38cd6b6d05ae` (merge base with `main`:
`f516d7e52d11a46f73dde89d2015be05b735511b`). The worktree was clean
before this report was written. This is continuation of review round 1.

**B9 verdict: CLOSED. Overall branch verdict: REJECT until R1-01 below is
repaired.**

## B9 — marker path classification

`worktree.py:370-404` now calls `lstat()` first. `FileNotFoundError` at that
step is treated as marker absence and permits the generic null/null record;
for a stable existing root this is the missing-marker case.
A present regular file, including a symlink whose target resolves to a regular
file, is classified as a CIU marker; `worktree.py:465-470` then refuses a
ready null/null pair. A dangling symlink raises while being resolved and is
converted to `WorktreeError`. Directories and other non-regular entries also
raise, as do permission and other lookup errors. The same predicate is used
by the reader and the allocation writer (`worktree.py:3643`). Thus the prior
broken-symlink reproducer no longer aliases a present marker path to absence.

`test_ciu_worktree_lease.py:135-245` covers absent and regular markers, a
symlink to a regular file, a dangling symlink, a directory, partial runtime
identity, and an injected marker lookup denial. The missing-marker test is a
positive oracle, and the caller's live read-only probe reports that this
branch's source inspected the real generic-root null/null record as `ready`.
The docs and CIU-112 entry now state the same exact-path distinction. A marker
or its parent can still change after the lookup; these calls do not promise
an atomic filesystem snapshot against an external writer. A vanished checkout
also fails the subsequent Git-status inspection. This race does not recreate
the deterministic dangling-symlink failure B9 identified.

## Remaining blocker from the same review: R1-01

The branch still writes an aggregate `ready` record in
`worktree.py:3643-3646` before `create()` prepares discovered nested roots at
`worktree.py:3877-3939`. A nested preparation failure changes the record to
`recovery-required` at `worktree.py:3940-3959`, but `ensure()` at
`worktree.py:4015-4022` calls `_finish_allocation()` again. For a markerless
aggregate root that function immediately writes `ready`, without rerunning
or verifying nested-root preparation. A concurrent inspector can also see
the initial premature `ready` value.

**Reproducer:** commit a valid marker under `services/api` in a Git family
whose top level has no CIU marker; make that nested root's environment
generation fail once; then restore the input and run `ciu worktree ensure
NAME --json`. It returns `ready` even if `services/api/ciu.instance.generated.toml`
was never produced. Minimum repair: keep the aggregate record non-ready until
all nested roots and shared metadata are prepared, and make `ensure` repeat or
verify that work before it reports `ready`. Add a failure-then-ensure oracle
and an inspection oracle for the preparation window.

## Other reviewed changes and evidence limit

The `test_spec_contracts.py` fixture now initializes Git and commits its CIU
marker, matching the current Git-backed identity requirement. The governance
test edits retain `write_iops=999` in the explicit override case, add explicit
`read_iops=0` for the derivation case, and assert that an uncapped default emits
no `blkio_config`; they match `governance.py`'s explicit-opt-in behavior.
`run-gate.toml` declares a three-CPU cap on the gate lane, separate from CIU
product governance defaults.

Caller-supplied evidence on this exact HEAD: `./run-gate.py ciu` exited 0 at
2026-10-04 04:53:52Z with R0/R1/R2/R3 PASS; R1 covered 38/38 changed lines
and 14/14 branches; R2 killed all 9 planned candidates; focused governance
tests reported 221 passed. The live probe inspected
`rg55-p6-r2-ciu` as `ready` through candidate source. I did not rerun tests,
gates, Docker, or the live probe during this review. These results do not
exercise R1-01's nested-root failure and retry path.

## Follow-up on 2026-10-05

The R1-01 blocker above was implemented after this report: the aggregate CIU
record stays `allocating` until every root at the saved allocation commit has
readable facts and the shared root-entry list is persisted. The failure/retry
and concurrent-inspection oracles are in
`tests/tests/test_ciu_workspace_adversarial_review.py`.

Reviewing the recovery path then exposed the adjacent CIU-107 case: an
interrupted adopt with no failure status could still reach `git reset --hard`,
and legacy root discovery could use a moved checkout's `HEAD`. Commit
`84f286967` compares resumed worktrees with `fork_point_sha` or, for older
records, the shared workspace's `base_commit`; a mismatch refuses without
resetting. Added a real-Git regression that preserves a commit made after an
interrupted adopt, plus a same-target recovery case and legacy-root commit
oracles. `py_compile` and `git diff --check` pass on that commit. Its registered
oracles. Follow-up commit `d00f89f76` demotes an unverifiable
legacy `ready` record before refusing, so inspection no longer retains a false
readiness claim after recovery discovers a moved allocation target.
`py_compile` and `git diff --check` pass on the updated source and tests. Its
registered gate is still pending; the 2026-10-04 gate evidence above is not
evidence for the updated tree.
