# RG-55 P6 final adversarial review — round 3
BLOCKED

The implementation blockers found in this blind review were repaired and the
short gates passed on the repaired code. The required live daemon acceptance
cannot be completed safely: the host reports `cgprofile.slice` as loaded but
provides neither a unit fragment nor a ControlGroup. That is insufficient to
verify the explicitly required, bounded daemon placement. I did not start a
daemon under an unverified parent or use host namespaces. This is a mechanical
review-evidence block, not a rejection based solely on the pending current-tree
R2 or full gate. No provisional integration or release is authorized by this
verdict.

## Identity, scope, and initial state

- Route observed from the actual process argv: `codex --model gpt-6-sol -c
  model_reasoning_effort="xhigh"`; target P6, isolated worktree
  `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile-final`, branch
  `rg55-followups-cgprofile-final`.
- Exact base: `4d32bcfea566ea36ae8835dcdf4e820b3183a501`; initial candidate:
  `6859bc3e9ada6dead4d8a5925eef39bb60f3b73a`. Before editing, HEAD was
  that candidate, `git status --porcelain` was empty, and the merge base was
  the exact base. Captured the full 16,384-line initial diff at
  `/tmp/cgprofile-p6-review-initial.diff` before editing. Reviewed the full
  base-to-tip change surface: 99 files, including code, tests, both fixture
  sets, infra, templates, public docs, controller and implementer records.
- Final reviewed implementation tip:
  `5ef6436051125f98c5f4ac65c7fa6d5d971bbfc7`. The final artifact
  commit changes the tree identity and is reported to the controller separately.
  The only pre-artifact untracked file was this round report. Earlier review
  rounds were preserved.
- Root and cgprofile interface-contract mirrors compare byte-for-byte. Frozen
  v1 fixture changes are restricted to version strings. A throwaway
  run-gate-project checkout from the exact base, with both P6 fixture copies
  transplanted, passed `nice -n 19 ionice -c 3 python3 -m pytest tests -q -k
  'fixture or golden or rg55'`: 22 passed, 1198 deselected, exit 0. The
  scratch checkouts were removed by exact path.

## Blind findings and repairs

| Finding | Severity, location, observable failure and reproduction | Prescription and disposition |
|---|---|---|
| B1 | **Critical**, original `lib/placement.py:206-231,397-404`: a symlink `rg-attacker00 -> rg-victim000` inside the gates slice passed the prefix guard. `check_write(attacker/cgroup.kill, "1")` returned normally and resolved to the victim's kill file. | Bind the guard to this session's exact `rg-<token>` leaf, refuse pre-existing leaves and symlinks, use atomic `mkdir`. Fixed in `fd77a4c4`; tests cover same-slice and outside-slice symlinks, another leaf, existing leaf, and creation race. |
| B2 | **High**, original `lib/liveness.py:268-289`: a lane-controlled FIFO with no writer blocked the sampler's `open`; a bounded local reproduction printed `BEFORE_FIFO_READ` then `timeout 2s` returned 124. | Open nonblocking, inspect the opened fd with `fstat`, accept only regular files. Fixed in `fd77a4c4`; no-writer FIFO oracle returns `present=false` without a timing verdict. |
| B3 | **Medium**, original `README.md:234` and `docs/CONSUMERS.md:127-133`: README promised nonexistent `daemon_slice.present`, and a pasteable gate example used the background tier instead of the gates tier. | State the shipped `ctl host` fields and use `CGROUP_PARENT_DEV_GATES`; synchronize README, DESIGN-GUIDE, CONSUMERS and PROTOCOL. Fixed in `fd77a4c4`. |
| B4 | **High**, original `lib/liveness.py:307-341`: appending only an unfinished line changed stream identity from `22:3d2f...` to `53:3d2f...` while the last complete event stayed `candidate`; trickled bytes reset the idle clock. | Use the ending byte offset and digest of the last complete JSON line; carry prior identity when no new complete line exists. Fixed in `d858852b`; a fake-clock oracle proves partial bytes cannot keep a silent lane alive. |
| B5 | **Medium**, original `cgprofile.py:1089-1127`: socket EOF after `reading` but before `end` returned CLI exit 0, falsely certifying a broken watch as complete. | Require exactly one terminal `end` for success, return transport fault 3 on premature EOF or a trailing fragment. Fixed in `d206877a`; a real AF_UNIX socket fixture covers premature EOF and complete/refused streams. |
| B6 | **Medium**, original `DESIGN.md:701,768` and `docs/CONSUMERS.md:24-28`: public design text described host PID/cgroup namespaces and gates under the background tier, and the 1.1 guide offered 1.0 release commands without the prerequisite sequence. | Document private namespaces, explicit host proc/cgroup binds, the gates tier, and 1.0.0 then 1.1.0 order. Fixed in `7bcdc37d`; stale source docstrings for namespace and progress identity fixed in `5ef64360`. |

