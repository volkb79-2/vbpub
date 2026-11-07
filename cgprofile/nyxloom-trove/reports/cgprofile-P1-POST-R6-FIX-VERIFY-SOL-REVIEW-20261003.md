BLOCKED

# RG-55 P1 post-round-6 fix verification — 2026-10-03

This is a supplemental review, not numbered round 7. Source, tests, and gate
configuration show no new P1 defect in the reviewed delta, but the two missing
observables below prevent an unqualified provisional-integration ACCEPT under
RW-381. This verdict is neither release, publication, installation, nor host
daemon activation clearance.

## Exact candidate and direct scope

The attached `rg55-p1-final-20261001` worktree was clean before this report,
with HEAD `8e6b2475d0c4df5c9f15d0be6c29143ea21b7adb`, tree
`20c12dbe3347d8910f77f28236667ff9437a84dc`, and current `main`
`d58b01ca7ccf864f45665e7f0bb0b97dc2d53107`. The separate attached R2
checkout has the same HEAD/tree; it was not modified, switched, detached, or
used for a gate. The direct `git diff main HEAD` and three-dot comparison name
the same 15 P1 paths: `CHANGES.md`, `README.md`, `assay.toml`,
`docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `lib/summary.py`,
`run-gate.toml`, `tests/test_summary.py`, `tests/test_targets.py`, the P1
LOG/REPORT/review handoff, the post-R6 refactor review, and the survivor
supplement plus its handoff. `git diff --check main HEAD` passed. This report
is the only new worktree file; no product code, tests, or config were edited.
The candidate's `ciu.worktree-instance.json` records this same path and
branch. A read-only `ciu worktree inspect --json rg55-p1-final-20261001`
failed before returning this candidate's inspection: `[S16] ready record
lacks a closed runtime identity` at
`.worktrees/rg55-p1-r2-20261003/ciu.worktree-instance.json`. Git and
this candidate's own identity record establish the attached state; no CIU
lifecycle action or repair was attempted.

## Base propagation and Assay scope

The real read-only `./run-gate.py --base
e5e9b95c5ac8be3452c93f1066f9436347f862fd --dry-run r2` exited 0 and
expanded the container command to:

```text
/usr/bin/docker run -d --name run-gate-vbpub-r2-584051-1791013572 --init --cgroup-parent dev-gates.slice -e CGROUP_PARENT_DEV_GATES=dev-gates.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub -e 'CGROUP_PARENT_DEV_BACKGROUND=<redacted>' -e 'RUN_GATE_PROFILE_SESSION=<redacted>' --cpus 3 tester-unified:local bash -c 'set -euo pipefail && export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig && git config --global --replace-all safe.directory '\''*'\'' && bash -c '\''export PYTHONPATH=/workspaces/vbpub/.worktrees/rg55-p1-final-20261001/assay/src && cd /workspaces/vbpub/.worktrees/rg55-p1-final-20261001/scripts/cgroup-profiler && mkdir -p .assay && exec $TESTER_VENV/bin/python3 -m assay.cli run r2 --request-base e5e9b95c5ac8be3452c93f1066f9436347f862fd --resume --progress .assay/r2-progress.jsonl --verdict-json .assay/verdict-r2.json'\'''
```

The corresponding `--dry-run gate` exited 0 and printed this effective
registered host command (it did not start nested lanes):

```text
bash -c 'cd /workspaces/vbpub/.worktrees/rg55-p1-final-20261001/scripts/cgroup-profiler && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/rg55-p1-final-20261001 r0-r1 && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/rg55-p1-final-20261001 --base e5e9b95c5ac8be3452c93f1066f9436347f862fd r2 && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/rg55-p1-final-20261001 r3'
```

The configured `judge.base_source = "request"` is read by Assay. Direct
`assay plan r2` without `--request-base` refused `BAD_LANE_CONFIG` (exit 2);
the same command with a nonexistent ref refused `GIT_FAILED` (exit 2).
Run-gate itself refuses an omitted `--base` in this checkout because it has no
usable upstream/base branch. Its dry run with an invalid named ref exited 0
and merely passed that ref through; the Assay plan is the validating second
step. A request base of `HEAD` refused `BASE_IS_HEAD` (exit 3), not a green
zero-candidate plan.

The actual read-only Assay plan with the requested SHA reports `status=ok`,
candidate commit/tree exactly as above, **1,161 candidates** (limit 1,500),
two jobs, and nine in-scope `lib` files: access 84, analyze 8, liveness 119,
placement 608, serve 197, subtree 10, summary 55, targets 72, version 8.
This is a combined pre-wave source scope including later P6 code, not a
193-candidate replay or a measured runtime estimate. `git merge-base` of the
requested SHA and candidate HEAD is exactly
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`; the source diff from that
base contains both `summary.py` and `targets.py`.

