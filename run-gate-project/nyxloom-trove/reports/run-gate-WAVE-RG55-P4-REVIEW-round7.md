# RG-55 P4 final adversarial review — round 7

ACCEPT

## Identity and reviewed tree

- Target: `P4` (`run-gate` 23.8.0, revision 42) in isolated worktree
  `/workspaces/vbpub/.worktrees/rg55-followups-run-gate`, branch
  `rg55-followups-run-gate`.
- Runtime metadata exposed by this session says Codex/GPT-5. The operator
  explicitly overrode the packet's identity-metadata blocker after independently
  verifying the configured `gpt-5.6-sol`/`xhigh` route. This artifact records
  that verification without inventing metadata the runtime did not expose.
- Frozen comparison base:
  `3df192a3b4c994cec41d1dca585139666ef812ef`.
- Initial review HEAD: `6b8e325220a0ec2beb02fa620489d320021c39ab`;
  initial product bytes were `c8f1654cc371c09e78faa6bf66feaf2aaf2e1a22`.
- Final reviewed product/test/docs HEAD:
  `b2eb633e7ed2574d4752e6b3e406446bf03a8321`.
- The worktree was clean before review and before final judging. The complete
  initial diff was captured blind before edits; its SHA-256 was
  `9f327596d4ba569246e3bfa08647e1fa4a66e42f4d27afeb9f6f4bfba1621eb3`.
- The review artifact and LOG are an administrative commit after the judged
  SHA. They change no product, test, gate, or user documentation bytes; the
  exact judged identity remains the SHA above and is the identity in both
  final Assay verdicts and every launcher record.

## Verdict

All eleven blockers found across rounds 5–7 are repaired. The exact final
candidate has 100% changed-line and changed-branch coverage, a complete R2
verdict with all 85 candidates killed, two independently rejected R3 canaries,
a passing aggregate gate, zero doctor failures, reproducible tester
infrastructure, and a live namespace-identity acceptance probe. No unresolved
item can alter shipped behavior. P4 is accepted for controller disposition;
this is not authority to merge or release.

## Blocker disposition

### B1 — critical — foreign exec record was overwritten

- Location: `run-gate.py:7088-7102`, `resolve_inflight` and its
  `run_container_lane` caller.
- Observable failure/reproduction: an exec-written inflight record printed a
  refusal but returned the same sentinel as “no record”, allowing a new
  container to overwrite recovery identity; live and `--fresh` tests formerly
  expected that unsafe success.
- Prescription/resolution: fail closed with exit 2 for live operation and make
  dry-run terminal before any fresh-run preflight. `a4786545` repaired the
  behavior. `b2eb633e` gives the final behavioral oracles: record bytes remain
  exact, a planted fresh filesystem preflight is never reached, and refusal
  bytes are visible through a real pipe while the producer is still alive.

### B2 — high — self-container lookup proved a name, not the process object

- Location: `run-gate.py`, `resolve_self_container_id`.
- Observable failure/reproduction: a hostname collision could profile an
  unrelated container; arbitrary/malformed IDs were accepted without a kernel
  identity comparison.
- Prescription/resolution: require a full Docker ID and compare the current
  mount namespace with a fixed read-only `docker exec <full-id>
  /usr/bin/readlink /proc/self/ns/mnt` probe. `a4786545`, `8f664a5b`, and
  `095f763d` implement and pin match, mismatch, malformed, inaccessible, and
  Docker-owned absence states. The final live probe is recorded below.

### B3 — high — inaccessible/malformed daemon responses collapsed into absence

- Location: `run-gate.py`, `ProfilerClient._ctl`,
  `daemon_not_running_reason`, and `cmd_doctor`'s Docker-ps branch.
- Observable failure/reproduction: permission errors and unrelated exit-125
  failures were reported as “daemon not running”; failed `docker ps` was
  treated like a successful empty list.
