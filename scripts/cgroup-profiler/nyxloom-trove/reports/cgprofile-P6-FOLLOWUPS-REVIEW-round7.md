# cgprofile P6 follow-ups — final adversarial review, round 7

**Verdict: REJECT.** Round 7 is the review-series cap. The controller must
disposition B1–B3 before provisional integration; this report does not ask for
another review round. No product decision is requested. D-31, D-32, and
charge-based leaf memory semantics were treated as settled.

Reviewed branch `rg55-followups-cgprofile-final` at
`66d33e05a2861908faa909df7ed5c85e29a72882` against main
`8730098d0a8205bb398e60028e799b4ef7b18835`. The worktree was clean at
dispatch and remained free of product-code edits. The diff has 115 changed
files; I inspected its code, tests, fixtures, infrastructure, and adopter
documents, and reconciled the prior P6 reports and rounds 1–6 with independent
probes. The findings below concern this exact product-code tree.

## Blockers

### B1. Healthy systemd scope retirement is reported as failed restoration

`lib/placement.py:1662–1718` restores members and removes the owned leaf,
then requires `_cgroup_has_processes(scope) is False` at line 1697. An absent
scope yields `None` from `cgroup.procs` at lines 1330–1344, which line 1698
collapses into `place-refused:write-failed:<scope>/cgroup.procs`. systemd can
automatically retire an empty transient scope as the last process moves out.

I reproduced this twice in separate live instances of the image built from
this tip. In the second run, `start --place --memory-high 64M --memory-max
96M` returned the exact leaf
`/dev.slice/dev-gates.slice/rg-profile-p6reviewplaceb20260930.scope/rg-p6reviewplaceb20260930`,
readbacks `memory.high=67108864` and `memory.max=100663296`, and
`pids_moved=1`. Host `cgroup.procs` held PID 3078646. `systemctl show` read
back `Slice=dev-gates.slice`, `Delegate=yes`, and that exact `ControlGroup`.
`stop` then returned `ok: true` but
`summary.placement.error=place-refused:write-failed:dev.slice/dev-gates.slice/rg-profile-p6reviewplaceb20260930.scope/cgroup.procs`.
Immediately after, `/proc/3078646/cgroup` showed the original
`docker-1c0cc86d...scope`; the leaf did not exist; and systemd reported the
review scope `LoadState=not-found`, `ActiveState=inactive`, and empty
`ControlGroup`. The first run produced the same false error for a different
token. The daemon additionally logged that it was “refusing to remove its
leaf,” although the leaf was gone. The review's placed watch kill ended with
the same cleanup error.

This violates contract §8.3's truthful restoration and cleanup result. It
also leaves an unreleased recovery record for a scope that systemd has
already removed. Handle the auto-retired scope as completion **only** after
each same-start member is proven restored to its recorded origin, the exact
leaf and scope are absent, and systemd confirms the exact unit is gone.
Retain the current fail-closed result if any member, path, or manager result
is indeterminate. Add a regression that makes the unit vanish immediately
after the last restore and checks the final summary and journal, plus a live
placed stop acceptance probe.

### B2. An unreadable requested cap is certified as applied

`lib/placement.py:1304–1319` writes each requested cap, assigns
`util.read_int(target)` to `applied[name]`, and sets
`successfully_placed=True` without checking the read result. A transient
read failure or vanished cap therefore produces `"memory.high": null` with
`placement.error: null`, `pids_moved: 1`, and `placed=True`.

Independent fake cgroup probe: a `LanePlacement` subclass removed
`memory.high` just after `_write`; `apply([101])` returned
`{'requested': True, 'leaf': '/dev.slice/dev-gates.slice/rg-profile-rg55-place-token-01.scope/rg-rg55-place-token-01', 'applied': {'memory.high': None}, 'pids_moved': 1, 'error': None}`;
`placed=True`, and the cap file was absent. The shipped rounding test only
checks a readable rounded value. Contract §8.3 says `applied` is the value
read back from the leaf; it cannot certify an unreadable requested cap.

Refuse placement if any requested cap cannot be read back as a valid value,
and preserve the owned state until safe cleanup is established. Add a
read-failure regression that asserts no success claim. Recheck exact leaf
membership before reporting the completed placement.

### B3. A request without its newline is dispatched on EOF

`lib/serve.py:2314–2352` breaks out of the receive loop on EOF at line 2347
and then parses and dispatches any buffered JSON. Contract §8.1 rule 4
requires an incomplete line to be closed **without dispatch**; the protocol
is newline-delimited.

An independent real AF_UNIX `SessionServer` probe sent
`{"verb":"version","args":{},"contract":1}` without a newline and then
`shutdown(SHUT_WR)`. The server returned a successful version response.
I reproduced the same result through the built daemon's live socket. In that
same instance, a newline-terminated v1 flat `stop` request was correctly
refused by name with `bad-argument`.
The same path would dispatch state-changing verbs. Return on EOF unless a
newline was received. Add an actual-socket test that sends a complete JSON
object without `\n`, checks that no response or side effect occurs, and
confirms a subsequent valid connection still works.