**BLOCKED — missing plan observable.** Assay's `plan` JSON has no declared
or resolved base key. I inspected its output keys and the plan payload
construction in `assay/src/assay/cli.py`; it emits commit/tree/candidates
but omits base. Thus the plan itself cannot *report* the requested resolved
base as required for this review, even though argv, Git merge-base, source
inventory, and an older failed verdict's `judgment.resolved.base` all
corroborate the SHA. No R2/full gate was run and no green R2 is inferred.
Adding a plan base witness belongs to Assay outside this P1 review's edit
scope. This is a missing evidence field, not evidence that this runner
selected a different base.

## Survivor and oracle verification

The historical isolated verdict/progress/history were read separately:
`10a344e2f4685ac033f586fcb846b99e5fc2c8d5` failed R2 with 193/193
accounted, 182 killed, 11 survived, zero equivalent/timeout/crash/hang, and
exit 1. It is not a current-tree receipt. I inspected the present source
before the historical dispositions. The seven real defects are covered as
follows:

| Old mutation | Current distinguishing contract and oracle |
| --- | --- |
| AVL rebalance at +1 / -1 | Premature rotations can break AVL height and logarithmic insertion while nearest-rank output remains correct. The fixed zigzag and its mirror assert balance, recomputed height, size, and logarithmic height after each insert. An independent in-memory probe changed each new reachable child-balance comparison to its inclusive form; each mutant failed this test, then the original passed after restoration. |
| Equal-value descent on either insertion branch | Repeated readings must retain O(distinct values) rank state. The 256-identical-value test requires one node and a multiplicity/size of 256. Its internal assertion is tied to the stated memory bound, not an arbitrary root shape; rank output alone would miss the defect. |
| Later missing usage / nonpositive time delta | A valid first CPU pair followed by either invalid pair must yield `cores_max = null`, not a stale earlier maximum. The two new accumulator tests assert that output. |
| Empty proc-stat `comm` rejected | `pid () ...` is a valid field-22 record. The new fake `/proc/42/stat` test requires the parsed start time 98765. |

The four old survivors claimed equivalent are removed by a
behavior-preserving source refactor: insertion-only AVL rebalance cannot
reach a zero child balance when its parent first crosses +/-2, so the two
old child-grandchild equality choices were inert; the new comparisons use
reachable -1/+1 boundaries. The old `>` versus `>=` peak assignments at
equal contract-valid numeric readings wrote the same value; `max` now
expresses that reduction without those mutation sites. The current plan
lists new AVL boundary candidates at `summary.py:281` and `:290`, and no
compare-swap peak candidate at the new max reductions. The in-memory probe
rejected both new AVL boundary mutants. This is source/behavior evidence,
not a prediction that all 1,161 current candidates will be killed. Native
Assay would still classify any surviving equivalent mutant as `survived`;
prose cannot waive the R2 release gate.

Independent local focused tests passed 179/179 in 3.18 s. The registered
exact-HEAD R0/R1 and R3 history records both say clean, eligible, PASS,
exit 0; the caller's raw gate evidence reports 2,160 tests, 7,365/7,365
statements and 2,696/2,696 branches, and 7/7 canaries rejected. History
alone does not carry those raw totals. My read-only doctor run exited 0 with
9 OK/2 WARN/0 SKIP/2 INFO; the caller's earlier doctor receipt was
8 OK/2 WARN/2 SKIP/2 INFO. The two warnings are the down singleton/coarse
rusage path and linked-worktree host Git view, not gate failures. The
plan/direct probes and local pytest are not registered gate verdicts.