- Prescription/resolution: only Docker-owned absence/stopped evidence certifies
  absence; all other failures remain indeterminate and retain diagnostics.
  `a4786545` plus the negative-state tests in `095f763d` close both directions.

### B4 — high — rusage duration/wait failure issued false profile status

- Location: `run-gate.py`, `finish_bare_host_profiling` and
  `run_bare_host_lane`'s `wait4` boundary.
- Observable failure/reproduction: whole-second wall timestamps fabricated a
  zero/one-second duration for subsecond children, and a later `wait4` failure
  could be hidden behind an earlier daemon-degradation warning.
- Prescription/resolution: use monotonic measured duration and preserve the
  combined accounting failure as “no profile recorded”, without changing the
  lane verdict. `a4786545` and `8f664a5b` supply the repair and direct value
  oracles.

### B5 — high — malformed optional start metadata could leak a live session

- Location: `run-gate.py`, `start_lane_profiling` and
  `start_bare_host_profiling`.
- Observable failure/reproduction: `ok=true`, a valid session, and
  `target=null` raised after session creation; degradation discarded the
  client/session so `stop` was never attempted.
- Prescription/resolution: type-check optional target metadata while retaining
  the valid session and normal stop path. Repaired and regression-tested by
  `a4786545`/`095f763d`.

### B6 — high — empty successful inspect output was certified as absence

- Location: `run-gate.py`, `resolve_self_container_id` inspect response.
- Observable failure/reproduction: zero-exit, empty stdout returned “not
  running in a container” without Docker-owned absence evidence.
- Prescription/resolution: empty success is malformed/indeterminate; explicit
  `no such object/container` remains ordinary absence. Red oracle `dc936cc1`,
  repair `bc1a64f5`, final fixture `4a27eb42`.

### B7 — high — profiler-doctor tests depended on cockpit mount topology

- Location: `tests/test_run_gate.py`, `TestDoctorProfilerCheck`.
- Observable failure/reproduction: six tests passed in the cockpit but failed
  in tester-unified because their throwaway `/tmp` repositories had no physical
  host translation.
- Prescription/resolution: isolate the unrelated `physical_path` boundary in
  those fixtures while retaining profiler-status and doctor-exit assertions.
  Fixed by `0a875494`; the final contained suite proves the real gate topology.

### B8 — critical — tester-unified had no reproducible launcher

- Location: formerly `tester-unified/` had only a Dockerfile; now
  `tester-unified/run`, its executable tests, README, DESIGN-GUIDE, and
  CONSUMERS documentation.
- Observable failure/reproduction: manual launches selected the wrong Python,
  then container-local `/tmp`, then no Docker socket. Operator shell choices
  determined whether the same tree was red or green.
- Prescription/resolution: commits `411150b1` through `b3f3d5c3` ship one
  fail-closed launcher. It derives the physical workspace, dual-mounts it,
  creates/cleans repository-owned host-backed temp, mounts the Docker socket
  with its actual group, uses `/opt/tester-venv`, serializes capacity admission,
  enforces PSI and a two-tester estate cap, runs detached as uid 1003, applies
  and verifies three CPUs and the declared cgroup, and preserves inspect/log/
  wait/job-marker evidence. Six launcher tests plus eight namespace negative
  paths passed together (14/14). Every final gate below used only this launcher.

### B9 — high — moving-progress oracle depended on scheduler timing

- Location: `tests/test_run_gate.py`,
  `TestStallEndToEnd.test_a_moving_lane_is_never_stopped`.
- Observable failure/reproduction: a 1-second product threshold plus a writer
  assumed to run every 0.1 seconds failed under scheduler contention even
  though the production watch behaved correctly.
- Prescription/resolution: `eae1accf` synchronizes each write with the shipped
  poll boundary and retains only a 60-second suite failsafe. The exact targeted
  test passed in 3.77 seconds; all later full contained suites are green.

### B10 — high — killed mutants became budget placeholders; three contracts survived

