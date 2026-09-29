# RG-55 P6 final adversarial review — round 6
BLOCKED

Initial blind finding recorded before production edits. Candidate
`5086c0d9cd911e3c33ae23199a760539a1edc1cb`, base
`038644c0ad0eafba5198a810bae82a261a52eabf`, branch
`rg55-followups-cgprofile-final`, clean on entry. The initial diff was
captured before editing. The caller supplied the Sol xhigh route; this report
does not infer that route from the process.

**B1 — High, `lib/access.py:187-239`: the daemon always refuses a healthy
loaded gates slice from its required private PID namespace.** The slice
verifier calls `systemctl show` inside the daemon. The live reviewer-owned
daemon returned `ctl host.gates_slice.present=false` and a placed
`start` returned `place-refused:no-gates-slice`. In that same isolated
container, `systemctl show dev-gates.slice` exited 1 with “System has not
been booted with systemd as init system (PID 1).” The host reports
`LoadState=loaded`, `FragmentPath=/etc/systemd/system/dev-gates.slice`,
`ControlGroup=/dev.slice/dev-gates.slice`, finite memory and CPU caps.
Inside the daemon, `busctl --system … GetUnit dev-gates.slice` exited 0
and returned the expected unit object path. Reproduction is the private
namespace probe with the production mounts and 3-CPU cap. Use that mounted
host system bus to read the exact loaded unit, authored fragment and
ControlGroup properties; preserve fail-closed parsing and verify finite
capacity. Test both the healthy host-bus path and transient/missing/malformed
unit refusals, then repeat the real placement and kill probes.

**B2 — Critical, `lib/placement.py:72-115,739-812`: the private-PID
placement bridge still cannot attach a real sibling-container PID.** After
the B1 verifier repair in a local iteration build, a reviewer-owned 80 MiB
token process was resolved in the target cgroup, but `start --place`
returned `place-refused:write-failed:<leaf>/cgroup.procs`, `leaf:null`,
`pids_moved:0`. The daemon logged “systemd could not attach pid 102781
to dev-gates.slice/rg-p6placement…”. This is fail-closed, but the promised
placement and shared-scope kill capability are unavailable on a healthy
installed host. Diagnose and repair the exact host-bus refusal without
loosening private namespaces or the cgroup write whitelist; prove actual
host-PID migration, stop-time restoration and a selected-lane kill live.

The second live diagnostic narrowed B2 further. After passing an absolute
subcgroup path, the host manager refused the same reviewer-owned process with
`Process migration not available on non-delegated units.` The host reports
`Delegate=no` for the loaded `dev-gates.slice`. The code-side absolute-path
repair and three path-boundary oracles are retained, but the required live
placement cannot be completed without changing the host unit/deployment
contract owned by `modern-debian-tools-python-debug/`, a path explicitly
forbidden to this review. I did not set a transient host property, join a host
namespace, or create a different systemd unit. The controller/operator must
decide and implement the host-unit change in its own scope, then have a fresh
review run placement, restoration, and kill probes. No provisional integration
or release is authorized by this BLOCKED verdict.

**B3 — High, `lib/serve.py:2189-2192`: the partial-request socket timeout
path has no behavioral oracle in the registered coverage gate.** The first
post-repair exact-tree `r0-r1` ran 1,811 passing tests but exited 2: two
statements at that exception handler were missed, leaving total statement
coverage 99% (6,393 statements, 2 missed; 2,258 branches fully covered).
The existing trickle tests cover the wall deadline, but they do not exercise
an idle `recv` timeout. Add a deterministic connection fixture that sends
an unfinished request, times out on its next receive, confirms no response
or dispatch for that request, and confirms a subsequent valid request is
served. The red gate is not a product failure but prevents the declared 100%
short-gate prerequisite.

## Traceability and combined-axis attacks