The initial `REJECT` record with these six findings was written to this file
before production edits. All six code/doc defects above are resolved on the
reviewed implementation tip; they are retained here as the adversarial record.

## Requirement to oracle traceability

| Requirement and attack axis | Production path | Positive observable / negative oracle | Evidence |
|---|---|---|---|
| D-15/D-25 cgroup write whitelist and token isolation | `lib/placement.py` `CgroupWriteGuard`, `LanePlacement` | Own leaf accepts allowed files and `+` subtree-control; cross-leaf, outside-slice, `-` and symlink writes refuse, including `cgroup.kill`. Existing leaf and mkdir race refuse without changing caps. | `tests/test_serve_placement.py` guard and placement cases; B1 repair. |
| D-20 placed lane lifecycle | `lib/placement.py`, `lib/serve.py` | Leaf is created, caps read back, late pids placed, survivors moved back, 3x removal retry; no-token/no-gates/over-slice/write-failed sessions still start and sample with explicit refusal. | Placement test class and v1.1 goldens; live host acceptance unavailable. |
| D-22/D-27 watch verdicts | `lib/liveness.py`, `lib/serve.py` | Fake clock covers stalled/runaway/hung/over-ceiling/throttled priority; no stream cannot be runaway, no leaf cannot be throttled; PSI pauses use gates-slice then host source. | `test_liveness.py`, `test_serve_watch.py`; real host watch unavailable. |
| D-28 kill boundary | `lib/serve.py`, placement kill | Shared scope without token cannot be killed; placed `sleep` subtree gets SIGKILL and a terminal watch/report state. | `test_serve_watch.py` real-subtree tests, R0/R1; live daemon kill unavailable. Contract §8.2 and CP-12 govern finalization. |
| §8.4 bounded progress | `lib/liveness.py` | 64 KiB bounded tail, last complete NDJSON event, per-pid root path, FIFO absent, torn append does not reset idle clock. | B2/B4 oracles and fake clock. |
| D-30/§8.1/§8.6 socket authority | `lib/serve.py`, `cgprofile.py` | Peer credentials/allowlist and one request checked; malformed allowlist or flat v1 request refused, root-only dir stays root-only, socket bind cannot follow symlink; `transports` matches bind. | `test_serve_socket_carrier.py`, contract goldens; live both-carrier parity unavailable. |
| §8.2 watch wire/CLI | `lib/serve.py`, `cgprofile.py` | Flushes changing verdict lines and exactly one `end`; unknown session one-line refusal; EOF before end gives exit 3. | B5 socket oracle and `test_serve_watch.py`; live exec bridge unavailable. |
| §8.5 host and slice facts | `_host_snapshot` call sites, `infra/cgprofile.slice`, ciu templates | Explicit `daemon_slice` and `gates_slice` shapes, absent fact stays absent, compose retains authored parent and private namespaces. | Deployment and host snapshot tests; installed daemon slice cannot be verified. |
| CP-4..CP-7/CP-10 | `lib/store.py`, `lib/analyze.py`, `lib/serve.py`, `lib/access.py` | Widened run IDs without session-ID drift; real events and read-only effective limits; DAMON series; peer and proc-root class order unaffected. | R0/R1, v1 goldens, six seeded class-order runs. |
| Frozen contract/new goldens/docs | Both contract mirrors, v1/v1.1 fixture copies, README/DESIGN-GUIDE/CONSUMERS/PROTOCOL | Mirror byte equality; v1 only version-string changes; new JSON fixtures byte-checked; public fields/examples match code. | `cmp` exit 0, throwaway fixture tests, R0/R1, doc review. |