- Location: `assay.toml:72-124`, `tests/test_run_gate.py` early mutation
  sentinels, and the production boundaries at `run-gate.py:5857-5860` and
  `run-gate.py:7088-7102`.
- Observable failure/reproduction: exact tree `eae1accf` executed all 85
  candidates but returned six `budget_exceeded`, three survived, and exit 4.
  The six already contradicted existing late assertions, then stranded test
  resources because pytest continued after the mutant was killed. The three
  gaps were Docker diagnostic text mode, flushed foreign-record disclosure,
  and terminal dry-run refusal.
- Prescription/resolution: R2 now uses `-x` because one failed behavioral
  assertion is a complete kill; early contract oracles distinguish invalid
  rusage facts/modes, textual diagnostics, singular status, and terminal/
  flushed refusal. `e4888a7f` eliminated every timeout and killed two of the
  three survivors (84/85); `1dda3bb0` modeled subprocess text/binary behavior
  and killed the final `text=True -> False` survivor. Final R2 is 85/85 with
  no placeholder bucket.

### B11 — high — the first flush/terminal oracle inspected implementation details

- Location: the pre-`b2eb633e` early sentinel in
  `tests/test_run_gate.py:387-406` (superseded).
- Observable failure/reproduction: a spy asserted `print` kwargs/call count and
  a direct helper sentinel. It killed mutations but violated the packet's
  “no hollow tests” rule and could certify implementation rather than operator
  behavior.
- Prescription/resolution: `b2eb633e` replaces it with two observable axes:
  (1) a foreign record makes dry-run finish before a deliberately failing
  fresh-run filesystem boundary, with record bytes unchanged; (2) a real
  buffered subprocess writes a post-return synchronization file and remains
  alive, while the parent proves the refusal already reached its pipe. The
  60-second bound is only a suite failsafe. Both focused tests passed in the
  canonical tester; all exact-tree evidence was regenerated afterward.

## Requirement-to-oracle traceability

| Requirement | Shipped path | Positive observable | Negative/combined attack | Final oracle |
|---|---|---|---|---|
| R-39f inflight ownership | `resolve_inflight` | foreign record untouched and terminal | exec→container change + live/`--fresh`/dry-run | end-to-end record bytes, no fresh preflight; B1/B11 |
| R-43i namespace provenance | `resolve_self_container_id` | full ID and equal mount namespace | hostname collision, malformed/empty ID, namespace mismatch | negative matrix + live full-ID probe |
| RG-59 status doctrine | `_ctl`, `cmd_doctor` | stopped differs from inaccessible | Docker-looking prefix + permission/exit 125; text bytes | direct classification and textual diagnostic tests |
| R-36h verdict neutrality | bare-host start/wait4/finish | lane exit survives profiling failures | daemon unavailable + wait4/cleanup failure | direct arithmetic/error tests + full gates |
| Contract §4.3a | rusage Summary | own child, monotonic duration, unknowns null | subsecond child + missing floor + unsupported mode | direct value/mode sentinels + R2 |
| R-43c session cleanup | start→state→stop | valid session always stopped | valid session + malformed optional target | combined response fixture |
| RW-46a lock isolation | `_lock_dir`, doctor | production `/tmp`, isolated tests | stale file/dir + concurrent suite | early singular status + full parallel-safe suite |
| O2 coverage | selftest / R1 | every changed executable line/branch covered | missing branch or excluded line | 274/274 lines, 96/96 branches; R1 568/568 and 96/96 |
| O3 mutation | Assay R2 | every candidate terminally accounted | killed assertion + broken cleanup; survivor gaps | `-x`, early behavioral sentinels, 85/85 killed |
| O4 live acceptance | launcher + Docker | defined namespace/cgroup/user/cpu/tmp facts | wrong PATH, local `/tmp`, missing socket, name-only ID | launcher evidence + live namespace probe |
| O5 contention neutrality | admission + stall tests | post-admission load cannot flip behavior | scheduler starvation and PSI rise during gate | event synchronization; aggregate gate remains green |

