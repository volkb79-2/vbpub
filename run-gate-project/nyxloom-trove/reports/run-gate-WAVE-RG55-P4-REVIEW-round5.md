# RG-55 P4 final adversarial review — round 5

REJECT

## Identity and initial state

- Target: `P4` (`rg55-followups-run-gate`).
- Runtime identity exposed to this session: Codex/GPT-5. The operator explicitly
  overrode the packet's route-metadata blocker after independently verifying the
  configured `gpt-5.6-sol`/`xhigh` route. This artifact records that operator
  verification; it does not invent unavailable runtime metadata.
- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-run-gate`.
- Initial HEAD: `6b8e325220a0ec2beb02fa620489d320021c39ab` (the preceding
  administrative BLOCKED review record); product bytes were unchanged from
  `c8f1654cc371c09e78faa6bf66feaf2aaf2e1a22`.
- Merge base with the then-current local `main`:
  `3df192a3b4c994cec41d1dca585139666ef812ef`.
- Initial worktree status: clean (`git status --porcelain=v1` emitted no
  entries).
- The complete initial diff was captured before these findings or any repair.
  SHA-256 of `git diff $(git merge-base HEAD main)..HEAD`:
  `9f327596d4ba569246e3bfa08647e1fa4a66e42f4d27afeb9f6f4bfba1621eb3`.
- At `2026-09-15T03:19:58Z`, memory PSI was
  `full avg10=0.02 avg60=0.09 avg300=0.10`. Two mutation lanes were already
  active and both were verified at `NanoCpus=3000000000`: P1 container
  `run-gate-vbpub-r2-3301167-1789437789` (client PID 3301167) and P6
  container `run-gate-vbpub-r2-3403254-1789439415` (client PID 3403254).
  No P4 mutation lane was launched.

## Initial verdict

P4 is not acceptable at the captured tree. Five shipped paths contradict the
binding R-36h/R-39/R-43 contracts or the estate-wide status-comparison rule.
They are independently reproducible and are repairable inside the authorized
`run-gate-project/run-gate.py`, `run-gate-project/tests/`, and report scope.

## Blockers captured before repair

### B1 — critical — a foreign exec record is overwritten after the alleged refusal

- Location: `run-gate-project/run-gate.py:6996-7005`, then
  `run-gate-project/run-gate.py:7251-7255` and the fresh record write in
  `run_container_lane`.
- Observable failure: `resolve_inflight()` prints that an exec-written record
  is foreign and will be left untouched, but returns `None`. Its documented
  return contract defines `None` as "run fresh". The container path therefore
  starts a new container, overwrites the same per-lane record, and clears it at
  completion. Recovery identity for the still-running persistent exec runner
  is lost. `--fresh` has the same outcome.
- Reproduction: the shipped
  `TestInflightRecordDecisions.test_live_run_refuses_a_foreign_record_and_runs_fresh_instead`
  explicitly expects `main(["suite"]) == 0` and one new container run after
  planting `runner="exec"`; its sibling `test_fresh_refuses...` expects the
  same under `--fresh`. Those passing expectations demonstrate the contract
  breach: R-39f says a foreign record is touched not at all.
- Prescription: live operation must fail closed with tool-refusal exit 2 before
  any new container starts or record write occurs. Dry-run may describe that
  live refusal without mutation. Replace the hollow success assertions with
  oracles proving no lane container starts and the record bytes remain exact.

### B2 — high — self-container resolution proves a name, not this process's object

- Location: `run-gate-project/run-gate.py:1810-1846`.
- Observable failure: `/etc/hostname` is passed to `docker inspect`; any
  container currently wearing that name is accepted as this process's own
  container. On a host invocation whose hostname collides with an unrelated
  container name, the daemon path profiles that unrelated container and labels
  its cgroup numbers DEVCONTAINER-WIDE for the lane. The returned ID is not
  even checked against the contract's 64-hex container-id grammar.
- Reproduction: shipped test
  `TestResolveSelfContainerIdDirectBranches.test_docker_inspect_success_returns_the_real_id`
  feeds hostname `abc123` and arbitrary output `sha256:realid789`; it expects
  success without one comparison tying the inspected object to the current
  process. A live acceptance probe on this devcontainer showed a sound
  comparison is available: current `/proc/self/ns/mnt` and
  `docker exec dstdns-devcontainer-vb readlink /proc/self/ns/mnt` both returned
  `mnt:[4026534299]`.
- Prescription: after direct inspect returns a valid full ID, compare a kernel
  namespace identity of this process with the inspected container via a fixed,
  read-only `docker exec` probe. A mismatch, malformed ID, or unavailable probe
  must degrade to rusage with a specific "could not verify" reason; it must not
  be rendered as confirmed absence.

### B3 — high — daemon failures collapse inaccessible and malformed into absent

- Location: `run-gate-project/run-gate.py:1128-1152`,
  `run-gate-project/run-gate.py:1230-1252`, and the `docker ps` branch in
  `cmd_doctor` near `run-gate-project/run-gate.py:5776`.
- Observable failure: any stderr whose last line merely begins `docker:` or
  `Error response from daemon:` is reported as "daemon not running; start it",
  including authorization failures and unrelated daemon refusals. Exit 125 is
  likewise treated as proof of absence although it only proves the Docker CLI
  could not perform the operation. `doctor` folds a nonzero `docker ps`
  (daemon inaccessible) into the same missing-container status.
- Reproduction: the shipped
  `test_docker_reserved_exit_code_names_the_real_cause_even_with_no_recognizable_prefix`
  supplies exit 125 plus `no such file or directory: unknown` and asserts the
  specific "not running" diagnosis. Substituting
  `Error response from daemon: permission denied` also satisfies the current
  prefix-only predicate. Neither input compares the daemon container's state.
- Prescription: certify absent/stopped only when Docker-owned stderr contains a
  matching absence/stopped signal. Treat other nonzero, non-JSON exec results
  as indeterminate transport/execution failures. In `doctor`, distinguish a
  failed `docker ps` from a successful empty listing. Update RG-59 prose that
  still describes the superseded substring/exit-code behavior.

### B4 — high — rusage timing and wait failure issue false measurements/status

- Location: `run-gate-project/run-gate.py:1050-1058`,
  `run-gate-project/run-gate.py:2152-2155`, and
  `run-gate-project/run-gate.py:7853-7855,7897-7916`.
- Observable failure: rusage duration is derived by subtracting timestamps
  intentionally quantized to whole seconds. A subsecond lane is recorded as
  zero or one second depending on a wall-clock boundary, and its average cores
  becomes `null` at zero. Separately, the daemon-degradation warning is printed
  before `wait4`; if `wait4` then fails, `or` retains the earlier reason and the
  `_warned` latch suppresses any correction. Output says "coarse rusage sampling
  only" although no profile was recorded, and history hides the wait failure.
- Reproduction: `_iso_utc()` uses `%Y-%m-%dT%H:%M:%SZ`, and
  `finish_bare_host_profiling()` subtracts the reparsed strings. The shipped
  `test_wait4_raising_never_aborts_the_lane` explicitly asserts that its planted
  `wait4 exploded` cause is absent from `profile_error`.
- Prescription: measure the child bracket with injected/monotonic seconds and
  pass that real duration into the Summary while retaining contract-format wall
  timestamps. Defer the one rusage degradation warning until accounting has
  finished; if wait4 fails, emit/store the combined cause with "no profile
  recorded", never a success-sounding fallback.

### B5 — high — malformed successful start data can discard a known live session

- Location: `run-gate-project/run-gate.py:2013-2032` (same unsafe target read
  exists in `start_lane_profiling` at `run-gate-project/run-gate.py:1889-1908`).
- Observable failure: after a valid successful `start` supplies a usable session
  id, `start_doc.get("target", {}).get(...)` raises when `target` is `null` or a
  non-object. The R-36h call-site guard converts that into rusage/basic state but
  discards the client and known session; `ctl stop <session>` is never issued,
  leaking the daemon session while the lane continues.
- Reproduction: a contract-1 object with `ok=true`, a valid `session`, and
  `target=null` passes `_ctl`'s only required-key check and raises
  `AttributeError` at the cited line after the producer has already created the
  session.
- Prescription: consume optional target data only when it is an object, preserve
  the valid session, and always reach the normal stop path. Validate required
  successful-response values as usable non-empty values so malformed envelopes
  degrade explicitly instead of reaching subprocess argv construction.

## Initial traceability and combined attacks

| Requirement | Code path | Positive observable | Negative/combined attack | Initial result |
|---|---|---|---|---|
| R-39f | `resolve_inflight` -> `run_container_lane` | foreign record untouched | exec->container flip + live/`--fresh` | **B1** |
| R-43i / contract 4.3a | `resolve_self_container_id` | own devcontainer id | host/container name collision + malformed ID | **B2** |
| RG-59 / status doctrine | `_ctl`, `cmd_doctor` | absent differs from unreachable/malformed | Docker-owned prefix + permission/exit 125 | **B3** |
| R-36h / Summary accuracy | bare-host wait4 path | verdict neutral and honest profile | prior daemon miss + wait4 failure; subsecond child | **B4** |
| R-43c / R-36h | start -> state -> finish | every successful start is stopped | valid session + optional target wrong type | **B5** |
| R-44d | `footprint_manifest_lane_expected` | five keys incl. source | old manifest method/scope derivation | verified in source; gates pending |
| RG-58 | load + doctor | WARN, never refusal | bare-host vs container/exec | verified in source; gates pending |
| RW-46a | `_lock_dir`, test fixture | production `/tmp`, tests isolated | directory plant + concurrent suite | verified in source; gates pending |

Combined-axis fixtures required by the repair are: foreign runner + `--fresh`;
same hostname + different mount namespace; Docker error prefix + inaccessible
daemon; daemon unavailable + wait4 failure; and valid session + malformed
optional start metadata. These are deliberately pairwise combinations: each
previous single-axis happy/negative test passed while the combined condition
remained unsafe.

## Evidence status

The controller's earlier exact-product-tree results (59/59 R2 killed,
selftest 1161 passed/3 skipped, two R3 canaries rejected, doctor exit 0) are
historical evidence only. Commit `6b8e3252` already changed the judged tree,
so the final packet's per-tree rule requires fresh evidence after repairs.
No final gate, coverage, mutation, or release verdict is claimed here.