Combined-axis fixtures include a valid token plus a same-slice malicious
symlink; a FIFO plus a live watcher; a complete progress event plus a torn
append and advancing fake clock; a placed leaf plus read-back caps, late pids
and release; socket refusal plus stream EOF; and the three-seed peer-credential
class order paired with real-subtree enforcement. These exercise both the
legitimate and alarming conditions rather than only the exception type.

## Independent checks and gate receipts

- D-15 independent write sweep covered `open(...,"w")`, `write_text`,
  `os.replace`, `mkdir/rmdir`, `shutil`, subprocess, kill/killpg, chmod/chown
  across `lib/` and `cgprofile.py`. Fake cgroupfs probes demonstrated B1 and
  the repaired refusal. Production permitted cgroup writes remain limited to
  the D-15 whitelist; no new cross-token path was found after repair.
- Six serial orders of `TestPeerCredentials` and
  `TestRealSubtreeEnforcement`, both class orders under
  `PYTHONHASHSEED=1,2,3`, each passed 14 tests, exit 0. Each launch passed
  host `memory full avg10 <=5` and used `nice -n 19 ionice -c 3`.
- Focused repair sets: 162 passed after `d858852b`, then 211 passed after
  `d206877a`; five deployment/docs tests passed after `7bcdc37d`.
- The first registered R0/R1 at `fd77a4c4` ran 1,656 tests but **exited 2**:
  uncovered new mkdir-race refusal lines (`placement.py:419-421`), 6,003
  statements / 2,060 branches at 99%/100%. This was repaired with a
  behavioral race oracle in `d858852b`, not hidden by an exclusion.
- Registered R0/R1 at `d858852b` exited 0: 1,659 tests,
  6,007/6,007 statements and 2,060/2,060 branches. At `d206877a`, R0/R1
  exited 0: 1,662 tests, 6,012/6,012 statements and 2,064/2,064 branches.
  The later docs-only `7bcdc37d` tip also exited 0 with the same 1,662 tests
  and 100% line/branch totals; log `/tmp/cgprofile-p6-review-r01-7b.log`.
- R3 at `d206877a` exited 0: seven canaries rejected, zero survived. At
  `7bcdc37d`, R3 again exited 0 with 7/7 rejected, 0 survived; own container
  `run-gate-vbpub-r3-826940-1790360494` was live-inspected after immediate
  `docker update --cpus=3`, showing `NanoCpus=3000000000`, parent
  `dev-gates.slice`, running. An initial R3 command at repository root exited
  127 (`./run-gate.py` absent) and launched no container; the corrected
  project-directory run produced this receipt.
- Final reviewed implementation tip `5ef64360` (docstring-only change):
  R0/R1 and R3 receipts are appended below after completion. Before the final
  R0/R1 launch, host PSI `full avg10=2.56`; the helper verified
  `dev-gates.slice` loaded at `/etc/systemd/system/dev-gates.slice` and its
  cgroup, placed its exact probe and test containers with `--cpus=3`, and
  immediately updated/verified each at 3 CPUs. The exact running test
  container `cgprofile-gate-828701-1790360555` was independently inspected:
  `NanoCpus=3000000000`, `CgroupParent=dev-gates.slice`.
- `git diff --check` passed at the repaired tip. No `no-cover` pragmas were
  introduced. Tests are serial; no wall-clock speed is used as a behavioral
  verdict. Full current-tree R2 and full registered gate are pending by the
  explicit provisional-integration ruling, and were not used as this verdict's
  sole blocking reason.

### Final short-gate receipt on `5ef64360`

From `scripts/cgroup-profiler/`,
`nice -n 19 ionice -c 3 ./run-gate.py r0-r1` exited **0**; the separately
read log `/tmp/cgprofile-p6-review-r01-5ef.log` says 1,662 passed, 4
warnings, **6,012/6,012 statements and 2,064/2,064 branches**, and
`run-gate: lane 'r0-r1' exit 0`. Its host PSI at launch was
`full avg10=2.56`, inside the `<=5` admission bound. The helper logged
`cgprofile-gate-probe-828701-1790360553301441708` under
`dev-interactive.slice` and `cgprofile-gate-828701-1790360555` under
`dev-gates.slice`, both `NanoCpus=3000000000` at creation and immediately
updated/verified at 3 CPUs. I independently live-inspected the latter as
running with the same parent and cap.

