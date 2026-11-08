# RG-55 P1 daemon adversarial review — round 5 (2026-10-01)

BLOCKED

## Target and review order

Starting worktree `rg55-p1-release-review-20260930` was clean at
`1063fc5f5d1853a40ad57db3bc32074a3bdb2256`; `main` and the merge base
were `8df26ed143925c882e65a1fe673d1447055bd754`. I captured the full
name/status/stat diff before editing: 19 paths at the starting tip, 22 paths
after repairs. I read the generic P55 packet and P1 review handoff in full,
then followed the blind order: plan §§2–5/10, frozen contract §§1–7 and the
fixture README, selected controller rulings through RW-405, implementer
handoff, and the complete P1 source/test/docs diff. I formed findings before
reading the P1 LOG, REPORT, briefs, and prior rounds 1–4. The latter records
are historical evidence only.

The selected worktree alone contains these review repairs:

| Commit | Repair |
| --- | --- |
| `aa4d11734d31b9633428d95f36a0e2d29c4b0a69` | Release provisional DAMON owners and discard partial sessions on failed start; refuse session-ID collisions. |
| `7d7171b600dbad1e172746ff6c07787a920c6d07` | Replace the probabilistic CP-4 run-ID oracle; mark CP-4 fixed and regenerate its backlog index. |

The final artifact commit, final HEAD, and exact-tip receipts are recorded
below after final verification. No merge, release, tag, publish, package
install, or main-daemon lifecycle action was performed.

## Initial blockers and same-session fix verification

**B1 — resolved in `aa4d1173`: failed start leaked an active owned DAMON
kdamond.** Starting `lib/serve.py` acquired DAMON before appending sample
zero and writing `manifest.json`; a post-acquisition storage failure returned
before registry publication and without `DamonSession.__exit__`. Normal
`stop`, shutdown, and restart recovery could not find that owner. Failure of
`thread.start()` after session construction had the same leak. The repair
wraps all post-acquisition assembly and thread launch, releases the owned
DAMON slot, removes the partial session directory, and preserves the daemon
fault. `tests/test_serve.py::test_failed_start_releases_owned_damon_and_permits_clean_retry`
injects manifest and thread failures with DAMON on/off, checks ownership
release and absence of a live/partial registry entry, then proves a retry
can start. The focused source run passed 377 tests, 1 skipped.

**B2 — resolved in `aa4d1173`: session-ID collision could overwrite a live
owner and mix series.** The timestamp plus four random hex digits is not a
uniqueness guarantee; `RunDir(create=True)` accepts an existing directory.
The repair rejects an ID present in either the in-memory registry or the
session root before any second series write. The deterministic tests
`test_session_id_collision_preserves_first_live_session_and_its_series`
and `test_session_id_collision_with_retained_disk_record_does_not_mix_series`
check both collision surfaces. They passed in the focused run and registered
R0/R1. The four-hex public ID shape remains unchanged.

**B3 — resolved in `7d7171b6`: CP-4 test issued a random gate verdict.**
The first registered R0/R1 on clean `aa4d1173` exited 1 only because
`tests/test_store.py::test_new_run_id_is_unique_even_for_the_same_instant`
observed 49 distinct values in 50 draws from a 65,536-value suffix space;
1,426 other tests passed. A collision is valid generator output. The new
oracle injects distinct entropy bytes under one timestamp and checks their
encoded suffixes. Its focused four-test set passed; CP-4's backlog entry is
fixed with the actual outcome. The subsequent registered R0/R1 passed.

## Independent code, contract, and oracle review

