# cgprofile-P1-DAEMON — adversarial review handoff (RG-55 wave, package P1)

**Reviewer:** a genuinely fresh Sol xhigh session, not a fork of the
implementer or controller. The caller selects and verifies the route from the
invocation/session metadata; do not ask the reviewer to attest its own route.
**Your job is to BREAK this before provisional merge.** This is a new review
cycle: historical rounds 1–3 reviewed earlier trees; round 4 has already
rejected the candidate and its fixes are now committed. Continue at round 5;
rounds 4–6 are the three-round cap. Fix-verification resumes the same live
reviewer; if it is gone, the controller seeds a fresh reviewer with all prior
review records. The operator authorizes scoped fixes on this isolated branch.
You may not merge, release, tag, publish, install, or start/stop the main
daemon. Records:
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-round<n>.md`.

### Controller status after round 4

Round 4 rejected tree `148481e4af504fc416679ed2fd8dffe380709de6`; preserve
its report. B1's stop-time rescan was replaced by ingestion-time summary
reducers and exact order-statistic trees, committed as `2e130da3`. The
single-tree DAMON percentile optimization is committed as `c81b2837`; focused
`test_summary.py` + `test_serve.py` passed on exact tree
`db044d4d37f017162976c78f624885ee5f595923` (205 passed, 1 skipped in 15.70
s). The old isolated R2 on `450fe53d` failed with 12 genuine proc-identity
oracle gaps. Direct parser/helper tests are now present in commit `01912a91`
and passed 167 focused target/helper tests. The separate isolated PASS on
`1080ac2f` is not transferable to this release candidate. Main is now
`8df26ed143925c882e65a1fe673d1447055bd754`; P1 reconciled it in merge
`0485d82e475930fcbf74040a0138268b437b30b6`. The latest main delta is one
Nyxloom-only commit touching the P113 log and Claude Code adapter test.
R0/R1, R3, and doctor passed on the prior candidate
`bd915d7f98929ef584deb8d65c0d00a8480e4193` (1,421 tests, 5,140/5,140
statements, 1,780/1,780 branches; 7/7 canaries rejected; doctor 0 failures,
2 warnings), but those receipts predate this latest-main merge and are not
exact-tip evidence. Rerun all three after the current checkpoint commit.
The operator also supplied
direct-host, read-only evidence that
`dev-interactive.slice` and `dev-gates.slice` are loaded at their authored
`/dev.slice/...` paths, both with `Delegate=no` and five-CPU quotas. The same
query also confirms `cgprofile.slice` loaded at `/cgprofile.slice`,
`Delegate=no`, unlimited CPU quota, and a 1 GiB memory limit. Exact values and
provenance are in controller rulings RW-397/RW-398 and P1 LOG/REPORT. This
closes the missing unit-state observable in B2, pending reviewer confirmation
and the required reviewer-owned live probes. Final exact-tip R0/R1, R3, and
doctor remain pending after the checkpoint refresh. The reviewer must
complete the required reviewer-owned live probes and round 5 seeded with
rounds 1–4. Do not claim acceptance or merge until the reviewer verifies the
preflight and live probes. Current-tree R2 and full gate remain release holds
and may run asynchronously in a separate attached CIU worktree after
provisional merge, under RW-381; no release or daemon activation before both
are green and the survivors are dispositioned.

Branch `rg55-p1-release-review-20260930`, worktree
`/workspaces/vbpub/.worktrees/rg55-p1-release-review-20260930`, project dir
`scripts/cgroup-profiler/`. The candidate is reconciled with current main
`8df26ed143925c882e65a1fe673d1447055bd754` in merge commit
`0485d82e475930fcbf74040a0138268b437b30b6`; the latest main delta modifies
only Nyxloom's P113 log and Claude Code adapter test, with no overlap in P1
product files. The controller supplies the exact final review tip after
evidence updates and fresh short gates. Verify clean status and HEAD before
review. Review the FULL P1 diff
`8df26ed143925c882e65a1fe673d1447055bd754...<tip>` — every changed P1
file, every type. If main advances before dispatch, reconcile first and rerun
all required short gates.

### Current checkpoint (2026-10-01; supersedes older base references below)

Main is `8df26ed1...`, reconciled in `0485d82e...`; the full-review base is
therefore `8df26ed1...`, superseding `6617c44e...`, `6ee297a4...`, and older
references in historical sections. R0/R1, R3, and doctor passed on
`bd915d7f...` (1,421 tests with full line/branch coverage; 7/7 canaries
rejected; doctor zero failures), but that commit predates this latest-main
merge and is not the final receipt. After committing this checkpoint, run all
three on the resulting exact candidate; preserve the tree until review is
complete. The operator has explicitly
authorized required gate runs regardless of memory PSI, so PSI is not a gate
launch veto or a functional verdict input. Keep exact cgroup placement, CPU
caps, the mutation-slot limit, and unrelated-container protections below.

The reviewer may commit the round-5 artifact in this worktree. If that adds
a commit after review, it changes the exact merge tip: rerun the required
short gates on the resulting tip before provisional integration. Any code
repair must be fix-verified by this same reviewer session while it is alive;
only after accepted fix verification and fresh exact-tip gates may the
controller provisionally merge.

## Phase 1 — BLIND (before any LOG/REPORT/BRIEF)

Read, in this order: the plan of record
(`run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
§2 D-1..D-16, §3, §4, §5-P1, §10), the contract
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §1–§7) and
`fixtures/rg55/README.md`, the controller log's Rulings section
(`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
— RW-3, RW-7, RW-9, RW-11, RW-13..RW-16, RW-19, RW-21, RW-23,
  RW-47, RW-48, RW-318..RW-328, RW-381..RW-392 and RW-393..RW-405 bind this
  package), the
implementer handoff (`cgprofile-P1-DAEMON-HANDOFF.md`, what was asked), then
the diff itself — `lib/summary.py`, `lib/subtree.py`, `lib/damon.py`,
`lib/serve.py`, `lib/store.py` changes, `cgprofile.py`, the shim, the
Dockerfile + build scripts, the ciu templates, `cmru.toml`, the root
`cmru.orchestration.toml`, `pyproject.toml`, every test file, docs. Form
your own view of correctness, safety and contract conformance BEFORE reading
the implementer's narrative. Run your OWN sweeps (the handoff biases
coverage, not conclusions).

## Phase 2 — RECONCILE against the implementer's claims

Read `cgprofile-P1-DAEMON-LOG.md`, `-REPORT.md`, every `-BRIEF-n.md`; check
each claim against what you found; list claims you could not verify.

## Attack surface (minimum; add your own)

1. **D-15 daemon safety.** Every write the daemon can issue: is there ANY
   path outside `WRITABLE_ROOTS` (sessions dir, `/sys/kernel/mm/damon/admin`)?
   Grep for `open(..., "w")`, `write_text`, `os.replace`, `shutil`, `subprocess`
   in `lib/serve.py`, `lib/damon.py`, `lib/store.py`, `cgprofile.py`. Is
   `--cap`/`TempCaps` reachable from `serve` or `ctl` by any argv? Does the
   compose template keep PID and cgroup namespaces private, use
   `network_mode: none`, omit the Docker socket, author its cgroup parent,
   mount host `/proc` and cgroup v2 explicitly read-only, retain only the
   DAMON sysfs write surface, and set the documented memory limits? Any host
   namespace mode is a blocker. Does SIGTERM restore `nr_kdamonds` and never
   tear down a foreign kdamond? Can a crash mid-session leak a kdamond?
2. **Contract conformance.** Every `ctl` verb's response vs contract §2 and
   the goldens; exit codes 0/2/3; `contract: 1` on every response incl.
   errors; ONE JSON document on stdout (plant a stray print and watch the
   test catch it); `stop` within 30 s after a LONG session (inject a clock:
   1 h of 1 s samples — is the summary incremental or recomputed?). Inspect
   the exact percentile state separately: the current order-statistic trees
   retain one node per distinct observed value (O(unique values), not bounded
   constant memory). Do not call this bounded merely because raw sample
   objects are discarded. Evaluate realistic intervals and configured
   concurrency against the daemon's 1 GiB memory limit; report the actual
   growth/claim boundary and whether it violates any contract or safety claim.
   idempotent `start` by (container id, token) → `reused: true`; idempotent
   idempotent `start` by (container id, token) → `reused: true`; idempotent
   `stop` → `already_stopped: true`; `too-many-sessions`; `target-not-found`.
3. **§7 arithmetic.** Nearest-rank (N=5 gotcha), `source` per scope
   (`memory.peak` vs `sampled-max`), baseline subtraction floored at 0,
   `cores_max` per interval pair, PSI deltas /1e6, `limit_drift` counting,
   null discipline (unreadable at ONE end → null, never a delta against
   nothing; RW-7 last-SUCCESSFUL read). Plant 6+ mutants of your own in
   `lib/summary.py` (swap max/min, drop a /1e6, off-by-one in rank, wrong
   floor) — every one must be caught by the suite; record which test caught
   each.
4. **Subtree resolver.** environ read of a vanished/zombie pid; pid reuse
   across discovery ticks; a descendant that re-parents to PID 1 (daemonized
   worker) — is it lost? (disclosed via `targets_seen` is acceptable, silent
   is not); `/proc/<pid>/task/*/children` absent → ppid map path exercised.
5. **DAMON pool.** Two concurrent sessions live (indices 0 and 1); stopping
   0 while 1 lives writes NO `nr_kdamonds`; reuse of 0; shrink at the end;
   `status: "unavailable"` with reason when sysfs is read-only or the module
   is absent; RW-15 no-token recommit; the RW-14 real report (`ctl report`
   renders the existing interactive HTML, not a stub — open the file).
6. **Registry/retention.** Restart with live sessions → `aborted:
   daemon-restarted` with partial summary; retention never removes a live or
   in-flight session; `--keep-days` vs `--keep-sessions` interplay.
7. **Image and stack.** No network fetch at runtime (grep the Dockerfile and
   scripts for pip/curl/wget at run time); `cgprofile` wrapper routes verbs
   correctly; OCI labels; `build-push.py --push` refuses without a version;
   ciu: render from the standalone root, confirm the authored `cgroup_parent`
   survives governance, the refusal when `$CGROUP_PARENT_DEV_INTERACTIVE` is
   unset (unset it and render), the singleton name, restart policy; cmru:
   `cmru status --project cgroup-profiler` clean; the release gate lanes.
8. **Hollow tests.** For every new test file: mutate the subject, watch the
   test. Coverage 100% line+branch is necessary, not sufficient. The
   contract-fixture identity test really compares bytes against
   `run-gate-project/nyxloom-trove/fixtures/rg55/`.
9. **Rulings honored.** RW-3 one-liner present; RW-13/RW-15/RW-16 as ruled;
   RW-19/RW-21/RW-23/RW-47/RW-48/RW-318..RW-328 recorded and reflected in
   code/tests and exact gate-launch evidence. Prior P1 R2 results are
   tree-specific: `1908316b` ended `BUDGET_EXCEEDED/CANDIDATE_HUNG`, while
   `4e5ff2d2` passed 125/125; neither is current-candidate evidence. The
   R2/R3 `resources.cpus = "3"` declarations must produce
   `NanoCpus=3000000000` on their live gate containers. CP-4..CP-7 entries
   must be real and honest. Read `.assay/verdict-r2.json` separately when it
   exists. The historical R2 PASS on `4e5ff2d2` judged only that exact tree;
   the current candidate has no transferable R2 receipt. Under RW-381, R2
   and the full gate may follow provisional merge in a separate attached CIU
   worktree; their absence is a disclosed shipping hold, not a reason to
   misstate evidence or skip code review. Independently attack the current
   behavior around `lib/summary.py` arithmetic and pool teardown, CLI request
   omission/default semantics, helper PID identity, daemon recovery, and
   systemd-owned placement.
10. **Docs and trust boundary.** README, DESIGN-GUIDE, CONSUMERS, PROTOCOL,
    and both byte-identical interface contracts must describe the shipped
    code. D-32 keeps the direct daemon-to-systemd bridge and no broker. This
    is not a least-privilege claim: Docker-group/run-gate operators already
    have host-administrator authority, while a compromised privileged daemon
    retains its declared system-bus, cgroupfs, and DAMON write authority.
    Review that this limitation is stated accurately, without implying a
    broker or kernel-enforced containment exists.

## Live probes (you run them yourself; host rule below)

- `python3 build-push.py --build` (or confirm `cgprofile:local` is current
  for the tip: compare the image's revision label to the tip).
- Use a separate, uniquely named review-daemon container built from this tip;
  never replace, stop, or `ciu down` an existing `cgprofile-host-daemon`.
  Derive its argv and explicit mounts from the rendered candidate Compose:
  privileged, private PID/cgroup namespaces, `network_mode: none`, read-only
  host `/proc` and cgroup v2, and the separate DAMON sysfs mount. Verify the
  authored cgroup parent is a loaded unit and apply/verify the 3-CPU cap on
  that exact name. Check `ctl version --json` verbatim.
- Ephemeral probe with DAMON on (100 MiB held 12 s in a throwaway
  `cmru-enroll-fixture:local` container under the loaded `dev-gates.slice`,
  capped at 3 CPUs, token set) → summary sanity (peak ≥ 100 MiB,
  `source: memory.peak`, cpu > 0,
  damon on with hot bytes or `unavailable:<reason>`); `ctl report` on it →
  open the HTML.
- Shared probe (sleep container + `docker exec -e RUN_GATE_PROFILE_SESSION=…
  … 80 MiB`) → `targets_seen ≥ 1`, `peak_over_baseline ≥ 70 MiB`,
  `source: sampled-max`.
- Two sessions concurrently (both probes at once) → two kdamond indices,
  independent summaries.
- **Helper `pid:N` identity through private namespaces.** From a reviewer-owned
  workload with private PID/cgroup namespaces and a process kept alive for the
  probe, run helper-mode target resolution and a short helper-mode collection
  for that process. Confirm the resolved target is kind `pid` (not merely its
  container), the sample contains that one helper-visible PID, and DAMON is
  given the same PID when available. If DAMON is unavailable, verify and record
  its explicit reason; do not claim the DAMON assertion passed. Also exercise
  the fixture-backed refusal for a process outside the selected subpath. This
  probe must use only reviewer-owned containers and scratch output.
- `finally`: remove only the exact reviewer-owned workload and daemon
  containers (and their dedicated scratch data); leave any pre-existing
  singleton untouched.

### Shared-host isolation (binding)

- Do not run `ciu up`/`ciu down` for review probes. Do not issue Docker
  `network create`, `connect`, `disconnect`, or `rm` commands. A CIU probe may
  attach the running cockpit container to its generated network; that
  attachment is shared state, not reviewer-owned cleanup.
- Run only uniquely named reviewer-owned probe containers, with
  `--network=none`, the exact verified loaded cgroup parent, and a 3-CPU cap.
  Reach the reviewer-owned daemon with `docker exec`; do not add network
  connectivity to make a probe convenient.
- Never alter or clean up any pre-existing container, network, or attachment,
  including the running cockpit `dstdns-devcontainer-vb`. If a live probe
  cannot be completed without touching shared state, record the exact missing
  evidence and stop that probe; do not improvise a recovery or cleanup.

## BLOCKED rule (mechanical)

If a required live probe cannot be performed using only reviewer-owned
containers, or the authored loaded cgroup parent/systemd unit state does not
match the documented contract, stop that probe and write `BLOCKED: <exact
condition>` with the missing observable and attempted read-only checks in the
round record. Do not change host units, join a host namespace, or substitute
another carrier. Continue the code review and report the evidence gap; do not
invent a product decision. If a true product choice remains, state it as a
decision ask for the controller.

## Test-oracle constraints for any repair

If a repair adds or changes tests, obey all of these constraints from
`nyxloom/reference/AUTHORING.md` §3b:

**A. Nothing may make the verdict depend on how fast the machine is.**
- No deadline/sleep/elapsed-time/iteration-count assertion as an oracle.
- Wait on a real synchronization point (`join()`, an `Event`, a drained queue).
- Best: remove the wait; extract and directly call the deterministic step.
- A timeout is only a generous failsafe (60s, not 3s), never the verdict.
- If a test fails on a slow machine, fix the test/race; never widen its timeout
  or raise cgroup weight/add CPU to get a pass.

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- Restore process-global state (`os.environ`, logging config, module attrs,
  singletons); do not patch lazy `__getattr__` objects via `setattr`.
- Teardown restores prior shared state, never destroys it; use fresh `tmp_path`
  and assert cleanup restored what it found.
- When a failure appears only in parallel, first look for earlier-test
  pollution before assuming a race.

**C. No hollow tests.**
- No `pass`, no assert-only-no-exception, no call-count/private-attribute/log
  trivia, and no weakened/deleted assertion to get past a failure.
- Assert the behavior and observable contract; where a check guards a real
  crash, prove that crash with a controlled broken implementation.

**D. No coverage evasion.**
- No no-cover pragma on changed lines, including a comment that merely
  describes the prohibition.
- Do not exclude an `except` body and assume that covers the clause.
- Restructure genuinely unreachable code so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- No real external network, registry, or model endpoint in a unit test.
- Do not assert on `datetime.now()`/`time.time()`; inject or mock boundaries
  and keep offline as the default.

**F. No predicted measurements.**
- Do not predict coverage/mutation numbers, missing-line lists, or
  permanently-uncoverable branches from reasoning about rendered reports.
- The coverage `Missing` column is not a complete branch-arc list.
- Assert policy (coverage floor, lane tier, decision); obtain measurements by
  running the project's real judge. Prove achievability/unreachability with
  the tool on real or synthetic code, not by reading a report.

For every test, ask whether it could flip on a slower machine, different
worker, or different order. If yes, it is not yet an oracle.

## Verdict

`ACCEPT` / `ACCEPT-conditional` / `REJECT` with numbered blockers (B1..),
each with file:line evidence and a concrete prescription; non-blocking
findings (S1..) separately; product calls named as decision asks for the
controller, never improvised. Claims you could not verify listed as such.
Write the round file, then return the verdict line first in your message.

## HOST LOAD (binding)

8 cores shared with a production game server; host contention is an allowed
condition and must not alter a functional verdict or mutation classification.
The operator has authorized gate execution regardless of memory PSI; do not
use PSI or scheduler delay as a verdict input. The host `dev-gates.slice` is
loaded and capped at 5 CPUs; at most 2 mutation lanes may run estate-wide
until RW-194's admission evidence changes that limit, each with its own
unique exact container name and immediate verified `docker update --cpus=3`.
pytest is serial and load-niced; do not tune the scheduler to make a test pass.
No container may use
host PID/cgroup/network namespace modes. Read-only access to
`run-gate-project/` is required for contract/ruling context; do not edit it
as reviewer. Never touch `ciu/src/` or `/workspaces/dstdns`. Remove only
your exact temporary containers in a `finally`. Keep a CIU-managed checkout
attached to its recorded branch. For an exact-tree run, freeze that branch at
the judged commit and do not commit or move it until the run/resume ends; use
a separate worktree for fixes. Do not detach a managed CIU checkout to pin a
tree.