The same project-directory command with lane `r3` exited **0**; the
separately read `/tmp/cgprofile-p6-review-r3-5ef.log` says seven canaries
rejected, zero survived, `run-gate: lane 'r3' exit 0`. Host PSI at launch was
`full avg10=0.36`. Its exact own container was
`run-gate-vbpub-r3-833792-1790360732`; the saved live inspection in
`/tmp/cgprofile-p6-review-r3-5ef-cap.txt` shows `NanoCpus=3000000000`,
`CgroupParent=dev-gates.slice`, and `status=running` after immediate
`docker update --cpus=3`.

## Mutation accounting and remaining evidence

The older P6 R2 at `aae66356bf3a65ef8b3ba7fa04a8042f2feee55c` is
`BUDGET_EXCEEDED/LANE_TIMEOUT`, exit 4: 362 candidates = 312 killed + 12
survived + 38 budget-exceeded + 0 crashed. The REPORT's twelve survivor rows
were read: ten are argued call-path equivalents (including corrected E12),
and two real oracle gaps gained tests for diagnostic flushing and a missing
CPU-rate baseline. The old receipt is neither a current-tree mutation result
nor proof that those replacements kill a candidate on this reviewed tree.
Current-tree mutant count, survivor disposition and completion marker are
unknown. A fresh exact-tree R2 and the full gate remain release blockers, not
independent grounds for rejecting provisional code review.

## Live acceptance and safety boundary

- Built `cgprofile:local` once under PSI with
  `nice -n 19 ionice -c 3 python3 build-push.py --build`, exit 0, from
  `d206877a`; image revision label matched that SHA. Later changes were docs
  and docstrings only, so this is an image build check, **not** exact-tip
  live acceptance.
- An exact-name, `--network none`, 3-CPU diagnostic container under
  `$CGROUP_PARENT_DEV_INTERACTIVE` mounted host systemd DBus and cgroup
  read-only. It reported `cgprofile.slice LoadState=loaded` but empty
  `FragmentPath` and empty `ControlGroup`; its corrected verification returned
  exit 2. The short-lived container was removed by exact name. The empty
  fields cannot certify an installed, active, bounded daemon slice; a typo
  can auto-create an unlimited transient slice. I did not launch
  `cgprofile-p6-review-probe` under that uncertain parent.
- Therefore the requested own-image daemon probes are unavailable: every
  verb over both carriers, peer refusal, placed exec-mode 80 MiB lane and
  cap/readback/cleanup, socket watch kill/report, and live `ctl host` fact
  shapes. Daemon/carrier availability on the **current reviewed tree** is
  unverified; the singleton `cgprofile-host-daemon` remained down. A
  daemon-absent `place-refused:no-gates-slice` was not mislabeled as a
  successful placement probe. Older implementer probes include host namespace
  modes prohibited by this packet and cannot substitute for these results.
- I did not use host PID/cgroup/network namespace modes, touch any other
  agent's container/campaign/worktree/CIU identity or Docker network, or
  start/stop/install the main daemon. Exact reviewer scratch checkouts and
  diagnostic containers were cleaned; the gate helpers clean their exact
  container names. No release, tag, merge or install was attempted.

## Non-blocking follow-up and controller action

S1: CP-11 remains open: daemon restart can leave an orphaned placed leaf.
The current code does not silently claim that a restart reclaims it; no
reviewer cleanup was improvised. This is a filed follow-up, separate from
this mechanical live-evidence block.

No new product decision is requested. An operator/controller needs to install
and verify the authored `cgprofile.slice` with its bounded values, then a
fresh independent review run must execute the named live probes under that
verified slice. The controller also needs current-tree R2, full gate, survivor
disposition and all RG-55 close-out requirements before any release. Prior
rounds and old mutation receipts must not be promoted as current evidence.