| Surface | Observation |
| --- | --- |
| D-15 / image | `serve` and `ctl` have no cap-changing argv. Authored Compose uses private cgroup and default private PID namespaces, `network_mode: none`, no Docker socket, explicit read-only host `/proc` and cgroup-v2 mounts, separate DAMON sysfs mount, `dev-interactive.slice` parent, 1 GiB memory and 2 GiB combined memory/swap. Dockerfile's pip work is in build stages; no runtime install. Source `build-push.py --push` resolves a release version before login/push. No host-namespace mode found. The only direct daemon write roots are its session storage and DAMON admin sysfs; the review did not treat those mounts as kernel-enforced containment. |
| Wire contract | The source/tests cover one JSON response, `contract: 1` on successes/errors, exit 0/2/3, strict client response validation, idempotent token starts/stops, target-not-found and too-many-sessions, start sample zero, registry/restart/retention, and real HTML report rendering. Tests for long-run stop use injected samples and summary state; normal `finalize` assembles accumulated state rather than rereading raw series. |
| Summary arithmetic | Both frozen five-frame goldens reproduced for `container` and `container-shared`; tests cover last successful reads, null discipline, baseline flooring, interval-pair CPU rates, PSI microsecond conversion, limit drift, and nearest-rank percentiles. The two contract copies are byte-identical (`sha256 a4ee627fbc144be19fd10c1c199f83dfb4b4ba9188eb4907ddf7e192fc0bdd0a`); fixture identity is a recursive byte comparison, not path existence. |
| Target and DAMON | Source/tests cover private namespace PID identity, PID reuse/refusal, absent `children` fallback, descendant attribution, two pool indices, no shrink while another owned index is live, and refusal to claim a foreign slot. The reviewer-owned real helper and pool observations remain missing as described below. |
| Stack and release configuration | Source-backed `cmru status cgroup-profiler --config cmru.orchestration.toml` exited 0 and showed no last tag, next guess `cgprofile-v0.1.0`; the handoff's `--project` syntax is stale for current CMRU and exits 2. `ciu check --define-root .` exited 0, but reported **zero rendered stacks**, so it is not evidence that a live Compose instance has the authored values. No render or lifecycle command was used to disturb the singleton. |
| Docs | README describes the daemon surface, DESIGN-GUIDE explains the private views, authority and growing exact percentile state, and CONSUMERS gives CLI/JSON adoption steps. The `PROTOCOL.md` cited by the handoff is a P6 surface, absent on this P1 tree as round 4 recorded. The `test_summary.py` module docstring's stale reference to nonexistent `test_hand_mutants.py` was corrected in this review. |

I planted seven temporary source mutants in a scratch copy, restoring the
original bytes after each; none changed the judged tree. All seven were
killed by `tests/test_summary.py::TestGoldenReproduction` against the
frozen goldens: shared peak max→swap max (shared golden), nearest-rank
ceil→floor (both), CPU `/1e6`→`/1e3` (both), PSI `/1e6`→`/1e3` (both),
limit-drift count→zero (both), shared counter-delta direction reversed
(shared), and baseline subtraction direction reversed (shared). These are
seven manual targeted probes, **not** a current-tree R2 verdict.

### Non-blocking review notes

**S1 — exact percentile memory grows with distinct values.**
`lib/summary.py` maintains one AVL node per distinct memory-current value
and one per distinct value in each of four DAMON classes, per live session.
That is O(unique values), not constant space; the docs accurately say so.
At the authored one-second interval and 16-session maximum, one day at
five all-unique streams would permit about 6.9 million nodes. On this
CPython build, a node alone is 80 bytes and an integer about 28 bytes, so
the rough node/value floor exceeds 700 MiB before allocator, tree and daemon
overhead; the 1 GiB cap could then become material. This is an analytical
upper-load scenario, not a measured OOM or an observed ordinary lane.
The normal stop path itself has O(log n) rank reads rather than O(samples)
replay, but the "any length" response promise presupposes that the bounded
daemon survives. Capacity should be tracked for sustained high-concurrency
use; no constant-memory claim is certified here.

**S2 — historical mutation receipts are tree-specific.** Earlier P1 R2
records include a complete 125/125 result on `4e5ff2d2` and an earlier
12-survivor result on `450fe53d`. Neither judges this candidate. Current-tree
R2 and full gate have not passed. RW-381 permits them after provisional merge
in a separate attached CIU worktree, but both remain release holds; this
review does not certify release.