Pairwise/combined fixtures explicitly cover: foreign runner + `--fresh`;
foreign runner + dry-run + unavailable fresh preflight; hostname collision +
mount-namespace mismatch; Docker-owned prefix + permission failure; daemon
absence + `wait4` failure; valid session + malformed optional metadata; empty
successful inspect + absence wording; tester-local `/tmp` + host translation;
killed mutant + broken cleanup; binary stderr + human diagnosis; and flushed
refusal + a producer deliberately held alive.

## Exact final evidence

Every command below ran from the isolated worktree through repository-owned
`tester-unified/run`. Launcher records show `git_head=b2eb633e...`, user 1003,
`CgroupParent=dev-background.slice`, `NanoCpus=3000000000`, the derived
host/workspace dual mount, host-backed `/tmp`, Docker socket group handling,
and the workspace-global launch lock. For every accepted run, `docker wait`
was read as 0 separately and the last container-log line was
`TESTER_UNIFIED_JOB_EXIT=0`.

| Gate | Exact command suffix / container | Launch PSI `full avg10` | Result |
|---|---|---:|---|
| selftest | `./run-gate.py selftest` / `rg55-p4-selftest-b2eb633e` | 0.00 | 1,201 passed, 3 skipped; diff 274/274 executable lines, 96/96 branches; exit 0 |
| R1 | `./run-gate.py --base 3df192a3... assay-r1` / `rg55-p4-r1-b2eb633e` | 0.00 | Assay 6.1.1 PASS at exact SHA; R1 568/568 executable, 96/96 branches; exit 0 |
| R2 | `./run-gate.py --base 3df192a3... assay-r2` / `rg55-p4-r2-b2eb633e` | 0.00 | PASS; 85 candidates, 85 killed, 0 survived/equivalent/budget-exceeded/crashed; exit 0 |
| R3 | `./run-gate.py assay-r3` / `rg55-p4-r3-b2eb633e` | 0.46 | 2 canaries rejected, 0 survived; exit 0 |
| gate-full | `./run-gate.py --base 3df192a3... gate-full` / `rg55-p4-gate-full-b2eb633e-r2` | 0.24 | selftest + R1 + R3 green; exit 0 |
| doctor | `./run-gate.py doctor` / `rg55-p4-doctor-b2eb633e` | 0.00 | 14 checks: 9 OK, 2 WARN, 0 FAIL, 2 SKIP, 1 INFO; exit 0 |

The first final gate-full launch was refused before container creation at PSI
`full avg10=9.43`; retry waited for admission and launched at 0.24. During the
admitted aggregate run PSI later rose (R3 reported 8.59), but both canaries and
the aggregate verdict remained green. That is disclosed contention context,
not a correctness result. At final R2 launch one unrelated tester container
was present; P4 was the second and the estate cap was never exceeded.

The final R1 JSON says `commit=b2eb633e...`, `assay_version=6.1.1`, PASS,
R0/R1 verified by Assay. The final R2 JSON independently says the same commit
and Assay version, PASS, exit 0, and the complete 85/85 accounting above.

## Live probes, interfaces, and generated evidence

- Live namespace acceptance on the final tree:
  `docker inspect --format '{{.Id}}' dstdns-devcontainer-vb` returned full ID
  `047fa698976acf3a0a72dd80b3d7cfb58a281f7f428434798f084c9c43a06fc2`.
  Local `/usr/bin/readlink /proc/self/ns/mnt` and fixed-argv
  `docker exec <full-id> /usr/bin/readlink /proc/self/ns/mnt` both returned
  `mnt:[4026534299]`; Docker exec exited 0.
- The run-gate and cgprofile copies of `RG55-INTERFACE-CONTRACT.md` both hash
  to `ecae45b46d81f557d050687a7a4c8000541c4e5a1cb006028b39fb0e90a10be7`.