| Contract | Code path and oracle | Round-6 result |
|---|---|---|
| D-15 / D-25 loaded, bounded gates authority | `access.verify_systemd_slice`, `placement.bounded_slice_capacity`; healthy authored host unit vs transient/malformed/missing bus | The original verifier collapsed healthy into absent. The Busctl repair passed 137 focused access tests and the live daemon reported `gates_slice.present:true`; fake negative cases still refuse. |
| D-25 private-PID placement and stop restoration | `_systemd_attach_process`, `LanePlacement.migrate/release`; absolute unit subpath vs relative/traversal; host PID under private namespaces | The local 189-test placement file passed. Live Busctl rejected the relative subpath, then rejected the corrected absolute subpath because the host slice is non-delegated. Live migration and restoration remain unproved. |
| RW-379/RW-380 selected lane kill | `TargetContainerKill`, `LanePlacement.kill`, `_enforce_stall_kill`; selected lane vs sibling and daemon | Cannot exercise shared-scope kill with no verified placed leaf. No kill claim. |
| §8.1 / §8.5 carrier and host facts | `SessionServer`, socket/exec clients, host snapshot | A private-namespace daemon and separate 1000:1000 socket client answered `version`, `host`, registry `status`, and `gc` over both carriers with matching response key sets. `gates_slice.present` changed from false to true after the verifier repair. Full verb parity remains unproved. |
| R-36h / RW-381 | profiling refusal must not change a lane verdict; R2/full gate are release conditions | Current exact-tree R0/R1 and R3 were PASS before review. No R2 was launched; the prior R2 is stale and inconclusive. |

Pairwise probes paired a healthy loaded host unit with the daemon's private
PID namespace, then a valid token and 80 MiB process with a verified gates
slice. The former exposed B1; the latter first exposed the relative
subcgroup error and then the non-delegated-unit refusal. The benign state
was constructed alongside the refusal state: host Busctl `GetUnit` succeeded,
and memory/CPU ceilings read as finite, while `systemctl` inside the daemon
failed solely because PID 1 was not systemd.

## Evidence boundary

The original branch, HEAD, merge base, and clean status matched the caller's
exact values. Initial production diff was saved at
`/tmp/cgprofile-p6-round6-initial.diff` before edits. Host memory
`full avg10` was 0.00 before the first build, 0.31 before the first repaired
live launch, and 0.26 before the absolute-subpath probe. Each reviewer-owned
daemon used `cgprofile.slice`; each client/target used `dev-gates.slice`;
all were launched with `--network=none --cgroupns=private --cpus=3`, updated
immediately to 3 CPUs, and inspected as `NanoCpus=3000000000` under the
exact parent. Each exact named container and dedicated scratch directory
was removed in the probe's `finally`. The pre-existing P1 mutation
container was untouched. The main daemon was neither started nor stopped.

The saved live transcript is `/tmp/cgprofile_p6_round6_probe.log` (latest
attempt); its temporary probe source is
`/tmp/cgprofile_p6_round6_probe.py`. The current local image was built
from the modified worktree with `python3 build-push.py --build`; until
commit it is an iteration image, not a final exact-tip acceptance image.
Local focused commands were
`nice -n 19 ionice -c 3 /home/vscode/.venv/bin/python -m pytest
tests/test_access.py -q` (137 passed, exit 0) and the same command for
`tests/test_serve_placement.py` (189 passed, exit 0).

The separately read `.run-gate/history.json` records clean PASS/exit 0
for `r0-r1` and `r3` at initial tree `5086c0d9`; the P6 report records
1,799 tests, 6,375/6,375 statements and 2,252/2,252 branches, and seven
canaries rejected. The older `6540f877` R2 ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG`; no current-tree mutation accounting
exists. The scoped code/test fixes made here invalidate the initial-tree
short-gate receipts for the resulting commit. The registered short gates
must be rerun on the final committed review tree; their post-commit receipts
are in the project's `.run-gate/history.json` and the controller's final
review return, not presumed by this paragraph.

No public configuration value or user-facing capability was changed by the
two narrow code fixes. The existing README, DESIGN-GUIDE and CONSUMERS
already describe the intended healthy verified-slice behavior; no new
backlog row is filed in this forbidden host-setup scope. Full code/diff
review, full carrier parity, fail-closed move-back, throttling, watch
kill/report, DAMON overhead, and current-tree R2/full gate remain
unverified because the named host-unit prerequisite is mechanically blocked.

The previous exact-tree R0/R1 and R3 history entries are PASS at
`5086c0d9`, but no current-tree R2/full gate exists. The latter are allowed
to remain pending only for provisional integration under RW-381. Live
acceptance is incomplete until B1 is repaired and the named probes pass.