**S3 — operational impact remains unresolved.** The operator reported Docker
stats unavailable for about 1–2 minutes near the preceding R0/R1 run. A
3-CPU gate container was present; inspected teardown journal lines showed
client-cancelled `/top` reads and a broken-pipe container JSON read, with no
`/stats` endpoint error or daemon restart in that interval. A 294-second
wrapper elapsed time versus about 90 seconds in-container does not establish
causality or prove the interruption unrelated. No unrelated live container
was touched to investigate it.

## Host facts, gate evidence, and live-probe hold

I independently queried the host system bus read-only from uniquely named
reviewer container `cgprofile-r5-unit-20261001-0254`, run with
`--network=none --cgroupns=private --cgroup-parent=dev-gates.slice --cpus=3`
and default private PID namespace. Its exact Docker inspect showed
`CgroupParent=dev-gates.slice`, `NanoCpus=3000000000`, network `none`, private
cgroup. `busctl` confirmed `dev-interactive.slice` loaded at
`/dev.slice/dev-interactive.slice`, `Delegate=false`, 5-CPU quota,
`MemoryMax=8589934592`; `dev-gates.slice` loaded at
`/dev.slice/dev-gates.slice`, `Delegate=false`, 5-CPU quota,
`MemoryMax=1610612736`; and `cgprofile.slice` loaded at
`/cgprofile.slice`, `Delegate=false`, unlimited CPU quota,
`MemoryMax=1073741824`. I removed only this exact query container. This
closes round 4's missing unit-state observation without joining a host
namespace. It does not prove daemon runtime behavior.

On clean `7d7171b6`, registered `nice -n 19 ionice -c 3 ./run-gate.py r0-r1`
exited **0**: 1,427 passed, 4 warnings, 5,156/5,156 statements and
1,786/1,786 branches. The separate
`scripts/cgroup-profiler/.run-gate/history.json` record says clean exact
commit, PASS, exit 0, eligible, started `2026-10-01T02:58:44Z`, duration
98.477 s. Its exact container `cgprofile-gate-252285-1790823527` had
`CgroupParent=dev-gates.slice` and `NanoCpus=3000000000`.
Registered R3 on that same clean tip exited **0**, 7/7 canaries rejected,
zero survived; separate history says clean exact commit, PASS, exit 0,
eligible, started `03:00:37Z`, duration 14.8 s. Exact container
`run-gate-vbpub-r3-255968-1790823637` had the same parent and CPU cap.
`./run-gate.py doctor` exited 0 with 13 checks: 9 OK, 2 WARN (linked-worktree
host-lane Git view and absent daemon singleton), 0 failures, 0 skips, 2 info.
Final-tip rerun results appear below after the artifact commit.

**BLOCKED: exact-tip image and required reviewer-owned live daemon,
ephemeral/shared, concurrent-DAMON, report-HTML and helper `pid:N` probes
remain unobserved.** Read-only `docker image inspect cgprofile:local` found
OCI revision `66d33e05a2861908faa909df7ed5c85e29a72882`, older than the
review tip. The independent `run-gate-vbpub-r2-140043-1790820236` mutation
container remained running at the latest check, and controller RW-39
serializes image builds and probe containers against mutation lanes. I did
not build an image or start a daemon/workload alongside it, and did not
alter that container. Without the current image, the P1 handoff's real
`ctl version`, 100 MiB ephemeral peak/CPU/DAMON, 80 MiB shared baseline,
two simultaneous kdamonds, real `ctl report` HTML, helper private-namespace
PID identity/DAMON and outside-subpath refusal cannot be certified. The
required probes use only reviewer-owned uniquely named containers, private
PID/cgroup namespaces, network none, a currently loaded explicit parent,
and a verified 3-CPU cap when the serialized slot is free. No substitute
host-namespace or existing-singleton carrier is authorized.

## Final-tip verification and disposition

This record is committed before the final exact-tip short gates. Read the
separate `.run-gate/history.json` entries for the artifact commit's exact
hash; a pre-artifact receipt cannot stand in for them. The BLOCKED condition
is a live-probe evidence hold, not a finding that the repaired product failed
its live acceptance criteria. Rounds 4–6 are the review cap; this is round 5.
