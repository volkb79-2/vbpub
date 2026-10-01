# RG-55 P6 final adversarial review — continuation after host slice installation

REJECT

This is the operator-requested continuation of round 3 after the authored
`cgprofile.slice` was installed. The previous mechanical host-slice block is
closed. Live acceptance on the unchanged code exposed the following product
blockers. These findings were recorded before any code edit in this round.

## Identity and host prerequisite

- Worktree: `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile-final`,
  branch `rg55-followups-cgprofile-final`, clean initial HEAD
  `5e37a7a62bef67ad2b964426e9fa3ca7c3707e43`. The changes since
  reviewed implementation `5ef6436051125f98c5f4ac65c7fa6d5d971bbfc7`
  are only the round-3 report and P6 LOG.
- This resumed session does not expose its own Sol/effort fields. Round 3
  recorded an actual `gpt-6-sol`/`xhigh` process route. The operator requested
  this continuation; no new runtime identity is inferred from that request.
- Host `systemctl show cgprofile.slice` after launch: `LoadState=loaded`,
  `FragmentPath=/etc/systemd/system/cgprofile.slice`,
  `ControlGroup=/cgprofile.slice`, `MemoryMin=134217728`,
  `MemoryHigh=805306368`, `MemoryMax=1073741824`, `CPUWeight=100`,
  `IOWeight=50`. Host installed file and authored `infra/cgprofile.slice`
  have the same SHA-256:
  `9efee57a1174ab70a4ebada0b039ce51f75381f155c50269a0ed7e32f2a8e6e2`.
  `systemd-analyze verify` exited 0.
- Initial memory PSI `full avg10` was above 5, so no launch occurred until it
  fell to 0.15 for the image build and 0.42 for the daemon. The image build
  passed and `cgprofile:local` revision matched `5e37a7a6`. Own daemon and
  client containers had private cgroup/PID namespaces, network disabled,
  exact authored parents and `NanoCpus=3000000000` after immediate update.
  The daemon's host `/proc` bind was read-only; host cgroup-v2 and DAMON
  mounts were explicit. No pre-existing container/network was altered.

## Blind live findings

| ID | Severity and failure | Reproduction and prescription |
| --- | --- | --- |
| B1 | High: `ctl status <live-id>` rejects a valid daemon response. `lib/serve.py:1391-1394` omits top-level `at` in the single-session branch while `cgprofile.py:1237-1240` requires it. | Both exec and socket CLI exited 3 with `invalid response: status missing at`; a raw socket request returned `ok:true`, `contract:1`, `session`, `host` but no top-level `at`. Add the contract timestamp to the producer's single-session response and a behavioral CLI/server oracle. |
| B2 | Critical: placed work is not placed under private PID namespaces. `lib/placement.py:465-490` silently swallows `ESRCH` when writing a host PID into `cgroup.procs` from the daemon's private PID namespace. `start` reported `placement.error:null`, an owned leaf and read-back caps, but `pids_moved:0` and the leaf's `cgroup.procs` stayed empty. | Target `sleep` PID 2231716 was resolved from `/hostproc` and visible in the target cgroup; a direct write of that PID to the reviewer-owned leaf's `cgroup.procs` returned errno 3 (`ESRCH`). The target stayed outside the leaf. Provide a safe namespace-correct migration mechanism within D-15 or refuse placement explicitly; do not claim a successful placed session with no migrated lane PID. The chosen mechanism must retain private container namespaces. |
| B3 | Critical: watch certifies a kill that never happened. `lib/serve.py:1325-1343` ignores every `os.kill(..., SIGKILL)` failure, then calls `record_kill([])` and finalizes the session. | Socket watch emitted `state:stalled`, `verdict:killed`, reason `SIGKILL sent to 0 pid(s)` and terminal `end:killed`; `docker inspect` immediately afterward showed the exact reviewer-owned workload still running. Refuse or report failed enforcement and keep the live session; only record `killed` after a successful signal or verified cgroup kill. A real private-namespace acceptance oracle must show the workload actually exited. |

## Other live evidence and limits

- Exec and socket `version`/`gc` responses were byte-identical; `host` and
  registry `status` had equal structure with expected time-varying facts.
  The socket carrier was reached from a separate reviewer-owned client
  container as UID 1000. A raw socket connection as excluded UID 1001 got
  the correct `peer-refused` document. The in-image CLI as UID 1001 instead
  exited 3 on `Broken pipe` before reading it; this is S1 for follow-up
  unless parity rules require a named refusal from the CLI itself.
- `ctl host` reported `gates_slice.present:true`, daemon slice limits above,
  and the owned leaf. Requested placement caps read back as
  `memory.high=67108864`, `memory.max=100663296`, `cpu.weight=100`, but
  the empty leaf could not throttle or account for the lane. The requested
  80 MiB placed-lane acceptance is therefore not proved.
- `--on-stall report` emitted `reported` over the exec watch carrier and an
  `end:stopped` after an explicit socket-carrier stop. The workload naturally
  exited near this verdict, so this run does not prove it stayed alive under
  report mode. `ctl report` over the socket returned an HTML path and the
  actual artifact was nonempty (4,933,044 bytes). Stop idempotence passed.
- All three exact reviewer-owned containers were removed; `docker inspect`
  confirmed them absent. The dedicated host scratch directory was removed.
  No `ciu up` was used, so no CIU stack or network needed teardown. The
  main daemon singleton was untouched.

## Verdict boundary

The installed slice closes round 3's only environmental block. B1–B3 are
live code failures and prevent provisional integration. The current-tree P6
R2 and full gate remain separate release requirements; no P6 mutation lane
was launched in this review. Any scoped repair changes the judged tree and
requires fresh short gates before a later ACCEPT.

## Bounded repair and remaining decision

The reviewer preserved the blind findings above before editing, then committed
`9c0a6e39` (`cgprofile: fail closed on unreachable watch kills and repair status
timestamp`). The patch adds top-level `at` to a single-session status response;
refuses to certify `cgroup.kill` on an empty/unreadable leaf; and permits PID
fallback signals only when the host-proc and local-proc views name the same
process. Failed, partial, or unverified enforcement now records a reported
`kill-refused` verdict and keeps the session live. Regression oracles exercise
the shipped CLI against the socket status response, the live-subtree failed
signal, the empty-leaf case, and a same-number/different-process proc view.
The focused placement/socket/watch suite passed: **174 tests**. The registered
short gates are to run on a committed, clean report-bearing tip; consult
`.run-gate/history.json` for the exact commit receipts after those runs.

This is only a fail-closed containment of B3, not a placement implementation.
B2 remains critical: the private-namespace daemon cannot migrate a host PID
through the current `cgroup.procs` write, so live placement, memory throttling,
and an actually enforced placed-lane kill remain unproved. A safe mechanism
that retains private namespaces is a controller/product design decision; this
review does not grant permission to switch to host PID/cgroup/network modes.
No further live launch was made after the initial probes: the changed code was
tested in the focused suite and must be independently live-accepted after B2
is repaired. No R2 or full gate was launched here.