## Contract, documents, and live boundary

The contract's nearest-rank, per-pair CPU, null, and RW-21 scope rules agree
with the changed summary code and tests. Both interface contract mirrors
remain byte-identical (SHA-256
`ff3fc49fb7667d19e23eaa5dc4c312eefa85ee2558e5631c52a5df32b836fc50`).
README, design guide, consumer guide, run-gate and Assay config consistently
name the explicit P1 base and distinguish provisional integration from R2
release evidence. The guide correctly says percentile state grows with
distinct readings, not constant memory. The historical review handoff's
2026-10-02 statement that the effective delta is only tests/reports is
superseded by this direct 15-path diff; it must not be used as current scope.

Nonblocking follow-ups for this provisional decision are sustained-load
capacity measurement for the O(distinct-values) rank state under the 1 GiB
daemon cap, and a dated clarification of that stale historical handoff
snapshot. Neither is a substitute for the two blocked observables. The
doctor warnings likewise require no P1 code change to interpret the short
gate PASS receipts.

`d48d1ed8e6b411fb2c20e920d62de94476347b29` is an ancestor. Every
listed runtime/deployment file besides `lib/summary.py` is byte-identical
to that P6 repair tree; the `lib` tree IDs differ
(`dda43d5f5443e1e4fe5f3974729b66a5346cb983` versus
`70000e101013d135bbafa41d896f3d69d978cf24`). P6's reviewer-owned
live evidence therefore transfers for unchanged placement/restore/socket
behavior on its tested inputs: the exact scope and caps were read back,
the PID returned to origin with `summary.placement.error=null`, owned paths
vanished, an unterminated socket command was ignored, and a valid version
request succeeded. Round 6 separately observed `ctl version`, 100 MiB
container and 80 MiB shared summaries, report HTML, and helper PID identity
on an older summary implementation. None certifies current summary-runtime
values or a current image.

**BLOCKED — current reviewer-owned live summary/version probe.** I read
`/proc/pressure/memory` (`full avg10=0.25`, within the <=5 launch limit),
the authored `CGROUP_PARENT_DEV_INTERACTIVE=dev-interactive.slice` and
`CGROUP_PARENT_DEV_GATES=dev-gates.slice`, and the current Docker/buildx
inventory. The cockpit's `systemctl show ...` is a stub that prints
"systemd is not running in this container" while returning 0;
`/sys/fs/cgroup/dev.slice/{dev-interactive,dev-gates}.slice` is absent in
its private cgroup view, and neither host system-bus nor systemd-private
socket is mounted. Thus I cannot read back the current host units' loaded
state before a container launch. `docker image inspect cgprofile:local`
reports OCI revision `07489a5e18bf7f8056e51efacb96c4580d766d1c`,
not this candidate. The exact missing observables are a verified *current*
loaded authored parent and a current-code reviewer-owned daemon's `ctl
version` plus at least one summary-producing session with immediate
three-CPU cap readback. I stopped only this probe and launched/built no
container; no host namespace, singleton, unrelated container/network, or
`/workspaces/dstdns` was touched. The prior host unit state in RW-398 is
historical, not a fresh readback. Live DAMON remains unavailable as proof:
round 6's kernel commit returned EINVAL, so neither live kdamond index
allocation nor DAMON overhead was measured.

## Disposition

There is no scoped code fix from this review; prior code gate receipts are
not invalidated by an implementation change here. The controller must
inspect/commit this report, recheck the resulting exact tree, obtain the
missing plan-base witness or explicitly reconcile that evidence requirement,
and complete safe current-code live version/summary evidence before using
RW-381 for provisional integration. Current-tree R2 and the registered full
gate remain separate release holds; P3 measured DAMON overhead is also
open. Abrupt daemon loss may leave a DAMON owner (S5), so this report gives
no daemon lifecycle or activation clearance. No merge, release, publish,
install, full gate, or R2 run occurred in this review.