- `run-gate.py` declares `__revision__ = 42`.
- `run-gate.footprint.json` is tracked, schema 1, revision 42,
  `generated_by=run-gate`, `from_commit=89eb7e38...`, and contains three real
  rusage-maxrss lanes. Doctor found all three within 25% of live history and
  the manifest three days old (within its 30-day threshold).
- `cgprofile-host-daemon` was not running; `docker ps` succeeded with an empty
  exact-name result. `/run/cgprofile/ctl.sock` was not a socket (probe exit 1).
  Doctor therefore truthfully reported bare-host coarse rusage fallback. No
  daemon was started and no host-socket or placement success is claimed;
  `place-refused:no-gates-slice` was not observed or invented in this P4 run.

## Repair evidence and invalidation ledger

- `a4786545` B1–B5 production repair followed red oracles in `8d6f38d1`;
  `8f664a5b`/`095f763d` expanded the negative identity/value matrix.
- `dc936cc1` made B6 red; `bc1a64f5` repaired it; `4a27eb42` pinned the fixture;
  `0a875494` repaired B7. The contained P4 regression slice reached 146 passed.
- B8's launcher sequence is `411150b1`, `5295df88`, `3d6c050d`, `000f55ba`,
  `f336dbff`, `b3f3d5c3`; its combined launcher/namespace boundary set was
  14/14 green.
- `eae1accf` repaired B9; its exact targeted test was 1/1 green.
- Exact `eae1accf` R2 (76 killed, 3 survived, 6 budget-exceeded) was a real red
  verdict, never a PASS. `e4888a7f` added R2 fail-fast plus six early sentinels
  (6/6 focused green); its R2 was 84/85 with no timeouts. `1dda3bb0` added the
  text/binary diagnostic oracle (1/1 focused green) and reached 85/85.
- The review then rejected its own implementation-facing flush/sentinel test.
  `b2eb633e` replaced it with two real-process behavioral oracles (2/2 focused
  green), invalidating all `1dda3bb0` evidence. The complete final evidence in
  the preceding table belongs only to `b2eb633e`.
- Early direct cockpit pytest/gate attempts are explicitly invalid evidence and
  were not used. Manual tester attempts made while diagnosing B8 are likewise
  infrastructure reproductions only. All accepted evidence is from the
  repository-owned launcher.

## Commits made during this review chain

Administrative review records: `6b8e3252`, `732c6b4d`, `989065fc`,
`4d69f197`. Repairs/oracles: `8d6f38d1`, `a4786545`, `8f664a5b`, `095f763d`,
`dc936cc1`, `bc1a64f5`, `4a27eb42`, `0a875494`, `411150b1`, `5295df88`,
`3d6c050d`, `000f55ba`, `f336dbff`, `b3f3d5c3`, `eae1accf`, `e4888a7f`,
`1dda3bb0`, and `b2eb633e`. The containing commit for this round-7 artifact and
LOG entry is administrative and is reported separately to the controller.

## Documentation, backlog, and open questions

B8's new user-facing launcher is documented in `tester-unified/README.md`,
`tester-unified/docs/DESIGN-GUIDE.md`, and
`tester-unified/docs/CONSUMERS.md`; executable tests pin its defined launch
contract. Existing run-gate README/DESIGN/CONSUMERS/SPEC/CHANGES/backlog and
authoring surfaces were reviewed as part of the full diff and final selftest.
The B10/B11 changes affect internal gate/oracle mechanics, not public config
vocabulary, so no adopter-doc or backlog change is required. No open product
decision remains. Optional profiler infrastructure remains absent and honestly
reported; final daemon/carrier installation and any P3 placement probes belong
to the controller's later release sequence, not this P4 review.

No merge, push, tag, install, release, final-daemon start, dstdns change, or
forbidden-path change was performed.