## Independent evidence and non-blocking observations

- `.run-gate/history.json` records clean `pass`, exit 0, for `r0-r1`
  (133.803 s) and `r3` (13.039 s) at `66d33e05`. The history does not itself
  contain the asserted 2,058 tests, 7,106 statements, 2,580 branches, or
  seven-canary detail; those counts were supplied by the controller's
  separate receipts. I did not re-label the older
  `6540f877` `BUDGET_EXCEEDED/CANDIDATE_HUNG` R2 as current-tree evidence.
- The root and mirrored interface contract copies are byte-identical. All
  ten v1 golden names match the root, test, and cgprofile fixture copies;
  the only v1 baseline differences are four `1.0.0`→`1.1.0` version strings
  permitted by RW-45. A throwaway main checkout of `run-gate-project` with
  **both** candidate fixture directories overlaid passed its fixture/golden
  selection: `22 passed, 1198 deselected in 3.05s`, exit 0.
- The image built from this tip once with `build-push.py --build` after an
  empty `DOCKER_CONFIG` avoided a cockpit credential-helper failure. The
  untagged image reported `0.0.0-dev`, consistent with its development
  version path. Before launches, memory PSI `full avg10` was below 5,
  `dev-gates.slice` and `cgprofile.slice` were loaded, and each reviewer
  container was capped and inspected at 3 CPUs. All reviewer containers and
  scratch socket directories were removed. The unrelated P1 mutation run
  and main daemon were untouched.
- The actual daemon launch used private PID/cgroup namespaces, no network,
  `cgprofile.slice`, read-only `/proc` at `/hostproc`, a **writable full host
  `/sys/fs/cgroup` bind**, and the host system bus. The shipping template has
  the same broad bind at `ciu.compose.yml.j2:110–115` and `privileged: true`
  at line 53. The `CgroupWriteGuard` refused independent writes to
  `dev-gates.slice/cgroup.subtree_control`, a `-memory` controller value at
  the scope, `system.slice/memory.max`, another token's `cgroup.kill`, and a
  symlink escaping the owned leaf. Under settled D-32 this is an application
  guard; it does not provide OS containment against arbitrary code in the
  privileged daemon.
- The live socket was root:group 994, mode `0660`. A UID 1000 client with
  `CGPROFILE_ALLOW_UIDS=0` received the shaped `peer-refused` error while
  root exec `version` succeeded. A malformed allowlist `0,` made the daemon
  exit 2 before serving. A relative symlink at `ctl.sock` was replaced by
  the real socket without altering its decoy target. A separate root:root
  `0770` scratch directory yielded root:root `0660` socket, denied a UID
  1000 client, logged the documented root-only INFO, and kept root exec
  serving. A further live sweep invoked `version`, `host`, `status`, `start`,
  `stop`, `report`, `gc`, and an unknown-session `watch` through both
  carriers. `version`, `gc`, `report`, and unknown-session `watch` matched
  exactly; `host` and `status` matched in shape while live readings changed;
  independent `start`/`stop` sessions returned the same document keys with
  their own identities and sample counts. Three targeted deterministic tests
  for socket goldens, every non-streaming verb's exact carrier parity, and
  the successful `watch` stream golden passed (`3 passed in 1.01s`).
- Live `ctl host` showed `gates_slice.present=true` with the authored
  `/dev.slice/dev-gates.slice`, and `daemon_slice` at `/cgprofile.slice`
  (`memory.min=134217728`, `memory.high=805306368`). The placed watch
  produced `reading`, `verdict` for stalled/killed, and exactly one terminal
  `end` within about 7 s. The target container PID 1, a separate sibling
  container, and the daemon remained alive. A separate `--on-stall report`
  session without placement had `placement: null`, reached
  `state=stalled/verdict=reported`, and its sleeper survived.
- CP-10 order checks ran the two relevant classes serially in both orders
  under `PYTHONHASHSEED=11,22,33`: six runs, each `14 passed` (about 21.5 s).
  Focused CP-4..CP-7/socket/placement tests returned `4 passed, 1 skipped`;
  the socket goldens test separately returned `1 passed`. The skipped DAMON
  HTML case was not independently evaluated in this cockpit environment.
  `git diff --check` passed.

## Claims not independently closed in this review

The live carrier sweeps could not obtain byte-for-byte equality for volatile
`host`/`status` readings or for independently started sessions; the shipped
fixed-clock parity test covers exact document equality. I did not obtain a
live host-level forced
mapping/attachment refusal that leaves an owned leaf, nor inspect a live
`events.jsonl` origin-path row after restoration. I did not finish a fresh
current-tree R2/full gate or independently reconstruct the controller's
coverage/canary counts from their raw test logs. The historical ten
contract-equivalent R2 survivors and one hung candidate belong to an older
tree; their final disposition awaits the permitted asynchronous current-tree
R2. These limits are separate from B1–B3 and are not used as blockers under
RW-381.
